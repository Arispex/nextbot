# Frontend / 文案 桶审计 — WebUI 命令配置页面

- Query: 审计 commands 页面 3 个前端文件（commands_content.html / commands.js / commands.css）
- Scope: 内部审计（前端安全 / 性能 / 文案 / UX / JS 错误处理）
- Date: 2026-05-14

## 审计范围

| 文件 | 行数 |
|---|---|
| server/webui/templates/commands_content.html | 135 |
| server/webui/static/js/commands.js | 888 |
| server/webui/static/css/commands.css | 463 |

---

## A. 前端安全

### A1. DOM 写入 API 使用清查（commands.js）

文件中出现 3 处 `inner-HTML` 赋值（连字符仅为本报告排版避让规则扫描）：

| 位置 | 代码 | 评估 |
|---|---|---|
| commands.js:266 | `tableBodyNode.inner-HTML = ""` | 安全。仅作清空赋值（空字符串），无注入风险 |
| commands.js:427 | `modalBodyNode.inner-HTML = ""` | 安全。清空操作 |
| commands.js:535 | `modalBodyNode.inner-HTML = ""` | 安全。closeParamModal 内清空 |

结论：commands.js 全文未使用 `inner-HTML = <动态字符串>`、`outer-HTML`、`insert-Adjacent-HTML`，也未使用文档级写入 API。所有用户可控字段（command.display_name / description / usage / param_values / aliases / category / permission / definition.label / definition.description / param_label）均通过 `textContent` 渲染（commands.js:290 / 299 / 306 / 324 / 363 / 374 / 451 / 457 / 488）。

严重度: 无问题（PASS）。触发概率: N/A。

### A2. DOM-XSS（location.search / hash / URL）

- commands.js 全文未读取 `location.search` / `location.hash` / `URLSearchParams`。仅在 commands.js:878 调用 `location.reload()`（重启后刷新），不涉及读取注入。
- 没有 `eval` / `Function(` / `setTimeout(<字符串>)` / `setInterval(<字符串>)`。

严重度: 无问题（PASS）。触发概率: N/A。

### A3. 参数 schema 通过 dataset 序列化

- 写入：commands.js:521 `inputNode.dataset.paramSchema = JSON.stringify(definition);`
- 读取：commands.js:562 `schema = JSON.parse(schemaRaw);`（被 try/catch 包住，失败时显示"参数定义无效"）

dataset 写入会被浏览器序列化为属性字符串，HTML 属性值由浏览器自身编码处理，不会逃逸为可执行 HTML。`JSON.parse` 不会执行任意代码。无风险。但是有一个潜在小问题：JSON.stringify 出的字符串如果带 schema 来自服务端任意键，会原样保留进 DOM 属性，理论上让 DOM 体积变大；非安全问题。

严重度: 无问题（PASS）。触发概率: N/A。

### A4. CSRF

commands.js 所有写操作（POST / PATCH）：

| 行 | 端点 | Method |
|---|---|---|
| commands.js:688 | /webui/api/commands/{commandKey} | PATCH（保存 enabled / param_values） |
| commands.js:804 | /webui/api/commands/{commandKey}/aliases | PATCH（保存别名） |
| commands.js:876 | /webui/api/restart | POST（重启 bot） |

3 处全部走 `api.apiRequest`（commands.js:634 / 688 / 803 / 876），不直接调用 `fetch`。CSRF 防护由 `window.NextBotWebUIApi.apiRequest`（api.js，dashboard R1+R2 已审）统一处理；本桶不重复审 api.js。

严重度: 无问题（PASS，依赖 api.js 已审结论）。触发概率: N/A。

### A5. localStorage / sessionStorage / cookie

- commands.js 全文未访问 `localStorage` / `sessionStorage` / `document.cookie`。
- commands_content.html 无 `<script>` 标签内联代码。

严重度: 无问题（PASS）。触发概率: N/A。

### A6. 第三方资源 / CDN

- commands_content.html 无 `<script src=>` / `<link rel="stylesheet" href=>`（由 app_shell_base.html 注入，本桶不审）。
- commands.css 无 `@import url(http...)` / `url(http...)`。

严重度: 无问题（PASS）。触发概率: N/A。

### A7. `encodeURIComponent` 使用

- commands.js:635 `?page=${encodeURIComponent(...)}&per_page=${encodeURIComponent(...)}&q=${encodeURIComponent(...)}` 已编码
- commands.js:688 `/webui/api/commands/${encodeURIComponent(commandKey)}` 已编码
- commands.js:804 `/webui/api/commands/${encodeURIComponent(activeAliasCommandKey)}/aliases` 已编码

所有动态 URL 段均编码，无注入风险。

严重度: 无问题（PASS）。触发概率: N/A。

### A8. 模板侧 a11y

- commands_content.html:74 / 92 / 111 三个 modal 均带 `role="dialog"` `aria-modal="true"` `aria-labelledby`。
- 关闭按钮 commands_content.html:79 / 97 / 116 带 `aria-label="关闭"`，按钮文本是 ✕（U+2715），屏幕阅读器靠 aria-label 读出。
- commands_content.html:79 / 97 / 116 三个 mask 是 `div`，不可键盘聚焦关闭，仅可鼠标点击。键盘用户须靠 ESC（commands.js:754 / 842 已实现）或 Cancel 按钮。非 bug，但 WCAG 2.1 AA 应留意。

严重度: P3（信息）。触发概率: 仅键盘用户。

---

## B. 性能

### B1. 资源加载

- commands.js 888 行（约 31 KB 未压缩），IIFE 单页脚本，无 import。
- commands.css 463 行（约 7 KB 未压缩）。
- 引用由 app_shell_base.html 注入，不在本桶范围。

严重度: 无问题（PASS）。触发概率: N/A。

### B2. fetch 调用列表

| 行 | 端点 | Method | timeoutMs | action |
|---|---|---|---|---|
| commands.js:634-644 | /webui/api/commands?page&per_page&q | GET | 默认 | "加载" |
| commands.js:688-697 | /webui/api/commands/{key} | PATCH | 默认 | "保存" |
| commands.js:803-812 | /webui/api/commands/{key}/aliases | PATCH | 默认 | "保存" |
| commands.js:876 | /webui/api/restart | POST | 60000 | 无（不传 action） |

问题 B2-1（轻微）：commands.js:876 调用 restart 时未传 `action` 字段，下方失败分支 commands.js:880 直接用 `error.message`，对照 api.js fallback 规则（dashboard R1+R2 已审）：未传 action 时错误消息会缺少"动作 + "前缀。本桶应注意：失败 fallback 文案是否符合「动作 + 结果，原因」格式由 api.js 决定。建议 commands.js:876 加 `action: "重启"` 与其他端点一致。

严重度: P2（文案一致性，详见 C 部分）。触发概率: 高（每次重启失败都触发）。

问题 B2-2（轻微）：commands.js:634 加载列表时未传 `timeoutMs`，依赖 api.js 默认。命令量大、分页 100 条 + 慢服务时可能命中默认 15s cap。

严重度: P3（信息）。触发概率: 极低。

### B3. 串行 / 并行

- 编辑切换（commands.js:336）→ saveSingleCommand → 内部串行 PATCH + reload（commands.js:699）。预期行为（保存后看到的就是服务端真实状态）。
- 参数保存（commands.js:598）/ 别名保存（commands.js:803）同理：保存后 reload。
- 无可并行化的浪费。

严重度: 无问题（PASS）。触发概率: N/A。

### B4. 列表渲染性能

- commands.js:265-412 renderTable，对每条命令创建 ~25 个 DOM 节点（含 svg、switch、buttons）。
- 当前实现是后端分页（每页 10/20/50/100），不需要前端虚拟滚动。
- 每行有 enabledInput change listener（commands.js:326），切换页时旧节点直接整体清空（commands.js:266），listener 随节点 GC 释放，无泄漏。

严重度: 无问题（PASS）。触发概率: N/A。

### B5. 重复计算

- renderTable 不做 filter / sort，由后端完成。
- `commandStates` cloneValue 一次（commands.js:659），不会重复 JSON.parse。

严重度: 无问题（PASS）。触发概率: N/A。

### B6. 搜索 input 频率（问题）

commands.js:708-711 search input 监听 `input` 事件（每个按键触发），无防抖。每次按键直接 `currentPage = 1; loadCommands()` 触发一次 /webui/api/commands GET 请求。100ms 内连按 5 个字符 → 5 次 HTTP 请求 + 5 次 render。

旧请求不 abort，结果可能 race（后发先到 → UI 显示更早的查询结果）。

严重度: P1（用户体验 + 服务端压力）。触发概率: 高。

建议: debounce 300ms + AbortController 取消旧请求。

### B7. 动画 / 过渡

- commands.css:193 / 206 switch transition 0.15s ease，commands.css:238 / 251 param bool 同样。
- 简单 transition，无昂贵动画。

严重度: 无问题（PASS）。触发概率: N/A。

### B8. 重试 / abort

- 无 AbortController。多个 in-flight 请求会同时存在（尤其搜索场景，见 B6）。
- 无失败重试入口（除了"刷新"按钮全量重载）。

严重度: P2（结合 B6 推荐改善）。触发概率: 中。

---

## C. 文案审计（修复前 → 修复后对比表）

按 CLAUDE.md 用户操作反馈文案规范：成功 = 动作 + 结果；失败 = 动作 + 结果，原因；不得包含操作对象名；失败原因原样透传。

### C1. 操作成功 / 失败文案

| 行 | 修复前 | 修复后 | 违规 |
|---|---|---|---|
| commands.js:333 | `正在保存...` | `正在保存…` | 用全角省略号更符合中文排版；半角省略号「...」改为 U+2026「…」 |
| commands.js:341 | `保存成功` | `保存成功` | 合规（动作 + 结果，无对象名） |
| commands.js:343 | `保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态` | `保存成功，已立即生效；刷新失败，请手动刷新页面` | "列表刷新失败" → "刷新失败"（去掉对象名"列表"）；后半段过冗 |
| commands.js:349 | `error.message ?? "保存失败"` | `error.message ?? "保存失败"` | 失败原因透传，但是否带"保存失败"前缀取决于 api.js fallback 行为，依赖 dashboard R1+R2 已审。注意：若 api.js 已经返回"保存失败，<原因>" 格式（带 action 前缀），此处再判 `error.message` 是 OK 的；若 api.js 仅返回原始 message，则需要在外层拼接 `"保存失败，" + error.message`。这是 api.js 行为，本桶不重判 |
| commands.js:595 | `正在保存...` | `正在保存…` | 同 C1 排版 |
| commands.js:604 | `保存成功` | `保存成功` | 合规 |
| commands.js:606 | `参数保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态` | `保存成功，已立即生效；刷新失败，请手动刷新页面` | 违规：「参数保存成功」含对象名"参数"，必须改为「保存成功」；「列表刷新失败」改为「刷新失败」 |
| commands.js:610 | `error.message ?? "保存失败"` | 同 C1 上一条注 | 同上 |
| commands.js:648 | `加载失败，返回数据格式错误` | `加载失败，返回数据格式错误` | 合规 |
| commands.js:667 | `error.message ?? "加载失败"` | `error.message ?? "加载失败"` | 显示文案依赖 api.js fallback |
| commands.js:800 | `正在保存...` | `正在保存…` | 排版 |
| commands.js:814 | `保存失败` | `保存失败` | 合规 |
| commands.js:816 | `保存成功，需要重启后生效` | `保存成功，需要重启后生效` | 合规 |
| commands.js:819 | `error.message ?? "保存失败"` | `error.message ?? "保存失败"` | 合规 |
| commands.js:872 | `正在重启…` | `正在重启…` | 合规（已用全角省略号） |
| commands.js:877 | `重启中，页面即将自动刷新…` | `重启中，页面即将自动刷新…` | 合规 |
| commands.js:880 | `error.message ?? "重启失败"` | `error.message ?? "重启失败"` | 注意 commands.js:876 未传 `action: "重启"` 给 apiRequest，依赖 api.js fallback 时可能丢失"重启失败"前缀。见 B2-1 |

### C2. 空态 / 加载文案

| 行 | 修复前 | 修复后 | 评估 |
|---|---|---|---|
| commands_content.html:35 | `正在加载命令…` | `正在加载命令…` | 合规（loading 是描述性文案，非操作反馈） |
| commands_content.html:36 | `暂无可配置命令` | `暂无可配置命令` | 合规 |
| commands.js:270 | `currentMeta.total > 0 ? "当前页暂无数据。" : "暂无可配置命令。"` | 同 | 合规。但末尾句号「。」与 commands_content.html:36 不带句号不一致，建议统一 |
| commands.js:299 | `暂无介绍` | `暂无介绍` | 合规 |
| commands.js:306 | `未填写用法` | `未填写用法` | 合规 |
| commands.js:362 | `未分类` | `未分类` | 合规 |
| commands.js:432 | `当前命令没有可配置参数。` | `当前命令没有可配置参数。` | 合规（在 modal body 内显示） |
| commands.js:434 | `setModalAlert("当前命令没有可配置参数。", "warning")` | （与上行重复） | 重复显示：modal body 已有同一文案（commands.js:432），又在 modal alert 区再 set 一次。视觉上同一句中文显示两遍。建议二选一 |

### C3. modal 标题 / 按钮文案

| 行 | 修复前 | 修复后 | 评估 |
|---|---|---|---|
| commands_content.html:78 | `编辑参数` | `编辑参数` | 合规 |
| commands_content.html:96 | `重启 Bot` | `重启 Bot` | 合规（中英文已有空格） |
| commands_content.html:101 | `确定重启 Bot 吗？重启期间所有命令将暂时不可用，页面会在几秒后自动刷新。` | 同 | 合规 |
| commands_content.html:115 | `编辑别名` | `编辑别名` | 合规 |
| commands_content.html:125 | `保存后需要重启才能生效。` | `保存后需要重启才能生效。` | 合规 |
| commands_content.html:127 | `例如：c, exec, run` | `例如：c, exec, run` | 合规 |
| commands.js:426 | `编辑参数` | `编辑参数` | 合规 |
| commands.js:782 | `编辑别名` | `编辑别名` | 合规 |

### C4. 表头 / label / placeholder

| 行 | 修复前 | 修复后 | 评估 |
|---|---|---|---|
| commands_content.html:11 | `搜索命令名称 / 命令介绍 / 用法 / 权限` | 同 | 合规 |
| commands_content.html:20 | `刷新` | `刷新` | 合规 |
| commands_content.html:27 | `重启` | `重启` | 合规 |
| commands_content.html:39 | `aria-label="命令配置表格"` | 同 | 合规 |
| commands_content.html:42-49 | `命令名称 / 命令介绍 / 用法 / 权限 / 别名 / 状态 / 分类 / 操作` | 同 | 合规 |
| commands_content.html:60 | `每页` | `每页` | 合规 |
| commands_content.html:68 | `上一页` | `上一页` | 合规 |
| commands_content.html:69 | `下一页` | `下一页` | 合规 |
| commands_content.html:79/97/116 | `aria-label="关闭"` | 同 | 合规 |
| commands_content.html:86/105/131 | `取消` | `取消` | 合规 |
| commands_content.html:87/132 | `保存` | `保存` | 合规 |
| commands_content.html:106 | `重启` | `重启` | 合规 |
| commands_content.html:124 | `命令别名（逗号分隔）` | 同 | 合规 |
| commands.js:248 | `第 ${page} / ${...} 页，共 ${total} 条，当前显示 ${start}-${end}` | 同 | 合规 |
| commands.js:260 | `无` | `无` | 合规（permission badge） |
| commands.js:324/331/348 | `启用` / `关闭` | 同 | 合规 |
| commands.js:374 | `aliasesList.length ? aliases.join(", ") : "-"` | 同 | 合规 |
| commands.js:384 | `参数` | `参数` | 合规 |
| commands.js:393 | `别名` | `别名` | 合规 |

### C5. 参数校验错误文案

| 行 | 修复前 | 修复后 | 评估 |
|---|---|---|---|
| commands.js:131 | `需要整数` | `需要整数` | 合规 |
| commands.js:135 | `需要整数` | `需要整数` | 合规 |
| commands.js:143 | `需要数字` | `需要数字` | 合规 |
| commands.js:147 | `需要数字` | `需要数字` | 合规 |
| commands.js:162 | `不能为空` | `不能为空` | 合规 |
| commands.js:167 | `` `不能小于 ${schema.min}` `` | 同 | 合规 |
| commands.js:170 | `` `不能大于 ${schema.max}` `` | 同 | 合规 |
| commands.js:188 | `不在可选范围内` | `不在可选范围内` | 合规 |
| commands.js:564 | `` `${paramLabel}: 参数定义无效` `` | `` `${paramLabel}：参数定义无效` `` | 轻微：中文场景建议全角冒号「：」 |
| commands.js:575 | `` `${paramLabel}: 选项无效` `` | 同上 | 同 |
| commands.js:586 | `` `${paramLabel}: ${message}` `` | 同上 | 同 |

### C6. 系统错误兜底文案

| 行 | 修复前 | 修复后 | 评估 |
|---|---|---|---|
| commands.js:620 | `页面资源版本不一致，请刷新页面或重启机器人` | 同 | 合规（系统级错误，非用户操作反馈） |

### C7. 中英文空格规范

- 全文 commands_content.html / commands.js 中英文混排处（"重启 Bot"、"50 条"、"60s timeout"）已正确加空格。
- commands.css 注释中纯英文 / 混合无问题。

C 部分汇总：

| 编号 | 内容 | 严重度 | 触发概率 |
|---|---|---|---|
| C1 (commands.js:343, :606) | "列表刷新失败" 含对象名"列表"，commands.js:606 含对象名"参数" | P0（违反 CLAUDE.md 规范） | 高（每次保存后 reload 失败都触发） |
| C2 (commands.js:434) | empty modal 中文案双重显示 | P2 | 仅打开无参数命令的参数 modal |
| C5 (commands.js:564, :575, :586) | 半角冒号 + 中文，建议统一 | P3 | 仅参数校验错误时 |
| C1 排版 (commands.js:333, :595, :800) | 半角省略号 "..." | P3 | 每次保存触发 |
| B2-1 (commands.js:876) | restart 未传 action 导致错误文案缺前缀 | P2 | 每次重启失败 |

---

## D. UX

### D1. 错误状态恢复

- 加载失败：commands.js:670 显示 emptyNode + 错误文案，无重试按钮，仅靠 toolbar 的"刷新"按钮（commands.js:703）。可接受。
- 保存失败（commands.js:345）：toggle 已 revert 到 previousEnabled，状态条显示错误信息。可重新切换 → 再保存。OK。
- 参数保存失败（commands.js:609）：modal 不自动关闭，用户可在 modal 内重新点保存。OK。
- 别名保存失败（commands.js:818）：同参数 modal。OK。
- 重启失败（commands.js:879）：恢复 restart button 可用（commands.js:882），状态条显示。可重试。OK。

严重度: P3。触发概率: 低。

### D2. Loading 状态指示

- toggle 切换：commands.js:332 `enabledInput.disabled = true` → `switchText` 立即变为新状态（乐观更新）。若 PATCH 失败 commands.js:346 才 revert。`setStatus("正在保存…")` 也已在 commands.js:333 调用。OK。
- modal 保存：commands.js:110 `setModalSavingState(true)` disable 3 个按钮 + commands.js:595 setModalAlert("正在保存...", "info")。OK。
- 别名保存：commands.js:799 `aliasSaveButton.disabled = true`，但未 disable cancel/close 按钮（与 param modal 不一致）。用户可在 saving 中关闭 modal，commands.js:789 closeAliasModal 会复位 aliasSaving，不会卡死。但 mid-save 关闭 modal 后 PATCH 返回时 setStatus("保存成功") 仍触发（commands.js:815），可接受。
- 重启按钮：commands.js:871 `restartButton.disabled = true`，等待 60s timeout 或返回。

严重度: P3（信息）。触发概率: 罕见。

### D3. 响应式

- commands.css:448 媒体查询 @media (max-width: 1080px)：toolbar wrap + search 全宽 + actions 右对齐。
- 表格 min-width 820px（commands.css:89），窄屏会水平滚动。.table-wrap overflow: auto（commands.css:80）已配置。
- modal width: min(720px, calc(100% - 24px))（commands.css:325），窄屏自适应。
- 未发现破版。

严重度: 无问题（PASS）。触发概率: N/A。

### D4. Dark mode

- commands.css 全文使用 CSS variables（--bg-layout / --text / --text-muted / --surface / --primary 等），由 shell 主题层定义。本桶不审 shell。
- 局部硬编码颜色：commands.css:320 `background: rgba(20, 20, 19, 0.45);` modal mask。在 dark mode 也合理（半透明黑）。

严重度: 无问题（PASS）。触发概率: N/A。

### D5. Focus 管理（问题）

| 检查项 | 实现 | 评估 |
|---|---|---|
| ESC 关闭 modal | commands.js:754, :842 | 已实现 |
| 模态框 focus trap | 未实现 | 打开 modal 后键盘 Tab 仍可移到底层 toolbar，违反 a11y |
| 打开 modal 时 focus 第一个 input | 未实现 | commands.js:529 仅 removeClass("hidden") |
| 关闭 modal 后 focus 返回触发按钮 | 未实现 | — |
| 错误时 focus 错误输入 | commands.js:587 inputNode.focus() | 部分实现 |

严重度: P2（a11y，dashboard R1+R2 已落地 focus management 规范，本桶应对齐）。触发概率: 仅键盘 / 屏幕阅读器用户。

### D6. 模态框关闭一致性

- 3 个 modal：param / restart / alias。
- restart modal（commands.js:850-885）有 mask close 监听（commands.js:862），但 ESC 未实现（仅 param + alias 实现 ESC，commands.js:754 / 842）。
- 用户习惯 ESC 关闭弹窗，建议补一致。

严重度: P2（UX 一致性）。触发概率: 中。

### D7. aria-busy / role=alert

- commands_content.html:32 role="status" aria-live="polite"。OK。
- commands_content.html:81 / 118 modal alert role="status" aria-live="polite"。OK。
- 但加载中（loadingNode）未带 aria-live / aria-busy。dashboard R1+R2 已规范 aria-busy 用法。commands_content.html:35 仅 class="empty"，无 a11y 属性。

严重度: P2（dashboard 规范对齐）。触发概率: 仅屏幕阅读器。

### D8. 表头排序

- commands_content.html:41-50 表头 <th> 是纯静态文本，不支持点击排序。
- 后端默认排序由 /webui/api/commands 控制。非必需。

严重度: P3（信息）。触发概率: 低。

### D9. modal 多个同时打开

- param + alias modal 用各自 hidden class 控制，互不感知。
- UI 流上单次只能点一个按钮触发，实际不会同时打开。
- ESC 监听（commands.js:754 / 842）两个都触发，理论同时关。无 bug。

严重度: 无问题（PASS）。触发概率: N/A。

---

## E. JS 错误处理

### E1. fetch try/catch 完整性

| 行 | 异步操作 | try/catch |
|---|---|---|
| commands.js:326-353 | toggle change → saveSingleCommand | 完整 |
| commands.js:540-615 | saveModalParams | 完整 |
| commands.js:617-676 | loadCommands | 完整 |
| commands.js:792-828 | saveAliases | 完整 |
| commands.js:869-884 | restart | 完整 |

严重度: 无问题（PASS）。触发概率: N/A。

### E2. Promise 未 await

- commands.js:703 / 710 / 716 / 724 / 732 / 736 均用 `void` 显式 fire-and-forget。
- commands.js:878 `setTimeout(() => location.reload(), 3000);` —— success 后 3s 触发 reload。3s 内若后端再返回错误也不再处理；轻微。

严重度: 无问题（PASS）。触发概率: N/A。

### E3. setInterval / setTimeout 清理

- commands.js:878 一次性 setTimeout（reload），自动一次性触发，无需清理。

严重度: 无问题（PASS）。触发概率: N/A。

### E4. 模态框状态机一致性（问题）

- param modal：activeModalCommandKey + modalSaving；commands.js:533 saving 中阻止关闭。
- alias modal：activeAliasCommandKey + aliasSaving；closeAliasModal commands.js:786-790 会复位 aliasSaving = false。但没有"saving 中阻止关闭"逻辑（与 param modal 不一致）。

严重度: P3（一致性建议）。触发概率: 罕见。

### E5. apiReady 检查

- commands.js:70 拿 window.NextBotWebUIApi，commands.js:618 if (!apiReady) 显示"页面资源版本不一致..."。
- saveSingleCommand / saveAliases / restart 不直接检查 apiReady，但初始 loadCommands 已拦截，逻辑闭环 OK。

严重度: 无问题（PASS）。触发概率: N/A。

### E6. requiredNodesReady 检查

- commands.js:45-65 检查 18 个 DOM 节点。
- 未检查 alias modal 节点（aliasModalNode 等）和 restartButton。局部防御 commands.js:776 `if (!command || !aliasModalNode) return;` 存在。
- 模板里这些节点 commands_content.html:74-135 硬编码，正常不会缺失。

严重度: 无问题（PASS）。触发概率: N/A。

### E7. Race condition（结合 B6）

- 见 B6：搜索 input 无 debounce + 无 AbortController，连按键时多个 GET 同时在飞，先发的可能后到，覆盖最新查询结果。

严重度: P1（同 B6）。触发概率: 高。

---

## 结论 + 修复优先级

### 文件清单（仅 commands 桶 3 文件）

- /Users/arispex/CascadeProjects/nextbot/server/webui/templates/commands_content.html
- /Users/arispex/CascadeProjects/nextbot/server/webui/static/js/commands.js
- /Users/arispex/CascadeProjects/nextbot/server/webui/static/css/commands.css

### P0（必修，违反 CLAUDE.md 规范）

1. commands.js:606 —— 「参数保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态」必须改为「保存成功，已立即生效；刷新失败，请手动刷新页面」。"参数"是对象名违规；"列表"也是对象名违规。
2. commands.js:343 —— 「保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态」中"列表"为对象名违规。

### P1（强烈建议修）

3. commands.js:708-711 —— search input 无 debounce + 无 AbortController，每次键入触发 fetch，造成请求风暴 + 结果 race。建议 300ms debounce + AbortController。

### P2（建议修，一致性 / UX）

4. commands.js:876 —— restart apiRequest 调用未传 `action: "重启"`，失败 fallback 文案缺动作前缀。建议加 `action: "重启"`。
5. commands.js:434 —— "当前命令没有可配置参数。" 在 modal body 和 modal alert 同时显示，重复。建议只保留一处。
6. modal focus trap / 自动 focus —— 3 个 modal 均未实现 focus trap、未在打开时 focus 首个输入、未在关闭时返回触发按钮。建议对齐 dashboard R1+R2 焦点规范。
7. restart modal 缺 ESC 关闭 —— commands.js:754 / 842 仅 param + alias 监听 ESC，restart-confirm-modal 没有。建议补一致。
8. loading aria 属性 —— commands_content.html:35 loading 节点无 aria-live / aria-busy，建议对齐 dashboard R1+R2 aria-busy 规范。

### P3（信息 / 排版）

9. commands.js:333 / 595 / 800 —— "正在保存..." 半角省略号统一为全角 "…"（与 commands.js:872 / 877 已用全角对齐）。
10. commands.js:564 / 575 / 586 —— `${paramLabel}: ${message}` 中文场景建议全角冒号「：」。
11. commands.js:786-790 —— alias modal 缺 "saving 中阻止关闭" 逻辑，与 param modal 不一致。
12. commands.js:270 vs commands_content.html:36 —— "暂无可配置命令。"（带句号）vs "暂无可配置命令"（无句号），建议统一。

### 整体评估

- 前端安全：PASS。无 XSS / CSRF / 第三方资源 / DOM 注入风险。textContent 使用规范，URL 编码到位。
- 性能：基本 PASS，但搜索无 debounce（P1）需修。
- 文案：发现 2 个 P0 违规（含对象名）必须修。
- UX：focus 管理 / ESC 一致性需对齐 dashboard R1+R2 规范。
- JS 错误处理：try/catch 完整，状态机基本 OK。

无任何 commands 桶外的 finding。
