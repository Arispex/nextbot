# audit: 项目剩余未审计代码全量扫描（安全 / 性能 / 交互 / 文案）

## Goal

之前已对项目几乎所有 plugins / WebUI / nextbot 基础设施做过多轮审计。本任务针对**至今未单独审计或仅做过 theme 清理**的代码做一次系统化审计 + 修复，覆盖安全 / 性能 / UX / 文案 4 维度。约束：**禁止破坏性更新，仅最小硬化**。

## 已轮审收敛（不在本任务）

- WebUI admin 全部 9+5 page + 公共模块（见 `archive/2026-05/05-15-audit-webui-6/` 及之前 dashboard / login / commands / servers / auth-401-vs-302）
- `nextbot/` 基础设施 20 文件（infra-audit r1/r8/r9）
- `nextbot/plugins/` 23 个 plugin + final-sweep + remaining-plugins + post-sweep-verify
- `bot.py`（infra audit 内）

## 本次范围（in-scope）

### Bucket A — server 核心（~1.3K LOC）
- `server/__init__.py` (2)
- `server/web_server.py` (429) — FastAPI 入口、CORS / 中间件 / 启停
- `server/server_config.py` (148) — 配置加载
- `server/page_store.py` (39) — 页面渲染数据缓存
- `server/screenshot.py` (253) — 截图服务
- `server/settings_service.py` (424) — 设置持久化（被 webui-6 settings 标 scope-out 引用过）

### Bucket B — server 路由公共层（~750 LOC）
- `server/routes/__init__.py` (160) — `_client_ip` / `api_success` / `api_error` / `read_pagination_query` / `read_json_object` 等共享 helper
- `server/routes/webui.py` (405) — webui 认证中间件本体（auth-401-vs-302 拆出 401 vs 302，但 webui.py 本体未做完整审计）
- `server/routes/render.py` (180) — render 端点 (`/render/<page>` 截图渲染)

### Bucket C — render 渲染页面后端（17 文件，~3K LOC）
`server/pages/`:
- about_page / admin_list_page / ban_list_page / console_page / inventory_page / leaderboard_page / lottery_list_page / lottery_result_page / lottery_view_page / menu_page / progress_page / red_packet_all_page / red_packet_own_page / shop_list_page / shop_view_page / tutorial_page / user_info_page / warehouse_page

每个 `<name>_page.py` 是一个 `render_<name>_page(...)` 函数，把游戏数据替换到 HTML 模板里输出。

### Bucket D — render 渲染模板（17 HTML，~5K LOC）
`server/templates/*.html`，被 Bucket C 占位符替换后由 playwright 截图。
- 视觉为主，但**用户控制的文本（玩家名、物品名、群名、商店名等）会拼到 HTML**，需关注 XSS / 注入 / 转义。
- 模板已经在 `05-04-audit-render-theme-cleanup` 做过 theme 清理，但未做 security / robustness 审计。

### Bucket E — scripts + Docker
- `scripts/migrate_add_user_coins.py`
- `scripts/package_release.py`
- `Dockerfile` / `docker-compose.yml`

## 审计维度（与既往一致）

1. **Security**：authn/authz 边界 / 输入校验 / 模板 XSS / SQL 注入 / SSRF / 路径穿越 / 凭据 / 日志注入 / 错误信息泄漏
2. **Performance**：N+1 / 同步阻塞 / 缺缓存 / 大对象内存 / playwright 资源 / 重复 fetch
3. **UX**（render layer）：错误回退 / 空数据 / 渲染失败容错 / 视觉一致性
4. **Copy**：模板文案统一 / 中英混排空格 / 占位符未替换 / 错误消息文案

## 流程

1. **Phase A — 并行 research**：派 5 个 `trellis-research` sub-agent，各审一个 bucket，落 `research/<bucket>.md`
2. **Phase B — 修复**：每 bucket 派 `trellis-implement` 应用 High + Medium，跳过 spec 内的"不修"项
3. **Phase C — 验证**：`trellis-check` 抽查（或 sub-agent 自验）
4. **Phase D — 收口报告**：`report.md` 汇总

## Requirements

- 严格遵守 CLAUDE.md（toast 文案 / log 格式 / error.message 透传 / 中英混排空格）
- 修复最小化、不破坏现有行为
- 跨模块发现仅 backlog 标注

## Acceptance Criteria

- [ ] 5 个 bucket 各产出 `research/<bucket>.md`
- [ ] 每条 High/Medium finding 给出 commit 或 explicit skip 原因
- [ ] 最终 `report.md` 汇总用户可读修复明细

## Out of Scope

- 已轮审收敛的模块（除非跨模块溢出）
- 重构 / 重新设计
- 性能基准压测
- nextbot 测试体系

## Technical Notes

- 已有规范来源：`/Users/arispex/.claude/CLAUDE.md`
- prior art：`.trellis/tasks/archive/2026-05/05-15-audit-webui-6/`、`05-13-nextbot-infra-audit-r9/`
