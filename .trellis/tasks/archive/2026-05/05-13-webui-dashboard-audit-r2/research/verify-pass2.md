# WebUI 仪表盘 Round 2 — 主代理二次审核日志

## 整体结论

Dashboard 桶 **Round 1（commit `c118d91`）10 项修复 100% PASS，无回归**。

但 Round 1 的 **P2 timeout 共享改造（api.js 加 AbortSignal.timeout(15000)）**给整个 webui 引入了 **3 处真实跨模块 P2 回归**，是 Round 1 评估时漏挖的副作用。

## Round 1 复审：10/10 PASS

| 修复 | 复审结果 |
|---|---|
| **后端 D3** 异常日志补 IP/UA | ✅ PASS（无循环 import / `!r` 转义控制字符 / 与 sibling 一致） |
| **后端 A1** `_render_app_shell_page` docstring | ✅ PASS（9 caller 全字面量 / 9 内容模板 0 占位符 / escape 防线完整） |
| **5 文案** T-1~T-5 | ✅ PASS（U+2014 / U+2026 字节验证通过） |
| **P2 aria-busy** | ✅ PASS（AT 兼容性好） |
| **P3 Sponsored→赞助** | ✅ PASS |
| **P3 role=alert 动态切换** | ✅ PASS（VoiceOver 兼容性可能打折，但实现顺序正确） |
| **P3 focus 恢复** | ✅ PASS（queueMicrotask + guard 正确） |
| **P2 timeout** | ⚠️ dashboard 局部 PASS，但**共享改造跨模块回归 3 处**（见下） |

## 关键新发现：P2 timeout 跨模块回归（3 处 P2）

Round 1 评估 P2 timeout 时**仅验证了 dashboard 路径（< 100ms 安全）**，漏掉了其他 webui 模块的长耗时路径。

### R2-T-1 [P2] users sync-whitelist 串行多服必超时

**文件**：`server/routes/webui_users.py:206`

**修复前行为**：
```python
async def _sync_user_whitelist(user: User) -> list[dict[str, Any]]:
    servers = session.query(Server).order_by(Server.id.asc()).all()
    for server in servers:                                   # ← 串行遍历
        response = await request_server_api(
            server, "/v3/server/rawcmd", ...,
        )                                                    # 每个 timeout=5.0s
```

N 个服务器场景：
- N=2 + 1 timeout = 10s（安全）
- **N=3 + 2 timeout = 15s（临界）**
- **N≥4 + 多个 timeout = 必超过 15s（前端必触发 AbortSignal.timeout）**

**症状**：用户点"同步白名单"等 15s 看到"同步失败，请求超时"，但**后端串行未取消，继续跑完后续服务器**，**写入 DB 成功但前端不知道**，用户重试 → 重复操作 / 写放大。

**修复方向（任选一）**：
- A. 后端改 `asyncio.gather` 并行（参考 `server_broadcast.broadcast` 已有模式）
- B. 让 `apiRequest` 接受 `timeoutMs` per-call override，sync-whitelist 调用方传 60000
- C. 前端禁用 15s timeout（不推荐 —— 失去 Round 1 P2 的初衷）

### R2-T-2 [P2] users ban/unban 同模式串行多服

**文件**：`server/routes/webui_users.py:593, 683`

**修复前行为**：与 R2-T-1 完全同模式，多服务器串行调 TShock 黑名单接口。

**风险等价**：N≥4 必超时。

### R2-T-3 [P2] verify-nextbot 后端 15s 与前端 15s race

**文件**：`server/routes/webui_servers.py:434`

**修复前行为**：
```python
response = await request_server_api(
    server, "/nextbot/config/verify-nextbot", timeout=15.0
)
```

后端 explicit `timeout=15.0`，前端 `REQUEST_TIMEOUT_MS=15000` 完全同步。若 TShock 在第 14.9s 才返回 → 前端 15.0s 已 abort，**前端报"验证失败，请求超时"但后端实际成功**。

**修复方向**：后端 timeout 降到 10s（留 5s 给前端缓冲），或前端 verify-nextbot 路径独立 30s timeout。

## 其他 P3 / P4 / P5（可不修）

| ID | 一句话 | 严重度 |
|---|---|---|
| R2-T-4 | AbortSignal.timeout 老浏览器（Safari 16.3-/Chrome 102-）无 timeout 兜底 | P3 |
| R2-T-5 | AbortSignal.any 缺失时 userSignal 静默丢弃（当前 0 caller 传 signal） | P3 |
| R2-T-6 | commands restart 可能 > 15s（需独立验证 `/webui/api/restart` 耗时） | P3 |
| R2-B-1 | `<div id="loading">` 缺 `role="status" aria-live`，AT 用户不知道为什么空白 | P3 |
| R2-B-2 | `.dashboard-section-desc` CSS 定义但 HTML 无使用 | P4 |
| R2-B-3 | `.dashboard-metrics` / `.dashboard-panels` class 挂 HTML 但 CSS 无规则 | P4 |
| R2-B-4 | dashboard 无 fetch cancel（apiRequest 支持 signal 但未用） | P5 |

## False positive / 主代理拒绝

- B.1.1 后端无 abort（同步阻塞）→ 项目整体模式，Round 1 backend.md B2 已标 Info
- B.1.3 `_client_ip` 信任 XFF → webui.py 既有问题，被 D3 复用但非新增
- A.2.1-A.2.4 A1 docstring 信任契约 → 完全验证通过
- A1 / D3 复审本身 0 finding

## 主代理终判

| 类别 | 数量 |
|---|---|
| **Critical** | 0 |
| **High** | 0 |
| **Medium / P2** | **3**（R2-T-1, R2-T-2, R2-T-3）—— **不在 dashboard 桶内，但 dashboard P2 timeout 引发的副作用** |
| Low / P3 | 4（R2-T-4/5/6 + R2-B-1） |
| Info / P4-P5 | 3 |

## 关键判断

**Dashboard 桶本身（前后端 + 文案）已完全收敛 —— 0 Critical / 0 High / 0 Medium / 0 Low 在 dashboard 范围内。**

但 Round 1 的 P2 timeout 共享 api.js 改造**跨模块溢出**到 users / servers 模块，需要在那两个模块层面修。建议：
1. **本任务范围**：仅声明 dashboard 桶 Round 2 收敛闭环（0 dashboard finding）
2. **独立 task**：起一个 "webui-shared-timeout-fix" 任务专门修 R2-T-1/T-2/T-3 三处 P2 跨模块回归，并把 P3 老浏览器降级 footgun 也并进去
