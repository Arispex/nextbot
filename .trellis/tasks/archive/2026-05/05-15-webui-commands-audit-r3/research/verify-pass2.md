# Commands R3 复审 — 主代理二次审核

## 整体结论

**Commands 页面经 R1 + R2 + R3 三轮系统审计已完全收敛闭环**：
- **0 Critical / 0 High / 0 Medium-阻塞**
- R1 (`10d7936`) ~20 项 + R2 (`f512c8c`) 13 项 = 累计 ~33 项修复**全 PASS 无 regression**
- R3 新发现仅低优先级 polish + 跨模块 backlog

## R2 修复复审：13/13 PASS（无 regression）

| R2 修复 | 状态 |
|---|---|
| **B-7** R1 regression（closeAliasModal arrow wrap） | ✅ PASS（彻底闭环，saveAliases force=true 路径正确） |
| B-1 cancelPendingSearch helper + 5 处调用 | ✅ PASS |
| B-3 previousFocus fallback + native button tabindex 修正 | ✅ PASS（self-fix 已生效） |
| B-4 modal stack + 单 ESC dispatcher | ✅ PASS（pushModalToStack 去重 / popModalFromStack 栈顶语义正确） |
| B-5 openModalWithFocus 已打开 return | ✅ PASS（防 previousFocus 被覆盖） |
| B-6 focusable selector 扩展 | ✅ PASS |
| B-8 requiredNodesReady + reloadButton + searchInput | ✅ PASS |
| B-9 apiReady=false disable 6 控件 | ✅ PASS |
| B-10 WeakMap paramInputSchemas | ✅ PASS（dataset.paramSchema 完全消除） |
| B-11 alias 中文逗号 + 去重 | ✅ PASS |
| B-12 body inline scroll lock（嵌套 modal 正确） | ✅ PASS |
| M-B2 enabled=null 拒绝 | ✅ PASS（isinstance bool 拦截 None/string/int） |
| M-B3 _client_ip + 5 logger sites | ✅ PASS（import 单向无环 / user_agent[:200]） |
| M-B4 _PARAM_KEY_PATTERN | ✅ PASS（实测 42 plugin schema key 100% 覆盖） |
| L-B2 alias strip 再算长度 | ✅ PASS |

## R3 新发现（不需修，仅记录）

### Low / 前端（5 项，可选修）

| ID | 文件 / 行号 | 一句话 |
|---|---|---|
| F-R3-3 | `commands.js:1031-1033` | saveAliases reload 失败时静默吞错，与 saveSingleCommand `{reloaded}` 二元状态模式不对齐 |
| F-R3-4 | `commands.js:1013` | alias saving 中只 disable saveButton，cancel/close 仍可点（点无效但视觉无变化）；与 param modal `setModalSavingState` disable 3 个按钮不一致 |
| F-R3-5 | `commands.js:1036-1038` | saveAliases 取 `details[0].message` 仅首项，与 api.js `buildDetailReason` 用 `";"` 拼接所有 detail 不一致 |
| F-R3-2 | `commands.js:1032` | alias 保存成功文案 "保存成功，需要重启后生效" 与同页面其他保存路径 `保存成功` 不完全对齐 |
| F-R3-1 | `commands_content.html:127` | alias placeholder `例如：c, exec, run` 未提示支持中文逗号（B-11 支持但 UX 未提示） |

### Low / 后端（2 项，不修）

| ID | 一句话 |
|---|---|
| L-B3 | webui_commands.py 部分 INFO 日志缺 user_agent，与 webui.py 基线一致，不修 |
| L-B4 | `_PARAM_KEY_PATTERN` 注释精度（`[A-Za-z_][A-Za-z0-9_]*` vs 实际 `[A-Za-z0-9_]+`），可选注释微调 |

### Scope-out backlog（跨模块，不在本任务）

- **B-OUT-1** api.js `unwrapData` "返回数据格式错误" 业务层翻译
- **B-OUT-2** api.js `buildDetailReason` vs caller 取 first detail 行为不一致
- **B-OUT-3** alias 后端是否大小写归一化 / 长度上限未知
- **B-OUT-4** modal save 在飞 + toolbar reload 并发模型未规范化（全 webui 共面）

## 验证关键 R2 修复（行号 verify）

| Finding | 行号 verify |
|---|---|
| B-7 修复实效 | ✅ `commands.js:1047-1049` `() => closeAliasModal()` arrow wrap |
| B-1 helper + 5 callers | ✅ `commands.js:874-883, 886, 907, 917, 926, 933` |
| B-3 fallback + tabindex native check | ✅ `commands.js:235-253` 含 `nativelyFocusable` check |
| B-4 modal stack | ✅ `commands.js:154-171, 937-946`，3 closer registered |
| B-10 WeakMap | ✅ `commands.js:54, 678, 713`；grep `dataset.paramSchema` 已清空 |
| B-12 scroll lock 嵌套 | ✅ `commands.js:176-185` 仅栈深 1 时 lock，仅栈空时 unlock |
| M-B2 isinstance bool | ✅ `webui_commands.py:206-213` |
| M-B3 _client_ip 5 sites | ✅ `webui_commands.py:24, 163-169, 228-249, 257-259, 291-313, 321-323` |
| M-B4 _PARAM_KEY_PATTERN | ✅ `webui_commands.py:34-35`，子代理实测 42 plugin schema key 100% 覆盖 |

## 主代理终判

**Commands 页面 R3 完全收敛闭环**：
- 0 Critical / 0 High / 0 Medium-阻塞
- R1 + R2 累计 ~33 项修复全部生效
- R3 ~6 项 Low 全是 polish / 一致性 / 错误处理边界，无强烈生产 trigger
- 4 项 scope-out backlog 跨模块，不在 commands 桶职责

## 建议

**A** 声明 commands 页面**彻底闭环**，剩余 polish 项进 backlog
**B** 顺手修 5 项前端 Low（F-R3-1 ~ F-R3-5），~30 行改动
**C** 按 ID 逐条选

推荐 **A**：commands 桶审计 ROI 已大幅下降，剩余 finding 全是低风险细节，可作 backlog；后续审计应转向其他业务页面（users / servers / lottery / shop / groups / warehouse / settings）。
