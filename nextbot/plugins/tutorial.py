from __future__ import annotations

import base64
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from nextbot.command_config import command_control, raise_command_usage
from nextbot.message_parser import parse_command_args_with_fallback
from nextbot.permissions import require_permission
from nextbot.plugins.tutorial_data import get_tutorial, list_tutorials
from nextbot.text_utils import EMOJI_GUIDE, reply_failure, reply_list
from nextbot.screenshot_temp import temp_screenshot_path
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_tutorial_page

tutorial_matcher = on_command("使用教程")

TUTORIAL_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=1400,
    full_page=True,
    fit_content_height=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


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
    tutorials = list_tutorials()

    if not tutorials:
        await bot.send(event, reply_failure("查询", "暂无可用教程"))
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
            reply_failure("查询", "未找到该教程，发送「使用教程」查看所有教程"),
        )
        return

    user_id = event.get_user_id()

    page_url = create_tutorial_page(
        tutorial=target,
        self_user_id=user_id,
    )
    logger.info(
        f"使用教程渲染地址：slug={target.get('slug')} user_id={user_id} internal_url={page_url}"
    )

    async with temp_screenshot_path("tutorial") as screenshot_path:
        try:
            await screenshot_url(
                page_url, screenshot_path, options=TUTORIAL_SCREENSHOT_OPTIONS,
            )
        except RenderScreenshotError as exc:
            await bot.send(event, reply_failure("生成", str(exc)))
            return

        logger.info(
            f"使用教程截图成功：slug={target.get('slug')} file={screenshot_path}"
        )
        if bot.adapter.get_name() == "OneBot V11":
            try:
                image_uri = _to_base64_image_uri(screenshot_path)
            except OSError:
                await bot.send(event, reply_failure("生成", "读取截图文件失败"))
                return
            await bot.send(event, OBV11MessageSegment.image(file=image_uri))
            return

        await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")
