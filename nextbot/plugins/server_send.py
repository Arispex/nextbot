import re

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, User, get_session
from nextbot.message_parser import parse_command_text_with_fallback
from nextbot.permissions import require_permission
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import (
    EMOJI_SERVER,
    at_prefix,
    reply_block,
    reply_failure,
    reply_success,
)


send_matcher = on_command("发送")

_WHITESPACE_RE = re.compile(r"\s+")
# ST-4.2：游戏内 /say 单条长度上限，防止 owner / 攻击者刷屏 + 触发 TShock 截断
_MAX_CONTENT_LENGTH = 200


def _parse_send_arg_text(text: str) -> tuple[int, str] | None:
    if not text:
        return None
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None
    server_id_text, content = parts
    try:
        server_id = int(server_id_text)
    except ValueError:
        return None
    # ST-5.5：拒绝 server_id <= 0
    if server_id <= 0:
        return None
    normalized = _WHITESPACE_RE.sub(" ", content).strip()
    if not normalized:
        return None
    return server_id, normalized


@send_matcher.handle()
@command_control(
    command_key="server.send",
    display_name="发送",
    permission="server.send",
    description="在指定服务器的游戏内广播一条 QQ 消息",
    usage="发送 <服务器 ID> <消息内容>",
    category="服务器工具",
)
@require_permission("server.send")
async def handle_send(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    text = parse_command_text_with_fallback(event, arg, "发送")
    parsed = _parse_send_arg_text(text)
    if parsed is None:
        raise_command_usage()

    target_id, content = parsed

    # ST-4.2：长度上限（whitespace-collapse 后再判断），超过即拒绝
    if len(content) > _MAX_CONTENT_LENGTH:
        await bot.send(event, at_prefix(event, reply_failure("发送", "内容过长")))
        return

    user_id = event.get_user_id()

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        server = session.query(Server).filter(Server.id == target_id).first()
    finally:
        session.close()

    if user is None:
        await bot.send(event, at_prefix(event, reply_failure("发送", "请先注册账号")))
        return
    if server is None:
        await bot.send(event, at_prefix(event, reply_failure("发送", "服务器不存在")))
        return

    raw_cmd = f"/say {user.name}（{user_id}）：{content}"
    logger.info(
        f"QQ 消息转发到服务器：server_id={target_id} user_id={user_id} "
        f"name={user.name} content_preview={content[:40]}"
    )

    try:
        # ST-4.3：把 read 超时拉到 10s，缓解 TShock 主线程被其它命令短暂阻塞时的误报
        response = await request_server_api(
            server,
            "/v3/server/rawcmd",
            params={"cmd": raw_cmd},
            timeout=10.0,
        )
    except TShockRequestError:
        await bot.send(event, at_prefix(event, reply_failure("发送", "无法连接服务器")))
        return

    if not is_success(response):
        # ST-4.5：去掉多余 f-string
        await bot.send(event, at_prefix(event, reply_failure("发送", get_error_reason(response))))
        return

    await bot.send(
        event,
        at_prefix(
            event,
            reply_block(
                reply_success("发送"),
                [
                    f"{EMOJI_SERVER} 服务器：{server.id}.{server.name}",
                    f"💬 内容：{content}",
                ],
            ),
            sep="\n",
        ),
    )
