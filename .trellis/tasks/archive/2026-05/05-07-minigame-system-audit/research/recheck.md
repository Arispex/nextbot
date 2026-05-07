# 小游戏系统二审报告

**对照基线**：commit `9c23490`（economy 修复后）→ 当前工作树（minigame 修复未提交）
**审计文件**：`guess_number.py` / `dice.py` / `rob.py` / `rob_protection.py`

---

## Phase 1: 修复项落实

| ID | 修复项 | 落实情况 |
|---|---|---|
| **M-1.1** | 猜数字条件 UPDATE | ✅ `guess_number.py:190-194` 用 `update().where(coins >= cost).values(coins = coins - cost)`，rowcount=0 → 重读最新金币给文案；payout / 统计累加全部用条件 UPDATE（line 234-273）|
| **M-2.1** | 掷骰子条件 UPDATE | ✅ `dice.py:159-163` 同模式，line 198-237 三分支统计累加全部条件 UPDATE |
| **M-3.1** | 抢劫 4 分支条件 UPDATE | ⚠️ **success / police / fail 分支已落实但 counter 分支引入新缺陷（见 Phase 3 NEW-1）**。<br>- success（line 276-322）：先扣 victim → 再加 attacker，attacker 失败有 victim 回滚 + commit；attacker WHERE 含冷却 + 保护<br>- police（line 377-406）/ fail（line 412-441）：attacker 条件 UPDATE + 兜底钳制为 0<br>- attacker 冷却合并到 `attacker_where_clauses()`（line 248-259） |
| **M-4.1** | rob_protection 互斥旧状态 + 余额校验 | ✅ `rob_protection.py:87-98` 单 UPDATE 含 `coins>=cost AND rob_protected.is_(not target)`；rowcount=0 后重新拉状态分别给"已处于该状态" / "金币不足" 文案（line 100-114）|
| **M-Common.1** | MAX_COINS_AMOUNT 上界 | ✅ `guess_number.py:15,160-165`、`dice.py:15,129-134`、`rob_protection.py:12,70-75` 全部 `from nextbot.plugins.economy import MAX_COINS_AMOUNT` 并加上界。`rob.py` 因抢劫金额由 victim_coins 派生，无需上界（与 findings 一致） |
| **M-Common.2** | 异常兜底 | ✅ 4 个 handler 全部加 `except Exception: logger.exception(...) + reply_failure`，外层 `try` / `finally session.close()` 仍保留 |
| **M-Common.4** | _safe_param_int helper | ✅ 4 个文件全部新增 helper，`grep "int(get_current_param"` 仅在 helper 内部出现 1 次/文件，handler 中无遗漏 |
| **M-3.3** | 自抢短路 | ✅ `rob.py:152-155` 在 `resolve_user_id_arg_with_fallback`（按 name lookup SQL）之前先做 `early_args[0].isdigit() and early_args[0] == robber_id` 短路；`@self` 在 message parser 转为数字 ID（参见 `message_parser.py:126`），同样被短路命中 |

---

## Phase 2: 行为不变性

### 命令 1 猜数字
- ✅ 入口校验（`数字范围 1-N` / `投入金币必须为正整数` / `最低投入 N 金币` / `最高投入 N 金币` / `请先注册账号` / `冷却中，还需等待 N 秒`）全部保留，文案 1:1 一致
- ✅ 命中文案 / 极近 / 接近 / 偏离 / 远离 → result_type 串保留（line 215-227），输出格式一致
- ✅ "金币不足（当前 N）" 中 N 取 rowcount=0 后重读 `User.coins.scalar()`，比 stale 旧值更准确，但不破坏文案格式
- ✅ 新增上界文案 `数量过大（最多 100000000）`（M-Common.1 必要新增，符合验收标准 1 关于 defense-in-depth）
- ✅ 新增异常兜底 `处理失败，请稍后重试`（M-Common.2 必要新增）
- ✅ 最终回复 `final_coins` 用 `User.coins.scalar()` 重读，比 stale ORM 实例准确

### 命令 2 掷骰子
- ✅ "请选择 大、小 或 豹子" / "投入金币必须为正整数" / "最低投入 N" / "最高投入 N" / "请先注册账号" / "冷却中，还需等待 N 秒" 全部保留
- ✅ 大 / 小 / 豹子 / 豹子通杀 / 猜对了 / 猜错了 / 刚好持平 文案保留（line 256-277）
- ✅ d1+d2+d3 输出格式一致

### 命令 3 抢劫
- ✅ "未找到该用户" / "用户名存在重复" / "不能抢劫自己" / "对方未注册账号" / "请先注册账号" / "冷却中" / "对方身无分文" / "你身无分文" / "金币不足 N" / "保护状态" 全部保留
- ✅ 5 种结果文案（crit / success / counter / police / fail）逐字保留（line 462-468）
- ✅ "@" 行为保留：成功 / 失败 / 冷却 / 保护 等所有 `bot.send` 都带 `at + " "` 前缀
- ✅ logger.info 抢劫结果格式保留
- ⚠️ **新增文案** `冷却中或保护状态变更，已取消`：原代码无此分支。是 M-3.1 fix 的必要副产物（attacker 状态在条件 UPDATE 之间被并发改变时）。属验收标准 1 中"行为不变性"的边界例外，但符合"原子化"的核心修复目标
- ✅ M-3.3 自抢短路文案与原 line 175 完全一致

### 命令 4 切换抢劫保护
- ✅ "已处于该状态" / "金币不足，需 N，当前 M" / "请先注册账号" 全部保留
- ✅ 成功回复 `🛡 抢劫保护：开启/关闭` / `💰 消耗金币：N` / `💰 当前金币：M` 格式保留
- ✅ 新增上界文案与命令 1 / 2 一致
- ✅ rowcount=0 后用重新查询逐项判定（注册 / 状态相同 / 金币不足），决定文案，与原 fast-path 校验顺序基本一致

**整体结论**：除了"必要新增"的 3 类新文案（上界、异常兜底、抢劫并发取消），所有原有文案与回复格式均完整保留。

---

## Phase 3: 新引入问题

### 🔴 NEW-1：抢劫 counter 分支 fallback 路径会凭空增发金币

- **位置**：`rob.py:342-371`
- **现象**：counter 分支主 UPDATE（要求 `attacker.coins >= amount`）失败时，进入 fallback：
  ```python
  update(User).where(*attacker_where_clauses(), User.coins > 0)
              .values(coins=0, ..., rob_total_penalty=...+ User.coins, ...)
  ```
  然后 line 364-371 给 victim 加 `User.coins + amount`。
- **复现**：
  1. attacker A.coins = 100，counter_steal_percent = 10 → amount = 10
  2. 与此同时，A 在另一会话已花掉 95 → A 现金币 5
  3. 主 UPDATE 因 `coins >= 10` 失败 → 走 fallback
  4. fallback 把 A.coins 钳制为 0（实际只扣 5），但 victim 仍被加 10
  5. **凭空 +5 金币**，数量随并发频次累积
- **影响**：与原 M-3.1 同等 → 金币系统总量被破坏（原审计核心目标未完全达成）。fallback 路径本来是"对原代码允许 attacker 负数余额的友善钳制"，但与 victim crediting 的耦合导致引入新 bug
- **修复方案 A（保守 / 推荐）**：去掉 fallback，主 UPDATE 失败直接退 "你的金币不足"，避免任何不对称。代码更短、语义更清晰
- **修复方案 B**：把 fallback 用 `RETURNING` 拿到实际扣到的 amount，再用该值给 victim 加。SQLAlchemy 2.x 支持 `update(User).returning(User.coins)`，但成本较高
- **严重级别**：🔴 必修。这是 fix 自己引入的回归，违反验收标准 3（小游戏系统再无漏洞缺陷）

### 🟡 NEW-2：rob.py counter / police / fail 分支多套 attacker_where_clauses 调用 closure 导致复杂度极高

- **位置**：`rob.py:255-259`，分支重复调用 `attacker_where_clauses()`
- **现象**：每个分支都 2 次调用 `attacker_where_clauses()`（主 UPDATE + fallback）。因 closure 内部每次都重建 list，无性能问题，但 ruff `C901` 报 `handle_rob` 复杂度 35（项目阈值 10）。其他 3 个 handler 也超阈值（11-30）但从 fix 前已是这个量级，不是 fix 引入
- **影响**：可维护性下降，但不影响行为
- **修复方案**：分支拆 helper 函数（非必修，与 fix 主线无关）
- **严重级别**：🟡 建议

### ✅ 已排查无问题

| 检查项 | 结论 |
|---|---|
| 条件 UPDATE 后读余额 | ✅ guess_number / dice / rob_protection 全部用 `session.query(User.coins).scalar()` 重读，未沿用 stale ORM |
| 抢劫 success 分支 victim 回滚 | ✅ a_rows=0 时 line 312-319 用 `coins + amount` / `rob_total_loss - amount` 补偿；line 320 显式 `session.commit()`；逻辑对称且正确 |
| 抢劫 police / fail 分支 fallback | ✅ 这两个分支不给 victim 加钱，fallback 钳制只影响 attacker 自身统计字段，不破坏总量守恒 |
| rob_protection rowcount=0 fallback 顺序 | ✅ 顺序：注册 → 状态相同 → 金币不足。与原 fast-path（line 81-86 注册检查 → line 105 状态相同 → line 109 金币不足）顺序一致 |
| MAX_COINS_AMOUNT 循环导入 | ✅ 单向依赖（economy 不反向 import minigame plugin），无循环导入风险。已 `grep` 验证 economy.py 第 1-32 行无 `nextbot.plugins.guess_number` / `dice` / `rob` 导入 |
| _safe_param_int 替换完整 | ✅ `grep -n "int(get_current_param"` 4 个文件各仅 1 处（helper 自身），handler 内全部已替换 |
| except Exception 兜底范围 | ✅ 已 `# noqa: BLE001` 标注；commit 失败、bot.send 失败均会落入兜底；不会吞 KeyboardInterrupt（KI 继承自 BaseException 而非 Exception，符合 Python 规范） |
| 抢劫 self 短路 @self 场景 | ✅ message parser 把 `@123` 转成数字字符串 `123`（参见 `message_parser.py:126 token.isdigit()` 分支），等于 robber_id 时短路命中 |

---

## Phase 4: 整体回归

### 已通过

- ✅ 无 SQL 注入（全部参数化绑定）
- ✅ 无命令注入 / 越权（permission decorator 完整）
- ✅ 无 N+1（条件 UPDATE 一次完成扣加 + 统计累加）
- ✅ 无串行 await（DB 操作在 try 块内同步执行）
- ✅ 资源管理：`finally session.close()` 仍在所有路径覆盖，包括新加的异常分支
- ✅ 整数 / NULL：`User.coins or 0` 统一处理 NULL；上界 `MAX_COINS_AMOUNT` 已加
- ✅ 边界：min_steal_percent / cooldown_minutes / cost 等全部经 `_safe_param_int` 防御
- ✅ engine 单例：`get_session()` 已是项目级单例（参见 `db.py:356-366`），4 个文件自动受益

### 新发现

- 🔴 **NEW-1**（见 Phase 3）：counter 分支 fallback 凭空增发金币
- 🟡 **NEW-2**（见 Phase 3）：handle_rob 复杂度过高，可维护性下降

### 静态检查

- pyright：11 个 `Result[Any].rowcount` 误报，与 economy.py 修复后状态一致（SQLAlchemy stub 限制，非真实 bug）
- ruff：87 个错误（fix 前 65，fix 后 87；新增 22 个主要为 E501 行长 + I001 import 排序，与项目其他 plugin 一致），均为风格问题，无新功能性问题

---

## 结论

| 验收标准 | 评估 |
|---|---|
| 1. **无破坏性**（外部行为完全一致） | **基本通过**：所有原文案 1:1 保留；3 类新增文案（上界 / 异常兜底 / 抢劫并发取消）属修复必要副产物，不算破坏 |
| 2. **开箱即用**（无 schema 改动） | **通过**：纯逻辑修复，未触 DB schema |
| 3. **小游戏系统再无漏洞缺陷与可优化空间** | **未通过**：counter 分支 fallback 引入新 lost-update（NEW-1），核心审计目标未完全达成 |

**总体：需要补修 1 处**

补修建议：让主代理派 implement，按方案 A（去掉 counter 分支 fallback，主 UPDATE 失败直接退 "你的金币不足，需 N"）。约 10 行代码改动，预期 5-10 分钟完成。修完即满足全部 3 条验收标准。

NEW-2（complexity）属遗留风格问题，不是验收标准红线，可下一轮再处理。
