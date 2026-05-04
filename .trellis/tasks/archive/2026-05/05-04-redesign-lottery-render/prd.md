# Redesign 查看奖池 / 抽奖 / 奖池列表 render per warm-canvas system

## Goal

按 `DESIGN.md` (Anthropic Claude.com warm-canvas editorial) 风格重构 `奖池列表` (lottery_list)、`查看奖池` (lottery_view)、`抽奖` 结果 (lottery_result) 三个截图模板，与已完成的 menu / user_info / red_packet / warehouse / shop 等页面保持视觉一致。

## What I already know

- 现有模板 `server/templates/lottery_{list,view,result}.html` 使用 Tailwind CDN + 双主题（dark / light）+ 多色渐变背景 + 紫粉色调 (purple/pink)，与新规范不一致。
- `nextbot/plugins/lottery.py` 的 3 个 `ScreenshotOptions` 都未启用 `fit_content_height`，需要补上。
- 设计 token (`/assets/css/render-tokens.css`) 与字体已就位，复用即可。
- payload schema 不动，仅替换前端渲染。

## Requirements

- 移除 dark mode 与 Tailwind CDN，改用 `render-tokens.css` + `render-fonts.css`。
- 去掉外层 `page-header` 与 `list-card` 卡片包裹层，header 直接居于 canvas 上。
- header 统一 text-hero 模式：coral rule + `奖池系统` eyebrow + serif h1
  - lottery_list h1 = 「奖池列表」（常量）
  - lottery_view h1 = pool_name（动态）
  - lottery_result h1 = 「抽奖结果」（常量）
- meta 行用 type-body-sm + muted-soft，`·` 分隔。
- Tier chip / kind pill 按 DESIGN.md 4-tier 语义映射重写：
  - 物品 / coin-pos → accent-teal outline
  - 指令 / coin-neg → accent-amber outline
  - tier none/0–5 cream，6–10 teal，11–15 amber，16–20 primary coral solid
- 抽奖结果的 gacha card：保留卡片格式（gacha 视觉是设计语言一部分），但去掉多色 tier 背景与渐变阴影；改为 cream-card + 4-tier 语义边框/强调。
- gacha-count-badge 改用 primary coral solid。
- stats tiles：去掉 ::before 彩色强调条与 box-shadow，改为 cream-card；stat-value 用 Inter 600 + tnum。
- command 模板用 mono 字体 + canvas 浅底 + hairline 边框。
- 全局数字（金币、价格、ID、概率、份数）使用 Inter 600 + `font-feature-settings: "tnum"`，去掉 💰/💸/💨 emoji 中"装饰性"用法（仅 result 卡片图标位保留 emoji 表达 outcome 类型）。
- 三个 ScreenshotOptions 增加 `fit_content_height=True`。

## Acceptance Criteria

- [ ] 三个页面截图与 menu / shop / warehouse 等已重构页面视觉风格一致。
- [ ] 不含 `data-theme="dark"` 分支与 Tailwind CDN。
- [ ] payload schema 完全保持不变，所有现有字段正确渲染。
- [ ] 截图自适应内容高度（无底部空白）。

## Out of Scope

- payload schema 修改。
- 抽奖业务逻辑、tier 计算、cost/probability 计算等。
- 其它截图页面。

## Technical Notes

- 沿用 shop / warehouse 重构建立的 cream-card + 4-tier chip + Inter tnum 数字模式。
- lottery_result 是最复杂的页面（stats tiles + gacha grid + cmd-card），需要保留卡片格式但精简视觉噪音。
