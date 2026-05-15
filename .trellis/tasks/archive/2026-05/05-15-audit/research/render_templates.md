# Research: render templates audit (Bucket D)

- **Query**: 全量审计 `server/templates/*.html`（17 文件，~5K LOC），覆盖安全 / 性能 / UX / 文案 4 维度，给出 file:line refs + severity + fix sketch。仅产报告，不动代码。
- **Scope**: internal — `server/templates/*.html` only（跨模块 → backlog）
- **Date**: 2026-05-15

## 0. 范围与上下文

17 个被截图的 HTML 模板：

| 模板 | 占位符 | 主要数据源 | LOC |
|---|---|---|---|
| `about.html` | `__ABOUT_DATA_JSON__` | `about_page.build_payload` | 339 |
| `admin_list.html` | `__ADMIN_LIST_DATA_JSON__` | `admin_list_page` | 204 |
| `ban_list.html` | `__BAN_LIST_DATA_JSON__` | `ban_list_page` | 267 |
| `inventory.html` | `__INVENTORY_DATA_JSON__` | `inventory_page` | 523 |
| `leaderboard.html` | `__LEADERBOARD_DATA_JSON__` | `leaderboard_page` | 483 |
| `lottery_list.html` | `__LOTTERY_LIST_DATA_JSON__` | `lottery_list_page` | 244 |
| `lottery_result.html` | `__LOTTERY_RESULT_DATA_JSON__` | `lottery_result_page` | 495 |
| `lottery_view.html` | `__LOTTERY_VIEW_DATA_JSON__` | `lottery_view_page` | 552 |
| `menu.html` | `__MENU_DATA_JSON__` | `menu_page` | 310 |
| `progress.html` | `__PROGRESS_DATA_JSON__` | `progress_page` | 377 |
| `red_packet_all.html` | `__RED_PACKET_ALL_DATA_JSON__` | `red_packet_all_page` | 322 |
| `red_packet_own.html` | `__RED_PACKET_OWN_DATA_JSON__` | `red_packet_own_page` | 315 |
| `shop_list.html` | `__SHOP_LIST_DATA_JSON__` | `shop_list_page` | 234 |
| `shop_view.html` | `__SHOP_VIEW_DATA_JSON__` | `shop_view_page` | 520 |
| `tutorial.html` | `__TUTORIAL_DATA_JSON__` | `tutorial_page` | 398 |
| `user_info.html` | `__USER_INFO_DATA_JSON__` | `user_info_page` | 428 |
| `warehouse.html` | `__WAREHOUSE_DATA_JSON__` | `warehouse_page` | 418 |

**占位符语法**：统一使用 `__NAME__` 风格（17/17 一致，不存在 `{{NAME}}` 风格）。每个模板有且仅有一个占位符；都位于 `<script id="..." type="application/json">__NAME__</script>` 中。占位符值由 Python 端 `json.dumps(data, ensure_ascii=False).replace("</", "<\\/")` 生成后字符串 `replace` 注入；前端通过 `JSON.parse(document.getElementById(...).textContent)` 读取，并几乎全部走 `textContent` / DOM API（未发现 `innerHTML` / `outerHTML` / `insertAdjacentHTML` / `doc.write`-style / `eval` / 动态 code-exec sink）。

**总体 XSS posture**：✅ 良好。不存在 `innerHTML` 类 sink，全部用户文本通过 `textContent` 输出；JSON 包装在 `application/json` script 块里使浏览器不解析为 JS；Python 端预先 `replace("</","<\\/")` 阻断 `</script>` 提前关闭。

---

## 1. Severity Summary

| Severity | Count |
|---|---|
| **High** | 0 |
| **Medium** | 8 |
| **Low** | 14 |
| **Info / Backlog** | 6 |
| **Total** | **28** |

**Top 3 issues**:
1. **avatar src 使用 `http://` 明文** — 7 个模板 hardcode `http://q1.qlogo.cn/...`，截图环境若走 https 头部可被 mixed-content 拦截；同时是隐式外链 leakage。(M)
2. **占位符无 fallthrough fallback** — 17/17 模板若 Python 端从未替换 `__XXX_DATA_JSON__`，`JSON.parse` 会抛 `SyntaxError`，页面变白屏，playwright 截图空白且不会报错。(M)
3. **`[hidden]` 全局守卫缺失** — 10/17 模板未声明 `[hidden] { display: none !important; }`，导致后端通过 `el.hidden = true` 隐藏元素时若该元素同时是 `display: flex/grid`，元素仍可见（曾在 leaderboard 修过，但其他 10 个未同步）。(M)

**占位符不安全位置统计**：0 处。所有占位符均在 `application/json` script 内，且无 attribute / inline-event / inline-`<script>` 直接拼接的位置。

---

## 2. Findings

### Security

#### S1 — avatar / logo 走 `http://` 明文 (Medium)
- **位置**：
  - `about.html:319` — `img.src = \`http://q1.qlogo.cn/g?b=qq&nk=${...}&s=100\``
  - `admin_list.html:182` — 同
  - `ban_list.html:219` — 同
  - `inventory.html:329` — 同（owner avatar）
  - `red_packet_all.html:249` — 同（sender avatar）
  - `warehouse.html:310` — 同（owner avatar）
  - `user_info.html:289` — 同（self avatar）
- **问题**：7 处硬编码 `http://`。即使页面经 https 服务，浏览器会以 mixed-content 拦截这些资源，进而 playwright 截图缺失头像。即便当前 render 端口走 http，迁移到 https 时会成为静默故障；同时把 QQ 号通过明文外发。
- **风险**：Medium（隐式可观测性故障 + 信息出站）。
- **Fix sketch**：改 `https://`（QQ 头像 CDN 实际支持 https），或抽到 `data.avatar_url` 由后端拼接 + masking。

#### S2 — `about.html` author / repo 链接未做 URL 协议白名单 (Low)
- **位置**：`about.html:298-304`，`authorEl.href = data.author_url || "#"`，`repoEl.href = repoUrl`（来自 `data.project_url`）。
- **问题**：当前 `about_page.build_payload()` 是硬编码常量（`https://github.com/...`），无注入路径；但模板若被复用 / payload 未来开放外部输入，未校验 `javascript:` / `data:` 协议会成为 XSS。截图场景被 playwright 渲染时这些 href 没有 click 行为，风险被掩盖。
- **风险**：Low（当前数据源安全）。
- **Fix sketch**：在 JS 侧加协议白名单 `if (!/^https?:/.test(url)) url = "#";`，或不绑定 href（截图不需要可点击）。

#### S3 — placeholder fallthrough 无兜底 (Medium)
- **位置**：所有 17 模板的 `<script type="application/json">__XXX__</script>`。
- **问题**：如果 Python 端因 bug 未调用 `.replace(...)`，占位符 `__XXX_DATA_JSON__` 留在 DOM 中，`JSON.parse` 抛 `SyntaxError: Unexpected token _`，页面变白屏。playwright 不会主动失败 — 截图返回空白页。
- **风险**：Medium（静默故障，难定位）。
- **Fix sketch**：模板里默认放 `{}` 兜底：`<script ...>{"_unreplaced":true}</script>` 并通过 `replace` 整个 script 块；或前端 `try { data = JSON.parse(...); } catch (e) { document.body.textContent = "数据未注入"; }`。

#### S4 — `target="_blank"` 缺失 `rel="noopener"`（仅 about）(Info / Backlog)
- **位置**：`about.html:257`、`:261` — `<a id="info-author" href="#">`、`<a id="info-repo" href="#">`，无 `rel="noopener noreferrer"` 也无 `target="_blank"`。
- **问题**：当前没有 `target="_blank"`，所以无 reverse-tabnabbing 风险；只是预防未来开发者加上后忘记 `rel`。
- **风险**：Info。
- **Fix sketch**：截图场景不需要 target，可不加；如未来加，约定 `rel="noopener noreferrer"`。

#### S5 — inline `<script>` / inline event handler 含未转义值 (None Found, ✅)
- 没有任何 `onclick=` / `onload=` / `onerror=` / 等 inline handler。
- 没有任何 `innerHTML` / `insertAdjacentHTML` / `doc.write`-style / `eval` / 动态 code-exec sink。
- ✅ Pass。

#### S6 — 外链资源 SRI / 版本固定 (Info)
- 外链资源只有 `/assets/css/render-fonts.css` 和 `/assets/css/render-tokens.css`（同源、自托管）。**没有任何 CDN font / icon**。SRI 不适用。
- 字体 fetching 走 `/assets/css/render-fonts.css` 内部声明，需要单独审计（属于 `server/web_server.py` 静态服务范围，但截图依赖该路径返回 200）。Backlog。

#### S7 — `progress.html` `data.server_id` 直接作 `String(...)` 显示 (Low)
- **位置**：`progress.html:327` — `document.getElementById("meta-server-id").textContent = String(data.server_id);`
- **问题**：值通过 `textContent` 输出，无 XSS；但 server_id 期望是数字，若 payload 给字符串如 `<script>` 会被原样显示在 meta 区。属于显示问题，非安全问题。`progress_page.build_payload` 已 `int()` 强制，安全。
- **风险**：Info（防御性）。
- **Fix sketch**：JS 侧 `String(Number(data.server_id))` 显式归一。

---

### Performance（render-for-screenshot 场景）

#### P1 — `inventory.html` / `lottery_view.html` / `shop_view.html` / `warehouse.html` 4 个模板每次都 `fetch /assets/dicts/item.json` + `/assets/dicts/prefix.json` (Medium)
- **位置**：
  - `inventory.html:372-394`
  - `lottery_view.html:392-414`
  - `shop_view.html:368-390`
  - `warehouse.html:319-341`
- **问题**：每次截图加载 2 个 JSON 字典，靠 `await Promise.all([fetch(...), fetch(...)])`。playwright 必须等 2 个 request 返回才能进入 `for of` 循环并渲染。dict 可能数 KB～百 KB，本地 fetch 影响不大但是 wait 链上多 1 hop。
- **风险**：Medium（render latency；尤其 dict 尺寸增长后）。
- **Fix sketch**：(a) Python 端预 join itemName / prefixName 到 payload；或 (b) playwright `waitForLoadState("networkidle")` 已覆盖，纯感知没问题但 build 路径多。建议 (a)，前端只做展示。注：跨模块（涉及 `*_page.py`），可标 backlog。

#### P2 — `inventory.html` 大量 slot DOM (~350 slots) 无虚拟化 (Low)
- **位置**：`inventory.html:435-459` `createCell()` 循环；总单元数固定 350（slots 0..349），每个 cell 含 img / span。
- **问题**：DOM 节点固定 ~350 个 cells + 350 个 slot-index span + 最多 350 个 img。截图分辨率下基本无 reflow 风险（固定 `--cell: 52px`，grid 布局），但慢设备 / 慢渲染时 playwright 的 image-loading wait 会随空 vs 占用比变化。
- **风险**：Low（固定上限，可接受）。
- **Fix sketch**：保留现状；如要进一步优化，把 `loading="lazy"`（已加，第 449 行）改成 `loading="eager"` 因为视口内全部可见，lazy 反而触发 IntersectionObserver 延迟。

#### P3 — `user_info.html` Contribution wall 内联生成 90~365 个 div (Low)
- **位置**：`user_info.html:416-425`，`weeks.forEach -> week.forEach(...)`，每天一个 div。
- **问题**：上限 365 cells，正常 90，性能可控。每个 cell 直接 inline style 写 `borderRadius` / `backgroundColor` 但不写到 class，CSS 反射 ok。
- **风险**：Info。

#### P4 — `progress.html` BOSS_IMG_MAP 加载 boss gif/webp/png 多张 (Info)
- **位置**：`progress.html:296-318` 21 个 BOSS image entries。
- **问题**：每次进度页都尝试加载所有 21 张 boss 图。playwright 必须等加载完成才截图。gif 体积较大，但都是同源 `/assets/imgs/boss/...`。
- **风险**：Info。

#### P5 — 全部模板缺 `font-display: swap` / preload 提示 (Info)
- **位置**：`<link rel="stylesheet" href="/assets/css/render-fonts.css" />` 在 17 个文件 line 7。
- **问题**：playwright 截图前 chromium 默认会等 web font 加载，但若 font 文件不存在或慢，会拖累首次截图。不属于模板范围（在 fonts.css 内）。
- **风险**：Info / Backlog（跨模块）。

---

### UX / 视觉

#### U1 — `[hidden] { display: none !important; }` 全局守卫缺失（10/17）(Medium)

**精确复核**（grep `\[hidden\]` 命中）：

- 有该规则的 7 个：`about.html:12`、`admin_list.html:12`、`ban_list.html:12`、`inventory.html:12`、`leaderboard.html:14`、`progress.html:12`、`tutorial.html:12`。
- **缺失 10 个**：`lottery_list.html`、`lottery_view.html`、`lottery_result.html`、`menu.html`、`red_packet_all.html`、`red_packet_own.html`、`shop_list.html`、`shop_view.html`、`user_info.html`、`warehouse.html`。

- **问题**：这 10 个模板里有大量 `el.hidden = true` 写法（如 `lottery_list.html:196` `listEl.hidden = true;`），但若 `listEl` 同时被 `display: flex` 应用（`.list { display: flex; }`），CSS specificity 会让 `display: flex` 胜过 UA `[hidden]` 的 `display: none`。leaderboard.html 已通过注释明确解释这个 bug 并加守卫。其他 10 个未同步。
- **风险**：Medium（潜在隐藏失败导致 list 与 empty-state 同时出现）。
- **Fix sketch**：每个模板顶部 CSS reset 加 `[hidden] { display: none !important; }`。

#### U2 — `user_info.html` 不使用 `header-rule` (Info, by design)
- **位置**：`user_info.html:39-47` 注释说明该页 hero anchor 是 avatar + serif name，不需要 rule。
- **问题**：与其他 16 个模板视觉一致性偏差，但有显式注释解释设计意图。
- **风险**：Info。

#### U3 — `inventory.html` / `warehouse.html` owner-bar 排版接近重复，但 inventory 多一个 server divider 隐藏逻辑 (Info)
- **位置**：`inventory.html:281-285` 与 `warehouse.html:284-294`。
- **问题**：两个模板的 owner-bar 实现非常相似但有微差异（inventory 有 server display，warehouse 没有）。已 ok。
- **风险**：Info。

#### U4 — `leaderboard.html` self-card label "我的排名" 与 podium 顺序 2-1-3 视觉规则缺少注释外 hint (Info, by design)
- **位置**：`leaderboard.html:329-348` 显示顺序 2 → 1 → 3。
- **问题**：注释已明确（"classic podium center stage"），用户能理解。
- **风险**：Info。

#### U5 — `progress.html` `summary-defeated` 颜色用 coral primary，与 `boss.defeated` 边框颜色重复 (Info)
- **位置**：`progress.html:88-101`、`170-172`。
- **问题**：coral 大量出现在 stat、边框、bar，截图整体偏 coral 重。设计意图：突出"已击败"。Info。

#### U6 — 各模板间字号 / 圆角 / 边框 token 基本统一 (Pass ✅)
- 全部用 `var(--space-*)` / `var(--radius-*)` / `var(--color-*)` token。
- `border: 1px solid var(--color-hairline)` 与 `border: 1px dashed var(--color-hairline)` 用法一致。
- `border-radius: var(--radius-lg)` 主卡片、`var(--radius-md)` 次级、`var(--radius-pill)` 圆形 / 徽章。
- ✅ Token 使用一致性强。

#### U7 — 占位符为空时的视觉 fallback (Pass / partial)
- 数据为空时，全部 17 模板都有 empty-state（除了 `lottery_result.html` — 因为没结果就不会渲染该页）。✅
- 字符串字段为空时 fallback 用 `—`（破折号）、`未X`、`暂无X` 三类混用 — 见 Copy C2。

---

### Copy（文案）

#### C1 — 空数据/未知值文案不统一（"暂无 / 没有 / 未 / — / —"）(Medium)
- **位置**：
  - `admin_list.html:150` `暂无管理员`、`:174` 单 entry 空时 `"—"`
  - `ban_list.html:181` `暂无封禁用户`、`:207` `"—"`、`:209` `"未说明"`
  - `lottery_list.html:169` `暂无可用奖池`、`:208` `"未命名奖池"`
  - `lottery_view.html:344` `该奖池暂无奖品`、`:370` `"未命名奖池"`、`:457` `"未命名"`
  - `lottery_result.html:361` `"未命名"`、`:363` `"未知玩家"`、`:451` `"未中奖"`、`:470` `"未知物品"`、`:475` `"未命名"`
  - `red_packet_all.html:206` `当前没有可抢的红包`、`:234` `"—"`、`:235` `"未知"`
  - `red_packet_own.html:200` `你还没发过红包`、`:234` `"—"`、`:235` `"红包"`
  - `shop_list.html:164` `暂无可用商店`、`:203` `"未命名商店"`
  - `shop_view.html:336` `该商店暂无可购买的商品`、`:353` `"未命名商店"`、`:429` `"未命名"`
  - `inventory.html:330` `"未知玩家"`、`:345-347` `"—"`
  - `progress.html:323` `"未知服务器"`、`:342` `"未知"`
  - `warehouse.html:311` `"未知用户"`
  - `user_info.html:290` `"—"`
  - `menu.html:217` `暂无命令`、`:218` `当前分类下没有可显示的命令`、`:246` `暂无介绍`、`:266` `暂无用法`
- **问题**：相同语义下文案 9+ 种变体。
- **风险**：Medium（一致性）。
- **Fix sketch**：约定：
  - 列表为空 → `暂无<类型>`（admin_list / lottery_list / shop_list 已遵循）
  - 单条字段空 → `—`（破折号 U+2014，已统一为 em dash）
  - 未知 / 不可知用户名 → `未知用户`（warehouse） vs `未知玩家`（inventory / lottery_result）。建议改为统一 `未知用户`
  - 未命名 entity → `未命名` 或 `未命名 + 类型`（已有差异）

#### C2 — 用户称呼："玩家" vs "用户" 混用 (Low)
- **位置**：
  - `inventory.html:274` eyebrow `玩家查询`
  - `inventory.html:330` `"未知玩家"`
  - `progress.html:263` eyebrow `玩家查询`
  - `lottery_result.html:363` `"未知玩家"`
  - `warehouse.html:311` `"未知用户"`
  - `user_info.html` 没有显式 "玩家"/"用户" 标签但 URL 是 `user_info`
  - `admin_list` 用 `用户` / `nickname`，封面叫 `管理员列表`
- **问题**："玩家" 偏向游戏内身份；"用户" 偏向 bot/QQ。各模板都是同一个 user。
- **风险**：Low。
- **Fix sketch**：统一为 "玩家"（项目主题是 Terraria 游戏），或保持 inventory/progress/lottery_result 的 "玩家"，把 warehouse 的"未知用户"也改为"未知玩家"。

#### C3 — 中英混排空格缺失 (Low → Info)
- **位置**：
  - `lottery_list.html:213` `\`ID ${entry?.pool_id ?? "-"}\`` — `ID 数字`，✅ 有空格
  - `lottery_list.html:218` `\`${...} 件奖品\`` — `数字 件奖品`，✅
  - `lottery_list.html:223` `\`${...} 金币 / 次\`` — `数字 金币 / 次`，✅
  - `lottery_result.html:368-377` 类似 ✅
  - `lottery_view.html:372-380` 检查 `单抽 ${cost} 金币` ✅、`未中奖率 ${...}%` — `数字%` **缺空格**（line 377）
  - `progress.html:337` `${pct}%` — 同样 `数字%` 缺空格
  - `lottery_view.html:539-543` `prob` + `%` 单独 span，渲染时无空格分隔（视觉上设计意图）
  - `red_packet_own.html:281-285` `已抢 ${taken}` ✅
- **问题**：百分号 `%` 前的数字无空格。CLAUDE.md 中英混排规则更针对中英文之间，纯 `数字%` 是数学单位，可不加空格（行业惯例）。视为 Info / Pass。
- **风险**：Info。

#### C4 — Footer "Powered by NextBot" 大小写一致 (Pass ✅)
- 17/17 模板都是 `Powered by NextBot`。✅

#### C5 — 标题 hierarchy 与 type-display-* 一致性 (Pass)
- 16 模板用 `type-display-lg`、1 个 `type-display-md`（user_info.html，因 avatar 旁边的 inline 排版要求小一档）。设计 ok。
- ✅。

#### C6 — `lottery_list.html:240` 与 `shop_list.html:230` hint 行使用引号 `「」` (Info)
- **位置**：`hintLine.textContent = "查看奖池：「查看奖池 <奖池 ID/奖池名称> [页数]」 · 抽奖：「抽奖 <奖池 ID/奖池名称> [次数]」";`
- **问题**：使用「」是 CJK 标准引号，符合中文排版。✅。

#### C7 — `tutorial.html` chat-avatar 沿用 `__BOT__` / `__SELF__` 特殊字符串作 sentinel (Info)
- **位置**：`tutorial.html:344-353`，`if (avatarSrc === "__BOT__")`。
- **问题**：用 `__SOMETHING__` 作 sentinel 与"占位符"语法重叠，可读性差，但功能正常（后端 `tutorial_page.py:15-22` 控制赋值）。
- **风险**：Info。

#### C8 — 时间戳显示统一性 (Pass)
- 全部使用 `data.generated_at`，由 `nextbot.time_utils.beijing_now_text()` 在后端生成（北京时间）。每个模板都把它放在 header-meta 行末，文案前缀无（直接显示时间字串）。✅

---

### Accessibility / 结构

#### A1 — `<img>` alt 文本部分为空 (Low)
- **位置**：
  - `about.html:243` `<img id="hero-logo" alt="Logo" />` ✅
  - `admin_list.html:181` `avatar.alt = nickname;` ✅
  - `ban_list.html:218` `avatar.alt = "";` ⚠️ 空 alt（装饰图）
  - `inventory.html:277` `alt="avatar"`、`:448` `img.alt = \`Item ${slot.netId}\`` ✅
  - `lottery_view.html:437` `img.alt = String(p.item_id);` ✅
  - `red_packet_all.html:248` `avatar.alt = "";` ⚠️
  - `tutorial.html:349` `img.alt = ""` ⚠️
  - `warehouse.html:360` `img.alt = String(slot.item_id)` ✅
  - `user_info.html:233` `alt="avatar"` ✅
- **问题**：截图场景 alt 不重要（屏幕阅读器场景不适用），但当 image 加载失败时 `alt=""` 会显示空白；其他用 `alt="avatar"` 等会保留辅助说明。Info。
- **风险**：Low / Info。

#### A2 — `<html lang="zh-CN">` 一致 (Pass ✅)
- 17/17 文件都是 `<html lang="zh-CN">`。

#### A3 — `<meta name="viewport">` 一致 (Pass ✅)
- 17/17 都是 `width=device-width, initial-scale=1.0`。

#### A4 — 缺 `<meta charset>` 顺序 (Pass)
- 17/17 都在 `<head>` 第一项 `<meta charset="UTF-8" />`。

---

## 3. Items per template (per-file scoreboard)

| 模板 | Security | Performance | UX | Copy | A11y |
|---|---|---|---|---|---|
| about.html | S1 / S2 | — | ✅ | ✅ | ✅ |
| admin_list.html | S1 | — | ✅ | C1 | ✅ |
| ban_list.html | S1 | — | ✅ | C1 | A1 |
| inventory.html | S1 | P1 / P2 | ✅ | C1 / C2 | ✅ |
| leaderboard.html | — | — | ✅ | C1 | ✅ |
| lottery_list.html | — | — | U1 | C1 | ✅ |
| lottery_result.html | — | — | U1 | C1 | ✅ |
| lottery_view.html | — | P1 | U1 | C1 | ✅ |
| menu.html | — | — | U1 | C1 | ✅ |
| progress.html | — | P4 | ✅ | C1 / C2 | ✅ |
| red_packet_all.html | S1 | — | U1 | C1 | A1 |
| red_packet_own.html | — | — | U1 | C1 | ✅ |
| shop_list.html | — | — | U1 | C1 | ✅ |
| shop_view.html | — | P1 | U1 | C1 | ✅ |
| tutorial.html | — | — | ✅ | C7 | A1 |
| user_info.html | S1 | P3 | U1 / U2 | C1 | ✅ |
| warehouse.html | S1 | P1 | U1 | C1 / C2 | ✅ |

---

## 4. Recommended fix order (priority)

1. **U1 — 给 10 个模板加 `[hidden]` 守卫**（一行 CSS，无视觉副作用，防潜在 list 与 empty-state 共显示）。**Medium / 易修**。
2. **S1 — 7 处 `http://q1.qlogo.cn` 改 `https://`**（验证 https 端可用即可）。**Medium / 易修**。
3. **S3 — 模板内 placeholder fallthrough fallback**（前端加 try/catch + log；或 Python 端 render 失败 raise 而不是返回未替换 HTML）。**Medium**。
4. **C1 — 文案统一**（约定 `暂无<类型>` / `—` / `未知用户`）。**Medium / 涉及多模板**。
5. **P1 — 字典 fetch 转后端预 join**（跨模块；标 backlog 或单独 task）。**Medium**。
6. **C2 — "玩家" vs "用户" 统一**。**Low**。
7. **S2 — about author/repo URL 协议白名单**（防御性，当前数据源安全）。**Low**。
8. **A1 — 装饰 img 的 alt 策略**。**Info**。

---

## 5. Cross-module backlog（不在本 bucket 内修）

- **B-1**: `*_page.py` 中字典预 join → 模板侧不再 fetch（涉及 Bucket C）。
- **B-2**: `server/web_server.py` 静态资源 cache header / mime 验证（涉及 Bucket A）。
- **B-3**: `nextbot.time_utils.beijing_now_text()` 输出格式与 timezone 显式标注（已审计 r9，不动）。
- **B-4**: `/assets/css/render-fonts.css` 字体 fallback 与 font-display 策略（涉及静态资源）。
- **B-5**: render endpoint `/render/<page>` 的 placeholder 注入失败兜底（涉及 Bucket B `render.py`）。
- **B-6**: 后端是否对用户提供的 `pool_name` / `shop_name` / 玩家名做 length 上限 — 模板侧已有 `overflow-wrap: break-word` / `word-break: break-all` 保险，但极长字符串仍会拉爆截图高度（Bucket C 范围）。

---

## 6. 不修的项（明确）

- **S4 — `target="_blank" + rel`**：当前无 `_blank`，无需现在处理。
- **U2 — `user_info.html` 不使用 header-rule**：有显式注释，by design。
- **U4 — leaderboard podium 2-1-3 顺序**：by design。
- **C6 — 「」CJK 引号**：符合中文排版，保留。

---

## 7. Findings 文件 / 行 速查表（重点）

| Severity | Finding | 文件 | 行号 |
|---|---|---|---|
| Medium | S1 avatar http:// | about.html | 319 |
| Medium | S1 avatar http:// | admin_list.html | 182 |
| Medium | S1 avatar http:// | ban_list.html | 219 |
| Medium | S1 avatar http:// | inventory.html | 329 |
| Medium | S1 avatar http:// | red_packet_all.html | 249 |
| Medium | S1 avatar http:// | warehouse.html | 310 |
| Medium | S1 avatar http:// | user_info.html | 289 |
| Medium | S3 placeholder fallthrough | 全部 17 | script id="*-data" 行 |
| Medium | U1 [hidden] guard 缺失 | lottery_list.html | head 顶部 css |
| Medium | U1 [hidden] guard 缺失 | lottery_view.html | head |
| Medium | U1 [hidden] guard 缺失 | lottery_result.html | head |
| Medium | U1 [hidden] guard 缺失 | menu.html | head |
| Medium | U1 [hidden] guard 缺失 | red_packet_all.html | head |
| Medium | U1 [hidden] guard 缺失 | red_packet_own.html | head |
| Medium | U1 [hidden] guard 缺失 | shop_list.html | head |
| Medium | U1 [hidden] guard 缺失 | shop_view.html | head |
| Medium | U1 [hidden] guard 缺失 | user_info.html | head |
| Medium | U1 [hidden] guard 缺失 | warehouse.html | head |
| Medium | P1 dict fetch | inventory.html | 372-394 |
| Medium | P1 dict fetch | lottery_view.html | 392-414 |
| Medium | P1 dict fetch | shop_view.html | 368-390 |
| Medium | P1 dict fetch | warehouse.html | 319-341 |
| Medium | C1 空文案不统一 | 全部 17 | 见 C1 列表 |
| Low | S2 about href | about.html | 298-304 |
| Low | S7 progress server_id | progress.html | 327 |
| Low | P2 inventory 350 slots | inventory.html | 435-459 |
| Low | C2 玩家 vs 用户 | inventory/progress/warehouse | — |
| Info | A1 alt="" | ban_list/red_packet_all/tutorial | 218 / 248 / 349 |
| Info | C7 tutorial __BOT__ sentinel | tutorial.html | 344-353 |

---

## Caveats / Not Found

- **未审 backend page py**：本 bucket 限定 HTML 模板；`_page.py` 转义和 normalize 逻辑由 Bucket C 覆盖。我抽样核对了 about / admin_list / inventory / lottery_result / lottery_view / menu / shop_view / tutorial 的 `render()` 函数，全部使用 `json.dumps(..., ensure_ascii=False).replace("</","<\\/")` 模式 — XSS 链路安全。
- **未审 `render-tokens.css` / `render-fonts.css`**：属于 assets 静态资源，不在 17 模板内。
- **未审 `/assets/items/Item_<id>.png` / `/assets/imgs/boss/...` 资源存在性**：是 web_server 静态服务范围。
- **prior art 对比**：`.trellis/tasks/archive/2026-05/05-04-audit-render-theme-cleanup/` 只做了 theme 清理（删 dark mode、删 Tailwind CDN），没做 security 审计 — 本次属增量。
