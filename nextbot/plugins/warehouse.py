from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path

from nonebot import on_command
from sqlalchemy.exc import IntegrityError
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import WAREHOUSE_CAPACITY, Server, User, WarehouseItem, get_session
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.progression import PROGRESSION_KEY_TO_ZH, TIER_OPTIONS, parse_tier
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_COIN,
    EMOJI_SERVER,
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
from server.web_server import create_warehouse_page

WAREHOUSE_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1200,
    viewport_height=600,
    full_page=True,
    fit_content_height=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


_DICTS_DIR = Path(__file__).resolve().parent.parent.parent / "server" / "assets" / "dicts"
_item_name_map: dict[int, str] | None = None
_prefix_name_map: dict[int, str] | None = None


def _load_dict_file(filename: str) -> dict[int, str]:
    path = _DICTS_DIR / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning(f"加载 dict 失败：{path}")
        return {}
    out: dict[int, str] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                eid = int(entry.get("id", 0))
            except (TypeError, ValueError):
                continue
            name = str(entry.get("name", "")).strip()
            if eid > 0 and name:
                out[eid] = name
    return out


def _item_display_name(item_id: int) -> str:
    global _item_name_map
    if _item_name_map is None:
        _item_name_map = _load_dict_file("item.json")
    return _item_name_map.get(int(item_id), f"物品 ID:{item_id}")


def _prefix_display_name(prefix_id: int) -> str:
    global _prefix_name_map
    if _prefix_name_map is None:
        _prefix_name_map = _load_dict_file("prefix.json")
    if int(prefix_id) <= 0:
        return ""
    return _prefix_name_map.get(int(prefix_id), f"前缀 ID:{prefix_id}")


def _format_item_label(item_id: int, prefix_id: int, quantity: int) -> str:
    name = _item_display_name(item_id)
    prefix = _prefix_display_name(prefix_id)
    full = f"{prefix} {name}" if prefix else name
    return f"{full} ×{quantity}"


def _parse_slot_expression(s: str) -> tuple[list[int], bool]:
    """格子表达式解析，返回 (排序去重后的格子号列表, 是否单格)。

    支持：单格 `5` / 区间 `1-10` / 列表 `1,3,5` / 组合 `1-3,5,7-9` / `全部`/`all`
    """
    raw = str(s or "").strip()
    if not raw:
        raise ValueError("格子表达式不能为空")
    if raw in {"全部", "all"}:
        return list(range(1, WAREHOUSE_CAPACITY + 1)), False
    if raw.isdigit():
        n = int(raw)
        if not (1 <= n <= WAREHOUSE_CAPACITY):
            raise ValueError(f"格子号必须为 1-{WAREHOUSE_CAPACITY}")
        return [n], True

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("格子表达式格式错误，例：5 / 1-10 / 1,3,5 / 全部")
    out: set[int] = set()
    for part in parts:
        if "-" in part:
            a_s, _, b_s = part.partition("-")
            try:
                a, b = int(a_s.strip()), int(b_s.strip())
            except ValueError as exc:
                raise ValueError("格子表达式格式错误，例：5 / 1-10 / 1,3,5 / 全部") from exc
            if a > b:
                raise ValueError("区间起点不能大于终点")
            for n in range(a, b + 1):
                if not (1 <= n <= WAREHOUSE_CAPACITY):
                    raise ValueError(f"格子号必须为 1-{WAREHOUSE_CAPACITY}")
                out.add(n)
        else:
            try:
                n = int(part)
            except ValueError as exc:
                raise ValueError("格子表达式格式错误，例：5 / 1-10 / 1,3,5 / 全部") from exc
            if not (1 <= n <= WAREHOUSE_CAPACITY):
                raise ValueError(f"格子号必须为 1-{WAREHOUSE_CAPACITY}")
            out.add(n)
    return sorted(out), False


def _build_tier_options_lines(per_line: int = 4) -> list[str]:
    names = [zh for _, zh in TIER_OPTIONS]
    return ["、".join(names[i:i + per_line]) for i in range(0, len(names), per_line)]


def _load_user(user_id: str) -> User | None:
    session = get_session()
    try:
        return session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()


def _load_user_by_name(name: str) -> User | None:
    session = get_session()
    try:
        return session.query(User).filter(User.name == name).first()
    finally:
        session.close()


def _load_server(server_id: int) -> Server | None:
    session = get_session()
    try:
        return session.query(Server).filter(Server.id == server_id).first()
    finally:
        session.close()


def _find_first_empty_slot(session, user_id: str) -> int | None:
    occupied = {
        int(s) for (s,) in session.query(WarehouseItem.slot_index)
        .filter(WarehouseItem.user_id == user_id).all()
    }
    return next(
        (i for i in range(1, WAREHOUSE_CAPACITY + 1) if i not in occupied),
        None,
    )


@asynccontextmanager
async def _acquire_two_warehouse_locks(user_a: str, user_b: str):
    # Deterministic acquisition order prevents ABBA deadlock when two gift
    # commands run concurrently between the same two users in opposite directions.
    first, second = sorted((user_a, user_b))
    async with warehouse_lock(first):
        async with warehouse_lock(second):
            yield


def _load_warehouse_slots(user_id: str) -> list[dict]:
    session = get_session()
    try:
        rows = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .order_by(WarehouseItem.slot_index.asc())
            .all()
        )
        return [
            {
                "slot_index": int(r.slot_index),
                "item_id": int(r.item_id),
                "prefix_id": int(r.prefix_id),
                "quantity": int(r.quantity),
                "min_tier": str(r.min_tier),
                "value": int(r.value or 0),
            }
            for r in rows
        ]
    finally:
        session.close()


async def _send_warehouse_image(
    bot: Bot,
    event: Event,
    *,
    page_url: str,
    file_prefix: str,
) -> None:
    screenshot_path = Path("/tmp") / f"{file_prefix}-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(
            page_url, screenshot_path, options=WAREHOUSE_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("查询", str(exc)))
        return

    logger.info(f"仓库截图成功：file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("查询", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


list_self_matcher = on_command("我的仓库")
list_user_matcher = on_command("用户仓库")
add_matcher = on_command("添加仓库物品")
remove_matcher = on_command("删除仓库物品")
drop_matcher = on_command("丢弃仓库物品")
recycle_matcher = on_command("回收仓库物品")
claim_matcher = on_command("领取仓库物品")
gift_matcher = on_command("赠送仓库物品")


@list_self_matcher.handle()
@command_control(
    command_key="warehouse.list_self",
    display_name="我的仓库",
    permission="warehouse.list_self",
    description="查看自己的仓库（网页渲染）",
    usage="我的仓库",
    category="仓库系统",
)
@require_permission("warehouse.list_self")
async def handle_list_self(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "我的仓库")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    user = _load_user(user_id)
    if user is None:
        at = OBV11MessageSegment.at(int(user_id))
        await bot.send(event, at + " " + reply_failure("查询", "未注册账号"))
        return

    slots = _load_warehouse_slots(user_id)
    page_url = create_warehouse_page(
        owner_user_id=user_id,
        owner_user_name=str(user.name),
        slots=slots,
    )
    logger.info(
        f"我的仓库渲染：user_id={user_id} used={len(slots)} internal_url={page_url}"
    )
    await _send_warehouse_image(bot, event, page_url=page_url, file_prefix="warehouse-self")


@list_user_matcher.handle()
@command_control(
    command_key="warehouse.list_user",
    display_name="用户仓库",
    permission="warehouse.list_user",
    description="查看指定用户的仓库（网页渲染）",
    usage="用户仓库 <用户 QQ/@用户/用户名称>",
    category="仓库系统",
)
@require_permission("warehouse.list_user")
async def handle_list_user(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "用户仓库", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, reply_failure("查询", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, reply_failure("查询", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error or target_user_id is None:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "用户仓库")
    if len(args) != 1:
        raise_command_usage()

    user = _load_user(target_user_id)
    if user is None:
        await bot.send(event, reply_failure("查询", "未找到该用户"))
        return

    slots = _load_warehouse_slots(target_user_id)
    page_url = create_warehouse_page(
        owner_user_id=target_user_id,
        owner_user_name=str(user.name),
        slots=slots,
    )
    logger.info(
        f"用户仓库渲染：user_id={target_user_id} used={len(slots)} internal_url={page_url}"
    )
    await _send_warehouse_image(bot, event, page_url=page_url, file_prefix="warehouse-user")


@add_matcher.handle()
@command_control(
    command_key="warehouse.add",
    display_name="添加仓库物品",
    permission="warehouse.add",
    description="将物品添加到指定用户仓库的第一个空格",
    usage="添加仓库物品 <用户 QQ/@用户/用户名称> <物品 ID> <数量> <前缀 ID> <进度名称> <物品价值>",
    category="仓库系统",
)
@require_permission("warehouse.add")
async def handle_add(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = OBV11MessageSegment.at(int(event.get_user_id()))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "添加仓库物品", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("添加", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("添加", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error or target_user_id is None:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "添加仓库物品")
    if len(args) != 6:
        raise_command_usage()

    try:
        item_id = int(args[1])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("添加", "物品 ID 必须为正整数"))
        return
    if item_id < 1:
        await bot.send(event, at + " " + reply_failure("添加", "物品 ID 必须为正整数"))
        return

    try:
        quantity = int(args[2])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("添加", "数量必须为正整数"))
        return
    if quantity < 1:
        await bot.send(event, at + " " + reply_failure("添加", "数量必须为正整数"))
        return

    try:
        prefix_id = int(args[3])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("添加", "前缀 ID 必须为非负整数"))
        return
    if prefix_id < 0:
        await bot.send(event, at + " " + reply_failure("添加", "前缀 ID 必须为非负整数"))
        return

    tier_key = parse_tier(args[4])
    if tier_key is None:
        await bot.send(
            event,
            at + "\n" + reply_block(
                reply_failure("添加", "未知进度"),
                ["📋 可选进度：", *_build_tier_options_lines()],
            ),
        )
        return

    try:
        value = int(args[5])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("添加", "价值必须为非负整数"))
        return
    if value < 0:
        await bot.send(event, at + " " + reply_failure("添加", "价值必须为非负整数"))
        return

    user = _load_user(target_user_id)
    if user is None:
        await bot.send(event, at + " " + reply_failure("添加", "未找到该用户"))
        return

    async with warehouse_lock(target_user_id):
        session = get_session()
        try:
            free_slot = _find_first_empty_slot(session, target_user_id)
            if free_slot is None:
                await bot.send(
                    event,
                    at + "\n" + reply_block(
                        reply_failure("添加", "仓库已满"),
                        [
                            f"{EMOJI_USER} 用户：{user.name}（{target_user_id}）",
                            f"{EMOJI_CHART} 已使用：{WAREHOUSE_CAPACITY} / {WAREHOUSE_CAPACITY}",
                            "💡 提示：可让用户使用「丢弃仓库物品」或管理员使用「删除仓库物品」释放格子",
                        ],
                    ),
                )
                return

            session.add(
                WarehouseItem(
                    user_id=target_user_id,
                    slot_index=free_slot,
                    item_id=item_id,
                    prefix_id=prefix_id,
                    quantity=quantity,
                    min_tier=tier_key,
                    value=value,
                    created_at=db_now_utc_naive(),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                # Defensive: with the per-user lock above, two concurrent adds
                # for the same user can't both pick the same slot. This guard
                # only triggers if some other path (e.g. WebUI bypassing the
                # lock) inserts in the meantime — surface a friendly error.
                session.rollback()
                logger.warning(
                    f"添加仓库物品冲突：target={target_user_id} slot={free_slot}"
                )
                await bot.send(
                    event,
                    at + " " + reply_failure(
                        "添加", "格子被占用，请稍后重试或使用 WebUI 手动指定空格",
                    ),
                )
                return
            used_after = (
                session.query(WarehouseItem)
                .filter(WarehouseItem.user_id == target_user_id)
                .count()
            )
        finally:
            session.close()

    logger.info(
        f"添加仓库物品成功：target={target_user_id} slot={free_slot} item={item_id} "
        f"prefix={prefix_id} qty={quantity} tier={tier_key} value={value}"
    )
    tier_zh = PROGRESSION_KEY_TO_ZH.get(tier_key, tier_key)
    item_label = _format_item_label(item_id, prefix_id, quantity)
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("添加"),
            [
                f"🎁 物品：{item_label}",
                f"{EMOJI_USER} 用户：{user.name}（{target_user_id}）",
                f"{EMOJI_WAREHOUSE} 格子：#{free_slot}",
                f"{EMOJI_TARGET} 最低进度：{tier_zh}",
                f"{EMOJI_COIN} 单价：{value} 金币",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


@remove_matcher.handle()
@command_control(
    command_key="warehouse.remove",
    display_name="删除仓库物品",
    permission="warehouse.remove",
    description="清空指定用户仓库的物品，支持单格 / 区间 / 列表 / 全部，单格可指定数量",
    usage="删除仓库物品 <用户 QQ/@用户/用户名称> <格子表达式> [数量]",
    category="仓库系统",
)
@require_permission("warehouse.remove")
async def handle_remove(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = OBV11MessageSegment.at(int(event.get_user_id()))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "删除仓库物品", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("删除", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("删除", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error or target_user_id is None:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "删除仓库物品")
    if not (2 <= len(args) <= 3):
        raise_command_usage()

    try:
        slot_indexes, is_single = _parse_slot_expression(args[1])
    except ValueError as exc:
        await bot.send(event, at + " " + reply_failure("删除", str(exc)))
        return

    quantity_arg: int | None = None
    if len(args) == 3:
        if not is_single:
            await bot.send(event, at + " " + reply_failure("删除", "多格操作不支持数量参数，请使用单格"))
            return
        try:
            quantity_arg = int(args[2])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("删除", "数量必须为正整数"))
            return
        if quantity_arg < 1:
            await bot.send(event, at + " " + reply_failure("删除", "数量必须为正整数"))
            return

    target_user = _load_user(target_user_id)
    target_name = str(target_user.name) if target_user is not None else "未知用户"

    async with warehouse_lock(target_user_id):
        if is_single:
            await _remove_single(bot, event, at, target_user_id, target_name, slot_indexes[0], quantity_arg)
        else:
            await _remove_many(bot, event, at, target_user_id, target_name, slot_indexes)


async def _remove_single(
    bot: Bot, event: Event, at: object,
    target_user_id: str, target_name: str, slot_index: int, quantity_arg: int | None,
) -> None:
    session = get_session()
    try:
        item = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == target_user_id,
                WarehouseItem.slot_index == slot_index,
            )
            .first()
        )
        if item is None:
            await bot.send(event, at + " " + reply_failure("删除", "该格子为空"))
            return

        current_qty = int(item.quantity)
        if quantity_arg is not None and quantity_arg > current_qty:
            await bot.send(
                event,
                at + " " + reply_failure("删除", f"数量超过该格当前数量（{current_qty}）"),
            )
            return

        remove_qty = quantity_arg if quantity_arg is not None else current_qty
        item_id = int(item.item_id)
        prefix_id = int(item.prefix_id)

        if remove_qty >= current_qty:
            session.delete(item)
            remaining = 0
        else:
            item.quantity = current_qty - remove_qty
            remaining = int(item.quantity)
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == target_user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"删除仓库物品成功：target={target_user_id} slot={slot_index} "
        f"item={item_id} prefix={prefix_id} qty={remove_qty} remaining={remaining}"
    )
    slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}"
    if remaining > 0:
        slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}（剩余 {remaining} 件）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("删除"),
            [
                f"🎁 物品：{_format_item_label(item_id, prefix_id, remove_qty)}",
                f"{EMOJI_USER} 用户：{target_name}（{target_user_id}）",
                slot_line,
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


async def _remove_many(
    bot: Bot, event: Event, at: object,
    target_user_id: str, target_name: str, slot_indexes: list[int],
) -> None:
    session = get_session()
    try:
        items = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == target_user_id,
                WarehouseItem.slot_index.in_(slot_indexes),
            )
            .all()
        )
        item_map = {int(it.slot_index): it for it in items}
        total_qty = 0
        processed = 0
        for s in slot_indexes:
            it = item_map.get(s)
            if it is None:
                continue
            total_qty += int(it.quantity)
            session.delete(it)
            processed += 1
        skipped_empty = len(slot_indexes) - processed
        if processed == 0:
            await bot.send(event, at + " " + reply_failure("删除", "未找到任何可删除的格子"))
            return
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == target_user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"批量删除仓库物品：target={target_user_id} processed={processed} "
        f"skipped_empty={skipped_empty} total_qty={total_qty}"
    )
    process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个"
    if skipped_empty > 0:
        process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个（跳过 {skipped_empty} 个空格）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("删除"),
            [
                f"{EMOJI_USER} 用户：{target_name}（{target_user_id}）",
                process_line,
                f"🎁 共删除：{total_qty} 件物品",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


@drop_matcher.handle()
@command_control(
    command_key="warehouse.drop_self",
    display_name="丢弃仓库物品",
    permission="warehouse.drop_self",
    description="丢弃自己仓库的物品，支持单格 / 区间 / 列表 / 全部，单格可指定数量",
    usage="丢弃仓库物品 <格子表达式> [数量]",
    category="仓库系统",
)
@require_permission("warehouse.drop_self")
async def handle_drop(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))

    args = parse_command_args_with_fallback(event, arg, "丢弃仓库物品")
    if not (1 <= len(args) <= 2):
        raise_command_usage()

    try:
        slot_indexes, is_single = _parse_slot_expression(args[0])
    except ValueError as exc:
        await bot.send(event, at + " " + reply_failure("丢弃", str(exc)))
        return

    quantity_arg: int | None = None
    if len(args) == 2:
        if not is_single:
            await bot.send(event, at + " " + reply_failure("丢弃", "多格操作不支持数量参数，请使用单格"))
            return
        try:
            quantity_arg = int(args[1])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("丢弃", "数量必须为正整数"))
            return
        if quantity_arg < 1:
            await bot.send(event, at + " " + reply_failure("丢弃", "数量必须为正整数"))
            return

    user = _load_user(user_id)
    if user is None:
        await bot.send(event, at + " " + reply_failure("丢弃", "未注册账号"))
        return

    async with warehouse_lock(user_id):
        if is_single:
            await _drop_single(bot, event, at, user_id, slot_indexes[0], quantity_arg)
        else:
            await _drop_many(bot, event, at, user_id, slot_indexes)


async def _drop_single(
    bot: Bot, event: Event, at: object,
    user_id: str, slot_index: int, quantity_arg: int | None,
) -> None:
    session = get_session()
    try:
        item = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index == slot_index,
            )
            .first()
        )
        if item is None:
            await bot.send(event, at + " " + reply_failure("丢弃", "该格子为空"))
            return

        current_qty = int(item.quantity)
        if quantity_arg is not None and quantity_arg > current_qty:
            await bot.send(
                event,
                at + " " + reply_failure("丢弃", f"数量超过该格当前数量（{current_qty}）"),
            )
            return

        drop_qty = quantity_arg if quantity_arg is not None else current_qty
        item_id = int(item.item_id)
        prefix_id = int(item.prefix_id)

        if drop_qty >= current_qty:
            session.delete(item)
            remaining = 0
        else:
            item.quantity = current_qty - drop_qty
            remaining = int(item.quantity)
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"丢弃仓库物品成功：user_id={user_id} slot={slot_index} "
        f"item={item_id} prefix={prefix_id} qty={drop_qty} remaining={remaining}"
    )
    slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}"
    if remaining > 0:
        slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}（剩余 {remaining} 件）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("丢弃"),
            [
                f"🎁 物品：{_format_item_label(item_id, prefix_id, drop_qty)}",
                slot_line,
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


async def _drop_many(
    bot: Bot, event: Event, at: object,
    user_id: str, slot_indexes: list[int],
) -> None:
    session = get_session()
    try:
        items = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index.in_(slot_indexes),
            )
            .all()
        )
        item_map = {int(it.slot_index): it for it in items}
        total_qty = 0
        processed = 0
        for s in slot_indexes:
            it = item_map.get(s)
            if it is None:
                continue
            total_qty += int(it.quantity)
            session.delete(it)
            processed += 1
        skipped_empty = len(slot_indexes) - processed
        if processed == 0:
            await bot.send(event, at + " " + reply_failure("丢弃", "未找到任何可丢弃的格子"))
            return
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"批量丢弃仓库物品：user_id={user_id} processed={processed} "
        f"skipped_empty={skipped_empty} total_qty={total_qty}"
    )
    process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个"
    if skipped_empty > 0:
        process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个（跳过 {skipped_empty} 个空格）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("丢弃"),
            [
                process_line,
                f"🎁 共丢弃：{total_qty} 件物品",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


@recycle_matcher.handle()
@command_control(
    command_key="warehouse.recycle_self",
    display_name="回收仓库物品",
    permission="warehouse.recycle_self",
    description="按价值比例回收自己仓库的物品换金币，支持单格 / 区间 / 列表 / 全部，单格可指定数量",
    usage="回收仓库物品 <格子表达式> [数量]",
    params={
        "recycle_ratio": {
            "type": "float",
            "label": "回收比例",
            "description": "回收时返还价值的比例，0.5 表示返还 50%",
            "required": False,
            "default": 0.5,
            "min": 0.0,
        },
    },
    category="仓库系统",
)
@require_permission("warehouse.recycle_self")
async def handle_recycle(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))

    args = parse_command_args_with_fallback(event, arg, "回收仓库物品")
    if not (1 <= len(args) <= 2):
        raise_command_usage()

    try:
        slot_indexes, is_single = _parse_slot_expression(args[0])
    except ValueError as exc:
        await bot.send(event, at + " " + reply_failure("回收", str(exc)))
        return

    quantity_arg: int | None = None
    if len(args) == 2:
        if not is_single:
            await bot.send(event, at + " " + reply_failure("回收", "多格操作不支持数量参数，请使用单格"))
            return
        try:
            quantity_arg = int(args[1])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("回收", "数量必须为正整数"))
            return
        if quantity_arg < 1:
            await bot.send(event, at + " " + reply_failure("回收", "数量必须为正整数"))
            return

    if _load_user(user_id) is None:
        await bot.send(event, at + " " + reply_failure("回收", "未注册账号"))
        return

    ratio = max(0.0, float(get_current_param("recycle_ratio", 0.5)))

    async with warehouse_lock(user_id):
        if is_single:
            await _recycle_single(bot, event, at, user_id, slot_indexes[0], quantity_arg, ratio)
        else:
            await _recycle_many(bot, event, at, user_id, slot_indexes, ratio)


async def _recycle_single(
    bot: Bot, event: Event, at: object,
    user_id: str, slot_index: int, quantity_arg: int | None, ratio: float,
) -> None:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        item = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index == slot_index,
            )
            .first()
        )
        if item is None:
            await bot.send(event, at + " " + reply_failure("回收", "该格子为空"))
            return

        current_qty = int(item.quantity)
        unit_value = int(item.value or 0)
        if unit_value <= 0:
            await bot.send(event, at + " " + reply_failure("回收", "物品无价值，不可回收"))
            return
        if quantity_arg is not None and quantity_arg > current_qty:
            await bot.send(
                event,
                at + " " + reply_failure("回收", f"数量超过该格当前数量（{current_qty}）"),
            )
            return

        recycle_qty = quantity_arg if quantity_arg is not None else current_qty
        refund = int(unit_value * recycle_qty * ratio)
        item_id = int(item.item_id)
        prefix_id = int(item.prefix_id)

        if recycle_qty >= current_qty:
            session.delete(item)
            remaining = 0
        else:
            item.quantity = current_qty - recycle_qty
            remaining = int(item.quantity)
        user.coins = int(user.coins or 0) + refund
        coins_after = int(user.coins)
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"回收仓库物品成功：user_id={user_id} slot={slot_index} "
        f"item={item_id} prefix={prefix_id} qty={recycle_qty} remaining={remaining} "
        f"unit_value={unit_value} ratio={ratio} refund={refund}"
    )
    slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}"
    if remaining > 0:
        slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}（剩余 {remaining} 件）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("回收"),
            [
                f"🎁 物品：{_format_item_label(item_id, prefix_id, recycle_qty)}",
                slot_line,
                f"{EMOJI_COIN} 单价：{unit_value} 金币",
                f"{EMOJI_CHART} 回收比例：{int(ratio * 100)}%",
                f"{EMOJI_COIN} 获得金币：{refund}",
                f"{EMOJI_COIN} 当前金币：{coins_after}",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


async def _recycle_many(
    bot: Bot, event: Event, at: object,
    user_id: str, slot_indexes: list[int], ratio: float,
) -> None:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        items = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index.in_(slot_indexes),
            )
            .all()
        )
        item_map = {int(it.slot_index): it for it in items}
        total_refund = 0
        processed = 0
        skipped_empty = 0
        skipped_no_value = 0
        for s in slot_indexes:
            it = item_map.get(s)
            if it is None:
                skipped_empty += 1
                continue
            unit_value = int(it.value or 0)
            if unit_value <= 0:
                skipped_no_value += 1
                continue
            total_refund += int(unit_value * int(it.quantity) * ratio)
            session.delete(it)
            processed += 1
        if processed == 0:
            await bot.send(event, at + " " + reply_failure("回收", "未找到任何可回收的格子"))
            return
        user.coins = int(user.coins or 0) + total_refund
        coins_after = int(user.coins)
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"批量回收仓库物品：user_id={user_id} processed={processed} "
        f"skipped_empty={skipped_empty} skipped_no_value={skipped_no_value} "
        f"ratio={ratio} total_refund={total_refund}"
    )
    process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个"
    skip_parts: list[str] = []
    if skipped_empty > 0:
        skip_parts.append(f"{skipped_empty} 个空")
    if skipped_no_value > 0:
        skip_parts.append(f"{skipped_no_value} 个无价值")
    if skip_parts:
        process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个（跳过 {'、'.join(skip_parts)}）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("回收"),
            [
                process_line,
                f"{EMOJI_CHART} 回收比例：{int(ratio * 100)}%",
                f"{EMOJI_COIN} 获得金币：{total_refund}",
                f"{EMOJI_COIN} 当前金币：{coins_after}",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


def _is_progress_satisfied(min_tier: str, progress: dict[str, bool]) -> bool:
    # Whitelist: only the explicit "无门槛" sentinel passes without a boss check.
    # An empty / unknown min_tier must NOT bypass — otherwise a malformed DB row
    # could leak past the gate.
    if min_tier == "none":
        return True
    return bool(progress.get(min_tier, False))


async def _check_player_online(
    server: Server, player_name: str,
) -> tuple[bool | None, str]:
    """返回 (在线?, 错误原因)。None 表示查询失败；True/False 表示在线状态。"""
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


async def _load_world_progress(
    server: Server,
) -> tuple[dict[str, bool] | None, str]:
    try:
        resp = await request_server_api(server, "/nextbot/world/progress")
    except TShockRequestError:
        return None, "无法连接服务器"
    if not is_success(resp):
        return None, get_error_reason(resp)
    progress = {k: bool(v) for k, v in resp.payload.items() if isinstance(v, bool)}
    if not progress:
        return None, "返回数据格式错误"
    return progress, ""


async def _issue_give_command(
    server: Server,
    *,
    player_name: str,
    item_id: int,
    prefix_id: int,
    quantity: int,
) -> tuple[bool, str]:
    if prefix_id > 0:
        cmd = f"/give {item_id} {player_name} {quantity} {prefix_id}"
    else:
        cmd = f"/give {item_id} {player_name} {quantity}"
    try:
        resp = await request_server_api(server, "/v3/server/rawcmd", params={"cmd": cmd})
    except TShockRequestError:
        return False, "无法连接服务器"
    if not is_success(resp):
        return False, get_error_reason(resp)
    return True, ""


@claim_matcher.handle()
@command_control(
    command_key="warehouse.claim_self",
    display_name="领取仓库物品",
    permission="warehouse.claim_self",
    description="从仓库领取物品到指定服务器，需玩家在线且服务器进度满足要求",
    usage="领取仓库物品 <服务器 ID> <格子表达式> [数量]",
    category="仓库系统",
)
@require_permission("warehouse.claim_self")
async def handle_claim(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(user_id))

    args = parse_command_args_with_fallback(event, arg, "领取仓库物品")
    if not (2 <= len(args) <= 3):
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("领取", "服务器 ID 必须为整数"))
        return

    try:
        slot_indexes, is_single = _parse_slot_expression(args[1])
    except ValueError as exc:
        await bot.send(event, at + " " + reply_failure("领取", str(exc)))
        return

    quantity_arg: int | None = None
    if len(args) == 3:
        if not is_single:
            await bot.send(event, at + " " + reply_failure("领取", "多格操作不支持数量参数，请使用单格"))
            return
        try:
            quantity_arg = int(args[2])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("领取", "数量必须为正整数"))
            return
        if quantity_arg < 1:
            await bot.send(event, at + " " + reply_failure("领取", "数量必须为正整数"))
            return

    user = _load_user(user_id)
    if user is None:
        await bot.send(event, at + " " + reply_failure("领取", "未注册账号"))
        return

    server = _load_server(server_id)
    if server is None:
        await bot.send(event, at + " " + reply_failure("领取", "服务器不存在"))
        return

    player_name = str(user.name)
    online, reason = await _check_player_online(server, player_name)
    if online is None:
        await bot.send(event, at + " " + reply_failure("领取", reason))
        return
    if not online:
        await bot.send(event, at + " " + reply_failure("领取", "未在该服务器在线，请先加入游戏"))
        return

    progress, reason = await _load_world_progress(server)
    if progress is None:
        await bot.send(event, at + " " + reply_failure("领取", reason))
        return

    server_label = f"{server.id}.{server.name}"
    async with warehouse_lock(user_id):
        if is_single:
            await _claim_single(
                bot, event, at, user_id, player_name,
                server, server_label, slot_indexes[0], quantity_arg, progress,
            )
        else:
            await _claim_many(
                bot, event, at, user_id, player_name,
                server, server_label, slot_indexes, progress,
            )


async def _claim_single(
    bot: Bot, event: Event, at: object,
    user_id: str, player_name: str,
    server: Server, server_label: str,
    slot_index: int, quantity_arg: int | None,
    progress: dict[str, bool],
) -> None:
    session = get_session()
    try:
        item = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index == slot_index,
            )
            .first()
        )
        if item is None:
            await bot.send(event, at + " " + reply_failure("领取", "该格子为空"))
            return

        current_qty = int(item.quantity)
        item_id = int(item.item_id)
        prefix_id = int(item.prefix_id)
        min_tier = str(item.min_tier or "")

        if not _is_progress_satisfied(min_tier, progress):
            tier_zh = PROGRESSION_KEY_TO_ZH.get(min_tier, min_tier)
            await bot.send(
                event,
                at + " " + reply_failure("领取", f"服务器进度不足（需要：{tier_zh}）"),
            )
            return

        if quantity_arg is not None and quantity_arg > current_qty:
            await bot.send(
                event,
                at + " " + reply_failure("领取", f"数量超过该格当前数量（{current_qty}）"),
            )
            return

        claim_qty = quantity_arg if quantity_arg is not None else current_qty

        ok, reason = await _issue_give_command(
            server, player_name=player_name, item_id=item_id,
            prefix_id=prefix_id, quantity=claim_qty,
        )
        if not ok:
            await bot.send(
                event,
                at + " " + reply_failure("领取", f"发送物品失败，{reason}"),
            )
            return

        if claim_qty >= current_qty:
            session.delete(item)
            remaining = 0
        else:
            item.quantity = current_qty - claim_qty
            remaining = int(item.quantity)
        session.commit()
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"领取仓库物品成功：user_id={user_id} server_id={server.id} slot={slot_index} "
        f"item={item_id} prefix={prefix_id} qty={claim_qty} remaining={remaining}"
    )
    slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}"
    if remaining > 0:
        slot_line = f"{EMOJI_WAREHOUSE} 格子：#{slot_index}（剩余 {remaining} 件）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("领取"),
            [
                f"🎁 物品：{_format_item_label(item_id, prefix_id, claim_qty)}",
                f"{EMOJI_SERVER} 服务器：{server_label}",
                f"{EMOJI_USER} 玩家：{player_name}",
                slot_line,
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


async def _claim_many(
    bot: Bot, event: Event, at: object,
    user_id: str, player_name: str,
    server: Server, server_label: str,
    slot_indexes: list[int],
    progress: dict[str, bool],
) -> None:
    session = get_session()
    try:
        items = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == user_id,
                WarehouseItem.slot_index.in_(slot_indexes),
            )
            .all()
        )
        item_map = {int(it.slot_index): it for it in items}

        skipped_empty = 0
        skipped_progress = 0
        skipped_give_failed = 0
        processed = 0
        total_qty = 0

        # Per-slot commit so that a crash mid-loop never leaves a window where
        # the item was already /give'd in-game but still sits in the warehouse
        # (which would let the user re-claim it on next bot restart).
        for s in slot_indexes:
            it = item_map.get(s)
            if it is None:
                skipped_empty += 1
                continue
            min_tier = str(it.min_tier or "")
            if not _is_progress_satisfied(min_tier, progress):
                skipped_progress += 1
                continue
            slot_qty = int(it.quantity)
            ok, _ = await _issue_give_command(
                server, player_name=player_name,
                item_id=int(it.item_id), prefix_id=int(it.prefix_id),
                quantity=slot_qty,
            )
            if not ok:
                skipped_give_failed += 1
                continue
            session.delete(it)
            session.commit()
            total_qty += slot_qty
            processed += 1

        if processed == 0:
            await bot.send(event, at + " " + reply_failure("领取", "未找到任何可领取的格子"))
            return

        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"批量领取仓库物品：user_id={user_id} server_id={server.id} "
        f"processed={processed} skipped_empty={skipped_empty} "
        f"skipped_progress={skipped_progress} skipped_give_failed={skipped_give_failed} "
        f"total_qty={total_qty}"
    )
    process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个"
    skip_parts: list[str] = []
    if skipped_empty > 0:
        skip_parts.append(f"{skipped_empty} 个空")
    if skipped_progress > 0:
        skip_parts.append(f"{skipped_progress} 个进度不足")
    if skipped_give_failed > 0:
        skip_parts.append(f"{skipped_give_failed} 个发送失败")
    if skip_parts:
        process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个（跳过 {'、'.join(skip_parts)}）"

    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("领取"),
            [
                f"{EMOJI_SERVER} 服务器：{server_label}",
                f"{EMOJI_USER} 玩家：{player_name}",
                process_line,
                f"🎁 共领取：{total_qty} 件物品",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


@gift_matcher.handle()
@command_control(
    command_key="warehouse.gift_self",
    display_name="赠送仓库物品",
    permission="warehouse.gift_self",
    description="把自己仓库的物品赠送给其他用户，支持单格 / 区间 / 列表 / 全部，单格可指定数量",
    usage="赠送仓库物品 <用户 QQ/@用户/用户名称> <格子表达式> [数量]",
    category="仓库系统",
)
@require_permission("warehouse.gift_self")
async def handle_gift(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    sender_id = event.get_user_id()
    at = OBV11MessageSegment.at(int(sender_id))

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "赠送仓库物品", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("赠送", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("赠送", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error or target_user_id is None:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "赠送仓库物品")
    if not (2 <= len(args) <= 3):
        raise_command_usage()

    if sender_id == target_user_id:
        await bot.send(event, at + " " + reply_failure("赠送", "不能赠送给自己"))
        return

    try:
        slot_indexes, is_single = _parse_slot_expression(args[1])
    except ValueError as exc:
        await bot.send(event, at + " " + reply_failure("赠送", str(exc)))
        return

    quantity_arg: int | None = None
    if len(args) == 3:
        if not is_single:
            await bot.send(event, at + " " + reply_failure("赠送", "多格操作不支持数量参数，请使用单格"))
            return
        try:
            quantity_arg = int(args[2])
        except ValueError:
            await bot.send(event, at + " " + reply_failure("赠送", "数量必须为正整数"))
            return
        if quantity_arg < 1:
            await bot.send(event, at + " " + reply_failure("赠送", "数量必须为正整数"))
            return

    if _load_user(sender_id) is None:
        await bot.send(event, at + " " + reply_failure("赠送", "请先注册账号"))
        return

    target_user = _load_user(target_user_id)
    if target_user is None:
        await bot.send(event, at + " " + reply_failure("赠送", "未找到该用户"))
        return

    target_name = str(target_user.name)
    async with _acquire_two_warehouse_locks(sender_id, target_user_id):
        if is_single:
            await _gift_single(
                bot, event, at, sender_id,
                target_user_id, target_name, slot_indexes[0], quantity_arg,
            )
        else:
            await _gift_many(
                bot, event, at, sender_id,
                target_user_id, target_name, slot_indexes,
            )


async def _gift_single(
    bot: Bot, event: Event, at: object,
    sender_id: str,
    target_user_id: str, target_name: str,
    slot_index: int, quantity_arg: int | None,
) -> None:
    session = get_session()
    try:
        item = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == sender_id,
                WarehouseItem.slot_index == slot_index,
            )
            .first()
        )
        if item is None:
            await bot.send(event, at + " " + reply_failure("赠送", "该格子为空"))
            return

        current_qty = int(item.quantity)
        if quantity_arg is not None and quantity_arg > current_qty:
            await bot.send(
                event,
                at + " " + reply_failure("赠送", f"数量超过该格当前数量（{current_qty}）"),
            )
            return

        target_slot = _find_first_empty_slot(session, target_user_id)
        if target_slot is None:
            await bot.send(event, at + " " + reply_failure("赠送", "对方仓库已满"))
            return

        gift_qty = quantity_arg if quantity_arg is not None else current_qty
        item_id = int(item.item_id)
        prefix_id = int(item.prefix_id)
        min_tier = str(item.min_tier or "none")
        value = int(item.value or 0)

        if gift_qty >= current_qty:
            session.delete(item)
            sender_remaining = 0
        else:
            item.quantity = current_qty - gift_qty
            sender_remaining = int(item.quantity)

        session.add(
            WarehouseItem(
                user_id=target_user_id,
                slot_index=target_slot,
                item_id=item_id,
                prefix_id=prefix_id,
                quantity=gift_qty,
                min_tier=min_tier,
                value=value,
                created_at=db_now_utc_naive(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # Defensive: both warehouse locks are held, so concurrent gifts can't
            # race. Triggers only if a path bypassing the lock (e.g. WebUI) inserts
            # into the chosen recipient slot in between.
            session.rollback()
            logger.warning(
                f"赠送仓库物品冲突：sender={sender_id} target={target_user_id} "
                f"dst_slot={target_slot}"
            )
            await bot.send(
                event,
                at + " " + reply_failure("赠送", "对方格子被占用，请稍后重试"),
            )
            return
        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == sender_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"赠送仓库物品成功：sender={sender_id} target={target_user_id} "
        f"src_slot={slot_index} dst_slot={target_slot} item={item_id} "
        f"prefix={prefix_id} qty={gift_qty} sender_remaining={sender_remaining}"
    )
    src_slot_line = f"{EMOJI_WAREHOUSE} 来源格子：#{slot_index}"
    if sender_remaining > 0:
        src_slot_line = f"{EMOJI_WAREHOUSE} 来源格子：#{slot_index}（剩余 {sender_remaining} 件）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("赠送"),
            [
                f"🎁 物品：{_format_item_label(item_id, prefix_id, gift_qty)}",
                f"{EMOJI_USER} 接收者：{target_name}（{target_user_id}）",
                src_slot_line,
                f"{EMOJI_WAREHOUSE} 接收格子：#{target_slot}",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )


async def _gift_many(
    bot: Bot, event: Event, at: object,
    sender_id: str,
    target_user_id: str, target_name: str,
    slot_indexes: list[int],
) -> None:
    session = get_session()
    try:
        items = (
            session.query(WarehouseItem)
            .filter(
                WarehouseItem.user_id == sender_id,
                WarehouseItem.slot_index.in_(slot_indexes),
            )
            .all()
        )
        item_map = {int(it.slot_index): it for it in items}

        skipped_empty = 0
        skipped_full = 0
        skipped_conflict = 0
        processed = 0
        total_qty = 0

        # Per-slot commit so a crash mid-loop never leaves the recipient with the
        # new row while the sender's row is still present (or vice versa).
        for s in slot_indexes:
            it = item_map.get(s)
            if it is None:
                skipped_empty += 1
                continue
            target_slot = _find_first_empty_slot(session, target_user_id)
            if target_slot is None:
                skipped_full += 1
                continue
            slot_qty = int(it.quantity)
            session.add(
                WarehouseItem(
                    user_id=target_user_id,
                    slot_index=target_slot,
                    item_id=int(it.item_id),
                    prefix_id=int(it.prefix_id),
                    quantity=slot_qty,
                    min_tier=str(it.min_tier or "none"),
                    value=int(it.value or 0),
                    created_at=db_now_utc_naive(),
                )
            )
            session.delete(it)
            try:
                session.commit()
            except IntegrityError:
                # Distinct from "warehouse full" — the chosen recipient slot was
                # taken between our scan and commit by some path that bypasses
                # the warehouse_lock (currently only the WebUI).
                session.rollback()
                logger.warning(
                    f"赠送仓库物品冲突（多格）：sender={sender_id} "
                    f"target={target_user_id} src_slot={s} dst_slot={target_slot}"
                )
                skipped_conflict += 1
                continue
            total_qty += slot_qty
            processed += 1

        if processed == 0:
            await bot.send(event, at + " " + reply_failure("赠送", "未找到任何可赠送的格子"))
            return

        used_after = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == sender_id)
            .count()
        )
    finally:
        session.close()

    logger.info(
        f"批量赠送仓库物品：sender={sender_id} target={target_user_id} "
        f"processed={processed} skipped_empty={skipped_empty} "
        f"skipped_full={skipped_full} skipped_conflict={skipped_conflict} "
        f"total_qty={total_qty}"
    )
    process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个"
    skip_parts: list[str] = []
    if skipped_empty > 0:
        skip_parts.append(f"{skipped_empty} 个空")
    if skipped_full > 0:
        skip_parts.append(f"{skipped_full} 个对方仓库已满")
    if skipped_conflict > 0:
        skip_parts.append(f"{skipped_conflict} 个格子冲突")
    if skip_parts:
        process_line = f"{EMOJI_WAREHOUSE} 处理格子：{processed} 个（跳过 {'、'.join(skip_parts)}）"
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("赠送"),
            [
                f"{EMOJI_USER} 接收者：{target_name}（{target_user_id}）",
                process_line,
                f"🎁 共赠送：{total_qty} 件物品",
                f"{EMOJI_CHART} 已使用：{used_after} / {WAREHOUSE_CAPACITY}",
            ],
        ),
    )
