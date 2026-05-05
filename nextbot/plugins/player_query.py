import base64
import binascii
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from nonebot import get_driver, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_inventory_page, create_progress_page
from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import Server, User, get_session
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.time_utils import beijing_filename_timestamp, format_online_seconds
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import reply_failure

online_matcher = on_command("在线")
self_kick_matcher = on_command("自踢")
inventory_matcher = on_command("用户背包")
my_inventory_matcher = on_command("我的背包")
progress_matcher = on_command("进度")
my_map_matcher = on_command("我的地图")

INVENTORY_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=2000,
    viewport_height=1000,
    full_page=True,
    fit_content_height=True,
)
PROGRESS_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1200,
    viewport_height=700,
    full_page=True,
    fit_content_height=True,
)
from nextbot.progression import PROGRESSION_KEY_TO_ZH as _PROGRESS_NAME_MAP


def _to_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None

    parsed: int
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError:
            return None
    else:
        return None

    if parsed < 0:
        return None
    return parsed


def _parse_user_info_texts(response_payload: dict[str, object]) -> dict[str, str] | None:
    current_life = _to_non_negative_int(response_payload.get("health"))
    max_life = _to_non_negative_int(response_payload.get("maxHealth"))
    current_mana = _to_non_negative_int(response_payload.get("mana"))
    max_mana = _to_non_negative_int(response_payload.get("maxMana"))
    fishing_tasks = _to_non_negative_int(response_payload.get("questsCompleted"))
    pve_deaths = _to_non_negative_int(response_payload.get("deathsPve"))
    pvp_deaths = _to_non_negative_int(response_payload.get("deathsPvp"))
    if (
        current_life is None
        or max_life is None
        or current_mana is None
        or max_mana is None
        or fishing_tasks is None
    ):
        return None

    online_seconds = _to_non_negative_int(response_payload.get("onlineSeconds"))
    return {
        "life_text": f"{current_life}/{max_life}",
        "mana_text": f"{current_mana}/{max_mana}",
        "fishing_tasks_text": str(fishing_tasks),
        "pve_deaths_text": str(pve_deaths if pve_deaths is not None else 0),
        "pvp_deaths_text": str(pvp_deaths if pvp_deaths is not None else 0),
        "online_time_text": format_online_seconds(online_seconds) if online_seconds is not None else "",
    }


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


def _to_public_render_url(url: str) -> str:
    config = get_driver().config
    base_url = str(getattr(config, "web_server_public_base_url", "")).strip()
    if not base_url:
        return url

    try:
        target = urlparse(url)
        base = urlparse(base_url)
    except Exception:
        return url

    if not base.scheme or not base.netloc:
        return url

    return urlunparse(
        (
            base.scheme,
            base.netloc,
            target.path,
            target.params,
            target.query,
            target.fragment,
        )
    )


@online_matcher.handle()
@command_control(
    command_key="player_query.online",
    display_name="在线",
    permission="player_query.online",
    description="查询服务器在线状态与在线玩家列表",
    usage="在线",
    category="玩家查询",
)
@require_permission("player_query.online")
async def handle_online(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "在线")
    if args:
        raise_command_usage()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        await bot.send(event, reply_failure("查询", "暂无服务器"))
        return

    lines: list[str] = []
    for i, server in enumerate(servers):
        if i > 0:
            lines.append("")
        lines.append(f"{server.id}.{server.name}")
        try:
            response = await request_server_api(
                server,
                "/v2/server/status",
                params={"players": "true"},
            )
        except TShockRequestError:
            lines.append("❌ 查询失败，无法连接服务器")
            continue

        if not is_success(response):
            lines.append(f"❌ 查询失败，{get_error_reason(response)}")
            continue

        players = response.payload.get("players")
        if not isinstance(players, list):
            lines.append("❌ 查询失败，返回数据格式错误")
            continue

        playercount = response.payload.get("playercount")
        maxplayers = response.payload.get("maxplayers")
        if not isinstance(playercount, int) or not isinstance(maxplayers, int):
            lines.append("❌ 查询失败，返回数据格式错误")
            continue

        if not players:
            lines.append("ℹ️ 无玩家在线")
            continue

        lines.append(f"在线玩家（{playercount}/{maxplayers}）")
        nicknames: list[str] = []
        for player in players:
            if isinstance(player, dict):
                nickname = str(player.get("nickname", "")).strip()
                if nickname:
                    nicknames.append(nickname)
                    continue
            nicknames.append(str(player))

        player_names = ",".join(nicknames)
        lines.append(player_names)

    logger.info(f"在线查询完成：server_count={len(servers)}")
    await bot.send(event, "🖥️ 服务器在线状态\n" + "\n".join(lines))


@self_kick_matcher.handle()
@command_control(
    command_key="player_query.kick.self",
    display_name="自踢",
    permission="player_query.kick.self",
    description="对所有服务器执行当前用户的踢出命令",
    usage="自踢",
    category="服务器工具",
)
@require_permission("player_query.kick.self")
async def handle_self_kick(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "自踢")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if user is None:
        await bot.send(event, at + " " + reply_failure("执行", "未注册账号"))
        return

    if not servers:
        await bot.send(event, at + " " + reply_failure("执行", "暂无服务器"))
        return

    lines: list[str] = []
    for server in servers:
        try:
            response = await request_server_api(
                server,
                "/v3/server/rawcmd",
                params={"cmd": f"/kick {user.name}"},
            )
        except TShockRequestError:
            lines.append(f"{server.id}.{server.name}：❌ 执行失败，无法连接服务器")
            continue

        if is_success(response):
            lines.append(f"{server.id}.{server.name}：✅ 执行成功")
            continue

        reason = get_error_reason(response)
        lines.append(f"{server.id}.{server.name}：❌ 执行失败，{reason}")

    logger.info(
        f"自踢执行完成：user_id={user_id} name={user.name} server_count={len(servers)}"
    )
    await bot.send(event, at + "\n🖥️ 自踢结果\n" + "\n".join(lines))


@inventory_matcher.handle()
@command_control(
    command_key="player_query.inventory.user",
    display_name="用户背包",
    permission="player_query.inventory.user",
    description="查询指定用户背包并生成截图",
    usage="用户背包 <服务器 ID> <用户 QQ/@用户/用户名称>",
    params={
        "show_stats": {
            "type": "bool",
            "label": "显示统计信息",
            "description": "关闭后隐藏背包顶部的统计信息栏",
            "required": False,
            "default": True,
        },
        "show_index": {
            "type": "bool",
            "label": "显示索引",
            "description": "关闭后隐藏物品格左上角的索引编号",
            "required": False,
            "default": True,
        },
        "send_link": {
            "type": "bool",
            "label": "发送链接",
            "description": "开启后在截图前额外发送背包页面链接",
            "required": False,
            "default": False,
        },
    },
    category="玩家查询",
)
@require_permission("player_query.inventory.user")
async def handle_user_inventory(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "用户背包")
    if len(args) != 2:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "用户背包",
        arg_index=1,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, reply_failure("查询", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, reply_failure("查询", "用户参数解析失败"))
        return

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        target_user = session.query(User).filter(User.user_id == target_user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return
    if target_user is None:
        await bot.send(event, reply_failure("查询", "用户不存在"))
        return

    try:
        response = await request_server_api(
            server,
            f"/nextbot/users/{target_user.name}/inventory",
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    inventory = response.payload.get("items")
    if not isinstance(inventory, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    try:
        info_response = await request_server_api(
            server,
            f"/nextbot/users/{target_user.name}/stats",
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(info_response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(info_response)}"))
        return

    info_texts = _parse_user_info_texts(info_response.payload)
    if info_texts is None:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    page_url = create_inventory_page(
        user_id=target_user.user_id,
        user_name=target_user.name,
        server_id=server.id,
        server_name=server.name,
        life_text=info_texts["life_text"],
        mana_text=info_texts["mana_text"],
        fishing_tasks_text=info_texts["fishing_tasks_text"],
        pve_deaths_text=info_texts["pve_deaths_text"],
        pvp_deaths_text=info_texts["pvp_deaths_text"],
        online_time_text=info_texts.get("online_time_text", ""),
        show_stats=bool(get_current_param("show_stats", True)),
        show_index=bool(get_current_param("show_index", True)),
        slots=[item for item in inventory if isinstance(item, dict)],
    )
    public_page_url = _to_public_render_url(page_url)
    logger.info(
        "用户背包渲染地址："
        f"server_id={server.id} target_user_id={target_user.user_id} "
        f"internal_url={page_url} public_url={public_page_url}"
    )
    if bool(get_current_param("send_link", False)):
        await bot.send(event, f"ℹ️ 用户背包链接：{public_page_url}")
    screenshot_path = Path("/tmp") / (
        f"inventory-{server.id}-{target_user.user_id}-{beijing_filename_timestamp()}.png"
    )
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=INVENTORY_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", f"{exc}"))
        return

    logger.info(
        f"用户背包截图成功：server_id={server.id} target_user_id={target_user.user_id} file={screenshot_path}"
    )
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return
    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


@my_inventory_matcher.handle()
@command_control(
    command_key="player_query.inventory.self",
    display_name="我的背包",
    permission="player_query.inventory.self",
    description="查询当前用户背包并生成截图",
    usage="我的背包 <服务器 ID>",
    params={
        "show_stats": {
            "type": "bool",
            "label": "显示统计信息",
            "description": "关闭后隐藏背包顶部的统计信息栏",
            "required": False,
            "default": True,
        },
        "show_index": {
            "type": "bool",
            "label": "显示索引",
            "description": "关闭后隐藏物品格左上角的索引编号",
            "required": False,
            "default": True,
        },
        "send_link": {
            "type": "bool",
            "label": "发送链接",
            "description": "开启后在截图前额外发送背包页面链接",
            "required": False,
            "default": False,
        },
    },
    category="玩家查询",
)
@require_permission("player_query.inventory.self")
async def handle_my_inventory(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "我的背包")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    user_id = event.get_user_id()
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return
    if user is None:
        await bot.send(event, reply_failure("查询", "用户不存在"))
        return

    try:
        response = await request_server_api(
            server,
            f"/nextbot/users/{user.name}/inventory",
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    inventory = response.payload.get("items")
    if not isinstance(inventory, list):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    try:
        info_response = await request_server_api(
            server,
            f"/nextbot/users/{user.name}/stats",
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(info_response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(info_response)}"))
        return

    info_texts = _parse_user_info_texts(info_response.payload)
    if info_texts is None:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    page_url = create_inventory_page(
        user_id=user.user_id,
        user_name=user.name,
        server_id=server.id,
        server_name=server.name,
        life_text=info_texts["life_text"],
        mana_text=info_texts["mana_text"],
        fishing_tasks_text=info_texts["fishing_tasks_text"],
        pve_deaths_text=info_texts["pve_deaths_text"],
        pvp_deaths_text=info_texts["pvp_deaths_text"],
        online_time_text=info_texts.get("online_time_text", ""),
        show_stats=bool(get_current_param("show_stats", True)),
        show_index=bool(get_current_param("show_index", True)),
        slots=[item for item in inventory if isinstance(item, dict)],
    )
    public_page_url = _to_public_render_url(page_url)
    logger.info(
        "我的背包渲染地址："
        f"server_id={server.id} user_id={user.user_id} "
        f"internal_url={page_url} public_url={public_page_url}"
    )
    if bool(get_current_param("send_link", False)):
        await bot.send(event, f"ℹ️ 我的背包链接：{public_page_url}")

    screenshot_path = Path("/tmp") / (
        f"inventory-{server.id}-{user.user_id}-{beijing_filename_timestamp()}.png"
    )
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=INVENTORY_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", f"{exc}"))
        return

    logger.info(
        f"我的背包截图成功：server_id={server.id} user_id={user.user_id} file={screenshot_path}"
    )
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return
    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


@my_map_matcher.handle()
@command_control(
    command_key="player_query.map.self",
    display_name="我的地图",
    permission="player_query.map.self",
    description="查询当前用户在指定服务器世界中的探索地图",
    usage="我的地图 <服务器 ID>",
    category="玩家查询",
)
@require_permission("player_query.map.self")
async def handle_my_map(bot: Bot, event: Event, arg: Message = CommandArg()):
    # API 已直接返回最终的 PNG base64，无需走 page+screenshot 渲染。
    args = parse_command_args_with_fallback(event, arg, "我的地图")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    user_id = event.get_user_id()
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return
    if user is None:
        await bot.send(event, reply_failure("查询", "用户不存在"))
        return

    logger.info(
        f"我的地图请求：server_id={server.id} user_id={user.user_id} target_user_name={user.name}"
    )

    try:
        response = await request_server_api(
            server,
            f"/nextbot/users/{user.name}/map-image",
            timeout=30.0,
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    b64_string = str(response.payload.get("base64") or "").strip()
    if not b64_string:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    try:
        png_bytes = base64.b64decode(b64_string, validate=True)
    except (binascii.Error, ValueError):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    screenshot_path = Path("/tmp") / (
        f"map-{server.id}-{user.user_id}-{beijing_filename_timestamp()}.png"
    )
    try:
        screenshot_path.write_bytes(png_bytes)
    except OSError:
        await bot.send(event, reply_failure("查询", "保存图片失败"))
        return

    logger.info(
        f"我的地图发送成功：server_id={server.id} user_id={user.user_id} file={screenshot_path}"
    )

    if bot.adapter.get_name() == "OneBot V11":
        await bot.send(event, OBV11MessageSegment.image(file=f"base64://{b64_string}"))
        return
    await bot.send(event, f"✅ 地图生成成功，文件：{screenshot_path}")


@progress_matcher.handle()
@command_control(
    command_key="player_query.progress",
    display_name="进度",
    permission="player_query.progress",
    description="查询世界进度并生成截图",
    usage="进度 <服务器 ID>",
    category="玩家查询",
)
@require_permission("player_query.progress")
async def handle_world_progress(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "进度")
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
            "/nextbot/world/progress",
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    progress = {
        _PROGRESS_NAME_MAP.get(k, k): v
        for k, v in response.payload.items()
        if isinstance(v, bool)
    }
    if not progress:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    page_url = create_progress_page(
        server_id=server.id,
        server_name=server.name,
        progress=progress,
    )
    logger.info(
        "世界进度渲染地址："
        f"server_id={server.id} "
        f"internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / (
        f"progress-{server.id}-{beijing_filename_timestamp()}.png"
    )
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=PROGRESS_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", f"{exc}"))
        return

    logger.info(
        f"世界进度截图成功：server_id={server.id} file={screenshot_path}"
    )
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return
    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")
