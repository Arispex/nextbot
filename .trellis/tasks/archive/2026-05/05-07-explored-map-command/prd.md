# 新增 查看地图 命令（玩家共同探索地图）

## Goal

新增 `查看地图 <服务器 ID>` 命令，归到"玩家查询"分类，调用后端新 API `/nextbot/world/explored-map-image` 返回"全玩家探索区域并集"地图，默认对所有 guest 开放。

## Background

- 之前的 `查看地图`（全亮地图）已重命名为 `全亮地图`，归在"服务器工具"
- 新 API 返回"任意 TShock 账号走过的区域"——非黑色 = 至少有一个玩家走过
- 与 `我的地图` / `用户地图` 同族（玩家查询分类，按服务器 ID 调用），用法一致

## API 契约

`GET /nextbot/world/explored-map-image`

```json
{
  "fileName": "map-world-explored-2025-03-24_10-30-00.png",
  "base64": "<base64-encoded PNG>"
}
```

- 权限：`nextbot.world.explored_map_image`
- 渲染开销：1080p 大世界约 1–2 秒，**不缓存**
- 错误：500 + error 字段（服务未配置 / 合并渲染异常）

## Requirements

### 1. 命令注册（`nextbot/plugins/player_query.py`）

紧邻 `user_map_matcher` 后新增：
- `explored_map_matcher = on_command("查看地图")`

### 2. Handler 实现

紧邻 `handle_user_map` 之后新增 `handle_explored_map`，**镜像 `handle_my_map`**（仅一个服务器 ID 参数，不需要解析目标用户），但：
- `command_control`：
  - `command_key="player_query.map.explored"`
  - `display_name="查看地图"`
  - `permission="player_query.map.explored"`
  - `description="查看所有玩家共同探索过的区域地图"`
  - `usage="查看地图 <服务器 ID>"`
  - `category="玩家查询"`
- `@require_permission("player_query.map.explored")`
- 业务流程：
  1. 解析 args，校验只能 1 个参数；`int(args[0])` 转 server_id
  2. DB 查 `Server`，不存在 → `reply_failure("查询", "服务器不存在")`
  3. `request_server_api(server, "/nextbot/world/explored-map-image", timeout=30.0)`
  4. `is_success` / `payload.get("base64")` 校验
  5. `base64.b64decode(b64_string, validate=True)` 解码 PNG
  6. 用 `temp_screenshot_path` 写文件（与 `我的地图` / `用户地图` 一致）
  7. OneBot V11：`at(requester) + image(base64://)` 同消息发送
  8. 失败路径全部返回中文 `reply_failure("查询", ...)`
- 日志：
  - `logger.info(f"查看地图请求：server_id={server.id} requester_user_id={requester_user_id}")`
  - `logger.info(f"查看地图发送成功：server_id={server.id} requester_user_id={requester_user_id} file={path}")`

### 3. 权限（`nextbot/db.py`）

`DEFAULT_GUEST_PERMISSIONS` frozenset 中**插入** `"player_query.map.explored"`，按字母序放在 `player_query.map.user` 之后（`map.explored` 在 `.self` `.user` 之间字母序怎么排？let me think — ASCII：`.explored` < `.self` < `.user`，所以应该在 `.self` 之前）。具体位置以 sub-agent 自行判断为准（保持 set 内字母序即可）。

## 不动的部分

- 不改 `我的地图` / `用户地图` / `全亮地图` 现有逻辑
- 不改后端 API
- 截图选项 / 请求 timeout / 错误回复文案沿用 `我的地图` 同款

## Acceptance Criteria

- [ ] OneBot V11 群聊发 `查看地图 1`，返回该服务器的"全玩家探索区域并集"PNG
- [ ] 服务器不存在 → "❌ 查询失败，原因：服务器不存在"
- [ ] API 错误（500 服务未配置）→ 透传 `error.message`
- [ ] guest 组默认拥有 `player_query.map.explored` 权限
- [ ] `菜单` 命令能列出 `查看地图`，分类在"玩家查询"
- [ ] OneBot V11 同消息内 @ 发起人 + 图片（与 `我的地图` 一致）

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再提交
