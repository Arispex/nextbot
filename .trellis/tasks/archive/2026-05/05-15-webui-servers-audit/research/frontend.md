# Frontend / 文案 桶审计 — WebUI 服务器管理页面

- **Scope**: `server/webui/templates/servers_content.html`（162 行）、`server/webui/static/js/servers.js`（1152 行）、`server/webui/static/css/servers.css`（529 行）
- **Date**: 2026-05-15
- **Prior art 已落地**：dashboard R1+R2、commands R1+R2、401vs302、api.js timeoutMs / AbortSignal 合并 / R2-T-* 全套
- **审计基线**：CLAUDE.md「用户操作反馈文案规范」+「中英文空格规范」+「API 错误透传规范」
- **跨模块 finding**：仅 backlog 标注（不修）

---

## A. 前端安全（含 token 泄漏专项）

### A-1 [严重度：低 | 概率：低] HTML 字符串直写仅用于固定 SVG 字面量（servers.js:96-109, 267, 717, 809, 832）

**事实**：
- `SHOW_ICON_SVG` / `HIDE_ICON_SVG` 是模块内**静态字符串字面量**（servers.js:96-109）。
- `setTokenButtonIcon` 给 button 写入 HTML 字符串（servers.js:267），RHS 是上述常量。
- `renderPluginConfigForm` 入口处把 plugin-config 模态 body 清空（servers.js:717）—— 清空操作。
- `openPluginConfigModal` 入口处写入字符串字面量 `<p class="confirm-modal-text">加载中...</p>`（servers.js:809）。
- 异常分支再次清空 plugin-config 模态 body（servers.js:832）。

**结论**：全部 HTML 字符串写入位点的 RHS 均为**模块内常量或空字符串**，**无任何用户可控数据进入 HTML 字符串赋值**。

**DOM 注入验证**：
- server.name / ip / token / game_port / restapi_port 渲染全部走 `textContent`（servers.js:326, 332, 337, 341, 345, 354, 355）—— 安全。
- plugin-config schema 全部 label / hint / 段标题为**前端硬编码字符串**（servers.js:111-162），渲染走 `textContent`（servers.js:733, 743, 781, 795）—— 安全。
- plugin-config 表单回填的 `input.value`（servers.js:759）走 DOM property 赋值而非 HTML，**不解析 HTML**，安全。
- delete modal 用 `${server.name}` 模板字符串 + `textContent`（servers.js:541）—— 安全。
- pagination info（servers.js:256）数字 + 中文，`textContent`，安全。

**判定**：无 DOM 注入面。可不修复。如需 defense-in-depth，可改为 `<template>` + `cloneNode`，但成本 > 收益。

### A-2 [严重度：低 | 概率：低] 通过 location 参数注入 DOM

**事实**：servers.js 全文 grep `location.search` / `location.hash` / `URLSearchParams` / `window.location`：**仅一处使用**——在 api.js 的 401 跳转构造 next 参数（api.js:227）。servers.js 自身**不读取** URL 参数。

**判定**：无 URL-driven DOM 注入入口。

### A-3 [严重度：高 | 概率：高] TShock token 在 DOM 上明文持久化

**事实**：
- `tokenText.textContent = tokenVisible ? server.token : formatMaskedToken(server.token);`（servers.js:354）
- `tokenText.title = tokenVisible ? server.token : "已隐藏";`（servers.js:355）
- 编辑表单回填 `tokenInput.value = server.token;`（servers.js:514）
- 内存中 `serverStates` 数组持有全部服务器 token（servers.js:472）

**触发场景**：
1. 用户在表格点「显示」眼睛图标 → token 明文写入 `<span>` 的 textContent **和** `title` attribute。即使重新关闭，`visibleTokenIds` set 控制重渲染时是否再次明文，但**只要切换过一次**，明文已通过 DOM 暴露给屏幕共享 / 录屏 / 任何浏览器扩展 / DevTools。
2. 同事路过看屏幕一眼即可窥探。
3. 浏览器扩展（如截图、翻译、广告屏蔽）扫 DOM 即可批量提取。
4. 客户端 JS 异常上报（若未来接 sentry / 阿里云 ARMS 等）默认会序列化 DOM 周围上下文，token 大概率被采集。
5. 即使从未点「显示」，`server.token` 仍保留在 `serverStates` 闭包内，DevTools heap snapshot 可见。

**关联现状**：CSS 已限宽 `.token-text { max-width: 180px; ... text-overflow: ellipsis; }`（servers.css:161-165）但 `title` attribute 含完整 token——hover 会显示原生 tooltip，等于把脱敏功能旁路了。

**修复方向**（不实施，仅记录）：
- 表格不渲染完整 token，仅展示固定数量 mask 字符 + 「显示完整 token」按钮，点击后**临时**通过专门的 API（如 `GET /webui/api/servers/{id}/reveal-token`）获取并显示 N 秒后自动隐藏，避免常驻 DOM。
- `tokenText.title` 永远不写 token；hover 时显示「点击眼睛图标显示」即可。
- 编辑表单不回填 token：保持 placeholder「留空表示不修改」，PATCH 时仅传非空字段。当前 PUT 流程（servers.js:623, 632）强制 token 必填且整对象覆盖（buildPayloadFromModal 必要校验 token），无法做到「不修改」语义。

**判定**：**高危**，独立 finding，建议下个迭代专项处理。

### A-4 [严重度：中 | 概率：高] plugin-config password 字段（包含 NextBot Token）回填 input.value

**事实**：
- schema 含 `{ path: "nextbot.token", label: "NextBot Token", type: "password" }`（servers.js:116）。
- 渲染时 `input.value = String(value ?? "");`（servers.js:759）——即使 type=password，**input.value DOM property 仍含明文**。
- DevTools 选中元素 → console `$0.value` 立刻看到明文。
- 同 A-3 风险：DOM 持有用户 token。
- 关闭模态框时 `closePluginConfigModal` 重置 `pluginConfigOriginal = {}`（servers.js:846）但**没有清空已渲染 input 的 value**——DOM 节点虽 hidden，input 仍残留 token，直到 body 在下次 `openPluginConfigModal` 被清空（servers.js:809）。

**关联**：CLAUDE.md 第 5 条要求保留 API 原始字段——但本项是「呈现到 DOM」的安全维度，不是字段命名维度。

**修复方向**（不实施）：
- 关闭模态时主动 `input.value = ""`，并对 password 类字段：
  - 加载时**不回填**明文，仅显示「●●●●●●」占位 + 「修改」按钮；用户点修改后才暴露 input。
  - 或参考 A-3，引入专门的 reveal 接口。

**判定**：**中危**，与 A-3 同步治理。

### A-5 [严重度：低 | 概率：低] token 进 console / 调试输出

**事实**：servers.js 全文搜索 `console.log` / `console.error` / `console.warn` / `console.debug` / `console.info`：**0 处**。

**判定**：无调试残留。安全。

### A-6 [严重度：低 | 概率：低] localStorage / sessionStorage / cookie

**事实**：servers.js 全文搜索 `localStorage` / `sessionStorage` / `document.cookie`：**0 处**。

**判定**：本页不存敏感数据到本地存储。安全。

### A-7 [严重度：低 | 概率：极低] CSRF

**事实**：所有写操作（servers.js）：
- POST `/webui/api/servers`（servers.js:623, 634）
- PUT `/webui/api/servers/{id}`（servers.js:623, 634）
- DELETE `/webui/api/servers/{id}`（servers.js:663-668）
- PATCH `/webui/api/servers/{id}/plugin-config`（servers.js:899-907 + 968-980 verify 内嵌保存）
- POST `/webui/api/servers/{id}/plugin-config/verify-nextbot`（servers.js:988-994）
- POST `/webui/api/servers/{id}/test`（servers.js:1027-1032）

均通过相对路径同源调用 + cookie session（隐式由浏览器附带）。**未见 CSRF token header / body 字段**。

**判定**：是否安全取决于后端 SameSite cookie 配置和登录 session 实现。已在 backend 桶审计范围内。前端层面**不报为 finding**——webui 全模块统一假设，不在 servers 桶单独治理。

### A-8 [严重度：低 | 概率：极低] 第三方资源 / CDN

**事实**：HTML 中无 `<script src="https://...">` / `<link href="https://...">`，全部内联 SVG + 同源静态资源。

**判定**：无第三方依赖泄漏面。安全。

---

## B. 性能

### B-1 [严重度：中 | 概率：高] 搜索框无 debounce，每次按键打满 API

**事实**：
```js
searchInput?.addEventListener("input", () => {
  currentPage = 1;
  void loadServers();
});
```
（servers.js:1063-1066）

**触发**：用户输入「主服一号」（5 字符 + 拼音过程中可能 30+ 个 input 事件）→ 触发 30+ 个 `GET /webui/api/servers?q=...`。
- 服务端 SQLite 全表 LIKE 扫描。
- 旧请求未 AbortController 取消，结果竞态：最后一次返回的可能是更早请求的结果。
- 网络抖动场景下用户看到的列表会瞬时跳变。

**关联 prior art**：commands R1+R2 已落地 debounce + AbortController（参考 `nextbot/plugins/.../commands.js`），servers 漏吃。

**修复方向**：
- 引入 200-300ms debounce。
- 每次发起前 abort 上一个 in-flight 请求；`api.apiRequest(..., { signal: ctrl.signal })` 透传。
- abort 错误（`AbortError`）静默吞掉，不进入 setStatus。

**判定**：**中危**，建议本轮修。

### B-2 [严重度：中 | 概率：中] 翻页 / per-page 切换同样无 debounce / abort

**事实**：
- `perPageSelect.addEventListener("change", ...)` → `loadServers()`（servers.js:1068-1072）
- `prevPageButton` / `nextPageButton`（servers.js:1074-1088）

**触发**：用户快速点击 prev → next → prev 三次，发起 3 个并发请求；最后一个完成的可能是最早发出的，列表与当前页码不一致。

**修复方向**：与 B-1 共用同一 AbortController + last-write-wins 守卫（如 requestSeq 单调递增，旧响应丢弃）。

### B-3 [严重度：低 | 概率：低] testServerConnectivity 无并发上限 / 无取消

**事实**：servers.js:1021 `testServerConnectivity(serverId)`：
- 点击「测试」按钮 → POST `/webui/api/servers/{id}/test`，默认 timeoutMs = `REQUEST_TIMEOUT_MS = 15000`（api.js:103）。
- testResultMap 记录 loading 状态，避免对**同一**服务器并发；但**不限制全局并发**：用户可对 50 台服务器逐个点测试，浏览器同源并发 6 个请求其余排队。
- 用户切到下一页 → loadServers 不取消已发起的 test 请求 → 后到达的 test 响应仍会 setStatus（servers.js:1041）覆盖当前页面状态，造成"上一页的测试结果污染下一页页面"。

**修复方向**：
- testResultMap 在 loadServers 成功后清理不在当前页的 server（已部分实现 servers.js:479-483，但**不取消**未完成的 fetch）。
- 引入 testAbortControllers Map<serverId, controller>；loadServers 时对**已不在当前页且仍 loading**的 controller `.abort()`。
- 或简单粗暴：translate 全局 status bar 改为 row-level loading 指示（result-badge 已有 "测试中…"），不再覆盖 setStatus。

**判定**：**低危**，可纳入 backlog。

### B-4 [严重度：低 | 概率：中] renderTable 全量重绘

**事实**：`renderTable` 每次执行清空 tbody（servers.js:306）然后整张表重建。触发时机：
- token 显隐切换（servers.js:367）—— **整表重绘**只为换一个 cell。
- testServerConnectivity 启动 / 完成（servers.js:1023, 1042, 1050）—— 整表重绘。
- 翻页 / 加载 / 搜索（合理）。

每页 100 条时，单次重绘约 100 行 × 8 cell × 多按钮 = 几千 DOM 节点 + 大量 event listener 重新绑定。低端机 / 老设备会有 100-200ms 卡顿。

**修复方向**：
- 细粒度更新：token 显隐只重渲对应 row 的 token cell。
- result-badge 状态变化只替换 badge 节点。
- 用 row dataset.serverId（已存在 servers.js:322）做 querySelector 定位。

**判定**：**低危**，可 backlog。

### B-5 [严重度：低 | 概率：高] 重复闭包变量 / 无 memo 列过滤

**事实**：本页不在前端做 filter/sort，全部后端 `?q=` 查询；renderTable 直接 iterate `serverStates`（servers.js:320）。

**判定**：无前端过滤性能问题。✓

### B-6 [严重度：低 | 概率：低] 资源加载

**事实**：HTML 由后端 server-side 渲染嵌入 app_shell，未直接看到 `<script>` 标签（在 app_shell_base.html 中，**out of scope**）。servers.js 文件大小约 36KB 未压缩。

**判定**：不在本桶治理范围。✓

### B-7 [严重度：低 | 概率：中] 缺少 timeoutMs override

**事实**：所有 `api.apiRequest(...)` 调用均未传 `timeoutMs`，默认 15s（api.js:103）。
- POST `/test`（servers.js:1027）—— 测试 TShock 连通性 + 走 NextBot 服务，链路上含外部 HTTP，**15s 偏紧**。
- POST `/plugin-config/verify-nextbot`（servers.js:988）—— 同上。

若外部 NextBot 服务首包延迟 + DNS 解析 = 实际可能 8-12s，遇到弱网会触发超时但其实后端仍在执行。

**修复方向**：测试 / 验证连通性接口传 `timeoutMs: 30_000` 或 `45_000`。

**判定**：**低危**，可纳入 backlog（dashboard R2-T-6 已为 restart 路径设过类似 60s 兜底）。

---

## C. 文案审计（修复前 → 修复后对比表）

按 CLAUDE.md 强制约束：
- 成功：`动作 + 结果`，**不带操作对象**
- 失败：`动作 + 结果，原因`，**原样透传原因**
- 动词通用：保存 / 删除 / 创建 / 更新 / 提交 / 上传 / 测试

### C-1 静态文案（servers_content.html）

| 位置 | 当前 | 修复后 | 严重度 | 说明 |
|------|------|--------|--------|------|
| L11 placeholder | `搜索服务器名称 / 地址 / 端口` | （保留） | — | 中英文规范 ✓，符合表单 hint 习惯 |
| L20 button | `刷新` | （保留） | — | 通用动词 ✓ |
| L27 button | `新建` | （保留） | — | 通用动词 ✓ |
| L35 empty | `正在加载服务器…` | `正在加载…` | 低 | 反例：含操作对象名「服务器」。规范是「动词 + 结果」，loading 文案应去对象。 |
| L36 empty | `暂无服务器` | （保留，**例外**） | — | **例外**：列表空态不是「操作反馈」，是名词性状态描述，可保留对象。但若与下方动态文案 L310「暂无服务器配置。」保持一致，建议统一为 `暂无服务器配置`。 |
| L39 aria-label | `服务器管理表格` | （保留） | — | a11y 描述性 label，可保留 ✓ |
| L78 modal title | `创建服务器` | （保留） | — | Modal 标题是页面导航 / 上下文锚点，需明确告知用户当前在操作什么对象——属于反馈规范例外。 |
| L79 aria-label | `关闭` | （保留） | — | ✓ |
| L87 form-label | `服务器名称` | （保留） | — | 表单 label 是字段名，非反馈 ✓ |
| L88 placeholder | `主服` | （保留） | — | ✓ |
| L91 form-label | `地址` | （保留） | — | ✓ |
| L92 placeholder | `127.0.0.1 / server.example.com` | （保留） | — | ✓ |
| L95 form-label | `游戏端口` | （保留） | — | ✓ |
| L99 form-label | `REST API 端口` | （保留） | — | 中英文混排已有空格 ✓ |
| L105 form-label | `Token` | （保留） | — | ✓ |
| L107 placeholder | `请输入 Token` | （保留） | — | 中英文空格 ✓ |
| L108 aria-label / title | `显示 Token` | （保留） | — | 中英文空格 ✓ |
| L118 button | `取消` | （保留） | — | ✓ |
| L119 button | `保存` | （保留） | — | 通用动词 ✓ |
| L128 modal title | `编辑插件配置` | （保留） | — | 同 L78，modal title 上下文锚点 |
| L138 button | `取消` | （保留） | — | ✓ |
| L139 button | `保存` | （保留） | — | ✓ |
| L148 modal title | `删除服务器` | （保留） | — | 同 L78 |
| L159 button | `删除` | （保留） | — | 通用动词 ✓ |
| L68 pagination | `上一页` / `下一页` | （保留） | — | ✓ |

### C-2 动态文案（servers.js）—— 严重违规清单

#### C-2-A [严重度：低 | 概率：必现] L256 分页信息中英文空格规范

**位置**：servers.js:256
```js
paginationInfoNode.textContent = `第 ${page} / ${Math.max(totalPages, 1)} 页，共 ${total} 条，当前显示 ${start}-${end}`;
```

**判定**：数字与中文之间空格 ✓。**符合规范**。无修改。

#### C-2-B [严重度：低 | 概率：必现] L284/L397 "测试中…"

**位置**：servers.js:284, 397
**判定**：badge / button 状态文案，非反馈 toast，「测试中…」表达"进行中状态"，规范不强制。**保留**。

#### C-2-C [严重度：高 | 概率：必现] L310 空态文案不一致

**位置**：
- `servers_content.html:36` → `暂无服务器`
- `servers.js:310` → `emptyNode.textContent = currentMeta.total > 0 ? "当前页暂无数据。" : "暂无服务器配置。";`

**问题**：
1. **HTML 初始态** vs **JS render 后**两个文案不一致（"暂无服务器" vs "暂无服务器配置。"）。
2. 一处有句号一处无——风格不统一。

**修复**：

| 当前 | 修复后 |
|------|--------|
| HTML L36: `暂无服务器` | `暂无服务器配置` |
| JS L310 当前页空: `当前页暂无数据。` | `当前页暂无数据` |
| JS L310 全空: `暂无服务器配置。` | `暂无服务器配置` |

统一原则：去尾句号；HTML 初始与 JS 一致。

#### C-2-D [严重度：高 | 概率：高] L491 加载失败时 emptyNode 显示错误文案，但 status 已展示

**位置**：servers.js:486-494
```js
} catch (error) {
  const message = error instanceof Error ? error.message : "加载失败";
  setStatus(message, "error");
  loadingNode.classList.add("hidden");
  emptyNode.classList.remove("hidden");
  emptyNode.textContent = message;
  ...
}
```

**问题**：
- `error.message` 由 `api.apiRequest` 构造，已是 `加载失败，<原始 reason>` 格式（api.js:238 `buildActionFailureMessage("加载", finalReason)`）。
- 现在把这条完整失败文案塞进 emptyNode（位置原本展示"暂无服务器"），双重展示 + emptyNode 语义被污染。
- emptyNode 是「列表内容区的空态展示」，错误时应该显示**结构化空态**（图标 + 简短提示），而不是把完整错误文案塞进去。

**修复**：

| 当前 | 修复后 |
|------|--------|
| `emptyNode.textContent = message;` | （删除该行）保留默认 `暂无服务器配置`，由 status bar 显示具体原因 |

#### C-2-E [严重度：低 | 概率：必现] L461 / L826 自抛错误失败原因不友好

**位置**：
- servers.js:461 `throw new Error("加载失败，返回数据格式错误");`
- servers.js:826 `throw new Error("读取失败，返回数据格式错误");`

**问题**：「返回数据格式错误」是开发者视角语言。

**修复建议**（可接受保留，低优先级）：

| 当前 | 修复后 |
|------|--------|
| `加载失败，返回数据格式错误` | `加载失败，响应数据格式异常` |
| `读取失败，返回数据格式错误` | `读取失败，响应数据格式异常` |

#### C-2-F [严重度：极高 | 概率：必现] L614 modalAlert 失败文案双重「失败」拼接

**位置**：servers.js:602-616
```js
let payload;
try {
  payload = buildPayloadFromModal();
} catch (error) {
  const message = error instanceof Error ? error.message : "表单校验失败";
  setModalAlert(`${isEdit ? "更新失败" : "创建失败"}，${message}`, "error");
  return;
}
```

**分析**：
- `buildPayloadFromModal` 抛出的错误如 `"服务器名称不能为空"`（servers.js:578）、`"游戏端口不能为空"`（servers.js:558）、`"Token 长度必须在 1-128 之间"`（servers.js:590）等。
- 拼接后变成：`更新失败，服务器名称不能为空` / `创建失败，Token 长度必须在 1-128 之间`。
- 格式上**符合**规范 `动作 + 失败，原因` ✓。
- `error.message` 本身是**字段校验错误**，原因部分 = 原始 reason，**符合 CLAUDE.md 第 7 条"原样透传"** ✓。

**判定**：合规，**无需修**。

#### C-2-G [严重度：高 | 概率：必现] 进行时文案缺一致性 + 部分含对象名

**位置**：
- servers.js:620 `setModalAlert("正在保存...", "info");` —— ✓ 通用动词，无对象，但用三个英文 `.`
- servers.js:893 `setPluginConfigModalAlert("正在保存...", "info");` —— ✓ 同上
- servers.js:967 `setPluginConfigModalAlert("正在保存地址和 Token...", "info");` —— **违反**："地址和 Token" 等价于操作对象
- servers.js:986 `setPluginConfigModalAlert("正在验证连通性...", "info");` —— 临界，"连通性" 是动作目标的修饰，**可保留**
- servers.js:961 `button.textContent = "正在验证";` —— 按钮 label，无 ellipsis，与其他 "正在..." 风格不一致
- servers.js:659-660 `setDeleteModalAlert(\`正在删除服务器 #${targetServer.id}...\`, "warning");` + `setStatus(\`正在删除服务器 #${targetServer.id}...\`, "warning");` —— **严重违反**：含 "服务器"
- servers.js:1024 `setStatus(\`正在测试服务器 #${serverId} 连通性...\`, "warning");` —— **严重违反**：含 "服务器"

**进行时文案修复表**：

| 位置 | 当前 | 修复后 | 说明 |
|------|------|--------|------|
| L620 | `正在保存...` | `正在保存…` | 英文 ... → 中文 …（可选规范化，**低优先**） |
| L659 | `正在删除服务器 #${targetServer.id}...` | `正在删除…` | **去对象名 + 去 ID**：删除按钮在该行，上下文已明确 |
| L660 | 同上 | `正在删除…` | 同上 |
| L893 | `正在保存...` | `正在保存…` | 同 L620 |
| L967 | `正在保存地址和 Token...` | `正在保存…` | **去对象**：modal 内上下文已明确 |
| L986 | `正在验证连通性...` | `正在验证…` | 简化 |
| L961 | `正在验证` | `正在验证…` | 加 ellipsis 与其他进行时文案统一 |
| L1024 | `正在测试服务器 #${serverId} 连通性...` | `正在测试…` | **去对象**：row 按钮触发，按钮已变 "测试中…" badge，全局 status 简洁化 |

#### C-2-H [严重度：极高 | 概率：必现] L675 删除成功 setStatus

**位置**：servers.js:670-676
```js
setStatus("删除成功", "success");
```

**判定**：`删除成功` 符合规范 ✓。

#### C-2-I [严重度：极高 | 概率：必现] L641 创建/更新成功 setStatus

**位置**：servers.js:641
```js
setStatus(isEdit ? "更新成功" : "创建成功", "success");
```

**判定**：✓ 完全合规。

#### C-2-J [严重度：极高 | 概率：必现] L910 / L1036 保存/测试成功文案

**位置**：
- servers.js:910 `setStatus("保存成功", "success");` —— ✓
- servers.js:1036 `const message = reachable ? "测试成功" : api.buildActionFailureMessage("测试", reason);` —— ✓

**判定**：完全合规。

#### C-2-K [严重度：高 | 概率：必现] L809 模态加载 placeholder 文案

**位置**：servers.js:809
```js
pluginConfigModalBodyNode.innerHTML = '<p class="confirm-modal-text">加载中...</p>';
```

**问题**：
- `加载中...` 是描述性文案，进行时；规范一致性建议 `加载中…`（中文 ellipsis）。
- 用 HTML 字符串赋值注入字符串字面量（虽是常量、无注入面），与同模块其他纯 DOM API（createElement / textContent）**风格不一致**。

**修复**：

| 当前 | 修复后（文案） | 修复后（结构） |
|------|------|------|
| `<p class="confirm-modal-text">加载中...</p>` | `<p class="confirm-modal-text">加载中…</p>` | 改用 `pluginConfigModalBodyNode.replaceChildren(p)` + `createElement('p')` + textContent |

#### C-2-L [严重度：高 | 概率：必现] L881 / L887 plugin-config 校验文案带"字段"业务词

**位置**：
- servers.js:881 `setPluginConfigModalAlert("无可保存的字段", "warning");`
- servers.js:887 `setPluginConfigModalAlert("未修改任何字段", "info");`

**问题**：
- 不是 `动作 + 结果` 格式。
- "字段" 是业务术语，对插件配置场景**用户可理解**。
- 本质是**保存前置条件校验**。

**修复**：

| 当前 | 修复后 |
|------|--------|
| `无可保存的字段` | `没有可保存的修改` |
| `未修改任何字段` | `没有可保存的修改` 或保留 |

或干脆**禁用保存按钮**（diff 为空时 disable），不弹文案。

#### C-2-M [严重度：高 | 概率：必现] L795 plugin-config 空态文案

**位置**：servers.js:795
```js
emptyNote.textContent = "该服务器未返回可编辑的配置字段";
```

**问题**：含"服务器"，属于状态描述，符合 prior art "modal 内上下文" 例外语境；但「未返回可编辑的配置字段」表达晦涩。

**修复**：

| 当前 | 修复后 |
|------|--------|
| `该服务器未返回可编辑的配置字段` | `当前没有可编辑的配置` |

#### C-2-N [严重度：极高 | 概率：必现] L1007 verify connection 结果文案暴露后端 enum

**位置**：servers.js:1001-1009
```js
const tone = probeStatus === "Ok"
  ? "success"
  : probeStatus === "Skipped"
    ? "info"
    : "error";
setPluginConfigModalAlert(
  message ? `${message}${suffix}` : `验证完成：${probeStatus || "未知状态"}`,
  tone,
);
```

**问题**：
- `验证完成：${probeStatus}` —— `probeStatus` 是后端原始 enum（"Ok" / "Skipped" / "Failed" 等），直接拼到面向用户的文案里**违反 CLAUDE.md 第 5 条**精神（API 原始字段值不应作为前端展示文案）。
- 同时不符合 `动作 + 结果` 格式：到底是成功还是失败？
- `${message}${suffix}` —— message 由后端返回（已遵循"原样透传"），但 success 路径下也走"动作+结果"：成功应是「验证成功」+ 可选明细。

**修复**：

```js
// 修复前
message ? `${message}${suffix}` : `验证完成：${probeStatus || "未知状态"}`

// 修复后逻辑（伪代码）
const verbResult =
  probeStatus === "Ok" ? "验证成功" :
  probeStatus === "Skipped" ? "验证已跳过" :
  "验证失败";
const detail = message ? `，${message}${suffix}` : (suffix ? `${suffix}` : "");
const final = `${verbResult}${detail}`;
```

| 当前 | 修复后 |
|------|--------|
| `验证完成：Ok` | `验证成功` |
| `验证完成：Skipped` | `验证已跳过` |
| `验证完成：Failed` | `验证失败，<原始 message>` |
| `<message>（HTTP 200）` | `验证成功，<message>（HTTP 200）` |
| `<message>（HTTP 503）` | `验证失败，<message>（HTTP 503）` |

**严重度：极高**，向用户暴露后端 enum 字面值。

#### C-2-O [严重度：低 | 概率：必现] L774 / L782 验证按钮 / 提示文案

**位置**：
- servers.js:774 `verifyButton.textContent = "验证连通性";` —— ✓ 简洁
- servers.js:782 `hint.textContent = "验证前会自动保存地址和 Token 改动";` —— ✓ 中英文空格 OK

**判定**：✓ 合规。

#### C-2-P [严重度：中 | 概率：低] L268-269 token 按钮 aria-label

**位置**：servers.js:266-270
```js
const setTokenButtonIcon = (button, visible) => {
  button.innerHTML = visible ? HIDE_ICON_SVG : SHOW_ICON_SVG;
  button.title = visible ? "隐藏 Token" : "显示 Token";
  button.setAttribute("aria-label", button.title);
};
```

**判定**：✓ 中英文空格 OK，a11y label 同时设置 title 与 aria-label。

#### C-2-Q [严重度：低 | 概率：必现] L298 失败 badge 文案

**位置**：servers.js:298 `badge.textContent = "失败";`
**判定**：badge label 是「状态描述」非「反馈消息」，单字"失败"足够 ✓。

#### C-2-R [严重度：低 | 概率：必现] L289 成功 badge 文案 `成功`

**位置**：servers.js:289
**判定**：✓ 同 C-2-Q。

#### C-2-S [严重度：中 | 概率：必现] L279 默认 badge `未测试`

**位置**：servers.js:279
**判定**：状态描述，可接受 ✓。

#### C-2-T [严重度：极高 | 概率：必现] L541 删除确认文案

**位置**：servers.js:541
```js
deleteModalTextNode.textContent = `确定删除服务器「${server.name}」吗？此操作不可恢复。`;
```

**问题分析**：
- 含"服务器"——但**这是确认文案，不是反馈文案**，CLAUDE.md 主要约束 toast/message/反馈。确认对话框需要明确告诉用户**删除的是什么对象**。
- "「${server.name}」"——`server.name` 是后端字段透传，已通过 textContent 渲染，无注入风险 ✓。

**判定**：**例外，保留**。确认对话框文案需要对象信息，与 toast 反馈规范分开看 ✓。

---

## C-3 错误消息透传链路审计

`api.apiRequest` (api.js) 处理：
- HTTP 错误（!response.ok）：`buildActionFailureMessage(action, finalReason)` → `<action>失败，<reason>`（api.js:238）
- 网络错误：`buildNetworkErrorMessage(action, error)` → `<action>失败，<error.message>`（api.js:74-77 + 207）
- 超时：`<action>失败，请求超时`（api.js:202）
- 401 + code=unauthorized：自动跳转登录（已落地）✓

servers.js 调用方传入的 `action`：
- L454 `"加载"` ✓
- L633 `"更新" / "创建"` ✓
- L666 `"删除"` ✓
- L819 `"读取"` ✓
- L905 `"保存"` ✓
- L977 `"保存"` ✓
- L992 `"验证"` ✓
- L1030 `"测试"` ✓

**判定**：action 用词全部为**通用动词**，无对象名 ✓。原因透传链路完整 ✓。

---

## D. UX

### D-1 [严重度：中 | 概率：必现] modal 缺 focus trap / focus restore

**事实**：
- servers.js:526 `nameInput.focus();` —— 编辑/创建 modal 打开时聚焦第一个 input ✓
- delete modal（servers.js:536-543）/ plugin-config modal（servers.js:800-837）打开时**无 focus 调用**——键盘用户无法聚焦到 modal 内容。
- 三个 modal 关闭时**无 focus restore**——焦点丢失到 body，无障碍体验差。
- 三个 modal 内**无 focus trap**：Tab 键可跳出 modal 到背景表格按钮，行为混乱。

**Prior art**：commands R1+R2 已落地 modal focus helpers 工具函数（WeakMap based）。servers 漏吃。

**修复方向**：复用 commands 模块已有 helpers，加载逻辑：openModal/openDeleteModal/openPluginConfigModal 各加一行 `setupModalFocus(modalNode, triggerEl)`。

### D-2 [严重度：中 | 概率：必现] modal 不响应 ESC

**事实**：servers.js 全文搜索 `keydown` / `keyup` / `Escape`：**0 处**。三个 modal 都不能用 ESC 关闭，只能点 mask / X / 取消按钮。

**Prior art**：commands R2 已落地 ESC 关闭 helper。

**修复方向**：与 D-1 共用 commands 已有 modal 帮助函数。

### D-3 [严重度：低 | 概率：必现] body scroll lock 缺失

**事实**：servers.js 全文搜索 `overflow` / `scroll` / `body.style`：**0 处**。打开 modal 时背景列表仍可滚动，与 modal 同步移动鼠标滚轮会带动后面表格滚动（取决于浏览器 overscroll 行为）。

**Prior art**：commands R2 已落地 body scroll lock helper。

**修复方向**：复用 helper。

### D-4 [严重度：低 | 概率：必现] mask click 关闭一致性

**事实**：
- 三个 modal 都有 `data-modal-close="1"` / `data-plugin-config-modal-close="1"` / `data-delete-modal-close="1"` 三种不同 attribute，但 mask 元素都加了对应 attr（HTML L75, L125, L145）✓
- handler 各自绑定（servers.js:1102-1110 / 1131-1139 / 1141-1149）✓
- **不一致**：modal X 按钮和 cancel 按钮**总是**关闭；mask click 关闭**仅在非 saving 时**关闭（closeModal 内 `if (modalSaving && !force) return`）。**这是 prior art 已有的行为，保留** ✓

### D-5 [严重度：中 | 概率：必现] alert 节点 role="status" 不适合错误展示

**事实**：HTML 中 statusNode（L32）、modal-alert（L81）、plugin-config-modal-alert（L131）、delete-modal-alert（L151）全部 `role="status"` + `aria-live="polite"`。

**问题**：
- 成功/info/warning 用 `role="status"` ✓
- **错误时**应改 `role="alert"` + `aria-live="assertive"` 让屏幕阅读器立即播报。
- dashboard R1 已落地 `role="alert"` 切换（参考 dashboard.js setStatus / setStatusError 二态切换）。

**修复方向**：setStatus / setModalAlert / setDeleteModalAlert / setPluginConfigModalAlert 在 type === "error" 时改 role + aria-live，其它情况恢复 polite。

### D-6 [严重度：中 | 概率：必现] loading 状态无 aria-busy

**事实**：HTML L35 `<div id="loading" class="empty">正在加载服务器…</div>` 无 `aria-busy="true"`。tableWrapNode 加载时被 .hidden 隐藏，loadingNode 显示。屏幕阅读器无法获知"正在加载"状态。

**Prior art**：dashboard R1 已落地 aria-busy 切换。

**修复方向**：loadingNode 加 `aria-busy="true"`，loadServers 完成后切换。

### D-7 [严重度：中 | 概率：必现] error 状态无重试入口

**事实**：servers.js:486-494 加载失败时只 setStatus，无"重试"按钮。reloadButton 在 toolbar 仍可用，但用户需理解"刷新"= 重试，**显式重试 UX 缺失**。

**修复方向**：emptyNode 在 error 态时附加「点击重试」链接 / button，或在 status bar 错误旁加重试按钮。

### D-8 [严重度：低 | 概率：低] dark mode 适配

**事实**：CSS 用 `var(--surface)` / `var(--text)` / `var(--accent-teal)` 等 design token，依赖 design system 提供 dark mode 切换。servers.css 自身**无 prefers-color-scheme media query**，但同 webui 其他模块共用 token，**out of scope** ✓。

### D-9 [严重度：低 | 概率：低] 响应式断点

**事实**：CSS L510-529 `@media (max-width: 1080px)` 处理 toolbar / form-grid 折叠。**仅一个断点**，但页面布局以表格为主，靠 `overflow: auto` 横向滚动兜底。OK ✓。

### D-10 [严重度：中 | 概率：必现] verifyNextBotConnection 流程 UX 复杂

**事实**：servers.js:933-1019 函数会：
1. 检测 baseUrl/token 是否有 diff
2. 有 diff → PATCH 保存
3. 然后 POST verify-nextbot

**问题**：
- 用户视角：点「验证连通性」按钮 → 看到 modal alert 文案先是「正在保存...」后是「正在验证连通性...」再是 result，三个状态切换。
- 但 button label 自始至终是「正在验证」（servers.js:961），与 alert 文案不一致——用户先看到"保存中"会困惑。
- 若 diff 保存失败但 verify 触发条件已满足，PATCH 抛错 → catch（servers.js:1010）→ alert 显示「保存失败，...」，但 button 文案仍叫"验证"——状态机不清晰。

**修复方向**：button label 与 alert 文案同步，或直接：
- 不在 verify 内偷偷保存：让用户手动先点保存再验证。
- 或保存按钮 disable 时 verify 也 disable，强制先保存。

**严重度：中**，UX 流程歧义。

### D-11 [严重度：低 | 概率：低] 表单字段无 inline 校验提示

**事实**：servers.js:555-600 buildPayloadFromModal 抛错后 setModalAlert 统一显示。**无字段级红框 / aria-invalid**——错误只在 modal 顶部 alert 显示，用户需要自己找哪个字段出错。

**修复方向**：根据 error.message 关键词（"服务器名称"、"游戏端口" 等）映射到对应 input 加 aria-invalid + 红色边框。**成本中等，可 backlog**。

---

## E. JS 错误处理

### E-1 [严重度：低 | 概率：低] fetch try/catch 覆盖率

**事实**：所有 `api.apiRequest` 调用均在 try/catch 内：
- L448-495 loadServers ✓
- L622-649 saveServer ✓
- L662-684 confirmDeleteServer ✓
- L813-836 openPluginConfigModal ✓
- L895-917 savePluginConfig ✓
- L965-1018 verifyNextBotConnection ✓
- L1026-1051 testServerConnectivity ✓

**判定**：✓ 全覆盖。

### E-2 [严重度：极低 | 概率：低] void promise pattern

**事实**：内部 async 函数都通过 `void asyncFn()`（servers.js:400, 408, 776, 1056, 1065, 1071, 1079, 1087, 1093, 1119, 1129）触发。`void` 显式表达"不需要 await，错误自行处理"——pattern 一致 ✓。

**潜在风险**：如果异步函数内 throw 了 try/catch 未捕获的同步错误（如 JSON.stringify 循环引用）会成为 unhandled promise rejection。当前代码看不到此风险，**安全**。

### E-3 [严重度：低 | 概率：低] setInterval / setTimeout

**事实**：servers.js 全文搜索 `setInterval` / `setTimeout`：**0 处**。无定时器清理担忧 ✓。

### E-4 [严重度：中 | 概率：中] modal 状态机不严密

**事实**：
- `pluginConfigVerifyButton` 是模块顶层 let（servers.js:171），verifyNextBotConnection 内 `const originalLabel = button.textContent`（servers.js:959）使用闭包 button，但 `pluginConfigVerifyButton = verifyButton`（servers.js:778）在 render 时赋值。
- 若用户：打开 plugin-config modal → 点验证 → 验证中关闭 modal（force = false 阻止 → ✓）→ 但 modal 已隐藏 → finally 块仍 `button.disabled = false`（servers.js:1015）/ `button.textContent = originalLabel`（servers.js:1016）作用于已 detached 的 button（DOM 已被下次 openPluginConfigModal 替换）。
- 实际无副作用（detached 节点 GC），但**逻辑上不严密**。

**修复方向**：
- closePluginConfigModal force=true 时设 `pluginConfigVerifying = false`（已做 ✓ servers.js:849）。
- verify catch / finally 块判断 `if (button.isConnected)` 再操作。
- 或更彻底：把 verifyButton 提升到 modal 范围状态，模态关闭即 abort。

**判定**：低危，可 backlog。

### E-5 [严重度：低 | 概率：低] modalSaving force 关闭逻辑

**事实**：
- `closeModal(force=false)`：`if (modalSaving && !force) return;`（servers.js:530-531）
- 保存成功路径调用 `closeModal(true);`（servers.js:637）—— 强制关闭 ✓
- mask / X / cancel 都调 `closeModal()` 无 force —— ✓ 保存中无法误关
- 但 `modalSaving` 在 finally 才恢复（servers.js:647-648）：保存出错 → setModalAlert 显示错误 → modalSaving=false → 用户可以再次点保存或取消 → ✓ 

**判定**：✓ 合理。

### E-6 [严重度：中 | 概率：中] verify 流程内联 PATCH，错误归因不清

**事实**：servers.js:966-984 verify 内若 diff 非空会先 PATCH 保存。PATCH 失败时 catch 抛出 `error.message = "保存失败，<reason>"`（api.js:238）→ `setPluginConfigModalAlert(message, "error")`（servers.js:1012）显示"保存失败"。

**问题**：用户点的是「验证连通性」按钮，看到"保存失败"会困惑——既不知道发生了什么，也不知道下一步该点什么。

**修复方向**：将 catch 中错误来源识别为 save vs verify，给不同前缀：
```js
// 改为局部 try/catch 分阶段
try {
  if (Object.keys(diff).length) {
    try {
      await api.apiRequest(.../* save */);
    } catch (e) {
      throw new Error(`验证失败，需要先保存地址和 Token：${e.message}`);
    }
  }
  // verify 阶段
  await api.apiRequest(.../* verify */);
}
```

**判定**：中危，UX + 错误归因。

### E-7 [严重度：低 | 概率：低] race condition：搜索时 loadServers 多次并发

**事实**：B-1 已记。`loadServers` 内 currentMeta / serverStates 状态写入无 lock，多个并发请求最后到达的覆盖先到达的——last-write 不一定是 last-fired。

**修复方向**：requestSeq 单调递增；只接受最新 seq 的响应。或 AbortController（B-1 已覆盖）。

---

## F. 跨模块 backlog（不在本桶修复，仅记录）

- 公共 modal helpers（focus trap / focus restore / ESC / body scroll lock）应抽到共享 util（api.js 同级新增 modal.js？），servers/commands/dashboard 共用。
- aria-busy / role=alert 切换 helper 应在 api.js 之外有 a11y util。
- token 显示策略（A-3 / A-4）需后端配合：提供 `GET /servers/{id}/reveal-token` 临时接口 + 前端清空 DOM 残留。
- CSRF token 全局策略（A-7）。

---

## 结论 + 修复优先级

### 必修（本轮 R1，高严重度 + 高概率）

| ID | 文件:行 | 问题 | 修复 |
|----|---------|------|------|
| C-2-G | servers.js:659, 660, 1024, 967 | 进行时文案含"服务器" / 业务对象 | 全部改通用动词 + ellipsis |
| C-2-N | servers.js:1007 | 暴露后端 enum `probeStatus` 到用户 | 改为 `验证成功` / `验证已跳过` / `验证失败，<reason>` |
| C-2-C | servers.html:36 + servers.js:310 | 空态文案不一致 | 统一为 `暂无服务器配置` / `当前页暂无数据`（去句号） |
| C-2-D | servers.js:491 | emptyNode 显示错误文案污染语义 | 移除 `emptyNode.textContent = message;` |
| C-2-K | servers.js:809 | HTML 字符串拼装 + 中英文 ellipsis | 改 createElement + textContent + `加载中…` |
| C-2-L | servers.js:881, 887 | 校验文案不规范 | 改 `没有可保存的修改` 或直接 disable 保存按钮 |
| C-2-M | servers.js:795 | "未返回可编辑的配置字段" 晦涩 | 改 `当前没有可编辑的配置` |
| B-1 | servers.js:1063 | 搜索框无 debounce + abort | 引入 200ms debounce + AbortController |
| D-5 | servers.js setStatus 系列 | error 时未切 role=alert | type==="error" 时切 role 与 aria-live |
| D-6 | servers.html:35 + servers.js | loading 无 aria-busy | 同 dashboard R1 模式 |
| D-1/D-2/D-3 | servers.js modal 系列 | 缺 focus trap / ESC / scroll lock | 复用 commands R2 helpers |

### 建议修（本轮 R1 末段或 R2）

- B-2 翻页 / per-page 并发 abort（与 B-1 共用 controller）
- D-7 错误状态显式重试入口
- D-10 verify 流程 UX 整合
- E-6 verify 内联保存错误归因
- A-7 CSRF 跨模块治理（不在本桶）

### Backlog（独立专项）

- A-3 / A-4 token DOM 持久化（**高危**，独立迭代）
- B-3 testServerConnectivity 全局并发治理
- B-4 renderTable 细粒度更新
- B-7 timeoutMs 长接口 override
- D-11 字段级 inline 校验提示
- E-4 verifyButton 状态机严密化

### 严重度速记

- **高严重度 issue 数**：约 8（C-2 系列文案违规 5 + D-1/D-2 + B-1）
- **中严重度 issue 数**：约 10
- **低严重度 issue 数**：约 12

文案审计是本次最大产出，CLAUDE.md 规范执行需要在 R1 完成。
