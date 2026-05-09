from __future__ import annotations

import asyncio
import math
import unicodedata

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import func, update

from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import (
    WAREHOUSE_CAPACITY,
    Server,
    Shop,
    ShopItem,
    User,
    WarehouseItem,
    execute_rowcount,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import MAX_COINS_AMOUNT
from nextbot.progression import PROGRESSION_KEY_TO_ZH
from nextbot.screenshot_render import render_and_send_screenshot
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
from nextbot.time_utils import db_now_utc_naive
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.warehouse_lock import warehouse_lock
from server.screenshot import ScreenshotOptions
from server.web_server import create_shop_list_page, create_shop_view_page

shop_list_matcher = on_command("商店列表")
shop_view_matcher = on_command("查看商店")
shop_buy_matcher = on_command("购买商品")

# 单笔购买上限：防御 buy_count 性能炸弹（购买商品 1 1 1000000 → 100 万次循环）
MAX_BUY_COUNT = 9999
# 单笔物品总数量上限：与 warehouse 共享建议常量
MAX_ITEM_QUANTITY = 9999


def _safe_param_int(
    key: str, default: int, min_value: int = 0, max_value: int | None = None,
) -> int:
    """容错读取 int 参数。"""
    try:
        value = int(get_current_param(key, default))
    except (TypeError, ValueError):
        value = default
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _normalize_player_name(name: str) -> str:
    """unicode NFKC + casefold 折叠，用于跨全角/半角 + 大小写比对。"""
    return unicodedata.normalize("NFKC", str(name)).strip().casefold()


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

# 商店列表 / 商店详情共享同一组渲染并发上限，避免 guest 高频刷命令撑爆 Playwright
_shop_screenshot_semaphore = asyncio.Semaphore(2)


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
    # S-2.2：unicode NFKC + casefold，跨全角/半角 + 大小写折叠匹配
    target = _normalize_player_name(player_name)
    for p in players:
        if isinstance(p, dict):
            nickname = str(p.get("nickname", ""))
        else:
            nickname = str(p)
        if _normalize_player_name(nickname) == target:
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
    user_id = event.get_user_id()
    try:
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

        # S-Common.4：_safe_param_int 替代 int(get_current_param(...))
        limit = _safe_param_int("limit", 10, min_value=1, max_value=50)

        session = get_session()
        try:
            # S-3.2：单次 LEFT JOIN + 分页推到 SQL 端，避免全量取后切片 + N+1 count
            total = (
                session.query(Shop)
                .filter(Shop.enabled.is_(True))
                .count()
            )
            if total == 0:
                await bot.send(event, reply_failure("查询", "暂无可用商店"))
                return

            total_pages = max(1, math.ceil(total / limit))
            if page > total_pages:
                await bot.send(
                    event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"),
                )
                return
            offset = (page - 1) * limit

            item_count_subquery = (
                session.query(
                    ShopItem.shop_id,
                    func.count(ShopItem.id).label("item_count"),
                )
                .filter(ShopItem.enabled.is_(True))
                .group_by(ShopItem.shop_id)
                .subquery()
            )
            shops_with_count = (
                session.query(
                    Shop, func.coalesce(item_count_subquery.c.item_count, 0),
                )
                .outerjoin(item_count_subquery, Shop.id == item_count_subquery.c.shop_id)
                .filter(Shop.enabled.is_(True))
                .order_by(Shop.sort_order.asc(), Shop.id.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            render_entries: list[dict[str, object]] = [
                {
                    "shop_id": int(s.id),
                    "name": str(s.name),
                    "description": str(s.description or ""),
                    "item_count": int(cnt),
                }
                for s, cnt in shops_with_count
            ]
        finally:
            session.close()

        page_url = create_shop_list_page(
            entries=render_entries,
            page=page,
            total_pages=total_pages,
            total=total,
        )
        logger.info(
            f"商店列表渲染地址：page={page}/{total_pages} total={total} "
            f"item_count={len(render_entries)} internal_url={page_url}"
        )

        await render_and_send_screenshot(
            bot,
            event,
            page_url=page_url,
            options=SHOP_LIST_SCREENSHOT_OPTIONS,
            file_prefix="shop-list",
            semaphore=_shop_screenshot_semaphore,
            failure_action="查询",
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"商店列表处理异常：user_id={user_id}")
        try:
            await bot.send(event, reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return


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
            "description": "每页显示的商品数量（按 2 列网格布局，建议为偶数）",
            "required": False,
            "default": 20,
            "min": 1,
            "max": 100,
        },
    },
    category="商店系统",
)
@require_permission("shop.view")
async def handle_shop_view(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    try:
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

        # S-Common.4：_safe_param_int 替代 int(get_current_param(...))
        limit = _safe_param_int("limit", 20, min_value=1, max_value=100)

        session = get_session()
        try:
            shop = _load_shop_by_selector(session, selector)
            if shop is None:
                await bot.send(event, reply_failure("查询", f"未找到商店「{selector}」"))
                return
            if not shop.enabled:
                await bot.send(event, reply_failure("查询", "该商店未上架"))
                return
            shop_id = int(shop.id)
            shop_name = str(shop.name)
            shop_desc = str(shop.description or "")

            user = session.query(User).filter(User.user_id == user_id).first()
            user_name = str(user.name) if user is not None else "未注册用户"
            user_coins = int(user.coins) if user is not None else 0

            # S-3.2：分页推到 SQL 端，避免全量取后切片
            total = (
                session.query(ShopItem)
                .filter(ShopItem.shop_id == shop_id, ShopItem.enabled.is_(True))
                .count()
            )
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            if total > 0 and page > total_pages:
                await bot.send(
                    event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"),
                )
                return
            offset = (page - 1) * limit

            page_items = (
                session.query(ShopItem)
                .filter(ShopItem.shop_id == shop_id, ShopItem.enabled.is_(True))
                .order_by(ShopItem.sort_order.asc(), ShopItem.id.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )

            server_label_map: dict[int, str] = {
                int(s.id): str(s.name) for s in session.query(Server).all()
            }
            render_items: list[dict[str, object]] = []
            for it in page_items:
                entry: dict[str, object] = {
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
                    entry["command_template"] = (
                        str(it.command_template or "") if show_command else ""
                    )
                render_items.append(entry)
        finally:
            session.close()

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
        )
        logger.info(
            f"商店详情渲染地址：shop_id={shop_id} page={page}/{total_pages} "
            f"total={total} item_count={len(render_items)} internal_url={page_url}"
        )
        await render_and_send_screenshot(
            bot,
            event,
            page_url=page_url,
            options=SHOP_VIEW_SCREENSHOT_OPTIONS,
            file_prefix=f"shop-{shop_id}",
            semaphore=_shop_screenshot_semaphore,
            failure_action="查询",
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"查看商店处理异常：user_id={user_id}")
        try:
            await bot.send(event, reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return


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

    try:
        args = parse_command_args_with_fallback(event, arg, "购买商品")
        if not (2 <= len(args) <= 3):
            raise_command_usage()

        try:
            shop_id = int(args[0])
            shop_item_id = int(args[1])
            buy_count = int(args[2]) if len(args) == 3 else 1
        except ValueError:
            await bot.send(
                event, at + " " + reply_failure("购买", "商店 ID、商品 ID、数量必须为正整数"),
            )
            return
        if shop_id < 1 or shop_item_id < 1 or buy_count < 1:
            await bot.send(
                event, at + " " + reply_failure("购买", "商店 ID、商品 ID、数量必须为正整数"),
            )
            return
        # S-Common.2：buy_count 上界，防御性能炸弹（buy_count=1000000 → 100 万次循环）
        if buy_count > MAX_BUY_COUNT:
            await bot.send(
                event,
                at + " " + reply_failure("购买", f"购买数量过大（最多 {MAX_BUY_COUNT}）"),
            )
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
        # S-Common.2：总价上界，与 economy MAX_COINS_AMOUNT 一致
        if total_price > MAX_COINS_AMOUNT:
            await bot.send(
                event,
                at + " " + reply_failure("购买", f"总金额过大（最多 {MAX_COINS_AMOUNT}）"),
            )
            return

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
    except Exception:  # noqa: BLE001
        logger.exception(f"购买商品处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("购买", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return


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
    # S-Obs.1：单笔总数量上界，防御 admin 配 quantity=999999 + buy_count 极大
    if total_quantity > MAX_ITEM_QUANTITY:
        await bot.send(
            event,
            at + " " + reply_failure("购买", f"单笔总数量过大（最多 {MAX_ITEM_QUANTITY}）"),
        )
        return

    async with warehouse_lock(user_id):
        session = get_session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user is None:
                await bot.send(event, at + " " + reply_failure("购买", "请先注册账号"))
                return

            # S-3.1：第二段重读 ShopItem + Shop，校验 enabled，拦截 TOCTOU
            target_item = (
                session.query(ShopItem).filter(ShopItem.id == target_id).first()
            )
            if target_item is None or not target_item.enabled:
                await bot.send(
                    event, at + " " + reply_failure("购买", "商品已下架，请刷新后重试"),
                )
                return
            shop_now = session.query(Shop).filter(Shop.id == shop_id).first()
            if shop_now is None or not shop_now.enabled:
                await bot.send(
                    event, at + " " + reply_failure("购买", "商店已下架，请刷新后重试"),
                )
                return

            empty_slot = _find_first_empty_slot(session, user_id)
            if empty_slot is None:
                await bot.send(event, at + " " + reply_failure("购买", "仓库已满，请先释放格子"))
                return

            # S-Common.3：actual_value cap，防止绕过 economy MAX_COINS_AMOUNT 限额
            if actual_value is not None:
                unit_value = max(0, min(int(actual_value), MAX_COINS_AMOUNT))
            else:
                unit_value = unit_price // quantity_per_pack if quantity_per_pack > 0 else 0

            # S-1.1：原子条件 UPDATE 扣金币（与 economy F-2.1 同模板），
            # 拦截与转账 / 抢红包 / 签到等并发的 lost-update。
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(User.user_id == user_id, User.coins >= total_price)
                .values(coins=User.coins - total_price),
            )
            if rowcount == 0:
                coins_now = int(
                    session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
                )
                await bot.send(
                    event,
                    at + " " + reply_failure(
                        "购买", f"金币不足（需要 {total_price}，当前 {coins_now}）",
                    ),
                )
                return

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
            final_coins = int(
                session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
            )
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

    # Charge coins now (atomic UPDATE), then execute commands
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("购买", "用户记录已变更，请重试"))
            return

        # S-3.1：第二段重读 ShopItem，校验 enabled，拦截 TOCTOU 下架
        target_item = (
            session.query(ShopItem).filter(ShopItem.id == target_id).first()
        )
        if target_item is None or not target_item.enabled:
            await bot.send(
                event, at + " " + reply_failure("购买", "商品已下架，请刷新后重试"),
            )
            return

        # S-1.2：原子条件 UPDATE 扣金币（与 economy F-2.1 同模板），
        # 拦截与转账 / 抢红包 / 签到等并发的 lost-update。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id, User.coins >= total_price)
            .values(coins=User.coins - total_price),
        )
        if rowcount == 0:
            coins_now = int(
                session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
            )
            await bot.send(
                event,
                at + " " + reply_failure(
                    "购买", f"金币不足（需要 {total_price}，当前 {coins_now}）",
                ),
            )
            return
        session.commit()
        final_coins = int(
            session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
        )
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
    total_count = len(exec_results)

    # S-2.1：金币已扣 + 全部 TShock 调用失败 → 记录 CRITICAL 日志，
    # 在用户回复中加入显著告警引导联系管理员退款。
    all_failed = success_count == 0 and total_count > 0
    if all_failed:
        logger.error(
            f"[CRITICAL] 商店指令购买全部失败但金币已扣："
            f"user_id={user_id} shop_id={shop_id} item={target_name} "
            f"shop_item_id={target_id} total_price={total_price} buy_count={buy_count} "
            f"servers={[int(s.id) for s, _, _ in exec_results]}"
        )

    # S-2.2：全失败时切到 reply_failure 标题，避免「显示成功但实际全失败」的歧义。
    if all_failed:
        head = reply_failure("购买", "所有服务器执行失败")
        lines = [
            "⚠️ 已扣金币但所有服务器执行失败，请联系管理员退款",
            f"{EMOJI_SHOP} 商店：{shop_name}（ID {shop_id}）",
            f"⚙️ 商品：{target_name} ×{buy_count}",
            f"{EMOJI_USER} 玩家：{player_name}",
            f"{EMOJI_COIN} 花费：{total_price} 金币（单价 {unit_price}）",
            f"{EMOJI_SERVER} 执行结果：成功 {success_count} / 失败 {fail_count}（共 {len(online_servers)} 服）",
        ]
    else:
        head = reply_success("购买")
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
    await bot.send(event, at + "\n" + reply_block(head, lines))
