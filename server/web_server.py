from __future__ import annotations

import threading
from typing import Any

import uvicorn
from fastapi import FastAPI
from nonebot.log import logger

from server.page_store import create_page
from server.pages import about_page, admin_list_page, ban_list_page, inventory_page, leaderboard_page, lottery_list_page, lottery_result_page, lottery_view_page, menu_page, progress_page, red_packet_all_page, red_packet_own_page, shop_list_page, shop_view_page, tutorial_page, user_info_page, warehouse_page
from server.routes.render import router as render_router
from server.routes.webui_commands import router as webui_commands_router
from server.routes.webui_dashboard import router as webui_dashboard_router
from server.routes.webui_groups import router as webui_groups_router
from server.routes.webui_login_requests import router as webui_login_requests_router
from server.routes.webui_player_events import router as webui_player_events_router
from server.routes.webui_servers import router as webui_servers_router
from server.routes.webui_settings import router as webui_settings_router
from server.routes.webui_users import router as webui_users_router
from server.routes.webui_lottery import router as webui_lottery_router
from server.routes.webui_shop import router as webui_shop_router
from server.routes.webui_warehouse import router as webui_warehouse_router
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
    map_exploration_text: str = "",
    show_stats: bool = True,
    show_index: bool = True,
    slots: list[dict[str, Any]] = [],
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
        map_exploration_text=map_exploration_text,
        show_stats=show_stats,
        show_index=show_index,
        slots=slots,
    )
    token = create_page("inventory", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/inventory/{token}"


def create_progress_page(
    *,
    server_id: int,
    server_name: str,
    progress: dict[str, Any],
) -> str:
    payload = progress_page.build_payload(
        server_id=server_id,
        server_name=server_name,
        progress=progress,
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
) -> str:
    payload = leaderboard_page.build_payload(
        title=title,
        value_label=value_label,
        page=page,
        total_pages=total_pages,
        entries=entries,
        self_entry=self_entry,
    )
    token = create_page("leaderboard", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/leaderboard/{token}"


def create_ban_list_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = ban_list_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    token = create_page("ban_list", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/ban_list/{token}"


def create_about_page() -> str:
    payload = about_page.build_payload()
    token = create_page("about", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/about/{token}"


def create_admin_list_page(
    *,
    admins: list[dict[str, str]],
) -> str:
    payload = admin_list_page.build_payload(admins=admins)
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
    )
    token = create_page("user_info", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/user_info/{token}"


def create_menu_page(
    *,
    title: str,
    commands: list[dict[str, str]],
) -> str:
    payload = menu_page.build_payload(title=title, commands=commands)
    token = create_page("menu", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/menu/{token}"


def create_red_packet_own_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = red_packet_own_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    token = create_page("red_packet_own", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/red_packet_own/{token}"


def create_red_packet_all_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = red_packet_all_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    token = create_page("red_packet_all", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/red_packet_all/{token}"


def create_tutorial_page(
    *,
    tutorial: dict[str, Any],
    self_user_id: str,
) -> str:
    payload = tutorial_page.build_payload(
        tutorial=tutorial,
        self_user_id=self_user_id,
    )
    token = create_page("tutorial", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/tutorial/{token}"


def create_warehouse_page(
    *,
    owner_user_id: str,
    owner_user_name: str,
    slots: list[dict[str, Any]],
) -> str:
    payload = warehouse_page.build_payload(
        owner_user_id=owner_user_id,
        owner_user_name=owner_user_name,
        slots=slots,
    )
    token = create_page("warehouse", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/warehouse/{token}"


def create_lottery_list_page(
    *,
    entries: list[dict[str, Any]],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> str:
    payload = lottery_list_page.build_payload(
        entries=entries, page=page, total_pages=total_pages, total=total,
    )
    token = create_page("lottery_list", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/lottery_list/{token}"


def create_lottery_view_page(
    *,
    pool_id: int,
    pool_name: str,
    pool_description: str,
    cost_per_draw: int,
    prizes: list[dict[str, Any]],
    miss_probability: float = 0.0,
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> str:
    payload = lottery_view_page.build_payload(
        pool_id=pool_id, pool_name=pool_name, pool_description=pool_description,
        cost_per_draw=cost_per_draw, prizes=prizes, miss_probability=miss_probability,
        page=page, total_pages=total_pages, total=total,
    )
    token = create_page("lottery_view", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/lottery_view/{token}"


def create_lottery_result_page(
    *,
    pool_id: int,
    pool_name: str,
    user_user_id: str,
    user_user_name: str,
    user_coins_after: int,
    draw_count: int,
    total_cost: int,
    coin_delta: int,
    outcomes: list[dict[str, Any]],
    item_value_gained: int = 0,
    item_slots_used: int = 0,
    command_results: list[dict[str, Any]] | None = None,
) -> str:
    payload = lottery_result_page.build_payload(
        pool_id=pool_id, pool_name=pool_name,
        user_user_id=user_user_id, user_user_name=user_user_name,
        user_coins_after=user_coins_after, draw_count=draw_count,
        total_cost=total_cost, coin_delta=coin_delta,
        item_value_gained=item_value_gained, outcomes=outcomes,
        item_slots_used=item_slots_used, command_results=command_results,
    )
    token = create_page("lottery_result", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/lottery_result/{token}"


def create_shop_list_page(
    *,
    entries: list[dict[str, Any]],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> str:
    payload = shop_list_page.build_payload(
        entries=entries,
        page=page,
        total_pages=total_pages,
        total=total,
    )
    token = create_page("shop_list", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/shop_list/{token}"


def create_shop_view_page(
    *,
    shop_id: int,
    shop_name: str,
    shop_description: str,
    user_user_id: str,
    user_user_name: str,
    user_coins: int,
    items: list[dict[str, Any]],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> str:
    payload = shop_view_page.build_payload(
        shop_id=shop_id,
        shop_name=shop_name,
        shop_description=shop_description,
        user_user_id=user_user_id,
        user_user_name=user_user_name,
        user_coins=user_coins,
        items=items,
        page=page,
        total_pages=total_pages,
        total=total,
    )
    token = create_page("shop_view", payload)
    settings = get_server_settings()
    return f"{_build_internal_base_url(settings)}/render/shop_view/{token}"


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
    app.include_router(webui_login_requests_router)
    app.include_router(webui_player_events_router)
    app.include_router(webui_users_router)
    app.include_router(webui_groups_router)
    app.include_router(webui_settings_router)
    app.include_router(webui_warehouse_router)
    app.include_router(webui_shop_router)
    app.include_router(webui_lottery_router)

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
