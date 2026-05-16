# fix(admin_list): 删除 Owner badge

## 改动

`server/templates/admin_list.html`：

1. JS line 198-201（badge 元素创建块）删除
2. CSS line 118-127 `.badge { ... }` 规则删除（badge 类不再被任何 element 使用）

## Scope

仅 `server/templates/admin_list.html`。

## Acceptance

- 管理员列表图片每个卡片不再显示 "Owner" 标签
- 其它字段（昵称 / QQ）正常
- 模板内 `.badge` / `Owner` 字面量零残留 grep

## DO NOT

- 不动 admin_list_page.py / 后端
- 不动其它模板
- 不 commit
