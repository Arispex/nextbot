# feat: 掷骰子/猜数字冷却时间支持分钟与小时

## Goal

`掷骰子` 和 `猜数字` 命令的冷却剩余时间目前只显示 `"还需等待 X 秒"`，长冷却（>60s）下数字难读。复用 `time_utils` 已有的"秒/分/时"格式化逻辑，让冷却提示自动按量级切换为 `5 分 30 秒` / `1 小时 5 分 30 秒`。

## Requirements

- 在 `nextbot/time_utils.py` 新增通用 helper `format_duration_seconds(seconds: int) -> str`，逻辑等同现有 `format_online_seconds`（< 60s → `N 秒`；< 3600s → `M 分 S 秒` 或 `M 分钟`；>= 3600s → `H 小时 [M 分] [S 秒]`）。
- 将 `format_online_seconds` 改为对 `format_duration_seconds` 的薄包装（保持向后兼容，不改动其他调用方）。
- `nextbot/plugins/dice.py:188` 与 `nextbot/plugins/guess_number.py:187` 的失败回复中，`"还需等待 {remaining_s} 秒"` 改为 `"还需等待 {format_duration_seconds(remaining_s)}"`。

## Acceptance Criteria

- [ ] `format_duration_seconds(0)` → `"0 秒"`
- [ ] `format_duration_seconds(45)` → `"45 秒"`
- [ ] `format_duration_seconds(60)` → `"1 分钟"`
- [ ] `format_duration_seconds(90)` → `"1 分 30 秒"`
- [ ] `format_duration_seconds(3600)` → `"1 小时"`
- [ ] `format_duration_seconds(3665)` → `"1 小时 1 分 5 秒"`
- [ ] `format_online_seconds(N)` 对所有 N 与 `format_duration_seconds(N)` 行为完全一致（不破坏 player_query / leaderboard 等调用方）。
- [ ] 短冷却（30s）时掷骰子 / 猜数字回复仍为 `"冷却中，还需等待 30 秒"`，与改动前字节一致。
- [ ] 长冷却（300s）时回复为 `"冷却中，还需等待 5 分钟"`；3665s 时为 `"冷却中，还需等待 1 小时 1 分 5 秒"`。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 合规性）。
- 不破坏 `leaderboard` / `player_query` 等现有 `format_online_seconds` 调用方。
- 文案符合 CLAUDE.md 用户反馈规范（保持 `reply_failure("掷骰子", "冷却中，还需等待 …")` 的形态：动词通用、原因不含对象名）。

## Out of Scope

- 不改 `format_online_seconds` 命名（保留 alias 防止改动 leaderboard / player_query 上下文）。
- 不调整冷却时间参数本身、配置面板、其他命令的冷却展示。
- 不引入"天"单位（冷却数十小时的场景不存在）。

## Technical Notes

- 现有逻辑：`nextbot/time_utils.py:51 format_online_seconds`
- 调用方：
  - `nextbot/plugins/dice.py:188`
  - `nextbot/plugins/guess_number.py:187`
  - （已有不变）`nextbot/plugins/leaderboard.py:29,689,856,864`
  - （已有不变）`nextbot/plugins/player_query.py:34,145`
- 失败回复模板：`reply_failure("掷骰子" | "猜数字", f"冷却中，还需等待 {duration}")`
