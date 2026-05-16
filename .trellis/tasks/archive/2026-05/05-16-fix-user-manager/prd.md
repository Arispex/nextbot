# fix(user_manager): 我的信息 / 用户信息 失败路径全部加 @ 调用者

## 改动

`nextbot/plugins/user_manager.py` 两个 handler 的 5 处失败路径补 `at + " "` 前缀（沿用 `safe_at_segment_or_empty` 形态，与注册命令一致）。

### `handle_user_info`（约 line 386-424）
- line 401 `用户名称不存在`
- line 404 `用户名称不唯一，请使用用户 QQ 或 @用户`
- line 407 `用户参数解析失败`
- line 415 `用户不存在`

### `handle_self_info`（约 line 437-459）
- line 450 `未注册账号`

### 模式

每处把
```python
await bot.send(event, reply_failure("查询", "..."))
```
改为
```python
at = safe_at_segment_or_empty(event.get_user_id())
await bot.send(event, at + " " + reply_failure("查询", "..."))
```

两个 handler 顶部统一取一次 `at = safe_at_segment_or_empty(event.get_user_id())` 复用。

`safe_at_segment_or_empty` 在该文件已 import（用于 `handle_add_user` / `handle_sync_whitelist`），无需新增 import。

## Scope

仅 `nextbot/plugins/user_manager.py`。

## Acceptance

- "我的信息" 未注册账号 → `@调用者 ❌ 查询失败，未注册账号`
- "用户信息" 4 个失败分支都带 `@调用者` 前缀
- 截图成功路径不变（已 commit 32e90a0 加了 at_user_id）
- 其它命令（注册账号 / 同步白名单）不动

## DO NOT

- 不改 `_render_and_send_user_info` helper
- 不改 user_info_page / 模板
- 不动其它 plugin
- 不 commit
