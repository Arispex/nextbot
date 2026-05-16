from __future__ import annotations

import asyncio
import threading
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from nonebot.log import logger

from server.page_store import create_page
from server.pages import about_page, admin_list_page, ban_list_page, dice_page, guess_number_page, inventory_page, leaderboard_page, lottery_list_page, lottery_result_page, lottery_view_page, menu_page, progress_page, red_packet_all_page, red_packet_own_page, rob_page, shop_list_page, shop_view_page, signin_page, tutorial_page, user_info_page, warehouse_page
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
from server.routes.webui import (
    add_security_headers_middleware,
    add_webui_auth_middleware,
    router as webui_router,
)
from server.server_config import WebServerSettings, get_server_settings

# M-3：路由注册表抽象。新增 webui_xxx.py 时统一在此挂入；
# 顺序无关（FastAPI include_router 不依赖顺序），但保持稳定排列便于代码 review。
_WEBUI_ROUTERS: tuple[APIRouter, ...] = (
    webui_router,
    webui_commands_router,
    webui_dashboard_router,
    webui_servers_router,
    webui_login_requests_router,
    webui_player_events_router,
    webui_users_router,
    webui_groups_router,
    webui_settings_router,
    webui_warehouse_router,
    webui_shop_router,
    webui_lottery_router,
)

# H-2：graceful shutdown 支持
_server_started = False
_server_lock = threading.Lock()
_uvicorn_server: uvicorn.Server | None = None


def _build_internal_base_url(settings: WebServerSettings) -> str:
    """构造 Playwright 截图浏览器使用的内部 URL。

    M-1：永远返回 loopback；不受 ``settings.host`` 控制（用户可能配置成
    ``0.0.0.0`` 或公网 IP）。截图浏览器始终运行在 bot 主机上，loopback 是
    最近且最安全的路径。调用方依赖此函数返回的内部 URL 进入 ``/render/*`` 端点，
    若未来需要改成 ``settings.host`` 直接拼接，须同步评估 ``/render/*``
    暴露公网的风险。
    """
    return f"http://127.0.0.1:{settings.port}"


def _make_page_url(page_type: str, payload: dict[str, Any]) -> str:
    """L-2：统一 create page → build URL 的 boilerplate。"""
    token = create_page(page_type, payload)
    return f"{_build_internal_base_url(get_server_settings())}/render/{page_type}/{token}"


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
    return _make_page_url("inventory", payload)


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
    return _make_page_url("progress", payload)


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
    return _make_page_url("leaderboard", payload)


def create_ban_list_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = ban_list_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    return _make_page_url("ban_list", payload)


def create_about_page() -> str:
    payload = about_page.build_payload()
    return _make_page_url("about", payload)


def create_admin_list_page(
    *,
    admins: list[dict[str, str]],
) -> str:
    payload = admin_list_page.build_payload(admins=admins)
    return _make_page_url("admin_list", payload)


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
    return _make_page_url("user_info", payload)


def create_menu_page(
    *,
    title: str,
    commands: list[dict[str, str]],
) -> str:
    payload = menu_page.build_payload(title=title, commands=commands)
    return _make_page_url("menu", payload)


def create_red_packet_own_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = red_packet_own_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    return _make_page_url("red_packet_own", payload)


def create_red_packet_all_page(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> str:
    payload = red_packet_all_page.build_payload(
        page=page, total_pages=total_pages, entries=entries,
    )
    return _make_page_url("red_packet_all", payload)


def create_dice_page(
    *,
    player_name: str,
    player_qq: str,
    choice: str,
    cost: int,
    dice: tuple[int, int, int],
    total: int,
    is_triple: bool,
    result_kind: str,
    payout: int,
    applied_payout: int,
    net: int,
    applied_net: int,
    final_coins: int,
    capped: bool,
) -> str:
    payload = dice_page.build_payload(
        player_name=player_name,
        player_qq=player_qq,
        choice=choice,
        cost=cost,
        dice=dice,
        total=total,
        is_triple=is_triple,
        result_kind=result_kind,
        payout=payout,
        applied_payout=applied_payout,
        net=net,
        applied_net=applied_net,
        final_coins=final_coins,
        capped=capped,
    )
    return _make_page_url("dice", payload)


def create_guess_number_page(
    *,
    player_name: str,
    player_qq: str,
    range_max: int,
    guess: int,
    answer: int,
    diff: int,
    cost: int,
    result_kind: str,
    result_label: str,
    payout: int,
    applied_payout: int,
    net: int,
    applied_net: int,
    final_coins: int,
    capped: bool,
) -> str:
    payload = guess_number_page.build_payload(
        player_name=player_name,
        player_qq=player_qq,
        range_max=range_max,
        guess=guess,
        answer=answer,
        diff=diff,
        cost=cost,
        result_kind=result_kind,
        result_label=result_label,
        payout=payout,
        applied_payout=applied_payout,
        net=net,
        applied_net=applied_net,
        final_coins=final_coins,
        capped=capped,
    )
    return _make_page_url("guess_number", payload)


def create_signin_page(
    *,
    player_name: str,
    player_qq: str,
    today_order: int,
    base_reward: int,
    streak_reward: int,
    total_reward: int,
    current_streak: int,
    streak_enabled: bool,
    streak_broken: bool,
    recent_signs: list[bool],
    coins_after: int,
    sign_total: int,
    capped: bool,
    requested_reward: int,
    applied_reward: int,
) -> str:
    payload = signin_page.build_payload(
        player_name=player_name,
        player_qq=player_qq,
        today_order=today_order,
        base_reward=base_reward,
        streak_reward=streak_reward,
        total_reward=total_reward,
        current_streak=current_streak,
        streak_enabled=streak_enabled,
        streak_broken=streak_broken,
        recent_signs=recent_signs,
        coins_after=coins_after,
        sign_total=sign_total,
        capped=capped,
        requested_reward=requested_reward,
        applied_reward=applied_reward,
    )
    return _make_page_url("signin", payload)


def create_rob_page(
    *,
    robber_name: str,
    robber_qq: str,
    victim_name: str,
    victim_qq: str,
    result_kind: str,
    result_label: str,
    amount: int,
    applied_amount: int,
    capped: bool,
    cap_subject: str,
    robber_final_coins: int,
) -> str:
    payload = rob_page.build_payload(
        robber_name=robber_name,
        robber_qq=robber_qq,
        victim_name=victim_name,
        victim_qq=victim_qq,
        result_kind=result_kind,
        result_label=result_label,
        amount=amount,
        applied_amount=applied_amount,
        capped=capped,
        cap_subject=cap_subject,
        robber_final_coins=robber_final_coins,
    )
    return _make_page_url("rob", payload)


def create_tutorial_page(
    *,
    tutorial: dict[str, Any],
    self_user_id: str,
) -> str:
    payload = tutorial_page.build_payload(
        tutorial=tutorial,
        self_user_id=self_user_id,
    )
    return _make_page_url("tutorial", payload)


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
    return _make_page_url("warehouse", payload)


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
    return _make_page_url("lottery_list", payload)


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
    return _make_page_url("lottery_view", payload)


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
    return _make_page_url("lottery_result", payload)


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
    return _make_page_url("shop_list", payload)


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
    return _make_page_url("shop_view", payload)


def create_app(settings: WebServerSettings | None = None) -> FastAPI:
    runtime_settings = settings or get_server_settings()

    app = FastAPI(
        title="NextBot Web Server",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.server_settings = runtime_settings

    # M-2：中间件 LIFO 链顺序（外 → 内）：
    #   1. CORS（H-3，最外层，处理 OPTIONS preflight 并附加 CORS 响应头）
    #   2. security headers（M-A3，所有 webui 响应注入 CSP / X-Frame-Options 等）
    #   3. webui auth（M-2 区分 401 vs 302）
    #   4. router
    # 新增中间件请在此顺序内插入，并同步更新本注释。
    # H-3：CORS allow_origins 默认空（仅 same-origin），用户在 .env 中配置
    # ``WEBUI_CORS_ALLOWED_ORIGINS`` 后才放开特定 origin；绝不使用 wildcard。
    allowed_origins = _resolve_cors_allowed_origins(runtime_settings)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            allow_headers=["*"],
        )
    add_security_headers_middleware(app)
    add_webui_auth_middleware(app, runtime_settings)
    app.include_router(render_router)
    for router in _WEBUI_ROUTERS:
        app.include_router(router)

    @app.get("/health")
    async def health(request: Request) -> dict[str, str]:
        # H-4：限制 /health 到 loopback；外网指纹识别 / 探活面收敛。
        client_host = request.client.host if request.client is not None else ""
        if client_host not in {"127.0.0.1", "::1"}:
            raise HTTPException(status_code=404, detail="not found")
        return {"status": "ok"}

    return app


def _resolve_cors_allowed_origins(settings: WebServerSettings) -> list[str]:
    """H-3：从 NoneBot config 读取 CORS 白名单。

    返回为空 list = 不挂 CORS 中间件（默认拒绝跨域）。配置示例：
    ``WEBUI_CORS_ALLOWED_ORIGINS=["https://admin.example.com"]``
    """
    try:
        from nonebot import get_driver

        raw = getattr(get_driver().config, "webui_cors_allowed_origins", None)
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    origins: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text or text == "*":
            # 显式过滤 wildcard，CORS + credentials 共用会成更大攻击面。
            continue
        origins.append(text)
    _ = settings  # 预留：后续如要从 public_base_url 派生 origin 可在此扩展
    return origins


def _run_server() -> None:
    """H-2：用显式 ``uvicorn.Server`` 实例而非 ``uvicorn.run``，便于 shutdown。

    注意：本函数在 daemon 后台线程内执行，所以 ``signal_handlers=False``
    （Python ``signal.signal`` 仅允许主线程调用）。优雅退出依赖
    ``stop_web_server`` 把 ``should_exit`` 置位 + 主进程退出前主动调用。
    截图浏览器 / NoneBot driver 的 shutdown 钩子各自负责自身资源；本函数
    只保证 uvicorn 自身的 keep-alive 连接 + 监听 fd 被释放。
    """
    global _uvicorn_server
    settings = get_server_settings()
    app = create_app(settings)

    # L-4：日志合并；监听地址打 settings.host，loopback URL 单独提示。
    # token 用 warning 级别打明文，便于运维直接从终端复制；运维需保证日志权限。
    logger.info(
        f"Web Server 已启动，监听 {settings.host}:{settings.port}（loopback 访问：http://127.0.0.1:{settings.port}/webui）"
    )
    if settings.auth_file_created:
        logger.info(
            f"已生成 Web UI 认证文件：{settings.auth_file_path}"
        )
    logger.warning(f"Web UI Token：{settings.webui_token}")

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
        loop="asyncio",
        lifespan="on",
        workers=1,
        # H-2：signal.signal 在子线程会 ValueError；显式关掉，由 stop_web_server
        # 控制 should_exit。
        use_colors=False,
    )
    server = uvicorn.Server(config)
    # uvicorn 默认会注册 signal handler，在子线程必定失败 / 抛 ValueError。
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]
    _uvicorn_server = server
    try:
        asyncio.run(server.serve())
    finally:
        _uvicorn_server = None


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


def stop_web_server() -> None:
    """H-2：触发 uvicorn 优雅关闭。

    供进程退出前 / 软重启路径调用；幂等 —— uvicorn ``should_exit=True`` 后
    serve 循环会自然返回，绑定线程随之结束。daemon 线程模型下若主进程已被
    强杀则本函数也来不及调用，仍依赖 atexit / OS 释放端口 fd。
    """
    server = _uvicorn_server
    if server is not None:
        server.should_exit = True


def start_render_server() -> None:
    # Backward compatible alias.
    start_web_server()
