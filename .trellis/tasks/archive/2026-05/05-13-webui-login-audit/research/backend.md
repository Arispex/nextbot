# Backend / Session / Cookie 桶审计

- **Query**：WebUI 登入相关后端 / Session / Cookie 安全审计
- **Scope**：`server/routes/webui.py`、`server/routes/webui_login_requests.py`、`server/server_config.py`、`server/web_server.py`、`nextbot/plugins/security.py`
- **Date**：2026-05-13

## 关键架构事实（先纠正前置假设）

阅读代码后发现：题面假设 "一次性 code" 不成立。实际的 WebUI 登入流程是：

1. **WebUI 自身的登入**：`POST /webui/api/session` 接收 **长期** webui_token（启动时由 `_load_or_create_webui_auth` 持久化到 `~/.../data/.webui_auth.json`），用 `hmac.compare_digest` 验证后下发 HMAC 签名 cookie。token 本身不是一次性，也不会过期。
2. **`/webui/api/login-requests` 是另一个无关功能**：让运维在 WebUI 上输入用户名，由后端调用 OneBot 向用户所在 QQ 群 `@用户` 发送"有新设备正在尝试登入服务器"消息；用户在群里回复 `允许登入` / `拒绝登入`（`nextbot/plugins/security.py:23-24`）来确认 / 拒绝**Terraria 游戏服务器的登入**（不是 WebUI 登入）。

因此审计分为两条主线：**WebUI Session（A/B/C/D/E）** 和 **Terraria 登入确认转发（F + 部分 C/D）**。题面 B 节"一次性 code 设计"对应到的实际机制是 **Terraria 服务器侧的 5 分钟 pending login**（`/nextbot/security/confirm-login/{user}` 由 TShock 插件实现，不在本仓库 Python 代码内），后端只负责消息转发。

---

## A. Session Cookie 安全

### A1. cookie 缺少 `Secure` 标志（Medium，公网部署时升级为 High）

- 位置：`server/routes/webui.py:103-112`
- 行为：`response.set_cookie(... secure=False ...)` 硬编码 `secure=False`，未根据 `request.url.scheme` / `X-Forwarded-Proto` / 配置项条件开启
- 影响：若部署在 HTTPS 反代后，cookie 仍以 HTTP 传输（HSTS 之前的首请求、子域降级请求、误配置 mixed scheme 时）；被网络中间人嗅探即可登入
- 修复前 / 后：当前任何 HTTP 中间链路都可读 cookie / 不读 → 修复后仅 HTTPS 通道下发 cookie
- 触发概率：取决于部署方式；默认 `web_server_host=127.0.0.1`（`server_config.py:106`）时低，绑外网 / 反代时高
- 注：`samesite="lax"`（`webui.py:108`）已正确避免 CSRF GET 攻击，但不防嗅探

### A2. 长 TTL（7 天）+ "stateless" cookie 即"无法在服务端注销"（High）

- 位置：`server/routes/webui.py:30` `_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60`；`webui.py:236-242` DELETE 仅 `response.delete_cookie`
- 行为：cookie payload 是 `issued_at.HMAC(issued_at, session_secret)` 的 base64（`webui.py:54-59`），没有 nonce、jti、版本号、user_id；服务端 **无任何 session store**，校验只看签名 + 是否在 7 天 TTL 内
- 影响：
  - DELETE `/webui/api/session` 只指示浏览器删 cookie，**已泄漏的 cookie 在 7 天内仍能登入**（攻击者保留副本即可）
  - 无法在 token 被盗时"踢下线"，唯一办法是改写 `.webui_auth.json` 的 `session_secret` 让所有 session 失效（同时所有合法用户也掉线）
  - 没有按用户区分，无法实现"踢这一个会话"或"列出活跃 session"
- 修复前 / 后：被盗 cookie 7 天有效 / 服务端可注销
- 触发概率：低（需先泄漏 cookie），影响：High
- 严重度：**High**

### A3. session_secret 持久化文件无权限保护（Medium）

- 位置：`server/server_config.py:65-100`，`_WEBUI_AUTH_FILE.write_text(...)`（第 98 行）
- 行为：`.webui_auth.json` 包含 `webui_token`（**主密码**）和 `session_secret`（伪造 cookie 用），写入时**不调用 `os.chmod(file, 0o600)`**，跟随 umask 默认 `0o644`（即 group / others 可读）
- 影响：同机器其他用户、备份 / docker bind-mount 镜像、日志收集器都能拿到主密码 + 签名密钥；拿到 `session_secret` 即可离线伪造任意 issued_at 的 cookie
- 修复前 / 后：文件权限 0644 / 0600 + 父目录 0700
- 触发概率：多租户机 / 共享 docker 卷 / 错误备份场景偏高
- 严重度：**Medium**（单机部署 Low；共享主机或 docker compose 把 data dir 暴露出来的 High）

### A4. cookie payload 缺少 user_id / role / version → 无审计基础（Low）

- 位置：`webui.py:54-59` `raw = f"{issued_at}.{signature}"`
- 行为：cookie 只能证明"某人在某时刻成功通过 token 校验"，无法区分是哪个用户、哪个角色，也没有版本号支持密钥/会话格式平滑迁移
- 影响：日志 `logger.info("创建登录会话成功")`（`webui.py:232`）没有 actor 维度，事后审计 / 异常检测困难
- 严重度：**Low**（设计层面）

### A5. 启动时 webui_token / session_secret **持久不变**（Medium，与 A2 联动）

- 位置：`server/server_config.py:65-100`
- 行为：首次启动随机生成并写入 `.webui_auth.json`，**之后永远不变**，除非手动删文件
- 影响：
  - `web_server.py:395` `logger.warning(f"Web UI Token：{settings.webui_token}")` **每次启动都把主密码以 WARN 级别打到 stdout / 日志聚合系统**，被日志投递到第三方（云 logs、Sentry breadcrumb、journald 转发）后泄漏面无限扩大
  - 没有定期 rotation 流程；secret 一旦泄漏只能重启服务并删文件，所有 cookie 同时失效，体验差
- 修复前 / 后：明文 token 永久挂在日志里，secret 永生 / token 启动时只打提示"已生成至文件"，并支持 rotation
- 严重度：**Medium**（日志泄漏 + 无 rotation 双重风险）

### A6. multi-worker / 重启场景下 session_secret 一致性（Info）

- 位置：`server_config.py:113`
- 行为：`_build_settings` 由 `_settings_lock` 串行化（`server_config.py:25, 127`）+ 单文件存储，单进程多线程 OK；当前 `web_server.py:397 uvicorn.run(app, ...)` 单 worker 启动，所以**当前部署形态下不存在 worker 间不一致**
- 风险：未来如果改 `uvicorn --workers N` 或 gunicorn 多 worker，每个 worker 都跑 `_load_or_create_webui_auth`，**因为有文件锁缺失**可能并发首次启动时生成不同 secret（first writer wins）。目前单 worker 不触发，标记为未来风险
- 严重度：**Info**

### A7. cookie path=`/`、未设置 Domain（合理，Info）

- 位置：`webui.py:110-111`
- 行为：`path="/"`，未设 domain；非 webui 路径（如 `/render/...`）也会带 cookie 但中间件不读它，无副作用
- 严重度：**Info**

---

## B. "一次性 Code" / Token 设计

### B0. 实际不存在"一次性 code"

如顶部架构说明：WebUI 主登入流走的是 **长期 webui_token**（不是 OTP）。Terraria 服务器侧的 5 分钟 pending login 在 TShock 插件内，本仓库无 Python 代码，无法审计。下面仅对 **webui_token 本身**审计。

### B1. webui_token 熵 + 长度（OK，Info）

- 位置：`server_config.py:82` `token = secrets.token_urlsafe(24)` → 24 字节 = 192 bit 熵，~32 字符 base64url
- 评价：熵足够、用 `secrets` 模块、无可预测性；OK

### B2. webui_token 验证 timing-safe（OK，Info）

- 位置：`webui.py:218` `hmac.compare_digest(provided_token, settings.webui_token)`
- 位置：`webui.py:99` query token 通道同样 `hmac.compare_digest`
- 评价：两条路径都走 timing-safe；OK

### B3. webui_token 无 TTL、无一次性消费（High，前置设计问题）

- 位置：`webui.py:198-233`
- 行为：webui_token 校验通过即下发 7 天 cookie；webui_token 本身永不失效 / 不消费 / 不限次
- 影响：
  - 任何持有 token 的人（截图分享、复制粘贴到第三方页面、误提交到 git）都可换无限多个 7 天 cookie
  - token 同时充当 cookie 校验 fallback（`webui.py:97-100`，`?token=xxx` 直接 query 通过），**这条 query token 通道甚至不下发 cookie**，每次请求都把主密码暴露在 URL 里（access log / 浏览器历史 / referer 都会留）
- 修复前 / 后：主密码可无限刷 cookie / token 有 TTL + 一次性换 cookie 后失效 / 至少把 query token 通道移除或加 deprecation
- 触发概率：team 共享 token 时高
- 严重度：**High**

### B4. URL 中明文 token 通道泄漏严重（Critical）

- 位置：`webui.py:97-100`
  ```python
  query_token = request.query_params.get("token", "").strip()
  return bool(query_token and hmac.compare_digest(query_token, settings.webui_token))
  ```
- 行为：任何 `/webui/...?token=<主密码>` 请求绕过 cookie，直接认证；不下发 cookie 意味着每次访问都暴露 token
- 影响：
  - 浏览器历史、bookmark、复制粘贴的 URL 都泄漏主密码
  - 反代 access log 通常默认记 query string → token 进日志
  - HTML referer header 在跳转到外站时会把 token 带走
  - 用户分享某页 URL 时直接把主密码送出
- 修复前 / 后：token 进 URL 任何环节都会落地 / 仅允许 POST `/webui/api/session` 一种方式换 cookie，移除 query token
- 触发概率：高（默认登入页就是带 `?next=` 的 URL，团队成员 bookmark 后会带 token）
- 严重度：**Critical**

### B5. webui_token 在启动日志中以明文输出（High）

- 位置：`server/web_server.py:395` `logger.warning(f"Web UI Token：{settings.webui_token}")`
- 行为：每次启动把主密码以 WARN 级别写入 stdout / nonebot logger
- 影响：日志被任何下游消费方采集（journald、docker logs、云日志、Sentry breadcrumb、运维群里截屏报错）都会泄漏
- 修复前 / 后：每次启动主密码进日志 / 仅在首次生成时提示"已生成 token 至 .webui_auth.json"
- 严重度：**High**

---

## C. 端点 + Rate Limit

### C1. 无任何 brute-force 防御（Critical）

- 位置：`webui.py:198-233`（POST `/webui/api/session`），无 middleware 限速，无 IP-based 计数，无失败延迟，无验证码
- 行为：攻击者可以无限速度尝试 token；24 字节 token 离线爆破不可行，但若 token 因前述 B4 / B5 泄漏前缀（如部分截屏、log 截断）后可在线补全；任何"自定义短 token"用户都会被秒爆
- 影响：组合 B5（token 进日志）+ C1（无限速）= 拿到日志片段后补全可能
- 修复前 / 后：无限速 / 每 IP 每分钟 N 次失败后熔断
- 严重度：**Critical**（与其他端点共用，影响面大）

### C2. 无 CSRF 保护（Medium，被 SameSite=Lax 部分缓解）

- 位置：`webui.py:198-242`（POST + DELETE）
- 行为：POST `/webui/api/session` 接受 JSON body 含 token；`SameSite=Lax` 让普通 form CSRF 不能带 cookie，但**这个端点本来就是"建立 cookie"，不依赖现有 cookie**，所以 CSRF 不是问题
- 但 **DELETE `/webui/api/session`** 同样无 CSRF 保护：攻击者通过恶意页面构造 `fetch('/webui/api/session', {method:'DELETE', credentials:'include'})`，被害人若已登入 webui，访问该恶意页即被踢下线
  - 注：`SameSite=Lax` 默认对**顶层导航**才送 cookie，对 fetch 类跨站请求默认不送 → 实际不可触发
  - 但若 cookie 后续改为 `SameSite=None` 或浏览器变更默认行为 → 触发
- 严重度：**Low → Medium**（取决于 cookie 策略）

### C3. 错误响应正确区分但仍可枚举（Low）

- 位置：`webui.py:209-224`
  - 空 token → 422 `validation_error` / "Token 不能为空"
  - 错 token → 401 `unauthorized` / "Token 错误"
- 评价：不区分"token 不存在" vs "token 已过期"是因为 token 不会过期，没有这层泄漏；但 401 vs 422 的边界让攻击者能区分"我是不是发空 body 了"。可接受
- 严重度：**Low**

### C4. middleware 白名单完整性（Info / Low）

- 位置：`webui.py:117-131`
  ```python
  is_webui_auth_free_path = (
      path.startswith("/webui/login")
      or path.startswith("/webui/api/session")
      or path.startswith("/webui/static/")
  )
  ```
- 检查：
  - `/webui/login` 前缀匹配 → `/webui/login-admin-secret` 也算白名单。**当前 router 没有匹配该前缀的页面**（只有 GET `/webui/login`），但是隐患
  - `/webui/api/session` 前缀 → `/webui/api/sessions-admin-list` 也算白名单。当前同样无此 endpoint
  - 顺序：先 `path.startswith("/webui")` 判断"是否要走 webui 鉴权" → 再判白名单。OK
  - **路径规范化漏洞**：FastAPI 默认不做 path normalization，`/webui/../webui/login/../servers` 由 starlette path 处理，一般会被 normalize；但 trailing slash `/webui` vs `/webui/` 处理 OK（`request.url.path` 是 raw 路径）
  - 大小写：`/Webui/login` → `path.startswith("/webui/login")` 返回 False → 走鉴权，OK
- 严重度：**Low**（建议改用精确匹配或正则）

### C5. DELETE `/webui/api/session` 不要求已登入（Low）

- 位置：`webui.py:236-242`
- 行为：任何匿名用户（甚至不带 cookie）请求 DELETE 都会 204 成功 + 触发 `logger.info("删除登录会话成功")`
- 影响：
  - 日志污染：陌生流量、爬虫、扫描器都会触发 INFO 日志
  - 不引起 cookie 实际删除（无 cookie 可删）但污染审计流
- 修复前 / 后：未鉴权也写"删除成功" / 检查到 cookie 存在再 delete + log
- 严重度：**Low**

### C6. POST `/webui/api/session` 成功响应 `Location: /webui/api/session`（Info）

- 位置：`webui.py:226-230`
  ```python
  response = api_success(
      status_code=201,
      data={"next": next_path},
      headers={"Location": "/webui/api/session"},
  )
  ```
- 评价：201 + Location 是 REST 规范，但 `Location` 指向**端点自己**而不是被创建资源 / 用户应去的地方，语义偏弱。`data.next` 才是前端真正用的跳转目标
- 严重度：**Info**

### C7. POST `/webui/api/login-requests` 缺鉴权 + 缺速率限制（High）

- 位置：`server/routes/webui_login_requests.py:85`
- 行为：该端点在 `/webui` 前缀下，理论上会被中间件保护（必须有合法 cookie 或 webui_token）；但：
  - **它确实在保护范围内**（路径 `/webui/api/login-requests` 不在白名单 → 走鉴权，OK）
  - 但端点本身可让任何已登入运维**无限频率地以任何用户名 spam QQ 群 `@用户`**，被恶意运维或被劫持的 session 用来骚扰用户
  - `notify_all=True` 时遍历所有 group_ids 全部发 → 一次 API 调用可在多个群刷消息
- 影响：
  - 骚扰：登入 token 泄漏后可对任意 user.name 刷登入确认（每次都 @user，5 分钟内可重复）
  - DoS QQ 群消息：onebot bot 被刷限频
  - 钓鱼：合法运维触发的"有新设备正在尝试登入服务器"可被攻击者借用，受害用户以为是真请求点了"允许登入"
- 修复前 / 后：无限制 / 每 user_id N 分钟内只允许 1 次申请 + 防重放
- 严重度：**High**

---

## D. 日志 / PII

### D1. webui_token 进 WARN 日志（High，与 B5 重复，此处看格式角度）

- 位置：`web_server.py:395` `logger.warning(f"Web UI Token：{settings.webui_token}")`
- 严重度：**High**

### D2. 日志格式不符合 CLAUDE.md "machine-search-first" 规范（Low）

- 位置：`webui.py:210, 219, 232, 241` 全部 `logger.info / logger.warning` 都是中文自然语句，**完全没有 key=value**：
  - `"创建登录会话失败：reason=Token 不能为空"` → 有 reason= 但没 actor / ip
  - `"创建登录会话成功"` → 单纯一句话
  - `"删除登录会话成功"` → 单纯一句话
- 对比 `webui_login_requests.py:106, 116, 134, 181` 已用 machine-search-first：`f"发送登入确认失败：name={name}，user_id={user_id}，reason=..."`，规范一致
- 影响：webui session 日志无法 grep `actor=xxx`、`ip=xxx`，事后追溯困难
- 修复前 / 后：缺 actor/ip 维度 / 加 `client_ip=`, `user_agent=` 字段
- 严重度：**Low**（合规性问题，不是安全漏洞）

### D3. 缺失登入失败的 IP / UA 记录（Medium）

- 位置：`webui.py:218-224`
- 行为：token 错误时只 `logger.warning("创建登录会话失败：reason=Token 错误")`，没有 `request.client.host` / `X-Forwarded-For` / `User-Agent`
- 影响：被 brute-force 时无法识别攻击源，无法接入 fail2ban / WAF 黑名单
- 严重度：**Medium**

### D4. cookie 值本身没进日志（Info）

- 位置：通搜 `cookie_value` / `provided_token` 的 logger 引用 → 仅在错误路径 `Token 错误` 也**未打印 token 值**，OK
- 评价：避免了把 token / cookie 写进日志，OK
- 严重度：**Info**（正面）

### D5. `webui_login_requests.py` 把 name + user_id 进日志（Low，必要）

- 位置：`webui_login_requests.py:106, 116, 134, 181`
- 评价：用户名、QQ user_id 都是低敏感识别符（用户主动公开），且日志是审计要求；OK
- 严重度：**Info**

---

## E. 性能

### E1. `_resolve_user_id_by_name` 每次开 / 关 session（Info）

- 位置：`webui_login_requests.py:29-35`
- 行为：每个请求一次 SQLAlchemy session.query + close()，**符合现状**（与 Round 7-9 已审基础设施一致）；非循环内调用、单次 query，无 N+1
- 评价：OK

### E2. `_find_user_group` 循环调用 `get_group_member_info` 串行（Medium）

- 位置：`webui_login_requests.py:38-58, 61-82`
- 行为：遍历所有 allowed_groups，**逐个 await** `get_group_member_info`；如果有 5 个群、每个 RPC 200ms，则 1s 阻塞
- 影响：登入确认请求响应时间随群数线性增长；用户感知延迟
- 修复前 / 后：串行 / `asyncio.gather`（参考 `nextbot/server_broadcast.py` 已有并行模式）
- 严重度：**Medium**

### E3. `_find_user_group` 一旦命中即返回，但仍然 `await` 一次 RPC 才能确定（Low）

- 位置：`webui_login_requests.py:38-58`
- 评价：与 E2 同根，找到第一个即返回属正常短路；OK 但仍受 E2 限制

### E4. POST `/webui/api/login-requests` 阻塞期间持有什么资源（Info）

- 位置：`webui_login_requests.py:104, 113, 124, 128/130, 160-172`
- 检查：
  - `_resolve_user_id_by_name` 内 session 在调用结束前已 close（`finally`），不会跨 await 持有 sqlite 锁；OK
  - 后续 `await bot.call_api` 不在任何 db transaction / session 内；OK
- 评价：不持有 sqlite BEGIN IMMEDIATE 锁；与 Round 7-9 的"async 内禁止持 sqlite 写锁"原则一致

### E5. `_get_settings_from_request` 反复读 app.state（Info）

- 位置：`webui.py:145-146`
- 评价：dict 访问 O(1)；OK

### E6. cookie 验证零数据库访问（Info）

- 位置：`webui.py:74-90`
- 评价：纯 HMAC + 时间比较；每请求中间件成本 ~微秒级；OK

### E7. middleware 调用顺序（Info）

- 位置：`web_server.py:365` `add_webui_auth_middleware(app, runtime_settings)` 在 `include_router` **之前**
- 评价：FastAPI middleware 顺序遵循"最后 add 最先执行"原则；仅注册一个 middleware，无冲突；OK

### E8. POST `/webui/api/login-requests` "全部发送都失败"才返回 502（Low）

- 位置：`webui_login_requests.py:185-190`
- 行为：只要有一个 group 发送成功，就返回 201（partial success），失败的 group 仅 message_id=null
- 评价：API 设计层面合理（multi-group fan-out 部分成功），但调用方需要主动检查 `results[].message_id is null` 才知道哪个失败；前端可能漏判
- 严重度：**Info / Low**

---

## F. Bot "申请登录" 命令端（实际是"允许登入" / "拒绝登入"）

题面说的"申请登录"在 bot 侧实际不存在 —— bot 没有"申请登录"命令。"申请"动作发生在 **WebUI 端**（运维 POST `/webui/api/login-requests`），bot 端只接收 `允许登入` / `拒绝登入` 应答。下面审计这两个应答命令。

### F1. `允许登入` / `拒绝登入` 由用户自己确认，缺乏受害人识别（Medium）

- 位置：`nextbot/plugins/security.py:171-216`
- 行为：handler 用 `event.get_user_id()` 取**当前发言用户**（`security.py:124`），把这个 user 作为 target_user 直接 broadcast `/nextbot/security/confirm-login/{user.name}` 到 Terraria 服务器
- 影响：
  - 设计正确点：只有用户自己能确认 / 拒绝自己的登入请求 ✓
  - 隐患：消息发送方是 WebUI POST 触发的群消息（`webui_login_requests.py:152-156` `@user_id` + "请回复..."），群里**任何人**理论上都能发"允许登入"，但 handler 把发言者当 target → 不是受害人误确认，而是发言者自己触发自己的 confirm（"我没在登录，但我发了允许登入"会触发我自己的 pending login confirm）。如果发言者**没有 pending login**，TShock 返回 `No pending login request`（`security.py:26` 已识别这种情况），无副作用。
  - 但攻击场景：A 在登入，bot 群里 `@A` 发了确认请求 → B 也在群里看到，发"允许登入" → handler 用 B 自己的 user_id 调 confirm-login/B.name → B 自己没在登录，所以无效。**OK，攻击不成立。**
  - 真正风险：A 看到"有新设备登入" → 误以为是自己 / 被钓鱼 → 发"允许登入" → A 自己被钓登入。这是社工层面，不是代码漏洞
- 严重度：**Low**（设计层面已有保护）

### F2. `允许登入` / `拒绝登入` 命令对发送频率无限制（Low）

- 位置：`security.py:181-192, 205-216`
- 行为：`require_permission("security.login.confirm")` 只检查权限，没有 per-user rate limit；同一用户连续打 100 次"允许登入"会触发 100 次 broadcast 到所有 Terraria 服务器
- 影响：放大攻击面：用户被劫持 QQ → 刷 confirm-login 给所有服务器；但因为只能 confirm 自己（path 是 `/nextbot/security/confirm-login/{self_user_name}`），无横向影响
- 严重度：**Low**

### F3. 命令通过群消息（**不是私聊**）应答（Medium / 设计）

- 位置：`webui_login_requests.py:160-166` `send_group_msg`
- 行为：登入确认请求**始终通过群消息**发送，群里所有成员都能看到 "@A 有新设备正在尝试登入服务器"
- 影响：
  - 群成员都知道 A 在某个 IP / 设备登入 Terraria → 信息泄漏（A 的活动状态、登入时间）
  - 群成员可发"拒绝登入" → 但 handler 用发送者自己的 user_id，不会影响 A（F1 已分析）
  - 真正问题：**A 不在群里活跃**时收不到 @ 提醒、收不到推送 → 登入确认时间窗口（5 分钟）容易超时
- 修复前 / 后：群消息 / 优先私聊 + 群消息兜底
- 触发概率：高（每次新设备登入都触发）
- 严重度：**Medium**（信息泄漏） / Low（仅是登入活动元数据）

### F4. 已有未消费 pending login 时再申请的行为（Info）

- 位置：`webui_login_requests.py:85-210`
- 行为：后端不检查"该用户是否已有 pending"，每次 POST 都直接发新消息 + 在 TShock 侧应该是覆盖（TShock 实现，本仓库无代码）
- 影响：5 分钟内可重复刷消息；与 C7 的 rate limit 缺失叠加
- 严重度：**Low / Info**（取决于 TShock 侧 dedupe 行为）

### F5. `_resolve_user_id_by_name` 大小写处理（Info）

- 位置：`webui_login_requests.py:29-35` `func.lower(User.name) == name.lower()` + `order_by(User.id.asc()).first()`
- 评价：大小写不敏感匹配 + 同名取最早注册的；OK 但有歧义（如果有两个同名用户，登入确认会发给第一个，可能误触发）
- 严重度：**Low**（业务层面）

### F6. 用户名 SQLi / 注入（Info）

- 位置：`webui_login_requests.py:32` 使用 SQLAlchemy ORM filter
- 评价：参数化查询，无 SQLi 风险；OK

### F7. `name` 字段未限制长度 / 字符（Low）

- 位置：`webui_login_requests.py:92-99` 只检查 strip 后非空
- 行为：name="A" * 100000 也接受，会进入 sql query 浪费 IO
- 影响：DoS 放大；攻击者可让 server 做大字符串 lower + sqlite scan
- 修复前 / 后：无长度限制 / `len(name) <= 64` 早 reject
- 严重度：**Low**

---

## 结论 / 修复优先级

### Critical（必修）

1. **B4 - 移除 URL query token 通道** (`webui.py:97-100`)：每次访问都暴露主密码到 access log / referer / 浏览器历史
2. **C1 - 加 brute-force 速率限制**：`POST /webui/api/session` 无任何限速，组合其他泄漏路径后可被利用

### High（重要）

3. **A2 - 短 TTL + 服务端可注销机制**：7 天 + 无 session store → 被盗 cookie 无法主动失效
4. **B3 - webui_token 应有 TTL + 一次性换 cookie 后失效**：主密码当前永生
5. **B5 / D1 - 启动日志中明文 token**：`web_server.py:395`，改为提示"已生成至文件，请查看"
6. **C7 - `/webui/api/login-requests` 加 per-user rate limit**：防骚扰 + 防钓鱼

### Medium

7. **A1 - cookie Secure 标志条件开启**（HTTPS / 配置项）
8. **A3 - `.webui_auth.json` 文件权限 0600**
9. **A5 - 支持 token rotation**（不让所有用户掉线）
10. **D3 - 登入失败日志补 client_ip / UA**（接入 fail2ban）
11. **E2 - `_find_user_group` 改 `asyncio.gather` 并行**
12. **F3 - 登入确认优先私聊 + 群消息兜底**

### Low / Info

13. **A4 - cookie payload 加 user_id / version**
14. **C2 - DELETE session 加 CSRF 防御 + 要求登入态**
15. **C4 - middleware 白名单改精确匹配**
16. **C5 - DELETE 端点未登入不写 INFO 日志**
17. **D2 - webui session 日志补 actor / ip / UA 字段**（machine-search-first 规范）
18. **F2 - 允许 / 拒绝登入 命令加 per-user rate limit**
19. **F7 - name 字段长度上限**

### 不存在的"问题"（避免误改）

- "session_secret 多 worker 一致性" → 当前单 worker uvicorn，A6 仅未来风险
- "code 校验持 sqlite 写锁" → 不持有，与 Round 7-9 一致
- "name 参数 SQLi" → 用 ORM，安全
- "cookie payload 校验 timing-safe" → `webui.py:84` 已用 `hmac.compare_digest`
