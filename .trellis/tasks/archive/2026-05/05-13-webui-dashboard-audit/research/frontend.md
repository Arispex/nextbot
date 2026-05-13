# Frontend / 文案 桶审计 — WebUI Dashboard

- **审计范围**：`server/webui/templates/dashboard_content.html`、`server/webui/static/js/dashboard.js`、`server/webui/static/css/dashboard.css`，`server/webui/static/js/api.js`（dashboard.js 引用部分）、`server/webui/static/js/webui.js`（共享 shell 逻辑）
- **API 契约**：`server/routes/webui_dashboard.py:13-25` → `api_success(data=metrics)`；`payload.data` 字段见 `nextbot/stats.py:122-137`（`running_status / server_count / user_count / group_count / command_total / command_enabled_count / command_execute_count / connected_bot_ids / generated_at` 等）
- **Shell**：`server/pages/console_page.py:105-118` 通过 `_render_app_shell_page` 注入 `app-shell.css + dashboard.css`、`api.js + webui.js + dashboard.js`
- **Prior art（已落地，不重复挖）**：`/webui*` 已注入 `Content-Security-Policy`、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`（`server/routes/webui.py:219-243`）

---

## A. 前端安全

整体结论：**dashboard 前端 XSS 面 0 项实际触发**。所有 server 数据均通过 `textContent` 或 `replaceChildren(...DOM 节点)` 写入，模板侧无内联事件、无 `innerHTML` / `outerHTML` / `document[.]write` / `insertAdjacentHTML` / `eval` 调用，无 `location.search / location.hash / document.cookie` 读取，无 `localStorage / sessionStorage` 写入。对 dashboard.js / dashboard_content.html 执行 grep 该 10 类危险 API 全空。

### A1. XSS / DOM 注入审计（逐字段）

| 字段 | 渲染位置（dashboard.js） | 方式 | 评级 |
|---|---|---|---|
| `running_status` | `dashboard.js:122` | `runningStatusNode.textContent = String(data.running_status \|\| "--")` | 安全 |
| `server_count / user_count / group_count / command_total / command_enabled_count / command_execute_count` | `dashboard.js:123-128` | `formatNumber()` → `toLocaleString` 后赋 `textContent`；非 finite 退回 `"--"` | 安全 |
| `generated_at` | `dashboard.js:129` | `dashboardUpdatedAtNode.textContent = String(data.generated_at \|\| "--")` | 安全 |
| `connected_bot_ids[]` | `dashboard.js:98-119` | 数组 map→trim→filter，逐项 `document.createElement("span")` + `node.textContent = item`，再 `replaceChildren(fragment)` | 安全 |
| `error.message` | `dashboard.js:155` 经 `api.apiRequest` 抛出 → `setStatus(...)` → `statusMessageNode.textContent` | 全程 textContent；CSS `white-space: pre-line`（dashboard.css:86） | 安全 |

`status-message` 容器（dashboard_content.html:19）`role="status" aria-live="polite"`，是 AT 可读区，但写入仍走 `textContent`（dashboard.js:64），无注入面。

### A2. CSRF / 状态变更端点

dashboard 仅有 1 个 HTTP 调用：`GET /webui/api/dashboard`（`dashboard.js:142-149`）。**无 POST / PUT / DELETE**，CSRF N/A。退出登录的 DELETE 在 `webui.js:139-146`，已含 same-origin fetch + HttpOnly session cookie + middleware 校验（`webui.js`、`webui.py:200-211`），不在 dashboard 桶范围。

### A3. 浏览器存储 / Cookie

`dashboard.js` 全文未访问 `document.cookie / localStorage / sessionStorage`，session 完全交给 HttpOnly cookie（`webui.py:140-148`）。共享 `webui.js:82, 127` 写入侧边栏折叠 / 主题偏好两项非敏感 key（`nextbot-webui-sidebar-collapsed / nextbot-webui-theme`），不属 dashboard 桶问题。

### A4. 第三方资源 / supply chain

- `dashboard_content.html:97` 出站链接 `https://www.miaovps.com` —— **`target="_blank" rel="noopener noreferrer"`**（已合规，tabnabbing 防护到位，referer 不外泄）。
- `app_shell_base.html:8, 229-231` 全部 stylesheet 与 script 均为 same-origin `/webui/static/` 与 `/assets/`，**无任何 CDN / 跨源 script**，CSP `script-src 'self' 'unsafe-inline'` 闭合该面。
- dashboard 自身 0 内联 `<script>`，0 内联 `style="..."`，仅依赖 `dashboard.css` 与外链 `dashboard.js`。CSP 的 `'unsafe-inline'` 是 shell 层为兼容现有内联（如 `theme-init.js` 不算 inline，brand SVG 等也不是 script）保留的，与 dashboard 本身无关。

### A5. SVG 安全

`dashboard_content.html:9-12` 内联 SVG 仅有 `<path>` 几何（reload icon），无 `<script>` / `<foreignObject>` / `onload="..."` / `href="javascript:..."`。SVG `aria-hidden="true"`，仅装饰。

### A6. 安全综合评级

| 子项 | 评级 | 备注 |
|---|---|---|
| XSS | 低 | 全 textContent / createElement，无 innerHTML |
| CSRF | N/A | 仅 GET |
| Storage | 低 | dashboard 自身无 localStorage 调用 |
| Supply chain | 低 | 无外部 script |
| Clickjacking | 已防护 | shell 层 `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` |

---

## B. 性能

### B1. 资源加载

`render_console_page` 输出的 `<head>` 与 `</body>` 之前各注入了 stylesheet / script：

- CSS：`app-shell.css`（12 KB）+ `dashboard.css`（8.7 KB） → 共 ~21 KB raw，**未启用 `media` 拆分、未启用 `preload`**。
- JS（外链顺序）：`theme-init.js`（502 B，放 `<head>` 内同步）→ body 末尾 `api.js`（4.6 KB）→ `webui.js`（4.6 KB）→ `dashboard.js`（5.2 KB），合计 ~15 KB raw。
- **均未带 `defer` / `async`**（`app_shell_base.html:10, 229-231` + console_page.py:54 的 `<script src>` 直出）。由于这些 `<script>` 已位于 `</body>` 之前且不阻塞首屏文本，影响有限；`theme-init.js` 放头部是为防 dark-mode FOUC，可接受。
- 静态资源带 `?v=<mtime>` 版本号（console_page.py:33-34），缓存策略由 `FileResponse` 默认决定（无显式 `Cache-Control` / `ETag`，是 starlette 内置 etag 行为）。**严重度低、不是 dashboard 桶职责**，归 shell 层。

dashboard 桶范围内无可执行的资源拆分优化（单页 5.2 KB JS，3 倍 brotli 后 < 2 KB）。

### B2. fetch / 轮询

- 单次拉取：`dashboard.js:142-149`，`GET /webui/api/dashboard`，初次自动触发（`dashboard.js:165`）+ 用户点击 reload 触发（`dashboard.js:161-163`）。
- **无 setInterval / setTimeout 轮询**，无后台压力放大风险（grep `setInterval | setTimeout` 全空）。**符合 ≥ 30 s 约束**（本来就 0 频率）。
- **无 AbortController**：reload 期间 `loading` 状态用 `loading` 局部布尔锁住二次入口（`dashboard.js:134-136`），按钮也被 `disabled = true`（`dashboard.js:79`），重复点击不会并发请求。**但**：页面 unload / 切走时（如点击侧边栏跳转），fetch 不会主动 abort，浏览器自然 cancel —— 单次请求无 leak 风险，可接受。
- **无显式超时**：`fetch` 默认无 timeout，依赖浏览器层（通常数十秒）。dashboard 后端 `get_dashboard_metrics()` 是同步 DB 查询（`stats.py:75-108` 多个 `session.query(...).scalar()`），在 SQLite 上一般 < 100 ms；**P0 风险不存在**。`fetch + 无超时 + 无 AbortController`，单点击重试期间用户卡 spinner 体验略差但功能正确。
- **无失败重试**：`apiRequest` 抛错后 `setStatus(error.message, "error")` 一次性结束（`dashboard.js:154-156`），用户可手动按 reload 重试，**有恢复入口**。

### B3. DOM 节点 / 渲染

- 初始 DOM 节点数 < 50（toolbar + 6 stat-card + 1 detail-card + 1 ad-card），无大列表，无虚拟滚动需求。
- `replaceChildren(fragment)`（dashboard.js:118）是单次原子替换，无 read-then-write 顺序问题，不会触发多次强制 reflow。
- `formatNumber` 用 `Number.toLocaleString("zh-CN")`（dashboard.js:50），逐次调用，6 个 stat-value 共 6 次 ToLocaleString，影响可忽略。

### B4. 动画 / 过渡

仅 1 处 transition：`dashboard.css:354` `.ad-cta { transition: background-color 0.15s ease, color 0.15s ease; }`，hover 触发，性能合理（仅 paint，无 layout）。**无 `@keyframes`，无 `animation`**。

### B5. 性能综合评级

| 子项 | 评级 | 备注 |
|---|---|---|
| 资源大小 | 低 | dashboard.js 5.2 KB / dashboard.css 8.7 KB |
| 阻塞渲染 | 中（shell 层） | `<script>` 无 defer，但位于 body 末尾，不阻塞首屏文本；shell 桶问题，不在本桶 |
| fetch 并发 / 轮询 | 低 | 单次 GET，按钮 disabled 防重，无轮询 |
| AbortController | 低 | 无切页 abort，但单请求无 leak |
| 超时 / 重试 | 中 | 无显式超时；失败靠用户手动重试 |
| DOM | 低 | 节点数 < 50，无大列表 |
| 动画 | 低 | 仅 1 处 hover transition |

---

## C. 文案审计（CLAUDE.md 规范，修复前 → 修复后）

按 CLAUDE.md：成功 `动作 + 结果`，失败 `动作 + 结果，原因`；**不得包含操作对象名**；动词通用；失败原因**原样透传**。

### C1. 静态文案（dashboard_content.html）

| 行号 | 当前文案 | 问题 | 建议修复后 |
|---|---|---|---|
| **8, 13** | `<button id="reload-btn">...<span data-label>刷新数据</span></button>` | "刷新数据" 包含对象名"数据"；动词应通用为"刷新" | `<span data-label>刷新</span>` |
| 5 | `最近更新 <span id="dashboard-updated-at">—</span>` | 短语合规（meta 标签，非反馈），保留 | 保留 |
| 22 | `<div id="loading" class="empty">正在拉取仪表盘数据…</div>` | "正在拉取仪表盘数据" 包含对象名"仪表盘数据"；建议中性"加载中" | `加载中…`（与 dashboard.js:80 的 "刷新中..." 用语保持一致；省略号风格统一为 ellipsis `…`） |
| 26 | `<h2>关键指标</h2>` | 章节标题，非反馈文案，合规 | 保留 |
| 30, 34, 38, 42, 46, 50 | `服务器 / 注册用户 / 身份组 / 命令总数 / 已启用命令 / 累计执行` | 卡片 label，非反馈文案，合规 | 保留 |
| 58 | `<h2>运行状态</h2>` | 合规 | 保留 |
| 64 | `<h3>Bot 连接概览</h3>` | "Bot" 与中文"连接概览"之间已有空格 ✓；合规 | 保留 |
| 69 | `已连接的 Bot ID` | "Bot ID" 之间无空格但 "Bot" + "ID" 是同一英文术语词组，本身不需要中间空格；中英文交界 `的 Bot` 已留空格 ✓ | 保留 |
| 71 | `<span class="tag-badge none">暂未连接</span>` | 这是占位（在 JS 渲染前显示），dashboard.js:107 渲染时会替换为 `"无"` —— **两处文案不一致** | 与 dashboard.js:107 对齐为 `"无"` 或反向把 JS 改为 `"暂未连接"`；推荐前者更简洁 |
| 80 | `<h2>合作推广</h2>` | 合规 | 保留 |
| 85 | `<p class="ad-kicker">Sponsored</p>` | 全英文 kicker，与全站中文环境略突兀但不违规；可改成 `赞助` | （非阻塞）`赞助` 或保持 `Sponsored` |
| 87 | `喵云 MiaoVPS` | 中英文之间已留空格 ✓ | 保留 |
| 88 | `<span class="ad-title-sub">高性价比云服务器</span>` | 中文文案合规 | 保留 |
| 90 | `高可用云主机、快速部署与弹性扩容，适配个人项目与小团队业务场景。` | 中文文案合规 | 保留 |
| 92-94 | `稳定网络 / 弹性升级 / 多地区节点` | 合规 | 保留 |
| 98-99 | `立即访问 / www.miaovps.com` | 合规 | 保留 |
| 82 | `aria-label="广告位"` | a11y 文案合规 | 保留 |
| 24, 56, 78 | `aria-label="关键指标 / 详细信息 / 推广信息"` | a11y 文案合规 | 保留 |

### C2. 动态文案（dashboard.js）

| 行号 | 当前文案 / 用法 | 问题 | 建议修复后 |
|---|---|---|---|
| **80** | `setReloadButtonText(loading ? "刷新中..." : "刷新")` | 1) `...` 是三个 ASCII 点而非中文省略号 `…`；2) 与按钮静态文案 "刷新数据"（dashboard_content.html:13）冲突，loading 态会变 "刷新中..." 但初始 / 收尾态是 "刷新数据" → 抖动 | `loading ? "刷新中…" : "刷新"`，并把 HTML 的 "刷新数据" 也改为 "刷新" |
| **122** | `runningStatusNode.textContent = String(data.running_status \|\| "--")` | 1) 占位 `"--"`（半角双 dash）与模板的 `—`（U+2014 长破折号，dashboard_content.html:5, 31, ...）**不一致**；2) `running_status` 已由后端格式化，前端不能改写，**这条仅做 fallback 文案统一** | `String(data.running_status \|\| "—")` |
| **123-128** | `formatNumber(value)` → `"--"`（dashboard.js:49） | 同上，与模板 `—` 不一致 | `return "—";`（U+2014） |
| **129** | `dashboardUpdatedAtNode.textContent = String(data.generated_at \|\| "--")` | 同上，`--` → `—` | `String(data.generated_at \|\| "—")` |
| **107** | `node.textContent = "无";`（空连接列表） | "无" 与模板初始占位 `暂未连接`（dashboard_content.html:71）不一致 | 与 C1 / L71 选项二选一对齐，推荐 `node.textContent = "无";` 并把 HTML 也改为 `<span class="tag-badge none">无</span>` |
| **122** | `String(data.running_status \|\| "--")` | `running_status` 是后端 API 原始字段值（"运行中（X Bot 已连接）" / "服务已启动（暂无 Bot 连接）"，见 `stats.py:118-120`），**符合 CLAUDE.md 第 5 条原始字段原样保留** | 保留语义；仅修 fallback 占位 |
| **147** | `apiRequest(..., action: "加载", ...)` → api.js:59 构造 `加载失败，<reason>` | 动词 "加载" 通用且无对象名 ✓；reason 由 api.js 透传 `error.message`（api.js:43, 131-133）✓ | 保留 |
| **155** | `setStatus(error instanceof Error ? error.message : "加载失败", "error")` | 走 ApiRequestError 时 `error.message` 已经是 `"加载失败，<reason>"`（api.js:59 / 122 / 133 / 148）；非 Error 兜底为 `"加载失败"` | 保留，**逻辑正确**；可加注释说明 message 已由 apiRequest 组装 |
| **139** | `setStatus("")` | 清空 status，无文案 | 保留 |
| **153** | `setStatus("")` | 成功路径不展示 toast（dashboard 是只读页面，刷新后数字变化即反馈，CLAUDE.md 鼓励**不冗余**），合规 | 保留 |
| 65-72 | `setReloadButtonText` 的 `<span data-label>` 替换逻辑 | 实现合规，无文案串接问题 | 保留 |
| 50 | `parsed.toLocaleString("zh-CN")` | 千位分隔符走中文格式 ✓ | 保留 |

### C3. a11y 文案

| 行号 | 内容 | 评级 | 备注 |
|---|---|---|---|
| dashboard_content.html:18 | `role="status" aria-live="polite"` | 合规 | status 容器，配 `aria-live="polite"` |
| dashboard_content.html:24, 56, 78, 82 | `aria-label="关键指标 / 详细信息 / 推广信息 / 广告位"` | 合规 | 区块语义清晰 |
| dashboard_content.html:9, 213-218（shell） | `<svg aria-hidden="true">` | 合规 | 装饰性 SVG 隐藏 |
| **dashboard_content.html:8** | reload 按钮**有可见 label 子节点 "刷新数据"** | 合规（非 icon-only） | 不需要额外 `aria-label` / `title` |
| **dashboard.js:79-80** | 按 reload 时 `reloadButton.disabled = true`，但 **未设置 `aria-busy="true"`**（在 `<section id="stats-grid">` 上） | 轻微缺陷 | 建议 loading 期间在 `statsGridNode` 加 `aria-busy="true"`，结束移除；或在 `loading` div 加 `role="status"` —— **此为 P3 改进项**，非 CLAUDE.md 文案规范问题 |
| **dashboard.js:155** | error 时 `setStatus(..., "error")` 仅改 CSS 类 `alert error`（dashboard.js:63） | a11y 提示 | `role="status" + aria-live="polite"` 已让屏幕阅读器朗读 error message，**合规**；但 alert 角色级别更高，对 error 可考虑 `role="alert"`，**P3** |

### C4. icon-only 按钮

dashboard 桶内 reload 按钮（dashboard_content.html:8-14）含 visible label `<span data-label>...`，**非 icon-only**，合规。dashboard 模板内**无其他 icon-only 按钮**。（shell 层 sidebar-toggle / theme-toggle / logout-btn 是 icon-only，已带 `aria-label`，见 webui.js:45, 69、`app_shell_base.html:212`，不在本桶。）

### C5. 中英文空格规范

通篇核查：

| 位置 | 内容 | 评级 |
|---|---|---|
| dashboard_content.html:64 | `Bot 连接概览` | ✓（Bot 与中文之间有空格） |
| dashboard_content.html:69 | `已连接的 Bot ID` | ✓ |
| dashboard_content.html:71 | `暂未连接`（含义 "暂未连接 Bot"，省略宾语，合理） | ✓ |
| dashboard_content.html:87 | `喵云 MiaoVPS` | ✓ |
| dashboard_content.html:99 | `www.miaovps.com` | N/A（纯 URL） |
| stats.py:118, 120（后端返回 `running_status`） | `运行中（X Bot 已连接）` / `服务已启动（暂无 Bot 连接）` | ✓（已在前端原样透传） |

dashboard 桶**无中英文空格违规**。

---

## D. UX

### D1. 错误恢复

错误 toast（`#status.alert.error`）显示 `加载失败，<原始 reason>`，**用户可点击右上角 `<button id="reload-btn">` 重试**，恢复入口存在。错误状态不会自动消失（无 timer 清除），下次 reload 成功后 `setStatus("")` 才清空（dashboard.js:153），符合"明确反馈"原则。

### D2. focus 管理

- 初次加载 / reload 触发期间 `reloadButton.disabled = true`（dashboard.js:79），focus 仍可保留在按钮上，**禁用按钮时焦点会被浏览器自然推走**到 body，可能丢失（轻微 a11y 缺陷，P3）。建议改为 `aria-busy="true"` + 保持可 focus 或 loading 结束后 `reloadButton.focus()` 恢复，但 dashboard 是只读单按钮页面，影响小。
- tab 顺序：reload → status（被 aria-live 朗读，非 focusable）→ stat-card（非 focusable）→ `<a class="ad-cta">`，自然合理。

### D3. 响应式

`dashboard.css` 三档断点：

- `@media (max-width: 1120px)`（dashboard.css:376-380）：`stats-grid-list` 由 3 列降为 2 列。
- `@media (max-width: 920px)`（dashboard.css:382-397）：toolbar / section-head / detail-card-head / ad-banner 全部纵向堆叠；`.ad-cta` 宽度 100%。
- `@media (max-width: 640px)`（dashboard.css:399-407）：`stats-grid-list` 单列，`stat-value` 字号 28 → 24，ad title 22 → 18。

覆盖合理。在 360 px 窄屏（典型手机），`.stat-card` 单列堆叠，每张约占满宽，文字 24 px 不溢出（containers 都 `min-width: 0`，dashboard.css:146, 278）。**未发现明显响应式 bug**。

### D4. Dark mode 适配

`html.dark` token 重定义在 `app-shell.css:60-78`：`--bg-layout / --surface / --surface-soft / --surface-cream-strong / --text / --text-muted / --text-muted-soft / --primary / --primary-active / --primary-soft` 等。`dashboard.css` 全部颜色均通过 `var(--xxx)` 引用，**dashboard 自身完全适配 dark mode**，无硬编码颜色（grep 验证：dashboard.css 中 `#` 仅出现在注释里）。

需关注：`.alert.error { color: var(--primary); border-color: var(--primary); }`（dashboard.css:73-76）在 dark 模式下用 `--primary: #cc785c`（珊瑚色），与 dark 背景 `#181715` 对比度可保证（W3C 估算 > 4.5 : 1）。

### D5. UX 综合评级

| 子项 | 评级 |
|---|---|
| 错误恢复 | 良 |
| focus 管理 | 中（按钮 disabled 后焦点丢失，P3） |
| 响应式 | 良 |
| Dark mode | 优 |
| 视觉一致性 | 中（占位符 `--` vs `—`、按钮文案 "刷新数据" vs "刷新中..." vs "刷新" 不统一） |

---

## E. JS 错误处理

### E1. try / catch 完整性

- `loadDashboardData`（dashboard.js:133-159）：try / catch / finally 完整闭合，catch 捕获 Error / 非 Error 两种形态（dashboard.js:154-155）。
- `apiRequest`（api.js:103-160）：fetch 自身的 network error 在 :115-125 已包装为 `ApiRequestError`；HTTP !ok 在 :130-139；`expectedStatus` 不匹配在 :141-154。三条失败路径都构造规范 message。
- **未发现裸 `.then` / `.catch` 链断点**。

### E2. unhandled rejection

- dashboard.js:162、165 两处 `void loadDashboardData()` 用 `void` 显式弃值，**配合内部 try / catch 永不会 reject**，无 unhandled rejection 风险。
- api.js 内部不存在 fire-and-forget promise。

### E3. setInterval / setTimeout / unload 清理

- dashboard.js 全文 **无 setInterval / setTimeout**，**无需清理**。
- 无 `addEventListener("beforeunload"...)` / `addEventListener("visibilitychange"...)`，dashboard 是简单只读页，**当前不需要**。

### E4. 状态机一致性

`loading` 局部变量（dashboard.js:42）+ `hasLoaded` 局部变量（dashboard.js:43）联合控制 3 个区块的显隐：

- 初次加载（loading = true, hasLoaded = false）：显示 `#loading`，隐藏 `#stats-grid` `#dashboard-panels`（:84-87）。
- reload 加载（loading = true, hasLoaded = true）：显示 `#loading`，**保留 stats-grid / panels 旧数据**（:82-88 的 `if (!hasLoaded)` 守卫）→ 用户重读时不闪烁，UX 友好。
- 完成（loading = false, hasLoaded = true）：隐藏 loading，显示 stats / panels（:91-95）。

**首次失败的边界 case**：初次加载失败时 `hasLoaded` 仍为 false，`setLoadingState(false)` 在 :91-95 仅隐藏 loading，**stats-grid / panels 保持隐藏**，页面只剩 error toast + reload 按钮，用户必须点 reload —— 合理。

### E5. requiredNodesReady 守卫

`dashboard.js:19-38` 对所有 14 个 `document.getElementById` 做 Boolean 校验，任一缺失则 `return`，避免后续空引用爆炸。**优秀实践，合规**。

### E6. 错误处理综合评级

| 子项 | 评级 |
|---|---|
| try / catch 覆盖 | 优 |
| unhandled rejection | 优 |
| 定时器清理 | N/A（无定时器） |
| 状态机 | 良（初次失败 UX 略空，可接受） |

---

## 结论 + 修复优先级

dashboard 桶是**整个 WebUI 安全 / 性能基线最干净**的页面之一：单一只读 GET、无内联事件、无 innerHTML、无外部 CDN、无轮询、无 storage、a11y 主框架就位。本桶问题集中在**文案一致性**与**少量 UX 细节**。

### P1（应修，符合 CLAUDE.md 第 7 条 + 文案规范）

1. **`dashboard_content.html:13`** 按钮静态文案 `刷新数据` → `刷新`（去掉对象名 "数据"，与 JS loading 态 `"刷新中…"`、收尾态 `"刷新"` 三态一致）。
2. **`dashboard_content.html:22`** loading 占位 `正在拉取仪表盘数据…` → `加载中…`（去对象名 + 与 JS `"刷新中…"` 风格统一）。
3. **`dashboard.js:80`** `"刷新中..."`（3 个 ASCII 点） → `"刷新中…"`（U+2026 中文省略号），与中文排版规范一致。
4. **`dashboard.js:49 / 122 / 129`** fallback 占位 `"--"` → `"—"`（U+2014），与模板 `dashboard_content.html:5, 31, 35, 39, 43, 47, 51, 65` 的 `—` 统一。
5. **`dashboard_content.html:71` 或 `dashboard.js:107`** 二选一对齐：将 `暂未连接` 改为 `无`，或反向把 JS 改为 `暂未连接`。推荐 **`暂未连接` 改 `无`**（更简洁、与 `.tag-badge.none` 语义贴合）。

### P2（建议改）

6. **a11y `aria-busy`**：loading 期间在 `#stats-grid` 加 `aria-busy="true"`（在 `#loading` 可见的同时），结束移除（`dashboard.js:82-95`）。
7. **fetch 超时 / abort**：可选 —— 在 `apiRequest` 接 `AbortSignal.timeout(15_000)` 这种 15 s 兜底，避免后端假死时用户卡 spinner（属共享 api.js 改造，非 dashboard 单桶）。

### P3（可不修）

8. **error toast `role="alert"`**：当前 `role="status"` 已能朗读，升级 alert 仅提升优先级；非阻塞。
9. **`Sponsored` kicker** 改 `赞助`：风格选择，无规范违反。
10. **focus 恢复**：reload 按钮 disabled 后焦点丢失到 body，可在 `setLoadingState(false)` 时 `reloadButton.focus()`，但 dashboard 是单按钮页，影响极小。

### **不在本桶职责**（仅记录，不修）

- `<script>` 无 `defer`（shell 层，所有页面统一）
- CSP `'unsafe-inline'` 保留（shell 层）
- 静态资源 `Cache-Control` 缺失（shell 层）
- `webui.js` localStorage 主题 / 折叠偏好（shell 层，非敏感）
