import asyncio
import math

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.audit import audit_permission_change
from nextbot.ban_core import (
    apply_ban_to_db,
    apply_unban_to_db,
    format_blacklist_add_lines,
    format_blacklist_remove_lines,
    sync_user_blacklist_remove,
    sync_user_to_blacklist,
)
from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import User, get_session
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import EMOJI_USER, reply_failure, reply_success
from nextbot.time_utils import format_beijing_datetime
from server.screenshot import ScreenshotOptions
from server.web_server import create_ban_list_page

ban_matcher = on_command("封禁用户")
unban_matcher = on_command("解封用户")
ban_list_matcher = on_command("封禁列表")

BAN_LIST_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=800,
    full_page=True,
    fit_content_height=True,
)

# SB-2.2：封禁列表 handler-wide semaphore，防止 guest 高频刷命令导致 Playwright 进程膨胀
_ban_list_semaphore = asyncio.Semaphore(2)


@ban_matcher.handle()
@command_control(
    command_key="admin.ban",
    display_name="封禁用户",
    permission="admin.ban",
    description="封禁用户并将其加入所有服务器黑名单",
    usage="封禁用户 <用户 QQ/@用户/用户名称> <封禁原因>",
    category="安全管理",
)
@require_permission("admin.ban")
async def handle_ban(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    operator_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(operator_id))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "封禁用户", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("封禁", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("封禁", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error or target_user_id is None:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "封禁用户")
    if len(args) < 2:
        raise_command_usage()
    reason = " ".join(args[1:]).strip()
    if not reason:
        raise_command_usage()

    result = apply_ban_to_db(target_user_id, reason)
    if result.code == "not_found":
        await bot.send(event, at + " " + reply_failure("封禁", "未找到该用户"))
        return
    if result.code == "owner_protected":
        # SS-2.1：拒绝路径走 audit_permission_change，便于安全监测
        audit_permission_change(
            actor_user_id=operator_id,
            action="user.ban.denied",
            target=target_user_id,
            context={"reason": "owner_protected"},
        )
        await bot.send(event, at + " " + reply_failure("封禁", "不能封禁 Owner"))
        return
    if result.code == "already_banned":
        await bot.send(event, at + " " + reply_failure("封禁", f"该用户已被封禁，原因：{result.previous_reason}"))
        return

    # SB-1.5：审计日志补 operator_id，便于未来追溯
    logger.info(
        f"用户封禁成功：operator_id={operator_id} target_user_id={result.user_qq} "
        f"target_name={result.user_name} reason={reason}"
    )
    # SS-2.1：手动封禁走 audit_permission_change，与 group_member_notify 的
    # 自动封禁（user.ban.auto_on_leave）形成完整审计入口对偶
    audit_permission_change(
        actor_user_id=operator_id,
        action="user.ban",
        target=str(result.user_qq),
        before={"is_banned": False},
        after={"is_banned": True, "ban_reason": reason},
        context={"target_name": result.user_name},
    )

    outcomes = await sync_user_to_blacklist(result.user_name, reason)

    lines: list[str] = [
        reply_success("封禁"),
        f"{EMOJI_USER} 用户：{result.user_name}（{result.user_qq}）",
        f"📋 原因：{reason}",
    ]
    lines.extend(format_blacklist_add_lines(outcomes))

    success_count = sum(1 for o in outcomes if o.ok)
    logger.info(
        f"封禁用户黑名单同步完成：operator_id={operator_id} target_user_id={result.user_qq} "
        f"target_name={result.user_name} success={success_count}/{len(outcomes)}"
    )
    await bot.send(event, at + "\n" + "\n".join(lines))


@ban_list_matcher.handle()
@command_control(
    command_key="ban.list",
    display_name="封禁列表",
    permission="ban.list",
    description="查看封禁用户列表",
    usage="封禁列表 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页数量",
            "description": "每页显示的封禁用户数量",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
    category="安全管理",
)
@require_permission("ban.list")
async def handle_ban_list(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "封禁列表")
    if len(args) > 1:
        raise_command_usage()

    page = 1
    if args:
        try:
            page = int(args[0])
        except ValueError:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return
        if page <= 0:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    # SB-2.4：count + offset/limit，避免万级封禁数全表 ORM 物化
    session = get_session()
    try:
        total = (
            session.query(User).filter(User.is_banned == True).count()  # noqa: E712
        )
        total_pages = max(1, math.ceil(total / limit))

        if total > 0 and page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return

        offset = (page - 1) * limit
        if total == 0:
            page_users: list[User] = []
        else:
            page_users = (
                session.query(User)
                .filter(User.is_banned == True)  # noqa: E712
                .order_by(User.banned_at.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
        entries = [
            {
                "index": offset + i + 1,
                "name": str(u.name),
                "user_id": str(u.user_id),
                "ban_reason": str(u.ban_reason or ""),
                "banned_at": format_beijing_datetime(u.banned_at) if u.banned_at else "",
            }
            for i, u in enumerate(page_users)
        ]
    finally:
        session.close()

    page_url = create_ban_list_page(
        page=page if total > 0 else 1,
        total_pages=total_pages,
        entries=entries,
    )

    logger.info(
        f"封禁列表渲染地址：page={page}/{total_pages} total={total} internal_url={page_url}"
    )

    # SB-2.2：helper 内置 semaphore + base64 size 上限防 OOM + V11 / 非 V11 fallback
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=BAN_LIST_SCREENSHOT_OPTIONS,
        file_prefix="ban-list",
        semaphore=_ban_list_semaphore,
        failure_action="查询",
    )


@unban_matcher.handle()
@command_control(
    command_key="admin.unban",
    display_name="解封用户",
    permission="admin.unban",
    description="解除封禁用户并将其从所有服务器黑名单移除",
    usage="解封用户 <用户 QQ/@用户/用户名称>",
    category="安全管理",
)
@require_permission("admin.unban")
async def handle_unban(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    operator_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(operator_id))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "解封用户", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("解封", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("解封", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "解封用户")
    if len(args) != 1:
        raise_command_usage()

    # SC-4.6 / SB-3.1 / SB-3.3：解封路径走 ban_core，commit 前 capture name/qq + 条件 UPDATE
    assert target_user_id is not None  # parse_error 分支已处理 None
    result = apply_unban_to_db(target_user_id)
    if result.code == "not_found":
        await bot.send(event, at + " " + reply_failure("解封", "未找到该用户"))
        return
    if result.code == "not_banned":
        await bot.send(event, at + " " + reply_failure("解封", "该用户未被封禁"))
        return

    # SB-3.6：审计日志补 operator_id
    logger.info(
        f"用户解封成功：operator_id={operator_id} target_user_id={result.user_qq} "
        f"target_name={result.user_name}"
    )
    # SS-2.1：手动解封走 audit_permission_change
    audit_permission_change(
        actor_user_id=operator_id,
        action="user.unban",
        target=str(result.user_qq),
        before={"is_banned": True},
        after={"is_banned": False},
        context={"target_name": result.user_name},
    )

    outcomes = await sync_user_blacklist_remove(result.user_name)

    lines: list[str] = [
        reply_success("解封"),
        f"{EMOJI_USER} 用户：{result.user_name}（{result.user_qq}）",
    ]
    lines.extend(format_blacklist_remove_lines(outcomes))

    success_count = sum(1 for o in outcomes if o.ok)
    logger.info(
        f"解封用户黑名单同步完成：operator_id={operator_id} target_user_id={result.user_qq} "
        f"target_name={result.user_name} success={success_count}/{len(outcomes)}"
    )
    await bot.send(event, at + "\n" + "\n".join(lines))
