# 迁移所有截图功能到 screenshot_render.py 公共 helper

## Goal

把 `nextbot/plugins/` 下还在用"旧模式"（自定义 `_to_base64_image_uri` + 手写 `temp_screenshot_path` + `screenshot_url` + 各自 size cap / semaphore 实现）的截图发送链，统一迁移到 `nextbot/screenshot_render.py:render_and_send_screenshot()` helper。

收益：
- 统一 OOM 防护（semaphore + MAX_BASE64_BYTES + V11/非 V11 分支）
- 消除 7+ 处 `_to_base64_image_uri` 重复函数
- 一致的非 V11 fallback 行为（不暴露 /tmp 路径）
- 后续 helper 升级一处生效

## 迁移范围

### 在范围（9 个 site，7 个文件）

| 文件 | site | handler |
|---|---|---|
| `nextbot/plugins/ban.py` | 1 (line 216) | `handle_ban_list` |
| `nextbot/plugins/permission_manager.py` | 1 (line 653) | `handle_admin_list` |
| `nextbot/plugins/shop.py` | 2 (line 276 + 443) | `handle_shop_list` + `handle_shop_view` |
| `nextbot/plugins/red_packet.py` | 1 (line 500) | 红包列表渲染 handler |
| `nextbot/plugins/warehouse.py` | 1 (line 286) | 仓库渲染 handler |
| `nextbot/plugins/user_manager.py` | 1 (line 321) | `handle_user_info` |
| `nextbot/plugins/player_query.py` | 2 (line 498 + 1172) | 用户背包 / 进度（用 Playwright 渲染） |

### 不在范围（保留现有写法）

`player_query.py` 的 3 个地图 handler（我的地图 / 用户地图 / 查看地图，line 665 / 798 / 947）**直接消费 API 返回的 base64**，不经 Playwright，与新 helper 模式不匹配。已加 `_my_map_semaphores` / `_user_map_semaphores` / `_explored_map_semaphores` + `MAX_BASE64_BYTES` 保护，保留。

## 验收标准

- [ ] 9 个 site 全部改为调用 `render_and_send_screenshot()`
- [ ] 删除每个文件本地的 `_to_base64_image_uri`（7 处重复 → 0）
- [ ] 保留每个文件原有的 per-handler semaphore 概念，作为 helper 的 `semaphore` 参数传入（如 `_ban_list_semaphore` / `_admin_list_semaphore` / 新建 `_shop_*_semaphore` 等）
- [ ] **无破坏性更新**：V11 成功路径输出 byte-identical（image segment 不变）；失败 / 非 V11 fallback 文案统一为新 helper 格式（视为可接受改进）
- [ ] **失败文案符合规范**：`reply_failure(action, reason)` 全程合规
- [ ] **修后再检查**：派 trellis-check 确认 9/9 site 正确迁移 + 无新引入问题

## Out of Scope

- `player_query.py` 的 3 个地图 handler（API base64 模式，不适用新 helper）
- 删除 helper 本身（保留）
- 修改 `screenshot_render.py` 签名（如需扩展能力，可加可选参数但不破坏现有调用方）
- 11 个先前已审 plugin 的其他逻辑

## Technical Notes

- `render_and_send_screenshot(bot, event, *, page_url, options, file_prefix, semaphore=None, failure_action="查询", success_caption=None) -> bool`
- 每个迁移 site 提取出 `page_url + options + file_prefix` 三件套，传入 helper
- `failure_action` 通常用原 reply_failure 的 action（如 "查询" / "封禁列表" 等）
- 对于已有 per-handler semaphore（如 `_ban_list_semaphore = asyncio.Semaphore(2)`），传入 helper 的 `semaphore` 参数；保留 module-level 变量
- 删除每个文件 import 的 `screenshot_url`、`temp_screenshot_path`、`RenderScreenshotError`、`base64`（如不再用）
- 注意 `red_packet.py` 等可能在 file_prefix 里包含 user_id 等动态参数，迁移时保留
