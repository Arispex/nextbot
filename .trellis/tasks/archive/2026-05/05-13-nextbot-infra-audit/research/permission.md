# 权限 / 审计 / 访问控制 桶审计

## 覆盖范围

- `nextbot/permissions.py` [299 lines]
- `nextbot/access_control.py` [83 lines]
- `nextbot/audit.py` [50 lines]
- `nextbot/ban_core.py` [312 lines]

调用方交叉验证：
- `nextbot/plugins/permission_manager.py`（has_permission / `_get_effective_permissions_in_session`）
- `nextbot/plugins/group_manager.py`（has_permission / `_get_effective_permissions_in_session`）
- `nextbot/plugins/ban.py` & `nextbot/plugins/group_member_notify.py`（apply_ban_to_db 调用方）
- `nextbot/command_config.py:868`（`@command_control` decorator stack 与 `_resolve_bot_event`）
- `nextbot/db.py:393-413`（BEGIN IMMEDIATE / get_session 行为）
- `.venv/lib/python3.14/site-packages/nonebot/dependencies/__init__.py:113`（NoneBot 依赖注入以 `**values` kwargs 形态调用 handler）

## 发现列表

### P-1.1 High `require_permission` 静默放行：handler 不含 `bot` / `event` 形参时跳过权限校验

- 文件：`nextbot/permissions.py:255-270`
- 修复前行为：装饰器内部 wrapper 用
  ```python
  bound = resolved_signature.bind_partial(*args, **kwargs)
  bot = bound.arguments.get("bot")
  event = bound.arguments.get("event")
  if bot is None or event is None:
      return await func(*args, **kwargs)
  ```
  逐字面名查找 `bot` / `event`。当前所有 18 处 `@require_permission` handler 形参名一致 (grep 全量已确认，见 `plugins/economy.py:281`、`plugins/ban.py:60`、`plugins/permission_manager.py:209`...) ，但 fail-open 的语义意味着：任何未来新增 handler 写成 `async def h(b: Bot, e: Event, ...)`、`async def h(bot: Bot | None = None, event: Event | None = None, ...)`、或不带 `bot`/`event` 形参的辅助 handler（比如重定向到 service 层），权限校验都会被无声跳过——既不报错也不写 audit 行。
  Owner 也使用同一短路路径，所以这条静默放行不会在 owner 测试中被发现；只有当一个 guest 调用形参不符合命名约定的 handler 时才暴露。
- 修复后行为：
  - 把短路改为「fail-closed」：`bot is None or event is None` 时记一行 WARN 并 `return`，而不是放行。
  - 或在装饰器构建时静态校验 `signature.parameters` 必须包含名为 `bot` 和 `event` 的形参，否则在 import 阶段直接抛 `RuntimeError(f"@require_permission 装饰的 {func.__qualname__} 必须有 bot 和 event 形参")`。后者最安全，因为是 import-time 检测。
- 触发概率：中（约束依赖团队 review 习惯而非代码层防御；新增 handler / 命名变更时极易踩坑，且不会有报错信号）。
- 影响范围：被装饰器静默跳过的 handler 等价于无权限保护；如果该 handler 落在 `economy.coins.add`、`server_tools.execute` 等 RCE-等价 key 上，等同于权限完全绕过。

### P-1.2 Medium `typing.get_type_hints` 异常 swallow 后 `Annotated` 元数据丢失，NoneBot 依赖注入有概率失败

- 文件：`nextbot/permissions.py:236-253`（与 `command_config.py:925-943` 形成对称问题）
- 修复前行为：
  ```python
  try:
      type_hints = typing.get_type_hints(func, include_extras=True)
  except Exception:
      type_hints = {}
  ```
  当被装饰 handler 引用了 `from __future__ import annotations` 下的前向引用 + 未导入的类型（罕见但发生在跨模块循环 import 时），`get_type_hints` 会抛 `NameError` 被 swallow。`type_hints` 退化为 `{}`，重建的 `parameters` 仍保留 `parameter.annotation`（已是字符串），但对 `Annotated[Dict, _STATE_FLAG]` 这类 NoneBot 依赖标记，字符串 annotation 无法被 NoneBot 的 `parse_params` 识别为 `T_State` / `T_Bot` / `T_Event`，导致这个参数会被当作未知参数尝试匹配 `Default` Param 类型并失败，handler 在 dispatch 阶段抛 `TypeError`。
  这是一个 silent degradation：handler 注册时不会报错（permissions.py 这层），运行时第一次被调用才暴露。
- 修复后行为：把 `except Exception: type_hints = {}` 改为 `except Exception as exc: logger.warning(f"无法解析 {func.__qualname__} 类型注解：{exc}"); type_hints = {}`，至少留下 forensic trail；更激进可以让它直接抛出，让注册期就失败而不是运行期。
- 触发概率：低（需要循环 import + 前向引用同时存在）。
- 影响范围：单个 handler 注册看似成功、第一次触发时崩溃。属于「错误信息丢失」性质，不是权限绕过。

### P-1.3 Medium `has_permission` owner 短路绕过了 `validate_permission_key` 与 `is_dangerous_permission` 的 forward-compat 隐式契约

- 文件：`nextbot/permissions.py:71-76`
- 修复前行为：`has_permission` 在 owner 时直接 `return True`，不读 DB。但调用方需要分清两件事：
  1. owner 在「`has_permission(operator_id, target_permission)` 是否满足」上必然 True（POLA 检查）。
  2. owner 在「`target_permission` 是否合法 / 未来 forward-compat 是否能授」上需要绕过 registry。
  现在 (1) 是装饰器主流量，(2) 是 `plugins/permission_manager.py:160`、`plugins/group_manager.py:674` 显式调用 `is_owner` 短路 `validate_permission_key`。设计是对的，但留下一处 forward-compat 风险：如果未来代码引入 `has_permission(operator_id, "some.new.unregistered.key")` 风格的代码（比如自动 promotion / wizard），owner 路径会立刻 True，但被赋予者在调用 handler 时 `validate_permission_key` 在 grant 时还会被检查（因 grant 入口已显式 is_owner 短路），看似对偶，实际是「两层短路」的设计：任何一层漏短路就会有 owner 无法授新 key 的退化。
  当前代码没有 bug，但 owner 短路在 `has_permission` 内部和 grant handler 各自实现一次，缺少集中的 docstring 强约束。这是文档 / 契约层面的脆弱点，不是 runtime bug。
- 修复后行为：建议在 `has_permission` 与 `validate_permission_key` 之间加一段共享 docstring/常量：
  ```python
  # CONTRACT: all permission-mutation entry points MUST is_owner-shortcut
  # BEFORE calling validate_permission_key, OR call has_permission(operator,
  # permission) which already short-circuits for owner. validate_permission_key
  # does NOT short-circuit owner internally to keep its semantics pure.
  ```
- 触发概率：低（取决于未来代码引入新短路点的纪律性）。
- 影响范围：forward-compat 退化路径，不影响当前运行。

### P-1.4 Medium `_get_group_permissions` 递归未受 `MAX_INHERIT_DEPTH` 约束

- 文件：`nextbot/permissions.py:52-68`、对照 `nextbot/permissions.py:142-144`、`nextbot/permissions.py:176-192`
- 修复前行为：
  - `_get_group_permissions` 是 **运行时** 路径（被 `has_permission` 间接调用），用 `visited: set[str]` 做循环检测，但不限深度。
  - `MAX_INHERIT_DEPTH = 8` 只在 `_measure_inherit_depth` 与「`group.inherit.add`」管理类命令的护栏中生效（参考 `permissions.py:176`，但 `_measure_inherit_depth` 本身没有被 enforce 出现在文件内的搜索结果中——只有 `validate_inherit` 之类管理路径可能调用）。
  - 实际写入路径（`group.inherit.add`）只在 add 时做环检测（`_would_create_inheritance_cycle`），但没有 ENFORCE 链深度。也就是说：**通过多次 `group.inherit.add` 跨越多代，**`MAX_INHERIT_DEPTH` 仅作为「软警告」未真正拦截写入**（grep 全量未发现写入路径会 reject if depth > 8）。
  - 如果一个具有 `group.inherit.add` 权限的 admin 误操作（或被 take-over）建一条 `a → b → c → ... → z` 的链，`_get_group_permissions` 每次 `has_permission` 都要 N 次 SELECT，且 Python 默认递归深度 1000，理论 ~ 500 层就会 RecursionError，整个 bot 命令开始崩溃（不是 silent，会被 log，但属于运维事故）。
- 修复后行为：
  - 把 `MAX_INHERIT_DEPTH` 真正在 `group.inherit.add` / `group.add`（含 inherits 字段）写入路径前做硬拦截，超过深度即 reject。
  - `_get_group_permissions` 加 depth guard 形参（runtime defense）：超过 `MAX_INHERIT_DEPTH * 2` 时 `logger.warning` 并截断，避免触底 RecursionError。
- 触发概率：低（需要 admin 持续误操作或恶意行为）。
- 影响范围：可被滥用做 DoS（让任意 `has_permission(...)` 命中此 group 链都 RecursionError）；正常运维下不会触发。

### P-1.5 Medium `_get_group_permissions` 在 `visited.copy()` 上的额外开销 vs `_measure_inherit_depth` 的一致性矛盾

- 文件：`nextbot/permissions.py:176-192`（`_measure_inherit_depth`）
- 修复前行为：`_measure_inherit_depth` 对每个 parent 用 `visited.copy()`，让多分支测量「该分支的最长路径」是正确的；但和 `_get_group_permissions:52-68` 共享 visited 的「并集」语义不一致——后者的 visited 不 copy（用于环检测），如果调用方误把这两个函数当成对偶 helper 使用，会得到不同语义。
  当前代码没有混用，只是命名相近，未来 refactor 风险。
- 修复后行为：把 `_measure_inherit_depth` 改名为 `_max_inherit_path_length`，明确「测量最长路径」语义；或在 docstring 强调「与 `_get_group_permissions` 的 visited 语义不同」。
- 触发概率：低（重构期才暴露）。
- 影响范围：维护性 / 文档脆弱性，不是 runtime bug。

### P-1.6 Medium `get_owner_ids` 多次 `get_driver().config` 调用 + 全量重新 parse，无缓存

- 文件：`nextbot/access_control.py:71-78`、调用方 `nextbot/permissions.py:72`、`nextbot/permissions.py:86`、`nextbot/ban_core.py:48`、`server/routes/webui_users.py:560`、`bot.py:105`
- 修复前行为：每次 `has_permission` / `is_owner` / `apply_ban_to_db` / WebUI 用户校验都重新 call `get_driver().config` + 跑 `_parse_id_list`（包括 `text.startswith("[")` 判断 + `json.loads` + 集合构造）。`OWNER_ID=["1291525582"]` 的真实配置每次都走 JSON parse 路径。
  在 NoneBot 主流量下 `get_driver().config` 是 module-level cached，开销低；但是 `_parse_id_list` 每次都构造新 `set`，对 `has_permission` 的高频调用是无意义重复（每条 plugin command 至少触发 1 次 `has_permission` → 1 次 `get_owner_ids` → 1 次 parse）。
- 修复后行为：用 `functools.lru_cache(maxsize=1)` 包装 `get_owner_ids` / `get_owner_ids_ordered`；或在 module load 时一次性 parse 缓存为 `_OWNER_IDS: frozenset[str]`。NoneBot 的 config 在 runtime 不会变更，缓存安全。
- 触发概率：高（每条命令都触发）。
- 影响范围：性能微回归（每命令多几十 μs），不影响正确性；但配合 P-1.7 的「每次开新 session」累积成 noticeable 延迟。

### P-1.7 Medium `has_permission` 每次调用都开 / 关 SQLite session（叠加 BEGIN IMMEDIATE）

- 文件：`nextbot/permissions.py:26-31`、`nextbot/permissions.py:71-76`
- 修复前行为：`has_permission(user_id, "x.y.z")` 流程：
  1. `get_owner_ids()` → 重新 parse `.env`
  2. 若不是 owner → `get_effective_permissions(user_id)` → `get_session()` 拿新 session →（SQLite engine 触发 `BEGIN IMMEDIATE`，全 DB 写锁）→ 2 次 SELECT（User + Group 至少一层）→ close。
  在 NoneBot 单条命令的生命周期里，`require_permission` 装饰器调一次 has_permission，POLA 检查在 handler 内部又调一次 has_permission（参考 `permission_manager.py:181`、`group_manager.py:693`、`group_manager.py:797`），即同一条命令两次 BEGIN IMMEDIATE + 4-N 次 SELECT。
  在写命令并发时，BEGIN IMMEDIATE 让所有读也持写锁；权限检查是高频读操作，每次都打写锁会显著拉长写事务排队。
- 修复后行为：
  - request 级缓存：在 `nextbot.command_config._current_command_context` 里加 `effective_perms_cache: dict[str, set[str]]`，同一 user_id 在同一 command 调用内只 query 一次。
  - 或者更简单：把 `validate_permission_key`、POLA、`is_dangerous_permission` 三步合并成一个函数 `check_user_perm_mutation(operator, perm) -> tuple[bool, str | None]`，内部只 query effective_perms 一次。
  - 不必上 LRU 缓存（涉及 invalidate 时机问题，admin 改权后必须立即生效）。
- 触发概率：高（每条命令至少 1 次，权限管理类 2 次）。
- 影响范围：BEGIN IMMEDIATE 下高并发时事务排队加剧，但不会让命令失败；属于性能项。

### P-1.8 High `_get_effective_permissions_in_session` 不复用调用方 session 时是否真的死锁？文档断言与代码现状一致，但 runtime 没有断言保护

- 文件：`nextbot/permissions.py:34-49`、调用方 `nextbot/plugins/permission_manager.py:525`、`nextbot/plugins/group_manager.py:481`
- 修复前行为：函数 docstring 写明「复用调用方 session 避免 BEGIN IMMEDIATE 嵌套死锁」，但函数签名 `session` 是 untyped、可以被调用方传入任何东西（含 `None`）。如果未来某次 refactor 错把 `get_session()` 传进来当 session（而不是当前已开的 session），会立即出现两个 BEGIN IMMEDIATE 嵌套，触发 5s `busy_timeout` 后 `OperationalError: database is locked`。
  目前所有 2 个调用点都正确传入「已开的 session」，但没有任何 type hint / assertion 防止误用。
- 修复后行为：
  - 给 session 形参加类型注解：`session: Session`（从 sqlalchemy import）。
  - 在函数入口加 `assert session.in_transaction(), "_get_effective_permissions_in_session 必须在已开事务中调用"` 作为 runtime defense；或包一个 contextmanager helper `with shared_session() as s: ...` 让误用在静态分析层暴露。
- 触发概率：低（取决于未来 refactor 纪律）。
- 影响范围：误用会直接导致 5s 超时后整个命令 fail，但不会 silent；属于 forward-compat 防御。

### P-1.9 Medium `audit.py` 缺少敏感字段 redact / `repr` 注入风险

- 文件：`nextbot/audit.py:39-50`
- 修复前行为：
  ```python
  parts.append(f"before={before!r}")
  parts.append(f"after={after!r}")
  parts.append(f"context={context!r}")
  ```
  - 任何对象的 `__repr__` 都会被原样输出到 logger.warning（再写到日志文件 / stdout）。
  - 在 audit 调用方（`permission_manager.py:151-188`、`server_manager.py:66-207`、`user_manager.py:553`），context 字段经常带 `target_name`、`permissions` CSV、`ban_reason` 等用户可控字段。如果一个 actor 把恶意 user_name 设为含换行的字符串（比如 `"alice\n[WARN] 权限审计：actor=owner ..."`），`repr` 会 escape 但 dict `{"target_name": "alice\\n..."}` 在日志里仍可能被运维误读。
  - 真正风险点：`context={"target_name": result.user_name}` 时（`plugins/ban.py:117`），`user_name` 是从 `User.name`（DB 字段）来的；DB 字段是否被验证过格式？grep 显示 `User.name` 在注册时由 `user.register` 写入（来自前端输入），可能未做严格 sanitize。
  - 另一个隐患：`before=str(some_user_id)`、`target=str(result.user_qq)`，如果传入的不是 str 而是 `User` ORM 对象，`f"{x!r}"` 会调用 `User.__repr__()`，泄漏全部字段（含 `password_hash` / `email` / `ban_reason` 等不该上日志的列）。当前所有调用方都已 `str(...)` 包装，但没有 type hint enforce。
- 修复后行为：
  - `target: str` / `actor_user_id: str` 形参已正确标注 str，建议 `before` / `after` / `context` 加 `isinstance(...)` runtime 校验（只接受 `str | int | dict | None`，遇到 ORM/object 拒收并 log 一行 ERROR）。
  - 对 `target_name` 之类用户可控字段做 newline / control-char strip，避免日志注入：
    ```python
    def _safe_repr(v: Any) -> str:
        s = repr(v)
        return s.replace("\n", "\\n").replace("\r", "\\r")
    ```
  - 评估是否在 audit 中暴露 `ban_reason`（前端可输入文本）；如允许，建议 truncate 到 200 字符。
- 触发概率：低-中（依赖 user.name / ban_reason 是否做了 sanitize；目前 grep 未见显式校验）。
- 影响范围：日志注入 / 误读，攻击者可在审计行里伪造看似合法的「actor=owner action=xxx」字符串污染审计流。无权限绕过。

### P-1.10 Low `audit_permission_change` 写日志失败时无 fallback，导致主流程异常

- 文件：`nextbot/audit.py:50`
- 修复前行为：`logger.warning(...)` 在极端场景（log handler 抛异常、磁盘写满）会向上传播 exception，污染主流程。permission_manager 的 audit 调用都在「成功路径」末尾，logger 失败会让命令报错回给用户，但 DB 已 commit，导致「命令成功但提示失败」的不一致状态。
- 修复后行为：
  ```python
  try:
      logger.warning(f"权限审计：{' '.join(parts)}")
  except Exception:
      # 审计写入失败时不可让主流程崩溃，但要让 stderr 留 trace
      import sys
      print(f"AUDIT_FALLBACK: {' '.join(parts)}", file=sys.stderr)
  ```
- 触发概率：极低（logger 通常很稳）。
- 影响范围：极端运维场景下，避免「数据库变了但 UI 报错」的状态分裂。

### P-1.11 Medium `apply_ban_to_db` owner 保护检查 vs `UPDATE WHERE is_banned=False` 之间存在「短暂窗口」，但被 BEGIN IMMEDIATE 兜底

- 文件：`nextbot/ban_core.py:40-92`
- 修复前行为：
  ```python
  user = session.query(User).filter(User.user_id == user_id).first()
  if user is None: ...
  if str(user.user_id) in get_owner_ids():  # check
      return BanDBResult(code="owner_protected", ...)
  ...
  rowcount = execute_rowcount(session, update(User).where(...).values(...))
  ```
  在 BEGIN IMMEDIATE 下，整个 session 已持写锁，所以 `get_owner_ids()` 是 `.env` 静态值（不会并发变化）+ SQL 串行化，不会出现「check-then-act race」。设计正确。
  但有一个**未受保护的边界**：如果未来 `OWNER_ID` 从 `.env` 改为可热 reload 的 DB 字段（feature plan 里有可能），`get_owner_ids()` 就会变成 DB 读，且未与同一 session 复用——双 session 读 + 写锁会立刻死锁（与 P-1.8 同类）。
  目前没 bug，是 forward-compat 隐患。
- 修复后行为：把 owner 来源限定为「.env / 进程内 frozen 集合」做契约化：
  ```python
  # CONTRACT: get_owner_ids() must remain a process-local read with no DB
  # access, so apply_ban_to_db / has_permission can call it inside their
  # own transaction without nested-session deadlock.
  ```
  写到 `access_control.py` docstring 顶端。
- 触发概率：低（受未来 feature 决策影响）。
- 影响范围：forward-compat 防御。

### P-1.12 Low `apply_ban_to_db` `already_banned` 重读路径在 session 外、`previous_reason` 来源不明

- 文件：`nextbot/ban_core.py:75-88`
- 修复前行为：
  ```python
  session.commit()  # commit 解锁
  if rowcount == 0:
      current = session.query(User).filter(...).first()
      ...
      return BanDBResult(code="already_banned", previous_reason=str(current.ban_reason or ""))
  ```
  `session.commit()` 在 SQLAlchemy 里会 expire 所有 ORM 对象。`current = session.query(...).first()` 在 commit 后立即重读，会触发新的 implicit transaction（再次 BEGIN IMMEDIATE）。逻辑正确，但**存在一个亚秒级窗口**：另一个 admin 在第一次 UPDATE 失败到 re-SELECT 之间执行了 unban，那么 `current.ban_reason` 是 ""，返回 `previous_reason=""`，反馈给用户「该用户已被封禁，原因：」空字符串。
  这不是严重 bug，但消息体很怪。
- 修复后行为：
  - 在 commit 前用 `session.refresh(user)` 拿到一致快照，然后再 commit；
  - 或者 `already_banned` 分支也走条件 UPDATE 拿到 currenct ban_reason（SQLite UPDATE...RETURNING 受 3.35+ 支持）。
- 触发概率：极低（双 admin 同时操作同一目标的窗口 + 一个 ban 一个 unban）。
- 影响范围：UI 反馈空 reason，无安全影响。

### P-1.13 Medium `sync_user_to_blacklist` / `sync_user_blacklist_remove` 的 `entries` 解析未防御 nested 异常

- 文件：`nextbot/ban_core.py:154-198`、`nextbot/ban_core.py:224-265`
- 修复前行为：
  ```python
  check = await request_server_api(server, "/nextbot/blacklist")
  if is_success(check):
      entries = check.payload.get("entries", [])
      already_exists = any(
          str(e.get("username", "")).lower() == user_name.lower()
          for e in entries
          if isinstance(e, dict)
      )
  ```
  - `check.payload` 是否一定 dict？`is_success(check)` 通常验证 status，但若 server 返回 `{"data": null}` 或 `{"entries": "not-a-list"}`，`payload.get("entries", [])` 会返回 `"not-a-list"`（字符串），`for e in "not-a-list"` 会迭代每个字符 → `e.get(...)` 抛 `AttributeError` 因为 char 不是 dict → 整个 `_add_one` 抛出 → 让 `broadcast` 的 per-server outcome 缺失。
  - 实际有 `if isinstance(e, dict)` 防御每个元素，但没防御 entries 本身不是 list 的场景；外层 `check.payload` 也没 `isinstance(check.payload, dict)` 防御。
  - 同样问题对 `sync_user_blacklist_remove` 的 `exists` 检查（line 232-238）。
- 修复后行为：
  ```python
  payload = check.payload if isinstance(check.payload, dict) else {}
  entries = payload.get("entries") or []
  if not isinstance(entries, list):
      entries = []
  ```
  最终封一个 helper `_extract_blacklist_entries(check) -> list[dict]`。
- 触发概率：低（依赖 TShock server 是否返回 well-formed JSON）。
- 影响范围：单个 server outcome 异常上抛到 `broadcast` 层处理；若 `broadcast` 没 try/except per task，整个广播被中断，剩余 server 不再处理。建议交叉看 `server_broadcast.py` 的 gather 行为。

### P-1.14 Low `sync_user_to_blacklist` 的 `_load_servers` 与 add 操作分离，无快照锁

- 文件：`nextbot/ban_core.py:129-134`、`nextbot/ban_core.py:147-149`
- 修复前行为：`_load_servers()` 内 `session.query(Server).order_by(...).all()` 然后立即 close session。返回 ORM 对象。close 之后访问 `server.id`、`server.name`、`server.host` 等属性，依赖 SQLAlchemy 的 expire_on_commit / lazy-load 行为：
  - SQLAlchemy 默认 `expire_on_commit=True`，但**这里没有 commit**，只是 close。`session.close()` 在 SQLite + autoflush=False 下不会 expire 已 loaded 列。
  - 已读取的列（在 query 时取的）可以访问；但 deferred 列 / relationships 不能。
  - 当前 `Server` 模型的常用字段（`id` / `name` / `host` / `token`）通常是非 deferred，能安全访问。属于「依赖模型 schema 当前实现」的隐式契约。
- 修复后行为：
  - 显式 `session.expunge_all()` 后 close，或返回 `list[dict]` 而非 ORM 对象，让契约显式化。
  - 或保留 session：`async def _sync_one(...)` 拿 server.id / server.name 的字符串拷贝，业务侧不依赖 ORM。
- 触发概率：低（取决于 Server 模型是否新增 deferred 列）。
- 影响范围：未来增加 relationship 时第一次访问会 raise `DetachedInstanceError`。

### P-1.15 Info `audit.py` 时间戳 / 级别由 logger 输出，与 CLAUDE.md「[timestamp] [level] <message>」规范一致

- 文件：`nextbot/audit.py:11`、`nextbot/audit.py:50`
- 修复前行为：docstring 声明「`[WARN] 权限审计：...`」交由 nonebot/loguru 输出 timestamp + level，业务消息不重复 timestamp / level。**符合规范**。无 finding。
- 备注：仅作为对照，确认审计入口未犯「业务消息里手写时间戳」错误。

## 结论

四个文件的关键路径（owner 短路、blocklist、registry validate、环检测）以及前几轮已修过的并发 / 双写 / 隔离都仍然成立。本轮重点关注的 **`require_permission` 装饰器静默放行（P-1.1）** 是当前 highest-actionable finding——技术上没暴露，但属于「再加一行 fail-closed log + handler 形参 import-time 校验」即可消除的 class-of-bug，建议优先落地。

其余 medium 项集中在三类：

1. **forward-compat 文档/契约脆弱**（P-1.3 / P-1.8 / P-1.11）：当前代码正确，但缺少 docstring + type hint + assertion 阻止未来 refactor 引入嵌套 session / 跨 session 调用。
2. **性能微回归**（P-1.6 / P-1.7）：每条命令两次以上的 `has_permission` + 每次新 session + BEGIN IMMEDIATE，可通过 request 级缓存或合并 POLA 检查显著降低。
3. **日志 / 审计安全防御**（P-1.9 / P-1.10 / P-1.13）：审计 repr 注入与 logger 失败回退、TShock payload 解析的 nested type defense。

`_get_group_permissions` 的深度问题（P-1.4）唯一可能引发运维事故的项，建议把 `MAX_INHERIT_DEPTH` 从「软警告」升级为「写入路径硬拦截 + 运行时 depth guard」。
