from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import User, UserSignRecord, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_COIN,
    EMOJI_FIRE,
    EMOJI_USER,
    reply_block,
    reply_failure,
    reply_success,
)
from nextbot.time_utils import beijing_today_text

sign_matcher = on_command("签到")
transfer_matcher = on_command("转账")
add_coins_matcher = on_command("添加金币")
remove_coins_matcher = on_command("扣除金币")

MAX_COINS_AMOUNT = 100_000_000


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
    },
    category="经济系统",
)
@require_permission("economy.sign")
async def handle_sign(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "签到")
    if args:
        raise_command_usage()

    at = OBV11MessageSegment.at(int(event.get_user_id()))
    min_coins = int(get_current_param("min_coins", 10))
    max_coins = int(get_current_param("max_coins", 30))
    enable_streak = bool(get_current_param("enable_streak", True))
    streak_bonus_per_day = int(get_current_param("streak_bonus_per_day", 5))
    max_streak_bonus = int(get_current_param("max_streak_bonus", 50))

    if min_coins < 0 or max_coins < 0 or streak_bonus_per_day < 0 or max_streak_bonus < 0:
        await bot.send(event, at + " " + reply_failure("签到", "签到奖励配置不能为负数"))
        return
    if min_coins > max_coins:
        await bot.send(event, at + " " + reply_failure("签到", "签到奖励配置错误：最小值不能大于最大值"))
        return

    user_id = event.get_user_id()
    today_text = _today_text()
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
        streak_result = _resolve_streak_reward(
            last_sign_date=last_sign_date,
            current_streak=int(user.sign_streak or 0),
            enable_streak=enable_streak,
            streak_bonus_per_day=streak_bonus_per_day,
            max_streak_bonus=max_streak_bonus,
            today_text=today_text,
        )
        total_reward = base_reward + streak_result.streak_reward

        # 原子条件 UPDATE：仅当 last_sign_date != today 时才写入。
        # 并发同时签到时，第二条 rowcount=0，被 schema/SQL 层拦下。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id, User.last_sign_date != today_text)
            .values(
                coins=User.coins + total_reward,
                last_sign_date=today_text,
                sign_streak=streak_result.next_streak,
                sign_total=User.sign_total + 1,
            ),
        )
        if rowcount == 0:
            await bot.send(event, at + " " + reply_failure("签到", "今天已经签到过了"))
            return

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
        user_name = str(
            session.query(User.name).filter(User.user_id == user_id).scalar() or ""
        )

        logger.info(
            "签到成功："
            f"user_id={user_id} name={user_name} base_reward={base_reward} "
            f"streak_reward={streak_result.streak_reward} total_reward={total_reward} "
            f"streak={streak_result.next_streak} coins={coins_after} today_order={today_order}"
        )
        lines = [
            f"{EMOJI_CHART} 签到排名：第 {today_order} 位",
            f"{EMOJI_COIN} 基础奖励：{base_reward}",
            f"{EMOJI_FIRE} 连续签到：{streak_result.next_streak} 天",
        ]
        if enable_streak:
            lines.append(f"{EMOJI_COIN} 连续签到奖励：{streak_result.streak_reward}")
        else:
            lines.append(f"{EMOJI_COIN} 连续签到奖励：未开启")
        lines.extend(
            [
                f"{EMOJI_COIN} 本次总获得：{total_reward}",
                f"{EMOJI_COIN} 当前金币：{coins_after}",
            ]
        )
        await bot.send(
            event,
            at + "\n" + reply_block(
                reply_success("签到"),
                lines,
                hint="明日继续签到可获得连续奖励",
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"签到处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("签到", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()


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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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
        session.execute(
            update(User)
            .where(User.user_id == target_user_id)
            .values(coins=User.coins + amount)
        )
        session.commit()

        sender_after = int(
            session.query(User.coins).filter(User.user_id == sender_id).scalar() or 0
        )
        sender_name = str(
            session.query(User.name).filter(User.user_id == sender_id).scalar() or ""
        )
        target_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )

        logger.info(
            f"转账成功：sender_id={sender_id} sender_name={sender_name} "
            f"target_id={target_user_id} target_name={target_name} "
            f"amount={amount} sender_remaining={sender_after}"
        )
        await bot.send(
            event,
            at + "\n" + reply_block(
                reply_success("转账"),
                [
                    f"{EMOJI_COIN} 转出金币：{amount}",
                    f"{EMOJI_USER} 转账对象：{target_name}（{target_user_id}）",
                    f"{EMOJI_COIN} 当前余额：{sender_after}",
                ],
            ),
        )
    except Exception:  # noqa: BLE001
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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
            return

        # 原子条件 UPDATE：避免并发 lost-update。
        session.execute(
            update(User)
            .where(User.user_id == target_user_id)
            .values(coins=User.coins + amount)
        )
        session.commit()
        coins = int(
            session.query(User.coins).filter(User.user_id == target_user_id).scalar() or 0
        )
        user_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"添加金币处理异常：user_id={target_user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("添加", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    logger.info(
        f"添加金币成功：user_id={target_user_id} name={user_name} amount={amount} coins={coins}"
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("添加"),
            [
                f"{EMOJI_USER} 用户：{user_name}（{target_user_id}）",
                f"{EMOJI_COIN} 数量：+{amount}",
                f"{EMOJI_COIN} 当前金币：{coins}",
            ],
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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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
        logger.exception(f"扣除金币处理异常：user_id={target_user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("扣除", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    logger.info(
        f"扣除金币成功：user_id={target_user_id} name={user_name} amount={amount} coins={coins}"
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
