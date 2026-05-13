from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx
from nonebot.log import logger

from nextbot.db import Server


# Round 7 H-4 (I-1.2)：响应体硬上限。略大于 large_image.MAX_BASE64_BYTES=200MB，
# 给 base64 图片 / 大型世界文件 base64 payload 留 25% overhead。超出即拒绝，
# 防止恶意 / 故障 TShock 后端通过任意大 body 让 httpx 在内存里缓冲 GB 级数据导致 OOM。
MAX_RESPONSE_BYTES: int = 250 * 1024 * 1024


TShockErrorKind = Literal[
    "timeout", "unreachable", "invalid_url", "protocol", "oversize", "unknown"
]


class TShockRequestError(Exception):
    """TShock REST 调用失败。

    Round 7 I-1.4：新增 `kind` 字段让调用方按需区分 timeout / unreachable /
    invalid_url / protocol / oversize / unknown。老调用方仍可 `except TShockRequestError:`
    不读 kind 字段，向后兼容。
    """

    def __init__(self, message: str = "", *, kind: TShockErrorKind = "unknown"):
        super().__init__(message)
        self.kind: TShockErrorKind = kind


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


# Round 7 I-1.3：模块级 httpx.AsyncClient 单例。每次 request 复用 keep-alive 连接，
# 显著减少 ban_core / lottery / leaderboard 等 fan-out 调用的 TCP 握手开销。
# 生命周期由 bot.py 的 @driver.on_shutdown 钩子调用 close_shared_client() 释放
# （Utils 桶子代理负责接线；若未接线，进程退出时由 finalizer 兜底，但不保证 graceful）。
_shared_client: httpx.AsyncClient | None = None
_shared_client_lock = asyncio.Lock()


async def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    async with _shared_client_lock:
        if _shared_client is None:
            _shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
                limits=httpx.Limits(
                    max_connections=50, max_keepalive_connections=20
                ),
            )
    return _shared_client


async def close_shared_client() -> None:
    """关闭模块级 httpx 客户端。bot.py shutdown hook 调用。"""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


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

    # Round 7 I-1.1：用 httpx.URL.build 让 httpx 规范化 host（IPv6 自动加方括号、
    # 非法字符直接抛 InvalidURL），不再 f-string 拼接 server.ip。defense-in-depth：
    # server_validation._normalize_host 已校验非空 + 长度 + 无换行，本步是第二道防线。
    try:
        url = httpx.URL(
            scheme="http",
            host=str(server.ip).strip(),
            port=int(server.restapi_port),
            path=safe_path,
        )
    except (httpx.InvalidURL, ValueError, TypeError) as exc:
        raise TShockRequestError(
            f"非法的服务器地址或端口：{exc}", kind="invalid_url"
        ) from exc

    client = await _get_shared_client()

    # Round 7 H-4 (I-1.2)：改 stream 模式 + chunk 累加 cap。常规小响应几乎无 perf 损失，
    # 大响应（恶意 / 故障后端塞 GB 级 body）在 250MB 处立即截断，httpx 不会先把全量字节读完。
    try:
        async with client.stream(
            "GET", url, params=query, timeout=effective_timeout
        ) as response:
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise TShockRequestError(
                        f"响应体过大（超过 {MAX_RESPONSE_BYTES} 字节）",
                        kind="oversize",
                    )
            status_code = response.status_code
        body = bytes(chunks)
    except TShockRequestError:
        # oversize 路径自己已设置 kind，直接重抛
        raise
    except httpx.TimeoutException as exc:
        raise TShockRequestError(str(exc), kind="timeout") from exc
    except httpx.ConnectError as exc:
        raise TShockRequestError(str(exc), kind="unreachable") from exc
    except httpx.InvalidURL as exc:
        raise TShockRequestError(str(exc), kind="invalid_url") from exc
    except httpx.RemoteProtocolError as exc:
        raise TShockRequestError(str(exc), kind="protocol") from exc
    except httpx.RequestError as exc:
        raise TShockRequestError(str(exc), kind="unknown") from exc

    # Round 7 I-1.5：非 JSON 响应静默兜底改为带诊断日志，方便排障
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        logger.warning(
            f"TShock 响应非 JSON：server_id={server.id} "
            f"server_ip={server.ip} content_length={len(body)} status={status_code}"
        )
        payload = {}

    if not isinstance(payload, dict):
        # JSON 顶层不是 object 时按空 dict 处理，避免下游 .get 报错
        payload = {}

    api_status = str(payload.get("status", "")).strip()
    return TShockResponse(
        http_status=status_code,
        payload=payload,
        api_status=api_status,
    )
