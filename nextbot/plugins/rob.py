import random
from datetime import datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.time_utils import db_now_utc_naive
from nextbot.text_utils import reply_failure

rob_matcher = on_command("抢劫")


@rob_matcher.handle()
@command_control(
    command_key="economy.rob",
    display_name="抢劫",
    permission="economy.rob",
    description="抢劫其他用户的金币",
    usage="抢劫 <用户 QQ/@用户/用户名称>",
    params={
        "cooldown_minutes": {
            "type": "int",
            "label": "冷却时间（分钟）",
            "description": "两次抢劫之间的最短间隔",
            "required": False,
            "default": 60,
            "min": 0,
        },
        "min_steal_percent": {
            "type": "int",
            "label": "最低抢夺百分比",
            "description": "成功时最少抢走对方金币的百分比",
            "required": False,
            "default": 5,
            "min": 1,
            "max": 100,
        },
        "max_steal_percent": {
            "type": "int",
            "label": "最高抢夺百分比",
            "description": "成功时最多抢走对方金币的百分比",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 100,
        },
        "crit_multiplier": {
            "type": "int",
            "label": "大成功倍率",
            "description": "大成功时抢夺金额的倍率",
            "required": False,
            "default": 2,
            "min": 1,
        },
        "fail_penalty_percent": {
            "type": "int",
            "label": "失败罚款百分比",
            "description": "普通失败时自己损失金币的百分比",
            "required": False,
            "default": 10,
            "min": 0,
            "max": 100,
        },
        "counter_steal_percent": {
            "type": "int",
            "label": "反被抢百分比",
            "description": "被反抢时自己损失金币的百分比",
            "required": False,
            "default": 10,
            "min": 0,
            "max": 100,
        },
        "police_penalty_percent": {
            "type": "int",
            "label": "警察罚款百分比",
            "description": "被警察抓获时罚款的金币百分比",
            "required": False,
            "default": 20,
            "min": 0,
            "max": 100,
        },
        "success_rate": {
            "type": "int",
            "label": "成功概率",
            "description": "抢劫成功的概率（百分比）",
            "required": False,
            "default": 50,
            "min": 0,
            "max": 100,
        },
        "crit_rate": {
            "type": "int",
            "label": "大成功概率",
            "description": "从成功中分出大成功的概率（百分比）",
            "required": False,
            "default": 10,
            "min": 0,
            "max": 100,
        },
        "counter_rate": {
            "type": "int",
            "label": "反被抢概率",
            "description": "被对方反抢的概率（百分比）",
            "required": False,
            "default": 20,
            "min": 0,
            "max": 100,
        },
        "police_rate": {
            "type": "int",
            "label": "警察介入概率",
            "description": "被警察抓获的概率（百分比）",
            "required": False,
            "default": 10,
            "min": 0,
            "max": 100,
        },
        "min_coins_to_rob": {
            "type": "int",
            "label": "最低金币要求",
            "description": "发起抢劫时双方的最低金币要求",
            "required": False,
            "default": 1,
            "min": 1,
        },
    },
    category="小游戏系统",
)
@require_permission("economy.rob")
async def handle_rob(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = OBV11MessageSegment.at(int(event.get_user_id()))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "抢劫", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("抢劫", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("抢劫", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "抢劫")
    if len(args) != 1:
        raise_command_usage()

    robber_id = event.get_user_id()
    if robber_id == target_user_id:
        await bot.send(event, at + " " + reply_failure("抢劫", "不能抢劫自己"))
        return

    cooldown_minutes = max(0, int(get_current_param("cooldown_minutes", 60)))
    min_steal_percent = max(1, min(int(get_current_param("min_steal_percent", 5)), 100))
    max_steal_percent = max(min_steal_percent, min(int(get_current_param("max_steal_percent", 10)), 100))
    crit_multiplier = max(1, int(get_current_param("crit_multiplier", 2)))
    fail_penalty_percent = max(0, min(int(get_current_param("fail_penalty_percent", 10)), 100))
    counter_steal_percent = max(0, min(int(get_current_param("counter_steal_percent", 10)), 100))
    police_penalty_percent = max(0, min(int(get_current_param("police_penalty_percent", 20)), 100))
    success_rate = max(0, min(int(get_current_param("success_rate", 50)), 100))
    crit_rate = max(0, min(int(get_current_param("crit_rate", 10)), 100))
    counter_rate = max(0, min(int(get_current_param("counter_rate", 20)), 100))
    police_rate = max(0, min(int(get_current_param("police_rate", 10)), 100))
    min_coins_to_rob = max(1, int(get_current_param("min_coins_to_rob", 1)))

    session = get_session()
    try:
        robber = session.query(User).filter(User.user_id == robber_id).first()
        if robber is None:
            await bot.send(event, at + " " + reply_failure("抢劫", "请先注册账号"))
            return

        victim = session.query(User).filter(User.user_id == target_user_id).first()
        if victim is None:
            await bot.send(event, at + " " + reply_failure("抢劫", "对方未注册账号"))
            return

        # 冷却检查
        now = db_now_utc_naive()
        if cooldown_minutes > 0 and robber.last_rob_time is not None:
            elapsed = now - robber.last_rob_time
            if elapsed < timedelta(minutes=cooldown_minutes):
                remaining = timedelta(minutes=cooldown_minutes) - elapsed
                remaining_minutes = int(remaining.total_seconds() // 60)
                remaining_seconds = int(remaining.total_seconds() % 60)
                await bot.send(
                    event,
                    at + " " + reply_failure("抢劫", f"冷却中，还需等待 {remaining_minutes} 分 {remaining_seconds} 秒"),
                )
                return

        # 金币检查
        robber_coins = int(robber.coins or 0)
        victim_coins = int(victim.coins or 0)
        if victim_coins <= 0:
            await bot.send(event, at + " " + reply_failure("抢劫", "对方身无分文"))
            return
        if robber_coins <= 0:
            await bot.send(event, at + " " + reply_failure("抢劫", "你身无分文"))
            return
        if robber_coins < min_coins_to_rob:
            await bot.send(event, at + " " + reply_failure("抢劫", f"你的金币不足 {min_coins_to_rob}"))
            return
        if victim_coins < min_coins_to_rob:
            await bot.send(event, at + " " + reply_failure("抢劫", f"对方金币不足 {min_coins_to_rob}"))
            return

        # 抢劫保护检查
        if bool(robber.rob_protected):
            await bot.send(event, at + " " + reply_failure("抢劫", "你处于保护状态，先关闭抢劫保护才能抢劫"))
            return
        if bool(victim.rob_protected):
            await bot.send(event, at + " " + reply_failure("抢劫", "对方处于保护状态，无法抢劫"))
            return

        # 抽签决定结果
        roll = random.randint(1, 100)
        # 区间: [1, success_rate] 成功, (success_rate, success_rate+counter_rate] 反被抢,
        #        (success_rate+counter_rate, success_rate+counter_rate+police_rate] 警察, 剩余 普通失败
        result_type: str
        amount: int = 0

        if roll <= success_rate:
            # 成功，判断是否大成功
            steal_percent = random.randint(min_steal_percent, max_steal_percent)
            base_amount = max(1, victim_coins * steal_percent // 100)
            crit_roll = random.randint(1, 100)
            if crit_roll <= crit_rate:
                result_type = "crit"
                amount = base_amount * crit_multiplier
            else:
                result_type = "success"
                amount = base_amount
            # 不能超过对方实际金币
            amount = min(amount, victim_coins)

            robber.coins = robber_coins + amount
            victim.coins = victim_coins - amount
            robber.rob_total_count = int(robber.rob_total_count or 0) + 1
            robber.rob_success_count = int(robber.rob_success_count or 0) + 1
            robber.rob_total_gain = int(robber.rob_total_gain or 0) + amount
            victim.rob_total_loss = int(victim.rob_total_loss or 0) + amount

        elif roll <= success_rate + counter_rate:
            result_type = "counter"
            amount = max(1, robber_coins * counter_steal_percent // 100)
            robber.coins = robber_coins - amount
            victim.coins = victim_coins + amount
            robber.rob_total_count = int(robber.rob_total_count or 0) + 1
            robber.rob_total_penalty = int(robber.rob_total_penalty or 0) + amount
            victim.rob_total_gain = int(victim.rob_total_gain or 0) + amount

        elif roll <= success_rate + counter_rate + police_rate:
            result_type = "police"
            amount = max(1, robber_coins * police_penalty_percent // 100)
            robber.coins = robber_coins - amount
            robber.rob_total_count = int(robber.rob_total_count or 0) + 1
            robber.rob_total_penalty = int(robber.rob_total_penalty or 0) + amount

        else:
            result_type = "fail"
            amount = max(1, robber_coins * fail_penalty_percent // 100)
            robber.coins = robber_coins - amount
            robber.rob_total_count = int(robber.rob_total_count or 0) + 1
            robber.rob_total_penalty = int(robber.rob_total_penalty or 0) + amount

        robber.last_rob_time = now
        session.commit()

        robber_name = str(robber.name)
        victim_name = str(victim.name)
    finally:
        session.close()

    victim_display = f"{victim_name}（{target_user_id}）"
    messages = {
        "crit": f"🔥 大成功！你趁 {victim_display} 不注意，抢走了 💰 {amount} 金币",
        "success": f"✅ 抢劫成功，从 {victim_display} 手中抢走了 💰 {amount} 金币",
        "counter": f"🚫 {victim_display} 反应迅速，反而从你手中抢走了 💰 {amount} 金币",
        "police": f"🚨 你被巡逻的警察当场抓获，罚款 💰 {amount} 金币",
        "fail": f"❌ 你被 {victim_display} 发现了，慌忙逃跑时丢失了 💰 {amount} 金币",
    }

    logger.info(
        f"抢劫结果：robber={robber_name}({robber_id}) victim={victim_name}({target_user_id}) "
        f"result={result_type} amount={amount}"
    )
    await bot.send(event, at + " " + messages[result_type])
