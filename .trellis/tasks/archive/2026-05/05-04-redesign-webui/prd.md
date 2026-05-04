# Redesign WebUI per warm-canvas system + copy/layout polish

## Goal

把 NextBot WebUI（管理后台）从当前 Ant Design / 蓝色 primary 风格重构为 `DESIGN.md` 的 Anthropic warm-canvas 风格，与 17 个截图模板的视觉语言统一；同时优化文案与布局，提升可读性与编辑感。

## What I already know

- WebUI 共 35 个源文件、~340KB：12 个 HTML 模板、10 个 CSS、12 个 JS。
- 共享外壳：`app_shell_base.html` 提供 sidebar 导航 + 9 个 content 模板（dashboard / commands / servers / users / groups / warehouse / lottery / shop / settings）+ 独立 `login.html`。
- 共享样式：`app-shell.css` 定义 design tokens (Ant Design 蓝 `#1677ff`)，每个页面自带专属 CSS。
- 主题切换：`theme-init.js` 通过 `html.dark` class 切换，用户偏好存储于 `localStorage["nextbot-webui-theme"]`，与截图渲染的 RENDER_THEME（已删除）独立。
- 引入 `antd@5/dist/reset.css` CDN（仅 reset，不是组件）。

## Decisions (confirmed with user)

- **Q1 = B**：**保留** webui 的 light/dark toggle，需为 warm-canvas 设计配套 dark 配色（不沿用截图 light-only 决策）。
- **节奏**：分 5 阶段推进，每阶段独立 commit / review。

## Phase plan

| Phase | 范围 | 状态 |
|-------|------|------|
| **Phase 1（本次）** | design tokens 重写（light + dark 双模） + `app_shell_base.html`（sidebar + brand）+ `dashboard` 作为 pilot 页面 | in progress |
| Phase 2 | `login.html`（独立入口体验）| pending |
| Phase 3 | `settings` + `commands`（表单 / 列表组件验证）| pending |
| Phase 4 | `servers` + `users` + `groups`（数据 CRUD 三件套）| pending |
| Phase 5 | `warehouse` + `lottery` + `shop`（业务管理三件套）| pending |

## Phase 1 Requirements

### Design tokens（核心）
- 重写 `app-shell.css` 的 `:root` 与 `html.dark` 块，对齐 DESIGN.md 的 warm-canvas token 体系：
  - **Light**：canvas `#faf9f5` / surface-card `#efe9de` / surface-soft `#f5f0e8` / ink `#141413` / muted `#6c6a64` / muted-soft `#8e8b82` / hairline `#e6dfd8` / primary `#cc785c` / accent-teal `#5db8a6` / accent-amber `#e8a55a`
  - **Dark**：canvas `#181715` / surface-card `#22201d` / surface-soft `#1f1d1b` / ink `#f5f4f0` / muted `#a39d92` / muted-soft `#7a7268` / hairline `#33312d` / primary `#cc785c`（保留品牌色）/ accent-teal `#5db8a6` / accent-amber `#e8a55a`
- 字体接入：复用 `/assets/css/render-fonts.css`（自托管 Cormorant Garamond / Inter / JetBrains Mono），不再走 Google Fonts CDN
- 引入 `--space-*` / `--radius-*` 与渲染端一致的 spacing/radius 体系
- 删除现有的蓝色 `--primary` `#1677ff`、`box-shadow` 厚阴影 token，改用 hairline 细线分隔为主

### App Shell 结构
- `app_shell_base.html` 重构：
  - sidebar 改为 cream-card 背景 + hairline 边框，菜单 link active 态用 primary coral 文字 + 左侧 2px coral 条
  - 顶部 brand 区域改用 serif `NextBot` + 小字 `WebUI` eyebrow，去掉双 logo 切换（改用单一文字标识 + theme toggle 按钮单独放）
  - 主体区域 padding 与 max-width 对齐 DESIGN.md（layout 用 1180px max + 32px gutter）
- 移除 `antd@5/dist/reset.css` CDN 依赖，改用项目内 reset

### Dashboard pilot
- 重写 `dashboard_content.html` + `dashboard.css`：
  - text-hero header（coral rule + `控制台` eyebrow + serif `仪表盘` h1 + 副标题说明）
  - 主要数据 cream-card tiles（Inter 600 + tnum 数字 + label uppercase muted）
  - 状态信息使用 4-tier semantic 颜色（teal=正常、amber=警告、coral=错误）
  - 文案优化：把当前的技术化字段名（如 "Bot 状态"、"Web 服务端口"）改为更清晰的描述

### 兼容性
- `dashboard.js` 不动 DOM 类名，CSS 完全重写但保留 ID/data 属性 hook
- 暗色 toggle 行为不变，只换色板
- `theme-init.js` 不动

## Acceptance Criteria

- [ ] light + dark 两套 token 都符合 warm-canvas 系统（dark 用 `#181715` 系列）
- [ ] sidebar 在 light/dark 都视觉协调，active 态明确
- [ ] dashboard 截图与 menu / shop / lottery 等截图风格一致
- [ ] 不引用 Tailwind / antd CDN
- [ ] 字体走 `/assets/css/render-fonts.css`
- [ ] Phase 1 不破坏 dashboard.js 的 DOM hook（API 调用、刷新等）

## Out of Scope (Phase 1)

- Phase 2-5 的页面
- 信息架构调整（导航菜单项不变）
- API 行为修改

## Technical Notes

- DESIGN.md dark mode 提示：DESIGN.md 主体是 light-first，dark 配色在 component examples 中提到 `surface-dark #181715`；其余 dark token 由本任务推导（深米色基底 + 品牌色保留）
- 所有 17 个 render templates 已统一走 warm-canvas + light only，webui 的 dark mode 是 webui-specific 用户偏好，不影响截图
