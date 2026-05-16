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
