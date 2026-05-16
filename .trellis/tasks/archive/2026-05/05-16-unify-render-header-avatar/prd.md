# 4 个图片模板 header 统一玩家 avatar bar

## Goal

把 4 个图片模板的 header 从旧式 `玩家 X · QQ Y · 其他` 文本模式，统一为 signin / warehouse 风格的 `[avatar] X (Y) · 其他` 模式：QQ 头像放最前，玩家名 + QQ 紧随，其他 meta 字段全部后移。

## Scope（4 个模板）

| 模板 | 当前 meta line | 迁移后 |
|---|---|---|
| `dice.html` | `玩家 X · QQ Y · 选择 Z · 投入 N · 时间` | `[avatar 48] X (Y) · 选择 Z · 投入 N · 时间` |
| `guess_number.html` | `玩家 X · QQ Y · 范围 R · 选择 Z · 投入 N · 时间` | `[avatar 48] X (Y) · 范围 R · 选择 Z · 投入 N · 时间` |
| `rob.html` | `抢劫者 X · QQ Y · 目标 W · QQ Z · 时间` | `[avatar 48] X (Y) · → 目标 W (Z) · 时间` |
| `lottery_result.html` | `奖池 X · ID I · 玩家 W · QQ Z · ...` | `[avatar 48] W (Z) · 奖池 X (#I) · ...`（玩家前置，奖池后移）|

### rob.html 特殊处理

rob 有 2 个用户（抢劫者 + 目标）。页面正文本身已经有双方头像（v1.6.0 加的 `rob.html` 流向图），所以 header 只放**抢劫者**头像 + 名字 + QQ，目标用文本 `→ 目标 W (Z)` 形式跟在后面，箭头 `→` 视觉提示流向。

### lottery_result.html 字段顺序调整

旧顺序：`奖池 · ID · 玩家 · QQ · ...`
新顺序：`[玩家 avatar] 玩家名 (QQ) · 奖池 X (#ID) · 中奖等级 · ...`

奖池名 + ID 合并成 `奖池 X (#ID)` 一段，减少 divider 数。

## 统一规范（mirror signin / warehouse）

### DOM
```html
<header>
  <div class="header-rule"></div>
  <h1 class="header-title type-display-lg">{标题}</h1>
  <div class="owner-bar">
    <img id="{prefix}-avatar" class="avatar" alt="avatar" />
    <div class="owner-meta type-body-sm">
      <span class="owner-name" id="{prefix}-owner-name"></span>
      <span class="owner-id" id="{prefix}-owner-id"></span>
      <span class="meta-divider">·</span>
      <!-- 其他 meta 字段，每段前加 meta-divider -->
      <span>...</span>
      <span class="meta-divider">·</span>
      <span id="{prefix}-generated-at"></span>
    </div>
  </div>
</header>
```

### CSS（添加 warehouse 同款，已存在的删除老 `header-meta` / `meta-value`）
```css
.owner-bar {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  color: var(--color-muted-soft);
}
.avatar {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-pill);
  background-color: var(--color-surface-card);
  border: 1px solid var(--color-hairline);
  object-fit: cover;
  flex-shrink: 0;
}
.owner-meta {
  display: flex;
  gap: var(--space-md);
  align-items: baseline;
  flex-wrap: wrap;
  min-width: 0;
}
.owner-name {
  color: var(--color-ink);
  font-weight: 500;
}
.owner-id {
  color: var(--color-muted);
  font-family: var(--font-code);
}
.meta-divider {
  color: var(--color-hairline);
}
```

**对 lottery_result.html / rob.html 这种保留 `meta-value` class 的**：检查 `meta-value` 还有其他地方在用没 —— 若仍被其他 span 使用，**保留** `.meta-value` 规则。

### JS
```js
document.getElementById("{prefix}-avatar").src =
  `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(playerQq)}&s=100`;
document.getElementById("{prefix}-owner-name").textContent = playerName || "未知玩家";
document.getElementById("{prefix}-owner-id").textContent = playerQq ? `(${playerQq})` : "";
```

## Out of Scope

- 不改 page 模块（`server/pages/*.py`）—— payload schema 不变（player_name / player_qq 字段名复用）
- 不改 backend handler / business logic
- 不改 DESIGN.md / render-tokens.css / render-fonts.css
- 不改 webui templates
- **不动** inventory / warehouse / signin（已是 avatar 模式）
- **不动** about / admin_list / ban_list / leaderboard / red_packet_* / shop_* / lottery_list / lottery_view / menu / progress / tutorial / user_info（无玩家 X QQ Y 模式或无 player 概念）

## Acceptance Criteria

- [ ] 4 个模板（dice / guess_number / rob / lottery_result）的 header 全部用 `owner-bar + avatar` 模式
- [ ] 4 个模板各自的 prefix 唯一（避免 id 冲突）：dice → `dice`，guess_number → `gn`，rob → `rob`，lottery_result → `lr`
- [ ] `grep -rn "玩家 <span\|QQ <span\|meta-player" server/templates/` → 0 matches
- [ ] avatar URL 用 `https://q1.qlogo.cn/g?b=qq&nk=${encodeURIComponent(qq)}&s=100`
- [ ] `.header-rule` / `.header-title` 保留
- [ ] page 模块 `dice_page.py` / `guess_number_page.py` / `rob_page.py` / `lottery_result_page.py` 未改
- [ ] 4 个文件均 `python3 -m py_compile`（HTML 不需要，但 page 模块如未改也应未损）
- [ ] HTML parser 测试 4 个文件都能 parse

## Verification Loop

用户要求"改完之后再次检查还有没有遗漏的"：
1. trellis-implement 改 4 个文件
2. trellis-check 用 grep 全仓扫 `玩家 <span` / `QQ <span` / `meta-player` 残留 + 验证 4 个文件都用 owner-bar 模式
3. 若残留，self-fix；否则结束

## Technical Notes

- rob.html 的 `meta-robber-name` / `meta-robber-qq` 改为 `rob-owner-name` / `rob-owner-id`；`meta-victim-name` / `meta-victim-qq` 保留为 meta 字段（不是 owner）但改文案 `→ 目标 W (Z)`
- lottery_result.html 的 player_qq 字段名在 payload 里可能不叫这个 —— 子代理需自己核对 payload schema 字段名
- 注意 4 个模板的 inline JS 数据绑定都在各自的 `<script>` 末尾段，找到 `meta-player-name / meta-player-qq / meta-robber-name / ...` 赋值的地方替换
