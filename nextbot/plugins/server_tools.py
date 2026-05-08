import asyncio
import base64
import re
import tempfile
from pathlib import Path

import httpx
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
from nextbot.text_utils import (
    EMOJI_SERVER,
    at_prefix,
    reply_block,
    reply_failure,
    reply_success,
)


execute_matcher = on_command("执行")
map_image_matcher = on_command("全亮地图")
download_map_matcher = on_command("下载地图")

# 大对象响应的硬上限，超过即拒绝（防止后端 bug / 攻击者控制后端塞数 GB base64 把进程打爆）
_MAX_BASE64_BYTES = 200 * 1024 * 1024
# 长 read 超时使用的 httpx Timeout 模板（地图渲染 / 世界文件下载可达数十秒）
_LONG_READ_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
# 文件名白名单：仅 ASCII 字母 / 数字 / `_` / `-` / `.`，长度 1-128
_SAFE_FILE_NAME_RE = re.compile(r"[\w\-.]{1,128}")
_SAFE_WLD_NAME_RE = re.compile(r"[\w\-.]{1,128}\.wld")

# Per-server 信号量，确保同一服务器同一时刻最多 1 个大对象请求在内存里。
# 不同 server_id 互不阻塞；不同 handler（map / download）也分开避免互相挤占。
_map_semaphores: dict[int, asyncio.Semaphore] = {}
_download_semaphores: dict[int, asyncio.Semaphore] = {}


def _semaphore_for(pool: dict[int, asyncio.Semaphore], server_id: int) -> asyncio.Semaphore:
    sem = pool.get(server_id)
    if sem is None:
        sem = asyncio.Semaphore(1)
        pool[server_id] = sem
    return sem


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

    # ST-5.5：拒绝 server_id <= 0，避免负数 / 0 通过类型校验后污染 DB 慢查询日志
    if server_id <= 0:
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


def _safe_wld_name(raw: object, server_id: int) -> str:
    """ST-3.1 / ST-3.5：把后端返回的 fileName 收敛到白名单内。

    Path(...).name 把任何路径剥离成 basename（屏蔽 ../etc/passwd、绝对路径），再做正则白名单。
    任何不匹配的输入直接回落 world-{server_id}.wld。
    """
    candidate = str(raw or "").strip()
    if not candidate:
        return f"world-{server_id}.wld"
    base = Path(candidate).name
    if not base or not _SAFE_WLD_NAME_RE.fullmatch(base):
        return f"world-{server_id}.wld"
    return base


def _safe_display_file_name(raw: object, server_id: int, fallback_suffix: str) -> str:
    """ST-2.4：非 V11 适配器展示 fileName 前先白名单清洗，避免后端字符串污染聊天显示。"""
    candidate = str(raw or "").strip()
    if not candidate:
        return f"map-{server_id}{fallback_suffix}"
    base = Path(candidate).name
    if not base or not _SAFE_FILE_NAME_RE.fullmatch(base):
        return f"map-{server_id}{fallback_suffix}"
    return base


def _resolve_group_id(event: Event) -> int:
    if isinstance(event, OBV11GroupMessageEvent):
        return int(event.group_id)
    return 0


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
    # ST-1.3：强制 / 前缀，避免 owner 笔误把非命令文本当成 say 广播或得到 "command not found"
    # 走 reply_failure 而不是 raise_command_usage，因为格式实际是对的，问题只是命令没带 /
    if not command.startswith("/"):
        await bot.send(event, at_prefix(event, reply_failure("执行", "命令必须以 / 开头")))
        return

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == target_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at_prefix(event, reply_failure("执行", "服务器不存在")))
        return

    try:
        # ST-1.4：部分 TShock 命令（如 /butcher all、/invade）耗时 > 5s，把 read 超时上调到 15s
        response = await request_server_api(
            server,
            "/v3/server/rawcmd",
            params={"cmd": command},
            timeout=15.0,
        )
    except TShockRequestError:
        await bot.send(event, at_prefix(event, reply_failure("执行", "无法连接服务器")))
        return

    if not is_success(response):
        # ST-4.5：去掉多余 f-string
        await bot.send(event, at_prefix(event, reply_failure("执行", get_error_reason(response))))
        return

    result_text = _extract_response_text(response.payload)
    if result_text:
        await bot.send(
            event,
            at_prefix(
                event,
                reply_block(
                    reply_success("执行"),
                    [
                        f"{EMOJI_SERVER} 服务器：{server.id}.{server.name}",
                        f"⚙️ 命令：{command}",
                        f"📋 返回内容：\n{result_text}",
                    ],
                ),
                sep="\n",
            ),
        )
        return

    await bot.send(
        event,
        at_prefix(
            event,
            reply_block(
                reply_success("执行"),
                [
                    f"{EMOJI_SERVER} 服务器：{server.id}.{server.name}",
                    f"⚙️ 命令：{command}",
                    "ℹ️ 无返回内容",
                ],
            ),
            sep="\n",
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

    # ST-5.5
    if server_id <= 0:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return

    # ST-2.1：per-server 单并发，避免同一服务器多人同时取地图导致 N×几十 MB 驻留
    sem = _semaphore_for(_map_semaphores, server_id)
    async with sem:
        try:
            # ST-2.2：read 超时拉到 300s（large 世界渲染可能 60-120s），其它维度仍用合理默认
            response = await request_server_api(
                server,
                "/nextbot/world/map-image",
                timeout=_LONG_READ_TIMEOUT,
            )
        except TShockRequestError:
            await bot.send(event, reply_failure("查询", "无法连接服务器"))
            return

        if not is_success(response):
            await bot.send(event, reply_failure("查询", get_error_reason(response)))
            return

        b64 = response.payload.get("base64")
        if not isinstance(b64, str) or not b64:
            await bot.send(event, reply_failure("查询", "返回数据格式错误"))
            return

        # ST-2.3：base64 串硬上限，超过就拒绝，避免被打爆
        if len(b64) > _MAX_BASE64_BYTES:
            logger.warning(
                f"全亮地图返回数据过大：server_id={server.id} size_bytes={len(b64)}"
            )
            await bot.send(event, reply_failure("查询", "返回数据过大"))
            return

        logger.info(f"世界地图获取成功：server_id={server.id} size_kb={len(b64) // 1024}")
        if bot.adapter.get_name() == "OneBot V11":
            try:
                await bot.send(event, OBV11MessageSegment.image(file=f"base64://{b64}"))
            finally:
                # 拿到结果后立刻释放本地引用，让 GC 尽早回收（消息段一旦发出就不再需要源串）
                del b64
                response.payload.pop("base64", None)
            return

        # ST-2.4：非 V11 适配器，fileName 走白名单后再展示
        safe_name = _safe_display_file_name(response.payload.get("fileName"), server.id, ".png")
        await bot.send(event, "ℹ️ 地图数据已获取，文件名：" + safe_name)
        del b64
        response.payload.pop("base64", None)


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

    # ST-5.5
    if server_id <= 0:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("下载", "服务器不存在"))
        return

    user_id = event.get_user_id()
    group_id = _resolve_group_id(event)

    # ST-3.3：per-server 单并发
    sem = _semaphore_for(_download_semaphores, server_id)
    async with sem:
        try:
            # ST-3.4：read 超时拉到 300s
            response = await request_server_api(
                server,
                "/nextbot/world/world-file",
                timeout=_LONG_READ_TIMEOUT,
            )
        except TShockRequestError:
            await bot.send(event, reply_failure("下载", "无法连接服务器"))
            return

        if not is_success(response):
            await bot.send(event, reply_failure("下载", get_error_reason(response)))
            return

        b64 = response.payload.get("base64")
        raw_file_name = response.payload.get("fileName")
        if not isinstance(b64, str) or not b64:
            await bot.send(event, reply_failure("下载", "返回数据格式错误"))
            return

        # ST-2.3 / 大小上限
        if len(b64) > _MAX_BASE64_BYTES:
            logger.warning(
                f"下载地图返回数据过大：server_id={server.id} size_bytes={len(b64)}"
            )
            await bot.send(event, reply_failure("下载", "文件过大"))
            return

        # ST-3.1 / ST-3.5：safe_name 在 OneBot upload 与 fallback 写盘两条路径上都使用
        safe_name = _safe_wld_name(raw_file_name, server.id)
        size_kb = len(b64) // 1024

        # ST-3.8：成功 / 失败两条路径都写一行带 user_id / group_id / size_kb 的审计日志
        logger.info(
            f"世界文件下载成功：server_id={server.id} user_id={user_id} "
            f"group_id={group_id} file={safe_name} size_kb={size_kb}"
        )

        if bot.adapter.get_name() == "OneBot V11":
            file_uri = f"base64://{b64}"
            # 拼出 file_uri 后原 b64 / payload["base64"] 已无人引用，立刻释放，避免 2× base64 同时驻留
            del b64
            response.payload.pop("base64", None)
            try:
                if isinstance(event, OBV11GroupMessageEvent):
                    await bot.call_api(
                        "upload_group_file",
                        group_id=event.group_id,
                        file=file_uri,
                        name=safe_name,
                    )
                else:
                    await bot.call_api(
                        "upload_private_file",
                        user_id=user_id,
                        file=file_uri,
                        name=safe_name,
                    )
            finally:
                # 上传完成后 file_uri 也不再需要，及时释放
                del file_uri
            return

        # 非 V11 fallback：tempfile 取唯一路径，写盘后用完立刻 unlink，不再泄漏 /tmp 路径
        tmp_path: Path | None = None
        try:
            file_data = base64.b64decode(b64)
            del b64
            response.payload.pop("base64", None)
            with tempfile.NamedTemporaryFile(
                suffix=".wld",
                prefix=f"world-{server.id}-",
                delete=False,
            ) as tmp:
                tmp.write(file_data)
                tmp_path = Path(tmp.name)
            del file_data
            # ST-3.6 / ST-3.7：仅展示文件名 + 大小，走 reply_success/reply_block 与其它路径一致
            await bot.send(
                event,
                reply_block(
                    reply_success("下载"),
                    [
                        f"📁 文件：{safe_name}",
                        f"📦 大小：{size_kb} KB",
                    ],
                ),
            )
        finally:
            # ST-3.2：fallback 写出的文件总要清理，避免 /tmp 被多次调用累计撑爆
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
