from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import User, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.time_utils import beijing_today_text

sign_matcher = on_command("签到")
transfer_matcher = on_command("转账")


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
            "default": 30,
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
            "default": 5,
            "min": 0,
        },
        "max_streak_bonus": {
            "type": "int",
            "label": "连续签到最大奖励",
            "description": "连续签到奖励超过该值时会被限制到该值",
            "required": False,
            "default": 50,
            "min": 0,
        },
    },
)
@require_permission("economy.sign")
async def handle_sign(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "签到")
    if args:
        raise_command_usage()

    min_coins = int(get_current_param("min_coins", 10))
    max_coins = int(get_current_param("max_coins", 30))
    enable_streak = bool(get_current_param("enable_streak", True))
    streak_bonus_per_day = int(get_current_param("streak_bonus_per_day", 5))
    max_streak_bonus = int(get_current_param("max_streak_bonus", 50))

    if min_coins < 0 or max_coins < 0 or streak_bonus_per_day < 0 or max_streak_bonus < 0:
        await bot.send(event, "签到失败，签到奖励配置不能为负数")
        return
    if min_coins > max_coins:
        await bot.send(event, "签到失败，签到奖励配置错误：最小值不能大于最大值")
        return

    user_id = event.get_user_id()
    today_text = _today_text()
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, "签到失败，请先注册账号")
            return

        last_sign_date = str(user.last_sign_date or "").strip()
        if bool(user.signed_today) or last_sign_date == today_text:
            await bot.send(event, "签到失败，今天已经签到过了")
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

        user.coins = int(user.coins or 0) + total_reward
        user.signed_today = True
        user.last_sign_date = today_text
        user.sign_streak = streak_result.next_streak
        session.commit()

        logger.info(
            "签到成功："
            f"user_id={user.user_id} name={user.name} base_reward={base_reward} "
            f"streak_reward={streak_result.streak_reward} total_reward={total_reward} "
            f"streak={streak_result.next_streak} coins={user.coins}"
        )
        lines = [
            "签到成功",
            f"获得金币：{base_reward}",
            f"连续签到：{streak_result.next_streak} 天",
        ]
        if enable_streak:
            lines.append(f"连续签到奖励：{streak_result.streak_reward}")
        else:
            lines.append("连续签到奖励：未开启")
        lines.extend(
            [
                f"本次总获得：{total_reward}",
                f"当前金币：{user.coins}",
            ]
        )
        await bot.send(event, "\n".join(lines))
    finally:
        session.close()


@transfer_matcher.handle()
@command_control(
    command_key="economy.transfer",
    display_name="转账",
    permission="economy.transfer",
    description="向其他用户转账金币",
    usage="转账 <用户 ID/@用户/用户名称> <数量>",
)
@require_permission("economy.transfer")
async def handle_transfer(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "转账")
    if len(args) != 2:
        raise_command_usage()

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "转账", arg_index=0
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, "转账失败，用户名称不存在")
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, "转账失败，用户名称不唯一，请使用用户 ID 或 @用户")
        return
    if target_user_id is None:
        await bot.send(event, "转账失败，用户参数解析失败")
        return

    amount_str = args[1].strip()
    try:
        amount = int(amount_str)
    except ValueError:
        await bot.send(event, "转账失败，数量必须为整数")
        return
    if amount <= 0:
        await bot.send(event, "转账失败，数量必须大于 0")
        return

    sender_id = event.get_user_id()
    if sender_id == target_user_id:
        await bot.send(event, "转账失败，不能转账给自己")
        return

    session = get_session()
    try:
        sender = session.query(User).filter(User.user_id == sender_id).first()
        if sender is None:
            await bot.send(event, "转账失败，请先注册账号")
            return

        target = session.query(User).filter(User.user_id == target_user_id).first()
        if target is None:
            await bot.send(event, "转账失败，目标用户不存在")
            return

        sender_coins = int(sender.coins or 0)
        if sender_coins < amount:
            await bot.send(event, f"转账失败，金币不足（当前：{sender_coins}）")
            return

        sender.coins = sender_coins - amount
        target.coins = int(target.coins or 0) + amount
        session.commit()

        logger.info(
            f"转账成功：sender_id={sender.user_id} sender_name={sender.name} "
            f"target_id={target.user_id} target_name={target.name} "
            f"amount={amount} sender_remaining={sender.coins}"
        )
        await bot.send(
            event,
            f"转账成功\n转出金币：{amount}\n转账对象：{target.name}（{target.user_id}）\n当前余额：{sender.coins}",
        )
    finally:
        session.close()
