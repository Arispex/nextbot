import base64
import math
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.access_control import get_owner_ids
from nextbot.ban_core import apply_ban_to_db, sync_user_to_blacklist
from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import Server, User, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.time_utils import beijing_filename_timestamp, db_now_utc_naive
from nextbot.time_utils import format_beijing_datetime
from nextbot.tshock_api import TShockRequestError, get_error_reason, is_success, request_server_api
from nextbot.text_utils import EMOJI_USER, reply_failure, reply_success
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
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


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


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
    at = OBV11MessageSegment.at(int(event.get_user_id()))

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
        await bot.send(event, at + " " + reply_failure("封禁", "不能封禁 Owner"))
        return
    if result.code == "already_banned":
        await bot.send(event, at + " " + reply_failure("封禁", f"该用户已被封禁，原因：{result.previous_reason}"))
        return

    logger.info(
        f"用户封禁成功：user_id={result.user_qq} name={result.user_name} reason={reason}"
    )

    lines: list[str] = [
        reply_success("封禁"),
        f"{EMOJI_USER} 用户：{result.user_name}（{result.user_qq}）",
        f"📋 原因：{reason}",
    ]
    lines.extend(await sync_user_to_blacklist(result.user_name, reason))

    logger.info(
        f"封禁用户黑名单同步完成：user_id={result.user_qq} name={result.user_name}"
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

    session = get_session()
    try:
        banned_users = (
            session.query(User)
            .filter(User.is_banned == True)
            .order_by(User.banned_at.asc())
            .all()
        )
    finally:
        session.close()

    total = len(banned_users)
    total_pages = max(1, math.ceil(total / limit))

    if total == 0:
        page_url = create_ban_list_page(
            page=1, total_pages=1, entries=[],
        )
    else:
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return

        offset = (page - 1) * limit
        page_users = banned_users[offset : offset + limit]
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
        page_url = create_ban_list_page(
            page=page, total_pages=total_pages, entries=entries,
        )

    logger.info(
        f"封禁列表渲染地址：page={page}/{total_pages} total={total} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"ban-list-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=BAN_LIST_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", f"{exc}"))
        return

    logger.info(f"封禁列表截图成功：page={page}/{total_pages} file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


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
    at = OBV11MessageSegment.at(int(event.get_user_id()))

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

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("解封", "未找到该用户"))
            return

        if not user.is_banned:
            await bot.send(event, at + " " + reply_failure("解封", "该用户未被封禁"))
            return

        user.is_banned = False
        user.banned_at = None
        user.ban_reason = ""
        session.commit()

        user_name = user.name
        user_qq = user.user_id
    finally:
        session.close()

    logger.info(f"用户解封成功：user_id={user_qq} name={user_name}")

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    lines: list[str] = [
        reply_success("解封"),
        f"{EMOJI_USER} 用户：{user_name}（{user_qq}）",
    ]

    if not servers:
        lines.append("🖥️ 同步服务器黑名单结果：ℹ️ 暂无服务器")
    else:
        lines.append("🖥️ 同步服务器黑名单结果：")
        for server in servers:
            try:
                check_response = await request_server_api(
                    server,
                    "/nextbot/blacklist",
                )
            except TShockRequestError:
                lines.append(f"{server.id}.{server.name}：❌ 移除失败，无法连接服务器")
                continue

            if is_success(check_response):
                entries = check_response.payload.get("entries", [])
                exists = any(
                    str(e.get("username", "")).lower() == user_name.lower()
                    for e in entries
                    if isinstance(e, dict)
                )
                if not exists:
                    lines.append(f"{server.id}.{server.name}：ℹ️ 不在黑名单中")
                    continue

            try:
                response = await request_server_api(
                    server,
                    f"/nextbot/blacklist/remove/{user_name}",
                )
            except TShockRequestError:
                lines.append(f"{server.id}.{server.name}：❌ 移除失败，无法连接服务器")
                continue

            if is_success(response):
                lines.append(f"{server.id}.{server.name}：✅ 移除成功")
            else:
                error_msg = get_error_reason(response)
                lines.append(f"{server.id}.{server.name}：❌ 移除失败，{error_msg}")

    logger.info(
        f"解封用户黑名单同步完成：user_id={user_qq} name={user_name} server_count={len(servers)}"
    )
    await bot.send(event, at + "\n" + "\n".join(lines))
