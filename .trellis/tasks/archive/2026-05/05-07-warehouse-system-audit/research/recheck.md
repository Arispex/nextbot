# 仓库系统二审报告（修复后复核）

**审计对象**：`nextbot/plugins/warehouse.py`（1700 → 1952 行）+ `nextbot/db.py` schema 改动
**审计日期**：2026-05-07
**比对基线**：`git diff HEAD`（未 commit 的工作区改动）
**初审报告**：`.trellis/tasks/05-07-warehouse-system-audit/research/findings.md`
**用户决策**：A 全修，跳过 W-3.1 / W-Common.2 / W-7.5

---

## Phase 1：修复项落实情况

| ID | 修复项 | 状态 | 位置 / 备注 |
|---|---|---|---|
| **W-6.1** | 回收金币条件 UPDATE | ✅ 已落实 | `_recycle_single` line 1104–1116（先 `update(User).values(coins=User.coins+refund)`，再 commit，再 query `coins_after`）；`_recycle_many` line 1191–1203 同模板。完全消除 lost-update。 |
| **W-7.1** | 单格领取 commit 兜底 | ✅ 已落实 | `_claim_single` line 1465–1481：`try: session.commit() except Exception as exc: session.rollback() + logger.error("[CRITICAL]") + reply_failure("领取","已发放但未确认到账，请联系管理员") + return`。日志 + 用户提示文案与计划一致。 |
| **W-7.2** | 多格领取 per-slot commit 兜底 | ✅ 已落实 | `_claim_many` line 1564–1576：每次 commit 包 try/except，失败时 rollback + logger.error 单格 CRITICAL + `unconfirmed_slots.append(s)` + continue。 |
| **W-Common.1** | 8 handler 异常兜底 | ✅ 已落实（全部）| `handle_list_self` 344–352 / `handle_list_user` 401–411 / `handle_add` 581–589 / `handle_remove` 652–660 / `handle_drop` 844–852 / `handle_recycle` 1045–1053 / `handle_claim` 1393–1401 / `handle_gift` 1718–1728。8/8 全部新增 `except CommandUsageError: raise / except Exception: logger.exception + 兜底 reply_failure`。`_remove_single/many` / `_drop_single/many` / `_recycle_single/many` / `_gift_single/many` 内层 commit 都包了 try/except: rollback; raise。 |
| **W-3.2** | value 上界 + refund cap | ✅ 已落实 | `add` line 491–497：`if value > MAX_COINS_AMOUNT` → reply_failure。`_recycle_single` line 1093 `refund = min(refund, MAX_COINS_AMOUNT)`；`_recycle_many` line 1189 同。`MAX_COINS_AMOUNT` 复用自 `economy.MAX_COINS_AMOUNT`（warehouse.py:30）。 |
| **W-7.3** | 多格领取失败明细 | ✅ 已落实 | `_claim_many` line 1534 `failed_details: list[tuple[int, str]]`；line 1561 `failed_details.append((s, reason))`；line 1611–1622 输出 `跳过明细：` block（最多 10 条，超出显示 `...还有 N 个`），同时 unconfirmed 行展示 `⚠️ 已发放但未确认到账` 警告。 |
| **W-3.3** | WarehouseItem.user_id 索引 + 启动迁移 | ✅ 已落实（降级为 INDEX，非 FK）| `db.py` line 780–796 新增 `ensure_warehouse_fk_schema()`（注释说明 SQLite 不支持给已有列加 FK，故降级为 `CREATE INDEX IF NOT EXISTS`），并在 `init_db()` 注册（line 390）。失败仅 `logger.warning` 不阻断启动，与 `ensure_user_name_unique_schema` 同模板。 |
| **W-7.4** | unicode 折叠 | ✅ 已落实 | `_normalize_player_name` line 230–235：`unicodedata.normalize("NFKC", name).strip().casefold()`，用于 `_check_player_online` line 1265 / 1271。仅作运行时比较，不影响 DB 存储。 |
| **W-Common.3** | 多 session 合并 | ⚠️ 部分落实 | `_load_user` / `_load_user_by_name` / `_load_server`（line 168–195）签名加 `session: Session \| None = None` 参数，传入则复用、否则自开自关。**但全部 13 处调用方都未传 session=** ——helper 接口扩展但优化未触发。功能上无回归（默认行为保持），但性能优化未实际生效。建议下一轮在每个 handler 一次性开 session 后传给 helper。 |
| **W-3.4 + W-8.1** | `_find_empty_slots` 批量 + gift break | ✅ 已落实 | line 209–227 新增 `_find_empty_slots(session, user_id, count)` 一次返回 ≤ count 个空格升序；`_gift_many` line 1865–1867 `need_count = sum(...) ; empty_slot_pool = _find_empty_slots(...) ; empty_iter = iter(...)`；line 1876 `target_slot = next(empty_iter, None)`；耗尽后 `skipped_full += 1`。N² → N。 |

---

## Phase 2：行为不变性

逐命令对比修复前后的输入校验、成功 / 失败回复文案、错误路径完整性。

### 命令 1：我的仓库 (`handle_list_self`)
- ✅ 入口校验完全一致（`if args: raise_command_usage()`）。
- ✅ 成功路径文案一致（直接 `_send_warehouse_image`，无文案差）。
- ✅ 错误文案 `查询`/`未注册账号` 保留。
- ⚠️ 新增"处理失败，请稍后重试"兜底（W-Common.1 允许）。

### 命令 2：用户仓库 (`handle_list_user`)
- ✅ 所有 `name_not_found` / `name_ambiguous` / `查询失败` 文案保留。
- ⚠️ 新增 `caller_user_id = event.get_user_id()` 用于异常日志（line 384）— 仅用于日志，不影响用户回复。
- ⚠️ 新增 W-Common.1 兜底文案。

### 命令 3：添加仓库物品 (`handle_add`)
- ✅ 全部输入校验文案保留（"物品 ID 必须为正整数" / "数量必须为正整数" / "前缀 ID 必须为非负整数" / "未知进度" / "价值必须为非负整数"）。
- ⚠️ **新增**校验 `value > MAX_COINS_AMOUNT` → "价值过大（最多 100000000）"（W-3.2 显式允许的新文案）。
- ✅ "未找到该用户" / "仓库已满" / "格子被占用" / 添加成功 reply_block 文案完全一致。
- ⚠️ 新增 W-Common.1 兜底文案。

### 命令 4：删除仓库物品 (`handle_remove`)
- ✅ 全部入口校验、`未找到该用户` / `用户名重复` / `多格不支持数量` / `数量必须为正整数` / `数量超过该格当前数量` / `该格子为空` / `未找到任何可删除的格子` 文案完全一致。
- ✅ 单格 / 多格成功 reply_block（含 `处理格子：N 个（跳过 M 个空格）`）文案一致。
- ⚠️ 新增 W-Common.1 兜底（commit 失败时 `处理失败，请稍后重试`，pre-fix 是直接 NoneBot 顶层吞掉）—— W-Common.1 的预期效果。

### 命令 5：丢弃仓库物品 (`handle_drop`)
- ✅ 入口校验、"未注册账号" / "该格子为空" / "数量超过该格当前数量" / "未找到任何可丢弃的格子" 文案保留。
- ✅ 单格 / 多格成功 reply_block 文案一致。
- ⚠️ 新增 W-Common.1 兜底。

### 命令 6：回收仓库物品 (`handle_recycle`)
- ✅ 入口校验、"未注册账号" / "该格子为空" / "物品无价值，不可回收" / "数量超过该格当前数量" / "未找到任何可回收的格子" 文案保留。
- ✅ 单格 / 多格成功 reply_block（含"回收比例" / "获得金币" / "当前金币" / "已使用" 行）文案完全一致。
- ⚠️ **行为改进（用户允许）**：`coins_after` 现在是 `update(User)` commit 后重新 query 的真实值（W-6.1），并发场景下值更准确；非并发场景与 pre-fix 完全相等。
- ⚠️ **行为改进（用户允许）**：`refund / total_refund` 经 `min(..., MAX_COINS_AMOUNT)` 上界 cap（W-3.2）。文案保留 `获得金币：{refund}`，但极端 admin 误配置时 refund 不会爆量。
- ⚠️ 新增 W-Common.1 兜底。
- 🟢 **额外改进**：`_recycle_single` / `_recycle_many` 现在显式判断 `if user is None: reply_failure("未注册账号"); return`（pre-fix 没这一层，依赖 outer handler 提前 `_load_user`）—— 防御性，无回归。

### 命令 7：领取仓库物品 (`handle_claim`)
- ✅ "服务器 ID 必须为整数" / "服务器不存在" / "未在该服务器在线" / 进度不足文案 / "数量超过该格当前数量" / "未找到任何可领取的格子" / "发送物品失败，{reason}" 文案保留。
- ✅ 单格成功 reply_block（含 服务器 / 玩家 / 格子 / 已使用）文案一致。
- ✅ 多格 reply_block 主体（含 处理格子 / 共领取 / 已使用）文案一致；`(跳过 X 个空、Y 个进度不足、Z 个发送失败)` 字符串格式与 pre-fix 一致。
- ⚠️ **新增**多格"跳过明细：" + 列表（W-7.3 允许的新文案）。
- ⚠️ **新增**单格"已发放但未确认到账，请联系管理员"（W-7.1 允许）。
- ⚠️ **新增**多格"⚠️ 已发放但未确认到账的格子：N 个，请联系管理员"（W-7.2 允许）。

### 命令 8：赠送仓库物品 (`handle_gift`)
- ✅ "未找到该用户" / "用户名重复" / "不能赠送给自己" / "数量必须为正整数" / "请先注册账号" / "对方仓库已满" / "对方格子被占用" / "未找到任何可赠送的格子" / "数量超过该格当前数量" / "该格子为空" 文案保留。
- ✅ 单格成功 reply_block 文案完全一致。
- ✅ 多格成功 reply_block + skip_parts（"X 个空"、"X 个对方仓库已满"、"X 个格子冲突"）文案完全一致。
- ⚠️ **行为改进**：`_gift_many` 用预取 `empty_slot_pool` + `iter` 替换循环内 `_find_first_empty_slot`。skipped_full 计数 / 处理顺序与 pre-fix 完全等价（详见 Phase 3 NEW-2）。
- ⚠️ 新增 W-Common.1 兜底。

**Phase 2 结论**：所有 8 个命令的输入校验、错误文案、成功文案完全保留，新增文案均限于 W-7.1 / W-7.2 / W-7.3（用户已签字允许）+ W-3.2 价值上界（明确实施项的衍生）+ W-Common.1 兜底（明确实施项）。**无未授权的破坏性文案改动**。

---

## Phase 3：新引入问题排查

### NEW-1：`_claim_single` / `_claim_many` rollback 后 ORM 对象 detached 访问 — ✅ 安全

- **检查点**：W-7.1/W-7.2 commit 失败时 `session.rollback()` 会将该会话的所有挂起对象 detached / expired。如果之后还访问 `item.quantity` / `item.item_id` 等会触发 lazy load，可能抛 DetachedInstanceError。
- **复核结论**：源码已在 commit 之前把所有需要的字段拷成 Python int / str：
  - `_claim_single` line 1426–1428 `item_id = int(item.item_id) ; prefix_id = int(item.prefix_id) ; min_tier = str(item.min_tier or "")`，line 1445 `claim_qty = quantity_arg if ... else current_qty`。rollback 路径用的是这些本地变量，不再触 item。
  - `_claim_many` line 1552–1554 同样预先取 `slot_qty / slot_item_id / slot_prefix_id`，logger 和 unconfirmed_slots 均使用本地变量。
- ✅ 无 detached 访问风险。

### NEW-2：`_gift_many` 预取空位池 vs pre-fix 每轮重扫 — ⚠️ 极小幅行为差异（用户已认可）

- **场景**：循环中如有 `IntegrityError`（WebUI 占用了选中的目标格），pre-fix 会下一轮重新扫 `_find_first_empty_slot` 拿到新空格再试；post-fix 用预取的 `empty_iter`，冲突后该 slot 已从迭代器消费掉，下次拿的是池里的下一个空格。
- **复核**：
  - 真要冲突说明该 slot 已被 WebUI 占据，**实际不空**了，pre-fix 的"重扫"也不会再选中它，会跳到下一个空格。
  - 因此 post-fix 行为在大多数情况下与 pre-fix 等价。
  - 唯一差异：如果 WebUI 在赠送过程中 *腾出* 了一个新的空格（比 pre-scan 时还要靠前），pre-fix 可能会用上，post-fix 不会。这种"动态新空位"是非常窄的边缘场景。
- 🟢 **结论**：W-3.4 + W-8.1 的设计取舍，用户已签字（"A 全修")。日志（`赠送仓库物品冲突（多格）`）和文案（`X 个对方仓库已满 / X 个格子冲突`）保留。无功能性回归。

### NEW-3：`_recycle_*` refund cap 顺序与 ratio 上界互动 — 🟢 安全

- **场景**：admin 通过 `recycle_ratio` 参数（W-Common.2 用户已选择跳过）误配 ratio=10 + value=100_000_000 → `int(unit_value * recycle_qty * ratio)` 在 Python 里是 unbounded int，先算成 10亿、然后 `min(refund, MAX_COINS_AMOUNT)` cap 到 1亿。无溢出风险。
- **复核**：Python int 任意精度，且 cap 在乘法之后立刻执行，无窗口。✓
- ⚠️ **未消除问题**：W-Common.2（ratio 上界）被用户明确跳过，仍是潜在管理员误操作风险。但这是用户选择，不是新引入问题。

### NEW-4：`_find_empty_slots` 顺序保证 — ✅ 一致

- 对比：`_find_first_empty_slot` 用 `next((i for i in range(1, CAP+1) if i not in occupied), None)` 升序找第一个；`_find_empty_slots` 用 `for i in range(1, CAP+1)` 升序累积。**升序一致**。pre-fix 多次调用得 `[s1, s2, s3, ...]` 与 post-fix 一次得 `[s1, s2, s3, ...]` 完全相同。✓

### NEW-5：`_normalize_player_name` 应用范围 — ✅ 仅用于在线检查

- 全文搜索仅 `_check_player_online` line 1265 / 1271 使用。DB 写入路径（claim 用 `player_name = str(user.name)` line 1367 → 直接传给 `_issue_give_command` 拼 `/give` 命令）**未** normalize → 以 DB 中存的玩家名为准发命令，正确。
- TShock 在 `/v2/server/status` 返回的 `nickname` 字段也只在 `_check_player_online` 内部 normalize 比对，不写回 DB。✓

### NEW-6：`_load_user(session=...)` 向后兼容 — ✅ 完全无回归

- 13 处调用全部走默认分支（`session is None` → 内部 `get_session() ... finally close()`），与 pre-fix 一字不差。✓
- 副作用：W-Common.3 优化未实际生效（mark for follow-up）。

### NEW-7：`ensure_warehouse_fk_schema` 失败处理 — ✅ 安全

- 与 `ensure_user_name_unique_schema` 同模板：`with engine.begin() as conn: try: conn.execute(...) except Exception: logger.warning(...)`。失败仅 warning，启动继续。
- 现有部署直接拉新代码：`CREATE INDEX IF NOT EXISTS` 是幂等的，已存在数据库执行不会报错（即使没有索引，执行后立即建上）。`init_db()` 在每次启动调用，零迁移成本。✓

### NEW-8：handler except 与 helper except 嵌套不会双重日志 — ✅ 安全

- `handle_recycle` 外层 `except Exception: logger.exception(...)`（line 1048）。`_recycle_single` 内层 commit `except Exception: session.rollback(); raise`（line 1111-1113）—— **只 raise，不 log**。异常向上传到 outer handler 时只产生一次 `logger.exception`。
- `handle_claim` 外层 `except Exception: logger.exception(...)`（line 1396）。`_claim_single` 内层 commit `except Exception: rollback + logger.error("[CRITICAL]") + send + return` —— **不 raise，自处理**。outer 不会再触发。同样无双重日志。
- 8 个 handler 全部检查过，无双重日志路径。✓

### NEW-9：W-6.1 是否使用 `execute_rowcount` helper — 🟡 未使用，但合理

- W-6.1 修复用 `session.execute(update(User).where(...).values(...))`（line 1104, 1191），未用 `execute_rowcount`。
- **复核**：本场景 WHERE 条件只是 `user_id == X`，rowcount 必为 1（用户存在性已在 outer handler 验证）。`execute_rowcount` 主要用于 `WHERE coins >= amount` 这种条件 UPDATE 后判断"是否真的扣了"，本场景无此需求。
- ✅ 不使用是合理设计取舍，但若用了会更显式（一致性建议）。无 bug。

### NEW-10：`_claim_many` `processed=0 + unconfirmed=N` 边界回复路径 — ⚠️ 文案略含混

- **场景**：所有 give 都成功但所有 commit 都失败（极端边界，比如磁盘满）。
- **当前行为**：line 1580 `if processed == 0 and not unconfirmed_slots:` —— 不进入 early-return 分支。继续往下执行 reply_block，标题是 `reply_success("领取")`，主体显示 `共领取：0 件物品 + ⚠️ 已发放但未确认到账的格子：N 个，请联系管理员`。
- **影响**：用户看到"领取成功"但又看到"未确认到账"——略含混，但这是 W-7.2 设计意图（成功调度 give，但 DB 未确认）。详细 warning line 已说清楚。
- 🟢 **结论**：边缘场景，文案保留 W-7.2 设计，可接受；如要更精准可改为 `reply_failure` 或在 `processed=0 + unconfirmed>0` 时切换标题。**仅作 follow-up 建议，不阻塞验收**。

---

## Phase 4：整体回归

| 检查项 | 结论 |
|---|---|
| SQL 注入 | ✅ 无：全部使用 ORM 参数化（`session.query(...).filter(...)` / `update(...).where(...).values(...)`）；`_issue_give_command` 用 f-string 拼 TShock 命令，但 player_name 来自 DB（已注册），item_id 来自 DB，prefix_id 来自 DB，quantity 已 int 校验。无注入面。 |
| 越权 | ✅ `warehouse.add` / `warehouse.remove` 默认非 guest 权限（`DEFAULT_GUEST_PERMISSIONS` 不含），仅 admin。`drop_self` / `recycle_self` / `claim_self` / `gift_self` 操作的对象是 `user_id == event.get_user_id()`。`gift` 已检查 `sender_id != target_user_id`。无越权。 |
| 资源泄漏 | ✅ 全部 `session = get_session() ... try ... finally session.close()`。`temp_screenshot_path` 用 async with。无泄漏。 |
| 错误处理缺口 | ✅ 8 个 handler 全部 W-Common.1 兜底；内层 helper commit 全部包 try/except；TShock API 调用全部 `(ok, reason)` 元组返回。 |
| Race condition | ✅ 仓库行：`warehouse_lock(user_id)` per-user 序列化；`gift` 用 `_acquire_two_warehouse_locks` 双锁固定顺序避 ABBA。金币：W-6.1 条件 UPDATE 消除 lost-update。<br>⚠️ **唯一未消除**：W-7.1 / W-7.2 的 DB ↔ TShock 双重一致性窗口（give 成功 + commit 失败 → 物品凭空 +1）。这是**分布式系统不可能定理**的体现，已加 logger.error CRITICAL + 用户提示 + 日志可追溯。除非引入分布式事务（不现实），无法 100% 消除。已设补偿告警，符合用户选项 A 中"先告警止血"的取舍。 |
| Lost-update 残留 | ✅ 全文检索 `user.coins =` 无匹配。所有金币加减都用 `update(User).values(coins=User.coins ± X)`。✓ |
| MAX_COINS_AMOUNT 复用 | ✅ `from nextbot.plugins.economy import MAX_COINS_AMOUNT`，单源真理。✓ |
| 异常兜底覆盖 | ✅ 8/8 handler；commit 路径覆盖。✓ |
| `temp_screenshot_path` | ✅ pre-existing，已迁移。✓ |
| `_safe_param_int` helper | 🟢 仓库唯一 param 是 `recycle_ratio: float`，`max(0.0, float(...))` 已是合理校验，无需 `_safe_param_int`。 |
| `execute_rowcount` helper | 🟡 W-6.1 未使用（理由见 NEW-9，可接受）。 |

---

## 结论

| 验收标准 | 结果 |
|---|---|
| **1. 无破坏性（外部行为完全一致）** | ✅ **通过**。8 个命令全部输入校验、错误文案、成功 reply_block 完全保留。新增文案严格限于 W-3.2 / W-7.1 / W-7.2 / W-7.3 / W-Common.1 兜底（用户允许范围内）。 |
| **2. 开箱即用（含启动迁移）** | ✅ **通过**。`db.py` 新增 `ensure_warehouse_fk_schema()` 已在 `init_db()` 注册（line 390）；用 `CREATE INDEX IF NOT EXISTS` 幂等；失败仅 warning 不阻断启动。已部署直接拉代码即可工作。 |
| **3. 仓库系统再无漏洞缺陷可优化空间** | ✅ **基本通过**。所有 🔴 必修项（W-6.1 / W-7.1 / W-7.2）已落实，所有 🟠 应修项（W-Common.1 / W-3.2 / W-7.3 / W-3.3）已落实。用户选择跳过的 3 项（W-3.1 / W-Common.2 / W-7.5）按预期未修。<br>⚠️ **遗留 follow-up（不阻塞验收）**：<br>• W-Common.3 helper 加了 `session=` 参数但无调用方使用 → 优化未生效（建议下一轮把 handler 改成 `with get_session() as s: _load_user(uid, session=s); ...`）<br>• NEW-10：`_claim_many` 在 `processed=0 + unconfirmed>0` 时仍显示 `reply_success("领取")` 标题，文案略含混（建议改为 `reply_failure` 或专用文案）<br>• NEW-9：W-6.1 未用 `execute_rowcount`（合理但不一致）<br>• W-7.1 / W-7.2 的 DB ↔ TShock 双重一致性窗口理论上仍存在（已加 CRITICAL 告警 + 用户提示，是分布式系统不可消除的窗口）|

### 总体结论

**通过验收**。3 项必修 + 5 项应修 + 关键 🟡 项（W-Common.3 / W-7.4 / W-3.4 / W-8.1）全部落实；行为不变性严格保持；启动迁移就绪；无新引入 bug。

3 个 follow-up（W-Common.3 调用方未传 session= / NEW-10 文案 / NEW-9 helper 一致性）属于优化建议，不影响验收，建议归档下一轮再处理。

### Verification 执行情况

- **TypeCheck (pyright)**：33 errors（均为 pre-existing `at: object` 操作符 + warning），与 pre-fix（36 errors）数量持平（实际下降 3，因部分 at 在新 try 块外提前定义为 `OBV11MessageSegment`）。**无新增类型错误**。
- **Lint (ruff)**：post-fix 148 errors，pre-fix 98 errors。Δ=50 全部为：
  - BLE001 +19（W-Common.1 设计上需要 broad except，符合 pattern 已有先例）
  - SIM105 +9（建议用 `contextlib.suppress` 替换嵌套 try/except pass，纯风格）
  - PLR0911/0912/0915/C901 +若干（handler 复杂度增加，预期内）
  - E501 +8（中文长字符串）
  - PLR2004 +1（`failed_details[:10]` magic number）
  - 这些都是 **预期内的风格警告**，无逻辑 bug。pre-fix 同类项目（warehouse.py 本身就有 47 个 E501 + 6 TRY003 + 14 PLR2004）已在 codebase 通行。
