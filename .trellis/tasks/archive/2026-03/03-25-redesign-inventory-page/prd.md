# 优化背包页面样式

## Goal

重新设计背包截图渲染页面的视觉风格，与进度页面保持同一 Terraria 暗色主题，提升高级感。

## 约束

- **不改 JS 逻辑**：只改视觉样式（CSS / Tailwind class / HTML 结构微调）
- 保留全部功能：sections、item 图片、tooltip、show_stats/show_index 参数、stack 数量显示

## 设计目标

- 暗色 Terraria 风格，与 progress.html 视觉语言一致
- 物品格：有物品时有微发光感，空格暗淡低调
- 分区卡片：深色玻璃感边框，金色/琥珀色标题点缀
- 顶部 header：玩家信息布局优化，生命/魔力值配色区分
- 统计栏：精简徽章样式
- 物品 tooltip：暗色高质感弹出框（逻辑不变，只改 class）
- 截图友好：2000px 宽度下布局紧凑美观

## 参考

- `server/templates/progress.html` — 同系风格参考
- 物品图片：`/assets/items/Item_{netId}.png`（像素画风格，暗色背景下更突出）

## Files to Modify

- `server/templates/inventory.html`（完全重设计视觉层）
