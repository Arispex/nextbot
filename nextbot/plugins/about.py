from __future__ import annotations

import asyncio

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.screenshot_render import render_and_send_screenshot
from server.screenshot import ScreenshotOptions
from server.web_server import create_about_page

about_matcher = on_command("关于")

ABOUT_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=800,
    full_page=True,
    fit_content_height=True,
)

# MI-1.1：handler-wide semaphore，防 guest 高频刷命令导致 Playwright 进程膨胀
_about_semaphore = asyncio.Semaphore(2)


@about_matcher.handle()
@command_control(
    command_key="about",
    display_name="关于",
    permission="about",
    description="显示项目关于页面",
    usage="关于",
    category="系统功能",
)
@require_permission("about")
async def handle_about(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "关于")
    if args:
        raise_command_usage()

    page_url = create_about_page()
    logger.info(f"关于页面渲染：url_prefix={page_url[:80]}...")

    # MI-1.2：base64 size cap + semaphore 通过统一 helper 处理
    await render_and_send_screenshot(
        bot, event,
        page_url=page_url,
        options=ABOUT_SCREENSHOT_OPTIONS,
        file_prefix="about",
        semaphore=_about_semaphore,
        failure_action="生成",
    )
