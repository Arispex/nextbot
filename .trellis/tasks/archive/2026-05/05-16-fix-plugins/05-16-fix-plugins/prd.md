# fix(plugins): 全量补齐命令失败回复的 @ 调用者前缀

## Goal

全量扫 `nextbot/plugins/*.py` 所有命令，找出"失败 / 拒绝 / 校验失败"回复**没带 @ 调用者**的位点，统一加 `safe_at_segment_or_empty(event.get_user_id())` 前缀。

用户例：`我的红包 -1` 当前回 `❌ 查询失败，页数必须为正整数`，应改为 `@用户 ❌ 查询失败，页数必须为正整数`。

## 审计范围

`nextbot/plugins/*.py` 全部 plugin 文件（约 23 个）。聚焦：
- `bot.send(event, reply_failure(...))` 调用前是否有 `at + " "` 前缀
- `bot.send(event, reply_warning(...))` 同样
- `bot.send(event, ...)` 含 `❌` / `⚠️` 文字开头的也算

## 排除

- **截图命令的失败兜底**：已经走 `render_and_send_screenshot` 的 `failure_action` 路径（reply_failure 在 helper 内部发送），由调用方在 caller 加 `at_user_id` 解决；不在本任务的"前缀"范畴
- **群消息广播 / 通知类**：不针对特定调用者的失败通知（如启动失败、跨群通知），无 @
- **成功 reply（reply_success / reply_block）**：仅审失败 path
- **assertion / raise** 类（让 NoneBot 自己处理 raise_command_usage 等）：由框架统一处理，无 @ 是正常

## 流程

1. **Phase A — Research**：派 `trellis-research` 全量扫 `nextbot/plugins/*.py`，输出每个文件的失败 send 位点 + 当前是否有 @ + 修复建议。报告路径 `.trellis/tasks/05-16-fix-plugins/research/audit.md`
2. **Phase B — Fix**：派 `trellis-implement` 按 audit 报告批量加 @
3. **Phase C — Re-audit**（轻）：派 research 验证 0 遗漏

## Acceptance

- 所有 plugin 命令的失败回复都带 @ 调用者前缀
- 现有 @ 用法保持不变
- 不破坏成功 reply / 截图 caller
- 失败 path 中那些 not user-facing（如 logger）不动

## DO NOT

- 不动 reply_failure / reply_warning helper 本身
- 不动 text_utils / safe_at_segment 实现
- 不动后端 / WebUI
- 不 commit 在 research 阶段；fix 阶段才 commit

## Technical Notes

- 统一前缀形态：`at = safe_at_segment_or_empty(event.get_user_id())` → `at + " " + reply_failure(...)`
- 若 handler 已有 `at` 局部变量，直接复用
- `safe_at_segment_or_empty` 已在 `nextbot.text_utils` 暴露
