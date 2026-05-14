# audit: WebUI 命令配置页面安全 + 性能 + 文案审计

## Goal

对 WebUI **命令配置**（`/webui/commands`）页面做系统性审计：
1. 安全漏洞（前后端 XSS / 注入 / CSRF / 权限边界）
2. 性能优化空间（N+1 / 不必要 query / 资源加载 / 同步阻塞）
3. 前端文案优化（CLAUDE.md 用户反馈规范 + 中英文空格）

子代理分桶查 → 主代理二次审核 → 报告"修复前 → 修复后"对比 → 用户决策。

## Scope（严格限定，禁止扩散到其他页面 / 共享文件）

### 后端
- `server/routes/webui_commands.py`（188 行）—— 命令配置 endpoints

### 前端
- `server/webui/templates/commands_content.html`（135 行）
- `server/webui/static/js/commands.js`（888 行）
- `server/webui/static/css/commands.css`（463 行）

### 关联但不审（**禁止扩散**）

- `nextbot/command_config.py` —— 基础设施层（Round 7-9 已审）
- `server/webui/static/js/api.js` —— 共享 JS（login-audit / dashboard-audit / 401vs302-audit 已审）
- `server/webui/static/js/webui.js` —— 共享 shell（独立任务）
- `server/pages/console_page.py` —— shell 渲染（dashboard-audit A1 已审）
- 其他 webui 模块（users / servers / dashboard / lottery / shop / groups / warehouse / settings）
- bot 命令处理层（Round 7-9 已闭环）

## 关注点

### 安全
- 模板 / JS 是否 escape server-injected 数据
- `innerHTML` vs `textContent` 使用
- DOM-XSS（读 location.search / hash 后注入）
- CSRF（POST / PUT / DELETE 端点）
- 权限边界（命令配置数据是否泄漏不该看的信息）
- SQL 注入边界（webui_commands.py 调 DB）

### 性能
- 命令列表是否分页 / 虚拟滚动
- fetch 并发 / 串行 / 重复
- DOM 大小（命令多时 render 性能）
- restart endpoint 后端阻塞？
- 资源加载（JS / CSS size + count + defer / async）

### 文案（按 CLAUDE.md 规范）
- 操作反馈：`动作 + 结果`（成功）/ `动作 + 结果，原因`（失败）
- **不得包含操作对象名称**（如 "保存命令" → "保存"）
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
- [ ] **不扩散到 dashboard / users / servers / api.js / shell 等其他范围**

## Out of Scope

- 实施修复（先报告）
- 任何 commands 页面以外的代码
- prior art：Round 7-9 / login-audit / dashboard R1+R2 / 401vs302 已落地的修复

## Technical Notes

- **scope-creep 警告**：dashboard R3 复审踩过坑（误把 cross-bucket finding 拉进 dashboard 报告），本任务严格限制只看 commands 4 个文件
- 已知 R3 误报曾提到 `commands.js:606` "参数保存成功" 文案违规，本任务会重新独立验证（不预设结论）
- shell 共享文件改造（api.js / webui.js）已在 dashboard R1+R2 + 401vs302 中落地，本任务**只看 commands 页面如何用它们**，**不审 shell 自身**
