# dice header 去重「投入」+「选择」chip 移到 stats 旁

## Goal

1. **删除** dice.html header meta 中的「投入 N」（与 stat-tile 重复）
2. **移动** dice.html header meta 中的「选择 X」到 stat-tiles 区域**左侧**，做成 chip 风格

## 现状（`server/templates/dice.html`）

### Header
```
[avatar] Name (QQ) · 选择 大 · 投入 30 · 时间
```
- 选择 / 投入 在 header
- 投入 同时也在下面的 stat-tile 显示，重复

### Stats
```
[投入 30] [实际获得 X] [净赚 Y] [当前金币 Z]   ← 4 grid columns
```

## 目标

### Header（去掉选择 + 投入 + 2 个 divider）
```
[avatar] Name (QQ) · 时间
```

### Stats row（chip 在左 + stat-tiles 在右）
```
[你的选择     | [投入]  [实际获得]  [净赚]  [当前金币]
 大          |   30        20         -10       1234
 (chip)]    |        (4 stat-tiles grid, flex:1)
```

## 实现

### DOM 修改

**Header L254-264**：删除 `选择` / `投入` 两段 span + 各自前面的 `meta-divider`。owner-meta 变为：
```html
<div class="owner-meta type-body-sm">
  <span class="owner-name" id="dice-owner-name"></span>
  <span class="owner-id" id="dice-owner-id"></span>
  <span class="meta-divider">·</span>
  <span id="generated-at"></span>
</div>
```

**Stats 区 L267-284**：把 `.stats-tiles` 包进 `.stats-row` flex 容器，左侧加 `.choice-chip`：
```html
<section class="stats-row">
  <div class="choice-chip">
    <span class="choice-label">你的选择</span>
    <span class="choice-value" id="meta-choice"></span>
  </div>
  <div class="stats-tiles">
    <div class="stat-tile">
      <span class="stat-label">投入</span>
      <span class="stat-value loss" id="sum-cost"></span>
    </div>
    <div class="stat-tile">
      <span class="stat-label">实际获得</span>
      <span class="stat-value" id="sum-payout"></span>
    </div>
    <div class="stat-tile">
      <span class="stat-label">净赚</span>
      <span class="stat-value" id="sum-net"></span>
    </div>
    <div class="stat-tile">
      <span class="stat-label">当前金币</span>
      <span class="stat-value" id="sum-coins"></span>
    </div>
  </div>
</section>
```

### CSS 修改

**`.stats-tiles` 现有规则去掉 grid 主声明**（grid 移到子层），添加新 `.stats-row` flex container + `.choice-chip` 系列规则：

```css
.stats-row {
  display: flex;
  gap: var(--space-sm);
  align-items: stretch;
}

.choice-chip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  justify-content: center;
  padding: var(--space-sm) var(--space-md);
  background-color: var(--color-surface-cream-strong);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-md);
  flex-shrink: 0;
  min-width: 96px;
}

.choice-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-muted);
}

.choice-value {
  font-family: var(--font-display);   /* serif，与 stat-tile mono 数字区分（qualitative vs quantitative）*/
  font-size: 22px;
  font-weight: 400;
  color: var(--color-ink);
  line-height: 1.1;
}

.stats-tiles {
  flex: 1;
  display: grid;
  gap: var(--space-sm);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
```

### JS 修改

**删 1 行**（`document.getElementById("meta-cost").textContent = ...` 如果有的话，因为 header 不再显示 cost；保留 `sum-cost` 的赋值，那是 stat-tile 用）

**保留**：`document.getElementById("meta-choice").textContent = choice;` —— id 和现有完全一样，只是 DOM 位置换了，JS 不用动。

实际上 JS 里 cost 是给 `sum-cost`（stat-tile）的，meta-cost 应该不存在了；只删 header 里那 1 行 `meta-cost` 的赋值（如果有），其余 meta-choice + sum-* 都保留。

## Out of Scope

- 不改 dice 业务逻辑 / 骰子动画 / 结果文案
- 不改其他模板（guess_number / rob / signin / etc.）—— 同类设计但要等下一轮统一决策
- 不改 page 模块（`server/pages/dice_page.py`）—— payload schema 不变
- 不改 backend handler

## Acceptance Criteria

- [ ] dice.html header owner-meta 只剩 `[avatar] Name (QQ) · 时间`
- [ ] `grep -n "选择 <span\|投入 <span" server/templates/dice.html` → 0 matches in header；`选择` `投入` 字样只在新 chip / stat-label 中出现
- [ ] `.stats-row` flex container 新增
- [ ] `.choice-chip` `.choice-label` `.choice-value` CSS 新增
- [ ] `meta-choice` id 仍存在（在 chip 中）
- [ ] `sum-cost` 仍正常显示「投入」金额（stat-tile 未动）
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 只显示 `server/templates/dice.html`

## Technical Notes

- 同样的设计模式（chip + stats-row）后续可推广到 guess_number / rob / lottery_result —— 本次仅限 dice
- choice-value 用 serif（display 字体）是有意区别 stat-value 的 mono 数字，让"选择 大"显得是 qualitative 标签而非数字
