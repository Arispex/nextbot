# audit: 掷骰子近期更新（plugin + render + template + shared helper）

## Goal

最近 7 个 commit 围绕"掷骰子"做了改动，按用户指令做安全 / 性能 / 健壮性 / 边界 / 可优化空间审计。逐项发现 → 修复 → 再审计，直到 clean。

## Scope（in-scope files）

7 次 commit 涉及的代码面：

| 文件 | 关键改动 |
|------|---------|
| `nextbot/plugins/dice.py` | 整段图片渲染替换文字 + win_rate 算法 + 4 个预计算 set + at_user_id 参数 |
| `nextbot/screenshot_render.py` | 新增 `at_user_id` 公开 + 内部参数；V11 + fallback 路径处理 |
| `server/pages/dice_page.py` | 新建：`build_payload` + `render` + `_template_cache` |
| `server/templates/dice.html` | 新建 + 重排版（text-hero）+ mono total + 标题去"结果" |
| `server/web_server.py` | 新增 `create_dice_page(...)` |
| `server/routes/render.py` | 新增 `/render/dice/{token}` 路由 |

## 审计维度

### Security
- payload schema：user-controlled string (player_name, choice) 进 HTML 是否走 escape？JSON.parse 反序列化路径里有 XSS 风险？
- `at_user_id` 是否被 sanitize（不可控字符串拼接进 OneBot @ segment）
- `win_rate` 输入参数边界（int overflow / 负数 / None / 非数字）
- 预计算 set 是否被 mutate（tuple 是 immutable 好）
- `_safe_param_int` 对 `win_rate` 的 clamp 是否真有效
- 截图 page_url 是否带 token 泄漏 user_id（已知是 page_store 一次性 token）
- 模板渲染输出是否 escape `</`（避免 `</script>` 提前关闭）

### Performance
- 4 个 set 预计算（216 行 × 4 = 864 元素）— 是否每次 import 都跑？load-time 一次性 OK
- `random.choice(target_set)` — O(1)，OK
- 模板缓存 mtime 检查每次 render 都 stat — 该 OK
- semaphore=4 是否过小 / 过大（4 是 lottery 同形）
- screenshot_url payload size — `dice` payload 很小（~200 字节），OK
- 重复 import / circular 风险

### 算法正确性
- win_rate=0 真的永不中？win_rate=100 真的永中？
- `_safe_param_int("win_rate", 50, min_value=0)` 然后再 `min(100, ...)` — 双重 clamp 正确？
- `random.random() < win_rate` 边界（0.0 / 1.0）行为
- WIN_BIG / LOSE_BIG 互斥 + 覆盖全部 216？sanity 验证
- 选豹子时 `is_triple` 自然概率不变？
- win_rate=100 选大时，是否仍可能命中 triple_win（即 4/5/6 三连出现在 win_set？）— **重要**：是否在 WIN_BIG_SET 中混入了 (4,4,4), (5,5,5), (6,6,6)？这些是 triple ≥11，应当**不**在 WIN_BIG_SET 内（因 `not is_triple`）。需 grep / 单测验证。

### 渲染边界
- payload 字段缺失 / null 时 JS 端 fallback 是否安全
- `player_name` 含 HTML 特殊字符（`<>&"`）时模板的 textContent / innerHTML 安全性
- 极长 `player_name`（>100 char）渲染换行是否破版
- `dice = (0, 0, 0)` 或越界（如 `(7, 7, 7)`）时 SVG 渲染是否报错
- 5 种 result_kind 之外的值（如未来加 "jackpot"）传入 JS 时降级处理
- `capped=true` 时 `payout > applied_payout` 文案是否正确

### 跨模块 / 一致性
- 选豹子时仍走自然 random — 是否绕过其它命令的 SF-X.1 cap 模式（应当不变，cap 在 payout 阶段）
- `at_user_id` 在非 V11 fallback 路径 — head 文案 prepend `@<id>` 时，user_id 是数字字符串，安全
- 模板里 `[hidden]` 守卫存在
- `command_control` 参数新增了 `max: 100`，但 `_safe_param_int` 实现是否真的尊重 max？（如果 `_safe_param_int` 不支持 max，clamp 必须在 caller 手动做 — 检查这点）

### Copy / UX
- 错误文案是否仍用 `reply_failure`
- win_rate label 文案是否准确

## Process

1. **Phase A — Research**：派 `trellis-research` 全量扫上述 6 文件，按上述维度产 finding 报告。
2. **Phase B — Fix**：主代理评审 finding，对每条 High/Medium 派 `trellis-implement` 修复（或合并到一个修复任务）。
3. **Phase C — Re-audit**：派 `trellis-research` 重审刚修复的代码（focus 在改动周边新暴露面）。
4. **Phase D — Loop or close**：若 re-audit 仍有 Critical/High → 回 Phase B；若仅 Low / scope-out → 收口报告。

## Acceptance

- 每条 High / Medium 给出 commit 或 explicit skip 理由
- 最终 re-audit 0 High（Low / Info 可接受）
- 不破坏现有行为（cap / cooldown / payout / stats / 渲染一致）

## DO NOT

- 不动玩家 / 命令体验（不改 win_rate 默认 50）
- 不动豹子自然概率
- 不引外部依赖
- 不 commit 在审计阶段；修复阶段才 commit

## Out of Scope

- 旧的 dice 文字回复路径（已删）
- 跨模块（lottery / red_packet 等）也用 `render_and_send_screenshot` 的 caller — 仅审 dice 改动周边
- 命令文档 / spec 更新（修复完成后看是否需要）
