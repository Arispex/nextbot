# 删除所有图片模板的 header-eyebrow（左上角大标题之上的小标题）

## Goal

用户要求：把所有图片模板（`server/templates/*.html`）大标题（`h1.header-title`）上方的 uppercase 小标题（`.header-eyebrow`）全部删除。例如「经济系统」「仓库系统」「命令菜单」「小游戏系统」「查询系统」「权限管理」「红包系统」「抽奖系统」「商店系统」「排行榜」「安全管理」「系统功能」等。

理由：信息层次冗余，大标题已经够清楚。

## Scope（21 个模板，全部）

### Static eyebrow（直接 DOM 写死，15 个）

| 文件 | eyebrow 文字 |
|---|---|
| `signin.html` | 经济系统 |
| `warehouse.html` | 仓库系统 |
| `dice.html` | 小游戏系统 |
| `guess_number.html` | 小游戏系统 |
| `rob.html` | 小游戏系统 |
| `inventory.html` | 查询系统 |
| `progress.html` | 查询系统 |
| `admin_list.html` | 权限管理 |
| `leaderboard.html` | 排行榜 |
| `ban_list.html` | 安全管理 |
| `lottery_result.html` | 抽奖系统 |
| `about.html` | 系统功能 |
| `tutorial.html` | 系统功能 |
| `user_info.html` | （待确认，grep 没显示但 file 在列表，可能是 dynamic）|

### Dynamic eyebrow（JS textContent 赋值，6 个）

| 文件 | eyebrow 文字 |
|---|---|
| `shop_view.html` | 商店系统 |
| `shop_list.html` | 商店系统 |
| `lottery_view.html` | 抽奖系统 |
| `lottery_list.html` | 抽奖系统 |
| `red_packet_all.html` | 红包系统 |
| `red_packet_own.html` | 红包系统 |
| `menu.html` | 命令菜单 |

## 每个文件需要删除的 3 类内容

1. **CSS 规则**（约 line 41-46）：
   ```css
   .header-eyebrow {
     color: var(--color-muted);
     margin-bottom: var(--space-xs);
   }
   ```
   连同前后空白行一起删，保持文件清洁。

2. **DOM 元素**（一行）：
   - Static：`<div class="header-eyebrow type-caption-uppercase">XXX</div>`
   - Dynamic：`<div id="header-eyebrow" class="header-eyebrow type-caption-uppercase"></div>`

3. **JS 赋值**（仅 dynamic 6 个文件）：
   ```js
   document.getElementById("header-eyebrow").textContent = "XXX";
   ```

## Out of Scope

- 不改 `.header-rule`（coral 横条，是视觉锚点，保留）
- 不改 `.header-title`（h1 大标题，是主要信息，保留）
- 不改 header 下方的 owner/meta/avatar bar
- 不改 page 模块（`server/pages/*.py`）
- 不改 backend handler / business logic
- 不改 DESIGN.md / render-tokens.css / render-fonts.css
- 不改 `webui/templates/*.html`（这些是 WebUI 后台页面，不是 image render）—— 仅限 `server/templates/`

## Acceptance Criteria

- [ ] `grep -rn "header-eyebrow" server/templates/` → 0 matches
- [ ] `grep -rn "type-caption-uppercase" server/templates/` → 0 matches（type-caption-uppercase 是被 eyebrow 专用的 utility class，仅出现在 eyebrow 上）
- [ ] 21 个模板的 .header-eyebrow CSS 规则全部删除
- [ ] 6 个 dynamic 模板的 JS `getElementById("header-eyebrow")` 赋值全部删除
- [ ] 不影响 .header-rule / .header-title / 其他 CSS
- [ ] 人工抽查 3-4 张图片渲染效果（签到 / 仓库 / 菜单 / 排行榜）确认大标题不再上方挂小标题

## Verification Loop

用户要求 "删除后再次检查一遍，直到全部删除干净后结束任务"：
1. trellis-implement 删除全部 21 个文件
2. trellis-check 用 grep 重新扫一遍，发现残留就 self-fix
3. 如果 check 报告全清，结束；否则 trellis-check 已经 self-fix 完，再 grep 一次确认

## Technical Notes

- 21 个模板批量改动，但每个都是简单 3 段删除
- type-caption-uppercase utility class 定义可能在 render-tokens.css，但如果只被 eyebrow 用就 dead；本次不动 CSS 文件（out of scope），仅在 grep 检查中确认这个 utility class 未在其他地方被引用，作为 cleanup 信号
- 字幕「待确认」的 `user_info.html` 需要 implement agent 自己 grep 一次确定 eyebrow 文字
