# 新增地图探索率排行榜命令

## Goal

新增 `地图探索率排行榜 <服务器 ID> [页数]` 命令，与 `死亡排行榜` / `渔夫任务排行榜` / `在线时长排行榜` 同类（按服务器查询、分页、自渲染截图）。数据来自后端新 API `/nextbot/leaderboards/map-exploration`。

## API 契约（后端已实现）

`GET /nextbot/leaderboards/map-exploration`

```json
{
  "entries": [
    { "username": "Arispex", "mapExplorationPercent": 42.5 },
    { "username": "NextBot", "mapExplorationPercent": 12.34 }
  ]
}
```

- 已按 `mapExplorationPercent` 降序排列
- 覆盖所有注册玩家（无探索记录的为 0）
- `mapExplorationPercent` 范围 0–100，已是百分数

## Requirements

### 1. 命令注册（`nextbot/plugins/leaderboard.py`）

按现有 `handle_online_time_leaderboard`（line 599）的镜像新增：

- matcher：`map_exploration_leaderboard_matcher = on_command("地图探索率排行榜")`，加在 line 42 现有 `online_time_leaderboard_matcher` 后
- handler：`handle_map_exploration_leaderboard`
- `command_control`：
  - `command_key="leaderboard.map_exploration"`
  - `display_name="地图探索率排行榜"`
  - `permission="leaderboard.map_exploration"`
  - `description="查看指定服务器的地图探索率排行榜"`
  - `usage="地图探索率排行榜 <服务器 ID> [页数]"`
  - `params={"limit": {default 10, min 1, max 50, ...}}`（与其他排行榜一致）
  - `category="排行榜"`
- `@require_permission("leaderboard.map_exploration")`
- 业务流程：
  1. `parse_command_args_with_fallback`，校验 1–2 个 args
  2. `int(args[0])` → `server_id`
  3. `_parse_page_arg(args[1:], ...)` → `page`
  4. `get_current_param("limit", 10)` clamp 到 [1, 50]
  5. DB 查 `Server` + 当前 `caller.name`
  6. `request_server_api(server, "/nextbot/leaderboards/map-exploration")`
  7. `is_success` / `payload.get("entries")` 校验，列表过滤合法条目（`username` 是 str + `mapExplorationPercent` 是 int/float 且非 bool）
  8. 总条数 → 总页数；超页 → 失败回复
  9. 切片当前页，构造 `entries`，`value=f"{e['mapExplorationPercent']:.2f}%"`
  10. 找 caller 的全表名次（线性扫，与 `handle_online_time_leaderboard` 一致）
  11. `_render_and_send`：
      - `title="地图探索率排行榜"`
      - `value_label="探索率"`
      - `file_prefix="leaderboard-map-exploration"`

### 2. 默认权限（`nextbot/db.py`）

在 `DEFAULT_GUEST_PERMISSIONS` frozenset 中插入 `"leaderboard.map_exploration"`，按字母序放在 `leaderboard.guess_number_win_rate` 后、`leaderboard.online_time` 前。

## Non-goals

- 不修改 `_render_and_send` 公共函数
- 不修改 `create_leaderboard_page` / 排行榜模板
- 不动其他排行榜命令
- 不需要传 limit 参数透传给 API（API 不接受 query 参数，本地分页）

## Acceptance Criteria

- [ ] OneBot V11 群聊执行 `地图探索率排行榜 1` 返回第 1 页（默认 10 名）
- [ ] `地图探索率排行榜 1 2` 返回第 2 页
- [ ] 服务器不存在 → "❌ 查询失败，原因：服务器不存在"
- [ ] 页数非正整数 → "❌ 查询失败，原因：页数必须为正整数"
- [ ] 页数超出 → "❌ 查询失败，原因：超出总页数（共 N 页）"
- [ ] API 报错 → 透传 `error.message`
- [ ] 自己上榜时，截图底部显示自己名次（`self_entry`）
- [ ] guest 组默认拥有 `leaderboard.map_exploration` 权限
- [ ] `菜单` 命令能列出 `地图探索率排行榜`
- [ ] 数值显示 2 位小数 + `%`，例如 `42.50%` / `2.41%` / `0.00%`

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再提交
