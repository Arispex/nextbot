# Round 4 Residual Sweep — 边角 / 老代码 / 偏门 path

- **Query**: Round 4 sweep — 找前 3 轮 sweep 都漏掉的边角问题
- **Scope**: internal（全 codebase grep + 重读关键 mutation handler）
- **Date**: 2026-05-09
- **预期发现量**: 低，但如果真 0 critical/high 才说明系统稳

## 总览

| Severity | 数量 |
|---|---|
| 🔴 Critical | 0 |
| 🟠 High | 0 |
| 🟡 Medium | 2 |
| 🟢 Low | 4 |
| ℹ️ Info | 3 |
| 扫描通过 | 5 |

**结论**：本轮无 critical/high。系统进入收敛区，剩余条目均为可接受 trade-off 或长尾 hardening。

---

## Checklist 1: ORM dirty-set vs 条件 UPDATE 重 grep

**扫描通过**：

- 全 codebase 仅剩 6 处 `xxx.<col> = <value>` ORM 直接赋值：
  - `nextbot/plugins/warehouse.py:688` (remove `item.quantity = current_qty - remove_qty`)
  - `nextbot/plugins/warehouse.py:880` (drop)
  - `nextbot/plugins/warehouse.py:1097` (recycle)
  - `nextbot/plugins/warehouse.py:1483` (claim)
  - `nextbot/plugins/warehouse.py:1813` (gift)
  - `nextbot/plugins/red_packet.py:381` (`claim.amount = applied_amount`)
- 5 个 warehouse mutation 全部位于 `async with warehouse_lock(target_user_id):` 内（`warehouse.py:638 / 830 / 1036 / 1403 / 1746` per-user lock 包住 try 块），lock 串行化 + ORM dirty-set + 同 session.commit() 模式安全
- red_packet `claim.amount` 是 `_claim_slot_atomic` 之后 `session.flush()` 完成的同事务行（`red_packet.py:336`），同 commit 原子化，安全

**未发现**：跨域 `User.X = Y` / `Group.X = Y` / `RedPacket.X = Y` / `LotteryPool.X = Y` 类型的 dirty-set 逃逸出 `warehouse_lock` 或不带条件 UPDATE 的位置。`user_manager.py` 名称变更已用 condition UPDATE。

---

## Checklist 2: try/except 块的 rollback 完整性

### 🟡 R4R-2.1：经济相关 handler `except Exception` 缺显式 `session.rollback()`

- **Severity**: 🟡 Medium
- **Files**:
  - `nextbot/plugins/economy.py:582-588`（转账 except）
  - `nextbot/plugins/economy.py:658-664`（添加金币 except）
  - `nextbot/plugins/economy.py:783-789`（扣除金币 except）
  - `nextbot/plugins/dice.py:240-246`（掷骰子 except）
  - `nextbot/plugins/red_packet.py:393-399`（抢红包 except）
  - `nextbot/plugins/red_packet.py:506-512`（收回红包 except）
  - 还有约 6 处类似 BLE001 except 块
- **Impact**：捕获到非 IntegrityError 的通用 Exception 时，未调 `session.rollback()`，仅依赖 `finally: session.close()` 触发隐式 rollback
- **复现**：在 `add_coins_with_cap` 之后、`session.commit()` 之前抛 RuntimeError → DB 状态未显式回滚，依赖 SQLAlchemy 的 `Session.close()` 自动 rollback unflushed changes
- **风险评估**：低，SQLAlchemy 文档明确 `close()` on dirty session 会先 rollback；但显式优于隐式，且违反项目内其他 handler 的统一风格（如 `user_manager.py:223 IntegrityError + rollback + reply`）
- **修法**：在每个 `except Exception:` 第一行加 `session.rollback()`（与 `economy.py:393-394` IntegrityError 分支风格统一）
- **PRD 影响**：本类问题前 3 轮未列入清单，属于 Round 4 新增 finding

### 🟢 R4R-2.2：47 个 `session.commit()` 中仅 15 个有 inner `try: ... except: rollback`

- **Severity**: 🟢 Low
- **Files**: 跨 handler 普遍现象
- **Impact**：commit 本身可能因 OperationalError / DataError 抛异常（连接断开、磁盘满），未被 inner try 包裹的 commit 直接传给外层 BLE001 except，触发 R4R-2.1 路径
- **复现**：commit 时 SQLite 进程被 SIGKILL → OperationalError → 走外层 except 但无 rollback
- **修法**：参考 `user_manager.py:523-531` 风格，用 inner `try: session.commit() except IntegrityError: session.rollback()` 显式处理；或在 helper 包一层
- **PRD 影响**：与 R4R-2.1 同根因

---

## Checklist 3: bot.send 后 finally / 错误路径

**扫描通过**：

- 所有 success-path 的 `await bot.send` 都在 `finally: session.close()` 之外（commit 后）
- 经济相关 handler 的 success path send 均位于 `try` 块内，但若失败：
  - `economy.py:432-446` 签到：bot.send 异常 → except → 尝试发 reply_failure（pass on second fail）→ DB 已 commit
  - 用户看到"处理失败"但 DB 真正成功；用户重试 → `IntegrityError` (`UserSignRecord` UniqueConstraint) 兜底重复签到
- 这是 **chat-bot 架构的固有 limitation**（无 2PC / outbox），项目已经做到 best-effort
- 无 high+ 项

**已知 acceptable trade-off**：
- 商店 / 抽奖 / 红包成功路径 commit 后才 send → bot.send 失败 = 用户没收到结果但 DB 已变更，统一靠后续命令查询补救（"金币" / "仓库"）

---

## Checklist 4: `get_session()` 生命周期

**扫描通过**：

- 137 处 `get_session()` 调用，全部紧跟 `try:` + `finally: session.close()`
- `command_config.py:627 / 700 / 786` 三个 sync helper 模式一致，已加 `session.rollback()` on `CommandConfigValidationError`（`command_config.py:686-688`）
- `lottery._charge_atomic` 内部 `session_local = get_session()`（`lottery.py:687`）在 try/finally 内，独立生命周期，与外层 handler 的 session 不冲突
- 无嵌套 session 泄漏

---

## Checklist 5: `_check_player_online` / `request_server_api` 跨插件语义

### 🟢 R4R-5.1：3 处 `_check_player_online` 实现微差异

- **Severity**: 🟢 Low
- **Files**:
  - `nextbot/plugins/lottery.py:158-180`
  - `nextbot/plugins/shop.py:135-156`
  - `nextbot/plugins/warehouse.py:1265-1289`
- **Impact**：三个独立实现行为不完全一致：

  | 维度 | lottery | shop | warehouse |
  |---|---|---|---|
  | 名称匹配 | `.lower()` 仅大小写 | `_normalize_player_name` (NFKC + casefold) | 同 shop |
  | offline 时 reason | `"玩家不在线"` | `""`（空串） | `""`（空串） |
  | RPC 失败时 reason | 兜底 `"查询失败"` | 不兜底（直接传 `get_error_reason`） | 不兜底 |

- **复现**：玩家用全角 `Ａｌｉｃｅ` 注册 → 命令商店 / 仓库匹配 ✅，抽奖匹配 ❌（因为 lottery 没用 NFKC normalize）
- **风险评估**：
  - 全角名一致性：lottery 漏掉 NFKC，是真实 inconsistency，但**用户名注册阶段** `user_manager._validate_user_name` 仅允许 `[A-Za-z0-9一-鿿]+`（`user_manager.py:64`），不允许全角拉丁字母 → 实际触发概率低
  - reason 文案：影响错误提示一致性，无功能影响
- **修法**：抽到一个 helper（如 `nextbot/server_validation.py` 已有 `_check_player_online`），3 个插件共用；或最低限度让 lottery 也用 `_normalize_player_name`

### ℹ️ R4R-5.2：rob.py 不做 player online 检查

- **Severity**: ℹ️ Info
- **Files**: `nextbot/plugins/rob.py`（无 `_check_player_online` 调用）
- **Impact**：抢劫纯粹基于 DB 状态，不需要在线 / 通过 RPC 验证，设计如此
- **修法**：N/A

---

## Checklist 6: tutorial_data + startup hooks

**扫描通过**：

- `tutorial_data.py` 73KB 静态字典 `TUTORIALS`（462 行）
- `tutorial.py:13` 仅 `from nextbot.plugins.tutorial_data import get_tutorial, list_tutorials`
- 模块级 dict 仅在 import 时一次性加载，无动态加载 / 反序列化路径 → 无 OOM 风险
- `list_tutorials()` 返回 `list(TUTORIALS.values())` 浅拷贝键序，每次调用 O(N)，N≈12 教程，可接受

---

## Checklist 7: subtle 计算 bug

### 🟡 R4R-7.1：dice / guess_number 的 `dice_total_gain += net` 在 cap 触发后过统计

- **Severity**: 🟡 Medium（不影响金币真实值，只影响排行榜 / 统计准确度）
- **Files**:
  - `nextbot/plugins/dice.py:195` `net = payout - cost` + `dice_total_gain += net`（line 215）
  - `nextbot/plugins/guess_number.py:230 / 250` 同模板
- **Impact**：`payout = cost * triple_multiplier`，若 admin 配置 `cost = MAX_COINS_AMOUNT (1e10)` + `triple_multiplier = 10` → `payout = 1e11`。`add_coins_with_cap` 触顶仅入账 `room ≤ 1e10`，但 `net = 1e11 - 1e10 = 9e10` 全额计入 `dice_total_gain` → 真实余额 + 1e10，但统计显示赢了 9e10
- **复现**：admin 把骰子 `cost` 设为接近 MAX，user 三连胜利 → 排行榜数字 ≠ 实际余额变化
- **修法**：把 `net` 改成 `applied_payout - cost`（用实际入账派金计算）；与 `lottery._charge_atomic` 用 `applied_coin_delta` 而非 `raw_coin_delta` 展示的修法对齐
- **历史关联**：与 R3E-1（红包蒸发）同类型 bug，只是 stats 而非真实金币

### 🟢 R4R-7.2：lottery `_draw_lucky` 浮点除法

- **Severity**: 🟢 Low / 扫描通过
- **File**: `nextbot/plugins/red_packet.py:69` `avg = remaining_amount / remaining_count`
- **Impact**：Python 3 真除法，结果是 float。`max(1, int(avg * 2))` 截断
- **风险评估**：
  - `remaining_amount ≤ MAX_COINS_AMOUNT = 1e10`
  - `int(1e10 * 2)` = `2e10`，float64 精确范围内（< 2^53 ≈ 9e15）
  - 无精度损失，无溢出
- **修法**：N/A

### ℹ️ R4R-7.3：rob payout 用 `random.randint` 而非 `secrets.SystemRandom`

- **Severity**: ℹ️ Info（已知设计）
- **Files**:
  - `nextbot/plugins/rob.py:247 / 269 / 271`
  - `nextbot/plugins/lottery.py:139` (`random.random() * 100`)
  - `nextbot/plugins/red_packet.py:78` (`random.randint(1, high)`)
  - `nextbot/plugins/dice.py:173-175`
  - `nextbot/plugins/guess_number.py:203`
  - `nextbot/plugins/economy.py:316`
- **Impact**：使用 Python 标准 `random` 模块（Mersenne Twister），不是 cryptographically secure。理论上若攻击者能观测足够多 sample 并预测内部状态，可预测后续 roll
- **风险评估**：
  - 攻击者需要：能观测 624 个连续输出 + 服务器进程未重启
  - 实际：单进程并发多种 random 调用混在一起，状态空间被多个域共享，预测难度极高
  - 接受 trade-off；如未来出现 abuse，再换 `secrets.SystemRandom`
- **修法**：N/A（已知 acceptable）

### 🟢 R4R-7.4：counter / police / fail 的 `max(1, ...)` 边界

- **Severity**: 🟢 Low / 扫描通过
- **File**: `nextbot/plugins/rob.py:347 / 388 / 425`
- **Impact**：`amount = max(1, robber_coins * X // 100)`。若 `robber_coins = 0` → `0 * X // 100 = 0` → `max(1, 0) = 1`。但 attacker 入场前已校验 `robber_coins <= 0` 短路（`rob.py:228-230`），所以 `robber_coins == 0` 不会进入 amount 计算
- **风险评估**：counter/police/fail 都有 `User.coins >= amount` 兜底，且 police/fail 还有 `coins=0` fallback 路径，无凭空生币风险
- **修法**：N/A

---

## Checklist 8: parse_int / args 二次校验

**扫描通过**：

- `_parse_positive_int` (`economy.py:55-63`) 严格 `isdigit()` + `> 0`，统一在 economy / dice / guess / lottery / red_packet / rob_protection / shop 使用
- 所有 mutation handler 后续都有 `_exceeds_max_amount` (`> MAX_COINS_AMOUNT`) 二次校验
- `parse_command_args_with_fallback` 配合 `len(args) != N` 严格 arity check + `raise_command_usage()` 兜底
- 无遗漏的负数 / 超大值绕过路径

---

## Checklist 9: 审计 log 完整性

### ℹ️ R4R-9.1：金币 / 红包 / 仓库 / 商店变更不走 audit_permission_change

- **Severity**: ℹ️ Info（已知设计）
- **Scope**：
  - `economy.py:677 / 802` 仅 `add_coins / remove_coins` 走 audit（cross-user admin action）
  - 其他金币变更（签到 / 转账 / 红包 / 抢劫 / 抽奖 / 商店 / 骰子 / 猜数字）只 `logger.info("金币变更：...")`
- **Impact**：审计聚合需要按 `[INFO] 金币变更：` 前缀 grep，而非统一 `audit_permission_change`
- **风险评估**：
  - 设计取舍：用户主动行为（自己消费）不是 "permission mutation" 范畴
  - 已经有标准化日志前缀 `actor=X target=Y action=Z amount=A reason=R`，可被 log pipeline 聚合
  - 不需要改
- **修法**：N/A

---

## Checklist 10: 不常用命令路径

### ℹ️ R4R-10.1：解封 / 重置访客 / 删除身份组 cascade

- **Severity**: ℹ️ Info / 扫描通过
- **Files & 行为**：
  - **解封**：`ban.py:244-304` → `apply_unban_to_db` (`ban_core.py:95-126`，条件 UPDATE + capture name/qq + audit_permission_change) → `sync_user_blacklist_remove` 跨服 fan-out
  - **重置访客权限**：`permission_manager.py:884-1050+`，二次确认 + 重读 old_csv 重新计算 diff（避免 stale）+ retry 模板
  - **删除身份组**：`group_manager.py:315-410`，二次确认 + 用户回退 default + 子组 inherits scrub + 单事务 commit
- **审计**：3 个路径全部走 `audit_permission_change`
- **修法**：N/A

---

## Bonus 发现：fan-out gather 缺 `return_exceptions=True`

### 🟢 R4R-B.1：4 处 `asyncio.gather` 无 `return_exceptions=True`

- **Severity**: 🟢 Low
- **Files**:
  - `nextbot/server_broadcast.py:66-68`（明确 `return_exceptions=False`，但 `_wrap` 已 catch BLE001，安全）
  - `nextbot/plugins/leaderboard.py:790`
  - `nextbot/plugins/user_manager.py:134`（`_sync_one_whitelist` fan-out）
  - `nextbot/plugins/user_manager.py:564`（`_rename_one_whitelist` fan-out）
  - `nextbot/plugins/permission_manager.py:661`（`_fetch_nickname_with_timeout` 已 catch Exception，安全）
- **Impact**：
  - `_sync_one_whitelist` / `_rename_one_whitelist` / `_fetch_one`（leaderboard）只 catch `TShockRequestError`，不 catch 通用 Exception
  - 若任一任务抛非 TShock 异常（`MemoryError`、`asyncio.CancelledError`、`KeyError`），gather 会取消其他任务
  - 在 `payload.get` 路径上，`payload` 已保证是 dict（`tshock_api.py:83`），所以实际暴露面小
  - **CancelledError 是真实风险**：handler 被超时/取消 → 一个 server task 取消 → 其他 server tasks 都被取消 → 部分白名单同步成功 + 部分未同步，且无显式区分
- **复现**：handler 超时取消 → 部分 server 完成同步、部分未完成；调用方拿到部分 results 不全
- **修法**：把 `asyncio.gather(...)` 改 `asyncio.gather(..., return_exceptions=True)` + per-result `isinstance(r, BaseException)` 分支记录失败原因，与 `shop.py:743-746` 已经实现的模板对齐
- **PRD 影响**：与之前 R3N-4.2（shop 改 `return_exceptions=True`）同模板，但当时只改了 shop，user_manager / leaderboard / 部分 permission_manager 漏

### 🟢 R4R-B.2：MAX_COINS_AMOUNT 在 `webui_shop.py` 重复定义

- **Severity**: 🟢 Low（已记入设计选择）
- **File**: `server/routes/webui_shop.py:23-28`
- **Impact**：`_MAX_COINS_AMOUNT = 10_000_000_000` 与 `economy.py:52` 重复定义，已注释说明"避免加载时触发 nonebot 副作用"
- **风险**：未来再 bump MAX_COINS 时容易漏掉这一处
- **修法**：N/A（已经有源码注释明示）；可考虑抽出独立 `nextbot/constants.py`

---

## 已知不修项目（Round 1-3 已 acceptable trade-off）

- bot.send 失败 → 用户感知 ≠ DB 状态（chat-bot 固有 limitation）
- random vs secrets.SystemRandom（性能 vs 随机质量 trade-off）
- 各域 mutation 不统一走 `audit_permission_change`（设计取舍）
- 部分胡 partial-fail 不退款（如 shop._buy_command 1/3 server 成功 → 2/3 价格不退；用户用 `cmd_total cmd_success` 查询）

---

## Caveats / 不确定项

- 未审 WebUI 路由（`server/routes/`）的并发安全 / 输入校验，已记入下游任务
- 未 read 全部 1900+ 行 warehouse.py（仅 sample 检查 lock + commit 模式）
- audit_economy_change helper 设计未涉及（PRD 明确 out-of-scope）

## 总结

本轮 sweep 找到 **2 medium + 4 low + 3 info + 1 bonus 区**：
- **唯一 actionable medium**：R4R-7.1（dice/guess net 在 cap 后过统计）+ R4R-2.1（except 缺 rollback 普遍化）
- 其余均为 long-tail hardening 或可接受 trade-off
- **无 critical / high → PRD 验收标准达成（critical=0 + high≤1）**
- 系统已收敛
