from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
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


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signed_today: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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


def get_engine() -> Engine:
    return create_engine(
        DATABASE_URL,
        future=True,
        echo=False,
        connect_args={"check_same_thread": False},
    )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    ensure_command_config_schema()
    ensure_user_signin_schema()
    ensure_sign_record_schema()
    ensure_user_ban_schema()
    ensure_user_rob_schema()
    ensure_user_guess_schema()
    ensure_user_dice_schema()
    ensure_red_packet_schema()
    ensure_shop_schema()
    ensure_lottery_schema()
    ensure_default_groups()
    ensure_default_stats()


def get_session() -> Session:
    engine = get_engine()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return session_factory()


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
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute('PRAGMA table_info("user")').fetchall()
        if not rows:
            return

        columns = {str(row[1]) for row in rows}
        changed = False
        if "signed_today" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "signed_today" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if "last_sign_date" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "last_sign_date" TEXT NOT NULL DEFAULT ""'
            )
            changed = True
        if "sign_streak" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "sign_streak" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if "sign_total" not in columns:
            conn.execute(
                'ALTER TABLE "user" ADD COLUMN "sign_total" INTEGER NOT NULL DEFAULT 0'
            )
            changed = True
        if changed:
            conn.commit()
    finally:
        conn.close()


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
