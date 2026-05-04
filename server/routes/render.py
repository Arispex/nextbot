from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
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


def _render_page(
    token: str,
    *,
    page_type: str,
    renderer: Callable[[dict[str, Any]], bytes],
) -> Response:
    payload = get_page(token)
    if payload is None or payload.get("type") != page_type:
        raise HTTPException(status_code=404, detail="page not found")
    try:
        content = renderer(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="template read error") from exc
    return Response(content=content, media_type="text/html; charset=utf-8")


def _resolve_static_file(root: Path, raw_path: str) -> Path:
    file_name = unquote(raw_path).strip()
    file_path = (root / file_name).resolve()
    try:
        file_path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="forbidden") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return file_path


@router.get("/render/inventory/{token}")
async def render_inventory(token: str) -> Response:
    return _render_page(token, page_type="inventory", renderer=inventory_page.render)


@router.get("/render/progress/{token}")
async def render_progress(token: str) -> Response:
    return _render_page(token, page_type="progress", renderer=progress_page.render)


@router.get("/render/menu/{token}")
async def render_menu(token: str) -> Response:
    return _render_page(token, page_type="menu", renderer=menu_page.render)


@router.get("/render/leaderboard/{token}")
async def render_leaderboard(token: str) -> Response:
    return _render_page(token, page_type="leaderboard", renderer=leaderboard_page.render)


@router.get("/render/user_info/{token}")
async def render_user_info(token: str) -> Response:
    return _render_page(token, page_type="user_info", renderer=user_info_page.render)


@router.get("/render/admin_list/{token}")
async def render_admin_list(token: str) -> Response:
    return _render_page(token, page_type="admin_list", renderer=admin_list_page.render)


@router.get("/render/about/{token}")
async def render_about(token: str) -> Response:
    return _render_page(token, page_type="about", renderer=about_page.render)


@router.get("/render/ban_list/{token}")
async def render_ban_list(token: str) -> Response:
    return _render_page(token, page_type="ban_list", renderer=ban_list_page.render)


@router.get("/render/red_packet_own/{token}")
async def render_red_packet_own(token: str) -> Response:
    return _render_page(token, page_type="red_packet_own", renderer=red_packet_own_page.render)


@router.get("/render/red_packet_all/{token}")
async def render_red_packet_all(token: str) -> Response:
    return _render_page(token, page_type="red_packet_all", renderer=red_packet_all_page.render)


@router.get("/render/tutorial/{token}")
async def render_tutorial(token: str) -> Response:
    return _render_page(token, page_type="tutorial", renderer=tutorial_page.render)


@router.get("/render/warehouse/{token}")
async def render_warehouse(token: str) -> Response:
    return _render_page(token, page_type="warehouse", renderer=warehouse_page.render)


@router.get("/render/shop_list/{token}")
async def render_shop_list(token: str) -> Response:
    return _render_page(token, page_type="shop_list", renderer=shop_list_page.render)


@router.get("/render/shop_view/{token}")
async def render_shop_view(token: str) -> Response:
    return _render_page(token, page_type="shop_view", renderer=shop_view_page.render)


@router.get("/render/lottery_list/{token}")
async def render_lottery_list(token: str) -> Response:
    return _render_page(token, page_type="lottery_list", renderer=lottery_list_page.render)


@router.get("/render/lottery_view/{token}")
async def render_lottery_view(token: str) -> Response:
    return _render_page(token, page_type="lottery_view", renderer=lottery_view_page.render)


@router.get("/render/lottery_result/{token}")
async def render_lottery_result(token: str) -> Response:
    return _render_page(token, page_type="lottery_result", renderer=lottery_result_page.render)


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
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(path=logo_path)


@router.get("/assets/imgs/logo-dark.png")
async def get_logo_dark_asset() -> FileResponse:
    logo_path = LOGOS_DIR / "logo__black_background_with_white_text.png"
    if not logo_path.is_file():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(path=logo_path)
