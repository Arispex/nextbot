"""服务器配置入参的公共校验 helper。

历史上 webui 路由有完整的 _validate_server_payload，bot 端 handle_add_server
零校验，导致 bot 端可写入空 ip / 非数字端口 / 空 token 的脏行（SM-1.3）。
本模块抽出统一规则，供 webui 与 bot 共用，避免出现两个真源。

防御重点：
- name 走与 webui 一致的正则；显式拒绝 \\n / \\r（SM-1.5），即使 parser 改用 shlex 也不破防。
- ip 仅做长度与非空校验（受当前部署 / 网络环境约束，不强限制 host 形态）。
- 端口必为 1-65535 整数，落库统一为 str（与 ORM 字段类型一致）。
- token 长度 1-128，显式拒绝换行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9一-鿿 ._-]{1,32}$")
_FORBIDDEN_NAME_CHARS = ("\n", "\r")
_MAX_IP_LENGTH = 128
_MAX_TOKEN_LENGTH = 128


@dataclass(frozen=True)
class ValidatedServerPayload:
    name: str
    ip: str
    game_port: str
    restapi_port: str
    token: str


class ServerPayloadValidationError(ValueError):
    def __init__(self, reason: str, *, field: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.field = field


def _check_no_newline(value: str, *, field: str) -> None:
    for ch in _FORBIDDEN_NAME_CHARS:
        if ch in value:
            raise ServerPayloadValidationError(
                f"{field} 不允许包含换行符", field=field
            )


def _normalize_name(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("服务器名称不能为空", field="name")
    _check_no_newline(value, field="name")
    if _NAME_PATTERN.fullmatch(value) is None:
        raise ServerPayloadValidationError(
            "服务器名称格式错误，仅允许中英文、数字、空格和 -_.，长度 1-32",
            field="name",
        )
    return value


def _normalize_host(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("服务器地址不能为空", field="ip")
    _check_no_newline(value, field="ip")
    if len(value) > _MAX_IP_LENGTH:
        raise ServerPayloadValidationError(
            f"服务器地址长度不能超过 {_MAX_IP_LENGTH}", field="ip"
        )
    return value


def _normalize_port(raw_value: Any, *, field: str) -> str:
    if isinstance(raw_value, bool):
        raise ServerPayloadValidationError("端口必须是 1-65535 的整数", field=field)

    parsed: int
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise ServerPayloadValidationError("端口必须是整数", field=field)
        parsed = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            raise ServerPayloadValidationError("端口不能为空", field=field)
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ServerPayloadValidationError("端口必须是整数", field=field) from exc
    else:
        raise ServerPayloadValidationError("端口必须是整数", field=field)

    if not 1 <= parsed <= 65535:
        raise ServerPayloadValidationError("端口范围必须在 1-65535", field=field)
    return str(parsed)


def _normalize_token(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("Token 不能为空", field="token")
    _check_no_newline(value, field="token")
    if not 1 <= len(value) <= _MAX_TOKEN_LENGTH:
        raise ServerPayloadValidationError(
            f"Token 长度必须在 1-{_MAX_TOKEN_LENGTH} 之间", field="token"
        )
    return value


def validate_server_payload(
    name: Any,
    ip: Any,
    game_port: Any,
    restapi_port: Any,
    token: Any,
) -> ValidatedServerPayload:
    """位置参数版本，便于 bot handler 使用 args 解包后直接传入。"""
    return ValidatedServerPayload(
        name=_normalize_name(name),
        ip=_normalize_host(ip),
        game_port=_normalize_port(game_port, field="game_port"),
        restapi_port=_normalize_port(restapi_port, field="restapi_port"),
        token=_normalize_token(token),
    )


def validate_server_payload_dict(payload: dict[str, Any]) -> ValidatedServerPayload:
    """Dict 版本，给 webui 路由使用，与历史接口保持一致。"""
    for key in ("name", "ip", "game_port", "restapi_port", "token"):
        if key not in payload:
            raise ServerPayloadValidationError(f"{key} 为必填项", field=key)
    return validate_server_payload(
        name=payload.get("name"),
        ip=payload.get("ip"),
        game_port=payload.get("game_port"),
        restapi_port=payload.get("restapi_port"),
        token=payload.get("token"),
    )
