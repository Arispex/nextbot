# 主代理二次复查结论

**日期**: 2026-05-09
**复查范围**: lottery (21) + leaderboard (≈25) + misc (16+) ≈ 62 项

---

## 复查方法

主代理读源码验证：
- `lottery.py:570-619, 625-651` 验证 lost-update on User.coins（LO-3.1）
- `leaderboard.py:83-125` 验证 _render_and_send 缺 OOM 防护（LB-0.1）
- `leaderboard.py:1085-1086` 验证全表 ORM .all() + Python sort（LB-10.1）
- `db.py:48-64` 验证 17 个 leaderboard 权限 + system 权限默认 guest
- `group_member_notify.py:32-34` 验证 3 处 on_notice() 无 rule

---

## ✅ 真实 critical（必修）

### Lottery（抽奖系统）

| ID | 复查结论 |
|---|---|
| **LO-3.1** lottery lost-update on User.coins | ✅ 真。lottery.py:570-619 + 625-651 两个分支都是 `user.coins = current_coins - total_cost; commit()` 经典 read-modify-write，无条件 UPDATE，无 MAX_COINS_AMOUNT cap，与 economy / shop 已修模式直接相反。 |
| **LO-3.2** TOCTOU 池/奖品配置 | ✅ 真。snapshot 在 session A 取，charge 在 session B，pool.enabled / cost_per_draw / prize.enabled 不再校验。 |

### Leaderboard（排行榜）

| ID | 复查结论 |
|---|---|
| **LB-0.1** _render_and_send 缺 OOM 防护 | ✅ 真。leaderboard.py:108-122 直接 read_bytes + base64encode 无 MAX_BASE64_BYTES，无 semaphore。**17 个 leaderboard 命令默认全 guest**（db.py:48-64），是被 OOM 攻击的最大入口面。 |
| **LB-10/13/14/15/16/17** 6 个 handler 全表 ORM .all() + Python sort | ✅ 真。leaderboard.py:1085 `session.query(User).filter(User.rob_total_count > 0).all()` 后 Python sort。User 表大量历史行 + 6 个 handler × guest 高频访问 = 严重性能 / OOM 风险。 |
| **LB-3.1** 总在线时长串行 fan-out | ✅ 真。leaderboard.py:865-882 `for server in servers:` 串行 await，N 服务器单慢拖死整个命令。 |

### Misc

| ID | 复查结论 |
|---|---|
| **MI-5.1** group_member_notify 三处 on_notice() 无 rule | ✅ 真。group_member_notify.py:32-34 三个 matcher 都用裸 `on_notice()`，nonebot 的类型注解只做 dependency hint，不做 dispatch 过滤。每条 NoticeEvent 都会进入 3 个 handler 的 dispatch 阶段。 |

## ✅ 真实 high（应修）

### Lottery
| ID | 状态 |
|---|---|
| LO-3.3 全失败缺 CRITICAL log + reply head 切换 | ✅ 真，与 shop S-2.1 同形未修 |
| LO-3.4 命令 prize 部分失败无 refund 机制 | ✅ 真 |
| LO-3.5 cmd 串行 fan-out 不走 server_broadcast.broadcast | ✅ 真 |
| LO-3.6 cmd_skip_reasons 仅 V11 路径发送，其他适配器丢失 | ✅ 真 |

### Leaderboard
| ID | 状态 |
|---|---|
| LB-1.1 缺 index：coins / sign_streak / sign_total / rob_total_loss / rob_total_penalty | ✅ 真，db.py:135-167 确认无 index=True |
| LB-3.2 totals dict 无 size cap | ✅ 真 |
| LB-8.1 UserSignRecord 缺 (sign_date, created_at) 复合索引 | ✅ 真 |
| LB-2.2 远端响应大小 cap | ✅ 真，但需要 transport 层兜底 |

### Misc
| ID | 状态 |
|---|---|
| MI-1.1 / 2.1 / 3.1 三个 system 截图缺 semaphore（about / tutorial / menu）| ✅ 真，全 guest，无 ban.py / permission_manager.py 已建立的 semaphore 范式 |
| MI-1.2 / 2.2 / 3.2 三个 system 截图缺 MAX_BASE64_BYTES | ✅ 真 |
| MI-5.2 auto_ban_on_leave 未走 audit_permission_change | ✅ 真，被动事件触发 ban 是最敏感的状态变更类别，反而绕过了刚加的统一审计 helper |
| MI-5.3 _lookup_user_name_and_ban_status TOCTOU 与 apply_ban 重复 | ✅ 真，apply_ban 内已有条件 UPDATE 兜底，前置 SELECT 多余 |

## 🔧 严重度调整

| ID | 子代理评级 | 主代理评级 | 理由 |
|---|---|---|---|
| LO-3.14 N×M 命令爆炸 | 🟡 medium | 🟠 high | 配合 LO-3.5 串行 fan-out，admin 配的"全服务器命令"prize 抽 100 次 = 数百 RPC，可造成实际 DoS |
| LB-99.1 17 命令默认 guest | ℹ️ info | 🟡 medium | 是设计意图但配合 LB-0.1 + LB-10.1 形成实际 DoS 入口，应至少加 cooldown |
| MI-5.4 event 类型守卫 | 🟡 medium | 🟢 low | 当前类型注解隐式过滤已 work；MI-5.1 修复后可降为 info |

## ❌ 误判 / 不予采纳

| ID | 子代理结论 | 主代理判断 |
|---|---|---|
| MI-3.3 search_command_matcher 全表扫描 | 子代理自标 medium | 实际 list_command_configs 走内存 dict，~80 命令量级无问题。剔除。 |
| MI-5.6 自动注册并发 | 子代理已澄清 | 不存在 auto-register 代码，剔除。 |
| LB-0.5 同步 IO 阻塞 event loop | 🟢 | 全项目通用模式，与本审计目标弱相关 |

## 🔁 去重

- **截图 OOM 防护缺失**：LB-0.1 / MI-1.1+1.2 / MI-2.1+2.2 / MI-3.1+3.2 / LO-1.3 → **6+ 处同根因**，建议统一修：抽 `nextbot/screenshot_render.py:render_and_send_screenshot()` helper（含 semaphore + size cap + V11/non-V11 分支），让 leaderboard / about / tutorial / menu / lottery / ban / permission_manager 都用
- **lost-update on User.coins**：LO-3.1 → 与 economy / shop 同形，conditional UPDATE + execute_rowcount 模式直接套用
- **cmd 串行 fan-out**：LO-3.5 → 复用 `server_broadcast.broadcast`
- **全表 ORM .all() + Python sort**：LB-10/13/14/15/16/17 6 处 → 改 SQL 表达式 ORDER BY + LIMIT/OFFSET（参考 LB-1 模式）
- **缺 User 索引**：LB-1.1 6 个字段 → 一个 ensure_*_schema migration

---

## 主代理整体看法

**本批审计的核心问题集中在 4 类**：

1. **lottery.py 没跟上 economy/shop 修复** —— 4 个月前修过的 lost-update / TOCTOU / CRITICAL log / fan-out 模式，lottery 全没应用（最大冷门）
2. **leaderboard.py 是 OOM 重灾区** —— 17 个 guest 命令 + 截图无 cap + 6 个 handler 全表 ORM = 攻击面最大
3. **system 公开命令（about/tutorial/menu）OOM 防护遗漏** —— 与 ban.py / permission_manager.py 已建立的范式不一致
4. **group_member_notify 是 audit blind spot** —— 被动 ban 是最敏感操作但绕过了刚加的统一审计

**估算 fix 范围（按 trellis 拆分）**：
- 必修 critical：8 项（LO-3.1/3.2 + LB-0.1 + LB-10.1 一组 6 handler + MI-5.1 + LO-3.4/3.3 + MI-5.2）
- 建议 high：约 12 项
- medium：约 10 项

可以一次大批量修，也可以分开（lottery / leaderboard / misc 三个独立 commit）。
