# feat: 注册账号 自动创建 TShock 用户 + 旧用户迁移

## Goal

`注册账号` 命令在写 bot DB + push 白名单的同时，**也在所有 TShock server 上自动创建对应账号**。同时支持旧版本平滑升级：旧用户表无密码字段时自动 backfill + 为每个旧用户生成随机密码并同步到所有 server。

## What I already know

- 命令入口：`nextbot/plugins/user_manager.py:212` `handle_add_whitelist`（命令 `注册账号`）
- 当前流程：参数校验 → `User` 表 insert（含 `name`, `group="default"`）→ `_sync_whitelist_to_all_servers` (POST `/nextbot/whitelist/add/<name>`) → 回 `注册成功`
- 用户指定使用 **TShock 原生** REST API `GET /v2/users/create?user=<name>&group=default&password=<plaintext>` 创建账号
- `User` model（`nextbot/db.py:135-`）目前无 `password` / `password_hash` 字段
- TShock 用 **标准 BCrypt**（已确认：`$2a$07$...` 格式 cost 7）
- 推送结果**不展示给用户**，仅"注册成功"；详情打 console log
- 上一 task 已有的 `_sync_whitelist_to_all_servers` / broadcast / outcomes 模式可参考

## Assumptions（待确认）

- 旧用户迁移阶段每个用户的新随机密码必须告知用户（否则迁移后他们登入不了）
- "Group 永远 default" — 与 TShock server 的 default group 概念一致（不是 bot `User.group="default"` 这个字段）

## Open Questions（按重要度排）

### ~~Q1~~（已决定）：密码用 **OneBot 临时私聊** 发送（B）

用户在群里发 `注册账号 <name>` → 机器人立即用 **临时会话**（不需要加好友，凭借共享群即可）私聊发送：
- 用户名
- 明文密码
- 提示用户自行保存

群消息仅"注册成功"。临时会话隐私性高，群成员看不到密码。

**Consequences**：
- 用户必须保存密码（一次性传递，机器人不持久化明文）
- 机器人侧只存 `password_hash`（Q2 自然推到 B1）
- 离线 server 无法回放（无明文）→ Q3 必须是 P2（跳过）或 P3（阻塞）
- 改密码必须由用户提供新明文 → MVP 是否包含见 Q4
- 若用户事后私聊密码丢了 → 走改密码流程重置（视 Q4）

### Q2（Blocking）：bot 侧持久化哪种形式的密码？

- **B1. 只存 password_hash**：bot DB 安全，但**离线 server 无法回放**（API 要明文，bot 没有了）→ pending queue 不可行
- **B2. 存 AES 加密的明文**（master key 在 .env）：可回放 / 可改密码 / 可查询；DB 泄漏不影响（除非同时拿到 .env）
- **B3. 不存任何密码**：用户失去密码就无法找回；离线 server 无回放

Q1 和 Q2 强相关（C / 回放 需要 B2）。

### ~~Q3~~（已决定）：跳过 + 日志（**P2**）

- 与"注册命令"现有的 `_sync_whitelist_to_all_servers` 行为完全对齐：失败仅打 console log
- 用户看到的永远是"注册成功"（不展示哪个 server 失败）
- **不在本任务实现 pending 补偿** —— 用户明确说"后面会通过同步机制完美解决"（下一个 task）

**Out-of-Scope 推论**：
- 离线 server 的补偿 / 回放 / 重发 → 留给未来同步机制 task
- TShock 端 hash 直推 endpoint（C# 插件改造）→ 同上

### ~~Q4~~（默认 Out-of-Scope）：`改密码` 命令本任务不做

- 用户已表示"未来同步机制"会重新设计这块（届时可能配合 hash-direct-write 一起做）
- 本任务先把"自动注册 + 迁移"跑通即可

### ~~Q5~~（已决定）：迁移**静默**，依赖未来 `修改密码` 命令救济

- bot 启动检测 `password_hash` 为 NULL 的旧用户 → 生成随机密码 → push 所有 server → 写 hash → **不通知用户**
- 老用户自己进 server 发现要密码 → 在群里求助 / 自助 → 跑 `修改密码 <自定义>` 重置（未来 task）
- 新注册（Q1）仍**私聊推送密码**，老用户路径走"懒重置"模式

**Consequences**：
- 迁移阶段**零 QQ 风控风险**（无 N 条私聊批发）
- 老用户体验：直到他们真正去登入 server 才意识到要重置 → 个别用户可能有困惑窗口期
- "改密码" 命令成为本设计的**必备后续**（用户已明确会做下一个 task）

## Requirements

### Schema
- `User` model 增加 `password_hash: str | None`（nullable）
- DB schema migration：参考 `db.py` 的 `_run_migration` + `ensure_*_schema` 模式，加 `ensure_user_password_hash_schema()` 检查列是否存在，不存在则 `ALTER TABLE` 加上

### 随机密码生成
- 字符集：`[A-Za-z0-9]`（避免 URL query param 特殊字符问题）
- 长度：**16 字符**
- `secrets.choice(...)` 生成（密码学安全 RNG）
- BCrypt hash：cost=7（与 TShock 兼容），用 Python `bcrypt` 库

### `注册账号` 命令流程（修改 `handle_add_whitelist`）
1. 现有校验 + DB insert（不变）
2. 生成 16 位随机密码 + BCrypt hash
3. 将 `password_hash` 写入 `User` 行（同事务 commit）
4. **并行** broadcast：
   - `/nextbot/whitelist/add/<name>`（现有）
   - `/v2/users/create?user=<name>&group=default&password=<plaintext>`（新）
5. **临时私聊**（OneBot v11 临时会话 / `private_msg`）推送密码给用户
   - 内容：用户名、密码、提示自行保存
   - 群消息仅"注册成功"
6. 推送 / 创建 / 私聊任何步骤失败 → 仅 console log，不影响 reply

### 启动时迁移（一次性，幂等）—— **仅写 DB hash，不动 server**
- bot 启动时（NoneBot startup hook 或 main 启动序列里）执行 `_migrate_legacy_users_password_hash()`
- 查询 `password_hash IS NULL` 的所有 `User`
- 每个：生成随机密码 → BCrypt hash → 写 `user.password_hash`
- **不 push 任何 server**（不调 TShock create / 不调 whitelist）
- **不私聊用户**
- 详细 log：`旧用户密码迁移：user_id=<masked_qq> name={name} hash_set=true`
- 迁移失败（hash 写不进 DB）→ 跳过该用户，下次启动重试（仍 NULL）

**设计理由**：旧用户可能已在各 server 手动注册自己的 TShock 账号，机器人不该用随机密码去 overwrite。迁移仅把 bot DB schema 状态对齐（NULL → 有 hash 占位），实际密码协调由未来的 `修改密码` 命令完成（用户提供已知明文 / 自定义新密码 → bot 写 hash + push 同步到所有 server）。

### Console log 标准
- 所有 TShock create push 结果：per-server `server_id=N name=qianyi result=ok/failed reason=...`
- 私聊密码：log `临时私聊密码已发送：user_id=<masked_qq> name=<name> 临时会话=success/fail`（不 log 密码本身）
- 迁移：批量日志 `迁移完成：total=N success_hash=N success_push=N/N×servers`

## Edge Cases / Known Limitations

| 场景 | 行为 | 是否阻塞本任务 |
|---|---|---|
| 注册时 TShock server 上同名账号已存在（用户先前手动注册过）| `/v2/users/create` 返回失败 → 仅 log；用户登入用 bot 推送的随机密码会失败 → 用户走 `修改密码` 救济 | 否，MVP 接受 |
| 注册时 server 离线 | 同上（broadcast 失败）→ 仅 log | 否 |
| 临时会话发送失败（用户阻止 / 风控）| log 警告；用户登入失败 → `修改密码` 救济 | 否 |
| 迁移时 server 离线 | 部分 server push 成功 / 部分失败 → log；用户在失败 server 登入失败 → `修改密码` 救济 | 否 |
| 用户在 bot DB 有 hash，但 TShock 上 hash 不一致 | bot 视角"已注册"，用户登入失败 → `修改密码` 救济 | 否 |
| 重复注册（已有 user_id）| 现有逻辑 reply `你已经注册过了` | 否（已有） |
| 用户密码忘了 | 走 `修改密码` 命令（未来 task） | 否（依赖未来） |

→ **所有失败场景**都通过未来的 `修改密码` 命令兜底，本任务保持 best-effort + log 策略。

## Acceptance Criteria

- [ ] `User` model 增加 `password_hash: str | None`，DB schema migration 函数 `ensure_user_password_hash_schema` 接入 `_run_migration` 列表
- [ ] Python 加 `bcrypt` 依赖（如果 pyproject.toml 还没有）
- [ ] `_generate_random_password()` helper：16 位 `[A-Za-z0-9]`，`secrets.choice` 随机
- [ ] `_hash_password(plaintext)` helper：bcrypt cost=7，输出 `$2a$07$...` 60 字符
- [ ] `_create_tshock_user_on_all_servers(name, plaintext)` helper：broadcast `/v2/users/create` 模式同 `_sync_whitelist_to_all_servers`
- [ ] `_send_temp_private_password(bot, user_id, name, password)` helper：OneBot 临时私聊发送
- [ ] `handle_add_whitelist`（注册账号 handler）流程加 password 生成 + hash 写入 + TShock create + 临时私聊；reply 仍仅"注册成功"
- [ ] `_migrate_legacy_users_password_hash()` 启动时执行；NULL hash 用户自动 backfill **仅写 DB hash**（不调任何 server API，不私聊）
- [ ] 所有失败仅 console log，不阻塞主流程，不暴露给用户
- [ ] `python3 -m py_compile` 关键文件通过

## Out of Scope（explicit）

- WebUI 端创建用户的 TShock create push（先做命令端，WebUI 视情况追加）
- `修改密码` 命令（下一个 task）
- 离线 server 补偿 / pending queue / hash 直推（未来"同步机制"task）
- TShock 端 NextBotAdapter 插件改造（hash-direct-write endpoint）
- 用户跨 server hash 不一致的合并 / 检测
- BCrypt cost 升级（cost 7 保持与 TShock 兼容）
- 密码强度策略升级（>16 / 含特殊字符）

## Decision (ADR-lite)

**Context**：现状 `注册账号` 命令只 push 白名单到 server，**不**自动创建 TShock 账号 → 玩家进入 server 仍要手动注册 → 各 server 密码可能不一致；同时旧用户表没有密码字段。

**Decision**：
1. 加 `User.password_hash` 字段，存 BCrypt hash（与 TShock 同款，cost 7）
2. `注册账号` 流程：bot 端生成 16 位随机密码 → hash 写 bot DB → 调 TShock `/v2/users/create` + `/nextbot/whitelist/add` 双 broadcast → 临时私聊推送明文密码
3. 启动时迁移：NULL hash 旧用户自动 backfill + push（不通知）
4. 所有失败 best-effort + log，不影响用户视角的"注册成功"
5. 离线 server 补偿 / `修改密码` / hash 同步机制留给未来 task

**Consequences**：
- ✅ 新用户：注册一步到位，所有在线 server 密码一致，私聊收到密码
- ✅ 旧用户：bot 启动后 DB 一致；进 server 发现要登入时走未来 `修改密码` 自助
- ⚠️ 边界 case（TShock 同名账号已存在 / 私聊失败 / server 离线）都依赖 `修改密码` 救济，MVP 不解决
- ⚠️ Bot DB 泄漏只暴露 hash → 攻击者要暴力破解（cost 7 较弱，但与 TShock 一致）
- ✅ 不引入明文持久化，安全等级与 TShock 自身相当

## Out of Scope

- WebUI 端的用户创建是否也加 TShock create push（先做命令端，WebUI 看情况追加）
- TShock 账号删除（DELETE）/ 改密码命令（视 Q4 决策）
- 用户跨 server 已有不同密码场景的合并 / 覆盖策略
- 旧用户迁移时已存在于某些 server 的账号要不要覆盖

## Technical Notes

- TShock REST API `/v2/users/create` 接受 GET query params: `user`, `group`, `password`
- 已确认 TShock 用标准 BCrypt cost 7（hash 字符串 `$2a$07$...`）
- Python `bcrypt` 库与 TShock 100% 互操作
- 现有 broadcast helper（`broadcast` / `BroadcastOutcome` / `aggregate` from `nextbot.server_broadcast`）可复用
- 命令端 `_sync_whitelist_to_all_servers` 模式可参考（`user_manager.py:130-148`）
- `User` model 在 `nextbot/db.py:135-`，schema migration helper 在 `db.py` 的 `_run_migration` 函数（见 `ensure_command_config_schema` 为例）

## Definition of Done

- [ ] Lint / py_compile 通过
- [ ] 文档化迁移策略（升级时机器人启动会做什么）
- [ ] 安全考虑写入 PRD（明文密码处理 / DB 字段加密 / 离线 server 策略）
