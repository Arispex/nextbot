# fix(lottery_list): 移除底部 hint + 每奖池右侧显示「查看奖池 <id>」

与 shop_list 同模式（commit a06482d）。

## 改动

`server/templates/lottery_list.html`：

- 删除底部 hint-line（HTML line 176 `<div id="hint-line">` / CSS line 144-147 `.hint-line` / JS line 245-247）
- 每个 entry-top 末尾加 `.entry-cmd` 显示 `查看奖池 <pool_id>`，mono 小字 muted-soft，`margin-left: auto` 右浮

## Scope

仅 `server/templates/lottery_list.html`。

## DO NOT

- 不改 lottery_list_page.py / 后端
- 不改 payload schema
- 不 commit
