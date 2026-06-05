# 现有「上下线通知」架构 + Boss 召唤通知平行实现蓝图

## 现有上下线通知（参照对象）端到端

### 1. 对外 API（复用此端点）
`server/routes/webui_player_events.py` → `POST /webui/api/player-events`：
- 入参：`player_name`、`server_name`、`event`(online/offline/message)、`message`(message 事件)。
- 校验：非空、长度上限（player/server 64、message 500）、禁止控制字符（`_contains_forbidden_chars`，保留 \t\n）、换行数上限。
- `_pick_onebot_bot()` 选 OneBot V11 bot；无 → 503 `bot_unavailable`。
- 目标群：`_resolve_target_groups()` 读 `player_notify_mode`(all/single)+`player_notify_group_id`；空 → 503 `service_misconfigured`。
- QQ 绑定：`_resolve_user_id_by_name(player_name)`（`func.lower(User.name)==name.lower()`）→ `display_name = f"{player_name}（{qq}）"`（命中）否则原名。
- 模板：`player_notify_online_template` / `player_notify_offline_template`（默认 `[{server}]{player} 上线了/下线了`）。
- 渲染：`_render_template(template, display_name, server_name, message_text)` —— **一次性**替换 `{player}`/`{server}`/`{message}`，且先把用户输入里的 `{ }` 转全角 `｛｝` 防二次替换。
- 下发：逐群 `bot.call_api("send_group_msg", group_id, message=text)`，每群成功/失败独立收集，返回 `{sent_groups, failed_groups, results, summary}`。
- 顶层 try/except → 500 `internal_error`（不泄漏栈）。

### 2. 鉴权（复用 → 天然同款）
`server/routes/webui.py` `add_webui_auth_middleware`：对 `/webui/*`（除 `/webui/login`、`/webui/api/session`、`/webui/static/`）校验 `_is_authenticated`：session cookie **或** `?token=<webui_token>`（`hmac.compare_digest`）。`/webui/api/*` 未授权 → 401 JSON。
→ **boss 复用 `/webui/api/player-events` 端点，鉴权 0 改动、与现有完全一致。**

### 3. 设置后端
`server/settings_service.py`：`.env` 持久化，每字段一个 `FieldSpec(field, env_key)`：
- 现有：`player_notify_mode`(PLAYER_NOTIFY_MODE) / `player_notify_group_id` / `player_notify_online_template` / `player_notify_offline_template`。
- `_FIELD_SPECS` 注册；`_SINGLE_LINE_STRING_FIELDS` 列出单行字段；`_normalize_field` 校验/归一；`_load_value_from_config` 给默认值。
- `get_settings_snapshot()` 读（env 优先，失败回退 config 默认）；`save_settings(payload)` 写（`_normalize_payload` 仅允许 `_FIELD_BY_NAME` 内字段）。

### 4. Web UI 前端
- `server/webui/templates/settings_content.html`：`<h3 id="section-player-notify">上下线通知</h3>` 区块 = 通知范围(select all/single, `field-player-notify-mode`) + 指定群号(`field-player-notify-group-id`) + 上线/下线模板(`field-player-notify-online/offline-template`)。
- `server/webui/static/js/settings.js`：
  - 顶部 `getElementById` 取各 input；
  - `fieldLabels` map（校验错误展示名，:98-104）；
  - save 时 `payload = { player_notify_mode: ..., ... }`（:352-358）；
  - `fillForm(data)` 用 snapshot 回填 + 默认值（:387-398）。

## Boss 召唤通知平行实现（复用旧 API）

### A. API（`webui_player_events.py`）
1. `_ALLOWED_EVENTS` 加 `"boss_summon"`。
2. 新增 `boss` 参数解析（仅 boss_summon 事件需要）：非空、`_BOSS_NAME_MAX_LENGTH=64`、`_contains_forbidden_chars` 校验，缺失/超限 → 422（field=`boss`）。
3. `_render_template` 增加 `boss` 形参 + `.replace("{boss}", safe_boss)`（`_strip_braces(boss)`）；其它事件传 `boss=""`。
4. 目标群：boss_summon → 新增 `_resolve_boss_target_groups()` 读 `boss_notify_mode`/`boss_notify_group_id`（仿 `_resolve_target_groups`）。
5. 模板选择：boss_summon → `boss_notify_template`（默认 `[{server}]{player} 召唤了 {boss}`）。
6. `display_name` 沿用 `_resolve_user_id_by_name` QQ 绑定（与上下线一致）。
7. 其余（选 bot、逐群下发、结果结构、日志、异常）完全复用。

### B. 设置后端（`settings_service.py`）
新增 3 个 `FieldSpec`：`boss_notify_mode`(BOSS_NOTIFY_MODE)、`boss_notify_group_id`(BOSS_NOTIFY_GROUP_ID)、`boss_notify_template`(BOSS_NOTIFY_TEMPLATE)。
- `_SINGLE_LINE_STRING_FIELDS` 加这三项。
- `_normalize_field`：`boss_notify_mode` 同 player（all/single，非法→all）；`boss_notify_group_id` allow_empty 字符串；`boss_notify_template` allow_empty 字符串。
- `_load_value_from_config`：`boss_notify_mode`→"all"、`boss_notify_group_id`→""、`boss_notify_template`→`[{server}]{player} 召唤了 {boss}`。

### C. Web UI 前端
- `settings_content.html`：新增 `<h3 id="section-boss-notify">Boss 召唤通知</h3>` 区 = 通知范围(`field-boss-notify-mode` select all/single) + 指定群号(`field-boss-notify-group-id`) + 消息模板(`field-boss-notify-template`, placeholder `[{server}]{player} 召唤了 {boss}`)。
- `settings.js`：取三个 input；`fieldLabels` 加三项；save payload 加三项；`fillForm` 回填 + 默认值。

### D. 测试
- API：boss_summon 事件成功下发（mock bot + 群解析 + 模板渲染含 {boss}）；缺 boss → 422；boss 非法字符 → 422；{boss} 注入防护（boss 含 `{player}` 不被二次替换）；未配置群 → 503。
- settings_service：boss_notify_* normalize（mode 非法→all）、默认值、save/snapshot round-trip。
- 既有 player-events（online/offline/message）不回归。

### E. API 文档（实现后产出给用户）
`POST /webui/api/player-events`（boss_summon 事件）：鉴权（`?token=` 或 session）、入参（player_name/server_name/event=boss_summon/boss）、响应（sent_groups/failed_groups/results/summary）、错误码（422/503/500）、模板占位符（{server}/{player}/{boss}）。
