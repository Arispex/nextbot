# chore: gitignore SQLite WAL 副边文件

## Goal

Round 7 commit `66b4d6c` 启用 SQLite WAL 模式后，运行时会在 `app.db` 旁产生 `app.db-shm` / `app.db-wal` 两个副边文件。当前 `.gitignore:147` 只忽略了 `app.db`，未忽略副边文件 → 每次 `git status` 都会显示 untracked，且容易被误 commit。

## Scope

文件：`.gitignore`

## 修改

在第 147 行 `app.db` 附近追加：
```
app.db-shm
app.db-wal
```

## Acceptance Criteria

- [ ] `.gitignore` 包含 `app.db-shm` / `app.db-wal`
- [ ] `git status` 不再显示 `app.db-shm` / `app.db-wal`

## Out of Scope

- 不删除现有 `app.db-shm` / `app.db-wal` 文件（这是 SQLite 运行时管理的，无需手工删）
- 不动其他 SQLite / Python / IDE 忽略规则
