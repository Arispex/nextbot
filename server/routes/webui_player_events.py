from __future__ import annotations

import nonebot
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Bot as OBV11Bot
from nonebot.log import logger
from sqlalchemy import func

from nextbot.access_control import get_group_ids
from nextbot.db import User, get_session
from server.routes import (
    api_error,
    api_success,
    client_ip as _shared_client_ip,
    read_json_object,
    user_agent as _shared_user_agent,
)

router = APIRouter()

_ALLOWED_EVENTS = {"online", "offline", "message"}

# M-3：输入长度上限，防止资源消耗 / OneBot 上游异常 / 风控。
_PLAYER_NAME_MAX_LENGTH = 64
_SERVER_NAME_MAX_LENGTH = 64
_MESSAGE_MAX_LENGTH = 500
# H-2：拒绝控制字符（保留 \n \t），防止日志注入与"公告样式"伪造。
_FORBIDDEN_CONTROL_CHARS = "".join(
    chr(i) for i in range(32) if i not in (9, 10)
)
_MAX_NEWLINES = 5


# CRIT-1 / HIGH-2：thin re-export aliases；canonical helper 在 server/routes/__init__.py。
_client_ip = _shared_client_ip
_user_agent = _shared_user_agent


def _pick_onebot_bot() -> OBV11Bot | None:
    bots = get_bots().values()
    onebot_bots = [bot for bot in bots if isinstance(bot, OBV11Bot)]
    if not onebot_bots:
        return None
    # L-1：多 bot 并存记录被选 bot，便于排查推送目标错误。
    if len(onebot_bots) > 1:
        logger.warning(
            f"挑选 OneBot 实例时存在多个候选 bot 数量={len(onebot_bots)} "
            f"selected_self_id={onebot_bots[0].self_id}"
        )
    return onebot_bots[0]


def _resolve_user_id_by_name(name: str) -> str | None:
    session = get_session()
    try:
        user = (
            session.query(User)
            .filter(func.lower(User.name) == name.lower())
            .order_by(User.id.asc())
            .first()
        )
        return str(user.user_id) if user is not None else None
    finally:
        session.close()


def _resolve_target_groups_by_mode(mode: str, single_gid: str) -> list[int]:
    mode_norm = mode.strip().lower()
    if mode_norm == "single":
        if single_gid.isdigit():
            return [int(single_gid)]
        return []

    target: list[int] = []
    for raw_gid in get_group_ids():
        text = str(raw_gid).strip()
        if text.isdigit():
            target.append(int(text))
    return target


def _resolve_target_groups() -> list[int]:
    config = nonebot.get_driver().config
    mode = str(getattr(config, "player_notify_mode", "all") or "")
    single_gid = str(getattr(config, "player_notify_group_id", "") or "").strip()
    return _resolve_target_groups_by_mode(mode, single_gid)


def _resolve_chat_target_groups() -> list[int]:
    config = nonebot.get_driver().config
    mode = str(getattr(config, "chat_sync_mode", "all") or "")
    single_gid = str(getattr(config, "chat_sync_group_id", "") or "").strip()
    return _resolve_target_groups_by_mode(mode, single_gid)


def _contains_forbidden_chars(text: str) -> bool:
    """H-2：检测控制字符（保留 \\t \\n），防止日志注入 / 终端转义。"""
    return any(ch in _FORBIDDEN_CONTROL_CHARS for ch in text)


def _too_many_newlines(text: str) -> bool:
    """H-2：换行符过多视为"公告样式"伪造尝试。"""
    return text.count("\n") > _MAX_NEWLINES


def _length_validation_error(field: str, max_length: int) -> JSONResponse:
    """M-3：长度超限 422 错误模板，错误文案仅保留原因。"""
    message = f"长度不能超过 {max_length}"
    return api_error(
        status_code=422,
        code="validation_error",
        message=message,
        details=[{"field": field, "message": message}],
    )


def _render_template(
    template: str,
    *,
    display_name: str,
    server_name: str,
    message_text: str,
) -> str:
    """M-4：模板字段一次性替换，防止 message_text / player_name 含 {server} /
    {message} 被二次 substitution 绕过契约。预先将用户输入中的 { } 转成全角
    ｛｝ 再做替换，等价"占位符仅来自模板"。
    """

    def _strip_braces(value: str) -> str:
        return value.replace("{", "｛").replace("}", "｝")

    safe_player = _strip_braces(display_name)
    safe_server = _strip_braces(server_name)
    safe_message = _strip_braces(message_text)

    return (
        template.replace("{player}", safe_player)
        .replace("{server}", safe_server)
        .replace("{message}", safe_message)
    )


@router.post("/webui/api/player-events")
async def webui_player_events_create(request: Request) -> JSONResponse:
    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    # M-1：顶层 try/except 防止异常路径泄漏 stack trace。
    try:
        player_name = str(data.get("player_name") or "").strip()
        if not player_name:
            return api_error(
                status_code=422,
                code="validation_error",
                message="玩家名称不能为空",
                details=[{"field": "player_name", "message": "玩家名称不能为空"}],
            )
        if len(player_name) > _PLAYER_NAME_MAX_LENGTH:
            return _length_validation_error(
                "player_name", _PLAYER_NAME_MAX_LENGTH
            )
        if _contains_forbidden_chars(player_name):
            message = "玩家名称包含非法字符"
            return api_error(
                status_code=422,
                code="validation_error",
                message=message,
                details=[{"field": "player_name", "message": message}],
            )

        server_name = str(data.get("server_name") or "").strip()
        if not server_name:
            return api_error(
                status_code=422,
                code="validation_error",
                message="服务器名称不能为空",
                details=[{"field": "server_name", "message": "服务器名称不能为空"}],
            )
        if len(server_name) > _SERVER_NAME_MAX_LENGTH:
            return _length_validation_error(
                "server_name", _SERVER_NAME_MAX_LENGTH
            )
        if _contains_forbidden_chars(server_name):
            message = "服务器名称包含非法字符"
            return api_error(
                status_code=422,
                code="validation_error",
                message=message,
                details=[{"field": "server_name", "message": message}],
            )

        event = str(data.get("event") or "").strip().lower()
        if event not in _ALLOWED_EVENTS:
            return api_error(
                status_code=422,
                code="validation_error",
                message="事件类型仅支持 online、offline 或 message",
                details=[
                    {
                        "field": "event",
                        "message": "事件类型仅支持 online、offline 或 message",
                    }
                ],
            )

        message_text = ""
        if event == "message":
            message_text = str(data.get("message") or "").strip()
            if not message_text:
                return api_error(
                    status_code=422,
                    code="validation_error",
                    message="消息内容不能为空",
                    details=[{"field": "message", "message": "消息内容不能为空"}],
                )
            if len(message_text) > _MESSAGE_MAX_LENGTH:
                return _length_validation_error("message", _MESSAGE_MAX_LENGTH)
            if _contains_forbidden_chars(message_text):
                msg = "消息内容包含非法字符"
                return api_error(
                    status_code=422,
                    code="validation_error",
                    message=msg,
                    details=[{"field": "message", "message": msg}],
                )
            if _too_many_newlines(message_text):
                msg = f"消息内容换行过多，最多允许 {_MAX_NEWLINES} 行"
                return api_error(
                    status_code=422,
                    code="validation_error",
                    message=msg,
                    details=[{"field": "message", "message": msg}],
                )

        bot = _pick_onebot_bot()
        if bot is None:
            logger.warning(
                f"推送玩家事件失败：player_name={player_name!r} "
                f"server_name={server_name!r} event={event} "
                f"reason=机器人未连接 client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            return api_error(
                status_code=503,
                code="bot_unavailable",
                message="机器人未连接",
            )

        if event == "message":
            target_groups = _resolve_chat_target_groups()
        else:
            target_groups = _resolve_target_groups()
        if not target_groups:
            # M-9：配置层错误改 503，区分"配置缺失"与"资源不存在"。
            logger.warning(
                f"推送玩家事件失败：player_name={player_name!r} "
                f"server_name={server_name!r} event={event} "
                f"reason=未配置有效通知群 client_ip={client_ip} "
                f"user_agent={user_agent!r}"
            )
            return api_error(
                status_code=503,
                code="service_misconfigured",
                message="未配置有效的通知群",
            )

        bound_user_id = _resolve_user_id_by_name(player_name)
        display_name = (
            f"{player_name}（{bound_user_id}）" if bound_user_id else player_name
        )

        config = nonebot.get_driver().config
        if event == "online":
            template = (
                str(getattr(config, "player_notify_online_template", "") or "").strip()
                or "[{server}]{player} 上线了"
            )
        elif event == "offline":
            template = (
                str(getattr(config, "player_notify_offline_template", "") or "").strip()
                or "[{server}]{player} 下线了"
            )
        else:
            template = (
                str(getattr(config, "chat_sync_template", "") or "").strip()
                or "[{server}]{player}：{message}"
            )
        # M-4：一次性 substitution，预先转义用户输入中的 { } 防止二次替换
        text = _render_template(
            template,
            display_name=display_name,
            server_name=server_name,
            message_text=message_text,
        )

        # M-5：每个 group 的失败原因独立返回，结构对齐 login-requests results。
        results: list[dict[str, object]] = []
        sent_groups: list[int] = []
        failed_groups: list[dict[str, object]] = []
        for gid in target_groups:
            try:
                send_result = await bot.call_api(
                    "send_group_msg", group_id=gid, message=text
                )
            except Exception as exc:
                logger.warning(
                    f"推送玩家事件到群失败：group_id={gid} "
                    f"player_name={player_name!r} event={event} reason={exc} "
                    f"client_ip={client_ip} user_agent={user_agent!r}"
                )
                failed_groups.append({"group_id": gid, "reason": str(exc)})
                results.append(
                    {"group_id": gid, "message_id": None, "reason": str(exc)}
                )
                continue

            # M-5：成功时也提取 message_id，对齐 login-requests
            msg_id: int | None = None
            if isinstance(send_result, dict):
                raw_msg_id = send_result.get("message_id")
                if isinstance(raw_msg_id, int):
                    msg_id = raw_msg_id

            sent_groups.append(gid)
            results.append({"group_id": gid, "message_id": msg_id, "reason": None})

        logger.info(
            f"推送玩家事件完成：player_name={player_name!r} "
            f"server_name={server_name!r} event={event} "
            f"sent={sent_groups} failed={[r['group_id'] for r in failed_groups]} "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )

        # L-6：增加 summary 元数据，便于调用方做监控埋点
        summary = {
            "total": len(target_groups),
            "success": len(sent_groups),
            "failed": len(failed_groups),
        }

        return api_success(
            data={
                "sent_groups": sent_groups,
                "failed_groups": failed_groups,
                "results": results,
                "summary": summary,
            }
        )
    except Exception:
        logger.exception(
            f"处理玩家事件请求异常 client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=500,
            code="internal_error",
            message="内部错误",
        )
