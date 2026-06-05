"""数据库备份模块（nextbot/db_backup.py）测试。

依赖轻量：不连网、不依赖 pytest-only fixture，全程使用临时目录 + 临时
SQLite 库，**绝不触碰真实 app.db / backups/**。可在 pytest 下运行
（``uv run pytest tests/test_db_backup.py``）或作为脚本直接运行
（``uv run python tests/test_db_backup.py``）。

覆盖：
  - backup_database：生成快照文件、内容可读（sqlite 可打开、表 / 数据完整）。
  - backup_database：源库不存在时返回 None 不抛。
  - prune_old_backups：保留最新 N、删最旧、retention ≥ 总数不删、retention<1 兜底。
  - run_backup_once：备份 + 清理串起来。
  - loop 判定逻辑：now-last 跨过 interval 才到点（用纯函数判定，不真跑 loop）。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

# allow `python tests/test_db_backup.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

from nextbot import db_backup

# ── 临时环境：把 DB_PATH / BACKUP_DIR 指向临时目录 ──────────────


class _TmpBackupEnv:
    """临时目录 + 临时 SQLite 库；patch db_backup 模块级 DB_PATH / BACKUP_DIR。"""

    def __init__(self, *, create_db: bool = True) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="db_backup_test_"))
        self.db_path = self._dir / "app.db"
        self.backup_dir = self._dir / "backups"
        self._saved_db = db_backup.DB_PATH
        self._saved_backup_dir = db_backup.BACKUP_DIR
        if create_db:
            self._seed_db()

    def _seed_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
            conn.executemany(
                "INSERT INTO demo (id, name) VALUES (?, ?)",
                [(1, "alpha"), (2, "bravo"), (3, "charlie")],
            )
            conn.commit()
        finally:
            conn.close()

    def open(self) -> "_TmpBackupEnv":
        db_backup.DB_PATH = self.db_path
        db_backup.BACKUP_DIR = self.backup_dir
        return self

    def close(self) -> None:
        db_backup.DB_PATH = self._saved_db
        db_backup.BACKUP_DIR = self._saved_backup_dir
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)

    def make_fake_backup(self, name: str) -> Path:
        """造一个占位备份文件（仅测 prune 排序 / 删除，不要求是有效库）。"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / name
        path.write_bytes(b"fake")
        return path


# ── backup_database ────────────────────────────────────────────


def test_backup_creates_readable_snapshot(env: _TmpBackupEnv) -> None:
    snapshot = db_backup.backup_database()
    assert snapshot is not None, "应返回快照路径"
    assert snapshot.exists(), "快照文件应落盘"
    assert snapshot.parent == env.backup_dir
    assert snapshot.name.startswith("app-") and snapshot.name.endswith(".db")

    # 快照必须是可正常打开的 SQLite 文件，表 / 数据完整。
    conn = sqlite3.connect(str(snapshot))
    try:
        rows = conn.execute("SELECT id, name FROM demo ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [(1, "alpha"), (2, "bravo"), (3, "charlie")]


def test_backup_missing_source_returns_none(env: _TmpBackupEnv) -> None:
    env.db_path.unlink()
    assert db_backup.backup_database() is None


# ── prune_old_backups ──────────────────────────────────────────


def test_prune_keeps_newest_n(env: _TmpBackupEnv) -> None:
    # 文件名时间戳天然有序：app-<ts>.db；倒序保留最新。
    names = [
        "app-20260101000000.db",
        "app-20260102000000.db",
        "app-20260103000000.db",
        "app-20260104000000.db",
        "app-20260105000000.db",
    ]
    for n in names:
        env.make_fake_backup(n)

    db_backup.prune_old_backups(2)

    remaining = sorted(p.name for p in env.backup_dir.glob("app-*.db"))
    assert remaining == ["app-20260104000000.db", "app-20260105000000.db"], remaining


def test_prune_noop_when_under_retention(env: _TmpBackupEnv) -> None:
    env.make_fake_backup("app-20260101000000.db")
    env.make_fake_backup("app-20260102000000.db")
    db_backup.prune_old_backups(5)
    remaining = sorted(p.name for p in env.backup_dir.glob("app-*.db"))
    assert remaining == ["app-20260101000000.db", "app-20260102000000.db"]


def test_prune_retention_equal_total_no_delete(env: _TmpBackupEnv) -> None:
    for i in range(3):
        env.make_fake_backup(f"app-2026010{i + 1}000000.db")
    db_backup.prune_old_backups(3)
    assert len(list(env.backup_dir.glob("app-*.db"))) == 3


def test_prune_retention_below_one_falls_back(env: _TmpBackupEnv) -> None:
    # retention<1 兜底为 1：保留最新一份，不清空全部。
    for i in range(3):
        env.make_fake_backup(f"app-2026010{i + 1}000000.db")
    db_backup.prune_old_backups(0)
    remaining = sorted(p.name for p in env.backup_dir.glob("app-*.db"))
    assert remaining == ["app-20260103000000.db"], remaining


# ── run_backup_once ────────────────────────────────────────────


def test_run_backup_once_creates_and_prunes(env: _TmpBackupEnv) -> None:
    # 预置 2 份旧占位 + 跑一次真实备份（retention=1）→ 应只剩最新 1 份。
    env.make_fake_backup("app-20200101000000.db")
    env.make_fake_backup("app-20200102000000.db")
    db_backup.run_backup_once(1)
    remaining = sorted(p.name for p in env.backup_dir.glob("app-*.db"))
    assert len(remaining) == 1, remaining
    # 真实备份名为当前北京时间（2026+），必然 > 2020 占位 → 旧占位全删。
    assert remaining[0].startswith("app-2026") or not remaining[0].startswith(
        "app-2020"
    ), remaining


# ── loop 到点判定（直接测真实 db_backup._is_due，不真跑 loop / 不等 24h）──


def test_loop_due_when_no_previous() -> None:
    assert db_backup._is_due(None, now=1000.0, interval_hours=24) is True


def test_loop_not_due_before_interval() -> None:
    # 间隔 24h，距上次仅 1h → 未到点。
    assert db_backup._is_due(0.0, now=3600.0, interval_hours=24) is False


def test_loop_due_after_interval() -> None:
    # 间隔 1h，距上次 1h+ → 到点。
    assert db_backup._is_due(0.0, now=3600.0 + 1, interval_hours=1) is True


def _run_all() -> int:
    env_tests = [
        test_backup_creates_readable_snapshot,
        test_backup_missing_source_returns_none,
        test_prune_keeps_newest_n,
        test_prune_noop_when_under_retention,
        test_prune_retention_equal_total_no_delete,
        test_prune_retention_below_one_falls_back,
        test_run_backup_once_creates_and_prunes,
    ]
    plain_tests = [
        test_loop_due_when_no_previous,
        test_loop_not_due_before_interval,
        test_loop_due_after_interval,
    ]
    failed = 0
    total = len(env_tests) + len(plain_tests)
    for t in env_tests:
        env = _TmpBackupEnv().open()
        try:
            t(env)
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
        finally:
            env.close()
    for t in plain_tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
