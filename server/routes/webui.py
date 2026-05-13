from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, FastAPI, Request
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from nonebot.log import logger

from server.pages.console_page import (
    render_console_page,
    render_groups_page,
    render_login_page,
    render_servers_page,
    render_lottery_page,
    render_shop_page,
    render_users_page,
    render_warehouse_page,
)
from server.routes import api_error, api_success, read_json_object
from server.server_config import WebServerSettings

router = APIRouter()

_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
WEBUI_STATIC_DIR = Path(__file__).resolve().parent.parent / "webui" / "static"

# H-A2：服务端 session store，存当前活跃 jti
# 进程重启所有 session 失效（接受 trade-off：token 永久，用户可重新登入）
_active_sessions_lock = threading.Lock()
_active_sessions: set[str] = set()

# H-A3：登入失败 brute-force 滑动窗口
_FAILED_LOGIN_WINDOW_SEC = 300
_FAILED_LOGIN_MAX_ATTEMPTS = 5
_failed_login_lock = threading.Lock()
_failed_login_history: dict[str, deque[float]] = {}


def _sanitize_next_path(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return "/webui"
    if not candidate.startswith("/"):
        return "/webui"
    if candidate.startswith("//"):
        return "/webui"
    return candidate


def _sign_payload(payload: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest


def _build_session_cookie(secret: str) -> tuple[str, str]:
    """构建 cookie，返回 (encoded_cookie_value, jti)。

    H-A2：payload 加入 jti（uuid4 hex）作为服务端 session store 的 key，使 DELETE
    端点可以真正使会话失效；进程重启则全部失效。
    """
    issued_at = str(int(time.time()))
    jti = uuid.uuid4().hex
    payload = f"{issued_at}.{jti}"
    signature = _sign_payload(payload, secret)
    raw = f"{payload}.{signature}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return encoded, jti


def _decode_session_cookie(cookie_value: str) -> tuple[str, str, str] | None:
    """解析 cookie，返回 (issued_at, jti, signature) 或 None"""
    if not cookie_value:
        return None
    padding = "=" * ((4 - len(cookie_value) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode((cookie_value + padding).encode("ascii"))
    except Exception:
        return None
    decoded = raw.decode("utf-8", errors="ignore")
    parts = decoded.split(".", maxsplit=2)
    if len(parts) != 3:
        return None
    issued_at_text, jti, signature = parts
    if not issued_at_text or not jti or not signature:
        return None
    return issued_at_text, jti, signature


def _verify_session_cookie(cookie_value: str, secret: str) -> bool:
    parsed = _decode_session_cookie(cookie_value)
    if parsed is None:
        return False

    issued_at_text, jti, provided_signature = parsed
    if not issued_at_text.isdigit():
        return False

    expected_signature = _sign_payload(f"{issued_at_text}.{jti}", secret)
    if not hmac.compare_digest(provided_signature, expected_signature):
        return False

    issued_at = int(issued_at_text)
    if int(time.time()) - issued_at > _SESSION_TTL_SECONDS:
        return False

    # H-A2：必须在服务端 session store 中存在；DELETE 后 jti 被 discard
    with _active_sessions_lock:
        if jti not in _active_sessions:
            return False
    return True


def _is_authenticated(request: Request, settings: WebServerSettings) -> bool:
    cookie_value = request.cookies.get(settings.cookie_name, "")
    if cookie_value and _verify_session_cookie(cookie_value, settings.session_secret):
        return True
    query_token = request.query_params.get("token", "").strip()
    return bool(
        query_token and hmac.compare_digest(query_token, settings.webui_token)
    )


def _set_session_cookie(response: Response, settings: WebServerSettings) -> None:
    encoded, jti = _build_session_cookie(settings.session_secret)
    with _active_sessions_lock:
        _active_sessions.add(jti)
    response.set_cookie(
        key=settings.cookie_name,
        value=encoded,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=_SESSION_TTL_SECONDS,
    )


def _client_ip(request: Request) -> str:
    """H-A3 / M-A4：从 X-Forwarded-For 或 client.host 取调用方 IP"""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        # 取链路最左侧（最原始客户端）
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _check_login_rate_limit(client_ip: str) -> tuple[bool, int]:
    """H-A3：检查登入失败次数，返回 (allowed, retry_after_seconds)"""
    now = time.monotonic()
    with _failed_login_lock:
        history = _failed_login_history.get(client_ip)
        if history is None:
            return True, 0
        # 清理过期记录
        while history and now - history[0] > _FAILED_LOGIN_WINDOW_SEC:
            history.popleft()
        if not history:
            _failed_login_history.pop(client_ip, None)
            return True, 0
        if len(history) >= _FAILED_LOGIN_MAX_ATTEMPTS:
            retry_after = int(_FAILED_LOGIN_WINDOW_SEC - (now - history[0])) + 1
            return False, max(retry_after, 1)
        return True, 0


def _record_login_failure(client_ip: str) -> None:
    """H-A3：登入失败时追加时间戳到滑动窗口"""
    now = time.monotonic()
    with _failed_login_lock:
        history = _failed_login_history.setdefault(client_ip, deque())
        history.append(now)


def _reset_login_failures(client_ip: str) -> None:
    """H-A3：登入成功时清空该 IP 失败计数"""
    with _failed_login_lock:
        _failed_login_history.pop(client_ip, None)


def add_webui_auth_middleware(app: FastAPI, settings: WebServerSettings) -> None:
    @app.middleware("http")
    async def _webui_auth_middleware(request: Request, call_next):
        path = request.url.path
        is_webui_auth_free_path = (
            path.startswith("/webui/login")
            or path.startswith("/webui/api/session")
            or path.startswith("/webui/static/")
        )
        if path.startswith("/webui") and not is_webui_auth_free_path:
            if not _is_authenticated(request, settings):
                next_path = path
                if request.url.query:
                    next_path = f"{next_path}?{request.url.query}"
                login_url = "/webui/login?" + urlencode({"next": next_path})
                return RedirectResponse(url=login_url, status_code=302)
        return await call_next(request)


# M-A3：webui 路径注入安全响应头
# - X-Frame-Options: DENY → 防 clickjacking（login 页面禁止被 iframe 嵌入）
# - X-Content-Type-Options: nosniff → 防 MIME sniff XSS
# - Referrer-Policy: strict-origin-when-cross-origin → 跨站跳转不泄漏完整 URL
# - CSP: 暂保 'unsafe-inline' 兼容现有内联 script / style；frame-ancestors 'none' 等价 X-Frame-Options: DENY
_WEBUI_SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "font-src 'self' data:; "
        "frame-ancestors 'none'"
    ),
}


def add_security_headers_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        # 仅对 webui 路径注入；render / 内部 API 暂不改动，避免影响截图与 broadcast
        if request.url.path.startswith("/webui"):
            for header_name, header_value in _WEBUI_SECURITY_HEADERS.items():
                response.headers[header_name] = header_value
        return response


def _resolve_webui_static_file(file_path: str) -> Path:
    resolved_path = (WEBUI_STATIC_DIR / file_path).resolve()
    try:
        resolved_path.relative_to(WEBUI_STATIC_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="forbidden") from exc
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return resolved_path


def _get_settings_from_request(request: Request) -> WebServerSettings:
    return request.app.state.server_settings


@router.get("/webui", response_class=HTMLResponse)
async def webui_index(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_console_page())


@router.get("/webui/servers", response_class=HTMLResponse)
async def webui_servers_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_servers_page())


@router.get("/webui/users", response_class=HTMLResponse)
async def webui_users_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_users_page())


@router.get("/webui/groups", response_class=HTMLResponse)
async def webui_groups_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_groups_page())


@router.get("/webui/warehouse", response_class=HTMLResponse)
async def webui_warehouse_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_warehouse_page())


@router.get("/webui/shop", response_class=HTMLResponse)
async def webui_shop_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_shop_page())


@router.get("/webui/lottery", response_class=HTMLResponse)
async def webui_lottery_page(request: Request) -> HTMLResponse:
    return HTMLResponse(content=render_lottery_page())


@router.get("/webui/static/{file_path:path}")
async def webui_static(file_path: str) -> FileResponse:
    return FileResponse(path=_resolve_webui_static_file(file_path))


@router.get("/webui/login", response_class=HTMLResponse)
async def webui_login_page(request: Request) -> Response:
    settings = _get_settings_from_request(request)
    next_path = _sanitize_next_path(request.query_params.get("next"))
    if _is_authenticated(request, settings):
        return RedirectResponse(url=next_path, status_code=302)
    return HTMLResponse(content=render_login_page(next_path=next_path))


@router.post("/webui/api/session")
async def webui_session_create(request: Request) -> Response:
    settings = _get_settings_from_request(request)
    client_ip = _client_ip(request)
    user_agent = request.headers.get("user-agent", "")[:200]

    # H-A3：登入失败 brute-force 速率限制
    allowed, retry_after = _check_login_rate_limit(client_ip)
    if not allowed:
        logger.warning(
            f"创建登录会话被限速：reason=登录失败次数过多 "
            f"client_ip={client_ip} user_agent={user_agent!r} retry_after={retry_after}"
        )
        return api_error(
            status_code=429,
            code="too_many_requests",
            message="登录失败次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    data, error_response = await read_json_object(request)
    if error_response is not None:
        return error_response
    assert data is not None

    provided_token = str(data.get("token", "")).strip()
    next_path = _sanitize_next_path(str(data.get("next", "")))

    if not provided_token:
        # M-A4：登入失败日志补 client_ip / user_agent
        logger.warning(
            f"创建登录会话失败：reason=Token 不能为空 "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=422,
            code="validation_error",
            message="Token 不能为空",
            details=[{"field": "token", "message": "Token 不能为空"}],
        )

    if not hmac.compare_digest(provided_token, settings.webui_token):
        # H-A3：记录失败次数；M-A4：日志补 client_ip / user_agent
        _record_login_failure(client_ip)
        logger.warning(
            f"创建登录会话失败：reason=Token 错误 "
            f"client_ip={client_ip} user_agent={user_agent!r}"
        )
        return api_error(
            status_code=401,
            code="unauthorized",
            message="Token 错误",
        )

    # H-A3：登入成功，清空该 IP 失败计数避免误伤
    _reset_login_failures(client_ip)

    response = api_success(
        status_code=201,
        data={"next": next_path},
        headers={"Location": "/webui/api/session"},
    )
    _set_session_cookie(response, settings)
    logger.info(f"创建登录会话成功：client_ip={client_ip}")
    return response


@router.delete("/webui/api/session")
async def webui_session_delete(request: Request) -> Response:
    settings = _get_settings_from_request(request)
    # H-A2：解析 cookie 拿到 jti 后从服务端 session store 移除，使该 cookie 立即失效
    cookie_value = request.cookies.get(settings.cookie_name, "")
    parsed = _decode_session_cookie(cookie_value)
    if parsed is not None:
        _, jti, _ = parsed
        with _active_sessions_lock:
            _active_sessions.discard(jti)
    response = Response(status_code=204)
    response.delete_cookie(key=settings.cookie_name, path="/")
    logger.info("删除登录会话成功")
    return response
