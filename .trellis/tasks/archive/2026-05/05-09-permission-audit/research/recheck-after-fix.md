# 修复后再检查报告：权限管理 22 修复模块

- **审查日期**: 2026-05-09
- **审查范围**: M0.5–M22 / `nextbot/audit.py`、`nextbot/permissions.py`、`nextbot/db.py`、`nextbot/command_config.py`、`nextbot/plugins/group_manager.py`、`nextbot/plugins/permission_manager.py`
- **基线对照**: `permission-{a,b}-findings.md` + `main-agent-recheck.md`
- **结论一句话**: 22 个修复模块全部落地，owner 短路覆盖完整、POLA / hierarchy / blocklist / registry 校验路径正确、cycle DFS / cascade preview / 二步确认 caller 绑定均符合规范；**仅一项需要警示的副作用**（系统级 BEGIN IMMEDIATE 影响所有非权限 handler 的并发模型），不构成回归但需要项目维护者知晓并可观测验证。整体置信度 **HIGH（90%）**。

---

## 整体结论

| 检查维度 | 状态 |
|---|---|
| Owner 短路覆盖率 | ✅ 完整（POLA / hierarchy / dangerous-key / registry 4 类校验全部 `is_owner()` 短路） |
| Lost-update 条件 UPDATE | ✅ 6 个 mutation 站点（group.permissions / group.inherits / user.permissions / user.group）全部走 `update().where(col == old).values(only_changed_column)` + retry 模式 |
| 审计日志统一入口 | ✅ 9 个 mutation handler + 1 个 reset 全部走 `audit_permission_change()`，含成功 / 拒绝两类 action |
| Cycle DFS 检测 | ✅ 直接 / 两步 / 多步 cycle 均拒绝（实测通过） |
| 删除身份组 cascade | ✅ 二步确认 + caller_user_id 绑定 + 子组 inherits 精确匹配（先 SQL `LIKE` 粗匹配 + Python `in split_csv_values()` 精准过滤）+ reassign 到 `default` |
| 重置访客权限 | ✅ 新命令注册、二步确认、caller 绑定、replace_with 语义、列出移除 + 新增 |
| 管理员列表 | ✅ `asyncio.gather` + `wait_for(timeout=5)` + 失败兜底，base64 上限保护 |
| 文案规范 | ✅ `reply_failure(action, raw_reason)`，未拼"动作 + 结果 + 原因" |
| 无 schema 变化 | ✅ `db.py` 仅新增常量 + engine event listeners，无 `Mapped[...]` 列变更 |
| 行为兼容性 | ⚠️ `身份组列表` 改为 count + preview（spec 已声明，文档化）；其他成功路径 reply 文案保持兼容 |
| Pyright（standard mode） | ✅ 0 errors / 0 warnings |
| Ruff lint | ✅ 仅项目共有的 stylistic warnings（ANN / PLR / E501 等），无新增 functional 错误 |

---

## 🔴 Bugs introduced（无）

无新引入 bug 或回归。

---

## 🟠 Fixes that are incomplete or ineffective

### O1 [HIGH] — 系统级 BEGIN IMMEDIATE 副作用波及所有 handler

**位置**：`nextbot/db.py:403-408`

```python
@event.listens_for(_engine, "begin")
def _force_immediate_begin(connection):
    connection.exec_driver_sql("BEGIN IMMEDIATE")
```

**问题**：`event.listens_for(_engine, "begin")` 钩在引擎的所有事务开启上，意味着 26 个使用 `get_session()` 的源文件（包括所有 `economy.py` / `lottery.py` / `warehouse.py` / `player_query.py` / `leaderboard*` / `ban*` 等只读查询）每次 transaction 开启都会显式 `BEGIN IMMEDIATE`，提前获取 SQLite RESERVED 锁。

**实际影响**：
- 在默认 `journal_mode=DELETE`（项目未配置 WAL）下，BEGIN IMMEDIATE 阻塞其他 writer 但不阻塞其他 reader，因此并发读仍可进行。
- 但**所有写操作系统全局串行化**：以前 `economy.signin` 与 `lottery.draw` 的写事务可以靠 SQLAlchemy DEFERRED + 各自获锁时序错峰；现在两者互相排队，吞吐降至单线程。
- handler 中跨 `await bot.send(...)` 持有 session 的代码（permission_manager 添加用户权限 / 删除用户权限 / 修改用户身份组在 try 内含多个 `await bot.send()`），现在网络 IO 期间持有 RESERVED 锁，进一步延长串行化窗口。

**为什么不是 critical**：
- 单 SQLite + bot.send 通常 <10ms，串行化的现实代价对 QQ bot 用户侧不可感知
- 项目本身已经是"单写者"模型，只是把"事实上的串行"变成"显式串行"
- 不引入正确性 bug，只可能引入吞吐回退

**建议**：
1. **首选**：把 `BEGIN IMMEDIATE` 收窄到只对权限 mutation handler 生效。可以通过 SQLAlchemy 的 `session.connection().exec_driver_sql("BEGIN IMMEDIATE")` 在每个权限 mutation handler 顶部按需打开，而不是引擎级全局事件。
2. **次选**：开启 SQLite WAL 模式 (`PRAGMA journal_mode = WAL`)，让读不被 BEGIN IMMEDIATE 阻塞，进一步降低串行化代价。
3. **观测验证**：上线后跑一次基准（同时 50 个 `economy.signin` + 50 个 `economy.dice`），对比 BEGIN IMMEDIATE 启用前后的 p95 延迟。

如果维护者评估后决定保留全局 BEGIN IMMEDIATE，需要在 `db.py` 注释中明确声明"本项目接受所有写事务串行化的设计"。

### O2 [MEDIUM] — 删除用户权限 registry 校验拦截了清理 legacy key 路径

**位置**：`nextbot/plugins/permission_manager.py:147-150`

```python
if not validate_permission_key(permission):
    suggestions = suggest_permission_keys(permission)
    hint = f"。是否想说：{', '.join(suggestions)}" if suggestions else ""
    return False, reply_failure(action_label, f"权限名称不存在{hint}")
```

**问题**：`_check_user_perm_mutation_pola` 在 `is_grant=False`（删除）路径也校验 registry。如果历史上某用户被授予过一个之后被删除 / 重命名的命令权限（例如 `economy.signin` 改名为 `economy.sign`），非 owner 操作员现在无法用 `删除用户权限` 清理这个遗留 key。

**实际影响**：
- Owner 仍可清理（line 141 owner 短路）
- 非 owner 操作员看到的是 "权限名称不存在" 错误文案，可能让其困惑
- 不构成安全漏洞，仅 UX 问题

**建议**：在 `_check_user_perm_mutation_pola` 中把 registry 校验从 `is_grant=False` 路径中移除：

```python
if is_grant and not validate_permission_key(permission):
    ...
```

或者保留校验但把"权限名称不存在"在 remove 路径上降级为 warning 而非 failure，让操作继续。

### O3 [MEDIUM] — `handle_reset_guest_perms_confirm` 用 ORM 直赋而非条件 UPDATE

**位置**：`nextbot/plugins/permission_manager.py:941`

```python
guest.permissions = new_csv
session.commit()
```

**问题**：与代码库其他地方统一的"条件 UPDATE 防 lost-update + ORM 跨列覆盖"模式不一致。虽然：
- BEGIN IMMEDIATE 已经把并发写排成串行，lost-update 窗口被消除
- SQLAlchemy 默认只更新 dirty 列（`permissions`），不会跨列覆盖 `inherits`

但这是个一致性问题。如果将来移除 BEGIN IMMEDIATE 全局开关（见 O1），这里就会回退到 lost-update 模式。

**建议**：改为统一模式：

```python
old_csv = str(guest.permissions or "")
rowcount = execute_rowcount(
    session,
    update(Group)
    .where(Group.name == _SYNC_GROUP_NAME, Group.permissions == old_csv)
    .values(permissions=new_csv),
)
if rowcount == 0:
    # 并发冲突：极小概率，但与其他 handler 一致
    ...
session.commit()
```

### O4 [MEDIUM] — `handle_delete_group_confirm` cascade 子组 inherits scrub 用 ORM 直赋

**位置**：`nextbot/plugins/group_manager.py:349`

```python
g.inherits = remove_inherit(g.inherits, name)
```

**问题**：与 O3 同根因，cascade 修改子组 inherits 时直接 ORM dirty-set，未走 `update().where().values()` 模式。

**实际影响**：
- BEGIN IMMEDIATE 下安全
- SQLAlchemy 只 UPDATE 改动列
- 不影响功能正确性

**建议**：可保留（cascade scrub 在删除事务内，与父删除原子化），但加注释说明依赖 BEGIN IMMEDIATE 的串行化保证。或者改为：

```python
for g in candidate_groups:
    parents = split_csv_values(g.inherits)
    if name not in parents:
        continue
    new_inherits = remove_inherit(g.inherits, name)
    execute_rowcount(
        session,
        update(Group)
        .where(Group.name == g.name, Group.inherits == g.inherits)
        .values(inherits=new_inherits),
    )
```

### O5 [LOW] — WebUI 删除身份组仍 reassign 到 `guest`，与 bot handler 不一致

**位置**：`server/routes/webui_groups.py:400-403`

```python
session.query(User).filter(User.group == group_name).update(
    {User.group: "guest"},
    synchronize_session=False,
)
```

**问题**：bot handler 已切换到 `GROUP_DELETE_FALLBACK = "default"`（`db.py:110`），但 WebUI 仍硬编码 `"guest"`。同一个删除组的语义在 bot vs WebUI 中产生分歧。

**Spec 解释**：Out of Scope 已声明 "WebUI 中的权限管理页"。

**建议**：在 spec 完成后单独发起一个 WebUI 对齐小任务，引用 `nextbot.db.GROUP_DELETE_FALLBACK` 替代字面 `"guest"`，避免常量重复定义。

### O6 [LOW] — `handle_add_group_perm` 危险 key denied audit 缺 `reason="dangerous_key"` 之外的 before/after

**位置**：`nextbot/plugins/group_manager.py:614-621`

denied 路径的 audit 只有 `context={"permission": ..., "reason": ...}`，没有 `before={"permissions": <current_group_perms>}`。读 log 时无法直接看出当时该组实际持有的权限集，必须额外查 DB 才能复盘。

**建议**：在 denied 路径补一个轻量的 `before` 快照，例如只记录 perm 数量而不记录全部 CSV：

```python
audit_permission_change(
    actor_user_id=operator_id,
    action="group.permission.add.denied",
    target=name,
    context={
        "permission": permission,
        "reason": "dangerous_key",
        "operator_perm_count": len(get_effective_permissions(operator_id)),
    },
)
```

不阻塞修复落地。

---

## 🟢 Quality / style improvements

### Q1 — 部分 await bot.send() 仍在 session try 内

**位置**：`permission_manager.py:228, 238, 245, 264, 269, 273` 等

session 在 try 内打开，多个失败 / no-op 路径 `await bot.send(event, ...)` 在 finally 关闭 session 之前。BEGIN IMMEDIATE 下这意味着网络 IO 期间持有 RESERVED 锁。

**建议**：把 reply 文案先组装到局部变量，session.close() 后再 `await bot.send()`。这是个小幅优化，但在 BEGIN IMMEDIATE 模型下值得做。例如：

```python
session = get_session()
try:
    user = session.query(User).filter(User.user_id == user_id).first()
    if user is None:
        failure_msg = reply_failure("添加", "用户不存在")
    else:
        # ... 实际工作
        success_msg = reply_block(...)
finally:
    session.close()

if failure_msg:
    await bot.send(event, at + " " + failure_msg)
    return
await bot.send(event, at + "\n" + success_msg)
```

### Q2 — `_get_effective_permissions_in_session` / `_would_create_inheritance_cycle` / `_measure_inherit_depth` / `_get_group_permissions` 是私有函数但被 plugin 模块直接 import

**位置**：`group_manager.py:38-53`、`permission_manager.py:42-43`

```python
from nextbot.permissions import (
    _measure_inherit_depth,
    _would_create_inheritance_cycle,
    _get_effective_permissions_in_session,
    _get_group_permissions,
    ...
)
```

下划线前缀按 PEP 8 是"私有"约定。从外部 import 私有函数破坏封装。建议要么：
- 把这几个函数去掉下划线前缀（公开 API 化），
- 或者新建 `nextbot.permission_internals` 模块明示"内部使用"，
- 或者把 hierarchy 检查 / cycle 检查抽到 `nextbot.audit.py` 旁边的 `nextbot.permission_helpers.py`。

### Q3 — `validate_permission_key` 的延迟 import 注释可以更清晰

**位置**：`permissions.py:191-194`

```python
# 延迟 import 避免与 command_config.py 形成 import-time 循环
from nextbot.command_config import get_permission_registry
```

可以加一句"`command_config.py` import `nextbot.db` import `nextbot.permissions`，因此模块顶级 import 会形成 cycle"。

### Q4 — `audit_permission_change` 的 `before` / `after` 用 `repr()` 输出 dict

**位置**：`audit.py:45-47`

```python
parts.append(f"before={before!r}")
```

dict `repr()` 在 grep 时可读性 OK，但若值包含敏感字段（例如未来可能新增的 `nickname` / `qq_avatar`），会无脱敏直写日志。建议加注释提醒 caller："before / after 不应包含 PII；目前 caller 只传 `{"permissions": csv, "inherits": csv}` 等纯权限字段，如未来要传用户姓名 / 昵称需先脱敏"。

### Q5 — `handle_add_user_perm` 和 `handle_remove_user_perm` 中 `target_name` 在 retry 循环内重新读

**位置**：`permission_manager.py:271, 412`

```python
target_name = str(current.name)
```

retry 时再次读 user 行时刷新 `target_name`。但如果 user 改名是另一个事务（比如 `重命名 admin <user> <new>`），retry 会读到新名字，而本次 audit context 里的 `target_name` 与操作开始时不一致。

属于 cosmetic，audit log 的 `target` 字段（QQ 号）是稳定的，name 仅辅助阅读。

---

## 具体 verification 项详解

### 1. Owner exemption 全覆盖 ✅

- `_check_user_perm_mutation_pola` line 141: `if is_owner(operator_id): return True, None`
- `handle_set_user_group` 层级护栏 line 503: `if not is_owner(operator_id):`
- `handle_add_group_perm` line 603: `if not is_owner(operator_id):`
- `handle_remove_group_perm` line 717: `if not is_owner(operator_id):`
- registry 校验 / dangerous-key / POLA 都在 `is_owner` 短路块内

owner 可以授予未注册的 key、可以授予 dangerous key、可以授予自己未持有的权限、可以把任意用户移到任意身份组。符合 spec 中 "owner 不受限制" 验收。

### 2. Conditional UPDATE retry 实现正确 ✅

6 个 CSV mutation 站点：
- `group_manager.py:443` `继承身份组` Group.inherits
- `group_manager.py:532` `取消继承身份组` Group.inherits
- `group_manager.py:649` `添加身份组权限` Group.permissions
- `group_manager.py:754` `删除身份组权限` Group.permissions
- `permission_manager.py:249` `添加用户权限` User.permissions
- `permission_manager.py:390` `删除用户权限` User.permissions
- `permission_manager.py:544` `修改用户身份组` User.group

全部使用 `update().where(name == ?, col == old).values(col=new)` + retry on `rowcount == 0` 模式。No-op 检测在 retry 之前 + retry 内部都有，避免无限重试。仅写一列，避免 ORM dirty-set 跨列覆盖 User.permissions / User.group 的相互干扰。retry 5 次耗尽后回 `并发冲突，请稍后重试`。

### 3. BEGIN IMMEDIATE engine 配置 ⚠️

- ✅ `busy_timeout=5000` 让阻塞的 writer 等待
- ✅ `connect` 事件挂在引擎初始化时，`begin` 事件每次事务前触发
- ⚠️ **副作用**：影响所有 `get_session()` 调用方（见 O1）。这是 spec 明确提到的"修后再检查"重点项，需要项目维护者评估是否接受全局序列化代价。
- ✅ `_get_effective_permissions_in_session()` workaround 正确——在已开 transaction 内调用 `get_session()` 会因为 BEGIN IMMEDIATE 在另一连接上等待而潜在死锁，复用 session 避免

### 4. 删除身份组 cascade 二步确认 ✅

- ✅ caller_user_id 绑定（line 315-317）
- ✅ confirm 阶段重新查 group（line 331-333）
- ✅ 子组 inherits 粗匹配 SQL `LIKE` + 精确 `name in split_csv_values()` 过滤，无子串误判
- ✅ reassign 用 `update().where().values(group=GROUP_DELETE_FALLBACK)`
- ✅ 审计 context 含 `reassigned_users` / `fallback_group` / `updated_child_groups`

### 5. DFS cycle 检查 ✅

实测：
- `A -> A` direct cycle: True ✅
- `A -> B exists, then B -> A`: True ✅
- `A -> B -> C, then C -> A`: True ✅
- 无 cycle 场景: False ✅

`_measure_inherit_depth` 用 `visited.copy()` 避免共享集合错误，且 visited 在每条 DFS 路径上独立。

### 6. 重置访客权限 ✅

- ✅ `permission.group.guest.reset` 通过 `command_control` 注册（line 853）
- ✅ NOT in `DEFAULT_GUEST_PERMISSIONS`（grep 验证）
- ✅ 二步确认 caller_user_id 绑定（line 920-922）
- ✅ `replace_with(DEFAULT_GUEST_PERMISSIONS)` 语义（line 933）
- ✅ preview 列出 `extras`（将移除）+ `missing`（将新增）
- ✅ audit context 含 `removed` 和 `added` 两份列表

### 7. 管理员列表 N+1 修复 ✅

`asyncio.gather(*(_fetch_nickname_with_timeout(bot, qq) for qq in owner_ids))`，per-call `wait_for(timeout=5.0)`，timeout / exception 用占位符兜底，gather 不传 `return_exceptions` 但 `_fetch_nickname_with_timeout` 内部已 catch 所有异常返回 `(qq, "（获取失败）")`，因此不会中断 gather。order 通过 `for qq, nickname in results` 保留 owner_ids 顺序。

### 8. 无 schema change ✅

`db.py` 仅新增 `RESERVED_GROUP_NAMES` / `GROUP_DELETE_FALLBACK` 常量 + 两个 engine event listener。无 `Mapped[...]` 列追加、无 `ALTER TABLE`。`init_db()` 启动时即可。

### 9. 失败文案规范 ✅

`reply_failure(action, raw_reason)` 由 `text_utils.py` 实现 `f"❌ {action}失败，{reason}"`，调用方（如 line 191 `reply_failure("添加", "身份组名称为系统保留字")`）只传原因，符合 user instructions "失败：动作 + 结果，原因"。

### 10. WebUI 兼容 ⚠️

- WebUI `webui_groups.py` / `webui_users.py` 仍直接读写 `Group.permissions` / `User.group` ORM 对象，未使用条件 UPDATE。在 BEGIN IMMEDIATE 全局生效下，WebUI 写入会被 bot handler 串行排队，不会数据不一致。
- 但 WebUI 删除身份组仍 reassign 到 `"guest"`（见 O5），与 bot handler 行为分歧。
- spec 已声明 WebUI 出 scope，不影响验收。

### 11. 行为兼容性 ⚠️

- `身份组列表` 输出格式从"完整 perm CSV"改为"perm 数 + 前 5 个预览"；spec 明确这是 PMA-1.1 修复（每页 10 组 + perm 截断），文档化，但调用方习惯需要适应。
- 其他成功路径 reply 文案：`reply_success("添加")` 输出 `"✅ 添加成功"`；`reply_block(reply_success("添加"), [...])` 输出多行卡片。新增的成功 / 删除 / 修改命令现在用 `reply_block` 多行展示，**对 V11 兼容**（OneBot V11 自然支持多行文本），但比之前更冗长。

### 12. 同步访客权限保持不动 ✅

`handle_sync_guest_perms` / `handle_sync_guest_perms_confirm` 保留原两步确认机制；只新增了 `audit_permission_change(...)` 调用记录 actor。spec 明确 "同步访客权限的两步确认机制（已正确，不动）"，符合验收。

---

## 推荐 follow-up

按优先级：

1. **[HIGH O1]** 评估并确认 BEGIN IMMEDIATE 全局副作用是否可接受。如果接受：在 `db.py` 顶部 docstring 显式声明"本项目接受所有写事务串行化"。如果不可接受：把 BEGIN IMMEDIATE 收窄到只在权限 mutation handler 中按需打开。
2. **[MEDIUM O2]** `_check_user_perm_mutation_pola` 在 `is_grant=False` 路径跳过 registry 校验，让 owner 之外的操作员可以清理 legacy 用户 perm。
3. **[MEDIUM O3, O4]** 把 `handle_reset_guest_perms_confirm` 和 `handle_delete_group_confirm` 的 ORM 直赋改为 `update().where().values()`，与代码库其他地方统一。
4. **[LOW O5]** 单独发起 WebUI 对齐小任务（不阻塞本次验收）。
5. **[LOW O6]** denied 路径的 audit 补 `before` 快照（轻量，例如 perm count）。
6. **[QUALITY Q1]** session 内的 `await bot.send()` 提取到 finally 之外，缩短 RESERVED 锁持有时间。
7. **[QUALITY Q2]** 把跨模块共享的 `_helper` 函数升格为公开 API 或抽到独立模块。

---

## Verification 命令快照

```bash
# Lint
.venv/bin/ruff check nextbot/audit.py nextbot/permissions.py \
  nextbot/db.py nextbot/command_config.py \
  nextbot/plugins/group_manager.py nextbot/plugins/permission_manager.py
# 仅项目共有 stylistic warnings；无 functional 错误

# Type check
.venv/bin/pyright --pythonpath .venv/bin/python <files>
# 0 errors, 0 warnings

# Smoke test
.venv/bin/python -c "from nextbot import audit, permissions, command_config, db; ..."
# All imports OK; 常量 / helper 全部正确暴露

# Cycle DFS 实测
A -> A direct: True
A -> B exists, then B -> A: True
A -> B -> C, then C -> A: True
A -> B, then C -> B (no cycle): False

# Dangerous-key 实测
is_dangerous(*) True
is_dangerous(permission.*) True
is_dangerous(group.delete) True
is_dangerous(economy.signin) False
```
