# 审计权限管理命令的漏洞和性能问题

## Goal

对 NextBot 的"权限管理"分类下的 12 条命令做系统化漏洞 / 性能审计：
- 列出每条命令的潜在漏洞、性能瓶颈、并发风险、注入风险、外部 IO 风险
- 给出问题等级、影响、复现操作、推荐解决方案
- 主代理对子代理结果做二次复查后再交付

## 审计范围

### 主线：分类 = 权限管理

#### `nextbot/plugins/group_manager.py`（327 行，7 命令）
| 命令 | handler | 行号 |
|---|---|---|
| 身份组列表 | `handle_list_groups` | 36 |
| 添加身份组 | `handle_add_group` | 75 |
| 删除身份组 | `handle_delete_group` | 110 |
| 继承身份组 | `handle_inherit_group` | 159 |
| 取消继承身份组 | `handle_clear_inherit_group` | 208 |
| 添加身份组权限 | `handle_add_group_perm` | 252 |
| 删除身份组权限 | `handle_remove_group_perm` | 296 |

#### `nextbot/plugins/permission_manager.py`（459 行，5 命令）
| 命令 | handler | 行号 |
|---|---|---|
| 添加用户权限 | `handle_add_user_perm` | 70 |
| 删除用户权限 | `handle_remove_user_perm` | 132 |
| 修改用户身份组 | `handle_set_user_group` | 194 |
| 管理员列表 | `handle_admin_list` | 272 |
| 同步访客权限 | `handle_sync_guest_perms` | 345 |

### 共享底层
- `nextbot/permissions.py`（140 行）：`@require_permission` / `has_permission` 等
- `nextbot/access_control.py`（83 行）：owner 列表、access control

## 审计关注维度

1. **越权 / 提权**：能否通过这些命令把自己提升为 admin？能否绕过 owner 保护？
2. **并发 / 竞态**：权限 / 身份组的 read-modify-write、并发授权 / 撤销冲突
3. **注入**：身份组名 / 权限 key 进入 DB 或 URL 的路径
4. **数据一致性**：`Group` 表与 `User.group` / `User.permissions` 的同步
5. **资源 / 性能**：N+1 查询、串行 fan-out（管理员列表会不会查所有 user？）
6. **可观测 / 错误传播**：审计日志是否记录 actor / target / 之前权限快照
7. **schema migration**：`同步访客权限` 涉及 `DEFAULT_GUEST_PERMISSIONS` 的同步逻辑，要看启动时是否已自动同步

## 验收标准

### 审计阶段（已完成）
- [x] 每条命令产出完整审计条目
- [x] 主代理对每条问题做二次复查
- [x] 结果汇总到 `research/permission-{a,b}-findings.md` + `research/main-agent-recheck.md`

### 用户关键澄清（2026-05-09）
- **owner 不是 DB 分组**：是 `.env` `owner_id` 短路，owner 用户可被分到任意组、可被授任意权限，对其实际有效权限无影响
- **不需要 owner 行级保护**：`修改用户身份组` / `添加用户权限` / `删除用户权限` 不需要拦截 owner target

### 修复范围（用户决策：D 全修）

#### 🔴 Critical（3 个根因）

| # | ID | 概要 |
|---|---|---|
| 1 | PMB-3.1 | 修改用户身份组 POLA 层级护栏：目标组 effective perms ⊆ operator effective perms（owner 例外） |
| 2 | PMA-6.1 + PMB-1.1 | 添加身份组权限 / 添加用户权限 POLA + dangerous-key blocklist + 禁止自授（owner 例外） |
| 3 | PMA-3.1 + PMA-3.6 | 删除身份组 reassign 到 `default`（不是 guest）+ 二次确认 + cascade preview |

#### 🟠 High（6 个根因）

| # | ID | 概要 |
|---|---|---|
| 4 | PMA-6.2 / 7.1 / 4.2 / PMB-1.2 / 2.2 / 3.2 / XC-3 | Lost-update on CSV：用 `update().where(col == old).values(only_changed_column)` + retry，避免 ORM dirty-set 跨列覆盖 |
| 5 | PMA-3.3 / 6.3 / 7.2 / PMB-1.4 / 2.4 / 3.3 / 5.2 / XC-2 | 抽 `nextbot/audit.py:audit_permission_change()` helper，9 个 mutation handler 共用（actor / before / after / cascade counts），WARN 级 |
| 6 | PMA-4.1 / 4.3 | 继承身份组 commit 前 DFS cycle check + `MAX_INHERIT_DEPTH = 8` |
| 7 | PMA-3.2 | SQLite engine 配置 `BEGIN IMMEDIATE` 序列化删除 cascade（或 sqlite_pragmas 配置）|
| 8 | PMA-CC-2 / XC-4 / PMB-1.3 | Permission key registry：从 `command_config._registry` 构建白名单 + difflib 建议 + owner 例外 |
| 9 | PMB-4.1 | `_fetch_nickname_via_bot` 改 `asyncio.gather(*[wait_for(fetch, timeout=5.0)...])` |

#### 🟡 Medium（8 项）

| # | ID | 概要 |
|---|---|---|
| 10 | PMA-2.2 | 身份组名正则 `re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", name)` |
| 11 | PMA-2.3 | `添加身份组` IntegrityError 捕获 + reply_failure |
| 12 | PMA-5.1 | 取消继承身份组：拒绝对 `default` / `guest` 操作 |
| 13 | PMB-2.3 + PMB-3.4 + PMA-7.3 + PMA-5.2 | no-op 静默成功改为 `ℹ️ 已 / 未 / 无变化` |
| 14 | PMB-3.5 | 修改用户身份组 group name 查询前 `.lower()` 标准化 |
| 15 | PMB-5.1 | 新增命令 `重置访客权限`：二次确认后 replace_with(DEFAULT_GUEST_PERMISSIONS) + 列出将移除的 key（同步访客权限的 reset 对偶）|
| 16 | PMA-1.1 | 身份组列表分页支持（每页 10 组）+ 单条 perm CSV 截断 + `身份组列表 [page]` |
| 17 | PMB-4.2 | 管理员列表 base64 加 `large_image.MAX_BASE64_BYTES` 上限 |

#### 🟢 Low / Defense-in-depth（5 项）

| # | ID | 概要 |
|---|---|---|
| 18 | PMA-2.1 | `RESERVED_GROUP_NAMES = {"owner", "admin", "root", "system", "superuser"}` 拒绝创建（降级理由：owner 是 .env 短路，但仍消除 UI 误导）|
| 19 | PMA-1.2 | 身份组列表当前需显式权限，文案上隐藏 perm 详情，仅展示 count（与 #16 合并）|
| 20 | PMA-3.5 | 删除身份组日志补 `reassigned_users` / `updated_child_groups`（合并到 #5 审计 helper）|
| 21 | PMB-2.5 | 删除用户权限：若 perm 来自组继承则提示"权限来自身份组继承，不可单独删除" |
| 22 | PMB-1.5 / 1.6 | 文档化 multi-token edge case，缓存 args 避免双 parse |

### 实施阶段验收

- [ ] 上述 22 个修复模块全部落地
- [ ] **无破坏性更新**：所有命令外部行为对 V11 保持兼容（成功路径输出格式不变；错误路径新增 POLA / blocklist / typo / 循环 / 保留名等明确文案；新增 ⚠️ 部分成功 / preview / 二次确认）
- [ ] **开箱即用**：本次预期无 schema 变化（仅新增 helper / 常量 / 文件，启动时 `init_db` 后自动构建 PERMISSION_REGISTRY）
- [ ] **失败文案符合规范**：`reply_failure(action, reason)` 不拼"动作 + 结果，原因"
- [ ] **审计日志规范**：所有 mutation 走 `audit_permission_change()`，WARN 级 `key=value` 机器可搜
- [ ] **owner 不受限制**：`is_owner(user_id)` 例外路径覆盖 POLA / blocklist / 层级护栏 / 注册名校验
- [ ] **修后再检查**：派 trellis-check 子代理对照 findings + recheck 再走一遍

## Out of Scope

- 其他分类的命令（已审完 10 个分类）
- WebUI 中的权限管理页
- `@require_permission` 装饰器本身的实现（仅消费）
- `command_config.py` 现有命令注册逻辑改动（仅新增 `get_permission_registry()` 暴露接口）
- 同步访客权限的两步确认机制（已正确，不动）

## Technical Notes

- 主审目录：`nextbot/plugins/group_manager.py`、`nextbot/plugins/permission_manager.py`
- 配套依赖：`nextbot/permissions.py`、`nextbot/access_control.py`、`nextbot/db.py`、`nextbot/command_config.py`
- 计划新增：`nextbot/audit.py`（`audit_permission_change()` helper）
- 修复模板参考：
  - `nextbot/ban_core.py` 的 `apply_ban_to_db` 条件 UPDATE 模式
  - `economy.py` 条件 UPDATE + `execute_rowcount`
  - `large_image.py` MAX_BASE64_BYTES 上限模式
