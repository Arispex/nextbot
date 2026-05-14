# audit: WebUI 服务器管理页面安全 + 性能 + 文案审计

## Goal

对 WebUI **服务器管理**（`/webui/servers`）页面做系统性审计：
1. 安全漏洞（前后端 XSS / 注入 / CSRF / 权限边界 / TShock token 泄漏）
2. 性能优化空间（N+1 / 不必要 query / 资源加载 / 同步阻塞）
3. 前端文案优化（CLAUDE.md 用户反馈规范 + 中英文空格）

子代理分桶查 → 主代理二次审核 → 报告"修复前 → 修复后"对比 → 用户决策。

## Scope（严格限定，禁止扩散到其他页面 / 共享文件）

### 后端
- `server/routes/webui_servers.py`（469 行）—— 服务器管理 endpoints

### 前端
- `server/webui/templates/servers_content.html`（162 行）
- `server/webui/static/js/servers.js`（1152 行，**最大文件，重点**）
- `server/webui/static/css/servers.css`（529 行）

### 关联但不审（**禁止扩散，scope-creep 警告**）

- `server/routes/webui.py` / `server/routes/__init__.py`（基础设施，login/dashboard/401vs302 已审）
- `server/webui/static/js/api.js` / `webui.js`（共享 JS，dashboard R1+R2 / 401vs302 已审）
- `server/pages/console_page.py`（dashboard-audit A1 已审）
- `nextbot/tshock_api.py` / `nextbot/db.py` / `nextbot/server_validation.py` / 其他基础设施（Round 7-9 已审）
- 其他 webui 模块（dashboard / users / lottery / shop / groups / warehouse / settings / commands）
- 任何 plugin / nextbot 命令处理层

## 关注点

### 安全
- 模板 / JS 是否 escape server-injected 数据
- `innerHTML` vs `textContent` 使用
- DOM-XSS（读 location.search / hash 后注入）
- CSRF（POST / PUT / DELETE / PATCH 端点）
- **TShock token 泄漏**（servers list 响应是否暴露 token，editor 表单是否回填 token）
- SQL 注入边界（webui_servers.py 调 DB）
- **CORS / referer 校验**：测试连接 / 验证插件等端点是否被滥用

### 性能
- 服务器列表是否分页 / 虚拟滚动（实际服务器数通常 < 10，但需确认）
- fetch 并发 / 串行 / 重复
- **后端**：`/test` / `/verify-nextbot` 等 RPC 调用 timeout / 并发
- DOM 大小、JS / CSS size + count、defer / async
- restart endpoint（如有）阻塞行为

### 文案（按 CLAUDE.md 规范）
- 操作反馈：`动作 + 结果`（成功）/ `动作 + 结果，原因`（失败）
- **不得包含操作对象名称**（如 "保存服务器" → "保存"；"删除服务器成功" → "删除成功"）
- 失败原因**原样透传** API `error.message`
- 中英文混排空格
- a11y 文案（aria-label / role）

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端）
2. **主代理二次审核**：High / Medium 行号验证 + 文案 finding 给"修复前 → 修复后"字符串对比
3. **报告口径**：严重度 + 文件 + 行号 + 修复前 / 后 + 触发概率 / 影响
4. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] 4 个目标文件全部覆盖
- [ ] 每条 finding 行号可 Read 验证
- [ ] **文案 finding 必须给字符串前后对比**
- [ ] 主代理沉淀 verify-pass2.md
- [ ] **严格 scope-creep 检查**：不扩散到其他模块（dashboard R3 教训）

## Out of Scope

- 实施修复（先报告）
- 任何 servers 页面以外的代码
- prior art：Round 7-9 / login-audit / dashboard R1+R2 / 401vs302 / commands R1+R2 已落地的修复

## Technical Notes

- **scope-creep 警告**：之前 dashboard R3 踩过坑，本任务严格只看 servers 4 个文件
- 已知 R2 commands audit 修过 `webui_servers.py:435` `verify-nextbot timeout 15→10` —— 本次复审需要关注此点是否仍 OK
- servers 页面 RPC 多（test / verify-nextbot / plugin-config 等），是 webui 中**安全 / 性能/ 文案最复杂**的页面之一
- TShock token 在 servers 表里是敏感字段，重点关注 list 响应 / editor 回填 / 日志泄漏
