# Re-Check After Fix — 服务器工具 / 服务器管理 审计修复复检

- **日期**: 2026-05-08
- **复检范围**: 21 条修复点（PRD 用户决定修部分）
- **复检方式**: git diff 全量阅读 + 关键行为/边界值人工推演 + helper 单测脚本验证
- **修改文件**:
  - `nextbot/db.py`（仅 `Server.__repr__`）
  - `nextbot/plugins/server_manager.py`
  - `nextbot/plugins/server_send.py`
  - `nextbot/plugins/server_tools.py`
  - `nextbot/text_utils.py`（新增 `at_prefix`）
  - `nextbot/tshock_api.py`（`request_server_api` 接受 `httpx.Timeout`）
  - `nextbot/server_validation.py`（NEW）
  - `server/routes/webui_servers.py`（改用 shared validator）

---

## 总体结论

**21 条修复全部落地，且无破坏性回归。** 关键能力（路径白名单、per-server 信号量、IntegrityError 显式回滚、超时分维度、Server.__repr__ token 屏蔽、共享校验 helper）实测有效。`shop.py / warehouse.py / lottery.py / leaderboard.py / user_manager.py / ban_core.py / ban.py / player_query.py / security.py / webui_users.py` 等其它 `request_server_api` 调用方未传 `timeout` 参数，全部沿用默认 `5.0` float → 新版本映射为 `httpx.Timeout(connect=5, read=5, write=10, pool=5)`，对 GET 小请求**无可观察行为差异**（写超时从 5s 提升到 10s 是空操作）。

`pyright`：基线 1 条 pre-existing 错误，本次修改后仍是 1 条（行号位移），**无新增类型错误**。

`ruff check`：基线 71 条 → 当前 100 条（+29 条均为风格类）。新增主要分布：`TRY003`（exception 文案过长）、`E501`（中文注释超 88 字符）、`ANN201/204`（异步 handler 缺返回标注）、`RUF100`（`# noqa: F841` 在新代码中无实际触发，标注冗余）。**无 F/B/SIM/PLW0603 等真正的 bug 类规则触发。**

下面分三档给出复检细节。

---

## 🔴 实现引入的 Bug

**无。**

---

## 🟠 修复不完整 / 不充分

**无关键修复缺位。** 以下两点是边缘观察，不影响交付，但值得记录：

### O-1. `download_map` OneBot 路径的 `file_uri` 局部变量未及时释放（与 ST-2.1/ST-3.3 相关）

- **位置**: `server_tools.py:379-399`
- **现象**: 进入 OneBot V11 上传分支后构造 `file_uri = f"base64://{b64}"`（一个新 110MB 字符串拼接），之后 `b64 = None` 和 `response.payload.pop("base64", None)` 释放原 base64，但 `file_uri` 仍持有新拼接出的等长字符串，需等到 `if bot.adapter.get_name() == "OneBot V11":` 分支整体出栈才被 GC。
- **影响**: 调用峰值内存仍是 ~2× base64 长度。配合 per-server semaphore（同 server 单并发）后，单服务器最多 220MB，多服务器并发时仍线性增长。已被 `_MAX_BASE64_BYTES = 200MB` 兜底，因此不会 OOM。
- **是否已合规**: 已满足 PRD「拿到后立刻 del」的要求（针对原 base64 引用），且实操不会 OOM；属于深度优化空间。
- **建议**: 在 `await bot.call_api(...)` 之后追加 `del file_uri`，把内存峰值压到 1×。**非阻塞。**

### O-2. `Server.__repr__` 屏蔽 `token` 但 SQLAlchemy `IntegrityError.str()` 仍包含完整 token 参数

- **位置**: `db.py:112-117`、`server_manager.py:82-92`、`webui_servers.py:136-143`
- **现象**: SQLAlchemy 抛 `IntegrityError` 时，`str(exc)` 形如 `[parameters: (1, 'TOKEN-VALUE-HERE')]`。`Server.__repr__` 只能屏蔽显式打印 server 对象的场景，不能拦截 `IntegrityError.__str__`。
- **本次实现的态势**:
  - `server_manager.py` 的 `except IntegrityError:` 分支只 `logger.warning(f"添加服务器失败：name=... attempted_id=... reason=ID 分配冲突")`，**未把 exc 文本写入日志**，token 不会泄漏 ✅。
  - `webui_servers.py` 的 `except Exception as exc: logger.exception(f"创建服务器异常：name=...，reason={exc}")` 是**预先存在的 pattern**（HEAD 已存在，本次未引入），且 `logger.exception` 会把 traceback（含 IntegrityError 的 parameters）写入日志。这是历史风险面，本次复用 `Server.__repr__` 不能消除。
- **是否阻断**: 不是本次新引入的问题，PRD 也未把它纳入 21 条修复列表。
- **建议**: 单独立项处理「web UI 异常路径屏蔽 SQL 参数」。**非本次范围。**

---

## 🟢 风格 / 质量改进

### Q-1. `# noqa: F841` 标注冗余（4 处）

- **位置**: `server_tools.py:287, 294, 397, 405`
- **现象**: 形如 `b64 = None  # noqa: F841`。Ruff 不会对「赋值后再次使用即被覆盖」触发 F841（F841 只对「赋值后未使用」触发），所以 `noqa: F841` 是无意义的，ruff `RUF100` 标记其冗余。
- **影响**: 仅风格噪音；功能正确。
- **建议**: 改成 `del b64`（语义更直接、无 noqa），或者直接删掉 `# noqa: F841`。

### Q-2. `_SAFE_WLD_NAME_RE` 上限注释与实际匹配长度不一致

- **位置**: `server_tools.py:46-47`
- **现象**: 注释写「长度 1-128」，但 `[\w\-.]{1,128}\.wld` 由于贪婪 + 后缀 `\.wld`，实际可匹配 1-132 字符。回归测试值 `'a' * 125 + '.wld'`（129 字符）会通过。
- **影响**: 仅文档准确性；无安全意义（后端可控的 fileName 经过 `Path(...).name` 已剥离路径，且 OneBot 仅作为显示名）。
- **建议**: 注释改为「name 部分 1-128，加 .wld 后缀」。

### Q-3. `_check_no_newline` 仅校验 `\n` `\r`，不含其它垂直空白

- **位置**: `server_validation.py:43-48`
- **现象**: `_FORBIDDEN_NAME_CHARS = ("\n", "\r")`。`\v` `\f` `\x1c` `\x1d` `\x1e` `\x85` ` ` ` ` 不在显式黑名单内，依赖 `_NAME_PATTERN`（限制 ASCII 字母数字 + `一-鿿` + 空格 + `._-`）兜底。`token` 字段无正则校验，理论上可写入 `\v`。
- **影响**: 实际威胁面极低：`/say` 走 `params={"cmd": ...}`，httpx 自动 percent-encode；token 仅用于 URL query，httpx 同样会 percent-encode。
- **建议**: 视为防御深度增强，可在 `_check_no_newline` 内追加 `\v\f` 或干脆 `if any(c.isspace() and c != ' ' for c in value)`。

### Q-4. `[INFO]` `[WARN]` `[ERROR]` 前缀依赖 nonebot.log 默认 formatter 自动输出

- **位置**: 所有 `logger.info/.warning/.exception` 调用
- **现状**: 项目沿用 nonebot 的 loguru wrapper，level 前缀由 framework 自动添加，符合 CLAUDE.md「不在业务消息文本里手写 level」的要求。
- **影响**: 合规。
- **建议**: 无。

### Q-5. ruff 风格类新增 29 条

- **类别分布**: TRY003 (8), E501 (22 含 db.py 多条 pre-existing), ANN201/204 (8), RUF001/003 (5), I001 (4), TC001 (1), PLR2004 (5), PLR0911/12/15 (3)
- **影响**: 不是 bug。属于团队风格门槛设定；如果 CI 要求 0 lint，可在 `pyproject.toml` 单独 ignore 或拆模块。
- **建议**: 与现有 ruff 基线一致即可。

---

## 21 条修复点逐项核对

| # | ID | 文件:行 | 实施状态 | 复核结论 |
|---|---|---|---|---|
| 1 | ST-3.1 | `server_tools.py:100-112` | ✅ | `_safe_wld_name` 实测拦截 `/etc/passwd` / `../etc/passwd.wld` / 空串 / 中文 / null byte / 换行；不匹配回落 `world-{server_id}.wld`；fallback `tempfile.NamedTemporaryFile` 完全绕开后端控制路径 |
| 2 | ST-2.1+ST-3.3 | `server_tools.py:51-60, 250-289, 337-399` | ✅ | per-server `asyncio.Semaphore(1)`，map / download 各自独立池；`_MAX_BASE64_BYTES = 200MB` 上限；拿到后 `pop("base64")` + `b64=None` |
| 3 | SM-1.1+SM-1.2+SM-3.1 | `server_manager.py:66-94` | ✅ | `func.max(Server.id)+1`；`except IntegrityError` 显式 `rollback` + `logger.warning` + `reply_failure("添加", "ID 分配冲突，请重试")`；与 `webui_servers.py:119` 写法对齐 |
| 4 | ST-2.2+ST-3.4+ST-1.4+ST-4.3 | `tshock_api.py:48-73`, `server_tools.py:163-167, 254-258, 341-345`, `server_send.py:99-106` | ✅ | `request_server_api` 现在接受 `float \| httpx.Timeout`；map/download 传 `_LONG_READ_TIMEOUT`(read=300s)；execute 传 15s；send 传 10s；其他历史调用方使用 `5.0` 默认值，向后兼容 |
| 5 | ST-3.5 | `server_tools.py:100-112, 387, 394` | ✅ | OneBot upload 的 `name=safe_name` 已用白名单后值；非 wld 后缀直接回落 `world-{server_id}.wld` |
| 6 | ST-2.3 | `server_tools.py:42, 273-279, 361-367` | ✅ | base64 `len(b64) > 200MB` 直接 `reply_failure("查询/下载", "返回数据过大/文件过大")` + `logger.warning` |
| 7 | ST-2.4 | `server_tools.py:115-123, 291-293` | ✅ | 非 V11 适配器走 `_safe_display_file_name`，仅展示白名单内 ASCII 文件名 |
| 8 | ST-3.6 | `server_tools.py:415-425` | ✅ | fallback 不再回显 `/tmp` 路径，仅展示 `safe_name` + size_kb |
| 9 | ST-3.7 | `server_tools.py:416-425` | ✅ | 走 `reply_block(reply_success("下载"), [...])` 与其它命令一致 |
| 10 | ST-4.2 | `server_send.py:31, 73-75` | ✅ | `_MAX_CONTENT_LENGTH=200`，**whitespace-collapse 之后**判断长度，超过即 `reply_failure("发送", "内容过长")` |
| 11 | ST-5.3 | `db.py:112-117` | ✅ | `__repr__` 把 token 置 `***`；其他字段照常显示。注：未覆盖 SQLAlchemy `IntegrityError.__str__` 的参数泄漏，但本次 IntegrityError handler 不写 exc 文本，安全 |
| 12 | ST-5.4 | `text_utils.py:84-96`, 三个 plugin 全局替换 | ✅ | `at_prefix(event, content, sep=" "/"\n")` helper；plugin 内 `OBV11MessageSegment.at(...) +` pattern 已 0 处遗留 |
| 13 | SM-1.3+SM-1.5 | `server_validation.py`（NEW），`server_manager.py:54-64`, `webui_servers.py:11-15, 44-46` | ✅ | name/ip/port/token 一致校验 + 显式拒绝换行；webui 与 bot 共用同一真源 |
| 14 | ST-1.3 | `server_tools.py:84-86` | ✅ | `_parse_execute_arg_text` 拒绝不以 `/` 开头的命令，触发 usage 提示 |
| 15 | ST-3.8 | `server_tools.py:373-377` | ✅ | 日志补 `user_id={user_id} group_id={group_id} size_kb={size_kb}`；私聊 group_id=0 |
| 16 | ST-4.5 | `server_send.py:113`, `server_tools.py:175` | ✅ | 已去掉 `f""` 包裹 |
| 17 | ST-5.5 | `server_tools.py:78, 237, 321`, `server_send.py:46` | ✅ | `_parse_execute_arg_text` / `_parse_send_arg_text` / `handle_map_image` / `handle_download_map` 全部拒绝 `server_id <= 0` |

---

## 关键防护点验证（spec 中 "Specific concerns to actively check"）

### 1. Semaphore 正确性 ✅

- `async with sem:` 包裹后所有 `return` 都在 with 块内，Python 保证 `__aexit__` 释放
- `_semaphore_for(pool, sid)` 是同步函数，不会被 asyncio 切片，无并发新建竞态
- `_map_semaphores` 与 `_download_semaphores` 互相独立，避免地图大请求阻塞下载（反之亦然）
- 字典只增不删；服务器数量有限（典型 <10），可忽略

### 2. Timeout API 向后兼容 ✅

- 全量 grep 所有 `request_server_api(...)` 调用方
- 默认 float 路径：`shop.py / warehouse.py / leaderboard.py / lottery.py / user_manager.py / ban.py / ban_core.py / player_query.py / security.py / webui_users.py / webui_servers.py:verify-nextbot(15.0)` 全部走原 `5.0` 或 `15.0` float → 映射为 `httpx.Timeout(connect=5, read=5/15, write=10, pool=5)`
- 写超时由 5s → 10s 是隐式松绑，对 GET 小请求无可观察影响
- 显式 `httpx.Timeout` 路径：仅 `server_tools.py:_LONG_READ_TIMEOUT`，正常工作

### 3. 路径遍历闭合 ✅

- `Path("/tmp") / "/etc/passwd"` 路径吞并已通过 `Path(raw).name` 截断为 basename
- `Path('../etc/passwd.wld').name = 'passwd.wld'` 已通过白名单正则放行（仅 ASCII + `.wld` 结尾），但只用于 OneBot 显示名 / 临时文件命名，不构成 RCE
- null byte / `\n` 在白名单正则下全部失配，回落到 `world-{server_id}.wld`
- fallback 路径完全改用 `tempfile.NamedTemporaryFile`，后端无法控制目标路径

### 4. 校验 helper 一致性 ✅

- `validate_server_payload`（位置参数，bot 用）与 `validate_server_payload_dict`（dict，webui 用）共享底层 `_normalize_*`
- 所有失败字段名 / 文案与历史 webui 路径完全一致：`服务器名称不能为空 / 服务器名称格式错误... / 服务器地址不能为空 / 端口必须是整数 / 端口范围必须在 1-65535 / Token 不能为空 / Token 长度必须在 1-128 之间`，加上新增的 `*** 不允许包含换行符`
- webui 422 响应体 `details=[{"field": exc.field, "message": exc.reason}]` 与历史 `str(exc)` 等价（`__init__` 内 `super().__init__(reason)`）

### 5. add server IntegrityError 处理 ✅

- `rollback` 在 `reply_failure` 之前
- 日志 `[WARNING] 添加服务器失败：name={name} reason=ID 分配冲突 attempted_id={new_id}` 不含 token / IP / 端口
- 无 traceback 写入日志（不是 `logger.exception`）

### 6. `Server.__repr__` token 屏蔽 ✅

- `repr(server)` 输出 `<Server id=1 name='X' ip='1.2.3.4' game_port='7777' restapi_port='7878' token=***>`
- `__str__` 未覆盖 → fallback `__repr__` → 同样安全
- 注意：SQLAlchemy `IntegrityError` 的 `[parameters: ...]` 仍含 token，但本次 handler 不写 exc 文本 → 不实际泄漏

### 7. at_prefix 全量迁移 ✅

- `grep -nE "OBV11MessageSegment\.at\(|at \+ \""` 在三个 plugin 内已 0 处遗留
- `OBV11MessageSegment` 在 `server_send.py / server_manager.py` 已删除 import；`server_tools.py` 仅保留用于 `OBV11MessageSegment.image(...)`（不可替代）

### 8. send content 长度上限时机 ✅

- `_parse_send_arg_text` 内先 `_WHITESPACE_RE.sub(" ", content).strip()` → handler 拿到的是已归一化的 content → `len(content) > _MAX_CONTENT_LENGTH` 判断的是归一化后字符数
- 攻击者无法用 `\n\n\n...` 把校验前长度刷到 200+ 字符然后被 collapse 成短串绕过

### 9. DB schema 无变更 ✅

- `db.py` 仅新增 `Server.__repr__` 方法，无字段 / 索引 / 表 / 默认值变化
- 无 `ensure_*_schema` 需要补
- 启动即可，无需迁移脚本

### 10. 日志格式合规 ✅

- 全部新增日志均为 key=value 风格：`server_id=` `user_id=` `group_id=` `size_kb=` `attempted_id=` `name=` `field=` `reason=`
- timestamp / level 由 nonebot.log（loguru）自动输出，业务消息不写 `[INFO]`
- 关键路径覆盖：execute / map / download / send / add / delete / list / test 八个 handler 全部有 `logger.info`，失败 / 异常路径有 `logger.warning`
- 无 print / 无 `print(..., flush=True)` / 无残留 debug log

---

## 必须保持不动的项（已确认未被改动）

| 项 | 文件:行 | 状态 |
|---|---|---|
| ST-1.1 命令回显（用户排除） | `server_tools.py:188` | ✅ 保留 `⚙️ 命令：{command}` |
| ST-2.5 失败动词「查询」 | `server_tools.py:247, 261, 265, 270, 278` | ✅ 仍是「查询」 |
| ST-2.6 全亮地图无 at | `server_tools.py:245-296` | ✅ 仍裸 `bot.send` |
| ST-4.1 /say 全角钓鱼（用户排除） | `server_send.py:93` | ✅ 仍 `f"/say {user.name}（{user_id}）：{content}"` |
| SM-1.4 name 唯一性 | `db.py:106` | ✅ 未加 UNIQUE 约束 |
| SM-3.2 测试错误信息 | `server_manager.py:266-267` | ✅ 仍透传 `get_error_reason` |
| SM-2.1 / SM-2.4 删除 renumber | `server_manager.py:152-154`, `webui_servers.py:208-211` | ✅ 仍 `update({Server.id: Server.id - 1})` |
| SM-4.1 renumber 锁表 | 同上 | ✅ |
| SM-4.2 PRAGMA foreign_keys | `db.py` engine 创建 | ✅ 未启用 |

---

## 验证小结

- **lint**: 新增 29 条全部为风格类（TRY003 / E501 / ANN / RUF100 / I001 / PLR），无 F / B / SIM / PLW0603 / 真正逻辑类违规。建议项目 ruff 设定与历史风格保持一致即可，本次不强制清零。
- **type**: pyright 输出 1 个 pre-existing 错误（webui_servers `Response` vs `JSONResponse`，与本次修改无关），无新增。
- **静态行为推演**: 21 条修复全部对应到具体代码段，均能按 PRD 目标生效；外部行为（成功路径输出、失败 reply 文案、日志结构）与历史一致或仅有有意收紧。
- **置信度**: 高 — 修复点准确、helper 设计合理、向后兼容性已逐项核对，未发现任何「修了但根因没变」或「引入新洞」的项。

---

## 推荐后续动作

1. （可选）把 `server_tools.py` 4 处 `b64 = None  # noqa: F841` 改为 `del b64`，干掉 ruff RUF100 噪音
2. （可选）把 `download_map` OneBot 路径的 `file_uri` 拼接后追加 `del file_uri`，把 base64 内存峰值压到 1×
3. （独立任务）`webui_servers.py` 的 `logger.exception(... reason={exc})` 在 IntegrityError 路径会泄漏 SQL 参数 / token，需要单独处理
4. （独立任务）SQLAlchemy IntegrityError 的 `[parameters: ...]` 全局脱敏 wrapper（影响所有 ORM 写入路径，非本次范围）
