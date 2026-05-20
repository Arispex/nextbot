from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nonebot.log import logger
from sqlalchemy import update

from nextbot.access_control import get_owner_ids
from nextbot.db import User, execute_rowcount, get_session
from nextbot.time_utils import db_now_utc_naive

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

