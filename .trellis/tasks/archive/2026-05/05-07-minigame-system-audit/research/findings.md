# 小游戏系统命令审计报告（已二次复查）

**审计对象**：`category="小游戏系统"` 4 个命令（猜数字 / 掷骰子 / 抢劫 / 切换抢劫保护）
**审计日期**：2026-05-07
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证
**参照修复**：commit `0206834`（economy 审计修复）+ `011aa68`（user 审计修复）

## 严重级别分布（复查后）

- 🔴 必修：**4** —— 所有 4 个命令都有 lost-update / 金币凭空产生 / 双扣
- 🟠 应修：**2** —— amount 上界缺失 + 异常未兜底
- 🟡 建议：**3** —— 内存冷却风格 / 参数防御 / 自抢拦截顺序
- 🟢 观察：**3**

复查中无误报，所有 🔴 / 🟠 项均已逐行核查源码确认。

---

## 🔴 必修

### M-1.1 — 猜数字并发金币凭空产生（lost-update）

- **位置**：`guess_number.py:166-218`
- **现象**：典型 `select user → check coins ≥ cost → user.coins = coins + net → commit` 模式：
  - `line 168` query `User`
  - `line 173-176` 应用层余额检查
  - `line 207` `user.coins = coins + net`（基于 stale 余额）
  - `line 214` commit
- **复现**：A 余额 100 → 同时发两条 `猜数字 50 100` → 两 session 都读 100 → 命中倍率 10 时各算 net=900 → 最终 A=1000（**金币 +800 凭空**）；同样若都未命中 net=-100，最终也是只扣 1 次（白嫖）
- **冷却失效**：`_cooldown_map`（`line 20`）是进程内 dict，line 220 在 `session.close()` 之后才写入，并发命令在 handler 主体阶段冷却 dict 仍是空 → 完全不挡
- **OneBot V11 触发条件**：`await bot.send` (line 127) 一旦 yield 就允许第二条命令并发进入 handler
- **影响**：金币系统总量被打破；可脚本批量复现
- **修复方案**：参照 economy F-2.1 / commit 0206834 模板：用条件 UPDATE 扣押金（`update().where(coins >= cost).values(coins = coins - cost)`），rowcount=0 即金币不足；payout/累计字段也改为条件 UPDATE

### M-2.1 — 掷骰子并发金币凭空产生（lost-update）

- **位置**：`dice.py:135-182`
- **现象**：与 M-1.1 完全同模式：line 137 query → line 142-145 检查 → line 171 `user.coins = coins + net` → line 178 commit
- **复现**：同 M-1.1 模板
- **影响**：同 M-1.1
- **修复方案**：与 M-1.1 同模板

### M-3.1 — 抢劫并发：金币凭空产生 + 冷却被绕过 + 统计字段失真

- **位置**：`rob.py:176-283`
- **现象**：3 个原子性问题叠加：
  - **(a) attacker.coins / victim.coins lost-update**：line 247-248（success）/ 257-258（counter）/ 266 / 273（police/fail）全部基于 line 203-204 读到的 stale 余额做减加
  - **(b) `last_rob_time` 冷却窗口竞态**：line 190 应用层判定，line 277 才落库 → 两条几乎同时的抢劫命令都可读到 stale `last_rob_time` 都通过冷却检查
  - **(c) `rob_total_*` 统计 lost-update**：line 249-275 全部 `field = int(field or 0) + N` → 同一 victim 被并发抢两次时 `rob_total_loss` 少累加，leaderboard 失真
- **最危险变种**（多攻击者抢同一 victim）：
  1. attacker1 / attacker2 / victim V (V 余额 1000) → 1/2 几乎同时 `抢劫 V`
  2. Session1 读 V.coins=1000，扣 V.coins=900，attacker1 +100，commit
  3. Session2 在 Session1 commit 前读 V.coins=1000（stale），扣 V.coins=900，attacker2 +100，commit
  4. 最终 V 实际只损失 100，但两 attacker 各 +100 → **金币凭空 +100**
- **影响**：金币系统总量被打破 + 冷却失效 + 统计失真。`economy.rob` 在 guest 默认权限
- **修复方案**：成功路径用条件 UPDATE 同时校验 `victim.coins >= amount AND victim.rob_protected = False AND attacker 冷却已过`：
  ```python
  v_rows = update(User).where(
      user_id=victim, coins>=amount, rob_protected.is_(False)
  ).values(coins=coins - amount, rob_total_loss=rob_total_loss + amount).rowcount
  if v_rows == 0: ...拒绝并 return
  r_rows = update(User).where(
      user_id=attacker, or_(last_rob_time.is_(None), last_rob_time < cutoff),
      rob_protected.is_(False)
  ).values(coins=coins + amount, ..., last_rob_time=now).rowcount
  if r_rows == 0:
      # 回滚 victim 扣款
      update(User).where(user_id=victim).values(
          coins=coins + amount, rob_total_loss=rob_total_loss - amount,
      )
      return
  ```
  其他 3 个分支（counter / police / fail）同样改为条件 UPDATE

### M-4.1 — 切换抢劫保护：1 次 cost 切 2 次状态 / 双扣

- **位置**：`rob_protection.py:62-90`
- **现象**：handler 走 `select → check rob_protected != target → check coins ≥ cost → user.coins -= cost; user.rob_protected = target → commit`：
  - line 69 应用层"已处于该状态"检查
  - line 83-85 全部基于 stale 字段
- **复现**（A 余额 1000、`rob_protected=False`、`toggle_cost=200`）：
  1. A 几乎同时发两条 `切换抢劫保护 开`
  2. Session1 读 rob_protected=False、coins=1000，写 coins=800、rob_protected=True，commit
  3. Session2 在 Session1 commit 前读 rob_protected=False（stale！），coins=1000（stale），写 coins=800（**重复扣减**）、rob_protected=True（同值覆盖），commit
  4. 最终：rob_protected=True（1 次状态切换），coins=600（**少了 400**，应为 800）→ 用户花 1 份 cost 完成 2 次"切换按钮"
- **影响**：用户白嫖一次切换费用，guest 权限可触发
- **修复方案**：原子条件 UPDATE 一次完成扣钱 + 切状态 + 互斥旧状态：
  ```python
  rowcount = update(User).where(
      user_id=user_id, coins>=cost, rob_protected.is_(not target),
  ).values(coins=coins - cost, rob_protected=target).rowcount
  if rowcount == 0:
      # 拉一次最新状态判定具体原因（金币不足 / 已处于该状态）
      ...
  ```

---

## 🟠 应修

### M-Common.1 — 押注 / 抢劫金额上界缺失

- **位置**：
  - 猜数字 cost 校验 `guess_number.py:134-149` 仅 min
  - 掷骰子 cost 校验 `dice.py:103-118` 仅 min
  - 切换抢劫保护 cost schema `rob_protection.py:59` 无 max
  - 抢劫由 victim_coins 自然限制
- **现象**：economy 已加 `MAX_COINS_AMOUNT = 100_000_000`（commit 0206834），minigame 4 处未跟进
- **影响**：玩家手抖输入 9 位数 cost / 配置失误时缺少 defense-in-depth 上界
- **修复方案**：复用 `economy.MAX_COINS_AMOUNT`，在 cost 校验里加 `if cost > MAX_COINS_AMOUNT`

### M-Common.2 — 4 个 handler 缺异常兜底

- **位置**：4 个 plugin 全部
- **现象**：仅有 `except ValueError` 用于参数解析；DB / commit / `bot.send` 异常无任何兜底，磁盘满 / 网络异常时 NoneBot 顶层吞掉错误
- **修复方案**：每个 handler 加 `except Exception: logger.exception(...); session.rollback(); await bot.send(reply_failure(...))`（与 economy F-Common.3 同模板）

---

## 🟡 建议

### M-Common.3 — `_cooldown_map` 进程内存方案：reload / 多进程下失效

- **位置**：`guess_number.py:20`、`dice.py:25`
- **现象**：内存 dict 重启即清零；多进程部署完全无效
- **修复**：把猜数字 / 掷骰子的冷却也落 DB（仿 rob 的 `last_rob_time` 模式），与 rob 风格统一。**非必修，下一轮**

### M-Common.4 — `int(get_current_param(...))` 抛异常未防御

- **位置**：4 个 plugin 多处
- **现象**：依赖 `_validate_by_schema` 保证入库值合法 int。schema 校验完整，理论上拦得住，但属 defense-in-depth 缺失
- **修复**：可加 try/except fallback to default。**非必修**

### M-3.3 — 抢劫自抢拦截顺序滞后

- **位置**：`rob.py:140-161`
- **现象**：先做 `resolve_user_id_arg_with_fallback`（按名称 lookup DB）才检测 `robber_id == target_user_id`，自抢需要先一次 SQL
- **修复**：在 lookup 之前先看 `args[0]` 是否为数字且等于 `event.get_user_id()` 短路。**非必修**

---

## 🟢 观察

- **M-Obs.1**：`random.randint` 不是密码学随机（Mersenne Twister）。理论上观察足量历史可推断状态，nonebot 多用户穿插不可行。OK
- **M-Obs.2**：抢劫保护检查放在金币检查之后（`rob.py:205-216` 早于 `218-224`）。若 victim 既身无分文又开启保护，回复优先返回"对方身无分文"。UX 微小不一致
- **M-Obs.3**：engine 单例已落地（`db.py:356-366`），minigame 自动受益

---

## 与 economy 修复（commit 0206834）的对照

| economy 修复点 | minigame 是否同病 / 适配方法 |
|---|---|
| F-2.1 转账并发条件 UPDATE | **M-1.1 / M-2.1 / M-3.1 / M-4.1 全部同病**，套同模板 |
| F-3.2/F-4.1 add/remove 条件 UPDATE | 同上 |
| F-Common.1 `MAX_COINS_AMOUNT` 上界 | **M-Common.1 全部缺**，复用常量 |
| F-Common.3 异常兜底回复 | **M-Common.2 全部缺**，套同模板 |
| F-Common.2 engine 单例 | 已修，minigame 已自动受益 |

---

## 推荐处理优先级

1. 🔴 **M-3.1 抢劫并发**（攻击面最大且最易脚本化，先修）
2. 🔴 **M-1.1 / M-2.1 猜数字 / 掷骰子并发**（同模板，2 处一次性改）
3. 🔴 **M-4.1 抢劫保护双扣**（小逻辑大影响）
4. 🟠 **M-Common.1 amount 上界 + M-Common.2 异常兜底**（统一加常量复用 + try/except 模板）
5. 🟡 **M-Common.3 / M-Common.4 / M-3.3 / Obs**（下一轮）
