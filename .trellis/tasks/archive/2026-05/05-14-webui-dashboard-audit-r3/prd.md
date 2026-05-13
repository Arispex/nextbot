# audit: WebUI 仪表盘 round-3 复审（R2 修复 + 全量再扫）

## Goal

Round 1 (`c118d91`) + Round 2 (`c1a96ca`) 累计 20 项修复后做 Round 3 复审：
1. 验证 R2 10 项修复实际正确性，特别是 3 处 P2 跨模块并行改造（webui_users 3 处 broadcast() 复用）
2. 全量再扫剩余 finding
3. 若 0 Critical / 0 High / 0 Medium，宣告 dashboard + 跨模块溢出彻底闭环

## R1 + R2 已修清单（必读避免重复挖）

### R1（commit c118d91）—— 10 项

后端：D3 (`webui_dashboard.py`) + A1 (`console_page.py` docstring)

前端文案：T-1~T-5（5 处）

前端 a11y / UX：P2 aria-busy / P3 role=alert / P3 Sponsored / P3 focus

前端共享：P2 timeout（首次落地 15s）

### R2（commit c1a96ca）—— 10 项

跨模块 P2：
- R2-T-1/T-2 `webui_users.py` 3 处串行 for → broadcast() 并行
- R2-T-3 `webui_servers.py:434` verify-nextbot timeout 15→10

P3 老浏览器 / restart：
- R2-T-4 `api.js` AbortController + setTimeout 兜底
- R2-T-5 AbortSignal.any 缺失时 userSignal 手动转发
- R2-T-6 apiRequest 接受 timeoutMs；commands.js restart 60000

dashboard 桶 a11y / cleanup：
- R2-B-1 `dashboard_content.html` #loading aria-live
- R2-B-2 删 `.dashboard-section-desc`
- R2-B-3 删冗余 class
- R2-B-4 `dashboard.js` currentReloadController

## 排除项（不动）

- **C3** `webui_dashboard.py:22` `message="内部错误"` 保留
- **M-1** dashboard TTL 缓存
- **M-2** auth middleware 302→401 后续独立任务

## Scope

### 后端
- `server/routes/webui_dashboard.py`
- `server/routes/webui_users.py`（R2 重点改造 3 处）
- `server/routes/webui_servers.py`（R2 timeout 改造）
- `server/pages/console_page.py`

### 前端
- `server/webui/templates/dashboard_content.html`
- `server/webui/static/js/dashboard.js`
- `server/webui/static/js/api.js`（R2 共享改造 + 老浏览器 fallback 重点）
- `server/webui/static/js/commands.js`（R2 restart timeoutMs）
- `server/webui/static/css/dashboard.css`

## 关注点（按优先级）

### R2 修复复审重点
1. **broadcast 复用正确性**：webui_users 3 处 outcome shape 与前端 users.js 消费一致
2. **broadcast import 单向性 + 闭环行为**
3. **api.js 老浏览器 fallback**：setTimeout timer 清理 / unhandled rejection / 多请求 timer 累积
4. **api.js `timeoutMs` per-call override**：边界值（0 / 负数 / NaN）行为
5. **commands.js restart 60s**：60s 是否足够（restart 真实耗时）
6. **dashboard.js currentReloadController**：abort 旧请求时的 race window
7. **dashboard 桶 R2-B-2/B-3 删 class** 是否真的没破坏视觉 / JS 引用

### 全量再扫
- dashboard 安全 / 性能 / 文案剩余项
- R2 改动周边新暴露面（如 broadcast 后写放大 / SQLite 锁压力 / 前端进度反馈）

### 不重复挖
- C3 / M-1 / M-2 用户排除
- R1 + R2 已修 20 项
- Round 7-9 + login-audit 已闭环范围
- shell 层非 dashboard 加载可见的

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端）
2. **主代理二次审核**：每条 High / Medium 行号验证，沉淀 `verify-pass2.md`
3. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] R2 10 项修复每条 PASS / NEW-ISSUE 判定
- [ ] 2 个子代理产物落 `research/{backend,frontend}.md`
- [ ] 主代理沉淀 `verify-pass2.md`
- [ ] 若 0 Critical / 0 High / 0 Medium，可声明 dashboard + 跨模块溢出彻底闭环

## Out of Scope

- 实施修复（先报告）
- C3 / M-1 / M-2 三项排除
- Round 7-9 / login-audit 已闭环范围

## Technical Notes

- prior art：R1 `c118d91` / R2 `c1a96ca`
- R2 任务归档：`.trellis/tasks/archive/2026-05/05-13-webui-dashboard-audit-r2/`
- 重点关注 `api.js` 老浏览器 fallback 的 timer 清理实现细节
