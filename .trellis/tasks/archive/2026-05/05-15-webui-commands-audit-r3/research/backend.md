# R3 Backend 桶审计 — commands 页面

- **Query**: 复审 R2 commit `f512c8c`（M-B2 / M-B3 / M-B4 / L-B2）+ 全量再扫剩余 finding
- **Scope**: 严格限定 1 个后端文件 `server/routes/webui_commands.py`
- **Date**: 2026-05-15

## Part A: R2 修复复审

### A.1 M-B2 enabled=null 拒绝 — **PASS**

代码位置：`webui_commands.py:204-213`

```python
if "enabled" in payload:
    raw_enabled = payload.get("enabled")
    if not isinstance(raw_enabled, bool):
        return api_error(status_code=422, code="validation_error", message="enabled 必须是布尔值")
    update_payload["enabled"] = raw_enabled
```

| 场景 | 行为 | 评估 |
|---|---|---|
| `{"enabled": null}` | `"enabled" in payload` = True, `isinstance(None, bool)` = False → 422 + `validation_error` | PASS：与目标语义一致 |
| `{"enabled": false}` | `isinstance(False, bool)` = True → `update_payload["enabled"] = False` → service 层正常设置 | PASS |
| `{"enabled": true}` | 同上 | PASS |
| `{"enabled": "true"}` | `isinstance("true", bool)` = False → 422 | PASS：service 层 `_coerce_enabled` 会接受 string，路由层在前面拦截更严格，不引发歧义 |
| `{"enabled": 1}` / `{"enabled": 0}` | `isinstance(1, bool)` = False（注意 Python `bool` 是 `int` 子类，但 `isinstance(int_value, bool)` 仍然 False，反向 `isinstance(True, int)` 才 True）→ 422 | PASS：与 dashboard 数值 enabled 风格一致 |
| **缺 enabled 仅传 param_values**：`{"param_values": {...}}` | `"enabled" in payload` = False → 整段跳过；`update_payload` 只有 `param_values` | PASS：PATCH 部分更新语义保留 |
| **同时缺 enabled 和 param_values**：`{}` | 两个分支都跳过 → `if not update_payload:` 触发 → 400 `invalid_request_body` | PASS |

**与 service 层语义协调**：
- service 层 `update_command_config(command_key, *, enabled=None, param_values=None)`（`command_config.py:620-625`）：`enabled is None` 路径直接 skip 不写库。
- 路由层修复后，`raw_enabled is None` 已在路由层拦截 422，不会到达 service 层。
- 路由层 `update_payload["enabled"] = raw_enabled` 只在 `isinstance(raw_enabled, bool)` 通过后写入，因此 service 层只会收到 bool 或不收到（sentinel `None`），无歧义。

**触发概率**：高（前端 JS bug / 人工 curl 都可能误传 null）。修复触发概率：每次错误请求均生效。

**结论**：M-B2 PASS。

### A.2 M-B3 _client_ip import + 5 logger sites — **PASS**

#### A.2.1 import 单向性（无循环）

| 方向 | 位置 | 状态 |
|---|---|---|
| `webui_commands.py:24` → `from server.routes.webui import _client_ip` | 直接依赖 | OK |
| `webui.py` → `webui_commands` | `grep -rn "from server.routes.webui_commands" server/routes/webui.py` 无命中 | **单向，无循环** |
| `web_server.py:13` | `from server.routes.webui_commands import router` | 顶层 wiring，正常 |

`server/routes/webui.py` 仅引入 `console_page` / `server_config`，无反向依赖。**Import 拓扑无环**。

#### A.2.2 5 logger 调用全含 `client_ip=` + `user_agent=`

| # | 行号 | 调用 | client_ip | user_agent | level | 备注 |
|---|---|---|---|---|---|---|
| 1 | `:166-169` | list 加载异常 | ✓ | ✓ | `exception` | 未知异常用 exception 合理 |
| 2 | `:236-239` | update_command_config 校验异常 | ✓ | ✓ | `warning` | service 主动抛 `CommandConfigValidationError`，用 warning 合理（业务可预期） |
| 3 | `:247-250` | update_command_config 未知异常 | ✓ | ✓ | `exception` | 兜底用 exception 合理 |
| 4 | `:299-302` | update_command_aliases 校验异常 | ✓ | ✓ | `warning` | 同上 |
| 5 | `:311-314` | update_command_aliases 未知异常 | ✓ | ✓ | `exception` | 同上 |

另有 2 处 `logger.info` 成功路径（`:257-259` 保存配置成功、`:321-323` 保存别名成功）：仅含 `client_ip=`，无 `user_agent=`。

**Finding L-B3（INFO 级别一致性，Low）**：
- 行号：`:257-259`、`:321-323`
- 现状：成功路径 INFO 日志缺 `user_agent` 字段
- 对比：登入成功路径 `webui.py:388` 同样仅含 `client_ip`（`logger.info(f"创建登录会话成功：client_ip={client_ip}")`）
- 严重度：Low / 一致性问题（与 webui.py 风格对齐，不算回归）
- 触发概率：N/A（已与基线一致）
- 修复建议：**不修**，与 login-audit / dashboard 风格一致；如要扩，应在共享层统一推进

#### A.2.3 user_agent truncate 200 字符（与 webui.py / webui_dashboard.py 一致）

| 文件 | 行号 | 表达式 |
|---|---|---|
| `webui_commands.py` | `:165, :230, :293` | `request.headers.get("user-agent", "")[:200]` |
| `webui.py` | `:211, :329` | `request.headers.get("user-agent", "")[:200]` |
| `webui_dashboard.py` | `:20` | `request.headers.get("user-agent", "")[:200]` |

**完全一致**，PASS。

#### A.2.4 logger.warning vs logger.exception 选择

- `logger.warning`：service 主动抛 `CommandConfigValidationError`（业务可预期），合理。
- `logger.exception`：未知异常 / `except Exception as exc: # noqa: BLE001`，合理（含 stack trace）。

DRY 程度：5 处 logger 调用结构高度重复（`reason=...client_ip=...user_agent=`），可抽 helper，但属于美学优化，**非缺陷**。

**结论**：M-B3 PASS。

### A.3 M-B4 _PARAM_KEY_PATTERN 字符集 — **PASS（带 1 处注释偏差）**

代码位置：`webui_commands.py:35`

```python
_PARAM_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
```

#### A.3.1 覆盖所有 plugin 实际 param key

实测扫描 `@command_control(... params={...})` 中所有顶层 key（42 个去重，commit message 称 48 是含未去重 / batch 重复，实测 42 已足够）：

```
big_multiplier, close_multiplier, close_range, cooldown_minutes, cooldown_seconds,
counter_rate, counter_steal_percent, crit_multiplier, crit_rate, enable_streak,
exact_multiplier, fail_penalty_percent, far_range, keep_order, limit, max_coins,
max_cost, max_count, max_draws, max_steal_percent, max_streak_bonus, min_amount_per_slot,
min_coins, min_coins_to_rob, min_cost, min_play_count, min_rob_count, min_steal_percent,
near_multiplier, near_range, police_penalty_percent, police_rate, range_max,
recycle_ratio, send_link, show_index, show_stats, small_multiplier, streak_bonus_per_day,
success_rate, toggle_cost, triple_multiplier
```

每个 key 都满足 `^[A-Za-z0-9_]+$`。**覆盖率 100%**。

#### A.3.2 注释偏差（非缺陷，但精确性提示）

`webui_commands.py:34, :64` 注释写：

> 与 plugin schema 实际命名约定对齐：`[A-Za-z_][A-Za-z0-9_]*`

实际 pattern `^[A-Za-z0-9_]+$` **比注释更宽松**：允许首字符为数字（如 `"1st_param"`），而注释声明的 `[A-Za-z_]` 起始不允许。当前 42 个实际 key 均以字母 / 下划线开头，因此**不影响业务**。

**Finding L-B4（注释精度，Low）**：
- 行号：`:34, :64`
- 现状：注释声明 `[A-Za-z_][A-Za-z0-9_]*`，实际 pattern `[A-Za-z0-9_]+` 允许首位数字
- 严重度：Low / 文档与实现差异
- 触发概率：极低（无 plugin 用首位数字）
- 修复选项二选一（**非阻塞**）：
  - 选 A：pattern 改为 `^[A-Za-z_][A-Za-z0-9_]*$`，对齐注释（更严格）
  - 选 B：保留 pattern，注释改为「`[A-Za-z0-9_]+`，宽松匹配 plugin schema」

#### A.3.3 模块级常量初始化 + 调用时序

- 模块级：`webui_commands.py:35` 一次性编译，符合 `re.compile` 复用最佳实践。
- 调用时序：`_validate_param_values` 先校验 `isinstance(param_key, str)` + 长度上限（`:57`），再调用 `_PARAM_KEY_PATTERN.fullmatch`（`:65`）。**先类型 / 长度后字符集**，时序合理（短路最便宜检查）。
- `fullmatch` 已隐含「整个串匹配」，等价 `^...$`；与 pattern 中 `^...$` 重复但无副作用。

**结论**：M-B4 PASS（注释偏差 L-B4 标 Low，不强制修）。

### A.4 L-B2 alias strip 再算长度 — **PASS（语义正确）**

代码位置：`webui_commands.py:96-103`

```python
# L-B2: 先 strip 再算长度，避免前端 trim 后合法的 alias 因末尾空格被路由层误拒。
# 不在此过滤空串：service 层会自动 continue 跳过。
if len(alias_item.strip()) > _ALIAS_MAX_LEN:
    return api_error(...)
```

#### A.4.1 strip 前后行为差异

| 输入 | 原行为（strip 前算长度） | 现行为（strip 后算长度） | 评估 |
|---|---|---|---|
| `"  myalias  "` (含前后空格 11 字符) | 11 > 32：PASS | 8 > 32：PASS | 等价（都通过） |
| `"a" * 33` | 33 > 32：reject | 33 > 32：reject | 等价 |
| `"a" * 32 + "  "` (尾空格 34 字符) | 34 > 32：**reject（误拒）** | 32 > 32：PASS | **L-B2 修复点** |
| `"  "` (纯空格) | 2 > 32：PASS | 0 > 32：PASS → 进入 service → service `strip()` 后空，`continue` 跳过 | 等价 |
| `""` (空串) | 0 > 32：PASS → 进入 service `continue` 跳过 | 同上 | 等价 |

修复点真实有效。

#### A.4.2 service 层 `update_command_aliases` 内部 strip 兼容

`command_config.py:796-806`：

```python
for raw in aliases:
    alias = str(raw).strip()
    if not alias:
        continue
    if " " in alias:
        raise CommandConfigValidationError("别名不能包含空格", ...)
    cleaned.append(alias)
```

- service 层独立 `strip()` 处理前后空格 → 与路由层 strip-then-len 兼容。
- service 层「`" " in alias`（strip 后）」检查 alias 内部含空格，会**主动 422 抛错**（非 silent drop）。
- 路由层 L-B2 修复不影响 service 层行为（不重复处理空白）。

#### A.4.3 边界场景

| 场景 | 路由层 | service 层 | 整体 |
|---|---|---|---|
| `["a b c"]` (含内部空格) | strip 后 `"a b c"` 长度 5 → PASS | `" " in "a b c"` → 422 `别名 "a b c" 包含空格` | 422 |
| `["a"]` 后追加 33 空格 | strip 后 `"a"` 长度 1 → PASS | strip 后 `"a"`：合法 | 200 |
| `[" "]` | strip 后 `""` 长度 0 → PASS | `not alias` → continue | 别名列表为空，service 落库为 `[]` |

#### A.4.4 单元素长度 `_ALIAS_MAX_LEN = 32` 是否合理

- alias 是 OneBot command 名，通常 1-6 字符（如 `/签到`、`/help`）。32 字符上限对中文 alias 约 10 个汉字（UTF-8 1 字符=1 Python str char），足够。

**结论**：L-B2 PASS。

---

## Part B: 全量再扫新发现

### B.1 严重度判定基线

| 严重度 | 含义 |
|---|---|
| Critical | 安全 / 数据完整 / 服务可用性即时风险 |
| High | 高概率引发线上事故，但非即时 |
| Medium | 业务正确但可能因边界条件失效 |
| Low | 美学 / 文档 / 一致性 |

### B.2 新发现

#### Finding R3-NB-1：keyword 大小写 normalization 不对称（**Low**）

- 行号：`webui_commands.py:137, 147-161`
- 现状：
  ```python
  keyword = str(request.query_params.get("q") or "").strip().lower()
  ...
  if keyword in " ".join([...]).lower()
  ```
- 现象：左右两侧都 `.lower()`，行为正确（ASCII / 中文 lower 等价 / Unicode lower 不变）。
- 潜在问题：Unicode 大小写折叠（如 Turkish I）`.lower()` 与 `.casefold()` 在某些 locale 下行为不一致。本场景全是 ASCII 命令名 / 中文 display_name，**无影响**。
- 严重度：Low / 美学
- 触发概率：极低
- 修复：**不修**

#### Finding R3-NB-2：keyword 长度无上限（**Low**）

- 行号：`webui_commands.py:137`
- 现状：`keyword = str(request.query_params.get("q") or "").strip().lower()`，无长度限制。
- 风险：恶意客户端传 1MB `q=`，触发 `keyword in haystack` 的 O(n*m) 子串扫描，对每个 command 都执行。
- 量化：commands 总量 ~50，每条 haystack ~500 字符，恶意 keyword 1MB → `in` 操作仍是常数级（haystack < keyword 直接 False）。FastAPI / Starlette 默认对 query string 无显式上限，但 HTTP server 层（uvicorn `h11_max_incomplete_event_size`）通常限制在 16KB 以内。**实际可利用面非常窄**。
- 严重度：Low
- 触发概率：极低（query string 已被 HTTP server 层限制）
- 修复建议：可选加 `keyword[:200]` 防御，**非阻塞**

#### Finding R3-NB-3：list endpoint 缺 client_ip / user_agent 在成功路径（**Low，与 dashboard 对齐保持现状**）

- 行号：`webui_commands.py:130-184`
- 现状：list endpoint 只在 `except` 异常路径打日志，**成功路径无 INFO**（与 dashboard `webui_dashboard.py:14-30` 完全一致）。
- 对比 PATCH endpoint：成功路径有 INFO（`:257-259`、`:321-323`）。
- 评估：GET / list 通常不打成功日志（避免高频噪音）；PATCH / DELETE 等变更操作打成功日志合理。**已是 web 服务通行风格**。
- 严重度：Low
- 修复：**不修**

#### Finding R3-NB-4：PATCH endpoint 同一请求触发 _client_ip 2 次解析（**Low / 性能**）

- 行号：`webui_commands.py:229, 230, 292, 293`
- 现状：每个 PATCH endpoint 在 try 外解析一次 `client_ip` 和 `user_agent`，在异常路径直接复用。**只解析一次，已是最优**。
- 评估：误读，实际已经只解析一次。**无 finding**。

（保留编号占位，无实际 finding。）

#### Finding R3-NB-5：keyword 搜索 join 在大数据集 / 每次请求重复构造（**Low / 性能**）

- 行号：`webui_commands.py:147-161`
- 现状：
  ```python
  if keyword:
      commands = [
          item for item in commands
          if keyword in " ".join([
              str(item.get("display_name") or ""),
              str(item.get("description") or ""),
              str(item.get("usage") or ""),
              str(item.get("permission") or ""),
              str(item.get("command_key") or ""),
              " ".join(item.get("aliases") or []),
          ]).lower()
      ]
  ```
- 现象：每个 command 都构造 1 个 list + 1 个 `" ".join` + 1 个 `.lower()`。50 命令 / 请求 → 50 次 join + lower，**毫秒级**，无问题。
- 严重度：Low / 性能
- 触发概率：N/A
- 修复：**不修**

#### Finding R3-NB-6：read_json_object 无 body size 限制（**Low，已知 H-B1 backlog**）

- 行号：`__init__.py:51-68`（routes 共享层）
- 现状：`await request.json()` 无显式 Content-Length 限制，依赖 ASGI server 层。
- PRD 已声明：H-B1 共享 body size limit 推进到 FastAPI middleware 层 backlog，不在本任务 scope。
- **scope-out backlog**：标记，**不计入本任务严重度**。

#### Finding R3-NB-7：update_payload 字段顺序 / 部分更新无原子性（**Low / 文档级**）

- 行号：`webui_commands.py:203-219`
- 现状：`update_payload` dict 接收 `enabled` 与 `param_values`，service 层 `update_command_config` 在同一 transaction 内处理两者。
- 实测 `command_config.py:649-712`：`session = get_session()` → 校验 / 写入 → `session.commit()`，**单 transaction**，原子性 OK。
- **无 finding**。

#### Finding R3-NB-8：_validate_aliases_list 仅对 list 元素长度限制，未限制总字符数（**Low**）

- 行号：`webui_commands.py:81-104`
- 现状：`_ALIASES_MAX_ITEMS=32`、`_ALIAS_MAX_LEN=32` → 最大 32×32 = 1024 字符（含中文 ~340 字）。
- 风险：可控（DB column 假定可容纳；JSON 序列化无问题）。
- 严重度：Low / 容量规划
- 修复：**不修**

#### Finding R3-NB-9：command_key 路径参数 + body 中无 command_key 字段交叉验证（**Low / 设计选择**）

- 行号：`webui_commands.py:187-188, 263-264`
- 现状：command_key 在 URL path 中；body 不接收 command_key。
- 评估：RESTful 设计正确，无 finding。

#### Finding R3-NB-10：_map_validation_error 字符串匹配脆弱（**Medium，scope-out**）

- 行号：`webui_commands.py:107-122`
- 现状：通过 `item_message == "命令不存在"` / `item_message == "命令已下线，无法编辑"` 字符串字面值映射到 404 / 409。
- 风险：service 层 `command_config.py:661, 666, 819, 824` 的 `message` 字符串若修改 → 路由层失去 404 / 409 映射 → 全部回落到 422。
- 严重度：Medium（潜在静默回归）
- 触发概率：低（这些字符串自 R1 起未变）
- **scope-out backlog**：跨模块字符串耦合 = R1+R2 已声明的 C-5 backlog（scope-out backlog，PRD line 47），**不计入本任务严重度**。

#### Finding R3-NB-11：raw_aliases 元素是 str 但内部 strip 不在路由层（**Low**）

- 行号：`webui_commands.py:296` `update_command_aliases(command_key, raw_aliases)`
- 现状：路由层不对 `raw_aliases` 中元素做 strip 后再传 service；service 内部独立 strip。
- 评估：与 service 层契约一致（service 接 raw list，自负归一化）。L-B2 修复只在路由长度校验时 strip。**无 finding**。

#### Finding R3-NB-12：_PARAM_VALUES_STR_MAX_LEN 仅校验 str 值，对其他类型无校验（**Low**）

- 行号：`webui_commands.py:71-77`
- 现状：仅当 `isinstance(param_value, str)` 时校验长度 4096。`int` / `float` / `bool` / `None` / `list` / `dict` **不在路由层校验**。
- 评估：service 层 `_validate_by_schema` 对 type 严格校验：
  - 非 schema 范围内的 `list` / `dict` / `None` → service 层 `_coerce_*` 抛 `CommandConfigValidationError`。
  - 路由层只是 cheap pre-check，**深度校验交给 service 合理**。
- 严重度：Low / 设计选择
- 触发概率：低
- **无 finding**

#### Finding R3-NB-13：keyword "" + sort 后顺序固定（**Low**）

- 行号：`webui_commands.py:141-146`
- 现状：sort key `(display_name.lower(), command_key.lower())`，**stable**，无 finding。

#### Finding R3-NB-14：build_pagination_slice 对超界 page 行为（**Low / 共享层**）

- 行号：`__init__.py:144-160`
- 评估：`current_page = min(max(int(page), 1), total_pages)`，超界回弹到末页，offset 计算正确。**无 finding**。

#### Finding R3-NB-15：list_command_configs 调用每次都 sort 2 次（**Low / 性能**）

- 行号：`webui_commands.py:141-146` 路由层 sort；`command_config.py:597` service 层 `sorted(_runtime_cache.keys())`。
- 现状：service 层先按 command_key 排序，路由层再按 (display_name, command_key) 排序。**两次 sort**，路由层覆盖 service 层结果。
- 评估：sort 复杂度 O(n log n)，n~50，毫秒级。冗余但**非缺陷**。
- 修复：service 层结果已排好可信任，**不修**

#### Finding R3-NB-16：异常路径上 logger 与 api_error 之间无 logger.info（**Low / 监控可视化**）

- 评估：异常路径已有 warning / exception 日志，无需补 info。**无 finding**。

#### Finding R3-NB-17：endpoints 缺 OpenAPI metadata（response_model / tags / description）（**Low**）

- 行号：所有 endpoint 装饰器
- 现状：未声明 `response_model` / `tags` / `description`。
- 评估：项目使用 `api_success` / `api_error` 自定义 envelope，FastAPI auto-doc 价值有限。**与项目风格一致**，无 finding。

### B.3 R2 改动周边新暴露面

#### B.3.1 `_PARAM_KEY_PATTERN` 引入后是否影响其他 endpoint？

- 仅在 `_validate_param_values` 内调用，仅 `webui_commands_api_update`（PATCH `/webui/api/commands/{command_key}`）路径调用。
- alias endpoint 不走 param_values。
- **无影响扩散**。

#### B.3.2 enabled=null 拒绝后，client 重试语义？

- 前端 `commands.js:485-510`：toggle 切换时永远传 `Boolean(nextEnabled)`，**不会传 null**。
- 422 路径只在恶意 / 手工 curl 触发，前端不暴露此路径。**无回归风险**。

#### B.3.3 _client_ip cross-module 引入后的耦合度

- `webui_commands.py:24` `from server.routes.webui import _client_ip`
- `webui_dashboard.py:9` 同样模式
- 已是 dashboard / commands 共同模式，**耦合度可接受**。
- 长期 backlog：`_client_ip` 可上提到 `server/routes/__init__.py`（与 `api_error` / `api_success` 同层），但属于重构 / **scope-out backlog**。

---

## 严重度统计

| 严重度 | 个数 | finding |
|---|---|---|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 0 | (R3-NB-10 标 Medium 但 scope-out backlog) |
| Low | 2 | L-B3（INFO 缺 user_agent，与基线一致不修）、L-B4（pattern 注释偏差） |
| Backlog (scope-out) | 3 | R3-NB-6 / R3-NB-10 / _client_ip 上提 |

## 结论

### R2 修复复审

| 项 | 评估 |
|---|---|
| M-B2 enabled=null 拒绝 | **PASS** |
| M-B3 _client_ip + 5 logger sites | **PASS** |
| M-B4 _PARAM_KEY_PATTERN 字符集 | **PASS**（带 L-B4 注释偏差 Low） |
| L-B2 alias strip 再算长度 | **PASS** |

### 全量再扫

- 0 Critical / 0 High / 0 Medium（在严格 scope 内）
- 2 Low（L-B3 一致性不修、L-B4 注释偏差，可选）
- 3 scope-out backlog（已声明）

### scope 守门记录

- **未扩散**到 `webui.py` / `command_config.py` / `api.js` / `__init__.py` / 其他 webui 模块。
- 跨模块发现（`_map_validation_error` 字符串耦合、`_client_ip` 上提、body size limit）均标 **scope-out backlog**，**不计入本任务严重度**。
- 单一审查文件：`server/routes/webui_commands.py`。

### 建议

后端桶在 commands 页面 R3 复审 **可声明收敛闭环**（0 Critical / 0 High / 0 Medium），L-B4 注释精度可选修，其余 backlog 已在 PRD 中明示，不在本任务行动项。
