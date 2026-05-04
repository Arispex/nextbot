from __future__ import annotations

import base64
import math
import random
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import (
    WAREHOUSE_CAPACITY,
    LotteryPool,
    LotteryPrize,
    Server,
    User,
    WarehouseItem,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.render_utils import resolve_render_theme
from nextbot.text_utils import reply_failure
from nextbot.time_utils import beijing_filename_timestamp, db_now_utc_naive
from nextbot.tshock_api import TShockRequestError, get_error_reason, is_success, request_server_api
from nextbot.warehouse_lock import warehouse_lock
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import (
    create_lottery_list_page,
    create_lottery_result_page,
    create_lottery_view_page,
)

lottery_list_matcher = on_command("奖池列表")
lottery_view_matcher = on_command("查看奖池")
lottery_draw_matcher = on_command("抽奖")

LOTTERY_LIST_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920, viewport_height=600, full_page=True, fit_content_height=True,
)
LOTTERY_VIEW_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1200, viewport_height=600, full_page=True, fit_content_height=True,
)
LOTTERY_RESULT_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920, viewport_height=600, full_page=True, fit_content_height=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    return f"base64://{base64.b64encode(raw).decode('ascii')}"


def _load_pool_by_selector(session, selector: str) -> LotteryPool | None:
    if selector.isdigit():
        pool = session.query(LotteryPool).filter(LotteryPool.id == int(selector)).first()
        if pool is not None:
            return pool
    return session.query(LotteryPool).filter(LotteryPool.name == selector).first()


def _list_active_prizes(session, pool_id: int) -> list[LotteryPrize]:
    return (
        session.query(LotteryPrize)
        .filter(LotteryPrize.pool_id == pool_id, LotteryPrize.enabled.is_(True))
        .order_by(LotteryPrize.sort_order.asc(), LotteryPrize.id.asc())
        .all()
    )


def _resolve_probabilities(prizes: list[LotteryPrize]) -> tuple[list[tuple[LotteryPrize, float]], float]:
    """Returns ([(prize, probability_pct), ...], miss_probability_pct).

    Prizes with weight=NULL share the remaining probability equally.
    If all prizes have weights set and sum < 100, the rest becomes miss.
    """
    set_prizes = [(p, float(p.weight)) for p in prizes if p.weight is not None]
    unset_prizes = [p for p in prizes if p.weight is None]
    set_total = sum(max(0.0, min(100.0, w)) for _, w in set_prizes)
    set_total = max(0.0, min(100.0, set_total))
    remaining = max(0.0, 100.0 - set_total)
    if unset_prizes:
        per_unset = remaining / len(unset_prizes)
        miss_pct = 0.0
    else:
        per_unset = 0.0
        miss_pct = remaining
    resolved = [(p, max(0.0, min(100.0, w))) for p, w in set_prizes]
    for p in unset_prizes:
        resolved.append((p, per_unset))
    return resolved, miss_pct


def _draw_one(resolved: list[tuple[LotteryPrize, float]], miss_pct: float) -> LotteryPrize | None:
    """Returns the prize hit, or None for miss."""
    roll = random.uniform(0.0, 100.0)
    cumulative = 0.0
    for prize, prob in resolved:
        cumulative += prob
        if roll < cumulative:
            return prize
    return None  # miss


async def _issue_raw_command(server: Server, cmd: str) -> tuple[bool, str]:
    try:
        resp = await request_server_api(server, "/v3/server/rawcmd", params={"cmd": cmd})
    except TShockRequestError:
        return False, "无法连接服务器"
    if not is_success(resp):
        return False, get_error_reason(resp)
    return True, ""


async def _check_player_online(server: Server, player_name: str) -> bool:
    try:
        resp = await request_server_api(
            server, "/v2/server/status", params={"players": "true"},
        )
    except TShockRequestError:
        return False
    if not is_success(resp):
        return False
    players = resp.payload.get("players")
    if not isinstance(players, list):
        return False
    name_lower = player_name.lower()
    for p in players:
        nickname = str(p.get("nickname", "")).strip() if isinstance(p, dict) else str(p).strip()
        if nickname.lower() == name_lower:
            return True
    return False


def _find_empty_slots(session, user_id: str, needed: int) -> list[int]:
    occupied = {
        int(s.slot_index)
        for s in session.query(WarehouseItem).filter(WarehouseItem.user_id == user_id).all()
    }
    empty: list[int] = []
    for i in range(1, WAREHOUSE_CAPACITY + 1):
        if i not in occupied:
            empty.append(i)
            if len(empty) >= needed:
                break
    return empty


# ---------- 奖池列表 ----------

@lottery_list_matcher.handle()
@command_control(
    command_key="lottery.list",
    display_name="奖池列表",
    permission="lottery.list",
    description="查看所有上架奖池（图片）",
    usage="奖池列表 [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页条数",
            "description": "每页显示的奖池数量",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="抽奖系统",
)
@require_permission("lottery.list")
async def handle_lottery_list(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "奖池列表")
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
        pools = (
            session.query(LotteryPool)
            .filter(LotteryPool.enabled.is_(True))
            .order_by(LotteryPool.sort_order.asc(), LotteryPool.id.asc())
            .all()
        )
        all_entries: list[dict[str, object]] = []
        for pool in pools:
            count = (
                session.query(LotteryPrize)
                .filter(LotteryPrize.pool_id == pool.id, LotteryPrize.enabled.is_(True))
                .count()
            )
            all_entries.append({
                "pool_id": int(pool.id),
                "name": str(pool.name),
                "description": str(pool.description or ""),
                "prize_count": int(count),
                "cost_per_draw": int(pool.cost_per_draw or 0),
            })
    finally:
        session.close()

    total = len(all_entries)
    if total == 0:
        await bot.send(event, reply_failure("查询", "暂无可用奖池"))
        return

    total_pages = max(1, math.ceil(total / limit))
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return
    offset = (page - 1) * limit
    render_entries = all_entries[offset:offset + limit]

    page_url = create_lottery_list_page(
        entries=render_entries, page=page, total_pages=total_pages,
        total=total, theme=resolve_render_theme(),
    )
    logger.info(
        f"奖池列表渲染地址：page={page}/{total_pages} total={total} "
        f"item_count={len(render_entries)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"lottery-list-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=LOTTERY_LIST_SCREENSHOT_OPTIONS)
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


# ---------- 查看奖池 ----------

@lottery_view_matcher.handle()
@command_control(
    command_key="lottery.view",
    display_name="查看奖池",
    permission="lottery.view",
    description="查看具体奖池内容（图片）",
    usage="查看奖池 <奖池 ID/奖池名称> [页数]",
    params={
        "limit": {
            "type": "int", "label": "每页条数",
            "description": "每页显示的奖品数量",
            "required": False, "default": 10, "min": 1, "max": 50,
        },
    },
    category="抽奖系统",
)
@require_permission("lottery.view")
async def handle_lottery_view(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "查看奖池")
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

    session = get_session()
    try:
        pool = _load_pool_by_selector(session, selector)
        if pool is None:
            await bot.send(event, reply_failure("查询", f"未找到奖池「{selector}」"))
            return
        if not pool.enabled:
            await bot.send(event, reply_failure("查询", "该奖池未上架"))
            return
        prizes = _list_active_prizes(session, int(pool.id))
        pool_id = int(pool.id)
        pool_name = str(pool.name)
        pool_desc = str(pool.description or "")
        cost_per_draw = int(pool.cost_per_draw or 0)

        server_label_map: dict[int, str] = {
            int(s.id): str(s.name) for s in session.query(Server).all()
        }
        resolved, miss_pct = _resolve_probabilities(prizes)
        prob_by_id = {p.id: prob for p, prob in resolved}

        all_entries: list[dict[str, object]] = []
        for prize in prizes:
            entry: dict[str, object] = {
                "name": str(prize.name),
                "description": str(prize.description or ""),
                "kind": str(prize.kind),
                "probability": float(prob_by_id.get(prize.id, 0.0)),
            }
            if prize.kind == "item":
                entry.update({
                    "item_id": int(prize.item_id or 0),
                    "prefix_id": int(prize.prefix_id or 0),
                    "quantity": int(prize.quantity or 1),
                    "min_tier": str(prize.min_tier or "none"),
                    "is_mystery": bool(getattr(prize, "is_mystery", False)),
                })
            elif prize.kind == "command":
                entry["target_server_id"] = (
                    int(prize.target_server_id) if prize.target_server_id is not None else None
                )
                entry["target_server_label"] = (
                    "全部服务器" if prize.target_server_id is None
                    else server_label_map.get(int(prize.target_server_id), f"#{int(prize.target_server_id)}")
                )
                entry["command_template"] = str(prize.command_template or "") if getattr(prize, "show_command", False) else ""
            else:  # coin
                entry["coin_amount"] = int(prize.coin_amount or 0)
            all_entries.append(entry)
    finally:
        session.close()

    total = len(all_entries)
    total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
    if total > 0 and page > total_pages:
        await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
        return
    offset = (page - 1) * limit
    render_prizes = all_entries[offset:offset + limit]

    page_url = create_lottery_view_page(
        pool_id=pool_id, pool_name=pool_name, pool_description=pool_desc,
        cost_per_draw=cost_per_draw, prizes=render_prizes,
        miss_probability=float(miss_pct),
        page=page, total_pages=total_pages, total=total,
        theme=resolve_render_theme(),
    )
    logger.info(
        f"奖池详情渲染地址：pool_id={pool_id} page={page}/{total_pages} "
        f"total={total} miss_pct={miss_pct:.2f} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"lottery-view-{pool_id}-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=LOTTERY_VIEW_SCREENSHOT_OPTIONS)
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


# ---------- 抽奖 ----------

@lottery_draw_matcher.handle()
@command_control(
    command_key="lottery.draw",
    display_name="抽奖",
    permission="lottery.draw",
    description="在指定奖池中抽奖（图片）",
    usage="抽奖 <奖池 ID/奖池名称> [次数]",
    params={
        "max_draws": {
            "type": "int", "label": "单次最大抽奖次数",
            "description": "一次命令允许抽奖的最大次数",
            "required": False, "default": 10, "min": 1, "max": 100,
        },
    },
    category="抽奖系统",
)
@require_permission("lottery.draw")
async def handle_lottery_draw(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))

    args = parse_command_args_with_fallback(event, arg, "抽奖")
    if not (1 <= len(args) <= 2):
        raise_command_usage()
    selector = args[0].strip()
    if not selector:
        raise_command_usage()

    draw_count = 1
    if len(args) == 2:
        try:
            draw_count = int(args[1])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("抽奖", "次数必须为正整数"))
            return
        if draw_count <= 0:
            await bot.send(event, at + " " + reply_failure("抽奖", "次数必须为正整数"))
            return

    max_draws = max(1, min(int(get_current_param("max_draws", 10)), 100))
    if draw_count > max_draws:
        await bot.send(event, at + " " + reply_failure("抽奖", f"单次抽奖次数不能超过 {max_draws}"))
        return

    # Load pool + prizes + player info
    session = get_session()
    try:
        pool = _load_pool_by_selector(session, selector)
        if pool is None:
            await bot.send(event, at + " " + reply_failure("抽奖", f"未找到奖池「{selector}」"))
            return
        if not pool.enabled:
            await bot.send(event, at + " " + reply_failure("抽奖", "该奖池未上架"))
            return
        prizes = _list_active_prizes(session, int(pool.id))
        if not prizes:
            await bot.send(event, at + " " + reply_failure("抽奖", "该奖池暂无可中奖的奖品"))
            return

        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("抽奖", "请先注册账号"))
            return
        coins = int(user.coins or 0)
        cost_per_draw = int(pool.cost_per_draw or 0)
        total_cost = cost_per_draw * draw_count
        if coins < total_cost:
            await bot.send(
                event,
                at + " " + reply_failure("抽奖", f"金币不足（需要 {total_cost}，当前 {coins}）"),
            )
            return

        pool_id = int(pool.id)
        pool_name = str(pool.name)
        player_name = str(user.name)
        # snapshot prize fields needed downstream
        prize_snapshots = {
            int(p.id): {
                "id": int(p.id),
                "name": str(p.name),
                "kind": str(p.kind),
                "is_mystery": bool(getattr(p, "is_mystery", False)),
                "item_id": int(p.item_id or 0),
                "prefix_id": int(p.prefix_id or 0),
                "quantity": int(p.quantity or 1),
                "min_tier": str(p.min_tier or "none"),
                "actual_value": int(p.actual_value) if getattr(p, "actual_value", None) is not None else None,
                "target_server_id": int(p.target_server_id) if p.target_server_id is not None else None,
                "command_template": str(p.command_template or ""),
                "require_online": bool(getattr(p, "require_online", False)),
                "coin_amount": int(p.coin_amount or 0),
                "unit_price": int(pool.cost_per_draw or 0),
            }
            for p in prizes
        }
        all_servers_snapshot = [
            {"id": int(s.id), "name": str(s.name)} for s in session.query(Server).all()
        ]
        resolved, miss_pct = _resolve_probabilities(prizes)
        draw_prob_by_id = {int(p.id): float(prob) for p, prob in resolved}
    finally:
        session.close()

    # Roll dice N times
    rolled_prize_ids: list[int | None] = []
    for _ in range(draw_count):
        prize = _draw_one(resolved, miss_pct)
        rolled_prize_ids.append(int(prize.id) if prize is not None else None)

    # Bucket outcomes by prize id (None = miss)
    bucket: dict[int | None, int] = {}
    for pid in rolled_prize_ids:
        bucket[pid] = bucket.get(pid, 0) + 1

    # Pre-flight: count distinct items needed and check warehouse capacity
    item_prize_ids = [pid for pid in bucket.keys() if pid is not None and prize_snapshots[pid]["kind"] == "item"]
    needed_slots = len(item_prize_ids)

    # Pre-flight: command online check
    server_label_map = {s["id"]: s["name"] for s in all_servers_snapshot}
    online_required_servers: dict[int, list[Server]] = {}  # prize_id -> server list to send
    server_online_cache: dict[tuple[int, str], bool] = {}

    async def _check_online_cached(srv_id: int, srv_name: str, srv_obj: Server, player: str) -> bool:
        key = (srv_id, player)
        if key in server_online_cache:
            return server_online_cache[key]
        ok = await _check_player_online(srv_obj, player)
        server_online_cache[key] = ok
        return ok

    # Re-load Server objects (need actual ORM instances)
    session = get_session()
    try:
        all_servers_orm = {int(s.id): s for s in session.query(Server).all()}
    finally:
        session.close()

    # For each command-prize hit, decide which servers to send to
    cmd_plan: list[tuple[int, list[Server]]] = []  # (prize_id, [servers_to_send])
    cmd_skip_reasons: list[str] = []
    for pid, count in bucket.items():
        if pid is None:
            continue
        snap = prize_snapshots[pid]
        if snap["kind"] != "command":
            continue
        target_id = snap["target_server_id"]
        if target_id is None:
            target_servers = list(all_servers_orm.values())
        else:
            srv = all_servers_orm.get(target_id)
            if srv is None:
                cmd_skip_reasons.append(f"奖品「{snap['name']}」目标服务器已不存在")
                continue
            target_servers = [srv]

        if snap["require_online"]:
            online = []
            for srv in target_servers:
                if await _check_online_cached(int(srv.id), str(srv.name), srv, player_name):
                    online.append(srv)
            if not online:
                cmd_skip_reasons.append(f"奖品「{snap['name']}」需要玩家在线，但无在线服务器")
                continue
            target_servers = online

        cmd_plan.append((pid, target_servers))

    if needed_slots > 0:
        async with warehouse_lock(user_id):
            session = get_session()
            try:
                empty_slots = _find_empty_slots(session, user_id, needed_slots)
                if len(empty_slots) < needed_slots:
                    await bot.send(
                        event,
                        at + " " + reply_failure("抽奖", f"仓库剩余空格不足（需要 {needed_slots} 格，剩余 {len(empty_slots)} 格）"),
                    )
                    return

                # Charge + insert items + add coins
                user = session.query(User).filter(User.user_id == user_id).first()
                if user is None:
                    await bot.send(event, at + " " + reply_failure("抽奖", "用户记录已变更，请重试"))
                    return
                current_coins = int(user.coins or 0)
                if current_coins < total_cost:
                    await bot.send(
                        event,
                        at + " " + reply_failure("抽奖", f"金币不足（需要 {total_cost}，当前 {current_coins}）"),
                    )
                    return

                user.coins = current_coins - total_cost
                # Insert item prizes (tracking total appraised value gained)
                slot_iter = iter(empty_slots)
                item_value_gained = 0
                for pid in item_prize_ids:
                    snap = prize_snapshots[pid]
                    count = bucket[pid]
                    total_qty = snap["quantity"] * count
                    actual_value = snap.get("actual_value")
                    if actual_value is not None:
                        unit_value = max(0, int(actual_value))
                    else:
                        per_pack = max(1, snap["quantity"])
                        unit_value = snap["unit_price"] // per_pack if per_pack > 0 else 0
                    item_value_gained += unit_value * total_qty
                    session.add(WarehouseItem(
                        user_id=user_id,
                        slot_index=next(slot_iter),
                        item_id=snap["item_id"],
                        prefix_id=snap["prefix_id"],
                        quantity=total_qty,
                        min_tier=snap["min_tier"],
                        value=int(unit_value),
                        created_at=db_now_utc_naive(),
                    ))

                # Apply coin prizes
                coin_delta = 0
                for pid, count in bucket.items():
                    if pid is None:
                        continue
                    snap = prize_snapshots[pid]
                    if snap["kind"] == "coin":
                        coin_delta += int(snap["coin_amount"]) * count
                if coin_delta:
                    user.coins = int(user.coins) + coin_delta
                final_coins = int(user.coins)
                session.commit()
            finally:
                session.close()
    else:
        # No item prizes — still need to charge + apply coin prizes
        item_value_gained = 0
        session = get_session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user is None:
                await bot.send(event, at + " " + reply_failure("抽奖", "用户记录已变更，请重试"))
                return
            current_coins = int(user.coins or 0)
            if current_coins < total_cost:
                await bot.send(
                    event,
                    at + " " + reply_failure("抽奖", f"金币不足（需要 {total_cost}，当前 {current_coins}）"),
                )
                return
            user.coins = current_coins - total_cost
            coin_delta = 0
            for pid, count in bucket.items():
                if pid is None:
                    continue
                snap = prize_snapshots[pid]
                if snap["kind"] == "coin":
                    coin_delta += int(snap["coin_amount"]) * count
            if coin_delta:
                user.coins = int(user.coins) + coin_delta
            final_coins = int(user.coins)
            session.commit()
        finally:
            session.close()

    # Execute command prizes (after charging — failures don't refund)
    cmd_results: list[dict[str, object]] = []
    for pid, servers in cmd_plan:
        snap = prize_snapshots[pid]
        count = bucket[pid]
        cmd_text = snap["command_template"].replace("{player}", player_name)
        for srv in servers:
            for _ in range(count):
                ok, reason = await _issue_raw_command(srv, cmd_text)
                cmd_results.append({
                    "server_label": f"#{srv.id} {srv.name}",
                    "ok": ok,
                    "reason": reason,
                })

    # Build outcomes for render
    outcomes: list[dict[str, object]] = []
    # Sort: items first, then commands, then coins, then misses
    kind_order = {"item": 0, "command": 1, "coin": 2, "miss": 3}
    items_sorted = sorted(
        bucket.items(),
        key=lambda kv: (kind_order.get(prize_snapshots[kv[0]]["kind"] if kv[0] is not None else "miss", 9), -kv[1]),
    )
    for pid, count in items_sorted:
        if pid is None:
            outcomes.append({
                "kind": "miss", "count": count, "name": "谢谢参与",
                "probability": float(miss_pct),
            })
        else:
            snap = prize_snapshots[pid]
            entry: dict[str, object] = {
                "kind": snap["kind"],
                "count": count,
                "name": snap["name"],
                "is_mystery": snap["is_mystery"],
                "probability": float(draw_prob_by_id.get(pid, 0.0)),
            }
            if snap["kind"] == "item":
                entry["item_id"] = snap["item_id"]
                entry["prefix_id"] = snap["prefix_id"]
                entry["quantity"] = snap["quantity"]
            elif snap["kind"] == "coin":
                entry["coin_amount"] = snap["coin_amount"]
            outcomes.append(entry)

    page_url = create_lottery_result_page(
        pool_id=pool_id, pool_name=pool_name,
        user_user_id=user_id, user_user_name=player_name,
        user_coins_after=final_coins, draw_count=draw_count,
        total_cost=total_cost, coin_delta=coin_delta,
        item_value_gained=item_value_gained,
        outcomes=outcomes, item_slots_used=needed_slots,
        command_results=cmd_results, theme=resolve_render_theme(),
    )
    logger.info(
        f"抽奖结果渲染地址：user_id={user_id} pool_id={pool_id} draws={draw_count} "
        f"cost={total_cost} coin_delta={coin_delta} item_value_gained={item_value_gained} "
        f"item_slots={needed_slots} cmd_executions={len(cmd_results)} "
        f"skipped={len(cmd_skip_reasons)} internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / f"lottery-result-{pool_id}-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(page_url, screenshot_path, options=LOTTERY_RESULT_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc:
        await bot.send(event, at + " " + reply_failure("抽奖", str(exc)))
        return

    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, at + " " + reply_failure("抽奖", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        if cmd_skip_reasons:
            await bot.send(event, at + " ⚠️ 部分指令奖品已跳过：" + "；".join(cmd_skip_reasons))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")
