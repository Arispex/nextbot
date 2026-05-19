# WebUI 用户改名拎出独立 dialog

## Goal

把"编辑用户" dialog 里的 name 字段编辑功能拆到独立的"修改用户名" dialog（与"修改密码"风格一致）。让编辑用户只改纯本地 DB 字段（coins / sign / group / permissions），改名是独立操作（需要 broadcast 到所有 server 做白名单 rename push）。

## Decisions (ADR-lite)

| Decision | 选择 |
|---|---|
| **Q1 编辑 dialog name 字段** | **B**：保留为 read-only / disabled 显示（提供上下文，但不可编辑） |
| **Q2 Owner 保护** | **A**：不保护（与 change-password / ban / unban / delete 一致；update handler 自己的 owner 保护仍保留） |

## Requirements

### Backend

#### 新增 endpoint：`POST /webui/api/users/{user_id}/change-name`

```
Body: {"name": "<new_name>"}
Auth: 现有 webui auth 中间件
```

**Response 200 OK**：
```json
{
  "user": { ... serialized_user ... },
  "server_results": [
    {"server_id": 1, "server_name": "本地", "success": true, "reason": "改名同步成功"},
    ...
  ]
}
```

**Response 4xx**：
- 404 user 不存在
- 409 用户名称已被占用
- 422 name validation 失败（空 / 格式错）

**流程**：
1. validate path param `user_id` ≥ 1
2. validate body `name`（复用 `_normalize_user_name`）
3. session.query User → 404 if not found
4. 检查 name 唯一（其他 user 同名 → 409）
5. **不**检查 owner 保护
6. 捕获 `original_name`
7. 写 `user.name = new_name` + commit
8. 序列化 user（session 关闭前）
9. 如果 `new_name != original_name`：broadcast `_broadcast_whitelist_rename(original_name, new_name)`
10. 否则：server_results = [] (改成同名 = no-op)
11. log + return 200

#### 修改 `webui_users_update`

- 不再写 `user.name = validated.name`（保持 user.name 不变）
- 不再检查"name 唯一"（不会冲突）
- 不再调 `_broadcast_whitelist_rename`（移到 change-name endpoint）
- 不再返回 `server_results`（update 现在只改纯本地 DB 字段）
- Update endpoint 现在 response 是 `{user: ...}`（与改造前相比删除 server_results 字段）

**注意**：`_validate_payload` 仍然校验 name 字段（用户可能仍在 payload 里传 name，validator 不报错），但 update handler 不应用 validated.name 到 user.name —— 即"接受但忽略"。Frontend 编辑 dialog 即使带 name 字段也无害。

**或者更严格**：update 时若 `payload.name != user.name`，返回 422 "改名请使用独立端点"。**推荐宽松路径（接受但忽略）**，避免给前端额外负担。

### Frontend

#### 修改：编辑用户 dialog

`server/webui/templates/users_content.html` 找到 name input → 加 `readonly` 属性 + 视觉 disable 样式：

```html
<!-- 旧 -->
<input type="text" id="field-name" required />

<!-- 新 -->
<input type="text" id="field-name" readonly />
```

注意：保留 input 让 form layout 不破坏；编辑时显示当前用户名（read-only）；admin 想改 → 通过新按钮。

可选 inline 提示：`<span class="hint">改名请使用"修改用户名"按钮</span>`。

#### 新增：每行"修改用户名"按钮

users.js 表格 row 加按钮（位置：和"修改密码"按钮并排）：

```html
<button type="button" data-action="change-name" data-user-id="${userId}" title="修改用户名">修改用户名</button>
```

#### 新增：change-name modal HTML

`server/webui/templates/users_content.html` 在 change-password modal 旁边加：

```html
<dialog id="change-name-modal" class="modal">
  <form method="dialog">
    <h2>修改用户名</h2>
    <p>目标用户：<span id="change-name-target-name"></span> <span id="change-name-target-qq" class="meta-id"></span></p>
    <div class="form-row">
      <label for="change-name-input">新用户名 <span class="required">*</span></label>
      <input type="text" id="change-name-input" maxlength="32" />
    </div>
    <div id="change-name-alert" class="modal-alert" hidden></div>
    <div class="modal-actions">
      <button type="button" id="change-name-cancel" class="btn">取消</button>
      <button type="button" id="change-name-submit" class="btn btn-primary">保存</button>
    </div>
  </form>
</dialog>
```

#### users.js handler

```js
function openChangeNameModal(user) {
  document.getElementById("change-name-target-name").textContent = user.name;
  document.getElementById("change-name-target-qq").textContent = `(${maskQq(user.user_id)})`;
  document.getElementById("change-name-input").value = user.name;
  // ... openModalWithFocus etc.
}

async function confirmChangeName(userId) {
  const newName = document.getElementById("change-name-input").value.trim();
  if (!newName) { setChangeNameModalAlert("修改失败，用户名不能为空"); return; }
  // 长度等其他校验
  
  const responsePayload = await api.apiRequest(
    `/webui/api/users/${userId}/change-name`,
    { method: "POST", body: { name: newName }, expectedStatus: 200, action: "修改" }
  );
  
  const result = api.unwrapData(responsePayload) || {};
  const serverResults = Array.isArray(result.server_results) ? result.server_results : [];
  
  // 渲染 toast: "用户名已修改" + "服务器白名单：" + per-server lines
  // 关闭 modal + loadUsers
}
```

### Reuse

- `_normalize_user_name` (已存在)
- `_broadcast_whitelist_rename` (已存在 in webui_users.py)
- `_outcomes_to_server_results` —— 不适用（_broadcast_whitelist_rename 已返回 `list[dict]` 形式，直接用）
- create / change-password 的 dialog open/close / openModalWithFocus / registerModalCloser 等 helper

## Out of Scope

- 用户改名后的旁路影响（如 inventory / signin 记录里残留旧名），由 DB schema FK 或业务 layer 处理
- TShock side 改名（仅同步白名单 remove old + add new；TShock 账号自身名字不变，需要用户在游戏内 `/user changename` 或类似命令）—— 这是已有 `_broadcast_whitelist_rename` 的语义边界，本任务不动
- 编辑 dialog 完全移除 name 字段（Q1=B 决策：保留 readonly）

## Acceptance Criteria

- [ ] 新 endpoint `POST /webui/api/users/{user_id}/change-name` 实现
- [ ] `webui_users_update` **不**再写 user.name / 不再 broadcast rename / 不再返回 server_results
- [ ] 编辑用户 dialog name input 改 `readonly`
- [ ] 表格行新增"修改用户名"按钮（与"修改密码"并排）
- [ ] 修改用户名 dialog HTML + JS 完整
- [ ] response shape `{user, server_results}` 与 change-password 一致
- [ ] 404 / 409 / 422 错误响应正确
- [ ] **无 owner 保护**（Q2=A）
- [ ] `python3 -m py_compile server/routes/webui_users.py` 通过
- [ ] `node --check server/webui/static/js/users.js` 通过
- [ ] users_content.html parse 通过

## Edge Cases

| 场景 | 行为 |
|---|---|
| new_name == original_name | server_results=[] no-op；DB commit 仍发生但无效；可选优化：直接 return without DB write |
| new_name 已被别的 user 占用 | 409 conflict |
| user_id 不存在 | 404 |
| name 空 / 含非法字符 | 422 |
| 改名期间 server 离线 | broadcast 失败仅 log + server_results 反映；DB name 已 commit |
| Frontend 编辑 dialog 中带 name 字段提交 update endpoint | name 字段接受但忽略（不写入 user.name） |
| Owner 改名 | 允许（Q2=A） |
