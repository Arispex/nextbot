# lottery_result header 奖池 ID 去掉 `#` prefix

## Goal

`server/templates/lottery_result.html` 中 `奖池 名称 (#N)` 的 `#N` 改成纯 `N`（DB 真实 pool_id），与 v1.6.0 的「shop / lottery list ID 列改真实 ID」决策保持一致。

## 现状

L395:
```js
document.getElementById("meta-pool-id").textContent = `#${data.pool_id ?? "-"}`;
```

显示：`奖池 千亦抽奖 (#7)`

## 目标

```js
document.getElementById("meta-pool-id").textContent = String(data.pool_id ?? "-");
```

显示：`奖池 千亦抽奖 (7)`

## 实现

仅 `server/templates/lottery_result.html` L395 一行修改。

## Out of Scope

- 不改 DOM 结构（`<span>奖池 ... (<span id="meta-pool-id"></span>)</span>` 保留）
- 不改 page module / handler / 其他模板

## Acceptance Criteria

- [ ] `grep -n "\`#\${data.pool_id" server/templates/lottery_result.html` → 0 matches
- [ ] `grep -n "data.pool_id" server/templates/lottery_result.html` → 仍存在但不带 `#`
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 仅 `server/templates/lottery_result.html`
