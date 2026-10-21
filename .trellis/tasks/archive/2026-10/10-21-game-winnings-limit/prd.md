# 掷骰子和猜数字增加可配置金币上限

## Goal

为掷骰子与猜数字分别添加 WebUI 可编辑的“最大上限”参数，限制用户通过对应功能获取的金币，默认 0 表示无上限。不增加用户指令参数。

## Confirmed requirements

- 参数位于两个命令各自的 command_control params 中，由现有 WebUI 参数编辑流程管理。
- 显示名称为“最大上限”，非负整数，默认 0 表示不限制。
- 不改变指令语法。

- 用户已确认：限制每局净赚，不含返还本金；投入 100、原返奖 1000、上限 500 时返奖 600，净赚 500。
- 参数键统一使用 max_profit；仅当 max_profit > 0 时，将 payout 限制为 min(payout, cost + max_profit)，随后应用现有账户余额上限。
- 亏损、返本不受影响，不新增累计收益或账户余额限制。

## Existing behavior

- 两个功能先扣投入金币，再计算含本金的 payout。
- 实际派发使用 economy.add_coins_with_cap，保留全局账户余额上限保护。
- 每个功能都有累计盈利 total_gain 与累计亏损 total_loss 字段。
- 倍率奖励、统计、结果图必须与新增限制的结算定义一致。

## Decision

单局净赚上限，与现有投入上限 max_cost 区分。两个命令独立配置；WebUI 复用现有动态参数表单和非负整数校验，不新增页面或数据库列。

## Acceptance criteria

- [x] WebUI 两个命令的参数编辑器都能显示、保存并读取该参数。
- [x] 0 保持现有结算不变；正数按确认的规则限制入账。
- [x] 结算、统计和用户看到的结果一致；保留现有全局余额上限保护。

## Out of scope

- 不修改 QQ 连接、其他小游戏、充值提现或额外风控。

## Technical notes

- nextbot/plugins/dice.py
- nextbot/plugins/guess_number.py
- nextbot/command_config.py
- 现有本地服务运行于 http://127.0.0.1:18081，未启用 QQ 适配器。

## Verification

- New SQLite-backed regression coverage: 4 tests pass, covering bounded/unbounded winnings, stake preservation, each-round reset, losses and global account cap precedence.
- Changed game modules and new test: Pyright reports 0 errors and 0 warnings.
- Changed Python files: Ruff E9/F63/F7/F82 checks pass.
- Browser: both parameter dialogs display 最大上限, default 0; saved 500 (dice) / 750 (guess), reloaded and verified; restored both to 0.
- WebUI API rejects max_profit=-1 with HTTP 422 and field param_values.max_profit.
- Full pytest invocation: 187 passed, 22 errors because four existing script-style modules have no pytest fixture definitions for their environment arguments. Their native script runners all pass (14/14, 10/10, 16/16, 8/8); no unrelated test code changed.
- Local panel restarted with updated code; OneBot remains disabled.
