# 主代理二次复查 — Round 3 (post-sweep 后第二次复查)

**日期**: 2026-05-09
**复查范围**: 2 个子代理结果（edge-case + new-code review）

## 复查方法

主代理读 `red_packet.py:300-350` 验证 R3E-1 high claim：✅ 真。

---

## 真实问题（去重 + 严重度调整）

### 🟠 真实 high（1 项）

**R3E-1** — 红包抢取触顶 cap 时金币凭空蒸发
- **位置**：`red_packet.py:325 + 333 + 349`
- **现状**：
  - line 325 `_claim_slot_atomic(session, packet_id, draw_amount)` 扣 packet `remaining_amount` 全额
  - line 333 `RedPacketClaim(amount=draw_amount)` 记账全额
  - line 349 `add_coins_with_cap(session, user_id, draw_amount) -> (applied_amount, capped)` 只入账 `applied`
  - **差额 `(draw_amount - applied_amount)` 凭空蒸发** —— packet 已扣全额、user 没收全额、claim audit 数字对不上
- **影响**：用户余额接近 1 亿时抢红包，packet 显示发了 N 元出去，但 user 收到 < N，差额无去向。审计回查时 RedPacketClaim 与 User.coins 变化不一致。
- **修法**：参考 `economy.transfer` 的 sender refund 模式（line 469-482），把 `draw_amount - applied_amount` 退回 packet（增加 `remaining_amount` + `remaining_count`），同时把 `RedPacketClaim.amount` 改成 `applied_amount`，并给用户 ⚠️ 触顶提示

### 🟡 真实 medium（5 项）

| ID | 简述 | 修法 |
|---|---|---|
| **R3E-2** | ban 全失败缺 CRITICAL log + reply head 切换（与 shop S-2.1 / lottery LO-3.3 模式不一致）| 加 logger.critical + reply_failure 全失败时 |
| **R3E-3 + R3N-3.2** | lottery `_charge_atomic` 自实现 partial cap 与 helper 不一致 | 加对偶 helper `subtract_coins_with_floor`（economy.py 下），让 lottery 复用 |
| **R3N-1.3** | `add_coins_with_cap` delta<0 silent return | 加 logger.warning("add_coins_with_cap 收到负 delta") + return (0, False) |
| **R3N-4.2** | lottery / shop asyncio.gather 缺 `return_exceptions=True` | 加参数防止任一失败 cancel 其他任务，或用 server_broadcast.broadcast 的 wrap 模式 |
| **R3N-5.2** | server_manager 失败路径（validation / IntegrityError / not_found）缺 denied audit | 与 ban / permission_manager 模式对齐 |

### 🟢 真实 low（多项）

- 空 at + " " 拼接前导空格（仅非数字 user_id 触发）
- lottery 负向 cap 警告无条件 fire（applied=0 时也提示）
- ensure_lottery_schema no-op 但开了 connection（误导 maintainer）
- success_caption OBV11 路径不发文字 / fallback 发文字 不对称

---

## 复查通过（grep / 阅读验证 OK）

- `add_coins_with_cap` delta=0 真 no-op DB；多次调用同一 user_id 安全；BEGIN IMMEDIATE 下不死锁
- `safe_at_segment_or_empty` 22 处 callsite 全 work；text_utils 无循环 import
- gather ordering preserved；`(bool|None, str)` 三态正确处理
- 17 处 `ensure_*_schema` 全 idempotent；启动顺序正确
- `DANGEROUS_PERMISSION_PREFIXES` 13 个通配 case 全 pass
- `audit_permission_change` 字段格式一致
- screenshot_render semaphore 失败路径释放正确
- bot 自身退群走 not_found 安全分支
- DB-API 双写 CRITICAL：shop / warehouse / lottery 通过；**ban 缺**（已记 R3E-2）
- conditional UPDATE 5 retry 失败 + session 状态正确

---

## 主代理整体看法

**第 16 批比预期发现稍多**：1 high + 5 medium 真实存在。

**关键发现**：R3E-1 红包蒸发是 post-sweep M1 (PC-8.1) 修复的**直接副作用**——M1 把所有 +coins UPDATE 改成 add_coins_with_cap 拿到 (applied, capped)，但 caller 端没正确处理 capped=True 时的"已扣对方 / 已记账 / 但只入了部分到 user"场景。这与 dice/guess/rob 不同（小游戏 payout 是凭空生成的，不需要从源头退）。**红包是从源头取，必须退**。

修复优先级：
1. 🟠 R3E-1（必修，金币蒸发）
2. 🟡 R3E-2（建议修，ban CRITICAL log 一致性）
3. 🟡 R3E-3 + R3N-3.2（架构性建议：抽 subtract_coins_with_floor helper）
4. 🟡 R3N-1.3 / R3N-4.2 / R3N-5.2（一致性 + 防御）
5. 🟢 low 项（cosmetic）
