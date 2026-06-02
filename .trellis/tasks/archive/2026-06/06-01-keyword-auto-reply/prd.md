# feat: 关键词自动回复 + WebUI 管理页

## Goal

新增一套"关键词自动回复"系统：消息匹配关键词时自动回复预设内容。规则由管理员通过 WebUI 增删改查；每条规则可单独开关「艾特用户」「引用回复」。涵盖范围：所有走 bot.py 私聊门面 + 群消息门面的消息（白名单群 / 白名单群临时会话 / owner 好友私聊）。

## Requirements

### R1 — DB 模型 `KeywordReply`

新增表 `keyword_reply`（`nextbot/db.py`），字段：

| 字段 | 类型 | 默认 / 约束 |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `keyword` | String | NOT NULL, 1-50 chars, **不区分大小写匹配** |
| `reply` | String | NOT NULL, 1-500 chars, 支持换行 |
| `enabled` | Boolean | default True |
| `at_user` | Boolean | default True — 命中后是否在回复前加 `@<user>` |
| `quote_reply` | Boolean | default True — 命中后是否以 reply 引用原消息 |
| `created_at` | DateTime | UTC 自然时间，用于排序 |

不加 `match_mode` 字段，固定 **包含匹配（substring contains）**，简单可预测。**不**对 keyword 加 UNIQUE 索引（允许多条规则共用一个关键词触发多条回复 —— 但本期固定"第一个命中即 break"，多条相同关键词只有 created_at 最早那条生效，提示用户避免重复）。

Migration：参照 `ensure_user_password_hash_schema` 模式，加 `ensure_keyword_reply_schema()` 并注册到 `_run_migration` 链。

### R2 — Plugin `nextbot/plugins/auto_reply.py`

注册一个 NoneBot matcher：

```python
auto_reply_matcher = on_message(priority=100, block=False)

@auto_reply_matcher.handle()
async def handle_auto_reply(bot: Bot, event: Event) -> None:
    ...
```

**为什么 priority=100 + block=False**：
- 让所有命令 matcher（priority 默认 1）先处理；命令消息（如 `/签到`、`签到`）走命令分支后不应触发自动回复
- block=False 让命令并行匹配——如果消息既命中命令又命中关键词，命令优先（priority 更低）
- 实际上更稳的做法：handler 内部检查 message text 是否以 `COMMAND_START` 配置（"/" 或 ""）开头并对应已注册命令，是则跳过。**简化做法**：检查 `event.get_message().extract_plain_text().strip()` 是否以 `/` 开头，开头则视为命令尝试，跳过自动回复。这个简化版本能覆盖 99% 场景。

**handler 流程**：
1. 仅处理 `message_type` 为 `group` / `private`（已经被 bot.py 门面过滤过）
2. 提取消息纯文本 `text = event.get_message().extract_plain_text().strip()`
3. 若 `text` 空或以 `/` 开头 → 跳过
4. 查 DB 所有 `enabled=True` 的 KeywordReply（按 created_at ASC，最早添加优先）
5. 对每条规则做 `regex.lower() in text.lower()` 包含判断（不区分大小写），**第一个命中即 break**
6. 命中后构造回复 message：
   - `MessageSegment.reply(event.message_id)` 如果 `quote_reply=True`（OneBot v11 引用消息）
   - `MessageSegment.at(event.user_id)` 如果 `at_user=True` 且 `event` 为 `GroupMessageEvent`
   - 然后是 `reply` 文本
7. `await bot.send(event, message)` 发出
8. 日志：`[INFO] 自动回复触发：rule_id=<n> keyword=<repr(k)> user_id=<masked> group_id=<g> matched_text_len=<n>`

**为什么不用 `on_keyword(["xxx"])`**：NoneBot `on_keyword` 在 import time 静态注册关键词列表，**不支持运行时动态变更**。我们的关键词来自 DB（运行时可增删），必须用 `on_message` + handler 内查 DB 模式。

**缓存策略**：每次进 handler 都查 DB 是浪费。加 module-level 缓存：
- `_cache: tuple[float, list[KeywordReply]]` — `(load_timestamp, rules)`
- TTL = 30 秒。变更通过 WebUI 后无需重启 bot：30s 内自动生效。
- 或者更主动：WebUI CRUD endpoint 写 DB 后调 `auto_reply.invalidate_cache()` 主动失效，handler 下次进入时重新加载。**推荐**：用 30s TTL + 主动失效双保险。

**频率限制 / 防刷**：本期不加，避免过度设计；管理员可以通过关闭具体规则 / 删规则止血。

### R3 — WebUI 后端 `server/routes/webui_autoreply.py`

新增 router，注册到 `server/web_server.py`。endpoint：

| Method | Path | 功能 |
|---|---|---|
| `GET` | `/webui/api/autoreply` | 列出所有规则，返回 `[{id, keyword, reply, enabled, at_user, quote_reply, created_at}, ...]` 按 created_at ASC |
| `POST` | `/webui/api/autoreply` | 创建规则。Body: `{keyword, reply, enabled?, at_user?, quote_reply?}`。校验 keyword 1-50、reply 1-500。成功后调用 `auto_reply.invalidate_cache()` |
| `PUT` | `/webui/api/autoreply/{id}` | 更新规则。支持改任意字段。成功后调 invalidate_cache |
| `DELETE` | `/webui/api/autoreply/{id}` | 删除规则。成功后调 invalidate_cache |
| `POST` | `/webui/api/autoreply/{id}/toggle` | 切换 enabled（便捷端点，可选；如果前端用 PUT 也行，就不加） |

权限：与现有 `webui_*` 路由一致（依赖 `add_webui_auth_middleware`）。

错误：`api_error(status_code=…, code=…, message=…)` 风格与 `webui_users.py` 一致。

### R4 — WebUI 前端

#### R4.1 模板 `server/webui/templates/autoreply_content.html`

风格参照 `users_content.html`：
- 顶部 toolbar：搜索 + 刷新 + 新建按钮
- 主体表格：列 `关键词` / `回复内容（缩略）` / `艾特` / `引用` / `启用` / `操作（编辑 / 删除）`
- 创建 / 编辑 dialog：keyword 输入框 + reply 文本域 + 3 个 toggle（at_user / quote_reply / enabled）
- 表格行的 `艾特` / `引用` / `启用` 用 checkbox 直接 inline 切换（提升效率），或者纯展示由编辑按钮改 —— 让 implement 决定，**推荐 inline checkbox** 体验更好

#### R4.2 JS `server/webui/static/js/autoreply.js`

CRUD 标准模板：fetch list → render table → bind add / edit / delete handlers → toast 反馈。

复用 `apiRequest` / `showToast` 等 helper（参考 users.js 已有 helper）。

#### R4.3 CSS `server/webui/static/css/autoreply.css`

最小化，复用现有 `.users-layout` / `.users-toolbar` 等通用类；仅给本页特有元素（如 reply 缩略 cell 截断）加少量私有规则。

### R5 — 路由 + 页面渲染 + 导航

#### R5.1 渲染入口

参照 `users` 页面在 `server/web_server.py` 注册一个 `GET /webui/autoreply` 路由（或在 `server/routes/webui.py` 中），加载 `app_shell_base.html` shell + `autoreply_content.html` 内容。

需要替换的占位符：
- `__PAGE_TITLE__` → "自动回复 - NextBot WebUI"
- `__PAGE_CONTENT__` → autoreply_content.html
- 各个 `__NAV_*_ACTIVE__` / `__NAV_*_ARIA__` 占位符 → 自动回复 active，其他非 active

#### R5.2 侧边栏菜单项

在 `server/webui/templates/app_shell_base.html` 的 menu-list 中，**在 `设置` 之前** 插入新菜单项 `自动回复`：

- href: `/webui/autoreply`
- 占位符: `__NAV_AUTOREPLY_ACTIVE__` / `__NAV_AUTOREPLY_ARIA__`
- icon: 选个聊天 / 闪电 / 自动相关的 SVG（让 implement 选）

确保 console_page / 其他 shell 渲染处都新增对应占位符替换逻辑（看 `server/pages/console_page.py` 是否统一管理）。

### R6 — 不动其他模块

- 不动 bot.py 私聊门面（auto_reply 在门面之后）
- 不动现有命令 / 同步逻辑 / 经济系统 / 任何其它 plugin
- 不动 WebUI 鉴权 / settings / DB 已有表

## Acceptance Criteria

- [ ] 启动后，console 日志含 `[INFO] migration 完成：keyword_reply schema 已确认`（或同等表创建成功 INFO）。
- [ ] 用 WebUI 创建一条规则：keyword=`你好`，reply=`你好呀～`，三个 toggle 全开。
- [ ] 群里发 `你好啊` → 机器人回复 `[reply:msg_id] @用户 你好呀～`（含引用 + at + 文本）。
- [ ] 关掉 `at_user` 后，群里发 `你好啊` → 回复变成 `[reply:msg_id] 你好呀～`（无 @）。
- [ ] 关掉 `quote_reply` 后 → 回复无引用部分。
- [ ] 关掉 `enabled` → 不触发。
- [ ] 命令消息（如 `/签到`、`签到`、`/在线`）不会被自动回复处理（即使消息中含某关键词的子串，命令优先）。
- [ ] 私聊（owner）发关键词消息 → 触发自动回复，但**不带 @**（私聊场景 at 无意义）。
- [ ] 同一条消息命中多个关键词 → 只回第一条（按 created_at ASC，最早添加优先），不刷屏。
- [ ] WebUI 侧边栏新增 `自动回复` 菜单项，进入页面能正常渲染，CRUD 工作。
- [ ] 删除规则后 30s 内（或立即，通过 invalidate_cache）旧关键词不再触发。
- [ ] 失败回复符合 CLAUDE.md：动作 + 结果，原因。

## Definition of Done

- 通过 trellis-check（lint / typecheck / 合规性）。
- 不破坏 bot.py 私聊门面 / 现有命令 / 现有 WebUI 页面。
- WebUI 风格与 users / commands 一致（DESIGN.md 中适用 WebUI 的颜色 / 字体 token）。
- 日志符合 logging-guidelines（key=value INFO / WARN）。

## Out of Scope

- 不支持 regex 匹配（本期仅 contains）
- 不支持多媒体回复（图片、表情包）—— 仅纯文本
- 不支持按群 / 用户精细化作用域（全局生效，与白名单门面共用）
- 不做频率限制（按需后续加）
- 不做导入 / 导出 / 批量操作
- 不做 UNIQUE 约束（多规则同关键词共存，第一条命中）
- 不做版本历史 / 审计

## Technical Notes

- DB schema 注册：`nextbot/db.py:_run_migration` 链
- 启动 hook：参考 `_run_legacy_users_password_hash_migration` 模式
- NoneBot on_message：`from nonebot import on_message; on_message(priority=100, block=False)`
- OneBot v11 MessageSegment：`from nonebot.adapters.onebot.v11 import MessageSegment` → `.at(user_id)` / `.reply(message_id)`
- 缓存 invalidation：模块级 `_invalidate_cache()` 函数，CRUD endpoint 调用
- 文本提取：`event.get_message().extract_plain_text()`
- bot.send：`await bot.send(event, message)` 支持 MessageSegment 拼接（用 `+` 运算）
- 现有 WebUI router 模板：`server/routes/webui_users.py`（CRUD endpoint 模板最佳参考）
- shell 占位符替换：`server/web_server.py` 或 `server/pages/console_page.py`（需 grep 确认）

## Design Notes (DESIGN.md 适用部分)

不全照搬 DESIGN.md 的 warm canvas / Copernicus serif（那是命令截图模板风格）；WebUI 用现有 utility 风格保持统一。但参照：
- 颜色 token 命名风格（如果现有 WebUI 用了 `--color-*` 自定义属性）
- 字体配对（中英文混排留空格）
- 信息密度（不要 over-design）
