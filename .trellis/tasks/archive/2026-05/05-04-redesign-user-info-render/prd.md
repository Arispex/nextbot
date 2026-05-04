# Redesign User Info Render

## Goal

按 [DESIGN.md](../../../DESIGN.md) 把「我的信息 / 用户信息」两个命令的截图重做成暖色编辑风，移除 dark theme，启用 `fit_content_height` 自适应高度。这是把菜单页（已完成）的方案套用到第二个截图页 — 公共 design-tokens / fonts CSS 已就位。

## What I Already Know

**当前实现**：
- 命令：[nextbot/plugins/user_manager.py:41-42](../../../nextbot/plugins/user_manager.py) 两个 matcher (`info_matcher`, `self_info_matcher`)，都通过 `_render_and_send_user_info()` 走同一渲染管线
- 模板：[server/templates/user_info.html](../../../server/templates/user_info.html) 270 行，单文件，Tailwind CDN，**有 light / dark 双 theme**
- Page builder：[server/pages/user_info_page.py](../../../server/pages/user_info_page.py) 接受 `theme: str` 参数
- 截图选项：`viewport_width=820, viewport_height=600, full_page=True`（在 user_manager.py:33）

**页面结构**（按 DOM 顺序）：
1. **Header**：圆形头像 + 用户名 + 身份组徽章 + QQ + 注册时间
2. **Stats grid（4 列）**：金币 / 累计签到 / 连续签到 / 权限列表
3. **Contribution Wall**：365 天 GitHub 风格签到热力图
4. **Footer**：Powered by NextBot

**当前样式特征**（要替换的）：
- 靛紫渐变背景 + 强阴影白卡
- 蓝色 group badge / 紫灰 perm badge
- 绿色（light）/ 靛蓝（dark）的签到日格子
- Tailwind 默认 sans 字体
- 所有 stat values 是粗体黑色 sans

**已就位的公共资产**（菜单页那次抽出来的）：
- `server/assets/css/render-tokens.css` — DESIGN.md 颜色 / 字体 / 间距 CSS 变量 + `.type-*` 排版预设类
- `server/assets/css/render-fonts.css` — Cormorant Garamond / Inter / JetBrains Mono `@font-face`
- `/assets/css/{path}` + `/assets/fonts/{path}` 静态路由
- `server/screenshot.py:ScreenshotOptions.fit_content_height` 内容自适应选项

## Decisions Locked (deriveable without user)

- **范围**：仅 `我的信息` + `用户信息`（共用同一模板）。其他 14 个截图页继续等后续单独任务。
- **dark theme**：删除（菜单页已先例，PRD 与实测一致）
- **theme 参数**：payload 保留向后兼容，模板不再读取
- **页面最大宽度**：从 820px 提到 880px（给 stats grid 多一点呼吸空间，仍远低于 DESIGN.md 1200px content-max）
- **footer 风格**：caption + muted-soft 颜色，沿用菜单页约定
- **大圆角主卡 → 中圆角**：DESIGN.md 用 `rounded-lg 12px` 给 content card；当前 `rounded-3xl` 太脱离品牌
- **背景从渐变 → cream canvas (#faf9f5)**
- **主卡片用 cream-card surface (#efe9de) + 1px hairline + 0 阴影**（DESIGN.md "color-block first, shadow rare"）
- **用户名字号**：`type-display-md` (36px Cormorant 衬线，负字距) — 编辑感大字
- **stat 值字号**：`type-display-sm` (28px 衬线) — Anthropic 风的大数字
- **stat 标签**：`type-caption-uppercase` (12px / 1.5px tracking) — 跟菜单页"权限"标签风格统一
- **group badge**：`badge-pill`（cream-canvas 底 + ink 文字 + hairline 边）
- **permission badges**：同样 `badge-pill`，无权限时显示 muted "无"
- **avatar ring**：1px hairline，无阴影
- **divider**：hairline-soft
- **截图启用 `fit_content_height=True`**：跟菜单页同样行为，避免低活跃用户截图下方一大片空白
- **viewport 宽度从 820 提到 920**（给 880px 主卡 + 左右各 20px 留白）

## Decisions Locked (cont.)

- **签到热力图签到日颜色（Q1 答 a）**：`accent-teal #5db8a6` — DESIGN.md 把 teal 定位为 active/status，语义贴合"签到=活跃"，且密集时视觉不过载
- **去掉外层 cream-card 容器，改为 canvas-first 布局**（迭代 v2）：DESIGN.md 的 surface 哲学是"cream-card 包内容单元，不包整个页面"，且菜单页已经用了 canvas-first，统一两个页面的骨架。新结构：
  - body 直接是 cream canvas
  - header（avatar + 名字 + meta）直接坐在 canvas 上
  - 4 个 stat 各自是独立 cream-card
  - 签到记录是独立 cream-card
  - footer 直接在 canvas 上
  - 各 section 之间用 `var(--space-xl)` margin 而不是 divider 线

## Requirements (Evolving)

- [ ] 整页 cream canvas 背景
- [ ] 主卡片 cream-card surface + 1px hairline + 0 阴影
- [ ] 用户名 Cormorant 衬线 display-md
- [ ] 4 个 stat card：cream canvas 底 + hairline 边 + 衬线大数字
- [ ] 权限徽章 / 身份组徽章统一 badge-pill 风格
- [ ] 签到热力图：empty=hairline，signed=accent-teal（待确认）
- [ ] 引入 `<link>` 加载公共 render-tokens.css + render-fonts.css
- [ ] 模板移除 `data-theme` 切换
- [ ] `USER_INFO_SCREENSHOT_OPTIONS` 启用 `fit_content_height=True`
- [ ] viewport 调到 `width=920, height=600`

## Acceptance Criteria

- [ ] `我的信息` / `用户信息` 截图视觉符合 DESIGN.md 暖色编辑风
- [ ] 用户名、stat 数字使用 Cormorant 衬线
- [ ] 签到热力图密集时不显得视觉过载
- [ ] 截图高度按内容自适应（无签到记录的低活跃用户截图也不会留大片空白）
- [ ] 命令层 API 零改动；payload `theme` 字段仍接受但被忽略

## Out of Scope

- 其他 14 个渲染页（仓库 / 商店 / 抽奖 / 排行榜 / 教程 / 红包 / 进度 等）
- contribution wall 算法本身（只换颜色 + 边距，不动 GitHub 式布局）
- 头像加载逻辑（仍走 `q1.qlogo.cn`）

## Definition of Done

- 修改限于 `server/templates/user_info.html` + `server/pages/user_info_page.py`（如有需要）+ `nextbot/plugins/user_manager.py` 的 `USER_INFO_SCREENSHOT_OPTIONS`
- 单/多签到记录用户截图均通过本地 Playwright 渲染验证
- 现有命令链路 + payload schema 零破坏
