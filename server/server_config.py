from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass

from nonebot import get_driver
from nonebot.log import logger

from nextbot.data_dir import DATA_DIR


@dataclass(frozen=True)
class WebServerSettings:
    host: str
    port: int
    public_base_url: str
    webui_token: str
    session_secret: str
    auth_file_path: str
    auth_file_created: bool
    cookie_name: str = "nextbot_webui_session"


_settings_lock = threading.Lock()
_cached_settings: WebServerSettings | None = None
_WEBUI_AUTH_FILENAME = ".webui_auth.json"
_WEBUI_AUTH_FILE = DATA_DIR / _WEBUI_AUTH_FILENAME


def _parse_port(raw_value: object, default: int = 18081) -> int:
    if isinstance(raw_value, bool):
        return default

    port: int
    if isinstance(raw_value, int):
        port = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            return default
        port = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return default
        try:
            port = int(text)
        except ValueError:
            return default
    else:
        return default

    if 1 <= port <= 65535:
        return port
    return default


def _normalize_public_base_url(value: str, *, host: str, port: int) -> str:
    text = value.strip().rstrip("/")
    if text:
        return text
    return f"http://{host}:{port}"


def _load_or_create_webui_auth() -> tuple[str, str, bool]:
    auth_payload: dict[str, object] = {}
    file_exists = _WEBUI_AUTH_FILE.is_file()

    if file_exists:
        try:
            parsed = json.loads(_WEBUI_AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                auth_payload = parsed
        except (OSError, json.JSONDecodeError):
            auth_payload = {}

    token = str(auth_payload.get("webui_token", "")).strip()
    session_secret = str(auth_payload.get("session_secret", "")).strip()
    created = False

    if not token:
        token = secrets.token_urlsafe(24)
        created = True
    if not session_secret:
        session_secret = secrets.token_urlsafe(32)
        created = True

    if created or not file_exists:
        payload_text = json.dumps(
            {
                "webui_token": token,
                "session_secret": session_secret,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _WEBUI_AUTH_FILE.write_text(payload_text + "\n", encoding="utf-8")

    # H-A1：token 永久 + URL 通道暴露 = 文件泄漏 = 永久接管，必须严格保护文件权限
    # 无论是新建还是已存在的文件 / 目录，都尽力收紧权限；Windows 上 chmod 行为有限但调用本身无害
    try:
        os.chmod(_WEBUI_AUTH_FILE, 0o600)
    except OSError as exc:
        logger.warning(
            f"收紧 webui 认证文件权限失败：path={str(_WEBUI_AUTH_FILE)!r} reason={exc!r}"
        )
    try:
        os.chmod(_WEBUI_AUTH_FILE.parent, 0o700)
    except OSError as exc:
        logger.warning(
            f"收紧 webui 认证目录权限失败：path={str(_WEBUI_AUTH_FILE.parent)!r} reason={exc!r}"
        )

    return token, session_secret, created


def _build_settings() -> WebServerSettings:
    config = get_driver().config

    host = str(getattr(config, "web_server_host", "127.0.0.1")).strip() or "127.0.0.1"
    port = _parse_port(getattr(config, "web_server_port", 18081))
    public_base_url = _normalize_public_base_url(
        str(getattr(config, "web_server_public_base_url", "")),
        host=host,
        port=port,
    )
    webui_token, session_secret, auth_file_created = _load_or_create_webui_auth()

    return WebServerSettings(
        host=host,
        port=port,
        public_base_url=public_base_url,
        webui_token=webui_token,
        session_secret=session_secret,
        auth_file_path=str(_WEBUI_AUTH_FILE),
        auth_file_created=auth_file_created,
    )


def get_server_settings() -> WebServerSettings:
    global _cached_settings
    with _settings_lock:
        if _cached_settings is None:
            _cached_settings = _build_settings()
        return _cached_settings
