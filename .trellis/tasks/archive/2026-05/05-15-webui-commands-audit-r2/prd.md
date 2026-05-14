# audit: WebUI 命令配置页面 round-2 复审（R1 修复 + 全量再扫）

## Goal

R1 (`10d7936`) 落地 ~20 项 commands 页面修复。R2 复审：
1. 验证 R1 修复实际正确性（input 校验 / 文案 / debounce / focus trap / aria 等）
2. 全量再扫剩余 finding，特别关注 R1 改造周边新暴露面
3. 若 0 Critical / 0 High / 0 Medium → 宣告 commands 页面收敛闭环

## R1 已修清单（必读避免重复挖）

### 后端（webui_commands.py）
- A-3 command_key regex 白名单（`^[A-Za-z0-9_.\-/]{1,64}$`）+ 长度上限
- A-4 `_validate_param_values` helper（64 keys / 64-char key / 4096-char value）
- A-5 `_validate_aliases_list` helper（32 item / 32-char per-alias）
- A-6 param_values isinstance 短路（与 aliases 风格统一）
- C-6 update_aliases route 补 except Exception 兜底 500
- D-2 logger `str(exc)[:500]` 截断（5 sites）
- 抽 `_map_validation_error` helper 去重 422→404/409 映射

### 前端（commands.js + commands_content.html）
- P0-T1 commands.js:343/606 文案去对象名 → "保存成功，已立即生效；刷新失败，请手动刷新页面"
- P1-Race search 300ms debounce + AbortController + signal 透传
- P2-T1 restart 传 `action: "重启"`
- P2-T2 删除 modal alert 重复
- P2-A 3 个 modal focus trap + 自动 focus + 关闭返还（getFocusableInModal / buildTrapFocusHandler / openModalWithFocus / closeModalAndRestoreFocus）
- P2-ESC restart modal window-level ESC keydown
- P2-Loading loading 节点 aria-live + aria-busy + JS setLoadingVisible 同步
- P3 排版（"…" / 全角冒号）
- P3 一致性（alias saving guard / 空态去句号 / replaceChildren）

## R1 排除项（不重复挖）

- C-1 message "至少需要提供..."（原始 reason 已合规）
- D-3 / D-6 日志细节决策保留
- C-5 跨模块字符串耦合（service 层 backlog）

## Scope（严格，与 R1 一致）

仅 3 文件：
- `server/routes/webui_commands.py`
- `server/webui/templates/commands_content.html`
- `server/webui/static/js/commands.js`

**禁止扩散到其他 webui 模块 / shell / api.js / 基础设施层**。

## 关注点（按优先级）

### R1 修复复审重点
1. **A-3 regex** 是否真覆盖所有 command_key 命名空间（实际 plugin command_key 是否会被误拒）
2. **A-4 大小限制阈值**（64/64/4096）是否合理 + 异常分支文案规范
3. **A-5 aliases 32 上限**：与 nextbot/command_config.py `update_command_aliases` 内部限制是否冲突
4. **C-6 except Exception 兜底**：是否正确返回 500 JSON
5. **D-2 `str(exc)[:500]`**：是否截断 unicode 字符导致乱码
6. **P1-Race debounce + AbortController**：abort 旧请求时 loadCommands 重入 / signal 透传 / cleanup
7. **P2-A focus helpers**：WeakMap GC / 多 modal 同时打开 / focus 恢复目标失效（previousFocus 被移除 DOM 后）
8. **P2-ESC restart ESC**：window-level keydown 是否与 param/alias modal ESC 冲突（多 modal 打开时）
9. **P2-Loading aria-busy 同步**：setLoadingVisible 是否在所有 loading 切换点调用

### 全量再扫
- 安全 / 性能 / 文案剩余项
- R1 改造周边新暴露面（如 `_map_validation_error` helper 失败模式 / `_validate_param_values` 边界值）

### 不重复挖
- C-1 / D-3 / D-6 / C-5
- R1 已修 ~20 项重复挖
- Round 7-9 / login-audit / dashboard R1+R2 / 401vs302 已闭环
- 任何 commands 页面以外的代码

## Requirements

1. **分桶并行**：2 桶 trellis-research（后端 + 前端）
2. **主代理二次审核**：High / Medium 行号验证，沉淀 verify-pass2.md
3. **本轮先报告**：用户决定

## Acceptance Criteria

- [ ] R1 ~20 项修复每条 PASS / NEW-ISSUE 判定
- [ ] 2 个子代理产物落 `research/{backend,frontend}.md`
- [ ] 主代理沉淀 `verify-pass2.md`
- [ ] 文案 finding 必须给"修复前 → 修复后"字符串对比
- [ ] 严格 scope，不扩散到其他模块
- [ ] 若 0 Critical / 0 High / 0 Medium，可声明 commands 页面收敛闭环

## Out of Scope

- 实施修复（先报告）
- C-1 / D-3 / D-6 / C-5 排除项
- Round 7-9 / login-audit / dashboard / 401vs302 已闭环范围
- commands 页面以外的代码

## Technical Notes

- prior art commit：`10d7936`
- R1 任务归档位置：`.trellis/tasks/archive/2026-05/05-14-webui-commands-audit/`
- scope-creep 警告：dashboard R3 踩过坑（误把 api.js 等共享改造的副作用拉进），本任务严格守住 3 文件
