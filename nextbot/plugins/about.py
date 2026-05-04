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
from nextbot.time_utils import beijing_filename_timestamp
from nextbot.text_utils import reply_failure
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url
from server.web_server import create_about_page

about_matcher = on_command("关于")

ABOUT_SCREENSHOT_OPTIONS = ScreenshotOptions(
    viewport_width=920,
    viewport_height=800,
    full_page=True,
    fit_content_height=True,
)


def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"


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
    logger.info(f"关于页面渲染地址：internal_url={page_url}")

    screenshot_path = Path("/tmp") / f"about-{beijing_filename_timestamp()}.png"
    try:
        await screenshot_url(
            page_url,
            screenshot_path,
            options=ABOUT_SCREENSHOT_OPTIONS,
        )
    except RenderScreenshotError as exc:
        await bot.send(event, reply_failure("生成", f"{exc}"))
        return

    logger.info(f"关于页面截图成功：file={screenshot_path}")
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
        except OSError:
            await bot.send(event, reply_failure("生成", "读取截图文件失败"))
            return
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
        return

    await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")
