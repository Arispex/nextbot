# Journal - arispex (Part 4)

> Continuation from `journal-3.md` (archived at ~2000 lines)
> Started: 2026-05-16

---



## Session 155: dice 加 win_rate 概率控制（默认 50%，仅大/小，豹子保留自然）

**Date**: 2026-05-16
**Task**: dice 加 win_rate 概率控制（默认 50%，仅大/小，豹子保留自然）
**Branch**: `main`

### Summary

dice.py 加 win_rate 参数（0-100，默认 50，label「大/小 命中率」）。算法：模块加载预计算 4 个 set（WIN_BIG=105/LOSE_BIG=111/WIN_SMALL=105/LOSE_SMALL=111）；choice=大/小 时按 random() < win_rate/100 决定从 win_set 还是 lose_set 采样；choice=豹子 保留 3 次 random.randint 自然概率（避免 10× 派奖被刷爆）。cap/cooldown/payout/stats/渲染不变。1000 局模拟：50% 实测 ~48-51%。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `7257317` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 156: dice 改动审计 R1+R2 闭环（20 findings / 8 fixes / 0 new H）

**Date**: 2026-05-16
**Task**: dice 改动审计 R1+R2 闭环（20 findings / 8 fixes / 0 new H）
**Branch**: `main`

### Summary

对最近 7 个 dice 相关 commit 做安全 / 性能 / 算法 / 渲染 / 跨模块全面审计。R1 20 项 finding（0 CRIT, 2 H, 6 M, 8 L, 4 I）；算法数学 sanity 通过（216=105+111，互斥/覆盖完整，win_rate 边界严格）。应用 8 处修复：at_user_id 统一 _sanitize 净化（H-1+H-2+M-3）、template cache threading.Lock（M-2）、Semaphore(4) 与豹子绕过 win_rate 注释（M-1+M-6）、_clamp_die 越界 warning（L-3）、player_name [:32] cap（L-10）、失败兜底+截图失败 warning（L-7+L-8）。R2 验证 8/8 PASS、0 new H/Critical，仅 2 Low + 2 Info 可观测性微调进 backlog。声明审计闭环。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `3e8792c` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 157: 我的信息 / 用户信息 截图加 @ 调用者

**Date**: 2026-05-16
**Task**: 我的信息 / 用户信息 截图加 @ 调用者
**Branch**: `main`

### Summary

_render_and_send_user_info 调 render_and_send_screenshot 时追加 at_user_id=event.get_user_id()，V11 路径生成 @调用者 [截图] 一条消息，与 dice 同模式。无论查自己/别人都 @ 触发者。依赖 commit aba28e6 的 _sanitize_at_user_id 防御。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `e926c78` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 158: 我的信息 / 用户信息 失败路径全部加 @ 调用者

**Date**: 2026-05-16
**Task**: 我的信息 / 用户信息 失败路径全部加 @ 调用者
**Branch**: `main`

### Summary

handle_user_info / handle_self_info 顶部统一取 at；5 处失败 bot.send（用户名不存在 / 不唯一 / 解析失败 / 用户不存在 / 未注册账号）全部改为 at + reply_failure 形式，与注册命令一致。成功截图路径 commit 32e90a0 已用 at_user_id 处理，不动。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `29dedc1` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete


## Session 159: 全量补齐 plugin 命令失败回复的 @ 调用者（134 处 / 12 文件）

**Date**: 2026-05-16
**Task**: 全量补齐 plugin 命令失败回复的 @ 调用者（134 处 / 12 文件）
**Branch**: `main`

### Summary

全量审 22 个 plugin 的失败 bot.send / reply_failure / reply_warning，发现 133 处缺 @ 前缀。统一加 at = safe_at_segment_or_empty(event.get_user_id()) 复用模式。涉及 11 文件（player_query 47 / leaderboard 33 / shop 11 / lottery 11 / server_tools 10 / red_packet 8 / ban 3 / menu 3 / tutorial 2 / warehouse 4 / permission_manager 1）。复审发现 handle_online 暂无服务器 残漏 1 处，已补。最终 grep 全 plugin 0 残留 bare reply_failure/reply_warning。截图失败兜底走 render_and_send_screenshot at_user_id（不在本任务范畴）。

### Main Changes

(Add details)

### Git Commits

| Hash | Message |
|------|---------|
| `c2df99b` | (see git log) |
| `7c7fae5` | (see git log) |

### Testing

- [OK] (Add test results)

### Status

[OK] **Completed**

### Next Steps

- None - task complete
