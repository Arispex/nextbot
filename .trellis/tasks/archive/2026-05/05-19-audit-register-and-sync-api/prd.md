# audit: 注册账号 + sync API 复审

## Goal

对最近 2 个 task 的产物做一次综合复审，排查是否有遗漏的安全漏洞 / 正确性 bug / 鲁棒性 gap，**排除刻意设计**（如 P1 占位 hash、admin token 复用、最终一致性策略等已记 ADR 的决策）。

## 范围

两个 commit 的全部新代码 / 修改：

1. **`1aa4bff`** — `feat(user_manager): 注册账号自动创建 TShock 账号 + 旧用户 hash 迁移`
   - `nextbot/db.py` `User.password_hash` 列 + `ensure_user_password_hash_schema`
   - `nextbot/plugins/user_manager.py` 整个文件（含 helpers + startup hook + handler 改造）
   - `pyproject.toml` + `uv.lock`（bcrypt 依赖）

2. **`d015756`** — `feat(webui_sync): GET /api/sync/snapshot 端点`
   - `server/routes/webui_sync.py`（新文件，~121 行）
   - `server/web_server.py`（路由注册）

## 审计维度

### 🔴 Security
- 密码 plaintext 生命周期 / log 泄漏
- BCrypt 用法 / cost / format 与 TShock 互操作
- URL injection（TShock API 调用）
- ETag side-channel（是否泄漏信息）
- Auth bypass / privilege escalation
- 日志中是否含明文 / token / hash

### 🟠 Correctness
- DB session lifecycle / detached instance
- Migration 幂等性 / fail-safe
- 并发 race（注册 vs 迁移 vs sync poll）
- ETag 稳定性 / 排序 / NULL 处理
- 304 / 200 响应正确性
- bcrypt round-trip
- Optional / nullable 字段处理

### 🟡 Robustness
- 异常路径完整（broadcast / startup / poll）
- bcrypt import fail-fast vs fallback
- 临时私聊失败处理
- 0 用户 edge case
- DB 错误处理

### 🟢 Consistency / Style
- log 格式
- 命名 / imports
- 与项目其他 handler 风格对齐

## 排除项（刻意设计 ADR，不审）

- 旧用户 backfill 写占位 hash（不写 NULL）—— P1 设计决策
- admin token 复用作 sync auth —— 已 acknowledged trade-off
- 离线 server 仅 log 不重试 —— 由 sync API 收敛
- 「修改密码」未做 —— 下个 task
- `_mask_user_id` 与 `webui_users._mask_qq` 重复 —— 已 flag OOS

## 处理方式

- 真实 bug → trellis-check 自修
- 含义需要决策的潜在问题 → 写入审计报告 `research/audit-findings.md`，让用户决定
- 无问题 → 输出 verdict CLEAN

## Acceptance Criteria

- [ ] 全部代码扫描完成
- [ ] 0 严重 / 0 高危 / 0 中危正确性 bug 未修
- [ ] 风格 / 一致性问题可保留（不强制修）
- [ ] 若有 issue → 输出 audit-findings.md + 自修；若无 → 输出 verdict
