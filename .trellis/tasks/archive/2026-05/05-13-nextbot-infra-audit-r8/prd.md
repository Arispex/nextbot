# audit: nextbot 基础设施层 round-8 复审（Round 7 修复 + 全量再扫）

## Goal

Round 7 在 commit `66b4d6c` 落地了 22 条修复（4 High + 2 Medium-High + 16 Medium）。本轮 round-8 是**复审 + 再扫**：

1. **验证 Round 7 修复的实际正确性**：每条修复都需要复读修复后的代码，确认：
   - 真正解决了原 finding 的根因（而非"看起来改了"）
   - 没有引入新的回归（如类型变更破坏 caller、新增逻辑路径覆盖不全）
2. **全量再扫剩余问题**：用全新的视角扫一遍同样的 20 个基础设施文件，特别关注 Round 7 改动周边代码的新暴露面（如新加的 stream 路径、shared client 生命周期、import-time 校验、合并的 user 列迁移）。
3. **报告剩余值得修复的问题**：Low / Info 级别的不重复 Round 7 已识别但跳过的项；只报新发现 + Round 7 修复留下的需要二次打磨的细节。

## Scope（与 Round 7 一致）

20 个目标文件：
- `bot.py`
- `nextbot/` 下：`access_control.py` / `audit.py` / `ban_core.py` / `command_config.py` / `data_dir.py` / `db.py` / `large_image.py` / `message_parser.py` / `permissions.py` / `progression.py` / `screenshot_render.py` / `screenshot_temp.py` / `server_broadcast.py` / `server_validation.py` / `stats.py` / `text_utils.py` / `time_utils.py` / `tshock_api.py` / `warehouse_lock.py`

显式排除：plugins / / server / / 测试 / 迁移脚本（同 Round 7）。

## Round 7 修复清单（必读，避免重复挖）

按桶列出，每条都已在 `66b4d6c` commit 内落地：

**DB（db.py）**
- H-1 `isolation_level = None`（连接 listener）
- H-2 `journal_mode = WAL` + `synchronous = NORMAL`
- D-1.2 强化 `_force_immediate_begin` docstring
- D-1.3 `ensure_*_schema` 统一走 `engine.begin() + sa_text`
- D-1.4 user 表 PRAGMA 合并到 `_USER_COLUMN_MIGRATIONS` + `_ensure_user_columns()`
- D-1.5 `ensure_user_signin_schema` 日志含 `sqlite3.sqlite_version`
- D-1.6 `ensure_default_*` 显式 rollback
- D-1.7 `execute_rowcount` 非 CursorResult 加 `logger.warning`

**权限 / 审计 / 访问控制**
- H-3 `require_permission` / `command_control` import-time `bot`/`event` 形参校验 + runtime fail-closed
- P-1.6 `get_owner_ids` / `get_owner_ids_ordered` / `get_group_ids` 加 `@lru_cache(1)`，返回 `frozenset` / `tuple`
- P-1.9 `audit.py` 加 `_safe_repr` + `_coerce_snapshot` 类型守卫
- P-1.13 `ban_core` 加 `_extract_blacklist_entries` payload 防御

**IO**
- H-4 `tshock_api` 改 stream 模式 + `MAX_RESPONSE_BYTES=250MB` chunk 累加 cap
- I-1.1 `tshock_api` 用 `httpx.URL(...)` 替代 f-string
- I-1.3 `tshock_api` 模块级 `httpx.AsyncClient` 单例 + `close_shared_client()` + `bot.py @driver.on_shutdown` 接线
- I-1.4 `TShockRequestError` 新 `kind` 字段（timeout / unreachable / invalid_url / protocol / oversize / unknown）
- I-1.5 `response.json` 失败日志加 `server_id` / `content_length` / `status`
- I-2.1 `large_image` 加 `release_server_semaphores` cleanup helper
- I-3.1 `server_broadcast._wrap` 把 `semaphore_for` + `async with sem` 移入 try 块

**Bot / Utils**
- MH-1 `bot.py` event_preprocessor 加 `bot: Bot` + console adapter guard
- MH-2 `command_config.update_command_aliases` 冲突集合加 `r.command_key`
- U-1.1 `bot.py ensure_env_file` 改 f-string + `try/except OSError` fail-soft
- U-2.3 `command_config` wrapper 包 `_check_user_banned` 异常 fail-soft
- U-2.4 `command_config._get_runtime_state` DB 异常改 `logger.exception`

**显式跳过（Round 7 已确认）**
- P-1.7 request-context effective_perms 缓存
- I-5.1 Playwright `new_context` async-with（代码实际在 `server/screenshot.py`，PRD 排除）
- 第 3 梯队剩余 Low / Info（P-1.4 / P-1.10 / P-1.12 / P-1.14 / I-1.6 / I-5.2 / I-5.3 / I-5.4 / U-1.3 / U-1.4 / U-3.1 等）

## Requirements

1. **Regression 校验**：每条 Round 7 修复都需要 Read 修复后代码 + 推演 trigger 条件，给 PASS / NEW-ISSUE 判定。任何 NEW-ISSUE 必须能用精确行号验证。
2. **全量再扫**：4 桶并行 trellis-research 子代理 + 主代理二次审核。子代理把发现 persist 到 `research/r8-{db,permission,io,utils}.md`，主代理 verify 到 `research/r8-verify-pass2.md`。
3. **报告口径**：与 Round 7 一致 —— 严重度（Critical / High / Medium / Low / Info）+ 文件 + 行号 + 修复前 / 修复后行为 + 触发概率 / 影响范围。
4. **本轮先报告**：实施由用户决定（A: 仅修第 1 梯队 / B: 第 1+2 梯队 / C: 全修 / D: 逐条选）。

## Acceptance Criteria

- [ ] 20 个目标文件全部覆盖
- [ ] Round 7 的 22 条修复每条都明确 PASS / NEW-ISSUE 判定
- [ ] 4 个子代理产物落到 `research/r8-{db,permission,io,utils}.md`
- [ ] 主代理二次审核日志落到 `research/r8-verify-pass2.md`
- [ ] 最终向用户呈现按严重度排序的合并报告 + 修复前后效果对照
- [ ] 用户确认修复范围后走 trellis-implement / trellis-check 路径（如 0 Critical / 0 High 也可声明 Round 7 修复闭环）

## Out of Scope

- 实施修复（先报告，用户决定）
- WebUI 同步审计（独立任务）
- plugins 命令层（已 6 轮 sweep + Round 7 已闭环基础设施层）
- 新功能需求

## Technical Notes

- Round 7 commit 全部改动：`git show 66b4d6c --stat` 或直接读 `commit 66b4d6c` 的 diff
- Round 7 任务归档位置：`.trellis/tasks/archive/2026-05/05-13-nextbot-infra-audit/`
- 子代理可读取 Round 7 的 `verify-pass2.md`（已归档到上述路径）作为 prior art
- 关注 Round 7 修复**周边代码**的新暴露面：
  - `tshock_api` 新加 stream 路径 + shared client 单例的 race / lifecycle
  - `command_config` import-time `RuntimeError` 对未来 wrapper 链的影响
  - `audit.py` `_coerce_snapshot` 对未知类型 fall back 到 str 的副作用
  - `db.py` `_USER_COLUMN_MIGRATIONS` 表驱动迁移的扩展契约
  - `ban_core` `_extract_blacklist_entries` 替换后旧调用点的 isinstance 过滤删除是否完整
  - `access_control` `frozenset` / `tuple` 返回类型变更的 caller 侧影响（trellis-check 已 grep，但 r8 需独立复查）
