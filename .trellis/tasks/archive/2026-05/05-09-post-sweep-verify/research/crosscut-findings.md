# Research: post-sweep cross-cutting verification

- **Query**: 14 批审计 + final-sweep 后做横切一致性最后一审；按 11 项 grep checklist 扫漏网同形模式
- **Scope**: internal
- **Date**: 2026-05-09

## Findings

### Checklist 总览

| # | 主题 | 状态 |
|---|---|---|
| 1 | ORM dirty-set + commit | ⚠️ 1 处剩余（`user_manager.py:491` rename，但已加 IntegrityError 兜底，整体已可控） |
| 2 | 串行 fan-out for 循环 | 🟡 mutation 全部走 `broadcast()` / `asyncio.gather`，但 2 处 read-only online-check 仍是串行 |
| 3 | URL 段 `quote(safe="")` 加固 | 🟠 `user_manager._sync_one_whitelist` / `_rename_one_whitelist` 三个 URL 完全没 quote |
| 4 | `int(event.get_user_id())` try/except 包装 | 🟠 仅 `player_query._safe_at_segment` 有兜底；其余 14+ 处文件全部直接 `int()` |
| 5 | 金币变更日志统一格式 | ✅ 主要路径已迁移到 M9 格式；`金币变更：actor=... target=... action=... before=... after=...` 已 9 处使用 |
| 6 | mutation handler audit_permission_change 完整性 | 🟡 `server_manager` add/delete server 与 `economy.coins.add/remove` admin 操作未走 audit |
| 7 | `screenshot_url` 直接调用残留 | ✅ grep 干净（仅 `screenshot_render.py` 内部一处 + docstring 引用） |
| 8 | `update(User).values(coins=...)` 加币不走 cap | 🔴 `dice.py` 3 处 / `guess_number.py` 3 处 / `rob.py` 3 处 / `economy.py:476` / `lottery.py:712` 加币路径绕开 `add_coins_with_cap` 与 `coins + delta <= MAX_COINS_AMOUNT` 条件 |
| 9 | `_to_base64_image_uri` / 内联 base64 | ✅ grep 干净（仅 `screenshot_render.py:118` 内部统一编码点） |
| 10 | `IntegrityError` 正确 rollback + 用户原因 | ✅ 全部 16 处都有 `session.rollback()` + `reply_failure` 明确原因 |
| 11 | `MAX_COINS_AMOUNT` import 路径一致 | ✅ 全项目从 `nextbot.plugins.economy` 单点导入，无重复定义 |

---

### 🔴 PC-8.1 — 加币 UPDATE 绕开 MAX_COINS_AMOUNT cap（多 handler）

- **Severity**: 🔴 Critical（绕过 SF-X.1 全局账户上限保护，长期累积会让用户 coins 越界 / 与 lottery / red_packet / sign / transfer 已修补的 cap 行为不一致）
- **Files**:
  - `nextbot/plugins/dice.py:203, 215, 235` — payout 入账（净赢 / 净输但有 payout / net=0 退押金）
  - `nextbot/plugins/guess_number.py:239, 251, 271` — 同 dice 模式
  - `nextbot/plugins/rob.py:305, 318, 356` — 抢劫 attacker / counter victim 加币
  - `nextbot/plugins/economy.py:476` — 转账 partial cap 退款给 sender
  - `nextbot/plugins/lottery.py:712` — 仓库不足回退退还 `total_cost` 给 user
- **Snippet (代表)**:
  ```python
  # dice.py:201-208
  session.execute(
      update(User)
      .where(User.user_id == user_id)
      .values(
          coins=User.coins + payout,  # ← 没有 coins + payout <= MAX_COINS_AMOUNT
          dice_total_count=User.dice_total_count + 1,
          ...
      )
  )
  ```
- **Impact**:
  1. dice / guess_number：cost ≤ MAX_COINS_AMOUNT (1e8)，但 `payout = cost × triple_multiplier`（默认 10×）→ 单次最大入账 1e9，叠加用户已有近 1e8 → coins 可达 ~1.1e9，**直接越过 SF-X.1 上限 11×**。
  2. rob.py：amount 来自 victim_coins，单次合法但与已积累 attacker.coins 相加可越界。
  3. economy.py:476 转账回退：sender 在 deduct 后到 refund 之间，可能因抢红包 / 转入 / 抽奖等再次入账，refund 加回会越过 cap。
  4. lottery.py:712 抽奖回退：同样的时间窗内可被 partial cap 路径加币。
- **复现**:
  1. 用户 A coins 已接近 MAX_COINS_AMOUNT（如 9.5e7）。
  2. A 押注 dice cost=1e7、押豹子，触发三同 → payout=1e8、入账后 coins=1.85e8。
  3. 后续 lottery / red_packet 路径已经 cap，但 dice 写入的 coins 已经超 cap，此时 `add_coins_with_cap` 内部的 `room = max(0, MAX_COINS_AMOUNT - coins_now)` 会返回 0，把 dice 路径的「越界」当作「触顶」继续静默放过 → SF-X.1 不变量已被破坏。
- **修法**:
  - 三类 handler 改写为先用 `add_coins_with_cap(session, user_id, payout)`，把统计字段 (`dice_total_count` 等) 拆成第二条 `update(User)` 单独累加。
  - 或者把条件 `User.coins + payout <= MAX_COINS_AMOUNT` 加到现有 UPDATE，rowcount=0 时退而求其次走 partial cap。
  - economy.py:476 / lottery.py:712 退款路径同样接 `add_coins_with_cap`，剩余无法退还的应记 WARN + 给用户保留提示。

---

### 🟠 PC-3.1 — `_sync_one_whitelist` / `_rename_one_whitelist` URL 段未 `quote(safe="")`

- **Severity**: 🟠 High（与 ban_core / player_query 已加固的同形模式不一致；当前依赖 `_validate_user_name` 字符白名单防御，是窄保护）
- **Files**:
  - `nextbot/plugins/user_manager.py:100` — `f"/nextbot/whitelist/add/{name}"`
  - `nextbot/plugins/user_manager.py:153` — `f"/nextbot/whitelist/remove/{old_name}"`
  - `nextbot/plugins/user_manager.py:164` — `f"/nextbot/whitelist/add/{new_name}"`
- **Snippet**:
  ```python
  # user_manager.py:96-101
  response = await request_server_api(
      server,
      f"/nextbot/whitelist/add/{name}",
  )
  ```
  对比 `ban_core.py:152, 180` / `player_query.py:422-427, 572-577, 699-703, 848-853`：
  ```python
  encoded_name = quote(user_name, safe="")
  await request_server_api(server, f"/nextbot/blacklist/add/{encoded_name}")
  ```
- **Impact**:
  - 现实路径下 `_validate_user_name`（user_manager.py:56）已经把 name 限制成 `[A-Za-z0-9一-鿿]+`，不会含 `/` `?` `#` 空格——所以**当前**没有 RCE / 路径越权。
  - 但只要历史导入了非数据库受限的旧用户名（e.g. WebUI 导入、SQL 直插）或将来放宽校验（产品需求经常加 ` ` `_` `-` `.`），同形模式立刻成 URL 注入。
  - 与 ban_core / player_query 的「全路径段 quote」收敛不一致，是个会被未来变更打破的漏点。
- **复现**: DBA 直接在 DB 里 `update users set name='admin/../config' where user_id='x'` → 调用「同步白名单」时拼出 `/nextbot/whitelist/add/admin/../config` → TShock side route confusion / 404。
- **修法**:
  ```python
  from urllib.parse import quote
  encoded_name = quote(name, safe="")
  await request_server_api(server, f"/nextbot/whitelist/add/{encoded_name}")
  ```
  3 处 URL 都改。

---

### 🟠 PC-4.1 — `int(event.get_user_id())` 缺乏防御性兜底（除 player_query 外全部直接 int）

- **Severity**: 🟠 High（PQB-X.4 已识别此风险并写了 `_safe_at_segment` helper，但**只在 player_query.py 内部使用**，未推广）
- **Files**: 至少 18 处直接 `int(event.get_user_id())` 无 try/except，分布于：
  - `nextbot/text_utils.py:96`（at_prefix，公共 helper）
  - `nextbot/command_config.py:966`
  - `nextbot/plugins/economy.py:216, 396, 540, 639`
  - `nextbot/plugins/permission_manager.py:99`
  - `nextbot/plugins/warehouse.py:416, 594`
  - `nextbot/plugins/guess_number.py:126`
  - `nextbot/plugins/group_manager.py:93`
  - `nextbot/plugins/user_manager.py:445`
  - `nextbot/plugins/rob_protection.py:53`
  - `nextbot/plugins/dice.py:101`
  - `nextbot/plugins/red_packet.py:126, 268, 416`
  - `nextbot/plugins/rob.py:147`
- **Snippet**:
  ```python
  # text_utils.py:96
  return OBV11MessageSegment.at(int(event.get_user_id())) + sep + content
  ```
  对比 `player_query.py:171-181`：
  ```python
  def _safe_at_segment(user_id: str) -> "OBV11MessageSegment | None":
      try:
          return OBV11MessageSegment.at(int(user_id))
      except (TypeError, ValueError):
          logger.warning(...)
          return None
  ```
- **Impact**:
  - OneBot V11 协议下 `user_id` 是数字字符串，正常路径不会出错。
  - 但任何非 V11 适配器（V11-shim / Telegram bridge / 自研协议）push 非数字 user_id 时，全 18 处 handler 直接抛 `ValueError` → uncaught → handler 中断 → 用户体验差 + 噪音 traceback。
  - PQB-X.4 已经识别这是个 hardening 项，写了 `_safe_at_segment` 但没下沉到 `text_utils`。
- **复现**: 模拟非 V11 user_id（如 `event.get_user_id() == "tg-123"`）→ 任何转账 / 添加金币 / @user 路径直接 ValueError。
- **修法**:
  - 把 `_safe_at_segment` 提升到 `nextbot/text_utils.py`（与 `at_prefix` 同模块），导出给所有 handler 共用。
  - `text_utils.at_prefix` 内部改用 `_safe_at_segment`，None 时 fallback 到不带 @ 的 prefix。
  - 18 处 handler 内的 `OBV11MessageSegment.at(int(event.get_user_id()))` 全部替换为 helper。

---

### 🟡 PC-6.1 — `server_manager` 与 `economy.coins.add/remove` admin 操作未走 `audit_permission_change`

- **Severity**: 🟡 Medium（当前 audit 模块 docstring 限定为 "permission-mutating handler"，这两类是否归类未明确，但事故回查时有缺）
- **Files / 现状**:
  - `nextbot/plugins/server_manager.py:79-100`（添加服务器）/ `:149-159`（删除服务器）：仅 `logger.info`，无 `audit_permission_change`。`server_manager` 顶部无 `from nextbot.audit import` 导入。
  - `nextbot/plugins/economy.py:533-617`（添加金币）/ `:644-720`（扣除金币）：admin 对任意用户的 coins 改写，仅 INFO 级 `金币变更：` 日志，无 audit。
- **Impact**:
  - 服务器条目变更（add/delete）涉及 token、IP、port，是基础设施级配置，事故时需要回查谁在何时改了哪台服务器。当前只能 grep INFO 日志，没有 WARN 级审计聚合。
  - admin 加 / 扣金币是直接对其它用户余额的操作，敏感度等同 `permission_manager` 的角色变更。当前 INFO 流量大，WARN 级审计能让 SOC 一眼区分。
- **复现**: 任意管理员账号被盗后，攻击者执行 `添加服务器 fake 1.2.3.4 ...` 或 `添加金币 victim 99999999`，只在 INFO 流中混入一行，无 WARN 突出。
- **修法**:
  - `server_manager`：add 路径加 `audit_permission_change(action="server.add", target=str(new_id), after={"name":..., "ip":..., "ports":...})`；delete 路径加 `action="server.delete"` + `before={...}`。
  - `economy.coins.add` / `economy.coins.remove` admin 路径加 `action="economy.coins.add"` / `action="economy.coins.remove"`，`actor=operator_id`、`target=target_user_id`、`after={"delta": amount}`。注意只在「actor != target」时记录 audit，避免把签到 / 抽奖等自家操作也淹进 WARN。

---

### 🟡 PC-2.1 — 在线检查仍是串行 `for srv in servers:` 循环（read-only 路径）

- **Severity**: 🟡 Medium（无 correctness / security 问题，纯性能放大）
- **Files**:
  - `nextbot/plugins/lottery.py:621-629` — `for srv in target_servers: ok, reason = await _check_online_cached(...)`
  - `nextbot/plugins/shop.py:740-747` — `for srv in servers: online, reason = await _check_player_online(srv, player_name)`
- **Snippet**:
  ```python
  # lottery.py:618-634
  if snap["require_online"]:
      online_servers = []
      offline_reasons: list[str] = []
      for srv in target_servers:
          ok, reason = await _check_online_cached(int(srv.id), srv, player_name)
          ...
  ```
- **Impact**:
  - 与 mutation fan-out 已统一走 `broadcast()` / `asyncio.gather` 不一致。
  - 在 N=8 服务器、单服 timeout 5s 场景下，wall time 从 5s（并行）变成 40s（串行），用户感知抽奖 / 买商品「卡死」。
  - 同 `leaderboard._fetch_one` (line 790) 已经是 gather pattern，可以照抄。
- **复现**: 配置 8 台服务器，其中 1 台超时；执行需 require_online 的「抽奖」或「购买」→ 用户等满 N×timeout。
- **修法**: 改为 `await asyncio.gather(*(_check_online_cached(int(s.id), s, name) for s in target_servers))`，结果按 server.id 排序。`_check_online_cached` 已带 per-server cache，并发 safe。

---

### ⚠️ PC-1.1 — `user_manager.py:491` ORM dirty-set 模式残留（已可控但非首选）

- **Severity**: 🟢 Info（已加 IntegrityError 兜底 + 函数级 read-then-write 全程在 session 内，不构成 lost-update；只是与项目已有的「全部走条件 UPDATE」收敛不一致）
- **Files**: `nextbot/plugins/user_manager.py:491`
- **Snippet**:
  ```python
  user.name = new_name
  try:
      session.commit()
  except IntegrityError:
      session.rollback()
      ...
  ```
- **Impact**:
  - 是项目内**唯一**剩余的「ORM attribute set + commit」模式（grep 全项目 `user\.\w+ = ` 在 plugins/ 仅此一处）。
  - 因为 `User.name` UNIQUE INDEX + IntegrityError rollback，并发时不会产生「两个用户都改成同名」。
  - 但仍然是 dirty-set 而非条件 UPDATE，与 economy / shop / lottery / dice / rob 等处的「`update(User).where(...).values(name=new_name)`」模式不一致；如果将来增加另一个并发改名路径（如 WebUI），只靠 ORM session-level dirty tracking 会让两次 read 同时看到 old_name 后两次 write，第一次成功，第二次撞 UNIQUE 然后 rollback——目前已经处理。
- **修法（非紧急）**: 改成
  ```python
  rowcount = execute_rowcount(
      session,
      update(User)
      .where(User.user_id == target_user_id, User.name == old_name)
      .values(name=new_name),
  )
  if rowcount == 0:
      ...  # raced or already renamed
  ```
  这样即可移除 IntegrityError 分支（被 WHERE 兜底）。

---

### ✅ Clean checks

- **Checklist 5 — 金币日志统一格式**: `economy.py:341-346, 491-495` / `red_packet.py:382-386` / `shop.py:674-679` / `lottery` 等均已采用 M9 格式 `金币变更：actor=... target=... action=... amount=... before=... after=... reason=...`。dice / guess_number / rob 等是 INFO 级游戏统计日志，未走 M9 格式但有独立结构，不算违规（M9 主要约束 admin / cross-user 路径）。
- **Checklist 7 — `screenshot_url` 直接调用**: grep 干净。所有 14+ handler 全部走 `render_and_send_screenshot`，仅 `screenshot_render.py` 内部一处合法封装。
- **Checklist 9 — `_to_base64_image_uri` / 内联 base64**: grep 干净。`screenshot_render.py:118` 是统一编码点。`player_query.py` 三个 map handler 直接消费 API 返回的 base64 是已识别的合理保留（`base64.b64decode(b64_string, validate=True)` for 非 V11 fallback only），未在 V11 路径重复编码。
- **Checklist 10 — IntegrityError 处理完整性**: 16 处全部 `session.rollback()` + 友好的 `reply_failure(...)` 文案，无静默吞掉的情况。
- **Checklist 11 — `MAX_COINS_AMOUNT` import 一致性**: 仅 `nextbot/plugins/economy.py:47` 定义，所有其它文件（`lottery.py:27, guess_number.py:15, warehouse.py:35, rob_protection.py:12, red_packet.py:23, shop.py:31, dice.py:15`）均 `from nextbot.plugins.economy import MAX_COINS_AMOUNT`，无重复定义。
- **Mutation fan-out**：`security.py` / `ban_core.py` / `ban.py` / `user_manager.py` 全部走 `server_broadcast.broadcast()` 或 `asyncio.gather`，无串行残留。

---

## Caveats / Not Found

1. **未审计**：`nextbot/plugins/__pycache__/` 下的 `.pyc` 与生成代码——本次只走源码 grep。
2. **未深入**：每条加币路径的实际 multiplier / 上限计算细节（PC-8.1 仅基于「raw `coins+payout` 无 cap」结构性判断，运行时 multiplier 是否能凑到越界场景需要实际 admin 配置 + 用户余额配合）。
3. **未横向比对**：dice / guess_number / rob 的「净赢累加」与 `add_coins_with_cap` helper 的接口是否兼容（helper 仅返回 `(applied_delta, capped)`，与 handler 想同时累加 `dice_total_gain` 等统计字段需拆成两条 UPDATE 才能用）——这是 PC-8.1 修法的细节，未在本审计内确认实现路径无冲突。
4. **未检查**：`server_manager` 是否有自己的 audit 入口（如旧的 `logger.warning` 模式）替代 `audit_permission_change`——只 grep 了 `from nextbot.audit import`，未排除其它命名。
