# R9 Utils 桶审计

- **Query**: Round 9 第二次复审 Utils / Bot / Command Config 桶（复审 Round 8 修复 + 全量再扫）
- **Scope**: internal
- **Date**: 2026-05-13
- **Files audited**:
  - `/Users/arispex/CascadeProjects/nextbot/bot.py`（189 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py`（1058 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/message_parser.py`（157 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/text_utils.py`（138 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/time_utils.py`（75 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/progression.py`（57 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/stats.py`（137 行）
  - `/Users/arispex/CascadeProjects/nextbot/nextbot/data_dir.py`（32 行）

---

## Part A: Round 8 修复复审（3 项 + self-fix）

### A.1 M-3 part 2 `bot.py` WAL checkpoint shutdown hook + LIFO 顺序 — **PASS**

- 位置：`bot.py:168-184`，`nextbot/db.py:942-961`
- **LIFO 行为验证**：
  - NoneBot SDK 源码 `.venv/lib/python3.14/site-packages/nonebot/internal/driver/_lifespan.py:79-81` 实际逻辑：
    ```python
    if self._shutdown_funcs:
        # reverse shutdown funcs to ensure stack order
        await self._run_lifespan_func(reversed(self._shutdown_funcs))
    ```
  - 注释明确 "reverse … to ensure stack order"，行为 = LIFO。
- **执行顺序验证**：
  - 注册顺序：`_wal_checkpoint`（`bot.py:173`）→ `_close_shared_http_client`（`bot.py:180`）。
  - LIFO 执行：`_close_shared_http_client` 先（关 httpx），`_wal_checkpoint` 后（truncate WAL）。
  - 与 `bot.py:168-172` 注释逻辑一致：先关 HTTP 客户端，避免 in-flight 请求在 checkpoint 后产生新 WAL 帧。
- **`wal_checkpoint_truncate` 实现**（`db.py:942-961`）：
  - 仅当 `DB_PATH.exists()` 时执行（新部署、`NEXTBOT_DATA_DIR` 还未建库时安全跳过）。
  - `engine.begin() …PRAGMA wal_checkpoint(TRUNCATE)`，失败时 `logger.warning` 不抛，避免 shutdown 中断。
  - 不调用 `engine.dispose()`，注释明确委托给进程退出回收（OK，因为 shutdown 阶段后进程立即退出）。
- **风险提示（非缺陷，记录给后续审计）**：
  - 若未来 NoneBot 升级把 `reversed(...)` 改为 FIFO，hook 顺序会反转 → 必须修改 `bot.py` 注册顺序。此契约依赖 NoneBot 实现细节而非公开 API，建议在 `bot.py` 注释中已明确标注（line 168-172 已写明）。
  - `wal_checkpoint_truncate` 内只调用一次 `engine.begin()`，不主动 ROLLBACK 其它正在进行的事务；若有 background task 持锁，TRUNCATE 会因 `SQLITE_BUSY` 失败 → 退化为 warning，可接受。

### A.2 M-4 (R8-U-B-1) `command_config.py` alias 自查 — **PASS**

- 位置：`command_config.py:846-875`
- **实际实现与任务描述伪代码不同**：
  - 任务描述担心 `if alias in {normalized_key, row.display_name, *cleaned}` 中 `*cleaned` 导致永真。实际代码用了两个独立 set：
    ```python
    self_conflict_names: set[str] = {normalized_key, row.display_name}
    seen_in_batch: set[str] = set()
    for alias in cleaned:
        if alias in self_conflict_names: ...       # 自查
        if alias in seen_in_batch: ...             # batch 内重复
        seen_in_batch.add(alias)                    # 增量加入
        if alias in conflict_names: ...            # 与其他命令冲突
    ```
  - `seen_in_batch` 增量构建，循环首次见到 `alias` 时 `seen_in_batch` 不包含它（在 `seen_in_batch.add` 之前判定），所以**不会误报自身重复**。
  - `self_conflict_names` 只含 `{normalized_key, row.display_name}`，不含 `cleaned`，逻辑正确。
- **覆盖场景**：
  - alias == command_key → 自查 reject（防 register_alias_matchers 双注册）。
  - alias == display_name → 自查 reject（防止 display_name 同时是 alias 和 command 主名）。
  - alias 在 batch 内重复（如 `["foo", "bar", "foo"]`）→ batch 重复 reject。
  - alias 与其他命令的 command_key / display_name / 其 alias 冲突 → conflict_names reject。
- **边角**：`row.display_name` 来自 DB，可能为空字符串吗？查 `command_control`（`command_config.py:933`）规范化为 `display_name or command_key`，存库时 `sync_registered_commands_to_db` 直接写入，所以 `display_name` 一定非空。OK。

### A.3 R8-U-B-2 `_runtime_cache_load_failure_logged` throttle — **PASS（实现优于描述）**

- 位置：`command_config.py:75-80, 475-495`
- **实际实现是 60s 时间窗口节流，不是任务描述担心的"永不重置布尔 flag"**：
  ```python
  _runtime_cache_last_load_error_at: float = 0.0
  _RUNTIME_CACHE_ERROR_THROTTLE_SEC: float = 60.0
  ...
  now = time.monotonic()
  if now - _runtime_cache_last_load_error_at >= _RUNTIME_CACHE_ERROR_THROTTLE_SEC:
      logger.exception(...)         # 完整 stack
      _runtime_cache_last_load_error_at = now
  else:
      logger.warning(...)            # 简短 throttled 警告
  ```
- **DB 恢复后再次故障**：因为时间戳 ≥ 60s 间隔会过期，所以恢复后再次故障**会重新打完整 stack**。不存在任务描述担心的"漏报后续失败"。
- **次要观察（不构成缺陷）**：
  - `_runtime_cache_last_load_error_at` 全局变量在 `_get_runtime_state` 中**未持锁**写入。并发场景下可能出现两个线程同时通过门槛检查、都打一次 `logger.exception`。
  - 这是 at-most-once-per-60s 的策略性日志，多打 1～2 条 stack 不会引发日志风暴或正确性问题，符合 Round 8 的设计意图。
  - 即便严格化，也只需在写入处加 `_registry_lock`，但当前实现可接受。
- **次要观察**：throttle 只覆盖 `_get_runtime_state` 路径；`list_command_configs` / `get_command_config` 通过 `_ensure_runtime_cache_loaded` 也会触发 refresh 异常但**不会进入 throttle 路径**（异常直接上抛给调用方）。这两个 API 由 Web UI 调用，调用频率有限，不会风暴，但需注意 throttle 不是全路径覆盖。

### A.4 关键 self-fix：`_wal_checkpoint` 注册顺序 — **PASS**

- 位置：`bot.py:173-184`
- 配合 A.1 验证完成。注释（`bot.py:168-172`）明确说明 LIFO 与意图："注册在前 → 执行在后"。

---

## Part B: Round 7 修复保留性确认（7 项）

| 编号 | 位置 | 保留状态 |
|---|---|---|
| **MH-1 (U-1.2)** Console adapter guard | `bot.py:132-140` | **PASS** — `adapter.get_name() == "Console"` 检查保留，try/except 包 `adapter.get_name()` 防止 adapter 缺接口。 |
| **MH-2 (U-2.2)** alias 加 command_key 比较 | `command_config.py:831-844` | **PASS** — 循环加 `conflict_names.add(r.command_key)` + `r.display_name` + existing aliases，三者都校验。 |
| **U-1.1** `ensure_env_file` try/except + f-string | `bot.py:77-89` | **PASS** — `try/except OSError`，错误 / 成功消息都用 f-string，权限不足 / 磁盘满 / RO mount 不再 crash 进程。 |
| **U-2.3** wrapper 包 `_check_user_banned` | `command_config.py:1021-1031` | **PASS** — `try/except Exception` 包 `_check_user_banned`，DB 故障 `logger.exception` + `ban_msg = ""` fail-soft 放行（符合"DB 故障不应阻塞命令"策略）。 |
| **U-2.4** `_get_runtime_state` DB 异常 logger.exception | `command_config.py:479-495` | **PASS** — 在 Round 8 throttle 包裹下保留，仍有 `logger.exception`（throttle 窗口首次）。 |
| **U-2.12 / H-3 part 2** command_control import-time bot/event check | `command_config.py:975-984` | **PASS** — `if "bot" not in param_names or "event" not in param_names: raise RuntimeError` 在装饰器执行时（import 时）触发。 |
| **I-1.3** shutdown hook for tshock_api | `bot.py:180-184` | **PASS** — `_close_shared_http_client` 钩子注册，调用 `nextbot.tshock_api.close_shared_client()`。 |

---

## Part C: 全量再扫新发现

### C.1 `command_config.py`

#### C.1.1 `refresh_runtime_cache` 异常时 cache 半填充 — **NEW LOW**

- 位置：`command_config.py:451-465`
- 当 `_to_runtime_state(row)` 在某行抛异常时（例如 `param_schema_json` 损坏触发 `_normalize_param_schema` 抛 `CommandConfigValidationError`），`runtime` 局部变量被丢弃，**`_runtime_cache_ready` 仍保持 False**，下次调用还会重试。
- **看似 OK**：失败时不会污染老 cache，调用方拿到老 cache（如果之前 ready）或 fallback 到 `RegisteredCommand`。
- **但有 silent skip 风险**：`_to_runtime_state` 内 `_normalize_param_schema` 是会抛的（`command_config.py:241-305`）；DB 中存在损坏 row 时整次 refresh 失败，导致**所有命令都退化到 RegisteredCommand fallback**（即 `enabled=default_enabled, aliases=[]`）。
- 单条损坏 row 影响全表，建议（**仅记录，不修复**）：循环内 per-row try/except + `logger.exception(row.command_key)`，损坏 row 退化到 fallback，其它 row 正常 cache。

#### C.1.2 `register_alias_matchers` 启动期重复注册风险 — **NEW LOW**

- 位置：`command_config.py:893-915`
- `register_alias_matchers` 在 `bot.py:163-164` 启动钩子调用，**当前没有幂等保护**。若任何场景再次调用（例如 hot reload / 测试 / Web UI 触发），同一个 `(alias, command_key)` 对会 `on_command(alias)` 第二次，导致**别名双注册**。
- 但项目现状只在启动调用一次，运行时 alias 变更只更新 DB cache、不重新注册 matcher（即修改 alias 不会立即生效，需重启），所以**不会触发当前缺陷**。但代码层面缺少幂等防护，记录为低风险。

#### C.1.3 `update_command_aliases` 未提示"需重启生效" — **NEW INFO**

- 位置：`command_config.py:788-887`
- 该接口保存 alias 到 DB 并 `refresh_runtime_cache`，但**不调用 `register_alias_matchers`**。
- 用户在 Web UI 改 alias 后，新 alias 不会立即可用，需重启。函数本身无 bug，但调用方（前端 / API 文档）需说明"重启生效"。属于产品体验/接口规约层，非基础设施缺陷。

#### C.1.4 `_ensure_runtime_cache_loaded` 与 `refresh_runtime_cache` 的双重锁/双重加载 — **NEW INFO**

- 位置：`command_config.py:468-472`
- `_ensure_runtime_cache_loaded` 在锁外检查 `ready`，锁外调用 `refresh_runtime_cache`。如果两个线程同时通过 ready 检查，会 double-load DB。
- `refresh_runtime_cache` 自身只在写入 cache 时持锁，重复加载只是浪费一次 DB 查询，结果幂等。可接受。

#### C.1.5 `_to_runtime_state` 不修剪过长 description / usage — **NEW INFO**

- 位置：`command_config.py:420-448`
- `description = row.description`、`usage = _normalize_usage_text(row.usage)`、`category = str(row.category or "")` 都直接来自 DB。
- 如果 DB 列被外部 SQL 写入超长字符串，缓存到内存的 RuntimeCommandState 也是超长字符串。这是 DB schema 约束层的责任，记录但非缺陷。

### C.2 `bot.py`

#### C.2.1 `_has_onebot_ws_urls` JSON parse 与 string fallback 都为空时 — **NEW INFO**

- 位置：`bot.py:54-74`
- `text="[]"` → `json.loads` 解析成 `[]` → `any(...)` 为 False → 返回 False。OK。
- `text="[abc"` → `json.JSONDecodeError` → `return bool(text)` → True。**这是异常 fallback**：非法 JSON 被当成"已配置"，会触发 OneBot adapter 注册并立即报 URL 解析错误。
- 表面上看是 fail-loud，但用户 typo `[]` 之类时会得到无意义的 adapter 报错。次要 UX 问题。

#### C.2.2 `ensure_env_file` write 失败后流程仍继续 — **NEW INFO**

- 位置：`bot.py:77-89`
- 写失败时只 logger.error + return，不抛异常。下游 `nonebot.init(_env_file=str(ENV_PATH))` 收到不存在的路径，NoneBot 会用空配置启动 → 所有 OWNER_ID / GROUP_ID 默认空 → fail-closed（消息全过滤）。
- 这是 U-1.1 修复的预期行为（fail-closed 而非 crash），**OK，已经是 Round 7 修复的策略意图**。

### C.3 `message_parser.py`（Round 7 / 8 未改）

#### C.3.1 `_extract_args_text` regex 处理无空格附着字符 — **NEW INFO**

- 位置：`message_parser.py:47-52`
- `re.match(rf"^/?{cmd}(?:\s+|$)", text)`：要求命令后必须是空格或字符串结束。若用户输入 `/bagextra`，正则不匹配 → 返回 None。**符合预期**（避免 `/bag` 误匹配 `/bagextra`）。

#### C.3.2 `resolve_user_id_arg_with_fallback` name 大小写不敏感匹配但用户名可能含 emoji / 多语言 — **NEW INFO**

- 位置：`message_parser.py:128-139`
- `func.lower(User.name) == token.lower()`：SQLite 的 LOWER 默认只处理 ASCII。中文 / emoji 用户名匹配可能不一致。
- 但项目内 user.name 来源 TShock 服务器（玩家名），ASCII 范围内绝大多数情况 OK。记录非缺陷。

#### C.3.3 `_segments_to_plain_text` at 段无 qq 时 silent drop — **NEW INFO**

- 位置：`message_parser.py:35-40`
- `qq` 为空或 `"all"` 时分支直接 continue，不会出现在解析结果。功能正确（@全体不应被识别为参数），无问题。

### C.4 `text_utils.py`（Round 7 / 8 未改）

#### C.4.1 `safe_at_segment` 对 user_id 含全角数字 — **NEW INFO**

- 位置：`text_utils.py:87-103`
- `int("１２３")` 在 Python 中**会成功**（Python int 接受 Unicode 数字字符），所以全角数字会被转成 int。OBV11 是否接受这种值是 protocol 层的事，但 helper 层不会 ValueError。记录非缺陷。

#### C.4.2 `at_prefix` 内容是 OBV11 Message 时拼接顺序 — **NEW INFO**

- 位置：`text_utils.py:122-137`
- `at_seg + sep + content`：如果 `content` 是 `Message`/`MessageSegment`，OBV11 的 `+` 重载会处理；如果是 str，会被自动包成 text segment。OK。

### C.5 `time_utils.py`（Round 7 / 8 未改）

#### C.5.1 `seconds_until_next_beijing_midnight` DST 边界处理 — **NEW INFO**

- 位置：`time_utils.py:67-74`
- `Asia/Shanghai` 自 1991 年后**无 DST**，函数无 DST 风险。
- `tzdata` 缺失时（极端 Alpine slim 镜像）`ZoneInfo("Asia/Shanghai")` 会 raise；但 `time_utils.py:7` 是 module-level 调用，import 失败会直接 abort 进程 → fail-fast，OK。

#### C.5.2 `db_now_utc_naive` 与 SQLAlchemy default 一致性 — **NEW INFO**

- 调用方多次复用此函数（`command_config.py:650, 723, 809`，`stats.py:34`）保持 DB 时间一致。OK。

### C.6 `progression.py`（Round 7 / 8 未改）

#### C.6.1 静态常量表，无新发现 — **PASS**

- 21 个 progression tier，无副作用，无状态。
- `parse_tier` 接受多种别名 (`无` / `None` / 中文 / 英文 key)，逻辑清晰。

### C.7 `stats.py`（Round 7 / 8 未改）

#### C.7.1 `increment_stat` 使用 sqlite UPSERT — **PASS**

- 位置：`stats.py:25-50`
- `insert(...).on_conflict_do_update(...)` SQLite 原生 ON CONFLICT，单条 SQL 原子操作。
- `engine.begin()` 自动事务边界，OK。

#### C.7.2 `get_dashboard_metrics` 多次 count 在一个 session 内 — **PASS**

- 位置：`stats.py:72-138`
- 同 session 串行 query，无事务隔离问题。
- `total_coins = func.sum(User.coins).scalar()`：如果 `coins` 列允许 NULL，`or 0` 已 fallback。`User.coins` 在 schema 中是 NOT NULL DEFAULT 0（查 db.py），所以 fallback 多余但无害。

#### C.7.3 `get_dashboard_metrics` 多次 `int(... or 0)` 防御 `None` — **PASS**

- `func.count` 一定返回数字（COUNT 永不 NULL），`or 0` 多余但无害；`func.sum` 可能 NULL（空表），`or 0` 必须保留。OK。

#### C.7.4 `connected_bot_ids` Bot.id 排序为字符串字典序 — **NEW INFO**

- 位置：`stats.py:112`
- `sorted(str(bot_id) for bot_id in get_bots().keys())`：QQ 号是数字字符串，字典序与数字序在等长情况下一致；不等长时可能 `"100" < "99"`（"1" < "9"）。Dashboard 显示用，可接受。

### C.8 `data_dir.py`（Round 7 / 8 未改）

#### C.8.1 `_resolve_data_dir` import-time 副作用 — **NEW INFO**

- 位置：`data_dir.py:25-32`
- `DATA_DIR: Path = _resolve_data_dir()` 是 module-level 表达式，import 时执行 `mkdir(parents=True, exist_ok=True)`。
- 若环境变量指向无权限路径，import 会 raise `PermissionError` → bot.py 立即崩溃。fail-fast，记录非缺陷。

---

## 结论

### Part A（Round 8 复审）

- **全部 PASS**：M-3 part 2 / M-4 / R8-U-B-2 / self-fix（注册顺序）。
- 关键验证：NoneBot `_lifespan.py:79-81` 实际使用 `reversed(_shutdown_funcs)` = LIFO，self-fix 正确。
- R8-U-B-2 实际是 60s 窗口节流（基于 `time.monotonic()` 时间戳），不是任务描述担心的"永不重置布尔 flag"，**优于** R8 的描述。
- M-4 实际实现拆分为两个 set（`self_conflict_names` 固定 + `seen_in_batch` 增量），**不存在任务描述担心的"alias in cleaned 永真"误报**。

### Part B（Round 7 保留性）

- 7 项 Round 7 修复 **全部保留**。

### Part C（全量再扫）

- **NEW LOW**：
  - C.1.1 `refresh_runtime_cache` 单条 row 损坏可能导致全表 cache 失败 → 退化全表 fallback 行为，建议 per-row try/except。
  - C.1.2 `register_alias_matchers` 缺幂等保护（当前只在启动调用一次，未触发，但代码层面缺防御）。
- **NEW INFO（非缺陷，记录）**：
  - C.1.3 `update_command_aliases` 不立即生效（需重启 / 调用 register_alias_matchers）。
  - C.1.4 `_ensure_runtime_cache_loaded` 锁外检查，可能 double-load，结果幂等可接受。
  - C.2.1 `_has_onebot_ws_urls` 非法 JSON 时退化为 `bool(text)`，UX 次要问题。
  - C.3 / C.4 / C.5 / C.6 / C.7 / C.8 内大部分为信息性记录，无修复必要。

无 HIGH / MEDIUM 级新发现。
