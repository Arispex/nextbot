# Research: Player Query Audit (Batch A — 在线 / 自踢 / 用户背包 / 我的背包)

- **Query**: Audit first 4 of 8 commands in `nextbot/plugins/player_query.py` (lines 158-605) for concurrency, injection, perf, lifecycle, error propagation, sensitive data
- **Scope**: internal
- **Date**: 2026-05-08

## Summary Table

| ID | Severity | Area | Title |
|---|---|---|---|
| PQA-3.1 | 🔴 critical | concurrency / lifecycle | `temp_screenshot_path` second-precision filename collides on concurrent renders for the same target |
| PQA-3.2 | 🟠 high | OOM / perf | No per-server (or per-target) concurrency cap on inventory rendering — replicates pre-fix `server_tools.全亮地图` shape |
| PQA-3.3 | 🟠 high | sensitive data / leak | `send_link=True` posts the public render URL to chat with no auth on `/render/inventory/{token}` |
| PQA-4.1 | 🟠 high | concurrency / lifecycle | 我的背包 has the same temp-path collision (PQA-3.1) for the caller |
| PQA-4.2 | 🟠 high | OOM / perf | 我的背包 has the same per-target concurrency gap (PQA-3.2) |
| PQA-1.1 | 🟡 medium | perf / availability | 在线 queries servers sequentially; one slow server stalls the whole list |
| PQA-2.1 | 🟡 medium | perf / availability | 自踢 kicks servers sequentially with default 5s timeout; N×5s worst-case |
| PQA-3.4 | 🟡 medium | error propagation | inventory + stats are 2 separate requests; first-success then second-fail loses partial information cleanly but doubles the failure window |
| PQA-3.5 | 🟡 medium | TOCTOU | Server / user lookups, then 2 API calls, then screenshot — long window where target can rename / be deleted |
| PQA-3.6 | 🟢 low | defense-in-depth | `target_user.name` is interpolated into the API URL with no defensive sanitization (relies entirely on registration validator) |
| PQA-3.7 | 🟢 low | logging / leak | Public render URL is logged at INFO level even when `send_link=False` |
| PQA-CC-1 | 🟡 medium | cross-cutting | Same in-memory base64 / single-channel-render shape as server_tools.全亮地图 (now fixed); player_query missed the equivalent fix |
| PQA-CC-2 | 🟢 low | cross-cutting | Two separate base64 in-memory copies (file → bytes → b64) per render in the OneBot V11 path |
| PQA-CC-3 | ℹ️ info | cross-cutting | `_to_public_render_url` and `_to_base64_image_uri` are duplicated locally; could move to shared util |

## Findings

---

### 1. `在线` → `handle_online` (line 158-233)

#### PQA-1.1 🟡 Sequential per-server status query stalls on slow servers

- **File:line**: `nextbot/plugins/player_query.py:186-231`
- **Current code**:

```python
for i, server in enumerate(servers):
    ...
    try:
        response = await request_server_api(
            server,
            "/v2/server/status",
            params={"players": "true"},
        )
    except TShockRequestError:
        lines.append("❌ 查询失败，无法连接服务器")
        continue
```

- **Impact**: Each server uses default `request_server_api(timeout=5.0)` (read=5s, connect=5s). With N servers offline / unreachable, total wall time is roughly N × (connect_timeout + read_timeout) = N × ~10s. For 5 servers, that's ~50s — well over the typical bot reply budget. Users will think the bot is dead.
- **Reproduction**: Configure 3 servers with one IP firewalled. Run `在线`. Observe response taking ~30s.
- **Recommended fix**: Wrap the per-server probes in `asyncio.gather(*..., return_exceptions=True)` with bounded concurrency. Use `asyncio.gather` (or `asyncio.as_completed`) to fan out, preserving the original `Server.id` ordering when emitting `lines`. Same pattern already used in `nextbot/plugins/user_manager.py:130-134` (`_sync_whitelist_to_all_servers`). Example:

```python
results = await asyncio.gather(
    *(_query_one_server(s) for s in servers),
    return_exceptions=False,
)
for line in results:
    lines.extend(line)
```

#### PQA-1.2 ℹ️ Info — `playercount`/`maxplayers` type guards are correct

The handler validates `players: list`, `playercount: int`, `maxplayers: int` (lines 205-213) before formatting — good defensive coding. No issue.

---

### 2. `自踢` → `handle_self_kick` (line 236-292)

#### PQA-2.1 🟡 Sequential rawcmd kicks; default 5s timeout, no parallelism

- **File:line**: `nextbot/plugins/player_query.py:271-287`
- **Current code**:

```python
for server in servers:
    try:
        response = await request_server_api(
            server,
            "/v3/server/rawcmd",
            params={"cmd": f"/kick {user.name}"},
        )
    except TShockRequestError:
        lines.append(f"{server.id}.{server.name}：❌ 执行失败，无法连接服务器")
        continue
```

- **Impact**: Same shape as PQA-1.1. With several servers, one stalled connect or read causes the whole 自踢 to time out. Users typing 自踢 likely want to bail _quickly_ from a frozen game; multi-second delay defeats the purpose.
- **Reproduction**: Configure 3 servers with one unreachable. `自踢` takes ~10-15s before responding.
- **Recommended fix**: Run all kick calls concurrently via `asyncio.gather`. The rawcmd endpoint is idempotent for kick (kicking an already-offline player just returns "player not online"), so concurrency is safe.

#### PQA-2.2 🟢 Low — `/kick {user.name}` injection — currently safe but defense-in-depth gap

- **File:line**: `nextbot/plugins/player_query.py:276`
- **Current code**:

```python
params={"cmd": f"/kick {user.name}"}
```

- **Impact**: `user.name` originates from `_validate_user_name` (`nextbot/plugins/user_manager.py:53-63`) which enforces `[A-Za-z0-9一-鿿]+` only. So today `user.name` cannot contain whitespace, `;`, `&`, `\n`, or `/`. If validation is ever loosened, or if dirty data was introduced via direct DB insert, the `/kick` cmd string could become e.g. `/kick foo /ban bar` — TShock parses arg-by-arg so additional arguments after the player name are usually dropped, but at minimum the kick targets the wrong account. There is no second-layer defense in `request_server_api` because TShock takes the whole `cmd` query param verbatim.
- **Reproduction**: Insert a `User` row with `name = "foo /ban admin"` directly via SQL. Run `自踢`. The cmd becomes `/kick foo /ban admin`.
- **Recommended fix**: Either (a) re-validate `user.name` via `_validate_user_name` defensively before building the cmd, or (b) reject names containing whitespace at this call site. Cheap and aligns with the "defense in depth" pattern already applied in `tshock_api.request_server_api` (`quote(request_path)` at line 58).

#### PQA-2.3 ℹ️ Info — User-facing message uses `at + " " + reply_failure(...)`

Consistent with other plugins; the failure message correctly carries the original error reason from `get_error_reason(response)`. No issue.

---

### 3. `用户背包` → `handle_user_inventory` (line 295-455)

#### PQA-3.1 🔴 Critical — temp screenshot path collides on concurrent renders for the same target_user_id

- **File:line**: `nextbot/plugins/player_query.py:431-433` (use site) + `nextbot/screenshot_temp.py:26` (definition) + `nextbot/time_utils.py:9` (`%Y%m%d%H%M%S`)
- **Current code** (`screenshot_temp.py:26`):

```python
path = Path("/tmp") / f"{prefix}-{beijing_filename_timestamp()}{suffix}"
```

`beijing_filename_timestamp()` returns seconds-precision. Use site:

```python
async with temp_screenshot_path(
    f"inventory-{server.id}-{target_user.user_id}"
) as screenshot_path:
    ...
    await screenshot_url(page_url, screenshot_path, options=...)
    ...
    image_uri = _to_base64_image_uri(screenshot_path)
```

- **Impact**: Two concurrent invocations of 用户背包 (same `server_id`, same `target_user_id`) within the same wall-clock second produce identical paths, e.g. `/tmp/inventory-1-12345-20260508153012.png`. Race outcomes:
  1. Both `screenshot_url` calls write to the same file. Second writer's PNG overwrites the first — first request reads the second's bytes (wrong content for that user, but same target so probably not catastrophic).
  2. First request finishes its `_to_base64_image_uri` and exits the `async with`, which `unlink()`s the file via `temp_screenshot_path` cleanup (`screenshot_temp.py:30-32`). Second request, mid-read, sees `OSError` from `path.read_bytes()` → user-facing error "读取截图文件失败".
  3. Three or more concurrent calls multiply the windows above.

  Same prefix collision happens between 用户背包 (requester A asks for target X) and 我的背包 (X queries themselves), or two different requesters both asking for target X — they all converge on `inventory-{server_id}-{X.user_id}`.
- **Reproduction**:
  1. Group has two members A and B. Both run `用户背包 1 12345` simultaneously (same target QQ 12345).
  2. Or, single user runs `我的背包 1` twice within one second (e.g. via macro / bot relay).
  3. Observe: one of the two requests sometimes returns `读取截图文件失败` or returns a base64 of a partially-written PNG (browsers may reject the segment).
- **Recommended fix**: Add per-process uniqueness to the temp path. Two acceptable shapes:
  - **Option A (preferred)**: switch `temp_screenshot_path` to use `tempfile.NamedTemporaryFile(prefix=..., suffix=..., delete=False)` for atomic uniqueness, then unlink in the cleanup. Same pattern already used in `nextbot/plugins/server_tools.py:413-419`.
  - **Option B**: add a UUID suffix in `screenshot_temp.py`, e.g. `f"{prefix}-{beijing_filename_timestamp()}-{uuid.uuid4().hex[:8]}{suffix}"`. Backwards-compatible, single-line fix.

  Either fix is module-local in `screenshot_temp.py`, so all callers (用户背包 / 我的背包 / 进度 / 我的地图 / 用户地图 / 查看地图) inherit the fix without further changes.

#### PQA-3.2 🟠 High — No per-server / per-target concurrency cap, replicates pre-fix `全亮地图` OOM shape

- **File:line**: `nextbot/plugins/player_query.py:431-453`
- **Current code**: no semaphore. `screenshot.py` exposes a single shared headless Chromium with `await browser.new_context(...)` per call. Each context: viewport 2000×1000, full_page render, plus the page's HTML payload (the inventory page embeds 350 slots × per-slot sprite info).
- **Impact**: When 10+ users in a popular group run 用户背包 / 我的背包 simultaneously, you get 10+ concurrent BrowserContexts holding 2000×N PNG buffers. Pixel buffer alone is `2000 × content_height × 4 bytes` ≈ several MB per render, and Chromium retains it during full_page capture. Same OOM concern that motivated `_map_semaphores` in `server_tools.py:51-60` for `全亮地图`. PQA missed the same fix.

  Beyond OOM: each render reads the file then base64-encodes it (`_to_base64_image_uri`, line 125-128) — that's a second in-memory copy. With N concurrent calls you have `N × (PNG bytes + base64 bytes)` in Python heap, on top of the BrowserContext memory.
- **Reproduction**: Spam-trigger 用户背包 from 20 fake QQ accounts in a load test. Watch RSS climb sharply.
- **Recommended fix**: Add a per-server semaphore around the screenshot block, mirroring `server_tools.py:51-60`:

```python
_inventory_semaphores: dict[int, asyncio.Semaphore] = {}

def _semaphore_for_inventory(server_id: int) -> asyncio.Semaphore:
    sem = _inventory_semaphores.get(server_id)
    if sem is None:
        sem = asyncio.Semaphore(2)  # tune: 2-3 concurrent renders per server
        _inventory_semaphores[server_id] = sem
    return sem
```

Then wrap `request_server_api` + `screenshot_url` block in `async with sem:`. Concurrency cap of 2-3 is more permissive than 全亮地图 (`Semaphore(1)`) because inventory renders are smaller/faster. Tune from staging metrics.

#### PQA-3.3 🟠 High — `send_link=True` exposes auth-free `/render/inventory/{token}` URL to chat

- **File:line**: `nextbot/plugins/player_query.py:429-430`, plus `server/routes/render.py:52-54` (no auth on the render endpoint)
- **Current code**:

```python
if bool(get_current_param("send_link", False)):
    await bot.send(event, f"ℹ️ 用户背包链接：{public_page_url}")
```

`server/routes/render.py:52-54` exposes `GET /render/inventory/{token}` with no auth middleware (`add_webui_auth_middleware` covers `/webui/*` only — see `web_server.py:24,365`).

- **Impact**: When `send_link=True`, the bot posts the render URL to a (potentially large) QQ group. Anyone in the group has up to `PAGE_EXPIRE_SECONDS = 600` (`page_store.py:8`) to fetch the URL. The rendered HTML embeds the target user's `user_id` (i.e. raw QQ number — see `inventory_page.py:96`), `user_name`, full inventory, life, mana, deaths, online time, map exploration. For 用户背包 (cross-user query), this leaks the target's data to anyone in the group (including those without `player_query.inventory.user` permission, since the group view is unrestricted once they see the URL).

  Token is `uuid.uuid4().hex` (128 bits, infeasible to guess), so the leak is bounded to "people with link access". But QQ groups regularly contain 100+ members, including non-bot-permission ones, and link sharing outside the group is trivial. Effectively, granting `send_link=True` to anyone elevates the inventory.user permission to "anyone with chat read access".
- **Reproduction**: As a regular group member, ask the bot operator to run `用户背包 1 <victim QQ> --send_link=true`. Copy the link from chat. Open in browser → see the victim's complete inventory + stats. Repeat within 10 minutes.
- **Recommended fix**: Choose one or more:
  1. **Strongest**: gate `/render/inventory/{token}` behind a one-time-use token, or require an Authorization header proxy from `_to_public_render_url`. The current shared `_pages` dict already supports this.
  2. **Middle**: bind the token to the requester's QQ (require an `?qq=<requester_id>&sig=<HMAC>` query) and reject mismatches.
  3. **Cheapest**: drop `send_link` as a public-facing param and only allow it via admin-restricted permission. Right now any group member can pass `--send_link=true`.
  4. **Compensating**: shorten `PAGE_EXPIRE_SECONDS` (e.g. 60s) and/or single-use-on-render.

  Note: the token leakage applies to all 14 `create_*_page` consumers (warehouse, leaderboard, lottery, etc.), not just inventory. This is a system-wide architectural finding.

#### PQA-3.4 🟡 Medium — Two sequential API calls (`/inventory` + `/stats`); partial-failure window doubled

- **File:line**: `nextbot/plugins/player_query.py:371-405`
- **Current code**: `request_server_api(.../inventory)` followed by `request_server_api(.../stats)` — two HTTP round-trips, both with the default 5s timeout, both contributing to the user's wait time (~10s worst case before a "无法连接服务器" reply).
- **Impact**: User waits up to 10s before a connection-error reply when both endpoints are slow. Also two log lines + two paths through the failure-handling branches make it harder to debug. If `/inventory` succeeds but `/stats` returns an error, the inventory data fetched is discarded (see line 398-400) — this is correct from a data-consistency standpoint but represents wasted server work.
- **Recommended fix**: Run both fetches concurrently via `asyncio.gather`:

```python
inv_task = request_server_api(server, f"/nextbot/users/{target_user.name}/inventory")
stats_task = request_server_api(server, f"/nextbot/users/{target_user.name}/stats")
inv_response, info_response = await asyncio.gather(inv_task, stats_task, return_exceptions=True)
```

Then handle exceptions / non-success branches once. Halves wall time, simplifies error path. Same fix applies to 我的背包 (PQA-4.x).

#### PQA-3.5 🟡 Medium — TOCTOU window between user/server lookup and screenshot send

- **File:line**: `nextbot/plugins/player_query.py:357-454`
- **Current code**: `target_user = session.query(User).filter(...).first()` (line 360) → session.close() → 2 HTTP calls → screenshot render → bot.send. End-to-end: 1-30s. During this window:
  - Target user could be renamed via `更改用户名称` (`user_manager.py:442+`). The cached `target_user.name` becomes stale; the `/inventory` call may 404 or return a different player's inventory if the new owner of the old name is a different account.
  - Target user could be deleted (admin op via webui).
  - `Server` row could be deleted, but `server.id`, `server.ip`, etc. are already snapshotted into the local var, so this is benign.
- **Impact**: Race window allows showing "Alice's" inventory under "Bob's" rendered name banner if Alice was renamed to Bob mid-flight, or 404 errors that look unexplained.
- **Reproduction**: 1) User A sends `用户背包 1 Alice`. 2) Admin runs `更改用户名称 Alice Bob` immediately. 3) The bot may return "用户名称不存在" via TShock or render with stale `target_user.name`.
- **Recommended fix**: Read `target_user.user_id` and `target_user.name` early, and re-validate after the fetch (`session.query(User).get(target_user.id).name == target_user.name`) before rendering the page. Or simpler: accept the race as low-likelihood and add a comment documenting it. The risk is benign (bot replies with a rendered page that may show a slightly stale name), not a security issue.

#### PQA-3.6 🟢 Low — `target_user.name` interpolated into URL path with no defensive sanitization

- **File:line**: `nextbot/plugins/player_query.py:374,392`
- **Current code**:

```python
response = await request_server_api(
    server,
    f"/nextbot/users/{target_user.name}/inventory",
)
```

- **Impact**: `target_user.name` is constrained to `[A-Za-z0-9一-鿿]+` by `_validate_user_name` (no slashes, no `..`, no whitespace). Plus `request_server_api` does `quote(request_path, safe="/")` (`tshock_api.py:58`), but note that `safe="/"` means a literal `/` in the name would NOT be encoded — `quote` would leave it alone. So if the validation is ever loosened or bypassed, a name like `foo/admin/whitelist/add` could redirect the request to a different TShock endpoint.
- **Reproduction**: SQL `UPDATE user SET name='evil/inventory/../whitelist/add/attacker' WHERE user_id='12345'`. Run `用户背包 1 12345`. The URL becomes `/nextbot/users/evil/inventory/../whitelist/add/attacker/inventory`, and depending on TShock routing, this may hit an unintended endpoint.
- **Recommended fix**: Use `quote(target_user.name, safe="")` (no safe chars) when interpolating into the URL path, or call `_validate_user_name(target_user.name)` defensively at this call site. Cheap and aligns with `tshock_api.request_server_api`'s "defense in depth" comment.

#### PQA-3.7 🟢 Low — Public render URL logged at INFO level even when send_link=False

- **File:line**: `nextbot/plugins/player_query.py:424-428`
- **Current code**:

```python
public_page_url = _to_public_render_url(page_url)
logger.info(
    "用户背包渲染地址："
    f"server_id={server.id} target_user_id={target_user.user_id} "
    f"internal_url={page_url} public_url={public_page_url}"
)
```

- **Impact**: The render URL contains the page token, which (per PQA-3.3) lets anyone with log access fetch the rendered inventory for 600s. Operators viewing log streams or shipping logs to a third-party (Loki, ElasticSearch, S3) inadvertently expose user inventories.
- **Recommended fix**: Drop the URL from the log line; log only `server_id`, `target_user_id`, and `token=<token>` (or omit the token entirely). The URL adds no diagnostic value beyond what `server_id` + `target_user_id` already provide.

---

### 4. `我的背包` → `handle_my_inventory` (line 458-603)

#### PQA-4.1 🟠 High — Same temp-path collision as PQA-3.1

- **File:line**: `nextbot/plugins/player_query.py:579-581`
- **Current code**:

```python
async with temp_screenshot_path(
    f"inventory-{server.id}-{user.user_id}"
) as screenshot_path:
```

- **Impact**: Identical to PQA-3.1, but for the caller's own user_id. Same collisions:
  - User double-taps 我的背包 within one second (via slow phone, network retry, fat-finger, or QQ adapter retry).
  - Same user requested by 用户背包 from another requester at the same instant.
- **Reproduction / fix**: Same as PQA-3.1.

#### PQA-4.2 🟠 High — Same per-target concurrency gap as PQA-3.2

- **File:line**: `nextbot/plugins/player_query.py:579-602`
- **Current code**: same shape as 用户背包 — no semaphore around `screenshot_url`.
- **Impact / fix**: Same as PQA-3.2. The fix should cover both 用户背包 and 我的背包 using a single shared `_inventory_semaphores` dict.

#### PQA-4.3 🟢 Low — `send_link=True` in 我的背包 also leaks via auth-free render endpoint

- **File:line**: `nextbot/plugins/player_query.py:576-577`
- **Impact**: Same as PQA-3.3, but for the caller's own data. Lower severity because (a) the link contains the caller's own info (they already know it), (b) anyone with the link URL still gets it. The risk of "QQ group member B grabs the link of A's inventory" remains.
- **Recommended fix**: Same as PQA-3.3 (auth-gate `/render/inventory/{token}`).

#### PQA-4.4 🟡 Medium — Same TOCTOU + sequential-fetch issues as 用户背包

PQA-3.4 and PQA-3.5 apply identically to 我的背包. Recommend fixing both handlers in the same patch.

---

## Cross-cutting Findings

#### PQA-CC-1 🟡 Medium — Pattern not yet hardened in this file

`server_tools.py` was hardened with:
- per-server semaphore (`_map_semaphores`, `_download_semaphores`)
- `_MAX_BASE64_BYTES` hard cap
- `_LONG_READ_TIMEOUT`
- `_safe_wld_name` / `_safe_display_file_name` for filename injection

`player_query.py` (this batch) has none of these. While `player_query`'s render shape differs (Playwright screenshot, not TShock-supplied PNG), it shares the **memory-amplification** characteristics:
- per-render produces multi-MB PNG (PQA-3.2)
- per-render produces a base64 in-memory copy (PQA-CC-2)
- no per-server gate

Recommend introducing `_inventory_semaphores: dict[int, asyncio.Semaphore]` (mirror of `server_tools._map_semaphores`) and applying it to both 用户背包 and 我的背包.

#### PQA-CC-2 🟢 Low — Two memory copies per render in OneBot V11 path

- **File:line**: `_to_base64_image_uri` (`player_query.py:125-128`)
- **Current code**:

```python
def _to_base64_image_uri(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"base64://{encoded}"
```

After this call, `raw` (the PNG bytes) and `encoded` (the base64 string, ~1.33× larger) coexist briefly. Plus the file on disk. So peak per-render memory ≈ 2.33× PNG size during the conversion. For 5 MB PNG → ~12 MB transient. Concurrent renders multiply this.

`server_tools.py:286-298` mitigates by `del b64; response.payload.pop("base64", None)` immediately after sending. `_to_base64_image_uri` doesn't have an analogous structure, but the local `raw` and `encoded` go out of scope at function return — the issue is just that the calling site holds `image_uri` (which still contains the full base64) while `await bot.send(...)` happens.

- **Recommended fix**: Acceptable as-is for typical inventory PNGs (~hundreds of KB). Document in a comment that for screenshot outputs >1 MB, switch to direct file upload (file:// URI) instead of base64:// to avoid the 1.33× amplification.

#### PQA-CC-3 ℹ️ Info — `_to_public_render_url` and `_to_base64_image_uri` are local helpers

These two helpers are duplicated in player_query.py but not exported. Other plugins (warehouse, leaderboard, etc.) may need the same logic. Worth promoting to a shared module like `nextbot/render_helpers.py` if a future audit finds duplication.

#### PQA-CC-4 🟢 Low — `_to_public_render_url` returns internal URL when env var unset

- **File:line**: `nextbot/plugins/player_query.py:131-155`
- **Current code**: When `web_server_public_base_url` is empty, the function returns the internal `http://127.0.0.1:18081/...` URL unchanged.
- **Impact**: With `send_link=True`, the bot sends `http://127.0.0.1:18081/render/inventory/<token>` to a QQ group. The link is unreachable from outside the host but reveals the bot's internal port. Defense in depth — low impact.

  Note: `server_config._normalize_public_base_url` (`server_config.py:58-62`) already provides a fallback to `http://{host}:{port}` for `WebServerSettings.public_base_url`, but `player_query._to_public_render_url` reads `getattr(config, "web_server_public_base_url", "")` directly (bypassing `WebServerSettings`). They diverge. Recommend reading from `get_server_settings().public_base_url` instead.

## Caveats / Not Found

- I did not run any code; all findings are static analysis.
- Reproduction steps for race conditions assume concurrent message dispatch — actual race likelihood depends on QQ message rate-limiting and nonebot's matcher concurrency model. PQA-3.1's collision is achievable in practice with two users in different groups hitting the same target.
- Severity calibration follows the prior server_tools audit: temp-file collision under same-second concurrency is graded 🔴 because cleanup can unlink another in-flight render's source file (data integrity) — same severity used for similar issues in other audits.
- The auth gap in `/render/inventory/{token}` (PQA-3.3) is a system-wide architectural finding visible from this audit but not unique to player_query — it affects all `create_*_page` consumers. Recorded here because `send_link=True` is the most direct path to exploitation.
