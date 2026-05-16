# Research: 掷骰子 命令更新 审计（dice 渲染管线 + win_rate + at_user_id）

- **Query**: 全维度审计 dice 6 处改动，输出 file:line + severity，仅报告，不改代码
- **Scope**: internal only — 6 files（无 spec 比对，仅与 prior-art render audit 对照）
- **Date**: 2026-05-16
- **Files audited (6)**:
  - `nextbot/plugins/dice.py` (365 行)
  - `nextbot/screenshot_render.py` (178 行)
  - `server/pages/dice_page.py` (106 行)
  - `server/templates/dice.html` (422 行)
  - `server/web_server.py` (改动 `create_dice_page` 段：230-263 行)
  - `server/routes/render.py` (改动 `render_dice` 段：112-114 行 + 12 行 import)

---

## 0. 上下文 / 与 prior-art 对照

新增的 dice 渲染走的是 **JSON-sandwich** 标准三段式（与 prior-art Bucket C/D 一致）：

1. `dice_page.build_payload(...)`：所有用户可控字符串 `str(...).strip()`，所有数字 `int(...)`，dice tuple `_clamp_die` 强制 1..6，`result_kind` 白名单过滤。
2. `dice_page.render(payload)`：`json.dumps(..., ensure_ascii=False).replace("</","<\\/")` 后 `template.replace("__DICE_DATA_JSON__", data_json)`。
3. `dice.html`：占位符位于 `<script id="dice-data" type="application/json">__DICE_DATA_JSON__</script>`；JS 端 `JSON.parse(textContent)` + `textContent` / `createElement` / `setAttribute` 渲染。Inline `<script>` 含 `try/catch` 兜底 fallthrough（`fallback` body class），优于其它 10 个未做兜底的模板。

**亮点**（相对 prior-art 改进）：

- `dice_page.py` **实现了模板 mtime cache**（`_template_cache: tuple[float, str]` 在 `_load_template`），直接解决 prior-art `render_pages.md` 的 High 1（"每次请求同步读模板阻塞 async"）。但 cache 写时 race condition 待评估（见 P-2）。
- `dice.html` **顶部 7 行包含 `[hidden] { display: none !important; }`**（line 14），是 prior-art `render_templates.md` Medium U1 的 17 模板中第 8 个加上守卫的，方向正确。
- `dice.html` 加了 `JSON.parse` 失败 fallback（`body.fallback` class，line 286-288 + 218-221），是 prior-art `render_templates.md` Medium S3 的 17 模板中**首个**补上 fallthrough 防御，方向正确。
- `screenshot_render.py:115-120` 文件大小为 0 字节早返回 + warning log，比 prior-art 仅靠 `MAX_BASE64_BYTES * 4 // 3` 上限检查更稳。
- 算法层 **预计算 4 个 tuple set**（line 33-55）一次性 import-time 计算，运行时 `random.choice` O(1)，相对 every-roll 自然 `randint`+resample 的实现更快。
- Win/loss 分类用 `result_kind` 白名单 + plugin 端按 `choice`/`is_triple`/`net` 五分支判定（line 316-327），与模板 5 个 `RESULT_PRESETS` 一一对应。

---

## 1. Sanity 数学校验

```
total combos          = 216  ✓
triples               = 6    ✓ (1,1,1)…(6,6,6)
WIN_BIG (非豹子,sum≥11) = 105 ✓
LOSE_BIG (豹子∪非豹子sum≤10) = 111 ✓
WIN_SMALL (非豹子,sum≤10) = 105 ✓
LOSE_SMALL (豹子∪非豹子sum≥11) = 111 ✓
WIN_BIG ∩ LOSE_BIG    = ∅   ✓ (互斥)
WIN_BIG ∪ LOSE_BIG    = 216 ✓ (全集覆盖)
WIN_SMALL ∩ LOSE_SMALL = ∅  ✓
WIN_SMALL ∪ LOSE_SMALL = 216 ✓
triples ⊂ LOSE_BIG     ✓ (PRD 预期：选大被豹子通杀)
triples ⊂ LOSE_SMALL   ✓
```

**Critical 验证**：三连 `(4,4,4)/(5,5,5)/(6,6,6)` 的 sum 分别是 12/15/18 ≥ 11，**理论上应同时落入 "大" 与 "豹子"**。代码用 `not _is_triple_combo(d) and sum(d) >= 11` 把三连**强制排除**出 WIN_BIG，落到 LOSE_BIG → 这是 "选大被豹子通杀"（`result_kind=triple_kill`）的实现，与 `dice.py:240-245`（`if not is_triple and total >= 11: payout=cost*big`）一致。**算法自洽，无 bug**。

**边界**：

- `win_rate=0` → `random.random() < 0.0` 永远 False → 永远走 lose_set → 非豹子时永不中（豹子分支不受 win_rate 控制，自然概率）。✓
- `win_rate=100` → `random.random() < 1.0` 永远 True（Python `random.random()` 返回 `[0.0, 1.0)`）→ 永远走 win_set → 非豹子时**严格** 100% 中。✓
- `win_rate=50` 下 choice="大" 时摸到豹子的概率 = `0.5 × 0 + 0.5 × 6/111 ≈ 2.703%`，几乎等于自然 `6/216 ≈ 2.778%`。**选豹子概率随 win_rate 微微波动但量级正确**（见 ALG-1 讨论）。

---

## 2. Severity Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High     | 2 |
| Medium   | 6 |
| Low      | 8 |
| Info     | 4 |
| **Total** | **20** |

**Top 3 issues**：

1. **`screenshot_render.py:165-166` non-V11 fallback 将原始 `at_user_id` 字符串拼到 head 文案前，无任何 sanitize / numeric 校验** —— 控制字符、`\n`、模板字符可注入文案 / 破排版。dice 是项目中**唯一**传 `at_user_id` 的 plugin。(High)
2. **`dice.py:174 + line 363` 把 `event.get_user_id()` 同时作为 `player_qq` 写入页面 payload 与 `at_user_id` 传入 `render_and_send_screenshot`，未通过 `safe_at_segment_or_empty` 同款 numeric 校验**；非数字 user_id（V11-shim / Telegram bridge）会破排版且 V11 路径 `OBV11MessageSegment.at(at_user_id)` 内部不再走 `int(...)` 强转（line 144-147）会抛或生成异常 @ 段。(High)
3. **`dice_page.py:17-22` template cache 未加锁** —— 多线程 / 多 worker 下 `_template_cache` 读改写可能交错（uvicorn workers=1 + asyncio threadpool 实际是单 event loop + threadpool，但 `asyncio.to_thread` 让 `render` 跑在线程池里，多请求并发命中 `_load_template` 时存在数据竞争虽通常无害但形式上是 bug）。(Medium)

---

## 3. Findings

---

### H-1 non-V11 fallback head prepend `@{at_user_id}` 无 sanitize

**File**: `nextbot/screenshot_render.py:165-166`
**Dimension**: security
**Issue**: V11 路径走 `OBV11MessageSegment.at(at_user_id)`（line 145）由 OneBot 适配器格式化为 CQ 码或 segment dict，相对安全。但**非 V11 fallback** 直接 `head = f"@{at_user_id} " + head`（line 166）把调用方传入的 `at_user_id` 原始字符串拼进文案。调用方（`dice.py:363`）传入的是 `event.get_user_id()`，**未经任何校验**。如果非 V11 适配器 push 一个含 `\n` / 控制字符 / `‮`（右向 override） / 大段空白 / OneBot CQ 码语法的 user_id 字符串，会破排版甚至伪造其他段。`text_utils.safe_at_segment` 内部走 `int(user_id)`（line 100），但本路径绕过了它。
**Fix sketch**：在 helper 入口对 `at_user_id` 做与 `safe_at_segment` 等价的校验：`if at_user_id is not None: try: int(at_user_id); except: at_user_id = None` 后再 prepend；或 fallback 路径只在 `at_user_id.isdigit()` 时拼。
**Risk if unfixed**: Medium — 当前 nextbot 主要走 V11 路径，非 V11 仅 fallback；但 helper 是 cross-plugin 共享（leaderboard / lottery / about / tutorial / menu / ban / permission_manager 后续会迁），未来一处恶意 push 即放大。

---

### H-2 V11 路径 `OBV11MessageSegment.at(at_user_id)` 缺 int 强转防御

**File**: `nextbot/screenshot_render.py:143-148`
**Dimension**: robustness / security
**Issue**: V11 路径直接 `OBV11MessageSegment.at(at_user_id)`（line 145）。OneBot V11 `MessageSegment.at` 期望 `int | "all"`；nextbot 项目内 ≥17 处 handler 走 `safe_at_segment` 做 `int(...)` 防御（`text_utils.py:99-103`），本 helper **未对齐**。dice 唯一调用方传入 `user_id = event.get_user_id()`，V11 下确实是数字字符串，但 helper 作为通用接口（参数 typed `str | None`），不能假设；若 caller 传入字符串 `"all"` 或非数字字符串，segment 行为未定义（可能直接 raise ValueError 让 `bot.send` 失败）。
**Fix sketch**：与 H-1 共用：helper 入口走 `safe_at_segment` / `safe_at_segment_or_empty`，把 segment 实例传入 V11 分支即可（避免重复 `int(...)`）；非 V11 分支用 sanitize 后的 `at_user_id_safe`。
**Risk if unfixed**: Medium — 与 H-1 同链，单点修复。

---

### M-1 `_dice_semaphore = Semaphore(4)` 与项目其它截图业务 Semaphore(2) 不一致

**File**: `nextbot/plugins/dice.py:58`
**Dimension**: perf / consistency
**Issue**: 全项目其它截图业务统一 `Semaphore(2)`（tutorial / menu / shop / about / warehouse / permission_manager / ban / lottery / red_packet / leaderboard / user_manager 全部 `2`，见 grep 输出）。dice 用 `4`。dice 输出 viewport `720×720` `fit_content_height`（line 357-359），单页较轻；提高并发是合理的 perf 选择，但**没有 spec 注释解释为何高于约定**。Playwright headless 浏览器全局只有一个进程，4 个 dice + 2 个 leaderboard + 2 个 lottery 同时进行 = 8 tab，峰值 RAM 可能超预算。
**Fix sketch**：(a) 注释里写明 "dice 单页 ≈ 720×720 轻量，可放宽到 4"；(b) 或者降到 `2` 与其它对齐 —— 业务上 dice 单玩家 30s 冷却已限制，多并发收益不明显。
**Risk if unfixed**: Low — 单点 RAM 峰值轻微抬升；推荐 (a) 加注释。

---

### M-2 `dice_page.py` template cache 非线程安全

**File**: `server/pages/dice_page.py:14-22`
**Dimension**: correctness / perf
**Issue**: `_template_cache: tuple[float, str] | None = None` 模块级全局；`_load_template` 在 `render` 内被调用，而 `render` 通过 `server/routes/render.py:50` 的 `await asyncio.to_thread(renderer, payload)` 跑在 ThreadPoolExecutor 上。多个并发 `/render/dice/<token>` 请求会**真并行**调用 `_load_template`：
- 两个线程同时 `stat(...).st_mtime` 都得到旧值，都进入 `read_text`；最终 `_template_cache =` 写入两次 —— 数据竞争形式上存在，实际无害（值相同）。
- 但若部署中替换模板（`mtime` 改变），多线程可能短暂看到旧值（同样无害）。

属于 **形式 bug**，不影响功能；与 prior-art `render_pages.md` High-1 推荐方案 A 的对齐度高，可接受。
**Fix sketch**: `import threading; _template_lock = threading.Lock()`，`_load_template` 内 `with _template_lock:`；或用 `functools.lru_cache(maxsize=1)` 封装且接受 mtime 作 key。
**Risk if unfixed**: Low — 实际无并发 corruption；只是与 spec/best-practice 不严格对齐。

---

### M-3 `player_qq` / `at_user_id` 共用同一原始 `user_id`，缺统一 normalize 入口

**File**: `nextbot/plugins/dice.py:174, 337, 363`
**Dimension**: security / consistency
**Issue**:
- `user_id = event.get_user_id()` (line 174) 直接传入：
  - `create_dice_page(..., player_qq=user_id, ...)` → 进 payload → 进模板 (`#meta-player-qq` `.textContent`, line 309，安全 via textContent)
  - `render_and_send_screenshot(..., at_user_id=user_id)` → V11 / 非V11 双路径
- 项目其它 plugin 调用 `safe_at_segment_or_empty(event.get_user_id())`（line 139）拿到 `at` 段后**只**拼到 reply_failure 路径。dice 在 line 139 已经拿到 `at`，但 line 363 又把**原始** `user_id` 字符串单独传给 helper —— 与 line 139 拿到的 `at` 段 deduplicate 不严格，没复用同一防御。
- 若 `event.get_user_id()` 返回非数字字符串（V11-shim / Telegram bridge 接进来的 string ID），line 139 的 `safe_at_segment_or_empty` 会退化为空 text 段（OK），但 line 363 传给 helper 后 V11 路径 `OBV11MessageSegment.at(non_numeric)` 会异常（见 H-2），非 V11 路径会原样 prepend（见 H-1）。
**Fix sketch**: helper 接收 `at_segment: OBV11MessageSegment | None` 而非 `at_user_id: str | None`，把"如何拿到 at 段"的责任留给 caller（已有 `safe_at_segment_or_empty`）。或让 helper 内部统一走 `safe_at_segment`。
**Risk if unfixed**: Medium — 与 H-1 / H-2 同链；helper 是共享 surface。

---

### M-4 `at_user_id` 仅 dice 一处使用，参数语义 / 命名 PRD 与其它共享路径未对齐

**File**: `nextbot/screenshot_render.py:43, 59-62, 79, 84, 97, 144, 165-166`
**Dimension**: consistency / scope
**Issue**: `at_user_id` 是新增的可选参数；通读全 plugin 仅 `dice.py:363` 使用。docstring（line 59-62）注释了语义，但其它 plugin（leaderboard / lottery 等同样应该 @ 玩家）尚未跟进。helper 加了新 capability 但调用方未对称使用 —— 长期会出现 "为什么只有 dice 截图带 @，其它不带" 的产品一致性问题。**不是 bug**，但是 scope-out backlog。
**Fix sketch**: 单独 task：评估 leaderboard / lottery_result / red_packet 等结果型截图是否应该一律 @ 玩家；统一启用或一律不启用。
**Risk if unfixed**: Low — UX 不一致，无功能影响。

---

### M-5 `dice.py:316-327` `result_kind` 五分支判定与模板 5 个 preset 一致，但 `triple_kill` 与 `lose` 在 `choice="豹子"` 下永远不会路过 `net`

**File**: `nextbot/plugins/dice.py:316-327`
**Dimension**: correctness
**Issue**: 当 `choice="豹子" and is_triple` → `triple_win`（payout > 0, net > 0）；当 `choice="豹子" and not is_triple` → `lose`（payout = 0, net = -cost）。两个分支在 line 316 / 318 都先级被处理，**正确**。但 line 318 处的 "猜豹子未中" 分支文案在模板（`RESULT_PRESETS.lose.text = "猜错了"`, line 397）显示，与 "猜大/小未中" 复用同一文案。猜豹子失败 vs 猜大/小失败的语义差异（猜豹子的赔率 10× 是高风险，失败 fallback 文案应更鲜明）—— 文案 UX 问题，不是算法 bug。
**Fix sketch**: 新增 `RESULT_PRESETS.triple_lose = "豹子未中"` 等专属文案；plugin 端 line 318 改为 `result_kind="triple_lose"`，模板 / `_VALID_RESULT_KINDS` 同步加白名单项。
**Risk if unfixed**: Low — UX 微差。

---

### M-6 `dice.py:215-218` 选豹子时**完全绕过** win_rate 控制，所有命中率 / 平衡参数互动模型不一致

**File**: `nextbot/plugins/dice.py:215-218`
**Dimension**: algorithm / fairness
**Issue**: 注释明确说 "豹子保留自然概率" → 自然概率 `6/216 = 2.78%`，赔率 `triple_multiplier=10×` → 期望回报 `2.78% × 10 = 0.278 < 1`，**对玩家不利**（house edge ≈ 72%）。这是设计选择，没问题。

但 prior-art "win_rate" 是**单一玩法控制器**：选大/小可以被运营调成必胜或必败，选豹子不行。运营如果想用 win_rate 同步调控豹子，必须改代码（或新增 `triple_win_rate` 参数）。PRD 列入 "选豹子时仍走自然 random 是否绕过其它命令的 cap / 限流模式" 项 —— 答案：**绕过了 win_rate**，但 cap / 限流（cooldown, min/max_cost, MAX_COINS_AMOUNT）均**保留**，无安全漏洞。
**Fix sketch**: (a) 现状可保留，注释里更显眼；(b) 加 `triple_win_rate` 参数（默认 nil = 自然概率），运营可选择性介入。
**Risk if unfixed**: Low — 设计选择，玩家不可利用。

---

### L-1 `_safe_param_int` 不支持 `max=` 参数，caller 必须手动 clamp

**File**: `nextbot/plugins/dice.py:61-66, 220`
**Dimension**: api-design
**Issue**: `_safe_param_int(key, default, min_value=0)` 只有 `min_value`，无 `max_value`。`win_rate` 在 schema 里声明 `min: 0, max: 100`（line 131-132），但 `command_config._validate_by_schema`（`command_config.py:204-238`）的 `min/max` 约束**仅在 WebUI 写库前**校验；运行时读出的 `param_values` 理论上必须在范围内。

dice 显式补丁：`win_rate_pct = max(0, min(100, _safe_param_int("win_rate", 50, min_value=0)))`（line 220）—— 是 defense-in-depth，**正确**。但其它参数（`min_cost`、`max_cost`、`cooldown_seconds`、`big_multiplier` 等）只用了 `max(min, ...)` 兜底**无上限**，意味着运营误配 `triple_multiplier=999999999` 会被照搬。`MAX_COINS_AMOUNT` 在 add_coins 路径上还能兜住（line 252 `add_coins_with_cap` 不会超 cap），但 `cost * multiplier` 中间计算可能溢出（Python int 无溢出，但 `final_coins` 仍 `≤ MAX_COINS_AMOUNT`，OK）。
**Fix sketch**: `_safe_param_int` 加 `max_value: int | None = None` 参数；plugin 用法对齐：每个数值参数都传 schema 中的 max。或重构 `get_current_param` → `get_current_int_param(key, default, min, max)` helper 入项目共享。
**Risk if unfixed**: Low — schema 已在 WebUI 端把关；当前实现是 defense-in-depth 缺一环但无利用面。

---

### L-2 `_cooldown_map: dict[str, datetime]` 无 size 上限 / 不过期清理

**File**: `nextbot/plugins/dice.py:27, 309`
**Dimension**: perf / memory
**Issue**: 每次成功掷骰子 `_cooldown_map[user_id] = now`（line 309），module-level dict 永远只增不减。若 bot 长期运行 + 海量不同 user_id（如开放群万人玩家），dict 增长无界。每个 entry `~100 字节`（key str + datetime）—— 10 万玩家 ≈ 10 MB，可接受；100 万玩家 ≈ 100 MB，开始有压力。**真正的边界**：恶意者用脚本伪造 `user_id` 灌入 → 内存放大。但 V11 路径 user_id 是 QQ 号空间（≤ 数十亿），且 `command_control` 在更上游会拦未注册命令。
**Fix sketch**: 走 LRU 缓存或定期 sweep（如：每次写入时若 dict 超阈值 / 触发 mod-N 检查，清掉所有 `now - v > cooldown_seconds` 的 entry）。`server/page_store.py:25-34` 同款 sweep 模式可参考。
**Risk if unfixed**: Low — 长期运行 + 大量唯一 user_id 才会浮现。

---

### L-3 `dice_page.py:54-56` `dice_list` pad-with-1 与 `_clamp_die(value=0)` 同样兜底为 1，但 0 显示为 1 不直观

**File**: `server/pages/dice_page.py:25-34, 54-56`
**Dimension**: robustness / debugging
**Issue**: `_clamp_die(0)` → 因 `n < 1` 返回 1（line 30）。如果 plugin 端 bug 传 `(0, 0, 0)` 或 `(7, 7, 7)`，渲染层会**静默** clamp 到 `(1, 1, 1)` / `(6, 6, 6)`。玩家看到正常骰面但实际是 bug；无日志告警。
**Fix sketch**: `_clamp_die` 内 `if n < 1 or n > 6: logger.warning(...)`; 或 raise；或保留 clamp 但加 warning。
**Risk if unfixed**: Low — 仅调试可观测性问题；plugin 端 `random.randint(1, 6)` / `random.choice(_ALL_DICE_COMBOS)` 不会越界。

---

### L-4 `dice_page.render()` 第二次 `_clamp_die` 重复（build_payload 已 clamp）

**File**: `server/pages/dice_page.py:89`
**Dimension**: code-quality
**Issue**: `render(payload)` 内 `[_clamp_die(d) for d in (payload.get("dice") or [1, 1, 1])[:3]]`（line 89）；但 `payload` 必然来自 `build_payload`（`web_server.create_dice_page` 走 `dice_page.build_payload(...)` → `create_page` → `get_page` → `render`），其中 `dice` 已 normalize 过。重复 clamp 是 defense-in-depth，不是 bug。**注意** payload 可能从 `server/page_store.py` 经 LRU 取回 —— 若 attacker 篡改了 store（需 loopback 内网 + bot 进程权限），重复 clamp 是兜底。OK，保留。
**Fix sketch**: 无需修。可考虑加注释说明 "payload 来自 page_store，理论可信但本函数为安全做 defense-in-depth"。
**Risk if unfixed**: None — 当前是正确的 defensive 设计。

---

### L-5 `dice.html` 中 `appliedNet` / `payout` / `cost` 等数值 fallback 用 `Number(... || 0)` 而非 `Number.isFinite` 校验

**File**: `server/templates/dice.html:294-305`
**Dimension**: robustness
**Issue**: `const cost = Number(data.cost || 0);` 这种写法在 `data.cost` 为 `"abc"` 时 `Number("abc") = NaN`，下游 `${cost}` 显示为 `"NaN"`，模板会出现 "− NaN" 文案。`build_payload` / `render` 已强转 `int(...)`，正常情况下不可能传非数字，但 `JSON.parse` 若数据被注入篡改（理论上 attacker 需先攻破 page_store，loopback only），可能传字符串。
**Fix sketch**: `const cost = Number.isFinite(Number(data.cost)) ? Number(data.cost) : 0;`。或：信任 payload，省略。
**Risk if unfixed**: Low — 内层防御冗余；page_store 是 internal memory，render 路由是 loopback only（`render.py:30-34` `_ensure_loopback`）。

---

### L-6 `dice.html:264, 366-374` 三个 dice-face div 每次都重新 createElement，未复用

**File**: `server/templates/dice.html:264, 366-374`
**Dimension**: perf
**Issue**: `for (let i = 0; i < 3; i++) { ... diceRowEl.appendChild(face); }` —— `diceRowEl` 初始为空（line 264 `<div class="dice-row" id="dice-row"></div>`），首次渲染 fine。但如果模板被复用（hot-swap payload 重渲染场景，目前不存在），需要先 clear children。**当前无此场景**，Info。
**Fix sketch**: 无需修；如未来加入动态刷新，需 `diceRowEl.replaceChildren()` 后再 append。
**Risk if unfixed**: None — 未来设计风险，非当前 bug。

---

### L-7 `dice.py:296-304` 异常路径有 `bot.send` 嵌套 try，可能吞掉网络异常 root cause

**File**: `nextbot/plugins/dice.py:296-307`
**Dimension**: observability
**Issue**: 主路径 `except Exception:` → `session.rollback()` + `logger.exception(...)` → `bot.send(reply_failure(...))`。`bot.send` 自身可能抛（适配器断连 / 协议异常）→ 内层 `except Exception: pass` 静默吞掉。**这是合理的兜底**（避免错误回复路径再次抛打断 finally），但**没有 log** —— 若适配器长期挂掉，玩家看不到反馈也没日志。
**Fix sketch**: `except Exception: logger.warning("...回复失败")`。对齐 `screenshot_render._render_and_send_inner` 中失败路径都有 `logger.warning`。
**Risk if unfixed**: Low — 仅调试可观测性。

---

### L-8 dice.py 缺少 "成功发送截图" 路径日志

**File**: `nextbot/plugins/dice.py:351-364`
**Dimension**: observability
**Issue**: line 329-333 有 `logger.info(f"掷骰子结果：...")` 在调用截图前，但**没有**截图发送成功 / 失败的日志。`render_and_send_screenshot` 返回 `True/False`（screenshot_render.py:75/82），调用方丢弃了返回值。Cooldown 在 line 309 已经写入 —— 即使截图发送失败（玩家看到 reply_failure 兜底）冷却仍生效。**这是合理的**（避免重试 spam），但失败时没有 plugin 层日志可追溯。
**Fix sketch**:
```python
ok = await render_and_send_screenshot(...)
if not ok:
    logger.warning(f"掷骰子截图发送失败：user_id={user_id} dice={d1},{d2},{d3}")
```
**Risk if unfixed**: Low — 内部 helper 已 log 失败 reason；plugin 层信息冗余。

---

### L-9 `dice_page.py:78` `generated_at = beijing_now_text()` 在 build_payload 内调用 → 缓存有效期跨越时区？

**File**: `server/pages/dice_page.py:78`
**Dimension**: correctness / time
**Issue**: `build_payload` 在 plugin 端（dice.py:335）同步调用 → token 写入 page_store → render 时取出。token 在 `page_store.py:11` `PAGE_EXPIRE_SECONDS = 600` 内可被多次重渲染（理论上 playwright 截图只渲染一次，但 cache 命中可能更晚）。`generated_at` 取 build 时刻，**不是 render 时刻** —— 玩家收到截图时显示的时间和截图实际拍摄时间最多差 10 分钟。**实际**：playwright 一般 <1s 内完成，不会出现 10 分钟差。OK，但语义上可能误导。
**Fix sketch**: 文档说明 "generated_at = 命令处理完成时刻，非截图时刻"；或在 render 时取 now。
**Risk if unfixed**: Info — 玩家不会感知。

---

### L-10 模板对 `player_name` 长度无上限（极长名字破排版）

**File**: `server/templates/dice.html:232 + 308`
**Dimension**: ux / robustness
**Issue**: `data.player_name` 直接 `textContent`（line 308），CSS 有 `.stat-value { overflow-wrap: break-word; word-break: break-all; }`（line 105-106）—— 但只对 stats tile 内的值生效。header-meta 的 `#meta-player-name`（line 232）无此规则，极长名字会撑破 flex 行（`.header-meta { gap; flex-wrap: wrap; }`，line 56-59，flex-wrap 是有，但单 token 不会 wrap）。`build_payload` `str(player_name).strip()` 无 length cap，DB `User.name` 列若允许 200 字符就会过 200 字符。
**Fix sketch**: build_payload 加 length cap `[:32]`，或 CSS `.meta-value { max-width: 200px; overflow: hidden; text-overflow: ellipsis; }`。
**Risk if unfixed**: Low — 玩家恶意改名长度受 register flow 限制；视觉问题非安全。

---

### I-1 `dice.html` 的 SVG dot-position grid 索引 0..4 用得只到 [1,3] 的偶/对角，0 和 4 未使用

**File**: `server/templates/dice.html:335-343`
**Dimension**: code-quality
**Issue**: `DOT_POSITIONS` 中 grid 是 5×5（索引 0..4），但实际使用的坐标只有 `{1, 2, 3}`（line 339 `4: [[1,1], [3,1], [1,3], [3,3]]`）。0 和 4 的格子永远空。`FACE_SIZE = 90` / `CELL = 18`，dot 中心 = `gx * 18 + 9 = 27 / 45 / 63`。**视觉正确**（5×5 中央 3 格），但 grid 命名为 5×5 让 reader 误以为有 5 列点。
**Fix sketch**: 注释里说明 "5×5 grid 仅用中间 3×3 用于 dot 中心定位，外圈作 padding"。
**Risk if unfixed**: None — 仅可读性。

---

### I-2 `_VALID_RESULT_KINDS` 字面量未与 `dice.py` 中的 `result_kind` 五分支共享常量

**File**: `server/pages/dice_page.py:12, nextbot/plugins/dice.py:316-327`
**Dimension**: consistency / spec
**Issue**: `_VALID_RESULT_KINDS = {"win", "lose", "triple_win", "triple_kill", "tie"}` 与 `dice.py` 的判定字面量是两份独立的常量声明。如果未来加 `triple_lose`（见 M-5）需在两处同步。模板里 `RESULT_PRESETS` 是第三份。
**Fix sketch**: 抽到 `nextbot/dice_types.py` 或 `server/pages/dice_page.py` 顶级导出，plugin 端 import 使用；模板侧通过 JS 不能直接 import，但可作为 "spec 注释" 注明。
**Risk if unfixed**: Info — 单点维护风险。

---

### I-3 `dice.py:152-153 / 155-157` 两段几乎重复的 reply_failure（"投入金币必须为正整数"）

**File**: `nextbot/plugins/dice.py:151-157`
**Dimension**: code-quality
**Issue**: ValueError 分支与 `cost <= 0` 分支文案相同；可合并：`try: cost = int(args[1]); assert cost > 0; except: ...`。
**Fix sketch**: 简化为 `try: cost = int(args[1]); except: cost = 0; if cost <= 0: reply_failure(...)`.
**Risk if unfixed**: Info — 微小重复。

---

### I-4 `dice.html:412-417` cap-warning 文案直接拼 `payout - appliedPayout`

**File**: `server/templates/dice.html:412-417`
**Dimension**: copy / correctness
**Issue**: `capEl.textContent = \`⚠️ 已触账户上限，理论派奖 ${payout}，${missing} 金币未入账\`;` —— 文案正确，但 `payout - appliedPayout` 在 JS 端用 `Number` 转换（payout / appliedPayout 在 line 300-301 已 `Number(... || 0)`），若 payload 篡改导致 `payout < appliedPayout`（理论不可能，因 cap 只会减少）则 missing 为负数：显示 "−1 金币未入账"。`build_payload` 已 `int()` 强转两个字段，且 plugin 层 `applied_payout, capped = add_coins_with_cap(...)`（economy.py:70）保证 `applied_payout <= payout`。**当前安全**。
**Fix sketch**: 加 `const missing = Math.max(0, payout - appliedPayout);` 防御。
**Risk if unfixed**: None — 当前数据流闭合。

---

## 4. Cross-module scope-out backlog（不在本 6 文件内修）

- **B-1**: prior-art `render_pages.md` High-1 (全 17 模板未做 mtime cache) —— dice_page 已自带，其它 16 个 page 模块需对齐。
- **B-2**: prior-art `render_templates.md` Medium S3 (placeholder fallthrough fallback) —— dice.html 已加 try/catch，其它 16 个模板需对齐。
- **B-3**: prior-art `render_templates.md` Medium U1 (`[hidden]` guard) —— dice.html 已加，其它 9 个未修模板需对齐。
- **B-4**: M-4 `at_user_id` 仅 dice 使用，未在 leaderboard / lottery_result / red_packet 等同类截图复用 —— 单独决策 task。
- **B-5**: `screenshot_render.py` 是 cross-plugin shared helper，H-1 / H-2 影响所有调用方；建议在 helper 内统一 sanitize 入口，而非每个 plugin 自己防御。

---

## 5. 文件 / 行 速查表

| Severity | Finding | 文件 | 行号 |
|---|---|---|---|
| High | H-1 fallback raw at_user_id prepend | screenshot_render.py | 165-166 |
| High | H-2 V11 at(at_user_id) 无 int 防御 | screenshot_render.py | 144-148 |
| Medium | M-1 dice Semaphore(4) vs 项目 (2) | dice.py | 58 |
| Medium | M-2 _template_cache 无锁 | dice_page.py | 14-22 |
| Medium | M-3 player_qq / at_user_id 共用 raw user_id | dice.py | 174, 337, 363 |
| Medium | M-4 at_user_id 仅 dice 使用，一致性 | screenshot_render.py | 43-62 |
| Medium | M-5 triple_lose 文案与 lose 复用 | dice.py | 318 / dice.html | 397 |
| Medium | M-6 选豹子绕过 win_rate 控制 | dice.py | 215-218 |
| Low | L-1 _safe_param_int 无 max= | dice.py | 61-66 |
| Low | L-2 _cooldown_map 无上限 | dice.py | 27, 309 |
| Low | L-3 _clamp_die 静默 clamp 无 warning | dice_page.py | 25-34 |
| Low | L-4 render 二次 clamp 冗余（defensive 可保留） | dice_page.py | 89 |
| Low | L-5 模板 Number(... || 0) NaN 风险 | dice.html | 294-305 |
| Low | L-6 dice-face 不复用 createElement | dice.html | 366-374 |
| Low | L-7 reply_failure 嵌套 try 吞日志 | dice.py | 301-304 |
| Low | L-8 截图发送结果未在 plugin 层 log | dice.py | 351-364 |
| Low | L-9 generated_at 是 build 时刻非 render 时刻 | dice_page.py | 78 |
| Low | L-10 player_name 无 length cap | dice_page.py | 64 / dice.html | 232 |
| Info | I-1 SVG 5×5 grid 外圈不使用 | dice.html | 335-343 |
| Info | I-2 result_kind 常量 3 处重复声明 | dice_page.py | 12 / dice.py | 316 / dice.html | 393 |
| Info | I-3 ValueError + cost<=0 重复文案 | dice.py | 151-157 |
| Info | I-4 cap-warning missing 可负数 | dice.html | 412-417 |

---

## 6. Caveats / Not Found

- 未对全 `nextbot/text_utils.py` 做改动审计，只对 `safe_at_segment` / `safe_at_segment_or_empty` 做行为对照。
- 未对 `command_config._validate_by_schema` 的运行时绕过路径（如 DB 行被外部进程修改）做攻防分析；本审计假设 `param_values` 内容来自 WebUI 已通过的写入。
- 未对 dice cmd 在 group permission / 黑名单层的拦截做交叉验证（`require_permission("economy.dice")` 是项目通用闸门，认为可信）。
- 未做 playwright 截图实际性能基准；M-1 的 `Semaphore(4)` 影响判断基于 prior-art 推论。
- Win/loss 算法的统计学分布（如长期 1000 轮玩家是否真获得 `win_rate%` 中奖率），未做蒙特卡洛验证；纯数学校验已通过。
- 未审 `dice.html` 的 `--color-*` / `--type-*` / `--space-*` token 是否在 `render-tokens.css` 中全部声明（属于 cross-module，prior-art `render_templates.md` U6 已确认全项目 token 一致）。
- 未对 `screenshot_render._render_and_send_inner` 的 `temp_screenshot_path` async context（line 99）做并发 dist 验证，假设已审计过。
