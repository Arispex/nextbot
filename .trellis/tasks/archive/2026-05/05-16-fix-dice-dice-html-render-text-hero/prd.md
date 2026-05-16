# fix(dice): 重排版 dice.html 对齐其它 render text-hero 风格

## 现状

上次 feat(dice) commit 把 dice.html 做成"中央一个大 card 包住所有内容"（cream surface + 圆角 + 整块阴影感）。这与 `lottery_result.html` / `inventory.html` / `red_packet_*.html` 等已落地的 render 模板的"text-hero on cream canvas"风格不一致 —— 它们的版式是：
- 顶部 **coral 横线 rule**（64×4 圆角小条，coral 色）
- **eyebrow** 小字大写分类标签（如"抽奖系统"）
- **serif display 大标题**（type-display-lg）
- **header-meta byline** 行内：玩家 · QQ · 池子 · generated_at
- 下方 **stats-tiles**（4-5 列等宽 cream-soft tiles，无大包围 card）
- 内容区直接坐在 cream canvas 上，不再嵌一层大 card
- footer 简洁一行"Powered by NextBot"

## 决议

把 `server/templates/dice.html` 改成 lottery_result 同形布局。

## 改动

### `server/templates/dice.html` 重写

参照 `server/templates/lottery_result.html:305-355` 的 DOM 结构。

#### 1) 整体结构
```html
<body>
  <main class="page">
    <header>
      <div class="header-rule"></div>
      <div class="header-eyebrow type-caption-uppercase">小游戏系统</div>
      <h1 class="header-title type-display-lg">掷骰子结果</h1>
      <div class="header-meta type-body-sm">
        <span>玩家 <span class="meta-value" id="meta-player-name"></span></span>
        <span class="header-meta-divider">·</span>
        <span>QQ <span class="meta-value" id="meta-player-qq"></span></span>
        <span class="header-meta-divider">·</span>
        <span>选择 <span class="meta-value" id="meta-choice"></span></span>
        <span class="header-meta-divider">·</span>
        <span>投入 <span class="meta-value" id="meta-cost"></span></span>
        <span class="header-meta-divider">·</span>
        <span id="generated-at"></span>
      </div>
    </header>

    <!-- 4-tile 横向：投入 / 实际获得 / 净赚 / 当前金币 -->
    <section class="stats-tiles">
      <div class="stat-tile">
        <span class="stat-label">投入</span>
        <span class="stat-value loss" id="sum-cost"></span>
      </div>
      <div class="stat-tile">
        <span class="stat-label">实际获得</span>
        <span class="stat-value" id="sum-payout"></span>
      </div>
      <div class="stat-tile">
        <span class="stat-label">净赚</span>
        <span class="stat-value" id="sum-net"></span>
      </div>
      <div class="stat-tile">
        <span class="stat-label">当前金币</span>
        <span class="stat-value" id="sum-coins"></span>
      </div>
    </section>

    <!-- 中央骰子展示区：直接在 canvas 上，3 个 dice face + 求和行 + 结果标签；不嵌大 card -->
    <section class="dice-display">
      <div class="dice-row">
        <div class="dice-face" id="dice-1"></div>
        <div class="dice-face" id="dice-2"></div>
        <div class="dice-face" id="dice-3"></div>
      </div>
      <div class="dice-sum type-display-md">
        <span class="dice-sum-numbers" id="dice-sum-numbers"></span>
        <span class="dice-sum-equals">=</span>
        <span class="dice-sum-total" id="dice-sum-total"></span>
        <span class="dice-sum-label" id="dice-sum-label"></span>
      </div>
      <div class="dice-result" id="dice-result"></div>
    </section>

    <!-- 触顶警告（hidden by default） -->
    <div id="cap-warning" class="cap-warning type-caption" hidden></div>

    <footer class="footer type-caption">Powered by NextBot</footer>
  </main>
  ...
</body>
```

#### 2) CSS 调整
- 删除原有大 card 的 `.card` 类与 padding / radius / background-card
- `body` 直接用 cream canvas `var(--color-canvas)` 背景
- `.page` 用 max-width 920px（与 lottery_result 一致）+ flex column + gap `var(--space-xl)` + padding `var(--space-xxl)`
- header 三件套（rule / eyebrow / title / meta）样式从 lottery_result 复制
- `.stats-tiles`：grid 4 列，stat-tile cream-card 背景 / radius-md / padding-sm 等（参考 lottery_result）
- `.stat-value.loss` 用 `var(--color-error)`；`.stat-value.gain` 用 `var(--color-success)` 或 coral（看 lottery_result 使用什么）
- `.dice-display`：直接 layout 在 canvas 上，无 card wrap；居中
- `.dice-row`：flex 横排 3 个 .dice-face，gap `var(--space-md)`
- `.dice-face`：cream-strong 背景 + radius-lg + 100×100px + SVG dot 居中。这是骰子本体视觉容器，**这是允许的**因为骰子作为可视化对象本身就是"格子图标"，不是包内容的 card
- `.dice-sum`：serif 大字 + mono 数字 inline，居中或左对齐
- `.dice-result`：颜色由 result_kind 切：win → coral text、triple_win → coral + amber dot、loss → muted、triple_kill → muted strong、tie → muted
- `.cap-warning`：amber 小字一行
- `.footer`：muted-soft 居右或居中

#### 3) JS 调整
保留原有的 IIFE try/catch JSON.parse fallback，重新映射 DOM ID：
- meta 区写入 player_name / player_qq / choice / cost / generated_at
- stats 写入 cost / applied_payout / net / final_coins，其中 net 有正负号 + gain/loss class
- dice-1/2/3 用 SVG 渲染骰子点（保留原 `renderDiceFace(value)` 逻辑）
- dice-sum-numbers `d1 + d2 + d3`、dice-sum-total `total`、dice-sum-label "大/小/豹子"
- dice-result：按 result_kind 切文案与配色 class（dice-result-win / dice-result-lose / dice-result-triple-win / dice-result-triple-kill / dice-result-tie）
- capped=true 时 cap-warning 移除 hidden 并填文案

## Scope

仅 `server/templates/dice.html`。不动 `dice_page.py` 后端 payload（schema 不变），不动 dice plugin / web_server / render route。

## Acceptance

- 视觉与 lottery_result 等命令"text-hero on cream canvas"风格一致
- 顶部为 coral rule + 小写大字 eyebrow + serif title + 行内 meta，**没有外层大 card**
- 4 个 stat-tiles 横排
- 中央骰子展示直接坐在 canvas 上
- 5 种 result_kind 状态文案与色调清晰
- 5 种 dice 状态在 playwright 截图下都不会破版 / 不出现 JSON 残留
- 模板 `[hidden]` 守卫保留
- HTML 平衡

## DO NOT

- 不改 dice_page.py payload schema
- 不改 dice.py / web_server.py / render.py
- 不引外部 CDN / 不动 render-tokens.css / render-fonts.css
- 不 commit

## Technical Notes

- 参考 `server/templates/lottery_result.html:305-355` 的 DOM 结构
- 参考其 inline `<style>` 段中的 `.header-rule` / `.header-eyebrow` / `.header-title` / `.header-meta` / `.header-meta-divider` / `.meta-value` / `.stat-tile` / `.stat-label` / `.stat-value` / `.footer` 等类的样式（直接复制对应规则到 dice.html）
- 5×5 dice dot SVG 逻辑保留
- viewport 仍 720×720 / fit_content_height=True；可适配实际内容高度
