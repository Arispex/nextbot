# Servers R2 复审 — 主代理二次审核

## 整体结论

R1 (`1355521`) 修了 ~22 项，**整体方向正确**但 R1 引入 **2 处 HIGH backend regression**（token 链改造的两个边界没考虑到）+ 1 处 MAJOR + 若干 Medium polish。前端 R1 13 项**全 PASS 无 regression**。

## R1 修复复审

### 后端：5 PASS + 2 HIGH regression + 3 MAJOR/Medium

| R1 修复 | 状态 |
|---|---|
| `_mask_token` helper | ✅ PASS |
| `_serialize_server` mask | ✅ PASS |
| reveal endpoint 新增 | ✅ PASS |
| A-4 plugin-config 白名单 | ✅ PASS |
| B-4 /test timeout 10s | ✅ PASS |
| B-6 IntegrityError retry | ✅ PASS |
| A-8 path Path(ge=1) | ✅ PASS |
| A-9 keyword [:200] | ✅ PASS |
| D-2 client_ip + user_agent | ⚠️ PASS 但 4/9 endpoint user_agent 缺失（MAJOR-2） |
| D-3 reindex log | ✅ PASS |
| PUT token 保留原值 | ❌ **2 处 HIGH regression**（见下） |

### 前端：13/13 全 PASS 无 regression

| R1 修复 | 状态 |
|---|---|
| H-1 token 链前端（10 子项） | ✅ ALL PASS |
| R1 self-fix（modalCloseButton arrow wrap） | ✅ PASS |
| H-2 verify enum 映射 | ✅ PASS |
| H-3 进行时文案（PRD 说 7 处实际 8 处） | ✅ PASS |
| H-4 空态文案 | ✅ PASS |
| B-1/B-2 debounce + abort | ✅ PASS |
| B-7 test/verify timeoutMs 30s | ✅ PASS |
| C-2-D/L/M/E/K 文案 | ✅ PASS |

## R2 新发现：2 HIGH + 1 MAJOR + 多项 Medium / Low

### 🔴 HIGH（2 项，R1 引入的真实 regression）

#### HIGH-1: PUT `{"token": null}` 把 token 写成字面量 "None"

**文件**：`webui_servers.py:227-228`

**Bug 链**：
```python
raw_token = str(payload.get("token", "")).strip() if isinstance(payload, dict) else ""
keep_existing_token = (not raw_token) or _is_mask_token(raw_token)
```

如果 client 传 `{"token": null}`:
- `payload.get("token", "")` → `None`
- `str(None)` → `"None"`（**字面量字符串**）
- `.strip()` → `"None"`
- `_is_mask_token("None")` → False
- `keep_existing_token = False`
- validation 接受 "None" 作为合法 token（_normalize_token 仅检查长度 1-128）
- **server.token 被写成字面量 "None"** → DB 污染

**修复前**：`str(payload.get("token", ""))` 把 None → "None"

**修复后**：
```python
token_value = payload.get("token") if isinstance(payload, dict) else None
if token_value is None:
    raw_token = ""
else:
    raw_token = str(token_value).strip()
keep_existing_token = (not raw_token) or _is_mask_token(raw_token)
```

**触发概率**：低（旧 caller 不会发 null），但若 client 误传或 JSON 序列化空值，token 被污染。

---

#### HIGH-2: list endpoint 缺 client_ip + user_agent 审计日志

**文件**：`webui_servers.py` list endpoint

R1 D-2 修了 8 个 endpoint 但 list endpoint 没在日志中记录 client_ip + user_agent（只有失败路径有 logger.warning，成功 list 不打日志符合规范，但失败路径缺字段不一致）。

实际上 list 成功路径不打日志是合理的（高频读路径），但**失败路径缺 client_ip**与其他 endpoint 风格不一致。

**修复**：list endpoint 失败路径（如有）补 client_ip + user_agent，与其他 endpoint 风格统一。

---

### 🟠 MAJOR（1 项）

#### MAJOR-1: `_is_mask_token` 用 `startswith("****")` 与真实 token 前缀理论冲突

**文件**：`webui_servers.py:54-56`

**风险**：TShock token 通常是 32 位 URL-safe alphanumeric，**几乎不可能以 `****` 开头**（`*` 在 base64 字符集外），但如果运维手工设置弱 token 含 `*`，会误判为 mask → 跳过更新。

**触发概率**：极低（需要运维手工设置含 `*` 开头的 token）

**修复**：可保持现状（实际不会触发），或加更严格的判断：除前缀 `****` 外还要求**总长度等于 mask 长度**（如 `len(token) == 4 或 8`，对应 mask + 末 4 位）

---

### 🟡 Medium / Low（多项）

| ID | 一句话 |
|---|---|
| Backend M-1 | reveal endpoint 缺 rate limit（敏感操作但 audit-only OK，可降级 Low） |
| Backend M-2 | `_load_server_or_none` 吞 DB 异常逃出 JSON 契约 |
| Backend M-3 | `release_server_semaphores_all` 失败影响 commit 已成功的 delete 返回 500 |
| Backend M-4 | plugin-config GET/PATCH timeout 仍默认 5s（与 verify-nextbot 10s 不齐） |
| Backend M-5 | user_agent 在 4/9 endpoint 缺失（与 D-2 一致性） |
| Frontend M-1 | F-B-3 unwrapData 抛错缺 action 前缀 |
| Frontend Low | F-A-2/F-B-2/F-B-13 modal 关闭路径 sanitize / guard 不完整（无安全/数据风险） |

### Scope-out backlog（不在 servers scope）

- F-B-9 servers.js 整体缺 Escape / focus trap / focus 恢复（与 commands.js 不一致，**跨模块基线问题**）
- max+1 主键并发改 autoincrement schema（跨 db.py）
- 4 跨模块 finding

## 主代理终判

| 类别 | 数量 |
|---|---|
| Critical | 0 |
| **High** | **2**（HIGH-1 + HIGH-2，R1 regression） |
| MAJOR | 1（MAJOR-1 边界 case） |
| Medium | ~6 |
| Low | ~5 |
| Scope-out backlog | 4 |

**HIGH-1 必修**（R1 真实 regression，token 被污染为 "None" 字面量）。

## 修复推荐梯队

| 选项 | 内容 | 行数 |
|---|---|---|
| **A** | 仅修 HIGH-1（null token → "None" 字面量）| ~5 行 |
| **B** | A + HIGH-2 + MAJOR-1 + 5 Medium | ~50 行 |
| **C** | 全修 | ~100 行 |
| **D** | 按 ID 逐条选 |

**强烈推荐至少修 HIGH-1（R1 真实 regression，DB 污染风险）**。
