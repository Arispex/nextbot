# Research: WebUI 设置页面安全 / 性能 / UX / 文案审计

- **Query**: 全量审计 WebUI 设置页面 4 文件
- **Scope**: internal（严格限定 4 文件）
- **Date**: 2026-05-15

## 审计范围

- `server/routes/webui_settings.py` (121 LOC)
- `server/webui/templates/settings_content.html` (248 LOC)
- `server/webui/static/js/settings.js` (436 LOC)
- `server/webui/static/css/settings.css` (229 LOC)

参考标准化模式：servers R2 (`1355521`) / commands R3 (`f512c8c`)。

---

## Findings 汇总

| 严重度 | 数量 |
|---|---|
| Critical | 1 |
| High | 4 |
| Medium | 9 |
| Low | 8 |
| **合计** | **22** |

---

## Critical

### CRIT-1 GET /webui/api/settings 明文返回 OneBot access token

**File**: `server/routes/webui_settings.py`:59-64；`server/settings_service.py`:397-410（scope-out 引用）；`server/webui/static/js/settings.js`:329, 364-379

**Dimension**: security

**Issue**: `get_settings_snapshot()` 在 `onebot_access_token` 上没有任何 mask，`/webui/api/settings` 直接把明文 token 写进响应 `data.onebot_access_token`；前端 `fillForm` (line 329) 又把它塞进 `<input type="password">`。结果是：
1. 任何已登录会话只要 GET 一次该端点即可拿到完整 token（与 servers 页面 R1 修复的 H-1 "token 链" 反方向）。
2. Token 进入浏览器 DOM input value，浏览器自带 "显示密码" / DevTools 都能直接读出。
3. 服务端 `get_settings_metadata()` 已经声明 `sensitive_fields: ["onebot_access_token"]`（`server/settings_service.py`:420-424），但响应链并未据此 mask。
4. 与 servers R2 H-1（mask + 按需 reveal 端点 + 10s 自动隐藏 + 日志记录）形成显著不一致。

`_FIELD_SPECS` 已经标记 `sensitive=True`（settings_service.py:40），却没有任何代码消费这个标记 → 看似有防护实际是装饰。

**Fix sketch**：参考 servers R2 H-1 链路
- 后端在 `webui_settings.py` 内做最小裁切（不动 settings_service）：在 `webui_settings_get` 中对返回 data 做 `data["onebot_access_token"] = _mask_token(data["onebot_access_token"])`，新增 `_mask_token`（与 `webui_servers._mask_token` 同形）和 `_is_mask_token`；
- PUT 入口：在 `payload` 进入 `save_settings` 之前，若 `onebot_access_token` 为空 / mask 串，则从当前 snapshot 复用原值后再传入（保留 token）；
- 可选：新增 `GET /webui/api/settings/onebot-token` reveal 端点（auth 中间件已覆盖），加 client_ip + UA WARN 日志；
- 前端 `fillForm`：token input 设置 `placeholder="留空表示保留原 Token"`，并清空 value，让用户点眼睛图标显式拉取；同时 buildPayload edit 模式空 token 允许。

**Risk if unfixed**：任何获得 webui 会话的人可拿到 OneBot bot 凭据，等价于完整接管 bot。属于显式凭据泄漏。

---

## High

### H-1 `onebot_access_token` 明文写入磁盘且无脱敏日志保护

**File**: `server/routes/webui_settings.py`:103；`server/settings_service.py`:140, 414-417（引用 scope-out）

**Dimension**: security

**Issue**: 保存成功日志 `logger.info(f"保存设置成功：saved_fields={','.join(result.saved_fields)}")` 本身没问题，但 `SettingsValidationError` 分支 line 76 `logger.warning(f"保存设置失败：field={exc.field or ''}，reason={exc}")` 在 token 字段触发校验错误时（例如包含换行），`exc` 的 message 由 `_assert_single_line_string` / `_coerce_string` 拼接生成，理论上不含 token 值；但 catch-all 分支 line 86-87 `logger.exception(f"保存设置异常：reason={exc}")` 若底层异常把 token 原文塞到 message（例如未来添加的 URL 解析失败 message 内含原始 token 串），就会在日志里落下明文。

scope-out 注：实际落地的 `.env` 文件由 `_write_env_values` 写入，token 持久化在磁盘明文（属于 `.env` 设计语义，跨模块 backlog）。

**Fix sketch**：在 `webui_settings_put` 的 except 链中只 log `exc.__class__.__name__` 或 `repr(exc)[:200]`，不要直接 `f"...{exc}"`；或在 logger 入口增加敏感字段脱敏 wrapper（参考全局 logger 入口规范）。当前最小修：把 line 87 改为 `logger.exception("保存设置异常")` 让 traceback 进 stderr，不参与字符串拼接。

**Risk if unfixed**：未来 settings_service 异常 message 若意外携带 token，日志即变凭据泄漏面。

---

### H-2 PUT 缺乏请求体大小上限 / payload 字段数 cap

**File**: `server/routes/webui_settings.py`:67-92；`server/settings_service.py`:304-313（引用）

**Dimension**: security / perf

**Issue**: `read_json_object` 后直接交给 `save_settings`，`_normalize_payload` 会逐字段 `assertSingleLineValue` + `coerce_string`，对单字段长度 / payload 总键数都没有约束。一个 100MB 的 JSON 字符串字段（如恶意构造的 `group_welcome_template`）会先在 `request.json()` 反序列化阶段占用内存，再在 `_write_env_values` 里被序列化进 `.env`（lines 122-163，整个文件 read-modify-write 在 `_WRITE_LOCK` 下进行），导致：
1. 单次请求可拖慢整个进程（阻塞所有写操作）。
2. `.env` 体积膨胀，下次启动配置加载缓慢甚至 OOM。
3. 缺少长度上限属于经典 DoS 面。

commands R3 已记录"共享层 body size limit" 为 backlog（FastAPI middleware 层）。

**Fix sketch**：在 `webui_settings_put` 入口添加最小护栏：
```python
content_length = int(request.headers.get("content-length", "0") or "0")
if content_length > 64 * 1024:
    return api_error(status_code=413, code="payload_too_large", message="请求体过大")
```
另外建议对 `group_welcome_template` / `group_farewell_template` / `chat_sync_template` / `player_notify_*_template` / `command_disabled_message` 这类自由文本字段在 settings_service 增加单字段长度上限（scope-out backlog，跨模块）。

**Risk if unfixed**：单条 PUT 即可让 .env 持久化变大或拖慢主进程，并影响后续启动。

---

### H-3 `/webui/api/restart` 是状态变更 POST 但无 CSRF / re-auth 保护

**File**: `server/routes/webui_settings.py`:112-121

**Dimension**: security

**Issue**: 中间件已挡未登录访问，但已登录用户可以被 CSRF 跨站脚本/链接触发 POST `/webui/api/restart` 来强制重启 bot 主进程（os.execv）。这是高破坏力的状态变更（杀掉所有在飞 OneBot 连接、所有 in-memory session、所有 rate-limit 状态）。当前认证仅依赖 session cookie（samesite=lax，line 144），lax 在顶层 navigation 的 POST 会被携带，但 fetch 跨站会被拒；不过结合恶意页面里的 `<form action="/webui/api/restart" method="POST">` + 用户点击仍可成功（lax 允许 top-level form submit）。

同样问题影响 PUT `/webui/api/settings`（line 67），但保存设置本身已经会触发重启，重叠风险面相同。

**Fix sketch**：
- 加 `Origin` / `Referer` 头校验：POST/PUT 路径要求 `Origin == request base_url`（与 `/webui/api/session` 等其他写入端点统一）；缺失或不匹配返回 403。
- 或要求自定义请求头 `X-Requested-With: NextBotWebUI`，因为 SimpleRequest 形态的 cross-site form 没法塞自定义头。
- 当前文件最小修：在 `webui_settings_put` 和 `webui_restart` 入口同步校验 `request.headers.get("x-requested-with")`。

**Risk if unfixed**：受控 OneBot bot 被任意离开者通过钓鱼链接强制重启，可被构造为反复重启（rate-limit storm）。

---

### H-4 setTimeout 重启回调引用 `window.location`，无重启失败的兜底分支

**File**: `server/webui/static/js/settings.js`:405-413

**Dimension**: ux / perf

**Issue**: 保存成功后 `setStatus("保存成功，正在重启程序", "success")` + `setTimeout(() => window.location.reload(), 3000)`。问题：
1. 后端 `_restart_worker` (webui_settings.py:30) `time.sleep(0.8)` 后 `os.execv`，但 execv 可能失败（line 33-36），失败后 `_RESTART_SCHEDULED` 被复位但进程仍在跑 —— 此时前端 3s reload 会成功，但用户以为重启完成；
2. 若进程真正在重启，3s 可能不够（execv 进程 + 重新监听端口），reload 会撞到 connection refused，浏览器显示错误页；
3. 用户在重启窗口期切换标签 / 关闭页面 — setTimeout 无 cleanup，无 visibilitychange 处理；
4. `saveButton.disabled = true` (line 391) 在成功路径里**没有恢复**，只有 catch 路径恢复（line 412），合理但与 ux 一致性（3s 后即将 reload，按钮 disabled 不可见即可）勉强可接受。

**Fix sketch**：
- 把 reload 延迟改为 1500ms + 加重试：reload 后探活失败时 setStatus 给出"重启失败，请手动刷新"；
- 或保留 3s 但在 unload 之前先 fetch `/webui/api/healthz` 探活，直到 200 再 reload；
- 至少在 setStatus 文案上明确"页面将在 3 秒后自动刷新；若长时间未恢复请手动刷新"。

**Risk if unfixed**：execv 失败或冷启动慢时用户被带到错误页，运维需要手动 SSH 排查。

---

## Medium

### M-1 `_serialize_env_value` 反斜杠 → 换行 转义只在 welcome/farewell 模板，chat_sync_template 等多行字段不在白名单

**File**: `server/settings_service.py`:117-118（scope-out 引用）；`server/routes/webui_settings.py` 间接调用

**Dimension**: security / 数据完整性

**Issue**: settings_service line 117 只对 `group_welcome_template`、`group_farewell_template` 做 `\\` 与 `\n` 的转义；而 `_SINGLE_LINE_STRING_FIELDS` (line 66-79) 包含 `chat_sync_template` / `player_notify_online_template` 等，前端 `assertSingleLineValue` (settings.js:220-226) 实际禁止换行，所以**目前**不会溢出。

但 `command_disabled_message` 也在单行白名单，若未来需求改为多行，缺少同步策略。属于"现在 OK，但容易踩"。

**Fix sketch**：在 `webui_settings.py` 注释里挂一个 docstring 指明"如要把 X 字段改成多行，需同步 settings_service _serialize_env_value 白名单"；或在 settings_service 集中检查（scope-out backlog）。当前文件最小修：无需改动，记录为已知约束。

**Risk if unfixed**：未来字段类型变更时，可能写入未转义的 `\n` 到 .env，造成下次解析时把后续行被切分错位（行注入面）。

---

### M-2 422 details 字段 message 与 servers/commands 不对齐：直接透传 `str(exc)` 仍含字段名

**File**: `server/routes/webui_settings.py`:75-85

**Dimension**: copy / api-design

**Issue**: 422 响应 `message=str(exc)` 与 `details=[{"field": ..., "message": str(exc)}]`。`SettingsValidationError` 的 message 形如 `"web_server_port 范围必须在 1-65535"`（settings_service.py:246），把英文字段名直接暴露给前端展示文案。前端 settings.js 在 buildPayload 阶段用中文 `FIELD_LABELS` 自己生成 message（lines 195-296），所以前端 client 侧校验通过、后端校验失败时，用户看到的是 `"保存失败，web_server_port 范围必须在 1-65535"`（英文字段名）。

与 servers/commands R2 后端约定不一致：commands 后端 `_map_validation_error` 会用 FIELD_LABELS 替换。

**Fix sketch**：在 `webui_settings.py` 内增加 `_FIELD_LABELS` 字典（与 settings.js:75-99 同步）+ `_localize_validation_message(exc)` helper，把 `str(exc)` 中的字段名替换成中文 label 再返回。或在 details message 透传英文，但在 top-level `message` 替换为中文 + 给前端依据 details[0].message 展示。

**Risk if unfixed**：用户看到混杂英文字段名（如 `group_auto_ban_on_leave_enabled`）的错误提示，文案断层。

---

### M-3 后端 error message 拼接 "保存设置失败" 违反 CLAUDE.md "动作+结果" 解耦原则

**File**: `server/routes/webui_settings.py`:76, 87, 95；line 99, 100, 118

**Dimension**: copy

**Issue**: CLAUDE.md 明确规定"后端 error.message 应仅返回有效原因，不拼接'动作+结果'"。当前：
- line 99：`message="重启已在进行中，请稍后刷新页面"` —— "请稍后刷新页面" 是面向前端展示的指导语，应由前端生成；后端只回 "重启已在进行中"。
- line 100：`details=[{"field": "restart", "message": "重启已在进行中"}]` —— 这条合规，前一条 message 应与之对齐。
- line 118：同样 `message="重启已在进行中，请稍后刷新页面"`。

前端 settings.js line 387 `setStatus(\`保存失败，${message}\`, "error")` 把后端 message 拼到"保存失败，"后面 → 输出 `"保存失败，重启已在进行中，请稍后刷新页面"` —— 末尾的"请稍后刷新页面"是前端不可控的展示文案。

**Fix sketch**：line 99 / line 118 message 改为 `"重启已在进行中"`；如需展示"请稍后刷新页面"，前端在 saveSettings 的 catch 分支根据 status=409 自行追加。

**Risk if unfixed**：违反 CLAUDE.md 接口规范，且无法在前端按用户语言或场景调整展示文案。

---

### M-4 前端校验 message 违反 "动作+结果" 规范（操作对象名拼接）

**File**: `server/webui/static/js/settings.js`:387, 405, 411

**Dimension**: copy

**Issue**: CLAUDE.md "用户操作反馈文案规范"明确"不得包含操作对象名称，动词后直接接成功/失败"。
- line 405：`setStatus("保存成功，正在重启程序", "success")` —— "正在重启程序"过度展示后端行为，"程序"也是操作对象名衍生；CLAUDE.md 正例是 `保存成功`。
- line 387：`setStatus(\`保存失败，${message}\`, "error")` —— 格式合规（动作+结果，原因），但 `message` 可能来自 buildPayload 抛出（如 `"OneBot 访问令牌 不能为空"`），这里字段名又是中文，合规。
- line 411：`setStatus(message, "error")` —— 来自 `apiRequest` 抛出的 `ApiRequestError`，message 已经是 `buildActionFailureMessage("保存", reason)` = "保存失败，{reason}"，合规。

主要问题是 line 405 把"正在重启程序"塞进 success 文案，**包含了操作对象 "程序"**。

**Fix sketch**：line 405 改为 `setStatus("保存成功", "success")`；如需暗示后续动作，挪到副文案或 setTimeout 之前的 toast。

**Risk if unfixed**：与 servers/commands 已审定通过的 "保存成功" / "删除成功" 形式不一致，文案断层。

---

### M-5 buildPayload 错误消息字段名带英文（间接通过 FIELD_LABELS 已避免，但子检验消息暴露）

**File**: `server/webui/static/js/settings.js`:142-296

**Dimension**: copy / ux

**Issue**: `parseCommaListField`、`validateWsUrls`、`validateQqIdList`、`assertSingleLineValue` 这些 helper 都接受 `fieldLabel`，调用时全部传入 `FIELD_LABELS.xxx`（中文），合规。

但 `command_disabled_mode` 错误信息 (line 285-287)：
```js
`${FIELD_LABELS.command_disabled_mode} 仅支持 ${MODE_LABELS.reply} 或 ${MODE_LABELS.silent}`
```
输出 `"命令关闭模式 仅支持 回复提示 或 静默拦截"` —— 中文之间多了一个空格（"命令关闭模式 仅支持"），CLAUDE.md 规则 4 "中文与英文混排时保留一个空格"，纯中文之间不应加空格。同类 issue line 198, 204, 207, 215, 224, 243, 251, 257, 260, 268, 274, 277, 295：模板字符串 `${FIELD_LABELS.xxx} 不能为空` 全部是 "中文字段名 + 空格 + 中文谓词"。

**Fix sketch**：模板字符串里把"`${label} 不能为空`"改成"`${label}不能为空`"（去掉一个空格）；只在 label 末尾是英文/数字时才加空格 —— 但本场景全部 label 是中文，统一去空格即可。

**Risk if unfixed**：与项目其他错误消息（servers/commands）的中文风格不一致，视觉碎屑。

---

### M-6 单 `setStatus` 节点既显示加载态又显示错误，无法区分先后

**File**: `server/webui/static/js/settings.js`:121-133, 364-413

**Dimension**: ux

**Issue**: `statusNode` 单节点四态混用（info/success/error/warning）。`loadSettings` 失败时 setStatus error；3 秒后用户点 reload 又走一次 loadSettings → setStatus("") 先清空，再 setStatus 错误 / 成功。无 loading 文案（line 365 直接 `setStatus("")`），用户感知不到"正在加载"。

对照 servers/commands R2 已规范：load 时显示"加载中…"。

**Fix sketch**：`loadSettings` 入口 setStatus("加载中…", "info")，成功 setStatus("")，失败 setStatus(error.message, "error")。

**Risk if unfixed**：弱网下用户点 reload 后多秒无反馈，疑似无响应。

---

### M-7 reload 按钮无 abort / 无 disabled，快速点击触发并发请求

**File**: `server/webui/static/js/settings.js`:416-418, 364-379

**Dimension**: perf / ux

**Issue**: `reloadButton.addEventListener("click", () => void loadSettings())` 无 disabled 标志、无 AbortController，用户连续点击会启动多个 fetch；后到响应可能覆盖先到响应（race）。settings 数据非分页、字段固定，race 影响有限但仍属规范缺失。

对照 servers R2 已采用 `cancelPendingReload` + `abortable` 模式。

**Fix sketch**：增加 `loadingSettings = false` 标志：reload 入口 `if (loadingSettings) return;` finally `loadingSettings = false`。或维护 `loadAbortController`，新调用前 `controller.abort()`。

**Risk if unfixed**：并发刷新偶发 race，fillForm 顺序不可预测。

---

### M-8 保存路径无 Enter 提交快捷键 / 无未保存提示 / 无 beforeunload 守护

**File**: `server/webui/static/js/settings.js`:416-431；`settings_content.html`:1-248

**Dimension**: ux

**Issue**: 大量 input 字段，用户可能修改多个字段后误关闭/导航，无 beforeunload 拦截；表单本身不是 `<form>` 元素（template 全部 div/section/label），无原生 form submit 行为。

commands R2 引入了 beforeunload abort（B-2），settings 未跟进。

**Fix sketch**：维护 `isDirty` 标志，input/change 监听器置 true；save 成功置 false；`window.addEventListener("beforeunload", e => { if (isDirty) e.preventDefault(); })`。

**Risk if unfixed**：用户填了 20 个字段后误关浏览器，无 warning 即丢失。

---

### M-9 web_server_host 缺乏格式校验，可被设为非法值导致下次启动失败

**File**: `server/webui/static/js/settings.js`:246-252；`server/settings_service.py`:259-260（引用）

**Dimension**: security / 可用性

**Issue**: `web_server_host` 前端仅检查"非空 + 单行"，settings_service `_coerce_string` 也只 strip。用户可以填入 `"hello world"` 或 `"; rm -rf /"` 等任意字符串，写入 `.env` → 下次启动 uvicorn `host` 参数解析失败 → bot 起不来 → 用户被锁在 WebUI 之外（无法登录 fix）。

类似 web_server_public_base_url (line 270-278) 已做 URL parse 校验。

**Fix sketch**：前端 `buildPayload` 中追加 IP / hostname 格式校验（IPv4、IPv6、域名、`*`、`0.0.0.0`、`127.0.0.1` 白名单或正则）；或前端只允许从 dropdown 选 `0.0.0.0` / `127.0.0.1`。

**Risk if unfixed**：错误配置后 bot 启动失败，用户失去 WebUI 入口 → 需 SSH 修复 .env。

---

## Low

### L-1 module-level worker thread name 写死 `"nextbot-restart-worker"`，重复触发场景调试不友好

**File**: `server/routes/webui_settings.py`:46-50

**Dimension**: perf / 可观测性

**Issue**: 单例线程名固定。`_schedule_process_restart` 加锁防并发，重复触发时返回 False，所以线程名重复不会发生 —— 合规，但在 log analysis 时无法区分是哪次重启请求来源（settings 保存 vs 直接 /restart）。

**Fix sketch**：在调用方传入 `source` 标签到 thread name，例如 `nextbot-restart-worker[settings-save]` vs `nextbot-restart-worker[manual]`。极低优先级。

**Risk if unfixed**：运维排查时无法追溯具体重启触发源。

---

### L-2 `os.execv` 不传 env，依赖默认 inheritance；DATA_DIR 环境变量未显式确认

**File**: `server/routes/webui_settings.py`:32

**Dimension**: security / 可用性

**Issue**: `os.execv(sys.executable, [sys.executable, *sys.argv])` 不传 envp，子进程继承父进程 env。若有热修改的 env 变量（除了 .env 文件持久化部分），重启后丢失 —— 实际场景中 settings 都落 .env，影响小。

**Fix sketch**：保持现状即可，或换 `os.execve(..., env=os.environ.copy())` 显式语义。

**Risk if unfixed**：未来若添加进程 env 注入路径，重启丢失。

---

### L-3 logger.exception 在 422 / 409 分支 leaks stack to log，但 message 安全；不一致：normal validation 用 warning，conflict 不打 stack

**File**: `server/routes/webui_settings.py`:76, 87, 95-101

**Dimension**: 可观测性

**Issue**: 三种异常路径：
- 422 validation: `logger.warning("保存设置失败：field=...")` — 不 stack
- 500 internal: `logger.exception(...)` — 带 stack
- 409 conflict: `logger.warning("保存设置失败：reason=重启已在进行中")` — 不 stack

逻辑正确。轻微吐槽：line 87 `f"保存设置异常：reason={exc}"` 与 422 路径 message 模板基本一样（"保存设置失败 vs 异常"），grep 难筛选。

**Fix sketch**：把 line 87 message 改为 `"保存设置内部错误"`，与 status_code=500 语义对齐。

**Risk if unfixed**：日志检索时 422 / 500 难区分。

---

### L-4 form-section 缺乏可访问性结构：`<section>` 无 `aria-labelledby`，h3 与 section 未绑定

**File**: `server/webui/templates/settings_content.html`:27-28, 57-58, 91-92, etc.

**Dimension**: ux / a11y

**Issue**: 每个 `<section class="form-section">` 后跟 `<h3 class="section-title">...</h3>`，但 section 没有 `aria-labelledby="..."` 指向 h3 的 id（h3 也无 id）。屏幕阅读器无法把 section 与标题关联。

**Fix sketch**：给每个 h3 加 id（如 `id="section-onebot"`），section 上加 `aria-labelledby="section-onebot"`。

**Risk if unfixed**：accessibility 评分降低，盲人用户难以理解结构。

---

### L-5 token-input-toggle 缺 keyboard focus 视觉反馈

**File**: `server/webui/static/css/settings.css`:179-205

**Dimension**: ux / a11y

**Issue**: `.token-input-toggle` 仅定义 `:hover` 状态，无 `:focus-visible` 描边；键盘 Tab 用户聚焦到此按钮看不到 focus ring。

**Fix sketch**：增加 `.token-input-toggle:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }`。

**Risk if unfixed**：键盘 a11y 不完整。

---

### L-6 tag-badge 用 `title` 做 tooltip，移动端无效

**File**: `server/webui/static/js/settings.js`:184；`settings.css`:146-160

**Dimension**: ux

**Issue**: `badge.title = value` 鼠标 hover 显示完整内容，但 max-width 100% + nowrap 在窄屏 mobile 上会被裁剪，且无 tooltip 触发方式（CSS 没有 click-to-show）。

**Fix sketch**：保留 `title` 作 desktop fallback，加 `aria-label`；移动端考虑 long-press 展开或 wrap 显示。

**Risk if unfixed**：移动端用户看不到被裁剪的长 token 值。

---

### L-7 alert `aria-live="polite"` 在状态频繁切换时屏幕阅读器读不全

**File**: `server/webui/templates/settings_content.html`:22-24

**Dimension**: ux / a11y

**Issue**: `<div id="status" class="alert hidden" role="status" aria-live="polite">` 先隐藏，setStatus 改 className 移除 hidden + 改 textContent。`aria-live="polite"` 配合 textContent 变化能被识别，但 `display: none` → `display: flex` 切换在某些屏幕阅读器实现里会重新读取整段；另外 `setStatus("")` 同时清空 className 和文本 — 没问题。

**Fix sketch**：保持现状即可，文档化"polite live region with display toggle"。

**Risk if unfixed**：极少数 screen reader 行为差异。

---

### L-8 textarea 多行模板 `\r` 静默 strip，没有提示用户

**File**: `server/webui/static/js/settings.js`:220-226；`settings_content.html`:207-208, 220-221

**Dimension**: ux

**Issue**: welcome / farewell template 是 `<textarea>`，前端 buildPayload 没有对这两个字段调 `assertSingleLineValue`（它们应该允许换行），后端 `_SINGLE_LINE_STRING_FIELDS` (settings_service.py:66-79) 也不含这两字段。但 `_serialize_env_value` 把 `\r` 显式 strip 掉（line 118 `.replace("\r", "")`）。Windows 用户复制粘贴 CRLF 时 `\r` 被吃掉，无任何提示。

**Fix sketch**：buildPayload 时把 `\r\n` → `\n`，并对用户隐性透明（实际就是当前行为，但需文档化）。或前端 textarea 加 `onChange` 把 `\r\n` 归一为 `\n`。

**Risk if unfixed**：用户疑惑为什么粘贴模板再保存格式略变。

---

## Scope-out backlog

- **共享层 body size limit**（commands R3 已记录）：FastAPI middleware 层统一 64KB 上限。
- **settings_service.py `_serialize_env_value` 多行字段白名单**：跨模块，应由 settings_service 维护"哪些字段允许多行"的统一表。
- **logger 全局脱敏 wrapper**：未来 settings_service 异常 message 若包含 token，需在 logger 入口做 sensitive 字段脱敏（CLAUDE.md 已规定但未落地）。
- **`.env` 持久化加密**：OneBot token 当前以明文写入 `.env`，应考虑用 secret store（macOS Keychain / Linux secret-service）替代。
- **CSRF middleware 全局化**：当前每个写入端点都需自查 Origin/Referer，应在 webui auth middleware 之后再加一层 CSRF 中间件统一处理。
- **`web_server_host` 校验下沉**：前端 + settings_service 应同步加 IP / hostname 校验。

---

## Top 3 Highest-Severity 摘要

1. **CRIT-1**：`GET /webui/api/settings` 明文返回 `onebot_access_token`；`sensitive_fields` 元数据宣称敏感但未真正 mask。整条链需参考 servers R2 H-1 token chain 改造（mask + reveal endpoint + 10s 隐藏 + edit 模式空 token 保留原值）。
2. **H-1**：`logger.exception(f"保存设置异常：reason={exc}")` 拼接 `exc` 字符串入日志，未来 settings_service 异常 message 可能携带 token 明文 → 转为不带 exc message 的 exception log。
3. **H-2**：PUT 无 body 大小上限；恶意构造 100MB 模板字符串可拖慢主进程 + 膨胀 `.env` + DoS。

---

## Caveats / Not Found

- 未发现 SQL 注入面（端点无 DB query，仅落 .env）。
- 未发现 SSRF 面（无外部 fetch）。
- 未发现 path traversal 面（不接受路径参数）。
- 未发现 XSS：`renderTagPreview` (settings.js:170-187) 用 `textContent` 不是 `innerHTML`，安全；token toggle 用 `innerHTML` (line 136) 但赋值的是常量 `SHOW_ICON_SVG` / `HIDE_ICON_SVG`，无注入面。
- 未发现 fetch 泄漏（API 调用统一走 `api.apiRequest`，已有 timeout 与 abort 兜底）。
- 未发现定时器 / listener leak：tokenToggle / reload / save 都是一次性 binding；setTimeout 在 saveSettings 是单次。
- 未发现 prop / SSRF 注入（baseUrl 已 parse + protocol whitelist）。
- 未审：`server/settings_service.py`、`server/routes/webui.py`、`api.js` 已 scope-out（仅在 fix 建议中引用）。
