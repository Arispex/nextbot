# Journal - arispex (Part 3)

> Continuation from `journal-2.md` (archived at ~2000 lines)
> Started: 2026-05-06

---



## Session 95: 新增 用户地图 命令

**Date**: 2026-05-06
**Task**: 新增 用户地图 命令
**Branch**: `main`

### Summary

新增 用户地图 <服务器 ID> <用户 QQ/@用户/用户名称> 命令，作为 我的地图 的指定用户版（镜像 用户背包/我的背包 模式）。复用 /nextbot/users/{user}/map-image API；OneBot V11 同消息 @ 发起人 + 图片发送；新增权限 player_query.map.user 并加入 guest 默认权限组。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `0684bbb` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 96: 背包页面新增地图探索率

**Date**: 2026-05-06
**Task**: 背包页面新增地图探索率
**Branch**: `main`

### Summary

用户背包/我的背包 渲染页面新增 地图探索率 stat tile，透传后端 API 新字段 mapExplorationPercent。stats-tiles 由 5 列改为 6 列，缺失时 tile 隐藏；缺失字段不会让原有校验失败，与 onlineSeconds 同模式。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `a23be37` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 97: 新增 地图探索率排行榜 命令

**Date**: 2026-05-06
**Task**: 新增 地图探索率排行榜 命令
**Branch**: `main`

### Summary

新增 地图探索率排行榜 <服务器 ID> [页数] 命令，调后端 /nextbot/leaderboards/map-exploration API。命令参数 / 错误回复 / self_entry / 日志格式与 在线时长排行榜 完全一致；数值格式 X.XX%。新增权限 leaderboard.map_exploration 默认加入 guest 组。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `235fa5a` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
