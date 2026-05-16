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
