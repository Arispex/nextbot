# fix(dice): 求和行 total 字体改为与骰子数字一致（mono）

## Bug

`server/templates/dice.html` 中 `.dice-sum` 行结构是 `N + N + N = total (label)`，其中：
- `.dice-sum-numbers`（`d1 + d2 + d3`）用 `var(--font-code)` mono
- `.dice-sum-total`（`9`）用 `var(--font-display)` serif

视觉断层：等号左边数字是 mono、等号右边的 total 突然变 serif，不协调。

## 改动

`server/templates/dice.html` 的 `.dice-sum-total` CSS：
```css
.dice-sum-total {
  font-family: var(--font-display);   /* 当前 */
  font-weight: 400;
  letter-spacing: -0.3px;
  color: var(--color-ink);
}
```
改为：
```css
.dice-sum-total {
  font-family: var(--font-code);     /* 与 .dice-sum-numbers 一致 */
  font-weight: 400;
  color: var(--color-ink);
}
```
去掉 `letter-spacing: -0.3px`（mono 字体不需要负 tracking）。

## Scope

仅 `server/templates/dice.html`。

## Acceptance

- 求和行 `1 + 5 + 3 = 9` 整个等号左右数字字体一致（mono）
- label "（大）/（小）/（豹子）" 保持 body / display 不变

## DO NOT

- 不动 payload / plugin / web_server / render route
- 不动 `.dice-sum-numbers` / `.dice-sum-equals` / `.dice-sum-label`
- 不 commit
