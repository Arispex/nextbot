# audit: nextbot 基础设施层 round-9 复审（Round 8 修复 + 全量再扫）

## Goal

Round 7 (`66b4d6c`, 22 fixes) + Round 8 (`5c41928`, 14 fixes) 累计 36 条修复。Round 9 是**第二次复审**：

1. **验证 Round 8 修复的实际正确性**：包括 2 处 check sub-agent self-fix（LIFO shutdown 顺序 / 8 dict identity-comparison）
2. **全量再扫剩余问题**：尤其关注 Round 8 改动周边代码的新暴露面（`_run_migration` helper / `wal_checkpoint_truncate` / `register_server_semaphore_pool` 中央注册 / `execute_rowcount` fast-fail 后 caller 是否需要 try/except / `bot.py` 两个 shutdown 钩子 LIFO 行为 / `_runtime_cache_load_failure_logged` flag throttle 副作用）
3. **是否声明 36 条修复收敛闭环**：若 0 Critical / 0 High / 0 Medium → 声明 nextbot 基础设施层审计正式收敛

## Scope

20 个目标文件（与 Round 7/8 一致）：
- `bot.py`
- `nextbot/` 下：`access_control.py` / `audit.py` / `ban_core.py` / `command_config.py` / `data_dir.py` / `db.py` / `large_image.py` / `message_parser.py` / `permissions.py` / `progression.py` / `screenshot_render.py` / `screenshot_temp.py` / `server_broadcast.py` / `server_validation.py` / `stats.py` / `text_utils.py` / `time_utils.py` / `tshock_api.py` / `warehouse_lock.py`

排除：`plugins/` / `server/`（webui 独立任务）/ 测试。

## Round 8 修复清单（必读避免重复挖）

**Medium (5)**
- M-1 (R8-D-6) `execute_rowcount` fast-fail TypeError
- M-2 (R8-D-1) `_run_migration` helper 包 16 个 ensure_*_schema
- M-3 (R8-D-3) `wal_checkpoint_truncate()` + bot.py on_shutdown
- M-4 (R8-U-B-1) `update_command_aliases` 加 alias 自查
- M-5 (R8-IO-B-1) `register_server_semaphore_pool` / `release_server_semaphores_all` 中央注册

**Low (9)**
- R8-D-4 rollback 异常链路：捕获 `commit_exc` 显式重抛
- R8-D-5 `_set_sqlite_pragma` dialect guard
- R8-P-1.14 `add_permission` 系列加逗号 sanitize
- R8-P-1.15 `_coerce_snapshot` 递归
- R8-P-1.16 `_get_effective_permissions_in_session` session guard
- R8-IO-B-2 import-time `assert MAX_RESPONSE_BYTES >= MAX_BASE64_BYTES * 5/4`
- R8-IO-B-3 + A-1.1 `json.loads(bytearray)`
- R8-U-B-2 `_runtime_cache_load_failure_logged` throttle

**Check sub-agent self-fix（关键）**
- bot.py LIFO 顺序：`_wal_checkpoint` 先注册（后执行），`_close_shared_http_client` 后注册（先执行）
- `large_image.register_server_semaphore_pool` identity 比较 `not any(p is pool for p in ...)`

**Round 8 显式跳过 / 下调**
- R8-D-2 `_user_columns_ensured` flag（实际 safe 归 Info）
- R8-D-7 engine 并发 init（下调 Low）
- R8-D-8 / R8-IO-A-3.x / R8-P-1.17~1.20 / R8-U-B-3~B-6

## Requirements

1. **Regression 校验**：Round 8 14 条修复 + 2 self-fix 每条 Read 验证 PASS / NEW-ISSUE
2. **全量再扫**：4 桶并行 trellis-research，产物落 `research/r9-{db,permission,io,utils}.md`，主代理二次审核落 `r9-verify-pass2.md`
3. **报告口径**：严重度 + 文件 + 行号 + 修复前 / 后行为 + 触发概率 / 影响
4. **本轮先报告**：用户决定后再走 trellis-implement / trellis-check

## Acceptance Criteria

- [ ] 20 个目标文件全部覆盖
- [ ] Round 8 14 条修复 + 2 self-fix 每条 PASS / NEW-ISSUE 判定
- [ ] 4 个子代理产物落 `research/r9-{db,permission,io,utils}.md`
- [ ] 主代理二次审核日志落 `r9-verify-pass2.md`
- [ ] 最终向用户呈现按严重度排序的合并报告
- [ ] 若 0 Critical / 0 High / 0 Medium，声明 36 条修复收敛闭环

## Out of Scope

- 实施修复（先报告）
- WebUI 同步审计（独立任务）
- plugins 命令层（Round 7 + 8 已闭环）

## Technical Notes

- Round 8 commit: `5c41928`
- Round 8 task 归档位置：`.trellis/tasks/archive/2026-05/05-13-nextbot-infra-audit-r8/`
- 子代理可读 Round 8 `r8-verify-pass2.md` 作 prior art
- 关注 Round 8 修复**周边代码**的新暴露面：
  - `_run_migration` helper 失败时的告警是否会覆盖原有的 sub-failure trace（如 `ensure_user_signin_schema` 内部已有 logger.warning）
  - `wal_checkpoint_truncate` 是否在 SQLite 持锁时阻塞 shutdown
  - `register_server_semaphore_pool` 中央注册 list `[]` 在并行 import 时是否有 race（module-level mutation 是 GIL safe 但模式可能脆弱）
  - `execute_rowcount` 改 TypeError fast-fail 后，是否所有 caller 都已 grep 确认无 SELECT 误传（包括之前没列举的 webui caller）
  - bot.py LIFO 顺序假设：NoneBot 内部 `_shutdown_funcs` 是否真的 reversed 调用（依赖具体版本）
