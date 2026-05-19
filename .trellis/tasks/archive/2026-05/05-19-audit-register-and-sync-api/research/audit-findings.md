# 审计报告：注册账号 + sync API

> 范围：commit `1aa4bff` (user_manager + db.py 注册账号 / hash 迁移) + commit `d015756` (webui_sync.py snapshot 端点)
>
> 模式：**只读复审 + 汇报**，未做任何代码修改。

---

## 严重程度图例

- 🔴 Critical（安全漏洞 / 数据损坏可能）
- 🟠 High（影响功能正确性的 bug）
- 🟡 Medium（鲁棒性 / 运维体验问题）
- 🟢 Low（风格 / 一致性 / 微优化）
- ⚪ Info（观察，不一定要改）

---

## Findings

### F-1 🔴 Critical — 明文密码出现在 TShock REST API 的 URL query string，会写入访问日志

- 文件：`nextbot/plugins/user_manager.py:124-129` + `nextbot/tshock_api.py:111-160`
- 问题描述：`_create_tshock_user_on_server` 通过 `request_server_api(...)` 调 `/v2/users/create`，并把 `password=plaintext` 放进 `params`。`request_server_api` 全程只用 `client.stream("GET", url, params=query, ...)`（`tshock_api.py:158`），即 **以 GET + query string 发送**。结果：
  1. TShock 自身的请求日志（access log / debug log / `Log.cs`）会原样写入完整 URL，包含 `password=<明文>` 和 `token=<admin token>`。
  2. 上游 reverse proxy / nginx / Apache（若存在）的 access log 也会记录。
  3. httpx 自身在 `RetryError` / `ConnectError` 等异常的 `str(exc)` 里可能携带完整 URL（`httpx.URL` 在某些错误信息里被 repr）。这些字符串被 `_create_tshock_user_on_server` 的 `except TShockRequestError as exc: detail=str(exc)` 捕获后 → 再进入 `_create_tshock_user_on_all_servers` 的 `logger.warning(... reason={outcome.detail})`，最终落到 bot 的 console / log 文件。
  4. 进程 traceback 若被 capture-locals 工具拦下，URL 同样裸露。
- 影响 / 触发条件：每次「注册账号」命令成功路径都会触发。bot 日志、TShock 日志、运维链路上的任何中间日志都将永久持有明文密码。即便密码生成 16 位强度足够，**用户首次登入前通常 30 秒到几小时窗口**内 log 抓取者可立即拿到，已构成账号窃取窗口。Long-term：log 归档后，旧日志泄漏仍可重放（除非用户已用「修改密码」覆盖，但当前 task 未实现该命令）。
- 推荐修复（候选项，由用户决定）：
  - A. 让 `tshock_api` 支持 POST（form-encoded body），把 `password` 放 body。需要确认 TShock REST 端点 `/v2/users/create` 接受 POST（TShock REST 默认支持 GET，POST 是否被路由匹配需要确认）。
  - B. 调用 TShock REST 时使用其专门的「带密码」端点，若 TShock 支持以预 hash 直接写入（避免 bot 侧传明文）。
  - C. 退一步：在 `tshock_api.py` 内对 `password` / `token` key 做日志脱敏（仅遮蔽 logger 输出），并修补 `TShockRequestError` 抛错时 `str(exc)` 不带 URL（用预格式化 message + kind）。但这不能解决 TShock 端 / 反代端的日志泄漏。
  - D. 若 TShock 一定要走 URL，最次也要在 `_create_tshock_user_on_server` 的 outcome `detail` 上做 URL 脱敏过滤 + 改 httpx exception 包装策略，防止密码进入 bot 自己的 log。

### F-2 🔴 Critical — 明文密码经 plain HTTP（非 TLS）发送到 TShock

- 文件：`nextbot/tshock_api.py:143` (`scheme="http"` hardcoded)
- 问题描述：所有 TShock 调用都强制 `scheme="http"`，没有 TLS。注册账号路径把明文密码塞到这条 HTTP 链路上，任何处于 bot 与 TShock server 之间网络的 sniffer 都能直接读到密码（同机器/同 LAN/同 Docker bridge/VPN/云 VPC peering 路径都算）。
- 影响 / 触发条件：任何 TShock server 与 bot 不在同主机时（绝大多数生产部署）触发。如果 bot 和 TShock 跨 host，这就是网络层明文凭据传输。
- 推荐修复（由用户决定）：
  - 短期：在文档里硬性要求 bot 与 TShock 必须同主机或同 trusted overlay 网络。
  - 中期：让 `tshock_api` 支持 `scheme` 可配置（HTTPS + 自签证书），或走 SSH/wireguard tunnel。
  - 长期：与上面 F-1 合并解决：要么换成 POST + TLS，要么换 control plane（gRPC + mTLS 等）。
- 备注：这是预存在问题，注册账号 task 把它的影响放大（之前 GET 路径不携带 secrets，只有 token；现在多了 password）。

### F-3 🟠 High — 临时私聊密码推送失败时，用户回执仍声称"密码已通过私聊发送"

- 文件：`nextbot/plugins/user_manager.py:492-513`
- 问题描述：`_send_temp_private_password` 返回 `bool` 表示是否发送成功，但调用方 `handle_add_whitelist` **丢弃了返回值**。无论失败与否，最终响应里都包含 `"🔑 密码已通过私聊发送，请查收并妥善保存"`。同时由于该 task 没有「修改密码」命令、密码只在内存里短暂存活、明文不再可恢复，**用户被误导成"密码已送达"但实际没收到**，账号事实上不可用直到下一个 task 实现重置。
- 影响 / 触发条件：
  - 用户从未与 bot 互加好友且非共享群成员（极端情况）。
  - 用户开启了"陌生人临时会话拒收"（OneBot 平台限制）。
  - OneBot 实现端口被屏蔽 / network glitch。
  - bot 账号被对方拉黑。
- 推荐修复（由用户决定）：
  - A. 接收 `_send_temp_private_password` 返回值，失败时把响应文案改成 `"⚠️ 密码私聊发送失败，请联系管理员重置"` 或 `"⚠️ 密码暂未送达，请发送「找回密码」（待实现）"`。
  - B. 失败时直接在群内回执里附上密码（弱选项，会暴露给群内其他人）。
  - C. 失败时把密码塞进 reply（@用户）以减少暴露面 —— 但 OneBot reply 也是群内可见。
  - D. 当前文案显式承诺 send，但实际不一定 send，**至少应改为如实陈述**。

### F-4 🟠 High — `password_hash` 列不存在时（migration 失败场景）所有 User 查询会 SQL 错误

- 文件：`nextbot/db.py:444-461` (`_run_migration`) + `nextbot/db.py:689-710` (`ensure_user_password_hash_schema`)
- 问题描述：`_run_migration` 把单步 migration 失败转成 `logger.warning + 继续启动`，目的是不让局部 schema 故障阻断整个 bot。但 `User.password_hash` 是 ORM mapped 列，SQLAlchemy 默认会把它列入 `SELECT user.id, user.user_id, user.name, user.password_hash, ...` 任何 User ORM load 都会带上它。若 `ensure_user_password_hash_schema` 失败（例如 SQLite ALTER TABLE 被并发占用、磁盘只读、未来某次重命名 / 删列出现 bug），列实际未加，**所有后续 `session.query(User).filter(...).all()` 都会触发 `OperationalError: no such column: user.password_hash`**。整个用户系统瞬时全坏，但 bot 不会停。
- 影响 / 触发条件：低概率（SQLite ALTER 在干净 schema 上几乎不会失败），但一旦失败就是全功能下线 + 无任何 alarm（只有一行 warning）。
- 推荐修复（由用户决定）：
  - A. `ensure_user_password_hash_schema` 失败时升级到 raise，强制 init_db 整体失败 → 让 bot.py 的 startup 失败抛出（fail-fast，运维必然立即看到）。
  - B. 在 `_run_legacy_users_password_hash_migration` 启动 hook 里再做一次 column 存在性 PRAGMA 检查；缺失时记录 `[ERROR]` 而不是 warning，并阻止其他 user 路径继续（feature flag）。
  - C. 保持现状，因为概率极低；但应在 `_run_migration` 整体 wrapper 上增加 metrics / alert hook，让 warning 不会被淹没在日志里。

### F-5 🟠 High — `User` 创建路径不一致：WebUI 创建的用户 `password_hash` 默认 NULL，启动迁移随机 backfill 后用户永远拿不到密码

- 文件：`server/routes/webui_users.py:546-554` vs `nextbot/plugins/user_manager.py:462-477`
- 问题描述：
  - 注册账号命令路径：先 `_generate_random_password` → `_hash_password` → 写 DB → push TShock → 临时私聊把明文给用户。流程完整。
  - WebUI `POST /webui/api/users` 路径：`User(... )` 不带 `password_hash`，默认 NULL；不调 TShock create user。下次启动时 `_migrate_legacy_users_password_hash` 把 NULL 的 user 全部 backfill 随机 hash（明文随机生成后立刻丢弃，永远无法找回）。**WebUI 建出来的用户实际上拿不到自己的 TShock 账号密码**，sync snapshot 又会把这个不能用的 hash 推给 C# 插件。
  - 同 commit 内 sync snapshot 还指望 password_hash 是用户实际能登入的 hash —— 但 WebUI 创建路径破坏了这个不变量。
- 影响 / 触发条件：任何通过 WebUI 创建用户的场景。当前 task 的注册账号 + sync 流虽自洽，但与现有 WebUI 创建路径不一致。
- 推荐修复（由用户决定）：
  - A. WebUI 创建用户也走相同的「生成密码 → hash → TShock create → 推送密码给操作员（admin 自己 / 出页面下载 / 短期一次性 reveal）」流程。
  - B. WebUI 创建用户 hash 字段保持 NULL，明确不参与 sync snapshot（在 webui_sync 过滤掉 NULL hash 用户或返回 "需重置密码" 标志），等用户首次「修改密码」时再 backfill。等价于下个 task 「修改密码」上线后再处理。
  - C. 启动 migration **跳过** WebUI 创建的 NULL 用户（无法区分来源 → 需要加一个 `created_via_webui` 字段或类似 marker）。
  - 备注：用户在 prd 排除项里写「旧用户 backfill 写占位 hash —— P1 设计决策」。**但 WebUI 创建的不是"旧用户"**，是 ongoing 创建路径。这条与 P1 ADR 不冲突，是真正的不一致。

### F-6 🟡 Medium — 注册成功响应包含明文密码摘要（消息内容会进 NoneBot 事件日志 + OneBot WS 链路）

- 文件：`nextbot/plugins/user_manager.py:179-208`
- 问题描述：`_send_temp_private_password` 构造的 message 字符串里含明文密码：
  ```python
  f"🔑 密码：{password}\n"
  f"🎮 在服务器内输入「/login {password}」登入..."
  ```
  这条 message 通过 `bot.call_api("send_private_msg", ...)`。在 NoneBot 默认 DEBUG 日志级别下，**OneBot adapter 的 outgoing message 会被日志记录**（视具体 adapter，多数 v11 实现会 debug-level dump call_api payload）。若运维误开 `LOG_LEVEL=DEBUG`，明文密码就泄漏到 bot 日志。
- 影响 / 触发条件：DEBUG 日志环境（开发 / 临时排障 / 误配置）。生产 INFO 级一般安全，但缺乏 defense-in-depth。
- 推荐修复（由用户决定）：
  - A. 在调用前后 explicit 让 NoneBot logger 临时降级（不优雅，难维护）。
  - B. 接受现状但在 README / 部署文档 / .env.example 里硬性提示「禁止在生产环境开启 DEBUG 日志」。
  - C. 改用 OneBot 的「直接调用 underlying http API」绕过 NoneBot 包装（破坏抽象）。
  - 现实最优：B + 在代码注释里加 warning，提醒未来维护者别把 logger 调成 DEBUG。

### F-7 🟡 Medium — `httpx` 异常 message 可能泄漏密码到 bot 日志

- 文件：`nextbot/tshock_api.py:173-182` + `nextbot/plugins/user_manager.py:130-133`
- 问题描述：`request_server_api` 把 `httpx.TimeoutException` / `ConnectError` 等转成 `TShockRequestError(str(exc), ...)`。某些 httpx 异常的 `str()` 会包含完整 request URL（含 password / token query params）。例如 `httpx.ConnectTimeout` 在新版本 httpx 中确有 url 信息。这个 message 会通过：
  - `_create_tshock_user_on_server` 返回 outcome.detail
  - `_create_tshock_user_on_all_servers` 的 `logger.warning(... reason={outcome.detail})`
  - 直接落到 bot 日志文件。
- 影响 / 触发条件：TShock server 不可达时（注册账号路径上 connection 失败是常见情况）。
- 推荐修复（由用户决定）：
  - A. 在 `request_server_api` 的 `except httpx.*` 路径里只取 `exc.__class__.__name__` + 删除 URL，避免泄漏 query。
  - B. 增加日志脱敏 helper：所有进入 logger 的 string 都过一遍 `re.sub(r'password=[^&]+', 'password=***', s)`。
  - C. 让 TShockRequestError 把 url 字段单独分出来（结构化），调用方决定是否展示，且默认 outcome.detail 不带 url。

### F-8 🟡 Medium — `_create_tshock_user_on_server` 没区分"用户已存在"和"真失败"，会重试 / log 噪声

- 文件：`nextbot/plugins/user_manager.py:117-143`
- 问题描述：TShock `/v2/users/create` 在用户已存在时返回非 200，error message 通常是「User already exists」。当前实现 `is_success` 判 false 后一律走 `logger.warning(... result=failed reason={outcome.detail})`。这导致：
  - 重复运行「注册账号」（用户已删除 bot DB 但 TShock 还有账号）触发 warning，看起来像故障。
  - 启动迁移目前未调 TShock create（设计正确），所以这条只在「注册账号」命令重放路径触发。
- 影响 / 触发条件：人为重放 / 部署边界场景。
- 推荐修复（由用户决定）：
  - 区分 "already exists" 错误类型（解析 reason 字符串或检查 TShock 返回 code），降级为 `logger.info(... result=exists)`，正常路径继续。
  - 或者文档化"重复注册"流程，让运维知道 warning 可忽略。

### F-9 🟡 Medium — `webui_sync` 响应头 `Cache-Control: no-cache` 不够强，应为 `no-store`

- 文件：`server/routes/webui_sync.py:103`
- 问题描述：`Cache-Control: no-cache` 允许中间缓存存储响应，只是要求每次重新验证。响应 body 里含**所有用户的 bcrypt hash**（敏感）。如果未来 webui 前面挂 caching proxy / CDN 误配置，hash 可能被 proxy 缓存到磁盘。
- 影响 / 触发条件：部署链路新加缓存代理（不常见但是 ops 失误高发场景）。
- 推荐修复（由用户决定）：
  - 改成 `Cache-Control: no-store, private` —— 明确禁止任何中间存储。
  - 304 路径同样改 header。

### F-10 🟡 Medium — `_parse_if_none_match` 不符合 RFC 7232 的 `*` 通配语义

- 文件：`server/routes/webui_sync.py:60-69`
- 问题描述：RFC 7232 §3.2 规定 `If-None-Match: *` 应匹配任何已有 representation（用于 PUT 防新建 / GET 防回传），客户端发 `*` 时服务器应返回 304。当前实现把 `*` 当普通字符串比对，永远不会等于 SHA256 hex → 不走 304 → 浪费带宽。
- 影响 / 触发条件：极少（C# plugin 自身实现，不会发 `*`），但若未来引入第三方 client 触发。
- 推荐修复：在 `_parse_if_none_match` 后判 `value == "*"` 走 304 fast path。低优先。

### F-11 🟡 Medium — `_create_tshock_user_on_all_servers` 与 `_sync_whitelist_to_all_servers` 在并发下共用一个 per-server 全局 semaphore 但仅前者走 broadcast

- 文件：`nextbot/plugins/user_manager.py:146-176` + `nextbot/plugins/user_manager.py:339-367`
- 问题描述：
  - `_create_tshock_user_on_all_servers` 用 `broadcast()`，受 `_broadcast_semaphores`（per-server）保护，单 server 单并发。
  - `_sync_whitelist_to_all_servers` 用裸 `asyncio.gather`，**不走 semaphore**。
  - 在注册路径上 `asyncio.gather(_sync_whitelist..., _create_tshock_user...)` 并行调度，对同一台 TShock 实际同时发起 2 个请求（一个走信号量、一个完全不走），破坏 broadcast helper 设计意图（每台 TShock 单并发，防短时压力）。
- 影响 / 触发条件：每次注册账号成功路径。当前生产请求不重，影响不大。但破坏了 broadcast 抽象，未来排障会困惑。
- 推荐修复（由用户决定）：
  - A. 把 `_sync_one_whitelist` 也迁到 `broadcast()` 接口（统一并提交 outcome）。
  - B. 在 `handle_add_whitelist` 改成串行：先 `_sync_whitelist...` 再 `_create_tshock_user...`，牺牲 ~connect timeout 的并行 win 换一致性。
  - C. 保持现状，加注释解释 trade-off。

### F-12 🟢 Low — `_mask_user_id` 与 `webui_users._mask_qq` 重复代码

- 文件：`nextbot/plugins/user_manager.py:109-114`
- 状态：**已在 prd 排除项里 acknowledge OOS**，本审计不重复打分。

### F-13 🟢 Low — `from server.routes.webui import _client_ip` 跨模块导入私有名

- 文件：`server/routes/webui_sync.py:14`
- 问题描述：`_client_ip` 是 `_` 前缀的私有 alias（在 `webui.py:174` 由 `_client_ip = _shared_client_ip` 再导出）。直接 import 公共 `client_ip from server.routes`（已经在 `__init__.py:57` 公开）即可，避免依赖私有 API。
- 影响：纯 style；若 `webui.py` 未来删除该 alias，本文件会 import 失败。
- 推荐修复：`from server.routes import client_ip as _client_ip` 或直接 `from server.routes import client_ip`。

### F-14 🟢 Low — bcrypt 字节级 fail-fast：import 阶段无显式校验

- 文件：`nextbot/plugins/user_manager.py:8`
- 问题描述：`import bcrypt` 顶部硬依赖；若 `pyproject.toml` 装 bcrypt 失败（编译错误 / wheel 不可用），整个 user_manager plugin 加载就失败，bot 启动直接挂。这其实是设计意图（fail-fast），但缺一行注释说明。
- 影响：可观测性。
- 推荐修复：补一行 `# bcrypt 是硬依赖；缺失即让 plugin 加载失败而非运行时再 hash 失败` 类的注释。

### F-15 🟢 Low — `del plaintext` 与 `plaintext_password = None` 是 cargo-cult defense

- 文件：`nextbot/plugins/user_manager.py:238` + `nextbot/plugins/user_manager.py:502`
- 问题描述：Python 字符串是不可变对象，`del`/`= None` 只释放栈引用，**底层 byte buffer 仍可能滞留在 heap 直到 GC + memory reuse**。这两行不能真正"清除"内存里的密码副本。如果目的是减少 capture-locals 调试器/分析器拍下来的概率，那只能算 best-effort，不能算 defense-in-depth。
- 影响：纯 style；注释有夸大其词嫌疑。
- 推荐修复：要么删掉 `del plaintext` / `plaintext_password = None` 两行（无实际效果），要么把注释改成「仅减少 stack frame 被 capture 时的可见性，不保证 heap 上被覆盖」。

### F-16 ⚪ Info — `etag[:12]` 截断后 log 里有 etag 前缀；不构成 timing side-channel

- 文件：`server/routes/webui_sync.py:111-113`
- 问题描述：日志输出 etag 的前 12 字符。SHA256 hex 前 12 = 48 bit 信息泄漏。考虑到 etag 输入完全来自用户数据库（name + banned + hash），**hash 输出 ≈ 数据库摘要的指纹**。理论上多次抓 log 比对 etag 前缀可推断数据库变化频率（whitelist 增删节奏）。
- 影响：实际利用价值极低，但合规视角可质疑。
- 推荐修复：可选项。生产环境若敏感，把 etag 截断长度调到 6 或 0；本审计认为可保留。

### F-17 ⚪ Info — `webui_sync` 端点把 `is_banned` 内联进 sync snapshot；未来加新字段时 ETag 不变会让 client miss update

- 文件：`server/routes/webui_sync.py:35-57`
- 问题描述：`_compute_snapshot_etag` 只对 `name / banned / password_hash` 三字段 hash。如果未来加 `permissions` / `group` 字段也想 sync，C# plugin 不会感知（ETag 不变）。这是设计选择（"仅 sync-relevant 字段参与"），但缺一个 contract 文档（注释只提一句"sync-relevant 字段"，没有外部 spec）。
- 推荐修复：在文件顶部加一段「sync 协议字段列表」注释 / 或在 `.trellis/spec/` 落一个 contract 文档，让未来加字段的人有意识地更新 ETag formula。

### F-18 ⚪ Info — `_run_legacy_users_password_hash_migration` 总是 SELECT 全表 NULL hash 用户后扫描；大表场景下 startup latency 增加

- 文件：`nextbot/plugins/user_manager.py:269-283`
- 问题描述：每次启动都 `session.query(User).filter(User.password_hash.is_(None)).all()`。若 user 表有 10w+ 行且都已 backfill，每次启动仍走一次 index-less 扫描（password_hash 列没有索引）。
- 影响 / 触发条件：取决于 user 表规模；当前实例规模不大。
- 推荐修复：加 partial index `CREATE INDEX ... ON user(password_hash) WHERE password_hash IS NULL`（SQLite 3.8+ 支持），或加 `LIMIT 1` 探测 + 提前 return。可选。

### F-19 ⚪ Info — `_send_temp_private_password` 仅 catch `Exception`；`int(user_id)` 抛 `ValueError` 时被吞，但 message 含密码已被构造

- 文件：`nextbot/plugins/user_manager.py:194-199`
- 问题描述：`message = f"...{password}..."` 先构造 → 然后 `int(user_id)` 抛 ValueError → 进入 except → 但 `message` 局部变量仍持有密码字符串。capture-locals 工具仍能拍到。栈 unwind 后释放。
- 影响：理论 capture-locals 风险（DEBUG 工具 / sentry）。低概率。
- 推荐修复：先 `int(user_id)` 再构造 message。微优化。

### F-20 ⚪ Info — `_create_tshock_user_on_server` 中 `TShockRequestError` 的 `kind` 字段被丢弃

- 文件：`nextbot/plugins/user_manager.py:130-133`
- 问题描述：异常处理只取 `str(exc)`，丢失 `kind`（timeout / unreachable / invalid_url 等）。其他模块（如 ban_core）已经基于 `kind` 做了分类。审计范围内的实现风格不一致。
- 推荐修复：日志里把 kind 也带上，便于排障。

### F-21 ⚪ Info — `handle_add_whitelist` 在 IntegrityError 时 message 永远是"用户名称已被占用"，但 user_id unique 也会触发

- 文件：`nextbot/plugins/user_manager.py:471-475`
- 问题描述：同一 QQ 并发跑两次「注册账号」，第二次会走 `exists IS None` race → INSERT → user_id UNIQUE constraint → IntegrityError。当前回执仍然是 "用户名称已被占用"。
- 影响：UX 误导，但极端少见。
- 推荐修复：要么 catch 后改成 "已注册"（需检查 IntegrityError.params），要么文案改通用如 "注册冲突，请重试"。

---

## 总结

- 🔴 Critical：**2 项**（F-1 明文密码进 URL/log；F-2 plain HTTP 传输密码）
- 🟠 High：**3 项**（F-3 私聊失败用户被误导；F-4 schema migration 失败下系统全 broken；F-5 WebUI 创建路径不一致）
- 🟡 Medium：**5 项**（F-6 DEBUG 日志泄漏；F-7 httpx 异常 message 含 URL；F-8 重复注册 noisy warning；F-9 Cache-Control 应改 no-store；F-10 If-None-Match `*` RFC 不合规；F-11 broadcast 信号量绕过）
- 🟢 Low：**3 项**（F-13 私名跨 import；F-14 bcrypt fail-fast 缺注释；F-15 del 防御无实际效果）
- ⚪ Info：**6 项**（F-12 已 OOS / F-16-F-21 观察项）

**关键决策点（需要用户判断）：**

1. **F-1 + F-2 + F-7**：明文密码在 URL → log / 网络层。这是本次改动**最严重的安全风险**，三者联合放大。需要决定是否：
   - 走 POST + TLS 路径修复底层
   - 或者接受现状 + 单独治理日志脱敏
   - 或者把 task 暂停，先做架构上的 TShock REST 升级
2. **F-3**：用户回执文案是否如实陈述（"密码已发送"是否要根据 `_send_temp_private_password` 真实返回值改写）。
3. **F-4**：`ensure_user_password_hash_schema` 失败是否要从 warning 升级到 raise（fail-fast vs 容忍）。
4. **F-5**：WebUI 创建用户路径是否在本 task 收口、还是等「修改密码」task。
5. **F-9**：sync snapshot 响应头 `Cache-Control` 是否提升到 `no-store, private`。

**整体 verdict（不计 OOS / 排除项）：**

> ⚠️ **NEEDS DECISION** — 2 Critical（密码透传层面）+ 3 High（UX 误导 / migration fragile / 路径不一致）必须由用户拍板修复策略，不能仅靠 trellis-check 自修。代码风格 / 一致性问题（Low / Info）可以保留，不影响功能。
