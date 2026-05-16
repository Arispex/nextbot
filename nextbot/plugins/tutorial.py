from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.tutorial_data import get_tutorial, list_tutorials
from nextbot.screenshot_render import render_and_send_screenshot
from nextbot.text_utils import EMOJI_GUIDE, reply_failure, reply_list, safe_at_segment_or_empty
from server.screenshot import ScreenshotOptions
from server.web_server import create_tutorial_page

tutorial_matcher = on_command("使用教程")

TUTORIAL_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=1400,
    full_page=True,
    fit_content_height=True,
)

# MI-2.1：handler-wide semaphore，防 guest 高频刷命令导致 Playwright 进程膨胀
_tutorial_semaphore = asyncio.Semaphore(2)


@tutorial_matcher.handle()
@command_control(
    command_key="system.tutorial",
    display_name="使用教程",
    permission="system.tutorial",
    description="查看各系统使用教程，新手必看",
    usage="使用教程 [名称/序号]",
    category="系统功能",
)
@require_permission("system.tutorial")
async def handle_tutorial(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "使用教程")
    at = safe_at_segment_or_empty(event.get_user_id())
    tutorials = list_tutorials()

    if not tutorials:
        await bot.send(event, at + " " + reply_failure("查询", "暂无可用教程"))
        return

    if not args:
        items = [
            f"{str(t.get('emoji', '')).strip() or EMOJI_GUIDE} {i}. {t.get('title', '')}"
            for i, t in enumerate(tutorials, 1)
        ]
        await bot.send(
            event,
            reply_list(
                "使用教程",
                items,
                title_emoji=EMOJI_GUIDE,
                hint="输入「使用教程 <名称/序号>」查看具体教程",
            ),
        )
        return

    if len(args) != 1:
        raise_command_usage()

    selector = args[0].strip()
    target: dict | None = None
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(tutorials):
            target = tutorials[idx - 1]
    if target is None:
        target = get_tutorial(selector)

    if target is None:
        await bot.send(
            event,
            at + " " + reply_failure("查询", "未找到该教程，发送「使用教程」查看所有教程"),
        )
        return

    user_id = event.get_user_id()

    page_url = create_tutorial_page(
        tutorial=target,
        self_user_id=user_id,
    )
    logger.info(
        f"使用教程渲染：slug={target.get('slug')} user_id={user_id} url_prefix={page_url[:80]}..."
    )

    # MI-2.2：base64 size cap + semaphore 通过统一 helper 处理
    await render_and_send_screenshot(
        bot, event,
        page_url=page_url,
        options=TUTORIAL_SCREENSHOT_OPTIONS,
        file_prefix="tutorial",
        semaphore=_tutorial_semaphore,
        failure_action="生成",
    )
