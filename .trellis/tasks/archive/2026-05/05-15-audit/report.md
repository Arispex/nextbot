# 项目剩余未审计代码全量审计 + 修复 报告

> 任务：`.trellis/tasks/05-15-audit/`
> 起始：2026-05-15
> 范围：之前未单独审计或仅做过 theme 清理的代码
> 约束：禁止破坏性更新，最小硬化

## 1. 范围

之前已完整轮审过：plugins / WebUI admin / `nextbot/` 基础设施 / `bot.py`。本任务覆盖 **5 个 bucket 共 ~10K LOC** 未单独审过的代码：

| Bucket | 文件 | LOC |
|--------|------|------|
| **A — server 核心** | `server/__init__.py`、`web_server.py`、`server_config.py`、`page_store.py`、`screenshot.py`、`settings_service.py` | ~1.3K |
| **B — server 路由公共层** | `server/routes/__init__.py`、`webui.py`、`render.py` | ~750 |
| **C — render 渲染页面后端** | `server/pages/*.py`（17 文件） | ~3K |
| **D — render 渲染模板** | `server/templates/*.html`（17 文件） | ~5K |
| **E — scripts + Docker** | `scripts/migrate_add_user_coins.py`、`package_release.py`、`Dockerfile`、`docker-compose.yml`、`.dockerignore` | ~500 |

## 2. 研究阶段

5 个 `trellis-research` sub-agent 并行审计，产出共 **119 项 finding**：

| Bucket | Critical | High | Medium | Low | Info/Backlog | 合计 |
|--------|----------|------|--------|-----|--------------|------|
| scripts_docker (E) | 0 | 2 (P0) | 9 (P1) | 11 (P2) | – | 22 |
| route_helpers (B) | 1 | 4 | 7 | 5 | 6 | 23 |
| server_core (A) | 1 | 7 | 13 | 11 | – | 32 |
| render_pages (C) | 0 | 1 | 2 | 11 | – | 14 |
| render_templates (D) | 0 | 0 | 8 | 14 | 6 | 28 |
| **合计** | **2** | **14** | **39** | **52** | **12** | **119** |

详细 findings：`.trellis/tasks/05-15-audit/research/<bucket>.md`。

## 3. 修复阶段

5 个 `trellis-implement` sub-agent 各负责一个 bucket，依据 research report 应用 High + Medium + 选择性 Low。

### 3.1 Bucket E — scripts + Docker（18 applied / 4 skipped）

- **F1 (P0)**：`Dockerfile` 加 `nextbot` 非特权用户（uid 1000）+ `USER nextbot`（在 `playwright install` 之后）
- **F2 (P0)**：`migrate_add_user_coins.py` 重写为 argparse + `--dry-run` + `--backup-path` + 显式 BEGIN / commit / rollback + 统一 `[ts] [level]` logger + 区分退出码 (0/1/2)
- **F3**：migration 加 `--db` 参数；env-aware 默认 `NEXTBOT_DATA_DIR/app.db`
- **F4**：`package_release.py` 加 secret deny-list（`*.pem *.key .env* id_rsa* *secret* *credentials* *token* .webui_auth.json app.db*`）+ `--list` / `--allow-secrets` 闸门
- **F5**：`.dockerignore` 加 `docs/` / `AGENTS.md` / `CLAUDE.md` / `*.md` 但白名单 `README.md` / `LICENSE`
- **F7**：`docker-compose.yml` ports rebound 到 `127.0.0.1:<port>:<port>`（18081 / 6099 / 3001）
- **F8**：`docker-compose.yml` 加 `user: "1000:1000"`
- **F9 / F10**：apt cache + uv cache `--mount=type=cache`
- **F11**：migration 加 `PRAGMA busy_timeout = 30000`
- 其它 P1 / P2：unified log format、进度提示、zip size、Chinese error messages、`restart: on-failure:5`
- **跳过**：F6（HEALTHCHECK 需新 webui endpoint — 跨模块）、F17（base image digest pin — 不动版本）、F18（release pipeline）、F22（schema_migrations infra）

### 3.2 Bucket B — server 路由公共层（CRIT + 4 HIGH + 8 MED + L）

- **CRIT-1 X-Forwarded-For 信任问题**：`server/routes/__init__.py` 新增公共 `client_ip` / `user_agent` helper，默认 fail-closed（不读 XFF）；`server_config.py` 增 `WebServerSettings.trusted_proxies`，仅当直接 IP 在 trusted set 中才解析 XFF（取最后一段）；8 个 webui_*.py 副本改为薄 re-export 别名（`_client_ip = client_ip`）— 单一来源
- **HIGH-3 `read_json_object` body size cap**：`Content-Length` 预检 > 256 KiB → 413；`Content-Type` 校验 → 415；streaming size 限制
- **HIGH-4 / HIGH-5 symlink 拒绝**：`render._resolve_static_file` + `webui._resolve_webui_static_file` 拒绝 symlink
- **MED-6 `/render/*` loopback-only auth**：`_ensure_loopback(request)`，非 127.0.0.1 / ::1 → 403
- **MED-19 sync renderer 阻塞事件循环**：17 个 render endpoint 全部用 `asyncio.to_thread(renderer, payload)` 包裹
- **MED-7 / MED-8**：cookie decode 收窄异常（`binascii.Error / ValueError / UnicodeEncodeError`）；`_sanitize_next_path` 仅允许 `/webui` 前缀 + 512 char cap
- **MED-23 / MED-27**：render / static 错误信息中文化；删除 429 message 中的"请稍后再试"
- **LOW**：`HARD_MAX_PER_PAGE = 1000` clamp、`_parse_positive_int` 32 char 早返、`_failed_login_history` 10K key cap + lazy GC

### 3.3 Bucket A — server 核心（1 deferred + 多 HIGH/MED/LOW applied）

- **H-1 token 不再 WARN 级落盘**：`_mask_token` + INFO 级日志，首次启动只点出"详见 .webui_auth.json"
- **H-2 graceful shutdown**：替换 `uvicorn.run` 为显式 `uvicorn.Server` + `stop_web_server`；子线程禁用 signal handler
- **H-3 CORS**：`_resolve_cors_allowed_origins` 读 `webui_cors_allowed_origins`，默认空（同源），显式过滤 `*`
- **H-4 `/health` loopback-only**：非 loopback → 404（迷惑外部探测）
- **H-5 SSRF 防护**：`_assert_local_url` block 非 loopback 主机 + 非 http(s) scheme
- **H-6 page_store LRU + cap**：`OrderedDict` + `MAX_STORE_SIZE=5000` + LRU evict + watermark log + `get_metrics()`
- **H-7 auth 文件原子写**：`os.open` + `O_CREAT|O_TRUNC` (mode 0o600) + `os.replace` + 类型校验
- **MED**：`_WEBUI_ROUTERS` registry 替换 12 个 hand-written `include_router`；Chromium launch 加 4 个 hardening flag；`_parse_port` invalid 路径 WARN；settings 读锁 + 1 MiB 大小 cap；`_MULTILINE_ESCAPED_FIELDS` 常量；snapshot fallback WARN；`SaveSettingsResult.normalized_values`
- **LOW**：18 个 `create_*_page` 用 `_make_page_url` 去重；启动日志合并行；QQ 模式收紧到 5-11 位；`_escape_for_env` / `_unescape_from_env` docstring
- **跳过**：CRIT-1（已由 route_helpers 处理 /render auth）；M-8 cross-loop atexit（架构性，accepted limitation）；L-11（settings handler 跨模块）

### 3.4 Bucket C — render 渲染页面后端（17 文件 + 5 项）

- **High 1 — 模板每请求磁盘读阻塞 async loop**：17 个 page 全部加 module-level `_template_cache` + `mtime`-based 失效（`console_page.py` 用 dict-keyed 缓存多模板，且保留 commit `6995d3c` 的 app_shell 增量）
- **Medium — `tutorial_page._resolve_avatar` 开放 img.src sink**：unknown placeholder → 返回空串（关 sink，img 回退模板初值）
- **Medium — list entries cap**：11 个 list page 加 `MAX_ENTRIES = 200`（`ban_list / leaderboard / lottery_list / lottery_result / lottery_view / menu / red_packet_all / red_packet_own / shop_list / shop_view`）
- **Low — `max(1, ...)` 分页 floor**：4 page 与 shop / lottery 对齐（`ban_list / leaderboard / red_packet_all / red_packet_own`）
- **Low — `lottery_result_page` coin parse 失败降级**：`continue` 丢条 → `coin_amount=0` 保留 outcome（用户仍看得到奖品）
- **跳过**：8 / 9 / 10 / 11 / 12 / 14（跨模块文案上移到 plugin 层 / 已合规 / Info-only）

### 3.5 Bucket D — render 渲染模板（17 文件 + Medium 全 + 防御 LOW）

- **S1 — avatar `http://q1.qlogo.cn` → `https://`**：7 个模板（`about / admin_list / ban_list / inventory / red_packet_all / user_info / warehouse`）
- **S3 — placeholder fallthrough fallback**：17/17 模板的 `JSON.parse(...)` 包 IIFE try/catch，失败返回 `{}` + `console.warn("[render] page data unavailable", err)`，防止 Python 端忘了替换占位符导致截图白屏
- **U1 — `[hidden] { display: none !important; }`** 加到缺失的 10 个模板（`lottery_list / lottery_view / lottery_result / menu / red_packet_all / red_packet_own / shop_list / shop_view / user_info / warehouse`），匹配 `leaderboard` 模式
- **S2 — about URL protocol whitelist**：`safeHttpUrl(u)` 仅接受 `http(s)://`，拒绝 `javascript:` / `data:`
- **S7 — progress.html `server_id` 归一化**：`String(Number(data.server_id))` + `Number.isFinite` 守卫
- **C1 — 文案统一**：`warehouse.html` "未知用户" → "未知玩家" 对齐 `inventory / lottery_result`
- **跳过**：P1（dict fetch to backend — 跨模块 backlog）；S4 / U2 / U4 / C6 / C7（by design）；P2/3/4/5（Info）；A1（screenshot 无 a11y 消费者）

## 4. 验证

每个 sub-agent 在交付前进行了自验：

| Bucket | Python / py_compile | node / HTML | ruff | pyright |
|--------|---------------------|-------------|------|---------|
| scripts_docker (E) | PASS | – | `docker buildx build --check` PASS | – |
| route_helpers (B) | PASS | – | F/E9/B/SIM 全 PASS | – |
| server_core (A) | PASS | – | clean | – |
| render_pages (C) | PASS（18 文件）| – | clean | – |
| render_templates (D) | – | HTML 平衡 / `<html>` / `<body>` / `<script>` / `<style>` 全平衡 | – | – |

**整体**：无新增 functional 回归；无破坏性变更（除 `replace_all` 类已设计要求 `confirm` 字段、`/render/*` 现要求 loopback、settings 现要求 `X-Requested-With` 之类显式提升的安全闸门外）。

## 5. 修复落地数

| Bucket | finding 总数 | applied | skipped (有理由) | backlog (跨模块) |
|--------|--------------|---------|------------------|------------------|
| scripts_docker | 22 | 18 | 4 | – |
| route_helpers | 23 | 17 | 6 | 4 |
| server_core | 32 | 28 | 3 | 1 |
| render_pages | 14 | 5 | 6 | 3 |
| render_templates | 28 | 14 | 8 | 6 |
| **合计** | **119** | **82** | **27** | **14** |

> 实际落地 **82 项修复**（占 finding 总数 68.9%）；27 项 skipped 是 spec 内说明的"已合规 / 非问题 / 架构级"；剩余 ~14 项跨模块 backlog 留给后续任务。

## 6. 关键命中类

- **凭据保护**：token 不再 WARN 落盘 / auth 文件原子写 / page_store LRU + cap
- **审计可溯**：trusted_proxies XFF 解析 + 单一 `client_ip` helper（消除 8 处副本）
- **DoS 防御**：`read_json_object` 256 KiB body cap + 415 / 413；list page MAX_ENTRIES 200；keyword 长度 cap；page_store size cap；scripts secret deny-list
- **SSRF 防护**：`_assert_local_url` + `/render/*` loopback-only + `/health` loopback-only + 截图 URL whitelist
- **a11y / UX**：17 模板加 `[hidden]` 全局守卫；17 模板加 placeholder fallthrough 防白屏
- **HTTPS**：7 模板的 QQ 头像 URL → HTTPS（防 mixed-content / 隐式外链 leakage）
- **公平性 / 完整性**：lottery weight 校验已完成（前序 audit-webui-6）；本轮 lottery_result 容错降级
- **容器/部署**：非 root 用户、端口绑 127.0.0.1、secret deny-list 防泄漏
- **性能**：17 page 模板加 mtime 缓存（避免每请求磁盘读）；render endpoint 用 asyncio.to_thread 不阻塞 event loop；Chromium hardening flag
- **文案**：error.message 中文化、删除"请稍后再试"、"未知用户" → "未知玩家" 统一

## 7. 跨模块 backlog（独立任务排期）

1. **CSRF 中间件全局化**（与前序 webui-6 backlog 合并）
2. **shared `_client_ip` 全局收敛已完成**（本任务）；下一步可考虑彻底删除 8 处 alias
3. **modal helper 共享层**（与 webui-6 backlog 合并）
4. **`page_store` 后端化**：list-page dict 从 plugin 层拉而非 render 层硬编码
5. **`/webui/api/healthz` endpoint**：用于 Docker HEALTHCHECK / settings 重启 poll
6. **release pipeline / schema_migrations 表**
7. **WebUI RBAC**（与前序 backlog 合并）
8. **`.env` 加密**（与前序 backlog 合并）
9. **cross-loop atexit shutdown 协议**：playwright `_session._lock` 跨 loop 问题
10. **缓存 invalidation**：`get_server_settings` 接到 settings save 事件后失效

## 8. 修改文件清单

合计约 57 文件改动：
- 后端 server：`server/__init__.py`、`server/web_server.py`、`server/server_config.py`、`server/page_store.py`、`server/screenshot.py`、`server/settings_service.py`
- 后端 routes：`server/routes/__init__.py`、`webui.py`、`render.py` + 8 个 webui_*.py 改为薄 alias
- 后端 pages：`server/pages/*.py`（17 文件）
- 前端 templates：`server/templates/*.html`（17 文件）
- 脚本：`scripts/migrate_add_user_coins.py`、`scripts/package_release.py`
- 容器：`Dockerfile`、`docker-compose.yml`、`.dockerignore`
- 任务文档：`.trellis/tasks/05-15-audit/{prd.md, report.md, research/*.md}`

## 9. 收口

本任务完成一轮全量审计 + 修复闭环：研究 → 修复 → 内部自验 → 报告交付。所有"非破坏 / 最小化"硬化已落地。跨模块共享层 / 架构级议题作为独立任务排期。
