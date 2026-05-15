# fix(webui): settings 保存后重启探活把 401 视为已恢复 + toast 提示正在重启

## Bug

设置页保存配置后，新进程实际已起来，但因为 .env 改动导致 webui token / session 失效，poll `/webui/api/settings` 拿到的是 401（auth-401-vs-302 已规定 API 在未登录时返回 401）。

当前实现：
- `server/webui/static/js/settings.js:485` `probeRestartReady` 只在 `response.status === 200` 时认为恢复
- `:477` 注释也明确说 "401 视为未恢复"

结果：poll 在整个 7.5s 窗口内只能拿到 401 / 网络错误，永远不会命中 200 → 走 "重启超时，请手动刷新页面" 分支 → 用户手动刷新才能跳到登录页。

期望：401 也是"新进程已上线"的信号，应立即 reload；reload 后浏览器请求 HTML 路由会被 auth 中间件 302 到登录页，达到"自动跳登录"的体感。

另外用户反馈："保存成功" 文案缺少"正在重启"的暗示，最好让用户知道接下来会发生什么。

## 修复

`server/webui/static/js/settings.js`：

1. **`probeRestartReady`（line 477-489）**：把 `response.status === 200` 改为 `response.status === 200 || response.status === 401`；更新 comment。
2. **`saveSettings`（line 552 / 566）**：
   - 把 toast 从 `"保存成功"` 改为 `"保存成功，正在重启…"`（虽然 audit M-4 提到去尾巴，但这里"正在重启"是状态指示器不是动作对象名，与命令页重启按钮 toast 模式一致；可接受偏离）
   - 或保持 success toast 简短，在进 poll 阶段再 setStatus `"正在重启，等待服务恢复…"`（更清晰的状态分阶段）
   - **推荐方案 B**：成功 toast 显示 "保存成功"，进 poll 时立即 `setStatus("正在重启，等待服务恢复…", "info")`，这样 timeline 上有清晰的两段反馈

## Scope

仅 `server/webui/static/js/settings.js` — `probeRestartReady` + `saveSettings` poll 段。

## Acceptance

- 改 token 等关键字段保存后：成功 toast → "正在重启..." toast → poll 命中 401 → reload → 跳登录页
- 非关键字段保存（session 仍有效）：成功 toast → "正在重启..." toast → poll 命中 200 → reload → 页面回来
- 超时分支保留（连续 7.5s 都拿不到 200/401/error 是真异常）

## Out of Scope

- 不动后端
- 不动 health endpoint
- 不动 poll 间隔 / 次数

## Technical Notes

- prior art：auth 中间件 401 vs 302 拆分（`05-14-webui-auth-401-vs-302`）
- audit fix：M-4 改 toast 文案为 "保存成功"（webui-6 settings.md）— 本任务在保持 success toast 简短前提下，用 info 阶段告知 "正在重启"
