# Audit: 身体本体 + head/body/leg 装备 — 帧 / cell / 层序 / 性别 正确性 (idle, direction=1)

- **Scope**: 只读审计（不改代码）。专查**身体本体（skin head/body/leg、eyes/eyewhites/eyelid、undershirt/shirt/pants/默认shoes）+ head/body/leg 装备（含 body armor 子部件 torso/前臂/后臂/前肩/后肩、glowmask、hair 遮挡、社交装备覆盖）**的「取对帧 / 对 cell / 对层 / 性别对」。**不查**：配饰/手部/盾/鞋 accessory（另两份 audit 已覆盖）、染料正确性（dye_passes/noise_dyes spec）。
- **逆向为准**: `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs`（draw 方法）、`PlayerDrawSet.cs`（composite 帧/cell + hair 帧）、`Terraria/Player.cs`（帧来源 / GetHairSettings / UpdateVisibleAccessory）、`Terraria.GameContent/PlayerEyeHelper.cs`（眼皮帧）、`Terraria.ID/ArmorIDs.cs`（Body.Sets）、`Terraria.Initializers/AssetInitializer.cs`（贴图路径）、`Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs`（master 层序）。
- **repo**: `nextbot/terraria_render/compositor.py`、`_build/extract_assets.py`、`data/variants.json`。
- **Date**: 2026-06-04

---

## 一句话结论

**身体本体与 body/head/leg 装备的「帧 / cell / 层序 / 性别」核心全部正确** —— idle 取顶帧（bodyFrame.Y=0 / legFrame.Y=0 / 眼皮 EyeOpen=frame0），composite 五子部件 cell（torso male0/female18、前臂2、后臂20、前肩9/27、后肩10/28）与逆向 `CreateCompositeData` 逐 cell 一致，female +2 行位移正确，社交装备 head/body/legs 覆盖与 `UpdateVisibleAccessory` 一致。**唯一成规模的「漏画」是 glowmask 发光层全缺**（composite body 的 Y+224 发光条 + 腿/头的独立 GlowMask 贴图都没提取、没画），以及**皮肤隐藏门（hidesTopSkin/hidesBottomSkin/IsBottomOverridden）未实现**（少数遮罩型甲/腿会露出本不该显示的裸皮）。我**排除**了若干「看似漏子部件」的疑点（后臂袖、前/后肩裸皮子部件、前臂 layer6）—— 经逐 cell 像素核对，这些 cell 在皮肤/布料 sheet 里本就是空的或与已画层完全重合，**无视觉影响**。

### P0
- 无。（"鞋 bug 同类"的层序错全在配饰侧，已由 `audit_frames_hands_shield_shoes.md` / `audit_frames_back_overbody.md` 覆盖；身体本体/装备子部件这一侧没有取错帧/错 cell/性别 cell 错/vanity 覆盖错。）

### P1
- **P1-A body armor glowmask 发光层全缺**：~11 个 composite body 槽（Nebula 227、Solar 188?→见表、Vortex、Stardust、Arkhalis 208、238/260、239、194、179、190、176、205）贴图是 360×**448**，发光像素在**下半 (sourceRect.Y += 224)**，逆向 `DrawCompositeArmorPiece` 对 torso/臂/肩各画一遍发光，**repo 完全不画** → 这些套装渲染偏暗、缺发光描边。`compositor.py` 全文无任何 glow 处理。
- **P1-B 皮肤隐藏门未实现（hidesTopSkin / hidesBottomSkin / IsBottomOverridden）**：repo `draw_player(3,"torso")`（躯干皮）与 `draw_player(10,"col")`（腿皮）**无条件画**；逆向对 body∈{21,22,82,83,93} 隐藏躯干皮、对 legs∈{20,21,214,215,216} 或 body93 隐藏腿皮、对 legs∈{55,63,67,106,138,140,143,217,222,226,228}∪shoe15 隐藏腿皮（`IsBottomOverridden`）→ 穿这些遮罩型甲/裙/美人鱼腿时，裸皮会从甲的空洞处透出（不该出现）。`compositor.py:699`（躯干皮）/`:700`（腿皮）。

### P2
- **P2-A 腿/头 glowmask（独立 GlowMask 贴图）也全缺**：与 P1-A 同源但走**独立 `TextureAssets.GlowMask[id]` 贴图**（非同 sheet Y+224）：腿 legs∈{111,157,158,210,222,225,226,110,134,130}、头一长串特殊盔。repo 无 GlowMask 资产、无引用。影响面比 body 窄（多为特殊套/盔）。
- **P2-B `UseSkinColor` 头盔（head∈{274,277}）应按肤色画**：逆向用 `colorHead + skinDyePacked` 取代 `colorArmorHead + cHead`（`PlayerDrawLayers.cs:2145-2149`）；repo `draw_armor(armor_head,...)` 一律 untinted+head_dye → 这 2 个盔颜色错（应随肤色）。`compositor.py:768`。

---

## Findings 表

| # | Severity | 症状 | 逆向证据 (file:line) | repo 证据 (compositor.py:line) | 建议修法 |
|---|---|---|---|---|---|
| **B1** | ✅ 正确 | composite body 五子部件 cell 全对（torso/前臂/后臂/前肩/后肩） | `PlayerDrawSet.cs:1889-1891`(pt/pt2/pt3 初值) + `1918-1920`(num=0: frameIndex2.X=2) + `1997-1998`(frameIndex=(2,2)) + `2007-2011`(pt=(1,1)/pt2=(0,1)/pt3=(0,0)) + `CreateCompositeFrameRect` `2205-2208`(`Rect(X*40,Y*56,40,56)`) | `variants.json idle_cells`: torso{m0/f18} 前臂2 后臂20 前肩{m9/f27} 后肩{m10/f28}（`compositor.py:341-345,376-382`）；`_frame` 9 列 grid 切片(`136-150`) | 无需改 — 逐 cell 数值一致（见对照表） |
| **B2** | ✅ 正确 | female +2 行位移：torso/前肩/后肩 +2 行，**臂不位移** | `PlayerDrawSet.cs:2001-2006`：`if(!Male){pt.Y+=2; pt2.Y+=2; pt3.Y+=2;}`（仅肩+躯干；frameIndex/frameIndex2 臂帧不变） | `_cell(...,male)` 对 torso/肩取 female 值（18/27/28），front_arm/back_arm 是标量 2/20 不分性别(`343-344`) | 无需改 — 与逆向一致（臂确实不分性别行） |
| **B3** | ✅ 正确 | body armor 走 **composite 360×224 grid**（不是 legacy 40×1120 `ArmorBody`/`ArmorArm`/`FemaleBody`） | `ArmorIDs.cs:673` `UsesNewFramingCode` 对全 vanilla body(1..261)=true → `PlayerDrawSet.cs:1878` `usesCompositeTorso=true` → 用 `ArmorBodyComposite[body]`(`PlayerDrawLayers.cs:1348/2005/3700` 等)，路径 `Images/Armor/Armor_{slot}`(`AssetInitializer.cs:475`) | `extract_assets.py:70,115-121` `BODY_PATTERN` 从 `Armor/` 子目录的 `Armor_{slot}.xnb` 取 → `ArmorBody_{slot}.png`（实测 360×224）；`draw_armor` 按同套 cell 切片(`400-412`) | 无需改 — repo 取的正是 composite sheet，性别也不靠 FemaleBody（靠 cell +2 行）|
| **B4** | ✅ 正确 | torso/前臂/后臂/前肩/后肩 **5 个子部件都画** + 层序穿插对（后臂在身后、前臂在身前、肩在臂上） | BACK 组 `12_SkinComposite_BackArmShirt`：后肩(`1351`)→后臂(`1365`)；前组 `28_ArmOverItemComposite`：num=0→`compShoulderOverFrontArm=true`(`1918`无 false 改写)→ 前肩(num2=1)在前臂(num3=0)**之后**画(`3692-3694,3720-3757`)。master 序 BACK 组(12) 早于 torso(17) 早于前臂组(28) | repo：后臂组 `draw_armor(body,"back_arm")`(`693`) + 后肩 `draw_armor(body,"back_shoulder")`(`743`)；torso `draw_armor(body,"torso")`(`742`)；前臂组 `draw_armor(body,"front_arm")`(`783`)→前肩(`784`，在前臂后=肩在臂上) | 无需改（5 子部件齐、肩在臂上、前后臂分属前后组）。**注**：后肩 repo 画在 BODY+LEG 组(743) 而非 back-arm 组——但都在 torso(742) 前、前臂组(781) 前，且后肩 cell 内容独立，视觉等价 |
| **B5** | **P1** | **body armor glowmask 发光层全缺**（composite 同 sheet Y+224 发光条） | `DrawCompositeArmorPiece`：torso `81-104`、臂/肩 `54-77` 各把 `sourceRect.Y += 224` 再画一遍发光（仅当 `bodyGlowColor`/`armGlowColor`≠0）。`PlayerDrawSet.cs:638-741` 设非零 glow 的 composite body：208/227/237/238/260/239/190/176/194/179/205（实测这些 `ArmorBody_*.png` 均 360×**448**） | `compositor.py` 全文无 glow（grep `glow` 0 命中）；`_frame` 只取上半 224 的 base cell | 对这些 body 槽：base cell 之外，再按 `cell+36`（Y+224=+4 行）取发光 cell，用 `bodyGlowColor`/`armGlowColor`(需补 PlayerDrawSet 的颜色推导)叠加。需先判定该 sheet 高≥448 才有发光条 |
| **B6** | **P1** | **皮肤隐藏门未实现**：躯干皮(3)/腿皮(10) 无条件画 | 躯干皮门 `hidesTopSkin`(body∈{82,83,93,21,22}, `PlayerDrawSet.cs:1755`)；腿皮门 `!hidesBottomSkin && !IsBottomOverridden`（`PlayerDrawLayers.cs:1285`/`1193`）。`hidesBottomSkin`=legs∈{20,21,216,214,215}∨body93(`1756`)；`IsBottomOverridden`=`CheckPants`(legs∈{55,63,67,106,138,140,143,217,222,226,228})∨`CheckShoes`(shoe==15)(`1205-1251`) | `draw_player(3,"torso")`(`699`) / `draw_player(10,"col")`(`700`) 无任何条件 | 躯干皮：body∈{82,83,93,21,22}→跳过。腿皮：legs∈{20,21,214,215,216}∨body93→跳过；legs∈{55,63,67,106,138,140,143,217,222,226,228}∨shoe15→跳过（shoe15 短路见 `audit_..._shoes.md` Sh6）|
| B7 | P2 | 腿/头 glowmask（独立 GlowMask 贴图）缺 | 腿：`PlayerDrawLayers.cs:1556-1574` 画 `GlowMask[legsGlowMask]`，legs∈{111,157,158,210,222,225,226,110,134,130}(`PlayerDrawSet.cs:742-802`)。头：`headGlowMask`(`PlayerDrawSet.cs:519-636` 一长串特殊盔) | repo 无 GlowMask 资产、无引用 | 需新增 `GlowMask_{id}.png` 资产 + 对应 draw；影响面窄（特殊套/盔），可低优先 |
| B8 | P2 | `UseSkinColor` 盔(274/277) 应按肤色画 | `PlayerDrawLayers.cs:2145-2149`（fullHair 分支）/`2223-2227`（flag 分支）：`UseSkinColor[head]`→color=colorHead, shader=skinDyePacked。`ArmorIDs.cs:16` `UseSkinColor={274,277}` | `draw_armor(armor_head,"col",head_dye)`(`768`) 一律 untinted白+head_dye | head∈{274,277}→改用肤色 tint（同 skin）。仅 2 槽 |
| **H-skin** | ✅ 正确 | head skin(0)/eyewhites(1)/eyes(2) idle 帧 + tint | `TheFace` 裸脸分支 `2613-2622`：`Players[var,0]`colorHead+skinDye、`[var,1]`colorEyeWhites、`[var,2]`colorEyes，全用 `bodyFrame`(idle Y=0)。贴图 40×1118 单列 | `draw_player(0/1/2,"col")`(`761-763`)；`_LAYER_TINT{0:skin,1:None,2:eye}`(`122-126`)；col sheet→顶帧(`136-150`) | 无需改（帧/tint 对，1=eyewhites 不 tint 对）|
| **Eyelid** | ✅ 正确（边角注） | 眼皮(15) idle 取 EyeOpen=frame0；col sheet 顶帧 | `TheFace_Eyelid` `2640-2668`：`val.Frame(1,3,0,frameY)`，`frameY=eyeHelper.EyeFrameToShow`。`PlayerEyeHelper.cs:39-72`：NormalBlinking 态绝大多数 tick=EyeOpen(0)，仅 `_timeInState%240∈[234,240)` 眨眼 | `draw_player(15,"col")`(`764`)；`Player_0_15`=40×168(3 帧列)→`_frame` `w<=40` 强制 cell0=顶帧=EyeOpen；`_LAYER_TINT{15:skin}` | 无需改（idle 静态取 EyeOpen 是正确代表帧；眨眼是时变动画，display doll 取 open 合理）|
| **L-skin** | ✅ 正确 | 腿皮(10)/默认裤(11)/默认鞋(12) idle 帧 + tint（受 B6 隐藏门约束） | 腿皮 `12_Skin_Composite:1293`(legFrame, colorLegs)；默认裤/鞋 `13_Leggings` 末支 `1576-1582`(`Players[var,11]`colorPants + `[var,12]`colorShoes, legFrame)。均 40×1120 单列顶帧 | 腿皮 `draw_player(10,"col")`(`700`)；裤/鞋 `draw_player(11/12,"col")`(`713-714`，仅无腿甲时)；`_LAYER_TINT{10:skin,11:pants,12:shoe}` | 帧/tint/层序对；唯缺 B6 隐藏门 |
| **SkinCoat15** | ✅ 正确 | skin long-coat(14) 仅 skinVar∈{3,7,8} 且无 body armor 时画 | `15_SkinLongCoat:1779`：`(skinVar==3||8||7) && body<=0 && !invis` → `Players[var,14]`(legFrame, colorShirt) | `draw_player(14,"col")` 仅 `if not armor_body`(`733-734`)；`_LAYER_TINT{14:shirt}`；var≠3/7/8 时 `Player_X_14` 不存在→`_resolve_player`返回 None 自动跳过 | 无需改（按 sheet 存在性天然 gate 了 skinVar；tint=shirt 对）|
| **Hair-mode** | ✅ 正确 | hair 遮挡 fullHair/hatHair/backonly/none + 前后 pass | `GetHairSettings` `Player.cs:16661-16791`：fullHair / hatHair / drawsBackHairWithoutHeadgear(head0/259) / hideHair(PreventHairDraw 或 faceHead)。后发 `01_BackHair` 画于 `head==-1∨fullHair∨drawsBackHairWithoutHeadgear`(`PlayerDrawLayers.cs:204`)，gate=`backHairDraw`(`PlayerDrawSet.cs:1750` 26px clip)。前发：fullHair(`2155`)∨hatHair(`2162`)∨裸头 else 分支(`2420-2424`) | `_hair_mode`(`289-298`) 用 `hair_sets.json` fullHair/hatHair/backonly；`_back_hair_style`(`301-307`) 精确移植 `backHairDraw` 谓词；`_resolve_hair`(`506-519`) draw_back/draw_front | 无需改（详见下方"hair 专项核对"，含裸头 head=None→"full" 的等价性证明）|
| **Hair-frame** | ✅ 正确 | hair 用 alt sheet（hatHair）vs 主 sheet；26px 前发 clip | `hatHair`→`PlayerHairAlt[hair]`(`2162`)；否则 `PlayerHair[hair]`(`2155`)。backHairDraw 时 `hairFrontFrame.Height=26`(`PlayerDrawSet.cs:1752-1753`)裁前发额部 | `_resolve_hair` hat→`Player_HairAlt_`，否则 `Player_Hair_`(`509-511`)；`draw_hair(clip_rows=_FRONT_HAIR_CLIP=26 if is_back)`(`80-81,766-767`) | 无需改 |
| **Vanity** | ✅ 正确 | head/body/legs 社交(armor10/11/12) 覆盖功能(armor0/1/2)，存在即覆盖 | `Player.cs:35331-35345`：`head=armor[0].headSlot; if(armor[10].headSlot>=0) head=armor[10].headSlot;`（body/legs 同构） | `_displayed_piece`(`348-355`)：vanity 有 netId 则覆盖 equipment（per-part）；`_resolve_armor`(`522-540`) 三件各独立解析 | 无需改（vanity 槽含 head/body/leg 件时其 slot≥0 ⟺ 有 netId，逻辑等价）|
| **NoArmor-arm** | ✅ 正确（排除疑点） | 无 body armor 时前臂袖（皮7/under8/shirt13）画对；后臂袖 under8/shirt13 缺但**cell 全空**无影响 | 后臂无甲 `1379-1404`：画 7+5(皮)+8(under)+13(shirt)@compBackArmFrame；前臂无甲 `3796-3803`：画 7(皮)+8+13+6@compFrontArmFrame | repo 后臂组画 7+5(`686-687`)；前臂组无甲画 7+8+13(`785-787`) | 无需改 — 实测 `Player_*_8`@后臂cell20=0px、`Player_*_13`@后臂=0px（全 variant），`Player_*_6`@前臂与 `13`@前臂**像素全重合**(both 同 colorShirt)→ 漏画 0 视觉差（见"排除的疑点"）|
| **NoArmor-sh** | ✅ 正确（排除疑点） | 无甲时前/后肩的皮/布子部件未画，但肩 cell 在皮肤/布料 sheet **全空** | torso 无甲 `17_TorsoComposite:2022-2025` 在 compBackShoulderFrame 画 4+6；前肩无甲 `3769-3778` 画 7+8+13+6@compFrontShoulderFrame | repo torso 无甲只画 4+6@torso(`745-746`)；前臂无甲不画肩子部件 | 无需改 — 实测 `Player_*_{4,5,6,7,8,13}` 在前肩(cell9)/后肩(cell10) **全 0px**（肩部仅作为"armor"存在）→ 漏画 0 视觉差 |
| **Torso-noarmor** | ✅ 正确 | 无 body armor 时躯干 under(4)+shirt(6) idle 帧+tint | `17_TorsoComposite:2024-2025`(无甲)：`[var,4]`colorUnderShirt + `[var,6]`colorShirt @compTorsoFrame；男女同（composite 不分支） | `draw_player(4,"torso")`+`draw_player(6,"torso")`(`745-746`)；`_LAYER_TINT{4:under,6:shirt}` | 无需改（实测 layer6@torso 有内容如 var0=232px，layer4@torso=0px 但画无害）|

---

## idle 子部件 帧 / cell 对照表（逆向 vs repo，逐 cell 数值核对）

idle 站立、direction=1、gravDir=1、`bodyFrame.Y=0`(`Player.cs:36209`)、`legFrame.Y=0`、`compositeArm.enabled=false`：

### A) body armor / 躯干皮 / 躯干布 五子部件（composite 360×224，9 列 grid，`cell=row*9+col`）

| 子部件 | 逆向 Point (col,row) | 逆向 `Rect(col*40,row*56,40,56)` | repo cell (variants.json) | repo `_frame` (cx,cy) | 一致? |
|---|---|---|---|---|---|
| Torso 男 | pt3=(0,0) | (0,0,40,56) | torso.male=0 | (0,0) | ✅ |
| Torso 女 | pt3=(0,2) `+2行` | (0,112,40,56) | torso.female=18 | (0,112) | ✅ |
| FrontArm | frameIndex2=(2,0) | (80,0,40,56) | front_arm=2 | (80,0) | ✅ |
| BackArm | frameIndex=(2,2) | (80,112,40,56) | back_arm=20 | (80,112) | ✅ |
| FrontShoulder 男 | pt2=(0,1) | (0,56,40,56) | front_shoulder.male=9 | (0,56) | ✅ |
| FrontShoulder 女 | pt2=(0,3) `+2行` | (0,168,40,56) | front_shoulder.female=27 | (0,168) | ✅ |
| BackShoulder 男 | pt=(1,1) | (40,56,40,56) | back_shoulder.male=10 | (40,56) | ✅ |
| BackShoulder 女 | pt=(1,3) `+2行` | (40,168,40,56) | back_shoulder.female=28 | (40,168) | ✅ |

> 该 8 行同样适用于躯干皮(layer3)、臂皮(5/7)、under(4/8)、shirt(6/13) —— 它们与 body armor 共享 360×224 composite 布局（实测 `Player_0_{3..9,13}` 全 360×224，9col×4row）。repo `draw_player(3,"torso")`/`draw_player(7/5,"back_arm")` 等用同套 cell。

### B) 列 sheet（40×宽=40，`_frame` 强制 cell=0=顶帧）

| 子部件 | layer | sheet 实测 | idle 帧 | repo | 一致? |
|---|---|---|---|---|---|
| head skin | 0 | 40×1118 (19行) | frame0 (bodyFrame.Y=0) | `draw_player(0,"col")` | ✅ |
| eye whites | 1 | 40×1118 | frame0 | `draw_player(1,"col")` | ✅ |
| eyes | 2 | 40×1118 | frame0 | `draw_player(2,"col")` | ✅ |
| leg skin | 10 | 40×1120 (20行) | frame0 (legFrame.Y=0) | `draw_player(10,"col")` | ✅(帧)/B6(门) |
| pants | 11 | 40×1120 | frame0 | `draw_player(11,"col")` | ✅ |
| default shoes | 12 | 40×1120 | frame0 | `draw_player(12,"col")` | ✅ |
| skin longcoat | 14 | (仅var3/7/8) | legFrame frame0 | `draw_player(14,"col")` | ✅ |
| eyelid | 15 | 40×168 (3帧) | `Frame(1,3,0,EyeFrameToShow)`，idle=EyeOpen=0 | `draw_player(15,"col")`→顶帧 | ✅ |
| head armor | — | 40×1120 | bodyFrame frame0 | `draw_armor(armor_head,"col")` | ✅ |
| leg armor | — | 40×1120 | legFrame frame0 | `draw_armor(armor_legs,"col")` | ✅ |

---

## hair 专项核对（fullHair / hatHair / backonly / none + 裸头等价性）

逆向 `GetHairSettings`(`Player.cs:16661-16791`) 三类可见性 + 后发谓词：
1. **fullHair**（一串戴盔仍露全发的 head：10,12,28,42,... `16670-16711`）：前发(`hairFrontFrame`)+后发都可能画，头盔也画。
2. **hatHair**（戴帽露"帽下发"：13,14,15,16,... `16712-16776`）：用 `PlayerHairAlt`。
3. 其余（含 head==0 裸头）：既非 fullHair 也非 hatHair。**前发**走 `DrawPlayer_21_Head` 末尾 `else if (flag4 && !invis && !PreventHairDraw)` 分支(`2420-2424`) 用 `PlayerHair[hair]` 画；**后发**走 `01_BackHair` 的 `drawsBackHairWithoutHeadgear`(head0/259)∨`head==-1` 条件(`204`)。
4. **hideHair**：`PreventHairDraw[face]`∨(`faceHead>0 && head!=0`)(`16778-16785`)。
5. **backHairDraw**（后发可见 + 前发 26px 额部裁切）：`num>50 && (num<56||num>63) && (num<74||num>77) && (num<88||num>89) && num!=94 && num!=100 && num!=104 && num!=112 && num<116`，外加强制 {6,133,134,146,162}(`16787-16791`)。

**repo 映射**（`_hair_mode` + `_resolve_hair`）：
- `head_slot in _FULLHAIR`→"full"（draw_front=T, draw_back=T if backHairDraw）。
- `head_slot in _HATHAIR`→"hat"（用 HairAlt）。
- `head_slot in _BACKONLY`→"backonly"（只 draw_back）。←对应 `drawsBackHairWithoutHeadgear`={0,259}。
- 其余 head_slot→"none"（不画发）。
- **`head_slot is None`（裸头/无 head armor）→"full"**(`290-291`)。

**裸头等价性证明**：repo 的「裸头=full」在功能上**正确**——
- 前发：repo "full" → draw_front=T；逆向裸头(head0/-1) 也画前发（`2420` else 分支，与 fullHair `2155` 同 `PlayerHair[hair]` + 同 `hairFrontFrame`）。两者前发都画、同 sheet、同帧。✅
- 后发：repo "full" → draw_back = backHairDraw；逆向裸头 `drawsBackHairWithoutHeadgear=T`(head0) → `01_BackHair`(`204`) 在 gate=backHairDraw 下画后发。✅ 
- 26px 前发裁切：两路都由 `_back_hair_style`/`backHairDraw` 控（repo `767` clip_rows=26 if is_back）。✅

> 唯一名义差异：repo 把裸头并入 "full" 而非单列 "backonly+裸前发"，但因 fullHair 与裸头分支的前/后发 sheet、帧、gate **完全相同**，结果一致。`hide_hair`(PreventHairDraw face / faceHead) 中 face PreventHairDraw 已由 `draw_front_hair = ... and acc_slots.get("face") not in _FACE_PREVENT_HAIR`(`664-665`) 覆盖；faceHead 属盔内字段(非 appearance faceSlot)，display doll 不涉及。

---

## 排除的疑点（逐 cell 像素核对，确认"看似漏画"无视觉影响）

> 这些是审计中**重点怀疑过的"鞋 bug 同类（漏子部件）"，但经实测 cell 内容为空或重合而排除**，列出以免后续 review 误报或误"修"出回归。

1. **后臂袖 under(8)/shirt(13) 未画**（repo 后臂组只 7+5）：逆向无甲后臂确画 8+13(`1401,1403`)，但实测 `Player_{var}_8`@back_arm(cell20)=0px、`Player_{var}_13`@back_arm=0px（**全 11 个 variant**）→ 这两 cell 在 sheet 里本就空（袖子只在前臂）。**漏画 = 0 像素差**。
2. **前/后肩裸皮/布子部件未画**（无甲时）：逆向在 compFront/BackShoulderFrame 画 4/6/7/8/13(`2022-2025,3769-3778`)，但实测 `Player_{var}_{4,5,6,7,8,13}` 在前肩(cell9)/后肩(cell10) **全 0px** → 肩部在皮肤/布料 sheet 无内容（仅 armor 有肩）。**漏画 = 0 像素差**。
3. **前臂 shirt layer6 未画**（repo 前臂只 7+8+13）：逆向前臂无甲画 6@compFrontArmFrame(`3803`)，实测 var{2,3,4,6,7,9} 的 `layer6`@前臂 与 `layer13`@前臂**像素 100% 重合**（only6=0/only13=0/overlap=full），且二者同用 `colorShirt` → 画 13 已等于画 13+6。**漏画 = 0 像素差**。
4. **后肩 repo 画在 BODY+LEG 组(743) 而非逆向的 back-arm 组**：两处都在 torso(742) 之前、前臂组(781) 之前，后肩 cell(10/28) 与其它 cell 不重叠，叠放顺序对最终像素无影响。

---

## Caveats / Not Found

- **本审计只覆盖 idle 站立 direction=1 gravDir=1 单帧的「帧/cell/层序/性别/漏画」**。不含：染料/shader 正确性（属 `dye_passes_spec.md`/`noise_dyes_spec.md`，B5 的 glow **颜色推导**也牵涉 `bodyGlowColor` 的逐 body 公式，未展开数值）、配饰/手部/盾/鞋 accessory（已由另两份 audit 覆盖）。
- **B5 glow 的精确修法**需移植 `PlayerDrawSet.cs:638-741` 的 `bodyGlowColor`/`armGlowColor` 逐 body 颜色（部分依赖 `Main.mouseTextColor`/`miscCounter` 等时变量；display doll 取代表值即可）；本审计只确认"发光条在 Y+224、repo 完全没画、受影响 body 槽贴图为 448 高"。
- **B6 hidesTopSkin/Bottom 的视觉幅度**取决于对应甲是否有透明洞：美人鱼腿(20/21)、遮罩 body(93) 这类整段替换的甲会明显露皮；普通甲因皮在甲下被覆盖，影响小。集合已逐一列出（`Player.cs:1755-1756` + `IsBottomOverridden` 1205-1251），与 `audit_..._shoes.md` 的 Sh4/Sh6（鞋 accessory 侧）同源但**作用对象不同**（此处是腿皮 layer10，那里是鞋 accessory）。
- **未深查的非 idle 分支**（isSitting/mount/invis/dead/babyBird/rabbitOrderFrame/各特殊 head 269/270/282/259/211/205 的独立帧逻辑、`armorAdjust` body 帧裁切）：display doll 恒 isSitting=false、无 mount、invis=false，且这些特殊 head 是单个稀有盔，主流程不触发；`armorAdjust` 仅 direction==-1 且特定 cape body 非零(`PlayerDrawLayers.cs:1935-1941`)，正面 idle direction=1 时其 X 偏移被 `if(direction==-1)num=0` 归零，对 bodyFrame 裁切也仅 cape body，普通 body 无影响。
- **`Body.Sets` 其它项**（`NeedsToDrawArm` `647`、`HidesShouldersAsCoat` 251、`showsShouldersWhileJumping`、`shouldersAreAlwaysInTheBack` 190、`DisableHandOnAndOffAccDraw` 83、`DisableBeltAccDraw` 83/82）：多数仅影响跳跃帧/coat 肩/特定 body 的配饰隐藏；idle 正面对 torso/臂/肩 cell 取值无影响（`showsShouldersWhileJumping` 仅 num=5 帧用，idle num=0 不涉及）。`HidesShouldersAsCoat`(251)/coat 相关已由 robe_extension（`_longcoat_ext_slot`）侧处理，本审计未交叉验证 coat 肩隐藏。**未发现** `bodyPartFrameOverride` 之类的 per-body 特殊帧覆盖集（该版 `Body.Sets` 无此字段；逐 body 特殊帧都在各 DrawPlayer 方法里按 `body==N` 硬编码，且均为非 idle / 稀有套）。
- **`Player_0_14` 缺失**为预期（skin longcoat 仅 var3/7/8 有），非资产缺漏。
