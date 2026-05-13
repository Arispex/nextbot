# audit: nextbot 基础设施层（plugins 外）漏洞与性能审计

## Goal

对 `nextbot/` 下、`nextbot/plugins/` 之外的所有基础设施层 `.py` 文件做一次系统性漏洞 / 性能审计。这些文件在之前 6 轮 plugin sweep 中只是被动修改（修复 plugin 问题的副产物），从未作为主目标系统审过。本轮把它们当一等公民审一遍，关注点和 plugin 不同：基础设施层主要看 API 契约稳定性、错误传播、并发 / 资源管理、init / shutdown lifecycle、循环 import、API 边界泄漏，而不是业务逻辑 / 权限漂移类。

## Scope

**审计范围（20 个文件）：**

1. **bot.py**（仓库根，NoneBot 启动入口）

2. **DB & 持久化 & 锁层**
   - `db.py`（核心，35KB，BEGIN IMMEDIATE 监听器 / 17+ ensure_*_schema / 双重 init 路径）
   - `warehouse_lock.py`
   - `screenshot_temp.py`

3. **权限 & 审计 & 访问控制**
   - `permissions.py`（owner / dangerous-key blocklist / 继承环 / registry 校验）
   - `access_control.py`（get_owner_ids）
   - `audit.py`（audit_permission_change）
   - `ban_core.py`（10KB，封禁内核）

4. **HTTP / IO / 外部边界**
   - `tshock_api.py`（TShock REST 调用 + 错误归一化）
   - `large_image.py`（base64 200MB cap + per-server semaphore + LONG_READ_TIMEOUT）
   - `server_broadcast.py`（多服广播 + per-server semaphore）
   - `server_validation.py`
   - `screenshot_render.py`（Playwright 渲染 + 0-byte 防御）

5. **Utils / Misc**
   - `command_config.py`（33KB，权限 registry / 命令注册元数据）
   - `message_parser.py`
   - `text_utils.py`（safe_at_segment / at_prefix）
   - `time_utils.py`
   - `progression.py`
   - `stats.py`
   - `data_dir.py`

**显式排除：**
- `nextbot/plugins/` 已审 6 轮，本轮不再覆盖
- `server/`（WebUI / 模板）—— 用户已声明独立任务
- 测试目录、迁移脚本

## Requirements

1. **覆盖率**：20 个目标文件每一个都必须有明确的"审过 → 结论"记录（即使结论是"无问题"）。
2. **分桶并行**：研究阶段拆 4 个 `trellis-research` 子代理并行跑，每桶按职责切分（DB / Permission / IO / Utils），子代理把发现 persist 到 `research/*.md`。
3. **二次审核**：每个子代理报告的"高 / 中"严重度发现，主代理必须独立 `Read` 验证（行号、代码上下文、claim 是否真实），不能照搬。
4. **报告口径**：最终给用户的报告必须按以下模板呈现：
   - 问题编号（沿用 X.N 格式）
   - 严重度（Critical / High / Medium / Low / Info）
   - 文件 + 行号
   - **修复前行为**（具体 trigger 条件 + 后果）
   - **修复后行为**（描述改完之后会变成什么样）
   - 影响范围 / 触发概率评估
5. **POLA**：发现的 false positive（子代理误报）必须显式标注并解释为什么不是问题。
6. **不直接修**：本轮先出报告，由用户决定修复范围后再走 trellis-implement / trellis-check / commit。

## Acceptance Criteria

- [ ] 20 个目标文件全部覆盖
- [ ] 4 个 trellis-research 子代理产物落到 `research/{db,permission,io,utils}.md`
- [ ] 主代理二次审核日志（针对高 / 中严重度）落到 `research/verify-pass2.md`
- [ ] 最终报告按问题列表 + 严重度排序 + 修复前 / 后效果对照呈现给用户
- [ ] 用户确认修复范围后，下游走标准 trellis-implement / trellis-check 路径

## Out of Scope

- **本轮不修代码**（先报告，user 选 fix scope）
- WebUI 同步审计（独立任务）
- plugins 命令层（已 6 轮 sweep 闭环）
- 新功能需求（如 audit_economy_change helper、DB CHECK constraint 等历史 pending 任务）

## Technical Notes

- 关注点参考：API 契约稳定性、错误传播、并发安全（asyncio.gather + return_exceptions）、资源管理（连接 / 信号量 / 文件句柄泄漏）、init / shutdown lifecycle、循环 import 风险、敏感信息日志泄漏、SQL 注入 / 命令注入边界、超时设置完整性、空值 / 边界值处理、类型安全
- 不关注点：plugin 业务逻辑、权限漂移、cap-stats 类（plugin 域）
- 之前 6 轮 sweep 已修过 / 已审过的代码不重复挖（如 `permissions.py` 的 `is_dangerous_permission` 通配匹配逻辑、`db.py` 的 BEGIN IMMEDIATE 监听器）；但**这些代码周边没审过的部分**仍在本轮范围
