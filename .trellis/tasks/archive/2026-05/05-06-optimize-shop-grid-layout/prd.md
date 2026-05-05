# 优化查看商店图片排版改为网格布局

## Goal

把 `查看商店` 命令生成的截图从单列长列表改成 2 列网格，**每张商品卡片内部布局完全保持不变**（icon 64×64 + 中间内容 + 右侧价格列）。镜像复用上一个任务（`05-06-optimize-pool-grid-layout`，commit `9f42d9f`）在 `查看奖池` 上验证过的方案。

## Background

`查看商店` 与 `查看奖池` 是同源排版模式（icon 左 + 中间名称/描述/meta + 右侧主数值列）。`查看奖池` 已通过最小改动（`.list` 由 flex column 改为 `grid 2 columns`）解决了图片过长 + 中间留白问题，本任务把同样的改动套到 `查看商店` 上。

## Requirements

- `server/templates/shop_view.html`：`.list` 由 flex column 改为 `grid` 2 列布局；`.item` 增加 `min-width: 0` 防止溢出。卡片内部布局不动。
- `nextbot/plugins/shop.py`：`查看商店` 命令的 `limit` 参数：`default: 10 → 20`，`max: 50 → 100`，描述加上"按 2 列网格布局，建议为偶数"提示，handler 内 clamp 同步更新。
- `商店列表`（`shop_list_matcher`，line 132 起）保持不动。

## Non-goals

- 不改 `shop_list.html` / `lottery_list.html` / 其他截图页面
- 不改 `查看商店` 卡片内部布局（icon 大小、价格字号、tier-chip 等）
- 不改 `SHOP_VIEW_SCREENSHOT_OPTIONS`（viewport / fit_content_height 等）

## Acceptance Criteria

- [ ] 截图的图片高度相比原来减半左右
- [ ] 同一行的两张卡片高度对齐（`align-items: stretch`）
- [ ] 长描述 / 长 meta 不会撑破 grid 列宽
- [ ] `查看商店 <ID>` 默认渲染 20 件商品 = 2 列 × 10 行
- [ ] 卡片内 icon / 价格 / tier-chip / cmd-block 视觉效果与改动前一致

## Reference

- 上一个任务：`.trellis/tasks/archive/2026-05/05-06-optimize-pool-grid-layout/`
- 工作 commit：`9f42d9f refactor(lottery): switch 查看奖池 prize list to 2-up grid`

## Definition of Done

- 单一 commit，遵循 Conventional Commits
- 用户测试通过后再 commit（不主动提交）
