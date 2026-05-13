# R3 Backend + 跨模块 桶审计

- **Query**: WebUI 仪表盘 Round 3 复审 + 全量再扫（R2 commit c1a96ca）
- **Scope**: internal（5 个 backend + cross-module 文件 + 前端消费端 users.js / api.js / servers.js）
- **Date**: 2026-05-14

---

## Part A: R2 修复复审（共 5 项：R2-T-1/T-2/T-3 + 保留项 R1 D3 / A1）

### R2-T-1 PASS — `webui_users.py:199-242` sync-whitelist broadcast 改造

**实际行号**：`server/routes/webui_users.py:199-242`（`_sync_user_whitelist`）

**复审 checklist 全部满足**：

1. **outcome shape 对齐前端**：
   - 后端输出字段：`server_id` (int)、`server_name` (str)、`success` (bool)、`reason` (str)（`webui_users.py:236-241`）
   - 前端消费：`users.js:853-862` 读取 `item?.server_id`, `item?.server_name`, `Boolean(item?.success)`, `String(item?.reason || "未知错误")` —— **字段名 / 类型 / 语义完全一致**
   - `success: true` + `reason=""` 在前端按 "同步成功" 渲染，与 R1 之前的串行版字段一致。

2. **import 循环**：
   - `webui_users.py:15` `from nextbot.server_broadcast import BroadcastOutcome, broadcast`
   - `nextbot/server_broadcast.py:13-22` 只 import `nextbot.db` / `nextbot.large_image` / `nonebot.log`，**未反向 import `server.routes.*`**（grep 验证：`grep -rn "from server\|import server" nextbot/server_broadcast.py` 无任何匹配）。无环。

3. **per-server semaphore=1**：
   - `server_broadcast.py:44` `max_concurrent_per_server: int = 1` 默认值
   - `_sync_user_whitelist` line 233 不传 override，走默认 1
   - 与原串行版 "单服内一次一发" 行为一致。

4. **outcome 排序**：
   - `server_broadcast.py:75` `return sorted(results, key=lambda o: o.server.id)` 保证升序
   - 与原 `session.query(Server).order_by(Server.id.asc()).all()` 一致；前端 `users.js:858 lines.push(\`${serverId}.${serverName}：...\`)` 按 outcome 顺序追加，UI 显示稳定。

5. **异常归一化**：
   - `_one` 内 `except TShockRequestError` (line 217) 返回 `BroadcastOutcome(ok=False, detail="无法连接服务器")`，与原 R1 之前文案一致。
   - 非 TShockRequestError 异常被 `server_broadcast.py:64-70` defensive `except Exception` 兜底，detail=`str(exc)`，**潜在问题见 Part B 新发现 R3-NEW-1**。

6. **DB session 生命周期**：
   - `webui_users.py:200-204` `servers = session.query(...).all()` 后立刻 `session.close()`，**broadcast 调用时已无 session 持有**。
   - `_one` 内 `await request_server_api(server, ...)` 期间 server 是 detached SQLA instance，但只读 `server.name` / `server.id`（lazy-load 不触发）。OK。

7. **outcome detail 信息泄漏**：
   - 正常路径 detail 是 `"无法连接服务器"` / `""` / `get_error_reason(response)`（业务文案），不含敏感字段。
   - 异常 fallback path：见 R3-NEW-1。

---

### R2-T-2 PASS（带 1 个 minor 观察） — `webui_users.py:585-642 / 681-737` ban/unban broadcast 改造

**实际行号**：`server/routes/webui_users.py:585-642`（ban）、`webui_users.py:681-737`（unban）

**outcome shape**：
- 后端 `server_results[]` 字段：`server_id` (int)、`server_name` (str)、`success` (bool)、`reason` (str)（line 634-641 / 729-736）
- 前端 `users.js:805-815`（toggleBan 路径）读取 `item.server_id`, `item.server_name`, `item.success`, `item.reason` —— **shape 一致**

**与 R2-T-1 一致的 7 项 checklist 全部通过**（import 循环 / semaphore / 排序 / 异常 / DB session / 字段语义）。

**Minor 观察 1（不构成 finding，只记录）**：
- ban 路径 `user_name` / `user_qq` 在 line 575-576 定义，紧跟 `session.commit()`（line 573）之后。如果 commit 抛错（如 SQLite IO 错误），`except` 在 line 578 接住，`return api_error(500)`，**不会进入 broadcast 阶段**。正常路径 `user_name` 必被赋值。`_ban_one` 闭包捕获 free var `user_name` / `reason` 一次性赋值后不再变更，asyncio.gather 并发安全。
- unban 路径同理。

**Minor 观察 2（已知 trade-off，task 说明里点过）**：
- 前端 abort（15s timeout）后，后端 `asyncio.gather` **不会自动 cancel**：broadcast 中所有 in-flight RPC 继续跑完，最后 outcomes 写入 response 但前端连接已断、写入失败仅产生一条 `ConnectionResetError` 日志。
- 这不会引起重复 DB 写入（DB 更新已在 line 570-573 commit 完成，broadcast 阶段不写 DB）。
- 不会引起锁竞争（broadcast 调用 TShock RPC 不持任何本地锁，per-server semaphore 是 in-process 软限流，不跨请求阻塞）。
- 等价于 "fire-and-forget 后台任务"，可接受。

---

### R2-T-3 PASS — `webui_servers.py:435` verify-nextbot timeout 10s

**实际行号**：`server/routes/webui_servers.py:432-436`

```python
# R2-T-3：后端 timeout 降到 10s，给前端 15s cap 留 5s 缓冲，避免 race。
response = await request_server_api(
    server, "/nextbot/config/verify-nextbot", timeout=10.0
)
```

**核对**：
1. **timeout 已改**：line 435 显示 `timeout=10.0`，落地。
2. **10s 是否充足**：在 `request_server_api` (tshock_api.py:131-136) 内被映射为 `httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)`。TShock `/nextbot/config/verify-nextbot` 端正常 < 1s，启动期 5-8s 也覆盖。10s 充足。
3. **前端无 override**：`servers.js:987-995` 调 `api.apiRequest(...)` 不传 `timeoutMs`，走 `api.js:108` 默认 `REQUEST_TIMEOUT_MS = 15000`。后端 10s + 前端 15s = **5s 缓冲**，与 R2 commit 描述一致。

---

### R1 D3（保留）PASS — `webui_dashboard.py:9, 15, 19-23` `_client_ip` 复用 + 日志结构化

**实际行号**：`server/routes/webui_dashboard.py:9, 15, 19-23`

复审：
- line 9 `from server.routes.webui import _client_ip`：**只有一条单向 import**（dashboard → webui），反向（webui → webui_dashboard）grep 无匹配 ✅
- line 15 `async def webui_dashboard_api(request: Request)` 接 `request` 参数 ✅
- line 19-22 错误路径打结构化日志：`reason={exc} client_ip={client_ip} user_agent={user_agent!r}` ✅
- 已知保留项 C3（line 27 `message="内部错误"`）：仍是泛化文案，由用户决策保留，**未引入新问题**。

---

### R1 A1（保留）PASS — `console_page.py:47-59` 模板信任假设 docstring

**实际行号**：`server/pages/console_page.py:47-59`

复审：
- docstring 明确「`page_title` / `header_title` 必须可信字面量」+「内容模板禁止 `__XXX__` 占位符接收外部数据」
- caller `render_console_page` (line 118-131) 等 9 个 caller 全部传入硬编码 str 字面量（`"NextBot WebUI - 仪表盘"` 等），**无 caller 从 DB / query / payload 读字符串**，docstring 信任假设成立。
- line 80-103 实际 render 时：`page_title` / `header_title` 仍 `html.escape(quote=True)` 双重防御，与 docstring 描述一致。
- 已知 trade-off：`_load_template(content_template)` 加载后直接 replace `__MAIN_CONTENT__`，未 escape；docstring 已明示约束，未来 caller 误传 placeholder 时一眼可查。

---

## Part B: 全量再扫新发现

### R3-NEW-1（P3 / LOW，信息泄漏风险，**defense-in-depth**）

**文件 + 行号**：`nextbot/server_broadcast.py:64-70`（fallback `except Exception` 路径）+ `server/routes/webui_users.py:639, 734`（reason 透传到 response.body）

**修复前**：

```python
# server_broadcast.py:52-70
async def _wrap(srv: Server) -> BroadcastOutcome[R]:
    try:
        sem = semaphore_for(_broadcast_semaphores, srv.id, ...)
        async with sem:
            return await fn(srv)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"广播任务异常：server_id={srv.id} reason={exc!r}")
        return BroadcastOutcome(
            server=srv, ok=False, detail=str(exc) or "异常", payload=None
        )
```

**触发链**：

1. `_ban_one` 在 `await request_server_api(...)` 之外的代码路径抛非 `TShockRequestError` 异常（例如 `AttributeError`、`TypeError`、`MemoryError` ——
   实际可能性：`response.payload.get("entries", [])` 处理 server 返回 dict 但 entries 类型非预期（已 `isinstance` 保护，但 `e.get("username", "")` 若 `e` 是 dict 但 username 是嵌套对象，触发未捕获 path 概率极低）。
2. `_wrap` 捕获，`str(exc)` 进入 `BroadcastOutcome.detail`
3. webui_users.py line 639 `"reason": o.detail` 入 `server_results[]`
4. 前端 `users.js:813 lines.push(... + (item.reason || "未知错误"))` 渲染到 UI

**潜在泄漏内容**：

- `str(AttributeError)` 通常是 `'NoneType' object has no attribute 'X'`（无敏感信息）✅
- `str(httpx.TimeoutException)` / `str(httpx.ConnectError)` 等已经被 tshock_api line 174-182 包成 `TShockRequestError(str(exc))`，**`str(httpx.*)` 通常不含 URL**（httpx 默认 `__str__` 是 message-only，**URL 仅在 `repr()` 中**）。但实际未抛 `TShockRequestError` 而是其它异常时，`exc!r` 会写日志。
- **关键风险**：如果未来某段代码（非现有路径）`raise ValueError(f"server {server.token} bad ...")`，`str(exc)` 会把 token 透到 client。当前**未发现**这种代码，但 detail=`str(exc)` 默认行为是 fail-open。

**修复后建议**（不必本轮修，仅记录）：

```python
except Exception as exc:  # noqa: BLE001
    logger.warning(f"广播任务异常：server_id={srv.id} reason={exc!r}")
    return BroadcastOutcome(
        server=srv, ok=False, detail="任务异常", payload=None  # 不透传 exc 内容
    )
```

**触发概率**：极低（< 1%）。当前所有 `fn` 实现都已 try/except `TShockRequestError`，剩余可能抛错路径都是逻辑错误，不会包含 server.token。

**严重度**：**P3 / LOW**。**defense-in-depth**，建议下轮修，不阻塞本轮。

---

### R3-NEW-2（P3 / LOW，日志注入面观察，**不修**）

**文件 + 行号**：
- `webui_users.py:577` ban success log `reason={reason}`（用户输入直接入日志）
- `webui_users.py:644` ban broadcast 完成日志 `name={user_name}`（DB 字段直入日志）
- `webui_users.py:739` unban 同上

**说明**：

- `reason` 来自前端 `data.get("reason")` (line 544)，未做换行 / control char 过滤
- 这些日志走 nonebot.logger，最终 stderr / 文件，**不进入任何前端**，无 XSS / SSRF 面
- 日志注入风险：攻击者用包含 `\n[INFO]` 的 reason 伪造日志行——但需要先登录，攻击面已被 auth 闭环，**M-4 / R7 已确认接受**
- 不动

**严重度**：**P3 / observation only**

---

### R3-NEW-3（已知项，重申不修）— `_client_ip` 信任 XFF

**文件 + 行号**：`server/routes/webui.py:151-159`（共享 helper）

**说明**：
- `webui_dashboard.py:19` 调用此 helper，与 webui.py 共享同一信任假设
- 当前部署文档要求 reverse-proxy 剥离 / 覆盖 XFF；裸跑无 proxy 部署时 XFF 可伪造
- 已在 archived audits（R7+ / login-audit）登记为「已知 trade-off，由部署文档约束」
- 本轮**不重复挖**，仅确认 R2 commit 未引入新 caller 滥用

---

### R3-NEW-4（已扫描，无新增）— dashboard.py 全文 + console_page.py 全文

**扫描结论**：
- `webui_dashboard.py`（31 行）只有一个 endpoint，调用 `get_dashboard_metrics()`（pure read），无副作用、无外部 RPC、无 user input；R1 D3 修复后无新增问题
- `console_page.py`（260 行）9 个 `render_*_page()` 全部硬编码字符串字面量，R1 A1 docstring 信任假设守住

---

### R3-NEW-5（已扫描，无新增）— SQLite 并发写锁

**说明**：
- ban / unban 在 commit 之后才进入 broadcast；broadcast 阶段所有 RPC 走 TShock，**不再写本地 DB**
- 多用户同时 sync-whitelist 完全只读（line 200-204 close session 后才 broadcast）
- **无 SQLite 写锁竞争**风险 ✅

---

### R3-NEW-6（已扫描，无新增）— `request_server_api` 错误传播

**说明**：
- `tshock_api.py:174-182` 全部 5 个 `raise TShockRequestError(str(exc), kind=...) from exc`：`str(httpx.*)` 已实测**不含 token / URL**（httpx 异常 `__str__` 是 message-only）
- 调用方 `_one` / `_ban_one` / `_unban_one` 全部 `except TShockRequestError` 转 "无法连接服务器" 文案，**未透传原始 message**
- 当前实现安全 ✅

---

## 结论

### R2 修复复审：5/5 PASS

| ID | 文件 / 位置 | 状态 |
|---|---|---|
| R2-T-1 | `webui_users.py:199-242` sync-whitelist | PASS |
| R2-T-2-A | `webui_users.py:585-642` ban broadcast | PASS（+2 minor observation） |
| R2-T-2-B | `webui_users.py:681-737` unban broadcast | PASS |
| R2-T-3 | `webui_servers.py:435` verify-nextbot timeout 10s | PASS |
| R1 D3（保留） | `webui_dashboard.py:9, 15, 19-23` | PASS |
| R1 A1（保留） | `console_page.py:47-59` | PASS |

### 全量再扫新发现

| ID | 严重度 | 文件 | 修复建议 |
|---|---|---|---|
| R3-NEW-1 | P3 / LOW | `server_broadcast.py:69` fallback `detail=str(exc)` | 改 "任务异常" 字面量；defense-in-depth；下轮修 |
| R3-NEW-2 | P3 / observation | `webui_users.py:577/644/739` log injection | 接受（攻击面已被 auth 闭环） |
| R3-NEW-3 | 已知 trade-off | `webui.py:151-159` `_client_ip` XFF | 部署文档约束；不修 |
| R3-NEW-4 | 无新增 | `webui_dashboard.py` 全文 | — |
| R3-NEW-5 | 无新增 | SQLite 写锁 | broadcast 阶段不写 DB |
| R3-NEW-6 | 无新增 | `request_server_api` 错误传播 | 现有实现安全 |

### 总体判定

**R2 commit c1a96ca 后端 + 跨模块修复全部 PASS**，无 P1 / P2 阻塞。

**R3-NEW-1 是唯一一个值得未来工序处理的 finding**（defense-in-depth），当前不影响合并。

排除项 C3 / M-1 / M-2 按 task 要求未审计。
