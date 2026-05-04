# 截图 Timeout 修复 + Playwright 性能优化

## Goal

`server/screenshot.py` 的截图功能偶发 `Timeout 15000ms`，与设备性能无关。根因是 `wait_until="networkidle"` 在现代浏览器里不稳定（500ms 无网络才算空闲，任何长尾网络抖动都可能让它超过 15s）。同时每次截图都 cold-launch Chromium，约 1-2s 浪费。

本任务一次性解决两件事：① 用更确定性的方式等待页面就绪，根除 Timeout；② 浏览器单例复用，把单次截图整体时延降低 1-2s。

## Requirements

### A. 等待策略改造（修 Timeout）

1. `ScreenshotOptions.wait_until` 默认值 `"networkidle"` → `"load"`
2. `ScreenshotOptions.timeout_ms` 默认值 `15000` → `30000`（防御性留余量，正常路径用不到）
3. `page.goto` 之后，新增 `await page.evaluate("document.fonts.ready")` 显式等字体加载完成（替代 networkidle 的隐式字体等待，确定性、有 promise 边界）
4. 现有调用方零改动 —— 都依赖默认值

### B. 浏览器单例复用（性能）

1. 模块级单例 `_PlaywrightSession`，持有 `playwright` + `browser`，懒初始化（首次截图时启动）
2. 每次截图：复用 browser，新建 `BrowserContext` + `Page`（contexts 互相隔离，创建只需 ~50ms）；用完关闭 context（不关 browser）
3. 并发安全：用 `asyncio.Lock` 串行化 launch/close 操作；`new_context` 本身线程安全可并行
4. 健壮性：捕获 `BrowserClosedError` 等异常 → 自动重启 browser 并对当前请求重试一次；二次失败才抛 `RenderScreenshotError`
5. 进程退出 hook：注册 `atexit` 或 NoneBot driver shutdown 钩子，干净关闭 playwright 实例
6. Chromium launch 参数加上 `args=["--disable-dev-shm-usage"]`（防 Linux /dev/shm 太小导致崩溃，对 macOS 无副作用）

### C. 错误信息改进

`RenderScreenshotError` 的 `__cause__` 已经是原始异常，但当前 `f"截图失败：{exc}"` 拼接的字符串损失了上下文。改为：
- 区分 navigation timeout / fonts ready timeout / screenshot capture failure 三类，错误 message 带具体阶段名

## Acceptance Criteria

- [ ] 多次跑同一截图命令（如 `菜单 系统功能` × 10），无任何 Timeout
- [ ] 第二次及之后的截图比第一次显著加快（单例命中，省去 Chromium cold-launch）
- [ ] 截图内容视觉无回归：字体、布局、`fit_content_height` 都正常
- [ ] Browser 崩溃模拟（手动 kill chromium 进程后再次截图）→ 自动重启成功，仅看到一行 logger.warning
- [ ] 进程退出时无 "browser was not closed" 警告
- [ ] 所有现有调用方（`menu.py` / `lottery.py` / `warehouse.py` / 等 12 个文件）零改动

## Definition of Done

- 上述 AC 全部通过
- 现有 `ScreenshotOptions` 数据类签名兼容（仅默认值变更，字段不删不重命名）
- 不引入新依赖
- 日志风格沿用项目惯例（参见 `.trellis/spec/backend/logging-guidelines.md`）

## Technical Approach

### 改造后 `server/screenshot.py` 骨架

```python
class _PlaywrightSession:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage"],
                )
            return self._browser

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

_session = _PlaywrightSession()


async def screenshot_url(url, output_path, *, options=None):
    render_options = options or ScreenshotOptions()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    last_exc = None
    for attempt in (1, 2):  # 单次重试
        try:
            browser = await _session.get_browser()
            context = await browser.new_context(viewport={...})
            try:
                page = await context.new_page()
                await page.goto(url, wait_until=render_options.wait_until, timeout=render_options.timeout_ms)
                await page.evaluate("document.fonts.ready")
                if render_options.fit_content_height:
                    ...
                else:
                    await page.screenshot(path=str(output_path), full_page=render_options.full_page)
                return
            finally:
                await context.close()
        except (BrowserClosedError, ConnectionResetError) as exc:
            logger.warning(f"截图浏览器异常，准备重启 attempt={attempt} err={exc}")
            await _session.close()
            last_exc = exc
            continue
        except PlaywrightTimeoutError as exc:
            raise RenderScreenshotError(f"截图导航超时：{exc}") from exc
        except Exception as exc:
            raise RenderScreenshotError(f"截图失败：{exc}") from exc
    raise RenderScreenshotError(f"截图失败（多次重试）：{last_exc}")
```

### 进程退出 hook

加在 `bot.py`（或 `server/screenshot.py` 模块级 `atexit.register`）：

```python
import atexit
atexit.register(lambda: asyncio.get_event_loop().run_until_complete(_session.close()))
```

或 NoneBot2 driver hook（更优雅）。

## Decision (ADR-lite)

**Context**：选 networkidle vs load 等待策略；选每次 cold-launch vs 单例复用；选浏览器复用 vs 上下文复用

**Decision**：
- `wait_until="load"` + `document.fonts.ready` —— 确定性 + 字体保证
- 单例 browser + 每次新 context —— 兼顾性能与隔离
- `asyncio.Lock` 仅保护 launch/close —— 截图本身可并发
- 单次自动重试 —— 区分"瞬时崩溃"与"持续失败"

**Consequences**：
- 优点：消除 Timeout 失败、单次截图省 1-2s、首次仍冷启动但只发生一次
- 缺点：Browser 长期驻留约 100MB 内存（NoneBot 进程已数百 MB，可忽略）；新增并发锁需小心 deadlock

## Out of Scope

- 不做 page pool（context 复用够轻量）
- 不做截图结果缓存
- 不重写调用方的 ScreenshotOptions 用法
- 不做 WebUI 截图
- 不引入 Playwright 之外的截图方案

## Technical Notes

- 文件：`server/screenshot.py`（80 行）
- 现有调用方：12 个 `nextbot/plugins/*.py` 文件（grep `screenshot_url` 验证）
- Playwright 文档明确标注 networkidle 为 DISCOURAGED
- `document.fonts.ready` 是 W3C 标准 promise，Chrome 35+ 支持
- `--disable-dev-shm-usage` 是官方推荐 flag（Docker/CI 经典坑）
- NoneBot2 driver shutdown hook 参考：`from nonebot import get_driver; get_driver().on_shutdown(...)`
