from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission

list_matcher = on_command("服务器列表")


@list_matcher.handle()
@command_control(
    command_key="server.list",
    display_name="服务器列表",
    permission="server.list",
    description="输出服务器列表",
    usage="服务器列表",
    category="服务器管理",
)
@require_permission("server.list")
async def handle_list_servers(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "服务器列表")
    if args:
        raise_command_usage()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        await bot.send(event, "ℹ️ 暂无服务器")
        return

    lines: list[str] = []
    for server in servers:
        lines.append(f"{server.id}.{server.name}")
        lines.append(f"IP：{server.ip}")
        lines.append(f"端口：{server.game_port}")
        lines.append("")

    message = "🖥️ 服务器列表\n" + "\n".join(lines).rstrip()
    logger.info(f"输出服务器列表，共 {len(servers)} 条")
    await bot.send(event, message)
