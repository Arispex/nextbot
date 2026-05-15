# fix(webui): 统一表格 ID 列文案 — shop / lottery 改 "#数字" 为 "ID"

## 现状

WebUI 表格 ID 列文案不一致：
- `users_content.html:42` `<th>ID</th>` + 单元格显示 `user.id`（DB PK）✓ 标准
- `servers_content.html:42` `<th>ID</th>` + 单元格显示 `server.id`（DB PK）✓ 标准
- `shop_content.html:74` `<th class="col-index">#</th>` + `shop.js:266` `tdIdx.textContent = "#" + (idx+1)`（**行号**，非 DB ID）
- `lottery_content.html:74` `<th class="col-index">#</th>` + `lottery.js:318` `tdIdx.textContent = "#" + (idx+1)`（**行号**，非 DB ID）

shop / lottery 当前显示的 "#1 / #2 / #3" 实际是渲染顺序的行号，**不是数据库 ID**，admin 排查时无法对应日志 / API 响应里的 prize_id / item_id。

## 决议

统一为 users / servers 风格：
- 表头：`ID`
- 单元格：真实数据库 ID（`item.id` / `prize.id`），不加 `#` 前缀

这同时解决：
1. 文案统一（用户诉求）
2. admin 看表格能直接读出对应 DB ID，便于排障与日志关联

## 改动

### `server/webui/templates/shop_content.html:74`
```html
<th class="col-index">#</th>
```
→
```html
<th class="col-index">ID</th>
```

保留 `col-index` 类名维持原 CSS 宽度等样式。

### `server/webui/templates/lottery_content.html:74`
同上。

### `server/webui/static/js/shop.js`
- `renderItemRow(it, displayIndex)`：`tdIdx.textContent = "#" + displayIndex;` → `tdIdx.textContent = String(it.id);`
- caller 仍传 displayIndex 但不再使用（保留参数兼容；也可删除参数）。最小修：保留 displayIndex 形参不动以减少 diff

### `server/webui/static/js/lottery.js`
- `renderPrizeRow(prize, displayIndex, probabilityPct, unsetUnderflow)`：`tdIdx.textContent = "#" + displayIndex;` → `tdIdx.textContent = String(prize.id);`
- caller 同上

## Scope

仅 4 个文件：
- `server/webui/templates/shop_content.html`
- `server/webui/templates/lottery_content.html`
- `server/webui/static/js/shop.js`
- `server/webui/static/js/lottery.js`

## Out of Scope

- warehouse 槽位卡片的 `#5` 标签（slot index 是 slot 自身标识符，不是表格列；视觉上是 slot grid 而非 row table）
- delete modal 中的 `#数字` 显示（`warehouse.js:497` `els.deleteSlot.textContent = "#" + state.editingSlot`）— 这是"删除第 5 号 slot"的提示，非表格列
- groups / commands / dashboard 等无 ID 列的表格
- col-index 的 CSS 宽度可能需要微调（实际 DB ID 数字位数比 1-99 可能更长），暂不在本任务调整 — 若发现挤压可后续单独任务

## Acceptance

- shop / lottery 表头都显示 "ID"
- shop / lottery 表格 ID 列显示真实 `item.id` / `prize.id`，无 `#` 前缀
- users / servers 行为不变
- col-index CSS 不动（保留宽度，DB ID 通常 1-4 位数仍 fit；若超 5 位字符截断由 CSS overflow 控制）

## Technical Notes

- 不动后端
- 不动后端 ID 返回结构（已是 numeric PK）
