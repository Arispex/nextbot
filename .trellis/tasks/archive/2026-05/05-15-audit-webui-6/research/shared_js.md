# Shared WebUI JS Audit — `api.js` / `webui.js` / `theme-init.js`

- **Query**: 审 nextbot 项目 WebUI 共享 JS 模块（API 客户端、app 引导、theme 预着色）
- **Scope**: 仅 3 文件
  - `server/webui/static/js/api.js` (278 LOC)
  - `server/webui/static/js/webui.js` (163 LOC)
  - `server/webui/static/js/theme-init.js` (17 LOC)
- **Date**: 2026-05-15
- **跨模块禁区**: `commands.js / servers.js / users.js / groups.js / warehouse.js / shop.js / lottery.js / settings.js / templates/*.html / 后端 server/routes/*` 一律仅作 prior art 引用，发现归 scope-out backlog

## 总览

| 严重度 | 数量 |
| --- | --- |
| Critical | 0 |
| High | 0 |
| Medium | 6 |
| Low | 9 |
| Scope-out backlog | 4 |

共享层 fix 都会放大到 9+ 业务页面，但目前没有 Critical / High 级别遗留 —— 主要因为 api.js 已被前几轮 audit 多次倒逼加固（R2 token chain / abort 合并 / timeout fallback / 401 跳转），webui.js 极简（只做侧边栏 + 主题 + logout），theme-init.js 17 LOC 内只有 localStorage + matchMedia。

### Top 3 优先

1. **M-1** `unwrapData` 抛裸 `Error("返回数据格式错误")`，跳出 `ApiRequestError` 契约 — 所有 caller 拿到的错误缺少 action 前缀，违 CLAUDE.md "动作 + 结果，原因" 规范。
2. **M-2** `webui.js` logout 路径 `_error` swallow + 跳转登录页，DELETE 失败用户无感知；同时无 abort 即刻 reload 导致竞态。
3. **M-3** `theme-init.js` / `webui.js` 主题切换无 `prefers-color-scheme` live listener，系统切换主题用户不会跟随；且 theme-init.js 的 `localStorage` failure path 默认 light（忽略系统 dark prefer），FOUC 之外引入"用户系统是 dark 但首次加载 light"反差。

---

## Findings

### M-1 `unwrapData` 抛裸 Error，跳出 ApiRequestError 契约
**File**: `server/webui/static/js/api.js:86-92`
**Dimension**: ux / copy
**Issue**: `unwrapData` 在 payload 缺 `data` 字段时直接 `throw new Error("返回数据格式错误")`，不是 `ApiRequestError`。所有 caller 的 catch 链拿到的 `error.message = "返回数据格式错误"`，**没有 action 前缀**（如"加载失败，..."），违反 CLAUDE.md "动作 + 结果，原因" 用户反馈规范。已被 servers R2（F-B-3 变体）、commands R3（B-OUT-1）两次标 scope-out backlog 至今未修。`buildActionFailureMessage` 已是公共 helper，但本路径未走它。
**Fix sketch**:
```js
const unwrapData = (result, { action = "" } = {}) => {
  const payload = unwrapPayload(result);
  if (!payload || typeof payload !== "object" || !("data" in payload)) {
    throw new ApiRequestError(
      action ? buildActionFailureMessage(action, "返回数据格式错误") : "返回数据格式错误",
      { code: "invalid_response_shape", reason: "返回数据格式错误" }
    );
  }
  return payload.data;
};
```
caller 渐进迁移 `unwrapData(payload, { action })`；旧签名兼容（不传 action 退化为旧文案）。
**Risk if unfixed**: 用户在罕见后端 schema 漂移场景看到"返回数据格式错误"裸字符串，跨模块文案体验割裂。

---

### M-2 logout 路径吞 DELETE 错误且立即 reload，竞态 + 用户无感知
**File**: `server/webui/static/js/webui.js:135-153`
**Dimension**: ux / security
**Issue**:
1. `try { await api.apiRequest(...) } catch (_error) { /* swallow */ } finally { window.location.assign("/webui/login") }` — DELETE 失败（网络中断 / 服务端 500 / 超时）一律静默跳登录页，用户以为"已登出"，但 cookie / session 可能仍有效。
2. `logoutButton.disabled = true` 之后未恢复 — 若 finally `window.location.assign` 因浏览器策略（如阻止跨域）未生效，按钮永久禁用。
3. `await apiRequest` 用默认 15s timeout — 用户点击后最多卡 15s 才跳转。
4. 无 `event.preventDefault()` 也无防双击（虽然 `disabled=true` 防了），但 disabled 之前的事件已经入队。

**Fix sketch**:
- 失败时 toast 显示"退出失败，<原始 reason>"，用户可决定是否强制跳转；或保留"无论成败都跳转"但缩短 timeout（如 5s）并在 catch 内补 `console.warn` 留排查痕迹。
- 移除 `disabled=true` 改为状态文案+忽略后续点击，或在 finally 内 `disabled=false` 再 reload。
**Risk if unfixed**: 用户在断网场景看似"登出成功"但 session 仍活，安全心智模型与后端实际状态不一致。

---

### M-3 主题预初始化不响应系统 `prefers-color-scheme` 实时切换
**File**: `server/webui/static/js/theme-init.js:1-17`, `server/webui/static/js/webui.js:64-70,123-133`
**Dimension**: ux
**Issue**:
1. `theme-init.js` 仅在脚本运行的瞬间读取 `prefers-color-scheme`，无 `matchMedia(...).addEventListener("change")` 监听。用户在使用过程中切换系统主题（macOS 自动昼夜模式、Windows 主题计划）页面不会跟随。
2. `webui.js` 的 `themeToggle` 只在用户手动点击时写 localStorage；用户从未点击过 toggle 的情况下系统主题切换不生效。
3. `localStorage.setItem` 失败（隐私模式 / quota）静默吞错（webui.js:128-130 / theme-init.js:14-16），未来无法排查"为什么我的主题没保存"。
**Fix sketch**:
- 在 webui.js 末尾加：
  ```js
  if (window.matchMedia && !localStorage.getItem("nextbot-webui-theme")) {
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e) => { root.classList.toggle("dark", e.matches); syncThemeButton(); };
    if (mql.addEventListener) mql.addEventListener("change", handler);
    else if (mql.addListener) mql.addListener(handler);
  }
  ```
- localStorage 失败考虑 sessionStorage / 内存兜底，避免每次刷新都跟随系统。
**Risk if unfixed**: 浅 UX 不一致；用户切夜间模式时 webui 仍是日间主题，疑似 bug 反馈。

---

### M-4 `apiRequest` 不默认设置 `Accept: application/json` 与 `Content-Type`
**File**: `server/webui/static/js/api.js:179-265`
**Dimension**: security / 一致性 / 重复劳动
**Issue**: `apiRequest` 不预置任何 default headers。每一个 caller（servers.js / commands.js / users.js / groups.js / warehouse.js / shop.js / lottery.js / settings.js）都得手写 `headers: { "Content-Type": "application/json", Accept: "application/json" }`，已 grep 出 18+ 处重复。任一处漏写 Accept，后端如果返回 HTML 错误页（如 502/网关错误）会绕过 `parseJsonSafe` 兜底（json() 失败 → null）但 `response.ok=false` 路径里 `finalReason = "HTTP 502"`，文案虽然降级了但语义已经丢。
**Fix sketch**:
```js
const mergedHeaders = {
  Accept: "application/json",
  ...(body != null && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
  ...headers,  // caller override 优先
};
```
**Risk if unfixed**: 任一新页面漏写 header 出 bug；当前没有 active bug 但模式脆弱。

---

### M-5 `apiRequest` 401-unauthorized 自动跳转含 token reveal / sensitive query 的 URL
**File**: `server/webui/static/js/api.js:222-235`
**Dimension**: security / privacy
**Issue**: 401 时拼 `next = window.location.pathname + window.location.search`，若用户当前 URL 中带敏感 query（如调试场景 `?debug_token=...`、第三方追踪 utm、或将来引入的 search 关键字含 token / API key）会被 encodeURIComponent 后塞进 `/webui/login?next=...`，登录页 server 端 `_sanitize_next_path` 已存在，但日志（access log）会留 next 参数明文。同时 `pathname` 含 `/webui/api/servers/{id}/token` 这类敏感路径也会被 echo 回 login 页。
**Fix sketch**:
- 只保留 `pathname`，丢弃 `search`：`const currentPath = window.location.pathname;`
- 或加 query 白名单（仅放行 page / filter 等业务 query，丢弃含 `token` / `secret` / `key` 字符串的 key）。
**Risk if unfixed**: 日志泄露敏感 query；access log / nginx log 长期保留含 next=... 的 GET 请求。

---

### M-6 `apiRequest` 401 跳转后 `throw new ApiRequestError` 可能跑在 unload 之前但调用栈被卸载打断
**File**: `server/webui/static/js/api.js:229-235`
**Dimension**: ux
**Issue**: 代码注释承认了 "实际页面已开始卸载"，但 caller `await api.apiRequest(...).catch(showToast)` 时若浏览器 unload 较慢，会先短暂闪现"登录已过期，正在跳转登录页" toast，再瞬间被新页面 replace。极端时（缓慢渲染 / devtools throttle）甚至会持续展示。文案是"动作 + 结果"形式（"登录已过期，正在跳转登录页"），主谓宾混合：动作"登录"+ 状态"已过期"+ 后续"正在跳转登录页"，三段。
**Fix sketch**:
- `window.location.replace(loginUrl)` 比 `.assign` 不留 history，且更早触发 unload，能避免 toast 闪烁。
- 也可考虑不 throw，改 `return new Promise(() => {})` 让调用链永久挂起，直到 unload 完成（pattern 来自 React Router `redirect()`）。
**Risk if unfixed**: 偶发 toast 闪烁；UX 抖动。

---

### L-1 `buildDetailReason` 与 caller 取 detail 的策略不一致
**File**: `server/webui/static/js/api.js:62-72`
**Dimension**: ux / 一致性
**Issue**: `buildDetailReason` 用 `；` 全量拼接，但 commands.js（R3 backend.md F-R3-5）/ servers.js 部分 caller 在自己 catch 内只取 `error.details[0].message`。结果同一后端 details 数组在不同页面展示不同（首项 vs 全量）。
**Fix sketch**: 不动 api.js，让 caller 统一用 `error.reason`（已是 buildDetailReason 产物）。在 api.js README/JSDoc 补"caller 应优先使用 error.reason 而非自取 details[0]"约定。
**Risk if unfixed**: 跨页面 UX 不一致 — Low 因为后端通常只返一条 detail。

---

### L-2 `apiRequest` 默认 `credentials` 未显式设置
**File**: `server/webui/static/js/api.js:194-199`
**Dimension**: security
**Issue**: `fetch(url, { method, headers, body, signal })` 无 `credentials` 字段。`fetch` 同源默认 `same-origin`（自 2017+ 浏览器一致），目前所有 caller 用相对路径 `/webui/api/...` 都是同源，cookie 会带。但**安全姿态**上建议显式 `credentials: "same-origin"`，防御未来引入跨域代理 / iframe 场景误改 default。
**Fix sketch**: `credentials: "same-origin"` 写死，禁止 caller 越权传 `"include"`。
**Risk if unfixed**: 当前无风险，仅是 defense-in-depth。

---

### L-3 `apiRequest` 无 `cache: "no-store"` / `no-cache` 控制，依赖后端 Cache-Control
**File**: `server/webui/static/js/api.js:194-199`
**Dimension**: security / perf
**Issue**: GET 请求若后端漏配 Cache-Control，浏览器可能缓存敏感响应（如 `/webui/api/servers/{id}/token` reveal endpoint）。前 audit 已对该 endpoint 标 WARN 日志 + auth 但未约束响应 cache header（跨模块，scope-out）。前端可主动加 `cache: "no-store"` 兜底。
**Fix sketch**: 默认 `cache: "no-store"` for 所有方法；GET caller 显式标 `cacheable: true` 才允许走 HTTP cache。
**Risk if unfixed**: 极端场景 token reveal 响应被中间代理或 BFCache 缓存。

---

### L-4 `apiRequest` 不强制 `Cache-Control` / `Pragma` request header 防御后退/前进缓存
**File**: `server/webui/static/js/api.js:194-199`
**Dimension**: security / ux
**Issue**: 用户点退出后按浏览器 "后退"，BFCache 可能还原已登出页面的 JS 状态（包含已 fetch 的敏感数据）。这是浏览器机制问题，但 webui.js 的 logout 路径可加 `pageshow` 监听强制 reload；api.js 层无需介入。归 scope-out。
**Fix sketch**: scope-out（webui.js 层处理 / BFCache 模式见 MDN `bfcache`）。
**Risk if unfixed**: 后退看到敏感数据残影；当前低风险。

---

### L-5 `webui.js` `sidebarLinks` 事件监听器内未做 `defaultPrevented` 检查
**File**: `server/webui/static/js/webui.js:97-105`
**Dimension**: ux
**Issue**: `sidebarLinks.forEach(link => link.addEventListener("click", () => { if (mobileMedia.matches) setMobileOpen(false); }))` 不检查 `event.defaultPrevented`。若未来有 link 加 `event.preventDefault()`（如 keyboard nav 拦截或者 dropdown），sidebar 仍会关闭，与"未跳转" 体验矛盾。
**Fix sketch**: `link.addEventListener("click", (e) => { if (e.defaultPrevented) return; if (mobileMedia.matches) setMobileOpen(false); })`。
**Risk if unfixed**: 当前无 callsite preventDefault，理论隐患。

---

### L-6 `webui.js` ESC 键全局监听未做 input-focus 排除
**File**: `server/webui/static/js/webui.js:107-111`
**Dimension**: ux
**Issue**: `document.addEventListener("keydown", event => { if (event.key === "Escape" && mobileMedia.matches && mobileOpen) setMobileOpen(false) })` 在 mobile 模式下移动端 mobileOpen 时无 input-focus 排除。**但实际**：mobile 视口 + sidebar 打开 + 在 input 输入时按 ESC 一般已是关掉操作，所以可接受。仅记备忘，不要求修。
**Fix sketch**: 不修。
**Risk if unfixed**: 极低。

---

### L-7 `webui.js` `mobileMedia.addListener` 老 API 兼容路径
**File**: `server/webui/static/js/webui.js:113-121`
**Dimension**: perf / 兼容
**Issue**: 用 `if (mobileMedia.addEventListener) ... else if (mobileMedia.addListener)` 双路径兼容老浏览器。Safari 14+ / Chrome 39+ / Firefox 55+ 都支持 `addEventListener` on MediaQueryList，老路径 2026 年已无目标。可移除。
**Fix sketch**: 直接 `mobileMedia.addEventListener("change", applySidebarState)`，删除老路径。
**Risk if unfixed**: 死代码 6 行。

---

### L-8 `theme-init.js` localStorage 异常路径默认 light，忽略系统 prefer
**File**: `server/webui/static/js/theme-init.js:14-16`
**Dimension**: ux
**Issue**: `catch (error) { document.documentElement.classList.remove("dark"); }` — 隐私模式 / quota / Safari ITP 等导致 localStorage 失败时一律降级为 light，忽略 `prefers-color-scheme: dark`。
**Fix sketch**:
```js
} catch (error) {
  try {
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", !!prefersDark);
  } catch (_inner) {
    document.documentElement.classList.remove("dark");
  }
}
```
**Risk if unfixed**: 隐私模式用户即便系统 dark 也看到 light 短暂闪烁后保持 light。

---

### L-9 `webui.js` 缺统一日志 / 调试钩子（catch 全静默）
**File**: `server/webui/static/js/webui.js:83-85,128-130,147-148,156-159`
**Dimension**: 可观测性
**Issue**: 4 处 `catch` 全静默吞错（localStorage 失败 / logout 失败 / 桌面折叠状态读取失败），无 console.warn / 任何上报。隐私模式 / quota / 网络故障难排查。共享 JS 没有统一日志门面（后端 CLAUDE.md "后端日志规则" 同理：前端也应有轻量统一入口）。
**Fix sketch**:
- 加 `window.NextBotWebUI.log = { warn(scope, msg, err) { /* console.warn 兜底 + 可挂上报 */ } }`，4 处 catch 调用 `log.warn("webui.js#sidebar-state", "...", error)`。
- 不要求每条都打，但 logout 失败这条（M-2 关联）建议至少 `console.warn`。
**Risk if unfixed**: 排查困难；用户报"退出没反应" 但无任何痕迹。

---

## Scope-out backlog（跨模块，不在 3 文件 scope）

- **OUT-1** caller 普遍重复手写 `Content-Type` / `Accept` 18+ 处（M-4 fix 后可移除）— 跨 9 个 caller 文件。
- **OUT-2** 共享层缺 toast / dialog helper — 每个页面各写一套 `showAlert` / modal，导致 commands.js R3 frontend.md F-R3-5 这类"取 details[0] vs api.js buildDetailReason 全量"不一致一直存在。建议下一轮 webui.js 升级为公共 modal / toast helper（含 focus trap / Escape / 焦点恢复 / aria-live="polite" / reduced-motion 媒体查询）— 已在 servers R2 F-B-9 标为 backlog。
- **OUT-3** BFCache / `pageshow` 退出登录后回退行为（L-4 关联）— 跨 webui.js + 后端 Cache-Control 协同。
- **OUT-4** 共享 `cache: "no-store"` 默认 vs 后端响应 Cache-Control（L-3 关联）— 跨前后端配合。

---

## 与历史 audit 对齐

| Prior finding | 当前判定 |
| --- | --- |
| servers R2 F-B-3（unwrapData 抛裸 Error，跨模块） | 升级为 **M-1**，建议本轮就在 api.js 内修，不再 scope-out |
| commands R3 B-OUT-1（同上） | 同 M-1 |
| commands R3 B-OUT-2（buildDetailReason vs caller 取首项） | 仍归 **L-1**，建议 caller 侧约束，不动 api.js |
| servers R2 F-B-9（缺 modal 公共 helper） | 仍 scope-out → **OUT-2**，待下一轮专项 |
| api.js R2 token 链改造 | 已 PASS，本轮无回归 |
| api.js R2 buildTimeoutSignal AbortSignal.any fallback | 已 PASS（line 107-171 三层兜底完整）|
| api.js 401 自动跳转登录页 + sanitize next | 本轮加 **M-5** / **M-6** 微调建议 |

---

## 安全维度逐项确认

| 检查项 | 结论 |
| --- | --- |
| 动态代码求值（eval / Function 构造器 / setTimeout(string)） | 3 文件全 0 处 |
| 拼接 HTML 注入（innerHTML / outerHTML / 同步流式写入 API） | 3 文件全 0；toast/dialog helper 不存在于本 scope |
| token / 敏感数据进 console.log | 3 文件全 0（webui.js / api.js / theme-init.js 均无 console.\*） |
| localStorage 存储 token / secret | 仅存 `nextbot-webui-theme` / `nextbot-webui-sidebar-collapsed`，无敏感字段 |
| URL 拼接未编码用户输入 | 仅 api.js:228 `next = encodeURIComponent(currentPath)`，已编码；M-5 是 query echo 隐患非编码缺失 |
| cookie 默认行为 | 同源 cookie 自动随 fetch，logout DELETE 路径有 cookie 携带（webui.py 端 `delete_cookie`）— OK |
| CSRF | webui.py 已有 cookie 鉴权 + 同源策略，前端层未涉及 — 出 scope |

## 性能维度逐项确认

| 检查项 | 结论 |
| --- | --- |
| Promise / fetch rejection 漏 catch | apiRequest 已强制 throw ApiRequestError；caller 漏 catch 是 caller 责任，本 scope 无问题 |
| 事件监听泄漏 | webui.js 注册的 listener 都绑在长生命周期 DOM（sidebar/toggle/theme/logout/document）— OK |
| 全局 fetch 重复 | apiRequest 不缓存请求；caller 各自管 — 本 scope 无问题（caller 侧已通过 abort / debounce 处理）|
| theme 闪烁 | theme-init.js 在 `<head>` 同步执行，pre-paint 设置 `<html class="dark">`，FOUC 已防（L-8 是异常路径降级问题）|
| AbortController 泄漏 | buildTimeoutSignal 三层兜底实现完整（line 107-171），timer / listener 都用 `{ once: true }` 自动解绑 — OK |

## 文案维度逐项确认

| 检查项 | 结论 |
| --- | --- |
| 默认 fallback 错误文案 | `HTTP <status>` / `请求超时` 简洁不带"请稍后重试" — 符合 CLAUDE.md "失败原因原样透传"|
| "动作 + 结果，原因" | api.js `buildActionFailureMessage` 已实现，M-1 是 unwrapData 跳出契约的反例 |
| 重复 "失败" 拼接 | servers R2 frontend.md 已验证：表单校验路径与 API 失败路径产物一致，无双重"失败" |
| 中英混排空格 | api.js error 文案纯中文，无混排；M-6 文案"登录已过期，正在跳转登录页"中英无混排，OK |
| error 对象 shape | `ApiRequestError { message, status, code, reason, details }` shape 完备，caller 取 `error.reason` 即可遵 "动作 + 结果，原因"。M-1 unwrapData 是唯一不符路径 |

---

## Caveats / Not Found

- **未发现 Critical / High 问题**。共享层经 R2 多轮加固后稳态。
- **toast / dialog 不存在于 3 文件 scope**：webui.js 仅做 sidebar + theme + logout；无 setToast / showDialog 实现。这是 OUT-2 的根因。如果题面"toast / dialog 是否 sanitize" 必须有结论 → 答："文件中无相关代码，每个业务页面各自实现"。
- 未审 CSS / HTML 模板 / 后端路由（按 scope 限制）。
- 未读 login.html 的内联 JS（不在 3 文件 scope，仅 grep 引用确认 next 参数链）。
- M-3 推荐补 `prefers-color-scheme` live listener 时需小心：若用户已手动点过 toggle（localStorage 有值），不应被系统切换覆盖（fix sketch 已含 `if (!localStorage.getItem(...))` 守卫）。

---

## 建议下一步

1. 本轮可实施 6 个 Medium，全部落在 3 文件 scope 内（无跨模块改动）：M-1 / M-2 / M-3 / M-4 / M-5 / M-6。
2. L-1 ~ L-9 视精力分批纳入；L-7（删死代码）可顺手。
3. OUT-1 ~ OUT-4 留下一轮"shared modal/toast helper"专项 audit。
