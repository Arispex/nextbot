# R3 Frontend + 共享 js 桶审计

- **Scope**: dashboard.js / api.js / commands.js / users.js / dashboard_content.html / dashboard.css
- **审计基线**：commit `c1a96ca`（R2）
- **Date**: 2026-05-14

---

## Part A：R2 修复复审

### A-1 R2-T-4 老浏览器 setTimeout fallback timer 清理（api.js:141-167）

**LOW / 性能 hygiene**

- setTimeout 触发后 controller.abort() 被调用；但 **fetch resolve / reject（非 timeout、非 user abort）路径未清理 timer**：fetch 5s 内正常返回 200，timer 仍在 15s 后触发 → 调 controller.abort(...) 对已 settle 的 fetch 无副作用，但纯浪费
- closure 持有 controller 引用 15s
- 高频请求场景下未清理 timer 累积，GC 延迟到 timer fire 后才能回收
- **未观察到泄漏**，仅引用持续时间过长 ≤ 15s
- **修复方向**：返回 `{signal, dispose}` 让 apiRequest 在 finally clearTimeout，改造面较大可接受现状

### A-2 R2-T-4 TimeoutError 类型识别老浏览器 fallback 文案缺口（api.js:173-177）

**LOW / 仅老浏览器**

- `isTimeoutError` fallback 路径（line 176）检查 `error.name === "AbortError" && /timeout/i.test(error.message)`
- 旧浏览器 fetch 抛 AbortError message 通常是 "Aborted" / "The user aborted a request"，**不包含 "timeout"** → fallback 失败 → 文案为 "请求失败，The user aborted a request" 而不是 "请求失败，请求超时"
- 受众小（≤ Chrome 100 / ≤ Safari 15.4），2026 年绝大部分用户已升级

### A-3 R2-T-5 AbortSignal.any 缺失 userSignal 转发（api.js:120-135）

**INFO / 实现正确**

- R2 用**新建 AbortController merged** 作合并源（line 121），不是修改 immutable signal
- fetch 接收 `merged.signal`，任一 timeoutSignal / userSignal abort 触发 listener 调 merged.abort → fetch 中止
- listener `{ once: true }` 自动移除
- `AbortController.abort` 幂等，多次调用 no-op
- **未发现 listener 泄漏**；微缺陷：listener 持续期 = userSignal 生命周期 ≠ fetch 生命周期，可在另一 signal 触发时 cross-remove，但不影响功能

### A-4 R2-T-6 timeoutMs 边界值（api.js:108）

**INFO**

- `Number.isFinite(timeoutMs) && timeoutMs > 0` 严格过滤：undefined / null / NaN / Infinity / 0 / -1 / 字符串 全 fallback 到 15000
- 字符串 `"60000"` 会被 `Number.isFinite` 拒绝（不做隐式转换），未来 caller 传字符串静默 fallback 到 15s 而无警告
- commands.js:876 restart 60s 对正常路径**绰绰有余**（execv 前 flush response < 200ms）；后端阻塞场景才可能 60s 不够

### A-5 R2-B-4 currentReloadController abort 路径 dead code（dashboard.js:46, 161-170）

**LOW / 防御性 dead branch**

- `loadDashboardData()` 入口 `if (loading) return`（line 156-158）—— loading=true 时直接 return
- **永远不会执行到** `currentReloadController.abort()`（line 163）
- finally 块（line 199-202）正确把 currentReloadController 置 null，**外部主动 abort 路径不可达**
- R2 实质价值在 line 186-188 / 194-196 检查 `localController.signal.aborted` silent return —— 防止 abort 后 catch 报 "加载失败"
- **修复方向**：移除 abort 逻辑保留 aborted-silent 分支；或加 `beforeunload` listener 让 R2-B-4 真正生效

### A-6 R1 保留性核查

| 项目 | 位置 | 状态 |
|---|---|---|
| #status `role="status"` + `aria-live="polite"` | dashboard_content.html:18 | OK |
| 错误时 `role="alert"` 切换 | dashboard.js:68 | OK |
| #loading `role="status"` + `aria-live` | dashboard_content.html:22 | R2-B-1 OK |
| `aria-busy` | dashboard.js:92-93, 102-103 | OK |
| Sponsored kicker | dashboard_content.html:86 | OK |
| reloadButton focus 恢复 | dashboard.js:109-117 | OK |
| timeoutMs 共享 timeout | api.js:103 + 198 | OK |
| `.dashboard-section-desc` 已删 | dashboard.css | OK |
| `.dashboard-metrics` / `.dashboard-panels` 冗余 class 已删 | dashboard_content.html:24, 56 | OK |

R1 + R2 共 20 项全部保留。

---

## Part B：全量再扫新发现

### B-1 dashboard.js loading guard + disabled state 冗余防护（dashboard.js:87, 156-158）

**INFO**

两层防护冗余但正确：disabled 是 UI 防护，`if (loading) return` 是逻辑防护。浏览器 click 不会在 disabled button 上触发，event loop 单线程保证不会重入。

### B-2 dashboard.js `queueMicrotask` focus 恢复抢用户已转移 focus（dashboard.js:111-117）

**LOW**

- setLoadingState(true) 时记录 `reloadButtonWasFocused = true`
- 用户在 loading 期间主动点击页面其他位置（如另一按钮 / 链接）
- finally 时 microtask 仍会把 focus 抢回 reload 按钮，**违反用户意图**
- **修复方向**：microtask 内再检查 `document.activeElement` 是否仍是 body / 之前 button —— 若用户主动 focus 别处则不抢

### B-3 dashboard.js abort 后 setLoadingState(false) 残留 UI 状态（dashboard.js:186-204）

**INFO / 当前不触发**

- abort 路径走 finally → setLoadingState(false) 隐藏 loading + 移除 aria-busy
- 但 `hasLoaded` 仍是 false（首次加载 abort）→ 不会 show stats-grid 和 dashboard-panels → 页面看似空白
- abort 路径在当前实现不可达（依赖 A-5），未来加入 beforeunload abort 会出现

### B-4 dashboard.css `.alert.success` 与 `.alert.info` 视觉不可区分（dashboard.css:63-81）

**LOW / 跨桶**

- 两者都用 `color: var(--accent-teal)`，**视觉完全一致**
- dashboard.js 不触发 success/info（仅 error），dashboard 桶不受影响
- commands.js / users.js 触发 success/info 会出现视觉混淆
- **修复方向**：`.alert.success` 改用绿色变量

### B-5 users.js syncWhitelist / toggleBan 多行 setStatus 主语违规（users.js:802, 811, 845）

**MEDIUM / 跨桶 / CLAUDE.md 文案规范**

- toggleBan: `actionText + "成功，用户 " + user.name`（line 802）→ "封禁成功，用户 张三" —— **含对象名"用户 X" 违反规范**
- syncWhitelist: `用户 ${userName} 白名单同步结果：`（line 845）→ **违反**
- multi-line 第一行包含主语，与 CLAUDE.md "不得包含操作对象名称" 冲突（反例 "删除服务器成功"）
- **修复方向**：改为 "封禁成功" 主标题，详情行保留 N 个服务器结果

### B-6 commands.js setStatus 文案合规（commands.js:606）

**LOW**

- line 606: `"参数保存成功，已立即生效；列表刷新失败..."` —— **"参数保存成功" 含对象名"参数"**，应为"保存成功"
- 其他 commands.js setStatus 调用：line 341 / 343 / 604 / 816 / 877 / 333 / 872 全部合规

### B-7 dashboard.js setStatus("") role 重置（dashboard.js:56-70）

**INFO / 无 bug**

- 空 message 时 className `"alert hidden"` + role="status"，符合 R1 设计意图
- 隐藏的 aria-live region 不会被屏幕阅读器朗读（CSS display:none 失效）

### B-8 dashboard.js renderConnectedBotIds 输入处理（dashboard.js:120-141）

**INFO / 安全 OK**

- Array.isArray 检测 + map+trim+filter empty
- textContent（line 130 / 135）防 XSS
- 极长 ID 被 `.tag-badge` max-width 截断
- 异常数千 ID 一次性 fragment append 可能卡顿，依赖后端契约保护

### B-9 dashboard.js formatNumber null/undefined（dashboard.js:48-54）

**INFO**

- `Number(null) === 0` → 返回 "0"
- `Number(undefined) === NaN` → 返回 "—"
- 后端契约若返回 null，前端显示 "0" 而非 "—" —— 与后端契约相关

### B-10 dashboard.js localController listener 链（dashboard.js:168-170）

**INFO / 无泄漏**

- 每次 reload 创建新 localController，旧 controller 在 finally 后无引用 GC OK
- AbortSignal listener `{ once: true }` 触发后自动移除

### B-11 dashboard_content.html 推广广告链接（line 97）

**INFO / OK**

`target="_blank" rel="noopener noreferrer"` 现代安全实践

### B-12 dashboard.css `.alert-message white-space: pre-line`（line 86）

**INFO / OK**

R1 已为 alert-message 加 `white-space: pre-line` 支持 multi-line setStatus

---

## 结论

### Part A R2 复审：10/10 功能正确，残留 LOW/INFO

| 编号 | R2 修复 | 状态 | 残留问题 |
|---|---|---|---|
| R2-T-4 老浏览器 setTimeout fallback | 功能正确 | A-1 timer 未 clearTimeout（LOW）/ A-2 老浏览器 AbortError 文案缺口（LOW） |
| R2-T-5 userSignal 转发 | **功能完全正确** | A-3 listener 持续期 = userSignal 生命周期（INFO） |
| R2-T-6 timeoutMs | 边界值稳健 | A-4 字符串入参静默 fallback（INFO） |
| R2-B-1 #loading aria-live | OK | — |
| R2-B-2 删 .dashboard-section-desc | OK | — |
| R2-B-3 删冗余 class | OK | — |
| R2-B-4 currentReloadController | **abort 路径 dead code** | A-5 + B-3 联动 |

### Part B 新发现严重度

| 编号 | 严重度 | 触发概率 | 位置 |
|---|---|---|---|
| **B-5** users 多行文案违反"动作+结果"主语规范 | MEDIUM | HIGH | users.js:802/845 |
| B-6 commands "参数保存成功" 主语违规 | LOW | MEDIUM | commands.js:606 |
| B-4 .alert success/info 视觉不可区分 | LOW | HIGH（commands/users 桶） | dashboard.css:63-71 |
| A-1 老浏览器 timer 不 clearTimeout | LOW | HIGH（fetch settle） | api.js:143-150 |
| A-2 老浏览器 AbortError 文案 | LOW | LOW-MEDIUM | api.js:173-177 |
| A-5+B-3 R2-B-4 abort 路径 dead code | LOW | N/A | dashboard.js:160-167 |
| B-2 microtask 抢用户转移 focus | LOW | LOW | dashboard.js:111-117 |
| 其他 | INFO | N/A | — |

### 整体评价

- **R2 实施整体可接受**，无 P0/P1 阻塞
- **R2-B-4 dashboard.js abort dead branch**：R2 注释与实际行为有偏差，但 silent-return 分支才是真正保护
- **跨桶溢出**：R2 修后端 broadcast，前端 users.js 消费 outcome 渲染未审视文案规范（B-5 MEDIUM）
- dashboard 桶本身已达产品质量，R1 + R2 共 20 项保留

### 推荐 R3 处理范围

由用户裁定。若 R3 仅修必要项：
- **B-5** users.js multi-line setStatus 主语精简（MEDIUM）
- **B-6** commands.js:606 "参数保存成功" → "保存成功"（LOW，简单）

其他 LOW / INFO 项可全部排除（accept-as-is）。
