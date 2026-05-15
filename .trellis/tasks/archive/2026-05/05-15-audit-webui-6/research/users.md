# 审计报告：WebUI 用户管理页面（users）

- **审计范围**：4 个文件
  - `server/routes/webui_users.py`（748 LOC）
  - `server/webui/templates/users_content.html`（174 LOC）
  - `server/webui/static/js/users.js`（988 LOC）
  - `server/webui/static/css/users.css`（414 LOC）
- **审计日期**：2026-05-15

## 汇总

| 严重度 | 数量 |
|---|---|
| Critical | 0 |
| High | 6 |
| Medium | 11 |
| Low | 8 |

**Top 3**:
1. **H-1** `webui_users.py` 全文件无 client_ip / user_agent 日志 —— 与 servers R2 D-2 / commands R2 M-B3 不一致
2. **H-2** list 端点全表加载 + 内存过滤，`per_page=0` 无上限
3. **H-3** ban / unban 闭包对 user_name / user_qq 的作用域逃逸（罕见路径 UnboundLocalError）

## 1. Security

### H-1 所有写操作日志缺失 client_ip / user_agent
- **File**: `server/routes/webui_users.py:255-748`（八个路由全部缺失）
- **Dimension**: security
- **Issue**: 八个写路由（封禁 L537、解封 L656、删除 L459、改金币 / 身份组 L380、改 PII L313、sync-whitelist L490）全部缺 IP/UA 痕迹。servers R2 D-2 已统一。
- **Fix**: 从 `server.routes.webui` 导入 `_client_ip` 与 `_user_agent`；为 delete / unban / sync-whitelist 三处补 `request: Request`。

### H-2 list 端点 SQL 全表加载 + 内存过滤
- **File**: `server/routes/webui_users.py:255-310`
- **Dimension**: security + perf
- **Issue**: L272 全表 query + L274-287 内存 keyword 过滤。`per_page=0` 跳过 cap。
- **Fix**: 搜索下推 SQL ilike + LIMIT/OFFSET；移除或截断 `per_page=0` 通道。

### H-3 _ban_one / _unban_one user_name 作用域逃逸
- **File**: `server/routes/webui_users.py:570-584, 593-608, 667-680, 690-705`
- **Dimension**: security + 正确性
- **Issue**: `user_name` / `user_qq` 在 try 内赋值，except 块仍引用 → UnboundLocalError；commit 已写库但 broadcast 失败状态不一致。
- **Fix**: 把 user_name / user_qq / reason 在 try 入口最早就读出。

### H-4 sync-whitelist / unban / update / delete 缺 Owner 边界检查
- **File**: `server/routes/webui_users.py:380, 459, 490, 656`
- **Dimension**: security (authz)
- **Issue**: ban 已 403 Owner，unban / update / delete 没有。
- **Fix**: 三处复用 `get_owner_ids()` 检查 → 403。

### H-5 ID 路径参数缺最小值校验
- **File**: `server/routes/webui_users.py:380, 459, 490, 537, 656`
- **Dimension**: security
- **Issue**: 接受 0、负数。servers R2 A-8 已要求 ge=1。
- **Fix**: `user_id: int = Path(..., ge=1)`.

### H-6 keyword 长度无上限
- **File**: `server/routes/webui_users.py:268`
- **Dimension**: security (DoS)
- **Issue**: `q=` 无截断。
- **Fix**: `keyword = keyword[:128]` 或长度校验 400。

### M-1 sync-whitelist / delete / unban 路由签名不接受 Request
- **File**: `server/routes/webui_users.py:460, 491, 657`
- **Dimension**: security
- **Issue**: 缺 request 参数，是 H-1 结构根因。
- **Fix**: 三处统一加 `request: Request`。

### M-2 用户名称 / QQ 进 logger 明文（PII + log injection 弱面）
- **File**: `server/routes/webui_users.py:362, 396, 445, 465, 476, 506, 577, 644, 674, 739`
- **Dimension**: security
- **Issue**: PII 大量进 INFO 日志；`reason` 字段未约束字符集。
- **Fix**: QQ 中间 mask；`reason.replace("\\n", "\\\\n").replace("\\r", "\\\\r")`.

### M-3 _USER_NAME_PATTERN 前后端手抄
- **File**: 后端 `webui_users.py:34`，前端 `users.js:103`
- **Dimension**: security
- **Issue**: 两份正则同步靠手动。
- **Fix**: 加 spec 记录；当前不强求修。

### M-4 sync-whitelist 无限频
- **File**: `webui_users.py:490-534`
- **Dimension**: security
- **Issue**: 单击同步并发 N 个服务器请求；curl 直连不受限。
- **Fix**: 每会话级 5s cooldown。

## 2. Performance

### M-5 无搜索 debounce
- **File**: `users.js:901-904`
- **Dimension**: perf
- **Issue**: 每按键 fire fetch。
- **Fix**: 抄 servers.js 的 `triggerSearchDebounced()`：300ms timer + AbortController。

### M-6 无 AbortController
- **File**: `users.js:478-524`
- **Dimension**: perf
- **Issue**: 翻页快速点击旧请求覆盖新请求。
- **Fix**: `loadUsers({ signal })` 透传。

### M-7 无 beforeunload 清理
- **File**: `users.js`
- **Dimension**: perf
- **Issue**: 离页 fetch 不取消。
- **Fix**: beforeunload listener 调 abortController.abort() + clearTimeout。

### M-8 renderTable 全表重渲染 + 重新挂事件
- **File**: `users.js:304-467`
- **Dimension**: perf
- **Issue**: sync 状态切换走全表重绘。
- **Fix**: sync 状态局部更新（查找该 tr 内 button 改 disabled + textContent）。

### M-9 ban/unban 触发 loadUsers 全表重拉
- **File**: `users.js:818, 873`
- **Dimension**: perf
- **Issue**: API 已返回 `data.user`，前端可直接更新对应一行。
- **Fix**: 用返回 user 更新 `userStates` 单行。

### L-1 syncResultMap "failed" 标志无过期
- **File**: `users.js:867-883, 506-510`
- **Dimension**: perf / UX
- **Fix**: 同步前重置 entry；status=success 后 delete。

### L-2 listener removeEventListener 风格
- **File**: `users.js:305`
- **Dimension**: perf
- **Fix**: 低优，不需立即修。

## 3. UX

### M-10 三个 modal 缺 ESC 关闭
- **File**: `users_content.html:77-174`、`users.js`
- **Dimension**: ux (a11y)
- **Issue**: commands R1 / R2 已落地 modal stack + 单 ESC dispatcher，users 全部缺失。
- **Fix**: 抄 commands.js modal stack + ESC dispatcher。

### M-11 modal 缺 focus trap + previousFocus 恢复
- **File**: `users.js:603-634, 533-542, 580-587`
- **Dimension**: ux (a11y)
- **Fix**: 复用 commands.js 的 `openModalWithFocus` 思路。

### M-12 modal 打开缺 body scroll lock
- **File**: `users.js`
- **Dimension**: ux
- **Fix**: 复用 `document.body.style.overflow = "hidden"` 模式。

### M-13 sync 按钮 disable 状态在重新渲染时丢失
- **File**: `users.js:404-408, 826-831`
- **Dimension**: ux
- **Fix**: loadUsers 完成后 + sync finally 统一更新；状态局部化。

### M-14 进行时文案使用 ASCII 省略号
- **File**: `users.js:567, 756, 784, 832`
- **Dimension**: ux + copy
- **Issue**: 同模块 servers / commands 已统一 `…`。
- **Fix**: `"正在封禁..."` → `"封禁中…"`；同时去对象名。

### L-3 删除 modal 文案带对象名（合规）
- **File**: `users.js:585`
- **Dimension**: copy
- **Note**: 是确认 modal 内容（用户需要知道删谁），合规。

### L-4 多服务器结果 \\n 拼接但 CSS 无 pre-line
- **File**: `users.js:802-817, 845-872`
- **Dimension**: ux
- **Fix**: `.alert .alert-message { white-space: pre-line; }`。

### L-5 「${user.name}」中英混排（合规）
- **Dimension**: copy
- **Note**: 中文标点紧贴名字 OK。

### L-6 modal 无单字段 inline 错误
- **File**: `users.js:636-698`
- **Dimension**: ux
- **Note**: 跨模块 backlog。

### L-7 reload 按钮无 loading 状态
- **File**: `users.js:886-889`
- **Dimension**: ux
- **Fix**: reloadButton.disabled = true / false 配 try/finally + 取消 pending。

## 4. Copy

### L-8 toast "封禁成功，用户 X" 拼对象名
- **File**: `users.js:802`
- **Dimension**: copy
- **Issue**: `actionText + "成功，用户 " + user.name` 违反 CLAUDE.md。
- **Fix**: `var lines = [actionText + "成功"];`。

### L-9 "同步失败，未知错误" 覆盖后端 reason
- **File**: `users.js:862`
- **Dimension**: copy
- **Issue**: 后端 detail 空时前端兜底"未知错误"，违反原样透传。
- **Fix**: detail 空时只显示 `"同步失败"`（不带逗号）。

### L-10 "请输入封禁原因" 走顶部 alert（跨模块）
- **File**: `users.js:560`
- **Note**: backlog。

### L-11 后端 error.message 含字段名
- **File**: `webui_users.py:331, 339, 412, 425`
- **Note**: "用户 QQ 已存在" 是字段语义，合规。

### L-12 422 details shape
- **Note**: 合规。

## Scope-out backlog
- 整套 webui 缺 CSRF / rate-limit / RBAC
- `api.js` / `webui.js` 共享 `cancelPendingFetch` helper
- `_client_ip` 统一到 `webui.py`
- `per_page=0` 在 servers / commands / users 统一禁用
