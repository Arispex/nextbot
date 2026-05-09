# 主代理二次复查结论 — Post-final-sweep

**日期**: 2026-05-09
**复查范围**: 2 个子代理结果归并

---

## 复查方法

主代理 grep 验证 PC-8.1 / PV-1.2 关键 critical claim：
- `dice.py:203/215/235` ✅ 真
- `guess_number.py:239/251/271` ✅ 真
- `rob.py:305/318/356` ✅ 真

共 **9 处** `coins=User.coins + payout/amount` 直接加币，**绕过 add_coins_with_cap helper**，可让账户余额突破 MAX_COINS_AMOUNT 上限。

---

## 真实问题（去重 + 严重度调整）

### 🔴 必修 critical（1 项）

**PC-8.1 + PV-1.2** — 9 处加币 UPDATE 绕过 add_coins_with_cap helper（同根因合并）

- **位置**：
  - `dice.py:203/215/235`（猜大小 / 猜单双 / 三倍胜率）
  - `guess_number.py:239/251/271`（命中数字派奖）
  - `rob.py:305/318/356`（抢劫成功派金 + 反抢补偿）
- **影响**：final-sweep M4 SF-X.1 只把 economy / red_packet / warehouse 接入了 helper，**遗漏了小游戏的派奖路径**。dice 三倍奖（10×）等场景下用户可超过 1 亿账户上限。
- **修法**：3 个文件全部接入 `add_coins_with_cap(session, user_id, payout)`，触顶时 logger.warning 并给用户提示
- **影响范围**：dice / guess_number / rob 也属于"经济流出端"——架构一致性 bug

### 🟠 真实 high（2 项）

**PC-3.1** — user_manager 3 处 whitelist URL 缺 `quote(safe="")`
- 位置：`user_manager.py:100/153/164` 三处 `f"/nextbot/whitelist/.../{name}"`
- 影响：与 ban_core / player_query 同形已加固，user_manager 漏。当前 `_validate_user_name` 字符白名单兜底，但放宽校验或 DB 直插即破
- 修法：所有拼接前 `quote(name, safe="")`

**PC-4.1** — `_safe_at_segment` 未推广到 `text_utils.at_prefix`
- 位置：`player_query.py` 内部 `_safe_at_segment` 单独使用，18+ 处其他 handler 用 `at_prefix(...)` 直接 `int(user_id)`
- 影响：非 V11 适配器（V12 / KOOK / Telegram bridge）下 user_id 非数字时崩
- 修法：把 `_safe_at_segment` 提升到 `text_utils.at_prefix` 内部，所有 handler 自动受益

### 🟡 medium（3 项）

**PC-6.1** — server_manager add/delete + admin economy.coins.add/remove 缺 audit
- 位置：`server_manager.py` 添加服务器 / 删除服务器；`economy.py` 增加金币 / 减少金币
- 影响：server CRUD + 管理员金币操作是高敏感事件但绕过了统一 audit 入口
- 修法：补 `audit_permission_change(action="server.add" / "server.delete" / "admin.coins.add" / ...)`

**PC-2.1** — lottery / shop 在线检查仍串行
- 位置：`lottery.py:621` / `shop.py:740` `for srv in servers: ... await _check_player_online`
- 影响：lottery / shop 玩家在线检查未并行 fan-out
- 修法：改 asyncio.gather

**PV-X.1** — lottery `_charge_atomic` partial UPDATE 没校验 rowcount
- 位置：`lottery.py:781-788, 814-821`
- 影响：极端 TOCTOU 下 user 看到 "+X 金币" 与"当前金币"对不上（数字偏差，无安全后果）
- 修法：partial UPDATE 后检 rowcount，rowcount=0 时回退 applied=0

### 🟢 low (2 项)

**PC-1.1** — `user_manager.py:491` `user.name = new_name` ORM dirty-set
- 已被 IntegrityError + UNIQUE 兜底，建议改条件 UPDATE 但非紧急

**PV-8.1** — `screenshot_render.py:57` docstring 说 "截图生成成功"，实际代码已改 "截图已生成"
- 文档过期，1 行修

---

## 复审通过（grep 干净 / 已验证）

- `add_coins_with_cap` 6 callsite 全部正确消费返回值
- `init_db()` 完全幂等（17 ensure_*_schema 全 IF NOT EXISTS）
- `shop._buy_command` 边界全部覆盖（empty / overflow / cap）
- `DANGEROUS_PERMISSION_PREFIXES` 不误拦合法 guest 权限（economy.signin / leaderboard.* / about / menu.* OK）
- 同步访客权限 confirm-time live diff 正确
- POLA PMB-3.1 + SS-4.1 对称且 owner 短路覆盖完整
- `audit_permission_change` commit 后调用 + denied 路径覆盖
- `screenshot_render` 文案不重复（M10 已修）
- `MAX_COINS_AMOUNT` 单点 import（economy.py 暴露）
- `screenshot_url` 直接调用 ✓ 全走 helper
- `_to_base64_image_uri` ✓ 已删
- IntegrityError + rollback ✓
- mutation fan-out 全走 server_broadcast / asyncio.gather

---

## 主代理整体看法

**预期"critical / high 应为 0"未达**。PC-8.1 + PV-1.2 是 **final-sweep M4 SF-X.1 修复的范围遗漏**——只覆盖了"经济流出"路径（red_packet / warehouse / economy）但漏了"小游戏派奖"路径（dice / guess_number / rob）。

**修法简单**：用现有 add_coins_with_cap helper 替换 9 处直接 UPDATE 即可。

**其他 high (PC-3.1 / PC-4.1) 是真实历史遗漏**，与 PC-8.1 同性质：之前的 audit 在某些 handler 内修了，但同形模式在其他 handler 漏掉。

**修复优先级**：
1. PC-8.1 (9 处加币 cap)
2. PC-3.1 (3 处 quote(safe=""))
3. PC-4.1 (at_prefix 推广)
4. PC-6.1 / PC-2.1 / PV-X.1 (medium 收尾)
5. PC-1.1 / PV-8.1 (low)
