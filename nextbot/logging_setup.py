"""日志持久化：每次运行写一个独立文件 sink（nextbot/logging_setup.py）。

全项目统一用 ``nonebot.log.logger``（NoneBot 的 loguru 单例），默认只输出到
stderr、无持久化。本模块在启动早期（``bot.py`` 的 ``nonebot.init()`` 之前）
为该 logger **追加**一个文件 sink，与 stderr 并存：

- 文件名按进程启动时刻唯一：``logs/nextbot-YYYYMMDD-HHMMSS.log``（北京时间，
  复用 ``nextbot.time_utils.beijing_filename_timestamp``）。同一次运行调用一次、
  写同一文件；不同运行写不同文件。
- 级别 **INFO 及以上**（与控制台一致，不含 DEBUG）。
- ``enqueue=True`` 异步写不阻塞事件循环；``diagnose=False`` 防异常 traceback 把
  变量值泄漏进日志；``encoding="utf-8"``。
- 保留最新 ``LOG_RETENTION`` 份，超出删最旧（best-effort，启动时清理一次）。

所有操作均为 best-effort：建目录 / 加 sink / 清理失败只记日志、绝不抛断，
stderr 日志始终可用、绝不因日志持久化拖垮 bot。
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from nonebot.log import logger

from nextbot.data_dir import DATA_DIR
from nextbot.time_utils import beijing_filename_timestamp

if TYPE_CHECKING:
    from pathlib import Path

LOG_DIR = DATA_DIR / "logs"
LOG_RETENTION = 30
# 日志文件名前缀 + 后缀；prune 据此 glob 与排序（时间戳天然字典序 = 时间序）。
_LOG_PREFIX = "nextbot-"
_LOG_SUFFIX = ".log"
_LOG_GLOB = f"{_LOG_PREFIX}*{_LOG_SUFFIX}"
# loguru 文件 sink 格式：对齐 CLAUDE.md「[timestamp] [level] <message>」语义。
# {time:...SSSZ} = 毫秒精度本地时间 + UTC 偏移（loguru 渲染为 +08:00）；业务消息
# 不手写时间 / 级别。loguru {time} 取系统本地时区，部署在 UTC+8 时即北京时间，与
# 项目其它北京时间逻辑（容器时区 Asia/Shanghai）一致。文件 sink 不带颜色标签。
_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSSZ} [{level}] {message}"

# 当前进程已添加的文件 sink id；用于幂等重入与测试 / 关闭时移除（loguru 全局
# 单例，sink 不移除会泄漏到后续逻辑 / 测试）。None 表示尚未添加。
_file_sink_id: int | None = None


def setup_file_logging() -> Path | None:
    """为 loguru 追加本次运行的文件 sink，返回日志文件路径；失败返回 None（不抛）。

    应在启动早期调用（``nonebot.init()`` 之前），以尽量捕获 init 期日志。重复调用
    幂等：已存在 sink 时先移除旧的再按新文件名重建。
    """
    # 进程内仅一个文件 sink，模块级记 id 以便幂等重建 / 测试清理；用法与
    # db_backup.py 改写模块级路径一致，单 sink 用全局比包成类更直白。
    global _file_sink_id  # noqa: PLW0603

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(f"日志持久化失败 reason=建目录失败 dir={LOG_DIR} err={exc!r}")
        return None

    log_path = LOG_DIR / f"{_LOG_PREFIX}{beijing_filename_timestamp()}{_LOG_SUFFIX}"

    # 幂等：若已加过文件 sink，先移除旧的，避免一次运行写多个文件 / 重复落盘。
    remove_file_logging()

    try:
        _file_sink_id = logger.add(
            str(log_path),
            level="INFO",
            format=_LOG_FORMAT,
            encoding="utf-8",
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
    except (OSError, ValueError) as exc:
        logger.error(f"日志持久化失败 reason=加 sink 失败 path={log_path} err={exc!r}")
        return None

    logger.info(
        f"日志持久化已启用 path={log_path} level=INFO retention={LOG_RETENTION}"
    )
    return log_path


def remove_file_logging() -> None:
    """移除已添加的文件 sink（若有）；best-effort（不抛）。

    loguru ``remove`` 会 flush 队列后关闭 sink；供幂等重建与测试 / 关闭清理使用。
    """
    global _file_sink_id  # noqa: PLW0603 - 见 setup_file_logging 说明

    if _file_sink_id is None:
        return
    # sink 已被移除（如外部 logger.remove() 全清）时 ValueError，视为已清理。
    with contextlib.suppress(ValueError):
        logger.remove(_file_sink_id)
    _file_sink_id = None


def prune_old_logs(retention: int = LOG_RETENTION) -> None:
    """按时间倒序保留最新 ``retention`` 份日志，删除多余；best-effort（不抛）。

    glob ``nextbot-*.log``，文件名时间戳天然字典序 = 时间序。retention < 1 时按 1
    兜底（防误删全部 / 越界）。份数 ≤ retention 时不删。
    """
    keep = max(int(retention), 1)
    try:
        logs = sorted(
            LOG_DIR.glob(_LOG_GLOB),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError as exc:
        logger.error(f"日志清理失败 reason={exc!r} dir={LOG_DIR}")
        return

    if len(logs) <= keep:
        return

    stale = logs[keep:]
    deleted = 0
    for path in stale:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning(f"日志清理删除失败 path={path} reason={exc!r}")
            continue
        deleted += 1
    logger.info(f"日志清理 deleted={deleted} kept={len(logs) - deleted}")
