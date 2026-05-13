"""服务器广播 fan-out 公共 helper。

替代历史上散落在 security.py / ban_core.py / ban.py 的串行 `for server in servers` 循环：
统一走 `asyncio.gather` 并行 + per-server 信号量限制（避免对同一台 TShock 短时间内放大压力），
异常被吞回成 `BroadcastOutcome`，调用方不需要再写 try/except。

调用方只需提供一个 `fn(server) -> BroadcastOutcome` 协程：构造 path、发请求、把结果归类成 ok/detail。

设计与 `nextbot/large_image.semaphore_for` 同模式（per-server 隔离），
但本模块封装得更高一层：调用方拿到的是已聚合好的 outcomes 列表，避免重复 boilerplate。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from typing import Callable, Generic, NamedTuple, TypeVar

from nonebot.log import logger

from nextbot.db import Server
from nextbot.large_image import register_server_semaphore_pool, semaphore_for

R = TypeVar("R")


class BroadcastOutcome(NamedTuple, Generic[R]):
    server: Server
    ok: bool
    detail: str
    payload: R | None


# 模块级 per-server 信号量池。max_concurrent_per_server 默认 1，匹配 ban_core
# 「先 GET /blacklist 再 POST /blacklist/add」这种连续两次往返的工作负载。
_broadcast_semaphores: dict[int, asyncio.Semaphore] = {}
register_server_semaphore_pool(_broadcast_semaphores)  # R8 M-5


async def broadcast(
    servers: Sequence[Server],
    fn: Callable[[Server], Awaitable[BroadcastOutcome[R]]],
    *,
    max_concurrent_per_server: int = 1,
) -> list[BroadcastOutcome[R]]:
    """对所有服务器并行调用 fn，每台服务器内部最多 max_concurrent_per_server 个并发请求。

    fn 内部抛出的异常会被捕获并转成 ok=False 的 outcome，避免单台服务器异常打断整个 gather。
    返回值按 server.id 升序排序，让调用方渲染顺序稳定。
    """

    async def _wrap(srv: Server) -> BroadcastOutcome[R]:
        # Round 7 I-3.1：把 semaphore_for(...) 与 async with sem 都纳入 try/except，
        # 防止 sem 获取 / acquire 阶段未来重构引入抛错时让 gather(return_exceptions=False)
        # cancel 其它 task。当前 dict.get / Semaphore.acquire 不抛错，但 defense-in-depth。
        try:
            sem = semaphore_for(
                _broadcast_semaphores,
                srv.id,
                max_concurrent=max_concurrent_per_server,
            )
            async with sem:
                return await fn(srv)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"广播任务异常：server_id={srv.id} reason={exc!r}"
            )
            return BroadcastOutcome(
                server=srv, ok=False, detail=str(exc) or "异常", payload=None
            )

    results = await asyncio.gather(
        *(_wrap(s) for s in servers), return_exceptions=False
    )
    return sorted(results, key=lambda o: o.server.id)


def aggregate(outcomes: Sequence[BroadcastOutcome]) -> tuple[int, int]:
    """返回 (success_count, total_count)。"""
    return sum(1 for o in outcomes if o.ok), len(outcomes)
