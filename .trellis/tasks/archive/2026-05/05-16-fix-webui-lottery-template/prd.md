# fix(webui): lottery template 移除高权命令前缀提示文案

## Bug

commit `8d9546c` 移除了 lottery 命令奖品的危险前缀黑名单（前后端常量、函数、listener 都清理了），但 `server/webui/templates/lottery_content.html:223-226` 模板里**还残留一条警示性提示**：

```html
<div class="field-section-hint" style="color:var(--accent-amber);">
  禁止录入高权命令前缀：<code>op</code> / <code>deop</code> / <code>ban</code> / <code>kick</code> / <code>stop</code> / <code>shutdown</code> / <code>restart</code> / <code>whitelist</code> / <code>pardon</code> / <code>save-all</code>。
</div>
```

实际后端已不再拒绝这些前缀，提示与行为脱节。

## 改动

`server/webui/templates/lottery_content.html` line 224-226：删除整个 `<div class="field-section-hint" style="color:var(--accent-amber);">...</div>` 块。

保留紧邻的 line 223 蓝色占位符提示（`占位符 {player} 会替换为玩家游戏名…`），它仍准确描述行为。

## Scope

仅 `server/webui/templates/lottery_content.html`。

## Acceptance

- 编辑命令奖品时不再显示橙色"禁止录入高权命令前缀…"提示
- 占位符提示（`{player}` 替换说明）保留
- 其它字段无回归

## DO NOT

- 不动后端 / JS / CSS
- 不删占位符提示
- 不 commit
