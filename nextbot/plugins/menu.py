from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import (
    command_control,
    list_command_configs,
    raise_command_usage,
)
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import (
    EMOJI_CHART,
    EMOJI_COIN,
    EMOJI_GAME,
    EMOJI_GROUP,
    EMOJI_LIST,
    EMOJI_LOCK,
    EMOJI_LOTTERY,
    EMOJI_RED_PACKET,
    EMOJI_SECURE,
    EMOJI_SERVER,
    EMOJI_SHOP,
    EMOJI_USER,
    EMOJI_WAREHOUSE,
    reply_failure,
    reply_list,
    safe_at_segment_or_empty,
)
from server.screenshot import ScreenshotOptions
from server.web_server import create_menu_page

menu_matcher = on_command("菜单")
search_command_matcher = on_command("搜索命令")
# MI-3.1 回滚：菜单是 trusted 内部模板（命令列表为项目自身静态数据），
# 1920 实际产物 ~几百 KB，远低于 MAX_BASE64_BYTES=200MB；下游 Semaphore(2) +
# screenshot_render.py 编码前/后双 cap 已是充分防御。窄到 920 让菜单卡片 /
# usage 字符串频繁换行，用户体感"变窄"。
MENU_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1920,
    viewport_height=1280,
    full_page=True,
    fit_content_height=True,
)

# MI-3.1：handler-wide semaphore，防 guest 高频刷命令导致 Playwright 进程膨胀
_menu_semaphore = asyncio.Semaphore(2)

CATEGORY_ORDER = [
    "用户系统",
    "查询系统",
    "经济系统",
    "小游戏系统",
    "红包系统",
    "仓库系统",
    "商店系统",
    "抽奖系统",
    "排行榜",
    "服务器管理",
    "服务器工具",
    "玩家查询",
    "安全管理",
    "权限管理",
    "系统功能",
]
_UNCATEGORIZED = "未分类"

CATEGORY_EMOJI = {
    "用户系统": EMOJI_USER,
    "经济系统": EMOJI_COIN,
    "小游戏系统": EMOJI_GAME,
    "红包系统": EMOJI_RED_PACKET,
    "仓库系统": EMOJI_WAREHOUSE,
    "商店系统": EMOJI_SHOP,
    "抽奖系统": EMOJI_LOTTERY,
    "排行榜": EMOJI_CHART,
    "服务器管理": EMOJI_SERVER,
    "服务器工具": EMOJI_SERVER,
    "玩家查询": EMOJI_USER,
    "安全管理": EMOJI_SECURE,
    "权限管理": EMOJI_LOCK,
    "系统功能": EMOJI_LIST,
    _UNCATEGORIZED: EMOJI_LIST,
    "群组管理": EMOJI_GROUP,
}


async def _render_and_send_menu(
    bot: Bot,
    event: Event,
    title: str,
    render_commands: list[dict[str, str]],
) -> None:
    page_url = create_menu_page(title=title, commands=render_commands)
    logger.info(
        f"{title}渲染：command_count={len(render_commands)} url_prefix={page_url[:80]}..."
    )

    # MI-3.2：base64 size cap + semaphore 通过统一 helper 处理
    await render_and_send_screenshot(
        bot, event,
        page_url=page_url,
        options=MENU_SCREENSHOT_OPTIONS,
        file_prefix="menu",
        semaphore=_menu_semaphore,
        failure_action="生成",
    )


def _group_by_category(items: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        if not item.get("is_registered"):
            continue
        cat = str(item.get("category") or "").strip() or _UNCATEGORIZED
        by_cat.setdefault(cat, []).append(item)
    for cmds in by_cat.values():
        cmds.sort(key=lambda c: str(c.get("command_key", "")))

    ordered = [c for c in CATEGORY_ORDER if c in by_cat]
    extras = sorted(c for c in by_cat if c not in CATEGORY_ORDER and c != _UNCATEGORIZED)
    if _UNCATEGORIZED in by_cat:
        extras.append(_UNCATEGORIZED)
    cat_names = ordered + extras
    return cat_names, by_cat


@menu_matcher.handle()
@command_control(
    command_key="menu.root",
    display_name="菜单",
    permission="menu.root",
    description="查看分类菜单 / 某分类下的命令",
    usage="菜单 [分类编号/分类名]",
    category="系统功能",
)
@require_permission("menu.root")
async def handle_menu(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "菜单")
    at = safe_at_segment_or_empty(event.get_user_id())

    cat_names, by_cat = _group_by_category(list_command_configs())

    if not cat_names:
        await bot.send(event, at + " " + reply_failure("查看菜单", "暂无可用命令"))
        return

    if not args:
        items = [
            f"{CATEGORY_EMOJI.get(cat, EMOJI_LIST)} {i}. {cat}（{len(by_cat[cat])}）"
            for i, cat in enumerate(cat_names, 1)
        ]
        await bot.send(
            event,
            reply_list(
                "命令菜单",
                items,
                hint="输入 `菜单 分类编号` 或 `菜单 分类名` 查看具体命令",
            ),
        )
        return

    if len(args) != 1:
        raise_command_usage()

    selector = args[0].strip()
    if not selector:
        raise_command_usage()

    target_cat: str | None = None
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(cat_names):
            target_cat = cat_names[idx - 1]
    if target_cat is None and selector in by_cat:
        target_cat = selector

    if target_cat is None:
        await bot.send(event, at + " " + reply_failure("查看菜单", f"未找到分类「{selector}」"))
        return

    render_commands = [
        {
            "display_name": str(item.get("display_name", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "usage": str(item.get("usage", "")).strip(),
            "permission": str(item.get("permission", "")).strip(),
            "aliases": [
                str(alias).strip()
                for alias in (item.get("aliases") or [])
                if str(alias).strip()
            ],
        }
        for item in by_cat[target_cat]
    ]

    await _render_and_send_menu(bot, event, target_cat, render_commands)


@search_command_matcher.handle()
@command_control(
    command_key="menu.search",
    display_name="搜索命令",
    permission="menu.search",
    description="按关键词搜索命令名称",
    usage="搜索命令 <关键词>",
    category="系统功能",
)
@require_permission("menu.search")
async def handle_search_command(
    bot: Bot, event: Event, arg: Message = CommandArg()
) -> None:
    args = parse_command_args_with_fallback(event, arg, "搜索命令")
    if len(args) != 1:
        raise_command_usage()

    at = safe_at_segment_or_empty(event.get_user_id())

    keyword = args[0].strip()
    if not keyword:
        raise_command_usage()

    all_items = list_command_configs()
    matched = [
        item for item in all_items
        if keyword in str(item.get("display_name", ""))
    ]

    if not matched:
        await bot.send(event, at + " " + reply_failure("搜索命令", f"未找到包含「{keyword}」的命令"))
        return

    items = [str(item.get("display_name") or "") for item in matched]
    await bot.send(event, reply_list(f"搜索「{keyword}」", items))
