from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from nonebot.log import logger

PAGE_EXPIRE_SECONDS = 600
# H-6：cache 容量上限 + LRU evict。按 100 个活跃玩家 × 10 cmd/min × 10 min ≈ 1 万峰值，
# 5000 留出 50% buffer 同时压住玩家高频 spam 内存放大面。
MAX_STORE_SIZE = 5000
# L-8：触发 over-size 告警的水位（容量的 80%），可观测性兜底。
_HIGH_WATERMARK = int(MAX_STORE_SIZE * 0.8)

# H-6：OrderedDict 维护 LRU；get / create 时 move_to_end 把热 token 放尾，
# 满容量时 popitem(last=False) 淘汰最旧条目。
_pages: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_pages_lock = threading.Lock()
_high_watermark_logged = False


def _cleanup_expired_pages() -> None:
    now = time.time()
    # H-6：created_at_ts 缺失视为已过期（fallback 不再用 now，防止外部代码注入永不过期条目）
    expired_tokens = [
        token
        for token, payload in _pages.items()
        if now - float(payload.get("created_at_ts", 0)) > PAGE_EXPIRE_SECONDS
    ]
    for token in expired_tokens:
        _pages.pop(token, None)


def _evict_if_oversize() -> None:
    """H-6：超过 MAX_STORE_SIZE 时 LRU 淘汰最旧条目。"""
    global _high_watermark_logged
    while len(_pages) > MAX_STORE_SIZE:
        # popitem(last=False) 弹出 OrderedDict 最旧（FIFO 头）条目。
        _pages.popitem(last=False)
    if len(_pages) >= _HIGH_WATERMARK and not _high_watermark_logged:
        logger.warning(
            f"页面缓存接近容量上限，已达水位线，size={len(_pages)} max={MAX_STORE_SIZE}"
        )
        _high_watermark_logged = True
    elif len(_pages) < _HIGH_WATERMARK // 2:
        # 回落足够低时重置 flag，允许下一次接近上限时再次告警
        _high_watermark_logged = False


def create_page(page_type: str, payload: dict[str, Any]) -> str:
    token = uuid.uuid4().hex
    page_payload = dict(payload)
    page_payload["type"] = page_type
    page_payload["created_at_ts"] = time.time()
    with _pages_lock:
        _cleanup_expired_pages()
        _pages[token] = page_payload
        _pages.move_to_end(token, last=True)
        _evict_if_oversize()
    return token


def get_page(token: str) -> dict[str, Any] | None:
    with _pages_lock:
        _cleanup_expired_pages()
        payload = _pages.get(token)
        if payload is not None:
            _pages.move_to_end(token, last=True)
        return payload


def get_metrics() -> dict[str, int]:
    """L-8：暴露 cache 状态供观测；调用方自行决定是否打点 / 上报。"""
    with _pages_lock:
        return {
            "size": len(_pages),
            "capacity": MAX_STORE_SIZE,
        }
