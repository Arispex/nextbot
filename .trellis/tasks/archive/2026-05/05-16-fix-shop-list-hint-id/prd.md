# fix(shop_list): 移除底部 hint + 每商店右侧显示「查看商店 <id>」命令

## 改动

`server/templates/shop_list.html`：

### 1) 移除底部 hint-line（"查看商店 ... 购买商品 ..."）

- HTML line 171 `<div id="hint-line" class="hint-line" hidden></div>` → 删除
- CSS line 139-142 `.hint-line { ... }` → 删除
- JS line 235-237 `const hintLine = ...; hintLine.textContent = "..."; hintLine.hidden = false;` → 删除

### 2) 每个商店右侧加 `查看商店 <id>` 命令

在 entry-top（line 204-221 的 `<div class="entry-top">`）末尾追加一个 `.entry-cmd` 元素：

```js
const cmdHint = document.createElement("span");
cmdHint.className = "entry-cmd";
cmdHint.textContent = `查看商店 ${entry?.shop_id ?? ""}`.trim();
top.appendChild(cmdHint);
```

CSS：
```css
.entry-cmd {
  margin-left: auto;
  font-family: var(--font-code);
  font-size: 12px;
  color: var(--color-muted-soft);
  font-feature-settings: "tnum";
  white-space: nowrap;
}
```

`margin-left: auto` 让命令右浮（entry-top 是 flex 容器，name + id-pill + count-pill 在左，命令贴右）。`white-space: nowrap` 防换行打断。

## Scope

仅 `server/templates/shop_list.html`（HTML + CSS + JS 一体改）。

## Acceptance

- 底部不再显示 hint-line 文案
- 每个商店条目右侧显示 mono `查看商店 <id>`（如 `查看商店 1`）
- 视觉与现有 id-pill / count-pill 协调
- HTML 平衡

## DO NOT

- 不改 shop_list_page.py / 后端
- 不改 payload schema
- 不动其它模板
- 不 commit
