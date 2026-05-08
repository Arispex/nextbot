# 审计服务器工具命令的漏洞和性能问题

## Goal

对 NextBot 的"服务器工具"以及相关"服务器管理"分类下的命令进行系统化漏洞 / 性能审计：
- 列出每条命令的潜在漏洞、性能瓶颈、并发风险、注入风险、外部 IO 风险
- 给出问题等级、影响、复现操作、推荐解决方案
- 主代理对子代理结果进行二次复查后再交付

## 审计范围

### 主线：分类 = 服务器工具
- `server_tools.execute` — 执行 `<服务器 ID> <TShock 命令>` （`nextbot/plugins/server_tools.py`）
- `server_tools.map_image` — 全亮地图 `<服务器 ID>`（`nextbot/plugins/server_tools.py`）
- `server_tools.download_map` — 下载地图 `<服务器 ID>`（`nextbot/plugins/server_tools.py`）
- `server.send` — 发送 `<服务器 ID> <消息内容>`（`nextbot/plugins/server_send.py`，category="服务器工具"）

### 副线：分类 = 服务器管理（顺手覆盖）
- `server.add` — 添加服务器
- `server.delete` — 删除服务器（含 ID 重排逻辑）
- `server.list` — 服务器列表
- `server.test` — 测试连通性

## 审计关注维度

1. **并发 / 竞态**：read-modify-write、唯一约束、ID 分配、TOCTOU
2. **注入 / 越权**：TShock 原始命令拼接、/say 注入、路径穿越
3. **资源 / 性能**：HTTP 超时、大文件 base64 in-memory、`/tmp` 文件清理、N+1 查询
4. **可观测 / 错误传播**：异常落点、返回内容截断、敏感信息泄漏（token / IP）
5. **数据一致性**：删除服务器对外键引用（如 UserServer、ShopItem 等）的影响

## 验收标准

### 审计阶段（已完成）
- [x] 每条命令产出完整审计条目（问题、等级、影响、复现、方案）
- [x] 主代理对每条问题做二次复查（含读源码 + 交叉文件验证）
- [x] 结果汇总到 `research/server-tools-findings.md`、`research/server-manager-findings.md`、`research/main-agent-recheck.md`

### 实施阶段（待执行 — 用户决策 2026-05-08）

#### 用户决定不修（11 条）
- 业务设计：SM-2.1 / SM-2.2 / SM-2.4（renumber 是有意保持 ID 连续）
- SM-2.x 衍生：SM-4.1（renumber 锁表）/ SM-4.2（PRAGMA foreign_keys 当前无 FK 声明，启用无效）
- 用户排除：ST-1.1（命令回显）/ ST-2.5（动词"查询"是本质动作）/ ST-2.6（结果服务器级，无需 at）/ ST-4.1（/say 全角钓鱼）/ SM-1.4（name 唯一性）/ SM-3.2（测试错误信息）

#### 用户决定修（21 条，按修复模块归并）

| # | ID | 等级 | 概要 |
|---|---|---|---|
| 1 | ST-3.1 | 🔴 | 下载地图 fallback 路径遍历 → 文件名白名单清洗 |
| 2 | ST-2.1 + ST-3.3 | 🔴 | 大对象 OOM → per-server semaphore + 长度上限 + 拿到后立刻 del |
| 3 | SM-1.1 + SM-1.2 + SM-3.1 | 🔴 | 添加服务器 ID 改 `max(id)+1` + IntegrityError 捕获 + reply_failure |
| 4 | ST-2.2 + ST-3.4 + ST-1.4 + ST-4.3 | 🟠/🟡 | `request_server_api` 暴露 read 超时；地图 / 下载传 read=300；执行/发送传 timeout |
| 5 | ST-3.5 | 🟠 | OneBot upload `name` 白名单清洗 |
| 6 | ST-2.3 | 🟡 | base64 长度上限 200MB |
| 7 | ST-2.4 | 🟡 | 非 V11 fileName 泄漏 → 清洗后再展示 |
| 8 | ST-3.6 | 🟡 | fallback 暴露 /tmp 路径 → 仅展示文件名 + 大小 |
| 9 | ST-3.7 | 🟡 | fallback 改用 `reply_success/reply_block` |
| 10 | ST-4.2 | 🟡 | 发送 content 长度上限 200 字 |
| 11 | ST-5.3 | 🟡 | `Server.__repr__` 屏蔽 token |
| 12 | ST-5.4 | 🟡 | `at_prefix` helper 入 `text_utils.py` |
| 13 | SM-1.3 + SM-1.5 | 🟡/🟢 | 抽 `_validate_server_payload` 到公共 helper（含拒绝换行） |
| 14 | ST-1.3 | 🟢 | 执行命令强制 `/` 前缀 |
| 15 | ST-3.8 | 🟢 | 下载地图日志补 user_id / group_id / size |
| 16 | ST-4.5 | 🟢 | 去掉多余 f-string |
| 17 | ST-5.5 | 🟢 | `server_id <= 0` 拒绝 |

### 实施阶段验收

- [ ] 21 条修复点全部落地
- [ ] **无破坏性更新**：所有命令外部行为保持一致（成功路径输出格式不变；失败路径文案符合全局规范，不拼接"动作 + 结果，原因"由前端组合）
- [ ] **开箱即用**：如涉及 DB schema 变化，沿用前序审计 `ensure_*_schema` 模式（启动时检查 + 自动迁移 + 失败兜底）
- [ ] **修后再检查**：实施完成后派发 trellis-check 子代理对照本 PRD + findings 再走一遍审计，确认本次修复覆盖完整且无新引入问题
- [ ] 影响到的 schema / migrations 在 `db.py` 内一并处理

## Out of Scope

- WebUI 中对服务器管理页的进一步审计
- TShock REST API 本身的漏洞
- 已确认"业务设计"或"用户排除"的 11 条问题

## Technical Notes

- 主审目录：`nextbot/plugins/server_tools.py`、`nextbot/plugins/server_send.py`、`nextbot/plugins/server_manager.py`
- 配套依赖：`nextbot/db.py` 中 `Server` 模型、`UserServer` 等可能存在的关联模型；`nextbot/tshock_api.py`
- 历史经验：前序 7 个分类审计中频繁出现 lost-update + DB-API 双写一致性 + TOCTOU 三类问题，但本分类逻辑较薄、写库较少，预期主要风险点在外部 IO 与命令注入
