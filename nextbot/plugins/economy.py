from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from nextbot.audit import audit_permission_change
from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import Server, User, UserSignRecord, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_COIN,
    EMOJI_FIRE,
    EMOJI_USER,
    reply_block,
    reply_failure,
    reply_success,
    safe_at_segment_or_empty,
)
from nextbot.time_utils import beijing_today_text
from nextbot.tshock_api import TShockRequestError, is_success, request_server_api
from server.screenshot import ScreenshotOptions
from server.web_server import create_signin_page

sign_matcher = on_command("签到")
transfer_matcher = on_command("转账")
add_coins_matcher = on_command("添加金币")
remove_coins_matcher = on_command("扣除金币")

# SF-X.1：账户上限（hard cap on User.coins balance）。
#
# 语义：用户 coins 余额任何时刻不得超过此值。所有 +coins 写入路径都必须
# 加 `User.coins + delta <= MAX_COINS_AMOUNT` 条件 UPDATE，触顶时退而求其次
# 加到上限即止（partial cap，参考 lottery._charge_atomic 模板）。
#
# 同时也用作单笔操作的 sanity 上界（add/remove/transfer/red_packet send/
# warehouse value/shop total_price 等），防御 admin 配置过大数值导致溢出。
#
# 上限值为 100 亿（10_000_000_000），SQLite INTEGER 是 64-bit signed
# (max 9.2e18)，远未达到 schema 上限，无需 schema 调整。所有 cap 防御
# 逻辑保留，仅放宽阈值。
MAX_COINS_AMOUNT = 10_000_000_000

# 限制 签到 同时渲染数量，避免 Playwright 浏览器并发过高。
# 签到单页轻量（静态点链 + 几行文字），与 dice 同档放宽到 4，
# 群多人同时签到时高峰仍能并行处理。
_signin_semaphore = asyncio.Semaphore(4)


def _parse_positive_int(text: str) -> int | None:
    value = text.strip()
    if not value or not value.isdigit():
        return None

    amount = int(value)
    if amount <= 0:
        return None
    return amount


def _exceeds_max_amount(amount: int) -> bool:
    return amount > MAX_COINS_AMOUNT


def add_coins_with_cap(session, user_id: str, delta: int) -> tuple[int, bool]:
    """带账户上限的加币原子操作。

    SF-X.1：所有 +coins 路径统一走此 helper，避免单笔合规但累加越界。

    返回 ``(applied_delta, capped)``：
        - applied_delta：实际入账的金币数（可能 < delta）
        - capped：True 表示触顶（部分或完全无法入账），调用方可据此提示用户

    语义：先尝试一次性加 delta（条件 `coins + delta <= cap`），若行影响为 0
    则退而求其次按可加余量加（partial cap）。delta <= 0 直接返回 (0, False)。

    调用方负责 commit / rollback。本函数仅 execute UPDATE。
    """
    if delta <= 0:
        if delta < 0:
            # R3N-1.3：区分 delta=0（合法 no-op）与 delta<0（调用方 bug）。
            # 仍 return (0, False) 不抛异常，但记 warning 帮助定位上游问题。
            logger.warning(
                f"add_coins_with_cap 收到负 delta：user_id={user_id} delta={delta}"
            )
        return 0, False
    rowcount = execute_rowcount(
        session,
        update(User)
        .where(
            User.user_id == user_id,
            User.coins + delta <= MAX_COINS_AMOUNT,
        )
        .values(coins=User.coins + delta),
    )
    if rowcount > 0:
        return delta, False
    # 触顶：按可加余量加
    coins_now = int(
        session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
    )
    room = max(0, MAX_COINS_AMOUNT - coins_now)
    if room <= 0:
        logger.warning(
            f"金币加币触顶 cap：user_id={user_id} requested_delta={delta} applied=0"
        )
        return 0, True
    partial = min(delta, room)
    rowcount = execute_rowcount(
        session,
        update(User)
        .where(
            User.user_id == user_id,
            User.coins + partial <= MAX_COINS_AMOUNT,
        )
        .values(coins=User.coins + partial),
    )
    if rowcount > 0:
        logger.warning(
            f"金币加币部分被 cap：user_id={user_id} requested_delta={delta} applied={partial}"
        )
        return partial, True
    # 极端情况：在我们 SELECT 之后又有人加了币把 room 占掉
    logger.warning(
        f"金币加币触顶 cap（partial UPDATE 也失败）：user_id={user_id} requested_delta={delta}"
    )
    return 0, True


def subtract_coins_with_floor(session, user_id: str, delta: int) -> tuple[int, bool]:
    """带余额下限的扣币原子操作（``add_coins_with_cap`` 的对偶）。

    R3E-3 / R3N-3.2：让 lottery._charge_atomic 等所有 -coins 路径统一走
    此 helper，避免每个 caller 自实现 partial floor 逻辑导致行为漂移。

    返回 ``(applied_delta, floored)``：
        - applied_delta：实际扣除的金币数（>=0，可能 < delta）
        - floored：True 表示被余额下限限制（部分或完全无法扣）

    语义：先尝试一次性扣 delta（条件 ``coins >= delta``），若行影响为 0
    则退而求其次扣到 0 余量。delta <= 0 直接返回 (0, False)，但
    delta < 0 时 logger.warning（调用方 bug）。

    调用方负责 commit / rollback。本函数仅 execute UPDATE。
    """
    if delta <= 0:
        if delta < 0:
            # 区分 delta=0（合法 no-op）与 delta<0（调用方 bug）
            logger.warning(
                f"subtract_coins_with_floor 收到负 delta：user_id={user_id} delta={delta}"
            )
        return 0, False
    rowcount = execute_rowcount(
        session,
        update(User)
        .where(User.user_id == user_id, User.coins >= delta)
        .values(coins=User.coins - delta),
    )
    if rowcount > 0:
        return delta, False
    # 余额不足：扣到 0 即止
    coins_now = int(
        session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
    )
    if coins_now <= 0:
        logger.warning(
            f"金币扣币触底 floor：user_id={user_id} requested_delta={delta} applied=0"
        )
        return 0, True
    partial = min(delta, coins_now)
    rowcount = execute_rowcount(
        session,
        update(User)
        .where(User.user_id == user_id, User.coins >= partial)
        .values(coins=User.coins - partial),
    )
    if rowcount > 0:
        logger.warning(
            f"金币扣币部分被 floor：user_id={user_id} requested_delta={delta} applied={partial}"
        )
        return partial, True
    # 极端情况：SELECT 之后又有人扣了币
    logger.warning(
        f"金币扣币触底 floor（partial UPDATE 也失败）：user_id={user_id} requested_delta={delta}"
    )
    return 0, True


@dataclass(frozen=True)
class SignResult:
    next_streak: int
    streak_reward: int


def _today_text() -> str:
    return beijing_today_text()


def _resolve_streak_reward(
    *,
    last_sign_date: str,
    current_streak: int,
    enable_streak: bool,
    streak_bonus_per_day: int,
    max_streak_bonus: int,
    today_text: str,
) -> SignResult:
    if not enable_streak:
        return SignResult(next_streak=1, streak_reward=0)

    yesterday_text = (date.fromisoformat(today_text) - timedelta(days=1)).isoformat()
    normalized_streak = max(int(current_streak), 0)
    if last_sign_date == yesterday_text:
        next_streak = normalized_streak + 1
    else:
        next_streak = 1

    streak_reward = max(next_streak - 1, 0) * max(streak_bonus_per_day, 0)
    return SignResult(
        next_streak=next_streak,
        streak_reward=min(streak_reward, max(max_streak_bonus, 0)),
    )


async def _check_player_online_anywhere(
    player_name: str,
) -> tuple[bool, int | None, int]:
    """并行查询所有服务器，判断玩家是否在任意一台在线。

    返回 ``(is_online, hit_server_id, probed_count)``：
        - is_online：是否在任意一台服务器命中
        - hit_server_id：命中的 server.id（未命中则 None）
        - probed_count：本次探测的服务器数量（用于日志/可观测）

    匹配规则：strip + casefold 比较 ``User.name`` 与每台服务器
    ``/v2/server/status?players=true`` 的 ``players[].nickname``。

    异常 / 单台失败均按"未命中"处理，不抛。服务器列表为空直接返回
    ``(False, None, 0)``，调用方按"不在线"分支处理（PRD 决策）。
    """
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    probed_count = len(servers)
    if probed_count == 0:
        return False, None, 0

    normalized_target = player_name.strip().casefold()
    if not normalized_target:
        # 空 player_name 永远不会命中任何 nickname；跳过 HTTP 直接返回未命中
        return False, None, probed_count

    async def _probe(server: Server) -> int | None:
        """命中返回 server.id；未命中或失败返回 None。异常不外抛。"""
        try:
            response = await request_server_api(
                server,
                "/v2/server/status",
                params={"players": "true"},
            )
        except TShockRequestError:
            return None

        if not is_success(response):
            return None

        players = response.payload.get("players")
        if not isinstance(players, list):
            return None

        for player in players:
            if not isinstance(player, dict):
                continue
            nickname = str(player.get("nickname", "")).strip().casefold()
            if nickname and nickname == normalized_target:
                return server.id
        return None

    # 简单可读为先：gather 等所有结果后再判定（PRD 默认推荐方案）
    raw_results = await asyncio.gather(
        *(_probe(s) for s in servers), return_exceptions=True
    )
    for server, raw in zip(servers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            logger.warning(
                f"签到在线探测异常：server_id={server.id} reason={raw!r}"
            )
            continue
        if raw is not None:
            return True, raw, probed_count
    return False, None, probed_count


@sign_matcher.handle()
@command_control(
    command_key="economy.sign",
    display_name="签到",
    permission="economy.sign",
    description="每日签到获取随机金币奖励",
    usage="签到",
    params={
        "min_coins": {
            "type": "int",
            "label": "最小奖励金币",
            "description": "签到随机奖励的最小金币值",
            "required": False,
            "default": 10,
            "min": 0,
        },
        "max_coins": {
            "type": "int",
            "label": "最大奖励金币",
            "description": "签到随机奖励的最大金币值",
            "required": False,
            "default": 100,
            "min": 0,
        },
        "enable_streak": {
            "type": "bool",
            "label": "开启连续签到",
            "description": "开启后按连续签到天数追加奖励",
            "required": False,
            "default": True,
        },
        "streak_bonus_per_day": {
            "type": "int",
            "label": "连续签到每日奖励",
            "description": "连续签到第 N 天额外奖励为 (N-1) * 此值",
            "required": False,
            "default": 10,
            "min": 0,
        },
        "max_streak_bonus": {
            "type": "int",
            "label": "连续签到最大奖励",
            "description": "连续签到奖励超过该值时会被限制到该值",
            "required": False,
            "default": 140,
            "min": 0,
        },
        "require_online": {
            "type": "bool",
            "label": "要求在线",
            "description": "开启后玩家必须在任意服务器在线才能签到",
            "required": False,
            "default": False,
        },
    },
    category="经济系统",
)
@require_permission("economy.sign")
async def handle_sign(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "签到")
    if args:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())
    min_coins = int(get_current_param("min_coins", 10))
    max_coins = int(get_current_param("max_coins", 30))
    enable_streak = bool(get_current_param("enable_streak", True))
    streak_bonus_per_day = int(get_current_param("streak_bonus_per_day", 5))
    max_streak_bonus = int(get_current_param("max_streak_bonus", 50))
    require_online = bool(get_current_param("require_online", False))

    if min_coins < 0 or max_coins < 0 or streak_bonus_per_day < 0 or max_streak_bonus < 0:
        await bot.send(event, at + " " + reply_failure("签到", "签到奖励配置不能为负数"))
        return
    if min_coins > max_coins:
        await bot.send(event, at + " " + reply_failure("签到", "签到奖励配置错误：最小值不能大于最大值"))
        return

    user_id = event.get_user_id()
    today_text = _today_text()

    # require_online：注册检查 + 今日已签检查通过后，并行 fan-out 查询所有服务器，
    # 任意一台命中 → 视为在线，进入下方主流程；全部未命中 / 空服务器列表 → 失败。
    # 检查在主 session 之外完成，避免 HTTP fan-out 期间长时间持有 DB 连接。
    if require_online:
        precheck_session = get_session()
        try:
            precheck_user = (
                precheck_session.query(User).filter(User.user_id == user_id).first()
            )
            if precheck_user is None:
                await bot.send(event, at + " " + reply_failure("签到", "请先注册账号"))
                return
            if str(precheck_user.last_sign_date or "").strip() == today_text:
                await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
                return
            player_name_for_check = str(precheck_user.name or "").strip()
        finally:
            precheck_session.close()

        is_online, hit_server_id, probed_count = await _check_player_online_anywhere(
            player_name_for_check,
        )
        logger.info(
            f"签到在线检查：user_id={user_id} name={player_name_for_check} "
            f"online={is_online} hit_server_id={hit_server_id} "
            f"probed_count={probed_count}"
        )
        if not is_online:
            await bot.send(event, at + " " + reply_failure("签到", "请先进入服务器"))
            return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("签到", "请先注册账号"))
            return

        # 单一真源：只判断 last_sign_date 是否是今天（不再读 signed_today，
        # 该字段已 DEPRECATED；仅保留列以兼容旧 schema）。
        last_sign_date = str(user.last_sign_date or "").strip()
        if last_sign_date == today_text:
            await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
            return

        base_reward = random.randint(min_coins, max_coins)
        previous_streak = int(user.sign_streak or 0)
        streak_result = _resolve_streak_reward(
            last_sign_date=last_sign_date,
            current_streak=previous_streak,
            enable_streak=enable_streak,
            streak_bonus_per_day=streak_bonus_per_day,
            max_streak_bonus=max_streak_bonus,
            today_text=today_text,
        )
        total_reward = base_reward + streak_result.streak_reward
        # requested_reward 保留 cap 前的理论奖励，用于截图模板展示 cap 损耗对账。
        # streak_broken：开启连续签到的前提下，原本 streak>0 而本次被重置为 1，
        # 视为"连续中断"；首次签到（previous_streak=0 → next_streak=1）不算中断。
        requested_reward = total_reward
        streak_broken = bool(
            enable_streak
            and previous_streak > 0
            and streak_result.next_streak == 1
        )

        # capped：是否走 partial cap 分支（金币入账触顶 MAX_COINS_AMOUNT），
        # 默认 False；仅在下方 rowcount==0 → partial cap 分支被置 True。
        # 与模板 cap warning 横条对账。
        capped = False
        applied_reward = total_reward

        # 原子条件 UPDATE：仅当 last_sign_date != today 时才写入。
        # 并发同时签到时，第二条 rowcount=0，被 schema/SQL 层拦下。
        # SF-X.1：加 `coins + total_reward <= MAX_COINS_AMOUNT` 守护账户上限；
        # 触顶时仅写 streak / sign_total / last_sign_date，金币按可加余量加（partial cap）。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(
                User.user_id == user_id,
                User.last_sign_date != today_text,
                User.coins + total_reward <= MAX_COINS_AMOUNT,
            )
            .values(
                coins=User.coins + total_reward,
                last_sign_date=today_text,
                sign_streak=streak_result.next_streak,
                sign_total=User.sign_total + 1,
            ),
        )
        if rowcount == 0:
            # 检查是否真的"今天已签"还是"账户触顶"
            current_user = session.query(User).filter(User.user_id == user_id).first()
            if current_user is None:
                await bot.send(event, at + " " + reply_failure("签到", "请先注册账号"))
                return
            if str(current_user.last_sign_date or "") == today_text:
                await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
                return
            # 余额触顶 → partial cap：写 streak / sign_total / last_sign_date，
            # coins 加到 cap 即止
            coins_now = int(current_user.coins or 0)
            room = max(0, MAX_COINS_AMOUNT - coins_now)
            applied_reward = min(total_reward, room)
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(
                    User.user_id == user_id,
                    User.last_sign_date != today_text,
                    User.coins + applied_reward <= MAX_COINS_AMOUNT,
                )
                .values(
                    coins=User.coins + applied_reward,
                    last_sign_date=today_text,
                    sign_streak=streak_result.next_streak,
                    sign_total=User.sign_total + 1,
                ),
            )
            if rowcount == 0:
                await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
                return
            if applied_reward < total_reward:
                logger.warning(
                    f"签到金币触顶 cap：user_id={user_id} "
                    f"requested={total_reward} applied={applied_reward}"
                )
                capped = True
            total_reward = applied_reward

        # 写 UserSignRecord —— UniqueConstraint(user_id, sign_date) 兜底并发。
        try:
            session.add(UserSignRecord(
                user_id=user_id,
                sign_date=today_text,
                streak=streak_result.next_streak,
            ))
            session.commit()
        except IntegrityError:
            session.rollback()
            await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
            return

        today_order = (
            session.query(UserSignRecord)
            .filter(UserSignRecord.sign_date == today_text)
            .count()
        )
        coins_after = int(
            session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
        )
        sign_total = int(
            session.query(User.sign_total).filter(User.user_id == user_id).scalar() or 0
        )
        user_name = str(
            session.query(User.name).filter(User.user_id == user_id).scalar() or ""
        )

        # 截图模板 hybrid streak chain：查询过去 30 天的真实签到记录，
        # 构建 recent_signs[30] bool 数组（index 0 = 29 天前，index 29 = 今天）。
        # 本次签到已 commit 进 UserSignRecord，所以 recent_signs[29] 一定为 True。
        today_dt = date.fromisoformat(today_text)
        date_list = [
            (today_dt - timedelta(days=29 - i)).isoformat()
            for i in range(30)
        ]
        signed_rows = (
            session.query(UserSignRecord.sign_date)
            .filter(
                UserSignRecord.user_id == user_id,
                UserSignRecord.sign_date.in_(date_list),
            )
            .all()
        )
        signed_set = {str(row.sign_date) for row in signed_rows}
        recent_signs = [d in signed_set for d in date_list]

        before_coins = coins_after - total_reward
        logger.info(
            f"金币变更：actor={user_id} target={user_id} action=economy.sign "
            f"name={user_name} base_reward={base_reward} streak_reward={streak_result.streak_reward} "
            f"amount={total_reward} before={before_coins} after={coins_after} "
            f"streak={streak_result.next_streak} today_order={today_order} reason=daily_sign"
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback，避免依赖 session.close()
        # 隐式 rollback；与同文件 IntegrityError 分支风格统一。
        session.rollback()
        logger.exception(f"签到处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("签到", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    # 成功落库；走截图渲染。所有截图链路异常 → 降级为原 7 行纯文本回复，
    # 保证用户即便渲染挂掉也能看到完整反馈（且金币已落库不会被回滚）。
    next_streak = streak_result.next_streak
    streak_reward_value = streak_result.streak_reward
    try:
        page_url = create_signin_page(
            player_name=user_name,
            player_qq=user_id,
            today_order=today_order,
            base_reward=base_reward,
            streak_reward=streak_reward_value,
            total_reward=total_reward,
            current_streak=next_streak,
            streak_enabled=enable_streak,
            streak_broken=streak_broken,
            recent_signs=recent_signs,
            coins_after=coins_after,
            sign_total=sign_total,
            capped=capped,
            requested_reward=requested_reward,
            applied_reward=applied_reward,
        )
        ok = await render_and_send_screenshot(
            bot,
            event,
            page_url=page_url,
            options=ScreenshotOptions(
                viewport_width=720,
                viewport_height=720,
                fit_content_height=True,
            ),
            file_prefix="signin",
            semaphore=_signin_semaphore,
            failure_action="签到",
            at_user_id=user_id,
        )
        if ok:
            return
        # render_and_send_screenshot 在内部 failure 路径已经 reply_failure 给
        # 用户；此处不再降级到纯文本，避免一次签到收到两条消息。仅日志告警。
        logger.warning(
            f"签到截图发送失败：user_id={user_id} streak={next_streak} order={today_order}"
        )
        return
    except Exception as exc:  # noqa: BLE001
        # 渲染链路本身抛异常（create_signin_page / Playwright 启动 / 模板缺失等
        # render_and_send_screenshot 未捕获到的异常）：金币已入账，必须给出文本
        # 反馈，避免用户以为签到失败。
        logger.warning(
            f"签到截图渲染异常，降级为文本回复：user_id={user_id} reason={exc!r}"
        )
        lines = [
            f"{EMOJI_CHART} 签到排名：第 {today_order} 位",
            f"{EMOJI_COIN} 基础奖励：{base_reward}",
            f"{EMOJI_FIRE} 连续签到：{next_streak} 天",
        ]
        if enable_streak:
            lines.append(f"{EMOJI_COIN} 连续签到奖励：{streak_reward_value}")
        else:
            lines.append(f"{EMOJI_COIN} 连续签到奖励：未开启")
        lines.extend(
            [
                f"{EMOJI_COIN} 本次总获得：{total_reward}",
                f"{EMOJI_COIN} 当前金币：{coins_after}",
            ]
        )
        try:
            await bot.send(
                event,
                at + "\n" + reply_block(
                    reply_success("签到"),
                    lines,
                    hint="明日继续签到可获得连续奖励",
                ),
            )
        except Exception:  # noqa: BLE001
            pass


@transfer_matcher.handle()
@command_control(
    command_key="economy.transfer",
    display_name="转账",
    permission="economy.transfer",
    description="向其他用户转账金币",
    usage="转账 <用户 QQ/@用户/用户名称> <金币数量>",
    category="经济系统",
)
@require_permission("economy.transfer")
async def handle_transfer(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "转账")
    if len(args) != 2:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "转账", arg_index=0
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("转账", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("转账", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("转账", "用户参数解析失败"))
        return

    # F-2.2：解析风格统一为 _parse_positive_int（与 add/remove 一致）。
    # 保留两条原文案区分非整数 vs 非正数。
    amount_str = args[1].strip()
    if not amount_str.isdigit():
        # _parse_positive_int 严格 isdigit 拒绝 "+100" / "1_000" / 负号等
        await bot.send(event, at + " " + reply_failure("转账", "数量必须为整数"))
        return
    amount = _parse_positive_int(amount_str)
    if amount is None:
        await bot.send(event, at + " " + reply_failure("转账", "数量必须大于 0"))
        return
    if _exceeds_max_amount(amount):
        await bot.send(
            event,
            at + " " + reply_failure("转账", f"数量过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    sender_id = event.get_user_id()
    if sender_id == target_user_id:
        await bot.send(event, at + " " + reply_failure("转账", "不能转账给自己"))
        return

    session = get_session()
    try:
        sender = session.query(User).filter(User.user_id == sender_id).first()
        if sender is None:
            await bot.send(event, at + " " + reply_failure("转账", "请先注册账号"))
            return

        target = session.query(User).filter(User.user_id == target_user_id).first()
        if target is None:
            await bot.send(event, at + " " + reply_failure("转账", "目标用户不存在"))
            return

        # 一次原子条件 UPDATE：扣 sender（带 coins ≥ amount 条件）。
        # 并发被抢走时 rowcount=0，回退"金币不足"。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == sender_id, User.coins >= amount)
            .values(coins=User.coins - amount),
        )
        if rowcount == 0:
            sender_coins_now = int(
                session.query(User.coins).filter(User.user_id == sender_id).scalar() or 0
            )
            await bot.send(
                event,
                at + " " + reply_failure("转账", f"金币不足（当前：{sender_coins_now}）"),
            )
            return

        # 加 target（同事务原子完成）
        # SF-X.1：账户上限保护——触顶时按可加余量加，剩余金额回退给 sender
        applied_amount, capped = add_coins_with_cap(session, target_user_id, amount)
        refund_amount = 0
        if capped and applied_amount < amount:
            # 把未入账的部分退回 sender
            refund_amount = amount - applied_amount
            execute_rowcount(
                session,
                update(User)
                .where(User.user_id == sender_id)
                .values(coins=User.coins + refund_amount),
            )
            logger.warning(
                f"转账触顶 cap：sender={sender_id} target={target_user_id} "
                f"requested={amount} applied={applied_amount} refund={refund_amount}"
            )
        session.commit()

        sender_after = int(
            session.query(User.coins).filter(User.user_id == sender_id).scalar() or 0
        )
        target_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )

        logger.info(
            f"金币变更：actor={sender_id} target={target_user_id} action=economy.transfer "
            f"requested={amount} applied={applied_amount} refund={refund_amount} "
            f"sender_remaining={sender_after} reason=transfer"
        )
        success_lines = [
            f"{EMOJI_COIN} 转出金币：{applied_amount}",
            f"{EMOJI_USER} 转账对象：{target_name}（{target_user_id}）",
            f"{EMOJI_COIN} 当前余额：{sender_after}",
        ]
        if refund_amount > 0:
            success_lines.append(
                f"⚠️ 对方账户已触上限，{refund_amount} 金币已退回",
            )
        await bot.send(
            event,
            at + "\n" + reply_block(
                reply_success("转账"),
                success_lines,
            ),
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"转账处理异常：sender_id={sender_id}")
        try:
            await bot.send(event, at + " " + reply_failure("转账", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()


@add_coins_matcher.handle()
@command_control(
    command_key="economy.coins.add",
    display_name="添加金币",
    permission="economy.coins.add",
    description="为指定用户增加金币",
    usage="添加金币 <用户 QQ/@用户/用户名称> <金币数量>",
    category="经济系统",
)
@require_permission("economy.coins.add")
async def handle_add_coins(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加金币")
    if len(args) != 2:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "添加金币",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("添加", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("添加", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("添加", "用户参数解析失败"))
        return

    amount = _parse_positive_int(args[1])
    if amount is None:
        await bot.send(event, at + " " + reply_failure("添加", "数量必须为正整数"))
        return
    if _exceeds_max_amount(amount):
        await bot.send(
            event,
            at + " " + reply_failure("添加", f"数量过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    actor_id = event.get_user_id()
    session = get_session()
    applied_amount = 0
    capped = False
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
            return

        # SF-X.1：账户上限保护——触顶时按可加余量加（partial cap）
        applied_amount, capped = add_coins_with_cap(session, target_user_id, amount)
        session.commit()
        coins = int(
            session.query(User.coins).filter(User.user_id == target_user_id).scalar() or 0
        )
        user_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"添加金币处理异常：user_id={target_user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("添加", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    before_coins = coins - applied_amount
    logger.info(
        f"金币变更：actor={actor_id} target={target_user_id} action=economy.coins.add "
        f"name={user_name} requested={amount} applied={applied_amount} "
        f"before={before_coins} after={coins} reason=admin_add"
    )
    # PC-6.1：admin 加币是直接对其它用户余额的高敏感操作，
    # 走统一 audit 入口让事故时易于聚合查询。仅 cross-user 时记录。
    if actor_id != target_user_id:
        audit_permission_change(
            actor_user_id=actor_id,
            action="economy.coins.add",
            target=str(target_user_id),
            before={"coins": before_coins},
            after={"coins": coins},
            context={
                "requested": amount,
                "applied": applied_amount,
                "name": user_name,
            },
        )
    success_lines = [
        f"{EMOJI_USER} 用户：{user_name}（{target_user_id}）",
        f"{EMOJI_COIN} 数量：+{applied_amount}",
        f"{EMOJI_COIN} 当前金币：{coins}",
    ]
    if capped and applied_amount < amount:
        success_lines.append(
            f"⚠️ 已触账户上限，{amount - applied_amount} 金币未入账",
        )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("添加"),
            success_lines,
        ),
    )


@remove_coins_matcher.handle()
@command_control(
    command_key="economy.coins.remove",
    display_name="扣除金币",
    permission="economy.coins.remove",
    description="为指定用户扣减金币",
    usage="扣除金币 <用户 QQ/@用户/用户名称> <金币数量>",
    category="经济系统",
)
@require_permission("economy.coins.remove")
async def handle_remove_coins(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "扣除金币")
    if len(args) != 2:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "扣除金币",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("扣除", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("扣除", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("扣除", "用户参数解析失败"))
        return

    amount = _parse_positive_int(args[1])
    if amount is None:
        await bot.send(event, at + " " + reply_failure("扣除", "数量必须为正整数"))
        return
    if _exceeds_max_amount(amount):
        await bot.send(
            event,
            at + " " + reply_failure("扣除", f"数量过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("扣除", "用户不存在"))
            return

        # 原子条件 UPDATE：避免并发 lost-update；rowcount=0 则金币不足。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == target_user_id, User.coins >= amount)
            .values(coins=User.coins - amount),
        )
        if rowcount == 0:
            coins_now = int(
                session.query(User.coins).filter(User.user_id == target_user_id).scalar() or 0
            )
            await bot.send(
                event,
                at + " " + reply_failure("扣除", f"金币不足，当前仅有 {coins_now}"),
            )
            return
        session.commit()
        coins = int(
            session.query(User.coins).filter(User.user_id == target_user_id).scalar() or 0
        )
        user_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"扣除金币处理异常：user_id={target_user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("扣除", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    actor_id = event.get_user_id()
    before_coins = coins + amount
    logger.info(
        f"金币变更：actor={actor_id} target={target_user_id} action=economy.coins.remove "
        f"name={user_name} amount={amount} after={coins} reason=admin_remove"
    )
    # PC-6.1：admin 扣币是直接对其它用户余额的高敏感操作，
    # 走统一 audit 入口让事故时易于聚合查询。仅 cross-user 时记录。
    if actor_id != target_user_id:
        audit_permission_change(
            actor_user_id=actor_id,
            action="economy.coins.remove",
            target=str(target_user_id),
            before={"coins": before_coins},
            after={"coins": coins},
            context={
                "amount": amount,
                "name": user_name,
            },
        )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("扣除"),
            [
                f"{EMOJI_USER} 用户：{user_name}（{target_user_id}）",
                f"{EMOJI_COIN} 数量：-{amount}",
                f"{EMOJI_COIN} 当前金币：{coins}",
            ],
        ),
    )
