# 连续签到排行榜

## Goal

在 `leaderboard.py` 新增「连续签到排行榜」命令，复用现有排行榜 UI。

## 命令

```
连续签到排行榜
```

## 数据来源

查询 `User` 表，按 `sign_streak` 降序排列，取前 N 名（limit 参数，默认 10）。

## 实现要点

与金币排行榜完全对称，仅改：
- 排序字段：`User.sign_streak`
- `value`：`int(u.sign_streak or 0)`
- `title`：`"连续签到排行榜"`
- `value_label`：`"天"`
- 截图文件名前缀：`leaderboard-streak-`
- log 描述文字对应更新

顺手修复现有 log typo：`金币排行榜榜` → `金币排行榜`

## Files to Modify

- `nextbot/plugins/leaderboard.py`
