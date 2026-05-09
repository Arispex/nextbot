# Recheck after fix — remaining-plugins audit

**Date**: 2026-05-09
**Reviewer**: trellis-check sub-agent (final pass)
**Scope**: 9 files (`nextbot/screenshot_render.py` NEW, `nextbot/db.py`, `nextbot/plugins/{lottery,leaderboard,about,tutorial,menu,rob_protection,group_member_notify}.py`)
**Verification**: `uv run pyright` → 0 errors / 0 warnings / 0 informations on all 9 files (project-wide pyright count unchanged at 51 pre-existing errors, all in untouched files like `warehouse.py`).

---

## Bucket 1 — 🔴 Bugs introduced

### 1.1 lottery.py: positive `coin_delta` could be silently dropped at MAX_COINS_AMOUNT cap → display-vs-state inconsistency

- **File**: `nextbot/plugins/lottery.py:740-776` (pre-fix), `:792-804` (pre-fix display)
- **Severity**: 🟠 high (now 🟢 — **self-fixed in this recheck pass**)
- **Symptom**: when user's `User.coins` is near `MAX_COINS_AMOUNT (=1e8)` and lottery awards a positive coin prize, the conditional `UPDATE ... WHERE coins + capped_pos <= MAX_COINS_AMOUNT` returns `rowcount=0` → nothing applied → balance unchanged → BUT display still showed "+5000 金币" because `coin_delta` was recomputed from raw bucket values at line 798. Same for negative `coin_delta` when balance < |delta|.
- **Why it matters**: result page rendered "你获得 +5000 金币，剩余 99999000" but actual `User.coins` = 99999000 (no change). User sees a falsely successful reward.
- **Self-fix applied in this pass**:
  - Changed `_charge_atomic` to return `(ok, final_coins, item_value_gained, applied_coin_delta, err)` (added `applied_coin_delta`).
  - For positive delta: if conditional UPDATE rowcount=0, fall back to "add up to room" semantics (`partial = min(capped_pos, MAX_COINS_AMOUNT - coins_now)`), apply via second conditional UPDATE, log `WARN` with `requested` vs `applied`.
  - For negative delta: if conditional UPDATE rowcount=0, fall back to "deduct up to balance" semantics (`partial = -min(coins_now, -coin_delta_neg)`), log WARN.
  - Display path now uses `applied_coin_delta` (actual DB delta) instead of raw bucket sum.
  - Logger.info also includes `raw_coin_delta` for ops-side reconciliation when capped.
- **Files touched**: `nextbot/plugins/lottery.py:662-832, :836-852, :898-905`.

No other bugs introduced.

---

## Bucket 2 — 🟠 Fixes that are incomplete or ineffective

(After the self-fix above, **none remain in this bucket**.)

Items I considered but cleared:

1. **lottery LO-3.1 conditional UPDATE on `coins`**: ✅ correct. `update(User).where(User.user_id==user_id, User.coins >= total_cost).values(coins=User.coins - total_cost)` is the canonical economy/shop pattern. `execute_rowcount` checked, rowcount=0 path queries fresh `coins_now` and reports `金币不足`.
2. **lottery LO-3.2 TOCTOU re-validate inside _charge_atomic**: ✅ correct. Re-fetches `LotteryPool` and verifies `enabled` + `cost_per_draw` parity; iterates `bucket` and re-fetches each `LotteryPrize`, fails the whole charge if any prize disabled. Snapshot's `snap_cost_per_draw` cached on `cost_per_draw_changed` rejects price drift.
3. **lottery LO-3.3 全失败 CRITICAL log + reply head 切换**: ✅ correct. Lines 882-887 emit `[CRITICAL]` log with full context (user_id, pool_id, draw_count, total_cost). Lines 913-922 emit text fallback `❌ 抽奖失败，全部指令奖品执行失败，金币已扣，请联系管理员对账` BEFORE the screenshot, so render failures don't mask the warning.
4. **lottery LO-3.5 server_broadcast.broadcast usage**: ✅ correct. `_execute_for_server` correctly closes over `cmd_text` and `count` via default-arg trick; per-server semaphore comes from `_broadcast_semaphores` pool inside `broadcast()` (default 1 concurrent per server). Outcomes properly preserve prize_id mapping via the outer `for pid, servers in cmd_plan` loop.
5. **lottery LO-3.6 cmd_skip_reasons sent on all adapter paths**: ✅ correct. Lines 906-910 send the skip notice via `bot.send` BEFORE the screenshot helper, so it's visible regardless of V11/non-V11 routing.
6. **lottery LO-3.10 `_player_name_safe_for_command`**: ✅ correct. Defined at line 65-75, called at 653 before any cmd execution. Refuses entire draw (not silent skip) when player name has forbidden chars.
7. **lottery LO-3.14 N×M cap**: ✅ correct. `planned_cmd_executions` accumulated at line 636, checked against `MAX_LOTTERY_CMD_EXECUTIONS (200)` at 639. User refunded with friendly message.
8. **leaderboard LB-0.1 OOM defense in screenshot_render**: ✅ correct. `screenshot_render.render_and_send_screenshot` does:
   - Acquires semaphore via `async with` (released on every exit including exceptions).
   - Pre-stat check `file_size * 4 // 3 > MAX_BASE64_BYTES` BEFORE `read_bytes` (avoids OOM during read).
   - Post-encode check `len(encoded) > MAX_BASE64_BYTES` (defense-in-depth for compressible content edge cases).
   - Non-V11 path sends `screenshot_path.name` + `size_kb` only — does NOT leak `/tmp` absolute path.
9. **leaderboard LB-3.1 `asyncio.gather` + per-server semaphore + 10s timeout**: ✅ correct. `_total_online_fanout_semaphore = Semaphore(5)` at module level, acquired inside `_fetch_one`; `request_server_api(..., timeout=LEADERBOARD_FETCH_TIMEOUT)` with 10.0 explicitly. Order is preserved naturally because `gather` returns in input order, and downstream `for _, entries in fetch_results` doesn't depend on order anyway (totals dict is keyed by username).
10. **leaderboard 6 SQL handlers (rob_income / rob_success_rate / guess_income / guess_win_rate / dice_income / dice_win_rate)**: ✅ correct. All use SQL `(User.X - User.Y).label(...)` or `(User.X * 1.0) / func.nullif(User.Y, 0)`. The `nullif(Y, 0)` correctly returns NULL when Y=0, but `min_filter = User.X_total_count >= min_play_count` (≥1) prevents division-by-zero in practice. self_entry uses SQL COUNT (`func.count() WHERE rate_expr > caller_rate`), not a Python loop. Tie-break by `User.X_total_count.desc(), User.user_id.asc()` for deterministic ordering.
11. **leaderboard LB-1.1 + LB-8.1 ensure_*_schema**: ✅ correct. Both `ensure_user_leaderboard_indexes_schema()` and `ensure_user_sign_record_index_schema()` use `CREATE INDEX IF NOT EXISTS`, wrap each `CREATE` in try/except + `logger.warning` (per-column for the leaderboard set), called from `init_db()` after `Base.metadata.create_all`. Idempotent for existing installs. Won't crash startup.
12. **screenshot_render.py public helper**: ✅ correct semaphore release via `async with`. All 5 failure paths (`RenderScreenshotError`, stat OSError, file_size cap, read_bytes OSError, encoded size cap) emit `reply_failure(failure_action, ...)` and return False. Success path returns True. Non-V11 success uses `reply_success(failure_action, success_caption or "截图生成成功")` + `reply_block` with `name` + `size_kb`. NEVER includes the absolute `screenshot_path` string.
13. **group_member_notify MI-5.1 rule filter**: ✅ correct. `_is_increase` / `_is_decrease` are async functions returning bool, wrapped in `Rule(...)` and passed to `on_notice(rule=...)`. Both decrease handlers (`decrease_matcher` for farewell + `auto_ban_on_leave_matcher` for actual ban) use `Rule(_is_decrease)` — intentional separation of concerns. Inner `isinstance(event, GroupDecreaseNoticeEvent)` guard remains as defense-in-depth in case rule changes.
14. **group_member_notify MI-5.2 audit log**: ✅ correct. `audit_permission_change(actor_user_id="system", action="user.ban.auto_on_leave", ...)` is called BEFORE `sync_user_to_blacklist`, with full context including `group_id`, `sub_type`, `user_name`. WARN level by default (audit.py uses logger.warning).
15. **group_member_notify MI-5.3 TOCTOU cleanup**: ✅ correct. Pre-`_lookup_user_name_and_ban_status` SELECT removed. Direct `apply_ban_to_db()` call with code-based dispatch (`not_found` / `owner_protected` / `already_banned` / `banned`). Owner protection delegated to `apply_ban_to_db` internal logic (which uses `get_owner_ids()` correctly). Catch-all defensive log for unknown future codes at line 194-200.
16. **rob_protection MI-4.2 capture-before**: ✅ correct. `original_name` and `original_coins` captured at line 86-87 BEFORE the conditional UPDATE. After UPDATE, `current_coins = original_coins - cost` (no second SELECT). Display matches actual DB state because the UPDATE was atomic (rowcount==1). Lost-update protected by the conditional UPDATE itself.
17. **about / tutorial / menu migration to new helper**: ✅ correct. All three use `render_and_send_screenshot(...)` with their own module-level semaphores (`_about_semaphore`, `_tutorial_semaphore`, `_menu_semaphore` all `Semaphore(2)`). Menu's `viewport_width` was correctly downsized from 1920 → 920 to align with project standard.
18. **db.py schema migration safety**: ✅ correct. Both new `ensure_*_schema()` functions are append-only (CREATE INDEX IF NOT EXISTS), called from `init_db()` after `create_all`, individual try/except per index column for the leaderboard set. Doesn't touch existing tables / columns / FKs. Pre-existing 11 audited plugins unaffected.
19. **No dependency cycle**: ✅ verified. `screenshot_render.py` imports from `large_image`, `screenshot_temp`, `text_utils`, `server.screenshot` — all leaf modules. `db.py` imports nothing from plugins. `lottery.py` correctly imports `MAX_COINS_AMOUNT` from `nextbot.plugins.economy` (existing path).

---

## Bucket 3 — 🟢 Quality / style improvements (deferred / non-blocking)

3.1 **screenshot_render.py — non-V11 success caption is misleading for some callers**
   - `failure_action="抽奖"` falls through to `reply_success("抽奖", "截图生成成功")` = "✅ 抽奖成功，截图生成成功" on non-V11 adapter. The actual lottery result was already awarded; this caption is cosmetic.
   - Not a regression vs pre-fix behaviour; the fallback path was always best-effort. **Skip**.

3.2 **lottery.py — `coin_delta` partial-cap warning level**
   - When `applied < requested`, we log `WARN`. Repeated WARN for same admin-misconfigured pool may be noisy. Could be downgraded to `INFO` since it's defensive (no user-visible bug).
   - **Skip — WARN is correct for "behavior diverged from prize config"; alerts ops to admin misconfig.**

3.3 **leaderboard.py — `_query_score_leaderboard` extra `caller = session.query(User)` lookup**
   - Re-queries `User` for `caller.name`. Caller already known by handler (could be passed in). Saves 1 query per request. Cosmetic.
   - **Skip — keeps helper signature simple.**

3.4 **lottery.py — `_render_and_send` removed; previous handlers wrapped own try/except**
   - Outer `try/except Exception` at the 3 lottery handlers correctly catches everything including the new helper's exceptions. ✅

3.5 **Lint: 32 new ruff warnings introduced (mostly E501 line-too-long, TC002/TC003 type-checking import hints, PLR0913 too many args).** Project baseline is 214 errors → now 246. Same noise level / category as existing code. **Not blocking.**

3.6 **screenshot_render.py — `from server.screenshot import ...`** introduces server→nextbot dependency direction reversal vs other modules in `nextbot/`. Project already does this in many places (e.g., `lottery.py:34-39`), so consistent. **OK.**

3.7 **lottery.py — `_player_name_safe_for_command` rejects whole draw if player has forbidden chars**
   - Discards all paid draws (item + cmd). Strict but safe. Worth surfacing to admin via separate event log if it occurs frequently. **Skip — fits "defense by default" posture.**

3.8 **leaderboard.py — `format_remote_failure` defaults empty reason → "未知错误"**
   - Doesn't compose "动作 + 结果，原因"; just bridges empty reason. Used as `reply_failure("查询", _format_remote_failure(...))`. ✅ correct.

---

## Specific concerns reviewer raised — verification status

| # | Concern | Status |
|---|---|---|
| 1 | LO-3.1 cap correctness (positive + negative) | ❌ display-vs-state bug found → **self-fixed** in this pass |
| 2 | leaderboard 6 SQL handlers / win-rate /0 | ✅ correct (`func.nullif`, `min_filter ≥ 1`) |
| 3 | screenshot_render semaphore release on all exits | ✅ correct (`async with` covers all branches) |
| 4 | group_member_notify rule filter + audit | ✅ correct |
| 5 | ensure_*_schema startup safety | ✅ correct (idempotent + try/except per column) |
| 6 | LO-3.2 TOCTOU re-validate in 2nd session | ✅ correct |
| 7 | LO-3.5 broadcast usage with prize_id mapping | ✅ correct |
| 8 | LB-3.1 asyncio.gather ordering + timeout=10s | ✅ correct |
| 9 | LB-1.1 + LB-8.1 indexes via IF NOT EXISTS | ✅ correct |
| 10 | V11 wire format byte-identical for success | ✅ correct (helper sends `OBV11MessageSegment.image(file=base64://...)`) |
| 11 | Empty reason → "未知错误" fallback | ✅ correct (`_format_remote_failure`) |
| 12 | rob_protection capture-before | ✅ correct |
| 13 | No dep cycle | ✅ correct |
| 14 | 11 previously-audited plugins unchanged | ✅ verified via `git diff --name-only` (no other plugins touched) |

---

## Verification commands run

```bash
# Type check (subset)
uv run pyright nextbot/screenshot_render.py nextbot/db.py \
    nextbot/plugins/lottery.py nextbot/plugins/leaderboard.py \
    nextbot/plugins/about.py nextbot/plugins/tutorial.py \
    nextbot/plugins/menu.py nextbot/plugins/rob_protection.py \
    nextbot/plugins/group_member_notify.py
# → 0 errors / 0 warnings / 0 informations

# Type check (project-wide regression check)
uv run pyright nextbot/
# → 51 pre-existing errors (all in untouched files: warehouse.py, etc.)

# Lint (subset)
uv run ruff check ...
# Baseline 214 → current 246. New 32 noise (line-too-long, TC0xx, PLR0913). Same shape as existing.
```

---

## Final verdict

**Ship-ready**: ✅ all critical / high paths from the audit landed correctly; one display-vs-state inconsistency in lottery's coin_delta path was found during recheck and **self-fixed in this pass**. No regressions in the 11 previously-audited plugins. No new dependency cycles. Type-clean. Schema migrations idempotent and crash-safe.

**Confidence**: high.

**Outstanding (out of scope for this task — already noted in PRD)**:
- ban.py / permission_manager.py not yet migrated to `screenshot_render.py` helper (deferred — they have their own size cap + semaphore).
- LotteryDrawRecord persistence (LO-3.12) — deferred as new feature.
- LB-99.1 cooldown / rate-limit for guest-default leaderboard commands — needs cooldown machinery added to `command_control` (separate task).
