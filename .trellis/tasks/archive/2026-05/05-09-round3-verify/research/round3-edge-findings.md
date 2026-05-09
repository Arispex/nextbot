# Round 3 Edge Sweep — 失败路径 / Corner Case 审计

- **Batch**: 第 16 批 sweep（专挑 corner case，前 15 批已覆盖 happy path）
- **Date**: 2026-05-09
- **Scope**: failure paths / partial-success / 并发与异常组合 / 启动 / 被动事件
- **Method**: read source + cross-check 已修过的几个核心 helper（`add_coins_with_cap` / `audit_permission_change` / `safe_at_segment_or_empty` / `server_broadcast` / `screenshot_render`）

---

## 总览

- 🔴 Critical: **0**
- 🟠 High: **1**（R3E-1 红包 partial cap 金币丢失）
- 🟡 Medium: **2**（R3E-2 ban 全失败无 CRITICAL 与 reply 切换；R3E-3 lottery 自实现 cap 与 helper 偏差）
- 🟢 Low: **3**（cosmetic / 防御性）
- ℹ️ Info: **4**

---

## Issue 列表

### 🟠 R3E-1 红包抢取 partial cap 时金币凭空蒸发

- **Severity**: 🟠 High
- **File**:
  - `nextbot/plugins/red_packet.py:325` (`_claim_slot_atomic`)
  - `nextbot/plugins/red_packet.py:349` (`add_coins_with_cap`)
- **Snippet**:

  ```python
  # red_packet.py:325
  if not _claim_slot_atomic(session, packet_id, draw_amount):
      ...
      return
  # ...
  applied_amount, capped = add_coins_with_cap(session, user_id, draw_amount)
  actual_grab_amount = applied_amount
  coin_capped = capped and applied_amount < draw_amount
  ```

  其中 `_claim_slot_atomic` 直接 `RedPacket.remaining_amount - draw_amount` 扣除 packet 池子，但 `add_coins_with_cap` 触顶时 `applied_amount < draw_amount`，差额既未退还 packet 也未退还 user。

- **Impact**: 当抢到红包的用户余额已接近 `MAX_COINS_AMOUNT (1e8)` 时，红包池子被减掉 `draw_amount`，但用户只入账 `applied_amount`，差额 `(draw_amount - applied_amount)` 直接消失。`RedPacketClaim.amount` 也仍然写的是 `draw_amount`，事后审计与展示都对不上。
- **复现**:
  1. 把某用户余额刷到 `MAX_COINS_AMOUNT - 1`。
  2. 该用户抢一个会让其触顶的红包（draw_amount=1000），实际只入账 1。
  3. `RedPacket.remaining_amount` 被扣 1000 而不是 1，差额 999 丢失。
- **修法（建议）**：partial cap 时把差额二次条件 UPDATE 退回 `RedPacket.remaining_amount` 与 `remaining_count`（注意需 `_claim_slot_atomic` 之外的补偿语义），或在 `_claim_slot_atomic` 之前 SELECT-then-cap，确认实际可入账后再走 UPDATE。和 `economy.transfer` 的 sender refund 模式（`economy.py:469-482`）保持一致。

---

### 🟡 R3E-2 ban handler DB 成功 + 全部 TShock 同步失败时无 CRITICAL log，也未切 reply head

- **Severity**: 🟡 Medium
- **File**:
  - `nextbot/plugins/ban.py:119-133`（手动封禁路径）
  - `nextbot/plugins/group_member_notify.py:216-238`（自动退群封禁路径）
- **Snippet**:

  ```python
  # ban.py:119-133
  outcomes = await sync_user_to_blacklist(result.user_name, reason)
  lines: list[str] = [
      reply_success("封禁"),  # ← 即使全部 TShock 都失败，head 仍是 ✅
      ...
  ]
  lines.extend(format_blacklist_add_lines(outcomes))
  success_count = sum(1 for o in outcomes if o.ok)
  logger.info(  # ← 仅 INFO，无 CRITICAL
      f"封禁用户黑名单同步完成：... success={success_count}/{len(outcomes)}"
  )
  ```

- **Impact**:
  - 与 `shop._buy_command` 的 S-2.1 / S-2.2（`shop.py:854-867`）和 `lottery` 的 LO-3.3（`lottery.py:948-993`）模式不一致：那两个 handler 在 DB 已写 + 全部下游失败时切 `reply_failure` 头部 + `logger.error("[CRITICAL] ...")`，方便 grep 报警。
  - ban 的 DB 行已是 `is_banned=True`，但下游 TShock blacklist 全空，等于"DB 显示封禁但游戏内仍能上线"。哪怕项目设计依赖"服务端反向同步"作为兜底（webhook on player join），仍然会有数十秒到数分钟的 race window，且操作员从 INFO 日志中很难发现。
  - 自动退群封禁链路（`group_member_notify.py:216-238`）问题相同。
- **复现**:
  1. 注册一个 user。
  2. 关掉所有 TShock 服务器（让 `request_server_api` 全部 `TShockRequestError`）。
  3. `/封禁用户 <qq> <reason>` → 看到 `✅ 封禁成功`，per-server 行 `❌ 添加失败，无法连接服务器`，但日志只有 INFO。
- **修法（建议）**：
  - 当 `success_count == 0 and len(outcomes) > 0` 时切 `reply_failure("封禁", "DB 已写但所有服务器同步失败")` head + 一行明显警告。
  - 同步 emit `logger.error("[CRITICAL] 封禁 DB 已写但所有服务器同步失败：...")`，让 grep "CRITICAL" 报警一致。
  - 对 `sync_user_blacklist_remove` (unban) 路径同样处理。

---

### 🟡 R3E-3 lottery `_charge_atomic` 与 `add_coins_with_cap` 模式偏差

- **Severity**: 🟡 Medium（一致性 / DRY；不是当下 bug）
- **File**: `nextbot/plugins/lottery.py:759-845`
- **Snippet**:

  ```python
  # lottery.py:759-804
  if coin_delta_pos > 0:
      capped_pos = min(coin_delta_pos, MAX_COINS_AMOUNT)  # ← 先把 delta 自身 cap 掉
      pos_rowcount = execute_rowcount(
          session_local,
          update(User)
          .where(User.user_id == user_id, User.coins + capped_pos <= MAX_COINS_AMOUNT)
          .values(coins=User.coins + capped_pos),
      )
      if pos_rowcount > 0:
          applied_pos = capped_pos
      else:
          # 重新做 partial cap（与 add_coins_with_cap 几乎完全重复的代码）
          ...
  ```

- **Impact**:
  - lottery 自己实现一份与 `economy.add_coins_with_cap` 几乎相同的 partial-cap 逻辑（含正向 + 负向两个分支），但**没有用 helper**。
  - 偏差 1：lottery 多了一个 `capped_pos = min(delta, MAX_COINS_AMOUNT)` 上层 cap，超过 cap 的 delta 被静默截断（即理论 prize delta > 1e8 时无 warning log）。
  - 偏差 2：`add_coins_with_cap` 在 partial UPDATE 触发时记 `WARN`，lottery 自己实现的也记 WARN，但格式不一致（`f"抽奖正向 coin 奖励..."` vs helper 的 `f"金币加币部分被 cap..."`），日志聚合时无法按统一 key 统计。
  - 后续若改 helper（如收紧 cap 行为），lottery 不会自动同步。
- **复现**: 改 helper 时若忘了同步 lottery，cap 语义会发生漂移。
- **修法（建议）**：把 lottery 的正向 cap 切成 `add_coins_with_cap` 调用；负向（`coin_delta_neg < 0`）部分 helper 暂未覆盖，可以新增 `subtract_coins_with_floor(session, user_id, amount)` 对偶 helper，再让 lottery 复用。

---

### 🟢 R3E-4 `safe_at_segment_or_empty` 在 V11 永远不会触发 logger.warning

- **Severity**: 🟢 Low（防御代码冗余，非 bug）
- **File**: `nextbot/text_utils.py:99-103`
- **Snippet**:

  ```python
  try:
      return OBV11MessageSegment.at(int(user_id))
  except (TypeError, ValueError):
      logger.warning(f"无法将 user_id 解析为整数 @ 段：user_id={user_id}")
      return None
  ```

- **Impact**:
  - 项目目前是 OBV11-only（`event_preprocessor` 在 `bot.py:101-133` 也只对 V11 message 做过滤），V11 协议保证 `event.get_user_id()` 返回纯数字字符串，`int()` 永远成功。
  - 因此 `logger.warning` 从未被触发，相当于死代码。但保留作为 defensive 代码也合理（如果未来接入 Telegram bridge / 自研适配器 push 非数字 user_id），所以不建议删，标 ℹ️ 即可。
  - **清单中关心"是否会被频繁触发污染日志"** → 在 V11 下永远 0 频次，不污染。

---

### 🟢 R3E-5 `safe_at_segment_or_empty` 返回空 text 段 + 拼接产生前导空格

- **Severity**: 🟢 Low（cosmetic）
- **File**: `nextbot/text_utils.py:106-119` + 全部 `at + " " + reply_failure(...)` 的 17+ 处 callsite
- **Snippet**:

  ```python
  def safe_at_segment_or_empty(user_id: str) -> "OBV11MessageSegment":
      seg = safe_at_segment(user_id)
      if seg is None:
          return OBV11MessageSegment.text("")
      return seg
  ```

  对应 callsite 模式：`at + " " + reply_failure("...", "...")` → 当 at 是 `text("")` 时，整条消息会被序列化为 `[text(""), text(" "), text("❌ ...")]`，OBV11 序列化后渲染为 `" ❌ ..."`（前导空格）。

- **Impact**: 仅在 `safe_at_segment` 拿到非数字 user_id 时才发生（V11 下永远拿到数字，所以实际触发频率为 0）。如果未来接入非 V11 适配器，会有一个前导空格的视觉异常。
- **修法（可选）**：要么保持现状（V11 下不触发），要么把 helper 改成 None-safe 的 `at_then(seg, sep, content)` 函数，在 None 时直接返回 `content` 不拼空格——但这就和现存 `at_prefix` 行为重叠。建议保持现状，仅 ℹ️ 记录。

---

### 🟢 R3E-6 `safe_at_segment_or_empty` 对非 V11 适配器返回 V11 类型

- **Severity**: 🟢 Low
- **File**: `nextbot/text_utils.py:114-119`
- **Snippet**:

  ```python
  from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment
  seg = safe_at_segment(user_id)
  if seg is None:
      return OBV11MessageSegment.text("")  # ← 即使 bot 是 Console / Telegram，也强制返回 V11 段
  return seg
  ```

- **Impact**: 项目目前 V11-only，不影响。未来如接入非 V11 adapter，`bot.send(event, OBV11MessageSegment.text("") + " " + text)` 会因 adapter 不识别 OBV11 段类型而 raise。已被现有代码以 PC-4.1 注释承认。
- **修法**: 暂无需要（V11-only），ℹ️ 备查。

---

### ℹ️ R3E-7 `add_coins_with_cap` 对 delta=0 / 负数静默返回，调用方需自行验证

- **Severity**: ℹ️ Info
- **File**: `nextbot/plugins/economy.py:80-81`

  ```python
  if delta <= 0:
      return 0, False
  ```

- **Verification**: 全部 callsite 对 delta 都已上游过滤（`recycle_qty * unit_value * ratio` 受 `recycle_qty>0`、`unit_value>0`、`ratio>0` 保护；红包 `draw_amount = max(1, _draw_lucky(...))` 保底 1；rob `amount = max(1, ...)` 保底 1；guess_number / dice 仅在 `payout > 0` 时调用）。
- **Conclusion**: 复查通过——没有调用方传入 0 或负数到这里。

---

### ℹ️ R3E-8 `server_broadcast.broadcast` 异常路径

- **Severity**: ℹ️ Info（复查通过）
- **File**: `nextbot/server_broadcast.py:39-69`
- **Items checked**:
  1. **fn 抛 BaseException / KeyboardInterrupt / SystemExit**：`except Exception:` 只捕获 Exception 子类。`KeyboardInterrupt` / `SystemExit` 不被吞，会向上抛出，`asyncio.gather(..., return_exceptions=False)` 会让整个 gather 抛 `KeyboardInterrupt`。这是合理行为：用户终止 / 进程关闭时不应静默继续。
  2. **aggregate(空 list)**：`(0, 0)` ✓ 正常。
  3. **同 server 被两个 broadcast 实例并发调用**：`_broadcast_semaphores[server_id]` 是模块级 dict，两次 broadcast 共享同一信号量（`max_concurrent_per_server=1`），所以同 server 上的两个 fn 仍然串行——这就是 SF-X 设计意图。`semaphore_for` 在 dict 创建瞬间没有 `await`，asyncio 单线程下原子。
- **Conclusion**: 复查通过，无 corner case 缺陷。

---

### ℹ️ R3E-9 `audit_permission_change` 异常路径

- **Severity**: ℹ️ Info（复查通过）
- **File**: `nextbot/audit.py:19-50`
- **Items checked**:
  1. **logger 不可用**：`from nonebot.log import logger`。在 nonebot 启动后 logger 永远可用；启动 hook 内调用也安全。
  2. **特殊字符**：`f"{before!r}"` 用 `repr` 序列化，对任何 Python 对象都安全（不会因为 token 含 \n/ 引号 raise）。
  3. **before/after 含敏感数据**：检查所有 callsite—— `server_manager.py:104-114`/`178-188` 主动排除 token，仅写 `name/ip/game_port/restapi_port`；`user_manager.py:540-546` 仅 `name`；`ban.py:110-117` 仅 `is_banned/ban_reason`；`permission_manager.py` / `group_manager.py` 仅 `permissions/inherits` CSV；`economy.py:608-619` 仅 `coins`。无 token / password 泄漏面。
- **Conclusion**: 复查通过。Audit 是 logger.warning 级别，写入主日志流，操作员可见——这是 by-design 的安全审计；如果未来要把 audit log 单独路由，需要改 audit.py。

---

### ℹ️ R3E-10 conditional UPDATE retry 模式 + session 状态

- **Severity**: ℹ️ Info（复查通过）
- **Files**:
  - `nextbot/plugins/permission_manager.py:269/410/566/803/984`
  - `nextbot/plugins/group_manager.py:357/513/602/719/824`
- **Pattern verified**:

  ```python
  for _ in range(_CSV_UPDATE_RETRY):  # =5
      rowcount = execute_rowcount(session, update(...))
      if rowcount == 1:
          session.commit()
          break
      session.rollback()  # ← 关键：retry 前必须 rollback
      current = session.query(...)  # 重新读取
      ...
  else:
      logger.warning(f"...重试耗尽 actor={operator_id}")
      await matcher.finish(... reply_failure("...", "并发冲突，请稍后重试"))
  ```

- **Items checked**:
  1. **5 次 retry 全失败**：`for/else` 触发 `else` 分支，发 `reply_failure("...", "并发冲突，请稍后重试")` —— 文案清晰，告诉用户重试。同时记 `WARN` 日志含 actor / retry 次数 / target。
  2. **session 状态**：每次 rowcount=0 之后都 `session.rollback()` 再重新查 ORM，避免 ORM identity-map 缓存 stale 行污染下一次 `where(... == old_csv)`。pattern 一致。
  3. **覆盖率**：所有 `_CSV_UPDATE_RETRY` 用法都遵循同一 pattern；`group_manager.py:357` cascade scrub 子组继承时虽然在 BEGIN IMMEDIATE 下几乎不会触发，但 retry-exhausted 时只记 WARN 不阻塞父组删除（comment 解释为合理设计）。
- **Conclusion**: 复查通过，无 corner case 缺陷。

---

## 各 checklist 项目状态汇总

| # | Checklist | 结论 |
|---|---|---|
| 1 | `add_coins_with_cap` 失败路径（delta=0/负 / cap fallback / 文案） | 复查通过；R3E-7 备查；reply 文案与 logger 字段在 cap 分支均完整。 |
| 2 | `audit_permission_change` 异常路径 / 敏感数据泄漏 | 复查通过；R3E-9 详。 |
| 3 | `safe_at_segment_or_empty` 二阶效应 | 复查通过；R3E-4 / R3E-5 / R3E-6 备查（V11 下不触发）。 |
| 4 | `server_broadcast.broadcast` 异常 | 复查通过；R3E-8 详。 |
| 5 | conditional UPDATE 重试模式 | 复查通过；R3E-10 详。 |
| 6 | `screenshot_render` 失败路径 | 复查通过；`temp_screenshot_path` finally 清理 + `async with semaphore` 保证释放；`screenshot_url` 已把所有非 RenderScreenshotError 包装。 |
| 7 | 群事件 / 被动 handler | bot 自身退群（`event.user_id == self_id`）→ `apply_ban_to_db` 走 `not_found` 分支安全跳过；rule 已用 `Rule(_is_decrease)` 显式过滤；`isinstance` defensive guard 双保险（MI-5.4）。复查通过。 |
| 8 | startup hook | `init_db` 失败 → 异常向上 → nonebot 启动崩溃（fail-loud）；`ensure_default_groups` 仅在缺失时 seed，不覆盖已修改的 guest 权限；都符合预期。 |
| 9 | DB-API 双写边界（ban / shop / warehouse） | shop / warehouse / lottery 都有 CRITICAL log + reply 切换；**ban 没有**——见 R3E-2。 |
| 10 | `lottery._charge_atomic + add_coins_with_cap` 共存 | 见 R3E-3。 |

---

## 建议优先级

1. 🟠 **R3E-1**：红包 partial cap 金币丢失，Round 4 应优先修。
2. 🟡 **R3E-2**：ban 全失败缺 CRITICAL + reply 切换，与其它 DB-API 双写域对称化。
3. 🟡 **R3E-3**：lottery 自实现 cap → 切到 `add_coins_with_cap` helper（顺带补 `subtract_coins_with_floor` 对偶）。
4. ℹ️ R3E-4 / R3E-5 / R3E-6：备查。

---

## Files Inspected

- `nextbot/plugins/economy.py` — `add_coins_with_cap` 定义
- `nextbot/plugins/red_packet.py` — 红包 grab / withdraw partial cap
- `nextbot/plugins/lottery.py` — `_charge_atomic` 自实现 cap
- `nextbot/plugins/shop.py` — DB-API 双写 CRITICAL pattern 对照
- `nextbot/plugins/warehouse.py` — 回收 partial cap + claim CRITICAL
- `nextbot/plugins/ban.py` + `nextbot/ban_core.py` — ban DB-API 双写路径
- `nextbot/plugins/group_member_notify.py` — 退群事件被动 handler
- `nextbot/plugins/guess_number.py` / `dice.py` / `rob.py` / `rob_protection.py` — 小游戏 cap path
- `nextbot/plugins/permission_manager.py` / `group_manager.py` — conditional UPDATE retry
- `nextbot/server_broadcast.py` + `nextbot/large_image.py` — broadcast / 信号量
- `nextbot/screenshot_render.py` + `nextbot/screenshot_temp.py` + `server/screenshot.py` — 截图链
- `nextbot/audit.py` — audit helper
- `nextbot/text_utils.py` — `safe_at_segment_or_empty` 等
- `nextbot/db.py` — `init_db` / `ensure_default_groups` / `add_coins_with_cap` 上游 schema
- `bot.py` — startup hook
- `nextbot/plugins/security.py` / `server_send.py` — 失败 head / 三态分支模板（用于对照）
