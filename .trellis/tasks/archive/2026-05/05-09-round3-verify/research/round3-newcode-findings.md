# Research: 第 16 批 sweep — final-sweep + post-sweep 新代码 audit

- **Query**: 重点扫描 final-sweep / post-sweep 引入的新代码（add_coins_with_cap、safe_at_segment_or_empty、lottery._charge_atomic partial UPDATE rowcount check、shop/lottery 在线检查并行、server_manager/economy 新 audit、user_manager 条件 UPDATE、init_db 单一入口、permissions 扩容、screenshot caption、helper 互调）
- **Scope**: internal
- **Date**: 2026-05-09

## Summary

| Severity | Count | Notes |
|---|---|---|
| Critical (red) | 0 | 预期 0，复查通过 |
| High (orange) | 0 | 复查通过 |
| Medium (yellow) | 3 | 一致性 / 错误传播 |
| Low (green) | 4 | 文案 / cosmetic |
| Info (blue) | 4 | 复查通过 |

总体新代码 quality 良好，没发现 correctness regression。Medium 集中在
audit 一致性 + asyncio.gather 异常传播；Low 都是 cosmetic 文案问题。

---

## 1. nextbot/plugins/economy.py: add_coins_with_cap

### R3N-1.1: ℹ️ 复查通过 — delta=0 真的 no-op DB

economy.py:80-81
```python
if delta <= 0:
    return 0, False
```
delta <= 0 时立即 return，无任何 SELECT / UPDATE。✅

### R3N-1.2: ℹ️ 复查通过 — delta < 0 防御正确

由于 helper 名为 ``add_coins_with_cap`` 且明确要求 delta > 0，delta < 0
被视作 no-op。所有 caller（economy / warehouse / guess / dice / rob /
red_packet）均传入正值（refund / payout / amount），无 negative 实际场景。
对 caller 而言这是合约：负数走自己的 ``UPDATE coins=coins - amount``
条件 UPDATE，不该绕到 ``add_coins_with_cap``。✅

### R3N-1.3: 🟡 Medium — delta < 0 silent return 缺 logger.warning

economy.py:80-81
```python
if delta <= 0:
    return 0, False
```
**Impact**：若未来某处 caller 误传负数（例如 refund 计算溢出成负），helper 会
silently return (0, False)，调用方还以为退款成功，但实际没扣。bug 难定位。

**复现**：``add_coins_with_cap(session, "u1", -100)`` → 返回 (0, False)，
日志无任何记录。

**修法**：``if delta < 0: logger.warning(f"add_coins_with_cap 收到负数 delta：user_id={user_id} delta={delta}"); return 0, False``。
区分 delta=0 (合法 no-op) 和 delta<0 (调用方 bug)。

### R3N-1.4: ℹ️ 复查通过 — 多次连续调用同一 user_id 安全

economy.py:66-122

helper 内部所有 SELECT / UPDATE 都通过 caller 传入的 ``session``，
不创建新 session。在同一 BEGIN IMMEDIATE 事务下：
- 第一次 UPDATE 后，下一次 SELECT scalar 看到最新值（同 session 内可见）
- 多次连续 partial cap 也会看到累加后的余额

rob.py:320 + rob.py:339 / rob.py:372 在同一 session 内连续调用 helper 是安全的。✅

### R3N-1.5: ℹ️ 复查通过 — BEGIN IMMEDIATE 下不死锁

helper 仅做 SELECT 和 UPDATE 同一行（``where User.user_id == user_id``），
不持其他锁、不调用其他 helper、不切换 session。BEGIN IMMEDIATE 全局序列化
本来就消除了 read-modify-write race，helper 内部条件 UPDATE 不会引入新死锁。✅

---

## 2. nextbot/text_utils.py: safe_at_segment_or_empty + at_prefix

### R3N-2.1: ℹ️ 复查通过 — 22 处 callsite 全部 work

22 处 ``at = safe_at_segment_or_empty(user_id)`` 全部走相同套路：
- ``at + " " + reply_failure(...)`` —— 行内单行回复
- ``at + "\n" + reply_block(...)`` —— 多行块回复

OBV11 ``MessageSegment`` 继承自 ``BaseMessageSegment``，``+`` 运算符
返回 ``Message``（line 61: ``MessageSegment.text(other) if isinstance(other, str) else other``），
所以 ``segment + str + str`` 工作正常。``at`` 是真正 ``MessageSegment.at(int)``
或 ``MessageSegment.text("")``，两种情况下拼接都不报错。✅

### R3N-2.2: 🟢 Low — empty at + " " 拼出多余前导空格

text_utils.py:106-119

当 ``user_id`` 非纯数字（如 "user" 测试桩 / 未来 Telegram bridge ID），
``safe_at_segment_or_empty`` 返回 ``MessageSegment.text("")``。

**实际渲染**：
- ``text("") + " " + "❌ 失败"`` → ``[text(""), text(" "), text("❌ 失败")]``
- 序列化为 `` ❌ 失败``（前导空格）

**Impact**：仅 cosmetic，console 适配器或测试桩看到多余空格。OBV11 真实
QQ 用户永远是数字 user_id，看不到。

**修法**：可在 ``safe_at_segment_or_empty`` 退化路径返回时把 sep 一起吞掉，
或在 caller 层判断；目前实现 trade-off 合理（统一 ``at + " " + ...`` 拼装）。
不修。

### R3N-2.3: ℹ️ 复查通过 — 无循环 import

text_utils.py:1-9 仅 import ``typing``、``nonebot.log``、TYPE_CHECKING 下的
``nonebot.adapters``。**无任何 ``from nextbot.X import Y``**，不会与 plugins
形成循环。✅ ``OBV11MessageSegment`` import 延迟到函数体内（line 97 / 114），
进一步避免在测试或非 OBV11 环境强制依赖 OneBot 包。

---

## 3. nextbot/plugins/lottery.py: _charge_atomic post-sweep partial UPDATE

### R3N-3.1: ℹ️ 复查通过 — partial UPDATE rowcount 检查正确

lottery.py:783-799（正向）+ lottery.py:826-839（负向）

正向 partial 路径：
```python
partial = min(capped_pos, room)
if partial > 0:
    partial_rowcount = execute_rowcount(session_local, update(User)...)
    if partial_rowcount > 0:
        applied_pos = partial
    else:
        logger.warning(f"...partial cap UPDATE 被并发覆盖：...applied=0")
```
正确处理了 partial UPDATE rowcount=0 的极端 race（``applied_pos`` 保持 0，
不会误声明）。负向同样正确。✅

### R3N-3.2: 🟡 Medium — lottery 自实现 partial cap，与 helper 行为不完全一致

lottery.py:761-843 vs economy.py:66-122

**差异 1**：helper 在第一次 UPDATE 失败后立刻 SELECT 当前余额计算 ``room``，
而 lottery 直接尝试 ``capped_pos = min(coin_delta_pos, MAX_COINS_AMOUNT)``
作为单笔 cap，再走条件 UPDATE，rowcount=0 才 SELECT room 走 partial。
两种顺序结果一致，但 lottery 多走一次条件 UPDATE 后 fallback。

**差异 2**：helper 用单一 ``logger.warning`` 区分两种 cap 场景（"触顶 cap"
vs "部分被 cap"），lottery 在正向 / 负向各写两条不同 warning。

**Impact**：两套实现均 correct，但维护上分裂。后续若要调整 cap 语义
（例如改成"触顶时拒绝整笔交易"），需要同时改 helper + lottery 内联实现。

**修法**：可将 lottery._charge_atomic 内的正向 cap 路径替换为
``add_coins_with_cap(session_local, user_id, coin_delta_pos)``。负向暂不
适用（helper 仅处理正数）。本次不修，作为后续 task 候选。

### R3N-3.3: 🟢 Low — 负向 cap 警告无条件 fire

lottery.py:840-843
```python
logger.warning(
    f"抽奖负向 coin 奖励部分被 cap：user_id={user_id} ...applied={applied_neg}"
)
```
**Impact**：当 ``coins_now == 0`` 且 ``coin_delta_neg < 0`` 时，``partial=0``
跳过 partial UPDATE 块，``applied_neg`` 始终为 0。warning 仍会 fire
``applied=0``，看起来像"部分被 cap"实际是"完全无法扣"。

**修法**：把 warning 移入 ``if applied_neg < coin_delta_neg`` 判断，
或区分 "完全无法扣 (applied=0)" vs "部分扣 (0 < |applied| < |requested|)"。
不修。

---

## 4. asyncio.gather 在线检查（shop / lottery）

### R3N-4.1: ℹ️ 复查通过 — gather ordering preserved

shop.py:741-744 + lottery.py:621-624 都用 ``asyncio.gather(*generator)`` 后
``zip(servers, check_results)``。``asyncio.gather`` **保证返回顺序与传入顺序对应**
（asyncio 文档明确说明），所以 zip 配对正确。✅

### R3N-4.2: 🟡 Medium — gather 异常传播会取消其他任务

shop.py:741-743 + lottery.py:621-623

```python
check_results = await asyncio.gather(
    *(_check_player_online(srv, player_name) for srv in servers)
)
```
**未传 ``return_exceptions=True``**。

**Impact**：若任一 ``_check_player_online`` 抛非 ``TShockRequestError`` 异常
（例如内部代码 bug、payload 格式异常导致 ``AttributeError``），整个 gather
立即向上抛，外层 ``try/except Exception`` (lottery.py:1011 / shop.py:562)
捕获并回 "处理失败，请稍后重试"。此时其他服务器的检查任务被 cancel，
本可成功的部分也丢失了。

**复现**：构造一个 TShock 返回 ``payload`` 不是 dict 的服务器（极端边界），
触发 ``payload.get("players")`` 之外的异常路径。

**修法**：改 ``return_exceptions=True``，对每个结果做 isinstance 判断
异常 → 当作 ``(None, str(exc))``。或在 ``_check_player_online`` 内加
更广泛的 ``except Exception``。

参考：``server_broadcast.broadcast()`` 在 ``_wrap`` 内已 catch all
exception 转 ``BroadcastOutcome``，是更好的模式。shop / lottery 在线检查
理论上也可包一层类似 wrapper。

### R3N-4.3: ℹ️ 复查通过 — (bool|None, str) 三态并行后处理正确

lottery.py:624-631 + shop.py:744-750

```python
for srv, (ok, reason) in zip(target_servers, check_results):
    if ok is True:    # 在线
        online_servers.append(srv)
    elif ok is False: # 确认离线
        offline_reasons.append(...)
    else:             # None：RPC 失败 / 格式异常
        offline_reasons.append(...)
```
``ok is True`` / ``ok is False`` / ``else`` 三分支覆盖完整三态，没有
``if not ok`` 这种 ``False == None`` 的歧义。✅

### R3N-4.4: 🟢 Low — shop 与 lottery 的 _check_player_online 文案不一致

shop.py:135-156 vs lottery.py:154-176

shop 实现 line 156: ``return False, ""``（空 reason）
lottery 实现 line 176: ``return False, "玩家不在线"``

**Impact**：shop callsite (line 748) 强制 hard-code ``"玩家不在线"`` 来弥补
空 reason，lottery callsite 直接用 reason。两套实现职责切分不一致。
shop ``_check_player_online`` 还多了 unicode NFKC + casefold 归一化（line 147-154）。

**修法**：把 ``_check_player_online`` 提到 ``nextbot/`` 顶层共享 module（如
``nextbot/server_player.py``），统一返回值语义和 normalization。本次不修。

---

## 5. server_manager.py + economy.py: audit_permission_change 调用

### R3N-5.1: ℹ️ 复查通过 — 字段完整性 OK

server_manager.py:104-114（add）+ server_manager.py:178-188（delete）+
economy.py:608-619（coins.add）+ economy.py:733-743（coins.remove）

所有 4 处调用都按 ``audit.py`` 签名传 ``actor_user_id / action / target /
before? / after? / context?``。add 操作传 ``after``（无 before），
delete 操作传 ``before``（无 after），mutate 操作两者都传。✅

### R3N-5.2: 🟡 Medium — 失败路径未审计 denied 事件（与 ban / permission_manager 不一致）

server_manager.py:64（validation 失败）+ server_manager.py:91（IntegrityError）+
server_manager.py:158（不存在）

这三处失败路径仅 ``logger.warning / info``，**未** 调用 ``audit_permission_change(action="server.add.denied", ...)``。

对比 ban.py:91-96：
```python
if result.code == "owner_protected":
    audit_permission_change(
        actor_user_id=operator_id, action="user.ban.denied",
        target=target_user_id, context={"reason": "owner_protected"},
    )
```

permission_manager.py 也对多处 denied 路径走 audit。

**Impact**：安全监测平台无法用统一 audit 入口聚合失败的 ``server.add``
尝试（只能去 grep INFO 日志）。攻击者反复尝试添加冲突 ID 不会触发
audit alarm。

**修法**：在 server_manager.py:64 / 91 加 ``audit_permission_change(
action="server.add.denied", context={"reason": "..."})``；line 158 加
``action="server.delete.denied"``。

economy.py:577 / 688（用户不存在）也类似——未审计 denied。但 economy
失败路径属于"目标不存在"低风险场景，audit 价值低。**仅强烈建议补
server_manager 的失败 audit**。

### R3N-5.3: ℹ️ 复查通过 — 字段格式与其他 audit 调用一致

经济类 audit 用 ``before={"coins": N}`` / ``after={"coins": N}`` /
``context={"requested": N, "applied": N, "name": ...}``。
server_manager 用 ``after={"name": str, "ip": str, "game_port": str,
"restapi_port": str}`` （注意没有 token，避免凭证泄露）。
permission_manager.py / group_manager.py 风格一致。✅

---

## 6. user_manager.py: handle_rename 条件 UPDATE

### R3N-6.1: ℹ️ 复查通过 — old_name + user_id 双 WHERE 合理

user_manager.py:497-522

```python
update(User)
.where(
    User.user_id == target_user_id,
    User.name == old_name,
)
.values(name=new_name)
```
``user_id`` 是 PK 级唯一，定位到行；``name == old_name`` 是 optimistic
locking，校验"我读到的状态依然有效"。

BEGIN IMMEDIATE 全局序列化下，从 SELECT user 到 UPDATE 之间不可能有
其他 transaction commit，所以 ``name == old_name`` 几乎永远 True。
但 defense-in-depth 仍 OK，未来若改 isolation level 就有用。

**race scenario**：rowcount=0 时返回 "并发冲突，请重试" 而不是
"用户不存在"，文案合理（这个时点 user 一定存在，name 已被并发改）。✅

### R3N-6.2: ℹ️ 复查通过 — IntegrityError 双层兜底

user_manager.py:509-515 + user_manager.py:523-531

```python
try:
    rowcount = execute_rowcount(session, update(...))
except IntegrityError:           # ← 第 1 层：UNIQUE 撞库（在 execute 阶段）
    session.rollback(); ...
if rowcount == 0:
    session.rollback(); ...      # ← 同名 / 已变更
try:
    session.commit()
except IntegrityError:           # ← 第 2 层：commit 阶段才触发的 UNIQUE 冲突
    session.rollback(); ...
```
SQLite 在 ``execute`` 阶段通常立即触发 UNIQUE 冲突，但 deferred
constraint / pending flush 等场景下可能在 commit 才触发。两层 catch 覆盖
完整。✅

不过注意：line 489-495 已先做 ``name_exists`` 预查（lower-case 比对），
理论上 BEGIN IMMEDIATE 下不会到 IntegrityError。这是 belt-and-suspenders 设计。

---

## 7. bot.py + db.py: init_db 单一入口

### R3N-7.1: ℹ️ 复查通过 — 17 处 ensure_*_schema 全 idempotent

逐项验证：

| ensure_*_schema | idempotent 机制 | 验证 |
|---|---|---|
| ensure_command_config_schema | ``if X not in columns: ALTER ADD COLUMN`` | ✅ |
| ensure_user_signin_schema | ``if "signed_today" not in columns: return``（drop 已存在列） | ✅ |
| ensure_sign_record_schema | ``CREATE TABLE IF NOT EXISTS`` | ✅ |
| ensure_sign_record_unique_schema | ``CREATE UNIQUE INDEX IF NOT EXISTS`` | ✅ |
| ensure_user_sign_record_index_schema | ``CREATE INDEX IF NOT EXISTS`` | ✅ |
| ensure_user_ban_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_user_rob_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_user_guess_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_user_dice_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_red_packet_schema | ``CREATE TABLE IF NOT EXISTS`` | ✅ |
| ensure_warehouse_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_shop_schema | ``if X not in columns: ADD COLUMN`` | ✅ |
| ensure_lottery_schema | no-op（仅 placeholder） | ✅ |
| ensure_user_name_unique_schema | ``CREATE UNIQUE INDEX IF NOT EXISTS`` | ✅ |
| ensure_user_leaderboard_indexes_schema | ``CREATE INDEX IF NOT EXISTS`` per col | ✅ |
| ensure_warehouse_fk_schema | ``CREATE INDEX IF NOT EXISTS`` | ✅ |
| ensure_default_groups | ``SELECT first ; if None: INSERT`` | ✅ |
| ensure_default_stats | ``SELECT first ; if None: INSERT`` | ✅ |

全部幂等。重复运行 ``init_db()`` 无任何副作用。✅

### R3N-7.2: ℹ️ 复查通过 — 启动顺序正确

db.py:424-444

```python
Base.metadata.create_all(engine)        # 1. 表
ensure_command_config_schema()          # 2. 表 ALTER
ensure_user_signin_schema()             #    （drop 旧列）
ensure_sign_record_schema()             #    表
ensure_sign_record_unique_schema()      #    索引
ensure_user_sign_record_index_schema()  #    索引
ensure_user_ban_schema()                #    ALTER
ensure_user_rob_schema()                #    ALTER
ensure_user_guess_schema()              #    ALTER
ensure_user_dice_schema()               #    ALTER
ensure_red_packet_schema()              #    表
ensure_warehouse_schema()               #    ALTER
ensure_shop_schema()                    #    ALTER
ensure_lottery_schema()                 #    no-op
ensure_user_name_unique_schema()        #    索引
ensure_user_leaderboard_indexes_schema()#    索引
ensure_warehouse_fk_schema()            #    索引
ensure_default_groups()                 # 3. seed groups
ensure_default_stats()                  # 4. seed stats
```

顺序：表 → 索引 → seed。任何步骤异常时函数直接抛，
后续 seed 不会执行。但中间步骤（如 ALTER）失败已被各 ensure 函数
内部 try/except 转 ``logger.warning``，不阻断启动——这与"异常时正确
abort"的诉求略有不同，但属于刻意设计（schema migration 失败仍允许
启动，由管理员手工修复）。✅

### R3N-7.3: 🟢 Low — ensure_lottery_schema no-op 注释含混

db.py:565-574
```python
def ensure_lottery_schema() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        pass
    finally:
        conn.close()
```
**Impact**：``conn`` 被打开又立即关闭，无任何 work。是 placeholder 给
未来加 column 用，但当前没有任何文档说明这是 placeholder。新人
maintainer 看了会以为是 BUG。

**修法**：直接 ``def ensure_lottery_schema() -> None: pass`` 或加 docstring
``# placeholder for future ALTER hooks``。不修。

---

## 8. permissions.py: DANGEROUS_PERMISSION_PREFIXES 扩容

### R3N-8.1: ℹ️ 复查通过 — 通配匹配正确

permissions.py:121-139

测试 case：
| 输入 | 预期 | 实际 | 通过 |
|---|---|---|---|
| ``"server_tools.execute"`` | True | True (exact match) | ✅ |
| ``"server_tools.*"`` | True | True (prefix=``server_tools.``，覆盖 ``server_tools.execute / map_image / download_map``) | ✅ |
| ``"server.add"`` | True | True (exact match) | ✅ |
| ``"server.*"`` | True | True (prefix=``server.``，覆盖 ``server.add / server.delete``) | ✅ |
| ``"server.list"`` | False | False (不在 set，不是 wildcard) | ✅ |
| ``"server.send"`` | False | False | ✅ |
| ``"economy.coins.add"`` | True | True (exact match) | ✅ |
| ``"economy.coins.*"`` | True | True (prefix=``economy.coins.``，覆盖 add / remove) | ✅ |
| ``"economy.coins.transfer"`` | False | False (不在 set) | ✅ |
| ``"economy.*"`` | True | True (prefix=``economy.``，覆盖 ``economy.coins.add / remove``) | ✅ |
| ``"admin.*"`` | True | True (prefix=``admin.``，覆盖 ``admin.ban / unban / rename``) | ✅ |
| ``"admin.ban"`` | True | True (exact match) | ✅ |
| ``"admin.rename"`` | True | True (exact match) | ✅ |
| ``"*"`` | True | True (special case) | ✅ |

无误拦：``server.list / server.send / server.test / economy.transfer /
economy.sign`` 等普通 guest 权限均返回 False。✅

---

## 9. screenshot_render.py

### R3N-9.1: 🟢 Low — success_caption 默认值未被任何 caller 用

screenshot_render.py:135-144

```python
await bot.send(
    event,
    reply_block(
        reply_success(failure_action, success_caption or "截图已生成"),
        ...
    ),
)
```

非 V11 fallback 走 ``reply_success(failure_action, "截图已生成")`` →
``"✅ 查询成功，截图已生成"`` / ``"✅ 抽奖成功，截图已生成"``。文案 OK，
但 OBV11 路径（line 128）不发任何 caption 文本，仅图片，调用方与
fallback 路径文案不对称。

**Impact**：仅在非 V11 fallback 适配器被使用时（项目目前实际未启用），
用户看到额外的 "✅ 抽奖成功，截图已生成" 文字。OBV11 用户看不到。

**修法**：可在 OBV11 路径也补一段 ``reply_success`` text 段；或干脆 fallback
也只发图不带文字。当前实现 trade-off 偏 fallback 友好，不修。

### R3N-9.2: ℹ️ 复查通过 — semaphore 池在 plugin reload 后仍 work

每个 caller 在 module-level 持有 ``_xxx_screenshot_semaphore = asyncio.Semaphore(N)``
（lottery.py:55 / user_manager.py:44 / shop.py: ...）。NoneBot 默认
``load_plugins`` 不做 hot reload，每次启动 import 一次。即使未来加
hot reload，semaphore 重新创建后 counter 重置——但同时所有引用旧
semaphore 的协程也会被新代码替换，无 inconsistency 风险。✅

---

## 10. 新 helper 互相调用

### R3N-10.1: ℹ️ 复查通过 — add_coins_with_cap 内 logger.warning 无 reentrancy 问题

economy.py:99-101 / 114-116 / 119-121

logger.warning 是 nonebot.log.logger（loguru 包装），线程安全。
helper 在同一 task 内串行调用 logger，不会 reentrant。✅

### R3N-10.2: ℹ️ 复查通过 — safe_at_segment_or_empty 不被 audit_permission_change 间接调用

audit.py 全文不涉及 ``safe_at_segment_or_empty``，只调 ``logger.warning``。
text_utils.py 不 import 任何 nextbot module。✅ 不存在循环调用。

---

## Caveats / Not Found

- 未对 lottery 100 抽 × 配置错误 admin 数据做端到端模拟，partial cap
  正负向汇合时的边界（如同一笔抽奖里同时有 +大金币 和 -大金币 prize）
  仅静态分析，未跑 unit test。
- DANGEROUS_PERMISSION_PREFIXES 的通配匹配验证基于代码 trace，未 hook
  到运行时 ``is_dangerous_permission`` 跑实际 fixture。
- ``init_db`` 17 处 ensure_* 的幂等性验证基于代码 trace + SQLite ``IF NOT
  EXISTS`` 语义，未实际跑 ``init_db()`` × 2 次验证 DB 文件 hash 一致。
- bot.py 启动顺序异常 abort 行为依赖各 ensure 函数 try/except 行为，未
  压测 corrupted DB 场景。
