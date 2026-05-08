# 商店系统命令安全与性能审计

## Goal

审计 `nextbot/plugins/shop.py` 的 3 个 `category="商店系统"` 命令，找出安全漏洞、缺陷、性能瓶颈，按严重级别整理报告（不直接修复）。

## 审计范围

| 命令 | display_name | command_key | 大致行号 |
|---|---|---|---|
| 1 | 商店列表 | `shop.list` | 132–237 |
| 2 | 查看商店 | `shop.view` | 237–376 |
| 3 | 购买商品 | `shop.buy` | 376–660 |

总 660 行。**购买商品**是同时操作 `User.coins` + `WarehouseItem`（或 TShock API 直接发物品）+ 库存（如有）的多重原子性挑战，是审计重点。

## 审计维度

### 安全（最关键）

**金币原子性（与之前所有审计同根）**：
- 购买商品扣 buyer.coins 是否原子？read-modify-write 模式吗？
- lost-update：与同时被转账 / 抢红包 / 签到 / 抢劫等共存
- **预期**：与已修的 economy / red_packet / warehouse 同模式，需要条件 UPDATE

**金币-物品双重原子性**：
- 购买后给物品的方式：直接调 TShock `/give`？还是写 WarehouseItem？还是发库存？
- DB-API 双重一致性窗口（与 warehouse W-7.1 同根）：钱已扣但物品未到 / 物品已到但 DB 未扣
- 中途失败的回滚

**库存 / 限购**：
- 商店物品有库存限制吗？库存递减是否原子？lost-update 会导致超卖
- 限购（限制 N 个 / 周 / 玩家）是否有 race
- 同一商品被 N 玩家并发购买，库存是否守恒

**越权 / 业务逻辑**：
- 商店是否限制只能在群里购买？
- 玩家能不能购买已下架的商品（race condition）
- 用户能否绕过价格直接拿物品

**输入校验 / 上界**：
- quantity（购买数量）上界
- 单笔总价 ≤ MAX_COINS_AMOUNT
- 物品 prefix / netId 等

### 缺陷

- 错误处理路径完整性（API 失败 / DB 异常）
- session try/finally close
- TShock API 调用与 DB 写入的事务边界
- 部分成功 / 部分失败
- 商店列表 / 查看商店的截图临时文件清理（已知 shop.py 用 `temp_screenshot_path`）

### 性能

- N+1 查询
- 多次 session 开关（economy 已修单例）
- 列表命令的渲染性能

### D 与最近修复（commit 011aa68 / 0206834 / fe11241 / 6ca05b8 / ec42714 / 8d5ba4d）的对照

- `User.coins` 加减是否还在用 lost-update 模式？should be 条件 UPDATE
- `MAX_COINS_AMOUNT = 100_000_000` 是否复用？
- 异常兜底是否覆盖？
- `_safe_param_int` helper 是否使用？
- `temp_screenshot_path`：shop.py 已迁移
- `execute_rowcount` helper 已就绪
- `User.name` 唯一索引：自动受益
- 商店买物品入仓库（如有）：复用 `_find_empty_slots` 模式

## Acceptance Criteria

- [ ] 3 个命令逐一过一遍
- [ ] 每个发现按 严重级别（🔴 必修 / 🟠 应修 / 🟡 建议 / 🟢 观察）分类
- [ ] 每个问题包含：现象描述 / 影响 / 复现步骤 / 修复方案 / 严重级别
- [ ] 误报由主代理二次复查后剔除
- [ ] 所有发现写到 `research/findings.md`

## Non-goals

- 不修复任何代码（这是审计任务）
- 不审计其他 category（已分别审计）

## Definition of Done

- 输出最终报告给用户，问题已经过主代理复核
