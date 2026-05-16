# feat(dice): 增加概率控制参数（默认 50%）

## Goal

让 admin 可在命令配置（`command.params`）调控"掷骰子"的中奖率，而不是固定靠真实骰子物理概率。算法：先按目标概率决定 win/lose，再按结果反向"挑一组合法骰子组合"展示给玩家（视觉随机，但命中率受控）。

## 当前自然概率（3 骰子 1-6）

| 选择 | 自然中奖率 | 默认派奖倍率 | EV |
|---|---|---|---|
| 大（total ≥ 11 且非豹子） | 108/216 ≈ 50.0% （实际除豹子≈48.6%）| 2× | 接近 1.0× |
| 小（total ≤ 10 且非豹子） | 108/216 ≈ 50.0% | 2× | 接近 1.0× |
| 豹子（三连） | 6/216 ≈ 2.78% | 10× | 0.278× |

豹子 EV ≈ 0.28（玩家长期亏 72%）；大/小 EV ≈ 0.97（庄家很薄）。

## 关键设计问题

**"默认 50%" 是怎么应用的？** 有 3 种理解：

- **A 全局命中率**：无论 选大/小/豹子，都按 50% 命中。豹子按 50% 命中 + 10× 派奖 → EV = 5.0，**bot 长期暴亏**。
- **B 仅大/小命中率可控**：大/小默认 50%；豹子保留自然 ~2.78% 不可调。
- **C 大/小 + 豹子分别可调**：大/小 默认 50%；豹子 默认 2.78%（保留自然），可独立调。

## Decision (ADR-lite)

**Q1**：只对 **大/小** 应用 `win_rate`（默认 50%）；**豹子** 保留自然 ~2.78% 不可配置，避免 10× 派奖被刷爆。

## Requirements

- 在 `command.params` 注册新参数 `win_rate`（int 0-100，默认 50，label「大/小 命中率（%）」）
- 选 **大/小** 时：按 `win_rate/100` 概率算法控制；选 **豹子** 时：保留自然 3 骰子 random（不受 win_rate 影响）
- 视觉上骰子结果仍然"看起来随机"（从合法组合集合里采样）
- 极端值边界：`win_rate=0` 永远输；`win_rate=100` 永远赢
- cap / cooldown / stats 逻辑全部不变

## Technical Approach

### 1) 预计算组合集合（module load）

```python
ALL_COMBOS = tuple((a, b, c) for a in range(1, 7) for b in range(1, 7) for c in range(1, 7))

def _is_triple(d): return d[0] == d[1] == d[2]

WIN_BIG_SET = tuple(d for d in ALL_COMBOS if not _is_triple(d) and sum(d) >= 11)
LOSE_BIG_SET = tuple(d for d in ALL_COMBOS if _is_triple(d) or (not _is_triple(d) and sum(d) <= 10))
WIN_SMALL_SET = tuple(d for d in ALL_COMBOS if not _is_triple(d) and sum(d) <= 10)
LOSE_SMALL_SET = tuple(d for d in ALL_COMBOS if _is_triple(d) or (not _is_triple(d) and sum(d) >= 11))
```

Set 容量校验：105 / 111 / 105 / 111 = 216 总数 OK。

### 2) 算法（替换原 line 173-176 `d1 = random.randint...`）

```python
if choice == "豹子":
    d1, d2, d3 = random.randint(1, 6), random.randint(1, 6), random.randint(1, 6)
else:
    win_rate = max(0, min(100, _safe_param_int("win_rate", 50, min_value=0))) / 100.0
    if choice == "大":
        win_set, lose_set = WIN_BIG_SET, LOSE_BIG_SET
    else:  # 小
        win_set, lose_set = WIN_SMALL_SET, LOSE_SMALL_SET
    target_set = win_set if random.random() < win_rate else lose_set
    d1, d2, d3 = random.choice(target_set)
total = d1 + d2 + d3
is_triple = d1 == d2 == d3
```

后续 payout / stats / 渲染逻辑完全不变（继续靠 `is_triple` / `total` 推导）。

### 3) 命令参数注册

`@command_control(params={...})` 段加：
```python
"win_rate": {
    "type": "int",
    "label": "大/小 命中率",
    "description": "选大/小时命中的概率（百分比，0-100），不影响豹子",
    "required": False,
    "default": 50,
    "min": 0,
    "max": 100,
},
```

`_safe_param_int` 已支持 min_value；新加 max 约束可直接 `min(100, ...)` clamp。

## Acceptance

- 命令配置（commands 页或命令 params）能看到并修改 `win_rate`（0-100）
- 玩家选 大/小 时命中率统计上接近设定值（如 50% 下 100 局约 50 胜）
- 玩家选 豹子 不受 win_rate 影响（保留 ~2.78% 自然命中）
- 0% / 100% 边界正确（必输 / 必赢，但选大/小被豹子通杀的极小概率仍存在于 lose 路径）
- 渲染 / cap / cooldown / stats 不变

## Scope

仅 `nextbot/plugins/dice.py`。

## DO NOT

- 不动 dice.html / dice_page.py / web_server.py / render.py
- 不改 cap / cooldown / payout 业务
- 不动豹子选项的自然概率
- 不 commit

## Out of Scope

- 玩家级别的概率个性化
- 概率动态调整（基于玩家胜率）
- 防套利的反检测策略（如必输/必赢序列）— 这是个 bigger 议题
- 修改派奖倍率
