# refactor: 黑白名单 / 账号管理统一走 sync API

## Goal

插件端已新增 `GET /nextbot/sync` 端点（机器人主动触发插件去拉取主库快照、强制全量、apply 后返回 `{syncStatus, whitelist:{added,removed,skipped}, blacklist:{...}, passwordHash:{...}}`）。本任务把命令端 / WebUI 所有细粒度的"添加白名单 / 删除白名单 / 创建 TShock 账号 / 改密 / 改名 / 加黑 / 解黑"调用统一替换为：**写完 DB → 调一次 sync trigger（带 debounce + future coalescing）**。WebUI 同步按钮从"每行一个"改为"页面右上角全局一个"。

## Requirements

### R1 — 新建统一 sync orchestrator

新建 `nextbot/sync_orchestrator.py`：

- **公开 API**：`async def trigger_sync_all_servers() -> list[SyncOutcome]`
  - 调用方 `await` 即可拿到 per-server 回执；不返回前不应继续后续动作。
  - 内部：debounce 500ms + future coalescing（500ms 窗口内的所有 caller 共享同一 future，定时器 fire 时统一执行）。
  - 用 **leading-edge + trailing-edge** 模式：单 caller 立刻 sync 不延迟；高频时窗口内后续 caller 合并到 trailing sync。
- **per-server 调用**：并行 fan-out `GET {server}/nextbot/sync?token=` 到每台 NextBotAdapter 服务器。
- **400 "Sync is already in progress" 处理**：等待 1 秒 retry，最多 3 次；3 次都 busy 则回执标记 `busy`（视为软失败，UI 文案体现"同步繁忙"）。
- **结果聚合**：返回 `list[SyncOutcome]`，每条含 `{server_id, server_name, ok: bool, status: "ok" | "busy" | "unauthorized" | "unreachable" | "skipped" | "not_modified" | "disabled" | "error", detail: str, raw_payload: dict}`。
  - `syncStatus = Ok / NotModified / Skipped` → `ok = True`
  - 其它 → `ok = False`
- **日志（机器搜索风格 key=value，写到 console 而非用户消息）**：
  - `[INFO] sync 触发：caller=<source> server_count=<n>`
  - `[INFO] sync 服务器结果：server_id=<id> name=<name> status=<status> whitelist_added=<n> whitelist_removed=<n> whitelist_skipped=<n> blacklist_added=<n> blacklist_removed=<n> blacklist_skipped=<n> password_updated=<n> password_created=<n> password_unchanged=<n> password_skipped=<n> password_failed=<n>`
  - `[INFO] sync 聚合：success=<x>/<total>`
  - `[WARN] sync 服务器异常：server_id=<id> reason=<exc>`
- **公开 helper**：`def format_sync_outcomes_for_user(outcomes) -> str` — 用户可见文案为 per-server 多行列表：
  ```
  同步服务器结果：
  1.xx服务器：同步成功
  2.yy服务器：同步失败，<原因>
  ```
  - 行号 = server.id；server 名 = server.name。
  - 单行成功：`<id>.<name>：同步成功`
  - 单行失败：`<id>.<name>：同步失败，<原因>` —— 原因从 `SyncOutcome.detail` 透传（严格遵循 CLAUDE.md：动作 + 结果 + 原因，原因不含对象名）。
  - **per-server 原因映射规则**（orchestrator 内统一）：
    - `syncStatus = Ok` → 成功
    - `syncStatus = NotModified` → 成功（无变化）
    - `syncStatus = Skipped` → 成功（行尾追加"无需同步"，例如 `<id>.<name>：同步成功，无需同步`）
    - `syncStatus = Unauthorized` → 失败，原因 = 上游 message（如 `Token is invalid (HTTP 401).`）
    - `syncStatus = Unreachable` → 失败，原因 = 上游 message（如 `No such host is known.`）
    - `syncStatus = Disabled` → 失败，原因 `同步功能已禁用`
    - HTTP 400 busy + 3 次重试仍失败 → 失败，原因 `同步繁忙`
    - 其它异常 → 失败，原因 = 异常摘要
- **详细 added/removed/updated/created 数字仍只走 console 日志**，不进用户消息（用户只关心成功/失败 + 失败原因；数字属于运维诊断范畴）。

### R2 — 命令端切换

| 文件 | 当前调用 | 新调用 |
|---|---|---|
| `nextbot/plugins/user_manager.py` `handle_add_whitelist` (注册账号) | `_sync_whitelist_to_all_servers` + `_create_tshock_user_on_all_servers` | 写 DB（含 password_hash）后调 `trigger_sync_all_servers()` |
| `nextbot/plugins/user_manager.py` `handle_sync_whitelist` (`同步白名单` 命令) | `_sync_whitelist_to_all_servers` | **整个命令 + matcher + handler 全部删除**（用户决策：现阶段同步机制完全可以替代）|
| `nextbot/plugins/user_manager.py` rename 流程 | `/nextbot/whitelist/remove + add` | 写 DB 后调 `trigger_sync_all_servers()` |
| `nextbot/plugins/ban.py` `handle_ban` | `sync_user_to_blacklist`（ban_core.py）| 写 DB 后调 `trigger_sync_all_servers()` |
| `nextbot/plugins/ban.py` `handle_unban` | `sync_user_blacklist_remove`（ban_core.py）| 同上 |
| `nextbot/plugins/group_member_notify.py` `handle_auto_ban_on_leave` | `sync_user_to_blacklist` | 同上（debounce 自动合并连环退群事件）|

**保留不变**：
- `nextbot/plugins/security.py` `允许登入` / `拒绝登入`（事件型，调 `/nextbot/security/confirm-login` 与 sync 无关）。
- 其它 `/v2/server/status` 在线查询、踢人、广播消息等读 / 事件 API。

### R3 — WebUI 后端切换 (`server/routes/webui_users.py`)

| endpoint | 当前调用 | 新调用 + response |
|---|---|---|
| `POST /webui/api/users` (`webui_users_create`) | `_sync_user_whitelist` + `_create_tshock_user_on_all_servers` | DB 写入（含 hash）→ `trigger_sync_all_servers()` → response `{user, sync_outcomes}` |
| `DELETE /webui/api/users/{id}` | `_broadcast_whitelist_remove` | DB 删除 → `trigger_sync_all_servers()` → response `{sync_outcomes}` |
| `POST /webui/api/users/{id}/ban` | `_ban_one` × N | DB 写 ban → `trigger_sync_all_servers()` → response `{user, sync_outcomes}` |
| `POST /webui/api/users/{id}/unban` | `_unban_one` × N | DB 写 unban → 同上 |
| `POST /webui/api/users/{id}/change-password` | `_update_tshock_user_password_on_all_servers` | DB 写 hash → 同上 |
| `POST /webui/api/users/{id}/change-name` | `_broadcast_whitelist_rename` | DB 写 name → 同上 |
| `POST /webui/api/users/{id}/sync-whitelist` (每行同步按钮) | `_sync_user_whitelist` | **整个 endpoint 删除**，由全局同步按钮替代 |

**新增 endpoint**：
- `POST /webui/api/sync/trigger` — 全局同步触发器：调 `trigger_sync_all_servers()` 直接返回 `{sync_outcomes}`，供 WebUI 右上角同步按钮使用。
  - 权限：admin（与现有 sync-whitelist 相同）。

### R4 — WebUI 前端 (`server/webui/static/js/users.js` + `users_content.html`)

- **删除每行的"同步"按钮**及相关代码（`users.js:586,681,1591`、`users.js:1579`、`users.css:63`、HTML 里对应 cell）。
- **页面右上角新增全局"同步"按钮**（与"刷新"按钮相邻），点击调 `POST /webui/api/sync/trigger`：
  - 按钮态：`同步中…` / `同步`。
  - toast 渲染 per-server 结果（与命令端一致），首行 `同步服务器结果：`，下面每台一行 `<id>.<name>：同步成功 / 同步失败，<原因>`。
- 所有现有 toast 里的 `服务器白名单：` / `服务器账号：` / `白名单同步结果：` 分段拼接逻辑删除，统一改为渲染后端返回的 `sync_outcomes` 数组（结构：`[{server_id, server_name, ok, status, detail}]`，前端按同一格式渲染 per-server 行）。

### R5 — 死代码清理（切换完成后）

删除：
- `nextbot/plugins/user_manager.py`：`_create_tshock_user_on_server`、`_create_tshock_user_on_all_servers`、`_sync_whitelist_to_all_servers`、`sync_matcher`、`handle_sync_whitelist`
- `nextbot/ban_core.py`：`sync_user_to_blacklist`、`sync_user_blacklist_remove`、`format_blacklist_add_lines`、`format_blacklist_remove_lines`（注意保留 `apply_ban_to_db` / `apply_unban_to_db` 这类 DB 层 helper）
- `server/routes/webui_users.py`：`_sync_user_whitelist`、`_update_tshock_user_password_on_all_servers`、`_broadcast_whitelist_remove`、`_broadcast_whitelist_rename`、`webui_users_sync_whitelist` endpoint，以及不再使用的 `_create_tshock_user_on_all_servers` import

**保留**：`broadcast` / `BroadcastOutcome` / `aggregate`（仍被 security.py 等使用）；`_extract_blacklist_entries` 等仅用于 sync 中间状态查询的 helper 视情况清理。

## Acceptance Criteria

- [ ] `nextbot/sync_orchestrator.py` 存在并对外暴露 `trigger_sync_all_servers()` / `format_sync_outcomes_for_user()`。
- [ ] 500ms 内连续 5 次调用 `trigger_sync_all_servers()` 实际只触发 ≤2 次 per-server HTTP（leading + 1 trailing）。
- [ ] 模拟某 server 返回 HTTP 400 "Sync is already in progress"，orchestrator 等 1s 重试，最多 3 次；3 次都 busy → outcome.status = `busy`、ok = False。
- [ ] 命令 `同步白名单` 已下线（输入命令不响应或返回 unknown command）。
- [ ] 注册账号 / 封禁 / 解封 / 退群自动封禁触发后，console 日志含 `sync 触发`、`sync 服务器结果`、`sync 聚合` 三条 INFO。
- [ ] 用户可见消息为 per-server 多行：`同步服务器结果：\n1.<name>：同步成功\n2.<name>：同步失败，<原因>`；**不含** added/removed/skipped 数字（数字只走 console 日志）。
- [ ] WebUI 用户列表行内**无**"同步"按钮；页面右上角**有**"同步"按钮且能正常工作。
- [ ] WebUI 创建用户 / 改名 / 改密 / 封禁 / 解封 / 删除后，前端只展示统一的"同步成功 / 失败"文案，不再有"服务器白名单：xxx / 服务器账号：xxx"分段。
- [ ] R5 列出的所有死代码已删除，`grep` 不再有任何调用。
- [ ] ruff / pyright / 现有 spec 检查均通过；现有 economy / lottery / shop / warehouse / 在线查询等无副作用。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 合规性 / 跨层数据流）。
- 所有 caller 切换完成，老 helper 全部清理。
- 用户体验：注册 / 封禁等操作的 response 时间 ≤ 1s（leading-edge sync 路径）；连环高频 ≤ 1.5s（trailing-edge 合并 + 1 个 retry 窗口）。
- 文案符合 CLAUDE.md 用户反馈规范（per-server 行为 `同步成功` / `同步失败，<原因>`，原因从上游 message 透传不改写，不含 added=N 等技术数字）。

## Decision Log (ADR-lite)

| # | 决策 | 选择 |
|---|---|---|
| 1 | 用户可见回执粒度 | per-server 多行：`<id>.<name>：同步成功 / 同步失败，<原因>`；added/removed/updated 数字仍只走 console 日志 |
| 2 | `同步白名单` 命令是否保留 | 删除 |
| 3 | `允许登入` / `拒绝登入` 是否纳入 | 不纳入（事件型 API，与 sync 无关）|
| 4 | 400 busy 处理 | 等 1s 重试，最多 3 次；3 次都 busy → 软失败 |
| 5 | debounce 粒度 | 全局 500ms 窗口（leading + trailing），所有 caller 共享 future |
| 6 | 退群封禁是否走 sync | 是；天然受益于 debounce 合并连环退群 |

## Out of Scope

- 不改 `/v2/server/status` / 踢人 / 广播 / 在线查询等事件型 / 读型 API。
- 不改 `security.py` 的 `允许登入` / `拒绝登入`。
- 不改 settings.json 的 `sync.whitelist` / `sync.blacklist` / `sync.passwordHash` 三个开关（这是插件端配置，机器人不管）。
- 不改 `/webui/api/sync/snapshot` snapshot endpoint（已经实现且仍被插件端 pull 使用）。
- 不改插件端 GET /nextbot/sync 的 contract（用户已固定）。

## Technical Notes

- 插件端 sync API 文档：会话中用户提供（GET /nextbot/sync，返回 `{syncStatus, message, httpStatus, whitelist, blacklist, passwordHash}`）。
- 现有 snapshot endpoint：`server/routes/webui_sync.py:GET /webui/api/sync/snapshot`（不动）。
- 已有 helper 可参考：
  - `nextbot/server_broadcast.py`：`broadcast / BroadcastOutcome / aggregate`（仍可在 orchestrator 内部复用）
  - `nextbot/tshock_api.py`：`request_server_api / is_success / get_error_reason`
- debounce + future coalescing 实现骨架已与用户讨论敲定（leading + trailing + `asyncio.Lock` + 共享 Future）。

## Implementation Phasing (suggested)

建议在一次 implement 流程内按顺序完成，便于一次 commit + check：

1. **Phase A**：实现 `nextbot/sync_orchestrator.py` + 单元逻辑 self-test
2. **Phase B**：迁移命令端 caller（user_manager / ban / group_member_notify）
3. **Phase C**：迁移 WebUI 后端 caller + 新增 `POST /webui/api/sync/trigger`
4. **Phase D**：WebUI 前端（删每行按钮 + 加全局按钮 + 统一 toast）
5. **Phase E**：删 R5 列出的全部死代码 + 跑 grep 验证清理干净
