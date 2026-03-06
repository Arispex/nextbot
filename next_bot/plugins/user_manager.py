import re

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from next_bot.command_config import command_control, raise_command_usage
from next_bot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from next_bot.permissions import require_permission

from next_bot.db import Server, User, get_session
from next_bot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)

add_matcher = on_command("注册账号")
sync_matcher = on_command("同步白名单")
info_matcher = on_command("用户信息")
self_info_matcher = on_command("我的信息")
add_coins_matcher = on_command("添加金币")
remove_coins_matcher = on_command("扣除金币")
MAX_USER_NAME_LENGTH = 16


def _validate_user_name(name: str) -> str | None:
    value = name.strip()
    if not value:
        return "用户名称不能为空"
    if len(value) > MAX_USER_NAME_LENGTH:
        return f"用户名称过长，最多 {MAX_USER_NAME_LENGTH} 个字符"
    if value.isdigit():
        return "用户名称不能为纯数字"
    if not re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff]+", value):
        return "用户名称不能包含符号，只能使用中文、英文和数字"
    return None


def _parse_positive_int(text: str) -> int | None:
    value = text.strip()
    if not value or not value.isdigit():
        return None

    amount = int(value)
    if amount <= 0:
        return None
    return amount


async def _sync_whitelist_to_all_servers(
    user_id: str, name: str
) -> list[tuple[Server, bool, str]]:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    results: list[tuple[Server, bool, str]] = []
    for server in servers:
        try:
            response = await request_server_api(
                server,
                "/v3/server/rawcmd",
                params={"cmd": f"/bwl add {name}"},
            )
        except TShockRequestError:
            logger.info(
                f"白名单同步失败：server_id={server.id} user_id={user_id} name={name} reason=无法连接服务器"
            )
            results.append((server, False, "无法连接服务器"))
            continue

        if is_success(response):
            results.append((server, True, ""))
            continue

        reason = get_error_reason(response)
        logger.info(
            "白名单同步失败："
            f"server_id={server.id} user_id={user_id} name={name} "
            f"http_status={response.http_status} api_status={response.api_status} reason={reason}"
        )
        results.append((server, False, reason))
    return results


@add_matcher.handle()
@command_control(
    command_key="user.register",
    display_name="注册账号",
    permission="user.register",
    description="注册当前 QQ 对应的账号",
    usage="注册账号 <用户名称>",
)
@require_permission("user.register")
async def handle_add_whitelist(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "注册账号")
    if len(args) != 1:
        raise_command_usage()

    name = args[0].strip()
    invalid_reason = _validate_user_name(name)
    if invalid_reason is not None:
        await bot.send(event, f"注册失败，{invalid_reason}")
        return

    user_id = event.get_user_id()

    session = get_session()
    try:
        exists = session.query(User).filter(User.user_id == user_id).first()
        if exists is not None:
            logger.info(f"账号已注册：user_id={user_id} name={exists.name}")
            await bot.send(event, "注册失败，该账号已注册")
            return
        name_exists = session.query(User).filter(User.name == name).first()
        if name_exists is not None:
            logger.info(f"用户名称已存在：name={name}")
            await bot.send(event, "注册失败，用户名称已被占用")
            return

        user = User(user_id=user_id, name=name, group="default")
        session.add(user)
        session.commit()
    finally:
        session.close()

    await _sync_whitelist_to_all_servers(user_id, name)

    logger.info(f"注册账号成功：user_id={user_id} name={name}")
    await bot.send(event, "注册成功")


@sync_matcher.handle()
@command_control(
    command_key="user.whitelist.sync",
    display_name="同步白名单",
    permission="user.whitelist.sync",
    description="将当前用户同步到所有服务器白名单",
    usage="同步白名单",
)
@require_permission("user.whitelist.sync")
async def handle_sync_whitelist(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "同步白名单")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if user is None:
        await bot.send(event, "同步失败，未注册账号")
        return

    results = await _sync_whitelist_to_all_servers(user_id, user.name)
    if not results:
        await bot.send(event, "同步失败，暂无可同步的服务器")
        return

    lines: list[str] = []
    for server, success, reason in results:
        if success:
            lines.append(f"{server.id}.{server.name}：同步成功")
        else:
            lines.append(f"{server.id}.{server.name}：同步失败，{reason}")

    logger.info(
        f"同步白名单完成：user_id={user_id} name={user.name} server_count={len(results)}"
    )
    await bot.send(event, "\n".join(lines))


@info_matcher.handle()
@command_control(
    command_key="user.info.user",
    display_name="用户信息",
    permission="user.info.user",
    description="查询指定用户信息",
    usage="用户信息 <用户 ID/@用户/用户名称>",
)
@require_permission("user.info.user")
async def handle_user_info(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "用户信息")
    if len(args) != 1:
        raise_command_usage()

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "用户信息",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, "查询失败，用户名称不存在")
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, "查询失败，用户名称不唯一，请使用用户 ID 或 @用户")
        return
    if target_user_id is None:
        await bot.send(event, "查询失败，用户参数解析失败")
        return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
    finally:
        session.close()

    if user is None:
        await bot.send(event, "查询失败，用户不存在")
        return

    created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    message = "\n".join(
        [
            f"用户 ID：{user.user_id}",
            f"用户名称：{user.name}",
            f"金币：{user.coins}",
            f"权限：{user.permissions or '无'}",
            f"身份组：{user.group}",
            f"创建时间：{created_at}",
        ]
    )
    await bot.send(event, message)


@self_info_matcher.handle()
@command_control(
    command_key="user.info.self",
    display_name="我的信息",
    permission="user.info.self",
    description="查询当前用户信息",
    usage="我的信息",
)
@require_permission("user.info.self")
async def handle_self_info(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "我的信息")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if user is None:
        await bot.send(event, "查询失败，未注册账号")
        return

    created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
    message = "\n".join(
        [
            f"用户 ID：{user.user_id}",
            f"用户名称：{user.name}",
            f"金币：{user.coins}",
            f"权限：{user.permissions or '无'}",
            f"身份组：{user.group}",
            f"创建时间：{created_at}",
        ]
    )
    await bot.send(event, message)


@add_coins_matcher.handle()
@command_control(
    command_key="user.coins.add",
    display_name="添加金币",
    permission="user.coins.add",
    description="为指定用户增加金币",
    usage="添加金币 <用户 ID/@用户/用户名称> <数量>",
)
@require_permission("user.coins.add")
async def handle_add_coins(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加金币")
    if len(args) != 2:
        raise_command_usage()

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "添加金币",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, "添加失败，用户名称不存在")
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, "添加失败，用户名称不唯一，请使用用户 ID 或 @用户")
        return
    if target_user_id is None:
        await bot.send(event, "添加失败，用户参数解析失败")
        return

    amount = _parse_positive_int(args[1])
    if amount is None:
        await bot.send(event, "添加失败，数量必须为正整数")
        return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, "添加失败，用户不存在")
            return

        user.coins += amount
        session.commit()
        coins = user.coins
        user_name = user.name
    finally:
        session.close()

    logger.info(
        f"添加金币成功：user_id={target_user_id} name={user_name} amount={amount} coins={coins}"
    )
    await bot.send(event, f"添加成功，{user_name} 当前金币：{coins}")


@remove_coins_matcher.handle()
@command_control(
    command_key="user.coins.remove",
    display_name="扣除金币",
    permission="user.coins.remove",
    description="为指定用户扣减金币",
    usage="扣除金币 <用户 ID/@用户/用户名称> <数量>",
)
@require_permission("user.coins.remove")
async def handle_remove_coins(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "扣除金币")
    if len(args) != 2:
        raise_command_usage()

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event,
        arg,
        "扣除金币",
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, "扣除失败，用户名称不存在")
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, "扣除失败，用户名称不唯一，请使用用户 ID 或 @用户")
        return
    if target_user_id is None:
        await bot.send(event, "扣除失败，用户参数解析失败")
        return

    amount = _parse_positive_int(args[1])
    if amount is None:
        await bot.send(event, "扣除失败，数量必须为正整数")
        return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, "扣除失败，用户不存在")
            return

        if user.coins < amount:
            await bot.send(event, f"扣除失败，金币不足，当前仅有 {user.coins}")
            return

        user.coins -= amount
        session.commit()
        coins = user.coins
        user_name = user.name
    finally:
        session.close()

    logger.info(
        f"扣除金币成功：user_id={target_user_id} name={user_name} amount={amount} coins={coins}"
    )
    await bot.send(event, f"扣除成功，{user_name} 当前金币：{coins}")
