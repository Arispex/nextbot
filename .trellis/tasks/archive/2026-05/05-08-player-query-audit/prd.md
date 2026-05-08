# 审计玩家查询命令的漏洞和性能问题

## Goal

对 NextBot 的"玩家查询"分类下的 8 条命令做系统化漏洞 / 性能审计：
- 列出每条命令的潜在漏洞、性能瓶颈、并发风险、注入风险、外部 IO 风险
- 给出问题等级、影响、复现操作、推荐解决方案
- 主代理对子代理结果做二次复查后再交付

## 审计范围

文件：`nextbot/plugins/player_query.py`（974 行）

8 条命令（按行数顺序）：

| 命令 | handler | 行号 | 说明 |
|---|---|---|---|
| 在线 | `handle_online` | 168 | 列出服务器在线玩家 |
| 自踢 | `handle_self_kick` | 246 | 把自己从游戏内踢下线 |
| 用户背包 | `handle_user_inventory` | 328 | 渲染他人背包截图 |
| 我的背包 | `handle_my_inventory` | 491 | 渲染自己背包截图 |
| 我的地图 | `handle_my_map` | 616 | 渲染自己地图（含 @ 用户）|
| 用户地图 | `handle_user_map` | 704 | 渲染他人地图（带 actor 转账上下文）|
| 查看地图 | `handle_explored_map` | 814 | 渲染共同探索地图（直接返回 PNG base64）|
| 进度 | `handle_world_progress` | 897 | 渲染世界进度截图 |

## 审计关注维度

1. **并发 / 竞态**：渲染中的临时文件锁、TShock 调用与数据库写入的顺序
2. **注入 / 越权**：URL 拼接（`_to_public_render_url`）、玩家名 / 用户 ID 作为模板参数
3. **资源 / 性能**：截图渲染耗时、`temp_screenshot_path` 生命周期、并发触发是否会撑爆内存 / 磁盘
4. **可观测 / 错误传播**：异常落点、请求失败时的兜底
5. **认证 / 权限**：用户/我自查询的权限差异、是否有越权读他人数据的可能

## 验收标准

### 审计阶段（已完成）
- [x] 每条命令产出完整审计条目（问题、等级、影响、复现、方案）
- [x] 主代理对每条问题做二次复查（含读源码 + 交叉文件验证）
- [x] 结果汇总到 `research/player-query-{a,b}-findings.md` + `research/main-agent-recheck.md`

### 实施阶段（用户决策 2026-05-08）

#### 用户决定不修（1 条）
- **PQA-3.3** `/render/{type}/{token}` 无 auth：故意设计，不修

#### 用户决定：除 PQA-3.3 外全部修复（共归并为 14 个修复模块）

| # | 涉及 ID | 等级 | 概要 |
|---|---|---|---|
| 1 | PQB-3.1+3.2 | 🔴 | 查看地图：保留 guest 权限，加 ST-2.1 模板（per-server semaphore + 200MB 上限 + 早 del + 300s read） |
| 2 | PQB-1.1+2.1 | 🔴 | 我的地图 / 用户地图：加 ST-2.1 模板（同 #1） |
| 3 | PQA-3.1 + 5 分身 | 🔴 | `screenshot_temp.py` 加 `uuid.uuid4().hex[:8]` 后缀，6 处碰撞一处修全 |
| 4 | PQA-CC-3 + PQA-CC-1 | 🟡 | 抽 `nextbot/large_image.py`：`_MAX_BASE64_BYTES`、`_LONG_READ_TIMEOUT`、`_semaphore_for(dict, server_id)`，让 server_tools.py 与 player_query.py 共用 |
| 5 | PQA-1.1 + PQA-2.1 | 🟠 | 在线 / 自踢：`asyncio.gather` 并行 fan-out |
| 6 | PQB-3.4 + PQB-4.1 | 🟡 | 查看地图 timeout 300s（合并入 #1）；进度 timeout 15s |
| 7 | PQA-3.2 + PQA-4.2 | 🟡 | 用户背包 / 我的背包：per-server semaphore（容量 2-3）|
| 8 | PQA-3.4 + PQA-4.4 | 🟡 | 用户背包 / 我的背包：inventory + stats 两次 API 改并行 |
| 9 | PQA-CC-4 | 🟡 | `_to_public_render_url` 改用 `get_server_settings().public_base_url` |
| 10 | PQB-4.5 | 🟡 | 进度补非 V11 fallback |
| 11 | PQB-4.6 | 🟡 | 进度日志补 `user_id` |
| 12 | PQB-1.6 + PQB-3.7 | 🟢 | 我的地图 / 查看地图 V11 路径跳过 b64decode + write_bytes（合并入 #1 #2）|
| 13 | PQB-1.5 + PQB-2.5 + PQB-3.5 | 🟢 | 非 V11 fallback 改用 `reply_block`，不暴露 `/tmp` 路径 |
| 14 | PQA-3.6 + PQB-1.3 + PQB-2.2 | 🟢 | URL 路径段注入 defense-in-depth：interpolation 前 `quote(safe="")` |
| 15 | PQA-3.7 | 🟢 | render URL 不写日志 token |
| 16 | PQA-3.5 + PQA-4.5 + PQB-2.7 | 🟢 | TOCTOU 改名：低概率、自然 404，加注释文档化即可 |
| 17 | PQB-4.4 | 🟢 | progress dict drop non-bool 时 `logger.warning` 记录 |
| 18 | PQB-X.4 (PQB-1.4 + 2.6 + 3.6) | ℹ️ | `int(user_id)` 加 `try/except ValueError` 防御 |

### 实施阶段验收

- [ ] 上述 18 个修复模块全部落地
- [ ] **无破坏性更新**：所有命令外部行为一致（成功路径输出、@ 行为、文件名格式不变）
- [ ] **开箱即用**：本次预期无 DB schema 变化（仅 `screenshot_temp.py` 改 filename 生成 + `large_image.py` 新文件 + 6 个 handler 改 + db.py 不变）
- [ ] **失败文案符合全局规范**：`reply_failure(action, reason)` 不拼"动作 + 结果，原因"
- [ ] **修后再检查**：派 trellis-check 子代理对照 findings + recheck 再走一遍

## Out of Scope

- PQA-3.3：用户决定故意设计不修
- 其他分类的命令（已审完）
- TShock REST API 本身
- render 端点的鉴权架构改造（系统级问题，不在本任务范围）

## Technical Notes

- 主审目录：`nextbot/plugins/player_query.py`
- 配套依赖：`nextbot/screenshot_temp.py`、`nextbot/tshock_api.py`、`nextbot/db.py` 中 `User` / `Server` / `DEFAULT_GUEST_PERMISSIONS`
- 修复模板参考：`nextbot/plugins/server_tools.py`（commit `942d923` 已落地的 ST-2.1/3.3 修复）
- 计划新增：`nextbot/large_image.py`（公共 OOM 防护 helpers）
