# fix(webui): 还原 app_shell 侧边 logo 颜色 + header 全宽两侧贴边布局

## 背景

最近 `refactor(webui): full audit fixes`（commit `6995d3c`）按 audit Low-2 / Medium-8 改了两处样式：

1. **`app-shell.css:215`**：`.brand-logo-svg { color: var(--color-ink); }` → `color: var(--text);`
2. **`app-shell.css:311-318`**：新增 `.app-header-inner { max-width: 1180px; margin: 0 auto; ... }`，并把 header 内容包到该 wrapper 里

用户实际使用反馈：
- 改 logo 颜色后 brand 视觉不对（之前是 coral 色 / `--primary`，现在是 ink 黑），希望还原。
- header 居中后超宽屏下"开关侧边栏 / 页面标题 / GitHub / 主题 / 退出"会跑到中间，不再左右贴两侧，希望还原成全宽。

## 范围

仅 2 个文件，1 个 CSS + 1 个 HTML（如果决定移除 wrapper）：
- `server/webui/static/css/app-shell.css`
- `server/webui/templates/app_shell_base.html`（仅 wrapper 选择会改）

## 修改方案

### Logo 颜色
`app-shell.css:211-216`：
```css
.brand-logo-svg {
  display: block;
  height: 26px;
  width: auto;
  color: var(--primary);  /* 还原为 coral，与之前继承 link color 的视觉一致 */
}
```

### Header 全宽
两选一，取 A（保留 HTML wrapper，只改 CSS 让 wrapper 透明）：

**方案 A（最小 CSS 改）**：
`app-shell.css:311-318`：
```css
.app-header-inner {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}
```
删 `max-width: 1180px;` 和 `margin: 0 auto;`。

注：`.app-header` 内已有 `padding: 0 var(--space-xl)`，配合 `.header-actions` 的 `margin-left: auto`（如果有的话）即可保证左右贴边。需检查 `.header-actions` 是否能 push 到右边 —— 如果不能，加一条 `.header-actions { margin-left: auto; }`。

## Acceptance Criteria

- 侧边 logo 还原 coral 色
- 超宽屏下 header 左侧 group（toggle + 页面标题）紧贴左边距，右侧 group（GitHub / 主题 / 退出）紧贴右边距
- 不影响 `.app-content` 的 1180px max-width（主内容仍可保持居中）
- 不破坏 mobile 响应式（header 在 760px 以下仍 OK）

## Out of Scope

- 主内容区 `.app-content` 的 max-width 不动
- 其他菜单 / 颜色 / 间距不动
- audit Low-2 / Medium-8 的判断在 spec 上是合理的，但用户偏好优先 — 不再重提
