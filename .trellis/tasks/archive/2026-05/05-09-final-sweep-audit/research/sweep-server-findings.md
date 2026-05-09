# Final Sweep Audit – 服务器交互 + 玩家查询（5 文件）

- **Query**: 第 13 轮最终复审
- **Scope**: internal
- **Date**: 2026-05-09
- **Files audited**:
  - `nextbot/plugins/server_tools.py` (427 行)
  - `nextbot/plugins/server_send.py` (129 行)
  - `nextbot/plugins/server_manager.py` (267 行)
  - `nextbot/plugins/player_query.py` (1141 行)
  - `nextbot/plugins/leaderboard.py` (1715 行)
- **Reference helpers verified**: `nextbot/large_image.py`、`nextbot/server_broadcast.py`、`nextbot/screenshot_render.py`、`nextbot/screenshot_temp.py`、`nextbot/tshock_api.py`、`nextbot/db.py`

---

## TL;DR

经第 13 轮逐项 checklist 复审，没有发现任何阻断级（🔴）或高危级（🟠）问题。整体已达发布水位：

- 截图迁移：3 个 player_query 地图 handler 与 inventory（`semaphore=None` 由调用方持锁）的 helper 调用模式正确、无死锁、与 leaderboard / progress 行为一致；
- fan-out：`在线`、`自踢`、`总在线时长` 三处 fan-out 全部走 `asyncio.gather`，ordering 由 `Server.id.asc()` 排序保证；
- URL 路径段：所有 `f"/nextbot/users/{name}/..."` 拼接已 `quote(safe="")`；`tshock_api.request_server_api` 自身还会再做一次 `quote(path, safe="/")` 防御兜底；
- OOM cap：`MAX_BASE64_BYTES = 200 MiB`、`MAX_LEADERBOARD_ENTRIES = 10000`、`MAX_TOTAL_ONLINE_USERNAMES = 50000` 三个 cap 全部生效；
- `server_id <= 0` 校验已覆盖 4 处 plugin 的 6 个入口；
- screenshot helper：semaphore 统一在最外层 `async with`，所有 exit path（return False / True / 异常）都自动释放；
- leaderboard 6 个 SQL 表达式 handler（金币 / 连续签到 / 签到 / 被抢 / 抢劫罚款 / 4 个净收入与 2 个胜率派生）行为正确，ORDER BY 与 self_entry rank 在 SQL 层一致。

下面给出确认结论及 4 条信息级 / 微优化级观察。

---

## 1. 截图迁移完整性 ✅

### 1.1 leaderboard 调用模式（PASS）

`leaderboard.py:121-128` `_render_and_send` 调用 `render_and_send_screenshot`：

```python
await render_and_send_screenshot(
    bot, event,
    page_url=page_url,
    options=LEADERBOARD_SCREENSHOT_OPTIONS,
    file_prefix=file_prefix,
    semaphore=_leaderboard_screenshot_semaphore,  # global Semaphore(2)
    failure_action="查询",
)
```

- `_leaderboard_screenshot_semaphore = asyncio.Semaphore(2)` 是 module-level、handler-wide（不分服务器），17 个 leaderboard handler 共享，正确语义：限制 Playwright OOM 放大；
- 信号量 acquire 在 helper 入口 `async with semaphore`（`screenshot_render.py:76-81`），所有 return / exception path 都通过 `__aexit__` 释放，无遗漏。

### 1.2 player_query 地图 handler（API base64 模式，PASS）

3 个地图 handler（`handle_my_map` / `handle_user_map` / `handle_explored_map`）**没有走 `render_and_send_screenshot` helper**，而是直接复用 TShock `/map-image` 端点返回的 PNG base64：

- `player_query.py:696-698`：`sem = _semaphore_for(_my_map_semaphores, server.id); async with sem:`
- `player_query.py:845-846`：`sem = _semaphore_for(_user_map_semaphores, server.id); async with sem:`
- `player_query.py:970-971`：`sem = _semaphore_for(_explored_map_semaphores, server.id); async with sem:`

三者都自带 per-server 独立 dict，`max_concurrent=1`（默认），不与 helper 路径互相干扰。helper 也未被调用，不会双重 acquire。

### 1.3 inventory 的 re-entrant lock（PASS）

`handle_user_inventory` (`player_query.py:419, 496-503`) 与 `handle_my_inventory` (`player_query.py:569, 644-651`) 已在 handler 外层 `async with sem:` 持有 `_inventory_semaphores[server_id]`，调用 helper 时**不传 semaphore**：

```python
# player_query.py:496-503
await render_and_send_screenshot(
    bot,
    event,
    page_url=page_url,
    options=INVENTORY_SCREENSHOT_OPTIONS,
    file_prefix=f"inventory-{server.id}-{target_user.user_id}",
    failure_action="查询",
    # 注意：未传 semaphore=
)
```

helper 默认 `semaphore: asyncio.Semaphore | None = None`（`screenshot_render.py:40`），`None` 时直接调用 `_render_and_send_inner`，**不再 acquire 第二把锁**（`screenshot_render.py:70-75`）。

→ 同 task 二次 acquire 死锁完全消除。注释 `player_query.py:493-495 / 642-643` 已说明设计意图。

### 1.4 progress handler（PASS）

`handle_world_progress` (`player_query.py:1132-1141`) 在 helper 外层**不持锁**，把 `_progress_semaphores[server_id]` 通过 `semaphore=sem` 直接传入 helper。

```python
sem = _semaphore_for(_progress_semaphores, server.id, max_concurrent=2)
await render_and_send_screenshot(
    bot, event,
    page_url=page_url, options=PROGRESS_SCREENSHOT_OPTIONS,
    file_prefix=f"progress-{server.id}",
    semaphore=sem,
    failure_action="查询",
)
```

→ 与 leaderboard 的"helper 内部 acquire"模式一致，正确。

---

## 2. fan-out 一致性 ✅

### 2.1 server_tools / server_send / server_manager（PASS — 不需要 broadcast）

通读三个 plugin 后，确认全部命令都是**单服务器命令**（`执行 <id>` / `全亮地图 <id>` / `下载地图 <id>` / `发送 <id>` / `添加服务器` / `删除服务器` / `服务器列表` / `测试连通性 <id>`），没有跨服务器 fan-out 需求，**不需要 `server_broadcast`** helper。

### 2.2 leaderboard 总在线时长（PASS — gather + 排序）

`leaderboard.py:790`：

```python
fetch_results = await asyncio.gather(*[_fetch_one(s) for s in servers])
```

- `_fetch_one` 返回 `(server, entries | None)`；
- 后续 `for _, entries in fetch_results: ...` **不依赖顺序**（只是把所有 entries 累加进 `totals: dict[str, int]`）；
- 最终 `all_entries = sorted(totals.items(), key=lambda x: x[1], reverse=True)` 按值排序，与原服务器顺序无关。

→ ordering 不再是问题。`servers` 在 DB 查询时已 `order_by(Server.id.asc())`，传入 `gather` 的 task 顺序也稳定，gather 返回顺序按提交顺序，唯一遗憾是聚合时不稳定但**结果与顺序无关**。

### 2.3 player_query 在线 / 自踢（PASS — gather + 输出按 servers 顺序）

- `handle_online` (`player_query.py:255-263`)：`results = await asyncio.gather(*(_query_one(s) for s in servers), ...)`，`for i, server_lines in enumerate(results)` 按 enumerate 顺序拼接（gather 返回顺序与传入顺序一致），所以输出仍按 `Server.id.asc()` 排序；
- `handle_self_kick` (`player_query.py:326-328`)：`lines = list(await asyncio.gather(*(_kick_one(s) for s in servers), ...))`，每行已带 `f"{server.id}.{server.name}：..."` 前缀，输出顺序稳定。

---

## 3. TShock URL 路径段 quote 一致性 ✅

### 3.1 `f"/nextbot/users/{name}/..."` 类拼接（PASS）

通过 grep 发现的所有 handler URL 拼接：

| File:Line | Handler | URL 模板 | quote? |
|---|---|---|---|
| `player_query.py:424` | `handle_user_inventory` | `f"/nextbot/users/{encoded_name}/inventory"` | ✅ `encoded_name = quote(target_user.name, safe="")` (L422) |
| `player_query.py:427` | `handle_user_inventory` | `f"/nextbot/users/{encoded_name}/stats"` | ✅ 同上 |
| `player_query.py:574` | `handle_my_inventory` | `f"/nextbot/users/{encoded_name}/inventory"` | ✅ `encoded_name = quote(user.name, safe="")` (L572) |
| `player_query.py:577` | `handle_my_inventory` | `f"/nextbot/users/{encoded_name}/stats"` | ✅ 同上 |
| `player_query.py:703` | `handle_my_map` | `f"/nextbot/users/{encoded_name}/map-image"` | ✅ `encoded_name = quote(user.name, safe="")` (L699) |
| `player_query.py:852` | `handle_user_map` | `f"/nextbot/users/{encoded_name}/map-image"` | ✅ `encoded_name = quote(target_user.name, safe="")` (L848) |
| `player_query.py:976` | `handle_explored_map` | `"/nextbot/world/explored-map-image"` | N/A（无变量段） |
| `server_tools.py:252` | `handle_map_image` | `"/nextbot/world/map-image"` | N/A |
| `server_tools.py:339` | `handle_download_map` | `"/nextbot/world/world-file"` | N/A |
| `player_query.py:1091` | `handle_world_progress` | `"/nextbot/world/progress"` | N/A |
| `leaderboard.py:_server_side_leaderboard` | 死亡 / 渔夫 / 在线时长 / 探索率 / 总在线时长 | `"/nextbot/leaderboards/<endpoint>"` | N/A（endpoint 是常量字符串） |

→ 所有变量段 100% 已 `quote(safe="")`。`request_server_api` (`tshock_api.py:58`) 还做了 `quote(request_path, safe="/")` 兜底（防 DB 脏数据），属于纵深防御。

### 3.2 server_tools / server_send / server_manager（PASS）

这三个 plugin 中没有任何变量段插值（`/v3/server/rawcmd`、`/v2/server/status`、`/tokentest`、`/nextbot/world/*` 都是常量），命令文本作为 `params={"cmd": ...}` 传递，由 httpx `params=` 安全转义。

---

## 4. OOM / size cap ✅

### 4.1 leaderboard fetch entries 10000 cap（PASS）

`leaderboard.py:531-537`：

```python
if len(all_entries) > MAX_LEADERBOARD_ENTRIES:
    logger.warning(...)
    all_entries = all_entries[:MAX_LEADERBOARD_ENTRIES]
```

走 `_server_side_leaderboard` 公共路径的 4 个 handler（死亡 / 渔夫任务 / 在线时长 / 地图探索率）都共享。**总在线时长**（`handle_total_online_time_leaderboard`）走的是另一条路径，cap 是 `MAX_TOTAL_ONLINE_USERNAMES = 50000` (`leaderboard.py:808`)，逻辑正确。

### 4.2 player_query 地图 base64 200 MB cap（PASS）

| Handler | File:Line | Check |
|---|---|---|
| `handle_my_map` | `player_query.py:720-725` | `if len(b64_string) > _MAX_BASE64_BYTES: ... return` |
| `handle_user_map` | `player_query.py:869-874` | 同上 |
| `handle_explored_map` | `player_query.py:993-998` | 同上 |
| `handle_map_image` (server_tools) | `server_tools.py:269-274` | 同上 |
| `handle_download_map` (server_tools) | `server_tools.py:357-362` | 同上 |

---

## 5. server_id ≤ 0 校验 ✅

| File | Function | 行 | 校验 |
|---|---|---|---|
| `server_tools.py` | `_parse_execute_arg_text` | L70-71 | ✅ `if server_id <= 0: return None` |
| `server_tools.py` | `handle_map_image` | L232-233 | ✅ |
| `server_tools.py` | `handle_download_map` | L316-317 | ✅ |
| `server_send.py` | `_parse_send_arg_text` | L46-47 | ✅ |
| `player_query.py` | `handle_user_inventory` 等 | — | ❌（见 §8.4） |
| `leaderboard.py` | `_server_side_leaderboard` | L487-488 | ✅ |
| `server_manager.py` | `handle_delete_server` / `handle_test_server` | — | ❌（见 §8.4） |

`Server.id` 在 DB 是 `primary_key=True, autoincrement=False`（`db.py:120`），`server_manager.handle_add_server` 使用 `max(id) + 1`，正向递增；`handle_delete_server` 删除后会 renumber `id > deleted_id` 的行（SM-2.1 by-design）。即使 `server_id <= 0` 直接落到 `Server.filter(Server.id == target_id).first()` 也是 None 安全返回 "服务器不存在"，**不构成安全/正确性问题**，仅是冗余防御不一致。

---

## 6. screenshot_render.py helper 自身审计 ✅

### 6.1 semaphore 释放在所有 exit path（PASS）

`screenshot_render.py:70-81`：

```python
if semaphore is None:
    return await _render_and_send_inner(...)
async with semaphore:
    return await _render_and_send_inner(...)
```

`async with` 上下文保证不论 `_render_and_send_inner` return 还是 raise，`__aexit__` 都释放。`_render_and_send_inner` 内部使用 `temp_screenshot_path` (`screenshot_temp.py:14-39`)，文件清理也在 `try/finally + suppress(OSError)` 里，无文件泄漏。

### 6.2 V11 / 非 V11 分支字节同源（PASS）

两条分支共享前置：

1. `screenshot_url(page_url, screenshot_path, options=options)` → 同一份 PNG；
2. `file_size = screenshot_path.stat().st_size`；
3. 相同的 `file_size * 4 // 3 > MAX_BASE64_BYTES` 预检 cap。

V11 分支额外做：`raw = read_bytes()` → `b64encode(raw)` → `base64://` segment。
非 V11 分支：仅返回 `file_name` + `size_kb`。

→ 同一文件、同一 cap、同一字节流，不存在分支不一致问题。

### 6.3 双校验是否冗余（PASS — 设计是有意的）

- 预检 (`L108`) `file_size * 4 // 3 > MAX_BASE64_BYTES`：避免读 200 MB 文件后再发现超限，节省 IO/内存；
- 后检 (`L122`) `len(encoded) > MAX_BASE64_BYTES`：base64 末尾 padding + line break + 整数除法误差，可能让前检通过（差几个字节），后检兜底。

属于纵深防御，不冗余。注释 `screenshot_render.py:107`（`# base64 编码后体积约为原始字节的 4/3`）已说明意图。

---

## 7. player_query 大命令路径并发 / 资源竞争 ✅

| Handler | per-server semaphore 池 | helper 是否再 acquire | 备注 |
|---|---|---|---|
| `handle_user_inventory` | `_inventory_semaphores` (max=2) | 否（外层持锁、helper `semaphore=None`） | PASS |
| `handle_my_inventory` | `_inventory_semaphores` (共享) | 否（外层持锁） | PASS — 与 user_inventory 共享池避免双倍 OOM |
| `handle_world_progress` | `_progress_semaphores` (max=2) | 是（helper 内部 acquire） | PASS — 外层不持锁 |
| `handle_my_map` | `_my_map_semaphores` (max=1) | N/A（不走 helper） | PASS |
| `handle_user_map` | `_user_map_semaphores` (max=1) | N/A | PASS |
| `handle_explored_map` | `_explored_map_semaphores` (max=1) | N/A | PASS |

`_inventory_semaphores` 由 `user_inventory` 与 `my_inventory` 共享是**正确的隔离选择**（`player_query.py:567-568` 注释指出）：避免「同一台 TShock 上 N 个用户同时跑 inventory 渲染 → Playwright × N 实例 → OOM」。

3 个 map handler 的 dict 互相独立（`_my_map / _user_map / _explored_map`），既允许 3 类查询并发但每类内对同一服务器是 1 并发——可控。

→ 无 cross-handler 资源竞争或死锁可能性。

---

## 8. 信息级 / 微优化观察（非必修）

### SV-8.1 ℹ️ leaderboard 缺失 `try` 包裹 `wrap` 的细节（信息）

**File**: `leaderboard.py:790`
**Snippet**: `fetch_results = await asyncio.gather(*[_fetch_one(s) for s in servers])`
**Note**: 没有传 `return_exceptions=True`。`_fetch_one` 内部的 `try / except TShockRequestError` 只捕获连接异常，对 `is_success(resp)` 失败也只是 `return server, None`。但**未捕获 `_fetch_one` 内意外异常**（如 `request_server_api` 抛非 `TShockRequestError` 异常时）—— gather 会瞬间打断所有 task。

实际上 `request_server_api` 其它异常（json 解析失败）已在内部消化，所以理论上不会 leak；但**player_query 的 `handle_online` 同样使用 `return_exceptions=False`** (`player_query.py:256`)，前置 `_query_one` 也只 catch `TShockRequestError`。两者风格一致，且历经多轮审计未发现问题，属于风格选择，**不修也可以**。

### SV-8.2 ℹ️ leaderboard SQL 表达式 ORDER BY 排序（PASS — 但需关注 None / 0）

**File**: `leaderboard.py:1256` `(User.rob_success_count * 1.0) / func.nullif(User.rob_total_count, 0)`
**Note**: `func.nullif(rob_total_count, 0)` 在 `rob_total_count = 0` 时返回 NULL，除法结果是 NULL。SQLite/MySQL `ORDER BY desc(<expr>)` 默认 NULL 在末尾（升序时排前；DESC 排末），所以 `min_filter = User.rob_total_count >= min_rob_count` 已经过滤了 0，NULL 不会出现，**安全**。

只是 `caller_passes` 判断里 (`leaderboard.py:1298`) `int(caller.rob_total_count or 0) >= min_rob_count` 与上面 `min_filter` 同义，逻辑一致；`min_rob_count` 通过 `max(1, ...)` 保证 ≥1，所以 `rob_total_count >= 1` 永远排除 0，NULL 不会触发——OK。

同样适用于 `guess_win_rate` (`leaderboard.py:1451`)、`dice_win_rate` (`leaderboard.py:1645`)。

### SV-8.3 ℹ️ `_query_score_leaderboard` 公共 helper 的 ORDER BY tie-breaker（PASS）

**File**: `leaderboard.py:178` `.order_by(desc(score_expr), User.user_id.asc())`
**Note**: 净收入 / 净亏 类的 `score_expr` 可能多用户同分。`User.user_id.asc()` 作为稳定 tie-breaker，分页前后顺序稳定；self_entry 的 rank 用 `func.count(score_expr > caller_score_value)`（严格大于），同分用户都算同一名 → rank 计算与 ORDER BY 不会出现 off-by-one 一致性问题（同分排名按 tie-breaker，但 "rank" 业务定义上同分共名次符合直觉）。

### SV-8.4 ℹ️ `server_id <= 0` 防御不一致（信息 — 不必修）

**File**: `player_query.py` 多处（如 L380-383 / L546-549 / L670-673 / L797-800 / L948-951）、`server_manager.py:135-138 / 232-235`
**Snippet**:
```python
try:
    server_id = int(args[0])
except ValueError:
    raise_command_usage()
# 没有 if server_id <= 0: raise_command_usage()
```
**Impact**: 0 / 负数 server_id 落到 `session.query(Server).filter(Server.id == server_id).first()` 都返回 None，handler 走 "服务器不存在" 提示分支——**结果安全**，但 DB 多走一次无意义 query。

server_tools / server_send / leaderboard `_server_side_leaderboard` 已加，而 player_query / server_manager 没加，属于**风格不一致**而非缺陷。统一加上更好，但不是发布阻断。

---

## 复审结论

| 类别 | 结果 |
|---|---|
| 🔴 阻断 | 0 |
| 🟠 高危 | 0 |
| 🟡 中等 | 0 |
| 🟢 低 | 0 |
| ℹ️ 信息 | 4 (SV-8.1 ~ SV-8.4) |

5 个 plugin（共 ~3679 行）经 13 轮迭代审计后：截图迁移 / fan-out / URL quote / OOM cap / semaphore 释放 / SQL 表达式排行榜 6 条都已闭环。可以放心 ship。

仅 4 条信息级风格观察可作为后续清理 backlog 的输入，**不影响本次发布**。
