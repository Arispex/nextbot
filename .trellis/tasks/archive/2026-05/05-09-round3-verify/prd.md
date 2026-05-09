# 第三轮全量复查残留漏洞

## Goal

经 15 批审计后做第三轮 full sweep。前两轮（final-sweep / post-sweep）都发现了真实问题：
- Round 14：14 修（含 1 critical bot.py 升级路径）
- Round 15：8 修（含 1 critical add_coins_with_cap 范围遗漏 9 site）

本轮聚焦**前两轮可能漏掉的死角**：
1. **失败路径 / 异常路径**：成功路径被审完了，失败路径有无资源泄漏 / 状态不一致
2. **新 helper 之间的交互**：safe_at_segment_or_empty / add_coins_with_cap / audit_permission_change 互相调用时的边界
3. **被动事件 / 定时任务**：group_member_notify / 任何 startup hook
4. **测试盲区**：handler 内 if/elif/else 不常走的分支
5. **新加代码的二阶效应**：M3 范围扩展把 22 处迁了 at_prefix，是否影响 V11 行为

## 审计方法

- 重点 **非快乐路径** + **新加代码本身**
- 不再逐条 grep 旧 checklist（前两轮已扫过）
- 关注 last 30 天有变更的文件

## 验收标准

### 审计阶段（已完成）
- [x] 2 子代理（edge + new-code）+ 主代理 grep 验证 R3E-1
- [x] 2 份 findings + main-agent-recheck.md

### 用户决策（2026-05-09）：D 全修 + 跳过 R3E-2 + MAX_COINS 改 100 亿

#### 用户决定不修
- **R3E-2** ban 全失败 CRITICAL log：服务端反向同步机制兜底，不修

#### 用户决定修

| # | ID | 修法 |
|---|---|---|
| 1 | **MAX_COINS_AMOUNT 调整** | `economy.py:48` 从 `100_000_000` (1 亿) 改为 `10_000_000_000` (100 亿)。验证下游所有引用（cap 防御逻辑保留，仅放宽上限值） |
| 2 | R3E-1 🟠 | 红包抢取触顶 cap 时退回 packet `(remaining_amount + diff, remaining_count + 1)` + `RedPacketClaim.amount = applied` + ⚠️ 提示，参考 economy.transfer sender refund 模式 |
| 3 | R3E-3 + R3N-3.2 🟡 | 抽对偶 helper `subtract_coins_with_floor(session, user_id, delta) -> (applied, floored)` 在 economy.py，让 lottery._charge_atomic 复用替换自实现 |
| 4 | R3N-1.3 🟡 | `add_coins_with_cap` delta<0 时 `logger.warning("收到负 delta")` + 仍 return (0, False) |
| 5 | R3N-4.2 🟡 | lottery / shop asyncio.gather 在线检查加 `return_exceptions=True` 防止单 task 异常 cancel 其他 |
| 6 | R3N-5.2 🟡 | server_manager 失败路径（validation / IntegrityError / not_found）加 `audit_permission_change(action="server.add.denied" / "server.delete.denied")` |
| 7 | low: 空 at 前导空格 | `text_utils.at_prefix` 在 safe_at_segment 返回空时不加 sep |
| 8 | low: lottery 负向 cap 警告无条件 fire | 仅 applied < requested 时 fire |
| 9 | low: `ensure_lottery_schema` no-op | 加注释或删除空函数 |
| 10 | low: success_caption OBV11/fallback 不对称 | 统一行为（V11 也不发文字 caption，与 fallback 一致；或 fallback 也不发） |

### 实施阶段验收

- [ ] 10 项修复 + MAX_COINS 调整全部落地
- [ ] **无破坏性更新**：MAX_COINS 调整后所有现有 cap 防御逻辑仍生效（不撤销 cap，只放宽值）
- [ ] **失败文案符合规范**
- [ ] **修后再检查**：派 trellis-check 子代理

## Out of Scope

- 重复前两轮已确认 acceptable trade-off
- WebUI
- 被列入下游任务的项（audit_economy_change / DB CHECK / cooldown 等）

## Technical Notes

预期发现：≤ 3 项。如发现 critical 说明 post-sweep 修复有遗漏 / 新引入。
