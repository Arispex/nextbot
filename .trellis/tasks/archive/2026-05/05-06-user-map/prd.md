# 用户地图查询命令

## Goal

新增 `用户地图 <服务器 ID> <用户 QQ/@用户/用户名称>` 命令，作为 `我的地图` 的"指定用户版"，类似 `用户背包` ↔ `我的背包` 的关系。让管理员或其他玩家可以查询任意目标用户在某服务器世界中的探索地图。

## Background

- 已有 `我的地图`（`handle_my_map`，nextbot/plugins/player_query.py:600）查当前发起人的地图
- 已有 `用户背包` / `我的背包` 模式：`handle_user_inventory`（line 314）用 `resolve_user_id_arg_with_fallback` 解析目标用户参数，复用同一 API 模板，仅替换调用者用户名
- 后端 API `/nextbot/users/{user.name}/map-image` 已支持任意用户名

## Requirements

- 在 `nextbot/plugins/player_query.py` 新增：
  - matcher：`user_map_matcher = on_command("用户地图")`，紧邻 `my_map_matcher` 定义
  - handler：`handle_user_map`，紧邻 `handle_my_map`
  - command_control 元数据：
    - `command_key="player_query.map.user"`
    - `display_name="用户地图"`
    - `permission="player_query.map.user"`
    - `description="查询指定用户在指定服务器世界中的探索地图"`
    - `usage="用户地图 <服务器 ID> <用户 QQ/@用户/用户名称>"`
    - `category="玩家查询"`
- 参数解析：
  - `args[0]` 转 `server_id`（int），失败 → `raise_command_usage`
  - `args[1]` 通过 `resolve_user_id_arg_with_fallback(event, arg, "用户地图", arg_index=1)` 解析 → 目标 `user_id`，对应错误回复（missing → usage、name_not_found → "用户名称不存在"、name_ambiguous → "用户名称不唯一，请使用用户 QQ 或 @用户"、None → "用户参数解析失败"）
- 业务流程与 `handle_my_map` 一致，仅把 `user` 替换为 `target_user`：
  - 查 `Server` / `User`
  - 调 `/nextbot/users/{target_user.name}/map-image`，timeout 30s
  - decode base64，写 `/tmp/map-{server.id}-{target_user.user_id}-<ts>.png`
  - OneBot V11 发送 `OBV11MessageSegment.image(file="base64://...")`，**不附 @at**（与 `用户背包` 一致：查别人的数据，结果消息不需要 @ 发起人）
  - 失败路径全部返回中文 `reply_failure("查询", ...)`
- 日志：与 `我的地图` 一致的两条 `logger.info`，但前缀改成 `用户地图请求` / `用户地图发送成功`，并加上 `requester_user_id`（发起者 QQ）字段，方便审计 "谁查了谁的地图"
- 在 `nextbot/db.py` 的 `DEFAULT_GUEST_PERMISSIONS` 加入 `"player_query.map.user"`（按字母序插入 `player_query.map.self` 之后）

## Non-goals

- 不改 `我的地图` 已有逻辑
- 不动后端 API
- 不动 `INVENTORY_SCREENSHOT_OPTIONS` / 截图渲染管线（地图 API 直接返回 PNG，不走 page+screenshot）

## Acceptance Criteria

- [ ] OneBot V11 群聊执行 `用户地图 1 @某人` / `用户地图 1 12345` / `用户地图 1 玩家名`，机器人都能返回该目标用户在 1 号服务器的地图图片
- [ ] 服务器不存在 → "❌ 查询失败，原因：服务器不存在"
- [ ] 目标用户不存在 → "❌ 查询失败，原因：用户不存在"（DB 查不到）
- [ ] 用户名称不唯一 → "❌ 查询失败，原因：用户名称不唯一，请使用用户 QQ 或 @用户"
- [ ] API 报错 → 透传 `error.message`（保持与 `用户背包` 一致）
- [ ] guest 组默认拥有 `player_query.map.user` 权限
- [ ] `菜单` 命令能列出 `用户地图`

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再提交
