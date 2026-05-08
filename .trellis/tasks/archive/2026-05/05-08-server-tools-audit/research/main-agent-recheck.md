# 主代理二次复查结论

**日期**: 2026-05-08
**复查范围**: server-tools-findings.md (31 项) + server-manager-findings.md (13 项)

---

## 复查方法

主代理对每条 critical / high 级问题做了源码读取 + 行为验证。下面按问题做出"真实存在 / 误判 / 严重度调整 / 重复"的结论。

---

## ✅ 验证为真（可直接报给用户）

### 服务器工具

| ID | 复查结论 |
|---|---|
| **ST-2.1** 全亮地图 OOM | ✅ 真。`request_server_api` 用 `httpx.AsyncClient` + `response.json()`，整段 base64 PNG 必经全量内存。无 semaphore，并发触发可放大 N 倍。 |
| **ST-3.1** 路径遍历 | ✅ 真。已实测 `Path("/tmp") / "/etc/passwd"` 返回 `Path("/etc/passwd")`，`Path("/tmp") / "../etc/passwd"` 返回 `Path("/tmp/../etc/passwd")`（OS 写入解析）。fallback 分支落入这里，`fileName` 完全由后端控制 → root 部署可任意覆盖。 |
| **ST-3.2** /tmp 不清理 | ✅ 真。`server_tools.py:262-265` 仅 `write_bytes` 后 `bot.send`，无 unlink、无清理任务。 |
| **ST-3.3** 下载地图 OOM | ✅ 真。同 ST-2.1，且 .wld 通常更大。 |
| **ST-2.2 / ST-3.4** 60s 超时偏短 | ✅ 真。`request_server_api` 把 `timeout=60.0` 同时套到 connect/read/write/pool 四个超时上，无法分开。 |
| **ST-3.5** OneBot upload `name` 来自后端 | ✅ 真。`upload_group_file(name=file_name)` 中 `file_name` 为后端返回串，无清洗。 |
| **ST-4.1** /say 全角字符社工 | ✅ 真。`user.name` 经 `_validate_user_name` 限制为 `[A-Za-z0-9一-鿿]+`，不含 `（）：`；但 `content` 仅 whitespace-collapse，可塞 `（10001）：` 伪装他人发言。 |
| **ST-5.1** rawcmd helper 未复用 | ✅ 真。`shop.py:124 _issue_raw_command` 已存在但仅 shop 内部用；`server_tools.py:92 / server_send.py:84` 仍是裸 `try/except is_success`。 |

### 服务器管理

| ID | 复查结论 |
|---|---|
| **SM-1.1** count+1 ID 冲突 | ✅ 真。`server_manager.py:45` 用 `count() + 1`；但 `webui_servers.py:208` 用 `func.max(Server.id) + 1`。两边不一致，且任何 gap 会让 bot 端 INSERT 撞已有 id。 |
| **SM-1.2** 并发 add 竞态 | ✅ 真，机制同 SM-1.1。SQLite UNIQUE 拦下后 IntegrityError 未被 catch。 |
| **SM-1.3** bot 端无输入校验 | ✅ 真。webui 走 `_validate_server_payload`（regex + 端口范围）；bot 端零校验，会写入空 ip / 非数字端口 / 空 token 的脏行。 |
| **SM-2.1** 删除服务器静默改写 target_server_id | 🚫 **业务设计，不修**（用户 2026-05-08 决定）。renumber 是有意保持 ID 连续 / UI 编号稳定的设计取舍，不视为缺陷。 |
| **SM-2.2** 删除前不检查级联 | ✅ 真。无 preflight count，无 dependency 警告。 |
| **SM-2.3** renumber + 并发 add 写偏斜 | ✅ 真。bot 端 add 与 webui add 之间也存在。 |
| **SM-2.4** webui 同样 renumber bug | 🚫 **业务设计，不修**（同 SM-2.1）。 |
| **SM-3.1** add 无 except 早返回 | ✅ 真，但 finally 里的 close 会隐式 rollback；用户体验问题更大于数据问题。 |
| **SM-4.2** PRAGMA foreign_keys 未开启 | ✅ 真。即使日后给 target_server_id 加 ForeignKey，SQLite 不开 PRAGMA 也不会强制约束。 |

---

## 🔧 严重度调整

| ID | 子代理评级 | 主代理评级 | 理由 |
|---|---|---|---|
| ST-1.1 命令原文回显敏感参数 | 🟡 medium | 🟢 low | owner 自己输入自己看到，多人在群场景下需注意但不构成"漏洞"。建议归为运营约定。 |
| ST-1.3 command 不强制 `/` 前缀 | 🟢 low | ℹ️ info | TShock 的 rawcmd 不强制 / 前缀，bot 端校验属人为约束，价值有限。 |
| ST-1.4 5s 超时不足 | 🟢 low | 🟡 medium | 实际会触发用户重试 → 与 ST-2.2 / ST-3.4 同因，但严重度偏高于 low。 |
| ST-2.5 失败动词"查询"不一致 | 🟢 low | ℹ️ info | 文案问题。 |
| SM-1.5 name 含换行 | 🟢 low | ℹ️ info | parser.split() 已经拒绝；纯防御层冗余。 |
| SM-3.1 add 无 rollback | 🟠 high | 🟡 medium | finally close 已隐式 rollback，本质问题是 UX（无错误回复 + 无 logger），不是数据风险。 |
| SM-4.1 renumber 锁全表 | 🟡 medium | 🟢 low | server 行通常 <10，锁影响极小。 |

---

## ❌ 误判 / 不予采纳

| ID | 子代理评级 | 主代理结论 |
|---|---|---|
| ST-1.5 `int(user_id)` 兼容性 | ℹ️ info | 误判：项目目前仅 OBV11，user_id 一定是数字。可保留为"未来扩展时考虑"。 |
| ST-4.4 User+Server 同 session 多次查询 N+1 | 🟢 low | 误判：2 次小查询，不构成 N+1。 |
| ST-4.5 多余 f-string | 🟢 low | 风格问题，与漏洞 / 性能无关，建议合并到代码风格 lint。 |
| ST-5.6 异常路径统计未对齐 | 🟢 low | 误判：与本次审计目标无关。 |

---

## 🔁 去重

ST-2.1 / ST-3.3 / ST-5.2 同根因（base64 全量内存 + 无限流），可合并为一条；ST-2.2 / ST-3.4 / ST-1.4 / ST-4.3 同根因（timeout=5/60s 没分 connect/read），可合并；ST-2.6 / ST-5.4 同根因（at 拼接重复），可合并。SM-1.1 / SM-1.2 / SM-2.3 同根因（count+1 + 无事务），可合并。

---

## 主代理整体看法

1. **本次审计的"真正大问题"只有 4 条**：
   - **SM-2.1 + SM-2.4** 删除服务器静默改写 target_server_id（bot + webui 双坑）—— 这是**critical 中的 critical**，且与之前几次审计风格不同：不是并发竞态，是**设计上的根本错误**。
   - **ST-3.1** /tmp 路径遍历 RCE —— 实操危害大（root 部署直接覆盖任意文件）。
   - **ST-2.1 / ST-3.3** 大对象 base64 全量驻留内存 —— 多人触发可 OOM 整个 bot。
   - **SM-1.1 + SM-1.2** count+1 → ID 冲突 / 并发竞态 —— 部署到生产前必修。

2. **其余多数是体验 / 一致性 / 防御深度**问题，可以批量列入"建议改"。

3. **server_send 安全性出乎意料地好**：whitespace-collapse 已经堵了 /say 跨行注入；name 正则也堵了用户名注入。剩下的 ST-4.1 全角字符社工是文案约定问题，可以选择不修（在 `/say` 协议里没有完美方案，要看产品取舍）。

4. **跨命令的设计性建议**应当作为一个独立的重构任务处理：
   - 服务器 ID 改为不可变 + 软删除 / FK SET NULL
   - 抽 `_issue_raw_command` 为 tshock_api 公共 helper
   - 大文件下载抽 `fetch_large_payload` helper（流式 + per-server semaphore）
   - 抽 `_validate_server_payload` 为公共 helper（bot/webui 共用）
