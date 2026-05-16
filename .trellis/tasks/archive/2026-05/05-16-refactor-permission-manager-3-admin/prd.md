# refactor: permission_manager 删除 3 条 admin 用户权限命令

## 决议

`nextbot/plugins/permission_manager.py` 共 6 条命令。WebUI `/webui/users` 编辑器已覆盖前 3 条（用户级权限 / 身份组维护）。决定下线 1 / 2 / 3，保留 4 / 5 / 6（WebUI 无对应）。

| # | 命令 | 处置 | 原因 |
|---|---|---|---|
| 1 | 添加用户权限 | **删除** | WebUI users 编辑 user.permissions |
| 2 | 删除用户权限 | **删除** | WebUI users 编辑 user.permissions |
| 3 | 修改用户身份组 | **删除** | WebUI users 编辑 user.group_name |
| 4 | 管理员列表 | 保留 | WebUI 无对应 |
| 5 | 同步访客权限 | 保留 | WebUI 无对应 |
| 6 | 重置访客权限 | 保留 | WebUI 无对应 |

外部引用扫描：0 处（grep 命令文本 / matcher 名 / handler 名跨整个 repo 零命中）。

## 改动

仅 `nextbot/plugins/permission_manager.py`。

### 删除
- 3 个 matcher 声明（line 69-71）：
  - `add_user_perm_matcher = on_command("添加用户权限")`
  - `remove_user_perm_matcher = on_command("删除用户权限")`
  - `set_user_group_matcher = on_command("修改用户身份组")`
- 3 个 handler 函数：
  - `handle_add_user_perm`（line 208-332 附近）
  - `handle_remove_user_perm`（line 333-466 附近）
  - `handle_set_user_group`（line 474-629 附近）

### 保留
- 3 个 matcher 声明（admin_list_matcher / sync_guest_perms_matcher / reset_guest_perms_matcher）
- 5 个 handler 函数：
  - handle_admin_list
  - handle_sync_guest_perms / handle_sync_guest_perms_confirm
  - handle_reset_guest_perms / handle_reset_guest_perms_confirm
- 与保留 handler 相关的常量 / helper：
  - `ADMIN_LIST_SCREENSHOT_OPTIONS` / `_admin_list_semaphore` / `_NICKNAME_FETCH_TIMEOUT` / `_CSV_UPDATE_RETRY`
  - `_operator_id` / `_at_segment` / `_fetch_nickname_via_bot` / `_fetch_nickname_with_timeout`
  - `_SYNC_CONFIRM_TOKEN` / `_SYNC_GROUP_NAME` / `_diff_guest_default_permissions`
  - `_RESET_CONFIRM_TOKEN`

### 可能可清理的 helper
- `_check_user_perm_mutation_pola`（line 129）— 名字暗示是给"user perm 变更"用的策略检查。**implementer 先 grep 验证**它仅被 1/2/3 三个 handler 调用；若是 → 删除；若也被保留 handler 调用 → 保留。

### Import 清理
implementer 自行用 ruff / grep 验证移除"仅被 1/2/3 用"的 imports（如 `RESERVED_GROUP_NAMES` / `validate_user_payload` 等若有）。

## Scope

仅 `nextbot/plugins/permission_manager.py`。

## Acceptance

- bot 重启后 QQ 群发 `添加用户权限` / `删除用户权限` / `修改用户身份组` 不响应
- `管理员列表` / `同步访客权限` / `重置访客权限` 仍正常工作
- `/webui/users` 所有编辑功能不变
- `python3 -m py_compile nextbot/plugins/permission_manager.py` 通过
- `ruff check nextbot/plugins/permission_manager.py` 无未用 import 残留（或维持 baseline）

## DO NOT

- 不动 `nextbot/permissions.py` / `nextbot/audit.py` / 其它共享模块
- 不动 `/webui/users` 后端 / 前端
- 不动 `command_config` 的 6 个 server.* 注册
- 不动 `tutorial_data.py`（grep 已确认无引用）
- 不 commit

## Out of Scope

- 删 user_manager / ban / economy 的 admin 命令 — 单独任务
- 把保留的 3 条命令迁到 WebUI — 单独议题（admin 命令仍是兜底）
