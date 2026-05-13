# WebUI 仪表盘 R3 复审 — 主代理二次审核

## 整体结论

**Dashboard 桶本身经 R1 + R2 累计 20 项修复后已彻底闭环**。R3 重新扫描发现：

- **R2 10 项修复 100% 功能正确**（实施质量高）
- **dashboard 桶范围内 0 finding**
- **跨桶溢出 / 其他 webui 模块**仅 2 项值得修：
  - **B-5 (Medium)** users.js multi-line setStatus 主语违规（"封禁成功，用户 张三"）
  - **B-6 (Low)** commands.js:606 "参数保存成功" 含对象名

## R2 复审：10/10 PASS

| 修复 | 状态 | 残留 |
|---|---|---|
| R2-T-1 sync-whitelist broadcast | ✅ PASS | outcome shape 与 users.js:853-862 对齐 |
| R2-T-2 ban/unban broadcast | ✅ PASS | 同上 |
| R2-T-3 verify-nextbot timeout 10s | ✅ PASS | `webui_servers.py:435` 已落地 |
| R2-T-4 老浏览器 setTimeout fallback | ✅ 功能 PASS | A-1 timer 未 clearTimeout（LOW hygiene） |
| R2-T-5 userSignal merged controller | ✅ 完全正确 | A-3 listener 持续期 INFO |
| R2-T-6 timeoutMs per-call | ✅ PASS | A-4 字符串入参 INFO |
| R2-B-1 #loading aria-live | ✅ PASS | — |
| R2-B-2 删 dead CSS | ✅ PASS | — |
| R2-B-3 删冗余 class | ✅ PASS | — |
| R2-B-4 currentReloadController | ⚠️ abort 路径 dead branch（silent-return 分支 OK） | LOW |

R1 + R2 共 20 项修复全部保留。

## R3 新发现验证（行号 verify）

### Medium（1 项，跨桶）

#### B-5 users.js 多行 setStatus 主语违规

**文件 / 行号 验证 ✅**：
- `users.js:802` `var lines = [actionText + "成功，用户 " + user.name];` → 渲染 "封禁成功，用户 张三"
- `users.js:845` `const lines = [\`用户 ${userName} 白名单同步结果：\`];` → 渲染 "用户 张三 白名单同步结果："

**违反 CLAUDE.md "用户操作反馈文案规范"**：
- 反例："删除服务器成功" → 应为 "删除成功"
- 本案"封禁成功，用户 张三" 同模式：含对象名 "用户 X" 违规

**触发概率**：HIGH（每次 ban / unban / sync-whitelist）

**修复**：主标题改"封禁成功"，详情行保留 N 服务器结果

### Low（2 项）

#### B-6 commands.js:606 "参数保存成功"

**行号 verify ✅**：`commands.js:606` `setStatus("参数保存成功，已立即生效；列表刷新失败，请手动刷新页面确认最新状态", "warning")`

**违规**："参数保存成功" 含对象名"参数"，应为"保存成功"

**触发概率**：MEDIUM（参数保存路径）

#### R3-NEW-1 server_broadcast.py:69 detail=str(exc) 异常 fallback

**行号 verify ✅**：`server_broadcast.py:69` `BroadcastOutcome(server=srv, ok=False, detail=str(exc) or "异常", payload=None)`

**风险**：`str(exc)` 在 fn 内异常时透传到前端，未来 fn 误把 server.token 等敏感信息进异常消息时会泄漏到前端

**当前**：所有 caller 都已在 fn 内 try/except TShockRequestError 在前 layer 拦截，**概率 < 1%**

**严重度**：LOW / Defense-in-depth

### Info / 不修

- A-1 老浏览器 timer 未 clearTimeout（性能 hygiene）
- A-2 老浏览器 AbortError 文案 fallback 不准确（≤ Chrome 100，受众小）
- A-5+B-3 R2-B-4 abort dead branch（silent-return 分支有效）
- B-2 microtask focus 抢用户转移焦点（触发概率低）
- B-4 .alert success/info 视觉相同（commands/users 跨桶细节）
- 其他 7 项 INFO 详见 frontend.md

## False positive 主代理拒绝

- 各种"未来风险 / 极端边界" 当前不触发的 INFO 项
- R2-B-4 dead branch 严格说不是 finding，是过度防御
- A-3 listener 跨 fetch lifetime —— R2 实现已正确

## 主代理终判

| 类别 | dashboard 桶内 | 跨桶 |
|---|---|---|
| Critical | 0 | 0 |
| High | 0 | 0 |
| **Medium** | **0** | **1**（B-5 users.js 主语） |
| Low | 0 | 3（B-6 + R3-NEW-1 + 老浏览器 A-1/A-2） |
| Info | — | 多项 |

**Dashboard 桶 R3 完全收敛闭环**。

剩余 1 Medium + 3 Low 在**其他 webui 模块**（users / commands / server_broadcast），不在 dashboard 桶职责内。建议：

- **A** 声明 dashboard 桶彻底闭环，B-5 / B-6 / R3-NEW-1 起独立 task 修复
- **B** 顺便修 B-5 + B-6 共 ~5 行文案改动（不动 dashboard 桶）
- **C** 全修（含 R3-NEW-1 defense-in-depth）

## 建议

dashboard 桶经 R1 + R2 + R3 三轮已**彻底闭环**。剩余跨桶 Medium / Low 是 R2 周边代码暴露面，建议在独立 task `webui-cross-module-copy-cleanup` 中统一修复（B-5 + B-6 + R3-NEW-1 + 老浏览器 hygiene），避免本任务再扩 scope。
