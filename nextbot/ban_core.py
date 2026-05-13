from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from nonebot.log import logger
from sqlalchemy import update

from nextbot.access_control import get_owner_ids
from nextbot.db import Server, User, execute_rowcount, get_session
from nextbot.server_broadcast import BroadcastOutcome, aggregate, broadcast
from nextbot.time_utils import db_now_utc_naive
from nextbot.tshock_api import (
    TShockRequestError,
    TShockResponse,
    get_error_reason,
    is_success,
    request_server_api,
)

BanDBCode = Literal["not_found", "owner_protected", "already_banned", "banned"]
UnbanDBCode = Literal["not_found", "not_banned", "unbanned"]


@dataclass
class BanDBResult:
    code: BanDBCode
    user_name: str = ""
    user_qq: str = ""
    previous_reason: str = ""


@dataclass
class UnbanDBResult:
    code: UnbanDBCode
    user_name: str = ""
    user_qq: str = ""


def apply_ban_to_db(user_id: str, reason: str) -> BanDBResult:
    session = get_session()
    try:
        # Owner 保护检查必须先于 UPDATE：owner 状态不会并发变化，read-then-check 安全。
        # 同时给后续 not_found / already_banned 复用同一条 SELECT。
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            return BanDBResult(code="not_found", user_qq=user_id)
        if str(user.user_id) in get_owner_ids():
            # SC-4.5：owner 保护拦截单独 logger.warning，便于审计
            logger.warning(
                f"封禁尝试被 owner 保护拒绝：target_user_id={user_id} target_name={user.name}"
            )
            return BanDBResult(
                code="owner_protected",
                user_name=str(user.name),
                user_qq=str(user.user_id),
            )

        target_name = str(user.name)
        target_qq = str(user.user_id)

        # SB-1.4：条件 UPDATE 防 lost-update。两个 admin 并发封禁同一目标时，
        # 第二条 rowcount=0，被 SQL 层拦下，不再互相覆盖 ban_reason / banned_at。
        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id, User.is_banned == False)  # noqa: E712
            .values(
                is_banned=True,
                banned_at=db_now_utc_naive(),
                ban_reason=reason,
            ),
        )
        session.commit()

        if rowcount == 0:
            # 重新读取以拿到当前 ban_reason（区分 already_banned）
            current = (
                session.query(User).filter(User.user_id == user_id).first()
            )
            if current is None:
                return BanDBResult(code="not_found", user_qq=user_id)
            return BanDBResult(
                code="already_banned",
                user_name=str(current.name),
                user_qq=str(current.user_id),
                previous_reason=str(current.ban_reason or ""),
            )

        return BanDBResult(code="banned", user_name=target_name, user_qq=target_qq)
    finally:
        session.close()


def apply_unban_to_db(user_id: str) -> UnbanDBResult:
    """SC-4.6：解封 DB 操作的对偶函数。

    SB-3.3：条件 UPDATE 防并发覆盖。
    SB-3.1：commit 前 capture user_name / user_qq，避免 commit 后 lazy-load 造成的 ORM 可见性差异。
    """
    session = get_session()
    try:
        # 先读一次拿 name/qq，保证 commit 后任何路径都不再依赖 ORM lazy-load
        existing = session.query(User).filter(User.user_id == user_id).first()
        if existing is None:
            return UnbanDBResult(code="not_found", user_qq=user_id)
        target_name = str(existing.name)
        target_qq = str(existing.user_id)

        rowcount = execute_rowcount(
            session,
            update(User)
            .where(User.user_id == user_id, User.is_banned == True)  # noqa: E712
            .values(is_banned=False, banned_at=None, ban_reason=""),
        )
        session.commit()

        if rowcount == 0:
            return UnbanDBResult(
                code="not_banned", user_name=target_name, user_qq=target_qq
            )
        return UnbanDBResult(
            code="unbanned", user_name=target_name, user_qq=target_qq
        )
    finally:
        session.close()


def _load_servers() -> list[Server]:
    session = get_session()
    try:
        return session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()


def _extract_blacklist_entries(check: TShockResponse) -> list[dict]:
    """P-1.13：防御 TShock 返回 payload 形态异常。

    原代码 `check.payload.get("entries", [])` 仅对单个 element 做
    isinstance(dict) 过滤，但若 entries 本身是字符串（例如 server bug
    返回 `{"entries": "string"}`），`for e in "string"` 会按字符迭代，下游
    `e.get("username", "")` 抛 AttributeError，整个 _add_one / _remove_one
    task 异常上抛至 broadcast 层。这里在 helper 内统一约束 entries 为
    list[dict] 形态，保证调用方逻辑稳定。
    """
    payload = check.payload if isinstance(check.payload, dict) else {}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


async def sync_user_to_blacklist(
    user_name: str, reason: str
) -> list[BroadcastOutcome[str]]:
    """添加用户到所有服务器黑名单。返回 per-server outcomes（按 server.id 升序）。

    payload 字段语义：
    - "added"：成功执行了 add 请求
    - "already_exists"：已在黑名单中（跳过 add）
    - None：失败
    """
    servers = _load_servers()
    if not servers:
        return []

    # SB-1.2：URL 路径段 percent-encoding，防御 user.name 含 / # ? 等字符
    encoded_name = quote(user_name, safe="")

    async def _add_one(server: Server) -> BroadcastOutcome[str]:
        try:
            check = await request_server_api(server, "/nextbot/blacklist")
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(check):
            entries = _extract_blacklist_entries(check)
            already_exists = any(
                str(e.get("username", "")).lower() == user_name.lower()
                for e in entries
            )
            if already_exists:
                return BroadcastOutcome(
                    server=server,
                    ok=True,
                    detail="已存在于黑名单中",
                    payload="already_exists",
                )

        try:
            response = await request_server_api(
                server,
                f"/nextbot/blacklist/add/{encoded_name}",
                params={"reason": reason},
            )
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            return BroadcastOutcome(
                server=server, ok=True, detail="添加成功", payload="added"
            )
        return BroadcastOutcome(
            server=server,
            ok=False,
            detail=get_error_reason(response),
            payload=None,
        )

    outcomes = await broadcast(servers, _add_one)
    success_count, total = aggregate(outcomes)
    logger.info(
        f"黑名单同步完成：user_name={user_name} success={success_count}/{total}"
    )
    return outcomes


async def sync_user_blacklist_remove(
    user_name: str,
) -> list[BroadcastOutcome[str]]:
    """从所有服务器黑名单移除用户。返回 per-server outcomes（按 server.id 升序）。

    payload 字段语义：
    - "removed"：成功执行了 remove 请求
    - "not_in_list"：不在黑名单中（跳过 remove）
    - None：失败
    """
    servers = _load_servers()
    if not servers:
        return []

    # SB-3.5：URL 路径段 percent-encoding
    encoded_name = quote(user_name, safe="")

    async def _remove_one(server: Server) -> BroadcastOutcome[str]:
        try:
            check = await request_server_api(server, "/nextbot/blacklist")
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(check):
            entries = _extract_blacklist_entries(check)
            exists = any(
                str(e.get("username", "")).lower() == user_name.lower()
                for e in entries
            )
            if not exists:
                return BroadcastOutcome(
                    server=server,
                    ok=True,
                    detail="不在黑名单中",
                    payload="not_in_list",
                )

        try:
            response = await request_server_api(
                server, f"/nextbot/blacklist/remove/{encoded_name}"
            )
        except TShockRequestError:
            return BroadcastOutcome(
                server=server, ok=False, detail="无法连接服务器", payload=None
            )

        if is_success(response):
            return BroadcastOutcome(
                server=server, ok=True, detail="移除成功", payload="removed"
            )
        return BroadcastOutcome(
            server=server,
            ok=False,
            detail=get_error_reason(response),
            payload=None,
        )

    outcomes = await broadcast(servers, _remove_one)
    success_count, total = aggregate(outcomes)
    logger.info(
        f"黑名单移除完成：user_name={user_name} success={success_count}/{total}"
    )
    return outcomes


def format_blacklist_add_lines(
    outcomes: list[BroadcastOutcome[str]],
) -> list[str]:
    """把 sync_user_to_blacklist 返回的 outcomes 渲染成消息行。"""
    if not outcomes:
        return ["🖥️ 同步服务器黑名单结果：ℹ️ 暂无服务器"]

    lines = ["🖥️ 同步服务器黑名单结果："]
    for o in outcomes:
        if o.ok and o.payload == "already_exists":
            lines.append(f"{o.server.id}.{o.server.name}：ℹ️ 已存在于黑名单中")
        elif o.ok:
            lines.append(f"{o.server.id}.{o.server.name}：✅ 添加成功")
        else:
            lines.append(
                f"{o.server.id}.{o.server.name}：❌ 添加失败，{o.detail}"
            )
    return lines


def format_blacklist_remove_lines(
    outcomes: list[BroadcastOutcome[str]],
) -> list[str]:
    """把 sync_user_blacklist_remove 返回的 outcomes 渲染成消息行。"""
    if not outcomes:
        return ["🖥️ 同步服务器黑名单结果：ℹ️ 暂无服务器"]

    lines = ["🖥️ 同步服务器黑名单结果："]
    for o in outcomes:
        if o.ok and o.payload == "not_in_list":
            lines.append(f"{o.server.id}.{o.server.name}：ℹ️ 不在黑名单中")
        elif o.ok:
            lines.append(f"{o.server.id}.{o.server.name}：✅ 移除成功")
        else:
            lines.append(
                f"{o.server.id}.{o.server.name}：❌ 移除失败，{o.detail}"
            )
    return lines
