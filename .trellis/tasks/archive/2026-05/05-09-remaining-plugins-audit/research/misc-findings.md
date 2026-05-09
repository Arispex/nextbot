# Research: 5 个杂项 plugin 文件的 Sweep 审计

- **Query**: 对 about.py / tutorial.py / rob_protection.py / group_member_notify.py / menu.py 做 final sweep
- **Scope**: internal
- **Date**: 2026-05-09

---

## 总览

| 文件 | 行数 | 类别 | guest 可调 | 截图 | 主要风险 |
|---|---|---|---|---|---|
| `nextbot/plugins/about.py` | 73 | 系统功能 | ✅（`about` ∈ DEFAULT_GUEST_PERMISSIONS） | ✅ | 缺 base64 size cap、缺 handler-wide semaphore |
| `nextbot/plugins/tutorial.py` | 121 | 系统功能 | ✅（`system.tutorial`） | ✅ | 同 about + payload 比 about 大（vp 1400） |
| `nextbot/plugins/menu.py` | 251 | 系统功能 | ✅（`menu.root` / `menu.search`） | ✅ | 同上 + 截图 viewport 1920×1280 + full_page，最大 |
| `nextbot/plugins/rob_protection.py` | 150 | 小游戏系统 | ❌（默认无） | ❌ | 整体已用条件 UPDATE，仅有 1 处 commit 后的二次 SELECT 可省 |
| `nextbot/plugins/group_member_notify.py` | 212 | 被动 notice | n/a | ❌ | `on_notice()` 三次注册导致同一事件被重复处理；自动封禁未走审计；事件类型未校验 |

---

## 文件 1：`nextbot/plugins/about.py`

### MI-1.1 🟠 截图 handler 缺 handler-wide semaphore（DoS 放大）

- **File**: `nextbot/plugins/about.py:34-73`
- **Current**:
  ```python
  @about_matcher.handle()
  @command_control(... permission="about" ...)
  @require_permission("about")
  async def handle_about(...):
      ...
      async with temp_screenshot_path("about") as screenshot_path:
          await screenshot_url(page_url, screenshot_path, options=ABOUT_SCREENSHOT_OPTIONS)
  ```
- **Impact**: `about` ∈ `DEFAULT_GUEST_PERMISSIONS`（`db.py:36`），任何注册过的群友都能调；handler 内 **无** semaphore，没有任何并发上限。10 个 guest 同时发 `关于` 即并发起 10 个 Playwright 渲染任务。对照 `nextbot/plugins/ban.py:50` 的 `_ban_list_semaphore = asyncio.Semaphore(2)`，明确说明这是 SB-2.2 修复"guest 高频刷命令导致 Playwright 进程膨胀"的范式。
- **复现**: 8 个不同 QQ 同时发 `关于`，观察 Playwright 子进程峰值。
- **修法**: 在模块顶部加 `_about_semaphore = asyncio.Semaphore(2)`，并在 `handle_about` 包裹截图块外层。

### MI-1.2 🟠 缺 base64 size cap（OOM 防护遗漏）

- **File**: `nextbot/plugins/about.py:64-71`
- **Current**:
  ```python
  if bot.adapter.get_name() == "OneBot V11":
      try:
          image_uri = _to_base64_image_uri(screenshot_path)
      except OSError:
          await bot.send(event, reply_failure("生成", "读取截图文件失败"))
          return
      await bot.send(event, OBV11MessageSegment.image(file=image_uri))
  ```
- **Impact**: 直接 `read_bytes()` + base64 编码，未做 size 预判。对照 `permission_manager.py:662-676` 的修复模式：先 `path.stat().st_size`，预估 `file_size * 4 // 3 > MAX_BASE64_BYTES` 即拒绝。`MAX_BASE64_BYTES = 200 * 1024 * 1024` (`nextbot/large_image.py:17`)。`ABOUT_SCREENSHOT_OPTIONS` 用了 `full_page=True, fit_content_height=True`，恶意 / bug 模板可能渲染出超大页面。
- **复现**: 触发后端 about 模板返回包含超大 SVG / image 元素的页面 → screenshot_url 产出几百 MB png → `read_bytes()` + base64 → 进程内存翻 8 倍。
- **修法**: 复用 `nextbot.large_image.MAX_BASE64_BYTES`，参照 `permission_manager.py:662-676` 的 stat + 预判 + reply_failure 三步。

### MI-1.3 🟢 文案 / helper 复用情况

- 73 行有自己的 `_to_base64_image_uri`（`about.py:28-31`）；同样的函数在 `tutorial.py:31-34`、`menu.py:85-88`、`ban.py:53-56`、`permission_manager.py` 都重复出现。
- 不是本次 sweep 的核心风险，但已是项目 long tail tech debt（5+ 处复制）。建议提取到 `nextbot/large_image.py` 或新建 `nextbot/screenshot_image.py`，与 size cap 一起统一。

---

## 文件 2：`nextbot/plugins/tutorial.py`

### MI-2.1 🟠 截图 handler 缺 semaphore（同 MI-1.1）

- **File**: `nextbot/plugins/tutorial.py:37-121`
- **Impact**: `system.tutorial` ∈ `DEFAULT_GUEST_PERMISSIONS`（`db.py:85`），与 `about` 同等暴露面；`TUTORIAL_SCREENSHOT_OPTIONS` viewport 1400 高度，比 about (800) 更大，渲染更慢，DoS 放大更明显。
- **修法**: 加 `_tutorial_semaphore = asyncio.Semaphore(2)` 并包裹截图块。

### MI-2.2 🟠 缺 base64 size cap（同 MI-1.2）

- **File**: `nextbot/plugins/tutorial.py:112-119`
- **Impact**: 同 about，直接 `read_bytes()` 无 size 预判。tutorial 模板含用户自定义 `self_user_id`，传入 `create_tutorial_page` 后被嵌入 chat avatar `__SELF__` 渲染——若头像 URL 走外部资源被恶意托管 GIF/超大图，输出 png 体积可控。
- **修法**: 同 MI-1.2，参考 `permission_manager.py:662-676`。

### MI-2.3 ℹ️ 输入 `selector` 解析顺序

- **File**: `nextbot/plugins/tutorial.py:74-82`
- 数字优先匹配 1-N，否则按 slug 查 `get_tutorial(selector)`。逻辑无注入风险（`tutorial_data.py` 是模块级 dict，不接 SQL）。`if 1 <= idx <= len(tutorials)` 边界正确。无需修改。

### MI-2.4 ℹ️ `selector.isdigit()` 不接受空字符

- **File**: `nextbot/plugins/tutorial.py:74`
- `args[0].strip()` 可能产生空串。空串 `isdigit()` 返回 False → 走 `get_tutorial("")` → 应该返回 None → 走"未找到"路径。逻辑安全。

---

## 文件 3：`nextbot/plugins/menu.py`

### MI-3.1 🟠 截图 handler 缺 semaphore + 截图体量最大

- **File**: `nextbot/plugins/menu.py:148-216`、`91-127`
- **Current**:
  ```python
  MENU_SCREENSHOT_OPTIONS = ScreenshotOptions(
      viewport_width=1920,
      viewport_height=1280,
      full_page=True,
      fit_content_height=True,
  )
  ```
- **Impact**: 三件事叠加：
  1. `menu.root` / `menu.search` ∈ `DEFAULT_GUEST_PERMISSIONS`（`db.py:68-69`），全开。
  2. `viewport_width=1920` 是所有截图 handler 中**最宽**的（about 920、tutorial 920、ban 920、admin_list 920）。
  3. `full_page=True, fit_content_height=True`，意味着会一直拉到 DOM 最底，命令最多分类（"小游戏系统"内能放几十条命令）会渲染出极长页面。
- **复现**: 多个 guest 同时发 `菜单 1` 命令，观察 Playwright RAM 与 png 大小。
- **修法**: `_menu_semaphore = asyncio.Semaphore(2)` 包裹 `_render_and_send_menu`。同时建议 `viewport_width=920`（与项目其他截图一致）。

### MI-3.2 🟠 缺 base64 size cap（同 MI-1.2，且因体量最大风险更高）

- **File**: `nextbot/plugins/menu.py:118-125`
- **Impact**: 同 about / tutorial。menu 截图体积是这三者中最大的，没有 size cap 等于最薄弱处不设防。
- **修法**: 复用 `MAX_BASE64_BYTES`，参考 `permission_manager.py:662-676`。

### MI-3.3 🟡 `search_command_matcher` 用 `keyword in display_name` 全表扫描

- **File**: `nextbot/plugins/menu.py:240-247`
- **Current**:
  ```python
  all_items = list_command_configs()
  matched = [
      item for item in all_items
      if keyword in str(item.get("display_name", ""))
  ]
  ```
- **Impact**: `list_command_configs()`（`command_config.py:571-580`）走的是 `_runtime_cache`（内存 dict），不是 SQL 查询，所以**没有 N+1 / 全表 SELECT 风险**。但每次 search 都会触发 `_serialize_runtime_state` + `_clone_dict` 序列化所有命令，对每条命令做深拷贝。当前命令数 ~80 量级，无性能问题。无需修改，仅作记录。

### MI-3.4 ℹ️ keyword 长度未校验

- **File**: `nextbot/plugins/menu.py:236-238`
- 只校验非空，未限制长度。理论上巨长 keyword 不会匹配任何 display_name，开销可控（短路 `in`）。无修改建议。

### MI-3.5 ℹ️ `selector` 走 `if selector in by_cat` 是字典 key 精确匹配

- **File**: `nextbot/plugins/menu.py:194`
- `by_cat` 的 key 来自 `command_control(category=...)`，是开发者声明的字符串。无注入面。

---

## 文件 4：`nextbot/plugins/rob_protection.py`

### MI-4.1 ✅ 已正确使用条件 UPDATE 防 lost-update

- **File**: `nextbot/plugins/rob_protection.py:87-99`
- **Current**:
  ```python
  rowcount = execute_rowcount(
      session,
      update(User)
      .where(
          User.user_id == user_id,
          User.coins >= cost,
          User.rob_protected.is_(not target),
      )
      .values(coins=User.coins - cost, rob_protected=target),
  )
  ```
- **Impact**: 单条 UPDATE 同时校验"目标状态需为 `not target`"+"金币足"+"扣费切换"，并发场景下第二条 rowcount=0 被 SQL 层兜底。这正是 `rob.py` SB-1.4 / `ban_core.py:64-73` 的标准模式。**无 lost-update 风险**，无需修改。

### MI-4.2 🟡 commit 后的二次 SELECT 可省

- **File**: `nextbot/plugins/rob_protection.py:117-124`
- **Current**:
  ```python
  session.commit()
  current_coins = int(
      session.query(User.coins).filter(User.user_id == user_id).scalar() or 0
  )
  name = str(
      session.query(User.name).filter(User.user_id == user_id).scalar() or ""
  )
  ```
- **Impact**: 切换花费固定，可在 UPDATE 前 capture name + coins，再算 `current_coins = old_coins - cost` 直接显示，省两条 SELECT。但当前不是 lost-update / 正确性问题，仅微优化。
- **复现**: 切换瞬间另一个 handler 也修改了 user.name → 返回的 name 是新版本，与 actor 看到的状态略有不一致；非安全问题。
- **修法**: 在第 80 行 SELECT 时同时捕获 `original_name = user.name, original_coins = int(user.coins or 0)`，UPDATE 成功后用 `original_coins - cost`。可与 ban_core SB-3.1 模式对齐。

### MI-4.3 🟢 自身保护，无越权面

- 命令只针对 `event.get_user_id()` 自己的 `User.rob_protected`，没有 target 用户参数。不存在越权设置他人保护的入口。

### MI-4.4 ℹ️ `cost` 上限校验位置

- **File**: `nextbot/plugins/rob_protection.py:69-75`
- `cost = _safe_param_int("toggle_cost", 200, min_value=0)` 后立即 `if cost > MAX_COINS_AMOUNT` 拒绝。`MAX_COINS_AMOUNT = 100_000_000` (`economy.py:39`)。逻辑安全。

### MI-4.5 ℹ️ `category="小游戏系统"` 已分类

- 之前小游戏审计可能确实漏了这文件。category 标签在 `command_config` 里 = `"小游戏系统"`，**未在 `DEFAULT_GUEST_PERMISSIONS`**——只有显式分配 `economy.rob_protection` 权限的组才能调。比 about / tutorial / menu 更收敛。

---

## 文件 5：`nextbot/plugins/group_member_notify.py`

### MI-5.1 🔴 三个 `on_notice()` 无 filter，每个 notice 都被 3 次匹配

- **File**: `nextbot/plugins/group_member_notify.py:32-34`
- **Current**:
  ```python
  increase_matcher = on_notice()
  decrease_matcher = on_notice()
  auto_ban_on_leave_matcher = on_notice()
  ```
- **Impact**: nonebot 的 `on_notice()` **不带任何过滤**，注册了三次意味着**任何 NoticeEvent**（friend_add、group_recall、ban、poke、honor、red_packet、好友 / 群 like 等）都会**依次触发 3 个 handler**。每个 handler 内部用 `isinstance(event, GroupIncreaseNoticeEvent)` 隐式靠类型注解？——**并不会自动过滤**：nonebot 把 type-annotation 上的 EventModel 子类视为 dependency hint，但 `on_notice()` 注册的 matcher 本身仍会被任意 NoticeEvent 触发。`handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent)` 在收到非 increase notice 时，nonebot 会尝试 cast `event` 失败，handler 不执行——**好的一面是**功能性 OK；**坏的一面是**：

  1. 每条 notice 都进入 3 个 handler 的 dispatch 阶段，浪费 CPU。
  2. 当 nonebot 升级或 driver 改行为时，可能从"不执行"变成"执行错事件"。
  3. **目前实际错误**：`auto_ban_on_leave_matcher` 注解为 `GroupDecreaseNoticeEvent`，与 `decrease_matcher` 同一事件类型，**两个 handler 都会响应同一条 GroupDecreaseNoticeEvent**——这是有意的（一个发送告别消息，一个执行自动封禁），不是 bug。但因为没有 priority 控制，二者顺序未定。
- **复现**: 任意 `on_notice` 被触发的 notice 类型（如 `notify` / `poke`）会进入 3 个 handler 的依赖注入阶段，可在 nonebot debug log 中观察。
- **修法**: 用 nonebot 提供的 `on_notice(rule=...)` 或事件类型过滤器，最直接的写法是参考其他项目用 `on_notice` 时显式判断：
  ```python
  from nonebot.rule import Rule

  async def _is_increase(event: Event) -> bool:
      return isinstance(event, GroupIncreaseNoticeEvent)
  increase_matcher = on_notice(rule=Rule(_is_increase))
  ```
  或检查 nonebot.adapters.onebot.v11 是否提供 `on_notice` 子事件 helper（旧版有 `GroupIncreaseEventType` 之类的），优先用 adapter 内置过滤。

### MI-5.2 🟠 `auto_ban_on_leave` 未走 `audit_permission_change` 审计

- **File**: `nextbot/plugins/group_member_notify.py:151-194`
- **Current**: 退群自动封禁直接调用 `apply_ban_to_db(user_id, reason)` + `sync_user_to_blacklist(...)`，仅有 `logger.info(...)` 业务日志，**没有写权限审计行**。
- **Impact**: 项目其他所有 mutating handler（`permission_manager.py:154,163,279,420,523,584,827,1001` 等 9 处、`group_manager.py` 多处）都通过 `audit_permission_change(actor=, action=, target=, before=, after=, context=)` 集中记审。被动事件触发 ban 是状态变更最敏感的类别（用户连命令都没发就被封禁），**反而绕过了审计入口**。这与 `nextbot/audit.py` 的 docstring "所有 permission-mutating handler 应通过 audit_permission_change() 记录变更" 直接冲突。
- **复现**: 退群事件触发 → 用户被封禁 → 审计日志（grep `权限审计`）找不到这次 ban；只有业务 INFO 日志。
- **修法**: 在 `apply_ban_to_db` 返回 `code="banned"` 后调用：
  ```python
  audit_permission_change(
      actor_user_id="system",  # 或 str(bot.self_id)
      action="user.ban.auto_on_leave",
      target=str(user_id),
      before="active",
      after="banned",
      context={"group_id": event.group_id, "sub_type": sub_type, "reason": reason},
  )
  ```

### MI-5.3 🟠 `_lookup_user_name_and_ban_status` 与 `apply_ban_to_db` 之间的 TOCTOU

- **File**: `nextbot/plugins/group_member_notify.py:140-148`、`168-188`
- **Current**:
  ```python
  user_name, already_banned = _lookup_user_name_and_ban_status(user_id)  # 第 168 行
  if user_name is None: return ...
  if already_banned: return ...
  ...
  result = apply_ban_to_db(user_id, reason)  # 第 183 行
  ```
- **Impact**: 在 SELECT 与 ban 之间存在 race window。如果在两步之间另一个管理员 unban 了这个目标，`_lookup_user_name_and_ban_status` 返回 `already_banned=True` 导致跳过；或者另一个 admin ban 了，apply_ban 返回 `already_banned`，本路径 `if result.code != "banned"` 走 `logger.warning + return`。**功能正确**（apply_ban_to_db 内部有 owner 保护 + 条件 UPDATE 兜底），但提前 SELECT 等同于做了 read-then-check，已经被 SQL 层兜底覆盖，**SELECT 可省**。
- **修法**: 直接调 `apply_ban_to_db(user_id, reason)`，根据返回 code 分流：
  - `not_found` → 改 log "退群跳过未注册用户"
  - `owner_protected` → 改 log "退群跳过 Owner"（顺便覆盖目前第 162-166 行需要事先 `get_owner_ids()` 的 IO）
  - `already_banned` → 改 log "退群跳过已封禁用户"
  - `banned` → 走 sync_user_to_blacklist
  少一次 SELECT，且单一信息源。

### MI-5.4 🟡 `event` 没有 `isinstance(event, GroupDecreaseNoticeEvent)` 类型守卫

- **File**: `nextbot/plugins/group_member_notify.py:152-160`
- **Current**:
  ```python
  async def handle_auto_ban_on_leave(bot: Bot, event: GroupDecreaseNoticeEvent) -> None:
      if not isinstance(bot, OBV11Bot):
          return
      if not _group_allowed(event.group_id):
          return
  ```
- **Impact**: 只校验 `bot` 是 OBV11Bot，未校验 `event` 是 `GroupDecreaseNoticeEvent`。如果 MI-5.1 的过滤问题暴露，可能传入非 decrease 事件，`event.group_id` / `event.user_id` / `event.sub_type` 在其他 NoticeEvent 上可能不存在 → AttributeError → 进入 handler 异常但被 nonebot 兜底吃掉。
- **修法**: 加 `if not isinstance(event, GroupDecreaseNoticeEvent): return` 作为防御。如果 MI-5.1 已通过 rule 修复，本项可降为 ℹ️。

### MI-5.5 🟡 `_render` 在所有 chunk 为空 + 无 `{at}` 占位时返回空 Message

- **File**: `nextbot/plugins/group_member_notify.py:65-75`、`78-104`
- **Current**:
  ```python
  def _render(template, ...) -> OBV11Message:
      ...
      parts = text.split("{at}")
      message = OBV11Message()
      for i, chunk in enumerate(parts):
          if chunk:
              message += OBV11MessageSegment.text(chunk)
          if i < len(parts) - 1:
              message += OBV11MessageSegment.at(user_id)
      return message
  ```
  调用方 `_send_group_notify` 第 95 行：
  ```python
  if not message:
      return
  ```
- **Impact**: `OBV11Message` 是 `list` 子类，empty list 的 `not message` 判定为 True，正确短路；不会发送空消息。但**模板被 `_unescape` + `replace` 处理后可能产生纯空白字符串** → `OBV11MessageSegment.text(" ")` 仍然是非空段，会被发送出去（看似空消息但 onebot 会被踢退）。
- **修法**: `if chunk.strip():` 取代 `if chunk:`；或者在 `_load_template` 后 `if not template.strip(): return` 提前短路。

### MI-5.6 ℹ️ 自动注册的并发安全问题不存在

- audit prompt 提到"自动注册新成员的并发安全（IntegrityError 捕获）"——通读全文件，**没有任何 auto-register 代码**：`handle_group_increase` 仅发送 welcome 模板，不触碰 User 表。注册路径只在 `user_manager.py` 的 `add_matcher = on_command("注册账号")`。本项无风险。

### MI-5.7 ℹ️ `_unescape` 实现略 hacky

- **File**: `nextbot/plugins/group_member_notify.py:44-45`
- 用 `\x00` 做中间占位避免 `\\\\` 与 `\\n` 混淆。逻辑正确但建议加单元测试覆盖如 `\\\\\\n` (三反斜+ n) 场景。非安全问题。

### MI-5.8 🟡 `_fetch_nickname` 失败仅 warning，nickname 用 user_id 兜底

- **File**: `nextbot/plugins/group_member_notify.py:54-62`、`66`
- 已经有 `try/except + logger.warning + return ""`，调用方 `display_nick = nickname or str(user_id)` 兜底。已防御，无需修改。

---

## 跨文件复用情况汇总

| Helper | 使用情况 |
|---|---|
| `temp_screenshot_path` | ✅ about / tutorial / menu 都正确使用 uuid suffix（PQA-3.1） |
| `MAX_BASE64_BYTES` | ❌ about / tutorial / menu **均未** 使用，仅 `ban.py:232` / `permission_manager.py:669` 用了 |
| handler-wide semaphore | ❌ about / tutorial / menu 都没有；`ban.py:50` 是范式 |
| `audit_permission_change` | ❌ `group_member_notify.handle_auto_ban_on_leave` 未调用，与项目其他 mutating handler 不一致 |
| `apply_ban_to_db` | ✅ `group_member_notify` 使用方式正确（owner 保护已在 ban_core 内层兜底） |
| `_to_base64_image_uri` 重复 | 5+ 处复制，可统一到 `nextbot/large_image.py` |

## Caveats / Not Found

- 没有验证 nonebot 在 `on_notice()` 无 rule 情况下，对 type-annotated handler 是否有自动 dispatch 过滤。本审计假设的"任意 NoticeEvent 都进 dispatch 阶段"基于 nonebot 通常行为，建议实现修复时用 `Rule` 显式过滤，避免依赖隐式行为（更稳健）。
- `MENU_SCREENSHOT_OPTIONS.viewport_width=1920` 是否有特殊设计意图（如 desktop 仿真）未在代码 / 注释中找到说明，本审计建议降为 920 时需先与原作者确认。
- `rob_protection.py` MI-4.5 的"小游戏系统遗漏"——该文件确实有 `category="小游戏系统"`，但因权限 `economy.rob_protection` 不在 guest 默认列表，攻击面比 about/menu/tutorial 小一个量级。
