# 实现蓝图：在线命令图片模式

主代码探查得出的精确落点与复用管线。**所有路径/行号为撰写时快照，实现前以实际文件为准。**

## 1. 改 `nextbot/plugins/player_query.py`

### handler：`handle_online`（约 :270–367）
- `@command_control(...)` 增加 `params={"image_mode": {...}}`（见下「params schema」）。
- 函数体首部读取开关：`image_mode = bool(get_current_param("image_mode", True))`（`get_current_param` 已 import，:18-22）。
- 分支：
  - `image_mode=False` → 走**现状文字逻辑**（现有 `_query_one` /v2/server/status fan-out + 文本拼接，原样保留，**不回归**）。
  - `image_mode=True`（默认）→ 走**新图片逻辑**。
- `if args: raise_command_usage()` 保留（命令本身仍不收用户参数）。

### params schema（参 `economy.py:319-366` 的 `min_coins`/`enable_streak`）
```python
params={
    "image_mode": {
        "type": "bool",
        "label": "图片模式",
        "description": "开启后以图片形式渲染在线玩家角色；关闭则维持文字列表",
        "required": False,
        "default": True,
    },
},
```

### 图片分支数据流
1. 查 `servers = session.query(Server).order_by(Server.id.asc()).all()`（同现状）；空 → 文字降级（`reply_failure("查询", "暂无服务器")`，同现状）。
2. 并行 fan-out：对每台 server `await request_server_api(server, "/nextbot/online-players")`（`return_exceptions=True`，参现有 `_query_one` 的 gather 模板 :346-348）。建议新增内部协程 `_query_online_players_one(server)` 返回结构化结果（server + players 列表 或 失败原因）。
3. 对每个 server 的每个 player：`appearance` 为 dict 才渲染；调用 `_build_character_sprite_uri`（**注意**现有签名是「传入 appearance_result（TShockResponse）」——见下「复用 vs 重构」）→ 立绘 data URI。`appearance=null`/失败 → 跳过该 player。
4. 收集「至少一个可渲染玩家」→ 构建 HTML 页 → 截图发送；否则文字降级。

### 复用 vs 重构：`_build_character_sprite_uri`（:187-267）
现签名吃**单个 appearance API 响应**（背包是 per-user 单独调 `/appearance`）。本任务 `/nextbot/online-players` 一次返回**多个 player 的 appearance 内联对象**（不是逐个响应）。两条路线：
- **(A) 抽取纯渲染核**：把「appearance dict + equipment/vanity/dye/accessories/... → `render_character` → data URI」抽成 `_render_appearance_to_uri(appearance, equipment, vanity, dye, accessories, vanity_accessories, accessory_dyes, *, log_label, ...)`，`_build_character_sprite_uri` 内部改为「解析响应后委托该核」。图片分支直接用内联 player 对象调该核。**推荐**——DRY，且与 :242-267 的 `asyncio.to_thread(render_character, ...)` + best-effort 兜底 + 日志完全一致。
- (B) 图片分支自行内联一遍 render_character 调用。重复代码，不推荐。
> 字段名映射：API `vanityAccessories`→kwarg `vanity_accessories`；API `accessoryDyes`→kwarg `accessory_dyes`。各块可能为 null，`isinstance(x, dict/list)` 守卫（同 :248-254）。

### 截图发送（复用 `render_and_send_screenshot`，:35 import / 全签名见 screenshot_render.py）
- `page_url = create_online_players_page(...)`（新增，见 §3）。
- 新增 module-level `ONLINE_PLAYERS_SCREENSHOT_OPTIONS = ScreenshotOptions(viewport_width=..., full_page=True, fit_content_height=True)`（参 `INVENTORY_SCREENSHOT_OPTIONS` :57-62）。榜单可能很宽/很高，viewport_width 取一个能容纳多列卡片的值（如 1600–2000）。
- per-server semaphore：在线是「所有服务器一张图」，非 per-server-per-user；可复用低频命令的「不加锁」路径（`semaphore=None`），或新增一个 module-level Semaphore(1)。优先 `semaphore=None`（在线是低频聚合命令）。
- `failure_action="查询"`；`at_user_id`=触发者（参背包/地图用 `safe_at_segment`）。

## 2. 文字模式（不回归）
现有 `_query_one`（:300-341，`/v2/server/status?players=true`）+ 拼接（:350-367）原样保留，仅被 `image_mode=False` 分支调用。

## 3. 新增渲染页（4 处，参 inventory 全链）

复用现有「payload → token → /render/<type>/<token> → render(payload)→HTML bytes」机制（`server/page_store.py` + `server/routes/render.py` + `server/web_server.py`）。

1. **`server/pages/online_players_page.py`**（参 `inventory_page.py`）：
   - `build_payload(*, servers: list[...], generated_at?...) -> dict`：把「按服务器分区 + 每玩家 {立绘 data URI, 账号名, 在线时长文本}」规整为稳定 dict。`render_character` 已在 handler 侧完成（payload 只装 data URI 字符串，与 inventory 的 `character_sprite_data_uri` 一致）。
   - `render(payload) -> bytes`：load 模板 → `json.dumps(...).replace("</","<\\/")` 注入占位符 → encode utf-8（**完全照搬** inventory_page.render 的注入方式，防 XSS / `</script>` 截断）。
2. **`server/templates/online_players.html`**（参 `inventory.html` head：`render-fonts.css`+`render-tokens.css`+设计令牌；立绘 CSS 参 `.portrait img` :98-112 `image-rendering:pixelated; height:280px`）：
   - 顶部 header（标题「在线玩家」+ header-rule）。
   - 按服务器分区（section 标题=server name）；每区一个卡片网格，卡片含：立绘 `<img>`（像素放大）+ 账号名 + 「在线时长 xxx」。
   - 占位符 `__ONLINE_PLAYERS_DATA_JSON__`，`<script type="application/json">` + JS 渲染（参 inventory.html :382-412 的取数+建 DOM）。
3. **`server/web_server.py`**：`from server.pages import ... online_players_page`（:13 import 行）；新增 `create_online_players_page(*, ...) -> str: return _make_page_url("online_players", online_players_page.build_payload(...))`（参 :78-113）。
4. **`server/routes/render.py`**：import `online_players_page`（:12）；新增路由 `@router.get("/render/online_players/{token}") -> _render_page(..., page_type="online_players", renderer=online_players_page.render)`（参 :72-74）。

## 4. 边界与降级（CLAUDE.md 文案规范）
- 无服务器 → `reply_failure("查询", "暂无服务器")`（同现状文字）。
- 所有服务器查询失败 / 无任何可渲染玩家 → 文字降级：无人在线用 `ℹ️ 无玩家在线` 语义；全失败透传原始 `get_error_reason(response)`。
- 部分服务器失败 → 渲染成功部分，跳过失败（不放弃整图）。
- 失败文案：动作+结果，原因；原因**原样透传** API `error.message`（不改写/翻译）。

## 5. 日志（CLAUDE.md 后端日志规范，machine-search-first 风格，与本文件现有日志一致）
- 入口：`logger.info(f"在线图片渲染请求：server_count={...} image_mode={...}")`。
- 每 server 结果：成功 `players=N renderable=M`；失败 `reason=...`。
- 立绘跳过/失败：复用 `_build_character_sprite_uri` 既有 best-effort 日志（:209-265）。
- 降级：`logger.info(f"在线图片降级文字：reason=...")`。

## 6. 测试（参 tests/ 现有 player_query / web_server 测试风格）
- 图片分支数据流：mock `request_server_api` 返回多 player（含 appearance=null 跳过、含失败 server 跳过），断言进入 `create_online_players_page` 的 payload 含正确玩家集 + 调用 `render_and_send_screenshot`。
- 文字分支不回归：`image_mode=False`（mock `get_current_param`）→ 走 `/v2/server/status` 文本路径，输出与现状一致。
- 降级：无服务器 / 全失败 / 无可渲染玩家 → 文字回复，原因透传。
- `online_players_page.build_payload`/`render`：稳定 payload + HTML 注入转义。
