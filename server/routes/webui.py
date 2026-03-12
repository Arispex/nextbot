from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, FastAPI, Request
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from server.pages.console_page import (
    render_console_page,
    render_groups_page,
    render_login_page,
    render_servers_page,
    render_users_page,
)
from server.server_config import WebServerSettings

router = APIRouter()

_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
WEBUI_STATIC_DIR = Path(__file__).resolve().parent.parent / "webui" / "static"


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


def _build_session_cookie(secret: str) -> str:
    issued_at = str(int(time.time()))
    signature = _sign_payload(issued_at, secret)
    raw = f"{issued_at}.{signature}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return encoded


def _decode_session_cookie(cookie_value: str) -> str | None:
    if not cookie_value:
        return None
    padding = "=" * ((4 - len(cookie_value) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode((cookie_value + padding).encode("ascii"))
    except Exception:
        return None
    decoded = raw.decode("utf-8", errors="ignore")
    return decoded if "." in decoded else None


def _verify_session_cookie(cookie_value: str, secret: str) -> bool:
    decoded = _decode_session_cookie(cookie_value)
    if decoded is None:
        return False

    issued_at_text, provided_signature = decoded.split(".", maxsplit=1)
    if not issued_at_text.isdigit():
        return False

    expected_signature = _sign_payload(issued_at_text, secret)
    if not hmac.compare_digest(provided_signature, expected_signature):
        return False

    issued_at = int(issued_at_text)
    if int(time.time()) - issued_at > _SESSION_TTL_SECONDS:
        return False
    return True


def _is_authenticated(request: Request, settings: WebServerSettings) -> bool:
    cookie_value = request.cookies.get(settings.cookie_name, "")
    if not cookie_value:
        return False
    return _verify_session_cookie(cookie_value, settings.session_secret)


def add_webui_auth_middleware(app: FastAPI, settings: WebServerSettings) -> None:
    @app.middleware("http")
    async def _webui_auth_middleware(request: Request, call_next):
        path = request.url.path
        is_webui_auth_free_path = (
            path.startswith("/webui/login")
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


@router.post("/webui/login", response_class=HTMLResponse)
async def webui_login_submit(request: Request) -> Response:
    settings = _get_settings_from_request(request)
    raw_body = (await request.body()).decode("utf-8", errors="ignore")
    form_data = parse_qs(raw_body, keep_blank_values=True)

    provided_token = form_data.get("token", [""])[0].strip()
    next_path = _sanitize_next_path(form_data.get("next", [""])[0])

    if not hmac.compare_digest(provided_token, settings.webui_token):
        return HTMLResponse(
            content=render_login_page(
                next_path=next_path,
                error_message="登录失败，Token 错误，请重试。",
            )
        )

    response = RedirectResponse(url=next_path, status_code=302)
    response.set_cookie(
        key=settings.cookie_name,
        value=_build_session_cookie(settings.session_secret),
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=_SESSION_TTL_SECONDS,
    )
    return response


@router.post("/webui/logout")
async def webui_logout(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/webui/login", status_code=302)
    settings = _get_settings_from_request(request)
    response.delete_cookie(key=settings.cookie_name, path="/")
    return response
