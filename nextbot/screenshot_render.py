"""截图发送统一入口。

把 "`temp_screenshot_path` + `screenshot_url` + V11 base64 / 非 V11 fallback +
size cap + per-handler semaphore" 这条链聚合到一个 helper，避免 leaderboard /
lottery / about / tutorial / menu / ban / permission_manager 各写一份。

调用方按业务持有自己的 module-level `asyncio.Semaphore(N)`（不同业务隔离），
传入本 helper；本 helper 内部负责：
  1. semaphore 内执行（防 OOM 放大）
  2. screenshot_url RenderScreenshotError → reply_failure(action, str(exc))
  3. base64 编码前后双重 size 校验（防极端边界）
  4. V11 走 base64://，非 V11 走文件名 + 大小 fallback（不暴露 /tmp 路径）

设计上不强制让 ban / permission_manager 立刻迁移：现网已有自己的 size cap +
semaphore 实现，迁移属于额外清理工作，由后续单独 task 处理。
"""

from __future__ import annotations

import asyncio
import base64

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
from nonebot.log import logger

from nextbot.large_image import MAX_BASE64_BYTES
from nextbot.screenshot_temp import temp_screenshot_path
from nextbot.text_utils import reply_block, reply_failure, reply_success
from server.screenshot import RenderScreenshotError, ScreenshotOptions, screenshot_url


async def render_and_send_screenshot(
    bot: Bot,
    event: Event,
    *,
    page_url: str,
    options: ScreenshotOptions,
    file_prefix: str,
    semaphore: asyncio.Semaphore | None = None,
    failure_action: str = "查询",
    success_caption: str | None = None,
) -> bool:
    """生成截图并发送到 V11 / 非 V11 适配器。

    Args:
        bot: nonebot Bot 实例。
        event: 触发命令的事件。
        page_url: 渲染目标页面 URL（通常由 web_server.create_*_page 返回）。
        options: 截图参数（viewport / full_page / fit_content_height）。
        file_prefix: 截图临时文件前缀，传入 `temp_screenshot_path`，
            内部会追加时间戳 + uuid 后缀避免并发碰撞。
        semaphore: 调用方 module-level Semaphore，限制本业务并发渲染数量。
            None 时不加锁（仅用于不需要限流的低频命令）。
        failure_action: 失败回复使用的动作动词（如 "查询" / "抽奖" / "生成"），
            被 `reply_failure(action, reason)` 拼成 "❌ <action>失败，<reason>"。
        success_caption: 非 V11 适配器的成功提示语，None 时使用默认 "截图生成成功"。

    Returns:
        True 表示成功发送（无论 V11 还是 fallback），False 表示失败（已经
        通过 reply_failure 通知用户）。

    Failure modes（全部已用 reply_failure 通知用户）:
        - RenderScreenshotError → reply_failure(action, str(exc))
        - screenshot 文件 stat 失败 → reply_failure(action, "读取截图文件失败")
        - 文件大小预估超过 MAX_BASE64_BYTES → reply_failure(action, "截图过大")
        - read_bytes / b64encode OSError → reply_failure(action, "读取截图文件失败")
        - base64 编码后再次超阈 → reply_failure(action, "截图过大")
    """
    if semaphore is None:
        return await _render_and_send_inner(
            bot, event, page_url=page_url, options=options,
            file_prefix=file_prefix, failure_action=failure_action,
            success_caption=success_caption,
        )
    async with semaphore:
        return await _render_and_send_inner(
            bot, event, page_url=page_url, options=options,
            file_prefix=file_prefix, failure_action=failure_action,
            success_caption=success_caption,
        )


async def _render_and_send_inner(
    bot: Bot,
    event: Event,
    *,
    page_url: str,
    options: ScreenshotOptions,
    file_prefix: str,
    failure_action: str,
    success_caption: str | None,
) -> bool:
    async with temp_screenshot_path(file_prefix) as screenshot_path:
        try:
            await screenshot_url(page_url, screenshot_path, options=options)
        except RenderScreenshotError as exc:
            await bot.send(event, reply_failure(failure_action, str(exc)))
            return False

        try:
            file_size = screenshot_path.stat().st_size
        except OSError:
            await bot.send(event, reply_failure(failure_action, "读取截图文件失败"))
            return False

        # base64 编码后体积约为原始字节的 4/3
        if file_size * 4 // 3 > MAX_BASE64_BYTES:
            logger.warning(
                f"截图过大：file_prefix={file_prefix} size_bytes={file_size}"
            )
            await bot.send(event, reply_failure(failure_action, "截图过大"))
            return False

        if bot.adapter.get_name() == "OneBot V11":
            try:
                raw = screenshot_path.read_bytes()
                encoded = base64.b64encode(raw).decode("ascii")
            except OSError:
                await bot.send(event, reply_failure(failure_action, "读取截图文件失败"))
                return False
            if len(encoded) > MAX_BASE64_BYTES:
                logger.warning(
                    f"截图 base64 编码后过大：file_prefix={file_prefix} encoded_len={len(encoded)}"
                )
                await bot.send(event, reply_failure(failure_action, "截图过大"))
                return False
            await bot.send(event, OBV11MessageSegment.image(file=f"base64://{encoded}"))
            return True

        # 非 V11 fallback：避免暴露 /tmp 内部路径，只回文件名 + 大小
        size_kb = file_size // 1024
        # SH-4.2：默认 caption 改为 "截图已生成"，避免 reply_success 拼出
        # "生成成功，截图生成成功" 这种 "动作 + 结果，结果" 的重复文案。
        await bot.send(
            event,
            reply_block(
                reply_success(failure_action, success_caption or "截图已生成"),
                [
                    f"📁 文件：{screenshot_path.name}",
                    f"📦 大小：{size_kb} KB",
                ],
            ),
        )
        return True
