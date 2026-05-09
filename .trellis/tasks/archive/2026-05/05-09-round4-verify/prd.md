# Round 4 复查

## Goal

第 17 批审计。前 3 轮 sweep 每次都发现真实漏洞：
- Round 1 (final-sweep)：14 修
- Round 2 (post-sweep)：8 修（含 critical 9 site 范围补齐）
- Round 3 (round3-verify)：1 high (R3E-1 红包蒸发) + 5 medium

本轮重点：
1. **Round 3 修复副作用**：新加 R3E-1 refund 逻辑、subtract_coins_with_floor helper、lottery._charge_atomic 重写、MAX_COINS 100 倍 bump 是否引入新 bug
2. **MAX_COINS bump 二阶影响**：100 亿后 schema / 显示文案 / log / sanity bound 是否处处一致
3. **3 轮 sweep 漏网的边角问题**

## 审计方法

- 重点 review last 3 commits（round-3 / post-sweep / final-sweep）的代码
- grep 验证 100_000_000 / 100亿 / 1亿 一致性
- 分析新 helper 在新 cap 下的行为

## 验收标准

### 审计阶段（已完成）
- [x] 2 trellis-research 子代理（副作用 + 残留）
- [x] 主代理 grep 验证 R4R-7.1
- [x] 2 份 findings + main-agent-recheck.md

### 用户决策（2026-05-09）：命令侧全修 + WebUI 跳过

#### 用户决定不修
- **R4S-3.4 WebUI 缺 cap**：user 明确 "webui 先别管后面统一搞"，推迟到独立 WebUI 同步任务

#### 用户决定修

| # | ID | 修法 |
|---|---|---|
| 1 | R4R-7.1 🟡 | dice / guess `net = payout - cost` 改 `net = applied_payout - cost`，stats 列写真实 applied_net |
| 2 | R4R-2.1 + R4R-2.2 🟡 | 6+ 处 `except Exception:` 第一行加 `session.rollback()`，所有 commit-time 错误路径显式 rollback |
| 3 | R4R-B.1 🟢 | user_manager.py:134/564 + leaderboard.py:790 等 4 处 asyncio.gather 加 `return_exceptions=True` |
| 4 | R4R-5.1 🟢 | lottery._check_player_online 加 NFKC normalize（与 shop / warehouse 一致） |
| 5 | polish 🟢 | lottery_result.html / user_info.html `.stat-value` 加 `overflow-wrap: break-word` 防 100 亿数字 ellipsis |

### 实施阶段验收

- [ ] 5 项修复全部落地
- [ ] **无破坏性更新**：V11 行为兼容
- [ ] **失败文案符合规范**
- [ ] **修后再检查**：派 trellis-check

## Out of Scope

- 已确认 acceptable trade-off 项
- WebUI / 已记入下游任务的项
- audit_economy_change helper 设计

## Technical Notes

预期发现：递减但非零。如果 critical = 0 + high ≤ 1，可视为系统收敛。
