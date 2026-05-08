# 主代理二次复查结论

**日期**: 2026-05-08
**复查范围**: player-query-a-findings.md (14 项) + player-query-b-findings.md (33 项)

---

## 复查方法

主代理逐项读源码 + 实测 Python 行为：
- 验证 `screenshot_temp.py:26` + `time_utils.py:9` 的秒精度碰撞机制
- 验证 `db.py:69-76` 的 `DEFAULT_GUEST_PERMISSIONS`（确认 `player_query.map.explored` 在内）
- 验证 `server/routes/render.py` 的 9 个 render endpoint 都没有 auth 中间件
- 实测 `urllib.parse.quote("foo/bar", safe="/")` 不编码 `/`

---

## ✅ 真实 critical（必修）

| ID | 复查结论 |
|---|---|
| **PQB-3.1 + PQB-3.2** 查看地图 OOM + guest 权限 | ✅ 真。`db.py:72` 确认 `player_query.map.explored` 在 DEFAULT_GUEST_PERMISSIONS；handler 无 semaphore / 无 200MB 上限 / 无早释放。**任何 guest 可让 bot OOM 的单一最严重问题**。 |
| **PQB-1.1 / PQB-2.1 / PQB-3.1** 三个地图 handler 缺 ST-2.1/3.3 修复模板 | ✅ 真。我的地图 / 用户地图 / 查看地图都直接读后端 base64，无任何防护，比刚修过的全亮地图更危险（base64 在 `b64_string`、`png_bytes`、`MessageSegment` 三处共存）。 |
| **PQA-3.1 = PQB-X.3 + 5 个分身** temp 文件秒精度碰撞 | ✅ 真。已实测 `_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"`，同秒同 prefix 必碰撞。`finally` 的 `unlink(missing_ok=True)` 会删另一并发请求的文件 → 数据级风险。**6 处碰撞同根因**，改 `screenshot_temp.py` 一处修全。|

## ✅ 真实 high（应修）

| ID | 复查结论 |
|---|---|
| **PQA-3.3** `/render/inventory/{token}` 无 auth | ✅ 真。`server/web_server.py` 的 `add_webui_auth_middleware` 仅覆盖 `/webui/*`，render 路由独立。token 是 uuid4 (128bit 不可猜)，但 `PAGE_EXPIRE_SECONDS=600` 内任意拿到 URL 的人可读。是**系统级**问题（影响所有 9 个 render endpoint）。 |
| **PQB-3.4** 查看地图 timeout=30s 太短 | ✅ 真。Large 世界 explored region 渲染常 60-120s，需 `_LONG_READ_TIMEOUT`（参见 ST-2.2 修复）。 |
| **PQA-1.1 / PQA-2.1** 在线/自踢 串行 fan-out | ✅ 真。N 个服务器 → N × (5s connect + 5s read) 总 wall time。`asyncio.gather` 即可。|

## 🔧 严重度调整

| ID | 子代理评级 | 主代理评级 | 理由 |
|---|---|---|---|
| **PQA-3.2 / PQA-4.2** 背包缺 semaphore | 🟠 high | 🟡 medium | 背包是 Playwright 截图（小 PNG）非大 base64，OOM 风险远低于地图。仍建议加 semaphore，但严重度下调。 |
| **PQB-X.2 / PQB-1.3 / PQB-2.2 / PQA-3.6** user.name 路径注入 | 🟠 high | 🟢 low | `_validate_user_name` 正则 `[A-Za-z0-9一-鿿]+` 已锁死 `/` 字符。仅在 SQL 直接写脏数据时可触发，纯 defense-in-depth。 |
| **PQB-X.4 / PQB-1.4 / PQB-2.6 / PQB-3.6** `int(user_id)` 兼容性 | 🟡 medium | ℹ️ info | 项目目前仅 OBV11 → user_id 一定是数字。前序 ST-1.5 同处理。 |
| **PQB-X.6 / PQB-1.5 / PQB-2.5 / PQB-3.5** /tmp 路径泄漏 | 🟢 low | 🟢 low | 但仅非 V11 分支命中，V11 是唯一在用分支。优先级低。 |
| **PQA-3.5 / PQB-2.7** TOCTOU 玩家改名 | 🟡 medium | 🟢 low | 改名极低频，且 TShock 端会 404 自然处理。 |

## ❌ 误判 / 不予采纳

| ID | 子代理评级 | 主代理结论 |
|---|---|---|
| **PQB-2.4** name_ambiguous 错误信息不准 | 🟡 medium | 子代理自己改口"informational, not a bug"。剔除。|
| **PQB-1.6 / PQB-3.7** V11 浪费 decode + disk write | ℹ️ info | 真实但与"漏洞 / 性能"目标弱相关。OOM 修复（PQB-1.1）顺带处理（V11 分支跳过 write_bytes）。可合并到 PQB-1.1。 |
| **用户地图含转账金币** | — | 子代理已澄清：handle_user_map 无任何 coin 操作。前序 session 摘要中的"actor_user_id transfer balance"是当时讨论的另一处，与此命令无关。 |

## 🔁 去重

- 6 处 temp 文件碰撞 (PQA-3.1 / PQA-4.1 / PQB-1.2 / PQB-2.3 / PQB-3.3 / PQB-4.2) → **1 个根因**，改 `screenshot_temp.py` 一处修全
- 3 处地图 OOM (PQB-1.1 / PQB-2.1 / PQB-3.1) → **1 个修复模板**，复用 ST-2.1 的 `_MAX_BASE64_BYTES` / `_LONG_READ_TIMEOUT` / per-server semaphore（建议提取到 `nextbot/large_image.py` 公共模块）
- 4 处 timeout 偏短 (PQB-1.1 / PQB-2.1 / PQB-3.1 / PQB-3.4) → 同步纳入 OOM 修复
- 多处 path injection (PQA-3.6 / PQB-1.3 / PQB-2.2) → user.name 已锁，可统一一行 `quote(safe="")` 加固

---

## 主代理整体看法

1. **本次审计的"真正大问题"集中在 3 条**：
   - **查看地图 = guest 权限 + 无 OOM 防护**（PQB-3.1+3.2）：单一最严重，guest 可 OOM bot
   - **三个地图 handler 全缺 ST-2.1 模板**（PQB-1.1/2.1/3.1）：可批量修
   - **temp 文件秒精度碰撞**（PQA-3.1 等 6 处）：源头一处修全

2. **render 端无 auth 是系统级架构问题**（PQA-3.3）：影响所有 9 个 render endpoint，不只 inventory。本次可先做"在 send_link=True 时把 PAGE_EXPIRE_SECONDS 缩到 60s"等缓解，长期方案需要单独任务。

3. **设计性建议**：把 `_MAX_BASE64_BYTES` / `_LONG_READ_TIMEOUT` / per-server semaphore / `_safe_*_name` 抽到 `nextbot/large_image.py`，让 `server_tools.py` 与 `player_query.py` 都用同一模板。

4. **背包 / 进度风险显著低于地图**：是 Playwright 渲染的小 PNG，没有大 base64。
