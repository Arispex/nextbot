# feat(webui): app_shell 退出登入按钮加确认 dialog

## 需求

`server/webui/templates/app_shell_base.html:219` 的 `#logout-btn` 当前点击后**立即**调 `DELETE /webui/api/session`（`webui.js:188-226`），缺少确认步骤，容易误点。

需在 shell 层加确认 dialog：
- 标题 "退出登录"
- 主文案 "确定退出登录吗？"
- 主按钮 "退出"（danger 样式）
- 次按钮 "取消"
- 关闭按钮 `×`、mask 点击、ESC 都关 modal

## Scope

- `server/webui/templates/app_shell_base.html` — 末尾追加 logout 确认 modal
- `server/webui/static/css/app-shell.css` — 加 `.modal / .modal-mask / .modal-card / .confirm-modal-card / .modal-head / .modal-title / .modal-close-btn / .modal-body / .confirm-modal-body / .confirm-modal-text / .modal-foot / .btn-danger` 共享样式（来源参考 `users.css:244-329` `.btn-danger:232-242`）— 复制即可，目的是让 shell 层 modal 在 dashboard / settings / servers / login 等"自身没定义 .modal CSS"的页面也能正确渲染
- `server/webui/static/js/webui.js` — 重写 `#logout-btn` 点击 handler，先开 modal；wire 确认 / 取消 / 关闭 / mask / ESC；确认才走 DELETE

## 复用既有 token

- `--z-dialog`（app-shell.css 已定义，audit M-5）
- `--shadow-elevated` / `--surface` / `--line` / `--text` 等已有 token
- prefers-reduced-motion 守卫已在 app-shell.css 文末

## 行为细节

- 打开 modal：移除 `hidden`、记录 previousFocus = activeElement、focus 到 "取消" 按钮（避免误确认）、`document.body.style.overflow = "hidden"` 锁滚动
- 关闭 modal：加回 `hidden`、还原 body.style.overflow、focus 回 previousFocus（fallback：`#logout-btn`）
- ESC 仅当 modal 可见时才关；不影响其它 page 的 ESC 处理（其它 page 的 modal-stack 各管各的，shell 层 modal 不挤压它们的 ESC 栈）
- 确认按钮：close modal → 调原有 DELETE 流程（`logoutButton.disabled = true` + apiRequest + 跳 `/webui/login`）
- 失败兜底：保持现有 `window.alert(...)` + `disabled = false` 行为

## Acceptance

- 点击退出 → 弹 modal（不会立即调 API）
- 默认焦点在 "取消"
- ESC / mask / × / 取消 都能关 modal 不调 API
- 确认 → 调 DELETE → 成功跳 `/webui/login`、失败 alert
- modal 在所有页面（dashboard / settings / servers / commands / users / groups / lottery / shop / warehouse / login）视觉一致
- 屏幕阅读器读出 "退出登录 — 确定退出登录吗？— 取消 / 退出"

## DO NOT

- 不动其它 page 的现有 modal（不改 commands.js 等的 modal-stack）
- 不重构 modal helper 到独立模块（共享 modal helper 抽取仍是 backlog）
- 不删旧 logout 失败 alert 兜底
- 不动后端

## Technical Notes

- prior art：commands_content.html:92-109 重启确认 modal 样式 / 结构作参考
- audit 已确认 modal helper 抽 shared lib 是 backlog（webui-6 task）
