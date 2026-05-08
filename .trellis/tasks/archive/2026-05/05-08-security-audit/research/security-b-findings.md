# 安全管理（封禁部分）审计 — `nextbot/plugins/ban.py` + `nextbot/ban_core.py`

- **范围**: `nextbot/plugins/ban.py`（315 行，3 命令）、`nextbot/ban_core.py`（110 行，共享逻辑）
- **依赖参考**: `nextbot/tshock_api.py`、`nextbot/db.py`、`nextbot/access_control.py`、`nextbot/permissions.py`、`nextbot/screenshot_temp.py`、`nextbot/large_image.py`、`nextbot/text_utils.py`、`server/web_server.py`、`server/screenshot.py`、`nextbot/plugins/economy.py`、`nextbot/plugins/group_member_notify.py`、`server/routes/webui_users.py`
- **日期**: 2026-05-08

## 总体结论

三个命令均通过 `@require_permission` 守护：`封禁用户` (`admin.ban`)、`解封用户` (`admin.unban`)、`封禁列表` (`ban.list`)。其中 **`ban.list` 已在 `DEFAULT_GUEST_PERMISSIONS` (`db.py:36`) 中**，即任何注册用户（甚至游客）都能查看封禁列表。`admin.ban` / `admin.unban` 不在游客集中，受 owner / admin 群组限制（行为符合预期）。

主要问题集中在：

1. **lost-update（read-modify-write）**：封禁与解封都使用 ORM 属性赋值 + `session.commit()`，没有像 `economy.py` 那样的条件 UPDATE，导致并发场景下两个操作员可能互相覆盖（封禁原因 / 时间戳错乱），且“封禁中途被解封 / 解封中途被封禁”的顺序无法在 DB 层保证。
2. **DB-API 双写一致性**：本地库改写 `is_banned=True` 后，再 fan-out `/nextbot/blacklist/add` 到所有服务器；当所有服务器同步全部失败时，没有 CRITICAL 日志、没有用户告警、没有补偿提示，本地与游戏端永久漂移。`group_member_notify.py:174-184` 在 `apply_ban_to_db` 之后也只是 `logger.info`，严重程度更甚（无人值守）。
3. **fan-out 串行**：`sync_user_to_blacklist` 与 `handle_unban` 内部对 N 台服务器的“先 GET `/nextbot/blacklist`，再 POST `/blacklist/add|remove`”是完全串行的；与 `player_query.py:260` 已修复的 PQA-1.1 模式（`asyncio.gather`）不一致，两次往返 × N 台累积墙钟。
4. **TShock URL 路径段未 percent-encoded**：`f"/nextbot/blacklist/add/{user_name}"`、`f"/nextbot/blacklist/remove/{user_name}"` 直接把 `user.name` 拼进 URL 路径段；`tshock_api.py:58` 的 `quote(safe="/")` 是对整段 path 做 URL 标准化，**保留 `/`**，意味着名字里若含 `/`、`#`、`?` 等会改变路由。这正是 PQB-2.2 / PQB-1.3 的修复内容（path 段插值前应 `quote(safe="")`），ban 路径未应用。
5. **`封禁列表` 输入截图分支**：使用了刚 patch 的 `temp_screenshot_path`（uuid 已防碰撞），但 **缺乏 `_to_base64_image_uri` 的 OOM 上限**（`large_image.MAX_BASE64_BYTES = 200 MiB`）、**缺乏 `_inventory_semaphores` 风格的 per-server / 全局并发限流**。`封禁列表` 本身列表项可控（封禁数 × 50 行/页），单页 PNG 不会很大；但是命令对 guest 开放，1000 用户同时刷会启动 1000 个 Playwright context，仍是 OOM 入口。
6. **审计缺失**：`解封用户` 没有把操作员 user_id 写进成功日志；只有“解封成功 user_id=被解封者”一条；管理员滥用难以追溯。`封禁用户` 同样缺少操作员 ID。
7. **owner 自封防护单点**：`apply_ban_to_db` 仅拒绝封禁 Owner，但 **管理员 vs 管理员、管理员封禁自己** 没有任何拦截；项目没有“群组分级”概念时勉强可接受，但建议显式拒绝 `target == operator`。
8. **未使用的 import**：`ban.py:11` `from nextbot.access_control import get_owner_ids`、`ban.py:18` `from nextbot.time_utils import db_now_utc_naive` 在迁移到 `ban_core.py` 后已经无人调用。

严重度图例：🔴 critical / 🟠 high / 🟡 medium / 🟢 low / ℹ️ info

---

## 1. `封禁用户` — `handle_ban`（`ban.py:43-102`）

### SB-1.1 🔴 critical — 黑名单同步全失败时无 CRITICAL 日志、无显式补偿提示，本地 / 游戏端永久漂移

- **位置**: `ban.py:77-102`、`ban_core.py:61-110`
- **现状**:
  ```python
  result = apply_ban_to_db(target_user_id, reason)   # 本地 commit
  ...
  lines.extend(await sync_user_to_blacklist(result.user_name, reason))  # fan-out
  logger.info(f"封禁用户黑名单同步完成：user_id={result.user_qq} ...")    # 无视成功率
  await bot.send(event, at + "\n" + "\n".join(lines))
  ```
  `sync_user_to_blacklist` 把每台服务器的成功 / 失败拼成 `lines` 返回，没有汇总成功率，没有把“X 台全部失败”识别成异常事件。最终成功日志固定输出 `黑名单同步完成`，与“同步 0/5 成功”视觉无差。
- **影响**:
  - 当所有服务器都连不上（网络分区 / TShock token 全过期）时，本地 `User.is_banned=True` 已 commit，但游戏端任何服务器都不知道这条封禁，被封者仍可正常进入；管理员误以为已生效。
  - 没有 CRITICAL 日志意味着告警系统抓不到该次漂移；运维事后追查只能靠 grep `添加失败` 单条。
  - 与仓库审计 W-7 已修复的“DB 写入成功但外部 API 全失败要 CRITICAL + 提醒用户对账”模式直接冲突。
- **复现**: 临时把所有 `Server.token` 改错 → `封禁用户 12345 测试` → 本地 `is_banned=True`、bot 回复每台“添加失败”但 emoji 仍是 ✅ 总成功标题。
- **建议**:
  1. `sync_user_to_blacklist` 返回 `(lines, success_count, total_count)`；
  2. 在 `handle_ban` 中：若 `success_count == 0 and total_count > 0`，追加 `reply_warning("⚠️ 所有服务器同步失败，请联系管理员手动核对")` + `logger.critical("封禁本地落库成功但黑名单全失败 user_id=...")`；
  3. 同步部分失败（`0 < success_count < total_count`）时，主标题降级为 `⚠️ 封禁部分成功`。

### SB-1.2 🟠 high — TShock URL 路径段未 percent-encoded，user_name 含 `/` `?` `#` 时路由错乱

- **位置**: `ban_core.py:95`、`ban.py:300`、`webui_users.py:618` `707`（同源问题）
- **现状**:
  ```python
  await request_server_api(server, f"/nextbot/blacklist/add/{user_name}", params={"reason": reason})
  ...
  await request_server_api(server, f"/nextbot/blacklist/remove/{user_name}")
  ```
  `tshock_api.py:58` 的 `quote(request_path, safe="/")` 仅做 defense-in-depth 的最小 URL 合法化，**保留 `/`**，因此 `user_name = "a/b"` 拼出 `/nextbot/blacklist/add/a/b` 会被 TShock 路由器解释成两段路径，命中错误的 endpoint（404）或匹配到不预期的子路由；`?` `#` 同理会触发 query / fragment 提前截断。
- **影响**:
  - `User.name` 字段在 `db.py:125` 仅 `String, nullable=False, index=True`，没有字符白名单；历史脏数据或恶意注册可能让 name 含 `/`。
  - 已在 `player_query.py:427`、`595` 通过 `quote(target_user.name, safe="")` 修复（PQB-2.2），ban 路径未跟进。
  - 影响面：封禁（add）和解封（remove）都会失败但不会回滚；`封禁列表` 也会基于错乱的 `is_banned` 状态展示。
- **复现**: 直接构造测试用户：`UPDATE user SET name='a/b' WHERE user_id='123'` → `封禁用户 123 测试` → 观察 TShock 服务器日志，URL 已变 `/nextbot/blacklist/add/a/b`，404。
- **建议**:
  ```python
  from urllib.parse import quote
  encoded_name = quote(user_name, safe="")
  await request_server_api(server, f"/nextbot/blacklist/add/{encoded_name}", params={"reason": reason})
  ```

### SB-1.3 🟠 high — fan-out 串行，N 台服务器封禁需 ≥ 2N × 5s

- **位置**: `ban_core.py:74-105`
- **现状**: `for server in servers:` 内每台先 `await request_server_api(... "/nextbot/blacklist")`（GET 列表），再 `await request_server_api(... "/blacklist/add/...")`，两次请求都串行。`request_server_api` 默认 `connect=5s, read=5s`。
- **影响**: 5 台服务器最坏情况需 5 × 2 × 5s = 50s 才能完成；这段时间 OneBot 心跳被阻塞，handler 内不能处理其他事件。`player_query.py:260` 的 PQA-1.1 模式（`asyncio.gather(*tasks)`）已确立项目并行 fan-out 标准。
- **复现**: 配置 5 台 `Server.ip` 为不可达地址 → 执行 `封禁用户 ...` → 测墙钟 ≥ 50s。
- **建议**:
  ```python
  async def _sync_one(server: Server) -> str:
      try:
          check = await request_server_api(server, "/nextbot/blacklist")
      except TShockRequestError:
          return f"{server.id}.{server.name}：❌ 添加失败，无法连接服务器"
      if is_success(check):
          ...
      try:
          resp = await request_server_api(server, f"/nextbot/blacklist/add/{quote(user_name, safe='')}", params={"reason": reason})
      except TShockRequestError:
          return f"{server.id}.{server.name}：❌ 添加失败，无法连接服务器"
      ...
      return ...

  results = await asyncio.gather(*(_sync_one(s) for s in servers), return_exceptions=False)
  ```

### SB-1.4 🟡 medium — `User.is_banned` 写入是 ORM 读改写，缺乏条件 UPDATE 守卫并发

- **位置**: `ban_core.py:32-56`
- **现状**:
  ```python
  user = session.query(User).filter(User.user_id == user_id).first()
  ...
  if user.is_banned:
      return BanDBResult(code="already_banned", ...)
  user.is_banned = True
  user.banned_at = db_now_utc_naive()
  user.ban_reason = reason
  session.commit()
  ```
  典型 read-modify-write，没有 `where User.is_banned == False` 守卫。SQLAlchemy 默认 issue 的是 `UPDATE user SET is_banned=1, banned_at=..., ban_reason=... WHERE id=?`，与并发竞争方完全互相覆盖。
- **影响**:
  - 两个管理员同时 `封禁用户 X 原因A` 和 `封禁用户 X 原因B` → 两边都读到 `is_banned=False`、两边都进 commit、最后存的是后写入者的 `ban_reason`，**两边消息都报“封禁成功”**，但实际只有一份原因，前者的 `banned_at` 也被覆盖。
  - 同理 `封禁` + `解封` 同时进行，最终状态由 commit 顺序决定，无法保证用户先收到的成功 / 失败提示与 DB 终态一致。
  - 与 `economy.py:192-205` 的 `update(...).where(...).values(...)` + `execute_rowcount` 模式不一致。
- **建议**:
  ```python
  from sqlalchemy import update
  from nextbot.db import execute_rowcount
  rowcount = execute_rowcount(
      session,
      update(User)
      .where(User.user_id == user_id, User.is_banned == False)
      .values(is_banned=True, banned_at=db_now_utc_naive(), ban_reason=reason),
  )
  if rowcount == 0:
      # 重新读取以区分 not_found vs already_banned
      ...
  ```

### SB-1.5 🟡 medium — 封禁成功日志未记录操作员 user_id

- **位置**: `ban.py:88-90`、`99-101`
- **现状**:
  ```python
  logger.info(f"用户封禁成功：user_id={result.user_qq} name={result.user_name} reason={reason}")
  ```
  日志中的 `user_id` 是**被封禁者**，不是**操作员**。`event.get_user_id()` 完全未进入日志。
- **影响**:
  - 当多个 admin 群组成员都有 `admin.ban` 时，事后审计无法回答“谁封了 X”。
  - 与 `permissions.py:106` 的 `权限不足：user_id=...` 风格一致性差。
- **建议**:
  ```python
  operator_id = event.get_user_id()
  logger.info(
      f"用户封禁成功：operator_id={operator_id} target_user_id={result.user_qq} "
      f"target_name={result.user_name} reason={reason}"
  )
  ```

### SB-1.6 🟡 medium — 同步结果总是同 reason 拼回消息，群里会泄露原因到所有人

- **位置**: `ban.py:96`（`f"📋 原因：{reason}"`）
- **现状**: 当 `封禁用户` 在群聊里执行时，回复消息（含原因）发给整个群。
- **影响**: 实战中 `reason` 经常包含具体行为描述（“在 PvP 中开挂”、“辱骂 XX 用户”），群里所有成员看到，被封禁者隐私 / 名誉风险。属于业务而非安全漏洞，但 owner 视角应有取舍。
- **建议**: 把 `📋 原因：...` 限定在私聊回复 / WebUI 审计页面；群里只报 ✅ 封禁成功 + 用户名。

### SB-1.7 🟢 low — `apply_ban_to_db` 的 `not_found` 与 `name_not_found` 错误语义在调用方混合

- **位置**: `ban.py:62-79`
- **现状**: 解析阶段 `name_not_found` 与 DB 阶段 `not_found` 都返回相同文案“未找到该用户”。前者发生于消息解析（用户名在 User 表查不到）、后者发生于 ban handler 拿到 user_id 后查 DB 失败（理论上消息解析阶段已经走过同库查询，几乎不可能命中）。
- **影响**: 排障时无法区分“传错了名字”和“DB 同名 ID 但 row 已被其他流程删除”。
- **建议**: `not_found` 改文案为 `用户记录已不存在，请重试`，与解析阶段错误区分开。

### SB-1.8 ℹ️ info — `args = parse_command_args_with_fallback(...)` 在 `resolve_user_id_arg_with_fallback` 之后再次解析，重复 logger 输出

- **位置**: `ban.py:70`、`message_parser.py:155`
- **现状**: `resolve_user_id_arg_with_fallback` 内部已经调用 `parse_command_args_with_fallback` 并打过 INFO 日志（成功 / 失败）；`ban.py` 又显式再调用一次 `parse_command_args_with_fallback` 取 `len(args)` 与 `reason`，会产生第二次 INFO 日志。
- **影响**: 日志噪音轻微重复，无功能问题。
- **建议**: 让 `resolve_user_id_arg_with_fallback` 同时返回 `args`，或在 ban handler 缓存第一次结果。

---

## 2. `封禁列表` — `handle_ban_list`（`ban.py:105-204`）

### SB-2.1 🟠 high — `ban.list` 默认在 guest 权限集中，未注册用户也可截图查询所有封禁数据

- **位置**: `ban.py:107-125`、`db.py:36`
- **现状**: `DEFAULT_GUEST_PERMISSIONS` 含 `"ban.list"`，意味着任何在群里发命令的 QQ（即使未通过 `用户注册`）都能触发 Playwright 截图、生成 PNG、并把所有被封者 QQ + 名字 + 原因 + 时间发给请求者。
- **影响**:
  - 隐私：被封禁者完整 QQ、姓名、原因（往往敏感）暴露给陌生人。
  - DoS 入口：guest 即可触发 Playwright context 启动 + 截图（每次约 1–3s CPU + 50–200 MB RSS）；恶意刷命令可让进程 OOM 或饿死其他截图任务。
  - 与 `admin.ban` 仅 owner 可执行的等级保护不对等：能看不能改，但“看”本身已是高敏感。
- **复现**:
  1. 用未注册 QQ 直接发 `封禁列表` → 收到截图，列表全公开。
  2. 同一未注册 QQ 在循环里 `封禁列表 1`、`封禁列表 2`... 30 次/分 → 单进程内存上涨。
- **建议**:
  - 从 `DEFAULT_GUEST_PERMISSIONS` 移除 `ban.list`，改入 `default` / 单独的 `admin` 群组；
  - 或在 handler 里加 `if not has_permission(operator_id, "admin.ban"): return failure`；
  - 即使决定保留 guest 可见，也应去敏感字段（隐藏 ban_reason，仅显示用户名 + 时间）。

### SB-2.2 🟠 high — Playwright 截图 + base64 编码全部走默认值，无并发上限、无 OOM 截断

- **位置**: `ban.py:187-204`、`screenshot_temp.py:30-39`、`large_image.py`
- **现状**:
  ```python
  async with temp_screenshot_path("ban-list") as screenshot_path:
      try:
          await screenshot_url(page_url, screenshot_path, options=BAN_LIST_SCREENSHOT_OPTIONS)
      ...
      image_uri = _to_base64_image_uri(screenshot_path)  # 一把读全文件 + b64encode 进内存
      await bot.send(event, OBV11MessageSegment.image(file=image_uri))
  ```
  没有 `_inventory_semaphores`-style 并发限流；`_to_base64_image_uri` 没用 `large_image.MAX_BASE64_BYTES` 校验。
- **影响**:
  - 被封禁列表理论上可达数千行 × `viewport_width=920` × full_page → PNG 体积可能数 MB；但更危险的是 Chromium full_page 渲染对内存峰值的放大（Playwright context + 双 buffer）。
  - 没有 per-handler `asyncio.Semaphore`：`封禁列表` 与 `inventory` / `progress` 共享同一个进程的 Chromium，但**没有任何限流**，10 个并发会启 10 个 BrowserContext。
  - `_to_base64_image_uri(path)` 一次性 `path.read_bytes()` + `b64encode`：原文件 X MB → 内存里 X + 1.33X ≈ 2.3X MB；超大文件直接 OOM，且没有像 `player_query.py:762` 的 `MAX_BASE64_BYTES` 拒绝。
- **建议**:
  ```python
  _ban_list_semaphore = asyncio.Semaphore(2)
  ...
  async with _ban_list_semaphore:
      async with temp_screenshot_path("ban-list") as screenshot_path:
          await screenshot_url(...)
          file_size = screenshot_path.stat().st_size
          if file_size * 4 // 3 > MAX_BASE64_BYTES:   # 预估 base64 大小
              await bot.send(event, reply_failure("查询", "封禁列表过大，请缩小页数"))
              return
          image_uri = _to_base64_image_uri(screenshot_path)
  ```

### SB-2.3 🟡 medium — `temp_screenshot_path("ban-list")` 在并发场景里**仍可能与同 prefix 的另一并发请求互相清理**（虽 uuid 已防同名碰撞，但 prefix 不含 page）

- **位置**: `ban.py:187`、`screenshot_temp.py:30-39`
- **现状**: prefix 固定为 `"ban-list"`，所有并发请求文件名只靠 `beijing_filename_timestamp() + uuid4()[:8]` 区分。uuid 已避免同名（已修复 PQA-3.1），但 prefix 不带 `page` / 操作员 ID，会让 `/tmp` 同时存在多个 `ban-list-*` 文件，无法在出错时区分谁属于谁。
- **影响**:
  - 不影响正确性（uuid 唯一）；仅影响排障：故障时 `/tmp` 看到 5 个 `ban-list-*` 不知归属。
  - 与 `player_query.py:1172` 的 `f"progress-{server.id}"`、`498` 的 `f"inventory-{server.id}-{target_user.user_id}"` 一致性差。
- **建议**: `temp_screenshot_path(f"ban-list-p{page}-{event.get_user_id()}")`。

### SB-2.4 🟡 medium — 整张表 `User.is_banned == True` 全量加载到内存做分页

- **位置**: `ban.py:144-156`
- **现状**:
  ```python
  banned_users = (
      session.query(User).filter(User.is_banned == True)
      .order_by(User.banned_at.asc()).all()
  )
  ...
  total = len(banned_users)
  ...
  page_users = banned_users[offset : offset + limit]
  ```
  把所有被封用户 row 全部 ORM 物化，然后 Python 切片分页。
- **影响**: 当封禁数到达 10K+，每次执行命令都全表 SELECT + ORM 实例化所有列（`User` 有 30+ 列），单次查询占用内存上百 MB；DB 也无索引帮忙。
- **复现**: 模拟 50K 行 `is_banned=True`，单进程 RSS 涨到 500MB+。
- **建议**:
  ```python
  total = session.query(User).filter(User.is_banned == True).count()
  page_users = (
      session.query(User).filter(User.is_banned == True)
      .order_by(User.banned_at.asc())
      .offset((page - 1) * limit).limit(limit).all()
  )
  ```
  并在 `db.py` 给 `is_banned, banned_at` 加复合索引（`ix_user_is_banned_banned_at`）。

### SB-2.5 🟢 low — 当 `total == 0` 时仍生成空白页面 + 截图，浪费 Playwright

- **位置**: `ban.py:158-161`、`187-204`
- **现状**: 没有任何被封者时仍 `create_ban_list_page(...)` 然后启动 Playwright 截图。
- **影响**: 1–3 秒资源浪费 + 一张几乎空白的图。
- **建议**: `if total == 0: await bot.send(event, reply_info("当前无封禁用户")); return`。

### SB-2.6 🟢 low — 错误路径下未清理 page_store token

- **位置**: `ban.py:179-191`、`server/web_server.py:117-122`
- **现状**: `create_ban_list_page` 通过 `create_page("ban_list", payload)` 落地一个 token-based store；当截图阶段抛 `RenderScreenshotError` 时，之前生成的 token 残留在 store 里直到 TTL 过期。
- **影响**: 仅占内存 / 存储；无安全影响（token 不可枚举）。
- **建议**: 截图失败后调用 `delete_page(token)`（如 store 支持），或改在截图前不 emit token。

### SB-2.7 ℹ️ info — `int(get_current_param("limit", 10))` 在 `get_current_param` 返回非数字时会抛 `ValueError`

- **位置**: `ban.py:142`
- **现状**: WebUI 配置里 `limit` 字段被改成字符串“abc”时，`int(...)` 抛 `ValueError`，handler 整体 500。
- **影响**: 罕见配置错误才会触发，且 `command_control` 的 schema (`type: "int"`) 会在加载时拦截大部分；属于二次防御缺失。
- **建议**: `try: limit = int(...); except (TypeError, ValueError): limit = 10`。

---

## 3. `解封用户` — `handle_unban`（`ban.py:207-315`）

### SB-3.1 🔴 critical — 解封解包 ORM 对象后**不在 session 中读 `user.name` / `user.user_id`**，触发 `DetachedInstanceError`

- **位置**: `ban.py:249-257`
- **现状**:
  ```python
  user.is_banned = False
  user.banned_at = None
  user.ban_reason = ""
  session.commit()              # ← commit 后默认会 expire 所有属性

  user_name = user.name         # ← 这里再读会触发 lazy load
  user_qq = user.user_id
  finally:
      session.close()
  ```
  SQLAlchemy 默认 `expire_on_commit=True`，commit 后所有 ORM 属性失效；**项目 sessionmaker `db.py:373` 没有显式关闭 expire**（仅设了 `autoflush=False, autocommit=False`），所以默认 `expire_on_commit=True` 仍生效。下一次访问 `user.name` 会触发 SELECT，但 session 还活着，所以**实际不会抛异常，会多 1 次 SQL**。
  真正问题是：commit 之后发生异常（比如内存不足），代码进入 finally 关闭 session，**但前面已经 commit 成功**——`user.is_banned=False` 已落库，bot 却没机会发任何回复。
- **影响**:
  - 多 1 次 `SELECT user WHERE id=?`（未必 critical，但代表 commit 后 ORM 假设错位）。
  - 真正的 critical 在于：**commit 与 `name = user.name` 中间任何异常都会让本地解封成功而用户无任何反馈，且后续 fan-out 不会执行 → 与 ban 同样的双写漂移**，但解封方向（用户被解封了，本地以为没解封，5 台游戏服务器还在黑名单中）。
- **复现**: 注入异常断点在 `user_name = user.name` 处 → 抛异常 → DB 已是 `is_banned=False`，bot 静默退出。
- **建议**:
  - 在 commit 前捕获 `name = str(user.name); user_qq = str(user.user_id)`：
    ```python
    user_name = str(user.name)
    user_qq = str(user.user_id)
    user.is_banned = False
    user.banned_at = None
    user.ban_reason = ""
    session.commit()
    ```
  - 解封逻辑迁入 `ban_core.py` 形成对称 `apply_unban_to_db()`，与 `apply_ban_to_db` 一样返回 dataclass。

### SB-3.2 🔴 critical — 解封同样无 CRITICAL 日志 / 用户告警，黑名单 remove 全失败时本地 / 游戏端永久漂移

- **位置**: `ban.py:267-315`
- **现状**: 与 `SB-1.1` 完全同结构。本地 `is_banned=False` commit 后再走 fan-out remove；任意比例的失败都只在每行末尾写文字，最后一条 `logger.info("解封用户黑名单同步完成")` 永远显示成功。
- **影响**: 用户已被解封（本地视角），但 5 台游戏服务器仍把他列在黑名单里，他登入会被拒绝。运维和管理员看封禁列表时看不到他，TShock 那边却拒登他，问题被错位归因为“TShock 故障”。
- **建议**: 与 SB-1.1 同方案——汇总 success_count，全失败 logger.critical + 提示用户对账。

### SB-3.3 🟠 high — `User.is_banned = False` 同样是 read-modify-write，缺乏条件 UPDATE

- **位置**: `ban.py:240-252`
- **现状**:
  ```python
  user = session.query(User).filter(User.user_id == target_user_id).first()
  if user is None: ...
  if not user.is_banned: ...
  user.is_banned = False; user.banned_at = None; user.ban_reason = ""
  session.commit()
  ```
- **影响**: 与 SB-1.4 同模式：两个 admin 同时 `解封用户 X` + `封禁用户 X` 互相覆盖、最终状态由 commit 顺序决定。`封禁用户` 的“已封禁”检查与 `解封用户` 的“未封禁”检查都在 read-modify-write 之间，**没有任何 DB 锁**。
- **建议**: 用 `update(User).where(User.user_id == target_user_id, User.is_banned == True).values(is_banned=False, banned_at=None, ban_reason="")` + `execute_rowcount`。`rowcount == 0` 时再回查区分 not_found / not_banned。

### SB-3.4 🟠 high — 解封 fan-out 同样串行，2 × 5s × N 累积墙钟

- **位置**: `ban.py:276-310`
- **现状**: 与 SB-1.3 同结构。
- **建议**: 与 SB-1.3 同方案——内联 `_remove_one(server)` + `asyncio.gather`。

### SB-3.5 🟠 high — TShock URL 路径未 percent-encoded（同 SB-1.2）

- **位置**: `ban.py:300`
- **现状**: `f"/nextbot/blacklist/remove/{user_name}"`。
- **建议**: `quote(user_name, safe="")`。

### SB-3.6 🟡 medium — 解封成功日志缺操作员 user_id

- **位置**: `ban.py:259`、`312-314`
- **现状**: `logger.info(f"用户解封成功：user_id={user_qq} name={user_name}")` 中 `user_id` 是被解封者。
- **影响**: 与 SB-1.5 一致，操作员审计缺失。
- **建议**: 加 `operator_id=event.get_user_id()`。

### SB-3.7 🟡 medium — `len(args) != 1` 在用户名含空格时误报“用法错误”

- **位置**: `ban.py:235`
- **现状**: `parse_command_args_with_fallback` 用空格分词；如果用户名是 `Mary Jane`，`args` 会有 2 项，handler 抛用法错误。但 `resolve_user_id_arg_with_fallback` 仅取第 0 项查表，第 0 项 = `"Mary"`，要么 `name_not_found` 要么误中同名 `Mary`。
- **影响**: 与 `db.py` 的 `User.name` 没禁止空格冲突；解封逻辑无法处理含空格名字。
- **建议**: `len(args) >= 1` + 用 `parse_command_text_with_fallback` 拼回原文，或要求 QQ 模式时跳过这个检查。

### SB-3.8 🟢 low — 二次开 session（259 行后再 261 行 get_session）

- **位置**: `ban.py:238-265`
- **现状**: 第一段 session 用于读 + 写；commit 后关闭；第二段 session 重新打开仅查 `Server` 列表。
- **影响**: 浪费一次连接池占用，但行为正确。
- **建议**: 在第一段 session 内一并 `session.query(Server).all()` 后再 commit 关闭，或先查服务器再开始 ban 事务。

---

## 4. 共享模块 — `nextbot/ban_core.py`

### SC-4.1 🟠 high — `apply_ban_to_db` 与 `sync_user_to_blacklist` 完全分离，没有事务回滚通道

- **位置**: `ban_core.py:29-110`
- **现状**: 公开 API 是两个独立函数，调用方负责按顺序调用：
  1. `apply_ban_to_db(user_id, reason)` 同步、commit；
  2. `sync_user_to_blacklist(user_name, reason)` 异步、I/O；
  本身没有“失败回滚 ban_db”的语义；调用方（`ban.py`、`group_member_notify.py`）也没有写补偿逻辑。
- **影响**:
  - DB-API 双写漂移（SB-1.1 / SB-3.2）的根因。
  - 调用方各自实现告警 / 不实现告警，行为不一致：`group_member_notify.py:181` 只 `logger.info`，`webui_users.py:638` 也只 `logger.info`，无人补偿。
- **建议**:
  - 增加 `apply_ban_with_sync(user_id, reason) -> AggregatedResult`：内部完成 commit + fan-out + 返回 `(db_result, success_count, total_count, lines)`；
  - 当 `success_count == 0 and total_count > 0` 时函数内自动 `logger.critical(...)`；调用方只需要展示 `lines` + 选择是否提示。

### SC-4.2 🟠 high — `sync_user_to_blacklist` 没有任何并发限流；多个调用者同时调用同一台服务器会触发 N 次 GET `/blacklist`

- **位置**: `ban_core.py:74-105`
- **现状**: 串行 + 无 semaphore；当 `封禁用户` 与 `group_member_notify`（退群自动封禁）同时触发，多线程 / 多 task 都会对同一台 TShock 发 `/nextbot/blacklist` 列表请求。
- **影响**: TShock 端 `/nextbot/blacklist` 返回所有黑名单项（可达数千行 JSON），多个并发调用瞬间放大网络与 TShock 解析负载；ban 路径里这块本身就是性能热点。
- **建议**: 与 `large_image.semaphore_for(...)` 模式一致，给 `ban_core` 加一个模块级 `_blacklist_semaphores: dict[int, asyncio.Semaphore]`，每台 max_concurrent=1；同时把“先 GET 整个 blacklist 判重”改成短期内存缓存（10s TTL）或干脆依赖 add 的幂等性（TShock 端若已存在该 username 的封禁，让 add 返回 `already_exists` 错误码即可）。

### SC-4.3 🟡 medium — `BanDBResult.user_qq` 是 `str`，但 `apply_ban_to_db` 在 `not_found` 路径返回 `user_qq=""`，调用方不能区分 owner 自封 vs 其他错误

- **位置**: `ban_core.py:21-58`
- **现状**: `not_found` 时 `BanDBResult(code="not_found")`（user_qq 默认 `""`）；`owner_protected` 时 `user_qq=str(user.user_id)`；`already_banned` 同理。`ban.py:78-82` 调用方把所有 `not_found` 都映射到“未找到该用户”，没问题；但日志层面 `not_found` 没记录 `user_id` 输入参数，丢失了诊断信息。
- **建议**: `not_found` 时也传回入参 user_id（即调用者输入），便于日志：
  ```python
  if user is None:
      logger.warning(f"封禁失败：user 不存在 user_id={user_id}")
      return BanDBResult(code="not_found", user_qq=user_id)
  ```

### SC-4.4 🟡 medium — `db_now_utc_naive()` 在 owner_protected / already_banned 分支被白白调用是可避免的（**实际没问题**），但 `apply_ban_to_db` 在 owner_protected 后没 commit 也没 rollback，留 ORM 对象 dirty

- **位置**: `ban_core.py:35-47`
- **现状**:
  ```python
  if str(user.user_id) in get_owner_ids():
      return BanDBResult(code="owner_protected", ...)   # ← session 没 close (在 finally), 但若有 autoflush 之类副作用, 可能…实际 autoflush=False, 安全
  ```
  当前实现安全（autoflush=False、未对 user 任何属性赋值）；但若未来代码维护者在 owner_protected 分支前增加 `user.last_ban_attempt = ...` 等无意改动，会被 finally 中的 session.close 隐式 rollback，行为不直观。
- **建议**: 在每个早期 return 前显式 `session.rollback()`，或让所有 return 路径走相同结构（统一 commit 位置）。

### SC-4.5 🟡 medium — `apply_ban_to_db` 没有日志记录“封禁原因被 owner 保护拒绝”

- **位置**: `ban_core.py:35-40`
- **现状**: owner_protected 直接 return，调用方在 `ban.py:81-83` 只发了一条 reply_failure，没有 logger.warning。
- **影响**: 安全审计场景下，**“有人尝试封禁 owner”是一个值得报警的事件**——可能是恶意 admin、可能是脚本错误、也可能是被盗号 admin。当前完全无日志。
- **建议**:
  ```python
  if str(user.user_id) in get_owner_ids():
      logger.warning(
          f"封禁尝试被 owner 保护拒绝：target_user_id={user_id} target_name={user.name}"
      )
      return BanDBResult(...)
  ```
  并要求调用方在 reply 之外也 `logger.warning(f"... operator_id={event.get_user_id()}")`。

### SC-4.6 🟢 low — 公共模块缺少 `apply_unban_to_db` 对偶函数，导致 `ban.py:handle_unban` 直接操作 ORM

- **位置**: `ban_core.py` 全文
- **现状**: 模块只暴露 `apply_ban_to_db` + `sync_user_to_blacklist`，没有解封函数。`ban.py:207-315` 自己写 ORM、commit、fan-out。`webui_users.py:650-732` 又是另一份独立实现。三处行为漂移风险高。
- **建议**: 抽 `apply_unban_to_db(user_id) -> BanDBResult`、`sync_user_blacklist_remove(user_name) -> list[str]`。让 ban.py / WebUI / group_member_notify 共享同一份。

### SC-4.7 ℹ️ info — `BanDBResult.user_qq` 与 `User.user_id` 类型已约定为 `str`，但模块内 `str(user.user_id)` 出现 4 次

- **位置**: `ban_core.py:35`、`39`、`45`、`55`
- **现状**: SQLAlchemy ORM 列 `user_id: Mapped[str]`，理论已是 `str`，但显式 `str(...)` 包了 4 次，是 defensive coding；保留无害。
- **建议**: 无需修改。

---

## 5. 跨切面发现

### 5.1 与已修复模式的对照表

| 已修复模式 | 修复点位 | ban.py / ban_core.py 现状 | 影响位 |
|---|---|---|---|
| **lost-update on User column → 条件 UPDATE** | `economy.py:192` | 未应用 | SB-1.4 / SB-3.3 |
| **DB 写成功 + 外部 API 全失败 → CRITICAL log + 用户提示** | warehouse W-7（已发布） | 未应用 | SB-1.1 / SB-3.2 |
| **fan-out 串行 → asyncio.gather** | `player_query.py:260` (PQA-1.1) | 未应用 | SB-1.3 / SB-3.4 / SC-4.2 |
| **TOCTOU → 在事务内 re-read** | `player_query.py:411` (PQB) | 解封路径未应用（仅一段 session） | SB-3.1 |
| **temp file 碰撞 → uuid 后缀** | `screenshot_temp.py:32` | **已应用**（uuid 已防同名） | — |
| **大 base64 OOM → MAX_BASE64_BYTES** | `player_query.py:762` (PQB-1.1) | **未应用**于 `_to_base64_image_uri` | SB-2.2 |
| **per-server semaphore 防 OOM** | `player_query.py:70 _inventory_semaphores` | **未应用**于 `封禁列表` 与 `sync_user_to_blacklist` | SB-2.2 / SC-4.2 |
| **TShock URL 路径段 quote(safe="")** | `player_query.py:427` (PQB-2.2) | 未应用 | SB-1.2 / SB-3.5 |

### 5.2 owner / admin 权限边界总结

- ✅ `apply_ban_to_db` 拒绝 owner 被封（`ban_core.py:35`）。
- ❌ 没有“管理员封禁自己”的拦截（admin 把自己加进黑名单后再也无法解封自己；自服务故障）。
- ❌ 没有“管理员 A 封禁管理员 B”的等级保护（项目无群组等级时合理，但应在文档中明确）。
- ❌ `ban.list` 默认对 guest 开放（SB-2.1）。
- ❌ `admin.unban` 没有 owner 双签 / 二次确认；管理员被攻陷后单条命令可解封任意账号。

### 5.3 重复 import 与代码漂移

- `ban.py:11` `from nextbot.access_control import get_owner_ids`：未使用（owner 检查已迁入 `ban_core.py`）。
- `ban.py:18` `from nextbot.time_utils import db_now_utc_naive`：未使用。
- `ban.py:217-315` 的解封逻辑与 `webui_users.py:650-732` 几乎逐行同构，但日志风格、错误文案不同（`reply_failure` vs `api_error`）。建议两者都迁入 `ban_core.apply_unban_to_db` + `ban_core.sync_user_blacklist_remove`。

### 5.4 建议的修复优先级（落地视角）

1. **P0**：SB-1.1 / SB-3.2（critical，永久状态漂移、无告警）→ 重构 `ban_core` 提供聚合函数 + critical log。
2. **P0**：SB-3.1（critical，解封事务断点静默丢消息）→ commit 前先 capture user_name / user_qq。
3. **P0**：SB-2.1（high，guest 可看封禁列表）→ 移出 `DEFAULT_GUEST_PERMISSIONS`。
4. **P1**：SB-1.2 / SB-3.5（high，TShock URL 路径未编码）→ `quote(safe="")`。
5. **P1**：SB-1.3 / SB-3.4 / SC-4.2（high，fan-out 串行）→ `asyncio.gather` + per-server semaphore。
6. **P1**：SB-1.4 / SB-3.3（high，read-modify-write 并发覆盖）→ 条件 UPDATE。
7. **P2**：SB-2.2（high，封禁列表无 OOM 上限）→ size check + semaphore。
8. **P2**：SB-2.4（medium，全表 ORM 物化）→ 改 `count() + offset/limit`。
9. **P3**：SB-1.5 / SB-3.6 / SC-4.5（medium，审计日志缺操作员）→ 统一加 `operator_id` 字段。
10. **P3**：剩余 low / info 项随同上一并修。
