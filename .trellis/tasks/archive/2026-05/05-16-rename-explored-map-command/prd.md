# 全亮地图 命令重命名为 查看全亮地图

## Goal

把 `nextbot/plugins/server_tools.py` 里的 `全亮地图` 命令重命名为 `查看全亮地图`。仅命令名变化，业务逻辑、权限 key、调用 API 全部保留。

## Background

之前的 `查看地图` 已重命名为 `全亮地图`（task `05-07-rename-map-image-command` / commit `ee6b320`），用于和 `我的地图` / `用户地图` 区分。

现在把命令再改名为 `查看全亮地图`，与同分类下的 `查看地图`（player_query 的 explored map，task `05-07-explored-map-command`）保持「查看 + 名词」的统一风格。

## Scope

仅 `nextbot/plugins/server_tools.py` 5 处字符串替换：

| 行 | 修改前 | 修改后 |
|---|---|---|
| 43 | `map_image_matcher = on_command("全亮地图")` | `map_image_matcher = on_command("查看全亮地图")` |
| 216 | `display_name="全亮地图",` | `display_name="查看全亮地图",` |
| 219 | `usage="全亮地图 <服务器 ID>",` | `usage="查看全亮地图 <服务器 ID>",` |
| 226 | `parse_command_args_with_fallback(event, arg, "全亮地图")` | `parse_command_args_with_fallback(event, arg, "查看全亮地图")` |
| 275 | `f"全亮地图返回数据过大：server_id={server.id} size_bytes={len(b64)}"` | `f"查看全亮地图返回数据过大：server_id={server.id} size_bytes={len(b64)}"` |

## Out of Scope

- 不改 `command_key="server_tools.map_image"` —— 是内部稳定标识，不能动（DB / 权限分组依赖）
- 不改 `permission="server_tools.map_image"` —— 同上
- 不改 matcher 变量名 `map_image_matcher` —— 内部实现细节
- 不改 `nextbot/plugins/player_query.py` 的 `查看地图` —— 是 explored map 命令，与本次无关
- 不改 archive 历史研究文档（`.trellis/tasks/archive/**`）

## Acceptance Criteria

- [ ] `nextbot/plugins/server_tools.py` 5 处字符串均已替换
- [ ] `grep -n "全亮地图" nextbot/plugins/server_tools.py` 无输出
- [ ] `command_key` / `permission` 保持 `server_tools.map_image` 不变
- [ ] 用户在群里发 `查看全亮地图 1` 能正常返回地图图片（人工验证）
- [ ] 重启 bot 后菜单显示 `查看全亮地图`，不再显示 `全亮地图`（人工验证）

## Technical Notes

- 参考 prior art：`05-07-rename-map-image-command`（同款字符串重命名）
- 不涉及 DB migration（command_key 不变）
- 不涉及权限重新配置（permission key 不变）
