# 经济系统命令审计报告（已二次复查）

**审计对象**：`nextbot/plugins/economy.py` 4 个 `category="经济系统"` 命令
**审计日期**：2026-05-06
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证

## 严重级别分布（复查后）

- 🔴 必修：**2**（转账并发金币凭空产生 / 签到并发 UserSignRecord 双写）
- 🟠 应修：**3**（user_sign_record 缺唯一约束 / 跨午夜签到短窗口误拒 / 添加金币 + 扣除金币 lost-update）
- 🟡 建议：**4**（amount 上界 / 解析风格不一致 / 多 session 项目级 / 异常未兜底回复）
- 🟢 观察：**3**

复查复核：所有 🔴 / 🟠 / 🟡 项均已对照源码逐行确认，行号准确。

---

## 🔴 必修

### F-2.1 — 转账并发：金币凭空产生（系统总量被打破）

- **位置**：`economy.py:277-296`
- **现象**：handler 内 `select sender → 检查 sender.coins ≥ amount → sender.coins -= amount; target.coins += amount; commit`。**无行级锁**、**无条件 UPDATE (`WHERE coins ≥ amount`)**、**无版本号**。`get_session()` 每次 `create_engine()`，并发 handler 拿到独立连接 / 独立事务，可读到 stale 余额。
- **复现**：
  1. A 余额 100，目标 B / C 是任意已注册用户
  2. 几乎同时（几十毫秒内）发送：`转账 B 100` 与 `转账 C 100`
  3. Session1 读 sender_coins=100、检查通过、写 A.coins=0、commit
  4. Session2 在 Session1 commit 之前读 sender_coins=100、检查通过、写 A.coins=0、B/C +100、commit
  5. 最终：A=0、B=+100、C=+100，**总流通量 +100**
- **影响**：金币系统总量守恒被打破；恶意用户用脚本批量并发转账即可放大资产，可重复触发。
- **修复方案**：
  ```sql
  UPDATE user SET coins = coins - :amt WHERE user_id = :sender AND coins >= :amt
  ```
  rowcount=0 时退回"金币不足"；target 用 `UPDATE ... SET coins = coins + :amt`；两个 UPDATE 包同一事务（`with engine.begin() as conn:`）。

### F-1.1 — 签到并发：UserSignRecord 双写 + UI 与实际余额不一致

- **位置**：`economy.py:155-190`
- **现象**：
  - 第 165 行用应用层 `if bool(user.signed_today) or last_sign_date == today_text:` 代替锁
  - 第 169-189 做 5 处写（coins / signed_today / last_sign_date / sign_streak / sign_total）+ 1 条 INSERT user_sign_record
  - 第 190 才 commit
  - `user_sign_record` 表（`db.py:180-189`）**没有 (user_id, sign_date) 唯一约束**
- **复现**：
  1. A 当前 `signed_today=False, last_sign_date=2026-05-05`，今天 2026-05-06
  2. 用脚本几乎同时发送两条 `签到`
  3. Session1 读通过，random base=20，streak=2，total=30
  4. Session2 同时读通过（signed_today 仍 False），random base=80，streak=2，total=90
  5. Session1 commit：coins=130，UserSignRecord 多 1 条
  6. Session2 commit：coins=190（基于同一 stale 100 基线，**覆盖** Session1 的 130），UserSignRecord 多第 2 条同 user/同 date 记录
- **金币流细节**：因 last-write-wins，coins 字段最终是 190（不是 100+30+90=220），所以"双倍发金币"不会出现。但：
  - UserSignRecord 多余记录 → leaderboard `daily_sign` / `signin` 计数失真，`today_order`（line 192-196）多算
  - `sign_total = old + 1` 同样 lost-update（最终 +1，不是 +2）
  - 用户在第一条命令的回复看到"获得 30"，实际余额却 +90 — **UI 与实际不一致**
- **影响**：签到记录脏数据 + UI 错乱。可被脚本触发，无需任何越权。
- **修复方案**：
  1. `user_sign_record` 加 `UniqueConstraint("user_id", "sign_date")`（参见 F-3.1）
  2. handler 改条件 UPDATE：
     ```sql
     UPDATE user SET coins = coins + :reward, ..., sign_total = sign_total + 1
     WHERE user_id = :uid AND last_sign_date != :today AND signed_today = 0
     ```
     rowcount=0 即"今天已签到过"

---

## 🟠 应修

### F-3.1 — user_sign_record 表缺 (user_id, sign_date) 唯一约束

- **位置**：`db.py:180-189`
- **现象**：`UserSignRecord` 没有任何 `UniqueConstraint` / 索引；任何 `(user_id, sign_date)` 重复均可入库
- **影响**：F-1.1 的根因之一；schema 层无法兜底
- **修复方案**：
  ```python
  __table_args__ = (UniqueConstraint("user_id", "sign_date", name="uq_sign_record_user_date"),)
  ```
  并在 `ensure_sign_record_schema()` 启动时建 `CREATE UNIQUE INDEX IF NOT EXISTS`（已有重复需先去重，否则降级建非唯一索引并 logger.warning，不阻断启动）—— 与 user-system 修复中 `ensure_user_name_unique_schema` 同模式

### F-1.2 — 跨午夜短窗口签到被错误拒绝

- **位置**：`economy.py:165` + `signin_reset.py:33-40`
- **现象**：
  - worker 用 `time.sleep(seconds_until_next_beijing_midnight())` 唤醒后才 reset `signed_today=False`
  - 唤醒到 commit 完成的几毫秒～几秒窗口内，DB 中 `signed_today=True`、`last_sign_date=昨日`
  - line 165：`if bool(user.signed_today) or last_sign_date == today_text:` —— `True or False` = True → 误拒
- **复现**：
  1. 23:59:50 用 A 签到 → `signed_today=True, last_sign_date="2026-05-05"`
  2. 00:00:01（worker 还在 sleep 唤醒中）A 再签
  3. handler 读 `signed_today=True, last_sign_date="2026-05-05"`，今日 today_text="2026-05-06"
  4. `True or False` = True → 拒绝"今天已经签到过了"
- **影响**：跨日时刻几毫秒到几秒的拒绝窗口；玩家抢"今日第一签"被屏蔽
- **修复方案**（**复查发现 sub-agent 给的 `or → and` 不够稳**）：

  推荐方案：单一真源，**只用 last_sign_date == today_text**：
  ```python
  if last_sign_date == today_text:
      ...拒绝...
  ```
  完全不依赖 `signed_today` 字段（该字段实际可由 last_sign_date 推导，是冗余信息）。这样：
  - 不依赖 worker 准时 reset
  - 跨午夜立刻可签（today_text 一变，last_sign_date 立刻不等于）
  - 同日重复签到仍能拦（last_sign_date == today_text）
  
  长期方案：删除 `signed_today` + reset worker，进一步简化系统

### F-3.2 + F-4.1 — 添加金币 / 扣除金币 lost-update

- **位置**：`economy.py:358-366`（add）、`economy.py:428-440`（remove）
- **现象**：`user.coins += amount; commit` / `user.coins -= amount; commit`，两个 admin 并发触发会基于同一 stale `coins`
- **复现**：X 当前 100 → admin A 与 admin B 几乎同时发 `添加金币 X 50` → 两 session 都读 100 都写 150 → 最终 X=150（**少加 50**）
- **影响**：与 F-2.1 同根，但触发面只有 admin（`economy.coins.add` / `remove` 不在 `DEFAULT_GUEST_PERMISSIONS`），守恒方向无问题（不会让金币凭空产生），**主要场景是 webui 批量发奖时少发**
- **修复方案**：与 F-2.1 同模式：
  ```sql
  -- add：
  UPDATE user SET coins = coins + :amt WHERE user_id = :uid
  -- remove：
  UPDATE user SET coins = coins - :amt WHERE user_id = :uid AND coins >= :amt
  ```
  remove rowcount=0 即金币不足

---

## 🟡 建议

### F-Common.1 — 4 个命令的 `amount` 都缺上界

- **位置**：`economy.py:38-46`、`262-270`、`353-356`、`423-426`
- **现象**：只校验 `> 0`，没有上界。SQLite INTEGER 64 位，常规游戏远不到溢出，但 admin 手抖输入 `添加金币 X 99999999` 不会被拦
- **修复**：定义 `MAX_AMOUNT = 100_000_000`，4 处校验都加 `amount > MAX_AMOUNT` 退回"数量过大"

### F-2.2 — 转账 `int(amount_str)` 与 add/remove `_parse_positive_int` 解析风格不一致

- **位置**：`economy.py:262-267`
- **现象**：转账接受 `+100`、`1_000`（Python 千分位下划线 = 1000）；add/remove 用 `_parse_positive_int` 严格 `isdigit()` 拒绝
- **影响**：行为差异 + 可维护性，无金额误差
- **修复**：转账也改用 `_parse_positive_int(args[1])`

### F-Common.2 — handler 多次 `get_session` / 每次 `create_engine`

- **位置**：与 user-system 审计 F-4.2 同根（`db.py:376-379`）
- **修复**：**项目级** —— engine + sessionmaker 改全局单例，与 user-system F-4.2 一并修

### F-Common.3 — handler 未对 DB / NoneBot 异常做 try/except 兜底

- **位置**：`economy.py:158-228`、`277-315`、`358-385`、`428-459`
- **现象**：4 个 handler 都只 `try: ... finally: session.close()`，没有 `except`。`session.commit()` 抛 `IntegrityError`、磁盘满、bot.send 网络错时，异常被 NoneBot 吞掉，用户那侧无回复
- **修复**：在 `finally` 之前加 `except Exception as exc: logger.exception(...); await bot.send(event, reply_failure(...))`

---

## 🟢 观察

- **F-Obs.1**：`int(event.get_user_id())` 跨 adapter 兼容性 —— 当前 OneBot V11 安全，与 user-system F-1.4 同根
- **F-Obs.2**：admin 权限隔离已生效，但若运营误把 `economy.coins.add` 加进 guest 组，所有人都能给自己发金币。代码层无 bug
- **F-Obs.3**：签到回复"获得金币"vs"本次总获得"文案歧义，建议改"基础奖励"
- **F-Obs.4**：转账成功只 @ caller，未 @ 收款方 —— UX 偏好，不是 bug

---

## 与 user-system 修复（commit 011aa68）的对照

| user-system 修复点 | economy 是否受益 / 影响 |
|---|---|
| `User.name` 唯一索引 | 转账按 name 查询走 `func.lower(User.name)`，享受到 |
| `tshock_api.py` `quote(path)` | economy 4 个命令都不调 server API，无影响 |
| `temp_screenshot_path` 路径迁移 | economy 不渲染截图，无残留 |
| 注册并发竞态修复模板（schema unique + IntegrityError 兜底）| F-2.1 / F-1.1 / F-3.1 / F-3.2 / F-4.1 是同类病；建议套同样模板（schema 改 + 条件 UPDATE） |

---

## 推荐处理顺序

1. 🔴 **F-2.1 转账并发**（金币凭空产生路径，可脚本批量复现）—— 条件 UPDATE 方案
2. 🔴 **F-1.1 + F-3.1 签到并发**（schema 升级 + handler 条件 UPDATE，两个一起修最经济）
3. 🟠 **F-1.2 跨午夜误拒**（line 165 用 last_sign_date == today_text 单一真源）
4. 🟠 **F-3.2 / F-4.1 add/remove lost-update**（与 F-2.1 同根，复用同一 SQL 模板）
5. 🟡 **F-Common.1 amount 上界 + F-2.2 解析一致性**（4 个 handler 统一）
6. 🟡 **F-Common.3 异常兜底回复**（项目级模式）
