import asyncio
import random
from datetime import datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import MAX_COINS_AMOUNT, add_coins_with_cap
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.time_utils import db_now_utc_naive
from nextbot.text_utils import (
    reply_failure,
    safe_at_segment_or_empty,
)
from server.screenshot import ScreenshotOptions
from server.web_server import create_dice_page

dice_matcher = on_command("掷骰子")

_cooldown_map: dict[str, datetime] = {}

_VALID_CHOICES = {"大", "小", "豹子"}

# 限制 dice 同时渲染数量，避免 Playwright 浏览器并发过高。
_dice_semaphore = asyncio.Semaphore(4)


def _safe_param_int(key: str, default: int, min_value: int = 0) -> int:
    try:
        value = int(get_current_param(key, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, value)


@dice_matcher.handle()
@command_control(
    command_key="economy.dice",
    display_name="掷骰子",
    permission="economy.dice",
    description="掷骰子小游戏，猜大小或豹子",
    usage="掷骰子 <大/小/豹子> <投入金币>",
    params={
        "min_cost": {
            "type": "int",
            "label": "最低投入",
            "description": "最低投入金币数",
            "required": False,
            "default": 10,
            "min": 1,
        },
        "max_cost": {
            "type": "int",
            "label": "最高投入",
            "description": "最高投入金币数，0 表示不限制",
            "required": False,
            "default": 0,
            "min": 0,
        },
        "big_multiplier": {
            "type": "int",
            "label": "猜大倍率",
            "description": "猜大正确时的倍率",
            "required": False,
            "default": 2,
            "min": 1,
        },
        "small_multiplier": {
            "type": "int",
            "label": "猜小倍率",
            "description": "猜小正确时的倍率",
            "required": False,
            "default": 2,
            "min": 1,
        },
        "triple_multiplier": {
            "type": "int",
            "label": "豹子倍率",
            "description": "猜中豹子时的倍率",
            "required": False,
            "default": 10,
            "min": 1,
        },
        "cooldown_seconds": {
            "type": "int",
            "label": "冷却时间（秒）",
            "description": "两次掷骰子之间的最短间隔",
            "required": False,
            "default": 30,
            "min": 0,
        },
    },
    category="小游戏系统",
)
@require_permission("economy.dice")
async def handle_dice(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())

    args = parse_command_args_with_fallback(event, arg, "掷骰子")
    if len(args) != 2:
        raise_command_usage()

    choice = args[0].strip()
    if choice not in _VALID_CHOICES:
        await bot.send(event, at + " " + reply_failure("掷骰子", "请选择 大、小 或 豹子"))
        return

    try:
        cost = int(args[1])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("掷骰子", "投入金币必须为正整数"))
        return
    if cost <= 0:
        await bot.send(event, at + " " + reply_failure("掷骰子", "投入金币必须为正整数"))
        return

    min_cost = max(1, _safe_param_int("min_cost", 10, min_value=1))
    max_cost = _safe_param_int("max_cost", 0, min_value=0)
    if cost < min_cost:
        await bot.send(event, at + " " + reply_failure("掷骰子", f"最低投入 {min_cost} 金币"))
        return
    if max_cost > 0 and cost > max_cost:
        await bot.send(event, at + " " + reply_failure("掷骰子", f"最高投入 {max_cost} 金币"))
        return
    if cost > MAX_COINS_AMOUNT:
        await bot.send(
            event,
            at + " " + reply_failure("掷骰子", f"数量过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    user_id = event.get_user_id()

    # 冷却检查
    cooldown_seconds = _safe_param_int("cooldown_seconds", 30, min_value=0)
    now = db_now_utc_naive()
    if cooldown_seconds > 0:
        last_time = _cooldown_map.get(user_id)
        if last_time is not None:
            elapsed = now - last_time
            if elapsed < timedelta(seconds=cooldown_seconds):
                remaining = timedelta(seconds=cooldown_seconds) - elapsed
                remaining_s = int(remaining.total_seconds())
                await bot.send(event, at + " " + reply_failure("掷骰子", f"冷却中，还需等待 {remaining_s} 秒"))
                return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("掷骰子", "请先注册账号"))
            return

        # session.close() 后 ORM 属性不可访问；在事务内 cache 出局部变量给
        # 后续渲染用，避免 detached instance 上读 .name。
        user_name = str(user.name)

        # 原子条件 UPDATE：扣押金。并发时第二条 rowcount=0 → 金币不足。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id, User.coins >= cost)
            .values(coins=User.coins - cost),
        )
        if rowcount == 0:
            coins_now = int(
                session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
            )
            await bot.send(event, at + " " + reply_failure("掷骰子", f"金币不足（当前 {coins_now}）"))
            return

        # 掷骰子
        d1 = random.randint(1, 6)
        d2 = random.randint(1, 6)
        d3 = random.randint(1, 6)
        total = d1 + d2 + d3
        is_triple = d1 == d2 == d3

        # 判定结果
        big_multiplier = max(1, _safe_param_int("big_multiplier", 2, min_value=1))
        small_multiplier = max(1, _safe_param_int("small_multiplier", 2, min_value=1))
        triple_multiplier = max(1, _safe_param_int("triple_multiplier", 10, min_value=1))

        payout = 0
        if choice == "豹子":
            if is_triple:
                payout = cost * triple_multiplier
        elif choice == "大":
            if not is_triple and total >= 11:
                payout = cost * big_multiplier
        elif choice == "小":
            if not is_triple and total <= 10:
                payout = cost * small_multiplier

        # PC-8.1：payout 加币走 add_coins_with_cap，受 SF-X.1 全局账户上限保护，
        # 与 economy / red_packet / warehouse / lottery 等域对称。统计字段拆出独立 UPDATE。
        applied_payout = 0
        capped = False
        if payout > 0:
            applied_payout, capped = add_coins_with_cap(session, user_id, payout)
            if capped:
                logger.warning(
                    f"掷骰子派奖触顶 cap：user_id={user_id} requested={payout} applied={applied_payout}"
                )

        # R4R-7.1：win / loss / tie 的分支判定仍按理论 net（用户行为真实结果），
        # 但 stats 累计值用 applied_net（实际入账，与 user.coins 真实变化对账）。
        # cap 触顶导致 applied_net <= 0 时仍归类为"赢"分支，避免 win_count 漏计。
        net = payout - cost
        applied_net = applied_payout - cost

        if net > 0:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    dice_total_count=User.dice_total_count + 1,
                    dice_win_count=User.dice_win_count + 1,
                    dice_total_gain=User.dice_total_gain + max(0, applied_net),
                )
            )
        elif net < 0:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    dice_total_count=User.dice_total_count + 1,
                    dice_total_loss=User.dice_total_loss + abs(applied_net),
                )
            )
        else:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    dice_total_count=User.dice_total_count + 1,
                )
            )
        session.commit()

        final_coins = int(
            session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback，避免依赖 session.close()
        # 隐式 rollback；与 user_manager IntegrityError 分支风格统一。
        session.rollback()
        logger.exception(f"掷骰子处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("掷骰子", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    _cooldown_map[user_id] = now

    # result_kind 分类：与模板 5 种 result band 状态对齐
    # - triple_win：猜豹子 + 摇到豹子
    # - lose（猜豹子未中）：猜豹子但非豹子
    # - triple_kill：猜大/小但摇到豹子（被豹子通杀）
    # - win / tie / lose：常规分支按 net 判定
    if choice == "豹子" and is_triple:
        result_kind = "triple_win"
    elif choice == "豹子" and not is_triple:
        result_kind = "lose"
    elif choice != "豹子" and is_triple:
        result_kind = "triple_kill"
    elif net > 0:
        result_kind = "win"
    elif net == 0:
        result_kind = "tie"
    else:
        result_kind = "lose"

    logger.info(
        f"掷骰子结果：user_id={user_id} choice={choice} dice={d1},{d2},{d3} total={total} "
        f"triple={is_triple} cost={cost} payout={payout} applied_payout={applied_payout} "
        f"capped={capped} net={net} result_kind={result_kind}"
    )

    page_url = create_dice_page(
        player_name=user_name,
        player_qq=user_id,
        choice=choice,
        cost=cost,
        dice=(d1, d2, d3),
        total=total,
        is_triple=is_triple,
        result_kind=result_kind,
        payout=payout,
        applied_payout=applied_payout,
        net=net,
        applied_net=applied_net,
        final_coins=final_coins,
        capped=capped,
    )
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=ScreenshotOptions(
            viewport_width=720,
            viewport_height=720,
            fit_content_height=True,
        ),
        file_prefix="dice",
        semaphore=_dice_semaphore,
        failure_action="掷骰子",
        at_user_id=user_id,
    )
