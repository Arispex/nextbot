from __future__ import annotations

import json
from math import ceil
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import Request

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def api_success(
    *,
    data: Any,
    status_code: int = 200,
    meta: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"data": data}
    if meta is not None:
        content["meta"] = meta
    return JSONResponse(status_code=status_code, content=content, headers=headers)


def api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


async def read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        payload: Any = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, api_error(
            status_code=400,
            code="invalid_json",
            message="请求体必须是 JSON",
        )

    if not isinstance(payload, dict):
        return None, api_error(
            status_code=400,
            code="invalid_request_body",
            message="请求体必须是对象",
        )

    return payload, None


def _parse_positive_int(
    raw_value: str | None,
    *,
    field: str,
    default_value: int,
    min_value: int = 1,
    max_value: int | None = None,
) -> tuple[int | None, JSONResponse | None]:
    text = str(raw_value or "").strip()
    if not text:
        return default_value, None

    try:
        value = int(text)
    except ValueError:
        message = f"{field} 必须是整数"
        return None, api_error(
            status_code=400,
            code="invalid_query_parameter",
            message=message,
            details=[{"field": field, "message": message}],
        )

    if value < min_value:
        message = f"{field} 必须大于等于 {min_value}"
        return None, api_error(
            status_code=400,
            code="invalid_query_parameter",
            message=message,
            details=[{"field": field, "message": message}],
        )

    if max_value is not None and value > max_value:
        message = f"{field} 必须小于等于 {max_value}"
        return None, api_error(
            status_code=400,
            code="invalid_query_parameter",
            message=message,
            details=[{"field": field, "message": message}],
        )

    return value, None


def read_pagination_query(
    request: Request,
    *,
    default_page: int = DEFAULT_PAGE,
    default_per_page: int = DEFAULT_PER_PAGE,
    max_per_page: int = MAX_PER_PAGE,
) -> tuple[dict[str, int] | None, JSONResponse | None]:
    page, page_error = _parse_positive_int(
        request.query_params.get("page"),
        field="page",
        default_value=default_page,
    )
    if page_error is not None:
        return None, page_error

    per_page, per_page_error = _parse_positive_int(
        request.query_params.get("per_page"),
        field="per_page",
        default_value=default_per_page,
        max_value=max_per_page,
    )
    if per_page_error is not None:
        return None, per_page_error

    assert page is not None
    assert per_page is not None
    return {"page": page, "per_page": per_page}, None


def build_pagination_meta(*, total: int, page: int, per_page: int) -> dict[str, int]:
    total_value = max(int(total), 0)
    per_page_value = max(int(per_page), 1)
    total_pages = ceil(total_value / per_page_value) if total_value > 0 else 0
    current_page = min(max(int(page), 1), total_pages) if total_pages > 0 else 1
    return {
        "total": total_value,
        "page": current_page,
        "per_page": per_page_value,
        "total_pages": total_pages,
    }


def build_pagination_slice(*, total: int, page: int, per_page: int) -> tuple[dict[str, int], int, int]:
    meta = build_pagination_meta(total=total, page=page, per_page=per_page)
    offset = (meta["page"] - 1) * meta["per_page"] if meta["total_pages"] > 0 else 0
    return meta, offset, meta["per_page"]
