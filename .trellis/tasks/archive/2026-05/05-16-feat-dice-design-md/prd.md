# feat(dice): 掷骰子结果改为图片渲染（参考 DESIGN.md 风格）

## Goal

`掷骰子` 命令当前回文字消息（多行 emoji + 数字）。改为渲染图片返回，与 lottery_result / red_packet / inventory 等命令保持视觉一致；图片风格参照 `DESIGN.md`：cream canvas + coral accent + Garamond serif display + 编辑器感卡片。

## Scope

### 新建（2 文件）
- `server/pages/dice_page.py` — `build_payload(...)` + `render(payload) → bytes`
- `server/templates/dice.html` — HTML 模板，`__DICE_DATA_JSON__` 占位符

### 修改（2 文件）
- `server/web_server.py` — `import dice_page` + 新增 `create_dice_page(...)` helper
- `nextbot/plugins/dice.py` — 把所有 **成功路径** 的 `bot.send(text)` 改为渲染图片返回；失败路径（参数错误 / 金币不足 / 冷却 / 注册等）保持文字 `reply_failure`（与其它截图命令一致）

## 设计（参照 DESIGN.md）

### 色彩 token
- canvas `#faf9f5` / surface-card `#efe9de` / surface-cream-strong `#e8e0d2` / hairline `#e6dfd8`
- coral primary `#cc785c` / coral-active `#a9583e`
- ink `#141413` / body `#3d3d3a` / muted `#6c6a64` / muted-soft `#8e8b82`
- success `#5db872` / warning `#d4a017` / error `#c64545`
- accent-amber `#e8a55a` 用于"豹子"高亮

### 排版
- display: `'Tiempos Headline', 'Cormorant Garamond', 'EB Garamond', Georgia, serif`，weight 400，letter-spacing -0.3px ~ -0.5px
- body: `'Inter', -apple-system, system-ui, 'Segoe UI', sans-serif`，weight 400 / 500
- mono: `'JetBrains Mono', 'SF Mono', 'Cascadia Mono', monospace`（用于骰子数字、金币数字）

### 卡片尺寸
- 单卡片居中，宽 640px，圆角 12px（`rounded-lg`），padding 32px（`spacing-xl`）
- 三层结构：header（玩家/选择/投入）→ dice center（3 骰子面 + 求和带）→ result band（净赚 / 实际获得 / 当前金币）

### Header 区
```
🎲 掷骰子                              [generated_at]
─────────────────────────────────────────────────────
玩家：小明 · QQ 10001                   投入：100 💰
选择：大
```
- 标题用 display（serif），右上 generated_at 用 muted 小字 mono
- 玩家 / 选择 / 投入 是 label + value 两列

### Dice center
- 3 个 SVG 骰子面，每个 100×100px，cream-strong 背景 `#e8e0d2`，圆角 12px
- 骰子点 = 深 ink `#141413`，圆形 dots，5×5 内部网格定位（1/2/3/4/5/6 标准布局）
- 三个并排，间距 24px
- 下方一行求和：`1 + 5 + 3 = 9` (mono large size 28px) + label 标签（"大" / "小" / "豹子"）display serif 28px

### Result band（占满底部宽度，圆角 12px，padding 24px）

**4 种状态：**

| 状态 | 背景 | 文案前缀 | emoji |
|---|---|---|---|
| 猜对 (`net > 0`) | coral `#cc785c` + text on-primary `#ffffff` | "猜对了！" | 🎉 |
| 豹子命中（猜豹子且 is_triple） | coral + amber 边框点缀 | "豹子！" | 🔥 |
| 豹子通杀（猜大/小但 is_triple） | surface-cream-strong（无 coral） | "豹子通杀，全部损失" | 💥 |
| 猜错 (`net < 0`) | surface-cream-strong (`#e8e0d2`) | "猜错了" | ❌ |
| 平局 (`net == 0`) | surface-cream-strong | "刚好持平" | ⚖️ |

band 内三列布局：
```
[投入]      [实际获得]    [净赚]
 100 💰      200 💰        +100
```
- 数字 mono 大字（28px）
- 标签 caption-uppercase（11px / +1.5px letter-spacing / muted）
- 净赚正数显 coral 加号、负数显 ink 减号

### Footer
- 当前金币：`💰 1234`（mono，body-strong）
- 触顶警告（如有）：amber 小行 `⚠️ 已触账户上限，<reason>`
- 极小字 generated_at + 项目水印（"NextBot"）— muted-soft

### 整体氛围
- 不用阴影（per DESIGN.md "color-block first, shadow rare"）
- 不用边框线，用 cream 不同色阶分层（canvas → card → cream-strong）
- coral 出现 ≤ 2 处（result band + 净赚正数）

## payload schema（dice_page.build_payload）

```python
{
    "player_name": str,
    "player_qq": str,
    "choice": str,           # "大" / "小" / "豹子"
    "cost": int,
    "dice": [int, int, int], # 1-6 each
    "total": int,            # sum
    "is_triple": bool,
    "result_kind": str,      # "win" / "lose" / "triple_win" / "triple_kill" / "tie"
    "payout": int,           # 理论派奖
    "applied_payout": int,   # 实际入账
    "net": int,
    "applied_net": int,
    "final_coins": int,
    "capped": bool,
    "generated_at": str,     # beijing_now_text()
}
```

## Plugin 改动（`nextbot/plugins/dice.py`）

1. 顶部 import：
   ```python
   from nextbot.screenshot_render import render_and_send_screenshot
   from server.screenshot import ScreenshotOptions
   from server.web_server import create_dice_page
   import asyncio
   ```
2. 模块级 semaphore：`_dice_semaphore = asyncio.Semaphore(4)` + 注册到 `register_server_semaphore_pool`（不一定需要，看 large_image 用法；可仅在 module scope 用）
3. 成功路径替换：删除 lines ~259-294 整段文字组装（`lines` + `reply_block` + `bot.send`），改为：
   ```python
   page_url = create_dice_page(
       player_name=user.name,
       player_qq=user_id,
       choice=choice,
       cost=cost,
       dice=(d1, d2, d3),
       total=total,
       is_triple=is_triple,
       result_kind=<分类>,
       payout=payout,
       applied_payout=applied_payout,
       net=net,
       applied_net=applied_net,
       final_coins=final_coins,
       capped=capped,
   )
   await render_and_send_screenshot(
       bot, event,
       page_url=page_url,
       options=ScreenshotOptions(viewport_width=720, viewport_height=720, fit_content_height=True),
       file_prefix="dice",
       semaphore=_dice_semaphore,
       failure_action="掷骰子",
   )
   ```
4. 失败 / 校验路径（参数 / 冷却 / 金币不足 / 注册）**保留**原 `reply_failure` 文字回复，与 lottery_result 等图片命令模式一致

## Acceptance

- `python3 -m py_compile` 4 个改动 / 新建文件全过
- `node --check`：HTML 在 playwright 渲染下 console 无 JSON.parse 错误
- 命令测试：
  - "掷骰子 大 100" 渲染图片 + 含 3 骰面 + 数字总和 + 选择 / 投入 / 净赚 / 当前金币
  - "掷骰子 豹子 100" 中豹子 → coral band 显示 "豹子！"
  - "掷骰子 大 100" 但摇到 6/6/6 → 显示 "豹子通杀"
  - 失败 / 冷却 / 金币不足 仍返回文字（不要为这些路径渲染图片）
- 触顶 cap 警告在图片上可见
- 与 lottery_result / red_packet 等图片视觉一致（cream canvas / 卡片）
- 模板缓存 + JSON safe `</` 转义 + `MAX_ENTRIES` 兜底（实际 dice 不需 entries cap，但 fallthrough fallback 仍需）

## DO NOT

- 不改 `掷骰子` 业务逻辑 / 概率 / 派奖 / 冷却 / cap 行为
- 不改命令名 / 参数定义
- 不改 stats 字段
- 不动其它 plugin / WebUI / 截图基础设施
- 不 commit

## Out of Scope

- 主题切换 / 暗色模式（截图固定 cream）
- 动画 / GIF（playwright 截图为静态 PNG）
- 玩家头像（dice 不展示头像，仅 QQ 号）
- 历史趋势 / 累计胜率（可单独 task）

## Technical Notes

- 参考 page 模式：`server/pages/red_packet_own_page.py` 含 `_template_cache`（audit 已下沉的 mtime 缓存）
- 截图入口：`nextbot/screenshot_render.py:render_and_send_screenshot`
- `ScreenshotOptions` 在 `server/screenshot.py`
- `beijing_now_text` 在 `nextbot/time_utils.py`
- 模板占位符规范：`__<NAME>_DATA_JSON__` 单一占位符在 `<script type="application/json">` 内，JS 端 try/catch JSON.parse fallback（audit M-S3 已规定）
- 模板内 `[hidden] { display: none !important; }` 守卫加上（audit U1 规定）
- HTML 平衡 + Chinese punctuation OK
