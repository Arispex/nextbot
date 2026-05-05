# 彻底清理 RENDER_THEME 残留死代码

## Goal

v1.5.0 删 RENDER_THEME 多主题支持时，保留了大量"theme 透传链"作为兼容层。截图模板已经完全不读 `theme` 字段了（`server/templates/about.html:292` 注释 `// Hero — always use light logo (no theme switching).` 印证），但 Python 这边整条链 17 个路由 + 16 个 page 模块还在传递这个死参数，造成上一次 leaderboard 那种"signature 改了但漏了一个调用点"的回归隐患。

本任务一次性清理掉所有 `theme=` 残留，确保零残留。

## Requirements

### A. `server/web_server.py`（17 个路由 handler）

逐个删除：

- 形参 `theme: str = "light"` / `theme: str = "dark"` 
- 下游调用里的 `theme=theme,` 关键字实参

涉及行：50, 66, 78, 84, 99, 108, 120, 123, 132, 134, 143, 145, 163, 176, 187, 189, 200, 203, 215, 218, 229, 234, 246, 252, 265, 268, 286, 291, 312, 321, 334, 341, 360, 373

**注意**：FastAPI 路由的 `theme` 形参其实会被解析为 query string `?theme=xxx`。删掉之后，旧 URL 带 `?theme=xxx` 仍然能访问（FastAPI 会忽略未声明的 query 参数），所以不破坏向后兼容。

### B. `server/pages/*.py`（16 个 page 模块）

涉及文件：`about_page.py` / `admin_list_page.py` / `ban_list_page.py` / `inventory_page.py` / `leaderboard_page.py` / `lottery_list_page.py` / `lottery_result_page.py` / `lottery_view_page.py` / `menu_page.py` / `progress_page.py` / `red_packet_all_page.py` / `red_packet_own_page.py` / `shop_list_page.py` / `shop_view_page.py` / `tutorial_page.py` / `user_info_page.py` / `warehouse_page.py`

每个文件三处删除：

1. `build_payload(...)` 形参里的 `theme: str = "..."`
2. `build_payload` 返回的 dict 里的 `"theme": str(theme).strip() if ... else "..."` 整行
3. `render(payload)` 内 `data` 字典里的 `"theme": str(payload.get("theme", "..."))` 整行

### C. 模板

不动 —— 已确认模板里没有读 `data.theme`（前面 grep 全 0）。

### D. 调用方

不动 —— 调用方都已经不传 `theme`（连 leaderboard 都改完了）。

## Acceptance Criteria

- [ ] `grep -rnE "theme=|theme:" --include="*.py" nextbot/ server/` 输出为空（修完后）
- [ ] `grep -rnE "RENDER_THEME" --include="*.py" --include="*.html" nextbot/ server/` 输出为空
- [ ] `python3 -m py_compile` 所有改动文件通过
- [ ] `python3 -c "from nextbot.plugins import leaderboard, menu, warehouse, lottery"` 全部干净导入
- [ ] 旧 URL `?theme=dark` 仍可访问（FastAPI 忽略未声明的 query 参数；行为跟移除前一致，因为模板本来就不读它）
- [ ] 重启 bot 后任意截图命令（菜单 / 排行榜 / 背包 / 商店）成功生成图，视觉无变化

## Definition of Done

- 全量 `grep theme` 在 nextbot/ + server/ 下命中 0 行（除了 `prefers-color-scheme` / `color-scheme` CSS 关键字 / 注释里讲历史的字符串）
- 不引入新依赖、不动模板
- 不动调用 site（已经都不传了）

## Technical Approach

trellis-implement 应**逐文件**做：

1. 先 `web_server.py` —— 17 个 route handler，模式都一样：删形参 `theme: ...`，删下游调用的 `theme=theme,`
2. 然后 16 个 page 模块 —— 每个删 3 处：build_payload 形参、build_payload 返回字典里的 theme key、render 函数内 data 字典里的 theme key
3. 修完后跑全量 grep 验证

每改一个文件后，运行 `python3 -m py_compile <file>` 确认语法。

## Decision (ADR-lite)

**Context**：上次 leaderboard 的 TypeError 暴露出"删 RENDER_THEME 时只删了一半"的真实问题；剩余 33 处 dead theme= 是同类隐患温床（任何一处的形参改默认值或者下游函数改签名都可能再炸）。

**Decision**：一次性全删。模板已经不读 theme，删除是安全的；而且让 codebase 真正"无残留"，避免后续再被 dead chain 误导。

**Consequences**：
- 优点：彻底无 theme 残留；类型安全；给来者明确信号
- 缺点：改动文件数多（18 个），但每处都是机械删除，不引入逻辑变化
- 风险：若某个被忽略的下游路径仍读 theme（已 grep 验证否），会导致 KeyError —— 但 grep 已确认零

## Out of Scope

- 不删 CSS 文件里的 `prefers-color-scheme` / `color-scheme: dark` 等浏览器原生主题关键字（这些是浏览器 UA 适配，与 RENDER_THEME 无关）
- 不重命名任何接口、不动 URL 路由路径
- 不动模板 HTML
- 不动 WebUI（前端已经在 v1.5.0 清理过）

## Technical Notes

- 完整 grep 结果保留在调研对话历史中（17 + 16×3 = 65 行删除）
- 验证脚本：`grep -rnE "theme=|theme:" --include="*.py" nextbot/ server/`
- 模板 0 引用已确认：`grep -nE "theme|data\.theme" server/templates/*.html` 仅命中 1 行历史注释
