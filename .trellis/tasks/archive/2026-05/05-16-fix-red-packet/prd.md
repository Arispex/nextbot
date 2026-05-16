# fix(red_packet): 我的红包 截图加 @ 调用者

## 改动

`nextbot/plugins/red_packet.py`：

1. `_send_red_packet_image`（line 546-561）函数签名加可选 `at_user_id: str | None = None`，透传到 `render_and_send_screenshot`：
```python
async def _send_red_packet_image(
    bot: Bot,
    event: Event,
    *,
    page_url: str,
    file_prefix: str,
    at_user_id: str | None = None,
) -> None:
    await render_and_send_screenshot(
        bot, event,
        page_url=page_url,
        options=_RED_PACKET_SCREENSHOT_OPTIONS,
        file_prefix=file_prefix,
        semaphore=_red_packet_screenshot_semaphore,
        failure_action="查询",
        at_user_id=at_user_id,
    )
```

2. `handle_list_own`（约 line 657）调用处追加 `at_user_id=user_id`：
```python
await _send_red_packet_image(
    bot, event, page_url=page_url, file_prefix="red-packet-own",
    at_user_id=user_id,
)
```

`handle_list_all`（红包列表）调用方**不传** at_user_id（保持原行为；用户只要求"我的红包"）。

## Scope

仅 `nextbot/plugins/red_packet.py`。

## Acceptance

- "我的红包" 命令返回图片为 `@玩家 [图片]` V11 一条消息
- "红包列表" 行为不变（仍是单段图片，无 @）
- `python3 -m py_compile` 通过

## DO NOT

- 不动 page / 模板 / web_server / render route
- 不动 红包列表 命令的截图调用
- 不 commit
