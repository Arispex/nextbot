# audit: WebUI 服务器管理页面 round-2 复审（R1 修复 + 全量再扫）

## Goal

R1 (`1355521`) 落地 ~22 项 servers 页面修复。R2 复审：
1. 验证 R1 修复实际正确性（特别 H-1 token 链改造 / H-2 enum 映射 / H-3 文案 / 1 self-fix）
2. 全量再扫剩余 finding
3. 若 0 Critical / 0 High / 0 Medium → 宣告 servers 页面收敛闭环

## ⚠️ 严格 scope（dashboard R3 教训）

**只审 servers 页面 3 文件**：
- `server/routes/webui_servers.py`
- `server/webui/templates/servers_content.html`
- `server/webui/static/js/servers.js`

CSS R1 未动，可不审。

**禁止扩散**到 api.js / webui.js / shell / 其他 webui 模块 / 基础设施。跨模块发现仅 `scope-out backlog` 标注。

## R1 已修清单（~22 项，不重复挖）

### High（5）
- **H-1 token 链改造**：
  - 后端 `_mask_token` + `_serialize_server` 返回 mask
  - PUT 接受空 / mask 时保留原值
  - 新增 `GET /webui/api/servers/{id}/token` reveal endpoint（auth + WARN 日志 + client_ip/UA）
  - 前端 `visibleTokenIds` Set → Map<id, fullToken>
  - 10s 自动隐藏 + clearRevealTimer 在 toggle/delete/reload-cleanup
  - editor `tokenInput.value=""` + placeholder
  - `buildPayloadFromModal(isEditMode)` 编辑模式空 token → 传空串
  - plugin-config password 字段不回填明文
  - `closePluginConfigModal` 清空 password input value
- **H-2** verify probeStatus 映射
- **H-3** 进行时文案去对象名（7 处）
- **H-4** 空态文案统一

### Medium（9）
- F-A-4 (H-1 子项) / B-1 search debounce / B-2 翻页 abort / B-4 /test timeout 10s / B-7 test/verify timeoutMs 30s / A-4 plugin-config 白名单 / C-2-D emptyNode 不塞错误 / C-2-L "没有可保存的修改" / C-2-M "当前没有可编辑的配置"

### Low（8 done + 2 skip）
- B-6 IntegrityError retry / C-2-E "响应数据格式异常" / C-2-K "加载中…" / D-2 8 endpoint client_ip+UA / A-8 path ge=1 / A-9 keyword 长度 / D-3 reindex log / R1 self-fix（modalCloseButton arrow wrap）

### 排除项（不重复挖）
- B-3 testServerConnectivity 全局并发（backlog）
- B-4 renderTable 全量重绘（实际 < 10 台）
- C-1 500 message 泛化（与 dashboard C3 决策一致）
- A-3 backend CSRF / A-5 上游 error 白名单 / A-7 DELETE reindex 副作用（跨模块）
- B-3 async DB / D-2 _client_ip helper 跨模块

## 关注点

### R1 修复复审重点
1. **H-1 token 链**：mask 形式合理 / reveal endpoint 权限 / 10s timer 清理 / editor 空 token 行为 / 不破坏旧 PUT caller / plugin-config password 行为
2. **R1 self-fix**：modalCloseButton arrow wrap 真正生效
3. **H-2 verify enum**：3 个分支正确
4. **B-1 debounce + abort**：commands R1 同模式，是否有 race 边界
5. **A-4 白名单**：实际 plugin-config 字段是否被误拒
6. **D-2 logger**：8 endpoint 全覆盖

### 全量再扫
- R1 改造周边新暴露面
- 严格 servers scope

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端）
2. **主代理二次审核**：High / Medium 行号验证，沉淀 verify-pass2.md
3. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] R1 ~22 项修复每条 PASS / NEW-ISSUE 判定
- [ ] 2 个子代理产物落 `research/{backend,frontend}.md`
- [ ] 主代理沉淀 `verify-pass2.md`
- [ ] 严格 scope 不扩散

## Out of Scope

- 实施修复（先报告）
- 排除项重新挖
- servers 页面以外的代码

## Technical Notes

- R1 prior art commit：`1355521`
- R1 任务归档：`.trellis/tasks/archive/2026-05/05-15-webui-servers-audit/`
- scope-creep 警告：dashboard R3 / commands R3 都已踩过坑，本任务严格遵守
- 重点验证 H-1 token 链改造（最复杂的修复，涉及前后端协同）
