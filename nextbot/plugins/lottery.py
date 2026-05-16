from __future__ import annotations

import asyncio
import math
import random
import unicodedata

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import func, update

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import (
    WAREHOUSE_CAPACITY,
    LotteryPool,
    LotteryPrize,
    Server,
    User,
    WarehouseItem,
    execute_rowcount,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import (
    MAX_COINS_AMOUNT,
    add_coins_with_cap,
    subtract_coins_with_floor,
)
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.server_broadcast import broadcast, BroadcastOutcome
from nextbot.text_utils import reply_failure, safe_at_segment_or_empty
from nextbot.time_utils import db_now_utc_naive
from nextbot.tshock_api import TShockRequestError, get_error_reason, is_success, request_server_api
from nextbot.warehouse_lock import warehouse_lock
from server.screenshot import ScreenshotOptions
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

# LO-1.3：handler-wide semaphore，防止 guest 高并发刷命令导致 Playwright OOM 放大
_lottery_screenshot_semaphore = asyncio.Semaphore(2)

# LO-3.14：单次抽奖产生的指令调用上限，防 admin 配 N 服务器 × 100 抽奖 = N00 RPC 爆炸
MAX_LOTTERY_CMD_EXECUTIONS = 200

# LO-3.10：玩家名进入指令模板前禁止包含的字符（防 TShock 命令解析器拼接歧义）
_FORBIDDEN_PLAYER_CHARS = set('"\';\n\r ')


def _normalize_player_name(name: str) -> str:
    """unicode NFKC + casefold 折叠，用于跨全角/半角 + 大小写比对。

    R4R-5.1：与 shop._normalize_player_name / warehouse._normalize_player_name
    保持一致，避免 lottery 与其他域玩家名匹配口径漂移。
    """
    return unicodedata.normalize("NFKC", str(name)).strip().casefold()


def _player_name_safe_for_command(name: str) -> bool:
    """玩家名注入指令模板前最低限度校验。

    TShock /v3/server/rawcmd 的 cmd 参数会被 TShock 内部按空格 / 引号拆分为
    多个 token，这些字符出现在 player 名中会让原本的命令分词错位。
    本项目 User.name 已在注册时由 Terraria 客户端约束（不允许这些字符），
    这里做兜底防御，遇到异常名称直接拒绝执行该奖品。
    """
    if not name:
        return False
    return not any(ch in _FORBIDDEN_PLAYER_CHARS for ch in name)


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

    LO-3.8：set_total > 100 时按 100/set_total 等比例归一化，避免后段奖品永远抽不到，
    并 logger.warning 提示 admin 配置错误。
    """
    set_prizes = [(p, float(p.weight)) for p in prizes if p.weight is not None]
    unset_prizes = [p for p in prizes if p.weight is None]
    raw_set_total = sum(max(0.0, min(100.0, w)) for _, w in set_prizes)
    if raw_set_total > 100.0:
        # admin 配置错误：所有奖品权重之和 > 100，重新归一化让所有奖品都能命中
        scale = 100.0 / raw_set_total
        logger.warning(
            f"奖池权重之和超过 100%，已按比例归一化："
            f"raw_total={raw_set_total:.2f} scale={scale:.4f} "
            f"prize_count={len(set_prizes)}"
        )
        set_prizes = [(p, w * scale) for p, w in set_prizes]
        set_total = 100.0
    else:
        set_total = max(0.0, raw_set_total)
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
    # random.random() 返回 [0.0, 1.0) 半开区间，乘以 100 得到 [0.0, 100.0) ——
    # 与 random.uniform(0, 100) 的全闭区间相比可避免 roll == 100.0 落空的极端边界
    roll = random.random() * 100.0
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


async def _check_player_online(server: Server, player_name: str) -> tuple[bool | None, str]:
    """LO-3.7：返回 (在线 | None, 原因)。

    None 表示 RPC 失败 / 返回格式异常（区别于"已确认离线"），
    调用方据此区分"暂时无法判断"和"确实离线"。
    """
    try:
        resp = await request_server_api(
            server, "/v2/server/status", params={"players": "true"},
        )
    except TShockRequestError:
        return None, "无法连接服务器"
    if not is_success(resp):
        return None, get_error_reason(resp) or "查询失败"
    players = resp.payload.get("players")
    if not isinstance(players, list):
        return None, "返回数据格式错误"
    # R4R-5.1：unicode NFKC + casefold，与 shop / warehouse 一致，跨全角/半角折叠匹配
    target = _normalize_player_name(player_name)
    for p in players:
        nickname = str(p.get("nickname", "")) if isinstance(p, dict) else str(p)
        if _normalize_player_name(nickname) == target:
            return True, ""
    return False, "玩家不在线"


def _find_empty_slots(session, user_id: str, needed: int) -> list[int]:
    """LO-3.9：只 SELECT slot_index ORDER BY，遇到第一段空槽即可早停。"""
    occupied_rows = (
        session.query(WarehouseItem.slot_index)
        .filter(WarehouseItem.user_id == user_id)
        .order_by(WarehouseItem.slot_index.asc())
        .all()
    )
    occupied = {int(row[0]) for row in occupied_rows}
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
    user_id = event.get_user_id()
    at = safe_at_segment_or_empty(user_id)
    try:
        args = parse_command_args_with_fallback(event, arg, "奖池列表")
        if len(args) > 1:
            raise_command_usage()

        page = 1
        if args:
            try:
                page = int(args[0])
            except ValueError:
                await bot.send(event, at + " " + reply_failure("查询", "页数必须为正整数"))
                return
            if page <= 0:
                await bot.send(event, at + " " + reply_failure("查询", "页数必须为正整数"))
                return

        limit = max(1, min(int(get_current_param("limit", 10)), 50))

        # LO-1.1：SQL 分页，LEFT JOIN 子查询替代 N+1 COUNT
        session = get_session()
        try:
            total = (
                session.query(LotteryPool)
                .filter(LotteryPool.enabled.is_(True))
                .count()
            )
            if total == 0:
                await bot.send(event, at + " " + reply_failure("查询", "暂无可用奖池"))
                return

            total_pages = max(1, math.ceil(total / limit))
            if page > total_pages:
                await bot.send(event, at + " " + reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
                return
            offset = (page - 1) * limit

            prize_count_subq = (
                session.query(
                    LotteryPrize.pool_id.label("pool_id"),
                    func.count(LotteryPrize.id).label("prize_count"),
                )
                .filter(LotteryPrize.enabled.is_(True))
                .group_by(LotteryPrize.pool_id)
                .subquery()
            )
            rows = (
                session.query(LotteryPool, prize_count_subq.c.prize_count)
                .outerjoin(prize_count_subq, prize_count_subq.c.pool_id == LotteryPool.id)
                .filter(LotteryPool.enabled.is_(True))
                .order_by(LotteryPool.sort_order.asc(), LotteryPool.id.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            render_entries = [
                {
                    "pool_id": int(pool.id),
                    "name": str(pool.name),
                    "description": str(pool.description or ""),
                    "prize_count": int(prize_count or 0),
                    "cost_per_draw": int(pool.cost_per_draw or 0),
                }
                for pool, prize_count in rows
            ]
        finally:
            session.close()

        page_url = create_lottery_list_page(
            entries=render_entries, page=page, total_pages=total_pages,
            total=total,
        )
        # LO-3.11：避免完整 internal_url（含 token）落审计平台
        logger.info(
            f"奖池列表渲染：page={page}/{total_pages} total={total} "
            f"item_count={len(render_entries)} url_prefix={page_url[:80]}..."
        )

        await render_and_send_screenshot(
            bot, event,
            page_url=page_url,
            options=LOTTERY_LIST_SCREENSHOT_OPTIONS,
            file_prefix="lottery-list",
            semaphore=_lottery_screenshot_semaphore,
            failure_action="查询",
        )
    except Exception:  # noqa: BLE001
        # LO-1.2：handler 外层 try/except，统一兜底
        logger.exception(f"奖池列表处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass


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
            "description": "每页显示的奖品数量（按 2 列网格布局，建议为偶数）",
            "required": False, "default": 20, "min": 1, "max": 100,
        },
    },
    category="抽奖系统",
)
@require_permission("lottery.view")
async def handle_lottery_view(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = safe_at_segment_or_empty(user_id)
    try:
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
                await bot.send(event, at + " " + reply_failure("查询", "页数必须为正整数"))
                return
            if page <= 0:
                await bot.send(event, at + " " + reply_failure("查询", "页数必须为正整数"))
                return

        limit = max(1, min(int(get_current_param("limit", 20)), 100))

        session = get_session()
        try:
            pool = _load_pool_by_selector(session, selector)
            if pool is None:
                await bot.send(event, at + " " + reply_failure("查询", f"未找到奖池「{selector}」"))
                return
            if not pool.enabled:
                await bot.send(event, at + " " + reply_failure("查询", "该奖池未上架"))
                return
            prizes = _list_active_prizes(session, int(pool.id))
            pool_id = int(pool.id)
            pool_name = str(pool.name)
            pool_desc = str(pool.description or "")
            cost_per_draw = int(pool.cost_per_draw or 0)

            # LO-2.2：仅当存在 command 类奖品时才加载 server label map
            need_server_labels = any(p.kind == "command" for p in prizes)
            if need_server_labels:
                server_label_map: dict[int, str] = {
                    int(s.id): str(s.name) for s in session.query(Server).all()
                }
            else:
                server_label_map = {}

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

        # LO-2.1：分页参数虽然依赖全部 prizes 才能解析概率（_resolve_probabilities），
        # 真正承压的是 list 长度。当前奖品数实际上限有限（admin 维护），
        # 为保持概率展示一致性，仍在内存切片，但已抽空 server query 的开销。
        total = len(all_entries)
        total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
        if total > 0 and page > total_pages:
            await bot.send(event, at + " " + reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
            return
        offset = (page - 1) * limit
        render_prizes = all_entries[offset:offset + limit]

        page_url = create_lottery_view_page(
            pool_id=pool_id, pool_name=pool_name, pool_description=pool_desc,
            cost_per_draw=cost_per_draw, prizes=render_prizes,
            miss_probability=float(miss_pct),
            page=page, total_pages=total_pages, total=total,
        )
        logger.info(
            f"奖池详情渲染：pool_id={pool_id} page={page}/{total_pages} "
            f"total={total} miss_pct={miss_pct:.2f} url_prefix={page_url[:80]}..."
        )

        await render_and_send_screenshot(
            bot, event,
            page_url=page_url,
            options=LOTTERY_VIEW_SCREENSHOT_OPTIONS,
            file_prefix=f"lottery-view-{pool_id}",
            semaphore=_lottery_screenshot_semaphore,
            failure_action="查询",
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"查看奖池处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass


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
    at = safe_at_segment_or_empty(user_id)

    try:
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

        # ===== Phase 1: snapshot pool / prizes / user info =====
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
            # LO-3.13：cost_per_draw × max_draws 也要受 MAX_COINS_AMOUNT 上限（admin 配 1e9 单价 + 100 抽 = overflow 风险）
            if total_cost > MAX_COINS_AMOUNT:
                await bot.send(
                    event,
                    at + " " + reply_failure("抽奖", f"抽奖花费过大（最多 {MAX_COINS_AMOUNT}）"),
                )
                return
            if coins < total_cost:
                await bot.send(
                    event,
                    at + " " + reply_failure("抽奖", f"金币不足（需要 {total_cost}，当前 {coins}）"),
                )
                return

            pool_id = int(pool.id)
            pool_name = str(pool.name)
            player_name = str(user.name)
            snap_cost_per_draw = cost_per_draw  # for TOCTOU re-check in phase 3
            # snapshot prize fields needed downstream
            def _snap_prize(p: LotteryPrize) -> dict:
                raw_actual = getattr(p, "actual_value", None)
                actual_value = int(raw_actual) if raw_actual is not None else None
                target_server_id = (
                    int(p.target_server_id) if p.target_server_id is not None else None
                )
                return {
                    "id": int(p.id),
                    "name": str(p.name),
                    "kind": str(p.kind),
                    "is_mystery": bool(getattr(p, "is_mystery", False)),
                    "item_id": int(p.item_id or 0),
                    "prefix_id": int(p.prefix_id or 0),
                    "quantity": int(p.quantity or 1),
                    "min_tier": str(p.min_tier or "none"),
                    "actual_value": actual_value,
                    "target_server_id": target_server_id,
                    "command_template": str(p.command_template or ""),
                    "require_online": bool(getattr(p, "require_online", False)),
                    "coin_amount": int(p.coin_amount or 0),
                    "unit_price": int(pool.cost_per_draw or 0),
                }
            prize_snapshots = {int(p.id): _snap_prize(p) for p in prizes}
            resolved, miss_pct = _resolve_probabilities(prizes)
            draw_prob_by_id = {int(p.id): float(prob) for p, prob in resolved}
        finally:
            session.close()

        # ===== Phase 2: roll dice in Python =====
        rolled_prize_ids: list[int | None] = []
        for _ in range(draw_count):
            prize = _draw_one(resolved, miss_pct)
            rolled_prize_ids.append(int(prize.id) if prize is not None else None)

        bucket: dict[int | None, int] = {}
        for pid in rolled_prize_ids:
            bucket[pid] = bucket.get(pid, 0) + 1

        item_prize_ids = [pid for pid in bucket.keys() if pid is not None and prize_snapshots[pid]["kind"] == "item"]
        needed_slots = len(item_prize_ids)

        # ===== Phase 3: pre-flight command-prize plan + LO-3.14 N×M cap =====
        session = get_session()
        try:
            all_servers_orm = {int(s.id): s for s in session.query(Server).all()}
        finally:
            session.close()

        cmd_plan: list[tuple[int, list[Server]]] = []
        cmd_skip_reasons: list[str] = []
        # 总指令次数 cap，防 admin 配的"全服务器命令"+ 100 抽 = 数百 RPC
        planned_cmd_executions = 0
        # LO-3.7：online cache 改 (bool|None, str)
        server_online_cache: dict[tuple[int, str], tuple[bool | None, str]] = {}

        async def _check_online_cached(srv_id: int, srv_obj: Server, player: str) -> tuple[bool | None, str]:
            key = (srv_id, player)
            if key in server_online_cache:
                return server_online_cache[key]
            ok, reason = await _check_player_online(srv_obj, player)
            server_online_cache[key] = (ok, reason)
            return ok, reason

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
                online_servers = []
                offline_reasons: list[str] = []
                # PC-2.1：跨服务器在线检查并行 fan-out（与 mutation broadcast / leaderboard 对齐）
                # R3N-4.2：return_exceptions=True 防止任一 task 抛非 TShockRequestError
                # 异常时整个 gather cancel 其它任务（如内部 bug 触发 AttributeError）
                check_results = await asyncio.gather(
                    *(_check_online_cached(int(srv.id), srv, player_name) for srv in target_servers),
                    return_exceptions=True,
                )
                for srv, result in zip(target_servers, check_results):
                    if isinstance(result, BaseException):
                        logger.warning(
                            f"在线检查异常：server_id={srv.id} reason={result!r}"
                        )
                        offline_reasons.append(f"#{srv.id} {srv.name}：查询失败（异常）")
                        continue
                    ok, reason = result
                    if ok is True:
                        online_servers.append(srv)
                    elif ok is False:
                        offline_reasons.append(f"#{srv.id} {srv.name}：{reason or '玩家不在线'}")
                    else:
                        # LO-3.7：区分 RPC 失败和确认离线
                        offline_reasons.append(f"#{srv.id} {srv.name}：查询失败（{reason or '未知'}）")
                if not online_servers:
                    suffix = "；".join(offline_reasons) if offline_reasons else "无在线服务器"
                    cmd_skip_reasons.append(f"奖品「{snap['name']}」需要玩家在线：{suffix}")
                    continue
                target_servers = online_servers

            planned_cmd_executions += len(target_servers) * count
            cmd_plan.append((pid, target_servers))

        if planned_cmd_executions > MAX_LOTTERY_CMD_EXECUTIONS:
            await bot.send(
                event,
                at + " " + reply_failure(
                    "抽奖",
                    f"本次抽奖产生的指令调用过多（{planned_cmd_executions} 次，"
                    f"上限 {MAX_LOTTERY_CMD_EXECUTIONS}），请减少次数或联系管理员",
                ),
            )
            return

        # ===== Phase 4: TOCTOU re-validate + atomic charge =====
        # LO-3.10：玩家名安全检查（防 TShock 命令解析器拼接歧义）
        # 与 cmd_plan 一起检查：如果有命令奖品但玩家名异常，整体拒绝（不要静默吃掉奖品）
        if cmd_plan and not _player_name_safe_for_command(player_name):
            await bot.send(
                event,
                at + " " + reply_failure(
                    "抽奖", "玩家名包含特殊字符，无法安全执行指令奖品，请联系管理员处理玩家名",
                ),
            )
            return

        async def _charge_atomic() -> tuple[bool, int, int, int, str]:
            """LO-3.1 + LO-3.2：原子扣费 + TOCTOU 重新校验。

            返回 (ok, final_coins, item_value_gained, applied_coin_delta, error_msg)
            ok=False 时 error_msg 已包含完整失败原因。

            applied_coin_delta：实际写入 DB 的 coin 奖励 delta（已 cap），
            用于结果页展示，避免显示用户"获得 +5000 金币"实际只入账 0 的不一致。
            """
            session_local = get_session()
            try:
                # TOCTOU re-validate pool
                pool_now = session_local.query(LotteryPool).filter(LotteryPool.id == pool_id).first()
                if pool_now is None or not pool_now.enabled:
                    return False, 0, 0, 0, "奖池已变更，请刷新后重试"
                if int(pool_now.cost_per_draw or 0) != snap_cost_per_draw:
                    return False, 0, 0, 0, "奖池价格已变更，请重新抽取"

                # TOCTOU re-validate每个 prize
                for pid in bucket:
                    if pid is None:
                        continue
                    prize_now = session_local.query(LotteryPrize).filter(LotteryPrize.id == pid).first()
                    if prize_now is None or not prize_now.enabled:
                        snap_name = prize_snapshots[pid]["name"]
                        return False, 0, 0, 0, f"奖品「{snap_name}」已变更，请重新抽取"

                # LO-3.1：条件 UPDATE 扣费 (与 economy F-2.1 / shop S-1.1 同模板)
                rowcount = execute_rowcount(
                    session_local,
                    update(User)
                    .where(User.user_id == user_id, User.coins >= total_cost)
                    .values(coins=User.coins - total_cost),
                )
                if rowcount == 0:
                    coins_now = int(
                        session_local.query(User.coins).filter(User.user_id == user_id).scalar() or 0
                    )
                    return False, 0, 0, 0, f"金币不足（需要 {total_cost}，当前 {coins_now}）"

                # 派 item 奖
                item_value_gained = 0
                if needed_slots > 0:
                    empty_slots = _find_empty_slots(session_local, user_id, needed_slots)
                    if len(empty_slots) < needed_slots:
                        # 回退扣费
                        execute_rowcount(
                            session_local,
                            update(User)
                            .where(User.user_id == user_id)
                            .values(coins=User.coins + total_cost),
                        )
                        session_local.commit()
                        return False, 0, 0, 0, (
                            f"仓库剩余空格不足（需要 {needed_slots} 格，"
                            f"剩余 {len(empty_slots)} 格），已退还金币"
                        )
                    slot_iter = iter(empty_slots)
                    for pid_item in item_prize_ids:
                        snap = prize_snapshots[pid_item]
                        count = bucket[pid_item]
                        total_qty = snap["quantity"] * count
                        actual_value_raw = snap.get("actual_value")
                        if actual_value_raw is not None:
                            # LO-3.13：cap unit_value 防止绕过 economy MAX_COINS_AMOUNT 限额
                            unit_value = max(0, min(int(actual_value_raw), MAX_COINS_AMOUNT))
                        else:
                            per_pack = max(1, snap["quantity"])
                            unit_value = snap["unit_price"] // per_pack if per_pack > 0 else 0
                        item_value_gained += unit_value * total_qty
                        session_local.add(WarehouseItem(
                            user_id=user_id,
                            slot_index=next(slot_iter),
                            item_id=snap["item_id"],
                            prefix_id=snap["prefix_id"],
                            quantity=total_qty,
                            min_tier=snap["min_tier"],
                            value=int(unit_value),
                            created_at=db_now_utc_naive(),
                        ))

                # 派 coin 奖：正负分支独立做 cap，确保不溢出 / 不负
                coin_delta_pos = 0
                coin_delta_neg = 0
                for pid_coin, count in bucket.items():
                    if pid_coin is None:
                        continue
                    snap = prize_snapshots[pid_coin]
                    if snap["kind"] != "coin":
                        continue
                    amt = int(snap["coin_amount"]) * count
                    if amt > 0:
                        coin_delta_pos += amt
                    elif amt < 0:
                        coin_delta_neg += amt
                # R3E-3 / R3N-3.2：复用 economy.add_coins_with_cap /
                # subtract_coins_with_floor helper，避免自实现 partial cap 与
                # helper 行为漂移。正向走 add_coins_with_cap，负向走
                # subtract_coins_with_floor（注意 helper 接受的是正数 delta）。
                applied_pos = 0
                applied_neg = 0
                if coin_delta_pos > 0:
                    applied_pos, _ = add_coins_with_cap(session_local, user_id, coin_delta_pos)
                if coin_delta_neg < 0:
                    requested_neg = -coin_delta_neg  # helper 用正数表示扣除量
                    applied_abs, _ = subtract_coins_with_floor(
                        session_local, user_id, requested_neg
                    )
                    applied_neg = -applied_abs

                applied_coin_delta = applied_pos + applied_neg
                session_local.commit()
                final_coins = int(
                    session_local.query(User.coins).filter(User.user_id == user_id).scalar() or 0
                )
                return True, final_coins, item_value_gained, applied_coin_delta, ""
            finally:
                session_local.close()

        if needed_slots > 0:
            async with warehouse_lock(user_id):
                ok, final_coins, item_value_gained, applied_coin_delta, err = await _charge_atomic()
        else:
            ok, final_coins, item_value_gained, applied_coin_delta, err = await _charge_atomic()

        if not ok:
            await bot.send(event, at + " " + reply_failure("抽奖", err))
            return

        # 用实际写入 DB 的 coin_delta 展示（避免显示"+5000 金币"实际入账 0 的不一致）
        coin_delta = applied_coin_delta
        # raw（按奖品计算的理论 delta）用于日志对账，便于追踪 cap 触发
        raw_coin_delta = 0
        for pid_coin, count in bucket.items():
            if pid_coin is None:
                continue
            snap = prize_snapshots[pid_coin]
            if snap["kind"] == "coin":
                raw_coin_delta += int(snap["coin_amount"]) * count

        # ===== Phase 5: execute command prizes (parallel via server_broadcast) =====
        cmd_results: list[dict[str, object]] = []
        if cmd_plan:
            # LO-3.5：按 prize 拆 fan-out，per-prize 用 broadcast 跨服务器并行
            for pid, servers in cmd_plan:
                snap = prize_snapshots[pid]
                count = bucket[pid]
                cmd_text = snap["command_template"].replace("{player}", player_name)

                async def _execute_for_server(srv: Server, _cmd_text: str = cmd_text, _count: int = count) -> BroadcastOutcome:
                    """单服务器内串行执行 _count 次（命令奖品需要每次独立调用）。

                    跨服务器之间由 broadcast 并行；同服内连续调用避免 TShock 自身的限流。
                    """
                    success_n = 0
                    last_reason = ""
                    for _ in range(_count):
                        ok, reason = await _issue_raw_command(srv, _cmd_text)
                        if ok:
                            success_n += 1
                        else:
                            last_reason = reason
                    return BroadcastOutcome(
                        server=srv,
                        ok=success_n == _count,
                        detail=f"成功 {success_n}/{_count}" + (f"（{last_reason}）" if last_reason else ""),
                        payload={"success_count": success_n, "total_count": _count, "last_reason": last_reason},
                    )

                broadcast_outcomes = await broadcast(servers, _execute_for_server)
                for outcome in broadcast_outcomes:
                    payload = outcome.payload or {}
                    suc = int(payload.get("success_count", 0)) if isinstance(payload, dict) else 0
                    tot = int(payload.get("total_count", count)) if isinstance(payload, dict) else count
                    last_reason = str(payload.get("last_reason", "")) if isinstance(payload, dict) else ""
                    # 展开为 cmd_results 旧 schema：每次单独一行（保持渲染兼容）
                    for i in range(tot):
                        cmd_results.append({
                            "server_label": f"#{outcome.server.id} {outcome.server.name}",
                            "ok": i < suc,
                            "reason": "" if i < suc else (last_reason or "失败"),
                        })

        # ===== Phase 6: build outcomes for render =====
        outcomes: list[dict[str, object]] = []
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

        # LO-3.3：cmd_results 全失败时 CRITICAL log + reply head 切换
        cmd_total = len(cmd_results)
        cmd_success = sum(1 for r in cmd_results if r["ok"])
        if cmd_total > 0 and cmd_success == 0:
            logger.error(
                f"[CRITICAL] 抽奖指令奖品全部失败但金币已扣："
                f"user_id={user_id} pool_id={pool_id} pool_name={pool_name} "
                f"draw_count={draw_count} total_cost={total_cost} cmd_total={cmd_total}"
            )

        page_url = create_lottery_result_page(
            pool_id=pool_id, pool_name=pool_name,
            user_user_id=user_id, user_user_name=player_name,
            user_coins_after=final_coins, draw_count=draw_count,
            total_cost=total_cost, coin_delta=coin_delta,
            item_value_gained=item_value_gained,
            outcomes=outcomes, item_slots_used=needed_slots,
            command_results=cmd_results,
        )
        logger.info(
            f"金币变更：actor={user_id} target={user_id} action=lottery.draw "
            f"pool_id={pool_id} draws={draw_count} amount={total_cost} "
            f"coin_delta={coin_delta} raw_coin_delta={raw_coin_delta} "
            f"after={final_coins} item_value_gained={item_value_gained} "
            f"item_slots={needed_slots} cmd_executions={cmd_total} cmd_success={cmd_success} "
            f"skipped={len(cmd_skip_reasons)} url_prefix={page_url[:80]}... reason=lottery_draw"
        )

        # LO-3.6：cmd_skip_reasons 在所有 adapter 路径都先发送（不依赖截图分支）
        if cmd_skip_reasons:
            try:
                await bot.send(event, at + " ⚠️ 部分指令奖品已跳过：" + "；".join(cmd_skip_reasons))
            except Exception:  # noqa: BLE001
                logger.warning(f"抽奖跳过通知发送失败：user_id={user_id}")

        # LO-3.3：全失败时通过单独前置文本告警（让 render 失败时也可见），随后再渲染图
        if cmd_total > 0 and cmd_success == 0:
            try:
                await bot.send(
                    event,
                    at + " " + reply_failure(
                        "抽奖", "全部指令奖品执行失败，金币已扣，请联系管理员对账",
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.warning(f"抽奖全失败告警发送失败：user_id={user_id}")
        elif cmd_total > 0 and cmd_success < cmd_total:
            try:
                await bot.send(
                    event,
                    at + " ⚠️ 抽奖部分成功，详情见下方截图",
                )
            except Exception:  # noqa: BLE001
                logger.warning(f"抽奖部分成功告警发送失败：user_id={user_id}")

        await render_and_send_screenshot(
            bot, event,
            page_url=page_url,
            options=LOTTERY_RESULT_SCREENSHOT_OPTIONS,
            file_prefix=f"lottery-result-{pool_id}",
            semaphore=_lottery_screenshot_semaphore,
            failure_action="抽奖",
        )
    except Exception:  # noqa: BLE001
        logger.exception(f"抽奖处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("抽奖", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
