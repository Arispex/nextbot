# Post-final-sweep 复查残留漏洞

## Goal

经过 14 批审计 + 修复（最新 final-sweep 落地 14 项 + 9 自查改进），对 `nextbot/plugins/` 下全部命令做最终复查。

**预期**：系统已高度稳固，本次审计应接近零新发现。重点：
1. **回归检查**：final-sweep 的 14 项修复（特别是 add_coins_with_cap、bot.py init_db、shop broadcast 迁移、permission_manager retry 模式、扩容的 DANGEROUS_PERMISSION_PREFIXES）有无引入新 bug
2. **遗漏复查**：14 批审计累积修复后 grep 一遍 checklist，确认无漏网模式
3. **新代码自身健壮性**：最近半月新加 / 改写的 helper / handler

## 审计范围

- 全部 23 个 plugin 文件 + 8 个共享模块
- 重点 spot-check：economy / red_packet / warehouse / shop / lottery（add_coins_with_cap 接入处）+ bot.py + permissions.py + permission_manager.py + ban.py（最近改动）

## 复查 checklist（grep 验证）

1. `add_coins_with_cap` 调用方都正确使用返回值 `(applied, capped)` 吗？
2. 所有 `update(User).where(...).values(coins=User.coins + N)` 都接入 helper 了吗？
3. `audit_permission_change` 调用 site 的 actor / before / after 完整？
4. `is_dangerous_permission` 不会误拦合法权限（如 `economy.signin` / `leaderboard.*`）？
5. `init_db()` 幂等性：所有 ensure_* 真用 IF NOT EXISTS？
6. shop `MAX_SHOP_CMD_EXECUTIONS=200` 真的拦下了 9999 × 5 server 场景？
7. POLA 层级护栏（PMB-3.1 + SS-4.1）owner 例外覆盖完整？
8. screenshot_render 修过的非 V11 fallback 文案"截图已生成"接入正确？

## 验收标准

### 审计阶段（已完成）
- [x] 2 个 trellis-research 子代理（regression + crosscut）
- [x] 主代理 grep 验证 PC-8.1 critical claim
- [x] 4 份 findings + main-agent-recheck.md

### 用户决策（2026-05-09）：D 全修

#### 🔴 必修 critical（1）

| # | ID | 修法 |
|---|---|---|
| 1 | PC-8.1 + PV-1.2 | dice / guess_number / rob 共 9 处加币 UPDATE 接入 add_coins_with_cap helper |

#### 🟠 必修 high（2）

| # | ID | 修法 |
|---|---|---|
| 2 | PC-3.1 | user_manager.py:100/153/164 三处 whitelist URL 加 quote(safe="") |
| 3 | PC-4.1 | _safe_at_segment 提升到 text_utils.at_prefix 内部，所有 handler 自动受益 |

#### 🟡 必修 medium（3）

| # | ID | 修法 |
|---|---|---|
| 4 | PC-6.1 | server_manager 添加/删除服务器 + economy admin 加/减金币 加 audit_permission_change |
| 5 | PC-2.1 | lottery._check_player_online (line 621) + shop (line 740) 玩家在线检查改 asyncio.gather 并行 |
| 6 | PV-X.1 | lottery._charge_atomic partial UPDATE 检 rowcount，rowcount=0 时回退 applied=0 |

#### 🟢 必修 low（2）

| # | ID | 修法 |
|---|---|---|
| 7 | PC-1.1 | user_manager.py:491 user.name = new_name 改条件 UPDATE（rowcount 检测）|
| 8 | PV-8.1 | screenshot_render.py:57 docstring "截图生成成功" 改 "截图已生成" |

### 实施阶段验收

- [ ] 8 个修复全部落地
- [ ] **无破坏性更新**：V11 行为兼容
- [ ] **开箱即用**：本批次纯逻辑改动，无 schema 变化
- [ ] **失败文案符合规范**
- [ ] **修后再检查**：派 trellis-check 子代理对照 4 份 findings + recheck 再走一遍

## Out of Scope

- WebUI（独立任务）
- 已确认 acceptable trade-off 的项目（如 semaphore_for 永不释放、context dict 大小限制等）
- 重复前序 14 批已修内容

## Technical Notes

预期发现：≤ 5 项 medium / low / info；critical / high 应为 0。如果出现 critical，说明 final-sweep 修复有回归。
