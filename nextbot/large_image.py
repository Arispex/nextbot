"""大对象响应（地图 PNG / 世界文件）公共防护工具。

用途：让 server_tools.py 与 player_query.py 共用同一组 OOM 防护常量与
per-server 信号量工厂，避免不同 handler 各自维护一份导致漂移。

调用方继续维护各自的 `dict[int, asyncio.Semaphore]`（不同业务隔离），
仅复用 `MAX_BASE64_BYTES` / `LONG_READ_TIMEOUT` / `semaphore_for(...)`。
"""

from __future__ import annotations

import asyncio

import httpx

# 大对象响应的硬上限，超过即拒绝（防止后端 bug / 攻击者控制后端塞数 GB base64 把进程打爆）
MAX_BASE64_BYTES: int = 200 * 1024 * 1024
# 长 read 超时使用的 httpx Timeout 模板（地图渲染 / 世界文件下载可达数十秒）
LONG_READ_TIMEOUT: httpx.Timeout = httpx.Timeout(
    connect=5.0, read=300.0, write=10.0, pool=5.0
)


def semaphore_for(
    pool: dict[int, asyncio.Semaphore],
    server_id: int,
    *,
    max_concurrent: int = 1,
) -> asyncio.Semaphore:
    """从 per-server 信号量池中取 / 创建信号量。

    pool 由调用方持有（通常是模块级 dict），不同 handler 用不同 dict 隔离，
    避免 map / inventory / download 互相挤占。
    """
    sem = pool.get(server_id)
    if sem is None:
        sem = asyncio.Semaphore(max_concurrent)
        pool[server_id] = sem
    return sem


def release_server_semaphores(
    pool: dict[int, asyncio.Semaphore],
    server_id: int,
) -> None:
    """webui / bot 删除 server 时调用，回收对应信号量条目。

    Round 7 I-2.1：`semaphore_for` 创建的条目永不自动清理，长期运行 + 频繁
    增删 server 会有持续小泄漏（单个 Semaphore 几百字节，量级可控但属于"只增
    不减"的资源）。提供本 helper 让删除 server 的入口主动 cleanup。

    调用方应在 DELETE server 后对自己维护的所有信号量池逐一调用本函数：

        release_server_semaphores(_map_semaphores, server_id)
        release_server_semaphores(_download_semaphores, server_id)
        ...

    集中接线由 webui 同步审计任务负责；本步骤只提供 helper，不改 caller。
    """
    pool.pop(server_id, None)
