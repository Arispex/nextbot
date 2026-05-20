"""统一 sync orchestrator。

替代历史上散落在 user_manager / ban_core / webui_users 各处的细粒度
「单条白名单 / 黑名单 / TShock 账号 / 改密 / 改名」HTTP fan-out。
所有 caller 写完 DB 之后调一次 `trigger_sync_all_servers(...)`，由本模块统一
向每台 NextBotAdapter 服务器 `GET /nextbot/sync`，让插件端 pull 主库快照并
apply 差异。

关键能力：
- debounce 500ms + future coalescing（leading-edge + trailing-edge）
- per-server 并行 fan-out（与 server_broadcast 同模式但简化）
- 400 "Sync is already in progress" 软重试（最多 3 次，间隔 1s）
- 统一 SyncOutcome 数据结构 + 用户可见文案 helper
- 机器搜索风格 console 日志（key=value）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from nonebot.log import logger

from nextbot.db import Server, get_session
from nextbot.tshock_api import (
    TShockRequestError,
    TShockResponse,
    request_server_api,
)

# ---- 公开数据结构 ----

SyncStatusLiteral = str
# 取值：
# - "ok"              syncStatus=Ok，apply 了变更
# - "not_modified"    syncStatus=NotModified，主库快照与上次相同
# - "skipped"         syncStatus=Skipped，同步功能配置为跳过
# - "busy"            HTTP 400 Sync is already in progress（重试 3 次仍 busy）
# - "unauthorized"    syncStatus=Unauthorized
# - "unreachable"     syncStatus=Unreachable 或 transport 层失败
# - "disabled"        syncStatus=Disabled
# - "error"           其它未识别异常


@dataclass(frozen=True)
class SyncOutcome:
    server_id: int
    server_name: str
    ok: bool
    status: SyncStatusLiteral
    detail: str  # 失败时为面向用户的原因；成功时可空
    raw_payload: dict[str, Any] = field(default_factory=dict)


# ---- debounce 状态（模块级 + asyncio.Lock 保护）----

_lock: asyncio.Lock = asyncio.Lock()
_window_open: bool = False
_pending_future: asyncio.Future[list[SyncOutcome]] | None = None
_pending_callers: list[str] = []

_DEBOUNCE_WINDOW_SECONDS: float = 0.5
_BUSY_RETRY_DELAY_SECONDS: float = 1.0
_BUSY_RETRY_MAX_ATTEMPTS: int = 3
_BUSY_ERROR_FRAGMENT: str = "sync is already in progress"
_HTTP_BUSY_STATUS: int = 400

# 防止 _close_window_after_delay() task 被 GC 提前回收（asyncio 仅 weakref 持有 task）
_background_tasks: set[asyncio.Task[None]] = set()


# ---- 公开 API ----


async def trigger_sync_all_servers(caller: str = "unknown") -> list[SyncOutcome]:
    """触发一次全服 sync。500ms 内连续调用会被合并。

    语义：
    - 窗口外的第一次调用 → 立刻 fan-out（leading-edge），同步等待结果并返回。
      同时打开 500ms 窗口。
    - 窗口内的后续调用 → 不立刻 sync，等窗口关闭时一次合并 sync（trailing-edge）。
      所有窗口内调用 await 同一 future，拿同一组 outcomes。

    Returns:
        per-server SyncOutcome 列表，按 server.id 升序。空列表表示 DB 中无 server。
    """
    global _window_open, _pending_future  # noqa: PLW0603 - 模块级 debounce 状态

    async with _lock:
        if not _window_open:
            _window_open = True
            # 异步开关窗口；trailing sync 在 _close_window_after_delay 内部执行
            task = asyncio.create_task(_close_window_after_delay())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            do_leading = True
            waiter: asyncio.Future[list[SyncOutcome]] | None = None
        else:
            do_leading = False
            if _pending_future is None or _pending_future.done():
                loop = asyncio.get_running_loop()
                _pending_future = loop.create_future()
                _pending_callers.clear()
            _pending_callers.append(caller)
            waiter = _pending_future

    if do_leading:
        return await _do_full_sync(caller=caller)

    assert waiter is not None
    return await waiter


def format_sync_outcomes_for_user(outcomes: list[SyncOutcome]) -> str:
    """渲染 per-server 用户可见多行文案。

    格式与命令端 / WebUI 前端 toast 一致：
        同步服务器结果：
        <id>.<name>：同步成功
        <id>.<name>：同步成功，无需同步
        <id>.<name>：同步失败，<原因>
    """
    if not outcomes:
        return "同步成功，暂无服务器"

    lines = ["同步服务器结果："]
    for o in outcomes:
        if o.ok:
            if o.status == "skipped":
                lines.append(f"{o.server_id}.{o.server_name}：同步成功，无需同步")
            else:
                lines.append(f"{o.server_id}.{o.server_name}：同步成功")
        else:
            reason = o.detail or "未知错误"
            lines.append(f"{o.server_id}.{o.server_name}：同步失败，{reason}")
    return "\n".join(lines)


# ---- 内部：窗口关闭 + trailing sync ----


async def _close_window_after_delay() -> None:
    """500ms 后关闭窗口；若期间有 trailing caller 排队，执行一次合并 sync。"""
    global _window_open, _pending_future  # noqa: PLW0603 - 模块级 debounce 状态
    await asyncio.sleep(_DEBOUNCE_WINDOW_SECONDS)

    async with _lock:
        _window_open = False
        future_to_resolve = _pending_future
        callers_snapshot = list(_pending_callers)
        _pending_future = None
        _pending_callers.clear()

    if future_to_resolve is None or future_to_resolve.done():
        return

    # trailing sync —— _do_full_sync 内部已 catch 全部异常并降级为 outcomes，
    # 但 cancel / KeyboardInterrupt 仍可能抛到这里。defense-in-depth：把异常传播
    # 到 future 让所有 awaiter 都能看到（否则会卡死）。
    caller_label = (
        f"trailing({','.join(callers_snapshot)})" if callers_snapshot else "trailing"
    )
    try:
        outcomes = await _do_full_sync(caller=caller_label)
    except BaseException as exc:
        if not future_to_resolve.done():
            future_to_resolve.set_exception(exc)
        raise
    if not future_to_resolve.done():
        future_to_resolve.set_result(outcomes)


# ---- 内部：fan-out + per-server 请求 ----


def _load_servers() -> list[Server]:
    session = get_session()
    try:
        return session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()


async def _do_full_sync(caller: str) -> list[SyncOutcome]:
    """触发一次完整 fan-out，永不抛异常（异常降级为单条 outcome）。"""
    try:
        servers = _load_servers()
    except Exception as exc:  # noqa: BLE001 — DB 异常降级
        logger.exception(f"sync 加载服务器失败：caller={caller} reason={exc!r}")
        return []

    logger.info(f"sync 触发：caller={caller} server_count={len(servers)}")

    if not servers:
        logger.info("sync 聚合：success=0/0")
        return []

    raw_results = await asyncio.gather(
        *(_sync_one_server(server) for server in servers),
        return_exceptions=True,
    )

    outcomes: list[SyncOutcome] = []
    for server, raw in zip(servers, raw_results, strict=True):
        if isinstance(raw, BaseException):
            logger.warning(
                f"sync 服务器异常：server_id={server.id} reason={raw!r}"
            )
            outcomes.append(
                SyncOutcome(
                    server_id=int(server.id),
                    server_name=str(server.name),
                    ok=False,
                    status="error",
                    detail=str(raw) or "同步异常",
                    raw_payload={},
                )
            )
        else:
            outcomes.append(raw)

    outcomes.sort(key=lambda o: o.server_id)

    for o in outcomes:
        wl = o.raw_payload.get("whitelist") if isinstance(o.raw_payload, dict) else None
        bl = o.raw_payload.get("blacklist") if isinstance(o.raw_payload, dict) else None
        ph = (
            o.raw_payload.get("passwordHash")
            if isinstance(o.raw_payload, dict)
            else None
        )
        logger.info(
            "sync 服务器结果："
            f"server_id={o.server_id} name={o.server_name} status={o.status} "
            f"whitelist_added={_count(wl, 'added')} "
            f"whitelist_removed={_count(wl, 'removed')} "
            f"whitelist_skipped={_count(wl, 'skipped')} "
            f"blacklist_added={_count(bl, 'added')} "
            f"blacklist_removed={_count(bl, 'removed')} "
            f"blacklist_skipped={_count(bl, 'skipped')} "
            f"password_updated={_count(ph, 'updated')} "
            f"password_created={_count(ph, 'created')} "
            f"password_unchanged={_count(ph, 'unchanged')} "
            f"password_skipped={_count(ph, 'skipped')} "
            f"password_failed={_count(ph, 'failed')}"
        )

    success_count = sum(1 for o in outcomes if o.ok)
    logger.info(f"sync 聚合：success={success_count}/{len(outcomes)}")
    return outcomes


def _count(section: Any, key: str) -> int:
    """从 sync 响应的某个 sub-section（whitelist / blacklist / passwordHash）取 count。

    section 可能是：
    - dict[str, int]    直接 .get(key, 0)
    - dict[str, list]   .get(key) 后 len(...)
    - None / 其它       一律按 0
    """
    if not isinstance(section, dict):
        return 0
    raw = section.get(key)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, list):
        return len(raw)
    return 0


async def _sync_one_server(server: Server) -> SyncOutcome:
    """单台 server fan-out，处理 400 busy 重试。永不抛异常（异常都转为 outcome）。"""
    last_payload: dict[str, Any] = {}

    for attempt in range(_BUSY_RETRY_MAX_ATTEMPTS):
        try:
            response = await request_server_api(server, "/nextbot/sync")
        except TShockRequestError as exc:
            # 上游 transport 层失败：unreachable / timeout / invalid_url 等
            return SyncOutcome(
                server_id=int(server.id),
                server_name=str(server.name),
                ok=False,
                status="unreachable",
                detail=str(exc) or "无法连接服务器",
                raw_payload={},
            )

        last_payload = (
            response.payload if isinstance(response.payload, dict) else {}
        )

        if _is_busy_response(response):
            if attempt < _BUSY_RETRY_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_BUSY_RETRY_DELAY_SECONDS)
                continue
            return SyncOutcome(
                server_id=int(server.id),
                server_name=str(server.name),
                ok=False,
                status="busy",
                detail="同步繁忙",
                raw_payload=last_payload,
            )

        return _parse_sync_response(server, response)

    # 兜底：理论上不会走到这里（循环内必然 return），但为类型完整性保留
    return SyncOutcome(
        server_id=int(server.id),
        server_name=str(server.name),
        ok=False,
        status="busy",
        detail="同步繁忙",
        raw_payload=last_payload,
    )


def _is_busy_response(response: TShockResponse) -> bool:
    """识别 400 busy 响应。

    插件端 contract：HTTP 400 + payload.error 含 "Sync is already in progress"。
    """
    if response.http_status != _HTTP_BUSY_STATUS:
        return False
    payload = response.payload if isinstance(response.payload, dict) else {}
    error = str(payload.get("error", "")).strip().lower()
    return _BUSY_ERROR_FRAGMENT in error


def _parse_sync_response(  # noqa: PLR0911 - 6 个 syncStatus 各自构造 SyncOutcome
    server: Server, response: TShockResponse
) -> SyncOutcome:
    """解析非 busy 的 200/4xx 响应。

    插件端 contract：
        {syncStatus, message, httpStatus, whitelist, blacklist, passwordHash}
    syncStatus 取值：Ok / NotModified / Skipped / Unauthorized / Unreachable / Disabled
    """
    payload = response.payload if isinstance(response.payload, dict) else {}
    raw_status = str(payload.get("syncStatus", "")).strip()
    message = str(payload.get("message", "")).strip()
    normalized = raw_status.lower()

    if normalized == "ok":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=True,
            status="ok",
            detail="",
            raw_payload=payload,
        )
    if normalized == "notmodified":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=True,
            status="not_modified",
            detail="",
            raw_payload=payload,
        )
    if normalized == "skipped":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=True,
            status="skipped",
            detail=message,
            raw_payload=payload,
        )
    if normalized == "unauthorized":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=False,
            status="unauthorized",
            detail=message or "未授权",
            raw_payload=payload,
        )
    if normalized == "unreachable":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=False,
            status="unreachable",
            detail=message or "无法连接服务器",
            raw_payload=payload,
        )
    if normalized == "disabled":
        return SyncOutcome(
            server_id=int(server.id),
            server_name=str(server.name),
            ok=False,
            status="disabled",
            detail=message or "同步功能已禁用",
            raw_payload=payload,
        )

    # 未识别的 syncStatus 或 HTTP 非 200 / 非 400 busy 响应
    error_msg = str(payload.get("error", "")).strip()
    fallback_detail = (
        message
        or error_msg
        or (raw_status or f"HTTP {response.http_status}")
    )
    return SyncOutcome(
        server_id=int(server.id),
        server_name=str(server.name),
        ok=False,
        status="error",
        detail=fallback_detail,
        raw_payload=payload,
    )
