# Round 5 Verify — R4 修复回归 + 收敛复审

- **Query**: Round 5 sweep — verify R4 fixes (M1-M5)，找最后边角；预期 critical=0, high=0, medium ≤ 2
- **Scope**: internal（codebase grep + 重读 R4 修复点 + 跨命令 race + 模板）
- **Date**: 2026-05-10
- **预期发现量**: 极低，本轮主要任务是确认收敛

## 总览

| Severity | 数量 |
|---|---|
| 🔴 Critical | 0 |
| 🟠 High | 0 |
| 🟡 Medium | 1 |
| 🟢 Low | 3 |
| ℹ️ Info | 2 |
| 扫描通过 | 12 |

**结论**：本轮 sweep 找到 **1 medium（cap-stats drift 第 4 处家族实例 in rob.py）+ 3 low + 2 info**。R4 5 项修复全部回归通过。系统已进入稳定收敛区。

---

## Part A: R4 修复回归检查（M1-M5）

### ✅ M1 R4R-7.1：dice / guess stats 用 `applied_net = applied_payout - cost`

**扫描通过**：

- `nextbot/plugins/dice.py:209-238`：`net = payout - cost` 用于分支判定（赢/平/输的 win_count 仍按理论 net），但 stats 列（`dice_total_gain` / `dice_total_loss`）已改用 `applied_net = applied_payout - cost`，且分别 wrap `max(0, applied_net)` / `abs(applied_net)`，即使 cap 触顶导致 `applied_net <= 0` 仍计入"赢"分支正确（wrap 0 不影响）
- `nextbot/plugins/guess_number.py:244-273`：同模板，`applied_net = applied_payout - cost`，wrap 同 dice
- 与 `user.coins` delta 一致（applied_payout 是实际加账的金币，applied_net = applied_payout - cost 即是实际净增）

**回归通过**：dice/guess 在 cap 触发场景下 stats 与真实余额变化对账一致。

### ✅ M2 R4R-2.1+2.2：11 处 mutation handler `except Exception:` 加 `session.rollback()`

**扫描通过**：

通过 grep `session.rollback` 验证在以下 handler 全部添加：
- `economy.py:443 / 587 / 665 / 792`（签到 / 转账 / 添加金币 / 扣除金币 outer except）
- `dice.py:247`
- `guess_number.py:282`
- `red_packet.py:228 / 397 / 512`（发红包 / 抢红包 / 收回红包）
- `rob.py:470`
- `rob_protection.py:127`

**rollback 顺序正确**：第一行 `session.rollback()`，紧跟 `logger.exception()`，最后 `try/except: bot.send + pass`，rollback 不会因 reply 失败而被跳过。

**warehouse 不在 M2 列表**：经核实，warehouse outer except 块（line 574 / 645 / 837 / 1043 / 1416）所包裹的 helper（`_remove_single` / `_drop_single` / `_recycle_single` / `_claim_single` 等）各自管理自己的 session（`get_session()` + try/finally + commit-time `try/except: rollback; raise` 模式），outer scope 没有 in-scope session，rollback 会 NameError。**设计正确，M2 不应列入 warehouse**。

同理 shop outer except 块（line 286 / 443 / 562）和 lottery outer except 块（line 318 / 457 / 965）所有 session 都在内部 helper 中，outer scope 无 session。

### ✅ M3 R4R-B.1：3 处 asyncio.gather 加 return_exceptions=True

**扫描通过**：

- `user_manager.py:137-150`（`_sync_one_whitelist` fan-out）+ `user_manager.py:579-590`（`_rename_one_whitelist` fan-out）：`return_exceptions=True` + per-result `isinstance(raw, BaseException)` 分支记录 warning，然后 append `(server, "fail", "同步异常")` / `(server.id.name: ❌ 同步异常)` —— **fallback tuple shape 与 success path 一致，下游解构匹配**
- `leaderboard.py:794-805`：`return_exceptions=True` + isinstance BaseException 分支 → fallback append `(server, None)`，与 `_fetch_one` 正常返回 `(server, list | None)` shape **一致**
- `lottery.py:638-648`（`_check_online_cached` fan-out）：之前 R3N-4.2 已加 `return_exceptions=True`，本次确认未回归

**未加但安全**：
- `permission_manager.py:661`（`_fetch_nickname_with_timeout`）：helper 内已 catch BaseException 转 fallback `(qq, "（获取失败）")` ── 不需要 `return_exceptions=True`（理由记入 R4R-B.1）
- `server_broadcast.py:66`：`_wrap` 已 catch BLE001 → 安全

**未修但 R5 新发现**：见下方 🟢 R5-B.1 player_query.py 漏网。

### ✅ M4 R4R-5.1：lottery `_normalize_player_name` (NFKC + casefold)

**扫描通过**：

- `lottery.py:69-75`：定义 `_normalize_player_name = unicodedata.normalize("NFKC", str(name)).strip().casefold()`，与 `shop.py:85` / `warehouse.py:232` 完全一致
- `lottery.py:186 + 189`：`_check_player_online` 调用方 NFKC normalize 双侧（target + nickname）
- `lottery.py:608-614 _check_online_cached`：内部直接调 `_check_player_online`，cache key 用原始 player 字符串 + srv_id，但 normalize 在 `_check_player_online` 内部做 → fan-out cache 与单次调用行为一致
- 全角 `Ａｌｉｃｅ` 与半角 `Alice` 现在在 lottery / shop / warehouse 三域匹配口径完全一致

**回归通过**：`_check_player_online` 对 fan-out（`_check_online_cached` / `asyncio.gather`）都生效。

### ✅ M5 模板 polish

**扫描通过**：

- `lottery_result.html:96-107` `.stat-value`：已添加 `word-break: break-all`，配合 `font-feature-settings: "tnum"` 防止 100 亿数字串溢出
- `user_info.html:129-141 + 170` `.stat-value`：已添加 `word-break: break-all`（含独立的 138-141 行注释解释 break-word 在 Chromium 不可靠，需要 break-all）

**其他截图模板（lottery_view / lottery_list / shop_list / shop_view / leaderboard / inventory / progress）grep 检查**：
- `lottery_view.html:175 + 235`：已用 `word-break: break-word` + `break-all`
- `lottery_list.html:100`：用 `break-word`（cost-pill 字段），简单 cost = `${Number(entry?.cost_per_draw || 0)} 金币 / 次` 上限 100亿 = 11 位 + " 金币 / 次" ≈ 19 字符，pill 容器内可接受
- `shop_view.html:167 + 228`：用 `break-word + break-all`
- `shop_list.html:103`：用 `break-word`
- `red_packet_all.html:121` + `red_packet_own.html:100`：用 `break-word`，但 `.stat-value` 内（line 162）显示 `${remainingAmount} / ${totalAmount}`（最大 100亿/100亿 ≈ 23 字符）未加 break-all → 见下方 🟢 R5-5.1
- `inventory.html .stat-value`：用 `nowrap + ellipsis`，仅显示 TShock 玩家 stats（life / mana / fishing-tasks 等），数值小，无需 break-all
- `progress.html`：纯文本 stats（XP / 等级），不显示金币

---

## Part B: cap 范围补齐第 4 处 — 🟡 R5-2.1 / 🟢 R5-2.2 / 🟢 R5-2.3

### 🟡 R5-2.1：rob.py:314 success path `rob_total_gain = User.rob_total_gain + amount` 用未 cap 值

- **Severity**: 🟡 Medium（不影响金币真实值，只影响 stats 准确度，与 R4R-7.1 同模式）
- **File**: `nextbot/plugins/rob.py:314`
- **Snippet**:
  ```python
  a_rows = execute_rowcount(
      session,
      update(User)
      .where(*attacker_where_clauses())
      .values(
          rob_total_count=User.rob_total_count + 1,
          rob_success_count=User.rob_success_count + 1,
          rob_total_gain=User.rob_total_gain + amount,   # ← 用未 cap 的 amount
          last_rob_time=now,
      ),
  )
  ...
  applied_amount, capped = add_coins_with_cap(session, robber_id, amount)   # ← 实际入账可能 < amount
  ```
- **Impact**：抢劫 attacker 接近 100 亿 cap 时，`add_coins_with_cap` 只入账 `applied_amount < amount`，但 `rob_total_gain` 列累计加 `amount`（理论值），导致 stats 与 user.coins 实际增量不一致。与 R4R-7.1 dice/guess `dice_total_gain += net`（用 payout 而非 applied_payout）**同形 bug，第 4 处家族实例**（前 3 处：R3E-1 红包蒸发 → R4R-7.1 dice → R4R-7.1 guess）
- **复现**：attacker 当前 coins=99_999_990_000（接近 100 亿 cap），victim coins=200_000_000，抢劫成功 amount=10_000_000 → applied=10_000_000（未 cap）但若 victim coins=50_000_000_000 → amount=2_500_000_000 → applied=10_000_000（cap 部分）→ rob_total_gain += 2_500_000_000 但 user.coins 只 +10_000_000 → 排行榜显示赢了 25 亿，实际入账 1 千万
- **修法**：把 `rob_total_gain=User.rob_total_gain + amount` 拆出 → 先调 `applied_amount, capped = add_coins_with_cap(...)` 拿到实际值 → 再独立 UPDATE `rob_total_gain=User.rob_total_gain + applied_amount`；与 dice.py:213-220 模板对齐

### 🟢 R5-2.2：rob.py:325 rollback path `rob_total_loss - amount` 漏 cap

- **Severity**: 🟢 Low（rollback 路径，触发概率低）
- **File**: `nextbot/plugins/rob.py:320-327`
- **Snippet**:
  ```python
  if a_rows == 0:
      # 回滚 victim 扣款
      refund_applied, refund_capped = add_coins_with_cap(session, target_user_id, amount)
      session.execute(
          update(User)
          .where(User.user_id == target_user_id)
          .values(
              rob_total_loss=User.rob_total_loss - amount,   # ← 减 amount，但 refund_applied 可能 < amount
          )
      )
  ```
- **Impact**：refund 触顶时（罕见：victim 在 deduction 与 rollback 之间被其他命令加币到接近 cap），`refund_applied < amount`，但 stats 列减去全额 amount → 抢劫历史损失累计偏小
- **修法**：把 `rob_total_loss - amount` 改 `rob_total_loss - refund_applied`；并配合 R5-2.1 一同修复

### 🟢 R5-2.3：rob.py:382 counter path `rob_total_gain + amount` 漏 cap

- **Severity**: 🟢 Low（counter 路径触发概率低）
- **File**: `nextbot/plugins/rob.py:372-384`
- **Snippet**:
  ```python
  applied_amount, capped = add_coins_with_cap(session, target_user_id, amount)   # victim 派金可能 cap
  ...
  session.execute(
      update(User)
      .where(User.user_id == target_user_id)
      .values(
          rob_total_gain=User.rob_total_gain + amount,   # ← 用未 cap 的 amount
      )
  )
  ```
- **Impact**：counter 路径下（attacker 反被抢），victim 入账触顶时与 R5-2.1 同形漂移
- **修法**：`rob_total_gain + amount` 改 `rob_total_gain + applied_amount`

---

## Part C: 极端 case

### ✅ subtract_coins_with_floor 在 user.coins=0 + delta>0 → (0, True)

**扫描通过**：

- `economy.py:135-191`：定义清晰，coins=0 时第一次 UPDATE rowcount=0 → 进入 fallback → `room=0` → return `(0, True)`，正常 logger.warning
- 全 codebase 唯一调用位置 `lottery.py:794` 接收 `applied_abs, _ = subtract_coins_with_floor(...)`，丢弃 `floored` 标记 → 即使触底 applied_abs=0 也合理（lottery._charge_atomic 用 `applied_neg = -applied_abs` 累加，0 不影响业务流，最终 `applied_coin_delta` 准确反映 DB 真实变化）
- 不影响业务流

### ✅ add_coins_with_cap 在 user 不存在（rowcount=0）：fallback 路径

**扫描通过**：

- `economy.py:92-132`：第一次 UPDATE rowcount=0 → fallback `coins_now = session.query(User.coins).filter(...).scalar() or 0` → user 不存在时 scalar() 返回 None，or 0 → coins_now=0 → room=MAX → partial UPDATE 仍 rowcount=0（user 不存在）→ logger.warning + return (0, True)
- 安全：不会抛 NoneType error，但也不会"假装成功"。`capped=True` 让调用方有机会显示"已触账户上限"提示（虽然实际是 user 不存在）。语义略不精确，但所有调用方都先 `session.query(User).filter(...).first()` check user 存在，所以这个 edge case 在实际路径里走不到。

### ✅ BEGIN IMMEDIATE 死锁：handler 内 helper 嵌套 session

**扫描通过**：

- `db.py:395-413`：BEGIN IMMEDIATE 在每个 connection 开始时执行；busy_timeout=5000ms 让阻塞 writer 等待
- `lottery.py:513-579 + 595-599 + 698-806`：3 个 session 全部 sequential（先 close 再开下一个），无嵌套
- 所有 warehouse handler 模式：outer 读 session → close → `warehouse_lock(user_id)` → inner helper 自己 get_session
- 所有 shop / red_packet / dice / guess / rob / economy handler 都是单 session 模式
- 无任何"outer session 开着，inner helper 又 get_session"的嵌套

### ✅ screenshot_render 在 image 文件 0 字节时 stat 检查通过吗 — 🟢 R5-3.1

- **Severity**: 🟢 Low（screenshot_url 应保证非 0 字节，0 字节意味着 playwright 内部异常未抛）
- **File**: `nextbot/screenshot_render.py:101-113`
- **Snippet**:
  ```python
  try:
      file_size = screenshot_path.stat().st_size
  except OSError:
      await bot.send(event, reply_failure(failure_action, "读取截图文件失败"))
      return False
  
  if file_size * 4 // 3 > MAX_BASE64_BYTES:   # ← 0 字节绕过
      ...
  
  if bot.adapter.get_name() == "OneBot V11":
      try:
          raw = screenshot_path.read_bytes()
          encoded = base64.b64encode(raw).decode("ascii")    # ← b64encode(b"") = ""
      ...
      await bot.send(event, OBV11MessageSegment.image(file=f"base64://{encoded}"))   # 发空图
  ```
- **Impact**：file_size=0 时绕过大小检查 → b64encode 空字节 = 空字符串 → 发出 `base64://` 空 src 给 OneBot V11 → 适配器层可能报错或显示损坏图片
- **风险评估**：极低 — `screenshot_url` 调用 playwright `page.screenshot(path=...)` 或抛 RenderScreenshotError 或写出有效 PNG。0 字节场景需要 playwright 内部成功返回但磁盘写失败（极罕见）
- **修法**：在 `if file_size * 4 // 3 > MAX_BASE64_BYTES` 前加 `if file_size <= 0: reply_failure(action, "截图为空"); return False`

### ✅ audit_permission_change 在 actor_user_id="system" 时 logger 字段是否清晰

**扫描通过**：

- `audit.py:38-50`：output 格式 `actor=system action=... target=... before=... after=... context=...`
- 唯一传入位置 `group_member_notify.py:204` 用 `actor_user_id="system"`，无歧义
- 字段不会与 user QQ 冲突（QQ 全数字，"system" 字符串）
- 字段清晰

---

## Part D: 跨命令组合攻击

### ✅ 抢劫 + 抢劫保护 同时切换

**扫描通过**：

- `rob.py:288 + 354 / 393 / 430`：所有 attacker / victim UPDATE 的 WHERE clause 都包含 `User.rob_protected.is_(False)` —— 即使 attacker 在 SELECT 之后被开启保护，UPDATE rowcount=0 触发 "冷却中或保护状态变更" 文案
- `rob_protection.py:90-102`：toggle UPDATE `WHERE coins >= cost AND rob_protected.is_(not target)` —— 互斥旧状态变更
- BEGIN IMMEDIATE 全局序列化 → 写写 race 由 SQLite 锁保证

### ✅ 红包发 + 红包关闭 同时进行

**扫描通过**：

- `red_packet.py:480-486 withdraw`：`UPDATE RedPacket WHERE status='active' status=...,closed_at=...` —— 已 grab 完毕（status='exhausted'）或已收回（status='withdrawn'）的 packet rowcount=0 触发 "该红包已关闭"
- `red_packet.py:327 grab`：`_claim_slot_atomic` 内部条件 UPDATE `WHERE remaining_amount >= draw_amount AND remaining_count > 0`，与 withdraw 互斥
- 双方都走 BEGIN IMMEDIATE 序列化

### ✅ 删身份组 + 添加身份组权限 race

**扫描通过**：

- `group_manager.py:343 / 373 cascade scrub`：删除身份组时 bulk UPDATE `User.group=GROUP_DELETE_FALLBACK WHERE group=name` + 子组 inherits 条件 UPDATE
- `group_manager.py:357-385 retry 模板`：重读 + 条件 UPDATE WHERE inherits=old_csv，并发时 rowcount=0 重试
- 删除前 `delete_matcher.handle_delete_group_confirm:336-394` 整个 flow 在单 commit 内，BEGIN IMMEDIATE 串行化

### ✅ 添加用户权限 + 改用户身份组 同时

**扫描通过**：

- `user_manager.py / permission_manager.py` 所有 mutation 走条件 UPDATE WHERE old_csv=旧值；并发时 retry _CSV_UPDATE_RETRY 次
- BEGIN IMMEDIATE 序列化保证单写者，理论上 retry 极少触发

---

## Part E: 文档与代码一致性

### ✅ 100 亿 / 10_000_000_000 一致性

**扫描通过**：

- `economy.py:49-52`：`MAX_COINS_AMOUNT = 10_000_000_000` 注释正确写明"100 亿"
- `webui_shop.py:25-28`：`_MAX_COINS_AMOUNT = 10_000_000_000`，注释 "R3 M0：从 1 亿 (100_000_000) 同步放宽到 100 亿"
- 全 codebase grep `100000000` / `100_000_000` / `1亿` 在**活跃代码 / 文档 / 模板 / spec 中无残留**（仅出现在 `.trellis/tasks/archive/` 历史 task 笔记中，属于设计中间过程记录，无需修改）

### ℹ️ R5-5.0：red_packet_all.html `.stat-value` 缺 break-all（潜在 polish）

- **Severity**: 🟢 Low / 🟢 R5-5.1 编号
- **File**: `server/templates/red_packet_all.html:162-169`
- **Impact**：`stat-value` 显示 `${remainingAmount} / ${totalAmount}`，最长 23 字符（"10000000000 / 10000000000"），未配 `word-break: break-all`，在窄卡片下可能溢出 stat 容器
- **风险评估**：与 R4 M5 的 lottery_result.html / user_info.html 同模式 polish，但 red_packet_all 卡片宽度通常足够容纳，触发概率低
- **修法**：在 `red_packet_all.html:162` `.stat-value` 加 `word-break: break-all`，对齐 lottery_result.html 模板

---

## Part F: R5 新发现（不在 R4 验收范围）

### 🟢 R5-B.1：player_query.py 2 处 `asyncio.gather` 漏 R4R-B.1 模板

- **Severity**: 🟢 Low（read-only / idempotent，安全 trade-off 明确）
- **Files**:
  - `nextbot/plugins/player_query.py:247-249`（`handle_online` `_query_one` fan-out）：`return_exceptions=False`
  - `nextbot/plugins/player_query.py:319-320`（`handle_self_kick` `_kick_one` fan-out）：`return_exceptions=False`
- **Impact**：与 R4R-B.1 同根因 —— `_query_one` / `_kick_one` 内部仅 `except TShockRequestError`，若任一 task 抛非 TShock 异常（CancelledError、KeyError），gather 会取消其他任务
- **风险评估**：
  - `handle_online`：read-only，部分查询失败仅影响展示，不破坏数据
  - `handle_self_kick`：踢出对已下线玩家幂等，部分服务器没踢成功不破坏数据
  - 风险可接受
- **修法**：与 R4R-B.1 一致，`return_exceptions=True` + per-result `isinstance(r, BaseException)` 分支记录失败原因
- **历史关联**：R4R-B.1 修了 user_manager / leaderboard，但 player_query 当时未列入修复范围

### ℹ️ R5-info.1：lottery 缺 stats 列与 dice/guess 不对称（设计取舍）

- **Severity**: ℹ️ Info（已知设计差异，不需要修）
- **Scope**：
  - `User` 模型有 `dice_total_count / dice_win_count / dice_total_gain / dice_total_loss`（dice）+ `guess_total_*`（guess）+ `rob_total_*`（rob），但**没有** `lottery_total_*` / `lottery_total_cost` / `lottery_total_gain` 等列
- **Impact**：lottery 不在 user-level 排行榜中（设计如此），不存在 cap-stats drift 隐患（因为没有 stats 列）。所有 lottery cap-related 数据都在 `applied_coin_delta` 内，单笔 reply / 渲染 / log 一致
- **修法**：N/A（设计取舍）

---

## 已知不修项目（前 4 轮已 acceptable trade-off）

- bot.send 失败 → 用户感知 ≠ DB 状态（chat-bot 固有 limitation）
- random vs secrets.SystemRandom（性能 vs 随机质量 trade-off）
- 各域 mutation 不统一走 audit_permission_change（设计取舍）
- `_MAX_COINS_AMOUNT` 在 `webui_shop.py` 重复定义（避免加载时触发 nonebot 副作用）
- player_query.py:247/319 `return_exceptions=False`（idempotent，可后续清理但不紧急）

---

## Caveats / 不确定项

- 未审 WebUI 路由（`server/routes/webui_*`）的并发安全 / 输入校验，已记入下游任务
- 未 read 全部 1900+ 行 warehouse.py（仅 sample 检查 outer except + commit-time rollback 模式）
- 未涉及 audit_economy_change helper 设计（PRD 明确 out-of-scope）
- 未涉及 DB CHECK constraint / cooldown 等下游任务

---

## 收敛性判断

**前 4 轮 sweep 发现量趋势**：14 → 8 → 11 → 5 → **R5: 4**（1 medium + 3 low + 2 info，扣除 info 实际 actionable 4 项）

**R5 的 4 项 actionable**：
1. 🟡 R5-2.1（rob.py success path cap-stats drift —— R4R-7.1 第 4 处家族实例）
2. 🟢 R5-2.2（rob.py rollback path cap-stats drift —— 与 R5-2.1 同模板）
3. 🟢 R5-2.3（rob.py counter path cap-stats drift —— 与 R5-2.1 同模板）
4. 🟢 R5-B.1（player_query.py asyncio.gather 漏 R4R-B.1 模板）

**评估**：
- 1 medium + 3 low，符合 PRD 预期（critical=0, high=0, medium ≤ 2）
- R5-2.1/2/3 是同一根因的 3 个 site（rob.py 内部 cap-stats 范围漏），实质是**"R4R-7.1 在 rob.py 漏修"** —— 与历史 cap-stats 范围漏修模式（R3E-1 红包 → R4R-7.1 dice/guess → R5-2.x rob）形成清晰的发现节奏
- R5-B.1 是 R4R-B.1 在 player_query.py 漏修，单点边角

**结论**：
- 系统已**显著收敛**。前 4 轮发现的所有 critical / high 问题均已闭合，本轮无 critical / high
- 剩余 cap-stats drift 是**家族 bug 的最后清扫**，rob.py 修完后 cap-stats 范围应彻底闭合
- 建议执行 **Round 6 修复 R5-2.1 + R5-2.2 + R5-2.3**（rob.py 一处修复同时覆盖 3 个 site）+ 可选 R5-B.1
- Round 6 之后 sweep 可结束 / 转入下游任务（WebUI 安全审计 / DB CHECK constraint / cooldown 设计 / audit_economy_change helper 等）

---

## 总结

- **R4 5 项修复全部回归通过**（M1 dice/guess applied_net + M2 11 处 rollback + M3 3 处 gather + M4 lottery normalize + M5 模板 polish）
- **R5 新发现 4 项 actionable**：3 项 rob.py cap-stats drift（同根因，1 次修复闭合）+ 1 项 player_query.py gather hardening
- **系统进入收敛尾声**：建议 Round 6 修完上述 4 项后，sweep 可结束
