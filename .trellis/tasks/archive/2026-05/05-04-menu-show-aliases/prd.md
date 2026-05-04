# 菜单图片显示命令别名

## Goal

`菜单 <分类>` 命令渲染的图片目前只显示命令的 `display_name`、`description`、`usage`、`permission`。由于命令别名（aliases）现在支持自定义，用户希望在菜单卡片上能看到每条命令的自定义别名，便于使用者了解完整的命令入口。

## Requirements

* 菜单卡片在存在别名时新增一行"别名"展示
* 别名以小型 pill 徽章呈现，复用现有 `perm-row` / `perm-badge` 设计语言
* 当某条命令的 `aliases` 为空时，该行整体不渲染（不显示"无别名"占位）
* 多个别名横向并排，超出宽度自动换行
* 别名行位置：放在 `usage-block` 之后、`perm-row` 之前

## Acceptance Criteria

* [ ] `菜单 <某有别名分类>` 渲染出的图片中，配置了别名的命令卡片上能看到 "别名" 标签 + 别名徽章
* [ ] 没有配置别名的命令卡片不显示别名行（视觉上等同于改造前）
* [ ] 多个别名时，徽章横向排列并在窄列内自动换行，不溢出卡片
* [ ] WebUI 命令配置页面修改别名后，再次发送 `菜单` 命令渲染出的图片立即反映新别名（依赖现有 `refresh_runtime_cache`）

## Definition of Done

* 三处修改均完成且菜单截图人工验证通过
* 现有视觉规范（warm-canvas tokens、type-caption、radius-pill 等）保持一致
* 现有无别名分类的菜单截图视觉无回归

## Technical Approach

修改三处：

1. **`nextbot/plugins/menu.py:201-211`** — 构造 `render_commands` 时把 `item.get("aliases", [])` 一并放入 dict
2. **`server/pages/menu_page.py:13-32`** — `build_payload` 的 `normalized_commands` 中保留 `aliases: list[str]` 字段（去重 + strip）
3. **`server/templates/menu.html`** — JS 渲染部分在 usage 之后插入条件 alias-row；CSS 新增 `.alias-row` / `.alias-label` / `.alias-badge`，复用 `--color-canvas` 背景 + `--color-hairline` 边框 + `--radius-pill` 样式

布局：

```
[card]
  display_name
  description
  usage-block
  alias-row (仅当存在)  ← 新增
    "别名"  [chip] [chip] [chip]
  perm-row
    "权限"  [badge]
```

## Decision (ADR-lite)

**Context**: 选择别名展示位置 — 紧贴标题 vs 独立行 vs 紧靠 usage
**Decision**: 独立行，放在 usage-block 与 perm-row 之间，复用 perm-row 视觉
**Consequences**: 视觉上和 perm-row 形成"运行时元数据双行"，便于阅读；但卡片高度会因别名数量略增

## Out of Scope

* 不在菜单文本（`reply_list`）中加别名
* 不修改搜索命令逻辑
* 不修改 WebUI 别名编辑界面
* 不为别名加链接 / 点击复制等交互

## Technical Notes

* `RuntimeCommandState.aliases` 已通过 `_to_runtime_state` 从 `aliases_json` 解析出来（command_config.py:406-411）
* `_serialize_runtime_state` 已经包含 `"aliases": list(item.aliases)`（command_config.py:550）
* 菜单数据流：`list_command_configs()` → `handle_menu` 构造 `render_commands` → `create_menu_page` → `build_payload` → 模板替换 `__MENU_DATA_JSON__`
* 模板 token：`server/templates/render-tokens.css`、`render-fonts.css` 提供所有 CSS 变量
