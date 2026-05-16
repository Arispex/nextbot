# 签到 改图片渲染（DESIGN.md 风格 + frontend-design skill）

## Goal

把 `签到` 命令的**成功路径**回复从纯文本改为图片渲染，使用 DESIGN.md 的 warm-canvas editorial 视觉系统、与 dice / rob / guess_number 形成系列一致性，并通过 `frontend-design` skill 的"有意图、不 generic"原则添加一个签到独有的视觉钩子（连续打卡点链）。

## 现状

- `nextbot/plugins/economy.py:280+` `handle_sign` 成功路径输出 7 行纯文本（签到排名 / 基础奖励 / 连续天数 / 连续奖励 / 本次总获得 / 当前金币）
- 失败路径已有 `at + " " + reply_failure("签到", ...)` 文本回复 + @ 调用者
- 现有命令 dice / rob / guess_number 已用 DESIGN.md 风格做了图片渲染，模板在 `server/templates/*.html`，page 模块在 `server/pages/*.py`

## Design Spec

### 视觉骨架（与 dice / rob / guess_number 一致）
- DESIGN.md warm-canvas editorial：cream canvas + coral accent + Copernicus serif headlines + StyreneB sans body
- Header text-hero：coral rule + uppercase eyebrow（`经济系统`）+ display 标题（`签到`）+ meta line（玩家 / QQ / 时间）
- Footer `Powered by NextBot`
- `var(--font-display)` / `var(--font-body)` / `var(--font-code)` 已在 `render-fonts.css` / `render-tokens.css` 定义，直接用
- `body.fallback` opacity 0.5 兜底（JSON 解析失败时）

### 签到独有元素

| 元素 | 设计 |
|---|---|
| **核心数字** | `+ 30 金币` 巨大 numeric（display 级），teal 色（DESIGN.md `--color-accent-teal #5db8a6`），与 dice 的 gain 色一致 |
| **拆解小字** | `基础 20 · 连续 +10`（muted），让用户理解收益构成 |
| **连续打卡点链** | 30 个 SVG circle row：已签到亮 amber（`--color-accent-amber #e8a55a`）、未签到淡 hairline。当前连续段从右往左排清晰可见。streak > 30 天 → 只显示最近 30 个 + `+M 天` 标签。下方大字 `第 N 天`（display-md） |
| **stat-tiles** | **仅 3 个**（累计签到 / 当前金币 / 今日第 N 位），不为对称凑 4 |
| **配色规则** | coral=header rule；teal=金币入账正向数字；amber=streak 点链 + warning |

### 边界状态

| 状态 | 视觉 |
|---|---|
| **连续中断**（streak 重置为 1） | 点链上方 amber 横条 `连续中断，今日重新开始`；点链最右 1 点亮 |
| **streak 未开启** | 拆解小字改 `基础 20 · 连续奖励未开启`；点链仍显示已签到天数 |
| **cap warning**（金币触顶 partial cap） | 与 dice 同款，底部 amber 横条 `⚠️ 已触账户上限，理论奖励 X，Y 金币未入账` |
| **JSON 解析失败** | `body.fallback` opacity 0.5 |

## Scope

### 新增

| 文件 | 作用 |
|---|---|
| `server/templates/signin.html` | DOM + CSS + inline JS（SVG 渲染连续点链） |
| `server/pages/signin_page.py` | `create_signin_page(...)` 构造 page URL + JSON payload，仿 `dice_page.py` |

### 修改

| 文件 | 改动 |
|---|---|
| `server/web_server.py` | 注册 `/render/signin` 路由（仿 `/render/dice`），无中间件 / auth 改动 |
| `nextbot/plugins/economy.py` | `handle_sign` 成功路径改 `screenshot_url + bot.send(at + image)`；失败路径保持文本 + @；保留所有业务逻辑 |

### Payload schema

```json
{
  "player_name": "千亦",
  "player_qq": "123456",
  "today_order": 5,
  "base_reward": 20,
  "streak_reward": 10,
  "total_reward": 30,
  "current_streak": 7,
  "streak_enabled": true,
  "streak_broken": false,
  "max_streak_chain": 30,
  "coins_after": 1234,
  "sign_total": 87,
  "capped": false,
  "requested_reward": 30,
  "applied_reward": 30,
  "generated_at": "2026-05-16 14:33:08"
}
```

## Out of Scope

- 不改业务逻辑：streak / 金币入账 / 并发原子保护 / IntegrityError 兜底 / partial cap
- 不改失败路径（已有 reply_failure + @ 调用者）
- 不改 `nextbot/signin_reset.py` worker / `leaderboard.signin` 排行榜
- 不改 DESIGN.md / render-tokens.css / render-fonts.css
- 不改 rob / dice / guess_number / 其他命令模板

## Acceptance Criteria

- [ ] `server/templates/signin.html` 新增，DOM + CSS（DESIGN.md token）+ inline JS（SVG 点链 + 拆解小字 + stat-tiles + edge cases）
- [ ] `server/pages/signin_page.py` 新增，`create_signin_page(...)` 返回 page URL，JSON payload 与 schema 一致
- [ ] `server/web_server.py` 注册 `/render/signin` 路由
- [ ] `nextbot/plugins/economy.py` `handle_sign` 成功路径用截图发图 + at 调用者；失败路径不变
- [ ] 图片渲染异常 try/except 降级为原文本回复（保险）
- [ ] `python3 -m py_compile` 三个 .py 文件通过
- [ ] 人工验证 4 场景：正常签到 / 连续中断 / streak 未开启 / cap warning

## Technical Notes

- prior art：`server/templates/dice.html` + `server/pages/dice_page.py` + `server/web_server.py` 中 `/render/dice` 注册 + `nextbot/plugins/dice.py` 调用方式
- 截图 helper：`server/screenshot.py` 已有 `screenshot_url` / `temp_screenshot_path`
- 字体 / 颜色 / 间距 token：`server/static/assets/css/render-tokens.css` + `render-fonts.css`
- frontend-design skill 应用：
  - **Differentiation**：连续点链是签到独有 hook（dice 没时序、rob 没累积）
  - **Don't堆砌**：stat-tile 不凑 4
  - **Typography**：复用 Copernicus，与系列一致
  - **No motion**：静态截图，重点在 composition + decorative details
