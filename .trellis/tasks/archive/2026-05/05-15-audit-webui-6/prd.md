# audit: WebUI 全量审计（剩余 6 页面 + 公共模块）安全 / 性能 / 交互 / 文案

## Goal

用户目标：完整审计 `server/webui` 相关代码（安全漏洞 / 性能可优化 / UX 可优化 / 文案统一），逐项修复，禁止破坏性更新，直到无新发现，最后产出修复报告。

## 背景

`server/webui` 前 5 个高频页面已轮审过：
- dashboard：R1 + R2 + R3（最近 commit a074e71）
- login：R1（commit 119949b）+ auth-401-vs-302 拆分（commit 9df669b）
- commands：R1 + R2 + R3（最近 commit 385ceb3）
- servers：R1 + R2（最近 commit a6418a6 + c8f1fb9）

**剩余未单独审计的页面 / 模块**：

| 模块 | route LOC | template LOC | js LOC | css LOC |
|------|-----------|--------------|--------|---------|
| groups | 421 | 133 | 610 | 404 |
| lottery | 853 | 328 | 854 | 720 |
| settings | 121 | 248 | 436 | 229 |
| shop | 846 | 319 | 856 | 725 |
| users | 748 | 174 | 988 | 414 |
| warehouse | 235 | 121 | 523 | 497 |
| 公共 app_shell | — | 233 | — | 529 |
| 公共 webui.js | — | — | 163 | — |
| 公共 api.js | — | — | 278 | — |
| 公共 theme-init.js | — | — | 17 | — |
| 公共 login_requests route | 210 | — | — | — |
| 公共 player_events route | 186 | — | — | — |

合计约 ~12K LOC 待审。

## Scope

### In-Scope（按桶分批）

**Bucket A — Pages（6 个，按 LOC 排序）**：
1. groups（routes + template + js + css）
2. settings（routes + template + js + css）
3. warehouse（routes + template + js + css）
4. users（routes + template + js + css）
5. shop（routes + template + js + css）
6. lottery（routes + template + js + css）

**Bucket B — Shared modules**：
- `server/webui/templates/app_shell_base.html` + `static/css/app-shell.css`
- `server/webui/static/js/api.js` + `webui.js` + `theme-init.js`
- `server/routes/webui_login_requests.py`
- `server/routes/webui_player_events.py`

### Out-of-Scope

- 已轮审收敛的 dashboard / login / commands / servers（除非新发现跨模块影响才会触及）
- `server/pages/` 与 `server/templates/`（属于 render 截图体系，非 admin WebUI）
- 后端基础设施（DB / page_store / settings_service）— 除非 webui 调用方式有问题才报告
- 重构 / 大型架构改动 — 仅 hardening 修复

## 审计维度

每个模块按以下 4 维度过：
1. **Security**：authn/authz / 输入校验 / token & 凭据处理 / log 注入 / XSS / SSRF / 越权
2. **Performance**：N+1 / 同步阻塞 / 无 debounce / 缺 abort / 内存泄漏 / 重复 fetch
3. **UX**：错误反馈 / 空态 / loading / 不可逆操作确认 / 键盘可达 / 焦点管理
4. **Copy**：toast 文案规范（`动作 + 结果` / `动作 + 结果，原因`，不含对象名，error 原样透传）/ 同义词统一 / 中英混排空格 / 大小写

## 流程

1. **Phase A — 每页面独立审计**：派 `trellis-research`，输出 `research/<page>.md`（行号 + 维度 + 严重度）
2. **Phase B — 收敛 + 修复**：主代理审 research，挑出 High/Medium 落 `trellis-implement` 修复（禁止破坏性 / 跨模块扩散标记为 backlog）
3. **Phase C — 验证**：派 `trellis-check` 复核每页修复
4. **Phase D — 公共模块审计**（顺位）：app_shell / api.js / webui.js / login_requests / player_events
5. **Phase E — 收口报告**：汇总所有修复项，沉淀 `report.md` 给用户

## Requirements

- 严格遵守已有 webui 全局规范（toast 文案 / log 格式 / API 设计 / 错误透传）
- 修复必须最小化、不破坏已有行为
- 跨模块发现仅打 `backlog` 标记，不无脑展开
- 每个 page 串行（避免互相冲突），但 page 内 backend + frontend 子代理可并行

## Acceptance Criteria

- [ ] 6 个页面 + 6 个公共模块各产出 `research/<name>.md`
- [ ] 每条 High/Medium finding 给出修复 commit 或 explicit skip 原因
- [ ] 修复后 `trellis-check` 通过
- [ ] 最终 `report.md` 汇总用户可读的修复明细

## Out of Scope

- 重构 / 重新设计
- 性能基准压测
- 已轮审 4 页面（除非跨模块溢出）
- 后端基础设施大改

## Technical Notes

- 已有规范来源：CLAUDE.md（toast 文案 / log 格式 / API error.message）
- 已有 audit 实例参考：`archive/2026-05/05-15-webui-servers-audit-r2/` 等
- 审计 commit 风格参考：`refactor(webui): <module> audit fixes — ...`
- 工作目录：`server/webui/` + `server/routes/webui_*.py`
