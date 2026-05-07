import random
from datetime import datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import MAX_COINS_AMOUNT
from nextbot.time_utils import db_now_utc_naive
from nextbot.text_utils import (
    EMOJI_COIN,
    EMOJI_FIRE,
    EMOJI_GAME,
    reply_block,
    reply_failure,
)

dice_matcher = on_command("掷骰子")

_cooldown_map: dict[str, datetime] = {}

_VALID_CHOICES = {"大", "小", "豹子"}


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
    at = OBV11MessageSegment.at(int(event.get_user_id()))

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

        net = payout - cost

        # 累加 payout + 统计字段，全部用条件 UPDATE
        if net > 0:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    coins=User.coins + payout,
                    dice_total_count=User.dice_total_count + 1,
                    dice_win_count=User.dice_win_count + 1,
                    dice_total_gain=User.dice_total_gain + net,
                )
            )
        elif net < 0:
            if payout > 0:
                session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(
                        coins=User.coins + payout,
                        dice_total_count=User.dice_total_count + 1,
                        dice_total_loss=User.dice_total_loss + abs(net),
                    )
                )
            else:
                session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(
                        dice_total_count=User.dice_total_count + 1,
                        dice_total_loss=User.dice_total_loss + abs(net),
                    )
                )
        else:
            # net == 0：押金原数加回
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    coins=User.coins + payout,
                    dice_total_count=User.dice_total_count + 1,
                )
            )
        session.commit()

        final_coins = int(
            session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"掷骰子处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("掷骰子", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    _cooldown_map[user_id] = now

    # 结果描述
    if is_triple:
        result_label = "豹子！"
        head_emoji = EMOJI_FIRE
    elif total >= 11:
        result_label = "大"
        head_emoji = EMOJI_GAME
    else:
        result_label = "小"
        head_emoji = EMOJI_GAME

    lines = [f"{head_emoji} 骰子：{d1} + {d2} + {d3} = {total}（{result_label}）"]

    if choice == "豹子" and not is_triple:
        lines.append(f"❌ 不是豹子！投入 {cost} 金币，全部损失")
    elif choice != "豹子" and is_triple:
        lines.append(f"❌ 豹子通杀！投入 {cost} 金币，全部损失")
    elif net > 0:
        lines.append(f"{EMOJI_COIN} 猜对了！投入 {cost}，获得 {payout}，净赚 {net} 金币")
    elif net == 0:
        lines.append(f"⚖️ 刚好持平！投入 {cost} 金币")
    else:
        lines.append(f"❌ 猜错了！投入 {cost} 金币，全部损失")

    lines.append(f"{EMOJI_COIN} 当前金币：{final_coins}")

    logger.info(
        f"掷骰子结果：user_id={user_id} choice={choice} dice={d1},{d2},{d3} total={total} "
        f"triple={is_triple} cost={cost} payout={payout} net={net}"
    )
    await bot.send(event, at + "\n" + reply_block(f"{EMOJI_GAME} 掷骰子", lines))
