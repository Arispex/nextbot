# Research: 手部复合图 / 盾 / 鞋 — 帧 / cell / 层序正确性只读审计

- **Query**: 审计 draw_acc_hand（手部 360×224 复合图取 cell）、盾、鞋 的帧 / cell / 层序是否与逆向一致；找"鞋子那种 bug"（取错 cell / 漏帧 / 被覆盖 / 不显示）。
- **Scope**: internal（repo compositor vs 逆向 decomp）
- **Date**: 2026-06-04
- **逆向源**: `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs`、`temp/decomp/full/Terraria.DataStructures/PlayerDrawSet.cs`、`temp/decomp/full/Terraria/Player.cs`、`temp/decomp/full/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs`
- **repo**: `nextbot/terraria_render/compositor.py`

---

## 一句话结论

**手部复合图的 cell 数学完全正确**（前臂 cell 2 → grid (col2,row0)，后臂 cell 20 → grid (col2,row2)，与逆向 idle `compFrontArmFrame`/`compBackArmFrame` 逐像素一致，没有"取错 cell / 漏掉某只手"）；但发现 **2 个 P1 层序/可见性 bug**（后手配饰被身体甲背臂袖覆盖、鞋 accessory 在特定腿甲下应隐藏却仍画）和 **2 个 P2**（前饰前半片 vs 盾层序反、轮滑鞋 +2px 偏移缺失），都属于"画出来的层取的层序/可见性不对"这一类。

---

## P0 / P1 清单

- **P0**: 无。
- **P1-A 后手配饰(handOff) 与身体甲背臂的层序反了** → 穿身体甲时后手配饰会被甲的背臂袖覆盖（应在其之上）。`compositor.py:690-693`。
- **P1-B 鞋 accessory 缺 `ShouldOverrideLegs` 抑制** → 穿特定腿甲（legs ∈ {55,63,67,106,138,140,143,217,222,226,228}）时游戏不画鞋配饰，repo 仍无条件画 → 多出一只不该出现的鞋。`compositor.py:702-706`。

---

## Findings 表

| # | Severity | 症状 | 逆向证据 (file:line) | repo 证据 (compositor.py:line) | 建议修法 |
|---|---|---|---|---|---|
| H1 | ✅ 正确 | 手部复合图 cell 取对：前臂 (col2,row0)、后臂 (col2,row2) | `PlayerDrawSet.cs:1894`(num=bodyFrame.Y/H=0)→`1916-1921`(case0: frameIndex2.X=2,Y=0)→`1997-1998`(frameIndex.X=2,Y=0+2=2)→`2009-2010`+`2205-2208`(`CreateCompositeFrameRect`= `Rect(X*40,Y*56,40,56)`)；idle 时 `compositeFrontArm/BackArm.enabled=false`(`Player.cs:42714-42715` 每帧重置，仅 item use/hold 置 true)，故 `UpdateCompositeArm`(`2210-2234`) 不覆盖 | `draw_acc_hand` `471-480` 调 `_frame(name,cell)` `136-150`：cols=360//40=9；cell=2→cx=80,cy=0=(col2,row0)；cell=20→cx=(20%9=2)*40=80,cy=(20//9=2)*56=112=(col2,row2) | 无需改 — 与逆向逐像素一致 |
| H2 | ✅ 正确 | 复合贴图维度 = 360×224（=`AccHandsOnComposite`/`AccHandsOffComposite`），不是 40×1120 legacy | 复合 draw 用 `AccHandsOffComposite[handoff]`(`1423`) / `AccHandsOnComposite[handon]`(`3830`) | 资产实测：`Acc_HandsOn_*.png` / `Acc_HandsOff_*.png` 全部 360×224（IHDR 实读 1/2/12/24/15 号均 360×224） | 无需改 |
| H3 | ✅ 正确 | HandOn 画前臂、HandOff 画后臂，分别落在 FRONT / BACK 臂组 | handOff 在 `DrawPlayer_12_SkinComposite_BackArmShirt` 末尾 `1421-1428`(BACK 臂组)；handOn 在 `DrawPlayer_17_Torso` 前臂复合末尾 `3828-3835`(FRONT 臂组) | handOff: `688-692`（back_arm 组）；handOn: `780-792`（front_arm 组） | 无需改（组归属正确） |
| **H4** | **P1** | **后手配饰被身体甲背臂袖覆盖** | 游戏 BACK 臂组内顺序：身体甲背臂 `1365` → (coat `1416`) → **handOff acc `1421`**。即 handOff 在身体甲背臂**之上** | repo 顺序：`draw_acc_hand(HandsOff, back_arm)` `690-692` → **`draw_armor(armor_body,"back_arm")` `693`**。handOff 被画在身体甲背臂**之下**（反了） | 把 690-692 的 handOff draw 移到 693 `draw_armor(...,"back_arm")` **之后**（仍在 balloonFront 696 之前/或之后按需，但必须在身体甲背臂之后） |
| H5 | ✅ 正确（边角注） | 非复合 `DrawPlayer_18/29` 是 dead path | `18_OffhandAcc` 受 `!usesCompositeBackHandAcc` 守卫(`2046`)、`29_OnhandAcc` 受 `!usesCompositeFrontHandAcc` 守卫(`3840`)；`HandOff/HandOn.Sets.UsesNewFramingCode` 对全部 vanilla 槽=true(`1879-1880`)，故非复合分支对 vanilla 永不触发 | repo 只走复合 `draw_acc_hand`，不实现非复合 strip | 无需改 |
| H6 | P2（边角） | 理论双画：`usesCompositeTorso=true` 且某 handoff/handon **不** UsesNewFramingCode 时，复合 draw(`1421`/`3828`，不受 NewFramingCode 守卫，仅受 `usesCompositeTorso`)与 18/29 会同时画 | `1421`/`3828` 仅判 `handoff/handon>0`，不判 NewFramingCode；`2046`/`3840` 判 `!usesComposite*HandAcc` | repo 只画一次（复合） | vanilla 全 UsesNewFramingCode=true，不可达；无需改 |
| **S1** | ✅ 正确 | 盾用 bodyFrame（idle=顶帧）、整条贴图宽（含 44px 宽盾），单帧 idle | `DrawPlayer_25_Shield` `3066`(bodyFrame)；`3069-3077`宽度≠时把 bodyFrame.Width 设为贴图宽；base draw `3093`；`shieldRaised` 脉冲/格挡(`3079-3121`)仅战斗态(idle=false) | `draw_acc_strip(Acc_Shield_*)` `429-442` → `_acc_strip_frame` `172-183` 取 `sheet[:56,:w]` 全宽顶帧 | 无需改（盾 9 号实测 44×1120，全宽顶帧已覆盖） |
| **S2** | P2 | 前饰前半片(FrontAcc_FrontPart) vs 盾 层序反 | 正常分支：`25_Shield` `LegacyPlayerRenderer.cs:230` 在 `32_FrontAcc_FrontPart` `241-244` **之前** → 盾在前饰前半片**之下** | repo：front-acc front-half `795-797` → 盾 `798-800` → 盾在前饰前半片**之上**（反了） | 仅当同时有 front 饰 + 盾时可见；如要精确：把 798-800 盾 draw 移到 795-797 前半片**之前** |
| S3 | ✅ 正确 | 盾不被前臂/身体覆盖（盾层 25 在前臂组 17 之后） | 盾 `230` 在 `17_Torso`(前臂含其中) `207` 之后 | repo 盾 `798-800` 在前臂组 `780-792` 之后 | 无需改（盾在前臂之上，符合） |
| **Sh1** | ✅ 正确 | 鞋三分支层序：无腿甲/有腿甲/长袍 都对 | `LegacyPlayerRenderer.cs:194-203`：`wearsRobe&&body!=166` → Shoes(14) 先、Leggings(13) 后（鞋在下）；else → Leggings 先、Shoes 后（鞋在上） | `722-731`：`wears_robe` 分支 `draw_shoe_acc()`→`draw_leggings()`；else 反之。`_WEARS_ROBE_BODIES`(`71-74`)与逆向一致，body166 故意排除 | 无需改 |
| Sh2 | ✅ 正确 | GlassSlipper 男 25→女 26 remap | `MaleToFemaleID` CreateIntSet(-1,25,26)（spec 引 ArmorIDs.cs:1841） | `_SHOE_MALE_TO_FEMALE={25:26}` `64`，应用于 `602-604`（`not male` 时 remap） | 无需改 |
| Sh3 | ✅ 正确（仅帧/位置） | FlameWaker 22/23 帧 / 位置与普通鞋一致（仅 dye shader 不同） | `DrawPlayer_14_Shoes` `1762-1765`：22/23 只把 shader 换成 `cFlameWaker`，texture/frame/offset 不变 | repo 走同一 `draw_acc_strip`（顶帧），dye 另查 | 无需改（dye 在 dye_passes_spec 范畴） |
| **Sh4** | **P1** | 鞋 accessory 缺 `ShouldOverrideLegs_CheckPants` 抑制 → 特定腿甲下应隐藏却仍画 | `DrawPlayer_14_Shoes` 守卫 `!ShouldOverrideLegs_CheckPants`(`1758`)；`CheckPants` `1218-1241` 对 legs∈{55,63,67,106,138,140,143,217,222,226,228} 返回 true（且该腿无 CheckShoes 特例时）→ 鞋**不画** | `702-706` 无条件 `draw_acc_strip(Acc_Shoes_*)`，不看 `armor_legs` 是否在该集合 | 在 `draw_shoe_acc()` 内加守卫：若当前显示腿甲槽 ∈ {55,63,67,106,138,140,143,217,222,226,228} 则跳过（注意 `CheckShoes`：shoe==15 时 CheckPants 提前返回 false，鞋仍画 — 见 Sh6） |
| **Sh5** | P2 | 轮滑鞋 27-30 缺 +(0,2) Y 偏移 | `Player.GetShoeDrawOffset()` `Player.cs:4729-4737`：shoe∈{27,28,29,30} → `(0,2)*Directions`（direction=1 即下移 2px） | `draw_acc_strip` `429-442` / `706` 不传 ly 偏移（`_over_cell` 默认 (0,0)） | shoe∈{27,28,29,30} 时下移 2px（`_over_cell(frame, 0, 2)`）；2px 视觉影响小 |
| Sh6 | P2（边角） | shoe==15 不触发腿覆盖（CheckShoes 让 CheckPants 早退 false），鞋仍画 | `CheckPants` `1220-1222` 先调 `CheckShoes`；`CheckShoes` `1243-1251` shoe==15→true → CheckPants 返回 false → 鞋**照画** | repo 照画（巧合正确） | 实现 Sh4 守卫时务必：shoe==15 → 不抑制（与 CheckShoes 短路一致） |

---

## 手部 cell 选择对照表（核心）

idle 站立、direction=1、无手持物、`compositeArm.enabled=false`：

| 部位 | 逆向 frameIndex (X=col, Y=row) | 逆向 source rect = `Rect(X*40, Y*56, 40, 56)` | grid 格(行,列) 9×4 | repo cell 值 | repo `_frame` 算出的 (cx,cy) | grid 格 | 一致? |
|---|---|---|---|---|---|---|---|
| 前臂 (HandOn) | X=2, Y=0 | (80, 0, 40, 56) | row0, col2 | `front_arm`=2 (`variants.json idle_cells`) | (2%9)*40=80, (2//9)*56=0 | row0, col2 | ✅ |
| 后臂 (HandOff) | X=2, Y=2 | (80, 112, 40, 56) | row2, col2 | `back_arm`=20 (`variants.json idle_cells`) | (20%9)*40=80, (20//9)*56=112 | row2, col2 | ✅ |

**推导链（逆向）**：
1. `num = bodyFrame.Y / bodyFrame.Height` —— idle 站立 `bodyFrame.Y=0` → `num=0`（`PlayerDrawSet.cs:1894`）。
2. `switch(num) case 0:` → `frameIndex2.X=2`（前臂 col），`frameIndex2.Y` 默认 0（`1918-1921`）。
3. `frameIndex.X = frameIndex2.X = 2`；`frameIndex.Y = frameIndex2.Y + 2 = 2`（后臂，`1997-1998`）。
4. `UpdateCompositeArm(compositeFrontArm,…,frameIndex2,7)` 与 `(compositeBackArm,…,frameIndex,8)`（`1999-2000`）：仅当 `data.enabled` 才覆盖为 (targetX, stretch行)。idle 时 enabled=false（`Player.cs:42714-42715` 每帧 `AnimatePlayerAndGetItemFrame` 开头重置），所以**不覆盖**，保留 (2,0) / (2,2)。
5. `compFrontArmFrame = CreateCompositeFrameRect((2,0)) = Rect(80,0,40,56)`；`compBackArmFrame = CreateCompositeFrameRect((2,2)) = Rect(80,112,40,56)`（`2009-2010` + `2205-2208`）。

**repo 实现**：`draw_acc_hand(name, cell, dye)` (`compositor.py:471-480`) 直接用 `comp.cells["front_arm"]=2` / `comp.cells["back_arm"]=20`（`376-379`，源自 `data/variants.json` `idle_cells: {front_arm:2, back_arm:20}`），交给 `_frame` 按 9 列 grid 切片。两者落到**同一格**。**没有取错 cell、没有漏手**。

> 注：repo 用 cell=2 / cell=20 这套"线性 cell 编号"恰好等价于逆向的 (col,row) 二维索引，因为 9 列 grid 下 `2 = row0·9+2`、`20 = row2·9+2`。身体臂层（`draw_player(7,...)`）也用同一 cell（terraria_render_spec §C 已记 front-arm cell 2 / back-arm cell 20），所以手配饰与身体臂逐像素对齐。

---

## 层序总览（逆向权威序，`LegacyPlayerRenderer.DrawPlayer_UseNormalLayers` 162-258）

仅列与手/盾/鞋相关的：

```
12_Skin            ← 内含 BackArmShirt：身体甲背臂(1365) → handOff复合acc(1421)   ← H4 反序点
13_Leggings / 14_Shoes  ← wearsRobe 分支决定先后(194-203)                          ← Sh1 ✓
17_Torso           ← 内含前臂复合：前臂甲/皮 → handOn复合acc(3828)
18_OffhandAcc(208) ← dead(非复合, vanilla 不触发)
...21_Head, 22_FaceAcc...
25_Shield(230)                                                                      ← S2/S3
29_OnhandAcc(239)  ← dead(非复合)
32_FrontAcc_FrontPart(243, 正常分支)  ← 在盾之后 → 前饰前半片在盾之上              ← S2 反序点
```

repo 对应序（`compositor.py` render_character 667-800）：
```
back_arm: draw_player(7/5) → handOff(690) → armor_body back_arm(693)              ← H4: handOff 与 693 反了
torso/leg: leggings/shoe(722-731)                                                  ← Sh1 ✓ / Sh4 缺抑制 / Sh5 缺27-30偏移
head/face...
front_arm: draw_player(7)/armor → handOn(788-792)
front-acc front-half(795-797) → shield(798-800)                                    ← S2: 与逆向反
```

---

## Caveats / Not found

- **本审计只看 idle 站立 direction=1 单帧的"帧/cell/层序/可见性"**，不含 dye/染料正确性（Sh3 的 FlameWaker、各 shader 编号属 `dye_passes_spec.md` / `noise_dyes_spec.md`）。
- **资产齐全性**按任务说明视为已查无缺，未重复核对每个槽位文件是否存在；仅抽样核对了维度（HandsOn/HandsOff=360×224、Shield/Shoes=40×1120，Shield 9=44×1120）。
- **H6/Sh6 为不可达/巧合正确的边角**，列出仅为修 Sh4/H4 时避免引入回归。
- **未深查**：`isSitting`/mount/`shieldRaised`/`drawFrontAccInNeckAccLayer` 等非 idle-display-doll 分支（display doll 恒 `isSitting=false`、无 mount、`shieldRaised=false`）。`drawFrontAccInNeckAccLayer`（颈饰层画前饰）由 `bodyFrame.Y/H==5 && DrawsInNeckLayer[front]`(`PlayerDrawSet.cs:1767`)触发，idle 时 `bodyFrame.Y/H=0≠5`，故 display doll 走正常分支(`241-244`)，S2 结论成立。
- 现有 `research/accessories_spec.md`（line 256-262, 414-425, 697-720）已记录正确的 cell 映射，但其草拟的层序清单把"前饰前半片"列在盾之前（line 718-720）——与逆向 230/243 不符（同 S2），且未捕捉 H4 的组内反序；本审计在其基础上补正。
