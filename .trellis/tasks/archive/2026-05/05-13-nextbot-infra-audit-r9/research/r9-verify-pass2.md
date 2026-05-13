# Round 9 主代理二次审核日志

## Round 8 复审：16/16 全部 PASS

| 桶 | Round 8 修复 + self-fix | PASS | 备注 |
|---|---|---|---|
| DB | 5 (M-1, M-2, M-3, R8-D-4, R8-D-5) | 5 | 0 regression |
| Permission | 3 (R8-P-1.14, R8-P-1.15, R8-P-1.16) | 3 | 0 regression |
| IO | 3 + self-fix (M-5 identity-compare) | 4 | 8 pool 注册全部成功 |
| Utils | 3 + self-fix (LIFO 顺序) | 4 | NoneBot `_lifespan.py:79-81` LIFO 实测验证 |
| **总计** | **15 + 1 self-fix** | **16** | **全部生效，0 regression** |

Round 7 (`66b4d6c`) 26 条修复 + Round 8 (`5c41928`) 15 条修复 = **41 条累计修复全部仍然生效**。

## R9 新发现验证（主代理逐条 Read 行号验证）

### 终判 Medium 级别（1 项）

| ID | 子代理判 | 主代理终判 | 验证 |
|---|---|---|---|
| **R9-D-4** | Medium | **Medium** | ✅ CONFIRMED `db.py:958-959` `conn.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))` 不读返回值。SQLite TRUNCATE 在 reader 阻塞时返回 `(busy=1, log, checkpointed)` 但**不抛异常**，当前代码静默吞掉 busy 信号 → WAL 未实际 truncate 但运维不可观测 |

### 终判 Low 级别（3 项）

| ID | 子代理判 | 主代理终判 | 验证 |
|---|---|---|---|
| **R9-P-1.21** caller 未 catch sanitize ValueError | Low | **Low** | ✅ CONFIRMED `permission_manager.py` / `group_manager.py` 4 个 mutation handler 没 catch R8-P-1.14 的 ValueError → 用户输 `权限名,逗号` 会让 traceback 进群里。但 sanitize 路径只在 **owner-only foot-gun** 场景触发（owner 已能授任意权限），影响极小 |
| **R9-U-C.1.1** refresh_runtime_cache 单 row 损坏 | Low | **Low** | ✅ CONFIRMED `command_config.py:451-465` 任一 row 的 `_to_runtime_state` 抛异常会让整次 refresh 失败 → 全表退化到 RegisteredCommand fallback。但触发要外部畸形 SQL 写入 row（admin 不会通过 webui 写出畸形 JSON），生产无路径 |
| **R9-U-C.1.2** register_alias_matchers 无幂等 | Low | **Low** | ✅ CONFIRMED 启动只调一次，但代码层缺幂等保护；若未来 hot-reload / 测试调两次会双注册 alias matcher |

### Medium → False positive / 下调

| ID | 子代理判 | 主代理终判 | 理由 |
|---|---|---|---|
| **R9-P-Medium** webui_groups._remove_inherit 旁路 R8-P-1.14 | Medium | **False positive** | ✅ Read `webui_groups.py:180-182`：该 helper 仅做 `[item for item in CSV.split() if item != removed_name]` —— **只删不加**，永远不会写入含逗号的新元素。R8-P-1.14 sanitize 的目的是防止 `add_permission("perm1,perm2")` 单 token 含逗号污染 CSV 存储；删除路径根本不需要 sanitize。子代理误判 |
| **R9-IO 注释 vs 代码顺序矛盾** | Low | **False positive** | IO 桶子代理理解错误。Utils 桶 Read NoneBot `_lifespan.py:79-81` 实测 LIFO 验证：先注册 `_wal_checkpoint` → LIFO 后执行；后注册 `_close_shared_http_client` → LIFO 先执行。代码 + 注释一致 |

### Info / 设计 trade-off（不修）

| ID | 一句话 |
|---|---|
| R9-P-1.22 | Ban 路径 audit 在 broadcast 失败时仍记 success（**用户 Round 7 已明确"服务端反向同步兜底"接受**） |
| R9-P-1.23 | `_get_group_permissions` 无硬深度上限（Round 7 P-1.4 + Round 8 都判 Low，maintains） |
| R9-P-1.24 | `access_control.lru_cache` 无 cache_clear 入口（仅测试场景影响） |
| R9-P-1.25 | `_coerce_snapshot` 无 cycle detection / 不递归 set/frozenset / 不放过 datetime（caller 现状全部安全） |
| R9-D-5 | `_user_columns_ensured` flag 在 reload 场景残留（生产单进程无影响） |
| R9-D-6 | DATABASE_URL `timeout` 与 PRAGMA busy_timeout 风格不统一（功能无差异） |
| R9-D-7 | `engine.dispose()` 未在 shutdown 调用（Round 8 显式接受） |
| R9-D-8 | BOOLEAN vs INTEGER ALTER 字面量风格不统一（SQLite 类型亲和组一致，仅可读性） |
| R9-D-9 | RedPacketClaim.claimer_user_id 非索引前缀（小数据量无感） |
| R9-U-C.x | 各文件 6 项 Info 级 polish（regex / unicode / etc.） |

## 主代理终判

**Round 8 修复 100% 闭环，零回归。** Round 9 新一轮全量再扫发现：

- **0 Critical**
- **0 High**
- **1 Medium**（R9-D-4，1 行 fetchone + warning 修复）
- **3 Low**（owner-only foot-gun / DB row 损坏 / hot-reload 幂等防御）
- **2 False positive**（webui_groups._remove_inherit 不需 sanitize / IO 桶注释 vs 代码 LIFO 理解错误）
- **~10 Info**

Round 7 (`66b4d6c`) 22 + Round 8 (`5c41928`) 14 = **36 条累计修复** **全部生效零回归**。

剩余 1 Medium 是 **Round 8 M-3 修复周边暴露面**（WAL checkpoint 启用但未观测 busy 状态），1 行修复即可彻底闭环。

## 建议

- **修 R9-D-4**：1 行 `fetchone()` + 条件 logger.warning，让运维知道 WAL truncate 是否实际完成
- **3 个 Low 可选修**：成本 ~10 行，无强烈生产 trigger
- **修完 R9-D-4 后**：宣告 nextbot 基础设施层（plugins 外）经 Round 7 + 8 + 9 三轮系统审计**正式收敛闭环**
