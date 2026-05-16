from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import threading
import time
import typing
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, NoReturn

from nonebot import get_driver, on_command
from nonebot.log import logger
from nonebot.matcher import current_matcher
from nonebot.params import CommandArg

from nextbot.db import CommandConfig, User, get_session
from nextbot.stats import increment_command_execute_total
from nextbot.text_utils import safe_at_segment_or_empty
from nextbot.time_utils import db_now_utc_naive

_ALLOWED_PARAM_TYPES = {"bool", "int", "float", "string"}
_DEFAULT_DISABLED_MODE = "reply"
_DEFAULT_DISABLED_MESSAGE = "⚠️ 该命令暂时关闭"


@dataclass(frozen=True)
class RegisteredCommand:
    command_key: str
    display_name: str
    description: str
    usage: str
    module_path: str
    handler_name: str
    permission: str
    default_enabled: bool
    param_schema: dict[str, dict[str, Any]]
    category: str
    meta_hash: str


@dataclass(frozen=True)
class RuntimeCommandState:
    command_key: str
    display_name: str
    description: str
    usage: str
    module_path: str
    handler_name: str
    permission: str
    enabled: bool
    param_schema: dict[str, dict[str, Any]]
    param_values: dict[str, Any]
    aliases: list[str]
    category: str
    is_registered: bool


class CommandConfigValidationError(ValueError):
    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.errors = errors or []


class CommandUsageError(Exception):
    pass


_registry_lock = threading.RLock()
_registry: dict[str, RegisteredCommand] = {}
_runtime_cache: dict[str, RuntimeCommandState] = {}
_runtime_cache_ready = False
# R8 R8-U-B-2：DB 故障时每条消息都会触发 refresh_runtime_cache 失败，
# 直接 logger.exception 会引发日志风暴。用 monotonic 时间戳节流：
# 首次失败打完整 stack trace，60 秒窗口内的后续失败只打简短 warning。
_runtime_cache_last_load_error_at: float = 0.0
_RUNTIME_CACHE_ERROR_THROTTLE_SEC: float = 60.0
_current_command_context: contextvars.ContextVar[RuntimeCommandState | None] = (
    contextvars.ContextVar("nextbot_current_command_context", default=None)
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clone_dict(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(_json_dumps(value))


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_usage_text(value: Any) -> str:
    return str(value).strip()


def _get_raw_command() -> str:
    try:
        matcher = current_matcher.get()
        prefix = matcher.state.get("_prefix", {})
        return str(prefix.get("raw_command", "")).strip()
    except Exception:
        return ""


def _build_usage_message(usage: str, *, actual_command: str = "") -> str:
    normalized = _normalize_usage_text(usage)
    if not normalized:
        return "❌ 命令格式错误"
    if actual_command:
        display_name = normalized.split()[0] if normalized else ""
        if display_name and actual_command != display_name:
            normalized = actual_command + normalized[len(display_name):]
    return f"❌ 格式错误，正确格式：{normalized}"


def _normalize_param_key(name: str) -> str:
    key = str(name).strip()
    if not key:
        raise CommandConfigValidationError("参数名称不能为空")
    return key


def _coerce_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise CommandConfigValidationError("需要布尔值")


def _coerce_int(raw: Any) -> int:
    if isinstance(raw, bool):
        raise CommandConfigValidationError("需要整数")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        raise CommandConfigValidationError("需要整数")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise CommandConfigValidationError("需要整数")
        try:
            return int(text)
        except ValueError as exc:
            raise CommandConfigValidationError("需要整数") from exc
    raise CommandConfigValidationError("需要整数")


def _coerce_float(raw: Any) -> float:
    if isinstance(raw, bool):
        raise CommandConfigValidationError("需要数字")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise CommandConfigValidationError("需要数字")
        try:
            return float(text)
        except ValueError as exc:
            raise CommandConfigValidationError("需要数字") from exc
    raise CommandConfigValidationError("需要数字")


def _coerce_string(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw)


def _normalize_enum(values: Any, *, param_name: str) -> list[Any] | None:
    if values is None:
        return None
    if not isinstance(values, list) or not values:
        raise CommandConfigValidationError(f"参数 {param_name} 的 enum 必须是非空数组")
    normalized: list[Any] = []
    for value in values:
        if value in normalized:
            continue
        normalized.append(value)
    return normalized


def _validate_by_schema(schema: dict[str, Any], value: Any, *, param_name: str) -> Any:
    param_type = str(schema.get("type", "")).strip()
    if param_type not in _ALLOWED_PARAM_TYPES:
        raise CommandConfigValidationError(f"参数 {param_name} 的类型不支持：{param_type}")

    normalized: bool | int | float | str
    if param_type == "bool":
        normalized = _coerce_bool(value)
    elif param_type == "int":
        normalized = _coerce_int(value)
    elif param_type == "float":
        normalized = _coerce_float(value)
    else:
        normalized = _coerce_string(value)

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and normalized not in enum_values:
        raise CommandConfigValidationError(f"参数 {param_name} 必须是预设值")

    if param_type in {"int", "float"}:
        min_value = schema.get("min")
        max_value = schema.get("max")
        if min_value is not None and normalized < min_value:
            raise CommandConfigValidationError(f"参数 {param_name} 不能小于 {min_value}")
        if max_value is not None and normalized > max_value:
            raise CommandConfigValidationError(f"参数 {param_name} 不能大于 {max_value}")

    if param_type == "string":
        required = bool(schema.get("required", False))
        normalized_text = normalized if isinstance(normalized, str) else str(normalized)
        if required and not normalized_text.strip():
            raise CommandConfigValidationError(f"参数 {param_name} 不能为空")
        normalized = normalized_text

    return normalized


def _normalize_param_schema(params: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise CommandConfigValidationError("参数定义必须是对象")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_def in params.items():
        param_name = _normalize_param_key(raw_name)
        if not isinstance(raw_def, dict):
            raise CommandConfigValidationError(f"参数 {param_name} 的定义必须是对象")

        param_type = str(raw_def.get("type", "")).strip()
        if param_type not in _ALLOWED_PARAM_TYPES:
            raise CommandConfigValidationError(
                f"参数 {param_name} 的类型不支持：{param_type}"
            )

        label = str(raw_def.get("label", "")).strip() or param_name
        description = str(raw_def.get("description", "")).strip()
        required = bool(raw_def.get("required", False))
        enum_values = _normalize_enum(raw_def.get("enum"), param_name=param_name)

        if "default" not in raw_def:
            raise CommandConfigValidationError(f"参数 {param_name} 缺少 default")

        schema: dict[str, Any] = {
            "type": param_type,
            "label": label,
            "description": description,
            "required": required,
        }

        if param_type in {"int", "float"}:
            if "min" in raw_def and raw_def["min"] is not None:
                schema["min"] = (
                    _coerce_int(raw_def["min"])
                    if param_type == "int"
                    else _coerce_float(raw_def["min"])
                )
            if "max" in raw_def and raw_def["max"] is not None:
                schema["max"] = (
                    _coerce_int(raw_def["max"])
                    if param_type == "int"
                    else _coerce_float(raw_def["max"])
                )
            min_value = schema.get("min")
            max_value = schema.get("max")
            if (
                min_value is not None
                and max_value is not None
                and min_value > max_value
            ):
                raise CommandConfigValidationError(
                    f"参数 {param_name} 的 min 不能大于 max"
                )

        if enum_values is not None:
            schema["enum"] = enum_values

        default_value = _validate_by_schema(schema, raw_def["default"], param_name=param_name)
        schema["default"] = default_value
        normalized[param_name] = schema

    return normalized


def _build_default_param_values(schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        name: definition.get("default")
        for name, definition in schema.items()
    }


def _build_meta_hash(
    *,
    command_key: str,
    display_name: str,
    description: str,
    usage: str,
    module_path: str,
    handler_name: str,
    permission: str,
    param_schema: dict[str, dict[str, Any]],
    category: str,
) -> str:
    payload = {
        "command_key": command_key,
        "display_name": display_name,
        "description": description,
        "usage": usage,
        "module_path": module_path,
        "handler_name": handler_name,
        "permission": permission,
        "param_schema": param_schema,
        "category": category,
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _get_registered_command(command_key: str) -> RegisteredCommand | None:
    with _registry_lock:
        return _registry.get(command_key)


def get_permission_registry() -> frozenset[str]:
    """返回 @command_control(permission=...) 注册过的所有 permission key 集合。

    供 nextbot.permissions.validate_permission_key() 校验权限名是否存在使用。
    所有命令的权限 key 均通过 command_control 装饰器声明，因此此 registry
    是项目内"已知权限"的单一真源。

    返回 frozenset 而非可变集合：调用方不应试图修改注册表。
    """
    with _registry_lock:
        return frozenset(
            cmd.permission for cmd in _registry.values() if cmd.permission
        )


def _get_disabled_policy() -> tuple[str, str]:
    config = get_driver().config
    mode = str(getattr(config, "command_disabled_mode", _DEFAULT_DISABLED_MODE)).strip().lower()
    if mode not in {"reply", "silent"}:
        mode = _DEFAULT_DISABLED_MODE
    message = str(
        getattr(config, "command_disabled_message", _DEFAULT_DISABLED_MESSAGE)
    ).strip() or _DEFAULT_DISABLED_MESSAGE
    return mode, message


def _check_user_banned(user_id: str) -> str:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is not None and user.is_banned:
            reason = str(user.ban_reason or "").strip()
            if reason:
                return f"🚫 你已被封禁\n原因：{reason}\n如有疑问，请联系管理员"
            return "🚫 你已被封禁\n如有疑问，请联系管理员"
    finally:
        session.close()
    return ""


def _coerce_enabled(value: Any) -> bool:
    try:
        return _coerce_bool(value)
    except CommandConfigValidationError as exc:
        raise CommandConfigValidationError("enabled 必须是布尔值") from exc


def _merge_param_values(
    *,
    schema: dict[str, dict[str, Any]],
    old_values: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for name, definition in schema.items():
        if name in old_values:
            try:
                merged[name] = _validate_by_schema(definition, old_values[name], param_name=name)
                continue
            except CommandConfigValidationError:
                pass
        merged[name] = definition.get("default")
    return merged


def _resolve_bot_event(
    resolved_signature: inspect.Signature, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any | None, Any | None]:
    try:
        bound = resolved_signature.bind_partial(*args, **kwargs)
    except Exception:
        return None, None
    return bound.arguments.get("bot"), bound.arguments.get("event")


def _to_runtime_state(row: CommandConfig) -> RuntimeCommandState:
    schema = _normalize_param_schema(_parse_json_object(row.param_schema_json))
    values = _merge_param_values(
        schema=schema,
        old_values=_parse_json_object(row.param_values_json),
    )
    aliases: list[str] = []
    try:
        raw_aliases = json.loads(row.aliases_json or "[]")
        if isinstance(raw_aliases, list):
            aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
    except (json.JSONDecodeError, TypeError):
        pass

    return RuntimeCommandState(
        command_key=row.command_key,
        display_name=row.display_name,
        description=row.description,
        usage=_normalize_usage_text(row.usage),
        module_path=row.module_path,
        handler_name=row.handler_name,
        permission=row.permission,
        enabled=bool(row.enabled),
        param_schema=schema,
        param_values=values,
        aliases=aliases,
        category=str(row.category or ""),
        is_registered=bool(row.is_registered),
    )


def refresh_runtime_cache() -> None:
    session = get_session()
    try:
        rows = session.query(CommandConfig).order_by(CommandConfig.command_key.asc()).all()
    finally:
        session.close()

    runtime: dict[str, RuntimeCommandState] = {}
    for row in rows:
        runtime[row.command_key] = _to_runtime_state(row)

    with _registry_lock:
        global _runtime_cache, _runtime_cache_ready
        _runtime_cache = runtime
        _runtime_cache_ready = True


def _ensure_runtime_cache_loaded() -> None:
    with _registry_lock:
        ready = _runtime_cache_ready
    if not ready:
        refresh_runtime_cache()


def _get_runtime_state(command_key: str) -> RuntimeCommandState:
    global _runtime_cache_last_load_error_at
    try:
        _ensure_runtime_cache_loaded()
    except Exception as exc:  # noqa: BLE001
        # U-2.4：runtime cache 加载失败时显式记录，避免 DB 不可用时
        # disable 操作完全失效却无任何告警的 silent failure。
        # R8 R8-U-B-2：DB 故障期间每条消息都会进到这里，logger.exception 会
        # 产生日志风暴 + 完整 stack trace 把日志盘打满。改为 60 秒窗口节流：
        # 窗口首次打完整 trace，后续打简短 warning。
        now = time.monotonic()
        if now - _runtime_cache_last_load_error_at >= _RUNTIME_CACHE_ERROR_THROTTLE_SEC:
            logger.exception(
                f"运行时命令缓存加载失败：command_key={command_key}（fallback 到 default_enabled）"
            )
            _runtime_cache_last_load_error_at = now
        else:
            logger.warning(
                f"运行时命令缓存加载失败 (throttled): command_key={command_key} "
                f"reason={type(exc).__name__}"
            )
    with _registry_lock:
        runtime = _runtime_cache.get(command_key)
    if runtime is not None:
        return runtime

    registered = _get_registered_command(command_key)
    if registered is None:
        return RuntimeCommandState(
            command_key=command_key,
            display_name=command_key,
            description="",
            usage="",
            module_path="",
            handler_name="",
            permission="",
            enabled=True,
            param_schema={},
            param_values={},
            aliases=[],
            category="",
            is_registered=False,
        )

    return RuntimeCommandState(
        command_key=registered.command_key,
        display_name=registered.display_name,
        description=registered.description,
        usage=registered.usage,
        module_path=registered.module_path,
        handler_name=registered.handler_name,
        permission=registered.permission,
        enabled=registered.default_enabled,
        param_schema=registered.param_schema,
        param_values=_build_default_param_values(registered.param_schema),
        aliases=[],
        category=registered.category,
        is_registered=True,
    )


def get_current_command_config() -> dict[str, Any] | None:
    context = _current_command_context.get()
    if context is None:
        return None
    return {
        "command_key": context.command_key,
        "display_name": context.display_name,
        "description": context.description,
        "usage": context.usage,
        "permission": context.permission,
        "enabled": context.enabled,
        "params": _clone_dict(context.param_values),
        "schema": _clone_dict(context.param_schema),
    }


def get_current_param(name: str, default: Any = None) -> Any:
    context = _current_command_context.get()
    if context is None:
        return default
    key = str(name).strip()
    if not key:
        return default
    return context.param_values.get(key, default)


def get_current_command_usage() -> str | None:
    context = _current_command_context.get()
    if context is None:
        return None
    usage = str(context.usage).strip()
    return usage or None


def raise_command_usage() -> NoReturn:
    raise CommandUsageError


def _serialize_runtime_state(item: RuntimeCommandState) -> dict[str, Any]:
    return {
        "command_key": item.command_key,
        "display_name": item.display_name,
        "description": item.description,
        "usage": item.usage,
        "module_path": item.module_path,
        "handler_name": item.handler_name,
        "permission": item.permission,
        "enabled": item.enabled,
        "param_schema": _clone_dict(item.param_schema),
        "param_values": _clone_dict(item.param_values),
        "aliases": list(item.aliases),
        "category": item.category,
        "is_registered": item.is_registered,
    }


def list_command_configs() -> list[dict[str, Any]]:
    _ensure_runtime_cache_loaded()
    with _registry_lock:
        commands = [
            _runtime_cache[key]
            for key in sorted(_runtime_cache.keys())
            if _runtime_cache[key].is_registered
        ]

    return [_serialize_runtime_state(item) for item in commands]



def get_command_config(command_key: str) -> dict[str, Any]:
    normalized_key = str(command_key).strip()
    if not normalized_key:
        raise CommandConfigValidationError("command_key 不能为空")

    state = _get_runtime_state(normalized_key)
    if not state.is_registered:
        raise CommandConfigValidationError(
            "命令不存在",
            errors=[{"field": "command_key", "message": "命令不存在"}],
        )
    return _serialize_runtime_state(state)



def update_command_config(
    command_key: str,
    *,
    enabled: Any = None,
    param_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_key = str(command_key).strip()
    if not normalized_key:
        raise CommandConfigValidationError("command_key 不能为空")

    normalized_enabled: bool | None = None
    if enabled is not None:
        try:
            normalized_enabled = _coerce_enabled(enabled)
        except CommandConfigValidationError as exc:
            raise CommandConfigValidationError(
                "参数校验失败",
                errors=[{"field": "enabled", "message": str(exc)}],
            ) from exc

    normalized_params: dict[str, Any] | None = None
    if param_values is not None:
        if not isinstance(param_values, dict):
            raise CommandConfigValidationError(
                "参数校验失败",
                errors=[{"field": "param_values", "message": "param_values 必须是对象"}],
            )
        normalized_params = param_values

    session = get_session()
    now = db_now_utc_naive()
    errors: list[dict[str, Any]] = []
    try:
        row = (
            session.query(CommandConfig)
            .filter(CommandConfig.command_key == normalized_key)
            .first()
        )
        if row is None:
            raise CommandConfigValidationError(
                "保存失败",
                errors=[{"field": "command_key", "message": "命令不存在"}],
            )
        if not row.is_registered:
            raise CommandConfigValidationError(
                "保存失败",
                errors=[{"field": "command_key", "message": "命令已下线，无法编辑"}],
            )

        schema = _normalize_param_schema(_parse_json_object(row.param_schema_json))
        current_values = _merge_param_values(
            schema=schema,
            old_values=_parse_json_object(row.param_values_json),
        )

        if normalized_params is not None:
            for raw_name, raw_value in normalized_params.items():
                name = str(raw_name).strip()
                if name not in schema:
                    errors.append(
                        {
                            "field": f"param_values.{name}",
                            "message": "参数未定义",
                        }
                    )
                    continue
                try:
                    current_values[name] = _validate_by_schema(
                        schema[name],
                        raw_value,
                        param_name=name,
                    )
                except CommandConfigValidationError as exc:
                    errors.append(
                        {
                            "field": f"param_values.{name}",
                            "message": str(exc),
                        }
                    )

        if errors:
            raise CommandConfigValidationError("保存失败", errors=errors)

        if normalized_enabled is not None:
            row.enabled = normalized_enabled
        row.param_values_json = _json_dumps(current_values)
        row.updated_at = now
        session.commit()
    except CommandConfigValidationError:
        session.rollback()
        raise
    finally:
        session.close()

    refresh_runtime_cache()
    return get_command_config(normalized_key)


def sync_registered_commands_to_db() -> None:
    with _registry_lock:
        registered_items = list(_registry.values())

    session = get_session()
    now = db_now_utc_naive()
    try:
        rows = session.query(CommandConfig).all()
        row_by_key = {row.command_key: row for row in rows}
        touched_keys: set[str] = set()

        for command in registered_items:
            touched_keys.add(command.command_key)
            schema = command.param_schema
            schema_json = _json_dumps(schema)
            row = row_by_key.get(command.command_key)

            if row is None:
                row = CommandConfig(
                    command_key=command.command_key,
                    display_name=command.display_name,
                    description=command.description,
                    usage=command.usage,
                    module_path=command.module_path,
                    handler_name=command.handler_name,
                    permission=command.permission,
                    enabled=command.default_enabled,
                    param_schema_json=schema_json,
                    param_values_json=_json_dumps(_build_default_param_values(schema)),
                    category=command.category,
                    is_registered=True,
                    meta_hash=command.meta_hash,
                    last_synced_at=now,
                    updated_at=now,
                )
                session.add(row)
                continue

            old_values = _parse_json_object(row.param_values_json)
            merged_values = _merge_param_values(schema=schema, old_values=old_values)

            row.display_name = command.display_name
            row.description = command.description
            row.usage = command.usage
            row.module_path = command.module_path
            row.handler_name = command.handler_name
            row.permission = command.permission
            row.param_schema_json = schema_json
            row.param_values_json = _json_dumps(merged_values)
            row.is_registered = True
            row.meta_hash = command.meta_hash
            row.last_synced_at = now
            row.updated_at = now
            row.category = command.category

        for row in rows:
            if row.command_key in touched_keys:
                continue
            if row.is_registered:
                row.is_registered = False
                row.updated_at = now
            row.last_synced_at = now

        session.commit()
    finally:
        session.close()

    refresh_runtime_cache()


def update_command_aliases(
    command_key: str,
    aliases: list[str],
) -> dict[str, Any]:
    normalized_key = str(command_key).strip()
    if not normalized_key:
        raise CommandConfigValidationError("command_key 不能为空")

    cleaned: list[str] = []
    for raw in aliases:
        alias = str(raw).strip()
        if not alias:
            continue
        if " " in alias:
            raise CommandConfigValidationError(
                "别名不能包含空格",
                errors=[{"field": "aliases", "message": f"别名 \"{alias}\" 包含空格"}],
            )
        cleaned.append(alias)

    session = get_session()
    now = db_now_utc_naive()
    try:
        row = (
            session.query(CommandConfig)
            .filter(CommandConfig.command_key == normalized_key)
            .first()
        )
        if row is None:
            raise CommandConfigValidationError(
                "保存失败",
                errors=[{"field": "command_key", "message": "命令不存在"}],
            )
        if not row.is_registered:
            raise CommandConfigValidationError(
                "保存失败",
                errors=[{"field": "command_key", "message": "命令已下线，无法编辑"}],
            )

        all_rows = session.query(CommandConfig).filter(
            CommandConfig.command_key != normalized_key,
            CommandConfig.is_registered.is_(True),
        ).all()
        conflict_names: set[str] = set()
        for r in all_rows:
            # MH-2 (U-2.2)：alias 也要避开他人的 command_key，否则启动期
            # register_alias_matchers 会同时绑定 plugin A 的 primary matcher
            # 和 plugin B 的 alias matcher，导致 /bag 之类命令双重处理。
            conflict_names.add(r.command_key)
            conflict_names.add(r.display_name)
            try:
                existing_aliases = json.loads(r.aliases_json or "[]")
                if isinstance(existing_aliases, list):
                    for a in existing_aliases:
                        conflict_names.add(str(a).strip())
            except (json.JSONDecodeError, TypeError):
                pass

        # R8 M-4 (R8-U-B-1)：alias 还必须避开当前命令自己的 command_key / display_name，
        # 否则 register_alias_matchers 会创建第二个 on_command(同名) matcher，
        # wrapper 双执行（双计数、双扣金币、双发消息）。
        # 同时校验 batch 内不重复，防御 UI 重复输入。
        self_conflict_names: set[str] = {normalized_key, row.display_name}

        seen_in_batch: set[str] = set()
        for alias in cleaned:
            if alias in self_conflict_names:
                raise CommandConfigValidationError(
                    "保存失败",
                    errors=[{
                        "field": "aliases",
                        "message": f"别名 \"{alias}\" 不能与命令自身的 command_key / 名称相同",
                    }],
                )
            if alias in seen_in_batch:
                raise CommandConfigValidationError(
                    "保存失败",
                    errors=[{
                        "field": "aliases",
                        "message": f"别名 \"{alias}\" 在本次 batch 内重复",
                    }],
                )
            seen_in_batch.add(alias)
            if alias in conflict_names:
                raise CommandConfigValidationError(
                    "保存失败",
                    errors=[{"field": "aliases", "message": f"别名 \"{alias}\" 与其他命令冲突"}],
                )

        row.aliases_json = json.dumps(cleaned, ensure_ascii=False)
        row.updated_at = now
        session.commit()
    except CommandConfigValidationError:
        session.rollback()
        raise
    finally:
        session.close()

    refresh_runtime_cache()
    return get_command_config(normalized_key)


_original_handlers: dict[str, Any] = {}


def register_alias_matchers() -> None:
    _ensure_runtime_cache_loaded()
    with _registry_lock:
        items = list(_runtime_cache.values())

    count = 0
    for state in items:
        if not state.is_registered or not state.aliases:
            continue
        original = _original_handlers.get(state.command_key)
        if original is None:
            continue

        for alias in state.aliases:
            alias_matcher = on_command(alias)
            alias_matcher.handle()(original)
            count += 1
            logger.info(
                f"注册命令别名：alias={alias} command_key={state.command_key}"
            )

    if count > 0:
        logger.info(f"命令别名注册完成：count={count}")


def command_control(
    *,
    command_key: str,
    display_name: str,
    permission: str,
    description: str = "",
    usage: str = "",
    default_enabled: bool = True,
    params: dict[str, dict[str, Any]] | None = None,
    category: str = "",
):
    normalized_key = str(command_key).strip()
    if not normalized_key:
        raise CommandConfigValidationError("command_key 不能为空")

    normalized_display_name = str(display_name).strip() or normalized_key
    normalized_permission = str(permission).strip()
    normalized_description = str(description).strip()
    normalized_usage = str(usage).strip()
    normalized_schema = _normalize_param_schema(params)
    normalized_category = str(category).strip()

    def decorator(func):
        module_path = str(getattr(func, "__module__", "")).strip()
        handler_name = str(getattr(func, "__name__", "")).strip() or normalized_key
        meta_hash = _build_meta_hash(
            command_key=normalized_key,
            display_name=normalized_display_name,
            description=normalized_description,
            usage=normalized_usage,
            module_path=module_path,
            handler_name=handler_name,
            permission=normalized_permission,
            param_schema=normalized_schema,
            category=normalized_category,
        )

        registered = RegisteredCommand(
            command_key=normalized_key,
            display_name=normalized_display_name,
            description=normalized_description,
            usage=normalized_usage,
            module_path=module_path,
            handler_name=handler_name,
            permission=normalized_permission,
            default_enabled=bool(default_enabled),
            param_schema=_clone_dict(normalized_schema),
            category=normalized_category,
            meta_hash=meta_hash,
        )

        with _registry_lock:
            exists = _registry.get(normalized_key)
            if exists is not None and exists != registered:
                raise RuntimeError(f"duplicate command_key detected: {normalized_key}")
            _registry[normalized_key] = registered

        signature = inspect.signature(func)
        # H-3 part 2 (U-2.12)：import-time 校验 handler 必须含 bot 和 event 形参，
        # 否则 wrapper 的 ban check / disabled reply 会因 _resolve_bot_event 返回
        # (None, None) 而被静默跳过，等同于权限层 fail-open。与 permissions.py
        # 的 require_permission 装饰器形参校验对称。
        param_names = set(signature.parameters.keys())
        if "bot" not in param_names or "event" not in param_names:
            raise RuntimeError(
                f"@command_control 装饰的 {func.__qualname__} 必须有 bot 和 event 形参"
            )
        try:
            # include_extras=True preserves Annotated metadata (e.g. NoneBot2's
            # `T_State = Annotated[Dict, _STATE_FLAG]`) so downstream injectors
            # can still recognize the parameter after we rebuild the signature.
            type_hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            type_hints = {}

        parameters = [
            parameter.replace(
                annotation=type_hints.get(parameter.name, parameter.annotation)
            )
            for parameter in signature.parameters.values()
        ]
        resolved_signature = signature.replace(
            parameters=parameters,
            return_annotation=type_hints.get("return", signature.return_annotation),
        )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            state = _get_runtime_state(normalized_key)
            context_token = _current_command_context.set(state)
            try:
                try:
                    increment_command_execute_total()
                except Exception:
                    logger.exception(f"命令计数写入失败：command_key={normalized_key}")
                if not state.enabled:
                    bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
                    mode, message = _get_disabled_policy()
                    if mode == "reply" and bot is not None and event is not None:
                        await bot.send(event, message)
                    return None

                bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
                if bot is not None and event is not None:
                    try:
                        ban_msg = _check_user_banned(event.get_user_id())
                    except Exception:  # noqa: BLE001
                        # U-2.3：DB 故障（busy_timeout 超时 / OperationalError）时
                        # fail-soft 放行命令执行，避免高峰期随机 traceback；
                        # 与 increment_command_execute_total 的 fail-soft 策略对齐。
                        logger.exception(
                            f"封禁检查失败（DB 异常）：command_key={normalized_key}"
                        )
                        ban_msg = ""
                    if ban_msg:
                        # PC-4.1：使用 safe_at_segment_or_empty，非数字 user_id 退化为空文本段
                        at = safe_at_segment_or_empty(event.get_user_id())
                        await bot.send(event, at + "\n" + ban_msg)
                        return None
                else:
                    # H-3 part 2 (U-2.12)：import-time 已强校验 bot / event 形参，
                    # 运行期理论不可达；保留 warning 留 trace，防止依赖注入回归。
                    logger.warning(
                        f"ban 检查跳过（缺 bot/event）：command_key={normalized_key}"
                    )

                return await func(*args, **kwargs)
            except CommandUsageError:
                bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
                if bot is not None and event is not None:
                    actual_cmd = _get_raw_command()
                    at = safe_at_segment_or_empty(event.get_user_id())
                    await bot.send(event, at + " " + _build_usage_message(state.usage, actual_command=actual_cmd))
                return None
            finally:
                _current_command_context.reset(context_token)

        setattr(wrapper, "__signature__", resolved_signature)
        _original_handlers[normalized_key] = wrapper
        return wrapper

    return decorator
