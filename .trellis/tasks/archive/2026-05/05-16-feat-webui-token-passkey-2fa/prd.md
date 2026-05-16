# feat(webui): 多登入方式（Token / 密码 / Passkey）+ 可选 2FA

## Goal

WebUI 当前仅支持 token 登入（`.webui_auth.json` 存储 token + session_secret）。新增设置页让用户在 3 种登入方式中**选择一种**作为主登入方式，并可选叠加 2FA 二次验证。覆盖现代浏览器主流认证体验 + 保持兼容（Token 仍是 fallback）。

## What I already know

- 当前是**单管理员**模型（不是多用户）
- 现有 auth：`server/routes/webui.py` middleware → `_verify_session_cookie` 或 query `?token=` → 命中即放行
- 凭据持久化：`.webui_auth.json`（atomic write，含 `webui_token` + `session_secret`）
- 登录页：`/webui/login` → POST 提交 token → HMAC compare → 写 signed cookie
- 登录失败有 per-IP 速率限制（10K key cap + 滑窗）
- DB 模型里没有 admin-user 表（`User` 是 QQ 玩家用，不能复用）
- 现有 401 vs 302 拆分：API 路由返 401 JSON / HTML 路由 302 跳 `/webui/login`
- 设置页已存在 `/webui/settings`，可作为本特性的入口（新增"安全 / 登入"分组）

## 用户描述的 3 种登入方式

1. **Token 登入**（现有）：明文 token，`.webui_auth.json` 持久
2. **自定义固定密码登入**：用户设的密码，前端表单提交
3. **Passkey 登入**：浏览器 WebAuthn（FIDO2）；未创建时引导创建后才能选择

## 2FA

- 主登入方式验证通过后，可选第二步 2FA
- 通常 = TOTP（Google Authenticator / 1Password / Authy 通用）
- 未创建时引导创建（扫码 / secret），保存 secret 后开启

## Decisions（增量更新）

- **Q1 / Q2 / Q5（部分）已决**：主登入方式三选一（Token / 密码 / Passkey 互斥）；**Token 始终隐式保底**作为恢复通道（持久在 `.webui_auth.json`）。用户在登录页可走当前选定的主方式，Token 永远作为"忘记密码 / 丢 Passkey 时"的紧急登入路径（UI 上需要一个低调入口，比如"使用 Token 登录"折叠链接）。
- 因此切换主方式不需"销毁 Token"，只是 UI 默认不显示 token 输入。

## Open Questions（剩余先决项）

3. **凭据存储位置** —— 扩展 `.webui_auth.json` 单文件 / 新建 sidecar 文件 / 新增 DB 表
4. **Passkey 库** + rp_id / origin 配置约束
5. **2FA 失锁恢复** —— Token 已是主方式保底；2FA 一旦开启，主方式过了但 2FA secret 丢了怎么办（Token bypass 2FA / 一次性 backup codes / 必须保留可信设备）
6. **设置入口** —— 嵌 `/webui/settings` 现有页 / 独立新页 `/webui/security` / `settings` 加新区块

## Assumptions（待验证）

- 单管理员（不引入多用户表）
- 不引入 OAuth / SSO
- 2FA = TOTP（不含 SMS / 邮件，单设备模型简单）
- 浏览器要求 HTTPS / localhost（Passkey 协议硬要求）
- 现有 token 登入的 session 机制（HMAC cookie + jti）保留，作为所有方式登入成功后的统一会话载体
- Token / 密码 / passkey 三选一**互斥**（用户字面"选择一种"）；这是 Q1 待确认

## Requirements（evolving）

待 Q&A 收敛后填入。

## Acceptance Criteria（evolving）

- [ ] `/webui/settings` 新增"登入方式"区域，3 个互斥选项（或多选，看 Q1）
- [ ] 切换到密码：引导设置密码 → 立即生效
- [ ] 切换到 Passkey：未注册凭据时引导 WebAuthn 注册流 → 注册成功后才允许切换
- [ ] 2FA 开关：未配置时引导 TOTP 设置（QR 码 + 验证一次性码）
- [ ] 主登入验证通过 → 若开 2FA → 进二步验证 → 全部通过才发 session cookie
- [ ] 登入流程 `/webui/login` 自适应当前启用方式（token form / password form / passkey button / +2FA step）
- [ ] 失败 rate limit 仍生效
- [ ] 失锁恢复路径明确（Q5 决定）

## Definition of Done

- 后端单元测试覆盖：每个 method 登入成功 / 失败 / rate-limit / 2FA 拒绝
- 浏览器 manual test：本地 + HTTPS 反代各跑一遍 passkey 流
- 文档：README / WebUI 帮助 tooltip 写清三种方式 + 2FA 的使用门槛
- 迁移兼容：现有用户从 token-only 升级到本版本时**不被锁出**，token 仍可用直到主动切换
- Auth 中间件无新增大开销（passkey 验证只在登录时跑一次，session 是同一套 cookie）

## Out of Scope（explicit）

- 多用户 / 多角色 / RBAC（仍单管理员）
- OAuth / SSO / OpenID Connect
- SMS / 邮件 2FA
- WebAuthn 用户验证「无密码注册账号」流（不引入注册概念）
- 密码找回邮件链接（无邮件基础设施）

## Technical Notes

- Passkey 库：候选 [`py_webauthn`](https://github.com/duo-labs/py_webauthn)
- TOTP 库：`pyotp`（社区主流，简单 + 稳定）
- `.webui_auth.json` schema 可扩展（H-7 atomic write 已就位）
- HTTPS 是 Passkey 硬要求（localhost 例外）；部署文档需点明
- 当前 `_check_login_rate_limit` per-IP 滑窗保留并扩展覆盖密码 / passkey / 2FA 失败

## Open Questions（再列一遍，对齐顺序）

1. 多方式 vs 单方式（最先决定，影响 UI 模型）
2. Token 去留
3. 凭据存储位置
4. Passkey 库选型 + rp_id / origin 配置
5. 失锁恢复策略
