# audit: WebUI 命令配置页面 round-3 复审（R2 修复 + 全量再扫）

## Goal

R1 (`10d7936`) ~20 项 + R2 (`f512c8c`) ~13 项累计 ~33 项 commands 修复。R3 复审：
1. 验证 R2 修复实际正确性（特别 B-7 R1 regression / modal stack / WeakMap 替换 / body scroll lock）
2. 全量再扫剩余 finding
3. 若 0 Critical / 0 High / 0 Medium → 宣告 commands 页面收敛闭环

## ⚠️ 严格 scope（dashboard R3 教训）

**只审 commands 页面 3 文件**：
- `server/routes/webui_commands.py`
- `server/webui/templates/commands_content.html`
- `server/webui/static/js/commands.js`

**禁止扩散**到 api.js / webui.js / shell / 其他 webui 模块 / 基础设施。任何"跨模块副作用 finding" 应明确标注为 backlog，不计入本任务严重度。

## R1 + R2 已修清单（必读避免重复挖）

### R1（commit 10d7936，~20 项）
后端：A-3 / A-4 / A-5 / A-6 / C-6 / D-2 / _map_validation_error helper
前端：P0-T1 / P1-Race / P2-T1 / P2-T2 / P2-A / P2-ESC / P2-Loading / P3 排版 / P3 一致性

### R2（commit f512c8c，13 项）
- **B-7** R1 regression：closeAliasModal arrow wrap
- B-1 cancelPendingSearch helper
- B-3 previousFocus fallback + tabindex on native button 修正（trellis-check self-fix）
- B-4 modal stack + 单 ESC dispatcher
- M-B2 enabled=null 拒绝
- M-B3 5 处 logger 补 client_ip + user_agent（复用 webui._client_ip）
- M-B4 _PARAM_KEY_PATTERN 字符集
- B-2 beforeunload abort
- B-5 openModalWithFocus 已打开 → return
- B-6 focusable selector 扩展
- B-8 requiredNodesReady 含 reload + search
- B-9 apiReady=false 时 disable 6 控件
- B-10 WeakMap paramInputSchemas
- B-11 alias 中文逗号 + 去重
- B-12 body inline scroll lock
- L-B2 alias strip 再算长度

## 排除项（不重复挖）

- C-1 message 原始 reason 已合规
- D-3 / D-6 日志细节决策保留
- C-5 跨模块字符串耦合（service 层 backlog）
- B-13 HTML output 语义（保持 <div>）
- L-B1 422 details shape（保持现状）
- H-B1 共享层 body size limit（FastAPI middleware 层 backlog）

## 关注点

### R2 修复复审重点
1. **B-7 修复实效**：arrow function 包裹后 force 参数不再被 MouseEvent 污染
2. **B-3 fallback tabindex 在 native button 的副作用**：self-fix 是否真的避免破坏 native focus order
3. **B-4 modal stack**：3 个 close 函数 + registerModalCloser + dispatcher 行为等价 R1
4. **B-10 WeakMap**：dataset.paramSchema 路径完全删除？JSON.parse 错误分支是否还在
5. **B-12 body scroll lock**：inline style 是否被其他 script 覆盖 / 嵌套 modal 行为
6. **M-B2 enabled=null 拒绝**：与现有 PATCH 部分更新语义协调
7. **M-B3 _client_ip import**：单向、无循环
8. **M-B4 _PARAM_KEY_PATTERN**：覆盖所有 plugin 实际 param key
9. **B-9 apiReady=false disable**：是否影响 R1/R2 已修的 setStatus 反复刷新

### 全量再扫
- 新发现 finding（特别 R2 改造周边）
- 严格限定 commands 3 文件

### 不重复挖
- C-1 / D-3 / D-6 / C-5 / B-13 / L-B1 / H-B1
- R1 + R2 已修 ~33 项
- Round 7-9 / login-audit / dashboard R1+R2 / 401vs302 已闭环
- shell / api.js / webui.js / 其他模块

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端）
2. **主代理二次审核**：High / Medium 行号验证，沉淀 verify-pass2.md
3. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] R2 13 项修复每条 PASS / NEW-ISSUE 判定
- [ ] 2 个子代理产物落 `research/{backend,frontend}.md`
- [ ] 主代理沉淀 `verify-pass2.md`
- [ ] **严格 scope，不扩散到其他模块**
- [ ] 若 0 Critical / 0 High / 0 Medium，可声明 commands 页面收敛闭环

## Out of Scope

- 实施修复（先报告）
- 排除项重新挖
- commands 页面以外的代码

## Technical Notes

- R1 prior art commit：`10d7936`，R2 prior art commit：`f512c8c`
- R2 任务归档：`.trellis/tasks/archive/2026-05/05-15-webui-commands-audit-r2/`
- scope-creep 警告：dashboard R3 教训不要重蹈覆辙
