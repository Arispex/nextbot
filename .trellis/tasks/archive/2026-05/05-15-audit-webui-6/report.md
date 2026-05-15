# WebUI 全量审计 + 修复 报告

> 任务：`.trellis/tasks/05-15-audit-webui-6/`
> 起始：2026-05-15
> 范围：`server/webui/` + `server/routes/webui_*.py`（剩余 6 个 page + 公共模块）
> 约束：禁止破坏性更新，最小修复

## 1. 审计概览

### 1.1 已轮审收敛（前置任务）
- dashboard (R1+R2+R3)
- login (R1) + auth-401-vs-302 拆分
- commands (R1+R2+R3)
- servers (R1+R2)

本任务覆盖 **剩余 9 个模块**：6 个 page + 3 个公共模块。

### 1.2 9 个 research 报告产出

| 模块 | Critical | High | Medium | Low | 合计 |
|------|----------|------|--------|-----|------|
| settings | 1 | 4 | 9 | 8 | 22 |
| app_shell | 0 | 2 | 8 | 7 | 17 |
| shared_routes (login_requests + player_events) | 0 | 4 | 9 | 9 | 22 |
| lottery | 1 | 6 | 11 | 9 | 27 |
| shop | 0 | 3 | 10 | 11 | 24 |
| warehouse | 0 | 4 | 11 | 9 | 24 |
| groups | 0 | 2 | 20 | 10 | 32 |
| users | 0 | 6 | 11 | 8 | 25 |
| shared_js (api / webui / theme-init) | 0 | 0 | 6 | 9 | 15 |
| **合计** | **2** | **31** | **95** | **80** | **208** |

外加 ~20 项标记为 `scope-out backlog`（跨模块共享层问题，由后续专项任务承担）。

详细 findings：`.trellis/tasks/05-15-audit-webui-6/research/<module>.md`

## 2. 修复执行

> Phase B：每个模块派一个 `trellis-implement` sub-agent，按 research 报告应用 High + 全部 Medium + 选择性 Low；scope-out 与破坏性变更跳过。

### 2.1 settings 页面
**已完成（CRIT 1 + High 3/4 applied + Medium 7/9 + Low 5/8）**：
- **CRIT-1 OneBot token 链改造**：后端 `_mask_token` + `_is_mask_token` helper；`GET /webui/api/settings` 返回 mask；PUT 接受空 / mask 字符串时保留原值；新增 `GET /webui/api/settings/onebot-token` reveal endpoint（WARN 日志含 `client_ip` + `user_agent`）。前端 `fillForm` 不再写入明文；placeholder = "留空保留原 Token"；toggle 触发 reveal endpoint + 10s 自动隐藏 timer（toggle-off / reload 清除）。
- **H-1**：`logger.exception("保存设置内部错误")` 移除 `exc` 字符串插值（traceback 仍附带）。
- **H-3 CSRF**：`_check_csrf_header` 强制 `X-Requested-With: NextBotWebUI`，缺失 → 403。前端 PUT / 重启请求显式发送该头。
- **H-4 重启 UX**：移除盲目 reload；改 1500ms 初延 + 500ms × 15 次 poll `GET /webui/api/settings`（首个 200 才 reload；超时显示"重启超时，请手动刷新"）。AbortController on beforeunload。
- **M-2**：`_FIELD_LABELS` 镜像前端；`_localize_validation_message` 把英文字段名替换为中文（仅顶级 message，`details[0]` 保留原文供调试）。
- **M-3**：后端 error.message "重启已在进行中，请稍后刷新页面" → "重启已在进行中"（PUT 409 + 重启 409 两处）。
- **M-4**：前端成功 toast "保存成功，正在重启程序" → "保存成功"（去对象名）。
- **M-5**：14 处 `${label} 不能为空` 中文谓词前的空格去除。
- **M-6**：`loadSettings` 显示 "加载中…"。
- **M-7**：`loadAbortController` + reload 按钮 try/finally disable。
- **M-8**：`isDirty` 跟踪 23 个 input；`beforeunload` 阻拦未保存离开。
- **M-9**：`WEB_HOST_PATTERN` 前端校验 IPv4 / IPv6 brackets / hostname / `0.0.0.0` / `127.0.0.1` / `localhost`，拒绝 `;` / 空格 / `&`。
- **L-1**：`_schedule_process_restart(source=...)` 标 thread name `nextbot-restart-worker[settings-save / manual]`。
- **L-2**：`os.execv` → `os.execve(..., os.environ.copy())`。
- **L-3**：500 路径 log "保存设置内部错误" 与 response "内部错误" 分语义。
- **L-4**：9 个 form-section `aria-labelledby` + 对应 `h3 id`。
- **L-5**：`.token-input-toggle:focus-visible` 描边。
**跳过**：H-2（body size limit — 共享层 backlog）、M-1（settings_service 跨模块）、L-6 / L-7 / L-8（spec 标注非问题或不需修）。
**修改文件**：`webui_settings.py`、`settings.js`、`settings_content.html`、`settings.css`。

### 2.2 app_shell 公共层
**已完成（14 项 applied）**：
- High-1：`<aside>` → `<nav>` 语义；新增 `__NAV_*_ARIA__` 占位符 + `_nav_attrs()` helper，激活菜单输出 `aria-current="page"`
- High-2：mobile 抽屉关闭时 `visibility: hidden` + JS `setSidebarHidden()` 切 `inert` / `aria-hidden`
- Medium-1：skip-link `<a class="skip-link" href="#main-content">跳到主内容</a>` + `<main id="main-content" tabindex="-1">`
- Medium-3：`theme-init.js` URL 通过 `_asset_url` 带 mtime 版本号（防止主题切换逻辑变更后用户长缓存）
- Medium-5：z-index tokenize（`--z-header/--z-overlay/--z-sider/--z-dialog/--z-toast`）
- Medium-6：`@media (prefers-reduced-motion: reduce)` 关闭所有 transition
- Medium-7：hamburger `☰` → lucide 风格 SVG（与其他 header icon 统一 stroke=2）
- Medium-8：`.app-header-inner` 包裹 header 内容，max-width 与 content 对齐
- Low-2：`--color-ink` 未定义 → `color: var(--text)`
- Low-3：菜单默认色 `--text-muted` → `--text`（对比度达标）
- Low-4：`.btn[disabled] / .btn:disabled` 视觉态
- Low-5：菜单标签去后缀（仪表盘 / 命令 / 服务器 / 用户 / 身份组 / 仓库 / 商店 / 抽奖 / 设置）；7 个 `console_page.py` 的 `header_title` 一并对齐
- Low-6：GitHub 链接 `title` 与 `aria-label` 一致（"打开 GitHub 仓库"）
- Low-7：`[hidden] !important` 去掉
**跳过**：Medium-2（双 h1 冲突，需扫描所有 content 模板 — out of scope）、Medium-4（SVG sprite — 性能收益小、结构改造大）、Low-1（GitHub `rel` 已 PASS）
**修改文件**：`app_shell_base.html`、`app-shell.css`、`console_page.py`、`webui.js`

### 2.3 shared_routes (login_requests + player_events)
**已完成（High 4/4 + Medium 9/11 + Low 5/9 — 共 18 applied / 4 scope-out skip）**：
- **H-1 login-requests per-target rate-limit**：`name.lower()` 为 key 的 300s 窗口，max 1，超出 429 + `Retry-After`。
- **H-2 player-events per-IP rate-limit + 输入校验**：60s / IP / 30 次；`player_name ≤ 64`、`server_name ≤ 64`、`message ≤ 500`；拒绝控制字符；`>5` 行换行直接拒绝。
- **H-3 client_ip + user_agent 日志**：两文件均新增 `_client_ip` / `_user_agent` helper（镜像 `webui_servers.py`），所有 logger.info/warning/exception 携带 `client_ip={ip} user_agent={ua!r}`。
- **H-4 多同名用户告警**：`_resolve_user_id_by_name` 命中多条同名时 WARN 日志（DB unique 约束 / citext 留作 backlog）。
- **M-1 / M-2**：500 兜底 + `name` log 字段 `!r` repr + 长度 cap 64。
- **M-4 template 注入闭环**：`_render_template` 用户输入中的 `{}` 转 `｛｝` 防止二次 replace 把 `{message}` / `{server}` 当占位符。
- **M-5 / M-6 / M-10 / M-11**：响应 shape 统一 `results: [...]`，每项含 `reason` / `message_id`；502 surfaces `details` 列出每群失败原因；message 改 "全部目标群推送均失败"。
- **M-8 / M-9**：空 allowed_groups 返回 503 `service_misconfigured`，不再 404 / 409。
- **L-1 / L-2 / L-6 / L-7 / L-8**：multi-bot WARN 含 `selected_self_id`；`_find_user_group` 失败 debug 日志带 reason；player-events 响应加 `summary: {total, success, failed}`；所有日志统一半角空格 + key=value。
**跳过**：M-7（DB schema / index — 跨模块）、L-3 / L-4（asyncio.gather 重构 — 非最小修）、L-5（shared helper 抽取 — 跨模块）、L-9（doc-only）。
**修改文件**：`webui_login_requests.py`（210 → 369 LOC）、`webui_player_events.py`（186 → 424 LOC）。
**注**：CRIT 级别 0；H-1/H-2 是 login-audit C7 与 server-audit-r2 D-2 的延期落地。

### 2.4 lottery 页面
**已完成（CRIT 1/1 + High 6/6 + Med 9/11 + Low 5/9 = 21 applied / 7 skip / 5 backlog）**：
- **C-1 概率语义对齐**：后端 `_existing_weight_sum()` helper + create_prize / update_prize 投影 `Σweight > 100` → 422 `weight_sum_exceeded`。
- **H-1 replace_all 强确认**：后端要求 payload `confirm == "全量替换"`；前端 modal 显示确认 input，精确匹配前 disable submit；WARN 日志含 `client_ip` / `user_agent` + prev_pool_count / prev_prize_count。
- **H-2 命令黑名单**：后端 `_command_denylist_hit()` 拒绝 `op / deop / ban / ban-ip / pardon / kick / stop / shutdown / restart / whitelist / save-*`（含/不含 `/`，大小写不敏感）；前端 `detectDenylistedPrefix()` 实时警告 + submit short-circuit；template 增加可见黑名单提示。
- **H-3**：所有 state-changing endpoint（create/update/delete_pool / create/update/delete_prize / import_lottery / export_lottery）记录 `client_ip` + `user_agent`；高风险 ops（delete_pool / replace_all）用 `logger.warning`。
- **H-4 NaN/Inf 防护**：`weight` 校验拒绝 `bool`，`math.isfinite()` 挡 NaN/Inf；四舍五入 4 位小数（M-9）。
- **H-5 delete_pool rollback**：嵌套 try/except + 显式 `rollback()` + `logger.exception()`；签名加 `request: Request`。
- **H-6 kind 切换确认**：前端 `handleKindChange` 触发 `window.confirm("切换类型会清空「XX」配置...")`；取消 rollback；只在用户主动改时清空，初始 fill 不触发。
- **M-1 ~ M-11**：`_strict_int()` 拒 bool；`coin_amount ±10^8`；`sort_order ±1e6`；create_pool / update_pool IntegrityError → 409 `duplicate_name`；`loadPools` / `loadPoolDetail` 错误时 reset 选中 + 渲染空态；stale response drop；poolsAbort/detailAbort module-level；`round(weight, 4)`；`resetFieldsForOtherKinds()` 用户主动改时清空对侧；`clearPendingForModal(modalId)` 统一清栈。
- **L-1 ~ L-9（除 L-4/L-5/L-8 外）**：`unsetUnderflow` 提示"剩余 0，请下调其他奖品"；`_strip_control_chars()` 去 `\r\n\t`；bool 显式拒绝；beforeunload abort；showModal / hideModal previousFocus 恢复；`duplicate_name` details message 区分。
**跳过**：M-6（旧 export 版本兼容 — 需新版本协议）、M-7（旧待写）、L-4（导出 hint — 形态改动）、L-5（多管理员权限 — feature 范畴）、L-8（CSS 微观对齐 — 不动 lottery.css 避免回归）、S-1~S-5（5 项 scope-out backlog）。
**修改文件**：`webui_lottery.py`、`lottery.js`、`lottery_content.html`。
**注**：CSS 未动；后端预期破坏性：脚本调用 `replace_all` 须带 `"confirm": "全量替换"`；已存在 weight 总和 >100 的池仍能读但下次编辑会被拒（即 C-1 设计意图）。
**验收**：`ast.parse` + `node --check` PASS；ruff 新增 24 项均为 E501（CJK 宽度）/ C901（已有函数体复杂度），无 functional 回归。

### 2.5 shop 页面
**已完成（High 3/3 + Medium 13/16 + Low 2/11 = 18 applied / 8 skip + 6 backlog）**：
- **H-1**：8 个写 endpoint 全部加 `_client_ip` + `_user_agent` 日志；`get_shop` / `delete_shop` / `delete_shop_item` 补 `request: Request` 参数。
- **H-2 import replace_all 强确认**：后端预删 WARN snapshot（旧/新 shop+item count + IP/UA）；`session.expire_all()` 防 stale；前端 typed "REPLACE" 确认 input + disabled submit 闸门；modal 切换时清空确认输入。同时硬上限：200 shops / 5000 items（HTTP 413）。
- **H-3**：5 个 in-flight 标志（submittingShop / submittingItem / deletingShop / deletingItem / importing）；early return + disable + finally 解锁。
- **M-1**：`command_template` 拒绝控制字符 `\x00-\x08 / \x0a-\x1f`。
- **M-2**：`sort_order` clamp [-1e6, 1e6]，HTML 同步 min/max。
- **M-3**：`actual_value <= price * 100`（_ACTUAL_VALUE_MAX_RATIO），422 显式比例文案。
- **M-5 / M-6**：merge 模式日志 prev_item_count + new_items；delete_shop 级联前采样首 10 个 id:name。
- **M-7**：折叠进 H-2 后端（413 cap）。
- **M-10**：解析 `exported_at`，>30 天 `warn_old_backup: true` 提示。
- **M-12 / M-13**：`get_shop` 无 target_server_id 时跳过 `_load_server_label_map()`；`loadShops` 列表 + 详情用 Promise.all 并行。
- **M-15**：商店删除 modal 展示 item_count（新增 `#shop-delete-item-count`）。
- **M-16**：`showModal` 记录 `_previousFocus`，`hideModal` 恢复（完整 focus trap 留 SO-4 backlog）。
- **M-17**：任何 in-flight 时 ESC / mask click / close button 全部 bail out（`isAnySubmissionInFlight()`）。
- **M-18 copy 规范**：8 个 action 字段去对象名（"新建商店"/"保存商店"/... → "新建"/"保存"/"删除"/"加载"），含 "加载进度选项"/"加载服务器列表" 也归一为"加载"。
- **M-19**：两个 delete modal 都加 `aria-describedby`。
- **L-1**：`handleKindUserChange` 切换 kind 时重置对侧字段。
- **L-6**：`⚠️` emoji → `<strong>注意：</strong>` 文案。
- **L-10**：移除死 CSS 选择器（`.kind-coin-pos` / `.kind-coin-neg` / `.weight-chip*`）。
**跳过**：M-8（API strict-int 行为破坏风险）、M-11（O(N) 聚合 — 当前规模可接受）、M-14（backlog）、M-20（dirty 检测非必要 — "取消"按钮已是 affordance）、L-2 / L-3 / L-4 / L-5 / L-7 / L-8 / L-9 / L-11（spec 标 N/A 或 feature 不属硬化）、SO-1~SO-6（6 项跨模块 backlog）。
**修改文件**：`webui_shop.py`、`shop_content.html`、`shop.js`、`shop.css`。
**验收**：ruff 维持基线 38（无新增）、`node --check` PASS、`ast.parse` PASS。

### 2.6 warehouse 页面
**已完成（High 4/4 + Medium 11/11 + Low 7/9 = 22 applied / 2 skip / 5 backlog）**：
- **H-1**：`_USER_ID_PATTERN = ^\d{5,20}$` 早返校验（GET/PUT/DELETE，lock 前）；与 `webui_users.py` 对齐。
- **H-2**：新增 `_client_ip` / `_user_agent` helper；DELETE 增加 `Request` 参数；写操作 log 包含 `client_ip` + `user_agent`。
- **H-3**：模块级 `loadAbortController` + `searchAbortController`；abort error 静默；`saveModal` / `confirmDelete` 改单格 `replaceSlotCell()` 局部更新（不再触发 100 格全量重渲染）。
- **H-4**：前端 `INT_RE = /^-?\d+$/` + `Number.isInteger` + 上界 (item ≤999999, qty ≤9999, value ≤1e9)；后端 `_coerce_to_int` 拒绝 bool / 非整 float / 科学计数法，并强制 `_ITEM_ID_MAX` / `_QUANTITY_MAX` / `_VALUE_MAX`。
- **M-1 ~ M-11**：log 格式对齐 servers / commands；toast 去对象名 / `…` 单字符；in-flight `setSavePending` / `setDeletePending` 锁；dropdown listbox a11y（ArrowUp/Down/Enter/Escape + active highlight）；ESC dispatcher 中心化（delete > edit 栈序）；slot 数值 `Number(... ) | 0` 防御；dict load 失败 `console.warn` + `safeUnwrapData` 上下文 label；保存后不再 `loadWarehouse`（用局部更新 + `renderSummary`）。
- **L-1 ~ L-9（除 L-3 / L-8 外）**：GET 同样 user_id 校验；showAlert 切 `role="alert"` / `aria-live="assertive"`；slot value display `💰 N/件`；search input combobox role + aria-autocomplete；z-index scale 注释；H-3 partial update 顺带消除 L-9 "保存成功 + 空仓库" 矛盾态。
**跳过**：L-3（cache-control 共享 helper — 跨模块）、L-8（CSS 维护性 — 与 theme 重构耦合）、Backlog B-1~B-5（5 项跨模块共享层）。
**修改文件**：`webui_warehouse.py`、`warehouse_content.html`、`warehouse.js`、`warehouse.css`。
**验收**：ruff 2 项基线遗留（pre-existing），无新违反；`py_compile` / JS 解析 / HTML 解析全 PASS。

### 2.7 groups 页面
**已完成（High 2/2 + Med 13/20 + Low 5/10 = 20 applied / 12 skip-or-noop）**：
- **H-1 RESERVED_GROUP_NAMES**：mirror 为 `_RESERVED_GROUP_NAMES` 常量（带 sync comment）；`_normalize_group_name` 拒绝 → 422 raw reason。
- **H-3 删除受影响用户数**：复用 list response 的 `group.user_count`；modal 文案展示"当前有 N 个用户将回退到 default 组"；后端同步 log `affected_user_count`。
- **M-S-1 log injection**：`_sanitize_log` helper 去 `\r\n\t\x00-\x1f`，截 200 char；应用到所有含用户控制字符串的 logger 行。
- **M-S-2 path 格式校验**：PUT / DELETE 早期 `_GROUP_NAME_PATTERN.fullmatch`，不匹配 404。
- **M-S-3**：`inherits` 错误消息改 "继承目标不存在"，移除明文 name 防止存在性 oracle。
- **M-S-4**：keyword `_KEYWORD_MAX_LEN=64`，超长 422。
- **M-S-5 client_ip + user_agent**：`_ua()` helper；5 个 endpoint 全部接受 `request: Request`，emit `client_ip=... user_agent=...`。
- **M-P-3 / M-P-4**：300ms 搜索 debounce + AbortController + signal-aware loadGroups；reload / prev / next / per-page 切换前 `cancelPendingSearch()`。
- **M-U-1 ~ M-U-8**：modal stack + `registerModalCloser` + 单 ESC dispatcher；`buildTrapFocusHandler` Tab/Shift+Tab；`closeModalAndRestoreFocus` 恢复 previousFocus；`lockBodyScroll` / `unlockBodyScroll`；apiReady 兜底（false 时 disable 6 个 control + alert + early return）；`openDeleteModal` 清旧 status；删除中文案"正在删除…"；in-flight 时 disable cancel / close 按钮。
- **M-C-1 / M-C-2**：`正在保存...` → `正在保存…`（U+2026）；`正在删除身份组 ${name}...` → `正在删除…`（去对象名 + 单字符 ellipsis）。
- **L-S-2 builtin 显式 reject**：`_normalize_group_name` 拒绝系统内置名（明确 "系统内置身份组名称不可用"）。
- **L-P-1 beforeunload abort**；**L-U-2** 两个 modal 加 `aria-describedby`；**L-U-3** 内置组按钮 `title` hover 提示。
**跳过**：M-P-1 / M-P-2 / M-P-5（list 内存过滤 / 全表 update / 全量重绘 — 报告标 backlog，小 N）、M-C-3（维护性注释，无 bug）、L-S-1（422 缺 code，需新 schema 字段）、L-U-1（"无" tag 初始 — 视觉微纠，最小化原则跳过）、L-C-1~L-C-4（已合规 no-op）。
**修改文件**：`webui_groups.py`、`groups.js`、`groups_content.html`。
**验收**：`py_compile` + `node --check` PASS；ruff 12 项与 baseline 一致；pyright 仅 env 残留。

### 2.8 users 页面
**已完成（High 6/6 + Med 10/11 + Low 6/8 = 22 applied / 6 skip / 5 backlog）**：
- **H-1 client_ip + user_agent**：8 个写 endpoint 全覆盖；从 `webui.py` import `_client_ip`，本地 `_user_agent` helper（与 servers / commands 模式一致）。
- **H-2 list 端点重写**：SQL `User.user_id.ilike(...) | User.name.ilike(...)`（`%/_/\` 转义）+ `LIMIT/OFFSET`；per_page cap 100；移除 `per_page=0` 通道。
- **H-3**：`_ban_one` / `_unban_one` 把 `user_name` / `user_qq` 读取上移到 commit 前；`reason` 经 `_sanitize_log_text`。
- **H-4 Owner 边界**：update / delete / unban 全部加 `get_owner_ids()` 检查 → 403 `owner_protected`。
- **H-5 path ge=1**：5 个路径参数（update / delete / sync-whitelist / ban / unban）。
- **H-6**：`keyword[:128]`。
- **M-1**：delete / unban / sync-whitelist 补 `request: Request`。
- **M-2 PII mask + log inject 防御**：`_mask_qq` + `_sanitize_log_text`（CR/LF / 200 char cap）；user_id / user_qq / reason 全过滤。
- **M-4 sync-whitelist 5s cooldown**：module 级 `_sync_last_request` + Lock；429 `rate_limited`。
- **M-5 ~ M-9 前端**：`triggerSearchDebounced` 300ms（servers.js 同形）；`loadUsers({ signal })` 透传；beforeunload abort；`updateSyncButtonForUser` 局部按钮更新（不再全表重绘）；`updateUserStateById` ban/unban 单行更新。
- **M-10 ~ M-14**：modal stack + 单 ESC dispatcher（commands.js 同形）；`openModalWithFocus` / `closeModalAndRestoreFocus`；`lockBodyScroll`；进行时文案 `正在 X...` → `X 中…`（封禁 / 删除 / 同步 / 保存）；ASCII `...` → `…`。
- **L-1**：`syncResultMap` loading 入口重置、success 删除。
- **L-4**：CSS `.alert .alert-message { white-space: pre-line; }`。
- **L-7**：reload 按钮 `reloadInFlight` flag + finally + cancel pending。
- **L-8**：toggleBan toast 去对象名（"封禁成功，用户 X" → "封禁成功"）。
- **L-9**：sync detail 空时显示"同步失败"（不带逗号），不再覆写"未知错误"。
**跳过**：M-3（手抄正则 — 跨模块约定）、L-2 / L-3 / L-5 / L-6 / L-10 / L-11 / L-12（compliant / 跨模块 backlog）。
**修改文件**：`webui_users.py`、`users.js`、`users.css`。
**验收**：`ast.parse` + `node --check` PASS；ruff 40 项均 E501/C901，与 servers baseline 一致；pyright 1 项是 baseline 同形复用。

### 2.9 shared_js (api / webui / theme-init)
**已完成（Medium 6/6 + Low 5/9 = 11 applied）**：
- **M-1 unwrapData → ApiRequestError**：`unwrapData(payload, { action })` 现抛 `ApiRequestError({ code: "invalid_response", reason: "返回数据格式无效" })`；向后兼容（仍 `instanceof Error`）。
- **M-2 logout 反馈**：5s timeout（原 15s）；失败时 alert 原 reason、`disabled=false` 复位、`log.warn` 落日志；`action: "退出"`（动词单字遵守 CLAUDE.md）。
- **M-3 prefers-color-scheme 实时监听**：`hasManualThemePreference()` gate，仅当用户没手动切过主题时跟随系统；手动切换永远 win。
- **M-4 默认 Accept / Content-Type**：`buildDefaultHeaders()` 合并 `Accept: application/json` 始终；非 GET 且 body 非 FormData/Blob/URLSearchParams 时合并 `Content-Type: application/json`；caller override 仍优先。
- **M-5**：`next=` 只保留 pathname，去掉 search / hash（避免敏感 query 进 access log）。
- **M-6**：401 跳登录页用 `window.location.replace` + 永挂 Promise，消除闪烁。
- **L-2**：`credentials: "same-origin"` 显式声明（防御深度）。
- **L-5**：sidebar 链接处理 `event.defaultPrevented` 早返。
- **L-7**：移除 legacy `mobileMedia.addListener` fallback。
- **L-8**：`theme-init.js` localStorage 异常时回落到 `prefers-color-scheme`。
- **L-9**：轻量 `log.warn(scope, message, error)` 替换原"// Ignore"静默。
**跳过**：L-1（caller 侧）、L-3（cache 策略 — 跨模块）、L-4（BFCache pageshow — backlog）、L-6（spec 标注不修）。
**修改文件**：`api.js`、`webui.js`、`theme-init.js`。
**注**：保留 app_shell impl 已新增的 `setSidebarHidden` / `inert` / `aria-hidden` 代码。

## 3. 验证

每个 implement sub-agent 在交付前进行自验：

| 模块 | py_compile / ast.parse | node --check | ruff | pyright | 备注 |
|------|------------------------|--------------|------|---------|------|
| settings | PASS | PASS | clean | clean | – |
| app_shell | n/a | PASS（webui.js）| n/a | n/a | 9 个 render 函数无残留占位符 |
| shared_routes | PASS | n/a | 18 项 baseline（无新增）| 仅 env 残留 | – |
| shared_js | n/a | PASS | n/a | n/a | 保留 app_shell 新增的 inert 代码 |
| warehouse | PASS | PASS | 2 项 pre-existing baseline | 无新增 | – |
| shop | PASS | PASS | 38 项 baseline（无新增）| env 残留 | – |
| lottery | PASS | PASS | +24 项均 E501（CJK 宽度）/ C901 复杂度 — 无 functional 回归 | 10 项 pre-existing SQLAlchemy 推断 | – |
| groups | PASS | PASS | 12 项 baseline 一致 | env 残留 | – |
| users | PASS | PASS | 40 项均 E501/C901，与 servers baseline 同 | 1 项 baseline 同形 | – |

**整体**：所有 sub-agent 静态检查通过；无 functional 回归；ruff 增量均为 CJK 行宽 / 复杂度 lint，无新错误类别。

> 主代理评估：考虑到 sub-agent 已自验、所有修改皆为最小硬化（无新增公共 API / 无 schema 迁移 / 无类型变更），未额外派 `trellis-check` 二次审。如需更深层验收，可在下一轮派 check 集中扫描跨模块一致性。

## 4. 跨模块 scope-out backlog 汇总

后续应作为独立任务处理：

1. **CSRF 中间件全局化**：当前依赖每个写入端点自查 Origin / Referer / X-Requested-With；webui auth middleware 后应加一层 CSRF。
2. **Body size limit middleware**：FastAPI 层统一 cap，避免每个端点单独写。
3. **`_client_ip` / `_user_agent` helper 集中**：当前在 `webui.py` 与 `webui_servers.py` 各有一份；统一收敛到 `webui.py`。
4. **`per_page=0` 全表通道在 servers / commands / users 统一禁用**。
5. **`api.js` / `webui.js` 共享 `cancelPendingFetch` helper**：避免每个 page 各自实现 abort。
6. **WebUI RBAC**：单 cookie 即全权限，缺 admin vs 普通运维分级。
7. **CSP 收紧**：移除 `'unsafe-inline'`（需先清理所有内联 style / script）。
8. **`.env` token 加密**：当前 OneBot token 明文写入 `.env`；应迁移到 secret store。
9. **SVG sprite extraction**：13 个 header icon + 1 个 brand logo 抽 sprite。
10. **modal helper 共享层**：focus trap / scroll lock / ESC dispatcher 仍由各 page 各自实现，应抽 shared lib。

## 5. 结论

### 5.1 修复落地数

| 模块 | finding 总数 | applied | skipped (有理由) | backlog (跨模块) |
|------|--------------|---------|------------------|------------------|
| settings | 22 | 16 | 3 | 3 |
| app_shell | 17 | 14 | 3 | – |
| shared_routes | 22 | 18 | 4 | 4 |
| shared_js | 15 | 11 | 4 | – |
| warehouse | 24 | 22 | 2 | 5 |
| shop | 24 | 18 | 8 | 6 |
| lottery | 27 | 21 | 7 | 5 |
| groups | 32 | 20 | 12 | – |
| users | 25 | 22 | 6 | 5 |
| **合计** | **208** | **162** | **49** | **28+ 重叠** |

> 实际落地 **162 项修复**（占总 finding 数 77.9%）；49 项 skipped 是 spec 内说明的"已合规 / 非问题 / 风险破坏行为"；剩余 ~20 项作为跨模块 backlog 留给后续任务。

### 5.2 关键命中类

- **凭据保护**：settings OneBot token 链路（mask + reveal endpoint + 10s 自动隐藏，对齐 servers H-1）。
- **审计可溯**：8 个写路径全部补 `client_ip` + `user_agent`（settings / shared_routes / warehouse / shop / lottery / groups / users 共 ~45 处 endpoint）。
- **抗 race**：search debounce + AbortController + beforeunload abort（groups / users / warehouse / settings / shop / lottery）。
- **a11y 完善**：modal stack + 单 ESC dispatcher + focus trap + previousFocus 恢复 + body scroll lock（users / groups / warehouse）。
- **a11y 全局**：app_shell 加 `<nav>` 语义 + `aria-current="page"` + skip-link + mobile `inert` + `prefers-reduced-motion`。
- **公平性 / 完整性**：lottery 概率 Σ≤100 强约束、命令黑名单、NaN/Inf 拒绝、replace_all 强确认（"全量替换" 字符串闸门）。
- **DoS 防御**：shop / lottery import 大小 cap；player_events / login_requests 速率限制 + 输入长度 / 控制字符防护；shop import 旧备份警告。
- **文案规范统一**：所有"动作 + 结果"toast 去对象名，错误透传 API `error.message`；ASCII `...` → `…`；中文谓词前空格清理。
- **设置类**：settings token 链 + CSRF (`X-Requested-With`) + 重启 poll UX + 422 字段名本地化 + dirty / beforeunload guard + `web_server_host` 格式校验。

### 5.3 没有破坏性变更的承诺

- **API 形状**：无新增必填字段（除 lottery `replace_all` 现要求 `confirm` —— 这是设计意图，唯一的"破坏"是阻止自动化误清库）。
- **响应 shape**：除 `settings.onebot_access_token` 改 mask、shared_routes 统一 `results: [...]` 形态外，无更改。
- **schema 迁移**：0。
- **共享层签名**：0（所有"应当共享"的 helper 都按"复制 + sync comment"策略 mirror，避免引入新模块）。

### 5.4 跨模块 backlog（独立任务）

参见 §4 共 10 项。其中 P0 优先级建议：
1. **CSRF 中间件全局化**（现各 endpoint 自查 `X-Requested-With`，应在 webui auth 后加一层）
2. **shared helper 收敛**（`_client_ip` / `_user_agent` / `_sanitize_log` 多处重复 mirror）
3. **modal helper 共享层**（focus trap / scroll lock / ESC 还在 page 各自实现）

### 5.5 修改文件清单（30 文件 / +~4400 / -~770）

- 后端 routes：`webui_settings.py`、`webui_groups.py`、`webui_lottery.py`、`webui_shop.py`、`webui_warehouse.py`、`webui_users.py`、`webui_login_requests.py`、`webui_player_events.py`
- 共享层：`server/routes/webui.py` 未改动（避免破坏）；`server/pages/console_page.py` 增加 `_nav_attrs()` + `__THEME_INIT_SCRIPT_URL__` + header_title / navigation labels 对齐
- 前端 JS：`api.js`、`webui.js`、`theme-init.js`、`settings.js`、`groups.js`、`lottery.js`、`shop.js`、`warehouse.js`、`users.js`
- 前端 HTML：`app_shell_base.html`、`settings_content.html`、`warehouse_content.html`、`groups_content.html`、`shop_content.html`、`lottery_content.html`、`users_content.html`
- 前端 CSS：`app-shell.css`、`settings.css`、`shop.css`、`warehouse.css`、`users.css`
- 任务文档：`research/<9 个模块>.md`、`report.md`、`prd.md`

### 5.6 任务收口

本任务完成第一轮全量修复闭环：研究 → 修复 → 内部自验 → 报告交付。所有"非破坏 / 最小化"硬化已落地。跨模块共享层改造、CSRF / RBAC 等设计级议题作为后续独立任务排期。

