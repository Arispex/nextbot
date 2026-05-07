# 仓库系统命令安全与性能审计

## Goal

审计 `nextbot/plugins/warehouse.py` 的 8 个 `category="仓库系统"` 命令，找出安全漏洞、缺陷、性能瓶颈，按严重级别整理报告（不直接修复）。

## 审计范围

| 命令 | display_name | command_key | 大致行号 |
|---|---|---|---|
| 1 | 我的仓库 | `warehouse.list_self` | 263–297 |
| 2 | 用户仓库 | `warehouse.list_user` | 297–343 |
| 3 | 添加仓库物品 | `warehouse.add` | 343–504 |
| 4 | 删除仓库物品 | `warehouse.remove` | 504–689 |
| 5 | 丢弃仓库物品 | `warehouse.drop_self` | 689–861 |
| 6 | 回收仓库物品 | `warehouse.recycle_self` | 861–1145 |
| 7 | 领取仓库物品 | `warehouse.claim_self` | 1145–1413 |
| 8 | 赠送仓库物品 | `warehouse.gift_self` | 1413–1700 |

总 1700 行。**仓库系统是高并发场景**：领取 / 赠送 / 回收涉及金币与物品的多步原子操作；丢弃涉及调用 TShock API；操作既动金币又动 item 表。

## 审计维度

### 安全（最关键）

**金币与物品双重原子性**：
- 回收：扣物品 + 加金币 是否同事务原子？lost-update？
- 领取：扣物品 + 调 TShock /v3/server/give 给玩家发物品 —— 中途失败如何回滚？是否会出现"DB 已扣但游戏未给"或"DB 未扣但游戏已给"
- 赠送：扣 sender 物品 + 加 receiver 物品 是否原子？sender 余额（如有手续费）lost-update？

**WarehouseItem 表的并发竞态**：
- 领取 / 丢弃 / 回收 / 赠送 都是 read-modify-write WarehouseItem：select → 检查归属 / 数量 → update / delete → commit
- 同一 item 被 user 并发触发两次"丢弃"会怎样？双重消费 / NoneType / 超卖？
- claim_count 类字段是否 lost-update（与金币 lost-update 同根）

**越权 / 业务逻辑**：
- `用户仓库` 是否限制只能 admin 查别人的仓库？还是 guest 也能查？
- `添加仓库物品` / `删除仓库物品` 是否限制 admin 才能操作？（这俩 likely admin-only 命令，给玩家发物品 / 收物品）
- 玩家能不能领取别人的仓库物品（slot_index 不匹配自己）
- 赠送时 sender 不是 caller 怎么办

**TShock API 错误处理**：
- 领取 / 丢弃 调用 TShock API 给游戏发物品 / 删物品
- API 失败时是否正确回滚 DB（避免双花 / 单花）
- 玩家不在线时如何处理

**输入校验 / 上界**：
- slot_index / quantity 等字段的边界
- 物品 prefix / netId 等字段的合法性
- 金币上界（与 economy F-Common.1 同款，如有金币交互）

### 缺陷

- 错误处理路径完整性
- session try/finally close
- TShock API 调用与 DB 写入的事务边界
- WarehouseItem 删除是 DELETE 还是软删除？slot 复用规则？
- gift 多步操作中途失败的回滚（sender 已扣但 receiver 未加）

### 性能

- N+1 查询：列表命令是否一次 SQL 取
- `_find_empty_slot` 类 helper 是否每次全表扫
- 多次 session 开关（economy 修过项目级单例，自动受益）
- 大量 item 时的渲染性能

### D 与最近修复（commit 011aa68 / 0206834 / fe11241 / 6ca05b8 / ec42714）的对照

- `User.coins` 加减是否还在用 lost-update 模式？should be 条件 UPDATE
- `MAX_COINS_AMOUNT = 100_000_000` 是否复用？
- 异常兜底是否覆盖？
- `_safe_param_int` helper 是否使用？
- `temp_screenshot_path`：仓库列表渲染（warehouse.py:229 已迁移）
- `execute_rowcount` helper 已就绪，仓库可用于条件 UPDATE 后的 rowcount 校验

## Acceptance Criteria

- [ ] 8 个命令逐一过一遍
- [ ] 每个发现按 严重级别（🔴 必修 / 🟠 应修 / 🟡 建议 / 🟢 观察）分类
- [ ] 每个问题包含：现象描述 / 影响 / 复现步骤 / 修复方案 / 严重级别
- [ ] 误报由主代理二次复查后剔除
- [ ] 所有发现写到 `research/findings.md`，主代理读取后向用户输出最终报告

## Non-goals

- 不修复任何代码（这是审计任务）
- 不审计其他 category（已分别审计）

## Definition of Done

- 输出最终报告给用户，问题已经过主代理复核
