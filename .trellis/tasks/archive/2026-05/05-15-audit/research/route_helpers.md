# Research: Route helpers audit (Bucket B)

- **Query**: 审计 `server/routes/__init__.py`、`server/routes/webui.py`、`server/routes/render.py` 的安全 / 性能 / UX / 文案
- **Scope**: internal（仅 3 个文件，跨文件问题入 scope-out backlog）
- **Date**: 2026-05-15

---

## 顶层结论

- 共 **27** 条 finding（含 PASS / Info）。主要问题：
  - `_client_ip` 完全信任 `X-Forwarded-For` → 让 H-A3 brute-force 限速被任意客户端绕过（**Critical**）。
  - 7 个 `/webui/api/*` 路由 / 业务模块**重复实现** `_client_ip`（与本文件同源），任何这里修不到的"forwarded 信任"问题都会扩散；helper 没下放到 `routes/__init__.py` 是工程层根因（**High**）。
  - `read_json_object` 无 size / content-type 校验，依赖 Starlette/uvicorn 默认（无默认 body size cap）→ memory-DoS（**High**）。
  - `render.py` 资产路径 helper `_resolve_static_file` **没有 reject 符号链接**——容器/共享卷场景下 symlink 可指向 root-fs 任意文件（**High** in shared FS）。
  - `_decode_session_cookie` 把所有解码异常吞为 `except Exception`，掩盖真实错误且对 `webui.py:268` 静态文件 helper 的 try-pattern 不一致（**Low**）。
- Top 3 修复优先级见末尾。

---

## 维度一：Security

### CRITICAL-1 `_client_ip` 盲信 X-Forwarded-For，让所有基于 IP 的限速失效
**File**: `server/routes/webui.py:151-159`
**Dimension**: security
**Issue**:
```python
def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    ...
```
- 没有 trusted-proxy 白名单（`grep -RnE "trust_proxy|forwarded_allow_ips"` 全仓 0 命中）。
- 部署默认 `web_server_host=127.0.0.1`（`server_config.py:123`）+ 文档无反代要求，**当前 99% 部署是裸 FastAPI 直接服务**，攻击者 `curl -H "X-Forwarded-For: 1.2.3.4"` 即让日志 / 限速记 `1.2.3.4`。
- 后果链：
  1. `_check_login_rate_limit(client_ip)`（`webui.py:332`）把每个伪造 IP 当独立桶 → H-A3 brute-force 限速完全失效，token 暴力破解 / 撞码可绕过。
  2. dashboard / users / settings / shop / lottery / servers 等所有日志的 `client_ip=` 字段被污染（IP 伪造）。
- 主代理已在 dashboard-audit-r2 A.1.3 标注 "这是 webui.py 既有问题，被复用传播" → **现在归本 bucket，必须修**。

**Fix sketch**:
- 引入 `WebServerSettings.trusted_proxies: list[str] = []`（默认空）。
- 只有当 `request.client.host` 在白名单内时才读 XFF；否则一律取 `request.client.host`。
- 升级 helper 到 `server/routes/__init__.py`（与 HIGH-2 一并做），让所有 caller 自动受益。

**Risk if unfixed**: H-A3 brute-force rate-limit 名存实亡；审计日志 IP 不可信。

---

### HIGH-2 `_client_ip` 在 8 个文件里重复实现，根因是 helper 没下放到 `routes/__init__.py`
**File**: `server/routes/__init__.py`（应有而无）；当前实现：`webui.py:151-159`、`webui_servers.py:59-66`、`webui_settings.py:77-83`、`webui_lottery.py:58-65`、`webui_shop.py:50-55`、`webui_warehouse.py:30-36`、`webui_login_requests.py:36-42`、`webui_player_events.py:40-47`
**Dimension**: security（一致性 / 修复扩散面）
**Issue**:
- 同一 helper 在 8 个文件被各自复制（部分注释为 "同 webui.py 实现"）。
- 任何对 CRITICAL-1 的修复都要在 8 处同步，否则就有"修了 3 处忘了 5 处"的回归。
- `webui_dashboard.py:9` / `webui_commands.py:24` 已是 `from server.routes.webui import _client_ip` 正解；其余 8 处违反 DRY。
- 这条 finding 严格上 cross-module（其它文件不在本 bucket scope）但**根因在 `routes/__init__.py` 缺这个公共 helper**，所以归本 bucket。

**Fix sketch**:
- 把 `_client_ip` 升级为 `routes/__init__.py:client_ip(request, *, trusted_proxies=...)` 并去掉前导下划线（导出语义）。
- 替换 8 个 caller 为 `from server.routes import client_ip`。
- 删除 `webui.py:151-159` 本地副本（dashboard / commands 早已 from import）。
- **如本任务 scope 只动 3 文件**：进 backlog 标 "扩散到 8 文件，统一时一并迁移"。

**Risk if unfixed**: 修一个漏一片；后续任何 hardening 的 audit 心智成本翻 8 倍。

---

### HIGH-3 `read_json_object` 无 body size / content-type / array nesting 校验
**File**: `server/routes/__init__.py:51-68`
**Dimension**: security
**Issue**:
```python
async def read_json_object(request: Request) -> ...:
    try:
        payload: Any = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        ...
    if not isinstance(payload, dict):
        ...
```
- `await request.json()` 内部走 Starlette 的 `await request.body()` → **无 size cap**。Uvicorn `h11_max_incomplete_event_size` 仅控 header；body 流不限大小。
- 攻击者 POST 1 GiB JSON 到 `/webui/api/session` / `/webui/api/users` 等任何用 `read_json_object` 的端点 → 进程驻留内存爆炸（OOM kill）。
- 共有 6 个文件 14 处调用此 helper（webui_settings / webui_warehouse / webui_lottery / webui_login_requests / webui_player_events / webui_commands / webui.py），全部受影响。
- 也未校验 `Content-Type: application/json`。`text/plain` body 是 JSON 也会被接受，可能让 CSRF 防御依赖 CT 时被绕过（虽然当前项目不依赖 CT）。
- 也未限制 JSON 嵌套深度（默认 Python `json` 解析栈深度上限较大，深嵌套可触发栈溢出 / 慢解析）。

**Fix sketch**:
```python
MAX_JSON_BODY_BYTES = 256 * 1024  # 256 KiB；webui 表单足够
async def read_json_object(request):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_JSON_BODY_BYTES:
        return None, api_error(status_code=413, code="payload_too_large", message="请求体过大")
    raw = await request.body()
    if len(raw) > MAX_JSON_BODY_BYTES:
        return None, api_error(...)
    # 可选：拒非 application/json
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        ...
```
保留现有 dict 校验；按需对 `application/json` 做软 CT 校验。

**Risk if unfixed**: 单请求 OOM；任何鉴权后端点都成 DoS 入口（webui 鉴权 cookie 一旦泄漏，可远程 OOM kill 进程）。

---

### HIGH-4 `render.py` 资产 helper 不防符号链接（symlink escape）
**File**: `server/routes/render.py:40-49`
**Dimension**: security
**Issue**:
```python
def _resolve_static_file(root: Path, raw_path: str) -> Path:
    file_name = unquote(raw_path).strip()
    file_path = (root / file_name).resolve()
    try:
        file_path.relative_to(root.resolve())
    ...
```
- `Path.resolve()` 解析符号链接到真实路径，**之后**才用 `relative_to(root.resolve())`。但 root 自身没强制 `resolve()`（即 `root.resolve()` 在 `relative_to` 参数里调用，OK）。
- 真正的漏洞：如果 `ITEMS_DIR / DICTS_DIR / BOSS_IMGS_DIR / FONTS_DIR / CSS_DIR / LOGOS_DIR` 里包含**指向 root 外**的符号链接（运维误配、容器 bind-mount 套用别人卷、docker `volumes:` 指 `/etc`），`(root / file_name).resolve()` 会跟随到 root 外的真实路径，`relative_to` 立即抛 ValueError → 403，看似 OK。
- **但反方向**也成立：如果 root **本身**是个 symlink 到 `/`（如 `LOGOS_DIR = SERVER_DIR.parent / "logos"`，开发者把 logos 软链接到别处），`root.resolve()` 解析后是 `/some/where/logos`，`(root / "../../etc/passwd").resolve()` = `/etc/passwd`，`relative_to(/some/where/logos)` → ValueError → 403。OK。
- 实际安全风险点：**当 file_name 是路径穿越但解析后落在 root 内**（即没逃出）→ 把意外文件吐回去。例如 `LOGOS_DIR.parent`（`SERVER_DIR.parent` = 整个仓库根）下扔个 `.git/credentials`，攻击者请求 `/assets/imgs/logo-light.png` 没事但是这条 endpoint **写死了文件名**（`render.py:167-180`），不可控。
- **真正剩余风险**：`/assets/items/{file_path:path}` 等 5 个 `file_path:path` endpoint（`render.py:137-164`），任何 `file_name` 都进 root，**确认 root 内** OK。当下没文件吐出 root 外，但：
  - `unquote(raw_path).strip()` 没 reject `\x00` / `\r\n`，Python 3 `Path` 不接受 null bytes（会抛 ValueError），strip 只去前后空白；中段 `..` 已被 `relative_to` 拦截。
  - 但 **未明确拒 symlink 文件**——若 attacker 能在 ITEMS_DIR 内 cp 一个 symlink 指向 `/etc/passwd`（需要写权限），`.resolve()` 解析后变 `/etc/passwd`，`relative_to(items_dir)` 抛 ValueError → 403。OK。**但** 若部署里 `ITEMS_DIR` 本身被运维用 bind-mount overlay 其他目录覆盖，可能出现 root 内有非预期 symlink。
- 综合：**纯算法层 OK**；**部署/共享存储场景下推荐显式 reject symlink**。

**Fix sketch**:
```python
file_path = root / file_name
real = file_path.resolve(strict=True)
real.relative_to(root.resolve())
if real.is_symlink() or any(p.is_symlink() for p in file_path.parents if root in p.parents):
    raise HTTPException(status_code=403, detail="forbidden")
```
或直接 `pathlib.Path.resolve(strict=True)` + `os.path.realpath`（已含解链）+ root 自身 realpath 比对。

**Risk if unfixed**: 部署侧 symlink 误配 → 静态文件 endpoint 变成任意文件读。生产硬化建议项，纯代码层目前 PASS。

---

### HIGH-5 `_resolve_webui_static_file` 同 HIGH-4，无 symlink 防御
**File**: `server/routes/webui.py:261-269`
**Dimension**: security
**Issue**: 与 HIGH-4 同结构，对 `server/webui/static/` 目录。同样依赖 `.resolve() + relative_to` 双护栏，不显式拒 symlink。
**Fix sketch**: 同 HIGH-4。
**Risk if unfixed**: 同 HIGH-4，scope 仅 webui static 目录（CSS / JS / 字体）。

---

### MEDIUM-6 `render.py` 端点完全无鉴权 + token 是 16 字节随机 + 10 分钟 TTL，但 `/render/<page>/<token>` 一旦泄漏即可读任意用户的 inventory / progress / leaderboard / banlist / 红包 / 仓库 / 抽奖结果
**File**: `server/routes/render.py:52-134` + `server/page_store.py:8-26`
**Dimension**: security
**Issue**:
- `create_page` 用 `uuid.uuid4().hex`（122-bit 熵）；10 分钟 TTL。
- 用途：**仅本机 Playwright headless browser 截图**，URL 为 `http://127.0.0.1:{port}/render/...`（`web_server.py:35-36`）。
- 风险面：
  - **若部署把 host 改为 `0.0.0.0`**（`server_config.py:123` 默认 `127.0.0.1`，但 `web_server_host` 配置项可被覆盖）→ `/render/*` 直接外网暴露，10 分钟内拿到 token 即可看别人 inventory / 红包详情 / 抽奖结果（含未公开抽奖名单）。
  - **若运维把反代映射到 `/render/*`** → 同上。
  - Token 不绑用户、不绑 IP、不绑使用次数（同一 token 可被重复 GET 多次）。
  - 没有 `X-Forwarded-Proto` / referer 校验、没有 `127.0.0.1` only check。
- 当前 mitigation：`web_server_host` 默认 `127.0.0.1` + Playwright 走 `_build_internal_base_url`（`web_server.py:35`）写死 `127.0.0.1`，**外网默认访问不到**。但默认值在 `getattr(config, "web_server_host", "127.0.0.1")`，运维一改即破。

**Fix sketch**:
- `render.py` 在 `_render_page` 起头加 `if request.client.host not in {"127.0.0.1", "::1"}: raise HTTPException(403)`。或者改用 unix socket。
- 资产路由 (`/assets/items/*`) 是公共素材（图片 / 字体 / CSS），可保持开放；只有 `/render/<page>/<token>` 需要锁本机。
- token 可加 single-use 标记（首次 GET 后从 page_store 移除）—但截图重试场景需要 N 次 GET，需斟酌。
- 退一步：在 settings 注入 `render_bind_host` 与认证 header（playwright 调用时加 header），让 `/render/*` 走"localhost 才能调 + 私钥 header 双护栏"。

**Risk if unfixed**: host=0.0.0.0 误配后 10 分钟窗口内任何持有 token 字符串的人能看到该次截图（背包 / 银行余额 / 抽奖结果）。

---

### MEDIUM-7 `_decode_session_cookie` 过度宽 `except Exception` 吞所有 base64 / 解码错误
**File**: `server/routes/webui.py:83-99`
**Dimension**: security（observability）
**Issue**:
```python
try:
    raw = base64.urlsafe_b64decode((cookie_value + padding).encode("ascii"))
except Exception:
    return None
```
- 用 `except Exception` 覆盖 `binascii.Error` / `UnicodeEncodeError` / `ValueError`，但**任何其他真实 bug**（如 `cookie_value` 是 None 触发 TypeError）也会被静默成"cookie 无效"。
- 同样的逻辑在 `webui.py:90` `decoded = raw.decode("utf-8", errors="ignore")` 用 `errors="ignore"` 而非 `errors="strict"` —— 不合法字节默默丢，得到的 `decoded` 可能比期望短，仍能切出 3 段被当合法 cookie 处理（不会真通过 HMAC，但增加分析难度）。
- 没有日志，无法观察"是被探测了"还是"用户 cookie 被截断"。

**Fix sketch**:
- 改 `except (binascii.Error, ValueError):`；其他异常 propagate。
- `errors="ignore"` → `errors="strict"`，让 UnicodeDecodeError 也走"无效 cookie"分支但**保留 logger.debug** 一条便于排查。
- 校验失败时 `logger.debug(f"解析 cookie 失败：reason=base64/utf8")`，与 webui_session_create 失败日志风格一致。

**Risk if unfixed**: 调试 cookie 异常时无线索；潜在 mute 真 bug。

---

### MEDIUM-8 `_sanitize_next_path` 只防 `//` 协议跳转，未防"内部路径跳转到鉴权后的 logout"
**File**: `server/routes/webui.py:48-56`
**Dimension**: security
**Issue**:
- 当前防御：`""` → `/webui`；非 `/` 开头 → `/webui`；`//evil.com` → `/webui`。OK。
- 未防御：
  - `/webui/api/session?_method=DELETE` 类参数 smuggling（FastAPI 不响应，但前端可能误判）。
  - `/webui/login?next=/webui/login?next=...` 嵌套递归（无害，但占带宽）。
  - 没限制长度（`next=/webui/{6MB}` 进 URL 进日志）。
  - 没拒 path traversal（`/webui/../etc`，浏览器规范化掉了，但 server-side 不做兜底）。
- 严重度比较低，但 helper 的语义是 "sanitize"，应该更严。

**Fix sketch**:
```python
def _sanitize_next_path(value: str | None) -> str:
    cand = (value or "").strip()
    if not cand or not cand.startswith("/") or cand.startswith("//"):
        return "/webui"
    if len(cand) > 512:
        return "/webui"
    if not cand.startswith("/webui"):  # 强制只跳 webui 域内
        return "/webui"
    return cand
```
**Risk if unfixed**: 攻击者把 `next` 设成 `/health` / `/render/<token>` 让用户登录后被引导到非 webui 区；信息泄漏 / UX 异常。

---

### LOW-9 `_resolve_webui_static_file` 同时被 webui auth middleware 加入白名单 → 静态文件**不需要登录**就能下载
**File**: `server/routes/webui.py:199-203, 311-313`
**Dimension**: security（设计 trade-off）
**Issue**:
- middleware 白名单 `path.startswith("/webui/static/")`（`webui.py:202`）→ 任何 `/webui/static/*` 匿名可访问。
- 当前 `server/webui/static/` 内仅 CSS / JS / 图标（公共 UI 资产），不含敏感数据 → **可接受**。
- 但 **`startswith("/webui/static/")` 不是精确前缀匹配**，`/webui/static/../` 会被 FastAPI starlette path normalize 处理，无注入。
- 但 future-risk：若有人误把含用户信息的 JSON 投到 `webui/static/`，会被 middleware 跳过鉴权。

**Fix sketch**: 加注释（"此前缀下严禁放敏感数据"），或将 `static` 改为 `assets` 独立 router 并加 `auth_free=True` 元数据驱动。
**Risk if unfixed**: 误投 → 信息泄漏。低概率。

---

### LOW-10 `read_pagination_query` 默认 cap=100 合理，但单调用方不能配 max
**File**: `server/routes/__init__.py:115-141`
**Dimension**: security（DoS 防御 / API 设计）
**Issue**:
- `max_per_page=MAX_PER_PAGE = 100` 已上限，OK 防止 `per_page=10000` 大查询。
- 但 helper 暴露的 `max_per_page` 参数让调用方可以**放大**到任意值（如 `read_pagination_query(request, max_per_page=10000)`），实际 4 个 caller 全部用默认 100，OK。
- 没有"sane upper bound" 终极限制（即让 caller 即使传 10000 也被钳到 1000）。

**Fix sketch**:
```python
HARD_MAX_PER_PAGE = 1000  # absolute ceiling regardless of caller param
def read_pagination_query(..., max_per_page=MAX_PER_PAGE):
    ceiling = min(max_per_page, HARD_MAX_PER_PAGE)
    ...
```
**Risk if unfixed**: 未来 caller 误传大值放大 DoS。

---

### LOW-11 `_parse_positive_int` 不防 `int(text)` 对超大字符串的解析开销
**File**: `server/routes/__init__.py:71-112`
**Dimension**: security（CPU DoS）
**Issue**:
- Python 3.11+ 对 `int(<超长十进制字符串>)` 有内置 4300 位上限保护（PEP 0651 / CVE-2020-10735），抛 `ValueError`。
- 但 3.10 及以下没保护：`int("9" * 10_000_000)` 触发分配大整数耗 CPU。
- 项目 `pyproject.toml` 限定的 Python 版本未知；建议 helper 自己加 `len(text) > 32` 早 reject。

**Fix sketch**:
```python
text = str(raw_value or "").strip()
if len(text) > 32:
    return None, api_error(status_code=400, code="invalid_query_parameter", ...)
```
**Risk if unfixed**: 在旧 Python 上有 CPU-DoS 隐患；3.11+ 自动 mitigated。

---

### INFO-12 session cookie `secure=False` 硬编码（与 login-audit A1 同根，已在 backlog）
**File**: `server/routes/webui.py:140-148`
**Dimension**: security
**Issue**: 见 `archive/2026-05/05-13-webui-login-audit/research/backend.md` A1。本 bucket 不重复。

---

### INFO-13 7-day cookie 无服务端 force-revocation API（与 login-audit A2 同根）
**File**: `server/routes/webui.py:33, 138-148, 392-405`
**Dimension**: security
**Issue**:
- jti store `_active_sessions` 只允许"已知 jti 退出"；没有"踢所有用户"API。
- 进程重启清空全部 jti（OK，明确 trade-off）。
- 任何 jti 在 7 天 TTL 内有效，无 sliding refresh。被 login-audit 已记录。

---

### INFO-14 brute-force window 限 IP-only，组合 CRITICAL-1 后失效
**File**: `server/routes/webui.py:42-44, 162-192`
**Dimension**: security
**Issue**: 与 CRITICAL-1 同根。限速桶 key 用 `_client_ip(request)` 返回值，IP 一旦被伪造，限速被旁路。
**Fix sketch**: 修 CRITICAL-1 即可。

---

### INFO-15 `_failed_login_history` 无 size cap，攻击者刷不同伪造 IP 让 dict 涨爆
**File**: `server/routes/webui.py:44, 162-192`
**Dimension**: security
**Issue**:
- `_failed_login_history: dict[str, deque[float]]` 没有最大键数限制。
- 组合 CRITICAL-1（XFF 伪造）→ 攻击者每次伪造一个新 IP 失败一次 → dict 每次新增一个 deque。
- 5 分钟内可塞数百万 key → 进程内存涨。
- 当前 `_check_login_rate_limit` 内只在某 IP 命中时才清过期，**未命中过的 IP 永不清**（注：每次失败创建 deque、5 分钟后再失败该 IP 时才会清；从未失败的 IP 不会进 dict——OK 这点没问题）；但**首次失败后 5 分钟内不再被这 IP 失败 → deque 留着，直到下次命中查询**。

**Fix sketch**:
```python
MAX_TRACKED_IPS = 10_000
if len(_failed_login_history) > MAX_TRACKED_IPS:
    # lazy GC：清最旧的一批
    cutoff = now - _FAILED_LOGIN_WINDOW_SEC
    _failed_login_history = {k: v for k, v in _failed_login_history.items()
                              if v and v[-1] >= cutoff}
```
**Risk if unfixed**: 配合 CRITICAL-1 = 内存 DoS。修 CRITICAL-1 后风险降为"真实多 IP 攻击下 dict 慢涨"，仍建议加 size cap。

---

### INFO-16 session secret 等敏感配置通过 `request.app.state.server_settings` 传递（OK）
**File**: `server/routes/webui.py:272-273`
**Dimension**: security
**Issue**: 通过 app.state 单次注入 + 全局共享 settings 对象，无序列化，无泄漏路径。OK。

---

## 维度二：Performance

### LOW-17 middleware 调用链开销可接受
**File**: `server/routes/webui.py:195-226, 250-258`
**Dimension**: perf
**Issue**:
- 每请求 webui_auth + security_headers 两层 middleware：
  - auth：path startswith 3 次、cookie get 1 次、HMAC 校验 ~微秒、session_store set lookup O(1)。
  - security_headers：dict update 4 项。
- 总 overhead ~10-50µs，可忽略。

**Fix sketch**: 无需。
**Risk if unfixed**: 无。

---

### MEDIUM-18 `_active_sessions` 用 `threading.Lock` + `set`，在 async 上下文里阻塞事件循环
**File**: `server/routes/webui.py:38-39, 102-123, 136-148, 392-405`
**Dimension**: perf
**Issue**:
- `with _active_sessions_lock:` 是同步 lock；在 async middleware 里同步持锁 → 短锁段 OK，但本机若 hundreds-of-rps 时是 fast-path 上的串行化点。
- 同理 `_failed_login_lock`、`_failed_login_history`。
- 当前流量小（内部 webui 工具），实际不构成瓶颈。

**Fix sketch**: 换 `asyncio.Lock`，或保持现状但加 ack 注释"假设 <100 rps，不优化"。
**Risk if unfixed**: 高并发下事件循环短暂卡顿。

---

### MEDIUM-19 `render.py` 端点完全同步 + 无并发限制
**File**: `server/routes/render.py:24-37`
**Dimension**: perf
**Issue**:
- `_render_page` 调用 `renderer(payload)` —— 各 `*_page.render()` 是 CPU-bound HTML 渲染（PIL / Jinja2 / 模板替换）。
- 函数定义为 `async def` 但**内部全是同步代码**——`renderer(payload)` 不 await，直接阻塞事件循环。
- 单 worker uvicorn（`web_server.py:404`）+ 17 个 render endpoint 同时被截图调用，会串行 → screenshot 任务排队。
- 上游 `nextbot/plugins/player_query.py` 同时有 N 个用户跑 `用户背包` → N 个 playwright `page.goto(/render/...)` 落到 N 个 FastAPI 协程上，每个 await `await page.screenshot(...)` 之前先做同步 renderer + Response 序列化，事件循环卡住直到 renderer 返回。
- **现有上层 mitigation**：`player_query.py` 已加 module-level semaphore 限制 playwright 并发（prior audit `archive/2026-05/05-08-player-query-audit` 提及）；所以"上游并发上限"已封堵。但 `_render_page` 没自己的护栏。

**Fix sketch**:
```python
@router.get("/render/inventory/{token}")
async def render_inventory(token: str) -> Response:
    return await _render_page(token, page_type="inventory",
                              renderer=inventory_page.render)

async def _render_page(token, *, page_type, renderer):
    payload = get_page(token)
    if ...:
        raise HTTPException(404, "page not found")
    try:
        # 把 CPU-bound renderer 推到 threadpool，让事件循环回弹
        content = await asyncio.to_thread(renderer, payload)
    except OSError as exc:
        raise HTTPException(500, "template read error") from exc
    return Response(content=content, media_type="text/html; charset=utf-8")
```
**Risk if unfixed**: 高并发截图请求时事件循环抖动；webui 其他 API 响应变慢。

---

### LOW-20 `page_store._cleanup_expired_pages` 每次 get / create 都 O(n) 扫
**File**: `server/page_store.py:14-22`（仅作为 render.py:30 调用链上下文）
**Dimension**: perf
**Issue**: 见 page_store.py。不在本 bucket scope。**Scope-out**。

---

### LOW-21 `read_json_object` 走 Starlette 默认 body parsing，缓存整个 body 进内存
**File**: `server/routes/__init__.py:51-68`
**Dimension**: perf
**Issue**: 与 HIGH-3 同根因。无 stream parsing → 大 body 占内存。修 HIGH-3 即缓解。

---

### INFO-22 `build_pagination_meta` / `build_pagination_slice` 都是纯算术，无 perf 问题
**File**: `server/routes/__init__.py:144-160`
**Dimension**: perf
**Issue**: OK。

---

## 维度三：UX / API consistency

### MEDIUM-23 `render.py` 错误响应是 `HTTPException(detail=str)`，**不走** `api_error` envelope
**File**: `server/routes/render.py:32-36, 46-48, 171, 179`
**Dimension**: ux (API consistency)
**Issue**:
- 项目约定（`.trellis/spec/backend/error-handling.md`）：JSON 端点必须用 `api_error(...)` 输出 `{"error":{"code":..,"message":..}}` envelope。
- render endpoint 输出 HTML 响应、所以错误也走 starlette 默认 `{"detail": "page not found"}`，**不一致**。
- 调用方是 Playwright（内部），不解析 envelope，**实际无业务影响**。但：
  - 误用：未来运维直接 curl `/render/inventory/<bad-token>` 拿到 `{"detail":"page not found"}`，与其他 webui endpoint 的 `{"error":{"code":"not_found",...}}` 风格不一致，排错时认知成本高。
  - 已有 spec 明确 "render/static routes may use HTTPException directly"（spec 行 102-103），所以这条**合规**，只是 UX 偏差。
- 同适用于 `webui.py:266, 268` 静态文件 HTTPException。

**Fix sketch**:
- 维持现状（spec 允许）；或为 render 单独定义 `render_error(status, code, msg)` helper，至少 message 字符串与项目其他错误（"内部错误" / "未找到"）保持一致。
- 文案 `"page not found"` 改 `"页面不存在"` 与 `"template read error"` 改 `"模板读取失败"` 与项目中文 message 一致。

**Risk if unfixed**: 内部一致性偏差，无功能问题。

---

### LOW-24 webui 鉴权 middleware 对 `/webui/api/*` 401 路径**没有 details 字段**，与其他 422 / 401 不对称
**File**: `server/routes/webui.py:216-220`
**Dimension**: ux (API consistency)
**Issue**:
- 401 unauthorized：`api_error(status_code=401, code="unauthorized", message="未登录")` — 无 details。
- 422 validation_error（`webui.py:362-364`）有 `details=[{"field":..,"message":..}]`。
- 401 vs 422 details 缺失符合 spec（401 表示鉴权失败，没有具体 field），OK。
- 但前端 `api.js` 检测 401 → 跳转登录页（见 archive `05-14-webui-auth-401-vs-302` PRD），不依赖 details，OK。

**Fix sketch**: 无需。
**Risk if unfixed**: 无。

---

### LOW-25 401 vs 403 边界含糊
**File**: `server/routes/webui.py:204-225, 366-377`
**Dimension**: ux
**Issue**:
- 现状：未登录全部 401 + `code=unauthorized`。
- 没有"已登入但权限不足"分支 —— 项目当前没有多角色概念（任何持 webui_token 者都是 admin），所以 403 用不上。
- 若未来加 RBAC，必须区分 401（未登录）vs 403（权限不足）。当前一致 OK。

**Fix sketch**: 无需。
**Risk if unfixed**: 未来 RBAC 时需要回头改。

---

### LOW-26 `webui.py:382-386` `Location` header 指向端点自己，语义弱（与 login-audit C6 同根）
**File**: `server/routes/webui.py:382-386`
**Dimension**: ux
**Issue**: 见 login-audit C6。本 bucket 不重复。

---

## 维度四：Copy（文案）

### MEDIUM-27 错误 message 部分混用动作 + 对象 + 结果，不完全符合 CLAUDE.md 后端规则
**File**: `server/routes/__init__.py:55-66, 86-110`；`server/routes/webui.py:218-220, 340-342, 359-377`；`server/routes/render.py:32, 36, 46, 48, 171, 179`
**Dimension**: copy
**Issue**:
按 CLAUDE.md 第 7 条："后端 error.message 应仅返回有效原因，不拼接'动作 + 结果'"。

| Location | Current message | 评估 |
|---|---|---|
| `__init__.py:58` | `"请求体必须是 JSON"` | OK，纯原因。✓ |
| `__init__.py:65` | `"请求体必须是对象"` | OK，纯原因。✓ |
| `__init__.py:86, 95, 104` | `"page 必须是整数"` / `"page 必须大于等于 1"` / `"page 必须小于等于 100"` | OK，纯原因。✓ |
| `webui.py:219` | `"未登录"` | OK，纯原因（鉴权拒绝场景）。✓ |
| `webui.py:341` | `"登录失败次数过多，请稍后再试"` | **拼接了"请稍后再试"动作建议** — 违反"不拼动作"原则。建议改 `"登录失败次数过多"`，让前端基于 429 + retry_after 决定文案。 |
| `webui.py:362-363` | `"Token 不能为空"` | OK，纯原因。✓ |
| `webui.py:376` | `"Token 错误"` | OK，纯原因。✓ |
| `render.py:32` | `"page not found"` | 英文 + 与其他中文 message 不一致；建议 `"页面不存在"`。 |
| `render.py:36` | `"template read error"` | 同上；建议 `"模板读取失败"`。 |
| `render.py:46` | `"forbidden"` | 同上；建议 `"禁止访问"`。 |
| `render.py:48` | `"not found"` | 同上；建议 `"文件不存在"`。 |
| `render.py:171, 179` | `"Logo not found"` | 同上；建议 `"Logo 不存在"`。 |

**Fix sketch**:
- `webui.py:341` 去掉"请稍后再试"。
- `render.py` 全部错误 message 改中文，与项目其他端点风格一致；或显式留英文并在 docstring 解释"render 端点 Detail 仅供内部 Playwright 调试，不面向用户"。

**Risk if unfixed**: 文案规范不一致；前端"动作 + 结果，原因"格式化时会重复"动作"。

---

## 跨文件 / Scope-out backlog

- **HIGH**: `_client_ip` 在 8 个文件里重复，根因是 `routes/__init__.py` 缺 helper。归本 bucket（见 HIGH-2）；扩散修复属于跨文件 backlog。
- **MEDIUM**: `_failed_login_history` 与其他模块的限速桶（warehouse / lottery / shop / settings / login_requests）应统一抽象成 `routes/rate_limit.py`，目前每个模块自己造轮子。归 backlog（"Bucket C - 业务路由共性 helper 下沉"）。
- **MEDIUM**: `page_store.py` 的 `_cleanup_expired_pages` 每次 get / create O(n) 扫，可换 heap。归 backlog（"server core - page_store 优化"）。
- **MEDIUM**: `web_server.py:35-36` 写死 `127.0.0.1` 是 render 端点的隐性鉴权基石；应该让 render endpoint 自己做 `request.client.host` localhost 校验而不是依赖配置（见 MEDIUM-6）。归 backlog 或本 bucket（取决于是否允许本任务修 render.py 内部新增 helper）。

---

## 总览统计

| Severity | 数量 | IDs |
|---|---|---|
| Critical | 1 | C1 (XFF 信任) |
| High | 4 | H2 (helper 未下沉), H3 (无 body cap), H4 (render symlink), H5 (webui static symlink) |
| Medium | 7 | M6 (render 无鉴权依赖 host 默认值), M7 (broad except), M8 (next_path 弱), M18 (sync lock in async), M19 (render 阻塞), M23 (render 不走 envelope), M27 (文案) |
| Low | 5 | L9, L10, L11, L24, L25 |
| Info | 6 | I12-16, I22 |
| **总计** | **23 主条 + 4 PASS/Info** | |

---

## Top 3 修复建议（按 ROI）

1. **CRITICAL-1 + HIGH-2 联动**：把 `_client_ip` 升级到 `routes/__init__.py` 并加 trusted-proxy 白名单 + 在 settings 配置项；一次性修掉"伪造 IP 旁路限速"的根因 + DRY 8 个文件副本。预估改动 ~30 行 + 8 处 caller 切换。
2. **HIGH-3**：`read_json_object` 加 256 KiB body size cap + 413 错误；改动 ~10 行；防 OOM。
3. **MEDIUM-6 + MEDIUM-19**：`render.py` 端点强制 `request.client.host in {127.0.0.1, ::1}` + 把 `renderer(payload)` 包到 `asyncio.to_thread`；改动 ~15 行；同时解决"host 误配即外泄"和"事件循环阻塞"两个问题。

---

## 不构成 finding（验证后 PASS）

- `api_success` / `api_error` envelope 设计与 spec 一致。
- `_sign_payload` 用 `hmac.compare_digest` + sha256（`webui.py:60-65`，`102-112`），timing-safe。
- `webui.py:269` 静态文件 helper 用 `Path.resolve()` + `relative_to`，路径穿越 PASS。
- `render.py` 各 endpoint 把 `payload.get("type") != page_type` 校验前置，token 串用错也不会被复用（`render.py:31-32`）。
- `build_pagination_meta` 对 `total=0` / `page>total_pages` 边界处理（`__init__.py:148`），数据安全 PASS。
- `_decode_session_cookie` 用 `split(".", maxsplit=2)` 控制段数（`webui.py:93`），无越界。
- `_set_session_cookie` 写 cookie 后 `_active_sessions.add(jti)`（`webui.py:138-148`）顺序正确，无 race（同 Lock 内）。
- security_headers middleware（`webui.py:250-258`）只对 `/webui` 前缀注入，不影响 `/render` 截图 PASS。
