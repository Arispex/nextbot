# Research: server 核心基础设施安全 / 性能 / UX / 文案审计（Bucket A）

- **Query**: 全量审计 server 核心基础设施 6 文件
- **Scope**: internal（严格限定 6 文件；跨模块只做 scope-out 引用）
- **Date**: 2026-05-15

## 审计范围

- `server/__init__.py` (2 LOC)
- `server/web_server.py` (429 LOC) —— FastAPI 入口、CORS / 中间件 / 启停 / 进程管理
- `server/server_config.py` (148 LOC) —— 配置加载
- `server/page_store.py` (39 LOC) —— 页面渲染数据缓存
- `server/screenshot.py` (253 LOC) —— Playwright 截图服务
- `server/settings_service.py` (424 LOC) —— 设置持久化

参考标准化模式：servers R2 (`1355521`) / settings R1 (`05-15-audit-webui-6`)。

---

## Findings 汇总

| 严重度 | 数量 |
|---|---|
| Critical | 1 |
| High | 7 |
| Medium | 13 |
| Low | 11 |
| **合计** | **32** |

---

## Critical

### CRIT-1 `/render/*` 渲染端点完全无认证，token UUID 仅为弱信道，外网暴露即等于数据外泄

**File**: `server/web_server.py`:35-36, 358-391；`server/page_store.py`:25-39（cache 主体）；`server/routes/render.py`:52-134（scope-out 引用，仅说明消费侧）

**Dimension**: security

**Issue**：`create_*_page` helpers 把 `_build_internal_base_url` 写成 `http://127.0.0.1:{port}`（line 35-36），但 settings 中的 `web_server_host` 默认是 `"127.0.0.1"`（`server/server_config.py`:123），用户在 WebUI 设置中可以把 host 改成 `0.0.0.0` 并/或者 `web_server_public_base_url` 改成公网 URL（`server/settings_service.py`:263-264 接受任意 http/https 域名），意味着 `/render/*` 路径可能被外网访问 —— 而 `_run_server`（`server/web_server.py`:404-410）的 uvicorn 直接 bind `settings.host`，没有任何"仅 127.0.0.1 才暴露 render"的护栏。`create_app`（line 358-391）也没有把 `render_router` 挂在不同 host 上。

后果：
1. `/render/<token>` 携带玩家敏感数据：仓库内容、抽奖凭证、user_info（QQ + 签到 + 权限组）、admin_list（管理员 QQ 列表）—— UUID4 hex 是 122 bit 安全，但 **token 通过 V11 base64 推送给 OneBot bot 在群里发图**，恶意群成员可以从消息日志 / OneBot 历史拿到该 URL 并直接 GET 拉数据。
2. `page_store` cache TTL 600s（`server/page_store.py`:8），10 分钟窗口足以离线分析。
3. 文件本身的 `_build_internal_base_url` 写死 127.0.0.1 给截图浏览器用，但 OneBot V11 fallback 非 base64 情形会把这条 URL 原样发回客户端（参见 `nextbot/screenshot_render.py`:30 scope-out）—— 此时 127.0.0.1 是 bot 主机视角，客户端打不开，但**如果 host 改 0.0.0.0 + public_base_url 改公网**，则 URL 在群里就是可外网访问的。
4. `_render_page` 仅检查 `payload.get("type") == page_type`（scope-out `render.py`:31），无任何 token 持有者身份比对。

**Fix sketch**：
- 当前文件最小修：在 `create_app` 中给 `render_router` 加一道 host filter middleware，拒绝非本机请求（`request.client.host in {"127.0.0.1", "::1"}`），并把 `/health` 单独放行。
- 或：把 `/render/*` 单独挂在 internal uvicorn instance（host 强制 127.0.0.1，port 独立）；`/webui` 与 `/render` 物理隔离。
- 长期：`create_page` 时绑定 owner_user_id；`/render/<token>` 校验请求 IP 在白名单（截图浏览器是 127.0.0.1）；外网请求一律 403。
- 文档化：`web_server.py`:35 注释中显式声明"render 端点必须仅绑定 loopback"。

**Risk if unfixed**：用户改 host 到 `0.0.0.0` 或在反向代理下，整个 `/render/*` 暴露公网；任何拿到 OneBot 消息历史的人可越权拉所有玩家仓库 / 管理员清单。属于配置触发的高破坏面，且 settings 页面已允许编辑 host（最小 reproduce 步骤：登入 WebUI → 把 `web_server_host` 改 `0.0.0.0` → 触发任意 `/render/inventory/<token>` 命令 → 截图 URL 即可被外网拉到）。

---

## High

### H-1 `WebUI Token` 以 WARNING 级别打到日志，长期落盘 = 凭据归档泄漏面

**File**: `server/web_server.py`:402

**Dimension**: security / copy

**Issue**：`logger.warning(f"Web UI Token：{settings.webui_token}")` 把完整 webui token 以 WARN 级别写日志 —— 日志聚合工具（journald / loki / syslog forwarder）会长期保存 WARN 及以上级别。NextBot 同进程 logger 还可能被同步到 OneBot 转发频道（参见 `nonebot.log.logger` 全局配置 scope-out）。

对照已修复：servers R2 H-1 token chain 已经规范"token 一旦出现就 mask"，server_config.py 的 `_load_or_create_webui_auth` 自身也只写 `.webui_auth.json`（line 91-100），未打 token。但 `_run_server` 启动横幅却直接 WARN 一条完整 token —— 启动一次 log 就泄漏一次。

`logger.info` 也连带打了 host:port + auth_file_path（line 398, 401），属于运维信息，可接受；token 本身不应该在任何 logger 里出现。

**Fix sketch**：
- 把 line 402 改为 `logger.info(f"Web UI Token 已写入：{settings.auth_file_path}，请通过该文件获取（首次启动后不再打印）")`；
- 若需要"首次启动"的便利，参考 `auth_file_created` 标志：仅在 `settings.auth_file_created` 为 True 时打 mask 后的前 8 字符 + "…"；
- WARN level 同步降级为 INFO（token 信息从语义上不是 warning）。

**Risk if unfixed**：日志聚合后凭据归档泄漏；接管 webui 等于 H-A1（servers R2）的反方向"凭据接管整个 bot"。

---

### H-2 `_run_server` 在 daemon 后台线程中 `uvicorn.run` 启动，无 graceful shutdown，进程退出时长连接 + Playwright + 监听端口 FD 全部泄漏

**File**: `server/web_server.py`:394-424

**Dimension**: security / perf

**Issue**：
1. `start_web_server`（line 413-424）使用 `daemon=True` 线程跑 `uvicorn.run(app, host=..., port=...)`。daemon 线程在主进程退出时**直接被强杀**，uvicorn 自带的 SIGTERM handler 不会触发 → uvicorn 不会优雅关闭 keep-alive 连接 / 不会调用 `app.on_shutdown` → 因此 `nonebot.get_driver().on_shutdown(_session.close)` （`server/screenshot.py`:253）的 playwright 关闭钩子**永远不会执行**（NoneBot driver shutdown 是 nonebot 主驱动的事件循环，但 uvicorn 跑在另一个线程，两个 event loop 互不通；NoneBot shutdown 触发的 `_session.close()` 是其自己的 loop 里的 coroutine —— 而 playwright `_session` 是被 uvicorn 线程里的截图请求建立的，跨线程 / 跨 loop 无法 close）。
2. uvicorn 的 `signal_handlers=True`（默认）在子线程里**会失败**：Python 的 `signal.signal` 仅允许在主线程调用 → uvicorn 启动时 silently 丢失 signal handler，SIGTERM / SIGINT 退场行为不可预测。
3. 没有 `start_web_server` 的对应 `stop_web_server`，模块对外仅暴露启动，无关闭。
4. 模块内 `_server_started = False` + `_server_lock` 仅防重复启动，无停启反复场景下的 fd reuse 校验。

对照已有：`screenshot.py`:243-253 的 `atexit.register(_atexit_close)` + NoneBot `on_shutdown` 是 best-effort fallback，但只覆盖 playwright，不覆盖 uvicorn。

**Fix sketch**：
- 推荐：保存 `uvicorn.Server` 实例（`server = uvicorn.Server(uvicorn.Config(app, host, port, log_level, access_log=False))`），用 `asyncio.run(server.serve())` 跑在显式 loop；模块退出时调 `server.should_exit = True`；
- 显式 `uvicorn.Config(..., loop="asyncio", lifespan="on")` 并在 NoneBot driver `on_shutdown` 中把 `should_exit` 置位 + asyncio.run_coroutine_threadsafe；
- 最小修：把 `uvicorn.Config(..., reload=False, workers=1)` 显式传入；在 `_run_server` 末尾用 `try/finally` 把 `_server_started` 复位。
- 文档化：daemon thread 模型在 reload 场景下不可恢复，需配合 `os.execv`（settings restart 链路）"软重启"补偿。

**Risk if unfixed**：进程退出时端口 FD 与 playwright 子进程未正确释放；重启间隙端口被占；长期跑出现 fd 泄漏。

---

### H-3 CORS 完全缺失：任意源浏览器都可以发起跨站请求到 `/webui/api/*`、`/render/*`

**File**: `server/web_server.py`:358-391

**Dimension**: security

**Issue**：`create_app` 没有挂任何 CORS middleware（FastAPI 默认拒绝跨域 OPTIONS preflight，但**简单请求**——`GET` / `POST` w/ `Content-Type: application/x-www-form-urlencoded` / multipart——会直接发出，浏览器看不到响应但服务端已经执行了状态变更）。结合 `add_webui_auth_middleware` 仅检查 session cookie + samesite=lax，恶意页面可：
1. 提交 `<form action="https://victim/webui/api/settings" method="POST">` 触发任意写入（lax 在 top-level navigation 携带 cookie）；
2. `<img src="https://victim/render/inventory/<known-token>">` 拉别人仓库截图链路（虽然不返回内容给攻击方 JS，但服务端日志 / cache 会被污染）；
3. `/health` 端点（line 387-389）无任何 host / origin 校验，被外网用作存活探测点。

settings R1 H-3 已经识别"CSRF 全局化"为 backlog，但目前没有任何文件层防护。`add_security_headers_middleware`（`server/routes/webui.py`:250 scope-out）只对 `/webui` 路径设响应头，**`/render`、`/health`、`/assets/*` 不设**。

**Fix sketch**：在 `create_app` 中显式：
- 加 CORS middleware：`from fastapi.middleware.cors import CORSMiddleware; app.add_middleware(CORSMiddleware, allow_origins=[settings.public_base_url], allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])`；
- 在 webui_auth middleware 之上加 CSRF（Origin/Referer 校验）中间件；
- `add_security_headers_middleware` 覆盖路径从 `/webui` 扩展到全部（含 `/render`、`/health`、`/assets`）；
- `/health` 端点考虑限定 `request.client.host == "127.0.0.1"` 或返回最小 payload。

**Risk if unfixed**：恶意页面可通过 form / img 标签构造 CSRF，越权改 .env 配置 / 触发重启 / 拉敏感截图链路。

---

### H-4 `/health` 端点无认证、无来源限制，结合 host=0.0.0.0 暴露版本探测面

**File**: `server/web_server.py`:387-389

**Dimension**: security

**Issue**：`@app.get("/health")` 永远返回 `{"status": "ok"}`，且未挂在 `/webui` 前缀下，因此 webui_auth_middleware 不拦截。配置 host=0.0.0.0 时该端点对公网开放：
1. 探活探测面：攻击者可以无成本判断 nextbot 是否运行；
2. 配合 Nonce port scan 可指纹识别 FastAPI（响应头 `server: uvicorn`、`Content-Length: 15` 等）；
3. 没有 rate-limit，可作为反射放大探测器。

对照 webui token 验证：`/webui/login` 已挂在 `/webui/login` 路径下，自然进入 webui_auth_middleware 的"白名单放行"分支（`server/routes/webui.py`:199-203 scope-out）。`/health` 不在任何 webui 路径，所有人都能直接访问。

**Fix sketch**：
- 最简：把 `/health` 改为 `/webui/api/health` 并加入 webui_auth_middleware 的 free-path 白名单；或限定 `if request.client and request.client.host in {"127.0.0.1", "::1"}: return ...; else: raise HTTPException(404)`。
- 或：返回去掉 server 头的 minimal response（`response.headers.pop("server", None)`）。
- 长期：把 `/health` 挂在独立的"运维端口"（仅 127.0.0.1）。

**Risk if unfixed**：bot 实例的公网暴露被低成本指纹识别，结合其他漏洞（CRIT-1 / H-3）可被定向攻击。

---

### H-5 Playwright 截图无 URL whitelist，`screenshot_url` 接受任意 URL 即 SSRF 武器化面

**File**: `server/screenshot.py`:111-220

**Dimension**: security

**Issue**：`screenshot_url(url, output_path, ...)` 把 `url` 直接传给 `page.goto(url, ...)`（line 133-137），无任何 scheme / host / IP 白名单校验。当前**调用方都是项目内 helper**（`server/web_server.py`:74, 90, ... 全部走 `_build_internal_base_url` 127.0.0.1），所以表面上无攻击面 —— 但：
1. `screenshot_url` 是 **module-level public API**，任何未来 caller（含 plugin、含 webui 端点新增的 "render external preview"）都可以传任意 URL → playwright 携带本机网络访问内网 / metadata endpoint / file:// 协议；
2. playwright `goto` 默认接受 `file://` scheme → 读取本机任意文件（Chromium 在某些配置下会渲染本地 HTML）；
3. `_LAUNCH_ARGS = ["--disable-dev-shm-usage"]`（line 61）没有 `--disable-gpu --no-sandbox` 之外的额外约束，缺 `--js-flags=--noexpose_wasm`、`--disable-features=...` 等加固开关；
4. 没有 `intercept_route` 拦截外网请求 —— 截图过程中页面里的 `<img src="http://evil/log?...">` 会真实发出，等于"通过截图渠道泄漏内部 URL token"。

**Fix sketch**：
- 入口加 URL whitelist：解析 url → 校验 `scheme in {"http", "https"} and host in {"127.0.0.1", "localhost", "::1"}`，否则 `raise RenderScreenshotError`；
- 启动时新增 `args`：`--disable-features=Translate,InterestCohort`、`--no-zygote`、`--block-new-web-contents`；
- 创建 context 时设置 `context.route("**/*", lambda route: route.abort() if not is_internal(route.request.url) else route.continue_())`，禁掉非 loopback 资源加载。

**Risk if unfixed**：未来添加任意"截图外链"功能，立刻变成 SSRF 通向云元数据 / 内网服务的渠道；当前虽 caller 安全，但 API 形态是埋雷。

---

### H-6 `page_store` 无 size cap / 无 LRU，仅靠"每次写入触发 expire 扫描"，全表 O(N) 扫 + 内存可无上限增长

**File**: `server/page_store.py`:8-39

**Dimension**: security / perf

**Issue**：
1. `_pages: dict[str, dict[str, Any]]` 在 module 全局，无 max-size 上限。每次 `create_page` 调 `_cleanup_expired_pages` 全表扫描 expire（line 14-22），随 cache 增长 O(N)；
2. 玩家可通过频繁触发 `/inventory`、`/menu`、`/leaderboard` 等命令让 bot 高频生成 page —— TTL 600s 期内堆积，单 payload 含 slots / entries / outcomes 字段，可数 KB 到数十 KB。100 个活跃玩家 × 每分钟 10 条命令 × 600s = **6 万 entry**，按 10 KB/payload 计 **600 MB** 常驻；
3. `_cleanup_expired_pages` 在锁内逐 entry 算 `now - created_at_ts`，6 万 entry 锁竞争明显；
4. 无 LRU evict，进程退出前内存只升不降；重启依赖 settings restart 链路（H-2）才能解；
5. `payload.get("created_at_ts", now)` fallback 设计：若 payload 被外部代码非法注入（绕过 create_page），`created_at_ts` 缺失时 `now - now = 0` < TTL，**永远不过期**。

**Fix sketch**：
- 引入 `collections.OrderedDict` + max size cap（如 5000）：`_pages.move_to_end(token, last=True)` on get；满了 `_pages.popitem(last=False)` evict LRU；
- `_cleanup_expired_pages` 改增量：按 expire 时间排序 / heap 维护 expire queue，O(log N) pop；
- 把 `created_at_ts` fallback 从 `now` 改为 `0`（不存在则视为过期）；
- 加 size metric / WARN log："page_store size > 4000，开始 evict"。

**Risk if unfixed**：玩家可通过命令 spam 让 bot 内存 OOM；高并发下锁竞争劣化整体响应；与 H-2 daemon 线程 leak 叠加，bot 进程难以软重启恢复。

---

### H-7 `_load_or_create_webui_auth` 写文件未原子化，存在 token 丢失 / 多进程竞争窗口

**File**: `server/server_config.py`:67-117

**Dimension**: security / 可用性

**Issue**：
1. `_WEBUI_AUTH_FILE.write_text(payload_text + "\n", encoding="utf-8")`（line 100）直接写目标文件，**不是 temp + rename 原子模式**。对照 `server/settings_service.py`:160-163 已正确实现：`temp_path = _ENV_PATH.with_suffix(".env.tmp"); temp_path.write_text(...); temp_path.replace(_ENV_PATH)`。`.webui_auth.json` 应同等处理；
2. 进程崩溃 / 断电中写时，文件可能被截断为 0 字节或部分写入 → 下次启动读 `json.JSONDecodeError` → silently 走 `auth_payload = {}`（line 76-77）→ **生成新 token**，**所有现有用户会话 cookie 立刻失效**（webui token + session_secret 一起换）；
3. `os.chmod` 在 write 之后才执行（line 105），写入瞬间文件权限是 umask 默认（一般 0o644），TOCTOU 窗口内攻击者可能读到 token 明文（同机器多用户场景）；
4. 该函数没有锁 —— 若 `get_server_settings` 并发首次调用（H-2 daemon thread + 主线程同时初始化），可能两个线程都进 `_build_settings`，竞争写文件；
5. `auth_payload.get("webui_token", "")` 强制 `str()` + strip 后判空（line 79-80）—— 若 JSON 文件被攻击者注入 `{"webui_token": null, "session_secret": 12345}`，仍能通过类型检查；但 `null` strip 后变 `"None"` 而**不是空**，会被当成有效 token（这点细节属设计缺陷）。

**Fix sketch**：
- 写入改 temp + rename：参考 settings_service `_write_env_values` (line 161-163) 模式；
- 在 write 之前显式 `os.umask(0o077)` 或 `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + `os.write` 模式控制；
- `_load_or_create_webui_auth` 整体外面加 `_settings_lock` 保护（或独立 file-level lock）；
- 类型校验：`if not isinstance(auth_payload.get("webui_token"), str)` 时强制重新生成。

**Risk if unfixed**：进程崩溃时序下 `.webui_auth.json` 损坏 → 全部 session 失效；多用户主机上 0.6s 窗口内可被读取 token；并发首启时 race。

---

## Medium

### M-1 `_build_internal_base_url` 写死 `http://127.0.0.1:{port}`，settings host 改 0.0.0.0 时与 uvicorn bind 一致性破缺

**File**: `server/web_server.py`:35-36

**Dimension**: security

**Issue**：函数名是 "internal"，但 `settings.host` 若用户改为 `0.0.0.0`（settings_service `web_server_host` 接受任意字符串，无白名单 settings R1 M-9），uvicorn 实际 bind 在所有网卡上 —— 此时 `_build_internal_base_url` 还是 127.0.0.1，截图浏览器请求会成功（loopback 总是 listening），但**意义错位**：函数语义"内部 url"已被破坏。CRIT-1 的根本问题就在于此假设的失守。

**Fix sketch**：保持函数返回 127.0.0.1 不变（这个语义是对的：截图浏览器永远走 loopback），但加 docstring + assert：函数依赖于 uvicorn bind 至少包含 loopback，settings.host 不应被改为 `::1` 之外的非 loopback 而忽略 loopback —— 校验 `if "0.0.0.0" not in settings.host and "127.0.0.1" not in settings.host: logger.warning(...)`。或文档化"`_build_internal_base_url` 与 settings.host 解耦：内部 url 永远 loopback，外网 host 由 settings.host 决定"。

**Risk if unfixed**：将来若有人把 internal base 改成 `settings.host` 直接拼，会立刻把内部 token URL 推到外网。

---

### M-2 `create_app` 路由注册顺序与中间件顺序未文档化，未来加 middleware 易破坏 LIFO 链

**File**: `server/web_server.py`:358-391

**Dimension**: security / 可维护性

**Issue**：line 369-372 注释提到"M-A3：安全响应头先注册，使其在中间件 LIFO 链最外层执行" —— 这条信息很关键，但**没有任何代码层守护**。未来开发者添加 middleware 时无 lint / 测试发现"加错位置"。webui_auth_middleware 现在是次外层；若未来加 rate-limit 中间件，必须夹在 security headers 内、auth 外才合规。

注释还提示"保证 auth 重定向等所有 webui 响应都带上 CSP / X-Frame-Options 等头" —— 但 `/render`、`/health`、`/assets` 都不在 `/webui` 前缀，**不会得到 security headers**（H-3 已记录）。

**Fix sketch**：
- 在 `create_app` 内加 assertion / log：枚举 `app.user_middleware` 验证顺序为 `[security_headers, auth, ...]`；
- 注释里把 LIFO 详细顺序写清：（外到内）security headers → CSRF（未来）→ rate limit（未来）→ auth → router；
- 提取 `_register_middlewares(app, settings)` helper 统一管理。

**Risk if unfixed**：未来 middleware 添加顺序失误，安全头丢失 / auth 被绕过。

---

### M-3 `create_app` 路由注册大量重复 import，未来添加 router 易漏挂

**File**: `server/web_server.py`:11-29, 373-385

**Dimension**: 可维护性

**Issue**：13 个 webui router 各自 import + include，缺少注册表抽象。新增一个 `webui_xxx.py` 需要同时改 import block（line 11-29）和 include_router block（line 373-385），双修改容易漏；当前已经能看到 `webui_dashboard`（line 14 / line 376）、`webui_commands`（line 13 / line 375）等数量相同，但人工同步成本高。

**Fix sketch**：把 `WEBUI_ROUTERS: list[APIRouter]` 提取到 `server/routes/__init__.py`（scope-out 跨模块）或 `server/web_server.py` 顶部，循环 `for r in WEBUI_ROUTERS: app.include_router(r)`。最小修：保持现状，加注释"修改时同步 import block 和 include block"。

**Risk if unfixed**：新增 router 时漏挂，跑通 unit test 也发现不了（404 即可）。

---

### M-4 settings cache singleton 永不刷新，settings restart 重启后才生效，与"实时配置"用户预期不符

**File**: `server/server_config.py`:27-28, 143-148

**Dimension**: ux / 一致性

**Issue**：`get_server_settings()` 使用 `_cached_settings` 单例缓存（line 143-148），**无失效机制**。settings_service `save_settings` 写 `.env` 后，settings cache 不更新，必须依赖 `_schedule_process_restart`（settings R1 引用的 webui_settings.py:30-50）触发 `os.execv` 重启进程 cache 才换。问题：
1. 任何调用方在 settings 修改后 - 重启完成前的 0.8s 窗口拿到旧值；
2. `_build_internal_base_url` 用 cached settings.port，端口变更后老 URL token 仍指向旧端口（重启后 token 在 page_store 内存里已丢，影响小）；
3. `webui_token` / `session_secret` 通过 `.webui_auth.json` 维护，不走 `.env`，但 `_load_or_create_webui_auth` 也只在首次 build 调用 —— 若外部工具修改 `.webui_auth.json`（如手动改 token），不重启不生效。

对照已有：settings 页面通过 `_schedule_process_restart` 显式 execv 解决 (settings R1 backlog 提到 "cache invalidation 由 restart 兜底")。

**Fix sketch**：
- 提供 `invalidate_settings_cache()` API，在 settings_service `save_settings` 写入成功后调用（scope-out 跨模块协调）；
- 或保持现状但在 module docstring 注明"settings cache 仅在进程重启时刷新；运行期内任何 .env 改动需 os.execv 触发"。

**Risk if unfixed**：用户从其他通道（手动 vim .env）改配置后无效；设置面板看到"已保存"但实际生效需重启。

---

### M-5 `_parse_port` 静默接受非法值 fallback 到 18081，无 WARN 日志

**File**: `server/server_config.py`:33-57

**Dimension**: 可观测性 / ux

**Issue**：用户在 `.env` 中写 `WEB_SERVER_PORT=abc` 或 `WEB_SERVER_PORT=70000`，`_parse_port` 静默 fallback 到 18081（line 33, 51, 53, 57）—— 启动日志只显示最终的 host:port，用户无任何反馈"我写的配置被忽略了"。

对照 `settings_service.py`:238-247 `_coerce_port` 会抛 `SettingsValidationError`；但 `_parse_port` 是 server_config 内部用，走的是另一条路径。

**Fix sketch**：每个 fallback 分支前加 `logger.warning(f"WEB_SERVER_PORT 配置无效（值={raw_value!r}），使用默认 {default}")`；接受值非数字时尤其需要明确告知。

**Risk if unfixed**：用户改了端口但 bot 仍跑在 18081，怀疑"配置丢失"，排查成本高。

---

### M-6 `_normalize_public_base_url` 仅 strip + rstrip("/")，无 scheme 校验

**File**: `server/server_config.py`:60-64

**Dimension**: security

**Issue**：函数接受任意字符串，未校验 `http(s)://`。settings_service `_coerce_http_url`（line 230-235）会做 URL 校验，但 `.env` 里的 `WEB_SERVER_PUBLIC_BASE_URL` 也可能被用户手动写为 `javascript:alert(1)` 或 `file:///etc/passwd`。该字段被 webui token URL 生成（间接通过 settings.public_base_url，scope-out caller），若拼接到 HTML / 跳转目标，构成 reflected XSS / open-redirect 面。

虽然当前 caller 全部走 internal 127.0.0.1，但 `public_base_url` 字段是为对外 webui URL 设计的，未来必然出现 caller。

**Fix sketch**：在 `_normalize_public_base_url` 中复用 settings_service 的 URL 校验逻辑，或：
```python
from urllib.parse import urlparse
parsed = urlparse(text)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    logger.warning(f"WEB_SERVER_PUBLIC_BASE_URL 无效（{text!r}），回退到默认")
    return f"http://{host}:{port}"
```

**Risk if unfixed**：恶意配置可让 public_base_url 指向 javascript: / data: URI，未来引用方触发 XSS。

---

### M-7 截图 `_LAUNCH_ARGS` 仅 `--disable-dev-shm-usage`，缺 sandbox / GPU 加固

**File**: `server/screenshot.py`:61

**Dimension**: security / perf

**Issue**：Chromium 默认 sandbox 在某些容器环境（docker --privileged=false）需要 `--no-sandbox`，缺失则崩溃；但在生产 host 上不加任何额外 args，缺：
- `--disable-gpu`（无头无 GPU，少耗 VRAM）
- `--disable-extensions`、`--disable-plugins`、`--disable-background-networking`（减少非业务网络请求面）
- `--disable-features=Translate,InterestCohort,FedCm`（关掉冷门功能）
- `--js-flags=--max-old-space-size=512`（限制 V8 内存）

`--disable-dev-shm-usage` 本身只解决 docker `/dev/shm` 太小导致渲染 OOM 的问题，与 sandbox / 网络无关。

**Fix sketch**：扩展 `_LAUNCH_ARGS`：
```python
_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-features=Translate,InterestCohort",
]
```
若已知部署在容器中，按需追加 `--no-sandbox`（与运维确认）。

**Risk if unfixed**：截图浏览器进程占资源偏高；后台网络请求面增大；某些容器 / 隔离环境下崩溃。

---

### M-8 `_PlaywrightSession.close` 在 `_atexit_close` 中 spin 新 loop 关闭，与 nonebot loop / asyncio loop policy 易冲突

**File**: `server/screenshot.py`:94-105, 228-243

**Dimension**: perf

**Issue**：`_atexit_close` 用 `asyncio.new_event_loop()` 跑 `_session.close()`（line 236-240），但 `_session._lock = asyncio.Lock()` 是绑定到**创建时所在 loop** 的；若 lock 是在 uvicorn 的 loop 中初始化，atexit 时新 loop 试图 acquire 会抛 `RuntimeError: Task got Future attached to a different loop`。`contextlib.suppress(Exception)` 把所有异常吃掉（line 235），所以**实际从未真正关闭过 playwright**，只是看起来无报错。

NoneBot `on_shutdown` 在主驱动 loop 里跑，与 uvicorn loop 也是不同 loop（H-2 已展开）。

**Fix sketch**：
- `_session._lock` 改为 `threading.Lock()` 而非 `asyncio.Lock()`（牺牲 async 等待，但跨 loop 安全）；
- 或显式记录"`screenshot._session` 仅可在创建它的 loop 中 close"，并在 nonebot 主 loop / uvicorn loop 退出前调用 close，atexit 退化为空操作；
- 加 debug log：`_atexit_close` 内 `logger.info("atexit 关闭截图浏览器：尝试 close session")` 让运维能看到 fallback 是否触发。

**Risk if unfixed**：playwright 子进程在 bot 退出后仍残留（zombie chromium process），需要手动 kill；fd / 内存泄漏。

---

### M-9 `screenshot_url` retry 仅捕获 `PlaywrightError / ConnectionResetError`，不区分**首次启动失败** vs **运行中崩溃**

**File**: `server/screenshot.py`:208-220

**Dimension**: perf

**Issue**：retry 循环最多 2 次（line 121），首次失败立刻 `_session.close()` 后重试（line 212）。但 `_session.get_browser()` 有可能因 `_PLAYWRIGHT_IMPORT_ERROR is not None`（line 78-81）直接抛 `RenderScreenshotError`，被 line 205-207 的"deterministic"分支无 retry 拦截。如果是首次启动 chromium 二进制缺失 → 重启浏览器毫无意义（chromium 还是不存在）→ 第二次 attempt 走同样路径 → 浪费一次 retry 周期 + 用户多等几秒。

另外 `last_exc: Exception | None` 初始化为 None，若两次 attempt 都被 line 205 / line 215 / 216 提前 raise，`last_exc` 永远是 None → 最终 line 218-220 抛 `"重启浏览器后仍未恢复：None"` 文案残缺。

**Fix sketch**：
- retry 前对错误分类：chromium 二进制不存在 / 配置缺失类错误直接 raise，不进 retry；
- `last_exc` 初始化为 `RenderScreenshotError("截图失败：未知原因")` 而非 None，避免文案 `"...：None"`。

**Risk if unfixed**：错误消息表达不完整；retry 在确定性失败上浪费时间。

---

### M-10 `_write_env_values` 单线程持锁全文件 rewrite，无 chunk / 无超时

**File**: `server/settings_service.py`:122-163

**Dimension**: perf

**Issue**：每次 `save_settings` 都：
1. `_WRITE_LOCK.acquire()` 在 RLock 上；
2. `_read_env_lines()` 全文读 `.env`（line 124，io 同步）；
3. 逐行 parse + 计算 update / append index；
4. write `.env.tmp` + `replace`；
5. 整个过程在锁内串行。

settings R1 H-2 已记录"payload size 无上限可拖慢主进程"。这里追加：
- **没有 timeout** 防长持锁：若 `.env` 是 100MB（极端场景 H-2），`read_text` 会同步 block；
- 与 `get_settings_snapshot`（line 397-410）共享 read 但**不持 _WRITE_LOCK**（line 100-107 `_read_env_values` 无锁）—— save 中途 snapshot 拿到的是部分写入数据？答：write 是 temp + rename 原子的，所以读到的要么是全旧要么是全新；但若两次连续 save 中间夹 snapshot，可能 snapshot 拿到中间态（合规，因为 rename 是原子的）。
- `_FIELD_BY_ENV` 与 `_FIELD_BY_NAME`（line 64-65）是 module-level dict，多线程读安全。

**Fix sketch**：
- `_read_env_values` / `_read_env_lines` 加 `_WRITE_LOCK` 保护读侧，让 snapshot 与 save 串行；
- 把 `_WRITE_LOCK` 替换为 `_FILE_LOCK = threading.Lock()`（RLock 不必要，无 reentrant 调用）；
- 加 `_MAX_ENV_SIZE = 1 * 1024 * 1024`，read 前 stat 文件 size 超限直接 raise，防 H-2 类放大。

**Risk if unfixed**：极端配置膨胀时锁持有时间长；snapshot 与 save 间无同步保证（虽然 rename 原子已经保住一致性）。

---

### M-11 `_serialize_env_value` 反斜杠 / 换行转义仅覆盖 welcome / farewell 模板

**File**: `server/settings_service.py`:117-118

**Dimension**: security / 数据完整性

**Issue**：line 117 仅对 `group_welcome_template`、`group_farewell_template` 做 `\\ -> \\\\` + `\n -> \\n` + 删 `\r`。`chat_sync_template` / `player_notify_online_template` / `player_notify_offline_template` 等模板字段也在 `_SINGLE_LINE_STRING_FIELDS`（line 66-79），目前禁换行所以**当前不会出问题**，但若未来需要多行（如 chat_sync 支持多行消息），缺少同步策略；同时 `command_disabled_message` 也在单行白名单。

settings R1 M-1 已记录此问题，这里在 server_core 侧补充：**`_serialize_env_value` 应该把"白名单"明确化为常量，而不是隐式 if-chain**。

**Fix sketch**：把 `_MULTILINE_ESCAPED_FIELDS = frozenset({"group_welcome_template", "group_farewell_template"})` 抽常量，line 117 改 `if field in _MULTILINE_ESCAPED_FIELDS`，注释说明"加字段时需同步 `_SINGLE_LINE_STRING_FIELDS` 反向移除"。

**Risk if unfixed**：未来字段类型迁移时遗漏，原始 `\n` 直入 `.env` 造成解析时行注入。

---

### M-12 `_load_value_from_env` 静默 fallback 到 config 默认值，校验失败无日志

**File**: `server/settings_service.py`:397-410

**Dimension**: 可观测性

**Issue**：`get_settings_snapshot` 在 `_load_value_from_env` 抛 `SettingsValidationError` 时静默 `pass` 走 config fallback（line 407-408）。后果：用户在 .env 写了非法值 → snapshot 显示的是 config 默认值 → 用户以为自己的修改"已生效"，实际被回退。

对照 M-5（_parse_port 静默 fallback），这是同类问题在 settings_service 侧的体现。

**Fix sketch**：在 `pass` 前加 `logger.warning(f"读取 settings 失败：field={spec.field}，原因={exc}")`；前端 snapshot 响应额外携带 `invalid_fields: list[str]` 告知前端"这些字段从 config 默认值回退"，前端可显示提示。

**Risk if unfixed**：用户手改 .env 后修改"消失"，排查只能 grep log。

---

### M-13 `save_settings` 返回 `saved_fields` 但未返回当前生效快照，前端必须再 GET 一次

**File**: `server/settings_service.py`:413-417

**Dimension**: ux / perf

**Issue**：`save_settings` 只返回 `SaveSettingsResult(saved_fields=[...])`，没有返回当前 normalized 后的值。前端必须：
1. PUT 表单值；
2. 收到 200 后，看 `saved_fields` 知道哪些字段保存了；
3. 必要时再 GET `/webui/api/settings` 拉回当前快照（确认 normalize 后的值，例如 url 末尾被 strip 的 `/`）。

settings R1 已经识别 saveSettings 后会立即 reload，所以前端"对账"被 reload 兜底，但 reload 是 `os.execv`（重启），普通字段（不需要重启的）不该走 execv。当前所有字段都触发 restart 是过度简化。

**Fix sketch**：
- `save_settings` 返回 `(saved_fields, normalized_values)`，前端拿到后可直接 fillForm 更新展示；
- 区分"需要重启才生效"的字段（如 host / port）与"热生效"字段（如 welcome template）—— 热生效字段不走 execv。

**Risk if unfixed**：每次保存都 execv 重启进程，所有 in-flight OneBot 连接被砍，体验粗糙。

---

## Low

### L-1 `server/__init__.py` 仅含 `from __future__ import annotations`，等同空文件，无 package docstring

**File**: `server/__init__.py`:1-2

**Dimension**: 可维护性

**Issue**：`server/` 作为顶层 package，`__init__.py` 仅有 `from __future__ import annotations` —— 这一行对 package 顶层无效（不是 module body），实际是 dead code。缺：
- package docstring 说明"server 是 nextbot 的 web/render/页面服务子系统"；
- 公共 API re-export（如 `from server.web_server import create_app, start_web_server`）让其他模块少写一层路径。

**Fix sketch**：
```python
"""NextBot Web / Render 服务子系统。

- `web_server`: FastAPI app 工厂 + uvicorn 启动
- `screenshot`: Playwright 截图入口
- `page_store`: 渲染 token cache
"""
from server.web_server import create_app, start_web_server, start_render_server

__all__ = ["create_app", "start_web_server", "start_render_server"]
```

**Risk if unfixed**：新开发者读 `server/` 不知道入口在哪。

---

### L-2 `web_server.py` 18 个 `create_*_page` helper 重复 `settings = get_server_settings()`，可提取公共 prefix

**File**: `server/web_server.py`:73-355

**Dimension**: 可维护性

**Issue**：18 个 helper 函数末尾几乎是完全相同的 3 行 boilerplate：
```python
token = create_page("xxx", payload)
settings = get_server_settings()
return f"{_build_internal_base_url(settings)}/render/xxx/{token}"
```
违反 DRY 原则；每加一个 page type 重复一份。

**Fix sketch**：抽 `_make_page_url(page_type: str, payload: dict) -> str`：
```python
def _make_page_url(page_type: str, payload: dict[str, Any]) -> str:
    token = create_page(page_type, payload)
    return f"{_build_internal_base_url(get_server_settings())}/render/{page_type}/{token}"
```
每个 helper 末尾改为 `return _make_page_url("inventory", payload)`。

**Risk if unfixed**：未来加 page type 时易抄错 page_type 字符串（已经有 `red_packet_own` / `red_packet_all` 类容易混淆的命名）。

---

### L-3 `get_server_settings()` 在 daemon thread / 主 thread 并发首次调用时的 race 窗口

**File**: `server/server_config.py`:143-148

**Dimension**: 并发

**Issue**：`_settings_lock` 已经覆盖临界区，但**首次调用**时 `_build_settings()` 会触发 `_load_or_create_webui_auth`（H-7）写文件 → 锁内执行 IO，期间其他调用方阻塞；典型场景：bot 启动时 `start_web_server` thread 与主线程的 `get_driver().on_shutdown` 注册（间接通过 module import）可能并发跑到此处。锁本身安全，但 IO 在锁内有性能小尾巴。

**Fix sketch**：把 `_build_settings()` 内的 `_load_or_create_webui_auth` 抽到 eager init（模块 import 时执行一次），settings cache 仅做 read。

**Risk if unfixed**：bot 启动期偶发 100ms 级阻塞，可忽略。

---

### L-4 `_run_server` 启动日志写 `Web UI：http://127.0.0.1:{port}/webui` 假设 host 是 127.0.0.1

**File**: `server/web_server.py`:399

**Dimension**: copy / ux

**Issue**：`logger.info(f"Web UI：http://127.0.0.1:{settings.port}/webui")` 永远打 127.0.0.1，**忽略 settings.host**。用户改 host 为 `0.0.0.0` 后，运维想知道实际 listening 地址，从这条日志看不到（line 398 打的是 settings.host，但 line 399 又写死 127.0.0.1）。

实际 webui 监听地址 = settings.host（含 0.0.0.0），但 host 改成 0.0.0.0 时实际访问 URL 应该是 public_base_url（已在 settings 中维护）。这里直接打 127.0.0.1 是合理的 loopback 提示，但与 line 398 的 settings.host 混搭让人困惑。

**Fix sketch**：把 line 398-399 合并为：
```python
logger.info(f"Web Server 已启动，监听 {settings.host}:{settings.port}（loopback 访问：http://127.0.0.1:{settings.port}/webui）")
```
或拆分为两条 INFO，明确"监听地址" vs "本机访问 URL"。

**Risk if unfixed**：运维看日志判断绑定地址时容易误判。

---

### L-5 `screenshot.py` `wait_for_load_state("networkidle", timeout=5000)` 被 suppress 后无降级日志

**File**: `server/screenshot.py`:145-146

**Dimension**: 可观测性

**Issue**：`contextlib.suppress(PlaywrightTimeoutError)` 把超时静默吃掉。注释（line 143-144）说"是 plan A 的补丁"，但若某个页面长期触发该超时（5s 频繁达到），运维无任何感知 —— 性能调优时缺数据。

**Fix sketch**：把 `contextlib.suppress` 换成 try/except：
```python
try:
    await page.wait_for_load_state("networkidle", timeout=5000)
except PlaywrightTimeoutError:
    logger.debug(f"截图等待 networkidle 超时（5s），继续：url={url}")
```
DEBUG 级别避免噪音，但保留可见性。

**Risk if unfixed**：截图性能问题难定位。

---

### L-6 `screenshot.py` 错误信息使用中英混排但部分缺空格

**File**: `server/screenshot.py`:80-81, 91, 140, 167-168, 198, 211, 216, 219

**Dimension**: copy

**Issue**：CLAUDE.md 规则 4 中英文混排保留一个空格。检查：
- line 80-81 `"未安装 playwright，请先执行：uv add playwright && uv run playwright install chromium"` —— "未安装 playwright" 中文/英文之间有空格 ✓；命令片段前用全角冒号 ✓；
- line 91 `f"截图浏览器启动完成，启动参数 {_LAUNCH_ARGS}"` —— "启动参数 {...}" 之间有空格 ✓；
- line 140 `f"截图导航超时（{render_options.wait_until} > {render_options.timeout_ms}ms）：{exc}"` —— "ms" 与 `）` 之间无空格 ❌（按规则应为 `ms ）`，但中文括号前不加空格是项目惯例，此处合规）；`{render_options.wait_until} >` 之间是英文，OK；
- line 167-168 `f"截图等待字体加载超时：{exc}"` —— 纯中文 + `：{exc}`，OK；
- line 198 `f"截图采集超时：{exc}"` —— OK；
- line 211 `f"截图浏览器异常，准备重启第 {attempt} 次：{exc}"` —— "重启第 {attempt} 次" 之间英文数字与中文之间合规（"第" 后加空格 + 数字 + 空格 + "次"），合规；
- line 216 `f"截图失败：{exc}"` —— OK；
- line 219 `f"截图失败（重启浏览器后仍未恢复）：{last_exc}"` —— OK；

总体合规，仅 line 140 的 `ms）` 紧贴属于"项目内中文括号惯例"，可保留。**唯一可议**：line 91 日志面向人眼，按 CLAUDE.md "动作 + 具体对象 + 结果 + 上下文" 推荐 → 当前是"动作 + 结果 + 上下文"，缺"对象（哪个浏览器实例 / 哪次启动）"。

**Fix sketch**：line 91 增补 launch session id 或 attempt 计数；其他保持。

**Risk if unfixed**：日志检索时只能 grep `截图浏览器启动完成`，看不到上下文。

---

### L-7 `page_store._cleanup_expired_pages` 在锁内做 dict comprehension，pop O(N)

**File**: `server/page_store.py`:14-22

**Dimension**: perf

**Issue**：H-6 已记录"全表扫"，此条补充微观：line 16-20 dict comprehension 构造 `expired_tokens` 列表后再循环 `pop` —— 实际可以一行 `_pages = {token: payload for token, payload in _pages.items() if now - float(payload.get("created_at_ts", now)) <= PAGE_EXPIRE_SECONDS}`，但 dict 替换会让 module-level 引用断（无问题，因为 `_pages` 是 module attr，重赋值后所有 import 都跟着新对象 —— 但**多线程读侧**若已经持有旧 dict 引用就会读到旧数据；当前 get_page 也在锁内取 `_pages.get`，所以是安全的）。

**Fix sketch**：保持现状即可，或在 H-6 fix 中一并用 OrderedDict + popitem 实现 LRU。

**Risk if unfixed**：微小性能优化空间，可忽略。

---

### L-8 `page_store` cache hit/miss / size 无 metrics 暴露

**File**: `server/page_store.py`:1-39

**Dimension**: 可观测性

**Issue**：cache 状态对运维不可见 —— size、hit rate、expire 速度都没有任何 log / 端点。配合 H-6 的 OOM 风险，加监控是关键防御。

**Fix sketch**：加 `def get_metrics() -> dict[str, int]: return {"size": len(_pages), ...}` + 周期 log（每 60s WARN if size > threshold）；或暴露 `/webui/api/internal/page-store-metrics`（auth 后访问）。

**Risk if unfixed**：H-6 触发前无预警。

---

### L-9 `settings_service` `_QQ_ID_PATTERN` 长度 5-20 位但 QQ 实际范围 5-11 位

**File**: `server/settings_service.py`:17, 206

**Dimension**: 数据完整性

**Issue**：`r"^\d{5,20}$"` 上限 20 位过宽（QQ 号实际 5-11 位）。20 位允许无意义大整数进入 owner_id / group_id 列表，未来与 OneBot 平台对接时校验失败。

**Fix sketch**：改 `r"^\d{5,11}$"`，与 OneBot 平台约束一致；或在错误消息中说明"QQ 号通常 5-11 位"。

**Risk if unfixed**：用户填错位数到 12-20 位，运行时才报错。

---

### L-10 `_serialize_env_value` 对 `\\` 转义在 caller 链上下文未文档化

**File**: `server/settings_service.py`:117-118

**Dimension**: 可维护性

**Issue**：line 117 `.replace("\\", "\\\\")` 把单反斜杠加倍 —— 这是为了 `.env` 解析器把 `\\n` 当 literal `\n` 而非换行。但 `_load_value_from_env`（line 336-338）反向解析时 `.replace("\\n", "\n").replace("\\\\", "\\")` 顺序敏感（先恢复换行再恢复反斜杠），逆向操作正确。但无 unit test 覆盖（scope-out）。

**Fix sketch**：抽 helper `_escape_for_env(text: str) -> str` / `_unescape_from_env(text: str) -> str`，加 docstring 说明 round-trip 不变；测试 round-trip 字符串 `"a\\b\nc\r"` → escape → unescape == 原值（除 `\r` 被刻意 strip）。

**Risk if unfixed**：未来 caller 改字段类型时不易理解 round-trip 约定。

---

### L-11 `get_settings_metadata` 仅暴露 `sensitive_fields` 元数据，未被任何代码消费（settings R1 CRIT-1 已识别）

**File**: `server/settings_service.py`:420-424

**Dimension**: 一致性

**Issue**：settings R1 CRIT-1 已经识别 "sensitive_fields 元数据宣称敏感但未真正 mask"。这里在 server_core 角度补充：`_FIELD_SPECS` 里只有 `onebot_access_token` 标 `sensitive=True`（line 39），与 `get_settings_metadata` 一致；但 **callers 无任何代码读 `metadata.sensitive_fields` 来决定 mask 策略**。该元数据成了"装饰"。

**Fix sketch**：webui_settings handler 在响应前消费 `metadata.sensitive_fields` 自动 mask，参考 servers R2 H-1 模式。settings R1 CRIT-1 fix 应在 webui_settings 路由层执行此处理；server_core 侧无需改动。

**Risk if unfixed**：与 settings R1 CRIT-1 重叠。

---

## Scope-out backlog（跨模块，仅引用，不在本次审计范围）

- **`/webui/api/restart` CSRF 防护**：见 settings R1 H-3，需 Origin / Referer 校验 + 自定义请求头；
- **共享层 body size limit**：commands R3 / settings R1 H-2 已记录，FastAPI middleware 层统一上限；
- **`.env` 持久化加密**：OneBot token 当前明文写盘，长期应换 secret store（Keychain / secret-service）；
- **CSRF middleware 全局化**：settings R1 已记录，与 H-3 配套；
- **`web_server_host` 白名单**：settings R1 M-9 已记录，前后端同步加 IP / hostname 校验；
- **logger 全局脱敏 wrapper**：CLAUDE.md 已规定但未落地，settings R1 H-1 已记录；
- **`/render/*` token 绑定 owner**：CRIT-1 长期方案，需 create_page 接受 owner_user_id 参数；
- **page_store 持久化 / 跨进程共享**：当前 module-level dict 进程退出即丢，重启后所有 token 失效；
- **uvicorn graceful shutdown 体系**：H-2 长期方案，需统一进程生命周期管理（asyncio Server + signal hook）；
- **render 路由 host filter**：CRIT-1 最小修，应在 `create_app` 中加 host-based middleware。

---

## Top 3 Highest-Severity 摘要

1. **CRIT-1**：`/render/*` 路由完全无认证，token 仅靠 UUID 强度。settings 允许 host 改 0.0.0.0 + public_base_url 改公网 → 端点暴露公网；玩家敏感数据（仓库 / 管理员清单 / user_info）随 OneBot 消息日志泄漏即可被外网拉取。
2. **H-1**：`logger.warning(f"Web UI Token：{settings.webui_token}")` 在启动横幅打完整 webui token，WARN 级别被日志聚合长期归档，凭据长期外泄面。
3. **H-2**：daemon thread + uvicorn.run 模型让进程退出时无 graceful shutdown，Playwright 子进程残留 + 端口 FD 泄漏 + signal handler 在子线程失效；与 H-6 page_store 内存泄漏叠加，bot 长跑后状态混乱难以软重启恢复。

---

## Caveats / Not Found

- 未发现 SQL 注入面（无 DB query）。
- 未发现 path traversal（`render._resolve_static_file` 已做 `relative_to` 校验，scope-out）。
- 未发现 XSS：所有 helpers 通过 `payload` 构造对象后由 page 模板渲染，server_core 文件本身不拼 HTML。
- 未发现 logger 内 secret 主动泄漏（除 H-1 `webui_token`）。`auth_file_path` 是路径，不是凭据，可接受。
- `_settings_lock` / `_pages_lock` / `_session._lock` 均为合规的并发原语；唯一异常 M-8 跨 loop 问题。
- `settings_service._normalize_field` 的 `_coerce_*` 分发函数齐全，validation 覆盖率高（settings R1 已细审 webui 路由层）。
- `_FIELD_SPECS` (line 38-62) 21 字段全部有 `_normalize_field` 分支，无缺漏。
- `screenshot_url` 的 `fit_content_height` 路径（line 172-190）评论详尽，逻辑正确。
- `_atexit_close` (line 228-243) 与 NoneBot on_shutdown (line 250-253) 双保险设计意图合理，问题在 loop 兼容（M-8）。
- 未审：`server/routes/webui.py`、`server/routes/render.py`、`server/pages/*`、`nextbot/screenshot_render.py`、`nextbot/plugins/*` 已 scope-out（仅引用关系）。
