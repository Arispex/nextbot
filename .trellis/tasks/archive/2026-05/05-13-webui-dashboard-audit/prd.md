# audit: WebUI 仪表盘页面安全 + 性能 + 文案审计

## Goal

对 WebUI 仪表盘（dashboard）页面做系统性审计，三个维度并行：
1. **安全漏洞**（前后端 XSS / 注入 / CSRF / 权限边界）
2. **性能优化空间**（N+1 / 不必要 query / 资源加载 / 同步阻塞）
3. **前端文案优化**（符合 CLAUDE.md "用户操作反馈文案规范"：动作+结果+原因；中英文空格；不含操作对象名等）

子代理分桶查 → 主代理二次审核 → 报告给用户 → 用户决策修复范围。

## Scope

### 后端

- `server/routes/webui_dashboard.py`（25 行，仅 endpoint）
- `server/pages/console_page.py`（246 行，shell + 渲染逻辑）
- `nextbot/stats.py` 的 `get_dashboard_metrics()`（已在 Round 7-9 审过，本轮看 caller 路径）

### 前端

- `server/webui/templates/dashboard_content.html`（104 行）
- `server/webui/static/js/dashboard.js`（166 行）
- `server/webui/static/css/dashboard.css`（407 行，仅关注样式 / 文案相关）
- `server/webui/templates/app_shell_base.html`（dashboard 加载的 shell 部分）

### 关注点

#### 安全
- XSS：模板渲染是否 escape 用户可控数据
- DOM-XSS：JS 读 location.search / innerHTML 风险
- CSRF：是否 POST/DELETE 类调用（dashboard 通常只 GET，需确认）
- 权限：dashboard 数据是否泄漏不该看的信息（如完整 user 列表 / 服务器 IP）
- 缓存 / Etag：响应头是否合理（CSP / X-Frame-Options 等可与之前 webui-login 任务的 middleware 协同）

#### 性能
- `get_dashboard_metrics` 单事务多 query（Round 7-9 已知）—— 看是否 caller 有缓存 / 节流
- JS 轮询频率（如有定时刷新）
- 资源加载：JS / CSS / 图标 / 字体；defer / async / preload
- DOM 大小：初次 render 是否大量节点
- 网络请求：fetch 并发 / 串行 / 重复
- 渲染：是否有强制 reflow（read-then-write DOM）

#### 前端文案
- 文案是否符合 CLAUDE.md "用户操作反馈文案规范"：
  - 成功：`动作 + 结果`
  - 失败：`动作 + 结果，原因`
  - 不得包含操作对象名称
  - 动词通用：保存 / 删除 / 创建 / 更新 / 提交 / 上传
- 中英文混排空格规范（CLAUDE.md 第 4 条）
- 数字 / 单位 / 时间格式（北京时间 / 千分位 / Loading / 空态 / 错态文案）
- aria-label / 屏幕阅读器友好度

### 排除项

- 登入页（已在 05-13-webui-login-audit 闭环）
- WebUI 业务页面（users / servers / lottery / shop / groups / warehouse）—— 独立任务
- bot 命令侧（Round 7-9 已闭环）

## Requirements

1. **分桶并行**：2 桶 trellis-research：
   - Backend（webui_dashboard.py / console_page.py / stats.py caller）
   - Frontend（dashboard_content.html / dashboard.js / dashboard.css / 文案）
2. **主代理二次审核**：High / Medium 行号验证，剔除 false positive，沉淀 `verify-pass2.md`
3. **报告口径**：严重度（Critical / High / Medium / Low / Info）+ 文件 + 行号 + 修复前 / 后行为 + 触发概率 / 影响 + 文案对照（前后字符串对比）
4. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] 后端 + 前端两桶覆盖全部目标文件
- [ ] 每条 High / Medium 行号可 Read 验证
- [ ] 文案问题给出"修复前" → "修复后"字符串对照
- [ ] 主代理沉淀 verify-pass2.md
- [ ] 向用户呈现按严重度排序的合并报告

## Out of Scope

- 实施修复（先报告）
- WebUI 其他业务页面
- 设计层重构（如新增可视化图表）

## Technical Notes

- prior art：Round 7-9 累计 37 条修复 + 05-13-webui-login-audit 5 条加固
- CSP / X-Frame-Options 等响应头已由 login-audit 任务的 `add_security_headers_middleware` 注入到 `/webui*` 路径（含 dashboard），本轮不重复挖
- `add_security_headers_middleware` 已设 `script-src 'self' 'unsafe-inline'`，dashboard 内联 script 是否能进一步收紧值得评估
