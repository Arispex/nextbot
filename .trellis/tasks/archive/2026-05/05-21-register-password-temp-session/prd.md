# fix: 注册账号密码私聊改走群临时会话

## Goal

`_send_temp_private_password` 调 `bot.call_api("send_private_msg", user_id=...)` 没传 `group_id`，导致 OneBot 实现 (go-cqhttp / NapCat / Lagrange) 走的是**好友私聊通道**而非"群临时会话"——非好友用户收不到密码。注释 + 日志号称"临时会话"，与实际不符。本任务修复为：始终传 `group_id`（来自注册命令所在群），让消息走"群临时会话"通道；已是好友的用户体验无影响。

## Requirements

### R1 — `_send_temp_private_password` 接收并透传 `group_id`

`nextbot/plugins/user_manager.py:109`：

- 函数签名加 `group_id: int | None = None` 参数。
- 构造 `call_api("send_private_msg", ...)` payload 时：若 `group_id is not None`，加入 `group_id=group_id`；否则只传 `user_id + message`（fallback 好友私聊）。
- 修正现有 docstring + 日志："临时会话" 不再是空话——加 group_id 后真正走群临时会话；没有 group_id 时回落到好友私聊。

### R2 — `handle_add_whitelist` 提取并传递 group_id

`nextbot/plugins/user_manager.py:212`：

- 在调用 `_send_temp_private_password(...)` 时，多传一个 `group_id=getattr(event, "group_id", None)`。
- `event.group_id` 只在 OneBot v11 `GroupMessageEvent` 上才存在；私聊里发的注册命令会拿到 `None`，自动回落好友私聊。
- 不引入对具体 Event 子类的硬依赖（用 `getattr` 防御性提取，与 NoneBot 跨 adapter 风格一致）。

### R3 — 日志增强（诊断用）

`_send_temp_private_password` 日志区分通道，便于运维定位"为啥没收到密码":

- 成功：`临时私聊密码已发送：user_id=<masked> name=<n> 通道=<group_temp|friend>`
- 失败：`临时私聊密码发送失败：user_id=<masked> name=<n> 通道=<group_temp|friend> reason=<exc>`

`通道` 字段取值：
- `group_temp` — 传了 `group_id`
- `friend` — 没传 `group_id`

## Acceptance Criteria

- [ ] 群里发"注册账号 xxx"（注册账号者不是机器人好友）→ 收到密码私聊（来自"群临时会话"通道）。
- [ ] 群里发"注册账号 xxx"（已是好友）→ 仍收到密码私聊，体验与改前无差（好友通道优先）。
- [ ] 私聊里发"注册账号 xxx"（边界，理论上 group_id=None）→ 走好友私聊；日志通道字段 = `friend`。
- [ ] 失败时日志能区分"群临时会话被屏蔽" 与 "好友私聊被屏蔽"（通过 `通道=` 字段）。
- [ ] 现有 reply 文案不变：群里仍回复 "✅ 注册成功 + 同步结果 + 密码私聊提示"。

## Definition of Done

- 通过 trellis-check。
- 不破坏现有注册流程任何分支（已注册 / 用户名占用 / hash 失败 / sync 失败）。
- 不引入 `from nonebot.adapters.onebot.v11 import GroupMessageEvent` 硬依赖（用 `getattr` 跨 adapter 安全）。
- 文案 / 注释 / 日志一致："临时会话" 字样名实相符。

## Out of Scope

- 不动 `_hash_password` / `_generate_random_password` / sync orchestrator / DB 事务。
- 不改"修改密码" / WebUI 创建用户的密码下发（WebUI 创建用户场景没法走群临时会话，行为不变）。
- 不强制好友 / 退群保护 / 拒收降级策略。

## Technical Notes

- OneBot v11 `send_private_msg` 参数表：`user_id`(required), `message`(required), `group_id`(optional — "主动发起临时会话时填"), `auto_escape`(optional)
- go-cqhttp / NapCat / Lagrange 行为一致：传 group_id 时若已是好友会优先走好友通道，否则走群临时会话；不传 group_id 强制走好友通道，非好友 retcode=非 0 失败。
- 现有代码位置：
  - `nextbot/plugins/user_manager.py:109` `_send_temp_private_password`
  - `nextbot/plugins/user_manager.py:212` `handle_add_whitelist`
  - `nextbot/plugins/user_manager.py:278` 调用 `_send_temp_private_password`
