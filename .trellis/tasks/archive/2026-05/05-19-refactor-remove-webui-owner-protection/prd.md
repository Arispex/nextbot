# 删除 WebUI ban / unban / delete 的 owner 保护机制

## Goal

`server/routes/webui_users.py` 中 3 个 endpoint（ban / unban / delete）当前对 owner 做硬保护（返回 403 `owner_protected`）。删除这个保护，让 admin 可在 WebUI 对 owner 执行 ban / unban / delete 操作。

`update` 保留 owner 保护（与本任务 scope 无关）。

## Rationale

与上一个 task（修改密码）Q3=B 决策一致：admin 通过 WebUI 操作 owner 应当被允许（admin 已是最高权限）。owner 保护是过度保险，反而妨碍正常运维场景。

## 改动

### `server/routes/webui_users.py`

删除 3 处 owner 保护代码：

**1. 删除 handler（L856-867）**
```python
# 删
if str(user.user_id) in get_owner_ids():
    logger.warning(
        f"删除用户被拒：user_id={user_id}，reason=owner_protected "
        f"client_ip={client_ip}"
    )
    return api_error(
        status_code=403,
        code="owner_protected",
        message="不能对管理员执行此操作",
    )
```

**2. ban handler（L1005-1012 附近）**
同样的 if block 删除。

**3. unban handler（L1137-1144 附近）**
同样的 if block 删除。

### 保留

- `update` handler L742 的 owner 保护**保留**（用户没要求改）
- `from nextbot.access_control import get_owner_ids` import 保留（update 仍用）

## Out of Scope

- `update` handler 的 owner 保护
- `change-password` endpoint（本来就没保护）
- 其他模块的 owner 保护（如有）

## Acceptance Criteria

- [ ] `grep -nE "owner_protected" server/routes/webui_users.py` → 仅 update handler 1 处保留（不是 4 处）
- [ ] `grep -n "get_owner_ids" server/routes/webui_users.py` → 仅 import + update handler 2 处
- [ ] delete / ban / unban handler 不再返回 403 owner_protected
- [ ] `python3 -m py_compile server/routes/webui_users.py` 通过
- [ ] `git diff --name-only` 仅 `server/routes/webui_users.py`

## 备注

行为变更 visible to users：
- 在 WebUI 对 owner 点 ban / unban / delete 按钮现在会**直接执行**（不再返回错误）
- delete owner = 实际删 owner 的 bot DB 行（不影响 `.webui_auth.json`，所以 owner 仍能登入 WebUI；但 bot DB 中没有该用户行，签到 / 经济等数据会丢）
- ban owner = bot DB 标 is_banned；玩家进 server 时被拦
- 这些行为风险由 admin 自负 —— admin 是 root，造成的损失 admin 自己负责
