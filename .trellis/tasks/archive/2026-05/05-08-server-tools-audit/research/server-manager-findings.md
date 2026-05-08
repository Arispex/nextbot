# Research: 服务器管理 Category Audit (server_manager.py)

- **Query**: Audit 添加服务器 / 删除服务器 / 服务器列表 / 测试连通性 for vulnerabilities, performance, data integrity, cross-file impact
- **Scope**: internal
- **Date**: 2026-05-08
- **Target file**: `/Users/arispex/CascadeProjects/nextbot/nextbot/plugins/server_manager.py` (222 lines)

---

## Executive Summary

The 4 commands in `server_manager.py` look small but contain the **single most dangerous design decision in the entire NextBot codebase**: `Server.id` is a *positional* identifier that the bot continuously renumbers. Two of the four commands actively mutate that ID space:

- `添加服务器` allocates IDs via `count + 1` (race-condition-prone, breaks under gaps).
- `删除服务器` does `Server.id = Server.id - 1` for every higher row, then commits.

Because **NO foreign-key constraint** ties the dependent tables (`shop_item.target_server_id`, `lottery_prize.target_server_id`) to `server.id`, the renumber silently rewires those rows to a different, still-existing server. Every reference in `shop`, `lottery`, `warehouse`, `player_query`, `leaderboard`, `server_send`, `server_tools`, `user_manager`, `ban_core`, and the **WebUI** uses these positional IDs as stable identifiers — and they are not stable.

This is the same flaw mirrored in `server/routes/webui_servers.py` lines 280-302, so fixing only the chat command leaves the WebUI as a second attack surface.

**Severity ladder**:
- 🔴 Critical: SM-2.1 (silent cross-table rewiring), SM-1.1 (ID collision after gaps), SM-2.4 (mirror bug in WebUI)
- 🟠 High: SM-1.2 (concurrent add race), SM-2.2 (cascade not checked), SM-3.1 (no transaction in add)
- 🟡 Medium: SM-1.3 (no input validation), SM-1.4 (no name uniqueness), SM-4.1 (lockable UPDATE on SQLite)
- 🟢 Low: SM-3.2 (info disclosure on test), SM-1.5 (newline in name)
- ℹ️ Info: SM-5.1 (token redacted in list — confirmed safe), SM-6.1 (default permission policy)

---

## Cross-Table Impact Map (key prerequisite for SM-2.x)

`Server.id` is referenced as a "stable" pointer in **49+ call sites across 3 layers**:

| Layer | File | Usage |
|---|---|---|
| Schema | `nextbot/db.py:295` | `ShopItem.target_server_id` (Optional Integer, **NO FK**, **NO INDEX**) |
| Schema | `nextbot/db.py:340` | `LotteryPrize.target_server_id` (Optional Integer, **NO FK**, **NO INDEX**) |
| Bot — direct ID arg | `plugins/server_send.py:35,67` | user types `转发 <server_id>` |
| Bot — direct ID arg | `plugins/server_tools.py:42,84,152,158,210,216` | user types `执行 <server_id> <cmd>` etc. |
| Bot — direct ID arg | `plugins/warehouse.py:1357,1391` | user types `领取 <server_id>` |
| Bot — direct ID arg | `plugins/player_query.py:336,499,623,711,821,905` | inventory / map / progress queries |
| Bot — direct ID arg | `plugins/leaderboard.py:388,498,608,722` | per-server leaderboards |
| Bot — broadcast | `ban_core.py:64`, `plugins/ban.py:263`, `plugins/user_manager.py:122,518` | iterate all servers (renumber-safe) |
| Bot — config-bound | `plugins/shop.py:541,577,707-730`, `plugins/lottery.py:333-338,475,535` | reads `target_server_id` from `ShopItem` / `LotteryPrize` |
| Server (HTTP) | `server/routes/webui_servers.py:280-302` | **same renumber bug, mirrored** |
| Server (HTTP) | `server/routes/webui_shop.py:225-234,605,808` | accepts `target_server_id` from frontend |
| Server (HTTP) | `server/routes/webui_lottery.py:217-226,610,817` | accepts `target_server_id` from frontend |
| Frontend | `webui/static/js/shop.js:483-521`, `lottery.js:492-531`, `servers.js:322-815` | renders / submits server IDs |
| Frontend | `templates/progress.html:324-327`, `pages/inventory_page.py:61`, `pages/progress_page.py:43` | renders server IDs in report URLs |

Two distinct categories of references:

1. **Transient** (user types `<server_id>` in a chat command): user just looks at the latest `服务器列表` and types a number. Safe across renumber **as long as the user's mental model is refreshed** between deletion and use.
2. **Persistent** (`ShopItem.target_server_id`, `LotteryPrize.target_server_id`, persisted in DB): rows were saved months ago pointing at "server 3 = PvE". After deletion of server 2, those rows still say `target_server_id=3`, but the server formerly known as 4 is now 3. **All shop purchases and lottery payouts are silently rerouted to the wrong server.**

This is the single fact that makes 删除服务器 a 🔴 critical bug, not a 🟡 cosmetic one.

---

## Findings

### Findings — 添加服务器 (handle_add_server, lines 24-72)

#### SM-1.1 🔴 ID collision after deletion (即使在单线程下也必然触发)

- **File**: `nextbot/plugins/server_manager.py:45-55`
- **Code**:
  ```python
  count = session.query(Server).count()
  server = Server(
      id=count + 1,
      name=name, ip=ip, game_port=game_port, restapi_port=restapi_port, token=token,
  )
  session.add(server)
  session.commit()
  ```
- **Impact**: `count + 1` only equals `max(id) + 1` when IDs are dense `1..N`. The 删除服务器 path *does* renumber to keep them dense, **but** in practice gaps appear via:
  - WebUI delete is the same code path (renumbers) — safe
  - Direct DB editing / data migration / restoring partial backups — gaps possible
  - Any future change that drops the renumber loop will instantly break 添加 — fragile coupling
  - If any server row is inserted with a non-default ID (e.g. test fixtures), `count` and `max(id)` diverge immediately

  When `count + 1` collides with an existing `id`: `Server.id` is `primary_key=True` (`db.py:105`), so SQLite raises `IntegrityError`. With the current code that exception escapes the `try/finally` (no `except`, no rollback), so the session leaks, the user sees a tracebacked NoneBot reply, and no cleanup runs.

  Note: WebUI `webui_servers.py:208` uses `func.max(Server.id) + 1`, which is the correct version. The bot lags behind.
- **Reproduction**:
  1. Have a single server with `id=1`. Delete it via `删除服务器 1` — table is empty, `count() = 0`.
  2. Manually `INSERT INTO server (id, name, ...) VALUES (5, ...)` (e.g. via WebUI bug, sqlite shell).
  3. `count() = 1`, so `添加服务器 X ...` tries to insert with `id = 1 + 1 = 2`. Succeeds, but **gap not closed**, and the next 添加 will try id=3, etc., all skipping 5. The first 添加 is fine; 添加 a 5th row tries id=5 → IntegrityError.
- **Recommended fix**:
  ```python
  max_id = int(session.query(func.max(Server.id)).scalar() or 0)
  server = Server(id=max_id + 1, ...)
  ```
  Match `webui_servers.py:208`. Better: switch to `autoincrement=True` (see Cross-Cutting).

#### SM-1.2 🟠 Concurrent add race (TOCTOU on `count + 1`)

- **File**: `nextbot/plugins/server_manager.py:45-55`
- **Code**: same as SM-1.1
- **Impact**: Two concurrent NoneBot handlers (NoneBot uses asyncio + threadpool; SQLAlchemy session is constructed per-handler) execute `count()` simultaneously, both read `N`, both try `INSERT id=N+1`. SQLite serialises writes via a global write-lock, so the loser raises `IntegrityError`; the user sees the unhandled traceback as in SM-1.1. The same vulnerability applies between bot-add and webui-add.
- **Reproduction**:
  1. Send `添加服务器 A 1.1.1.1 7777 7878 t1` and the WebUI POST `/webui/api/servers` simultaneously.
  2. Both reach `count()`/`max()` before either commits → second commit raises `sqlite3.IntegrityError: UNIQUE constraint failed: server.id`.
- **Recommended fix**:
  - Use `autoincrement=True` on `Server.id` to delegate ID allocation to SQLite.
  - If positional IDs must remain, wrap the read-modify-write in a `BEGIN IMMEDIATE` transaction or use `INSERT INTO server (id, ...) SELECT COALESCE(MAX(id), 0)+1, ...`.

#### SM-1.3 🟡 No input validation (parity gap with WebUI)

- **File**: `nextbot/plugins/server_manager.py:38-55`
- **Code**:
  ```python
  if len(args) != 5:
      raise_command_usage()
  name, ip, game_port, restapi_port, token = args
  # straight to DB insert — no checks
  ```
- **Impact**: The WebUI path validates everything (`webui_servers.py:64-134`):
  - `name` regex `^[A-Za-z0-9一-鿿 ._-]{1,32}$`, non-empty
  - `ip` non-empty (still weak — accepts `127.0.0.1:80`, hostnames)
  - `game_port` / `restapi_port` parsed to int, must be 1-65535, stored back as str
  - `token` non-empty, length 1-128
  
  The bot path enforces NONE of these. So the bot can persist `name=""`, `ip=""`, `game_port="not-a-number"`, `token=""`. Subsequent operations:
  - `request_server_api` constructs `http://{ip}:{restapi_port}{path}` (`tshock_api.py:63`). If `ip=""` → URL `http://:7878/...` → httpx raises, caught as `TShockRequestError` → `测试连通性` returns "无法连接服务器".
  - If `restapi_port="abc"` → httpx fails to parse port → `httpx.InvalidURL` is **not** a `httpx.RequestError`, so `request_server_api` lets it propagate; user sees a NoneBot traceback.
  - Empty token causes silent auth bypass on TShock servers configured permissively.
- **Reproduction**:
  1. `添加服务器 "" "" abc def ""` → DB row written successfully.
  2. `测试连通性 <id>` → unhandled `httpx.InvalidURL` because `int("abc")` fails inside httpx URL parser.
- **Recommended fix**: Extract `_validate_server_payload` from `webui_servers.py` into a shared helper (e.g. `nextbot/server_validation.py`) and call it from both code paths.

#### SM-1.4 🟡 No name-uniqueness check

- **File**: `nextbot/plugins/server_manager.py:46-55` (and `webui_servers.py` — bug is shared)
- **Code**: `Server.name` is declared `nullable=False` only — no UNIQUE constraint (`db.py:106`).
- **Impact**: Two servers can be named "PvE", and dependent commands display them as `1.PvE` / `4.PvE`. Cross-server commands (`server_send`, `server_tools`) only address by ID, but humans copy-paste by name; this is a data-quality smell rather than a vulnerability. Listed because the WebUI test path uses name in failure logs without uniqueness, and `target_server_label` resolution in shop/lottery would render confusingly.
- **Recommended fix**: Decide whether name should be unique at DB layer (`UniqueConstraint`) or just at app layer. WebUI already has a regex but no uniqueness check.

#### SM-1.5 🟢 Newline / control characters in `name` survive into reply rendering

- **File**: `nextbot/plugins/server_manager.py:42, 64-71` and `text_utils.reply_block`
- **Impact**: `parse_command_args_with_fallback` calls `text.split()` which splits on any whitespace including `\n` (line 71 of `message_parser.py`). So a literal newline inside `name` cannot reach the bot via this path — the parser will treat it as multiple args and `len(args) != 5` would trip. **However**, args 2-5 (`ip`, ports, token) cannot contain whitespace either for the same reason. ✅ This vector is closed by the parser.
  
  Caveat: WebUI does not have this protection — `webui_servers.py:_normalize_name` regex covers it (`^[A-Za-z0-9一-鿿 ._-]{1,32}$` excludes `\n`). Both paths happen to be safe, but for different reasons; if the parser is ever changed to use `shlex` (to support quoted args), this defense disappears.
- **Recommended fix**: Add explicit `if "\n" in name or "\r" in name` guard to `_validate_server_payload` to make the protection independent of the splitter.

---

### Findings — 删除服务器 (handle_delete_server, lines 75-127)

#### SM-2.1 🔴 ID renumber silently rewires `ShopItem.target_server_id` and `LotteryPrize.target_server_id`

- **File**: `nextbot/plugins/server_manager.py:107-113`
- **Code**:
  ```python
  session.delete(server)
  session.flush()
  session.query(Server).filter(Server.id > deleted_id).update(
      {Server.id: Server.id - 1}, synchronize_session=False
  )
  session.commit()
  ```
- **Impact**: `ShopItem.target_server_id` (`db.py:295`) and `LotteryPrize.target_server_id` (`db.py:340`) are plain `Integer` columns with **no FK constraint**. Dropping & renumbering `Server` does **not** propagate to those tables.

  Concrete scenario:
  - Servers exist: `1=PvP`, `2=PvE`, `3=Creative`.
  - A shop item is configured "send to server 3 (Creative)" → `shop_item.target_server_id=3`.
  - Admin runs `删除服务器 2`. After this:
    - `server` table: `1=PvP`, `2=Creative` (Creative was renumbered from 3 to 2).
    - `shop_item.target_server_id=3` is now a dangling reference to a server that no longer exists.
    - When a user buys that item, `shop.py:730` does `session.query(Server).filter(Server.id == 3).first()` → `None`. Depending on the surrounding handler, this either silently no-ops (item awarded but command never ran) or raises an unhandled exception.
  - **Worse case**: servers `1=PvP`, `2=PvE`, `3=Creative`, `4=Survival`. Shop item points at server 4 (Survival). Admin deletes server 2.
    - After: `1=PvP`, `2=Creative`, `3=Survival`.
    - `shop_item.target_server_id=4` → still no match → benign.
    - But for items with `target_server_id=3` (Creative), they now resolve to server 3 = Survival. **The shop item silently delivers commands to the wrong server**. This is full-blown silent data corruption.

  Same bug exists in `LotteryPrize` snapshots (`lottery.py:333-338, 475, 535` reads `target_server_id` from the prize and from a saved snapshot in `ban_core` /  `entry["target_server_id"]`). The lottery snapshot path makes this even worse: the snapshot is taken at draw time and persisted, so even a *future* renumber after the snapshot is taken corrupts retrospective data.

  Cross-file accomplices that make this worse:
  - `webui_shop.py:233` validates `target_server_id in valid_server_ids` *only at write time* — never re-validates after server deletion.
  - `webui_lottery.py:225` same pattern.
  - There is no migration / cleanup job to re-point or null out dangling refs.

- **Reproduction**:
  1. Run `添加服务器 A ... ; 添加服务器 B ... ; 添加服务器 C ...` to get IDs 1, 2, 3.
  2. Configure a shop item targeting server 3 (via `webui` or DB). Confirm `shop_item.target_server_id=3`.
  3. `删除服务器 2`.
  4. `SELECT id, name FROM server` → `1=A, 2=C`. `SELECT target_server_id FROM shop_item` → still `3` (orphan), or if you instead configured the item against server 2 you'd see it now silently pointing to old server 3 (now 2).
  5. Trigger a purchase of that shop item → command runs on server C, not the originally-intended A.

- **Recommended fix** (in priority order):
  1. **Stop renumbering**. The `update({Server.id: Server.id - 1})` should be deleted entirely. Treat `Server.id` as immutable.
  2. After step 1, fix downstream impact:
     - Add a cascading cleanup: when deleting server X, set `ShopItem.target_server_id = NULL WHERE target_server_id = X`, same for `LotteryPrize`. Alternatively, refuse deletion when refs exist (see SM-2.2).
     - Add the missing `ForeignKey("server.id", ondelete="SET NULL")` to both columns; SQLite enforces only when `PRAGMA foreign_keys = ON`, which would need to be set in `db.py:_ensure_engine_and_factory` via `connect_args`.
  3. Migration script to detect and report any currently-dangling `target_server_id` values from the historical renumber bug. (See Cross-Cutting Findings.)

#### SM-2.2 🟠 Delete does not check or warn about cascading impact

- **File**: `nextbot/plugins/server_manager.py:100-113`
- **Impact**: Even if SM-2.1 is fixed by stopping the renumber, the deletion still leaves orphan refs in `ShopItem` / `LotteryPrize` (see fix step 2). Today the bot performs the destructive op with **no preflight count, no confirmation prompt, no second-step token**. A typo on a 4-server install ( `删除服务器 2` instead of `3`) silently breaks every shop item & lottery prize aimed at the wrong server.
- **Recommended fix**: Before deleting, run:
  ```python
  shop_refs = session.query(ShopItem).filter(ShopItem.target_server_id == target_id).count()
  prize_refs = session.query(LotteryPrize).filter(LotteryPrize.target_server_id == target_id).count()
  if shop_refs or prize_refs:
      # require explicit `--force` flag, or require typing the server name
  ```
  At minimum, log + reply with the dependency count.

#### SM-2.3 🟠 Renumber + concurrent add → write skew

- **File**: `nextbot/plugins/server_manager.py:110-113` and `45-55`
- **Impact**: An admin runs `删除服务器 1` while another admin runs `添加服务器 ...`. Sequence:
  1. Add reads `count()` → 3 (servers exist with id 1, 2, 3). Plans to insert id=4.
  2. Delete acquires SQLite write lock, deletes id=1, renumbers id=2→1, id=3→2, commits.
  3. Add now commits id=4. Final state: `1, 2, 4` — gap reintroduced.
  
  Because there's no transaction enclosing add's `count() + insert` (autoflush=False, autocommit=False session, but no explicit BEGIN), SQLite uses a deferred transaction that promotes to write at commit time. The gap doesn't break anything immediately, but it **re-arms SM-1.1**: the next add will use `count() + 1 = 4`, colliding with the existing id=4.
- **Recommended fix**: Together with SM-1.1 / SM-1.2 — switch to `max(id)+1` or autoincrement, and wrap in `BEGIN IMMEDIATE`.

#### SM-2.4 🔴 Same renumber bug exists in WebUI delete endpoint

- **File**: `server/routes/webui_servers.py:280-302`
- **Code**: identical pattern — `session.delete(server); session.flush(); session.query(Server).filter(Server.id > deleted_id).update({Server.id: Server.id - 1}, ...)`.
- **Impact**: Fixing only `server_manager.py` leaves the same data-corruption vector exposed via `DELETE /webui/api/servers/{server_id}`. Any audit fix must touch both files.
- **Recommended fix**: Centralise delete logic into a single function (e.g. `nextbot.server_admin.delete_server(target_id, session)`) and have both the bot handler and the WebUI route call it.

---

### Findings — Session / Transaction / SQLite

#### SM-3.1 🟠 No `try/except` + no `session.rollback()` around add — leaked sessions on failure

- **File**: `nextbot/plugins/server_manager.py:43-57`
- **Code**:
  ```python
  session = get_session()
  try:
      count = session.query(Server).count()
      server = Server(id=count+1, ...)
      session.add(server)
      session.commit()
  finally:
      session.close()
  ```
- **Impact**: When `commit()` raises (e.g. `IntegrityError` from SM-1.1 or SM-1.2), the exception propagates up. `finally` does `close()` which discards uncommitted changes — that's fine. But:
  - The user-facing `await bot.send(...)` block (lines 62-72) is **outside** the try/finally, so on failure no reply is sent → the user sees a NoneBot traceback only.
  - There is no `rollback()` before close — SQLAlchemy 2.x will roll back implicitly on close, so technically OK, but `webui_servers.py:226` does the explicit `rollback()` and returns a structured 500 to the caller. Symmetry argues for the same here.
  - The `count + 1` calculation is read at line 45 but used to render the success reply at lines 60, 67 — if commit fails this still claims success in the log line at 59 because it runs before the implicit rollback... wait, actually `logger.info` is at line 59, *after* `finally`, so on commit failure it's never executed (we'd be unwinding). Still: line 59 references `count + 1` from the closed session — works because it's a Python int snapshot, but it confusingly looks DB-bound.
- **Recommended fix**:
  ```python
  session = get_session()
  try:
      max_id = int(session.query(func.max(Server.id)).scalar() or 0)
      new_id = max_id + 1
      server = Server(id=new_id, ...)
      session.add(server)
      session.commit()
  except IntegrityError:
      session.rollback()
      logger.warning(f"添加服务器失败：name={name} reason=ID 冲突")
      await bot.send(event, at + " " + reply_failure("添加", "ID 分配冲突，请重试"))
      return
  except Exception:
      session.rollback()
      raise
  finally:
      session.close()
  ```

#### SM-4.1 🟡 The renumber UPDATE locks the entire `server` table on SQLite

- **File**: `nextbot/plugins/server_manager.py:110-113`
- **Impact**: SQLite uses table-level locking for writes. The `UPDATE server SET id = id - 1 WHERE id > deleted_id` runs as part of the same transaction as the prior `DELETE`. With `synchronize_session=False`, SQLAlchemy issues raw SQL and skips ORM-level invalidation, which is fine; but **other read-only handlers blocked on a SELECT against `server`** will queue while the transaction is open. Server count is small (typically <10), so this is not a perf problem in practice, but the renumber is the only place in the codebase that writes O(N) rows in one statement, and it does so unnecessarily — eliminating SM-2.1 also eliminates this contention.

#### SM-4.2 ℹ️ Engine config note

- **File**: `nextbot/db.py:357-367`
- `connect_args={"check_same_thread": False}` is fine for NoneBot's threadpool model. `autoflush=False, autocommit=False` is correct. Sessions are constructed per-call. **PRAGMA foreign_keys is NOT enabled**, which means even if `target_server_id` were declared as a `ForeignKey`, SQLite would not enforce it. To make the recommended cascade in SM-2.1 actually fire, `db.py` needs:
  ```python
  from sqlalchemy import event
  @event.listens_for(_engine, "connect")
  def _enable_fk(dbapi_conn, _):
      dbapi_conn.execute("PRAGMA foreign_keys = ON")
  ```

---

### Findings — 服务器列表 (handle_list_servers, lines 130-166)

#### SM-5.1 ✅ Token / restapi_port redaction — confirmed safe

- **File**: `nextbot/plugins/server_manager.py:158-162`
- **Code**:
  ```python
  for server in servers:
      lines.append(f"{server.id}.{server.name}")
      lines.append(f"IP：{server.ip}")
      lines.append(f"端口：{server.game_port}")
      lines.append("")
  ```
- **Impact**: Output never references `server.token` or `server.restapi_port`. ✅ No leak via this command.
- **Caveat**: `server.list` is in `DEFAULT_GUEST_PERMISSIONS` (`db.py:79`) — guests see all server IPs and game ports. This is intended (game ports must be public for players to connect), but if any deployment uses `server.ip` to encode an internal hostname (e.g. `tshock-internal.local`), guests learn it. **This is policy, not a bug.**
- **Recommended fix** (optional): document in a spec that `server.ip` is treated as public.

---

### Findings — 测试连通性 (handle_test_server, lines 169-222)

#### SM-3.2 🟢 Failure message uses `get_error_reason` which can leak HTTP/API status text

- **File**: `nextbot/plugins/server_manager.py:217-222`, `tshock_api.py:29-45`
- **Code**:
  ```python
  reason = get_error_reason(response)
  await bot.send(event, at + " " + reply_failure("测试", f"{reason}"))
  ```
- **Impact**: `get_error_reason` returns:
  - The TShock-server-provided `payload["error"]` text directly (could include path / token state info if the upstream is a malicious / misconfigured service)
  - Otherwise a localised reason (`"未提供令牌"` / `"无效的令牌"` / etc.) — these literally tell the user that the *bot's stored token* was rejected, which is fine to surface.
  - Otherwise `"状态码 {code}"` — fine.
  
  No path leaks the bot-side `server.token` itself. The only privacy concern is that "无效的令牌" tells the user the configured token is wrong; a malicious user with `server.test` permission could brute-force token edits and use the response to confirm validity. `server.test` is admin-level (NOT in DEFAULT_GUEST_PERMISSIONS), so the threat is internal-only. ✅ Acceptable.
- **Recommended fix**: None required. Optionally, normalise the error to a generic "无法验证服务器" when `api_status in ("401", "403")` to avoid hinting at the token-config issue.

#### SM-3.3 🟡 Session held during `await request_server_api` call?

- **File**: `nextbot/plugins/server_manager.py:192-196` and `202-209`
- **Code**:
  ```python
  session = get_session()
  try:
      server = session.query(Server).filter(Server.id == target_id).first()
  finally:
      session.close()
  
  if server is None:
      ...
  
  try:
      response = await request_server_api(server, "/tokentest")
  ```
- **Impact**: The session is **closed before** the await — good. But `server` is a detached ORM instance whose lazy-loaded attributes would error if accessed; here only `server.ip / server.restapi_port / server.token` are accessed (all eager-loaded scalars), so it works. ✅ Pattern is correct.
- **Caveat**: Some other commands (`warehouse.py`, `player_query.py`) re-query inside an open session for the same data — inconsistent style across the codebase, but not a vulnerability.

---

### Findings — Permissions (cross-cutting)

#### SM-6.1 ℹ️ Permission policy — verified

- **Default guest permissions** (`db.py:34-95`): includes `server.list` and `server.send`, **excludes** `server.add` / `server.delete` / `server.test`.
- **Bot decorator policy** (`server_manager.py:33, 84, 139, 178`): `@require_permission("server.add" | "server.delete" | "server.list" | "server.test")`.
- **Owner override** (`permissions.py:62-67`): owners always get all perms.
- **Conclusion**: ✅ Privileged ops are properly gated behind explicit admin grants. No accidental guest access.

#### SM-6.2 ℹ️ WebUI authn parity

- The WebUI server CRUD routes (`webui_servers.py`) do **not** appear to be guarded by the same permission system in this audit's scope (no `require_permission` calls in this file). They presumably rely on session/cookie middleware that's not visible from this slice. **Out of scope** for this audit; flagging for downstream review.

---

## Cross-Cutting Findings — The ID Renumber Anti-Pattern

The four issues SM-1.1, SM-2.1, SM-2.3, SM-2.4 all stem from one design choice: **`Server.id` is a positional, mutable identifier**. Every fix above is a workaround. The structural fix is:

### Recommendation: replace renumber with stable IDs + ON DELETE SET NULL

1. **`Server.id` becomes a stable surrogate key** (autoincrement, never reused, never renumbered). Existing rows keep their IDs.
2. **Display position decouples from ID**. Add a `display_order: int` column or just sort by `id` and accept gaps in the listed numbering. Users address servers by `name` (or by listed line number) rather than by raw DB ID.
3. **Add proper FKs**:
   ```python
   target_server_id: Mapped[Optional[int]] = mapped_column(
       Integer, ForeignKey("server.id", ondelete="SET NULL"), nullable=True, index=True
   )
   ```
   on both `ShopItem.target_server_id` and `LotteryPrize.target_server_id`. Enable `PRAGMA foreign_keys = ON` (see SM-4.2).
4. **Soft delete** (alternative): introduce `Server.deleted_at`. `服务器列表` filters out deleted; existing `target_server_id` references continue to work but resolve to a "已删除服务器" placeholder until cleaned up. This is the lowest-risk rollout because no data migration is required.
5. **One-time migration script**: scan `ShopItem` and `LotteryPrize` for `target_server_id` values referring to *currently-existing* servers that may be victims of historical renumber bugs. Cannot be reliably automated (no audit log of past renumbers), but can at least *report* every distinct (server_name, target_server_id, last_modified) tuple for human review.
6. **Centralise delete**: create `nextbot/server_admin.py` exposing `delete_server(target_id)` used by both `plugins/server_manager.py` and `server/routes/webui_servers.py`. Drop the renumber loop in both call sites.

### Migration risk

- Existing users may have learned to address servers by their renumbered ID (`/转发 2 hello` works on whatever is currently server 2). After the change, the displayed number stays stable — *better* UX, not regressive.
- The single thing that will break: any external automation (other bots, scripts, screenshots in user docs) that hard-codes specific IDs assuming density. Risk is low because IDs are local-install-specific anyway.

---

## Caveats / Not Found

- **Race-condition reproductions are theoretical**: I did not run the bot against a live multi-handler test harness. The IntegrityError path is well-defined by SQLite's UNIQUE constraint semantics, but exact NoneBot behaviour on uncaught exceptions in handlers is not directly verified.
- **TShock upstream behaviour**: `get_error_reason` returns `payload["error"]` verbatim. I have not audited what TShock actually puts there for `/tokentest` — it may include hostnames or paths from the upstream. Risk is low (admin-only command) but unverified.
- **PRAGMA foreign_keys default**: confirmed not set in `db.py:_ensure_engine_and_factory` (`db.py:357-367`). Some SQLite distributions default it to ON via compile flags; should not be relied on.
- **Frontend impact of fixing renumber**: `server/webui/static/js/servers.js:322-815` displays `server.id` as the row identifier. A switch to non-dense IDs would require minor copy changes ("ID #5 of 3 servers" looks odd) but no functional changes.
- **Not audited**: log volume / log level for the existing logger.info calls (lines 59, 117, 165, 213). They look proportionate.
