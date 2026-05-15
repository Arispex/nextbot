# fix(webui): shop _load_server_label_map 内嵌新 session 同样触发 BEGIN IMMEDIATE 自死锁

## Bug

商店页面新建商品 / 修改商品 / 查看商店详情都可能命中和 lottery 同形的 SQLite 自死锁：
`sqlite3.OperationalError: database is locked  [SQL: BEGIN IMMEDIATE]`

## 命中点

`server/routes/webui_shop.py`：

| 行号 | 上下文 | 问题 |
|------|--------|------|
| 791 | `get_shop` — 查商店详情，外层 session 已开（line 778） | session 已 autobegin 读数据，再开新 session 调 `_load_server_label_map` |
| 928 | `create_shop_item` — `session.commit() + session.refresh(item)` 后 | refresh 触发 BEGIN IMMEDIATE，再开新 session 死锁 |
| 989 | `update_shop_item` — `session.commit()` 后 | 同上 |

## 修复

与 lottery hotfix（commit `7c24541`）同形：让 `_load_server_label_map(session=None)` 可选接收已有 session，三个 caller 改为 `_load_server_label_map(session)` 复用外层连接。其它无外层 session 的 caller 行为不变（实际从 grep 看没有其它调用点，但保留无参分支以备）。

## Scope

仅 `server/routes/webui_shop.py` — 行号 ~340-348 (定义) + 791 + 928 + 989。

## Acceptance

- GET `/webui/api/shop/{shop_id}` 不死锁
- POST `/webui/api/shop/{shop_id}/items` 成功
- PUT `/webui/api/shop/{shop_id}/items/{item_id}` 成功
- 静态检查 `py_compile` 通过

## Out of Scope

- 其它 endpoint
- BEGIN IMMEDIATE 全局策略
- `_load_server_id_set` 同形改造（它不在 commit+refresh 后调用，无急需）
