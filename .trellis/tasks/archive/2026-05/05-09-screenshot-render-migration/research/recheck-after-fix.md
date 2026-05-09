# Re-check: screenshot_render migration (9 sites, 7 files)

## Verdict

All 9 migration sites are correctly converted. No bugs introduced; no incomplete fixes; one minor cosmetic note. **Confidence: high.**

Pyright on the 6 non-warehouse/shop migrated files: 0 errors. shop.py / warehouse.py error counts unchanged before vs after the diff (pre-existing `at: object` typing issues are unrelated to migration). Ruff `F401` (unused imports): clean.

## Site-by-site verification

| File | Site | semaphore arg | failure_action | file_prefix | Status |
|---|---|---|---|---|---|
| ban.py:217 | `handle_ban_list` | `_ban_list_semaphore` (Semaphore(2)) | "查询" | `"ban-list"` | OK |
| permission_manager.py:660 | `handle_admin_list` | `_admin_list_semaphore` (Semaphore(2), newly added) | "查询" | `"admin-list"` | OK |
| shop.py:272 | `handle_shop_list` | `_shop_screenshot_semaphore` (Semaphore(2), shared) | "查询" | `"shop-list"` | OK |
| shop.py:429 | `handle_shop_view` | `_shop_screenshot_semaphore` (shared) | "查询" | `f"shop-{shop_id}"` | OK |
| red_packet.py:496 | `_send_red_packet_image` (called by list_own/list_all) | `_red_packet_screenshot_semaphore` (Semaphore(2)) | "查询" | caller-supplied (`red-packet-own` / `red-packet-all`) | OK |
| warehouse.py:283 | `_send_warehouse_image` (called by both list views) | `_warehouse_screenshot_semaphore` (Semaphore(2)) | "查询" | caller-supplied | OK |
| user_manager.py:323 | `_render_and_send_user_info` | `_user_info_screenshot_semaphore` (Semaphore(2)) | "查询" | `f"user-info-{user_id}"` | OK |
| player_query.py:496 | `handle_user_inventory` | **None** (outer `async with sem`) | "查询" | `f"inventory-{server.id}-{target_user.user_id}"` | OK |
| player_query.py:644 | `handle_my_inventory` | **None** (outer `async with sem`) | "查询" | `f"inventory-{server.id}-{user.user_id}"` | OK |
| player_query.py:1133 | `handle_world_progress` | `_progress_semaphores[server.id]` (newly added per-server pool, max=2) | "查询" | `f"progress-{server.id}"` | OK |

## Inventory re-entrant semaphore claim — VERIFIED

`asyncio.Semaphore` is **not** task-aware: if the SAME task did `async with sem` twice, Semaphore(2) would tolerate it, but Semaphore(1) would deadlock. The implementer's choice to pass `semaphore=None` to the helper is the correct defensive approach regardless of `max_concurrent` value.

helper handles `semaphore=None` correctly (no acquire):
```python
# nextbot/screenshot_render.py:70-75
if semaphore is None:
    return await _render_and_send_inner(
        bot, event, page_url=page_url, options=options,
        file_prefix=file_prefix, failure_action=failure_action,
        success_caption=success_caption,
    )
```

So the inventory path is: outer `async with sem` (per-server, max=2) → helper `_render_and_send_inner` directly without re-acquire. **Safe.**

## `_to_base64_image_uri` deletion — COMPLETE

`grep -rn _to_base64_image_uri nextbot/ server/` returns **zero matches**. All 7 helper duplicates removed (ban / permission_manager / shop / red_packet / warehouse / user_manager / player_query).

## Map handlers (out of scope) — INTACT

3 player_query map handlers (`handle_my_map`:730, `handle_user_map`:879, `handle_explored_map`) still construct `OBV11MessageSegment.image(file=f"base64://{b64_string}")` from API payload directly, never touched by migration. Their per-server semaphores (`_my_map_semaphores` / `_user_map_semaphores` / `_explored_map_semaphores`) and `_MAX_BASE64_BYTES` cap remain. `temp_screenshot_path` / `base64` / `binascii` imports are still required by these handlers (non-V11 fallback writes PNG to temp). All retained imports are justified.

## Import cleanup — COMPLETE

| File | Removed | Retained (still needed) |
|---|---|---|
| ban.py | `base64`, `Path`, `MAX_BASE64_BYTES`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `OBV11MessageSegment` (used by `at(...)` at lines 62/229) |
| permission_manager.py | `base64`, `MAX_BASE64_BYTES`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `OBV11MessageSegment` (`_at_segment` helper) |
| shop.py | `base64`, `Path`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `OBV11MessageSegment` (other handlers) |
| red_packet.py | `base64`, `Path`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `OBV11MessageSegment` (other handlers) |
| warehouse.py | `base64`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `Path` (used by `_DICTS_DIR` at line 73) |
| user_manager.py | `base64`, `temp_screenshot_path`, `RenderScreenshotError`, `screenshot_url` | `OBV11MessageSegment` |
| player_query.py | `Path`, `RenderScreenshotError`, `screenshot_url` | `base64` / `binascii` / `temp_screenshot_path` (3 map handlers) |

`asyncio` newly added to shop.py / warehouse.py / red_packet.py for the new `_*_screenshot_semaphore` module-level vars.

## Behavioral parity

- **V11 success**: helper output `OBV11MessageSegment.image(file=f"base64://{encoded}")` is byte-identical to the old per-file inline code. No regression.
- **Failure**: helper does `reply_failure(failure_action, str(exc))` — matches original handlers' `reply_failure("查询", f"{exc}")` / `reply_failure("查询", str(exc))`.
- **Non-V11 fallback**: now uses `reply_block(reply_success("查询", "截图生成成功"), [filename + size])`. Old code exposed `/tmp` paths; new code does not. PRD calls this "可接受改进". Confirmed.
- **base64 size cap**: helper enforces `file_size * 4 // 3 > MAX_BASE64_BYTES` (pre-encode estimate) AND `len(encoded) > MAX_BASE64_BYTES` (post-encode exact). ban / permission_manager dropped their own cap — fine, helper covers them.

## Findings buckets

### Bugs introduced
None.

### Fixes that are incomplete or ineffective
None.

### Quality improvements
1. The helper's non-V11 success message reads `"✅ 查询成功，截图生成成功\n📁 文件：xxx\n📦 大小：xx KB"` — the doubled "成功" (查询成功 + 截图生成成功) is slightly awkward in Chinese. **Not a bug**; cosmetic only. Possible improvement: pass `success_caption=None` to print just `"✅ 查询成功\n📁 ..."`, but `failure_action="查询"` doubles as the success verb here, which is a small semantic mismatch (e.g. for ban_list the action is "查询" — semantically correct; for `_render_and_send_user_info` the originally intended "用户信息查询" might be lost). Out of scope of this task per PRD ("修改 screenshot_render.py 签名" out-of-scope).
2. `_progress_semaphores` is newly introduced — small cleanliness concern: `_progress_semaphores` and `_inventory_semaphores` use `max_concurrent=2` so global Playwright concurrency for one server can reach 4 (background + progress). Probably fine and in line with prior behavior; no change needed.

## Lint / typecheck

- `ruff --select=F401` on all 7 changed files: **passed** (no unused imports introduced).
- `pyright` on ban / permission_manager / red_packet / user_manager / player_query / screenshot_render.py: **0 errors**.
- `pyright` on shop.py / warehouse.py: error count unchanged (18 / 33 respectively; all pre-existing `at: object` operator typing issues outside migration sites). Verified by `git stash` baseline comparison.

## Conclusion

Migration is correct, complete, and free of regressions. Ship it.
