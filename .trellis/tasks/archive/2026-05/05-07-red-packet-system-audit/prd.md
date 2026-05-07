# 红包系统命令安全与性能审计

## Goal

审计 `nextbot/plugins/red_packet.py` 的 5 个 `category="红包系统"` 命令，找出安全漏洞、缺陷、性能瓶颈，按严重级别整理报告（不直接修复）。

## 审计范围

| 命令 | display_name | command_key | 行号 |
|---|---|---|---|
| 1 | 发红包 | `economy.red_packet.send` | 86–217 |
| 2 | 抢红包 | `economy.red_packet.grab` | 218–333 |
| 3 | 收回红包 | `economy.red_packet.withdraw` | 334–447 |
| 4 | 我的红包 | `economy.red_packet.list_own` | 448–528 |
| 5 | 红包列表 | `economy.red_packet.list_all` | 529–621 |

**红包系统是高并发场景**：抢红包多人争抢同一红包是核心业务场景，原子性 / lost-update / 重复抢 是审计重点。

## 审计维度

### 安全（最关键）
- **金币原子性**：发红包扣钱 / 抢红包加钱 / 收回红包退款 是否原子？
- **抢红包并发**：N 个用户同时抢同一红包，会不会
  - 同一用户重复抢成功（破规则）
  - 总抢到额超出红包总额（金币凭空产生）
  - 红包余额变负
  - 先抢完毕但后到的请求拿到 stale 余额仍能成功
- **金币 lost-update**：与 economy / minigame 同根问题
- **越权**：
  - 非创建者能不能 收回别人的红包
  - 不在群里的人能不能抢群红包
- **过期红包处理**：发出后过了 X 分钟，未抢完的金额是否能被退回？退回逻辑是否原子？
- **整数溢出 / 上界**：发红包金额 / 个数是否有上界（与 economy F-Common.1 同款）
- **金额分配算法**：随机分配是否存在数值不平衡（如最大 / 最小）边界 bug

### 缺陷
- **错误处理路径完整性**
- **session try/finally close**
- **抢红包后通知**：成功 / 已被抢光的文案
- **过期红包自动退款**：是否有定时任务 / 启动检查 / 还是被动触发
- **edge cases**：金额为 0 / 个数为 0 / 群 vs 私聊

### 性能
- **N+1 查询**：列表命令是否一次 SQL 取
- **多次 session 开关**：单 handler 多次 `get_session()`（economy 修了项目级单例，自动受益）
- **不必要的全表扫**

### D 与最近修复（commit 011aa68 / 0206834 / fe11241）的对照
- `User.coins` 加减是否还在用 lost-update 模式？should be conditional UPDATE
- `MAX_COINS_AMOUNT = 100_000_000` 是否复用？
- 异常兜底是否覆盖？
- `_safe_param_int` helper 是否使用？
- `User.name` 唯一索引：抢红包按 user_id 查询，自然受益
- `temp_screenshot_path`：grep 后已知 `red_packet.py:415` 有截图文件，已迁移到 temp 路径

## Acceptance Criteria

- [ ] 5 个命令逐一过一遍
- [ ] 每个发现按 严重级别（🔴 必修 / 🟠 应修 / 🟡 建议 / 🟢 观察）分类
- [ ] 每个问题包含：现象描述 / 影响 / 复现步骤 / 修复方案 / 严重级别
- [ ] 误报由主代理二次复查后剔除
- [ ] 所有发现写到 `research/findings.md`，主代理读取后向用户输出最终报告

## Non-goals

- 不修复任何代码（这是审计任务）
- 不审计其他 category（已分别审计）

## Definition of Done

- 输出最终报告给用户，问题已经过主代理复核
