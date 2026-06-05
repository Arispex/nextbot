"""SQLite 自动备份：在线快照 + 保留清理 + 后台调度。

WAL 模式下直接 copy ``app.db`` 会漏掉 WAL 中尚未 checkpoint 的帧，因此
统一走 sqlite3 在线备份 API ``src.backup(dst)``，正确处理锁与 WAL，产出
单文件一致快照。所有备份 / 清理均为 best-effort：失败只记日志，绝不向上
抛断、绝不影响 bot 运行或下一次调度。

调度由 ``bot.py`` 的 @driver.on_startup / @driver.on_shutdown 接线：启动后
台 asyncio tick-loop，启动时（enabled）立即备份一次，之后按 settings 中的
``db_backup_interval_hours`` 间隔轮询。改间隔 / 开关运行时生效，无需重启。
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from typing import TYPE_CHECKING

from nonebot.log import logger

from nextbot.data_dir import DATA_DIR
from nextbot.db import DB_PATH
from nextbot.time_utils import beijing_filename_timestamp

if TYPE_CHECKING:
    from pathlib import Path

BACKUP_DIR = DATA_DIR / "backups"
# 备份文件名前缀 + 后缀；prune 据此 glob 与排序（时间戳天然字典序 = 时间序）。
_BACKUP_PREFIX = "app-"
_BACKUP_SUFFIX = ".db"
_BACKUP_GLOB = f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"
# tick 轮询间隔；与 interval_hours 解耦，仅决定"多久检查一次是否到点"。
_TICK_SECONDS = 60.0
# 调度读配置失败时的兜底默认（与 settings_service 默认一致）。
_DEFAULT_ENABLED = True
_DEFAULT_INTERVAL_HOURS = 24
_DEFAULT_RETENTION = 30
_SECONDS_PER_HOUR = 3600


def _close_quietly(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    with contextlib.suppress(sqlite3.Error):
        conn.close()


def _unlink_quietly(path: Path) -> bool:
    """删除文件，成功返回 True；失败记 warning 返回 False（不抛）。"""
    try:
        path.unlink()
    except OSError as exc:
        logger.warning(f"备份清理删除失败 path={path} reason={exc!r}")
        return False
    return True


def backup_database() -> Path | None:
    """生成一份 SQLite 在线备份快照，返回快照路径；失败返回 None（不抛）。

    输出 ``backups/app-YYYYMMDDHHMMSS.db``（北京时间）。源库不存在视为
    无可备份，记 warning 返回 None。
    """
    if not DB_PATH.exists():
        logger.warning(f"数据库备份跳过 reason=源库不存在 path={DB_PATH}")
        return None

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            f"数据库备份失败 reason=建目录失败 dir={BACKUP_DIR} err={exc}"
        )
        return None

    dst_path = BACKUP_DIR / (
        f"{_BACKUP_PREFIX}{beijing_filename_timestamp()}{_BACKUP_SUFFIX}"
    )
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    try:
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(dst_path))
        # 在线备份 API：WAL 安全，产出单文件一致快照。
        src.backup(dst)
    except (sqlite3.Error, OSError) as exc:
        logger.error(f"数据库备份失败 reason={exc!r} dst={dst_path}")
        # 关连接后清理可能残留的半成品快照，避免 prune 误把它当成有效份。
        _close_quietly(dst)
        dst = None
        with contextlib.suppress(OSError):
            if dst_path.exists():
                dst_path.unlink()
        return None
    finally:
        _close_quietly(dst)
        _close_quietly(src)

    try:
        size_bytes = dst_path.stat().st_size
    except OSError:
        size_bytes = -1
    logger.info(f"数据库备份成功 path={dst_path} bytes={size_bytes}")
    return dst_path


def prune_old_backups(retention: int) -> None:
    """按时间倒序保留最新 ``retention`` 份，删除多余；best-effort（不抛）。

    retention < 1 时按 1 兜底（防误删全部 / 越界）。份数 ≤ retention 时不删。
    """
    keep = max(int(retention), 1)
    try:
        backups = sorted(
            BACKUP_DIR.glob(_BACKUP_GLOB),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError as exc:
        logger.error(f"备份清理失败 reason={exc!r} dir={BACKUP_DIR}")
        return

    if len(backups) <= keep:
        return

    stale = backups[keep:]
    deleted = sum(1 for path in stale if _unlink_quietly(path))
    logger.info(f"备份清理 deleted={deleted} kept={len(backups) - deleted}")


def run_backup_once(retention: int) -> None:
    """串起一次备份 + 清理，供调度与测试复用；best-effort（不抛）。"""
    snapshot = backup_database()
    if snapshot is None:
        return
    prune_old_backups(retention)


def _read_backup_settings() -> tuple[bool, int, int]:
    """读取当前 (enabled, interval_hours, retention)；读取失败时回落默认开/24/30。

    用 settings snapshot（与现有设置消费一致），改开关 / 间隔运行时生效。
    """
    try:
        from server.settings_service import get_settings_snapshot

        snapshot = get_settings_snapshot()
        enabled = bool(snapshot["db_backup_enabled"])
        interval_hours = int(snapshot["db_backup_interval_hours"])
        retention = int(snapshot["db_backup_retention"])
    except Exception as exc:  # noqa: BLE001 - 调度读配置失败必须降级，绝不崩 loop
        logger.warning(f"读取备份设置失败，回退默认值 reason={exc!r}")
        return _DEFAULT_ENABLED, _DEFAULT_INTERVAL_HOURS, _DEFAULT_RETENTION
    return enabled, interval_hours, retention


def _is_due(last_monotonic: float | None, now: float, interval_hours: int) -> bool:
    """到点判定：从无上次记录 / 距上次已满 interval 即到点。"""
    interval_seconds = max(int(interval_hours), 1) * _SECONDS_PER_HOUR
    return last_monotonic is None or now - last_monotonic >= interval_seconds


async def backup_scheduler_loop(stop_event: asyncio.Event) -> None:
    """后台备份调度 loop：启动立即备一次（enabled），之后按间隔轮询。

    每 tick 重读设置 → 改 enabled / interval / retention 运行时生效。备份在
    线程池执行（sqlite .backup() 是阻塞调用），避免阻塞事件循环。
    """
    loop = asyncio.get_running_loop()
    last_backup_monotonic: float | None = None

    # 启动时立即备份一次（enabled 时）。
    enabled, _interval_hours, retention = _read_backup_settings()
    if enabled:
        logger.info("数据库备份调度启动，执行首次备份")
        await loop.run_in_executor(None, run_backup_once, retention)
        last_backup_monotonic = loop.time()
    else:
        logger.info("数据库备份调度启动，自动备份已关闭，跳过首次备份")

    while not stop_event.is_set():
        try:
            # 用 wait_for 在 stop_event 上等待，stop 时立即返回提前退出。
            await asyncio.wait_for(stop_event.wait(), timeout=_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break

        if stop_event.is_set():
            break

        enabled, interval_hours, retention = _read_backup_settings()
        if not enabled:
            # 关闭期间不积累 "欠账"：重置基准，重新开启后按新基准计时。
            last_backup_monotonic = None
            continue

        if _is_due(last_backup_monotonic, loop.time(), interval_hours):
            try:
                await loop.run_in_executor(None, run_backup_once, retention)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - best-effort，绝不崩 loop
                logger.error(f"数据库备份调度执行异常 reason={exc!r}")
            last_backup_monotonic = loop.time()

    logger.info("数据库备份调度已停止")
