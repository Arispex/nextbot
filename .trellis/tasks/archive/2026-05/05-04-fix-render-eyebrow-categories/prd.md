# Fix render eyebrow categories to match menu hierarchy

## Goal

让所有截图模板的 eyebrow（标题上方小字）反映该命令在 `/菜单` 中的真实二级分类，而不是临时拍脑袋起的名字。

## What I already know

- 各功能在代码中已通过 `register_command(category=...)` 声明分类，菜单页面以此分组。
- 多个已重构页面的 eyebrow 与真实分类不符。

## Required mapping

| 模板 | 当前 eyebrow | 真实 category | 命令 |
|------|-------------|---------------|------|
| lottery_list.html | 奖池系统 | **抽奖系统** | 奖池列表 |
| lottery_view.html | 奖池系统 | **抽奖系统** | 查看奖池 |
| lottery_result.html | 奖池系统 | **抽奖系统** | 抽奖 |
| leaderboard.html | 排行榜系统 | **排行榜** | 各种排行榜 |
| inventory.html | 背包系统 | **玩家查询** | 我的背包 / 用户背包 |
| progress.html | 进度系统 | **玩家查询** | 进度 |

不变（已正确）：
- shop_list / shop_view → 商店系统 ✓
- red_packet_all / red_packet_own → 红包系统 ✓
- warehouse → 仓库系统 ✓
- menu → 命令菜单 ✓（菜单页面自身用 "命令菜单" 作为 hero eyebrow，合理）
- user_info → 无 eyebrow（visual-hero 模式，avatar 是锚点）

## Out of Scope

- 修改 `register_command` 的 category 值（保留菜单结构）。
- 调整其它视觉元素。
