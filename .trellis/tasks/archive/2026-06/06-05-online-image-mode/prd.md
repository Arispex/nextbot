# 在线命令图片模式：渲染在线玩家角色图

## Goal

给 `在线` 命令新增一个 **Web UI 可配的「图片模式」开关（默认开）**。开启时：对所有服务器调 `GET /nextbot/online-players` 拿在线**已登录**玩家的外观/装备/vanity/染料/配饰/`sessionOnlineSeconds`，用 `render_character` 渲染各玩家立绘，合成 **HTML 卡片榜单**（按服务器分区）经浏览器截图发送。关闭时：维持现有文字模式。严格二选一（要么文字、要么图片）。

## Requirements

- `在线` 的 `command_control` 新增 **Web UI 可配 `image_mode` bool 参数（默认 True）**——后台管理参数（像 `economy.sign` 的 `min_coins`/`enable_streak`），**非用户命令参数**。运行时 `bool(get_current_param("image_mode", True))` 读取（同 `economy.py:376`）。
- `image_mode=False` → 维持现有文字模式（`/v2/server/status`，所有服务器，`count/max` + 昵称列表），**不回归**。
- `image_mode=True`（默认）→ 对**所有服务器**并行调 `/nextbot/online-players`，渲染各**已登录可渲染**玩家立绘，合成 **HTML 卡片榜单**（按服务器分区；每玩家：**立绘 + 账号名 + 本次在线时长**）经 `create_*_page` + `render_and_send_screenshot` 截图发送。
- 图片模式**只显示已登录可渲染玩家**（`appearance` 非 null）；未登录在线玩家不进图；登录但 `appearance=null`（罕见，刚连入无 SSC）的玩家**跳过**。
- 边界降级：**无可渲染玩家** / **全部服务器查询失败** / **无服务器** → 降级**文字**（"无玩家在线" / 原始错误原因）；**部分服务器失败** → 渲染已成功的、跳过失败的（不因个别失败放弃整图）。
- 严格二选一：要么文字、要么图片，不混发。
- 命令权限沿用 `player_query.online`；`/nextbot/online-players` 的 `nextbot.online_players.view` 为**服务端权限**，由 `request_server_api` 的服务器 token 处理，命令侧无需新增权限。

## Acceptance Criteria

- [ ] `image_mode` 默认 True → `在线` 走图片模式，渲染并发送在线玩家角色榜单图。
- [ ] `image_mode=False` → 文字模式输出与现状一致（不回归）。
- [ ] 复用 `render_character`（装备/vanity/染料/配饰/配饰染料全字段）+ `create_*_page` + `render_and_send_screenshot`。
- [ ] 无可渲染玩家 / 全部失败 → 文字降级；部分服务器失败 → 渲染可用部分、跳过失败。
- [ ] 颜色等字段原样透传（不改服务端、不业务化改写）。
- [ ] 单测/集成覆盖：图片分支数据流、文字分支不回归、空/失败降级。

## Definition of Done

- 单测/集成测试覆盖图片模式数据流 + 文字模式不回归 + 边界降级。
- ruff / pyright / 测试 全绿。
- 用户反馈文案遵循 CLAUDE.md（动作+结果 / 动作+结果，原因；失败原样透传 API error）。

## Technical Approach

- **开关**：`command_control` 的 `params` 加 `image_mode`（bool, label「图片模式」, default True）；`handle_online` 首部 `image_mode = bool(get_current_param("image_mode", True))` 分支。
- **数据**：所有服务器并行 `request_server_api(server, "/nextbot/online-players")`（沿用现有 `_query_one` fan-out）。
- **渲染**：每玩家用 `_build_character_sprite_uri(appearance, equipment, vanity, dye, accessories, vanityAccessories, accessoryDyes)` → 立绘 data URI；新增 online-players HTML 页模板（`server/pages/`）+ `create_online_players_page(...)`（参 `create_inventory_page`），布局按服务器分区、每玩家立绘+账号名+在线时长；`render_and_send_screenshot` 发送。
- **文字模式**：保留现有 `_query_one` `/v2/server/status` 路径。

## Decision (ADR-lite)

- **Context**：需要图片/文字两种在线展示，由管理员后台控制。
- **Decision**：用 Web UI 可配 `image_mode` bool（默认 True）切换；图片模式复用 `render_character` + HTML 卡片截图管线，只渲已登录玩家，所有服务器分区；空/全失败降级文字。
- **Consequences**：图片模式比文字模式少显未登录玩家（API 限制）；多服务器多玩家时图较大/略慢（浏览器截图）；扩展点：后续可做单服务器筛选/渲染缓存/更多字段。

## Out of Scope

- 不改 `/nextbot/online-players` 服务端。
- 不做动画/多帧（立绘单帧）。
- 不做未登录玩家的占位渲染。

## Technical Notes

- 开关机制：`command_config.py` `get_current_param` / `command_control` `params`；参考 `economy.py:376`、`economy.py:320` 的 params schema。
- 当前 `在线`：`player_query.py:48 online_matcher` / `:280 handle_online`；fan-out `_query_one`。
- 渲染：`_build_character_sprite_uri:187` / `create_inventory_page`(`:15`) / `render_and_send_screenshot:35` / `render_character`。
- `/nextbot/online-players` 字段形状同 `GET /users/{user}/appearance`（`render_character` 已兼容）。
