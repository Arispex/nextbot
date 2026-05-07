from __future__ import annotations

from nonebot import get_bots
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert

from nextbot.db import (
    CommandConfig,
    Group,
    Server,
    SystemStat,
    User,
    STAT_COMMAND_EXECUTE_TOTAL,
    get_engine,
    get_session,
)
from nextbot.time_utils import (
    beijing_now_text,
    beijing_today_text,
    db_now_utc_naive,
    format_beijing_datetime,
)


def increment_stat(stat_key: str, delta: int = 1) -> None:
    key = str(stat_key).strip()
    if not key:
        return

    amount = int(delta)
    if amount == 0:
        return

    now = db_now_utc_naive()
    engine = get_engine()

    with engine.begin() as connection:
        statement = insert(SystemStat).values(
            stat_key=key,
            stat_value=amount,
            updated_at=now,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[SystemStat.stat_key],
            set_={
                "stat_value": SystemStat.stat_value + amount,
                "updated_at": now,
            },
        )
        connection.execute(upsert)


def get_stat_value(stat_key: str, default: int = 0) -> int:
    key = str(stat_key).strip()
    if not key:
        return int(default)

    session = get_session()
    try:
        row = session.query(SystemStat).filter(SystemStat.stat_key == key).first()
        if row is None:
            return int(default)
        return int(row.stat_value)
    finally:
        session.close()


def increment_command_execute_total() -> None:
    increment_stat(STAT_COMMAND_EXECUTE_TOTAL, 1)


def get_dashboard_metrics() -> dict[str, int | str | list[str]]:
    generated_at = beijing_now_text()

    session = get_session()
    try:
        server_count = int(session.query(func.count(Server.id)).scalar() or 0)
        user_count = int(session.query(func.count(User.id)).scalar() or 0)
        group_count = int(session.query(func.count(Group.name)).scalar() or 0)
        command_total = int(
            session.query(func.count(CommandConfig.command_key)).scalar() or 0
        )
        command_enabled_count = int(
            session.query(func.count(CommandConfig.command_key))
            .filter(CommandConfig.enabled.is_(True))
            .scalar()
            or 0
        )
        command_disabled_count = max(command_total - command_enabled_count, 0)
        today_text = beijing_today_text()
        signed_today_count = int(
            session.query(func.count(User.id))
            .filter(User.last_sign_date == today_text)
            .scalar()
            or 0
        )
        total_coins = int(session.query(func.sum(User.coins)).scalar() or 0)
        command_total_row = (
            session.query(SystemStat)
            .filter(SystemStat.stat_key == STAT_COMMAND_EXECUTE_TOTAL)
            .first()
        )
        command_execute_count = int(command_total_row.stat_value) if command_total_row else 0
        command_execute_updated_at = (
            command_total_row.updated_at if command_total_row else None
        )
    finally:
        session.close()

    connected_bot_ids: list[str] = []
    try:
        connected_bot_ids = sorted(str(bot_id) for bot_id in get_bots().keys())
    except Exception:
        connected_bot_ids = []

    connected_bot_count = len(connected_bot_ids)
    if connected_bot_count > 0:
        running_status = f"运行中（{connected_bot_count} Bot 已连接）"
    else:
        running_status = "服务已启动（暂无 Bot 连接）"

    return {
        "running_status": running_status,
        "server_count": server_count,
        "user_count": user_count,
        "group_count": group_count,
        "command_total": command_total,
        "command_enabled_count": command_enabled_count,
        "command_disabled_count": command_disabled_count,
        "command_execute_count": command_execute_count,
        "command_execute_updated_at": format_beijing_datetime(command_execute_updated_at),
        "connected_bot_count": connected_bot_count,
        "connected_bot_ids": connected_bot_ids,
        "signed_today_count": signed_today_count,
        "total_coins": total_coins,
        "generated_at": generated_at,
    }
