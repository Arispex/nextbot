# 自动备份数据库 — 研究蓝图

## 现状（已调查）

### 数据库
- SQLite，`DB_PATH = DATA_DIR / "app.db"`（`nextbot/db.py:28`），**WAL 模式**（`app.db-wal` / `app.db-shm`）。
- `get_engine()` / `get_session()` 现成；`wal_checkpoint_truncate()`（shutdown 前 checkpoint）。
- **安全备份方式**：WAL 模式下直接 copy `app.db` 会漏掉 WAL 中未 checkpoint 的帧 → 用 sqlite3 **在线备份 API** `sqlite3.connect(DB_PATH).backup(dst_conn)`（正确处理锁 + WAL，产出单文件一致快照）。备份前可选 `PRAGMA wal_checkpoint(TRUNCATE)` 但 `.backup()` 本身已一致，非必须。

### 调度机制（无 apscheduler）
- NoneBot `@driver.on_startup` / `@driver.on_shutdown` 钩子（`bot.py:198/221`）+ `asyncio.create_task` 后台 loop（参 `sync_orchestrator.py` 的 create_task/sleep 模式）。
- 范例：`user_manager.py:179 @nonebot.get_driver().on_startup`。
- → 备份用 on_startup 启动一个后台 asyncio 任务：`while not stop: await asyncio.sleep(tick); if enabled: backup + prune`；on_shutdown 取消任务。

### 设置系统（已熟，Boss 通知刚用过）
- `server/settings_service.py`：`.env` 持久化，每字段一个 `FieldSpec(field, env_key)` + `_normalize_field` + `_load_value_from_config` 默认 + `_SINGLE_LINE_STRING_FIELDS`。
- 整数字段范例：`web_server_port`（`_coerce_port`）；bool 字段：`group_welcome_enabled`（`_coerce_bool`）。
- Web UI：`server/webui/templates/settings_content.html`（section）+ `server/webui/static/js/settings.js`（getElementById / fieldLabels / save payload / fillForm）。

## 平行实现蓝图

### A. 设置（3 项）
| field | env_key | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `db_backup_enabled` | `DB_BACKUP_ENABLED` | bool | True | 是否开启自动备份 |
| `db_backup_interval_hours` | `DB_BACKUP_INTERVAL_HOURS` | int(≥最小值) | 待定（brainstorm） | 备份时间间隔（小时） |
| `db_backup_retention` | `DB_BACKUP_RETENTION` | int(≥1) | 待定（brainstorm） | 备份保留数量 |
- `settings_service.py`：3 个 FieldSpec + `_normalize_field`（bool 用 `_coerce_bool`；两个 int 用类似 `_coerce_port` 的范围校验，interval 设最小值防抖动如 ≥1h，retention ≥1）+ `_load_value_from_config` 默认值。int 字段不进 `_SINGLE_LINE_STRING_FIELDS`（仿 web_server_port）。
- 注意 `_serialize_env_value`：int 默认 `str(value)` 落 env；`_load_value_from_env` 对新 int 字段需走 coerce（仿 web_server_port 分支）。

### B. 备份模块（新建 `nextbot/db_backup.py`）
- `BACKUP_DIR = DATA_DIR / "backups"`（启动时 mkdir）。
- `backup_database() -> Path`：sqlite3 `.backup()` 快照 → `backups/app-YYYYMMDD-HHMMSS.db`（北京时间，`nextbot.time_utils`）。失败记日志不抛断。
- `prune_old_backups(retention: int)`：按文件名/mtime 降序，保留最新 retention 份，删多余；记日志（删了哪些）。
- 统一日志入口（logger），key=value：`数据库备份成功 path=... size=...` / `备份清理 deleted=N kept=M`。

### C. 调度（接线 `bot.py` 或新建 plugin 的 on_startup）
- `@driver.on_startup` → `asyncio.create_task(_backup_loop())`；`@driver.on_shutdown` → set stop event + cancel。
- `_backup_loop`：每 tick 读 `get_settings_snapshot()`（或 driver.config）拿 enabled/interval/retention；enabled 才备份；以「上次备份时间 + interval」判断是否到点（或简单 sleep(interval_hours*3600)）。改间隔/开关运行时生效。
- 防呆：interval 有最小值；备份目录与 app.db 同盘（copy 量）。

### D. Web UI
- `settings_content.html`：新增 `<h3 id="section-db-backup">数据库自动备份</h3>` 区：开关(checkbox/select bool) + 时间间隔(number, 小时) + 保留数量(number)。
- `settings.js`：取 3 input；fieldLabels 加 3 项；save payload 加 3 项（bool/int 转换）；fillForm 回填 + 默认。

### E. 测试
- backup_database：生成快照文件、内容可读（sqlite 可打开、表存在）。
- prune：保留 N、删旧、retention≥总数时不删。
- settings：3 字段 normalize（bool、int 范围、非法回退/报错）、默认、round-trip。
- loop：enabled=False 不备份；间隔逻辑（可注入 fake 时间/短间隔）。

## 待 brainstorm 决策
1. 时间间隔单位 + 默认值（建议：小时，默认 24）。
2. 保留数量默认值（建议：7）。
3. （次要）启动时是否立即备份一次 / 仅按间隔。
