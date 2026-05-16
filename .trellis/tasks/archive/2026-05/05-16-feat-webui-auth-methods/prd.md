# [ABANDONED] WebUI 三种登入方式 (token / password / passkey)

**Status**: Abandoned in planning phase（未启动实施，无代码改动）

## 原始想法

WebUI 增加可配置登入方式，三选一：
1. Token（现有）
2. 自定义固定密码（新增）—— 创建两次输入一致；修改要旧密码 + 两次新密码；KDF 不可逆存 `.webui_auth.json`
3. Passkey（WebAuthn）—— 没有则引导创建，可修改；数据存 `.webui_auth.json`

## 主代理在 brainstorm 阶段提出的不合理 / 待澄清点

### 红色（必须澄清）
1. **Passkey 不需要"加密存储"** —— WebAuthn 协议下服务器只存公钥 + credential ID + counter，本身就是公开数据。"高强度加密存放" 只适用于密码 hash
2. **三种方式的并存关系不明确** —— 任选其一 / 同时启用 OR / 多因素 AND 三种语义差别大，安全等级 = 最弱方法
3. **Passkey 强制 HTTPS + RP ID 一旦定下不能改** —— 切域名 / 切端口 = 所有已注册 Passkey 失效
4. **忘记密码 / Passkey 设备丢失的 recovery 路径** —— 没有邮箱重置，唯一 fallback 是手动改 `.webui_auth.json`，需 recovery code 机制

### 黄色（trade-off）
5. KDF 选型：`hashlib.scrypt`（stdlib）vs `argon2id`（C FFI 依赖）
6. 切换登入方式 / 改密码后是否 rotate session_secret 作废所有现有 session
7. 限速策略：密码必须严格限速、Passkey 不需要

### 绿色（小建议）
8. 密码强度策略（长度下限、字典）
9. `.webui_auth.json` schema 设计
10. 凭证操作审计日志（client_ip / UA）
11. Passkey 多设备管理 UI

## 用户决策路径

1. 用户表示对 Passkey 不熟 → 主代理写了 Passkey 完整介绍（公钥密码学、为什么不需要加密存储、HTTPS / RP ID 边界、与 Token / Password 对比）
2. 用户决定：**只做自定义密码**（"这个使用场景估计不太可能 HTTPS"）
3. 主代理收敛 scope 到 Token + Password 两种，列出 3 个剩余决策（并存关系 / recovery 路径 / KDF 选型）
4. 用户决定：**暂时不需要，放弃这个计划**

## 归档原因

放弃实施。当前 WebUI Token 一种登入方式已经够用。

## 未来如果重启

- 阅读本 PRD 即可恢复完整讨论上下文
- 主代理给出的 11 点 critique + Passkey 介绍 + 推荐默认值都还有效
- Passkey 部分可以独立从 scope 里删
- 如果后续启用 HTTPS 部署，可以再考虑加 Passkey
