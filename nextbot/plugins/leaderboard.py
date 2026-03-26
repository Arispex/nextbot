from __future__ import annotations

import base64
import math
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import Server, User, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.permissions import require_permission
from nextbot.render_utils import resolve_render_theme
from nextbot.time_utils import beijing_filename_timestamp
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_leaderboard_page

coins_leaderboard_matcher = on_command("金币排行榜")
streak_leaderboard_matcher = on_command("连续签到排行榜")
signin_leaderboard_matcher = on_command("签到排行榜")
deaths_leaderboard_matcher = on_command("死亡排行榜")
fishing_leaderboard_matcher = on_command("渔夫任务排行榜")
online_time_leaderboard_matcher = on_command("在线时长排行榜")

LEADERBOARD_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=900,
    viewport_height=800,
    full_page=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"



def _format_online_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m} 分 {s} 秒" if s else f"{m} 分钟"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = [f"{h} 小时"]
    if m:
        parts.append(f"{m} 分")
    if s:
        parts.append(f"{s} 秒")
    return " ".join(parts)


def _parse_page_arg(args: list[str], command_name: str) -> int | None:
    """解析可选页数参数，返回 None 表示参数无效（已发送错误提示由调用方处理）。"""
    if not args:
        return 1
    try:
        page = int(args[0])
    except ValueError:
        return None
    if page <= 0:
        return None
    return page


async def _render_and_send(
    bot: Bot,
    event: Event,
    *,
    title: str,
    value_label: str,
    page: int,
    limit: int,
    entries: list[dict],
    total_pages: int,
    file_prefix: str,
    self_entry: dict | None = None,
    theme: str,
) -> None:
    page_url = create_leaderboard_page(
        title=title,
        value_label=value_label,
        page=page,
        total_pages=total_pages,
        entries=entries,
        self_entry=self_entry,
        theme=theme,
    )
    logger.info(
        f"{title}渲染地址：page={page}/{total_pages} entry_count={len(entries)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"{file_prefix}-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=LEADERBOARD_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc:
        await bot.send(event, f"查询失败，{exc}")
        return

    logger.info(f"{title}截图成功：page={page}/{total_pages} file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, "查询失败，读取截图文件失败")
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"截图成功，文件：{screenshot_path}")


@coins_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.coins",
    display_name="金币排行榜",
    permission="leaderboard.coins",
    description="查看金币数量排行榜",
    usage="金币排行榜 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.coins")
async def handle_coins_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "金币排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "金币排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
            return
        offset = (page - 1) * limit
        users = (
            session.query(User)
            .order_by(User.coins.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.coins or 0)}
            for i, u in enumerate(users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None:
            caller_coins = int(caller.coins or 0)
            caller_rank = session.query(User).filter(User.coins > caller_coins).count() + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_coins}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="金币排行榜",
        value_label="金币",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-coins",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )


@streak_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.streak",
    display_name="连续签到排行榜",
    permission="leaderboard.streak",
    description="查看连续签到天数排行榜",
    usage="连续签到排行榜 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.streak")
async def handle_streak_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "连续签到排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "连续签到排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
            return
        offset = (page - 1) * limit
        users = (
            session.query(User)
            .order_by(User.sign_streak.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.sign_streak or 0)}
            for i, u in enumerate(users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None:
            caller_streak = int(caller.sign_streak or 0)
            caller_rank = session.query(User).filter(User.sign_streak > caller_streak).count() + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_streak}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="连续签到排行榜",
        value_label="天",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-streak",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )


@signin_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.signin",
    display_name="签到排行榜",
    permission="leaderboard.signin",
    description="查看累计签到次数排行榜",
    usage="签到排行榜 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.signin")
async def handle_signin_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "签到排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "签到排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
            return
        offset = (page - 1) * limit
        users = (
            session.query(User)
            .order_by(User.sign_total.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.sign_total or 0)}
            for i, u in enumerate(users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None:
            caller_total = int(caller.sign_total or 0)
            caller_rank = session.query(User).filter(User.sign_total > caller_total).count() + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_total}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="签到排行榜",
        value_label="次",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-signin",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )


@deaths_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.deaths",
    display_name="死亡排行榜",
    permission="leaderboard.deaths",
    description="查看指定服务器的玩家死亡次数排行榜",
    usage="死亡排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.deaths")
async def handle_deaths_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "死亡排行榜")
    if len(args) < 1 or len(args) > 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    page = _parse_page_arg(args[1:], "死亡排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        caller_id = event.get_user_id()
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_name = caller.name if caller is not None else None
    finally:
        session.close()

    if server is None:
        await bot.send(event, "查询失败，服务器不存在")
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/deaths")
    except TShockRequestError:
        await bot.send(event, "查询失败，无法连接服务器")
        return

    if not is_success(response):
        await bot.send(event, f"查询失败，{get_error_reason(response)}")
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, "查询失败，返回数据格式错误")
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("deaths"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": e["username"], "value": int(e["deaths"])}
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {"rank": idx + 1, "name": caller_name, "value": int(e["deaths"])}
                break

    logger.info(
        f"死亡排行榜查询成功：server_id={server_id} total={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title="死亡排行榜",
        value_label="次",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-deaths",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )


@fishing_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.fishing",
    display_name="渔夫任务排行榜",
    permission="leaderboard.fishing",
    description="查看指定服务器的渔夫任务完成数排行榜",
    usage="渔夫任务排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.fishing")
async def handle_fishing_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "渔夫任务排行榜")
    if len(args) < 1 or len(args) > 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    page = _parse_page_arg(args[1:], "渔夫任务排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        caller_id = event.get_user_id()
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_name = caller.name if caller is not None else None
    finally:
        session.close()

    if server is None:
        await bot.send(event, "查询失败，服务器不存在")
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/fishing-quests")
    except TShockRequestError:
        await bot.send(event, "查询失败，无法连接服务器")
        return

    if not is_success(response):
        await bot.send(event, f"查询失败，{get_error_reason(response)}")
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, "查询失败，返回数据格式错误")
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("questsCompleted"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": e["username"], "value": int(e["questsCompleted"])}
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {"rank": idx + 1, "name": caller_name, "value": int(e["questsCompleted"])}
                break

    logger.info(
        f"渔夫任务排行榜查询成功：server_id={server_id} total={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title="渔夫任务排行榜",
        value_label="次",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-fishing",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )


@online_time_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.online_time",
    display_name="在线时长排行榜",
    permission="leaderboard.online_time",
    description="查看指定服务器的玩家在线时长排行榜",
    usage="在线时长排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
)
@require_permission("leaderboard.online_time")
async def handle_online_time_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "在线时长排行榜")
    if len(args) < 1 or len(args) > 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    page = _parse_page_arg(args[1:], "在线时长排行榜")
    if page is None:
        await bot.send(event, "查询失败，页数必须为正整数")
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        caller_id = event.get_user_id()
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_name = caller.name if caller is not None else None
    finally:
        session.close()

    if server is None:
        await bot.send(event, "查询失败，服务器不存在")
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/online-time")
    except TShockRequestError:
        await bot.send(event, "查询失败，无法连接服务器")
        return

    if not is_success(response):
        await bot.send(event, f"查询失败，{get_error_reason(response)}")
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, "查询失败，返回数据格式错误")
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("onlineSeconds"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, f"查询失败，超出总页数（共 {total_pages} 页）")
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": e["username"], "value": _format_online_seconds(int(e["onlineSeconds"]))}
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {
                    "rank": idx + 1,
                    "name": caller_name,
                    "value": _format_online_seconds(int(e["onlineSeconds"])),
                }
                break

    logger.info(
        f"在线时长排行榜查询成功：server_id={server_id} total={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title="在线时长排行榜",
        value_label="",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-online-time",
        self_entry=self_entry,
        theme=resolve_render_theme(),
    )
