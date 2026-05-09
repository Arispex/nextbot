"""用户权限 / 身份组管理命令。

5 个命令 + 2 个新增命令位于"权限管理"分类：

- ``添加用户权限`` / ``删除用户权限`` / ``修改用户身份组``：受 POLA / 危险
  key blocklist / 层级护栏（target group ⊆ operator perms）约束
- ``管理员列表``：并行获取昵称（asyncio.gather + wait_for），截图统一走
  ``render_and_send_screenshot`` helper（内置 base64 size cap + per-handler
  semaphore + V11 / 非 V11 分支）
- ``同步访客权限``：保留两步确认（已正确）
- ``重置访客权限``（新增）：reset 到 DEFAULT_GUEST_PERMISSIONS（清掉额外
  权限），二次确认 + 列出将移除 / 新增的 key
"""
from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from sqlalchemy import func, update

from nextbot.access_control import get_owner_ids_ordered
from nextbot.audit import audit_permission_change
from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import (
    DEFAULT_GUEST_PERMISSIONS,
    Group,
    User,
    execute_rowcount,
    get_session,
)
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import (
    _get_effective_permissions_in_session,
    _get_group_permissions,
    add_permission,
    has_permission,
    is_dangerous_permission,
    is_owner,
    join_csv_values,
    remove_permission,
    require_permission,
    split_csv_values,
    suggest_permission_keys,
    validate_permission_key,
)
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_GROUP,
    EMOJI_LOCK,
    EMOJI_USER,
    reply_block,
    reply_failure,
    reply_info,
    reply_success,
)
from server.screenshot import ScreenshotOptions
from server.web_server import create_admin_list_page

add_user_perm_matcher = on_command("添加用户权限")
remove_user_perm_matcher = on_command("删除用户权限")
set_user_group_matcher = on_command("修改用户身份组")
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

# 重试次数（与 group_manager 保持一致）
_CSV_UPDATE_RETRY = 5


def _operator_id(event: Event) -> str:
    return event.get_user_id()


def _at_segment(event: Event) -> OBV11MessageSegment:
    return OBV11MessageSegment.at(int(event.get_user_id()))


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


def _check_user_perm_mutation_pola(
    *,
    operator_id: str,
    target_user_id: str,
    permission: str,
    action_label: str,
    audit_action_denied: str,
    is_grant: bool,
) -> tuple[bool, str | None]:
    """共用的 add / remove user-perm POLA 校验。

    Args:
        is_grant: True 表示授权（添加），False 表示撤销（删除）。
            自授禁止仅在授权路径生效（PMB-1.1）；自撤是无害的。

    返回 ``(ok, failure_message)``。owner 调用方短路放行。
    """
    if is_owner(operator_id):
        return True, None
    # PMB-1.1：仅禁止自授；自撤是无害的（用户随时可主动放弃自己的权限）
    if is_grant and target_user_id == operator_id:
        return False, reply_failure(action_label, "不能为自己添加权限")
    # PMB-1.3：registry validate（仅 grant 路径校验；
    # remove 路径需要支持清理 legacy / typo 历史 key，绕过 registry 即可）
    if is_grant and not validate_permission_key(permission):
        suggestions = suggest_permission_keys(permission)
        hint = f"。是否想说：{', '.join(suggestions)}" if suggestions else ""
        return False, reply_failure(action_label, f"权限名称不存在{hint}")
    # PMB-1.1 dangerous-key blocklist
    if is_dangerous_permission(permission):
        audit_permission_change(
            actor_user_id=operator_id,
            action=audit_action_denied,
            target=target_user_id,
            context={"permission": permission, "reason": "dangerous_key"},
        )
        return False, reply_failure(action_label, "该权限不可委派")
    # PMB-1.1 POLA：actor 自身需先持有该权限
    if not has_permission(operator_id, permission):
        audit_permission_change(
            actor_user_id=operator_id,
            action=audit_action_denied,
            target=target_user_id,
            context={"permission": permission, "reason": "pola"},
        )
        verb = "授予" if is_grant else "撤销"
        return False, reply_failure(action_label, f"无法{verb}自己未持有的权限")
    return True, None


# ---------------------------------------------------------------------------
# 添加用户权限
# ---------------------------------------------------------------------------


@add_user_perm_matcher.handle()
@command_control(
    command_key="permission.user.add",
    display_name="添加用户权限",
    permission="permission.user.add",
    description="为用户增加单独权限（受 POLA / 危险 key blocklist / 禁止自授约束）",
    usage="添加用户权限 <用户 QQ/@用户/用户名称> <权限名称>",
    category="权限管理",
)
@require_permission("permission.user.add")
async def handle_add_user_perm(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    # PMB-1.6：缓存 args，避免双 parse
    args = parse_command_args_with_fallback(event, arg, "添加用户权限")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "添加用户权限",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("添加", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("添加", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if user_id is None:
        await bot.send(event, at + " " + reply_failure("添加", "用户参数解析失败"))
        return

    # PMB-1.5：args[1] 是 whitespace-split 单 token，多 token 已在 len(args)==2
    # 校验中拒绝。文档化以便后续若放宽 token 数限制能注意到。
    permission = args[1]

    ok, failure_msg = _check_user_perm_mutation_pola(
        operator_id=operator_id,
        target_user_id=user_id,
        permission=permission,
        action_label="添加",
        audit_action_denied="user.permission.add.denied",
        is_grant=True,
    )
    if not ok:
        await bot.send(event, at + " " + (failure_msg or ""))
        return

    session = get_session()
    target_name = ""
    old_csv = ""
    new_csv = ""
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
            return

        target_name = str(user.name)
        old_csv = str(user.permissions or "")
        new_csv = add_permission(old_csv, permission)
        if new_csv == old_csv:
            await bot.send(event, at + " " + reply_info("该用户已持有此权限"))
            return

        # PMB-1.2 / XC-3：条件 UPDATE 防 lost-update + ORM 跨列覆盖
        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(User.user_id == user_id, User.permissions == old_csv)
                .values(permissions=new_csv),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = (
                session.query(User).filter(User.user_id == user_id).first()
            )
            if current is None:
                await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
                return
            old_csv = str(current.permissions or "")
            new_csv = add_permission(old_csv, permission)
            if new_csv == old_csv:
                await bot.send(event, at + " " + reply_info("该用户已持有此权限"))
                return
            target_name = str(current.name)
        else:
            await bot.send(event, at + " " + reply_failure("添加", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="user.permission.add",
        target=user_id,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"permission": permission, "target_name": target_name},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("添加"),
            [
                f"{EMOJI_USER} 用户：{target_name}（{user_id}）",
                f"{EMOJI_LOCK} 权限：{permission}",
            ],
        ),
    )


# ---------------------------------------------------------------------------
# 删除用户权限
# ---------------------------------------------------------------------------


@remove_user_perm_matcher.handle()
@command_control(
    command_key="permission.user.remove",
    display_name="删除用户权限",
    permission="permission.user.remove",
    description="从用户移除单独权限（受 POLA / 危险 key blocklist 约束）",
    usage="删除用户权限 <用户 QQ/@用户/用户名称> <权限名称>",
    category="权限管理",
)
@require_permission("permission.user.remove")
async def handle_remove_user_perm(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "删除用户权限")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "删除用户权限",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("删除", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("删除", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if user_id is None:
        await bot.send(event, at + " " + reply_failure("删除", "用户参数解析失败"))
        return

    permission = args[1]

    ok, failure_msg = _check_user_perm_mutation_pola(
        operator_id=operator_id,
        target_user_id=user_id,
        permission=permission,
        action_label="删除",
        audit_action_denied="user.permission.remove.denied",
        is_grant=False,
    )
    if not ok:
        await bot.send(event, at + " " + (failure_msg or ""))
        return

    session = get_session()
    target_name = ""
    old_csv = ""
    new_csv = ""
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("删除", "用户不存在"))
            return

        target_name = str(user.name)
        old_csv = str(user.permissions or "")
        new_csv = remove_permission(old_csv, permission)
        # PMB-2.3：no-op detect
        if new_csv == old_csv:
            # PMB-2.5：检查是否来自身份组继承（用同一 session 避免 BEGIN
            # IMMEDIATE 死锁）。这里只做精确匹配；通配 .* 不在此提示——
            # 通配本来就不能"单独删除"，提示统一为"权限来自身份组继承"
            # 即可。
            group_name = str(user.group or "guest")
            group_perms = _get_group_permissions(session, group_name, set())
            inherited = permission in group_perms or any(
                granted.endswith(".*") and permission.startswith(granted[:-1])
                for granted in group_perms
            )
            if inherited:
                await bot.send(
                    event,
                    at + " " + reply_info("权限来自身份组继承，不可单独删除"),
                )
            else:
                await bot.send(
                    event,
                    at + " " + reply_info("该用户未持有此权限"),
                )
            return

        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(User.user_id == user_id, User.permissions == old_csv)
                .values(permissions=new_csv),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = (
                session.query(User).filter(User.user_id == user_id).first()
            )
            if current is None:
                await bot.send(event, at + " " + reply_failure("删除", "用户不存在"))
                return
            old_csv = str(current.permissions or "")
            new_csv = remove_permission(old_csv, permission)
            if new_csv == old_csv:
                await bot.send(event, at + " " + reply_info("该用户未持有此权限"))
                return
            target_name = str(current.name)
        else:
            await bot.send(event, at + " " + reply_failure("删除", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="user.permission.remove",
        target=user_id,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"permission": permission, "target_name": target_name},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("删除"),
            [
                f"{EMOJI_USER} 用户：{target_name}（{user_id}）",
                f"{EMOJI_LOCK} 权限：{permission}",
            ],
        ),
    )


# ---------------------------------------------------------------------------
# 修改用户身份组（POLA 层级护栏 + 条件 UPDATE）
# ---------------------------------------------------------------------------


@set_user_group_matcher.handle()
@command_control(
    command_key="permission.user.group.set",
    display_name="修改用户身份组",
    permission="permission.user.group.set",
    description="调整用户所属身份组（目标组权限须 ⊆ operator 权限）",
    usage="修改用户身份组 <用户 QQ/@用户/用户名称> <身份组名称>",
    category="权限管理",
)
@require_permission("permission.user.group.set")
async def handle_set_user_group(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "修改用户身份组")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "修改用户身份组",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("修改", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("修改", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("修改", "用户参数解析失败"))
        return

    group_name_input = args[1]
    session = get_session()
    target_name = ""
    before_group = ""
    canonical_group_name = ""
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("修改", "用户不存在"))
            return

        # PMB-3.5：case-insensitive group name lookup
        group = (
            session.query(Group)
            .filter(func.lower(Group.name) == group_name_input.lower())
            .first()
        )
        if group is None:
            await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
            return
        canonical_group_name = str(group.name)

        # PMB-3.1：层级护栏（owner 例外）
        if not is_owner(operator_id):
            # 复用 session：BEGIN IMMEDIATE 下嵌套 get_session() 会死锁
            operator_perms = _get_effective_permissions_in_session(
                session, operator_id
            )
            target_group_perms = _get_group_permissions(
                session, canonical_group_name, set()
            )
            forbidden = target_group_perms - operator_perms
            if forbidden:
                forbidden_preview = ",".join(sorted(forbidden)[:5])
                await bot.send(
                    event,
                    at + " " + reply_failure(
                        "修改",
                        f"目标身份组包含您不持有的权限：{forbidden_preview}",
                    ),
                )
                # O6：denied audit 补 before 快照（target 当前所在组）
                audit_permission_change(
                    actor_user_id=operator_id,
                    action="user.group.set.denied",
                    target=target_user_id,
                    before={"group": str(user.group)},
                    context={
                        "attempted_group": canonical_group_name,
                        "forbidden": sorted(forbidden),
                        "reason": "hierarchy",
                    },
                )
                return

        target_name = str(user.name)
        before_group = str(user.group)
        # PMB-3.4：no-op detect
        if before_group == canonical_group_name:
            await bot.send(
                event,
                at + " " + reply_info("该用户已在此身份组"),
            )
            return

        # PMB-3.2 / XC-3：条件 UPDATE，仅写 group 列避免 ORM 跨列覆盖
        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(
                    User.user_id == target_user_id,
                    User.group == before_group,
                )
                .values(group=canonical_group_name),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = (
                session.query(User).filter(User.user_id == target_user_id).first()
            )
            if current is None:
                await bot.send(event, at + " " + reply_failure("修改", "用户不存在"))
                return
            before_group = str(current.group)
            if before_group == canonical_group_name:
                await bot.send(
                    event,
                    at + " " + reply_info("该用户已在此身份组"),
                )
                return
            target_name = str(current.name)
        else:
            await bot.send(
                event,
                at + " " + reply_failure("修改", "并发冲突，请稍后重试"),
            )
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="user.group.set",
        target=target_user_id,
        before={"group": before_group},
        after={"group": canonical_group_name},
        context={"target_name": target_name},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("修改"),
            [
                f"{EMOJI_USER} 用户：{target_name}（{target_user_id}）",
                f"{EMOJI_GROUP} 身份组：{canonical_group_name}",
            ],
        ),
    )


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
    session = get_session()
    try:
        guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
        if guest is None:
            await matcher.finish(
                at + " " + reply_failure("同步", "guest 身份组不存在"),
            )

        current = set(split_csv_values(guest.permissions))
        # Re-diff against live row in case WebUI added some of the missing keys
        # between the preview and the confirmation.
        actually_added = sorted(set(missing) - current)
        if actually_added:
            guest.permissions = join_csv_values(current | set(actually_added))
            session.commit()
        target_count = len(current | set(actually_added))
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
        before={"count": len(current)},
        after={"count": target_count},
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

    extras: list[str] = matcher.state.get("reset_extras") or []
    missing: list[str] = matcher.state.get("reset_missing") or []

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

    audit_permission_change(
        actor_user_id=operator_id,
        action="guest.permissions.reset",
        target=_SYNC_GROUP_NAME,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"removed": extras, "added": missing},
    )

    success_lines = [
        f"{EMOJI_GROUP} 身份组：{_SYNC_GROUP_NAME}",
        f"{EMOJI_CHART} 权限数：{len(DEFAULT_GUEST_PERMISSIONS)} 个",
    ]
    if extras:
        success_lines.append(f"{EMOJI_LOCK} 已移除：{len(extras)} 个")
    if missing:
        success_lines.append(f"{EMOJI_LOCK} 已新增：{len(missing)} 个")
    await matcher.finish(
        at + "\n" + reply_block(reply_success("重置"), success_lines),
    )


# Re-export for backward-compat: import sites elsewhere may use get_owner_ids
from nextbot.access_control import get_owner_ids  # noqa: E402, F401
