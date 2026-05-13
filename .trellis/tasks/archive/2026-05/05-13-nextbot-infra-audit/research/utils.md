# Research: Utils / Misc / Entry Bucket Audit (Round 7)

- **Query**: 审计 nextbot 基础设施层的 8 个 utils / misc / entry 文件，提炼独立、未被前几轮覆盖的新发现
- **Scope**: internal
- **Date**: 2026-05-13

## 审计文件清单

| 文件 | 行数 |
|---|---|
| `bot.py` | 155 |
| `nextbot/command_config.py` | 984 |
| `nextbot/message_parser.py` | 156 |
| `nextbot/text_utils.py` | 137 |
| `nextbot/time_utils.py` | 74 |
| `nextbot/progression.py` | 56 |
| `nextbot/stats.py` | 137 |
| `nextbot/data_dir.py` | 32 |

每条 finding 都给出精确文件 + 行号，可用 `Read` 工具复核。

---

## U-1 bot.py（入口）

### U-1.1 `ensure_env_file()` 写盘失败无兜底，且 loguru 不支持 `%s` 插值

`bot.py:77-85`：

```python
def ensure_env_file() -> None:
    if ENV_PATH.exists():
        return

    ENV_PATH.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
    logger.warning(".env 不存在，已创建默认 .env 文件：%s", ENV_PATH)


ensure_env_file()
```

两点：

1. `write_text` 没有 try/except。若 `DATA_DIR` 权限错误 / 磁盘满 / Docker 把 `/app/data` mount 成 RO（容器化部署常见），`PermissionError` / `OSError` 在 import 阶段裸抛，进程崩在 `bot.py:85` 这一行，traceback 显示 `ensure_env_file` 而非 `init_db`，排障会先怀疑 .env 模板问题。
2. `logger.warning(".env 不存在，已创建默认 .env 文件：%s", ENV_PATH)` 使用 `%s` + 位置参数。NoneBot 的 `nonebot.log.logger` 是 loguru 实例，loguru 的 `logger.<level>(msg, *args, **kwargs)` 只支持 `{}`-style `.format()` 占位（参考 loguru 文档），传入的额外位置参数被当成 `extra` 字段或忽略，输出会保留字面量 `%s` 后跟空格 + path。同一文件下方 `bot.py:95, 97, 112, 121, 130, 141, 143, 145, 148` 全部使用 f-string，独这一处用 `%s`，明显是漏改/笔误。

### U-1.2 `_filter_allowed_messages` 对 `user_id == "user"` 的硬编码绕过未做适配器 guard

`bot.py:125-126`：

```python
    if event.get_user_id() == "user":
        return
```

这是 NoneBot console adapter 的固定虚拟用户 id。但 bypass 没有伴随 `event.adapter` / `event.bot.type` 校验。若某第三方 adapter（V11-shim、Telegram bridge、自研 adapter）push 出 `user_id="user"`（字面字符串），会绕过 owner_id / group_id allowlist，直接落到下游 plugin handler。属于身份模拟漏洞。建议改为 `event.bot.adapter.get_name() == "Console"` 之类的强 guard。

### U-1.3 启动顺序中 `register_alias_matchers()` 在 plugin 加载之后、driver 启动期间动态注册 NoneBot Matcher

`bot.py:136-155`：

```python
@driver.on_startup
async def _init_database() -> None:
    ...
    init_db()
    logger.info("数据库初始化 / 表结构检查完成")

    sync_registered_commands_to_db()
    logger.info("命令配置同步完成")
    from nextbot.command_config import register_alias_matchers
    register_alias_matchers()
    start_web_server()

nonebot.load_plugins("nextbot/plugins")

nonebot.run()
```

`register_alias_matchers()`（`command_config.py:843-865`）在启动期间对每个 alias 调 `on_command(alias)`。NoneBot2 的 plugin / matcher 注册 contract 是：所有 Matcher 应在 `nonebot.load_plugins(...)` 期间（plugin 模块 import 时）静态完成，启动后通过 `Matcher._default_state` 等内部状态保证一致性。`@driver.on_startup` 阶段动态注册的 matcher，虽然 NoneBot 实现上仍会被加入全局 `_default_state_modifier` 字典，但属于 NoneBot 文档不保证的用法。NoneBot 升级到 3.x 或核心 matcher 注册机制重构时存在破坏风险。

### U-1.4 缺少 `@driver.on_shutdown` hook，DB engine / web server / 浏览器进程无 graceful shutdown

全仓搜索 `on_shutdown`：

- `bot.py`：0 处
- `server/screenshot.py:253`：`get_driver().on_shutdown(_session.close)`（只关闭 playwright session）

`nextbot/db.py:375-416` 维护一个进程级 SQLAlchemy `_engine` 连接池（SQLite，`check_same_thread=False`）；`server/web_server.py` 启动 uvicorn worker；外部 HTTP client 散在 plugins 中（`nextbot/tshock_api.py:77` 用 `async with httpx.AsyncClient(...)`，短期 OK）。

- SQLite + SQLAlchemy：进程退出时 finalizer 自动 dispose，但若有未 commit 的写事务（U-7 stats 章节有讨论），ProcessTermination 会丢；
- Uvicorn worker：依赖 NoneBot driver 关闭机制自行 cleanup；
- 用户 SIGTERM（K8s/Docker stop） 后没有 explicit hook flush loguru buffered logs，sink 配 enqueue=True 时 buffer 中的最后几条日志会丢。

属于"非阻塞性、但可观测性损失"风险。

### U-1.5 `_filter_allowed_messages` 对未知 `message_type` 默认 deny + 容易把 console 调试静默吞掉

`bot.py:100-133`：函数已经在开头用 `if event.get_type() != "message": return` 跳过 notice/request/meta。但 `message_type = getattr(event, "message_type", "")` 在 console adapter 下可能为空（console adapter 的 MessageEvent 没有 `message_type` 属性）。流程走到最后的 `raise IgnoredException("message blocked by access allowlist")`。同时 U-1.2 的 `user_id == "user"` bypass 保证 console adapter 的本地消息能通过，但若 console adapter 在测试中 push 出 `user_id != "user"`（用户改了 adapter 配置），整个 console 链路被静默 deny。审计：deny-by-default 安全模型正确，但 console 调试体验依赖 U-1.2 的硬编码 bypass。

### U-1.6 `_has_onebot_ws_urls()` 在 `nonebot.init` 之前调用会异常

`bot.py:54-74`：函数访问 `nonebot.get_driver().config`，必须在 `nonebot.init()` 之后调用。当前代码顺序（`bot.py:85` `ensure_env_file()` → `bot.py:89` `nonebot.init()` → `bot.py:93` `_has_onebot_ws_urls()`）符合，OK。但函数实现只 try/except 了 `getattr` 返回 None 的分支，没有对 `nonebot.get_driver()` 本身异常做兜底。未来若重排（例如把 adapter 注册提到 init 之前），此处会以 `ValueError: get_driver failed before nonebot.init` 形式裸抛。属于"对调用顺序的隐含依赖"。

---

## U-2 command_config.py（核心 registry 模块）

### U-2.1 `_registry` 重复注册 RuntimeError 是 plugin-loader 致命错；错误消息缺少冲突双方 module_path

`command_config.py:919-923`：

```python
        with _registry_lock:
            exists = _registry.get(normalized_key)
            if exists is not None and exists != registered:
                raise RuntimeError(f"duplicate command_key detected: {normalized_key}")
            _registry[normalized_key] = registered
```

`RegisteredCommand` 是 `@dataclass(frozen=True)`（line 29），`__eq__` 按字段全相等。

- 真正 duplicate（两个 plugin 用同 command_key）：抛 `RuntimeError`，让 `nonebot.load_plugins("nextbot/plugins")` 中断，后续 plugin 全部 skip。错误消息只有 `command_key`，没有冲突双方 `module_path`，plugin 作者无法直接定位。
- 假 duplicate（同 module 被 import 两次、且字段全等）：silently overwrite，无审计日志。

后者实际不太可能发生（NoneBot 不会重复 import 同一 plugin），但若开启 dev hot reload 或测试 fixture 重置 + 重新 import，会发生 eq 通过 → silent overwrite 路径。审计：错误消息信息量不足；overwrite 无日志。

### U-2.2 `register_alias_matchers()` 不检查 alias 是否撞 primary command_key

`command_config.py:843-865`：

```python
def register_alias_matchers() -> None:
    _ensure_runtime_cache_loaded()
    with _registry_lock:
        items = list(_runtime_cache.values())

    count = 0
    for state in items:
        if not state.is_registered or not state.aliases:
            continue
        original = _original_handlers.get(state.command_key)
        if original is None:
            continue

        for alias in state.aliases:
            alias_matcher = on_command(alias)
            alias_matcher.handle()(original)
            count += 1
            logger.info(
                f"注册命令别名：alias={alias} command_key={state.command_key}"
            )
```

`update_command_aliases()`（`command_config.py:805-825`）只比较 alias 与"其他命令的 display_name + aliases_json"，**没有比较与任何命令的 command_key**：

```python
        all_rows = session.query(CommandConfig).filter(
            CommandConfig.command_key != normalized_key,
            CommandConfig.is_registered.is_(True),
        ).all()
        conflict_names: set[str] = set()
        for r in all_rows:
            conflict_names.add(r.display_name)
            try:
                existing_aliases = json.loads(r.aliases_json or "[]")
                if isinstance(existing_aliases, list):
                    for a in existing_aliases:
                        conflict_names.add(str(a).strip())
            except (json.JSONDecodeError, TypeError):
                pass
```

若 plugin A 注册 `command_key="bag"`，admin 给 plugin B 配 alias `"bag"`，admin 操作通过校验（不撞 display_name / aliases_json），DB 写入成功；启动期 `register_alias_matchers()` 用 `on_command("bag")` 注册 alias matcher。NoneBot 收到 `/bag` 时同时触发 plugin A 的 primary matcher 和 plugin B 的 alias matcher，两条命令各自处理一遍消息，可能产生重复回复 + 权限混乱。修复：`update_command_aliases` 的 conflict 集合应加入所有 `r.command_key` 和 `r.display_name`。

### U-2.3 `wrapper` 内 `_check_user_banned` 异常未被捕获，DB 锁超时会把命令异常冒泡

`command_config.py:945-970`：

```python
async def wrapper(*args, **kwargs):
    state = _get_runtime_state(normalized_key)
    context_token = _current_command_context.set(state)
    try:
        try:
            increment_command_execute_total()
        except Exception:
            logger.exception(f"命令计数写入失败：command_key={normalized_key}")
        if not state.enabled:
            ...
            return None

        bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
        if bot is not None and event is not None:
            ban_msg = _check_user_banned(event.get_user_id())   # <-- 没有 try/except
            if ban_msg:
                at = safe_at_segment_or_empty(event.get_user_id())
                await bot.send(event, at + "\n" + ban_msg)
                return None

        return await func(*args, **kwargs)
```

- `increment_command_execute_total()` 失败用 `logger.exception` 不冒泡，OK；
- `_check_user_banned()`（`command_config.py:366-377`）做 `session.query(User).filter(User.user_id == user_id).first()`，配合 `db.py:403-413` 的 `BEGIN IMMEDIATE` 让 SELECT 也持写锁，命令并发高峰时 SQLite busy_timeout=5000ms 后会抛 `OperationalError: database is locked`。`wrapper` 没有捕获 → 异常冒泡到 NoneBot 框架 → 命令执行栈 traceback。属于 fail-hard 而非 fail-soft，与 increment 计数的 fail-soft 策略不对称。

### U-2.4 `_get_runtime_state` 把 cache 加载异常完全吞掉，造成 silent degradation

`command_config.py:469-495`：

```python
def _get_runtime_state(command_key: str) -> RuntimeCommandState:
    try:
        _ensure_runtime_cache_loaded()
    except Exception:
        pass
    with _registry_lock:
        runtime = _runtime_cache.get(command_key)
    if runtime is not None:
        return runtime

    registered = _get_registered_command(command_key)
    if registered is None:
        return RuntimeCommandState(
            command_key=command_key,
            display_name=command_key,
            description="",
            usage="",
            module_path="",
            handler_name="",
            permission="",
            enabled=True,           # <-- fallback 默认 enabled=True
            param_schema={},
            param_values={},
            aliases=[],
            category="",
            is_registered=False,
        )
```

DB 读失败时 `pass`，无任何日志；fall back 到 `_get_registered_command` 走内存 `_registry`，最终 `state.enabled=True`（默认）。结果：DB 不可用 → 后台管理界面对命令的 disable 操作完全失效（用户输 `/cmd` 仍执行），且无任何告警。属于 silent failure。建议至少 `logger.warning` 一次（限流防日志爆炸）。

### U-2.5 `_to_runtime_state` 对损坏 `param_schema_json` 静默 fallback 到空 schema

`command_config.py:414-442`：

```python
def _to_runtime_state(row: CommandConfig) -> RuntimeCommandState:
    schema = _normalize_param_schema(_parse_json_object(row.param_schema_json))
    values = _merge_param_values(
        schema=schema,
        old_values=_parse_json_object(row.param_values_json),
    )
```

`_parse_json_object`（`command_config.py:88-95`）：

```python
def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
```

DB 中 `param_schema_json` 字段被外部破坏 / 兼容性变更后失败 → 返回 `{}` → schema 为空 → 管理后台编辑 param 时 `_validate_by_schema` 对任何下发 param 都报"参数未定义"（line 657-663），但用户命令仍能执行（用 default 值）。审计：DB 数据损坏不会被发现，只在管理后台体现出"无法编辑参数"。建议至少在 `_parse_json_object` 失败时打一条 warning。

### U-2.6 `_check_user_banned` 在每条命令上重复触发完整事务

`command_config.py:366-377`：

```python
def _check_user_banned(user_id: str) -> str:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is not None and user.is_banned:
            ...
    finally:
        session.close()
    return ""
```

配合 `db.py:403-413` 的 `BEGIN IMMEDIATE`：每条命令执行触发一次"持写锁的 SELECT"。在高并发命令场景下，ban 检查事务串行化所有命令处理，本质上把整个机器人变成单线程命令调度器。User 表上 `user_id` 是 `unique=True`（`db.py:139`），查询本身 O(log n)，但事务开销主导。属于 hot-path 上的 fail-safe 但性能放大器。

### U-2.7 `_registry_lock` 是 `threading.RLock`，与 asyncio 单线程模型存在冗余但无害

`command_config.py:71`：`_registry_lock = threading.RLock()`。NoneBot2 是 asyncio 单线程，RLock 在单线程下永远立即获取。OK，但 over-engineered；未来引入 ThreadPoolExecutor 调用 mutation 函数时才有用。审计上不算问题，仅指出风格不一致：项目其他地方（e.g. stats.py）未使用任何 lock。

### U-2.8 `_get_raw_command` 静默 fallback 到 `""`，导致格式错误回复"模糊"

`command_config.py:102-108`：

```python
def _get_raw_command() -> str:
    try:
        matcher = current_matcher.get()
        prefix = matcher.state.get("_prefix", {})
        return str(prefix.get("raw_command", "")).strip()
    except Exception:
        return ""
```

`_build_usage_message`（line 111-119）在 `actual_command` 为空时回退到 `state.usage` 的第一个 token 作为 display_name。NoneBot `current_matcher.get()` 在 wrapper 内应有效，但 fallback 路径吞掉一切异常 → 若 matcher 状态丢失，usage 错误提示退回到模板的第一个 token（可能是别名而非实际触发的命令）。审计：silent fallback 影响 UX 而非正确性。

### U-2.9 `sync_registered_commands_to_db` 在 commit 失败时让 `_init_database` 整体启动失败

`command_config.py:696-763`：commit 失败 → 抛异常 → `finally` 关 session → `refresh_runtime_cache()`（line 763）不执行（因为异常已在 `session.commit()` 处冒泡 → finally 后继续 raise）→ 冒泡到 `bot.py:147` 的 `_init_database` → `@driver.on_startup` 失败 → NoneBot 启动失败。是 fail-fast 行为，正确。但 `finally` 只 close 不 rollback。SQLAlchemy session.close 时未 commit 事务自动 rollback，OK。审计：行为正确，记录确认。

### U-2.10 `update_command_aliases` 在校验阶段对其他 row 的 `aliases_json` 容错解析，损坏数据被静默跳过

`command_config.py:810-818`：

```python
        for r in all_rows:
            conflict_names.add(r.display_name)
            try:
                existing_aliases = json.loads(r.aliases_json or "[]")
                if isinstance(existing_aliases, list):
                    for a in existing_aliases:
                        conflict_names.add(str(a).strip())
            except (json.JSONDecodeError, TypeError):
                pass
```

某 command 的 `aliases_json` 损坏 → 该 command 的所有 aliases 不进入冲突集合 → 当前请求设置的 alias 可能与损坏 row 的真实 alias 重复，DB 写入成功 → 启动期 `register_alias_matchers()` 调 `on_command(alias)` 两次（NoneBot 允许，但产生 duplicate matcher）→ 同一 alias 上的消息被两条 matcher 重复处理。审计：等同于 U-2.2 的另一种触发路径。

### U-2.11 `param_values_json` 与 `param_schema_json` 间的"orphan param" 问题

`command_config.py:387-401` 的 `_merge_param_values`：

```python
def _merge_param_values(
    *,
    schema: dict[str, dict[str, Any]],
    old_values: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for name, definition in schema.items():
        if name in old_values:
            try:
                merged[name] = _validate_by_schema(definition, old_values[name], param_name=name)
                continue
            except CommandConfigValidationError:
                pass
        merged[name] = definition.get("default")
    return merged
```

行为：只迭代 `schema` 的 keys，不在 schema 中的 `old_values` keys 被静默丢弃。plugin 升级移除 param 时，DB 中保留的旧 value 自动清理。OK，符合 schema-driven 设计。但若 schema 字段名变更（`old_name` → `new_name`），旧值无法迁移，用户配置静默丢失到 default。属于 plugin 升级 contract 限制，记录但非 bug。

### U-2.12 `command_control` 装饰器对 `func` 不强制要求 `bot`/`event` 参数，silent skip 双重默认行为

`command_config.py:945-967`：

```python
@wraps(func)
async def wrapper(*args, **kwargs):
    ...
    if not state.enabled:
        bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
        mode, message = _get_disabled_policy()
        if mode == "reply" and bot is not None and event is not None:
            await bot.send(event, message)
        return None

    bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
    if bot is not None and event is not None:
        ban_msg = _check_user_banned(event.get_user_id())
        ...
```

`_resolve_bot_event` 通过 `bind_partial` + `bound.arguments.get("bot")` 提取，要求 handler 参数名严格叫 `bot` / `event`（line 411）。若 plugin 作者写成 `def my_handler(b: Bot, e: Event)`，`bound.arguments.get("bot")` 返回 None → ban 检查直接 skip（line 962 条件不成立）→ 被封禁用户仍能执行命令；disabled 命令的 "reply" 模式也降级为 silent skip。装饰器没有 lint-time 检查 handler 参数名，是潜在 contract 漏洞。

### U-2.13 装饰器对 `func` 已被另一装饰器包裹时的 module/handler_name 提取

`command_config.py:891-892`：

```python
        module_path = str(getattr(func, "__module__", "")).strip()
        handler_name = str(getattr(func, "__name__", "")).strip() or normalized_key
```

由于 `@command_control(...)` 在装饰器栈中相对位置不固定（可能在 `@matcher.handle()` 内层或外层），若 plugin 把 `@command_control` 放在另一个不传递 `__module__` / `__name__` 的 wrapper 内层，`module_path` / `handler_name` 退化为空字符串，最终影响 `meta_hash`（line 893-903）的稳定性。审计：影响"是否需要重新 sync DB row" 判断，不直接影响功能。建议至少 `functools.WRAPPER_ASSIGNMENTS` 提示，但当前项目 plugin 均直接装饰 raw handler 函数，OK。

---

## U-3 message_parser.py

### U-3.1 `_segments_to_plain_text` 把 `at` 段转成纯数字注入 text，与用户手输数字混淆

`message_parser.py:35-42`：

```python
        if seg_type == "at":
            qq = str(data.get("qq", "")).strip()
            if qq and qq != "all":
                # Keep spaces around converted qq so split() works reliably.
                parts.append(f" {qq} ")
            continue
```

`at` 段被转成 ` 123456 ` 注入 text。`parse_command_args` 后续 `text.split()` 拆出 `123456` token，下游 `resolve_user_id_arg_with_fallback`（line 126）`token.isdigit()` 命中，直接当 user_id 返回。

问题场景：用户手输 `/ban 123456 spam`（无 @ 段），123456 也是纯数字。同理 `/gift 1000 to @alice` 这种 amount-then-user 命令，第一个 token 是金额，第二个 token 是 user_id（被 @ 段转换）。`parse_command_args` 不保留 segment 类型，下游 handler 根据 `arg_index` 提取，无法区分"@ 段被转成的数字"和"用户手输的数字"。

对纯数字昵称用户（QQ 群里允许设置纯数字昵称）尤其有歧义。Round 7 plugins 审计需关注：handler 是否对参数顺序做了严格 contract（先 user 后 amount，还是反过来）。

### U-3.2 `parse_command_args` 把多空格压成单空格，会破坏 LLM-prompt-style 命令

`message_parser.py:43-44`：

```python
    text = "".join(parts)
    # Normalize duplicated whitespace generated by mixed segments.
    return re.sub(r"\s+", " ", text).strip()
```

`\s+ → " "` 把所有连续空白（含 `\t` `\n` `\r`）压成单空格。对参数分隔型命令（`/ban user reason`）OK；对 prompt-style 命令（若未来加 `/gpt please   format    this`），用户故意输入的多空格会被消除。审计上记一笔：当前所有 plugin 都是参数分隔型，没问题，但未来加 prompt 命令需要绕过此 helper。

### U-3.3 `_extract_args_text` 对 command_name 不做 normalize；与 NoneBot 的 alias 规则一致但脆弱

`message_parser.py:47-52`：

```python
def _extract_args_text(text: str, command_name: str) -> str | None:
    cmd = re.escape(command_name)
    match = re.match(rf"^/?{cmd}(?:\s+|$)", text)
    if match is None:
        return None
    return text[match.end() :].strip()
```

正则 `^/?{cmd}(?:\s+|$)` 要求命令名后紧跟空白或行尾。Handler 调用 `parse_command_args(event, command_name)` 时传入的 `command_name` 必须与触发用户输入的命令名严格一致（含大小写）。但 NoneBot `on_command("签到", aliases={"check_in"})` 触发后，handler 用 `parse_command_args(event, "签到")` 还是 `parse_command_args(event, "check_in")`？答：handler 通常硬编码 primary command_name，那么用户输 `/check_in arg1 arg2` 时 `_extract_args_text("check_in arg1 arg2", "签到")` 不匹配 → 返回 None → fallback 路径（`parse_command_args_with_fallback`）使用 NoneBot 自带的 `arg.extract_plain_text()`，绕过 message_parser 的 @ 段转换。结果：alias 触发的命令 @ 段解析能力丢失，下游 `resolve_user_id_arg_with_fallback` 只能用裸 plain text。审计：alias 路径与 primary 路径的解析能力不对称。

### U-3.4 `resolve_user_id_arg_with_fallback` 的 SQL 查询使用 `func.lower(User.name) == token.lower()`，依赖 `LOWER(name)` 函数索引

`message_parser.py:126-150`：

```python
    if token.isdigit():
        return token, None

    session = get_session()
    try:
        matched = (
            session.query(User)
            .filter(func.lower(User.name) == token.lower())
            .order_by(User.id.asc())
            .limit(2)
            .all()
        )
```

`nextbot/db.py:802-828` 启动时确保 `CREATE UNIQUE INDEX IF NOT EXISTS "ix_user_name_lower_unique" ON "user" (LOWER("name"))`，所以索引命中、OK。但若启动时已有重复 name 数据（uniqueness 失败），fallback 到非唯一 `ix_user_name_lower` 索引；若 fallback 也失败（极端情况），查询退化为全表扫描。`message_parser.py:131-137` 没有日志记录"未命中索引"或返回的 SQL plan，运维不易察觉性能 regress。审计：依赖外部模块的索引保证，本身实现 OK。

### U-3.5 `parse_command_args_with_fallback` 的 fallback 路径绕过日志

`message_parser.py:91-98`：

```python
def parse_command_args_with_fallback(
    event: Any, arg: Any, command_name: str
) -> list[str]:
    args = parse_command_args(event, command_name)
    if args:
        return args
    text = getattr(arg, "extract_plain_text", lambda: "")().strip()
    return [item for item in text.split() if item]
```

`parse_command_args` 返回非空时已经在 `message_parser.py:72` 打了 `logger.info`；返回空时 fallback 到 `arg.extract_plain_text()`，**没有日志记录走 fallback 路径**。排障时无法区分"原 message 无参数" / "alias 触发未匹配 command_name" / "@ 段解析失败"。建议至少在 fallback 命中时打一条 info。

---

## U-4 text_utils.py

### U-4.1 `safe_at_segment` 和 `safe_at_segment_or_empty` 内联 `from nonebot.adapters.onebot.v11 import MessageSegment` 每次调用 import

`text_utils.py:87-119`：

```python
def safe_at_segment(user_id: str) -> "OBV11MessageSegment | None":
    ...
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

    try:
        return OBV11MessageSegment.at(int(user_id))
    except (TypeError, ValueError):
        logger.warning(f"无法将 user_id 解析为整数 @ 段：user_id={user_id}")
        return None


def safe_at_segment_or_empty(user_id: str) -> "OBV11MessageSegment":
    ...
    from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment

    seg = safe_at_segment(user_id)
    if seg is None:
        return OBV11MessageSegment.text("")
    return seg
```

注释说 import 延迟是为了让 text_utils 在非 OBV11 环境（测试/V11-shim）可被 import。OK 的设计。但 Python 内部的 import-cache 让重复 import 几乎无成本（一次 lookup），不影响 hot path。审计上记录："延迟 import 是合规的，无性能影响"。

### U-4.2 `at_prefix` 在 user_id 非数字时退化为不带 @ 的内容直发，调用方需自己处理

`text_utils.py:122-137`：

```python
def at_prefix(event: "Event", content: Any, *, sep: str = " ") -> Any:
    ...
    at_seg = safe_at_segment(event.get_user_id())
    if at_seg is None:
        return content
    return at_seg + sep + content
```

`safe_at_segment` 返回 None 时（user_id 非数字，PC-4.1），`at_prefix` 返回 content 本身，调用方拿到的可能是 str / Message 任意类型。NoneBot 处理 reply 时一般兼容（OneBotV11 适配器把 str 当文本发送）。但若 content 是 OBV11 MessageSegment（已经 build 好的复杂消息），返回时类型一致；若 content 是 plain str，调用方拼接到其他 Message 上会失败。审计：silent type degradation 在多 adapter 项目里可能踩坑，但当前项目仅 OBV11 + console，影响小。

### U-4.3 `safe_at_segment_or_empty` 在 user_id 非数字时返回空 text 段 `text("")`，与 OneBot 发送语义微妙

`text_utils.py:114-119`：返回 `MessageSegment.text("")`。OneBot V11 协议允许空 text 段，client（NapCat / go-cqhttp）一般 silently drop。但 `at + " " + content` 后变成 `text("") + " " + content`，渲染为 `" " + content`，即正文前多一个空格。审计：silent UX 退化，无功能性问题。

### U-4.4 `reply_success`/`reply_failure` 与项目 CLAUDE.md "用户操作反馈文案规范" 是否一致

`text_utils.py:40-48`：

```python
def reply_success(action: str, detail: str | None = None) -> str:
    text = f"{STATUS_SUCCESS} {action}成功"
    if detail:
        text += f"，{detail}"
    return text


def reply_failure(action: str, reason: str) -> str:
    return f"{STATUS_FAILURE} {action}失败，{reason}"
```

CLAUDE.md 规定"动词使用通用词：保存 / 删除 / 创建 / 更新 / 提交 / 上传"、"不得包含操作对象名称"。`reply_success(action="保存")` → `"✅ 保存成功"`，符合规范。但 helper 不强制 `action` 内容，plugin 作者传 `action="删除服务器"` → 输出 `"✅ 删除服务器成功"`，违反规范。审计：helper 本身合规，但 lint 不到 caller 是否传入合规动词。建议 plugin 桶审计时检查 `reply_success("xxx")` 调用点 action 文本。

---

## U-5 time_utils.py

### U-5.1 `format_beijing_datetime(None)` 返回空串而非"未知" / "—"

`time_utils.py:36-40`：

```python
def format_beijing_datetime(value: datetime | None) -> str:
    converted = utc_naive_to_beijing(value)
    if converted is None:
        return ""
    return converted.strftime(_DATETIME_FORMAT)
```

`stats.py:131` 的 `format_beijing_datetime(command_execute_updated_at)` 在 SystemStat row 不存在时（首次启动尚无 stat 写入）返回 `""`，前端 dashboard 看到空字符串。后端 dict key 仍存在，前端需要自己判定空。审计：API 行为一致，但前端依赖"空字符串 = 未知" 隐式约定，未在文档体现。

### U-5.2 `utc_naive_to_beijing` 假定输入 naive datetime 是 UTC

`time_utils.py:28-33`：

```python
def utc_naive_to_beijing(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)
```

DB 中 `DateTime` 字段使用 `db_now_utc_naive()`（line 12-13）写入，保证是 UTC naive。但若有任何代码路径直接写入 `datetime.now()`（本地时间 naive）到 DateTime 列，`utc_naive_to_beijing` 会把本地时间当作 UTC 再 +8h，渲染出未来 8 小时的"北京时间"。审计：依赖项目其他模块严格使用 `db_now_utc_naive`，无强制约束。

全仓搜 `datetime.now(` 验证：

```bash
grep -rn "datetime.now(" /Users/arispex/CascadeProjects/nextbot/nextbot --include="*.py" | grep -v UTC
```

需要 plugin 桶审计层面检查。time_utils 自身 OK。

### U-5.3 `seconds_until_next_beijing_midnight` 在 DST 边界返回不准确（实际 Asia/Shanghai 无 DST，OK）

`time_utils.py:67-74`：用 `BEIJING_TZ = ZoneInfo("Asia/Shanghai")` + `datetime.combine(date + 1day, time.min, tzinfo=BEIJING_TZ)` 计算下一个北京零点。`Asia/Shanghai` 自 1991 年起无 DST，结果稳定。审计：实现正确。若未来支持其他时区（含 DST），需重新审视。

### U-5.4 `db_now_utc_naive` + `BEGIN IMMEDIATE` 时钟偏移风险

`time_utils.py:12-13`：

```python
def db_now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
```

`db_now_utc_naive()` 在 `command_config.py:701, 787` 等 mutation 处生成时间戳。若服务器 NTP 时钟回拨（短暂 < 1s 跳变常见），新写入的 `updated_at` 可能 < 上一次写入的 `updated_at`。当前项目没有依赖 `updated_at` 单调性的代码（dashboard 只显示，不排序），影响小。但 SH-8.x 系列后若引入"按 updated_at 排序的事件日志"，需要 monotonic clock 保证。审计：记录潜在风险。

---

## U-6 progression.py

### U-6.1 `parse_tier` 的 alias 集合硬编码且未文档化

`progression.py:41-52`：

```python
def parse_tier(raw: str) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    # Accept several aliases for "no requirement"
    if s in {TIER_NONE, TIER_NONE_ZH, "无要求", "None", "NONE"}:
        return TIER_NONE
    if s in PROGRESSION_KEY_TO_ZH:
        return s
    if s in PROGRESSION_ZH_TO_KEY:
        return PROGRESSION_ZH_TO_KEY[s]
    return None
```

接受的 "no requirement" 别名：`"none"`、`"无"`、`"无要求"`、`"None"`、`"NONE"`。但没接受 `"None"` 之外的常见 case 变体如 `"none"`（已在 `TIER_NONE`） / `"无"`（已在 `TIER_NONE_ZH`） / `"NONE"` / `"None"`，遗漏 `"none "`（带空格已被 strip）和小写 `"none"` 已覆盖。审计：覆盖较全。但 `"无 要求"`（中文带空格）不在内，且 alias 集合是 hardcoded set 而非 enum / data file，扩展时容易遗漏。属于设计风格，非 bug。

### U-6.2 `PROGRESSION_RANK[TIER_NONE] = -1` 与 `PROGRESSION_TIERS` 索引 0 冲突？

`progression.py:34-35`：

```python
PROGRESSION_RANK: dict[str, int] = {key: i for i, (key, _) in enumerate(PROGRESSION_TIERS)}
PROGRESSION_RANK[TIER_NONE] = -1
```

`kingSlime` rank=0，`TIER_NONE` rank=-1。下游 plugin 比较时 `user_rank >= required_rank`，TIER_NONE 永远满足任何 required_rank > -1 的要求 —— 这是 bug 还是 feature？答：feature。"无要求" 意味着任何进度都满足，但用户没打过任何 boss 时 user_rank=-1 应该满足 required_rank=-1（kingSlime 之前），所以语义是"用户进度（含起点）≥ 命令要求（含 None）"。OK。

### U-6.3 `tier_zh(key)` 在未知 key 时返回 key 本身

`progression.py:55-56`：

```python
def tier_zh(key: str) -> str:
    return PROGRESSION_KEY_TO_ZH.get(key, key)
```

未知 key 返回 key 字符串本身（英文 camelCase 如 `"someUnknownBoss"`），不抛异常。前端展示时若 DB 中残留旧 tier key（plugin 升级未清理），用户看到英文裸名，不美观但不致命。审计：silent fallback，记录。

---

## U-7 stats.py

### U-7.1 `increment_stat` 用 SQLite UPSERT 语法，`engine.begin()` 配合 `BEGIN IMMEDIATE` 序列化所有 stat 更新

`stats.py:25-50`：

```python
def increment_stat(stat_key: str, delta: int = 1) -> None:
    key = str(stat_key).strip()
    if not key:
        return

    amount = int(delta)
    if amount == 0:
        return

    now = db_now_utc_naive()
    engine = get_engine()

    with engine.begin() as connection:
        statement = insert(SystemStat).values(
            stat_key=key,
            stat_value=amount,
            updated_at=now,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[SystemStat.stat_key],
            set_={
                "stat_value": SystemStat.stat_value + amount,
                "updated_at": now,
            },
        )
        connection.execute(upsert)
```

使用 `sqlalchemy.dialects.sqlite.insert(...).on_conflict_do_update(...)`，SQLite 原生 UPSERT，原子操作，无 lost-update。`engine.begin()` 配合 `db.py:403-413` 的 `BEGIN IMMEDIATE` 让事务一开始就持写锁，多并发 increment 串行执行但不丢更新。OK。

但 `increment_command_execute_total()` 在 `command_config.py:951` wrapper 内被每条命令调用，导致每条命令额外占用一个写事务窗口（含 `BEGIN IMMEDIATE` 锁等待）。高 QPS 下 stats 写入是天然瓶颈点。审计：实现正确，但与 ban 检查（U-2.6）合并起来，单条命令至少 2 次持写锁 DB 事务（command_execute_total + user ban check）。

### U-7.2 `get_stat_value` 不带事务，与 `BEGIN IMMEDIATE` 配合时仍持写锁

`stats.py:53-65`：

```python
def get_stat_value(stat_key: str, default: int = 0) -> int:
    key = str(stat_key).strip()
    if not key:
        return int(default)

    session = get_session()
    try:
        row = session.query(SystemStat).filter(SystemStat.stat_key == key).first()
        if row is None:
            return int(default)
        return int(row.stat_value)
    finally:
        session.close()
```

`get_session()` 创建的 Session 在第一次 query 时隐式开启事务（autoflush=False, autocommit=False, `db.py:415`），事件 `BEGIN IMMEDIATE` 触发（`db.py:403-413`），即"读 stat 也会持写锁"。审计：读路径性能受写串行化影响，与 SH-8.2 描述的设计 trade-off 一致。

### U-7.3 `get_dashboard_metrics` 单事务内做 8 次独立 query，无 N+1 但有"长事务" 风险

`stats.py:72-108`：

```python
session = get_session()
try:
    server_count = ...
    user_count = ...
    group_count = ...
    command_total = ...
    command_enabled_count = ...
    command_disabled_count = max(command_total - command_enabled_count, 0)
    today_text = beijing_today_text()
    signed_today_count = ...
    total_coins = ...
    command_total_row = ...
    command_execute_count = int(command_total_row.stat_value) if command_total_row else 0
    command_execute_updated_at = (
        command_total_row.updated_at if command_total_row else None
    )
finally:
    session.close()
```

整个 dashboard 查询在单 session 中执行，配合 `BEGIN IMMEDIATE` 持写锁 8 个 query 期间，所有 mutation（命令注册更新、用户签到、stats 写入）必须等 dashboard query 完成才能 acquire 写锁。Dashboard 由 web_server 提供给管理端，访问频率低，但若管理端定时轮询 dashboard，会成为 mutation 串行点。

另外 `command_disabled_count = max(command_total - command_enabled_count, 0)` 是两次独立 count 后减法，理论上两次 count 之间数据可能变化（不在原子事务内），但 `BEGIN IMMEDIATE` 让 session 内所有 SELECT 持同一写锁，事务隔离让两次 count 在同一快照中。OK。

### U-7.4 `get_dashboard_metrics` 对 `get_bots()` 的容错过宽

`stats.py:110-114`：

```python
connected_bot_ids: list[str] = []
try:
    connected_bot_ids = sorted(str(bot_id) for bot_id in get_bots().keys())
except Exception:
    connected_bot_ids = []
```

`nonebot.get_bots()` 返回 dict（启动后保证存在）。捕获 `Exception` 太宽 —— 若 get_bots 抛 `ValueError("get_driver failed before nonebot.init")`（启动期早期调用），silently 返回空列表，dashboard 显示"暂无 Bot 连接"，与"实际未启动" 难以区分。审计：silent fallback，建议至少 `logger.warning`。

### U-7.5 `increment_stat` 对 `delta` 强制 `int(delta)`，浮点 delta 静默转 int

`stats.py:30-32`：

```python
amount = int(delta)
if amount == 0:
    return
```

`delta=0.5` → `amount = 0` → silent skip；`delta=1.9` → `amount = 1`；负 delta（计数撤销）会让 stat 减少。当前唯一 caller 是 `increment_command_execute_total()`（line 68-69）传 `1`，没问题。但 API 接受 `int = 1` 默认参数，类型注解未限制为正整数。若未来加 `decrement_stat` 用 `increment_stat(key, -1)`，能跑通但语义混乱。审计：类型 contract 不严格，记录。

### U-7.6 dashboard `command_execute_count` 与 wrapper increment 时机的 cache inconsistency

`stats.py:98-106` 读 `STAT_COMMAND_EXECUTE_TOTAL`；`command_config.py:951` wrapper 进入时 `increment_command_execute_total()`。当用户禁用命令（`state.enabled=False`，line 954）时 wrapper 已经 incremented，但命令实际未执行 → 计数包含被拒绝的命令。当用户被 ban 时（line 962-968）同样已 incremented → 计数包含被 ban 用户的尝试次数。审计：`command.execute.total` 实际语义是"命令 wrapper 触发次数"而非"成功执行次数"。文档未澄清。

---

## U-8 data_dir.py

### U-8.1 `_resolve_data_dir` 在 import 时执行 `mkdir`，无权限失败兜底

`data_dir.py:25-32`：

```python
def _resolve_data_dir() -> Path:
    raw = os.environ.get("NEXTBOT_DATA_DIR", "").strip()
    path = Path(raw).expanduser().resolve() if raw else _PROJECT_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR: Path = _resolve_data_dir()
```

`mkdir(parents=True, exist_ok=True)` 在 path 权限不足 / 父路径不可写时抛 `PermissionError`。import 阶段直接崩，错误堆栈停在 `data_dir.py:28`，对用户提示是 NoneBot 启动失败。若用户用相对路径 `NEXTBOT_DATA_DIR=./data`（typo 没设绝对路径），`Path.resolve()` 解析为相对当前工作目录的绝对路径，与 `LOCALSTORE_USE_CWD=true`（bot.py:22）协同工作但脆弱：systemd unit 用 `WorkingDirectory=` 切换 cwd 时可能找不到 .env / app.db。审计：默认值（`_PROJECT_ROOT`）和环境变量混用、无显式日志说明最终路径，运维排障困难。

### U-8.2 `Path(raw).expanduser().resolve()` 会跟随符号链接

`data_dir.py:27`：`Path(raw).expanduser().resolve()` 中 `resolve()` 默认 `strict=False` 但会跟随 symlink。Docker 部署常用 `NEXTBOT_DATA_DIR=/app/data`，宿主 mount 到容器内 `/app/data` —— OK；但若管理员通过 symlink 重定向（`/app/data -> /mnt/storage/nextbot`），`resolve()` 会展开为目标路径。后续日志 `app.db` 位置显示真实路径而非用户配置的 symlink。属于"少量 UX 困惑"，无功能问题。

### U-8.3 `_PROJECT_ROOT = Path(__file__).resolve().parent.parent` 与 packaging 模型耦合

`data_dir.py:22`：假设 `nextbot/data_dir.py` 在项目根 `nextbot/` 包下，向上两级是项目根（含 `bot.py`）。若 nextbot 包被 pip-installed 到 site-packages（未来打包发布），`__file__` 解析为 site-packages 内路径，`_PROJECT_ROOT` 指向 site-packages 父目录，写 `.env` 到那里会失败 / 权限错误。审计：当前仅 source-checkout / Docker 部署，OK；未来若发布为 pip 包，需要重构。

### U-8.4 路径遍历 / 注入

`data_dir.py:26-27`：`os.environ.get("NEXTBOT_DATA_DIR")` 是受信任的环境变量（管理员设置），不接受用户输入。`Path(raw).expanduser().resolve()` 解析 `~/...`，没有 `../../` 等 traversal 风险（resolve 会规范化）。审计：实现正确，无 traversal 攻击面。

### U-8.5 `mkdir` 与并发 import 的 TOCTOU

`data_dir.py:28`：`mkdir(parents=True, exist_ok=True)` 是原子操作（POSIX），`exist_ok=True` 处理目录已存在场景。Python 多 worker / multi-process 同时启动时无 race。审计：实现正确。

---

## 总结：8 个文件 38 条 finding

| Bucket | 主要风险 | findings |
|---|---|---|
| bot.py | env-file 写盘无 fallback；console-bypass 漏洞；缺 shutdown hook | U-1.1 ~ U-1.6 |
| command_config.py | alias 冲突 / ban 检查 fail-hard / DB 损坏 silent fallback / 装饰器 contract 漏洞 | U-2.1 ~ U-2.13 |
| message_parser.py | @ 段与数字 token 混淆；alias 触发解析能力不对称 | U-3.1 ~ U-3.5 |
| text_utils.py | reply helper 不校验 caller 是否符合文案规范 | U-4.1 ~ U-4.4 |
| time_utils.py | format_None 返回空串前端隐式约定；naive UTC 依赖 | U-5.1 ~ U-5.4 |
| progression.py | tier_zh 未知 key silent fallback | U-6.1 ~ U-6.3 |
| stats.py | 每条命令 2 次持写锁事务；dashboard 长事务；command_execute_count 语义不清 | U-7.1 ~ U-7.6 |
| data_dir.py | mkdir 无 fallback；symlink resolve；packaging 假设 | U-8.1 ~ U-8.5 |

最严重需要 plugin 桶审计跟进确认的：U-2.2（alias / command_key 冲突）、U-2.3（ban 检查 fail-hard）、U-2.12（handler 参数名 contract）、U-3.1（@ 段与数字 token 混淆）、U-1.2（console adapter 硬编码 bypass）。

## Caveats / Not Found

- 未对 `command_config.py` 的 DB 写入做端到端事务测试（仅静态审计）。
- 未运行 stats 高并发压测验证 U-7.1 的瓶颈定量数据。
- `_filter_allowed_messages` 的 console adapter 行为依赖 NoneBot console adapter 源码细节，未在测试环境复现。
- U-3.3 的 alias-via-NoneBot 路径需要 handler 实际代码确认是否调用 `parse_command_args_with_fallback`，本审计未枚举 caller。
