# Round 5 复查

## Goal

第 18 批审计。前 4 轮 sweep 发现量递减：14 → 8 → 11 → 5。R4 已达 0 critical / 0 high。

本轮目标：
1. **验证收敛**：R4 5 项修复有无引入回归
2. **最后边角**：经历 4 轮 sweep 仍可能漏的极端 case
3. **跨 round 累积模式**：cap 范围补齐已 3 次（R3E-1 红包 → R4R-7.1 dice/guess stats），还有第 4 处吗？

## 审计方法

派 1 个 sub-agent（不是 2-4 个，因为预期发现量低），focus：
1. R4 修复的副作用
2. 全项目 grep MAX_COINS / applied_ / cap 模式找最后漏网
3. 命令路径之外的潜在问题

## 验收标准

### 审计阶段（已完成）
- [x] 1 trellis-research 子代理（含 R4 回归 + cap drift + 极端 case + 跨命令攻击 + 文档一致性）
- [x] 主代理 grep 验证 R5-2.1/2.2/2.3
- [x] findings + main-agent-recheck.md

### 用户决策（2026-05-10）：全修

| # | ID | 修法 |
|---|---|---|
| 1 | R5-2.1+2.2+2.3 | rob.py 3 site stats 用 applied_amount / refund_applied 真实值（cap-stats 家族第 4 处闭合）|
| 2 | R5-3.1 | screenshot_render.py 在 file_size <= 0 时早返回 reply_failure("截图为空") |
| 3 | R5-5.0 | red_packet_all.html .stat-value 加 word-break: break-all（对齐 R4 M5 模板）|
| 4 | R5-B.1 | player_query.py:247-249 (handle_online) + 319-320 (handle_self_kick) gather 加 return_exceptions=True |

**跳过**：R5-info.1（lottery 缺 stats 列与 dice/guess 不对称 — 设计取舍）

### 实施阶段验收

- [ ] 4 模块落地
- [ ] V11 行为兼容
- [ ] cap-stats drift 家族闭合
- [ ] 修后再检查

## Out of Scope

- WebUI（用户已明确独立任务统一搞）
- 已确认 acceptable trade-off
- audit_economy_change / DB CHECK / cooldown 等下游任务

## Technical Notes

预期发现：critical=0, high=0, medium ≤ 2。如有 critical/high 说明 R4 修复有回归。
