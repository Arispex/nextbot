from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_CST = timezone(timedelta(hours=8))


def _ts() -> str:
    return datetime.now(_CST).isoformat(timespec="milliseconds")


def _log(level: str, message: str) -> None:
    print(f"[{_ts()}] [{level}] {message}", flush=True)


def _resolve_default_db_path() -> Path:
    data_dir_env = os.environ.get("NEXTBOT_DATA_DIR")
    if data_dir_env:
        return (Path(data_dir_env) / "app.db").resolve()
    return (BASE_DIR / "app.db").resolve()


def _get_user_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute('PRAGMA table_info("user")').fetchall()
    return {str(row[1]) for row in rows}


def _count_user_rows(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute('SELECT COUNT(*) FROM "user"').fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="为 user 表添加 coins 列的一次性 SQLite migration 脚本",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="目标 SQLite 文件路径，缺省读取 $NEXTBOT_DATA_DIR/app.db 或仓库根目录 app.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将执行的 SQL 与诊断信息，不写入数据库",
    )
    parser.add_argument(
        "--backup-path",
        default=None,
        help="执行前将 DB 文件复制到该路径作为备份；若目标已存在则中止",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    db_path = Path(args.db).expanduser().resolve() if args.db else _resolve_default_db_path()

    if not db_path.exists():
        _log("ERROR", f"找不到数据库文件，db={db_path}")
        return 2

    _log("INFO", f"开始 migration，db={db_path}，dry_run={args.dry_run}")

    if args.backup_path:
        backup_path = Path(args.backup_path).expanduser().resolve()
        if backup_path.exists():
            _log("ERROR", f"备份路径已存在，拒绝覆盖，path={backup_path}")
            return 1
        if args.dry_run:
            _log("INFO", f"dry-run 跳过备份复制，path={backup_path}")
        else:
            try:
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(db_path, backup_path)
                _log("INFO", f"备份完成，path={backup_path}")
            except OSError as exc:
                _log("ERROR", f"备份失败，path={backup_path}，cause={exc}")
                return 1
    else:
        _log("WARN", "未指定 --backup-path，建议在执行前手动备份 DB")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        columns = _get_user_columns(conn)
        before_rows = _count_user_rows(conn)
        _log("INFO", f"读取 user 表结构完成，columns={sorted(columns)}，rows={before_rows}")

        if "coins" in columns:
            _log("INFO", "跳过：列 user.coins 已存在")
            return 0

        sql = 'ALTER TABLE "user" ADD COLUMN "coins" INTEGER NOT NULL DEFAULT 0'

        if args.dry_run:
            _log("INFO", f"dry-run 将执行 SQL: {sql}")
            return 0

        try:
            conn.execute("BEGIN")
            conn.execute(sql)
            conn.commit()
        except sqlite3.Error as exc:
            try:
                conn.rollback()
            except sqlite3.Error as rollback_exc:
                _log("ERROR", f"回滚失败，cause={rollback_exc}")
            _log("ERROR", f"添加列 user.coins 失败，cause={exc}")
            return 1

        after_rows = _count_user_rows(conn)
        _log(
            "INFO",
            f"添加列 user.coins 成功，db={db_path}，rows_before={before_rows}，rows_after={after_rows}",
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
