from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import threading
import typing
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, NoReturn

from nonebot import get_driver
from nonebot.log import logger

from next_bot.db import CommandConfig, get_session
from next_bot.stats import increment_command_execute_total
from next_bot.time_utils import db_now_utc_naive

_ALLOWED_PARAM_TYPES = {"bool", "int", "float", "string"}
_DEFAULT_DISABLED_MODE = "reply"
_DEFAULT_DISABLED_MESSAGE = "该命令暂时关闭"


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


def _build_usage_message(usage: str) -> str:
    normalized = _normalize_usage_text(usage)
    if not normalized:
        return "命令格式错误"
    return f"格式错误，正确格式：{normalized}"


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
    }
    return hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()


def _get_registered_command(command_key: str) -> RegisteredCommand | None:
    with _registry_lock:
        return _registry.get(command_key)


def _get_disabled_policy() -> tuple[str, str]:
    config = get_driver().config
    mode = str(getattr(config, "command_disabled_mode", _DEFAULT_DISABLED_MODE)).strip().lower()
    if mode not in {"reply", "silent"}:
        mode = _DEFAULT_DISABLED_MODE
    message = str(
        getattr(config, "command_disabled_message", _DEFAULT_DISABLED_MESSAGE)
    ).strip() or _DEFAULT_DISABLED_MESSAGE
    return mode, message


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
    try:
        _ensure_runtime_cache_loaded()
    except Exception:
        pass
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


def command_control(
    *,
    command_key: str,
    display_name: str,
    permission: str,
    description: str = "",
    usage: str = "",
    default_enabled: bool = True,
    params: dict[str, dict[str, Any]] | None = None,
):
    normalized_key = str(command_key).strip()
    if not normalized_key:
        raise CommandConfigValidationError("command_key 不能为空")

    normalized_display_name = str(display_name).strip() or normalized_key
    normalized_permission = str(permission).strip()
    normalized_description = str(description).strip()
    normalized_usage = str(usage).strip()
    normalized_schema = _normalize_param_schema(params)

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
            meta_hash=meta_hash,
        )

        with _registry_lock:
            exists = _registry.get(normalized_key)
            if exists is not None and exists != registered:
                raise RuntimeError(f"duplicate command_key detected: {normalized_key}")
            _registry[normalized_key] = registered

        signature = inspect.signature(func)
        try:
            type_hints = typing.get_type_hints(func)
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
                return await func(*args, **kwargs)
            except CommandUsageError:
                bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
                if bot is not None and event is not None:
                    await bot.send(event, _build_usage_message(state.usage))
                return None
            finally:
                _current_command_context.reset(context_token)

        setattr(wrapper, "__signature__", resolved_signature)
        return wrapper

    return decorator
