# WebUI 用户修改密码功能

## Goal

WebUI 用户管理页面增加"修改密码"功能：admin 点击表格每行的按钮 → 弹 dialog 输入新密码（含生成按钮） → 后端 bcrypt hash 写 DB + 调每个 server 的 TShock `/v2/users/update` → 返回 server_results。

## Decisions (ADR-lite)

| Decision | 选择 | 备注 |
|---|---|---|
| **Q1 入口位置** | **A**：每行加"修改密码"按钮，与 ban/unban/delete 同行 | 一目了然 |
| **Q2 旧密码验证** | **A**：不需要 | admin token 已是最高权限；与 ban/unban/delete 决策一致 |
| **Q3 Owner 保护** | **B**：不保护 | **偏离现有 ban/unban/delete 的 owner_protected 模式**。理由：admin 想用 WebUI 改自己密码 / owner 密码，不应该被强制走 config file 路径 |

## Requirements

### Backend

新增 endpoint：

```
POST /webui/api/users/{user_id}/change-password
Body: {"password": "<new_plaintext>"}
Auth: 现有 webui auth 中间件
```

**Response (200 OK)**：
```json
{
  "user": { ... serialized_user ... },
  "server_results": [
    {"server_id": 1, "server_name": "本地", "success": true, "reason": ""},
    {"server_id": 2, "server_name": "云端", "success": false, "reason": "无法连接服务器"}
  ]
}
```

**Response (4xx)**：
- 404 user 不存在
- 422 密码 validation 失败

**流程**：
1. validate path param `user_id` ≥ 1
2. validate body `password`（复用 `_normalize_password`，长度 ≥ 8）
3. session.query User，404 if not found
4. bcrypt hash → `user.password_hash` → commit
5. 序列化 user + 捕获 name（session 关闭前）
6. broadcast TShock update 到所有 server（每个 server 调 `/v2/users/update?user=<name>&type=name&password=<plaintext>`）
7. plaintext 立即清空
8. log（mask QQ + client_ip + success_count）
9. return 200 + {user, server_results}

异常路径 / fail-safe：DB commit before broadcast；broadcast 失败仅打 log + 反映在 server_results；plaintext 在 finally 清空。

**新 helper：`_update_tshock_user_password_on_all_servers(name: str, plaintext: str)`**（在 `webui_users.py` 内）：

```python
async def _update_tshock_user_password_on_all_servers(
    name: str, plaintext: str
) -> list:
    """对所有 server 调 /v2/users/update 改密码。"""
    session = get_session()
    try:
        servers = session.query(Server).order_by(Server.id.asc()).all()
    finally:
        session.close()
    if not servers:
        return []

    async def _one(server: Server) -> BroadcastOutcome[str]:
        try:
            resp = await request_server_api(
                server,
                "/v2/users/update",
                params={"user": name, "type": "name", "password": plaintext},
            )
        except TShockRequestError:
            return BroadcastOutcome(server=server, ok=False, detail="无法连接服务器", payload=None)
        if is_success(resp):
            return BroadcastOutcome(server=server, ok=True, detail="改密码成功", payload="updated")
        return BroadcastOutcome(server=server, ok=False, detail=get_error_reason(resp), payload=None)

    outcomes = await broadcast(servers, _one)
    return outcomes
```

注意 helper 直接 import 而非复用 user_manager 私有 helper（user_manager 的 helper 是 register 专用，这里是 update）。

### Frontend

#### 表格 actions 加按钮

`users.js` 渲染表格 row 的地方加按钮（位置：和 ban/unban/delete 一起）：

```html
<button class="btn-row-action btn-change-password" data-user-id="${user.id}" title="修改密码">🔑</button>
```

#### Change password dialog

复用 / 仿造现有 modal 结构（参考 ban / delete 的 dialog）：

- title: "修改密码"
- body:
  - 显示目标用户：用户名 + QQ (mask)
  - 密码 input + 生成按钮（mirror create 流程的生成按钮）
  - 确认密码 input
- footer: 取消 + 提交

提交逻辑：
1. 前端校验：必填、长度 ≥ 8、两次一致
2. POST `/webui/api/users/{user_id}/change-password` body `{password}`
3. 处理 response：渲染 server_results 风格 toast（"服务器账号：" 段，与 create 的 tshock_results 渲染一致）
4. 关闭 dialog，清空 input + 切回 password type

### Validation 复用

- `_normalize_password` 已在 `webui_users.py` 中（create 任务时建的）
- 复用

## Out of Scope

- 用户自助改密码（命令端 `修改密码` 命令 — 另一个 task）
- 改密码后强制下线现有 session（OOS — TShock 端的 session 管理）
- 批量改密码（OOS — 单用户即可）
- 强制密码定期改 / 密码历史（OOS）

## Acceptance Criteria

- [ ] 新 endpoint `POST /webui/api/users/{user_id}/change-password` 实现
- [ ] 新 helper `_update_tshock_user_password_on_all_servers`
- [ ] `_normalize_password` 被复用（如果在 webui_users.py 内）
- [ ] Frontend 表格行加按钮 + dialog + 提交逻辑
- [ ] Response shape `{user, server_results}`
- [ ] 404 / 422 错误响应正确
- [ ] plaintext 全路径生命周期最小化
- [ ] log 不含 plaintext
- [ ] `_serialize_user` 不输出 password_hash（已验证 from create task）
- [ ] **无 owner 保护**（Q3=B）—— owner 可被改密码
- [ ] `python3 -m py_compile server/routes/webui_users.py` clean
- [ ] `node --check server/webui/static/js/users.js` clean

## Edge Cases

| 场景 | 行为 |
|---|---|
| 密码空 / < 8 位 | 422 validation_error |
| user_id 不存在 | 404 not_found |
| TShock update push 失败 | DB 已 commit；server_results 标 fail；admin 看到部分成功 |
| Owner 改自己密码 | 允许（Q3 决策）|
| 同名 server 上的 TShock account 不存在 | TShock 返回 `User <name> not found` → outcome fail；admin 应看到原因，知道这个用户在该 server 还没创建 |
| broadcast 异常 | 仅 log，server_results 为空数组，仍返回 200（DB 已 commit）|

## Technical Notes

- TShock `/v2/users/update` REST API 文档：
  ```
  GET /v2/users/update?user=<name>&type=name&password=<new>
  ```
  - `user`: name or id
  - `type`: "name" or "id"
  - `password`: 新明文密码
  - `group`: 不传（仅改密码）

- TShock update endpoint 接受 GET（与 create 一致），plaintext 经 URL query — F-1/F-2 边界问题（已 acknowledged，OOS）
- helper 不要复用 `_create_tshock_user_on_all_servers`（那是 create endpoint）；写新 helper `_update_tshock_user_password_on_all_servers` 调 `/v2/users/update`
