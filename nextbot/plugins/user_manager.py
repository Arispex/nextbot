import asyncio
import re
import secrets
import string
from typing import Any, Literal
from urllib.parse import quote

import bcrypt
import nonebot
from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nextbot.audit import audit_permission_change
from nextbot.command_config import command_control, raise_command_usage
from nextbot.message_parser import (
    parse_command_args_with_fallback,
    resolve_user_id_arg_with_fallback,
)
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.server_broadcast import BroadcastOutcome, broadcast
from nextbot.time_utils import format_beijing_datetime
from server.screenshot import ScreenshotOptions
from server.web_server import create_user_info_page

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nextbot.db import Server, User, UserSignRecord, execute_rowcount, get_session
from nextbot.tshock_api import (
    TShockRequestError,
    get_error_reason,
    is_success,
    request_server_api,
)
from nextbot.text_utils import (
    EMOJI_USER,
    STATUS_HINT,
    reply_block,
    reply_failure,
    reply_success,
    reply_warning,
    safe_at_segment_or_empty,
)


USER_INFO_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=600,
    full_page=True,
    fit_content_height=True,
)

# 用户信息渲染共享并发上限，防止刷命令撑爆 Playwright
_user_info_screenshot_semaphore = asyncio.Semaphore(2)

add_matcher = on_command("注册账号")
sync_matcher = on_command("同步白名单")
info_matcher = on_command("用户信息")
self_info_matcher = on_command("我的信息")
rename_matcher = on_command("更改用户名称")
MAX_USER_NAME_LENGTH = 16

SyncStatus = Literal["new", "exists", "fail"]


def _validate_user_name(name: str) -> str | None:
    value = name.strip()
    if not value:
        return "用户名称不能为空"
    if len(value) > MAX_USER_NAME_LENGTH:
        return f"用户名称过长，最多 {MAX_USER_NAME_LENGTH} 个字符"
    if value.isdigit():
        return "用户名称不能为纯数字"
    if not re.fullmatch(r"[A-Za-z0-9一-鿿]+", value):
        return "用户名称不能包含符号，只能使用中文、英文和数字"
    return None


# ---- 随机密码 / BCrypt / TShock 账号创建 / 临时私聊 helpers ----
#
# 设计要点：
# - 密码 16 位 `[A-Za-z0-9]`：避免 URL query param 转义问题；62^16 ≈ 4.7e28 强度足够
# - BCrypt cost=7 prefix=2a：与 TShock 100% 互操作（TShock 用 cost 7 / $2a$ 格式）
# - 明文密码仅在 in-process 短暂存活（生成 → hash + push + 私聊一次），不写文件 / 不入 log

_PASSWORD_ALPHABET: str = string.ascii_letters + string.digits
_PASSWORD_LENGTH: int = 16
_BCRYPT_COST: int = 7


def _generate_random_password() -> str:
    """生成 16 位 `[A-Za-z0-9]` 随机密码（密码学安全 RNG）。"""
    return "".join(
        secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH)
    )


def _hash_password(plaintext: str) -> str:
    """BCrypt cost=7 / prefix=2a，与 TShock 互操作。输出 60 字符 `$2a$07$...`。"""
    return bcrypt.hashpw(
        plaintext.encode("utf-8"),
        bcrypt.gensalt(rounds=_BCRYPT_COST, prefix=b"2a"),
    ).decode("ascii")


def _mask_user_id(user_id: str) -> str:
    """QQ 中间打码（首 2 + *** + 尾 2），与 webui_users._mask_qq 对齐避免 PII 落日志。"""
    text = str(user_id or "")
    if len(text) < 4:
        return text
    return text[:2] + "***" + text[-2:]


async def _create_tshock_user_on_server(
    server: Server, name: str, plaintext: str
) -> BroadcastOutcome[str]:
    """在单个 TShock server 上调 `/v2/users/create` 创建账号。

    httpx 自动对 params 做 URL 编码，无需手动 quote。
    """
    try:
        response = await request_server_api(
            server,
            "/v2/users/create",
            params={"user": name, "group": "default", "password": plaintext},
        )
    except TShockRequestError as exc:
        return BroadcastOutcome(
            server=server, ok=False, detail=str(exc) or "无法连接服务器", payload=None
        )

    if is_success(response):
        return BroadcastOutcome(
            server=server, ok=True, detail="", payload=""
        )

    reason = get_error_reason(response)
    return BroadcastOutcome(
        server=server, ok=False, detail=reason, payload=None
    )


async def _create_tshock_user_on_all_servers(
    name: str, plaintext: str
) -> list[BroadcastOutcome[str]]:
    """向所有 server 广播 `/v2/users/create`，失败仅 log，不抛。

    与 `_sync_whitelist_to_all_servers` 对齐：失败用户视角不可见，仅 console log。
    """
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        return []

    outcomes = await broadcast(
        servers,
        lambda srv: _create_tshock_user_on_server(srv, name, plaintext),
    )
    for outcome in outcomes:
        if outcome.ok:
            logger.info(
                f"TShock 账号创建成功：server_id={outcome.server.id} name={name} result=ok"
            )
        else:
            logger.warning(
                f"TShock 账号创建失败：server_id={outcome.server.id} name={name} "
                f"result=failed reason={outcome.detail}"
            )
    return outcomes


async def _send_temp_private_password(
    bot: Bot, user_id: str, name: str, password: str
) -> bool:
    """通过 OneBot 临时会话私聊把账号密码推给用户。

    群成员临时会话无需加好友（共享群即可）。失败仅 log warn 不抛、不 log 密码本身。
    """
    masked = _mask_user_id(user_id)
    message = (
        "✅ 注册成功，请妥善保存以下服务器登入账号信息：\n"
        f"👤 用户名：{name}\n"
        f"🔑 密码：{password}\n"
        f"🎮 在服务器内输入「/login {password}」登入（如果服务器已开启自动登入可忽略）\n"
        "ℹ️ 密码仅推送一次，丢失请使用「修改密码」命令重置"
    )
    try:
        await bot.call_api(
            "send_private_msg",
            user_id=int(user_id),
            message=message,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"临时私聊密码发送失败：user_id={masked} name={name} reason={exc!r}"
        )
        return False
    logger.info(
        f"临时私聊密码已发送：user_id={masked} name={name} 临时会话=success"
    )
    return True


def _migrate_legacy_users_password_hash() -> None:
    """启动时一次性把 `password_hash IS NULL` 的旧用户 backfill 一个随机 hash。

    仅写 bot DB，不调任何 server API、不私聊。设计理由：旧用户可能已在各 server
    手动注册过 TShock 账号，机器人不该用随机密码 overwrite。本步只把 DB schema
    状态对齐（NULL → 有 hash 占位），实际密码协调留给未来的「修改密码」命令。

    幂等：再次启动时已 backfill 的用户 hash 不为 NULL，循环为空。
    迁移失败（hash 写不进 DB）→ 跳过该用户，下次启动重试（仍 NULL）。
    """
    session = get_session()
    total = 0
    success_hash = 0
    try:
        legacy_users = (
            session.query(User).filter(User.password_hash.is_(None)).all()
        )
        total = len(legacy_users)
        if total == 0:
            logger.info("旧用户密码迁移：total=0 跳过（无 NULL hash 用户）")
            return

        for user in legacy_users:
            try:
                plaintext = _generate_random_password()
                user.password_hash = _hash_password(plaintext)
                # 立即丢弃明文引用；不 log、不返回、不持久化
                del plaintext
                success_hash += 1
                logger.info(
                    f"旧用户密码迁移：user_id={_mask_user_id(str(user.user_id))} "
                    f"name={user.name} hash_set=true"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"旧用户密码迁移失败：user_id={_mask_user_id(str(user.user_id))} "
                    f"name={user.name} reason={exc!r}（下次启动重试）"
                )
        # 批量 commit 一次（即使部分用户 hash 失败，成功的也要落盘）
        try:
            session.commit()
        except Exception as commit_exc:  # noqa: BLE001
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("旧用户密码迁移 rollback 自身抛错")
            logger.warning(
                f"旧用户密码迁移 commit 失败：reason={commit_exc!r}（下次启动重试）"
            )
            success_hash = 0
    finally:
        session.close()

    logger.info(
        f"旧用户密码迁移完成：total={total} success_hash={success_hash}"
    )


@nonebot.get_driver().on_startup
async def _run_legacy_users_password_hash_migration() -> None:
    """启动 hook：触发一次旧用户 password_hash backfill。

    注：必须在 db.init_db()（bot.py 的 `_init_database` startup hook）完成 schema
    迁移之后执行，否则 password_hash 列尚不存在会 SELECT 失败。
    NoneBot 按注册顺序触发 startup hooks，bot.py 在 import 阶段先注册
    `_init_database`，本 plugin 后被 `load_plugins` 加载注册本 hook，顺序保证正确。
    """
    try:
        _migrate_legacy_users_password_hash()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"旧用户密码迁移异常（启动 hook 兜底）：reason={exc!r}（下次启动重试）"
        )


async def _sync_one_whitelist(
    server: Server, user_id: str, name: str
) -> tuple[Server, SyncStatus, str]:
    # 先查询白名单，判断用户名是否已存在
    try:
        wl_response = await request_server_api(server, "/nextbot/whitelist")
    except TShockRequestError:
        logger.info(
            f"白名单同步失败：server_id={server.id} user_id={user_id} name={name} reason=无法连接服务器"
        )
        return server, "fail", "无法连接服务器"

    if not is_success(wl_response):
        reason = get_error_reason(wl_response)
        logger.info(
            f"白名单查询失败：server_id={server.id} user_id={user_id} name={name} "
            f"http_status={wl_response.http_status} api_status={wl_response.api_status} reason={reason}"
        )
        return server, "fail", reason

    existing_users = wl_response.payload.get("users", [])
    if name in existing_users:
        logger.info(
            f"白名单已存在：server_id={server.id} user_id={user_id} name={name}"
        )
        return server, "exists", ""

    # 添加白名单
    # PC-3.1：URL path segment quote(safe="")，与 ban_core / player_query 加固对齐
    encoded_name = quote(name, safe="")
    try:
        response = await request_server_api(
            server,
            f"/nextbot/whitelist/add/{encoded_name}",
        )
    except TShockRequestError:
        logger.info(
            f"白名单同步失败：server_id={server.id} user_id={user_id} name={name} reason=无法连接服务器"
        )
        return server, "fail", "无法连接服务器"

    if is_success(response):
        return server, "new", ""

    reason = get_error_reason(response)
    logger.info(
        "白名单同步失败："
        f"server_id={server.id} user_id={user_id} name={name} "
        f"http_status={response.http_status} api_status={response.api_status} reason={reason}"
    )
    return server, "fail", reason


async def _sync_whitelist_to_all_servers(
    user_id: str, name: str
) -> list[tuple[Server, SyncStatus, str]]:
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    if not servers:
        return []

    # R4R-B.1：return_exceptions=True 防止任一 task 抛非 TShockRequestError 异常
    # （如 CancelledError、内部 bug）时整个 gather cancel 其他任务，
    # 与 shop / lottery 的 fan-out 模板对齐。
    raw_results = await asyncio.gather(
        *(_sync_one_whitelist(server, user_id, name) for server in servers),
        return_exceptions=True,
    )
    results: list[tuple[Server, SyncStatus, str]] = []
    for server, raw in zip(servers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            logger.warning(
                f"白名单同步异常：server_id={server.id} user_id={user_id} name={name} reason={raw!r}"
            )
            results.append((server, "fail", "同步异常"))
        else:
            results.append(raw)
    return results


async def _rename_one_whitelist(
    server: Server, old_name: str, new_name: str
) -> tuple[Server, bool, bool, str, str]:
    """对单个服务器执行白名单 remove(old) + add(new)。

    返回 (server, remove_ok, add_ok, remove_msg, add_msg)。
    """
    remove_ok = False
    add_ok = False
    remove_msg = ""
    add_msg = ""

    # 删除旧白名单
    # PC-3.1：URL path segment quote(safe="")
    encoded_old_name = quote(old_name, safe="")
    try:
        response = await request_server_api(
            server, f"/nextbot/whitelist/remove/{encoded_old_name}",
        )
        remove_ok = is_success(response)
        if not remove_ok:
            remove_msg = get_error_reason(response)
    except TShockRequestError:
        remove_msg = "无法连接服务器"

    # 添加新白名单
    # PC-3.1：URL path segment quote(safe="")
    encoded_new_name = quote(new_name, safe="")
    try:
        response = await request_server_api(
            server, f"/nextbot/whitelist/add/{encoded_new_name}",
        )
        add_ok = is_success(response)
        if not add_ok:
            add_msg = get_error_reason(response)
    except TShockRequestError:
        add_msg = "无法连接服务器"

    return server, remove_ok, add_ok, remove_msg, add_msg


@add_matcher.handle()
@command_control(
    command_key="user.register",
    display_name="注册账号",
    permission="user.register",
    description="注册当前 QQ 对应的账号",
    usage="注册账号 <用户名称>",
    category="用户系统",
)
@require_permission("user.register")
async def handle_add_whitelist(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "注册账号")
    if len(args) != 1:
        raise_command_usage()

    user_id = event.get_user_id()
    at = safe_at_segment_or_empty(user_id)
    name = args[0].strip()
    invalid_reason = _validate_user_name(name)
    if invalid_reason is not None:
        await bot.send(event, at + " " + reply_failure("注册", f"{invalid_reason}"))
        return

    # 在 DB 事务外预生成 password / hash，让事务持锁时间最短（_force_immediate_begin
    # 让所有写串行；hash 计算 cost=7 大约 ~10ms，放进事务里会无谓拖长持锁）。
    plaintext_password = _generate_random_password()
    try:
        password_hash = _hash_password(plaintext_password)
    except Exception as exc:  # noqa: BLE001
        # bcrypt 极少抛错；即便抛了也不该把"注册成功"假象给用户 → 直接失败
        logger.warning(
            f"注册账号 hash 计算失败：user_id={_mask_user_id(user_id)} name={name} reason={exc!r}"
        )
        await bot.send(event, at + " " + reply_failure("注册", "内部错误，请稍后重试"))
        return

    session = get_session()
    try:
        exists = session.query(User).filter(User.user_id == user_id).first()
        if exists is not None:
            logger.info(f"账号已注册：user_id={user_id} name={exists.name}")
            await bot.send(event, at + " " + reply_warning("你已经注册过了，请勿重复注册"))
            return
        name_exists = session.query(User).filter(func.lower(User.name) == name.lower()).first()
        if name_exists is not None:
            logger.info(f"用户名称已存在：name={name}")
            await bot.send(event, at + " " + reply_failure("注册", "用户名称已被占用"))
            return

        user = User(
            user_id=user_id,
            name=name,
            group="default",
            password_hash=password_hash,
        )
        try:
            session.add(user)
            session.commit()
        except IntegrityError:
            session.rollback()
            logger.info(f"注册并发竞态：name={name} 已被另一并发请求占用")
            await bot.send(event, at + " " + reply_failure("注册", "用户名称已被占用"))
            return
    finally:
        session.close()

    # 并行：白名单 push（旧逻辑保留）+ TShock 账号创建 push（新逻辑）。
    # 任一失败仅 console log，不影响用户视角的"注册成功"。
    try:
        await asyncio.gather(
            _sync_whitelist_to_all_servers(user_id, name),
            _create_tshock_user_on_all_servers(name, plaintext_password),
            return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"注册账号广播异常：user_id={_mask_user_id(user_id)} name={name} reason={exc!r}"
        )

    # 临时私聊把明文密码推给用户；失败仅 log，不暴露给用户
    try:
        await _send_temp_private_password(bot, user_id, name, plaintext_password)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"临时私聊密码推送异常：user_id={_mask_user_id(user_id)} name={name} reason={exc!r}"
        )

    # Defense-in-depth：明文密码已 hash 入库 + push + 私聊完成，立即释放栈上引用，
    # 防止后续 bot.send 异常被任何 capture-locals 的日志/采样工具一并落盘。
    plaintext_password = None

    logger.info(f"注册账号成功：user_id={user_id} name={name}")
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("注册"),
            [
                f"{EMOJI_USER} 用户名称：{name}",
                f"🆔 QQ：{user_id}",
                "🔑 密码已通过私聊发送，请查收并妥善保存",
                f"{STATUS_HINT} 如果进入服务器提示不在白名单中，群里发送「同步白名单」即可",
            ],
        ),
    )


@sync_matcher.handle()
@command_control(
    command_key="user.whitelist.sync",
    display_name="同步白名单",
    permission="user.whitelist.sync",
    description="将当前用户同步到所有服务器白名单",
    usage="同步白名单",
    category="用户系统",
)
@require_permission("user.whitelist.sync")
async def handle_sync_whitelist(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    args = parse_command_args_with_fallback(event, arg, "同步白名单")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    at = safe_at_segment_or_empty(user_id)
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if user is None:
        await bot.send(event, at + " " + reply_failure("同步", "未注册账号"))
        return

    results = await _sync_whitelist_to_all_servers(user_id, user.name)
    if not results:
        await bot.send(event, at + " " + reply_failure("同步", "暂无可同步的服务器"))
        return

    lines: list[str] = []
    for server, status, reason in results:
        if status == "exists":
            lines.append(f"{server.id}.{server.name}：ℹ️ 已在白名单中")
        elif status == "new":
            lines.append(f"{server.id}.{server.name}：✅ 同步成功")
        else:
            lines.append(f"{server.id}.{server.name}：❌ 同步失败，{reason}")

    logger.info(
        f"同步白名单完成：user_id={user_id} name={user.name} server_count={len(results)}"
    )
    await bot.send(event, at + "\n" + reply_success("同步白名单") + "\n" + "\n".join(lines))


def _get_sign_dates(session: Session, user_id: str, days: int) -> list[str]:
    records = (
        session.query(UserSignRecord)
        .filter(UserSignRecord.user_id == user_id)
        .order_by(UserSignRecord.sign_date.desc())
        .limit(days)
        .all()
    )
    return [r.sign_date for r in records]


async def _render_and_send_user_info(
    bot: Bot,
    event: Event,
    *,
    user_data: dict[str, Any],
    sign_dates: list[str],
    days: int,
) -> None:
    page_url = create_user_info_page(
        user_id=user_data["user_id"],
        user_name=user_data["user_name"],
        coins=user_data["coins"],
        sign_streak=user_data["sign_streak"],
        sign_total=user_data["sign_total"],
        permissions=user_data["permissions"],
        group=user_data["group"],
        created_at=user_data["created_at"],
        sign_dates=sign_dates,
        days=days,
    )
    logger.info(
        f"用户信息渲染地址：user_id={user_data['user_id']} name={user_data['user_name']} "
        f"days={days} sign_dates_count={len(sign_dates)} internal_url={page_url}"
    )
    await render_and_send_screenshot(
        bot,
        event,
        page_url=page_url,
        options=USER_INFO_SCREENSHOT_OPTIONS,
        file_prefix=f"user-info-{user_data['user_id']}",
        semaphore=_user_info_screenshot_semaphore,
        failure_action="查询",
        at_user_id=event.get_user_id(),
    )


def _serialize_user_for_render(user: User) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "user_name": user.name,
        "coins": int(user.coins or 0),
        "sign_streak": int(user.sign_streak or 0),
        "sign_total": int(user.sign_total or 0),
        "permissions": str(user.permissions or ""),
        "group": str(user.group or ""),
        "created_at": format_beijing_datetime(user.created_at),
    }


@info_matcher.handle()
@command_control(
    command_key="user.info.user",
    display_name="用户信息",
    permission="user.info.user",
    description="查询指定用户信息并生成截图",
    usage="用户信息 <用户 QQ/@用户/用户名称>",
    category="用户系统",
)
@require_permission("user.info.user")
async def handle_user_info(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    at = safe_at_segment_or_empty(event.get_user_id())
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
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不存在"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户"))
        return
    if target_user_id is None:
        await bot.send(event, at + " " + reply_failure("查询", "用户参数解析失败"))
        return

    days = 365
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("查询", "用户不存在"))
            return
        user_data = _serialize_user_for_render(user)
        sign_dates = _get_sign_dates(session, str(user.user_id), days)
    finally:
        session.close()

    await _render_and_send_user_info(
        bot, event, user_data=user_data, sign_dates=sign_dates, days=days,
    )


@self_info_matcher.handle()
@command_control(
    command_key="user.info.self",
    display_name="我的信息",
    permission="user.info.self",
    description="查询当前用户信息并生成截图",
    usage="我的信息",
    category="用户系统",
)
@require_permission("user.info.self")
async def handle_self_info(
    bot: Bot, event: Event, arg: Message = CommandArg()
):
    at = safe_at_segment_or_empty(event.get_user_id())
    args = parse_command_args_with_fallback(event, arg, "我的信息")
    if args:
        raise_command_usage()

    user_id = event.get_user_id()
    days = 365
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("查询", "未注册账号"))
            return
        user_data = _serialize_user_for_render(user)
        sign_dates = _get_sign_dates(session, str(user.user_id), days)
    finally:
        session.close()

    await _render_and_send_user_info(
        bot, event, user_data=user_data, sign_dates=sign_dates, days=days,
    )


@rename_matcher.handle()
@command_control(
    command_key="admin.rename",
    display_name="更改用户名称",
    permission="admin.rename",
    description="更改指定用户的用户名称",
    usage="更改用户名称 <用户 QQ/@用户/用户名称> <新用户名称>",
    category="用户系统",
)
@require_permission("admin.rename")
async def handle_rename(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    at = safe_at_segment_or_empty(event.get_user_id())

    target_user_id, parse_error = resolve_user_id_arg_with_fallback(
        event, arg, "更改用户名称", arg_index=0,
    )
    if parse_error == "missing":
        raise_command_usage()
    if parse_error == "name_not_found":
        await bot.send(event, at + " " + reply_failure("更改", "未找到该用户"))
        return
    if parse_error == "name_ambiguous":
        await bot.send(event, at + " " + reply_failure("更改", "用户名存在重复，请使用 QQ 或 @用户"))
        return
    if parse_error:
        raise_command_usage()

    args = parse_command_args_with_fallback(event, arg, "更改用户名称")
    if len(args) != 2:
        raise_command_usage()

    new_name = args[1].strip()
    invalid_reason = _validate_user_name(new_name)
    if invalid_reason is not None:
        await bot.send(event, at + " " + reply_failure("更改", f"{invalid_reason}"))
        return

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == target_user_id).first()
        if user is None:
            await bot.send(event, at + " " + reply_failure("更改", "未找到该用户"))
            return

        old_name = str(user.name)
        if old_name.lower() == new_name.lower():
            await bot.send(event, at + " " + reply_failure("更改", "新用户名与当前相同"))
            return

        name_exists = session.query(User).filter(
            func.lower(User.name) == new_name.lower(),
            User.user_id != target_user_id,
        ).first()
        if name_exists is not None:
            await bot.send(event, at + " " + reply_failure("更改", "用户名称已被占用"))
            return

        # PC-1.1：条件 UPDATE 取代 ORM dirty-set，便于在 WHERE 中带 old_name 校验，
        # 与项目内其它 mutation 路径风格统一；UNIQUE 撞库仍由 IntegrityError 兜底。
        try:
            rowcount = execute_rowcount(
                session,
                update(User)
                .where(
                    User.user_id == target_user_id,
                    User.name == old_name,
                )
                .values(name=new_name),
            )
        except IntegrityError:
            session.rollback()
            logger.info(
                f"更改用户名称并发竞态：user_id={target_user_id} new_name={new_name} 已被另一并发请求占用"
            )
            await bot.send(event, at + " " + reply_failure("更改", "用户名称已被占用"))
            return
        if rowcount == 0:
            session.rollback()
            logger.info(
                f"更改用户名称并发竞态：user_id={target_user_id} old_name={old_name} 已被另一并发请求修改"
            )
            await bot.send(event, at + " " + reply_failure("更改", "并发冲突，请重试"))
            return
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            logger.info(
                f"更改用户名称并发竞态：user_id={target_user_id} new_name={new_name} 已被另一并发请求占用"
            )
            await bot.send(event, at + " " + reply_failure("更改", "用户名称已被占用"))
            return
    finally:
        session.close()

    operator_id = event.get_user_id()
    logger.info(
        f"更改用户名称成功：user_id={target_user_id} old_name={old_name} new_name={new_name}"
    )
    # SH-9.1：rename 走 audit_permission_change，便于按 actor / target 追溯
    audit_permission_change(
        actor_user_id=operator_id,
        action="user.rename",
        target=str(target_user_id),
        before={"name": old_name},
        after={"name": new_name},
    )

    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()

    lines: list[str] = [
        reply_success("更改"),
        f"{EMOJI_USER} 用户 QQ：{target_user_id}",
        f"📝 旧名称：{old_name}",
        f"📝 新名称：{new_name}",
    ]
    if not servers:
        lines.append("🖥️ 同步服务器白名单结果：ℹ️ 暂无服务器")
    else:
        lines.append("🖥️ 同步服务器白名单结果：")
        # R4R-B.1：return_exceptions=True 防止任一 task 抛非 TShockRequestError
        # 异常（如 CancelledError、内部 bug）时整个 gather cancel 其他任务。
        raw_rename_results = await asyncio.gather(
            *(_rename_one_whitelist(s, old_name, new_name) for s in servers),
            return_exceptions=True,
        )
        for server, raw in zip(servers, raw_rename_results, strict=True):
            if isinstance(raw, BaseException):
                logger.warning(
                    f"更改用户名称白名单同步异常：server_id={server.id} "
                    f"old_name={old_name} new_name={new_name} reason={raw!r}"
                )
                lines.append(f"{server.id}.{server.name}：❌ 同步异常")
                continue
            _, remove_ok, add_ok, remove_msg, add_msg = raw
            if remove_ok and add_ok:
                lines.append(f"{server.id}.{server.name}：✅ 同步成功")
            else:
                details = []
                details.append(
                    f"移除旧白名单 {'✅ 成功' if remove_ok else '❌ 失败，' + remove_msg}"
                )
                details.append(
                    f"添加新白名单 {'✅ 成功' if add_ok else '❌ 失败，' + add_msg}"
                )
                lines.append(f"{server.id}.{server.name}：{'；'.join(details)}")

        logger.info(
            f"更改用户名称白名单同步完成：user_id={target_user_id} old_name={old_name} new_name={new_name} server_count={len(servers)}"
        )
    await bot.send(event, at + "\n" + "\n".join(lines))
