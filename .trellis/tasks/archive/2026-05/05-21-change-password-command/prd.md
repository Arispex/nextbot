# feat: 新增「修改密码」命令（私聊专用）

## Goal

新增命令 `修改密码 <新密码>` 让已注册用户自助修改 TShock 账号密码。仅在私聊（含好友 / 群临时会话）可用；群里使用提示用户私聊；改完用 sync orchestrator 推到所有服务器。复用现有 `_hash_password` + `trigger_sync_all_servers`，与 WebUI "修改密码" 端点行为对齐。

## Requirements

### R1 — 命令注册

`nextbot/plugins/user_manager.py` 新增：

```python
change_password_matcher = on_command("修改密码")

@change_password_matcher.handle()
@command_control(
    command_key="user.password.change",
    display_name="修改密码",
    permission="user.password.change",
    description="修改当前账号密码（仅私聊可用）",
    usage="修改密码 <新密码>",
    category="用户系统",
)
@require_permission("user.password.change")
async def handle_change_password(bot: Bot, event: Event, arg: Message = CommandArg()) -> None:
    ...
```

### R2 — 私聊门面校验

仅 `message_type == "private"` 时放行；否则回复：
```
❌ 修改失败，请私聊机器人使用此命令
```
（动词通用 + 原因不含对象名）

用 `getattr(event, "message_type", "")` 防御性提取（不引入 OneBot v11 硬依赖）。

### R3 — 参数 / 密码强度校验

- 必须恰好 1 个 arg（新密码）；否则 `raise_command_usage()`（NoneBot 会回 usage 文案）。
- 密码 strip 后非空且 ≥ 8 字符；否则：
  - 空：`❌ 修改失败，密码不能为空`
  - 太短：`❌ 修改失败，密码长度至少 8 位`

校验函数内联在 handler 即可，不必抽 helper（与 webui_users `_normalize_password` 各自维护，避免跨层耦合）。

### R4 — 注册校验

读 user：未注册 → `❌ 修改失败，请先注册账号`（与"注册账号"命令的失败文案风格一致）。

### R5 — 写 DB + 触发 sync

```python
password_hash = _hash_password(plaintext)  # 复用现有 helper
# UPDATE user SET password_hash = :h WHERE user_id = :uid
# session commit / close

# Defense-in-depth：清掉栈上明文（与 handle_add_whitelist 一致风格）
plaintext = None

outcomes = await trigger_sync_all_servers(caller="change_password_command")
sync_text = format_sync_outcomes_for_user(outcomes)

# 成功回复
await bot.send(event, reply_block(
    reply_success("修改"),
    [sync_text]
))
```

注意：
- 在私聊场景 `safe_at_segment_or_empty(user_id)` 返回空字符串（OneBot 私聊不需要 @），实际不需要 at 前缀；按现有 user_manager 命令风格 reply 即可（如有现成 helper 直接复用）。
- 不打印密码到日志，沿用 `_mask_user_id` 风格记录 user_id。
- 关键日志：`修改密码成功：user_id=<masked> name=<name>`（不含密码）

### R6 — 权限 key 注册

`nextbot/db.py` `DEFAULT_GUEST_PERMISSIONS` 添加 `"user.password.change"`（默认 guest 允许自己改自己密码，与 `user.register` 同级）。**不**加到 `DANGEROUS_PERMISSION_PREFIXES`（用户改自己密码不算危险操作）。

## Acceptance Criteria

- [ ] 群里发 `修改密码 newpass123` → 收到 `❌ 修改失败，请私聊机器人使用此命令`，DB 未改。
- [ ] 私聊未注册用户发 `修改密码 newpass123` → 收到 `❌ 修改失败，请先注册账号`。
- [ ] 私聊已注册用户发 `修改密码 short` → 收到 `❌ 修改失败，密码长度至少 8 位`，DB 未改。
- [ ] 私聊已注册用户发 `修改密码 ""`（空）→ 收到 `❌ 修改失败，密码不能为空`。
- [ ] 私聊已注册用户发 `修改密码 abcdefgh` → 收到 `✅ 修改成功\n同步服务器结果：\n1.<name>：同步成功\n...`；DB 中 password_hash 已更新；用新密码在服务器能 `/login`。
- [ ] 群临时会话发 `修改密码 ...` → 走私聊分支（`message_type=private`），与好友私聊行为一致。
- [ ] Console 日志含 `修改密码成功：user_id=<masked> name=<name>`，**不含密码本身**。
- [ ] WebUI "命令" 页能看到 `修改密码` 命令（命令注册到 DB），并能正确显示 usage / category。
- [ ] WebUI "权限组" 页 guest 默认权限里含 `user.password.change`（自动 seeding）。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 合规性）。
- 不破坏现有 `handle_add_whitelist` / `handle_rename` 等命令端逻辑。
- 失败文案严格 CLAUDE.md：动词 "修改" + 原因不含对象名 + 原因可读。
- 不触碰 WebUI 改密 endpoint（路径独立，命令端 / WebUI 都走 sync orchestrator，行为一致）。

## Out of Scope

- 不要求旧密码验证（管理员级操作；用户私聊机器人本身的 QQ ID 已可信）。
- 不支持随机密码生成（用户必须提供新密码）。
- 不修改 WebUI 改密 endpoint。
- 不改 sync orchestrator / `_hash_password` / 其它密码 helper。

## Technical Notes

- 现有 helper：
  - `nextbot/plugins/user_manager.py:93 _hash_password(plaintext) -> str`
  - `nextbot/plugins/user_manager.py:_mask_user_id(user_id) -> str`
  - `nextbot/sync_orchestrator.py:trigger_sync_all_servers(caller) -> list[SyncOutcome]`
  - `nextbot/sync_orchestrator.py:format_sync_outcomes_for_user(outcomes) -> str`
- 现有命令注册范式参考：`handle_add_whitelist`（同文件）
- 文案 helper：`reply_failure / reply_success / reply_block / EMOJI_USER`（`nextbot/text_utils.py`）
- DB schema：`User.password_hash: Optional[str]` 已存在
- 权限注册：`nextbot/db.py:35 DEFAULT_GUEST_PERMISSIONS`
