# feat(guess_number): 猜数字结果改为图片渲染（与 dice 同模式）

## Goal

`猜数字` 命令成功路径由多行文字（`reply_block` + 5 行 emoji + 数字）改为渲染图片返回，**与 dice 截图同模式**：cream canvas text-hero 风格，shell-less，5 状态 result band。失败 / 校验 / 冷却路径保留文字 reply_failure（与 dice 一致）。

## Scope

### 新建 (2 文件)
- `server/pages/guess_number_page.py` — `build_payload` + `render` + `_template_cache` + `threading.Lock`
- `server/templates/guess_number.html` — text-hero 模板（参考 `server/templates/dice.html` 直接同构）

### 修改 (3 文件)
- `server/web_server.py` — `import guess_number_page` + `create_guess_number_page(...)` helper
- `server/routes/render.py` — import + `@router.get("/render/guess_number/{token}")` 路由
- `nextbot/plugins/guess_number.py` — 成功路径替换为 `create_guess_number_page` + `render_and_send_screenshot(at_user_id=user_id)`；失败路径保留文字

## 5 个 result_kind（对应 dice.py 现有 5 等级派奖）

| result_kind | 触发条件 | 业务名 | 派奖 |
|---|---|---|---|
| `exact` | `diff == 0` | 命中 | `cost × exact_multiplier`（默认 10×）|
| `near` | `diff <= near_range` | 极近 | `cost × near_multiplier`（默认 5×）|
| `close` | `diff <= close_range` | 接近 | `cost × close_multiplier`（默认 2×）|
| `far` | `diff <= far_range` | 偏离 | `cost // 2` 返还 |
| `miss` | 其它 | 远离 | 0 全部损失 |

## payload schema（guess_number_page.build_payload）

```python
{
    "player_name": str,
    "player_qq": str,
    "range_max": int,        # 范围 1..N
    "guess": int,
    "answer": int,
    "diff": int,
    "cost": int,
    "result_kind": str,      # exact / near / close / far / miss
    "result_label": str,     # 中文 命中 / 极近 / 接近 / 偏离 / 远离
    "payout": int,           # 理论派奖
    "applied_payout": int,   # 实际入账
    "net": int,
    "applied_net": int,
    "final_coins": int,
    "capped": bool,
    "generated_at": str,
}
```

## 设计（参照 dice.html 同结构）

- canvas cream + `.page` flex column max-width 920px
- **Header**：`.header-rule` coral 64×4 + `.header-eyebrow` "小游戏系统" + `.header-title` serif "猜数字" + `.header-meta` 行内（玩家 · QQ · 范围 · 投入 · generated_at）
- **stats-tiles** 4 列：投入 / 实际获得 / 净赚 / 当前金币（net 用 .gain / .loss 切色）
- **中央展示区**：直接坐 canvas（不嵌大 card）
  - 左侧"你猜"卡片（cream-strong 容器 + 大字 mono 数字）；右侧"答案"卡片（同样形态）
  - 中间一个"≈ 差 N"小标签（mono）
  - 下方 `.guess-result` 5 状态文案与配色：
    - `exact` → coral 大字 "🎯 命中！"
    - `near` → coral 中亮 "✨ 极近"
    - `close` → 普通 "🎯 接近"
    - `far` → muted "↗ 偏离"
    - `miss` → muted-strong "❌ 远离"
- **cap-warning** amber 一行（hidden by default）
- **footer**："Powered by NextBot"

## Plugin 改动（`nextbot/plugins/guess_number.py`）

1. 顶部 import：
   ```python
   import asyncio
   from nextbot.screenshot_render import render_and_send_screenshot
   from server.screenshot import ScreenshotOptions
   from server.web_server import create_guess_number_page
   ```
2. 模块级 `_guess_semaphore = asyncio.Semaphore(4)`（与 dice 一致：单页轻量）
3. 把 line 294-317（`lines = [...]` + `reply_block` + `bot.send`）替换为：
   ```python
   result_kind = "exact" if diff == 0 else "near" if diff <= near_range else "close" if diff <= close_range else "far" if diff <= far_range else "miss"
   page_url = create_guess_number_page(
       player_name=user_name, player_qq=user_id,
       range_max=range_max, guess=guess, answer=answer, diff=diff, cost=cost,
       result_kind=result_kind, result_label=result_type,
       payout=payout, applied_payout=applied_payout,
       net=net, applied_net=applied_net,
       final_coins=final_coins, capped=capped,
   )
   ok = await render_and_send_screenshot(
       bot, event, page_url=page_url,
       options=ScreenshotOptions(viewport_width=720, viewport_height=720, fit_content_height=True),
       file_prefix="guess",
       semaphore=_guess_semaphore,
       failure_action="猜数字",
       at_user_id=user_id,
   )
   if not ok:
       logger.warning(f"猜数字截图发送失败：user_id={user_id} guess={guess} answer={answer}")
   ```
4. **user.name 提前 cache**：session 内 `user_name = str(user.name)` 存局部，避免 detached ORM
5. 删除原 `lines` 组装 + `reply_block` 调用 + 不再用的 `EMOJI_*` / `reply_block` import（grep 检查）

## Acceptance

- `python3 -m py_compile` 5 文件全过
- 命令测试：
  - "猜数字 50" 渲染图片含 你猜 / 答案 / 差 / 投入 / 实际获得 / 净赚 / 当前金币
  - 5 种 result_kind 都能渲染
  - capped=true 时显示 cap warning
  - 失败 / 校验 / 冷却 / 金币不足仍返回文字（不渲染图片）
  - V11 消息形态 `@玩家 [图片]` 一条
- 模板包含 `[hidden]` 守卫 + JSON.parse try/catch fallback + `__GUESS_NUMBER_DATA_JSON__` 占位
- threading.Lock 保护模板缓存
- 与 dice / lottery_result / red_packet 视觉一致

## DO NOT

- 不改 `猜数字` 业务逻辑 / 概率 / 派奖 / 冷却 / cap
- 不改命令参数 / stats 字段
- 不动其它 plugin / 截图基础设施
- 不引外部 CDN
- 不 commit

## Out of Scope

- 主题切换 / GIF 动画 / 头像
- 玩家累计胜率 / 历史趋势（可单独 task）
- 命令名 / 参数 schema 更改

## Technical Notes

- 完整参照 dice 已落地代码（commit b46189d + 9394880 + 7c596d9 + aba28e6 + 32e90a0）
- 参考 `server/pages/dice_page.py` 实现 `_template_cache` + `threading.Lock` + `_clamp_*` 防御 + `_VALID_RESULT_KINDS` 白名单
- 参考 `server/templates/dice.html` text-hero 结构 + 5 状态 result band CSS
- 模板占位符 `__GUESS_NUMBER_DATA_JSON__`；JSON safe `</` 转义已由 `dice_page.render` pattern 提供
- `at_user_id` 已由 `nextbot/screenshot_render.py:_sanitize_at_user_id` 净化
