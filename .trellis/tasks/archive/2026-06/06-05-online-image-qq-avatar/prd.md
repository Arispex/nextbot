# 在线图片卡片显示 QQ 头像与 QQ 号

## Goal

在线命令图片模式的玩家卡片中，玩家名称行改为 **`[QQ 头像] 昵称（QQ）`**：左侧小 QQ 头像、昵称、右侧括号显示 QQ 号。映射来源是本地 `User` 表（Terraria 账号名 → 绑定 QQ）。**数据库无映射的玩家维持现状——只显示昵称，无头像、无括号。**

## Requirements

- 图片模式卡片名称行：QQ 头像（左）+ 昵称 + `（QQ 号）`（右），当该玩家的 Terraria 账号名能在 `User` 表查到绑定 QQ 时。
- 查不到映射（`User.name` 无此账号名）→ **只显示昵称**，与现状逐字节一致（不渲头像、不渲括号）。
- 复用既有 QQ 头像取法：`https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(qq)}&s=100`（同 `inventory.html`，浏览器截图时加载）。
- 映射查询批量化：一次 `User.name.in_(names)` 建 `{name: user_id}`，不每卡一查。
- qq 三层贯穿：handler（DB 映射）→ `online_players_page`（card `qq` 字段）→ 模板（渲染头像 + 括号）。
- 仅图片模式卡片名行变化；文字模式、分区头（含上一任务的服务器 ID）、降级逻辑、立绘渲染均不变。
- HTML 注入安全沿用（`</`→`<\/`；URL 用 `encodeURIComponent`；括号用 `textContent`）。

## Acceptance Criteria

- [ ] 有 QQ 映射的玩家卡片：名行 = 小 QQ 头像 + 昵称 + `（QQ）`。
- [ ] 无 QQ 映射的玩家卡片：只昵称（不回归）。
- [ ] `User.name.in_(names)` 批量映射，独立 session 查完即关。
- [ ] qq 经 handler → page payload → 模板贯穿，类型/缺失兜底（空字符串）。
- [ ] 文字模式 / 分区头服务器 ID / 降级 / 立绘 均不回归。
- [ ] 单测：含 QQ 映射（命中）+ 无映射（只昵称）两路；payload 含 qq；模板渲染含头像 URL + 括号。
- [ ] ruff / pyright / 测试 全绿。

## Definition of Done

- 单测覆盖命中/未命中两路 + payload + 模板。
- 用户反馈文案不涉及（纯展示）；遵循 CLAUDE.md。
- ruff / pyright / 测试全绿。

## Technical Approach

见 `research/qq-avatar-mapping.md`（映射、复用模式、三层数据流、注意点）。要点：
- handler `_handle_online_image`：收集 ok server 玩家名 → `User.name.in_(names)` → `name_to_qq` → 传入 `_render_online_player_card(..., qq=...)`。
- `_render_online_player_card`：card 加 `"qq"`。
- `online_players_page._normalize_player`：纳入 `qq`（str strip 兜底）。
- `online_players.html`：名行渲染 `[qlogo 头像] 昵称（qq）`，qq 空时只昵称；新增 `.player-qq-avatar` / `.player-qq` 样式。

## Decision (ADR-lite)

- **Context**：卡片要显示玩家 QQ 头像 + QQ 号，但 online-players API 只给 Terraria 账号名。
- **Decision**：用本地 `User` 表 `name`(账号名)→`user_id`(QQ) 批量反查；命中渲头像 + 括号，未命中只昵称（用户明确要求）。头像复用 `inventory.html` 的 qlogo 客户端模式。
- **Consequences**：未注册/未绑定的在线玩家无头像（符合预期）；多一次轻量 DB 批查；扩展点：后续可缓存映射 / 头像本地代理。

## Out of Scope

- 不改 `/nextbot/online-players` 服务端、不新增 name→QQ 的 API。
- 不改文字模式、不改分区头服务器 ID、不改立绘渲染。
- 不做头像本地缓存 / 代理（沿用客户端 qlogo 直载）。

## Technical Notes

- `User`：`user_id`(QQ, unique) / `name`(账号名, indexed+unique)；注册 `user_manager.py` 写入。
- 头像：`inventory.html:392-395` `q1.qlogo.cn/g?b=qq&nk=...&s=100` + `(${userId})` 括号。
- 卡片名行：`online_players.html:237-246`；card builder `_render_online_player_card`；page `_normalize_player`。
