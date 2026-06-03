# 我的角色 / 用户角色命令：渲染 Terraria 角色立绘卡片

## Goal

新增查询命令 `我的角色 <服务器 ID>`（本人）与 `用户角色 <服务器 ID> @某人`（他人），从指定服务器拉取角色外观 + 装备 + 染料数据，bot 端**本地合成**角色立绘，套一张 DESIGN.md 风格的信息卡片（含玩家名 / 服务器），截图发送。渲染逻辑抽成不依赖 NoneBot 的可复用模块，供后续功能复用。

渲染管线（XNB+LZX 解码、composite 全帧、头发遮挡、精确染料）已在 `temp/xnb_probe/` 逐项对照游戏内验证一致。本任务把原型落地为生产代码并接入查询系统。

## Requirements

### 命令层（`nextbot/plugins/player_query.py`，category `查询系统`）
* `我的角色 <服务器 ID>`：镜像 `我的背包`。`event.get_user_id()` → `User.name` → 调 API → 渲染 → 发卡片。权限 `player_query.character.self`。
* `用户角色 <服务器 ID> @某人`：镜像 `用户背包`，`resolve_user_id_arg_with_fallback` 取目标。权限 `player_query.character.user`。
* 两个权限加入 `DEFAULT_GUEST_PERMISSIONS` + `command_control` 注册。
* 调 API：`request_server_api(server, f"/nextbot/users/{quote(name,safe='')}/appearance")`。

### 数据 → 渲染范围
* **appearance + equipment(head/body/legs) + vanity(head/body/legs) + dye(head/body/legs)**。
* 装饰优先级**逐部位**：某部位有 vanity 则用 vanity，否则用 equipment（对应该部位的 dye 同样作用于最终显示件）。
* 染料：基础色染料（ArmorColored 系：colored / black / bright / silver-trim）精确还原；exotic/动画染料（gradient/rainbow/reflective/living…）best-effort，无法静态还原的回退为不染色。
* 配饰（accessories / vanityAccessories / accessoryDyes）**本期不渲染**。

### 可复用渲染模块（`nextbot/terraria_render/`，不依赖 NoneBot）
* 公开 API：`render_character(appearance, equipment, vanity, dye) -> bytes`（透明底 PNG，已放大），入参为 API 各数据块（保留原始字段）。
* 组成：`compositor.py`（图层合成 + 头发遮挡 + 装备/装饰）、`dye.py`（ArmorColored shader）、`data/`（查表：netID→equip slot、dye→shader、fullHair/hatHair 集、male variants、robe 扩展）、`assets/`（预提取 PNG）。
* `appearance == null`（无 SSC 存档）由命令层处理为友好提示，不进渲染。

### 信息卡片（`server/pages/character_page.py` + `web_server.create_character_page`）
* 镜像 `inventory_page.py` / `user_info_page.py` 的模板+payload+render 模式，风格用 DESIGN.md token（cream canvas、coral accent、圆角、字体层级）。
* 卡片内嵌角色立绘（base64 data URI，CSS `image-rendering: pixelated` 保持像素清晰），展示玩家名、服务器名。
* 经 `render_and_send_screenshot` 截图发送（与其他卡片一致）。

### 资源 / 工具（一次性，非运行时）
* `tools/`（或 task 内）一次性提取脚本：读用户自有 Terraria 安装 → XNB→PNG 全量提取（Player_*、Player_Hair_*/Alt、Armor_Head_*、Armor/Armor_*、Armor_Legs_*，约 1200 张）→ `nextbot/terraria_render/assets/`（入库，方案 A）。
* 查表数据由反编译源（`temp/decomp/full/`：`Item.cs`、`DyeInitializer.cs`、`Player.cs`）生成/烘焙为 `data/*.json`，入库。运行时纯 PNG + numpy，不含 LZX 解码器。

## Acceptance Criteria

* [ ] `我的角色 <ID>` / `用户角色 <ID> @某人` 返回与游戏内一致的角色立绘卡片（含装备/装饰/基础染料）。
* [ ] API 返回各块为 `null`（无存档）→ 提示「暂无角色数据」，不报错不空图。
* [ ] 账号不存在(400) / 服务器不存在 / 连接失败 → 「动作+结果，原因」反馈。
* [ ] `render_character(...)` 可脱离 NoneBot 独立调用并稳定输出。
* [ ] 资源仅在 `nextbot/terraria_render/assets/`，主代码逻辑不含游戏素材；提取脚本与数据生成有说明。
* [ ] ruff / pyright 通过；颜色字段原样透传不改写；统一日志入口。

## Definition of Done
* 渲染模块最小测试（已知输入 → 稳定 PNG，关键像素/尺寸断言）。
* 提取脚本 + 数据生成步骤有文档；卡片在真实数据下截图正常（像素不糊）。

## Out of Scope
* 配饰（62-68 / 72-78 / 82-88）渲染 —— 后续任务。
* 动画染料逐帧动画；长袍裙摆扩展 / coat 披风槽（除非后续确认）。
* 渲染结果缓存（render 足够快，按需再加）。

## Decision (ADR-lite)
* **输出**：HTML 卡片（DESIGN.md 风格）+ 浏览器截图，复用 `create_*_page` + `render_and_send_screenshot`；可复用模块只产原始透明立绘，卡片在命令层组装。
* **资源**：方案 A 预提取 PNG 入库（约数 MB），运行时无 LZX。
* **范围**：本期本人 + 他人两命令；外观+装备+装饰+基础染料；配饰留后。

## Technical Notes
* 原型/工具：`temp/xnb_probe/`（`lzx_xnb.py`、`xnb_to_png.py`、`compose_player.py`、`armor_colored_impl.py`、`fx_parse.py`）。规格：`temp/terraria_render_spec.md`、`temp/dye_shader_spec.md`。
* 反编译源：`temp/decomp/full/`。
* 接入参考：`player_query.py` `handle_my_inventory`/`handle_user_inventory`；`server/web_server.py` `create_inventory_page`；`server/pages/user_info_page.py`；`nextbot/screenshot_render.py`。
* 约束：颜色 packed int 原样；文案「动作+结果，原因」；统一日志入口；API 字段不翻译。
</content>
