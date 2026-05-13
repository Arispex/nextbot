# audit: WebUI 登入页面前后端安全 + 性能审计

## Goal

对 WebUI 登入页面（用户在 bot 内发"申请登录"命令→拿一次性 code→打开 webui 输入 code→拿 cookie 进入主页）的**前端 + 后端 + session / cookie 机制**做系统性安全 + 性能审计。

定位审计真正的安全漏洞 / 性能瓶颈，而非纯防御加固。子代理分桶查 → 主代理二次审核 → 报告给用户 → 用户决策修复范围。

## Scope

### 后端（关键路径）

- `server/routes/webui_login_requests.py`（210 行）—— 一次性 code 生命周期 / 验证逻辑
- `server/routes/webui.py:54-241`（session cookie 构建 / 校验 / middleware / `/webui/api/session` POST/DELETE 端点）—— session secret + cookie 设计
- `server/web_server.py`（仅 WEBUI_SESSION / cookie 相关部分）—— 环境配置 / 启动期 secret 生成
- bot 侧"申请登录"命令 handler（如果存在；grep 定位）

### 前端

- `server/webui/templates/login.html`（363 行）—— 表单 / fetch 调用 / 错误处理 / 自动跳转
- `server/webui/static/js/webui.js`（与 login 相关部分）—— 401 处理 / 跳转 / CSRF 防御

### 关注点（按优先级）

1. **session cookie 设计**：HMAC？签名？secret 来源（hard-coded / env / 启动生成）？过期？HttpOnly / Secure / SameSite flag
2. **一次性 code 生命周期**：生成熵 / TTL / 撞码概率 / 一次性写入失败回滚 / 兜底清理
3. **登录 brute force / rate limit**：是否有？限制是 IP-based 还是 user-based？
4. **CSRF / replay attack**：POST `/webui/api/session` 是否需要 CSRF token？code 是否一次性消费？
5. **timing attack**：code 校验是否走 `hmac.compare_digest`？
6. **error message 信息泄漏**：错误响应是否区分 "code 无效" vs "code 已过期" vs "已使用"？
7. **日志 PII 泄漏**：code 是否进日志？user_id 进日志？
8. **frontend XSS**：login.html 是否 inline render 用户输入？error message 是否 escape？
9. **性能**：登录路径是否有 N+1 / 不必要 DB query / 无 cap 重试 / 同步阻塞 await

### 排除项

- WebUI 主页 / 业务页面的安全（webui_users / webui_groups 等，**用户已多次声明独立任务**）
- bot 命令业务逻辑（已 Round 7-9 闭环）

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端 / cookie），产物落 `research/{backend,frontend}.md`
2. **主代理二次审核**：每条 High / Medium 用 Read + 行号验证，剔除 false positive，沉淀 `verify-pass2.md`
3. **报告口径**：严重度 + 文件 + 行号 + 修复前 / 后行为 + 触发概率 / 影响
4. **本轮先报告**：实施由用户决定

## Acceptance Criteria

- [ ] 后端 + 前端两桶全部覆盖
- [ ] 关键文件（webui_login_requests / webui.py session 段 / login.html）每条 finding 行号可 Read 验证
- [ ] 主代理二次审核沉淀 verify-pass2.md
- [ ] 向用户呈现按严重度排序的合并报告 + 修复前后效果

## Out of Scope

- 实施修复（先报告，user 决定）
- WebUI 主页 / 业务页面审计（独立 task）

## Technical Notes

- 子代理可读 `nextbot/permissions.py` / `nextbot/audit.py` 作 prior art（已 Round 7-9 闭环）
- 关注 webui_login_requests 与 bot 命令 handler 的交互（"申请登录"命令在哪里？需 grep 定位）
- session secret 在哪里？env / runtime / 持久化？是否每次启动变化（让所有 session 失效）
