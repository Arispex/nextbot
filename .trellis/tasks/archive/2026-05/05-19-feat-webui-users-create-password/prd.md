# WebUI 创建用户 dialog 加密码字段 + 调 TShock create

## Goal

`POST /webui/api/users` 与 WebUI 创建用户 modal 加密码 / 确认密码字段，与命令端 `注册账号` 流程对齐 —— admin 输入密码 → bcrypt 入 bot DB → 推送 TShock create 到所有 server → 返回 server_results。

闭环 audit F-5 留下的 gap：WebUI 创建的用户从此不再走"未来 sync 兜底"路径，注册的瞬间就具备完整 TShock 账号。

## Decisions (ADR-lite)

| Decision | 选择 |
|---|---|
| **Q1 密码字段** | **Required + "生成"按钮**（admin 必填；旁边有按钮一键生成 16 位随机密码自动填入两个字段） |
| **Q2 二次确认** | **要**（前端校验两次一致；后端不接收 confirm 字段） |
| **Q3 失败回报** | **server_results 风格**（与 ban/unban/delete 一致：response 含 `{user, server_results: [{server_id, server_name, success, reason}]}`） |

## Requirements

### Frontend

文件：`server/webui/templates/users_content.html` + `server/webui/static/js/users.js`

#### Modal 字段（创建模式）

在现有 modal 的字段后追加：

```html
<div class="form-row" data-create-only>
  <label for="user-password">密码 <span class="required">*</span></label>
  <div class="input-with-action">
    <input type="password" id="user-password" minlength="8" autocomplete="new-password" />
    <button type="button" id="user-password-generate" class="btn-secondary">🎲 生成</button>
  </div>
</div>
<div class="form-row" data-create-only>
  <label for="user-password-confirm">确认密码 <span class="required">*</span></label>
  <input type="password" id="user-password-confirm" minlength="8" autocomplete="new-password" />
</div>
```

- `type="password"` 浏览器自动遮蔽
- `minlength="8"` 浏览器原生校验
- 仅创建模式显示；编辑模式 hidden（修改密码由未来命令处理）
- 🎲 生成按钮：点击 → 生成 16 位 `[A-Za-z0-9]` 随机密码 → 自动填入两个 input（密码 + 确认）→ input type 临时切到 `text` 让 admin 一眼看到生成的密码（点击后 3 秒内或 modal 关闭前都可见）；admin 仍能手动 backspace 修改

#### 前端校验逻辑（`saveUser` 或类似 handler）

```js
if (!isEdit) {
  const pwd = document.getElementById("user-password").value;
  const pwdConfirm = document.getElementById("user-password-confirm").value;
  if (!pwd) { setAlert("密码不能为空"); return; }
  if (pwd.length < 8) { setAlert("密码长度至少 8 位"); return; }
  if (pwd !== pwdConfirm) { setAlert("两次输入的密码不一致"); return; }
  payload.password = pwd;
}
// 编辑模式 payload 不带 password
```

#### Response 处理

继续复用现有 `unwrapData` + `Array.isArray(result.server_results)` 的展示模式（与 delete / rename 一致）。新创建成功 toast 同时显示 per-server 结果。

### Backend

文件：`server/routes/webui_users.py`

#### `_validate_payload`

加 password 字段处理（仅创建用，更新时不接收）：

```python
class ValidatedUserCreatePayload(ValidatedUserPayload):
    password: str  # plaintext，仅创建用，validated 后立即 hash

def _normalize_password(raw: Any) -> str:
    if not isinstance(raw, str):
        raise UserPayloadValidationError("password 必须是字符串")
    pwd = raw.strip()  # 可选：strip 防意外空格
    if len(pwd) < 8:
        raise UserPayloadValidationError("password 长度至少 8 位")
    # 不限制字符集（httpx 会 URL encode；bcrypt 接受任意字符）
    return pwd
```

#### `webui_users_create`

流程：

```python
async def webui_users_create(request: Request) -> JSONResponse:
    # 1. 解析 + 校验（含 password）
    payload, err = await read_json_object(request)
    if err: return err
    try:
        validated = _validate_payload(payload)  # 加 password 校验
        plaintext_password = _normalize_password(payload.get("password"))
    except UserPayloadValidationError as exc:
        return _validation_error(exc)

    # 2. bcrypt hash (复用命令端 helper 或在此局部 import bcrypt)
    from nextbot.plugins.user_manager import _hash_password  # 或就近 import
    password_hash = _hash_password(plaintext_password)

    # 3. session.add(user with password_hash=password_hash) + commit
    ...
    user = User(..., password_hash=password_hash, ...)
    session.add(user)
    session.commit()

    # 4. 序列化 user + 捕获 name 到栈（session 关闭前）
    serialized_user = _serialize_user(user)
    created_name = str(user.name)
    # session.close 在 finally

    # 5. broadcast: 复用命令端 _create_tshock_user_on_all_servers + _sync_user_whitelist
    try:
        from nextbot.plugins.user_manager import _create_tshock_user_on_all_servers
        whitelist_results = await _sync_user_whitelist(created_user)
        tshock_outcomes = await _create_tshock_user_on_all_servers(created_name, plaintext_password)
        # 合并两个 outcome list 到统一 server_results 格式
        server_results = ...
    except Exception:
        server_results = []
        logger.exception(...)

    # 6. plaintext 立即清空
    plaintext_password = ""

    # 7. response 201 with server_results
    return api_success(status_code=201, data={"user": serialized_user, "server_results": server_results})
```

要点：
- **password 字段必须在 payload 解析时立即提取并 hash**，不长时间持有
- helper 复用：`_hash_password` + `_create_tshock_user_on_all_servers` 在 `nextbot/plugins/user_manager.py`，跨模块 import
- DB commit 在 broadcast 之前；broadcast 失败仅 log，不回滚 DB
- 异常路径：plaintext 在 except 分支也要清空

#### `_serialize_user` 已不变（password_hash 不输出）

确认 `_serialize_user` **不**返回 password_hash（敏感字段，response 不该包含）。如果当前已经包含，本任务也修一下。

#### server_results 格式合并

两个 helper 返回不同 shape：
- `_sync_user_whitelist`: `[{server_id, server_name, success, reason}]`
- `_create_tshock_user_on_all_servers`: `list[BroadcastOutcome[str]]`

需要在 `webui_users_create` 内合并 / normalize 成统一 shape 输出。每个 server 一行，标注是哪类操作。例如：

```python
def _server_results_from_outcomes(outcomes, op_name: str):
    return [
        {
            "server_id": int(o.server.id),
            "server_name": str(o.server.name),
            "success": o.ok,
            "reason": "" if o.ok else o.detail,
            "op": op_name,  # "whitelist_add" / "tshock_create"
        }
        for o in outcomes
    ]
```

但这样前端可能想分别展示两类。**简化方案**：合并所有 outcomes 不分 op，前端按 server_id 聚合（同 server 两类操作各一行）。或者直接拼接两个 list 各一行 / 每 server 双行。

**推荐**：每 server 输出 1 行，aggregated：`success = whitelist_ok && tshock_ok`，`reason` = 拼接 / 最严重一个。简化前端展示。

```python
def _combine_per_server(
    whitelist_outcomes, tshock_outcomes
) -> list[dict]:
    # both lists are aligned per server (来自相同的 server_broadcast)
    # zip + aggregate
    ...
```

如果 zip 困难（顺序不一定一致），按 server_id 字典聚合后输出。

## Out of Scope

- 编辑用户改密码（修改密码命令是另一个 task）
- WebUI 改密码 endpoint
- 密码强度高级规则（复杂度评分 / 字典检查）—— 仅做长度 ≥ 8
- "查看密码" 显示 / 隐藏 toggle 按钮（仅"生成"按钮后短暂 `text` reveal 即可，不做完整 toggle）
- 与命令端注册的 deduplication（admin 创建 vs 用户自助注册的冲突处理 — 现有 unique constraint 兜底足够）

## Acceptance Criteria

- [ ] `users_content.html` 创建 modal 加 password / confirm_password 两个 input（type=password）
- [ ] `users.js` saveUser 加前端校验：required + minlength 8 + 两次一致
- [ ] `webui_users.py` `_validate_payload` 接收 password 字段（创建时 required，更新时 ignored）
- [ ] `webui_users_create` 调用 `_hash_password` + `_create_tshock_user_on_all_servers`
- [ ] Response shape `{user, server_results: [...]}`，与现有 ban/unban/delete 一致
- [ ] plaintext 不 log、不 echo 给客户端、栈生命周期最短
- [ ] `_serialize_user` 不输出 password_hash
- [ ] `python3 -m py_compile` 通过
- [ ] `node --check server/webui/static/js/users.js` 通过

## Edge Cases

| 场景 | 行为 |
|---|---|
| 密码空 / 缺失 | 后端 422 validation_error |
| 密码 < 8 位 | 同上 |
| 编辑模式提交带 password 字段 | 后端忽略（仅创建路径接受） |
| TShock create push 失败 | DB user 已创建，server_results 标注失败；admin 看到部分成功提示 |
| 同名用户已存在 TShock 上（admin 用相同名再创建）| TShock 返回 already exists → outcome 标 fail；用户实际还能用旧密码登入，bot DB 记的是新密码（不一致）。这是 F-1 / F-2 同类问题的子集，OOS |
| 私聊密码 | 本 task **不**发私聊（与命令端不同：admin 自己输入了，知道密码） |

## Technical Notes

- 命令端 `_create_tshock_user_on_all_servers` 在 `nextbot/plugins/user_manager.py:146`
- 命令端 `_hash_password` 在 `nextbot/plugins/user_manager.py:101`
- 跨模块 import: `from nextbot.plugins.user_manager import _hash_password, _create_tshock_user_on_all_servers` —— 私有 helper 跨模块复用是已有 pattern（webui_users 已 import `_sync_user_whitelist` 是自己模块内 helper）
- 前端 toast / modal 风格参考现有 delete / rename 的 per-server 结果展示
