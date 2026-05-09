# 主代理二次复查结论 — 全量复审

**日期**: 2026-05-09
**复查范围**: 4 个子代理结果（金融 + 小游戏/安全/权限 + 服务器交互 + 用户/系统/共享模块），约 30+ 项发现

---

## 复查方法

主代理读源码验证：
- `bot.py:153-170` vs `db.py:419-438` 验证 SH-8.1：existing-DB 路径漏 6 个 ensure_*

---

## ✅ 真实 critical（必修）

### 🔴 SH-8.1 — `bot.py` existing-DB 升级路径漏 6 个 schema migration

- **位置**：`bot.py:153-170` else 分支（已存在 app.db 时）
- **现状**：只调用了 11 个 ensure，遗漏 6 个：
  ```
  ensure_user_sign_record_index_schema
  ensure_shop_schema (show_command/require_online/actual_value/is_mystery 列)
  ensure_lottery_schema
  ensure_user_name_unique_schema (LOWER(name) 唯一索引)
  ensure_user_leaderboard_indexes_schema (5 个排行榜字段索引)
  ensure_warehouse_fk_schema (warehouse user_id 索引)
  ```
- **影响**：
  - **会破坏现有部署升级**：旧库升级后商店命令首次添加 mystery 商品会 `OperationalError: no such column show_command`
  - **缺索引性能退化**：排行榜 / 签到查询全表扫描
  - **race window 实际存在**：缺 user.name 唯一索引时并发改名 IntegrityError 永不抛 → 重名静默成功
- **修法（最佳）**：把 `bot.py:157-170` 整个 else 分支替换为单一 `init_db()` 调用——`Base.metadata.create_all()` 已是幂等，所有 `ensure_*` 内部都是 `IF NOT EXISTS`，重复调用安全

## ✅ 真实 high（应修）

| ID | 简述 | 修法 |
|---|---|---|
| **SS-1.1** 同步访客权限 confirm 仍 ORM dirty-set | 与对偶 `重置访客权限` 不一致，sweep 留下唯一一处 lost-update 例外 | 改条件 UPDATE + retry（参考 重置访客权限 的 935-944） |
| **SS-2.1** 手动 ban/unban 漏 audit_permission_change | 被动 auto-ban 调了，主动 ban 反而漏，audit 完整性断裂 | 加 audit("user.ban" / "user.unban") |
| **SF-X.1** MAX_COINS_AMOUNT 语义 drift | lottery 当账户上限做 partial-cap，其他 4 文件当单笔上限。组合命令可绕过 lottery cap | 二选一：(a) 项目级定义为单笔上限（lottery 跟齐）/ (b) 项目级定义为账户上限 + 加 DB-level CHECK |
| **SF-4.x** shop 没迁 server_broadcast | _buy_command 串行 fan-out 无总 RPC cap（lottery 已迁过）| 抽 broadcast 调用 + 加 MAX_SHOP_CMD_EXECUTIONS=200 |

## ✅ 真实 medium

| ID | 简述 |
|---|---|
| **SS-3.1** `_check_user_perm_mutation_pola` 4 条拒绝路径只 2 条打 denied audit | 漏 self_grant / unknown_key |
| **SS-4.1** 继承身份组缺 POLA 层级护栏 | 与对偶 修改用户身份组（PMB-3.1）不对称，仅靠 dangerous-key blocklist 单点防御 |
| **SF-4.3** shop `require_online=False + target_server_id=None` 语义模糊 | 玩家不在线时 TShock silent-fail 用户付 N 倍价拿 1 倍东西 |
| **SF-X.2** 金币 / 道具变更无统一审计入口 | 5 个 handler 各写各的 logger.info 字段名不统一 |
| **SH-4.2** 非 V11 fallback 文案重复："生成成功，截图生成成功" | 仅非 V11 适配器触发，V11 不受影响 |

## ✅ 真实 low / info（仅观察）

| ID | 简述 |
|---|---|
| SS-5.1 / SS-6.1 / SS-7.1 | 重置访客权限 stale data / rob counter 设计取舍 / DANGEROUS_PERMISSION_PREFIXES 可考虑加 admin.* / server.* / server_tools.execute |
| SH-1.1 / SH-2.2 / SH-3.3 / SH-7.x / SH-9.1 | shared 模块边界 case，多数 acceptable trade-off |
| SH-8.2 | BEGIN IMMEDIATE event listener SQLite-only，未来切 Postgres 需守卫 |
| SH-9.1 | admin.rename 不走 audit_permission_change（设计取舍） |

## ✅ 复审通过（无新问题）

- **子代理 C（服务器交互）全过**：截图迁移 / fan-out / URL quote / OOM cap / server_id 校验 / re-entrant lock / 大命令并发 全部 ✓
- screenshot_render 信号量释放 / V11 byte-identical / size cap 双校验 全部 ✓
- screenshot_temp uuid + finally cleanup ✓
- audit_permission_change 11 个 call site 参数正确 ✓
- large_image / server_broadcast / permissions 各 helper 边界 case 全部 ✓
- command_config init order 正确 ✓
- 12 轮已修模式（条件 UPDATE / TOCTOU / 公共 helper / SQL 表达式排序 / BEGIN IMMEDIATE / DFS 循环 / quote(safe="") / _safe_at_segment / IntegrityError / MAX_COINS_AMOUNT / MAX_BASE64_BYTES / per-server semaphore）**全部稳固**

---

## 主代理整体看法

**关键修复优先级**：

1. **🔴 SH-8.1（必修）**：bot.py existing-DB 路径漏 6 个 ensure，会让旧部署升级直接破坏。一个简单的 else 分支重写到 `init_db()` 即解决。**这是本轮 sweep 找到的最严重问题，且可立即修复。**

2. **🟠 SS-1.1 + SS-2.1 + SF-4.x（建议修）**：3 个项目级一致性差距，单独 high 但不致命，可批量修。

3. **🟠 SF-X.1（架构性）**：MAX_COINS_AMOUNT 语义需要项目级决定，建议先决定语义再补规范文档（`.trellis/spec/backend/economy-conventions.md`）+ 让 lottery 与其他 4 文件对齐。

4. **🟡 medium 8 项 + 🟢 low/info 多项**：可选修，主要是 audit log 完整性 / 文案 / defense-in-depth。

**SS-7.1 值得重点提及**：DANGEROUS_PERMISSION_PREFIXES 当前不含 `admin.*` / `server.*` / `server_tools.execute`（最敏感的 RCE 等价 key）—— 持有 `group.permission.add` 的非 owner 可授给某 group `server_tools.execute` 然后通过 `修改用户身份组` 自移到该组（如果 PMB-3.1 层级护栏对该 key 不严格）。**建议至少补上 `server_tools.execute` 进 blocklist**。
