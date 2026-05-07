# 仓库系统命令审计报告（已二次复查）

**审计对象**：`nextbot/plugins/warehouse.py` 8 个 `category="仓库系统"` 命令（共 1700 行）
**审计日期**：2026-05-07
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证
**参照修复**：commit `0206834`（economy）+ minigame / red_packet / `ec42714`（execute_rowcount helper）

## 严重级别分布（复查后）

- 🔴 必修：**3** —— 回收金币 lost-update / 领取 DB-TShock 双重一致性 / 多格领取同 commit 失败窗口
- 🟠 应修：**5** —— 8 handler 缺异常兜底 / quantity 上界 / value × ratio 上界 / claim_many 失败明细 / FK 缺失
- 🟡 建议：**6** —— ratio 上界 / player_name 大小写 / 多 session / find_empty_slot 性能 / gift target 满后 break / 进度无 cache
- 🟢 观察：**3**

仓库系统的特殊性：**同时操作金币 + 物品 + TShock API**，原子性挑战是 3 重的（物品-物品 / 物品-金币 / 物品-API），是迄今为止最复杂的审计目标。

---

## 🔴 必修

### W-6.1 — 回收物品：user.coins lost-update（金币少加）

- **位置**：
  - `_recycle_single`：`warehouse.py:921-972`，关键 line 927 / 963 / 965
  - `_recycle_many`：`warehouse.py:999-1043`，关键 line 1005 / 1034 / 1036
- **现象**：`warehouse_lock` 只保护**同一 user 的 WarehouseItem 操作**，不保护 `User.coins` 字段。回收路径：
  ```python
  user = session.query(User).filter(User.user_id == user_id).first()  # 读 stale
  ...
  user.coins = int(user.coins or 0) + refund  # 基于 stale 写绝对值
  session.commit()
  ```
  与 economy F-2.1 / red_packet R-2.1 / R-3.1 完全同模式。同 user 同时执行"回收"和"任意其他改 coins 的路径"（被人转账、签到、抢红包、admin 调金币、抢劫等）会发生 lost-update。
- **复现**（A 余额 100，仓库格 5 unit_value=50 qty=1 ratio=0.5 → refund=25）：
  1. A 几乎同时执行 `回收仓库物品 5` 与（被人）`转账 A 50`
  2. recycle session 读 A.coins=100；transfer session 读 A.coins=100，写 150 commit
  3. recycle session 写 `A.coins = 100 + 25 = 125` commit（**覆盖了 150**）
  4. 最终 A.coins=125，应为 175 → **A 损失 50**（凭空蒸发）
- **影响**：金币系统总量被打破。`warehouse.recycle_self` 在 guest 默认权限里
- **修复方案**（与 economy F-2.1 同模板，复用 `execute_rowcount`）：
  ```python
  session.execute(
      update(User).where(User.user_id == user_id)
      .values(coins=User.coins + refund)
  )
  session.commit()
  coins_after = int(session.query(User.coins).filter(User.user_id == user_id).scalar() or 0)
  ```

### W-7.1 — 领取单格：DB-TShock 双重一致性窗口（物品凭空产生）

- **位置**：`warehouse.py:1226-1293`
- **现象**：
  - line 1269 `_issue_give_command` 先调用 TShock `/give` 给玩家发物品
  - line 1280-1285 然后 `session.delete(item)` 减仓库行
  - line 1286 `session.commit()`
  - 之间任何异常（commit 抛 OperationalError / 磁盘满 / SQLite WAL 锁繁忙超时 / 进程被 kill）→ give 已成功，DB 行保留 → 玩家可再领一次
- **与红包/经济不同**：这是**双重一致性**（DB ↔ TShock）问题，不是 lost-update
- **复现**（需 commit 失败，可在测试环境模拟）：
  1. 玩家 A 在线，仓库格 5 = 某物品 ×1
  2. `领取仓库物品 1 5`，TShock 200 OK，玩家 inventory +1
  3. commit 前 kill bot 或 SQLite WAL 锁超时 → DB 行保留
  4. 重启后 A 再发同命令，仍能领 → 物品凭空 +1
- **影响**：物品凭空产生
- **修复方案**（无完美方案，需权衡）：
  - **保守**：commit 失败时记录 `logger.error` + 用户提示"已发放但 DB 未确认"，让事故可追溯（最低成本）
  - **更稳**：先 `delete + commit`（DB-first），再 give；commit 成功后 give 失败则补偿日志 + 提示用户

### W-7.2 — 领取多格：per-slot commit 注释承认 crash 风险但仍有 commit 自身失败窗口

- **位置**：`warehouse.py:1345-1366`
- **现象**：注释（line 1342-1344）声明"per-slot commit 防止崩溃 mid-loop"。但**同 W-7.1**：commit 自身失败（不是进程 crash 而是 SQLite OperationalError）一样会让 give 已发、行未删。注释只考虑了"crash"的一半。
- **复现**：与 W-7.1 同模板，多格场景命中概率更高
- **修复方案**：与 W-7.1 一并修，循环里 commit 失败时单独记录 + 提示用户

---

## 🟠 应修

### W-Common.1 — 8 handler 缺异常兜底

- **位置**：`handle_list_self` (272-293) / `handle_list_user` (306-339) / `handle_add` (352-500) / `handle_remove` (513-561) / `handle_drop` (698-735) / `handle_recycle` (880-918) / `handle_claim` (1154-1223) / `handle_gift` (1422-1488)
- **现象**：内层 `_remove_single` / `_drop_many` / `_claim_single` / `_gift_single` 等都只 `try: ... finally: session.close()`，无 `except`。`commit` 抛 IntegrityError/OperationalError、`bot.send` 网络错时直接逃出 NoneBot 顶层吞掉
- **同病**：与 economy F-Common.3 / minigame M-Common.2 / red_packet R-Common.1 一致
- **影响**：回收/赠送/领取等高价值操作"无声失败"，用户重试 → 进一步触发 W-6.1 / W-7.1
- **修复方案**：每个 handler / 内部协程加 `except Exception: session.rollback() + logger.exception + reply_failure(...)`

### W-3.1 — 添加仓库物品 quantity 缺上界

- **位置**：`warehouse.py:382-389`，仅 `quantity < 1` 校验
- **现象**：terraria item stack 上限通常 9999，但 SQLite INTEGER 接受 64 位；admin 误输 9 位数会过
- **修复方案**：加 `MAX_ITEM_QUANTITY` 上界（建议 9999 或 1_000_000）

### W-3.2 — 添加 value × recycle ratio 缺总额上界 → 回收 refund 可超 MAX_COINS_AMOUNT

- **位置**：
  - `add` 的 `value`：`warehouse.py:412-418`，仅 `value < 0` 校验
  - `_recycle_single` line 953 `refund = int(unit_value * recycle_qty * ratio)`
  - `_recycle_many` line 1028 同公式
- **现象**：admin 一次添加 `value=2_000_000_000` 物品 → 玩家回收即获海量金币，绕过 economy 的 `MAX_COINS_AMOUNT = 100_000_000`
- **影响**：admin 误操作 / 测试数据混入生产，`recycle_self` 是 guest 权限
- **修复方案**：
  1. `add` 添加 `MAX_ITEM_VALUE` 上界
  2. recycle 计算 refund 后 `min(refund, MAX_COINS_AMOUNT)` 兜底
  3. `recycle_ratio` 加上界（W-Common.2）

### W-7.3 — 多格领取 give 失败的格子 silently skipped

- **位置**：`warehouse.py:1355-1395`
- **现象**：循环里 `_issue_give_command` 失败仅 `skipped_give_failed += 1; continue`，最终汇总只显示"X 个发送失败"，**不展示哪几个格子 / 什么原因**
- **修复方案**：保留每格 reason，在 reply_block 追加明细

### W-3.3 — WarehouseItem.user_id 无 FK 约束

- **位置**：`db.py:249`
- **现象**：`WarehouseItem.user_id` 是 String 字段，无 FK 到 `user.user_id`。`add` 中 `_load_user` 用独立 session 查完即关，中间无锁
- **影响**：低概率（无注销路径），schema 隐患
- **修复方案**：长期加 FK，短期可忽略

---

## 🟡 建议

### W-Common.2 — `recycle_ratio` 参数无上限
- `warehouse.py:912` `ratio = max(0.0, float(...))`，admin 误配 ratio=10 → 玩家放大资产 10 倍
- 修复：cap `min(1.0, ratio)`

### W-7.4 — `_check_player_online` 大小写匹配脆弱
- `warehouse.py:1097-1104`，`nickname.lower() == name_lower`，未 unicode normalize
- 玩家改 character name / unicode 异形 → 误报"未在线"

### W-Common.3 — 多 session 跨 await
- `_load_user` / `_load_user_by_name` / `_load_server` 各自开 session 立即 close，handler 内多次重开
- engine 已是单例，性能影响轻微，主要是可维护性

### W-3.4 — `_find_first_empty_slot` 全表 + python set 扫，赠送多格时 N²
- `warehouse.py:178-186`，每次循环全扫 occupied
- 性能优化：循环外初始化，循环内增量

### W-8.1 — `_gift_many` target 满后未 break
- `warehouse.py:1622-1630`，第一次返回 None 后剩余循环每次仍调一遍
- 性能微小 + 逻辑冗余

### W-7.5 — 进度 / 在线状态每次都打远程，无 cache
- `warehouse.py:1082-1120`，1 次领取 = 3 次 HTTP；批量 100 = 102 次
- 修复：进度可 30 秒 cache（变化罕见）

---

## 🟢 观察

- **W-Obs.1**：`warehouse.add` / `warehouse.remove` 默认 admin only（不在 `DEFAULT_GUEST_PERMISSIONS`），合理设计
- **W-Obs.2**：`_issue_give_command` 用 `f"/give {item_id} {player_name} ..."` 字符串拼接；player_name 自由字符串，含空格会解析失败但不越权
- **W-Obs.3**：`_parse_slot_expression` 输入校验扎实（1..WAREHOUSE_CAPACITY），无负数/0/越界风险

---

## 与最近修复的对照

| 修复点 | 仓库是否同病 |
|---|---|
| economy F-2.1 转账并发条件 UPDATE | **W-6.1 同病** |
| F-3.2/4.1 add/remove lost-update | **同上** |
| F-Common.1 MAX_COINS_AMOUNT | **W-3.2 应修** |
| F-Common.3 异常兜底 | **W-Common.1 全部缺** |
| `_safe_param_int`（minigame）| 仓库唯一 param 是 `recycle_ratio`（float），可加 `_safe_param_float` |
| `temp_screenshot_path` | 已迁移 ✓ |
| `execute_rowcount`（commit ec42714）| W-6.1 修复时复用 |

**新维度**：W-7.x 是 DB ↔ TShock 双重一致性，不在前几次修复模式里。

---

## 推荐处理顺序

1. 🔴 **W-6.1 回收金币 lost-update**（清晰可复现，与已修同模板，复用 execute_rowcount）
2. 🔴 **W-7.1 + W-7.2 领取双重一致性**（先加 commit 失败告警日志快速止血，再设计补偿/逆操作）
3. 🟠 **W-Common.1 异常兜底**（模板化，8 处统一加）
4. 🟠 **W-3.1 + W-3.2 上界**（quantity / value 一并加，与 W-6.1 同 commit）
5. 🟠 **W-7.3 失败明细**
6. 🟡 W-Common.2 / W-Common.3 / W-7.4 / W-3.4 / W-8.1 / W-7.5（下一轮）

## 主代理复查最容易误判的点

- **W-7.1 / W-7.2**：DB-API 双重一致性是经典分布式问题，任何"修复"都不能完全消除窗口。建议主代理读 `_claim_single` 源码亲自走 give→commit 边界
- **W-3.3 (FK 缺失)**：长期建议而非短期 bug，建议建档但不在此 audit 修复
