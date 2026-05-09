from __future__ import annotations

import asyncio
import math
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from nextbot.command_config import (
    command_control,
    get_current_param,
    raise_command_usage,
)
from nextbot.db import RedPacket, RedPacketClaim, User, execute_rowcount, get_session
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.economy import MAX_COINS_AMOUNT, add_coins_with_cap
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_COIN,
    EMOJI_GAME,
    EMOJI_LIST,
    EMOJI_RED_PACKET,
    reply_block,
    reply_failure,
    reply_success,
    safe_at_segment_or_empty,
)
from nextbot.time_utils import db_now_utc_naive, format_beijing_datetime
from server.screenshot import ScreenshotOptions
from server.web_server import create_red_packet_all_page, create_red_packet_own_page

send_matcher = on_command("发红包")
grab_matcher = on_command("抢红包")
withdraw_matcher = on_command("收回红包")
list_own_matcher = on_command("我的红包")
list_all_matcher = on_command("红包列表")

_TYPE_ZH_TO_EN = {"平分": "equal", "拼手气": "lucky"}
_TYPE_EN_TO_ZH = {v: k for k, v in _TYPE_ZH_TO_EN.items()}
_STATUS_ZH = {"active": "进行中", "exhausted": "已抢完", "withdrawn": "已收回"}

_RED_PACKET_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=600,
    full_page=True,
    fit_content_height=True,
)

# 红包列表渲染共享并发上限，防止刷命令撑爆 Playwright
_red_packet_screenshot_semaphore = asyncio.Semaphore(2)


def _draw_equal(remaining_amount: int, remaining_count: int, base: int) -> int:
    if remaining_count <= 1:
        return remaining_amount
    return min(base, remaining_amount - (remaining_count - 1))


def _draw_lucky(remaining_amount: int, remaining_count: int) -> int:
    if remaining_count <= 1:
        return remaining_amount
    avg = remaining_amount / remaining_count
    high = max(1, int(avg * 2))
    high = min(high, remaining_amount - (remaining_count - 1))
    if high < 1:
        logger.warning(
            f"_draw_lucky 边界异常：remaining_amount={remaining_amount}，"
            f"remaining_count={remaining_count}，high={high}"
        )
        return 1
    return random.randint(1, high)


def _claim_slot_atomic(session, packet_id: int, draw_amount: int) -> bool:
    stmt = (
        sa_update(RedPacket)
        .where(RedPacket.id == packet_id)
        .where(RedPacket.status == "active")
        .where(RedPacket.remaining_count > 0)
        .where(RedPacket.remaining_amount >= draw_amount)
        .values(
            remaining_count=RedPacket.remaining_count - 1,
            remaining_amount=RedPacket.remaining_amount - draw_amount,
        )
    )
    return execute_rowcount(session, stmt) > 0


@send_matcher.handle()
@command_control(
    command_key="economy.red_packet.send",
    display_name="发红包",
    permission="economy.red_packet.send",
    description="发一个红包让别人抢",
    usage="发红包 <平分/拼手气> <红包名称> <红包总金额> <红包个数>",
    params={
        "max_count": {
            "type": "int",
            "label": "最大个数",
            "description": "单个红包最多多少个位置",
            "required": False,
            "default": 100,
            "min": 1,
            "max": 1000,
        },
        "min_amount_per_slot": {
            "type": "int",
            "label": "每份最低金额",
            "description": "每个位置至少分到多少金币",
            "required": False,
            "default": 1,
            "min": 1,
        },
    },
    category="红包系统",
)
@require_permission("economy.red_packet.send")
async def handle_send(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())
    args = parse_command_args_with_fallback(event, arg, "发红包")
    if len(args) != 4:
        raise_command_usage()

    type_zh = args[0].strip()
    if type_zh not in _TYPE_ZH_TO_EN:
        await bot.send(event, at + " " + reply_failure("发红包", "类型仅支持 平分 或 拼手气"))
        return
    type_en = _TYPE_ZH_TO_EN[type_zh]

    name = args[1].strip()
    if not name:
        raise_command_usage()
    if len(name) > 32:
        await bot.send(event, at + " " + reply_failure("发红包", "红包名称长度不能超过 32 字符"))
        return

    try:
        total_amount = int(args[2])
        count = int(args[3])
    except ValueError:
        await bot.send(event, at + " " + reply_failure("发红包", "总金额和个数必须为正整数"))
        return
    if total_amount <= 0 or count <= 0:
        await bot.send(event, at + " " + reply_failure("发红包", "总金额和个数必须为正整数"))
        return
    if total_amount > MAX_COINS_AMOUNT:
        await bot.send(
            event,
            at + " " + reply_failure("发红包", f"金额过大（最多 {MAX_COINS_AMOUNT}）"),
        )
        return

    max_count = max(1, int(get_current_param("max_count", 100)))
    min_amount_per_slot = max(1, int(get_current_param("min_amount_per_slot", 1)))

    if count > max_count:
        await bot.send(event, at + " " + reply_failure("发红包", f"个数超过上限 {max_count}"))
        return
    if total_amount < count * min_amount_per_slot:
        await bot.send(
            event,
            at + " " + reply_failure("发红包", f"总金额不足以每人至少 {min_amount_per_slot} 金币"),
        )
        return

    user_id = event.get_user_id()
    send_success = False
    session = get_session()
    try:
        existing = session.query(RedPacket).filter(RedPacket.name == name).first()
        if existing is not None:
            await bot.send(event, at + " " + reply_failure("发红包", "红包名称已被使用过，请换一个"))
            return

        sender = session.query(User).filter(User.user_id == user_id).first()
        if sender is None:
            await bot.send(event, at + " " + reply_failure("发红包", "请先注册账号"))
            return

        # 原子条件 UPDATE：扣 sender 余额（带 coins ≥ total_amount 条件）
        # 并发被抢走时 rowcount=0，回退"金币不足"
        rowcount = execute_rowcount(
            session,
            sa_update(User)
            .where(User.user_id == user_id, User.coins >= total_amount)
            .values(coins=User.coins - total_amount),
        )
        if rowcount == 0:
            sender_coins_now = int(
                session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
            )
            await bot.send(
                event,
                at + " " + reply_failure("发红包", f"金币不足（当前 {sender_coins_now}，需 {total_amount}）"),
            )
            return

        packet = RedPacket(
            name=name,
            sender_user_id=user_id,
            type=type_en,
            total_amount=total_amount,
            total_count=count,
            remaining_amount=total_amount,
            remaining_count=count,
            status="active",
        )
        session.add(packet)
        try:
            session.commit()
        except IntegrityError:
            # 名称撞 UNIQUE 等约束冲突：rollback 自动撤销之前的扣款 UPDATE
            # （SQLAlchemy 2.0 + autocommit=False 事务原子性保证）
            session.rollback()
            await bot.send(event, at + " " + reply_failure("发红包", "红包名称已被使用过，请换一个"))
            return

        send_success = True
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"发红包处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("发红包", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    if not send_success:
        return

    logger.info(
        f"发红包成功：user_id={user_id}，name={name}，type={type_en}，"
        f"total_amount={total_amount}，count={count}"
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            f"{EMOJI_RED_PACKET} 发红包成功",
            [
                f"{EMOJI_LIST} 名称：{name}",
                f"{EMOJI_GAME} 类型：{type_zh}",
                f"{EMOJI_COIN} 总金额：{total_amount} 金币 / {count} 份",
            ],
            hint=f"输入 `抢红包 {name}` 即可参与",
        ),
    )


@grab_matcher.handle()
@command_control(
    command_key="economy.red_packet.grab",
    display_name="抢红包",
    permission="economy.red_packet.grab",
    description="凭红包名称抢红包",
    usage="抢红包 <红包名称>",
    category="红包系统",
)
@require_permission("economy.red_packet.grab")
async def handle_grab(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())
    args = parse_command_args_with_fallback(event, arg, "抢红包")
    if len(args) != 1:
        raise_command_usage()

    name = args[0].strip()
    if not name:
        raise_command_usage()

    user_id = event.get_user_id()

    grab_success = False
    packet_name = ""
    packet_type = ""
    packet_total_amount = 0
    draw_amount = 0
    taken_amount = 0
    actual_grab_amount = 0
    coin_capped = False
    session = get_session()
    try:
        packet = session.query(RedPacket).filter(RedPacket.name == name).first()
        if packet is None:
            await bot.send(event, at + " " + reply_failure("抢红包", "红包不存在"))
            return
        if packet.status != "active":
            await bot.send(event, at + " " + reply_failure("抢红包", "该红包已关闭"))
            return

        already = (
            session.query(RedPacketClaim)
            .filter(RedPacketClaim.red_packet_id == packet.id)
            .filter(RedPacketClaim.claimer_user_id == user_id)
            .first()
        )
        if already is not None:
            await bot.send(event, at + " " + reply_failure("抢红包", "你已经抢过这个红包了"))
            return

        remaining_amount = int(packet.remaining_amount)
        remaining_count = int(packet.remaining_count)
        if remaining_count <= 0 or remaining_amount <= 0:
            await bot.send(event, at + " " + reply_failure("抢红包", "该红包已关闭"))
            return

        if packet.type == "lucky":
            draw_amount = _draw_lucky(remaining_amount, remaining_count)
        else:
            base = int(packet.total_amount) // int(packet.total_count)
            draw_amount = _draw_equal(remaining_amount, remaining_count, base)
        draw_amount = max(1, draw_amount)

        packet_id = int(packet.id)
        packet_name = str(packet.name)
        packet_type = str(packet.type)
        packet_total_amount = int(packet.total_amount)

        if not _claim_slot_atomic(session, packet_id, draw_amount):
            session.rollback()
            await bot.send(event, at + " " + reply_failure("抢红包", "手慢了一步"))
            return

        claim = RedPacketClaim(
            red_packet_id=packet_id,
            claimer_user_id=user_id,
            amount=draw_amount,
        )
        session.add(claim)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            await bot.send(event, at + " " + reply_failure("抢红包", "你已经抢过这个红包了"))
            return

        grabber = session.query(User).filter(User.user_id == user_id).first()
        if grabber is None:
            session.rollback()
            await bot.send(event, at + " " + reply_failure("抢红包", "请先注册账号"))
            return
        # SF-X.1：账户上限保护——触顶时按可加余量加（partial cap）
        applied_amount, capped = add_coins_with_cap(session, user_id, draw_amount)
        actual_grab_amount = applied_amount
        coin_capped = capped and applied_amount < draw_amount
        if coin_capped:
            logger.warning(
                f"抢红包触顶 cap：user_id={user_id} packet_id={packet_id} "
                f"requested={draw_amount} applied={applied_amount}"
            )
            # R3E-1：partial cap 下未入账的金币凭空蒸发
            #
            # _claim_slot_atomic 已扣 packet 全额 draw_amount 但用户只入账 applied，
            # 差额 (draw_amount - applied) 必须退回 packet（参考 economy.transfer
            # sender refund 模式 economy.py:469-482）。同时把 RedPacketClaim.amount
            # 改成实际入账值，让事后审计与展示能对上。
            refund = draw_amount - applied_amount
            refund_rowcount = execute_rowcount(
                session,
                sa_update(RedPacket)
                .where(RedPacket.id == packet_id)
                .values(
                    remaining_amount=RedPacket.remaining_amount + refund,
                    remaining_count=RedPacket.remaining_count + 1,
                ),
            )
            if refund_rowcount == 0:
                # 极罕见：退还失败（packet 已被 withdraw 等竞态修改）。
                # 这是 DB 内一致性问题，必须 CRITICAL log 让管理员介入对账。
                logger.error(
                    f"[CRITICAL] 红包退还失败：packet_id={packet_id} user_id={user_id} "
                    f"refund={refund} draw={draw_amount} applied={applied_amount}"
                )
            # 改 RedPacketClaim.amount 为实际入账值（claim 已 flush 但未 commit）
            claim.amount = applied_amount

        refreshed_packet = session.query(RedPacket).filter(RedPacket.id == packet_id).first()
        if refreshed_packet is not None and int(refreshed_packet.remaining_count) == 0:
            refreshed_packet.status = "exhausted"
            refreshed_packet.closed_at = db_now_utc_naive()

        session.commit()
        taken_amount = packet_total_amount - (
            int(refreshed_packet.remaining_amount) if refreshed_packet is not None else 0
        )
        grab_success = True
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"抢红包处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("抢红包", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    if not grab_success:
        return

    type_zh = _TYPE_EN_TO_ZH.get(packet_type, packet_type)
    logger.info(
        f"金币变更：actor={user_id} target={user_id} action=red_packet.grab "
        f"packet={packet_name} type={packet_type} requested={draw_amount} "
        f"applied={actual_grab_amount} taken={taken_amount}/{packet_total_amount} reason=grab"
    )
    success_lines = [
        f"{EMOJI_LIST} 名称：{packet_name}（{type_zh}）",
        f"{EMOJI_COIN} 获得 {actual_grab_amount} 金币",
        f"{EMOJI_CHART} 已抢 {taken_amount}/{packet_total_amount}",
    ]
    if coin_capped:
        # R3E-1：未入账金币已退回红包，提示用户而非"未入账"
        if actual_grab_amount > 0:
            success_lines.append(
                f"⚠️ 已触账户上限，{draw_amount - actual_grab_amount} 金币已退回红包",
            )
        else:
            success_lines.append(
                f"⚠️ 已触账户上限，本次未入账（{draw_amount} 金币已退回红包）",
            )
    await bot.send(
        event,
        at + "\n" + reply_block(
            f"{EMOJI_RED_PACKET} 抢红包成功",
            success_lines,
        ),
    )


@withdraw_matcher.handle()
@command_control(
    command_key="economy.red_packet.withdraw",
    display_name="收回红包",
    permission="economy.red_packet.withdraw",
    description="收回自己发出的红包，剩余金额退回",
    usage="收回红包 <红包名称>",
    category="红包系统",
)
@require_permission("economy.red_packet.withdraw")
async def handle_withdraw(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())
    args = parse_command_args_with_fallback(event, arg, "收回红包")
    if len(args) != 1:
        raise_command_usage()

    name = args[0].strip()
    if not name:
        raise_command_usage()

    user_id = event.get_user_id()

    withdraw_success = False
    refund_amount = 0
    actual_refund_amount = 0
    coin_capped = False
    session = get_session()
    try:
        packet = session.query(RedPacket).filter(RedPacket.name == name).first()
        if packet is None:
            await bot.send(event, at + " " + reply_failure("收回红包", "红包不存在"))
            return
        if packet.sender_user_id != user_id:
            await bot.send(event, at + " " + reply_failure("收回红包", "只能收回自己发的红包"))
            return
        if packet.status != "active":
            await bot.send(event, at + " " + reply_failure("收回红包", "该红包已关闭"))
            return

        packet_id = int(packet.id)
        stmt = (
            sa_update(RedPacket)
            .where(RedPacket.id == packet_id)
            .where(RedPacket.status == "active")
            .values(status="withdrawn", closed_at=db_now_utc_naive())
        )
        if execute_rowcount(session, stmt) == 0:
            session.rollback()
            await bot.send(event, at + " " + reply_failure("收回红包", "该红包已关闭"))
            return

        refreshed_packet = session.query(RedPacket).filter(RedPacket.id == packet_id).first()
        refund_amount = int(refreshed_packet.remaining_amount) if refreshed_packet else 0

        sender = session.query(User).filter(User.user_id == user_id).first()
        if sender is None:
            session.rollback()
            await bot.send(event, at + " " + reply_failure("收回红包", "请先注册账号"))
            return
        # SF-X.1：账户上限保护——触顶时按可加余量加（partial cap）
        applied_amount, capped = add_coins_with_cap(session, user_id, refund_amount)
        actual_refund_amount = applied_amount
        coin_capped = capped and applied_amount < refund_amount
        if coin_capped:
            logger.warning(
                f"收回红包触顶 cap：user_id={user_id} packet={name} "
                f"requested={refund_amount} applied={applied_amount}"
            )
        session.commit()
        withdraw_success = True
    except Exception:  # noqa: BLE001
        # R4R-2.1：commit 前任意路径抛异常时显式 rollback。
        session.rollback()
        logger.exception(f"收回红包处理异常：user_id={user_id}")
        try:
            await bot.send(event, at + " " + reply_failure("收回红包", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
    finally:
        session.close()

    if not withdraw_success:
        return

    logger.info(
        f"金币变更：actor={user_id} target={user_id} action=red_packet.withdraw "
        f"packet={name} requested={refund_amount} applied={actual_refund_amount} reason=withdraw"
    )
    success_lines = [
        f"{EMOJI_RED_PACKET} 红包：{name}",
        f"{EMOJI_COIN} 退回：{actual_refund_amount} 金币",
    ]
    if coin_capped:
        success_lines.append(
            f"⚠️ 已触账户上限，{refund_amount - actual_refund_amount} 金币未退回",
        )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("收回"),
            success_lines,
        ),
    )


async def _send_red_packet_image(
    bot: Bot,
    event: Event,
    *,
    page_url: str,
    file_prefix: str,
) -> None:
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=_RED_PACKET_SCREENSHOT_OPTIONS,
        file_prefix=file_prefix,
        semaphore=_red_packet_screenshot_semaphore,
        failure_action="查询",
    )


@list_own_matcher.handle()
@command_control(
    command_key="economy.red_packet.list_own",
    display_name="我的红包",
    permission="economy.red_packet.list_own",
    description="查看自己发出过的红包",
    usage="我的红包 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页条数",
            "description": "每页显示的红包数量",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
    category="红包系统",
)
@require_permission("economy.red_packet.list_own")
async def handle_list_own(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "我的红包")
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
    user_id = event.get_user_id()

    try:
        session = get_session()
        try:
            total = (
                session.query(RedPacket)
                .filter(RedPacket.sender_user_id == user_id)
                .count()
            )
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            if total > 0 and page > total_pages:
                await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
                return
            offset = (page - 1) * limit
            packets = (
                session.query(RedPacket)
                .filter(RedPacket.sender_user_id == user_id)
                .order_by(RedPacket.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
        finally:
            session.close()

        entries: list[dict[str, object]] = []
        for i, packet in enumerate(packets):
            type_zh = _TYPE_EN_TO_ZH.get(str(packet.type), str(packet.type))
            status_zh = _STATUS_ZH.get(str(packet.status), str(packet.status))
            taken = int(packet.total_amount) - int(packet.remaining_amount)
            taken_count = int(packet.total_count) - int(packet.remaining_count)
            created = format_beijing_datetime(packet.created_at) if packet.created_at else ""
            entries.append(
                {
                    "index": offset + i + 1,
                    "name": str(packet.name),
                    "type_zh": type_zh,
                    "total_amount": int(packet.total_amount),
                    "taken": taken,
                    "total_count": int(packet.total_count),
                    "taken_count": taken_count,
                    "status_zh": status_zh,
                    "created": created,
                }
            )

        page_url = create_red_packet_own_page(
            page=page, total_pages=total_pages, entries=entries,
        )
        logger.info(
            f"我的红包渲染地址：user_id={user_id} page={page}/{total_pages} total={total} internal_url={page_url}"
        )
        await _send_red_packet_image(bot, event, page_url=page_url, file_prefix="red-packet-own")
    except Exception:  # noqa: BLE001
        logger.exception(f"我的红包处理异常：user_id={user_id}")
        try:
            await bot.send(event, reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return


@list_all_matcher.handle()
@command_control(
    command_key="economy.red_packet.list_all",
    display_name="红包列表",
    permission="economy.red_packet.list_all",
    description="查看当前可抢的红包",
    usage="红包列表 [页数]",
    params={
        "limit": {
            "type": "int",
            "label": "每页条数",
            "description": "每页显示的红包数量",
            "required": False,
            "default": 10,
            "min": 1,
            "max": 50,
        },
    },
    category="红包系统",
)
@require_permission("economy.red_packet.list_all")
async def handle_list_all(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "红包列表")
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
    user_id = event.get_user_id()

    try:
        session = get_session()
        try:
            total = (
                session.query(RedPacket)
                .filter(RedPacket.status == "active")
                .count()
            )
            total_pages = max(1, math.ceil(total / limit)) if total > 0 else 1
            if total > 0 and page > total_pages:
                await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))
                return
            offset = (page - 1) * limit
            packets = (
                session.query(RedPacket)
                .filter(RedPacket.status == "active")
                .order_by(RedPacket.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            sender_ids = {p.sender_user_id for p in packets}
            senders = (
                session.query(User).filter(User.user_id.in_(sender_ids)).all()
                if sender_ids
                else []
            )
            name_map = {u.user_id: u.name for u in senders}
        finally:
            session.close()

        entries: list[dict[str, object]] = []
        for i, packet in enumerate(packets):
            type_zh = _TYPE_EN_TO_ZH.get(str(packet.type), str(packet.type))
            entries.append(
                {
                    "index": offset + i + 1,
                    "name": str(packet.name),
                    "sender_name": name_map.get(packet.sender_user_id, "未知"),
                    "sender_user_id": str(packet.sender_user_id),
                    "type_zh": type_zh,
                    "remaining_amount": int(packet.remaining_amount),
                    "total_amount": int(packet.total_amount),
                    "remaining_count": int(packet.remaining_count),
                    "total_count": int(packet.total_count),
                }
            )

        page_url = create_red_packet_all_page(
            page=page, total_pages=total_pages, entries=entries,
        )
        logger.info(
            f"红包列表渲染地址：page={page}/{total_pages} total={total} internal_url={page_url}"
        )
        await _send_red_packet_image(bot, event, page_url=page_url, file_prefix="red-packet-all")
    except Exception:  # noqa: BLE001
        logger.exception(f"红包列表处理异常：user_id={user_id}")
        try:
            await bot.send(event, reply_failure("查询", "处理失败，请稍后重试"))
        except Exception:  # noqa: BLE001
            pass
        return
