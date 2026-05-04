# Redesign 排行榜 render per warm-canvas system

## Goal

按 `DESIGN.md` (Anthropic Claude.com warm-canvas editorial) 风格重构 `leaderboard.html` 截图模板（10+ 个排行榜命令共用此模板：金币 / 签到 / 死亡 / 渔夫任务 / 在线时长 / 抢劫 / 猜数字 / 掷骰子 等），与已完成的 menu / shop / lottery 等页面保持视觉一致。

## What I already know

- 模板 `server/templates/leaderboard.html` 当前是 dark-theme-first，使用 Tailwind CDN + 双主题 + 蓝/金属/玻璃质感渐变。
- `nextbot/plugins/leaderboard.py` 用单个 `LEADERBOARD_SCREENSHOT_OPTIONS` 控制所有排行榜命令的截图，未启用 `fit_content_height`。
- 数据 payload 由 `server/pages/leaderboard_page.py` 产出：`title`、`value_label`、`entries` (rank/name/user_id/value)、`self_entry`、`page` / `total_pages`。
- 设计 token (`/assets/css/render-tokens.css`) 与字体已就位。

## Requirements

- 移除 dark mode 与 Tailwind CDN，改用项目内 token + 字体 CSS。
- 去掉外层 `page-header`、`list-wrap`、`self-wrap` 卡片包裹层，所有内容直接居于 canvas 上；每个列表行 / podium 卡是独立的 cream-card。
- header 改为 text-hero：coral rule + `排行榜系统` eyebrow + serif h1 = `data.title`；meta 行显示 `第 X / Y 页 · 时间`。
- Podium（top 3）保留三栏视觉（podium 是核心设计语言），改用 4-tier 语义映射：
  - #1 → primary coral（2px border，体型最大居中）
  - #2 → accent-amber outline
  - #3 → accent-teal outline
- 去掉 🥇🥈🥉 emoji；改用 serif 大数字 `1` / `2` / `3` + tier color 强调（参考 DESIGN.md 编辑风格）。
- 去掉所有金属渐变、box-shadow 阴影、text-shadow 发光效果，统一用 cream-card + hairline。
- List rows：每行直接是 cream-card，rank-badge 用 cream + tnum 数字 + hairline；name 用 ink + medium，QQ ID 用 mono + muted-soft。
- Value 与 unit：Inter 600 + `font-feature-settings: "tnum"`，value 用 ink，unit 用 muted-soft。
- Self entry：单独的 cream-card，顶部 caption-uppercase 标签「我的排名」。
- `LEADERBOARD_SCREENSHOT_OPTIONS` 增加 `fit_content_height=True`；宽度 900→920 与其他页面对齐。

## Acceptance Criteria

- [ ] 截图与 menu / shop / lottery 等已重构页面视觉风格一致。
- [ ] 不含 `data-theme="dark"` 分支与 Tailwind CDN。
- [ ] 不含 🥇🥈🥉 emoji。
- [ ] payload schema 完全保持不变；所有排行榜命令均能正确渲染（金币 / 签到 / 死亡 / 在线时长 / 渔夫任务 / 抢劫 / 猜数字 等）。
- [ ] 截图自适应内容高度。

## Out of Scope

- payload schema 修改、新增排行榜命令、其它截图页面。

## Technical Notes

- 沿用已重构页面的 cream-card + Inter tnum 数字 + 4-tier semantic 映射模式。
- podium 比 list 更醒目：1st 居中、padding 更大；2nd / 3rd 在两侧；rank serif 数字用大字号（48–64px）作为视觉锚点，替代 emoji。
