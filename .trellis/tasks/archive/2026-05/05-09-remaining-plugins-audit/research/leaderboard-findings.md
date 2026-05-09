# Research: leaderboard.py 安全 / 性能审计

- **Query**: 排行榜插件全量 audit（性能 / 并发 / 注入 / OOM / 一致性 / 权限 / 文案 / 重复）
- **Scope**: internal — `nextbot/plugins/leaderboard.py` (1743 行) + 相关共享工具
- **Date**: 2026-05-09
- **审计基准对照**:
  - `nextbot/large_image.py` — `MAX_BASE64_BYTES = 200 MiB`、`semaphore_for(...)` per-server pool
  - `nextbot/screenshot_temp.py` — `temp_screenshot_path` 已带 uuid8 后缀（PQA-3.1 / PQB-X.3 已修）
  - `nextbot/plugins/ban.py:50,215,232` — `_ban_list_semaphore = asyncio.Semaphore(2)` + 截图前 `file_size * 4 // 3 > MAX_BASE64_BYTES` 拒绝
  - `nextbot/plugins/player_query.py:70-73,762,911,1035` — 每业务 per-server `dict[int, Semaphore]` + 编码后再 cap base64
  - `nextbot/db.py:35-96` — `DEFAULT_GUEST_PERMISSIONS` 列入了全部 17 个 leaderboard.* 权限（默认 guest 可调用）
  - `nextbot/db.py:135-167` — `User` 表统计字段 `coins / sign_streak / sign_total / rob_* / guess_* / dice_*` **全部未加 index**，仅 `name` 有 `index=True`
  - `server/pages/leaderboard_page.py` + `server/templates/leaderboard.html:307-433` — 前端用 `JSON.parse(textContent)` 取数据并通过 `nameEl.textContent / idText.textContent / v.textContent` 渲染，**不存在 XSS 注入面**

---

## Findings

### Handler 索引（共 17 个 `category="排行榜"`，全部 `permission` 默认 guest）

| # | 命令 | 行号 | permission | 数据源 | 排序方式 | 备注 |
|---|---|---|---|---|---|---|
| 1 | 金币排行榜 | 128-202 | leaderboard.coins | DB User | SQL `ORDER BY coins DESC` LIMIT/OFFSET | self_entry: `COUNT(coins>x)+1` |
| 2 | 连续签到排行榜 | 205-279 | leaderboard.streak | DB User | SQL `ORDER BY sign_streak DESC` LIMIT/OFFSET | 同上 |
| 3 | 签到排行榜 | 282-356 | leaderboard.signin | DB User | SQL `ORDER BY sign_total DESC` LIMIT/OFFSET | 同上 |
| 4 | 死亡排行榜 | 359-466 | leaderboard.deaths | TShock API | server-side 已排序，Python slice | server_id 取自 args |
| 5 | 渔夫任务排行榜 | 469-576 | leaderboard.fishing | TShock API | server-side 已排序，Python slice | 同上 |
| 6 | 在线时长排行榜 | 579-690 | leaderboard.online_time | TShock API | server-side 已排序，Python slice | 同上 |
| 7 | 地图探索率排行榜 | 693-811 | leaderboard.map_exploration | TShock API | server-side 已排序，Python slice | 同上 |
| 8 | 总在线时长排行榜 | 814-923 | leaderboard.total_online_time | **N 个 TShock API 串行 fan-out** | Python sort | 危险点 |
| 9 | 今日签到排行榜 | 933-1040 | leaderboard.daily_sign | DB UserSignRecord JOIN User | SQL `ORDER BY created_at` LIMIT/OFFSET | self 用 created_at 较早数 |
| 10 | 抢劫排行榜 (净收入) | 1047-1117 | leaderboard.rob_income | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |
| 11 | 被抢排行榜 | 1120-1195 | leaderboard.rob_loss | DB User | SQL `ORDER BY rob_total_loss DESC` LIMIT/OFFSET | self_entry COUNT |
| 12 | 抢劫罚款排行榜 | 1198-1273 | leaderboard.rob_penalty | DB User | SQL `ORDER BY rob_total_penalty DESC` LIMIT/OFFSET | self_entry COUNT |
| 13 | 抢劫成功率排行榜 | 1276-1375 | leaderboard.rob_success_rate | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |
| 14 | 猜数字排行榜 (净收入) | 1400-1470 | leaderboard.guess_number_income | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |
| 15 | 猜数字胜率排行榜 | 1473-1570 | leaderboard.guess_number_win_rate | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |
| 16 | 掷骰子排行榜 (净收入) | 1573-1643 | leaderboard.dice_income | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |
| 17 | 掷骰子胜率排行榜 | 1646-1743 | leaderboard.dice_win_rate | **DB User .all() + Python sort** | 全表 ORM 读 | 高危 |

公共渲染：`_render_and_send`（lines 83-125）走 `temp_screenshot_path("leaderboard-*")` + `screenshot_url` + V11 `MessageSegment.image(file=base64://...)`。
**无 per-handler / 全局 semaphore，无 base64 size cap**，与 SB-2.2 / PQB-X.3 修复模式不一致。

---

## 关键问题清单

### LB-0 通用层（影响全部 17 个 handler）

#### LB-0.1 截图发送链缺 OOM 防护（base64 上限 + semaphore）— 🔴 严重

**文件**：`nextbot/plugins/leaderboard.py:83-125 _render_and_send`

```python
async with temp_screenshot_path(file_prefix) as screenshot_path:
    try:
        await screenshot_url(page_url, screenshot_path, options=LEADERBOARD_SCREENSHOT_OPTIONS)
    ...
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)   # ← 直接 read+b64encode，无 size cap
        except OSError:
            ...
        await bot.send(event, OBV11MessageSegment.image(file=image_uri))
```

**对照 ban.py:215-237**：先 `async with _ban_list_semaphore`（限制并发 2），再 `screenshot_path.stat().st_size * 4 // 3 > MAX_BASE64_BYTES` 拒绝。
**对照 player_query.py:762**：`if len(b64_string) > _MAX_BASE64_BYTES` 编码后再校验。

**Impact**：
- `_to_base64_image_uri` 直接 `path.read_bytes()` → `b64encode` → 单次进程内存峰值 ~ 2.3× 原文件大小（read 缓冲 1× + b64 编码后 1.33×）。`LEADERBOARD_SCREENSHOT_OPTIONS` 用 `viewport_width=920 viewport_height=800 full_page=True fit_content_height=True`，limit=50 时页面会很长，再叠加并发就有 OOM 风险。
- 17 个排行榜命令同时被多个用户调用，无 semaphore 控制，会同时持有多份大 base64 字符串。
- 与 SB-2.2 / PQB-X.3 已建立的"截图发送 = 读字节 → cap → 发送"硬约束破坏一致性，新人改 leaderboard 不会想到补这层防护。

**复现**：默认 limit 上限 50，并发 N 用户一起发"金币排行榜 1"，Python 进程驻留 N×（截图字节 + b64 编码）；攻击者只要拉够并发即可挤占内存。

**修法**：参考 ban.py / player_query.py：
1. 模块顶部 `_screenshot_semaphore = asyncio.Semaphore(2)` 或 per-handler-key 池；
2. `_render_and_send` 内取 `screenshot_path.stat().st_size`，`* 4 // 3 > MAX_BASE64_BYTES` 时 `reply_failure("查询", "排行榜过大，请使用更小的页码")`；
3. 整段 send 包在 `async with _screenshot_semaphore`。

---

#### LB-0.2 用户名 / 服务器名信任链审计（注入面）— ✅ 实际无 XSS

**文件**：`server/templates/leaderboard.html:366-422`、`server/pages/leaderboard_page.py:22-42`

模板 JS 通过 `nameEl.textContent = String(item?.name || "").trim() || "—";` 渲染所有用户名 / value，且 payload 经 `json.dumps(...).replace("</", "<\\/")` 注入到 `<script type="application/json">` 后由 `JSON.parse(textContent)` 读取——**未发现 XSS 注入面**，handler 端不需要再 escape。

**Caveat**：当前防护**完全依赖渲染层的 textContent**。若未来有人改用不安全 DOM 写入方式（例如直接拼接 HTML 字符串）或换模板（例如 SSR 字符串拼接），这道防线就破了。建议在 `leaderboard_page.build_payload` 写明"normalize 后字段必须由 caller 用 textContent 渲染，禁止任何形式的 raw HTML 注入"。属 ℹ️ 提示，不是 issue。

---

#### LB-0.3 server_id 缺 `> 0` 校验，落库 `Server.id` 自增主键有正负不区分隐患 — 🟡 一致性

**影响**：所有读 server_id 的 handler — `handle_deaths_leaderboard:388 / handle_fishing_leaderboard:498 / handle_online_time_leaderboard:608 / handle_map_exploration_leaderboard:722`

```python
try:
    server_id = int(args[0])
except ValueError:
    raise_command_usage()
# ⚠️ 没有 if server_id <= 0
```

**对照前序 ST-5.5 模式**（任务描述提到）：所有以 server_id 进 SQL 的入口应做 `server_id <= 0 → reply_failure / raise_command_usage`。
当前 `Server` 主键 `autoincrement=False`（`db.py:120`），意味着 server_id 完全由建表脚本指定，理论上永远不会有负值。负数能进入 SQL 不会报错，只会被 `Server.id == server_id` 过滤掉返回 None，然后落入"服务器不存在"分支——**本身不会越权**，但与 ST-5.5 防御性编码模式不一致。

**修法**：每个 handler 在 `int(args[0])` 后加：
```python
if server_id <= 0:
    raise_command_usage()
```

---

#### LB-0.4 错误文案违反"动作 + 结果，原因"规范 — 🟡 文案

**位置**：所有出现 `reply_failure("查询", f"{exc}")` / `reply_failure("查询", f"{get_error_reason(response)}")` 的地方
- `_render_and_send:112` — `reply_failure("查询", f"{exc}")`
- `handle_deaths_leaderboard:419` — `reply_failure("查询", f"{get_error_reason(response)}")`
- `handle_fishing_leaderboard:529` — 同上
- `handle_online_time_leaderboard:639` — 同上
- `handle_map_exploration_leaderboard:753` — 同上

`reply_failure(action, reason)` 已经会拼成 `❌ 查询失败，{reason}`，所以传入 `f"{exc}"` 是 OK 的；
但当 reason 来自 `get_error_reason(response)` 且本身可能是空字符串时，会输出 `❌ 查询失败，` 这种孤悬逗号的文案。

**对照 CLAUDE.md "用户操作反馈文案规范"**：失败原因必须原样透传 API 返回；空 reason 时应 fallback 到通用语而不是输出空。

**Impact**：用户体验问题，非安全。属低优。

**修法**：`reason or "未知错误"` 或在 `reply_failure` 里判断空原因。

---

#### LB-0.5 `_to_base64_image_uri` 用同步 IO 阻塞 event loop — 🟢 性能微瑕

`leaderboard.py:63-66`：
```python
def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()       # ← 同步阻塞 event loop
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"
```

**Impact**：单文件几 MB 时阻塞数十毫秒，并发场景累积。ban.py / player_query.py 同样是同步 IO，所以不算 leaderboard.py 独有问题，但 leaderboard 因为可能 17 路并发更脆弱。

**修法**：可放 `await asyncio.to_thread(...)`。属优化，不影响安全。

---

### LB-1 金币排行榜（lines 128-202）— SQL 分页代表实现，OK

**SQL 查询计划**：
- `total_count = session.query(User).count()` — 全表 COUNT
- `session.query(User).order_by(User.coins.desc()).offset(offset).limit(limit).all()` — `coins` 字段无 index → ORDER BY 走 sortmerge

**LB-1.1 缺索引导致 ORDER BY 全表排序 — 🟠 性能**

`db.py:141 coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)` —— **没有 `index=True`**。

**Impact**：用户表行数小（百~万级）时无感；增长到十万级，每次榜单都做全表排序，叠加 17 个榜单 + 用户高频访问会拖慢 DB。

**修法**：`coins` 字段加 `index=True`，并在 alembic / 启动脚本里 CREATE INDEX。同样问题适用于 `sign_streak / sign_total / rob_total_loss / rob_total_penalty`（都有 SQL `ORDER BY` LIMIT/OFFSET 路径）。

**LB-1.2 self_entry 计算用 `COUNT(*) WHERE coins > caller_coins`，走索引才快 — 🟠 性能**

```python
caller_rank = session.query(User).filter(User.coins > caller_coins).count() + 1
```

无 index 时退化为全表 scan。修复同 LB-1.1。

**LB-1.3 计算 caller_rank 时未持有事务一致性 — 🟡 一致性**

`total_count` / `users` / `caller_coins` / `caller_rank` 四次 session.query 不在同一事务隔离级别下，期间他人 coins 变化会让 `caller_rank > total_count` 可能出现。session 是默认 commit 级别，单 handler 内并发改 `User.coins` 时存在 phantom。

**Impact**：仅显示数字偏差 1-2 位，无安全后果。

---

### LB-2 死亡排行榜（lines 359-466）— TShock API 拉全量 + Python slice

代表 LB-4 / LB-5 / LB-6 / LB-7 (death/fishing/online_time/map_exploration) 同模式。

**LB-2.1 `request_server_api` 默认 5s read timeout 对榜单可能偏短 — 🟡**

数据量大时 `tshock_api.py:53 timeout: float | httpx.Timeout = 5.0` 默认 5 秒可能不够。当前 leaderboard 调用未传 `timeout=`。

**Impact**：服务器在线 200+ 玩家、map 数据序列化慢时容易 TShockRequestError → 用户看到"无法连接服务器"误以为服务器挂了。

**修法**：可考虑显式 `timeout=15.0`。

**LB-2.2 全量 entries 在内存 / 通过 JSON 反序列化 — 🟡**

`raw_entries = response.payload.get("entries")` —— TShock 返回多大客户端就吃多大。无 size cap、无 entry-count cap。
攻击场景：攻击者控制 TShock 后端 / 中间人塞 100MB JSON。

**Impact**：Python 进程内存峰值 = JSON 字节 + dict / list overhead × N。

**修法**：截一个上限，如 `if len(all_entries) > 10000: all_entries = all_entries[:10000]` 或在 `request_server_api` 层面加响应体大小限制。

**LB-2.3 caller_name 匹配用 O(N) 线性扫描 — 🟢 性能微瑕**

`for idx, e in enumerate(all_entries):` 在 1k 行级别 OK，万级以上有压力，但很少触发。

---

### LB-3 总在线时长排行榜（lines 814-923）— 🔴 严重，多个并发隐患

**LB-3.1 N 个服务器 TShock API 串行调用 — 🔴 性能**

```python
for server in servers:
    try:
        response = await request_server_api(server, "/nextbot/leaderboards/online-time")
    except TShockRequestError:
        continue
```

**Impact**：N 台服务器逐一 await，每个默认 5s timeout。N=10 台 1 台超时 → 单次命令耗时 5+ 秒；N=20 → 灾难。
**对照 SB-2.x security audit 结论**：fan-out 应用 `asyncio.gather + asyncio.Semaphore` 并发限制，并对每个调用加超时上限。

**修法**：
```python
async def _fetch(server):
    try:
        return server, await request_server_api(server, "/nextbot/leaderboards/online-time", timeout=10.0)
    except TShockRequestError:
        return server, None

async with asyncio.Semaphore(5):
    results = await asyncio.gather(*[_fetch(s) for s in servers], return_exceptions=False)
```

**LB-3.2 `totals: dict[str, int]` 无大小上限 — 🟠 OOM**

`totals[username] = totals.get(username, 0) + int(e["onlineSeconds"])` 不断增长，username 是 str 主键。攻击者通过控制 TShock 给出不同 username 即可让 dict 无界增长。

**Impact**：单服务器返回 N 个虚假 username × M 个服务器 = N×M 条记录。

**修法**：limit 总键数（如 50000）后早停。

**LB-3.3 整个 fan-out 过程持有 0 个 session 但**很慢，期间 caller_name 已读完，**OK**。但 `total_count = len(all_entries)` 后再分页 → 整段排序 in-memory。N 服务器 × M 用户 ~ 大数据时性能掉。

**LB-3.4 `if not servers: reply_failure("查询", "暂无服务器")` 文案 — 🟡**

属正常空集，应该用 reply_info / reply_warning，不是 reply_failure。"查询失败，暂无服务器"语义模糊，用户会误以为是请求出错。

---

### LB-4-7 Server-side TShock 榜单（deaths / fishing / online_time / map_exploration）— 🟠 同 LB-2 模式

行号：`handle_deaths_leaderboard:380-466 / handle_fishing_leaderboard:490-576 / handle_online_time_leaderboard:600-690 / handle_map_exploration_leaderboard:714-811`

四个 handler 90% 重复代码：

```python
session = get_session()
try:
    server = session.query(Server).filter(Server.id == server_id).first()
    caller_id = event.get_user_id()
    caller = session.query(User).filter(User.user_id == caller_id).first()
    caller_name = caller.name if caller is not None else None
finally:
    session.close()

if server is None:
    await bot.send(event, reply_failure("查询", "服务器不存在"))
    return

try:
    response = await request_server_api(server, "/nextbot/leaderboards/<endpoint>")
except TShockRequestError:
    await bot.send(event, reply_failure("查询", "无法连接服务器"))
    return

if not is_success(response):
    await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
    return

raw_entries = response.payload.get("entries")
if not isinstance(raw_entries, list):
    await bot.send(event, reply_failure("查询", "返回数据格式错误"))
    return

all_entries = [e for e in raw_entries if isinstance(e, dict) and ...]
total_count = len(all_entries)
total_pages = max(1, math.ceil(total_count / limit))
...
self_entry = None
if caller_name is not None:
    for idx, e in enumerate(all_entries):
        if e.get("username") == caller_name:
            self_entry = {...}
            break
```

**LB-4.1 重复代码可抽 helper — 🟡**

可抽出 `_fetch_server_leaderboard(server, endpoint, value_field, value_formatter=None)`，配合 `_render_and_send` 大幅瘦身。

**LB-4.2 同 LB-2.x 问题（timeout / size cap / linear caller search）适用于这 4 个 handler — 同 LB-2.x**

---

### LB-8 今日签到排行榜（lines 933-1040）— 🟡 SQL 优化

**LB-8.1 SQL JOIN UserSignRecord 缺组合索引 — 🟠 性能**

`db.py:203-215 UserSignRecord` 只有 `(user_id, sign_date)` UniqueConstraint，**`sign_date` 单列无 index，`created_at` 也无 index**。

```python
session.query(UserSignRecord, User.name)
    .join(User, User.user_id == UserSignRecord.user_id)
    .filter(UserSignRecord.sign_date == today)
    .order_by(UserSignRecord.created_at.asc())
    .offset(offset)
    .limit(limit)
```

`WHERE sign_date = today ORDER BY created_at` 没有合适复合索引时全表扫描 + 排序。每天表新增 N 条（N=活跃签到用户数），随时间累积成大表后会越来越慢。

**修法**：加 `Index("idx_sign_record_date_created", "sign_date", "created_at")`。

**LB-8.2 caller_rank 第二次查 UserSignRecord — 🟢 微瑕**

```python
caller_rank = session.query(UserSignRecord).filter(
    UserSignRecord.sign_date == today,
    UserSignRecord.created_at < caller_record.created_at,
).count() + 1
```

加了 idx_sign_record_date_created 后这条 COUNT 也会走 index range，无 index 时全表 scan。

---

### LB-10 抢劫排行榜（净收入，lines 1047-1117）— 🔴 严重，全表 ORM 读

**LB-10.1 `.all() + Python sort` 全表读 — 🔴 性能 / OOM**

```python
all_users = session.query(User).filter(User.rob_total_count > 0).all()  # ← 全表 ORM 读
sorted_users = sorted(all_users, key=_rob_net_income, reverse=True)     # ← Python 排序
total_count = len(sorted_users)
...
page_users = sorted_users[offset : offset + limit]
```

`_rob_net_income` 是 `rob_total_gain - rob_total_penalty`，无法直接 SQL ORDER BY 同时走 index（除非建复合表达式索引）。

**Impact**：
- User 表大量无效行（鬼号 / 历史游客）会被全部加载到内存（每个 ORM 实例数 KB）。
- `rob_total_count > 0` 过滤虽减少行数，但仍可能数千起。
- 同样问题适用于 LB-13（rob_success_rate）、LB-14（guess_income）、LB-15（guess_win_rate）、LB-16（dice_income）、LB-17（dice_win_rate） — **6 个 handler 全部踩这个坑**。
- 这是 task brief 中明确标记为"最大关注"的"全表 ORM .all() then Python slice"反模式。

**修法（两选一）**：
1. **首选**：改写为 SQL 表达式排序：
   ```python
   net_income = (User.rob_total_gain - User.rob_total_penalty).label("net_income")
   query = (
       session.query(User, net_income)
       .filter(User.rob_total_count > 0)
       .order_by(net_income.desc())
   )
   total_count = query.count()
   page_rows = query.offset(offset).limit(limit).all()
   ```
   self_entry 用 `WHERE (gain - penalty) > caller_net` 的 COUNT。
2. 若坚持 Python sort：至少加 cap，例如最多读 5000 行，提示用户榜单太长。

**LB-10.2 self_entry 计算用 `sum(1 for u in sorted_users if ...)` 是 O(N) Python 循环 — 🟢 同 LB-10.1 同时修**

---

### LB-13 抢劫成功率排行榜（lines 1276-1375）— 🔴 全表 ORM + min_rob_count 缺上限

**LB-13.1 同 LB-10.1 全表 ORM .all() + Python sort — 🔴**

```python
all_users = session.query(User).filter(User.rob_total_count >= min_rob_count).all()
def _success_rate(u: User) -> float:
    total = int(u.rob_total_count or 0)
    if total == 0:
        return 0.0
    return int(u.rob_success_count or 0) / total
sorted_users = sorted(all_users, key=_success_rate, reverse=True)
```

修法同 LB-10.1，SQL 表达式：`(rob_success_count * 1.0 / rob_total_count).label("rate")`。

**LB-13.2 `min_rob_count` 参数无 max 上限 — 🟡 资源**

`leaderboard.py:1293-1300`：
```python
"min_rob_count": {
    "type": "int",
    ...
    "default": 1,
    "min": 1,
    # 注意：没有 "max"
},
```

虽然 `max(1, int(get_current_param(...)))` 防住了下界，**没有上界**。用户传 `min_rob_count=0`/负数 被拉到 1，但传 `min_rob_count=99999999` 不报错。
影响有限（只会让结果集更少），但同类参数 limit 都有 max=50，此处缺一致性。

**LB-13.3 default 值不一致 — 🟢 配置**

注释说"上榜需要的最低抢劫次数"`default: 1`，但 handler 默认 `min_rob_count = max(1, int(get_current_param("min_rob_count", 10)))` 用的是 `10`。
配置 schema 与代码默认不一致，会让管理员困惑。

---

### LB-14 / LB-16 猜数 / 骰子净收入榜（lines 1400-1470 / 1573-1643）— 🔴 同 LB-10.1

完全同模式，参考 LB-10.x 修法。

---

### LB-15 / LB-17 猜数 / 骰子胜率榜（lines 1473-1570 / 1646-1743）— 🔴 同 LB-13

完全同模式，参考 LB-13.x 修法。

**附加：min_play_count 同 LB-13.2 缺 max 上限**（lines 1490-1497, 1663-1670）。

---

### LB-99 权限审计

**LB-99.1 全部 17 个 leaderboard 权限默认 guest — ℹ️ 设计意图，不是 issue**

`db.py:48-64` 列入了 17 个 `leaderboard.*` 权限，意味着任何注册用户（甚至未注册 guest）都可以频繁触发：
- 17 路全表扫描 / 全表排序
- 17 路截图渲染（每路一次 headless browser 渲染 + b64 编码）
- 8 路远程 TShock API 请求（包括 LB-3 的 N 路 fan-out）

**Impact 复合场景**：5 个未授权用户每秒并发"金币排行榜 / 抢劫排行榜 / 猜数字胜率排行榜 ..." → DB CPU 飙高、headless browser 队列爆炸、TShock 服务器压力大。
当前**完全没有 rate-limit、cooldown、screenshot semaphore**——这是最容易被滥用的 17 个 guest-default 命令。

**修法**：
- (a) 引入 per-user / per-command cooldown（`command_control` 是否已支持？需查）
- (b) `_screenshot_semaphore = asyncio.Semaphore(2)` 限制全局排行榜截图并发
- (c) 考虑某些"重"榜单（如总在线时长 / 全表 ORM 的 6 个）需要更高权限

---

## 建议修复优先级

| 优先级 | Issue | 理由 |
|---|---|---|
| 🔴 P0 | LB-0.1 截图 OOM 防护（semaphore + base64 cap） | 直接 OOM 风险，且与 SB-2.2 / PQB-X.3 已建立 pattern 严重不一致 |
| 🔴 P0 | LB-3.1 N 服务器串行 fan-out | 单慢服务器拖死整个命令，DOS 自身 |
| 🔴 P0 | LB-10/13/14/15/16/17 全表 ORM .all() + Python sort（6 个 handler） | task brief 标记的最大关注点 |
| 🟠 P1 | LB-1.1 缺 index：coins / sign_streak / sign_total / rob_total_loss / rob_total_penalty | 数据量增长后 SQL ORDER BY 全部退化 |
| 🟠 P1 | LB-3.2 totals dict 无界 | 配合 LB-3.1 一起修 |
| 🟠 P1 | LB-8.1 UserSignRecord 缺 (sign_date, created_at) 复合索引 | 累积数据后慢 |
| 🟠 P1 | LB-2.2 远端响应大小 cap | 中间人 / 后端 bug 防护 |
| 🟡 P2 | LB-0.3 server_id ≤ 0 校验（4 处） | 防御性编码一致性 |
| 🟡 P2 | LB-4.1 抽公共 helper（4 个 server-side handler 高度重复） | 维护性 |
| 🟡 P2 | LB-13.2 / LB-15 / LB-17 min_* 参数加 max 上限 | 一致性 |
| 🟡 P2 | LB-13.3 配置 schema default 与代码默认不一致 | 配置正确性 |
| 🟡 P2 | LB-3.4 "暂无服务器"应该用 reply_info | 文案语义 |
| 🟡 P2 | LB-0.4 空 reason 时孤悬逗号 | 文案 |
| ℹ️ | LB-99.1 17 个命令默认 guest，是设计意图 | 加强 cooldown + 截图 semaphore |
| ℹ️ | LB-0.2 XSS 实际无注入面，但要在 leaderboard_page 留 docstring 警告 | 维护性 |

---

## Caveats / Not Found

1. 未审 `command_control` 是否已带 cooldown / rate-limit 机制 — 若有，LB-99.1 的 (a) 项可直接复用；若无，建议 task 内引入。
2. 未确认 `request_server_api` 是否在 SB 系列里加过响应体大小 cap — 若已在 transport 层 cap，则 LB-2.2 / LB-3.2 在 handler 端只是双保险。
3. SB-2.2 提到的 `_ban_list_semaphore = asyncio.Semaphore(2)` 数字是否合适于 leaderboard（17 个命令 + 高 guest 并发）需评估，可能需要分级（轻 SQL 榜 vs 重 fan-out 榜）。
4. `Server.id` 是 `autoincrement=False` 主键由建表脚本指定，理论上不会有负 server_id 进 DB，所以 LB-0.3 是防御性而非实际可利用。
