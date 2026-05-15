# Bucket C — render pages backend audit (server/pages/*.py)

- **Query**: 安全 / 性能 / UX / 文案审计 — `server/pages/` 渲染页后端
- **Scope**: internal only — 仅 `server/pages/*.py`（17 files，含 `__init__.py` + `console_page.py` 仅看非 nav 部分）
- **Date**: 2026-05-15
- **Files audited (17)**: `__init__.py`, `about_page.py`, `admin_list_page.py`, `ban_list_page.py`, `console_page.py`, `inventory_page.py`, `leaderboard_page.py`, `lottery_list_page.py`, `lottery_result_page.py`, `lottery_view_page.py`, `menu_page.py`, `progress_page.py`, `red_packet_all_page.py`, `red_packet_own_page.py`, `shop_list_page.py`, `shop_view_page.py`, `tutorial_page.py`, `user_info_page.py`, `warehouse_page.py`

---

## 渲染管线模型（全部 16 个游戏页一致）

所有 `<name>_page.py` 都是以下三段式：

1. `build_payload(...)` —— 把上游传入的 dict 字段归一化、强类型化、容器化。**关键防护**：所有用户可控字符串（player name, item name, shop name 等）都被 `str(...).strip()` 包装；数字字段都被 `int(...)/float(...)` 强转；非法项被 `if not isinstance(item, dict): continue` 过滤丢弃。
2. `render(payload)` —— `template = TEMPLATE_PATH.read_text(...)`，`json.dumps(data, ensure_ascii=False).replace("</", "<\\/")`，`template.replace("__XXX_DATA_JSON__", data_json)`，输出 utf-8 bytes。
3. **template 侧**（全 17 个 HTML）：占位符 `__XXX_DATA_JSON__` 都被嵌入 `<script id="..." type="application/json">...</script>`；客户端 JS 用 `JSON.parse(document.getElementById(...).textContent)` 读取，然后**严格使用 `textContent` + `createElement`** 注入到 DOM（grep 危险 DOM-write API 全模板 0 命中）。

**这是 XSS 安全的"标准三明治"**：数据→JSON→`<script type=application/json>`→`JSON.parse`→`textContent`。`</` → `<\/` 替换阻断了 `</script>` 跳出。这是本次审计最重要的结论 —— **本 bucket 没有传统 HTML 模板拼接 XSS**。

`console_page.py` 走的是另一条路径：WebUI app shell，受信字面量直接 `html.escape(quote=True)`，文档里有显式注释（line 47-59）声明"模板信任假设"。已审计过，没有 page 数据进 console，仅 WebUI 链接 / 主题脚本 URL（同样 `html.escape`），无 finding。

---

## Findings

合计 14 finding，按严重度排序。

| # | 严重 | 维度 | 标题 | 文件 |
|---|---|---|---|---|
| 1 | High | Perf | `render()` 每次请求同步从磁盘读模板 + 阻塞 async 事件循环 | 全 17 |
| 2 | Medium | Security | `tutorial_page._resolve_avatar` 对未知 placeholder 透传 `raw` 到 `img.src`，无 URL scheme 白名单 | `tutorial_page.py:15-21` |
| 3 | Medium | Perf | 多数页面对 `entries` / `prizes` / `items` / `outcomes` 列表无服务端长度上限 | 12 个 page |
| 4 | Medium | Robustness | 4 个分页 page 缺 `max(1, ...)` 兜底 | `ban_list_page.py`, `red_packet_all_page.py`, `red_packet_own_page.py`, `leaderboard_page.py` |
| 5 | Medium | Robustness | `inventory_page._normalize_slots` 写死 350 slot 数 + 静默丢弃 `slot >= 350` 项 | `inventory_page.py:22-23` |
| 6 | Low | Robustness | `render` 内 `OSError` 之外的异常不被捕获，由 FastAPI 500 兜底 | `server/routes/render.py:33-36` (跨模块，本 bucket 外) |
| 7 | Low | Robustness | 多个 page 仅 `int(...)` 强转，未 `try/except` —— 上游传入非数字字符串会直接 500 | `user_info_page.py:30-37`, `leaderboard_page.py:26-32`, 等 |
| 8 | Low | Copy | `_normalize_entries` 兜底文案"未命名商店"/"未命名奖池" 不一致 | `shop_list_page.py:26`, `lottery_list_page.py:28` |
| 9 | Low | Copy | `target_server_label` 默认值 `"全部服务器"` 兜底两处重复字面量 | `shop_view_page.py:53`, `lottery_view_page.py:51` |
| 10 | Low | UX | `admin_list_page.build_payload` 不返回 `total` / 空态字段（template 自带空态，可接受但接口不对齐其他 list page） | `admin_list_page.py` |
| 11 | Low | UX | `tutorial_page._resolve_name` 兜底 `"未命名"` ——脚本字面 fallback，与其它 page 的空串行为不一致 | `tutorial_page.py:24-26` |
| 12 | Low | Security/Logging | 全 bucket 0 行日志 —— 渲染失败 / 大 payload / 异常分支无可观测性 | 全 17 |
| 13 | Low | Robustness | `lottery_result_page` `_normalize_outcomes` 对 `coin_amount` 强转失败 `continue` 跳过整条 outcome 而非降级为 0 —— 玩家可能看到丢条目 | `lottery_result_page.py:69-72` |
| 14 | Info | Security | JS 行分隔符 U+2028 / U+2029 在 JSON 里无需额外转义；当前管线使用 `JSON.parse(textContent)` 而非 JS 表达式求值，无风险 | 全 17 |

---

## High

### 1. 同步模板读 + 阻塞 async 事件循环 / 无模板缓存

- **位置**：全 17 个 page 的 `render()` 第一行均为 `template = TEMPLATE_PATH.read_text(encoding="utf-8")`；`server/routes/render.py:34` 处 `content = renderer(payload)` 在 async handler 内同步调用。
- **现象**：每个 `/render/<type>/<token>` 请求都从磁盘重新读模板（5–18 KB），HTTP 服务进程下这会**在 asyncio 事件循环主线程上阻塞 I/O**。截图链路峰值（玩家批量查仓库、商店、抽奖结果）会顺序阻塞其它请求。
- **数据**：17 templates 总 ~203 KB；最大 `lottery_result.html` 18 KB。生产里每个 SSD 读 < 1 ms，HDD / 容器 mount / NFS 上可能 10–50 ms × 并发数。
- **修复**（最小硬化，不破坏 hot reload）：
  - 方案 A：模块级 `_TEMPLATE_CACHE: dict[Path, str] = {}`，按 `path.stat().st_mtime_ns` 失效；命中 cache 直接返回字符串。
  - 方案 B：进程启动时一次性预读所有模板到内存。
  - 方案 C：把同步 `read_text` 放到 `asyncio.to_thread(...)` 里（仅缓解阻塞，不去 IO）。
  - 推荐 A —— 不影响开发期 hot reload，且生产几乎 0 IO。
- **严重度评估**：单次开销小，但 17 个端点 × `<script type=application/json>` 块由 playwright 截图，平均每次截图触发 2–3 次模板渲染（首屏 + 字体加载 + 重渲染）；High 是因为简单可治。

---

## Medium

### 2. `tutorial_page._resolve_avatar` 对未知 placeholder 透传 `raw` 到 `img.src`

- **位置**：`server/pages/tutorial_page.py:15-21`
  ```python
  def _resolve_avatar(placeholder: str, self_user_id: str) -> str:
      raw = str(placeholder or "").strip()
      if raw == "__SELF__":
          return f"http://q1.qlogo.cn/g?b=qq&nk={self_user_id}&s=100"
      if raw == "__BOT__":
          return _BOT_AVATAR
      return raw   # <-- 透传任意字符串
  ```
  下游 `server/templates/tutorial.html:346-350` 直接 `img.src = avatarSrc;`。
- **风险**：如果 `chat[].avatar` 传入 `"javascript:alert(1)"` / `"data:text/html,..."`，浏览器 `img.src` 不会执行 `javascript:`（modern browsers），但 `data:` URL 可能被部分降级 UA 渲染。**当前生产数据全为静态字面量（`nextbot/plugins/tutorial_data.py` 仅 `__SELF__` / `__BOT__`）**，所以**实际未被利用**。但 sink 是开放的，未来若有动态 tutorial 内容（DB 拉取 / 配置 hot reload），会变成 stored XSS 风险。
- **修复**：白名单 scheme，或在 unknown placeholder 直接返回空串：
  ```python
  if raw == "__SELF__": return f"http://q1.qlogo.cn/g?b=qq&nk={self_user_id}&s=100"
  if raw == "__BOT__":  return _BOT_AVATAR
  return ""  # 不信任未声明 placeholder，回退到 fallback initial 'B'/'U'
  ```
- **同类排查**：grep `\.src\s*=` 全模板，所有其它 page 的 img.src 都来自 (a) 受信常量路径 `/assets/items/Item_${id}.png`（id 已 `int()` 化），(b) `q1.qlogo.cn?nk=${encodeURIComponent(...)}`，(c) `about.html:299 authorEl.href = data.author_url || "#"` —— `author_url` 在 `about_page.py:23` 硬编码 `"https://github.com/Arispex"`，无用户输入。**只有 tutorial 这条是开放 sink**。

### 3. 多数 list-page 对 `entries` 长度无服务端上限

- **位置**：以下 page 在 `build_payload` 中 `for item in entries: ... normalized.append(...)` 无 length cap：
  - `ban_list_page.py:19-31`
  - `red_packet_all_page.py:19-35`
  - `red_packet_own_page.py:19-39`
  - `leaderboard_page.py:22-34`
  - `menu_page.py:13-38`
  - `shop_list_page.py:13-29`
  - `shop_view_page.py:14-60`
  - `lottery_list_page.py:13-31`
  - `lottery_view_page.py:14-64`
  - `lottery_result_page.py:35-77`
  - `admin_list_page.py:13-27`
  - `inventory_page._normalize_slots` 硬编码 350 OK；`warehouse_page._normalize_slots` 硬编码 100 OK。
- **风险**：如果调用方分页 bug 把 10K row 全塞进来，render 会序列化 10K JSON entry → playwright 渲染 10K row → 截图 OOM / 超时。**调用链上游负责分页**，但页面层没有最后一道兜底。
- **修复**（最小硬化）：在 `_normalize_entries` 上加常量 `MAX_ENTRIES = 200`（或对齐 plugin 的 `PAGE_SIZE`），超出后 `break` + 不抛错（视觉上多余条目不显示）。**不推荐**抛异常，因为 caller 可能用同一个函数同时构造多页。

### 4. 4 个分页 page 缺 `max(1, ...)` 兜底

- **位置**：
  - `ban_list_page.py:34-35`: `"page": int(page), "total_pages": int(total_pages)`
  - `red_packet_all_page.py:38-39`: 同上
  - `red_packet_own_page.py:42-43`: 同上
  - `leaderboard_page.py:48-49`: 同上
- **对比**：`shop_list_page.py:43`, `lottery_list_page.py:45`, `shop_view_page.py:86`, `lottery_view_page.py:88` 都有 `max(1, int(page))` / `max(1, int(total_pages))`。
- **风险**：上游传 `page=-1` / `page=0` 会被原样落到 template，页脚展示 `第 0 页 / 共 -1 页`。**仅视觉**，无安全影响。
- **修复**：把这 4 处 `int(...)` 改成 `max(1, int(...))`，与其它 page 对齐。

### 5. `inventory_page._normalize_slots` 静默丢弃 `slot_index >= 350`

- **位置**：`server/pages/inventory_page.py:22-23`
  ```python
  if 0 <= slot_index < 350:
      slot_map[slot_index] = item
  ```
- **现象**：常量 `350` 写死（Terraria 主背包 + 装备 + 杂项格总数），若 caller 传入 slot 350+（trash / piggy bank）会被静默丢弃；调试期不易发现。
- **修复**：抽常量 `INVENTORY_SLOT_COUNT = 350` 到模块顶 + 文档里写明 magic number 来源（与 `warehouse_page.WAREHOUSE_CAPACITY = 100` 对齐风格）。

---

## Low

### 6. `render()` 异常仅 `OSError` 被路由层捕获

- **位置**：`server/routes/render.py:33-36`（**跨模块**，标 scope-out backlog）。
- **现象**：page 的 `render(payload)` 若抛 `KeyError` / `ValueError` / `TypeError`（理论上 `int(payload.get("page", 1))` 当 payload 被传入非法值时可能抛），未被路由层捕获，FastAPI 500 默认 handler 返回；DEBUG 模式可能泄露 traceback。
- **本 bucket 修复**：page 的 `render` 全部走 `int(payload.get(..., default))` —— 如果 payload 已经过 `build_payload` 归一化就不会失败。但 `get_page` 直接返回 store 里的 dict，理论上可被未来代码污染。
- **建议**：路由层 `except Exception` 兜底（**跨模块**，由 Bucket B 负责），page 层无需改。

### 7. 多个 page 的 `int(...)` 无 try/except 防御

- **位置**：
  - `user_info_page.py:30-37` `build_payload` 直接 `int(coins)` / `int(sign_streak)` 等。
  - `leaderboard_page.py:26-32` 处理 `value` 时区分 str vs int，但其它字段如 `int(item.get("rank", 0))` 无防御。
  - `progress_page.py:51-56` `build_payload`：`str(server_id)` OK，但 `defeated_count = sum(...)` 不防御。
- **对比**：`shop_list_page._normalize_entries` / `lottery_list_page` 都包了 `try: int(...) except (TypeError, ValueError): continue`。
- **风险**：caller 传入 `"abc"` 时直接抛 `ValueError`，路由层 500。和 #6 是同一类问题。
- **修复**：风格统一 —— 对 caller 信任度不同：
  - `build_payload` 来自**内部** plugin，可以 fail-fast（不改）。
  - `render` 来自 `page_store`，应该 defensive（已经是 `int(payload.get(..., 0))`，OK）。
  结论：不修，把"caller 必须传合法 int"作为内部契约，但建议在 `nextbot/plugins/*` 那边补类型检查（**跨模块**）。

### 8. 兜底文案"未命名商店"/"未命名奖池" 不一致

- **位置**：
  - `shop_list_page.py:26`: `str(raw.get("name", "")).strip() or "未命名商店"`
  - `lottery_list_page.py:28`: `... or "未命名奖池"`
  - 其它 page（`shop_view_page`, `lottery_view_page` 等）的 `name` 不兜底（保留空字符串，由 template 决定怎么处理）。
- **现象**：兜底点不统一 —— `shop_list` / `lottery_list` 显示"未命名商店"，`shop_view` 显示空。
- **修复**：统一两条策略：(a) 全部兜底（统一字面量"未命名"），(b) 全部不兜底（由 template 控制）。**推荐 (b)**：兜底应该在 plugin 层（数据源头），不是 render 层。把 `or "未命名商店"` / `or "未命名奖池"` 去掉。

### 9. `target_server_label` 默认值重复

- **位置**：`shop_view_page.py:53` 和 `lottery_view_page.py:51` 都有：
  ```python
  target_server_label = str(raw.get("target_server_label", "")).strip() or "全部服务器"
  ```
- **现象**：两处重复字面量，且这是"业务文案"硬编码在 render 层，**违反 CLAUDE.md "API 原始返回内容不得对字段值做业务化改写"**（虽然 render 层不算 API，但同款原则适用）。
- **修复**：上移到 plugin 层（caller 决定 label），render 层只透传。**或**：抽公共常量 `EMPTY_TARGET_SERVER_LABEL = "全部服务器"` 到 `nextbot/server_state.py` 类似公共模块。

### 10. `admin_list_page.build_payload` 接口不对齐其它 list

- **位置**：`admin_list_page.py:13-27` —— 只接 `admins`，没有 `page` / `total_pages` / `total`。
- **现象**：与 `ban_list` / `shop_list` 等其它 list 接口不一致。可能是因为 admin 数量必然很少（< 50），不需要分页。
- **修复**：不改。但记录在 spec 里：admin 列表显式约定不分页，避免日后 refactor 时混淆。

### 11. `tutorial_page._resolve_name` 兜底文案 "未命名"

- **位置**：`tutorial_page.py:24-26`
  ```python
  def _resolve_name(raw: str) -> str:
      value = str(raw or "").strip()
      return value or "未命名"
  ```
- **现象**：tutorial chat 里如果 name 为空就显示"未命名"，但其它 chat / list page 不兜底。同 #8。
- **修复**：去掉兜底（chat name 为空时不显示昵称行更自然，目前 template 也是 `if (nameStr) { ... }`，所以兜底其实**不必要**且违反原意，让原本"不显示昵称"的消息强制显示"未命名"行）。

### 12. 全 bucket 0 行日志

- **位置**：全 17 个 page 文件 `grep "logger\|logging\|traceback\|print" → 0 命中`。
- **现象**：渲染失败 / 大 payload / 跳过非法 entry 全部静默。CLAUDE.md 后端日志规则要求"关键入口 + 异常 + 关键决策分支"应有日志。
- **修复**：**保守起见不在 page 层加日志**（page 层是纯 transform，没有"对象"概念）。但 plugin 层调用 `build_payload` / `create_page` 时应该 log。属于**跨模块** backlog（plugin / `web_server.py` 责任）。

### 13. `lottery_result_page._normalize_outcomes` 整条丢弃 vs 字段降级

- **位置**：`server/pages/lottery_result_page.py:69-72`
  ```python
  elif kind == "coin":
      try:
          entry["coin_amount"] = int(raw.get("coin_amount", 0))
      except (TypeError, ValueError):
          continue   # <-- 整条 outcome 被丢弃
  ```
- **现象**：抽奖中奖 coin 字段格式错误时，整条 outcome 消失。玩家可能看到"抽到 5 个奖品但页面只显示 4 个"，**完全无法追踪**（无日志）。同款问题在 item 分支 `lottery_result_page.py:65-66`。
- **修复**：把 `continue` 改成 `entry["coin_amount"] = 0` —— 数据降级而不是丢弃，玩家至少看到一条 outcome（即使 coin=0），有调试痕迹。

### 14. JS line separator 注释

- **位置**：全 17 page 都用 `json.dumps(data, ensure_ascii=False).replace("</", "<\\/")`。
- **理论场景**：JS 字符串字面量里 U+2028 / U+2029 是非法换行（ES5），如果数据被直接拼到 `<script>const data = {...};</script>` 那种位置就会触发 syntax error。**这里不是那种场景**：数据进 `<script type="application/json">`，再由 `JSON.parse(textContent)` 解析，JSON 规范允许这两个码点出现在字符串里。
- **结论**：无实际风险。仅作为 info-level 提示给后续 reviewer —— 若后续重构改为在 `<script>` 块内直接绑定变量，则必须额外转义 ` ` / ` `。

---

## XSS 命中数：1（理论性，Medium 第 2 条 tutorial avatar）

**没有传统 HTML 模板拼接 XSS** —— 所有用户控制字符串都进 `<script type="application/json">` 数据块，下游 JS 用 `textContent` + `createElement` 渲染。唯一一处开放 sink（`tutorial_page._resolve_avatar` 透传到 `img.src`）当前数据全为静态字面量，**实际不可利用**，但作为代码契约属于"开放 sink"，应该收紧。

---

## 相关 spec / prior art

- `.trellis/tasks/archive/2026-05/05-04-audit-render-theme-cleanup/` —— 只做 theme 清理，**未做 security/perf**。本审计第一次覆盖。
- 无 `.trellis/spec/backend/render*.md` —— 建议补一份 spec "render page contract"：(1) JSON sandwich 模式，(2) caller 必须分页 + 长度上限，(3) template 必须用 `textContent`，禁止其它 DOM write API。

---

## Caveats / Not Found

- 未交叉验证 `nextbot/plugins/*` 各 caller 是否真的 paginated（**跨模块**）。
- 未对 playwright 截图链路做压测（不在 audit 范围）。
- `progress_page` 的 `progress: dict[str, Any]` 输入未排序 —— 字典遍历顺序由 Python 3.7+ 保证插入序，但 plugin 端如果改用 set 等结构会乱序。属于 plugin 契约，不在 page 层修。
- `lottery_result_page._rarity_tier` 用了硬编码阈值常量（`_TIER_LEGENDARY_MAX_PCT = 1.0` 等），如果未来奖池稀有度规则改变需同步改，**没问题但提醒后续**。
