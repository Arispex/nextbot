# fix(templates): inventory / progress 模板 eyebrow 玩家查询→查询系统

## 改动

commit 5b0e63d 已把 player_query 8 条命令的 `category` 改为「查询系统」，但模板里的 hero `eyebrow` 仍是硬编码"玩家查询"，需同步。

- `server/templates/inventory.html:274` `<div class="header-eyebrow ...">玩家查询</div>` → `查询系统`
- `server/templates/progress.html:263` 同样改为 `查询系统`

## Scope

2 文件。

## Acceptance

- "用户背包" / "我的背包" / "进度" 等命令的截图顶部 eyebrow 显示「查询系统」
- 其它字段不变
- grep `玩家查询` 在 server/templates 零命中

## DO NOT

- 不动 page / 后端
- 不动其它模板
- 不 commit
