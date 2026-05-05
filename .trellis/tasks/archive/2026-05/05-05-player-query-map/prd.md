# 新增 「我的地图」 命令（玩家查询）

## Goal

新增命令 `我的地图 <服务器 ID>`，调用 TShock NextBotAdapter 的 `/nextbot/users/{user}/map-image` API 生成当前玩家视角下的地图 PNG（已探索区域真色 + 未探索黑色），把 base64 解码发图返回。归类到「玩家查询」分类，与 `我的背包` / `进度` 共存。

## Requirements

### 命令

- 命令名：`我的地图 <服务器 ID>`
- `command_key="player_query.map.self"`、`category="玩家查询"`、`permission="player_query.map.self"`、`display_name="我的地图"`
- description: `"查询当前用户在指定服务器世界中的探索地图"`
- usage: `"我的地图 <服务器 ID>"`
- 命令参数：暂无（API 自身返回 PNG 即可）

### 行为

1. 解析 `args[0]` 为 `int server_id`，失败 → `raise_command_usage()`
2. DB 查 `Server` + `User`：
   - server 不存在 → `reply_failure("查询", "服务器不存在")`
   - user 未注册 → `reply_failure("查询", "用户不存在")`
3. 调用 `request_server_api(server, f"/nextbot/users/{user.name}/map-image", timeout=30.0)`
   - 连接异常（`TShockRequestError`）→ `reply_failure("查询", "无法连接服务器")`
4. `is_success(response)` 失败 → `reply_failure("查询", get_error_reason(response))`
5. 校验 payload：`base64` 必须是非空 string；`fileName` 可选
6. base64 解码：
   - 失败（`binascii.Error` / `ValueError`）→ `reply_failure("查询", "返回数据格式错误")`
7. 发图：
   - 写一份 PNG 到 `/tmp/map-{server_id}-{user_id}-{timestamp}.png`（沿用现有 diagnostic 习惯，便于排障）
   - OBV11 用 `OBV11MessageSegment.image(file=f"base64://{b64_string}")` 直接发，避免文件 round-trip
   - 非 OBV11 适配器 → `await bot.send(event, f"✅ 地图生成成功，文件：{screenshot_path}")`

### 日志

- `logger.info(f"我的地图请求：server_id={server.id} user_id={user.user_id} target_user_name={user.name}")`
- `logger.info(f"我的地图发送成功：server_id={server.id} user_id={user.user_id} file={screenshot_path}")`

## Acceptance Criteria

- [ ] `我的地图 1`（注册用户、服务器在线）成功收到地图 PNG
- [ ] `我的地图`（不带参） / `我的地图 abc` → 触发 usage 提示
- [ ] `我的地图 999`（不存在的 server_id）→ `❌ 查询失败，服务器不存在`
- [ ] 未注册用户调用 → `❌ 查询失败，用户不存在`
- [ ] 服务器离线 → `❌ 查询失败，无法连接服务器`
- [ ] API 返回 400 `User was not found.` → `❌ 查询失败，User was not found.`
- [ ] API 返回 500 → `❌ 查询失败，<API 异常信息>`
- [ ] 命令出现在 `菜单 玩家查询` 截图里
- [ ] 通过 WebUI 命令配置可见、可改 enabled

## Definition of Done

- 单文件改动（`nextbot/plugins/player_query.py`）
- 不引入新依赖（`base64` / `binascii` 是 stdlib）
- 风格与 `我的背包` 完全一致：装饰器顺序、错误文案、日志结构
- 命令在 NoneBot 启动期通过 `sync_registered_commands_to_db` 自动入库（无需迁移脚本）

## Technical Approach

### 顶部 imports

`base64` 已在文件第 1 行 import 了（用于 `_to_base64_image_uri`）。无需新增。

### Matcher 注册

在第 37 行后追加：

```python
my_map_matcher = on_command("我的地图")
```

### Handler 函数（追加在 `handle_my_inventory` 之后）

```python
@my_map_matcher.handle()
@command_control(
    command_key="player_query.map.self",
    display_name="我的地图",
    permission="player_query.map.self",
    description="查询当前用户在指定服务器世界中的探索地图",
    usage="我的地图 <服务器 ID>",
    category="玩家查询",
)
@require_permission("player_query.map.self")
async def handle_my_map(bot: Bot, event: Event, arg: Message = CommandArg()):
    args = parse_command_args_with_fallback(event, arg, "我的地图")
    if len(args) != 1:
        raise_command_usage()

    try:
        server_id = int(args[0])
    except ValueError:
        raise_command_usage()

    user_id = event.get_user_id()
    session = get_session()
    try:
        server = session.query(Server).filter(Server.id == server_id).first()
        user = session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()

    if server is None:
        await bot.send(event, reply_failure("查询", "服务器不存在"))
        return
    if user is None:
        await bot.send(event, reply_failure("查询", "用户不存在"))
        return

    logger.info(
        f"我的地图请求：server_id={server.id} user_id={user.user_id} target_user_name={user.name}"
    )

    try:
        response = await request_server_api(
            server,
            f"/nextbot/users/{user.name}/map-image",
            timeout=30.0,
        )
    except TShockRequestError:
        await bot.send(event, reply_failure("查询", "无法连接服务器"))
        return

    if not is_success(response):
        await bot.send(event, reply_failure("查询", f"{get_error_reason(response)}"))
        return

    b64_string = str(response.payload.get("base64") or "").strip()
    if not b64_string:
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    try:
        png_bytes = base64.b64decode(b64_string, validate=True)
    except (binascii.Error, ValueError):
        await bot.send(event, reply_failure("查询", "返回数据格式错误"))
        return

    screenshot_path = Path("/tmp") / (
        f"map-{server.id}-{user.user_id}-{beijing_filename_timestamp()}.png"
    )
    try:
        screenshot_path.write_bytes(png_bytes)
    except OSError:
        await bot.send(event, reply_failure("查询", "保存图片失败"))
        return

    logger.info(
        f"我的地图发送成功：server_id={server.id} user_id={user.user_id} file={screenshot_path}"
    )

    if bot.adapter.get_name() == "OneBot V11":
        await bot.send(event, OBV11MessageSegment.image(file=f"base64://{b64_string}"))
        return
    await bot.send(event, f"✅ 地图生成成功，文件：{screenshot_path}")
```

`binascii` 需要新 import：

```python
import binascii
```

### 时序

`request_server_api(timeout=30.0)` 单独覆盖默认 5s。地图生成是实时 PNG 渲染，大世界可能 10s+。

## Decision (ADR-lite)

**Context**：是否为「我的地图」做 page 渲染（像 `我的背包` 那样走 page → screenshot），还是直接吃 API 的 PNG base64 发图

**Decision**：直接吃 API 的 base64，不走 page 渲染

**Consequences**：
- 优：API 返回的 PNG 已经是最终图，再渲染一次毫无意义；少一次截图开销；少一份模板
- 劣：发图路径与 `我的背包` 不一样（一份用 page+screenshot，一份直接 base64），有人后期看代码可能困惑
- 缓解：在 handler 注释里说明 API 已返回完成的 PNG

## Out of Scope

- 不做 `用户地图 <服务器 ID> <用户>` 查他人地图的版本（如 `用户背包` 那种）—— 用户没要求，单独迭代
- 不做地图刷新提示 / 超时友好降级
- 不做命令参数化（如缩放、是否裁切）
- 不接入 WebUI（命令配置页会自动列出，但不专门做 UI）
- 不写单元测试

## Technical Notes

- API 文档（用户提供）：`/nextbot/users/{user}/map-image` GET 返回 `{fileName, base64}`
- 错误约定：400 + `error: "User was not found."` / 500 + `error: <异常>`
- 参考实现：`nextbot/plugins/player_query.py:441-585 handle_my_inventory`
- HTTP 客户端：`nextbot/tshock_api.py request_server_api(server, path, *, timeout=...)` 已支持 timeout kwarg
- `base64://...` URI 是 OBV11 / OneBot V11 通用格式
- TShock API base64 已是 PNG 二进制；`base64.b64decode(s, validate=True)` 在 padding / 字符集异常时抛 `binascii.Error`
