from __future__ import annotations

import asyncio
import atexit
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from nonebot.log import logger

if TYPE_CHECKING:
    from playwright.async_api import Browser

class _PlaywrightUnavailable(Exception):
    """Sentinel exception used as a stand-in when playwright cannot be imported.

    Keeps ``except (PlaywrightError, ...)`` clauses syntactically valid, but
    distinct from ``Exception`` so the broader catch-all clause stays reachable
    in the eyes of static analyzers.
    """


try:
    from playwright.async_api import (
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )

    _PLAYWRIGHT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover  # noqa: BLE001
    PlaywrightError = _PlaywrightUnavailable  # type: ignore[assignment,misc]
    PlaywrightTimeoutError = _PlaywrightUnavailable  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_IMPORT_ERROR = exc


class RenderScreenshotError(Exception):
    pass


WaitUntilState = Literal["commit", "domcontentloaded", "load", "networkidle"]


@dataclass(frozen=True)
class ScreenshotOptions:
    viewport_width: int = 2000
    viewport_height: int = 1000
    wait_until: WaitUntilState = "load"
    timeout_ms: int = 30000
    full_page: bool = True
    # When True, after the page loads, resize the viewport to match the actual
    # document body height so the screenshot is exactly content-tall (no
    # below-content whitespace from `full_page=True` falling back to viewport
    # height when content is shorter than viewport). Off by default to keep
    # behavior identical for pages that rely on a fixed viewport for layout.
    fit_content_height: bool = False


# M-7：默认加固。
# - ``--disable-dev-shm-usage``：避免 docker /dev/shm 太小导致渲染 OOM
# - ``--disable-gpu``：无头模式无需 GPU，减少 VRAM 占用
# - ``--disable-extensions`` / ``--disable-background-networking``：禁掉非业务网络请求
# - ``--disable-features=Translate,InterestCohort``：关闭冷门 / 隐私敏感子系统
_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-features=Translate,InterestCohort",
]

# H-5：截图 URL 白名单 host。``screenshot_url`` 是 module-level public API，
# 任何 caller（含未来 plugin）传入非 loopback URL 都会被 fail-closed 拒绝，
# 防止 Playwright 携带本机网络访问内网 / 元数据 endpoint / file:// 协议读本地文件。
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _assert_local_url(url: str) -> None:
    """H-5：校验 ``url`` 仅指向 loopback host + http/https scheme。

    不通过则 raise ``RenderScreenshotError``；调用方应在请求 Playwright 前
    显式调用本函数。
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise RenderScreenshotError(
            f"截图 URL 协议不在白名单：scheme={scheme!r} url={url!r}"
        )
    hostname = (parsed.hostname or "").lower()
    if hostname not in _ALLOWED_HOSTS:
        raise RenderScreenshotError(
            f"截图 URL 主机不在白名单：host={hostname!r} url={url!r}"
        )


class _PlaywrightSession:
    """Module-level singleton holding a long-lived Playwright + Chromium browser.

    The lock only serializes launch/close, not screenshots themselves —
    BrowserContext creation is concurrency-safe and is what each screenshot uses
    for isolation.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        if _PLAYWRIGHT_IMPORT_ERROR is not None or async_playwright is None:
            raise RenderScreenshotError(
                "未安装 playwright，请先执行：uv add playwright && uv run playwright install chromium"
            ) from _PLAYWRIGHT_IMPORT_ERROR
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=_LAUNCH_ARGS,
            )
            # L-6：补充浏览器实例 id 作运维上下文，方便日志检索定位单次启动周期
            logger.info(
                f"截图浏览器启动完成，浏览器实例={id(self._browser)} 启动参数={_LAUNCH_ARGS}"
            )
            return self._browser

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                # Best-effort: browser may already be dead.
                with contextlib.suppress(Exception):
                    await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                with contextlib.suppress(Exception):
                    await self._playwright.stop()
                self._playwright = None
            logger.info("截图浏览器已关闭")


_session = _PlaywrightSession()


async def screenshot_url(
    url: str,
    output_path: Path,
    *,
    options: ScreenshotOptions | None = None,
) -> None:
    # H-5：入口处 URL 白名单校验；非 loopback / 非 http(s) 一律 fail-closed。
    _assert_local_url(url)

    render_options = options or ScreenshotOptions()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # M-9：保证 retry 全失败时仍有可读 last_exc 文案，不会出现 "...：None"。
    last_exc: Exception = RenderScreenshotError("截图失败：未知原因")
    for attempt in (1, 2):
        try:
            browser = await _session.get_browser()
            context = await browser.new_context(
                viewport={
                    "width": render_options.viewport_width,
                    "height": render_options.viewport_height,
                }
            )
            try:
                page = await context.new_page()
                try:
                    await page.goto(
                        url,
                        wait_until=render_options.wait_until,
                        timeout=render_options.timeout_ms,
                    )
                except PlaywrightTimeoutError as exc:
                    raise RenderScreenshotError(
                        f"截图导航超时（{render_options.wait_until} > {render_options.timeout_ms}ms）：{exc}"
                    ) from exc

                # Plan A 的 load 只等 HTML 同步资源；JS fetch 后动态 createElement('img') 的 sprite
                # 不在等待范围。下面两道补丁覆盖动态资源场景，避免回退到不稳定的 networkidle。
                # L-5：networkidle 超时降为 DEBUG（频繁触发不应噪音），但保留可见性供性能调优定位。
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeoutError as exc:
                    logger.debug(
                        f"截图等待 networkidle 超时，继续渲染：timeout=5000ms reason={exc}"
                    )
                await page.evaluate(
                    """
                    () => Promise.all(
                      Array.from(document.images).map(img =>
                        img.complete && img.naturalHeight > 0
                          ? Promise.resolve()
                          : new Promise(resolve => {
                              img.addEventListener('load',  resolve, { once: true });
                              img.addEventListener('error', resolve, { once: true });
                            })
                      )
                    )
                    """
                )

                try:
                    await page.evaluate(
                        "document.fonts ? document.fonts.ready : Promise.resolve()"
                    )
                except PlaywrightTimeoutError as exc:
                    raise RenderScreenshotError(
                        f"截图等待字体加载超时：{exc}"
                    ) from exc

                try:
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
                            {
                                "width": render_options.viewport_width,
                                "height": fit_height,
                            }
                        )
                        # full_page would re-introduce the viewport-as-min-height
                        # behavior we just escaped; with the viewport already matching
                        # content, a plain viewport screenshot is exactly content-tall.
                        await page.screenshot(path=str(output_path), full_page=False)
                    else:
                        await page.screenshot(
                            path=str(output_path),
                            full_page=render_options.full_page,
                        )
                except PlaywrightTimeoutError as exc:
                    raise RenderScreenshotError(
                        f"截图采集超时：{exc}"
                    ) from exc
                return
            finally:
                # Context may already be gone if browser crashed.
                with contextlib.suppress(Exception):
                    await context.close()
        except RenderScreenshotError:
            # Deterministic failure (timeout / capture failure) — no retry.
            raise
        except (PlaywrightError, ConnectionResetError) as exc:
            logger.warning(
                f"截图浏览器异常，准备重启第 {attempt} 次：{exc}"
            )
            await _session.close()
            last_exc = exc
            continue
        except Exception as exc:
            raise RenderScreenshotError(f"截图失败：{exc}") from exc

    raise RenderScreenshotError(
        f"截图失败（重启浏览器后仍未恢复）：{last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Lifecycle hooks: ensure browser shuts down cleanly on process exit.
# ---------------------------------------------------------------------------


def _atexit_close() -> None:
    """Best-effort sync close on interpreter shutdown.

    atexit runs in sync context; the running loop (if any) is usually closed by
    now. We spin up a fresh loop just for the close call. Any failure here is
    silenced — the process is exiting anyway.
    """
    with contextlib.suppress(Exception):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_session.close())
        finally:
            loop.close()


atexit.register(_atexit_close)


# Prefer NoneBot's shutdown hook when available — it runs while the loop is
# still alive, giving the browser a chance to close gracefully before atexit's
# fallback. Wrapped in try/except so this module stays importable outside
# NoneBot (e.g. CI, ad-hoc scripts).
with contextlib.suppress(Exception):
    from nonebot import get_driver

    get_driver().on_shutdown(_session.close)
