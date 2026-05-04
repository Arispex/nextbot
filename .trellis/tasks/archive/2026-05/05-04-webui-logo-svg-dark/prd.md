# WebUI 侧栏 logo 切换为 SVG 并适配暗色

## Goal

WebUI 侧栏现在用两张带背景色的 PNG（白底黑字 / 黑底白字）通过 `html.dark` class 切换，导致 logo 周围有方块背景色，视觉上不"贴"侧栏底色。改用矢量 SVG 配合 `currentColor`，让 logo 颜色跟随主题、消除背景色块。

## Requirements

* 把 `app_shell_base.html` 里 `.sider-head > .brand-logo-link` 内的两张 `<img>` 替换为单段 inline `<svg>`
* 该 inline SVG 的 `fill="currentColor"`，颜色由父级 `color` 决定
* 同时把 `server/webui/static/img/logo.svg` 文件本体也改成 `fill="currentColor"`，保证文件单独打开 / 别处引用时也能 work（浏览器默认 color 为黑，行为不变）
* CSS 上：
  - 浅色模式下 logo 显示为 `var(--color-ink)`（深墨色）
  - `html.dark` 下显示为 `var(--color-ink)`（已是浅色 `#f5f4f0`），无需写双套规则
  - 高度 36px、宽度 auto，与原来一致
* 删除 `.brand-logo-image-light` / `.brand-logo-image-dark` 这两条 CSS（不再使用）
* 两张老 PNG 文件**保留**（`logo__white_background_with_black_text.png` / `logo__black_background_with_white_text.png`），不删除以保证向后兼容（截图渲染等场景仍可能引用）

## Acceptance Criteria

* [ ] WebUI 侧栏顶部显示新的 NEXT BOT logo
* [ ] 切换浅色 / 暗色主题时，logo 颜色随之翻转，且周围**没有方块背景色**
* [ ] DOM 检查：`.sider-head` 内只有一个 `<svg>` 而不是两个 `<img>`
* [ ] `logo.svg` 单独在浏览器打开仍能看到黑色字形
* [ ] 现有暗色切换 `html.dark` 路径无回归

## Definition of Done

* 三处文件均完成且本地浏览验证通过
* CSS 不残留无用规则

## Technical Approach

1. **`server/webui/static/img/logo.svg`**：把 `<g fill="#0d0c0a" ...>` 改为 `<g fill="currentColor" ...>`
2. **`server/webui/templates/app_shell_base.html`**：把两个 `<img class="brand-logo-image brand-logo-image-light/dark" ...>` 替换为 inline SVG（直接复制 `logo.svg` 的内容），加 `class="brand-logo-svg"`、保留 `aria-label`
3. **`server/webui/static/css/app-shell.css`**：
   - 删除 `.brand-logo-image` / `.brand-logo-image-light` / `.brand-logo-image-dark` 三条规则及其 `html.dark` 覆盖
   - 新增 `.brand-logo-svg { display: block; height: 36px; width: auto; color: var(--color-ink); }`

## Decision (ADR-lite)

**Context**：三种方案：(a) 双 SVG 文件 + display 切换；(b) 外链 SVG + CSS mask-image；(c) inline SVG + currentColor
**Decision**：(c) inline + currentColor
**Consequences**：HTML 多 ~40 行（SVG 8 个 path），但消除 HTTP 请求、消除两份重复资源、theming 走 CSS 一条线；标准做法、易维护

## Out of Scope

* 不删除两张老 PNG
* 不改动 favicon / login 页 logo（如有）
* 不接入 SVG sprite / icon system
* 不调整侧栏其他视觉

## Technical Notes

* `html.dark` class 由 `server/webui/static/js/theme-init.js` 在初始化时附加、由 `webui.js` 的 toggle 切换
* `var(--color-ink)` 在 `:root` 是深墨色、在 `html.dark` 自动变浅，token 已是 theme-aware
* logo.svg 的 `viewBox="0 0 1436.5 187.63"` 决定 width:height ≈ 7.66:1，36px 高 → ~276px 宽
* 现有 logo 引用位置：`app_shell_base.html:18-21`
* CSS 现有规则：`server/webui/static/css/app-shell.css:188-199`
