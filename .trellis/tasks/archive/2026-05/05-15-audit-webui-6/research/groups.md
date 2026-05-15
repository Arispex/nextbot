# Audit: WebUI 身份组管理页面（Round 1 全量扫）

- **Scope**：仅以下 4 个文件
  - `server/routes/webui_groups.py`（421 LOC）
  - `server/webui/templates/groups_content.html`（133 LOC）
  - `server/webui/static/js/groups.js`（610 LOC）
  - `server/webui/static/css/groups.css`（404 LOC）
- **Date**：2026-05-15
- **基准 prior art**：`05-15-webui-servers-audit-r2`（commit `1355521`，~22 项）+ `05-15-webui-commands-audit-r3`（commit `f512c8c`，~33 项）+ `05-14-webui-dashboard-audit-r3`（commit `c1a96ca`，~20 项）

## 总览

| 维度 | Critical | High | Medium | Low | 合计 |
|---|---|---|---|---|---|
| Security | 0 | 1 | 5 | 2 | 8 |
| Performance | 0 | 0 | 4 | 1 | 5 |
| UX | 0 | 1 | 8 | 3 | 12 |
| Copy | 0 | 0 | 3 | 4 | 7 |
| **小计** | **0** | **2** | **20** | **10** | **32** |

> 与 servers R1（~22）、commands R1+R2（~33）、dashboard R1+R2（~20）量级一致。前端 UX 缺口最多（与 R1 commands 同形态），后端较干净（一处保留字漏判属于安全 High）。

## Top 3 高严重度

1. **H-1**（security）：`webui_groups.py` 创建时未校验 `RESERVED_GROUP_NAMES`（`owner / admin / root / system / superuser`），但 bot 端 `nextbot/plugins/group_manager.py:194` 已强制拒绝；WebUI 路径绕过保留字导致 `admin` 等身份组可被创建，造成 webui / CLI 行为割裂 + 运维误导。
2. **H-3**（ux）：删除身份组没有任何"受影响用户回退到 `default`" 的二次告知；后端 `webui_groups_delete` 静默把所有该组用户重写为 `GROUP_DELETE_FALLBACK`（`nextbot/db.py:110`），与 bot 端 `group_manager.py:302` 主动告警 "👤 将影响 X 个用户" 形成行为不一致。
3. **M-S-1**（security / log injection）：`logger.warning(f"参数校验失败：field={exc.field or ''}，reason={exc}")`（`webui_groups.py:186`）等多处把用户原始输入直接拼到日志行，PUT/DELETE 的 `group_name` path 参数未做 newline / 控制字符过滤；攻击者可通过 URL 编码的 `%0a` 污染日志。

---

## 1. Security

### H-1 创建身份组未校验 RESERVED_GROUP_NAMES
**File**: `server/routes/webui_groups.py:47-56, 133-149, 261-317`
**Dimension**: security
**Issue**: bot 端 `nextbot/plugins/group_manager.py:194` 已强制 `name.lower() in RESERVED_GROUP_NAMES` 拒绝 `owner / admin / root / system / superuser`，集合在 `nextbot/db.py:101` 集中维护（注释："owner 也不应建——owner 是 .env 短路非 DB 组，创建一个名叫 owner 的组对实际权限无影响但会误导后续运维"）。但 `_validate_create_payload` / `_normalize_group_name` 完全没有引用此集合，WebUI 路径可成功创建 `admin` / `root` 等"看似特权实际无效"的身份组。
**Fix sketch**: `_normalize_group_name` 内 `if value.lower() in RESERVED_GROUP_NAMES: raise GroupPayloadValidationError("身份组名称为系统保留字", field="name")`；顶部 import 加 `RESERVED_GROUP_NAMES`。
**Risk if unfixed**: WebUI / 命令行权限模型分裂，运维以为创建了 admin 组可控制管理员，实际无任何效果但污染 group 列表与继承图。

### M-S-1 日志注入：用户输入未过滤 newline / 控制字符
**File**: `server/routes/webui_groups.py:186, 234, 251, 310, 331, 365, 378, 390, 414`
**Dimension**: security
**Issue**: 多处 `logger.warning(f"... name={group_name} ...")` / `f"...reason={exc}"` 直接拼接用户控制的字段（`group_name`、`exc.field`、异常消息）。`_GROUP_NAME_PATTERN` 限制了 create 路径的 name，但 PUT/DELETE 的 path 参数 `group_name` 来自 URL，FastAPI 解码 `%0a` → `\n` 后未经校验直接落入日志。攻击者通过 `DELETE /webui/api/groups/x%0a%5BCRITICAL%5D%20fake` 可注入伪造日志行。
**Fix sketch**: 复用 / 新增 `_sanitize_log(value)` helper：`re.sub(r"[\r\n\t\x00-\x1f]", "_", str(value)[:200])`；或对 PUT/DELETE 的 `group_name` 在落库前先跑 `_GROUP_NAME_PATTERN.fullmatch`，不匹配直接返回 404。
**Risk if unfixed**: 日志被污染干扰排查；告警规则可能被绕过；审计链断裂。

### M-S-2 PUT/DELETE path 参数未做格式校验
**File**: `server/routes/webui_groups.py:320-321, 375-376`
**Dimension**: security
**Issue**: `group_name: str` path 参数没跑 `_GROUP_NAME_PATTERN`，任何长串都会被 SQLAlchemy 当 string 处理。即使 SQLite primary key 字符串比较安全，也应该尽早拒绝以减小日志注入面 (M-S-1) 与慢路径放大。
**Fix sketch**: 在每个 PUT/DELETE handler 顶部加：

```python
if _GROUP_NAME_PATTERN.fullmatch(group_name) is None:
    return api_error(status_code=404, code="not_found", message="身份组不存在")
```

或者用 FastAPI `Path(..., regex=...)`。
**Risk if unfixed**: 慢路径放大 + 日志噪声。

### M-S-3 `_validate_inherits_targets` 错误消息泄漏存在性
**File**: `server/routes/webui_groups.py:90-109`
**Dimension**: security
**Issue**: missing 计算 + 错误消息 `f"继承目标不存在：{missing[0]}"`（line 107）回报具体不存在的组名。功能正确，但若用户传入 inherits 列表去探测哪些组存在（user enumeration），错误消息会泄漏存在性。考虑到 WebUI 已经过 auth middleware 且 `/webui/api/groups/options` 本身就返回全部组名，严重度 Medium。
**Fix sketch**: 错误消息泛化为 `"继承目标不存在"` 不带具体名字。
**Risk if unfixed**: 已登录用户可枚举组名，但 options 接口已暴露 → 维持 Medium 不升 High。

### M-S-4 列表接口未限制 `q` 长度
**File**: `server/routes/webui_groups.py:202`
**Dimension**: security
**Issue**: `keyword = str(request.query_params.get("q") or "").strip().lower()` 无长度上限。servers R1 的 A-9 修复在 query 层加了 keyword 长度上限。此处可传超长 `q` 走全表 in-memory filter → 放大 DoS 风险。
**Fix sketch**: 添加 `if len(keyword) > 64: return api_error(...)`；或参考 servers R1 的统一 helper。
**Risk if unfixed**: 已登录用户可发送超长查询字符串放大 CPU/内存负载。

### M-S-5 5 个 endpoint 全部缺 `client_ip` / `user_agent` 日志
**File**: `server/routes/webui_groups.py:196, 245, 262, 321, 376` 全部 5 个 endpoint
**Dimension**: security / observability
**Issue**: prior art：servers R1 D-2、commands R2 M-B3 都已统一补 `client_ip = _client_ip(request)` + `user_agent = request.headers.get("user-agent", "")[:200]` 落 logger.info / warning / exception。groups 全部 5 个 endpoint（list / options / create / update / delete）都没有此字段。delete / create / update 是**状态变更**操作，必须有审计链。注意 delete handler (line 376) 当前签名 `webui_groups_delete(group_name: str)` 缺少 `request: Request` 参数。
**Fix sketch**: `from server.routes.webui import _client_ip`；所有 5 个 endpoint 添加 `request: Request` 参数；所有 logger.info / warning / exception 追加 `client_ip={client_ip} user_agent={user_agent!r}`。
**Risk if unfixed**: 审计断链；无法溯源恶意操作；与其它 webui 模块不一致。

### L-S-1 422 details 仅 `field`、缺少 `code`
**File**: `server/routes/webui_groups.py:191`
**Dimension**: security
**Issue**: details shape `[{"field": exc.field, "message": str(exc)}]` 与 commands R1 / servers R1 一致，但缺少 per-field machine-readable `code`（如 `name_format`、`name_reserved`）。前端只能字符串比对消息。
**Fix sketch**: 长期可在 `GroupPayloadValidationError` 上加 `code` 字段；本轮可忽略。
**Risk if unfixed**: 前端无法基于 code 做更精细的内联错误展示。

### L-S-2 `_BUILTIN_GROUPS` create 未保护，仅 DELETE 保护
**File**: `server/routes/webui_groups.py:23, 261-317, 377`
**Dimension**: security
**Issue**: 当前 create 流程靠 `Group.name` 唯一约束 + 409 conflict 来防止 `guest` / `default` 重复创建。实际上 `ensure_default_groups()` 启动时已 seed 这两个组（`nextbot/db.py:510`），create 总会撞 unique → 409。功能正确但语义上未显式 reject `_BUILTIN_GROUPS`，错误信息是"身份组已存在"而非"系统内置身份组名称不可用"，与 DELETE 文案不一致。
**Fix sketch**: 在 `_normalize_group_name` 之后、unique 检查之前显式 reject `_BUILTIN_GROUPS`，错误消息 `"系统内置身份组名称不可用"`。
**Risk if unfixed**: 错误消息不一致；语义上 builtin 仅靠 unique 兜底。

---

## 2. Performance

### M-P-1 `webui_groups_list` 全表扫描后内存过滤 / 分页
**File**: `server/routes/webui_groups.py:206-232`
**Dimension**: perf
**Issue**: 列表实现是 `session.query(Group).order_by(Group.name.asc()).all()` 拉全表，然后 Python 端过滤 + 切片。当前 group 数量小，但同样的模式应用到 user 表会爆炸。
**Fix sketch**: 对于 `q` 关键字命中模式，可改为 SQL `LIKE` 过滤 + `OFFSET/LIMIT`。`_build_user_count_map` 当前 OK（SQL GROUP BY），但应加 `User.group IS NOT NULL` 过滤减少桶。
**Risk if unfixed**: 当 group 数破百时延迟开始抬头；非紧急。

### M-P-2 `webui_groups_delete` 全表更新 inherits
**File**: `server/routes/webui_groups.py:405-408`
**Dimension**: perf
**Issue**: 删除时 `all_groups = session.query(Group).all()` 拉全表，然后对每条做 `_remove_inherit` 字符串切片 + 重写。SQLAlchemy 会全量 UPDATE 所有 entity 即使 inherits 字段不含被删 group。
**Fix sketch**: 先 `session.query(Group).filter(Group.inherits.like(f"%{group_name}%")).all()`，再针对真命中条目做精确字符串过滤后写回。
**Risk if unfixed**: O(N) 写放大；小数据量无感。

### M-P-3 前端搜索缺 debounce + AbortController
**File**: `server/webui/static/js/groups.js:543-546`
**Dimension**: perf
**Issue**: `searchInput.addEventListener("input", () => { currentPage = 1; void loadGroups(); });` 每次 keystroke 都立刻发请求。commands R1 / servers R1 都已加 300ms debounce + abort（见 `servers.js:1202-1210`、`commands.js:891-902`），属于已标准化 pattern。
**Fix sketch**: 复用 servers/commands 的 `searchDebounceTimer` + `searchAbortController` 模式，提供 `cancelPendingSearch()` helper，并把所有 `loadGroups()` 调用点改为 `loadGroupsWithAbort()`。
**Risk if unfixed**: 每个字符都发请求；快速搜索时结果 race（旧请求 resolve 后覆盖新请求）。

### M-P-4 翻页 / per-page 切换没有 abort 上一次请求
**File**: `server/webui/static/js/groups.js:548-568`
**Dimension**: perf
**Issue**: `perPageSelect.addEventListener("change", ...)` / `prev/next` 都直接发新请求，没取消在飞的旧请求。慢的旧响应会覆盖新响应。servers/commands 已用 `searchAbortController` 统一处理。
**Fix sketch**: 同 M-P-3，所有触发 `loadGroups` 的入口都过 `loadGroupsWithAbort` helper。
**Risk if unfixed**: 翻页 race 导致界面闪烁 / 显示错误页数据。

### M-P-5 整表全量重渲染（保留决策）
**File**: `server/webui/static/js/groups.js:219-307`
**Dimension**: perf
**Issue**: `renderTable` 每次都把 tbody 清空后从零构建 N 行 + N×2 个 tag-list。与 servers / commands 同形态 —— servers R1 的 B-4 backlog 已说明"renderTable 全量重绘（实际 < 10 台）"。
**Fix sketch**: backlog；保留全量重渲染直至条目数破百。
**Risk if unfixed**: 视觉闪烁；非紧急。

### L-P-1 缺 `beforeunload` abort
**File**: `server/webui/static/js/groups.js` 全文无 `beforeunload`
**Dimension**: perf
**Issue**: commands R2 B-2 已加 `window.addEventListener("beforeunload", () => { cancelPendingSearch(); ... })` 卸载时清理 timer + abort。groups.js 没有。
**Fix sketch**: 同上 helper 加 `beforeunload` 监听。
**Risk if unfixed**: 标签页切换或导航时残留 fetch promise；可忽略但破坏一致性。

---

## 3. UX

### H-3 删除组缺"受影响用户数"二次告警
**File**: `server/webui/static/js/groups.js:365-372`、`server/routes/webui_groups.py:400-403`
**Dimension**: ux
**Issue**: 后端 `webui_groups_delete` 把所有 `User.group == group_name` 的用户**静默** UPDATE 为 `GROUP_DELETE_FALLBACK`（即 `default`）。前端 modal 文案是「确定删除身份组「{name}」吗？此操作不可恢复。」完全没提到 N 个用户会被回退到 default 组。bot 端 `nextbot/plugins/group_manager.py:302` 删除前会主动 `👤 将影响 {affected_user_count} 个用户（回退到 {GROUP_DELETE_FALLBACK}）`。前端体验显著低于 CLI。
**Fix sketch**:
- 后端：list 接口已经返回 `user_count`，前端 modal 已有 `group.user_count`，直接在 confirm 文案带出：`确定删除身份组「${group.name}」吗？${user_count > 0 ? \`当前有 ${user_count} 个用户将回退到 default 组；\` : ""}此操作不可恢复。`
- 或者后端新增 `GET /webui/api/groups/{name}/preview-delete` 显式返回 `{affected_user_count, fallback_group}`。
**Risk if unfixed**: 误删后大量用户权限被回退 → 业务影响大；用户不知情。

### M-U-1 modal 没有 ESC 关闭
**File**: `server/webui/static/js/groups.js:573-607`
**Dimension**: ux
**Issue**: 两个 modal（编辑 / 删除）只有 close button + mask click 关闭，没有 ESC keydown 监听。commands R1 已实现 `registerModalCloser` + 统一 ESC dispatcher（`commands.js:153-184, 936-945`）。servers.js 当前也没有 ESC，属于跨页未标准化的 gap。
**Fix sketch**: 复用 commands.js 的 `registerModalCloser` 模式；或本页加局部 `window.addEventListener("keydown", e => { if (e.key === "Escape" && !modalNode.classList.contains("hidden")) closeModal(); })`。
**Risk if unfixed**: 键盘用户体验差；与 commands 页不一致。

### M-U-2 modal 没有 focus trap
**File**: `server/webui/static/js/groups.js:389-418`
**Dimension**: ux
**Issue**: 打开 modal 后 Tab 键焦点会跳到 modal 外的页面元素（reload / 搜索框等）。commands R1 P2-A 已实现 focus trap（`commands.js:137-150`）。servers 也无 → 属于跨页 gap。
**Fix sketch**: 复用 commands.js 的 `openModalWithFocus` helper（带 Tab cycle handler）。
**Risk if unfixed**: a11y 不达标；屏幕阅读器用户无法限定在 modal 内。

### M-U-3 modal 关闭后未恢复 focus
**File**: `server/webui/static/js/groups.js:358-363, 374-382`
**Dimension**: ux
**Issue**: closeModal / closeDeleteModal 只是 `add("hidden")`，没 restore previousFocus。打开 modal 前的"编辑"/"删除"/"新建" 按钮失去 focus 不会回填。commands R1 / R2 的 P2-A + B-3 已实现 `closeModalAndRestoreFocus` + previousFocus fallback。
**Fix sketch**: 复用 commands.js 的 `openModalWithFocus(modalNode)` 记忆 + close 时 `restoreFocus`。
**Risk if unfixed**: a11y；键盘 / 屏幕阅读器用户失去定位。

### M-U-4 modal 打开未 body scroll lock
**File**: `server/webui/static/js/groups.js:389-418`
**Dimension**: ux
**Issue**: 打开 modal 时背景仍可滚动；commands R2 B-12 已加 `document.body.style.overflow = "hidden"` + 关闭时恢复（`commands.js:178-184`）。
**Fix sketch**: 复用 commands.js 的 `bodyOverflowBeforeModal` 模式。
**Risk if unfixed**: 移动端 modal 打开后背景可滚动；体验劣化。

### M-U-5 缺 `apiReady` 控件 disable 兜底
**File**: `server/webui/static/js/groups.js:39-76, 78`
**Dimension**: ux
**Issue**: `requiredNodesReady` 仅在 DOM 节点缺失时 `return` 早退；但 `api = window.NextBotWebUIApi`（line 78）加载失败时（commands R2 B-9 已加 `apiReady` 检查 + 禁用 6 控件）groups.js 无此兜底。如果 api.js 加载失败，groups.js 的 `api.apiRequest(...)` 会抛 `TypeError: api is undefined` 整页崩溃。
**Fix sketch**: `const apiReady = Boolean(api && typeof api.apiRequest === "function"); if (!apiReady) { setStatus("加载失败，请刷新页面", "error"); reloadButton.disabled = true; addGroupButton.disabled = true; searchInput.disabled = true; perPageSelect.disabled = true; prevPageButton.disabled = true; nextPageButton.disabled = true; return; }`
**Risk if unfixed**: api.js 加载失败时整页 JS 抛错 + 无用户感知。

### M-U-6 错误状态残留：删除失败后 setStatus 不会清除
**File**: `server/webui/static/js/groups.js:519-527, 365-372`
**Dimension**: ux
**Issue**: `confirmDeleteGroup` 失败时 `setStatus(message, "error")` line 527 留在页面顶部；用户重新打开 delete modal 时（`openDeleteModal` line 365-372）虽清空了 modal alert，但顶部 status alert 不会清空 → 用户看到的是"上次的失败错误"叠加在"新弹窗"上。
**Fix sketch**: `openDeleteModal` 入口加 `setStatus("");`，或者关闭 modal 时若是失败状态显式清空 setStatus。
**Risk if unfixed**: 状态污染；用户误以为新操作也失败。

### M-U-7 删除中文案含具体 group name
**File**: `server/webui/static/js/groups.js:509, 511`
**Dimension**: ux + copy
**Issue**:
- line 509 ``setDeleteModalAlert(`正在删除身份组 ${targetGroup.name}...`, "warning")``
- line 511 ``setStatus(`正在删除身份组 ${targetGroup.name}...`, "warning")``

按 CLAUDE.md 规则"**不得包含操作对象名称**"，应改为 `"正在删除…"`。servers.js:752 prior art 用的就是 `setDeleteModalAlert("正在删除…", "warning")` + `setStatus("正在删除…", "warning")`，groups.js 偏离了标准。
**Fix sketch**: 改为 `"正在删除…"`（含中文省略号 `…`，不是 `...`）。
**Risk if unfixed**: 文案规范不一致；与 servers 页面 prior art 偏离。

### M-U-8 modal saving 中 Cancel/Mask 无视觉禁用反馈
**File**: `server/webui/static/js/groups.js:358-363, 374-382`
**Dimension**: ux
**Issue**: `closeModal` 在 `modalSaving` 期间 `return`（line 359-361），但 mask click / Cancel button 没有视觉禁用反馈，用户点击无响应会以为页面卡住。
**Fix sketch**: saving 时给 mask 加 `cursor: not-allowed` + cancel 按钮 `disabled = true`。
**Risk if unfixed**: 用户感知混乱。

### L-U-1 输入预览框初始即显示"无" tag
**File**: `server/webui/templates/groups_content.html:93-106`、`server/webui/static/js/groups.js:163-179`
**Dimension**: ux
**Issue**: `#permission-preview-list` / `#inherit-preview-list` 在输入为空时显示 `tag-badge none` 含文案"无"。首次进入 modal 时该区域已有"无"标签会让用户误以为已经有数据。
**Fix sketch**: 模板初次为空可隐藏 preview 区直到 input 有值；或者文案改为 `"(暂无)"` / 半透明。
**Risk if unfixed**: 轻微视觉混淆。

### L-U-2 modal 没有 `aria-describedby` 关联 modal-alert
**File**: `server/webui/templates/groups_content.html:72, 79-81, 115, 122-124`
**Dimension**: ux / a11y
**Issue**: `aria-labelledby` 指向 title，但 `modal-alert` 的错误消息没有 `aria-describedby` 关联 → 屏幕阅读器不会朗读错误。`modal-alert` 已有 `role="status" aria-live="polite"` 但与 modal 关联弱。
**Fix sketch**: `<div role="dialog" aria-labelledby="group-modal-title" aria-describedby="modal-alert-message">`。
**Risk if unfixed**: a11y 折扣。

### L-U-3 内置组的"编辑"按钮无 hover 提示其全局影响
**File**: `server/webui/static/js/groups.js:272-295`
**Dimension**: ux
**Issue**: 内置组（guest / default）可被编辑 permissions / inherits（by design）。但 UI 上没有任何视觉提示告知用户"编辑会立即生效全局"。
**Fix sketch**: `if (group.builtin) editButton.title = "编辑会立即影响全局默认权限，请谨慎";` 或 modal 顶部加 info banner。
**Risk if unfixed**: 误操作风险（特别 guest 权限）。

---

## 4. Copy

### M-C-1 modalSaveButton "正在保存..." 用 `...` 而非 `…`
**File**: `server/webui/static/js/groups.js:466`
**Dimension**: copy
**Issue**: `setModalAlert("正在保存...", "info")` 使用 ASCII `...`。servers.js:711 / commands.js:490 等 prior art 全部用中文省略号 `…`（U+2026），与 CLAUDE.md 中英混排规范一致。
**Fix sketch**: 改为 `"正在保存…"`。
**Risk if unfixed**: 排版规范不一致。

### M-C-2 删除中文案含"身份组"对象名
**File**: `server/webui/static/js/groups.js:509, 511`
**Dimension**: copy
**Issue**: 见 M-U-7。`正在删除身份组 ${name}...` 同时违反"不得包含操作对象名"+"省略号用 `...`"两条规则。
**Fix sketch**: → `"正在删除…"`。
**Risk if unfixed**: 文案规范偏离。

### M-C-3 表单校验失败前缀手动拼接 / apiRequest 自动拼接 双路径不统一
**File**: `server/webui/static/js/groups.js:458-462, 493-495`
**Dimension**: copy
**Issue**: 表单校验抛错路径手动拼 `${"更新失败" 或 "创建失败"}，${message}`（line 460）；apiRequest 抛错路径 `error.message` 已被 `api.js:238` 自动拼成 `"${action}失败，${reason}"`，前端直接展示。**两路径前缀生成方式不一致**——如果未来表单校验也走 apiRequest，会出现"更新失败，更新失败，..."双前缀。当前**无 bug**，但维护风险存在。
**Fix sketch**: 注释明确：apiRequest catch 块 `error.message` 已含 `{action}失败，` 前缀；表单校验 catch 块手动加前缀。变量名可改为 `prefixedMessage` 提升可读性。或者两路径都不拼前缀，让上层统一 `setModalAlert(\`${actionPrefix}，${reason}\`)`。
**Risk if unfixed**: 维护期 cognitive load；非紧急。

### L-C-1 创建 / 更新成功提示用"成功"单字 ✅
**File**: `server/webui/static/js/groups.js:491, 522`
**Dimension**: copy
**Issue**: `setStatus(isEdit ? "更新成功" : "创建成功", "success")` 与 `setStatus("删除成功", "success")` 与 servers/commands prior art 完全一致，**合规**。仅记录确认。
**Fix sketch**: 无。
**Risk if unfixed**: 无。

### L-C-2 后端 success response 未携带前端展示 message ✅
**File**: `server/routes/webui_groups.py:300-304, 359, 411`
**Dimension**: copy
**Issue**: 成功路径只返回 `data` / 204，没塞"创建成功"/"更新成功"等前端展示文案 → **符合 CLAUDE.md 规范**（成功 message 由前端生成）。
**Fix sketch**: 无。
**Risk if unfixed**: 无。

### L-C-3 后端 error.message 仅含原因不含动作 ✅
**File**: `server/routes/webui_groups.py:280-282, 332-336, 379-384, 390-395`
**Dimension**: copy
**Issue**: 后端 `message="身份组已存在"` / `"身份组不存在"` / `"系统内置身份组不可删除"` 都只表达**原因**，不拼接"创建失败 / 删除失败"，由前端通过 `action` 参数加前缀 → **合规**。
**Fix sketch**: 无。
**Risk if unfixed**: 无。

### L-C-4 modal title 与 confirm dialog 含对象名"身份组" ✅（保留）
**File**: `server/webui/templates/groups_content.html:76, 119`、`server/webui/static/js/groups.js:370, 396, 403`
**Dimension**: copy
**Issue**: modal 标题"创建身份组"/"编辑身份组"/"删除身份组" + delete confirm `确定删除身份组「${name}」吗？此操作不可恢复。` 含对象名"身份组"。根据 CLAUDE.md 严格读应改为"创建"/"编辑"/"删除"+`确定删除「{name}」吗？`。但 servers.js:630 prior art 是 `确定删除服务器「${server.name}」吗？` —— R1 未列入修复，团队对 **dialog 标题 / 确认 dialog 体** 接受含对象名（属于"必要语境"）。**保留与 prior art 一致**。
**Fix sketch**: 无（与 servers prior art 一致优先）。
**Risk if unfixed**: 无。

---

## 5. 跨模块 / scope-out backlog（不计入本任务）

- `html, body { overflow-x: hidden; }`（`groups.css:3`）属于 `app.css` 通用行为应下沉，可能与其它页面 CSS 冲突 → scope-out backlog 一行记录。
- CSRF：webui 全局未启用 CSRF token，仅依赖 SameSite=Lax cookie + 同源 → 与 servers / commands / dashboard R1 决策一致，scope-out backlog。
- `_BUILTIN_GROUPS = ("guest", "default")` 与 `RESERVED_GROUP_NAMES = {"owner", "admin", "root", "system", "superuser"}` 是**两个互不相交**的概念集合：builtin = 已 seed 的可编辑组，reserved = 禁止创建的保留名。

---

## 建议修复顺序

1. **H-1 RESERVED_GROUP_NAMES** —— 后端单点改动，立刻消除 webui / CLI 行为割裂。
2. **H-3 删除二次告警** —— 后端 list 已有 user_count，前端 modal 文案带出即可，0 后端改动。
3. **M-S-1 / M-S-2 / M-S-5** 一并改：日志 sanitize + path 校验 + client_ip 日志，三处共一个 logger helper。
4. **M-P-3 / M-P-4 / M-U-1~M-U-6** 一并改：复用 commands.js 的 `cancelPendingSearch` / `openModalWithFocus` / `closeModalAndRestoreFocus` / `registerModalCloser` / body scroll lock / apiReady。
5. **M-C-1 / M-C-2 / M-U-7** 一并改：文案 `…` 替换 + 删除中文案去对象名。
6. 其余 Low / 排除项 backlog。

## Caveats / Not Found

- 未发现 Critical 级问题（无 RCE / auth bypass / 明文凭证泄漏）。
- 后端 auth 由 `add_webui_auth_middleware`（`server/routes/webui.py:195-226`）统一拦截 `/webui/api/groups*`，无 endpoint 漏配。
- CSS 文件（`groups.css` 404 LOC）通读后无安全 / 性能 / a11y 致命问题。
- 未检查 i18n（项目暂无 i18n 框架，所有文案硬编码中文，按 prior art 接受）。
- 后端 SQL 注入风险：所有查询均通过 SQLAlchemy ORM 参数化，无 raw SQL / string concat，安全。
- 前端 XSS 风险：所有用户输入展示均通过 `textContent`（`groups.js:170, 177, 191, 242, 261, 370` 等），未发现 `innerHTML` / `insertAdjacentHTML` 写入用户内容的位置。整体安全。
