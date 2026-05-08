# 服务器工具 (server tools) 类目审计

- **范围**: `nextbot/plugins/server_tools.py`、`nextbot/plugins/server_send.py`
- **依赖参考**: `nextbot/tshock_api.py`、`nextbot/text_utils.py`、`nextbot/db.py`、`nextbot/permissions.py`、`nextbot/plugins/user_manager.py`（用户名约束）
- **日期**: 2026-05-08

## 总体结论

四个命令均被 `@require_permission` 守卫，权限分别为 `server_tools.execute`、`server_tools.map_image`、`server_tools.download_map`、`server.send`。其中只有 `server.send` 在 `DEFAULT_GUEST_PERMISSIONS` 中（`db.py:80`），其余三个默认仅 owner 才能执行，这与“管理员命令”预期一致。

数据库会话生命周期在 4 个 handler 中均使用 `try/finally` 关闭，正确。但下载 / 地图分支存在多个**严重内存与运行时**问题，主要涉及大文件全量加载、回退分支的路径遍历、`/tmp` 文件未清理、超时不足、错误路径下未 base64 解码即 `bot.send`、以及 `/say` 注入面收敛但仍存在原文回显问题。下方按命令分块列出，每块内按严重度排序。

严重度图例：🔴 critical / 🟠 high / 🟡 medium / 🟢 low / ℹ️ info

---

## 1. `执行 <服务器 ID> <TShock 命令>`（`server_tools.py:62-131`）

权限 key 为 `server_tools.execute`，未在 `DEFAULT_GUEST_PERMISSIONS` 中（`db.py:34-95`），权限校验通过 `@require_permission` 装饰器进行（`permissions.py:70-116`）。装饰器在执行 handler 之前调用 `has_permission`，且 `command_control` 在装饰器之外，意味着权限检查会在路径中前置执行，未发现绕过。

### ST-1.1 🟡 medium — `cmd` 参数原文回显，敏感命令进入用户消息历史

- **位置**: `server_tools.py:113-117`
- **现状**: 成功响应的 `reply_block` 中包含 `f"⚙️ 命令：{command}"`，将原始 TShock 指令原样发回 QQ 群 / 私聊。
- **影响**: 当 owner 在群里执行 `/op admin password123` 之类带凭证 / token / IP 的指令时，原文会暴露到群消息记录中；其他在场成员可见。
- **复现**: 执行 `执行 1 /op someone secret_pwd` → bot 回复块第二行展示 `⚙️ 命令：/op someone secret_pwd`。
- **建议**: 在群消息上下文下截断或仅显示 `命令：/op ***`，或在 `command` 命中常见敏感关键字（`password`, `token`, `op `, `auth`）时脱敏；或要求 `执行` 命令仅响应私聊。

### ST-1.2 🟡 medium — `result_text` 无截断，TShock 大输出可能导致超长消息或被 OneBot 拒收

- **位置**: `server_tools.py:52-59`、`107-118`
- **现状**: `_extract_response_text` 会把 `payload["response"]`（可能是 list 也可能是 str）整体拼成单条消息发回。没有对长度封顶。
- **影响**: 像 `/who -all`、`/find item-`、`/userinfo` 在大型服务器上可能返回上百行；超出 OneBot 单条消息上限（一般约 4–5 KB）会导致发送失败或被截断后看不到完整诊断信息；同时引发 QQ 风控。
- **复现**: 在玩家众多的服务器上 `执行 1 /who`，观察响应是否被吞 / 报错。
- **建议**: 对 `result_text` 加最大长度封顶（如 3000 字符）、超长时附 `（已截断）` 提示；或将长文转成转发合并消息 / 文件上传。

### ST-1.3 🟢 low — `command` 完全无前缀校验，用户可省略 `/` 直接发任意原文

- **位置**: `server_tools.py:32-49`、`93-97`
- **现状**: `_parse_execute_arg_text` 仅按空格分割 `server_id` 与剩余文本，未检查 `command` 是否以 `/` 开头。最终 `params={"cmd": command}` 直接发送给 TShock 的 `/v3/server/rawcmd`。
- **影响**: 不算漏洞，但是导致 owner 可能因笔误（漏 `/`）触发非命令文本被当成 say 广播或返回 "command not found"，用户体验略差，也无法走预期审计路径。
- **建议**: 在 `_parse_execute_arg_text` 中拒绝不以 `/` 开头的 `command_text`，并在 usage 提示中明确。

### ST-1.4 🟢 low — 默认 5s 超时不足以执行慢命令

- **位置**: `server_tools.py:93-97`、`tshock_api.py:48-66`
- **现状**: `执行` 没有显式传 `timeout`，沿用 `request_server_api` 默认值 5.0 秒。但部分 TShock 命令（如 `/butcher all`、`/invade`、`/freezetime` 切换）可能耗时数秒以上。
- **影响**: 命令实际已成功执行，但 bot 因超时返回 "无法连接服务器"，造成 owner 误判并重复执行（**幂等性风险**：连续两次 `/butcher` 不会冲突，但连续两次 `/op` 之类则可能遗留状态）。
- **建议**: `执行` 显式 `timeout=15.0` 或在文档 / usage 提示中说明长时命令可能误报。

### ST-1.5 ℹ️ info — `int(event.get_user_id())` 在某些 OneBot 来源（如 guild_id 字符串）会抛 `ValueError`

- **位置**: `server_tools.py:81`
- **现状**: 直接 `int(event.get_user_id())`，未处理非数字 ID 场景；其他 handler 也有同样写法（`server_send.py:55`）。
- **影响**: 当未来扩展到 OneBot V12 或频道适配器时，user_id 可能不是纯数字，会导致 handler 抛未捕获异常。但 V11 群 / 私聊场景一定是数字。
- **建议**: 项目当前显式仅支持 OneBot V11，可不修；若计划扩展，封装一个 `safe_at(event)` helper。

---

## 2. `全亮地图 <服务器 ID>`（`server_tools.py:134-189`）

### ST-2.1 🔴 critical — 大世界 base64 PNG 全量驻留内存，没有并发保护，多人同时调用会 OOM

- **位置**: `server_tools.py:166-187`
- **现状**:
  ```python
  response = await request_server_api(
      server, "/nextbot/world/map-image", timeout=60.0,
  )
  ...
  b64 = response.payload.get("base64")
  ...
  await bot.send(event, OBV11MessageSegment.image(file=f"base64://{b64}"))
  ```
  整个流程把原始 HTTP 响应（base64 PNG，数 MB 到数十 MB）解析进 `httpx.Response.json()`，再缓存到 `payload`，最终转成消息段——这一过程在内存中至少存在 3 份大 buffer（response.content、payload dict、message segment）。
- **影响**:
  - Large 模式 Terraria 世界的全亮地图 PNG 可达 30–80 MB（base64 后 40–110 MB）。
  - `httpx` 默认会把整个 response 读到 `_content`，不会流式落盘。
  - Python 进程在 N 个并发调用下，常驻内存 ≈ 3 × N × 110 MB → 即使 N=2 也接近 700 MB。
  - 没有任何并发限流（无 semaphore、无 dedupe by server_id、无 cooldown）。
- **复现**: 在 large 世界下让两个 owner 几乎同时发 `全亮地图 1`，`docker stats` 观察内存激增。
- **建议**:
  1. 在 handler 顶部加 per-server semaphore（如 `asyncio.Semaphore(1)` keyed by `server_id`）防止同一服务器并发取地图。
  2. 让 TShock 端 `/nextbot/world/map-image` 改为返回临时下载 URL，或允许 `Range`，bot 端走流式下载到磁盘临时文件，再用 `OBV11MessageSegment.image(file=f"file://{tmp}")` 发送，发送完成 / 失败后 `Path.unlink()`。
  3. 短期缓解：在拿到 `b64` 后立刻 `del response.payload["base64"]` 与 `del response`，让 GC 尽早释放原 dict。

### ST-2.2 🟠 high — 60 秒超时不足以渲染 Large 世界

- **位置**: `server_tools.py:170`
- **现状**: `timeout=60.0` 同时覆盖 connect / read / write / pool 四类（`httpx.AsyncClient(timeout=60.0)`）。Large 世界全亮地图渲染本身在低端机上可能 60–120 秒。
- **影响**: 即使服务器最终成功生成图，bot 这边已 `httpx.RequestError` → 抛 `TShockRequestError` → 用户看到 "无法连接服务器"，反复重试加重服务器负担（每次都重新渲染）。
- **建议**:
  1. 将 read 超时单独提升至 180–300s（`httpx.Timeout(connect=5, read=300, write=10, pool=5)`），并通过 `request_server_api` 暴露 `read_timeout` 参数。
  2. 后端实现增量轮询（提交渲染 job → poll 结果），避免长连接卡死 TShock REST 线程。

### ST-2.3 🟡 medium — `payload.get("base64")` 数据格式校验缺乏长度上限

- **位置**: `server_tools.py:180-183`
- **现状**: 仅检查 `isinstance(b64, str) and b64`，未对 `len(b64)` 做防御性上限校验。
- **影响**: 若 TShock 端 bug / 攻击者控制后端，返回数 GB base64 字符串 → bot 进程立刻 OOM 崩溃。
- **建议**: 加 `if len(b64) > 200 * 1024 * 1024:` 直接拒绝，并 `logger.warning` 上报。

### ST-2.4 🟡 medium — 非 OneBot V11 适配器的"成功提示"会泄露 `fileName`

- **位置**: `server_tools.py:189`
- **现状**: 当 `bot.adapter.get_name() != "OneBot V11"` 时，`reply` 文本里包含 `response.payload.get('fileName', '')`，由后端控制。
- **影响**: 后端若返回 `world.wld\n[admin token: xxx]` 或长串错误信息，会被原样写到群里。属于低概率信任域内容泄漏。
- **建议**: 对 `fileName` 做白名单（仅允许 `^[\w.\-]+$`），超长截断。

### ST-2.5 🟢 low — 失败 reply 使用动词 "查询" 不一致（同文件其它处用 "下载" / "执行"）

- **位置**: `server_tools.py:163,173,177,182`
- **现状**: `全亮地图` 命令的失败动词是 "查询"，而命令展示名为 "全亮地图"。其他命令的 reply_failure 动词都与命令名一致。
- **影响**: 仅是文案不一致，对用户造成轻微困惑（"查询失败" 容易让用户以为发错了别的命令）。
- **建议**: 改为 `reply_failure("全亮地图", ...)` 或专门的 "生成地图"。

### ST-2.6 🟢 low — 缺少 `at` 前缀，群里多人时不知道是回给谁

- **位置**: `server_tools.py:163,173,177,182,187`
- **现状**: `全亮地图` 与 `下载地图` 的 `bot.send` 都没有 `at + " " +` 前缀，而 `执行` 与 `发送` 都有。
- **影响**: 群内有多人同时调用时，不容易辨认结果归属。
- **建议**: 与其他 handler 风格一致，前置 `at = OBV11MessageSegment.at(int(event.get_user_id()))`。

---

## 3. `下载地图 <服务器 ID>`（`server_tools.py:192-265`）

### ST-3.1 🔴 critical — 路径遍历：fallback 分支用 API 返回的 `fileName` 拼 `/tmp/<file_name>`，可写任意位置

- **位置**: `server_tools.py:239`、`262-265`
- **现状**:
  ```python
  file_name = response.payload.get("fileName") or "world.wld"
  ...
  file_data = base64.b64decode(b64)
  file_path = Path("/tmp") / file_name
  file_path.write_bytes(file_data)
  ```
  `file_name` 完全由 TShock 后端控制；`Path("/tmp") / "../etc/passwd"` 等价于 `Path("/etc/passwd")`，且 `/` 开头时 `Path("/tmp") / "/usr/bin/python"` 直接等于 `Path("/usr/bin/python")`（pathlib 的 `/` 运算符在右侧为绝对路径时丢弃左侧）。
- **影响**:
  - 攻击场景：TShock 插件被劫持 / 恶意维护者修改 `/nextbot/world/world-file` 的实现 → bot 进程在 fallback 路径下覆盖任意文件（覆盖 `bot` 自身二进制 / `/etc/cron.d/*` / `~/.ssh/authorized_keys`）。
  - bot 在 docker / systemd 下若以 root 运行将直接 RCE；若以非 root 运行也能覆盖应用自身 `app.db` / `data/` 数据。
  - 当前 OneBot V11 走 `upload_group_file(file=base64://...)` 路径不会 hit 这个分支，但任何非 V11 适配器（V12 / Kook / Telegram bridge）都会落入这里。
- **复现**:
  1. 部署 bot 时挂载非 V11 适配器（或临时 `bot.adapter.get_name()` patched）。
  2. 后端伪造 response: `{"base64": "<wld_bytes>", "fileName": "../../etc/cron.d/evil"}`。
  3. 文件写入 `/etc/cron.d/evil`，10 分钟内执行任意 shell 命令。
- **建议**:
  1. 强制白名单：`safe_name = Path(file_name).name`，再校验 `re.fullmatch(r"[\w\-.]+\.wld", safe_name)`，否则用 `world.wld` 兜底。
  2. 用 `tempfile.NamedTemporaryFile(suffix=".wld", delete=False)` 替代手拼路径。
  3. 严禁将后端字符串直接拼到 `Path("/tmp")`。

### ST-3.2 🔴 critical — `/tmp` 文件无清理：每次调用增加一份 50–200 MB，未限速 / 未删除

- **位置**: `server_tools.py:262-265`
- **现状**: fallback 分支 `file_path.write_bytes(file_data)`，写完之后没有 `try/finally unlink`、没有任何后台清理任务，文件名也固定为 `world.wld`（或后端给定），下次同名会覆盖但仍占用磁盘。
- **影响**:
  - 多服务器场景下，每个 server.id 触发一次 `下载地图` 都会留下一份 .wld；磁盘使用量随服务器数与调用次数线性增长。
  - `/tmp` 在很多 systemd 环境下是 tmpfs，其大小一般是 RAM 的 50%。一次 200MB 世界 + 50 个 server 直接打满 tmpfs，导致整个进程无法分配新 tmp 文件，部分系统调用会返回 ENOSPC。
- **复现**: 配置 5 个服务器 + 每个 100MB 世界，连续触发 `下载地图`，观察 `df -h /tmp`。
- **建议**:
  1. 使用 `with tempfile.NamedTemporaryFile(delete=False)` 取得唯一 path；发送完成后立刻 `Path(path).unlink(missing_ok=True)`，写在 `try/finally`。
  2. 启动时清理 `/tmp/world*.wld` 残留（仅清理本进程命名规则匹配的文件）。
  3. 加每个用户的 cooldown（比如 5 分钟一次）+ 全局 semaphore。

### ST-3.3 🔴 critical — base64 全量 in-memory：与全亮地图同等 OOM 风险，且 wld 文件更大

- **位置**: `server_tools.py:225-260`
- **现状**: 与 ST-2.1 相同，`response.payload["base64"]` 持有完整世界文件 base64（Large 世界压缩后通常仍 50–200 MB；base64 膨胀 4/3 倍）。`upload_group_file(file=f"base64://{b64}")` 会再做一次 copy（OneBot 适配器底层把 base64 → bytes → 写入临时上传 buffer）。
- **影响**: 单次峰值内存 ≈ 4× 世界文件大小；并发两次足以打爆默认 docker container。
- **复现**: 与 ST-2.1 类似。
- **建议**:
  1. 后端 `/nextbot/world/world-file` 改为返回下载 URL（短时签名），bot 流式 `httpx.AsyncClient.stream` 落地 → 调用 OneBot 的 `file://` 上传。
  2. 短期：拿到 `b64` 后立刻 `del response.payload["base64"]`；用 `base64.b64decode` 后立刻 `del b64`。
  3. 加 per-server semaphore + 用户级 cooldown。

### ST-3.4 🟠 high — 60 秒超时对世界文件下载偏短

- **位置**: `server_tools.py:228`
- **现状**: 与 ST-2.2 相同。Large 世界 .wld 文件压缩后 50–150 MB；通过 HTTP/JSON+base64 传输需要承担 ~33% 膨胀，60 秒在跨地域 / 弱网下可能超时。
- **影响**: 用户重复调用，又触发 ST-3.2 / ST-3.3。
- **建议**: 与 ST-2.2 一致——拆分 connect / read 超时；read ≥ 300s。

### ST-3.5 🟠 high — `file_name` 不可信也直接传给 OneBot `upload_group_file`

- **位置**: `server_tools.py:248-260`
- **现状**: 即便不走 fallback，`upload_group_file(name=file_name)` 中的 `name` 仍由后端控制，`file_name` 没经过任何过滤。
- **影响**:
  - OneBot 协议端（go-cqhttp / Lagrange）通常会做基础校验，但 `name` 含 `\n`、`%00`、`\\..` 时不同实现行为不一致，部分会写到 QQ 文件存储的子路径上。
  - 用户群可见的文件名可被后端伪造成钓鱼名称（"重要更新.exe"）。
- **复现**: 后端响应 `{"fileName": "重要更新.exe"}` → 群里看到一个 .exe 文件挂在 bot 名下。
- **建议**:
  1. 强制 `re.fullmatch(r"[\w\-.一-鿿]+\.wld", file_name)`，否则用 `f"world-{server.id}.wld"` 兜底。
  2. 在日志中记录原始与归一化后名称的差异（便于排查后端异常）。

### ST-3.6 🟡 medium — fallback 分支成功提示泄露 `/tmp` 路径

- **位置**: `server_tools.py:265`
- **现状**: `await bot.send(event, f"✅ 下载成功，文件已保存：{file_path}")`，将 `/tmp/world.wld` 这种内部路径回显到聊天。
- **影响**: 信息泄漏（让攻击者了解 bot 部署目录结构 / 用户）；同时违反全局规范第 7 条（前后端文案应解耦，不直接暴露内部细节）。
- **建议**: 仅展示 `file_name`（归一化后）+ 文件大小，不含路径。

### ST-3.7 🟡 medium — 失败 reply 动词使用 "下载"，但 fallback 成功提示直接写 ✅，不走 reply_success

- **位置**: `server_tools.py:265`
- **现状**: 失败用 `reply_failure("下载", ...)`，成功却用裸 `f"✅ 下载成功..."`，未通过 `reply_success("下载")` / `reply_block`，与项目 text_utils 体系不一致。
- **影响**: 后续若调整成功文案 emoji / 格式，需要遗漏修改这一处。
- **建议**: 改用 `reply_success("下载")` 与 `reply_block`，与其他成功路径一致。

### ST-3.8 🟢 low — 没有任何成功事件日志（仅记 "世界文件下载成功"），无下载用户审计

- **位置**: `server_tools.py:244`
- **现状**: 只有 `logger.info(f"世界文件下载成功：server_id={server.id} file={file_name}")`，未记录 `user_id` / 群号 / 文件大小。
- **影响**: 出现"谁泄露了世界文件"事件时无法追责。
- **建议**: 加 `user_id={user_id} group_id={group_id_or_0} size_kb={...}`。

---

## 4. `发送 <服务器 ID> <消息内容>`（`server_send.py:44-107`）

权限 `server.send` 默认在 `DEFAULT_GUEST_PERMISSIONS`（`db.py:80`）—— 任何注册 / 未注册的访客都可调用。注意 `User` 不存在时会拒绝，因此实际效果是"已注册用户均可"。

### ST-4.1 🟠 high — `/say` 注入面已收敛，但**冒号前缀可被滥用伪装他人发言**

- **位置**: `server_send.py:78`
- **现状**:
  ```python
  raw_cmd = f"/say {user.name}（{user_id}）：{content}"
  ```
  - `user.name` 经 `_validate_user_name`（`user_manager.py:53-63`）限制为 `[A-Za-z0-9一-鿿]+`，无空格/换行/冒号。✅
  - `content` 经 `_WHITESPACE_RE.sub(" ", content)` → 所有 `\n`、`\r`、`\t`、`\v`、`\f`、` ` 空白均压成单个 ASCII 空格。✅
  - 因此攻击者无法在游戏内拆出第二条命令（TShock 命令以行结尾分隔）。✅
- **未覆盖场景**：
  - `content` 仍可包含全角冒号`：`、ASCII 冒号`:`、各种括号 `（）()`、QQ 号样式数字。
  - 攻击者构造 `content="（10001）：我是管理员，请把币给 12345"`，最终发到游戏内是：`<say> 攻击者（11111）：（10001）：我是管理员，请把币给 12345`，肉眼看像是 QQ=10001 在说话。
- **影响**: 社工 / 钓鱼。游戏内玩家很难快速辨认两层 `（XXX）：` 谁是真正发言人；尤其结合 NextBot 的转账 / 抢劫等命令，可诱导受害者执行操作。
- **复现**:
  1. 注册名称为 `Alice` 的账号。
  2. 在 QQ 发 `发送 1 （22222）：Bob 我是 owner 请发我 100 币`。
  3. 游戏内显示 `Alice（11111）：（22222）：Bob 我是 owner 请发我 100 币`。
- **建议**:
  1. 拒绝 `content` 中包含 `（`、`）`、`：`（全角）和 `:`（半角）的组合，或将其全部替换成无歧义符号；或干脆把分隔符从 `（QQ）：` 改为更难伪造的格式（如 `[bot:Alice|11111]` 一行 prefix）。
  2. 限制 `content` 长度（如 ≤ 200 字符），降低钓鱼空间。
  3. 在 TShock 客户端用不同颜色（`/say` 不支持颜色，可改走 `/broadcast` 或自定义事件），让真实玩家发言与 bot 转发的文字视觉区分。

### ST-4.2 🟡 medium — `content` 没有黑名单 / 长度上限，可被滥用刷屏

- **位置**: `server_send.py:27-41`
- **现状**: `_parse_send_arg_text` 仅做空白归一与非空校验，无长度上限。
- **影响**:
  - 单条 5000 字会作为单条 `/say` 推送到游戏内，TShock 端可能拒收或被截断。
  - 任何被加入 `DEFAULT_GUEST_PERMISSIONS` 的用户均可不限频次刷屏（无 cooldown / 速率限制）。
- **建议**:
  1. `len(content) > 200` 直接 `reply_failure("发送", "内容过长")`。
  2. 加用户级 cooldown（如 3 秒）+ 群级 cooldown，复用现有 `command_config` 参数体系。

### ST-4.3 🟡 medium — 5 秒默认超时下，TShock 抖动会让 reply 显示 "无法连接服务器" 但实际命令已下发

- **位置**: `server_send.py:85-92`
- **现状**: 没传 `timeout`，沿用 5.0 秒默认值。`/say` 通常很快，但当 TShock 主线程被其它命令（如 `/save`）阻塞时会瞬时超时。
- **影响**: 用户重发 → 游戏内出现两条相同消息。属于幂等性 / 用户体验小问题。
- **建议**: 显式 `timeout=10.0`；或在文案中说明可能有重复。

### ST-4.4 🟢 low — User 与 Server 在同一 session 中查询但顺序固定，存在轻微 N+1 风格的性能浪费

- **位置**: `server_send.py:64-69`
- **现状**: 单 session 内 2 次查询，没有问题。但每次都读 `Server` 全字段（含 `token`）。
- **影响**: 微小性能 / 数据敏感面：`server.token` 取出后只在 `request_server_api` 内部使用，但已驻留在 Python 对象上下文一段时间。
- **建议**: 可忽略，或改为 `session.query(Server.id, Server.name, Server.ip, Server.restapi_port, Server.token)`。

### ST-4.5 🟢 low — 失败原因 reply 文案 `f"{get_error_reason(response)}"` 多余 f-string

- **位置**: `server_send.py:95`、`server_tools.py:103, 177, 235`
- **现状**: 直接 `reply_failure("发送", get_error_reason(response))` 即可，包 f-string 没意义。
- **影响**: 仅代码风格。
- **建议**: 去掉 `f""`。

---

## 5. 跨命令 / 横切问题

### ST-5.1 🟠 high — `_issue_raw_command` 的 helper 已在 `shop.py:124-131` 实现，但 `执行 / 发送` 没复用

- **位置**: `shop.py:124-131`、`server_tools.py:92-104`、`server_send.py:84-96`
- **现状**: 三处代码重复同一模式 `try request_server_api → except TShockRequestError → if not is_success`。
- **影响**: 新增的横切策略（重试、日志、metrics）很难统一。
- **建议**: 将 `_issue_raw_command` 提升为公共 helper（如 `nextbot/tshock_api.py: issue_raw_command(server, cmd, *, timeout=5.0)`），返回 `(ok: bool, error_reason: str, payload: dict)`，所有调用方共用。

### ST-5.2 🟠 high — 大文件 base64 模式（map-image / world-file）无统一限流 / 缓存 / 流式下载

- **位置**: `server_tools.py:166-265`
- **现状**: 两个大对象 endpoint 走完全相同的"全量 JSON + base64"流程，没有抽象、没有共享 semaphore。
- **影响**: 复现前述 ST-2.1 / ST-3.3 OOM。
- **建议**:
  1. 抽出 `fetch_large_payload(server, path, *, max_size_mb, on_chunk)` helper，内部使用 `httpx.AsyncClient.stream` + 边读边写临时文件 + 读完成后只暴露 path。
  2. 在该 helper 中实现 per-server 信号量（`asyncio.Semaphore` keyed by `server.id`），同一服务器最多 1 个并发大请求。

### ST-5.3 🟡 medium — `Server` 查询都做完整对象加载，token 意外被序列化的风险面分散

- **位置**: 全部 4 个 handler 的 `session.query(Server)`
- **现状**: 取出整个 `Server`（含 `token`），后续仅在 `request_server_api` 内部使用 `server.token`。但任何调试日志、`repr(server)`、异常 traceback 都会把 token 暴露出去。
- **影响**: 信息泄漏面。当前未发现实际泄漏点（`logger.info` 仅打印 `server.id`），但风险随代码演进上升。
- **建议**:
  1. 在 `Server` 上重写 `__repr__` 屏蔽 `token`。
  2. 或将 `server.token` 改为只读 lazy 属性，从 `keyring` / 环境变量等外部源加载，DB 仅存引用。

### ST-5.4 🟡 medium — `at + " " + reply_failure(...)` 与 `at + "\n" + reply_block(...)` 拼接模式重复 ≥ 8 处

- **位置**: `server_tools.py:89, 99, 103, 110, 122; server_send.py:72, 75, 91, 95, 99`
- **现状**: 同一拼装模板分散在多个 handler。后续若调整 at 与文本之间的分隔（如改用零宽空格），需要改多处。
- **建议**: 在 `text_utils.py` 增加 `at_prefix(event, content, *, sep=" ")` helper，handler 调用 `bot.send(event, at_prefix(event, reply_failure(...)))`。

### ST-5.5 🟡 medium — `int(args[0])` / `int(server_id_text)` 会接受负数与超大整数

- **位置**: `server_tools.py:42-43, 152, 210; server_send.py:34-37`
- **现状**: 都用 `int(...)` 解析 server_id；`-1`、`9999999999999` 都能通过类型检查，再走 DB 查询返回 None → "服务器不存在"。
- **影响**: 不算漏洞，但有助于攻击者扫描 server.id 空间（虽然 `Server.id` 默认是用户填入的小整数）。也会污染 DB 慢查询日志。
- **建议**: 加 `if server_id <= 0: raise_command_usage()` 防御。

### ST-5.6 🟢 low — 4 个 handler 都没在异常路径下统计命令执行（`increment_command_execute_total`）

- **位置**: `command_config.py:30 STAT_COMMAND_EXECUTE_TOTAL`、`server_tools.py` / `server_send.py`
- **现状**: handler 通过 `@command_control` 装饰器隐式累计，没问题。但当 `raise_command_usage()` 在 handler 中早早抛出时，是否计数取决于 `command_control` 实现。
- **影响**: 统计准确性。
- **建议**: 不在本次审计范围，保留观察。

### ST-5.7 ℹ️ info — 4 个 handler 都没有"服务器配置缺失 token / ip"的兜底

- **位置**: 所有 handler 在拿到 `server` 后直接传给 `request_server_api`
- **现状**: `Server.ip`、`Server.token`、`Server.restapi_port` 都是 `nullable=False`，理论上不会为空。但被运营误改成空字符串时，会拼出 `http://:0/...` 的怪 URL。
- **影响**: 极低概率边界 case。
- **建议**: 在 `request_server_api` 入口加 `if not server.ip or not server.restapi_port` 防御。

---

## 严重度汇总

| ID | 命令 | 标题 | 严重度 |
|---|---|---|---|
| ST-2.1 | 全亮地图 | 大世界 base64 PNG 全量驻留内存 | 🔴 |
| ST-3.1 | 下载地图 | fallback 路径遍历 (`Path("/tmp")/file_name`) | 🔴 |
| ST-3.2 | 下载地图 | `/tmp` 文件无清理，磁盘耗尽 | 🔴 |
| ST-3.3 | 下载地图 | base64 全量内存导致 OOM | 🔴 |
| ST-2.2 | 全亮地图 | 60s 超时不足以渲染 Large 世界 | 🟠 |
| ST-3.4 | 下载地图 | 60s 超时偏短 | 🟠 |
| ST-3.5 | 下载地图 | 后端 `fileName` 未清洗即传给 OneBot | 🟠 |
| ST-4.1 | 发送 | `/say` 内容含全角 `（）：` 可伪装他人发言 | 🟠 |
| ST-5.1 | cross | rawcmd helper 未复用 | 🟠 |
| ST-5.2 | cross | 大对象下载缺少限流 / 流式 helper | 🟠 |
| ST-1.1 | 执行 | 命令原文回显敏感参数 | 🟡 |
| ST-1.2 | 执行 | result_text 无截断 | 🟡 |
| ST-2.3 | 全亮地图 | base64 长度无上限 | 🟡 |
| ST-2.4 | 全亮地图 | 非 V11 适配器泄露 fileName | 🟡 |
| ST-3.6 | 下载地图 | fallback 暴露 `/tmp` 路径 | 🟡 |
| ST-3.7 | 下载地图 | 成功 reply 不走 `reply_success/block` | 🟡 |
| ST-4.2 | 发送 | content 无长度上限与 cooldown | 🟡 |
| ST-4.3 | 发送 | 5s 超时易误报 | 🟡 |
| ST-5.3 | cross | Server 全字段加载，token 暴露面分散 | 🟡 |
| ST-5.4 | cross | at + 拼接模板重复 | 🟡 |
| ST-5.5 | cross | server_id 接受负数 / 超大整数 | 🟡 |
| ST-1.3 | 执行 | command 不强制 `/` 前缀 | 🟢 |
| ST-1.4 | 执行 | 默认 5s 超时不足 | 🟢 |
| ST-2.5 | 全亮地图 | 失败动词 "查询" 不一致 | 🟢 |
| ST-2.6 | 全亮地图 | 缺 `at` 前缀 | 🟢 |
| ST-3.8 | 下载地图 | 缺 user_id / group_id 审计日志 | 🟢 |
| ST-4.4 | 发送 | Server 全字段读取 | 🟢 |
| ST-4.5 | 发送 | 多余 f-string | 🟢 |
| ST-1.5 | 执行 | `int(user_id)` 兼容性 | ℹ️ |
| ST-5.6 | cross | 异常路径统计未对齐 | 🟢 |
| ST-5.7 | cross | 服务器字段缺失兜底 | ℹ️ |

## 验证 / Not Found

- ✅ DB 会话均使用 `try/finally session.close()`，无泄漏。
- ✅ `user.name` 在注册 / 改名两个入口都经过 `_validate_user_name`（`user_manager.py:53-63`）正则约束，无法注入空白 / 冒号。
- ✅ `_WHITESPACE_RE.sub(" ", content)` 覆盖所有 `\s` 字符（`\n\r\t\v\f ` 等），确认无法跨行注入新 TShock 命令。
- ✅ `request_server_api` 对 `path` 做了 `quote(..., safe="/")`，防止 path traversal 漏洞（注意：是对**路径段**编码，不是对 query 参数；query 里 `cmd` 由 httpx 自动编码）。
- ❓ 未追踪 OneBot V11 适配器在 `upload_group_file(file=base64://<huge>)` 内部是否会先解码到内存或直接流式上传到 NTQQ —— 若内部仍是全量解码，则 ST-3.3 的内存放大会再翻一倍。需要查看 `nonebot-adapter-onebot` 源码或抓包验证。
