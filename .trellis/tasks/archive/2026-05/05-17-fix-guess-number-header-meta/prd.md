# guess_number header 删 范围 / 投入 元信息

## Goal

把 `server/templates/guess_number.html` header 中的 `范围 1-N` 和 `投入 X` 两段元信息删除，header 变为 `[avatar] Name (QQ) · 时间`（与 dice / signin 一致）。

## 现状（`server/templates/guess_number.html`）

### Header（L246-248 附近）
```
[avatar] Name (QQ) · 范围 1-100 · 投入 5 · 时间
```

### Stat-tiles
```
[投入 5] [实际获得] [净赚] [当前金币]
```

→ `投入` 在 header 和 stat-tile 重复；`范围` 在 header 显示但用户觉得不必要。

## 目标

### Header
```
[avatar] Name (QQ) · 时间
```

### Stats
保持原样（4 个 stat-tile 不变）

## 实现

### DOM 修改（约 L246-248）

**修改前**：
```html
<div class="owner-meta type-body-sm">
  <span class="owner-name" id="gn-owner-name"></span>
  <span class="owner-id" id="gn-owner-id"></span>
  <span class="meta-divider">·</span>
  <span>范围 <span class="meta-value" id="meta-range"></span></span>
  <span class="meta-divider">·</span>
  <span>选择 <span class="meta-value" id="meta-choice"></span></span>
  <span class="meta-divider">·</span>
  <span>投入 <span class="meta-value" id="meta-cost"></span></span>
  <span class="meta-divider">·</span>
  <span id="generated-at"></span>
</div>
```

**修改后**：
```html
<div class="owner-meta type-body-sm">
  <span class="owner-name" id="gn-owner-name"></span>
  <span class="owner-id" id="gn-owner-id"></span>
  <span class="meta-divider">·</span>
  <span>选择 <span class="meta-value" id="meta-choice"></span></span>
  <span class="meta-divider">·</span>
  <span id="generated-at"></span>
</div>
```

删除：`范围` span + 其前 divider；`投入` span + 其前 divider。

**保留** `选择` —— 用户只说删 `范围` 和 `投入`，没说删 `选择`。

### JS 修改

删除两行：
```js
document.getElementById("meta-range").textContent = `1-${rangeMax}`;
document.getElementById("meta-cost").textContent = String(cost);
```

**保留**：
- `document.getElementById("meta-choice").textContent = ...`（如果有，header 仍有 choice）
- `document.getElementById("sum-cost").textContent = ...`（stat-tile 仍要 cost）

### CSS 修改

无 —— 没新增/删除任何 CSS 规则，只动 DOM/JS。`.meta-value` 仍被 `meta-choice` 使用。

## Out of Scope

- 不改业务逻辑 / page 模块 / 其他模板
- **不动** `选择` 字段（用户没要求改）
- 不动 stat-tiles（4 tile 不变）
- 不像 dice 那样合并成 5 stat-tile（用户没要求）

## Acceptance Criteria

- [ ] `grep -n "范围 <span\|投入 <span" server/templates/guess_number.html` → 0 matches
- [ ] `grep -n "meta-range\|meta-cost" server/templates/guess_number.html` → 0 matches
- [ ] `grep -n "meta-choice" server/templates/guess_number.html` → still present (DOM + JS)
- [ ] `grep -n "sum-cost" server/templates/guess_number.html` → still present (stat-tile + JS)
- [ ] `.meta-value` CSS 规则保留（被 meta-choice 用）
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 只显示 `server/templates/guess_number.html`
