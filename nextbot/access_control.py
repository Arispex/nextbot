from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from nonebot import get_driver


def _parse_id_list(raw_value: Any) -> set[str]:
    if raw_value is None:
        return set()

    if isinstance(raw_value, (list, tuple, set)):
        return {str(item).strip() for item in raw_value if str(item).strip()}

    if isinstance(raw_value, (int, float)):
        text = str(int(raw_value)) if isinstance(raw_value, float) else str(raw_value)
        text = text.strip()
        return {text} if text else set()

    text = str(raw_value).strip()
    if not text:
        return set()

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return {str(item).strip() for item in parsed if str(item).strip()}

    return {item.strip() for item in text.split(",") if item.strip()}


def _parse_id_list_ordered(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    items: list[str] = []
    if isinstance(raw_value, (list, tuple)):
        items = [str(item).strip() for item in raw_value]
    elif isinstance(raw_value, set):
        items = [str(item).strip() for item in raw_value]
    elif isinstance(raw_value, (int, float)):
        text = str(int(raw_value)) if isinstance(raw_value, float) else str(raw_value)
        items = [text.strip()]
    else:
        text = str(raw_value).strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                items = [str(item).strip() for item in parsed]
            else:
                items = [item.strip() for item in text.split(",")]
        else:
            items = [item.strip() for item in text.split(",")]

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


# P-1.6：owner_id / group_id 来源于 .env，进程 runtime 不变。每条命令至少
# 一次 has_permission → get_owner_ids 是 hot path，原实现每次都重新 parse
# JSON / CSV，叠加 BEGIN IMMEDIATE 持写锁串行化时性能可观察。lru_cache(1)
# 让 parse 只跑一次。返回 frozenset / tuple 是不可变对象，避免调用方意外
# mutate 影响其他持有同一引用的代码路径。
@lru_cache(maxsize=1)
def get_owner_ids() -> frozenset[str]:
    config = get_driver().config
    return frozenset(_parse_id_list(getattr(config, "owner_id", None)))


@lru_cache(maxsize=1)
def get_owner_ids_ordered() -> tuple[str, ...]:
    config = get_driver().config
    return tuple(_parse_id_list_ordered(getattr(config, "owner_id", None)))


@lru_cache(maxsize=1)
def get_group_ids() -> frozenset[str]:
    config = get_driver().config
    return frozenset(_parse_id_list(getattr(config, "group_id", None)))
