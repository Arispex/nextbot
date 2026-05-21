# chore: 清理 sync 重构遗留 (tutorial 文案 + 死权限项)

## Goal

sync 重构（commit `c42dd91`）下线了 `同步白名单` 命令，但还留了 2 处 stale 引用：教程数据里的第 4 步、`user.whitelist.sync` 死权限注册项。彻底清理这两处。

## Requirements

### R1 — tutorial_data.py 清理"第 4 步：同步白名单"

`nextbot/plugins/tutorial_data.py` 现有结构：第 1-7 步。

- **删除** 第 4 步整个 dict（line 38-47 `第 4 步：同步白名单（仅在提示白名单错误时执行）`）。
- **重新编号** 原 step 5-7 → 新 step 4-6（手工改 `title` 字段里的"第 N 步"前缀）。
- **修改** 原第 3 步（line 36）的 tip 文案：`如果游戏提示「不在白名单内」，先回群里继续第 4 步。` → 改为 `如果游戏提示「不在白名单内」，请联系群管理员排查（注册账号时机器人应已自动加白）。`（因为现在注册账号会自动 sync 白名单，普通用户已经无能为力，只能找管理员）。
- 不动其他教程章节（仓库系统等）。

### R2 — 死权限项清理

#### R2.1 静态注册项
- **删除** `nextbot/db.py:89` `DEFAULT_GUEST_PERMISSIONS` 集合里的 `"user.whitelist.sync"`。
- **删除** `nextbot/permissions.py:124` `DANGEROUS_PERMISSION_PREFIXES` 集合里的 `"user.whitelist.sync"`（含末尾注释）。

#### R2.2 DB migration（清理现存 user / user_group 行中的残留）

新增 `ensure_purge_user_whitelist_sync_permission_schema()` migration（参照 `ensure_user_password_hash_schema` 模式）：

- 扫描 `user` 表和 `user_group` 表的 `permissions` 字段（comma-separated string）。
- 把每行的 permissions 字符串里的 `user.whitelist.sync` 项移除（小心 split / rejoin、空字符串与逗号边界）。
- 用一个 UPDATE 把所有受影响行更新（`permissions != ''` 的全行 SELECT + per-row recompute + UPDATE 也可，量小无所谓）。
- 注册到 `_run_migration` 链。
- 失败应抛出（让启动失败暴露问题），与现有 ensure_* 一致。

实现注意事项：
- 用 SQL 直接 UPDATE：`UPDATE user SET permissions = <rebuilt> WHERE permissions LIKE '%user.whitelist.sync%'`。
- 重建 permissions 字符串：`",".join(p for p in old.split(",") if p.strip() != "user.whitelist.sync")`。
- 不要漏 trailing/leading comma 边界（split 后过滤后用 `,` join，空 token 顺手过滤）。
- 添加 [INFO] 日志：`migration 清理：table=<name> affected_rows=<n>`，便于运维确认。

## Acceptance Criteria

- [ ] `nextbot/plugins/tutorial_data.py`：`grep "同步白名单"` 在 `tutorial_data.py` 内返回 0。
- [ ] 教程第 1-3 步保持不变（标题与正文），原第 4 步内容彻底消失。
- [ ] 重新编号后存在第 4-6 步（原 5-7），每个 step 的 `title` 字段前缀都对齐新编号。
- [ ] 原第 3 步的 tip 文案已更新为指引用户找管理员排查。
- [ ] `grep -rn "user.whitelist.sync" nextbot/` 在 active code 中（排除 archive / journal / DB 内容）应该 **0 命中**。
- [ ] Migration 跑完后：所有 user / user_group 行的 permissions 列都不含 `user.whitelist.sync`。
- [ ] Migration 是幂等的（连跑 2 次第二次 `affected_rows=0`）。
- [ ] 启动日志能看到 migration 执行情况（包括 affected_rows）。
- [ ] WebUI 用户管理 / 权限组页面打开正常，编辑某个 guest user 的权限时**不再**看到 `user.whitelist.sync` 选项（如果该项原本会渲染成 checkbox / list 项）。

## Definition of Done

- 通过 trellis-check。
- 启动机器人不报错。
- 教程页面浏览正常（slug=新手教程 路由可达）。
- migration 写入和 grep 验证都干净。

## Out of Scope

- 不动其他教程章节（仓库系统等）。
- 不动 sync 重构本身的代码（已在上一个任务完成）。
- 不动 `nextbot/audit.py` 等如果有历史 audit log 里残留 `user.whitelist.sync` 的字符串记录（审计是历史快照，不该改）。
- 不重命名权限或调整其他权限默认。

## Technical Notes

- 教程数据：`nextbot/plugins/tutorial_data.py` 顶层 `TUTORIALS["新手教程"]["steps"]` 列表
- 权限静态注册：`nextbot/db.py:89` `DEFAULT_GUEST_PERMISSIONS`、`nextbot/permissions.py:124` `DANGEROUS_PERMISSION_PREFIXES`
- Migration 模式：`nextbot/db.py:444 _run_migration` + `nextbot/db.py:467+ _run_migration("name", ensure_*_schema)`
- 参考实现：`ensure_user_password_hash_schema()`（同文件，schema migration 范本）
- permissions 列：`user.permissions` / `user_group.permissions`，类型 `String`，comma-separated 字符串
