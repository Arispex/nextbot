# 仪表盘命令总数 / 已启用命令数把已下线命令也算进去

## Goal

修复 WebUI 仪表盘 `命令总数` / `已启用命令` 数字 stale 的 bug：当代码里删除了某个命令，仪表盘数字没下降；命令页面数字正确。

## Root Cause

`sync_registered_commands_to_db()`（`nextbot/command_config.py:773-779`）是**软删除策略**：代码里删除的命令在 DB 里只把 `is_registered = False`，行不删除（保留历史 param_values，便于命令重新上线时复用配置）。

- **命令页面**（`command_config.py:592-601` `list_command_configs`）：过滤 `is_registered=True` ✅
- **仪表盘**（`nextbot/stats.py:80-88`）：`COUNT(CommandConfig.command_key)` 全表 COUNT，**没过滤** `is_registered` ❌

→ 仪表盘把 `is_registered=False` 的残留行也算进 `command_total` / `command_enabled_count`，数字与命令页面对不齐。

## Scope

仅 `nextbot/stats.py` 2 处 query 加 filter：

| 位置 | 修改前 | 修改后 |
|---|---|---|
| L80-82 `command_total` | `session.query(func.count(CommandConfig.command_key)).scalar()` | 加 `.filter(CommandConfig.is_registered.is_(True))` |
| L83-88 `command_enabled_count` | `session.query(func.count(...)).filter(CommandConfig.enabled.is_(True)).scalar()` | 追加 `.filter(CommandConfig.is_registered.is_(True))` |

`command_disabled_count = max(command_total - command_enabled_count, 0)` 自动跟随，不需改。

## Out of Scope

- 不改软删除策略 —— `is_registered=False` 的残留行保留（产品决策：命令重新上线时复用历史 param_values）
- 不改命令页面（已经过滤正确）
- 不改 `command_execute_count`（这个是累计执行次数，与命令是否在线无关）
- 不改其他仪表盘字段

## Acceptance Criteria

- [ ] `nextbot/stats.py` 中 `command_total` / `command_enabled_count` 两个 query 都过滤 `CommandConfig.is_registered.is_(True)`
- [ ] 仪表盘数字与命令页面（`list_command_configs` 长度）一致
- [ ] 已下线命令的 DB 行未被删除（软删除语义保留）
- [ ] 人工验证：删除一个命令 + 重启 → 仪表盘 `命令总数` / `已启用命令` 数字下降，与命令页面一致

## Technical Notes

- `CommandConfig.is_registered` 字段在 `nextbot/db.py:178+` 定义
- 这个 bug 是删命令的副作用：之前的 task 删除了命令但没 audit 仪表盘 count
