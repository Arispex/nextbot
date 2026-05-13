# Round 6 复查

## Goal

第 19 批审计。前 5 轮 sweep 发现量趋势：14 → 8 → 11 → 5 → 4。R5 已闭合 cap-stats drift 家族。

本轮目标：
1. R5 4 项修复有无回归
2. 前 5 轮可能仍漏的极端边角
3. **趋向 0 发现的确认轮** — 如果 R6 真的 0 critical/0 high/≤1 medium，可宣布 plugins 审计完全收敛

## 审计方法

派 1 个 trellis-research 子代理：
1. R5 修复回归（rob.py / screenshot_render.py / red_packet_all.html / player_query.py 4 文件）
2. 全项目 grep 找最后漏网模式
3. 整体收敛性判断

## 验收标准

- [ ] 1 trellis-research 子代理
- [ ] 主代理对任何 high+ 项二次复查
- [ ] 结果汇总到 `research/round6-findings.md` + `research/main-agent-recheck.md`

## Out of Scope

- WebUI（已纳入下游统一任务）
- 已确认 acceptable trade-off
- 下游已记录任务（audit_economy_change / DB CHECK / cooldown）

## Technical Notes

预期发现：critical=0, high=0, medium ≤ 1。如完全 0 发现，建议正式结束 sweep。
