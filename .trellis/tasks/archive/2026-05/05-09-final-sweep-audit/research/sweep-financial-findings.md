# Final Sweep Audit — Financial / Transactional Handlers

- **Date**: 2026-05-09
- **Scope**: 5 files — `economy.py`, `red_packet.py`, `warehouse.py`, `shop.py`, `lottery.py`
- **Goal**: Catch (a) regressions from the 12 prior audits, (b) cross-handler/cross-file bugs that single-file passes missed, (c) edge cases at new helper boundaries.

Severity legend: 🔴 critical / 🟠 high / 🟡 medium / 🟢 low / ℹ️ informational.

---

## 1. economy.py

### SF-1.1 🟠 跨命令组合可把 `coins` 推过 `MAX_COINS_AMOUNT` 全局上限

**File**: `nextbot/plugins/economy.py:455-459`（`handle_add_coins`）；同问题也出现在 SF-2.x / SF-3.x / SF-5.x（见后文）。

```python
session.execute(
    update(User)
    .where(User.user_id == target_user_id)
    .values(coins=User.coins + amount)
)
```

**Impact**：每条单命令对 `amount` 做了 `MAX_COINS_AMOUNT` 上界检查（line 440-445），但条件 `UPDATE` 没有 `User.coins + amount <= MAX_COINS_AMOUNT` 约束。因此只要用户当前余额接近上限：

- admin 一次「添加金币」最多加 100M，但若用户已有 99M → 新余额 199M（>cap）。
- 红包派发（SF-2.1）/ 仓库回收（SF-3.1）/ 抽奖正向 coin 奖（已修，参考 SF-5.x 注）/ 商店购买退款（不存在，不适用）等所有 +coins 路径都有同样模式，组合下让 `User.coins` 漂上限以上是稳定可复现的。

实际上 `MAX_COINS_AMOUNT` 没有被作为「账户余额硬天花板」，只是「单笔操作金额硬天花板」。两层语义需要项目层面明确：

- 如果 `MAX_COINS_AMOUNT` 是**单笔上限**（命名上是"AMOUNT"），那就只是 sanity 防御，余额可以超过；当前代码就是这个语义，那就 OK，没 bug，但需要在 spec 中明确，并在 lottery 的 partial-cap 逻辑里对齐（lottery 是把它当余额上限）。
- 如果 `MAX_COINS_AMOUNT` 是**账户上限**（lottery `_charge_atomic` 显然是这么解读的），那 economy / red_packet / warehouse / shop 全都要补 `User.coins + delta <= MAX_COINS_AMOUNT` 条件。

**修法**：先确认语义。**lottery 的 LO-3.13 注释（`unit_value cap`）和 line 767 的 `User.coins + capped_pos <= MAX_COINS_AMOUNT` 让它做的是「账户上限」语义；其他文件都是「单笔上限」语义。这个不一致是真 bug。**

**复现**：
1. admin 给玩家 A 加 99,999,999 金币（economy add_coins 通过，单笔 < cap）。
2. 玩家 A 去 economy add_coins（如果有权限）/ 收回旧红包 / 仓库回收（高单价物品）任一路径再 + 几千。
3. `User.coins > MAX_COINS_AMOUNT` 持久化。
4. 接下来跑 lottery 抽奖中 coin 正向奖 → lottery 自己的余额上限校验生效 → admin 看到不一致的限额行为。

---

### SF-1.2 ℹ️ `audit_permission_change` 不适用于金币变更

**File**: `nextbot/audit.py:1-50`、本组所有 mutation handler。

`audit_permission_change` 文档明确说 *「权限审计统一日志入口」*，参数命名（`action="group.add"` 等）也只覆盖权限语义。**economy / red_packet / warehouse / shop / lottery 这些金币 / 道具 mutation handler 不应该调用它。** 当前代码也确实没有调用，符合设计——本审计点已确认无遗漏。

如果需要金币 / 道具变更的审计统一入口，应另开一个 `audit_economy_change(actor_user_id, action, target, before, after, context)` helper，而不是滥用 `audit_permission_change`。

---

### SF-1.3 🟢 `handle_sign` race window：两次 `session.query(...).count()` 读发生在 commit 之后

**File**: `nextbot/plugins/economy.py:220-224`

```python
today_order = (
    session.query(UserSignRecord)
    .filter(UserSignRecord.sign_date == today_text)
    .count()
)
```

commit 之后再 SELECT，期间另一用户可能完成签到、`today_order` 读到比真实"我是第几位"更大的值。**展示语义不严格**（用户看到的"签到排名：第 N 位"略有偏移，N 可能晚于实际）。不是 correctness bug，仅 UX 偏差。

**修法**：在 commit 之前用 `session.query(...).count()` 读取当时的快照（与 `INSERT` 在同事务内），或者改为基于 sign_streak 排名等更稳定语义。

---

## 2. red_packet.py

### SF-2.1 🟠 `handle_grab` 加金币不带 `MAX_COINS_AMOUNT` cap（与 SF-1.1 同）

**File**: `nextbot/plugins/red_packet.py:349-353`

```python
session.execute(
    sa_update(User)
    .where(User.user_id == user_id)
    .values(coins=User.coins + draw_amount)
)
```

**Impact**：与 SF-1.1 同根因。`draw_amount` 单次 ≤ packet 的 `total_amount`（已 ≤ MAX_COINS_AMOUNT），但用户当前 `coins` 加上去可能超 cap。复现：用户 A 余额 99.5M → grab 一个 500K 红包 → coins=100M 正好 = cap；再 grab 第二个红包 → coins 超过。

**修法**：参考 lottery 的两段式 condition update（capped + partial），或者全项目统一改为「加和 cap」：
```python
.values(coins=func.min(User.coins + draw_amount, MAX_COINS_AMOUNT))
```

---

### SF-2.2 🟠 `handle_withdraw` 退款不带 `MAX_COINS_AMOUNT` cap

**File**: `nextbot/plugins/red_packet.py:454-458`

同 SF-2.1。`refund_amount` 是 packet 的 `remaining_amount`，理论上 sender 当年发包时已经从余额扣过；正常情况下退回不会超 cap。但是攻击场景：

1. 用户 A 余额 1M。
2. A 发 100M 红包（coins=1M-100M=负数？被扣条件拦下）→ 假设 A 通过其他途径攒到 100M 余额，发 100M 红包 → coins=0。
3. A 收到大量转账 / 抽奖 coin 奖 → coins=99M。
4. A 收回红包，refund=100M → coins=199M（>cap）。

实际可达。

---

### SF-2.3 🟢 `_draw_lucky` 边界路径已被发送时校验间接保护，但仍是脆弱的

**File**: `nextbot/plugins/red_packet.py:66-78`

```python
def _draw_lucky(remaining_amount, remaining_count):
    ...
    if high < 1:
        logger.warning(...)
        return 1
```

只有当 `remaining_amount < remaining_count` 时才会触发。当前 send 校验保证 `total_amount >= count * min_amount_per_slot >= count`，每次 draw 后该不变量被保留（draw ≤ remaining_amount - remaining_count + 1）。所以 `high < 1` 路径**不可达**。

但如果未来有人放宽 `min_amount_per_slot=0` 配置，这里 `return 1` 会让 `_claim_slot_atomic` 失败（rowcount=0：`remaining_amount(0) < draw_amount(1)`），用户看到「手慢了一步」，但红包 `status=active` + `remaining_amount=0` + `remaining_count>0`，永远抢不完也不会被 mark 为 exhausted（line 356 仅 check `remaining_count==0`）。

**修法**：要么把 `min_amount_per_slot` 的 `min=1` 提升为 hard contract（去掉默认值，强制配置），要么在 `_draw_lucky` 的 `high < 1` 路径直接返回 `remaining_amount`（如果 0 就 raise，让 caller 跳过 claim）。

---

### SF-2.4 🟢 `handle_grab` 中 `_claim_slot_atomic` 成功后 `add(claim).flush()` 失败时正确 rollback；但 `User.coins+=draw` UPDATE 没有 rowcount 校验

**File**: `nextbot/plugins/red_packet.py:349-353`

如果 user 行被并发删（极端，几乎不会发生）→ rowcount=0，但代码不检查。Packet 那一侧已经 decrement 了，commit 后 sender 会丢失这部分金额，grabber 也没拿到。

**修法**：照 warehouse `_recycle_*` 加 `if rowcount != 1: logger.error('[CRITICAL] ...')`。

---

## 3. warehouse.py

### SF-3.1 🟠 `_recycle_single` / `_recycle_many` 加金币不带 `MAX_COINS_AMOUNT` cap

**File**: `nextbot/plugins/warehouse.py:1101-1108`、`1196-1207`

```python
rowcount = execute_rowcount(
    session,
    update(User)
    .where(User.user_id == user_id)
    .values(coins=User.coins + refund),  # 没 + cap 条件
)
```

W-3.2 的注释说 `refund = min(refund, MAX_COINS_AMOUNT)` 是「单次 refund 上限」，但同 SF-1.1：用户当前余额 + refund 仍可超过 cap。

实际利用：admin 添加一堆高单价（接近 1M）物品到玩家仓库 → 玩家积累 99M 金币 → 回收剩余 → 余额漂上去。

**修法**：要么改为 `User.coins + refund <= MAX_COINS_AMOUNT` 条件 + partial fallback（参考 lottery 模式），要么明确「`MAX_COINS_AMOUNT` 是单笔上限不是账户上限」并在 lottery 里把 cap 逻辑去掉。

---

### SF-3.2 🟢 `_claim_many` 中 `give` 已发但 commit 失败：当前所有 unconfirmed_slots 不影响 used_after，但回复中 `processed=N` 不包含 unconfirmed

**File**: `nextbot/plugins/warehouse.py:1582-1593`、`1660-1672`

`unconfirmed_slots` 已经 give 出去了，但仓库 row 没删；回复里只展示 `processed=N`（不含 unconfirmed），用户会以为这些格子没动 / 还可再领。下一次他们触发「领取仓库物品」会**再发一次 give**，造成 double-spend（道具）。

**修法**：参考 NEW-10（line 1641）的逻辑：把 unconfirmed 在显示给用户的「跳过明细」中标红，并提示「这些格子可能已发到游戏，不要再次领取」。或者更激进：在 unconfirmed 列表非空时把整个回复改成 reply_failure（warning 风），强行让用户感知问题。当前代码 NEW-10 仅在 `processed==0 and unconfirmed_slots` 时切回 failure，部分 unconfirmed + 部分 success 的混合场景仍可能 double-spend。

---

### SF-3.3 🟢 `handle_add` 的 `value > MAX_COINS_AMOUNT` 校验在 admin 路径上是 sanity；但 webui 路径绕过

**File**: `nextbot/plugins/warehouse.py:485-490`

`handle_add` 的金币 cap 是 chat handler 校验。如果 webui 也有 「添加仓库物品」 endpoint，需要同样 cap。这是「同语义两个入口」的典型 drift 风险。

**修法**：把 `value > MAX_COINS_AMOUNT` 的校验下沉到 model 层（pydantic validator / SA event listener / dataclass post-init），或者在 db.py 加 `WarehouseItem.value` 的 CHECK 约束。本审计未实际确认 webui 是否有该 endpoint，留待后续 task 验证。

---

### SF-3.4 🟢 `_acquire_two_warehouse_locks` 内层 lock acquire 之间 `await` 暂停未被防御

**File**: `nextbot/plugins/warehouse.py:242-249`

```python
async with warehouse_lock(first):
    async with warehouse_lock(second):
        yield
```

`first` 已持有，`second` await 期间发生 cancellation（如 task 超时），`first` 因 `async with` 正确释放。无 deadlock；OK，复审通过。

---

## 4. shop.py

### SF-4.1 🟠 `_buy_command` 没有用 `server_broadcast.broadcast`，仍是串行 `for srv in online_servers: for _ in range(buy_count)`

**File**: `nextbot/plugins/shop.py:792-795`

```python
exec_results: list[tuple[Server, bool, str]] = []
for srv in online_servers:
    for _ in range(buy_count):
        ok, reason = await _issue_raw_command(srv, cmd)
        exec_results.append((srv, ok, reason))
```

任务描述说「shop / lottery 的 fan-out 都改用 broadcast 了吗？」**lottery 已迁移；shop 没有**。多服务器购买 + 大 buy_count 时（最大 buy_count=9999），单服 9999 RPC 串行 + 多服串行 = 长时间阻塞 + Bot 整体卡死。

**Impact**：
- 性能：不论 buy_count 是 1 还是 9999，跨服务器没并行化，单笔购买可达数十秒到数分钟。
- 一致性：lottery 跨服并行 + 单服串行的设计已沉淀到 `server_broadcast.broadcast`；shop 偏离这一约定。

**修法**：把 `_issue_raw_command` 套成 `_execute_for_server(srv) -> BroadcastOutcome`（参考 lottery `_execute_for_server`，line 866），用 `await broadcast(online_servers, _execute_for_server)`。同时 `MAX_BUY_COUNT=9999` 是不是也太宽？lottery 已经 cap 到 200 RPC，shop 单服可达 9999 × 1 = 9999 RPC，悬殊。

---

### SF-4.2 🟠 `_buy_command` 缺 `online_servers × buy_count` 的总 RPC 数 cap

**File**: `nextbot/plugins/shop.py:792-799`

延伸 SF-4.1。用户 buy_count=9999 + 全服务器命令 + N=10 个服务器 → 99,990 RPC。lottery 的 LO-3.14（`MAX_LOTTERY_CMD_EXECUTIONS = 200`）在这里完全缺失。

**修法**：加 `MAX_SHOP_CMD_EXECUTIONS = 200`，在金币扣费之前 pre-flight check：
```python
total_rpcs = len(online_servers) * buy_count
if total_rpcs > MAX_SHOP_CMD_EXECUTIONS:
    await bot.send(... "本次购买产生的指令调用过多 ...")
    return  # 在扣费之前
```

---

### SF-4.3 🟡 `_buy_command` 把已经 offline 的服务器从 `servers` 里剔除（require_online=True 路径），但 require_online=False 时**不查 online**直接发；这会把用户没在线的服务器也送 `/give {player}` —— TShock 会 silent-drop

**File**: `nextbot/plugins/shop.py:742-743`

```python
else:
    online_servers = list(servers)
```

如果商品 `require_online=False` 且 `target_server_id is None`（"全部服务器"），即使玩家只登录其中 1 台，命令也会发给所有 N 台。已扣的金币照扣，TShock /give 在玩家不在线时**通常 silent fail**（取决于 TShock 版本），exec_results 显示 `ok=True`，用户感知是"购买成功"，但实际只有 1 台真的发到游戏；其余在玩家下次登录时也不补发。

**Impact**：用户付了 N 倍价格只拿到 1 倍东西；商品 admin 配 `require_online=False` 是想做"在线就送 / 不在线就跳过"语义，但当前代码不区分。

**修法**：明确商品语义。如果 `require_online=False` 是"任意服务器，不管在不在线都执行"，那就保留当前行为但回复里要写明"已发送到所有目标服务器，玩家不在线的服务器不会到货"。如果是"只发到玩家所在服务器"，那 require_online 字段名就是误导，应改为单独 `auto_pick_online_server` 行为。

---

### SF-4.4 🟢 `_buy_item` 在 `warehouse_lock` 内做 `_find_first_empty_slot` + insert，但 lock 释放后 `final_coins` 已经 commit；`session.close()` 在 lock 内，OK

**File**: `nextbot/plugins/shop.py:584-655`

复审通过。

---

### SF-4.5 🟢 `_check_player_online` 与 lottery 用不一致的 nickname 比对（NFKC vs lower）

**File**: `nextbot/plugins/shop.py:80-82`、`130-151`；vs `nextbot/plugins/lottery.py:155-177`

shop 用 `_normalize_player_name`（NFKC + casefold），warehouse 用同一 helper，**lottery 用 `.lower()` 直接比较**。

**Impact**：玩家名包含全角 / 半角差异时，shop / warehouse 视为相同（命令奖品执行），lottery 视为不同（玩家被判离线、cmd_skip_reasons 累加）。同一个用户在 shop 能买到的商品，在 lottery 抽不到指令奖品。

**修法**：lottery `_check_player_online` 也用 `_normalize_player_name`（提到 shared helper，比如 `nextbot.text_utils` 或 `nextbot.player_name`）。建议抽到共享 module，避免三处 duplicate。

---

## 5. lottery.py

### SF-5.1 🟢 `_check_player_online` 与 shop / warehouse 不一致（同 SF-4.5，主修在 lottery 这一侧）

**File**: `nextbot/plugins/lottery.py:155-177`

见 SF-4.5。

---

### SF-5.2 🟢 `_charge_atomic` rollback 路径里 `User.coins + total_cost` 无 cap 校验

**File**: `nextbot/plugins/lottery.py:706-718`

```python
empty_slots = _find_empty_slots(session_local, user_id, needed_slots)
if len(empty_slots) < needed_slots:
    execute_rowcount(
        session_local,
        update(User)
        .where(User.user_id == user_id)
        .values(coins=User.coins + total_cost),
    )
```

由于 BEGIN IMMEDIATE 序列化，同一事务内 coins 状态可控（X-total_cost → X），不会越过 cap。但**如果未来有人改 `_force_immediate_begin` 或者重构 lottery 为多事务**，这个 refund UPDATE 就成隐患。建议加 `User.coins + total_cost <= MAX_COINS_AMOUNT` defensive cap 注释，或者直接套 lottery 自己已有的 partial-cap 模板（line 760-787）。

---

### SF-5.3 🟢 `_charge_atomic` 成功后 `final_coins` SELECT 在同事务，但 commit 之后；如果 final SELECT 失败（极端），用户已扣 + 已发，但 returns `(False, ...)` —— 不可能，但无 try/except 

**File**: `nextbot/plugins/lottery.py:828-834`

```python
session_local.commit()
final_coins = int(
    session_local.query(User.coins).filter(User.user_id == user_id).scalar() or 0
)
return True, final_coins, ...
```

commit 后 SELECT。如果 commit 之后 connection drop / SELECT raise，函数走 `finally session_local.close()` 然后异常透传到 outer try（line 992-997），用户看到「处理失败，请稍后重试」——但实际上扣费 + 派奖都已经 commit。**用户会重试一次抽奖，再扣一次费**。

**修法**：把 final_coins 改为 commit 前的算术推断（`final_coins = coins_at_start - total_cost + applied_coin_delta`），避免 commit 之后任何 IO。或者把 SELECT 包在自己的 try/except，失败时走"渲染中文案"占位（已扣已发但显示金币不准）。

---

### SF-5.4 🟢 lottery `_check_online_cached` 在 `cmd_plan` 阶段做的 cache，**不会在 Phase 5 重复使用**

**File**: `nextbot/plugins/lottery.py:592-600`、`857-886`

cache 只在 Phase 3（plan 计算 `MAX_LOTTERY_CMD_EXECUTIONS`）使用一次，Phase 5 直接用 `cmd_plan` 里的 servers 列表，不再 check online。OK 设计上对 — Phase 4 期间不可能新增服务器，cache 不再读完全合理。复审通过。

---

### SF-5.5 🟢 `_resolve_probabilities` 归一化把 NULL-weight 奖品的份额重置为 0

**File**: `nextbot/plugins/lottery.py:104-129`

```python
set_prizes = [(p, float(p.weight)) for p in prizes if p.weight is not None]
unset_prizes = [p for p in prizes if p.weight is None]
raw_set_total = sum(...)
if raw_set_total > 100.0:
    scale = 100.0 / raw_set_total
    ...
    set_prizes = [(p, w * scale) for p, w in set_prizes]
    set_total = 100.0
else:
    set_total = max(0.0, raw_set_total)
remaining = max(0.0, 100.0 - set_total)
if unset_prizes:
    per_unset = remaining / len(unset_prizes)
```

如果 `raw_set_total > 100`（admin 配错），归一化后 `set_total=100`，`remaining=0`，**所有 NULL-weight 奖品 per_unset=0** —— 永远抽不到。原意可能是"NULL 奖品兜底分剩余"，归一化让兜底失效。

**Impact**：admin 配错（设定权重之和 > 100）时，原本配 NULL 的兜底奖品悄无声息消失，玩家全压到设定权重的几个奖。trellis-check 之前自修过 lottery 显示一致性是不是这条？需要看 `lottery 显示一致性（trellis-check 自修过）` 那一项。

**修法**：归一化时把 unset_prizes 一起算到归一化分母里（"如果 admin 把权重之和算 > 100，那 unset 也是 admin 错配，让他们一起降"），或者归一化后给 unset 留一个最小份额（例如 0.01），或者直接在归一化前 reject（log error + 不抽）。当前 silent normalize + drop unset 是最隐蔽的语义。

---

### SF-5.6 🟢 lottery `_charge_atomic` 返回值五元组，caller 单条解构（line 838 / 840），未处理 `applied_coin_delta != raw_coin_delta` 时给用户显式提示

**File**: `nextbot/plugins/lottery.py:840-855`

```python
ok, final_coins, item_value_gained, applied_coin_delta, err = await _charge_atomic()
...
coin_delta = applied_coin_delta
raw_coin_delta = 0  # 重新算 raw
```

`coin_delta`（实际入账）和 `raw_coin_delta`（理论应得）的差额只 logged warning（line 790-793 / 822-825 在 `_charge_atomic` 内部），用户层只看到 `coin_delta`。如果用户中了"+50000 金币"奖但实际只入账 0（已封顶 / 余额不足扣不动），渲染页面只显示实际值，用户不会感知"差额"。

**Impact**：用户层信息不对称。lottery 自己产生差额（cap），但用户没线索去追问 admin。这是**已知 trade-off**（comment 说 "避免显示用户'获得 +5000 金币'实际入账 0 的不一致"），但应当有显式提示，例如在 cmd_skip_reasons 旁边加一行 `coin_skip_reasons`。

---

## 6. 跨切面 / 跨文件 section

### SF-X.1 🟠 「`MAX_COINS_AMOUNT` 是单笔上限还是账户上限」语义 drift —— 全局

汇总 SF-1.1 / SF-2.1 / SF-2.2 / SF-3.1 / SF-5.x：

| 文件 | 把 `MAX_COINS_AMOUNT` 当成 |
|---|---|
| `economy.py` `add/remove/transfer` | 单笔上限（不限账户） |
| `red_packet.py` `send` | 单笔上限 |
| `red_packet.py` `grab/withdraw` | 完全没校验 |
| `warehouse.py` `add (value)` | 单价上限（不限账户） |
| `warehouse.py` `recycle` | refund 上限（不限账户） |
| `shop.py` `buy_item / buy_command` | total_price 上限（扣费方向） |
| `lottery.py` `_charge_atomic 加奖 path` | **账户上限**（cap 触发） |

lottery 是孤岛。**这是 12 轮单文件审计漏掉的最严重 cross-handler bug**：组合命令可绕过 lottery 自以为生效的账户上限（先把 coins 推到 200M，再去抽奖触发 lottery cap，结果 cap 完全不起作用，因为 200M >> capped_pos 还是 < User.coins + capped_pos）。

**Recommended fix**（首选）：把 `MAX_COINS_AMOUNT` 明确为「账户上限」，所有 +coins UPDATE 一律加 `User.coins + delta <= MAX_COINS_AMOUNT` 条件 + partial-cap fallback（lottery 模板），并 `User.coins` 上加一个 SQLAlchemy CheckConstraint / 启动期 sanity 数据校验脚本。

或者反方向（次选）：把 lottery 的 account-cap 逻辑去掉，回归"单笔上限"语义，明确允许 coins 漂任意大；但这需要在 schema / db 把 `User.coins` 类型从 INTEGER 升到 BIGINT，并审视所有展示路径有没有溢出风险。

---

### SF-X.2 🟡 `audit_permission_change` 不覆盖金币 / 道具变更，本组无金币审计入口

**File**: `nextbot/audit.py` + 5 文件

12 轮审计针对 permission 模块加了 `audit_permission_change`，但金币 / 仓库变更没有对等 audit helper，全靠各 handler 自己 `logger.info(...)`，格式参差不齐：

- `economy.handle_sign`：`f"签到成功：user_id={user_id} name={user_name} ..."`
- `red_packet.handle_grab`：`f"抢红包成功：user_id={user_id}，name={packet_name}..."`（注意是中文逗号）
- `warehouse._recycle_single`：`f"回收仓库物品成功：user_id={user_id} ..."` （空格分隔）
- `shop._buy_item`：`f"商店购买物品成功：user_id={user_id} shop_id=..."`
- `lottery`：`f"抽奖结果渲染：user_id={user_id} ..."`

字段名 `user_id`、actor、target、before、after 都不统一；事故复盘时按用户 grep 要写 5 套 regex。

**修法**：开 `audit_economy_change(...)`（或 `audit_balance_change`），统一字段名，所有 +/- coins / item 路径走它。本次不要直接复用 `audit_permission_change`（语义错位）。

---

### SF-X.3 🟢 `_screenshot_semaphore` 都是 `Semaphore(2)`，每 handler 一份

**Files**:
- `red_packet.py:57` `_red_packet_screenshot_semaphore = asyncio.Semaphore(2)`
- `warehouse.py:70` `_warehouse_screenshot_semaphore = asyncio.Semaphore(2)`
- `shop.py:100` `_shop_screenshot_semaphore = asyncio.Semaphore(2)`
- `lottery.py:56` `_lottery_screenshot_semaphore = asyncio.Semaphore(2)`

复审：每 handler 隔离的 module-level semaphore **设计上正确**——不同业务不抢资源。但全局总并发可达 8 个截图任务，而 Playwright pool 通常 1-4 worker。建议确认 `screenshot_url` 内部是否有再一层全局 semaphore 防 OOM；如果没有，且实测 4+ 并发会撑爆 Playwright，应该在 `screenshot_render.py` 里加个 module-level master semaphore（例如 `Semaphore(4)`），不影响调用方语义。

复审通过（design 正确，仅运行配置层面建议）。

---

### SF-X.4 🟠 shop 没用 `server_broadcast.broadcast`（与 lottery 不一致）

见 SF-4.1 / SF-4.2。明确列在跨切面 section 因为：**12 轮审计期间 lottery 迁移到 broadcast 了，但 shop._buy_command 同模式的 fan-out 没一起迁移**。这是任务描述里直接问的问题。

---

### SF-X.5 🟢 `screenshot_render.render_and_send_screenshot` 边界 case：`semaphore=None` 路径

**File**: `nextbot/screenshot_render.py:70-81`

```python
if semaphore is None:
    return await _render_and_send_inner(...)
async with semaphore:
    return await _render_and_send_inner(...)
```

5 个金币 handler 都传了 semaphore，none 路径在本组未触发，复审通过。但要注意未来 handler 接入时**忘了传 semaphore** = 走 None 路径，这是静默失败模式。建议把签名改为 `semaphore: asyncio.Semaphore`（必填，无默认 None），或者在 docstring 顶部加红字警告。

---

### SF-X.6 🟢 `MAX_LOTTERY_CMD_EXECUTIONS=200` vs `MAX_BUY_COUNT * server_count` 无 cap

总 RPC 配额：

| handler | 单次最大 RPC |
|---|---|
| lottery | 200（写死） |
| shop._buy_command | `MAX_BUY_COUNT × N_servers` = 9999 × N |
| red_packet | 0（无 RPC） |
| warehouse._claim_many | `len(slot_indexes) ≤ 100`（间接 cap） |

shop 是离群点，见 SF-4.2。

---

### SF-X.7 🟢 `User.coins` 列没有 DB-level CHECK 约束

**File**: `nextbot/db.py:141`

```python
coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

无 `CheckConstraint(User.coins >= 0)` 也无 `CheckConstraint(User.coins <= MAX_COINS_AMOUNT)`。所有保护都在 application 层。如果某个 handler 漏了条件 UPDATE，DB 不会兜底。

考虑到：
- 已有 BEGIN IMMEDIATE 序列化所有写
- 已有大量 application-level 条件 UPDATE
- DB-level CHECK 在 SQLite 是支持的

加上去成本低、收益（防御 future regression）明显。**修法**：在 db.py User 的 `__table_args__` 里加：
```python
__table_args__ = (
    CheckConstraint("coins >= 0", name="ck_user_coins_nonneg"),
    # 如果决定 cap 是账户上限：
    CheckConstraint("coins <= 100000000", name="ck_user_coins_max"),
)
```
注意 SQLite 已有数据可能违反，迁移要分两步（先放警告 log，再 ALTER）。

---

## 总结

- **🟠 严重 cross-handler bug**：SF-X.1（MAX_COINS_AMOUNT 语义 drift），SF-X.4 / SF-4.1 / SF-4.2（shop 没迁移到 broadcast 且无 RPC cap），SF-2.1 / SF-2.2 / SF-3.1（红包 + 仓库可推 coins 越 cap）。
- **🟡 设计偏差**：SF-X.2（金币审计入口缺失），SF-4.3（shop require_online=False 语义模糊）。
- **🟢 加固建议**：SF-2.3 / SF-2.4 / SF-3.2 / SF-3.3 / SF-3.4 / SF-4.4 / SF-4.5 / SF-5.1 ~ SF-5.6 / SF-X.3 / SF-X.5 / SF-X.6 / SF-X.7。
- **复审通过 / 已确认设计正确**：SF-1.2（audit_permission_change 不应被金币用），warehouse 的 lock 嵌套 deadlock 防御，红包发送 → 抢的 atomic 链路（除了 SF-2.4 的 rowcount 校验缺失），lottery 的 BEGIN IMMEDIATE + TOCTOU 重校验 + partial-cap 模板（在 lottery 内部正确，仅 cross-handler 语义 drift 有问题）。

12 轮单文件审计后剩下的 issue 几乎都集中在「跨 handler 协调」这一层：
- 金币上限语义没有项目级别共识（SF-X.1）
- broadcast / RPC cap helper 没有规定哪些 handler 必须用（SF-X.4）
- 金币审计统一入口缺失（SF-X.2）

建议在 `.trellis/spec/backend/` 加一份 `economy-conventions.md` 把这三件事写成项目级约束，避免下一轮 audit 又踩同样的坑。
