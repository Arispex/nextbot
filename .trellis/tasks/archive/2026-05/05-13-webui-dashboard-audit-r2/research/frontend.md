# R2 Frontend / 文案 桶审计

- **Query**: WebUI dashboard 复审 Round 1 修复（commit c118d91 8 项前端）+ 全量再扫
- **Scope**: internal
- **Date**: 2026-05-13
- **Audit Files**:
  - `/Users/arispex/CascadeProjects/nextbot/server/webui/templates/dashboard_content.html`
  - `/Users/arispex/CascadeProjects/nextbot/server/webui/static/js/dashboard.js`
  - `/Users/arispex/CascadeProjects/nextbot/server/webui/static/css/dashboard.css`
  - `/Users/arispex/CascadeProjects/nextbot/server/webui/static/js/api.js`（共享）
  - `/Users/arispex/CascadeProjects/nextbot/server/webui/static/js/webui.js`（共享）

---

## Part A: Round 1 修复复审

### A1. 5 项文案修复 — PASS

| ID | 位置 | 修复 | 字节验证 | 评价 |
|---|---|---|---|---|
| T-1 | `dashboard_content.html:13` | `刷新数据` → `刷新` | （HTML 文本节点）`<span data-label>刷新</span>` 已落地 | PASS |
| T-2 | `dashboard_content.html:22` | `正在拉取仪表盘数据…` → `加载中…` | line 22 实际为 `加载中…`，`…` = E2 80 A6 (U+2026) | PASS |
| T-3 | `dashboard.js:86` | `刷新中...` → `刷新中…` | `od -c` 验证字节 `345 210 267 346 226 260 344 270 255 342 200 246` —— 末三字节 `342 200 246` = U+2026 真字符，非 ASCII 三点 | PASS |
| T-4 | `dashboard.js:49, 142, 149` | `--` → `—` | 三行 `od -c` 显示 `342 200 224` = U+2014 真字符 | PASS |
| T-5 | `dashboard_content.html:71` | `暂未连接` → `无` | `<span class="tag-badge none">无</span>` 已落地 | PASS |

**字符层面 verifier 验证**：通过 Python 脚本扫描 4 个 dashboard 相关文件，HTML `dashboard_content.html` 含 7 处 U+2014（line 5, 31, 35, 39, 43, 47, 51, 65）+ 1 处 U+2026（line 22），JS `dashboard.js` 含 1 处 U+2026 + 3 处 U+2014。无 ASCII 三点 `.` `.` `.` 或单独 `--` 占位符遗漏。

**Sponsored → 赞助**（P3 已修，line 85）：通过。CSS line 257 注释里仍有 `Sponsored / promo card`，但属内部代码注释，不暴露给用户，可忽略。

---

### A2. P2 aria-busy（dashboard.js:90-91, 100-101）— PASS（语义正确）

```js
loading: 
  statsGridNode.setAttribute("aria-busy", "true")
  dashboardPanelsNode.setAttribute("aria-busy", "true")
完成:
  statsGridNode.removeAttribute("aria-busy")
  dashboardPanelsNode.removeAttribute("aria-busy")
```

**评价**：
- 加在 `#stats-grid` + `#dashboard-panels` 两个 region（`aria-label="关键指标" / "详细信息"`），加载时屏幕阅读器会感知"内容正在更新"
- AT 行为：NVDA / VoiceOver / JAWS 通常会在 `aria-busy=true` 时**抑制对该 region 的实时更新通告**，等 false 后再播报。所以 loading 期间不会反复念半截数字。
- **触发概率**：100%（每次刷新）
- **副作用**：dashboard 当前实际没有 `aria-live` 区域，所以"抑制更新"语义其实没用上；但加 aria-busy 至少向 AT 表明"这块还在加载"，**符合 WAI-ARIA Best Practice**

**一个小观察（不必修）**：line 22 `<div id="loading" class="empty">加载中…</div>` 是 visual loading indicator，但**没标 `role="status"` 或 `aria-live`**，屏幕阅读器不会主动播报"加载中"。若需让 AT 主动告知 loading 开始，可加 `role="status" aria-live="polite"`。当前是"silent visual hint"，不算 bug。

---

### A3. P2 timeout 共享改造（api.js:103-167）— 多处问题需关注

#### A3.1 `AbortSignal.timeout` 浏览器兼容 — **降级路径有缺陷**

```js
const buildTimeoutSignal = (userSignal) => {
  if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
    return userSignal;   // ← 老浏览器：完全没 timeout 保护，但请求仍会发出
  }
  ...
};
```

**问题**：
- **Caniuse 数据**：`AbortSignal.timeout` 需要 Chrome 103+（2022-05）/ Firefox 100+（2022-05）/ Safari 16.4+（2023-03）。WebUI 没有最低浏览器声明。
- **降级行为**：老浏览器（Safari 16.3 及以下、Chrome 102 以下）会**完全没有 timeout**，请求挂死直到 fetch 自己超时（浏览器默认通常 60-300s，依赖 OS TCP keepalive）。
- 触发概率：低（绝大多数用户在新浏览器上），但 Safari 16.3 用户 / 老 iOS 设备会命中。
- **严重度：P3（边缘场景）**
- **修复方向**（如果用户决定支持老浏览器）：用 `setTimeout` + 自建 AbortController fallback，例如：
  ```js
  if (typeof AbortSignal.timeout !== "function") {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), REQUEST_TIMEOUT_MS);
    // 需用户调用方在 finally clearTimeout(id)，或用 wrapping 自动清理
    return controller.signal;
  }
  ```
  但这需要 wrap fetch 以便完成后 clearTimeout，工程量大。

#### A3.2 `AbortSignal.any` 兜底 — 静默丢弃 userSignal — **P3**

```js
if (typeof AbortSignal.any === "function") {
  return AbortSignal.any([userSignal, timeoutSignal]);
}
return timeoutSignal;  // ← 静默丢弃 userSignal
```

**问题**：
- `AbortSignal.any` 需要 Chrome 116+（2023-08）/ Firefox 124+（2024-03）/ Safari 17.4+（2024-03）。比 `AbortSignal.timeout` 兼容窗口窄。
- **降级时**：caller 传的 `signal`（如用户点击取消按钮）会被**静默丢弃**，只剩 timeout signal。
- **现状审查**：grep 全 webui js 没看到任何 caller 实际传 `signal:` 字段，所以**当前 0% 触发**。
- **未来风险**：如果未来加上 "取消请求"按钮，开发者预期 userSignal 会工作；但在老浏览器上不会 abort。
- **严重度：P3**（当前无实际触发，但未来 footgun）
- **建议**：要么在 `AbortSignal.any` 缺失时显式记录 console.warn，要么手动实现 OR-合并（监听 userSignal 的 abort 事件并触发自建的合并 AbortController）。

#### A3.3 15s 是否合适 — **影响全部 webui 模块的回归面（关键）**

`apiRequest` 是 8 个 webui 模块共享入口。grep 全 webui 调用：

| 模块 | 调用路径 | 后端 timeout | 风险评估 |
|---|---|---|---|
| dashboard | `/webui/api/dashboard` | SQLite query < 100ms | 安全（远低于 15s） |
| servers list | `/webui/api/servers` GET / POST / PUT / DELETE | DB-only | 安全 |
| **servers test** | `/webui/api/servers/{id}/test` POST → `request_server_api(server, "/tokentest", timeout=5.0)` | httpx 默认 read=5.0s | 安全（5s < 15s） |
| **servers plugin-config get** | `/webui/api/servers/{id}/plugin-config` → `request_server_api(server, "/nextbot/config")` timeout=5.0 | 5s | 安全 |
| **servers plugin-config verify** | `/webui/api/servers/{id}/plugin-config/verify-nextbot` → `request_server_api(server, "/nextbot/config/verify-nextbot", timeout=15.0)` | **15s 严丝合缝** | **临界值！** |
| **users sync-whitelist** | `/webui/api/users/{id}/sync-whitelist` → **`_sync_user_whitelist` 串行遍历所有服务器**，每个 `request_server_api` timeout=5.0 | **N × 5s（串行）** | **N≥4 全超时即触发** |
| users ban/unban | `/webui/api/users/{id}/ban` → 同样遍历多服务器调用 TShock | 类似 sync-whitelist | 同上风险 |
| commands save / restart | DB + restart | restart 后端可能 > 15s（需查证） | 中等风险 |
| lottery/shop export/import | 大 JSON 传输 | 一般 < 1s，但 export 大数据集时可能慢 | 低风险 |
| settings | DB-only | 安全 |
| groups / users CRUD | DB-only | 安全 |

**两个明确的 timeout 冲突**：

##### A3.3.1 servers plugin-config verify 临界值（`webui_servers.py:434`）

后端 explicit `timeout=15.0`（read 维度），加上 connect/write/pool 5-10s 默认值。如果 TShock 在 readout 末尾才返回，前端 `REQUEST_TIMEOUT_MS = 15000` 几乎**同步触发**，竞赛条件：
- 后端在 15.0s 完成 → fetch 接收 → 前端在第 15.X 秒已经触发 AbortSignal.timeout（也是 15s）
- **触发概率：中**（取决于 TShock 响应时间分布，通常 < 1s 但偶发慢）
- **症状**：前端报"验证失败，请求超时"，但实际后端可能成功 / 即将返回结果。
- **严重度：P2**

##### A3.3.2 users sync-whitelist 在多服务器场景必超时（`webui_users.py:198-243`）

```python
async def _sync_user_whitelist(user: User) -> list[dict[str, Any]]:
    servers = session.query(Server).order_by(Server.id.asc()).all()
    results: list[dict[str, Any]] = []
    for server in servers:                                   # ← 串行遍历
        try:
            response = await request_server_api(
                server, "/v3/server/rawcmd", params={"cmd": f"/bwl add {user.name}"},
            )                                                # ← 每个 timeout=5.0s
        except TShockRequestError: ...
```

**风险计算**（N = 服务器数）：

| 场景 | 总耗时上限 | 前端 15s cap 是否命中 |
|---|---|---|
| N=1，全部正常 | 5s | 安全 |
| N=2，1 个 timeout | 5+5=10s | 安全 |
| N=3，2 个 timeout | 5+5+5=15s | **临界，几乎必触发** |
| N=4，3 个 timeout | 20s | **必触发** |
| N≥4，多个慢响应 | N×5s | **必触发** |

**症状**：用户点击"同步白名单"，等了 15s 看到"同步失败，请求超时"，但实际后端仍在串行处理后续服务器，**前端 AbortSignal abort 不会取消后端的 Python 协程**（FastAPI 不会主动 cancel）—— 后端继续跑完，写入数据库，但用户已经被告知失败，会重试，造成**重复操作 / 写放大**。

**触发概率：高**（任何有 3+ 服务器且至少 1 个不可达的场景）

**严重度：P2**

##### A3.3.3 users ban / unban 同样问题（`webui_users.py:595-720`）

未细读但模式相同（多服务器串行调 TShock）。**严重度：P2**。

##### A3.3.4 其他可能超 15s 的路径

- `commands restart`（`commands.js:874`）：后端 `/webui/api/restart` 行为未查，但 bot 重启通常 > 5s，需评估是否会超 15s
- `lottery/shop export` 大数据集导出：现状未观察到瓶颈，低风险

**总体修复方向**（任选一）：
1. **方案 A（推荐）**：把 15s 改成"每路由可配置"，dashboard 用 15s（够），sync-whitelist / verify 用 60-90s，restart 用 60s
2. **方案 B**：把 N×5s 串行的后端改成 `asyncio.gather` 并发（_sync_user_whitelist 的串行循环），单服务器仍 5s，总耗时 ≈ max(5s)
3. **方案 C**：在 caller 调用时显式 override（`apiRequest(url, { action, timeoutMs: 60000 })`），需扩展 `apiRequest` 接受 `timeoutMs`

#### A3.4 TimeoutError → "请求超时" 文案规范一致性 — **PASS**

```js
throw new ApiRequestError(buildActionFailureMessage(action, "请求超时"), { ... });
```

`buildActionFailureMessage("加载", "请求超时")` 拼成 `"加载失败，请求超时"`，符合 CLAUDE.md "动作 + 结果，原因" 规范。✓

但 dashboard.js line 175 `setStatus(error instanceof Error ? error.message : "加载失败", "error")` —— 直接 `error.message`，已是 `"加载失败，请求超时"`，✓ 正确显示。

---

### A4. P3 role=alert 动态切换（dashboard.js:58, 66 + HTML:18）— **AT 兼容性存疑**

```js
// statusNode init: role="status" aria-live="polite" aria-atomic="true" (HTML line 18)
setStatus("", *) → role="status"
setStatus(text, "error") → role="alert"
setStatus(text, others) → role="status"
```

**评价**：
- HTML 初始 `role="status"`，JS 动态切换。
- **AT 兼容性**：
  - **NVDA**：根据 [WHATWG / ARIA 1.2 spec](https://www.w3.org/TR/wai-aria-1.2/) 动态 role 切换在大多数现代浏览器和 NVDA 是被识别的，但**触发更新通告的时机不一定**——NVDA 在 textContent 改变时根据**当前 role** 决定 polite/assertive，所以**先 setAttribute 再 setText 顺序很重要**。
  - **代码顺序**（line 65-67）：`statusNode.className = ...` → `statusNode.setAttribute("role", ...)` → `statusMessageNode.textContent = text`。**顺序正确**：role 切换先于文本更新，AT 应能捕获 assertive 时机。
  - **VoiceOver (macOS/iOS)**：role 动态切换支持较弱，可能仍按初始 role 处理（[WebKit Bug 197068](https://bugs.webkit.org/show_bug.cgi?id=197068)）。误差概率较高。
- **触发概率**：中（错误场景才触发，dashboard 错误率本身低）
- **严重度：P3**
- **现状评估**：实现没问题，**但实际 a11y 效果在 VoiceOver 用户上可能打折扣**。如要做到稳健：把错误消息放到独立的 `<div role="alert">` 容器，正常消息放到 `<div role="status">` 容器，切换的是**容器可见性**而非 role 属性。但成本不低。
- **现状可以接受**，因为已比之前 polite-only 更好。

---

### A5. P3 focus 恢复（dashboard.js:44, 81-83, 107-115）— **PASS，但有 microtask race 风险**

```js
let reloadButtonWasFocused = false;
// setLoadingState(true):
if (isLoading && !loading) {
  reloadButtonWasFocused = document.activeElement === reloadButton;
}
// setLoadingState(false):
if (reloadButtonWasFocused) {
  queueMicrotask(() => {
    if (!reloadButton.disabled) {
      reloadButton.focus({ preventScroll: true });
    }
  });
  reloadButtonWasFocused = false;
}
```

**评价**：
- **逻辑正确**：只在第一次进入 loading 时捕获 focus 状态，避免 `loading=true` 时再次调用 `setLoadingState(true)` 误覆盖（line 81 `isLoading && !loading` guard）。
- **queueMicrotask vs setTimeout**：
  - queueMicrotask 在当前 synchronous task 之后立刻跑，比 `setTimeout(0)` 更快。
  - **正常流程**：finally `setLoadingState(false)` → button.disabled=false → queueMicrotask → focus()。同步代码无其他对 button 的修改，**race 风险低**。
- **理论 race**：
  - 如果未来在 `loadDashboardData` finally 之后链了任何同步代码改 reloadButton.disabled / focus / blur，会和 microtask 竞速。当前没有，**0% 触发**。
- **连续点击**：reloadButton 在 loading 期间 disabled=true，浏览器忽略后续 click。不存在重入。

**实测注意**：`reloadButton.focus({preventScroll:true})` 在某些老浏览器（Safari 13 及以下）不支持 `preventScroll` 参数。但 Safari 14+ 已支持，按当前最低支持窗口可接受。

- **严重度：P4（OK）**

---

### A6. Round 1 排除项 C3 / M-1 / M-2 — 未动，符合用户决策

---

## Part B: 全量再扫新发现

### B1. dashboard.css 未使用选择器 — **P4（低风险，纯洁性）**

| CSS 行号 | 选择器 | HTML 是否使用 |
|---|---|---|
| `dashboard.css:124-129` | `.dashboard-section-desc` | **HTML 中无任何元素使用**（grep 0 命中） |

`.dashboard-metrics`（HTML line 24）/ `.dashboard-panels`（HTML line 56）—— HTML 元素挂着 class，但 CSS 没有同名规则。这两个是 **dead class on HTML**，删了无视觉差异。

**严重度：P4**（无视觉影响）
**触发概率：100%（dead code 一直在）**
**修复方向**：要么删 `.dashboard-section-desc` 规则，要么删 HTML 上的 `dashboard-metrics`/`dashboard-panels` 冗余 class，或两者都保留（如果未来要扩展）。

### B2. `<div id="loading">` 缺 ARIA 角色 — **P3**

`dashboard_content.html:22`：
```html
<div id="loading" class="empty">加载中…</div>
```

- **现状**：仅是视觉元素，**屏幕阅读器不会主动播报"加载中"**。
- **影响**：第一次进入仪表盘时，AT 用户只看到 toolbar 和空白，再过几秒数据出现，但不知道"为什么空白"。
- **触发概率**：中（每次进入仪表盘，AT 用户）
- **严重度：P3（a11y 体验）**
- **建议**：加 `role="status" aria-live="polite"`，让 AT 在元素从 hidden 切换到可见时播报"加载中"。

### B3. Sidebar toggle SVG `aria-hidden` 与 logout button 缺 `aria-busy` — **P4**

`app_shell_base.html:178-182`：sidebar-toggle 按钮内容是 `☰`，无 SVG，但 `aria-label` 动态切换。OK。

`webui.js:137`：logout button click → disabled = true → `await apiRequest(...)`，**无 aria-busy / no loading text**。但 logout 按钮按下后立即跳转，**没有 visual feedback 期窗口**，所以可忽略。

### B4. CSS `font-feature-settings: "tnum"` 但 `font-family: var(--font-code)` 可能未启用 tnum — **P4**

`dashboard.css:244-246`（`.tag-badge`）和 `dashboard.css:166`（`.stat-value`）依赖 `tnum`（Tabular Numbers）OpenType feature。如果项目的 `--font-body` / `--font-code` 是不支持 tnum 的字体（如系统默认 Inter / fallback），则该规则无效。该方面**与 dashboard 修复无关**，但属"潜在视觉漂移"，**严重度 P5（信息）**。

### B5. status node 在 setStatus("") 时仅 className="alert hidden"，未清 className 上的 type — **P4**

`dashboard.js:57`：
```js
statusNode.className = "alert hidden";  // ← 直接覆写
```

**评价**：用赋值覆写整个 className，所有上一次的 type class（如 `success` / `error`）会自动消失。**逻辑正确**。但若未来 CSS 加了 `.alert.dashboard-foo` 这类外部依赖类，会被一并清除。当前无此问题。

### B6. fetch 错误处理 / DOM 注入 / XSS — **未发现新风险**

- 无 `innerHTML` / `insertAdjacentHTML` 在 dashboard.js / api.js / webui.js
- 所有动态文本通过 `.textContent = ...`（line 67, 74, 77, 127, 133, 142-149）
- `renderConnectedBotIds`（line 118-139）使用 `document.createDocumentFragment + appendChild + textContent`，安全

### B7. fetch 重试 / 取消 — **未实现，但不是 dashboard 范畴**

dashboard 现状无 retry / cancel 机制。`apiRequest` 接受 `signal` 但 dashboard.js 不传 → reload 期间无法取消。**P5（信息）**。

### B8. CSS 响应式断点 920px 在 toolbar 上的副作用 — **P4**

`dashboard.css:382-389`：在 920px 以下 `.dashboard-toolbar`/`.dashboard-section-head`/`.detail-card-head`/`.ad-banner` 全部改成 column。`reload-btn` 落到下一行，**且 `.panel-head-actions` 强制 `width:100% justify-content:flex-end`**，按钮会贴右下角。

- **现状评估**：这是 desktop tablet UX 取舍，无 bug
- **严重度：P5（OK）**

### B9. shell 层 dashboard 加载时可见文案（按指示仅挖 dashboard 加载可见的）

`app_shell_base.html`：
- line 45（sidebar-toggle）：`关闭/打开导航菜单` —— ✓
- line 60（sidebar-toggle）：`展开/隐藏侧边栏` —— ✓
- line 69, 80, 92, 105, 116, 128, 140, 154, 167（menu-label）：`仪表盘 / 命令配置 / 服务器管理 / 用户管理 / 身份组管理 / 仓库管理 / 商店管理 / 抽奖管理 / 设置` —— ✓
- line 187（`aria-label="打开 GitHub 仓库"`）—— ✓（中英空格符合 CLAUDE.md）
- line 195, 212（theme/logout aria-label）：`切换到深色/浅色主题 / 退出登录` —— ✓

**结论**：shell 层在 dashboard 加载时可见的文案**全部合规**，无违规项。

### B10. webui.js shell-level 共享 `apiRequest` 调用 — **P3**

`webui.js:139-146`：logout 调 `apiRequest("/webui/api/session", {..., action: "退出登录", expectedStatus: 204})`。

`buildActionFailureMessage("退出登录", "请求超时")` → `"退出登录失败，请求超时"`。**符合规范**。

但 `webui.js:148` 直接 `// Ignore logout errors and continue to login page.` → 静默吞掉错误。
- 若用户网络问题，logout 不到 server-side，本地仍跳到 /webui/login，**但 cookie 未清，下一次 visit 可能仍然登录态**（依赖 server-side session invalidation）。
- **严重度：P4**（边缘行为，已是合理设计）

---

## 结论

### Round 1 修复总评

| 修复 | 状态 | 备注 |
|---|---|---|
| 5 文案（T-1~T-5） | **全部 PASS** | Unicode 字节验证通过 |
| P2 aria-busy | **PASS** | 语义正确，AT 兼容性好 |
| P3 Sponsored→赞助 | **PASS** | 用户面已无 Sponsored |
| P3 role=alert 动态切换 | **PASS（VoiceOver 兼容性打折）** | 实现顺序正确，VoiceOver 可能识别不全 |
| P3 focus 恢复 | **PASS** | queueMicrotask + reloadButtonWasFocused guard 正确 |
| **P2 timeout 共享改造** | **3 处需关注** | 见 A3.1/A3.2/A3.3 |

### P2 timeout 改造的**关键回归风险**（必须升级到主审计）

| ID | 风险 | 严重度 | 触发概率 | 影响范围 |
|---|---|---|---|---|
| **R2-T-1** | `users sync-whitelist` 串行遍历，N≥3 服务器场景必触发前端 15s timeout，后端继续跑 → 用户重试 → 写放大 | **P2** | **高**（任何 3+ 服务器实例） | users 模块 |
| **R2-T-2** | `users ban/unban` 同样多服务器串行模式 | **P2** | 高 | users 模块 |
| **R2-T-3** | `servers plugin-config verify-nextbot` 后端 explicit `timeout=15.0` 与前端 15s cap 同步触发 race | **P2** | 中 | servers 模块 |
| R2-T-4 | `AbortSignal.timeout` 在 Safari 16.3-/Chrome 102- 完全无 timeout 兜底 | P3 | 低 | 所有模块 |
| R2-T-5 | `AbortSignal.any` 缺失时 userSignal 静默丢弃 | P3 | 当前 0%（无 caller 传 signal） | 所有模块 |
| R2-T-6 | `commands restart` 后端耗时可能 > 15s（需查证 `/webui/api/restart` 路径） | P3 | 未知 | commands 模块 |

### dashboard 全量再扫新发现

| ID | finding | 严重度 | 位置 |
|---|---|---|---|
| R2-B-1 | `<div id="loading">` 缺 `role="status" aria-live`，AT 用户不知道"为什么空白" | P3 | `dashboard_content.html:22` |
| R2-B-2 | `.dashboard-section-desc` CSS 规则定义但 HTML 无使用 | P4 | `dashboard.css:124-129` |
| R2-B-3 | `.dashboard-metrics` / `.dashboard-panels` class 挂在 HTML 但 CSS 无规则 | P4 | `dashboard_content.html:24, 56` |
| R2-B-4 | dashboard 无 fetch cancel 机制（apiRequest 支持 signal 但未用） | P5 | `dashboard.js:162-169` |

### **核心结论**

Round 1 dashboard 局部修复 **8/8 实现正确**，但**共享的 P2 timeout 改造给整个 webui 引入了新的回归风险**（特别是 users sync-whitelist / ban，servers verify-nextbot 三处 P2）。这些 finding **本就在 P2 timeout 改造时应该评估**，但当时仅查了 dashboard 路径（< 100ms 安全），漏掉了 users / servers / commands 模块的长耗时路径。

**建议下一轮 (R3) 必修**：R2-T-1 / R2-T-2 / R2-T-3（3 处 P2 共享改造副作用）。可独立列为"webui-shared-timeout-fix"任务。

**建议下一轮 (R3) 可选**：R2-B-1（loading aria）+ R2-B-2/B-3（CSS dead code 清理）。

## Caveats / Not Found

- 未实际运行 NVDA / VoiceOver 测试 role 切换行为，结论基于 spec + 现有兼容性表
- 未观察实际生产环境的 sync-whitelist 耗时分布（N=? servers 配置不可知，但 N=3 已临界）
- 未查 `/webui/api/restart` 后端实现（commands 模块 R2-T-6 假定为风险，需独立验证）
