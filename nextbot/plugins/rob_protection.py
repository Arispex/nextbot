from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import MAX_COINS_AMOUNT
from nextbot.text_utils import reply_block, reply_failure, reply_success, safe_at_segment_or_empty

rob_protection_matcher = on_command("切换抢劫保护")


_ENABLE_TOKENS = {"开启", "开", "on"}
_DISABLE_TOKENS = {"关闭", "关", "off"}


def _safe_param_int(key: str, default: int, min_value: int = 0) -> int:
    try:
        value = int(get_current_param(key, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, value)


@rob_protection_matcher.handle()
@command_control(
    command_key="economy.rob_protection",
    display_name="切换抢劫保护",
    permission="economy.rob_protection",
    description="切换抢劫保护状态，开启后既不能抢劫他人，也不会被他人抢劫",
    usage="切换抢劫保护 <开启/关闭>",
    params={
        "toggle_cost": {
            "type": "int",
            "label": "切换花费金币",
            "description": "每次切换抢劫保护状态花费的金币数量",
            "required": False,
            "default": 200,
            "min": 0,
        },
    },
    category="小游戏系统",
)
@require_permission("economy.rob_protection")
async def handle_toggle_rob_protection(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())

    args = parse_command_args_with_fallback(event, arg, "切换抢劫保护")
    if len(args) != 1:
        raise_command_usage()

    token = args[0].strip().lower()
    if token in _ENABLE_TOKENS:
        target = True
        state_label = "开启"
    elif token in _DISABLE_TOKENS:
        target = False
        state_label = "关闭"
    else:
        raise_command_usage()

    cost = _safe_param_int("toggle_cost", 200, min_value=0)
    if cost > MAX_COINS_AMOUNT:
        await bot.send(
            event,
            at + " " + reply_failure("切换抢劫保护", f"数量过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    user_id = event.get_user_id()
    session = get_session()
    try:
        # MI-4.2：capture-before 策略——把 name + 原始 coins 一次性读出来，
        # 后续直接用 original_coins - cost 显示，省两条 commit 后 SELECT。
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("切换抢劫保护", "请先注册账号"))
            return
        original_name = str(user.name or "")
        original_coins = int(user.coins or 0)

        # 原子条件 UPDATE：互斥旧状态 + 余额校验 + 扣费 + 切换。
        # 并发时第二条 rowcount=0，由 SQL 层兜底。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(
                User.user_id == user_id,
                User.coins >= cost,
                User.rob_protected.is_(not target),
            )
            .values(
                coins=User.coins - cost,
                rob_protected=target,
            ),
        )
        if rowcount == 0:
            # 拉最新状态判定具体原因，保持原有错误文案
            latest = session.query(User).filter(User.user_id == user_id).first()
            if latest is None:
                await bot.send(event, at + " " + reply_failure("切换抢劫保护", "请先注册账号"))
                return
            if bool(latest.rob_protected) == target:
                await bot.send(event, at + " " + reply_failure("切换抢劫保护", "已处于该状态"))
                return
            latest_coins = int(latest.coins or 0)
            await bot.send(
                event,
                at + " " + reply_failure(
                    "切换抢劫保护", f"金币不足，需 {cost}，当前 {latest_coins}"
                ),
            )
            return
        session.commit()

        # MI-4.2：直接基于 capture-before 计算，避免再次 SELECT
        current_coins = original_coins - cost
        name = original_name
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"切换抢劫保护处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("切换抢劫保护", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    logger.info(
        f"切换抢劫保护：user={name}({user_id}) state={'on' if target else 'off'} "
        f"cost={cost} coins={current_coins}"
    )

    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("切换抢劫保护"),
            [
                f"🛡 抢劫保护：{state_label}",
                f"💰 消耗金币：{cost}",
                f"💰 当前金币：{current_coins}",
            ],
        ),
    )
