# 允许登入 / 拒绝登入 多服务器场景至少一台成功即返回成功

## Goal

把 `允许登入` / `拒绝登入` 命令的多服务器结果展示改为：**只要至少一台服务器成功，就返回简单的成功消息，不展示每台服务器的明细**。

## Background

当前 `_handle_login_action`（`nextbot/plugins/security.py:138-149`）三态展示：

| 状态 | 当前文案 |
|---|---|
| 全成功 | `允许成功，可在 5 分钟内重新连接` |
| 部分成功 | `⚠️ 允许部分成功（1/2）\n2.测试2：❌ Not authorized.` |
| 全失败 | `允许失败，<原因>` 或 `允许失败，全部 N 台服务器失败\n<明细>` |

实际典型场景：
- 用户只在 **一台服务器** 上发起登入（TShock 登入是 per-server 状态）
- 其他台返回 `No pending login request`（`_NO_PENDING_MARK`）
- 但当前被当作"部分失败"展示 → 噪音 + 用户困惑

用户原话："我希望只要一个服务器成功就返回成功的消息，而不是这样返回每一个服务器的结果"

## Decision (ADR-lite)

**Context**：登入命令的"部分成功"是误判 —— 其他台没 pending 是预期状态，不是 bug。
**Decision**：把"全成功"和"部分成功"两个分支合并为"至少一台成功"，统一返回简单成功文案。
**Consequences**：
- ✅ 用户体验：消息简洁，符合"动作 + 结果"规范
- ✅ 减少误解：用户不再看到不相关服务器的"失败"
- ⚠️ Trade-off：小概率边界 case（一台 success + 一台真实超时 / 连接失败），用户看不到失败明细。但审计日志（`_log_results` L99-107）仍记录 per-server 结果，运维可追溯
- ✅ 全失败分支保持不变（用户确实需要知道"为什么没成功"）

## Scope

仅 `nextbot/plugins/security.py` `_handle_login_action`：

### 修改点

**修改前**（L138-149）：
```python
# SA-1.7：完全成功 / 部分成功 / 全失败 三态区分
if success_count == total:
    await bot.send(event, at + " " + reply_success(action, success_detail))
    return

if success_count > 0:
    # 部分成功：⚠️ 头 + 失败明细，让用户知道哪几台 OK 哪几台未 OK
    head = f"⚠️ {action}部分成功（{success_count}/{total}）"
    failure_lines = _format_failure_lines(outcomes)
    body = reply_block(head, failure_lines)
    await bot.send(event, at + "\n" + body)
    return
```

**修改后**：
```python
# SA-1.7 + UX：至少一台服务器成功即视为成功；其他台多半是 No pending login，
# 是预期状态，不展示明细。审计日志仍记录 per-server 结果，运维可追溯。
if success_count > 0:
    await bot.send(event, at + " " + reply_success(action, success_detail))
    return
```

L150+ 全失败分支不变。

## Out of Scope

- 不改全失败分支（`_all_no_pending` / 单台 `reply_failure` / 多台 `reply_block`）
- 不改 `_log_results` 审计日志（仍记录 per-server 详细结果）
- 不删 `_format_failure_lines` helper（全失败分支仍在用）
- 不改 `_broadcast_login_action` / `_load_self_and_servers` / `BroadcastOutcome` / `aggregate`
- 不改其他命令（仅登入相关）

## Acceptance Criteria

- [ ] `_handle_login_action` 中 `success_count > 0` → 单一成功文案分支
- [ ] 删除"⚠️ 部分成功"代码路径
- [ ] 全失败分支（`success_count == 0`）逻辑完全保留
- [ ] 审计日志 `_log_results` 不变
- [ ] `_format_failure_lines` helper 仍存在（全失败分支用）
- [ ] `python3 -m py_compile nextbot/plugins/security.py` 通过
- [ ] 人工验证：2 台服务器，发"允许登入"，应返回 `允许成功，可在 5 分钟内重新连接`，不再有部分成功明细

## Technical Notes

- `reply_success(action, success_detail)` 已符合 CLAUDE.md "动作 + 结果" 规范
- 同一个 `_handle_login_action` 同时服务 `允许登入` 和 `拒绝登入`，一处修改两个命令受益
- SA-1.7 注释需更新（三态 → 二态）
