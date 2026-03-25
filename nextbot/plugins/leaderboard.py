from __future__ import annotations

import base64
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


@coins_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.coins",
    display_name="金币排行榜",
    permission="leaderboard.coins",
    description="查看金币数量排行榜",
    usage="金币排行榜",
    params={
        "limit": {
            "type": "int",
            "label": "显示名次",
            "description": "排行榜显示的最大名次数",
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
    if args:
        raise_command_usage()

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        users = (
            session.query(User)
            .order_by(User.coins.desc())
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.coins or 0)}
            for i, u in enumerate(users)
        ]
    finally:
        session.close()

    page_url = create_leaderboard_page(title="金币排行榜", value_label="金币", entries=entries)
    logger.info(
        f"金币排行榜渲染地址：entry_count={len(entries)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"leaderboard-coins-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=LEADERBOARD_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, f"查询失败，{exc}")
        return

    logger.info(f"金币排行榜截图成功：entry_count={len(entries)} file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, "查询失败，读取截图文件失败")
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"截图成功，文件：{screenshot_path}")


@streak_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.streak",
    display_name="连续签到排行榜",
    permission="leaderboard.streak",
    description="查看连续签到天数排行榜",
    usage="连续签到排行榜",
    params={
        "limit": {
            "type": "int",
            "label": "显示名次",
            "description": "排行榜显示的最大名次数",
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
    if args:
        raise_command_usage()

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        users = (
            session.query(User)
            .order_by(User.sign_streak.desc())
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.sign_streak or 0)}
            for i, u in enumerate(users)
        ]
    finally:
        session.close()

    page_url = create_leaderboard_page(title="连续签到排行榜", value_label="天", entries=entries)
    logger.info(
        f"连续签到排行榜渲染地址：entry_count={len(entries)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"leaderboard-streak-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=LEADERBOARD_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, f"查询失败，{exc}")
        return

    logger.info(f"连续签到排行榜截图成功：entry_count={len(entries)} file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, "查询失败，读取截图文件失败")
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"截图成功，文件：{screenshot_path}")
