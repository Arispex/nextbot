from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

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
    # CRIT-1 / HIGH-2：受信代理 IP 白名单（裸字符串 IP，原样字符串比对 request.client.host）。
    # 仅当连接来源 IP 在此白名单中时才信任 X-Forwarded-For；默认空集合 = 一律取 request.client.host。
    trusted_proxies: frozenset[str] = frozenset()
    cookie_name: str = "nextbot_webui_session"


_settings_lock = threading.Lock()
_cached_settings: WebServerSettings | None = None
_WEBUI_AUTH_FILENAME = ".webui_auth.json"
_WEBUI_AUTH_FILE = DATA_DIR / _WEBUI_AUTH_FILENAME


def _parse_port(raw_value: object, default: int = 18081) -> int:
    """M-5：解析 .env 中的 WEB_SERVER_PORT，非法值 fallback 到默认并 WARN。"""
    if isinstance(raw_value, bool):
        logger.warning(
            f"WEB_SERVER_PORT 配置无效（值={raw_value!r}，类型为 bool），回退到默认 {default}"
        )
        return default

    port: int
    if isinstance(raw_value, int):
        port = raw_value
    elif isinstance(raw_value, float):
        if not raw_value.is_integer():
            logger.warning(
                f"WEB_SERVER_PORT 配置无效（值={raw_value!r}，非整数 float），回退到默认 {default}"
            )
            return default
        port = int(raw_value)
    elif isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return default
        try:
            port = int(text)
        except ValueError:
            logger.warning(
                f"WEB_SERVER_PORT 配置无效（值={raw_value!r}，无法解析为整数），回退到默认 {default}"
            )
            return default
    else:
        logger.warning(
            f"WEB_SERVER_PORT 配置无效（值={raw_value!r}，类型={type(raw_value).__name__}），回退到默认 {default}"
        )
        return default

    if 1 <= port <= 65535:
        return port
    logger.warning(
        f"WEB_SERVER_PORT 配置越界（值={port}，需在 1-65535 之间），回退到默认 {default}"
    )
    return default


def _normalize_public_base_url(value: str, *, host: str, port: int) -> str:
    """M-6：除了 strip / rstrip 外，校验 scheme 必须是 http / https。

    非法 scheme（javascript: / file: / data: 等）一律回退到 ``http://{host}:{port}``，
    并 WARN 提示用户配置被忽略。
    """
    text = value.strip().rstrip("/")
    if not text:
        return f"http://{host}:{port}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning(
            f"WEB_SERVER_PUBLIC_BASE_URL 无效（值={text!r}，需为 http/https URL），回退到默认 http://{host}:{port}"
        )
        return f"http://{host}:{port}"
    return text


def _parse_trusted_proxies(raw_value: object) -> frozenset[str]:
    """CRIT-1 / HIGH-2：解析受信代理 IP 列表。

    支持 list[str] / 逗号分隔 str；空白 / 非字符串元素丢弃。默认空集合表示
    "不信任任何 X-Forwarded-For"，部署裸 FastAPI 时即 fail-closed。
    """
    if raw_value is None:
        return frozenset()
    if isinstance(raw_value, str):
        candidates = [p.strip() for p in raw_value.split(",")]
    elif isinstance(raw_value, (list, tuple, set, frozenset)):
        candidates = [str(p).strip() for p in raw_value if isinstance(p, (str, int, float))]
    else:
        return frozenset()
    return frozenset(c for c in candidates if c)


def _atomic_write_auth_file(payload_text: str) -> None:
    """H-7：temp + rename 原子写 .webui_auth.json。

    1. 先 chmod 0o600 写 temp，避免普通 umask 0o644 短窗口被同机器用户读取；
    2. ``os.replace`` 原子换名，崩溃中不可能落出半行 JSON；
    3. 父目录权限单独收紧到 0o700（最佳努力，Windows 上 chmod 行为有限）。
    """
    temp_path = _WEBUI_AUTH_FILE.with_suffix(".json.tmp")
    # 使用 O_CREAT|O_WRONLY|O_TRUNC + 0o600 一步把权限收紧，避免 write + chmod 之间的 TOCTOU
    fd = os.open(
        str(temp_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, (payload_text + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(temp_path, _WEBUI_AUTH_FILE)


def _load_or_create_webui_auth() -> tuple[str, str, bool]:
    auth_payload: dict[str, object] = {}
    file_exists = _WEBUI_AUTH_FILE.is_file()

    if file_exists:
        try:
            parsed = json.loads(_WEBUI_AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                auth_payload = parsed
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"读取 webui 认证文件失败，将重新生成 token：path={str(_WEBUI_AUTH_FILE)!r} reason={exc!r}"
            )
            auth_payload = {}

    # H-7：严格类型校验。``null`` / 非字符串值一律视为缺失，避免 ``str(None)`` 误当成有效 token。
    raw_token = auth_payload.get("webui_token")
    token = raw_token.strip() if isinstance(raw_token, str) else ""
    raw_secret = auth_payload.get("session_secret")
    session_secret = raw_secret.strip() if isinstance(raw_secret, str) else ""
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
        try:
            _atomic_write_auth_file(payload_text)
        except OSError as exc:
            logger.warning(
                f"写入 webui 认证文件失败：path={str(_WEBUI_AUTH_FILE)!r} reason={exc!r}"
            )

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
    trusted_proxies = _parse_trusted_proxies(
        getattr(config, "webui_trusted_proxies", None)
    )

    return WebServerSettings(
        host=host,
        port=port,
        public_base_url=public_base_url,
        webui_token=webui_token,
        session_secret=session_secret,
        auth_file_path=str(_WEBUI_AUTH_FILE),
        auth_file_created=auth_file_created,
        trusted_proxies=trusted_proxies,
    )


def get_server_settings() -> WebServerSettings:
    """返回进程级缓存的运行期配置；首次调用时构建并锁定。

    L-3 / M-4：cache 在进程生命周期内不刷新。settings_service.save_settings 写
    ``.env`` 后必须配合 ``os.execv`` 软重启才能让新值生效；运行期内任何 .env 改
    动若不重启都不会被本函数感知。
    """
    global _cached_settings
    with _settings_lock:
        if _cached_settings is None:
            _cached_settings = _build_settings()
        return _cached_settings
