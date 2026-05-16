# feat(rob): 抢劫结果改为图片渲染 + 警察改地牢守卫

## Goal

1. `抢劫` 命令成功路径（5 种 result_type）由文字 `bot.send(at + " " + msg)` 改为渲染图片（与 dice / guess_number 同模式：text-hero on cream canvas）
2. 项目内所有"警察"文案改为"地牢守卫"（Terraria 主题更贴切）

## Scope

### 新建 (2 文件)
- `server/pages/rob_page.py` — `build_payload` + `render` + `_template_cache` + `threading.Lock`
- `server/templates/rob.html` — text-hero 模板（参照 dice.html / guess_number.html 同结构）

### 修改 (5 文件)
- `server/web_server.py` — `import rob_page` + `create_rob_page(...)` helper
- `server/routes/render.py` — import + `@router.get("/render/rob/{token}")` 路由
- `nextbot/plugins/rob.py` — 成功路径（最末 `bot.send(event, at + " " + reply_text)` line 519）替换为 `create_rob_page` + `render_and_send_screenshot(at_user_id=robber_id)`；失败路径保留文字 reply_failure
- `nextbot/plugins/rob.py` — 4 处"警察"标签 / 描述 / 消息 → "地牢守卫"（**仅改文案，不改 param key `police_rate` / `police_penalty_percent`**，避免破坏既有命令配置）
- `nextbot/plugins/tutorial_data.py` — 教程文案里 3 处"警察" → "地牢守卫"

## 5 个 result_kind（对应 rob 现有 5 路径）

| result_kind | emoji | 业务名 | 场景 |
|---|---|---|---|
| `crit` | 🔥 | 大成功 | 抢走目标 5-10% 金币 × 2 |
| `success` | ✅ | 抢劫成功 | 抢走目标 5-10% 金币 |
| `counter` | 🚫 | 反被抢 | 损失自己 10% 金币给目标 |
| `police` | 🚨 | 地牢守卫介入 | 损失自己 20% 金币（凭空消失） |
| `fail` | ❌ | 失败 | 损失自己 10% 金币（凭空消失） |

## payload schema（rob_page.build_payload）

```python
{
    "robber_name": str,
    "robber_qq": str,
    "victim_name": str,
    "victim_qq": str,
    "result_kind": str,        # crit / success / counter / police / fail
    "result_label": str,       # 中文 大成功 / 抢劫成功 / 反被抢 / 地牢守卫介入 / 失败
    "amount": int,             # 理论金额
    "applied_amount": int,     # 实际入账 (crit/success/counter 用 add_coins_with_cap 受 cap；police/fail 无 helper)
    "capped": bool,
    "cap_subject": str,        # "robber" / "victim" / "none"（用于触顶警示文案）
    "robber_final_coins": int,
    "generated_at": str,
}
```

## 设计（参照 dice / guess_number 同结构）

- canvas cream + `.page` flex column max-width 920px
- **Header**：`.header-rule` coral + `.header-eyebrow` "小游戏系统" + `.header-title` serif "抢劫" + `.header-meta`（抢劫者 · QQ · 目标 · QQ · generated_at）
- **stats-tiles** 4 列：理论金额 / 实际转移 / 抢劫者金币变化 / 当前金币
  - 抢劫者金币变化 = applied_amount * sign（crit/success +、其它 -）；用 `.gain` / `.loss` 切色
- **中央展示区** `.rob-display`（直接坐 canvas）：
  - 左侧"抢劫者"卡片（cream-strong 容器，QQ 头像区可暂用占位 / 名字+QQ）
  - 中间动态箭头 / 图标（按 result_kind 切方向 / 色）
  - 右侧"目标"卡片
- **大字 result label** `.rob-result` 5 状态配色：
  - `crit` → coral + amber 强调 "🔥 大成功"
  - `success` → coral "✅ 抢劫成功"
  - `counter` → muted-strong "🚫 反被抢"
  - `police` → muted-strong "🚨 地牢守卫介入"
  - `fail` → muted "❌ 失败"
- **cap-warning** amber 一行（hidden by default），根据 cap_subject 切文案
- **footer**："Powered by NextBot"

## Plugin 改动（`nextbot/plugins/rob.py`）

1. 顶部 import：
   ```python
   import asyncio
   from nextbot.screenshot_render import render_and_send_screenshot
   from server.screenshot import ScreenshotOptions
   from server.web_server import create_rob_page
   ```
2. `_rob_semaphore = asyncio.Semaphore(4)`（与 dice 一致）
3. 把 line 494-519（messages dict + reply_text 拼接 + bot.send）替换为：
   ```python
   # 抢劫者 / 目标名 cache（DB session 已 close，避免 detached ORM）
   # victim_name 已在 session 内取，robber_name 也需提前 cache
   result_kind = result_type  # crit / success / counter / police / fail
   result_labels = {
       "crit": "大成功",
       "success": "抢劫成功",
       "counter": "反被抢",
       "police": "地牢守卫介入",
       "fail": "失败",
   }
   cap_subject = "robber" if result_type in ("crit", "success") and capped else "victim" if result_type == "counter" and capped else "none"
   page_url = create_rob_page(
       robber_name=robber_name, robber_qq=robber_id,
       victim_name=victim_name, victim_qq=target_user_id,
       result_kind=result_kind, result_label=result_labels[result_kind],
       amount=amount, applied_amount=applied_amount,
       capped=capped, cap_subject=cap_subject,
       robber_final_coins=robber_final_coins,
   )
   ok = await render_and_send_screenshot(
       bot, event, page_url=page_url,
       options=ScreenshotOptions(viewport_width=720, viewport_height=720, fit_content_height=True),
       file_prefix="rob",
       semaphore=_rob_semaphore,
       failure_action="抢劫",
       at_user_id=robber_id,
   )
   if not ok:
       logger.warning(f"抢劫截图发送失败：robber={robber_id} victim={target_user_id} result={result_type}")
   ```
4. 在 session.close() 之前提前 cache `robber_name = str(robber.name)` + `robber_final_coins = int(robber.coins)`
5. 删除不再用的 emoji / reply_text 拼接代码

## 警察 → 地牢守卫 替换

### `nextbot/plugins/rob.py`

| 行 | 原 | 改 |
|---|---|---|
| 91 | `"label": "警察罚款百分比"` | `"label": "地牢守卫罚款百分比"` |
| 92 | `"description": "被警察抓获时罚款的金币百分比"` | `"description": "被地牢守卫抓获时罚款的金币百分比"` |
| 127 | `"label": "警察介入概率"` | `"label": "地牢守卫介入概率"` |
| 128 | `"description": "被警察抓获的概率（百分比）"` | `"description": "被地牢守卫抓获的概率（百分比）"` |
| 504 | `"police": f"🚨 你被巡逻的警察当场抓获..."` | 文案改"地牢守卫"；但本任务整段已替换为图片渲染，msg dict 在 rob_page 模板里实现，需保持 "🚨 地牢守卫" 文案 |

**关键**：param key `police_rate` / `police_penalty_percent` **不改**，避免破坏既有 WebUI 命令配置数据库行。

### `nextbot/plugins/tutorial_data.py`

3 处"警察" → "地牢守卫"：
- line 375 "或被警察罚款" → "或被地牢守卫罚款"
- line 413 "• 71-80（警察概率 10%） → 警察抓" → "• 71-80（地牢守卫概率 10%） → 地牢守卫抓"
- line 413 "🚨 警察介入" → "🚨 地牢守卫介入"
- line 413 "失败 / 反被抢 / 警察 加起来" → "失败 / 反被抢 / 地牢守卫 加起来"
- line 426 "🚨 你被巡逻的警察当场抓获" → "🚨 你被巡逻的地牢守卫当场抓获"

## Acceptance

- `python3 -m py_compile` 5 文件全过
- 命令测试：5 种 result_kind 都能渲染图片
- 失败 / 校验 / 冷却 / 金币不足 / 找不到目标 路径仍返回文字
- 触顶 cap_subject 切换 cap-warning 文案
- "警察" 在全项目（除 git history）零残留 grep
- param key `police_rate` / `police_penalty_percent` 不变（grep 验证）
- WebUI 命令配置打开抢劫命令仍能看到 label 已变 "地牢守卫"

## DO NOT

- 不改业务（概率 / 派奖 / cap / 冷却 / stats）
- 不改 param key `police_*`（仅改 label / description / msg）
- 不动其它 plugin（除 tutorial_data 文案）
- 不引外部 CDN
- 不动命令名 / 参数 schema 数量
- 不 commit

## Technical Notes

- 完整参照 dice / guess_number 已落地代码
- `at_user_id` 已由 `_sanitize_at_user_id` 净化
- `safe_at_segment_or_empty` 在 rob.py 已 import
- 模板占位符 `__ROB_DATA_JSON__`
- `_template_cache` + `threading.Lock` 与 dice 同形
- 5 状态 result band 配色 + cap-warning amber + footer 同 dice
