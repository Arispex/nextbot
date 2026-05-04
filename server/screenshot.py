from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class RenderScreenshotError(Exception):
    pass


WaitUntilState = Literal["commit", "domcontentloaded", "load", "networkidle"]


@dataclass(frozen=True)
class ScreenshotOptions:
    viewport_width: int = 2000
    viewport_height: int = 1000
    wait_until: WaitUntilState = "networkidle"
    timeout_ms: int = 15000
    full_page: bool = True
    # When True, after the page loads, resize the viewport to match the actual
    # document body height so the screenshot is exactly content-tall (no
    # below-content whitespace from `full_page=True` falling back to viewport
    # height when content is shorter than viewport). Off by default to keep
    # behavior identical for pages that rely on a fixed viewport for layout.
    fit_content_height: bool = False


async def screenshot_url(
    url: str,
    output_path: Path,
    *,
    options: ScreenshotOptions | None = None,
) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RenderScreenshotError(
            "未安装 playwright，请先执行：uv add playwright && uv run playwright install chromium"
        ) from exc

    render_options = options or ScreenshotOptions()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={
                    "width": render_options.viewport_width,
                    "height": render_options.viewport_height,
                }
            )
            await page.goto(
                url,
                wait_until=render_options.wait_until,
                timeout=render_options.timeout_ms,
            )
            if render_options.fit_content_height:
                # Use body.getBoundingClientRect().bottom — this is the only
                # measurement that ignores the html element's implicit
                # viewport-fill behavior. document(Element).scrollHeight returns
                # max(body, viewport_height), defeating the whole purpose.
                content_height = await page.evaluate(
                    "Math.ceil(document.body.getBoundingClientRect().bottom)"
                )
                fit_height = max(int(content_height), 1)
                await page.set_viewport_size(
                    {"width": render_options.viewport_width, "height": fit_height}
                )
                # full_page would re-introduce the viewport-as-min-height
                # behavior we just escaped; with the viewport already matching
                # content, a plain viewport screenshot is exactly content-tall.
                await page.screenshot(path=str(output_path), full_page=False)
            else:
                await page.screenshot(
                    path=str(output_path), full_page=render_options.full_page
                )
            await browser.close()
    except Exception as exc:
        raise RenderScreenshotError(f"截图失败：{exc}") from exc
