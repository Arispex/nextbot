from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from nextbot.db import Server


class TShockRequestError(Exception):
    pass


@dataclass
class TShockResponse:
    http_status: int
    payload: dict[str, Any]
    api_status: str


def is_success(response: TShockResponse) -> bool:
    return response.http_status == 200 and (
        not response.api_status or response.api_status == "200"
    )


def get_error_reason(response: TShockResponse) -> str:
    error_msg = str(response.payload.get("error", "")).strip()
    if error_msg:
        return error_msg

    status_reason_map = {
        "400": "出现错误",
        "401": "未提供令牌",
        "403": "无效的令牌",
        "404": "端点不存在",
    }
    status_code = response.api_status or str(response.http_status)
    if status_code in status_reason_map:
        return status_reason_map[status_code]
    if status_code != "200":
        return f"状态码 {status_code}"
    return "返回数据格式错误"


async def request_server_api(
    server: Server,
    path: str,
    params: dict[str, str] | None = None,
    *,
    timeout: float = 5.0,
    include_token: bool = True,
) -> TShockResponse:
    request_path = path if path.startswith("/") else f"/{path}"
    # Defense-in-depth：path 段做 percent-encoding，防止 DB 脏数据 / 漏校验导致路径劫持
    safe_path = quote(request_path, safe="/")
    query = dict(params or {})
    if include_token and "token" not in query:
        query["token"] = server.token

    url = f"http://{server.ip}:{server.restapi_port}{safe_path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=query)
    except httpx.RequestError as exc:
        raise TShockRequestError from exc

    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}

    api_status = str(payload.get("status", "")).strip()
    return TShockResponse(
        http_status=response.status_code,
        payload=payload,
        api_status=api_status,
    )
