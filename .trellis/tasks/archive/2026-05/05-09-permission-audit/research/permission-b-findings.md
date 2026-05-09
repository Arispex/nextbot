# Audit: 权限管理 commands in `nextbot/plugins/permission_manager.py`

- **Scope**: internal (Python bot handlers —权限管理 category, 5 commands / 6 handlers, 459 lines)
- **Date**: 2026-05-09
- **Reference fix patterns**: `apply_ban_to_db` (`nextbot/ban_core.py`) — owner-protection short-circuit + conditional UPDATE; `screenshot_temp.py` — uuid suffix for concurrent screenshot file names; `large_image.py` — OOM constants

---

## Executive summary

The permission-management surface is the most security-critical handler set in the bot. The audit found **3 critical privilege-escalation vectors** and **multiple high-severity gaps**. The single most dangerous pattern is `handle_set_user_group` (line 194) — it accepts ANY existing group name and assigns it without (a) checking whether the target is an owner, (b) checking whether the resulting group hierarchy grants privileges the operator does not itself hold, or (c) blocking the demotion of owners. Cross-cut against `apply_ban_to_db` (`ban_core.py:48`), this file has effectively no owner-protection layer at all.

The file also replicates **two patterns we have already fixed elsewhere**:
1. **Lost-update on `User.permissions` / `User.group`** (string CSV column, ORM read-modify-write) — same shape as the fix landed for `User.is_banned` in `apply_ban_to_db` (PMB-1.2 / PMB-2.2 / PMB-3.2 below).
2. **N+1 + screenshot rendering on a guest-callable handler** — same shape as PQA / SC findings; `管理员列表` defaults to `permission.admin.list` and is already in the `guest` default permission set candidates by analogy with other broadly-callable list commands (verify against `DEFAULT_GUEST_PERMISSIONS`; in current `db.py:34-95` it is NOT in the guest default set, downgrading severity to `medium` rather than `high`).

The two-step `同步访客权限` confirm flow has solid call-site fencing (token literal + caller-id verification + re-diff under SQL session) but exposes a **secondary owner-bypass** if the `permission.group.guest.sync` permission ever leaks to a non-owner — guest can be silently broadened forever, including with administrative permission keys.

---

## Cross-cutting findings

### XC-1 (🔴 critical) — No owner-protection layer for any 权限管理 handler
**Affected**: `permission_manager.py` lines 70-250 (all three mutation handlers)

**Pattern**: `apply_ban_to_db` short-circuits when `str(user.user_id) in get_owner_ids()` (`ban_core.py:48-57`) and emits an audit `logger.warning`. Webui mirrors this at `webui_users.py:560-561`. Permission-manager bot handlers have **zero such checks**:

- `handle_add_user_perm` (line 70) — can grant `*` to owner (no-op-but-confusing) AND, more importantly, can grant `permission.user.group.set` to any non-owner who already holds `permission.user.add` (privilege ratchet).
- `handle_remove_user_perm` (line 132) — can strip permissions from an owner. Owner is still effective via `has_permission` short-circuit (`permissions.py:62-67`), but the User row gets quietly mutated; any operator with `permission.user.remove` can wipe an owner's individually-granted perms with no audit barrier.
- `handle_set_user_group` (line 194) — CAN move owner into `guest`. Owner is still effective via the same short-circuit, but `get_effective_permissions(owner)` (`permissions.py:26-40`) now returns a much smaller set, which silently changes downstream behavior anywhere that consults the union (e.g. UI rendering, cached effective sets, future code paths that rely on `user.group` directly without going through `has_permission`).

**Recommended fix**: extract a `_check_owner_protected(target_user_id) -> bool` helper in `permissions.py` (or reuse `is_owner` if exposed — currently only `has_permission` does the owner short-circuit). Apply it BEFORE every commit in `handle_add_user_perm` / `handle_remove_user_perm` / `handle_set_user_group`. Pattern exactly mirrors `ban_core.py:48-57`. Severity is critical because the asymmetry between `has_permission`'s owner short-circuit and the DB's mutable owner row is exactly the kind of footgun that bites a future contributor refactoring "redundant" code paths.

### XC-2 (🟠 high) — Operator (`actor`) is missing from every audit log line
**Affected**: lines 109, 171, 238-240, 283, 447-450

`ban.py` already established the convention (`ban.py:107`): `operator_id={operator_id} target_user_id={...}`. None of the 权限管理 handlers log the operator. Concrete current lines:

- `添加用户权限`: `logger.info(f"添加用户权限成功：user_id={user_id} permission={permission}")` — `user_id` here is the TARGET, not the actor.
- `删除用户权限`: same shape.
- `修改用户身份组`: `logger.info(f"修改用户身份组成功：user_id={target_user_id} group={group_name}")` — also missing `before_group`.
- `管理员列表`: `logger.info(f"管理员列表查询：owner_count={len(owner_ids)}")` — missing operator (this is read-only but still useful for abuse detection).
- `同步访客权限`: `logger.info(f"同步访客权限成功：group={_SYNC_GROUP_NAME} added={actually_added} target_count={target_count}")` — missing operator entirely.

For 修改用户身份组 specifically, the audit line MUST include `operator_id=`, `target_user_id=`, `target_name=`, **`before_group=`**, `after_group=` so a privilege-escalation attempt is reconstructable from logs alone.

**Recommended fix**: capture `operator_id = event.get_user_id()` at the top of each handler (already done implicitly when computing `at`); log it in success and failure lines per the `ban.py:107` pattern.

### XC-3 (🟠 high) — `User.permissions` / `User.group` mutation is read-modify-write on a CSV string, vulnerable to lost update
**Affected**: lines 103, 165, 232 (and same shape exists in `group_manager.py:268, 312`)

```python
user.permissions = add_permission(user.permissions, permission)  # line 103
```

`add_permission` (`permissions.py:119-122`) does `set(split_csv_values(value))`, `.add(permission)`, `join_csv_values(...)`. Two admins concurrently calling `添加用户权限 X perm.a` and `添加用户权限 X perm.b` → both read the same starting CSV → both write back without the other's addition → one perm is silently lost. Same shape applies to `删除用户权限` (concurrent grant during a revoke wins, the perm "comes back"). For `修改用户身份组` (line 232) the race is between `添加用户权限` and `修改用户身份组` — the perm grant runs against the old user object, group change runs in parallel — depending on commit ordering one overwrites the other on the row.

This is the EXACT shape of the bug fixed by SB-1.4 in `ban_core.py:64-89` (conditional UPDATE with `where(User.is_banned == False)` + rowcount check). Permissions/group don't have a clean idempotent equivalent (the CSV is set-valued), but two routes work:
- **Optimistic version column** (`User.permissions_version` int, increment + WHERE on previous version, retry on rowcount=0).
- **Database-level locking** with `SELECT ... FOR UPDATE` (SQLite supports `BEGIN IMMEDIATE`).

**Recommended fix**: simplest path — wrap the read-modify-write in `BEGIN IMMEDIATE` (engine-level lock for SQLite, since the entire DB is one writer anyway in this app). Alternative: switch to a normalized `user_permission(user_id, permission)` table with `INSERT OR IGNORE` / `DELETE WHERE`, eliminating the lost-update class entirely. Note that the 同步访客权限 confirm path at lines 419-431 already does the right thing — it re-reads under the same session, then unions with `actually_added` — copy that pattern to the user-permission handlers.

### XC-4 (🟡 medium) — No permission-key whitelist; arbitrary unknown keys can be silently granted
**Affected**: line 95, 157 (no validation of `args[1]`)

```python
permission = args[1]
...
user.permissions = add_permission(user.permissions, permission)
```

If an operator types `添加用户权限 12345 some.fake.perm`, the value lands in the DB and is later returned by `get_effective_permissions`. Because `_match_permission` (`permissions.py:19-23`) supports `.*` wildcards, an attacker who guesses an upcoming feature's permission prefix (e.g. `payments.*`) can pre-stake a grant that activates the moment that feature ships. There is no `command_config`-derived whitelist and no `set` of valid keys to validate against, even though `command_config.py` already maintains `_registry: dict[str, RegisteredCommand]` keyed on `command_key` (which is the same string as `permission` for every handler in `permission_manager.py`).

**Recommended fix**: add a `validate_permission_key(key: str) -> bool` that consults the live `_registry` (or a snapshot exposed by `command_config`). Reject unknown keys at the handler with `reply_failure("添加", "权限名称不存在")`. Allow `.*` patterns by stripping the suffix before lookup. Owner can still grant arbitrary keys for forward compat by gating the validation behind `if not is_owner(operator_id)`.

### XC-5 (ℹ️ info) — `管理员列表` rendering follows the screenshot-OOM pattern but is not currently exposed to guests
**Affected**: lines 272-311

`handle_admin_list` calls `screenshot_url` with `fit_content_height=True` and base64-encodes the file with `screenshot_path.read_bytes()` (line 304) — exactly the surface that PQA-3.2 / SC-N concerns target. Two facts mitigate severity:
1. `permission.admin.list` is NOT in `DEFAULT_GUEST_PERMISSIONS` (`db.py:34-95`), so guests cannot trigger it without an admin granting it.
2. The grid renders one card per owner; owner count is set by `.env` and is small (typically <10).

If permission is later granted to guests, or if attackers discover a way to grant themselves `permission.admin.list` (cf. XC-4), the handler reads the entire screenshot into a Python `bytes` object before base64-encoding. Apply `large_image.MAX_BASE64_BYTES` as a guardrail.

---

## Per-command findings

---

## 1. `添加用户权限` → `handle_add_user_perm` (line 70)

### PMB-1.1 (🔴 critical) — Privilege ratchet: holder of `permission.user.add` can grant themselves any permission, including `*`
**File**: `permission_manager.py:60-119`

**Current code** (line 95-107):
```python
permission = args[1]
session = get_session()
try:
    user = session.query(User).filter(User.user_id == user_id).first()
    if user is None:
        await bot.send(event, at + " " + reply_failure("添加", "用户不存在"))
        return

    user.permissions = add_permission(user.permissions, permission)
    target_name = str(user.name)
    session.commit()
finally:
    session.close()
```

**Impact**: a user A who has `permission.user.add` (whether granted directly or via group) can:
1. Type `添加用户权限 <A's own QQ> *` → A now matches every permission via `_match_permission`'s `.*` branch (`permissions.py:20-22` — `granted=".*"`, `prefix="."`, `required.startswith(".")` is True for any string starting with `.`; it also fires for the literal `*` case where `granted="*"` doesn't end with `.*` but the wildcard handling could still be widened).
2. More realistically: `添加用户权限 <self> permission.user.group.set`, then `修改用户身份组 <self> <some_high_priv_group>` — full takeover of every permission key the bot manages, EXCEPT what the owner short-circuit blocks.

There is no check that `permission` is one the operator themselves holds, no check that target ≠ self, no check against owner-only keys (e.g. `permission.user.group.set` should arguably never be self-grantable).

**Reproduction**:
1. Owner grants user A `permission.user.add` (legitimately, intending A to manage low-level perms).
2. A sends `添加用户权限 <A's QQ> permission.user.group.set`.
3. Bot replies with success; A's `User.permissions` now contains both keys.
4. A sends `修改用户身份组 <A's QQ> default` (or any group). With XC-1 unfixed, A is now in `default` AND has `permission.user.group.set` granted directly — A can now move arbitrary users into arbitrary groups.

**Recommended fix** (combine with XC-1 + XC-4):
```python
# After resolving target_user_id, add:
operator_id = event.get_user_id()
if not is_owner(operator_id):
    # Restrict self-grant to a known-safe subset, OR forbid self-grant entirely.
    if user_id == operator_id:
        await bot.send(event, at + " " + reply_failure("添加", "不能为自己添加权限"))
        return
    # Optional but recommended: whitelist of "delegatable" permission keys.
    if permission not in DELEGATABLE_PERMISSIONS:
        await bot.send(event, at + " " + reply_failure("添加", "该权限不可委派"))
        return
    # Owner-protection on target — copied from ban_core.py:48
    if user_id in get_owner_ids():
        await bot.send(event, at + " " + reply_failure("添加", "不能修改 Owner 的权限"))
        return
```

### PMB-1.2 (🟠 high) — Lost update on `User.permissions` (CSV)
**File**: `permission_manager.py:103`

See XC-3. Concrete trigger: two admins both running `添加用户权限 X perm_a` / `添加用户权限 X perm_b` within the same SQLAlchemy session window → only one perm survives.

**Reproduction**:
1. User X starts with `User.permissions = ""`.
2. Admin 1 enters handler at T0; reads `permissions=""`.
3. Admin 2 enters handler at T0+10ms; reads `permissions=""`.
4. Admin 1 commits `permissions="perm_a"` at T0+50ms.
5. Admin 2 commits `permissions="perm_b"` at T0+60ms — `perm_a` is overwritten silently.

**Recommended fix**: wrap session in `BEGIN IMMEDIATE` for the SQLite engine, or add a normalized `user_permission` table with `INSERT OR IGNORE`. See XC-3 for full discussion.

### PMB-1.3 (🟠 high) — Permission key not validated against any whitelist
See XC-4. Allows pre-staking unknown keys for upcoming features.

### PMB-1.4 (🟡 medium) — Operator missing from audit log
**File**: `permission_manager.py:109`
Current: `logger.info(f"添加用户权限成功：user_id={user_id} permission={permission}")`
Fix: include `operator_id=` and `target_name=`. See XC-2.

### PMB-1.5 (🟢 low) — Permission name allows multi-token edge case via `args = parse_command_args_with_fallback`
**File**: `permission_manager.py:73-95`
`parse_command_args_with_fallback` splits on whitespace (`message_parser.py:71`), and the handler enforces `len(args) != 2` (line 74). A permission key with whitespace would be rejected. This is correct behavior, but if a future change relaxes the count check, multi-segment keys would silently be truncated. Document the invariant in a comment.

### PMB-1.6 (🟢 low) — `parse_command_args_with_fallback` is invoked twice
**File**: `permission_manager.py:73, 78`
Once for length check, once via `resolve_user_id_arg_with_fallback`. Each call hits `_segments_to_plain_text` + regex extraction. Minor double-parse; functionally correct. Cache the parsed args list and pass to both call sites for clarity.

---

## 2. `删除用户权限` → `handle_remove_user_perm` (line 132)

### PMB-2.1 (🟠 high) — Owner protection missing: any holder of `permission.user.remove` can strip permissions from an Owner row
**File**: `permission_manager.py:132-181`

**Current code** (line 158-167):
```python
session = get_session()
try:
    user = session.query(User).filter(User.user_id == user_id).first()
    if user is None: ...
    user.permissions = remove_permission(user.permissions, permission)
    target_name = str(user.name)
    session.commit()
```

**Impact**: owner is still effective via the `has_permission` short-circuit (`permissions.py:62-67`), so the operational damage is limited — but:
1. Webhooks / future code that reads `user.permissions` directly (e.g. UI rendering of "individually granted") will display the wrong state.
2. A future refactor that removes the owner short-circuit and relies on `User.permissions` becomes a critical bug overnight.
3. Audit trail is misleading — the owner appears to have lost a perm they never lost.
4. Pattern asymmetry with `ban_core.py:48` — security-conscious devs expect owner protection on every mutation; absence here is a silent violation of the project's own convention.

**Reproduction**:
1. Owner has `User.permissions = "extra.perm"` (granted via `添加用户权限`).
2. Hostile delegated admin sends `删除用户权限 <owner_qq> extra.perm`.
3. Owner's `User.permissions = ""`. WebUI now shows owner with no individually-granted perms; owner is unaware.

**Recommended fix**: same as PMB-1.1 — add owner-protection check before mutation.

### PMB-2.2 (🟠 high) — Lost update on `User.permissions`
Same shape as PMB-1.2 but inverted (concurrent revoke/grant). Race: admin removes perm `X` while admin grants perm `Y`; depending on commit order, one operation is silently undone. See XC-3.

### PMB-2.3 (🟡 medium) — Idempotency: removing a non-existent permission silently succeeds
**File**: `permission_manager.py:165, permissions.py:125-128`
`remove_permission` uses `set.discard()`, which is no-op on missing keys. The success message `✅ 删除成功` displays even when the perm wasn't there — operator cannot tell whether they actually changed anything. Compare with PMB-3.4 below for `修改用户身份组` which has the same issue.

**Recommended fix**: detect no-op (`old_set == new_set`) and reply `ℹ️ 该用户未持有此权限`. Better, `remove_permission` itself can return a bool.

### PMB-2.4 (🟡 medium) — Operator missing from audit log
Same as PMB-1.4. `logger.info(f"删除用户权限成功：user_id={user_id} permission={permission}")` lacks `operator_id`.

### PMB-2.5 (🟢 low) — No "the perm came from a group, not from you" warning
**File**: `permission_manager.py:165`
If the perm is granted via group inheritance, `remove_permission` on `User.permissions` does nothing useful — the user still has it. The handler should detect this (`get_effective_permissions(user_id)` still contains the perm) and warn.

---

## 3. `修改用户身份组` → `handle_set_user_group` (line 194) — **most security-critical handler in the file**

### PMB-3.1 (🔴 critical) — No group-name allowlist + no operator-vs-target group hierarchy check; effectively unbounded escalation if the group has high-priv perms
**File**: `permission_manager.py:184-250`

**Current code** (line 219-234):
```python
group_name = args[1]
session = get_session()
try:
    user = session.query(User).filter(User.user_id == target_user_id).first()
    if user is None: ...
    group = session.query(Group).filter(Group.name == group_name).first()
    if group is None:
        await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
        return
    user.group = group_name
    target_name = str(user.name)
    session.commit()
```

**Impact**: any holder of `permission.user.group.set` can move any user into any group. The bot's group system is hierarchical (`Group.inherits` → `_get_group_permissions` recursion at `permissions.py:43-59`) and has NO concept of "rank" or "operator may only assign groups whose effective permissions are a subset of operator's effective permissions". Concretely:

1. If a group named `super_admin` exists with `permission.user.group.set` + `permission.user.add` + `*`, any operator with `permission.user.group.set` can put themselves there: `修改用户身份组 <self> super_admin` → operator now has all perms.
2. Even without an explicit `super_admin` group, any group that inherits indirectly from one with broad perms will work; the recursive resolver in `_get_group_permissions` doesn't bound depth or validate against operator's own grants.
3. Operator can move OWNER OUT of whatever group owner is in (XC-1) — owner still works because of `has_permission`'s short-circuit, BUT a future contributor refactoring that short-circuit is one PR away from a production-breaking bug.

**Reproduction**:
1. Group `power_users` exists with permissions `user.whitelist.sync, server.send, server.list, ban.add, permission.user.add` (a plausible "trusted helpers" group an admin might build).
2. Operator A holds `permission.user.group.set` (e.g. delegated to manage greeter rotation).
3. A sends `修改用户身份组 <A's QQ> power_users` → A now holds all the above perms transitively.
4. With XC-4 unfixed, A continues escalating via `添加用户权限`.

**Recommended fix**: **the most important fix in the audit**. Implement BEFORE commit at line 232:

```python
operator_id = event.get_user_id()

# (a) Owner protection on TARGET — cannot move owner out of their group
if target_user_id in get_owner_ids():
    await bot.send(event, at + " " + reply_failure("修改", "不能修改 Owner 的身份组"))
    logger.warning(
        f"修改用户身份组被 owner 保护拒绝：operator_id={operator_id} "
        f"target_user_id={target_user_id} attempted_group={group_name}"
    )
    return

# (b) Hierarchy guard — operator may only assign groups whose effective perms
#     are a SUBSET of operator's own effective perms (excluding `*`).
if not is_owner(operator_id):
    operator_perms = get_effective_permissions(operator_id)
    target_group_perms = _get_group_permissions(session, group_name, set())
    forbidden = target_group_perms - operator_perms
    if forbidden:
        await bot.send(event, at + " " + reply_failure("修改", "目标身份组包含您不持有的权限"))
        logger.warning(
            f"修改用户身份组被权限上限拒绝：operator_id={operator_id} "
            f"target_user_id={target_user_id} attempted_group={group_name} "
            f"forbidden_perms={sorted(forbidden)}"
        )
        return

# (c) Capture before_group for audit
before_group = str(user.group)
user.group = group_name
session.commit()

logger.info(
    f"修改用户身份组成功：operator_id={operator_id} target_user_id={target_user_id} "
    f"target_name={user.name} before_group={before_group} after_group={group_name}"
)
```

### PMB-3.2 (🟠 high) — Lost update on `User.group` racing with `添加用户权限` on the same row
**File**: `permission_manager.py:232`

Same lost-update class as PMB-1.2/PMB-2.2. Concretely: admin A runs `修改用户身份组 X g1` while admin B runs `添加用户权限 X p1`. Both load the same User row, both modify different attributes, both commit; SQLAlchemy's UPDATE writes ALL columns of the dirty entity, so whichever commits second overwrites the other's column. Effective state is non-deterministic.

**Recommended fix**: same as XC-3 — `BEGIN IMMEDIATE` or row-level conditional UPDATE. SQLAlchemy's `session.bulk_update_mappings` or explicit `update().where().values(group=...)` writes only the named column and avoids cross-column overwrite, which is the minimal fix.

### PMB-3.3 (🟠 high) — Audit log missing `operator_id` AND `before_group`
**File**: `permission_manager.py:238-240`

Current:
```python
logger.info(
    f"修改用户身份组成功：user_id={target_user_id} group={group_name}"
)
```

For the most security-critical command in the system, this line is dangerously underspecified. Forensic reconstruction of a privilege-escalation incident is impossible without `operator_id`, `before_group`, `after_group`, `target_name`. Ban handler (`ban.py:107`) sets the standard.

**Recommended fix**: include `operator_id`, `target_user_id`, `target_name`, `before_group`, `after_group`. Capture `before_group = str(user.group)` BEFORE the `user.group = group_name` assignment.

### PMB-3.4 (🟡 medium) — No-op assignment silently shows success
**File**: `permission_manager.py:232-249`
If `user.group == group_name` already, the handler still commits + replies "✅ 修改成功". Operator cannot distinguish a real change from a no-op. Add `if user.group == group_name: reply "ℹ️ 该用户已在此身份组"`.

### PMB-3.5 (🟡 medium) — Group existence check happens via `Group.name == group_name` (case-sensitive); no normalization
**File**: `permission_manager.py:227`
Group `Default` is treated as different from `default`. WebUI may have established naming conventions (`db.py:419-441` seeds `guest` and `default` lowercase). If an admin types `修改用户身份组 X Default`, they get `身份组不存在`. Document or normalize.

### PMB-3.6 (🟢 low) — No protection against demoting the LAST holder of high-priv group
If a group `co_owner` has critical permissions and only one user is in it, an operator can move that user out, leaving zero holders. Owners still work, but business invariants may break. Out of scope for a base audit.

---

## 4. `管理员列表` → `handle_admin_list` (line 272)

### PMB-4.1 (🟠 high) — N+1 OneBot API call: one `get_stranger_info` per owner, awaited serially
**File**: `permission_manager.py:286-289`, `_fetch_nickname_via_bot` at line 52

```python
for qq in owner_ids:
    nickname = await _fetch_nickname_via_bot(bot, qq)
    admins.append({"user_id": qq, "nickname": nickname})
```

For 10 owners with average 200ms RTT to NapCat, this is ~2s blocking. If NapCat is slow or unreachable, the handler hangs for `len(owner_ids)` × per-call timeout. The `_fetch_nickname_via_bot` helper has NO timeout argument and relies on `bot.call_api` defaults.

Compared to other audits in this category, this is the same N+1 shape as PQA's player-info enrichment.

**Recommended fix**: parallelize with `asyncio.gather(*[_fetch_nickname_via_bot(bot, qq) for qq in owner_ids])`. Bound each call with `asyncio.wait_for(..., timeout=5.0)`. Optionally cache nickname for `N` minutes since owner list changes rarely.

### PMB-4.2 (🟡 medium) — `screenshot_path.read_bytes()` has no size guard before base64-encoding
**File**: `permission_manager.py:303-305`

```python
raw = screenshot_path.read_bytes()
image_uri = f"base64://{base64.b64encode(raw).decode('ascii')}"
```

If a future template change (e.g. malicious sprite, infinite-scroll loop) produces a multi-MB PNG, this loads the entire file into memory + does a 4/3 base64 expansion. `large_image.MAX_BASE64_BYTES = 200 * 1024 * 1024` is the project standard. Even though the current admin-list page is small, any other screenshot-based handler in the codebase will eventually want this pattern; consolidate now.

**Recommended fix**: add `if screenshot_path.stat().st_size > MAX_BASE64_BYTES: raise/reply` before `read_bytes`. Or move the read+encode behind a shared helper in `large_image.py`.

### PMB-4.3 (🟡 medium) — Read errors are surfaced as a vague "读取截图文件失败"
**File**: `permission_manager.py:306-308`
Catches `OSError` only; PIL/codec failures or `MemoryError` (large file edge case) propagate as 500-level. Acceptable but document.

### PMB-4.4 (🟢 low) — `bot.adapter.get_name()` branch implicitly assumes single adapter type per process
**File**: `permission_manager.py:302-311`
The non-OBV11 branch sends a literal text `✅ 截图成功，文件：{screenshot_path}` — exposing a `/tmp` filesystem path to the chat surface. For an admin-only command this is acceptable, but if `permission.admin.list` ever leaks (cf. XC-4 / XC-5), the path is leaked too.

### PMB-4.5 (🟢 low) — Per-owner nickname is logged at INFO with `nickname={nickname!r}` (`line 289`)
**File**: `permission_manager.py:289`
PII (admin nicknames) lands in app log every invocation. For an admin-only read query this is fine, but consider DEBUG level in production.

---

## 5. `同步访客权限` (handle_sync_guest_perms + handle_sync_guest_perms_confirm — lines 318, 345, 389)

### PMB-5.1 (🔴 critical IF the permission is delegated; otherwise 🟠 high) — Holder of `permission.group.guest.sync` can grant ANYTHING to `guest`, broadening every guest-callable surface in one shot
**File**: `permission_manager.py:318-459`

The mechanism is by-design constrained — it only adds keys from `DEFAULT_GUEST_PERMISSIONS` (`db.py:34-95`) to the live `guest` row. **However**, the security boundary depends on `DEFAULT_GUEST_PERMISSIONS` being a hard-coded constant in the trusted source tree. There are two related risks:

1. **Pre-existing perms are NOT removed.** Comment at line 340 says "仅新增、不删除已有权限". If an attacker (or an earlier compromise) added `permission.user.group.set` to `guest` via WebUI or `添加身份组权限`, sync does NOT clean it up. Sync is "additive idempotent only", which is correct for that intent but means it cannot serve as a "reset to safe baseline" recovery tool.
2. **Confirm message is parsed by literal substring `确认`** (line 314, 402: `text != _SYNC_CONFIRM_TOKEN`). The caller-id verification at line 396-398 is solid: `if event.get_user_id() != caller_user_id: matcher.reject()`. NoneBot2's session id scoping is independently verified. Good defense-in-depth; correct pattern.

**Impact (low residual risk)**: assuming `DEFAULT_GUEST_PERMISSIONS` is correctly authored and reviewed at PR time (it is — it's a `frozenset` literal in `db.py:34`), the runtime risk is bounded. The sync flow itself is safe-by-design, but the ASYMMETRY (additive only, no remove path) means a hostile/compromised group state cannot be normalized via this command — that has to be done manually via `删除身份组权限`.

**Recommended fix** (medium priority):
1. Add a sibling command `重置访客权限` that does `replace_with(DEFAULT_GUEST_PERMISSIONS)` after a 二次确认 with explicit "extra perms will be REMOVED" warning listing them.
2. Audit log should record the OPERATOR for both initial preview and confirm:
   ```
   logger.info(f"同步访客权限发起：operator_id={...} missing={...}")
   logger.info(f"同步访客权限确认：operator_id={...} added={actually_added}")
   ```
3. Document that sync is intentionally additive and is NOT a recovery-from-compromise tool.

### PMB-5.2 (🟡 medium) — Operator missing from audit log
**File**: `permission_manager.py:447-450`

Current:
```python
logger.info(
    f"同步访客权限成功：group={_SYNC_GROUP_NAME} added={actually_added} "
    f"target_count={target_count}"
)
```
Same gap as XC-2. Add `operator_id={caller_user_id}`.

### PMB-5.3 (🟡 medium) — `_diff_guest_default_permissions` opens its own session, separate from the confirm-handler session — small TOCTOU between preview and confirm
**File**: `permission_manager.py:318-332` vs `417-432`

The preview reads in one session and closes (line 324-329). The confirm path reads again in a NEW session (line 417-419) and re-diffs against the live row (line 425-428). This is ALMOST TOCTOU-safe — the `actually_added = sorted(set(missing) - current)` re-compares against the live state at confirm time, intentionally. Comment at line 426-427 acknowledges this. Correct design.

The only residual oddity: if WebUI ADDS one of the missing keys between preview and confirm, the preview's "缺失：N 个" is stale by the time confirm runs. The success line accurately reports what was actually added (line 451-456), so user-facing accuracy is preserved. Document this as intentional.

### PMB-5.4 (🟡 medium) — No upper bound on `matcher.state` payload size; if `missing` were enormous (e.g. via future code change that diffs against a different baseline), state could balloon
**File**: `permission_manager.py:366-369`
`matcher.state["sync_missing"] = missing` — currently bounded by `len(DEFAULT_GUEST_PERMISSIONS)` ~50 strings. Defensive note only; no current exploit.

### PMB-5.5 (🟢 low) — `confirm_reply.extract_plain_text().strip()` does not normalize Unicode; user typing 中文标点 around `确认` is not accepted
**File**: `permission_manager.py:401-405`
e.g. ` 确认 ` works, but `「确认」` does not (`.strip()` doesn't strip Chinese brackets). The hint message at line 383 is unambiguous, so this is just a UX polish item.

### PMB-5.6 (🟢 low) — Non-confirm reply triggers `matcher.finish` with "已取消" — silent fallthrough on unrelated messages from caller
**File**: `permission_manager.py:402-405`
Any message from caller in the same session that doesn't equal `确认` cancels the flow. If caller types 何 then immediately `确认`, the first message cancels. Acceptable; document.

### PMB-5.7 (🟢 low) — `guest is None` check at line 419-423 races with `身份组删除`
**File**: `permission_manager.py:419-423`
If admin B runs `删除身份组 guest` between preview and confirm, the confirm replies "guest 身份组不存在". `group_manager.py:119` actually blocks deletion of `guest`/`default` (`if name in {"guest", "default"}: 系统内置身份组不可删除`), so this is defensive-only. Verified safe.

---

## Severity rollup

| ID | Severity | Title |
|---|---|---|
| XC-1 | 🔴 critical | No owner-protection layer for any 权限管理 mutation handler |
| PMB-1.1 | 🔴 critical | Privilege ratchet via `添加用户权限` self-grant |
| PMB-3.1 | 🔴 critical | `修改用户身份组` lacks owner-protection + hierarchy guard |
| PMB-5.1 | 🔴 critical (conditional) | `同步访客权限` is additive-only; cannot recover from compromise |
| XC-2 | 🟠 high | Operator missing from every audit log line |
| XC-3 | 🟠 high | Lost update on `User.permissions` / `User.group` (CSV read-modify-write) |
| PMB-1.2 | 🟠 high | Lost update on `User.permissions` (concurrent grants) |
| PMB-1.3 | 🟠 high | Permission key not validated against any whitelist |
| PMB-2.1 | 🟠 high | Owner protection missing for `删除用户权限` |
| PMB-2.2 | 🟠 high | Lost update on revoke racing with grant |
| PMB-3.2 | 🟠 high | Lost update on `User.group` racing with `添加用户权限` |
| PMB-3.3 | 🟠 high | Audit log missing `operator_id` AND `before_group` |
| PMB-4.1 | 🟠 high | N+1 OneBot API call (serial nickname fetch) |
| XC-4 | 🟡 medium | No permission-key whitelist; arbitrary keys silently accepted |
| PMB-1.4 | 🟡 medium | Operator missing from `添加用户权限` audit log |
| PMB-2.3 | 🟡 medium | `删除用户权限` succeeds silently on missing perm |
| PMB-2.4 | 🟡 medium | `删除用户权限` audit log missing operator |
| PMB-3.4 | 🟡 medium | `修改用户身份组` no-op silently shows success |
| PMB-3.5 | 🟡 medium | Group name lookup is case-sensitive; no normalization |
| PMB-4.2 | 🟡 medium | `screenshot.read_bytes()` no size guard |
| PMB-4.3 | 🟡 medium | Vague "读取截图文件失败" error |
| PMB-5.2 | 🟡 medium | `同步访客权限` audit log missing operator |
| PMB-5.3 | 🟡 medium | TOCTOU (designed-safe) between preview and confirm sessions |
| PMB-5.4 | 🟡 medium | No upper bound on `matcher.state["sync_missing"]` |
| XC-5 | ℹ️ info | `管理员列表` follows screenshot-OOM pattern but not currently guest-callable |
| PMB-1.5 | 🟢 low | Multi-token permission key edge case |
| PMB-1.6 | 🟢 low | `parse_command_args_with_fallback` invoked twice |
| PMB-2.5 | 🟢 low | No "perm came from group" warning on `删除用户权限` |
| PMB-3.6 | 🟢 low | No protection against demoting last holder of high-priv group |
| PMB-4.4 | 🟢 low | Non-OBV11 branch leaks `/tmp` path |
| PMB-4.5 | 🟢 low | Owner nickname logged at INFO every invocation |
| PMB-5.5 | 🟢 low | Confirm token doesn't normalize Chinese punctuation |
| PMB-5.6 | 🟢 low | Non-confirm reply silently cancels |
| PMB-5.7 | 🟢 low | `guest is None` race (defensive) |

---

## Recommended fix sequence (priority order)

1. **XC-1 + PMB-3.1** — extract `is_owner(user_id)` helper in `permissions.py`, apply owner-protection check before commit in all three mutation handlers. Add `operator_id` and `target_name` (and `before_group` for set_user_group) to all audit log lines (XC-2 / PMB-3.3).
2. **PMB-3.1 hierarchy guard** — operator may only assign groups whose effective perms ⊆ operator's own. Single check, biggest blast-radius reduction.
3. **PMB-1.1 + XC-4** — block self-grant for non-owner; add permission-key whitelist sourced from `command_config._registry`.
4. **XC-3 / PMB-1.2 / PMB-2.2 / PMB-3.2** — switch all `User.*` mutations to `update().where().values(only_the_changed_column=...)` form to eliminate cross-column overwrite from ORM dirty-set semantics, OR adopt `BEGIN IMMEDIATE` on the SQLite engine.
5. **PMB-4.1** — parallelize nickname fetch with `asyncio.gather` + per-call `wait_for`.
6. **PMB-5.1** — add `重置访客权限` as the recovery counterpart to `同步访客权限`; document that sync is intentionally additive only.
7. Remaining mediums and lows — UX polish, defensive guards.
