import asyncio
import base64
import binascii
from urllib.parse import quote, urlparse, urlunparse

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from server.screenshot import ScreenshotOptions
from server.server_config import get_server_settings
from server.web_server import create_inventory_page, create_progress_page
from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import Server, User, get_session
from nextbot.large_image import (
    LONG_READ_TIMEOUT as _LONG_READ_TIMEOUT,
    MAX_BASE64_BYTES as _MAX_BASE64_BYTES,
    register_server_semaphore_pool as _register_server_semaphore_pool,
    semaphore_for as _semaphore_for,
)
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.screenshot_temp import temp_screenshot_path
from nextbot.time_utils import format_online_seconds
from nextbot.tshock_api import (
    TShockRequestError,
    TShockResponse,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import reply_block, reply_failure, reply_success, safe_at_segment, safe_at_segment_or_empty

online_matcher = on_command("在线")
self_kick_matcher = on_command("自踢")
inventory_matcher = on_command("用户背包")
my_inventory_matcher = on_command("我的背包")
progress_matcher = on_command("进度")
my_map_matcher = on_command("我的地图")
user_map_matcher = on_command("用户地图")
explored_map_matcher = on_command("查看地图")

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

# Per-server 信号量池：限制同一服务器同时驻留内存的大对象渲染数量。
# 不同 handler 用不同 dict 隔离，避免 inventory / map / explored-map / progress 互相挤占。
# - 背包 / 进度：Playwright 截图，PNG 较小，max_concurrent=2
# - 地图（base64 直回）：单并发，避免几十 MB 累积
_inventory_semaphores: dict[int, "asyncio.Semaphore"] = {}
_progress_semaphores: dict[int, "asyncio.Semaphore"] = {}
_my_map_semaphores: dict[int, "asyncio.Semaphore"] = {}
_user_map_semaphores: dict[int, "asyncio.Semaphore"] = {}
_explored_map_semaphores: dict[int, "asyncio.Semaphore"] = {}
# R8 M-5：注册到中央 pool 列表，server 删除时统一清理
_register_server_semaphore_pool(_inventory_semaphores)
_register_server_semaphore_pool(_progress_semaphores)
_register_server_semaphore_pool(_my_map_semaphores)
_register_server_semaphore_pool(_user_map_semaphores)
_register_server_semaphore_pool(_explored_map_semaphores)


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

    raw_map_exploration = response_payload.get("mapExplorationPercent")
    map_exploration_value: float | None
    if isinstance(raw_map_exploration, bool):
        map_exploration_value = None
    elif isinstance(raw_map_exploration, (int, float)):
        map_exploration_value = float(raw_map_exploration)
    else:
        map_exploration_value = None

    return {
        "life_text": f"{current_life}/{max_life}",
        "mana_text": f"{current_mana}/{max_mana}",
        "fishing_tasks_text": str(fishing_tasks),
        "pve_deaths_text": str(pve_deaths if pve_deaths is not None else 0),
        "pvp_deaths_text": str(pvp_deaths if pvp_deaths is not None else 0),
        "online_time_text": format_online_seconds(online_seconds) if online_seconds is not None else "",
        "map_exploration_text": f"{map_exploration_value:.2f}%" if map_exploration_value is not None else "",
    }


def _to_public_render_url(url: str) -> str:
    # PQA-CC-4：用 server_settings.public_base_url（含 http://{host}:{port} fallback），
    # 不再直接读 driver.config，与 server_config._normalize_public_base_url 行为对齐。
    base_url = str(get_server_settings().public_base_url or "").strip()
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


# PQB-X.4 / PC-4.1：_safe_at_segment 已提升到 nextbot.text_utils.safe_at_segment，
# 此处保留模块级 alias，避免本文件其它 callsite 大改。
_safe_at_segment = safe_at_segment


@online_matcher.handle()
@command_control(
    command_key="player_query.online",
    display_name="在线",
    permission="player_query.online",
    description="查询服务器在线状态与在线玩家列表",
    usage="在线",
    category="查询系统",
)
@require_permission("player_query.online")
async def handle_online(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "在线")
    if args:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        await bot.send(event, at + " " + reply_failure("查询", "暂无服务器"))
        return

    # PQA-1.1：并行 fan-out，避免 N 台服务器串行 connect+read 各 5s 累积 N×10s
    async def _query_one(server: Server) -> list[str]:
        out: list[str] = [f"{server.id}.{server.name}"]
        try:
            response = await request_server_api(
                server,
                "/v2/server/status",
                params={"players": "true"},
            )
        except TShockRequestError:
            out.append("❌ 查询失败，无法连接服务器")
            return out

        if not is_success(response):
            out.append(f"❌ 查询失败，{get_error_reason(response)}")
            return out

        players = response.payload.get("players")
        if not isinstance(players, list):
            out.append("❌ 查询失败，返回数据格式错误")
            return out

        playercount = response.payload.get("playercount")
        maxplayers = response.payload.get("maxplayers")
        if not isinstance(playercount, int) or not isinstance(maxplayers, int):
            out.append("❌ 查询失败，返回数据格式错误")
            return out

        if not players:
            out.append("ℹ️ 无玩家在线")
            return out

        out.append(f"在线玩家（{playercount}/{maxplayers}）")
        nicknames: list[str] = []
        for player in players:
            if isinstance(player, dict):
                nickname = str(player.get("nickname", "")).strip()
                if nickname:
                    nicknames.append(nickname)
                    continue
            nicknames.append(str(player))
        out.append(",".join(nicknames))
        return out

    # R5-B.1：return_exceptions=True 防止任一 task 抛非 TShockRequestError 异常
    # （如 CancelledError、内部 bug）时整个 gather cancel 其他任务，与 R4 M3
    # user_manager / leaderboard / lottery 的 fan-out 模板对齐。
    raw_results = await asyncio.gather(
        *(_query_one(s) for s in servers), return_exceptions=True
    )

    results: list[list[str]] = []
    for server, raw in zip(servers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            logger.warning(
                f"在线查询异常：server_id={server.id} reason={raw!r}"
            )
            results.append([f"{server.id}.{server.name}", "❌ 查询失败，查询异常"])
        else:
            results.append(raw)

    lines: list[str] = []
    for i, server_lines in enumerate(results):
        if i > 0:
            lines.append("")
        lines.extend(server_lines)

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
    at_seg = _safe_at_segment(user_id)
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if user is None:
        msg = reply_failure("执行", "未注册账号")
        if at_seg is not None:
            await bot.send(event, at_seg + " " + msg)
        else:
            await bot.send(event, msg)
        return

    if not servers:
        msg = reply_failure("执行", "暂无服务器")
        if at_seg is not None:
            await bot.send(event, at_seg + " " + msg)
        else:
            await bot.send(event, msg)
        return

    # PQA-2.1：并行 fan-out。/kick 对已下线玩家是幂等的，并发安全。
    async def _kick_one(server: Server) -> str:
        try:
            response = await request_server_api(
                server,
                "/v3/server/rawcmd",
                params={"cmd": f"/kick {user.name}"},
            )
        except TShockRequestError:
            return f"{server.id}.{server.name}：❌ 执行失败，无法连接服务器"

        if is_success(response):
            return f"{server.id}.{server.name}：✅ 执行成功"
        return f"{server.id}.{server.name}：❌ 执行失败，{get_error_reason(response)}"

    # R5-B.1：return_exceptions=True 防止任一 task 抛非 TShockRequestError 异常
    # 时整个 gather cancel 其他任务。/kick 对已下线玩家幂等，部分失败可接受。
    raw_results = await asyncio.gather(
        *(_kick_one(s) for s in servers), return_exceptions=True
    )
    lines: list[str] = []
    for server, raw in zip(servers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            logger.warning(
                f"自踢执行异常：server_id={server.id} user_id={user_id} reason={raw!r}"
            )
            lines.append(f"{server.id}.{server.name}：❌ 执行失败，执行异常")
        else:
            lines.append(raw)

    logger.info(
        f"自踢执行完成：user_id={user_id} name={user.name} server_count={len(servers)}"
    )
    body = "🖥️ 自踢结果\n" + "\n".join(lines)
    if at_seg is not None:
        await bot.send(event, at_seg + "\n" + body)
    else:
        await bot.send(event, body)


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
    category="查询系统",
)
@require_permission("player_query.inventory.user")
async def handle_user_inventory(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "用户背包")
    if len(args) != 2:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())

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
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户参数解析失败"))
        return

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        # TOCTOU: target may be renamed between DB read and TShock fetch; TShock will return 404 in that case.
        target_user = session.query(User).filter(User.user_id == target_user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return
    if target_user is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户不存在"))
        return

    # PQA-3.2：per-server 并发上限（背包是 Playwright 截图，PNG 较小，max=2 比地图宽松）
    sem = _semaphore_for(_inventory_semaphores, server.id, max_concurrent=2)
    async with sem:
        # PQA-3.4：inventory + stats 两次 API 改并行（halve wall time）
        # PQB-X.2 / PQA-3.6：URL 路径段插值前 quote(safe="") 防御 user.name 含 / 等字符
        encoded_name = quote(target_user.name, safe="")
        inv_task = request_server_api(
            server, f"/nextbot/users/{encoded_name}/inventory"
        )
        stats_task = request_server_api(
            server, f"/nextbot/users/{encoded_name}/stats"
        )
        try:
            inv_result, stats_result = await asyncio.gather(
                inv_task, stats_task, return_exceptions=True
            )
        except Exception:
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return

        # 任一连接级异常 → 统一回 "无法连接服务器"
        if isinstance(inv_result, TShockRequestError) or isinstance(
            stats_result, TShockRequestError
        ):
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return
        # 其它未预期异常按 raise 处理（通常是开发期 bug，应该 surface）
        if isinstance(inv_result, BaseException):
            raise inv_result
        if isinstance(stats_result, BaseException):
            raise stats_result

        response: TShockResponse = inv_result
        info_response: TShockResponse = stats_result

        if not is_success(response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
            return

        inventory = response.payload.get("items")
        if not isinstance(inventory, list):
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return

        if not is_success(info_response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(info_response)))
            return

        info_texts = _parse_user_info_texts(info_response.payload)
        if info_texts is None:
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
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
            map_exploration_text=info_texts.get("map_exploration_text", ""),
            show_stats=bool(get_current_param("show_stats", True)),
            show_index=bool(get_current_param("show_index", True)),
            slots=[item for item in inventory if isinstance(item, dict)],
        )
        public_page_url = _to_public_render_url(page_url)
        # PQA-3.7：日志不再记录完整 render URL（含 token），只保留诊断必要字段
        logger.info(
            f"用户背包渲染：server_id={server.id} target_user_id={target_user.user_id}"
        )
        if bool(get_current_param("send_link", False)):
            await bot.send(event, f"ℹ️ 用户背包链接：{public_page_url}")
        # per-server semaphore 已由外层 `async with sem` 持有；helper 不再
        # 重复加锁，否则同一 task 二次 acquire 同一信号量在 max=2 + 并发 2 时
        # 可能构成死锁。
        await render_and_send_screenshot(
            bot,
            event,
            page_url=page_url,
            options=INVENTORY_SCREENSHOT_OPTIONS,
            file_prefix=f"inventory-{server.id}-{target_user.user_id}",
            failure_action="查询",
        )


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
    category="查询系统",
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
    at = safe_at_segment_or_empty(user_id)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        # TOCTOU: caller may be renamed between DB read and TShock fetch; TShock will return 404.
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return
    if user is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户不存在"))
        return

    # PQA-4.2：与 handle_user_inventory 共享同一组 per-server semaphore，防止 OOM
    sem = _semaphore_for(_inventory_semaphores, server.id, max_concurrent=2)
    async with sem:
        # PQA-4.4：inventory + stats 并行
        # PQB-X.2：URL 路径段插值前 quote(safe="") 防御
        encoded_name = quote(user.name, safe="")
        inv_task = request_server_api(
            server, f"/nextbot/users/{encoded_name}/inventory"
        )
        stats_task = request_server_api(
            server, f"/nextbot/users/{encoded_name}/stats"
        )
        try:
            inv_result, stats_result = await asyncio.gather(
                inv_task, stats_task, return_exceptions=True
            )
        except Exception:
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return

        if isinstance(inv_result, TShockRequestError) or isinstance(
            stats_result, TShockRequestError
        ):
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return
        if isinstance(inv_result, BaseException):
            raise inv_result
        if isinstance(stats_result, BaseException):
            raise stats_result

        response: TShockResponse = inv_result
        info_response: TShockResponse = stats_result

        if not is_success(response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
            return

        inventory = response.payload.get("items")
        if not isinstance(inventory, list):
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return

        if not is_success(info_response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(info_response)))
            return

        info_texts = _parse_user_info_texts(info_response.payload)
        if info_texts is None:
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
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
            map_exploration_text=info_texts.get("map_exploration_text", ""),
            show_stats=bool(get_current_param("show_stats", True)),
            show_index=bool(get_current_param("show_index", True)),
            slots=[item for item in inventory if isinstance(item, dict)],
        )
        public_page_url = _to_public_render_url(page_url)
        # PQA-3.7：日志不再记录完整 render URL（含 token）
        logger.info(
            f"我的背包渲染：server_id={server.id} user_id={user.user_id}"
        )
        if bool(get_current_param("send_link", False)):
            await bot.send(event, f"ℹ️ 我的背包链接：{public_page_url}")

        # per-server semaphore 已由外层 `async with sem` 持有；helper 不再
        # 重复加锁，避免同 task 重复 acquire 在并发场景下死锁。
        await render_and_send_screenshot(
            bot,
            event,
            page_url=page_url,
            options=INVENTORY_SCREENSHOT_OPTIONS,
            file_prefix=f"inventory-{server.id}-{user.user_id}",
            failure_action="查询",
        )


@my_map_matcher.handle()
@command_control(
    command_key="player_query.map.self",
    display_name="我的地图",
    permission="player_query.map.self",
    description="查询当前用户在指定服务器世界中的探索地图",
    usage="我的地图 <服务器 ID>",
    category="查询系统",
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
    at = safe_at_segment_or_empty(user_id)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        # TOCTOU: caller may be renamed between DB read and TShock fetch; TShock will return 404.
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return
    if user is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户不存在"))
        return

    logger.info(
        f"我的地图请求：server_id={server.id} user_id={user.user_id} target_user_name={user.name}"
    )

    # PQB-1.1：per-server 单并发 + 长 read 超时，避免大世界并发渲染撑爆内存
    sem = _semaphore_for(_my_map_semaphores, server.id)
    async with sem:
        # PQB-X.2 / PQB-1.3：URL 段插值前 quote(safe="") 防御
        encoded_name = quote(user.name, safe="")
        try:
            response = await request_server_api(
                server,
                f"/nextbot/users/{encoded_name}/map-image",
                timeout=_LONG_READ_TIMEOUT,
            )
        except TShockRequestError:
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return

        if not is_success(response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
            return

        b64_string = str(response.payload.get("base64") or "").strip()
        if not b64_string:
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return

        # PQB-1.1：硬上限，超过即拒绝
        if len(b64_string) > _MAX_BASE64_BYTES:
            logger.warning(
                f"我的地图返回数据过大：server_id={server.id} user_id={user.user_id} size_bytes={len(b64_string)}"
            )
            await bot.send(event, at + " " + reply_failure("查询", "返回数据过大"))
            return

        if bot.adapter.get_name() == "OneBot V11":
            # PQB-1.6：V11 路径直接用 b64_string，跳过 b64decode + write_bytes（去掉一份冗余拷贝）
            at_seg = _safe_at_segment(user_id)
            image = OBV11MessageSegment.image(file=f"base64://{b64_string}")
            try:
                if at_seg is not None:
                    # 同消息内 @用户 + 图片，与自踢等命令的 at 模式保持一致
                    await bot.send(event, at_seg + image)
                else:
                    await bot.send(event, image)
            finally:
                # 拼出消息段后立刻释放本地引用，让 GC 尽早回收
                del b64_string
                response.payload.pop("base64", None)
            logger.info(
                f"我的地图发送成功：server_id={server.id} user_id={user.user_id}"
            )
            return

        # 非 V11 fallback：写一次盘，仅展示文件名 + 大小，不暴露 /tmp 路径
        try:
            png_bytes = base64.b64decode(b64_string, validate=True)
        except (binascii.Error, ValueError):
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return
        size_kb = len(png_bytes) // 1024
        del b64_string
        response.payload.pop("base64", None)

        async with temp_screenshot_path(
            f"map-{server.id}-{user.user_id}"
        ) as screenshot_path:
            try:
                screenshot_path.write_bytes(png_bytes)
            except OSError:
                await bot.send(event, at + " " + reply_failure("查询", "保存图片失败"))
                return
            del png_bytes

            logger.info(
                f"我的地图发送成功：server_id={server.id} user_id={user.user_id} file={screenshot_path.name}"
            )
            # PQB-1.5：不暴露 /tmp 路径
            await bot.send(
                event,
                reply_block(
                    reply_success("查询"),
                    [
                        f"📁 文件：{screenshot_path.name}",
                        f"📦 大小：{size_kb} KB",
                    ],
                ),
            )


@user_map_matcher.handle()
@command_control(
    command_key="player_query.map.user",
    display_name="用户地图",
    permission="player_query.map.user",
    description="查询指定用户在指定服务器世界中的探索地图",
    usage="用户地图 <服务器 ID> <用户 QQ/@用户/用户名称>",
    category="查询系统",
)
@require_permission("player_query.map.user")
async def handle_user_map(bot: Bot, event: Event, arg: Message = CommandArg()):
    # API 已直接返回最终的 PNG base64，无需走 page+screenshot 渲染。
    args = parse_command_args_with_fallback(event, arg, "用户地图")
    if len(args) != 2:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "用户地图",
        arg_index=1,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户参数解析失败"))
        return

    requester_user_id = event.get_user_id()
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        # TOCTOU: target may be renamed between DB read and TShock fetch; TShock will return 404.
        target_user = (
            session.query(User).filter(User.user_id == target_user_id).first()
        )
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return
    if target_user is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户不存在"))
        return

    logger.info(
        f"用户地图请求：server_id={server.id} requester_user_id={requester_user_id} "
        f"target_user_id={target_user.user_id} target_user_name={target_user.name}"
    )

    # PQB-2.1：per-server 单并发 + 长 read 超时
    sem = _semaphore_for(_user_map_semaphores, server.id)
    async with sem:
        # PQB-X.2 / PQB-2.2：URL 段插值前 quote(safe="") 防御
        encoded_name = quote(target_user.name, safe="")
        try:
            response = await request_server_api(
                server,
                f"/nextbot/users/{encoded_name}/map-image",
                timeout=_LONG_READ_TIMEOUT,
            )
        except TShockRequestError:
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return

        if not is_success(response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
            return

        b64_string = str(response.payload.get("base64") or "").strip()
        if not b64_string:
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return

        # PQB-2.1：硬上限
        if len(b64_string) > _MAX_BASE64_BYTES:
            logger.warning(
                f"用户地图返回数据过大：server_id={server.id} target_user_id={target_user.user_id} size_bytes={len(b64_string)}"
            )
            await bot.send(event, at + " " + reply_failure("查询", "返回数据过大"))
            return

        if bot.adapter.get_name() == "OneBot V11":
            # PQB-1.6：V11 路径跳过 b64decode + write_bytes
            at_seg = _safe_at_segment(requester_user_id)
            image = OBV11MessageSegment.image(file=f"base64://{b64_string}")
            try:
                if at_seg is not None:
                    # 同消息内 @ 发起人 + 图片，与 我的地图 的 at 模式一致
                    await bot.send(event, at_seg + image)
                else:
                    await bot.send(event, image)
            finally:
                del b64_string
                response.payload.pop("base64", None)
            logger.info(
                f"用户地图发送成功：server_id={server.id} requester_user_id={requester_user_id} "
                f"target_user_id={target_user.user_id}"
            )
            return

        # 非 V11 fallback
        try:
            png_bytes = base64.b64decode(b64_string, validate=True)
        except (binascii.Error, ValueError):
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return
        size_kb = len(png_bytes) // 1024
        del b64_string
        response.payload.pop("base64", None)

        async with temp_screenshot_path(
            f"map-{server.id}-{target_user.user_id}"
        ) as screenshot_path:
            try:
                screenshot_path.write_bytes(png_bytes)
            except OSError:
                await bot.send(event, at + " " + reply_failure("查询", "保存图片失败"))
                return
            del png_bytes

            logger.info(
                f"用户地图发送成功：server_id={server.id} requester_user_id={requester_user_id} "
                f"target_user_id={target_user.user_id} file={screenshot_path.name}"
            )
            # PQB-2.5：不暴露 /tmp 路径
            await bot.send(
                event,
                reply_block(
                    reply_success("查询"),
                    [
                        f"📁 文件：{screenshot_path.name}",
                        f"📦 大小：{size_kb} KB",
                    ],
                ),
            )


@explored_map_matcher.handle()
@command_control(
    command_key="player_query.map.explored",
    display_name="查看地图",
    permission="player_query.map.explored",
    description="查看所有玩家共同探索过的区域地图",
    usage="查看地图 <服务器 ID>",
    category="查询系统",
)
@require_permission("player_query.map.explored")
async def handle_explored_map(bot: Bot, event: Event, arg: Message = CommandArg()):
    # API 已直接返回最终的 PNG base64，无需走 page+screenshot 渲染。
    args = parse_command_args_with_fallback(event, arg, "查看地图")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    requester_user_id = event.get_user_id()
    at = safe_at_segment_or_empty(requester_user_id)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return

    logger.info(
        f"查看地图请求：server_id={server.id} requester_user_id={requester_user_id}"
    )

    # PQB-3.1 / PQB-3.2：保留 guest 权限，但加 ST-2.1 模板（per-server 单并发 + 200MB 上限 + 长 read）
    # 防止任意 guest 通过并发触发 OOM
    sem = _semaphore_for(_explored_map_semaphores, server.id)
    async with sem:
        try:
            # PQB-3.4：30s -> 300s（large 世界 explored region 渲染常 60-120s）
            response = await request_server_api(
                server,
                "/nextbot/world/explored-map-image",
                timeout=_LONG_READ_TIMEOUT,
            )
        except TShockRequestError:
            await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
            return

        if not is_success(response):
            await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
            return

        b64_string = str(response.payload.get("base64") or "").strip()
        if not b64_string:
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return

        # PQB-3.1：硬上限
        if len(b64_string) > _MAX_BASE64_BYTES:
            logger.warning(
                f"查看地图返回数据过大：server_id={server.id} requester_user_id={requester_user_id} size_bytes={len(b64_string)}"
            )
            await bot.send(event, at + " " + reply_failure("查询", "返回数据过大"))
            return

        if bot.adapter.get_name() == "OneBot V11":
            # PQB-3.7：V11 路径跳过 b64decode + write_bytes
            at_seg = _safe_at_segment(requester_user_id)
            image = OBV11MessageSegment.image(file=f"base64://{b64_string}")
            try:
                if at_seg is not None:
                    # 同消息内 @ 发起人 + 图片，与 我的地图 / 用户地图 一致
                    await bot.send(event, at_seg + image)
                else:
                    await bot.send(event, image)
            finally:
                del b64_string
                response.payload.pop("base64", None)
            logger.info(
                f"查看地图发送成功：server_id={server.id} requester_user_id={requester_user_id}"
            )
            return

        # 非 V11 fallback
        try:
            png_bytes = base64.b64decode(b64_string, validate=True)
        except (binascii.Error, ValueError):
            await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
            return
        size_kb = len(png_bytes) // 1024
        del b64_string
        response.payload.pop("base64", None)

        async with temp_screenshot_path(
            f"explored-map-{server.id}"
        ) as screenshot_path:
            try:
                screenshot_path.write_bytes(png_bytes)
            except OSError:
                await bot.send(event, at + " " + reply_failure("查询", "保存图片失败"))
                return
            del png_bytes

            logger.info(
                f"查看地图发送成功：server_id={server.id} requester_user_id={requester_user_id} file={screenshot_path.name}"
            )
            # PQB-3.5：不暴露 /tmp 路径
            await bot.send(
                event,
                reply_block(
                    reply_success("查询"),
                    [
                        f"📁 文件：{screenshot_path.name}",
                        f"📦 大小：{size_kb} KB",
                    ],
                ),
            )


@progress_matcher.handle()
@command_control(
    command_key="player_query.progress",
    display_name="进度",
    permission="player_query.progress",
    description="查询世界进度并生成截图",
    usage="进度 <服务器 ID>",
    category="查询系统",
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

    user_id = event.get_user_id()
    at = safe_at_segment_or_empty(user_id)
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at + " " + reply_failure("查询", "服务器不存在"))
        return

    try:
        # PQB-4.1：拉到 15s（与 server_tools 执行的 ST-1.4 一致），世界进度在繁忙服务器偶有超过 5s
        response = await request_server_api(
            server,
            "/nextbot/world/progress",
            timeout=15.0,
        )
    except TShockRequestError:
        await bot.send(event, at + " " + reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, at + " " + reply_failure("查询", get_error_reason(response)))
        return

    # PQB-4.4：丢弃非 bool 字段时记一条 warning，便于发现 TShock 端字段类型变更
    payload_items = list(response.payload.items())
    progress = {
        _PROGRESS_NAME_MAP.get(k, k): v
        for k, v in payload_items
        if isinstance(v, bool)
    }
    dropped = [
        f"{k}({type(v).__name__})"
        for k, v in payload_items
        if not isinstance(v, bool) and k != "status"
    ]
    if dropped:
        logger.warning(
            f"世界进度返回非 bool 字段：server_id={server.id} dropped={','.join(dropped)}"
        )
    if not progress:
        await bot.send(event, at + " " + reply_failure("查询", "返回数据格式错误"))
        return

    page_url = create_progress_page(
        server_id=server.id,
        server_name=server.name,
        progress=progress,
    )
    # PQA-3.7 一致性：日志只保留诊断字段，不再打 page_url
    logger.info(f"世界进度渲染：server_id={server.id} user_id={user_id}")

    # PQB-4.x：per-server semaphore（max=2，与背包一致），helper 内置 base64
    # size cap + V11 / 非 V11 fallback。
    sem = _semaphore_for(_progress_semaphores, server.id, max_concurrent=2)
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=PROGRESS_SCREENSHOT_OPTIONS,
        file_prefix=f"progress-{server.id}",
        semaphore=sem,
        failure_action="查询",
    )
