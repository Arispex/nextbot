# rob header 删「→ 目标」元信息

## Goal

把 `server/templates/rob.html` header `.owner-meta` 中的 `→ 目标 W (Z)` 段删掉，header 变成 `[avatar] Name (QQ) · 时间`（与 dice / signin / guess_number 一致）。

## 现状（L314-321）

```html
<div class="owner-meta type-body-sm">
  <span class="owner-name" id="rob-owner-name"></span>
  <span class="owner-id" id="rob-owner-id"></span>
  <span class="meta-divider">·</span>
  <span>→ 目标 <span class="meta-value" id="rob-victim-text"></span></span>
  <span class="meta-divider">·</span>
  <span id="generated-at"></span>
</div>
```

→ 目标 W (Z) 在 header 显示。但 rob 页面正文已经有目标玩家的 avatar / card 完整展示（`rob-victim-avatar` / `rob-victim-card`，L357-358），header 这段是多余信息。

## 目标

```html
<div class="owner-meta type-body-sm">
  <span class="owner-name" id="rob-owner-name"></span>
  <span class="owner-id" id="rob-owner-id"></span>
  <span class="meta-divider">·</span>
  <span id="generated-at"></span>
</div>
```

## 实现

### DOM 修改（L317-319）
删除 2 行：
- L317 `<span class="meta-divider">·</span>` （目标之前的 divider）
- L318 `<span>→ 目标 <span class="meta-value" id="rob-victim-text"></span></span>`

实际上是删 L317-318，并保留 L319 的 divider 作为 owner-id 和 generated-at 之间的唯一 divider。

净删 2 行。

### JS 修改
删除 2 行（构造 victimText + 写入 rob-victim-text 的两行）：
```js
const victimText = victimQq ? `${victimName} (${victimQq})` : (victimName || "未知玩家");
document.getElementById("rob-victim-text").textContent = victimText;
```

或者类似形式 —— 找到 `rob-victim-text` 赋值的那两行删掉。

**保留**：
- `rob-owner-*` (header 仍在用)
- `generated-at` (header 仍在用)
- `rob-victim-avatar` / `rob-victim-card` (正文流向图，与 header 无关)
- `victimName` / `victimQq` 变量本身保留（正文 card 还在用）

### CSS 修改
无 —— `.meta-value` 规则若还被其他 span 引用就保留；若没人用就 dead，但本次不动 CSS（避免范围扩散）。检查后报告。

## Out of Scope

- 不动 rob 业务逻辑 / page 模块 / 其他模板
- 不动正文流向图的 victim avatar / card
- 不动 `robberName` / `robberQq` / `victimName` / `victimQq` 变量

## Acceptance Criteria

- [ ] `grep -n "rob-victim-text" server/templates/rob.html` → 0 matches
- [ ] `grep -n "→ 目标" server/templates/rob.html` → 0 matches
- [ ] `grep -n "rob-victim-avatar\|rob-victim-card" server/templates/rob.html` → 仍存在（正文用）
- [ ] `grep -n "rob-owner-name\|rob-owner-id\|generated-at" server/templates/rob.html` → 仍存在
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 只显示 `server/templates/rob.html`
