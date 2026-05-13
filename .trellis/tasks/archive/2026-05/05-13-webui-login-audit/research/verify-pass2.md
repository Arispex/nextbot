# WebUI 登入审计 — 主代理二次审核日志

## PRD 前置假设修正

原 PRD "一次性 code" 假设错误。实际架构（后端子代理已纠正）：

- **WebUI 登入**：长期 `webui_token`（启动持久化到 `.webui_auth.json`）→ POST `/webui/api/session` 用 token 换 HMAC 签名 cookie（7 天 TTL）
- **`/webui/api/login-requests`** 是**另一个无关功能**：让运维 push QQ 群消息让用户确认 **Terraria 游戏服务器**登入（用户在群里回复 `允许登入` / `拒绝登入`）—— **不是 WebUI 登入流程**
- Bot 侧 "申请登录" 命令**不存在**；只有 `允许登入` / `拒绝登入` 应答命令

## 关键 finding 验证

### Critical (2 项，全部 CONFIRMED)

| ID | 验证 |
|---|---|
| **B4** URL `?token=` query 通道泄漏主密码 | ✅ Read `webui.py:97-100`：`query_token = request.query_params.get("token", "").strip()` + `hmac.compare_digest(query_token, settings.webui_token)`。每次访问都把主密码暴露在 URL（access log / referer / 浏览器历史 / bookmark） |
| **C1** 无 brute-force rate limit | ✅ Read `webui.py:115-131` middleware 无限速逻辑；`webui.py:198-233` `webui_session_create` 端点无失败计数 / 无 IP-based 限速 / 无 captcha |

### High (4 项，全部 CONFIRMED)

| ID | 验证 |
|---|---|
| **A2** 7 天 cookie + stateless 无服务端注销 | ✅ Read `webui.py:30, 88, 236-242`：`_SESSION_TTL_SECONDS = 7*24*60*60`；DELETE 仅 `response.delete_cookie`，无服务端 session store。被盗 cookie 7 天有效，无法主动注销 |
| **B3** webui_token 永生不轮换 | ✅ Read `server_config.py:65-100` `_load_or_create_webui_auth`：首次启动随机生成并持久化到 `.webui_auth.json`，之后永不变化。任何持有 token 的人可无限刷 7 天 cookie |
| **B5/D1** 启动日志明文 token | ✅ Read `web_server.py:395`：`logger.warning(f"Web UI Token：{settings.webui_token}")` 每次启动都把主密码以 WARN 级别打 stdout。被任何日志聚合 / journald / Sentry breadcrumb 采集都泄漏 |
| **C7** `/webui/api/login-requests` 无 rate limit | ✅ Read `webui_login_requests.py:85-210`：端点在 webui 鉴权保护内（必须有 cookie），但运维一旦登入即可无限频率以任意 user.name spam QQ 群 `@用户` 消息，被劫持 session 后可骚扰 + 钓鱼 |

### Medium (5 项，全部 CONFIRMED)

| ID | 验证 |
|---|---|
| **A1** cookie `secure=False` 硬编码 | ✅ Read `webui.py:109`：`secure=False` 硬编码，未根据 scheme 条件开启。HTTPS 部署时仍以 HTTP 传输 cookie |
| **A3** `.webui_auth.json` 无文件权限保护 | ✅ Read `server_config.py:65-100`：`write_text` 不调 `os.chmod(0o600)`，跟随 umask 默认 0644，group/others 可读 |
| **A5** webui_token 无 rotation 机制 | ✅ 同 B3 根因，单独立项是 rotation 缺失（与 token 永生不同维度） |
| **Frontend A-4** 缺 CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy | ✅ `grep -rn "Content-Security-Policy\|X-Frame" server/` 全仓 0 命中。login 页可被 iframe 嵌入（clickjacking）+ XSS 二级防御网完全缺失 |
| **D3** 登入失败日志缺 client_ip / UA | ✅ Read `webui.py:218-224`：只 logger.warning reason=，无 `request.client.host` / `User-Agent`，无法接 fail2ban |

### Low / Info (各项已在子代理报告标注)

详见 `backend.md` / `frontend.md`。

## False positive / 主代理拒绝

| 子代理判 | 主代理 | 理由 |
|---|---|---|
| F1 "群内任何人发允许登入会替别人确认" | **False positive** | Read `nextbot/plugins/security.py:124, 171-216`：handler 用 `event.get_user_id()`（发言者自己），只能 confirm 自己的 pending login。设计正确 |
| E1/E4/E5/E6/E7 性能项全部 | **不视为 finding** | 子代理自己标 Info 都 OK；只是为完整性记录 |
| A6 multi-worker secret 一致性 | **不视为 finding** | 当前单 worker uvicorn，子代理已标 Info |

## 主代理终判

**0 Critical false positive，0 High false positive。** 子代理报告质量高，关键 finding 全部行号可验。

### 终判分级

| 级别 | 数量 | 必修理由 |
|---|---|---|
| **Critical** | 2 | B4 URL token 泄漏（每次访问都泄漏主密码）/ C1 无 brute-force 防御（组合泄漏路径后可在线爆破） |
| **High** | 4 | A2 cookie 无注销 / B3 token 永生 / B5 启动日志泄漏 / C7 push 端点无限速 |
| **Medium** | 5 | A1 secure flag / A3 文件权限 / A5 rotation / Frontend A-4 安全响应头 / D3 日志补 IP |
| **Low / Info** | ~15 | 见子代理报告细则 |

### 修复梯队建议

**第 1 梯队（Critical，必修）**
- **B4** 移除 `webui.py:97-100` 的 query token 通道（直接删除，~3 行）
- **C1** 加 brute-force 限速（IP-based + per-token 失败计数 + 增量延迟，~50 行）

**第 2 梯队（High，强烈建议）**
- **B5** `web_server.py:395` 移除明文 token 打印，改为"已生成至文件，请查看 `.webui_auth.json`"（~3 行）
- **A2** 加服务端 session store（list / map 存活 cookie token，支持 revocation），改 cookie payload 加 jti（~30 行）
- **B3** webui_token 改为：env / CLI 命令生成短期 token，长期 token 用 refresh 机制（~50 行）— 或简单做 token rotation API
- **C7** `/webui/api/login-requests` 加 per-user rate limit（同一 target_name 5 分钟最多 1 次，~10 行）

**第 3 梯队（Medium）**
- **A1** secure flag 由配置驱动（~3 行）
- **A3** `os.chmod(0o600)` + 父目录 0o700（~4 行）
- **Frontend A-4** middleware 注入 4 个安全响应头：CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy（~20 行）
- **D3** 日志补 `client_ip` / `user_agent` 字段（~8 行）

**第 4 梯队（Low / 不推荐修）**
- 其余 Low / Info 项（已确认无生产 trigger）
