# 审计安全管理命令的漏洞和性能问题

## Goal

对 NextBot 的"安全管理"分类下的 5 条命令做系统化漏洞 / 性能审计：
- 列出每条命令的潜在漏洞、性能瓶颈、并发风险、注入风险、外部 IO 风险
- 给出问题等级、影响、复现操作、推荐解决方案
- 主代理对子代理结果做二次复查后再交付

## 审计范围

| 文件 | 命令 | handler |
|---|---|---|
| `nextbot/plugins/security.py` (171 行) | 允许登入 | `handle_confirm_login` (line 106) |
| `nextbot/plugins/security.py` | 拒绝登入 | `handle_reject_login` (line 145) |
| `nextbot/plugins/ban.py` (315 行) | 封禁用户 | `handle_ban` (line 53) |
| `nextbot/plugins/ban.py` | 解封用户 | `handle_unban` (line 217) |
| `nextbot/plugins/ban.py` | 封禁列表 | `handle_ban_list` (line 126) |

附带依赖：`nextbot/ban_core.py` (110 行) 共享底层逻辑。

## 审计关注维度

1. **并发 / 竞态**：DB write 顺序、TShock 调用与本地状态的一致性、多次封禁同一用户的幂等性
2. **注入 / 越权**：用户名 / 用户 ID 进入 TShock 命令、QQ 号到 user_id 的解析路径
3. **资源 / 性能**：fan-out 串行（参考 PQA-1.1 / 2.1 模式）、截图渲染（封禁列表大概率走 page render）、超时分类
4. **数据一致性**：DB-API 双写（参考 W-7.x 仓库模式）—— 本地标 ban，TShock 端踢人 / 加白名单失败的兜底
5. **可观测 / 错误传播**：异常落点、是否有审计字段（操作者 / 时间 / 原因）
6. **权限边界**：guest 是否可调用、是否可越权封禁他人 / 自己

## 验收标准

### 审计阶段（已完成）
- [x] 每条命令产出完整审计条目（问题、等级、影响、复现、方案）
- [x] 主代理对每条问题做二次复查（含读源码 + 交叉文件验证）
- [x] 结果汇总到 `research/security-{a,b}-findings.md` + `research/main-agent-recheck.md`

### 实施阶段（用户决策 2026-05-08）

#### 用户决定不修

**A. 业务设计**（命令默认对 guest 开放是设计意图，保留权限）：
- SA-COMMON.1 / SB-2.1：`security.login.confirm` / `security.login.reject` / `ban.list` 保留在 `DEFAULT_GUEST_PERMISSIONS`

**B. 服务端反向同步兜底**（无永久漂移场景，无需 CRITICAL log）：
- SB-1.1 / SB-3.2 / SC-4.1：DB-API 双写不一致问题在服务端启动时会反向同步追上，机器人这边 commit 后不需要 CRITICAL log 或聚合层

**C. 用户范围 C（critical + high + medium 全修）默认排除 low / info**

#### 用户决定修复（9 个修复模块）

| # | 涉及 ID | 等级 | 概要 |
|---|---|---|---|
| 1 | SA-1.1+2.1+SB-1.3+3.4+SC-4.2 | 🟠 | 抽 `nextbot/server_broadcast.py`：asyncio.gather + per-server semaphore + 部分失败聚合，让 security.py / ban.py / ban_core 全部 fan-out 复用 |
| 2 | SB-1.2+3.5 | 🟠 | TShock URL 路径段 `quote(safe="")`（与 PQB-2.2 同形修复）|
| 3 | SB-1.4+3.3 | 🟠 | `User.is_banned` 改条件 UPDATE（与 economy 同模式 + execute_rowcount）|
| 4 | SB-2.2 | 🟠 | 封禁列表加 `large_image.MAX_BASE64_BYTES` + per-handler semaphore（**guest 权限保留**）|
| 5 | SA-1.2+1.7 | 🟡 | 错误聚合 + 部分成功 emoji 区分（⚠️ 部分 / ❌ 全失败）|
| 6 | SA-1.3+2.4+SB-1.5+3.6+SC-4.5 | 🟡 | 审计日志补 `operator_id`，含 owner_protected 拦截 logger.warning |
| 7 | SB-2.4 | 🟡 | 封禁列表改 `count() + offset/limit`，避免全表 ORM 物化 |
| 8 | SC-4.6 | 🟡 | 抽 `apply_unban_to_db` 对偶函数，重构 ban.py 解封路径走 ban_core |
| 9 | SB-3.1 | 🟡 | commit 前 capture user_name / user_qq |

### 实施阶段验收

- [ ] 上述 9 个修复模块全部落地
- [ ] **无破坏性更新**：所有命令外部行为一致（成功路径输出 / @ 行为 / 文件名格式不变）
- [ ] **开箱即用**：本次预期无 DB schema 变化（新增 `server_broadcast.py` + 改 4 个文件 + ban_core 重构）
- [ ] **失败文案符合全局规范**：`reply_failure(action, reason)` 不拼"动作 + 结果，原因"
- [ ] **修后再检查**：派 trellis-check 子代理对照 findings + recheck 再走一遍

## Out of Scope

- SA-COMMON.1 / SB-2.1（业务设计，guest 权限保留）
- SB-1.1 / SB-3.2 / SC-4.1（服务端反向同步兜底，无永久漂移）
- 其他分类的命令（已审完）
- 渲染层（`server/templates/*.html`、`server/pages/*.py`）
- TShock REST API 本身
- WebUI 中的封禁管理页（`server/routes/webui_users.py` 重复实现可以一并修，但不在本任务必修范围）

## Technical Notes

- 主审目录：`nextbot/plugins/security.py`、`nextbot/plugins/ban.py`、`nextbot/ban_core.py`
- 计划新增：`nextbot/server_broadcast.py`（公共 fan-out helper）
- 配套依赖：`nextbot/db.py` 中 `User` / `Server` / `execute_rowcount`
- 修复模板参考：
  - `player_query.py:260` PQA-1.1 修复（asyncio.gather）
  - `player_query.py:427` PQB-2.2 修复（quote safe=""）
  - `economy.py:192-205` 条件 UPDATE 修复
  - `large_image.py` 公共 OOM helper
