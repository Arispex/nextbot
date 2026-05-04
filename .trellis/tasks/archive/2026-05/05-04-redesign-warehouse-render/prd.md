# Redesign Warehouse Render

## Goal

按 DESIGN.md 把「我的仓库」+「用户仓库」两个截图重做成暖色编辑风：去外层 wrap card、移除 dark theme、启用 fit_content_height。本次有个特殊性 —— 仓库是**固定 100 格**的网格而不是变长列表，所以"自适应高度"主要靠简化 header 和外层 padding 收益，10×10 主网格高度本身是刚性的。

## What I Already Know

**当前实现**：
- 命令：`我的仓库` + `用户仓库` 共用同一个 `_send_warehouse_image()` → `create_warehouse_page()` → `warehouse.html` 模板
- 模板 [server/templates/warehouse.html](../../../server/templates/warehouse.html) 303 行，单文件 + Tailwind CDN + 内联 CSS + 内联 JS
- 截图选项：`viewport_width=1200, viewport_height=600, full_page=True`（[nextbot/plugins/warehouse.py:41](../../../nextbot/plugins/warehouse.py)）
- payload：`owner_user_id / owner_user_name / capacity=100 / used / slots[100] / theme`
- 异步加载 `/assets/dicts/{item,prefix}.json` 字典做 ID → 中文名映射

**当前样式特征**：
- 红橙渐变背景 + 大圆角 page-header 卡 + 大圆角 grid-card 包整个 100 格网格
- 每格 4:5 长宽比，左上角 `#N` 编号，右上角 `×N` 堆叠数（>1 时）
- 占用格：浅黄底 + 金色边
- 空格：浅灰底 + 灰边 + 居中"·"
- tier-chip 4 阶颜色：早期蓝 / 中期紫 / 后期粉 / 终局金
- header 三列布局：左 title "用户仓库" + 中 owner name/QQ/usage + 右生成时间

**两个特殊点**：
1. 标题硬编码为"用户仓库"，**不区分**「我的仓库」和「用户仓库」 — 这其实是 bug，但因为 owner 名字本身就标识了"这是谁的仓库"，标题反而冗余
2. 跟用户信息页同样是"个人 hero 页"语义 — 应该走"无 header-rule，owner 名字+头像作锚"的模式

## Decisions Locked

按已成型模式直接套用：

- **Canvas-first**：删 body 渐变、删 page-header wrap card、删 grid-card 外包 — 保留主网格，但每个 slot 的视觉处理改用 token 化样式
- **Header 走视觉 hero 模式**（与用户信息页一致）：avatar + 大字 owner name + meta（QQ + 使用率 + 时间），**无 header-rule**
- **删除"用户仓库"硬编码标题** — owner name + avatar 已经清楚说明"这是谁的仓库"
- **payload 增加可选 title 字段**？— **不加**。让两个命令都用同样的"无标题、owner 即 hero"风格，与「我的信息」/「用户信息」共用模板的处理方式一致
- **删除 dark theme 整段**：payload `theme` 保留兼容
- **空格 slot**：cream canvas 底 + 1px hairline 虚线（或细边）+ rounded-md，居中"·" muted-soft
- **占用格 slot**：cream-card 底 + 1px hairline 实线 + rounded-md
- **slot id `#N`**：保留，但用 caption 字号 + muted 色
- **stack badge `×N`**：保留右上角，背景 surface-cream-strong + ink，无金色描边
- **prefix / item name**：保留居中两行，前缀用 muted/accent-amber，物品名 ink
- **value 行**：删 `💰` emoji，改"金币 N"明文，font-feature-settings: tnum
- **tier-chip 4 阶语义色重映射**到 DESIGN.md token：
  - tier-none + tier-0~5（前期）→ cream pill (canvas + ink + hairline) — 最低门槛
  - tier-6~10（中期）→ accent-teal 描边
  - tier-11~15（后期）→ accent-amber 描边
  - tier-16~20（终局，月亮领主等）→ primary coral 描边 + 实底 — DESIGN.md 强调 "scarce coral"，给最稀有物品恰到好处
- **viewport 宽度调到 1200**（保持当前 1200，10 列容得下）+ `fit_content_height=True`
- **grid 间距**：从 8px 改成 10px（与 DESIGN.md `--space-sm` 12px 接近，比当前略宽给视觉呼吸）
- **footer**：caption + muted-soft

## Requirements

- [ ] 整页 cream canvas，无外层 wrap card
- [ ] Header 走视觉 hero 模式：avatar + serif owner name + meta 行
- [ ] 占用 slot = cream-card + hairline；空 slot = canvas + 虚线 hairline
- [ ] tier-chip 4 阶颜色重映射到 DESIGN.md token
- [ ] 数字（`#N` slot id / `×N` stack / `金币 N` value）用 Inter sans + tnum
- [ ] 模板移除 `data-theme` 切换
- [ ] `WAREHOUSE_SCREENSHOT_OPTIONS` 启用 `fit_content_height=True`
- [ ] 删除"用户仓库"硬编码 title

## Acceptance Criteria

- [ ] 「我的仓库」+「用户仓库」截图视觉符合 DESIGN.md 暖色编辑风
- [ ] 100 格 10×10 布局完整、不破版
- [ ] 占用格 / 空格视觉对比清晰
- [ ] tier-chip 颜色按 DESIGN.md 4 阶分级，与游戏进度语义匹配（前期 → 终局视觉重要性递增）
- [ ] payload `theme` 字段被忽略但接受（向后兼容）
- [ ] 命令链路 0 改动

## Out of Scope

- 物品 icon 图片本身（仍走 `/assets/items/Item_<id>.png`）
- 物品 / 前缀字典加载逻辑（保持异步 fetch）
- WebUI 后台仓库管理（`server/webui/templates/warehouse_content.html` 不动）
- 「用户背包」/「我的背包」（is `inventory.html`，不在范围）

## Definition of Done

- 修改限于 `server/templates/warehouse.html` + `nextbot/plugins/warehouse.py` 的 `WAREHOUSE_SCREENSHOT_OPTIONS`
- 本地 Playwright 渲染验证：空仓库 / 少量物品 / 满仓库三种密度
