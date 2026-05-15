from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from server.page_store import get_page
from server.pages import about_page, admin_list_page, ban_list_page, inventory_page, leaderboard_page, lottery_list_page, lottery_result_page, lottery_view_page, menu_page, progress_page, red_packet_all_page, red_packet_own_page, shop_list_page, shop_view_page, tutorial_page, user_info_page, warehouse_page

router = APIRouter()

SERVER_DIR = Path(__file__).resolve().parent.parent
ITEMS_DIR = SERVER_DIR / "assets" / "items"
DICTS_DIR = SERVER_DIR / "assets" / "dicts"
BOSS_IMGS_DIR = SERVER_DIR / "assets" / "imgs" / "boss"
FONTS_DIR = SERVER_DIR / "assets" / "fonts"
CSS_DIR = SERVER_DIR / "assets" / "css"
LOGOS_DIR = SERVER_DIR.parent / "logos"

# MED-6：/render/<page>/<token> 仅限本机访问。Playwright headless 走 127.0.0.1，
# 任何来自外网的访问（即使持有 token）也直接 403，避免运维把 host 改为 0.0.0.0
# 后 token 字符串泄漏导致用户隐私数据被读。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _ensure_loopback(request: Request) -> None:
    """MED-6：拒绝非环回访问的 /render/* 端点。"""
    host = request.client.host if request.client is not None else ""
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="禁止访问")


async def _render_page(
    request: Request,
    token: str,
    *,
    page_type: str,
    renderer: Callable[[dict[str, Any]], bytes],
) -> Response:
    _ensure_loopback(request)
    payload = get_page(token)
    if payload is None or payload.get("type") != page_type:
        raise HTTPException(status_code=404, detail="页面不存在")
    try:
        # MED-19：CPU-bound renderer 推到线程池，避免阻塞事件循环；上层 player_query 已限并发。
        content = await asyncio.to_thread(renderer, payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="模板读取失败") from exc
    return Response(content=content, media_type="text/html; charset=utf-8")


def _resolve_static_file(root: Path, raw_path: str) -> Path:
    file_name = unquote(raw_path).strip()
    raw_target = root / file_name
    file_path = raw_target.resolve()
    try:
        file_path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="禁止访问") from exc
    # HIGH-4：显式拒绝 symlink，部署侧 bind-mount overlay 时不让 symlink 逃出 root。
    if raw_target.is_symlink() or file_path.is_symlink():
        raise HTTPException(status_code=403, detail="禁止访问")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return file_path


@router.get("/render/inventory/{token}")
async def render_inventory(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="inventory", renderer=inventory_page.render)


@router.get("/render/progress/{token}")
async def render_progress(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="progress", renderer=progress_page.render)


@router.get("/render/menu/{token}")
async def render_menu(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="menu", renderer=menu_page.render)


@router.get("/render/leaderboard/{token}")
async def render_leaderboard(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="leaderboard", renderer=leaderboard_page.render)


@router.get("/render/user_info/{token}")
async def render_user_info(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="user_info", renderer=user_info_page.render)


@router.get("/render/admin_list/{token}")
async def render_admin_list(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="admin_list", renderer=admin_list_page.render)


@router.get("/render/about/{token}")
async def render_about(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="about", renderer=about_page.render)


@router.get("/render/ban_list/{token}")
async def render_ban_list(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="ban_list", renderer=ban_list_page.render)


@router.get("/render/red_packet_own/{token}")
async def render_red_packet_own(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="red_packet_own", renderer=red_packet_own_page.render)


@router.get("/render/red_packet_all/{token}")
async def render_red_packet_all(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="red_packet_all", renderer=red_packet_all_page.render)


@router.get("/render/tutorial/{token}")
async def render_tutorial(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="tutorial", renderer=tutorial_page.render)


@router.get("/render/warehouse/{token}")
async def render_warehouse(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="warehouse", renderer=warehouse_page.render)


@router.get("/render/shop_list/{token}")
async def render_shop_list(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="shop_list", renderer=shop_list_page.render)


@router.get("/render/shop_view/{token}")
async def render_shop_view(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="shop_view", renderer=shop_view_page.render)


@router.get("/render/lottery_list/{token}")
async def render_lottery_list(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="lottery_list", renderer=lottery_list_page.render)


@router.get("/render/lottery_view/{token}")
async def render_lottery_view(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="lottery_view", renderer=lottery_view_page.render)


@router.get("/render/lottery_result/{token}")
async def render_lottery_result(request: Request, token: str) -> Response:
    return await _render_page(request, token, page_type="lottery_result", renderer=lottery_result_page.render)


@router.get("/assets/items/{file_path:path}")
async def get_item_asset(file_path: str) -> FileResponse:
    resolved_path = _resolve_static_file(ITEMS_DIR, file_path)
    return FileResponse(path=resolved_path)


@router.get("/assets/dicts/{file_path:path}")
async def get_dict_asset(file_path: str) -> FileResponse:
    resolved_path = _resolve_static_file(DICTS_DIR, file_path)
    return FileResponse(path=resolved_path)


@router.get("/assets/imgs/boss/{file_path:path}")
async def get_boss_img_asset(file_path: str) -> FileResponse:
    resolved_path = _resolve_static_file(BOSS_IMGS_DIR, file_path)
    return FileResponse(path=resolved_path)


@router.get("/assets/fonts/{file_path:path}")
async def get_font_asset(file_path: str) -> FileResponse:
    resolved_path = _resolve_static_file(FONTS_DIR, file_path)
    return FileResponse(path=resolved_path, media_type="font/woff2")


@router.get("/assets/css/{file_path:path}")
async def get_css_asset(file_path: str) -> FileResponse:
    resolved_path = _resolve_static_file(CSS_DIR, file_path)
    return FileResponse(path=resolved_path, media_type="text/css; charset=utf-8")


@router.get("/assets/imgs/logo-light.png")
async def get_logo_light_asset() -> FileResponse:
    logo_path = LOGOS_DIR / "logo__white_background_with_black_text.png"
    if not logo_path.is_file():
        raise HTTPException(status_code=404, detail="Logo 不存在")
    return FileResponse(path=logo_path)


@router.get("/assets/imgs/logo-dark.png")
async def get_logo_dark_asset() -> FileResponse:
    logo_path = LOGOS_DIR / "logo__black_background_with_white_text.png"
    if not logo_path.is_file():
        raise HTTPException(status_code=404, detail="Logo 不存在")
    return FileResponse(path=logo_path)
