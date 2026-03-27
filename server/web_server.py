from __future__ import annotations

import threading
from typing import Any

import uvicorn
from fastapi import FastAPI
from nonebot.log import logger

from server.page_store import create_page
from server.pages import admin_list_page, inventory_page, leaderboard_page, menu_page, progress_page, user_info_page
from server.routes.render import router as render_router
from server.routes.webui_commands import router as webui_commands_router
from server.routes.webui_dashboard import router as webui_dashboard_router
from server.routes.webui_groups import router as webui_groups_router
from server.routes.webui_servers import router as webui_servers_router
from server.routes.webui_settings import router as webui_settings_router
from server.routes.webui_users import router as webui_users_router
from server.routes.webui import add_webui_auth_middleware, router as webui_router
from server.server_config import WebServerSettings, get_server_settings

_server_started = False
_server_lock = threading.Lock()


def _build_internal_base_url(settings: WebServerSettings) -> str:
    return f"http://127.0.0.1:{settings.port}"


def create_inventory_page(
    *,
    user_id: str,
    user_name: str,
    server_id: int,
    server_name: str,
    life_text: str,
    mana_text: str,
    fishing_tasks_text: str,
    pve_deaths_text: str,
    pvp_deaths_text: str,
    online_time_text: str = "",
    show_stats: bool = True,
    show_index: bool = True,
    slots: list[dict[str, Any]] = [],
    theme: str = "light",
) -> str:
    payload = inventory_page.build_payload(
        user_id=user_id,
        user_name=user_name,
        server_id=server_id,
        server_name=server_name,
        life_text=life_text,
        mana_text=mana_text,
        fishing_tasks_text=fishing_tasks_text,
        pve_deaths_text=pve_deaths_text,
        pvp_deaths_text=pvp_deaths_text,
        online_time_text=online_time_text,
        show_stats=show_stats,
        show_index=show_index,
        slots=slots,
        theme=theme,
    )
    token = create_page("inventory", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/inventory/{token}"


def create_progress_page(
    *,
    server_id: int,
    server_name: str,
    progress: dict[str, Any],
    theme: str = "dark",
) -> str:
    payload = progress_page.build_payload(
        server_id=server_id,
        server_name=server_name,
        progress=progress,
        theme=theme,
    )
    token = create_page("progress", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/progress/{token}"


def create_leaderboard_page(
    *,
    title: str,
    value_label: str,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
    self_entry: dict[str, Any] | None = None,
    theme: str = "dark",
) -> str:
    payload = leaderboard_page.build_payload(
        title=title,
        value_label=value_label,
        page=page,
        total_pages=total_pages,
        entries=entries,
        self_entry=self_entry,
        theme=theme,
    )
    token = create_page("leaderboard", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/leaderboard/{token}"


def create_admin_list_page(
    *,
    admins: list[dict[str, str]],
    theme: str = "light",
) -> str:
    payload = admin_list_page.build_payload(admins=admins, theme=theme)
    token = create_page("admin_list", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/admin_list/{token}"


def create_user_info_page(
    *,
    user_id: str,
    user_name: str,
    coins: int,
    sign_streak: int,
    sign_total: int,
    permissions: str,
    group: str,
    created_at: str,
    sign_dates: list[str],
    days: int = 90,
    theme: str = "light",
) -> str:
    payload = user_info_page.build_payload(
        user_id=user_id,
        user_name=user_name,
        coins=coins,
        sign_streak=sign_streak,
        sign_total=sign_total,
        permissions=permissions,
        group=group,
        created_at=created_at,
        sign_dates=sign_dates,
        days=days,
        theme=theme,
    )
    token = create_page("user_info", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/user_info/{token}"


def create_menu_page(
    *,
    title: str,
    commands: list[dict[str, str]],
    theme: str = "light",
) -> str:
    payload = menu_page.build_payload(title=title, commands=commands, theme=theme)
    token = create_page("menu", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/menu/{token}"


def create_app(settings: WebServerSettings | None = None) -> FastAPI:
    runtime_settings = settings or get_server_settings()

    app = FastAPI(
        title="NextBot Web Server",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.server_settings = runtime_settings

    add_webui_auth_middleware(app, runtime_settings)
    app.include_router(render_router)
    app.include_router(webui_router)
    app.include_router(webui_commands_router)
    app.include_router(webui_dashboard_router)
    app.include_router(webui_servers_router)
    app.include_router(webui_users_router)
    app.include_router(webui_groups_router)
    app.include_router(webui_settings_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _run_server() -> None:
    settings = get_server_settings()
    app = create_app(settings)

    logger.info(f"Web Server 已启动：http://{settings.host}:{settings.port}")
    logger.info(f"Web UI：http://127.0.0.1:{settings.port}/webui")
    if settings.auth_file_created:
        logger.info(f"已初始化 Web UI 认证文件：{settings.auth_file_path}")
    logger.warning(f"Web UI Token：{settings.webui_token}")

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


def start_web_server() -> None:
    global _server_started
    with _server_lock:
        if _server_started:
            return
        thread = threading.Thread(
            target=_run_server,
            name="nextbot-web-server",
            daemon=True,
        )
        thread.start()
        _server_started = True


def start_render_server() -> None:
    # Backward compatible alias.
    start_web_server()
