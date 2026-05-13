# R8 权限 / 审计 / 访问控制 桶审计

- **Bucket**: permissions / access_control / audit / ban_core
- **Files audited**:
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/permissions.py`
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/access_control.py`
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/audit.py`
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/ban_core.py`
- **Round**: 8 复审 + 全量再扫
- **Date**: 2026-05-13

---

## Part A: Round 7 修复复审

### A-1. H-3 (P-1.1) `@require_permission` 形参校验 + runtime fail-closed

**位置**: `permissions.py:229-296`

**复审结论**: **PASS**

**验证细节**:

1. **import-time 形参校验** (`permissions.py:241-248`)：
   ```python
   param_names = set(signature.parameters.keys())
   missing = {"bot", "event"} - param_names
   if missing:
       raise RuntimeError(...)
   ```
   - `inspect.signature(func).parameters` 是 OrderedDict，key 即形参字面名。VAR_POSITIONAL (`*args`) 与 VAR_KEYWORD (`**kwargs`) 形参在 `parameters` 里的 key 是不带星号的名字（如 `args`、`kwargs`），不会误伤 `async def h(*args, bot, event, **kwargs)` 这种 signature——`bot` 和 `event` 仍会以 keyword-only 形式出现，校验 PASS。
   - 全仓 grep `@require_permission` 共 58 个 handler，全部使用字面 `bot: Bot, event: Event` 形参（无 `b`/`e` 简写），import-time 校验对现有代码不会误报。详见 `nextbot/plugins/*.py`。

2. **runtime fail-closed** (`permissions.py:270-282`)：
   ```python
   bound = resolved_signature.bind_partial(*args, **kwargs)
   bot = bound.arguments.get("bot")
   event = bound.arguments.get("event")
   if bot is None or event is None:
       logger.warning(...)
       return
   ```
   - `return None` 让上游 NoneBot matcher chain 静默结束。NoneBot handler 返回值不被消费（matcher 通过副作用 send / finish 工作），return 等价于"skip command without reply"，符合 fail-closed 期望（attacker 拿不到漏权信号）。
   - 与之配对的 `nextbot/command_config.py:933-942` 的 `command_control` 装饰器有**对称**的形参校验（H-3 part 2 / U-2.12），两层防御等价。

3. **forward-compat 边界**：若未来 NoneBot 把 bot/event 改成 ContextVar 注入而不再通过 kwarg 传递，校验会"误警"——但 import-time hard fail 已经阻止 plugin import，等于强制开发者升级；runtime warning 只是兜底，行为正确。

---

### A-2. P-1.6 `access_control` helper `lru_cache(1)` + 不可变返回类型

**位置**: `access_control.py:77-92`

**复审结论**: **PASS**

**验证细节**:

1. **`lru_cache(maxsize=1)` 作用于零参函数 = "调用一次后永久缓存"**：
   - `get_owner_ids()` / `get_owner_ids_ordered()` / `get_group_ids()` 三个 helper 都依赖 `get_driver().config`。
   - **首次调用时机风险**：`lru_cache` 不缓存异常。若 plugin import 阶段 / module-level 触发首次调用且 `get_driver()` 还未初始化（NoneBot driver 未启动），会抛 `ValueError("Driver not initialized")`——异常不被缓存，下次调用重试，最终在 NoneBot 启动后第一次 handler 调用成功并缓存。**行为安全**。
   - **module-level 调用 grep 结果**：`grep -E "^(get_owner_ids|is_owner|has_permission)\\(\\)"` 全仓 0 命中。所有调用都在 handler / async function 内（`ban_core.py:49`、`permissions.py:72,86`、`permission_manager.py:1064` 是 re-export not call、`group_member_notify.py:53` 在 handler 内）。**当前没有 import-time 首调风险**。

2. **返回类型不可变性**：
   - `get_owner_ids()` → `frozenset[str]`：所有 `in` membership check 都是 O(1)（`ban_core.py:49`、`permissions.py:73,86`），无性能回归。
   - `get_owner_ids_ordered()` → `tuple[str, ...]`：唯一 caller 是 `permission_manager.py:653`，只做 iteration 与 `sorted(...)`，不做 `in` 检查。**任务 brief 假设的"tuple `in` O(n) 微回归"在真实调用点不成立**。
   - `get_group_ids()` → `frozenset[str]`：唯一 caller `group_member_notify.py:53` 用作 iterable 喂入 set comprehension，frozenset 与原 set 等价。

3. **mutate 防御**：frozenset / tuple 不可变，避免被调用方意外 mutate 污染 cached 引用——是 P-1.6 的核心收益。

---

### A-3. P-1.9 `audit._safe_repr` + `_coerce_snapshot`

**位置**: `audit.py:19-43`

**复审结论**: **PASS**（一处轻微 caveat）

**验证细节**:

1. **`_safe_repr` 控制字符覆盖**：
   - 对 `str` 输入：Python 内置 `repr()` **已经**转义所有控制字符（`\t`、`\x00`、`\x1b`、`\n`、`\r`），所以 `.replace("\n", "\\n").replace("\r", "\\r")` 对纯 str 是 no-op。
   - 对 `dict` / `list` 输入：Python 内置 `repr()` 保留 inner string 的字面值（包括字面 `\n`/`\r`），但**仍然**对 inner string 的 `\t`/`\x00`/`\x1b` 等做转义。所以手工补 `\n`/`\r` replace 是必要且足够的——验证：
     ```python
     repr({'k': 'a\nb'})       # → "{'k': 'a\nb'}"   ← 字面换行
     repr({'k': 'a\tb'})       # → "{'k': 'a\\tb'}"  ← 已转义
     repr({'k': 'a\x00b'})     # → "{'k': 'a\\x00b'}"
     repr({'k': 'a\x1b[31m'})  # → "{'k': 'a\\x1b[31m'}"
     ```
   - **结论**：日志注入面（`\n`/`\r` 伪造审计行）已堵死。`\t` 不会破坏 `audit` 行格式（用空格 + `=` 分隔），ANSI 转义序列被 repr 自动转义，**无残留注入向量**。

2. **`_coerce_snapshot` 类型守卫**：
   - 允许 `str / int / bool / float / dict / list / tuple / NoneType`，遇到 ORM 对象等 `logger.error` + `str(value)` 兜底。
   - ORM 对象的 `__str__` / `__repr__` 通常输出形如 `<User id=1>`（SQLAlchemy default）或类自定义。Round 7 修复后**字段值不会泄漏**（除非 ORM 类自定义 `__repr__` 把字段值塞进去）。SQLAlchemy 默认 `__repr__` 只输出主键，**安全**。
   - **微 caveat**：`str(value)` 后再走 `_safe_repr` (`audit.py:77`)，对 dict-like ORM 输出（如 `<User id=1>`）会输出 `'<User id=1>'`。攻击者无法借此泄漏敏感列（password_hash / ban_reason 等），符合 P-1.9 设计目标。
   - **logger.error 失败处理**：`_coerce_snapshot` 触发 `logger.error` 后继续返回 `str(value)`。`audit_permission_change` 主流程不被打断。若 `logger.error` 本身因 sink 异常抛错，`audit_permission_change` 会冒泡到 handler——此为系统级故障，不在 audit helper 责任范围内（属于 P-1.10 Round 7 Low / FP）。

---

### A-4. P-1.13 `ban_core._extract_blacklist_entries`

**位置**: `ban_core.py:138-152`，复用点 `ban_core.py:181, 250`

**复审结论**: **PASS**

**验证细节**:

1. **三级 isinstance 校验完整**：
   ```python
   payload = check.payload if isinstance(check.payload, dict) else {}   # 1. payload
   entries = payload.get("entries")
   if not isinstance(entries, list):                                     # 2. entries
       return []
   return [e for e in entries if isinstance(e, dict)]                    # 3. element
   ```
   - payload 为非 dict → 空 list（直接拿 `.get("entries")` 会 AttributeError，这里防御）
   - entries 为 `None` / `string` / dict → 空 list
   - 列表内非 dict element → 过滤
   - **所有路径都返回 list[dict]**，下游 `e.get("username", "")` 安全。

2. **原 inline 过滤删除**：grep `for e in entries\b` / `check.payload.get` 在 `ban_core.py` 仅命中 `_extract_blacklist_entries` 内的实现注释；`_add_one`（`ban_core.py:180-185`）和 `_remove_one`（`ban_core.py:249-254`）的调用点都改为 `entries = _extract_blacklist_entries(check)` 后 `for e in entries`，**inline isinstance 过滤被完全替换**，无重复 / 残留。

3. **边界**：
   - `payload = {}`（is_success 返回但 payload 空）→ `payload.get("entries")` 为 None → 不是 list → 返回 `[]`。`already_exists` / `exists` 计算为 False，进入 add/remove 主路径。**与 Round 6 行为兼容**（Round 6 也是把没看到的当未存在处理）。
   - `payload = {"entries": "string"}` → 直接返回 `[]`，避开 `for c in "string"` 的字符迭代陷阱（P-1.13 原始 motivation）。
   - `payload = {"entries": [{}, "junk", {"username": "x"}]}` → 返回 `[{}, {"username": "x"}]`，下游 `e.get("username", "")` 对空 dict 返回空串，正确。

---

### A-5. Round 7 跳过项确认

| 项 | Round 7 判定 | R8 是否重新提 |
|---|---|---|
| P-1.7 request-context 缓存 | 明确跳过 | **不重提**（缓存生命周期定义复杂，且 lru_cache(1) 已覆盖 99% 收益） |
| P-1.3 forward-compat docstring | Low / FP | **不重提** |
| P-1.5 forward-compat docstring | Low / FP | **不重提** |
| P-1.11 forward-compat docstring | Low / FP | **不重提** |

---

## Part B: 全量再扫新发现

### R8-P-1.14 (Low) `permissions.add_permission` / `remove_permission` 缺参数 sanitization，owner 路径下可被 CSV-inject

- **文件**: `nextbot/permissions.py:299-320`
- **触发链**：
  1. owner 调用 `添加用户权限 <target> "foo,admin.ban"`（permission 字符串含逗号）
  2. `permission_manager._check_user_perm_mutation_pola` 第一行 `is_owner(operator_id)` 短路放行（`permission_manager.py:146`），**跳过 `validate_permission_key` 和 `is_dangerous_permission`**
  3. `args[1] = "foo,admin.ban"`（`message_parser.parse_command_args_with_fallback` 以空格 split，含逗号的 token 保持为单 token）
  4. `add_permission("", "foo,admin.ban")` → `set(["foo,admin.ban"])` → `join_csv_values` → `"foo,admin.ban"`（**整串作为 ONE 元素被 join，得到含逗号的字符串**）
  5. DB 写入 `user.permissions = "foo,admin.ban"`
  6. 下次 `has_permission` 读取时 `split_csv_values` 切回两个 token `["foo", "admin.ban"]`——**用户额外获得了 `admin.ban` 权限**

- **修复前**：`add_permission` / `remove_permission` / `add_inherit` / `remove_inherit` 直接 `set.add(permission_str)`，对含 `,` 的输入无校验。
- **修复后**：在 `add_permission` 顶部加 `if "," in permission: raise ValueError("permission key 不可含逗号")`。同理 `add_inherit`（group name 含逗号会污染 inherits 链）。
- **触发概率**：**极低**。攻击面 = owner 自己。owner 已经能授任意权限，自污染 CSV 没有提权收益。但若 owner 通过 admin webhook / 脚本批量推权限时拼字符串失误（如 `"perm1,perm2"` 想分两次调用却拼到一起），会得到意外结果。
- **影响**：**owner 自伤型 foot-gun**，不构成安全边界突破。但 sanitize 成本极低（单个 `if ","` 检查），值得作为防御性编程加上。
- **严重度**：**Low**（防御加固，非真实 RCE / 越权）。

### R8-P-1.15 (Low-Medium) `audit_permission_change` 对 dict context 内 nested ORM 对象无递归类型守卫

- **文件**: `nextbot/audit.py:30-43`
- **现状**：`_coerce_snapshot` 只在 top-level 检查 `isinstance(value, _ALLOWED_SNAPSHOT_TYPES)`。若 caller 传：
  ```python
  audit_permission_change(
      actor_user_id="x", action="x", target="x",
      context={"target_user": user_orm_object},   # ← nested ORM
  )
  ```
  `_coerce_snapshot` 对 `context` dict 整体通过类型检查（dict 在 allow list），但 `_safe_repr({...})` → `repr({"target_user": <User>})` → 会把 `<User>` 的 `__repr__` 嵌入审计行。
- **真实风险**：grep `audit_permission_change` 全仓 30+ caller，context 几乎都是 `{permission, reason, target_name, group_id, sub_type, user_name}` 等 primitive 字段，**当前没有 ORM 对象 nested 传入**。但代码层面缺乏 enforcement——未来 caller 不小心传 `{"user": user_orm}` 会回退到 P-1.9 修复前的状态。
- **修复前**：单层守卫。
- **修复后**：`_coerce_snapshot` 递归检查 dict / list / tuple 的 element；遇到 ORM 等非白名单类型同样 `logger.error` + `str(...)`。或者在 docstring 强声明 "context 仅接受 primitive key/value，nested 容器层级 ≤ 2"。
- **触发概率**：**未来 caller 误用**才会触发，当前 caller 全部安全。
- **影响**：可能在未来重构 / 新 plugin 中泄漏 ORM 字段。
- **严重度**：**Low**（当前无 caller 命中），若把"未来防御"纳入考量则 **Low-Medium**。

### R8-P-1.16 (Low) `_get_effective_permissions_in_session` 缺 runtime session 类型守卫，可被传 None / closed session

- **文件**: `nextbot/permissions.py:34-49`
- **现状**：函数签名 `_get_effective_permissions_in_session(session, user_id: str)`，无 session 校验。Caller 传 `None` 或 closed session 会在 `session.query(User)...` 时抛 `AttributeError` / `InvalidRequestError`，错误冒泡到 handler。
- **当前 caller 安全**：`permission_manager.py:525` 和 `group_manager.py:481` 都在 `with get_session()` / `session = get_session(); try:` 内调用，session 必非 None 且未 close。
- **修复前**：无校验。
- **修复后**：函数顶部加 `if session is None: raise ValueError("session 不能为 None")`，或更严格地 `assert isinstance(session, Session)`。
- **触发概率**：**极低**（私有函数 `_` 前缀，仅 2 个 caller，都安全）。
- **影响**：未来调用方误用时报错点更清晰。
- **严重度**：**Low**（纯防御加固，无安全收益）。

### R8-P-1.17 (Low) `ban_core.apply_ban_to_db` already_banned 路径 `previous_reason=""` race

- **文件**: `nextbot/ban_core.py:77-89`
- **现状**：rowcount=0 后重读 user 拿 `previous_reason`。两个 admin 几乎同时封禁同一目标：
  - admin A 写 `ban_reason="reasonA"`，commit
  - admin B 的 UPDATE rowcount=0
  - admin B 重读拿到 `ban_reason="reasonA"`，返回 `already_banned previous_reason="reasonA"`
  - **正确，无 race**
- **特殊场景**：admin A commit 后**立刻** admin C 执行 unban（rowcount=1 → `is_banned=False, ban_reason=""`），然后 admin B 的重读拿到 `ban_reason=""` —— 此时 `code="already_banned"` 但 `is_banned=False`、`ban_reason=""`，**状态不一致**。
- **触发概率**：**毫秒级窗口**，需要 ban / unban / ban 在 3 个独立 admin / 3 个 session 同时发起。SQLite WAL 单写串行化时仍可能出现。
- **影响**：handler 返回"已被封禁，原因为空"误导信息。**不是权限漏洞**，是状态可见性 race。
- **修复前**：信任重读结果。
- **修复后**：重读时同时检查 `current.is_banned`——若为 False，应该按 `code="banned"` 重新执行 UPDATE（但这会引入循环重试）。或者干脆把 `apply_ban_to_db` 整体加 `BEGIN IMMEDIATE` 写锁。当前 Round 7 跳过项判 Low，**R8 维持 Low** —— 触发频率太低，状态不一致仅影响 UX 文案。
- **严重度**：**Low**（与 Round 7 P-1.12 一致）。

### R8-P-1.18 (Low) `access_control._parse_id_list` 对 nested list 不递归

- **文件**: `nextbot/access_control.py:14-15`
- **现状**：`isinstance(raw_value, (list, tuple, set))` 分支用 `{str(item).strip() for item in raw_value if str(item).strip()}`。若 `raw_value = [[1,2], 3]`，`str([1,2])` = `"[1, 2]"`，被作为一个 owner_id token 加入，下游 `if user_id in get_owner_ids()` 永远 False（user_id 不会是 `"[1, 2]"`）。**安全**，但 silently 接受了无效配置。
- **修复前**：无 nested 检查。
- **修复后**：在 list 分支内 isinstance check element，nested 容器 `logger.warning + skip`。
- **触发概率**：**配置错误**才触发，正常 `.env` 不会嵌套。
- **影响**：owner 配置错误时静默忽略（owner 路径 fail-closed）——比静默 fail-open 安全。
- **严重度**：**Low**（防御加固）。

### R8-P-1.19 (Low) `permissions.is_dangerous_permission` 不处理空字符串 / 特殊 token

- **文件**: `nextbot/permissions.py:121-139`
- **现状**：`permission == "*"` → True；`permission == ""` → False（既不在 blocklist，也不 endswith `.*`）。
- **触发链**：调用方传空串到 `is_dangerous_permission("")` → False → 进入后续 `validate_permission_key("")` → False（registry 不含空串）→ rejected。**当前 caller 都先经过 args[1] 解析，empty 已在 `split_csv_values` / `args[1]` 阶段过滤**。但 `add_permission` 直接被 owner 调用 + skip validate 时（R8-P-1.14 链路）若传 `""` 会污染 CSV（join_csv_values 会过滤空串，**实际安全**）。
- **严重度**：**Informational / Low**。

### R8-P-1.20 (Informational) `permissions.require_permission` wrapper 的 `event.get_user_id()` 不在 try / except 中

- **文件**: `nextbot/permissions.py:284`
- **现状**：`user_id = event.get_user_id()` 若 NoneBot 内部异常（event 类型不支持 get_user_id，如 NoticeEvent 子类），异常上抛到 NoneBot matcher。
- **真实风险**：所有 NoneBot V11 MessageEvent 都实现 `get_user_id()`，但 `@require_permission` 当前无显式 event 类型约束。如果未来 plugin 把 `@require_permission` 用到 NoticeEvent matcher 上，`get_user_id()` 可能抛异常。
- **严重度**：**Informational**（当前 58 个 caller 全部 MessageEvent 路径，无 issue）。

---

## 结论

### Round 7 修复复审

| 项 | 结论 |
|---|------|
| H-3 (P-1.1) `@require_permission` import + runtime fail-closed | **PASS** |
| P-1.6 access_control lru_cache + 不可变返回类型 | **PASS** |
| P-1.9 audit `_safe_repr` / `_coerce_snapshot` | **PASS**（dict nested ORM 守卫见 R8-P-1.15） |
| P-1.13 ban_core `_extract_blacklist_entries` | **PASS** |

Round 7 跳过项（P-1.7 / P-1.3 / P-1.5 / P-1.11 / P-1.12）维持原判定。

### R8 新发现

| 编号 | 严重度 | 文件 | 概述 |
|---|---|---|---|
| R8-P-1.14 | Low | `permissions.py:299-320` | `add_permission/remove_permission/add_inherit/remove_inherit` 缺逗号 sanitization（owner foot-gun） |
| R8-P-1.15 | Low（未来 Medium） | `audit.py:30-43` | `_coerce_snapshot` 不递归检查 dict / list nested ORM |
| R8-P-1.16 | Low | `permissions.py:34-49` | `_get_effective_permissions_in_session` 缺 session 非 None 守卫 |
| R8-P-1.17 | Low | `ban_core.py:77-89` | already_banned 重读路径在 ban→unban→ban 三方 race 下显示空 reason |
| R8-P-1.18 | Low | `access_control.py:14-15` | `_parse_id_list` 对 nested list 不递归 |
| R8-P-1.19 | Informational | `permissions.py:121-139` | `is_dangerous_permission("")` 边界 |
| R8-P-1.20 | Informational | `permissions.py:284` | `event.get_user_id()` 无 type guard（仅 forward-compat） |

### 总体评价

**Round 7 修复全部 PASS**。剩余发现集中在**防御加固 / 未来 caller 误用** 类，无新发现的安全边界突破。建议 R8 优先级：
1. R8-P-1.14（add_permission 逗号守卫）—— 单行修复，收益清晰
2. R8-P-1.15（audit 递归类型守卫）—— 防止未来回归
3. 其余 Low / Informational 可作为 backlog
