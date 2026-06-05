# 菜单将「查询系统」分类移到序号 2

## Goal

`菜单` 命令列出的分类按 `nextbot/plugins/menu.py` 的 `CATEGORY_ORDER` 排序、以 `{序号}. {分类}` 展示。当前「查询系统」**不在** `CATEGORY_ORDER`，被归入 extras 排到末尾。用户希望把「查询系统」移到**序号 2**（紧随「用户系统」之后）。

## What I already know

- 菜单排序：`nextbot/plugins/menu.py:55 CATEGORY_ORDER`（list），`_group_by_category` 取 `[c for c in CATEGORY_ORDER if c in by_cat]` + extras（非列表内的按字母序追加）；展示 `enumerate(cat_names, 1)` → `{i}. {cat}`。
- 实际分类统计：使用中的有 查询系统(8)、排行榜(17)、仓库系统(8) 等；**「玩家查询」「服务器管理」在 CATEGORY_ORDER 里但无任何命令使用**（运行时被过滤），故不影响显示序号。
- 「查询系统」由 8 个命令使用（`player_query.py` 多个 + `server_manager.py:21`），当前因不在 CATEGORY_ORDER 而显示在最后。
- 「用户系统」(5 命令) 在 CATEGORY_ORDER 首位且实际存在 → 显示序号 1。

## Requirements

- 在 `CATEGORY_ORDER` 中把「查询系统」放到「用户系统」之后（index 1），使菜单显示为 `2. 查询系统`。
- 其余分类相对顺序不变（用户系统仍 1，其后整体顺延）。
- 不动分类名本身、不动各命令的 `category=`、不动 extras 兜底逻辑、不动菜单其它行为（搜索命令、分类详情等）。

## Acceptance Criteria

- [ ] `CATEGORY_ORDER` 中「查询系统」位于「用户系统」之后、「经济系统」之前。
- [ ] `菜单` 顶层列表「查询系统」显示为序号 2（用户系统为 1）。
- [ ] 其它分类相对顺序不变；菜单其它功能不回归。
- [ ] （如适用）补一条轻量单测断言 `_group_by_category` 返回的 `cat_names` 中「查询系统」紧随「用户系统」。

## Out of Scope

- 不清理 CATEGORY_ORDER 里未使用的「玩家查询」「服务器管理」（用户未要求；本任务只移「查询系统」）。
- 不改各命令的 `category=` 取值、不改 emoji 映射、不改菜单截图 / 搜索逻辑。

## Technical Notes

- 改动点：`nextbot/plugins/menu.py:55 CATEGORY_ORDER` —— 在 `"用户系统",` 后插入 `"查询系统",`。
- `CATEGORY_EMOJI` 已有「查询系统」? 否 → 缺失时 `_group_by_category`/展示用 `EMOJI_LIST` 兜底（`CATEGORY_EMOJI.get(cat, EMOJI_LIST)`），不会报错；可顺带补一个合适 emoji（如 EMOJI_USER/EMOJI_SERVER），但非必须，避免扩大范围——**默认不加**，除非已有约定。
