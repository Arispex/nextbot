# 主代理二次复查 — Round 4

**日期**: 2026-05-09
**复查范围**: 2 个子代理结果归并

---

## 复查方法

主代理 grep 验证 R4R-7.1（dice/guess net 计算）：
- dice.py:195 `net = payout - cost` ✅ 用未 cap payout
- dice.py:215 `dice_total_gain += net` ✅ 写入虚假累计
- dice.py:272 reply 用 `applied_net = applied_payout - cost` ✅ 用真实
- guess_number.py:230/250/292 同结构

**确认存在 stats 与真实金币变化的偏差** —— 与 R3E-1 红包蒸发同形（cap 后用了未 cap 值），上轮在 dice/guess 漏修。

---

## 真实问题（去重 + 严重度）

### 🟠 真实 high → 调整为 🟡 medium（影响仅在 stats 列，非余额安全）

| ID | 简述 |
|---|---|
| **R4R-7.1** | dice / guess 在 cap 触发后 `net = payout - cost` 用未 cap 值写入 `dice_total_gain` / `guess_total_gain`，与真实余额变化不一致 |

### 🟡 真实 medium（3 项）

| ID | 简述 | 修法 |
|---|---|---|
| **R4S-3.4** | webui_lottery + webui_warehouse 缺 `> MAX_COINS_AMOUNT` 上界 cap（webui_shop 已修）| 复用 webui_shop 模板加 cap 检查 |
| **R4R-2.1** | 6+ 处 except 缺显式 session.rollback() | 每个 except 第一行加 session.rollback() |
| **R4R-7.1** | dice/guess net 用未 cap 值写 stats（同 R3E-1 模式范围漏） | net = applied_payout - cost；写 stats 用 applied_net |

### 🟢 真实 low（4 项）

| ID | 简述 |
|---|---|
| **R4R-5.1** | _check_player_online 在 lottery / shop / warehouse 三处独立实现，lottery 漏 NFKC normalize |
| **R4R-2.2** | 47 个 commit 中 32 个 commit-time OperationalError 走外层 except 时不显式 rollback |
| **R4R-B.1** | 4 处 asyncio.gather 缺 return_exceptions=True（user_manager:134/564, leaderboard:790, 等）|
| **lottery_result.html / user_info.html** | 100 亿数字模板宽度 polish（会触发 ellipsis）|

### ℹ️ 复查通过项（多）

- 红包 refund 事务原子性 ✓
- subtract_coins_with_floor 与 add_coins_with_cap 完全对称 ✓
- lottery._charge_atomic 重写无副作用 ✓
- MAX_COINS bump 后日志 / SQLite int / 单笔 sanity bound 全 OK ✓
- shop _buy_command 在 charge 之前 cap 检查 ✓
- broadcast outcomes 全失败处理与 lottery 一致 ✓
- at_prefix 22 处迁移完整（仅 3 处合理保留）✓
- _broadcast_semaphores 共享设计意图 ✓
- 5 次 retry 无 sleep 但配合 BEGIN IMMEDIATE 安全 ✓
- 137 处 get_session 全 try/finally 关闭 ✓

---

## 主代理整体看法

**Round 4 比预期更稳**：critical = 0，high = 0，**仅 3 项 medium**。

**关键洞察**：R4R-7.1 与 R3E-1 红包蒸发同模式 —— **post-sweep M1 PC-8.1 修复（add_coins_with_cap 接入 9 site）的范围又一次漏了**：
- M1 改 9 处 +coins UPDATE 时，正确改了 reply 显示（M1.reply）
- **但漏了 dice / guess 的 stats 列**（dice_total_gain / guess_total_gain 累计派奖）
- 类似地 R3 修了 lottery / red_packet 用 applied，但 dice / guess stats 一直在用未 cap 值

**其他 medium 都是新发现**：webui 缺 cap、except 缺 rollback、asyncio.gather 异常传播。

**low 项**：100 亿显示宽度 polish / 边界微优化。

**修复优先级**：
1. 🟡 R4R-7.1（最值得修，与 R3E-1 同模式补完）
2. 🟡 R4S-3.4（webui ↔ bot 双口径一致）
3. 🟡 R4R-2.1（rollback 显式化）
4. 🟢 low 项（按需）
