# 红包系统二审报告

**审计对象**：`nextbot/plugins/red_packet.py`
**二审日期**：2026-05-07
**初审报告**：`research/findings.md`
**目标**：验证 A 全修方案落实情况、行为不变性、是否引入新问题

---

## Phase 1: 修复项落实情况

| ID | 修复项 | 落实状态 | 位置 |
|---|---|---|---|
| R-1.1 | 发红包 sender 条件 UPDATE | ✅ 已落实 | line 194-207；`update(User).where(user_id=user_id, coins>=total_amount).values(coins=User.coins-total_amount)`，rowcount=0 回读余额并回复"金币不足" |
| R-2.1 | 抢红包 grabber 条件 UPDATE | ✅ 已落实 | line 358-362；`update(User).values(coins=User.coins+draw_amount)` |
| R-3.1 | 收回红包 sender 条件 UPDATE | ✅ 已落实 | line 464-468；同模板 |
| R-1.2 | IntegrityError 兜底 + 回滚 sender 余额 | ⚠️ **已添加但实现错误**（详见 Phase 3 NEW-1） | line 220-232 |
| R-Common.1 | 5 handler 异常兜底 | ✅ 已落实 | handle_send 235-241 / handle_grab 374-380 / handle_withdraw 471-477 / handle_list_own 618-624 / handle_list_all 722-728 |
| R-Common.2 | total_amount 上界 | ✅ 已落实 | line 24 import `MAX_COINS_AMOUNT`；line 158-163 校验；文案与 economy 一致 |
| R-2.3 | `_draw_lucky` 边界 warning log | ✅ 已落实 | line 76-81 |

---

## Phase 2: 行为不变性

### 命令 1 — 发红包

- ✅ 入口校验文案完全一致（"类型仅支持 平分 或 拼手气" / "红包名称长度不能超过 32 字符" / "总金额和个数必须为正整数" / "个数超过上限" / "总金额不足以每人至少 X 金币"）
- ✅ 成功 `reply_block` 输出（标题 / 名称 / 类型 / 总金额 / hint）格式与原版一致
- ✅ "金币不足（当前 X，需 Y）" 文案保留；rowcount=0 时通过单独 SELECT 回读 X，文案与原 `sender_coins` 路径完全等价
- ✅ "请先注册账号" 错误路径保留
- ✅ "红包名称已被使用过，请换一个" 文案保留（pre-check 路径与新增 IntegrityError 兜底路径同文案）
- 🆕 新增 `MAX_COINS_AMOUNT` 上界文案"金额过大（最多 100000000）"——这是新增校验，符合 R-Common.2 设计目标（不算破坏，是补全）
- 🆕 新增 "处理失败，请稍后重试"——R-Common.1 兜底，符合用户验收

### 命令 2 — 抢红包

- ✅ 4 种结果文案全部保留：
  - "红包不存在" (line 299)
  - "该红包已关闭" (line 302、318)
  - "你已经抢过这个红包了" (line 312、349)
  - "手慢了一步" (line 336)
  - "请先注册账号" (line 355)
- ✅ 成功 `reply_block`（"抢红包成功" 标题 / 名称 / 获得金币 / 已抢 X/Y）格式一致
- ✅ 入口校验 `len(args) != 1` / 空名称 → `raise_command_usage()` 行为不变

### 命令 3 — 收回红包

- ✅ 错误文案全部保留："红包不存在" / "只能收回自己发的红包" / "该红包已关闭" / "请先注册账号"
- ✅ 成功 `reply_block`（"收回成功" / 红包名 / 退回 X 金币）格式一致
- ✅ rowcount=0 路径保留（line 450-453），rollback 行为不变

### 命令 4 — 我的红包

- ✅ "页数必须为正整数" / "超出总页数（共 X 页）" 文案保留
- ✅ 截图渲染 + 上下文管理器（`temp_screenshot_path`）保留
- ✅ logger.info 渲染地址日志保留
- 🆕 新增"处理失败，请稍后重试"，符合 R-Common.1

### 命令 5 — 红包列表

- ✅ 同命令 4 逻辑，文案不变
- ✅ N+1 已经避免（一次 `User.user_id.in_(sender_ids)` 批量查 sender 名）

### 总体评估
- 5 命令对外行为整体一致，仅新增的"金额过大"和"处理失败"是约定补全文案
- 但 **handle_send 的 IntegrityError 兜底路径在金币守恒维度行为破坏**（详见 Phase 3 NEW-1）

---

## Phase 3: 新引入问题（关键）

### 🔴 NEW-1（必修）—— handle_send IntegrityError 路径双重退款，导致金币凭空增加

- **位置**：`red_packet.py:220-232`
- **核心代码**：
  ```python
  session.add(packet)
  try:
      session.commit()
  except IntegrityError:
      session.rollback()                                          # (a)
      session.execute(
          sa_update(User).where(User.user_id == user_id)
          .values(coins=User.coins + total_amount)                # (b)
      )
      session.commit()
      await bot.send(event, ... "红包名称已被使用过，请换一个")
      return
  ```
- **错误本质**：`session.rollback()` (a) 已经把同事务内的"扣 sender total_amount"那条 UPDATE 一并回滚——SQLAlchemy 2.0 + `autocommit=False` 下，同一个 `Session` 在 commit 失败后调用 rollback 会回滚整笔事务（包括前面已 flush 但未 commit 的 UPDATE）。然后 (b) 又对 sender 加了一次 `+total_amount`，相当于"先回滚扣款 → 再加一次 total_amount" → sender 凭空 +total_amount。
- **复现脚本**（A 余额 N，可直接利用，无需并发）：
  1. A 用任意名字成功发红包 → 占用一个 name
  2. A 再次用同名字发同一红包，触发 line 182-185 pre-check 命中 → 已经返回，不进 IntegrityError 分支。**TOCTOU 才能触发本 bug**：必须在 line 182 SELECT 之后到 line 221 commit 之前，让另一个事务先插入了同名 RedPacket。
  3. 实际利用：A 同时发起两条 `发红包 平分 同名 100 1`：
     - Session1：SELECT 不存在 → 扣 100 → INSERT → commit ✅；A 余额 N-100
     - Session2：SELECT 不存在（与 Session1 并发，未提交）→ 扣 100（A 余额 N-200）→ INSERT 撞 UNIQUE → IntegrityError → **rollback 把 Session2 的扣 100 回滚到 N-100**（实际上 Session2 看到的不是 N-200 而是它自己事务开始时的快照）→ **再 UPDATE +100** → A 余额变 N
  4. 净效果：A 发 1 个红包占用 100 金币，第二个失败但 A 余额不仅未损失任何额度，反而比"扣后状态"多了 100，**等价于第二个红包白发**
- **更糟糕的攻击模型**：在并发场景下，攻击者构造极高 RPS 同名重发，每次失败的事务都会"白送" total_amount。这是 attacker-friendly 的金币凭空生产 bug。
- **影响**：金币守恒被打破——比 R-1.1 修复前更严重，因为 R-1.1 至少需要并发，而 NEW-1 是修复路径自身的逻辑错。
- **修复方案**：
  ```python
  except IntegrityError:
      session.rollback()    # rollback 已经撤销了扣款，就此结束
      await bot.send(event, ... "红包名称已被使用过，请换一个")
      return
  ```
  即**删除** line 225-230 那段"再加一次 total_amount"的 UPDATE，rollback 已经天然恢复 sender 余额。模板与 `economy.py:214-217` 签到 IntegrityError 路径完全一致——economy 的 rollback 也是依赖事务原子性自然撤销前面的金币 UPDATE。

### 🟠 NEW-2（应修）—— 异常兜底块的 inner `try/except: pass` 形式

- **位置**：5 处 `except Exception:` 中嵌套 `try: bot.send ... except: pass`，line 237/376/473/620/724
- **现象**：bot.send 失败时被悄悄吞掉，仅 `logger.exception` 留痕。这与 economy / minigame 同模板，本身可接受。但 ruff 报 SIM105 提示用 `contextlib.suppress`。**非破坏性，纯风格**。
- **影响**：无功能性问题；只是 lint 噪音。
- **是否修**：可选。

### 🟢 NEW-3（观察）—— `MAX_COINS_AMOUNT` 跨模块导入

- **位置**：line 24 `from nextbot.plugins.economy import MAX_COINS_AMOUNT`
- **现象**：red_packet 反向依赖 economy 顶层常量。验证：grep 确认 economy.py 不导入 red_packet（无循环）；NoneBot plugin loader 加载顺序按目录字母序，`economy.py` 在 `red_packet.py` 之前；安全。
- **建议**：未来若有更多金币上界跨模块复用，可下沉到独立常量模块（如 `nextbot/coins_constants.py`）。

### 🟢 NEW-4（观察）—— rowcount 类型与 SQLAlchemy 2.0

- **位置**：line 198 / 450
- **现象**：SQLAlchemy 2.0 `Result.rowcount` 在 ORM-update 模式下可能返回 `-1`。但本仓库 `update(User).where(...).values(...)` 是 Core 层 update 语句（非 `Session.execute(update(...).execution_options(synchronize_session=...))`），SQLite + Core update 返回精确 rowcount（0 或 1）。
- **额外验证**：pyright 报告 line 198/450 `Cannot access attribute "rowcount" for class "Result[Any]"`——这是已存在的 type stub 问题（pre-fix line 376 同样报错），不影响运行时。
- **建议**：可选——加 `# type: ignore[attr-defined]` 注释或用 `cast`。

### ✅ NEW-5（已验证）—— 抢红包/收回红包 None 检查路径完整

- **抢红包**（line 352-356）：`grabber = session.query(...).first()`；None 时 `session.rollback()` 撤销 `_claim_slot_atomic` 与 `RedPacketClaim` flush，然后回复"请先注册账号"。原子性保留。
- **收回红包**（line 458-462）：`sender = session.query(...).first()`；None 时 `session.rollback()` 撤销 `RedPacket` 状态 UPDATE，回复"请先注册账号"。原子性保留。
- 两处 `None 检查 + rollback` 与 pre-fix 完全一致。

### ✅ NEW-6（已验证）—— `_send_red_packet_image` 上下文管理器未破坏

- handle_list_own / handle_list_all 把整个 `_send_red_packet_image` 调用纳入新增的 `try: ... except Exception:` 块。`async with temp_screenshot_path(...)` 在 try 块**内部**：异常发生时，`async with` 的 `__aexit__` 仍会被调用清理临时文件，再被外层 except 捕获。资源清理路径完整。

### ✅ NEW-7（已验证）—— 金币守恒不变量（除 NEW-1 外）

- 抢红包：`grabber.coins += draw_amount` 与 `_claim_slot_atomic` 同事务，commit 失败时一并 rollback；金额来源是红包池减扣，对账平。
- 收回红包：`sender.coins += refund_amount` 与 RedPacket 状态 UPDATE 同事务；refund_amount 等于剩余 remaining_amount；对账平。
- 发红包：**正常路径** sender 扣 total_amount + RedPacket 入账 total_amount，对账平。**异常路径见 NEW-1**。

### ✅ NEW-8（已验证）—— `RedPacketClaim` flush 后的 None 检查回滚顺序

- handle_grab line 339-356：`session.add(claim)` → `session.flush()` → 若 IntegrityError → rollback；若 flush 成功，再读 grabber，None 时 rollback 撤销 RedPacketClaim 与 `_claim_slot_atomic`。回滚序列正确。

---

## Phase 4: 整体回归

### ✅ 5 handler 整体回归

- **SQL 注入**：所有 query / update 都用参数化 ORM；无注入面。
- **越权**：handle_withdraw 行 435 `if packet.sender_user_id != user_id` 仍在；非创建者无法收回。抢红包无群组限制（pre-fix 也没有，业务设计如此）。
- **资源泄漏**：5 handler 全部 `finally: session.close()` 或 `async with temp_screenshot_path`；无新泄漏。
- **错误处理缺口**：5 handler 全有外层 except；R-Common.1 落实。
- **race condition**：抢红包 grabber.coins 已用条件 UPDATE（R-2.1）；收回 sender.coins 已用条件 UPDATE（R-3.1）；剩余 race 仅 NEW-1。
- **持有 SQLite 写锁期间 await**：抢红包 line 297-369 路径中只在 `await bot.send(...)` 处 yield，但所有 await bot.send 之前都已 `session.rollback()` 或事务终态。**主成功路径**（rowcount>0 → flush → grabber UPDATE → commit）**全程不 await**——commit 之后才 await。无锁穿越问题。

### ⚠️ 整体回归发现

- **NEW-1 是阻断性问题**：A 全修方案的 R-1.2 修复不能在当前实现下合并，否则比修复前更严重。
- 其他维度（性能、N+1、文案、错误处理）均通过。

---

## 结论

| 验收标准 | 评估 |
|---|---|
| 1. 无破坏性（输入 / 输出 / 文案 / 错误回复一致） | **部分通过**——文案 OK，但 NEW-1 破坏金币守恒不变量（attacker-exploitable） |
| 2. 开箱即用（无 schema 改动） | ✅ 通过——纯逻辑修改 |
| 3. 修后红包系统再无漏洞缺陷与可优化空间 | ❌ **不通过**——NEW-1 引入新漏洞 |

### 总体：**需要补修**

仅需一处 3 行改动：删除 `red_packet.py:225-230` 的"二次 UPDATE +total_amount"（保留 `session.rollback()` 与 `bot.send(... 红包名称已被使用过)`）即可。

补修后建议再跑一次 audit 确认 R-1.2 IntegrityError 路径金币守恒。

---

## 补修建议

```python
# nextbot/plugins/red_packet.py:220-232
        session.add(packet)
        try:
            session.commit()
        except IntegrityError:
-           # 名称撞 UNIQUE 等约束冲突：必须把已扣的 sender 余额原子退回
-           session.rollback()
-           session.execute(
-               sa_update(User)
-               .where(User.user_id == user_id)
-               .values(coins=User.coins + total_amount)
-           )
-           session.commit()
+           # 名称撞 UNIQUE 等约束冲突：rollback 已撤销前面的扣款，无需再退款
+           session.rollback()
            await bot.send(event, at + " " + reply_failure("发红包", "红包名称已被使用过，请换一个"))
            return
```

参照 `economy.py:214-217` 签到 IntegrityError 路径——economy 在 rollback 后仅回复用户、不再额外退款，正是因为 rollback 自然撤销了同事务的金币 UPDATE。red_packet 应严格对齐该模板。
