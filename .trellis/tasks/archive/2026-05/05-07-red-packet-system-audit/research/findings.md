# 红包系统命令审计报告（已二次复查）

**审计对象**：`nextbot/plugins/red_packet.py` 5 个 `category="红包系统"` 命令
**审计日期**：2026-05-07
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证
**参照修复**：commit `0206834`（economy）+ minigame 审计 + `011aa68`（user）

## 严重级别分布（复查后）

- 🔴 必修：**3** —— 发红包 sender 并发金币凭空 / 抢红包 grabber.coins lost-update / 收回红包 sender.coins lost-update
- 🟠 应修：**3** —— 发红包同名 IntegrityError 未兜底 / 5 handler 缺异常兜底 / total_amount 缺上界
- 🟡 建议：**4** —— 抢红包事务跨 await / `_draw_lucky` 边界 / 列表 count+select 双查 / 抢空通知缺 broadcast
- 🟢 观察：**3**

复查中无误报，所有 🔴 / 🟠 项均已逐行核查源码确认。

---

## 🔴 必修

### R-1.1 — 发红包并发：sender.coins lost-update（金币凭空产生）

- **位置**：`red_packet.py:163-197`
- **现象**：
  - line 175 `sender_coins = int(sender.coins or 0)`（snapshot stale）
  - line 176 应用层余额检查
  - line 183 `sender.coins = sender_coins - total_amount`（绝对值写入，依赖 stale 读）
  - line 195 commit
- **复现**（A 余额 100）：
  1. A 几乎同时发 `发红包 平分 测试1 100 1` 与 `发红包 平分 测试2 50 1`
  2. Session1 读 sender_coins=100、检查通过、写 A.coins=0、INSERT 红包1（持有 100）
  3. Session2 在 Session1 commit 之前读 sender_coins=100（stale）、检查通过、写 A.coins=50、INSERT 红包2（持有 50）
  4. Session2 commit：A.coins=50（last-write-wins），红包池总额 = 150
  5. **总流通量 +100 凭空**
- **影响**：金币系统总量被打破。`economy.red_packet.send` 在 guest 默认权限里，脚本可批量复现
- **修复方案**（参照 economy F-2.1 / commit 0206834）：
  ```python
  rowcount = session.execute(
      update(User).where(User.user_id == user_id, User.coins >= total_amount)
      .values(coins=User.coins - total_amount)
  ).rowcount
  if rowcount == 0:
      coins_now = int(session.query(User.coins).filter(User.user_id == user_id).scalar() or 0)
      ... 金币不足回复 ...
      return
  ```

### R-2.1 — 抢红包：grabber.coins lost-update（金币少加）

- **位置**：`red_packet.py:296-308`
- **现象**：红包级别有保护（line 278 `_claim_slot_atomic` 原子扣红包余额 + line 289-294 UNIQUE 约束防重复抢），**但 grabber.coins 加金币这一步仍是 read-modify-write**：
  - line 296 `grabber = session.query(User).filter(...)`（读 stale）
  - line 301 `grabber.coins = int(grabber.coins or 0) + draw_amount`
  - line 308 commit
- **复现**（A 余额 100）：
  1. B 发 `转账 A 50`，几乎同时 A 抢一个 amount=10 的红包
  2. 转账 session：读 A.coins=100、写 A.coins=150、commit
  3. 抢红包 session：在转账 commit 之前读 A.coins=100（stale），抢到 10，line 301 写 A.coins=110，line 308 commit（**覆盖了 150**）
  4. 最终：A.coins=110，应为 160 → **少加 50（A 损失 50）**
- **影响**：用户损失而非凭空产生；常见场景"被转账时抢红包" / "同时抢两个不同红包" / "抢红包时被 admin 调金币"
- **修复方案**（参照 economy F-3.2/F-4.1）：
  ```python
  session.execute(
      update(User).where(User.user_id == user_id)
      .values(coins=User.coins + draw_amount)
  )
  ```

### R-3.1 — 收回红包：sender.coins lost-update（金币少加）

- **位置**：`red_packet.py:381-390`
- **现象**：与 R-2.1 完全同模式。`sa_update(RedPacket).where(status='active')` 已原子保证单次收回（line 369-376），**但 sender 退款这一步仍是 read-modify-write**：
  - line 384 `sender = session.query(User).filter(...)`
  - line 389 `sender.coins = int(sender.coins or 0) + refund_amount`
  - line 390 commit
- **复现**：与 R-2.1 同模板（sender 同时被转账 / 被 admin 调金币 / 抢别人红包等）
- **影响**：sender 收回剩余金额时少加
- **修复方案**：与 R-2.1 同模板

---

## 🟠 应修

### R-1.2 — 发红包同名：name UNIQUE TOCTOU + IntegrityError 未兜底

- **位置**：`red_packet.py:166-194`
- **现象**：
  - line 166 应用层 SELECT 检查 `RedPacket.name`
  - line 184-194 INSERT，commit 时若 race 触发 UNIQUE 冲突（`db.py:209` `RedPacket.name` 是 unique）→ `IntegrityError` 直接逃出 try/finally 给 NoneBot 顶层吞掉，用户无回复
- **复现**：脚本几乎同时发两条 `发红包 平分 同名 100 1`
- **影响**：用户体验差 + 日志噪音；金币不损失（commit 失败回滚）
- **修复方案**（参照 user 审计 commit 011aa68 同模板）：
  ```python
  try:
      session.commit()
  except IntegrityError:
      session.rollback()
      await bot.send(event, ... reply_failure("发红包", "红包名称已被使用过，请换一个"))
      return
  ```
  注意：修 R-1.1 后 sender 扣款已是条件 UPDATE，IntegrityError 回滚不会让 sender 多扣

### R-Common.1 — 5 handler 全部缺异常兜底

- **位置**：`handle_send` (121-214) / `handle_grab` (227-330) / `handle_withdraw` (343-406) / `handle_list_own` (457-526) / `handle_list_all` (550-621)
- **现象**：所有 handler 仅 `try: ... finally: session.close()`，无 `except`。`commit` 抛 IntegrityError、磁盘满、`bot.send` 网络错时，异常被 NoneBot 顶层吞掉，用户那侧无回复
- **同病**：与 economy F-Common.3 / minigame M-Common.2 一致
- **修复方案**：每个 handler 加 `except Exception: logger.exception + session.rollback (有 session) + reply_failure`（同模板）

### R-Common.2 — 发红包 total_amount 缺上界

- **位置**：`red_packet.py:140-148`
- **现象**：
  - `int(args[2])` 仅校验 `> 0`，**无上界**
  - `count` 受 schema 默认 100 / max 1000 保护
  - `total_amount` 完全无上界，handler 内只判 `total_amount >= count * min_amount_per_slot`
  - economy 已加 `MAX_COINS_AMOUNT = 100_000_000`，红包未跟进
- **影响**：admin 误操作 / 用户手抖输 9 位数无 defense-in-depth；R-1.1 修复后 rowcount=0 自然拦下，但应在解析阶段就拒绝
- **修复方案**：
  ```python
  from nextbot.plugins.economy import MAX_COINS_AMOUNT
  if total_amount > MAX_COINS_AMOUNT:
      ... 数量过大回复 ...
      return
  ```

---

## 🟡 建议

### R-2.2 — 抢红包事务中跨 await（影响小，结构脆弱）

- **位置**：`red_packet.py:240-313`
- **现象**：当前主路径未在持锁期间 await，但代码结构容易在未来被改坏
- **修复**：把 `await bot.send(...)` 全部移出 try 块外（先 close session 再发回复）。**非必修**

### R-2.3 — `_draw_lucky` 极端边界

- **位置**：`red_packet.py:65-73`
- **现象**：`high < 1: return 1` 兜底，理论上发红包阶段已校验不会触发，但有数据腐败时会返回 1 后被 `_claim_slot_atomic` 拒绝，文案误导
- **修复**：defense-in-depth 缺失，**非必修**

### R-Common.3 — 列表 count + select 双查询无一致性保证

- **位置**：`red_packet.py:478-495` / `570-587`
- **现象**：count 之后、select 之前可能有新红包写入或被抢空，total_pages 与显示行数偶发偏差
- **修复**：**非必修**，极偶发，不引发数据错误

### R-1.3 — 发红包成功 / 抢空通知缺 broadcast

- **位置**：`red_packet.py:203-214` / `320-330`
- **现象**：发红包后只 @ 发送方；抢红包成功只 @ 抢者。群里其他用户看不到"红包来了" / "红包被抢空了"
- **修复**：UX 偏好，**非必修**

---

## 🟢 观察

- **R-Obs.1**：`RedPacketClaim` 表查询 `red_packet_id + claimer_user_id` 走 `uq_redpacket_claimer` unique 索引，OK
- **R-Obs.2**：`RedPacket.name` 是全局 unique（`db.py:209`），不是 `(sender, name)` unique；不同人不能用同名红包。如业务允许跨用户复用红包名（如"春节红包"），当前 schema 限制
- **R-Obs.3**：抢红包文案"手慢了一步"覆盖 `_claim_slot_atomic` 失败的所有原因；与"该红包已关闭"有重叠

---

## 与最近修复的对照

| 修复点 | 红包系统是否同病 |
|---|---|
| economy F-2.1 转账并发条件 UPDATE | **R-1.1 同病** |
| F-3.2/F-4.1 add/remove lost-update | **R-2.1 / R-3.1 同病** |
| F-Common.1 MAX_COINS_AMOUNT 上界 | **R-Common.2 缺** |
| F-Common.3 异常兜底 | **R-Common.1 全部缺** |
| user 注册 IntegrityError 兜底 | **R-1.2 缺** |
| `_safe_param_int`（minigame）| 红包未用，schema 已保证；非必修 |
| `temp_screenshot_path` | 已迁移 |
| `User.name` 唯一索引 | 自动受益 |

---

## 推荐处理顺序

1. 🔴 **R-1.1 发红包并发金币凭空产生**（攻击面大，可脚本化）
2. 🔴 **R-2.1 / R-3.1 lost-update**（同模板，2 处一次性改）
3. 🟠 **R-1.2 IntegrityError 兜底**
4. 🟠 **R-Common.1 异常兜底 + R-Common.2 上界**（统一加常量复用 + try/except 模板）
5. 🟡 R-2.2 / R-2.3 / R-Common.3 / R-1.3（下一轮）
