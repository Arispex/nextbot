# 玩家统计加地图探索率字段

## Goal

在 `用户背包` / `我的背包` 渲染的网页统计栏中新增"地图探索率"字段，展示后端 API 新返回的 `mapExplorationPercent`（已是百分数，0 ~ 100）。

## API 新增字段

```json
{
  "mapExplorationPercent": 2.41
}
```

字段含义：玩家在该服务器世界里已探索（点亮）的地图百分比。后端已直接返回百分数（不需再 ×100）。

## Requirements

### 1. 后端解析
`nextbot/plugins/player_query.py` 的 `_parse_user_info_texts(response_payload)`：
- 新增解析 `response_payload.get("mapExplorationPercent")`，类型转 float，缺失/非法 → `None`
- 输出加 `"map_exploration_text": f"{value:.2f}%"`（2 位小数 + 百分号），缺失 → `""`（与 `online_time_text` 缺失策略一致）

### 2. 调用链透传
- `nextbot/plugins/player_query.py`：`handle_user_inventory` / `handle_my_inventory` 两处 `create_inventory_page(...)` 调用增加 `map_exploration_text=info_texts.get("map_exploration_text", "")`
- `server/web_server.py`：`create_inventory_page` 形参增加 `map_exploration_text: str = ""`，转给底层
- `server/pages/inventory_page.py`：`build_payload` 形参增加 `map_exploration_text: str = ""`，写入 payload 与 `render()` 的 data dict

### 3. 网页渲染（`server/templates/inventory.html`）
- `.stats-tiles` grid 由 5 列改为 6 列：`repeat(6, minmax(0, 1fr))`
- 新增一个 `.stat-tile`：
  - label：`地图探索率`
  - value：`map_exploration_text`，缺失 → 整个 tile `hidden`（与 `tile-online-time` 同模式）
- 仍遵守 DESIGN.md：复用 `--color-surface-card` / `--color-hairline` / `--radius-md` token，与现有 tile 完全一致，不引入新色 / 新字号

## Non-goals

- 不改 `进度` 命令或其他截图模板
- 不动 API path / timeout / 错误处理
- 不动统计栏的隐藏开关 `show_stats`
- 不重排 tile 顺序（地图探索率追加到 在线时长 之后）

## Acceptance Criteria

- [ ] API 返回 `mapExplorationPercent: 2.41` 时，截图统计栏第 6 个 tile 显示 "地图探索率 / 2.41%"
- [ ] API 缺该字段时，tile 整个隐藏（与现有 在线时长 缺失行为一致）
- [ ] 6 列 grid 在 1900px 宽度下排版正常，不溢出
- [ ] 视觉风格（背景色、边框、字号、间距）与原 5 个 tile 完全一致
- [ ] `用户背包` 与 `我的背包` 都能正确显示新字段

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再提交
