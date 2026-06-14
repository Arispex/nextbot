# 持久化日志到每次运行独立文件

## Goal

程序日志当前只输出到 stderr（无持久化）。新增：每次运行把日志写入 `logs/` 下一个**以启动时刻命名的独立文件**（每次运行不同文件），级别 INFO 及以上，保留最新 30 份。

## Requirements

- 启动时尽早为 loguru（`nonebot.log.logger`）追加一个**文件 sink**，与现有 stderr 并存。
- 文件名按进程启动时刻唯一：`logs/nextbot-YYYYMMDD-HHMMSS.log`（**北京时间**，`nextbot.time_utils`）。同一次运行写同一文件，不同运行写不同文件。
- 级别 **INFO 及以上**（与控制台一致，不含 DEBUG）。
- 格式遵循 CLAUDE.md：`[timestamp] [level] <message>`，timestamp 带毫秒 + 时区偏移（北京时间 UTC+8），由 loguru 输出（业务消息不手写时间/级别）。
- **保留最新 30 份** `nextbot-*.log`，超过删最旧（best-effort，启动时清理一次）。
- 文件 sink `enqueue=True`（异步不阻塞）、`diagnose=False`（防异常变量值泄漏到日志）、`encoding="utf-8"`。
- 始终开启（用户未要求开关 / Web UI 配置，保持最小范围）；保留数量用模块常量 `LOG_RETENTION=30`。
- 失败 best-effort：日志目录创建 / sink 添加 / 清理失败不崩 bot（尽量 stderr 仍可用）。

## Acceptance Criteria

- [ ] 启动后 `logs/nextbot-<启动时间>.log` 生成，`logger.info/warning/error` 内容写入该文件。
- [ ] 同一次运行所有日志进同一文件；重启写新文件（文件名不同）。
- [ ] 文件级别 INFO+（DEBUG 不进文件）。
- [ ] 文件格式含 `[timestamp(ms+offset)] [level] message`，时区为北京时间。
- [ ] 保留最新 30 份，超出删最旧；不足 30 不删。
- [ ] sink 尽早添加（捕获 init 期日志）；不阻塞事件循环。
- [ ] 失败 best-effort 不崩；`.gitignore` 忽略 `logs/`。
- [ ] 单测覆盖 sink 写入 + prune；ruff/pyright/测试全绿。

## Decision (ADR-lite)

- **Context**：日志无持久化；NoneBot 用 loguru，无文件 sink；无 apscheduler。
- **Decision**：新建 `nextbot/logging_setup.py`，启动早期 `logger.add(per-run file, level=INFO, enqueue, diagnose=False)` + `prune_old_logs(30)`；在 `bot.py` `nonebot.init()` 前接线。保留 30 份常量、北京时间命名、始终开启。
- **Consequences**：无新依赖；每次运行一个文件、最多留 30 份；如需可配/可关后续再加。

## Out of Scope

- 不做 Web UI 开关 / 设置（用户只要"实现持久化"）。
- 不做单次运行内按大小/时间轮转（一次运行一个文件）。
- 不做远程/集中式日志、不改各业务 `logger.xxx` 调用点。
- 不改控制台 stderr 输出行为。

## Technical Notes

详见 `research/blueprint.md`。改动：`nextbot/logging_setup.py`(新)、`bot.py`（init 前接线）、`.gitignore`（logs/）、`tests/`。
- 复用 `nextbot/data_dir.py`(DATA_DIR)、`nextbot/time_utils`（北京时间戳，参 db_backup 的 `beijing_filename_timestamp`）、`nextbot/db_backup.py` 的 prune 模式。
- loguru format 建议形如 `{time:YYYY-MM-DD HH:mm:ss.SSSZ} [{level}] {message}`（`Z`=offset）；文件 sink 不带颜色标签。
- 测试注意：loguru 全局 sink，测试用 monkeypatch LOG_DIR + 末尾 `logger.remove(sink_id)` 清理，勿污染真实 logs/ 或泄漏到其它测试。
