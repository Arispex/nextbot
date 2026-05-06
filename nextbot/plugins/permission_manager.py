import base64

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import Arg, CommandArg

from nextbot.access_control import get_owner_ids, get_owner_ids_ordered
from nextbot.command_config import command_control, get_current_param, raise_command_usage
from nextbot.db import DEFAULT_GUEST_PERMISSIONS, Group, User, get_session
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import (
    add_permission,
    join_csv_values,
    remove_permission,
    require_permission,
    split_csv_values,
)
from nextbot.screenshot_temp import temp_screenshot_path
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
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_admin_list_page

add_user_perm_matcher = on_command("添加用户权限")
remove_user_perm_matcher = on_command("删除用户权限")
set_user_group_matcher = on_command("修改用户身份组")
admin_list_matcher = on_command("管理员列表")
sync_guest_perms_matcher = on_command("同步访客权限")

ADMIN_LIST_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=400,
    full_page=True,
    fit_content_height=True,
)


async def _fetch_nickname_via_bot(bot: Bot, qq: str) -> str:
    """通过 OneBot V11 get_stranger_info 获取昵称，编码由 NapCat 处理。"""
    try:
        info = await bot.call_api("get_stranger_info", user_id=int(qq))
        return str(info.get("nickname", "")).strip()
    except Exception as exc:
        logger.info(f"get_stranger_info 失败：qq={qq} reason={exc}")
        return ""
@add_user_perm_matcher.handle()
@command_control(
    command_key="permission.user.add",
    display_name="添加用户权限",
    permission="permission.user.add",
    description="为用户增加单独权限",
    usage="添加用户权限 <用户 QQ/@用户/用户名称> <权限名称>",
    category="权限管理",
)
@require_permission("permission.user.add")
async def handle_add_user_perm(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加用户权限")
    if len(args) != 2:
        raise_command_usage()

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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

    permission = args[1]
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
            return

        user.permissions = add_permission(user.permissions, permission)
        target_name = str(user.name)
        session.commit()
    finally:
        session.close()

    logger.info(f"添加用户权限成功：user_id={user_id} permission={permission}")
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


@remove_user_perm_matcher.handle()
@command_control(
    command_key="permission.user.remove",
    display_name="删除用户权限",
    permission="permission.user.remove",
    description="从用户移除单独权限",
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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("删除", "用户不存在"))
            return

        user.permissions = remove_permission(user.permissions, permission)
        target_name = str(user.name)
        session.commit()
    finally:
        session.close()

    logger.info(f"删除用户权限成功：user_id={user_id} permission={permission}")
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


@set_user_group_matcher.handle()
@command_control(
    command_key="permission.user.group.set",
    display_name="修改用户身份组",
    permission="permission.user.group.set",
    description="调整用户所属身份组",
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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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

    group_name = args[1]
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("修改", "用户不存在"))
            return

        group = session.query(Group).filter(Group.name == group_name).first()
        if group is None:
            await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
            return

        user.group = group_name
        target_name = str(user.name)
        session.commit()
    finally:
        session.close()

    logger.info(
        f"修改用户身份组成功：user_id={target_user_id} group={group_name}"
    )
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("修改"),
            [
                f"{EMOJI_USER} 用户：{target_name}（{target_user_id}）",
                f"{EMOJI_GROUP} 身份组：{group_name}",
            ],
        ),
    )


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
    owner_ids = get_owner_ids_ordered() if keep_order else sorted(get_owner_ids())
    if not owner_ids:
        await bot.send(event, reply_failure("查询", "未配置管理员（owner_id）"))
        return

    logger.info(f"管理员列表查询：owner_count={len(owner_ids)}")

    admins: list[dict[str, str]] = []
    for qq in owner_ids:
        nickname = await _fetch_nickname_via_bot(bot, qq)
        admins.append({"user_id": qq, "nickname": nickname})
        logger.info(f"管理员昵称获取：qq={qq} nickname={nickname!r}")

    page_url = create_admin_list_page(admins=admins)
    logger.info(f"管理员列表渲染地址：admin_count={len(admins)} internal_url={page_url}")

    async with temp_screenshot_path("admin-list") as screenshot_path:
        try:
            await screenshot_url(page_url, screenshot_path, options=ADMIN_LIST_SCREENSHOT_OPTIONS)
        except RenderScreenshotError as exc:
            await bot.send(event, reply_failure("查询", f"{exc}"))
            return

        logger.info(f"管理员列表截图成功：file={screenshot_path}")
        if bot.adapter.get_name() == "OneBot V11":
            try:
                raw = screenshot_path.read_bytes()
                image_uri = f"base64://{base64.b64encode(raw).decode('ascii')}"
            except OSError:
                await bot.send(event, reply_failure("查询", "读取截图文件失败"))
                return
            await bot.send(event, OBV11MessageSegment.image(file=image_uri))
            return
        await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")


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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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

    at = OBV11MessageSegment.at(int(event.get_user_id()))
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

    logger.info(
        f"同步访客权限成功：group={_SYNC_GROUP_NAME} added={actually_added} "
        f"target_count={target_count}"
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
