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
from nextbot.db import Server, User, UserSignRecord, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.permissions import require_permission
from nextbot.time_utils import (
    beijing_filename_timestamp,
    beijing_today_text,
    format_online_seconds,
    utc_naive_to_beijing,
)
from nextbot.text_utils import reply_failure
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_leaderboard_page

coins_leaderboard_matcher = on_command("金币排行榜")
streak_leaderboard_matcher = on_command("连续签到排行榜")
signin_leaderboard_matcher = on_command("签到排行榜")
deaths_leaderboard_matcher = on_command("死亡排行榜")
fishing_leaderboard_matcher = on_command("渔夫任务排行榜")
online_time_leaderboard_matcher = on_command("在线时长排行榜")
map_exploration_leaderboard_matcher = on_command("地图探索率排行榜")
total_online_time_leaderboard_matcher = on_command("总在线时长排行榜")
daily_sign_leaderboard_matcher = on_command("今日签到排行榜")
rob_income_leaderboard_matcher = on_command("抢劫排行榜")
rob_loss_leaderboard_matcher = on_command("被抢排行榜")
rob_penalty_leaderboard_matcher = on_command("抢劫罚款排行榜")
rob_success_rate_leaderboard_matcher = on_command("抢劫成功率排行榜")
guess_income_leaderboard_matcher = on_command("猜数字排行榜")
guess_win_rate_leaderboard_matcher = on_command("猜数字胜率排行榜")
dice_income_leaderboard_matcher = on_command("掷骰子排行榜")
dice_win_rate_leaderboard_matcher = on_command("掷骰子胜率排行榜")

LEADERBOARD_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=800,
    full_page=True,
    fit_content_height=True,
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
        await bot.send(event, reply_failure("查询", f"{exc}"))
        return

    logger.info(f"{title}截图成功：page={page}/{total_pages} file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
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
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/deaths")
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("deaths"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
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
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/fishing-quests")
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("questsCompleted"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
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
    category="排行榜",
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
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
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
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/online-time")
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("onlineSeconds"), int)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": e["username"], "value": format_online_seconds(int(e["onlineSeconds"]))}
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {
                    "rank": idx + 1,
                    "name": caller_name,
                    "value": format_online_seconds(int(e["onlineSeconds"])),
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
    )


@map_exploration_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.map_exploration",
    display_name="地图探索率排行榜",
    permission="leaderboard.map_exploration",
    description="查看指定服务器的地图探索率排行榜",
    usage="地图探索率排行榜 <服务器 ID> [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.map_exploration")
async def handle_map_exploration_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "地图探索率排行榜")
    if len(args) < 1 or len(args) > 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    page = _parse_page_arg(args[1:], "地图探索率排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
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
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    try:
        response = await request_server_api(server, "/nextbot/leaderboards/map-exploration")
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict)
        and isinstance(e.get("username"), str)
        and isinstance(e.get("mapExplorationPercent"), (int, float))
        and not isinstance(e.get("mapExplorationPercent"), bool)
    ]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {
            "rank": offset + i + 1,
            "name": e["username"],
            "value": f"{float(e['mapExplorationPercent']):.2f}%",
        }
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {
                    "rank": idx + 1,
                    "name": caller_name,
                    "value": f"{float(e['mapExplorationPercent']):.2f}%",
                }
                break

    logger.info(
        f"地图探索率排行榜查询成功：server_id={server_id} total={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title="地图探索率排行榜",
        value_label="探索率",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-map-exploration",
        self_entry=self_entry,
    )


@total_online_time_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.total_online_time",
    display_name="总在线时长排行榜",
    permission="leaderboard.total_online_time",
    description="汇总所有服务器在线时长排行榜",
    usage="总在线时长排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.total_online_time")
async def handle_total_online_time_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "总在线时长排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "总在线时长排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
        caller_id = event.get_user_id()
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_name = caller.name if caller is not None else None
    finally:
        session.close()

    if not servers:
        await bot.send(event, reply_failure("查询", "暂无服务器"))
        return

    # 汇总各服务器数据，按用户名累加
    totals: dict[str, int] = {}
    success_count = 0
    for server in servers:
        try:
            response = await request_server_api(server, "/nextbot/leaderboards/online-time")
        except TShockRequestError:
            logger.info(f"总在线时长排行榜：server_id={server.id} 无法连接，已跳过")
            continue
        if not is_success(response):
            logger.info(f"总在线时长排行榜：server_id={server.id} 返回错误，已跳过")
            continue
        raw_entries = response.payload.get("entries")
        if not isinstance(raw_entries, list):
            continue
        for e in raw_entries:
            if isinstance(e, dict) and isinstance(e.get("username"), str) and isinstance(e.get("onlineSeconds"), int):
                username = e["username"]
                totals[username] = totals.get(username, 0) + int(e["onlineSeconds"])
        success_count += 1

    if not totals:
        await bot.send(event, reply_failure("查询", f"所有服务器均无法获取数据（共 {len(servers)} 台）"))
        return

    all_entries = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": username, "value": format_online_seconds(seconds)}
        for i, (username, seconds) in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, (username, seconds) in enumerate(all_entries):
            if username == caller_name:
                self_entry = {"rank": idx + 1, "name": caller_name, "value": format_online_seconds(seconds)}
                break

    logger.info(
        f"总在线时长排行榜查询成功：server_count={success_count}/{len(servers)} "
        f"total_players={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title="总在线时长排行榜",
        value_label="",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-total-online-time",
        self_entry=self_entry,
    )


def _format_sign_time(created_at) -> str:
    converted = utc_naive_to_beijing(created_at)
    if converted is None:
        return ""
    return converted.strftime("%H:%M:%S")


@daily_sign_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.daily_sign",
    display_name="今日签到排行榜",
    permission="leaderboard.daily_sign",
    description="查看今日签到先后顺序",
    usage="今日签到排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.daily_sign")
async def handle_daily_sign_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "今日签到排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "今日签到排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    today = beijing_today_text()

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = (
            session.query(UserSignRecord)
            .filter(UserSignRecord.sign_date == today)
            .count()
        )
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        records = (
            session.query(UserSignRecord, User.name)
            .join(User, User.user_id == UserSignRecord.user_id)
            .filter(UserSignRecord.sign_date == today)
            .order_by(UserSignRecord.created_at.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {
                "rank": offset + i + 1,
                "name": name or "",
                "user_id": record.user_id,
                "value": _format_sign_time(record.created_at),
            }
            for i, (record, name) in enumerate(records)
        ]

        self_entry = None
        caller_record = (
            session.query(UserSignRecord)
            .filter(
                UserSignRecord.sign_date == today,
                UserSignRecord.user_id == caller_id,
            )
            .first()
        )
        if caller_record is not None:
            caller_rank = (
                session.query(UserSignRecord)
                .filter(
                    UserSignRecord.sign_date == today,
                    UserSignRecord.created_at < caller_record.created_at,
                )
                .count()
                + 1
            )
            caller_user = session.query(User).filter(User.user_id == caller_id).first()
            caller_name = caller_user.name if caller_user else ""
            self_entry = {
                "rank": caller_rank,
                "name": caller_name,
                "value": _format_sign_time(caller_record.created_at),
            }
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="今日签到排行榜",
        value_label="签到时间",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-daily-sign",
        self_entry=self_entry,
    )


def _rob_net_income(user: User) -> int:
    return int(user.rob_total_gain or 0) - int(user.rob_total_penalty or 0)


@rob_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_income",
    display_name="抢劫排行榜",
    permission="leaderboard.rob_income",
    description="查看抢劫净收入排行榜",
    usage="抢劫排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.rob_income")
async def handle_rob_income_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "抢劫排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "抢劫排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = session.query(User).filter(User.rob_total_count > 0).all()
        sorted_users = sorted(all_users, key=_rob_net_income, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": _rob_net_income(u)}
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.rob_total_count or 0) > 0:
            caller_income = _rob_net_income(caller)
            caller_rank = sum(1 for u in sorted_users if _rob_net_income(u) > caller_income) + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_income}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="抢劫排行榜",
        value_label="净收入",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-rob-income",
        self_entry=self_entry,
    )


@rob_loss_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_loss",
    display_name="被抢排行榜",
    permission="leaderboard.rob_loss",
    description="查看被抢金额排行榜",
    usage="被抢排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.rob_loss")
async def handle_rob_loss_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "被抢排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "被抢排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).filter(User.rob_total_loss > 0).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        users = (
            session.query(User)
            .filter(User.rob_total_loss > 0)
            .order_by(User.rob_total_loss.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.rob_total_loss or 0)}
            for i, u in enumerate(users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.rob_total_loss or 0) > 0:
            caller_loss = int(caller.rob_total_loss or 0)
            caller_rank = session.query(User).filter(User.rob_total_loss > caller_loss).count() + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_loss}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="被抢排行榜",
        value_label="被抢金额",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-rob-loss",
        self_entry=self_entry,
    )


@rob_penalty_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_penalty",
    display_name="抢劫罚款排行榜",
    permission="leaderboard.rob_penalty",
    description="查看抢劫罚款金额排行榜",
    usage="抢劫罚款排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.rob_penalty")
async def handle_rob_penalty_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "抢劫罚款排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "抢劫罚款排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        total_count = session.query(User).filter(User.rob_total_penalty > 0).count()
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        users = (
            session.query(User)
            .filter(User.rob_total_penalty > 0)
            .order_by(User.rob_total_penalty.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": int(u.rob_total_penalty or 0)}
            for i, u in enumerate(users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.rob_total_penalty or 0) > 0:
            caller_penalty = int(caller.rob_total_penalty or 0)
            caller_rank = session.query(User).filter(User.rob_total_penalty > caller_penalty).count() + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_penalty}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="抢劫罚款排行榜",
        value_label="罚款金额",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-rob-penalty",
        self_entry=self_entry,
    )


@rob_success_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_success_rate",
    display_name="抢劫成功率排行榜",
    permission="leaderboard.rob_success_rate",
    description="查看抢劫成功率排行榜",
    usage="抢劫成功率排行榜 [页数]",
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
        "min_rob_count": {
            "type": "int",
            "label": "最低抢劫次数",
            "description": "上榜需要的最低抢劫次数",
            "required": False,
            "default": 1,
            "min": 1,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.rob_success_rate")
async def handle_rob_success_rate_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "抢劫成功率排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "抢劫成功率排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    min_rob_count = max(1, int(get_current_param("min_rob_count", 10)))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = (
            session.query(User)
            .filter(User.rob_total_count >= min_rob_count)
            .all()
        )

        def _success_rate(u: User) -> float:
            total = int(u.rob_total_count or 0)
            if total == 0:
                return 0.0
            return int(u.rob_success_count or 0) / total

        sorted_users = sorted(all_users, key=_success_rate, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_success_rate(u) * 100:.1f}%（{int(u.rob_success_count or 0)}/{int(u.rob_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.rob_total_count or 0) >= min_rob_count:
            caller_rate = _success_rate(caller)
            caller_rank = sum(1 for u in sorted_users if _success_rate(u) > caller_rate) + 1
            self_entry = {
                "rank": caller_rank,
                "name": caller.name,
                "value": f"{caller_rate * 100:.1f}%（{int(caller.rob_success_count or 0)}/{int(caller.rob_total_count or 0)}）",
            }
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="抢劫成功率排行榜",
        value_label="成功率",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-rob-rate",
        self_entry=self_entry,
    )


def _guess_net_income(user: User) -> int:
    return int(user.guess_total_gain or 0) - int(user.guess_total_loss or 0)


def _dice_net_income(user: User) -> int:
    return int(user.dice_total_gain or 0) - int(user.dice_total_loss or 0)


def _guess_win_rate(user: User) -> float:
    total = int(user.guess_total_count or 0)
    if total == 0:
        return 0.0
    return int(user.guess_win_count or 0) / total


def _dice_win_rate(user: User) -> float:
    total = int(user.dice_total_count or 0)
    if total == 0:
        return 0.0
    return int(user.dice_win_count or 0) / total


@guess_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.guess_number_income",
    display_name="猜数字排行榜",
    permission="leaderboard.guess_number_income",
    description="查看猜数字净收入排行榜",
    usage="猜数字排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.guess_number_income")
async def handle_guess_income_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "猜数字排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "猜数字排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = session.query(User).filter(User.guess_total_count > 0).all()
        sorted_users = sorted(all_users, key=_guess_net_income, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": _guess_net_income(u)}
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.guess_total_count or 0) > 0:
            caller_income = _guess_net_income(caller)
            caller_rank = sum(1 for u in sorted_users if _guess_net_income(u) > caller_income) + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_income}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="猜数字排行榜",
        value_label="净收入",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-guess-income",
        self_entry=self_entry,
    )


@guess_win_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.guess_number_win_rate",
    display_name="猜数字胜率排行榜",
    permission="leaderboard.guess_number_win_rate",
    description="查看猜数字胜率排行榜",
    usage="猜数字胜率排行榜 [页数]",
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
        "min_play_count": {
            "type": "int",
            "label": "最低参与次数",
            "description": "上榜需要的最低参与次数",
            "required": False,
            "default": 1,
            "min": 1,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.guess_number_win_rate")
async def handle_guess_win_rate_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "猜数字胜率排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "猜数字胜率排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    min_play_count = max(1, int(get_current_param("min_play_count", 1)))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = (
            session.query(User)
            .filter(User.guess_total_count >= min_play_count)
            .all()
        )

        def _sort_key(u: User) -> tuple[float, int]:
            return (_guess_win_rate(u), int(u.guess_total_count or 0))

        sorted_users = sorted(all_users, key=_sort_key, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_guess_win_rate(u) * 100:.1f}%（{int(u.guess_win_count or 0)}/{int(u.guess_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.guess_total_count or 0) >= min_play_count:
            caller_rate = _guess_win_rate(caller)
            caller_key = _sort_key(caller)
            caller_rank = sum(1 for u in sorted_users if _sort_key(u) > caller_key) + 1
            self_entry = {
                "rank": caller_rank,
                "name": caller.name,
                "value": f"{caller_rate * 100:.1f}%（{int(caller.guess_win_count or 0)}/{int(caller.guess_total_count or 0)}）",
            }
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="猜数字胜率排行榜",
        value_label="胜率",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-guess-win-rate",
        self_entry=self_entry,
    )


@dice_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.dice_income",
    display_name="掷骰子排行榜",
    permission="leaderboard.dice_income",
    description="查看掷骰子净收入排行榜",
    usage="掷骰子排行榜 [页数]",
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
    category="排行榜",
)
@require_permission("leaderboard.dice_income")
async def handle_dice_income_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "掷骰子排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "掷骰子排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = session.query(User).filter(User.dice_total_count > 0).all()
        sorted_users = sorted(all_users, key=_dice_net_income, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {"rank": offset + i + 1, "name": u.name, "user_id": u.user_id, "value": _dice_net_income(u)}
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.dice_total_count or 0) > 0:
            caller_income = _dice_net_income(caller)
            caller_rank = sum(1 for u in sorted_users if _dice_net_income(u) > caller_income) + 1
            self_entry = {"rank": caller_rank, "name": caller.name, "value": caller_income}
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="掷骰子排行榜",
        value_label="净收入",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-dice-income",
        self_entry=self_entry,
    )


@dice_win_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.dice_win_rate",
    display_name="掷骰子胜率排行榜",
    permission="leaderboard.dice_win_rate",
    description="查看掷骰子胜率排行榜",
    usage="掷骰子胜率排行榜 [页数]",
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
        "min_play_count": {
            "type": "int",
            "label": "最低参与次数",
            "description": "上榜需要的最低参与次数",
            "required": False,
            "default": 1,
            "min": 1,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.dice_win_rate")
async def handle_dice_win_rate_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "掷骰子胜率排行榜")
    if len(args) > 1:
        raise_command_usage()

    page = _parse_page_arg(args, "掷骰子胜率排行榜")
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    min_play_count = max(1, int(get_current_param("min_play_count", 1)))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        all_users = (
            session.query(User)
            .filter(User.dice_total_count >= min_play_count)
            .all()
        )

        def _sort_key(u: User) -> tuple[float, int]:
            return (_dice_win_rate(u), int(u.dice_total_count or 0))

        sorted_users = sorted(all_users, key=_sort_key, reverse=True)
        total_count = len(sorted_users)
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = sorted_users[offset : offset + limit]
        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_dice_win_rate(u) * 100:.1f}%（{int(u.dice_win_count or 0)}/{int(u.dice_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]
        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.dice_total_count or 0) >= min_play_count:
            caller_rate = _dice_win_rate(caller)
            caller_key = _sort_key(caller)
            caller_rank = sum(1 for u in sorted_users if _sort_key(u) > caller_key) + 1
            self_entry = {
                "rank": caller_rank,
                "name": caller.name,
                "value": f"{caller_rate * 100:.1f}%（{int(caller.dice_win_count or 0)}/{int(caller.dice_total_count or 0)}）",
            }
    finally:
        session.close()

    await _render_and_send(
        bot, event,
        title="掷骰子胜率排行榜",
        value_label="胜率",
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix="leaderboard-dice-win-rate",
        self_entry=self_entry,
    )
