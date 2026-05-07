# 小游戏系统命令安全与性能审计

## Goal

审计 `category="小游戏系统"` 的 4 个命令，找出安全漏洞、缺陷、性能瓶颈，按严重级别整理报告（不直接修复）。

## 审计范围

| 命令 | 文件 | 大致行数 |
|---|---|---|
| 1 | 猜数字 | `nextbot/plugins/guess_number.py` | 239 |
| 2 | 抢劫 | `nextbot/plugins/rob.py` | 298 |
| 3 | 掷骰子 | `nextbot/plugins/dice.py` | 216 |
| 4 | 切换抢劫保护 | `nextbot/plugins/rob_protection.py` | 107 |

总 ~860 行。这 4 个命令都直接操作 `User.coins`，是经济系统的延伸面。

## 审计维度

### 安全（最关键）
- **金币原子性**：扣 → 加 / 押注 → 中奖 / 抢成功 / 抢失败惩罚 是否在同一事务里？
- **lost-update**：与刚修复的 economy（commit 0206834）同样的并发问题
  - 押注后是否还能并发触发同一玩家再次扣钱
  - 抢劫的 attacker 押金 + victim 收益 / 退款是否原子
- **`rob_protected` 检查**：抢劫前是否检查 attacker 自己 / victim 都不是受保护状态？
- **抢劫冷却**：`last_rob_time` 检查后到 commit 之间的窗口是否能并发
- **自抢 / 自猜**：能否抢自己 / 自我下注作弊
- **整数溢出 / 上界**：押注金额是否有上界（与 economy F-Common.1 同款问题）
- **`rob_protected` 切换花费**：cost 校验完整性

### 缺陷
- **错误处理路径**：DB 异常 / 中途 bot.send 失败 / commit 失败有没有兜底
- **session try/finally close**：早 return 会跳过 close
- **猜数字的 `weight_X_to_Y` 概率配置**：从 webui 改动 weight 后并发猜数字会不会读到 stale 配置
- **抢劫的 victim 选择**：随机选时是否可能选到受保护玩家 / 自己 / 不存在的用户

### 性能
- **N+1 查询**：抢劫候选 victim 是否一次 SQL 取，还是循环 query
- **多次 session 开关**：单 handler 多次 `get_session()`（economy 已修了项目级单例，这 4 个文件是否享受到？）
- **不必要的全表扫**：`User.query.all()` 然后内存过滤

### D 与最近修复的对照（commit 011aa68 + 0206834）
- `User.name` 唯一索引：抢劫 victim / 猜数字 / 掷骰子 是否按 name 查询？已经能享受到索引
- `tshock_api.py` `quote(path)`：4 个 minigame 是否调用 server API？理论上应该不调用（纯 DB 操作）
- `temp_screenshot_path`：4 个 minigame 是否还在用旧 `Path("/tmp")` 模式
- `get_session` 单例：所有调用方自动受益，无需改

## Acceptance Criteria

- [ ] 4 个命令逐一过一遍
- [ ] 每个发现按 严重级别（🔴 必修 / 🟠 应修 / 🟡 建议 / 🟢 观察）分类
- [ ] 每个问题包含：现象描述 / 影响 / 复现步骤 / 修复方案 / 严重级别
- [ ] 误报由主代理二次复查后剔除
- [ ] 所有发现写到 `research/findings.md`，主代理读取后向用户输出最终报告

## Non-goals

- 不修复任何代码（这是审计任务）
- 不审计 经济系统 / 用户系统 / 玩家查询 / 排行榜 等（已分别审计或不在本次范围）
- 不审计 LotteryPool / RedPacket（独立分类，不在小游戏系统下）

## Definition of Done

- 输出最终报告给用户，问题已经过主代理复核
