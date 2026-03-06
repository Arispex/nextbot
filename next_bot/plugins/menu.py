import base64
from datetime import datetime
from pathlib import Path

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from next_bot.command_config import (
    command_control,
    list_command_configs,
    raise_command_usage,
)
from next_bot.message_parser import parse_command_args_with_fallback
from next_bot.permissions import require_permission
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_menu_page

menu_matcher = on_command("菜单")
MENU_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=1920,
    viewport_height=1280,
    full_page=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


@menu_matcher.handle()
@command_control(
    command_key="menu.root",
    display_name="菜单",
    permission="menu.root",
    description="显示命令菜单截图",
    usage="菜单",
)
@require_permission("menu.root")
async def handle_menu(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    args = parse_command_args_with_fallback(event, arg, "菜单")
    if args:
        raise_command_usage()

    command_items = list_command_configs()
    command_items.sort(key=lambda item: str(item.get("command_key", "")))

    render_commands: list[dict[str, str]] = []
    for item in command_items:
        render_commands.append(
            {
                "display_name": str(item.get("display_name", "")).strip(),
                "description": str(item.get("description", "")).strip(),
                "usage": str(item.get("usage", "")).strip(),
                "permission": str(item.get("permission", "")).strip(),
            }
        )

    page_url = create_menu_page(commands=render_commands)
    logger.info(
        "菜单渲染地址："
        f"command_count={len(render_commands)} "
        f"internal_url={page_url}"
    )

    screenshot_path = Path("/tmp") / (
        f"menu-{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
    )
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=MENU_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, f"生成失败，{exc}")
        return

    logger.info(
        f"菜单截图成功：command_count={len(render_commands)} file={screenshot_path}"
    )
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, "生成失败，读取截图文件失败")
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"截图成功，文件：{screenshot_path}")
