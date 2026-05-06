# 用户系统二审报告

**审计日期**：2026-05-06
**审计范围**：`nextbot/plugins/user_manager.py` 中 5 个 `category="用户系统"` 命令（修复后）
**审计依据**：`findings.md` + 用户最终选择（修 1 必修 + 4 应修 + 4 建议；跳过 F-1.3 / F-5.3 / F-1.4 / F-3.2）

---

## Phase 1: 修复项落实情况

| ID | 修复项 | 状态 | 验证位置 |
|---|---|---|---|
| F-1.1 | `User.name` 唯一约束 + `IntegrityError` 兜底 | ✅ 已落实（采用大小写不敏感唯一索引方案） | `db.py:706-732` `ensure_user_name_unique_schema()`；在 `init_db()` 中注册 `db.py:371`；`user_manager.py:214-218`（注册）+ `user_manager.py:502-508`（rename）的 commit 都有 `try/except IntegrityError` + `session.rollback()` |
| F-1.2 / F-2.2 | 注册账号 / 同步白名单 多服务器并发化 | ✅ 已落实 | `user_manager.py:117-132` `_sync_whitelist_to_all_servers` 使用 `asyncio.gather`；helper `_sync_one_whitelist` (lines 66-114) 内部 try/except 全部 TShockRequestError，gather 不会抛 |
| F-5.2 | 更改用户名称 多服务器并发化 | ✅ 已落实 | `user_manager.py:532-534` `asyncio.gather(*(_rename_one_whitelist(s, ...) for s in servers))`；helper `_rename_one_whitelist` (lines 135-169) 内部 try/except 完整 |
| F-2.1 / F-5.1 | `request_server_api` path quote | ✅ 已落实 | `tshock_api.py:5,58,63` `from urllib.parse import quote` + `safe_path = quote(request_path, safe="/")` |
| F-3.1 / F-4.1 | `/tmp` 截图清理（项目级） | ✅ 已落实 | 新建 `nextbot/screenshot_temp.py` 提供 `temp_screenshot_path` async context manager；12 个 plugin 文件全部接入（about / ban / leaderboard / lottery / menu / permission_manager / player_query / red_packet / shop / tutorial / user_manager / warehouse），`server_tools.py` 按预期保留（属于上传文件路径而非截图） |
| F-DB.1 | `User.name` 索引 | ✅ 已落实 | `db.py:116` `name: Mapped[str] = mapped_column(String, nullable=False, index=True)` 加上 `index=True`；同时在 `ensure_user_name_unique_schema` 失败回退路径里再补一个 `LOWER(name)` 的非唯一索引 |
| F-4.2 | handler 内合并 session | ✅ 已落实 | `_get_sign_dates(session, user_id, days)` (line 286) 改为接收 session 参数；`handle_user_info` (line 391-400) 与 `handle_self_info` (line 426-435) 现在只开一次 session，`user` 查询 + `_get_sign_dates` + `_serialize_user_for_render` 全部在同一 try 块内完成 |
| F-2.4 | 三态 `Literal` | ✅ 已落实 | `user_manager.py:50` `SyncStatus = Literal["new", "exists", "fail"]`；`_sync_one_whitelist` 返回值统一用 `"new"` / `"exists"` / `"fail"`；`handle_sync_whitelist` (lines 271-278) 用 `status == "exists"` / `status == "new"` / 兜底 fail 显示对应文案 |

**Phase 1 结论**：8 项修复全部落实。F-1.1 选择了"大小写不敏感唯一索引（基于 `LOWER(name)`）"而非"在列上加 `unique=True`"，这是更精确的实现（应用层 `func.lower(User.name) == name.lower()` 配套），优于原方案。

---

## Phase 2: 行为不变性

逐 handler 与 `git show HEAD:` 原版本字符串级对比。

### 命令 1 - 注册账号 (`user.register`)
- ✅ 入口校验文案：`_validate_user_name` 4 条返回值文案完全一致（"用户名称不能为空"/"用户名称过长，最多 16 个字符"/"用户名称不能为纯数字"/"用户名称不能包含符号，只能使用中文、英文和数字"），仅正则字面量 `一-鿿` → `一-鿿` 等价改写
- ✅ 错误回复完全一致：`reply_failure("注册", invalid_reason)`、`reply_failure("注册", "该账号已注册")`、`reply_failure("注册", "用户名称已被占用")`
- ✅ 成功回复完全一致：`reply_success("注册")` + 同样的两行 detail
- ✅ 新增 IntegrityError 分支也用 `"用户名称已被占用"`，与 select 时命中的兜底文案保持一致（用户视角无差别）
- ⚠️ **未修（按用户选择跳过 F-1.3）**：注册成功仍不感知 `_sync_whitelist_to_all_servers` 返回值；如果服务器全失败，DB 已写入但回复仍是"注册成功"。这是已知的、用户决定本轮不修的项。

### 命令 2 - 同步白名单 (`user.whitelist.sync`)
- ✅ 入口校验文案完全一致：`reply_failure("同步", "未注册账号")`、`reply_failure("同步", "暂无可同步的服务器")`
- ✅ 关键展示分支文案完全一致：
  - `status == "exists"` → `"{server.id}.{server.name}：ℹ️ 已在白名单中"`（对应原 `success and reason == "already"`）
  - `status == "new"` → `"{server.id}.{server.name}：✅ 同步成功"`（对应原 `success`）
  - `status == "fail"` → `"{server.id}.{server.name}：❌ 同步失败，{reason}"`（对应原 `not success`）
- ✅ 顺序保持：`servers = session.query(Server).order_by(Server.id.asc()).all()` (line 122) → `asyncio.gather(*(... for server in servers))` 按入参顺序返回
- ✅ 整体回复文案完全一致：`at + "\n" + reply_success("同步白名单") + "\n" + "\n".join(lines)`
- ✅ 日志文案完全一致

### 命令 3 - 用户信息 (`user.info.user`)
- ✅ 所有 parse_error 分支文案完全一致：`"用户名称不存在"` / `"用户名称不唯一，请使用用户 QQ 或 @用户"` / `"用户参数解析失败"` / `"用户不存在"`
- ✅ `_render_and_send_user_info` 输出语义不变：`OBV11MessageSegment.image(file=image_uri)` 或 `f"✅ 截图成功，文件：{screenshot_path}"`
- ✅ 截图失败 / 读取失败回复完全一致：`reply_failure("查询", f"{exc}")` / `reply_failure("查询", "读取截图文件失败")`

### 命令 4 - 我的信息 (`user.info.self`)
- ✅ 所有错误文案一致：`reply_failure("查询", "未注册账号")`
- ✅ 截图相关与命令 3 共用 helper，文案不变

### 命令 5 - 更改用户名称 (`admin.rename`)
- ✅ 全部入口校验文案完全一致：`"未找到该用户"` / `"用户名存在重复，请使用 QQ 或 @用户"` / `invalid_reason`
- ✅ 业务校验文案完全一致：`"未找到该用户"` / `"新用户名与当前相同"` / `"用户名称已被占用"`
- ✅ 成功 header 一致：`reply_success("更改")` + 三行 detail（用户 QQ / 旧名称 / 新名称）
- ✅ 单服务器逐项展示文案完全一致：`"{server.id}.{server.name}：✅ 同步成功"` 或 `"{server.id}.{server.name}：移除旧白名单 ✅ 成功；添加新白名单 ❌ 失败，{add_msg}"` 等
- ✅ "暂无服务器"分支保持：`"🖥️ 同步服务器白名单结果：ℹ️ 暂无服务器"`
- ✅ 顺序保持：servers `Server.id.asc()` → gather 按入参顺序返回
- ⚠️ **未修（按用户选择跳过 F-5.3）**：DB commit 仍发生在 multi-server sync 之前，所以服务器全部不可达时 DB 已是 new_name 但服务器仍是 old_name；玩家以 new_name 进游戏会被拒。已知遗留项。

**Phase 2 结论**：所有命令对外行为（输入、输出文案、错误回复、展示顺序）与修复前完全一致，没有破坏性更新。两处遗留差异（F-1.3、F-5.3）属于用户明确选择的不修项，不计入"破坏性更新"。

---

## Phase 3: 新引入问题排查

### NEW-A：`temp_screenshot_path` 退出时机 — ✅ 安全
- 在 `_render_and_send_user_info` (line 321) 中，`async with` 包裹了 `screenshot_url` + 读文件 + base64 编码 + `bot.send`
- 文件解 unlink 发生在 `bot.send` 之后（context manager exit 时），且 OneBot V11 路径下文件内容已经在 `image_uri = f"base64://..."` 时被读入内存，bot.send 不再依赖磁盘文件
- non-OneBot V11 路径下 `bot.send(event, f"✅ 截图成功，文件：{screenshot_path}")` 只发送字符串路径不发图，文件被删与否不影响发送本身（但用户那边路径会失效——这是 dev/debug 路径，问题与原版相同）

### NEW-B：`IntegrityError` 后是否正确 rollback + close — ✅ 安全
- `handle_add_whitelist` (lines 214-218)：`except IntegrityError: session.rollback(); ... await bot.send(...); return`，外层 `try/finally` 保证 close
- `handle_rename` (lines 502-508)：同样 rollback + send + return；外层 finally 保证 close
- 两处都先 rollback 后 send，避免 send 抛错时 session 已经 rollback 干净

### NEW-C：`_get_sign_dates(session, ...)` 调用方 — ✅ 安全
- 唯一两个调用点 `handle_user_info` (line 398) 和 `handle_self_info` (line 433) 都在外层 `try/finally session.close()` 保护内
- 没有任何代码再开一次 session 调 `_get_sign_dates`
- 函数签名变化没有遗漏调用方

### NEW-D：`asyncio.gather` 异常吞噬 — ✅ 安全
- `_sync_one_whitelist` (lines 66-114) 所有 `await request_server_api(...)` 都在 `try/except TShockRequestError` 内消化为 `("fail", reason)` 返回值，**不会抛异常**
- `_rename_one_whitelist` (lines 135-169) 同样：两个 await 各自 try/except，函数始终返回 5 元组
- 因此 `asyncio.gather(...)` 既不会因为一个失败而中断其他，也不需要 `return_exceptions=True` —— 这是正确实现
- 唯一的剩余 risk：如果 `request_server_api` 内 `httpx.AsyncClient(...)` 构造抛非 `httpx.RequestError` 异常（如 RAM OOM 等），会逃出 helper。但这个原版也无法 catch，且不在当前审计范围。

### NEW-E：`quote(path, safe="/")` 行为 — ✅ 安全
- `urllib.parse.quote` 默认对 ASCII alphanumeric + `_.-~` + safe 字符不编码，其余 percent-encode
- 所有调用 `request_server_api` 的 path 形如 `/nextbot/whitelist/add/{name}`，name 已通过 `_validate_user_name` 限制为 `[A-Za-z0-9一-鿿]+`：英数字不变；中文 → UTF-8 percent-encoding（如 `张三` → `%E5%BC%A0%E4%B8%89`）
- httpx 内部本就会对 raw 中文做 percent-encoding 后再上行；显式 quote 与 httpx 隐式行为产生相同 on-wire bytes，**没有引入双编码**或破坏现有正常 path
- query 参数（`token=...`）通过 `params=query` 传入而不在 path 段，所以 `quote(path, safe="/")` 不会触碰 query 部分
- 注意：`_rename_one_whitelist` 中的 `old_name` 来源于 DB `user.name`，理论上如果存在历史脏数据未过 validation，可能含 `%` 字符，会被 `quote` 二次编码。但这种数据原本就违反 schema 规范，不属于本次修复回归

### NEW-F：`ensure_user_name_unique_schema` 启动行为 — ✅ 安全
- `init_db()` 顺序：先 `Base.metadata.create_all(engine)` (line 360) → 各 ensure_*_schema → `ensure_user_name_unique_schema` (line 371) → 默认 group/stat
- 所以 `user` 表必然存在
- `CREATE UNIQUE INDEX IF NOT EXISTS` 是幂等的；如果存在历史重复 name 数据，索引创建会失败，进入 `except`，logger.warning 不阻断启动，并降级创建非唯一索引
- 满足"开箱即用 DB 自动迁移"的用户验收标准

**Phase 3 结论**：未发现新引入的漏洞 / 缺陷 / 资源泄漏 / 兼容性破坏。

---

## Phase 4: 回归审计 — 用户系统是否仍有漏洞

最后一轮完整重读 5 个 handler，按维度排查：

| 维度 | 状态 | 说明 |
|---|---|---|
| SQL 注入 | ✅ 无 | 所有查询都是参数化 ORM；`func.lower(User.name) == name.lower()` 是 SQLAlchemy 函数，参数绑定 |
| 命令注入 | ✅ 无 | 没有 raw shell / TShock RawCmd 拼接（rename 走的是 `/nextbot/whitelist/remove/add` 而非 RawCmd） |
| 路径注入 | ✅ 已防御 | `_validate_user_name` 主防 + `tshock_api.quote` defense-in-depth |
| 权限校验 | ✅ 无 | 5 个 handler 都用 `@require_permission(...)` 装饰；handle_rename 用 `admin.rename` 与其他严格分离 |
| 用户输入校验 | ✅ 无 | `_validate_user_name` 检查空 / 长度 / 纯数字 / 字符集；`int(event.get_user_id())` 仅用于 OneBot V11 at segment 构造（用户选择 F-1.4 不修） |
| 越权 | ✅ 无 | rename 仅在 `admin.rename` permission 下；自己改自己名也走同一路径，文案有 "新用户名与当前相同" 兜底 |
| 信息泄露 | ✅ 无 | API 错误 reason 会回显给用户（如"无法连接服务器"/"端点不存在"），但不含 token / 配置等敏感信息 |
| 竞态条件 | ✅ 已处理 | F-1.1 通过唯一索引 + IntegrityError 兜底解决了"同名 register 双写"；同名 rename 也走相同保护 |
| 错误处理路径 | ✅ 完整 | helper 内部 try/except TShockRequestError 全部消化；handler 内 IntegrityError 已加 try/except 并 rollback |
| Session 关闭 | ✅ 完整 | 所有 `get_session()` 都配 `try/finally session.close()` |
| 部分成功反馈 | ⚠️ 已知遗留 | F-1.3 注册阶段服务器全失败仍返回成功 / F-5.3 rename DB 与服务器漂移：用户明确选择不修 |
| N+1 查询 | ✅ 无 | 单 user 查询，无循环内 SQL |
| 多余 session | ✅ 已优化 | `_get_sign_dates` 接收 session，`handle_user_info` / `handle_self_info` 单 session |
| 串行 await | ✅ 已并发 | `_sync_whitelist_to_all_servers` + `handle_rename` 多服务器循环都用 `asyncio.gather` |
| DB 索引 | ✅ 已加 | `User.name index=True` + `LOWER(name)` 唯一索引 |
| 文案规范 | ⚠️ 部分违规（已知项目状态） | 所有 `reply_failure("注册", "...")` / `reply_failure("查询", "...")` 等使用 `动作 + 失败 + 原因` 形式，其中"动作"使用 `注册` / `同步` / `查询` / `更改` 而非全局规则推荐的 `保存` / `创建`。但这是项目历史一致风格，全局命令都这样，超出本次审计范围 |

**Phase 4 结论**：用户系统的可观测漏洞 / 缺陷 / 性能问题在本轮范围内已基本清空。两个遗留点（F-1.3、F-5.3）是用户明确选择不修。

---

## 验收标准达成情况

| 标准 | 状态 | 说明 |
|---|---|---|
| 1. 无破坏性更新 | ✅ 达到 | 5 个命令的输入 / 输出文案 / 错误回复 / 展示顺序与修复前完全一致；新增 `IntegrityError` 分支文案与原 select 兜底统一 |
| 2. 开箱即用（DB 自动迁移） | ✅ 达到 | `ensure_user_name_unique_schema` 注册到 `init_db()`；幂等；冲突时降级到非唯一索引 + warn，不阻断启动 |
| 3. 用户系统再无漏洞缺陷可优化空间 | ⚠️ 基本达到（含已知遗留） | 本轮选定的 9 项全部完成；2 项用户明确跳过的项（F-1.3、F-5.3）为已知遗留 |

---

## 总体结论

**通过（含 2 项用户明确选择不修的已知遗留）**

- Phase 1 & 2：8 项修复全部落实，5 个命令对外行为完全保持一致，无破坏性更新
- Phase 3：未发现新引入的漏洞 / 缺陷 / 资源泄漏
- Phase 4：用户系统的可观测漏洞 / 缺陷 / 性能问题基本清空
- 工具验证：
  - `pyright`：0 errors / 0 warnings on changed files
  - `ruff`：仅遗留 pre-existing E501 行长 / I001 import 排序，C901 复杂度从 17 降到 14（下降）

**建议下一步**：
1. 如果用户希望 100% 闭环，可再开一轮 task 修 F-1.3（注册成功反馈感知 sync 结果）+ F-5.3（rename 原子性）
2. F-1.4（int(event.get_user_id()) 跨 adapter 兼容）可在引入第二个 adapter 时统一抽 `safe_at()` helper

---

## 附录：变更文件清单

- `nextbot/db.py` — `User.name index=True` + `ensure_user_name_unique_schema()` + `init_db()` 注册
- `nextbot/tshock_api.py` — path quote defense-in-depth
- `nextbot/screenshot_temp.py` — 新增 `temp_screenshot_path` async context manager
- `nextbot/plugins/user_manager.py` — 主改动文件（5 个 handler 覆盖）
- 11 个其他 plugin（about / ban / leaderboard / lottery / menu / permission_manager / player_query / red_packet / shop / tutorial / warehouse）— 接入 `temp_screenshot_path`
- `nextbot/plugins/server_tools.py` — 按预期保留（属于上传文件而非截图，不在 F-3.1/4.1 范围）
