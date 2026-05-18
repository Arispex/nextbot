# WebUI 创建用户补 server 白名单 push

## Goal

`POST /webui/api/users`（`webui_users_create` at `server/routes/webui_users.py:354`）目前只 commit DB，不 push server 白名单。补齐 push，与命令端 `注册账号` 行为一致，与上一 task 修的 delete/rename push 行为一致。

## 现状

`webui_users_create` (L354-413):
```python
session.add(user)
session.commit()
logger.info(f"创建用户成功：...")
return api_success(status_code=201, data=_serialize_user(user), headers={"Location": ...})
```

→ 没 push server 白名单。新建用户必须额外手动点 sync-whitelist。

参考：
- 命令端 `nextbot/plugins/user_manager.py:212+ handle_add_whitelist` 用 `_sync_whitelist_to_all_servers` 自动 push add
- 上一 task 的 `_sync_user_whitelist` (`webui_users.py:229`) 已是 add 用 helper

## 实现

### 直接复用已有的 `_sync_user_whitelist`

`_sync_user_whitelist(user)` 已在 `webui_users.py:229` 定义（手动 sync-whitelist 端点也用它），返回 `list[dict[str, Any]]` 的 server_results。逻辑：
- broadcast 所有 server 调 `/nextbot/whitelist/add/<name>`
- 已存在 → 视为成功
- 失败原因结构化返回

**不需要写新 helper，直接复用**。

### 修改 `webui_users_create` (L354-413)

在 `session.commit()` 之后、`return api_success` 之前，加：

```python
# commit 后捕获用户数据（避免 detached instance）
serialized_user = _serialize_user(user)
# 复用已有 helper，broadcast 白名单 add 到所有 server
server_results = await _sync_user_whitelist(user)
success_count = sum(1 for r in server_results if r["success"])
logger.info(
    f"WebUI 创建用户白名单同步完成：user_id={_mask_qq(user.user_id)} name={user.name} "
    f"success={success_count}/{len(server_results)} client_ip={client_ip}"
)
```

⚠️ **session 生命周期**：`_sync_user_whitelist` 内部用 `user.name`，需要确保调用时 user 还附着在 session（未 close）。如果是已 commit 但 session 还开着（在 try 块内），是 OK 的。如果 `_sync_user_whitelist` 实际上是先 `_serialize_user(user)` 再 broadcast，应该没问题。implement agent 阅读 `_sync_user_whitelist` 源码确认依赖。

### 响应改造

```python
return api_success(
    status_code=201,
    data={"user": serialized_user, "server_results": server_results},
    headers={"Location": f"/webui/api/users/{user.id}"},
)
```

**响应 shape 变化**：`data` 从 `<user>` → `{user: ..., server_results: [...]}`。与 ban / unban / 上一 task 的 delete / rename 一致。

**Status code 仍是 201**（新建资源），不变。

### Frontend 改造

`server/webui/static/js/users.js` 已经在用 `api.unwrapData(payload)` 取数据。检查 create 流程的成功路径：
- 是否直接读 `result.user_id` / `result.name`？如果是，需改读 `result.user.user_id` / `result.user.name`
- 是否需要展示 server_results？参考上一 task 的 delete 路径（show per-server outcome lines）

implement agent 处理这两点。

## 安全 / 鲁棒性

继承上一 task 的标准：
1. ✅ DB commit 前 push 不做（commit 先于 push）
2. ✅ Push 失败不回滚 DB
3. ✅ URL path quote 已在 `_sync_user_whitelist` 内部处理
4. ✅ Empty server → `[]`
5. ✅ Audit log 含 client_ip / mask QQ
6. ✅ session.close 在 finally
7. ✅ 不同 transaction：`_sync_user_whitelist` 用自己的 session 查 Server

## Out of Scope

- 不动 ban / unban / delete / rename / 其他 endpoint
- 不改命令端 `_sync_whitelist_to_all_servers`
- 不动 `_sync_user_whitelist` 实现本身（复用即可）
- 不改 page 模块 / 其他模板

## Acceptance Criteria

- [ ] `webui_users_create` 在 commit 后 call `_sync_user_whitelist(user)`
- [ ] 响应 data shape = `{user, server_results}`（与 delete/rename 一致）
- [ ] status_code 仍是 201
- [ ] frontend 适配新 data shape（如有需要）
- [ ] `python3 -m py_compile server/routes/webui_users.py` 通过
- [ ] `git diff --name-only` 仅 2 文件（webui_users.py + 可能 users.js）

## Self-Check Loop

trellis-check 重点审：
- [ ] **安全**：URL path / SSRF / 权限 / PII / 日志注入
- [ ] **正确性**：commit ↔ push 顺序、detached instance、idempotency
- [ ] **鲁棒性**：异常路径、session.close、broadcast 不抛
- [ ] **一致性**：与 delete/rename/ban/unban handler 风格对齐
- [ ] **frontend 适配**：response shape 变化后 users.js create 流程是否正确处理

发现真实 bug → self-fix，重新 verify。直到 0 issue。
