import asyncio
import random
from datetime import timedelta

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import or_ as sa_or, update

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback, resolve_user_id_arg_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import add_coins_with_cap
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.time_utils import db_now_utc_naive, format_duration_seconds
from nextbot.text_utils import reply_failure, safe_at_segment_or_empty
from server.screenshot import ScreenshotOptions
from server.web_server import create_rob_page

rob_matcher = on_command("抢劫")

# 限制 rob 同时渲染数量，避免 Playwright 浏览器并发过高。
# 与 dice 一致放宽到 4：单玩家 60 分钟 cooldown 已严格限并发，
# 但群多人同时玩的峰值需更大缓冲。
_rob_semaphore = asyncio.Semaphore(4)


def _safe_param_int(key: str, default: int, min_value: int = 0) -> int:
    try:
        value = int(get_current_param(key, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, value)


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
            "label": "地牢守卫罚款百分比",
            "description": "被地牢守卫抓获时罚款的金币百分比",
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
            "label": "地牢守卫介入概率",
            "description": "被地牢守卫抓获的概率（百分比）",
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
    at = safe_at_segment_or_empty(event.get_user_id())
    robber_id = event.get_user_id()

    # M-3.3：自抢短路。如果第一个参数是数字 ID 且等于发起者，立即拒绝，
    # 避免先做一次按名字 lookup 的 SQL。按名字 / @ 的自抢仍由后面的 robber_id == target_user_id 兜底。
    early_args = parse_command_args_with_fallback(event, arg, "抢劫")
    if early_args and early_args[0].isdigit() and early_args[0] == robber_id:
        await bot.send(event, at + " " + reply_failure("抢劫", "不能抢劫自己"))
        return

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
    if target_user_id is None:
        # 兜底：上面 parse_error 分支理论上已覆盖所有 None 情况
        await bot.send(event, at + " " + reply_failure("抢劫", "用户参数解析失败"))
        return

    args = parse_command_args_with_fallback(event, arg, "抢劫")
    if len(args) != 1:
        raise_command_usage()

    if robber_id == target_user_id:
        await bot.send(event, at + " " + reply_failure("抢劫", "不能抢劫自己"))
        return

    cooldown_minutes = _safe_param_int("cooldown_minutes", 60, min_value=0)
    min_steal_percent = max(1, min(_safe_param_int("min_steal_percent", 5, min_value=1), 100))
    max_steal_percent = max(min_steal_percent, min(_safe_param_int("max_steal_percent", 10, min_value=1), 100))
    crit_multiplier = max(1, _safe_param_int("crit_multiplier", 2, min_value=1))
    fail_penalty_percent = max(0, min(_safe_param_int("fail_penalty_percent", 10, min_value=0), 100))
    counter_steal_percent = max(0, min(_safe_param_int("counter_steal_percent", 10, min_value=0), 100))
    police_penalty_percent = max(0, min(_safe_param_int("police_penalty_percent", 20, min_value=0), 100))
    success_rate = max(0, min(_safe_param_int("success_rate", 50, min_value=0), 100))
    crit_rate = max(0, min(_safe_param_int("crit_rate", 10, min_value=0), 100))
    counter_rate = max(0, min(_safe_param_int("counter_rate", 20, min_value=0), 100))
    police_rate = max(0, min(_safe_param_int("police_rate", 10, min_value=0), 100))
    min_coins_to_rob = max(1, _safe_param_int("min_coins_to_rob", 1, min_value=1))

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

        # 冷却检查（应用层提前给文案；真正强制冷却由下面 UPDATE WHERE 兜底）
        now = db_now_utc_naive()
        if cooldown_minutes > 0 and robber.last_rob_time is not None:
            elapsed = now - robber.last_rob_time
            if elapsed < timedelta(minutes=cooldown_minutes):
                remaining = timedelta(minutes=cooldown_minutes) - elapsed
                remaining_s = int(remaining.total_seconds())
                await bot.send(
                    event,
                    at + " " + reply_failure("抢劫", f"冷却中，还需等待 {format_duration_seconds(remaining_s)}"),
                )
                return

        # 金币检查（基于 stale 余额；并发钳制由后面的条件 UPDATE 兜底）
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
        result_type: str
        amount: int = 0

        # attacker 的冷却 / 保护条件，所有路径都必须校验
        cutoff = now - timedelta(minutes=cooldown_minutes) if cooldown_minutes > 0 else None
        attacker_cooldown_filter = (
            sa_or(User.last_rob_time.is_(None), User.last_rob_time < cutoff)
            if cutoff is not None
            else None
        )

        def attacker_where_clauses() -> list:
            clauses: list = [User.user_id == robber_id, User.rob_protected.is_(False)]
            if attacker_cooldown_filter is not None:
                clauses.append(attacker_cooldown_filter)
            return clauses

        applied_amount = 0
        capped = False
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
            # 不能超过对方实际金币（基于 stale，并发钳制由 UPDATE WHERE 兜底）
            amount = min(amount, victim_coins)

            # 1) 先扣 victim：要求 coins >= amount AND rob_protected = False
            v_rows = execute_rowcount(
                session,
                update(User)
                .where(
                    User.user_id == target_user_id,
                    User.coins >= amount,
                    User.rob_protected.is_(False),
                )
                .values(
                    coins=User.coins - amount,
                    rob_total_loss=User.rob_total_loss + amount,
                ),
            )
            if v_rows == 0:
                # victim 状态发生变更（金币不足 / 开启保护）
                # 不需要回滚（还没改任何东西），重新拉一个清晰文案
                latest_victim = session.query(User).filter(User.user_id == target_user_id).first()
                if latest_victim is not None and bool(latest_victim.rob_protected):
                    await bot.send(event, at + " " + reply_failure("抢劫", "对方处于保护状态，无法抢劫"))
                else:
                    await bot.send(event, at + " " + reply_failure("抢劫", "对方身无分文"))
                return

            # 2) attacker 冷却 / 保护守护 + 统计字段（不含 coins / rob_total_gain）
            #    coins 通过 helper 受 cap 保护；rob_total_gain 在 helper 之后用
            #    applied_amount 真实值入账（R5-2.1：cap-stats drift 家族第 4 处闭合）。
            #    先尝试条件 UPDATE，若 attacker 状态变更则回退 victim。
            a_rows = execute_rowcount(
                session,
                update(User)
                .where(*attacker_where_clauses())
                .values(
                    rob_total_count=User.rob_total_count + 1,
                    rob_success_count=User.rob_success_count + 1,
                    last_rob_time=now,
                ),
            )
            if a_rows == 0:
                # 回滚 victim 扣款（PC-8.1：refund 也走 helper 受 cap 保护）
                refund_applied, refund_capped = add_coins_with_cap(session, target_user_id, amount)
                # R5-2.2：rob_total_loss 用 refund_applied 真实值回撤，
                # 触顶时差额留作 victim 的经济沉淀（与 user.coins 实际增量一致）。
                session.execute(
                    update(User)
                    .where(User.user_id == target_user_id)
                    .values(
                        rob_total_loss=User.rob_total_loss - refund_applied,
                    )
                )
                if refund_capped and refund_applied < amount:
                    logger.warning(
                        f"抢劫回滚 victim refund 触顶 cap：victim={target_user_id} "
                        f"requested={amount} applied={refund_applied}"
                    )
                session.commit()
                await bot.send(event, at + " " + reply_failure("抢劫", "冷却中或保护状态变更，已取消"))
                return

            # PC-8.1：attacker 派金走 add_coins_with_cap，受 SF-X.1 全局账户上限保护。
            # 若 capped，差额视为经济沉淀（victim 已扣，attacker 仅按可加余量入账）。
            applied_amount, capped = add_coins_with_cap(session, robber_id, amount)
            if capped:
                logger.warning(
                    f"抢劫成功派金触顶 cap：robber={robber_id} requested={amount} applied={applied_amount}"
                )
            # R5-2.1：rob_total_gain 用 applied_amount 真实值累加，与 user.coins
            # delta 一致（dice/guess R4R-7.1 同模板）。触顶时差额为经济沉淀。
            session.execute(
                update(User)
                .where(User.user_id == robber_id)
                .values(
                    rob_total_gain=User.rob_total_gain + applied_amount,
                )
            )

        elif roll <= success_rate + counter_rate:
            result_type = "counter"
            amount = max(1, robber_coins * counter_steal_percent // 100)

            # 扣 attacker：同时校验金币足够 + 冷却 + 保护
            a_rows = execute_rowcount(
                session,
                update(User)
                .where(
                    *attacker_where_clauses(),
                    User.coins >= amount,
                )
                .values(
                    coins=User.coins - amount,
                    rob_total_count=User.rob_total_count + 1,
                    rob_total_penalty=User.rob_total_penalty + amount,
                    last_rob_time=now,
                ),
            )
            if a_rows == 0:
                # 并发：attacker 金币 / 冷却 / 保护状态变更，整次 counter 取消
                # 不能回退到 "钳制为 0"，因为后续 victim 还要 +amount，会凭空产生金币
                await bot.send(event, at + " " + reply_failure("抢劫", "金币不足以承担反被抢的损失，已取消"))
                return

            # PC-8.1：victim 派金走 add_coins_with_cap，受 SF-X.1 全局账户上限保护。
            # 若 capped，差额视为经济沉淀（attacker 已扣，victim 仅按可加余量入账）。
            applied_amount, capped = add_coins_with_cap(session, target_user_id, amount)
            if capped:
                logger.warning(
                    f"抢劫反抢 victim 派金触顶 cap：victim={target_user_id} "
                    f"requested={amount} applied={applied_amount}"
                )
            # R5-2.3：rob_total_gain 用 applied_amount 真实值累加，与 user.coins
            # delta 一致；触顶时差额为经济沉淀。
            session.execute(
                update(User)
                .where(User.user_id == target_user_id)
                .values(
                    rob_total_gain=User.rob_total_gain + applied_amount,
                )
            )

        elif roll <= success_rate + counter_rate + police_rate:
            result_type = "police"
            amount = max(1, robber_coins * police_penalty_percent // 100)

            a_rows = execute_rowcount(
                session,
                update(User)
                .where(
                    *attacker_where_clauses(),
                    User.coins >= amount,
                )
                .values(
                    coins=User.coins - amount,
                    rob_total_count=User.rob_total_count + 1,
                    rob_total_penalty=User.rob_total_penalty + amount,
                    last_rob_time=now,
                ),
            )
            if a_rows == 0:
                a_rows_fallback = execute_rowcount(
                    session,
                    update(User)
                    .where(
                        *attacker_where_clauses(),
                        User.coins > 0,
                    )
                    .values(
                        coins=0,
                        rob_total_count=User.rob_total_count + 1,
                        rob_total_penalty=User.rob_total_penalty + User.coins,
                        last_rob_time=now,
                    ),
                )
                if a_rows_fallback == 0:
                    await bot.send(event, at + " " + reply_failure("抢劫", "冷却中或保护状态变更，已取消"))
                    return

        else:
            result_type = "fail"
            amount = max(1, robber_coins * fail_penalty_percent // 100)

            a_rows = execute_rowcount(
                session,
                update(User)
                .where(
                    *attacker_where_clauses(),
                    User.coins >= amount,
                )
                .values(
                    coins=User.coins - amount,
                    rob_total_count=User.rob_total_count + 1,
                    rob_total_penalty=User.rob_total_penalty + amount,
                    last_rob_time=now,
                ),
            )
            if a_rows == 0:
                a_rows_fallback = execute_rowcount(
                    session,
                    update(User)
                    .where(
                        *attacker_where_clauses(),
                        User.coins > 0,
                    )
                    .values(
                        coins=0,
                        rob_total_count=User.rob_total_count + 1,
                        rob_total_penalty=User.rob_total_penalty + User.coins,
                        last_rob_time=now,
                    ),
                )
                if a_rows_fallback == 0:
                    await bot.send(event, at + " " + reply_failure("抢劫", "冷却中或保护状态变更，已取消"))
                    return

        session.commit()

        # session.close() 后 ORM 属性不可访问；在事务内 cache 出局部变量给后续渲染用，
        # 避免 detached instance 上读 .name / .coins。
        robber_name = str(
            session.query(User.name).filter(User.user_id == robber_id).scalar() or ""
        )
        victim_name = str(
            session.query(User.name).filter(User.user_id == target_user_id).scalar() or ""
        )
        robber_final_coins = int(
            session.query(User.coins).filter(User.user_id == robber_id).scalar() or 0
        )
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"抢劫处理异常：robber_id={robber_id} target_id={target_user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("抢劫", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    # PC-8.1：success / crit / counter 路径中的 "抢走了 X 金币" 显示 applied_amount
    # （实际入账），与 dice / guess_number / red_packet 一致；触顶时另起一行展示
    # 理论数额。police / fail 路径没有派金 helper（attacker 直接扣，无 cap），
    # 仍用原 amount。
    applied_for_render = applied_amount if result_type in ("crit", "success", "counter") else amount

    result_labels = {
        "crit": "大成功",
        "success": "抢劫成功",
        "counter": "反被抢",
        "police": "地牢守卫介入",
        "fail": "失败",
    }
    if result_type in ("crit", "success") and capped and applied_amount < amount:
        cap_subject = "robber"
    elif result_type == "counter" and capped and applied_amount < amount:
        cap_subject = "victim"
    else:
        cap_subject = "none"

    logger.info(
        f"抢劫结果：robber={robber_name}({robber_id}) victim={victim_name}({target_user_id}) "
        f"result={result_type} amount={amount} applied_amount={applied_amount} capped={capped}"
    )

    page_url = create_rob_page(
        robber_name=robber_name,
        robber_qq=robber_id,
        victim_name=victim_name,
        victim_qq=target_user_id,
        result_kind=result_type,
        result_label=result_labels[result_type],
        amount=amount,
        applied_amount=applied_for_render,
        capped=capped,
        cap_subject=cap_subject,
        robber_final_coins=robber_final_coins,
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
        file_prefix="rob",
        semaphore=_rob_semaphore,
        failure_action="抢劫",
        at_user_id=robber_id,
    )
    if not ok:
        logger.warning(
            f"抢劫截图发送失败：robber={robber_id} victim={target_user_id} result={result_type}"
        )
