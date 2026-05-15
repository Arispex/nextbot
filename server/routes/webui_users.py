from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response
from nonebot.log import logger
from sqlalchemy import func, or_

from nextbot.access_control import get_owner_ids
from nextbot.db import Group, Server, User, get_session
from nextbot.server_broadcast import BroadcastOutcome, broadcast
from nextbot.time_utils import db_now_utc_naive, format_beijing_datetime
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from server.routes import (
    api_error,
    api_success,
    build_pagination_slice,
    client_ip as _client_ip,
    read_json_object,
    read_pagination_query,
    user_agent as _shared_user_agent,
)

router = APIRouter()

_USER_ID_PATTERN = re.compile(r"^\d{5,20}$")
_USER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff]+$")
_MAX_USER_NAME_LENGTH = 16
_KEYWORD_MAX_LENGTH = 128  # H-6：搜索关键字长度上限
_PER_PAGE_MAX = 100  # 每页上限，仅对非全表请求 cap

# M-4：sync-whitelist 每用户 5s 冷却（curl 直连同样限流）
_SYNC_COOLDOWN_SECONDS = 5.0
_sync_cooldown_lock = Lock()
_sync_last_request: dict[int, float] = {}


# CRIT-1 / HIGH-2：thin re-export alias；canonical helper 在 server/routes/__init__.py。
_user_agent = _shared_user_agent


def _mask_qq(qq: str) -> str:
    """M-2：QQ 中间打码，仅保留首尾 2 位，防 PII 落日志。"""
    text = str(qq or "")
    if len(text) < 4:
        return text
    return text[:2] + "***" + text[-2:]


def _sanitize_log_text(text: str) -> str:
    """M-2：剔除 CR / LF 防 log injection，限制单条 reason 长度。"""
    return str(text or "").replace("\n", "\\n").replace("\r", "\\r")[:200]


def _escape_like_keyword(keyword: str) -> str:
    """H-2：转义 ilike 通配符 % / _ / \\，避免 keyword 注入 LIKE 模式。"""
    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _check_sync_cooldown(user_db_id: int) -> tuple[bool, float]:
    """M-4：返回 (allowed, remaining_seconds)；首次调用直接放行。"""
    now = time.monotonic()
    with _sync_cooldown_lock:
        last = _sync_last_request.get(user_db_id)
        if last is not None and (now - last) < _SYNC_COOLDOWN_SECONDS:
            remaining = _SYNC_COOLDOWN_SECONDS - (now - last)
            return False, remaining
        _sync_last_request[user_db_id] = now
        return True, 0.0


@dataclass(frozen=True)
class ValidatedUserPayload:
    user_id: str
    name: str
    coins: int
    sign_total: int
    sign_streak: int
    permissions: str
    group: str


class UserPayloadValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


def _require_field(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise UserPayloadValidationError(f"{key} 为必填项", field=key)
    return payload.get(key)


def _normalize_user_id(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("用户 QQ 不能为空", field="user_id")
    if _USER_ID_PATTERN.fullmatch(value) is None:
        raise UserPayloadValidationError("用户 QQ 必须是 5-20 位数字", field="user_id")
    return value


def _normalize_user_name(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("用户名称不能为空", field="name")
    if len(value) > _MAX_USER_NAME_LENGTH:
        raise UserPayloadValidationError(
            f"用户名称过长，最多 {_MAX_USER_NAME_LENGTH} 个字符",
            field="name",
        )
    if value.isdigit():
        raise UserPayloadValidationError("用户名称不能为纯数字", field="name")
    if _USER_NAME_PATTERN.fullmatch(value) is None:
        raise UserPayloadValidationError(
            "用户名称不能包含符号，只能使用中文、英文和数字",
            field="name",
        )
    return value


def _normalize_coins(raw_value: Any) -> int:
    if isinstance(raw_value, bool):
        raise UserPayloadValidationError("金币必须是非负整数", field="coins")

    parsed: int
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise UserPayloadValidationError("金币必须是整数", field="coins")
        parsed = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            raise UserPayloadValidationError("金币不能为空", field="coins")
        try:
            parsed = int(text)
        except ValueError as exc:
            raise UserPayloadValidationError("金币必须是整数", field="coins") from exc
    else:
        raise UserPayloadValidationError("金币必须是整数", field="coins")

    if parsed < 0:
        raise UserPayloadValidationError("金币必须是非负整数", field="coins")
    return parsed


def _normalize_sign_count(raw_value: Any, field: str) -> int:
    if isinstance(raw_value, bool):
        raise UserPayloadValidationError(f"{field} 必须是非负整数", field=field)
    parsed: int
    if isinstance(raw_value, int):
        parsed = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            raise UserPayloadValidationError(f"{field} 必须是整数", field=field)
        parsed = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            parsed = 0
        else:
            try:
                parsed = int(text)
            except ValueError as exc:
                raise UserPayloadValidationError(f"{field} 必须是整数", field=field) from exc
    else:
        raise UserPayloadValidationError(f"{field} 必须是整数", field=field)
    if parsed < 0:
        raise UserPayloadValidationError(f"{field} 必须是非负整数", field=field)
    return parsed


def _normalize_permissions(raw_value: Any) -> str:
    if raw_value is None:
        return ""
    text = str(raw_value).strip()
    if not text:
        return ""
    values = sorted({item.strip() for item in text.split(",") if item.strip()})
    return ",".join(values)


def _normalize_group(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise UserPayloadValidationError("身份组不能为空", field="group")
    return value


def _validate_payload(payload: dict[str, Any]) -> ValidatedUserPayload:
    user_id = _normalize_user_id(_require_field(payload, "user_id"))
    name = _normalize_user_name(_require_field(payload, "name"))
    coins = _normalize_coins(_require_field(payload, "coins"))
    sign_total = _normalize_sign_count(payload.get("sign_total", 0), "sign_total")
    sign_streak = _normalize_sign_count(payload.get("sign_streak", 0), "sign_streak")
    permissions = _normalize_permissions(payload.get("permissions", ""))
    group = _normalize_group(_require_field(payload, "group"))
    return ValidatedUserPayload(
        user_id=user_id,
        name=name,
        coins=coins,
        sign_total=sign_total,
        sign_streak=sign_streak,
        permissions=permissions,
        group=group,
    )


def _format_created_at(value: datetime | None) -> str:
    return format_beijing_datetime(value)


def _serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": int(user.id),
        "user_id": str(user.user_id),
        "name": str(user.name),
        "coins": int(user.coins),
        "sign_total": int(user.sign_total or 0),
        "sign_streak": int(user.sign_streak or 0),
        "permissions": str(user.permissions or ""),
        "group": str(user.group),
        "is_banned": bool(user.is_banned),
        "banned_at": format_beijing_datetime(user.banned_at) if user.banned_at else "",
        "ban_reason": str(user.ban_reason or ""),
        "created_at": _format_created_at(user.created_at),
    }


async def _sync_user_whitelist(user: User) -> list[dict[str, Any]]:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    # R2-T-1：原 `for server in servers:` 串行（N×5s timeout）改为 broadcast 并行，
    # 避免前端 15s timeout 在 N≥3 服务器场景必触发的回归。
    user_name = str(user.name)

    async def _one(server: Server) -> BroadcastOutcome[str]:
        try:
            response = await request_server_api(
                server,
                "/v3/server/rawcmd",
                params={"cmd": f"/bwl add {user_name}"},
            )
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            return BroadcastOutcome(
                server=server, ok=True, detail="", payload=None
            )
        return BroadcastOutcome(
            server=server,
            ok=False,
            detail=get_error_reason(response),
            payload=None,
        )

    outcomes = await broadcast(servers, _one)
    return [
        {
            "server_id": int(o.server.id),
            "server_name": str(o.server.name),
            "success": o.ok,
            "reason": "" if o.ok else o.detail,
        }
        for o in outcomes
    ]


def _validation_error(exc: UserPayloadValidationError) -> JSONResponse:
    logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc}")
    return api_error(
        status_code=422,
        code="validation_error",
        message=str(exc),
        details=[{"field": exc.field, "message": str(exc)}] if exc.field else None,
    )


@router.get("/webui/api/users")
async def webui_users_list(request: Request) -> JSONResponse:
    pagination, error_response = read_pagination_query(request)
    if error_response is not None:
        return error_response
    assert pagination is not None

    page = max(1, int(pagination["page"]))
    per_page_raw = int(pagination["per_page"])
    fetch_all = per_page_raw == 0  # 0 = 取全表
    if fetch_all:
        per_page = 0
    elif per_page_raw < 0 or per_page_raw > _PER_PAGE_MAX:
        per_page = min(_PER_PAGE_MAX, max(1, per_page_raw))
    else:
        per_page = per_page_raw

    # H-6：keyword 长度截断，防 DoS / 超长 LIKE
    keyword = str(request.query_params.get("q") or "").strip()[:_KEYWORD_MAX_LENGTH]

    session = get_session()
    try:
        # 搜索下推 SQL ilike，避免全表加载到内存
        base = session.query(User)
        if keyword:
            escaped = _escape_like_keyword(keyword)
            pattern = f"%{escaped}%"
            base = base.filter(
                or_(
                    User.user_id.ilike(pattern, escape="\\"),
                    User.name.ilike(pattern, escape="\\"),
                )
            )

        total = int(base.with_entities(func.count(User.id)).scalar() or 0)

        if fetch_all:
            users = base.order_by(User.id.asc()).all()
            meta = {
                "page": 1,
                "per_page": total,
                "total": total,
                "total_pages": 1,
            }
        else:
            meta, offset, limit = build_pagination_slice(
                total=total,
                page=page,
                per_page=per_page,
            )
            users = (
                base.order_by(User.id.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
        serialized = [_serialize_user(item) for item in users]
        return api_success(data=serialized, meta=meta)
    except Exception as exc:
        logger.exception(f"加载用户列表失败：reason={exc}")
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/users")
async def webui_users_create(request: Request) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    try:
        validated = _validate_payload(data)
    except UserPayloadValidationError as exc:
        return _validation_error(exc)

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    session = get_session()
    try:
        if session.query(User).filter(User.user_id == validated.user_id).first() is not None:
            return api_error(
                status_code=409,
                code="conflict",
                message="用户 QQ 已存在",
                details=[{"field": "user_id", "message": "用户 QQ 已存在"}],
            )

        if session.query(User).filter(func.lower(User.name) == validated.name.lower()).first() is not None:
            return api_error(
                status_code=409,
                code="conflict",
                message="用户名称已被占用",
                details=[{"field": "name", "message": "用户名称已被占用"}],
            )

        if session.query(Group).filter(Group.name == validated.group).first() is None:
            return api_error(
                status_code=422,
                code="validation_error",
                message="身份组不存在",
                details=[{"field": "group", "message": "身份组不存在"}],
            )

        user = User(
            user_id=validated.user_id,
            name=validated.name,
            coins=validated.coins,
            sign_total=validated.sign_total,
            sign_streak=validated.sign_streak,
            permissions=validated.permissions,
            group=validated.group,
        )
        session.add(user)
        session.commit()
        logger.info(
            f"创建用户成功：user_id={_mask_qq(user.user_id)}，name={user.name} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(
            status_code=201,
            data=_serialize_user(user),
            headers={"Location": f"/webui/api/users/{user.id}"},
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"创建用户异常：user_id={_mask_qq(validated.user_id)}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.put("/webui/api/users/{user_id}")
async def webui_users_update(
    request: Request,
    user_id: int = Path(..., ge=1),  # H-5
) -> JSONResponse:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None

    try:
        validated = _validate_payload(payload)
    except UserPayloadValidationError as exc:
        return _validation_error(exc)

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning(
                f"更新用户失败：user_id={user_id}，reason=用户不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="用户不存在",
            )

        # H-4：Owner 边界检查（与 ban 路由对齐）
        if str(user.user_id) in get_owner_ids():
            logger.warning(
                f"更新用户被拒：user_id={user_id}，reason=owner_protected "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=403,
                code="owner_protected",
                message="不能对管理员执行此操作",
            )

        if (
            session.query(User)
            .filter(User.user_id == validated.user_id, User.id != user_id)
            .first()
            is not None
        ):
            return api_error(
                status_code=409,
                code="conflict",
                message="用户 QQ 已存在",
                details=[{"field": "user_id", "message": "用户 QQ 已存在"}],
            )

        if (
            session.query(User)
            .filter(func.lower(User.name) == validated.name.lower(), User.id != user_id)
            .first()
            is not None
        ):
            return api_error(
                status_code=409,
                code="conflict",
                message="用户名称已被占用",
                details=[{"field": "name", "message": "用户名称已被占用"}],
            )

        if session.query(Group).filter(Group.name == validated.group).first() is None:
            return api_error(
                status_code=422,
                code="validation_error",
                message="身份组不存在",
                details=[{"field": "group", "message": "身份组不存在"}],
            )

        user.user_id = validated.user_id
        user.name = validated.name
        user.coins = validated.coins
        user.sign_total = validated.sign_total
        user.sign_streak = validated.sign_streak
        user.permissions = validated.permissions
        user.group = validated.group
        session.commit()
        logger.info(
            f"更新用户成功：user_id={user_id}，account_id={_mask_qq(user.user_id)} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_success(data=_serialize_user(user))
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"更新用户异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.delete("/webui/api/users/{user_id}")
async def webui_users_delete(
    request: Request,
    user_id: int = Path(..., ge=1),  # H-5
) -> JSONResponse:
    # M-1：补 request 参数；H-1：日志补 client_ip / user_agent
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning(
                f"删除用户失败：user_id={user_id}，reason=用户不存在 "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="用户不存在",
            )

        # H-4：Owner 不可被删除
        if str(user.user_id) in get_owner_ids():
            logger.warning(
                f"删除用户被拒：user_id={user_id}，reason=owner_protected "
                f"client_ip={client_ip}"
            )
            return api_error(
                status_code=403,
                code="owner_protected",
                message="不能对管理员执行此操作",
            )

        deleted_user_id = str(user.user_id)
        deleted_name = str(user.name)
        session.delete(user)
        session.commit()
        logger.info(
            f"删除用户成功：user_id={user_id}，account_id={_mask_qq(deleted_user_id)}，"
            f"name={deleted_name} client_ip={client_ip} user_agent={user_agent!r}"
        )
        return Response(status_code=204)
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"删除用户异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()


@router.post("/webui/api/users/{user_id}/sync-whitelist")
async def webui_users_sync_whitelist(
    request: Request,
    user_id: int = Path(..., ge=1),  # H-5
) -> JSONResponse:
    # M-1：补 request 参数；H-1：日志补 client_ip / user_agent
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    # M-4：5s cooldown，避免单击连发 + curl 直连泛洪
    allowed, remaining = _check_sync_cooldown(user_id)
    if not allowed:
        logger.warning(
            f"同步用户白名单被限流：user_id={user_id}，"
            f"retry_after={remaining:.1f}s client_ip={client_ip}"
        )
        return api_error(
            status_code=429,
            code="rate_limited",
            message="操作过于频繁，请稍后再试",
        )

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
    except Exception as exc:
        logger.exception(
            f"同步用户白名单异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
    finally:
        session.close()

    if user is None:
        logger.warning(
            f"同步用户白名单失败：user_id={user_id}，reason=用户不存在 "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=404,
            code="not_found",
            message="用户不存在",
        )

    try:
        results = await _sync_user_whitelist(user)
    except Exception as exc:
        logger.exception(
            f"同步用户白名单异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )

    if not results:
        logger.warning(
            f"同步用户白名单失败：user_id={user_id}，reason=暂无可同步的服务器 "
            f"client_ip={client_ip}"
        )
    else:
        logger.info(
            f"同步用户白名单完成：user_id={user_id}，server_count={len(results)} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )

    return api_success(
        data={
            "user_id": str(user.user_id),
            "name": str(user.name),
            "results": results,
        }
    )


@router.post("/webui/api/users/{user_id}/ban")
async def webui_users_ban(
    request: Request,
    user_id: int = Path(..., ge=1),  # H-5
) -> JSONResponse:
    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    reason = str(data.get("reason", "")).strip()
    if not reason:
        return api_error(
            status_code=422,
            code="validation_error",
            message="封禁原因不能为空",
            details=[{"field": "reason", "message": "封禁原因不能为空"}],
        )

    # H-1：日志补 client_ip / user_agent
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)
    # M-2：reason 防 log injection
    sanitized_reason = _sanitize_log_text(reason)

    # H-3：user_name / user_qq 在 try 入口最早就读出，避免 except / finally 引用未绑定变量
    user_name = ""
    user_qq = ""

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            return api_error(status_code=404, code="not_found", message="用户不存在")

        if str(user.user_id) in get_owner_ids():
            return api_error(
                status_code=403,
                code="owner_protected",
                message="不能对管理员执行此操作",
            )

        if user.is_banned:
            return api_error(
                status_code=409,
                code="conflict",
                message="该用户已被封禁",
                details=[{"reason": str(user.ban_reason or "")}],
            )

        # H-3：在 mutation / commit 之前先读出，except 分支也能安全引用
        user_name = str(user.name)
        user_qq = str(user.user_id)

        user.is_banned = True
        user.banned_at = db_now_utc_naive()
        user.ban_reason = reason
        session.commit()

        logger.info(
            f"WebUI 封禁用户成功：user_id={_mask_qq(user_qq)} name={user_name} "
            f"reason={sanitized_reason} client_ip={client_ip} user_agent={user_agent!r}"
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"WebUI 封禁用户异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(status_code=500, code="internal_error", message="内部错误")
    finally:
        session.close()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    # R2-T-2：原串行 `for server in servers:` 改为 broadcast 并行，
    # 避免 N≥4 服务器场景下前端 15s timeout 必触发的回归。
    async def _ban_one(server: Server) -> BroadcastOutcome[str]:
        try:
            check_response = await request_server_api(server, "/nextbot/blacklist")
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(check_response):
            payload = check_response.payload if isinstance(check_response.payload, dict) else {}
            entries = payload.get("entries", [])
            already_exists = any(
                isinstance(e, dict)
                and str(e.get("username", "")).lower() == user_name.lower()
                for e in (entries if isinstance(entries, list) else [])
            )
            if already_exists:
                return BroadcastOutcome(
                    server=server, ok=True, detail="已存在于黑名单中", payload=None
                )

        try:
            response = await request_server_api(
                server,
                f"/nextbot/blacklist/add/{user_name}",
                params={"reason": reason},
            )
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            return BroadcastOutcome(
                server=server, ok=True, detail="", payload=None
            )
        return BroadcastOutcome(
            server=server, ok=False, detail=get_error_reason(response), payload=None
        )

    outcomes = await broadcast(servers, _ban_one)
    server_results: list[dict[str, Any]] = [
        {
            "server_id": int(o.server.id),
            "server_name": str(o.server.name),
            "success": o.ok,
            "reason": o.detail,
        }
        for o in outcomes
    ]

    logger.info(
        f"WebUI 封禁用户黑名单同步完成：user_id={_mask_qq(user_qq)} name={user_name} "
        f"server_count={len(servers)} client_ip={client_ip}"
    )

    session = get_session()
    try:
        refreshed_user = session.query(User).filter(User.id == user_id).first()
        user_data = _serialize_user(refreshed_user) if refreshed_user else {}
    finally:
        session.close()

    return api_success(data={"user": user_data, "server_results": server_results})


@router.post("/webui/api/users/{user_id}/unban")
async def webui_users_unban(
    request: Request,
    user_id: int = Path(..., ge=1),  # H-5
) -> JSONResponse:
    # M-1：补 request 参数；H-1：日志补 client_ip / user_agent
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    # H-3：user_name / user_qq 在 try 入口最早就读出，避免 except / finally 引用未绑定变量
    user_name = ""
    user_qq = ""

    session = get_session()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if user is None:
            return api_error(status_code=404, code="not_found", message="用户不存在")

        # H-4：Owner 在业务上不应处于"被封禁"状态，但仍补一道边界
        if str(user.user_id) in get_owner_ids():
            return api_error(
                status_code=403,
                code="owner_protected",
                message="不能对管理员执行此操作",
            )

        if not user.is_banned:
            return api_error(status_code=409, code="conflict", message="该用户未被封禁")

        # H-3：在 mutation / commit 之前先读出，except 分支也能安全引用
        user_name = str(user.name)
        user_qq = str(user.user_id)

        user.is_banned = False
        user.banned_at = None
        user.ban_reason = ""
        session.commit()

        logger.info(
            f"WebUI 解封用户成功：user_id={_mask_qq(user_qq)} name={user_name} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
    except Exception as exc:
        session.rollback()
        logger.exception(
            f"WebUI 解封用户异常：user_id={user_id}，reason={exc} "
            f"client_ip={client_ip}"
        )
        return api_error(status_code=500, code="internal_error", message="内部错误")
    finally:
        session.close()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    # R2-T-2：原串行 `for server in servers:` 改为 broadcast 并行，
    # 避免 N≥4 服务器场景下前端 15s timeout 必触发的回归。
    async def _unban_one(server: Server) -> BroadcastOutcome[str]:
        try:
            check_response = await request_server_api(server, "/nextbot/blacklist")
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(check_response):
            payload = check_response.payload if isinstance(check_response.payload, dict) else {}
            entries = payload.get("entries", [])
            exists = any(
                isinstance(e, dict)
                and str(e.get("username", "")).lower() == user_name.lower()
                for e in (entries if isinstance(entries, list) else [])
            )
            if not exists:
                return BroadcastOutcome(
                    server=server, ok=True, detail="不在黑名单中", payload=None
                )

        try:
            response = await request_server_api(
                server, f"/nextbot/blacklist/remove/{user_name}"
            )
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            return BroadcastOutcome(
                server=server, ok=True, detail="", payload=None
            )
        return BroadcastOutcome(
            server=server, ok=False, detail=get_error_reason(response), payload=None
        )

    outcomes = await broadcast(servers, _unban_one)
    server_results: list[dict[str, Any]] = [
        {
            "server_id": int(o.server.id),
            "server_name": str(o.server.name),
            "success": o.ok,
            "reason": o.detail,
        }
        for o in outcomes
    ]

    logger.info(
        f"WebUI 解封用户黑名单同步完成：user_id={_mask_qq(user_qq)} name={user_name} "
        f"server_count={len(servers)} client_ip={client_ip}"
    )

    session = get_session()
    try:
        refreshed_user = session.query(User).filter(User.id == user_id).first()
        user_data = _serialize_user(refreshed_user) if refreshed_user else {}
    finally:
        session.close()

    return api_success(data={"user": user_data, "server_results": server_results})
