# Audit: WebUI 共享路由（login-requests + player-events）

- **Date**: 2026-05-15
- **Scope（严格 2 文件）**:
  - `server/routes/webui_login_requests.py`（210 LOC）
  - `server/routes/webui_player_events.py`（186 LOC）
- **Dimensions**: security / performance / UX / copy
- **方法**: 通读两文件 → 对比 prior audit（auth-401-vs-302、servers-audit-r2、login-audit 的 C7/E8）→ 与 `error-handling.md` / `logging-guidelines.md` / global CLAUDE.md（动作 + 结果，原样透传）对照。

> 关键定性：这两个路由**不是 SSE / long-poll**，而是 **POST 写端点**（被外部 Terraria 游戏服务器 / chat sync 调用 → 经 OneBot 推送到 QQ 群）。auth middleware（`server/routes/webui.py:204-220`）会拦截 `/webui/api/*` 未登录请求并返回 `401 unauthorized`，因此两端点**都受 cookie / token 鉴权保护**，不会出现匿名读取风险。但仍存在多处可被滥用的细节问题。
>
> 这意味着调用方实际上是「**已登录 webui 管理员/已掌握 webui token 的游戏服务器进程**」。**Server-to-Server 调用通过 query token 鉴权** —— 任何持有 `webui_token` 的脚本均可调用。一旦 token 泄漏（含 `server/web_server.py:402` 明文日志泄漏面），即可被远程批量调用骚扰 / 钓鱼 / 探测。

---

## 总体统计

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 4 |
| Medium | 9 |
| Low | 9 |
| **Total** | **22** |

### Top 3 最高优先级

1. **H-1 缺少 per-user / per-endpoint rate limit（login-requests）** —— 与 login-audit C7 同结论，至今未落地。运维一旦掌握 token / 被劫持 session，可对任意 `User.name` 无限制 spam `@用户` 私聊式钓鱼消息，5 分钟有效窗口里堆叠多条「请回复『允许登入』/『拒绝登入』」极易诱骗用户授权。
2. **H-2 player-events 完全无 rate limit，且 `message` 内容直接拼模板转发到 QQ 群** —— `player_name` / `server_name` / `message_text` 均未过滤，可作为「合法白名单转发器」滥用：远程持 token 调用 → bot 在群里发出任意文本（含 url / 钓鱼 / 广告），管理员看日志难以归因（日志只记调用入参，未记 client_ip / UA）。
3. **H-3 敏感操作（login-requests 推送 / player-events 推送）未记录 client_ip / User-Agent** —— 与 servers-audit-r2 D-2 落地的 8 端点对比，这两个**风险更高**的推送端点反而是仅有未覆盖的盲区，事后追溯 / 滥用归因无依据。

---

## Security

### H-1 login-requests 缺少 per-target rate limit
**File**: `server/routes/webui_login_requests.py:85-210`
**Dimension**: security
**Issue**: 端点对同一 `name` / 同一 client_ip 没有任何节流。`login-audit C7` 已明确此项 5 分钟最多 1 次的修复建议，至今未落地。受保护接口（webui 鉴权内）+ 5 分钟「允许登入 / 拒绝登入」有效窗口，攻击者可在窗口内反复 push `@用户 请回复…` 制造 race condition / 社工骚扰；尤其结合 `notify_all=True` 路径会向用户所有群同时刷屏。
**Fix sketch**:
- 复用 `webui.py:_FAILED_LOGIN_WINDOW_SEC` 同款滑动窗口，单独建 `_login_request_history: dict[str, deque[float]]` key 为 `user_id`（或 `name.lower()`），窗口 300s 上限 1。
- 命中限速返回 `429 too_many_requests`，`Retry-After=window_remaining`，`message` 仅原因（如 `"该用户最近已发送过登入确认，请稍后再试"`），不要拼「动作+结果」。
- 单元线程安全：复用 `threading.Lock`。
**Risk if unfixed**: 钓鱼消息刷屏 / 群审计噪音 / 用户因「连续多次允许登入提示」误授权。

### H-2 player-events 完全无 rate limit，可作群消息转发器
**File**: `server/routes/webui_player_events.py:71-186`
**Dimension**: security
**Issue**: 任何持 webui token 的调用方可：调用 `event=message`，`message` 字段任意文本（含链接 / `@everyone` 类伪装 / 广告）→ 走 `_resolve_chat_target_groups()` 转发到 chat sync 群；或 `event=online/offline` 但 `server_name` 字段塞钓鱼链接（`[{server}]{player} 上线了` 默认模板会把 `server_name` 直接 substitute 进文本）。无并发 / 速率限制，bot QQ 账号容易被风控 / 封禁。
**Fix sketch**:
- 引入 per-server / per-player 滑动窗口（如 `server_name + event` 维度，60s 内 30 条），命中返回 429。
- 入参长度严格上限：`player_name ≤ 64`、`server_name ≤ 64`、`message ≤ 500`（建议低于群消息 5000 字节限制并避开图片 / 富文本注入面）。
- 调用 `_validate_text_safe(message_text)`：拒绝含控制字符（`\x00-\x08\x0b\x0c\x0e-\x1f`）和换行符过多的输入（>5 个 `\n` 视为可疑），防止单条消息被拆成「公告样式」。
**Risk if unfixed**: bot 账号被 QQ 风控 / 封禁；管理群被滥用为广告 / 钓鱼通道；运营事故难定位发起方。

### H-3 两端点均未记录 client_ip / user_agent
**File**:
- `server/routes/webui_login_requests.py:106, 116, 134, 168, 180`
- `server/routes/webui_player_events.py:118, 132, 170, 176`
**Dimension**: security
**Issue**: `servers-audit-r2 D-2` 已为 servers 模块 8 个端点统一注入 `client_ip` / `user_agent`（`webui_servers.py:59-71` 提供 `_client_ip` / `_user_agent`，截断 UA 200 字符）。**这两个推送类端点同等关键，却完全无 IP/UA 字段**，与 `webui.py:_client_ip` 风格也不一致。一旦发生骚扰 / 误推送 / 攻击事件，无任何审计线索。
**Fix sketch**:
- 在两文件分别新增 `_client_ip` / `_user_agent` 私有 helper（与 `webui_servers.py:59-71` 文字一致即可；跨模块复用可放入 `server/routes/__init__.py`，但本任务 scope 内允许局部复制并标 `scope-out backlog: 抽取 _client_ip helper 到 routes/__init__.py`）。
- 所有 `logger.info(成功)` / `logger.warning(失败)` / `logger.exception(异常)` 行尾追加 `client_ip={ip} user_agent={ua!r}`，与 servers 模块同款 `key=value`。
**Risk if unfixed**: 后续审计 / 滥用追责无据；与 servers / login session 模块审计字段不对齐。

### H-4 login-requests `_resolve_user_id_by_name` 大小写匹配可导致跨用户误授权
**File**: `server/routes/webui_login_requests.py:29-35`
**Dimension**: security
**Issue**: `func.lower(User.name) == name.lower()`：若数据库中允许并存 `Alice` 与 `alice`（取决于 `User.name` 唯一约束是否大小写敏感），按 `User.id.asc()` first 的策略会**始终命中较早注册的那个**，将登入确认推送给非预期用户。在 Terraria 玩家昵称生态中（昵称大小写差异常见、可能重名），可能造成把 A 玩家的登入提示发给同名玩家 B、并诱导 B 误回「允许登入」。
**Fix sketch**:
- 优先在 `User` 模型上确认 `name` 是否有 `unique`/`citext` 约束；若数据库本就保证大小写不敏感唯一，则保留逻辑但补注释。
- 若不能保证：精确大小写匹配 `User.name == name`，仅在 strict 模式失败时回退 `ilike`，并在多于 1 条时返回 `409 conflict`（`code="ambiguous_name"`），让运维明确触发哪一个 `user_id`。
- 同步增加调用方应优先传 `user_id` 而非 `name` 的 backlog（scope-out）。
**Risk if unfixed**: 多账号同名场景下钓鱼面扩大 / 错误授权事故。

### M-1 login-requests `int(user_id)` / `int(raw_gid)` 未防御
**File**: `server/routes/webui_login_requests.py:51-52, 75-76, 152`
**Dimension**: security
**Issue**: `_resolve_user_id_by_name` 返回 `str(user.user_id)`，但下游多次 `int(user_id)` 调用前未做异常防御（仅 `int(raw_gid)` 有 `try/except`）。若数据库中 `User.user_id` 出现脏数据（如非数字字符串、`telegram:` 前缀）或将来接入非 OneBot V11 适配器，会直接 `ValueError` → 当前没有顶层 `try`，**Stack trace 会被 FastAPI 默认 500 处理器吐出**，泄漏内部路径。项目已有 `nextbot/text_utils.py:safe_at_segment(...)` 防御，未复用。
**Fix sketch**:
- 在最外层包 `try / except Exception` → `logger.exception(...)` → 返回 `500 internal_error message="内部错误"`，与 `error-handling.md` 推荐模式一致。
- `OBV11MessageSegment.at(int(user_id))` 改为 `safe_at_segment_or_empty(user_id)`（已在仓库内）。
**Risk if unfixed**: 异常路径 500 + 内部信息泄漏；将来扩展非 OBV11 适配器时崩溃。

### M-2 login-requests 用户输入直入日志，存在 log injection
**File**: `server/routes/webui_login_requests.py:106, 116, 134, 169, 181`
**Dimension**: security
**Issue**: `name` 是请求方任意 string，直接 `f"...name={name}..."` 拼日志。若 `name` 含换行 / ANSI 转义 / 伪造 `[ERROR]` 前缀，会污染日志检索 / 终端显示。
**Fix sketch**: 统一 `name={name!r}` 用 repr 包一层，与 `webui.py:329-330` 的 `user_agent={user_agent!r}` 一致；同时硬限制 `len(name) <= 64`（同 M-3 输入长度限制）。
**Risk if unfixed**: 日志注入伪造 / 排障误导。

### M-3 player-events 输入长度无上限
**File**: `server/routes/webui_player_events.py:78-114`
**Dimension**: security
**Issue**: `player_name` / `server_name` / `message_text` 仅 `.strip()` + 非空校验，无 max length。攻击者可发 1MB 的 `message` → bot.call_api 失败或被 QQ 风控；亦可在 `server_name` 嵌入超长模板触发字符串替换爆炸。
**Fix sketch**:
- 在 `read_json_object` 后立即做长度校验，超长返回 `422 validation_error`，`details=[{"field":...,"message": "长度不能超过 N"}]`，遵循模块内既有 422 模式。
- 推荐：`player_name ≤ 64`、`server_name ≤ 64`、`message ≤ 500`。
**Risk if unfixed**: 资源消耗 / 风控 / 触发 OneBot 上游异常 500。

### M-4 player-events `message_text.replace("{message}", ...)` 之后无任何转义
**File**: `server/routes/webui_player_events.py:157-161`
**Dimension**: security
**Issue**: `template.replace("{player}", display_name).replace("{server}", server_name).replace("{message}", message_text)`，若 `message_text` 自己含 `{server}` / `{player}` 占位符，按顺序替换会被二次 substitution。除一般展示问题外，结合 `display_name = f"{player_name}（{bound_user_id}）"`，攻击者构造 `player_name="{message}"` 可让玩家名替换出 message 字段，**绕过 chat sync / online template 文案契约**（如 player 名变成被同步的聊天内容）。
**Fix sketch**:
- 切换为按字典一次性 `format_map`（自定义 dict 类 `MissingKey` 防 KeyError），或先把 `message_text` / `player_name` / `server_name` 中的 `{` `}` 全部替换成全角 `｛｝` 再做模板拼装。
- 同步在 prd / spec 补一条：「QQ 推送模板字段 substitution 一次性、不可二次 replay」。
**Risk if unfixed**: 玩家昵称被用来注入伪造的聊天 / 系统消息；模板契约被绕过。

### M-5 player-events Bot `send_group_msg` 调用未截断 OneBot 上游返回值
**File**: `server/routes/webui_player_events.py:166-174`
**Dimension**: security / observability
**Issue**: 与 `webui_login_requests.py:166-178` 对比，这里没有提取 `message_id`，外部调用方无法做幂等回扫，且 `exc` 直接进日志（保留 OneBot 原始异常没问题，但响应里只回 `sent_groups` / `failed_groups`，**调用方不知道每个 gid 上 OneBot 给出的真实失败原因**）。
**Fix sketch**:
- 失败 `gid` 与原始 `error.message` 一起放进 `failed_groups`（结构改为 `[{"group_id": ..., "reason": "<原始错误>"}]`）；按 global CLAUDE.md「不翻译 / 不改写第三方原始错误」原样透传。
- 成功项也带 `message_id`，与 login-requests 风格对齐。
**Risk if unfixed**: 上游故障难定位；调用方无幂等线索。

### M-6 login-requests 全部群发送失败时 502 但部分失败被吞掉
**File**: `server/routes/webui_login_requests.py:185-210`
**Dimension**: security / UX
**Issue**: 当 `notify_all=True` 且只有 1 个群 send 成功时，`any(r["message_id"] is not None for r in results)` 为 True → 返回 201，但 `results` 中的失败项**只有 `message_id=None`，没有原因**。运维看到「部分成功」却无法定位哪个群挂了、原因是 onebot 限流 / 权限 / 被踢出。`login-audit E8` 已标记类似担忧未闭环。
**Fix sketch**:
- 失败 `result` 增加 `"reason"` 字段，原样保存 `exc` 的 `str(exc)`，对齐 global CLAUDE.md「保留原始错误」。
- success response 不放面向前端展示的 message。
**Risk if unfixed**: 多群推送不可观测；用户某些群没收到登入确认却无法溯源。

### L-1 `_pick_onebot_bot` 多 bot 并存时取首个
**File**:
- `server/routes/webui_login_requests.py:22-26`
- `server/routes/webui_player_events.py:21-25`
**Dimension**: security
**Issue**: `for bot in get_bots().values(): if isinstance(bot, OBV11Bot): return bot`：当存在多 bot 时无选择策略（迭代 dict 顺序非稳定），可能将「@用户」/ 玩家事件推送到错误账号的群。生产单 bot 场景下无影响，但缺乏防御性。
**Fix sketch**:
- 至少在 `get_bots()` 返回 > 1 时 `logger.warning` 一行，记录被选 bot 的 self_id。
- 或允许 settings 指定 `webui_push_bot_id`，未指定则取 `min(bots.keys())` 保稳定。
**Risk if unfixed**: 多 bot 部署下推送目标不确定。

### L-2 `_find_user_group` / `_find_all_user_groups` 静默吞所有异常
**File**: `server/routes/webui_login_requests.py:48-58, 67-82`
**Dimension**: security / observability
**Issue**: `except Exception: continue`：onebot `get_group_member_info` 失败原因（用户不在群 / API 限流 / 网络抖动）一律吞掉，没有 debug 日志。用户报「为什么没收到登入消息」时无法排查。
**Fix sketch**: `except Exception as exc: logger.debug(f"群成员探测失败：user_id={user_id} group_id={group_id} reason={exc}")`。
**Risk if unfixed**: 异常路径完全黑盒。

### L-3 `notify_all=True` 路径下顺序探测所有群，无并发
**File**: `server/routes/webui_login_requests.py:67-82, 127-128`
**Dimension**: performance
**Issue**: 串行 await N 个 `get_group_member_info`，N=授权群数。即便 onebot 单次平均 100ms，10 个群也要 1s。
**Fix sketch**: `asyncio.gather(*[bot.call_api(...) for gid in group_ids], return_exceptions=True)`。需保留对每个 gid 的成功 / 失败状态。
**Risk if unfixed**: 单端点延迟与授权群数线性相关。

### L-4 `notify_all=True` 路径串行发送，且单失败不阻塞但顺序无并发
**File**: `server/routes/webui_login_requests.py:159-183`
**Dimension**: performance
**Issue**: 同 L-3，发送阶段也是串行，对 N 群推送总耗时 = 累加。
**Fix sketch**: 同上，用 `asyncio.gather`。注意保留每个 result 的 `group_id` / `message_id` / `exception` 顺序。
**Risk if unfixed**: 用户高峰期推送延迟感知。

---

## Performance

### M-7 `_resolve_user_id_by_name` 每次新建 Session 且不带索引提示
**File**:
- `server/routes/webui_login_requests.py:29-35`
- `server/routes/webui_player_events.py:28-39`
**Dimension**: performance
**Issue**: `func.lower(User.name) == name.lower()` 不会用到 `User.name` 普通 b-tree 索引（除非 SQLite collation NOCASE / Postgres 函数索引）。在 user 表大时全表扫描。
**Fix sketch**:
- 验证 `User.name` 列是否 `collation="NOCASE"`（SQLite）或为 expression index（Postgres）；若非，建议改为 `ilike` + 在迁移里补 `func.lower(User.name)` 表达式索引（scope-out: 跨模块 schema 变更）。
- 至少在本任务标记 backlog。
**Risk if unfixed**: 用户表增长后 push 接口慢、阻塞 event loop。

### L-5 重复 helper `_resolve_user_id_by_name` 跨两文件
**File**:
- `server/routes/webui_login_requests.py:29-35`
- `server/routes/webui_player_events.py:28-39`
**Dimension**: maintainability
**Issue**: 完全相同的实现两份；与 `_pick_onebot_bot` 一致。后续如修一处忘修另一处，行为分叉。
**Fix sketch**: 抽到 `server/routes/_shared.py` 或 `nextbot/access_control` / 既有 user lookup helper。本任务 scope 内不强制实施，标 `scope-out backlog: shared helper extraction`。
**Risk if unfixed**: 修复漂移。

---

## UX

### M-8 `notify_all=False` 但 `_find_user_group` 返回空时与 `notify_all=True` 时空列表错误码不一致
**File**: `server/routes/webui_login_requests.py:127-141`
**Dimension**: ux
**Issue**: 两条路径都返回 `404 group_not_found`，正确。但 `notify_all=True` 且 `_find_all_user_groups` 返回 `[]` 时，调用方无法区分「该用户不在任何授权群里」与「allowed_groups 配置为空」（后者本质是服务端配置错误，应返回 `409` / `503` 之类）。
**Fix sketch**:
- 在 `_find_all_user_groups` / `_find_user_group` 顶部检查 `allowed_groups`，空则 raise 一个明确的 internal config error，端点层翻译为 `503 service_unavailable` + `code="no_allowed_groups"`，message 仅原因「未配置授权群」。
**Risk if unfixed**: 配置错误被误报为「用户找不到」，运维误排查。

### M-9 player-events `409 no_target_group` 与 login-requests `404 group_not_found` 语义混淆
**File**:
- `server/routes/webui_player_events.py:131-139`
- `server/routes/webui_login_requests.py:137-141`
**Dimension**: ux
**Issue**: 两个文件对「服务端配置未指定目标群」选择了不同状态码（409 vs 404）。从 REST 语义看，**配置层错误更接近 `503 service_unavailable` 或 `409 conflict`**，404 不合适（资源本身存在，缺的是配置）。
**Fix sketch**: 统一为 `503 service_unavailable code="service_misconfigured"`，message 仅原因。同步在 `.trellis/spec/backend/error-handling.md` 的「status 映射」段落补一条「配置缺失类错误 → 503」。
**Risk if unfixed**: 前端 / 调用方按 4xx 处理重试逻辑，但实际服务端需运维介入。

### L-6 player-events 成功 response 含可被理解为「面向展示」的 `sent_groups` / `failed_groups`
**File**: `server/routes/webui_player_events.py:181-186`
**Dimension**: ux / contract
**Issue**: 字段名 OK（机器消费），但**没有总览字段（如 `total_targets`、`success_count`），** 前端 / 运维需自己 `.length`。与 servers 模块返回单数据结构的风格略有差异。
**Fix sketch**: 增加 `summary: {total: int, success: int, failed: int}` 元数据；放在 `data` 内（不放 `meta`，因为不是分页元数据）。
**Risk if unfixed**: 调用方做监控埋点时需要二次聚合。

---

## Copy（基于 global CLAUDE.md / error-handling.md）

### M-10 message 拼装「动作 + 结果」违反 global rule
**File**:
- `server/routes/webui_login_requests.py:189` `message="发送消息失败"`
- `server/routes/webui_player_events.py:124` `message="机器人未连接"`（OK，单一原因）
**Dimension**: copy
**Issue**: 按 global CLAUDE.md：「后端 error.message 应仅返回有效原因，不拼接『动作+结果』」。`"发送消息失败"` 是「动作（发送消息）+ 结果（失败）」式表达，应仅返回**原因**（如 `"全部目标群推送均失败"` 或原始 OneBot 错误）。
**Fix sketch**:
- `message="发送消息失败"` → `message="全部目标群推送均失败"` 或将上游 OneBot 异常聚合为列表透传到 `details`。
- 同步在 `webui_login_requests.py:186-190` 把每个 group 的 `reason` 放进 `details=[{"group_id": ..., "reason": ...}]`。
**Risk if unfixed**: 前端展示重复「保存失败，发送消息失败」式拼接，用户看不到真正原因。

### M-11 login-requests 多群 success response 字段结构在「1 群」与「多群」分叉
**File**: `server/routes/webui_login_requests.py:192-210`
**Dimension**: copy / contract
**Issue**: `len(results) == 1` 时返回顶级 `group_id` / `message_id`；否则返回 `results: [...]`。调用方需写两套解析。违反 REST 一致性，**且 success response 没有面向展示文案**这点本身是对的（符合 global rule），但**结构二态**是反例。
**Fix sketch**: 统一返回 `results: [{group_id, message_id}]`，1 群也用数组；上层调用方根据数组长度走分支。
**Risk if unfixed**: 前端 / 调用方写两套解码逻辑，未来扩展易出 bug。

### L-7 login-requests warning 日志格式中文逗号 vs key=value 风格混用
**File**: `server/routes/webui_login_requests.py:106, 116, 134, 169, 181`
**Dimension**: copy / log style
**Issue**: 日志主句 `f"发送登入确认失败：name={name}，user_id={user_id}，reason=..."` —— 用了中文全角逗号分隔 key=value，与 servers 模块（半角空格分隔）不一致。按 `logging-guidelines.md` 推荐保持模块内一致，但全仓 webui 后端整体趋向 servers / webui.py 那种半角空格 + key=value。
**Fix sketch**: 改半角空格分隔 `name={name} user_id={user_id} reason=...`；同时把面向人读的句首「发送登入确认失败：」保留即可。
**Risk if unfixed**: 日志聚合工具（grep / loki query）跨模块匹配规则需写两套。

### L-8 player-events warning 日志同样中文逗号
**File**: `server/routes/webui_player_events.py:119-120, 133-134, 171-172, 177-179`
**Dimension**: copy / log style
**Issue**: 同 L-7。
**Fix sketch**: 同 L-7。
**Risk if unfixed**: 同上。

### L-9 验证 message 与 details.message 100% 重复
**File**:
- `server/routes/webui_login_requests.py:94-99`
- `server/routes/webui_player_events.py:80-114`
**Dimension**: copy
**Issue**: `message="用户名称不能为空"` + `details=[{"field":"name","message":"用户名称不能为空"}]`：项目其他验证错误也常见，但语义冗余。`message` 是 top-level reason，`details[i].message` 应是字段级 reason；如果字段级 reason 与 top-level 一致，建议简化 `details.message` 或省 top-level。
**Fix sketch**: 保持现状或统一在 `error-handling.md` 写明「单字段验证错误时 top-level message = details[0].message 是约定的冗余以便 i18n / fallback」。本项不阻断。
**Risk if unfixed**: 文档契约缺失。

---

## 不在 scope 内的相关 backlog（仅记录，不在本审计修复）

- `_client_ip` / `_user_agent` helper 跨模块抽取到 `server/routes/__init__.py`（已在 servers-audit-r2 D-2 scope-out）。
- `_resolve_user_id_by_name` / `_pick_onebot_bot` 跨模块去重。
- `User.name` 大小写不敏感唯一约束 / 表达式索引补迁移。
- bot.call_api 全局并发 / 退避机制（影响所有 push 类端点）。
- 全仓「success response 不带 message + error.message 仅原因 + 不翻译第三方错误」spec 化（建议在 `.trellis/spec/backend/error-handling.md` 加专门小节，跨多 audit 反复出现）。

---

## 与 prior art 的对齐情况

| Prior audit 结论 | 本次状态 |
|---|---|
| login-audit C7（per-user rate limit）| **未落地** → H-1 |
| login-audit E8（多群部分失败可观测性）| **未落地** → M-6 |
| servers-audit-r2 D-2（IP/UA 全覆盖）| **未覆盖这两端点** → H-3 |
| auth-401-vs-302 修复（API → 401 JSON）| ✅ 受益：两端点未登录访问已正确 401 |
| error-handling.md（500 不泄漏原文）| **部分违反** → M-1（无顶层 try 兜底） |

## Caveats / Not Found

- 任务描述把这两端点称为「SSE / poll endpoint for pending login approvals」/「SSE / poll endpoint for live player events」，**与实际代码不符**：两者均为 **POST 推送端点**（外部 → bot → QQ）。SSE 相关安全维度（per-event 重鉴权、connection 清理、backpressure）**不适用**，本审计据实改写为推送端点的等价风险（rate-limit、log injection、输入长度、bot.call_api 并发）。
- 未发现 SQL injection（SQLAlchemy ORM 参数化）、未发现 SSRF（无 outbound URL 解析）、未发现 token 链泄漏（这两端点不返回任何 token / 凭据）。
- 未对 OneBot adapter 内部进行审计（scope 外）。
