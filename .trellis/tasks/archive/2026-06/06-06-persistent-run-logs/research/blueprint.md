# 持久化日志（每次运行独立文件）— 研究蓝图

## 现状（已调查）
- 全项目统一用 `from nonebot.log import logger`（NoneBot 的 **loguru** 单例）。
- **无任何文件 sink / `logger.add()`** —— 日志只输出到 stderr（NoneBot 默认 handler）。无自定义日志配置、无 `LOG_LEVEL` 在 .env/config。
- `bot.py`：顶部 import（含 `from nextbot.data_dir import DATA_DIR`，:13）→ `nonebot.init(_env_file=str(ENV_PATH))`（:101）→ `driver = get_driver()`（:103）→ … → `nonebot.load_plugins(...)`（:273）→ `nonebot.run()`（:275）。
- loguru `logger.add(sink, ...)` 可在 NoneBot 配置好 stderr handler 后**追加**一个文件 sink，两者并存。越早 add 捕获越全（含 init 期日志）。

## 实现蓝图

### A. 日志配置模块（新建 `nextbot/logging_setup.py`）
- `LOG_DIR = DATA_DIR / "logs"`（mkdir(parents=True, exist_ok=True)）。
- 每次运行唯一文件名：`nextbot-YYYYMMDD-HHMMSS.log`（**北京时间**，用 `nextbot.time_utils`；进程启动时刻确定一次 → 同一次运行写同一文件，不同运行不同文件）。
- `setup_file_logging() -> Path`：`logger.add(str(path), level=<级别>, format=<格式>, encoding="utf-8", enqueue=True, backtrace=False, diagnose=False)`。
  - `enqueue=True`：异步写，不阻塞；多进程/线程安全。
  - `diagnose=False`：防异常 traceback 泄漏变量值到日志（安全）。
  - format 对齐 CLAUDE.md「[timestamp] [level] <message>」语义（loguru：`{time:YYYY-MM-DD HH:mm:ss.SSSZ} | {level} | {message}` 或复用 NoneBot 风格）。timestamp 带毫秒 + 时区。
  - 返回 path 并 `logger.info("日志持久化已启用 path=... level=...")`。
- 可选 `prune_old_logs(retention)`（若选保留策略）：仿 db_backup.prune，保留最新 N 份 `nextbot-*.log`，删更旧；best-effort。

### B. 接线（`bot.py`）
- 在**顶部 import 之后、`nonebot.init()` 之前**调用 `setup_file_logging()`（尽早，捕获 init 日志）。`DATA_DIR` 已在顶部 import，可直接用。
- 若有保留策略：启动时（add sink 后）调一次 `prune_old_logs(retention)`。

### C. .gitignore
DATA_DIR 默认 = 仓库根 → 新增忽略 `logs/`（与 backups/ 并列）。

### D. 测试
- `setup_file_logging`：调用后 `logger.info(...)` 内容确实写入返回的文件；文件名格式 / 在 LOG_DIR 下；用临时 DATA_DIR/LOG_DIR，**勿污染真实 logs/**（monkeypatch LOG_DIR；注意 loguru 全局 sink 需在测试末尾 `logger.remove(sink_id)` 清理避免泄漏到其它测试）。
- （若有）prune_old_logs：保留 N、删旧。

## 待 brainstorm 决策
1. 旧日志保留策略（全部保留 / 保留最新 N / 按天数）——影响磁盘增长。
2. 文件日志级别（INFO+ 与控制台一致 / 全部含 DEBUG）。
3. （次要，默认否）是否需要 Web UI 开关——用户只说"实现持久化"，默认始终开、不加设置。
