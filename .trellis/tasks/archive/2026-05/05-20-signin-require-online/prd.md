# feat: 签到新增"要求在线"开关

## Goal

给"签到"命令新增一个开关参数 `require_online`，开启后玩家必须在任意服务器在线才能签到。默认关闭，保持现有行为不变。

## Requirements

- 在 `economy.sign` 的 `command_control(params=…)` 中新增 bool 参数 `require_online`，默认 `False`。
- 当 `require_online=True` 时，在 `handle_sign` 中（注册检查之后、生成奖励 / 写库之前）做"是否在线"检查：
  - 并行查询所有 `Server` 的 `/v2/server/status?players=true`。
  - 玩家匹配以 `User.name`（TShock 用户名，与白名单一致）为准，对比每个 server 的 `players[].nickname`（不区分大小写、strip 后比较）。
  - 任意一台服务器命中 → 视为在线，继续原签到流程。
  - 全部未命中（含服务器查询失败 / 离线）→ 视为不在线，返回失败：`reply_failure("签到", "请先进入服务器")`。
- 服务器列表为空时直接判定为不在线（避免"开了开关但没服务器→无脑通过"），返回同样的失败原因。
- `require_online=False` 时完全跳过查询，无性能开销。
- 检查不应阻塞过久：并行 fan-out，沿用 `request_server_api` 默认超时，模仿 `handle_online` 的并行模板。

## Acceptance Criteria

- [ ] `require_online` 参数出现在签到命令的 WebUI 配置面板，类型 bool，默认 off。
- [ ] 默认关闭时签到行为与现状一致（不发额外 HTTP 请求）。
- [ ] 开启后，玩家在线 → 签到正常成功（金币 / streak / 截图渲染都不变）。
- [ ] 开启后，玩家不在线 → 收到 `❌ 签到失败，请先进入服务器`，DB 不写入。
- [ ] 开启后某台服务器查询失败，但玩家在另一台在线 → 仍判定为在线，签到成功。
- [ ] 开启后所有服务器都查询失败 → 视为不在线（保守策略），返回失败。
- [ ] 关键日志：检查触发时记一条 [INFO]（user_id / online=True/False / hit_server_id / probed_count）。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 关键路径自检）。
- 不破坏已有 5 个签到参数的行为。
- 文案符合 CLAUDE.md 用户反馈规范（"动作 + 结果，原因"，原因为"请先进入服务器"，不带对象名）。

## Out of Scope

- 不引入"特定服务器才算在线"的精细配置（按需以后再说）。
- 不缓存在线列表（每次签到都现查）。
- 不修改其他命令（仅签到）。

## Technical Notes

- 现有签到入口：`nextbot/plugins/economy.py:289 handle_sign`
- 在线查询模板：`nextbot/plugins/player_query.py:183 handle_online`（`/v2/server/status?players=true` → `players[].nickname`）
- 命令参数读取：`get_current_param("require_online", False)`
- 失败回复：`reply_failure("签到", "请先进入服务器")`（动词通用，原因不含对象名）
- 并行 fan-out 模板：`asyncio.gather(..., return_exceptions=True)`
- User.name vs nickname 比较：strip + casefold（与 TShock 用户名风格一致，防止大小写差异）
