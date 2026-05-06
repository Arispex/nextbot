# 我的地图 增加艾特用户

## Goal

`我的地图 <服务器 ID>` 命令在发送地图图片时，**同消息内**先艾特发起命令的用户，再附图片。让用户在群聊里更容易找到自己的地图回复。

## Requirements

- 在 `nextbot/plugins/player_query.py` 的 `handle_my_map` 中：
  - OneBot V11 分支：构造 `at = OBV11MessageSegment.at(int(user_id))`，将 `at + image` 拼成一条消息发送，**不再拆成两条**。
  - 其他 adapter 分支（fallback 文本回复）保持不变。
- 复用 `自踢` / 其他命令已有的 at 模式（line 240：`at = OBV11MessageSegment.at(int(user_id))`），保证风格一致。

## Non-goals

- 不改其他截图命令（背包、进度等）的发送逻辑
- 不改地图 API 调用 / base64 解码 / 临时文件落盘逻辑
- 不动权限或参数

## Acceptance Criteria

- [ ] OneBot V11 群聊执行 `我的地图 <ID>` 后，机器人回复消息同时包含 @用户 和地图图片，作为一条消息发送
- [ ] 失败路径（API 报错 / 服务器不存在 / 用户不存在 / 解码失败 / 写文件失败）的回复保持原样（已经是文本回复，不附 at 也可以）
- [ ] 非 OneBot V11 adapter 的 fallback `f"✅ 地图生成成功，文件：{screenshot_path}"` 不变

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再提交
