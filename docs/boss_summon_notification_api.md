# Boss 召唤通知 API

当玩家在游戏内召唤 Boss 时，由 TShock / 插件侧调用本 API，将通知推送到配置的 QQ 群。

本功能**复用上下线通知端点** `POST /webui/api/player-events`，通过 `event` 字段区分事件类型——Boss 召唤使用 `event=boss_summon`，鉴权、响应结构与上下线通知完全一致。

## POST `/webui/api/player-events`（`event=boss_summon`）

### 鉴权

与上下线通知一致（复用同一 Web UI 鉴权中间件），二选一：

- **Query Token**：`?token=<webui_token>`（与服务端 `webui_token` 等时比较）
- **Session Cookie**：已登录 Web UI 的会话 cookie

未授权 → `401 unauthorized`。

### 请求

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `player_name` | string | 是 | 召唤者玩家名。非空、长度 ≤ 64、禁止控制字符（保留 `\t` `\n`） |
| `server_name` | string | 是 | 服务器名。非空、长度 ≤ 64、禁止控制字符 |
| `event` | string | 是 | 固定为 `"boss_summon"` |
| `boss` | string | 是 | Boss 名称。非空、长度 ≤ 64、禁止控制字符；原样透传渲染 |

请求示例：

```json
{
  "player_name": "Alice",
  "server_name": "生存服",
  "event": "boss_summon",
  "boss": "克苏鲁之眼"
}
```

```bash
curl -X POST "http://<host>:<port>/webui/api/player-events?token=<webui_token>" \
  -H "Content-Type: application/json" \
  -d '{"player_name":"Alice","server_name":"生存服","event":"boss_summon","boss":"克苏鲁之眼"}'
```

### 行为

1. 选取已连接的 OneBot 实例（无则 `503 bot_unavailable`）。
2. 解析目标群：按设置 `boss_notify_mode`（`all`=推送到 `GROUP_ID` 全部群 / `single`=仅 `boss_notify_group_id` 指定群）；无有效群 → `503 service_misconfigured`。
3. QQ 绑定：若 `player_name` 命中已注册账号（不区分大小写），`{player}` 渲染为 `玩家名（QQ）`，否则为原玩家名。
4. 渲染模板 `boss_notify_template`（Web UI 可配，默认 `[{server}]{player} 召唤了 {boss}`）。
5. 逐群下发；每群成败独立返回，部分失败不影响整体 `200`。

### 响应 `200`

```json
{
  "data": {
    "sent_groups": [111, 222],
    "failed_groups": [
      { "group_id": 333, "reason": "<原始错误原因>" }
    ],
    "results": [
      { "group_id": 111, "message_id": 9111, "reason": null },
      { "group_id": 222, "message_id": 9222, "reason": null },
      { "group_id": 333, "message_id": null, "reason": "<原始错误原因>" }
    ],
    "summary": { "total": 3, "success": 2, "failed": 1 }
  }
}
```

| 字段 | 说明 |
|---|---|
| `sent_groups` | 成功下发的群号列表 |
| `failed_groups` | 失败的群及原始原因 |
| `results` | 逐群结果（`message_id` 成功时为消息 ID，失败为 `null`） |
| `summary` | `total` / `success` / `failed` 计数 |

### 错误码

| 状态码 | `code` | 触发条件 |
|---|---|---|
| `422` | `validation_error` | `boss` / `player_name` / `server_name` 缺失、超长（> 64）或含非法控制字符；`event` 非法。`details[].field` 指明字段，`message` 仅原因（如「Boss 名称不能为空」「Boss 名称包含非法字符」「长度不能超过 64」） |
| `503` | `service_misconfigured` | 未配置有效通知群（`single` 模式群号无效 / `all` 模式 `GROUP_ID` 为空） |
| `503` | `bot_unavailable` | 无已连接的 OneBot 实例 |
| `500` | `internal_error` | 未预期异常（`message` 固定「内部错误」，不泄漏堆栈） |
| `400` / `413` / `415` | `invalid_json` / `payload_too_large` / `unsupported_media_type` | 请求体非法 JSON / 过大 / 非 JSON |

### 消息模板与占位符

模板由设置项 `boss_notify_template` 提供（Web UI「Boss 召唤通知」区可配），默认 `[{server}]{player} 召唤了 {boss}`。

| 占位符 | 含义 |
|---|---|
| `{server}` | 服务器名 |
| `{player}` | 玩家名（命中注册账号时自动追加 `（QQ）`） |
| `{boss}` | Boss 名称 |

**注入防护**：`player_name` / `server_name` / `boss` 中的 `{` `}` 会先转为全角 `｛` `｝` 再做一次性替换，占位符仅来自模板本身。例如 `boss="{player}"` 渲染为字面 `｛player｝`，不会被二次替换成玩家名。

## Web UI 设置

「Boss 召唤通知」设置区（与「上下线通知」并列）：

| 设置项 | 字段 | 默认 | 说明 |
|---|---|---|---|
| 通知范围 | `boss_notify_mode` | `all` | `all`=推送到 `GROUP_ID` 全部群；`single`=仅指定群 |
| 指定群号 | `boss_notify_group_id` | （空） | `single` 模式生效 |
| 消息模板 | `boss_notify_template` | `[{server}]{player} 召唤了 {boss}` | 支持 `{server}` `{player}` `{boss}` |

设置持久化到 `.env`（`BOSS_NOTIFY_MODE` / `BOSS_NOTIFY_GROUP_ID` / `BOSS_NOTIFY_TEMPLATE`）。
