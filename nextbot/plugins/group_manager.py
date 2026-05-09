"""身份组（Group）管理命令。

7 个 handler，全部位于"权限管理"分类。每个 mutation handler 共用以下规则：

- POLA 防自我提权：非 owner 不可授予自己未持有的权限（添加身份组权限）。
- Dangerous-key blocklist：非 owner 不可授予 ``permission.*`` / ``group.*``
  等 owner-only 危险 key。
- Permission-key registry：未注册的 key 直接拒绝并给出 difflib 建议。
- Lost-update guard：CSV 字段（``permissions`` / ``inherits``）的 read-modify-write
  改条件 UPDATE + retry，避免并发授权 / 撤销互相覆盖。
- 统一审计：所有成功 / 拒绝路径走 ``audit_permission_change()``，WARN 级
  ``actor=... action=... target=... before=... after=...`` 机器可搜。
"""
from __future__ import annotations

import re

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from nextbot.audit import audit_permission_change
from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import (
    GROUP_DELETE_FALLBACK,
    RESERVED_GROUP_NAMES,
    Group,
    User,
    execute_rowcount,
    get_session,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import (
    MAX_INHERIT_DEPTH,
    _get_effective_permissions_in_session,
    _get_group_permissions,
    _measure_inherit_depth,
    _would_create_inheritance_cycle,
    add_inherit,
    add_permission,
    has_permission,
    is_dangerous_permission,
    is_owner,
    remove_inherit,
    remove_permission,
    require_permission,
    split_csv_values,
    suggest_permission_keys,
    validate_permission_key,
)
from nextbot.text_utils import (
    EMOJI_GROUP,
    EMOJI_LOCK,
    reply_block,
    reply_failure,
    reply_info,
    reply_success,
    safe_at_segment_or_empty,
)

list_matcher = on_command("身份组列表")
add_matcher = on_command("添加身份组")
delete_matcher = on_command("删除身份组")
inherit_matcher = on_command("继承身份组")
clear_inherit_matcher = on_command("取消继承身份组")
add_perm_matcher = on_command("添加身份组权限")
remove_perm_matcher = on_command("删除身份组权限")

# PMA-2.2：身份组名仅允许英文字母 / 数字 / 下划线 / 连字符，长度 1-32。
# 防止 ``,`` / ``\n`` / NBSP 等字符进入 CSV 列后破坏 inherits / permissions
# 解析。
GROUP_NAME_PATTERN = re.compile(r"[A-Za-z0-9_\-]{1,32}")

# PMA-1.1：身份组列表分页，每页 N 组，避免 OneBot V11 单条消息超限。
GROUP_LIST_PAGE_SIZE = 10
# 单条 perm CSV 在列表中的预览截断阈值，超过用 "+N more" 展示。
GROUP_LIST_PERM_PREVIEW = 5

# PMA-6.2 / 7.1 / 4.2 lost-update 重试次数：5 次足够覆盖正常并发场景，
# 失败回 ``并发冲突`` 让 actor 重试。
_CSV_UPDATE_RETRY = 5


def _operator_id(event: Event) -> str:
    return event.get_user_id()


def _at_segment(event: Event) -> OBV11MessageSegment:
    # PC-4.1：使用 safe_at_segment_or_empty，非数字 user_id 退化为空文本段
    return safe_at_segment_or_empty(event.get_user_id())


def _format_perm_preview(csv_value: str) -> str:
    """渲染身份组列表中的 perm CSV 预览：截断到 N 个，剩余用 +M more 表示。"""
    perms = split_csv_values(csv_value)
    if not perms:
        return "无"
    if len(perms) <= GROUP_LIST_PERM_PREVIEW:
        return ",".join(perms)
    head = ",".join(perms[:GROUP_LIST_PERM_PREVIEW])
    return f"{head} (+{len(perms) - GROUP_LIST_PERM_PREVIEW} more)"


@list_matcher.handle()
@command_control(
    command_key="group.list",
    display_name="身份组列表",
    permission="group.list",
    description="显示所有身份组（分页，每页 10 组）",
    usage="身份组列表 [page]",
    category="权限管理",
)
@require_permission("group.list")
async def handle_list_groups(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "身份组列表")
    # PMA-1.1：支持可选的 page 参数（默认 1，1-based）
    if len(args) > 1:
        raise_command_usage()

    page = 1
    if args:
        try:
            page = int(args[0])
            if page < 1:
                raise ValueError
        except ValueError:
            raise_command_usage()

    session = get_session()
    try:
        groups = session.query(Group).order_by(Group.name.asc()).all()
    finally:
        session.close()

    if not groups:
        await bot.send(event, "ℹ️ 暂无身份组")
        return

    total = len(groups)
    total_pages = (total + GROUP_LIST_PAGE_SIZE - 1) // GROUP_LIST_PAGE_SIZE
    if page > total_pages:
        await bot.send(event, reply_failure("查询", f"页码超出范围（共 {total_pages} 页）"))
        return

    start = (page - 1) * GROUP_LIST_PAGE_SIZE
    end = start + GROUP_LIST_PAGE_SIZE
    page_groups = groups[start:end]

    lines: list[str] = []
    for group in page_groups:
        perm_count = len(split_csv_values(group.permissions))
        inherit_csv = group.inherits or "无"
        lines.append(group.name)
        lines.append(f"权限数：{perm_count}（{_format_perm_preview(group.permissions)}）")
        lines.append(f"继承：{inherit_csv}")
        lines.append("")

    header = f"👥 身份组列表（第 {page}/{total_pages} 页，共 {total} 组）"
    message = header + "\n" + "\n".join(lines).rstrip()
    logger.info(f"输出身份组列表：page={page}/{total_pages} total={total}")
    await bot.send(event, message)


@add_matcher.handle()
@command_control(
    command_key="group.add",
    display_name="添加身份组",
    permission="group.add",
    description="新增身份组",
    usage="添加身份组 <身份组名称>",
    category="权限管理",
)
@require_permission("group.add")
async def handle_add_group(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加身份组")
    if len(args) != 1:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    name = args[0]

    # PMA-2.1：保留组名拒绝（owner 也不应建——owner 是 .env 短路非 DB 组，
    # 创建一个名叫 owner 的组对实际权限无影响但会误导后续运维）
    if name.lower() in RESERVED_GROUP_NAMES:
        await bot.send(event, at + " " + reply_failure("添加", "身份组名称为系统保留字"))
        return

    # PMA-2.2：name regex
    if not GROUP_NAME_PATTERN.fullmatch(name):
        await bot.send(
            event,
            at + " " + reply_failure(
                "添加", "名称仅允许英文字母 / 数字 / _ -，长度 1-32"
            ),
        )
        return

    session = get_session()
    try:
        exists = session.query(Group).filter(Group.name == name).first()
        if exists is not None:
            await bot.send(event, at + " " + reply_failure("添加", "身份组已存在"))
            return

        session.add(Group(name=name, permissions="", inherits=""))
        try:
            session.commit()
        except IntegrityError:
            # PMA-2.3：并发提交同名组时 PRIMARY KEY 冲突，捕获后改回友好提示
            session.rollback()
            await bot.send(event, at + " " + reply_failure("添加", "身份组已存在"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.add",
        target=name,
        before=None,
        after={"permissions": "", "inherits": ""},
    )
    await bot.send(event, at + " " + reply_success("添加"))


# ---------------------------------------------------------------------------
# 删除身份组（两步确认 + cascade preview）
# ---------------------------------------------------------------------------


@delete_matcher.handle()
@command_control(
    command_key="group.delete",
    display_name="删除身份组",
    permission="group.delete",
    description="删除身份组（二次确认；受影响用户回退到 default）",
    usage="删除身份组 <身份组名称>",
    category="权限管理",
)
@require_permission("group.delete")
async def handle_delete_group(
    bot: Bot, event: Event, matcher: Matcher, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "删除身份组")
    if len(args) != 1:
        raise_command_usage()

    at = _at_segment(event)
    name = args[0]
    if name in {"guest", "default"}:
        await bot.send(event, at + " " + reply_failure("删除", "系统内置身份组不可删除"))
        return

    # PMA-3.6：cascade preview，必须二次确认
    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            await bot.send(event, at + " " + reply_failure("删除", "身份组不存在"))
            return

        affected_user_count = (
            session.query(User).filter(User.group == name).count()
        )
        # 粗匹配 inherits CSV 中含 name；可能误命中子串（"helper" 命中 "helper2"），
        # 在 confirm 阶段会精确再次校验。
        candidate_groups = (
            session.query(Group)
            .filter(Group.inherits.contains(name))
            .all()
        )
        affected_child_count = sum(
            1 for g in candidate_groups if name in split_csv_values(g.inherits)
        )

        old_perms = str(group.permissions or "")
        old_inherits = str(group.inherits or "")
    finally:
        session.close()

    matcher.state["delete_target_name"] = name
    matcher.state["delete_caller_user_id"] = event.get_user_id()
    matcher.state["delete_old_perms"] = old_perms
    matcher.state["delete_old_inherits"] = old_inherits
    matcher.state["delete_affected_users"] = affected_user_count
    matcher.state["delete_affected_children"] = affected_child_count

    preview_lines = [
        f"{EMOJI_GROUP} 身份组：{name}",
        f"{EMOJI_LOCK} 权限：{old_perms or '无'}",
        f"🔗 继承：{old_inherits or '无'}",
        f"👤 将影响 {affected_user_count} 个用户（回退到 {GROUP_DELETE_FALLBACK}）",
        f"🔗 将影响 {affected_child_count} 个子身份组（清理其继承）",
    ]
    await matcher.send(
        at + "\n" + reply_block(
            reply_info("删除预览"),
            preview_lines,
            hint="回复「确认」执行删除，回复其他内容取消",
        )
    )


@delete_matcher.got("delete_confirm")
async def handle_delete_group_confirm(
    bot: Bot, event: Event, matcher: Matcher,
    delete_confirm: Message = Arg("delete_confirm"),
) -> None:
    caller_user_id = matcher.state.get("delete_caller_user_id")
    if caller_user_id and event.get_user_id() != caller_user_id:
        await matcher.reject()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    text = delete_confirm.extract_plain_text().strip()
    if text != "确认":
        await matcher.finish(at + " " + reply_failure("删除", "已取消"))

    name: str = matcher.state.get("delete_target_name") or ""
    old_perms: str = matcher.state.get("delete_old_perms") or ""
    old_inherits: str = matcher.state.get("delete_old_inherits") or ""

    reassigned = 0
    updated_children = 0
    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            await matcher.finish(at + " " + reply_failure("删除", "身份组已不存在"))

        # PMA-3.1：回退到 default（不是 guest）。单语句 bulk UPDATE 原子化，
        # 不需要 retry。
        reassigned = execute_rowcount(
            session,
            update(User)
            .where(User.group == name)
            .values(group=GROUP_DELETE_FALLBACK),
        )

        # O4：cascade scrub child groups' inherits 也走条件 UPDATE，
        # 与 add_perm / remove_perm / clear_inherit 模式一致。
        # 在 BEGIN IMMEDIATE 下整个 confirm 事务串行化，retry 极少触发；
        # 但保留 retry 模板便于未来收窄锁或换 engine。
        all_groups = session.query(Group).all()
        for g in all_groups:
            child_name = str(g.name)
            for _ in range(_CSV_UPDATE_RETRY):
                fresh = (
                    session.query(Group)
                    .filter(Group.name == child_name)
                    .first()
                )
                if fresh is None:
                    # 子组在并发中被删除，跳过
                    break
                old_child_inherits = str(fresh.inherits or "")
                if name not in split_csv_values(old_child_inherits):
                    # 已无引用（最初粗匹配是子串误中，或并发已清理）
                    break
                new_child_inherits = remove_inherit(old_child_inherits, name)
                if new_child_inherits == old_child_inherits:
                    break
                rowcount = execute_rowcount(
                    session,
                    update(Group)
                    .where(
                        Group.name == child_name,
                        Group.inherits == old_child_inherits,
                    )
                    .values(inherits=new_child_inherits),
                )
                if rowcount == 1:
                    updated_children += 1
                    break
                session.rollback()
            else:
                # 重试耗尽：在 BEGIN IMMEDIATE 下几乎不可能；记录告警，
                # 不阻塞父组删除（父组本身仍是确认动作）。
                logger.warning(
                    f"删除身份组 cascade scrub 重试耗尽：parent={name} child={child_name}"
                )

        session.delete(group)
        session.commit()
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.delete",
        target=name,
        before={"permissions": old_perms, "inherits": old_inherits},
        after=None,
        context={
            "reassigned_users": reassigned,
            "fallback_group": GROUP_DELETE_FALLBACK,
            "updated_child_groups": updated_children,
        },
    )
    await matcher.finish(
        at + "\n" + reply_block(
            reply_success("删除"),
            [
                f"{EMOJI_GROUP} 身份组：{name}",
                f"👤 已将 {reassigned} 个用户回退到 {GROUP_DELETE_FALLBACK}",
                f"🔗 已清理 {updated_children} 个子身份组的继承",
            ],
        )
    )


# ---------------------------------------------------------------------------
# 继承身份组 / 取消继承身份组
# ---------------------------------------------------------------------------


@inherit_matcher.handle()
@command_control(
    command_key="group.inherit.add",
    display_name="继承身份组",
    permission="group.inherit.add",
    description="设置身份组继承关系（拒绝循环 / 拒绝超过 8 层）",
    usage="继承身份组 <身份组名称> <要继承的身份组名称>",
    category="权限管理",
)
@require_permission("group.inherit.add")
async def handle_inherit_group(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "继承身份组")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    child, parent = args
    if child == parent:
        await bot.send(event, at + " " + reply_failure("修改", "不能继承到自身"))
        return

    session = get_session()
    try:
        child_group = session.query(Group).filter(Group.name == child).first()
        parent_group = session.query(Group).filter(Group.name == parent).first()
        if child_group is None or parent_group is None:
            await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
            return

        # PMA-4.1：DFS cycle check
        if _would_create_inheritance_cycle(session, child=child, new_parent=parent):
            await bot.send(event, at + " " + reply_failure("修改", "会形成循环继承"))
            return

        # PMA-4.3：MAX_INHERIT_DEPTH 软警告
        projected_depth = (
            _measure_inherit_depth(session, parent, set()) + 1
        )
        if projected_depth > MAX_INHERIT_DEPTH:
            await bot.send(
                event,
                at + " " + reply_failure(
                    "修改", f"继承链超过 {MAX_INHERIT_DEPTH} 层"
                ),
            )
            return

        # SS-4.1：POLA 层级护栏（与 修改用户身份组 PMB-3.1 对称）。
        # 非 owner 不能让某个组继承到一个含有自己未持有权限的父组，
        # 防止通过组合 group.inherit.add + 修改用户身份组 绕过 hierarchy 护栏。
        if not is_owner(operator_id):
            operator_perms = _get_effective_permissions_in_session(session, operator_id)
            parent_perms = _get_group_permissions(session, parent, set())
            forbidden = parent_perms - operator_perms
            if forbidden:
                forbidden_preview = ",".join(sorted(forbidden)[:5])
                audit_permission_change(
                    actor_user_id=operator_id,
                    action="group.inherit.add.denied",
                    target=child,
                    before={"inherits": str(child_group.inherits or "")},
                    context={
                        "attempted_parent": parent,
                        "forbidden": sorted(forbidden),
                        "reason": "hierarchy",
                    },
                )
                await bot.send(
                    event,
                    at + " " + reply_failure(
                        "修改",
                        f"父身份组包含您不持有的权限：{forbidden_preview}",
                    ),
                )
                return

        # PMA-4.2：lost-update conditional UPDATE
        old_inherits = str(child_group.inherits or "")
        new_inherits = add_inherit(old_inherits, parent)
        if new_inherits == old_inherits:
            await bot.send(event, at + " " + reply_info("该继承关系已存在"))
            return

        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(Group)
                .where(Group.name == child, Group.inherits == old_inherits)
                .values(inherits=new_inherits),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = session.query(Group).filter(Group.name == child).first()
            if current is None:
                await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
                return
            old_inherits = str(current.inherits or "")
            new_inherits = add_inherit(old_inherits, parent)
            if new_inherits == old_inherits:
                await bot.send(event, at + " " + reply_info("该继承关系已存在"))
                return
        else:
            await bot.send(event, at + " " + reply_failure("修改", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.inherit.add",
        target=child,
        before={"inherits": old_inherits},
        after={"inherits": new_inherits},
        context={"parent": parent},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("继承"),
            [
                f"{EMOJI_GROUP} 身份组：{child}",
                f"🔗 继承：{parent}",
            ],
        ),
    )


@clear_inherit_matcher.handle()
@command_control(
    command_key="group.inherit.clear",
    display_name="取消继承身份组",
    permission="group.inherit.clear",
    description="清空身份组继承关系（系统内置组不可清）",
    usage="取消继承身份组 <身份组名称>",
    category="权限管理",
)
@require_permission("group.inherit.clear")
async def handle_clear_inherit_group(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "取消继承身份组")
    if len(args) != 1:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    name = args[0]

    # PMA-5.1：拒绝对 default / guest 操作
    if name in {"guest", "default"}:
        await bot.send(
            event,
            at + " " + reply_failure("修改", "系统内置身份组的继承关系不可清空"),
        )
        return

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
            return

        # PMA-5.2：no-op detect
        old_inherits = str(group.inherits or "")
        if not old_inherits:
            await bot.send(event, at + " " + reply_info("该身份组已无继承可清空"))
            return

        # 条件 UPDATE 防 lost update
        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(Group)
                .where(Group.name == name, Group.inherits == old_inherits)
                .values(inherits=""),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = session.query(Group).filter(Group.name == name).first()
            if current is None:
                await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
                return
            old_inherits = str(current.inherits or "")
            if not old_inherits:
                await bot.send(event, at + " " + reply_info("该身份组已无继承可清空"))
                return
        else:
            await bot.send(event, at + " " + reply_failure("修改", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.inherit.clear",
        target=name,
        before={"inherits": old_inherits},
        after={"inherits": ""},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("取消继承"),
            [
                f"{EMOJI_GROUP} 身份组：{name}",
                "🔗 已清空全部继承",
            ],
        ),
    )


# ---------------------------------------------------------------------------
# 添加 / 删除身份组权限（POLA + blocklist + lost-update）
# ---------------------------------------------------------------------------


@add_perm_matcher.handle()
@command_control(
    command_key="group.permission.add",
    display_name="添加身份组权限",
    permission="group.permission.add",
    description="为身份组添加权限（受 POLA / 危险 key blocklist 约束）",
    usage="添加身份组权限 <身份组名称> <权限名称>",
    category="权限管理",
)
@require_permission("group.permission.add")
async def handle_add_group_perm(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加身份组权限")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    name, permission = args

    # PMA-6.1：POLA + blocklist + registry validate（owner 例外）
    if not is_owner(operator_id):
        if not validate_permission_key(permission):
            suggestions = suggest_permission_keys(permission)
            hint = (
                f"。是否想说：{', '.join(suggestions)}" if suggestions else ""
            )
            await bot.send(
                event,
                at + " " + reply_failure("添加", f"权限名称不存在{hint}"),
            )
            return
        if is_dangerous_permission(permission):
            await bot.send(event, at + " " + reply_failure("添加", "该权限不可委派"))
            audit_permission_change(
                actor_user_id=operator_id,
                action="group.permission.add.denied",
                target=name,
                context={"permission": permission, "reason": "dangerous_key"},
            )
            return
        if not has_permission(operator_id, permission):
            await bot.send(
                event,
                at + " " + reply_failure("添加", "无法授予自己未持有的权限"),
            )
            audit_permission_change(
                actor_user_id=operator_id,
                action="group.permission.add.denied",
                target=name,
                context={"permission": permission, "reason": "pola"},
            )
            return

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            await bot.send(event, at + " " + reply_failure("添加", "身份组不存在"))
            return

        old_csv = str(group.permissions or "")
        new_csv = add_permission(old_csv, permission)
        if new_csv == old_csv:
            await bot.send(event, at + " " + reply_info("该权限已存在"))
            return

        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(Group)
                .where(Group.name == name, Group.permissions == old_csv)
                .values(permissions=new_csv),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = session.query(Group).filter(Group.name == name).first()
            if current is None:
                await bot.send(event, at + " " + reply_failure("添加", "身份组不存在"))
                return
            old_csv = str(current.permissions or "")
            new_csv = add_permission(old_csv, permission)
            if new_csv == old_csv:
                await bot.send(event, at + " " + reply_info("该权限已存在"))
                return
        else:
            await bot.send(event, at + " " + reply_failure("添加", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.permission.add",
        target=name,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"permission": permission},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("添加"),
            [
                f"{EMOJI_GROUP} 身份组：{name}",
                f"{EMOJI_LOCK} 权限：{permission}",
            ],
        ),
    )


@remove_perm_matcher.handle()
@command_control(
    command_key="group.permission.remove",
    display_name="删除身份组权限",
    permission="group.permission.remove",
    description="从身份组移除权限（受 POLA / 危险 key blocklist 约束）",
    usage="删除身份组权限 <身份组名称> <权限名称>",
    category="权限管理",
)
@require_permission("group.permission.remove")
async def handle_remove_group_perm(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "删除身份组权限")
    if len(args) != 2:
        raise_command_usage()

    at = _at_segment(event)
    operator_id = _operator_id(event)
    name, permission = args

    # 与 add 对偶：non-owner 不能撤销自己未持有的权限 / 危险 key
    if not is_owner(operator_id):
        if is_dangerous_permission(permission):
            await bot.send(event, at + " " + reply_failure("删除", "该权限不可委派"))
            audit_permission_change(
                actor_user_id=operator_id,
                action="group.permission.remove.denied",
                target=name,
                context={"permission": permission, "reason": "dangerous_key"},
            )
            return
        if not has_permission(operator_id, permission):
            await bot.send(
                event,
                at + " " + reply_failure("删除", "无法撤销自己未持有的权限"),
            )
            audit_permission_change(
                actor_user_id=operator_id,
                action="group.permission.remove.denied",
                target=name,
                context={"permission": permission, "reason": "pola"},
            )
            return

    session = get_session()
    try:
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            await bot.send(event, at + " " + reply_failure("删除", "身份组不存在"))
            return

        old_csv = str(group.permissions or "")
        new_csv = remove_permission(old_csv, permission)
        # PMA-7.3：no-op detect
        if new_csv == old_csv:
            await bot.send(event, at + " " + reply_info("该身份组未持有此权限"))
            return

        for _ in range(_CSV_UPDATE_RETRY):
            rowcount = execute_rowcount(
                session,
                update(Group)
                .where(Group.name == name, Group.permissions == old_csv)
                .values(permissions=new_csv),
            )
            if rowcount == 1:
                session.commit()
                break
            session.rollback()
            current = session.query(Group).filter(Group.name == name).first()
            if current is None:
                await bot.send(event, at + " " + reply_failure("删除", "身份组不存在"))
                return
            old_csv = str(current.permissions or "")
            new_csv = remove_permission(old_csv, permission)
            if new_csv == old_csv:
                await bot.send(event, at + " " + reply_info("该身份组未持有此权限"))
                return
        else:
            await bot.send(event, at + " " + reply_failure("删除", "并发冲突，请稍后重试"))
            return
    finally:
        session.close()

    audit_permission_change(
        actor_user_id=operator_id,
        action="group.permission.remove",
        target=name,
        before={"permissions": old_csv},
        after={"permissions": new_csv},
        context={"permission": permission},
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("删除"),
            [
                f"{EMOJI_GROUP} 身份组：{name}",
                f"{EMOJI_LOCK} 权限：{permission}",
            ],
        ),
    )
