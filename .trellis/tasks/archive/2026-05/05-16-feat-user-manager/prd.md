# feat(user_manager): 我的信息 / 用户信息 截图加 @ 调用者

## 改动

`nextbot/plugins/user_manager.py` 内 `_render_and_send_user_info` helper（line 328-360）调用 `render_and_send_screenshot(...)` 时追加 kwarg：
```python
at_user_id=event.get_user_id(),
```

这样 V11 路径生成 `@<调用者> [截图]` 一条消息，与 dice 同模式。`event.get_user_id()` 就是发命令的人；无论查的是自己还是别人，都 @ 触发者。

`render_and_send_screenshot` 内部已有 `_sanitize_at_user_id`（commit aba28e6），非数字 user_id 会被 sanitize 为 None，不会破排版。

## Scope

仅 `nextbot/plugins/user_manager.py`。

## Acceptance

- "我的信息" 截图消息以 `@发命令者 [图片]` 形式出现
- "用户信息 X" 截图消息以 `@发命令者 [图片]` 形式出现（@ 触发者，不是被查询的 X）
- 失败路径文案不变
- 其它命令（注册账号 / 同步白名单 等）行为不变

## DO NOT

- 不改 helper 签名
- 不改 user_info_page / 模板
- 不动其它命令
- 不 commit
