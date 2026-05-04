from __future__ import annotations

import base64
import math
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import (
    WAREHOUSE_CAPACITY,
    Server,
    Shop,
    ShopItem,
    User,
    WarehouseItem,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.progression import PROGRESSION_KEY_TO_ZH
from nextbot.render_utils import resolve_render_theme
from nextbot.text_utils import (
    EMOJI_COIN,
    EMOJI_SERVER,
    EMOJI_SHOP,
    EMOJI_TARGET,
    EMOJI_USER,
    EMOJI_WAREHOUSE,
    reply_block,
    reply_failure,
    reply_success,
)
from nextbot.time_utils import beijing_filename_timestamp, db_now_utc_naive
from nextbot.tshock_api import TShockRequestError, get_error_reason, is_success, request_server_api
from nextbot.warehouse_lock import warehouse_lock
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_shop_list_page, create_shop_view_page

shop_list_matcher = on_command("商店列表")
shop_view_matcher = on_command("查看商店")
shop_buy_matcher = on_command("购买商品")

SHOP_VIEW_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1200,
    viewport_height=600,
    full_page=True,
    fit_content_height=True,
)

SHOP_LIST_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=600,
    full_page=True,
    fit_content_height=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


def _load_shop_by_selector(session, selector: str) -> Shop | None:
    if selector.isdigit():
        shop = session.query(Shop).filter(Shop.id == int(selector)).first()
        if shop is not None:
            return shop
    return session.query(Shop).filter(Shop.name == selector).first()


def _list_active_items(session, shop_id: int) -> list[ShopItem]:
    return (
        session.query(ShopItem)
        .filter(ShopItem.shop_id == shop_id, ShopItem.enabled.is_(True))
        .order_by(ShopItem.sort_order.asc(), ShopItem.id.asc())
        .all()
    )


async def _issue_raw_command(server: Server, cmd: str) -> tuple[bool, str]:
    try:
        resp = await request_server_api(server, "/v3/server/rawcmd", params={"cmd": cmd})
    except TShockRequestError:
        return False, "无法连接服务器"
    if not is_success(resp):
        return False, get_error_reason(resp)
    return True, ""


async def _check_player_online(server: Server, player_name: str) -> tuple[bool | None, str]:
    try:
        resp = await request_server_api(
            server, "/v2/server/status", params={"players": "true"},
        )
    except TShockRequestError:
        return None, "无法连接服务器"
    if not is_success(resp):
        return None, get_error_reason(resp)
    players = resp.payload.get("players")
    if not isinstance(players, list):
        return None, "返回数据格式错误"
    name_lower = player_name.lower()
    for p in players:
        if isinstance(p, dict):
            nickname = str(p.get("nickname", "")).strip()
        else:
            nickname = str(p).strip()
        if nickname.lower() == name_lower:
            return True, ""
    return False, ""


def _find_first_empty_slot(session, user_id: str) -> int | None:
    occupied = {
        int(s.slot_index)
        for s in session.query(WarehouseItem)
        .filter(WarehouseItem.user_id == user_id)
        .all()
    }
    for i in range(1, WAREHOUSE_CAPACITY + 1):
        if i not in occupied:
            return i
    return None


@shop_list_matcher.handle()
@command_control(
    command_key="shop.list",
    display_name="商店列表",
    permission="shop.list",
    description="查看所有上架商店（图片）",
    usage="商店列表 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页条数",
            "description": "每页显示的商店数量",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
    category="商店系统",
)
@require_permission("shop.list")
async def handle_shop_list(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "商店列表")
    if len(args) > 1:
        raise_command_usage()

    page = 1
    if args:
        try:
            page = int(args[0])
        except ValueError:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return
        if page <= 0:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    session = get_session()
    try:
        shops = (
            session.query(Shop)
            .filter(Shop.enabled.is_(True))
            .order_by(Shop.sort_order.asc(), Shop.id.asc())
            .all()
        )
        all_entries: list[dict[str, object]] = []
        for shop in shops:
            count = (
                session.query(ShopItem)
                .filter(ShopItem.shop_id == shop.id, ShopItem.enabled.is_(True))
                .count()
            )
            all_entries.append({
                "shop_id": int(shop.id),
                "name": str(shop.name),
                "description": str(shop.description or ""),
                "item_count": int(count),
            })
    finally:
        session.close()

    total = len(all_entries)
    if total == 0:
        await bot.send(event, reply_failure("查询", "暂无可用商店"))
        return

    total_pages = max(1, math.ceil(total / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return
    offset = (page - 1) * limit
    render_entries = all_entries[offset:offset + limit]

    page_url = create_shop_list_page(
        entries=render_entries,
        page=page,
        total_pages=total_pages,
        total=total,
        theme=resolve_render_theme(),
    )
    logger.info(
        f"商店列表渲染地址：page={page}/{total_pages} total={total} "
        f"item_count={len(render_entries)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"shop-list-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=SHOP_LIST_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", str(exc)))
        return

    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


@shop_view_matcher.handle()
@command_control(
    command_key="shop.view",
    display_name="查看商店",
    permission="shop.view",
    description="查看具体商店内容（图片）",
    usage="查看商店 <商店 ID/商店名称> [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页条数",
            "description": "每页显示的商品数量",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
    category="商店系统",
)
@require_permission("shop.view")
async def handle_shop_view(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "查看商店")
    if not (1 <= len(args) <= 2):
        raise_command_usage()
    selector = args[0].strip()
    if not selector:
        raise_command_usage()

    page = 1
    if len(args) == 2:
        try:
            page = int(args[1])
        except ValueError:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return
        if page <= 0:
            await bot.send(event, reply_failure("查询", "页数必须为正整数"))
            return

    limit = max(1, min(int(get_current_param("limit", 10)), 50))

    user_id = event.get_user_id()
    session = get_session()
    try:
        shop = _load_shop_by_selector(session, selector)
        if shop is None:
            await bot.send(event, reply_failure("查询", f"未找到商店「{selector}」"))
            return
        if not shop.enabled:
            await bot.send(event, reply_failure("查询", "该商店未上架"))
            return
        items = _list_active_items(session, int(shop.id))
        user = session.query(User).filter(User.user_id == user_id).first()
        user_name = str(user.name) if user is not None else "未注册用户"
        user_coins = int(user.coins) if user is not None else 0
        shop_id = int(shop.id)
        shop_name = str(shop.name)
        shop_desc = str(shop.description or "")

        server_label_map: dict[int, str] = {
            int(s.id): str(s.name) for s in session.query(Server).all()
        }
        all_entries = []
        for it in items:
            entry = {
                "shop_item_id": int(it.id),
                "name": str(it.name),
                "description": str(it.description or ""),
                "kind": str(it.kind),
                "price": int(it.price),
            }
            if it.kind == "item":
                entry.update({
                    "item_id": int(it.item_id or 0),
                    "prefix_id": int(it.prefix_id or 0),
                    "quantity": int(it.quantity or 1),
                    "min_tier": str(it.min_tier or "none"),
                    "is_mystery": bool(getattr(it, "is_mystery", False)),
                })
            else:
                entry["target_server_id"] = (
                    int(it.target_server_id) if it.target_server_id is not None else None
                )
                if it.target_server_id is None:
                    entry["target_server_label"] = "全部服务器"
                else:
                    entry["target_server_label"] = server_label_map.get(
                        int(it.target_server_id), f"#{int(it.target_server_id)}"
                    )
                show_command = bool(getattr(it, "show_command", False))
                entry["command_template"] = str(it.command_template or "") if show_command else ""
            all_entries.append(entry)
    finally:
        session.close()

    total = len(all_entries)
    total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
    if total > 0 and page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return
    offset = (page - 1) * limit
    render_items = all_entries[offset:offset + limit]

    page_url = create_shop_view_page(
        shop_id=shop_id,
        shop_name=shop_name,
        shop_description=shop_desc,
        user_user_id=user_id,
        user_user_name=user_name,
        user_coins=user_coins,
        items=render_items,
        page=page,
        total_pages=total_pages,
        total=total,
        theme=resolve_render_theme(),
    )
    logger.info(
        f"商店详情渲染地址：shop_id={shop_id} page={page}/{total_pages} "
        f"total={total} item_count={len(render_items)} internal_url={page_url}"
    )
    screenshot_path = Path("/tmp") / f"shop-{shop_id}-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=SHOP_VIEW_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", str(exc)))
        return

    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


@shop_buy_matcher.handle()
@command_control(
    command_key="shop.buy",
    display_name="购买商品",
    permission="shop.buy",
    description="购买某个商店的指定商品；物品送入仓库，指令立即执行",
    usage="购买商品 <商店 ID> <商品 ID> [数量]",
    category="商店系统",
)
@require_permission("shop.buy")
async def handle_shop_buy(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))

    args = parse_command_args_with_fallback(event, arg, "购买商品")
    if not (2 <= len(args) <= 3):
        raise_command_usage()

    try:
        shop_id = int(args[0])
        shop_item_id = int(args[1])
        buy_count = int(args[2]) if len(args) == 3 else 1
    except ValueError:
        await bot.send(event, at + " " + reply_failure("购买", "商店 ID、商品 ID、数量必须为正整数"))
        return
    if shop_id < 1 or shop_item_id < 1 or buy_count < 1:
        await bot.send(event, at + " " + reply_failure("购买", "商店 ID、商品 ID、数量必须为正整数"))
        return

    # First pass: load shop, validate, pick the target item by shop_item_id
    session = get_session()
    try:
        shop = session.query(Shop).filter(Shop.id == shop_id).first()
        if shop is None or not shop.enabled:
            await bot.send(event, at + " " + reply_failure("购买", "商店不存在或未上架"))
            return
        target = (
            session.query(ShopItem)
            .filter(
                ShopItem.id == shop_item_id,
                ShopItem.shop_id == shop_id,
                ShopItem.enabled.is_(True),
            )
            .first()
        )
        if target is None:
            await bot.send(event, at + " " + reply_failure("购买", "商品不存在或未上架"))
            return
        target_id = int(target.id)
        target_name = str(target.name)
        target_kind = str(target.kind)
        target_price = int(target.price)
        target_item_id = int(target.item_id or 0)
        target_prefix_id = int(target.prefix_id or 0)
        target_quantity_per_pack = int(target.quantity or 1)
        target_min_tier = str(target.min_tier or "none")
        raw_actual = getattr(target, "actual_value", None)
        target_actual_value = int(raw_actual) if raw_actual is not None else None
        target_server_id = (
            int(target.target_server_id) if target.target_server_id is not None else None
        )
        target_command_template = str(target.command_template or "")
        target_require_online = bool(getattr(target, "require_online", False))
        shop_name = str(shop.name)
    finally:
        session.close()

    total_price = target_price * buy_count

    if target_kind == "item":
        await _buy_item(
            bot=bot, event=event, at=at, user_id=user_id,
            shop_id=shop_id, shop_name=shop_name,
            target_id=target_id, target_name=target_name,
            unit_price=target_price, total_price=total_price,
            buy_count=buy_count,
            item_id=target_item_id, prefix_id=target_prefix_id,
            quantity_per_pack=target_quantity_per_pack, min_tier=target_min_tier,
            actual_value=target_actual_value,
        )
    else:
        await _buy_command(
            bot=bot, event=event, at=at, user_id=user_id,
            shop_id=shop_id, shop_name=shop_name,
            target_id=target_id, target_name=target_name,
            unit_price=target_price, total_price=total_price,
            buy_count=buy_count,
            target_server_id=target_server_id,
            command_template=target_command_template,
            require_online=target_require_online,
        )


async def _buy_item(
    *,
    bot: Bot, event: Event, at: object, user_id: str,
    shop_id: int, shop_name: str,
    target_id: int, target_name: str,
    unit_price: int, total_price: int, buy_count: int,
    item_id: int, prefix_id: int, quantity_per_pack: int, min_tier: str,
    actual_value: int | None,
) -> None:
    total_quantity = quantity_per_pack * buy_count
    async with warehouse_lock(user_id):
        session = get_session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user is None:
                await bot.send(event, at + " " + reply_failure("购买", "请先注册账号"))
                return
            coins = int(user.coins or 0)
            if coins < total_price:
                await bot.send(
                    event,
                    at + " " + reply_failure("购买", f"金币不足（需要 {total_price}，当前 {coins}）"),
                )
                return
            empty_slot = _find_first_empty_slot(session, user_id)
            if empty_slot is None:
                await bot.send(event, at + " " + reply_failure("购买", "仓库已满，请先释放格子"))
                return

            user.coins = coins - total_price
            if actual_value is not None:
                unit_value = max(0, int(actual_value))
            else:
                unit_value = unit_price // quantity_per_pack if quantity_per_pack > 0 else 0
            new_item = WarehouseItem(
                user_id=user_id,
                slot_index=empty_slot,
                item_id=item_id,
                prefix_id=prefix_id,
                quantity=total_quantity,
                min_tier=min_tier,
                value=int(unit_value),
                created_at=db_now_utc_naive(),
            )
            session.add(new_item)
            session.commit()
            final_coins = int(user.coins)
        finally:
            session.close()

    lines = [
        f"{EMOJI_SHOP} 商店：{shop_name}（ID {shop_id}）",
        f"🎁 商品：{target_name} ×{buy_count}",
        f"{EMOJI_WAREHOUSE} 入库格子：#{empty_slot}（数量 {total_quantity}）",
    ]
    if min_tier and min_tier != "none":
        tier_zh = PROGRESSION_KEY_TO_ZH.get(min_tier, min_tier)
        lines.append(f"{EMOJI_TARGET} 最低进度：{tier_zh}")
    lines.extend([
        f"{EMOJI_COIN} 花费：{total_price} 金币（单价 {unit_price}）",
        f"{EMOJI_COIN} 当前金币：{final_coins}",
    ])
    logger.info(
        f"商店购买物品成功：user_id={user_id} shop_id={shop_id} item={target_name} "
        f"shop_item_id={target_id} count={buy_count} total_quantity={total_quantity} "
        f"price={total_price} slot={empty_slot}"
    )
    await bot.send(event, at + "\n" + reply_block(reply_success("购买"), lines))


async def _buy_command(
    *,
    bot: Bot, event: Event, at: object, user_id: str,
    shop_id: int, shop_name: str,
    target_id: int, target_name: str,
    unit_price: int, total_price: int, buy_count: int,
    target_server_id: int | None,
    command_template: str,
    require_online: bool,
) -> None:
    # Load player + servers; optionally verify online
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("购买", "请先注册账号"))
            return
        coins = int(user.coins or 0)
        if coins < total_price:
            await bot.send(
                event,
                at + " " + reply_failure("购买", f"金币不足（需要 {total_price}，当前 {coins}）"),
            )
            return
        player_name = str(user.name)
        if target_server_id is None:
            servers = session.query(Server).order_by(Server.id.asc()).all()
            servers = list(servers)
        else:
            srv = session.query(Server).filter(Server.id == target_server_id).first()
            if srv is None:
                await bot.send(event, at + " " + reply_failure("购买", "目标服务器已不存在"))
                return
            servers = [srv]
    finally:
        session.close()

    if not servers:
        await bot.send(event, at + " " + reply_failure("购买", "暂无可用服务器"))
        return

    offline_reasons: list[str] = []
    if require_online:
        online_servers: list[Server] = []
        for srv in servers:
            online, reason = await _check_player_online(srv, player_name)
            if online is True:
                online_servers.append(srv)
            elif online is False:
                offline_reasons.append(f"#{srv.id} {srv.name}：玩家不在线")
            else:
                offline_reasons.append(f"#{srv.id} {srv.name}：{reason or '查询失败'}")

        if not online_servers:
            if offline_reasons:
                await bot.send(
                    event,
                    at + "\n" + reply_block(
                        reply_failure("购买", "无可用的目标服务器"),
                        [f"  ❌ {r}" for r in offline_reasons],
                    ),
                )
            else:
                await bot.send(event, at + " " + reply_failure("购买", "玩家未在线"))
            return
    else:
        online_servers = list(servers)

    # Charge coins now (commit), then execute commands
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("购买", "用户记录已变更，请重试"))
            return
        coins = int(user.coins or 0)
        if coins < total_price:
            await bot.send(
                event,
                at + " " + reply_failure("购买", f"金币不足（需要 {total_price}，当前 {coins}）"),
            )
            return
        user.coins = coins - total_price
        session.commit()
        final_coins = int(user.coins)
    finally:
        session.close()

    cmd = command_template.replace("{player}", player_name)

    exec_results: list[tuple[Server, bool, str]] = []
    for srv in online_servers:
        for _ in range(buy_count):
            ok, reason = await _issue_raw_command(srv, cmd)
            exec_results.append((srv, ok, reason))

    success_count = sum(1 for _, ok, _ in exec_results if ok)
    fail_count = len(exec_results) - success_count

    lines = [
        f"{EMOJI_SHOP} 商店：{shop_name}（ID {shop_id}）",
        f"⚙️ 商品：{target_name} ×{buy_count}",
        f"{EMOJI_USER} 玩家：{player_name}",
        f"{EMOJI_COIN} 花费：{total_price} 金币（单价 {unit_price}）",
        f"{EMOJI_SERVER} 执行结果：成功 {success_count} / 失败 {fail_count}（共 {len(online_servers)} 服）",
    ]
    for srv, ok, reason in exec_results:
        mark = "✅" if ok else "❌"
        suffix = "" if ok else f"（{reason}）" if reason else "（失败）"
        lines.append(f"  {mark} #{srv.id} {srv.name}{suffix}")
    if offline_reasons:
        lines.append(f"⚠️ 跳过 {len(offline_reasons)} 个服务器：")
        for r in offline_reasons:
            lines.append(f"  · {r}")
    lines.append(f"{EMOJI_COIN} 当前金币：{final_coins}")

    logger.info(
        f"商店购买指令完成：user_id={user_id} shop_id={shop_id} item={target_name} "
        f"shop_item_id={target_id} count={buy_count} price={total_price} "
        f"online_servers={len(online_servers)} success={success_count} fail={fail_count}"
    )
    await bot.send(event, at + "\n" + reply_block(reply_success("购买"), lines))
