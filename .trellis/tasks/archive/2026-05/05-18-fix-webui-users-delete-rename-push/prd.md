# WebUI 删除/改名用户 push 白名单到服务器

## Goal

补齐 WebUI 两个用户管理端点缺失的服务器白名单 push（与命令端 `_sync_whitelist_to_all_servers` / `_rename_one_whitelist` 行为一致）：

1. **删除用户** (`DELETE /webui/api/users/{id}`)：commit DB delete 后 broadcast `/nextbot/whitelist/remove/<name>` 到所有 server
2. **改名用户** (`PUT /webui/api/users/{id}`)：若 `name` 发生变化，commit DB 改名后 broadcast `/nextbot/whitelist/remove/<old>` + `/nextbot/whitelist/add/<new>`

## 当前 gap

- `server/routes/webui_users.py:537-590` `webui_users_delete`：仅 `session.delete(user); session.commit()`，server 白名单上仍留旧名 → 被删账号可无限制重连
- `server/routes/webui_users.py:430-528` `webui_users_update`：`user.name = validated.name; session.commit()`，server 白名单仍是旧名 → 新名进不去 / 旧名仍能进

命令端已有正确模式：
- `nextbot/plugins/user_manager.py:106-128` `_sync_one_whitelist` (add 单 server)
- `nextbot/plugins/user_manager.py:130-148` `_sync_whitelist_to_all_servers` (broadcast add)
- `nextbot/plugins/user_manager.py:161-200` `_rename_one_whitelist` (remove old + add new 单 server)

## 实现

### 共用：抽出 broadcast helper（推荐）

在 `webui_users.py` 内（不污染命令端）抽 2 个 helper（mirror ban/unban handler 的 broadcast/aggregate 模式）：

```python
async def _broadcast_whitelist_remove(name: str) -> list[dict[str, Any]]:
    """对所有 server 调 /nextbot/whitelist/remove/<name>，返回 server_results。"""
    servers = _load_servers()  # 或 from db import Server, get_session
    if not servers:
        return []
    encoded = quote(name, safe="")
    
    async def _one(server: Server) -> BroadcastOutcome[str]:
        try:
            resp = await request_server_api(
                server, f"/nextbot/whitelist/remove/{encoded}"
            )
        except TShockRequestError:
            return BroadcastOutcome(server=server, ok=False, detail="无法连接服务器", payload=None)
        if is_success(resp):
            return BroadcastOutcome(server=server, ok=True, detail="移除成功", payload="removed")
        # 注意：白名单本来就没该用户 → 视为成功（与已删除目标语义一致）
        error_text = get_error_reason(resp).lower()
        if "not" in error_text and ("found" in error_text or "exist" in error_text):
            return BroadcastOutcome(server=server, ok=True, detail="白名单中不存在", payload="not_present")
        return BroadcastOutcome(server=server, ok=False, detail=get_error_reason(resp), payload=None)
    
    outcomes = await broadcast(servers, _one)
    return [
        {"server_id": int(o.server.id), "server_name": str(o.server.name), "success": o.ok, "reason": o.detail}
        for o in outcomes
    ]

async def _broadcast_whitelist_rename(old_name: str, new_name: str) -> list[dict[str, Any]]:
    """改名：每 server 先 remove old 再 add new，返回 per-server 综合结果。"""
    # 复用 user_manager._rename_one_whitelist 的语义；若不便复用则就地实现
    ...
```

或者：**直接 import 并复用** `nextbot/plugins/user_manager.py` 的 `_sync_one_whitelist` / `_rename_one_whitelist` —— 但这两个是 `_`-prefix 私有 helper，跨模块导入有规范洁癖。**implement agent 二选一**：

- 选项 A：复用（import 私有 helper，能用就行）
- 选项 B：在 webui_users.py 内重写 broadcast helpers（与 ban/unban 风格一致）

推荐 B，scope 局限在 WebUI，命令端不受影响。

### 修改 1：`webui_users_delete` (L537-590)

```python
deleted_user_id = str(user.user_id)
deleted_name = str(user.name)
session.delete(user)
session.commit()
logger.info(f"删除用户成功：user_id={user_id}，...")

# 新增 push：commit 后 best-effort 清理 server 白名单
server_results = await _broadcast_whitelist_remove(deleted_name)
logger.info(
    f"WebUI 删除用户白名单清理完成：name={deleted_name} "
    f"server_count={len(server_results)} client_ip={client_ip}"
)

# 响应改为 200 + JSON（带 server_results），不再返回 204
return api_success(data={"server_results": server_results})
```

**响应格式变更**：204 No Content → 200 OK + JSON。
- 前端若有任何依赖 204 的代码需要同步改（grep 检查 `webui/static/js/users.js`）
- 与 ban/unban 响应风格一致

如果不想破坏 API 兼容，**alternative**：保留 204，仅 log，不返回 server_results。看 implement agent 权衡（推荐改 200 与 ban 一致；若 frontend 代码改动量大则保 204 + log）。

### 修改 2：`webui_users_update` (L430-528)

捕获旧 name，commit 后比较：

```python
original_name = str(user.name)  # 在改字段前捕获

user.user_id = validated.user_id
user.name = validated.name
user.coins = validated.coins
# ... 其他字段
session.commit()
logger.info(f"更新用户成功：user_id={user_id}，...")

# 仅 name 变化时才 push
server_results: list[dict[str, Any]] | None = None
if validated.name != original_name:
    server_results = await _broadcast_whitelist_rename(original_name, validated.name)
    logger.info(
        f"WebUI 改名白名单同步完成：old_name={original_name} new_name={validated.name} "
        f"server_count={len(server_results)} client_ip={client_ip}"
    )

return api_success(data={
    "user": _serialize_user(user),
    **({"server_results": server_results} if server_results is not None else {}),
})
```

**响应**：现有 `api_success(data=_serialize_user(user))` → 改成 `{user: ..., server_results?: [...]}`，与 ban/unban shape 一致。

## 安全 / 鲁棒性要求

1. **URL path 编码**：`quote(name, safe="")` 防 `/` `?` `#` 注入路径
2. **白名单上不存在的 user remove**：视为成功（idempotent），不当 failure
3. **DB 操作已成功 → push 失败不应让 DB 操作回滚**：commit 必须在 push 之前；push 失败仅写 audit log + 返回 `server_results` 中标 failure，让 WebUI 可见
4. **不在 logger 里泄露 PII**：name 可写日志（运维需要追查），但避免在 ERROR 级别 dump 完整 stacktrace 含用户名
5. **broadcast 超时**：默认 5s timeout 即可（与命令端一致）
6. **空 server 列表**：返回 `server_results=[]`，不抛错
7. **Owner 删除 / 改名仍要 owner_protected 拒绝**（已有，保留）

## Out of Scope

- 不修 WebUI create（P1，后续单独 task）
- 不修 WebUI ban / unban 是否同时操作白名单（P2，需先确认 C# 插件语义）
- 不抽 helper 到命令端（webui_users.py 内部 helper 即可）
- 不动命令端 `_sync_*` / `_rename_*`
- 不改 frontend `users.js`（若 200/204 变更，agent 需在 PRD 报告中提示用户）

## Acceptance Criteria

- [ ] `webui_users_delete` 删 DB 后 broadcast `/whitelist/remove`
- [ ] `webui_users_update` 检测到 name 变化时 broadcast remove old + add new
- [ ] DB commit 失败时不 push（早期 return / 抛异常路径）
- [ ] Push 失败时 DB 不回滚
- [ ] `quote(name, safe="")` 用于 URL path
- [ ] Audit log 含 client_ip / user_agent / 操作摘要
- [ ] Owner 保护逻辑保留
- [ ] `python3 -m py_compile server/routes/webui_users.py` 通过
- [ ] 不修改任何其他 .py / .html / .js（除非 200/204 变更需要 frontend 适配）

## Self-Check Loop

trellis-check 重点审：
- [ ] **安全**：注入（URL path / SQL）、SSRF（broadcast 仅到 DB 中的 server，OK）、权限提升、PII 泄漏
- [ ] **正确性**：DB / push 顺序、commit 边界、push 失败处理、edge case（无 server / name 含特殊字符 / 改名到自己）
- [ ] **鲁棒性**：异常路径完整、broadcast 不抛、session.close 在 finally
- [ ] **一致性**：与 ban/unban handler 的 broadcast pattern 对齐、response shape 一致、log 格式对齐
- [ ] **规范**：日志（user.md 后端日志规则）、API 设计（api-design skill）、错误响应 schema

发现真实 bug / 安全漏洞 → self-fix，重新 verify。直到 0 issue。
