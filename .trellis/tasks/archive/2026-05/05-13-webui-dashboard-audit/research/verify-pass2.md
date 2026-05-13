# WebUI 仪表盘审计 — 主代理二次审核日志

## 整体结论

Dashboard 是**整个 WebUI 安全 / 性能基线最干净的页面之一**：
- 0 Critical / 0 High 安全漏洞
- 模板零 `{{ }}` / `__XXX__` 变量插入 + 全 JS 用 `textContent` / `createElement` → 0 XSS 风险
- 单一只读 GET 端点 → CSRF N/A
- 无轮询 / 无 localStorage 敏感写入 / 无 CDN supply chain 风险
- shell 层的 4 个安全响应头已 cover dashboard 响应（login-audit 任务已落地）

主要 finding 集中在**性能（无缓存）+ 文案一致性**。

## Finding 行号验证

### Medium（2 项，全部 CONFIRMED）

| ID | 文件 / 行号 | 验证 |
|---|---|---|
| **B1** 无缓存导致每请求 8 SQL query | `webui_dashboard.py:16` + `stats.py:72-138` | ✅ `metrics = get_dashboard_metrics()` 每次重跑 8 个 count/sum + 1 个 SystemStat first，无 TTL 缓存层 |
| **C5** 未登录 API 被 302 HTML | `webui.py:117-131` | ✅ middleware 路径白名单不区分 `/webui/api/*` vs `/webui/*` HTML，统一 302。属于跨 router 设计问题（Round 7-9 已知） |

### Low（3 项，全部 CONFIRMED）

| ID | 文件 / 行号 | 一句话 |
|---|---|---|
| **C3** error.message "内部错误" 与 CLAUDE.md 规范不匹配 | `webui_dashboard.py:22` | ✅ message="内部错误" 既不是动作也不是有效原因，前端无法生成"动作+结果,原因"展示 |
| **D3** 异常日志缺 client_ip | `webui_dashboard.py:18` | ✅ logger.exception 未带 client_ip / user_agent，与 login-audit M-A4 风格不一致 |
| **A1** `_render_app_shell_page` 内容模板信任假设需文档化 | `console_page.py:48, 80` | ✅ `_load_template(content_template)` 直接读盘塞进 `__MAIN_CONTENT__` 不 escape，依赖"模板内不含 `__XXX__` 用户数据"假设。当前 dashboard_content.html 零变量，安全；建议加注释固化契约 |

### P1 文案一致性（5 项，全部 CONFIRMED）

| ID | 文件 / 行号 | 修复前 | 修复后 |
|---|---|---|---|
| **T-1** | `dashboard_content.html:13` | `<span data-label>刷新数据</span>` | `<span data-label>刷新</span>`（去对象名 "数据" + 与 JS 三态文案对齐） |
| **T-2** | `dashboard_content.html:22` | `<div id="loading" class="empty">正在拉取仪表盘数据…</div>` | `<div id="loading" class="empty">加载中…</div>`（去对象名 + 中性化） |
| **T-3** | `dashboard.js:80` | `setReloadButtonText(loading ? "刷新中..." : "刷新")` | `setReloadButtonText(loading ? "刷新中…" : "刷新")`（ASCII `...` → U+2026 `…`） |
| **T-4** | `dashboard.js:48, 122, 129` | `return "--";` / `\|\| "--"` | `return "—";` / `\|\| "—"`（与模板 `dashboard_content.html:5, 31, 35, 39, 43, 47, 51, 65` 的 U+2014 `—` 一致） |
| **T-5** | `dashboard_content.html:71` vs `dashboard.js:107` | HTML 占位 `"暂未连接"` 与 JS 渲染 `"无"` 不一致 | 二选一统一，推荐 HTML `<span class="tag-badge none">无</span>` + JS 保持 `"无"` |

### P2 / P3（5 项，全部真实但可不修）

| ID | 一句话 | 主代理判 |
|---|---|---|
| P2 `aria-busy="true"` loading 时 | dashboard.js:82-95 缺 | Low / 单按钮页面影响小 |
| P2 fetch AbortSignal.timeout(15s) | api.js 共享改造 | Low / 当前无后端假死 trigger，shell 层改 |
| P3 error toast `role="alert"` | 升级 a11y 优先级 | 当前 role="status" 已能朗读，可不动 |
| P3 `Sponsored` → `赞助` | 风格选择 | 非规范违反 |
| P3 focus 恢复 reloadButton | disabled 后焦点丢失 | 单按钮页面影响小 |

## False positive / 不视为 finding

| 子代理 raise | 主代理 | 理由 |
|---|---|---|
| B2 async + 同步 SQLAlchemy | **不修** | 项目整体模式（Round 7-9 已审），单独改 dashboard 无意义；子代理自标信息性 |
| B4 无 Cache-Control: no-store | **不修** | 默认 fetch 不带 Cache-Control 也不会被代理缓存，理论风险 |
| D5 `_asset_url` 每次 stat 文件 | **不修** | < 1ms 影响，shell 层 polish |
| Shell 层 `<script>` 无 defer | **不修** | 非 dashboard 桶职责 |
| Shell 层 CSP 'unsafe-inline' | **不修** | login-audit 已留过渡空间 |

## 严重度分级（主代理终判）

- **0 Critical / 0 High**
- **2 Medium**（B1 + C5）
- **3 Low**（C3 + D3 + A1）
- **5 P1 文案**（T-1 ~ T-5）
- **5 P2/P3 + ~3 信息性**

## 修复推荐梯队

**性价比优先**（5 P1 文案修起来最快）：
1. **5 条 P1 文案**（~5 行改动）—— 立竿见影 UX 统一，无任何风险
2. **B1 dashboard TTL 缓存**（~10 行）—— SQLite writer 队列释压
3. **C3 + D3**（~5 行）—— message 改"加载失败" + 异常日志补 client_ip

**可选**：
4. C5 auth middleware 区分 API vs HTML（跨 router 改动，影响所有 webui API 端点；建议挂独立 task）
5. A1 console_page.py 文档化（注释，无功能改动）

**Round 9 收敛后维持**：P2 / P3 / 信息性项全归 backlog
