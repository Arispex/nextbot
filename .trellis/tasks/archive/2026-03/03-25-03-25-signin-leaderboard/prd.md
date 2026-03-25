# 累计签到排行榜

## Goal

新增「签到排行榜」命令，按累计签到次数（`sign_total`）降序展示排行榜，支持翻页，适配双主题渲染。

## Requirements

- 命令：`签到排行榜 [页数]`，页数默认为 1
- 排序字段：`User.sign_total` 降序
- 数值单位：次
- 支持翻页（与金币/连续签到排行榜完全一致的分页逻辑）
- 触发者已注册时，底部显示自己的排名和累计签到次数
- 渲染主题通过 `resolve_render_theme()` 读取配置

## Implementation

与 `leaderboard.streak` 完全对称，仅字段和标题不同：

1. `nextbot/plugins/leaderboard.py`：
   - 新增 `signin_leaderboard_matcher = on_command("签到排行榜")`
   - 新增 `handle_signin_leaderboard` handler
   - `command_key="leaderboard.signin"`，`display_name="签到排行榜"`
   - 排序：`User.sign_total.desc()`
   - value 字段：`u.sign_total`，`value_label="次"`
   - self_entry rank：按 `sign_total > caller_sign_total` 计数
   - `file_prefix="leaderboard-signin"`

## Files to Modify

- `nextbot/plugins/leaderboard.py`

## Acceptance Criteria

- [ ] `签到排行榜` 按 `sign_total` 降序展示
- [ ] 翻页正常，超出页数给出提示
- [ ] 触发者已注册时底部显示自身排名
- [ ] 亮色/暗色主题均正常渲染
