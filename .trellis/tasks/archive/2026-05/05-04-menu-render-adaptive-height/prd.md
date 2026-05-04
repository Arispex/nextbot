# Menu Render Adaptive Height

## Goal

让「菜单」截图根据命令数量自适应高度，命令少时不要在卡片下方留大片空白。

## Root Cause

[server/templates/menu.html:24](../../../server/templates/menu.html) 的 `min-height: 100vh` 强制 body 至少占满视口（1280px），即使只有 2 张卡片实际内容只有 ~300px，body 仍然撑到 1280px。Playwright `full_page=True` 截屏会把这 1280px 全部抓下来，造成下方大片空白。

## Fix

两步：

1. **CSS 层**：删除 menu.html body 的 `min-height: 100vh` 一行（必要但不充分，因为 Playwright `full_page=True` 实际是 `max(viewport_height, content_height)`，光改 CSS 截图仍会被视口 1280 撑高）
2. **screenshot 工具层**：给 `server/screenshot.py` `ScreenshotOptions` 加 `fit_content_height: bool = False` 选项；启用时截图前 `set_viewport_size` 到实际内容高度，再普通 `screenshot()`（不 full_page），让截图严格等于内容高度。**默认 False 保持其他 16 个截图页行为不变**
3. **菜单启用**：`nextbot/plugins/menu.py` 的 `MENU_SCREENSHOT_OPTIONS` 加 `fit_content_height=True`

96px 的 `padding-bottom`（`--space-section`）保留 — 这是编辑式留白，给 footer 与最后一行卡片之间合理喘息空间，不会显得拥挤。

## Acceptance Criteria

- [ ] 1–2 张命令卡时，截图高度紧贴内容（约 = 96px header padding + header 高 + 卡片高 + 96px footer padding，~600–700px 量级）
- [ ] 8 张命令卡时（如「仓库系统」），3 列布局下高度不变（约 1000px）
- [ ] 极多命令（>15 张）时仍然能 full_page 截全
- [ ] header / footer / 卡片间距视觉手感不变

## Out of Scope

- 其他 16 个截图页（仓库 / 商店 / 抽奖等）的同类问题 — 留待后续公共 CSS 推平时一并处理
- 视口宽度 / `viewport_height` 调整 — viewport_height 只影响 Playwright 初始视口，`full_page=True` 已经在按内容高度截了

## Technical Notes

文件：`server/templates/menu.html` 单行删除。

预期 diff：
```diff
     body {
       background-color: var(--color-canvas);
       color: var(--color-ink);
       font-family: var(--font-body);
       font-size: var(--type-body-md-size);
       line-height: 1.55;
-      min-height: 100vh;
       padding: var(--space-section) var(--space-xxl);
       -webkit-font-smoothing: antialiased;
       -moz-osx-font-smoothing: grayscale;
     }
```
