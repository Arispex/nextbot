from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from nonebot.log import logger
from sqlalchemy import (
    Boolean,
    DateTime,
    Executable,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from nextbot.data_dir import DATA_DIR
from nextbot.time_utils import db_now_utc_naive

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"
STAT_COMMAND_EXECUTE_TOTAL = "command.execute.total"

# Single source of truth for the factory `guest` permission set.
# `ensure_default_groups()` seeds this on first run; `同步访客权限` admin
# command diffs against it when補全 missing keys after upgrades.
DEFAULT_GUEST_PERMISSIONS: frozenset[str] = frozenset({
    "about",
    "ban.list",
    "economy.dice",
    "economy.guess_number",
    "economy.red_packet.grab",
    "economy.red_packet.list_all",
    "economy.red_packet.list_own",
    "economy.red_packet.send",
    "economy.red_packet.withdraw",
    "economy.rob",
    "economy.sign",
    "economy.transfer",
    "leaderboard.coins",
    "leaderboard.daily_sign",
    "leaderboard.deaths",
    "leaderboard.dice_income",
    "leaderboard.dice_win_rate",
    "leaderboard.fishing",
    "leaderboard.guess_number_income",
    "leaderboard.guess_number_win_rate",
    "leaderboard.map_exploration",
    "leaderboard.online_time",
    "leaderboard.rob_income",
    "leaderboard.rob_loss",
    "leaderboard.rob_penalty",
    "leaderboard.rob_success_rate",
    "leaderboard.signin",
    "leaderboard.streak",
    "leaderboard.total_online_time",
    "lottery.draw",
    "lottery.list",
    "lottery.view",
    "menu.root",
    "menu.search",
    "player_query.inventory.self",
    "player_query.inventory.user",
    "player_query.kick.self",
    "player_query.map.explored",
    "player_query.map.self",
    "player_query.map.user",
    "player_query.online",
    "player_query.progress",
    "security.login.confirm",
    "security.login.reject",
    "server.list",
    "server.send",
    "shop.buy",
    "shop.list",
    "shop.view",
    "system.tutorial",
    "user.info.self",
    "user.info.user",
    "user.register",
    "user.whitelist.sync",
    "warehouse.claim_self",
    "warehouse.drop_self",
    "warehouse.gift_self",
    "warehouse.list_self",
    "warehouse.list_user",
    "warehouse.recycle_self",
})


# 保留组名（不可创建）。owner 是 .env 短路非 DB 组，列入仅消除 UI 误导，
# 防止管理员误创建一个看似"特权"的组结果实际无效。
RESERVED_GROUP_NAMES: frozenset[str] = frozenset({
    "owner", "admin", "root", "system", "superuser",
})


# 删除身份组时受影响 user 的回退目标组。
# 旧逻辑硬编码到 "guest"，但 ensure_default_groups() seed 的 "default" 才是
# post-registration baseline（继承 guest），改为 "default" 避免 silently 把
# 用户降到 guest 等级。
GROUP_DELETE_FALLBACK = "default"


class Base(DeclarativeBase):
    pass


class Server(Base):
    __tablename__ = "server"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    ip: Mapped[str] = mapped_column(String, nullable=False)
    game_port: Mapped[str] = mapped_column(String, nullable=False)
    restapi_port: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志 / traceback
        # 显式屏蔽 token，避免任何 logger.info(server) / repr(server) / traceback 把凭证写到日志或上报
        return (
            f"<Server id={self.id} name={self.name!r} ip={self.ip!r} "
            f"game_port={self.game_port!r} restapi_port={self.restapi_port!r} token=***>"
        )


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sign_date: Mapped[str] = mapped_column(String, nullable=False, default="")
    sign_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sign_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    permissions: Mapped[str] = mapped_column(String, nullable=False, default="")
    group: Mapped[str] = mapped_column(String, nullable=False, default="guest")
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    banned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    ban_reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    rob_total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rob_success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rob_total_gain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rob_total_loss: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rob_total_penalty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_rob_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    rob_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guess_total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guess_win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guess_total_gain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guess_total_loss: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dice_total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dice_win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dice_total_gain: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dice_total_loss: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class Group(Base):
    __tablename__ = "user_group"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    permissions: Mapped[str] = mapped_column(String, nullable=False, default="")
    inherits: Mapped[str] = mapped_column(String, nullable=False, default="")


class CommandConfig(Base):
    __tablename__ = "command_config"

    command_key: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    usage: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module_path: Mapped[str] = mapped_column(String, nullable=False, default="")
    handler_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    permission: Mapped[str] = mapped_column(String, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    param_schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    param_values_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    category: Mapped[str] = mapped_column(String, nullable=False, default="")
    is_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class UserSignRecord(Base):
    __tablename__ = "user_sign_record"
    __table_args__ = (
        UniqueConstraint("user_id", "sign_date", name="uq_sign_record_user_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    sign_date: Mapped[str] = mapped_column(String, nullable=False)
    streak: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class SystemStat(Base):
    __tablename__ = "system_stat"

    stat_key: Mapped[str] = mapped_column(String, primary_key=True)
    stat_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class RedPacket(Base):
    __tablename__ = "red_packet"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    sender_user_id: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)


class RedPacketClaim(Base):
    __tablename__ = "red_packet_claim"
    __table_args__ = (
        UniqueConstraint("red_packet_id", "claimer_user_id", name="uq_redpacket_claimer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    red_packet_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claimer_user_id: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


WAREHOUSE_CAPACITY = 100


class WarehouseItem(Base):
    __tablename__ = "warehouse_item"
    __table_args__ = (
        UniqueConstraint("user_id", "slot_index", name="uq_warehouse_user_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    prefix_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_tier: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class Shop(Base):
    __tablename__ = "shop"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class ShopItem(Base):
    __tablename__ = "shop_item"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "item" | "command"
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # kind == "item"
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prefix_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_tier: Mapped[str] = mapped_column(String, nullable=False, default="none")
    actual_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_mystery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # kind == "command"
    target_server_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    command_template: Mapped[str] = mapped_column(String, nullable=False, default="")
    show_command: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class LotteryPool(Base):
    __tablename__ = "lottery_pool"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cost_per_draw: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


class LotteryPrize(Base):
    __tablename__ = "lottery_prize"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "item" | "command" | "coin"
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # percentage 0-100, NULL = share remainder

    # kind == "item"
    item_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prefix_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_tier: Mapped[str] = mapped_column(String, nullable=False, default="none")
    actual_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_mystery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # kind == "command"
    target_server_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    command_template: Mapped[str] = mapped_column(String, nullable=False, default="")
    show_command: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # kind == "coin"
    coin_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # negative allowed = deduction

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=db_now_utc_naive
    )


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_engine_and_factory() -> tuple[Engine, sessionmaker[Session]]:
    global _engine, _session_factory
    if _engine is None or _session_factory is None:
        _engine = create_engine(
            DATABASE_URL,
            future=True,
            echo=False,
            connect_args={"check_same_thread": False},
        )

        # PMA-3.2：SQLite 默认 BEGIN DEFERRED 允许并发读，但
        # 删除身份组的 cascade（删除 + bulk update + read-modify-write
        # 其它组 inherits）需要序列化写入避免 dangling references。
        # busy_timeout=5000ms 让阻塞的 writer 等待而不是立即报错；
        # BEGIN IMMEDIATE 让每个事务一开始就持写锁，所有 mutation
        # 串行执行。本项目本来也是单 SQLite writer 模型，影响可控。
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout = 5000")
            finally:
                cursor.close()

        @event.listens_for(_engine, "begin")
        def _force_immediate_begin(connection):  # noqa: ANN001
            # SH-8.2：仅在 SQLite dialect 触发 BEGIN IMMEDIATE。
            # 未来若切到 PostgreSQL / MySQL，BEGIN IMMEDIATE 是 SQLite
            # 专属语法，会让 connect 失败；加 dialect 守卫便于后续迁移。
            if connection.dialect.name != "sqlite":
                return
            # SQLAlchemy 默认 BEGIN DEFERRED；显式 BEGIN IMMEDIATE 让
            # SELECT 也持写锁，将后续 commit 排队，消除 read-modify-write
            # race 的窗口。
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine, _session_factory


def get_engine() -> Engine:
    engine, _ = _ensure_engine_and_factory()
    return engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_command_config_schema()
    ensure_user_signin_schema()
    ensure_sign_record_schema()
    ensure_sign_record_unique_schema()
    ensure_user_sign_record_index_schema()
    ensure_user_ban_schema()
    ensure_user_rob_schema()
    ensure_user_guess_schema()
    ensure_user_dice_schema()
    ensure_red_packet_schema()
    ensure_warehouse_schema()
    ensure_shop_schema()
    ensure_lottery_schema()
    ensure_user_name_unique_schema()
    ensure_user_leaderboard_indexes_schema()
    ensure_warehouse_fk_schema()
    ensure_default_groups()
    ensure_default_stats()


def get_session() -> Session:
    _, factory = _ensure_engine_and_factory()
    return factory()


def execute_rowcount(session: Session, stmt: Executable) -> int:
    """执行 INSERT/UPDATE/DELETE 并返回 rowcount。

    封装类型转换：session.execute() 在类型 stub 中返回 Result[Any]，
    但实际 INSERT/UPDATE/DELETE 返回 CursorResult，这里通过 getattr
    让 pyright 接受 .rowcount 属性，同时对非 CursorResult（如 SELECT）
    回退为 0，避免崩溃。
    """
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", 0))


def ensure_default_groups() -> None:
    session = get_session()
    try:
        guest = session.query(Group).filter(Group.name == "guest").first()
        if guest is None:
            session.add(Group(
                name="guest",
                permissions=",".join(sorted(DEFAULT_GUEST_PERMISSIONS)),
                inherits="",
            ))

        default = session.query(Group).filter(Group.name == "default").first()
        if default is None:
            session.add(
                Group(
                    name="default",
                    permissions="",
                    inherits="guest",
                )
            )
        session.commit()
    finally:
        session.close()


def ensure_default_stats() -> None:
    session = get_session()
    try:
        command_total = (
            session.query(SystemStat)
            .filter(SystemStat.stat_key == STAT_COMMAND_EXECUTE_TOTAL)
            .first()
        )
        if command_total is None:
            session.add(
                SystemStat(
                    stat_key=STAT_COMMAND_EXECUTE_TOTAL,
                    stat_value=0,
                )
            )
        session.commit()
    finally:
        session.close()


def ensure_command_config_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("command_config")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        if "usage" not in columns:
            conn.execute(
                'ALTER TABLE "command_config" ADD COLUMN "usage" TEXT NOT NULL DEFAULT ""'
            )
            changed = True
        if "aliases_json" not in columns:
            conn.execute(
                'ALTER TABLE "command_config" ADD COLUMN "aliases_json" TEXT NOT NULL DEFAULT \'[]\''
            )
            changed = True
        if "category" not in columns:
            conn.execute(
                'ALTER TABLE "command_config" ADD COLUMN "category" TEXT NOT NULL DEFAULT \'\''
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_warehouse_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("warehouse_item")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        if "value" not in columns:
            conn.execute(
                'ALTER TABLE "warehouse_item" ADD COLUMN "value" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_lottery_schema() -> None:
    # Tables themselves are created by Base.metadata.create_all; this hook
    # exists so future column additions can ALTER without dropping data.
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        pass
    finally:
        conn.close()


def ensure_shop_schema() -> None:
    # Tables themselves are created by Base.metadata.create_all; this hook
    # patches existing tables when new columns are added.
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("shop_item")').fetchall()
        if not rows:
            return
        columns = {str(row[1]) for row in rows}
        changed = False
        if "show_command" not in columns:
            conn.execute(
                'ALTER TABLE "shop_item" ADD COLUMN "show_command" BOOLEAN NOT NULL DEFAULT 0'
            )
            changed = True
        if "require_online" not in columns:
            conn.execute(
                'ALTER TABLE "shop_item" ADD COLUMN "require_online" BOOLEAN NOT NULL DEFAULT 0'
            )
            changed = True
        if "actual_value" not in columns:
            conn.execute('ALTER TABLE "shop_item" ADD COLUMN "actual_value" INTEGER')
            changed = True
        if "is_mystery" not in columns:
            conn.execute(
                'ALTER TABLE "shop_item" ADD COLUMN "is_mystery" BOOLEAN NOT NULL DEFAULT 0'
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_user_signin_schema() -> None:
    """启动时清理废弃的 signed_today 列（如存在）。

    历史上 signed_today 由 signin_reset.py worker 维护；现在已切换到
    last_sign_date 单一真源，该字段不再需要。

    SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN。失败时（旧 SQLite）
    保留列、仅 logger.warning，不阻断启动。
    """
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
        columns = {str(row[1]) for row in rows}
        if "signed_today" not in columns:
            return
        try:
            conn.execute(sa_text('ALTER TABLE "user" DROP COLUMN "signed_today"'))
            logger.info("已清理废弃字段 user.signed_today")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"清理 user.signed_today 字段失败（可能 SQLite 版本 < 3.35），"
                f"保留列不阻断启动: {exc}"
            )


def ensure_sign_record_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "user_sign_record" (
                "id" INTEGER PRIMARY KEY AUTOINCREMENT,
                "user_id" TEXT NOT NULL,
                "sign_date" TEXT NOT NULL,
                "streak" INTEGER NOT NULL DEFAULT 1,
                "created_at" DATETIME NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def ensure_sign_record_unique_schema() -> None:
    """启动时确保 user_sign_record 上有 (user_id, sign_date) 唯一索引。

    若已有重复（历史脏数据），降级为非唯一索引并 logger.warning，
    不阻断启动（让管理员手动清理重复数据后再手动重建唯一索引）。
    """
    engine = get_engine()
    with engine.begin() as conn:
        try:
            conn.execute(sa_text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "uq_sign_record_user_date" '
                'ON "user_sign_record" (user_id, sign_date)'
            ))
            logger.info("user_sign_record 唯一索引已就绪")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"user_sign_record 唯一索引创建失败（可能存在历史重复 (user_id, sign_date)，"
                f"请手动清理后重启）: {exc}"
            )
            try:
                conn.execute(sa_text(
                    'CREATE INDEX IF NOT EXISTS "ix_sign_record_user_date" '
                    'ON "user_sign_record" (user_id, sign_date)'
                ))
            except Exception:  # noqa: BLE001
                pass


def ensure_user_ban_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("user")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        if "is_banned" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "is_banned" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if "banned_at" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "banned_at" DATETIME'
            )
            changed = True
        if "ban_reason" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "ban_reason" TEXT NOT NULL DEFAULT ""'
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_user_rob_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("user")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        for col in ("rob_total_count", "rob_success_count", "rob_total_gain", "rob_total_loss", "rob_total_penalty"):
            if col not in columns:
                conn.execute(
                    f'ALTER TABLE "user" ADD COLUMN "{col}" INTEGER NOT NULL DEFAULT 0'
                )
                changed = True
        if "last_rob_time" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "last_rob_time" DATETIME'
            )
            changed = True
        if "rob_protected" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "rob_protected" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_user_guess_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("user")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        for col in ("guess_total_count", "guess_win_count", "guess_total_gain", "guess_total_loss"):
            if col not in columns:
                conn.execute(
                    f'ALTER TABLE "user" ADD COLUMN "{col}" INTEGER NOT NULL DEFAULT 0'
                )
                changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_user_dice_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("user")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        for col in ("dice_total_count", "dice_win_count", "dice_total_gain", "dice_total_loss"):
            if col not in columns:
                conn.execute(
                    f'ALTER TABLE "user" ADD COLUMN "{col}" INTEGER NOT NULL DEFAULT 0'
                )
                changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


def ensure_user_name_unique_schema() -> None:
    """启动时确保 User.name 上有大小写不敏感的唯一索引。

    若已有重复 name（历史脏数据），只创建普通索引并 logger.warning，
    不阻断启动（让管理员手工处理后再手动重建索引）。
    """
    engine = get_engine()
    with engine.begin() as conn:
        try:
            conn.execute(sa_text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "ix_user_name_lower_unique" '
                'ON "user" (LOWER("name"))'
            ))
            logger.info("User.name 唯一索引已就绪")
        except Exception as exc:  # noqa: BLE001
            # 通常是因为已有大小写重复的 name
            logger.warning(
                f"User.name 唯一索引创建失败（可能存在历史重复 name 数据，请手动清理后重启）: {exc}"
            )
            # 退而求其次：建非唯一索引提升查询性能
            try:
                conn.execute(sa_text(
                    'CREATE INDEX IF NOT EXISTS "ix_user_name_lower" '
                    'ON "user" (LOWER("name"))'
                ))
            except Exception:  # noqa: BLE001
                pass


def ensure_warehouse_fk_schema() -> None:
    """启动时确保 warehouse_item.user_id 上有索引。

    SQLite 不支持给已有列动态添加 FK 约束，但加索引可提升按 user_id
    过滤的查询性能，并保持与 ORM 层 index=True 注解一致。失败仅
    logger.warning 不阻断启动。
    """
    engine = get_engine()
    with engine.begin() as conn:
        try:
            conn.execute(sa_text(
                'CREATE INDEX IF NOT EXISTS "ix_warehouse_item_user_id" '
                'ON "warehouse_item" (user_id)'
            ))
            logger.info("warehouse_item.user_id 索引已就绪")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"warehouse_item.user_id 索引创建失败: {exc}")


def ensure_user_leaderboard_indexes_schema() -> None:
    """启动时确保排行榜常用字段都有索引。

    LB-1.1：ORDER BY <field> DESC LIMIT/OFFSET 大表时全表排序退化。
    每个字段单独 try/except，单字段失败不阻断其他字段。
    """
    engine = get_engine()
    with engine.begin() as conn:
        for col in (
            "coins",
            "sign_streak",
            "sign_total",
            "rob_total_loss",
            "rob_total_penalty",
        ):
            try:
                conn.execute(sa_text(
                    f'CREATE INDEX IF NOT EXISTS "ix_user_{col}" ON "user" ("{col}")'
                ))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"创建 user.{col} 索引失败: {exc}")
        logger.info("user 排行榜字段索引已就绪")


def ensure_user_sign_record_index_schema() -> None:
    """启动时确保 user_sign_record 上有 (sign_date, created_at) 复合索引。

    LB-8.1：今日签到排行榜 WHERE sign_date = today ORDER BY created_at 在
    无组合索引时全表扫描 + 排序。失败仅 logger.warning 不阻断启动。
    """
    engine = get_engine()
    with engine.begin() as conn:
        try:
            conn.execute(sa_text(
                'CREATE INDEX IF NOT EXISTS "ix_sign_record_date_created" '
                'ON "user_sign_record" ("sign_date", "created_at")'
            ))
            logger.info("user_sign_record (sign_date, created_at) 复合索引已就绪")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"user_sign_record (sign_date, created_at) 复合索引创建失败: {exc}"
            )


def ensure_red_packet_schema() -> None:
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "red_packet" (
                "id" INTEGER PRIMARY KEY AUTOINCREMENT,
                "name" TEXT NOT NULL UNIQUE,
                "sender_user_id" TEXT NOT NULL,
                "type" TEXT NOT NULL,
                "total_amount" INTEGER NOT NULL,
                "total_count" INTEGER NOT NULL,
                "remaining_amount" INTEGER NOT NULL,
                "remaining_count" INTEGER NOT NULL,
                "status" TEXT NOT NULL DEFAULT 'active',
                "created_at" DATETIME NOT NULL,
                "closed_at" DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS "red_packet_claim" (
                "id" INTEGER PRIMARY KEY AUTOINCREMENT,
                "red_packet_id" INTEGER NOT NULL,
                "claimer_user_id" TEXT NOT NULL,
                "amount" INTEGER NOT NULL,
                "claimed_at" DATETIME NOT NULL,
                CONSTRAINT "uq_redpacket_claimer" UNIQUE ("red_packet_id", "claimer_user_id")
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
