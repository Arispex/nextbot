# fix(message_parser): 命令别名 + @用户 参数解析失败

## Bug

设 `用户背包` 别名 `背包`。
- `用户背包 1 @用户` ✓
- `背包 1 @用户` ✗ "格式错误，正确格式：..."
- `背包 1 <QQ>` ✓

## 根因

`nextbot/message_parser.py:47-52` `_extract_args_text`：
```python
def _extract_args_text(text: str, command_name: str) -> str | None:
    cmd = re.escape(command_name)
    match = re.match(rf"^/?{cmd}(?:\s+|$)", text)
    if match is None:
        return None
    ...
```

只按 canonical `command_name`（`用户背包`）做前缀匹配。当用户用别名（`背包`）时：
1. `_segments_to_plain_text` 把 `背包 1 @user` 还原为 `背包 1 <qq>`
2. `_extract_args_text(text, "用户背包")` 正则匹配 `^/?用户背包` 失败 → 返回 None
3. `parse_command_args` 返回 `[]`
4. `parse_command_args_with_fallback` 走 `arg.extract_plain_text()` fallback
5. `extract_plain_text()` 跳过 at 段 → 只剩 `"1"` → args=["1"]，缺 user 参数
6. plugin `if len(args) != 2: raise_command_usage()` → "格式错误"

QQ 号能 work 是因为 QQ 在 plain text 段中，extract_plain_text 不会丢。

## 修复

让 `_extract_args_text` 同时识别用户实际输入的命令（alias）。NoneBot 在 `matcher.state["_prefix"]["raw_command"]` 存了实际命令名（与 `command_config._get_raw_command` 同源）。

### `nextbot/message_parser.py`

1. 新增 helper `_get_actual_command()` 读取 raw_command：
```python
try:
    from nonebot.matcher import current_matcher
except ImportError:  # 测试/非 nonebot 环境降级
    current_matcher = None


def _get_actual_command() -> str:
    if current_matcher is None:
        return ""
    try:
        matcher = current_matcher.get()
        prefix = matcher.state.get("_prefix", {})
        return str(prefix.get("raw_command", "")).strip()
    except Exception:
        return ""
```

2. `_extract_args_text` 加可选参数 `actual_command`，优先匹配实际命令：
```python
def _extract_args_text(
    text: str,
    command_name: str,
    actual_command: str = "",
) -> str | None:
    candidates = [c for c in (actual_command, command_name) if c]
    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        cmd = re.escape(c)
        match = re.match(rf"^/?{cmd}(?:\s+|$)", text)
        if match is not None:
            return text[match.end():].strip()
    return None
```

3. `parse_command_args` 和 `parse_command_text` 调用时传 `_get_actual_command()`：
```python
def parse_command_args(event: Any, command_name: str) -> list[str]:
    segments = _message_segments_from_event(event)
    if not segments:
        return []
    text = _segments_to_plain_text(segments)
    if not text:
        return []
    actual_cmd = _get_actual_command()
    args_text = _extract_args_text(text, command_name, actual_cmd)
    if args_text is None:
        return []
    ...
```

## Scope

仅 `nextbot/message_parser.py`。

## Acceptance

- `背包 1 @用户` 正常返回 args = ["1", "<qq>"]
- `用户背包 1 @用户` 行为不变
- `背包 1 <QQ>` 行为不变
- 不影响其它命令的 alias 解析

## DO NOT

- 不动 command_config / plugin handler
- 不引外部依赖
- 不 commit
