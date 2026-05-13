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

    Deprecated（Round 8 M-5）：推荐使用 `release_server_semaphores_all`，
    各 pool 在模块顶部通过 `register_server_semaphore_pool` 注册，删除
    server 时一次性遍历所有 pool 清理，避免 caller 各自维护清理逻辑。

    本 helper 仍保留以向后兼容（如未注册的临时 pool）。
    """
    pool.pop(server_id, None)


# Round 8 M-5：模块级注册中心。所有 server-keyed semaphore pool 都注册在这里，
# server 删除时调 `release_server_semaphores_all(server_id)` 一次性清理。
# 避免各 caller 维护 8+ 个 `release_server_semaphores(pool, id)` 调用。
_registered_server_pools: list[dict[int, asyncio.Semaphore]] = []


def register_server_semaphore_pool(pool: dict[int, asyncio.Semaphore]) -> None:
    """Round 8 M-5：plugin 模块级 semaphore pool 注册到中央清理列表。

    server 删除时由 `release_server_semaphores_all(server_id)` 遍历所有
    已注册 pool 调 `pool.pop(server_id, None)`，避免 caller 各自维护清理
    逻辑。

    调用方在 pool 定义后立刻调一次：

        _xxx_semaphores: dict[int, asyncio.Semaphore] = {}
        register_server_semaphore_pool(_xxx_semaphores)

    幂等：重复调相同 pool 不重复注册。**注意**：使用 identity（is）而非
    equality（==）判定，因为模块 import 阶段所有 pool 都是空 dict，
    `{} == {}` 为 True 会导致只有第一个 pool 注册成功，后续全被去重剔除。
    """
    if not any(p is pool for p in _registered_server_pools):
        _registered_server_pools.append(pool)


def release_server_semaphores_all(server_id: int) -> None:
    """Round 8 M-5：删除 server 时调用，清理所有已注册 pool 中对应 entry。

    替代原本散落的多次 `release_server_semaphores(pool, server_id)` 调用，
    通过模块级 `_registered_server_pools` 遍历所有已注册 pool 一次清理。
    """
    for pool in _registered_server_pools:
        pool.pop(server_id, None)
