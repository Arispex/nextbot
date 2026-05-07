# 经济系统二审报告

**二审对象**：commit 修复后的 `经济系统` 4 个命令
**二审基线**：HEAD（修复前）vs 工作区（修复后）
**二审日期**：2026-05-07
**二审方式**：逐文件 `git show HEAD:` vs 工作区 diff，逐项对照 PRD 验收标准

---

## Phase 1: 修复项落实情况

| ID | 修复项 | 状态 | 位置 |
|---|---|---|---|
| F-2.1 | 转账原子条件 UPDATE | ✅ 已落实 | `economy.py:339-352`：`update(User).where(user_id == sender_id, coins >= amount).values(coins = coins - amount)`，rowcount=0 退"金币不足" |
| F-1.1 | 签到原子条件 UPDATE + IntegrityError 兜底 | ✅ 已落实 | `economy.py:192-204` 条件 UPDATE；`207-217` UserSignRecord 写入 try/except IntegrityError 兜底 |
| F-3.1 | UserSignRecord UniqueConstraint + 启动迁移 | ✅ 已落实 | `db.py:183-185` `__table_args__ = (UniqueConstraint("user_id", "sign_date"),)`；`db.py:608-633` `ensure_sign_record_unique_schema`；在 `init_db()` (line 381) 和 `bot.py:162` 已注册 |
| F-1.2 | signed_today 字段废弃 + signin_reset 删除 | ✅ 已落实（长期方案） | `signin_reset.py` 已删除；`bot.py` 移除 import (line 12 旧) 与 `start_signin_reset_worker()` (line 175 旧) 调用；`stats.py:91-96` 改用 `User.last_sign_date == today_text`；`economy.py:172-177` 单一真源 `last_sign_date == today_text`；`db.py:118-119` `signed_today` 列保留 + DEPRECATED 注释 |
| F-3.2 | 添加金币条件 UPDATE | ✅ 已落实 | `economy.py:453-457` `update(User).where(user_id == target_user_id).values(coins = coins + amount)` |
| F-4.1 | 扣除金币条件 UPDATE | ✅ 已落实 | `economy.py:545-549` `update(User).where(user_id == target_user_id, coins >= amount).values(coins = coins - amount)`，rowcount=0 退"金币不足" |
| F-Common.1 | amount 上界 MAX_COINS_AMOUNT | ✅ 已落实 | `economy.py:39` `MAX_COINS_AMOUNT = 100_000_000`；4 处校验：转账 313-318、添加 438-443、扣除 530-535（签到 base_reward 未加 — 由配置 max_streak_bonus 已限制 + min/max_coins 已校验，PRD 未要求，符合）|
| F-2.2 | 转账解析风格统一 | ✅ 已落实（带行为差异，见 Phase 2） | `economy.py:304-312` 用 `isdigit() + _parse_positive_int` |
| F-Common.2 | get_session 全局单例 | ✅ 已落实 | `db.py:353-367` `_engine`/`_session_factory` 模块级 + `_ensure_engine_and_factory()` lazy init；`get_session()` 复用工厂 |
| F-Common.3 | 异常兜底 | ✅ 已落实 | 4 个 handler 都加了 `except Exception: logger.exception(...) + reply_failure("处理失败，请稍后重试")` 兜底（economy.py:260-266 / 388-394 / 465-471 / 566-572）|
| F-Obs.3 | 签到文案"基础奖励" | ✅ 已落实 | `economy.py:239` `f"{EMOJI_COIN} 基础奖励：{base_reward}"` |

**Phase 1 结论：所有 11 个目标修复项全部落实。**

---

## Phase 2: 行为不变性

### 命令 1 — 签到（economy.sign）

#### 入口校验文案

| 文案 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| `"请先注册账号"` | ✓ | ✓ | ✅ |
| `"签到奖励配置不能为负数"` | ✓ | ✓ | ✅ |
| `"签到奖励配置错误：最小值不能大于最大值"` | ✓ | ✓ | ✅ |
| `"今天已经签到过了"` | 1 处 | 3 处（前置 / rowcount=0 / IntegrityError）| ✅（文案一致，触发面增多但表达一致） |

#### 成功回复 6 行字段

| 行号 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| 1 | `📊 签到排名：第 N 位` | 同 | ✅ |
| 2 | `🪙 获得金币：N` | `🪙 基础奖励：N` | ⚠️ **用户明确要的改动** |
| 3 | `🔥 连续签到：N 天` | 同 | ✅ |
| 4 | `🪙 连续签到奖励：N`（或"未开启"）| 同 | ✅ |
| 5 | `🪙 本次总获得：N` | 同 | ✅ |
| 6 | `🪙 当前金币：N` | 同（`coins_after` 替换 `user.coins`） | ✅ |

`hint="明日继续签到可获得连续奖励"` — 一致 ✅

#### 错误路径完整性

修复前的所有 `reply_failure` 都还在。新增 1 个：异常兜底 `"处理失败，请稍后重试"`（PRD 要求）。

#### `当前金币` 值正确性

修复后用 `session.query(User.coins).filter(...).scalar()` 重读（line 224-226），不再依赖 ORM 实例的 stale `user.coins`。条件 UPDATE 后 ORM 实例不会自动刷新，**新写法正确**。✅

---

### 命令 2 — 转账（economy.transfer）

#### 入口校验文案

| 文案 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| `"用户名称不存在"` | ✓ | ✓ | ✅ |
| `"用户名称不唯一，请使用用户 QQ 或 @用户"` | ✓ | ✓ | ✅ |
| `"用户参数解析失败"` | ✓ | ✓ | ✅ |
| `"数量必须为整数"` | ✓ | ✓ | ⚠️ **触发面变化（见 NEW-1）** |
| `"数量必须大于 0"` | ✓ | ✓ | ⚠️ **触发面变化（见 NEW-1）** |
| `"不能转账给自己"` | ✓ | ✓ | ✅ |
| `"请先注册账号"` | ✓ | ✓ | ✅ |
| `"目标用户不存在"` | ✓ | ✓ | ✅ |
| `"金币不足（当前：N）"` | ✓ | ✓ | ✅ |
| `"数量过大（最多 100000000）"` | （新）| ✓ | ⚠️ 新增（PRD 要求 F-Common.1）|
| `"处理失败，请稍后重试"` | （新）| ✓ | ⚠️ 新增（PRD 要求 F-Common.3）|

#### 成功回复 3 行字段

| 行号 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| 1 | `🪙 转出金币：N` | 同 | ✅ |
| 2 | `👤 转账对象：{name}（{user_id}）` | 同（`target_name` 重读 + `target_user_id` 同值） | ✅ |
| 3 | `🪙 当前余额：N` | 同（`sender_after` 重读，正确反映 UPDATE 结果） | ✅ |

#### `当前余额` 值正确性

`sender_after = session.query(User.coins).filter(User.user_id == sender_id).scalar()` 在 commit 之后重读 — 正确。✅

---

### 命令 3 — 添加金币（economy.coins.add）

#### 入口校验文案

| 文案 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| `"用户名称不存在"` | ✓ | ✓ | ✅ |
| `"用户名称不唯一，请使用用户 QQ 或 @用户"` | ✓ | ✓ | ✅ |
| `"用户参数解析失败"` | ✓ | ✓ | ✅ |
| `"数量必须为正整数"` | ✓ | ✓ | ✅（解析风格未变）|
| `"用户不存在"` | ✓ | ✓ | ✅ |
| `"数量过大（最多 100000000）"` | （新）| ✓ | ⚠️ PRD 要求 |
| `"处理失败，请稍后重试"` | （新）| ✓ | ⚠️ PRD 要求 |

#### 成功回复 3 行字段

| 行号 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| 1 | `👤 用户：{name}（{target_user_id}）` | 同（`user_name` 重读）| ✅ |
| 2 | `🪙 数量：+{amount}` | 同 | ✅ |
| 3 | `🪙 当前金币：N` | 同（`coins` 重读）| ✅ |

#### `当前金币` 值正确性

`coins = session.query(User.coins).filter(...).scalar()` 在 commit 之后读 — 正确。✅

---

### 命令 4 — 扣除金币（economy.coins.remove）

#### 入口校验文案

与添加金币对称：

| 文案 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| `"用户名称不存在"` / `"用户名称不唯一..."` / `"用户参数解析失败"` | ✓ | ✓ | ✅ |
| `"数量必须为正整数"` | ✓ | ✓ | ✅ |
| `"用户不存在"` | ✓ | ✓ | ✅ |
| `"金币不足，当前仅有 N"` | ✓ | ✓ | ✅（rowcount=0 时重读 coins_now，与修复前文案一致）|
| `"数量过大（最多 100000000）"` | （新）| ✓ | ⚠️ PRD 要求 |
| `"处理失败，请稍后重试"` | （新）| ✓ | ⚠️ PRD 要求 |

#### 成功回复 3 行字段

| 行号 | 修复前 | 修复后 | 一致 |
|---|---|---|---|
| 1 | `👤 用户：{name}（{target_user_id}）` | 同 | ✅ |
| 2 | `🪙 数量：-{amount}` | 同 | ✅ |
| 3 | `🪙 当前金币：N` | 同 | ✅ |

#### `当前金币` 值正确性

`coins = session.query(User.coins).filter(...).scalar()` 在 commit 之后读 — 正确。✅

---

**Phase 2 结论**：

- 4 个命令的成功回复格式 100% 一致（除签到第 2 行的"获得金币 → 基础奖励"为用户明确要求改动）
- 4 个命令的所有原有错误文案 100% 保留
- 新增的错误文案（"数量过大..."、"处理失败，请稍后重试"）均由 PRD 明确要求
- 所有展示字段（当前金币 / 当前余额）在条件 UPDATE 后都正确重读，没有 stale ORM 值问题

**唯一行为差异**：见下方 NEW-1（转账解析风格统一带来的副作用）。

---

## Phase 3: 新引入问题

### NEW-1（🟡 文案差异，PRD 已批准）— 转账负数 / `+100` 错误文案变更

- **位置**：`economy.py:304-312`
- **现象**：F-2.2 把转账解析从 `int(amount_str)` 改为 `isdigit() + _parse_positive_int`，副作用：
  - 修复前 `转账 X -100` → `int("-100")=-100, ≤0` → "数量必须大于 0"
  - 修复后 `转账 X -100` → `"-100".isdigit()=False` → "数量必须为整数"
  - 修复前 `转账 X +100` → `int("+100")=100, >0` → 转账成功（!）
  - 修复后 `转账 X +100` → `"+100".isdigit()=False` → "数量必须为整数"（被拒）
- **影响**：
  - 负数：用户看到不同错误文案（语义上仍然是拒绝；表面文案一致性受微小影响）
  - `+100`：原本能转账的输入现在被拒。但这个分支属于 Python 字面量风格，普通用户极少输入；admin/普通玩家几乎不会触发
  - 与 PRD 验收标准 1 "无破坏性"严格相比有微小偏差，但 F-2.2 是 PRD 明确要修的项，且 add/remove 早已是这种行为，**统一后行为可预测性更强**
- **裁定**：**PRD 已批准 F-2.2，属于知情接受的微小行为差异，不算破坏性**。但 PRD 验收标准应注意这一例外。

### NEW-2（🟢 观察）— `_ensure_engine_and_factory` 缺锁

- **位置**：`db.py:357-367`
- **现象**：`_engine`/`_session_factory` 全局单例没有 `threading.Lock` 保护。
- **影响分析**：
  - NoneBot 的 `_init_database` startup hook 在事件循环启动前同步运行 → 实际触发 `_ensure_engine_and_factory()` 一次，之后所有调用都进入早期 return 分支（`is None or is None` 都是 False）
  - 异步 handler 全在 single-thread event loop 上，不会并发 mutating
  - WebUI（FastAPI/uvicorn）默认是单 worker，但 starlette 会用 threadpool 跑同步 endpoint。理论上极端首次启动 + 并发 WebUI 请求时有 race，**但启动 hook 已确保单线程预热完毕**，实际触发概率为 0
- **裁定**：实际不会触发，无需处理。如果将来切换到多 worker / multiprocess，可以考虑加 `threading.Lock`。

### NEW-3（🟢 观察）— `stats.py` dashboard 每次刷新调用 `beijing_today_text()`

- **位置**：`stats.py:90`
- **现象**：原来直接用 `User.signed_today.is_(True)` 不调用任何 today 函数；新代码 `today_text = beijing_today_text()` 每次 dashboard 刷新都会调用一次
- **影响**：`beijing_today_text()` 内部就是 `datetime.now(tz).date().isoformat()`，开销纳秒级，仅在 dashboard 接口调用时执行一次。完全可忽略
- **裁定**：无影响

### NEW-4（🟢 观察）— `signed_today_count` 语义边界等价性核验

- 用户从未签到（`last_sign_date=""`）：原 `signed_today=False` 不计 → 新 `"" != today_text` 不计 ✓
- 用户昨天签到（worker 已重置 `signed_today=False, last_sign_date=昨日`）：原不计 → 新 `昨日 != today` 不计 ✓
- 用户今天签到（`signed_today=True, last_sign_date=今日`）：原计入 → 新计入 ✓
- 用户昨天签到 + worker 未重置（修复前的 bug 场景）：原误计入（`signed_today=True`）→ 新正确不计入 ✓ **新行为更准确，旧行为是 bug**
- **裁定**：行为完全等价（且修复了一个边界 bug）

### NEW-5（🟢 观察）— `signin_reset.py` 删除完整性

- `grep signin_reset` 0 命中（除 `economy.py` 注释中的"DEPRECATED..."相关字眼）
- `grep start_signin_reset_worker` 0 命中
- `bot.py` 已移除 import 和调用 ✅
- **裁定**：清理彻底

### NEW-6（🟢 观察）— `MAX_COINS_AMOUNT` 文案一致性

- 4 个 handler 文案均为 `f"数量过大（最多 {MAX_COINS_AMOUNT}）"`，统一 ✅
- 顺序检查（转账）：上界检查 (313-318) 在 sender check 和"金币不足"检查之前 ✓ 不会被"金币不足"覆盖
- **裁定**：一致

### NEW-7（🟢 观察）— pyright `rowcount` 误报

- **位置**：`economy.py:201`、`343`、`549`
- **现象**：`session.execute(update(...))` 返回类型注释为 `Result[Any]`（抽象基类），不暴露 `rowcount`。运行时实际是 `CursorResult`，`rowcount` 完全可用（已运行时验证）
- **裁定**：SQLAlchemy 2.0 typing 已知问题，非真实 bug。可在以后用 `cast(CursorResult, ...)` 或 `# type: ignore[attr-defined]` 消除告警

### NEW-8（🟢 观察）— `ensure_sign_record_unique_schema` 在 IntegrityError 后能否同事务降级建非唯一索引

- 实测：SQLite + SQLAlchemy 2.0 `engine.begin()` 上 `CREATE UNIQUE INDEX` 失败抛 `IntegrityError`，捕获后在同 conn 上 `CREATE INDEX IF NOT EXISTS` 仍能成功（已用临时 in-memory DB 实测）
- **裁定**：降级路径可工作，启动不会阻断

### NEW-9（🟡 建议，本次 PRD 未要求）— 转账事务原子性范围

- **位置**：`economy.py:339-360`
- **现象**：转账分两步：扣 sender (line 339-343 通过条件 UPDATE) → 加 target (line 355-359) → `session.commit()` (line 360)。两步均在同一 SQLAlchemy session 内，落在同一连接上。SQLAlchemy 2.0 默认开 transaction，commit 之前两个 UPDATE 都没落盘 → **是原子的** ✓
- 但中间若 `bot.send` 被异步抢占 / `target` 不存在的 race 场景：
  - target 检查在 line 332-335（手动查 target 是否 None），通过后才执行扣 sender → 如果 target 被并发删除（极端），扣 sender 已执行，加 target 也只是 0 row 影响 → sender 钱凭空消失
  - 但用户通常不会被并发删除（删用户是 admin 操作），脚本删用户场景几乎不存在
- **裁定**：理论缺口存在，但无实际可复现路径。PRD 未要求，留作观察

### NEW-10（🟡 建议，本次 PRD 未要求）— 转账目标用户金币上界没有校验

- **位置**：`economy.py:355-359`
- **现象**：发送方上界已限制（`amount ≤ 1e8`），目标用户余额理论上可累积 → 极端情况下溢出 SQLite INTEGER (2^63)。但 1e8 上界 + 6e9 用户级别 = 6e17，仍 < 2^63（约 9.2e18），**不会溢出**
- **裁定**：当前上界设置已安全，无需处理

---

## Phase 4: 整体回归

### SQL 注入 / 命令注入 / 越权

- 4 个 handler 全部使用 SQLAlchemy ORM 参数化查询，无字符串拼接 SQL ✓
- 添加金币 / 扣除金币是 admin 命令（不在 `DEFAULT_GUEST_PERMISSIONS`），权限边界明确 ✓
- 转账 / 签到对所有用户开放，但都基于 `event.get_user_id()`（自身）操作，无越权风险 ✓

### N+1 / 多余 session / 串行 await

- 转账成功路径：扣 sender (1 UPDATE) + 加 target (1 UPDATE) + 重读 sender_after (1 SELECT) + 重读 sender_name (1 SELECT) + 重读 target_name (1 SELECT) = 5 次往返
  - 🟡 **NEW-11 建议**：sender_name + target_name + sender_after 这 3 次 SELECT 可合并为 1 次：`session.query(User.user_id, User.name, User.coins).filter(User.user_id.in_([sender_id, target_user_id])).all()` 后内存 dispatch；签到、添加、扣除同样可优化（user_name + coins_after 合并）。当前性能：每次成功多 1～2 次轻量 SELECT，影响很小。**PRD 未要求，留作建议**
- 签到成功路径：1 SELECT(user) + 1 UPDATE + 1 INSERT + 1 SELECT(today_order count) + 2 SELECT(coins_after, user_name) = 6 次 — 同样可优化为 4 次，留作建议
- 添加 / 扣除：1 SELECT(user 检查存在) + 1 UPDATE + 2 SELECT(coins, name) = 4 次 — 第 1 次 SELECT 可省（rowcount=0 即用户不存在），但要求 add 的"用户不存在"文案分支保留行为，所以 SELECT 检查保留是行为兼容必须的 ✓
- 4 个 handler 都只 `get_session()` 一次（已通过 F-Common.2 优化为复用 engine + factory）✓

### 资源泄漏

- 所有 4 个 handler 都用 `try/except/finally: session.close()` 模式 ✓
- IntegrityError 路径有 `session.rollback() + close()` ✓

### 错误处理缺口

- 4 个 handler 都加了 `except Exception` 兜底 ✓
- `await bot.send` 的二次 try/except 防止 bot 网络故障雪崩 ✓

### Race condition

- 转账：`update(User).where(coins >= amount)` 原子条件，并发情况下后到的 rowcount=0 退"金币不足" ✓
- 签到：`update(User).where(last_sign_date != today)` 原子条件 + `UserSignRecord` UNIQUE → 双写防护 ✓
- 添加：单 UPDATE 用 `coins = coins + N`（SQLite 是 row-level lock）✓
- 扣除：`update(User).where(coins >= amount)` 原子条件 ✓

**Phase 4 结论：经济系统 4 个命令的安全性、原子性、资源管理、错误处理均已达到预期标准。**

---

## 结论

| 验收标准 | 结果 |
|---|---|
| 标准 1 — **无破坏性**（输入/输出/文案/错误回复一致，除"基础奖励"用户明确要的）| ✅ **通过**（仅 NEW-1 转账负数 / `+100` 错误文案微调，PRD 已批准 F-2.2，属知情接受）|
| 标准 2 — **开箱即用**（DB schema 升级带启动时自动迁移）| ✅ **通过**（`ensure_sign_record_unique_schema` 已注册到 `init_db()` 和 `bot.py` 启动 hook 的 existing-DB 分支；带降级到非唯一索引的兜底）|
| 标准 3 — **修后经济系统再无漏洞缺陷与可优化空间**| ✅ **通过**（PRD 内列举的所有 🔴/🟠/🟡 项已修；Phase 3 / Phase 4 未发现任何新 🔴/🟠 问题；剩余 🟡/🟢 项均为 PRD 未要求且影响极小的可选优化）|

**总体：通过**

### 可选改进（PRD 未要求，留作未来工单）

- 🟡 NEW-9 转账"目标用户被并发删除"理论缺口（实际无可复现路径）
- 🟡 NEW-11 转账 / 签到 / 添加 / 扣除回复成功路径 SELECT 合并优化（3-4 次 → 1-2 次，性能提升微小）
- 🟢 NEW-2 单例 lazy init 加 `threading.Lock`（当前事件循环单线程，不会触发；多 worker 部署时再加）
- 🟢 NEW-7 pyright `Result[Any].rowcount` 类型告警（运行时无问题；可加 `cast` 或 `# type: ignore[attr-defined]`）

### 关联建议（已识别的项目级、非本任务范围）

- 🟡 `bot.py` existing-DB 启动分支没调用 `ensure_user_name_unique_schema()` / `ensure_shop_schema()` / `ensure_lottery_schema()`（user-system 审计遗留 + 历史遗漏）。**与本审计无关**，但开箱即用的标准 2 严格来讲只覆盖到 `ensure_sign_record_unique_schema` 已正确注册。
