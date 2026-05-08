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
    timeout: float | httpx.Timeout = 5.0,
    include_token: bool = True,
) -> TShockResponse:
    request_path = path if path.startswith("/") else f"/{path}"
    # Defense-in-depth：path 段做 percent-encoding，防止 DB 脏数据 / 漏校验导致路径劫持
    safe_path = quote(request_path, safe="/")
    query = dict(params or {})
    if include_token and "token" not in query:
        query["token"] = server.token

    # 当传入 float 时，把它当作 read 超时（最常见的瓶颈），其他维度使用合理默认；
    # 这样调用方可以简单传 `timeout=300.0` 让大对象下载不被 connect/write 默认值卡死
    if isinstance(timeout, httpx.Timeout):
        effective_timeout: httpx.Timeout = timeout
    else:
        effective_timeout = httpx.Timeout(
            connect=5.0,
            read=float(timeout),
            write=10.0,
            pool=5.0,
        )

    url = f"http://{server.ip}:{server.restapi_port}{safe_path}"
    try:
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
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
