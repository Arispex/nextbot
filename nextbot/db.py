from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

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
    # BCrypt hash（与 TShock 同款 cost=7），可空：旧用户尚未 backfill 时为 NULL。
    # 明文密码不在 bot 侧持久化；注册时 hash 后丢弃，临时私聊推送一次给用户自存。
    password_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
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
            # R8 R8-D-5：PRAGMA + isolation_level=None 都是 SQLite 专属语义；
            # 未来切 PostgreSQL/MySQL 时 connect listener 一旦触发会立刻抛
            # OperationalError。加 dialect 守卫便于后续迁移（与 _force_immediate_begin
            # 内的 dialect 守卫对称）。
            # 注意：listener 注册时 _engine 已 assign，回调触发时 _engine 必非 None。
            if _engine is not None and _engine.dialect.name != "sqlite":
                return
            # SQLAlchemy 2.0 SQLite 官方 recipe：禁用 pysqlite 默认 deferred 自动 BEGIN，让 begin 事件里的 BEGIN IMMEDIATE 真正生效
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout = 5000")
                # WAL 让 reader 不阻塞 writer / writer 不阻塞 reader；synchronous=NORMAL 是 WAL 模式的常见组合，丢失最近一次 commit 概率极低但写性能显著好于 FULL
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.execute("PRAGMA synchronous = NORMAL")
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
            #
            # 调用方禁止在 session 生命周期内 await 非 DB I/O（如 bot.send）；
            # 该约束已在 plugin 层 6 轮 sweep 闭环，未来回归需在此处守门 —
            # BEGIN IMMEDIATE 让全 DB 写入串行在最慢的 await 上，违反约束会
            # 让其他命令在 busy_timeout=5000ms 内排队，超时即 OperationalError。
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine, _session_factory


def get_engine() -> Engine:
    engine, _ = _ensure_engine_and_factory()
    return engine


def _run_migration(name: str, func: Callable[[], None]) -> None:
    """R8 M-2：单个 ensure_*_schema 迁移失败时 logger.warning + 继续。

    旧实现下 17 个 ensure_*_schema 顺序裸调，中段失败抛异常 → init_db 整体
    挂掉 → 第 N+1 ~ 17 个迁移未执行，留下半 migrate 状态。改造后单个迁移
    失败仅记录 warning（保留旧 schema 不阻断启动），与现有
    ensure_user_name_unique_schema / ensure_user_leaderboard_indexes_schema
    已有的 try/except 风格一致。

    注意：只用于幂等的 ensure_*_schema migration helpers；ensure_default_*
    是 seeding 不是 migration，失败必须阻断启动，不要走这条路径。
    """
    try:
        func()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"迁移失败：name={name} reason={exc!r}（保留旧 schema 不阻断启动）"
        )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _run_migration("command_config", ensure_command_config_schema)
    _run_migration("user_signin", ensure_user_signin_schema)
    _run_migration("user_password_hash", ensure_user_password_hash_schema)
    _run_migration("sign_record", ensure_sign_record_schema)
    _run_migration("sign_record_unique", ensure_sign_record_unique_schema)
    _run_migration("user_sign_record_index", ensure_user_sign_record_index_schema)
    _run_migration("user_ban", ensure_user_ban_schema)
    _run_migration("user_rob", ensure_user_rob_schema)
    _run_migration("user_guess", ensure_user_guess_schema)
    _run_migration("user_dice", ensure_user_dice_schema)
    _run_migration("red_packet", ensure_red_packet_schema)
    _run_migration("warehouse", ensure_warehouse_schema)
    _run_migration("shop", ensure_shop_schema)
    _run_migration("lottery", ensure_lottery_schema)
    _run_migration("user_name_unique", ensure_user_name_unique_schema)
    _run_migration("user_leaderboard_indexes", ensure_user_leaderboard_indexes_schema)
    _run_migration("warehouse_fk", ensure_warehouse_fk_schema)
    # ensure_default_* 是 seeding 不是 migration，失败必须阻断启动（业务需要这些行）
    ensure_default_groups()
    ensure_default_stats()


def get_session() -> Session:
    _, factory = _ensure_engine_and_factory()
    return factory()


def execute_rowcount(session: Session, stmt: Executable) -> int:
    """执行 INSERT/UPDATE/DELETE 并返回 rowcount。

    封装类型转换：session.execute() 在类型 stub 中返回 Result[Any]，
    但实际 INSERT/UPDATE/DELETE 返回 CursorResult。

    R8 M-1：误传 SELECT 等非 mutation 语句时 raise TypeError，避免 silently
    return 0 导致 ban_core / economy / lottery 等 50+ 处 `if rowcount == 0:`
    走业务降级误触发（例如把"未匹配到行"误判为"已经被封禁 / 已经领取"等）。
    """
    result = session.execute(stmt)
    rowcount = getattr(result, "rowcount", None)
    if rowcount is None:
        raise TypeError(  # noqa: TRY003
            f"execute_rowcount 仅支持 INSERT/UPDATE/DELETE，"
            f"收到 stmt={type(stmt).__name__}（可能误传 SELECT）"
        )
    return int(rowcount)


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
        # D-1.6：显式 try/except: rollback。session.close() 隐式 rollback 可兜底，
        # 但显式 rollback 让"commit 失败 → 回滚"语义更清晰，并避免该 connection
        # 在 pool 内多停留一个 close 周期才被回收 / 重置事务状态。
        # R8 R8-D-4：捕获 commit 异常变量，rollback 自身抛错时仅 logger.exception
        # 不覆盖原 commit 异常 —— 否则裸 raise 会抛 rollback 异常，把根因
        # 降为 __context__，让 caller 拿到的异常类型与诊断歧义。
        try:
            session.commit()
        except Exception as commit_exc:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("rollback 自身抛错（已保留原 commit 异常）")
            raise commit_exc  # noqa: TRY201
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
        # D-1.6：显式 try/except: rollback。
        # R8 R8-D-4：捕获 commit 异常变量，rollback 自身抛错时仅 logger.exception
        # 不覆盖原 commit 异常。
        try:
            session.commit()
        except Exception as commit_exc:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("rollback 自身抛错（已保留原 commit 异常）")
            raise commit_exc  # noqa: TRY201
    finally:
        session.close()


def ensure_command_config_schema() -> None:
    if not DB_PATH.exists():
        return

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("command_config")')).fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        if "usage" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "command_config" ADD COLUMN "usage" TEXT NOT NULL DEFAULT ""'
            ))
        if "aliases_json" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "command_config" ADD COLUMN "aliases_json" TEXT NOT NULL DEFAULT \'[]\''
            ))
        if "category" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "command_config" ADD COLUMN "category" TEXT NOT NULL DEFAULT \'\''
            ))


def ensure_warehouse_schema() -> None:
    if not DB_PATH.exists():
        return

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("warehouse_item")')).fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        if "value" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "warehouse_item" ADD COLUMN "value" INTEGER NOT NULL DEFAULT 0'
            ))


def ensure_lottery_schema() -> None:
    """No-op forward-compat hook for lottery schema migrations.

    Tables themselves are created by ``Base.metadata.create_all``; this
    placeholder exists so future column additions can ALTER without dropping
    data. Currently no migration is needed, so we deliberately do nothing
    (avoid opening a sqlite3 connection that does no work).
    """
    return


def ensure_shop_schema() -> None:
    # Tables themselves are created by Base.metadata.create_all; this hook
    # patches existing tables when new columns are added.
    if not DB_PATH.exists():
        return
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("shop_item")')).fetchall()
        if not rows:
            return
        columns = {str(row[1]) for row in rows}
        if "show_command" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "shop_item" ADD COLUMN "show_command" BOOLEAN NOT NULL DEFAULT 0'
            ))
        if "require_online" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "shop_item" ADD COLUMN "require_online" BOOLEAN NOT NULL DEFAULT 0'
            ))
        if "actual_value" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "shop_item" ADD COLUMN "actual_value" INTEGER'
            ))
        if "is_mystery" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "shop_item" ADD COLUMN "is_mystery" BOOLEAN NOT NULL DEFAULT 0'
            ))


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
                f"清理 user.signed_today 字段失败（SQLite 版本={sqlite3.sqlite_version}，"
                f"需要 ≥ 3.35），保留列不阻断启动: {exc}"
            )


def ensure_user_password_hash_schema() -> None:
    """启动时确保 user 表上有 password_hash 列（旧库升级）。

    SQLite ALTER TABLE ADD COLUMN 幂等：先 PRAGMA 检查再 ALTER，缺列才加。
    新建库由 Base.metadata.create_all 直接带上该列，本函数 no-op。

    失败仅 logger.warning 不阻断启动（_run_migration 包装层兜底）。
    """
    if not DB_PATH.exists():
        return

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        if "password_hash" not in columns:
            conn.execute(sa_text(
                'ALTER TABLE "user" ADD COLUMN "password_hash" TEXT'
            ))


def ensure_sign_record_schema() -> None:
    if not DB_PATH.exists():
        return

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa_text(
            """
            CREATE TABLE IF NOT EXISTS "user_sign_record" (
                "id" INTEGER PRIMARY KEY AUTOINCREMENT,
                "user_id" TEXT NOT NULL,
                "sign_date" TEXT NOT NULL,
                "streak" INTEGER NOT NULL DEFAULT 1,
                "created_at" DATETIME NOT NULL
            )
            """
        ))


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


# D-1.4：合并 user 表的多个 ensure_*_schema 为单次 PRAGMA + 多列条件 ALTER。
# 旧的 ensure_user_ban_schema / ensure_user_rob_schema / ensure_user_guess_schema /
# ensure_user_dice_schema 现在都是 _ensure_user_columns() 的 wrapper，保留旧函数名
# 维持 import 路径向后兼容；启动时只触发一次 PRAGMA + 一个事务。
_USER_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    # ban 相关
    ("is_banned", 'ALTER TABLE "user" ADD COLUMN "is_banned" INTEGER NOT NULL DEFAULT 0'),
    ("banned_at", 'ALTER TABLE "user" ADD COLUMN "banned_at" DATETIME'),
    ("ban_reason", 'ALTER TABLE "user" ADD COLUMN "ban_reason" TEXT NOT NULL DEFAULT ""'),
    # rob 相关
    ("rob_total_count", 'ALTER TABLE "user" ADD COLUMN "rob_total_count" INTEGER NOT NULL DEFAULT 0'),
    ("rob_success_count", 'ALTER TABLE "user" ADD COLUMN "rob_success_count" INTEGER NOT NULL DEFAULT 0'),
    ("rob_total_gain", 'ALTER TABLE "user" ADD COLUMN "rob_total_gain" INTEGER NOT NULL DEFAULT 0'),
    ("rob_total_loss", 'ALTER TABLE "user" ADD COLUMN "rob_total_loss" INTEGER NOT NULL DEFAULT 0'),
    ("rob_total_penalty", 'ALTER TABLE "user" ADD COLUMN "rob_total_penalty" INTEGER NOT NULL DEFAULT 0'),
    ("last_rob_time", 'ALTER TABLE "user" ADD COLUMN "last_rob_time" DATETIME'),
    ("rob_protected", 'ALTER TABLE "user" ADD COLUMN "rob_protected" INTEGER NOT NULL DEFAULT 0'),
    # guess 相关
    ("guess_total_count", 'ALTER TABLE "user" ADD COLUMN "guess_total_count" INTEGER NOT NULL DEFAULT 0'),
    ("guess_win_count", 'ALTER TABLE "user" ADD COLUMN "guess_win_count" INTEGER NOT NULL DEFAULT 0'),
    ("guess_total_gain", 'ALTER TABLE "user" ADD COLUMN "guess_total_gain" INTEGER NOT NULL DEFAULT 0'),
    ("guess_total_loss", 'ALTER TABLE "user" ADD COLUMN "guess_total_loss" INTEGER NOT NULL DEFAULT 0'),
    # dice 相关
    ("dice_total_count", 'ALTER TABLE "user" ADD COLUMN "dice_total_count" INTEGER NOT NULL DEFAULT 0'),
    ("dice_win_count", 'ALTER TABLE "user" ADD COLUMN "dice_win_count" INTEGER NOT NULL DEFAULT 0'),
    ("dice_total_gain", 'ALTER TABLE "user" ADD COLUMN "dice_total_gain" INTEGER NOT NULL DEFAULT 0'),
    ("dice_total_loss", 'ALTER TABLE "user" ADD COLUMN "dice_total_loss" INTEGER NOT NULL DEFAULT 0'),
)

_user_columns_ensured: bool = False


def _ensure_user_columns() -> None:
    """单次 PRAGMA + 多列条件 ALTER，合并 ban / rob / guess / dice 四个 user 列迁移。

    D-1.4：旧实现每个 ensure_user_*_schema 各自开 raw sqlite3 连接 + 各自
    PRAGMA table_info("user")。同一启动周期重复 4 次。改造后单次 PRAGMA +
    单事务多列 ALTER，逻辑等价、性能改善。

    幂等：首次调用执行完后置 _user_columns_ensured=True，后续 wrapper 调用 no-op。
    """
    global _user_columns_ensured
    if _user_columns_ensured:
        return
    if not DB_PATH.exists():
        return

    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(sa_text('PRAGMA table_info("user")')).fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        for col_name, alter_sql in _USER_COLUMN_MIGRATIONS:
            if col_name not in columns:
                conn.execute(sa_text(alter_sql))

    _user_columns_ensured = True


def ensure_user_ban_schema() -> None:
    """Wrapper：保留 import 路径向后兼容，底层走合并迁移。"""
    _ensure_user_columns()


def ensure_user_rob_schema() -> None:
    """Wrapper：保留 import 路径向后兼容，底层走合并迁移。"""
    _ensure_user_columns()


def ensure_user_guess_schema() -> None:
    """Wrapper：保留 import 路径向后兼容，底层走合并迁移。"""
    _ensure_user_columns()


def ensure_user_dice_schema() -> None:
    """Wrapper：保留 import 路径向后兼容，底层走合并迁移。"""
    _ensure_user_columns()


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

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa_text(
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
        ))
        conn.execute(sa_text(
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
        ))


def wal_checkpoint_truncate() -> None:
    """R8 M-3 + R9 R9-D-4：进程关闭前主动 checkpoint + truncate WAL 文件。

    WAL 模式下 `app.db-wal` / `app.db-shm` 持续累积；自动 checkpoint
    需要"无活跃 reader 跨越 frame"才能完整推进，nextbot 长连接 daemon +
    BEGIN IMMEDIATE 持锁经常只能 partial 推进，WAL 文件可累积到 GB 级。
    on_shutdown 时主动 TRUNCATE 一次确保进程正常退出时 WAL 不残留。

    PRAGMA wal_checkpoint(TRUNCATE) 返回 (busy, log_pages, checkpointed)：
    - busy=0：truncate 完整成功，WAL 文件已实际清空。
    - busy=1：有 reader 阻塞 → 仅部分 checkpoint，WAL 未实际 truncate。
    SQLite 在 busy 时不抛异常，需主动读返回值 + 条件 warning，否则运维
    无法观测 WAL 是否实际收尾。

    接线契约：本函数由 bot.py 的 @driver.on_shutdown 调用，与
    nextbot.tshock_api.close_shared_client 并列。仅负责 checkpoint，
    不负责 engine.dispose（engine 会在进程退出时自然释放）。
    """
    if not DB_PATH.exists():
        return
    try:
        engine = get_engine()
        with engine.begin() as conn:
            row = conn.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
        if row is not None:
            busy = int(row[0])
            log_pages = int(row[1])
            checkpointed = int(row[2])
            if busy != 0:
                logger.warning(
                    f"WAL checkpoint 部分推进：busy={busy} log_pages={log_pages} "
                    f"checkpointed={checkpointed}（可能有 reader 持锁，WAL 文件未实际 truncate）"
                )
            else:
                logger.info(
                    f"WAL checkpoint 完成：log_pages={log_pages} checkpointed={checkpointed}"
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"WAL checkpoint 失败：reason={exc!r}")
