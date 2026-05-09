# 主代理二次复查结论

**日期**: 2026-05-09
**复查范围**: permission-a-findings.md (25 项) + permission-b-findings.md (30 项)

---

## 复查方法

主代理逐项读源码 + 实测：
- `permissions.py:19-23` `_match_permission` 通配符行为（验证 `.*` 后缀逻辑）
- `permissions.py:62-67` `has_permission` owner 短路（验证 owner 是 .env 而非 DB 组）
- `group_manager.py:119` 验证只有 `删除身份组` 检查 reserved name `{guest, default}`，`添加身份组` / `取消继承身份组` / `修改用户身份组` 都没检查
- `group_manager.py:134` 验证 `删除身份组` reassign 到 `guest` 而非 `default`
- `permission_manager.py:95-107, 219-234` 验证 `添加用户权限` / `修改用户身份组` 缺 owner check

---

## ✅ 真实 critical（必修）

| ID | 复查结论 |
|---|---|
| **PMA-2.1** 添加身份组允许保留名 | ✅ 真。grep 确认 `group_manager.py:83-92` 仅 SELECT-then-INSERT，无 reserved-name 检查。可以创建 `owner` / `admin` / `root` 命名的组，但因为 owner 是 .env 短路非 DB 组，单独无害；与 PMA-6.1 + PMB-3.1 串联即提权链。 |
| **PMA-3.1** 删除身份组 reassign 到 guest | ✅ 真。`group_manager.py:134` 硬编码 `User.group: "guest"`。`db.py` 同时 seed 了 `default`（继承 guest）作为 baseline，但删除 cascade 跳过 default 直接到 guest，silently strips inheritance perms。 |
| **PMA-6.1** 添加身份组权限无 allow-list | ✅ 真。`group_manager.py:259-269` 直接 `add_permission()` 任何字符串。配合 `_match_permission` (`permissions.py:20-22`) 的 `.endswith(".*")` 通配，可授予 `permission.*` 给低权组 → 提权链关键一环。**注意：孤立 `*` 不匹配（必须以 `.*` 结尾），子代理已正确识别。** |
| **XC-1 + PMB-3.1** 修改用户身份组无 owner protection + hierarchy guard | ✅ 真。`permission_manager.py:219-234` 直接 `user.group = group_name + commit`，无 owner check，无层级护栏。这是**单一最严重提权向量**——任何持 `permission.user.group.set` 的用户可移动 owner 出组，或自移动到任意高权组。 |
| **PMB-1.1** 添加用户权限 privilege ratchet | ✅ 真。`permission_manager.py:95-107` 没有 "actor 必须持有所授权限" 检查，没有 target ≠ self 检查。持 `permission.user.add` 的用户可自授任意权限包括 `permission.user.group.set`。 |

## ✅ 真实 high（应修，按根因合并）

| 根因 | 涉及 ID | 复查结论 |
|---|---|---|
| **lost-update on CSV** | PMA-6.2 / 7.1 / 4.2 / PMB-1.2 / 2.2 / 3.2 / XC-3 | ✅ 真。所有 CSV 读改写都没有条件 UPDATE，并发授权 / 撤销互相覆盖；ORM dirty-set UPDATE 写所有列让 `User.group` 与 `User.permissions` 还互相干扰。 |
| **审计日志缺 operator** | PMA-3.3 / 6.3 / 7.2 / PMB-1.4 / 2.4 / 3.3 / 5.2 / XC-2 | ✅ 真。所有 9 个 mutation handler 的 `logger.info` 把 target 写为 `user_id`，actor 完全缺失。permission 改动是审计最高优先级，必修。 |
| **继承循环** | PMA-4.1 / 4.3 | ✅ 真。`_get_group_permissions` (`permissions.py:43-59`) 用 visited 集合避免栈溢出，但 `继承身份组` 自身没拒绝循环，运维不可见。 |
| **删除身份组并发** | PMA-3.2 | ✅ 真。SQLite 默认 BEGIN DEFERRED，`删除身份组 X + 继承身份组 Y X` 并发可留 dangling reference。 |
| **permission key registry** | PMA-CC-2 / XC-4 / PMB-1.3 | ✅ 真。`add_permission` 接受任意字符串，typo `economy.singin` 静默存入，运行时永不命中。 |
| **`_fetch_nickname_via_bot` N+1** | PMB-4.1 | ✅ 真。`permission_manager.py:286-289` for 循环串行 await + 无 timeout。改 `asyncio.gather` 即可。 |

## 🔧 严重度调整

| ID | 子代理评级 | 主代理评级 | 理由 |
|---|---|---|---|
| **PMB-5.1** 同步访客权限 additive-only | 🔴 critical | 🟡 medium | 子代理自己说"作为设计是 additive idempotent"，token + caller-id 已正确防伪造。"无法做 reset 工具"是新功能需求而非漏洞。可保留为"建议补 `重置访客权限` 命令"。 |
| **PMA-1.1** 身份组列表 message size | 🟡 medium | 🟢 low | 当前 N×M 实测 < 4KB（30 组 × 50 perm CSV ≈ 1.5KB），需要极端规模才会超限。可降级。 |
| **PMA-CC-5** 透传性提权 | ℹ️ info | ℹ️ info | 是结构性的 delegated 权限管理特性，不属本批次缺陷。 |
| **PMB-4.2** screenshot OOM | 🟡 medium | 🟢 low | 管理员列表受 `.env owner_count` 上限，不可任意扩张；无紧迫性。 |
| **PMA-3.6 / PMA-2.3** | 🟢 low | 🟢 low | 保留。|

## ❌ 误判 / 不予采纳

| ID | 子代理评级 | 主代理结论 |
|---|---|---|
| **PMA-1.3 / PMB-1.5 / 1.6** N+1 / 多 token / 双 parse | ℹ️ info | 都是子代理自己标 info 的"未触发但需注意"，可剔除或保留作 future-proof 注释。 |
| **PMB-3.6** demote last holder of high-priv group | 🟢 low | 业务逻辑取舍，非缺陷。剔除。 |
| **PMB-4.4 / 4.5** | 🟢 low | 非 V11 fallback / nickname log，仅观察项。 |
| **PMB-5.5 / 5.6 / 5.7** | 🟢 low | 子代理自己说"defensive only"或"verified safe"。可剔除。 |
| **PMA-3.4** cache 失效（forward-looking） | 🟡 medium | 当前无 cache，无缺陷。剔除。 |

## 🔁 去重

- **lost-update on CSV**：6 处同根因 → 一处修法（条件 UPDATE / `BEGIN IMMEDIATE` / 改用 `update().where().values(only_changed_column)`）
- **审计日志补 operator_id**：9 处同根因 → 一处 helper（`audit_permission_change()`）
- **owner 保护缺失**：3 处（添加 / 删除 / 修改用户权限/身份组）→ 一处 helper（`is_owner(user_id)` + 各 mutation handler 调用前检查）
- **permission key registry**：3 处（添加身份组权限 / 添加用户权限 / 删除用户权限）→ 一处 registry 抽出（从 `command_config._registry` 构建）
- **保留名 + 名称正则**：2 处（添加身份组 / 取消继承身份组）→ 一处 helper（`RESERVED_GROUP_NAMES` + name regex）
- **继承循环检查**：1 处（继承身份组 DFS 检查）

---

## 主代理整体看法

1. **本次审计的"真正大问题"集中在 5 条 critical**：
   - PMB-3.1 修改用户身份组无 owner 保护 + 无层级护栏（**单一最严重提权向量**）
   - PMA-2.1 + PMA-6.1 添加身份组无保留名 + 添加身份组权限无 allow-list（**提权链关键两环**）
   - PMB-1.1 添加用户权限 privilege ratchet（**自授提权**）
   - XC-1 所有 mutation handler 缺 owner protection（**结构性缺失**）
   - PMA-3.1 删除身份组 reassign 到 guest 而非 default（**业务设计 + 数据级影响**）

2. **设计性建议**（值得用户优先关注）：
   - **`is_owner(user_id)` helper** 抽到 `permissions.py`，所有 mutation handler 调用前检查（与 `apply_ban_to_db` 模式对齐）
   - **`audit_permission_change()` 统一审计** 抽到 `permissions.py`（或新 `nextbot/audit.py`），9 个 mutation handler 共用
   - **`PERMISSION_REGISTRY`** 从 `command_config._registry` 构建，验证授权 key 存在；owner 例外（forward compat）
   - **`POLA` 自授规则**：actor 不能授予自己未持有的权限（owner 例外）
   - **层级护栏**：`修改用户身份组` 时，目标组的 effective perms 必须 ⊆ operator 的 effective perms（owner 例外）
   - **保留名集合**：`RESERVED_GROUP_NAMES = {"owner", "admin", "root", "system", "superuser"}` + 名称正则
   - **继承循环 DFS 检查**：`_would_create_cycle()`

3. **schema migration 几乎不需要**：本批修复都是逻辑层 + helper，DB 层只需要可选的 `User.group` FK / 索引（可放后续）。

4. **owner 是 .env 而非 DB 组**这一前提，决定了上面所有 owner 保护方案都用 `is_owner(user_id) ↔ user_id in .env` 模式，而不是创建 owner 组。
