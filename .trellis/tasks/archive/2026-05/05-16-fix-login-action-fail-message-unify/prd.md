# 允许登入 / 拒绝登入 全失败统一返回 "没有待处理的登入请求"

## Goal

把 `允许登入` / `拒绝登入` 命令的 **全失败** 分支也简化：不论真实原因，统一返回 `允许失败，没有待处理的登入请求` / `拒绝失败，没有待处理的登入请求`。

## Background

上一个 task（commit `7d9b84a`）把成功分支合并成 "至少一台成功即返回成功"。但全失败分支还有 3 段逻辑：

| 当前 | 文案 |
|---|---|
| `_all_no_pending` 全部 No pending | `允许失败，没有待处理的登入请求` |
| 单台失败 | `允许失败，<API 真实原因>` |
| 多台失败（混合） | `❌ 允许失败，全部 N 台服务器失败\n1.xxx：❌ ...\n2.xxx：❌ ...` |

用户报错：
```
❌ 允许失败，全部 2 台服务器失败
1.本地123：❌ No pending login request found for user 'qianyi'.
2.本次：❌ Not authorized. The specified API endpoint requires a token, but the provided token was not valid.
```

用户原话："失败的话跟之前一样返回'允许失败，没有待处理的登入请求'"

确认了 scope（用户选择 A）：**全失败都返回 "没有待处理的登入请求"，包括单服务器**。

## Decision (ADR-lite)

**Context**：登入命令的失败原因（除 No pending 外）大多是运维侧问题（token 配置、网络），用户看不懂也没法处理。
**Decision**：全失败分支统一为 `reply_failure(action, "没有待处理的登入请求")`，删除所有 per-server 明细 / 真实原因 / `_all_no_pending` 分支。
**Consequences**：
- ✅ 用户体验：单一文案，无技术细节
- ✅ 与上一个任务的 "至少一台成功即成功" 形成对称：成功 / 失败 都是单行简洁文案
- ⚠️ Trade-off：真实失败原因（如 token 配置）从用户消息消失。运维需要从审计日志（`_log_results` L99-107）追查
- ✅ 审计日志 per-server 详情仍保留

## Scope

`nextbot/plugins/security.py`：

### 修改 1：collapse 全失败分支

**修改前**（L144-161）：
```python
    # 全失败
    if _all_no_pending(outcomes):
        await bot.send(
            event,
            at + " " + reply_failure(action, "没有待处理的登入请求"),
        )
        return

    failure_lines = _format_failure_lines(outcomes)
    if len(failure_lines) == 1:
        # 单台服务器场景，沿用原 reply_failure 单行格式
        only_reason = next(o.detail for o in outcomes if not o.ok)
        await bot.send(event, at + " " + reply_failure(action, only_reason))
        return

    head = reply_failure(action, f"全部 {total} 台服务器失败")
    body = reply_block(head, failure_lines)
    await bot.send(event, at + "\n" + body)
```

**修改后**：
```python
    # 全失败：统一返回"没有待处理的登入请求"，不暴露 per-server 技术原因。
    # 真实失败原因仍记录在审计日志（_log_results）供运维追查。
    await bot.send(
        event,
        at + " " + reply_failure(action, "没有待处理的登入请求"),
    )
```

### 修改 2：删除 dead code

删除以下 helper 和 import（grep 验证仅在被删除的全失败分支使用）：

- L26 `_NO_PENDING_MARK = "No pending login request"`
- L73-79 `def _format_failure_lines(...)`
- L82-87 `def _all_no_pending(...)`
- L15 import `reply_block`（保留 `reply_failure` / `reply_success` / `safe_at_segment_or_empty`）

`total` 变量（L135 `success_count, total = aggregate(outcomes)`）改为 `success_count, _ = aggregate(outcomes)` 或保持原样（仍传给 `_log_results`，所以保留 `total` 实际不影响）。

→ 实际 `_log_results` 用的是 `outcomes`（自己取 length），不需要 `total`。改成 `success_count, _ = aggregate(outcomes)` 更干净。

## Out of Scope

- 不改成功分支（已经是 `success_count > 0 → reply_success`，无需动）
- 不改 `_broadcast_login_action` / `_load_self_and_servers` / `_log_results` / `BroadcastOutcome` / `aggregate`
- 不改其他命令

## Acceptance Criteria

- [ ] `_handle_login_action` 全失败分支为单一 `reply_failure(action, "没有待处理的登入请求")`
- [ ] 删除 `_NO_PENDING_MARK` / `_format_failure_lines` / `_all_no_pending`
- [ ] 删除 `reply_block` import
- [ ] `total` 变量改为 `_`
- [ ] `python3 -m py_compile nextbot/plugins/security.py` 通过
- [ ] `grep -nE "(_all_no_pending|_format_failure_lines|reply_block|_NO_PENDING_MARK|部分成功|全部.*台服务器失败)" nextbot/plugins/security.py` 无输出
- [ ] 审计日志 `_log_results` 调用未变
- [ ] 两个 decorator (`允许登入` / `拒绝登入`) 仍接 `_handle_login_action`
- [ ] 人工验证：2 台服务器，发"允许登入"且都失败，返回 `允许失败，没有待处理的登入请求`

## Technical Notes

- 与上一 task `05-16-fix-login-action-partial-success`（commit `7d9b84a`）形成对称，二者一起把命令的对外消息收敛成"成功"或"没有待处理的登入请求"两个文案
- `aggregate(outcomes)` 仍调用（虽然 `total` 不再用于消息，但 `success_count` 用于 if 判断）
