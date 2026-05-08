# 主代理二次复查结论

**日期**: 2026-05-08
**复查范围**: security-a-findings.md (17 项) + security-b-findings.md (28 项)

---

## 复查方法

主代理逐项读源码 + 实测：
- `db.py:36/77/78` 验证 DEFAULT_GUEST_PERMISSIONS 含 `ban.list` / `security.login.confirm` / `security.login.reject`
- `security.py:39-62` 验证 `_broadcast_login_action` 串行 fan-out
- `ban_core.py:29-110` 验证 `apply_ban_to_db` 与 `sync_user_to_blacklist` 双写无 CRITICAL log
- `ban.py:217-316` 验证 `handle_unban` 同步路径 + commit 顺序
- `ban_core.py:48-51` + `ban.py:249-252` 验证 lost-update 模式

---

## ✅ 真实 critical（必修）

| ID | 复查结论 |
|---|---|
| **SA-COMMON.1** + **SB-2.1** 默认 guest 权限敏感命令 | ✅ 真。`db.py:36` 含 `ban.list`，`db.py:77/78` 含 `security.login.confirm/reject`。三个敏感命令默认对游客开放。`ban.list` 暴露所有被封者隐私 + 启动 Playwright 渲染（DoS 入口），`security.login.*` 让 guest 绕过审核流程 + 触发 N 服务器 fan-out 放大 DoS。 |
| **SB-1.1** + **SB-3.2** DB-API 双写无 CRITICAL 日志 | ✅ 真。`apply_ban_to_db` (ban_core.py:48-51) commits `is_banned=True`，`sync_user_to_blacklist` 全失败时只 `logger.info` 一句"黑名单同步完成"，与 W-7 仓库审计已修复模式正好相反。解封路径 (ban.py:252-315) 同结构。 |

## ✅ 真实 high（应修）

| ID | 复查结论 |
|---|---|
| **SA-1.1 + SA-2.1 + SB-1.3 + SB-3.4 + SC-4.2** 全部串行 fan-out | ✅ 真。`security.py:45` `for server in servers:`、`ban_core.py:74`（封禁两次往返 ×N 串行）、`ban.py:276` 同结构。这是项目内**最后一处**未应用 PQA-1.1 / PQA-2.1 模式。 |
| **SB-1.2 + SB-3.5** TShock URL 路径段未 percent-encoded | ✅ 真。`ban_core.py:95` `f"/nextbot/blacklist/add/{user_name}"`、`ban.py:300` `f"/nextbot/blacklist/remove/{user_name}"`，未走 `quote(safe="")`。`tshock_api.py:58` 的 `quote(safe="/")` 保留 `/`，无防御。已在 player_query 修过同类（PQB-2.2），ban 漏。 |
| **SB-1.4 + SB-3.3** lost-update on User.is_banned | ✅ 真。`ban_core.py:48-51` / `ban.py:249-252` 都是 read-modify-write 风格，没有 `update().where(User.is_banned == False).values(...)` 条件 UPDATE。两 admin 并发封禁同一目标，原因互相覆盖；封禁 + 解封并发竞争。 |
| **SB-2.2** 封禁列表无 OOM 上限 + 无 semaphore | ✅ 真。`_to_base64_image_uri` 一次 read_bytes + b64encode，未应用 `large_image.MAX_BASE64_BYTES`，未应用 `_inventory_semaphores` 模式。配合 SB-2.1 guest 可触发 → 任意 guest 可 OOM。 |
| **SC-4.1** ban_core 缺聚合层 | ✅ 真。`apply_ban_to_db` + `sync_user_to_blacklist` 两个独立公共 API，调用方各自实现 / 不实现告警，是 SB-1.1 / SB-3.2 的根因。 |

## ✅ 真实 medium

| ID | 复查结论 |
|---|---|
| **SA-1.2 / SA-1.7** 错误聚合 / 部分成功不展示 | ✅ 真。`_pick_failure_reason` 只取第一条，部分成功视作完全成功。ban.py 的逐行明细模式更优。 |
| **SA-1.3 / SA-2.4 / SB-1.5 / SB-3.6 / SC-4.5** 审计日志缺操作员 | ✅ 真。所有 5 处 logger.info 都把"被操作者"写为 `user_id`，不区分 actor / target。owner_protected 拦截无 logger.warning（SC-4.5）。 |
| **SB-2.4** 封禁列表全表 ORM 物化 | ✅ 真。`ban.py:144-156` `.all()` + Python 切片，万级封禁数性能差。改 `count() + offset/limit` 即可。 |
| **SB-3.7** name 含空格无法解封 | ✅ 真。`User.name` regex 实际限制 `[A-Za-z0-9一-鿿]+` 不允许空格（user_manager.py），所以这个其实**触发不到**——子代理误判一半。降级到 low。 |
| **SC-4.6** 缺 `apply_unban_to_db` 对偶函数 | ✅ 真。ban.py / webui_users.py 各写一份解封逻辑，重构进 ban_core 可统一。 |

## 🔧 严重度调整

| ID | 子代理评级 | 主代理评级 | 理由 |
|---|---|---|---|
| **SB-3.1** commit 后读 user.name | 🔴 critical | 🟡 medium | 子代理自己改口"实际不会抛异常，会多 1 次 SQL"。session 还活着，lazy-load 触发额外 SELECT 而非 DetachedInstanceError。真正风险（commit 后异常→静默退出）极低概率。但确实应在 commit 前 capture 字段。 |
| **SA-2.2** 拒绝登入 abuse | 🟠 high | 🟡 medium | 当前对自己生效，spam 仅自伤；"未来扩展为 admin 模式"是假设。配合 SA-COMMON.1 修复后即不可滥用。 |
| **SA-1.4** TShock 端是否幂等 | 🟡 medium | ℹ️ defer | 取决于 TShock 端语义，bot 端无法独立判断，留作运营层观察。 |
| **SB-3.7** name 含空格解析 | 🟡 medium | 🟢 low | `_validate_user_name` 正则 `[A-Za-z0-9一-鿿]+` 已禁空格，触发不到。 |
| **SB-1.7** not_found vs name_not_found | 🟢 low | ℹ️ info | 文案差异，不属于漏洞 / 性能。 |
| **SB-1.8** 重复 parse 日志 | ℹ️ info | ℹ️ info | 保留。 |
| **SC-4.4** owner_protected 后 dirty ORM | 🟡 medium | ℹ️ info | 当前 autoflush=False 安全；仅未来维护风险。 |

## ❌ 误判 / 不予采纳

| ID | 子代理评级 | 主代理结论 |
|---|---|---|
| **SA-CC-3** at_prefix 不一致 | ℹ️ info | 与漏洞 / 性能弱相关。但既然顺手可改，可纳入。 |
| **SC-4.7** str(user.user_id) defensive | ℹ️ info | 子代理自己说"无需修改"。剔除。|
| **SB-2.6** page_store token 残留 | 🟢 low | token TTL 自然过期，无安全风险。可不修。 |
| **SB-2.7** int(get_current_param) ValueError | ℹ️ info | command_config schema 已拦下大部分。可不修。|

## 🔁 去重

- **fan-out 串行同根因**：SA-1.1 / SA-2.1 / SA-CC-1 / SB-1.3 / SB-3.4 / SC-4.2 → 抽 `nextbot/server_broadcast.py` 公共 helper（与 large_image 同级），所有 fan-out 命令复用
- **DB-API 双写无 CRITICAL log 同根因**：SB-1.1 / SB-3.2 / SC-4.1 → `ban_core` 增加 `apply_ban_with_sync` / `apply_unban_with_sync` 聚合函数
- **lost-update 同根因**：SB-1.4 / SB-3.3 → `update().where(...).values(...)` + `execute_rowcount`
- **TShock URL 路径段**：SB-1.2 / SB-3.5 → 一处 `quote(safe="")` 加固
- **审计日志**：SA-1.3 / SA-2.4 / SB-1.5 / SB-3.6 / SC-4.5 → 统一加 `operator_id`

---

## 主代理整体看法

1. **本次审计的"真正大问题"集中在 4 条**：
   - **三个敏感命令默认 guest 权限**（SA-COMMON.1 + SB-2.1）：影响最大 + 最容易修
   - **DB-API 双写 + 无 CRITICAL log**（SB-1.1 + SB-3.2 + SC-4.1）：与 W-7 模式不一致
   - **fan-out 串行**（SA-1.1 等 6 处）：项目内最后一处未修
   - **TShock URL 路径段未编码**（SB-1.2 + SB-3.5）：与 PQB-2.2 同形未应用

2. **设计性建议**：
   - 抽 `nextbot/server_broadcast.py` 公共 fan-out helper（per-server semaphore + asyncio.gather + 部分失败聚合 + CRITICAL log）
   - `ban_core.py` 补 `apply_unban_to_db` / `apply_ban_with_sync` / `apply_unban_with_sync` 对偶函数 + 聚合层
   - `User.name` 字段路径段插值统一改 `quote(safe="")`

3. **本次涉及面较大，建议把 `webui_users.py` 也纳入修复范围**（与 ban.py 高度重复），但用户范围明确是"安全管理"分类命令——可单独在用户决策时确认。

4. **SA-COMMON.1 修复需要数据库迁移**：现有部署已 seed 老 guest 组含这三个权限，启动时需扫 `Group(name="guest").permissions`，自动剔除（沿用前序 `ensure_*_schema` 模式）。
