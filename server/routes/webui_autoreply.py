"""关键词自动回复 CRUD endpoints。

风格与 ``webui_users.py`` / ``webui_groups.py`` 对齐：
- 共享 envelope helpers（``api_success`` / ``api_error`` / ``read_json_object``）
- 路由内 manual validation，错误转 422 ``validation_error``
- 写路径 ``commit()`` / ``rollback()`` 配对
- 写成功后调 ``invalidate_cache()`` 主动失效 plugin 缓存
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger

from nextbot.db import KeywordReply, get_session
from nextbot.time_utils import format_beijing_datetime
from server.routes import (
    api_error,
    api_success,
    read_json_object,
)
from server.routes import (
    client_ip as _client_ip,
)
from server.routes import (
    user_agent as _user_agent,
)

router = APIRouter()

_KEYWORD_MAX_LEN = 50
_REPLY_MAX_LEN = 500
_LOG_SANITIZE_MAX_LEN = 200
_LOG_CONTROL_PATTERN = re.compile(r"[\r\n\t\x00-\x1f]")


def _sanitize_log(value: Any) -> str:
    """把含 user-controlled 内容的字符串里的 newline / 控制字符替换成 ``_``。

    与 ``webui_groups.py`` 的 ``_sanitize_log`` 对齐：先截到 200 字符再替换。
    """
    text = str(value) if value is not None else ""
    if not text:
        return ""
    return _LOG_CONTROL_PATTERN.sub("_", text[:_LOG_SANITIZE_MAX_LEN])


def _invalidate_auto_reply_cache() -> None:
    """Lazy import to dodge NoneBot 'imported before loading' RuntimeError.

    ``webui_autoreply`` 是 FastAPI router 模块，会在 NoneBot ``load_plugins`` 之前
    就被 ``server/web_server.py`` import。如果在 module top-level 直接 import
    ``nextbot.plugins.auto_reply``，会把它提前塞进 ``sys.modules``，之后 NoneBot
    PluginManager 加载 plugin 时会抛出 ``RuntimeError: Module ... is not loaded as
    a plugin``。这里改为函数体内 lazy import，与 ``webui_users.py`` 引用
    ``nextbot.plugins.user_manager`` 的方式一致。
    """
    from nextbot.plugins.auto_reply import invalidate_cache

    invalidate_cache()


@dataclass(frozen=True)
class ValidatedAutoReplyPayload:
    keyword: str
    reply: str
    enabled: bool
    at_user: bool
    quote_reply: bool


class AutoReplyPayloadValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


def _normalize_keyword(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        raise AutoReplyPayloadValidationError("关键词必须是字符串", field="keyword")
    value = raw_value.strip()
    if not value:
        raise AutoReplyPayloadValidationError("关键词不能为空", field="keyword")
    if len(value) > _KEYWORD_MAX_LEN:
        raise AutoReplyPayloadValidationError(
            f"关键词过长，最多 {_KEYWORD_MAX_LEN} 个字符",
            field="keyword",
        )
    return value


def _normalize_reply(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        raise AutoReplyPayloadValidationError("回复内容必须是字符串", field="reply")
    # reply 保留换行；仅做首尾 strip 防意外尾随空白。
    value = raw_value.strip()
    if not value:
        raise AutoReplyPayloadValidationError("回复内容不能为空", field="reply")
    if len(value) > _REPLY_MAX_LEN:
        raise AutoReplyPayloadValidationError(
            f"回复内容过长，最多 {_REPLY_MAX_LEN} 个字符",
            field="reply",
        )
    return value


def _normalize_bool(raw_value: Any, *, field: str, default: bool) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    # JSON 反序列化基本只产 bool；额外支持字符串 / int 增加 PUT 局部更新
    # 的灵活性。
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        return bool(raw_value)
    if isinstance(raw_value, str):
        text = raw_value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off", ""}:
            return False
    raise AutoReplyPayloadValidationError(f"{field} 必须是布尔值", field=field)


def _validate_create_payload(payload: dict[str, Any]) -> ValidatedAutoReplyPayload:
    return ValidatedAutoReplyPayload(
        keyword=_normalize_keyword(payload.get("keyword")),
        reply=_normalize_reply(payload.get("reply")),
        enabled=_normalize_bool(payload.get("enabled"), field="enabled", default=True),
        at_user=_normalize_bool(payload.get("at_user"), field="at_user", default=True),
        quote_reply=_normalize_bool(
            payload.get("quote_reply"), field="quote_reply", default=True
        ),
    )


def _validate_update_payload(
    payload: dict[str, Any],
    *,
    current: KeywordReply,
) -> ValidatedAutoReplyPayload:
    """PUT 支持局部字段：未提供的字段沿用 current 的值。"""
    return ValidatedAutoReplyPayload(
        keyword=(
            _normalize_keyword(payload["keyword"])
            if "keyword" in payload
            else str(current.keyword)
        ),
        reply=(
            _normalize_reply(payload["reply"])
            if "reply" in payload
            else str(current.reply)
        ),
        enabled=(
            _normalize_bool(payload["enabled"], field="enabled", default=True)
            if "enabled" in payload
            else bool(current.enabled)
        ),
        at_user=(
            _normalize_bool(payload["at_user"], field="at_user", default=True)
            if "at_user" in payload
            else bool(current.at_user)
        ),
        quote_reply=(
            _normalize_bool(payload["quote_reply"], field="quote_reply", default=True)
            if "quote_reply" in payload
            else bool(current.quote_reply)
        ),
    )


def _serialize_rule(rule: KeywordReply) -> dict[str, Any]:
    return {
        "id": int(rule.id),
        "keyword": str(rule.keyword or ""),
        "reply": str(rule.reply or ""),
        "enabled": bool(rule.enabled),
        "at_user": bool(rule.at_user),
        "quote_reply": bool(rule.quote_reply),
        "created_at": format_beijing_datetime(rule.created_at),
    }


def _validation_error(exc: AutoReplyPayloadValidationError) -> JSONResponse:
    logger.warning(
        f"参数校验失败：field={_sanitize_log(exc.field or '')}，"
        f"reason={_sanitize_log(exc)}"
    )
    return api_error(
        status_code=422,
        code="validation_error",
        message=str(exc),
        details=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
    )


@router.get("/webui/api/autoreply")
async def webui_autoreply_list(request: Request) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    session = get_session()
    try:
        rules = (
            session.query(KeywordReply)
            .order_by(KeywordReply.created_at.asc(), KeywordReply.id.asc())
            .all()
        )
        serialized = [_serialize_rule(rule) for rule in rules]
        return api_success(data=serialized)
    except Exception as exc:
        logger.exception(
            f"加载自动回复列表失败：reason={_sanitize_log(exc)} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/autoreply")
async def webui_autoreply_create(request: Request) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_create_payload(data)
    except AutoReplyPayloadValidationError as exc:
        return _validation_error(exc)

    session = get_session()
    try:
        rule = KeywordReply(
            keyword=validated.keyword,
            reply=validated.reply,
            enabled=validated.enabled,
            at_user=validated.at_user,
            quote_reply=validated.quote_reply,
        )
        session.add(rule)
        session.commit()
        serialized = _serialize_rule(rule)
        new_id = int(rule.id)
        logger.info(
            f"创建自动回复规则成功：rule_id={new_id} "
            f"keyword={_sanitize_log(validated.keyword)!r} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"创建自动回复规则异常：keyword={_sanitize_log(validated.keyword)!r} "
            f"reason={_sanitize_log(exc)} client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    _invalidate_auto_reply_cache()
    return api_success(
        status_code=201,
        data=serialized,
        headers={"Location": f"/webui/api/autoreply/{new_id}"},
    )


@router.put("/webui/api/autoreply/{rule_id}")
async def webui_autoreply_update(
    request: Request,
    rule_id: int = Path(..., ge=1),
) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    session = get_session()
    try:
        rule = session.query(KeywordReply).filter(KeywordReply.id == rule_id).first()
        if rule is None:
            logger.warning(
                f"更新自动回复规则失败：rule_id={rule_id} reason=规则不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="规则不存在",
            )

        try:
            validated = _validate_update_payload(payload, current=rule)
        except AutoReplyPayloadValidationError as exc:
            return _validation_error(exc)

        rule.keyword = validated.keyword
        rule.reply = validated.reply
        rule.enabled = validated.enabled
        rule.at_user = validated.at_user
        rule.quote_reply = validated.quote_reply
        session.commit()
        serialized = _serialize_rule(rule)
        logger.info(
            f"更新自动回复规则成功：rule_id={rule_id} "
            f"keyword={_sanitize_log(validated.keyword)!r} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
    except AutoReplyPayloadValidationError as exc:
        session.rollback()
        return _validation_error(exc)
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"更新自动回复规则异常：rule_id={rule_id} reason={_sanitize_log(exc)} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    _invalidate_auto_reply_cache()
    return api_success(data=serialized)


@router.delete("/webui/api/autoreply/{rule_id}")
async def webui_autoreply_delete(
    request: Request,
    rule_id: int = Path(..., ge=1),
) -> Response:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    session = get_session()
    try:
        rule = session.query(KeywordReply).filter(KeywordReply.id == rule_id).first()
        if rule is None:
            logger.warning(
                f"删除自动回复规则失败：rule_id={rule_id} reason=规则不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="规则不存在",
            )

        keyword_for_log = str(rule.keyword or "")
        session.delete(rule)
        session.commit()
        logger.info(
            f"删除自动回复规则成功：rule_id={rule_id} "
            f"keyword={_sanitize_log(keyword_for_log)!r} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"删除自动回复规则异常：rule_id={rule_id} reason={_sanitize_log(exc)} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    _invalidate_auto_reply_cache()
    return Response(status_code=204)
