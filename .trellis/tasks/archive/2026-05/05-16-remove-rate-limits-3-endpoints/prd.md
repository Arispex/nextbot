# 移除 3 个端点的限速 / 节流

## Goal

按用户偏好移除以下 3 个端点的所有 rate-limit / cooldown 逻辑，调用方不再会拿到 429。

- `POST /webui/api/login-requests` —— per-name 5 分钟 1 次（H-1 节流）
- `POST /webui/api/player-events` —— per-IP 60s / 30 次（H-2 节流）
- `POST /webui/api/users/{id}/sync-whitelist` —— per-user_db_id 5s 冷却（M-4 cooldown）

> 注：`POST /webui/api/session` 的登录失败速率限制（H-A3）**不在本任务范围**，那是登录端点的 brute-force 防护，仍需保留。

## Requirements

1. 删除 3 个端点函数体内的"限速检查"代码块（`if not allowed: return api_error(429, ...)`）。
2. 删除支撑代码（已无引用即删，不留 dead code）：
   - `_check_*_rate_limit` / `_record_*` / `_check_*_cooldown` 辅助函数
   - 滑动窗口存储（`deque` / `dict` / `threading.Lock`）
   - 配置常量（窗口秒数、上限、冷却秒数）
3. 不变更：
   - 各端点其它输入校验 / 鉴权 / 业务逻辑
   - `webui_login_requests.py` 与 `webui_player_events.py` 的失败 detail 字段结构
   - 错误响应信封约定（`{error: {code, message, details}}`）
4. 顺手清理：
   - 因删 deque 导致 `from collections import deque` 不再使用 → 删 import
   - 因删 lock 导致 `import threading` 不再使用 → 删 import
   - `import time` 如果还有其它用途则保留

## Acceptance Criteria

- [ ] 3 个端点的代码中不存在 429 / `too_many_requests` / `rate_limited` 返回路径
- [ ] grep 不到 `_check_login_request_rate_limit` / `_record_login_request` / `_check_player_event_rate_limit` / `_record_player_event` / `_check_sync_cooldown`
- [ ] grep 不到 `_LOGIN_REQUEST_WINDOW_SEC` / `_LOGIN_REQUEST_MAX_PER_WINDOW` / `_login_request_history` / `_login_request_lock`
- [ ] grep 不到 `_PLAYER_EVENT_WINDOW_SEC` / `_PLAYER_EVENT_MAX_PER_WINDOW` / `_player_event_history` / `_player_event_lock`
- [ ] grep 不到 `_SYNC_COOLDOWN_SECONDS` / `_sync_last_request` / `_sync_cooldown_lock`
- [ ] 模块级 import 清理（删 deque / threading / time 中未使用的）
- [ ] `python3 -c "import server.routes.webui_login_requests"` 等 3 个模块能 import 通过

## Out of Scope

- `POST /webui/api/session` 登录失败速率限制（保留）
- 其他端点加 / 删限速
- 限速文案 / 错误码改造
- 更新 `docs/webui_api_migration_guide.md`（先改代码，文档刷新留作后续任务，避免本任务范围膨胀）

## Technical Approach

每个文件按相同套路三步走：

1. 删函数体内 rate-limit 检查 + 记录调用（`_record_*`）
2. 删模块级常量 / 锁 / dict / deque
3. 删辅助函数
4. 清理多余 import

## Files

- `server/routes/webui_login_requests.py`
- `server/routes/webui_player_events.py`
- `server/routes/webui_users.py`

## Technical Notes

- 文件已读过；3 处 rate-limit 在结构上互相独立，可并行修改。
- 删除时注意：`webui_player_events.py` 在 rate-limit 块中有"通过校验后才记节流"的注释，对应的 `_record_player_event(client_ip)` 在 line 325 附近；需要一并删。
- `webui_users.py` 中 sync-whitelist 的 cooldown 是 per-user_db_id 维度，与 web UI"5s 内连点"防误触有关；用户明确要求删除，按指令执行。
