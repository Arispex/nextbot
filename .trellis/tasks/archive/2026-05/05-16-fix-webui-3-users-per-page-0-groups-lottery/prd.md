# fix(webui): 回退 3 项审计限制

按用户偏好回退 commit `6995d3c` / `d364692` 中 3 项审计引入的限制：
1. **users**：`per_page=0` 全表通道封掉 → 重新开放
2. **groups**：创建组拒绝保留名（owner / admin / root / system / superuser） → 移除拒绝
3. **lottery**：命令奖品危险前缀黑名单（op / deop / ban / ban-ip / pardon / kick / stop / shutdown / restart / whitelist / save-*） → 移除黑名单

## 改动

### 1) users `per_page=0` 全表通道

`server/routes/webui_users.py`：

`webui_users_list`（line 304-）当前 line 313-315 把 `per_page<=0` clamp 到 10 / `_PER_PAGE_MAX`。改为：
```python
per_page_raw = int(pagination["per_page"])
fetch_all = per_page_raw == 0  # 0 = 取全表
if not fetch_all and (per_page_raw < 0 or per_page_raw > _PER_PAGE_MAX):
    per_page = min(_PER_PAGE_MAX, max(1, per_page_raw))
elif fetch_all:
    per_page = 0  # 占位，后续不会用
else:
    per_page = per_page_raw
```

查询路径（line 334-345）改为条件分支：
- `fetch_all=True`：`users = base.order_by(User.id.asc()).all()`，meta 用 `{"page": 1, "per_page": total, "total": total, "total_pages": 1}`
- 否则：保留现有 `build_pagination_slice` + offset/limit 路径

保留 `_PER_PAGE_MAX` 常量（用于非全表请求的 cap）。

### 2) groups 保留名拒绝

`server/routes/webui_groups.py`：

- 删除 line 27-31 整段 `_RESERVED_GROUP_NAMES` 常量定义 + 同步注释
- 删除 line 82-88 整块（`# H-1` 注释 + `if value.lower() in _RESERVED_GROUP_NAMES: raise ...`）
- 保留 `_BUILTIN_GROUPS` 检查（L-S-2，那是不同需求 — 防止误创建 `guest` / `default`，用户没要求移除）

### 3) lottery 命令前缀黑名单

**后端** `server/routes/webui_lottery.py`：
- 删除 line 42-58 整段 `_COMMAND_DENYLIST_PREFIXES` 常量定义 + 注释
- 删除 line 84-93 `_command_denylist_hit` 函数定义
- 修改 line 351-360 验证块：移除 H-2 check（删除 `else` 分支内的 hit 检查），直接执行 `command_template = stripped_cmd`。具体：

```python
elif len(command_template) > _CMD_MAX_LEN:
    details.append({"field": "command_template", "message": f"命令长度不能超过 {_CMD_MAX_LEN}"})
else:
    command_template = stripped_cmd
```

**前端** `server/webui/static/js/lottery.js`：
- 删除 line 34-38 `COMMAND_DENYLIST_PREFIXES` 常量 + 注释
- 删除 line 87-99 `detectDenylistedPrefix` 函数
- 删除 line 572-577 `applyKindVisibility` 内的 `if/else refreshCommandWarning / hideCommandWarning` 调用（保留 kind 切显示逻辑）
- 删除 line 602-615 `refreshCommandWarning` + `hideCommandWarning` 两个函数
- 删除 line 722-727 `savePrize` 提交前预检块（`const hit = detectDenylistedPrefix...`）
- 删除 line 1056 `els.prizeFieldCommandTemplate.addEventListener("input", refreshCommandWarning);`（保留其它该输入框上的 listener 如果有）

## Scope

仅 4 个文件：
- `server/routes/webui_users.py`
- `server/routes/webui_groups.py`
- `server/routes/webui_lottery.py`
- `server/webui/static/js/lottery.js`

## Acceptance

- users：GET `/webui/api/users?per_page=0` 返回完整 user 列表（不分页），meta.total 正确
- groups：POST `/webui/api/groups` 用 name="admin" / "owner" / "root" / "system" / "superuser" 可成功（不再 422 拒绝）；guest / default 仍拒绝（L-S-2 保留）
- lottery：POST `/webui/api/lottery/{pool_id}/prizes` kind=command 的 command_template 可填 `op @a` / `ban xxx` / `stop` 等而不被 422；前端 modal 不再 show 命令前缀警告

## DO NOT

- 不动 `_BUILTIN_GROUPS` (L-S-2 仍生效)
- 不动 H-3 client_ip + user_agent 日志
- 不动 H-4 NaN/Inf 校验
- 不动 H-1 replace_all 强确认
- 不动其它任何 audit 修复
- 不 commit

## Out of Scope

- 不动 `_KEYWORD_MAX_LENGTH` / `_PER_PAGE_MAX` 上限值
- 不动后端日志格式
- 不动 lottery / shop / users / groups 其它 endpoint

## Technical Notes

- 这些 audit 限制原本是 H-1 / H-2 严重度。回退后这些 endpoint 的对应攻击面回来（DoS / 保留字混淆 / 高权命令奖品），是用户明确取舍后的决定。
- 不需要更新 spec 文档。
