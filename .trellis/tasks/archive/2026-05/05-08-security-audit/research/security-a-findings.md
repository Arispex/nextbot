# Research: Security Audit (Batch A — 允许登入 / 拒绝登入)

- **Query**: Audit `nextbot/plugins/security.py` (171 行) 的 2 条命令（允许登入 / 拒绝登入），覆盖并发、注入、外部 IO、DB-API 一致性、错误传播、审计、资源泄漏维度
- **Scope**: internal
- **Date**: 2026-05-08

## Summary Table

| ID | Severity | Area | Title |
|---|---|---|---|
| SA-COMMON.1 | 🔴 critical | authorization | `security.login.confirm` / `security.login.reject` 被列入 `DEFAULT_GUEST_PERMISSIONS`，访客直接拥有，与 PRD「按设计应仅 admin」相悖 |
| SA-1.1 | 🟠 high | concurrency / perf | 允许登入 串行 fan-out 所有服务器（PQA-1.1 / PQA-2.1 同型问题），N×timeout 累加 |
| SA-1.2 | 🟡 medium | error propagation | `_pick_failure_reason` 只挑一个非「No pending」错误，其余服务器原因被吞掉，用户看到的失败原因不能区分「超时」「宕机」「未审核」 |
| SA-1.3 | 🟡 medium | observability | `_log_results` 不记录 actor（操作人）—— 命令对自己生效，但谁触发的、何时触发的、广播了几台都缺记录，安全取证困难 |
| SA-1.4 | 🟡 medium | TOCTOU / DB-API 双广播 | 同一 user 在 5 分钟有效窗口内重复点 允许登入，两次广播间存在窗口；TShock 端如果 idempotent 则无害，但代码没有任何单飞 / dedup |
| SA-1.5 | 🟢 low | defense-in-depth | 沿用 `quote(user_name, safe="")`，符合 PQB 同期防御模式 —— 标记为 ok，同时记录依赖关系 |
| SA-1.6 | 🟢 low | resource | `_load_self_and_servers` session lifecycle 已用 `try/finally` 关闭，正常 |
| SA-1.7 | 🟢 low | error propagation | 部分成功（5 台中 3 台 OK）当作整体成功上报，但失败 2 台的错误日志只记录在 INFO，用户看不到，运维需要去日志里抓 |
| SA-2.1 | 🟠 high | concurrency / perf | 拒绝登入 同 SA-1.1（串行 fan-out） |
| SA-2.2 | 🟠 high | abuse / DoS | 拒绝登入 没有自我防滥用：同一用户对自己重复 拒绝登入 不会带来副作用，但若未来扩展为 `拒绝登入 <user>` 或 admin 模式，会直接演变成 deny-of-service vector |
| SA-2.3 | 🟡 medium | semantic | 拒绝登入 失败时复用 `_pick_failure_reason`，但行为语义与 允许登入 不对称：拒绝失败应该明确告诉用户「没有待审核请求」即「无事可拒绝」，目前文案 OK 但逻辑分支与 允许登入 完全相同，意味着未来如果 confirm/reject 语义分化，需要拆分 |
| SA-2.4 | 🟡 medium | observability | 同 SA-1.3 |
| SA-2.5 | 🟢 low | defense-in-depth | 同 SA-1.5 |
| SA-CC-1 | 🟠 high | cross-cutting | 与 player_query (PQA-1.1 / PQA-2.1)、ban.unban（解封用户的服务器同步循环也是串行，见 `ban.py:276-310`）共享串行 fan-out 反模式，安全管理这两条命令是「最后一处」未修；需要纳入同一批 fix |
| SA-CC-2 | 🟡 medium | cross-cutting | `_log_results` 与 ban.py / player_query 没有统一的「广播聚合日志」格式；建议抽公共 helper（与 `_broadcast_login_action` 一起放进新的 `nextbot/server_broadcast.py` 或 large_image 同级 util） |
| SA-CC-3 | ℹ️ info | cross-cutting | 文件 import 了 `at_prefix` 没用（用了 `OBV11MessageSegment.at(int(user_id))` 内联），与 player_query._safe_at_segment 模式不一致：`int(user_id)` 在异常 user_id 下会抛 `ValueError`，比 player_query 的 `_safe_at_segment` 弱 |

## Findings

---

### 0. Cross-cutting issue — 权限默认开放给 guest（最严重，先列）

#### SA-COMMON.1 🔴 Critical — `security.login.confirm` / `security.login.reject` 被 seed 为 guest 默认权限

- **File:line**: `nextbot/db.py:34-95`，特别是 line 77-78
- **Current code**:

```python
DEFAULT_GUEST_PERMISSIONS: frozenset[str] = frozenset({
    "about",
    "ban.list",
    ...
    "security.login.confirm",   # line 77
    "security.login.reject",    # line 78
    ...
})
```

- **Impact**:
  - PRD 中明确指出「这两个命令按设计应是 admin-only」(用户问题：「are these commands in DEFAULT_GUEST_PERMISSIONS? They MUST NOT be (admin-only by intent)」)。当前代码恰好相反 —— 任意访客在第一次 `ensure_default_groups()` seed 后直接拥有 `security.login.confirm` / `security.login.reject` 权限。
  - 实际行为：每位注册用户都可以广播 confirm-login / reject-login；handler 里 `user = session.query(User).filter(User.user_id == user_id).first()` 用的是**自己** 的 user_id，所以确实是「对自己生效」，并不是越权封禁他人。但「自己确认登入」本身也属于敏感操作 —— TShock 端 `/nextbot/security/confirm-login/{user}` 的设计意图通常是：管理员对待审核的玩家做最终授权。如果 TShock 端的「confirm-login」就是「直接进入服务器 / 移出待审核队列」，那么允许任何 guest 自己 confirm 自己 = 绕过审核流程；即使不绕过审核，也允许 guest 任意 spam confirm/reject 广播请求，对 N 台服务器造成放大攻击（每个 guest 调用 → N × HTTP request）。
  - 结合 SA-2.2：guest 可以无限制 spam `拒绝登入`，每次都触发 N 台服务器 fan-out，没有节流，构成 amplification DoS 向量。
- **Reproduction**:
  1. 用一个新 QQ 注册（默认 group=guest）
  2. 登入后立即在群里发 `允许登入` —— 命令通过权限校验，向所有服务器广播 `/nextbot/security/confirm-login/<my_name>`
  3. 重复 100 次 / 秒，bot 进程会向所有服务器 fan-out 100 × N 个 HTTP 请求
- **Recommended fix**:
  - **Option A（首选）**：从 `DEFAULT_GUEST_PERMISSIONS` 删除 `security.login.confirm` 和 `security.login.reject`，改归 `default` group 或更严格的 admin group。同时要为已有库（已经 seed 过 guest 的部署）写一个 migration / 启动 hook：扫 `Group(name="guest").permissions`，移除这两项，避免 deploy 后老库仍允许 guest 调用。
  - **Option B（兼容）**：保留默认开放，但在 handler 层加 `(user.is_banned 检查 + 速率限制 + 审核状态校验)`，让 guest 调用前必须自己已经在 pending 队列里。这种方式逻辑更复杂，不推荐。
  - 建议 A + 启动期 audit log（INFO 级）打印「检测到老库 guest 包含 security.login.* 已自动剔除」便于排障。
- **Cross-ref**: 该问题影响命令权限的「设计意图」与「实际行为」一致性，是本次审计的最高优先级。建议主代理在 fix 阶段前先与用户确认「TShock 端 confirm-login 到底做什么」，决定 fix 方向。

---

### 1. `允许登入` → `handle_confirm_login` (line 96-132)

#### SA-1.1 🟠 High — 串行 fan-out 所有服务器，N × timeout 累加

- **File:line**: `nextbot/plugins/security.py:39-62`（`_broadcast_login_action`）+ `:123-125`（调用点）
- **Current code**:

```python
async def _broadcast_login_action(
    servers: list[Server], user_name: str, path_template: str
) -> tuple[int, list[tuple[Server, bool, str]]]:
    path = path_template.format(user=quote(user_name, safe=""))
    results: list[tuple[Server, bool, str]] = []
    success_count = 0
    for server in servers:                           # ← 串行
        try:
            response = await request_server_api(server, path)   # ← 默认 timeout=5.0
        except TShockRequestError:
            results.append((server, False, "无法连接服务器"))
            continue
        ...
```

- **Impact**:
  - 与 PQA-1.1（在线）、PQA-2.1（自踢）完全同型：N 台服务器中 1 台 unreachable，handler 阻塞 ~10s（connect_timeout + read_timeout），N=5 就是 ~50s。
  - 用户体验：群里发 `允许登入` 后等十几秒到几十秒才收到任何回复，期间 bot 看起来「死了」。
  - 对照 PQA-1.1 已修复方案（`asyncio.gather`），本文件还停留在串行模式，是审计周期内的「漏网之鱼」。
  - `request_server_api` 没有传显式 `timeout=` 参数，默认 5s read（`tshock_api.py:53`），N×5s + N×5s connect 在多台离线服务器场景下是 worst-case。
- **Reproduction**:
  1. 配置 3 台服务器，1 台 IP 防火墙拦截 connect
  2. 发送 `允许登入`
  3. 观察响应时间 ~10s（connect timeout × 1）
- **Recommended fix**:
  - 将 `_broadcast_login_action` 的循环改成 `asyncio.gather`，对照 `player_query.handle_self_kick._kick_one`（line 317-329）的模式：

```python
async def _broadcast_login_action(
    servers: list[Server], user_name: str, path_template: str
) -> tuple[int, list[tuple[Server, bool, str]]]:
    path = path_template.format(user=quote(user_name, safe=""))

    async def _broadcast_one(server: Server) -> tuple[Server, bool, str]:
        try:
            response = await request_server_api(server, path)
        except TShockRequestError:
            return server, False, "无法连接服务器"
        if is_success(response):
            success_text = str(response.payload.get("response") or "").strip()
            return server, True, success_text
        error_text = str(response.payload.get("error") or "").strip()
        if not error_text:
            error_text = get_error_reason(response)
        return server, False, error_text

    results = list(
        await asyncio.gather(*(_broadcast_one(s) for s in servers), return_exceptions=False)
    )
    success_count = sum(1 for _, ok, _ in results if ok)
    return success_count, results
```

  - 同时建议显式传 `timeout=10.0` 或类似，避免 5s 读太紧。

#### SA-1.2 🟡 Medium — `_pick_failure_reason` 吞掉多服务器错误差异

- **File:line**: `nextbot/plugins/security.py:65-75`
- **Current code**:

```python
def _pick_failure_reason(
    action: str, results: list[tuple[Server, bool, str]]
) -> str:
    non_pending_reasons = [
        reason
        for _, ok, reason in results
        if not ok and _NO_PENDING_MARK not in reason
    ]
    if not non_pending_reasons:
        return reply_failure(action, "没有待处理的登入请求")
    return reply_failure(action, non_pending_reasons[0])    # ← 只取第一条
```

- **Impact**:
  - 5 台服务器，1 台 returning「No pending login request」，1 台超时（「无法连接服务器」），1 台 returning「审核已过期」，2 台返回 「未注册」—— 这种异质性失败在当前实现下被压成单一 `non_pending_reasons[0]` 一句话，用户看到的可能是任意一条原因，且无法区分 5 台服务器到底哪几台因为啥失败。
  - 失败定位需要去翻 INFO 日志（`_log_results` 第二个 `for` 循环），用户排障路径长。
  - 对比 ban.py 的 `handle_unban`（line 274-310）的做法：每台服务器独立列出一行 `{server.id}.{server.name}：✅/❌ ...` —— 用户体验远好于现在的「only one reason」。
- **Reproduction**:
  1. 3 台服务器，1 台返回 `error="No pending login request"`，1 台返回 `error="审核已过期"`，1 台 connect 失败
  2. `success_count=0`，handler 走 `_pick_failure_reason` 分支
  3. 用户只能看到「允许失败，审核已过期」或「允许失败，无法连接服务器」，不知道还有别的原因；也不知道是不是只有一台失败
- **Recommended fix**:
  - 参考 ban.py / unban 的逐行汇总模式：失败时 reply_block 列出每台服务器结果。
  - 或者保留单行 reply 但拼出多行 reason：「允许失败，3 台服务器：S1=审核已过期, S2=无法连接服务器, S3=无待审核请求」。
  - 至少应在用户回复中包含「N/M 台失败」的统计，让用户知道是部分失败还是全失败。

#### SA-1.3 🟡 Medium — 审计日志缺少 actor、target name 与广播 outcome 三件套

- **File:line**: `nextbot/plugins/security.py:78-93`
- **Current code**:

```python
def _log_results(
    action: str,
    user_id: str,
    user_name: str,
    success_count: int,
    results: list[tuple[Server, bool, str]],
) -> None:
    logger.info(
        f"{action}处理完成：user_id={user_id} name={user_name} "
        f"success={success_count} total={len(results)}"
    )
    for server, ok, reason in results:
        logger.info(
            f"{action}服务器结果：server_id={server.id} name={server.name} "
            f"ok={ok} reason={reason}"
        )
```

- **Impact**:
  - 当前命令是「自己对自己」，所以 `user_id`（操作者）== target，看似没问题。但日志格式上 actor 与 target 不分离，未来如果命令演化为 admin 对他人审核（极有可能），日志会变成「actor=被审核者」而非「actor=管理员」，安全取证完全错位。
  - 没有时间戳（依赖 logger 自带 timestamp，能接受），但**缺少 outcome 字段**：如「broadcast confirm-login user=X by=Y outcome=3/5_servers_ok」是基本审计要求。当前只在 INFO 级别且分两条记录（一条总结 + N 条逐服务器），grep 时需要按 user_id+timestamp 关联。
  - 拒绝登入 同样问题（SA-2.4）。
- **Reproduction**:
  1. 管理员手动通过 SQL 把别人的 `permissions` 加上 `security.login.confirm` —— 那个用户去群里发 `允许登入`
  2. 事故发生后回查日志，无法快速从单行日志判断「是自己 confirm 自己」还是「管理员 confirm 别人」
- **Recommended fix**:
  - 字段 schema：`{action} 审计：actor_user_id={qq} actor_name={name} target_user_id={qq} target_name={name} success={count}/{total} reasons=[...]`。
  - 即使现在 actor=target，也要显式标注两个字段，便于未来命令扩展。
  - 失败 reasons 拼成单行 list，便于聚合查询。

#### SA-1.4 🟡 Medium — 重复点 允许登入 在 5 分钟窗口内会重复广播，无 dedup

- **File:line**: `nextbot/plugins/security.py:106-132`
- **Current code**: handler 没有任何 dedup / rate-limit / single-flight 机制
- **Impact**:
  - `reply_success("允许", "可在 5 分钟内重新连接")` 暗示成功有 5 分钟的有效窗口。在这 5 分钟内：
    1. 用户重复点 `允许登入` → 每次都重新广播一遍 → N 台服务器都接收 confirm
    2. 用户被踢后重连，期间另一个管理员或自己再发 `允许登入` 又一次广播
    3. 用户先发 `拒绝登入` 再发 `允许登入`，TShock 状态机如果不严格，可能出现「先 reject 后 confirm」状态颠覆
  - 是否真的有问题取决于 TShock 端 `/nextbot/security/confirm-login` 是否幂等。代码完全依赖 TShock 端的状态机正确性，bot 端不做任何防御。审计角度看，这是「无防御写入」的反模式（同 W-7.x 模式）。
  - 没有 race，因为 bot 端 handler 顺序由 NoneBot 排队，但 fan-out 完成前的几秒内，用户可能多次触发命令 → 触发多次重叠广播。
- **Reproduction**:
  1. 自动化脚本快速发 5 次 `允许登入`
  2. 观察服务器收到 5 次相同的 confirm 请求
- **Recommended fix**:
  - 简单 throttle：维护 `dict[str, float]` 记录每个 user_id 的最后一次成功广播时间，5s 内拒绝重复
  - 或者：把 `success_count > 0` 路径改为「写一个 cooldown record」（DB / in-memory cache），下次同 user_id 来时如果在 cooldown 内直接返回 reply_success
  - 优先级低：依赖 TShock 端是否幂等。建议主代理先确认 TShock 端的 confirm-login 行为再决定是否修

#### SA-1.5 🟢 Low — `quote(user_name, safe="")` 路径段编码已经做了，与 `request_server_api` 的 `quote(safe="/")` 形成 defense-in-depth

- **File:line**: `nextbot/plugins/security.py:42`
- **Current code**:

```python
path = path_template.format(user=quote(user_name, safe=""))
```

- **Impact**: 与 PQB-X.2（player_query 的 `quote(safe="")`）一致 —— `user_name` 段做严格 URL 编码；`request_server_api:58` 又做了 `quote(safe="/")` 兜底。即使 user_name 含 `/`、空格、`%`，都不会逃出当前 path segment。**这是正向项**，与 PQA / PQB / 历次 audit fix 一致。
- **Note**: 这条不需要修，记录为「与现有 defense-in-depth 模式一致」。

#### SA-1.6 🟢 Low — DB session 生命周期正确

- **File:line**: `nextbot/plugins/security.py:29-36`
- **Current code**:

```python
def _load_self_and_servers(user_id: str) -> tuple[User | None, list[Server]]:
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        servers = session.query(Server).order_by(Server.id.asc()).all()
        return user, servers
    finally:
        session.close()
```

- **Impact**: `try/finally` 闭合，模式与 ban.py / player_query.py 一致。无 leak。
- **Note**: 正向项，无需修改。

#### SA-1.7 🟢 Low — 部分成功视作成功，失败明细只在 INFO 日志，不进消息

- **File:line**: `nextbot/plugins/security.py:128-130`
- **Current code**:

```python
if success_count > 0:
    await bot.send(event, at + " " + reply_success("允许", "可在 5 分钟内重新连接"))
    return
```

- **Impact**:
  - 5 台服务器中 1 台成功就当成功，反馈用户「成功」。但 4 台失败的原因只在 INFO 日志里，用户和管理员都看不到。
  - 与 W-7 模式（warehouse 部分成功未告警）有相似性：业务上 confirm-login 部分成功是合理的（用户在 1 台上能进就够了），但「至少应该在用户回复里说明哪几台 OK / 哪几台 NOT」。
  - 如果 success_count == len(servers) 时只发「成功」，部分成功时（success_count < len(servers)）应该带明细 —— 这是 PRD「partial failure 是否 log CRITICAL」的对应。
- **Reproduction**: 5 台服务器，1 台返回 OK，4 台 connect 失败 → 用户看到「允许成功」，根本不知道有 4 台失败
- **Recommended fix**:
  - 完全成功路径不变
  - 部分成功路径补充 reply_block，列出失败服务器明细
  - 或至少 logger.warning（而非 INFO）记录 partial failure，便于运维 grep

---

### 2. `拒绝登入` → `handle_reject_login` (line 135-171)

#### SA-2.1 🟠 High — 同 SA-1.1（串行 fan-out）

- **File:line**: `nextbot/plugins/security.py:39-62`（共享 helper）+ `:162-164`（调用点）
- **Impact / Reproduction / Fix**: 同 SA-1.1，因为共用 `_broadcast_login_action`。修复 SA-1.1 即同步修复此条。

#### SA-2.2 🟠 High — 拒绝登入 缺乏抗 abuse 防护，guest 默认拥有权限叠加 amplification DoS

- **File:line**: `nextbot/plugins/security.py:135-171`
- **Current code**:

```python
@reject_login_matcher.handle()
@command_control(
    command_key="security.login.reject",
    permission="security.login.reject",
    ...
)
@require_permission("security.login.reject")
async def handle_reject_login(...):
    ...
    user_id = event.get_user_id()
    ...
    user, servers = _load_self_and_servers(user_id)
    ...
    success_count, results = await _broadcast_login_action(
        servers, user.name, "/nextbot/security/reject-login/{user}"
    )
```

- **Impact**:
  - PRD 直接问到：「For 拒绝登入 specifically — anti-abuse: can an attacker spam 拒绝登入 to deny legitimate users entry?」
  - 当前命令是「对自己生效」（用 `event.get_user_id()` 查自己的 User row），所以 spam reject-login 只会拒绝自己，不会拒绝别人。乍看安全。
  - **但**：结合 SA-COMMON.1（guest 默认拥有此权限）+ SA-2.1（串行 fan-out 无 throttle），任何注册 guest 可以 1Hz 速度发 `拒绝登入`，每次触发 N 台服务器 fan-out + 串行。短时间内可以制造 bot 进程长时间占用（每次响应 ~N×5s），形成 self-inflicted DoS。
  - **未来风险更大**：如果命令演化为 `拒绝登入 <某用户>`（admin 拒绝他人），此时缺乏速率限制就会变成「攻击者拒绝合法用户登入」的真实攻击向量。当前模板里没有任何「不能拒绝其他人」的护栏，扩展时极易出 bug。
- **Reproduction**:
  - 当前形式：用 guest 账号每秒发 `拒绝登入`，bot 进程堆积请求，其他命令处理被拖慢
  - 假设性扩展：`拒绝登入 victim_user` → 攻击者每秒拒绝目标 → 目标永远进不了服务器
- **Recommended fix**:
  - 短期：加 per-user rate limit（5 秒内只能发一次 confirm/reject），命中限速时回 `❌ 操作过于频繁`
  - 中期：把 guest 默认权限删除（SA-COMMON.1），改为 default 或 admin 组
  - 长期：如果未来扩展为 admin 模式，必须配套加 audit log + permission split（`security.login.reject.self` vs `security.login.reject.user`）

#### SA-2.3 🟡 Medium — confirm/reject 失败语义共用 `_pick_failure_reason`，未来分化时易遗漏

- **File:line**: `nextbot/plugins/security.py:65-75` + `:171`
- **Current code**: confirm 和 reject 都调用同一个 `_pick_failure_reason`，把「No pending login request」当作「没有待处理的登入请求」
- **Impact**:
  - 对 `允许登入` 来说，「没有待处理的请求」语义上是「无事可允许」 —— 合理
  - 对 `拒绝登入` 来说，「没有待处理的请求」语义上是「无事可拒绝」 —— 也合理，但用户视角不同：confirm 失败是「我想进但没机会」，reject 失败是「我想阻止登入但没人在等」，文案上现在等价是 OK 的，但**未来语义分化时易出 bug**（例如 reject 失败可能要返回更明确的「No threat」状态）
  - 当前不影响功能，但属于「helper 共用导致未来语义耦合」的代码味道。
- **Reproduction**: N/A（语义层）
- **Recommended fix**:
  - 选项 A：保持现状，等需要分化时再拆
  - 选项 B：把 `_pick_failure_reason(action, ...)` 的 action 参数实际利用起来，对 confirm 和 reject 分别返回不同的 default reason
  - 优先级低，记录为「未来扩展易踩坑」

#### SA-2.4 🟡 Medium — 同 SA-1.3（审计日志字段缺失）

- **Impact / Recommended fix**: 同 SA-1.3，统一改 `_log_results` 字段 schema 即可

#### SA-2.5 🟢 Low — 同 SA-1.5（路径段编码 OK）

- **Impact**: 共用 `_broadcast_login_action`，路径编码已经做了

---

### 3. Cross-cutting findings（与既有审计的对照）

#### SA-CC-1 🟠 High — 串行 fan-out 是「最后一处」未修

- **File:line**: `nextbot/plugins/security.py:39-62`
- **历史对照**:
  - PQA-1.1（在线）：已修复为 `asyncio.gather`（player_query.py:260）
  - PQA-2.1（自踢）：已修复为 `asyncio.gather`（player_query.py:332）
  - 解封用户（ban.py:276-310）：**仍然是串行**！这条命令对每台服务器先 GET `/nextbot/blacklist`、再 GET `/nextbot/blacklist/remove/{user_name}` 都串行 —— 同型未修
  - 封禁用户：通过 `ban_core.sync_user_to_blacklist`（未读到）实现，待 batch B 审；初步看 ban.py:97 调用此 helper，可能也是串行
  - 允许登入 / 拒绝登入：当前主审对象，串行
- **Conclusion**: 安全管理 + 解封 这两个命令是同型反模式的最后一处。建议主代理在 fix 阶段把 security + ban.unban 一起改，不要分批
- **Recommended fix**: 抽公共 broadcast helper（建议放在 `nextbot/server_broadcast.py` 新文件），所有 fan-out 命令复用，避免下次又出现串行漏网

#### SA-CC-2 🟡 Medium — `_log_results` 与既有日志格式不统一

- **File:line**: `security.py:78-93` vs `ban.py:88-90` / `player_query.py:270`
- **Impact**:
  - security 的 `_log_results` 输出两条 INFO（汇总 + 逐服务器）
  - ban.py 输出一条 INFO `用户封禁成功：user_id=... name=... reason=...`
  - player_query.handle_self_kick 输出一条 INFO `自踢执行完成：user_id=... name=... server_count=...`
  - 三种格式互相不一致；运维要写不同的 grep 模式才能聚合
- **Recommended fix**:
  - 抽公共「广播聚合日志」helper：`log_broadcast_action(action, actor, target, success_count, total, failures_per_server)`
  - 所有 fan-out 命令统一调用

#### SA-CC-3 ℹ️ Info — `at` 段构造未走 `_safe_at_segment`，与 player_query 不一致

- **File:line**: `security.py:114, 153` vs `player_query.py:182-186`
- **Current code**:

```python
# security.py
at = OBV11MessageSegment.at(int(user_id))   # 直接 int()，user_id 非数字会 ValueError
```

- **Impact**:
  - `event.get_user_id()` 在 OBV11 下永远是数字，所以 `int(user_id)` 在生产环境不会抛
  - 但 player_query 已经统一加了 `_safe_at_segment`（line 182-186）做 try/except 保护，security.py 没有跟进
  - 不一致让维护成本上升；如果未来支持其他适配器（V12 / shim），会从这里炸
- **Recommended fix**:
  - 把 `_safe_at_segment` 提到公共 utils（`nextbot/text_utils.py` 已有 `at_prefix` —— 实际可以直接用 `at_prefix(event, content)`，security.py 已经 `from text_utils import reply_failure, reply_success` 但没用 `at_prefix`）
  - 改为 `at_prefix(event, content)`，下沉容错到 utils 层
- **Note**: 用户工程规范要求「中英文之间空格」；当前代码 `at + " " + reply_failure(...)` 已经手动加了空格，符合规范，但混用 OBV11 段直接拼接和 text_utils.at_prefix 会让代码风格不一致

---

## 推荐 Fix 优先级（建议主代理参考）

1. 🔴 **SA-COMMON.1**（权限默认开放给 guest）—— 最严重，先确认 TShock 端语义后立即修
2. 🟠 **SA-1.1 + SA-2.1**（串行 fan-out）—— 与 SA-CC-1 一起修，包含 ban.unban
3. 🟠 **SA-2.2**（abuse / DoS）—— 加 rate limit，与 1 配合
4. 🟡 **SA-1.2**（错误聚合）+ SA-1.7（部分成功明细）—— 用户体验改进
5. 🟡 **SA-1.3 / SA-2.4**（审计日志）—— 与 SA-CC-2 一起统一格式
6. 🟡 **SA-1.4**（去重）—— 取决于 TShock 端 idempotent 行为，确认后再决定
7. 🟢 **SA-CC-3**（at_prefix 一致性）—— 顺手改

## 待主代理 recheck 的关键点

1. **TShock 端 `/nextbot/security/confirm-login` 实际行为** —— 决定 SA-COMMON.1 / SA-1.4 修复方向
2. **解封用户串行循环（ban.py:276-310）是否纳入本批 fix** —— 决定 SA-CC-1 范围
3. **`ban_core.sync_user_to_blacklist`（封禁用户调用，line 97）的并发模式** —— batch B 主要审查对象
4. **PRD 中提到「DB write before broadcast」—— 当前 confirm/reject 没有任何 DB 写入**，所以「部分成功后 DB 状态不一致」窗口本身不存在 —— 但要主代理确认 TShock 端是否会反向通知 bot 端写入 DB（如 webhook）；如果有，当前代码完全没有处理 callback
