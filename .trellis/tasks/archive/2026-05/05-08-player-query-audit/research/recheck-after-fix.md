# Recheck After Fix — 玩家查询审计实施阶段二次复查

- **Date**: 2026-05-08
- **Scope**: 18 修复模块的实现核对（PQA + PQB + cross-cutting）
- **Method**: git diff + 静态阅读 + pyright + ruff + import smoke test
- **Result**: 1 个真实 pyright bug 已自修；其余 18 模块全部落地，行为一致，对照 prd.md / findings 验收通过

---

## 概览

| Bucket | Count | Status |
|---|---|---|
| 🔴 Bugs introduced | 1 | 已自修 |
| 🟠 Incomplete / ineffective fixes | 0 | — |
| 🟢 Quality / style notes | 5 | 信息级，不阻塞 |

实现质量整体高，所有关键路径（V11 / 非 V11 / 异常 / 信号量泄漏）均按 ST-2.1/3.3 模板对齐。

---

## 🔴 已自修的真实 bug

### B-1 `_with_at(...)` 返回 `object`，pyright 抱怨 `bot.send` 形参类型不匹配

- **File:line**: `nextbot/plugins/player_query.py:300-308`（修复前）
- **现象**:
  ```
  player_query.py:304:31 - error: Argument of type "object" cannot be assigned to parameter "message" of type "str | Message[...] | MessageSegment[...]"
  player_query.py:308:31 - error: ...
  ```
  `_with_at(content: str) -> object` 把消息段加字符串后丢失了类型，传入 `bot.send` 触发 `reportArgumentType`。
- **修复**: 直接 inline 两处分支，不再走 `_with_at` helper：
  ```python
  if user is None:
      msg = reply_failure("执行", "未注册账号")
      if at_seg is not None:
          await bot.send(event, at_seg + " " + msg)
      else:
          await bot.send(event, msg)
      return
  ```
- **验证**: `uv run pyright nextbot/plugins/player_query.py` → `0 errors, 0 warnings`

---

## 🟢 Quality notes（不阻塞，仅记录）

### Q-1 `temp_screenshot_path` 文件名格式变了（必要变更，已写入 PRD）

- 旧：`/tmp/{prefix}-YYYYmmddHHMMSS.png`
- 新：`/tmp/{prefix}-YYYYmmddHHMMSS-{8 位 uuid}.png`
- **影响**: 非 V11 fallback 在用户消息中会看到带 uuid 后缀的文件名。PRD 明确允许（"screenshot_temp.py 改 filename 生成"）。V11 路径不展示文件名，无影响。
- 其他 8+ plugins（lottery / warehouse / leaderboard / user_manager 等）也使用 `temp_screenshot_path`，它们的非 V11 fallback 同样获得 uuid 后缀；本次任务范围外，但这是预期收益（同根因修全）。

### Q-2 inventory 成功路径日志仍包含完整 `screenshot_path`（不是 `.name`）

- **File:line**: `player_query.py:512`、`674`
  ```python
  logger.info(f"用户背包截图成功：... file={screenshot_path}")
  logger.info(f"我的背包截图成功：... file={screenshot_path}")
  ```
  这与 `我的地图`、`用户地图`、`查看地图`（全部用 `.name`）不一致。
- **影响**: 仅影响日志聚合工具上的字段长度，非用户可见。也算保留运维上能看到完整临时路径的便利。
- **建议**: 后续可统一为 `.name`，但当前不阻塞，且 PRD 没有强制要求。

### Q-3 V11 路径中 `image` 引用没有显式 `del`

- **File:line**: 三个 map handler 的 V11 分支，例如 `player_query.py:772-786`
  ```python
  image = OBV11MessageSegment.image(file=f"base64://{b64_string}")
  try:
      await bot.send(event, ... image)
  finally:
      del b64_string
      response.payload.pop("base64", None)
  ```
  `image` 内部仍持有 base64 字符串的引用。`del b64_string` 释放的只是局部变量名，`image` 在 `logger.info` + `return` 之间仍然存活。
- **影响**: 内存释放时机晚 ~1 行；GC 在 handler return 时回收。不会影响 `_MAX_BASE64_BYTES` 上限保护，也不会泄漏。OOM 风险已被 per-server semaphore + 200MB 上限完全覆盖。
- **建议**: 可在 `logger.info` 前加 `del image`，但收益有限。不阻塞。

### Q-4 inventory + stats `gather(return_exceptions=True)` 外层 `try/except Exception`

- **File:line**: `player_query.py:434-440`、`597-603`
  ```python
  try:
      inv_result, stats_result = await asyncio.gather(
          inv_task, stats_task, return_exceptions=True
      )
  except Exception:
      await bot.send(event, reply_failure("查询", "无法连接服务器"))
      return
  ```
  设了 `return_exceptions=True` 后 `gather` 不会再 raise（除非 gather 自身被取消，那是 `BaseException`），`except Exception` 实际不可达。
- **影响**: 死代码，无功能影响。
- **建议**: 可移除 `try/except Exception`；但作为防御性写法保留也合理。不阻塞。

### Q-5 Ruff E501 / C901 / ANN201 / RUF001-003 警告

- 引入了一些 line-too-long、复杂度警告，但 baseline (`git stash` + ruff) 已经有 81 个，新增 23 个集中在新加的注释（含中文 emoji 触发 RUF001/003）和 handler 复杂度（OOM 防护增加了分支）。
- 项目历史状态：ruff 全套规则没有 CI gate，许多文件长期带 81-100+ 警告。
- **建议**: 不阻塞本任务；后续可独立做 ruff 整治。

---

## 18 修复模块逐项核对

| # | ID | 落点 | 验证 |
|---|---|---|---|
| 1 | PQB-3.1+3.2 | 查看地图：`_explored_map_semaphores` (Sem(1)) + `_LONG_READ_TIMEOUT` + 200MB 上限 + V11 早 del + 非 V11 走 `reply_block` | ✅ `player_query.py:1005-1093` 闭环；guest 权限保留 |
| 2 | PQB-1.1+2.1 | 我的地图 / 用户地图：`_my_map_semaphores` / `_user_map_semaphores` (Sem(1)) + 长 read + 200MB 上限 + V11 早 del | ✅ `player_query.py:732-816` / `881-966` 闭环 |
| 3 | PQA-3.1 + 5 分身 | `screenshot_temp.py` 加 8 位 uuid 后缀 | ✅ `screenshot_temp.py:30-32` 一处修全所有 6 个调用点 |
| 4 | PQA-CC-1+3 | 抽 `nextbot/large_image.py`：`MAX_BASE64_BYTES` / `LONG_READ_TIMEOUT` / `semaphore_for` | ✅ `nextbot/large_image.py` 39 行；`server_tools.py` import 别名复用、删除局部副本；`player_query.py` 同样 import |
| 5 | PQA-1.1 + PQA-2.1 | 在线 / 自踢：`asyncio.gather` 并行；保持 Server.id 升序 | ✅ `player_query.py:217-271` / `311-337`；`gather` 输入按 `.order_by(Server.id.asc())` 顺序，输出列表自然按提交顺序 |
| 6 | PQB-3.4 + PQB-4.1 | 查看地图 timeout=300s（合并入 #1）；进度 timeout=15s | ✅ `player_query.py:1014` 用 `_LONG_READ_TIMEOUT`；`1129` 进度 `timeout=15.0` |
| 7 | PQA-3.2 + PQA-4.2 | 用户背包 / 我的背包：per-server `_inventory_semaphores`（max=2，共享同一 dict） | ✅ `player_query.py:418` / `586` 复用同一 dict `_inventory_semaphores` |
| 8 | PQA-3.4 + PQA-4.4 | inventory + stats `asyncio.gather(return_exceptions=True)` | ✅ `player_query.py:435-447` / `598-613`；逐一 `isinstance(.., TShockRequestError)` 处理 |
| 9 | PQA-CC-4 | `_to_public_render_url` 改用 `get_server_settings().public_base_url` | ✅ `player_query.py:148-173`；fallback 行为与 `_normalize_public_base_url` 对齐 |
| 10 | PQB-4.5 | 进度补非 V11 fallback | ✅ `player_query.py:1191-1194` `reply_block(reply_success("查询"), ...)` |
| 11 | PQB-4.6 | 进度日志补 `user_id` | ✅ `player_query.py:1165` 渲染日志 + `1180` 截图成功日志均带 `user_id={user_id}` |
| 12 | PQB-1.6 + PQB-3.7 | V11 路径跳过 b64decode + write_bytes | ✅ 三个 map handler 的 V11 分支只用 `b64_string`，跳过 `base64.b64decode` 和 `screenshot_path.write_bytes` |
| 13 | PQB-1.5 + PQB-2.5 + PQB-3.5 | 非 V11 fallback 用 `reply_block` 不暴露 `/tmp` 路径 | ✅ 三个 map + 进度 + 两个 inventory 全部走 `reply_block(reply_success("查询"), [f"📁 文件：{screenshot_path.name}"])` |
| 14 | PQA-3.6 + PQB-1.3 + PQB-2.2 | URL 路径段 `quote(target_user.name, safe="")` | ✅ 5 处 interpolation 全部改为先 `encoded_name = quote(name, safe="")` 后再 f-string；grep `f"/nextbot/users/{` → 全部使用 `encoded_name` |
| 15 | PQA-3.7 | render URL 不写日志 | ✅ `player_query.py:493-495` / `654-656` 日志只剩 `server_id` + `target_user_id` / `user_id`；`1165` 进度同样 |
| 16 | PQA-3.5 + PQA-4.5 + PQB-2.7 | TOCTOU 改名：加注释文档化 | ✅ 4 处 DB 读取后加 `# TOCTOU: ...` 注释（line 410, 573, 716, 862）|
| 17 | PQB-4.4 | progress dict drop non-bool 时 `logger.warning` | ✅ `player_query.py:1140-1154` 收集 `dropped` list 并在非空时 `logger.warning`；过滤掉 `status` 字段以避免噪音 |
| 18 | PQB-X.4 | `int(user_id)` 包 `_safe_at_segment` | ✅ `player_query.py:176-186` helper；3 处 V11 分支 + 2 处 self-kick 调用，失败回退到不带 @ 的发送 |

---

## 关键正确性深审

### 1. Semaphore 释放路径

**所有 6 个 handler（3 个 map + 2 个 inventory + 进度）**的 `async with sem:` 都覆盖：
- 成功路径
- `is_success` false
- b64 missing / oversized
- `TShockRequestError`
- `bot.send` raise
- V11 / 非 V11 双分支

`async with` 通过 `__aexit__` 在任意路径释放信号量。**未发现任何泄漏**。

### 2. V11 路径跳过 b64decode + write_bytes（PQB-1.6 / PQB-3.7）

- `handle_my_map` V11 分支（行 769-786）：直接 `OBV11MessageSegment.image(file=f"base64://{b64_string}")`，**没有** `base64.b64decode` 或 `screenshot_path.write_bytes`；非 V11 fallback（行 788-816）才 decode + write。✓
- `handle_user_map`（行 913-930） / `handle_explored_map`（行 1037-1058）同样模式。✓
- `del b64_string` + `payload.pop("base64", None)` 都在 `bot.send` 之后的 `finally:` 块内，**send 失败也释放**。✓

### 3. `asyncio.gather` 顺序保证

- `handle_online` (行 260-268)：`gather(*(_query_one(s) for s in servers))` 输入按 Server.id 升序，结果列表索引一一对应，`for i, server_lines in enumerate(results):` 保持 ascending 输出。✓
- `handle_self_kick` (行 326-328)：同样保持 Server.id ascending。✓

### 4. inventory + stats `gather(return_exceptions=True)` 错误分支

每个错误形态都被处理：
- 任一为 `TShockRequestError` → 统一回 "无法连接服务器"
- 任一为 其它 `BaseException` → re-raise（surface 开发期 bug）
- 两个都成功后再依次 `is_success(inv)` → `inventory shape` → `is_success(stats)` → 渲染

不会出现"只返回了 inventory 但没 stats"的部分渲染。✓

### 5. `screenshot_temp.py` uuid 后缀覆盖所有碰撞点

`grep "temp_screenshot_path" nextbot/plugins/player_query.py` → 6 个调用：
- 用户背包 (line 498)
- 我的背包 (line 660)
- 我的地图 (line 793)
- 用户地图 (line 942)
- 查看地图 (line 1065)
- 进度 (line 1167)

全部走同一 `screenshot_temp.py` 实现，全部获得 uuid 后缀。✓

### 6. URL 路径注入防御覆盖

`grep "f\"/nextbot/users/{" nextbot/plugins/player_query.py`：
- line 424 inventory（用户）：`{encoded_name}` ✓
- line 427 stats（用户）：`{encoded_name}` ✓
- line 592 inventory（自己）：`{encoded_name}` ✓
- line 595 stats（自己）：`{encoded_name}` ✓
- line 740 my_map：`{encoded_name}` ✓
- line 889 user_map：`{encoded_name}` ✓

所有 6 处 user.name interpolation 都先 `quote(safe="")`。✓

### 7. `_safe_at_segment` 失败 fallback

`_safe_at_segment` 返回 `None` 时所有调用方都退化为不带 @ 的发送：
- 自踢（V11 + 非 V11 都覆盖；行 303-309 / 333-337）
- 我的地图 V11（行 774-778）
- 用户地图 V11（行 918-922）
- 查看地图 V11（行 1042-1046）

**不会** 因为 `int()` 抛 ValueError 中断 handler，整张图片仍会发出。✓

### 8. 验收：无 DB schema 变化

`git diff nextbot/db.py` → empty。`DEFAULT_GUEST_PERMISSIONS` 仍包含 `player_query.map.explored`（按 #1 决策保留）。✓

### 9. 验收：PQA-3.3 故意不修

`git diff server/routes/render.py server/page_store.py server/web_server.py` → empty。render endpoint 鉴权架构无改动。✓

### 10. 验收：失败文案符合全局规范

所有 `reply_failure(action, reason)` 的 action 都是 "查询" 或 "执行"（无对象名）；reason 是 API 原始 `error.message` 透传（`get_error_reason(response)`）或固定语义短语（"无法连接服务器" / "返回数据格式错误" / "返回数据过大" / "保存图片失败" / "读取截图文件失败" / "用户不存在" / "服务器不存在" / "未注册账号" / "暂无服务器"）。**符合"动作 + 结果，原因"**，无"删除服务器"、"保存订单"这类反例。✓

### 11. 验收：M9 `_to_public_render_url`

```python
base_url = str(get_server_settings().public_base_url or "").strip()
```
`get_server_settings().public_base_url` 由 `_normalize_public_base_url` 保证非空（fallback `http://{host}:{port}`），所以 `if not base_url: return url` 这条早出分支基本不会触发——这是预期变化（旧版 `getattr(config, "web_server_public_base_url", "")` 在 env 未配时返回空，新版统一回退到 `http://{host}:{port}`）。和 `server_config._normalize_public_base_url` 行为一致。✓

### 12. server_tools 重构无回归

- `server_tools.py:16-20` import `LONG_READ_TIMEOUT as _LONG_READ_TIMEOUT, MAX_BASE64_BYTES as _MAX_BASE64_BYTES, semaphore_for as _semaphore_for`
- 局部 dict `_map_semaphores` / `_download_semaphores` 仍在（handler-specific 隔离）
- 局部 `_MAX_BASE64_BYTES` / `_LONG_READ_TIMEOUT` / `_semaphore_for` 删除
- `httpx` import 不再需要（已删除）
- `_semaphore_for(_map_semaphores, server_id)` 调用未传 `max_concurrent`，使用默认 `1`（与原行为一致）

### 13. 行为一致性（V11 成功路径字节级对照）

| 命令 | 旧代码 V11 输出 | 新代码 V11 输出 | 一致 |
|---|---|---|---|
| 在线 | "🖥️ 服务器在线状态\n..." | 同 | ✓ |
| 自踢 | `at + "\n🖥️ 自踢结果\n..."` | `at_seg + "\n🖥️ 自踢结果\n..."`（at_seg 为 numeric uid 时等价） | ✓ |
| 用户/我的背包 | `OBV11MessageSegment.image(file=image_uri)` | 同 | ✓ |
| 我的/用户/查看地图 | `at + image` | `at_seg + image`（at_seg 为 numeric uid 时等价） | ✓ |
| 进度 | `OBV11MessageSegment.image(file=image_uri)` | 同 | ✓ |

V11 成功路径对正常 numeric user_id 输出**字节级相同**。

---

## 工具验证结果

| Tool | Result |
|---|---|
| `uv run pyright nextbot/large_image.py nextbot/screenshot_temp.py nextbot/plugins/player_query.py nextbot/plugins/server_tools.py` | **0 errors, 0 warnings** |
| `python -c "import ast; ast.parse(...)"` 4 个文件 | **OK** |
| `python -c "from nextbot.large_image import ..."` | OK，常量值正确 |
| `git diff nextbot/db.py` | **empty**（无 schema 变化） |
| `git diff server/routes/render.py server/page_store.py server/web_server.py` | **empty**（PQA-3.3 未触碰） |

ruff 有警告但全为 baseline 既存类别（line-too-long、复杂度），项目无 ruff CI gate，本次新增 ~23 个警告集中在新加的中文注释（RUF001-003 ambiguous Unicode）和 handler 复杂度（OOM 防御加分支），**不影响功能**。

---

## 总评

**信心度**: 高（95%+）

- 18 个修复模块全部落地，对照 prd.md / a-findings / b-findings / main-agent-recheck 一一打勾
- 关键 OOM 路径（3 个 map handler + 2 个 inventory）信号量、上限、超时、早释放四件套对齐 ST-2.1/3.3 模板
- 成功路径在 V11（生产唯一适配器）字节级一致
- 失败路径均使用 `reply_failure(action, reason)` 全局规范格式
- 1 个真实 pyright 类型 bug 已自修

**未发现破坏性问题。**
