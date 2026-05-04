# Redesign Menu Render Style

## Goal

按 [DESIGN.md](../../../DESIGN.md)（Anthropic Claude.com 暖色编辑设计语言）重构 `菜单 <分类名>` 命令生成的网页截图样式，把现在的"靛紫渐变 + blob + 圆角卡片"换成"暖奶油画布 + 珊瑚色 CTA + 衬线大字标题"的编辑风格。

## What I Already Know

**当前实现链路**：
- `菜单 <category>` 命令在 [nextbot/plugins/menu.py:158](../../../nextbot/plugins/menu.py) 调 `_render_and_send_menu()`
- 渲染模板 [server/templates/menu.html](../../../server/templates/menu.html) — 单文件，内嵌 Tailwind CDN + 内联 CSS + 内联 JS
- Payload 由 [server/pages/menu_page.py](../../../server/pages/menu_page.py) 拼装：`{title, commands[], generated_at, theme}`
- 每条 command 有：`display_name`、`description`、`usage`、`permission`
- 视口 `1920×1280 full_page`、Playwright 截屏、base64 回发 OneBot V11 图片
- 当前模板支持 `data-theme="light" | "dark"` 两套配色

**当前样式特征**（要替换的）：
- `body` 用靛紫→蓝灰渐变 + 三个 blob 模糊圆球
- `card` 白底 + 阴影 + 蓝色 index badge
- 等宽 usage 框 + 紫色权限胶囊
- 字体：Tailwind 默认 sans
- "01/02/03" 编号徽标

**DESIGN.md 关键约束**（要落地的）：
- Canvas `#faf9f5`（暖奶油）替代靛紫渐变；不要 blob
- Coral `#cc785c` 作为强调色（标题装饰、权限/usage 高亮、装饰性元素）
- 标题用 Copernicus/Tiempos Headline 衬线（项目无本地字体 → Google Fonts 走 `Cormorant Garamond` 500 + `-0.02em` 替代；body 用 Inter）
- 卡片用 `surface-card #efe9de` + `rounded.lg 12px` + `padding.xl 32px`，无阴影（color-block first）
- usage 代码块用 `code-window-card-dark`（`#181715` 底）+ JetBrains Mono — 还原"Anthropic 在暖色页面里嵌深色代码框"的特征
- Section 内边距 `96px`、卡片间距 `24px / 32px`
- Hairline 边框 `#e6dfd8`、不要纯黑文字（用 `ink #141413`）
- 不展示 hover/动画状态（截图无意义）

**项目里其他渲染页**（仓库 / 商店 / 抽奖 / 排行榜 / 教程 等共 ~17 个 .html）目前各自有独立内联样式，没有共享设计 token。本次只改 `menu.html` 一个，但会顺手把"项目渲染页设计 token"沉淀成可复用的 CSS 变量块（写进 menu.html 内部即可，先不抽公共 css，避免越改越大）。

## Assumptions (Temporary)

1. **范围**：本次只重构「菜单」页面（`server/templates/menu.html` + `server/pages/menu_page.py`），不动其他渲染页 — 把这次当作样板，后续要不要批量推由后续任务决定。
2. **Web 字体**：用 Google Fonts CDN（Cormorant Garamond 衬线 + Inter 无衬线 + JetBrains Mono 代码）— 截图渲染端有外网，能加载在线字体；如果不行就用系统衬线 fallback。
3. **dark theme**：DESIGN.md 是"cream + dark navy 双 surface 共存于一页"，**不是**整页 dark mode。当前模板有 `data-theme="dark"` — 这次重构后**移除** dark theme 分支，全部走暖色画布（dark navy 仅用作 usage 代码块背景）。
4. **编号徽标**：DESIGN.md 没有强制的"序号徽标"概念，倾向**移除** `01 / 02 / 03` index badge（不是品牌元素），改用 caption-uppercase tag 或干脆不要。
5. **背景装饰**：移除三个 blob 模糊圆球；改为纯 canvas 底，可在右上角点缀一个小 Anthropic spike-mark 风格的装饰（4-spoke 黑色 SVG，作为品牌锚定）。
6. **footer**：保留 `Powered by NextBot` 但用 `caption` 字体 + `muted-soft` 颜色，靠右下，不再是宽字距全大写。
7. **theme 参数**：`menu_page.py.build_payload()` 的 `theme` 参数保留（向后兼容），但渲染端忽略它（永远走暖色）。

## Decisions Locked

- **范围（Q1 答 3）**：菜单做样板 + 抽公共 CSS 设计 token 文件，后续别的渲染页可直接 `<link>` 引用。CSS token 文件落在 `server/templates/_design_tokens.css`（或类似），通过 `menu.html` 内联 `<style>@import url(...)</style>` 或 build 时拼装注入。
- **字体来源（Q2 答 2）**：自托管 woff2 字体到 `server/webui/static/fonts/`（或 `server/static/fonts/`）。需要下载：
  - **Cormorant Garamond** weight 400 / 500（衬线 display）— SIL OFL 1.1
  - **Inter** weight 400 / 500（无衬线 body / nav）— SIL OFL 1.1
  - **JetBrains Mono** weight 400（代码 usage）— SIL OFL 1.1
  - 中文 fallback 用系统字体 `"PingFang SC", "Microsoft YaHei"`（不打包，约 ~50MB）
  - 总打包字节预估：每个字体的 latin 子集 woff2 约 30–60KB，三个加起来 ~150KB
  - 由 Playwright 加载本地 HTML 时通过 `file://` 或现有的 `http://127.0.0.1:18081/static/...` 路径访问
- **品牌锚（Q3 答 1）**：保留现有的珊瑚色短规则线（`header-rule`），不引入 Anthropic spike-mark / 自创品牌图标。最节制，符合 DESIGN.md "scarce coral" 哲学。

## Edge Cases (in MVP)

- **少卡居中**：1–2 张卡时，grid 改用 `repeat(auto-fit, minmax(360px, 1fr))` 让卡片在容器内居中分布；≥3 张回到 3 列
- **超长 description / usage**：`overflow-wrap: break-word` + `word-break: break-all`（中文长串、permission key 长串都能断）
- **空字段 fallback**：`usage` 空 → "—"；`permission` 空 → "无需额外权限"（同当前行为）
- **字体加载失败**：fallback 链回退到系统 serif / sans-serif / monospace，视觉降级但不破版

## Decision (ADR-lite)

**Context**：现有 menu.html 用的是靛紫渐变 + blob 模糊球 + 白底卡 + 紫色徽标的"通用 SaaS"风格，跟新 DESIGN.md 的"暖色编辑/Anthropic Claude.com"美学严重错位。其他 16 个渲染页同样还是老风格。

**Decision**：把 menu.html 当作样板按 DESIGN.md 重做，**同时**抽出 `_design_tokens.css` 公共 CSS 让后续页面能直接套用。字体走自托管 woff2 + 系统中文 fallback。删除 dark theme 分支，统一为暖色画布。装饰只保留珊瑚色短规则线，不引入 Anthropic spike-mark 避免蹭品牌。

**Consequences**：
- ✅ 后续 16 个页面重做时只要 `<link>` 引 design tokens 就拿到全套设计变量，不用重复定义
- ✅ 字体本地化后渲染 100% 离线、稳定、可预测
- ⚠️ +~150KB 的 woff2 字节加进仓库（一次性）
- ⚠️ menu.html 失去 dark theme 选项 — 但 DESIGN.md 本身就是 light-first，dark navy 仅作为页面内的小尺度组件存在
- ⚠️ 跟其他 16 个页面短期内视觉不一致（直到它们也被推到新设计）

## Requirements (Evolving)

- [ ] 整页底色 `#faf9f5`（移除靛紫渐变 + blob）
- [ ] 大标题：衬线（Cormorant Garamond / Tiempos / Garamond fallback），weight 400，`-0.02em` letter-spacing，48–64px
- [ ] 卡片：`#efe9de` 底 + `rounded-lg 12px` + `padding 32px` + 无阴影 + 1px hairline `#e6dfd8`
- [ ] command name 用 `title-md` (Inter 18px / 500)
- [ ] description 用 `body-md` (Inter 16px / 400 / 1.55 line-height)
- [ ] usage 代码框：dark surface `#181715` + JetBrains Mono 14px + cream-tinted text
- [ ] permission 胶囊：`badge-pill` 风格（`#efe9de` 底 + ink 文字）；无权限时用 `badge-coral` 或 `surface-cream-strong`
- [ ] 移除 dark theme 分支（payload `theme` 参数保留但渲染忽略）
- [ ] footer 只保留 `Powered by NextBot`，caption 字体 + muted-soft 颜色

## Acceptance Criteria (Evolving)

- [ ] `菜单 仓库系统` 命令产出的图片符合 DESIGN.md 暖色编辑视觉
- [ ] 截图视口 1920×1280 full_page 不破版
- [ ] 中文 + 英文混排不出现 fallback 字体闪现（serif 显示中文不会变成默认字体）
- [ ] 现有 `_render_and_send_menu()` API 不变，纯 HTML/CSS 替换
- [ ] payload `theme` 参数仍兼容（不做破坏性改动）

## Definition of Done (team quality bar)

- 不修改 `nextbot/plugins/menu.py`（命令逻辑 / 触发链路不动）
- 修改限于 `server/templates/menu.html` + 可能微调 `server/pages/menu_page.py`
- 新设计在 1920×1280 视口 full-page 截图能完整显示
- 中英文混排能正常显示
- 现有 light/dark theme 行为有变化（统一为暖色），需在 PRD 决策中记录

## Out of Scope

- 其他渲染页（仓库 / 商店 / 抽奖 / 排行榜 / 教程 等 16 个 .html）— 留待后续任务
- 抽公共 CSS / 设计 token 文件 — 本次内联在 menu.html，后续推广时再抽
- WebUI 后台页面（`server/webui/static/css/*`）— 设计语言用途不同
- 动画 / 过渡（截图场景不需要）

## Technical Notes

**关键文件**：
- [server/templates/menu.html](../../../server/templates/menu.html) — 主要修改对象
- [server/pages/menu_page.py](../../../server/pages/menu_page.py) — 可能微调
- [nextbot/plugins/menu.py](../../../nextbot/plugins/menu.py) — 不动
- [DESIGN.md](../../../DESIGN.md) — 设计源

**字体策略**（待确认）：
```css
/* Display (h1 / 标题) */
font-family: "Cormorant Garamond", "Tiempos Headline", "Times New Roman", serif;

/* Body (description / nav) */
font-family: "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;

/* Code (usage) */
font-family: "JetBrains Mono", ui-monospace, Menlo, monospace;
```

中文 fallback `PingFang SC` / `Microsoft YaHei` 必加，否则中文字面会用 Cormorant Garamond 的 fallback 链尾的默认字体。

**渲染流程**：Tailwind CDN 仍可用（已在用），但 DESIGN.md 颜色用 CSS 变量定义在 `:root`，避免散落 hex。

## Research References

(本次无外部研究 — DESIGN.md 自身即设计源)
