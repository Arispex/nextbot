from __future__ import annotations

import html
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent.parent
WEBUI_TEMPLATE_DIR = BASE_DIR / "webui" / "templates"
WEBUI_STATIC_DIR = BASE_DIR / "webui" / "static"

AppShellMenu = Literal[
    "dashboard",
    "commands",
    "servers",
    "users",
    "groups",
    "warehouse",
    "shop",
    "lottery",
    "settings",
]


def _load_template(name: str) -> str:
    path = WEBUI_TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8")


def _asset_url(path: str) -> str:
    normalized = path.lstrip("/")
    file_path = WEBUI_STATIC_DIR / normalized
    if file_path.is_file():
        version = str(int(file_path.stat().st_mtime))
        return f"/webui/static/{normalized}?v={version}"
    return f"/webui/static/{normalized}"


def _render_app_shell_page(  # noqa: PLR0913
    *,
    page_title: str,
    header_title: str,
    active_menu: AppShellMenu,
    content_template: str,
    page_style_urls: tuple[str, ...] = (),
    page_script_urls: tuple[str, ...] = (),
) -> str:
    """渲染 app shell 模板。

    Round 9 dashboard-audit A1：明确模板信任假设——

    - ``page_title`` / ``header_title`` 必须为**可信字面量**（不接受用户输入 /
      DB 字段）。虽然函数内已用 ``html.escape(quote=True)`` 兜底，但仅作为最后
      防线，禁止依赖该兜底来传入不可信数据。
    - ``_load_template(content_template)`` 加载的模板内容**直接塞入
      ``__MAIN_CONTENT__`` 占位符，不再做 escape**。内容模板（如
      ``dashboard_content.html``）内禁止使用 ``__XXX__`` 占位符接收外部数据
      （DB / 用户输入）；如未来需要服务端注入变量，必须在 caller 端显式
      ``html.escape(...)`` 后再传入。
    """
    base_template = _load_template("app_shell_base.html")
    content_html = _load_template(content_template)
    style_links_html = "\n  ".join(
        f'<link rel="stylesheet" href="{html.escape(url, quote=True)}" />'
        for url in page_style_urls
    )
    script_tags_html = "\n  ".join(
        f'<script src="{html.escape(url, quote=True)}"></script>'
        for url in page_script_urls
    )
    dashboard_active = "is-active" if active_menu == "dashboard" else ""
    commands_active = "is-active" if active_menu == "commands" else ""
    servers_active = "is-active" if active_menu == "servers" else ""
    users_active = "is-active" if active_menu == "users" else ""
    groups_active = "is-active" if active_menu == "groups" else ""
    warehouse_active = "is-active" if active_menu == "warehouse" else ""
    shop_active = "is-active" if active_menu == "shop" else ""
    lottery_active = "is-active" if active_menu == "lottery" else ""
    settings_active = "is-active" if active_menu == "settings" else ""

    return (
        base_template.replace("__PAGE_TITLE__", html.escape(page_title))
        .replace("__HEADER_TITLE__", html.escape(header_title))
        .replace("__PAGE_STYLE_LINKS__", style_links_html)
        .replace("__NAV_DASHBOARD_ACTIVE__", dashboard_active)
        .replace("__NAV_COMMANDS_ACTIVE__", commands_active)
        .replace("__NAV_SERVERS_ACTIVE__", servers_active)
        .replace("__NAV_USERS_ACTIVE__", users_active)
        .replace("__NAV_GROUPS_ACTIVE__", groups_active)
        .replace("__NAV_WAREHOUSE_ACTIVE__", warehouse_active)
        .replace("__NAV_SHOP_ACTIVE__", shop_active)
        .replace("__NAV_LOTTERY_ACTIVE__", lottery_active)
        .replace("__NAV_SETTINGS_ACTIVE__", settings_active)
        .replace("__MAIN_CONTENT__", content_html)
        .replace(
            "__WEBUI_SCRIPT_URL__",
            html.escape(_asset_url("js/webui.js"), quote=True),
        )
        .replace(
            "__WEBUI_API_SCRIPT_URL__",
            html.escape(_asset_url("js/api.js"), quote=True),
        )
        .replace("__PAGE_SCRIPT_TAGS__", script_tags_html)
    )


def render_login_page(*, next_path: str) -> str:
    escaped_next = html.escape(next_path, quote=True)
    template = _load_template("login.html")
    return (
        template.replace("__NEXT_PATH__", escaped_next)
        .replace(
            "__WEBUI_API_SCRIPT_URL__",
            html.escape(_asset_url("js/api.js"), quote=True),
        )
    )


def render_console_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 仪表盘",
        header_title="仪表盘",
        active_menu="dashboard",
        content_template="dashboard_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/dashboard.css"),
        ),
        page_script_urls=(
            _asset_url("js/dashboard.js"),
        ),
    )


def render_commands_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 命令配置",
        header_title="命令配置",
        active_menu="commands",
        content_template="commands_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/commands.css"),
        ),
        page_script_urls=(
            _asset_url("js/commands.js"),
        ),
    )


def render_servers_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 服务器管理",
        header_title="服务器管理",
        active_menu="servers",
        content_template="servers_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/servers.css"),
        ),
        page_script_urls=(
            _asset_url("js/servers.js"),
        ),
    )


def render_users_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 用户管理",
        header_title="用户管理",
        active_menu="users",
        content_template="users_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/users.css"),
        ),
        page_script_urls=(
            _asset_url("js/users.js"),
        ),
    )


def render_groups_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 身份组管理",
        header_title="身份组管理",
        active_menu="groups",
        content_template="groups_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/groups.css"),
        ),
        page_script_urls=(
            _asset_url("js/groups.js"),
        ),
    )


def render_warehouse_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 仓库管理",
        header_title="仓库管理",
        active_menu="warehouse",
        content_template="warehouse_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/warehouse.css"),
        ),
        page_script_urls=(
            _asset_url("js/warehouse.js"),
        ),
    )


def render_shop_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 商店管理",
        header_title="商店管理",
        active_menu="shop",
        content_template="shop_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/shop.css"),
        ),
        page_script_urls=(
            _asset_url("js/shop.js"),
        ),
    )


def render_lottery_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 抽奖管理",
        header_title="抽奖管理",
        active_menu="lottery",
        content_template="lottery_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/lottery.css"),
        ),
        page_script_urls=(
            _asset_url("js/lottery.js"),
        ),
    )


def render_settings_page() -> str:
    return _render_app_shell_page(
        page_title="NextBot WebUI - 设置",
        header_title="设置",
        active_menu="settings",
        content_template="settings_content.html",
        page_style_urls=(
            _asset_url("css/app-shell.css"),
            _asset_url("css/settings.css"),
        ),
        page_script_urls=(
            _asset_url("js/settings.js"),
        ),
    )
