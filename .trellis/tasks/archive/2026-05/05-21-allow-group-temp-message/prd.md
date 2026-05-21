# feat: 允许白名单群成员发起群临时会话

## Goal

`bot.py:107-147` 的 `_filter_allowed_messages` event_preprocessor 目前只放行：
- 白名单群（`GROUP_ID`）的群消息
- owner（`OWNER_ID`）的好友私聊

**问题**：白名单群成员对机器人发起"群临时会话"（QQ 客户端"临时消息"对话框）目前被当作 `message_type=private` 拒掉。注册账号后用户想私聊问问题、修改密码等场景体验断裂。

**目标**：允许白名单群（`GROUP_ID`）成员通过群临时会话给机器人发消息。owner 私聊行为不变；非白名单源群的临时会话仍拒绝；好友私聊仍仅 owner 可用。

## Requirements

### R1 — 识别"群临时会话"

OneBot v11 spec：群临时会话事件是 `PrivateMessageEvent` + `sub_type="group"`（NoneBot 实现见 `nonebot/adapters/onebot/v11/event.py:198-208`）。

新过滤逻辑（伪代码，替换 `_filter_allowed_messages` 中 `message_type == "private"` 分支）：

```python
if message_type == "private":
    user_id = event.get_user_id()
    # owner 任何形态私聊都放行（含好友、临时会话）
    if user_id in owner_ids:
        return
    
    sub_type = str(getattr(event, "sub_type", "")).strip()
    if sub_type == "group":
        # 群临时会话：检查源群 ID 是否在白名单
        source_group_id = _extract_temp_source_group_id(event)
        if source_group_id is not None and source_group_id in group_ids:
            logger.info(
                f"消息放行：type=private sub_type=group user_id={user_id} "
                f"source_group_id={source_group_id}"
            )
            return
        logger.info(
            f"消息被过滤：type=private sub_type=group user_id={user_id} "
            f"source_group_id={source_group_id}"
        )
        raise IgnoredException("group temp message blocked by group_id allowlist")
    
    # 好友私聊：仍仅 owner
    logger.info(f"消息被过滤：type=private sub_type={sub_type or 'friend'} user_id={user_id}")
    raise IgnoredException("private message blocked by owner_id allowlist")
```

### R2 — `_extract_temp_source_group_id(event)` helper

OneBot v11 标准 spec 里临时会话没有顶层 `group_id` 字段，但 NapCat / Lagrange 等主流实现把它扩展到 `sender.group_id`（pydantic `extra="allow"` 保留）。一小部分实现放在事件顶层 `event.group_id`。

```python
def _extract_temp_source_group_id(event) -> str | None:
    # 优先：sender.group_id（NapCat / Lagrange / OneBot 扩展，sender 是 pydantic extra='allow'）
    sender = getattr(event, "sender", None)
    if sender is not None:
        gid = getattr(sender, "group_id", None)
        if gid is None:
            extra = getattr(sender, "model_extra", None) or {}
            gid = extra.get("group_id")
        if gid is not None:
            text = str(gid).strip()
            if text and text != "0":
                return text
    # 备选：事件顶层 group_id（少数实现）
    gid = getattr(event, "group_id", None)
    if gid is not None:
        text = str(gid).strip()
        if text and text != "0":
            return text
    return None
```

`group_id == "0"` 视为 absent（OneBot 实现里可能用 0 表示"无"）。

### R3 — 放在 `bot.py` 内还是抽到模块

helper 仅这一个 callsite，可以放在 `bot.py` 内（与现有 `_filter_allowed_messages` 同文件，私有函数 `_extract_temp_source_group_id`）。**不需要**新建模块。

### R4 — 日志增强

新增/调整三类日志（key=value 风格，与现有日志体系一致）：

- 临时会话放行：`消息放行：type=private sub_type=group user_id=<n> source_group_id=<g>`
- 临时会话拒绝：`消息被过滤：type=private sub_type=group user_id=<n> source_group_id=<g or None>`
- 好友私聊拒绝（保留原日志风格但加 sub_type 字段）：`消息被过滤：type=private sub_type=<friend|other|...> user_id=<n>`

owner 私聊放行**不打日志**（默认行为；现在 owner 私聊也是 silent 放行的）。

## Acceptance Criteria

- [ ] 白名单群成员对机器人发起群临时会话（QQ"临时消息"对话框）→ 消息能被机器人接收并触发命令 / 回复。
- [ ] 非白名单源群的临时会话 → 仍被过滤；日志含 `source_group_id` 字段。
- [ ] owner 的好友私聊 → 仍能接收（与现状一致）。
- [ ] 非 owner 的好友私聊 → 仍被过滤（与现状一致）。
- [ ] 白名单群的群消息 → 仍能接收（与现状一致，未触及该分支）。
- [ ] 非白名单群的群消息 → 仍被过滤。
- [ ] Console adapter 的 `user="user"` 绕过仍能用（保留原 MH-1 / U-1.2 防御）。
- [ ] 拿不到 `source_group_id`（既不在 sender 也不在 event 顶层）→ 保守拒绝，日志记录便于排查。

## Definition of Done

- 通过 trellis-check。
- 不破坏现有放行 / 过滤分支（owner / 白名单群 / 普通群消息 / console bypass）。
- 文案 / 日志符合 CLAUDE.md（key=value 机器搜索风格，时区 / level 由 logger 自动加）。

## Out of Scope

- 不动 owner 提取（`get_owner_ids`）或 group 提取（`get_group_ids`）helper。
- 不动 .env 模板字段（`OWNER_ID` / `GROUP_ID` 含义不变）。
- 不引入新配置项区分"允许临时会话的群子集"（默认与 `GROUP_ID` 等价；若日后有需要再加 `GROUP_ID_TEMP_ALLOW` 之类的独立配置）。
- 不改任何业务命令 / WebUI。
- 不改 `_send_temp_private_password`（上一个任务已修，机器人 → 用户方向）。本任务是相反方向：用户 → 机器人。

## Technical Notes

- OneBot v11 PrivateMessageEvent 定义：`.venv/lib/python3.14/site-packages/nonebot/adapters/onebot/v11/event.py:198`
- Sender pydantic `extra="allow"` 保留扩展字段：`event.py:79`
- 现有过滤入口：`bot.py:107-147 _filter_allowed_messages`
- access_control helper：`nextbot/access_control.py get_group_ids() / get_owner_ids()`
