# Backend 桶审计 — WebUI 服务器管理页面

- Target: `server/routes/webui_servers.py`（469 行，唯一审计文件）
- Date: 2026-05-15
- Auditor: trellis-research backend bucket
- Scope discipline: 严格仅审 servers 路由。其他基础设施（`server/routes/__init__.py`、`webui.py`、`nextbot/tshock_api.py`、`server_validation.py`、`db.py`、`large_image.py`）仅作 trust-boundary 参考与 `scope-out backlog` 记录，不计严重度。

Endpoint 清单：

| Method | Path | Handler |
|---|---|---|
| GET | `/webui/api/servers` | `webui_servers_list` (L59) |
| POST | `/webui/api/servers` | `webui_servers_create` (L106) |
| PUT | `/webui/api/servers/{server_id}` | `webui_servers_update` (L149) |
| DELETE | `/webui/api/servers/{server_id}` | `webui_servers_delete` (L192) |
| POST | `/webui/api/servers/{server_id}/test` | `webui_servers_test` (L230) |
| GET | `/webui/api/servers/{server_id}/plugin-config` | `webui_servers_plugin_config_get` (L307) |
| PATCH | `/webui/api/servers/{server_id}/plugin-config` | `webui_servers_plugin_config_update` (L346) |
| POST | `/webui/api/servers/{server_id}/plugin-config/verify-nextbot` | `webui_servers_plugin_config_verify_nextbot` (L421) |

共 8 个 endpoint。所有需要写操作的 endpoint 都走 `/webui/api/*` 前缀，由 `webui.py:195-226` 的 `add_webui_auth_middleware` 统一拦截未授权访问（401 JSON for `/webui/api/*`，302 for HTML）。Authn 边界在框架层已确认，本审计不重复评估。

---

## A. 安全

### A-1 [High] TShock token 在 list/create/update 响应中**完整明文返回** — 与 db.py:127 token-mask `__repr__` 的设计意图严重不一致

- 位置：
  - `_serialize_server` `webui_servers.py:34-42`，`token` 字段直接 `str(server.token)` 返回（L41）
  - 调用点：
    - list 响应：`webui_servers_list` L71 `serialized = [_serialize_server(item) for item in servers]` → L92 切片返回
    - create 响应：`webui_servers_create` L134 `data=_serialize_server(server)`（201 Created）
    - update 响应：`webui_servers_update` L179 `api_success(data=_serialize_server(server))`
- 严重度：**High**
- 触发概率：100%。任何已登入 webui 用户访问 `/webui/api/servers` 即可拿到所有 TShock token；前端 servers 页面默认调用此接口加载列表。
- 影响：
  1. TShock REST API token 等价于服务器后台凭据，泄漏后可远程踢人、ban、改世界、跑 `/v2/server/rawcmd` 任意命令。
  2. 浏览器历史 / 截图 / DevTools network tab / 代理日志全部留痕；webui token 一次泄漏即等同 RCE。
  3. 与 `nextbot/db.py:127-132` 显式 `__repr__` mask 的设计**自相矛盾**——repr 防 traceback / logger 泄漏，但 API 直出。
  4. PRD 关注点 #4 / #38 明确指明 "TShock token 泄漏（servers list 响应是否暴露 token，editor 表单是否回填 token）" 为本轮重点。
- 修复前：

```python
def _serialize_server(server: Server) -> dict[str, Any]:
    return {
        "id": int(server.id),
        "name": str(server.name),
        "ip": str(server.ip),
        "game_port": str(server.game_port),
        "restapi_port": str(server.restapi_port),
        "token": str(server.token),  # ← 完整明文
    }
```

- 修复后（建议方向，由实施 agent 决策）：
  1. list 响应不返回 token 字段（或返回 `"token_set": true/false`）
  2. detail / editor 加载时返回 mask 形式（如 `****` + 末 4 位 `XXXX`），或单独提供 `GET /webui/api/servers/{id}/token` 端点显式取（POST log audit）
  3. update / patch 接受 token 字段时，若客户端传"未变更"哨兵（如空串 + 显式 keep 标志）则保留原值；前端不再依赖回填
- 关联 finding：A-2（响应 token 泄漏的派生场景）、D-1（日志规范是 OK 的，反衬出 API 层缺失同等防护）

### A-2 [High] update endpoint 设计依赖前端回填 token，强制把 token 透出 → 形成 A-1 的设计正反馈

- 位置：`webui_servers_update` L149-189，特别是 L176 `server.token = validated.token`
- 严重度：**High**（与 A-1 同根，单独列以便修复同时考虑流程改造）
- 触发概率：100%（每次 editor "保存" 都强制写 token）
- 影响：
  - 当前流程：编辑表单 → GET list/detail 拿 token 回填到 input → 用户保存 → PUT 全量字段覆盖。要求 token 必经"明文出库 → 明文回前端 → 明文回库"。
  - 任何 servers list 拦截 / XSS / 浏览器扩展 / 截图都能拿到 token；A-1 单独 mask 列表是不够的——只要 update 仍要求 token 回填，editor 加载时仍需返回明文。
- 修复前：endpoint 总把 `validated.token` 写库（即使用户没改密码）
- 修复后（建议方向）：
  - PUT 允许 token 字段缺省 / null 表示"保留原值"；服务端在 `validated.token is None` 时跳过赋值
  - 或拆 endpoint：`PATCH /webui/api/servers/{id}`（不含 token）+ `PUT /webui/api/servers/{id}/token`（专项轮转）
  - 然后前端就不再需要从 GET 拿 token 回填，A-1 list/detail 才能彻底去 token

### A-3 [High] **全站缺少 CSRF 防护，session cookie 是 SameSite=Lax**——任何跨站 POST/PUT/DELETE/PATCH（含本审计的 8 个写端点中 6 个）在用户登入态下可被诱导触发

- 证据：
  - `server/routes/webui.py:140-148` set_cookie：`samesite="lax", secure=False`，无 CSRF token cookie，无 `X-CSRF-Token` 校验 middleware
  - `grep -rn "csrf" server/` 命中 0 行
  - 写端点：POST `/webui/api/servers`、PUT `/webui/api/servers/{id}`、DELETE `/webui/api/servers/{id}`、POST `/webui/api/servers/{id}/test`、PATCH `/webui/api/servers/{id}/plugin-config`、POST `/webui/api/servers/{id}/plugin-config/verify-nextbot`
- 严重度：**High**
- 触发概率：依赖钓鱼 / iframe 引导，但所有写动作均无第二层防护
- 影响：
  - SameSite=Lax 默认拦截跨站 POST 自动带 cookie，但**简单表单 POST（`enctype=text/plain` / `multipart`）+ top-level navigation** 仍能命中 lax 例外；DELETE / PUT / PATCH 大多被 lax 拦但部分浏览器边界行为不可依赖
  - 攻击者可诱导管理员点击恶意链接 → DELETE servers / PATCH plugin-config 改 TShock 行为
  - PRD 关注点 #37 "CSRF（POST / PUT / DELETE / PATCH 端点）" 明确要求列举
- 修复前：仅依赖 cookie SameSite=Lax + `secure=False`
- 修复后（建议方向）：
  - 引入 double-submit cookie 或 sync token 模式：登录时下发 `csrf_token` cookie + 前端 `X-CSRF-Token` header 写每个写请求；middleware 校验
  - 或全站要求 fetch 写请求带 `X-Requested-With: XMLHttpRequest`（form-post 不会自动带），middleware 校验
  - 把 cookie 升级 `samesite="strict"`（如果 webui 不需要跨站跳转登录后保留 session）
- scope-out 提示：CSRF middleware 落点在 `server/routes/webui.py`，非本任务文件；修复时跨模块协作

### A-4 [Medium] `plugin_config_update` 把客户端任意 key/value 透传给 TShock `/nextbot/config/update`，无字段白名单 / 类型约束

- 位置：`webui_servers_plugin_config_update` L346-418，特别是 L362-371（构建 `params`）和 L388-390（透传 `params=params` 给 `request_server_api`）
- 严重度：**Medium**
- 触发概率：取决于 TShock NextBot 插件本身字段过滤强度。如果服务端无白名单（"trust webui"），任意未知 key/value 都会落到插件配置
- 影响：
  1. 客户端可注入未知字段，污染插件配置 / 触发未知 code path
  2. value 全部 `str(value)`（L371），bool 显式转 `"true"/"false"`（L367），None 转空串（L369）——但 list/dict/嵌套结构会被字符串化成 `"['a','b']"` 这种无意义字符
  3. key.strip() 只去前后空格，不限制字符集；`?<key>=<val>` 通过 httpx params 自动 URL-encode，所以**没有 CRLF / 路径注入**
  4. 字段数无上限：`len(data)` 多大都会构造 params dict。可被滥用做内存放大或慢 RPC（缓解：tshock_api stream + 250MB cap）
- 修复前：

```python
for key, value in data.items():
    if not isinstance(key, str) or not key.strip():
        continue
    ...
    params[key.strip()] = str(value)
```

- 修复后（建议方向）：
  - 维护字段白名单（与 TShock NextBot 插件支持的 config key 对齐），白名单外的 key 直接 422
  - 对每个白名单字段做类型约束（bool / int / str + 长度），不再依赖 `str(value)` 兜底
  - 限制 `len(params)` 与单个 value 长度上限，避免 RPC 放大

### A-5 [Medium] `_extract_upstream_error` 把上游 `payload.error` **原样透传**到 API `error.message`（L290-296，调用方 L334、L407、L455）

- 位置：`_extract_upstream_error` L290-296；调用方 L334、L407、L455
- 严重度：**Medium**
- 触发概率：100%（plugin-config 与 verify-nextbot 失败时都会触发）
- 影响：
  - 与 CLAUDE.md 第 6/7 条 "error.message 应仅返回有效原因，不拼接动作 + 结果" 在方向上一致——**这部分行为是好的**
  - **但**：上游错误若包含路径 / 内部结构（如 TShock 插件版本号 / 文件路径 / SQL fragment / 配置文件 dump），会一路透到前端 toast。需要确认 TShock NextBot 插件 `/nextbot/config/*` 的错误体不会泄漏内部信息
  - 此外 `payload.error` 来自外部不可信源——前端如直接 innerHTML 渲染则有 XSS 风险（前端桶审）
- 修复前：原样 strip 后透传
- 修复后（建议方向）：
  - 与前端约定：仅取业务无关短码（如 `payload.error_code`）+ 本地化 message，不透传 `payload.error` 原文
  - 或在后端做白名单（已知 error_code 集合），未知码统一返回 `"上游错误"`，原文写日志

### A-6 [Medium] `_extract_upstream_error` 对 `response.payload` 不做 `isinstance(dict)` 二次保险，依赖 `tshock_api.request_server_api:196-198` 把非 dict 归一化为 `{}`

- 位置：`webui_servers.py:290-296`
- 严重度：**Medium**（属于 defense-in-depth，单边脆性 OK 但有耦合）
- 触发概率：低（tshock_api 已兜底）
- 影响：若未来 `request_server_api` 行为改变（如返回 list payload），`_extract_upstream_error` 的 `payload.get("error")` 会 AttributeError 串到 500
- 修复前：

```python
def _extract_upstream_error(response: Any) -> str:
    payload = getattr(response, "payload", {}) or {}
    if isinstance(payload, dict):
        raw = payload.get("error")
        ...
```

- 修复后：增加 `if not isinstance(payload, dict): return get_error_reason(response)` 前置守卫（其实当前代码有 `if isinstance(payload, dict)` 但用法对，本条 informational，可仅备注）
- 注：此条偏 nit，可与 A-5 合并修

### A-7 [Medium] DELETE 端点的 ID 重排（L209-212）只在 webui 这一处做 — 业务一致性风险（**不算安全，列在此处因为涉及主键稳定性**）

- 位置：`webui_servers_delete` L209-212：

```python
session.query(Server).filter(Server.id > deleted_id).update(
    {Server.id: Server.id - 1},
    synchronize_session=False,
)
```

- 严重度：**Medium**（安全归属：主键被 webui 主动 mutate；其他模块若 cache server_id 会引发引用错乱）
- 触发概率：100%（每次删除中间 ID 时触发）
- 影响：
  1. ID 不稳定：其他模块（broadcast / large_image semaphore pool / 命令历史 / 审计日志）若按 server_id 持久化引用，DELETE 后会引用到错的 server
  2. `release_server_semaphores_all(deleted_id)`（L215）在 commit 之后调用，**用的是删除前的 id**，OK；但其他 server 的 id 已被 -1，semaphore pool 中**其他 key 未做迁移**——下次 N+1 号 server 取 semaphore 时会拿到 N 号的旧 pool 引用（已知 R8 M-5 修过，但本次审计要确认 reindex 后 pool key 是否同步）
  3. 没有事务隔离保障：`session.flush()` 后立即批量 update，如果 ID 是其他表外键且 ON UPDATE 没 cascade，会出现悬挂 ref
- 修复前：删除后批量 reindex
- 修复后（建议方向）：
  - 移除批量 reindex，保留稀疏 ID（推荐）；或
  - 增加显式注释 + 在事务内同时迁移所有依赖该 server_id 的引用（包括 large_image semaphore pool key 迁移）
- scope-out 提示：reindex 副作用范围需跨模块评估；本任务仅记录

### A-8 [Low] path 参数 `server_id: int` 由 FastAPI 自动转换，FastAPI 内置整数校验 OK；但未设 `ge=1`/上界

- 位置：所有 `/webui/api/servers/{server_id}` 端点（L149、192、230、307、346、421）
- 严重度：**Low**
- 触发概率：低
- 影响：用户传 `0` / 负数 / `1e18` 路径都会进入 handler 走一次 DB 查询。当前 SQLAlchemy filter 不会注入，但白白消耗一次 IO；上界缺失也让 enumeration 探测略容易
- 修复后（建议方向）：FastAPI 路径参数加 `server_id: int = Path(ge=1, le=2**31 - 1)`

### A-9 [Low] `webui_servers_list` 关键字过滤在内存层做（L72-85），先把所有 server 全 serialize 再过滤；keyword 来自 `request.query_params.get("q")`，无长度上限

- 位置：L66-85
- 严重度：**Low**（项目实际 server < 10 台，性能无影响）
- 触发概率：低
- 影响：
  - keyword 没有长度上限，攻击者可发超长 `q` 触发额外 CPU——但拼接 / lower 都是 O(n)，且 n<10，可忽略
  - 不算 SQL 注入（不进入 query）
- 修复后（建议方向）：`q` 截断为合理上限（如 200 char），并在 ORM 层用 `LIKE` 做过滤（也顺带修 B-1）

### A-10 [Info] SQL 注入审查通过

- 全文 8 个 endpoint 全部使用 ORM `session.query(Server).filter(...)`，无 `.execute(text(...))` / 字符串拼 SQL
- `validate_server_payload_dict` 在写之前做参数校验（newline / 长度 / 端口范围）
- DELETE 的 `Server.id > deleted_id` 用 ORM column expression
- **结论：无 SQL 注入风险**

### A-11 [Info] 权限边界审查 — webui 单管理员模型 OK

- `add_webui_auth_middleware`（webui.py L195-226）对 `/webui/api/*` 路径统一拒绝未授权
- webui 是单管理员模型，无 RBAC，list/detail/CUD 全部对等
- 与 PRD 描述一致："当前 webui 是单管理员模型，OK 但需确认"

### A-12 [Info] SSRF 风险评估 — 边界由 server_validation + tshock_api 双重防御覆盖

- `request_server_api`（tshock_api.py L111-205）使用 `httpx.URL.build` 显式构造 URL，避免拼接漏洞；`server_validation._normalize_host` 做 newline / 长度校验
- ip 字段允许任意 hostname（包括内网），属于产品设计：webui 管理员要能添加任意 TShock 后端
- /test、/verify-nextbot、/plugin-config 都需要先创建 server 才能调用；写 endpoint 已要求 webui session
- **结论**：技术上 webui admin 可指向内网 TShock 端口，但**当前威胁模型是 admin 等价于服务器主，**不属于额外漏洞。仅记录

### A-13 [Info] JSON 反序列化攻击面

- `read_json_object`（`__init__.py:51-68`）做了 `isinstance(payload, dict)` + JSONDecodeError 兜底
- create/update 在拿到 dict 后立即 `_validate_server_payload(data)` → `validate_server_payload_dict`，再校验字段存在性 + 类型 + 边界
- plugin-config-update 增加 `isinstance(data, dict) or not data` 二次守卫（L355）
- **结论：反序列化攻击面已闭合**

---

## B. 性能

### B-1 [Medium] list 端点全量 serialize 后再分页 + 内存关键字过滤（L70-92）

- 位置：`webui_servers_list` L70-92
- 严重度：**Medium**（项目实际 < 10 台，可忽略；但属于 API 设计反模式）
- 触发概率：100%（每次 list 调用）
- 影响：
  1. `session.query(Server).order_by(Server.id.asc()).all()` 把全表 load 进内存（L70），即使 `per_page=1` 也全量读
  2. `_serialize_server` 对全部行执行（L71），keyword 过滤也在 Python 层（L72-85）
  3. 分页只是 `serialized[offset : offset + limit]`（L92）——分页 meta 准确，但 IO 全量
  4. 如果未来 server 数量增长（项目设计上 < 10），无影响；但与 dashboard / users / groups 的分页一致性差
- 修复前：全量 query + 内存过滤 + 切片
- 修复后（建议方向）：
  - ORM `.filter(or_(Server.name.like(...), Server.ip.like(...)))` + `.offset(...).limit(...)` + 单独 `count()` 查 total
  - 或维持现状但补 docstring 注明"项目假设 < 10 台"

### B-2 [Medium] DELETE 端点的批量 reindex 是隐式全表更新（L209-212）

- 位置：`webui_servers_delete` L209-212
- 严重度：**Medium**
- 触发概率：100%（每次删除）
- 影响：
  - 删除 id=1 会触发 N-1 行 UPDATE（id - 1）
  - `synchronize_session=False` 让 SQLAlchemy 跳过 session 同步——后续若有 server 对象在 session 内被使用会读到老 ID
  - SQLite 单文件锁 + 全表 UPDATE，并发其他 webui 写操作会被阻塞
  - 与 A-7 同根
- 修复前：reindex 全部 id > deleted
- 修复后：见 A-7 修复方向，移除 reindex 是首选

### B-3 [Medium] **每个 endpoint 用 `get_session()` + 同步 SQLAlchemy 在 async handler 中阻塞 event loop**

- 位置：所有 8 个 endpoint
- 严重度：**Medium**（项目整体模式，非 servers 路由独有；与 `db.py` 整体使用方式相关）
- 触发概率：100%
- 影响：
  - 每次 `session.query(...).all()` / `.commit()` 都在 event loop 上同步执行；SQLite 单文件、查询快，所以业务上影响小
  - 但 webui_servers_test / verify-nextbot 在做完 DB query 后还有 RPC（5s ~ 10s），DB query 阻塞 + RPC 阻塞**串行**叠加
  - 与 PRD 关注点 #C-3 "async def 内同步 SQLAlchemy（项目整体模式）" 一致
- 修复前：直接 sync ORM 调用
- 修复后（建议方向）：项目级议题，本任务记录后由统一 DB 改造收口（不在 servers scope）

### B-4 [Medium] `/test` endpoint 使用 `request_server_api` **默认 5s timeout**（read），其他维度连接 5s / write 10s

- 位置：`webui_servers_test` L254 `await request_server_api(server, "/tokentest")`（无 timeout 参数）
- 严重度：**Medium**
- 触发概率：服务器无响应 / DNS 慢时触发
- 影响：
  - tshock_api 默认 `timeout=5.0`（L116）→ 实际 `httpx.Timeout(connect=5.0, read=5.0, write=10.0, pool=5.0)`
  - 5s read OK；但若用户点 "测试" 期间服务端 hang，前端无明显进度反馈（仅 5s 后报"无法连接"）
  - 对比 verify-nextbot L435 显式 10s（R2 已修），plugin-config (L317、L389) 也是默认 5s
- 修复前：默认 5s
- 修复后（建议方向）：
  - 显式 timeout（如 `timeout=10.0`，与 R2 verify-nextbot 一致），并加注释
  - 或前端在 RPC 进行时显示 "测试中" loading（前端桶覆盖）

### B-5 [Low] plugin-config GET / PATCH 与 verify-nextbot 是 3 个独立 RPC，没有 fan-out 优化机会（**单次只调一个 server**）

- 位置：L307、L346、L421
- 严重度：**Low / Info**
- 触发概率：—
- 影响：servers 页面是单 server 操作，无需 `server_broadcast.broadcast`（这是 dashboard / commands 多 server 场景的工具）。本审计无需改动
- 注：PRD 关注点 #C-5 提到"多服务器 fan-out：如果有，是否用 `server_broadcast.broadcast`"——结论是 servers 单独不涉及

### B-6 [Low] create 端点用 `func.max(Server.id) + 1` 计算下一个 ID（L120-122），无 UNIQUE 冲突保护

- 位置：`webui_servers_create` L120-122
- 严重度：**Low**
- 触发概率：极低（两个 webui session 同时创建 server，时间窗口 < 几 ms）
- 影响：
  - 并发两次 POST → 两次 `func.max` 都读到 N → 两次 `Server(id=N+1)` → 二次 commit IntegrityError → 500 内部错误
  - 当前 webui 是单管理员，并发 POST 几乎不会发生
- 修复前：用户态 max+1
- 修复后（建议方向）：
  - 让 `Server.id` 走 SQLite autoincrement（schema 改动较大，跨 scope）
  - 或捕获 IntegrityError 后 retry 一次
- scope-out 提示：DB schema 改动跨 scope；建议 backlog

---

## C. API 设计（按 CLAUDE.md 第 6/7 条 + api-design 规则）

### C-1 [Medium] 所有 500 路径都返回 `message="内部错误"`——上游错误原因丢失

- 位置：L100、L143、L186、L223、L240、L268、L330、L403、L451
- 严重度：**Medium**
- 触发概率：500 路径触发时
- 影响：
  - 与 CLAUDE.md 第 7 条 "error.message 应仅返回有效原因，不拼接动作 + 结果" 方向一致——**不应回拼业务前缀，OK**
  - 但 "内部错误" 是泛化兜底；details 字段未填；前端拿不到任何原因，只能展示 "保存失败，内部错误"
  - 对比 plugin-config-update L407 上游错误能透传 `_extract_upstream_error(reason)` —— 设计不一致
- 修复前：

```python
return api_error(status_code=500, code="internal_error", message="内部错误")
```

- 修复后（建议方向）：
  - 保留泛化 `"内部错误"` message（避免泄漏 stack trace），但 details 补 trace_id（如果有）便于前端复制给运维
  - 或区分错误来源：DB 异常 vs 序列化异常 vs 未知异常，code 字段细化（`db_error` / `serialize_error` / `internal_error`），message 仍模糊

### C-2 [Medium] `webui_servers_test` 把"不可达"映射为 200 + `{reachable: false}`（L256-262、L281-287）—— 与其他 endpoint 不一致

- 位置：L256-262 (TShockRequestError)、L281-287 (TShock 业务失败)
- 严重度：**Medium**（属于 API 一致性，由 dashboard R3 等场景已沉淀；本端点设计上**故意**走 200，因为 test 本身就是探针）
- 触发概率：100%（测试不可达时）
- 影响：
  - "不可达" 走 200 + payload field 表达，跟 plugin-config-get/update 用 502 upstream_error 表达**不一致**
  - **但 test 是探针端点，200 + bool 字段是 RESTful 探针的合理设计**（POST `/test` 的语义是"做一次探测，返回探测结果"）
  - 真正风险：前端 OK / 错误展示分支需要识别 `data.reachable === false`，而其他 endpoint 是按 HTTP status 分支
- 修复前：200 + reachable bool
- 修复后（建议方向）：
  - 保留现状但在 spec 注明"探针端点统一用 200 + 业务字段"
  - 或改 503 + `error.code=server_unreachable`，前端按 HTTP status 分支

### C-3 [Medium] update endpoint 接受 token 字段不允许"保留原值"——与 A-2 同根，列为 API 设计议题

- 位置：`webui_servers_update` L149-189
- 严重度：**Medium**
- 修复前：全字段强制 + token 必填
- 修复后（建议方向）：见 A-2

### C-4 [Low] PATCH `/plugin-config` 拒绝空对象返回 `"未提供任何更新字段"`（L355-360、L373-378）

- 位置：L355-360、L373-378
- 严重度：**Low**
- 影响：
  - message 是合规的（"原因，不拼接动作"）
  - 但前端展示文案应是 "保存失败，未提供任何更新字段"（前端拼接）；CLAUDE.md 第 7 条 OK
- 备注：合规，列为 Info

### C-5 [Low] DELETE 返回 `Response(status_code=204)`（L217）——无 body，符合 REST 规范

- 位置：L217
- 严重度：—
- 备注：合规，列为 Info

### C-6 [Low] create 返回 `201 + Location` header（L132-136）——合规

- 备注：合规

### C-7 [Medium] `code` 字段命名不统一

- 现有 code：`validation_error`、`internal_error`、`not_found`、`upstream_error`
- 缺失：DELETE 没有显式 200 success code；test endpoint 200 没有 error 但没有 success 业务码
- 与 webui 其他模块（dashboard / users / groups）对比一致性需要主代理 verify-pass2 复核

---

## D. 日志 / 可观测性

### D-1 [Info] 日志总体规范一致，token 未在日志中泄漏

- 全文 18 处 `logger.info` / `logger.warning` / `logger.exception`，全部使用 `动作 + 结果 + 关键上下文（server_id / name / reason）` 句式，符合 CLAUDE.md 日志规则
- 关键字段（server_id / field_count / probeStatus）以 key=value 形式 inline 拼接
- **`server.token` 从未直接出现在日志参数中**——defense-in-depth 与 `db.py:127` `__repr__` mask 一致
- 与 `.trellis/spec/backend/logging-guidelines.md` 第 38 行 "good examples" 自洽

### D-2 [Low] 日志缺少 `client_ip` / `user_agent` 字段，与 webui.py:212-214 login 路径风格不一致

- 位置：所有 18 处日志
- 严重度：**Low**
- 影响：
  - servers 路由 CRUD 日志只有 `server_id` / `name`，无操作人 IP / UA
  - webui 是单管理员，actor 唯一，但日志缺失 client_ip 让审计追溯依赖反查 access log（已知 Uvicorn access log 已禁用，见 logging-guidelines.md L115）
  - 对比 webui.py login 路径明确记录 client_ip + user_agent → CRUD 路径缺失
- 修复前：

```python
logger.info(f"创建服务器成功：server_id={server.id}，name={server.name}")
```

- 修复后（建议方向）：
  - 提取 `request: Request` 参数（DELETE / test / verify-nextbot 当前未取 Request，需补）
  - 在 success / warning 日志附加 `client_ip={client_ip}`（沿用 webui.py:151-159 的 `_client_ip` helper）
- scope-out 提示：`_client_ip` 是 webui.py 私有 helper；若改成跨模块共享需要少量重构

### D-3 [Low] DELETE 日志（L216）已记录 `deleted_id` 和 `deleted_name`——合规；但缺 reindex 影响行数

- 位置：L209-216
- 严重度：**Low**
- 影响：批量 reindex 实际更新了 N-deleted_id 行（A-7/B-2），日志只 log 删除事件，未 log reindex 行数。审计回溯时无法判断"为什么 id=5 的 server 突然变成 id=4"
- 修复后：reindex 后补 `affected_rows=update_result.rowcount` 字段

### D-4 [Info] `logger.exception` 在 500 路径上覆盖完整

- L96、L139、L182、L220、L236、L264、L328、L401、L447 共 9 处
- 与 `logging-guidelines.md` "logger.exception 用法" 一致

### D-5 [Info] 上游错误透传日志（L335-337、L408-410、L456-458）

- 把 `_extract_upstream_error(response)` 的 reason 落到 logger.warning + 同时返回前端
- 同 A-5 关注点；日志侧记录是合理的（运维需要看到完整 reason），但响应侧需要白名单（A-5 修复建议）

---

## 结论 + 修复优先级

### Critical
- 无

### High（必修）
1. **A-1** TShock token 在 list / create / update 响应中明文返回 → 设计 mask / 末 4 位 / 单独取 token 端点
2. **A-2** update 强制要求 token 回填 → 改"未变更跳过赋值" 或拆 token rotate endpoint（A-1 的前置条件）
3. **A-3** 全站缺 CSRF 防护 + cookie SameSite=Lax → 引入 double-submit token 或 `X-Requested-With` header 校验

### Medium
4. **A-4** plugin-config-update 无字段白名单 → 加 enum 白名单 + 类型约束
5. **A-5** 上游 error.message 原样透传 → 与前端约定白名单 + 本地化 message
6. **A-7 / B-2** DELETE 批量 reindex 主键不稳定 → 移除 reindex（推荐）或同步迁移依赖
7. **B-1** list 全量 query + 内存过滤 → 改 ORM filter + 分页（项目假设 < 10 OK，可降级 Low）
8. **B-3** async handler 内同步 SQLAlchemy → 项目级 backlog
9. **B-4** `/test` 默认 5s timeout → 显式 10s 或 spec 注明
10. **C-1** 500 message 泛化 `"内部错误"` → 保留 message 但补 trace_id details
11. **C-2** test endpoint 200 + reachable bool → 与 HTTP status 分支策略统一
12. **C-3** update token 必填 → 见 A-2

### Low / Info
13. **A-6** `_extract_upstream_error` payload 非 dict 兜底（与 A-5 合并）
14. **A-8** path 参数无 `ge=1` 边界
15. **A-9** keyword 参数无长度上限
16. **B-6** create 主键并发风险
17. **C-4 / C-5 / C-6** message / status 合规
18. **D-2** 日志缺 client_ip / user_agent
19. **D-3** DELETE reindex 行数未 log

### Info（合规 / 无需改）
- A-10 SQL 注入：无
- A-11 权限边界：单管理员 OK
- A-12 SSRF：威胁模型内
- A-13 反序列化：闭合
- B-5 fan-out：不适用
- D-1 / D-4 / D-5 日志规范一致

### scope-out backlog（非 servers scope，仅记录）
- CSRF middleware 落点在 `server/routes/webui.py`（A-3）
- `_client_ip` helper 跨模块复用（D-2）
- async DB 改造（B-3）
- Server.id schema 改 autoincrement（B-6）
- A-7 / B-2 reindex 副作用涉及 large_image semaphore pool key 迁移、其他模块对 server_id 的持久化引用
- A-5 上游 error message 白名单需要跟 TShock NextBot 插件协同
