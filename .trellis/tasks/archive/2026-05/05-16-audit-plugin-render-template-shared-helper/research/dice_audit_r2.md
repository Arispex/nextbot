# Research: 掷骰子 命令更新 审计 R2（验证 R1 修复 + 再扫）

- **Query**: 验证 R1 8 个修复 + 再扫 6 文件改动周边
- **Scope**: internal only — 同 R1 6 files
- **Date**: 2026-05-16
- **R1 报告**: `dice_audit.md`（20 findings, 2H/6M/8L/4I）

---

## R1 修复验证

### H-1+H-2+M-3  `_sanitize_at_user_id` helper + 公开入口净化  PASS
**实际改动**: `screenshot_render.py:33-48` 新增 `_sanitize_at_user_id`，公开入口 `render_and_send_screenshot` 在 line 93 一次性净化；下游 `_inner` 信任已净化值。V11 路径 `OBV11MessageSegment.at(at_user_id)`（line 165）与非 V11 fallback `f"@{at_user_id} "`（line 186）都收到已 `int(x)` 校验过的字符串或 None。
**判定**: PASS — 把 H-1 / H-2 / M-3 三条同链 finding 一次性闭合。
**Risk**: Low —
- `int("01")` 通过 → 前导零保留为字符串 "01" 传给 V11 `at`，OneBot V11 内部会再 `int(...)` 兜住，OK。
- `int("1e3")` 抛 → None，安全。
- `int(" 12 ")` 因 line 41 已 `.strip()` 通过 → "12" prepend，OK。
- `at_user_id="all"` → `int("all")` 抛 → 退化为 None。**注意**：OneBot V11 `at` 段官方支持 `"all"` 全员 @，本 helper 现在**拒绝** "all"。当前 dice 唯一调用方传 `event.get_user_id()` 不会传 "all"，无回归；但若未来其它 plugin 想用 `@all` 推送，会被静默拦截 → 见 **N-INFO-1**。

### M-2  `threading.Lock` 包模板缓存  PASS
**实际改动**: `dice_page.py:4` import threading；line 20 `_template_lock = threading.Lock()`；`_load_template` 整段 `with _template_lock:` 包裹 stat + read + 写 cache。
**判定**: PASS — 多线程并发命中 `asyncio.to_thread(renderer, payload)` 时，stat/read/写 cache 三段原子化。
**Risk**: Low（无死锁）—
- 锁内仅做 `Path.stat()` + `Path.read_text()` + tuple 赋值，**无任何 await / 无回调 / 无嵌套锁** → 不可能与 asyncio 产生死锁。
- `read_text` 是 blocking I/O，锁持有时间 ≈ disk read 时长（模板 14 KB），并发请求会串行化模板加载（cache 命中后只走 stat → 仍串行但极快）。性能影响可忽略。
- 多 uvicorn worker（多进程）场景：每个进程独立 cache + 独立 lock，互不干扰，行为正确。

### M-1  Semaphore(4) 加注释  PASS
**实际改动**: `dice.py:57-59` 三行注释明确说明 "dice 单页 720×720 轻量，相比项目其它截图业务（Semaphore(2)）放宽到 4。单玩家 30s cooldown 已限并发，群多人同时玩的峰值需更大缓冲。"
**判定**: PASS — R1 推荐的方案 (a) "加注释解释偏离"已落地，方向正确。
**Risk**: None — 文档性修复，不改运行时行为。

### M-6  豹子绕过 win_rate 加注释  PASS
**实际改动**: `dice.py:216-219` 三行注释说明 "豹子保留自然概率 ~2.78%（受 6 个三连组合 / 216 总组合约束）。不接入 win_rate：10× 派奖 + 50% 命中 → bot 长期暴亏（EV 5×）。如需运营干预豹子命中，新增 triple_win_rate 参数走 lottery / 概率派奖路径。"
**判定**: PASS — 明确写出 EV 数学 + 不接入理由 + 未来扩展路径。
**Risk**: None — 文档性修复。

### L-3  `_clamp_die` 越界 warning  PASS
**实际改动**: `dice_page.py:32-41` `_clamp_die` 现在：
- `int(value)` 抛 → `logger.warning("无法解析为整数...已 clamp 为 1")`
- `n < 1 or n > 6` → `logger.warning("越界...已 clamp 到 [1,6]")` 后 clamp
**判定**: PASS — 静默兜底变可观测；R1 推荐方案落地。
**Risk**: Low — **不会刷屏**。
- plugin 端唯一数据源是 `random.randint(1,6)` / `random.choice(_ALL_DICE_COMBOS)`，永远 1..6，正常路径 0 warning。
- `render()` 第二次 `_clamp_die`（line 97）从 `page_store` 取 payload，payload 已被 `build_payload` clamp 过 → 第二次 0 warning。
- 仅当 attacker 篡改 in-memory `page_store`（loopback only，需进程权限）时才会触发 → 警告期望频率 = 0/s，可观测性正确。
- warning 不含 PII（仅 `value=repr`，e.g. `value=0`），符合后端日志规范。

### L-7  失败兜底 except 加 warning  PASS
**实际改动**: `dice.py:308-309` 内层 `except Exception as inner: logger.warning(f"掷骰子失败兜底回复异常：reason={inner!r}")`
**判定**: PASS — R1 推荐方案落地。
**Risk**: Low — 仅适配器断连时打 warning，频率受 adapter 状态约束；`reason={inner!r}` 不含 user 数据，符合规范。

### L-8  截图发送失败 warning  PASS
**实际改动**: `dice.py:370-373` 捕获 `render_and_send_screenshot` 返回值 `ok`，`if not ok: logger.warning(f"掷骰子截图发送失败：user_id={user_id} dice={d1},{d2},{d3}")`
**判定**: PASS — R1 推荐方案落地。
**Risk**: Low — **PII 检查**: warning 包含 `user_id`（QQ 号）与 dice tuple。QQ 号在项目内 ≥ 20 处其它 logger.info / logger.warning 中也直接出现（如 line 305 `logger.exception(f"...user_id={user_id}")`、line 334-337 `logger.info(...)`），属项目既定日志风格，未引入新泄漏面；dice tuple 是游戏结果非敏感数据。符合"动作+对象+结果+上下文"日志规范。

### L-10  `player_name[:32]` 长度 cap  PASS
**实际改动**: `dice_page.py:71-72` 注释 "32 与 User.name 注册路径 max 长度对齐；defense-in-depth 防极长昵称破版" + `str(player_name).strip()[:32]`
**判定**: PASS — R1 推荐方案落地。
**Risk**: Low — Python `str[:32]` 是 **codepoint 切片**（不是字节），中文场景按字符切（"测试昵称" → 4 字符），符合预期；不会出现切到 utf-8 续字节中间产生乱码字符的问题。模板 `.meta-value` 无 ellipsis 但 `header-meta` 有 `flex-wrap: wrap`，32 字符极限下排版可接受。

---

## 验证小结

| ID | 状态 | 备注 |
|---|---|---|
| H-1+H-2+M-3 | PASS | 三 finding 单点闭合；唯一边缘是 `"all"` → 见 N-INFO-1 |
| M-2 | PASS | 锁内无 await，无死锁风险 |
| M-1 | PASS | 文档化 |
| M-6 | PASS | 文档化 |
| L-3 | PASS | warning 不刷屏，无 PII |
| L-7 | PASS | reason 安全 |
| L-8 | PASS | PII 与既有 log 风格一致 |
| L-10 | PASS | codepoint 切片，中文安全 |

**8 / 8 PASS，0 NEW-ISSUE 在 H/Critical 等级**。

---

## R2 新发现

### N-INFO-1  `_sanitize_at_user_id` 静默拒绝 OneBot V11 合法字面量 `"all"`
**File**: `nextbot/screenshot_render.py:33-48`
**Severity**: Info
**Dimension**: api-design / completeness
**Issue**: OneBot V11 `MessageSegment.at` 官方支持 `qq="all"` 表示全员 @；`_sanitize_at_user_id` 现在用 `int(s)` 一刀切，把 `"all"` 当非法值返回 None。当前 dice 唯一 caller 传 `event.get_user_id()` 不会传 "all"，**当前无回归**。但 helper 是 cross-plugin shared surface（R1 backlog B-5 提到 leaderboard/lottery/red_packet 未来迁入），若未来出现 "群广播结果截图 @all" 的需求，会被静默拦截。
**Fix sketch**（非必须）: `if s == "all": return "all"; try: int(s); ...`。
**Risk if unfixed**: None — 当前 0 caller 受影响；未来扩展时再处理即可。

### N-INFO-2  `_template_lock` 与 `_template_cache` 在多进程 fork 后状态独立（uvicorn workers>1）
**File**: `server/pages/dice_page.py:17-29`
**Severity**: Info
**Dimension**: deployment / consistency
**Issue**: Python `threading.Lock` + module-level `_template_cache` 在 `os.fork()` 后每个 worker 独立持有；模板热更新时各 worker 独立检测 mtime。**这是正确且期望的行为**（无共享状态、无 cross-worker race），但 R1 已指出项目当前 `workers=1`，多 worker 路径未被验证 —— 锁的行为在 fork 后实际是 best-case（每个进程一把新锁），无需调整。
**Fix sketch**: 无需修。可在注释里加一行 "多 worker / fork 时每进程独立 cache，无 cross-worker 同步需求"。
**Risk if unfixed**: None。

### N-LOW-1  `_clamp_die` 在 `render()`（dice_page.py:97）路径上的 warning 会用 `value` 而非 `payload["dice"]` 索引位置，调试时难以定位是 d1/d2/d3 中的哪一个
**File**: `server/pages/dice_page.py:97`
**Severity**: Low
**Dimension**: observability
**Issue**: `render()` 内 `[_clamp_die(d) for d in (payload.get("dice") or [1, 1, 1])[:3]]` 把每个 d 单独 clamp；越界时 `_clamp_die` 只记 `value=...` 不带 index。若 attacker 篡改 store 让 `dice=[3, 99, 5]`，只能看到 `value=99` 不知道是哪颗。**当前不重要**（路径仅 defense-in-depth），属调试体验微差。
**Fix sketch**: `_clamp_die(d, *, ctx: str = "")` 加 context 参数；call site 传 `f"index={i}"`。或维持现状（已属冗余防御层）。
**Risk if unfixed**: None — 仅诊断信息粒度。

### N-LOW-2  `dice.py:309` `logger.warning(f"...reason={inner!r}")` 中 `inner` 来自 `bot.send(...)` 异常，未含调用上下文
**File**: `nextbot/plugins/dice.py:306-309`
**Severity**: Low
**Dimension**: observability
**Issue**: 兜底 `except Exception as inner` 只 log `reason={inner!r}`，**未带 user_id**。outer except 已 `logger.exception(... user_id={user_id})` 包含 user_id（log line 305），同一异常链上下文完整 —— grep 时按 user_id 仍能找到 outer log。可接受，但若日志聚合系统按 line 分发，单看 line 309 缺关联 key。
**Fix sketch**: `logger.warning(f"掷骰子失败兜底回复异常：user_id={user_id} reason={inner!r}")`。
**Risk if unfixed**: Low — 排障时需关联两行 log。

---

## R1 未修 finding 状态（未在本轮修复列表中）

| R1 ID | 状态 | 说明 |
|---|---|---|
| M-4 | 维持 backlog | `at_user_id` 仅 dice 使用 — 跨 plugin 决策项 |
| M-5 | 维持 open | `triple_lose` vs `lose` 文案差异 — UX 微差 |
| L-1 | 维持 open | `_safe_param_int` 无 `max=` — defense-in-depth 缺一环 |
| L-2 | 维持 open | `_cooldown_map` 无上限 — 长期运行风险 |
| L-4 | 维持（按设计）| render() 二次 clamp 是 defensive 设计 |
| L-5 | 维持 open | 模板 `Number(... \|\| 0)` NaN 风险 — 内层冗余 |
| L-6 | 维持（按设计）| dice-face 不复用 — 未来动态刷新场景 |
| L-9 | 维持 info | `generated_at` 取 build 时刻 |
| I-1 ~ I-4 | 维持 info | 代码质量 / 可读性 |

---

## 收口判定

- R1 8 个修复：**8 PASS / 0 NEW-ISSUE**
- R2 新发现：**0 Critical / 0 High / 0 Medium / 2 Low / 2 Info**
- 无 H 及以上新风险，符合"0 New Critical/High"收口条件。

**建议：声明 dice 审计闭环**。

R2 新发现的 4 项（N-INFO-1 / N-INFO-2 / N-LOW-1 / N-LOW-2）均为可观测性 / 完备性微调，可一并并入 R1 维持 open 的 L/I backlog 列表，由后续单独清理 task 处理。

R1 维持 open 的 H/M finding：仅 M-4（跨 plugin scope）与 M-5（UX 微差），均不属本任务硬指标，转出 backlog。

---

## Caveats

- 未跑 monkeypatch 单元测试覆盖 `_sanitize_at_user_id` 所有边界字符串，仅基于 Python `int()` 语义推理。
- 未对 `threading.Lock` 在 cython/uvloop 路径上做基准测试，假设 `asyncio.to_thread → ThreadPoolExecutor` 标准行为。
- 未跨核对 `screenshot_render.py` 的下游 `_render_and_send_inner` 签名是否在其它 caller（leaderboard 等）侧需同步调整 —— 本轮仅看 dice 链路。
