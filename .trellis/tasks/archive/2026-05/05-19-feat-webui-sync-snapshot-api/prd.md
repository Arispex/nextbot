# WebUI 新增 sync snapshot API（拉模式同步）

## Goal

新增一个 WebUI API endpoint，让 C# 端（NextBotAdapter 插件）周期性 poll 来同步**白名单、黑名单、账号密码 hash**三类数据到本地 TShock。把现有的"机器人主动 push 各 server" 模式补充为"server 主动 pull bot" 模式 —— 解决离线 server 上线后无补偿、新 server 接入需 bot 知情、push 失败无重试等问题。

## What I already know

- bot 端已有：
  - `User` 表含 `name` / `is_banned` / `password_hash`（nullable）
  - `password_hash` 已 backfill 旧用户（占位 hash）+ 新注册时正确 hash
  - WebUI 认证中间件（`add_webui_auth_middleware`）对所有 `/webui/api/*` endpoint 生效
- 之前架构讨论决定走"拉模式 + ETag"（无状态同步，server 周期 poll）
- 现有数据 push 路径仍保留（白名单 / 注册），同步 API 只是补丁层让弱一致最终收敛

## Decisions（ADR-lite）

| 设计点 | 选择 | 备注 |
|---|---|---|
| **版本号方案** | **ETag = sha256 of canonical state** | 无状态、无 trigger、无需 bump 业务路径；C# 用 `If-None-Match` 头 |
| **Schema 形态** | **合并 `users` 数组**（不分 whitelist/blacklist/accounts） | DRY；C# 端按 `banned` 字段 partition |
| **NULL hash 用户行为** | **P1：直接输出占位 hash** | C# 直接同步 → 用户用占位 hash 进不去 → 必须走「修改密码」命令重置（依赖未来 task） |
| **认证** | **复用 WebUI admin token**（`.webui_auth.json` 里的 token） | C# 配置 1 个 token；本 endpoint 走现有 webui auth 中间件 |

**TRADEOFF acknowledged**：admin token 给所有 server 用 → 某 server compromised = admin token 泄漏 = WebUI 后台暴露。MVP 接受；未来可加 read-only sync token 区分。

## Requirements

### 新增 endpoint：`GET /webui/api/sync/snapshot`

**Response（200 OK）**：
```json
{
  "version": "ab1c2d3e4f...",     // sha256 hex of canonical state
  "generated_at": "2026-05-19T...",
  "users": [
    {
      "name": "user1",
      "banned": false,
      "password_hash": "$2a$07$..."
    },
    {
      "name": "banned1",
      "banned": true,
      "password_hash": "$2a$07$..."
    }
  ]
}
```

**Response（304 Not Modified）**：当请求 `If-None-Match: <last_version>` 与当前 ETag 一致 → 返回 304 空 body

**Response Headers**：
- `ETag: "<version>"`（标准 HTTP ETag 格式，带双引号）
- `Cache-Control: no-cache`（必须每次校验）

### ETag 计算

```python
def _compute_snapshot_etag(users: list[dict]) -> str:
    # 排序后稳定序列化（sort by name 避免 dict 序列化波动）
    canonical = json.dumps(
        sorted([
            {"name": u["name"], "banned": u["banned"], "password_hash": u["password_hash"] or ""}
            for u in users
        ], key=lambda x: x["name"]),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

仅基于 sync-relevant 字段（name / banned / password_hash），其他变化（coins / sign_streak / rob_*）不影响 ETag。

### 认证

走现有 `add_webui_auth_middleware`，与其他 `/webui/api/*` endpoint 同款（cookie 或 Authorization Bearer）。无新代码路径，复用即可。

### 性能 / 编码

- SQL：`SELECT name, is_banned, password_hash FROM user`（轻量，无 join）
- ETag 计算：< 1ms / 1000 用户
- JSON 序列化：~200KB / 1000 用户（可接受）

### 旧用户 hash backfill 调整

**当前**：bot 启动时给 NULL hash 旧用户写占位 hash。
**保持不变**（与 P1 一致）：旧用户输出占位 hash，C# 同步后用户必须走「修改密码」重置。

## Out of Scope（explicit）

- 「修改密码」命令（下一个 task；P1 决策的必要救济）
- 增量 sync（`?since=<version>`）—— 现阶段 full state 够用
- per-server 拉日志 / 同步状态仪表盘
- read-only sync token 区分 admin token（未来安全加固）
- soft-delete 标记（删用户 C# 怎么知道？现阶段 C# 端用 full-state diff 自行判断）
- C# 端的同步逻辑（这是插件作者的事，本 task 仅提供 API）
- WebUI 前端展示同步状态

## Acceptance Criteria

- [ ] 新增 `server/routes/webui_sync.py` （或加到现有 webui_*.py），定义 `GET /webui/api/sync/snapshot`
- [ ] 路由注册到 `server/routes/webui.py` 的 `_WEBUI_ROUTERS`
- [ ] 返回 JSON shape 与 PRD 一致（version / generated_at / users[]）
- [ ] `If-None-Match` 匹配 → 304
- [ ] `If-None-Match` 不匹配 / 缺失 → 200 + body + ETag header
- [ ] 走 webui auth 中间件（未认证 → 401 JSON 与其他 endpoint 一致）
- [ ] ETag 计算稳定（同样数据生成同样 hash；name 排序后再 hash）
- [ ] ETag 仅基于 name / banned / password_hash（其他字段变化不影响）
- [ ] `python3 -m py_compile` 关键文件通过
- [ ] 单元 / 集成测试可选（如果与现有 webui_* 风格一致就做）

## Edge Cases

| 场景 | 行为 |
|---|---|
| 0 用户 | `users: []`, version 为空串 sha256 = `e3b0c44...` 标准值 |
| password_hash NULL | 视为占位 hash 缺失 → 在 ETag 中以空字符串 `""` 参与；JSON 输出为 `null` |
| 同时大量 poll | DB 查询轻量；FastAPI 无需特殊并发限制 |
| 用户名含 emoji / 非 ASCII | `ensure_ascii=False` 序列化 + utf-8 hash，稳定 |

## Technical Notes

- 现有 webui endpoint 参考：`server/routes/webui_users.py`（auth 中间件 + Path validation + 错误处理风格）
- `User` 表字段在 `nextbot/db.py:135-` 定义
- WebUI auth 中间件：`server/routes/webui.py:225` `add_webui_auth_middleware`
- HTTP ETag spec：RFC 7232 `ETag: "<value>"`（双引号）、`If-None-Match: "<value>"`
- 标准库 `hashlib.sha256` 即可，无需新依赖
