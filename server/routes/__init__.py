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
# LOW-10：硬上限，caller 即使误传 max_per_page=10000 也会被钳到此值。
HARD_MAX_PER_PAGE = 1000
# HIGH-3 / LOW-21：JSON body 默认大小上限，webui 写入端足够；超出直接 413。
# 商店 / 抽奖导入端点通过 read_json_object(max_bytes=None) 单独豁免。
MAX_JSON_BODY_BYTES = 256 * 1024  # 256 KiB
# LOW-11：_parse_positive_int 拒绝过长输入文本，规避 Python <3.11 int() 超大字符串 CPU 攻击。
_MAX_INT_TEXT_LENGTH = 32


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
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if details:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=headers,
    )


def client_ip(request: "Request") -> str:
    """CRIT-1 / HIGH-2：取调用方 IP。

    仅当直接连接 IP 命中 `WebServerSettings.trusted_proxies` 时，才信任
    `X-Forwarded-For`；否则一律返回 `request.client.host`。裸部署（默认空
    trusted_proxies）即 fail-closed，攻击者无法通过伪造 XFF 头旁路基于 IP 的限速 /
    日志归属。

    受信代理场景下取 XFF 的**最后一段**：因为下游可信链路追加自己看到的远端 IP
    到 XFF 末尾，最后一段才是 nextbot 上游代理观察到的真实地址。
    """
    direct_host = request.client.host if request.client is not None else ""
    settings = getattr(request.app.state, "server_settings", None)
    trusted = getattr(settings, "trusted_proxies", frozenset()) if settings else frozenset()

    if direct_host and trusted and direct_host in trusted:
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            last = forwarded.rsplit(",", 1)[-1].strip()
            if last:
                return last

    return direct_host or "unknown"


def user_agent(request: "Request") -> str:
    """HIGH-2：截断 User-Agent 防超长，配合日志注入防御。"""
    return request.headers.get("user-agent", "")[:200]


async def read_json_object(
    request: "Request",
    *,
    max_bytes: int | None = MAX_JSON_BODY_BYTES,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """读取并校验 JSON 请求体，返回 ``(payload, error_response)``。

    ``max_bytes`` 控制请求体字节上限，两道校验（Content-Length 预检 + 流式逐块累加）
    都遵循它：

    - 默认 ``MAX_JSON_BODY_BYTES``（256 KiB），超限返回 413 ``payload_too_large``。
    - 传 ``None`` 表示**不限制**字节，仅供商店 / 抽奖「导入配置文件」端点使用——
      导入的是 admin 自己导出的整份配置，天然可能超过 256 KiB。其余端点一律保持默认。

    字节上限之外的校验（Content-Type 软校验、JSON 解析、dict 结构校验）不受该参数影响。
    """
    # HIGH-3：Content-Length 预检；超过上限直接 413，避免 Starlette 缓存大 body 进内存。
    content_length_raw = request.headers.get("content-length", "")
    if (
        max_bytes is not None
        and content_length_raw.isdigit()
        and int(content_length_raw) > max_bytes
    ):
        return None, api_error(
            status_code=413,
            code="payload_too_large",
            message="请求体过大",
        )

    # HIGH-3：Content-Type 软校验；仅允许 application/json（含 ;charset=utf-8 等参数）。
    content_type = request.headers.get("content-type", "").strip().lower()
    if content_type and content_type.split(";", 1)[0].strip() != "application/json":
        return None, api_error(
            status_code=415,
            code="unsupported_media_type",
            message="请求体必须是 application/json",
        )

    # HIGH-3：流式读 body，逐块校验大小，避免 Content-Length 缺失时仍 OOM。
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if max_bytes is not None and len(body) > max_bytes:
            return None, api_error(
                status_code=413,
                code="payload_too_large",
                message="请求体过大",
            )

    try:
        payload: Any = json.loads(bytes(body)) if body else None
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

    # LOW-11：过长十进制字符串走 int() 在 Python <3.11 上是 CPU-bound，提前拒绝。
    if len(text) > _MAX_INT_TEXT_LENGTH:
        message = f"{field} 必须是整数"
        return None, api_error(
            status_code=400,
            code="invalid_query_parameter",
            message=message,
            details=[{"field": field, "message": message}],
        )

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
    request: "Request",
    *,
    default_page: int = DEFAULT_PAGE,
    default_per_page: int = DEFAULT_PER_PAGE,
    max_per_page: int = MAX_PER_PAGE,
    allow_zero_per_page: bool = False,
) -> tuple[dict[str, int] | None, JSONResponse | None]:
    # LOW-10：caller 即使误传超大 max_per_page 也被钳到 HARD_MAX_PER_PAGE。
    ceiling = min(int(max_per_page), HARD_MAX_PER_PAGE)

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
        min_value=0 if allow_zero_per_page else 1,
        max_value=ceiling,
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
