# 主代理二次复查 — Round 5

**日期**: 2026-05-10

## 复查方法

主代理 grep 验证 R5-2.1/2.2/2.3（rob.py cap-stats drift）：
- rob.py:314 `rob_total_gain += amount` 但 line 339 实际入账 applied_amount ✅ 真
- rob.py:325 rollback `rob_total_loss - amount` 但 refund 实际 refund_applied ✅ 真
- rob.py:382 counter victim `rob_total_gain + amount` 但 line 372 实际 applied_amount ✅ 真

reply 已用 applied_amount（line 485 / 497-499）。**stats 与 reply 不一致**，与 R3E-1 / R4R-7.1 完全同模式。

---

## 真实问题

### 🟡 真实 medium（1 项 — cap-stats drift 第 4 家族实例）

**R5-2.1 + R5-2.2 + R5-2.3** — rob.py 3 处 stats 用未 cap 的 amount

- **位置**：
  - rob.py:314 success path `rob_total_gain += amount`（应 += applied_amount）
  - rob.py:325 rollback path `rob_total_loss - amount`（应 - refund_applied）
  - rob.py:382 counter path victim `rob_total_gain += amount`（应 += applied_amount）
- **影响**：长期触顶玩家的 rob_total_gain / rob_total_loss 累计 stats 偏高，与真实金币变化不一致
- **修复前**：reply 显示 applied / stats 写虚假 amount → 不一致
- **修复后**：stats 用 applied 真实值，与 dice/guess/red_packet 修复模式一致

### 🟢 真实 low（3 项）

| ID | 简述 |
|---|---|
| R5-B.1 | player_query.py:247-249 / 319-320 两处 asyncio.gather 仍 return_exceptions=False，inner 仅 catch TShockRequestError；read-only 路径，安全 trade-off 明确（建议补齐一致性，非必修）|
| 其他 low | 见 round5-findings.md（细节 polish）|

### ℹ️ Info（2 项 — 复查通过）

R4 修复（M1-M5）全部回归检查通过 ✓
- M1 dice/guess stats 与 user.coins 一致
- M2 11 处 rollback 全到位
- M3 fallback tuple shape 一致
- M4 lottery._normalize_player_name 跨 plugin byte-identical
- M5 模板 polish OK，其他截图模板大多已用 break-word/break-all

---

## 主代理整体看法

**Round 5 比 R4 略多 1 项 medium**，但全部是同一根因（cap-stats drift 第 4 家族）。

**4 轮 sweep 累计 cap 范围补齐路径**：
- R3E-1 红包蒸发（packet refund + claim.amount）
- R4R-7.1 dice / guess stats（dice_total_gain / guess_total_gain）
- **R5-2.1/2.2/2.3 rob stats（rob_total_gain / rob_total_loss）** ← 本次

**cap 范围补齐家族至此应已闭合**（grep 全部 *_total_gain / *_total_loss / *_total_count 涉及 cap 路径都已修过 / 或本次修）

**收敛性判断**：
- 发现量趋势：14 → 8 → 11 → 5 → 4
- critical / high 全程为 0（R5）
- R5-2.1 修完后 cap-stats drift 家族应彻底闭合
- 建议 round 6 sweep 不再做（边际收益 ≤ low 项 polish），或最后一轮做一次"零发现"确认就结束

修复优先级：
1. 🟡 R5-2.1+2.2+2.3（一次修完闭合 cap-stats 家族）
2. 🟢 R5-B.1（player_query gather 一致性，可选）
