# 自动备份数据库

## Goal

新增「数据库自动备份」功能：后台定时把 SQLite `app.db` 备份到 `backups/` 目录，保留最新 N 份。Web UI 设置页新增一组设置——是否开启（默认开）、时间间隔（小时）、保留数量。

## Requirements

### 设置（Web UI 可配，仿现有设置区）
| field | env_key | 类型 | 默认 | 约束 |
|---|---|---|---|---|
| `db_backup_enabled` | `DB_BACKUP_ENABLED` | bool | `True` | — |
| `db_backup_interval_hours` | `DB_BACKUP_INTERVAL_HOURS` | int | `24` | ≥1（防抖动），≤8760 |
| `db_backup_retention` | `DB_BACKUP_RETENTION` | int | `30` | ≥1，≤1000 |
- `settings_service.py` 新增 3 个 FieldSpec + normalize（bool 用 `_coerce_bool`；两个 int 仿 `_coerce_port` 做范围校验）+ `_load_value_from_config` 默认值 + `_load_value_from_env` int 分支。两个 int 字段**不**进 `_SINGLE_LINE_STRING_FIELDS`（仿 web_server_port）。
- Web UI 新增「数据库自动备份」设置区（开关 / 时间间隔(小时, number) / 保留数量(number)）：`settings_content.html` + `settings.js`（getElementById / fieldLabels / save payload / fillForm + 默认）。

### 备份逻辑（新建 `nextbot/db_backup.py`）
- `BACKUP_DIR = DATA_DIR / "backups"`（首次 mkdir）。
- 安全快照：sqlite3 在线备份 API `sqlite3.connect(DB_PATH).backup(dst)`（正确处理 WAL + 锁，产出单文件一致快照），输出 `backups/app-YYYYMMDD-HHMMSS.db`（北京时间，`nextbot.time_utils`）。
- 保留：按时间戳/mtime 降序保留最新 `retention` 份，删多余。
- best-effort：单次备份/清理失败只记日志（WARN/ERROR），不崩 bot、不影响下次。

### 调度（NoneBot 钩子 + asyncio 后台任务）
- `@driver.on_startup` 启动后台任务；`@driver.on_shutdown` 优雅停止（stop event + cancel）。
- **启动时立即备份一次**（enabled 时），之后按间隔。
- tick 轮询（如每 60s）+ 记录上次备份时间：`enabled and now-last >= interval_hours` 时备份 + 清理 → 改间隔/开关运行时生效（无需重启）。
- 读取设置用 `get_settings_snapshot()` / driver.config（与现有设置消费一致）。

### 日志（CLAUDE.md，统一 logger，machine-search-first key=value）
- `数据库备份成功 path=... bytes=...` / 失败 `数据库备份失败 reason=...`；清理 `备份清理 deleted=N kept=M`；调度启停。

## Acceptance Criteria

- [ ] Web UI「数据库自动备份」区可配开关/间隔/保留，保存落 `.env`、快照回填、默认 True/24/30。
- [ ] 启动时（enabled）立即生成一份快照；之后每 `interval_hours` 一份。
- [ ] 快照是可正常打开的 SQLite 文件（表/数据完整）；WAL 未 checkpoint 帧不丢（用 `.backup()`）。
- [ ] 保留数量生效：超过则删最旧；retention≥现有份数时不误删。
- [ ] enabled=False 不备份；改间隔/开关运行时生效。
- [ ] 备份/清理失败 best-effort，不崩 bot。
- [ ] int 设置范围校验（interval/retention 越界报错或回退）。
- [ ] 单测覆盖 backup/prune/settings/loop 判定；ruff/pyright/测试全绿。

## Decision (ADR-lite)

- **Context**：需要定时备份 SQLite，bot 无 apscheduler，DB 为 WAL。
- **Decision**：sqlite3 `.backup()` 在线备份（WAL 安全）+ NoneBot on_startup 启动 asyncio tick-loop（无新依赖）；设置仿现有 `settings_service` + Web UI 设置区。默认 开/24h/30 份，启动先备一次。
- **Consequences**：无新依赖；改间隔运行时生效；备份占盘随 retention 增长（用户可调）。

## Out of Scope

- 不做备份恢复/还原 UI（仅自动备份 + 保留）。
- 不做远程/云备份、不压缩（单 `.db` 文件，沿用 sqlite 体积）。
- 不做手动「立即备份」按钮（仅启动 + 定时；如需后续加）。

## Technical Notes

详见 `research/blueprint.md`。改动：`nextbot/db_backup.py`(新)、调度接线（`bot.py` on_startup/shutdown 或新 plugin）、`server/settings_service.py`、`server/webui/templates/settings_content.html`、`server/webui/static/js/settings.js`、`tests/`、`.gitignore`（若 DATA_DIR 为仓库根需忽略 `backups/`）。
- 复用：`nextbot/db.py`（DB_PATH/get_engine）、`nextbot/data_dir.py`（DATA_DIR）、`nextbot/time_utils`（北京时间戳）、设置系统、Web UI 设置页结构。
