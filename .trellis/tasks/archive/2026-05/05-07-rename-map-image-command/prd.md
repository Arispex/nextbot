# 查看地图 命令重命名为 全亮地图

## Goal

把 `nextbot/plugins/server_tools.py` 里的 `查看地图` 命令重命名为 `全亮地图`。仅命令名变化，业务逻辑、权限、调用 API 全部保留。

## Background

当前命令 `查看地图 <服务器 ID>` 调用 `/nextbot/world/map-image` 生成全图（**整张世界地图，全亮**）。

新增的 `我的地图` / `用户地图` 是查"个人探索过的地图"，与全图概念不同。`查看地图` 这个名字与 `我的地图` / `用户地图` 容易混淆，重命名为 `全亮地图` 更准确。

## 改动范围（4 处，全部在 `nextbot/plugins/server_tools.py`）

| 行号 | 原文 | 新文 |
|---|---|---|
| 28 | `map_image_matcher = on_command("查看地图")` | `map_image_matcher = on_command("全亮地图")` |
| 137 | `display_name="查看地图"` | `display_name="全亮地图"` |
| 140 | `usage="查看地图 <服务器 ID>"` | `usage="全亮地图 <服务器 ID>"` |
| 147 | `parse_command_args_with_fallback(event, arg, "查看地图")` | `parse_command_args_with_fallback(event, arg, "全亮地图")` |

## 不动的部分

- `command_key="server_tools.map_image"` —— DB 主键，保持稳定
- `permission="server_tools.map_image"` —— 既有权限配置不破坏
- `category="服务器工具"`
- `description` / API 调用 / 错误回复文案 / 截图逻辑

## DB 自动同步

启动时 `command_control` 装饰器会把新的 `display_name` / `usage` 同步到 `command_config` 表（按 `command_key` 主键），无需手动迁移。

## Acceptance Criteria

- [ ] 用户在群里发 `全亮地图 1` 能正常返回地图图片
- [ ] 用户在群里发 `查看地图 1` 不再被识别（命令未注册）
- [ ] 重启 bot 后菜单显示"全亮地图"，不再显示"查看地图"
- [ ] 已有 `server_tools.map_image` 权限的用户和分组配置不受影响（无需重新配权限）

## Definition of Done

- 单一 commit
- 用户测试通过后再提交
