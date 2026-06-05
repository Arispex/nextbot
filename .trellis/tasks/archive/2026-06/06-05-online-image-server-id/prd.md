# 在线图片模式显示服务器 ID

## Goal

在线命令图片模式（`image_mode=True`）渲染的在线玩家榜单图中，每个服务器分区的标题处显示该服务器的 **ID（数字）**，与文字模式（`{id}.{name}`）信息对齐，便于用户对照服务器 ID 执行后续命令。

## What I already know

- 图片模式分区头当前只显示 `server_name` + `N 名玩家`（`server/templates/online_players.html` `server-head`：`server-name` + `server-count`）。
- handler `_handle_online_image`（`nextbot/plugins/player_query.py`）构造 `page_servers` 为 `{"server_name": result.server.name, "players": cards}` —— **未带 server_id**，`result.server.id` 现成可取。
- page builder `online_players_page._normalize_servers` / `build_payload` 只透传 `server_name` + `players`。
- 文字模式 `_query_online_status_one` 输出 `f"{server.id}.{server.name}"`（ID 在前，点分隔）——既有展示约定。

## Requirements

- 图片模式分区头显示服务器 ID（数字）+ 服务器名。
- 数据流贯穿：handler 传 `server_id` → `build_payload`/`_normalize_servers` 纳入 → 模板渲染。
- ID 缺失/异常兜底不崩（与现有 best-effort 一致）。
- 仅影响图片模式分区头；文字模式不变；玩家卡片不变。
- HTML 注入安全沿用（`</`→`<\/`）。

## Acceptance Criteria

- [ ] 图片模式每个服务器分区头显示其 ID 数字 + 名称。
- [ ] `server_id` 从 handler 经 page payload 流到模板，类型/缺失兜底。
- [ ] 文字模式、玩家卡片、降级逻辑均不回归。
- [ ] 单测覆盖 payload 含 server_id + 模板渲染含 ID。
- [ ] ruff / pyright / 测试全绿。

## Decision (ADR-lite)

- **Context**：图片模式分区头需显示服务器 ID，且要选一种展示格式。
- **Decision**：用 **`{id}. {name}`**（ID 前缀 + 点 + 空格 + 名称，如 `3. 生存服`），与文字模式 `{id}.{name}` 信息对齐；视觉上 ID 可略微弱化（muted），但文案即「ID. 名称」。
- **Consequences**：跨模式（文字/图片）信息一致，用户可直接对照 ID 执行后续命令；与文字模式的细微差异仅在点后加一个空格（卡片 UI 有空间，更易读）。

## Out of Scope

- 不改文字模式格式。
- 不改 `/nextbot/online-players` 服务端。
- 不在玩家卡片上加服务器 ID（只在分区头）。

## Technical Notes

- 改动点：`nextbot/plugins/player_query.py`（`_handle_online_image` page_servers 加 `server_id=result.server.id`）；`server/pages/online_players_page.py`（`_normalize_servers` 纳入 `server_id`，`int` 兜底）；`server/templates/online_players.html`（`server-head` 渲染 ID）。
- 测试：`tests/test_online_image_mode.py` 既有用例补 server_id 断言。
- 展示脚本 `temp/showcase_online_players.py` 的 servers 项可加 `server_id` 以便复看效果（scratch）。
