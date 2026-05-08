from __future__ import annotations

from urllib.parse import quote

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, User, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.server_broadcast import BroadcastOutcome, aggregate, broadcast
from nextbot.text_utils import reply_block, reply_failure, reply_success
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)

confirm_login_matcher = on_command("允许登入")
reject_login_matcher = on_command("拒绝登入")

_NO_PENDING_MARK = "No pending login request"


def _load_self_and_servers(user_id: str) -> tuple[User | None, list[Server]]:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        servers = session.query(Server).order_by(Server.id.asc()).all()
        return user, servers
    finally:
        session.close()


async def _broadcast_login_action(
    servers: list[Server], user_name: str, path_template: str
) -> list[BroadcastOutcome[str]]:
    """SA-1.1 / SA-2.1：fan-out 改并行（asyncio.gather + per-server semaphore）。

    payload 字段：成功时为 server 返回的 response 文本（可空）；失败时为 None。
    """
    encoded_name = quote(user_name, safe="")
    path = path_template.format(user=encoded_name)

    async def _one(server: Server) -> BroadcastOutcome[str]:
        try:
            response = await request_server_api(server, path)
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            success_text = str(response.payload.get("response") or "").strip()
            return BroadcastOutcome(
                server=server, ok=True, detail=success_text, payload=success_text
            )

        error_text = str(response.payload.get("error") or "").strip()
        if not error_text:
            error_text = get_error_reason(response)
        return BroadcastOutcome(
            server=server, ok=False, detail=error_text, payload=None
        )

    return await broadcast(servers, _one)


def _format_failure_lines(outcomes: list[BroadcastOutcome[str]]) -> list[str]:
    """SA-1.2：把每台服务器的失败原因逐行列出，让用户能区分超时 / 宕机 / 未审核。"""
    return [
        f"{o.server.id}.{o.server.name}：❌ {o.detail}"
        for o in outcomes
        if not o.ok
    ]


def _all_no_pending(outcomes: list[BroadcastOutcome[str]]) -> bool:
    """所有失败原因都是 "No pending login request" 时，等价于 "没有待处理的登入请求"。"""
    failures = [o for o in outcomes if not o.ok]
    if not failures:
        return False
    return all(_NO_PENDING_MARK in o.detail for o in failures)


def _log_results(
    action: str,
    operator_id: str,
    target_user_id: str,
    user_name: str,
    success_count: int,
    outcomes: list[BroadcastOutcome[str]],
) -> None:
    """SA-1.3 / SA-2.4：审计日志同时记录 operator_id 与 target_user_id，便于未来命令扩展。"""
    logger.info(
        f"{action}处理完成：operator_id={operator_id} target_user_id={target_user_id} "
        f"target_name={user_name} success={success_count}/{len(outcomes)}"
    )
    for o in outcomes:
        logger.info(
            f"{action}服务器结果：server_id={o.server.id} name={o.server.name} "
            f"ok={o.ok} reason={o.detail}"
        )


async def _handle_login_action(
    bot: Bot,
    event: Event,
    arg: Message,
    *,
    command_name: str,
    action: str,
    path_template: str,
    success_detail: str | None,
) -> None:
    args = parse_command_args_with_fallback(event, arg, command_name)
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))
    user, servers = _load_self_and_servers(user_id)
    if user is None:
        await bot.send(event, at + " " + reply_failure(action, "未注册账号"))
        return
    if not servers:
        await bot.send(event, at + " " + reply_failure(action, "暂无服务器"))
        return

    outcomes = await _broadcast_login_action(servers, user.name, path_template)
    success_count, total = aggregate(outcomes)
    _log_results(command_name, user_id, user_id, user.name, success_count, outcomes)

    # SA-1.7：完全成功 / 部分成功 / 全失败 三态区分
    if success_count == total:
        await bot.send(event, at + " " + reply_success(action, success_detail))
        return

    if success_count > 0:
        # 部分成功：⚠️ 头 + 失败明细，让用户知道哪几台 OK 哪几台未 OK
        head = f"⚠️ {action}部分成功（{success_count}/{total}）"
        failure_lines = _format_failure_lines(outcomes)
        body = reply_block(head, failure_lines)
        await bot.send(event, at + "\n" + body)
        return

    # 全失败
    if _all_no_pending(outcomes):
        await bot.send(
            event,
            at + " " + reply_failure(action, "没有待处理的登入请求"),
        )
        return

    failure_lines = _format_failure_lines(outcomes)
    if len(failure_lines) == 1:
        # 单台服务器场景，沿用原 reply_failure 单行格式
        only_reason = next(o.detail for o in outcomes if not o.ok)
        await bot.send(event, at + " " + reply_failure(action, only_reason))
        return

    head = reply_failure(action, f"全部 {total} 台服务器失败")
    body = reply_block(head, failure_lines)
    await bot.send(event, at + "\n" + body)


@confirm_login_matcher.handle()
@command_control(
    command_key="security.login.confirm",
    display_name="允许登入",
    permission="security.login.confirm",
    description="允许当前账号的待确认登入请求",
    usage="允许登入",
    category="安全管理",
)
@require_permission("security.login.confirm")
async def handle_confirm_login(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    await _handle_login_action(
        bot,
        event,
        arg,
        command_name="允许登入",
        action="允许",
        path_template="/nextbot/security/confirm-login/{user}",
        success_detail="可在 5 分钟内重新连接",
    )


@reject_login_matcher.handle()
@command_control(
    command_key="security.login.reject",
    display_name="拒绝登入",
    permission="security.login.reject",
    description="拒绝当前账号的待确认登入请求",
    usage="拒绝登入",
    category="安全管理",
)
@require_permission("security.login.reject")
async def handle_reject_login(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    await _handle_login_action(
        bot,
        event,
        arg,
        command_name="拒绝登入",
        action="拒绝",
        path_template="/nextbot/security/reject-login/{user}",
        success_detail=None,
    )
