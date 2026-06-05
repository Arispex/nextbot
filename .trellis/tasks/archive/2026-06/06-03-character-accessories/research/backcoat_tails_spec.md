# Research: ArmorBackCoat / Tails / Backpacks / coat 装扮槽 实现级规格

- **Query**: 逆向 ArmorBackCoat 袍背片 / Tails 尾饰 / Backpacks 背包 / coat 装扮槽 的实现级规格，逐项判断能否从我方 API 数据推导
- **Scope**: internal（逆向 `temp/decomp/full` + 对照 compositor/data/assets）
- **Date**: 2026-06-04

## 一句话结论

**Tails 完全可实现**（与现有 back-cape 同源，纹理已在仓库、数据可从 accessory `backSlot` 直接推出）；
**ArmorBackCoat + coat 装扮槽（item 5587 ChickenBonesRobe）成对可实现**（纹理 `Armor_Legs_239`/`238` 已在仓库，但需在解析期对 netId 5587 单独打 `coat=251` 标记 —— 现有数据表完全没收录 5587）；
**Backpacks 部分受限**：触发条件可达（head/body/legs netId），但**贴图缺失需新提取**（`TextureAssets.Extra[212/213]` 与 `TextureAssets.BackPack[*]` 未在仓库），且 held-item 分支不可达（我方无手持物品数据）。
另有一条**共性缺口**：以上 tail/backpack/cape 还能由**身体防具**经 `IncludedCapeBack`/`IncludeCapeFrontAndBack` 触发，这条 body→back 路由现有 compositor 完全没实现（只实现了 accessory `back` 路径）。

| 项目 | 判定 | 纹理 | 数据可达性 |
|---|---|---|---|
| **Tails**（accessory 来源） | ✅ 可实现 | 已在仓库 (`Acc_Back_18/19/21/25/26/27/28.png`) | 可达：`_ACC_SLOTS[netId]["back"]` ∈ DrawInTailLayer |
| **ArmorBackCoat**（背片，coat=251） | ✅ 可实现 | 已在仓库 (`Armor_Legs_239.png`) | 可达：需对 netId==5587 特判置 coat=251 |
| **coat 装扮槽 + 前片 LongCoat（238）** | ✅ 可实现 | 已在仓库 (`Armor_Legs_238.png`) | 可达：同上，netId==5587 |
| **Backpacks**（accBack 来源，backSlot∈Backpack） | ✅ 可实现 | 已在仓库 (`Acc_Back_7/8/9/10/15/16/32/33.png`) | 可达：`_ACC_SLOTS[netId]["back"]` ∈ DrawInBackpackLayer |
| **Backpacks**（armor-set 触发 266/235/218、268/237/222） | ⚠️ 触发可达/贴图缺 | **缺失，需提取 `Extra_212`/`Extra_213`** | 触发可达（displayed head/body/legs） |
| **Backpacks**（held-item / turtle / body106/170 分支） | ❌ 不可达 | 部分缺 (`BackPack_*`) | held-item 数据我方没有 |
| **body→back 路由（IncludedCapeBack 等）** | ❌ 当前不可达 | 复用 `Acc_Back_*` | **数据表未生成**，需补 body→back 映射 |

---

## Findings

### Files Found（逆向源 / 仓库现状）

| File Path | Description |
|---|---|
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:444` | `DrawPlayer_08_Backpacks` |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:568` | `DrawPlayer_08_1_Tails` |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:1440` | `DrawPlayer_13_ArmorBackCoat`（背片） |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:1791` | `DrawPlayer_16_ArmorLongCoat`（前片，含 coat 分支 1808） |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:1823` | `DrawLongCoat`（共用绘制，仅 238 有 GlowMask 特例） |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:1838` | `GetMatchingBodyExtensionBack`（**仅 251→239**） |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:1848` | `GetMatchingBodyExtension`（含 251→238） |
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:435` | `DrawPlayer_08_PlayerVisuallyHasFullArmorSet`（读 displayed head/body/legs） |
| `temp/decomp/full/Terraria/Player.cs:36457` | `UpdateVisibleAccessory(itemSlot, item)`（per-item 视觉槽解析） |
| `temp/decomp/full/Terraria/Player.cs:36585` | **`if (item.type == 5587) coat = 251;`** |
| `temp/decomp/full/Terraria/Player.cs:36475` | accessory `item.backSlot` → backpack/tail/back 路由 |
| `temp/decomp/full/Terraria/Player.cs:35409` | body 防具 `IncludedCapeBack[body]` → backpack/tail/back 路由 |
| `temp/decomp/full/Terraria/Player.cs:35435` | body 防具 `IncludeCapeFrontAndBack[body]` → front+back 路由 |
| `temp/decomp/full/Terraria/Player.cs:9329` | `UpdateItemDye`（cTail/cBackpack/cCoat 的 dye 来源） |
| `temp/decomp/full/Terraria.ID/ArmorIDs.cs:1695` | `Back.Sets.DrawInBackpackLayer = {7,8,9,10,15,16,32,33}` |
| `temp/decomp/full/Terraria.ID/ArmorIDs.cs:1697` | `Back.Sets.DrawInTailLayer = {18,19,21,25,26,27,28}` |
| `temp/decomp/full/Terraria.ID/ArmorIDs.cs:1780` | **`Back.Count = 40`**（不是 1686 的 16；那是 Balloon 的） |
| `temp/decomp/full/Terraria.ID/ArmorIDs.cs:649` | `Body.Sets.IncludedCapeBack`（body→back 配对） |
| `temp/decomp/full/Terraria.ID/ItemID.cs:12713` | `ChickenBonesRobe = 5587` |
| `temp/decomp/full/Terraria/Item.cs:43958` | item 5587 `SetDefaults`：仅 `accessory=true; vanity=true;`，**无任何 `<cat>Slot`** |
| `temp/decomp/full/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs:162` | `DrawPlayer_UseNormalLayers` 完整层序 |
| `nextbot/terraria_render/compositor.py:52-54` | 现有 `_BACK_BACKPACK`/`_BACK_TAIL`（与上面两 Set 完全一致）→ **当前被 skip** |
| `nextbot/terraria_render/data/robe_extensions.json` | 含 `"251": 238`，但 compositor 按 body 槽查表，无 netId 命中 body 251 |

### 层序（LegacyPlayerRenderer.cs:162-258，cite 行号）

由后到前（grav/dir=1 正常 idle）：
```
177 DrawPlayer_08_Backpacks        ← 最靠后（在 wings 之前）
179 DrawPlayer_08_1_Tails          ← 在 wings 之前
181 DrawPlayer_09_Wings            (compositor 已实现 draw_acc_wing)
183 DrawPlayer_01_BackHair         (compositor: back hair)
184 DrawPlayer_10_BackAcc          (compositor: back cape)
185 DrawPlayer_01_3_BackHead       (compositor: back-head)
187 DrawPlayer_11_Balloons         (compositor: balloon)
192 DrawPlayer_13_ArmorBackCoat    ← 在 balloons 之后、Skin 之前
193 DrawPlayer_12_Skin             (compositor: 躯干/腿 skin + 背臂 composite)
197/201 DrawPlayer_13_Leggings
202/196 DrawPlayer_14_Shoes
205 DrawPlayer_15_SkinLongCoat
206 DrawPlayer_16_ArmorLongCoat    ← 前片（含 body-ext + coat-ext 两段）
207 DrawPlayer_17_Torso
```
→ compositor 对应插入点：
- Backpacks/Tails：在 `draw_acc_wing`（compositor.py:750）**之前**，整个 BEHIND-BODY 组最底。
- ArmorBackCoat：在 balloon 之后（compositor.py:769 之后）、BODY+LEG SKIN 段（:787 `comp.draw_player(3,...)`）**之前**。注意它在 `12_Skin`/背臂 composite 之前，所以排在 compositor 背臂组（:771-785）之前更安全（背臂 composite 属 12_Skin，行 193）。
- ArmorLongCoat 前片（coat 段）：与现有 `_longcoat_ext_slot`（compositor.py:850）同一插入点（:845-852，skin long-coat 之后、torso 之前）。

---

## 逐项实现规格

### 1. ArmorBackCoat（袍背片，`DrawPlayer_13_ArmorBackCoat`，PlayerDrawLayers.cs:1440）

**触发条件**
`GetMatchingBodyExtensionBack(coat)` ≠ -1。该函数（:1838）**只有一个 case**：`bodyValue == 251 → 239`，其余全 -1。
而 `drawPlayer.coat` **何时被设**？grep `Player.cs` 全部 `coat =`：
- `Player.cs:1568` 字段默认 `coat = -1`；`:30697` 每帧 reset `coat = -1`。
- `Player.cs:36587` **唯一赋值点**：`if (item.type == 5587) coat = 251;`，位于 `UpdateVisibleAccessory(itemSlot, item)`（:36457）内。
→ 即 **`coat` 与身体防具 (Body.Sets) 无关**，只由**装扮/社交配饰槽里的 item 5587（ChickenBonesRobe）**触发。`UpdateVisibleAccessory` 对 7 个功能槽 + 7 个社交槽的每件 item 逐一调用，所以只要 5587 出现在 `accessories` 或 `vanityAccessories` 任一槽即触发。

**数据可达性**：✅ **可达，但现有表无法表达**。
- 我方 `accessories` / `vanityAccessories` 是定长 7 的 `{netId,...}` 列表，netId==5587 完全可见。
- 但 item 5587 `SetDefaults`（Item.cs:43958）只设 `accessory=true; vanity=true;`，**没有任何 `<cat>Slot`**，因此 `gen_tables.py` 既不会把它收进 `equip_slots.json` 也不会进 `accessory_slots.json`（已实测：`5587 in equip_slots: None`、`5587 in accessory_slots: None`）。
- 现有 compositor 的 `_resolve_accessories` 完全基于 `_ACC_SLOTS` 的类目，**看不到 5587**。
- **实现路径**：在 `_resolve_accessories` 里对 7+7 槽的 netId 增加一条「`netId == 5587` → 置 `coat = 251`，并记下该槽 index 供 dye 用」的特判（不依赖 `_ACC_SLOTS`）。

**绘制定位 / 帧**（:1451）
- 贴图 `TextureAssets.ArmorLeg[239]` → 仓库 `Armor_Legs_239.png`（**已存在**）。
- 用 `legFrame`（腿帧，idle 行 0），origin `legVect`，位置基于 `legPosition`（与现有 `Armor_Legs_{ext}` 长袍前片绘制公式同构 —— compositor `draw_armor(..., "col", ...)` 走 `_frame(name, leg_cell)`，leg_cell=0）。
- 颜色 `colorArmorBody`（白娃娃路径 → 白）；shader = `cCoat`。
- `DrawLongCoat`（:1823）：把这条 DrawData 入队；仅 `specialLegCoat == 238` 时追加 ChickenBones 发光（239 背片**不触发**发光，无需额外处理）。

**染料**：`cCoat ← dyeItem.dye`，仅当 `armorItem.type == 5587`（UpdateItemDye，Player.cs:9466-9468）。`dyeItem` = **与 5587 同一配饰槽**的染料 → 即我方 `accessoryDyes[k]`（k = 放 5587 的槽 index）。✅ 可达。

**与现有 robe 前片区分**：现有 `_longcoat_ext_slot`（compositor.py:397/850）走 `robe_extensions.json`（`GetMatchingBodyExtension`），**按 body 防具槽**查表、用 **cBody** 染、画在 torso 之前（**前片**）。本项是 **背片**（`GetMatchingBodyExtensionBack`，仅 251→239），用 **cCoat** 染、画在 **skin 之前**（更靠后）。两者纹理不同（239 vs body 槽对应的 ext），插入点不同（背片在 12_Skin 前 / 前片在 16_ArmorLongCoat）。

---

### 2. coat 装扮槽（前片 LongCoat，`DrawPlayer_16_ArmorLongCoat` 的 coat 分支，PlayerDrawLayers.cs:1808）

**是什么**：item **5587 = ChickenBonesRobe**（ItemID.cs:12713），一件 `accessory=true; vanity=true` 的装扮配饰（"coat 装扮槽" 即把它放进配饰/社交槽）。它**不占** head/body/legs，也不占任何 `<cat>Slot`；唯一作用就是 `coat = 251`（+ `cCoat = dye`）。

`DrawPlayer_16_ArmorLongCoat` 跑两段：
- :1793 `GetMatchingBodyExtension(body)` —— **现有 robe 前片**（按 body 防具，cBody）。
- :1808 `GetMatchingBodyExtension(coat)` —— coat==251 → **238**，贴图 `ArmorLeg[238]` → 仓库 `Armor_Legs_238.png`（**已存在**），shader = `cCoat`，颜色 `colorArmorBody`（白）。`DrawLongCoat(...,238)` 会**追加 ChickenBones 发光层**（GlowMask[363]，:1826-1834，颜色 `GetChickenBonesGlowColor`）—— 若要 1:1 还需提取 `GlowMask_363`；最简实现可先省略发光（仅基础 238 片）。

**数据可达性**：✅ 同 §1，靠 netId==5587 特判得到 `coat=251`，再查 `robe_extensions.json["251"]=238`（**已存在**该 key）。注意：现有 compositor 因为按 **body 槽** 查 `_ROBE_EXT`、而无 netId 命中 body 251，所以这段前片**当前永不触发** —— 必须改成「`coat==251` 时额外画 `Armor_Legs_238`（cCoat 染）」。

**插入点**：与现有 `_longcoat_ext_slot` 同一处（compositor.py:845-852，skin long-coat 之后、torso 之前）。

---

### 3. Tails（尾饰，`DrawPlayer_08_1_Tails`，PlayerDrawLayers.cs:568）

**触发条件**（:570）：`tail > 0 && tail < ArmorIDs.Back.Count(=40) && !mount.Active`。

**`drawPlayer.tail` 何时设**？grep `Player.cs` 全部 `tail =`：
- `:1592` 默认 `tail = -1`；`:30691` reset。
- **(a) accessory 来源**（`UpdateVisibleAccessory`，:36483）：`if (item.backSlot > 0)` → `DrawInBackpackLayer[backSlot]` ? backpack : `DrawInTailLayer[backSlot]` ? **`tail = item.backSlot`** : back。
- **(b) body 防具来源**（:35419、:35449）：`IncludedCapeBack[body]`（或 `IncludeCapeFrontAndBack[body].backCape`）得到 back 槽 `b`，若 `DrawInTailLayer[b]` 则 `tail = b; cTail = cBody;`。

**数据可达性**：
- **(a) accessory 路径：✅ 完全可达**。我方 `accessories`/`vanityAccessories` 的 netId → `_ACC_SLOTS[netId]["back"]` 就是 `item.backSlot`（`gen_accessory_slots` 已把 `back` 列入 `_ACC_CATS`，compositor.py:53-54 的 `_BACK_TAIL = {18,19,21,25,26,27,28}` 与 `DrawInTailLayer` **逐位一致**）。当前 compositor 对落在 `_BACK_TAIL` 的 back 槽是**直接 skip**（compositor.py:757）；改为路由到 tail 绘制即可。
- **(b) body 路径：❌ 当前不可达**（见末尾「共性缺口」）：需要新增 `IncludedCapeBack`/`IncludeCapeFrontAndBack` 的 body→back 映射表（现仓库无）。body 96→back18、94→back19、80→back21 都会落进 tail。

**贴图**（:584）：`TextureAssets.AccBack[tail]` —— **与 back-cape 同一套 `Acc_Back_*`**！仓库已存在 `Acc_Back_18/19/21/25/26/27/28.png`（实测全部 OK）。**无需新提取**。

**帧 / 偏移 / 层位**（:572-585）
- 帧 `bodyFrame`（躯干帧，idle 行 0），origin `bodyVect`。
- 偏移：基准 = `bodyPosition + (width/2, height - bodyFrame.Height/2) + (0,-4) + (0,8)`（与 backpack/back 同基准）；外加 `zero`：sitting 时 `Y-=2`（立绘非 sitting，忽略），**女性 `X += 2*direction`**（dir=1 → +2x；男性 0）。`.Floor()`。
- 层位：在 wings **之前**（最靠后组）。compositor 插入点：BEHIND-BODY 段最底（`draw_acc_wing` 之前）。
- shader = `cTail`。

**染料**：`cTail ← dyeItem.dye`（UpdateItemDye:9359-9361，accessory backSlot∈DrawInTailLayer）。`dyeItem` = 同槽染料 → 我方 `accessoryDyes[k]`。✅ 可达，与现有 back/wing 配饰染料同机制（`_resolve_accessories` 的 `dye_index` 已处理）。body 来源时 `cTail = cBody`。

---

### 4. Backpacks（背包，`DrawPlayer_08_Backpacks`，PlayerDrawLayers.cs:444）

该层有 **5 个互斥/并列子分支**，分别判断可达性：

**(a) armor-set 触发 #1**（:446）：`PlayerVisuallyHasFullArmorSet(266,235,218)`（head=266 & body=235 & legs=218，读 **displayed** 槽）。
- 触发可达：✅ 我方有 displayed head/body/legs（compositor `_displayed_piece` 已算出 head_slot/body_slot/leg_slot）。
- 贴图 ❌：`TextureAssets.Extra[212]`，仓库**无 `Extra_212.png`**（仅有 `Extra_156.png`）→ **需新提取**。
- 帧（:451）：`value.Frame(1,5,0, miscCounter%25/5)` —— 5 帧竖条动画，立绘固定取 **frame 0**（`miscCounter` 无值→0）。位置基准 `(-2 + -2*Directions.X, 0)` + 通用 body 基准。颜色 `Color(250,250,250,200)`（半透明），shader=`cBody`。

**(b) armor-set 触发 #2**（:458）：`PlayerVisuallyHasFullArmorSet(268,237,222)` → `Extra[213]`，frame(1,5) 取 0，偏移 `(-9+Directions.X, -4*Directions.Y)`。同样**贴图缺失需提取 `Extra_213`**，触发可达。

**(c) held-item 4818 分支**（:470）：依赖 `heldItem.type` 与 `ownedProjectileCounts` → **❌ 不可达**（我方无手持/弹幕数据）。

**(d) `backpack` 变量分支**（:479）：`backpack > 0 && backpack < Back.Count(=40) && (!mount || ...)`。
- `drawPlayer.backpack` 何时设？同 tail：accessory `item.backSlot`∈`DrawInBackpackLayer`（:36479 `backpack = item.backSlot`）或 body `IncludedCapeBack` 路由（:35414/:35444 `backpack = b; cBackpack = cBody`）。
- 触发可达：✅ accessory 路径同 §3(a)，`_ACC_SLOTS[netId]["back"]` ∈ `_BACK_BACKPACK={7,8,9,10,15,16,32,33}`（= `DrawInBackpackLayer`，compositor.py:53 已逐位一致，当前 skip）。body 路径同 §3(b) ❌。
- 贴图（:484）：`TextureAssets.AccBack[backpack]` —— **复用 `Acc_Back_*`**！仓库已存在 `Acc_Back_7/8/9/10/15/16/32/33.png`（实测全 OK）→ **无需新提取**。
- 帧：`bodyFrame`，基准 + `(0,8)` + `(0,-4)`，origin `bodyVect`，颜色 `colorArmorBody`（白），shader=`cBackpack`。
- 染料：`cBackpack ← dyeItem.dye`（:9355-9357）→ 我方 `accessoryDyes[k]`。✅ 可达。

**(e) else 分支（held-item / turtleArmor / body 106 / body 170）**（:488-565）：`heldItem.type ∈ {1178,779,5134,1295,1910}` 或 `turtleArmor` 或 body∈{106,170}，用 `TextureAssets.BackPack[num2]`。
- body 106/170 触发可达（✅ 我方有 body 槽），但 `turtleArmor`/heldItem 不可达；
- 贴图 `BackPack_*` 仓库**无** → 需提取（若只做 body106/170 则需 `BackPack_6`/`BackPack_7`）。

**Backpacks 综合判定**：⚠️ **分两档**——
- **(d) accessory-backpack 子分支可立即实现**（贴图 `Acc_Back_*` 已在仓库，数据可达，与 tail 对称）；
- **(a)(b) armor-set 背包需先提取 `Extra_212`/`Extra_213`** 两张图；
- **(c)(e) held-item 相关分支不可达**（缺手持物品数据），(e) 的 body106/170 子项要 `BackPack_*` 贴图。

---

## 共性缺口：body 防具 → back/tail/backpack 路由（当前完全未实现）

`tail`/`backpack`/`back` 除了 accessory `item.backSlot` 外，还能由**身体防具**触发：
`Body.Sets.IncludedCapeBack[body]`（ArmorIDs.cs:649）与 `IncludeCapeFrontAndBack[body]`（:655），把 body 槽映射到一个 back 槽 `b`，再用 `DrawInBackpackLayer`/`DrawInTailLayer` 同样三分路由（Player.cs:35409-35457）。
- 配对示例（`CreateIntSet(-1, body, back, ...)`）：body 207→back13、206→12、205→11、185→17、**96→18(tail)**、**94→19(tail)**、**80→21(tail)**、217→22、24→29、**238→32(backpack)**。
- 现状：`nextbot/terraria_render/` 内**没有任何 `IncludedCapeBack`/`IncludeCapeFront` 表**（实测 grep 为空），compositor 只实现了 accessory `back` 路径，**body 防具自带披风/尾/背包一律不画**。
- 数据可达性：✅ 可达（我方有 displayed body 槽），但**需在 `gen_tables.py` 新增 body→back 映射 + Female 变体**，并在解析期接入三分路由（同时影响 §3(b)、§4(d) 的 body 来源，以及现有 back-cape 的完整性）。

---

## External References

无（纯逆向，无需外部文档）。

## Related Specs

- `.trellis/tasks/06-02-my-character-render/research/accessories_spec.md`（前序：12 配饰类目 / vanity-override / hideVisuals / 配饰染料解析 —— tail/backpack 的 dye 解析复用其 `dye_index` 机制）
- `nextbot/terraria_render/data/robe_extensions.json`（已含 `"251": 238`，但 §1/§2 需改为按 coat 而非 body 触发）

## Caveats / Not Found

- **`Back.Count` 易踩坑**：ArmorIDs.cs:1686 的 `Count = 16` 属于 **Balloon** 类，**Back.Count 在 :1780 = 40**。tail/backpack 槽（最大 33）全部 < 40，合法。
- **白娃娃 vs colorArmorBody**：以上层在游戏里用 `colorArmorBody`/半透明色；compositor 走 display-doll 白色路径（`draw_acc_strip`/`draw_armor` 不染底色，只叠 dye），与现有 back-cape/robe-ext 一致即可，无需特别处理（armor-set 背包的 `(250,250,250,200)` 半透明若要 1:1 需额外乘 alpha）。
- **ArmorBackCoat 的 `legFrame` vs `bodyFrame`**：背片(239)/前片(238) 用 **legFrame**（腿帧），tail/backpack 用 **bodyFrame**（躯干帧）；立绘 idle 两者都取行 0，compositor 现有 `_frame(name, 0)` 已覆盖，但注意 origin（legVect vs bodyVect）与基准公式不同（背片用 legPosition+legVect，已与现有 `_longcoat_ext_slot` 的 `draw_armor(...,"col",...)` 对齐）。
- **发光特例**：仅 coat 前片 238 触发 ChickenBones 发光（GlowMask[363]，需 `GlowMask_363` 资源，仓库未确认）；背片 239 与所有 tail/backpack 无发光。MVP 可先不做 238 发光。
- **`Extra_212`/`Extra_213`/`BackPack_*` 资源缺失**已实测确认（assets 仅有 `Extra_156.png`，无 `Extra_212/213`、无 `BackPack_*`）。若要做 armor-set 背包，需在 `extract_assets.py` 流程补提取这些 `TextureAssets.Extra` / `TextureAssets.BackPack` 条目。
- held-item 相关一切分支（backpack (c)(e) 的 heldItem、turtleArmor）**结构性不可达**：appearance API 不含手持物品；除非 API 增补 `heldItem` 字段，否则只能省略。
