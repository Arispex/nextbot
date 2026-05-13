# R9 Permission 桶审计

- **范围**：`nextbot/permissions.py`、`nextbot/access_control.py`、`nextbot/audit.py`、`nextbot/ban_core.py`
- **方法**：Round 8 修复点逐条复审 + Round 7 修复保留性核查 + 全量再扫
- **结论先行**：R8-P-1.14 / R8-P-1.15 / R8-P-1.16 三项修复实现到位，Round 7 保留项无回退。新发现集中在“静默成功”路径（caller 未捕获新增 ValueError，会以 nonebot 默认异常回吐 traceback 给群里）、`_get_group_permissions` 仍无深度上限（与 cycle guard 协同但未硬限）、以及 audit 在白名单 broadcast 失败时仍记 success 三类。无 Critical / High，全部 Medium-Low / Info。

---

## Part A: Round 8 修复复审（3 项）

### R8-P-1.14 逗号 sanitize（4 个 helper）

实现位置 `nextbot/permissions.py:306-342`：

```python
def add_permission(value: str, permission: str) -> str:
    if "," in permission:
        raise ValueError("permission key 不可含逗号（会污染 CSV 存储）")
    ...
def remove_permission(value: str, permission: str) -> str:
    if "," in permission:
        raise ValueError("permission key 不可含逗号")
    ...
def add_inherit(value: str, parent: str) -> str:
    if "," in parent:
        raise ValueError("group name 不可含逗号（会污染 CSV 存储）")
    ...
def remove_inherit(value: str, parent: str) -> str:
    if "," in parent:
        raise ValueError("group name 不可含逗号")
    ...
```

复审结论：

1. **覆盖完整**：4 个 helper 全部加 `","` 检测，对称、信息一致。`remove_*` 也加 sanitize 是合理的（即使理论上单纯 remove 不可能污染存储，仍能拒绝畸形输入提早 fail）。
2. **触达路径**：caller 走 `parse_command_args_with_fallback`（`nextbot/message_parser.py:55-73`）做 `text.split()`，即 whitespace 切分。`"a,b"` 不含空白，所以**会作为单 token 进入 handler**，最终落到 `add_permission(old, "a,b")` 触发 ValueError。Sanitize 是真实有效的兜底。
3. **caller 是否捕获 ValueError**：**未捕获**。`grep -n 'except.*Value' nextbot/plugins/permission_manager.py nextbot/plugins/group_manager.py` 仅返回 `group_manager.py:132-133` 处对 `page` 解析的 ValueError，与 sanitize 路径无关。
   - 所有 add/remove perm/inherit handler 调用链都是：handler → `add_permission(...)`（在 `try: session = get_session() ... finally: session.close()` 内但**不 catch ValueError**）。
   - 后果：用户输入 `添加用户权限 @张三 a,b` 会让 nonebot framework 把 ValueError 当成未处理异常，默认行为是抛 traceback 到日志 + 群里发"运行出错"（具体取决于 nonebot 版本，但**不是友好提示**）。`session` 在 `finally` 处正确 close，无资源泄漏。
   - **严重度 Low**：恶意 actor 已通过 `require_permission` 校验才能触发；输入不规范会得到 traceback 而非 reply，但 DB 安全无损。建议：在 4 个 handler 的 `try` 块捕获 ValueError 转 `reply_failure("添加/删除", "权限名/身份组名不可含逗号")`。
4. **GROUP_NAME_PATTERN 重叠校验**：`group_manager.py:77` 的 regex `[A-Za-z0-9_\-]{1,32}` 已经拒绝逗号，但**只在 `handle_add_group`（line 199）应用**。`handle_inherit_group`（line 446 `child, parent = args`）、`handle_remove_group`（cascade scrub 调用 `remove_inherit`）等**未对 parent 名做 regex 校验**——既有组在 DB 里大概率已通过 add_group 限制了字符集，但 webui_groups（`server/routes/webui_groups.py:180-407`）有自己的 `_remove_inherit` helper（**未走 R8-P-1.14 sanitize**），是另一条潜在污染来源（详见 Part C 第 4 项）。

### R8-P-1.15 `_coerce_snapshot` 递归

实现位置 `nextbot/audit.py:25-55`：

```python
def _coerce_snapshot(name: str, value: Any, actor_user_id: str, action: str) -> Any:
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, dict):
        return {str(k): _coerce_snapshot(f"{name}.{k}", v, actor_user_id, action)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_snapshot(f"{name}[{idx}]", item, actor_user_id, action)
                for idx, item in enumerate(value)]
    logger.error(...)
    return str(value)
```

复审结论：

1. **递归形态正确**：dict / list / tuple 三种容器都被递归 coerce，nested ORM 现在确实会被替换为 `str(...)`。`audit_permission_change` 顶层对 `before` / `after` / `context` 三个参数都走 coerce（line 78-85），不再有"context 旁路"。
2. **递归深度**：无显式 `max_depth` 守卫。Python 默认递归深度 1000，常见 audit context（深度 1-3）远不会触发。但**没有 cycle detection**：若 caller 误传自引用 dict（罕见，但 ORM 关系字段或测试 fixtures 偶尔会出现 self-ref），会 RecursionError 把整个 audit 调用挂掉。当前 caller 都是显式构造 dict，风险接近零；记 Info：可在 helper 起始加一个 `_seen: set[id]` 短路。
3. **性能**：递归遍历 + per-element f-string 构造 `name`。常见 audit payload <10 个 element，开销忽略。极端情况：若 caller 把整个 `entries` 列表（百级）丢进 context，是 O(N) 遍历，仍可接受。无 hot-path 影响（audit 仅在 mutation 路径触发）。
4. **新增 logger.error**：line 51-54 在遇到非白名单类型时 log。频次受 caller 输入约束，**不应**被高频触发；若日志聚合发现该 ERROR 出现，应直接定位 caller 修正。建议：log 中带上 caller 文件/行号（`sys._getframe`），便于排查。Info 级。
5. **frozenset/set 兜底**：`isinstance(value, (list, tuple))` **不包含 `set` / `frozenset`**。若 caller 误传 `context={"members": frozenset(...)}`，会走到非白名单分支 → `str(...)`。结果可读但丢失 JSON 表达；同时 set 元素若是 ORM，repr 会先打到 `str(frozenset)` 内层。
   - 实际审计 `grep set 'audit_permission_change'` 未发现 caller 传 set 字段，但 `DEFAULT_GUEST_PERMISSIONS`（frozenset）在 reset 路径用 `join_csv_values` 转字符串后传，安全。
   - **严重度 Info**：补 set/frozenset 分支让审计行可读。

### R8-P-1.16 `_get_effective_permissions_in_session` session guard

实现位置 `nextbot/permissions.py:34-46`：

```python
def _get_effective_permissions_in_session(session, user_id: str) -> set[str]:
    if session is None:
        raise ValueError(
            "_get_effective_permissions_in_session 的 session 参数不能为 None"
        )
    user = session.query(User).filter(User.user_id == user_id).first()
    ...
```

复审结论：

1. **guard 形态**：只检测 None，未做 `isinstance(session, Session)` 强类型校验。当前两个 caller 都来自 `get_session()`，类型稳定；若未来 mock 测试或 driver 切换造成 duck-typed 对象，该 guard 依然能让 `session.query` 在错误位置抛 AttributeError 时上下文清晰。**合理取舍**：fail-hard on None 是高频实际错误，类型校验属过度防御。
2. **caller 验证**：
   - `nextbot/plugins/permission_manager.py:525`：`handle_set_user_group` 在 `try: session = get_session() ... finally: session.close()` 块内调用，session 必非 None。
   - `nextbot/plugins/group_manager.py:481`：`handle_inherit_group` 同样模式。
   - 两处都在 owner 短路后才调用（`if not is_owner(operator_id):` 块内），逻辑正确。
3. **runtime trace**：`raise ValueError` 会被 nonebot exception 上抛——这里**不是用户输入错误**而是开发错误，traceback 应当出现以便修复。`audit_permission_change` 在 denied 分支不依赖此函数返回，故 guard 不会破坏审计完整性。

---

## Part B: Round 7 修复保留性确认（4 项）

### H-3 (P-1.1) `require_permission` import + runtime fail-closed

`nextbot/permissions.py:236-303` 实现保留完整：

- **Import-time check**（line 248-255）：`missing = {"bot", "event"} - param_names`，缺形参则 `raise RuntimeError`。这能让 nonebot 在 plugin 加载阶段就 fail-loud，防止命名漂移导致 wrapper 静默跳过权限校验。
- **Runtime fail-closed**（line 281-289）：`if bot is None or event is None: logger.warning(...); return`。早期版本会 `await func(...)` 进而走 fail-open，现已强制 return。无回退。
- **`type_hints` 加 `include_extras=True`**（line 261-264）保留，防止 `Annotated[Dict, _STATE_FLAG]` 这类 nonebot 注入元信息被吞掉。

### P-1.6 `access_control.lru_cache(1)` + frozenset/tuple

`nextbot/access_control.py:77-93` 保留完整：

- `get_owner_ids` → `frozenset[str]`、`get_owner_ids_ordered` → `tuple[str, ...]`、`get_group_ids` → `frozenset[str]`，三者都 `@lru_cache(maxsize=1)`。
- 返回不可变对象，调用方 `if user_id in get_owner_ids():` 用法不会因 mutate 污染缓存。
- **无回退**。Hot-path 优化保留。
- 关联新发现见 Part C 第 1 项（cache_clear 入口缺失）。

### P-1.9 `_safe_repr` + `_coerce_snapshot`

`nextbot/audit.py:19-22` 的 `_safe_repr` 完整保留（`repr(...).replace("\n", "\\n").replace("\r", "\\r")`），防止 user-controlled 字段（user.name / ban_reason）通过换行注入伪造审计行。`_coerce_snapshot` 已增强为递归版本，见 Part A。

### P-1.13 `ban_core._extract_blacklist_entries`

`nextbot/ban_core.py:138-152` 完整保留：

```python
payload = check.payload if isinstance(check.payload, dict) else {}
entries = payload.get("entries")
if not isinstance(entries, list):
    return []
return [e for e in entries if isinstance(e, dict)]
```

`sync_user_to_blacklist`（line 181）和 `sync_user_blacklist_remove`（line 250）都通过该 helper 读取 entries，不再有 `"entries": "string"` 导致按字符迭代抛 AttributeError 的风险。**无回退**。

---

## Part C: 全量再扫新发现

### 1. `access_control.lru_cache` 无 cache_clear 入口（Info → Low）

位置：`nextbot/access_control.py:77-92`

- 三个 `@lru_cache(maxsize=1)` 函数全部依赖 `get_driver().config` 读取 `.env`。NoneBot 默认不支持运行时热更新 `.env`，但**测试场景**（用 monkeypatch 修改 config 后调用 cached 函数）会拿到 stale 值。
- 当前代码无 `get_owner_ids.cache_clear()` 等清理入口，测试 fixture 必须显式 `func.cache_clear()` 才能保证隔离。
- **现状风险**：生产场景零影响（owner_id 进程内不变）；测试隔离场景存在隐性耦合。
- **建议（Info）**：暴露 `reset_access_control_cache()` 集中调用三者的 `cache_clear()`，给 pytest fixture 复用。

### 2. `_get_group_permissions` 仍无显式深度上限（Medium-Low，复读 Round 7 P-1.4）

位置：`nextbot/permissions.py:59-75`

- 仅靠 `visited: set[str]` 防环，无 `max_depth` 限制。
- `add_inherit` 路径有 `MAX_INHERIT_DEPTH = 8` 软警告（`group_manager.py:468-475` 调用 `_measure_inherit_depth`），新建边时会拒绝超过 8 层。
- 但**老数据**或**绕过 8 层校验的路径**（webui_groups.py 可能不走 add_inherit helper——见第 4 项）能造成超长链，`_get_group_permissions` 每次 `has_permission` 都全链 DFS。
- BEGIN IMMEDIATE 串行化下 DB 负担可控，但 has_permission 调用频率高（每个命令至少一次），单组多父继承（fanout）会指数膨胀（用 `set` 防环不防重复访问同一节点的不同 path——`visited` 在 DFS 内全局，OK）。复盘代码：`visited` 是单调累积的，没有问题，节点数上限是 Group 总数。
- **现状风险**：DoS via 巨型 group 表（百级以上）的延迟放大。生产环境组数有限。
- **建议（Medium-Low）**：在 `_get_group_permissions` 也加 `MAX_INHERIT_DEPTH * 2` 硬上限作为深度防御。

### 3. `is_dangerous_permission("")` 边界（Info，复读 Round 8 R8-P-1.19）

位置：`nextbot/permissions.py:128-146`

- 空串 `""`：
  - 不等 `"*"`、不在 `DANGEROUS_PERMISSION_PREFIXES`、`"".endswith(".*")` 是 False → 返回 False。
  - 行为正确（空串不是危险 key）。
- 但 `add_permission(value, "")` / `_match_permission(granted, "")`：
  - `add_permission("", "")` 经过 `set(split_csv_values(""))` 得 `set()`，加入空串后 → `{""}`，再 `join_csv_values` 仍生成空字符串（`split_csv_values` 过滤空串）。无害。
  - `_match_permission(granted, "")` 中 `granted.endswith(".*")` 时 `"".startswith(prefix)` 仅当 prefix 为 `""` 时成立——但 `granted == ".*"` 走通配会让任何用户的空串 `permission` 短路。`has_permission` caller 是 `require_permission(permission)`，permission 由开发者硬编码，不会传空。
- **现状风险**：零。仅 Info。
- **建议**：`validate_permission_key` 已隐式拒绝空串（registry 不含空 key），无需额外校验。

### 4. webui 的 `_remove_inherit` 旁路 R8-P-1.14 sanitize（Medium）

位置：`server/routes/webui_groups.py:180-407`

```
180:def _remove_inherit(inherits: str, removed_name: str) -> str:
...
407:            item.inherits = _remove_inherit(item.inherits, group_name)
```

- webui 路径有**自己的 `_remove_inherit` helper**，不调用 `nextbot.permissions.remove_inherit`，故**没有 R8-P-1.14 的逗号 sanitize**。
- 如果该 webui 实现做的是 `csv.replace(removed_name, "")` 朴素替换而 `removed_name` 含逗号，会破坏存储。需进一步审计 webui_groups.py 实现细节。
- 同时若该 webui 模块对其他 mutation 也有 fork 实现（add_inherit / add_permission），R8 sanitize 覆盖不全。
- **建议（Medium）**：把 webui_groups 的本地 `_remove_inherit` 替换为 import `from nextbot.permissions import remove_inherit`，让 sanitize / fail-hard 自动覆盖；或在 webui 也复制等价 sanitize。

### 5. Audit 调用时机：commit 失败时审计已写（Medium-Low）

模式重复出现，举两个典型：

- `nextbot/plugins/server_manager.py:127-137` `handle_add_server`：commit 成功后 audit（顺序正确）。
- `nextbot/plugins/permission_manager.py:298-305` `handle_add_user_perm`：`finally: session.close()` 之**后**才 audit，commit 在 try 块内执行，顺序正确。
- `nextbot/plugins/ban.py:110-117` `handle_ban`：`apply_ban_to_db` 内部 commit 后再 audit。**但 `apply_ban_to_db` 返回 `code != "banned"` 的"软失败"（owner_protected/already_banned/not_found）也会让外层在 owner_protected 分支补一条 denied audit（line 91-96）**，整体审计语义清晰。

新发现：

- **`sync_user_to_blacklist` 失败仍记 ban audit**：
  - `handle_ban`（`nextbot/plugins/ban.py:110-118`）的 audit `user.ban` 在 DB commit 后、`sync_user_to_blacklist` **之前**调用。
  - 若 broadcast 全部失败，DB 上用户已 banned，但 TShock 端无黑名单，审计行只显示成功。日志后续 `success={success_count}/{len(outcomes)}` 印在 logger 里，但**不进 audit_permission_change**。
  - **现状风险 Medium-Low**：审计聚合时可能误读为"全成功"。
  - **建议**：要么把 broadcast 结果 append 进 audit context；要么在 broadcast 失败时再补一条 `user.ban.blacklist_partial` denied/warn audit。
- **`audit_permission_change` 是同步调用**：纯 `logger.warning(...)` 一行，无 I/O 阻塞（nonebot logger 默认 loguru 异步 sink 走队列，单条 ~µs），不会阻塞 handler。**无问题**。

### 6. `apply_ban_to_db` / `apply_unban_to_db` 缺显式 rollback（Low）

位置：`nextbot/ban_core.py:41-127`

```python
def apply_ban_to_db(user_id: str, reason: str) -> BanDBResult:
    session = get_session()
    try:
        ...
        session.commit()    # line 75
        ...                 # 后续可能再 query
        return ...
    finally:
        session.close()
```

- `session.commit()` 失败抛异常时（如 SQLite 锁超时、IntegrityError），不会被 `try/except` 捕获，**异常直接上抛**到 caller `handle_ban`，caller 也没有 try/except——会被 nonebot 当作 handler 异常处理（logged + 群里默认提示）。
- `session.close()` 在 SQLAlchemy 中会自动 rollback 未提交事务（`Session.close` 内含 `expunge_all` + 释放 connection），但**显式 `session.rollback()` 更清晰**。
- 实际行为：异常状态下 DB 已经 rollback，资源正确释放，**无数据破坏**。
- **建议（Low）**：在 try 内 `except` commit 异常 → 显式 rollback + 转 BanDBResult `code="db_error"`（需新增 code）让 caller 转友好提示，避免 traceback 直达群消息。

### 7. 跨 session 并发：`sync_user_to_blacklist` / `sync_user_blacklist_remove`

位置：`nextbot/ban_core.py:155-288`

- 这两个函数**不开 DB session**，只读 `_load_servers()` 一次（开/关 session 是局部），然后用 `httpx` 做 broadcast。
- 并发安全：`servers` 是值拷贝（ORM `.all()` 后 session 关闭，对象仍持有列值但不再 lazy-load——`Server.id / .name` 等都是 plain column，OK）。
- `broadcast(servers, _add_one)` 由 `server_broadcast.broadcast` 控制并发，单 user 多 server 并发 OK；多个 user 并发触发同名 user_name 的 add/remove 会在 TShock 侧依赖 server 实现，与 nextbot 无关。
- **无新发现**。

### 8. `_match_permission(".*", "anything")`（Info）

位置：`nextbot/permissions.py:19-23`

```python
def _match_permission(granted: str, required: str) -> bool:
    if granted.endswith(".*"):
        prefix = granted[:-1]
        return required.startswith(prefix)
    return granted == required
```

- `granted = ".*"` → prefix = `"."` → `required.startswith(".")` 只有以 `.` 开头的 permission 才匹配。
- `granted = "*"` → 不走通配分支，走 exact match。`is_dangerous_permission` 对 `*` 单独 True；但 `_match_permission("*", "foo")` 返回 False。
- 含义：**单 `*` 在 DB 里没有匹配效果**——它只在 `is_dangerous_permission` 路径被识别为"危险关键字"。开发者若手工塞 `*` 到 user.permissions，对实际 has_permission 无效。
- **现状风险**：零（DANGEROUS 已阻止非 owner 授 `*`，owner 直接走 `is_owner` 短路）。但**易误解**。
- **建议（Info）**：在 `validate_permission_key("*")` 显式返回 False（已有），并在 `_match_permission` 头部加注释说明 `"*"` 不作为通配处理。

### 9. `_coerce_snapshot` 对 datetime / Decimal 等常见值类型走兜底

- `audit.py:38` 仅放过 `str / int / bool / float / None`。
- `datetime / date / decimal.Decimal / UUID` 等 caller 常见值会进 logger.error 分支 → `str(...)`。
- 实际审计：所有 caller 都把 datetime 转 `format_beijing_datetime(...)` 或 ISO 字符串再传（见 `user_manager.py:540-558` rename audit、`ban.py` 等），未发现直接传 datetime 的 caller。
- **现状风险**：零，但每加一个新 caller 都要小心。
- **建议（Info）**：把 `datetime / date / Decimal / UUID` 列入白名单（统一 isoformat()/str()），减少 caller 心智负担。

---

## 结论

- **Round 8 三项修复（R8-P-1.14 / 1.15 / 1.16）均到位**，实现正确、对称、有注释。
- **Round 7 四项保留（H-3 / P-1.6 / P-1.9 / P-1.13）无回退**。
- **新发现共 9 项**，均为 Medium-Low / Info：
  - 主要遗漏面是 **caller 配合**——4 个 handler 没有 try/except ValueError 把 sanitize 失败转成友好提示（C-1.14 真实暴露给 actor 时会回吐 traceback）。
  - **webui_groups.py 自有 `_remove_inherit`** 旁路 nextbot helper，是 R8-P-1.14 sanitize 覆盖不全的最显眼缺口（建议 Medium）。
  - audit 与 broadcast 在 ban 路径的"DB 成功 + TShock 失败"语义对外不透明，是 Round 9 可考虑改进的方向。
  - `_get_group_permissions` 仍无硬深度限制，与 cycle guard 协作但缺乏深度防御。

无 Critical / High 级新发现。可以批准本桶进入 Round 10（或归档）。
