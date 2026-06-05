# 服务器配置 dialog 加 bossSummon 事件开关

## Goal

Web UI 服务器页面的「插件配置」dialog 里「玩家事件推送」section，TShock 插件侧新增了 `playerEvents.bossSummon` 配置（推送 Boss 召唤事件）。在前端配置 schema 中补上该项，使其在 dialog 中显示为可切换开关。

## What I already know

- 配置 schema 在 `server/webui/static/js/servers.js` 的「玩家事件推送」section（约 :153-160），现有项：`playerEvents.enabled`（总开关）/ `playerEvents.online` / `playerEvents.offline` / `playerEvents.message`，均 `type: "bool"`。
- dialog 渲染过滤（:818）：只显示 `getByPath(config, item.path) !== undefined` 的 schema 项——即仅显示 TShock fetched config 里存在的项。TShock 既已新增 `bossSummon`，补 schema 项即可显示。
- 保存：`PATCH /webui/api/servers/{id}/plugin-config`（`server/routes/webui_servers.py:519`）是**通用透传**——仅校验 key 格式（`_PLUGIN_CONFIG_KEY_PATTERN`：字母数字下划线点，≤128）+ value 类型，**无具体 key 白名单**。`playerEvents.bossSummon` 直接透传到 TShock `/nextbot/config/update`。
- load：`GET /webui/api/servers/{id}/plugin-config` → TShock `/nextbot/config`，原样返回。

## Requirements

- 在 `servers.js`「玩家事件推送」section 的 items 数组中新增：`{ path: "playerEvents.bossSummon", label: "推送 Boss 召唤事件", type: "bool" }`，置于 `playerEvents.message` 之后（与上下线/消息并列）。
- 不改后端（透传已支持）、不改全局设置、不改其它 section。

## Acceptance Criteria

- [ ] dialog「玩家事件推送」显示「推送 Boss 召唤事件」开关（当 TShock config 含 `playerEvents.bossSummon`）。
- [ ] 切换并保存后 `playerEvents.bossSummon` 经 PATCH 透传到 TShock（key 格式校验通过）。
- [ ] 其它事件开关 / dialog 其它 section 不回归。

## Out of Scope

- 不改后端 plugin-config 路由（通用透传已支持）。
- 不改上一任务的全局 Boss 召唤通知设置（`boss_notify_*`）——那是 bot 侧通知配置，本任务是 TShock 插件侧「是否推送该事件」开关，两者独立。
- 不动 TShock 插件本身（其已新增 bossSummon 配置）。

## Technical Notes

- 唯一改动：`server/webui/static/js/servers.js`「玩家事件推送」items 加一行。label 用用户给定「推送 Boss 召唤事件」。
- 与全局 `boss_notify_*` 的关系：TShock `playerEvents.bossSummon`=该服务器是否上报 boss 事件到 bot；bot 收到后按全局 `boss_notify_*` 决定推送哪些群——链路互补。
