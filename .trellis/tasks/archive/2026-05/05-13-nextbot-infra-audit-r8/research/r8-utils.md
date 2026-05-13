# R8 Utils / Bot / Command Config 桶审计

- **Query**: Round 7 修复复审 + 全量再扫（bot.py / command_config.py / message_parser.py / text_utils.py / time_utils.py / progression.py / stats.py / data_dir.py）
- **Scope**: internal（含 NoneBot SDK 二次确认）
- **Date**: 2026-05-13

---

## Part A: Round 7 修复复审

### A-1. MH-1 (U-1.2) bot.py adapter guard — **PASS（带 caveat）**

修复位置：`/Users/arispex/CascadeProjects/nextbot/bot.py:108-147`

修复后行为：
- `_filter_allowed_messages(bot: Bot, event: Event)` 签名加 `bot` 形参，可拿 adapter。
- 132-140 新增 console 守卫：`adapter.get_name() == "Console" and event.get_user_id() == "user"` 才绕过 owner/group allowlist。

复审证据：
- NoneBot SDK 真实值已对照确认：`.venv/.../adapters/console/adapter.py:38-39` 返回 `"Console"`，`.venv/.../adapters/onebot/v11/adapter.py:80-82` 返回 `"OneBot V11"`。常量字符串匹配可靠。
- 137 行 `except Exception` 已 `noqa: BLE001`，仅吞 `Exception`，不影响 `CancelledError` / `KeyboardInterrupt`。
- 守卫只在 message_type 既非 `"private"` 也非 `"group"`（含空字符串 / `"system"`）的兜底路径生效；当前 bot.py:99 ConsoleAdapter 行被注释，**守卫等价 dead-code**，但未来若取消注释立即生效；这是预期 forward-compat 行为。

判定：**PASS**。原 finding 根因（Console adapter 推 user_id="user" 绕过 allowlist）被根除，没有引入回归。

---

### A-2. MH-2 (U-2.2) update_command_aliases 冲突集合加 command_key — **PASS（但遗漏 alias=self.command_key 新场景，见 B-1）**

修复位置：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:813-826`

修复后行为：
- `for r in all_rows: conflict_names.add(r.command_key)`（818 行）已落实。
- 配合原有 `add(r.display_name)` + 现有 aliases 集合，新建 alias 现在能避免与其他命令的 command_key / display_name / aliases 冲突。

复审证据：
- 809-812 查询：`CommandConfig.command_key != normalized_key AND is_registered=True` —— 排除当前 row 自己，所以 `conflict_names` 集合**不含当前 row 的 command_key / display_name / aliases**。
- 与 `register_alias_matchers`（851-870）配合：alias_matcher = `on_command(alias)`，与 plugin 启动期注册的 `on_command(command_key)` matcher 同名时，NoneBot 不会去重 —— 两个独立 matcher。

判定：**PASS**（原 finding "alias 撞其他 plugin 的 command_key" 已闭合）。但暴露新洞 → 见 B-1。

---

### A-3. U-1.1 ensure_env_file fail-soft — **PASS**

修复位置：`/Users/arispex/CascadeProjects/nextbot/bot.py:77-89`

修复后行为：
- `try: ENV_PATH.write_text(...)` → `except OSError as exc: logger.error(...) return`
- 失败后不抛，下游 `nonebot.init(_env_file=str(ENV_PATH))` 用不存在路径继续。

复审证据：
- NoneBot SDK `.venv/.../nonebot/config.py:131-134` `_read_env_files` 内：`if env_path.is_file(): self._read_env_file(env_path)`。**显式 file-existence guard，文件不存在静默跳过**，不抛异常。
- 失败结果：进程继续启动，配置全部从环境变量取（或 pydantic 默认值），运维通过 logger.error 看到根因。符合 fail-soft 设计意图。

判定：**PASS**。无 NoneBot 侧 raise 风险。

---

### A-4. U-2.3 wrapper 包 _check_user_banned fail-soft — **PASS（但 trade-off 未在 docstring 显式标记）**

修复位置：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:980-989`

修复后行为：
- `try: ban_msg = _check_user_banned(...)` → `except Exception: logger.exception(...); ban_msg = ""`
- DB OperationalError（busy_timeout 超时）/ connection lost 时，命令照常执行。

复审证据：
- 行为正确：与 968-970 的 `increment_command_execute_total` fail-soft 对齐，DB 出问题不阻塞业务。
- **副作用**：被 ban 的用户在 DB 故障期间可执行命令。这是接受的 trade-off（fail-soft 优先于命令拒绝），但 980 行 docstring 仅在代码注释里说明，调用方 / admin 不易感知。

判定：**PASS**。但建议在 README / spec 标注："封禁系统在 DB 故障时降级为放行模式"，便于安全审计。**不视为新 finding**。

---

### A-5. U-2.4 _get_runtime_state DB 异常 logger.exception — **PASS（但留下 N-2 高频重试问题，见 B-2）**

修复位置：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:469-477`

修复后行为：
- `try: _ensure_runtime_cache_loaded() except Exception: logger.exception(...)`
- 失败时 fallback 到 `_registry` 的 in-memory `RegisteredCommand`，使用 `default_enabled`。

复审证据：
- fallback 行为安全：DB 不可用 → 命令走静态 default_enabled，没有"被 ban 但放行"的二次风险。
- **但** `_ensure_runtime_cache_loaded`（462-466）在 `refresh_runtime_cache` 抛出后 `_runtime_cache_ready` 保持 False，下次调用又触发 `refresh_runtime_cache()` → 又 raise → 又 logger.exception。**详见 B-2 New finding**。

判定：**PASS**（原 finding 修了）。但延伸出 B-2。

---

### A-6. H-3 part 2 (U-2.12) command_control import-time 形参校验 — **PASS**

修复位置：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:933-960`

修复后行为：
- 938-942：`if "bot" not in param_names or "event" not in param_names: raise RuntimeError(...)`，模块 import 阶段直接失败。
- 943-960：`typing.get_type_hints(func, include_extras=True)` + `parameter.replace(annotation=...)` 重建 signature，保留 `Annotated[Dict, _STATE_FLAG]` 等 NoneBot 依赖注入 metadata。

复审证据：
- grep `@command_control` 全项目 87 处（87 个 handler），全部 pass —— `nonebot.load_plugins("nextbot/plugins")` 启动期检查保证零绕过。
- NoneBot 依赖注入路径：`.venv/.../nonebot/dependencies/utils.py:20-34 get_typed_signature` 调用 `inspect.signature(call)`，对 wrapper 通过 `setattr(wrapper, "__signature__", resolved_signature)` 暴露内层签名 —— Python `inspect.signature` 标准行为遵循 `__signature__`。**注入链路 OK**。
- `include_extras=True` 与 NoneBot 的 `T_State = Annotated[Dict, _STATE_FLAG]` 检测兼容（NoneBot 的 `get_typed_annotation`:37-53 直接读 `param.annotation`，因 wrapper signature 已是 Annotated 原值，无脱壳）。

判定：**PASS**。

---

### A-7. I-1.3 hook (bot.py shutdown) — **PASS（但有 micro-quibble）**

修复位置：`/Users/arispex/CascadeProjects/nextbot/bot.py:168-172`

修复后行为：
- `@driver.on_shutdown async def _close_shared_http_client(): from nextbot.tshock_api import close_shared_client; await close_shared_client()`
- `tshock_api.close_shared_client`（tshock_api.py:94-99）`await _shared_client.aclose()` 后置 None。

复审证据：
- NoneBot 生命周期：`on_shutdown` 在 lifespan 关闭前回调，event loop 仍正常运行 —— `aclose` 安全。
- httpx `AsyncClient.aclose` 在 loop 即将关闭场景下不抛（httpx 内部用 anyio cancellation 友好）。
- **Micro-quibble（非 finding，info-only）**：171 行 `from nextbot.tshock_api import close_shared_client` 延迟 import 在此场景无必要 —— bot.py 启动期已加载 `nextbot.command_config / nextbot.access_control` 等，`tshock_api` 大概率已被某 plugin 间接 import。改 top-level import 不引入循环（tshock_api 不 import bot.py），但维持现状也对，**不报 finding**。

判定：**PASS**。

---

## Part B: 全量再扫新发现

### B-1（NEW / **Medium**）alias 与 self.command_key 同名导致 register_alias_matchers 双重 matcher

**文件 / 行号**：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:809-833` + `851-870`

**根因**：
- `update_command_aliases` 查询 conflict_names 时 809 行加了 `CommandConfig.command_key != normalized_key` 排除当前 row 自己 —— 因此 conflict_names 集合**永远不含当前 row 自己的 command_key、display_name、现有 aliases**。
- admin 可设置 `aliases=["bag"]` for command_key="bag"，校验通过（"bag" 不在 conflict_names）。
- `register_alias_matchers`:864-866 内 `alias_matcher = on_command("bag")` 与 plugin 启动期通过装饰器 `@bag_matcher = on_command("bag")` 注册的主 matcher 同名。
- NoneBot `on_command()` 每次调用返回**新 matcher 对象**，无去重（matcher_id 不同）。
- 结果：用户发 `/bag` 时主 matcher 和 alias matcher 都触发，wrapper 执行 2 次：
  - `increment_command_execute_total()` 双计数（dashboard 数据膨胀）
  - `_check_user_banned()` 双查询（DB QPS 翻倍）
  - 业务 handler `func(*args, **kwargs)` 双执行（金币、签到、抽奖等幂等性差的命令会双扣双发）

**触发条件**：admin 在 webui 手动给某命令配 alias = 该命令自身的 command_key / display_name。仅当 admin 误操作触发，但缺失校验。

**修复方向（仅描述，不实施）**：在 828-833 的循环里追加 `if alias in {normalized_key, row.display_name, *cleaned}` 自查。

**影响**：
- Severity: **Medium**
- 触发概率: Low（admin 手动误操作）
- 影响：双消费金币 / 双扣抽奖次数 / 双发消息（用户感知）
- 修复成本：~3 行

---

### B-2（NEW / **Low**）_ensure_runtime_cache_loaded 在 DB 故障时**每条消息**都重试 + logger.exception

**文件 / 行号**：`/Users/arispex/CascadeProjects/nextbot/nextbot/command_config.py:445-477`

**根因**：
- `refresh_runtime_cache`:445-459 抛出异常时，`_runtime_cache_ready = True`（458 行）**不会**被赋值（异常发生在 448 行 query 阶段）。
- `_ensure_runtime_cache_loaded`:462-466 每次都检查 `_runtime_cache_ready`；DB 故障时永远 False，**每条消息都尝试重新 query**。
- `_get_runtime_state`:470-477 在 wrapper 内被调用，每条命令消息都触发一次。DB 故障期间每条消息都打一条**完整 stack trace** 日志。
- 后果：
  - 日志风暴（高峰期可能数千条 / 秒，日志盘 IO 打满）
  - DB 故障期间持续重连尝试（增加恢复期负担）
  - 已经有 fallback 到 `_registry` 的 in-memory 路径，**fail-soft 本身正确**，问题在 retry 无 throttle。

**修复方向（仅描述）**：
- 选项 A：把 `_runtime_cache_ready = True` 移到 try 内即使部分失败也认为已加载（最简单）。
- 选项 B：用 `_last_load_attempt_at` 时间戳 + 60s cooldown 节流。
- 选项 C：异常时 `logger.warning`（无 stack）首次记录、后续静默 N 分钟。

**影响**：
- Severity: **Low**（DB 故障本身是 outage，此问题放大但不构成新 outage）
- 触发概率: Medium（DB 故障窗口期）
- 修复成本：3-5 行

---

### B-3（NEW / **Low**）resolve_user_id_arg_with_fallback 每次解析名字都开 BEGIN IMMEDIATE 写锁会话

**文件 / 行号**：`/Users/arispex/CascadeProjects/nextbot/nextbot/message_parser.py:129-139`

**根因**：
- `get_session()` 在 nextbot db 层基于 `connect()` event listener `BEGIN IMMEDIATE`（db.py:392-423）—— 每个 session 一开始就持写锁。
- 即使只读 query（131-138 `func.lower(User.name) == token.lower()`）也在写事务下执行，与并发命令竞争 5s busy_timeout 窗口。
- 影响放大：`resolve_user_id_arg_with_fallback` 被 ban / 红包 / 转账等命令 hot-path 调用，参数解析阶段就锁竞争。

**复审 caveat**：
- 这是 db.py 全局 BEGIN IMMEDIATE 策略的副作用，**不是 message_parser 自身的问题**。Round 7 D-1 桶已确认 BEGIN IMMEDIATE 是 trade-off（写串行 vs 读读并行）。
- 但 message_parser 一个**纯读**用例承担了写锁开销，是潜在优化点。

**修复方向**：声明独立 read-only session（不走 BEGIN IMMEDIATE，比如直接 `engine.connect()` 不 begin）。改动跨 db.py + message_parser，超出 Utils 桶范围。

**影响**：
- Severity: **Low**
- 触发概率: Medium（红包高峰期 / 群活跃时段）
- **不建议本轮修**（设计性 trade-off，归 db.py 桶讨论）

---

### B-4（INFO / **Info**）stats.py get_dashboard_metrics 完全 fail-hard

**文件 / 行号**：`/Users/arispex/CascadeProjects/nextbot/nextbot/stats.py:72-137`

**根因**：
- 9 个独立 query（server_count / user_count / group_count / command_total / command_enabled_count / signed_today_count / total_coins / command_total_row + bots fetch）任一抛 → 整个函数抛。
- 但调用方 `server/routes/webui_dashboard.py:14-23` 已 `try/except Exception: api_error(500)`，影响**仅限 dashboard 端点 500**，不会影响其他 webui / bot 业务。

**判定**：原报告中"dashboard 显示故障导致整个 webui 不可用"的担忧**不成立**（caller 已隔离）。但函数内部仍是 all-or-nothing，未来 schema 演进时风险叠加 —— info-only 备注。

**修复方向**：可考虑按 query 单独 fallback（如 `signed_today_count` 失败时回退到 None / "—"），让 admin 看到部分指标。**非本轮必修**。

---

### B-5（INFO）time_utils.py / progression.py / data_dir.py / text_utils.py 再扫干净

- `time_utils.py`：纯函数，输入校验充足。`seconds_until_next_beijing_midnight` 用 `max(..., 1.0)` 保 floor 已合理。无新发现。
- `progression.py`：纯常量表 + parse_tier 容忍 None/"无要求"/"None"/"NONE"。无新发现。
- `data_dir.py`：32 行，`Path(raw).expanduser().resolve() → mkdir(parents=True, exist_ok=True)`，无副作用。无新发现。
- `text_utils.py`：`safe_at_segment` / `safe_at_segment_or_empty` / `at_prefix` 均按 PC-4.1 防御。OBV11 延迟 import 已落实。无新发现。

---

### B-6（INFO）command_config.py 周边再扫

- `sync_registered_commands_to_db`:700-767 commit 失败让启动失败（Round 7 判 OK）：复审仍 OK，因为：
  - SQLAlchemy session 抛后 finally 中 `session.close()` 释放资源。
  - **没有 explicit rollback**：依赖 session.close() 隐式 rollback；SQLite + autocommit=False 下 session.close() 会 rollback 未 commit 的事务。对，是 OK 的。
  - 单一 commit（763 行），无部分成功 / 部分失败的中间状态 —— **原担忧不成立**。
- `_merge_param_values`:387-401 类型不匹配 silent fallback 到 default：admin 在 webui 看到 default 与"上次保存值"不一致 —— 是 schema-driven trade-off，Round 7 已接受，不报。
- `update_command_config`:602-697 errors 收集 + `if errors: raise CommandConfigValidationError`：错误聚合正确。

---

## 结论

### Round 7 修复复审：**7/7 PASS**

| ID | 状态 | 备注 |
|---|---|---|
| MH-1 (U-1.2) | PASS | adapter.get_name() 字面值已 SDK 二次确认 |
| MH-2 (U-2.2) | PASS | 但遗漏 alias=self.command_key（→ B-1） |
| U-1.1 | PASS | NoneBot env_file 缺失静默跳过，无 raise 风险 |
| U-2.3 | PASS | trade-off 接受，建议 spec 备注 |
| U-2.4 | PASS | 但 retry-no-throttle 暴露 B-2 |
| U-2.12 (H-3 p2) | PASS | NoneBot DI 链路 + Annotated 兼容已验证 |
| I-1.3 hook | PASS | shutdown 时机正确，aclose 安全 |

### 新发现汇总

| ID | Severity | 文件 | 简述 |
|---|---|---|---|
| B-1 | **Medium** | command_config.py:809-833 + 851-870 | alias 与自己的 command_key 同名 → register_alias_matchers 创建双重 matcher → wrapper 双执行 |
| B-2 | Low | command_config.py:445-477 | DB 故障时每条消息触发一次 refresh_runtime_cache + logger.exception，无 throttle |
| B-3 | Low | message_parser.py:129-139 | 名字解析走 BEGIN IMMEDIATE 写锁（db.py 全局策略副作用，不在本桶修） |
| B-4 | Info | stats.py:72-137 | dashboard fail-hard，但 caller 已隔离，不构成新 outage |
| B-5/B-6 | Info | time_utils / progression / data_dir / text_utils / command_config 周边 | 再扫无新发现 |

### 建议优先级

- 第 1 梯队（建议修）：**B-1**（Medium，3 行）
- 第 2 梯队（可选）：**B-2**（Low，3-5 行，提升 outage 期日志质量）
- 第 3 梯队（设计层讨论）：**B-3 / B-4**

### 关键判断

- Round 7 Utils 桶 7 条修复**全部 PASS**，没有引入回归。
- 仅遗留 **1 个 Medium 新发现（B-1）**，是 MH-2 修复时未覆盖的边角 case，符合"修周边代码暴露的新洞"预期。
- 其他文件（time_utils / progression / data_dir / text_utils）干净，确认设计良好。
