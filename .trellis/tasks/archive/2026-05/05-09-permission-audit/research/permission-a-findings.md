# Research: Permission Management Audit (Batch A — group_manager.py, 7 commands)

- **Query**: Audit `nextbot/plugins/group_manager.py` (327 lines, 7 commands in `权限管理` category) for privilege escalation, race conditions, validation, OOM/perf, data consistency, observability, and owner/admin protection.
- **Scope**: internal (with cross-references to `permissions.py`, `db.py`, `permission_manager.py`, `access_control.py`)
- **Date**: 2026-05-09

## Threat-model preface

Before scoring, three project-wide facts that anchor every finding below:

1. **There is no DB-side `owner` group.** "Owner" is a hardcoded short-circuit in `nextbot/permissions.py:62-67`: `has_permission()` returns `True` for any `user_id` listed in `.env`'s `owner_id`, regardless of group membership. This means:
   - Owner status cannot be assigned via `修改用户身份组`. Good.
   - But it also means there are zero DB checks against assigning a non-owner into _any_ group named "owner" — the name is ordinary, not reserved.
2. **There is no DB-side `admin` group either.** `ensure_default_groups()` (`db.py:419-441`) seeds only `guest` and `default`. Anything called `admin` would be a user-created group with whatever permissions the creator gave it.
3. **There is no permission registry / catalogue.** Permissions are free-form strings. `add_permission()` (`permissions.py:119-122`) accepts any string — typos like `economy.singin` (instead of `signin`) silently no-op at grant time and silently no-op at check time. There is also no list of "dangerous" permission keys (`group.delete`, `permission.user.group.set`, …) the system could refuse to grant to a non-trusted group.

These shape the most-severe findings (PMA-2.1 reserved-name bypass, PMA-6.1 elevation chain, PMA-CC-2 typo no-op, PMA-CC-3 no audit log).

## Summary table

| ID | Severity | Area | Title |
|---|---|---|---|
| PMA-2.1 | 🔴 critical | privilege escalation / owner protection | `添加身份组` accepts reserved-looking names like `owner` / `admin` / `guest` / `default`; combined with PMA-6.1 / cross-file `修改用户身份组` lets anyone with `group.add` + `permission.user.group.set` mint and self-assign an admin-equivalent group |
| PMA-6.1 | 🔴 critical | privilege escalation | `添加身份组权限` has no allow-list / blocklist on the permission string — anyone with `group.permission.add` can grant `*`, `permission.*`, `group.delete`, etc. to a low-privilege group (typically guest's parent chain) and escalate every user in that group |
| PMA-3.1 | 🔴 critical | data integrity / cascade | `删除身份组` reassigns all affected users to `guest` rather than `default` — silently strips them of every group-specific permission they had via inheritance, and may also drop them _below_ the project's intended baseline (e.g. user previously in a `default→guest`-inheriting group). No warning, no preview, no rollback. |
| PMA-3.2 | 🟠 high | concurrency / cascade | `删除身份组` does delete + bulk-update + read-modify-write of every other group's `inherits` in one session; on SQLite without `BEGIN IMMEDIATE`, two concurrent `删除身份组` calls (or `删除身份组` + `继承身份组`) interleave and either leave dangling `inherits` references or lose updates |
| PMA-6.2 | 🟠 high | concurrency / lost-update | `添加身份组权限` does read-modify-write on the CSV `Group.permissions` column with no row lock and no conditional UPDATE; two concurrent `添加身份组权限 admin a` + `添加身份组权限 admin b` can lose one of the two perms |
| PMA-7.1 | 🟠 high | concurrency / lost-update | `删除身份组权限` has the same read-modify-write race as PMA-6.2: concurrent `删除` + `添加` (or two concurrent `删除`s) can resurrect a removed perm or skip a deletion |
| PMA-4.1 | 🟠 high | privilege escalation / DoS | `继承身份组` has no cycle detection. `继承身份组 A B` then `继承身份组 B A` builds a cycle. `_get_group_permissions()` in `permissions.py:43-59` does carry a `visited` set so it does NOT recurse infinitely (good), but every `has_permission` call now traverses both groups every time, and a deeper cycle (A→B→C→A) hides from operators because the loop is invisible in `身份组列表`. Combined with PMA-6.1, an attacker can also create `guest → attacker_group` and grant `guest` itself elevated perms via the parent. |
| PMA-4.2 | 🟠 high | concurrency / lost-update | `继承身份组` does read-modify-write on `Group.inherits`; concurrent `继承身份组 A X` + `继承身份组 A Y` can lose one parent (same bug class as PMA-6.2/7.1 but on a different column) |
| PMA-3.3 | 🟠 high | observability | `删除身份组` does not log _what_ permissions/inherits the deleted group held nor _which users_ were re-grouped; impossible to forensically reverse a malicious delete |
| PMA-CC-2 | 🟠 high | validation / silent failure | No permission-key registry: `添加身份组权限 admin permssion.user.add` (typo) silently succeeds, group looks "configured", but the typo never matches anything in `_match_permission` — admin actions silently fail at runtime, looking like "the bug is in the command" |
| PMA-CC-3 | 🟠 high | observability / forensics | None of the 7 commands log a structured before/after permission snapshot keyed by actor `user_id` + target group + timestamp; permission changes are the single most-important audit surface in the bot, and right now you cannot answer "who gave guest the `*` permission yesterday?" |
| PMA-1.1 | 🟡 medium | OOM / output size | `身份组列表` builds the entire response as a single string with no row cap and no truncation — `permissions` and `inherits` CSVs can be arbitrarily long; with hundreds of groups + hundreds of perms each, the OneBot V11 message exceeds the 4-5k char practical limit (segments dropped or send rejected). Mirrors prior PQB-1.1 / SB-2.2 size patterns even though no screenshot is rendered. |
| PMA-2.2 | 🟡 medium | validation / injection | `添加身份组` permits names containing `\n`, `,`, `:`, leading/trailing whitespace, the empty string after trim is technically blocked by `parse_command_args_with_fallback` token-splitting but a name like `admin ` (NBSP) passes; CSV-stored `inherits` later treats `,` inside a name as a separator → orphaned inheritance |
| PMA-3.4 | 🟡 medium | data integrity | `删除身份组` removes references from `Group.inherits` of other groups but does NOT scrub the permission caches / forces no re-evaluation; if any future caching layer is introduced (none today, but mentioned in spec dirs), this becomes a stale-permission bug |
| PMA-4.3 | 🟡 medium | validation | `继承身份组` does not check for transitive cycles (A→B already, then 继承身份组 C A then 继承身份组 B C builds A→B→C→A); only checks direct self-inherit (`child == parent`, line 168) |
| PMA-5.1 | 🟡 medium | data consistency | `取消继承身份组` overwrites `inherits` to empty string even if `name` is `default` (which is supposed to inherit `guest`) — silently breaks the documented default group semantics |
| PMA-6.3 | 🟡 medium | observability | `添加身份组权限` log includes `name`, `permission`, but not the actor `user_id` doing the grant nor the resulting full permission set — log line is "what changed" not "who changed it" |
| PMA-7.2 | 🟡 medium | observability | `删除身份组权限` has the same gap as PMA-6.3 |
| PMA-1.2 | 🟢 low | minor leakage | `身份组列表` sends the raw `permissions` CSV; in IM groups where junior members might be present (depends on deployment), this exposes capability surface to non-admins. Today `group.list` requires its own permission, so this is gated, but worth noting if `group.list` is ever added to `default`/`guest`. |
| PMA-2.3 | 🟢 low | UX / IntegrityError | `添加身份组` does a SELECT-then-INSERT; under concurrent submission of the same name the second commit raises `IntegrityError` (uncaught — bubbles to NoneBot's default error handler and the user sees a generic 500/error message, not `身份组已存在`) |
| PMA-3.5 | 🟢 low | logging | `删除身份组` log says `删除身份组成功：name={name}` but doesn't note how many users were re-grouped or how many other groups had their `inherits` modified — reduces post-incident understanding |
| PMA-3.6 | 🟢 low | UX | `删除身份组` does not require confirmation. The `同步访客权限` command in `permission_manager.py` _does_ require `回复「确认」`. Asymmetric risk profile: deleting an inheritance-root group cascades to many users, deserving the same confirmation gate. |
| PMA-5.2 | 🟢 low | UX | `取消继承身份组` permits running on a group that already has empty `inherits` and silently "succeeds" — no `已无继承可清空` info reply |
| PMA-CC-1 | 🟢 low | dead code / consistency | `delete_matcher`, `inherit_matcher`, etc. are top-level module attributes (lines 19-25) wired up before any function definition; code is correct but a stylistic inversion compared to other plugin files where `on_command(...)` lives directly above its handler |
| PMA-CC-4 | ℹ️ info | cross-cutting | `User.group` is a free-string FK with no DB-level FOREIGN KEY constraint to `Group.name`; even with PMA-3.1 fixed, an admin can `修改用户身份组 alice ghost_group` (with `ghost_group` not in DB) and `permission_manager.py:227-229` does check existence at write time, but `User.group` can still drift to a non-existent group if a group is deleted via the WebUI directly without going through `删除身份组` |
| PMA-CC-5 | ℹ️ info | cross-cutting | The `category="权限管理"` decoration ties every command to one permission family but no command checks whether the actor's permission is _at least as high as_ what they're modifying — i.e. an actor with only `group.permission.add` can grant `permission.user.add` to a group that doesn't currently have it, then add themselves to that group, forming a transitive escalation |

---

## Per-command findings

### 1. `身份组列表` → `handle_list_groups` (line 36-62)

#### PMA-1.1 🟡 Medium — Unbounded message size; no row/perm/inherits truncation

- **File:line**: `nextbot/plugins/group_manager.py:43-62`
- **Current code**:

```python
groups = session.query(Group).order_by(Group.name.asc()).all()
...
lines: list[str] = []
for group in groups:
    lines.append(group.name)
    lines.append(f"权限：{group.permissions or '无'}")
    lines.append(f"继承：{group.inherits or '无'}")
    lines.append("")

message = "👥 身份组列表\n" + "\n".join(lines).rstrip()
await bot.send(event, message)
```

- **Impact**: For each `Group` row, the handler appends the **entire** `permissions` CSV (which today for `guest` is already 55 keys ≈ 1 KB after `ensure_default_groups()` seeding) plus `inherits` CSV. With N groups each carrying ≈ M permissions, message size grows O(N·M). NoneBot OneBot V11 adapters typically truncate or reject messages over ≈ 4-5 KB; long messages also strain the QQ client renderer. No pagination, no `limit`, no per-row truncation. Same shape as PQB-1.1 / SB-2.2 OOM concerns from prior audits, just text-rendered instead of screenshot-rendered.
- **Reproduction**: Seed 30 user groups, each with the full 55-key default perm CSV (e.g. via `添加身份组权限 X economy.* … etc.`). Run `身份组列表`. Inspect the assembled message length — expect > 8 KB. Some adapters silently send only the first ≈ 4 KB.
- **Recommended fix**: Either (a) cap visible permissions per group at e.g. 10 with `... +N more` suffix, (b) page the response (`身份组列表 [page]`), or (c) defer the heavy detail to `身份组详情 <name>` and have the listing show only `name` + counts (`权限数: K, 继承: M`). Option (c) also addresses PMA-1.2 (gated leakage).

#### PMA-1.2 🟢 Low — Raw permission CSV exposure

- **File:line**: `nextbot/plugins/group_manager.py:55-58`
- **Impact**: Listing dumps the literal permission CSV. Today `group.list` is permission-gated (`@require_permission("group.list")`, line 35), but if the perm is ever added to `default`/`guest` (or granted broadly), low-privilege members learn the exact capability surface (`security.login.confirm`, `permission.user.add`, etc.) — useful reconnaissance for crafting subsequent escalation attempts.
- **Recommended fix**: Same as PMA-1.1 (c): default to a count-only listing; require an explicit `身份组详情` command (with stricter permission) to see the full CSV.

#### PMA-1.3 ℹ️ Info — N+1 not present

The audit prompt asks about a per-group `SELECT COUNT(*) FROM user WHERE group=X` N+1. The current handler does NOT compute user counts — it only renders `permissions` + `inherits`. So no N+1 today. But the moment a future change adds a `用户数：K` column (likely, since it's the obvious next request), the naive implementation would be `for g in groups: session.query(User).filter(User.group==g.name).count()` → N+1. Worth pre-emptively pointing future work at `session.query(User.group, func.count()).group_by(User.group).all()` to build a dict in one query.

---

### 2. `添加身份组` → `handle_add_group` (line 75-97)

#### PMA-2.1 🔴 Critical — No reserved-name protection; mints `owner` / `admin` / `guest` / `default` clones

- **File:line**: `nextbot/plugins/group_manager.py:83-92`
- **Current code**:

```python
name = args[0]
session = get_session()
try:
    exists = session.query(Group).filter(Group.name == name).first()
    if exists is not None:
        await bot.send(event, at + " " + reply_failure("添加", "身份组已存在"))
        return

    session.add(Group(name=name, permissions="", inherits=""))
    session.commit()
```

- **Impact**: There is no allow-list, no blocklist, no character-class filter, no length limit. A user with `group.add` permission can create a group named `owner` or `admin`. By itself that is harmless (no code path treats `owner`/`admin` group names specially — see threat-model preface). **But** combined with cross-file `修改用户身份组` (`permission_manager.py:184-250`) and PMA-6.1 (no permission allow-list), the attack chain is:
  1. Attacker has `group.add` + `group.permission.add` + `permission.user.group.set` (any of which a careless admin might have packaged into a "moderator" role).
  2. Attacker runs `添加身份组 admin_role` → succeeds.
  3. Attacker runs `添加身份组权限 admin_role *` (if the perm-matcher's wildcard support `granted.endswith(".*")` in `permissions.py:19-23` is permissive enough — it matches any prefix-`.*` string) → grants near-admin perms.
  4. Attacker runs `修改用户身份组 attacker_self admin_role` → self-elevates.
- **Reproduction**:
  ```
  添加身份组 owner          → "✅ 添加成功" (no warning that name is sensitive)
  添加身份组 admin          → "✅ 添加成功"
  添加身份组 guest          → "❌ 添加失败，身份组已存在" (only because guest pre-exists; otherwise would also succeed)
  添加身份组 ""             → arg parser drops empty token, so this is implicitly blocked
  添加身份组 "  admin  "    → token.strip() not applied here; depending on parser, may store with whitespace
  ```
- **Recommended fix**: Introduce `RESERVED_GROUP_NAMES = frozenset({"owner", "admin", "root", "system", "superuser"})` in `db.py` next to `DEFAULT_GUEST_PERMISSIONS`. In `handle_add_group`, after parsing the name, reject if `name.lower() in RESERVED_GROUP_NAMES` with `reply_failure("添加", "身份组名称为系统保留字")`. Also enforce a positive name regex (`re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", name)`) to address PMA-2.2.

#### PMA-2.2 🟡 Medium — Name validation absent

- **File:line**: `nextbot/plugins/group_manager.py:83`
- **Current code**:

```python
name = args[0]
```

- **Impact**: `args[0]` comes from `parse_command_args_with_fallback` which splits on whitespace, so `name` is one whitespace-separated token. But:
  - Names with `,` are accepted (`添加身份组 a,b`) — and the CSV-stored `inherits` of any group later set to `继承身份组 X "a,b"` will be split as two parents (`a` and `b`) by `split_csv_values()` in `permissions.py:11-12`.
  - Names with `:` / `.` / `/` / unicode non-printables (e.g. NBSP ` `) are accepted.
  - No length cap → DB column is `String` (SQLite has no length limit, but the WebUI renderer / OneBot message could choke).
  - Empty / whitespace-only is blocked by the parser dropping the token, but if the token is ` ` (NBSP) the parser does NOT strip it and you can store an "invisible name" group.
- **Reproduction**: `添加身份组 a,b` → row inserted with `name='a,b'`. Then `继承身份组 some_other a,b` stores `inherits='a,b'`. Then `_get_group_permissions(session, "some_other", set())` runs `split_csv_values("a,b") → ["a", "b"]` and looks up groups `a` and `b` (neither exists) → silent no-op; the inheritance is broken.
- **Recommended fix**: Validate against `re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", name)`. Reject otherwise. Same regex pattern is used in `_validate_user_name` for users (`user_manager.py:53-63` — see PQA-2.2 follow-up).

#### PMA-2.3 🟢 Low — IntegrityError on concurrent add not caught

- **File:line**: `nextbot/plugins/group_manager.py:86-92`
- **Current code**:

```python
exists = session.query(Group).filter(Group.name == name).first()
if exists is not None:
    await bot.send(event, at + " " + reply_failure("添加", "身份组已存在"))
    return

session.add(Group(name=name, permissions="", inherits=""))
session.commit()
```

- **Impact**: Two concurrent `添加身份组 X` requests race between the SELECT and the INSERT/commit. Both pass the existence check, both attempt to commit; the second hits `Group.name` PRIMARY KEY constraint and SQLAlchemy raises `IntegrityError`. The handler does not catch it → bubbles to NoneBot default → user sees a generic error or no reply at all (depending on framework config).
- **Reproduction**: Open two QQ sessions, both with `group.add`, send `添加身份组 X` simultaneously.
- **Recommended fix**: Wrap the commit in `try/except IntegrityError`, rollback, and reply `reply_failure("添加", "身份组已存在")`. This matches the SAGE pattern from prior audits (e.g. red-packet name uniqueness handling).

---

### 3. `删除身份组` → `handle_delete_group` (line 110-146)

#### PMA-3.1 🔴 Critical — Cascade reassigns to `guest` rather than `default`, silently strips perms

- **File:line**: `nextbot/plugins/group_manager.py:130-141`
- **Current code**:

```python
session.delete(group)
session.flush()

session.query(User).filter(User.group == name).update(
    {User.group: "guest"}, synchronize_session=False
)
all_groups = session.query(Group).all()
for g in all_groups:
    parents = {p.strip() for p in g.inherits.split(",") if p.strip()}
    if name in parents:
        g.inherits = remove_inherit(g.inherits, name)
session.commit()
```

- **Impact**: Two issues compound here.
  - **Wrong reassignment target.** `ensure_default_groups()` (`db.py:419-441`) seeds two groups: `guest` (the bare-minimum perm set) and `default` (which inherits `guest` and is intended as the post-registration baseline — see `User.group` default `"guest"` at `db.py:131` is itself arguably a separate bug, but `default` clearly exists for a reason). Reassigning deleted-group users to `guest` instead of `default` strips them of any future `default`-only perms and breaks the implied hierarchy.
  - **No notice / no preview.** The actor sees `✅ 删除成功` with zero info about how many users were just demoted. `logger.info` records only `name=name`. Users themselves get no notification.
  - **Lost inheritance impact is invisible.** If group `mod` inherited from `helper`, and the actor deletes `helper`, then `mod`'s `inherits` is silently scrubbed and every user in `mod` instantly loses every perm `helper` provided — without any audit trail.
- **Reproduction**:
  1. `添加身份组 vip`, `添加身份组权限 vip economy.bonus`.
  2. `修改用户身份组 alice vip` → alice is in vip, has `economy.bonus`.
  3. `删除身份组 vip` → alice is now in `guest` (not `default`), and `economy.bonus` is gone with no record.
- **Recommended fix**:
  - Pick a single, intentional fallback group (most likely `default`, not `guest`) and document it. Make it configurable (`FALLBACK_GROUP = "default"`).
  - Before deletion, count affected users + affected child-groups; render preview, require `「确认」` reply (same pattern as `同步访客权限` in `permission_manager.py:335-385`).
  - On commit, log `actor=<owner_id> action=group.delete name=<deleted> permissions=<csv> inherits=<csv> reassigned_users=<count> updated_child_groups=<count>` at INFO. (See PMA-CC-3.)

#### PMA-3.2 🟠 High — Race between cascade-update and concurrent `继承身份组` / `修改用户身份组`

- **File:line**: `nextbot/plugins/group_manager.py:123-141`
- **Impact**:
  - Concurrent `删除身份组 X` and `继承身份组 Y X`: between `session.delete(group)` and the loop that scrubs other groups' `inherits`, another transaction can write `Y.inherits = "X"` referencing the now-deleted group. The cascade scrub already SELECTed `all_groups` before the concurrent INSERT, so it never sees `Y`'s new `inherits`. End state: `Y.inherits` references non-existent `X` — silent broken inheritance.
  - Concurrent `删除身份组 X` and `修改用户身份组 alice X`: the user-group SET happens after the bulk UPDATE saw the prior set; alice ends up assigned to the just-deleted group `X` (no FK to enforce), then her permission lookups silently return empty (`_get_group_permissions` returns `set()` for missing group, `permissions.py:53-54`).
  - SQLite default isolation is `SERIALIZABLE` only with `BEGIN IMMEDIATE` / `BEGIN EXCLUSIVE`; SQLAlchemy's default `BEGIN DEFERRED` allows concurrent reads. The `connect_args={"check_same_thread": False}` (`db.py:371`) compounds this.
- **Reproduction**: hit `删除身份组 X` from one console while the other runs `继承身份组 Y X`. Inspect `Y.inherits` after both complete — likely contains `X` even though `X` is gone.
- **Recommended fix**: Either (a) wrap the entire delete-cascade in `BEGIN IMMEDIATE`/`SELECT ... FOR UPDATE` (Postgres-style) — but SQLite supports only `BEGIN IMMEDIATE` which serializes writers, sufficient here; or (b) in `继承身份组`, re-validate parent existence inside the same transaction before commit (a TOCTOU narrowing, not a fix); or (c) add a `FOREIGN KEY (group) REFERENCES user_group(name) ON DELETE SET DEFAULT` to `User.group` and a self-FK on `Group.inherits` (hard with CSV column — would require normalizing).

#### PMA-3.3 🟠 High — No forensic log of what was deleted

- **File:line**: `nextbot/plugins/group_manager.py:145`
- **Current code**: `logger.info(f"删除身份组成功：name={name}")`.
- **Impact**: After malicious or mistaken delete, operator has no record of:
  - Who triggered the delete (no `actor user_id`).
  - What permissions / inherits the group held.
  - How many users were re-assigned, or who they were.
  - Which child groups had their `inherits` scrubbed.
- **Recommended fix**: Add structured audit log per PMA-CC-3 (also covers PMA-6.x, PMA-7.x). Minimum:
  ```
  logger.warning(
      f"权限审计：actor={actor_uid} action=group.delete target={name} "
      f"perms_snapshot={old_perms!r} inherits_snapshot={old_inherits!r} "
      f"reassigned_user_count={user_count} updated_child_groups={updated_count}"
  )
  ```
  Use `WARN` rather than `INFO` to make it stand out in log scanning.

#### PMA-3.4 🟡 Medium — No cache invalidation hook (forward-looking)

- **File:line**: `nextbot/plugins/group_manager.py:130-141`
- **Impact**: There is no in-memory permission cache today. But several other commands in this category (especially `同步访客权限`) read `Group.permissions` repeatedly per request. If a future change adds a TTL cache for `_get_group_permissions`, deleting a group needs to invalidate any cached entry; deleting a parent group needs to invalidate every child's cache. Without an explicit invalidation hook, this becomes a stale-permission bug. Worth annotating now while the cascade logic is being touched.
- **Recommended fix**: Define a `permissions_cache_invalidate(group_names: set[str])` no-op hook and call it from delete / inherit / clear / add-perm / remove-perm paths. Today it's a stub; the moment caching is added, behavior is correct.

#### PMA-3.5 🟢 Low — Log line lacks cascade counts

Same root cause as PMA-3.3, lower severity dimension.

#### PMA-3.6 🟢 Low — No confirmation gate

`删除身份组 vip` is one-shot. `同步访客权限` requires `「确认」`. Asymmetric: deleting a group is more destructive than the sync. Add the same `got("confirm_reply")` two-step pattern.

---

### 4. `继承身份组` → `handle_inherit_group` (line 159-195)

#### PMA-4.1 🟠 High — No cycle detection (direct + transitive)

- **File:line**: `nextbot/plugins/group_manager.py:167-181`
- **Current code**:

```python
child, parent = args
if child == parent:
    await bot.send(event, at + " " + reply_failure("修改", "不能继承到自身"))
    return
...
child_group.inherits = add_inherit(child_group.inherits, parent)
session.commit()
```

- **Impact**:
  - Direct self-cycle (`child == parent`) is blocked. Good.
  - Two-step cycle is NOT blocked: `继承身份组 A B` then `继承身份组 B A` succeeds. Resolution in `_get_group_permissions` (`permissions.py:43-59`) does carry a `visited` set and short-circuits on revisit, so it does not stack-overflow. But:
    1. Every `has_permission(user_in_A_or_B, *)` now traverses both A and B always — perf regression scales with cycle size × call frequency.
    2. The cycle is invisible in `身份组列表` output — operators see `A 继承: B` and `B 继承: A` as two innocuous lines without a "cycle" marker.
    3. Combined with PMA-6.1, an attacker can craft `guest → attacker_group → guest` where `attacker_group` carries dangerous perms, effectively granting them to all guests via inheritance — and the operator can't easily spot it.
  - Deep nesting (A→B→C→D→…→Z, no cycle): each `has_permission` call walks the full chain. With chain length L and call rate Q, work is O(L × Q). No DoS in practice (groups are bounded), but worth a soft cap.
- **Reproduction**:
  ```
  添加身份组 a
  添加身份组 b
  继承身份组 a b   → ✅
  继承身份组 b a   → ✅ (should reject — would create cycle)
  ```
- **Recommended fix**: Before commit, simulate the new graph and detect cycles via DFS:

  ```python
  def _would_create_cycle(session, child: str, parent: str) -> bool:
      # If `parent` (or any of its ancestors) is `child`, adding `child -> parent` cycles.
      stack = [parent]
      visited: set[str] = set()
      while stack:
          node = stack.pop()
          if node == child:
              return True
          if node in visited:
              continue
          visited.add(node)
          group = session.query(Group).filter(Group.name == node).first()
          if group is None:
              continue
          stack.extend(split_csv_values(group.inherits))
      return False
  ```
  Reply with `reply_failure("继承", "会形成循环继承")` if true. Also enforce `MAX_INHERIT_DEPTH = 8` as a soft guard against accidental deep chains.

#### PMA-4.2 🟠 High — Read-modify-write race on `Group.inherits`

- **File:line**: `nextbot/plugins/group_manager.py:174-181`
- **Impact**: Same lost-update class as PMA-6.2 / PMA-7.1 but on `inherits`. `add_inherit()` (`permissions.py:131-134`) reads the current CSV, sets-add the new parent, joins back. Two concurrent `继承身份组 A X` + `继承身份组 A Y` both read `inherits=""`, each commit `inherits="X"` or `"Y"` — one wins, the other is lost.
- **Recommended fix**: Same as PMA-6.2 below — either pessimistic lock with `with_for_update()` (only on backends that support it; SQLite needs `BEGIN IMMEDIATE`), or atomic conditional UPDATE: `UPDATE user_group SET inherits = ? WHERE name = ? AND inherits = ?` with retry on 0 rowcount.

#### PMA-4.3 🟡 Medium — Transitive-cycle gap (covered by PMA-4.1's recommended fix)

Subsumed by the DFS check above.

---

### 5. `取消继承身份组` → `handle_clear_inherit_group` (line 208-239)

#### PMA-5.1 🟡 Medium — Silently breaks `default` group's documented inheritance

- **File:line**: `nextbot/plugins/group_manager.py:217-225`
- **Current code**:

```python
group = session.query(Group).filter(Group.name == name).first()
if group is None:
    await bot.send(event, at + " " + reply_failure("修改", "身份组不存在"))
    return

group.inherits = ""
session.commit()
```

- **Impact**: `ensure_default_groups()` seeds `default` with `inherits="guest"` (`db.py:430-438`). The intention is clearly that `default` always inherits from `guest`. `取消继承身份组 default` succeeds silently and breaks that invariant; from then on, every user in `default` loses the entire guest perm set. There is no protection on the `default` group name (compare with `删除身份组` which does check `name in {"guest", "default"}`).
- **Reproduction**: `取消继承身份组 default` → `default.inherits = ""`. Run `身份组列表` → `default` now shows `继承：无`. Every user previously relying on `default → guest` perm chain breaks.
- **Recommended fix**: Apply the same reserved-name set to `取消继承身份组`:
  ```python
  if name in {"guest", "default"}:
      await bot.send(event, at + " " + reply_failure("修改", "系统内置身份组的继承关系不可清空"))
      return
  ```
  Or, alternately, allow `取消继承` for `default` only by simultaneously confirming the loss of `guest` inheritance.

#### PMA-5.2 🟢 Low — No-op masquerading as success

If the group already has `inherits=""`, the handler still writes `""` and returns `✅ 取消继承成功`. Cheap to fix:
```python
if not group.inherits:
    await bot.send(event, at + " " + reply_info("已无继承可清空"))
    return
```

---

### 6. `添加身份组权限` → `handle_add_group_perm` (line 252-283)

#### PMA-6.1 🔴 Critical — No allow-list on permission key being granted

- **File:line**: `nextbot/plugins/group_manager.py:259-269`
- **Current code**:

```python
name, permission = args
session = get_session()
try:
    group = session.query(Group).filter(Group.name == name).first()
    if group is None:
        await bot.send(event, at + " " + reply_failure("添加", "身份组不存在"))
        return

    group.permissions = add_permission(group.permissions, permission)
    session.commit()
```

- **Impact**: `permission` is whatever string the actor typed. There's no check that the actor _has_ that permission themselves (i.e. you can grant perms higher than your own — except for `owner` short-circuit, which only the .env `owner_id` users have). There's no blocklist on dangerous keys. Concrete escalation flow:
  1. Actor has `group.permission.add` (perhaps as part of a "group helper" role).
  2. Actor runs `添加身份组权限 default permission.user.add`.
  3. Every user in `default` (which inherits `guest`, which is the default for new registrations) now has `permission.user.add`.
  4. Actor runs `添加用户权限 self group.delete` — they had `permission.user.add` via `default`, so `@require_permission("permission.user.add")` passes; now they can delete groups too.
  5. Repeat for any escalation target including `permission.user.group.set`.
- **Wildcard amplification**: `_match_permission` (`permissions.py:19-23`) treats `granted.endswith(".*")` as a prefix match. So `添加身份组权限 default permission.*` grants `permission.user.add`, `permission.user.remove`, `permission.user.group.set`, `permission.admin.list`, `permission.group.guest.sync` all at once. Even more dangerous: `添加身份组权限 default *` would grant — wait, `add_permission` stores `*` literally; `_match_permission` then checks `granted.endswith(".*")` which is False for plain `*`, so plain `*` does NOT actually grant everything. But `添加身份组权限 default group.*` does grant the whole `group.*` family including `group.delete`.
- **Reproduction**: Have `group.permission.add`, run `添加身份组权限 default permission.*`. As a user in `default`, run `修改用户身份组 self admin_role` (after creating it via PMA-2.1). Now you have whatever perms `admin_role` carries.
- **Recommended fix**: Three layers, increasing strictness:
  - **Layer 1 (necessary)**: enforce `actor must currently have ≥ permission they're granting`. Pseudocode:
    ```python
    actor_uid = event.get_user_id()
    if not has_permission(actor_uid, permission):
        await bot.send(event, at + " " + reply_failure("添加", "无法授予自己未持有的权限"))
        return
    ```
    Combined with the owner short-circuit (`has_permission` returns True for owners), this lets owners grant anything but constrains delegates. This is the standard "no privilege escalation" rule (POLA / "you can't grant what you don't have").
  - **Layer 2 (defense-in-depth)**: maintain an explicit registry of valid permission keys (auto-built from `command_control(permission=...)` annotations + a small extra set for non-command checks). Reject grants for unknown keys with `身份组权限不存在，可能拼写错误` (also fixes PMA-CC-2).
  - **Layer 3 (operational)**: blocklist of "owner-only" keys (`group.delete`, `permission.user.group.set`, `permission.group.guest.sync`, `permission.user.add`, `permission.user.remove`, `group.permission.add`, `group.permission.remove`, `group.add`) that can NEVER be granted via `添加身份组权限`, only by directly editing the DB (or via a dedicated owner-only command). This guards against compromised non-owner sessions.

#### PMA-6.2 🟠 High — Read-modify-write race on `Group.permissions`

- **File:line**: `nextbot/plugins/group_manager.py:262-269`
- **Current code**:

```python
group = session.query(Group).filter(Group.name == name).first()  # SELECT
...
group.permissions = add_permission(group.permissions, permission)  # in-memory union
session.commit()  # UPDATE
```

- **Impact**: Two admins concurrently run `添加身份组权限 admin perm_a` and `添加身份组权限 admin perm_b`. Both transactions SELECT `permissions=""`, both compute their respective new CSV, both UPDATE. The later commit wins; the earlier perm is lost.
- **Reproduction**: Manually demonstrate via two parallel SQL sessions on a SQLite test DB; or use threading harness on the actual `get_session()`.
- **Recommended fix**: Two viable approaches:
  - **Atomic conditional UPDATE with retry**:
    ```python
    for _ in range(5):
        group = session.query(Group).filter(Group.name == name).first()
        if group is None:
            ...
        old = group.permissions
        new = add_permission(old, permission)
        if new == old:  # already present
            break
        rowcount = execute_rowcount(
            session,
            update(Group)
                .where(Group.name == name, Group.permissions == old)
                .values(permissions=new),
        )
        if rowcount == 1:
            session.commit()
            break
        session.rollback()
    else:
        await bot.send(event, at + " " + reply_failure("添加", "并发冲突，请稍后重试"))
        return
    ```
  - **Pessimistic lock** via `BEGIN IMMEDIATE` (SQLite serializes writers).

  Either is fine; the conditional-UPDATE pattern is already used elsewhere in the codebase (e.g. coin balance updates; cross-check `nextbot/economy_*` for prior art).

#### PMA-6.3 🟡 Medium — Log line lacks actor + before/after snapshot

- **File:line**: `nextbot/plugins/group_manager.py:273`
- **Current**: `logger.info(f"添加身份组权限成功：name={name} permission={permission}")`.
- **Recommended**: Add `actor=<event.get_user_id()>` and `permissions_after=<group.permissions>` so the log answers who, what, and the resulting state.

---

### 7. `删除身份组权限` → `handle_remove_group_perm` (line 296-327)

#### PMA-7.1 🟠 High — Same lost-update race as PMA-6.2

- **File:line**: `nextbot/plugins/group_manager.py:306-313`
- **Impact**: Identical class to PMA-6.2 but on the remove path. Concurrent `删除身份组权限 admin perm_a` + `添加身份组权限 admin perm_b` can:
  - Have remove see `["a","b"]`, drop `a` → `["b"]`.
  - Have add see `["a","b"]`, append `c` → `["a","b","c"]`.
  - Last commit wins. If add wins, `a` is resurrected. If remove wins, `c` is lost.
- **Recommended fix**: Same conditional-UPDATE-with-retry as PMA-6.2.

#### PMA-7.2 🟡 Medium — Log gap (same as PMA-6.3)

Apply the same actor + before/after snapshot fix.

#### PMA-7.3 ℹ️ Info — Removing a non-existent permission silently "succeeds"

`remove_permission(value, permission)` (`permissions.py:125-128`) uses `set.discard()`, which is a no-op when the perm isn't present. The handler then commits and replies `✅ 删除成功` even though nothing changed. Low severity (no security impact), but the reply is misleading. Consider replying `reply_info("权限不存在，无需删除")` when the resulting set equals the original.

---

## Cross-cutting findings

### PMA-CC-1 🟢 Low — Module-level matcher binding style inversion

`list_matcher = on_command("身份组列表")` etc. (lines 19-25) are declared in a block before any handler. Other plugin files in `nextbot/plugins/` typically declare `matcher = on_command(...)` immediately above its `@matcher.handle()`-decorated function. Functionally equivalent; stylistic only.

### PMA-CC-2 🟠 High — No permission-key registry → typos silently no-op

- **Affected**: `添加身份组权限`, `删除身份组权限` (and across `permission_manager.py`'s `添加用户权限`).
- **Issue**: `add_permission(value, "economy.singin")` (typo: should be `signin`) succeeds, stores `economy.singin` in CSV, and then `_match_permission(granted="economy.singin", required="economy.signin")` is False, so users never get the perm. The bot looks broken; the actor sees ✅. Worse, when the actor runs `身份组列表`, the typo'd perm is right there in the CSV but they don't notice.
- **Fix**: Build a registry at `init_db()` time by walking `command_control(permission=...)` registrations + a small "non-command perms" allow-list (the `*.list`/system perms). Reject grants for unknown keys, with a "did you mean" suggestion (`difflib.get_close_matches`). This is a structural fix that benefits 3+ commands and one ongoing operational pain.

### PMA-CC-3 🟠 High — No structured audit log for permission-changing actions

- **Affected**: All 7 commands here + `permission_manager.py`'s 3 mutating commands (`添加用户权限`, `删除用户权限`, `修改用户身份组`).
- **Issue**: Every command in this category is, definitionally, a permission change. The current logs (`logger.info(f"... 成功：name={X}")`) record _what command ran_ but not:
  - **Actor**: which `user_id` invoked it.
  - **Snapshot**: before/after state of the affected resource.
  - **Cascade**: how many users / groups were touched downstream.

  Permission changes are the single highest-value audit surface in the bot — the answer to "how did attacker X get admin?" must be reconstructable from logs.
- **Fix**: Add a small audit helper in `nextbot/permissions.py` (or a new `nextbot/audit.py`):

  ```python
  def audit_permission_change(
      *,
      actor_user_id: str,
      action: str,             # "group.add", "group.delete", "group.permission.add", ...
      target: str,             # group name or user_id
      before: dict | None,
      after: dict | None,
      context: dict | None = None,
  ) -> None:
      logger.warning(
          f"权限审计：actor={actor_user_id} action={action} target={target} "
          f"before={before!r} after={after!r} context={context!r}"
      )
  ```

  Use `WARN` level deliberately so audit lines stand out from routine INFO traffic, even if they're not "warnings" semantically. Then call from every mutating handler. This is the highest-leverage fix in the audit because it converts every other finding from "we have no idea what happened" into "we can replay what happened" — even if the underlying race / escalation isn't yet patched.

### PMA-CC-4 ℹ️ Info — `User.group` lacks DB-level FK

`User.group: Mapped[str]` (`db.py:131`) is a free string with no `ForeignKey("user_group.name")`. So:
- WebUI / direct DB tooling can drop a group without going through `删除身份组` → users orphaned (`User.group` references non-existent group).
- `_get_group_permissions(session, "ghost", set())` returns `set()` silently. User sees "permission denied" with no clue why.

Adding the FK (with `ON DELETE SET DEFAULT` to a configured fallback group) would harden PMA-3.1. Trade-off: SQLite supports FKs but does not enforce them by default (`PRAGMA foreign_keys = ON` must be set per-connection — currently not done in `db.py`).

### PMA-CC-5 ℹ️ Info — Transitive escalation via permission family ownership

Even with PMA-6.1's "actor must have ≥ what they grant" rule applied, an actor with `permission.user.add` can grant `permission.user.add` to a group `X`, then add user `Y` to that group (if they have `permission.user.group.set`), then `Y` can grant `permission.user.add` to anything, including their own user record. The chain is:

```
actor(has permission.user.add, permission.user.group.set, group.permission.add)
  → 添加身份组权限 X permission.user.add        [legal under POLA: actor has it]
  → 修改用户身份组 attacker X                     [legal: actor has the cmd]
  → attacker now has permission.user.add via X
  → attacker can recursively grant this to others
```

This is _not_ a bug in this file alone — it's a structural property of "delegated permission management" without role hierarchy. Two mitigations to consider:
- Mark `permission.*` and `group.permission.*` keys as **non-delegatable** (only owners can grant them, never via `添加身份组权限`).
- Enforce that anyone modifying a group's permissions must have `permission.delegate.<key>` for each key — adds bureaucracy but draws the trust boundary explicitly.

---

## Caveats / Not found / Out of scope

- I did not load `nextbot/server_broadcast.py`. Group commands here are pure local DB writes — no fan-out to game servers, so the broadcast helper does not apply. Confirmed by `grep` for `broadcast` / `request_server_api` in this file: zero matches.
- I did not run a live concurrency repro; race assertions are derived from reading the code (no row lock, no conditional UPDATE, no `BEGIN IMMEDIATE`). The fix patterns recommended (PMA-6.2 conditional UPDATE) exist as prior art in the codebase but I did not pin the exact reference; the implement agent should `grep -n "execute_rowcount" nextbot/` to find an existing example before duplicating.
- "Owner / admin protection" findings are framed against the threat model that **there is no DB-side owner group** — only the `.env` short-circuit. If the project plans to introduce a DB owner group later, half of PMA-2.1 / PMA-3.1's fix recommendations need re-evaluation.
- `修改用户身份组` lives in `permission_manager.py` and is in batch B. Audit prompt mentions it under "owner protection" — covered here only as a cross-reference for PMA-2.1 / PMA-6.1 attack chains; full audit of that handler belongs in batch B.
- I did not check `command_control`'s permission-toggle behavior end-to-end (whether disabling a command in the WebUI bypasses `@require_permission`). Worth a separate look but out of scope here.
