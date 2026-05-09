# Final Sweep Audit (round 13) — 小游戏 / 安全 / 权限

- **Query**: 第 13 轮最终复审，重点找回归 / 遗漏 / 跨文件问题
- **Scope**: internal（8 plugin + 3 core）
- **Date**: 2026-05-09
- **Files reviewed**:
  - `nextbot/plugins/dice.py` / `guess_number.py` / `rob.py` / `rob_protection.py`
  - `nextbot/plugins/ban.py` / `security.py` + `nextbot/ban_core.py`
  - `nextbot/plugins/group_manager.py` / `permission_manager.py` + `nextbot/audit.py`
  - 参考：`nextbot/plugins/group_member_notify.py`、`nextbot/permissions.py`、`nextbot/db.py`、`nextbot/command_config.py`、`nextbot/plugins/economy.py`

---

## 总体评价

经过 12 轮审计后，绝大多数 invariants（owner 短路 / POLA / dangerous-key blocklist /
hierarchy / lost-update conditional UPDATE / BEGIN IMMEDIATE）已经收敛。本轮复审
没有发现 critical / high 等级的回归。但找出 **3 个真实的一致性 / 完整性差距**
（其中 1 个是显式的 lost-update 规则违反，2 个是 audit / 完整性 gap），以及若干
可选的强化建议。

---

## Findings

### SS-1.1 🟠 `同步访客权限` 的 confirm 路径仍走 ORM 写，未用条件 UPDATE

- **File**: `nextbot/plugins/permission_manager.py:778-795`
- **Snippet**:
  ```python
  guest = session.query(Group).filter(Group.name == _SYNC_GROUP_NAME).first()
  ...
  current = set(split_csv_values(guest.permissions))
  actually_added = sorted(set(missing) - current)
  if actually_added:
      guest.permissions = join_csv_values(current | set(actually_added))  # ← ORM write
      session.commit()
  ```
- **Impact**: lost-update 模式不一致。本轮"重置访客权限"（`handle_reset_guest_perms_confirm` line 920-967）已经规范地用
  `update(Group).where(name=_, permissions == old_csv).values(permissions=new_csv)` + retry，但
  对偶的"同步访客权限"仍走 ORM `guest.permissions = ...`。
  - 在 BEGIN IMMEDIATE 全局串行化保护下不会真正 lost-update（事务级互斥），所以**实
    际数据安全**；
  - 但 sweep 维度上违反了 PMA-6.2 / PMA-7.1 / O3 一直强调的"每条 CSV write 都条件
    UPDATE + retry"规则，是回归留下的唯一一处例外；
  - 未来若收窄 SQLite 锁范围 / 改用 PG / 引入 read-replica，这条路径会突然成为唯一
    的 lost-update 入口。
- **复现**:
  1. 两个 owner 同时发"同步访客权限"+"确认"；BEGIN IMMEDIATE 让它们串行，no
     functional bug；
  2. 把 `_force_immediate_begin` 改回 DEFAULT（DEFERRED），并发 commit 即丢失其中
     一次的 added keys。
- **修法**: 与 `重置访客权限` 对齐——把 `guest.permissions = ...` 改成
  `execute_rowcount(update(Group).where(name=_, permissions == old_csv).values(permissions=new_csv))`
  + 5 次 retry；audit 的 `before/after` 用 capture 的 old_csv / new_csv 而不是 count。

---

### SS-2.1 🟠 `封禁用户` / `解封用户` handler 不经 `audit_permission_change`

- **File**: `nextbot/plugins/ban.py:97-100, 260-263`
- **Snippet**:
  ```python
  # ban
  logger.info(
      f"用户封禁成功：operator_id={operator_id} target_user_id={result.user_qq} "
      f"target_name={result.user_name} reason={reason}"
  )
  ```
- **Impact**: 用户级 ban 是最敏感的状态变更（影响所有 server 黑名单、覆盖整个用户的
  command 入口），但 `apply_ban_to_db` / `apply_unban_to_db` 的 caller `ban.py`
  只走 `logger.info`，未调用 `audit_permission_change`。
  - 对比：`group_member_notify.handle_auto_ban_on_leave`（line 203-214）会调用
    `audit_permission_change(action="user.ban.auto_on_leave", ...)`。被动 auto-ban
    走统一审计入口，**主动 ban / unban 反而漏了**——这是 audit 完整性的一条线
    断裂。
  - 后果：审计聚合（按 actor / target / action 索引）时无法把"管理员 X 在
    2026-05-09 封禁了 Y"挑出来；只能 fallback 到全局 `logger.info` 的 grep。
  - 上一轮 PRD 显式要求"9 处 mutation handler + 1 个 重置访客权限 +
    group_member_notify auto-ban —— 全都调了吗" → ban / unban 是这条规则的**两个
    遗漏**。
- **复现**: 启动 bot，发"封禁用户 @target reason"，查 logger 输出——只有
  "用户封禁成功" + "封禁用户黑名单同步完成"，没有 `权限审计：actor=... action=user.ban.manual ...`。
- **修法**: 在 `handle_ban` 成功路径（line 100 之后）调用：
  ```python
  audit_permission_change(
      actor_user_id=operator_id,
      action="user.ban.manual",
      target=result.user_qq,
      before={"is_banned": False},
      after={"is_banned": True, "ban_reason": reason},
      context={"target_name": result.user_name},
  )
  ```
  对偶在 `handle_unban` 加 `action="user.unban.manual"`。owner_protected 拒绝路径
  也建议补一条 `action="user.ban.manual.denied" reason="owner_protected"`。

---

### SS-3.1 🟡 `_check_user_perm_mutation_pola` 在 self-grant / 未知权限 key 路径未触发 denied audit

- **File**: `nextbot/plugins/permission_manager.py:144-174`
- **Snippet**:
  ```python
  if is_grant and target_user_id == operator_id:
      return False, reply_failure(action_label, "不能为自己添加权限")
  if is_grant and not validate_permission_key(permission):
      suggestions = suggest_permission_keys(permission)
      hint = f"。是否想说：{', '.join(suggestions)}" if suggestions else ""
      return False, reply_failure(action_label, f"权限名称不存在{hint}")
  if is_dangerous_permission(permission):
      audit_permission_change(...)        # ← 有
      return False, ...
  if not has_permission(operator_id, permission):
      audit_permission_change(...)        # ← 有
      return False, ...
  ```
- **Impact**: 4 条拒绝路径只有 2 条（dangerous_key、pola）打了 denied audit；前 2
  条（self_grant、unknown_key）只回 user 一条文案就 return。
  - 后果：尝试自我提权 / 拼写未注册 key 的攻击行为不留 audit 痕迹，sec 团队监测不
    到"用户在试探权限模型"的信号。
  - 回归性质：本轮新加的 `_check_user_perm_mutation_pola` helper 抽出来后，
    self_grant 检查从原来的 `add` handler 内迁过来时漏掉了 audit；旧 `add` handler
    也没有，所以严格说不是回归，但 sweep 维度该补齐。
  - `group_manager.handle_add_group_perm` (line 641-650) 对 unknown_key 同样只回
    文案不审计。
- **修法**: 在 self_grant、unknown_key 两条路径上加 audit：
  ```python
  audit_permission_change(
      actor_user_id=operator_id,
      action=audit_action_denied,
      target=target_user_id,
      context={"permission": permission, "reason": "self_grant"},  # 或 "unknown_key"
  )
  ```

---

### SS-4.1 🟡 `继承身份组` 缺 POLA 层级护栏（defense-in-depth gap）

- **File**: `nextbot/plugins/group_manager.py:432-504`
- **Snippet**:
  ```python
  @require_permission("group.inherit.add")
  async def handle_inherit_group(...):
      ...
      # cycle check + depth check
      # NO POLA / hierarchy check（不像 handle_set_user_group 那样）
      for _ in range(_CSV_UPDATE_RETRY):
          rowcount = execute_rowcount(
              session,
              update(Group)
              .where(Group.name == child, Group.inherits == old_inherits)
              .values(inherits=new_inherits),
          )
  ```
- **Impact**: `handle_set_user_group` 有 PMB-3.1 层级护栏（`forbidden = target_group_perms - operator_perms`），
  但对偶动作 `继承身份组` 没有——理论上 non-owner 拿到 `group.inherit.add` 后可以
  把自己的低权身份组挂在高权 parent 下，绕过 hierarchy 校验拿 parent 的权限。
  - 实际防御依赖：`group.inherit.add` 在 `DANGEROUS_PERMISSION_PREFIXES`，所以
    non-owner 永远拿不到这条权限——blocklist 是当前唯一的防线。
  - 风险点：如果将来 owner 通过 webUI / SQL 直接给某 admin 授了 `group.inherit.add`
    （绕过 dangerous_key 的命令路径），这条命令会让该 admin 合法地把任意子组挂
    到 owner-only 父组下，从而获得任意权限。
  - 严重程度 🟡：当前模型下 unreachable，但 invariant 没有在 handler 内显式校验，
    单点失效（blocklist 改了 / 漏了）→ 立即 critical。
- **修法**: 与 `handle_set_user_group` 保持一致，加：
  ```python
  if not is_owner(operator_id):
      operator_perms = _get_effective_permissions_in_session(session, operator_id)
      parent_perms = _get_group_permissions(session, parent, set())
      forbidden = parent_perms - operator_perms
      if forbidden:
          audit_permission_change(
              actor_user_id=operator_id,
              action="group.inherit.add.denied",
              target=child,
              context={"parent": parent, "forbidden": sorted(forbidden), "reason": "hierarchy"},
          )
          await bot.send(event, at + " " + reply_failure("修改", f"目标父组包含您不持有的权限：{sorted(forbidden)[:5]}"))
          return
  ```

---

### SS-5.1 ℹ️ 重置访客权限 audit context 字段使用 stale preview 数据

- **File**: `nextbot/plugins/permission_manager.py:982-989`
- **Snippet**:
  ```python
  extras: list[str] = matcher.state.get("reset_extras") or []
  missing: list[str] = matcher.state.get("reset_missing") or []
  ...
  audit_permission_change(
      actor_user_id=operator_id,
      action="guest.permissions.reset",
      target=_SYNC_GROUP_NAME,
      before={"permissions": old_csv},
      after={"permissions": new_csv},
      context={"removed": extras, "added": missing},  # ← preview-time，不是真正 diff
  )
  ```
- **Impact**: `extras` / `missing` 来自 preview 阶段的 `matcher.state`。如果在 preview→
  confirm 之间 webUI 或另一条命令改了 guest.permissions，最终 `before/after` 会
  反映真实变更，但 `context.removed / added` 仍是旧 preview 的 diff，**误导**
  audit 审阅者。
  - 不是 security 问题（before/after 准确），仅 observability 噪音。
- **修法**: 在 confirm 路径里基于 `old_csv` / `new_csv` 重新计算 diff：
  ```python
  before_set = set(split_csv_values(old_csv))
  after_set = set(split_csv_values(new_csv))
  context={"removed": sorted(before_set - after_set), "added": sorted(after_set - before_set)},
  ```

---

### SS-6.1 ℹ️ rob.py counter 路径 victim 接收金币时不校验 `rob_protected`

- **File**: `nextbot/plugins/rob.py:352-359`
- **Snippet**:
  ```python
  # 加 victim（counter 成功时）
  session.execute(
      update(User)
      .where(User.user_id == target_user_id)
      .values(
          coins=User.coins + amount,
          rob_total_gain=User.rob_total_gain + amount,
      )
  )
  ```
- **Impact**: 与 success 路径不一致——success 路径 victim 一侧严格 `rob_protected.is_(False)`，
  counter 路径 victim 接收金币时**不校验**。
  - 业务语义：victim 成功反抢，"奖励"金币给 victim；即使 victim 在攻击中开启了
    保护，也应该奖励。**这是合理的** —— 不是 bug，而是设计选择。
  - 但 sweep 维度记一笔：未来若改"开启保护即视为退出抢劫"语义时，这里需要同
    步加校验。
- **修法**: 当前不必修，仅记录设计选择。若未来收紧语义，把 `User.rob_protected.is_(False)`
  也加到 victim 一侧的 where。

---

### SS-7.1 ℹ️ DANGEROUS_PERMISSION_PREFIXES 不含 `admin.*` / `server.*` / `server_tools.*`

- **File**: `nextbot/permissions.py:92-104`
- **Snippet**:
  ```python
  DANGEROUS_PERMISSION_PREFIXES: frozenset[str] = frozenset({
      "permission.user.add", "permission.user.remove", "permission.user.group.set",
      "permission.group.guest.sync", "permission.group.guest.reset",
      "group.permission.add", "group.permission.remove",
      "group.add", "group.delete",
      "group.inherit.add", "group.inherit.clear",
  })
  ```
- **Impact**: registry 里下面这些 key 同样能"通过授权升级特权"，但**不在 blocklist**：
  - `admin.ban` / `admin.unban` / `admin.rename`：能影响其他用户（封号、改名）；
  - `server.add` / `server.delete` / `server.test`：能往 nextbot 注入 / 删除 TShock
    server 端点；
  - `server_tools.execute` / `server_tools.map_image` / `server_tools.download_map`：
    最严重——`server_tools.execute` 等同 RCE（可以发 TShock 任意命令）；
  - `economy.coins.add` / `economy.coins.remove`：能凭空创造 / 销毁金币；
  - `user.whitelist.sync`：能影响所有 server 白名单。

  当前 POLA "actor 须先持有该权限"会兜底（你不能授予自己未持有的权限），但 dangerous
  blocklist 的语义是"即使 actor 自己持有，也不许委派给别人"——例如 owner 委派
  `economy.coins.add` 给某 admin 后，该 admin 不应该再委派出去（防 sub-delegation
  失控）。当前 blocklist 只覆盖了"权限 / 身份组管理类"key，没覆盖"其他 admin
  特权"。
  - 严重程度 ℹ️：是产品策略问题不是 bug；但 sweep 维度建议讨论是否把 `admin.*` /
    `server.*` / `server_tools.*` 至少 `economy.coins.*` 加入 blocklist。
- **修法**（可选）: 扩展集合：
  ```python
  DANGEROUS_PERMISSION_PREFIXES = frozenset({
      ...existing...,
      "admin.ban", "admin.unban", "admin.rename",
      "server.add", "server.delete",
      "server_tools.execute", "server_tools.map_image", "server_tools.download_map",
      "economy.coins.add", "economy.coins.remove",
      "user.whitelist.sync",
  })
  ```

---

### SS-8.1 ℹ️ `_would_create_inheritance_cycle` / `_measure_inherit_depth` 边界 case

- **File**: `nextbot/permissions.py:133-178`
- **Snippet**: DFS 的实现 OK——`split_csv_values` 已 strip + 过滤空字符串
  （`nextbot/permissions.py:11-12`），所以 `inherits=""` / `inherits=","`
  不会进入 stack。`child == new_parent` 在 line 144 直接 True。
  - `new_parent` 为空字符串：`split_csv_values` 已上游在 `add_inherit` 处理掉
    （line 276-279 用 set），所以不会传入 empty。
  - `_measure_inherit_depth` line 177 用 `visited.copy()`，每条分支独立 visited
    集合——正确，避免误判 sibling 路径相互污染。
- **复审通过**。无需改动。

---

## 复审通过的文件 / 维度（无新发现）

| 维度 | 文件 / 范围 | 状态 |
|---|---|---|
| owner 例外覆盖（POLA / blocklist / hierarchy） | `permission_manager.py`、`group_manager.py`、`ban_core.py` (owner_protected) | ✅ 完整且对称 |
| `audit_permission_change` 完整性（除 SS-2.1 / SS-3.1） | `permission_manager.py`、`group_manager.py`、`group_member_notify.py`（auto_ban） | 9 处 mutation handler 全有 success audit；denied audit 覆盖 dangerous_key + pola 两类 |
| Lost-update 条件 UPDATE 覆盖（除 SS-1.1） | `permission_manager.py` add/remove/set_group/reset；`group_manager.py` add/remove/inherit/clear/cascade scrub；`ban_core.py` ban/unban；`rob_protection.py`；`rob.py`；`dice.py`；`guess_number.py`；`economy.py` transfer/sign/remove_coins | 除"同步访客权限" confirm 外，所有 CSV / coin / boolean state write 都用条件 UPDATE |
| rob.py + rob_protection.py 互动 | rob.py 三类路径全部用 `attacker_where_clauses`（含 `rob_protected.is_(False)` + cooldown），victim success 路径加 `rob_protected.is_(False)` | ✅ Capture-before 模式正确，rollback 路径用绝对增量保证可交换 |
| 小游戏与经济竞态 | `dice.py`、`guess_number.py`、`rob.py`：押金扣除 → 计算结果 → 净值/payout 累加，全部条件 UPDATE | ✅ payout=0 / net<0 / net>0 三分支语义正确 |
| group_member_notify auto-ban 路径 | rule 过滤 + isinstance 守卫 + apply_ban_to_db 条件 UPDATE 兜底 + audit_permission_change 入口 | ✅ 已收敛 |
| permission key registry vs blocklist 集合 | `validate_permission_key` 完整匹配 `_registry`；`is_dangerous_permission` 通配规则正确（`permission.*` 覆盖所有 `permission.*` key） | ✅ 但参考 SS-7.1 关于覆盖范围的讨论 |
| `command_config.get_permission_registry()` 完整性 | 79 个 command_key 全部有非空 permission 注册 | ✅ |
| `command_config.get_permission_registry()` 用法 | 仅在 `validate_permission_key` 用作 allowlist（grant 路径），remove 路径不校验以兼容 legacy | ✅ 不存在被错误用作 blocklist 的情况 |

---

## 优先级总结

| ID | Severity | 一句话描述 | 修法成本 |
|---|---|---|---|
| SS-1.1 | 🟠 | 同步访客权限 confirm 用 ORM 写而非条件 UPDATE | 小（10 行 patch） |
| SS-2.1 | 🟠 | 手动 ban / unban 不走 audit_permission_change | 小（4 处加调用） |
| SS-3.1 | 🟡 | self-grant / unknown_key 拒绝路径不打 denied audit | 小 |
| SS-4.1 | 🟡 | 继承身份组缺 POLA 层级护栏（依赖 blocklist 单点防御） | 中 |
| SS-5.1 | ℹ️ | 重置访客权限 audit context 用 stale preview 数据 | 小 |
| SS-6.1 | ℹ️ | rob counter 路径 victim 不校验 rob_protected（设计选择） | — |
| SS-7.1 | ℹ️ | DANGEROUS blocklist 不含 admin.* / server.* / server_tools.* | 中（产品决策） |
| SS-8.1 | — | DFS 边界 case 复审通过 | — |

无 🔴 critical / 🔴 high。本轮整体收敛度高，主要剩 audit 完整性（SS-2.1）和一致性
（SS-1.1）两个值得本轮修复的项。
