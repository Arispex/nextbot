# 用户系统命令审计报告（已二次复查）

**审计对象**：`nextbot/plugins/user_manager.py` 中 `category="用户系统"` 的 5 个命令
**审计日期**：2026-05-06
**复查方式**：trellis-research sub-agent 初审 → 主代理逐条对照源码 + 调用链验证

## 严重级别分布（复查后）

- 🔴 必修：**1**（注册并发竞态）
- 🟠 应修：**4**（多服务器串行 await ×2、注册成功反馈缺失、rename 原子性、/tmp 文件累积）
- 🟡 建议：**4**（path 编码 defense-in-depth、get_session 复用、handler 内多 session、name 索引）
- 🟢 观察：**2**（int(user_id) 兼容性、tuple 三态语义）

**复查剔除的误报：**
- 路径注入 🔴（降级为 🟡 defense-in-depth）—— 经源码验证 `_validate_user_name` (line 47) + webui `_normalize_user_name` (line 69) 都用相同正则 `[A-Za-z0-9一-鿿]+` 屏蔽了所有 URL 危险字符。理论上需要"直接 SQL 写入脏数据"才能触发，攻击前提已经超过 bot 命令面。

---

## 🔴 必修

### F-1.1 — 注册并发竞态可让同名账户双写

- **位置**：`user_manager.py:150-167`
- **现象**：handler 内先 `select` 再 `insert`，未加锁。`User.name` 字段在 `db.py:106` 没有 `unique=True` 约束、没有索引。
- **复现**：两个不同 QQ 同时发 `注册账号 abc`，两次 `func.lower(User.name) == "abc"` 都返回 None，两次 `commit` 都成功 → DB 出现两行 `name="abc"`。
- **影响**：
  - 后续 `用户信息 abc` 因名字重复返回 "name_ambiguous"
  - 白名单写入双方都成功，但用户系统层认为名字"不唯一"
  - 业务一致性破坏，难以靠运营修复
- **修复方案**：
  1. `db.py` 给 `User.name` 加 `unique=True`（注意 SQLite 大小写敏感，建议存一个 `name_lower` 列做唯一索引）
  2. `handle_add_whitelist` 把 select+insert 包 `try/except IntegrityError`，捕获后退回到统一文案"用户名称已被占用"
- **严重级别**：🔴（数据一致性受损 + 无法靠应用层完全消除）

---

## 🟠 应修

### F-1.2 / F-2.2 / F-5.2 — 多服务器同步串行 await

- **位置**：
  - `user_manager.py:60-122`（`_sync_whitelist_to_all_servers`，被注册账号 + 同步白名单共用）
  - `user_manager.py:447-485`（更改用户名称的 remove + add 双串行）
- **现象**：`for server in servers: await request_server_api(server, ...)` 串行等待。`request_server_api` 默认 `timeout=5.0`（`tshock_api.py:52`）。
- **复现**：
  1. 配置 5 台不可达服务器
  2. 触发 `同步白名单`
  3. 实测延时约 `2 × 5 × 5s = 50s`（每台两次请求 GET 白名单 + POST add）
  4. `更改用户名称` 同样问题，延时也 `2 × N × 5s`
- **影响**：单条命令长时间阻塞，N 台服务器线性放大；用户体验差。
- **修复方案**：
  ```python
  async def sync_one(server): ...
  results = await asyncio.gather(*[sync_one(s) for s in servers])
  ```
  N 台并发后总耗时降到约 1 个 timeout（5s）。
- **严重级别**：🟠（无安全风险，但性能影响显著）

### F-1.3 — 注册成功反馈未感知白名单同步结果

- **位置**：`user_manager.py:169-181`
- **现象**：`await _sync_whitelist_to_all_servers(...)` 的返回值被丢弃，handler 直接回复"注册成功"，即使所有服务器同步全失败也一样。
- **复现**：
  1. 配置所有服务器为不可达
  2. 未注册 QQ 发 `注册账号 testuser`
  3. DB 已写入 User，bot 回复 "✅ 注册成功"
  4. 玩家进游戏被白名单拒绝 → 用户被误导以为已可用
- **影响**：用户被误导，问题源排查困难。
- **修复方案**：把 `results` 转成多行附加在回复里（参考同 plugin 的 `同步白名单` 218–225 行的写法），或至少在有失败时追加 "⚠️ N 台服务器白名单同步失败，请稍后使用「同步白名单」重试"。
- **严重级别**：🟠

### F-5.3 — 更改用户名称 commit 与白名单同步无原子性

- **位置**：`user_manager.py:422-485`
- **现象**：先 `user.name = new_name; session.commit()`，然后才循环同步服务器。若所有服务器同步全失败，DB 已是 new_name 但服务器侧仍是 old_name；玩家以新名进入游戏会被白名单拒绝。
- **复现**：
  1. 5 台服务器全不可达
  2. admin 执行 `更改用户名称 X newname`
  3. DB 中 name 已变；回复显示全部 ❌
  4. 玩家以 `newname` 进服务器被踢
- **影响**：DB 与服务器状态漂移；恢复需要手工 SQL 回滚或重命名第二次。
- **修复方案**：
  1. 先尝试至少一台同步成功才 commit DB；或
  2. 同步全失败时自动回滚 DB 并向 admin 反馈（首选）
- **严重级别**：🟠

### F-3.1 / F-4.1 — `/tmp` 截图文件不清理

- **位置**：`user_manager.py:267-284`，`_render_and_send_user_info` 共用
- **现象**：每次成功输出截图都会落一个 PNG 在 `/tmp/user-info-<id>-<ts>.png`，没有任何清理。**注意**：这是项目级别的模式，奖池/抽奖结果/用户背包/地图等命令都一样不清理，并不只是 user_manager 的问题。
- **复现**：连续触发 100 次 `用户信息 X` → `/tmp/user-info-*.png` 累积 100 个。
- **影响**：磁盘 / 容器卷压力；含用户名 + QQ 的截图泄漏到 `/tmp`，多用户主机隐私面。
- **修复方案**：
  1. **局部修**：在发送后 `try/finally screenshot_path.unlink(missing_ok=True)`
  2. **项目级修**（推荐）：抽 `_save_temp_image` helper，所有 plugin 统一管理 `/tmp` 临时文件 + cron 清理
- **严重级别**：🟠（项目级技术债，但单条命令影响有限）

---

## 🟡 建议

### F-2.1 / F-5.1 — `request_server_api` 路径段未做 percent-encode

- **位置**：`tshock_api.py:60` `url = f"http://...{request_path}"`
- **现象**：URL 路径段直接拼字符串，没有 `urllib.parse.quote`。
- **当前真实风险**：**接近零** —— `_validate_user_name` (`user_manager.py:55`) + webui `_normalize_user_name` (`webui_users.py:69`) 都用相同正则 `[A-Za-z0-9一-鿿]+` 屏蔽了所有 URL 关键字符（`/`、`?`、`#`、`&`、`=`、`%`、空格）。要触发只能直接 SQL 改 DB，已经超出 bot 命令面。
- **defense-in-depth 价值**：仍建议在 `tshock_api.py` 层加 `quote(path, safe="/")`。一次修改，5 处调用受益，万一未来有验证遗漏的字段也安全。
- **严重级别**：🟡（不是漏洞，是健壮性建议）

### F-4.2 — `_render_and_send_user_info` 内多次 `get_session`

- **位置**：`user_manager.py:248-249`（外层）+ `_get_sign_dates` (`233-245`) 内部又开 session
- **现象**：单条 `我的信息` / `用户信息` 至少两次开关 session。每次 `get_session()` 都会调 `create_engine()` (`db.py:339-345`)，未复用全局 engine + sessionmaker。
- **影响**：单次 ~50–100ms 额外开销（SQLite 轻），但项目级别（**所有命令都受影响**）值得改。
- **修复方案**：
  1. 局部：合并到一次 session 查询（user + 签到记录）
  2. 项目级：改 `db.py` 让 engine + sessionmaker 全局单例，`get_session()` 只 `session_factory()`
- **严重级别**：🟡（项目级优化项）

### F-DB.1 — `User.name` 字段无索引

- **位置**：`db.py:106` `name: Mapped[str] = mapped_column(String, nullable=False)`
- **现象**：所有按 name 查询（`message_parser.resolve_user_id_arg_with_fallback`、`_validate_user_name` 中的存在性检查）都需要全表扫。
- **影响**：当用户量 < 1000 时几乎不可感知；> 10k 时查询耗时显著上升。
- **修复方案**：`name: Mapped[str] = mapped_column(String, nullable=False, index=True)`，配合 F-1.1 的 unique 一起加。
- **严重级别**：🟡（与 F-1.1 一并修最经济）

### F-2.4 — `(success, reason)` 三态语义压在 2 元组

- **位置**：`user_manager.py:81-96`
- **现象**：`results: list[tuple[Server, bool, str]]` 用 `(True, "")` / `(True, "already")` / `(False, reason)` 表三种状态。当前控制流处理对（先判 `success and reason == "already"`），但耦合度高。
- **影响**：未来加新状态容易写出 `"❌ 同步失败，already"` 类 bug。
- **修复方案**：`Enum` 或 `Literal["new", "exists", "fail"]` 做返回结构。
- **严重级别**：🟡（代码可维护性）

---

## 🟢 观察

### F-1.4 — `int(event.get_user_id())` 跨 adapter 兼容性

- **位置**：`user_manager.py:142-143, 202, 376`
- **现象**：QQ 字符串直接 `int()`。OneBot V11 始终是数字字符串，当前安全；其他 adapter 可能返回非数字 → `ValueError`。
- **当前不可触发**（项目目前只用 OneBot V11）。
- **修复**：未来跨 adapter 时做 `safe_at()` helper。
- **严重级别**：🟢

### F-3.2 — `user.info.user` 默认对所有 guest 开放

- **位置**：`db.py:DEFAULT_GUEST_PERMISSIONS`
- **现象**：任何 guest 都能查任意用户的 coins / 签到 / permissions / group 等。是设计选择，不是 bug。
- **修复**：按业务决定。如要收紧把 `user.info.user` 移出 `DEFAULT_GUEST_PERMISSIONS`。
- **严重级别**：🟢

---

## 推荐处理顺序（给主代理的执行建议）

1. 🔴 **F-1.1 + F-DB.1**：一次 schema 升级（unique=True + index=True + IntegrityError 兜底）
2. 🟠 **F-1.2/2.2/5.2**：`asyncio.gather` 并发化，三处通用，工作量小收益大
3. 🟠 **F-1.3 + F-5.3**：注册反馈 + rename 原子性，体验关键
4. 🟠 **F-3.1/4.1**：`/tmp` 清理，先做局部再考虑项目级 helper
5. 🟡 **F-2.1/5.1**：tshock_api 加 path-quote（defense-in-depth，1 行改动 5 处受益）
6. 🟡 **F-4.2 + F-2.4**：技术债，可放下一轮
