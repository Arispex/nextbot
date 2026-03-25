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
from nextbot.db import User, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.time_utils import beijing_filename_timestamp
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_leaderboard_page

coins_leaderboard_matcher = on_command("金币排行榜")
streak_leaderboard_matcher = on_command("连续签到排行榜")

LEADERBOARD_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=900,
    viewport_height=800,
    full_page=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


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
) -> None:
    page_url = create_leaderboard_page(
        title=title,
        value_label=value_label,
        page=page,
        total_pages=total_pages,
        entries=entries,
        self_entry=self_entry,
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
    )
