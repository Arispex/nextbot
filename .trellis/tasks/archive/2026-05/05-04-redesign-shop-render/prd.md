# Redesign 商店列表 / 查看商店 render per warm-canvas system

## Goal

按 `DESIGN.md` (Anthropic Claude.com warm-canvas editorial) 风格重构 `商店列表` (shop_list) 与 `查看商店` (shop_view) 两个截图模板，与已完成的 menu / user_info / red_packet / warehouse 等页面保持视觉一致。

## What I already know

- 现有模板 `server/templates/shop_list.html`、`server/templates/shop_view.html` 使用 Tailwind CDN + 双主题（dark / light）+ 卡片包裹层 + 渐变背景，与新规范不一致。
- `nextbot/plugins/shop.py` 已通过 `ScreenshotOptions(viewport_width, viewport_height, full_page=True)` 控制截图，需要补 `fit_content_height=True` 与已重构的页面对齐。
- 设计 token (`/assets/css/render-tokens.css`) 与字体 (`/assets/css/render-fonts.css`) 已就位，复用即可。
- 数据 payload 由 `server/web_server.py` 的 `create_shop_list_page` / `create_shop_view_page` 产出，本次重构不动 payload schema，仅消费现有字段。

## Requirements

- 移除 dark mode，统一使用 warm-canvas 浅色面板。
- 移除 Tailwind CDN，使用项目内 `render-tokens.css` + `render-fonts.css`。
- 去掉外层 `list-card` / `page-header` 卡片包裹，列表直接居于 canvas 上，每个 entry 是 cream-surface card。
- header 使用 text-hero 模式：coral rule + `商店系统` eyebrow + serif h1（shop_list 用「商店列表」常量；shop_view 用动态 shop_name）。
- meta 行用 type-body-sm + muted-soft，使用 `·` 分隔符。
- Tier chip 按 DESIGN.md 4-tier 语义映射（none/0–5 cream，6–10 teal，11–15 amber，16–20 primary coral）。
- kind pill：物品 = accent-teal outline，指令 = accent-amber outline。
- 价格、ID、计数等数字使用 Inter sans 600 + `font-feature-settings: "tnum"`；去掉 💰 emoji。
- 命令样例块用 mono 字体 + canvas 浅底 + hairline 边框。
- `nextbot/plugins/shop.py` 的两个 `ScreenshotOptions` 增加 `fit_content_height=True`。

## Acceptance Criteria

- [ ] 两个页面的截图与 menu / warehouse / red_packet 等已重构页面视觉风格一致。
- [ ] 不含任何 `data-theme="dark"` 分支。
- [ ] 不引用 Tailwind CDN，仅引用 `/assets/css/render-fonts.css` 与 `/assets/css/render-tokens.css`。
- [ ] payload schema 保持不变，所有现有字段正确渲染。
- [ ] 页面截图自适应内容高度（无底部空白）。

## Out of Scope

- payload schema 修改（如新增 `user_coins` 显示）。
- 其它 shop 相关命令（购买商品 / 商店管理 webui）。
- 其它截图页面（背包 / 抽奖 / 排行榜 等留待后续任务）。

## Technical Notes

- 沿用 warehouse / red_packet 重构建立的 tier chip 4-tier 配色与 cream-surface card 模式。
- `fit_content_height` 实现位于 `server/screenshot.py`，已支持 measure `body.getBoundingClientRect().bottom`。
