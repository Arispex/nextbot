from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from nonebot.log import logger

from nextbot.db import WAREHOUSE_CAPACITY, User, WarehouseItem, get_session
from nextbot.progression import PROGRESSION_KEY_TO_ZH, TIER_OPTIONS
from nextbot.time_utils import db_now_utc_naive
from nextbot.warehouse_lock import warehouse_lock
from server.routes import (
    api_error,
    api_success,
    client_ip as _shared_client_ip,
    read_json_object,
    user_agent as _shared_user_agent,
)

router = APIRouter()

# H-1：user_id 字符集与 webui_users.py `_USER_ID_PATTERN` 一致（5-20 位数字）。
_USER_ID_PATTERN = re.compile(r"^\d{5,20}$")

# H-4：数值字段上限，避免奇怪溢出 / 经济系统输入污染。
_ITEM_ID_MAX = 999_999
_PREFIX_ID_MAX = 999_999
_QUANTITY_MAX = 9_999
_VALUE_MAX = 1_000_000_000


# CRIT-1 / HIGH-2：thin re-export aliases；canonical helper 在 server/routes/__init__.py。
_client_ip = _shared_client_ip
_user_agent = _shared_user_agent


def _validate_user_id(user_id: str) -> JSONResponse | None:
    """H-1 / L-2：user_id 必须为 5-20 位数字，否则 400 早拒（不占锁）。"""
    value = (user_id or "").strip()
    if not value or _USER_ID_PATTERN.fullmatch(value) is None:
        return api_error(
            status_code=400,
            code="invalid_path_parameter",
            message="user_id 必须为 5-20 位数字",
            details=[
                {"field": "user_id", "message": "user_id 必须为 5-20 位数字"},
            ],
        )
    return None


@router.get("/webui/api/warehouse/tiers")
async def list_tiers(request: Request) -> JSONResponse:
    return api_success(
        data=[{"key": key, "label": zh} for key, zh in TIER_OPTIONS],
    )


@router.get("/webui/api/warehouse")
async def list_warehouse(request: Request) -> JSONResponse:
    user_id = str(request.query_params.get("user_id", "")).strip()
    if not user_id:
        return api_error(
            status_code=400,
            code="invalid_query_parameter",
            message="user_id 不能为空",
            details=[{"field": "user_id", "message": "user_id 不能为空"}],
        )
    # L-2：query 参数也走与路径参数一致的字符集校验，区分 user_id 非法 vs 用户不存在。
    if _USER_ID_PATTERN.fullmatch(user_id) is None:
        return api_error(
            status_code=400,
            code="invalid_query_parameter",
            message="user_id 必须为 5-20 位数字",
            details=[{"field": "user_id", "message": "user_id 必须为 5-20 位数字"}],
        )

    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            return api_error(
                status_code=404, code="user_not_found", message="未找到该用户",
            )
        items = (
            session.query(WarehouseItem)
            .filter(WarehouseItem.user_id == user_id)
            .order_by(WarehouseItem.slot_index.asc())
            .all()
        )
        slots = [
            {
                "slot_index": int(it.slot_index),
                "item_id": int(it.item_id),
                "prefix_id": int(it.prefix_id),
                "quantity": int(it.quantity),
                "value": int(it.value or 0),
                "min_tier": str(it.min_tier),
                "min_tier_label": PROGRESSION_KEY_TO_ZH.get(
                    str(it.min_tier), str(it.min_tier),
                ),
            }
            for it in items
        ]
    finally:
        session.close()

    return api_success(
        data={
            "user_id": user_id,
            "user_name": str(user.name),
            "capacity": WAREHOUSE_CAPACITY,
            "used": len(slots),
            "slots": slots,
        },
    )


_INT_STRING_RE = re.compile(r"-?\d+")


def _coerce_to_int(raw: Any) -> int | None:  # noqa: PLR0911
    """H-4：严格 coerce 为 int；非法返回 None。

    - `True/False` 是 bool 子类 int，语义上不是数值 → 拒绝。
    - `1.5` float 会被 `int()` 截断 → 必须先用 `is_integer()` 过滤。
    - 字符串 `"1e10"` 在 `int()` 会抛 ValueError → 由 regex 提前拦截。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or _INT_STRING_RE.fullmatch(text) is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_strict_int(
    raw: Any, field: str, *, min_value: int, max_value: int,
) -> tuple[int | None, dict[str, str] | None]:
    """H-4：严格整数校验入口；返回 (parsed, error_detail)。"""
    parsed = _coerce_to_int(raw)
    if parsed is None:
        return None, {"field": field, "message": f"{field} 必须是整数"}
    if parsed < min_value:
        return None, {"field": field, "message": f"{field} 不能小于 {min_value}"}
    if parsed > max_value:
        return None, {"field": field, "message": f"{field} 不能大于 {max_value}"}
    return parsed, None


def _validate_slot_payload(
    data: dict[str, Any],
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    details: list[dict[str, str]] = []

    item_id, err = _parse_strict_int(
        data.get("item_id"), "item_id", min_value=1, max_value=_ITEM_ID_MAX,
    )
    if err is not None:
        details.append(err)

    prefix_id, err = _parse_strict_int(
        data.get("prefix_id"), "prefix_id", min_value=0, max_value=_PREFIX_ID_MAX,
    )
    if err is not None:
        details.append(err)

    quantity, err = _parse_strict_int(
        data.get("quantity"), "quantity", min_value=1, max_value=_QUANTITY_MAX,
    )
    if err is not None:
        details.append(err)

    value, err = _parse_strict_int(
        data.get("value"), "value", min_value=0, max_value=_VALUE_MAX,
    )
    if err is not None:
        details.append(err)

    min_tier = str(data.get("min_tier", "")).strip()
    if min_tier not in PROGRESSION_KEY_TO_ZH:
        details.append({"field": "min_tier", "message": "min_tier 不在进度列表中"})

    if details:
        return None, api_error(
            status_code=422,
            code="validation_error",
            message="参数校验失败",
            details=details,
        )

    return {
        "item_id": item_id,
        "prefix_id": prefix_id,
        "quantity": quantity,
        "value": value,
        "min_tier": min_tier,
    }, None


@router.put("/webui/api/warehouse/{user_id}/{slot_index}")
async def upsert_slot(user_id: str, slot_index: int, request: Request) -> JSONResponse:
    # H-1：在拿锁 / 解析 body 之前先做 user_id 字符集 / slot 范围校验。
    path_error = _validate_user_id(user_id)
    if path_error is not None:
        return path_error
    if not (1 <= slot_index <= WAREHOUSE_CAPACITY):
        return api_error(
            status_code=400,
            code="invalid_path_parameter",
            message=f"slot_index 必须为 1-{WAREHOUSE_CAPACITY}",
        )

    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    validated, validation_error = _validate_slot_payload(data)
    if validation_error is not None:
        return validation_error
    assert validated is not None

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    # H-1：user 存在性检查保留在 lock 内（与原实现一致），_validate_user_id
    # 已在 lock 之前做字符集 / 长度校验，避免明显非法路径占用锁。
    async with warehouse_lock(user_id):
        session = get_session()
        try:
            user = session.query(User).filter(User.user_id == user_id).first()
            if user is None:
                return api_error(
                    status_code=404, code="user_not_found", message="未找到该用户",
                )
            existing = (
                session.query(WarehouseItem)
                .filter(
                    WarehouseItem.user_id == user_id,
                    WarehouseItem.slot_index == slot_index,
                )
                .first()
            )
            if existing is None:
                session.add(
                    WarehouseItem(
                        user_id=user_id,
                        slot_index=slot_index,
                        item_id=validated["item_id"],
                        prefix_id=validated["prefix_id"],
                        quantity=validated["quantity"],
                        value=validated["value"],
                        min_tier=validated["min_tier"],
                        created_at=db_now_utc_naive(),
                    )
                )
                action = "create"
            else:
                existing.item_id = validated["item_id"]
                existing.prefix_id = validated["prefix_id"]
                existing.quantity = validated["quantity"]
                existing.value = validated["value"]
                existing.min_tier = validated["min_tier"]
                action = "update"
            session.commit()
        finally:
            session.close()

    # H-2 / M-1：日志与 servers/commands 对齐 — 动作 + 对象成功 + key=value。
    logger.info(
        f"WebUI 仓库 {action} 成功：user_id={user_id} slot={slot_index} "
        f"item={validated['item_id']} qty={validated['quantity']} "
        f"value={validated['value']} tier={validated['min_tier']} "
        f"client_ip={client_ip} user_agent={user_agent!r}"
    )
    return api_success(
        data={
            "slot_index": slot_index,
            "item_id": validated["item_id"],
            "prefix_id": validated["prefix_id"],
            "quantity": validated["quantity"],
            "value": validated["value"],
            "min_tier": validated["min_tier"],
            "min_tier_label": PROGRESSION_KEY_TO_ZH[validated["min_tier"]],
        },
    )


@router.delete("/webui/api/warehouse/{user_id}/{slot_index}")
async def delete_slot(user_id: str, slot_index: int, request: Request) -> JSONResponse:
    # H-1：DELETE 与 PUT 对齐做 user_id 字符集 / slot 范围校验。
    path_error = _validate_user_id(user_id)
    if path_error is not None:
        return path_error
    if not (1 <= slot_index <= WAREHOUSE_CAPACITY):
        return api_error(
            status_code=400,
            code="invalid_path_parameter",
            message=f"slot_index 必须为 1-{WAREHOUSE_CAPACITY}",
        )

    client_ip = _client_ip(request)
    user_agent = _user_agent(request)

    async with warehouse_lock(user_id):
        session = get_session()
        try:
            existing = (
                session.query(WarehouseItem)
                .filter(
                    WarehouseItem.user_id == user_id,
                    WarehouseItem.slot_index == slot_index,
                )
                .first()
            )
            if existing is None:
                return api_error(
                    status_code=404, code="slot_empty", message="该格子为空",
                )
            session.delete(existing)
            session.commit()
        finally:
            session.close()

    logger.info(
        f"WebUI 仓库 delete 成功：user_id={user_id} slot={slot_index} "
        f"client_ip={client_ip} user_agent={user_agent!r}"
    )
    return api_success(data={"slot_index": slot_index})
