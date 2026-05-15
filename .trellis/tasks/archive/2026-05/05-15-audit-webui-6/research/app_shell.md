# Audit: WebUI app_shell

- **Query**: WebUI app_shell 全量审计（security / perf / ux / copy）
- **Scope**:
  - `server/webui/templates/app_shell_base.html`（233 LOC）
  - `server/webui/static/css/app-shell.css`（529 LOC）
- **Date**: 2026-05-15
- **CSP context**（`server/routes/webui.py:238-246`，仅作为背景，不在本次 finding 范围内）：
  - `script-src 'self' 'unsafe-inline'` / `style-src 'self' 'unsafe-inline'` —— shell 模板没有内联 `<script>`，但 `style-src 'unsafe-inline'` 默认放开，与 shell 本身风险相关性较低；保留为 scope-out backlog。

---

## 总计

- Critical: 0
- High: 2
- Medium: 8
- Low: 7

---

## Findings

### High-1 主导航缺 `<nav>` 语义 + `aria-current="page"` 缺失

**File**: `server/webui/templates/app_shell_base.html:15-172`
**Dimension**: ux (a11y)
**Issue**:
侧边栏使用 `<aside class="app-sider" aria-label="主导航">` 作为站点级主导航的最外层容器。`<aside>` 在 ARIA / HTML 语义上表示「辅助 / 补充内容」（complementary landmark），不是 navigation landmark；屏幕阅读器在「跳转到导航」时不会把它列为主导航候选。同时，内部 `<ul class="menu-list">` 也没有 `<nav>` 包裹。
更严重的是「当前页」语义只通过 CSS 类 `is-active`（`app_shell_base.html:63、73、84、96、109、120、132、144、158`）表达，缺少 `aria-current="page"`，盲人用户无法识别当前位置。

**Fix sketch**:
1. 把 `<aside id="webui-sidebar" class="app-sider" aria-label="主导航">` 改为 `<nav id="webui-sidebar" class="app-sider" aria-label="主导航">`，外层若需保留 `<aside>` 作为视觉容器，应在内部用 `<nav aria-label="主导航">` 单独包住 `<ul class="menu-list">`。
2. 在 `console_page.py` 渲染逻辑中，当前激活菜单的 `<a>` 同时追加 `aria-current="page"`（新增 `__NAV_*_ARIA__` 占位符），或在 webui.js 启动时根据 `is-active` 自动补全。

**Risk if unfixed**: 屏幕阅读器用户无法快速跳转到主导航，也无法识别当前位置，破坏 WCAG 2.4.1 + 2.4.8。

---

### High-2 mobile 端 `Tab` 焦点会泄漏到隐藏的侧边栏 / overlay 不被 ESC 关

**File**: `server/webui/templates/app_shell_base.html:15、173-174`；`server/webui/static/css/app-shell.css:497-528`
**Dimension**: ux (a11y / 键盘)
**Issue**:
mobile 视口下侧边栏通过 `transform: translateX(-100%)` 移出屏外（`app-shell.css:507`），但**没有 `visibility: hidden` 或 `inert`**，因此键盘 Tab 仍会聚焦到屏外的 9 个导航链接（`app_shell_base.html:63-169`），用户看不到聚焦在哪。同时 `aside aria-label="主导航"` 在隐藏状态下也没动态 `aria-hidden="true"`，screen reader 会读到不可见菜单。
此外 ESC 关闭仅由 webui.js 监听，shell 模板本身没有 `role="dialog" aria-modal` 之类语义，sidebar-overlay 也只是装饰性 div（`app_shell_base.html:173` `aria-hidden="true"` 但没有 `role`），mobile 抽屉开启时 Tab 可以跳出抽屉到背景内容。

**Fix sketch**:
1. CSS：mobile 默认状态加 `visibility: hidden`，`.is-mobile-open` 时改为 `visibility: visible`（保持 transform 动画兼容）。
2. HTML / JS（webui.js）：mobile 模式打开时给 `#webui-sidebar` 加 `aria-hidden="false"`，关闭时回 `true`；或更稳的方案——加 `inert` 属性（现代浏览器原生支持）。
3. 可选：mobile 抽屉打开时给 sidebar 加 `role="dialog" aria-modal="true"`，并实现焦点陷阱（关闭后焦点回 `#sidebar-toggle`）。

**Risk if unfixed**: 键盘用户在 mobile 无法操作 webui；屏幕阅读器读到屏外菜单造成混乱；不符合 WCAG 2.1.1 / 2.4.3 / 2.4.11。

---

### Medium-1 缺 skip-link / 主内容缺 `id`

**File**: `server/webui/templates/app_shell_base.html:13、223`
**Dimension**: ux (a11y)
**Issue**:
全站没有「跳到主内容」的 skip-link。9 个导航项 + header 内多个按钮的 Tab 顺序前置，键盘用户每次切页都要 Tab 10+ 次才到主内容。`<main class="app-content">` 也没有 `id`，无法做锚跳。

**Fix sketch**:
1. `<body>` 开头加 `<a class="skip-link" href="#main-content">跳到主内容</a>`，配套 CSS：`.skip-link { position:absolute; left:-9999px; } .skip-link:focus { left:var(--space-md); top:var(--space-md); z-index:100; }`。
2. `<main class="app-content" id="main-content" tabindex="-1">`（`tabindex=-1` 让 fragment 跳转后可程序化聚焦）。

**Risk if unfixed**: 键盘 / 屏幕阅读器用户体验下降，WCAG 2.4.1。

---

### Medium-2 `<h1>` 在每页都是 header 标题，主内容里再出现 h1 会破坏标题层级

**File**: `server/webui/templates/app_shell_base.html:182`
**Dimension**: ux (a11y)
**Issue**:
shell 把 header 标题（`__HEADER_TITLE__` = 「仪表盘」「命令配置」…）渲染为 `<h1>`。如果内容模板（`dashboard_content.html` 等，不在本次 scope）里也用 `<h1>`，整页会出现 2 个 h1，违反单一 h1 原则；若内容用 `<h2>` 起，逻辑上又对，但语义关系不直观。

**Fix sketch**:
最小改：将 header 标题降级为 `<p class="header-title" role="heading" aria-level="1">`，或保留 `<h1>` 并在 spec 中规定内容模板必须从 `<h2>` 起。建议落 spec 到 `.trellis/spec/guides/`（让 dashboard / commands 等 7 个内容模板对齐）。

**Risk if unfixed**: 与内容模板的 h1 双 h1，破坏 outline；不符合 WCAG 1.3.1。

---

### Medium-3 `theme-init.js` 同步阻塞渲染（render-blocking script in `<head>`）

**File**: `server/webui/templates/app_shell_base.html:10`
**Dimension**: perf
**Issue**:
`<script src="/webui/static/js/theme-init.js"></script>` 没有 `defer` / `async`，是经典反 FOUC（首屏避免主题闪烁）做法，需要同步执行——这是必要的；但 URL 没有 `?v=<mtime>` 版本号（与 `__WEBUI_SCRIPT_URL__` 不同），用户切主题策略变更后浏览器会长缓存命中旧脚本。

**Fix sketch**:
在 `console_page.py` 增加占位符 `__THEME_INIT_SCRIPT_URL__` + `_asset_url("js/theme-init.js")`，模板改为 `<script src="__THEME_INIT_SCRIPT_URL__"></script>`，保持同步（不加 defer/async，否则 FOUC）。

**Risk if unfixed**: 修改 theme-init.js 后，老用户长时间命中浏览器缓存；同时无 SRI/版本号，缓存毒化或 CDN（如有）变更不可控。

---

### Medium-4 navbar 9 个 inline SVG 体积 + 重复 stroke-width / fill 属性

**File**: `server/webui/templates/app_shell_base.html:65-167`
**Dimension**: perf
**Issue**:
9 个菜单图标 + GitHub / 太阳 / 月亮 / 退出 4 个 header 图标，共 13 个 inline SVG。每个 SVG 都重复声明 `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`。每个页面 HTML 都重新下发同一份 SVG（无法被浏览器缓存为外部资源）。
此外，**品牌 logo（line 18-55）是一个超长 path 的 NextBot 字体路径**，约占整个 shell 模板 1/4 LOC。

**Fix sketch**:
轻量级方案：把 13 个图标 + logo 抽成 `/webui/static/svg/icons.svg` SVG sprite，模板里用 `<svg><use href="/webui/static/svg/icons.svg#icon-dashboard"/></svg>`。能命中 HTTP 缓存，HTML 体积下降约 30%。
代价：增加 1 次额外请求；但 sprite 文件可加 `?v=<mtime>` 走长缓存。

**Risk if unfixed**: 9 个 webui 页面每次 HTML 下发都重复 13 份 SVG；HTML gzip 后多约 2-3KB；首字节延迟略增。

---

### Medium-5 z-index 层级与 toast / dialog 冲突隐患

**File**: `server/webui/static/css/app-shell.css:154、285、505`
**Dimension**: ux
**Issue**:
shell 自身定义了 3 个 z-index：
- `.sidebar-overlay`：39（`app-shell.css:154`）
- `.app-header`（sticky）：20（`app-shell.css:285`）
- `.app-sider`（mobile fixed）：40（`app-shell.css:505`）

没有 spec 定义 toast / modal 应该 ≥ 50。如果业务页面（servers/commands/users）的 dialog 用了 z-index=50，恰好高于 sider；但若 toast 用了 z-index=30，会被 mobile sider（40）盖住。缺少 token 化的 `--z-*` 变量约束。

**Fix sketch**:
在 `:root` 加 z-index token：
```
--z-header: 20;
--z-overlay: 39;
--z-sider: 40;
--z-dialog: 60;
--z-toast: 80;
```
并落入 `.trellis/spec/guides/` 规约：webui 业务 css 不得直接写 z-index 字面量。

**Risk if unfixed**: toast / dialog 与 mobile sider 抢层级，特定场景遮挡。

---

### Medium-6 `prefers-reduced-motion` 完全未支持

**File**: `server/webui/static/css/app-shell.css:130、153、181、222、331、445、508`
**Dimension**: ux (a11y)
**Issue**:
共 7 处 `transition`（侧边栏宽度、overlay 透明度、品牌 hover、菜单 hover、按钮 hover、输入 focus、mobile 抽屉滑入）。**没有 `@media (prefers-reduced-motion: reduce)` 关闭动画**。前庭功能障碍 / 晕动症用户开启系统「减少动效」时仍会看到滑入动画。

**Fix sketch**:
在 css 末尾加：
```css
@media (prefers-reduced-motion: reduce) {
  .app-sider, .sidebar-overlay, .brand-logo-link,
  .menu-link, .btn, .input,
  .search-input, .token-input, input, select, textarea {
    transition: none !important;
  }
}
```

**Risk if unfixed**: WCAG 2.3.3（Level AAA）；对部分用户造成不适。

---

### Medium-7 sidebar-toggle 的 hamburger 字符 `☰` 用文本字形渲染

**File**: `server/webui/templates/app_shell_base.html:178-181`
**Dimension**: ux / perf
**Issue**:
header 全部图标都用 SVG，**唯独 sidebar-toggle 用了 unicode `☰`**。问题：
1. 字体回退不一致（不同系统字形大小、垂直对齐差异大）；
2. 与同 header 其他 SVG 图标视觉风格不一致（粗细 / 间隙）；
3. `aria-label="隐藏侧边栏"` 是初始静态值，webui.js 启动后根据 mobile/desktop 动态切换为「打开导航菜单 / 关闭导航菜单 / 展开侧边栏 / 隐藏侧边栏」（`webui.js:45、60`），但 HTML 渲染初值与 JS 启动前可能有短暂错位（如桌面初值刚好不冲突，mobile 初值就错）。

**Fix sketch**:
换成 lucide `menu` SVG（3 条横线，与其他 stroke=2 图标统一）。同时让初始 `aria-label` 用通用值「切换导航菜单」，由 webui.js 在 `applySidebarState()` 内精化。

**Risk if unfixed**: 视觉不统一；不同浏览器 hamburger 字形差异。

---

### Medium-8 `.app-content { max-width: 1180px }` 与 `.app-header` 不对齐

**File**: `server/webui/static/css/app-shell.css:275-286、487-494`
**Dimension**: ux (布局)
**Issue**:
`.app-header` 横铺整个 `.app-main` 宽度（无 max-width），padding `0 var(--space-xl)`；但 `.app-content` 有 `max-width: 1180px; margin: 0 auto`。在超宽屏（>1180px + sidebar）下，header 右侧 action 区贴右边缘，主内容居中，**视觉上 header 与 content 边界不对齐**，header 的 "GitHub / 主题 / 退出" 按钮会和主内容右边缘错开。

**Fix sketch**:
方案 A：把 header 也包到同一 `max-width: 1180px` 居中容器里（header 内 `.header-left / .header-actions` 套 wrapper）。
方案 B：取消 content 的 max-width，改用每个 content 模板自管 max-width。
推荐 A，保持 shell 统一。

**Risk if unfixed**: 超宽屏视觉错位；不影响功能，但破坏「设计感」。

---

### Low-1 GitHub 链接 `target="_blank"` 已带 `rel="noopener noreferrer"` —— 通过

**File**: `server/webui/templates/app_shell_base.html:186`
**Dimension**: security
**Issue**: 已正确设置 `rel="noopener noreferrer"`，无 tabnabbing 风险。**仅作为审计完成项记录**，无需修复。

**Fix sketch**: N/A
**Risk if unfixed**: N/A

---

### Low-2 `brand-logo-svg color: var(--color-ink)` —— 引用了未定义 token

**File**: `server/webui/static/css/app-shell.css:192`
**Dimension**: ux (视觉)
**Issue**:
`color: var(--color-ink);` 但 `:root` / `html.dark` 中**没有定义 `--color-ink`**（line 7-58、60-90 都只有 `--text`、`--text-muted` 等）。浏览器会回退到 `currentColor`/继承色，依赖 `<a class="brand-logo-link">` 默认链接颜色（`a { color: var(--primary); }`，line 108）。结果：brand logo 是 coral 色，与设计意图（应该是 `--text` ink 黑）不符。

**Fix sketch**:
改为 `color: var(--text);` 或定义 `--color-ink: var(--text);` token。

**Risk if unfixed**: logo 显示为 coral，与设计基线偏离；dark 模式下尤为明显（应该是浅色文字但被着色成 coral）。

---

### Low-3 menu hover 与 active 视觉对比度不足

**File**: `server/webui/static/css/app-shell.css:213-235`
**Dimension**: ux (视觉对比)
**Issue**:
- 默认 `color: var(--text-muted)` = `#6c6a64`，背景 `--surface` = `#efe9de`，对比度 ≈ 4.0:1，**勉强达到 WCAG AA 文本最低（4.5:1）门槛但失败**。
- `is-active` 用 `--primary`（coral `#cc785c`）在 `--bg-layout` `#faf9f5` 上，对比度 ≈ 3.4:1，**未达 AA**。

**Fix sketch**:
- 默认色改 `--text`（`#141413`），hover/active 时再切回。或定义 `--text-strong` token，仅用于菜单文字。
- 激活态 coral 文字加大字重已是 600，但视觉依赖颜色——可加左侧 2px 色条（已有 `::before`）+ 文字仍用 `--text`，仅图标和左侧条用 coral。

**Risk if unfixed**: WCAG 1.4.3 失败；弱视用户难辨。

---

### Low-4 logout 按钮无二次确认 / 无 loading 反馈（shell 层提示）

**File**: `server/webui/templates/app_shell_base.html:211-219`
**Dimension**: ux / copy
**Issue**:
退出按钮点击后由 webui.js 直接调用 DELETE /webui/api/session 并 `window.location.assign("/webui/login")`（`webui.js:136-153`）。**shell 模板自身没有任何反馈语义**——按钮无 disabled 视觉态 / loading icon；用户多次点击在 webui.js 中通过 `logoutButton.disabled = true` 已有保护，但 css 没有 `.btn[disabled]` 样式定义（全文件无 `[disabled]` 选择器），按钮无视觉变化。

**Fix sketch**:
1. 在 app-shell.css 加 `.btn[disabled] { opacity: 0.6; cursor: not-allowed; pointer-events: none; }`。
2. 可选：title 文案"退出登录"无歧义，保留。

**Risk if unfixed**: 用户连点 → 视觉无变化 → 误以为点击未生效。

---

### Low-5 navbar 标签命名不一致：「管理」后缀混用

**File**: `server/webui/templates/app_shell_base.html:69、80、92、105、116、128、140、154、167`
**Dimension**: copy
**Issue**:
9 个菜单标签：
| 行号 | 标签 |
|---|---|
| 69 | 仪表盘 |
| 80 | 命令配置 |
| 92 | 服务器管理 |
| 105 | 用户管理 |
| 116 | 身份组管理 |
| 128 | 仓库管理 |
| 140 | 商店管理 |
| 154 | 抽奖管理 |
| 167 | 设置 |

「服务器/用户/身份组/仓库/商店/抽奖」都带「管理」后缀；「命令配置」是「配置」后缀；「仪表盘 / 设置」无后缀。整体不统一：
- 「命令配置」vs「命令管理」？
- 或全部去掉后缀（「服务器」「用户」「身份组」「仓库」「商店」「抽奖」「命令」「设置」「仪表盘」），更简洁更一致。

**Fix sketch**:
统一去后缀方案最易落地：
```
仪表盘 / 命令 / 服务器 / 用户 / 身份组 / 仓库 / 商店 / 抽奖 / 设置
```
（同时 9 个 `console_page.py` 的 `header_title` 一并对齐。）

**Risk if unfixed**: copy 不一致，违反全局 CLAUDE.md「保持文案规范」原则，影响产品感。

---

### Low-6 GitHub 链接 `aria-label="打开 GitHub 仓库"` 中英混排空格 OK，但 title 仅 "GitHub" 与 aria 内容不一致

**File**: `server/webui/templates/app_shell_base.html:186-187`
**Dimension**: copy / a11y
**Issue**:
`aria-label="打开 GitHub 仓库"`（screen reader 读这个），`title="GitHub"`（鼠标 hover 看这个），二者内容不一致。WAI-ARIA 推荐 title 与 aria-label 表达一致；不一致时屏幕阅读器与视觉用户体验出现偏差。

**Fix sketch**:
`title="打开 GitHub 仓库"` 或两者都改为 `GitHub 仓库`。

**Risk if unfixed**: 微小一致性问题。

---

### Low-7 `[hidden] { display: none !important }` 用 !important 过于激进

**File**: `server/webui/static/css/app-shell.css:116`
**Dimension**: ux
**Issue**:
`!important` 会胜过任何后续业务规则。如果某天某 dialog 用 `[hidden]` 属性 + JS 控制时想用 `transform` 隐藏（保留 size），会被强制 `display:none`。

**Fix sketch**:
通常去掉 `!important` 即可（HTML 标准已规定 `[hidden]` 默认 `display:none`，浏览器已内置，不需要再 force）。

**Risk if unfixed**: 未来某些动画 / size 测量场景被卡。

---

## Scope-out backlog（不在本次 2 文件审计内、但顺手记录）

- **CSP `'unsafe-inline'`** —— `server/routes/webui.py:240-241`。shell 模板本身没有内联 script/style，但 login.html / inline `<style>` 存在；全站 CSP 收紧需要单独任务清理所有 inline style。
- **`theme-init.js`** —— `server/webui/static/js/theme-init.js`，里面 `localStorage.getItem("nextbot-webui-theme")` 同 webui.js 重复硬编码 key，可以抽常量；不在本次范围。
- **mobile sider 抽屉的焦点陷阱实现** —— 实现在 `webui.js`，本次只指出 HTML/CSS 需要 `inert`，焦点陷阱代码改造属于 webui.js 范围。
- **`<main>` 的 `id` + skip-link 配套行为** —— 落地需要碰 webui.js（focus 切换），不在本次 2 文件范围，但需要主代理协同。
- **9 个内容模板里的 `<h1>` 与 shell `<h1>` 冲突** —— 9 个 content 模板逐一确认，超 scope。

---

## 优先级 Top-3

1. **High-1**：主导航缺 `<nav>` 语义 + `aria-current` —— 影响所有屏幕阅读器用户，跨 9 个页面。
2. **High-2**：mobile 端 Tab 焦点泄漏到隐藏侧边栏，且无 inert / 焦点陷阱 —— mobile 键盘 / 屏幕阅读器用户严重受影响。
3. **Medium-1**：缺 skip-link + `<main>` 缺 id —— 9 个页面都受影响，键盘用户每次切页要 Tab 10+ 次。

## 整改建议组织

- **可单 PR 落地的纯 CSS / HTML 改**：High-1、High-2（CSS 部分）、Medium-1（CSS 部分）、Medium-5、Medium-6、Medium-7、Medium-8、Low-2、Low-3、Low-4、Low-5、Low-6、Low-7
- **需要碰 console_page.py 或 webui.js 协同的**：High-1（`aria-current`）、High-2（`inert` JS）、Medium-1（focus 跳转 JS）、Medium-2（h1 与 content 协调）、Medium-3（theme-init 带版本号）、Medium-4（SVG sprite）
