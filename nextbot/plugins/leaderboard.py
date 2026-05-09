from __future__ import annotations

import asyncio
import math

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import desc, func

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
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.time_utils import (
    beijing_today_text,
    format_online_seconds,
    utc_naive_to_beijing,
)
from nextbot.text_utils import reply_failure, reply_info
from server.screenshot import ScreenshotOptions
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

# LB-0.1：handler-wide semaphore，防止 17 个 guest-default 命令并发刷屏导致 Playwright OOM 放大
_leaderboard_screenshot_semaphore = asyncio.Semaphore(2)

# LB-2.2：远端 TShock 返回 entries 数量上限（防中间人 / 后端 bug 塞超大 JSON）
MAX_LEADERBOARD_ENTRIES = 10000

# LB-3.2：总在线时长榜累计 username 字典 size 上限（攻击者控制 TShock 给出大量 fake username）
MAX_TOTAL_ONLINE_USERNAMES = 50000

# LB-3.1：fan-out 单服务器 timeout（默认 5s 对榜单偏短，10s 给慢 TShock）
LEADERBOARD_FETCH_TIMEOUT = 10.0

# LB-3.1：fan-out 跨服务器并发上限（避免一次性把所有 TShock 都吵醒）
_total_online_fanout_semaphore = asyncio.Semaphore(5)


def _parse_page_arg(args: list[str]) -> int | None:
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


def _format_remote_failure(reason: str) -> str:
    """LB-0.4：空 reason 时兜底为 "未知错误"，避免出现 ❌ 查询失败， 这种孤悬逗号文案。"""
    return reason or "未知错误"


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
        f"{title}渲染：page={page}/{total_pages} entry_count={len(entries)} "
        f"url_prefix={page_url[:80]}..."
    )

    await render_and_send_screenshot(
        bot, event,
        page_url=page_url,
        options=LEADERBOARD_SCREENSHOT_OPTIONS,
        file_prefix=file_prefix,
        semaphore=_leaderboard_screenshot_semaphore,
        failure_action="查询",
    )


# ---------- 基于 SQL 表达式的"得分榜"公共 helper ----------
# LB-10.1 / 13.1 / 14.1 / 15.1 / 16.1 / 17.1：把 6 个 handler 共同的"全表 ORM .all() + Python sort"
# 反模式抽成参数化 SQL 表达式 ORDER BY + LIMIT/OFFSET 的公共流程。
def _query_score_leaderboard(
    *,
    score_expr,
    min_count_filter,
    page: int,
    limit: int,
    caller_id: str,
    caller_score_value,
    caller_passes_filter: bool,
    entry_value_fn,
    self_entry_value_fn,
):
    """
    Args:
        score_expr: SQL 表达式（如 (User.rob_total_gain - User.rob_total_penalty)）。
        min_count_filter: SQL 过滤条件（如 User.rob_total_count > 0）。
        page / limit: 分页参数。
        caller_id: 当前用户 user_id（用于 self_entry）。
        caller_score_value: 调用者的当前 score_expr 值（用于 self rank COUNT）。
        caller_passes_filter: 调用者是否满足 min_count_filter。
        entry_value_fn(user) -> str | int：渲染列表中每行的 value 字段。
        self_entry_value_fn(caller) -> str | int：self_entry value 字段。

    Returns:
        (total_count, total_pages, entries, self_entry) —— entries 为 None
        表示请求页超出，调用方应回 reply_failure 给用户。
    """
    session = get_session()
    try:
        total_count = (
            session.query(func.count())
            .select_from(User)
            .filter(min_count_filter)
            .scalar()
            or 0
        )
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            return total_count, total_pages, None, None

        offset = (page - 1) * limit
        page_users = (
            session.query(User)
            .filter(min_count_filter)
            .order_by(desc(score_expr), User.user_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": entry_value_fn(u),
            }
            for i, u in enumerate(page_users)
        ]

        self_entry = None
        if caller_passes_filter:
            caller = session.query(User).filter(User.user_id == caller_id).first()
            if caller is not None:
                # rank = 严格大于本人 score 的人数 + 1
                higher_count = (
                    session.query(func.count())
                    .select_from(User)
                    .filter(min_count_filter)
                    .filter(score_expr > caller_score_value)
                    .scalar()
                    or 0
                )
                self_entry = {
                    "rank": higher_count + 1,
                    "name": caller.name,
                    "value": self_entry_value_fn(caller),
                }
        return total_count, total_pages, entries, self_entry
    finally:
        session.close()


# ---------- 金币排行榜 ----------

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

    page = _parse_page_arg(args)
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


# ---------- 连续签到排行榜 ----------

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

    page = _parse_page_arg(args)
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


# ---------- 签到排行榜 ----------

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

    page = _parse_page_arg(args)
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


# ---------- Server-side TShock 榜单公共 helper ----------

async def _server_side_leaderboard(
    bot: Bot,
    event: Event,
    *,
    args: list[str],
    title: str,
    value_label: str,
    file_prefix: str,
    endpoint: str,
    value_field: str,
    value_validator,
    value_formatter,
) -> None:
    """LB-4.1：4 个 server-side TShock 榜单的公共流程（deaths / fishing / online_time / map_exploration）。

    Args:
        args: 已 parsed 的命令参数（>=1，第一个是 server_id）。
        endpoint: 后端 API 路径（如 /nextbot/leaderboards/deaths）。
        value_field: 返回 entry 中的 value 字段名（如 "deaths" / "questsCompleted"）。
        value_validator(value) -> bool: 校验 value 类型。
        value_formatter(value) -> str | int: 渲染时使用的 value 文本（int / "12.34%" 等）。
    """
    if len(args) < 1 or len(args) > 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()
        return  # for type checker

    # LB-0.3：server_id <= 0 防御性校验
    if server_id <= 0:
        raise_command_usage()

    page = _parse_page_arg(args[1:])
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
        # LB-2.1：榜单查询给 10s timeout
        response = await request_server_api(server, endpoint, timeout=LEADERBOARD_FETCH_TIMEOUT)
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", _format_remote_failure(get_error_reason(response))))
        return

    raw_entries = response.payload.get("entries")
    if not isinstance(raw_entries, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    all_entries = [
        e for e in raw_entries
        if isinstance(e, dict) and isinstance(e.get("username"), str) and value_validator(e.get(value_field))
    ]

    # LB-2.2：远端 entries cap，防 OOM
    if len(all_entries) > MAX_LEADERBOARD_ENTRIES:
        logger.warning(
            f"{title}远端 entries 超过上限：server_id={server_id} "
            f"raw={len(all_entries)} cap={MAX_LEADERBOARD_ENTRIES}"
        )
        all_entries = all_entries[:MAX_LEADERBOARD_ENTRIES]

    total_count = len(all_entries)
    total_pages = max(1, math.ceil(total_count / limit))
    if total_count > 0 and page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

    offset = (page - 1) * limit
    page_entries = all_entries[offset: offset + limit]
    entries = [
        {"rank": offset + i + 1, "name": e["username"], "value": value_formatter(e[value_field])}
        for i, e in enumerate(page_entries)
    ]

    self_entry = None
    if caller_name is not None:
        for idx, e in enumerate(all_entries):
            if e.get("username") == caller_name:
                self_entry = {
                    "rank": idx + 1,
                    "name": caller_name,
                    "value": value_formatter(e[value_field]),
                }
                break

    logger.info(
        f"{title}查询成功：server_id={server_id} total={total_count} page={page}/{total_pages}"
    )

    await _render_and_send(
        bot, event,
        title=title,
        value_label=value_label,
        page=page,
        limit=limit,
        entries=entries,
        total_pages=total_pages,
        file_prefix=file_prefix,
        self_entry=self_entry,
    )


# ---------- 死亡排行榜 ----------

@deaths_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.deaths",
    display_name="死亡排行榜",
    permission="leaderboard.deaths",
    description="查看指定服务器的玩家死亡次数排行榜",
    usage="死亡排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.deaths")
async def handle_deaths_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "死亡排行榜")
    await _server_side_leaderboard(
        bot, event,
        args=args,
        title="死亡排行榜",
        value_label="次",
        file_prefix="leaderboard-deaths",
        endpoint="/nextbot/leaderboards/deaths",
        value_field="deaths",
        value_validator=lambda v: isinstance(v, int) and not isinstance(v, bool),
        value_formatter=lambda v: int(v),
    )


# ---------- 渔夫任务排行榜 ----------

@fishing_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.fishing",
    display_name="渔夫任务排行榜",
    permission="leaderboard.fishing",
    description="查看指定服务器的渔夫任务完成数排行榜",
    usage="渔夫任务排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.fishing")
async def handle_fishing_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "渔夫任务排行榜")
    await _server_side_leaderboard(
        bot, event,
        args=args,
        title="渔夫任务排行榜",
        value_label="次",
        file_prefix="leaderboard-fishing",
        endpoint="/nextbot/leaderboards/fishing-quests",
        value_field="questsCompleted",
        value_validator=lambda v: isinstance(v, int) and not isinstance(v, bool),
        value_formatter=lambda v: int(v),
    )


# ---------- 在线时长排行榜 ----------

@online_time_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.online_time",
    display_name="在线时长排行榜",
    permission="leaderboard.online_time",
    description="查看指定服务器的玩家在线时长排行榜",
    usage="在线时长排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.online_time")
async def handle_online_time_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "在线时长排行榜")
    await _server_side_leaderboard(
        bot, event,
        args=args,
        title="在线时长排行榜",
        value_label="",
        file_prefix="leaderboard-online-time",
        endpoint="/nextbot/leaderboards/online-time",
        value_field="onlineSeconds",
        value_validator=lambda v: isinstance(v, int) and not isinstance(v, bool),
        value_formatter=lambda v: format_online_seconds(int(v)),
    )


# ---------- 地图探索率排行榜 ----------

@map_exploration_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.map_exploration",
    display_name="地图探索率排行榜",
    permission="leaderboard.map_exploration",
    description="查看指定服务器的地图探索率排行榜",
    usage="地图探索率排行榜 <服务器 ID> [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="排行榜",
)
@require_permission("leaderboard.map_exploration")
async def handle_map_exploration_leaderboard(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "地图探索率排行榜")
    await _server_side_leaderboard(
        bot, event,
        args=args,
        title="地图探索率排行榜",
        value_label="探索率",
        file_prefix="leaderboard-map-exploration",
        endpoint="/nextbot/leaderboards/map-exploration",
        value_field="mapExplorationPercent",
        value_validator=lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        value_formatter=lambda v: f"{float(v):.2f}%",
    )


# ---------- 总在线时长排行榜（fan-out 跨服务器） ----------

@total_online_time_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.total_online_time",
    display_name="总在线时长排行榜",
    permission="leaderboard.total_online_time",
    description="汇总所有服务器在线时长排行榜",
    usage="总在线时长排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
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
        # LB-3.4："暂无服务器"用 reply_info 而不是 reply_failure（语义是空集，不是出错）
        await bot.send(event, reply_info("暂无服务器"))
        return

    # LB-3.1：并行 fan-out + per-server semaphore + 单服 timeout
    async def _fetch_one(server: Server) -> tuple[Server, list | None]:
        async with _total_online_fanout_semaphore:
            try:
                resp = await request_server_api(
                    server, "/nextbot/leaderboards/online-time",
                    timeout=LEADERBOARD_FETCH_TIMEOUT,
                )
            except TShockRequestError:
                logger.info(f"总在线时长排行榜：server_id={server.id} 无法连接，已跳过")
                return server, None
            if not is_success(resp):
                logger.info(f"总在线时长排行榜：server_id={server.id} 返回错误，已跳过")
                return server, None
            entries = resp.payload.get("entries")
            if not isinstance(entries, list):
                return server, None
            return server, entries

    fetch_results = await asyncio.gather(*[_fetch_one(s) for s in servers])

    # LB-3.2：totals dict 加 size cap，防控制 TShock 的攻击者塞海量假 username
    totals: dict[str, int] = {}
    success_count = 0
    capped = False
    for _, entries in fetch_results:
        if entries is None:
            continue
        success_count += 1
        for e in entries:
            if not (isinstance(e, dict) and isinstance(e.get("username"), str)
                    and isinstance(e.get("onlineSeconds"), int)):
                continue
            username = e["username"]
            if username in totals:
                totals[username] += int(e["onlineSeconds"])
            else:
                if len(totals) >= MAX_TOTAL_ONLINE_USERNAMES:
                    capped = True
                    continue
                totals[username] = int(e["onlineSeconds"])
    if capped:
        logger.warning(
            f"总在线时长排行榜：username 数已达上限 {MAX_TOTAL_ONLINE_USERNAMES}，"
            f"超出部分已丢弃"
        )

    if not totals:
        await bot.send(
            event,
            reply_failure("查询", f"所有服务器均无法获取数据（共 {len(servers)} 台）"),
        )
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


# ---------- 今日签到排行榜 ----------

@daily_sign_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.daily_sign",
    display_name="今日签到排行榜",
    permission="leaderboard.daily_sign",
    description="查看今日签到先后顺序",
    usage="今日签到排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
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


# ---------- 抢劫排行榜（净收入，SQL ORDER BY 表达式） ----------

@rob_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_income",
    display_name="抢劫排行榜",
    permission="leaderboard.rob_income",
    description="查看抢劫净收入排行榜",
    usage="抢劫排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    # caller stats 提前 fetch（用于计算 self_entry score）
    session = get_session()
    try:
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_passes = caller is not None and int(caller.rob_total_count or 0) > 0
        caller_score = (
            int(caller.rob_total_gain or 0) - int(caller.rob_total_penalty or 0)
            if caller is not None else 0
        )
    finally:
        session.close()

    score_expr = User.rob_total_gain - User.rob_total_penalty
    min_filter = User.rob_total_count > 0
    total_count, total_pages, entries, self_entry = _query_score_leaderboard(
        score_expr=score_expr,
        min_count_filter=min_filter,
        page=page,
        limit=limit,
        caller_id=caller_id,
        caller_score_value=caller_score,
        caller_passes_filter=caller_passes,
        entry_value_fn=lambda u: int(u.rob_total_gain or 0) - int(u.rob_total_penalty or 0),
        self_entry_value_fn=lambda u: int(u.rob_total_gain or 0) - int(u.rob_total_penalty or 0),
    )
    if entries is None:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

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


# ---------- 被抢排行榜 ----------

@rob_loss_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_loss",
    display_name="被抢排行榜",
    permission="leaderboard.rob_loss",
    description="查看被抢金额排行榜",
    usage="被抢排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
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


# ---------- 抢劫罚款排行榜 ----------

@rob_penalty_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_penalty",
    display_name="抢劫罚款排行榜",
    permission="leaderboard.rob_penalty",
    description="查看抢劫罚款金额排行榜",
    usage="抢劫罚款排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
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


# ---------- 抢劫成功率排行榜（SQL 表达式） ----------

@rob_success_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.rob_success_rate",
    display_name="抢劫成功率排行榜",
    permission="leaderboard.rob_success_rate",
    description="查看抢劫成功率排行榜",
    usage="抢劫成功率排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
        "min_rob_count": {
            "type": "int",
            "label": "最低抢劫次数",
            "description": "上榜需要的最低抢劫次数",
            "required": False,
            "default": 1,
            "min": 1,
            # LB-13.2：加 max 上限保持参数一致性
            "max": 100000,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    # LB-13.3：与 schema default=1 对齐（之前代码 default=10 不一致）
    min_rob_count = max(1, min(int(get_current_param("min_rob_count", 1)), 100000))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        # SQL 表达式：成功率 = success_count * 1.0 / total_count
        rate_expr = (User.rob_success_count * 1.0) / func.nullif(User.rob_total_count, 0)
        min_filter = User.rob_total_count >= min_rob_count

        total_count = (
            session.query(func.count())
            .select_from(User)
            .filter(min_filter)
            .scalar()
            or 0
        )
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = (
            session.query(User)
            .filter(min_filter)
            .order_by(desc(rate_expr), User.rob_total_count.desc(), User.user_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        def _rate(u: User) -> float:
            total = int(u.rob_total_count or 0)
            if total == 0:
                return 0.0
            return int(u.rob_success_count or 0) / total

        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_rate(u) * 100:.1f}%（{int(u.rob_success_count or 0)}/{int(u.rob_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]

        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.rob_total_count or 0) >= min_rob_count:
            caller_rate = _rate(caller)
            # rank = COUNT(rate > caller_rate) + 1，使用 SQL 表达式直接比较
            higher_count = (
                session.query(func.count())
                .select_from(User)
                .filter(min_filter)
                .filter(rate_expr > caller_rate)
                .scalar()
                or 0
            )
            self_entry = {
                "rank": higher_count + 1,
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


# ---------- 猜数字排行榜（净收入） ----------

@guess_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.guess_number_income",
    display_name="猜数字排行榜",
    permission="leaderboard.guess_number_income",
    description="查看猜数字净收入排行榜",
    usage="猜数字排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_passes = caller is not None and int(caller.guess_total_count or 0) > 0
        caller_score = (
            int(caller.guess_total_gain or 0) - int(caller.guess_total_loss or 0)
            if caller is not None else 0
        )
    finally:
        session.close()

    score_expr = User.guess_total_gain - User.guess_total_loss
    min_filter = User.guess_total_count > 0
    total_count, total_pages, entries, self_entry = _query_score_leaderboard(
        score_expr=score_expr,
        min_count_filter=min_filter,
        page=page,
        limit=limit,
        caller_id=caller_id,
        caller_score_value=caller_score,
        caller_passes_filter=caller_passes,
        entry_value_fn=lambda u: int(u.guess_total_gain or 0) - int(u.guess_total_loss or 0),
        self_entry_value_fn=lambda u: int(u.guess_total_gain or 0) - int(u.guess_total_loss or 0),
    )
    if entries is None:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

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


# ---------- 猜数字胜率排行榜 ----------

@guess_win_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.guess_number_win_rate",
    display_name="猜数字胜率排行榜",
    permission="leaderboard.guess_number_win_rate",
    description="查看猜数字胜率排行榜",
    usage="猜数字胜率排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
        "min_play_count": {
            "type": "int",
            "label": "最低参与次数",
            "description": "上榜需要的最低参与次数",
            "required": False,
            "default": 1,
            "min": 1,
            "max": 100000,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    min_play_count = max(1, min(int(get_current_param("min_play_count", 1)), 100000))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        rate_expr = (User.guess_win_count * 1.0) / func.nullif(User.guess_total_count, 0)
        min_filter = User.guess_total_count >= min_play_count

        total_count = (
            session.query(func.count())
            .select_from(User)
            .filter(min_filter)
            .scalar()
            or 0
        )
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = (
            session.query(User)
            .filter(min_filter)
            .order_by(desc(rate_expr), User.guess_total_count.desc(), User.user_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        def _rate(u: User) -> float:
            total = int(u.guess_total_count or 0)
            if total == 0:
                return 0.0
            return int(u.guess_win_count or 0) / total

        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_rate(u) * 100:.1f}%（{int(u.guess_win_count or 0)}/{int(u.guess_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]

        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.guess_total_count or 0) >= min_play_count:
            caller_rate = _rate(caller)
            higher_count = (
                session.query(func.count())
                .select_from(User)
                .filter(min_filter)
                .filter(rate_expr > caller_rate)
                .scalar()
                or 0
            )
            self_entry = {
                "rank": higher_count + 1,
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


# ---------- 掷骰子排行榜（净收入） ----------

@dice_income_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.dice_income",
    display_name="掷骰子排行榜",
    permission="leaderboard.dice_income",
    description="查看掷骰子净收入排行榜",
    usage="掷骰子排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        caller = session.query(User).filter(User.user_id == caller_id).first()
        caller_passes = caller is not None and int(caller.dice_total_count or 0) > 0
        caller_score = (
            int(caller.dice_total_gain or 0) - int(caller.dice_total_loss or 0)
            if caller is not None else 0
        )
    finally:
        session.close()

    score_expr = User.dice_total_gain - User.dice_total_loss
    min_filter = User.dice_total_count > 0
    total_count, total_pages, entries, self_entry = _query_score_leaderboard(
        score_expr=score_expr,
        min_count_filter=min_filter,
        page=page,
        limit=limit,
        caller_id=caller_id,
        caller_score_value=caller_score,
        caller_passes_filter=caller_passes,
        entry_value_fn=lambda u: int(u.dice_total_gain or 0) - int(u.dice_total_loss or 0),
        self_entry_value_fn=lambda u: int(u.dice_total_gain or 0) - int(u.dice_total_loss or 0),
    )
    if entries is None:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return

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


# ---------- 掷骰子胜率排行榜 ----------

@dice_win_rate_leaderboard_matcher.handle()
@command_control(
    command_key="leaderboard.dice_win_rate",
    display_name="掷骰子胜率排行榜",
    permission="leaderboard.dice_win_rate",
    description="查看掷骰子胜率排行榜",
    usage="掷骰子胜率排行榜 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页名次",
            "description": "每页显示的名次数",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
        "min_play_count": {
            "type": "int",
            "label": "最低参与次数",
            "description": "上榜需要的最低参与次数",
            "required": False,
            "default": 1,
            "min": 1,
            "max": 100000,
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

    page = _parse_page_arg(args)
    if page is None:
        await bot.send(event, reply_failure("查询", "页数必须为正整数"))
        return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))
    min_play_count = max(1, min(int(get_current_param("min_play_count", 1)), 100000))

    caller_id = event.get_user_id()
    session = get_session()
    try:
        rate_expr = (User.dice_win_count * 1.0) / func.nullif(User.dice_total_count, 0)
        min_filter = User.dice_total_count >= min_play_count

        total_count = (
            session.query(func.count())
            .select_from(User)
            .filter(min_filter)
            .scalar()
            or 0
        )
        total_pages = max(1, math.ceil(total_count / limit))
        if page > total_pages:
            await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        page_users = (
            session.query(User)
            .filter(min_filter)
            .order_by(desc(rate_expr), User.dice_total_count.desc(), User.user_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        def _rate(u: User) -> float:
            total = int(u.dice_total_count or 0)
            if total == 0:
                return 0.0
            return int(u.dice_win_count or 0) / total

        entries = [
            {
                "rank": offset + i + 1,
                "name": u.name,
                "user_id": u.user_id,
                "value": f"{_rate(u) * 100:.1f}%（{int(u.dice_win_count or 0)}/{int(u.dice_total_count or 0)}）",
            }
            for i, u in enumerate(page_users)
        ]

        caller = session.query(User).filter(User.user_id == caller_id).first()
        self_entry = None
        if caller is not None and int(caller.dice_total_count or 0) >= min_play_count:
            caller_rate = _rate(caller)
            higher_count = (
                session.query(func.count())
                .select_from(User)
                .filter(min_filter)
                .filter(rate_expr > caller_rate)
                .scalar()
                or 0
            )
            self_entry = {
                "rank": higher_count + 1,
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
