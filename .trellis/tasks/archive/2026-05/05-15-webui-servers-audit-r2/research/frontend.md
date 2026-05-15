# R2 Frontend 桶审计 — servers 页面

- **范围**: `server/webui/templates/servers_content.html` + `server/webui/static/js/servers.js`
- **日期**: 2026-05-15
- **commit baseline**: `1355521` (R1)
- **跨文件验证**: 仅对照 `server/webui/static/js/api.js`、`server/webui/static/js/commands.js`（prior art / 公共契约用途，不审）

---

## Part A: R1 修复复审

### A.1 H-1 token 链改造（前端侧）— PASS（含 2 处局部隐患）

#### A.1.a `visibleTokenIds` Map 操作 — PASS

`servers.js:184-190` 类型升级正确：
- `const visibleTokenIds = new Map();` （之前为 Set）
- `const revealTimers = new Map();`
- `const REVEAL_TIMEOUT_MS = 10_000;`

读 / 写 / 删全部一致：
- `set(serverId, fullToken)`：`servers.js:349`
- `get(server.id)`：`servers.js:410`（渲染分支）
- `has(serverId)`：`servers.js:332`（toggle 判定）
- `delete(serverId)`：`servers.js:326, 532, 764`（hideReveal / loadServers cleanup / 删除 server cleanup）
- 渲染分支判读：`servers.js:410-413`，`const fullToken = visibleTokenIds.get(server.id); const tokenVisible = typeof fullToken === "string" && fullToken.length > 0;` 类型守卫充分。

#### A.1.b 10s setTimeout 清理 — PASS

`servers.js:317-323` `clearRevealTimer`：
```js
const timerId = revealTimers.get(serverId);
if (timerId) {
  clearTimeout(timerId);
  revealTimers.delete(serverId);
}
```

清理调用点全覆盖：
- 用户手动隐藏：`hideRevealedToken` (`servers.js:326-329`)
- 删除 server：`confirmDeleteServer` (`servers.js:763-765`)
- 翻页 / 搜索 reload 时清理已不在当前页的 reveal：`loadServers` (`servers.js:528-535`)
- toggle 重新隐藏路径：`toggleRevealToken` (`servers.js:332-335`)

#### A.1.c reveal endpoint fetch — PASS（含子隐患 F-A-1）

`servers.js:331-357` 正确处理：
- 已 reveal 时点击 → 走 `hideRevealedToken` 同步路径，无网络抖动
- `unwrapData(payload)` 取 `data.token`，空串时 `setStatus("显示失败，未返回 Token", "error")` 兜底（`servers.js:344-348`）
- 失败时 `error.message` 透传到 status bar（`servers.js:353-356`），符合用户操作反馈文案规范（动作 + 结果，原因）

#### A.1.d editor tokenInput 空串 + placeholder — PASS

`servers.js:601-602` (edit 模式):
```js
tokenInput.value = "";
tokenInput.placeholder = "留空表示保留原 Token";
```

`servers.js:610-611` (create 模式):
```js
tokenInput.value = "";
tokenInput.placeholder = "请输入 Token";
```

每次 openModal 时 placeholder / value 都明确重置，无 mode 间残留。

#### A.1.e `buildPayloadFromModal(isEditMode)` 签名扩展 — PASS

`servers.js:659` 签名 `buildPayloadFromModal = (isEditMode = false)`，仅 1 个 caller：
- `servers.js:697-701` `saveServer`：`const isEdit = modalMode === "edit" && typeof editingServerId === "number"; ... payload = buildPayloadFromModal(isEdit);`

校验分支 `servers.js:676-678`：
```js
if (!isEditMode && !token) {
  throw new Error("Token 不能为空");
}
```
create 模式强制非空；edit 模式允许空串（由后端识别）。逻辑正确。

#### A.1.f plugin-config password 字段不回填明文 — PASS

`servers.js:849-862` `renderPluginConfigForm`：
```js
if (item.type === "password") {
  input.value = "";
  input.placeholder = "留空表示保留原值";
} else {
  ...
  input.value = String(value ?? "");
}
```

`closePluginConfigModal` 清空 password value：`servers.js:948-963`，
```js
pluginConfigModalBodyNode.querySelectorAll('input[type="password"]').forEach((inp) => {
  inp.value = "";
});
```

diff 收集 `servers.js:979-983`：password 字段空串 = 不进 diff，仅 `current && current !== originalText` 才推进 diff。正确。

verifyNextBotConnection 同模式 `servers.js:1075-1078`。

#### A.1.g mask token 展示 — PASS

`servers.js:412`: `tokenText.textContent = tokenVisible ? fullToken : (server.token || formatMaskedToken(""));`

后端 `_mask_token` 返回类似 `****abcd` 串，前端直接 `server.token`（已是 mask）展示；若意外为空才走 `formatMaskedToken("")` 兜底（8 个 bullet）。逻辑正确。

`servers.js:272-275` `formatMaskedToken` 自身固定长度区间 8-16，未泄露真实长度。

---

### A.2 H-1 token 链 — 子隐患（Low）

#### F-A-1 toggleRevealToken 重入：双击眼睛在 fetch 未返回时触发两次请求 — Low

**位置**: `servers.js:331-357`

**前置**:
```js
const toggleRevealToken = async (serverId) => {
  if (visibleTokenIds.has(serverId)) {
    hideRevealedToken(serverId);
    return;
  }
  try {
    const payload = await api.apiRequest(`/webui/api/servers/${serverId}/token`, ...);
    ...
    visibleTokenIds.set(serverId, fullToken);
    renderTable();
    const timerId = setTimeout(() => hideRevealedToken(serverId), REVEAL_TIMEOUT_MS);
    revealTimers.set(serverId, timerId);
  } catch (error) { ... }
};
```

**触发场景**: 网络慢时用户连续点 2 次眼睛图标 → `visibleTokenIds.has(serverId)` 第一次和第二次都是 false（首次 fetch 未返回，map 未 set）→ 发出 2 次 `GET /token`（产生 2 条后端 WARN 日志 + 2 条审计行），然后：
1. 两次 fetch 各自 resolve，第二次 `visibleTokenIds.set(serverId, fullToken)` 覆盖第一次（同值，无副作用）
2. 两次 `revealTimers.set(serverId, timerId2)`，timer1 句柄被覆盖丢失，未清理
3. 10s 后 timer1 触发 → `hideRevealedToken` 清掉 `visibleTokenIds` + 清掉 timer2 句柄
4. 又过若干 ms 后 timer2 触发 → 重复 `visibleTokenIds.delete`（no-op）+ 多余 `renderTable`

**影响**: 单次产生 1 次冗余 renderTable + 1 条多余审计日志；明文 token 在 DOM 中存在时间符合预期（按首次触发的 10s 算）。**无安全 / 数据正确性问题**，仅有日志噪音 + 极轻微性能噪音。

**触发概率**: 低（需用户在 fetch 未返回前快速点 2 次）。

**修复前**: 重入未拦截。

**修复后建议**（不在 R2 修复范围，仅记录）: 在 `toggleRevealToken` 开头加一个 `revealInFlight Set` 守卫，或先 `visibleTokenIds.set(serverId, "")` 占位再覆盖。

---

#### F-A-2 modal 关闭时不重置 tokenInput.type / value — Low

**位置**: `servers.js:618-623` `closeModal`

**当前**:
```js
const closeModal = (force = false) => {
  if (modalSaving && !force) {
    return;
  }
  modalNode.classList.add("hidden");
};
```

**触发场景**: 用户打开创建 / 编辑 modal → 输入 token → 点击眼睛图标使 `tokenInput.type = "text"`（明文展示）→ 按 ✕ 关闭。`tokenInput.value` 与 `tokenInput.type` 保留在 DOM 中直到下次 `openModal` 才重置（`servers.js:587-588, 601, 610` 才重置）。

期间若浏览器 devtools 被打开、被分享屏幕或受到截屏类扩展程序，明文 token 与可见状态一并暴露。

**与 commands R2 prior art 对比**: commands.js 的 modal 关闭也未做 sanitize，但 commands 编辑器无 password 字段，对比性弱。`closePluginConfigModal` 在关闭时主动清 password value（`servers.js:954-956`）— **同模块内一致性缺口**。

**影响**: 低概率泄露窗口，无数据正确性问题；与 plugin-config modal 关闭行为不一致。

**触发概率**: 低（需要 token 已被切换成 text 状态 + 外部观察源）。

**修复前**: 关闭后 `tokenInput.type` 仍为 "text"、`tokenInput.value` 仍为明文。

**修复后建议**（不在 R2 修复范围）: `closeModal` 内追加：
```js
tokenInput.value = "";
tokenInput.type = "password";
modalTokenVisible = false;
setTokenButtonIcon(modalTokenToggleButton, false);
```

---

### A.3 R1 self-fix: modalCloseButton / modalCancelButton arrow wrap — PASS

`servers.js:1235-1238`:
```js
// commands R2 B-7 prior art：不能直接绑定 closeModal，否则 MouseEvent 会作为
// force 参数传入（!MouseEvent === false），绕过 modalSaving guard
modalCloseButton.addEventListener("click", () => closeModal());
modalCancelButton.addEventListener("click", () => closeModal());
```

`servers.js:1259-1264, 1269-1274` 同模式：
- `deleteModalCloseButton.addEventListener("click", () => { closeDeleteModal(); });`
- `deleteModalCancelButton.addEventListener("click", () => { closeDeleteModal(); });`
- `pluginConfigModalCloseButton.addEventListener("click", () => { closePluginConfigModal(); });`
- `pluginConfigModalCancelButton.addEventListener("click", () => { closePluginConfigModal(); });`

mask 关闭也走 closure：`servers.js:1249-1257, 1278-1286, 1288-1296`，全部通过 `closeModal()` / `closeDeleteModal()` / `closePluginConfigModal()` 形式调用，无 MouseEvent → force 误传。

`closeModal(force=false)` guard：`servers.js:619-620 `, `closeDeleteModal`: `:635-636`, `closePluginConfigModal`: `:949-950`。所有 saving 中阻止关闭逻辑生效。

---

### A.4 H-2 verify probeStatus enum 映射 — PASS

`servers.js:1128-1144`:
```js
const probeStatus = String(data.probeStatus || "");
const message = String(data.message || "").trim();
const httpStatus = data.httpStatus;
const suffix = Number.isInteger(httpStatus) ? `（HTTP ${httpStatus}）` : "";
const verbResult = probeStatus === "Ok"
  ? "验证成功"
  : probeStatus === "Skipped"
    ? "验证已跳过"
    : "验证失败";
const tone = probeStatus === "Ok"
  ? "success"
  : probeStatus === "Skipped"
    ? "info"
    : "error";
const detail = message ? `，${message}${suffix}` : suffix;
setPluginConfigModalAlert(`${verbResult}${detail}`, tone);
```

3 分支语义清晰：
- `Ok` → 验证成功（success）
- `Skipped` → 验证已跳过（info）
- 其他（含空串）→ 验证失败（error）

`detail` 拼接边界正确：
- 有 message + 有 httpStatus → `验证成功，<msg>（HTTP 200）`
- 有 message 无 httpStatus → `验证成功，<msg>`
- 无 message 有 httpStatus → `验证成功（HTTP 200）`
- 无 message 无 httpStatus → `验证成功`

符合用户操作反馈文案规范（动作 + 结果 [+ 原因]）。

---

### A.5 H-3 进行时文案去对象名（7 处）— PASS

逐处确认：

| # | 位置 | 修复前（推断 R0） | 修复后 | 验证 |
|---|------|--------|------|------|
| 1 | `servers.js:711` `saveServer` | "正在保存服务器..." | `"正在保存…"` | 去对象名 + 全角 `…` 正确 |
| 2 | `servers.js:752` `confirmDeleteServer` modal alert | "正在删除服务器..." | `"正在删除…"` | 同上 |
| 3 | `servers.js:753` `confirmDeleteServer` status bar | "正在删除服务器..." | `"正在删除…"` | 同上 |
| 4 | `servers.js:1014` `savePluginConfig` | "正在保存配置..." | `"正在保存…"` | 同上 |
| 5 | `servers.js:1088` `verifyNextBotConnection` button | "验证中..." | `"正在验证…"` | 去对象名 + 全角 `…` 正确 |
| 6 | `servers.js:1095` `verifyNextBotConnection` modal alert (保存阶段) | "正在保存配置..." | `"正在保存…"` | 同上 |
| 7 | `servers.js:1115` `verifyNextBotConnection` modal alert (验证阶段) | "正在验证..." | `"正在验证…"` | 同上 |
| 8 | `servers.js:1160` `testServerConnectivity` | "正在测试服务器..." | `"正在测试…"` | 同上 |

实际 8 处，PRD 写 "7 处"略低估，但**全数 PASS**。

**额外细节**: `buildResultBadge` (`servers.js:295`) `"测试中…"` 和 testButton (`servers.js:450`) `"测试中…"` 也用了全角 `…`，与新文案风格一致。

---

### A.6 H-4 空态文案统一 — PASS

`servers.js:365`:
```js
emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据" : "暂无服务器配置";
```

HTML 初始态 `servers_content.html:36`:
```html
<div id="empty" class="empty hidden">暂无服务器配置</div>
```

错误路径回退态 `servers.js:557`:
```js
emptyNode.textContent = "暂无服务器配置";
```

3 处文案一致，无尾句号。**PASS**。

---

### A.7 B-1 search debounce + AbortController — PASS

**debounce**: `servers.js:1202-1210`:
```js
searchInput?.addEventListener("input", () => {
  cancelPendingLoad();
  searchDebounceTimer = setTimeout(() => {
    searchDebounceTimer = null;
    currentPage = 1;
    void loadServersWithAbort();
  }, SEARCH_DEBOUNCE_MS);
});
```

`SEARCH_DEBOUNCE_MS = 300` (`servers.js:196`)，符合 commands R1 prior art。

**AbortController 生命周期**: `cancelPendingLoad`（`:565-574`）+ `loadServersWithAbort`（`:576-580`）。每次 reload 先取消上一次 controller + 创建新 controller。

**signal 透传**: `loadServers({ signal })` → `apiRequest({ ..., signal })`（`servers.js:509`），api.js `buildTimeoutSignal(userSignal, timeoutMs)` 合并 user signal + timeout（`api.js:107-170`）。

**abort 后 catch 路径**: `servers.js:543-551` 双重检测：
- `signal && signal.aborted` 优先（PASS — 因 api.js 把 AbortError 包成 ApiRequestError，`error.name === "ApiRequestError"`，不再是 AbortError）
- `error.name === "AbortError"` 兜底（实际**用不到** — 不会有 ApiRequestError 命中此分支；属于防御性冗余，无害）

**触发顺序验证**:
1. T0: 用户键入 → debounce 启动
2. T+50ms: 用户再键入 → `cancelPendingLoad` 清 timer + abort（无 controller，no-op），重启 timer
3. T+350ms: debounce 触发 → fetch 1 启动 (controller A)
4. T+400ms: 用户键入 → `cancelPendingLoad` abort controller A → fetch 1 在 await 处抛错；新 timer 启动
5. fetch 1 catch 分支：`signal.aborted === true`（controller A 已 abort），return false 静默退出
6. T+700ms: 新 debounce → fetch 2 启动 (controller B)

无 race，无错误闪烁。**PASS**。

---

### A.8 B-2 翻页 / per-page abort — PASS

- `perPageSelect` change → `loadServersWithAbort()`（`:1212-1217`）— 取消上一次
- `prevPageButton` click → `loadServersWithAbort({ clearStatus: false })`（`:1219-1225`）— 取消上一次
- `nextPageButton` click → `loadServersWithAbort({ clearStatus: false })`（`:1227-1233`）— 取消上一次
- `reloadButton` click → `loadServersWithAbort()`（`:1192-1196`）
- 初始加载 → `loadServersWithAbort()`（`:1299`）
- `saveServer` 成功后 reload → `loadServersWithAbort({ clearStatus: false })`（`:731`）
- `confirmDeleteServer` 成功后 reload → `loadServersWithAbort({ clearStatus: false })`（`:769`）

全部 reload 调用点统一走 abort 通道，无遗漏。**PASS**。

---

### A.9 B-7 timeoutMs — PASS

- `testServerConnectivity`: `timeoutMs: 30_000`（`servers.js:1169`）
- `verifyNextBotConnection`: `timeoutMs: 30_000`（`servers.js:1124`）

api.js `buildTimeoutSignal(userSignal, timeoutMs)` 正确接收（`api.js:107-108`）。其它 endpoint 未覆盖，沿用默认 15s（合理 — load/save 等不涉及外部 HTTP 链路）。

**PASS**。

---

### A.10 C-2-D / -E / -K / -L / -M 文案修正 — PASS

| 编号 | 位置 | 修复后字符串 | 验证 |
|------|------|----------|------|
| C-2-D | `servers.js:557` | `emptyNode.textContent = "暂无服务器配置";` (替换原 error message) | PASS — emptyNode 不再塞错误，错误改由 statusNode 展示 |
| C-2-E (loadServers) | `servers.js:516` | `throw new Error("加载失败，响应数据格式异常");` | PASS — 由 catch 拼成 `加载失败，响应数据格式异常`（但注意见 B.3） |
| C-2-E (openPluginConfigModal) | `servers.js:934` | `throw new Error("读取失败，响应数据格式异常");` | PASS — 用户视角 |
| C-2-K | `servers.js:916` | `loadingPlaceholder.textContent = "加载中…";` | PASS — 全角 `…` + createElement + textContent 与同模块风格一致 |
| C-2-L (savePluginConfig 无 input) | `servers.js:1000` | `setPluginConfigModalAlert("没有可保存的修改", "warning");` | PASS |
| C-2-L (savePluginConfig 无 diff) | `servers.js:1007` | `setPluginConfigModalAlert("没有可保存的修改", "info");` | PASS |
| C-2-M | `servers.js:898` | `emptyNote.textContent = "当前没有可编辑的配置";` | PASS — 去业务术语 |

---

## Part B: 全量再扫新发现

### B.1 F-B-1 saveServer 校验失败拼装 "更新失败"/"创建失败" 重复"失败" — 排除（误判）

**位置**: `servers.js:702-705`

**当前**:
```js
try {
  payload = buildPayloadFromModal(isEdit);
} catch (error) {
  const message = error instanceof Error ? error.message : "表单校验失败";
  setModalAlert(`${isEdit ? "更新失败" : "创建失败"}，${message}`, "error");
  return;
}
```

**触发场景**: 用户在编辑表单中清空 token（但 create 模式）/ 把游戏端口填空 / 服务器名留空。`buildPayloadFromModal` 抛 `Error("Token 不能为空")` / `Error("服务器名称不能为空")` 等。

**修复前 → 修复后行为**:
- payload error 文案: `Token 不能为空`
- modalAlert 文案: `创建失败，Token 不能为空`

符合规范（动作 + 结果，原因）。但与 catch 分支 `servers.js:735-737` 对比：
```js
const message = error instanceof Error ? error.message : isEdit ? "更新失败" : "创建失败";
setModalAlert(message, "error");
```

后端 API 错误时 message 已是 `api.js:buildActionFailureMessage` 拼好的 `更新失败，<reason>`，直接 `setModalAlert(message)`。而表单校验路径在前端手动拼了一遍 "更新失败/创建失败"。**两条路径产物一致，无重复 "失败"**（验证：表单校验拼 `更新失败，Token 不能为空`；API 失败拼 `更新失败，<API reason>`）。**误判，无 finding。**

---

### B.2 F-B-2 hideRevealedToken 时 setStatus 未消除 — Low

**位置**: `servers.js:325-329`

**当前**:
```js
const hideRevealedToken = (serverId) => {
  visibleTokenIds.delete(serverId);
  clearRevealTimer(serverId);
  renderTable();
};
```

**触发场景**: 用户点眼睛 → reveal 失败 → status bar 显示 `显示失败，<reason>`（`servers.js:346, 354`）。10s 后用户重试点击眼睛 → 这次成功 reveal → 但 status bar 仍显示上次失败文案，不一致。

更严重的是：用户成功 reveal 后又点眼睛隐藏 — `hideRevealedToken` 调用，但若上次 setStatus 还残留 `显示失败` / 其它错误，hideRevealedToken 不主动清除。

**修复前**: hideRevealedToken 不调用 `setStatus("")`，残留旧 status 文案。

**修复后建议**: 在 `toggleRevealToken` 成功分支前先 `setStatus("")` 清除。

**影响**: 极低 — 仅在罕见连续失败 → 成功序列下出现陈旧文案。无数据正确性问题。

**触发概率**: 低。

**严重度**: Low。

---

### B.3 F-B-3 loadServers 响应格式异常时错误拼装 — Medium

**位置**: `servers.js:514-517` + `servers.js:552-553`

**当前**:
```js
const servers = api.unwrapData(payload);
const meta = api.unwrapMeta(payload);
if (!Array.isArray(servers)) {
  throw new Error("加载失败，响应数据格式异常");
}
```

catch 分支：
```js
const message = error instanceof Error ? error.message : "加载失败";
setStatus(message, "error");
```

**触发场景**: 当 `servers` 不是数组时，**手动抛 Error**，message 已预拼 `加载失败，响应数据格式异常`。catch 直接透传到 statusNode。

但 `api.unwrapData` 在 payload 缺少 `data` 字段时**也**抛 `Error("返回数据格式错误")`（`api.js:89`），不在 ApiRequestError 链中。catch 分支会拿到 `error.message = "返回数据格式错误"`，**没有 "加载失败" 前缀**，最终 statusNode 显示 `返回数据格式错误`。

**修复前 → 修复后字符串对比**:
- 当前 payload 缺 data：status bar 显示 `返回数据格式错误`（违反"动作 + 结果，原因"规范）
- 期望：`加载失败，返回数据格式错误`

类似的：`openPluginConfigModal` `:931-935` 在 `api.unwrapData(payload)` 抛错时也只能拿到 `返回数据格式错误`，但 catch `:939-940` 同样 `error.message` 直传，不带"读取失败"前缀。

**影响**: 用户看到的失败文案不一致 — 大多数 API 失败遵循"动作失败，原因"规范（由 api.js `buildActionFailureMessage` 处理），但 unwrapData 失败时违规。属于跨模块契约缺口，但同时也是 servers.js catch 内可补一层"动作 + " 前缀。

**与全局规范对比**: 用户操作反馈文案规范要求"失败：动作 + 结果，原因"。

**触发概率**: 低（仅在后端响应缺 `data` 字段时触发；目前后端契约稳定）。

**严重度**: Medium（违反全局文案规范，但触发场景罕见）。

**修复后建议** (不在 R2 修复范围):
- 方案 A: `loadServers` catch 内 `setStatus(error.message.startsWith("加载") ? error.message : "加载失败，" + error.message, "error")`
- 方案 B: api.js `unwrapData` 直接抛 ApiRequestError 包 action 前缀（**跨模块，scope-out backlog**）

---

### B.4 F-B-4 testServerConnectivity 失败不写 status，仅 in-row badge — Low（设计意图）

**位置**: `servers.js:1181-1189`

**当前**:
```js
} catch (error) {
  const message = error instanceof Error ? error.message : "测试失败";
  testResultMap.set(serverId, {
    status: "failed",
    reason: message,
  });
  setStatus(message, "error");
  renderTable();
}
```

catch 路径既写 status bar 又写 badge，但 success 路径（`:1174-1180`）也同样双写。设计一致。

但 `buildResultBadge` 的 title hover 提示是 `result.reason`（`servers.js:302-304, 310-313`），失败时 reason 来自 `error.message`（已含"测试失败，<reason>"前缀），hover 时显示 `测试失败，连接超时` — 在表格 cell 局部上下文里冗余"测试"。

**修复前**: title 为 `测试失败，连接超时`
**修复后建议**: title 仅显示纯 reason（`连接超时`），statusNode 保留全文。

**严重度**: Low（UX 微瑕）。**触发概率**: 中（任何测试失败时）。

---

### B.5 F-B-5 modalSaving 中 close/cancel 按钮未 disabled — Low（UX 不一致）

**位置**: `servers.js:692-742` (saveServer) + `servers.js:1237-1238` (close/cancel handler)

**当前**:
- `saveServer` 设 `modalSaving = true; modalSaveButton.disabled = true;`（`:708-709`）
- close / cancel / 关闭 × 按钮 **未 disabled**，仅 `closeModal()` 内部 `if (modalSaving && !force) return;` 阻止关闭

**与 commands.js prior art 对比** (`commands.js:256-261`):
```js
const setModalSavingState = (saving) => {
  modalSaving = Boolean(saving);
  modalSaveButton.disabled = modalSaving;
  modalCancelButton.disabled = modalSaving;
  modalCloseButton.disabled = modalSaving;
};
```

commands 显式 disable 全部按钮，视觉上提示"操作进行中"。servers.js 仅 disable save，close/cancel 按钮仍是高亮态但点击无响应（被 `closeModal` guard 拦截）— **视觉欺骗**。

**影响**: 用户点击 cancel/close 但无反应，可能误判按钮失灵。

**触发概率**: 中（任何保存 / 删除 / 配置保存中按 cancel）。

**严重度**: Low（UX 微瑕，无数据正确性问题）。

同模式问题也存在于 `closeDeleteModal` / `closePluginConfigModal` — `deleteSaving` / `pluginConfigSaving` 中也只 disable 主操作按钮。

**修复后建议** (不在 R2 修复范围): 抽象 `setModalSavingState` 同时 disable 三类按钮。

---

### B.6 F-B-6 saveServer 重置 currentPage = 1 但不重置 search query — Info

**位置**: `servers.js:728-731`

**当前**:
```js
closeModal(true);
currentPage = 1;
const reloaded = await loadServersWithAbort({ clearStatus: false });
```

新建 / 编辑成功后强制跳第 1 页。若用户当前在筛选状态（searchInput 有值），新创建的 server 名字可能不匹配筛选词 → 用户跳回第 1 页但**仍受 search filter 限制**，可能仍不可见，体验割裂。

**触发场景**: 用户搜索 "alpha"，结果空 → 点新建创建 "beta" → 成功后回第 1 页（搜索仍是 "alpha"，无法看到新创建的 "beta"）→ 仅 statusNode 显示"创建成功"。

**严重度**: Info（设计取舍，非缺陷；R1 未列入）。**触发概率**: 低。

---

### B.7 F-B-7 closeDeleteModal 第二个 if 永远 true — Low（代码 noise）

**位置**: `servers.js:634-642`

**当前**:
```js
const closeDeleteModal = (force = false) => {
  if (deleteSaving && !force) {
    return;
  }
  deleteModalNode.classList.add("hidden");
  if (force || !deleteSaving) {
    deletingServer = null;
  }
};
```

第二个 `if` 逻辑：当 `force` 为 true 或 `!deleteSaving` 时清 `deletingServer`。

考虑实际触发：
- 调用 1: `closeDeleteModal()` 即 `force=false`。第一个 if 检查 `deleteSaving && !false` = `deleteSaving`；如果 `deleteSaving=true` 直接 return。否则继续。到第二个 if `force || !deleteSaving` = `false || true` = `true`，清 `deletingServer`。
- 调用 2: `closeDeleteModal(true)` 即 `force=true`。第一个 if 跳过。第二个 if `true || (...)` = `true`，清 `deletingServer`。

**两种调用下，凡是执行到 hide 那行，deletingServer 一定被清**。第二个 if 永远为 true → **冗余但无害**。

**严重度**: Low（代码 noise，无功能影响）。

---

### B.8 F-B-8 reveal token 错误路径不调用 `clearRevealTimer` — Info

**位置**: `servers.js:336-356`

**当前**:
```js
try {
  const payload = await api.apiRequest(...);
  const data = api.unwrapData(payload);
  const fullToken = String(data?.token || "");
  if (!fullToken) {
    setStatus("显示失败，未返回 Token", "error");
    return;
  }
  visibleTokenIds.set(serverId, fullToken);
  renderTable();
  const timerId = setTimeout(() => hideRevealedToken(serverId), REVEAL_TIMEOUT_MS);
  revealTimers.set(serverId, timerId);
} catch (error) { ... }
```

错误路径直接 return，未操作 map（也无需）。**正确**。本节作为正向验证 — 没有发现需要清理的残留态。

---

### B.9 F-B-9 servers.js 无 Escape 键 / 焦点 trap / 焦点恢复 — scope-out backlog（commands R2 已识别但属跨模块基线缺）

**位置**: 全文件无 keydown / Escape / focus trap

**与 commands.js prior art 对比**:
- `commands.js:215-254` `closeModalAndRestoreFocus` — 卸载 trap、恢复焦点
- `commands.js` 有 `modalTrapHandlers` + `modalPreviousFocus` 系统

servers.js 仅 `nameInput.focus()`（`:615`）首次 focus，无 trap、无 Escape 关闭、无 close 后焦点恢复。

**影响**: 可达性下降（键盘用户、屏幕阅读器用户无法 Esc 关闭 modal、焦点关闭后丢失到 body）。

**严重度**: Medium（a11y），但属**跨模块基线问题** — commands.js 有此能力，servers.js 没有，可在共享 utility 层补齐。

**Scope-out backlog**: 建议下一轮跨模块（webui.js / 共享 modal lib）专项处理，而非在 servers R2 闭环内单点修复。

---

### B.10 F-B-10 SVG 图标硬编码内联富文本赋值 — Info（无 XSS 风险）

**位置**: `servers.js:96-109` `SHOW_ICON_SVG` / `HIDE_ICON_SVG`，`setTokenButtonIcon` 在 `:277-281` 用富文本赋值。

`SHOW_ICON_SVG` / `HIDE_ICON_SVG` 都是**静态字符串常量，不含用户输入**，无注入风险。

**严重度**: 无 finding，仅记录为 prior art（DOM 静态扫描可能误报）。

---

### B.11 F-B-11 currentPerPage 与 `Number(perPageSelect.value || 10)` 容错 — PASS

`servers.js:181` `let currentPerPage = Number(perPageSelect.value || 10);`

如果 HTML option `value="10"` 默认 selected (`servers_content.html:62`)，`perPageSelect.value === "10"`，`Number("10") = 10`，OK。

`servers.js:1214` `currentPerPage = Number(perPageSelect.value || 10);` — 用户切换时再 Number 转换。OK。

`servers.js:255` `perPageSelect.value = String(perPage);` — 后端返回 meta.per_page 后回填 select。OK。

无 finding。

---

### B.12 F-B-12 reloadButton / addServerButton 用 optional chaining 但已 required 检查 — Info

**位置**: `servers.js:1192, 1198`

```js
reloadButton?.addEventListener("click", () => { ... });
addServerButton?.addEventListener("click", () => { ... });
searchInput?.addEventListener("input", () => { ... });
```

但 `reloadButton` / `addServerButton` 并未列入 `requiredNodesReady` 校验（`servers.js:49-89` 仅校验 statusNode、modalNode 等）。**实际不存在时 optional chaining 静默跳过**，没有 fail-fast。

对比：`perPageSelect.addEventListener` / `prevPageButton.addEventListener` / `nextPageButton.addEventListener`（`:1212, 1219, 1227`）不带 `?.`，是因为它们在 `requiredNodesReady` 内。

**触发场景**: 模板被改、`reload-btn` / `add-server-btn` / `server-search` 被移除 → 页面静默丢失功能，无错误提示。

**严重度**: Info（防御性 vs 显式校验的取舍）。**触发概率**: 极低。

---

### B.13 F-B-13 pluginConfigVerifying 状态与 button 引用解耦 — Low

**位置**: `servers.js:880, 962, 1054-1153`

**前置**:
- `pluginConfigVerifyButton = verifyButton;` 在 `renderPluginConfigForm` 设置（`:880`）
- `closePluginConfigModal` 把 `pluginConfigVerifyButton = null;` + `pluginConfigVerifying = false;`（`:961-962`）

但 `verifyNextBotConnection` 函数自己接受 `button` 参数（`:1054, 1085-1086, 1150-1151`），保留对**原 button 引用**的 closure，且在 `finally` 中 `button.disabled = false; button.textContent = originalLabel;`。

**关键边界**: 用户在 verify 进行中点击 cancel（`closePluginConfigModal()` force=false）。第一个 if 检查 `pluginConfigSaving && !force` = `false && true` = `false`（**注意：是 `pluginConfigSaving`，不是 `pluginConfigVerifying`**），所以 close 路径**不被阻止**！modal 被 hide，但 verifyNextBotConnection 仍在进行中。

随后 verify resolve → `finally`:
- `pluginConfigVerifying = false;` — OK
- `button.disabled = false; button.textContent = originalLabel;` — 操作 hidden modal 的 DOM 节点，无害
- `pluginConfigModalSaveButton.disabled = false;` — 操作 hidden modal 的 save button，无害但 next open 时若 saveButton 仍 disabled 因 renderPluginConfigForm 内未显式重置

实际 `openPluginConfigModal` `:910` 显式 `pluginConfigModalSaveButton.disabled = true;`（loading 时禁用），fetch 完成后 `:938` 重置为 false。所以**下次 open 行为正确**。

但还有一个微问题：verify 仍在 in-flight 时关闭 modal → verify success/failure 仍会调 `setPluginConfigModalAlert(...)`（`:1144, 1147`），尝试设置 hidden modal 的 alert。无害（modal 已 hidden，下次 open 时 `setPluginConfigModalAlert("")` 重置 `:918`）。

**整体结论**: 边界场景不会崩溃，但 `closePluginConfigModal` 应该和 saving 一样 guard `pluginConfigVerifying`，避免悬空异步操作。

**严重度**: Low（无功能伤害，但代码契约模糊）。**触发概率**: 低。

---

## 结论

### R1 修复复审

| 编号 | 项目 | 判定 |
|------|------|------|
| H-1 | token 链改造（前端） | **PASS**（含 2 处 Low 子隐患 F-A-1 / F-A-2） |
| R1 self-fix | modalCloseButton arrow wrap | **PASS** |
| H-2 | verify probeStatus 映射 | **PASS** |
| H-3 | 进行时文案 7→8 处 | **PASS**（PRD 写 7 处，实际 8 处覆盖） |
| H-4 | 空态文案统一 | **PASS** |
| B-1 | search debounce + AbortController | **PASS** |
| B-2 | 翻页 / per-page abort | **PASS** |
| B-7 | test / verify timeoutMs 30s | **PASS** |
| C-2-D | emptyNode 不塞错误 | **PASS** |
| C-2-E | "响应数据格式异常"（2 处） | **PASS** |
| C-2-K | "加载中…" | **PASS** |
| C-2-L | "没有可保存的修改"（2 处） | **PASS** |
| C-2-M | "当前没有可编辑的配置" | **PASS** |

**R1 前端修复全部 PASS，无 NEW-ISSUE 否决。**

### 全量再扫新发现汇总（仅前端范围）

| 编号 | 名称 | 严重度 | 触发概率 | 是否服务器页内修 |
|------|------|--------|----------|------------------|
| F-A-1 | toggleRevealToken 双击重入产生 2 次 GET /token | Low | 低 | 可（小补丁） |
| F-A-2 | closeModal 不清 tokenInput.type/value | Low | 低 | 可（小补丁） |
| F-B-2 | hideRevealedToken 不消除旧 setStatus 文案 | Low | 低 | 可（小补丁） |
| F-B-3 | api.unwrapData 抛错时文案缺"加载失败，"前缀 | Medium | 低 | 可（servers.js catch 补前缀） |
| F-B-4 | testServerConnectivity badge title 带"测试"冗余 | Low | 中 | 可（小补丁） |
| F-B-5 | modalSaving 中 close/cancel 按钮未 disabled（UX） | Low | 中 | 可（抽 setModalSavingState） |
| F-B-7 | closeDeleteModal 第二个 if 永远 true（代码 noise） | Low | — | 可（清理） |
| F-B-12 | reload/add/search button optional chaining 但无 required 校验 | Info | 极低 | 可（取舍） |
| F-B-13 | pluginConfigVerifying 中关闭 modal 不被 guard（悬空异步） | Low | 低 | 可（补 guard） |

**Scope-out backlog（跨模块，不在 R2 修复）**:

| 编号 | 名称 | 备注 |
|------|------|------|
| F-B-9 | 无 Escape / 焦点 trap / 焦点恢复（a11y） | Medium，跨模块基线缺；建议下一轮抽 webui.js 公共 modal helper |
| F-B-3（变体） | api.js `unwrapData` 抛 Error 不带 action 前缀 | 同上 |

### 收敛判定

- **0 Critical**
- **0 High**
- **1 Medium**（F-B-3 — 但触发场景罕见，可降级为 Low 或 scope-out）
- **8 Low / Info**

按 PRD 收敛标准（0 Critical / 0 High / 0 Medium → 闭环），**F-B-3 是唯一阻碍因素**，但建议视为前端 catch 内补 1 行前缀的小修，或承认与 api.js 跨模块契约一致性问题归入 backlog。

**严格 scope 守住**：未审 api.js / webui.js / commands.js 实现细节，仅引用作为 prior art / 共享契约说明。所有 finding 行号精确落在 `servers_content.html` / `servers.js`。
