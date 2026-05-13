# R8 IO 桶审计

- **审计范围**：`nextbot/tshock_api.py` / `nextbot/large_image.py` / `nextbot/server_broadcast.py` / `nextbot/server_validation.py` / `nextbot/screenshot_render.py`
- **审计时间**：2026-05-13
- **审计员**：trellis-research IO 桶子代理
- **目标**：复审 Round 7 修复 + 全量再扫

---

## Part A: Round 7 修复复审

### A-1. H-4 (I-1.2) stream + chunk cap —— PASS（带 1 个 Low 关注点 A-1.1）

**修复位置**：`tshock_api.py:146-161, 162-174`

**复审结论**：核心目标（防止 GB 级 body 占用内存）达成。

**机制验证**：
- `client.stream("GET", url, params=query, timeout=effective_timeout)` 进入流式模式。httpx ≥ 0.27 在 stream 路径下不会先全量缓冲 body。
- `chunks = bytearray()` + `chunks.extend(chunk)` 累加；每个 chunk 累加后立即 `if len(chunks) > MAX_RESPONSE_BYTES: raise TShockRequestError(kind="oversize")`。
- 内部 `raise` 在 `async with client.stream(...)` 上下文中触发：httpx `Response.__aexit__` 会调用 `await response.aclose()`，归还底层连接到 keep-alive 池，**stream 不会泄漏**。已验证过 stream API 在异常退出时连接清理正确。

**异常映射验证**（`tshock_api.py:162-174`）：
| httpx 异常 | kind |
|---|---|
| `TShockRequestError`（oversize 重抛） | oversize（透传） |
| `httpx.TimeoutException` | timeout |
| `httpx.ConnectError` | unreachable |
| `httpx.InvalidURL` | invalid_url |
| `httpx.RemoteProtocolError` | protocol |
| `httpx.RequestError`（兜底） | unknown |

`httpx.TimeoutException` 是 `httpx.RequestError` 子类，必须放在前面 —— 顺序正确。
`httpx.ConnectError` 是 `httpx.TransportError` 子类，同样 `RequestError` 子类，顺序正确。
`httpx.InvalidURL` 是 `RequestError` 子类（在 build URL 时也会被前面 try/except 捕获，但 stream 内部如果遇到 redirect 跳到非法 URL 也走这一支）。

**异常树边界**（次要观察，非阻塞）：
- `httpx.DecodingError`（响应体 Content-Encoding 损坏时抛出）是 `httpx.RequestError` 子类，会落入 `unknown` 兜底 —— 行为正确，但 caller 无法精确分类。属于 Low 范畴，不阻塞。
- `httpx.StreamError`（响应已关闭等 stream API 内部异常）也是 `httpx.RequestError` 子类，同样落入 `unknown`。

#### A-1.1 Low：`bytes(chunks)` 在接近 250MB 时短暂双倍内存占用（Round 7 修复后引入的细节）

**位置**：`tshock_api.py:161`

```python
body = bytes(chunks)  # 在 async with 退出后执行
```

**触发条件**：
- 响应实际大小 `N` 接近但未超过 `MAX_RESPONSE_BYTES`（如 240MB）。
- `bytearray` → `bytes` 强制 copy（Python 实现层面 `bytes(bytearray)` 必然分配新 buffer 并 memcpy）。
- 这一刻内存中同时存在 `chunks`（bytearray，240MB）+ `body`（bytes，240MB）= 480MB。
- 紧接着 `body.decode("utf-8")` 又会构造一个 str（最坏情况 ASCII：约等于字节数；UTF-8 多字节：更小或相等，但临时再加一份）。
- 极端：240MB chunks + 240MB body + 240MB decoded str ≈ 720MB 瞬时占用。

**严重度**：Low。原始 `httpx` 默认 `response.aread()` 也会构造一份完整 bytes（约 1 倍内存），所以 250MB cap 后最坏 480~720MB 仅比修复前的不可控 GB 级好一个数量级，目的达到了。但 `MAX_RESPONSE_BYTES = 250MB` 这个上限规划时**没有考虑** bytearray→bytes 复制 + decode 复制的乘数。若想精确控制内存上限到例如 1.5×N，应该在 chunk cap 触发后立刻 `del chunks` 或将判定阈值设为 `MAX_RESPONSE_BYTES / 2`（如 125MB）以留 headroom。

**影响范围**：极少触发 —— 正常 TShock 响应（`/tokentest`、`/nextbot/blacklist`、`/v3/server/rawcmd`）几乎都在 KB 级，地图 PNG 通常 < 10MB，世界文件 base64 通常 < 100MB。仅当 TShock 后端有 bug / 被攻陷 / 调试模式开 dump 全图时才会接近 250MB。

**修复建议（可选，非强制）**：
- 选项 A：把 `MAX_RESPONSE_BYTES` 降到 128MB（保留 base64 25% overhead 后约 100MB 实际数据，仍覆盖 `large_image.MAX_BASE64_BYTES = 200MB` 的下游消费 —— wait，不行，200MB 是 base64 编码后大小，对应 ~150MB 原始字节。所以 250MB 边界确实是匹配 base64 的）。
- 选项 B：保留 250MB，但在文档化注释里说明峰值内存可达 2-3×。本审计倾向 B。
- 选项 C：把 `body = bytes(chunks); del chunks` 改为 `del chunks` 后才 `body = ...` —— 不可行，因为 `body = bytes(chunks)` 之前 chunks 必须还活着。如果改用 `body = bytes(chunks); chunks.clear()` 也救不了，chunks 已经被复制。最干净的做法是流式 `bytearray` 直接传给 `json.loads(memoryview(chunks).tobytes()...)`，但 `json.loads(bytes | bytearray)` 在 Python 3.10+ 已经支持 bytearray，可以直接 `payload = json.loads(chunks)` 跳过 `bytes(chunks)`，省一半内存。**这是非常具体的小改进，但严重度仍是 Low。**

---

### A-2. I-1.1 httpx.URL.build —— PASS

**修复位置**：`tshock_api.py:132-142`

**复审结论**：通过。`httpx.URL(scheme=, host=, port=, path=)` 让 httpx 自己做 IDN / IPv6 / percent-encoding 规范化。

**输入安全性验证**：
- `server.ip` 上游已经 `server_validation._normalize_host()` 校验（非空 / 长度 ≤ 128 / 禁止 `\n`/`\r`）—— defense-in-depth 链完整。
- `int(server.restapi_port)` 若 ORM 字段是 str 且能 parse，正常通过；若空串或非数字，抛 `ValueError`，被 `except (httpx.InvalidURL, ValueError, TypeError)` 捕获 → `kind="invalid_url"`。
- 中文 / IDN 域名：httpx 在 host 含非 ASCII 时调用 `idna` 库做 punycode，失败抛 `httpx.InvalidURL`，已被 catch。

**异常 catch 完整性**：
- `httpx.InvalidURL`：host 字符非法 / port 越界 / scheme 错。
- `ValueError`：`int(server.restapi_port)` 解析失败、`httpx.URL(port=)` 收到负数。
- `TypeError`：理论上 host=None 等场景；`str(server.ip).strip()` 已经避免，但保留作 defense-in-depth。

PASS。

---

### A-3. I-1.3 模块级 shared client —— PASS（带 2 个 Low 关注点 A-3.1, A-3.2）

**修复位置**：
- `tshock_api.py:75-99`（`_shared_client` / `_get_shared_client` / `close_shared_client`）
- `bot.py:168-172`（`@driver.on_shutdown` 接线，已验证）

**复审结论**：核心功能正常，但有 2 个生命周期边角。

**Double-Checked Locking 验证**（`tshock_api.py:79-91`）：
```python
async def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is not None:
        return _shared_client                      # 快路径
    async with _shared_client_lock:
        if _shared_client is None:                  # 慢路径再校验
            _shared_client = httpx.AsyncClient(...)
    return _shared_client
```

- 第一次 `is not None` check 是同步操作。asyncio 单线程模型下，任何 `await` 之间不会切换协程，所以多协程同时执行到 fast-path 时不会 race。
- 即使第一次有 race（两个协程都看到 None），慢路径有锁 + 再 check，保证只有一个真正 init。
- **PASS 对于 asyncio 单线程**。

#### A-3.1 Low：FastAPI threadpool 同步 endpoint 调用可能并发访问 `_shared_client`

**触发条件**：
- nextbot 通过 nonebot + FastAPI 提供 webui（`server/routes/webui_servers.py` 等）。
- FastAPI 同步 endpoint（`def`，不是 `async def`）会被放进 anyio threadpool 执行。**但** —— 关键：FastAPI 的同步 endpoint 不能直接调用 `async def request_server_api`，必须 `asyncio.run_coroutine_threadsafe(...)` 或者 wrap。
- grep 确认：所有 `request_server_api` 调用点都在 `async def` 中（`server/routes/webui_servers.py:251` `async def test_server_connection`，等等）。所以**实际上不会**从 threadpool 触发并发访问 `_shared_client`。

**严重度**：Low（理论存在，实际不触发）。

**建议**：保留现状。若未来引入 sync endpoint 调 `request_server_api`，需要把 fast-path 也用锁保护或改 `threading.Lock`。**当前 PASS**。

#### A-3.2 Low：shutdown 期间 in-flight request 与 `close_shared_client` 的竞争

**位置**：`tshock_api.py:94-99` + `bot.py:168-172`

```python
async def close_shared_client() -> None:
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()    # 此时仍有协程在 client.stream(...)
        _shared_client = None
```

**触发条件**：
- nonebot 触发 shutdown hook 时，可能仍有协程在 `request_server_api` 中（例如 `广播` 命令 fan-out 中，慢服务器尚未返回）。
- `_shared_client.aclose()` 会 close 所有正在使用的连接，正在 stream 的协程会抛 `httpx.RequestError` 子类（具体是 `httpx.ReadError` / `httpx.RemoteProtocolError`），被现有 catch 转成 `unknown` 或 `protocol` 类。
- shutdown 后 `_shared_client = None`，**之后**若仍有协程到达 `_get_shared_client()` 会重新 init 一个新 client —— 但 nonebot shutdown 流程通常会取消所有未完成的 task，所以此路径理论上不会触发。
- 若触发，新 init 的 client 在进程退出时不会被 aclose（finalizer 兜底，但不 graceful），产生 warning（`ResourceWarning: unclosed`）。

**严重度**：Low。

**修复建议（可选）**：让 `close_shared_client` 在置 None 后 set 一个 `_shutdown = True` 标志，`_get_shared_client` 检查到 `_shutdown` 直接抛 `TShockRequestError("client closed", kind="unknown")`。但这会让 shutdown 期间的命令报错更明显（也算改进）。当前行为可接受，**PASS**。

---

### A-4. I-1.4 TShockRequestError kind —— PASS

**修复位置**：`tshock_api.py:21-36`

**复审结论**：
- 6 个 `kind` 字面量：`timeout / unreachable / invalid_url / protocol / oversize / unknown`，与 raise 位置一一对应（grep 验证）：
  - `kind="invalid_url"` → `tshock_api.py:141` (URL build) + `tshock_api.py:170` (httpx.InvalidURL)
  - `kind="oversize"` → `tshock_api.py:158` (chunk cap)
  - `kind="timeout"` → `tshock_api.py:166`
  - `kind="unreachable"` → `tshock_api.py:168`
  - `kind="protocol"` → `tshock_api.py:172`
  - `kind="unknown"` → `tshock_api.py:174` (RequestError 兜底) + 默认值
- 老 caller 全部 `except TShockRequestError:` 不读 `kind` 字段，向后兼容（grep 18 处使用全部确认）：`ban_core.py` / `user_manager.py` / `warehouse.py` / `security.py` / `lottery.py` / `server_manager.py` / `server_send.py` / `webui_servers.py` / `webui_users.py`。
- 没有任何 caller 使用 `.kind`（grep 已确认）。`kind` 字段是预留给未来使用。

**异常树边界**：
- `httpx.RequestError` 是所有 httpx 网络异常的 root（不含 `httpx.HTTPStatusError`，后者要 `response.raise_for_status()` 才抛，我们不调）。
- 唯一可能漏 catch：`httpx.HTTPError`（`RequestError` 的父类，但 httpx 源码里几乎不直接抛 `HTTPError` 实例）+ 普通 `Exception`（如内存不足时的 `MemoryError`）。但这些都不属于"协议级错误"，让它们冒泡到 caller 是合理的。

**PASS**。

---

### A-5. I-1.5 非 JSON 响应日志 —— PASS

**修复位置**：`tshock_api.py:177-188`

**复审结论**：
- catch `(ValueError, UnicodeDecodeError)` 覆盖 `json.JSONDecodeError`（是 `ValueError` 子类）+ `body.decode("utf-8")` 失败。
- 日志格式包含 `server_id` / `server.ip` / `content_length` / `status` —— 排障所需字段齐全。
- 顶层非 dict（如 JSON array `[...]`）兜底为空 dict，避免下游 `.get` 报错。

**轻微观察**：`server.ip` 直接 f-string，若 ip 含 `\n` 会污染日志单行（但 `server_validation._normalize_host` 已拒绝 `\n`/`\r`，链路安全）。PASS。

---

### A-6. I-2.1 release_server_semaphores helper —— PASS（接线属于 pending Low，符合 Round 7 承诺）

**修复位置**：`large_image.py:42-60`

**复审结论**：
- helper 实现正确（`pool.pop(server_id, None)`，dict.pop 默认不抛 KeyError）。
- docstring 明确说明"caller 端未接线，由 webui 同步审计任务负责接线"。
- **grep 全仓**：`release_server_semaphores` 仅在 `large_image.py` 内部 docstring 示例出现，**零外部 caller**。
- 符合 Round 7 承诺：helper 提供 + caller 端推迟接线。

**剩余 Low pending（已知，不报）**：
- `nextbot/plugins/player_query.py` 维护 `_my_map_semaphores` / `_user_map_semaphores` / `_explored_map_semaphores` / `_inventory_semaphores` / `_progress_semaphores`（5 个 dict）。
- `nextbot/plugins/server_tools.py` 维护 `_map_semaphores` / `_download_semaphores`（2 个 dict）。
- `nextbot/server_broadcast.py` 维护 `_broadcast_semaphores`（1 个 dict）。
- 合计 8 个 dict，删除 server 时全部应该 `release_server_semaphores(...)` 但目前都没有。
- `server/routes/webui_servers.py:206 session.delete(server)` 后无 cleanup。
- `nextbot/plugins/server_manager.py:195 session.delete(server)` 后无 cleanup。
- 单个 Semaphore 内存 ≈ 数百字节，假设运行 1 年频繁增删 100 个 server，总泄漏 < 1MB，**不影响 OOM 风险**，仅 hygiene。

**PASS（helper 部分）**。caller 接线作为 R8 新发现 B-1 单独报告。

---

### A-7. I-3.1 server_broadcast._wrap try 块 —— PASS

**修复位置**：`server_broadcast.py:51-69`

**复审结论**：
```python
async def _wrap(srv: Server) -> BroadcastOutcome[R]:
    try:
        sem = semaphore_for(_broadcast_semaphores, srv.id, max_concurrent=max_concurrent_per_server)
        async with sem:
            return await fn(srv)
    except Exception as exc:
        logger.warning(...)
        return BroadcastOutcome(server=srv, ok=False, detail=..., payload=None)
```

- `semaphore_for` + `async with sem` 都在 try 内 —— 即使未来 `dict.get` / `Semaphore.acquire` 因奇怪原因抛错，也不会逃出。
- 兜底 `except Exception` 转 BroadcastOutcome，配合 `gather(return_exceptions=False)` 仍然成立：任何 task 都不会真正抛错，gather 不会 cancel 兄弟 task。
- `logger.warning` 把异常 `repr` 落盘，排障可用。

**轻微观察**（不算 issue）：`detail=str(exc) or "异常"` 在 `str(exc) == ""` 时回退到"异常"，但仍会被 `aggregate` 统计为失败，对外仅显示"异常"二字。可接受。

**PASS**。

---

## Part B: 全量再扫新发现

### B-1. Low：`release_server_semaphores` 接线缺口（Round 7 承诺保留，按 R8 显式列出）

**位置**：
- `webui` 路由：`server/routes/webui_servers.py:206 session.delete(server)`
- bot：`nextbot/plugins/server_manager.py:195 session.delete(server)`

**问题描述**：Round 7 提供了 `release_server_semaphores(pool, server_id)` helper，但两个删除 server 的入口都没有调用。具体应该清理 8 个池：
- `nextbot/plugins/server_broadcast._broadcast_semaphores`
- `nextbot/plugins/server_tools._map_semaphores` / `_download_semaphores`
- `nextbot/plugins/player_query._my_map_semaphores` / `_user_map_semaphores` / `_explored_map_semaphores` / `_inventory_semaphores` / `_progress_semaphores`

**触发概率**：常规（每删一个 server）。

**影响**：每删除一个 server 永久泄漏 ≤ 8 个 Semaphore 对象（< 1KB / server）。属于"只增不减"的资源，非 OOM 阻塞，但破坏 hygiene。

**严重度**：Low。

**修复建议**：在两个 delete 入口后，集中调用所有 pool 的 cleanup。可在 `large_image.py` 或新模块（如 `server_lifecycle.py`）提供 `release_all_server_semaphores(server_id)`，让 plugins 把自己的 pool 注册过来，避免分散的 cleanup 调用难以维护。

---

### B-2. Low：`MAX_RESPONSE_BYTES` 与 `MAX_BASE64_BYTES` 关系的 invariant 在代码里没断言

**位置**：`tshock_api.py:18` (`MAX_RESPONSE_BYTES = 250 * 1024 * 1024`) + `large_image.py:17` (`MAX_BASE64_BYTES = 200 * 1024 * 1024`)

**问题描述**：
- 注释声称"250MB 略大于 MAX_BASE64_BYTES=200MB，给 base64 留 25% overhead"。
- 但若后续维护者把 `MAX_BASE64_BYTES` 改到 250MB 而忘了同步调 `MAX_RESPONSE_BYTES`，下游 base64 cap 立刻会被上游 cap 卡住，行为静默错位。
- 当前没有 import-time / startup-time 断言保证 `MAX_RESPONSE_BYTES >= MAX_BASE64_BYTES * 5 // 4`。

**严重度**：Low（属于工程纪律，非运行时 bug）。

**修复建议**：
- 选项 A：在 `tshock_api.py` 顶部 `from nextbot.large_image import MAX_BASE64_BYTES; assert MAX_RESPONSE_BYTES >= MAX_BASE64_BYTES * 5 // 4`，让模块 import 时就报错。
- 选项 B：把这两个常量统一到 `large_image.py`，作为单一真源（`MAX_RESPONSE_BYTES` 改名 `MAX_API_RESPONSE_BYTES`），减少漂移机会。
- 选项 C：不修，依赖人工注释维护（当前状态）。

---

### B-3. Low：`json.loads(body)` 可以直接吃 bytearray，省一份内存

**位置**：`tshock_api.py:161, 178`

**问题描述**：
```python
body = bytes(chunks)                            # 复制一份
payload = json.loads(body.decode("utf-8"))      # 再复制成 str
```

- Python 3.6+ 的 `json.loads` 接受 `bytes | bytearray | str`。
- `json.loads(chunks)`（bytearray 直接传入）省去 `bytes(chunks)` 这一份复制，节省一倍内存。
- 但代码现在还需要 `body.decode("utf-8")` 给非 JSON 兜底日志的 `len(body)` 用 —— 也可以直接 `len(chunks)`。

**严重度**：Low（性能优化，非 bug）。仅在 body 接近 250MB 时有可观差异；KB 级响应无感。

**修复建议（可选）**：
```python
try:
    payload = json.loads(chunks) if chunks else {}
except (ValueError, UnicodeDecodeError):
    logger.warning(
        f"TShock 响应非 JSON：server_id={server.id} "
        f"content_length={len(chunks)} status={status_code}"
    )
    payload = {}
```
保留向后兼容 `UnicodeDecodeError` catch（json.loads 内部 decode 失败抛此异常）。

---

### B-4. Info：`broadcast()` 的 `max_concurrent_per_server=1` 默认对所有 caller 都合适

**位置**：`server_broadcast.py:39-44`

**问题描述**（验证而非缺陷）：
- 当前 5 个 caller 全部用默认 `max_concurrent_per_server=1`：
  - `ban_core.py:216` `await broadcast(servers, _add_one)` ✓
  - `ban_core.py:283` `await broadcast(servers, _remove_one)` ✓
  - `lottery.py:858` `await broadcast(servers, _execute_for_server)` ✓
  - `security.py:70` `await broadcast(servers, _one)` ✓
  - `shop.py:847` `await broadcast(online_servers, _execute_for_server)` ✓
- 默认值 1 与 `ban_core` 的「先 GET /blacklist 再 POST /blacklist/add」模式吻合（同服内两次串行调用之间不会被另一个 fan-out 插队）。
- `lottery._execute_for_server` 内部对单台 server 也是串行多个 prize 执行，max=1 合适。
- `shop._execute_for_server` 类似。
- **PASS**：默认值合适，无需调整。

---

### B-5. Info：`server_validation` 自 Round 7 未改动，复审无新发现

**位置**：`server_validation.py`（全文）

**复审要点**：
- `_NAME_PATTERN = re.compile(r"^[A-Za-z0-9一-鿿 ._-]{1,32}$")` —— 中文 CJK 基本块 `一` 到 `鿿`（U+4E00 ~ U+9FFF）。覆盖常用汉字，但不包含 CJK 扩展 A/B/C（`㐀-䶿` U+3400-U+4DBF 等）。若 server 名含罕见字将拒绝；目前业务无诉求，**不报 issue**。
- `_normalize_port` 处理 `bool` 优先（`isinstance(raw_value, bool)` —— bool 是 int 子类，必须先排），处理顺序正确。
- `_normalize_token` 长度上限 128 与 `_MAX_TOKEN_LENGTH` 一致。
- `_check_no_newline` 只检查 `\n` / `\r`，没检查 ` ` / ` `（Unicode line separators）—— 对 TShock REST host 无意义（host 已经 `httpx.URL` 规范化），但若有日志注入风险需要考虑。属 Info / 不报。

**PASS（未改动，无新 issue）**。

---

### B-6. Info：`screenshot_render` 自 Round 7 未改动，复审无新发现

**位置**：`screenshot_render.py`（全文）

**复审要点**：
- `_render_and_send_inner` 在 `temp_screenshot_path` context manager 内完成全部 IO，cleanup 自动。
- `file_size <= 0` 早返回（R5-3.1）+ `file_size * 4 // 3 > MAX_BASE64_BYTES` 编码前预估 + `len(encoded) > MAX_BASE64_BYTES` 编码后再校验，三道防线齐全。
- `read_bytes` / `b64encode` 用 `try/except OSError` 兜底；`base64.b64encode` 本身不抛 OSError，但 `read_bytes` 会，catch 范围合理。
- **理论缺口**：`base64.b64encode(raw)` 处理 ~150MB 字节时可能 `MemoryError`，未 catch。但前面已经按 `file_size * 4 // 3 > MAX_BASE64_BYTES` 拒绝过，进入此路径时 raw ≤ 150MB，b64encode 后 ≤ 200MB，按 Python 实现 `bytes(...)` 分配应该 OK。**不报 issue**。
- `bot.adapter.get_name() == "OneBot V11"` 字符串比较：若适配器名称变更（如 OneBot V12 升级），fallback 路径会触发但不报错。可接受。

**PASS（未改动，无新 issue）**。

---

## 结论

### Round 7 修复复审

| Round 7 Item | 状态 | 备注 |
|---|---|---|
| H-4 (I-1.2) stream + chunk cap | **PASS** | 附带 Low A-1.1 关注点 |
| I-1.1 httpx.URL.build | **PASS** | catch 完整 |
| I-1.3 shared client + shutdown hook | **PASS** | 附带 Low A-3.1 / A-3.2 关注点 |
| I-1.4 TShockErrorKind | **PASS** | 6 kind 全覆盖，向后兼容 |
| I-1.5 非 JSON 响应日志 | **PASS** | 字段齐全 |
| I-2.1 release_server_semaphores helper | **PASS** | 接线缺失符合 Round 7 承诺 |
| I-3.1 server_broadcast 异常包围 | **PASS** | gather 不再被打断 |

**0 NEW-ISSUE（针对 Round 7 修复本身的回归）**。

### 全量再扫新发现

| Item | 严重度 | 位置 | 概要 |
|---|---|---|---|
| B-1 | **Low** | `server/routes/webui_servers.py:206` + `nextbot/plugins/server_manager.py:195` | `release_server_semaphores` 接线缺失（Round 7 承诺保留） |
| B-2 | **Low** | `tshock_api.py:18` + `large_image.py:17` | `MAX_RESPONSE_BYTES >= MAX_BASE64_BYTES * 5/4` invariant 无 assert |
| B-3 | **Low** | `tshock_api.py:161, 178` | `json.loads(bytearray)` 可省一份内存复制 |
| A-1.1 | **Low** | `tshock_api.py:161` | `bytes(chunks)` 在 240MB 时短暂双倍内存（与 B-3 相关） |
| A-3.1 | **Low** | `tshock_api.py:81` | DCL fast-path 理论上仅 asyncio 安全（threadpool 场景未来风险） |
| A-3.2 | **Low** | `tshock_api.py:94-99` | shutdown 期间 in-flight + 重 init 边角 |
| B-4 | **Info** | 5 处 broadcast caller | `max_concurrent_per_server=1` 默认对所有 caller 合适 |
| B-5 | **Info** | `server_validation.py` | Round 7 未改动，无新 issue |
| B-6 | **Info** | `screenshot_render.py` | Round 7 未改动，无新 issue |

**0 Critical / 0 High / 0 Medium / 6 Low / 3 Info**。

### 总评

Round 7 IO 桶的 7 条修复全部 PASS。新一轮全量再扫**未发现** Medium 或以上级别的新问题。剩余 6 个 Low 都属于工程纪律 / 内存峰值优化 / 接线 hygiene，**不影响业务正确性，不阻塞 Round 8 闭环**。

推荐处置：
- B-1（接线）：可在 R8 实施阶段顺手补，单点改动 < 20 行。
- B-2 + B-3 + A-1.1：合并为一个小补丁（让 `json.loads` 直接吃 bytearray + 加 assert + 文档化 MAX_RESPONSE_BYTES 内存峰值），改动 < 10 行。
- A-3.1 + A-3.2：**不修**，保留为已知边角；若未来引入 sync endpoint 调 tshock_api，再处理。
