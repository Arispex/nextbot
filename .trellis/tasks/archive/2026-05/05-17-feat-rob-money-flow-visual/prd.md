# [ABANDONED] rob 图片改「金币流」视觉

**Status**: Abandoned in implementation phase（V1 实施了 SVG money-flow track，用户看后决定放弃，工作树已 git restore 还原到上个版本）

**Abandoned reason**: 用户审视 V1 后认为效果不符合预期；未要求 V2 迭代

## Goal

升级 `server/templates/rob.html` 中央 flow 区域的视觉，从扁平的「← 抢走 1234」改为有方向感、有质感的"钱币流"效果。frontend-design skill 原则：克制 + 有意图 + 装饰细节给画面 character。

## V1 设计提案

### 中央 flow track 结构（取代当前 `.rob-flow`）

宽度：240-280px（让 3 列 robber + track + victim 在 920px max-width 内舒展）
高度：~140px（与 player-card min-height 对齐）

垂直分 3 层：

**上层：金币圆点序列**（暗示流动方向）
- 8 个 amber `<circle r=6>` 等距分布
- 在 SVG 中沿水平中线排列
- 流向 source → target（左→右 / 右→左 由 JS 决定）
- 静态截图无动画，但 opacity 沿方向渐变（source 端 0.4 → target 端 1.0）→ 暗示"从源点流出，越靠近终点越聚集"

**中层：粗箭头线**
- SVG `<path>` 粗 4px stroke
- 渐变色：source 端 `--color-accent-amber`，target 端 `--color-primary`（coral）
- 末端 `marker-end` 三角箭头（coral）
- 长度横跨 track 宽度

**下层：金额 + label**
- 金额：mono 28-32px，color 跟随 result_kind（success/crit → coral；counter → amber；fail/police → muted）
- label：uppercase 11px caption（"抢走" / "反被抢" / "罚款消失" / "无收益"）

### 5 个 result_kind 的视觉表达

| result_kind | source → target 方向 | 箭头 / 圆点 | 金额色 | 文案 |
|---|---|---|---|---|
| `crit` | victim → robber | 双线（粗 + 火焰 dot），coral | coral，加 🔥 | 大成功 抢走 N |
| `success` | victim → robber | 标准粗箭头 | coral | 抢走 N |
| `counter` | robber → victim | 标准粗箭头（amber） | amber | 反被抢 N |
| `police` | 无 | 虚线 + 中央 ↓ + 散落圆点 + 🚨 | muted-soft | 罚款 N |
| `fail` | 无 | 虚线 + ❌ + 圆点淡化 | muted-soft | 无收益 |

### source / target 卡片样式（保留+加强）

- `.is-source`：opacity 0.65 + 卡片左上 / 右上小 amber dot（取决于方向）
- `.is-target`：coral outline 2px + 轻微 box-shadow 强调"收方"
- `police` / `fail`：双方卡片都不加 source/target class，正常显示

### SVG 实现细节

整个 track 用 1 个 SVG 实现（width ~260, height ~80）：
- viewBox 让缩放灵活
- 上层圆点：`<circle>` × 8，cx 等距，cy 居上
- 中层箭头：`<defs><linearGradient id="flowGrad">` + `<marker id="arrow">` + `<path stroke="url(#flowGrad)" marker-end="url(#arrow)">`
- 下方文字（金额 + label）在 SVG 外的 `<div>` 层（保证字体 / 字距精确）

### Out of Scope

- 不改业务逻辑 / page 模块 / payload schema
- 不改 header / stats-tiles / cap-warning / footer / 其他模板
- 不加 animation（静态截图）
- police / fail 状态下不动 player-card 主体样式（仅 source/target class 不加）

## Acceptance Criteria

- [ ] 中央 flow 区域是 SVG-driven 视觉（不是纯文本 ←/→）
- [ ] success / crit：金币流向左（victim → robber），coral 渐变
- [ ] counter：金币流向右（robber → victim），amber
- [ ] police：垂直消散视觉（向下散落 + 🚨）
- [ ] fail：虚线 + 淡化（无明显流向）
- [ ] crit 多一个火焰 dot 或加粗（与 success 区分）
- [ ] is-source / is-target 卡片样式仍生效
- [ ] HTML parse 通过
- [ ] `git diff --name-only` 只显示 `server/templates/rob.html`

## Technical Notes

- frontend-design skill 应用：
  - **不要 generic**：避免就一个 → 箭头加金额（这是当前的样子）
  - **Differentiation**：8 个 amber 圆点 + 渐变箭头是 rob 独有的钱币流 hook
  - **Atmosphere**：渐变色 + 圆点序列给画面"动感"，即使静态
  - **No motion**：静态截图，靠 composition 和颜色梯度暗示运动
- 用 SVG `<defs>` 一次性定义 linearGradient + marker，多个 result_kind 共用
- player-card 不动，仅靠 is-source / is-target 切换 class 表达
