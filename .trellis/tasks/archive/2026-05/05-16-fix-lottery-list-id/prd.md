# fix(lottery_list): 每奖池右侧再加「抽奖 <id>」命令（两行右浮）

## 改动

`server/templates/lottery_list.html`：

把单行 `.entry-cmd` 改为容器 `.entry-cmds`（flex-column 右对齐）+ 两个 `.entry-cmd`：
- 第 1 行：`查看奖池 <pool_id>`
- 第 2 行：`抽奖 <pool_id>`

### CSS

```css
.entry-cmds {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.entry-cmd {
  font-family: var(--font-code);
  font-size: 12px;
  color: var(--color-muted-soft);
  font-feature-settings: "tnum";
  white-space: nowrap;
}
```

（去掉原 `.entry-cmd` 的 `margin-left: auto` — 容器 `.entry-cmds` 接管定位。）

### JS

把原创建单个 cmdHint 块替换为：
```js
const cmds = document.createElement("div");
cmds.className = "entry-cmds";

const viewCmd = document.createElement("span");
viewCmd.className = "entry-cmd";
viewCmd.textContent = `查看奖池 ${entry?.pool_id ?? ""}`.trim();
cmds.appendChild(viewCmd);

const drawCmd = document.createElement("span");
drawCmd.className = "entry-cmd";
drawCmd.textContent = `抽奖 ${entry?.pool_id ?? ""}`.trim();
cmds.appendChild(drawCmd);

top.appendChild(cmds);
```

## Scope

仅 `server/templates/lottery_list.html`。

## Acceptance

- 每个奖池条目右侧两行命令：
  - 第 1 行 `查看奖池 <id>`
  - 第 2 行 `抽奖 <id>`
- 视觉对齐（右对齐），上下紧凑 gap 2px
- 不破坏 shop_list（其样式独立）

## DO NOT

- 不改 lottery_list_page.py / 后端
- 不动 shop_list
- 不 commit
