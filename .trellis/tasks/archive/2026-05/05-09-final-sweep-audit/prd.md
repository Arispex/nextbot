# 全量复审所有 plugins 命令的残留漏洞

## Goal

经过 12 轮分类审计 + 1 轮截图迁移之后，对 `nextbot/plugins/` 下全部 23 个文件 + 关键共享模块做最终复审，关注：

1. **回归**：12 轮审计修复（条件 UPDATE / audit / 公共 helper / SQL 表达式排序 / BEGIN IMMEDIATE / 等）是否在交叉时引入新问题
2. **遗漏**：单文件审计可能错过的跨 handler / 跨文件问题
3. **新代码**：审计期间新增的 helper（`audit.py` / `screenshot_render.py` / `server_broadcast.py` / `large_image.py` / `permissions.py` 增量）本身是否健壮
4. **横切一致性**：`DEFAULT_GUEST_PERMISSIONS`、所有 mutation handler audit log 完整性、所有 fan-out 都走 server_broadcast、所有截图都走 screenshot_render

## 审计范围

### 主体（23 plugin 文件）
about.py / ban.py / dice.py / economy.py / group_manager.py / group_member_notify.py / guess_number.py / leaderboard.py / lottery.py / menu.py / permission_manager.py / player_query.py / red_packet.py / rob.py / rob_protection.py / security.py / server_manager.py / server_send.py / server_tools.py / shop.py / tutorial.py / user_manager.py / warehouse.py
（不审 `tutorial_data.py` —— 纯静态数据）

### 关键共享模块（横切）
audit.py / ban_core.py / large_image.py / server_broadcast.py / screenshot_render.py / screenshot_temp.py / permissions.py / command_config.py / db.py / tshock_api.py

## 复查关注维度

1. **新引入回归**：BEGIN IMMEDIATE 全局 + 条件 UPDATE 互动 / 公共 helper 边界 case / 新加的 audit log 是否漏调
2. **横切一致性**：12 轮审计的修法在每个文件都应用了吗？
3. **新 helper 自身健壮性**：异常路径、信号量释放、超时、边界值
4. **未审过的代码路径**：审计期间新增的代码是否本身有 bug
5. **跨 handler 攻击面**：能否通过组合多条命令绕过单条命令的防护

## 验收标准

### 审计阶段（已完成）
- [x] 4 个 trellis-research 子代理并行复审
- [x] 主代理读源码二次复查 SH-8.1 critical claim
- [x] 结果汇总到 4 份 sweep findings + main-agent-recheck.md

### 用户决策（2026-05-09）：都修 + 二次检查

#### 🔴 必修 critical（1）

| # | ID | 修法 |
|---|---|---|
| 1 | SH-8.1 | `bot.py:157-170` else 分支替换为单一 `init_db()` 调用，避免现有部署升级时缺 6 个 ensure_*_schema |

#### 🟠 必修 high（4）

| # | ID | 修法 |
|---|---|---|
| 2 | SS-1.1 | 同步访客权限 confirm 改条件 UPDATE + retry（参考 重置访客权限 935-944） |
| 3 | SS-2.1 | 手动 ban / unban 加 audit_permission_change(action="user.ban" / "user.unban") |
| 4 | SF-X.1 | **决定 MAX_COINS_AMOUNT 是账户上限**（lottery 已对齐）；其他 4 文件（economy / red_packet / warehouse / shop）加币时全部加 `coins + delta <= MAX_COINS_AMOUNT` 条件 UPDATE |
| 5 | SF-4.x | shop `_buy_command` 改 server_broadcast.broadcast + 加 `MAX_SHOP_CMD_EXECUTIONS=200` |

#### 🟡 必修 medium（5）

| # | ID | 修法 |
|---|---|---|
| 6 | SS-3.1 | `_check_user_perm_mutation_pola` 补 self_grant / unknown_key 两条 denied audit |
| 7 | SS-4.1 | 继承身份组加 POLA 层级护栏（与 PMB-3.1 修改用户身份组对称：parent group 的 effective perms ⊆ operator 的 effective perms） |
| 8 | SF-4.3 | shop `require_online=False + target_server_id=None` 时拒绝（reply_failure），强制管理员明确 target |
| 9 | SF-X.2 | 5 个 handler 的金币变更 logger.info 字段统一（actor / target / before / after / amount / reason）—— audit_economy_change helper 作为后续任务推迟 |
| 10 | SH-4.2 | screenshot_render 非 V11 fallback 文案 "生成成功，截图生成成功" 改 "截图已生成" |

#### 🟢 必修 low / ℹ️ info

| # | ID | 修法 |
|---|---|---|
| 11 | SS-7.1 | DANGEROUS_PERMISSION_PREFIXES 加 `admin.*` / `server.*` / `server_tools.execute` / `server.add` / `server.delete` 等关键 RCE 等价 key |
| 12 | SS-5.1 | 重置访客权限 audit context 用 confirm-time live data（不用 preview-time stale） |
| 13 | SH-8.2 | BEGIN IMMEDIATE event listener 加 `if dialect.name == "sqlite":` 守卫 |
| 14 | SH-9.1 | admin.rename 加 audit_permission_change(action="user.rename")，记录 actor / target / before / after name |

### 推迟（明确不本批做）

- audit_economy_change helper 设计（需要单独任务，5 个 handler 接入是大重构）
- SS-6.1（rob counter 设计取舍，用户已确认是合理设计）
- SH-1.1（context dict 大小限制，acceptable trade-off）
- SH-2.2（semaphore_for 永不释放，acceptable trade-off）

### 实施阶段验收

- [ ] 14 个修复模块全部落地
- [ ] **无破坏性更新**：V11 行为兼容
- [ ] **开箱即用**：bot.py existing 路径补全 init_db 调用，旧库升级零破坏
- [ ] **失败文案符合规范**
- [ ] **修后再检查**：派 trellis-check 子代理对照 4 份 findings + recheck 再走一遍

## Out of Scope

- WebUI / 渲染层
- TShock REST API 本身
- tutorial_data.py 静态数据

## Technical Notes

12 轮审计已修复模式（用作 checklist）：
- 条件 UPDATE + execute_rowcount 防 lost-update
- BEGIN IMMEDIATE 全局序列化写者
- TOCTOU 第二 session re-validate
- DB-API 双写 CRITICAL log + reply head 切换
- fan-out 走 server_broadcast.broadcast
- 截图走 screenshot_render.render_and_send_screenshot
- temp 文件走 screenshot_temp uuid 后缀
- mutation 走 audit_permission_change
- POLA + dangerous-key blocklist + permission registry
- continued inheritance DFS 循环检测
- URL 路径段 quote(safe="")
- _safe_at_segment 防 int(user_id) ValueError
- IntegrityError 捕获 + reply_failure
- MAX_COINS_AMOUNT / MAX_BASE64_BYTES 上限
- per-server semaphore + LONG_READ_TIMEOUT
