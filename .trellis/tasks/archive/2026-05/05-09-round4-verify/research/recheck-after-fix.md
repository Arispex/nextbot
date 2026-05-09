# Round 4 修后复查

**日期**：2026-05-09
**复查范围**：Round 4 五大模块修复（M1 ~ M5）
**核查方式**：git diff 全文 + 逐项对照 PRD spec + pyright + ruff + 关键代码路径手动 trace

---

## 复查结论速览

| 类别 | 数量 | 备注 |
|---|---|---|
| Bugs introduced（引入 bug）| 0 | |
| Fixes incomplete（修不彻底）| 0 | |
| Quality improvements（可优化项）| 2 | 均为低优先级 polish，可纳入下游任务 |

**整体判定**：5 项修复全部正确落地，pyright 0 errors，无新 ruff E501，无 V11 行为破坏。可接入主线。

---

## M1：dice / guess `applied_net` 修复

### 验证项

| 项 | 期望 | 实际 |
|---|---|---|
| `net = payout - cost` 仍保留作为分支判定 | ✓ | dice:209、guess:244 |
| `applied_net = applied_payout - cost` 用于 stats | ✓ | dice:210、guess:245 |
| 赢路径 `dice_total_gain += max(0, applied_net)` | ✓ | dice:219、guess:254 |
| 输路径 `dice_total_loss += abs(applied_net)` | ✓ | dice:228、guess:263 |
| reply 行用 `applied_net`（已提到 try 外） | ✓ | dice:279、guess:299 |
| `cost > MAX_COINS_AMOUNT` 入口 cap 仍在 | ✓ | dice:129、guess:同 |
| 触顶 warning 仍 emit | ✓ | dice:202-204、guess:237-239 |

### 数学边界回溯

- 赢且全部入账：`applied_net = net > 0` → `gain += net` ✓
- 赢且部分 cap：`0 ≤ applied_net < net` → `gain += applied_net`（非 net）✓
- 赢且全 cap（applied_payout=0）：`applied_net = -cost` → `max(0, applied_net) = 0`，`win_count += 1`，**stat 归零是正确语义**（与 PRD 一致）
- 输（payout=0）：`applied_net = -cost`，`abs = cost` → `loss += cost` ✓
- 输（guess "偏离"，payout = cost//2）且部分 cap：`applied_net = applied_payout - cost`（负数）`abs = cost - applied_payout` ✓
- 平：分支不动 stats 字段 ✓

### 已知 trade-off（不修）

- 赢路径全 cap 时 `gain += 0` 而 `loss` 不动，user 实际亏损 `cost`，total_gain - total_loss 与 coins 累计 delta 在该极端场景下不严格对账。但项目原 PRD 明确"避免 win_count 漏计"优先于 stats 闭环。

---

## M2：mutation handler `except Exception` 显式 rollback

### 覆盖核查

| 文件 | 位置 | 已加 rollback |
|---|---|---|
| economy.py | 440（签到）、585（转账）、663（添加）、790（扣除）| ✓ × 4 |
| red_packet.py | 226（发）、395（抢）、510（收回）| ✓ × 3 |
| dice.py | 244 | ✓ |
| guess_number.py | 279 | ✓ |
| rob.py | 468 | ✓ |
| rob_protection.py | 125 | ✓ |

**总计 11 个 mutation handler 全部修齐**，对照 R4R-2.1 列表的 6+ 处实际超额覆盖。

### 顺序核查

每个修复点格式统一为：

```python
except Exception:  # noqa: BLE001
    session.rollback()           # 1. 显式 rollback
    logger.exception(...)        # 2. 异常 traceback
    try:
        await bot.send(... reply_failure(...))  # 3. 用户告警
    except Exception:
        pass
    return
```

✓ 顺序与 user_manager IntegrityError 风格统一。

### 不需要 rollback 的 except（核查正确跳过）

- `lottery.py:318/457/965`：handle_lottery_list / view 是只读 + screenshot，handle_lottery_draw 用内层 `_charge_atomic` 自管 session
- `red_packet.py:656/760`：read-only handler
- `lottery.py:934/946/954`：bot.send 失败兜底，与 session 无关

---

## M3：`asyncio.gather(return_exceptions=True)` fan-out

### 覆盖核查

| 文件 | 位置 | gather call | exception 处理 |
|---|---|---|---|
| user_manager.py | 137-140（`_sync_whitelist_to_all_servers`）| ✓ | 转 `(server, "fail", "同步异常")` 与 `tuple[Server, SyncStatus, str]` shape 一致 |
| user_manager.py | 579-582（`handle_rename` 内联）| ✓ | 转 `lines.append(... ❌ 同步异常)` + continue，避免 unpack |
| leaderboard.py | 794-796（`handle_total_online_time_leaderboard`）| ✓ | 转 `(server, None)` 与 `tuple[Server, list \| None]` shape 一致 |

### 顺序保留 & 异常字段格式

- 三处都用 `zip(servers, raw_results, strict=True)`，**ordering 保留** ✓
- logger.warning 全部包含 `server_id={server.id}` + `reason={raw!r}`，符合统一日志规范 ✓
- 注释引用同源（lottery / shop fan-out 模板对齐）✓

### 跨域差异（acceptable）

- `server_broadcast.py:66-68`：原 `return_exceptions=False`，`_wrap` 已 catch BLE001（已知安全，未列入修法）
- `permission_manager.py:661`：`_fetch_nickname_with_timeout` 已 catch Exception（已知安全）

---

## M4：lottery `_normalize_player_name` NFKC 折叠

### 实现一致性

| 实现 | 内容 |
|---|---|
| `lottery.py:75` | `unicodedata.normalize("NFKC", str(name)).strip().casefold()` |
| `shop.py` | 同 |
| `warehouse.py` | 同（注释略有差异，行为完全一致）|

✓ 三处实现完全相同。

### 调用点

`_check_player_online`（lottery.py:182-189）：
- target = normalize(player_name) ✓
- 遍历每个 player 时 normalize(nickname) 再比对 ✓
- 替换原 `name_lower = player_name.lower()` + `nickname.lower()` 路径

### 副作用核查

- 全 codebase grep `player_name.lower()` 在 lottery.py 已无残留 ✓
- 抽奖 phase 3 `player_name = str(user.name)`（lottery.py:550）只用于 RPC 模板替换，不进入名字比对，**不需要 normalize** ✓

---

## M5：CSS `overflow-wrap: break-word` polish

### 文件改动

#### lottery_result.html（96-108）

- 删除 `overflow: hidden / text-overflow: ellipsis / white-space: nowrap`
- 加 `overflow-wrap: break-word; word-break: break-all`
- 注释清晰说明原因

#### user_info.html（129-142）

- 保留原 `overflow-wrap: break-word`
- 改 `word-break: break-word` → `word-break: break-all`（注释说明 Chromium 兼容）

### 不破坏现有渲染

- 二者改动均限定在 `.stat-value` selector 内
- 不影响外层 grid / card 布局
- `min-width: 0`（lottery_result.html:85）已为 flex 子元素打开 shrink，配合 `break-all` 可正确换行
- 100 亿数字（带千分位 `10,000,000,000` = 14 字符）在 18px / 28px 字号下能完整换行而不截断 ✓

### 复查通过

- 没有删掉 `font-feature-settings: "tnum"`（数字等宽不动）
- 没有改 line-height、letter-spacing
- `flex` 布局父级未受影响

---

## 工具校验结果

| 工具 | 命令 | 结果 |
|---|---|---|
| `python -m compileall` | 9 个文件 | OK（0 错）|
| `pyright` | 9 个文件 | **0 errors, 0 warnings, 0 informations** |
| `ruff check` | 9 个文件 | 428 errors（**与基线 428 完全一致**，无新增）|

ruff 残留全部为预先就存在的 E501 / try-except-pass，与本次修改无关。

---

## Quality improvements（建议下游 polish）

### Q1：rename 路径异常文案与 sync 路径不对称

- **位置**：user_manager.py:589
- **现状**：`f"{server.id}.{server.name}：❌ 同步异常"`
- **对比**：sync 路径返回 `(server, "fail", "同步异常")` 后渲染为 `... ❌ 同步失败，同步异常`
- **建议**：rename 异常分支统一改为 `❌ 同步失败，异常`，与 sync 路径风格对齐
- **优先级**：低（语义清晰，用户都能理解，不影响功能）

### Q2：`SyncStatus` 失败 reason 文本可下沉到常量

- **位置**：user_manager.py:147 `"同步异常"` 字面量
- **建议**：与 `_sync_one_whitelist` 内的 `"无法连接服务器"` 等失败原因常量化，避免散落
- **优先级**：低（refactor，不阻塞）

---

## Bugs introduced

无。

## Fixes incomplete

无。M1~M5 全部按 PRD 验收点落地。

---

## 最终结论

Round 4 五项修复全部通过复查：

1. **M1（R4R-7.1）** — applied_net 在 cap 后正确流入 stats，与 R3E-1 红包蒸发同模式对齐
2. **M2（R4R-2.1/2.2）** — 11 个 mutation handler 全部显式 rollback，超额覆盖原 6+ 估算
3. **M3（R4R-B.1）** — 3 处 fan-out gather 加 `return_exceptions=True`，fallback tuple shape 与原 return type 一致
4. **M4（R4R-5.1）** — lottery 的 `_check_player_online` 与 shop / warehouse 完全口径一致
5. **M5（polish）** — 100 亿数字模板 CSS 不破坏现有 920px 渲染

**pyright 0 错，ruff 与基线持平，建议接入主线。**
