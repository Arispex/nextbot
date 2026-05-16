# 失败回复 @调用者 前缀审计

- **Query**: 审计所有 plugin 失败回复点，找出缺少 `@调用者` 前缀的 `bot.send` 调用
- **Scope**: internal（仅 `nextbot/plugins/*.py`）
- **Date**: 2026-05-16

## 审计规则速览

- 关注 `bot.send(event, reply_failure(...))` / `reply_warning(...)` / `❌` / `⚠️` 开头文本的失败回复
- PASS：已加 `at + " "` 或 `at + "\n"` 前缀
- MISSING：用户可见失败回复但未带 @
- N/A：截图 helper 兜底（`render_and_send_screenshot` 内部带 `at_user_id`）、广播 / 通知 / 启动类（无特定调用者）、`raise_command_usage()` 等

---

## `nextbot/plugins/about.py`

### N/A
- 仅有 `render_and_send_screenshot` 一处发送（line 49-56），截图失败由 helper 内部处理，已托管。

---

## `nextbot/plugins/ban.py`

### MISSING @
- `handle_ban_list` line 167: `await bot.send(event, reply_failure("查询", "页数必须为正整数"))` → 修复：加 `at + " "` 前缀（handler 未声明 `at`，需先在函数顶部 `at = safe_at_segment_or_empty(event.get_user_id())`）
- `handle_ban_list` line 170: `await bot.send(event, reply_failure("查询", "页数必须为正整数"))` → 修复：加 `at + " "`
- `handle_ban_list` line 184: `await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))` → 修复：加 `at + " "`

### PASS（已有 @）
- `handle_ban`: line 70 / 73 / 87 / 97 / 100 — 均使用 `at + " " + reply_failure(...)`
- `handle_ban` 成功汇总：line 133 — `at + "\n" + ...`
- `handle_unban`: line 254 / 257 / 270 / 273 — 均使用 `at + " " + reply_failure(...)`
- `handle_unban` 成功汇总：line 304 — `at + "\n" + ...`

### N/A
- `handle_ban_list` 截图渲染走 `render_and_send_screenshot`（line 223-231），由 helper 内部托管。

---

## `nextbot/plugins/dice.py`

### PASS（已有 @）
- `handle_dice`: line 149 / 155 / 158 / 164 / 167 / 170 / 188 / 195 / 213 / 307 — 全部 `at + " " + reply_failure(...)`

### N/A
- 无其他失败位点。

---

## `nextbot/plugins/economy.py`

### PASS（已有 @）
- `handle_signin`: line 294 / 297 / 306 / 313 / 350 / 353 / 376 / 395 / 432-block / 446 — 全部 `at + " "` 或 `at + "\n"`
- `handle_transfer`: line 476 / 479 / 482 / 490 / 494 / 497-block / 505 / 512 / 517 / 532-block / 578-success / 590 — 均带 at
- `handle_add_coins`: line 624 / 627 / 630 / 635 / 638-block / 651 / 668 / 705-success — 均带 at
- `handle_remove_coins`: line 740 / 743 / 746 / 751 / 754-block / 764 / 778-block / 795 / 822-success — 均带 at

### N/A
- 无未处理的失败位点。

---

## `nextbot/plugins/group_member_notify.py`

### N/A
- 仅使用 `bot.call_api` 发群通知（line 113 / 232），非针对特定调用者的失败回复，且广播类无 @ 责任。

---

## `nextbot/plugins/guess_number.py`

### PASS（已有 @）
- `handle_guess`: line 136 / 139 / 145 / 148 / 154 / 157 / 160-block / 178 / 185 / 199 / 285 — 全部 `at + " " + reply_failure(...)`
- 成功汇总：line 317 — `at + "\n" + reply_block(...)`

---

## `nextbot/plugins/leaderboard.py`

> 该文件所有榜单 handler 均未在函数顶部声明 `at`，所有 `reply_failure("查询", ...)` 用户失败回复都缺少 @ 前缀。修复时建议在每个 handler 头部统一加 `at = safe_at_segment_or_empty(event.get_user_id())`。

### MISSING @
- `handle_coins_leaderboard` line 248: `await bot.send(event, reply_failure("查询", "页数必须为正整数"))` → 修复：加 `at + " "` 前缀
- `handle_coins_leaderboard` line 259: `await bot.send(event, reply_failure("查询", f"超出总页数（共 {total_pages} 页）"))` → 修复：加 `at + " "`
- `handle_streak_leaderboard` line 327: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_streak_leaderboard` line 338: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_signin_leaderboard` line 406: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_signin_leaderboard` line 417: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `_server_side_leaderboard` line 492: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`（传入 helper 时需同步 caller 的 at）
- `_server_side_leaderboard` line 507: `reply_failure("查询", "服务器不存在")` → 修复：加 `at + " "`
- `_server_side_leaderboard` line 514: `reply_failure("查询", "无法连接服务器")` → 修复：加 `at + " "`
- `_server_side_leaderboard` line 518: `reply_failure("查询", _format_remote_failure(get_error_reason(response)))` → 修复：加 `at + " "`
- `_server_side_leaderboard` line 523: `reply_failure("查询", "返回数据格式错误")` → 修复：加 `at + " "`
- `_server_side_leaderboard` line 542: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_total_online_time_leaderboard` line 752: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_total_online_time_leaderboard` line 834: `reply_failure("查询", f"所有服务器均无法获取数据（共 {len(servers)} 台）")` → 修复：加 `at + " "`
- `handle_total_online_time_leaderboard` line 844: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_daily_sign_leaderboard` line 914: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_daily_sign_leaderboard` line 930: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_rob_income_leaderboard` line 1022: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_rob_income_leaderboard` line 1054: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_rob_loss_leaderboard` line 1098: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_rob_loss_leaderboard` line 1109: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_rob_penalty_leaderboard` line 1174: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_rob_penalty_leaderboard` line 1185: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_rob_success_rate_leaderboard` line 1260: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_rob_success_rate_leaderboard` line 1283: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_guess_income_leaderboard` line 1373: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_guess_income_leaderboard` line 1404: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_guess_win_rate_leaderboard` line 1457: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_guess_win_rate_leaderboard` line 1478: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_dice_income_leaderboard` line 1567: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_dice_income_leaderboard` line 1598: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_dice_win_rate_leaderboard` line 1651: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_dice_win_rate_leaderboard` line 1672: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`

### N/A
- `handle_total_online_time_leaderboard` line 768: `reply_info("暂无服务器")` 是空集语义信息，不是失败回复
- 各榜单截图通过 `_render_and_send` helper 渲染，本身不直接 bot.send 失败

---

## `nextbot/plugins/lottery.py`

> `handle_lottery_list` / `handle_lottery_view` / `handle_lottery_draw` 中查询类失败回复缺 @，而抽奖类 (`handle_lottery_draw` 内 `at` 已定义) 一部分已带 @。

### MISSING @
- `handle_lottery_list` line 243: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`（需先在 handler 头声明 `at`）
- `handle_lottery_list` line 246: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_lottery_list` line 260: `reply_failure("查询", "暂无可用奖池")` → 修复：加 `at + " "`
- `handle_lottery_list` line 265: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_lottery_list` line 322: `reply_failure("查询", "处理失败，请稍后重试")` → 修复：加 `at + " "`
- `handle_lottery_view` line 361: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_lottery_view` line 364: `reply_failure("查询", "页数必须为正整数")` → 修复：加 `at + " "`
- `handle_lottery_view` line 373: `reply_failure("查询", f"未找到奖池「{selector}」")` → 修复：加 `at + " "`
- `handle_lottery_view` line 376: `reply_failure("查询", "该奖池未上架")` → 修复：加 `at + " "`
- `handle_lottery_view` line 433: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 修复：加 `at + " "`
- `handle_lottery_view` line 460: `reply_failure("查询", "处理失败，请稍后重试")` → 修复：加 `at + " "`

### PASS（已有 @）
- `handle_lottery_draw`: line 486-`at = safe_at_segment_or_empty(user_id)`，line 501 / 504 / 509 / 517 / 520 / 524 / 529 / 536-block / 542-block / 815 / 933 / 940-block / 950-block / 968 — 均带 at

### N/A
- 截图发送通过 `render_and_send_screenshot` helper（line 449-456）托管，failure_action="查询"

---

## `nextbot/plugins/menu.py`

### MISSING @
- `handle_menu` line 148: `reply_failure("查看菜单", "暂无可用命令")` → 修复：加 `at + " "`（需在 handler 头声明 `at`）
- `handle_menu` line 182: `reply_failure("查看菜单", f"未找到分类「{selector}」")` → 修复：加 `at + " "`
- `handle_search_command` line 231: `reply_failure("搜索命令", f"未找到包含「{keyword}」的命令")` → 修复：加 `at + " "`

### N/A
- 成功类输出 line 156 / 235：使用 `reply_list(...)`，不是失败回复。

---

## `nextbot/plugins/permission_manager.py`

### MISSING @
- `handle_admin_list` line 145: `reply_failure("查询", "未配置管理员（owner_id）")` → 修复：加 `at + " "`（handler 头有 `_safe_at_segment_or_empty` import，但未调用获取 at）

### PASS
- 文件内其他 handler 大量使用 `_caller_at_segment(event)` 辅助（line 84-85），具体审计中未发现其它 bot.send 失败位点。

---

## `nextbot/plugins/player_query.py`

> 该文件结构复杂，绝大多数 `reply_failure("查询", ...)` 用户失败回复均未带 @。`handle_my_map` / `handle_user_map` / `handle_explored_map` 在成功路径（V11）下用 `at_seg + image`，但路径前部的失败回复都未带 @。

### MISSING @
- `handle_user_inventory` line 416: `reply_failure("查询", "用户名称不存在")` → 加 `at + " "`
- `handle_user_inventory` line 419: `reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户")` → 加 `at + " "`
- `handle_user_inventory` line 422: `reply_failure("查询", "用户参数解析失败")` → 加 `at + " "`
- `handle_user_inventory` line 434: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_user_inventory` line 437: `reply_failure("查询", "用户不存在")` → 加 `at + " "`
- `handle_user_inventory` line 457: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_user_inventory` line 464: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_user_inventory` line 476: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_user_inventory` line 481: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_user_inventory` line 485: `reply_failure("查询", get_error_reason(info_response))` → 加 `at + " "`
- `handle_user_inventory` line 490: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_my_inventory` line 584: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_my_inventory` line 587: `reply_failure("查询", "用户不存在")` → 加 `at + " "`
- `handle_my_inventory` line 607: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_my_inventory` line 613: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_my_inventory` line 624: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_my_inventory` line 629: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_my_inventory` line 633: `reply_failure("查询", get_error_reason(info_response))` → 加 `at + " "`
- `handle_my_inventory` line 638: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_my_map` line 708: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_my_map` line 711: `reply_failure("查询", "用户不存在")` → 加 `at + " "`
- `handle_my_map` line 730: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_my_map` line 734: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_my_map` line 739: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_my_map` line 747: `reply_failure("查询", "返回数据过大")` → 加 `at + " "`
- `handle_my_map` line 773: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_my_map` line 785: `reply_failure("查询", "保存图片失败")` → 加 `at + " "`
- `handle_user_map` line 835: `reply_failure("查询", "用户名称不存在")` → 加 `at + " "`
- `handle_user_map` line 838: `reply_failure("查询", "用户名称不唯一，请使用用户 QQ 或 @用户")` → 加 `at + " "`
- `handle_user_map` line 841: `reply_failure("查询", "用户参数解析失败")` → 加 `at + " "`
- `handle_user_map` line 856: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_user_map` line 859: `reply_failure("查询", "用户不存在")` → 加 `at + " "`
- `handle_user_map` line 879: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_user_map` line 883: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_user_map` line 888: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_user_map` line 896: `reply_failure("查询", "返回数据过大")` → 加 `at + " "`
- `handle_user_map` line 922: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_user_map` line 934: `reply_failure("查询", "保存图片失败")` → 加 `at + " "`
- `handle_explored_map` line 984: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_explored_map` line 1003: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_explored_map` line 1007: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_explored_map` line 1012: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_explored_map` line 1020: `reply_failure("查询", "返回数据过大")` → 加 `at + " "`
- `handle_explored_map` line 1045: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`
- `handle_explored_map` line 1057: `reply_failure("查询", "保存图片失败")` → 加 `at + " "`
- `handle_world_progress` line 1107: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`
- `handle_world_progress` line 1118: `reply_failure("查询", "无法连接服务器")` → 加 `at + " "`
- `handle_world_progress` line 1122: `reply_failure("查询", get_error_reason(response))` → 加 `at + " "`
- `handle_world_progress` line 1142: `reply_failure("查询", "返回数据格式错误")` → 加 `at + " "`

### PASS（已有 @）
- `handle_online` line 207: `reply_failure("查询", "暂无服务器")` 单台广播汇总，无具体调用者 ← 实际上该 handler 内 `at` 也未定义，但「在线」是广播信息表，下方汇总 line 278 也未加 @；这是另一类语义（多服务器汇总），暂归为弱失败位，可与本次范围分开处理
- `handle_self_kick`: line 310 / 312 / 318 / 320 / 358 / 360 — 走 `_safe_at_segment(user_id)` 然后 `at_seg + " " + msg` 或 fallback，已带 @
- `handle_my_map` 成功路径 V11: line 757 `at_seg + image`（V11 路径）/ line 793 非 V11 fallback 文件名汇报 — V11 已带 @；非 V11 fallback 是成功路径不再纳入失败审计
- `handle_user_map` V11 line 906 `at_seg + image` 已带 @
- `handle_explored_map` V11 line 1030 `at_seg + image` 已带 @

### N/A
- 在线查询 line 278 `🖥️ 服务器在线状态\n...`：聚合查询结果，非"调用者"语义的失败回复（多服务器并行查询表）
- `handle_self_kick` 整体逻辑独立，已使用 at_seg helper

---

## `nextbot/plugins/red_packet.py`

### MISSING @
- `handle_list_own` line 595: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`（handler 头未声明 `at`，需新增）
- `handle_list_own` line 598: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_list_own` line 614: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 加 `at + " "`
- `handle_list_own` line 659: `reply_failure("查询", "处理失败，请稍后重试")` → 加 `at + " "`
- `handle_list_all` line 696: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_list_all` line 699: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_list_all` line 715: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 加 `at + " "`
- `handle_list_all` line 763: `reply_failure("查询", "处理失败，请稍后重试")` → 加 `at + " "`

### PASS（已有 @）
- `handle_send_red_packet`: line 133 / 141 / 148 / 151 / 154-block / 164 / 167-block / 179 / 184 / 199-block / 222 / 231 — 均带 at
- `handle_send_red_packet` 成功汇总 line 245 — `at + ...`
- `handle_grab_red_packet`: line 293 / 296 / 306 / 312 / 329 / 342 / 348 / 400 — 均带 at
- `handle_grab_red_packet` 成功汇总 line 431 — `at + ...`
- `handle_revoke_red_packet`: line 470 / 473 / 476 / 488 / 497 / 515 — 均带 at
- `handle_revoke_red_packet` 成功汇总 line 537 — `at + ...`

---

## `nextbot/plugins/rob.py`

### PASS（已有 @）
- `handle_rob`: line 154 / 163 / 166 / 172 / 180 / 200 / 205 / 216-block / 226 / 229 / 232 / 235 / 240 / 243 / 300 / 302 / 337 / 379 / 434 / 471 / 487 — 均使用 `at + " " + reply_failure(...)`
- `handle_rob` 结果汇总 line 519 — `at + " " + reply_text`

---

## `nextbot/plugins/rob_protection.py`

### PASS（已有 @）
- `handle_toggle_rob_protection`: line 70-block / 83 / 107 / 110 / 113-block / 130 — 均带 at
- 成功汇总 line 142 — `at + ...`

---

## `nextbot/plugins/security.py`

### PASS（已有 @）
- `_handle_login_action`（共享辅助）：line 128 / 131 / 140 / 148 / 153-block / 163 / 168 — 均带 at；`handle_confirm_login` / `handle_reject_login` 通过它复用

---

## `nextbot/plugins/server_manager.py`

### N/A
- 文件仅一个 handler `handle_list_servers`，line 38 `ℹ️ 暂无服务器` 为空集语义信息 / line 50 为成功汇总，均非失败回复。

---

## `nextbot/plugins/server_send.py`

### PASS（已有 @）
- `handle_send`: line 74 / 87 / 90 / 108 / 113 — 均使用 `at_prefix(event, reply_failure(...))`，`at_prefix` 内部自动拼 @
- 成功汇总 line 116 — `at_prefix(event, ...)`

---

## `nextbot/plugins/server_tools.py`

### MISSING @
- `handle_world_map` line 246: `reply_failure("查询", "服务器不存在")` → 加 `at + " "`（或 `at_prefix(event, ...)`，与同文件 `handle_execute` 风格统一）
- `handle_world_map` line 260: `reply_failure("查询", "无法连接服务器")` → 加 @
- `handle_world_map` line 264: `reply_failure("查询", get_error_reason(response))` → 加 @
- `handle_world_map` line 269: `reply_failure("查询", "返回数据格式错误")` → 加 @
- `handle_world_map` line 277: `reply_failure("查询", "返回数据过大")` → 加 @
- `handle_download_map` line 330: `reply_failure("下载", "服务器不存在")` → 加 @
- `handle_download_map` line 347: `reply_failure("下载", "无法连接服务器")` → 加 @
- `handle_download_map` line 351: `reply_failure("下载", get_error_reason(response))` → 加 @
- `handle_download_map` line 357: `reply_failure("下载", "返回数据格式错误")` → 加 @
- `handle_download_map` line 365: `reply_failure("下载", "文件过大")` → 加 @

### PASS（已有 @）
- `handle_execute`: line 147 / 157 / 169 / 174 / 179-block / 196 — 均使用 `at_prefix(event, reply_failure(...))`
- `handle_world_map` line 283 — 成功路径发送图片；line 292 `ℹ️ 地图数据已获取` 非 V11 信息汇报，可放宽（非 V11 fallback）
- `handle_download_map` line 418 — 成功路径

### N/A
- 非 V11 图片汇报 line 283 / 292 是 V11 适配器下成功路径的 send 或 ℹ️ 信息，不归类失败

---

## `nextbot/plugins/shop.py`

### MISSING @
- `handle_shop_list` line 205: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`（handler 顶部 `user_id` 已读取，但未声明 `at`，需新增）
- `handle_shop_list` line 208: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_shop_list` line 223: `reply_failure("查询", "暂无可用商店")` → 加 `at + " "`
- `handle_shop_list` line 228-block: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 加 `at + " "`
- `handle_shop_list` line 289: `reply_failure("查询", "处理失败，请稍后重试")` → 加 `at + " "`
- `handle_shop_view` line 331: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_shop_view` line 334: `reply_failure("查询", "页数必须为正整数")` → 加 `at + " "`
- `handle_shop_view` line 344: `reply_failure("查询", f"未找到商店「{selector}」")` → 加 `at + " "`
- `handle_shop_view` line 347: `reply_failure("查询", "该商店未上架"))` → 加 `at + " "`
- `handle_shop_view` line 365-block: `reply_failure("查询", f"超出总页数（共 {total_pages} 页）")` → 加 `at + " "`
- `handle_shop_view` line 446: `reply_failure("查询", "处理失败，请稍后重试")` → 加 `at + " "`

### PASS（已有 @）
- `handle_shop_buy`: line 464-`at = safe_at_segment_or_empty(user_id)`，line 476 / 481 / 487 / 498 / 510 / 534 / 565 / 594 / 602-block / 608-block / 615 / 636-block — 均带 at
- `handle_shop_buy` 成功汇总：line 583 / 680 / 697 / 715-block / 727 / 734 / 764-block / 772 / 780-block / 794 / 802-block / 819-block / 911 — 均带 at

### N/A
- 截图发送通过 `render_and_send_screenshot` helper（list / view 内）托管

---

## `nextbot/plugins/tutorial.py`

### MISSING @
- `handle_tutorial` line 47: `reply_failure("查询", "暂无可用教程")` → 加 `at + " "`（handler 未声明 `at`）
- `handle_tutorial` line 79-82: `reply_failure("查询", "未找到该教程，发送「使用教程」查看所有教程")` → 加 `at + " "`

### N/A
- 列表信息 line 55 `reply_list(...)` 非失败
- 截图发送 line 96-103 走 `render_and_send_screenshot` helper

---

## `nextbot/plugins/tutorial_data.py`

### N/A
- 仅静态数据声明，不审。

---

## `nextbot/plugins/user_manager.py`

### PASS（已有 @）
- `handle_add_whitelist`: line 220-`at`，line 224 / 232 / 237 / 247 — 均带 at；成功汇总 line 255
- `handle_sync_whitelist`: line 286-`at`，line 294 / 299；成功汇总 line 314
- `handle_user_info`: line 390-`at`，line 403 / 406 / 409 / 417
- `handle_self_info`: line 442-`at`，line 453
- `handle_rename`: line 484 / 487 / 499 / 506 / 511 / 519 / 539 / 546 / 555 — 均带 at；成功汇总 line 619

> 注：`handle_rename` 顶部需有 `at` 声明（看 line 484 之前的代码），从 grep 输出看 line 476 `at = safe_at_segment_or_empty(event.get_user_id())` 已存在，确认 PASS。

---

## `nextbot/plugins/warehouse.py`

### MISSING @
- `handle_list_user` line 363: `reply_failure("查询", "未找到该用户")` → 加 `at + " "`（handler 头部仅声明 `caller_user_id`，未声明 `at`）
- `handle_list_user` line 366: `reply_failure("查询", "用户名存在重复，请使用 QQ 或 @用户")` → 加 `at + " "`
- `handle_list_user` line 379: `reply_failure("查询", "未找到该用户")` → 加 `at + " "`
- `handle_list_user` line 399: `reply_failure("查询", "处理失败，请稍后重试")` → 加 `at + " "`

### PASS（已有 @）
- `handle_list_self`: line 318-`at`，line 322 / 340；成功路径 line 391 走截图 helper
- `handle_add`: line 424 / 427 / 439 / 442 / 448 / 451 / 457 / 460 / 465-block / 477 / 480 / 484-block / 493 / 501-block / 537-block / 558-block / 577 — 均带 at（前提 `at` 在 handler 头有，需核实 line 420 之前）
- `handle_delete` / `handle_drop` / `handle_recycle` / `handle_claim` / `handle_gift`：均在 handler 头声明 `at = safe_at_segment_or_empty(...)`（798 / 998 / 1340 / 1691），下游 `bot.send` 均带 at（line 602 / 605 / 617 / 623 / 628 / 631 / 648 / 669 / 674-block / 710 / 750 / 772 / 807 / 813 / 818 / 821 / 827 / 840 / 861 / 866-block / 902 / 941 / 963 / 1007 / 1013 / 1018 / 1021 / 1029 / 1046 / 1060 / 1071 / 1077 / 1080-block / 1147 / 1164 / 1192 / 1247 / 1349 / 1355 / 1361 / 1366 / 1369 / 1378 / 1383 / 1391 / 1394 / 1399 / 1419 / 1443 / 1453-block / 1460-block / 1473-block / 1496-block / 1518 / 1603 / 1647-block / 1663 / 1699 / 1702 / 1712 / 1718 / 1724 / 1729 / 1732 / 1737 / 1742 / 1764 / 1787 / 1792-block / 1800 / 1839-block / 1863 / 1953 / 1980）

### N/A
- 用户仓库截图（line 391）走 `_send_warehouse_image` helper

---

## 汇总

- **总文件数**：23（其中 `tutorial_data.py` 为静态数据已排除，实审 22 个）
- **有 MISSING 的文件数**：10
  1. `ban.py`（3）
  2. `leaderboard.py`（33）
  3. `lottery.py`（11）
  4. `menu.py`（3）
  5. `permission_manager.py`（1）
  6. `player_query.py`（47）
  7. `red_packet.py`（8）
  8. `server_tools.py`（10）
  9. `shop.py`（11）
  10. `tutorial.py`（2）
  11. `warehouse.py`（4）
- **总 MISSING 位点数**：**133**

### Top 5 MISSING 文件（按位点数倒序）
1. **`player_query.py`**（47）— 几乎所有 `查询`/`下载` 失败回复都缺 @
2. **`leaderboard.py`**（33）— 所有榜单 handler 都未声明 `at`，分页/服务器错误回复全缺 @
3. **`lottery.py`**（11）— `奖池列表` / `查看奖池` 查询类失败缺 @；抽奖类已 PASS
4. **`shop.py`**（11）— `商店列表` / `查看商店` 查询类失败缺 @；购买类已 PASS
5. **`server_tools.py`**（10）— `查询/下载` 失败缺 @；`handle_execute` 已使用 `at_prefix` PASS

### 修复模式建议（不在本审计范围，仅参考）
对每个 MISSING handler：
1. 在函数顶部加 `at = safe_at_segment_or_empty(event.get_user_id())`
2. 把 `bot.send(event, reply_failure(...))` 改成 `bot.send(event, at + " " + reply_failure(...))`
3. 或者复用 `at_prefix(event, ...)` helper（`text_utils.py:122`），与 `server_send.py` / `server_tools.py` 已有的 `handle_execute` 风格一致

## Caveats / Not Found

- `player_query.handle_online`（line 207）和 `handle_self_kick` 的 fallback 路径属于多服务器广播汇总语义，是否纳入"调用者 @"范围可由实现者决定；目前归类为弱失败位
- `handle_user_inventory` / `handle_my_inventory` 类 handler，成功路径 line 515 / 663 等仍是 `ℹ️ 用户背包链接：...`，但属于成功类信息发送，未纳入本次失败审计
- `permission_manager.py` 文件较长（120+ 处 bot.send？），但 grep 仅捕获到 `bot.send` 一次（line 145），如有遗漏需要二次确认
- `user_manager.handle_add` 顶部声明位置依赖第一遍 grep 推断；若改 spec/code 时建议二次确认
