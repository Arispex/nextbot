# 用户抢劫状态切换（带金币消耗）

## Goal

为用户引入"抢劫保护"状态：开启后既不能抢别人，也不会被别人抢。提供一个明确指定开关的命令切换该状态，每次切换花费一定金币（默认 200，可在命令参数里调）。这样用户可以"花钱买和平"，但代价是失去主动出击能力。

## Requirements

### 数据
- `User.rob_protected: bool`，默认 `False`（即新用户默认可抢可被抢）
- 与 `is_banned` 等列同样走 ALTER TABLE 增量迁移（参考 `nextbot/db.py:609 ensure_user_rob_schema`）

### 命令
- 命令名：`切换抢劫保护 <开启/关闭>`（必填参数）
- `command_key="economy.rob_protection"`、`category="小游戏系统"`、`permission="economy.rob_protection"`
- 命令参数（runtime 可调）：
  - `toggle_cost`（int，默认 200，min 0，label "切换花费金币"）
- 行为：
  1. 解析 `开启 / 关闭`（同义词支持：`开 / 关 / on / off`，大小写不敏感）；不合法 → `raise_command_usage()`
  2. 用户未注册 → `reply_failure("切换抢劫保护", "请先注册账号")`
  3. 已处于目标状态 → `reply_failure("切换抢劫保护", "已处于该状态")`（不扣金币）
  4. 金币不足 `toggle_cost` → `reply_failure("切换抢劫保护", f"金币不足，需 {cost}，当前 {coins}")`
  5. 通过：扣金币 + 翻转 `rob_protected`，单事务 commit
- 成功回复（与签到/转账风格一致）：
  ```
  @user
  ✅ 切换抢劫保护成功
  🛡 抢劫保护：开启 / 关闭
  💰 消耗金币：200
  💰 当前金币：xxxx
  ```
- 日志：`logger.info(f"切换抢劫保护：user={name}({uid}) state={on/off} cost={cost} coins={coins}")`

### 抢劫拦截（修改 `nextbot/plugins/rob.py`）
- 在金币检查通过后、随机轮盘（`roll = random.randint(1, 100)`）之前，新增两道前置：
  1. `robber.rob_protected` → `reply_failure("抢劫", "你处于保护状态，先关闭抢劫保护才能抢劫")`
  2. `victim.rob_protected` → `reply_failure("抢劫", "对方处于保护状态，无法抢劫")`
- 这两道检查不消耗冷却、不更新 `last_rob_time`、不计入 `rob_total_count`

## Acceptance Criteria

- [ ] DB 增列 `rob_protected`，对老库走 ALTER TABLE，重启后正常
- [ ] `切换抢劫保护 开启` 在金币 ≥ 200 时扣 200 金币、状态 → True、回复风格匹配 `reply_success`/`reply_block`
- [ ] `切换抢劫保护 关闭` 行为对称
- [ ] 金币不足时 `reply_failure`，状态不变、金币不变
- [ ] 已处于目标状态时 `reply_failure`，不扣金币
- [ ] 抢劫保护开启的用户调 `抢劫 <他人>` → `reply_failure`，冷却 / `last_rob_time` 不变
- [ ] 抢劫保护开启的用户被 `抢劫` → `reply_failure`，攻击方冷却 / 计数不变
- [ ] `切换抢劫保护 lol` 等不合法参数 → 触发 `raise_command_usage()` 走 `_build_usage_message`
- [ ] WebUI 命令配置页面看得到新命令（依赖 `sync_registered_commands_to_db`，纯增量、无需手动）

## Definition of Done

- 上述 AC 全部通过
- 不影响现有抢劫成功 / 反抢 / 警察 / 失败四种结果路径
- 命令参数可在 WebUI 命令配置页编辑生效（验证 `toggle_cost` 改后立刻应用）

## Technical Approach

1. **`nextbot/db.py`**：在 `User` 类增 `rob_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)`；扩展 `ensure_user_rob_schema()`（或新增 `ensure_user_rob_protection_schema()`）做 ALTER TABLE
2. **新建 `nextbot/plugins/rob_protection.py`**：单 `command_control` 装饰函数 `handle_toggle_rob_protection`，复用 `text_utils.reply_success/reply_failure/reply_block`；位于 `nextbot/plugins/__init__.py` 已通过文件名自动加载（如有手动注册再补）
3. **`nextbot/plugins/rob.py`**：在第 217 行（"金币检查"区块结束后）插入两个 `if robber.rob_protected` / `if victim.rob_protected` 早返回
4. **`bot.py`**：检查 `init_db()` / `ensure_user_rob_schema()` 调用是否包含新列；若拆出新函数需在启动期 hook 调用

## Decision (ADR-lite)

**Context**：选 toggle vs 显式开关参数；选 cooldown vs 纯金币门槛；选独立 plugin 文件 vs 加进 rob.py

**Decision**：
- 显式开关参数（`<开启/关闭>`）—— 避免误操作，状态可观测
- 纯金币门槛，不加额外冷却 —— 金币本身就是节流
- 独立 plugin 文件 `rob_protection.py` —— rob.py 已 290 行，功能解耦更易维护

**Consequences**：
- 用户每次想切换必须打全 `切换抢劫保护 开启`（略长但清晰）
- 反复横跳成本仅取决于金币；如果将来发现刷抢可调高 `toggle_cost` 默认值或加冷却参数（向后兼容）

## Out of Scope

- toggle 自身的冷却时间（未来可补 `toggle_cooldown_minutes` 参数）
- protection 切换次数 / 金币消耗的统计字段
- 用户信息卡 / 排行榜显示保护状态
- WebUI 直接编辑 `rob_protected` 字段
- 抢劫保护期间被红包 / 抽奖 / 转账等其他经济交互的影响（这些功能本就不属于"抢"）

## Technical Notes

- 现有抢劫流程：`nextbot/plugins/rob.py:137-290`，金币检查在第 202-216 行，轮盘在 219 行
- `User` 模型：`nextbot/db.py:98-119`
- 增量迁移参考：`nextbot/db.py:609 ensure_user_rob_schema()`
- Reply 工具：`nextbot/text_utils.py:32-77`（`reply_success` / `reply_failure` / `reply_block`）
- Emoji 选用：保护状态 → `🛡`（与 `EMOJI_SECURE` 一致或新增）；金币 `💰`
- `command_control` 参数 schema：参考 `rob.py:27-133` 的字典结构
- `at` 头部 + reply 拼接：单行用 `at + " " + ...`，多行块用 `at + "\n" + reply_block(...)`（参考 `economy.py:219-226`）
