# fix(plugins): 注册账号文案优化（已注册更直白 + 成功加白名单提示）

## 改动

仅 `nextbot/plugins/user_manager.py` 内 `handle_add_user`（`注册账号` 命令）的两条文案。

### 1) 已注册回复改为直白提示（line 224）

当前：
```python
await bot.send(event, at + " " + reply_failure("注册", "该账号已注册"))
# 输出："@xxx ❌ 注册失败，该账号已注册"
```

改为：
```python
await bot.send(event, at + " " + reply_warning("你已经注册过了，请勿重复注册"))
# 输出："@xxx ⚠️ 你已经注册过了，请勿重复注册"
```

理由：这不是"操作失败"语义，而是"友好提示已存在"。`reply_warning` 已在 `nextbot/text_utils.py:51` 提供。

### 2) 注册成功末尾加白名单提示（line 247-256）

当前 `reply_block` 的 lines 列表：
```python
[
    f"{EMOJI_USER} 用户名称：{name}",
    f"🆔 QQ：{user_id}",
]
```

改为追加一行 hint：
```python
[
    f"{EMOJI_USER} 用户名称：{name}",
    f"🆔 QQ：{user_id}",
    f"{STATUS_HINT} 如果进入服务器提示不在白名单中，群里发送「同步白名单」即可",
]
```

`STATUS_HINT = "💡"` 已在 `nextbot/text_utils.py:16`。

## Scope

仅 `nextbot/plugins/user_manager.py`。

## Acceptance

- 已注册用户再发"注册账号 xxx"，bot 回 `⚠️ 你已经注册过了，请勿重复注册`（不再是 ❌ 注册失败）
- 新注册成功消息末尾多一行 `💡 如果进入服务器提示不在白名单中，群里发送「同步白名单」即可`
- 其它失败分支（名字非法 / 名字被占用 / 数据库冲突）不变
- `python3 -m py_compile` 通过

## DO NOT

- 不动 `_validate_user_name` / 注册主流程
- 不动 `同步白名单` 命令
- 不动 `text_utils.py`
- 不动其它 plugin
- 不动 WebUI users
- 不 commit
