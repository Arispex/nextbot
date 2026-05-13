# audit: WebUI 仪表盘 round-2 复审（dashboard 10 项修复 + 全量再扫）

## Goal

Round 1 dashboard audit（commit `c118d91`）落地 10 项修复 + 3 项排除。Round 2：
1. **复审 Round 1 修复实际正确性**（含 P2 共享 `api.js` AbortSignal.timeout 改造的兼容性）
2. **全量再扫**剩余 finding，特别关注 Round 1 修复周边代码新暴露面
3. **不重复挖**已确认的 3 项排除（C3 / M-1 / M-2）

## Round 1 已修清单（必读避免重复挖）

后端：
- **D3** `webui_dashboard.py:9, 15, 19-23` 异常日志补 `client_ip` + `user_agent`（trunc 200）
- **A1** `console_page.py:47-59` `_render_app_shell_page` 加 docstring 文档化模板信任契约

前端文案：
- **T-1** `dashboard_content.html:13` `刷新数据` → `刷新`
- **T-2** `dashboard_content.html:22` `正在拉取仪表盘数据…` → `加载中…`
- **T-3** `dashboard.js:86` `刷新中...` → `刷新中…`
- **T-4** `dashboard.js:49, 142, 149` 3 处 `--` → `—`
- **T-5** `dashboard_content.html:71` `暂未连接` → `无`

前端 a11y / UX：
- **P2 aria-busy** `dashboard.js:90-91, 100-101` setLoadingState 内 stats-grid + dashboard-panels 加 aria-busy
- **P3 role=alert** `dashboard.js:58, 66` + `dashboard_content.html:18` 动态切换 + aria-atomic
- **P3 Sponsored** `dashboard_content.html:85` → 赞助
- **P3 focus 恢复** `dashboard.js:44, 81-83, 107-115` queueMicrotask 恢复

前端共享改造（影响所有 webui API）：
- **P2 timeout** `api.js:103-167` AbortSignal.timeout(15000) + AbortSignal.any 合并 + feature-detection 兜底

## Round 1 排除项（**绝对不重复挖**）

- **C3** `webui_dashboard.py:22` `message="内部错误"` 保留（用户决策：防泄露的有效安全设计）
- **M-1** dashboard TTL 缓存（用户决策不修）
- **M-2** auth middleware 302→401（独立任务统一修复）

## Scope（与 Round 1 一致）

### 后端
- `server/routes/webui_dashboard.py`
- `server/pages/console_page.py`
- `nextbot/stats.py:get_dashboard_metrics` caller 路径

### 前端
- `server/webui/templates/dashboard_content.html`
- `server/webui/static/js/dashboard.js`
- `server/webui/static/css/dashboard.css`
- `server/webui/static/js/api.js`（**Round 1 P2 timeout 改造重点复审**）
- `server/webui/templates/app_shell_base.html`（dashboard 加载的 shell 部分）

## 关注点

### 复审 Round 1 修复
- **D3** `_client_ip` import 是否引入循环 import / 是否暴露不该暴露的 IP（如 NAT 后内网 IP）
- **A1** docstring 是否覆盖所有 caller 路径
- **5 文案** 字符是否真的是 U+2014 / U+2026 而非 ASCII 替代
- **P2 aria-busy** loading 持续期间是否会有 race（用户连续点击）
- **P2 timeout** `AbortSignal.any` 在 caller 已传 abort 时的行为；TimeoutError 透传给 caller 后是否触发 unhandled rejection
- **P3 role 动态切换** 是否在 a11y 工具下表现正常
- **P3 focus 恢复** `queueMicrotask` 是否会被其他 sync code 抢占

### 全量再扫
- 安全 / 性能 / 文案剩余项
- Round 1 修复周边代码的新暴露面

### 不重复挖
- C3 / M-1 / M-2（用户决策）
- Round 7-9 + login-audit 已闭环的基础设施 / cookie 机制

## Requirements

1. **分桶并行**：2 桶 trellis-research（backend + frontend）
2. **主代理二次审核**：每条 High / Medium 行号验证，剔除 false positive，沉淀 `verify-pass2.md`
3. **报告口径**：严重度 + 文件 + 行号 + 修复前 / 后 + 触发概率 / 影响
4. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] 10 项 Round 1 修复每条 PASS / NEW-ISSUE 判定
- [ ] 2 个子代理产物落 `research/{backend,frontend}.md`
- [ ] 主代理沉淀 `verify-pass2.md`
- [ ] 若 0 Critical / 0 High / 0 Medium 可声明 dashboard 收敛闭环

## Out of Scope

- 实施修复（先报告）
- C3 / M-1 / M-2 三项用户排除项
- Round 7-9 / login-audit 已闭环范围

## Technical Notes

- prior art commit：`c118d91`
- Round 1 任务归档位置：`.trellis/tasks/archive/2026-05/05-13-webui-dashboard-audit/`
- 重点关注 `api.js` P2 timeout 共享改造的回归面：所有 webui 模块（login / users / servers / lottery / shop / groups / warehouse / settings）都会受影响
