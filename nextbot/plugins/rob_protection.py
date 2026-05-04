from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import User, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.text_utils import reply_block, reply_failure, reply_success

rob_protection_matcher = on_command("切换抢劫保护")


_ENABLE_TOKENS = {"开启", "开", "on"}
_DISABLE_TOKENS = {"关闭", "关", "off"}


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
    at = OBV11MessageSegment.at(int(event.get_user_id()))

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

    cost = max(0, int(get_current_param("toggle_cost", 200)))

    user_id = event.get_user_id()
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("切换抢劫保护", "请先注册账号"))
            return

        if bool(user.rob_protected) == target:
            await bot.send(event, at + " " + reply_failure("切换抢劫保护", "已处于该状态"))
            return

        coins = int(user.coins or 0)
        if coins < cost:
            await bot.send(
                event,
                at + " " + reply_failure(
                    "切换抢劫保护", f"金币不足，需 {cost}，当前 {coins}"
                ),
            )
            return

        user.coins = coins - cost
        user.rob_protected = target
        session.commit()

        name = str(user.name)
        current_coins = int(user.coins)
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
