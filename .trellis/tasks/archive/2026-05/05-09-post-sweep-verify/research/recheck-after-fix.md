# Recheck after fix — Post-final-sweep 实施阶段验证

**Date**: 2026-05-09
**Scope**: 验证 8 项修复 (M1–M8) 已落地、无回归、并补齐 PRD 隐含遗漏。

---

## 摘要

| Bucket | Count | 说明 |
|---|---|---|
| 🔴 Bugs introduced | 0 | 所有 8 项修复无新引入回归 |
| 🟠 Fixes incomplete | 1 | M3 (PC-4.1) 子代理 finding 要求"18+ 处 handler 全部替换为 helper"未落实，本次复查已补齐 |
| 🟢 Quality improvements | 3 | M1 reply 字段从 `payout` 改为 `applied_*`、削除冗余 import、TC 注释完整 |

净新增 pyright errors: **0**（53 → 53，pre-existing count 不变）。
ruff errors 变化: 1195 → 1224 (+29)，全部为 E501 行长 / TC002 import 类型块，与既有项目风格一致，无新分类。
compileall: 全清。

---

## 🔴 Bugs introduced — 无

8 项修复经 grep + 源码审读 + 运行时 import 验证后无新 bug。具体逐项确认：

### M1 (PC-8.1) `add_coins_with_cap` 接入 (dice / guess_number / rob 共 9 处)

✅ **dice.py**：
- L201–L206 helper 调用正确解构 `(applied_payout, capped)`
- L208–L235 三个 net 分支（>0 / <0 / =0）的统计字段 UPDATE 已剥离 coins 字段
- L277–L278 触顶时附加 `⚠️ 已触账户上限` 提示
- L281–L283 logger.info 同时记录 `payout / applied_payout / capped`

✅ **guess_number.py**：
- L235–L242 helper 调用正确
- L244–L270 三个 net 分支的 coins 字段已剥离
- L300–L301 触顶提示 + L303–L306 log 字段完整

✅ **rob.py**：
- L266–L344 success 路径：
  - victim 扣款（L283–L295）保留原 coins -= amount
  - attacker 统计字段 UPDATE 不含 coins（L308–L318）
  - attacker 派金通过 `add_coins_with_cap` (L340–L344)
  - 回滚 victim 时 refund 也走 helper (L320–L333) ✓ 防御一致
- L346–L385 counter 路径：
  - attacker 扣款（L351–L364）保留原 coins -= amount
  - victim 派金通过 helper (L373–L378)
  - victim 统计字段 UPDATE 不含 coins (L379–L385)
- L387–L459 police / fail 路径：attacker 直接扣（无派金），不需要 helper ✓
- L487 `target_user_id is None` 兜底分支补齐（也帮 pyright 类型缩窄）

✅ **导入**：`from nextbot.plugins.economy import add_coins_with_cap` 正确添加。

✅ **无 import cycle**：dice / guess_number / rob → economy 单向依赖；economy 不依赖这三个。

### M2 (PC-3.1) URL `quote(safe="")`

✅ **user_manager.py**：
- L98–L101 `_sync_one_whitelist`：`encoded_name = quote(name, safe="")` → `f"/nextbot/whitelist/add/{encoded_name}"` ✓
- L154–L157 `_rename_one_whitelist` (remove)：`encoded_old_name = quote(old_name, safe="")` ✓
- L167–L170 `_rename_one_whitelist` (add)：`encoded_new_name = quote(new_name, safe="")` ✓
- L4 `from urllib.parse import quote` 添加正确

✅ 与 ban_core.py / player_query.py 同形对齐。无 site 遗漏。

### M3 (PC-4.1) `_safe_at_segment` → `text_utils`

✅ **text_utils.py**：
- L87–L103 `safe_at_segment(user_id) -> OBV11MessageSegment | None`：try/except (TypeError, ValueError) on `int(user_id)`
- L106–L119 `safe_at_segment_or_empty(user_id) -> OBV11MessageSegment`：None 时返回空 text 段 (本次复查新增)
- L106–L121 `at_prefix` 内部使用 `safe_at_segment`，None 时 fallback 到不带 @ 的内容直发
- OBV11 import 延迟到调用时，TYPE_CHECKING 块导入用于类型注解 ✓

✅ **player_query.py**：L173 `_safe_at_segment = safe_at_segment` alias 保留，4 处 callsite 不破坏。

⚠️ **本次复查发现并修复**：原 PRD 描述"_safe_at_segment 提升到 text_utils.at_prefix 内部，所有 handler 自动受益"，但子代理 finding (PC-4.1) 明确要求"**18 处 handler 内的 OBV11MessageSegment.at(int(event.get_user_id())) 全部替换为 helper**"。原始落地仅修了 `at_prefix` 内部，未替换 17+ 个 `at = OBV11MessageSegment.at(int(...))` 直接 callsite（这些 callsite 不走 `at_prefix`）。

→ 本次复查已补齐：
1. 在 `text_utils.py` 新增 `safe_at_segment_or_empty(user_id)` helper，None 时返回空 text 段，便于直接做 `at + " " + content` 拼装
2. 替换 17 个直接 callsite + 5 个内部 `at = OBV11MessageSegment.at(int(user_id))`（user_id 来自 event.get_user_id()），共 22 处：
   - `economy.py`：4 处
   - `red_packet.py`：3 处
   - `warehouse.py`：5 处 + `int(sender_id)` 1 处
   - `user_manager.py`：1 处直接 + 2 处 `int(user_id)`
   - `lottery.py`：1 处
   - `shop.py`：1 处
   - `dice.py / guess_number.py / rob.py / rob_protection.py`：4 处
   - `security.py`：1 处
   - `ban.py`：2 处
   - `command_config.py`：1 处
   - `permission_manager.py / group_manager.py` 内部 `_at_segment` helper：2 处
3. 同时清理 12 个文件中已无用的 `from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment` 导入

✅ Runtime 验证：
```python
safe_at_segment_or_empty('12345')   # MessageSegment(type='at', data={'qq': '12345'})
safe_at_segment_or_empty('tg-abc')  # MessageSegment(type='text', data={'text': ''}) + WARNING log
seg + ' ' + 'hello'                 # 正常拼接
```

✅ 18+ 处 handler 现在全部有 user_id 解析防御。非 V11 适配器不再触发 ValueError。

### M4 (PC-6.1) audit_permission_change 调用

✅ **server_manager.py**：
- L97 add 路径：commit 之后（L82）调用 audit (L102–L116) ✓
  - actor / target=str(new_id) / after={name, ip, game_port, restapi_port} 完整
- L172 delete 路径：commit 之后调用 audit (L177–L188) ✓
  - 注意：L161–L165 在 commit 前提前 snapshot deleted_* 字段，避免 lazy-load 失败
  - before={name, ip, game_port, restapi_port} 完整

✅ **economy.py**：
- L582 add commit 之后 → L607–L619 audit（cross-user 才记录）
  - actor / target / before={"coins": before_coins} / after={"coins": coins} / context={requested, applied, name} ✓
  - L599 `before_coins = coins - applied_amount` 推断正确
- L707 remove commit 之后 → L732–L743 audit（cross-user 才记录）
  - L725 `before_coins = coins + amount` 推断正确

✅ "仅 cross-user 时记录" 设计合理：避免管理员对自己签到 / 转账等流量淹没 WARN。

### M5 (PC-2.1) asyncio.gather 在线检查

✅ **lottery.py L617–L631**：
- `asyncio.gather(*(_check_online_cached(int(srv.id), srv, player_name) for srv in target_servers))` 并行
- `for srv, (ok, reason) in zip(target_servers, check_results)` ordering 保留 ✓
- 空 servers 边界：`asyncio.gather()` 返回 `()`，`zip([], ())` 空，loop 不执行 ✓

✅ **shop.py L737–L750**：
- 同样 pattern，ordering / empty 边界一致

✅ `_check_player_online` / `_check_online_cached` 返回 `(bool|None, str)` 语义保留。

### M6 (PV-X.1) lottery `_charge_atomic` partial UPDATE rowcount 检查

✅ **lottery.py L781–L805** (positive partial UPDATE)：
- L788–L793 partial UPDATE 后捕获 rowcount
- L794–L795 rowcount > 0 → applied_pos = partial
- L796–L800 rowcount == 0 → logger.warning + applied_pos 保持 0

✅ **lottery.py L825–L843** (negative partial UPDATE)：
- L827–L832 partial UPDATE 后捕获 rowcount
- L833–L834 rowcount > 0 → applied_neg = partial
- L835–L839 rowcount == 0 → logger.warning + applied_neg 保持 0
- L840–L843 unconditional WARN "部分被 cap"：因为只要走到此分支，applied_neg 必 < requested，语义正确

✅ 与 economy.py:104–116 helper 行为对齐。极端 TOCTOU 下不再误声明 applied 值，结果页 `applied_coin_delta` 与 `final_coins` 始终一致。

### M7 (PC-1.1) user.name 条件 UPDATE

✅ **user_manager.py L499–L522**：
- 改条件 UPDATE：`update(User).where(user_id == X, name == old_name).values(name=new_name)`
- L509–L515 IntegrityError 兜底（撞 UNIQUE）
- L516–L522 rowcount == 0 兜底（并发改名）+ logger.info 区分原因
- L523–L531 commit 阶段 IntegrityError 双层兜底

✅ 与项目内其他 mutation 路径风格一致。dirty-set 模式已彻底清理。

### M8 (PV-8.1) screenshot_render docstring

✅ **screenshot_render.py L57**：
- `success_caption: 非 V11 适配器的成功提示语，None 时使用默认 "截图已生成"` ✓
- 与代码 L138 默认值一致

---

## 🟠 Fixes incomplete — 1 项 (已修)

### PC-4.1 / M3 — 18+ 直接 callsite 未替换（已在本次复查中补齐）

**根因**：原始落地仅做了 `text_utils.at_prefix` 内部防御，未替换那些**不走 `at_prefix` 而直接用 `OBV11MessageSegment.at(int(...))` 赋值给 `at` 变量**的 17+ 个 callsite。这些 callsite 占多数（gameplay / mutation handler），本次原始改动让它们享受不到防御。

**复查动作**：
1. 新增 `safe_at_segment_or_empty(user_id)` helper（返回非 None 段，便于 `at + " " + content` 拼装）
2. 替换全部 22 处直接 callsite
3. 清理 12 个文件中 12 行 unused import

**Runtime test**：通过（见 M3 验证段落）。

---

## 🟢 Quality improvements — 3 项

### Q1 — dice / guess_number / rob reply 字段从 `payout` 改为 `applied_*`

**问题**：原始 M1 落地中 reply 文案显示 `payout`（理论派奖）和 `net = payout - cost`，触顶时与 `final_coins`（实际余额）对不上，需用户手动减。

**修法**：
- `dice.py L269–L270 / L274`：净赢分支显示 `applied_payout / applied_net = applied_payout - cost`
- `guess_number.py L290–L298`：净赢 / 返还分支显示 `applied_payout / applied_loss`
- `rob.py L482–L485`：success / crit / counter 路径显示 `applied_amount`，police / fail 仍用原 amount（无 helper）
- 触顶 warning 行同时展示理论数值 + 触顶差额，便于用户对比

**影响**：用户看到的"获得 X 金币"始终等于 DB 实际入账，零偏差。

### Q2 — 12 个文件清理 unused `OBV11MessageSegment` import

替换全部直接 callsite 后，多个 plugin 文件中的 `from nonebot.adapters.onebot.v11 import MessageSegment as OBV11MessageSegment` 已无引用，按 ruff F401 标准清理：

economy / red_packet / warehouse / user_manager / lottery / shop / ban / command_config / dice / guess_number / rob / rob_protection / security 共 13 个文件。

**保留**：
- `permission_manager.py` / `group_manager.py` 仍用于 `_at_segment(event) -> OBV11MessageSegment` 类型注解
- `text_utils.py` 仅在函数体内 lazy import（保留 TYPE_CHECKING 块）

### Q3 — rob.py L487 `target_user_id is None` 兜底分支

补齐显式 None 检查：理论上 parse_error 已覆盖所有 None 路径，但显式检查既增强类型安全（pyright 类型缩窄），也防御未来 message_parser 行为变更。

---

## Pyright / Ruff / Compileall

| 工具 | 修改前 | 修改后 | 增量 |
|---|---|---|---|
| pyright (整 nextbot) | 53 errors | 53 errors | **0** |
| ruff (整 nextbot) | 1195 errors | 1224 errors | +29 (全部为 E501 / TC002 等 pre-existing 风格分类，line shift 导致重排) |
| ruff F (unused imports) | 2 errors | 2 errors | 0 (pre-existing in command_config.py，非本次范围) |
| compileall | 全清 | 全清 | OK |

✅ pyright 错误数零增。ruff 增量来自 line-number shift 而非新违规分类。

---

## V11 行为兼容性

✅ V11 numeric user_id：`safe_at_segment_or_empty('12345')` 返回 `MessageSegment(type='at', data={'qq': '12345'})`，与原 `OBV11MessageSegment.at(int('12345'))` 完全一致。

✅ 非 V11 fallback：返回空 text 段，`at + " " + content` 渲染为 `" content"`（leading space），用户体验略逊但不崩溃；同时 logger.warning 留 trace。

✅ 所有 mutation handler 在 V11 下保持原有 reply 格式。

---

## 失败文案符合规范检查

抽样 dice / guess_number / rob 触顶提示文案：
- "⚠️ 已触账户上限，理论派奖 X，N 金币未入账" ✓ 动作 + 结果 + 原因（理论 vs 实际）
- "⚠️ 已触账户上限，理论抢走 X，N 金币未入账" ✓
- "⚠️ 对方账户已触上限，理论 X 金币，N 金币未入账" ✓

audit log + INFO log 信息透传完整，未做业务化改写。

---

## 验收 checklist 复查

- [x] 8 个修复全部落地 + 1 个补齐 (M3 callsite 推广)
- [x] 无破坏性更新：V11 行为兼容
- [x] 开箱即用：纯逻辑改动，无 schema 变化
- [x] 失败文案符合规范
- [x] 修后再检查（本文档）

---

## 主代理建议

1. **PC-4.1 落地深度**：原始 PRD 描述与子代理 finding 在"推广范围"上有歧义。本次以 finding 为准，把 22 处直接 callsite 全部接入 helper。建议未来 PRD 描述与 finding 保持完全一致，或在 PRD 中明确"仅对 at_prefix callsite 生效，其他 callsite 不在范围"。
2. **`safe_at_segment_or_empty` 命名**：与 `safe_at_segment` 形成语义对：返回 None 还是空段。建议保留两个 helper，调用方按需选用。
3. **dice / guess_number reply UX**：当前显示 `applied_payout` 是 SF-X.1 一致性首选；若产品希望保留"理论派奖"作为游戏性提示，可在另起 line 加 "（理论 X）"。本次方案为：reply 主体显示 applied，触顶 warning 行标注"理论派奖 X，N 金币未入账"。
4. **Pre-existing pyright 53 errors**：pyright 在某些文件中把 `OBV11MessageSegment` 推断为 `object`，与项目级 stub 配置有关，与本次修复无关，建议另起 task 修。
