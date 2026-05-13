# Research: HTTP / IO / 外部边界（infra audit bucket）

- **Query**: 审计 5 个基础设施文件的 HTTP / IO / 外部边界相关漏洞和性能问题
- **Scope**: internal
- **Date**: 2026-05-13
- **Target files**:
  - `nextbot/tshock_api.py`
  - `nextbot/large_image.py`
  - `nextbot/server_broadcast.py`
  - `nextbot/server_validation.py`
  - `nextbot/screenshot_render.py`

## 全部发现清单

| ID | 严重度 | 文件 : 行 | 简述 |
|---|---|---|---|
| I-1.1 | High | `tshock_api.py:75-83` | URL 拼接未校验 `server.ip`，DB 脏数据可触发 SSRF / URL 注入 / IPv6 解析失败 |
| I-1.2 | High | `tshock_api.py:77-83` | 响应体无大小上限：`response.content` / `response.json()` 把任意大 body 全部缓冲入内存，绕过下游 `MAX_BASE64_BYTES` |
| I-1.3 | Medium | `tshock_api.py:77` | 每次调用新建 `httpx.AsyncClient` 上下文（无连接池复用 / 无 Limits） |
| I-1.4 | Medium | `tshock_api.py:79-80` | `TShockRequestError` 吞掉所有 `httpx.RequestError` 子类，调用方拿不到超时 / DNS / TLS 区分 |
| I-1.5 | Medium | `tshock_api.py:82-85` | `response.json()` 只捕获 `ValueError`：极大或畸形 body 可能抛 `json.JSONDecodeError`（子类化 ValueError，OK）但同时丢失原始 payload 用于排障 |
| I-1.6 | Low | `tshock_api.py:60-61` | 调用方可通过 `params={"token": "..."}` 覆盖 `include_token` 注入的 token（无显式优先级文档 / 防御） |
| I-2.1 | Medium | `large_image.py:24-39` | `semaphore_for` 池条目永不删除：server 删表后 `dict[int, asyncio.Semaphore]` 仍持有信号量（小量级泄漏，但不会自动回收） |
| I-2.2 | Low | `large_image.py:36-38` | `pool.get(...) → if None → pool[...] = ...` 在协程切换点之间不保证原子（asyncio 单线程下安全，但接口未文档化「禁止 await 中间态」） |
| I-3.1 | Medium | `server_broadcast.py:66-68` | `asyncio.gather(..., return_exceptions=False)` 与「fn 内已 try/except」配合时是 OK，但任何在 `_wrap` 外层（`semaphore_for` / `async with sem`）抛错会向上冒泡 cancel 其它 task |
| I-3.2 | Low | `server_broadcast.py:51-64` | `except Exception` 把 `CancelledError` 一并吞掉（Py3.8 之后 `CancelledError` 不是 `Exception` 子类，OK；记为 info 留痕） |
| I-3.3 | Low | `server_broadcast.py:36` | `_broadcast_semaphores` 与 `large_image.semaphore_for` 同样存在 dict 条目不清理 |
| I-4.1 | Medium | `server_validation.py:64-73` | `_normalize_host` 只 strip 首尾 + 拒绝 `\n\r`：允许内部空格 / tab / `;` / 引号等任意字符流入 URL 拼接（被 `tshock_api.py:75` 直接 f-string） |
| I-4.2 | Low | `server_validation.py:21` | `_NAME_PATTERN = r"^[A-Za-z0-9一-鿿 ._-]{1,32}$"` 锚定且无嵌套量词，已确认无 ReDoS（实测 1e4 字符 < 0.02ms） |
| I-4.3 | Info | `server_validation.py:103-112` | `_normalize_token` 长度上限 128：与 TShock 默认 token 形态对齐，OK；下限只校验非空 → 允许「a」单字符 token 入库 |
| I-5.1 | Medium | `screenshot_render.py:121-204` | Playwright 重试循环：`get_browser()` → `new_context()` 之间若被取消（`asyncio.CancelledError`），`context` 未 `await close()`，且 `finally` 只在 `try` 体内 |
| I-5.2 | Medium | `screenshot_render.py:122-204` | 重启循环最多 2 次，但 `last_exc` 在第一次进入 `RenderScreenshotError` 分支前为 `None`：第 2 轮失败时 `RenderScreenshotError("...None")` 文本会泄露内部 sentinel |
| I-5.3 | Low | `screenshot_render.py:124-128` | viewport 无上限校验：`ScreenshotOptions(viewport_width=999999)` 直接传给 Chromium → OOM / GPU 资源拒绝 |
| I-5.4 | Low | `screenshot_render.py:177-186` | `content_height` 来自 `page.evaluate(...)` 是 JS-controlled：恶意页面可返回 `1e9` 触发巨型 viewport 分配（无 cap） |

---

## 详细发现

### I-1.1 [High] URL 拼接未校验 `server.ip`，路径段已 percent-encode 但 host 未做防御

**文件**: `nextbot/tshock_api.py:48-93`

`request_server_api` 第 57-58 行已经把 `path` 做了 percent-encoding（`quote(request_path, safe="/")`），但第 75 行：

```python
url = f"http://{server.ip}:{server.restapi_port}{safe_path}"
```

`server.ip` 直接 f-string 插入，没有任何 host-level 校验。结合 `server_validation._normalize_host`（见 I-4.1）只 strip 首尾 + 拒绝 `\n\r`：

- IP 可以包含**空格 / tab / 分号 / 引号**：例如 `127.0.0.1 ; rm -rf /` 通过 `_normalize_host` → URL 变成 `http://127.0.0.1 ; rm -rf /:7878/...`，httpx 会在第一个空格处截断并解析为相对 URL，可能触发意外路由 / DNS 查询。
- IPv6 地址（`::1`）未加方括号：URL 变成 `http://::1:7878/...`，httpx 会解析失败抛 `httpx.InvalidURL`，**被 catch 转成 `TShockRequestError` 但日志里只看到一个"无法连接服务器"**，运维难以诊断为何 IPv6 不工作。
- DB 里如果存在历史脏数据（迁移前缺少校验、或绕过 webui 直接 SQL 写入），通过 `tshock_api` 调用即可成为 SSRF 跳板（host 替换为内网 metadata endpoint 等）。

**触发条件**: 任何能间接控制 `server.ip` 列的入口（早期 webui handler / 直接 SQL / SQLite 文件被替换）。

**当前防御差距**: 
1. `tshock_api.py` 仅做 path 的 percent-encoding，不做 host
2. `server_validation._normalize_host` 仅做 length + newline 校验，**允许任意 ASCII 字符流入 URL**

**修复方向**: 在 `_normalize_host` 强制 IP 形态（IPv4 字面量 / IPv6 字面量 / 受限 DNS-name 子集），或在 `tshock_api.py` 调用 `httpx.URL.build(scheme="http", host=server.ip, port=int(...), path=safe_path)` 让 httpx 做规范化（IPv6 会被自动加方括号，非法字符会抛 `InvalidURL`）。

---

### I-1.2 [High] 响应体无大小上限：`response.content` 把任意大 body 全部缓冲入内存

**文件**: `nextbot/tshock_api.py:77-83`

```python
async with httpx.AsyncClient(timeout=effective_timeout) as client:
    response = await client.get(url, params=query)
...
payload = response.json() if response.content else {}
```

`httpx.AsyncClient.get` 默认是 buffered（非 stream），`response.content` 触发**整个响应体读入内存**。下游 `MAX_BASE64_BYTES = 200 MB` 的 cap 是**字符串长度** check（`len(b64) > _MAX_BASE64_BYTES`，见 `server_tools.py:269` / `player_query.py:736`），但 200 MB 的 base64 payload 在被 `response.json()` 解析时，**httpx 已经在内存里持有 ~200 MB 原始字节 + json 又复制成 dict + 字符串 → 峰值约 600-800 MB**。

更严重的是：恶意 / 故障 TShock 后端可以返回任意大的 body（比如 10 GB），httpx 会**先把它读完再交给我们**。`MAX_BASE64_BYTES` cap 此时已经太晚——进程在 httpx 内部就 OOM。

**触发条件**: 后端 bug / 攻击者控制 TShock 进程、或代理中间人注入。

**当前防御差距**:
1. `httpx.AsyncClient` 未传 `limits=httpx.Limits(...)` / `max_response_bytes`（httpx 1.x 实际无 max_body_size 选项，需通过 `stream=True` + 手动累加 chunk 实现）
2. `effective_timeout.read=300.0` 给了攻击者 5 分钟把任意数据塞进来

**修复方向**: 改用 `async with client.stream("GET", url, params=query) as response:`，在 `aiter_bytes()` 循环里累加到阈值即抛 `TShockRequestError("响应过大")`。

---

### I-1.3 [Medium] 每次调用新建 `httpx.AsyncClient`，无连接池复用 / 无 Limits

**文件**: `nextbot/tshock_api.py:77`

```python
async with httpx.AsyncClient(timeout=effective_timeout) as client:
    response = await client.get(url, params=query)
```

每次 `request_server_api` 调用：
1. 新建 TCP + TLS 握手（虽然 TShock 是 http）
2. 退出 `async with` 立即关闭连接，**无 keep-alive 复用**
3. 没有 `limits=httpx.Limits(max_connections=..., max_keepalive_connections=...)`，并发暴涨时 fd 用量无上限

`tshock_api` 被 `ban_core` / `lottery` / `shop` / `user_manager` / `security` / `player_query` / `server_tools` 高频调用，且 `server_broadcast.broadcast` 对所有 server 并发 fan-out。一次 ban 同步 N 服 × M 用户 = N×M 个新连接。

**修复方向**: 模块级单例 `httpx.AsyncClient`（如 `screenshot_render.py` 的 `_PlaywrightSession` 模式），生命周期挂 `get_driver().on_shutdown`。

---

### I-1.4 [Medium] `TShockRequestError` 吞掉所有 `httpx.RequestError` 子类，丢失语义信息

**文件**: `nextbot/tshock_api.py:79-80`

```python
except httpx.RequestError as exc:
    raise TShockRequestError from exc
```

调用方拿到的 `TShockRequestError` 无法区分：
- `httpx.ConnectTimeout` / `httpx.ReadTimeout`（超时）
- `httpx.ConnectError`（DNS / 不可达）
- `httpx.InvalidURL`（host 畸形，见 I-1.1）
- `httpx.RemoteProtocolError`（TShock 响应损坏）

所有调用方（`ban_core.py:157` / `lottery.py:161` / `user_manager.py:75` …）都只能 `reply_failure("查询", "无法连接服务器")`，**屏蔽了"超时 vs 网络不通 vs URL 畸形"的关键诊断信息**。

实例：I-1.1 描述的 IPv6 host 不加方括号问题会抛 `httpx.InvalidURL` → 被归并成"无法连接"，运维盯日志一周也定位不出来。

**修复方向**: `TShockRequestError` 接受 `kind: Literal["timeout", "unreachable", "invalid_url", "protocol"]` 字段；或保留 `__cause__` 让调用方按需 unwrap。

---

### I-1.5 [Medium] `response.json()` 异常处理只接 `ValueError`，OK 但丢失诊断信息

**文件**: `nextbot/tshock_api.py:82-85`

```python
try:
    payload = response.json() if response.content else {}
except ValueError:
    payload = {}
```

`json.JSONDecodeError` 是 `ValueError` 子类，所以 catch 是完备的。但：
1. **payload 静默置空**：调用方 `is_success(resp)` 会因为 `api_status=""` 而走"返回数据格式错误"的兜底，看不到「后端返回了非 JSON 的 N 字节内容」。
2. **没有 size cap**：与 I-1.2 是同一根问题——`response.content` 已经读了全量字节。

**修复方向**: 失败时 logger.warning 记录 `len(response.content)` + `content_type` 头部，方便排障。

---

### I-1.6 [Low] 调用方可通过 `params` 覆盖 token

**文件**: `nextbot/tshock_api.py:60-61`

```python
query = dict(params or {})
if include_token and "token" not in query:
    query["token"] = server.token
```

`if "token" not in query` 让调用方可以传 `params={"token": "evil"}` 显式覆盖。理论上调用方都是 trusted code，但：
- 防御性编程角度，token 应是 server-owned，不允许 caller 覆盖
- 没有 logger 警告 / assert，错误使用难以发现

**修复方向**: 改为 `query.setdefault("token", server.token)` 不变，但 `params` 文档化为不接受 `token` key，或直接 `raise ValueError` 当 caller 误传。

---

### I-2.1 [Medium] `semaphore_for` 池条目永不清理

**文件**: `nextbot/large_image.py:24-39`

```python
def semaphore_for(pool, server_id, *, max_concurrent=1):
    sem = pool.get(server_id)
    if sem is None:
        sem = asyncio.Semaphore(max_concurrent)
        pool[server_id] = sem
    return sem
```

`pool` 由调用方持有（模块级 dict）。当：
1. 一个 server 被 webui 删除（DELETE FROM server WHERE id=N）
2. 后续不再有针对该 server_id 的请求

`pool[N]` 中的 `asyncio.Semaphore` 永远留在 dict 里。**单次实例化几乎无开销**（asyncio.Semaphore 是个小对象，几百字节），但：
- 长期运行 + 频繁增删 server 会有持续小泄漏
- 所有调用方（`server_tools._map_semaphores` / `server_tools._download_semaphores` / `player_query._inventory_semaphores` / `player_query._my_map_semaphores` / `player_query._user_map_semaphores` / `player_query._explored_map_semaphores` / `player_query._progress_semaphores` / `server_broadcast._broadcast_semaphores`）都受影响

**修复方向**: 提供 `release_server(pool, server_id)` 在 webui DELETE / bot remove-server handler 调用；或弱引用 / 使用 `weakref.WeakValueDictionary`（但 Semaphore 没有 caller 持引用时会被回收，破坏并发约束，所以 weakref 不适用）。**实际推荐**：在 server 删除 handler 主动调用 cleanup。

---

### I-2.2 [Low] `pool.get → pool[...] = ...` 非原子，但在 asyncio 单线程下安全

**文件**: `nextbot/large_image.py:36-38`

```python
sem = pool.get(server_id)
if sem is None:
    sem = asyncio.Semaphore(max_concurrent)
    pool[server_id] = sem
```

该函数体内**没有 await**，asyncio 协作式调度下不会被其它 task 抢占，**当前实现安全**。但：
- 函数 docstring 没有约束「禁止在调用方传入会触发 await 的 pool」（理论上 dict 永远不会 await，但保险起见应文档化）
- 如果未来该函数被改为 async（看似无害），就会出现 TOCTOU：两个并发 task 都看到 `sem is None`，分别创建 + 写入，第二个覆盖第一个，**已经持有第一个 sem 的 task 释放后不影响第二个，并发限制被打破到 2N**

**修复方向**: 在 docstring 显式标注"必须保持同步，禁止 await"；或加 `assert not asyncio.iscoroutinefunction(...)` 防御。

---

### I-3.1 [Medium] `gather(return_exceptions=False)` 与外层 try/except 的微妙交互

**文件**: `nextbot/server_broadcast.py:51-69`

```python
async def _wrap(srv: Server) -> BroadcastOutcome[R]:
    sem = semaphore_for(_broadcast_semaphores, srv.id, max_concurrent=max_concurrent_per_server)
    async with sem:
        try:
            return await fn(srv)
        except Exception as exc:  # noqa: BLE001
            ...
            return BroadcastOutcome(server=srv, ok=False, detail=str(exc) or "异常", payload=None)

results = await asyncio.gather(*(_wrap(s) for s in servers), return_exceptions=False)
```

设计意图是"`fn` 内异常都被 catch，所以 gather 不会拿到 exception"。但：
1. **`semaphore_for(...)` 在 try 块之外**：理论上 `dict.get` / `dict[...] = ...` 不抛错，但任何未来重构（比如加 logger / 改成 async）就会破窗
2. **`async with sem` 的 `__aenter__` 在 try 块之外**：Semaphore.acquire 理论上不抛错，但若调用方传了一个错误的 sem 对象（type 错误）就会破窗
3. **break case**: 若任何 `_wrap` 抛 `Exception` → `gather(return_exceptions=False)` 会立即把 exception 重抛给调用方，同时**cancel 其它在运行的 `_wrap`**——这些 task 拿到 `CancelledError`，**会在 `async with sem` 退出时正常 release sem**，但 `fn(srv)` 内部的 HTTP 请求被中断、半成品状态留在 TShock 后端

**触发条件**: 极低（需要 `_wrap` 体内 try 块外的代码抛 Exception）。但一旦触发后果严重：部分 server 已经 mutate、其它 server 被中断，调用方拿到 exception 误以为全部失败 → 重试 → 在已成功的 server 上重放 → 业务错。

**修复方向**: 把 `semaphore_for(...)` / `async with sem` 也包到 try/except 内；或 `gather(return_exceptions=True)` + 在 `gather` 调用方再 narrow 处理。

---

### I-3.2 [Low] `except Exception` 与 `CancelledError` 关系（Py3.8+ 已修复，仅记录）

**文件**: `nextbot/server_broadcast.py:58`

Python 3.8+ 起 `asyncio.CancelledError` 直接继承 `BaseException`（不再是 `Exception` 子类），所以 `except Exception` **不会**吞掉取消信号。当前代码安全。仅留 info 提示：任何后续代码降级到 Py3.7 之前会破窗。

---

### I-3.3 [Low] `_broadcast_semaphores` 同样存在 dict 条目不清理

**文件**: `nextbot/server_broadcast.py:36`

同 I-2.1。模块级 `_broadcast_semaphores: dict[int, asyncio.Semaphore] = {}` 没有清理钩子。

---

### I-4.1 [Medium] `_normalize_host` 校验过松，允许任意 ASCII 字符流入 URL

**文件**: `nextbot/server_validation.py:64-73`

```python
def _normalize_host(raw_value: Any) -> str:
    value = str(raw_value).strip()
    if not value:
        raise ServerPayloadValidationError("服务器地址不能为空", field="ip")
    _check_no_newline(value, field="ip")
    if len(value) > _MAX_IP_LENGTH:
        raise ServerPayloadValidationError(...)
    return value
```

只检查：
1. 非空
2. 无 `\n\r`
3. 长度 ≤ 128

允许：
- 内部空格 / tab：`"127.0.0.1\tEVIL"`
- 分号 / 引号 / `#` / `?`：`"127.0.0.1;evil"`
- 完整 URL：`"http://attacker.com:80/?x="` —— 与 `f"http://{ip}:..."` 拼成 `http://http://attacker.com:80/?x=:7878/...`，httpx 会按真实 host 路由（虽然实际场景下 `:7878` 会让 httpx 抛 InvalidURL，但 host 字段已被劫持的风险存在）

**文档已声明**："ip 仅做长度与非空校验（受当前部署 / 网络环境约束，不强限制 host 形态）"。但下游 `tshock_api.py:75` **直接** f-string 进 URL，没有补做防御（I-1.1）。

**修复方向**: 二选一：
1. 在 `_normalize_host` 强制 IPv4 / IPv6 字面量 / 受限 hostname（最严）
2. 在 `tshock_api.py:75` 改用 `httpx.URL.build(...)` 让 httpx 拒绝非法字符（最低改动）

---

### I-4.2 [Low / Verified safe] `_NAME_PATTERN` 无 ReDoS

**文件**: `nextbot/server_validation.py:21`

```python
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9一-鿿 ._-]{1,32}$")
```

锚定（`^...$`）+ 单字符类 + 上限 32 + 无嵌套量词。Python `re` 引擎对该模式无灾难性回溯。结论：**OK，无 ReDoS 风险**。

---

### I-4.3 [Info] token 下限为 1 字符

**文件**: `nextbot/server_validation.py:108-111`

```python
if not 1 <= len(value) <= _MAX_TOKEN_LENGTH:
```

允许「a」单字符 token。TShock 实际颁发的 token 是 32+ 位的 base32，但 validation 不强制最小长度，**允许运维误配进入**。

**触发条件**: 运维 typo / 手动 SQL。

**修复方向**: 提一个 reasonable 下限（如 16）。但严格说不是 infra bug，是策略选择。

---

### I-5.1 [Medium] Playwright 重试循环的 context 泄漏窗口

**文件**: `nextbot/screenshot_render.py:121-216`

```python
for attempt in (1, 2):
    try:
        browser = await _session.get_browser()
        context = await browser.new_context(...)
        try:
            page = await context.new_page()
            ...
        finally:
            with contextlib.suppress(Exception):
                await context.close()
    except RenderScreenshotError:
        raise
    except (PlaywrightError, ConnectionResetError) as exc:
        ...
```

`context = await browser.new_context(...)` 在 try 块**内部**，但 try 块捕获的异常类型包括 `PlaywrightError`。如果 `new_context()` 自己抛 `PlaywrightError`（比如 browser 已断开），异常会被外层 `except (PlaywrightError, ConnectionResetError)` 接住、`_session.close()` 后进入下一轮——**但内层的 `finally` 块从未执行（因为没到 page = ... 那一步，context 已经创建失败）**。

实际行为：`new_context()` 抛错时 context 通常不会成功返回，Playwright 内部会自己清理。但代码字面意义是：**`browser = await _session.get_browser()` 与 `context = await browser.new_context(...)` 之间被 cancel**（外层 task 被取消）时，`context` 已被部分初始化但没有 await close 路径——**仅在 cancel 场景下有 BrowserContext 泄漏窗口**。

**触发条件**: 调用方 task 被 cancel（例：HTTP 客户端断连导致 nonebot 取消整个 handler）。

**修复方向**: 改用 `async with` 风格 BrowserContext（Playwright 支持），让取消时自动释放。

---

### I-5.2 [Medium] `last_exc` 在第二轮失败时可能携带 `None`

**文件**: `nextbot/screenshot_render.py:120-220`

```python
last_exc: Exception | None = None
for attempt in (1, 2):
    try:
        ...
    except (PlaywrightError, ConnectionResetError) as exc:
        ...
        last_exc = exc
        continue
    except Exception as exc:
        raise RenderScreenshotError(f"截图失败：{exc}") from exc

raise RenderScreenshotError(f"截图失败（重启浏览器后仍未恢复）：{last_exc}") from last_exc
```

`last_exc` 只在 `except (PlaywrightError, ConnectionResetError)` 分支被赋值。若：
- 第一轮 `return` 成功 → 不到这行（OK）
- 第一轮 `raise RenderScreenshotError` → 直接 reraise（OK）
- 第一轮 `except Exception` → 直接 raise（OK，不到尾部）
- **第一轮 PlaywrightError → 第二轮 PlaywrightError → 走到尾部 raise**：此时 `last_exc` 是第二次的 exc（OK）
- **特殊**: 当代码循环结构变化（例如未来加第三次重试或改为 while 循环），`last_exc = None` 时尾部 raise 会输出 `截图失败（重启浏览器后仍未恢复）：None`

当前 2 次循环 + 每次必然走某个分支的结构是安全的。但**字面 fragile**——任何修改都可能破窗。

**修复方向**: `raise RenderScreenshotError(...) from last_exc` 改为 `assert last_exc is not None` 显式约束；或重构成 `for attempt in range(MAX_RETRIES)` + 明确终止状态。

---

### I-5.3 [Low] viewport 无上限校验

**文件**: `nextbot/screenshot_render.py:124-128`

```python
context = await browser.new_context(
    viewport={
        "width": render_options.viewport_width,
        "height": render_options.viewport_height,
    }
)
```

`ScreenshotOptions(viewport_width=2000, viewport_height=1000)` 是默认值，但**调用方可以传任意大小**。若有 plugin 写：
```python
options = ScreenshotOptions(viewport_width=50000, viewport_height=50000)
```

会让 Chromium 尝试分配 50000×50000×4 = 10 GB 显存/RAM → OOM 或被 OS 杀。**当前所有调用方传的都是合理值**（grep `viewport_width=` 看：`leaderboard.py`, `lottery.py` 等都是 100-2000 区间），但 ScreenshotOptions 没有 dataclass 后置 `__post_init__` 校验。

**修复方向**: `ScreenshotOptions.__post_init__` 校验 width / height ≤ 8192（典型上限）。

---

### I-5.4 [Low] `content_height` 来自 page-JS，未 cap

**文件**: `nextbot/screenshot_render.py:177-186`

```python
if render_options.fit_content_height:
    content_height = await page.evaluate(
        "Math.ceil(document.body.getBoundingClientRect().bottom)"
    )
    fit_height = max(int(content_height), 1)
    await page.set_viewport_size({
        "width": render_options.viewport_width,
        "height": fit_height,
    })
```

`page.evaluate(...)` 返回页面 JS 的计算值——**页面内容控制**。如果 webui 模板有 bug 渲染出 `body { height: 99999999px }`，或恶意页面用 `Object.defineProperty(document.body, 'getBoundingClientRect', () => ({ bottom: 1e9 }))`：

- `fit_height = 1_000_000_000`
- `page.set_viewport_size({width:2000, height:1e9})` → Chromium 分配巨型 viewport → OOM

**当前缓解**: 所有 fit_content_height=True 的调用方都是渲染**内部 webui 模板**（grep `fit_content_height=True` 看：lottery / leaderboard / red_packet 都是 trusted），但**没有 cap**。

**修复方向**: `fit_height = min(max(int(content_height), 1), MAX_VIEWPORT_HEIGHT)`，例如 16384。

---

## Caveats / Not Found

### 已审范围但无问题

- `large_image.MAX_BASE64_BYTES = 200 * 1024 * 1024` cap 设计本身 OK（与 OneBot V11 base64 image 限制对齐）
- `large_image.LONG_READ_TIMEOUT` connect=5 / read=300 / write=10 / pool=5 数值合理
- `server_broadcast.BroadcastOutcome` NamedTuple + Generic[R] API 形态稳定，调用方契约清晰
- `screenshot_render.render_and_send_screenshot` 的 0-byte 早返回（line 110-115）已在 R5-3.1 修复，验证通过
- `screenshot_render` 的 V11/非 V11 分支（line 125-162）行为对称，文档化清晰
- `server_validation._normalize_port` 边界处理完整：拒绝 bool、拒绝小数部分非零的 float、拒绝空字符串、范围 [1, 65535]
- Playwright 的 `_PlaywrightSession._lock`（screenshot.py:75）正确序列化 launch / close
- `screenshot_render` 的 semaphore acquire / release 用 `async with`，异常路径自动 release（OK）

### 未审 / 不在 bucket 范围

- TShock 协议本身的语义正确性（PRD 显式排除）
- plugin 业务层（已 6 轮 sweep，本桶不重复）
- `screenshot_temp.py` 在 utils 桶
- `server/screenshot.py` 不在 5 个目标文件内，仅作为依赖参考读取

### 验证方法

每个发现都可以用 `Read` 工具按以下行号验证：

| ID | 验证命令 |
|---|---|
| I-1.1 | `Read tshock_api.py:55-80` |
| I-1.2 | `Read tshock_api.py:75-90` |
| I-1.3 | `Read tshock_api.py:77` |
| I-1.4 | `Read tshock_api.py:79-80` |
| I-2.1 | `Read large_image.py:24-39` |
| I-3.1 | `Read server_broadcast.py:51-69` |
| I-4.1 | `Read server_validation.py:64-73` |
| I-5.1 | `Read screenshot_render.py:121-216` |
| I-5.2 | `Read screenshot_render.py:120-220` |
| I-5.3 | `Read screenshot_render.py:124-128` |
| I-5.4 | `Read screenshot_render.py:177-186` |
