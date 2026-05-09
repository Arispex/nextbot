# Post-Final-Sweep 回归审计 — Findings

- **Query**: 复查 final-sweep 14 项修复有无引入新 bug + 同形模式有无遗漏
- **Scope**: internal grep 验证 + 关键 helper 源码确认
- **Date**: 2026-05-09
- **Files audited**: `economy.py` / `lottery.py` / `warehouse.py` / `red_packet.py` / `dice.py` / `guess_number.py` / `rob.py` / `rob_protection.py` / `shop.py` / `bot.py` / `db.py` / `permissions.py` / `permission_manager.py` / `group_manager.py` / `ban.py` / `user_manager.py` / `screenshot_render.py`

---

## 总结

| Severity | Count | 说明 |
|---|---|---|
| 🔴 Critical | 0 | — |
| 🟠 High | 1 | dice / guess_number / rob 的 payout 加币路径未走 `add_coins_with_cap`，可能突破 MAX_COINS_AMOUNT cap |
| 🟡 Medium | 1 | lottery `_charge_atomic` 的 partial-cap fallback rowcount 未校验，TOCTOU 边界下 `applied_*` 与 DB 实际值不一致 |
| 🟢 Low | 2 | screenshot_render 文档与代码不一致；user_manager.rename audit 在白名单同步前 |
| ℹ️ Info | 6 | checklist 各项复查通过 |

预期"critical / high 应为 0"未达成，**PV-1.2 是 high**，需主代理二次复查。

---

## PV-1.1 `add_coins_with_cap` 接入完整性 — 复查通过 ℹ️

✅ 6 个 callsite 全部正确解构 `(applied, capped)` 并消费：

| File:line | 调用上下文 | applied 用途 | capped 用途 |
|---|---|---|---|
| `economy.py:467` | transfer 加 target | 显示成功金额 + refund 计算 | 触发 refund 回退给 sender |
| `economy.py:580` | admin add_coins | 显示成功金额 + 日志 | 显示 "已触账户上限" 提示 |
| `warehouse.py:1101` | 单格回收 refund | 显示金币 + 日志 | 显示触顶提示 |
| `warehouse.py:1198` | 批量回收 refund | 显示金币 + 日志 | 显示触顶提示 |
| `red_packet.py:349` | 抢红包 grab | 显示金额 + 日志 | 触发触顶 reply 提示 |
| `red_packet.py:465` | 收回红包 refund | 显示金额 + 日志 | 触发触顶 reply 提示 |

✅ helper 自身（`economy.py:65-121`）实现正确：先尝试整额 → rowcount=0 走 partial → partial UPDATE 仍 rowcount=0 兜底返回 (0, True)。每条分支都校验 rowcount 才声明 applied 值。

---

## PV-1.2 `User.coins +` 直接加币遗漏 helper — 🟠 High

grep `coins=User.coins +` 命中 9 处不走 helper 的加币路径。其中 6 处可能在 `User.coins ≈ MAX_COINS_AMOUNT` 时突破账户上限：

### 命中分析

| File:line | Context | 加币来源 | 是否可突破 cap | Severity |
|---|---|---|---|---|
| `economy.py:270`, `:299` | sign payout | `total_reward = base_reward + streak_reward` | ❌ 已带 `User.coins + total_reward <= MAX_COINS_AMOUNT` 守护 | OK |
| `economy.py:476` | transfer refund 给 sender | 仅退回 sender 原本扣掉的金额，sender 余额 ≤ before - applied < before | ❌ sender 余额单调下降 | OK |
| `lottery.py:712` | charge 失败回退 | 退回的是用户原本扣掉的 `total_cost`，不会超 cap | ❌ | OK |
| `lottery.py:769`, `:786`, `:803`, `:819` | coin 奖励派发 | 都带 `User.coins + X <= MAX_COINS_AMOUNT` 或 `>= 0` 条件 | ❌ | OK |
| **`dice.py:203`, `:215`, `:235`** | 押注赢回 + payout | `User.coins + payout` 无 cap 守护 | ⚠️ 可突破 | 🟠 |
| **`guess_number.py:239`, `:251`, `:271`** | 押注赢回 + payout | `User.coins + payout` 无 cap 守护 | ⚠️ 可突破 | 🟠 |
| **`rob.py:305`, `:318`, `:356`** | 抢劫成功 / 反抢 victim 回血 | `User.coins + amount` 无 cap 守护 | ⚠️ 可突破 | 🟠 |

### 复现（dice 为例，`dice.py:198-208`）

```python
if net > 0:
    session.execute(
        update(User)
        .where(User.user_id == user_id)
        .values(
            coins=User.coins + payout,   # ← 无 cap 守护
            dice_total_count=User.dice_total_count + 1,
            ...
        )
    )
```

**Trigger**：用户 `coins = 99_999_950`，押注 `cost = 100`（已扣到 99_999_850），猜中豹子 → `payout = 100 * 10 = 1000` → 写入后余额 = 99_999_850 + 1000 = 100_000_850，超 cap 850。

### Impact

- 单笔越界量取决于 game payout 上界。dice / guess_number 受 `min/max_cost + multiplier` 约束（缺 max_cost 时 cost ≤ MAX_COINS_AMOUNT，配合 multiplier=10/100 → payout 可达 1e10），rob 受 victim 余额约束（victim ≤ MAX_COINS_AMOUNT，counter / police / fail 路径基于 `robber_coins * percent // 100`，单笔越界 ≤ MAX_COINS_AMOUNT）。
- 经济不变量"任意时刻 coins ≤ MAX_COINS_AMOUNT" 被绕过。后续读 `coins` 比对会出现> cap 数据。
- 与 economy / lottery / warehouse / red_packet 已修的 SF-X.1 防线不对称——同一用户从 dice / rob 赢回的金币不受 cap，从其他路径加的受 cap，治理面不一致。

### 修法建议

把这 9 处都改成：要么用 `add_coins_with_cap(session, user_id, payout)` + 把 `dice_total_count / dice_total_gain / ...` 单独一次 UPDATE；要么照抄 sign 的"先尝试带 cap 条件 UPDATE，rowcount=0 走 partial"模式。统一成 helper 模式更便于以后升级 cap 语义（partial 通知文案 / capped flag 上报）。

注：本项不是 final-sweep 引入的回归；是历史遗漏（此前 14 批审计未把 dice / guess_number / rob 纳入 SF-X.1 范围），final-sweep 仅修了 economy / red_packet / warehouse / lottery 4 个域。

---

## PV-2 `bot.py init_db` 幂等性 — 复查通过 ℹ️

✅ `bot.py:137-145`：`@driver.on_startup` 单一入口，`init_db()` 内部按序调用所有 `ensure_*_schema`。
✅ `db.py:424-444` `init_db()` 完整列出 16 个 ensure 函数。
✅ 全部 ensure_* 函数都用 `IF NOT EXISTS` 或先 `PRAGMA table_info` 检查 column 再 ALTER：
  - `ensure_*_index_schema` 系列：`CREATE [UNIQUE] INDEX IF NOT EXISTS` ✓
  - `ensure_*_schema` 系列（user / warehouse / shop / red_packet）：`PRAGMA table_info` → `ALTER TABLE ADD COLUMN` 仅在 col 不在时添加 ✓
  - `ensure_user_signin_schema`：`PRAGMA + try ALTER DROP COLUMN + except logger.warning` ✓
  - `ensure_default_groups` / `ensure_default_stats`：query first → add only if None ✓

✅ 没有发现重复运行会失败的语句。重复启动安全。

---

## PV-3 shop `_buy_command` broadcast 边界 — 复查通过 ℹ️

逐项验证：

| 边界 | 行为 | 验证 |
|---|---|---|
| `online_servers` 为空（require_online=True 全部离线） | `shop.py:749-760` early return，附带 offline_reasons 列表 | ✓ |
| `online_servers` 为空（require_online=False） | 由 `shop.py:733-735` 的 `if not servers` 拦截，不走到 broadcast | ✓ |
| `buy_count <= 0` | `shop.py:480-484` parse-time 拒绝 | ✓ |
| `buy_count > MAX_BUY_COUNT (9999)` | `shop.py:486-491` 拒绝 | ✓ |
| `online_servers × buy_count > 200` | `shop.py:765-774` 在 charge 前拒绝，附带具体数字 | ✓ |
| 全失败 | `shop.py:851-871` CRITICAL log + reply head 切换为 `reply_failure("购买", "所有服务器执行失败")` + 引导联系管理员退款 | ✓ |
| 部分成功 | `shop.py:874-880` 走 reply_success 头但 lines 列出每服 ✅/❌ | ✓ |
| broadcast outcome 内部异常（`outcome.payload is None`） | `shop.py:838-842` 当作 buy_count 次失败计入 exec_results，避免漏统计 | ✓ |

✅ broadcast 改造正确，没有边界漏洞。

---

## PV-4 `DANGEROUS_PERMISSION_PREFIXES` 扩容后误拦风险 — 复查通过 ℹ️

`permissions.py:93-118` blocklist 包含 18 项（permission/group 管理 11 项 + 高危管理 7 项）。

### 误拦校验

`is_dangerous_permission` 仅在 `permission_manager.py:170` / `group_manager.py:682,786` 三处被调用，全部在「授予 / 撤销 permission key」时校验，**不影响**用户调用命令的 `_match_permission`。所以以下合法 guest 权限不会被误拦：

| 权限 key | exact 匹配 | `.* ` 后缀含 dangerous prefix | 结果 |
|---|---|---|---|
| `economy.sign` | ❌ | ❌ | False ✓ |
| `economy.transfer` | ❌ | ❌ | False ✓ |
| `economy.guess_number` / `economy.dice` / `economy.rob` | ❌ | ❌ | False ✓ |
| `economy.red_packet.grab` / `*.send` / `*.withdraw` / `*.list*` | ❌ | ❌ | False ✓ |
| `leaderboard.*` (各项 leaf) | ❌ | ❌ | False ✓ |
| `about` / `menu.root` / `menu.search` | ❌ | ❌ | False ✓ |
| `lottery.draw` / `lottery.list` / `lottery.view` | ❌ | ❌ | False ✓ |
| `shop.buy` / `shop.list` / `shop.view` | ❌ | ❌ | False ✓ |
| `warehouse.*_self` / `warehouse.list_user` | ❌ | ❌ | False ✓ |
| `player_query.*` | ❌ | ❌ | False ✓ |
| `system.tutorial` / `user.register` / `user.info.*` | ❌ | ❌ | False ✓ |

### 危险 prefix 正确匹配验证

| 输入 key | 期望 | 实际 |
|---|---|---|
| `server_tools.execute` | True | ✓ exact match |
| `server_tools.*` (wildcard) | True | ✓ prefix `server_tools.` 覆盖 `server_tools.execute` |
| `server.add` / `server.delete` | True | ✓ exact |
| `server.*` | True | ✓ 覆盖 `server.add` 等 |
| `admin.ban` / `admin.unban` / `admin.rename` | True | ✓ exact |
| `admin.*` | True | ✓ 覆盖 |
| `economy.coins.add` / `*.remove` | True | ✓ exact |
| `economy.coins.*` | True | ✓ 覆盖 |
| `economy.*` | True | ✓ 覆盖 `economy.coins.add` |
| `user.whitelist.sync` | True | ✓ exact |
| `user.whitelist.*` | True | ✓ 覆盖 |
| `*` | True | ✓ 万能拒绝分支 |

✅ 没有误拦合法权限，新加 key 全部正确匹配。

### 注意事项 ℹ️

`user.whitelist.sync` 同时在 `DEFAULT_GUEST_PERMISSIONS`（`db.py:89`）里 — 即 guest 默认就持有此权限。这意味着：
- guest 可以自由触发 `同步白名单` 命令（如果存在），但**不能被授予 / 撤销**该权限（POLA），需要 owner。
- 这是 acceptable trade-off：guest 默认已持有，新 admin 也无法 grant 给"自己持有 + 别人没有"的目标，这条线由 hierarchy guard 接管而非 dangerous 拦截。
- 若产品意图是"该权限不能 guest 默认拥有"，需要把 `user.whitelist.sync` 从 DEFAULT_GUEST_PERMISSIONS 移除。当前未发现明确 spec 要求，标记为 info。

---

## PV-5 同步访客权限 retry confirm-time live diff — 复查通过 ℹ️

`permission_manager.py:762-872` `handle_sync_guest_perms_confirm` 流程：

1. preview-time（`handle_sync_guest_perms`）：算 `missing = DEFAULT - current`，存到 `matcher.state["sync_missing"]`，发预览 + 提示回 "确认"。
2. confirm-time：每次 retry 重读 guest live row → 重新计算 `actually_added = sorted(set(missing) - current)`（live diff），即"preview 后 webui 已经手动加了部分 missing key"的情况会被正确扣除。
3. 若 `actually_added == []` → no-op success（不是 audit 事件），合理。
4. 若 conditional UPDATE rowcount=1 → break + commit + audit。
5. 若 5 次 retry 全失败 → `logger.warning(... retry 耗尽 ...)` + reply "并发冲突，请稍后重试"。

✅ retry / live diff 行为符合预期，audit context 用 confirm-time 的 `old_csv` / `new_csv` / `actually_added`，不会出现 stale。

✅ 重置访客权限 `handle_reset_guest_perms_confirm`（`permission_manager.py:946-1048`）同样在 confirm-time 用 `old_csv` / `new_csv` 重新算 diff（`SS-5.1` 改造 `actual_removed` / `actual_added`），也正确。

---

## PV-6 POLA 层级护栏对称性 — 复查通过 ℹ️

| 命令 | 层级护栏 site | owner 短路 | 复用 session |
|---|---|---|---|
| `修改用户身份组` (PMB-3.1) | `permission_manager.py:521-551` | ✓ `if not is_owner(operator_id)` | ✓ `_get_effective_permissions_in_session(session, operator_id)` |
| `继承身份组` (SS-4.1) | `group_manager.py:478-502` | ✓ `if not is_owner(operator_id)` | ✓ `_get_effective_permissions_in_session(session, operator_id)` |
| `添加身份组权限` (PMA-6.1) | `group_manager.py:670+` | ✓ `if not is_owner(operator_id)` | ✓ |
| `撤销身份组权限` (PMA-6.2) | `group_manager.py:784+` | ✓ `if not is_owner(operator_id)` | ✓ |
| `添加用户权限` | `permission_manager.py:170+` | ✓ | ✓ |

✅ 对称。两个层级护栏（修改用户组 + 继承身份组）逻辑一致：computed `forbidden = target_perms - operator_perms`，非空 → 拒绝 + audit denied + 短文案预览前 5 个。owner 短路在所有 site 都是首位检查，覆盖完整。

---

## PV-7 `audit_permission_change` commit 后调用 — 复查通过 ℹ️

| 入口 | audit site | 时机 | failure denied audit |
|---|---|---|---|
| `ban.handle_ban` | `ban.py:111-118` | `apply_ban_to_db` 返回成功后 | ✓ owner_protected 路径有 `user.ban.denied`（line 92-97） |
| `ban.handle_unban` | `ban.py:283-290` | `apply_unban_to_db` 返回成功后 | （not_found / not_banned 是 idempotent fail 不需要 denied audit） |
| `user_manager.handle_rename` | `user_manager.py:509-515` | session.commit 成功后 | （白名单同步失败不影响 DB rename，audit 反映真实状态正确） |
| `permission_manager.handle_set_user_group` | `:601-608` 成功 + `:540-550` denied | commit 后 / hierarchy 拒绝时 | ✓ |
| `permission_manager.handle_add/remove_user_perm` | `:296` / `:437` | commit 后 | ✓ |
| `permission_manager.handle_sync_guest_perms_confirm` | `:855-862` | commit 后 | （并发冲突重试耗尽路径 logger.warning 但无 denied audit；可接受，正常 retry 不算安全事件） |
| `permission_manager.handle_reset_guest_perms_confirm` | `:1040+` | commit 后 / no-op 不审计 | （并发冲突同上） |
| `group_manager.handle_inherit_group` | `:537-544` 成功 + `:484-494` denied | commit 后 / hierarchy 拒绝时 | ✓ |

✅ 关键 mutation handler 的 audit 都在 commit 后，且失败 denied 路径覆盖完整。

⚠️ **次要观察**：`user_manager.handle_rename` 的 audit 在白名单同步前调用（`user_manager.py:509` 早于 line 533 的 `asyncio.gather(*_rename_one_whitelist...)`）。如果白名单同步部分失败，audit 仍记 success，没有"白名单同步失败"附加 context。这是可接受的 — DB rename 已确认成功，audit 反映"主操作已完成"是准确的；白名单同步本身有自己的日志（`user_manager.py:549-551`）+ 用户回复行（`:537-547`）。本项标记为 ℹ️，无需修复。

---

## PV-8 `screenshot_render` 文案 — 复查通过 ℹ️ + 1 项 🟢 文档

✅ `screenshot_render.py:138` 默认 caption = "截图已生成"，与 `reply_success(failure_action, "截图已生成")` 拼出形如 `✅ 查询成功，截图已生成` / `✅ 抽奖成功，截图已生成`，**不会**出现 "截图已生成，截图已生成" 重复。

✅ 全部 14 个 callsite 都使用默认 caption（`success_caption=None` 即默认 fallback），未发现传入冗余值。

### 🟢 PV-8.1 — 文档与代码不一致

`screenshot_render.py:57` docstring 仍写：
> success_caption: 非 V11 适配器的成功提示语，None 时使用默认 "截图生成成功"。

但实际代码默认值是 `"截图已生成"`（line 138）。docstring 漏改。

**Severity**: 🟢 (low / cosmetic)
**Impact**: 仅影响阅读 docstring 的开发者，不影响运行时行为。
**修法**: docstring 改成 "None 时使用默认 '截图已生成'。"

---

## 额外发现：lottery `_charge_atomic` 与 helper 行为差异 — 🟡 Medium

### PV-X.1 — `applied_pos` / `applied_neg` 在 partial UPDATE rowcount=0 时仍被声明为 partial 值

**File:line**: `nextbot/plugins/lottery.py:781-788` (positive path) + `:814-821` (negative path)

```python
# positive path（lottery.py:781-788）：
if partial > 0:
    execute_rowcount(
        session_local,
        update(User)
        .where(User.user_id == user_id, User.coins + partial <= MAX_COINS_AMOUNT)
        .values(coins=User.coins + partial),
    )
    applied_pos = partial   # ← rowcount 未校验，TOCTOU 下可能 UPDATE 实际未执行
```

### Impact

`add_coins_with_cap`（economy.py:103-116）的 partial UPDATE 之后**校验** `if rowcount > 0:` 才声明 `return partial, True`，否则走兜底日志 + `return 0, True`。

lottery 自己的 `_charge_atomic` 在 partial UPDATE 后直接 `applied_pos = partial`，**没有**检查 rowcount。这意味着极端 TOCTOU（在 SELECT coins_now 之后、partial UPDATE 之前，另一个并发请求把余额涨到 cap 附近，导致 partial UPDATE 的 `coins + partial <= MAX` 守护 false）下：
- DB 实际 coins 没动
- `applied_pos = partial` 误声明
- `applied_coin_delta = applied_pos + applied_neg` 计入响应展示金额
- **但 `final_coins`（line 829-831）从 DB 重新 SELECT** → 用户看到的 "当前金币" 是真实值

→ 用户看到 "+X 金币" 但 "当前金币" 加起来对不上（差 partial 部分）。

### 复现

1. 用户 coins = 99_999_900，抽奖中了 +200 coin 奖励 → `coin_delta_pos = 200`
2. capped UPDATE `coins + 200 <= MAX` → false（99_999_900 + 200 > 100_000_000）→ rowcount=0
3. SELECT coins_now=99_999_900 → room=100，partial=min(200, 100)=100
4. 另一个并发请求（如签到）刚好把 coins 加到 100_000_000
5. partial UPDATE `coins + 100 <= MAX` → false → rowcount=0
6. `applied_pos = 100` 但 DB 实际仍是 100_000_000
7. `applied_coin_delta = 100`，结果页显示 "+100 金币"，但 "当前金币 100_000_000"，加减对不上 100

### Severity

🟡 Medium。条件极端（多并发刚好踩到 cap），但与 helper 行为不一致是事实，且 final-sweep 文档明确写"lottery 之前自己实现 partial-cap"——这次 sweep 的目标本身就是把 lottery 与 helper 对齐。

### 修法

加 rowcount 校验：

```python
if partial > 0:
    partial_rowcount = execute_rowcount(...)
    if partial_rowcount > 0:
        applied_pos = partial
    # 否则保持 applied_pos = 0
```

Negative path（line 814-821）做对称修改。或最干净——把整段重构成调用 `add_coins_with_cap`（正向）/ 类似 helper 模式（负向）。

---

## Checklist 复查结论

| Checklist 项 | 状态 |
|---|---|
| 1. `add_coins_with_cap` 调用方都正确使用返回值 `(applied, capped)` | ✓ ℹ️ PV-1.1 通过 |
| 2. 所有 `update(User).values(coins=User.coins + N)` 都接入 helper | ✗ 🟠 PV-1.2 dice / guess_number / rob 9 处遗漏（历史，非 final-sweep 引入） |
| 3. `audit_permission_change` 调用 site 的 actor / before / after 完整 | ✓ ℹ️ PV-7 通过 |
| 4. `is_dangerous_permission` 不会误拦合法权限 | ✓ ℹ️ PV-4 通过 |
| 5. `init_db()` 幂等性 | ✓ ℹ️ PV-2 通过 |
| 6. shop `MAX_SHOP_CMD_EXECUTIONS=200` 拦下 9999×N 场景 | ✓ ℹ️ PV-3 通过 |
| 7. POLA 层级护栏（PMB-3.1 + SS-4.1）对称 + owner 短路 | ✓ ℹ️ PV-6 通过 |
| 8. screenshot_render 非 V11 fallback 文案 | ✓ ℹ️ PV-8 通过；🟢 PV-8.1 docstring 漏改 |
| 9. lottery `_charge_atomic` 与 helper 行为对齐 | ✗ 🟡 PV-X.1 partial-cap rowcount 未校验 |

---

## 主代理建议

1. **PV-1.2 (🟠 High)** 是 final-sweep 显式漏掉的 4 个 plugin（dice / guess_number / rob）。优先级最高。如果业务上"小游戏 / 抢劫赢钱也应受 MAX_COINS_AMOUNT 约束"，应该在下一批改造里把这 9 处加上 cap 守护或改用 helper。
2. **PV-X.1 (🟡 Medium)** 是 lottery 自己实现的 partial-cap 与 helper 行为有 1 处对齐缺失。推荐重构为 helper 调用以彻底对齐；最小化修复是加 2 处 rowcount 校验。
3. **PV-8.1 (🟢)** 是 docstring 单行修复。
4. checklist 1 / 2（除遗漏外）/ 3 / 4 / 5 / 6 / 7 / 8 全部通过 → final-sweep 14 项修复**没有**引入新回归。

预期结果：critical=0 ✅，high=1（历史遗漏，不算 final-sweep 回归）❗，需主代理决定是否纳入下一批。

---

## Caveats

- 本次审计未对 dice / guess_number / rob 内部的统计字段（`*_total_count` / `*_win_count` / `*_total_gain` 等）做溢出审计——它们用 INTEGER 列且本来就累加，单次审计不展开。如果 admin 滥用配置导致 stat 字段溢出（理论 64-bit signed int 上界 ~9.2e18，dice 单次最多 +1e8 gain，需要 1e10 次 dice 才能溢出），属于极远期问题。
- 未对所有 `command_template.replace("{player}", ...)` 做注入面审计；`player_name` 在 lottery 已加 `_player_name_safe_for_command`（`lottery.py:65-75`），shop 没有同等校验（`shop.py:820 cmd = command_template.replace("{player}", player_name)`）。但 shop 的 player_name 同样来自 User.name 注册时已被 Terraria 客户端约束。Acceptable trade-off，未列入 finding。
- 审计采用 grep + 抽读关键文件方式，没有 100% 覆盖 23 个 plugin 的所有路径。主代理如对其他 mutation 路径有疑问可单独追加调查。
