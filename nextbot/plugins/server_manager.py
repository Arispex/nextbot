from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from nextbot.audit import audit_permission_change
from nextbot.command_config import command_control, raise_command_usage
from nextbot.db import Server, get_session
from nextbot.large_image import release_server_semaphores_all
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.server_validation import (
    ServerPayloadValidationError,
    validate_server_payload,
)
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import (
    EMOJI_SERVER,
    at_prefix,
    reply_block,
    reply_failure,
    reply_success,
)

add_matcher = on_command("添加服务器")
delete_matcher = on_command("删除服务器")
list_matcher = on_command("服务器列表")
test_matcher = on_command("测试连通性")

@add_matcher.handle()
@command_control(
    command_key="server.add",
    display_name="添加服务器",
    permission="server.add",
    description="新增服务器",
    usage="添加服务器 <服务器名称> <IP> <游戏端口> <RestAPI 端口> <RestAPI Token>",
    category="服务器管理",
)
@require_permission("server.add")
async def handle_add_server(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "添加服务器")
    if len(args) != 5:
        raise_command_usage()

    raw_name, raw_ip, raw_game_port, raw_restapi_port, raw_token = args

    # SM-1.3 / SM-1.5：复用 webui 的校验，确保 bot 端不再写入空字段 / 非法端口 / 含换行的脏行
    try:
        validated = validate_server_payload(
            raw_name, raw_ip, raw_game_port, raw_restapi_port, raw_token
        )
    except ServerPayloadValidationError as exc:
        logger.warning(
            f"添加服务器失败：field={exc.field or ''} reason={exc.reason}"
        )
        # R3N-5.2：与 ban / permission_manager denied 模式对齐，失败路径也走
        # 统一 audit 入口便于安全监测平台聚合（防恶意反复尝试添加冲突 ID 等）
        audit_permission_change(
            actor_user_id=event.get_user_id(),
            action="server.add.denied",
            target=str(raw_name),
            context={
                "reason": "validation_error",
                "field": exc.field or "",
                "details": exc.reason,
            },
        )
        await bot.send(event, at_prefix(event, reply_failure("添加", exc.reason)))
        return

    session = get_session()
    try:
        # SM-1.1：与 webui_servers.py:208 保持一致，避免 count() 在历史 / 并发 gap 情况下撞已有 id
        max_id = int(session.query(func.max(Server.id)).scalar() or 0)
        new_id = max_id + 1
        server = Server(
            id=new_id,
            name=validated.name,
            ip=validated.ip,
            game_port=validated.game_port,
            restapi_port=validated.restapi_port,
            token=validated.token,
        )
        session.add(server)
        try:
            session.commit()
        except IntegrityError:
            # SM-1.2 / SM-3.1：并发 add 撞 UNIQUE 时显式回滚 + 友好提示，避免抛 traceback 到 nonebot
            session.rollback()
            logger.warning(
                f"添加服务器失败：name={validated.name} reason=ID 分配冲突 attempted_id={new_id}"
            )
            # R3N-5.2：失败 audit
            audit_permission_change(
                actor_user_id=event.get_user_id(),
                action="server.add.denied",
                target=str(validated.name),
                context={
                    "reason": "integrity_error",
                    "attempted_id": new_id,
                },
            )
            await bot.send(
                event,
                at_prefix(event, reply_failure("添加", "ID 分配冲突，请重试")),
            )
            return
    finally:
        session.close()

    logger.info(
        f"添加服务器成功：server_id={new_id} name={validated.name} "
        f"ip={validated.ip} game_port={validated.game_port} "
        f"restapi_port={validated.restapi_port}"
    )
    # PC-6.1：服务器条目变更（add）涉及 token / IP / port，是基础设施级配置，
    # 走统一 audit 入口便于事故回查
    audit_permission_change(
        actor_user_id=event.get_user_id(),
        action="server.add",
        target=str(new_id),
        after={
            "name": validated.name,
            "ip": validated.ip,
            "game_port": validated.game_port,
            "restapi_port": validated.restapi_port,
        },
    )
    await bot.send(
        event,
        at_prefix(
            event,
            reply_block(
                reply_success("添加"),
                [
                    f"🆔 服务器 ID：{new_id}",
                    f"{EMOJI_SERVER} 名称：{validated.name}",
                    f"🌐 地址：{validated.ip}:{validated.game_port}",
                ],
            ),
            sep="\n",
        ),
    )


@delete_matcher.handle()
@command_control(
    command_key="server.delete",
    display_name="删除服务器",
    permission="server.delete",
    description="删除服务器",
    usage="删除服务器 <服务器 ID>",
    category="服务器管理",
)
@require_permission("server.delete")
async def handle_delete_server(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "删除服务器")
    if len(args) != 1:
        raise_command_usage()

    try:
        target_id = int(args[0])
    except ValueError:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == target_id).first()
        if server is None:
            # R3N-5.2：失败 audit（防恶意反复探测不存在 server_id）
            audit_permission_change(
                actor_user_id=event.get_user_id(),
                action="server.delete.denied",
                target=str(target_id),
                context={"reason": "not_found"},
            )
            await bot.send(event, at_prefix(event, reply_failure("删除", "服务器不存在")))
            return

        deleted_id = int(server.id)
        deleted_name = str(server.name)
        deleted_ip = str(server.ip)
        deleted_game_port = int(server.game_port)
        deleted_restapi_port = int(server.restapi_port)
        session.delete(server)
        session.flush()

        session.query(Server).filter(Server.id > deleted_id).update(
            {Server.id: Server.id - 1}, synchronize_session=False
        )
        session.commit()
    finally:
        session.close()

    # R8 M-5：删除 server 后清理所有已注册 per-server semaphore pool 中的对应 entry
    release_server_semaphores_all(deleted_id)

    logger.info(f"删除服务器成功：server_id={deleted_id}")
    # PC-6.1：服务器删除是基础设施级变更，走统一 audit 入口
    audit_permission_change(
        actor_user_id=event.get_user_id(),
        action="server.delete",
        target=str(deleted_id),
        before={
            "name": deleted_name,
            "ip": deleted_ip,
            "game_port": deleted_game_port,
            "restapi_port": deleted_restapi_port,
        },
    )
    await bot.send(
        event,
        at_prefix(
            event,
            reply_block(
                reply_success("删除"),
                [
                    f"🆔 服务器 ID：{deleted_id}",
                    f"{EMOJI_SERVER} 名称：{deleted_name}",
                ],
            ),
            sep="\n",
        ),
    )


@list_matcher.handle()
@command_control(
    command_key="server.list",
    display_name="服务器列表",
    permission="server.list",
    description="输出服务器列表",
    usage="服务器列表",
    category="服务器管理",
)
@require_permission("server.list")
async def handle_list_servers(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "服务器列表")
    if args:
        raise_command_usage()

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        await bot.send(event, "ℹ️ 暂无服务器")
        return

    lines: list[str] = []
    for server in servers:
        lines.append(f"{server.id}.{server.name}")
        lines.append(f"IP：{server.ip}")
        lines.append(f"端口：{server.game_port}")
        lines.append("")

    message = "🖥️ 服务器列表\n" + "\n".join(lines).rstrip()
    logger.info(f"输出服务器列表，共 {len(servers)} 条")
    await bot.send(event, message)


@test_matcher.handle()
@command_control(
    command_key="server.test",
    display_name="测试连通性",
    permission="server.test",
    description="测试服务器 REST API 连通性",
    usage="测试连通性 <服务器 ID>",
    category="服务器管理",
)
@require_permission("server.test")
async def handle_test_server(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "测试连通性")
    if len(args) != 1:
        raise_command_usage()

    try:
        target_id = int(args[0])
    except ValueError:
        raise_command_usage()

    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == target_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, at_prefix(event, reply_failure("测试", "服务器不存在")))
        return

    try:
        response = await request_server_api(server, "/tokentest")
    except TShockRequestError:
        logger.info(
            f"测试连通性失败：server_id={target_id} ip={server.ip} port={server.restapi_port}"
        )
        await bot.send(event, at_prefix(event, reply_failure("测试", "无法连接服务器")))
        return

    status_code = response.http_status
    status_value = response.api_status
    logger.info(
        f"测试连通性完成：server_id={target_id} http={status_code} status={status_value}"
    )

    if is_success(response):
        await bot.send(event, at_prefix(event, reply_success("测试", "一切正常")))
        return

    reason = get_error_reason(response)
    await bot.send(event, at_prefix(event, reply_failure("测试", reason)))
