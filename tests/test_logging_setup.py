"""日志持久化模块（nextbot/logging_setup.py）测试。

依赖轻量：不连网、不依赖 pytest-only fixture，全程使用临时目录，**绝不触碰
真实 logs/**。可在 pytest 下运行（``uv run pytest tests/test_logging_setup.py``）
或作为脚本直接运行（``uv run python tests/test_logging_setup.py``）。

覆盖：
  - setup_file_logging：生成 nextbot-*.log，logger.info(...) 内容确实写入返回文件，
    级别 INFO+（DEBUG 不进文件），文件名 / 目录正确。
  - setup_file_logging：建目录失败时返回 None 不抛。
  - prune_old_logs：保留最新 N、删最旧、retention ≥ 总数不删、retention<1 兜底、
    不足不删。

注意：loguru 是全局单例 sink，每个用例末尾必须 remove_file_logging() 清理，避免
sink 泄漏到后续逻辑 / 其它测试 / 真实 logs。enqueue=True 为异步写，读取文件前用
remove_file_logging()（loguru remove 会 flush 队列后关闭）确保内容已落盘。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# allow `python tests/test_logging_setup.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot
from nonebot.log import logger


def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

from nextbot import logging_setup

# ── 临时环境：把 LOG_DIR 指向临时目录，确保 sink 末尾清理 ──────────


class _TmpLogEnv:
    """临时目录；patch logging_setup 模块级 LOG_DIR，并在 close 时移除 sink。"""

    def __init__(self) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="logging_setup_test_"))
        self.log_dir = self._dir / "logs"
        self._saved_log_dir = logging_setup.LOG_DIR

    def open(self) -> "_TmpLogEnv":
        logging_setup.LOG_DIR = self.log_dir
        return self

    def close(self) -> None:
        # 先移除可能残留的文件 sink，再恢复 LOG_DIR，最后删临时目录。
        logging_setup.remove_file_logging()
        logging_setup.LOG_DIR = self._saved_log_dir
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)

    def make_fake_log(self, name: str) -> Path:
        """造一个占位日志文件（仅测 prune 排序 / 删除）。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / name
        path.write_text("fake", encoding="utf-8")
        return path


# ── setup_file_logging ─────────────────────────────────────────


def test_setup_creates_file_and_writes_info(env: _TmpLogEnv) -> None:
    log_path = logging_setup.setup_file_logging()
    assert log_path is not None, "应返回日志文件路径"
    assert log_path.parent == env.log_dir
    assert log_path.name.startswith("nextbot-") and log_path.name.endswith(".log")

    probe = "探针消息_info_AB12"
    logger.info(probe)
    # remove 会 flush enqueue 队列并关闭 sink，确保内容落盘后再读。
    logging_setup.remove_file_logging()

    content = log_path.read_text(encoding="utf-8")
    assert probe in content, content
    # 格式应含 [级别]；级别名为 loguru 原样的 INFO。
    assert "[INFO]" in content, content


def test_setup_debug_not_written(env: _TmpLogEnv) -> None:
    log_path = logging_setup.setup_file_logging()
    assert log_path is not None
    assert log_path.parent == env.log_dir

    logger.debug("不应进文件的_DEBUG_探针")
    logger.info("应进文件的_INFO_探针")
    logging_setup.remove_file_logging()

    content = log_path.read_text(encoding="utf-8")
    assert "应进文件的_INFO_探针" in content
    assert "不应进文件的_DEBUG_探针" not in content, content


def test_setup_mkdir_failure_returns_none(env: _TmpLogEnv) -> None:
    # 把 LOG_DIR 指到一个"父级是文件"的路径 → mkdir 必失败，best-effort 返回 None。
    blocker = env._dir / "blocker"
    blocker.write_text("x", encoding="utf-8")
    logging_setup.LOG_DIR = blocker / "logs"
    assert logging_setup.setup_file_logging() is None


# ── prune_old_logs ─────────────────────────────────────────────


def test_prune_keeps_newest_n(env: _TmpLogEnv) -> None:
    names = [
        "nextbot-20260101000000.log",
        "nextbot-20260102000000.log",
        "nextbot-20260103000000.log",
        "nextbot-20260104000000.log",
        "nextbot-20260105000000.log",
    ]
    for n in names:
        env.make_fake_log(n)

    logging_setup.prune_old_logs(2)

    remaining = sorted(p.name for p in env.log_dir.glob("nextbot-*.log"))
    assert remaining == [
        "nextbot-20260104000000.log",
        "nextbot-20260105000000.log",
    ], remaining


def test_prune_noop_when_under_retention(env: _TmpLogEnv) -> None:
    env.make_fake_log("nextbot-20260101000000.log")
    env.make_fake_log("nextbot-20260102000000.log")
    logging_setup.prune_old_logs(5)
    remaining = sorted(p.name for p in env.log_dir.glob("nextbot-*.log"))
    assert remaining == [
        "nextbot-20260101000000.log",
        "nextbot-20260102000000.log",
    ]


def test_prune_retention_equal_total_no_delete(env: _TmpLogEnv) -> None:
    for i in range(3):
        env.make_fake_log(f"nextbot-2026010{i + 1}000000.log")
    logging_setup.prune_old_logs(3)
    assert len(list(env.log_dir.glob("nextbot-*.log"))) == 3


def test_prune_retention_below_one_falls_back(env: _TmpLogEnv) -> None:
    # retention<1 兜底为 1：保留最新一份，不清空全部。
    for i in range(3):
        env.make_fake_log(f"nextbot-2026010{i + 1}000000.log")
    logging_setup.prune_old_logs(0)
    remaining = sorted(p.name for p in env.log_dir.glob("nextbot-*.log"))
    assert remaining == ["nextbot-20260103000000.log"], remaining


def test_prune_default_retention_is_thirty(env: _TmpLogEnv) -> None:
    # 造 31 份 → 默认保留 30、删最旧 1 份。
    for i in range(31):
        env.make_fake_log(f"nextbot-202601{i + 1:02d}000000.log")
    logging_setup.prune_old_logs()
    remaining = sorted(p.name for p in env.log_dir.glob("nextbot-*.log"))
    assert len(remaining) == logging_setup.LOG_RETENTION == 30, len(remaining)
    # 删掉的应是最旧的一份（01 号）。
    assert "nextbot-20260101000000.log" not in remaining
    assert "nextbot-20260131000000.log" in remaining


def _run_all() -> int:
    env_tests = [
        test_setup_creates_file_and_writes_info,
        test_setup_debug_not_written,
        test_setup_mkdir_failure_returns_none,
        test_prune_keeps_newest_n,
        test_prune_noop_when_under_retention,
        test_prune_retention_equal_total_no_delete,
        test_prune_retention_below_one_falls_back,
        test_prune_default_retention_is_thirty,
    ]
    failed = 0
    total = len(env_tests)
    for t in env_tests:
        env = _TmpLogEnv().open()
        try:
            t(env)
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
        finally:
            env.close()
    print(f"{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
