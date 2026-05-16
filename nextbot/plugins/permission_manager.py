"""用户权限 / 身份组管理命令。

3 个保留命令位于"权限管理"分类：

- ``管理员列表``：并行获取昵称（asyncio.gather + wait_for），截图统一走
  ``render_and_send_screenshot`` helper（内置 base64 size cap + per-handler
  semaphore + V11 / 非 V11 分支）
- ``同步访客权限``：保留两步确认（已正确）
- ``重置访客权限``：reset 到 DEFAULT_GUEST_PERMISSIONS（清掉额外
  权限），二次确认 + 列出将移除 / 新增的 key

历史上的「添加用户权限 / 删除用户权限 / 修改用户身份组」已迁至 WebUI
（``/webui/users``），命令已下线。
"""
from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from sqlalchemy import update

from nextbot.access_control import get_owner_ids_ordered
from nextbot.audit import audit_permission_change
from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import (
    DEFAULT_GUEST_PERMISSIONS,
    Group,
    execute_rowcount,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import (
    join_csv_values,
    require_permission,
    split_csv_values,
)
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_GROUP,
    EMOJI_LOCK,
    reply_block,
    reply_failure,
    reply_info,
    reply_success,
    safe_at_segment_or_empty,
)
from server.screenshot import ScreenshotOptions
from server.web_server import create_admin_list_page

admin_list_matcher = on_command("管理员列表")
sync_guest_perms_matcher = on_command("同步访客权限")
reset_guest_perms_matcher = on_command("重置访客权限")

ADMIN_LIST_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=400,
    full_page=True,
    fit_content_height=True,
)

# 管理员列表 handler-wide semaphore，限制并发渲染数量，与 ban / shop / leaderboard 等
# 业务隔离，防止 Playwright 进程膨胀。
_admin_list_semaphore = asyncio.Semaphore(2)

# PMB-4.1：单个 owner 昵称获取超时（秒），超时后用占位符兜底，
# 避免 N 个 owner × per-call timeout 累积阻塞 handler。
_NICKNAME_FETCH_TIMEOUT = 5.0

# 重试次数（沿用既有上限）
_CSV_UPDATE_RETRY = 5


def _operator_id(event: Event) -> str:
    return event.get_user_id()


def _at_segment(event: Event) -> OBV11MessageSegment:
    # PC-4.1：使用 safe_at_segment_or_empty，非数字 user_id 退化为空文本段
    return safe_at_segment_or_empty(event.get_user_id())


async def _fetch_nickname_via_bot(bot: Bot, qq: str) -> str:
    """通过 OneBot V11 get_stranger_info 获取昵称，编码由 NapCat 处理。"""
    try:
        info = await bot.call_api("get_stranger_info", user_id=int(qq))
        return str(info.get("nickname", "")).strip()
    except Exception as exc:
        logger.info(f"get_stranger_info 失败：qq={qq} reason={exc}")
        return ""


async def _fetch_nickname_with_timeout(bot: Bot, qq: str) -> tuple[str, str]:
    """带 timeout 的昵称获取，失败时用占位符兜底。"""
    try:
        nickname = await asyncio.wait_for(
            _fetch_nickname_via_bot(bot, qq), timeout=_NICKNAME_FETCH_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning(f"获取管理员昵称超时：qq={qq}")
        nickname = "（获取超时）"
    except Exception as exc:
        logger.warning(f"获取管理员昵称失败：qq={qq} reason={exc!r}")
        nickname = "（获取失败）"
    return qq, nickname


# ---------------------------------------------------------------------------
# 管理员列表（并行昵称 + base64 size cap）
# ---------------------------------------------------------------------------


@admin_list_matcher.handle()
@command_control(
    command_key="permission.admin.list",
    display_name="管理员列表",
    permission="permission.admin.list",
    description="查看 Bot 管理员列表",
    usage="管理员列表",
    params={
        "keep_order": {
            "type": "bool",
            "label": "按配置顺序显示",
            "description": "开启后按 .env 中填写的 QQ 号顺序显示，关闭则按 QQ 号排序",
            "required": False,
            "default": True,
        },
    },
    category="权限管理",
)
@require_permission("permission.admin.list")
async def handle_admin_list(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "管理员列表")
    if args:
        raise_command_usage()

    keep_order = bool(get_current_param("keep_order", True))
    owner_ids = get_owner_ids_ordered() if keep_order else sorted(get_owner_ids_ordered())
    if not owner_ids:
        await bot.send(event, reply_failure("查询", "未配置管理员（owner_id）"))
        return

    logger.info(f"管理员列表查询：owner_count={len(owner_ids)}")

    # PMB-4.1：并行 + 单条 timeout，避免串行 N+1 阻塞
    results = await asyncio.gather(
        *(_fetch_nickname_with_timeout(bot, qq) for qq in owner_ids)
    )
    admins: list[dict[str, str]] = [
        {"user_id": qq, "nickname": nickname} for qq, nickname in results
    ]
    logger.info(f"管理员昵称获取完成：count={len(admins)}")

    page_url = create_admin_list_page(admins=admins)
    logger.info(f"管理员列表渲染地址：admin_count={len(admins)} internal_url={page_url}")

    # PMB-4.2：helper 内置 base64 size cap + 非 V11 fallback；handler-wide
    # semaphore 限并发，避免恶意模板生成超大图把进程打爆。
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=ADMIN_LIST_SCREENSHOT_OPTIONS,
        file_prefix="admin-list",
        semaphore=_admin_list_semaphore,
        failure_action="查询",
    )


# ---------------------------------------------------------------------------
# 同步访客权限（保留两步确认；audit log 增加 operator）
# ---------------------------------------------------------------------------


_SYNC_CONFIRM_TOKEN = "确认"
_SYNC_GROUP_NAME = "guest"


def _diff_guest_default_permissions() -> tuple[list[str], list[str], int, int]:
    """Return (current_sorted, missing_sorted, current_count, target_count).

    Reads the live `guest` row, splits its CSV, diffs against the in-code default
    set. Missing keys are returned sorted for stable display.
    """
    session = get_session()
    try:
        guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
        current = set(split_csv_values(guest.permissions)) if guest is not None else set()
    finally:
        session.close()
    missing = sorted(DEFAULT_GUEST_PERMISSIONS - current)
    target = current | set(missing)
    return sorted(current), missing, len(current), len(target)


@sync_guest_perms_matcher.handle()
@command_control(
    command_key="permission.group.guest.sync",
    display_name="同步访客权限",
    permission="permission.group.guest.sync",
    description="把 guest 身份组补全至默认权限集（仅新增、不删除已有权限），需二次确认",
    usage="同步访客权限",
    category="权限管理",
)
@require_permission("permission.group.guest.sync")
async def handle_sync_guest_perms(
    bot: Bot, event: Event, matcher: Matcher, arg: Message = CommandArg(),
) -> None:
    args = parse_command_args_with_fallback(event, arg, "同步访客权限")
    if args:
        raise_command_usage()

    at = _at_segment(event)
    _, missing, current_count, target_count = _diff_guest_default_permissions()

    if not missing:
        await matcher.finish(
            at + "\n" + reply_block(
                reply_success("同步", "无需补全"),
                [
                    f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
                    f"{EMOJI_CHART} 已有权限：{current_count} 个",
                ],
            )
        )

    matcher.state["sync_missing"] = missing
    matcher.state["sync_current_count"] = current_count
    matcher.state["sync_target_count"] = target_count
    matcher.state["sync_caller_user_id"] = event.get_user_id()

    preview_lines = [
        f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
        f"{EMOJI_LOCK} 缺失权限：{len(missing)} 个",
    ]
    preview_lines.extend(f"• {key}" for key in missing)
    preview_lines.append(
        f"{EMOJI_CHART} 当前已有：{current_count} 个 → 同步后：{target_count} 个",
    )
    await matcher.send(
        at + "\n" + reply_block(
            reply_info("同步预览"),
            preview_lines,
            hint=f"回复「{_SYNC_CONFIRM_TOKEN}」执行同步，回复其他内容取消",
        )
    )


@sync_guest_perms_matcher.got("confirm_reply")
async def handle_sync_guest_perms_confirm(
    bot: Bot, event: Event, matcher: Matcher,
    confirm_reply: Message = Arg("confirm_reply"),
) -> None:
    # Defense-in-depth: NoneBot2's session id should already scope `got` waits
    # to the originating user in a group, but verify explicitly so a misbehaving
    # adapter or future version can't let another group member confirm for us.
    caller_user_id = matcher.state.get("sync_caller_user_id")
    if caller_user_id and event.get_user_id() != caller_user_id:
        await matcher.reject()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    text = confirm_reply.extract_plain_text().strip()
    if text != _SYNC_CONFIRM_TOKEN:
        await matcher.finish(
            at + " " + reply_failure("同步", "已取消"),
        )

    missing: list[str] = matcher.state.get("sync_missing") or []
    if not missing:
        # Defensive: should not reach here because the first step finishes early
        # when the diff is empty. If it does (e.g. session lost state), bail out.
        await matcher.finish(
            at + " " + reply_failure("同步", "缺失权限列表已失效，请重新发起命令"),
        )

    actually_added: list[str] = []
    current: set[str] = set()
    target_count = 0
    old_csv = ""
    new_csv = ""
    session = get_session()
    try:
        # SS-1.1：与 重置访客权限 confirm 对齐——条件 UPDATE + retry，
        # 避免依赖 BEGIN IMMEDIATE 全局串行化（forward-compat：未来若收窄
        # 锁范围或换 engine，模式无需重写）。
        committed = False
        for _ in range(_CSV_UPDATE_RETRY):
            guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
            if guest is None:
                await matcher.finish(
                    at + " " + reply_failure("同步", "guest 身份组不存在"),
                )

            old_csv = str(guest.permissions or "")
            current = set(split_csv_values(old_csv))
            # Re-diff against live row in case WebUI added some of the missing keys
            # between the preview and the confirmation.
            actually_added = sorted(set(missing) - current)
            if not actually_added:
                target_count = len(current)
                committed = True
                break
            new_csv = join_csv_values(current | set(actually_added))
            rowcount = execute_rowcount(
                session,
                update(Group)
                .where(
                    Group.name == _SYNC_GROUP_NAME,
                    Group.permissions == old_csv,
                )
                .values(permissions=new_csv),
            )
            if rowcount == 1:
                session.commit()
                target_count = len(current | set(actually_added))
                committed = True
                break
            session.rollback()
        if not committed:
            logger.warning(
                f"同步访客权限并发冲突重试耗尽 actor={operator_id} "
                f"target={_SYNC_GROUP_NAME} retry={_CSV_UPDATE_RETRY}"
            )
            await matcher.finish(
                at + " " + reply_failure("同步", "并发冲突，请稍后重试"),
            )
    finally:
        session.close()

    if not actually_added:
        await matcher.finish(
            at + "\n" + reply_block(
                reply_success("同步", "无需补全"),
                [
                    f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
                    f"{EMOJI_CHART} 已有权限：{target_count} 个",
                ],
            )
        )

    audit_permission_change(
        actor_user_id=operator_id,
        action="guest.permissions.sync",
        target=_SYNC_GROUP_NAME,
        before={"permissions": old_csv, "count": len(current)},
        after={"permissions": new_csv, "count": target_count},
        context={"added": actually_added},
    )
    success_lines = [
        f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
        f"{EMOJI_LOCK} 新增权限：{len(actually_added)} 个",
    ]
    success_lines.extend(f"• {key}" for key in actually_added)
    success_lines.append(f"{EMOJI_CHART} 已有权限：{target_count} 个")
    await matcher.finish(
        at + "\n" + reply_block(reply_success("同步"), success_lines),
    )


# ---------------------------------------------------------------------------
# 重置访客权限（同步的 reset 对偶；PMB-5.1）
# ---------------------------------------------------------------------------


_RESET_CONFIRM_TOKEN = "确认"


@reset_guest_perms_matcher.handle()
@command_control(
    command_key="permission.group.guest.reset",
    display_name="重置访客权限",
    permission="permission.group.guest.reset",
    description="把 guest 身份组替换为默认权限集（移除额外权限），需二次确认",
    usage="重置访客权限",
    category="权限管理",
)
@require_permission("permission.group.guest.reset")
async def handle_reset_guest_perms(
    bot: Bot, event: Event, matcher: Matcher, arg: Message = CommandArg(),
) -> None:
    args = parse_command_args_with_fallback(event, arg, "重置访客权限")
    if args:
        raise_command_usage()

    at = _at_segment(event)
    session = get_session()
    try:
        guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
        current = set(split_csv_values(guest.permissions)) if guest is not None else set()
    finally:
        session.close()

    extras = sorted(current - DEFAULT_GUEST_PERMISSIONS)  # 将被移除
    missing = sorted(DEFAULT_GUEST_PERMISSIONS - current)  # 将被新增

    if not extras and not missing:
        await matcher.finish(
            at + "\n" + reply_block(
                reply_success("重置", "已与默认一致"),
                [
                    f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
                    f"{EMOJI_CHART} 权限数：{len(current)} 个",
                ],
            )
        )

    matcher.state["reset_caller_user_id"] = event.get_user_id()
    matcher.state["reset_extras"] = extras
    matcher.state["reset_missing"] = missing
    matcher.state["reset_current_count"] = len(current)

    preview_lines = [
        f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
        f"{EMOJI_CHART} 当前权限：{len(current)} 个 → 重置后：{len(DEFAULT_GUEST_PERMISSIONS)} 个",
    ]
    if extras:
        preview_lines.append(f"{EMOJI_LOCK} 将移除 {len(extras)} 个权限：")
        preview_lines.extend(f"• {key}" for key in extras)
    if missing:
        preview_lines.append(f"{EMOJI_LOCK} 将新增 {len(missing)} 个权限：")
        preview_lines.extend(f"• {key}" for key in missing)

    await matcher.send(
        at + "\n" + reply_block(
            reply_info("重置预览"),
            preview_lines,
            hint=f"回复「{_RESET_CONFIRM_TOKEN}」执行重置，回复其他内容取消",
        )
    )


@reset_guest_perms_matcher.got("reset_confirm_reply")
async def handle_reset_guest_perms_confirm(
    bot: Bot, event: Event, matcher: Matcher,
    reset_confirm_reply: Message = Arg("reset_confirm_reply"),
) -> None:
    caller_user_id = matcher.state.get("reset_caller_user_id")
    if caller_user_id and event.get_user_id() != caller_user_id:
        await matcher.reject()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    text = reset_confirm_reply.extract_plain_text().strip()
    if text != _RESET_CONFIRM_TOKEN:
        await matcher.finish(at + " " + reply_failure("重置", "已取消"))

    # SS-5.1：preview-time 的 reset_extras / reset_missing 不再用作 audit context；
    # 改为在 confirm-time 基于 old_csv / new_csv 重新计算，避免外部并发
    # 修改导致 stale diff 与真实 before/after 不一致。
    new_csv = join_csv_values(DEFAULT_GUEST_PERMISSIONS)
    old_csv = ""
    no_op = False
    session = get_session()
    try:
        guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
        if guest is None:
            await matcher.finish(at + " " + reply_failure("重置", "guest 身份组不存在"))

        old_csv = str(guest.permissions or "")
        # O3：与其他 mutation handler 一致，使用条件 UPDATE + retry，
        # 避免依赖 BEGIN IMMEDIATE 全局串行化（forward-compat：未来若收窄
        # 锁范围或换 engine，模式无需重写）。
        if old_csv == new_csv:
            # TOCTOU：preview 之后另一路径已把 guest 同步到默认；no-op
            no_op = True
        else:
            committed = False
            for _ in range(_CSV_UPDATE_RETRY):
                rowcount = execute_rowcount(
                    session,
                    update(Group)
                    .where(
                        Group.name == _SYNC_GROUP_NAME,
                        Group.permissions == old_csv,
                    )
                    .values(permissions=new_csv),
                )
                if rowcount == 1:
                    session.commit()
                    committed = True
                    break
                session.rollback()
                current = (
                    session.query(Group)
                    .filter(Group.name == _SYNC_GROUP_NAME)
                    .first()
                )
                if current is None:
                    await matcher.finish(
                        at + " " + reply_failure("重置", "guest 身份组不存在"),
                    )
                old_csv = str(current.permissions or "")
                if old_csv == new_csv:
                    no_op = True
                    committed = True
                    break
            if not committed:
                logger.warning(
                    f"重置访客权限并发冲突重试耗尽 actor={operator_id} "
                    f"target={_SYNC_GROUP_NAME} retry={_CSV_UPDATE_RETRY}"
                )
                await matcher.finish(
                    at + " " + reply_failure("重置", "并发冲突，请稍后重试"),
                )
    finally:
        session.close()

    if no_op:
        await matcher.finish(
            at + "\n" + reply_block(
                reply_success("重置", "已与默认一致"),
                [
                    f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
                    f"{EMOJI_CHART} 权限数：{len(DEFAULT_GUEST_PERMISSIONS)} 个",
                ],
            ),
        )

    # SS-5.1：用 confirm-time 的 old_csv / new_csv 重新计算 diff，
    # 避免 preview / confirm 之间外部并发修改导致 stale extras / missing
    # 与真实 before/after 不一致。
    before_set = set(split_csv_values(old_csv))
    after_set = set(split_csv_values(new_csv))
    actual_removed = sorted(before_set - after_set)
    actual_added = sorted(after_set - before_set)
    audit_permission_change(
        actor_user_id=operator_id,
        action="guest.permissions.reset",
        target=_SYNC_GROUP_NAME,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"removed": actual_removed, "added": actual_added},
    )

    success_lines = [
        f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
        f"{EMOJI_CHART} 权限数：{len(DEFAULT_GUEST_PERMISSIONS)} 个",
    ]
    if actual_removed:
        success_lines.append(f"{EMOJI_LOCK} 已移除：{len(actual_removed)} 个")
    if actual_added:
        success_lines.append(f"{EMOJI_LOCK} 已新增：{len(actual_added)} 个")
    await matcher.finish(
        at + "\n" + reply_block(reply_success("重置"), success_lines),
    )


# Re-export for backward-compat: import sites elsewhere may use get_owner_ids
from nextbot.access_control import get_owner_ids  # noqa: E402, F401
