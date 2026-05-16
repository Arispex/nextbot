# fix(plugins): 命令格式错误回复也加 @ 调用者（command_config 集中入口）

## 改动

`nextbot/command_config.py:1045-1050` 的 `CommandUsageError` 分支：当前 `bot.send(event, _build_usage_message(...))` 没带 @。

改为复用同函数 line 1033-1035 已用的 `safe_at_segment_or_empty(event.get_user_id())` 模式：

```python
except CommandUsageError:
    bot, event = _resolve_bot_event(resolved_signature, args, kwargs)
    if bot is not None and event is not None:
        actual_cmd = _get_raw_command()
        at = safe_at_segment_or_empty(event.get_user_id())
        await bot.send(event, at + " " + _build_usage_message(state.usage, actual_command=actual_cmd))
    return None
```

集中入口一处改完，所有命令的"❌ 格式错误，正确格式：..." 自动带上 `@调用者`。

`safe_at_segment_or_empty` 已在 line 22 imported，无需新增。

## Scope

仅 `nextbot/command_config.py`。

## Acceptance

- 任意命令格式错误（如 `猜数字 -1` 触发 `raise_command_usage`）回 `@调用者 ❌ 格式错误，正确格式：...`
- ban 提示已带 @（不动）
- 其它路径（成功执行 / 命令禁用 / DB 故障 fail-soft）不变
- `python3 -m py_compile nextbot/command_config.py` 通过

## DO NOT

- 不动 `_build_usage_message` / `_resolve_bot_event`
- 不动其它 except 分支
- 不动 plugin 文件
- 不 commit
