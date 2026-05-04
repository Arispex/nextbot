# Redesign 封禁列表 / 管理员列表 render per warm-canvas system

## Goal

按 `DESIGN.md` (Anthropic Claude.com warm-canvas editorial) 风格重构 `ban_list.html`（封禁列表）与 `admin_list.html`（管理员列表）两个截图模板，与已重构页面保持视觉一致。

## What I already know

- `ban_list.html` 默认 dark theme + Tailwind CDN + 红色金属渐变 + index 大数字水印（视觉过载）。
- `admin_list.html` 默认 light theme + Tailwind CDN + 紫色玻璃质感 hero + 3-列 admin grid 卡片。
- 两个 plugin 配置：`BAN_LIST_SCREENSHOT_OPTIONS` (900×800) 与 `ADMIN_LIST_SCREENSHOT_OPTIONS`，未启用 `fit_content_height`。
- 真实 category 映射：封禁列表 = `安全管理`，管理员列表 = `权限管理`。

## Requirements

- 移除 dark mode 与 Tailwind CDN，改用项目内 token + 字体 CSS。
- 去掉外层 `page-header` / `main-card` 包裹层；list / grid 直接居于 canvas 上。
- header 改为 text-hero：
  - ban_list：eyebrow `安全管理`、h1 `封禁列表`
  - admin_list：eyebrow `权限管理`、h1 `管理员列表`
- meta 行：count + 页码 (如适用) + 时间，`·` 分隔
- ban_list rows：cream-card row，每行含 avatar (48px) + name + QQ + reason（用 primary coral 强调"封禁原因"语义）+ time；移除 index 大数字水印
- admin grid cards：cream-card，每张含 avatar (80px) + name + QQ + `Owner` cream-pill badge；保留 3-列网格
- 数字（QQ ID 等）用 mono 字体 (`var(--font-code)`)
- `BAN_LIST_SCREENSHOT_OPTIONS` / `ADMIN_LIST_SCREENSHOT_OPTIONS` 增加 `fit_content_height=True`；ban_list 宽度 900→920 与其它列表对齐
- 加全局 `[hidden] { display: none !important; }` 守卫

## Acceptance Criteria

- [ ] 两个页面与已重构页面视觉风格一致。
- [ ] 不含 `data-theme="dark"` 分支与 Tailwind CDN。
- [ ] 不含 index 大数字水印。
- [ ] payload schema 完全保持不变。
- [ ] 截图自适应内容高度。

## Out of Scope

- payload schema 修改、新增 admin 角色（保留 Owner）。
