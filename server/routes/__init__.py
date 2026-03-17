from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import Request


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


async def read_json_data(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    payload, error_response = await read_json_object(request)
    if error_response is not None:
        return None, error_response

    assert payload is not None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, api_error(
            status_code=400,
            code="invalid_request_body",
            message="data 必须是对象",
        )

    return data, None
