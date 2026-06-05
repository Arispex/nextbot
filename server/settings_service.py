from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from nonebot import get_driver
from nonebot.log import logger

from nextbot.data_dir import DATA_DIR

_ENV_PATH = DATA_DIR / ".env"
# M-10：read / write 共享同一把锁；保证 snapshot 读取与 save 写入串行。
# 沿用 RLock 兼容潜在 reentrant 链路（虽然当前没有，但加固时不破坏既有行为）。
_FILE_LOCK = threading.RLock()
# 向后兼容：保留旧名 ``_WRITE_LOCK``，外部模块若 import 不致 ImportError。
_WRITE_LOCK = _FILE_LOCK
# L-9：QQ 号约束收紧到 5-11 位（OneBot 平台实际范围）。
_QQ_ID_PATTERN = re.compile(r"^\d{5,11}$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# M-10：限制 .env 单次读取上限（1 MiB），防止极端膨胀场景下同步 read_text 长持锁。
_MAX_ENV_SIZE = 1 * 1024 * 1024
# M-11：明确"需要 multi-line escape 的字段"白名单，加字段时同步从
# ``_SINGLE_LINE_STRING_FIELDS`` 反向移除。
_MULTILINE_ESCAPED_FIELDS = frozenset({"group_welcome_template", "group_farewell_template"})


@dataclass(frozen=True)
class FieldSpec:
    field: str
    env_key: str
    sensitive: bool = False


@dataclass(frozen=True)
class SaveSettingsResult:
    saved_fields: list[str]
    # M-13：返回 normalize 后的当前值，前端可直接对账展示，无需再 GET 一次 snapshot。
    normalized_values: dict[str, Any] | None = None


class SettingsValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


_FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("onebot_ws_urls", "ONEBOT_WS_URLS"),
    FieldSpec("onebot_access_token", "ONEBOT_ACCESS_TOKEN", sensitive=True),
    FieldSpec("owner_id", "OWNER_ID"),
    FieldSpec("group_id", "GROUP_ID"),
    FieldSpec("web_server_host", "WEB_SERVER_HOST"),
    FieldSpec("web_server_port", "WEB_SERVER_PORT"),
    FieldSpec("web_server_public_base_url", "WEB_SERVER_PUBLIC_BASE_URL"),
    FieldSpec("command_disabled_mode", "COMMAND_DISABLED_MODE"),
    FieldSpec("command_disabled_message", "COMMAND_DISABLED_MESSAGE"),
    FieldSpec("login_notify_all_groups", "LOGIN_NOTIFY_ALL_GROUPS"),
    FieldSpec("player_notify_mode", "PLAYER_NOTIFY_MODE"),
    FieldSpec("player_notify_group_id", "PLAYER_NOTIFY_GROUP_ID"),
    FieldSpec("player_notify_online_template", "PLAYER_NOTIFY_ONLINE_TEMPLATE"),
    FieldSpec("player_notify_offline_template", "PLAYER_NOTIFY_OFFLINE_TEMPLATE"),
    FieldSpec("chat_sync_mode", "CHAT_SYNC_MODE"),
    FieldSpec("chat_sync_group_id", "CHAT_SYNC_GROUP_ID"),
    FieldSpec("chat_sync_template", "CHAT_SYNC_TEMPLATE"),
    FieldSpec("boss_notify_mode", "BOSS_NOTIFY_MODE"),
    FieldSpec("boss_notify_group_id", "BOSS_NOTIFY_GROUP_ID"),
    FieldSpec("boss_notify_template", "BOSS_NOTIFY_TEMPLATE"),
    FieldSpec("group_welcome_enabled", "GROUP_WELCOME_ENABLED"),
    FieldSpec("group_welcome_template", "GROUP_WELCOME_TEMPLATE"),
    FieldSpec("group_farewell_enabled", "GROUP_FAREWELL_ENABLED"),
    FieldSpec("group_farewell_template", "GROUP_FAREWELL_TEMPLATE"),
    FieldSpec("group_auto_ban_on_leave_enabled", "GROUP_AUTO_BAN_ON_LEAVE_ENABLED"),
    FieldSpec("group_auto_ban_on_leave_notify", "GROUP_AUTO_BAN_ON_LEAVE_NOTIFY"),
)

_FIELD_BY_NAME: dict[str, FieldSpec] = {item.field: item for item in _FIELD_SPECS}
_FIELD_BY_ENV: dict[str, FieldSpec] = {item.env_key: item for item in _FIELD_SPECS}
_SINGLE_LINE_STRING_FIELDS = {
    "onebot_access_token",
    "web_server_host",
    "web_server_public_base_url",
    "command_disabled_mode",
    "command_disabled_message",
    "player_notify_mode",
    "player_notify_group_id",
    "player_notify_online_template",
    "player_notify_offline_template",
    "chat_sync_mode",
    "chat_sync_group_id",
    "chat_sync_template",
    "boss_notify_mode",
    "boss_notify_group_id",
    "boss_notify_template",
}


def _parse_env_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in line:
        return None
    key = line.split("=", 1)[0].strip()
    if not _ENV_KEY_PATTERN.fullmatch(key):
        return None
    return key


def _read_env_lines() -> list[str]:
    if not _ENV_PATH.is_file():
        return []
    # M-10：超过上限直接 raise，防 .env 异常膨胀（外部 bug / 攻击）拖慢同步读
    try:
        size = _ENV_PATH.stat().st_size
    except OSError:
        size = 0
    if size > _MAX_ENV_SIZE:
        raise SettingsValidationError(
            f".env 文件超出 {_MAX_ENV_SIZE} 字节上限，当前 {size} 字节"
        )
    return _ENV_PATH.read_text(encoding="utf-8").splitlines()


def _read_env_values() -> dict[str, str]:
    # M-10：read 侧也持锁，保证 snapshot 与 save 之间无中间态读取。
    with _FILE_LOCK:
        values: dict[str, str] = {}
        for line in _read_env_lines():
            key = _parse_env_key(line)
            if key is None:
                continue
            values[key] = line.split("=", 1)[1]
        return values


def _escape_for_env(text: str) -> str:
    """L-10：把含换行 / 反斜杠的字符串编码到 .env 单行格式。

    Round-trip 约定：先 ``\\`` 加倍，再 ``\n`` 转义，最后丢 ``\r``；
    ``_unescape_from_env`` 反向先恢复换行再恢复反斜杠（顺序敏感）。
    """
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "")


def _unescape_from_env(text: str) -> str:
    """L-10：把 .env 单行字符串还原成多行原文（反向 ``_escape_for_env``）。"""
    return text.replace("\\n", "\n").replace("\\\\", "\\")


def _serialize_env_value(field: str, value: Any) -> str:
    if field in {"onebot_ws_urls", "owner_id", "group_id"}:
        return json.dumps(value, ensure_ascii=False)
    if field == "web_server_port":
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if field in _MULTILINE_ESCAPED_FIELDS:
        return _escape_for_env(str(value))
    return str(value)


def _write_env_values(normalized_values: dict[str, Any]) -> None:
    with _WRITE_LOCK:
        lines = _read_env_lines()
        existing_indices: dict[str, list[int]] = {}

        for index, line in enumerate(lines):
            key = _parse_env_key(line)
            if key in _FIELD_BY_ENV:
                existing_indices.setdefault(key, []).append(index)

        new_lines = list(lines)
        remove_indexes: set[int] = set()
        append_lines: list[str] = []

        for spec in _FIELD_SPECS:
            if spec.field not in normalized_values:
                continue

            env_line = f"{spec.env_key}={_serialize_env_value(spec.field, normalized_values[spec.field])}"
            indices = existing_indices.get(spec.env_key, [])
            if indices:
                new_lines[indices[0]] = env_line
                for idx in indices[1:]:
                    remove_indexes.add(idx)
            else:
                append_lines.append(env_line)

        if remove_indexes:
            new_lines = [
                line for idx, line in enumerate(new_lines)
                if idx not in remove_indexes
            ]

        if append_lines:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.extend(append_lines)

        output = "\n".join(new_lines).rstrip("\n") + "\n"
        temp_path = _ENV_PATH.with_suffix(".env.tmp")
        temp_path.write_text(output, encoding="utf-8")
        temp_path.replace(_ENV_PATH)


def _coerce_string(value: Any, *, field: str, allow_empty: bool = False) -> str:
    text = str(value).strip()
    if not allow_empty and not text:
        raise SettingsValidationError(f"{field} 不能为空", field=field)
    return text


def _assert_single_line_string(field: str, value: Any) -> None:
    if field not in _SINGLE_LINE_STRING_FIELDS:
        return
    raw_text = str(value)
    if "\r" in raw_text or "\n" in raw_text:
        raise SettingsValidationError(f"{field} 不能包含换行", field=field)


def _coerce_list_of_str(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SettingsValidationError(f"{field} 必须是 JSON 数组", field=field)
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            raise SettingsValidationError(f"{field} 不能包含空项", field=field)
        normalized.append(text)
    return normalized


def _coerce_qq_id_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SettingsValidationError(f"{field} 必须是 JSON 数组", field=field)

    values: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        values.append(text)

    for item in values:
        if _QQ_ID_PATTERN.fullmatch(item) is None:
            raise SettingsValidationError(f"{field} 仅支持 5-11 位数字", field=field)
    return values


def _coerce_ws_urls(value: Any, *, field: str) -> list[str]:
    values = _coerce_list_of_str(value, field=field)
    for item in values:
        parsed = urlparse(item)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise SettingsValidationError(f"{field} 必须是 ws/wss URL", field=field)
    return values


def _coerce_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise SettingsValidationError(f"{field} 必须是 true 或 false", field=field)


def _coerce_http_url(value: Any, *, field: str) -> str:
    text = _coerce_string(value, field=field)
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SettingsValidationError(f"{field} 必须是 http/https URL", field=field)
    return text.rstrip("/")


def _coerce_port(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise SettingsValidationError(f"{field} 必须是整数", field=field)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsValidationError(f"{field} 必须是整数", field=field) from exc
    if parsed < 1 or parsed > 65535:
        raise SettingsValidationError(f"{field} 范围必须在 1-65535", field=field)
    return parsed


def _normalize_field(field: str, value: Any) -> Any:
    if field == "onebot_ws_urls":
        return _coerce_ws_urls(value, field=field)
    if field == "onebot_access_token":
        return _coerce_string(value, field=field)
    if field == "owner_id":
        return _coerce_qq_id_list(value, field=field)
    if field == "group_id":
        return _coerce_qq_id_list(value, field=field)
    if field == "web_server_host":
        return _coerce_string(value, field=field)
    if field == "web_server_port":
        return _coerce_port(value, field=field)
    if field == "web_server_public_base_url":
        return _coerce_http_url(value, field=field)
    if field == "command_disabled_mode":
        mode = _coerce_string(value, field=field).lower()
        if mode not in {"reply", "silent"}:
            raise SettingsValidationError(
                "command_disabled_mode 仅支持 reply 或 silent",
                field=field,
            )
        return mode
    if field == "command_disabled_message":
        return _coerce_string(value, field=field)
    if field == "login_notify_all_groups":
        return _coerce_bool(value, field=field)
    if field == "player_notify_mode":
        mode = _coerce_string(value, field=field, allow_empty=True).lower()
        if mode not in {"all", "single"}:
            return "all"
        return mode
    if field == "player_notify_group_id":
        return _coerce_string(value, field=field, allow_empty=True)
    if field in {"player_notify_online_template", "player_notify_offline_template"}:
        return _coerce_string(value, field=field, allow_empty=True)
    if field == "chat_sync_mode":
        mode = _coerce_string(value, field=field, allow_empty=True).lower()
        if mode not in {"all", "single"}:
            return "all"
        return mode
    if field == "chat_sync_group_id":
        return _coerce_string(value, field=field, allow_empty=True)
    if field == "chat_sync_template":
        return _coerce_string(value, field=field, allow_empty=True)
    if field == "boss_notify_mode":
        mode = _coerce_string(value, field=field, allow_empty=True).lower()
        if mode not in {"all", "single"}:
            return "all"
        return mode
    if field == "boss_notify_group_id":
        return _coerce_string(value, field=field, allow_empty=True)
    if field == "boss_notify_template":
        return _coerce_string(value, field=field, allow_empty=True)
    if field in {"group_welcome_enabled", "group_farewell_enabled"}:
        return _coerce_bool(value, field=field)
    if field in {"group_welcome_template", "group_farewell_template"}:
        return _coerce_string(value, field=field, allow_empty=True)
    if field in {"group_auto_ban_on_leave_enabled", "group_auto_ban_on_leave_notify"}:
        return _coerce_bool(value, field=field)
    raise SettingsValidationError("不支持的配置项", field=field)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _FIELD_BY_NAME:
            raise SettingsValidationError(f"不允许修改字段：{key}", field=key)
        _assert_single_line_string(key, value)
        normalized[key] = _normalize_field(key, value)
    if not normalized:
        raise SettingsValidationError("至少提交一个字段")
    return normalized


def _parse_json_array_env(raw: str, *, field: str) -> list[Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettingsValidationError(f"{field} 不是有效 JSON 数组", field=field) from exc
    if not isinstance(parsed, list):
        raise SettingsValidationError(f"{field} 必须是 JSON 数组", field=field)
    return parsed


def _load_value_from_env(field: str, raw_value: str) -> Any:
    if field in {"onebot_ws_urls", "owner_id", "group_id"}:
        values = _parse_json_array_env(raw_value, field=field)
        return _normalize_field(field, values)
    if field == "web_server_port":
        return _coerce_port(raw_value, field=field)
    if field == "login_notify_all_groups":
        return _coerce_bool(raw_value, field=field)
    if field in {"group_welcome_enabled", "group_farewell_enabled"}:
        return _coerce_bool(raw_value, field=field)
    if field in _MULTILINE_ESCAPED_FIELDS:
        unescaped = _unescape_from_env(raw_value)
        return _normalize_field(field, unescaped)
    if field in {"group_auto_ban_on_leave_enabled", "group_auto_ban_on_leave_notify"}:
        return _coerce_bool(raw_value, field=field)
    return _normalize_field(field, raw_value)


def _load_value_from_config(field: str, config: Any) -> Any:
    raw_value = getattr(config, field, None)
    if field in {"onebot_ws_urls", "owner_id", "group_id"}:
        if raw_value is None:
            return []
        if isinstance(raw_value, (set, tuple)):
            raw_value = list(raw_value)
        if isinstance(raw_value, list):
            raw_value = [item for item in raw_value if str(item).strip()]
        return _normalize_field(field, raw_value)
    if field == "web_server_port":
        return _coerce_port(raw_value if raw_value is not None else 18081, field=field)
    if field == "web_server_public_base_url" and raw_value is None:
        host = getattr(config, "web_server_host", "127.0.0.1")
        port = getattr(config, "web_server_port", 18081)
        raw_value = f"http://{host}:{port}"
    if field == "command_disabled_mode" and raw_value is None:
        raw_value = "reply"
    if field == "command_disabled_message" and raw_value is None:
        raw_value = "该命令暂时关闭"
    if field == "login_notify_all_groups":
        return _coerce_bool(raw_value if raw_value is not None else False, field=field)
    if field == "player_notify_mode" and raw_value is None:
        raw_value = "all"
    if field == "player_notify_group_id" and raw_value is None:
        raw_value = ""
    if field == "player_notify_online_template" and raw_value is None:
        raw_value = "[{server}]{player} 上线了"
    if field == "player_notify_offline_template" and raw_value is None:
        raw_value = "[{server}]{player} 下线了"
    if field == "chat_sync_mode" and raw_value is None:
        raw_value = "all"
    if field == "chat_sync_group_id" and raw_value is None:
        raw_value = ""
    if field == "chat_sync_template" and raw_value is None:
        raw_value = "[{server}]{player}：{message}"
    if field == "boss_notify_mode" and raw_value is None:
        raw_value = "all"
    if field == "boss_notify_group_id" and raw_value is None:
        raw_value = ""
    if field == "boss_notify_template" and raw_value is None:
        raw_value = "[{server}]{player} 召唤了 {boss}"
    if field in {"group_welcome_enabled", "group_farewell_enabled"}:
        return _coerce_bool(raw_value if raw_value is not None else False, field=field)
    if field == "group_welcome_template":
        if raw_value is None:
            raw_value = "{at} 欢迎加入本群！\n请先阅读群公告~"
        else:
            raw_value = _unescape_from_env(str(raw_value))
    if field == "group_farewell_template":
        if raw_value is None:
            raw_value = "{nickname}（{user_id}）离开了本群"
        else:
            raw_value = _unescape_from_env(str(raw_value))
    if field in {"group_auto_ban_on_leave_enabled", "group_auto_ban_on_leave_notify"}:
        return _coerce_bool(raw_value if raw_value is not None else False, field=field)
    return _normalize_field(field, raw_value if raw_value is not None else "")


def get_settings_snapshot() -> dict[str, Any]:
    env_values = _read_env_values()
    config = get_driver().config
    data: dict[str, Any] = {}
    for spec in _FIELD_SPECS:
        raw = env_values.get(spec.env_key)
        if raw is not None:
            try:
                data[spec.field] = _load_value_from_env(spec.field, raw)
                continue
            except SettingsValidationError as exc:
                # M-12：.env 中的字段无法通过 normalize 时降级到 config 默认值。
                # 静默 fallback 会让用户以为自己的修改已生效，必须 WARN 暴露。
                logger.warning(
                    f"读取 settings 字段失败，回退到默认值：field={spec.field} reason={exc}"
                )
        data[spec.field] = _load_value_from_config(spec.field, config)
    return data


def save_settings(payload: dict[str, Any]) -> SaveSettingsResult:
    normalized_values = _normalize_payload(payload)
    _write_env_values(normalized_values)
    saved_fields = [spec.field for spec in _FIELD_SPECS if spec.field in normalized_values]
    # M-13：把 normalize 后的当前值随返回；前端可直接 fillForm 更新展示，避免再 GET 一次。
    return SaveSettingsResult(
        saved_fields=saved_fields,
        normalized_values={field: normalized_values[field] for field in saved_fields},
    )


def get_settings_metadata() -> dict[str, Any]:
    return {
        "managed_fields": [item.field for item in _FIELD_SPECS],
        "sensitive_fields": [item.field for item in _FIELD_SPECS if item.sensitive],
    }
