# Boss 召唤通知

## Goal

参考现有「上下线通知」，新增「Boss 召唤通知」：当玩家召唤 Boss 时，经对外 API 推送通知到配置的 QQ 群。**复用现有 `POST /webui/api/player-events` 端点**（新增 `boss_summon` 事件 + `boss` 参数），鉴权天然与上下线一致。设置项（通知范围 / 指定群号 / 消息模板）在 Web UI 可配，默认模板 `[{server}]{player} 召唤了 {boss}`。

## Requirements

### 1. 设置（Web UI 可配，仿上下线通知）
- `boss_notify_mode`：通知范围（`all`=推送到 GROUP_ID 全部群 / `single`=仅指定群），默认 `all`。
- `boss_notify_group_id`：指定群号（single 模式生效）。
- `boss_notify_template`：消息模板，默认 `[{server}]{player} 召唤了 {boss}`。
- 落 `.env`（`settings_service.py` 新增 3 个 FieldSpec）；Web UI 新增「Boss 召唤通知」设置区（通知范围 / 指定群号 / 消息模板）。

### 2. 对外 API（复用旧端点）
- 复用 `POST /webui/api/player-events`，新增事件 `event: "boss_summon"` + 参数 `boss`（Boss 名称）。
- 入参：`player_name`、`server_name`、`event="boss_summon"`、`boss`。
- 鉴权：沿用 `add_webui_auth_middleware`（`?token=<webui_token>` 或 session），与上下线**完全一致**，0 改动。
- 校验：`boss` 非空、长度上限 64、禁止控制字符（仿 player_name）；缺失/超限/非法 → 422。
- 行为：选 OneBot bot → 解析 boss 目标群（`boss_notify_mode`/`boss_notify_group_id`）→ QQ 绑定 `{player}=name（QQ）`（命中时，同上下线）→ 渲染 `boss_notify_template`（`{server}`/`{player}`/`{boss}`）→ 逐群下发 → 返回 `{sent_groups, failed_groups, results, summary}`。

### 3. 模板占位符
- `{server}`、`{player}`、`{boss}`；`_render_template` 增加 `{boss}`，沿用「一次性替换 + 用户输入 `{}`→全角」防二次注入。

### 4. API 文档
实现后产出 `POST /webui/api/player-events`（boss_summon）的 API 文档给用户（鉴权、入参、响应、错误码、模板占位符）。

## Acceptance Criteria

- [ ] Web UI「Boss 召唤通知」区可配通知范围 / 指定群号 / 消息模板，保存落 `.env`、快照回填。
- [ ] `POST /webui/api/player-events` `event=boss_summon` + `boss` → 按 boss 设置渲染默认模板并下发目标群。
- [ ] `boss` 缺失/超长/非法字符 → 422；未配置群 → 503；bot 未连 → 503；异常 → 500。
- [ ] `{boss}` 占位符渲染正确；boss/player 含 `{}` 不被二次替换。
- [ ] 鉴权与上下线一致（复用中间件，未授权 401）。
- [ ] 既有 online/offline/message 事件不回归。
- [ ] 单测覆盖 API（成功 / 各错误 / 注入防护）+ settings（normalize / 默认 / round-trip）。
- [ ] 产出 API 文档。
- [ ] ruff / pyright / 测试全绿。

## Decision (ADR-lite)

- **Context**：要加 Boss 召唤通知，与上下线通知高度同构。
- **Decision**：复用 `/webui/api/player-events` 端点（加 `boss_summon` 事件 + `boss` 参数）而非新端点（用户指定「复用旧的 API」）；设置仿 `player_notify_*` 新增 `boss_notify_*`；`{player}` 沿用现有 QQ 绑定保持与上下线一致。
- **Consequences**：鉴权/下发/结果结构/前端全部复用，改动面小且一致；boss_summon 与 online/offline 共享端点，靠 `event` 分流。

## Out of Scope

- 不新建独立 API 端点（复用旧的）。
- 不改鉴权机制（复用中间件）。
- 不在 bot 命令侧加 Boss 通知触发（仅对外 API 驱动，由 TShock/插件侧调用）。
- 不做 Boss 名称中英映射/校验白名单（原样透传渲染）。

## Technical Notes

详见 `research/existing-player-events-blueprint.md`（端到端架构 + 平行实现逐文件落点）。改动文件：`server/routes/webui_player_events.py`、`server/settings_service.py`、`server/webui/templates/settings_content.html`、`server/webui/static/js/settings.js`、`tests/`、新增 API 文档。
