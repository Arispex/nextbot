# QQ 头像 + 昵称（QQ）：映射与复用模式

## 需求（用户原话）
卡片玩家名称处：**左边显示 QQ 头像，右边加括号显示 QQ 号** → `头像 昵称（QQ）`。
**数据库不存在映射则只保留昵称**（与现状一致，无头像、无括号）。

## 1. Terraria 账号名 → QQ 映射（DB）
- `User` 表（`nextbot/db.py`）：`user_id`（QQ 号，String，unique）、`name`（Terraria 账号名，String，**indexed + 迁移后 unique**）。
- 注册 `user_manager.py`：`User(name=<注册账号名>, user_id=<QQ>)` —— 故 `User.name` == online-players API 的 `players[].name`（都是 `Account.Name`）。
- **反查**：`session.query(User).filter(User.name.in_(names)).all()` → 建 `{User.name: User.user_id}` 映射。
- 现有代码全是 `User.user_id == ...`（正查）；本任务是 `User.name in names`（反查），一次批量查询即可。
- 兜底：名字不在表中 → 无 QQ → 只显昵称（用户明确要求）。空/None 名字不进 IN 查询。

## 2. QQ 头像复用模式（客户端 JS，与 inventory.html 一致）
`server/templates/inventory.html`（:392-395）：
```js
const userId = String(data.user_id || "");
el.src = `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(userId)}&s=100`;
document.getElementById("meta-user-id").textContent = userId ? `(${userId})` : "";
```
- 头像由**浏览器截图时**向 `q1.qlogo.cn` 加载（截图管线已支持外网图，背包/其它页都这么用）。
- 无效 QQ 时 qlogo 返回默认头像（无需 onerror 特判；如需更稳可加 onerror 隐藏）。
- `（QQ）` 括号文案也是 inventory 既有模式（`(${userId})`）。本任务用全角括号 `（${qq}）`（中文语境）或半角，按卡片观感定；inventory 用半角 `()`。

## 3. 卡片名行改造（`server/templates/online_players.html`，:237-246）
当前：
```js
const pname = document.createElement("div");
pname.className = "player-name";
pname.textContent = String((player && player.name) || "未知玩家");
```
改为名行 = `[小 QQ 头像 img] 昵称（QQ）`：
- `qq` 非空 → 头像 `img.src = qlogo(qq)`（左，小圆头像 ~20-24px，参 inventory `.avatar` 但更小）+ 昵称 + `（qq）`（弱化/muted 可选）。
- `qq` 空 → 只 `昵称`（现状，不渲头像、不渲括号）。
- 新增 `.player-qq-avatar` / `.player-qq` 样式（圆形、`image-rendering` 不需要，普通光栅头像）。

## 4. 数据流（三层贯穿 qq）
1. **handler** `nextbot/plugins/player_query.py` `_handle_online_image`：
   - 收集所有 ok server 的 players 的 `name` → 一次 `User.name.in_(names)` 查询建 `name_to_qq` 映射（独立 session，查完即关，参既有 `session = get_session(); try/finally: session.close()`）。
   - 在 build cards 循环里 `qq = name_to_qq.get(player_name)`，传入 `_render_online_player_card(player, server_id=..., qq=qq)`。
2. **card builder** `_render_online_player_card`：card dict 加 `"qq": qq or ""`。
3. **page builder** `online_players_page._normalize_player`：纳入 `qq`（str 兜底 strip）。
4. **模板**：名行渲染头像 + 昵称 + （qq）。

## 5. 注意
- DB 查询是 CPU/IO，在 async handler 里：`get_session()` 是同步 SQLAlchemy；既有 handler 都在协程里直接同步查（如 :647），本任务沿用（量小，一次 IN 查询）。如担心阻塞可 `asyncio.to_thread`，但与既有风格一致优先（既有都直接查）。
- 注入安全：`qq` 经 `online_players_page.render` 同一 `</`→`<\/` data_json 注入；JS 用 `encodeURIComponent(qq)` 拼 URL + `textContent` 渲染括号，XSS 安全。
- 不回归：无 qq 时卡片与现状逐字节一致（只昵称）。
- 日志：批量映射可 info 一条 `在线图片 QQ 映射：names=N matched=M`。
