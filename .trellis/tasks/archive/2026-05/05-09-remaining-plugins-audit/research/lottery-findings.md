# Lottery Plugin Audit Findings

- **Scope**: `nextbot/plugins/lottery.py` (733 lines), category `抽奖系统`
- **Date**: 2026-05-09
- **Reference patterns**: `plugins/shop.py` (S-1.1 / S-1.2 / S-2.1 / S-3.1), `plugins/economy.py` (F-2.1), `large_image.py`, `screenshot_temp.py`, `server_broadcast.py`

## Command Map

| Matcher | Command | Permission | category | Handler | Lines |
|---|---|---|---|---|---|
| `lottery_list_matcher` | `奖池列表 [页数]` | `lottery.list` | 抽奖系统 | `handle_lottery_list` | 155-252 |
| `lottery_view_matcher` | `查看奖池 <id/name> [页数]` | `lottery.view` | 抽奖系统 | `handle_lottery_view` | 257-382 |
| `lottery_draw_matcher` | `抽奖 <id/name> [次数]` | `lottery.draw` | 抽奖系统 | `handle_lottery_draw` | 387-733 |

All three have permission entries in `nextbot/db.py:35` `DEFAULT_GUEST_PERMISSIONS` (`lottery.draw`/`lottery.list`/`lottery.view`), so an unprivileged guest can draw — by design but worth noting.

Total handlers: 3. Heavy logic concentrates in `抽奖`.

---

## Severity Legend

🔴 critical · 🟠 high · 🟡 medium · 🟢 low · ℹ️ info

---

## `奖池列表` (`handle_lottery_list`, lines 155-252)

### LO-1.1 🟡 — N+1 count query for every active pool

**File:** `nextbot/plugins/lottery.py:191-211`

```python
pools = (
    session.query(LotteryPool)
    .filter(LotteryPool.enabled.is_(True))
    .order_by(LotteryPool.sort_order.asc(), LotteryPool.id.asc())
    .all()
)
all_entries: list[dict[str, object]] = []
for pool in pools:
    count = (
        session.query(LotteryPrize)
        .filter(LotteryPrize.pool_id == pool.id, LotteryPrize.enabled.is_(True))
        .count()
    )
```

Every enabled pool fires a separate `COUNT(*)`. Compare with `shop.py:233-252` which uses a `LEFT JOIN` subquery + `OFFSET/LIMIT` (S-3.2 fix).

**Impact:** With N pools, N+1 queries. Pagination is also fully in-memory: `all_entries[offset:offset+limit]` (line 225) — DB returns every row regardless of `limit`.

**Repro:** seed 200 enabled pools. `奖池列表` → 201 SQL round trips.

**Fix:** mirror shop S-3.2 — `LEFT JOIN` subquery counts, push `offset/limit` to SQL, `count()` on `LotteryPool` for total.

### LO-1.2 🟢 — No `try/except` around handler body

**File:** `nextbot/plugins/lottery.py:172-252`

`shop.py:192-301` wraps the entire handler in `try/except Exception` with `logger.exception(...)` + a generic `reply_failure("查询", "处理失败，请稍后重试")` so unexpected errors (e.g. screenshot service crash, OOM) surface as user-friendly text and a stacktrace in logs. `handle_lottery_list` has no such wrapper. Any uncaught exception just bubbles up the matcher pipeline → silent for user.

**Fix:** wrap with the shop pattern (`logger.exception(f"奖池列表处理异常：user_id={user_id}")`).

### LO-1.3 🟡 — Screenshot path has no `MAX_BASE64_BYTES` cap nor per-instance semaphore

**File:** `nextbot/plugins/lottery.py:236-252` (and identical pattern in `view`/`draw`)

```python
async with temp_screenshot_path("lottery-list") as screenshot_path:
    try:
        await screenshot_url(page_url, screenshot_path, options=LOTTERY_LIST_SCREENSHOT_OPTIONS)
    except RenderScreenshotError as exc: ...
    if bot.adapter.get_name() == "OneBot V11":
        try:
            image_uri = _to_base64_image_uri(screenshot_path)
```

`_to_base64_image_uri` (line 53) does `path.read_bytes()` + `base64.b64encode(...)` with **no size guard**. Compare `player_query.py:24` which imports `MAX_BASE64_BYTES as _MAX_BASE64_BYTES` from `large_image` and rejects payloads above 200 MB (e.g. `player_query.py:762`, `:911`).

Also no per-instance / per-pool semaphore — concurrent `奖池列表` from 50 users at once will all be encoding 5-10 MB PNGs into RAM simultaneously. `player_query.py:67-73` documents the pattern: separate `dict[int, asyncio.Semaphore]` per resource type.

**Impact:** OOM if a malicious / buggy template renders a multi-GB PNG, or if many concurrent screenshot requests pile up.

**Fix:** add module-level `_lottery_screenshot_semaphores: dict[str, asyncio.Semaphore]` (or single global sem since pool isn't per-server) wrap base64 encode with size check vs `MAX_BASE64_BYTES`. Same fix needed at `lottery.py:373-378` and `:715-728`.

---

## `查看奖池` (`handle_lottery_view`, lines 257-382)

### LO-2.1 🟢 — Pagination still in memory (mirror of LO-1.1)

**File:** `nextbot/plugins/lottery.py:316-353`

`_list_active_prizes` returns ALL active prizes; only after building `all_entries` does the code slice `[offset:offset+limit]`. For pools with a few thousand prizes this is wasteful (memory + CPU) and inconsistent with shop’s S-3.2 fix.

**Fix:** push `offset/limit` to `_list_active_prizes(session, pool_id, offset, limit)`, count separately.

### LO-2.2 🟡 — `Server.all()` per request fans into N+1-style label resolution

**File:** `nextbot/plugins/lottery.py:310-312`

```python
server_label_map: dict[int, str] = {
    int(s.id): str(s.name) for s in session.query(Server).all()
}
```

Loaded unconditionally even when the page contains zero `command` prizes. Cheap but worth a `if any(p.kind == "command" for p in prizes)` guard.

### LO-2.3 🟢 — No outer `try/except` (mirror of LO-1.2)

Same gap as `handle_lottery_list`.

### LO-2.4 ℹ️ — `command_template` exposed to viewer when `show_command=True`

**File:** `nextbot/plugins/lottery.py:340`

```python
entry["command_template"] = str(prize.command_template or "") if getattr(prize, "show_command", False) else ""
```

By design (admin opt-in), template appears in the rendered image. Frontend uses `textContent` (`lottery_view.html`) so no XSS, but if templates ever contain secret-bearing strings (`{api_token}`-style placeholders), they leak. Worth documenting that admins must NEVER put credentials into `command_template`.

---

## `抽奖` (`handle_lottery_draw`, lines 387-733)

This is the load-bearing handler. Multiple critical / high issues.

### LO-3.1 🔴 — Non-atomic `User.coins` mutation: lost-update + no `MAX_COINS_AMOUNT` cap

**File:** `nextbot/plugins/lottery.py:570-619` (item branch) and `:625-651` (no-item branch)

```python
user = session.query(User).filter(User.user_id == user_id).first()
...
current_coins = int(user.coins or 0)
if current_coins < total_cost:
    ...
user.coins = current_coins - total_cost          # read-modify-write
...
if coin_delta:
    user.coins = int(user.coins) + coin_delta    # second read-modify-write
session.commit()
```

This is the EXACT lost-update pattern that `economy.py` F-2.1 and `shop.py` S-1.1 / S-1.2 fixed by switching to a conditional `UPDATE ... WHERE coins >= total_cost`. The lottery handler reverted (or never adopted) the pattern.

**Impact:**
1. **Lost-update**: `抽奖` + `转账` (or `签到`/`抢红包`) running concurrently can wipe one of the two updates. Per-user `warehouse_lock` serializes ONLY against other warehouse-touching handlers, NOT against pure-coin handlers like `economy.transfer` / `economy.sign`.
2. **No upper cap**: `coin_delta` can be arbitrarily positive (admin sets `coin_amount=1_000_000_000` × draws), and `user.coins + coin_delta` can blast past `MAX_COINS_AMOUNT = 100_000_000` (defined `economy.py:39`). Compare `shop.py:551-557` which guards `total_price > MAX_COINS_AMOUNT`.
3. **Negative `coin_amount`** is documented in the model (`db.py:368` says `negative allowed = deduction`) — combined with non-atomic update this can drive `coins` negative without a `coins >= 0` guard.
4. **Re-read on line 638 (`user.coins = current_coins - total_cost`)** uses the snapshot from line 631; if a concurrent `转账` increased coins between query and commit, lottery silently overwrites the new balance.

**Repro:** user has 100 coins. Concurrently fire `抽奖 1` (cost 50) and `转账 someone 50`. Both read 100, both write — final balance 50 instead of 0; user effectively spent 100 but kept 50.

**Fix:** Replace both branches with `execute_rowcount(session, update(User).where(User.user_id==user_id, User.coins >= total_cost).values(coins=User.coins - total_cost))`. Apply coin prizes via separate `update(...).values(coins=User.coins + coin_delta)` after capping `coin_delta` against `MAX_COINS_AMOUNT - current_coins`. For negative `coin_amount`, add a `coins + coin_delta >= 0` guard or accept-and-clamp policy. Pattern reference: `shop.py:645-661` and `economy.py:192-202`.

### LO-3.2 🔴 — TOCTOU between dice roll and charging: pool/prize config can mutate mid-draw

**File:** `nextbot/plugins/lottery.py:432-489` (snapshot phase) → `:491-495` (roll) → `:558-619` (charge & inventory)

The handler snapshots prizes + pool inside the first session, closes it, rolls dice in Python, then reopens a session to charge. Between the snapshot and the second session:
- Admin disables the pool (`pool.enabled = False`) → user still gets charged at original `cost_per_draw`, prizes still issued.
- Admin changes `cost_per_draw` to a higher value via `web/lottery_admin` → user pays the OLD rate (stale snapshot).
- Admin disables / deletes a `LotteryPrize` between roll and grant → user receives a now-inactive prize.

`shop.py:617-630` reloads `ShopItem` + `Shop` inside the second session and rechecks `enabled` (S-3.1 TOCTOU fix). `lottery.py` does not.

**Impact:** medium-frequency exploitation: admin tries to "pull" a misconfigured prize, in-flight draws still get it; users rage-charged at wrong cost.

**Fix:** in the second-session block (line 559 onward), re-`session.query(LotteryPool).filter(...).first()` and verify `pool.enabled`. Re-fetch prize rows as needed for any item still in `bucket`. Reject with `reply_failure("抽奖", "奖池或奖品已变更，请刷新后重试")` if mismatch.

### LO-3.3 🟠 — Charging happens AFTER command-prize fan-out plan but BEFORE execution; failures don't refund

**File:** `nextbot/plugins/lottery.py:653-666`

```python
# Execute command prizes (after charging — failures don't refund)
cmd_results: list[dict[str, object]] = []
for pid, servers in cmd_plan:
    snap = prize_snapshots[pid]
    count = bucket[pid]
    cmd_text = snap["command_template"].replace("{player}", player_name)
    for srv in servers:
        for _ in range(count):
            ok, reason = await _issue_raw_command(srv, cmd_text)
```

Same shape as shop S-2.1 but without the safeguards:
1. **No CRITICAL log when ALL command-prize executions fail** (compare `shop.py:826-834` which logs `[CRITICAL] 商店指令购买全部失败但金币已扣` when `success_count == 0`). Lottery just silently embeds failure rows in `cmd_results` and renders them.
2. **No `head = reply_failure(...)` switch when all fail** — render shows "🎉 抽奖结果" header even when 100% of command prizes errored. Compare `shop.py:836-855`.
3. **Reply has no per-server failure breakdown channel separate from the screenshot** — if screenshot rendering itself fails (`RenderScreenshotError` at line 718), user sees only `reply_failure("抽奖", str(exc))` and never learns how their `cmd_skip_reasons` resolved. The `cmd_skip_reasons` follow-up message (line 729-730) only triggers on the OneBot success branch.

**Impact:** user pays N × cost, gets nothing playable, no clear reason in the chat reply, no audit trail to drive admin refund.

**Fix:** mirror shop S-2.1 / S-2.2:
- After loop, compute `cmd_success = sum(1 for r in cmd_results if r["ok"])` and `cmd_total = len(cmd_results)`.
- If `cmd_total > 0 and cmd_success == 0`: emit `logger.error(f"[CRITICAL] 抽奖指令奖品全部失败但金币已扣：user_id={user_id} pool_id={pool_id} ...")`.
- Always send a text fallback (in addition to image) summarizing `cmd_results` so render failures don’t hide them.

### LO-3.4 🟠 — Item warehouse insertions + coin charge are atomic under one session, but the partial-failure semantics for command prizes leave the local accounting inconsistent

**File:** `nextbot/plugins/lottery.py:597-619` vs. `:653-666`

Items + coins commit together (good). Command prizes execute via TShock fan-out AFTER commit, with NO compensating action on failure: failed command means the user paid for the prize, has nothing in inventory (commands don't go to warehouse), and `cmd_results` shows ❌. Per-server fan-out also runs serially (`for srv in servers: for _ in range(count)`).

**Impact:** unrecoverable state when a single prize is a multi-server command (say, `/spawn` on all 5 servers, 4 fail). User cannot retry without re-paying. No automated refund.

**Fix options (any one):**
(a) Reserve coin via conditional UPDATE, execute commands first, then commit charge only if success_threshold met (e.g., ≥1 server succeeded per prize) — adds complexity but prevents "paid for nothing".
(b) Keep current "fire and accept" semantics but persist a refund queue / `LotteryDrawRecord` row with `outcome="cmd_partial_failure"` so admin can see-and-refund.
(c) At minimum: log `logger.error` per failed (`prize_id`, `server_id`) and track a "potential-refund" CRITICAL marker like S-2.1.

### LO-3.5 🟠 — Command-prize fan-out is serial — no `server_broadcast.broadcast` helper

**File:** `nextbot/plugins/lottery.py:655-666`

```python
for pid, servers in cmd_plan:
    ...
    for srv in servers:
        for _ in range(count):
            ok, reason = await _issue_raw_command(srv, cmd_text)
```

Five servers × 10 draws of the same command = 50 sequential awaits. Each `_issue_raw_command` is a 5s default `httpx` GET with potentially long round-trip; total wall-time for `抽奖 1 10` against all-server prize can exceed 60s, blocking the matcher and risking nonebot timeout / user re-submission.

`server_broadcast.broadcast` (lines 39-69) was added to replace exactly this pattern; it gives parallel `asyncio.gather` with per-server semaphore (default 1). `shop.py` `_buy_command` shares the bug.

**Impact:** poor UX, potential matcher timeout, user smashes "抽奖" again → race with LO-3.1 / LO-3.2.

**Fix:** wrap with `broadcast(servers, fn=...)` and aggregate results.

### LO-3.6 🟠 — `target_server_id` graceful fallback exists but loses snapshot — re-load happens AFTER initial snapshot

**File:** `nextbot/plugins/lottery.py:519-555`

```python
# Re-load Server objects (need actual ORM instances)
session = get_session()
try:
    all_servers_orm = {int(s.id): s for s in session.query(Server).all()}
finally:
    session.close()
...
target_id = snap["target_server_id"]
if target_id is None:
    target_servers = list(all_servers_orm.values())
else:
    srv = all_servers_orm.get(target_id)
    if srv is None:
        cmd_skip_reasons.append(f"奖品「{snap['name']}」目标服务器已不存在")
```

Good: there IS a graceful fallback when `target_server_id` no longer resolves (consistent with SM-2.1 by-design renumber where Server.id can shift). Two issues:
1. The user is still **charged** for the missed prize (it falls into `cmd_skip_reasons`, not into a refund/credit). Same root cause as LO-3.4.
2. `cmd_skip_reasons` is only sent on the OneBot V11 branch (line 729-730). Other adapters (line 733 `await bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")`) silently lose the skipped-prize information.

**Fix:** decide policy — either compensate user for skipped prizes (refund/credit), or surface the skipped list in the screenshot itself; either way, send the warning on every adapter branch.

### LO-3.7 🟡 — `_check_player_online` cache is per-handler-call, not per-server SLA-aware

**File:** `nextbot/plugins/lottery.py:511-517`

```python
async def _check_online_cached(srv_id: int, srv_name: str, srv_obj: Server, player: str) -> bool:
    key = (srv_id, player)
    if key in server_online_cache:
        return server_online_cache[key]
    ok = await _check_player_online(srv_obj, player)
    server_online_cache[key] = ok
    return ok
```

The cache returns ONLY `bool`, swallowing the network-failure case. `shop.py:134-155` returns `(bool|None, str)` to distinguish "offline" from "query failed" — lottery treats them identically as "skip with vague reason". Compare `shop.py:744-765` which builds detailed per-server `offline_reasons`.

**Impact:** users see "需要玩家在线，但无在线服务器" when the actual cause is a TShock outage; admin gets no signal.

**Fix:** mirror shop's `(bool|None, str)` return, propagate the reason into `cmd_skip_reasons` distinguishing offline vs. RPC failure.

### LO-3.8 🟡 — `_resolve_probabilities` floating-point cumulative drift + `_draw_one` early-exit

**File:** `nextbot/plugins/lottery.py:75-106`

`_resolve_probabilities` clamps each weight to [0, 100] but `set_total = sum(...)` can overflow 100 (e.g., 5 prizes each at 30%). The remaining fallback then becomes 0 (correct), BUT `_draw_one` walks `cumulative += prob` — final cumulative ≤ 100 only if all weights actually sum ≤ 100. With over-100 totals, `roll = random.uniform(0.0, 100.0)` ALWAYS hits the first 1-2 prizes; later prizes never trigger.

Also: `random.uniform(0.0, 100.0)` is INCLUSIVE of both ends. If `cumulative == 100.0` and `roll == 100.0` exactly, `roll < cumulative` is False → falls through to miss. With float math it's near-zero probability but non-zero.

**Impact:** misconfigured pool (admin enters weight totals > 100) silently disables back-of-list prizes, no warning logged. Slight bias toward "miss" when totals == 100.

**Fix:** in `_resolve_probabilities`, after computing `set_total`, if `set_total > 100`: `logger.warning(...)` AND re-normalize each weight to `w * 100 / set_total`. Use `random.random() * 100.0` (half-open) or special-case the last prize as "fallthrough".

### LO-3.9 🟡 — `_find_empty_slots` is O(N × WAREHOUSE_CAPACITY) and snapshot-only

**File:** `nextbot/plugins/lottery.py:139-150`

Acquired BEFORE `warehouse_lock` (called once at line 561 inside the lock — that part is correct). But `_find_empty_slots` does `session.query(WarehouseItem).filter(user_id=user_id).all()` and builds an `occupied` set — fine for a few items, expensive if `WAREHOUSE_CAPACITY` is large or warehouse near-full + many concurrent draws. Same optimization shop uses (`_find_first_empty_slot`) — but shop only needs ONE slot per buy; lottery needs N (`needed_slots`). The current implementation is reasonable but loads ALL warehouse rows even if only the first 5 indices are needed.

**Impact:** low; cosmetic perf.

**Fix optional:** SQL `SELECT slot_index FROM warehouse_item WHERE user_id=? ORDER BY slot_index` and walk; or `SELECT MAX(slot_index)` early-exit.

### LO-3.10 🟡 — `command_template` `{player}` substitution does NOT URL-encode, but does NOT shell-escape either

**File:** `nextbot/plugins/lottery.py:658`

```python
cmd_text = snap["command_template"].replace("{player}", player_name)
```

`request_server_api` passes `cmd_text` via `params={"cmd": cmd_text}` (line 111) → httpx URL-encodes it → safe at the HTTP layer.

BUT `player_name` flows from `User.name` (DB-stored). If admin allows arbitrary usernames containing TShock command syntax (e.g., `"; /op evil_user"` or quotes), `command_template` like `/give {player} 100 1` becomes `/give "; /op evil_user" 100 1` — TShock's command parser splits on spaces / quotes and may treat the injected portion as a separate command. Whether TShock's `rawcmd` does shell-style quoting is implementation-dependent.

Compare `shop.py:813` — same pattern, same risk.

**Impact:** depends on TShock's command lexer. If `{player}` is wrapped by admin in the template (e.g. `"{player}"`), the embedded `"` breaks the wrapper. Worth a hardening pass: validate `User.name` against a `^[A-Za-z0-9_\-]{1,32}$` regex at registration / rename.

**Fix:**
(a) Validate at username registration that names match a safe charset (best fix).
(b) Or in handler, refuse to issue command if `player_name` contains `[ "';\n\r]`.
(c) Document for admins that `{player}` is unsafe to embed inside quoted arguments.

### LO-3.11 🟢 — Logged `internal_url` may leak short-lived token

**File:** `nextbot/plugins/lottery.py:231-234`, `:361-364`, `:708-713`

```python
logger.info(
    f"奖池列表渲染地址：page={page}/{total_pages} total={total} "
    f"item_count={len(render_entries)} internal_url={page_url}"
)
```

`create_lottery_*_page` returns a URL containing a token (`/render/lottery_list/{token}`). If logs are shipped to a less-trusted aggregator, anyone with read access to logs can fetch the rendered page within the token TTL. This is consistent with shop / player_query (so probably accepted), but worth a note.

**Fix optional:** log token prefix only (`page_url[:80] + '...'`).

### LO-3.12 🟢 — No audit-grade lottery draw record persisted

**File:** `nextbot/plugins/lottery.py` (entire handler)

The implementation logs a single `logger.info(f"抽奖结果渲染地址：...")` at line 708, but creates NO `LotteryDrawRecord` (or similar) DB row. If the user disputes a draw outcome ("I never got my legendary"), admin has only the log line — which omits the `outcome` (which prize hit) and per-prize success state.

`shop.py:693-697` and `:866-870` log structured info per purchase, which together with `WarehouseItem.created_at` is reconstructable. Lottery has nothing comparable on the inventory side because items go to warehouse via `WarehouseItem` (good — that's reconstructable) BUT command/coin outcomes leave NO trace beyond the log.

**Impact:** disputed transactions hard to resolve; no leaderboards / analytics on draw history.

**Fix:** Add `LotteryDrawRecord(id, user_id, pool_id, draw_count, total_cost, coin_delta, outcomes_json, cmd_results_json, created_at)` and persist in the same transaction as the charge.

### LO-3.13 ℹ️ — `unit_value` fallback uses pool `cost_per_draw` divided by `quantity`

**File:** `nextbot/plugins/lottery.py:590-596`

```python
if actual_value is not None:
    unit_value = max(0, int(actual_value))
else:
    per_pack = max(1, snap["quantity"])
    unit_value = snap["unit_price"] // per_pack if per_pack > 0 else 0
```

Compare `shop.py:638-641` which clamps `actual_value` against `MAX_COINS_AMOUNT` (S-Common.3 — defense against admin setting `actual_value=10**18` to bypass MAX_COINS during recycling). Lottery has no such cap.

**Impact:** admin sets `actual_value` ridiculously high → recycling that warehouse item later (`warehouse.recycle`) produces coins beyond `MAX_COINS_AMOUNT`. Bypasses the global coin invariant.

**Fix:** `unit_value = max(0, min(int(actual_value), MAX_COINS_AMOUNT))`.

### LO-3.14 🟡 — `single per-call` execution count not bounded; admin can craft an N×M-explosion prize

**File:** `nextbot/plugins/lottery.py:529-555` + `:655-666`

If admin defines a single command-prize with `target_server_id=None` (= "all servers"), and there are 10 servers, then a `count` of 10 (`bucket[pid]`) means **10 servers × 10 executions = 100 serial RPC calls** for ONE prize. With `max_draws=100` (param default) and a pool that always hits this prize, that's 1000 sequential RPCs.

`shop.py:62` defines `MAX_BUY_COUNT = 9999` and `_buy_command` serially issues `len(online_servers) * buy_count` commands, but at least `buy_count` is user-bounded by a hard cap. In lottery the analogous cap is `max_draws=100` (per `command_control.params.max_draws.max`), but the multiplier from "all-server" prizes is uncapped.

**Impact:** matcher latency, user re-tries → race with LO-3.1, TShock load.

**Fix:** add a `MAX_LOTTERY_CMD_EXECUTIONS = 200` (or similar), short-circuit with `reply_failure("抽奖", "本次抽奖产生的指令调用过多，请减少次数或联系管理员调整")`.

### LO-3.15 🟢 — `screenshot_temp` race already fixed (uuid suffix)

**File:** `nextbot/plugins/lottery.py:236, 366, 715`

All three call sites use `temp_screenshot_path("lottery-list")` / `temp_screenshot_path(f"lottery-view-{pool_id}")` / `temp_screenshot_path(f"lottery-result-{pool_id}")`. Per `nextbot/screenshot_temp.py:30-33`, the implementation appends `uuid4().hex[:8]` so concurrent requests with the same prefix don't collide. **Confirmed safe.**

### LO-3.16 ℹ️ — XSS surface in rendered pages

**File:** `server/templates/lottery_list.html`, `lottery_view.html`, `lottery_result.html`

All user/admin-controlled strings (`pool.name`, `prize.name`, `prize.description`, `user.name`) are rendered via `element.textContent = ...` in client JS (verified: `lottery_list.html:208, 232`, `lottery_result.html:469, 472, 474`). **No XSS exposure.** The JSON injection in `pages/lottery_view_page.py:108` uses `.replace("</", "<\\/")` to break out-of-script tag injection — also safe.

---

## Cross-Cutting Patterns Replicated From Already-Fixed Bugs

| Already-fixed pattern | Status in lottery.py | Issue ID |
|---|---|---|
| Conditional `UPDATE ... WHERE coins >= cost` (economy F-2.1, shop S-1.1/1.2) | ❌ NOT applied — read-modify-write | **LO-3.1** |
| `MAX_COINS_AMOUNT` cap on coin mutations | ❌ NOT applied to `coin_delta` or `actual_value` | **LO-3.1, LO-3.13** |
| Screenshot OOM defense (semaphore + `MAX_BASE64_BYTES`, `large_image.py`) | ❌ NOT applied | **LO-1.3** |
| TOCTOU re-validation in second session (shop S-3.1) | ❌ NOT applied to `LotteryPool` / `LotteryPrize` | **LO-3.2** |
| All-failed CRITICAL log + `reply_failure` head switch (shop S-2.1/2.2) | ❌ NOT applied | **LO-3.3** |
| Parallel fan-out via `server_broadcast.broadcast` | ❌ NOT applied (serial loop) | **LO-3.5** |
| `_check_player_online` returning `(bool|None, str)` | ❌ Older `bool`-only signature | **LO-3.7** |
| Username-injection hardening on `{player}` substitution | ❌ NOT applied (same bug as shop) | **LO-3.10** |
| `temp_screenshot_path` uuid suffix | ✅ Inherited via helper | LO-3.15 |
| Frontend XSS via `textContent` | ✅ Templates use `textContent` | LO-3.16 |
| `target_server_id` graceful fallback when server gone (SM-2.1) | ✅ Present (skip + reason) — but fails to refund | **LO-3.6** |
| Outer `try/except Exception` + `logger.exception` (shop pattern) | ❌ NOT applied to any of 3 handlers | LO-1.2, LO-2.3 |
| SQL-side pagination (shop S-3.2) | ❌ In-memory slicing | LO-1.1, LO-2.1 |
| Pool-config change race (`enabled` toggle, `cost_per_draw` change) | ❌ Stale snapshot used | LO-3.2 |
| Per-handler audit record persisted to DB | ❌ Only `logger.info` | LO-3.12 |

---

## Severity Summary

| Severity | Count | IDs |
|---|---|---|
| 🔴 critical | 2 | LO-3.1, LO-3.2 |
| 🟠 high | 4 | LO-3.3, LO-3.4, LO-3.5, LO-3.6 |
| 🟡 medium | 8 | LO-1.1, LO-1.3, LO-2.1, LO-2.2, LO-3.7, LO-3.8, LO-3.9, LO-3.10, LO-3.14 |
| 🟢 low | 4 | LO-1.2, LO-2.3, LO-3.11, LO-3.15 |
| ℹ️ info | 3 | LO-2.4, LO-3.13, LO-3.16 |

Top priorities for remediation: **LO-3.1** (lost-update + missing MAX_COINS cap, identical pattern to already-fixed economy/shop bugs), **LO-3.2** (TOCTOU on pool config), **LO-3.3** (no CRITICAL log on full command-fan-out failure — silent loss of user funds), **LO-1.3** (screenshot OOM defense missing across all 3 handlers).
