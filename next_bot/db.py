from __future__ import annotations

import sqlite3
from pathlib import Path

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"


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
    permissions: Mapped[str] = mapped_column(String, nullable=False, default="")
    group: Mapped[str] = mapped_column(String, nullable=False, default="guest")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
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
    is_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class SystemStat(Base):
    __tablename__ = "system_stat"

    stat_key: Mapped[str] = mapped_column(String, primary_key=True)
    stat_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
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
            session.add(Group(name="guest", permissions="um.add", inherits=""))

        default = session.query(Group).filter(Group.name == "default").first()
        if default is None:
            session.add(
                Group(
                    name="default",
                    permissions="sm.*,gm.*,pm.*",
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
            .filter(SystemStat.stat_key == "command.execute.total")
            .first()
        )
        if command_total is None:
            session.add(
                SystemStat(
                    stat_key="command.execute.total",
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
        if "usage" not in columns:
            conn.execute(
                'ALTER TABLE "command_config" ADD COLUMN "usage" TEXT NOT NULL DEFAULT ""'
            )
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
        if changed:
            conn.commit()
    finally:
        conn.close()
