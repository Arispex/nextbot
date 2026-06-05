# 角色渲染：配饰（功能 / 社交 / 配饰染料）

## Goal

扩展 `nextbot/terraria_render` 渲染模块，给角色立绘加上**功能配饰**（accessories 62-68）、**社交配饰**（vanityAccessories 72-78）、**配饰染料**（accessoryDyes 82-88）。这三个字段 `GET /nextbot/users/{user}/appearance` 已返回。渲染机制**全部从反编译的 Terraria 二进制逆向**（draw 图层顺序、各配饰类别的取帧/位置/偏移、slot→贴图映射、社交覆盖规则、染料、hideVisuals），不猜测。

## What I already know

### API 数据（已返回）
- `accessories` / `vanityAccessories` / `accessoryDyes`：各定长 7，元素 `{netId,stack,prefixId}`，空槽零值。
- `appearance.hideVisuals`：`Player.hideVisibleAccessory[10]` 位掩码（玩家手动隐藏的配饰）。

### 配饰类别与 draw 图层（LegacyPlayerRenderer 顺序，已勘查）
11 个配饰 draw 层（按前后顺序）：`09_Wings → 10_BackAcc → 11_Balloons → 14_Shoes → 18_OffhandAcc → 19_WaistAcc → 20_NeckAcc → 22_FaceAcc → 25_Shield → 29_OnhandAcc → 32_FrontAcc`，外加 beard（在 head 组）。
- 每个配饰物品按 `item.*Slot` 字段归类：`wingSlot`(47)/`backSlot`(20)/`balloonSlot`(14)/`shoeSlot`(27)/`handOffSlot`(18)/`waistSlot`(17)/`neckSlot`(12)/`faceSlot`(18)/`shieldSlot`(8)/`handOnSlot`(27)/`frontSlot`(6)/`beardSlot`(1)。无视觉 slot 的配饰不绘制。
- 贴图：`Wings_*`(51)/`Acc_HandsOn_*`(45)/`Acc_Back_*`(39)/`Acc_Shoes_*`(30)/`Acc_HandsOff_*`(28)/`Acc_Face_*`(23)/`Acc_Balloon_*`(19)/`Acc_Waist_*`(16)/`Acc_Front_*`(16)/`Acc_Neck_*`(12)/`Acc_Shield_*`(9)/`Acc_Beard_*`(4)（在 `Content/Images` 及 `Accessories/` 子目录）。

### 现有渲染模块（待扩展）
- `compositor.py`（图层合成、变体 fallback、装备/装饰/染料、长袍扩展、hairDye）；`dye.py`/`dye_noise.py`（染料 shader，可复用到配饰染料）；`data/*.json`（查表）；`assets/`（预提取 PNG，需补配饰贴图）；`_build/`（提取 + 生成脚本）。

## Open Questions
* **类别范围**：渲染全部 12 个可见配饰类别，还是优先高频类（翅膀/背饰/气球/鞋/手部）？
* 社交配饰覆盖功能配饰的**精确规则**（逐槽？整组？）—— 待逆向确认。
* 动画配饰（翅膀拍动、气球浮动）取 idle 帧。

## Requirements (evolving)
* 解析 accessories / vanityAccessories / accessoryDyes 三组（各 7 槽），每个 netId → 视觉类别 + slot → 贴图。
* 社交配饰覆盖功能配饰（按逆向规则）；`hideVisuals` 位掩码隐藏的配饰不绘制。
* 各配饰按其 draw 层的**逆向位置/帧/顺序**合成；配饰染料逐槽复用 `apply_dye`。
* 渲染范围与机制全部以反编译源为准（`temp/decomp/full`：`PlayerDrawLayers`、`Item.cs` slot 字段、`Player` 的 accessory/vanity/hide 逻辑）。

## Acceptance Criteria (evolving)
* [ ] 装了翅膀/背饰/气球/鞋/腰/颈/脸/盾/手部/前饰/胡须的角色，立绘按游戏内位置/层序显示。
* [ ] 社交配饰正确覆盖功能配饰；hideVisuals 隐藏的不显示。
* [ ] 配饰染料逐槽生效（复用现有染料 shader）。
* [ ] 渲染模块仍不依赖 NoneBot；ruff/pyright/测试通过。
* [ ] 提供手动测试任务（含各类别物品 netID + 染料 + 社交覆盖 + 隐藏用例）。

## Out of Scope
* 动画配饰逐帧动图（取 idle 代表帧）。
* 非视觉配饰（无 *Slot 的功能饰品）。
* 坐骑/宠物/光环等非 accessory 槽实体。

## Technical Notes
* 反编译源 `temp/decomp/full`：`Terraria.DataStructures/PlayerDrawLayers.cs`（各 DrawPlayer_NN_*Acc 的纹理/帧/位置/偏移/顺序）、`Terraria/Item.cs`（*Slot 字段全表）、`Terraria/Player.cs`（`hideVisibleAccessory`、社交/功能合并、`GetColor`/dye 槽）、`ArmorIDs`（各 *.Sets：DrawInForeground、动画帧数等）。
* 现有规格：`research/terraria_render_spec.md`、`dye_passes_spec.md`、`noise_dyes_spec.md`、`hairdye_spec.md`、`robe_extension_spec.md`。
* 染料：accessoryDyes 复用 `dye.apply_dye`（同 GameShaders.Armor 体系）。
</content>
