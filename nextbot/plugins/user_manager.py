import asyncio
import re
import secrets
import string
from typing import Any

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
from nextbot.sync_orchestrator import (
    format_sync_outcomes_for_user,
    trigger_sync_all_servers,
)
from nextbot.time_utils import format_beijing_datetime
from server.screenshot import ScreenshotOptions
from server.web_server import create_user_info_page

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nextbot.db import User, UserSignRecord, execute_rowcount, get_session
from nextbot.text_utils import (
    EMOJI_USER,
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
info_matcher = on_command("用户信息")
self_info_matcher = on_command("我的信息")
rename_matcher = on_command("更改用户名称")
change_password_matcher = on_command("修改密码")
MAX_USER_NAME_LENGTH = 16
MIN_PASSWORD_LENGTH = 8


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


async def _send_temp_private_password(
    bot: Bot, user_id: str, name: str, password: str, group_id: int | None = None
) -> bool:
    """通过 OneBot 群临时会话推送密码；无 group_id 时回落到好友私聊。

    带 group_id 时：OneBot 走"群临时会话"通道，非好友也能收到（共享群即可），
    已是好友会优先走好友通道，体验无差。
    不带 group_id 时：只走好友私聊，非好友会失败。

    失败仅 log warn 不抛、不 log 密码本身。

    ⚠️ 部署警告：本函数构造的 message 字符串含明文密码。NoneBot 在 LOG_LEVEL=DEBUG 时
    会通过 OneBot adapter 将 outgoing call_api payload 写入日志。生产环境必须保持
    LOG_LEVEL >= INFO；切勿在生产开启 DEBUG，否则明文密码会泄漏到 bot 日志文件。
    """
    masked = _mask_user_id(user_id)
    channel = "group_temp" if group_id is not None else "friend"
    message = (
        "✅ 注册成功，请妥善保存以下服务器登入账号信息：\n"
        f"👤 用户名：{name}\n"
        f"🔑 密码：{password}\n"
        f"🎮 在服务器内输入「/login {password}」登入（如果服务器已开启自动登入可忽略）\n"
        "ℹ️ 密码仅推送一次，丢失请使用「修改密码」命令重置"
    )
    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "message": message,
    }
    if group_id is not None:
        payload["group_id"] = int(group_id)
    try:
        await bot.call_api("send_private_msg", **payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"临时私聊密码发送失败：user_id={masked} name={name} 通道={channel} reason={exc!r}"
        )
        return False
    logger.info(
        f"临时私聊密码已发送：user_id={masked} name={name} 通道={channel}"
    )
    return True


def _migrate_legacy_users_password_hash() -> None:
    """旧用户 password_hash backfill —— 现已 NO-OP。

    设计修订（F-5 audit findings）：
    - 旧用户 / WebUI 创建用户保持 password_hash=NULL
    - sync API 输出 NULL → C# 端跳过该用户的 TShock 账号同步
    - 用户必须通过「修改密码」命令设置真实密码

    此函数保留作为占位 + 兼容 startup hook，不再做实际写入。仅 count + log
    （运维可见多少用户尚未设密码）。
    """
    session = get_session()
    try:
        null_count = (
            session.query(User)
            .filter(User.password_hash.is_(None))
            .count()
        )
        logger.info(
            f"旧用户密码迁移：跳过（设计修订后保持 NULL）。null_hash_count={null_count}"
        )
    finally:
        session.close()


@nonebot.get_driver().on_startup
async def _run_legacy_users_password_hash_migration() -> None:
    """启动 hook：触发一次旧用户 password_hash backfill。

    注：必须在 db.init_db()（bot.py 的 `_init_database` startup hook）完成 schema
    迁移之后执行，否则 password_hash 列尚不存在会 SELECT 失败。
    NoneBot 按注册顺序触发 startup hooks，bot.py 在 import 阶段先注册
    `_init_database`，本 plugin 后被 `load_plugins` 加载注册本 hook，顺序保证正确。

    F-4 fail-fast：先 PRAGMA 校验 password_hash 列存在；若 schema migration 失败，
    raise 让 bot 启动彻底停下，避免在 schema 损坏状态下继续运行。
    """
    try:
        session = get_session()
        try:
            from sqlalchemy import text as sa_text
            rows = session.execute(sa_text('PRAGMA table_info("user")')).fetchall()
            columns = {row[1] for row in rows}
            if "password_hash" not in columns:
                raise RuntimeError(
                    "user.password_hash 列缺失 —— "
                    "ensure_user_password_hash_schema migration 可能失败。请检查启动日志。"
                )
        finally:
            session.close()

        _migrate_legacy_users_password_hash()
    except Exception as exc:
        logger.exception(f"旧用户密码迁移启动失败：reason={exc}")
        raise  # F-4: fail-fast，不允许 bot 在 schema 损坏状态下继续运行


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

    # DB 已写入（含 password_hash），统一走 sync orchestrator 让插件端 pull 主库快照
    # 并 apply 白名单 + TShock 账号差异。orchestrator 永不抛异常，per-server 异常会
    # 转成 ok=False outcome，用户可见文案由 format_sync_outcomes_for_user 统一渲染。
    sync_outcomes = await trigger_sync_all_servers(caller="register")

    # 临时私聊把明文密码推给用户；失败如实回执告知用户走「修改密码」重置
    private_sent = False
    try:
        private_sent = await _send_temp_private_password(
            bot, user_id, name, plaintext_password,
            group_id=getattr(event, "group_id", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"临时私聊密码推送异常：user_id={_mask_user_id(user_id)} name={name} reason={exc!r}"
        )
        private_sent = False

    # Defense-in-depth：明文密码已 hash 入库 + push + 私聊完成，立即释放栈上引用，
    # 防止后续 bot.send 异常被任何 capture-locals 的日志/采样工具一并落盘。
    plaintext_password = None

    password_hint = (
        "🔑 密码已通过私聊发送，请查收并妥善保存"
        if private_sent
        else "⚠️ 密码私聊发送失败（可能临时会话被屏蔽），请使用「修改密码」命令重置"
    )

    logger.info(f"注册账号成功：user_id={user_id} name={name}")
    sync_text = format_sync_outcomes_for_user(sync_outcomes)
    await bot.send(
        event,
        at + "\n" + reply_block(
            reply_success("注册"),
            [
                f"{EMOJI_USER} 用户名称：{name}",
                f"🆔 QQ：{user_id}",
                password_hint,
                sync_text,
            ],
        ),
    )


@change_password_matcher.handle()
@command_control(
    command_key="user.password.change",
    display_name="修改密码",
    permission="user.password.change",
    description="修改当前账号密码（仅私聊可用）",
    usage="修改密码 <新密码>",
    category="用户系统",
)
@require_permission("user.password.change")
async def handle_change_password(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    user_id = event.get_user_id()

    # R2 私聊门面：仅 message_type=="private" 放行（含好友 / 群临时会话）。
    # 用 getattr 防御性提取，避免在非 OneBot v11 适配器环境下硬依赖。
    message_type = str(getattr(event, "message_type", "")).strip()
    if message_type != "private":
        await bot.send(event, reply_failure("修改", "请私聊机器人使用此命令"))
        return

    # R3 参数 / 密码强度校验
    args = parse_command_args_with_fallback(event, arg, "修改密码")
    if len(args) != 1:
        raise_command_usage()

    plaintext = args[0].strip()
    if not plaintext:
        await bot.send(event, reply_failure("修改", "密码不能为空"))
        return
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        await bot.send(
            event, reply_failure("修改", f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
        )
        return

    # 与 handle_add_whitelist 风格一致：DB 事务外先 hash，缩短事务持锁时间。
    try:
        password_hash = _hash_password(plaintext)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"修改密码 hash 计算失败：user_id={_mask_user_id(user_id)} reason={exc!r}"
        )
        await bot.send(event, reply_failure("修改", "内部错误，请稍后重试"))
        return

    # R4 注册校验 + R5 写 DB
    name: str | None = None
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            await bot.send(event, reply_failure("修改", "请先注册账号"))
            return
        name = str(user.name)
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id)
            .values(password_hash=password_hash),
        )
        if rowcount == 0:
            session.rollback()
            masked = _mask_user_id(user_id)
            logger.info(
                f"修改密码并发竞态：user_id={masked} 已被另一并发请求修改"
            )
            await bot.send(event, reply_failure("修改", "并发冲突，请重试"))
            return
        session.commit()
    finally:
        session.close()

    # Defense-in-depth：明文已 hash 入库，立即释放栈引用，避免后续 await
    # 异常被任何 capture-locals 的日志/采样工具一并落盘。
    plaintext = None

    logger.info(
        f"修改密码成功：user_id={_mask_user_id(user_id)} name={name}"
    )

    # R5 后段：DB 已写入新 password_hash，统一走 sync orchestrator 推到所有服务器。
    sync_outcomes = await trigger_sync_all_servers(caller="change_password_command")
    sync_text = format_sync_outcomes_for_user(sync_outcomes)

    await bot.send(
        event,
        reply_block(reply_success("修改"), [sync_text]),
    )


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

    # DB 已写入新 name，统一走 sync orchestrator 让插件端 apply 白名单 rename。
    sync_outcomes = await trigger_sync_all_servers(caller="rename")

    lines: list[str] = [
        reply_success("更改"),
        f"{EMOJI_USER} 用户 QQ：{target_user_id}",
        f"📝 旧名称：{old_name}",
        f"📝 新名称：{new_name}",
        format_sync_outcomes_for_user(sync_outcomes),
    ]
    await bot.send(event, at + "\n" + "\n".join(lines))
