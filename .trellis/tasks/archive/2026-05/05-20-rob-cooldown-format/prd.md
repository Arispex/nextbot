# fix: 抢劫冷却时间显示支持分钟与小时

## Goal

`抢劫` 命令的冷却剩余时间目前硬编码为 `"{M} 分 {S} 秒"`，冷却参数 `cooldown_minutes` 默认 60 分钟，调高到 120+ 分钟时会显示成 `"120 分 0 秒"` 这种难读格式。复用 05-20-cooldown-format-units 引入的 `format_duration_seconds` helper，自动按量级切换为秒/分/小时。

## Requirements

- `nextbot/plugins/rob.py:217-227` 的冷却失败回复：把手动计算的 `remaining_minutes / remaining_seconds` 替换为 `format_duration_seconds(int(remaining.total_seconds()))`。
- import `format_duration_seconds` from `nextbot.time_utils`。
- 不动其他业务逻辑（`UPDATE WHERE` 兜底、保护态、冷却参数本身、其余 `reply_failure("抢劫", "冷却中或保护状态变更，已取消")` 等不变）。

## Acceptance Criteria

- [ ] 60s 剩余 → `"冷却中，还需等待 1 分钟"`（短冷却仍可读）
- [ ] 90s 剩余 → `"冷却中，还需等待 1 分 30 秒"`
- [ ] 3665s 剩余 → `"冷却中，还需等待 1 小时 1 分 5 秒"`
- [ ] 主流程未被破坏：其他失败 / 成功路径文案不变。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 合规性）。
- 文案保持 CLAUDE.md 用户反馈规范（动词通用、原因不含对象名）。

## Out of Scope

- 不重命名 / 不重构 rob.py 其它部分。
- 不调冷却参数默认值或语义。

## Technical Notes

- 新 helper：`nextbot/time_utils.py format_duration_seconds`（上一个任务引入）
- 改动点：`nextbot/plugins/rob.py:217-227`
- 参考实现：`nextbot/plugins/dice.py:188`、`nextbot/plugins/guess_number.py:187`
