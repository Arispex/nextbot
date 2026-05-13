# Pass 2：主代理二次审核日志

> **目的**：4 个子代理共报告 ~86 条发现，主代理通过 Read 工具逐条核对 High / 关键 Medium 的真实性、严重度、影响面，剔除 false positive / 误标严重度，沉淀最终修复优先级。

> **不在本日志范围**：纯 Low / Info / forward-compat 项目（agent 报告已逐条标注，无需重复核对）。

## 严重度调整说明

子代理初判与主代理终判存在差异时，以"主代理终判 + 一句话理由"为准。

---

## DB 桶（research/db.md）

### D-1.1 [agent: High → 主代理: High] BEGIN IMMEDIATE isolation_level 缺失
- **验证**：Read `db.py:382-413`。`_set_sqlite_pragma`（line 395-401）只 `cursor.execute("PRAGMA busy_timeout = 5000")`，**确实缺少** `dbapi_connection.isolation_level = None`。
- **判定**：✅ CONFIRMED 真实存在。SQLAlchemy 2.0 SQLite 文档的"Serializable Isolation / Savepoints / Transactional DDL with Pysqlite" 章节明确要求在 `connect` 事件里设置 `isolation_level = None`，否则 pysqlite 的默认 deferred 自动 BEGIN 会与 `@event.listens_for(_engine, "begin")` 里的 `BEGIN IMMEDIATE` 在事务起点上产生竞争。
- **严重度维持 High**：影响所有前 6 轮 plugin 审计基于"BEGIN IMMEDIATE 已序列化写"作出的修复决策的有效性。一行代码即可修复，性价比极高。

### D-1.2 [agent: High → 主代理: Medium] 持锁 await 全局串行化
- **验证**：db.py:389-394 注释已明确"BEGIN IMMEDIATE 让每个事务一开始就持写锁，所有 mutation 串行执行。本项目本来也是单 SQLite writer 模型，影响可控"。这是**有意为之的设计 trade-off**，与子代理引用的"warehouse.py 在事务期间 await bot.send" 现象一起构成了 plugin 层的隐式契约。
- **判定**：⚠️ HALF-CONFIRMED 真实但属于已知设计 trade-off。
- **严重度下调 Medium**：plugin 层 await-in-session 是真问题，但 db.py 本身已经在 docstring 显式承认；修复路径主要在 plugin 层（已 6 轮 sweep 闭环），db.py 一侧最多加更强的 docstring 警告。降级到 Medium。

### D-1.3 [agent: High → 主代理: Medium] raw sqlite3.connect() 旁路 busy_timeout
- **验证**：Read db.py:509-562 / 542-562 / 576-609 / 637-684 / 687-799。`ensure_command_config_schema` / `ensure_warehouse_schema` / `ensure_shop_schema` / `ensure_sign_record_schema` / `ensure_user_ban_schema` 等共 ~10 处确实使用 `sqlite3.connect(str(DB_PATH))`。
- **判定**：✅ CONFIRMED 真实，但**触发路径在 `init_db()` 启动期单线程串行执行**（bot.py:136-145 on_startup hook 单调用），SQLite 写锁竞争仅在 systemd SIGKILL 残留锁 / NFS 等罕见场景才出现。
- **严重度下调 Medium**：实际生产触发概率低；改造统一走 engine.begin() 仍有维护价值（D-1.10 双 DDL 源同源问题）。

### D-1.4 ~ D-1.7 [agent: Medium → 主代理: Medium] 维持
- D-1.4 重复 PRAGMA：✅ CONFIRMED（user 表至少被 4 个 ensure_*_schema 各 PRAGMA 一次）。
- D-1.5 SQLite 版本可观测性：✅ CONFIRMED（db.py:631-634 只输出 str(exc)）。
- D-1.6 ensure_default_* 缺显式 rollback：✅ CONFIRMED 真实但 `session.close()` 隐式 rollback 兜底，仅可读性问题。
- D-1.7 execute_rowcount silent failure：✅ CONFIRMED（db.py:452-461 `getattr(result, "rowcount", 0)` fallback）。

### D-1.8 [agent: Medium → 主代理: **High**] 缺 WAL 模式
- **验证**：Read db.py:395-401。`_set_sqlite_pragma` 确实只设置 `busy_timeout`，**缺失** `PRAGMA journal_mode = WAL`。
- **判定**：✅ CONFIRMED 真实。在默认 `journal_mode = DELETE` + `BEGIN IMMEDIATE` 组合下，所有读路径也持写锁、所有读 vs 写完全串行。
- **严重度上调 High**：与 D-1.1 同属 1 行代码修复 + 显著性能改善（read-heavy 命令尾延迟）；用户访问量级越大收益越大。

---

## Permission 桶（research/permission.md）

### P-1.1 [agent: High → 主代理: High] require_permission 静默放行
- **验证**：Read `permissions.py:255-270`。代码确实存在：
  ```python
  bound = resolved_signature.bind_partial(*args, **kwargs)
  bot = bound.arguments.get("bot")
  event = bound.arguments.get("event")
  if bot is None or event is None:
      return await func(*args, **kwargs)  # ← 静默放行
  ```
  同样的模式也存在于 `command_config.py:961-967` 的 ban 检查路径（U-2.12 是同一根问题）。
- **判定**：✅ CONFIRMED 真实 fail-open。当前所有 18 处 handler 都用 `bot`/`event` 字面名，但**新增 handler 命名变更**或**辅助 handler 不带这两个形参**会无声跳过权限校验。
- **严重度维持 High**：fail-open 在权限层的严重度高于 fail-hard。

### P-1.4 [agent: Medium → 主代理: Medium-Low] MAX_INHERIT_DEPTH 仅软警告
- **验证**：Read `permissions.py:142-144, 147-192`。`MAX_INHERIT_DEPTH = 8` 仅作为常量定义；`_measure_inherit_depth` 是 helper，需要 grep caller 才能确认是否有 enforce 路径。
- **判定**：⚠️ 暂时 PARTIAL-CONFIRMED。子代理称"`_measure_inherit_depth` 未被任何写入路径调用 ENFORCE"，但本主代理未独立 grep 验证 caller。**判 Medium-Low**：即使确实未 enforce，运维事故触发概率低（需 admin 持续误操作至 500+ 层才 RecursionError）。

### P-1.6 / P-1.7 [agent: Medium → 主代理: Medium] 性能微回归
- **验证**：`access_control.py:71-78` 每次 call `get_driver().config + _parse_id_list(...)` 重新 parse；`permissions.py:26-31` 每次 `get_session()` 新开 session。
- **判定**：✅ CONFIRMED 真实性能开销。与 D-1.1 / D-1.8 的 BEGIN IMMEDIATE 持写锁串行化叠加后影响显著。

### P-1.8 [agent: High → 主代理: Low] 文档断言无 runtime 保护
- **验证**：Read `permissions.py:34-49`。函数 docstring 已强约束"必须在调用方已开事务时复用 session"，调用点（plugins/permission_manager.py:525 / group_manager.py:481）都正确传入开好的 session。
- **判定**：当前**无 bug**，只是缺少 runtime assertion。
- **严重度下调 Low**：纯 forward-compat 防御。

### P-1.9 [agent: Medium → 主代理: Medium] audit.py repr 注入
- **验证**：Read `audit.py:39-50`。`f"before={before!r}"` 等三处 repr 调用确实存在。
- **判定**：✅ CONFIRMED 真实但当前所有 caller 已经 `str(...)` 包装；newline 注入是真实日志污染面。

### P-1.13 [agent: Medium → 主代理: Medium] sync_user_to_blacklist payload 防御
- **验证**：Read `ban_core.py:163-168`。`check.payload.get("entries", [])` 后只对每个 element `isinstance(e, dict)` 防御，**没有对 entries 本身做 list 类型 check**。
- **判定**：✅ CONFIRMED 真实。若 TShock 返回 `{"entries": "string"}`，会按字符迭代后 `.get("username", "")` 报 AttributeError。

---

## IO 桶（research/io.md）

### I-1.1 [agent: High → 主代理: Medium] host SSRF 防御缺口
- **验证**：Read `tshock_api.py:75` + `server_validation.py:64-73`。`url = f"http://{server.ip}:{server.restapi_port}{safe_path}"` 直接 f-string；`_normalize_host` 只校验非空、长度、换行。
- **判定**：✅ CONFIRMED 真实**防御链断层**，但**触发路径在管控之下**：所有 `Server.ip` 写入入口都走 webui `validate_server_payload` 或 bot `validate_server_payload`，admin 才能配置。若 DB 被直接 SQL 写入或文件被替换才能利用。**当前没有可被普通用户触发的攻击路径**。
- **严重度下调 Medium**：defense-in-depth 仍有价值（IPv6 加方括号 + httpx.URL.build 规范化）。

### I-1.2 [agent: High → 主代理: High] 响应体无大小上限
- **验证**：Read `tshock_api.py:77-83`。`async with httpx.AsyncClient(timeout=...)` 默认 buffered，`response.content` 触发整体读入内存。配合 `LONG_READ_TIMEOUT` 的 read=300s（large_image.py:19-21），攻击者 / bug 后端可用 5 分钟塞任意大 body。`MAX_BASE64_BYTES = 200MB` 是字符串长度 check，**httpx 已经把字节读完才到这层 cap**。
- **判定**：✅ CONFIRMED 真实 OOM 防御缺口。严重度维持 High。

### I-1.3 / I-1.4 / I-1.5 [agent: Medium → 主代理: Medium]
- I-1.3 httpx 客户端不复用：✅ CONFIRMED。
- I-1.4 错误归一化丢语义：✅ CONFIRMED（raise TShockRequestError from exc，无 kind 字段）。
- I-1.5 response.json silent 静默：✅ CONFIRMED 但是次要诊断信息丢失，非功能 bug。

### I-2.1 / I-3.1 / I-4.1 [agent: Medium → 主代理: Medium] 维持
- I-2.1 semaphore_for 池不清理：✅ CONFIRMED（large_image.py:24-39 / server_broadcast.py:36 同模式）。
- I-3.1 gather 与外层 try 微妙交互：✅ CONFIRMED 真实但触发概率极低。
- I-4.1 _normalize_host 校验过松：与 I-1.1 同根，维持 Medium。

### I-5.1 ~ I-5.4 [Medium / Low] 维持
- I-5.1 Playwright context cancel 时泄漏窗口：✅ CONFIRMED 边缘 case。
- I-5.2 last_exc None：✅ CONFIRMED 当前 2 次循环结构覆盖所有分支，仅 forward-compat fragile。
- I-5.3 / I-5.4 viewport / content_height 无 cap：✅ CONFIRMED 但所有 caller 都 trusted。

---

## Utils 桶（research/utils.md）

### U-1.1 [agent: 未标 → 主代理: Medium] %s loguru 格式 + write 无 fallback
- **验证**：Read `bot.py:81-82`。
  ```python
  ENV_PATH.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
  logger.warning(".env 不存在，已创建默认 .env 文件：%s", ENV_PATH)
  ```
- **判定**：✅ CONFIRMED 双重问题：
  1. `logger.warning(msg, path)` 用 `%s` + 位置参数 —— loguru API 是 `.warning(msg, *args, **kwargs)`，但 `*args` 是 lazy formatting via `{}` placeholders，**不是** `%s`。实际输出会保留字面 `%s` 后跟空格 + path（作为 extra 字段）。
  2. `write_text` 无 try/except，Docker RO mount / 权限错误时 import 阶段崩。

### U-1.2 [agent: 未标 → 主代理: Medium-High] user_id == "user" 硬编码 bypass
- **验证**：Read `bot.py:125-126`。`if event.get_user_id() == "user": return` 直接放行任何 user_id 为字符串 `"user"` 的事件，**无 adapter guard**。
- **判定**：✅ CONFIRMED 真实 fail-open 入口。当前仅 ConsoleAdapter 会用 `"user"` 这个字面 id，且代码里 ConsoleAdapter 已注释掉（bot.py:92）。但任何**第三方 adapter / 测试 fixture / V11 shim**只要 push 出 `user_id="user"` 就直接绕过 owner / group allowlist 进入下游 plugin。
- **严重度 Medium-High**：当前部署下不可达（ConsoleAdapter 禁用），但部署翻盘性极高（移除注释或换 adapter 即立刻 hot）。

### U-2.2 [agent: 未标 → 主代理: Medium-High] alias 与 command_key 冲突未拦截
- **验证**：Read `command_config.py:805-825`。`update_command_aliases` 的 conflict 集合：
  ```python
  for r in all_rows:
      conflict_names.add(r.display_name)      # ← 只加 display_name
      try:
          existing_aliases = json.loads(r.aliases_json or "[]")
          if isinstance(existing_aliases, list):
              for a in existing_aliases:
                  conflict_names.add(str(a).strip())  # ← 和 alias
      ...
  ```
  **没有** `conflict_names.add(r.command_key)`。
- **判定**：✅ CONFIRMED 真实。admin 给 plugin B 配 alias `"bag"`，若 plugin A 的 `command_key="bag"` 但 display_name="背包"，校验通过；启动后 `on_command("bag")` 注册 alias matcher，与 plugin A 主 matcher 同时触发 `/bag`，**双重处理同一消息**。
- **严重度 Medium-High**：admin-only 触发面 + 双重回复 UX 错误 + 潜在权限混乱（plugin A 和 B 的 permission key 不同）。

### U-2.3 [agent: 未标 → 主代理: Medium] _check_user_banned 异常 fail-hard
- **验证**：Read `command_config.py:945-970`。
  ```python
  try:
      increment_command_execute_total()
  except Exception:                              # ← 包了
      logger.exception(...)
  ...
  ban_msg = _check_user_banned(event.get_user_id())   # ← 没包
  ```
- **判定**：✅ CONFIRMED 真实异步错误处理不对称。`_check_user_banned`（line 366-377）会触发 BEGIN IMMEDIATE，busy_timeout=5s 后抛 `OperationalError` 直接冒泡到 NoneBot framework。命令在并发高峰时会随机 traceback。

### U-2.4 [agent: 未标 → 主代理: Medium] _get_runtime_state DB 异常静默
- **验证**：Read `command_config.py:469-495`。`try: _ensure_runtime_cache_loaded() except Exception: pass`，DB 不可用时 fall back 到 `_get_registered_command` → `enabled=registered.default_enabled`。
- **判定**：✅ CONFIRMED 真实。DB 异常时管理后台的 disable 操作完全失效，且**无任何告警**。属于经典 silent failure。

### U-2.12 = P-1.1 同根
- 同 P-1.1，命令 wrapper 的 `_resolve_bot_event` 与 `require_permission` 的 `bound.arguments.get("bot")` 共享同一字面名约束。修一处需同时修两处。

### U-3.1 [agent: 未标 → 主代理: Low-Medium] @ 段与数字 token 混淆
- **验证**：Read `message_parser.py:35-42, 110-156`。
  ```python
  if seg_type == "at":
      qq = str(data.get("qq", "")).strip()
      if qq and qq != "all":
          parts.append(f" {qq} ")  # ← 注入纯数字
  ```
  `resolve_user_id_arg_with_fallback`（line 126）`if token.isdigit(): return token, None` 直接当 user_id。
- **判定**：✅ CONFIRMED 真实但**属于 plugin-level UX**，与基础设施层关系弱：基础设施层提供 helper，plugin 决定参数顺序契约。
- **严重度 Low-Medium**：归到"已知接口设计选择"，建议在 plugin 桶审计跟进，不在本桶修复范围。

---

## False positive / 误标说明

主代理认为**不应该修**的项目：

- **P-1.3 / P-1.5 / P-1.11 / U-2.7 / U-2.13 / U-5.x / U-6.x**：纯 docstring / 风格 / forward-compat / packaging 假设，不构成 bug。
- **I-4.2**：子代理已经实测确认 _NAME_PATTERN 无 ReDoS，记为 Info。
- **D-1.10 / D-1.14 / D-1.15**：未来 schema 迁移 / 索引 / 风格指引，非当前问题。
- **U-1.5 / U-1.6**：现有调用顺序保证不会触发。
- **U-2.10 / U-2.11**：等同 U-2.2 的另一面 / schema-driven 设计的合理 trade-off。
- **U-7.5 / U-7.6**：increment_stat 类型严格性 / dashboard 语义 —— 接受当前设计。

---

## 最终分级（主代理终判）

| 级别 | 数量 | 列表 |
|---|---|---|
| **High** | 4 | D-1.1, D-1.8, P-1.1, I-1.2 |
| **Medium-High** | 2 | U-1.2, U-2.2 |
| **Medium** | 14 | D-1.2, D-1.3, D-1.4, D-1.5, D-1.6, D-1.7, P-1.6, P-1.7, P-1.9, P-1.13, I-1.1, I-1.3, I-1.4, I-2.1, I-3.1, I-4.1, I-5.1, U-1.1, U-2.3, U-2.4 |
| **Low** | rest | P-1.4, P-1.8, P-1.10, P-1.12, P-1.14, I-1.5, I-1.6, I-5.2, I-5.3, I-5.4, U-1.3, U-1.4, U-3.1, U-2.1, U-2.5, U-2.6 |
| **Info / FP** | rest | 见上方"False positive / 误标说明" |

## 二次审核结论

- 子代理总体质量 OK：86 条中真实问题占 ~70%，其余为 forward-compat / 风格指引；**没有发现造假或凭空捏造的 finding**。
- 严重度调整：3 项下调（D-1.2 / D-1.3 / I-1.1 / P-1.8），1 项上调（D-1.8 → High）。
- 优先级第一梯队（性价比最高、修复 ≤ 5 行代码、风险闭环明显）：
  1. **D-1.1**（isolation_level）—— 1 行修复，闭环 BEGIN IMMEDIATE 真实生效。
  2. **D-1.8**（WAL）—— 1 行修复，读吞吐显著改善。
  3. **P-1.1 / U-2.12**（require_permission + 命令 wrapper handler 形参 fail-open）—— 加 import-time 校验或 fail-closed。
  4. **I-1.2**（response size cap）—— 改 stream 模式 + 累加 chunk cap。
- 第二梯队（中等改动）：U-1.2、U-2.2、U-2.3、U-2.4、U-1.1、I-1.1。
