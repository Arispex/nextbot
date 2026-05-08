# 修复后再检查报告（trellis-check）

**日期**: 2026-05-08
**复查范围**: 9 个修复模块的实际代码 vs `prd.md` 验收 + `security-{a,b}-findings.md` + `main-agent-recheck.md`

---

## 复查方法

逐文件 read + 静态推理 + 关键路径运行验证：

- `nextbot/server_broadcast.py`（NEW，75 行）
- `nextbot/ban_core.py`（rewrite，313 行）
- `nextbot/plugins/security.py`（rewrite，218 行）
- `nextbot/plugins/ban.py`（rewrite，313 行）
- `nextbot/plugins/group_member_notify.py`（caller 跟进）
- `nextbot/db.py`（验证 UNCHANGED — `git diff` 为空）
- `nextbot/large_image.py`（验证 `MAX_BASE64_BYTES`/`semaphore_for` 已被消费）
- `server/routes/webui_users.py`（验证未受影响 — 维持自有黑名单 fan-out 实现，符合 PRD「out of scope」）

实际验证：
- `python -c "from nextbot.server_broadcast import ..."` 导入并构造 `BroadcastOutcome` 成功（Generic NamedTuple 可用）
- 模拟跑 `broadcast` 让 fn 在不同 server 上 raise 不同异常 → 全部被 `_wrap` 吞回 ok=False，`asyncio.gather` 不爆。
- `git diff nextbot/db.py` 为空（无 schema 变化）
- `grep -n "ban.list\|security.login.confirm\|security.login.reject\|DEFAULT_GUEST_PERMISSIONS" nextbot/db.py` → 三个 guest 权限保留
- `grep -rn "logger.critical" nextbot/plugins/security.py nextbot/plugins/ban.py nextbot/ban_core.py` → 无（符合 PRD「服务端反向同步兜底，不打 CRITICAL」）

---

## 高优先级：Owner 保护无回归 ✅

> 用户特别要求确认：「if conditional UPDATE bypasses owner check, this is a critical bug.」

**结论：未回归。**

`apply_ban_to_db` (`ban_core.py:40-92`) 的执行顺序：

1. SELECT user
2. 若 `user is None` → return `not_found`（早 return，不进 UPDATE）
3. 若 `str(user.user_id) in get_owner_ids()` → `logger.warning` + return `owner_protected`（早 return，不进 UPDATE）
4. **唯有非 owner 才到达条件 UPDATE**
5. UPDATE WHERE `user_id == user_id AND is_banned == False` + commit
6. rowcount=0 → re-read 区分 `not_found` / `already_banned`；rowcount=1 → return `banned`

owner check 在 UPDATE 之前，未与条件 UPDATE 合并（合并后无法再表达 owner 语义，因为 SQLite 无 CHECK 约束依赖外部 config）。

**TOCTOU 分析**：在 SELECT 与 UPDATE 之间，owner 状态会变吗？`get_owner_ids()` 读取的是 `nonebot.get_driver().config.owner_id`（`access_control.py:71-73`），来自启动时的环境变量，运行时不变。零并发风险。

**用户输入伪造场景**：调用方传入 `user_id`，UPDATE 条件包含 `User.user_id == user_id`，与 SELECT 语义一致。即便有人尝试用 owner_id 调用，`if str(user.user_id) in get_owner_ids():` 早 return，根本到不了 UPDATE。

→ owner 保护回归点：✅ **不存在**。

---

## 9 个修复模块逐项验收

| # | 涉及 ID | 实施情况 | 结论 |
|---|---|---|---|
| 1 | SA-1.1+2.1+SB-1.3+3.4+SC-4.2 | `server_broadcast.broadcast` + `_wrap` 吞异常 + `semaphore_for` per-server。`security._broadcast_login_action` / `ban_core.sync_user_to_blacklist` / `ban_core.sync_user_blacklist_remove` 全部走该 helper。 | ✅ 完成 |
| 2 | SB-1.2+3.5 | `ban_core.py:152` `quote(user_name, safe="")`、`ban_core.py:222` 同样、`security.py:47` 同样。 | ✅ 完成 |
| 3 | SB-1.4+3.3 | `apply_ban_to_db` / `apply_unban_to_db` 都用 `update().where(...).values(...)` + `execute_rowcount`，rowcount=0 路径合理区分 not_found / already_banned / not_banned。 | ✅ 完成 |
| 4 | SB-2.2 | `ban.py:50` `_ban_list_semaphore = asyncio.Semaphore(2)`、`ban.py:215` 包裹整段截图 + `ban.py:232` `file_size * 4 // 3 > MAX_BASE64_BYTES` 上限 check。 | ✅ 完成 |
| 5 | SA-1.2+1.7 | `_handle_login_action` 三态区分（完全 / 部分 / 全失败），部分成功 ⚠️ + 失败明细 reply_block，全失败时单台沿用 `reply_failure` 单行、多台用 `全部 N 台服务器失败` head。 | ✅ 完成 |
| 6 | SA-1.3+2.4+SB-1.5+3.6+SC-4.5 | `security._log_results` 同时记录 `operator_id` + `target_user_id`；`ban.handle_ban` / `handle_unban` 日志补 `operator_id`；`ban_core.apply_ban_to_db` owner_protected 分支补 `logger.warning`。 | ✅ 完成 |
| 7 | SB-2.4 | `ban.py:170-189` 改 `count() + offset/limit`，`order_by(User.banned_at.asc())` 由 DB 处理，不再全表 ORM 物化。 | ✅ 完成 |
| 8 | SC-4.6 | 新增 `apply_unban_to_db` / `sync_user_blacklist_remove` / `format_blacklist_remove_lines`；`ban.handle_unban` 全部走 `ban_core`，删除原 ORM 直接操作 + 串行循环。 | ✅ 完成 |
| 9 | SB-3.1 | `apply_unban_to_db` 在 commit 前 `target_name = str(existing.name); target_qq = str(existing.user_id)`，commit 后再读 ORM 不会 lazy-load 出问题。 | ✅ 完成 |

---

## 用户决定不修的项 — 验证维持原状

| 项 | 状态 |
|---|---|
| SA-COMMON.1 / SB-2.1（`security.login.confirm` / `security.login.reject` / `ban.list` 默认 guest 权限） | ✅ `db.py:36/77/78` 保留，`git diff nextbot/db.py` 空 |
| SB-1.1 / SB-3.2 / SC-4.1（DB-API 双写无 CRITICAL log，无聚合层） | ✅ `grep "logger.critical"` 在 security/ban/ban_core 全部为零；`apply_ban_with_sync` 函数未实现，符合 PRD |

---

## 行为一致性检查

### ✅ 完全成功路径输出（byte-identical）

**允许登入 全成功**：
- 老：`at + " " + reply_success("允许", "可在 5 分钟内重新连接")`
- 新：`at + " " + reply_success(action, success_detail)` 其中 `action="允许"`, `success_detail="可在 5 分钟内重新连接"`
- → 文本输出完全一致

**拒绝登入 全成功**：
- 老：`at + " " + reply_success("拒绝")`
- 新：`reply_success(action, None)` → `"✅ 拒绝成功"`
- → 文本输出完全一致

**封禁用户 全成功**：
- `lines` 拼接：`reply_success("封禁")` + 用户行 + 原因行 + `format_blacklist_add_lines(outcomes)`
- 与老 `lines.extend(await sync_user_to_blacklist(...))` 在所有服务器都成功的情况下文本一致：每台都是 `{server.id}.{server.name}：✅ 添加成功` 或 `ℹ️ 已存在于黑名单中`，head 是 `🖥️ 同步服务器黑名单结果：`
- → 一致

**解封用户 全成功**：同上 ✓

### ⚠️ 部分成功路径输出（INTENTIONAL 行为变化，符合 Module 5 PRD）

**允许登入 / 拒绝登入 部分成功**：
- 老：`success_count > 0` 即视为成功，回 `✅ 允许成功，可在 5 分钟内重新连接`
- 新：`0 < success_count < total` 回 `⚠️ 允许部分成功（X/Y）` + 每台失败明细
- → 用户决定的破坏性变更，PRD 明确「Module 5 ⚠️ 部分 / ❌ 全失败」三态区分

封禁 / 解封部分成功：lines 中部分 ✅ 部分 ❌，老行为已经是逐行混合（外层标题始终是 `reply_success("封禁")` / `reply_success("解封")`）。新行为同样如此 — 没有针对部分成功的 ⚠️ 包装。

**↑ 这是与 security.py 的不对称，且符合 PRD：**
- Module 1 改 fan-out 模式
- Module 5 改的是 SA-1.2+1.7（仅限 security.py 的 confirm/reject）
- ban.py 的封禁/解封不在 Module 5 范围

→ 不需要修。

### ✅ 失败文案符合全局规范

- `reply_failure(action, reason)` 输出 `"❌ {action}失败，{reason}"`
- 没有出现「动作 + 结果，原因」拼接（如 `❌ 删除服务器成功`），全部是 `❌ {动作}失败，{原因}`
- 例如 `reply_failure("封禁", "未找到该用户")` → `"❌ 封禁失败，未找到该用户"`

→ 符合规范

### ✅ 日志风格统一

- 全部使用 `logger.info`/`logger.warning`，无 `logger.critical`（符合「服务端反向同步兜底」决策）
- key=value 格式：`operator_id=... target_user_id=... target_name=... reason=... success=X/Y` ✓
- 无 `[INFO]`/`[WARN]` 手写前缀（由 logger 框架统一前缀） ✓

---

## 🔴 发现的问题（无）

无 critical / high 级别 bug。

---

## 🟠 发现的问题（无）

无修复不完整或失效的 case。

---

## 🟢 改进建议（5 项，全部 low / info — 未自修，因属代码风格层）

### G-1 ℹ️ `requires-python = ">=3.10"` 与 Generic NamedTuple

`nextbot/server_broadcast.py:27` 使用 `class BroadcastOutcome(NamedTuple, Generic[R])`，此语法**仅 Python 3.11+ 支持**，3.10 会抛 `TypeError: can only inherit from a NamedTuple type and Generic`。

实际部署：`Dockerfile` `FROM python:3.11-slim-bookworm`，`.venv` 是 3.14.3 — 都满足 3.11+。但 `pyproject.toml` 声明 `requires-python = ">=3.10"`，理论上若有用户用 3.10 安装会启动失败。

**建议**：要么把 `requires-python` 收紧到 `>=3.11`，要么把 `BroadcastOutcome` 改成 `dataclass(slots=True)` 同时支持 Generic（3.10 OK）。

**未自修**：属于跨代码风格 + pyproject 元数据决策，需要用户确认 Python 最低版本目标。

### G-2 ℹ️ `success_count == total > 0` 时仍走 `aggregate` 但未利用 total==0 的早 return

`security.py:135-136` 先调用 `_broadcast_login_action(servers, ...)`，再 `aggregate(outcomes)`。在 `not servers` 已早 return 的前提下，`total >= 1` 恒成立。代码逻辑正确，仅注释/可读性视角，可在 `_handle_login_action` 内用 `assert total > 0` 让意图更显式。

**未自修**：纯可读性，不影响行为。

### G-3 ℹ️ `_ban_list_semaphore = asyncio.Semaphore(2)` 模块级 hard-code

`ban.py:50` 直接 `Semaphore(2)`。其他类似处（如 `large_image.semaphore_for`）允许从环境变量调整。

**未自修**：与 PRD 一致（PRD 仅要求加 semaphore + size check，未指定可配置）；符合 KISS。

### G-4 ℹ️ `_handle_login_action` 7 参数（PLR0913）

ruff 警告参数过多。可用 `dataclass` 或 keyword-only `**kwargs` 收拢。

**未自修**：纯风格，不影响正确性；与项目其它内部 helper 的多参数风格一致。

### G-5 ℹ️ `success_count == 0` 单台失败 + 多台失败的「No pending」混合行为

当所有服务器失败 + 所有失败原因都是 "No pending login request" → 回 `没有待处理的登入请求`。
当所有服务器失败 + 部分失败是 "No pending"、部分是网络 → 回 `❌ 允许失败，全部 N 台服务器失败` + 逐行明细（包含 "No pending"）。

老代码用 `_pick_failure_reason` 也会跳过 "No pending"，但只取一条非 "No pending" 的原因。新代码包含全部，对运维更有价值。

**未自修**：行为更优，无 regression。

---

## 验证结果

- **TypeCheck**：项目级 pyright 报 280 errors，全部是 `reportMissingImports`（pyright 未识别 nonebot 包），与本次改动无关。新增/改动文件无独立类型错误（`BroadcastOutcome[str]` 实例化、`aggregate` 返回 tuple、`apply_*_to_db` dataclass 字段都对得上）。
- **Lint**：项目基线 ruff 1101 errors（全是 Chinese 字符 RUF001 / 长行 E501 / TC003 等），本次改动新增 8 个错误（仅在 `server_broadcast.py`），全部是 Chinese 字符长行 / type-checking import — 与项目历史风格一致，未引入新型警告。
- **Runtime smoke**：`python -c "from nextbot.server_broadcast import broadcast; ..."` 实际跑 `broadcast` + 模拟异常路径 → 全部按预期吞异常返回 ok=False。

---

## 结论

**信心：高（>95%）**

9 个修复模块全部如 PRD 要求落地：
- 无 owner 保护回归
- 无 DB schema 变化（`git diff nextbot/db.py` 空）
- 无 critical log 误打（符合「服务端反向同步兜底」决策）
- 无破坏性 API 变化（`webui_users.py` 仍可调用旧名字 — 但实际它没用 `sync_user_to_blacklist`，自有实现，无需跟进）
- 失败文案、日志格式、key=value 字段、`operator_id` 全部到位
- 部分成功 ⚠️ 三态区分仅作用于 security.py（用户明确要求范围），ban.py 维持原行为

剩余的 5 个低优先级建议（G-1 ~ G-5）属于代码风格 / 元数据层，不阻塞合并。
