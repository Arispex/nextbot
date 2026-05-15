from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import nonebot
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Bot as OBV11Bot
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from sqlalchemy import func

from nextbot.access_control import get_group_ids
from nextbot.db import User, get_session
from nextbot.text_utils import safe_at_segment_or_empty
from server.routes import api_error, api_success, read_json_object

router = APIRouter()


# H-1：per-target 滑动窗口节流（按 name.lower() 维度），与 webui.py 同款实现。
_LOGIN_REQUEST_WINDOW_SEC = 300
_LOGIN_REQUEST_MAX_PER_WINDOW = 1
_login_request_lock = threading.Lock()
_login_request_history: dict[str, deque[float]] = {}

# M-2 / M-3：限制日志注入和资源消耗的输入长度。
_NAME_MAX_LENGTH = 64


def _client_ip(request: Request) -> str:
    """H-3：从 X-Forwarded-For 或 client.host 取调用方 IP。

    与 webui.py / servers 模块同实现。
    """
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _user_agent(request: Request) -> str:
    """H-3：截断 User-Agent 防超长（与 servers 模块同实现）。"""
    return request.headers.get("user-agent", "")[:200]


def _check_login_request_rate_limit(key: str) -> tuple[bool, int]:
    """H-1：检查 per-target 节流，返回 (allowed, retry_after_seconds)。"""
    now = time.monotonic()
    with _login_request_lock:
        history = _login_request_history.get(key)
        if history is None:
            return True, 0
        while history and now - history[0] > _LOGIN_REQUEST_WINDOW_SEC:
            history.popleft()
        if not history:
            _login_request_history.pop(key, None)
            return True, 0
        if len(history) >= _LOGIN_REQUEST_MAX_PER_WINDOW:
            retry_after = int(_LOGIN_REQUEST_WINDOW_SEC - (now - history[0])) + 1
            return False, max(retry_after, 1)
        return True, 0


def _record_login_request(key: str) -> None:
    """H-1：成功推送后记录一次时间戳到滑动窗口。"""
    now = time.monotonic()
    with _login_request_lock:
        history = _login_request_history.setdefault(key, deque())
        history.append(now)


def _pick_onebot_bot() -> OBV11Bot | None:
    bots = get_bots().values()
    onebot_bots = [bot for bot in bots if isinstance(bot, OBV11Bot)]
    if not onebot_bots:
        return None
    # L-1：多 bot 并存时记录被选 bot，便于排查推送到错误账号的问题
    # （多 bot 选择策略 scope-out backlog）。
    if len(onebot_bots) > 1:
        logger.warning(
            f"挑选 OneBot 实例时存在多个候选 bot 数量={len(onebot_bots)} "
            f"selected_self_id={onebot_bots[0].self_id}"
        )
    return onebot_bots[0]


def _resolve_user_id_by_name(name: str) -> str | None:
    # H-4：name 大小写不敏感匹配，多个同名用户时取最早注册的；记录 WARN 便于
    # 后续接入 citext / 表达式索引的迁移（scope-out backlog）。
    session = get_session()
    try:
        users = (
            session.query(User)
            .filter(func.lower(User.name) == name.lower())
            .order_by(User.id.asc())
            .all()
        )
        if not users:
            return None
        if len(users) > 1:
            logger.warning(
                f"按 name 解析 user_id 时发现多个匹配 name={name!r} "
                f"matched={len(users)} selected_user_id={users[0].user_id}"
            )
        return str(users[0].user_id)
    finally:
        session.close()


async def _find_user_group(bot: OBV11Bot, user_id: str) -> int | None:
    allowed_groups = get_group_ids()
    if not allowed_groups:
        return None

    for raw_gid in allowed_groups:
        try:
            group_id = int(raw_gid)
        except (TypeError, ValueError):
            continue
        try:
            await bot.call_api(
                "get_group_member_info",
                group_id=group_id,
                user_id=int(user_id),
                no_cache=False,
            )
        except Exception as exc:
            # L-2：探测失败原因走 debug，可排查"为什么没收到登入消息"
            logger.debug(
                f"探测用户所在群失败 user_id={user_id} group_id={group_id} reason={exc}"
            )
            continue
        return group_id
    return None


async def _find_all_user_groups(bot: OBV11Bot, user_id: str) -> list[int]:
    allowed_groups = get_group_ids()
    if not allowed_groups:
        return []

    found: list[int] = []
    for raw_gid in allowed_groups:
        try:
            group_id = int(raw_gid)
        except (TypeError, ValueError):
            continue
        try:
            await bot.call_api(
                "get_group_member_info",
                group_id=group_id,
                user_id=int(user_id),
                no_cache=False,
            )
        except Exception as exc:
            # L-2：同 _find_user_group，记录探测失败原因方便排查
            logger.debug(
                f"探测用户所在群失败 user_id={user_id} group_id={group_id} reason={exc}"
            )
            continue
        found.append(group_id)
    return found


@router.post("/webui/api/login-requests")
async def webui_login_requests_create(request: Request) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    # M-1：顶层 try/except 防御未捕获异常导致 500 泄漏内部信息。
    try:
        name = str(data.get("name") or "").strip()
        if not name:
            return api_error(
                status_code=422,
                code="validation_error",
                message="用户名称不能为空",
                details=[{"field": "name", "message": "用户名称不能为空"}],
            )

        # M-3：限制输入长度，防止日志膨胀 / 模板拼装资源消耗。
        if len(name) > _NAME_MAX_LENGTH:
            return api_error(
                status_code=422,
                code="validation_error",
                message=f"用户名称长度不能超过 {_NAME_MAX_LENGTH}",
                details=[
                    {
                        "field": "name",
                        "message": f"用户名称长度不能超过 {_NAME_MAX_LENGTH}",
                    }
                ],
            )

        new_device = bool(data.get("newDevice", False))
        new_location = bool(data.get("newLocation", False))

        # H-1：per-target 节流，按 name.lower() 维度，命中返回 429（仅原因）。
        rate_key = name.lower()
        allowed, retry_after = _check_login_request_rate_limit(rate_key)
        if not allowed:
            logger.warning(
                f"发送登入确认失败：name={name!r} reason=触发节流 "
                f"retry_after={retry_after} client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            return api_error(
                status_code=429,
                code="too_many_requests",
                message="该用户最近已发送过登入确认，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

        user_id = _resolve_user_id_by_name(name)
        if user_id is None:
            logger.warning(
                f"发送登入确认失败：name={name!r} reason=用户不存在 "
                f"client_ip={client_ip} user_agent={user_agent!r}"
            )
            return api_error(
                status_code=404,
                code="not_found",
                message="用户不存在",
            )

        bot = _pick_onebot_bot()
        if bot is None:
            logger.warning(
                f"发送登入确认失败：name={name!r} user_id={user_id} "
                f"reason=机器人未连接 client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            return api_error(
                status_code=503,
                code="bot_unavailable",
                message="机器人未连接",
            )

        config = nonebot.get_driver().config
        notify_all = bool(getattr(config, "login_notify_all_groups", False))

        if notify_all:
            group_ids = await _find_all_user_groups(bot, user_id)
        else:
            first = await _find_user_group(bot, user_id)
            group_ids = [first] if first is not None else []

        if not group_ids:
            # M-8：区分"未配置授权群"与"用户不在任何群"
            if not get_group_ids():
                logger.warning(
                    f"发送登入确认失败：name={name!r} user_id={user_id} "
                    f"reason=未配置授权群 client_ip={client_ip} "
                    f"user_agent={user_agent!r}"
                )
                return api_error(
                    status_code=503,
                    code="service_misconfigured",
                    message="未配置授权群",
                )

            logger.warning(
                f"发送登入确认失败：name={name!r} user_id={user_id} "
                f"reason=未在任何群中找到该用户 client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            return api_error(
                status_code=404,
                code="group_not_found",
                message="未在任何群中找到该用户",
            )

        if new_device and new_location:
            change_text = "有新设备在新地点正在尝试登入服务器"
        elif new_device:
            change_text = "有新设备正在尝试登入服务器"
        elif new_location:
            change_text = "在新地点正在尝试登入服务器"
        else:
            change_text = "有新设备或者在新地点正在尝试登入服务器"

        # M-1：safe_at_segment_or_empty 防御非数字 user_id（适配器扩展场景）。
        text_body = (
            f"\n{change_text}\n请回复「允许登入」或「拒绝登入」\n"
            f"该请求 5 分钟内有效"
        )
        message: list[Any] = [
            safe_at_segment_or_empty(user_id),
            OBV11MessageSegment.text(text_body),
        ]

        results: list[dict[str, Any]] = []
        for gid in group_ids:
            try:
                send_result = await bot.call_api(
                    "send_group_msg",
                    group_id=gid,
                    message=message,
                )
            except Exception as exc:
                logger.exception(
                    f"发送登入确认异常：name={name!r} user_id={user_id} "
                    f"group_id={gid} reason={exc} client_ip={client_ip} "
                    f"user_agent={user_agent!r}"
                )
                # M-6：失败 result 带上原始 reason，便于多群推送的可观测性
                results.append(
                    {"group_id": gid, "message_id": None, "reason": str(exc)}
                )
                continue

            msg_id: int | None = None
            if isinstance(send_result, dict):
                raw_msg_id = send_result.get("message_id")
                if isinstance(raw_msg_id, int):
                    msg_id = raw_msg_id

            logger.info(
                f"发送登入确认成功：name={name!r} user_id={user_id} "
                f"group_id={gid} message_id={msg_id} client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            results.append({"group_id": gid, "message_id": msg_id, "reason": None})

        if not any(r["message_id"] is not None for r in results):
            # M-10：error.message 仅返回原因；details 透传每个 group 的原始错误
            return api_error(
                status_code=502,
                code="send_failed",
                message="全部目标群推送均失败",
                details=[
                    {"group_id": r["group_id"], "reason": r["reason"]}
                    for r in results
                ],
            )

        # H-1：至少 1 群成功后才记录节流时间戳。
        _record_login_request(rate_key)

        # M-11：统一 results 数组形式，调用方不再走两套解码
        return api_success(
            status_code=201,
            data={
                "name": name,
                "user_id": user_id,
                "results": results,
            },
        )
    except Exception:
        logger.exception(
            f"处理登入确认请求异常 client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
