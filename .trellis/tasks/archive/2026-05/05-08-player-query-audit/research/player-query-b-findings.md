# Research: Player Query Audit (Batch B — last 4 of 8)

- **Query**: 审计 `nextbot/plugins/player_query.py` 后 4 条命令（我的地图 / 用户地图 / 查看地图 / 进度）
- **Scope**: internal — Python source review with cross-reference to ST-2.1/3.3 fix template
- **Date**: 2026-05-08

---

## Audit context

- **Target**: `nextbot/plugins/player_query.py` lines 606–974
- **Reference patterns** (gold standard, just committed in `942d923`):
  - `nextbot/plugins/server_tools.py:42-60` — `_MAX_BASE64_BYTES = 200 * 1024 * 1024`, `_LONG_READ_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)`, `_map_semaphores: dict[int, asyncio.Semaphore]`, `_semaphore_for(...)` helper
  - `nextbot/plugins/server_tools.py:227-298` — `handle_map_image` per-server semaphore + size cap + early `del`
  - `nextbot/plugins/server_tools.py:97-120` — `_safe_wld_name` / `_safe_display_file_name` whitelist for backend-returned filename
- **TShock client** (`nextbot/tshock_api.py:48-92`):
  - `request_server_api(server, path, params=, *, timeout=5.0, include_token=True)`
  - Already does `quote(request_path, safe="/")` — defense in depth for path injection (line 58)
  - Accepts `httpx.Timeout` for full per-dimension control (line 65)
  - Default 5 s `read` is the only thing changed when caller passes `timeout=30.0` (line 68-73)
- **Temp file lifecycle** (`nextbot/screenshot_temp.py:13-32`):
  - Path is `Path("/tmp") / f"{prefix}-{beijing_filename_timestamp()}{suffix}"`
  - `beijing_filename_timestamp` is `%Y%m%d%H%M%S` — **second-resolution only** (`nextbot/time_utils.py:9`, `:47-48`)
  - Two concurrent renders started in the same second with the same `prefix` produce **the same path**
- **Permission default** (`nextbot/db.py:34-95`): `player_query.map.self`, `player_query.map.user`, `player_query.map.explored`, `player_query.progress` are all in `DEFAULT_GUEST_PERMISSIONS` — guest-callable

---

## Section 1 — `handle_my_map` (我的地图, lines 606-691)

Self-rendered map. Hits `/nextbot/users/{user.name}/map-image` → server returns ready-made PNG base64 → bot writes the bytes to a temp file *and* sends the same base64 inline via OneBot V11 `MessageSegment.image(file=f"base64://{b64_string}")`. There is no html-page → screenshot pipeline (the comment at line 617 confirms).

### PQB-1.1 🔴 critical — Large base64 OOM (no semaphore, no size cap, no early `del`)

- **File**: `nextbot/plugins/player_query.py:646-689`
- **Current code**:
  ```python
  response = await request_server_api(
      server,
      f"/nextbot/users/{user.name}/map-image",
      timeout=30.0,
  )
  ...
  b64_string = str(response.payload.get("base64") or "").strip()
  ...
  png_bytes = base64.b64decode(b64_string, validate=True)   # 2nd full copy in memory
  async with temp_screenshot_path(...) as screenshot_path:
      screenshot_path.write_bytes(png_bytes)                # 3rd copy via write
      ...
      await bot.send(event, at + OBV11MessageSegment.image(file=f"base64://{b64_string}"))
  ```
- **Impact**: Same OOM class as the now-fixed `handle_map_image` (ST-2.1/2.3) and `handle_download_map` (ST-3.3). For a Large/2x world, the server returns 30–80 MB of base64 (decoded ~25–60 MB). At the moment of `bot.send`, three copies coexist in memory: `b64_string`, `png_bytes`, and the constructed `MessageSegment` payload. With `N` concurrent callers on the same server, that becomes `N × ~120-240 MB`. No per-server semaphore (vs. `server_tools._map_semaphores`), no `_MAX_BASE64_BYTES` length check, no `del b64_string` / `payload.pop("base64", None)` after the message segment is built.
- **Reproduction**:
  1. Take a Large world that has been mostly explored on a single TShock server.
  2. From three different QQ accounts simultaneously send `/我的地图 1`.
  3. Watch RSS spike to ~`3 × (60 MB base64 + 45 MB png_bytes + duplicated segment payload)` ≈ 500 MB during the send.
  4. With more concurrent users or larger worlds, the bot OOMs.
- **Recommended fix**: Mirror `server_tools.handle_map_image` exactly:
  ```python
  # module level
  _MAX_BASE64_BYTES = 200 * 1024 * 1024
  _LONG_READ_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
  _my_map_semaphores: dict[int, asyncio.Semaphore] = {}
  _user_map_semaphores: dict[int, asyncio.Semaphore] = {}
  _explored_map_semaphores: dict[int, asyncio.Semaphore] = {}
  ```
  - Wrap the request + send inside `async with _semaphore_for(_my_map_semaphores, server.id):`
  - After `b64_string = ...` add `if len(b64_string) > _MAX_BASE64_BYTES: reply_failure(...)` and log it.
  - Skip the intermediate `png_bytes = base64.b64decode(b64_string, validate=True)` + `screenshot_path.write_bytes(png_bytes)` for the V11 success path (the server already validated the base64; we're holding 2 redundant decoded copies). Either:
    - V11 path: send `f"base64://{b64_string}"` directly, then `del b64_string`, `response.payload.pop("base64", None)`. No file at all.
    - non-V11 path: keep the file write, but `del png_bytes` and `del b64_string` immediately after the message segment is built.
  - Switch `timeout=30.0` to `timeout=_LONG_READ_TIMEOUT` (large worlds can take > 30 s on the server side; ST-2.2 used 300 s).

### PQB-1.2 🟠 high — Temp filename collision under same-second concurrency

- **File**: `nextbot/plugins/player_query.py:671-678` and `nextbot/screenshot_temp.py:26`
- **Current code**:
  ```python
  async with temp_screenshot_path(f"map-{server.id}-{user.user_id}") as screenshot_path:
      screenshot_path.write_bytes(png_bytes)
  ```
  Path = `/tmp/map-{server_id}-{user_id}-{YYYYmmddHHMMSS}.png`.
- **Impact**: Timestamp resolution is **seconds**, not milliseconds (`_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"`). Two paths to collision:
  1. Same user retries 我的地图 within 1 s after the first one returns (UI duplicate-tap, network retry on the OneBot side, double-CommandArg dispatch). Both contexts compute the same path. The `__aexit__` of the first does `path.unlink(missing_ok=True)`, deleting the file the second context just wrote — `bot.send` may upload a 0-byte / nonexistent file. Even worse, on V11 the inline `f"base64://{b64_string}"` ignores the file, but `screenshot_path.write_bytes` could be racing the unlink on a slower filesystem. It's currently masked because V11 sends inline base64, but the non-V11 fallback (`await bot.send(event, f"✅ 地图生成成功，文件：{screenshot_path}")`) hands the user a path that may already be unlinked by the other invocation.
  2. With the proposed PQB-1.1 fix, if we keep writing files at all, the collision is the same.
- **Reproduction**:
  1. Modify `bot.adapter.get_name()` mocked to non-V11.
  2. Two `handle_my_map` calls dispatched 50 ms apart from the same user. Both observe identical `/tmp/map-1-12345-20260508184412.png`.
  3. Whichever exits first deletes the file the other still references; the second `bot.send` reports a path the user can't open.
- **Recommended fix**:
  - Either bump `_FILENAME_TIMESTAMP_FORMAT` to include `%f` (microseconds), or move to `tempfile.NamedTemporaryFile(prefix=..., suffix=...)` like `server_tools.handle_download_map` lines 413-419 — `mkstemp` style guarantees uniqueness.
  - Independent of that, the PQB-1.1 fix that drops the file write entirely on the V11 path eliminates this for the only currently exercised branch.

### PQB-1.3 🟡 medium — Player name in URL path is unsanitized

- **File**: `nextbot/plugins/player_query.py:649`
- **Current code**: `f"/nextbot/users/{user.name}/map-image"`
- **Impact**: `user.name` comes from the bot DB (`User.name` ultimately from TShock-side registration), but it's interpolated into the path *before* `request_server_api`. The function `tshock_api.request_server_api` does call `quote(request_path, safe="/")` on line 58, so a `/`, ` `, or `?` in the name does get percent-encoded *post-fact*. However, a name containing `/` will still be path-segment-broken before `quote` sees it (the slash ends up in `safe="/"` and is left alone). Today `User.name` has a unique-lower index but no character whitelist, so a TShock account named `admin/../../v3/server/rawcmd?cmd=/help` would route to a different endpoint. This is parallel to the `_safe_wld_name` whitelist `server_tools.py:97-109` adopted for backend-returned strings.
- **Reproduction**: Register a TShock account whose nickname contains `/v3/server/rawcmd?cmd=`, sync into bot. `/我的地图 1` will dispatch to TShock's `rawcmd` endpoint with attacker-controlled `cmd=` because `quote(safe="/")` keeps the `/` literal.
- **Recommended fix**: Either whitelist `User.name` at registration (no path/control characters), or in `request_server_api` change `safe="/"` to `safe=""` so any `/` inside an interpolated segment gets escaped. Adding a per-handler `urllib.parse.quote(user.name, safe="")` before f-string interpolation is the cheap defensive option.

### PQB-1.4 🟡 medium — `int(user_id)` raises on non-numeric IDs (non-QQ adapters)

- **File**: `nextbot/plugins/player_query.py:687`
- **Current code**: `at = OBV11MessageSegment.at(int(user_id))`
- **Impact**: `event.get_user_id()` returns `str` per nonebot adapter API. For OneBot V11 + QQ this is always digits, but the `bot.adapter.get_name() == "OneBot V11"` branch is checked *afterwards*. If a non-QQ V11-shaped adapter ever sends a non-digit user_id, `int(...)` raises `ValueError` and crashes the handler with a Python traceback (instead of a polite reply). Same pattern in `handle_user_map:797` and `handle_explored_map:880`.
- **Reproduction**: Deploy on a OneBot V12 / Discord adapter where the canonical user_id is a UUID. `/我的地图 1` → `ValueError: invalid literal for int()`.
- **Recommended fix**: Wrap `int(user_id)` in `try/except ValueError`, or check digit-only with `if user_id.isdigit():` and skip the `at` segment otherwise (just send the image without `@`).

### PQB-1.5 🟢 low — `screenshot_path` text leak (non-V11 fallback)

- **File**: `nextbot/plugins/player_query.py:691`
- **Current code**: `await bot.send(event, f"✅ 地图生成成功，文件：{screenshot_path}")`
- **Impact**: The `screenshot_path` is `/tmp/map-{server_id}-{user_id}-{ts}.png`. Same shape ST-3.6/3.7 already cleaned up for `handle_download_map`'s non-V11 fallback (`server_tools.py:421-431`) — they switched to `reply_block` showing only filename + size. Here we still leak the full `/tmp/...` path which:
  - includes `user_id` (could be a QQ number)
  - is a real path on the bot host (info disclosure to whichever non-V11 client the user is on)
- **Reproduction**: Deploy on a non-V11 adapter, run 我的地图. Bot sends back a `/tmp/...` absolute path containing the operator's QQ.
- **Recommended fix**: Mirror ST-3.6 — use `reply_block(reply_success("查询"), [f"📁 文件：{screenshot_path.name}"])`. Also applies symmetrically to PQB-2.5 / PQB-3.5 / PQB-4.5.

### PQB-1.6 ℹ️ info — `screenshot_path.write_bytes(png_bytes)` is wasted I/O on V11

- **File**: `nextbot/plugins/player_query.py:671-689`
- **Current code**: We always `b64decode → write_bytes → ...` regardless of adapter, then on V11 we send `f"base64://{b64_string}"` (the original base64, *not* the file).
- **Impact**: For every V11 call (the only branch in production), the bot does an unnecessary 30-80 MB decode + disk write that is read by nobody and unlinked by `temp_screenshot_path.__aexit__`. Pure waste of CPU + tmpfs IOPS + the 2nd in-memory copy fueling PQB-1.1.
- **Recommended fix**: Move the file write *into* the non-V11 branch only. The V11 branch uses `b64_string` directly.

---

## Section 2 — `handle_user_map` (用户地图, lines 694-801)

Same shape as 我的地图 but for a third-party target user, and the prompt mentions a "coin transfer context (actor_user_id)". Reading the implementation (lines 704-801), **there is no coin transfer happening in this command**. The "actor_user_id" comment in the prompt seems to refer to the audit dimension list, not the code. The `requester_user_id = event.get_user_id()` is *only* used to `@` the requester (line 797) and to log (line 750-753). No `User.coins` read, no UPDATE, no withdrawal. Confirming: zero matches for `coins` / `transfer` / `economy` in lines 700-801.

So PQB-2 is mostly the same set as PQB-1, plus differences for the target-user lookup and the @ semantics.

### PQB-2.1 🔴 critical — Same large base64 OOM (no semaphore, no cap, no early del)

- **File**: `nextbot/plugins/player_query.py:755-799`
- **Current code**: identical pattern to PQB-1.1, calling `/nextbot/users/{target_user.name}/map-image`, then decode → write_bytes → send.
- **Impact**: Worse than PQB-1.1 because there's no implicit per-user single-flight: any 10 group members can request `/用户地图 1 admin` simultaneously and pile up 10 × 30-80 MB of one admin's map. Per-server semaphore + 200 MB cap + early `del` are the same fix.
- **Reproduction**: 5 group members type `/用户地图 1 <whoever>` within 1 s. RSS shoots up by ~5× world size.
- **Recommended fix**: identical to PQB-1.1. Re-use `_user_map_semaphores: dict[int, asyncio.Semaphore]` (per-server, not per-target-user — a single hot target shouldn't be parallel-rendered N times either; per-server is enough and matches ST-2.1).

### PQB-2.2 🟠 high — Target player name from user input flows into URL path unsanitized

- **File**: `nextbot/plugins/player_query.py:715-758`
- **Current code**:
  ```python
  target_user_id, parse_error = resolve_user_id_arg_with_fallback(...)  # resolves @/QQ/name → user_id
  target_user = session.query(User).filter(User.user_id == target_user_id).first()
  ...
  await request_server_api(server, f"/nextbot/users/{target_user.name}/map-image", timeout=30.0)
  ```
- **Impact**: Same path-injection class as PQB-1.3 but more directly user-influenced. The lookup goes `user input → User.name (DB) → URL path`. While `request_server_api` percent-encodes everything except `/`, a malicious `User.name` that contains `/` is unmodified, e.g. resolved name `foo/../../v3/server/rawcmd?cmd=` would produce path `/nextbot/users/foo/../../v3/server/rawcmd?cmd=/map-image` post-quote — TShock then routes this to wherever its router resolves the dot-segments. (TShock's HTTP router collapses dot segments? See unmodeled risk: depends on TShock; we should treat this as defense-in-depth.)
- **Reproduction**: Plant a `User` row with `name='foo/../../v3/server/status'` (only possible if there's no validation at registration; check `nextbot/plugins/user.py`'s register flow). `/用户地图 1 foo/../../v3/server/status` resolves the row, builds the malformed URL.
- **Recommended fix**: 
  - Apply `quote(target_user.name, safe="")` before f-string.
  - Or better: fix `request_server_api`'s `safe="/"` → `safe=""` so all single segments are encoded and only the literal slashes the caller put in the path remain.
  - Add a registration-time whitelist on `User.name` (e.g. only Terraria's allowed character set, no `/`).

### PQB-2.3 🟠 high — Same temp filename collision (cross-user this time)

- **File**: `nextbot/plugins/player_query.py:780-781`
- **Current code**:
  ```python
  async with temp_screenshot_path(f"map-{server.id}-{target_user.user_id}") as screenshot_path:
  ```
- **Impact**: 
  - Two requesters asking for the *same* target user's map within the same second collide: prefix is keyed on `target_user.user_id`, not `requester_user_id`. (The 我的地图 prefix uses `user.user_id` which is also the requester, so PQB-1.2 was self-collision; here it's *cross-requester* collision — much easier to trigger.)
  - Plus the cross-handler collision: 我的地图 and 用户地图 share prefix `map-{server.id}-{user_id}`. If user X runs 我的地图 and user Y runs 用户地图 targeting X within the same second, they collide.
- **Reproduction**:
  1. User X opens chat, types `/我的地图 1`.
  2. User Y simultaneously types `/用户地图 1 X`.
  3. Both produce `/tmp/map-1-{X.user_id}-20260508184412.png`. First `__aexit__` deletes; second message references a missing file.
- **Recommended fix**: 
  - Include the requester in the prefix: `f"user-map-{server.id}-{target_user.user_id}-by-{requester_user_id}"` for 用户地图; rename 我的地图 to `f"my-map-{server.id}-{user_id}"` to avoid the cross-handler clash.
  - Or, switch to `tempfile.NamedTemporaryFile` with random suffix.

### PQB-2.4 🟡 medium — `name_ambiguous` error message recommends behavior the code doesn't enforce

- **File**: `nextbot/plugins/player_query.py:726-727`
- **Current code**:
  ```python
  if parse_error == "name_ambiguous":
      await bot.send(event, reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户"))
  ```
- **Impact**: `db.ensure_user_name_unique_schema()` (`nextbot/db.py:758-784`) creates a **`LOWER(name)` unique index** at startup. So in steady state `name_ambiguous` should be unreachable — the DB rejects duplicate `name`. But if the unique-index creation logged a warning and fell back to a non-unique index (line 776-783 for legacy installs with duplicate names), then `name_ambiguous` is actually reachable, and the user sees that message. This is informational, not a bug — but the audit dimension "distinguishing API failure from no-data" applies here: this distinguishes `name not in DB` from `name has multiple matches`, which is correct.
- **Recommended fix**: Optional — log the ambiguity branch with the actual resolved candidates so an operator can decommission duplicates.

### PQB-2.5 🟢 low — Same `/tmp` path leak in non-V11 fallback (line 801)

Same as PQB-1.5. Use `reply_block(... [f"📁 文件：{screenshot_path.name}"])`.

### PQB-2.6 🟢 low — `int(requester_user_id)` raise on non-digit ID (line 797)

Same as PQB-1.4.

### PQB-2.7 🟡 medium — Race between target_user lookup and `request_server_api` (TOCTOU)

- **File**: `nextbot/plugins/player_query.py:737-758`
- **Current code**: 
  ```python
  target_user = session.query(User).filter(User.user_id == target_user_id).first()
  session.close()
  ...
  await request_server_api(server, f"/nextbot/users/{target_user.name}/map-image", timeout=30.0)
  ```
- **Impact**: If the target user is renamed (admin-side `/用户改名` or DB write) between the bot DB read and the TShock request, the URL still uses the stale `target_user.name`. TShock will return `404` / `not found` and `get_error_reason` produces "状态码 404" or "玩家不存在" — graceful, but the audit dimension "distinguishing HTTP failure from player not found" is partially satisfied. Same TOCTOU exists in 我的地图 (PQB-1) and 我的背包 / 用户背包 from batch A.
- **Reproduction**: rare in production (rename is uncommon), but possible.
- **Recommended fix**: low priority, but can be hardened by re-querying `User` inside the semaphore-guarded section, or by passing `target_user_id` to TShock instead of `name` if the API supports it.

---

## Section 3 — `handle_explored_map` (查看地图, lines 804-884)

World-wide explored region map. Returns base64 PNG directly from `/nextbot/world/explored-map-image` — no per-user request, no actor context. This is the closest analog to `server_tools.handle_map_image`.

### PQB-3.1 🔴 critical — Same large base64 OOM (no semaphore, no cap, no early del)

- **File**: `nextbot/plugins/player_query.py:840-882`
- **Current code**: identical pattern.
- **Impact**: Same OOM class. Plus: this is a *world*-wide map, often *larger* than per-user ones (the union of every player's explored region). On Large/2x worlds with many players, this pushes the upper bound of base64 payload. The fix template in `server_tools.handle_map_image:227-298` literally exists for this exact endpoint shape.
- **Reproduction**: Same as PQB-1.1, but image is even larger.
- **Recommended fix**: Identical to ST-2.1 / ST-2.3. Use `_explored_map_semaphores`, `_MAX_BASE64_BYTES = 200 * 1024 * 1024`, `_LONG_READ_TIMEOUT`, and `del b64_string` / `payload.pop("base64", None)` after the OneBot send.

### PQB-3.2 🟠 high — Permission key default-granted to guest users

- **File**: `nextbot/plugins/player_query.py:808` + `nextbot/db.py:72`
- **Current code**: 
  ```python
  permission="player_query.map.explored",
  ```
  And in `DEFAULT_GUEST_PERMISSIONS`: `"player_query.map.explored"` is included.
- **Impact**: Any guest QQ can call `/查看地图 1`. Combined with PQB-3.1's missing semaphore/cap, this is a denial-of-service via guest accounts: each call holds 60-80 MB until `bot.send` returns (and concurrent calls have no per-server serialization). Compare 全亮地图 / 下载地图 (`server_tools.map_image` / `download_map`) which are *not* in `DEFAULT_GUEST_PERMISSIONS` (verify in `nextbot/db.py:34-95` — they are not present, so they default to admin-only). 查看地图 is the only large-map endpoint reachable by guests.
- **Reproduction**: From a guest (default-permission) QQ account, fire 10 simultaneous `/查看地图 1` requests. Because every guest has the permission and there's no per-server semaphore, the bot OOMs.
- **Recommended fix**: Either remove `player_query.map.explored` from `DEFAULT_GUEST_PERMISSIONS` (treat it like 全亮地图), or — if the design wants guests to see explored maps — at minimum apply PQB-3.1's semaphore + size cap so guest spam can't OOM. The combination is currently unsafe; pick one mitigation.

### PQB-3.3 🟠 high — Same temp filename collision

- **File**: `nextbot/plugins/player_query.py:865-866`
- **Current code**: prefix `f"explored-map-{server.id}"` — only `server_id`, no requester. Two guests within the same second collide more easily than the per-user maps.
- **Recommended fix**: `f"explored-map-{server.id}-{requester_user_id}"` plus microseconds, or `tempfile.NamedTemporaryFile`.

### PQB-3.4 🟡 medium — `timeout=30.0` likely too short for global-explored renders

- **File**: `nextbot/plugins/player_query.py:840-845`
- **Current code**: `timeout=30.0`. Per `tshock_api.request_server_api:65-73`, this maps to `read=30.0`.
- **Impact**: ST-2.2 / ST-3.4 raised `read` to 300 s for full-world map / world file because Large worlds render in 60-120 s. The explored-map endpoint computes the union of all player visited tiles → comparable scale. 30 s will return `TShockRequestError` for big servers → user sees `"无法连接服务器"` which is misleading (server is fine, just slow).
- **Recommended fix**: `timeout=_LONG_READ_TIMEOUT`.

### PQB-3.5 🟢 low — `/tmp` path leak in non-V11 fallback (line 884)

Same as PQB-1.5.

### PQB-3.6 🟢 low — `int(requester_user_id)` raise on non-digit ID (line 880)

Same as PQB-1.4.

### PQB-3.7 ℹ️ info — Same wasted decode + disk write on V11 path

Same as PQB-1.6.

---

## Section 4 — `handle_world_progress` (进度, lines 887-973)

This is the only one of the four that actually *does* go through the html-page → screenshot pipeline. It calls `/nextbot/world/progress`, gets a flat dict of progression flags, builds a page via `create_progress_page`, and screenshots it via `screenshot_url`. No giant base64 in memory; image size is bounded by `PROGRESS_SCREENSHOT_OPTIONS = ScreenshotOptions(viewport_width=1200, viewport_height=700, full_page=True, fit_content_height=True)` — small, predictable.

### PQB-4.1 🟡 medium — Default 5 s `read` timeout for `/nextbot/world/progress`

- **File**: `nextbot/plugins/player_query.py:919-923`
- **Current code**: `await request_server_api(server, "/nextbot/world/progress")` — no `timeout=` passed → uses `tshock_api.request_server_api`'s default 5 s `read`.
- **Impact**: World progress reads global state from TShock; on busy servers with many plugins this can occasionally exceed 5 s. The other three handlers in this batch all pass `timeout=30.0`. Inconsistency, plus user-visible "无法连接服务器" on a healthy-but-loaded server.
- **Reproduction**: Server under heavy CPU load → `httpx.ReadTimeout` → `TShockRequestError` → reply_failure "无法连接服务器".
- **Recommended fix**: Pass `timeout=15.0` (matches ST-1.4's bumped exec timeout) or accept the default; either way, document it. Light fix.

### PQB-4.2 🟡 medium — Temp filename collision (concurrent same-second)

- **File**: `nextbot/plugins/player_query.py:952`
- **Current code**: `temp_screenshot_path(f"progress-{server.id}")` — prefix only includes `server_id`. Two callers in the same second produce the same path.
- **Impact**: Same race as PQB-1.2 / PQB-3.3. With `screenshot_url` taking ~1-3 s, collisions here are easier to trigger because the prefix has zero per-call entropy.
- **Reproduction**: 3 users type `/进度 1` within 1 s.
- **Recommended fix**: Prefix `f"progress-{server.id}-{user_id}"` (and bump timestamp resolution for completeness).

### PQB-4.3 🟢 low — `_to_base64_image_uri` reads the entire screenshot into memory

- **File**: `nextbot/plugins/player_query.py:125-128` and `:968`
- **Current code**:
  ```python
  def _to_base64_image_uri(path: Path) -> str:
      raw = path.read_bytes()
      encoded = base64.b64encode(raw).decode("ascii")
      return f"base64://{encoded}"
  ```
- **Impact**: For 进度 the image is small (1200×700 PNG ≈ 100-300 KB), so this is fine here. But the same function is also used for 我的背包 / 用户背包 (large-page screenshots) — bigger ones could double-buffer. Out of scope for this batch but worth noting cross-cuttingly.
- **Recommended fix**: Not blocking for 进度; revisit if any future caller produces > 5 MB screenshots.

### PQB-4.4 🟢 low — `progress` dict comprehension silently drops non-bool values

- **File**: `nextbot/plugins/player_query.py:932-936`
- **Current code**:
  ```python
  progress = {
      _PROGRESS_NAME_MAP.get(k, k): v
      for k, v in response.payload.items()
      if isinstance(v, bool)
  }
  if not progress:
      await bot.send(event, reply_failure("查询", "返回数据格式错误"))
      return
  ```
- **Impact**: The filter discards any non-bool values silently. If TShock changes the API to return strings or ints (e.g. `"defeated_eye_of_cthulhu": "true"`), the bot reports "返回数据格式错误" which is misleading — the response is correctly formatted, just not in the shape we expect. Low-priority observability gap.
- **Recommended fix**: Log the dropped keys/types so a TShock plugin upgrade is visible. Not a security issue.

### PQB-4.5 🟢 low — `/tmp` path leak in non-V11 fallback (implicit on line 973)

The 进度 handler doesn't have an explicit non-V11 fallback after sending — line 973 just `return`s without sending anything for non-V11. So: **non-V11 adapters get nothing back at all**. This is a behavior bug (silent success) but not a leak. Should be aligned with 我的地图 / 查看地图 patterns.

### PQB-4.6 🟢 low — Missing user_id in success log (line 963-965)

- **File**: `nextbot/plugins/player_query.py:963-965`
- **Current code**: `logger.info(f"世界进度截图成功：server_id={server.id} file={screenshot_path}")`
- **Impact**: Every other handler in this batch (我的地图:680, 用户地图:789, 查看地图:874) includes `user_id` (or `requester_user_id`) in the success log line. 进度's log line omits it — minor audit inconsistency.
- **Recommended fix**: Add `user_id={event.get_user_id()}`.

---

## Cross-cutting findings (apply to multiple commands in this batch)

### PQB-X.1 🔴 critical — Three of four large-image handlers lack the ST-2.1/3.3 fix template

| Handler | Per-server semaphore | 200 MB cap | Early `del` | Long-read timeout |
|---|---|---|---|---|
| `handle_my_map` (我的地图) | ❌ | ❌ | ❌ | only 30 s |
| `handle_user_map` (用户地图) | ❌ | ❌ | ❌ | only 30 s |
| `handle_explored_map` (查看地图) | ❌ | ❌ | ❌ | only 30 s |
| `handle_map_image` (全亮地图, ST-2) | ✅ | ✅ | ✅ | 300 s |
| `handle_download_map` (下载地图, ST-3) | ✅ | ✅ | ✅ | 300 s |

The three player_query map handlers are functionally equivalent to 全亮地图 from an OOM perspective but received none of the recent hardening. The shared module-level helpers (`_MAX_BASE64_BYTES`, `_LONG_READ_TIMEOUT`, `_semaphore_for`, dict pools) should either:
1. Be lifted to a shared helper module (e.g. `nextbot/large_image.py`) and imported by both `server_tools.py` and `player_query.py`, or
2. Be duplicated in `player_query.py` (faster, but invites drift).

Recommendation: extract once, mirror the helpers in batch B fix.

### PQB-X.2 🟠 high — Path-segment injection via interpolated user.name (PQB-1.3, PQB-2.2)

Both `handle_my_map` and `handle_user_map` interpolate `user.name` / `target_user.name` into URL paths without per-segment encoding. `request_server_api`'s `quote(safe="/")` is too lenient. Recommend tightening to `safe=""` globally.

### PQB-X.3 🟠 high — `temp_screenshot_path` is not collision-safe at second resolution (PQB-1.2, PQB-2.3, PQB-3.3, PQB-4.2)

Five distinct collision shapes across this batch alone. Best fix is at the source: change `beijing_filename_timestamp()` to include `%f` (microseconds) so all callers benefit, or have `temp_screenshot_path` mix in `os.urandom(4).hex()`.

### PQB-X.4 🟡 medium — `int(event.get_user_id())` is not adapter-portable (PQB-1.4, PQB-2.6, PQB-3.6)

Every map handler with an `@` segment does `OBV11MessageSegment.at(int(user_id))` *after* checking adapter name, but the cast happens unconditionally inside the V11 branch — which is fine, until somebody drops the adapter check or runs on a V11-shim adapter with non-numeric ids. Wrap the cast.

### PQB-X.5 🟡 medium — Default-guest permission for explored map enables OOM-by-guest (PQB-3.2)

The combination "guest can call" + "no semaphore" + "no size cap" on `查看地图` is the highest-impact single issue in this batch. Either yank the permission from `DEFAULT_GUEST_PERMISSIONS` (line 72 of `nextbot/db.py`) or land PQB-3.1's fix.

### PQB-X.6 🟢 low — `/tmp` path leak in non-V11 fallback messages (PQB-1.5, PQB-2.5, PQB-3.5)

Mirror ST-3.6/3.7's `reply_block` cleanup.

### PQB-X.7 ℹ️ info — V11 path always wastes a decode + disk write (PQB-1.6, PQB-3.7)

我的地图 / 用户地图 / 查看地图 all decode and disk-write the PNG even though the V11 send uses the original base64 string. Removing the decode + write from the V11 branch removes one of the three large-memory copies fueling the OOM.

---

## Summary table — issue count by command

| Command | 🔴 | 🟠 | 🟡 | 🟢 | ℹ️ | total |
|---|---|---|---|---|---|---|
| 我的地图 | 1 | 1 | 2 | 1 | 1 | 6 |
| 用户地图 | 1 | 2 | 2 | 2 | 0 | 7 |
| 查看地图 | 1 | 2 | 1 | 2 | 1 | 7 |
| 进度 | 0 | 0 | 2 | 4 | 0 | 6 |
| cross-cutting | 1 | 2 | 2 | 1 | 1 | 7 |

Highest priority: **PQB-X.1** (extract ST-2/3 helpers) + **PQB-3.2** (revoke guest permission OR land semaphore for 查看地图). Without either, a single guest can OOM the bot host.

---

## Caveats / Not Found

- The prompt mentioned `用户地图` carries "coin transfer context (actor_user_id)" and asked to cross-reference with the economy audit. I could not find any `User.coins` read or transfer in `handle_user_map` lines 704-801. The handler reads only the bot DB `User` row to obtain the target's `name`, then issues a single TShock request — no economy side-effect. Treating that prompt note as informational.
- I did not inspect `server/routes/render` for the actual map-rendering code (the prompt explicitly said routes are out of scope).
- The exact upper bound of explored-map base64 (PQB-3.1) on a Large 2x world is empirical; cited "30-80 MB / 60-100 MB" ranges are extrapolated from typical Terraria full-world map exports — a benchmark on the deployed servers would tighten the recommended `_MAX_BASE64_BYTES` if 200 MB ever proves insufficient.
- TShock's HTTP router behavior on path-segment dot-collapsing (relevant to PQB-2.2's reachability) was not verified end-to-end. Defense-in-depth recommendation stands regardless.
