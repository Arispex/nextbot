# Redesign 我的背包 / 用户背包 / 进度 render per warm-canvas system

## Goal

按 `DESIGN.md` (Anthropic Claude.com warm-canvas editorial) 风格重构 `inventory.html` (我的背包 / 用户背包共用) 与 `progress.html` (进度) 两个截图模板，与已完成的 menu / shop / lottery / leaderboard / warehouse 等页面保持视觉一致。

## What I already know

- `inventory.html` 默认 light theme + Tailwind CDN + 多色渐变 Hero + 13 个 section 分组（主背包 / 猪猪 / 保险箱 / 装备 ×3 / 饰品 / 染料 / 货币 / 弹药 / 垃圾桶 / 熔炉 / 虚空袋），每个 section 有彩色 stripe + cell 网格。
- `progress.html` 默认 dark theme + Tailwind CDN + 玻璃质感 Hero + boss 卡片网格（21 个 boss，已击败/未击败两态）。
- `nextbot/plugins/player_query.py` 用 `INVENTORY_SCREENSHOT_OPTIONS`（2000×1000）与 `PROGRESS_SCREENSHOT_OPTIONS`（1700×700）控制截图，未启用 `fit_content_height`。
- 设计 token (`/assets/css/render-tokens.css`) 与字体已就位。

## Requirements

- 移除 dark mode 与 Tailwind CDN，改用项目内 token + 字体 CSS。
- 去掉外层 `card-root` / `hero` / `body-area` 包裹层，整体直接居于 canvas 上；header 直接出现在 canvas 顶部。
- header 改为 text-hero（coral rule + eyebrow + serif h1）：
  - inventory：eyebrow `背包系统`、h1 = `用户背包`（始终硬编码，不区分"我的"/"用户"，参考 warehouse 决策）
  - progress：eyebrow `进度系统`、h1 = `世界进度`
- inventory：玩家信息改为 byline 横排（avatar + name + QQ + server + time）；stats 改成 5-列 cream-card tiles（生命、魔力、渔夫任务、PVE/PVP 死亡合并 或保持分开、在线时长），数字 Inter 600 + tnum；section 保留 cream-card 分组（必要的视觉边界）但去掉彩色 stripe + box-shadow，改用 4-tier 语义边框：
  - 主背包 / 猪猪 / 保险箱：cream-card hairline（普通容器）
  - 装备 / 饰品 / 染料：accent-teal outline（角色装备）
  - 货币 / 弹药：accent-amber outline（资源）
  - 熔炉 / 虚空袋：cream-card hairline
  - 垃圾桶：cream-soft + muted（次要）
- inventory cell：occupied = cream + hairline；empty = canvas + dashed；slot-index muted-soft；stack tag 用 canvas + hairline
- progress：服务器信息 byline；大字击败统计 (Inter 600 + tnum) + progress bar（hairline track + primary coral fill）；boss 卡片 — defeated = primary coral 2px border + 完整图、undefeated = cream-soft + dashed + 灰度图；badge 用 cream + 4-tier color（defeated = primary coral solid、undefeated = cream + muted）
- 全部移除 ✓ emoji 装饰，改用纯文字「已击败」/「未击败」
- 全部移除：dark mode 分支、Tailwind CDN、彩色渐变、box-shadow 发光、text-shadow
- `INVENTORY_SCREENSHOT_OPTIONS` / `PROGRESS_SCREENSHOT_OPTIONS` 增加 `fit_content_height=True`
- 加全局 `[hidden] { display: none !important; }` 守卫（与 leaderboard 同样的 bug 防御）

## Acceptance Criteria

- [ ] 两个页面截图与 menu / lottery / leaderboard / warehouse 等已重构页面视觉风格一致。
- [ ] 不含 `data-theme="dark"` 分支与 Tailwind CDN。
- [ ] payload schema 完全保持不变；inventory 13 个 section 全部正确渲染；progress 21 个 boss 全部正确显示两态。
- [ ] 截图自适应内容高度。

## Out of Scope

- payload schema 修改、新增 boss 名映射、其它截图页面。

## Technical Notes

- 沿用已重构页面的 cream-card + Inter tnum + 4-tier semantic 模式。
- inventory 是当前最复杂的页面（13 个 section + 5 stats tiles + hero meta），但 layout 结构 (`sectionRowsConfig` + `BOSS_IMG_MAP`) 都保留不动，只重写 CSS 与 DOM 类名。
