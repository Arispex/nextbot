# fix(webui): lottery create_prize / update_prize 内嵌新 session 导致 BEGIN IMMEDIATE 自死锁

## Bug

新建抽奖奖品（POST `/webui/api/lottery/{pool_id}/prizes`）报：
```
sqlite3.OperationalError: database is locked
[SQL: BEGIN IMMEDIATE]
```
Traceback 显示在 `webui_lottery.py:1039` `label_map = _load_server_label_map()` 的内部 `session.query(Server).all()` 阶段。

## 根因

`create_prize`（`webui_lottery.py:976-1055`）流程：
1. 外层 `session = get_session()` 打开
2. `session.add(prize)` → `session.commit()`
3. **`session.refresh(prize)`** 触发 SQLAlchemy autobegin → 引擎 `begin` 事件 → `_force_immediate_begin` 在外层连接执行 `BEGIN IMMEDIATE` → 外层连接拿到 SQLite RESERVED 写锁
4. **`label_map = _load_server_label_map()`** 在内部又 `get_session()` 拿新连接 → autobegin → `BEGIN IMMEDIATE` → SQLite 同库另一连接已持锁 → busy_timeout 内拿不到 → `OperationalError`

`update_prize`（`webui_lottery.py:1058-1135`）有完全相同的模式（line 1123）。

## 修复

最小、非破坏：把 `_load_server_label_map()` 的两处调用改为复用外层 session。

```python
# create_prize:1039 / update_prize:1123
label_map = {int(s.id): str(s.name) for s in session.query(Server).all()}
```

或者把 `_load_server_label_map` 改造为可选接 session（不动其它 caller）：
```python
def _load_server_label_map(session: Session | None = None) -> dict[int, str]:
    if session is not None:
        return {int(s.id): str(s.name) for s in session.query(Server).all()}
    s = get_session()
    try:
        return {int(x.id): str(x.name) for x in s.query(Server).all()}
    finally:
        s.close()
```

两个 caller `_load_server_label_map(session)`。

## Scope

仅 `server/routes/webui_lottery.py` — 行号 ~435, ~1039, ~1123。

## Acceptance

- POST `/webui/api/lottery/{pool_id}/prizes` 成功创建并返回 prize 含 `target_server_label`
- PUT `/webui/api/lottery/{pool_id}/prizes/{prize_id}` 成功更新
- 其它 `_load_server_label_map` 调用点（line 848 等）行为不变

## Out of Scope

- 其它 endpoint 的 session 管理优化
- BEGIN IMMEDIATE 全局策略
