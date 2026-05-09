# 审计剩余 plugins 文件的漏洞和性能问题

## Goal

完成 `nextbot/plugins/` 下所有未被前 11 次审计覆盖的文件的系统化漏洞 / 性能审计。前序已审：
- 用户系统 (user_manager.py)、经济系统 (economy.py)、小游戏 (dice.py + guess_number.py + rob.py)
- 红包 (red_packet.py)、仓库 (warehouse.py)、商店 (shop.py)
- 服务器工具 + 管理 (server_tools.py + server_send.py + server_manager.py)
- 玩家查询 (player_query.py)、安全管理 (security.py + ban.py)、权限管理 (group_manager.py + permission_manager.py)

## 审计范围

| 分类 | 文件 | 行数 | 备注 |
|---|---|---|---|
| 抽奖系统 | `lottery.py` | 733 | 奖池抽奖，含 transactional 经济操作 |
| 排行榜 | `leaderboard.py` | 1743 | 最大文件，多榜单查询 + 截图渲染 |
| 系统功能 | `about.py` / `menu.py` / `tutorial.py` | 445 | 简单查询 / 帮助命令 |
| 被动事件 | `rob_protection.py` | 150 | 抢劫保护开关 |
| 群成员通知 | `group_member_notify.py` | 212 | 群成员加退事件，可能触发 ban_core |

## 审计关注维度（沿用前序 audit checklist）

1. **并发 / 竞态**：lost-update on User/Group fields，TOCTOU
2. **注入 / 越权**：URL path segment（quote(safe="") 应用情况）、命令参数注入
3. **资源 / 性能**：截图渲染 OOM / per-server semaphore 缺失（参考 large_image.py）/ N+1 / fan-out 串行
4. **数据一致性**：DB-API 双写、抽奖派奖事务原子性
5. **可观测 / 错误传播**：审计日志、reply_failure 文案规范
6. **schema / 公共 helper 复用**：是否复用 `screenshot_temp.py` (uuid)、`large_image.py`、`server_broadcast.py`、`audit.py`、新增的 `permissions.is_owner/validate_permission_key/is_dangerous_permission`、`db.RESERVED_GROUP_NAMES`、`execute_rowcount` 等

## 验收标准

### 审计阶段（已完成）
- [x] 62+ 条原始发现持久化到 `research/{lottery,leaderboard,misc}-findings.md`
- [x] 主代理读源码二次复查，结果在 `research/main-agent-recheck.md`

### 用户决策（2026-05-09）：D 全修

#### 🔴 Critical（4 个根因，共 8 项）

| # | 涉及 ID | 概要 |
|---|---|---|
| 1 | LO-3.1 + LO-3.2 | lottery 抽奖 lost-update on User.coins → 条件 UPDATE + execute_rowcount + MAX_COINS_AMOUNT 上限；池/奖品 TOCTOU → 第二 session reload pool/prize.enabled |
| 2 | LB-0.1 | leaderboard 截图无 OOM 防护 → 抽 `nextbot/screenshot_render.py` 公共 helper（semaphore + MAX_BASE64_BYTES + V11/non-V11 分支） |
| 3 | LB-10.1 / 13.1 / 14.1 / 15.1 / 16.1 / 17.1 | 6 个净收入/胜率榜全表 ORM .all() + Python sort → SQL 表达式 ORDER BY + LIMIT/OFFSET |
| 4 | MI-5.1 | group_member_notify 三处 on_notice() 无 rule → `Rule(_is_increase/_is_decrease)` 显式过滤 |

#### 🟠 High（约 12 项）

| 类别 | 涉及 ID | 修法 |
|---|---|---|
| lottery 与 shop 模式对齐 | LO-3.3 / 3.4 / 3.5 / 3.6 / 3.14 | 全失败 CRITICAL log + reply head 切换 / 命令 prize 部分失败记录 / 走 server_broadcast.broadcast / cmd_skip_reasons 全适配器发送 / N×M 命令上限 MAX_LOTTERY_CMD_EXECUTIONS |
| leaderboard 索引 + 数据规模 | LB-1.1 / LB-8.1 | User 表加 ix_user_coins / ix_user_sign_streak / ix_user_sign_total / ix_user_rob_total_loss / ix_user_rob_total_penalty；UserSignRecord 加 (sign_date, created_at) 复合索引；通过 ensure_*_schema 启动时检查 |
| leaderboard 串行 fan-out | LB-3.1 | 总在线时长改 asyncio.gather + per-server semaphore |
| leaderboard 数据 cap | LB-3.2 / LB-2.2 | totals dict 加 50000 键上限；entries cap 10000 |
| system 截图 OOM | MI-1.1+1.2 / MI-2.1+2.2 / MI-3.1+3.2 | about / tutorial / menu 全部走新 `screenshot_render.py` helper |
| group_member_notify 审计 | MI-5.2 / MI-5.3 | auto_ban_on_leave 加 audit_permission_change(action="user.ban.auto_on_leave"); 删除前置 SELECT 改用 apply_ban code 分流 |

#### 🟡 Medium（约 10 项）

| 涉及 ID | 修法 |
|---|---|
| LO-1.1 / 2.1 | 奖池列表 / 查看奖池 SQL pagination（参考 shop S-3.2）|
| LO-3.7 | _check_player_online cache 改 (bool|None, str) 区分 offline / RPC failure（参考 shop） |
| LO-3.8 | _resolve_probabilities 检测 set_total > 100 → re-normalize + logger.warning |
| LO-3.9 | _find_empty_slots 改 SELECT slot_index ORDER BY 早停 |
| LO-3.10 | {player} 替换前用 quote(safe="") 或拒绝特殊字符 |
| LO-3.13 | unit_value 加 MAX_COINS_AMOUNT 上限 |
| LB-0.3 | server_id <= 0 校验（4 处 server-side handler） |
| LB-99.1 | 17 命令默认 guest 加 cooldown 提示（评估 command_control 是否已有支持） |
| MI-4.2 | rob_protection commit 后二次 SELECT 改 capture-before |
| MI-5.4 / 5.5 | event 类型守卫 + chunk.strip() 检查 |

#### 🟢 Low / Info（约 12 项 — 一致性 / 文案 / 微优化）

LO-1.2 / LO-2.3（外层 try/except）/ LO-3.11（log token 截断）/ LO-3.12（LotteryDrawRecord 持久化—评估）/ LO-2.4 / LO-3.16 / LO-3.15（已正确）/ LB-0.4 / LB-0.5 / LB-13.2/13.3 / LB-3.4 / MI-1.3 / MI-4.4 / MI-4.5 / MI-5.7 / MI-5.8

### 设计性改造（贯穿）

1. **新增 `nextbot/screenshot_render.py`**：统一截图发送链，一次性消化 8+ 处重复 + LB-0.1 / MI-1.1+1.2 / MI-2.1+2.2 / MI-3.1+3.2 / LO-1.3 / 也可让 ban / permission_manager 受益
2. **lottery.py 全面对齐 economy/shop 已修模式**
3. **leaderboard.py 6 净收入/胜率榜抽公共 SQL helper**：参数化 `_score_expr` + `_min_count_filter`
4. **db.py 增加 leaderboard 索引 + ensure_user_leaderboard_indexes_schema()` 启动迁移**

### 实施阶段验收

- [ ] 上述全部修复落地（critical + high + medium + low + 设计性改造）
- [ ] **无破坏性更新**：所有命令外部行为对 V11 保持兼容
- [ ] **开箱即用**：DB schema 变化（5 + 1 索引）通过 ensure_*_schema 启动时检查 + try/except + logger.warning 兜底
- [ ] **失败文案符合规范**：reply_failure(action, reason) 不拼"动作 + 结果，原因"
- [ ] **修后再检查**：派 trellis-check 子代理对照 findings + recheck 再走一遍

## Out of Scope

- 已审过的 11 个分类
- WebUI（已记入下游任务）
- 渲染层 / TShock REST API 本身
- LotteryDrawRecord 持久化（如 LO-3.12 评估后认为是新功能则推后）

## Technical Notes

- 公共 helper 参考：`large_image.MAX_BASE64_BYTES` / `screenshot_temp.temp_screenshot_path` (uuid 后缀) / `server_broadcast.broadcast` / `audit.audit_permission_change` / `db.execute_rowcount`
- 计划新增：`nextbot/screenshot_render.py`（公共截图发送 helper）
- 历史经验：lost-update / DB-API 双写 / TOCTOU / 路径遍历 / OOM / fan-out 串行 / typo 静默接受 / 路径段未编码
