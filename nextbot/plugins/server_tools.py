import base64
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent as OBV11GroupMessageEvent
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, get_session
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    parse_command_text_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import EMOJI_SERVER, reply_block, reply_failure, reply_success


execute_matcher = on_command("执行")
map_image_matcher = on_command("全亮地图")
download_map_matcher = on_command("下载地图")


def _parse_execute_arg_text(text: str) -> tuple[int, str] | None:
    if not text:
        return None

    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None

    server_id_text, command = parts
    try:
        server_id = int(server_id_text)
    except ValueError:
        return None

    command_text = command.strip()
    if not command_text:
        return None
    return server_id, command_text


def _extract_response_text(payload: dict[str, object]) -> str:
    value = payload.get("response")
    if isinstance(value, list):
        lines = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(lines)
    if isinstance(value, str):
        return value.strip()
    return ""


@execute_matcher.handle()
@command_control(
    command_key="server_tools.execute",
    display_name="执行",
    permission="server_tools.execute",
    description="在指定服务器执行指令",
    usage="执行 <服务器 ID> <TShock 命令>",
    category="服务器工具",
)
@require_permission("server_tools.execute")
async def handle_execute(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    text = parse_command_text_with_fallback(event, arg, "执行")
    parsed = _parse_execute_arg_text(text)
    if parsed is None:
        raise_command_usage()

    target_id, command = parsed
    at = OBV11MessageSegment.at(int(event.get_user_id()))
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == target_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("执行", "服务器不存在"))
        return

    try:
        response = await request_server_api(
            server,
            "/v3/server/rawcmd",
            params={"cmd": command},
        )
    except TShockRequestError:
        await bot.send(event, at + " " + reply_failure("执行", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, at + " " + reply_failure("执行", f"{get_error_reason(response)}"))
        return

    result_text = _extract_response_text(response.payload)
    if result_text:
        await bot.send(
            event,
            at + "\n" + reply_block(
                reply_success("执行"),
                [
                    f"{EMOJI_SERVER} 服务器：{server.id}.{server.name}",
                    f"⚙️ 命令：{command}",
                    f"📋 返回内容：\n{result_text}",
                ],
            ),
        )
        return

    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("执行"),
            [
                f"{EMOJI_SERVER} 服务器：{server.id}.{server.name}",
                f"⚙️ 命令：{command}",
                "ℹ️ 无返回内容",
            ],
        ),
    )


@map_image_matcher.handle()
@command_control(
    command_key="server_tools.map_image",
    display_name="全亮地图",
    permission="server_tools.map_image",
    description="生成当前世界地图图片",
    usage="全亮地图 <服务器 ID>",
    category="服务器工具",
)
@require_permission("server_tools.map_image")
async def handle_map_image(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "全亮地图")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    try:
        response = await request_server_api(
            server,
            "/nextbot/world/map-image",
            timeout=60.0,
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    b64 = response.payload.get("base64")
    if not isinstance(b64, str) or not b64:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    logger.info(f"世界地图获取成功：server_id={server.id}")
    if bot.adapter.get_name() == "OneBot V11":
        await bot.send(event, OBV11MessageSegment.image(file=f"base64://{b64}"))
        return
    await bot.send(event, "ℹ️ 地图数据已获取，文件名：" + str(response.payload.get('fileName', '')))


@download_map_matcher.handle()
@command_control(
    command_key="server_tools.download_map",
    display_name="下载地图",
    permission="server_tools.download_map",
    description="下载当前世界的 .wld 文件",
    usage="下载地图 <服务器 ID>",
    category="服务器工具",
)
@require_permission("server_tools.download_map")
async def handle_download_map(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "下载地图")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("下载", "服务器不存在"))
        return

    try:
        response = await request_server_api(
            server,
            "/nextbot/world/world-file",
            timeout=60.0,
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("下载", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("下载", f"{get_error_reason(response)}"))
        return

    b64 = response.payload.get("base64")
    file_name = response.payload.get("fileName") or "world.wld"
    if not isinstance(b64, str) or not b64:
        await bot.send(event, reply_failure("下载", "返回数据格式错误"))
        return

    logger.info(f"世界文件下载成功：server_id={server.id} file={file_name}")
    if bot.adapter.get_name() == "OneBot V11":
        file_uri = f"base64://{b64}"
        if isinstance(event, OBV11GroupMessageEvent):
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=file_uri,
                name=file_name,
            )
        else:
            await bot.call_api(
                "upload_private_file",
                user_id=event.get_user_id(),
                file=file_uri,
                name=file_name,
            )
        return
    file_data = base64.b64decode(b64)
    file_path = Path("/tmp") / file_name
    file_path.write_bytes(file_data)
    await bot.send(event, f"✅ 下载成功，文件已保存：{file_path}")
