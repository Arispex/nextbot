# Audit: 帧 / 偏移 / 层序 — 翅膀·气球·背饰·前饰·腰·颈·脸·胡须 (idle, direction=1)

- **Scope**: 只读审计（不改代码）。只查"画出来的层取的帧/偏移/层序对不对"，**不查染料、不查资产齐全性**。
- **逆向为准**: `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs` (draw 方法) + `…/Terraria/Player.cs` (帧/偏移来源) + `…/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs` (master 层序) + `…/Terraria/Main.cs` (偏移表)。
- **repo 渲染器**: `nextbot/terraria_render/compositor.py`、`data/wing_meta.json`。
- **Date**: 2026-06-04

---

## 一句话结论

**帧号基本全对**（idle 标准帧 = bodyFrame.Y=0 / legFrame.Y=0 / wingFrame=0，气球 4 帧动画 pin frame 0 是合理 still）；**主要问题集中在 (a) 两处层序与逆向相反/错位（前饰背半、盾牌），与"鞋 bug"同类；(b) 4 个尾游戏翅膀 (47/49/50/51) 走了默认偏移公式但逆向是 bespoke 公式 → cell-local 偏移错 5~9px。** 腰/颈/脸/胡须/背饰/气球/默认翅膀的帧·偏移与逆向一致。

### P0（层序错 / 同"鞋 bug"类，影响常见物品）

- **P0-1 Shield(25) 画得太晚**：compositor 把盾画在最外层（front 臂之上、前饰前半之上）；逆向 master 顺序里盾在 **front 臂之前、前饰前半之前**（应被前臂遮挡）。
- **P0-2 FrontAcc 背半 (32_BackPart) 画得太早**：compositor 在 neck 之后、**head 之前**画；逆向在 **head/face 之后**画（背半应盖住头颈区域，现在被头盖住）。

### P1（少数物品 / 端游翅膀 / 次级层）

- **P1-1 翅膀 47/49/50/51 偏移错**：走默认 `lx=11+num13-w/2, ly=33+num12-fh/2`，但逆向是各自 bespoke 公式（用 `OffsetsPlayerHeadgear`/独立 base/`-UnitX*dir*4`）。X 偏 5~9px、Y 偏 1~3px。帧数 N 已对。
- **P1-2 翅膀 47/49/50/51 漏 `-2` 裁边**：逆向对这些 `Frame(1,N,…)` 做 `Width-=2; Height-=2`，compositor 取满帧 → 多 2px（次要）。
- **P1-3 脸 face 19 漏 `GetFaceDrawOffset` 的 (0,-6)·Directions**：compositor 脸一律 cell-local (0,0)，逆向 face==19 有 -6 Y。
- **P1-4 `DrawInFaceUnderHairLayer` 的脸漏了"头发之下"层**：compositor 把所有 face 画在头/发之上（layer22）；逆向该集合的 face 画在 layer21 头发**之前**。
- **P1-5 前饰 front 13 漏 `GetFrontDrawOffset` 的 (-2,0)·Directions**；胡须漏 `GetBeardDrawOffset`（仅戴特定头盔 head∈{165,146,150,152,148} 时非零，裸头 idle 为 0，**通常无影响**）。

---

## Findings 表

| severity | 症状 | 逆向证据 (file:line) | repo 证据 (compositor.py:line) | 建议修法 |
|---|---|---|---|---|
| **P0** | **Shield 层序错**：盾画在 front 臂 + 前饰前半之上，应在二者之下 | `LegacyPlayerRenderer.cs:229-243`：顺序为 `32_BackPart(229) → 25_Shield(230) → [front 臂 23/28/29/30, 238-240] → 32_FrontPart(243, 默认分支)` | `compositor.py:780-800`：先画 front 臂(781-792)，再 `draw_acc_front_half(front=True)`(795-797)，**最后**画 shield(798-800) | 把 shield 提到 **front 臂组之前**（即 head/face 组之后、`draw_player(7,"front_arm")` 之前），且在 FrontAcc 前半之前。顺序应为 …back-half… → shield → front 臂 → front-half |
| **P0** | **FrontAcc 背半层序错**：背半（右 20px）画在 head 之前 → 被头盖住；应盖住头颈 | `LegacyPlayerRenderer.cs:213,218,229`：`21_Head → 22_FaceAcc → … → 32_FrontAcc_BackPart(229)`。BackPart 在 head/face **之后** | `compositor.py:755-758`：在 neck 后、head 组(760+)**之前** 调 `draw_acc_front_half(front=False)` | 把 front 背半移到 head/face 组**之后**（与 shield 一起，BackPart 在 shield 前，见上）。注意 idle 时 `drawFrontAccInNeckAccLayer=false`（仅 bodyFrame.Y/H==5 或特殊集合才 true，`PlayerDrawSet.cs:1760-1771`） |
| **P1** | 翅膀 **47/49** 偏移错：用默认公式，逆向是 `vector + ((1,1)+headgear[0]-（0,2))·dir - UnitX·dir·4` | `PlayerDrawLayers.cs:800-804`(47)/`819-823`(49)：`vector8=OffsetsPlayerHeadgear[0]=(0,2)`(`Main.cs:506`)，`-=2`→(0,0)，`+(1,1)`→(1,1)；`vec=vector+(1,1)·dir-(4,0)`。idle 实 center cell-local=(17,32) | `compositor.py:466-469`：`num13,num12=_WING_OFFSET.get(slot,(0,0))`=(0,0)→`lx=11-w/2, ly=33-fh/2`（=默认 center (11,33)） | 给 47/49 单独偏移：等效 `num13=+6, num12=-1`（使 center=(17,32)）。或为这些 wing 走专门公式 |
| **P1** | 翅膀 **50** 偏移错：逆向 base 无默认的 `-9` X | `PlayerDrawLayers.cs:918-923`：`vec10=vector+0·dir - UnitX·dir·4`=vector+(-4,0)。idle 实 center=(16,31) | `compositor.py:466-469` + `wing_meta.json` `offset["50"]=[-4,0]`→`lx=11-4-w/2=7-w/2, ly=33-fh/2`（center (7,33)） | 50 实际 center=(16,31)；应让 `num13`等效 `+5`、`num12`等效 `-2`（即 offset 改为约 `[+5,-2]`，因为默认公式已扣 9，需 `11+5=16`、`33-2=31`） |
| **P1** | 翅膀 **51** 偏移错：逆向 base 不含 `(0,7)`，含 `(0,6)` 和 `-UnitX·dir·4` | `PlayerDrawLayers.cs:778-780`：`vector7=(0,6)`；`vec4=Position+(w/2,h-bodyFrame.H/2)-screen + (0,6) - (4,0)`（= `vector` − (0,7)+(0,6)−(4,0)=vector+(-4,-1)）。idle 实 center=(16,30) | `compositor.py:466-469`，51 无 offset 项→center=(11,33) | 51 应 center=(16,30)；offset 等效 `[+5,-3]` |
| **P1** | 翅膀 47/49/50/51 漏 `Width-=2; Height-=2` 裁边 | `PlayerDrawLayers.cs:806-807,825-826,(50 无裁), 782-783`（47/49/51 裁；50 用 `value10` **不裁**） | `compositor.py:200-211` `_acc_wing_frame` 取满 `(0,0,w,fh)` | 对 47/49/51 取帧后右/下各裁 2px（与 origin 同步），保持像素对齐。50 不需裁 |
| P1 | 脸 **face 19** 漏 (0,-6)·Directions 偏移 | `Player.cs:4384-4386` `GetFaceDrawOffset` case 19：`zero += (0,-6)·Directions` | `compositor.py:776-778` `draw_acc_strip(Acc_Face_*)` 一律 cell-local(0,0)，无 face 偏移 | 对 face 应用 `GetFaceDrawOffset(face)`：多数为 0，face 19 = (0,-6)。其余 (1/6/8/9/22…) 仅在特定 head 下非零，裸头 idle 多为 0 |
| P1 | `DrawInFaceUnderHairLayer` 的 face 漏"发下"层位 | `PlayerDrawLayers.cs:2583-2588 / 2631-2636`（layer21 `TheFace` 内、画发**之前**）；与 `DrawPlayer_22_FaceAcc:2807` 互斥（`!DrawInFaceUnderHairLayer` 才走 22） | `compositor.py:776-778`：所有 face 画在 head armor + front hair 之后（over everything） | 对该集合的 face 改在 **front hair 之前**画（即 head skin/eyes 之后、`draw_hair(front)` 之前）；非该集合保持现状。需读 `ArmorIDs.Face.Sets.DrawInFaceUnderHairLayer` |
| P1 | 前饰 **front 13** 漏 (-2,0)·Directions | `Player.cs:4708-4717` `GetFrontDrawOffset`：仅 front==13 → (-2,0)·Directions | `compositor.py:482-503` `draw_acc_front_half` 无 `GetFrontDrawOffset` | front 两半都加 `GetFrontDrawOffset()`（仅 13 非零） |
| P2 | 胡须漏 `GetBeardDrawOffset`（裸头 idle=0，**通常无影响**） | `Player.cs:4634-4662`：仅 head∈{165,146,150,152,148} 非零；裸头/无 mount = (0,0)。`PlayerDrawLayers.cs:2426` 还有 `PreventBeardDraw[head]` 门 | `compositor.py:770-774` 胡须 cell-local(0,0)，无 head 偏移、无 PreventBeardDraw 门 | 低优先：戴特定头盔时才需偏移；若 PRD 只渲染裸头/常见头盔可忽略。建议至少补 `PreventBeardDraw[head]` 隐藏门 |

---

## 逐类核对详情（帧 / 偏移 / 层序 / 性别 / 次级层）

### 翅膀 Wings — `DrawPlayer_09_Wings` (PlayerDrawLayers.cs:655) / compositor `draw_acc_wing` (454)

1. **帧号**：grounded idle `wingFrame=0`（折叠帧）。来源 `Player.cs:22370-22372`（`velocity.Y==0 → wingFrame=0`）、`26004`/`pulley`。非 AlwaysAnimated 翅膀**无视 velocity 都画**（方法体里只有 AlwaysAnimated 分支 gate `ShouldDrawWingsThatAreAlwaysAnimated()`）。compositor 取 frame 0 ✓，并正确跳过 `_WING_ALWAYS_ANIMATED={22,28,34,39,40,44,45,48}`（`compositor.py:670`，与 `ArmorIDs.Wing.Sets.AlwaysAnimated` 一致）。
2. **帧数 N**：默认 `num14=4`（`:934`）。特殊：43→7(`:939`)、44→7(AlwaysAnimated, 跳过)、47/49/50→11(`Frame(1,11)`/`num11=11`)、51→8(`Frame(1,8)`)。`wing_meta.json frames={43:7,47:11,49:11,50:11,51:8}`、`get(slot,4)` ✓。
3. **偏移（默认块, 适用 ~大多数 + 5/12/27/41/43）**：逆向 `vector=center+(0,7)`（`:663-664`），`vector18=vector+(num13-9, num12+2)·dir`（`:993`），origin=`(W/2, fh/2)`。换算 cell-local：body cell center=(20,28)，故 `vector` center=(20,31)，`vector18` center=(11+num13, 33+num12)，top-left=`(11+num13-W/2, 33+num12-fh/2)`。**compositor `lx=11+num13-w//2, ly=33+num12-fh//2`（`:467-468`）完全一致 ✓**。`wing_meta.json offset` {5:[4,-4],12:[-1,-1],27:[3,0],41:[-1,0],43:[-5,-7]} 与逆向 `:935-962` 一致 ✓。
4. **偏移（bespoke 翅膀 47/49/50/51）**：**错**（见表 P1-1/P1-2 行）。这些不走 `(num13-9, num12+2)`，compositor 误套默认公式。
5. **性别**：翅膀无性别行位移；帧选择两性同为 wingFrame=0。compositor 无性别处理 ✓（无需）。
6. **次级层**：所有 glow/flame/trail 次级 DrawData（`:684-698, 741-751, 997-1104` 等）仅 airborne / `shadow==0` / 特定 ID 才出现，still idle 不画 → compositor 略过合理 ✓。

### 背饰 BackAcc — `DrawPlayer_10_BackAcc` (590) / `draw_acc_strip` (429, behind body)

- **帧**：用 `bodyFrame`（idle=(0,0,40,56) frame 0）。40x1120 strip。compositor `_acc_strip_frame` 取 `(0,0,texW,56)` = frame 0 ✓。
- **偏移**：`vec=basePos + (0,-4) + (0,8)`，origin `bodyVect`（`:624-627`）。净 cell-local 与 body 同（top-left 对齐）✓。`armorAdjust`（`:596-621`）仅在同时穿 front cape(1-4) 时非零，影响 body 帧裁切，与本层 strip frame0 无关。
- **层序**：master `09 Wings → 01 BackHair → 10 BackAcc → 11 Balloons`（`:181-187`）。compositor `wing → back hair → back acc → balloon`（`:669-683`）✓。
- **次级层**：back 36 SuperHeroCostume 的 shimmer glow 条（`:630-651`）跳过合理。
- **性别**：无性别 split ✓。

### 气球 Balloons — `DrawPlayer_11_Balloons` (1140) / `draw_acc_balloon` (444)

- **帧**：非 torso 气球 = 52x224 四帧，按**挂钟** `DateTime.Now.Millisecond%800/200`（`:1154`）。pin frame 0 作为确定性 still 合理 ✓。torso-framed 气球 18 用 `bodyFrame` frame0，compositor 经 `_BALLOON_TORSO={18}` 走 `draw_acc_strip` 的 balloonFront 路径（`:695-696`）✓。
- **偏移**：逆向 `vector=OffsetsPlayerOffhand[0]=(14,20)`（`Main.cs:458`），`vector2=(0,8)+(0,6)=(0,14)`，`vector3=Position-screen + (14,20)·(1,1) + (0,h-bodyFrame.H) + (0,14)`，origin=`(26+dir*4, 28+grav*6)=(30,34)`（`:1164-1168`）。换算 cell-local top-left=**(-6,-4)**（X 中 `width/2` 与 cell 锚点抵消；用 width=20/height=42/bodyFrame=40x56 验证）。**compositor `_BALLOON_OFFSET=(-6,-4)`（`:47`）完全一致 ✓**。
- **层序**：layer 11 behind body ✓。气球 18 (balloonFront, `DrawPlayer_12_1`) 画在 back 臂之前 — compositor `:695-696` 在 back-arm 组（`draw_armor(body,"back_arm")` 后）画，与 master `17_Torso` 前的位置近似（注：master 用合并 17_Torso，composite 路径拆 back-arm 组，气球 18 在 back-arm 之上、body 之前，符合 `cBalloonFront` 语义）✓。
- **次级层**：无（单帧）。**性别**：无 split ✓。

### 腰 WaistAcc — `DrawPlayer_19_WaistAcc` (2066) / `draw_acc_strip` (749)

- **帧**：默认用 **`legFrame`**（不是 bodyFrame！），仅 `UsesTorsoFraming[waist]` 时用 bodyFrame（`:2070-2074`）。但 idle 时 `legFrame.Y==bodyFrame.Y==0`（`Player.cs:36209,36214`），故 frame 0 两者等价。compositor 取 strip frame0 ✓（idle 下不受 legFrame vs bodyFrame 影响）。
- **偏移**：锚 `legFrame.Width/2` + `legPosition + legVect`（origin=`legVect=(20,42)`）。idle 下 leg cell 与 body cell 同位、源矩形 Y=0 → top-left 与 body 对齐，1:1 composite ✓。
- **层序**：master `17_Torso → 18_Offhand → 19_Waist → 20_Neck`（`:207-210`）。compositor 在 torso 组之后画 waist→neck（`:749-754`）✓。
- **性别 / 次级层**：无 ✓（idle frame 不分性别行；strip 内容若有性别差异由贴图本身承载）。

### 颈 NeckAcc — `DrawPlayer_20_NeckAcc` (2081) / `draw_acc_strip` (753)

- **帧**：`bodyFrame` frame0（40x1120 strip）✓。**偏移**：basePos + `bodyVect` origin，top-left 对齐 cell（`:2085`）✓。**层序**：20，在 waist 之后、head 之前 ✓（compositor `:752-754`）。**性别/次级层**：无 ✓。

### 头组里的脸 / 胡须

- **Head(21)** `DrawPlayer_21_Head` (2091)：含 face-under-hair(`TheFace` 内)、head armor、front hair、beard，且**faceMask 在头之下**(`:2125-2140`)。compositor head 组 `:760-778`：skin/eyes → front hair → head armor → beard → face。
- **胡须 Beard**：逆向画在 head 组内（`:2431-2442`），origin `headVect`，偏移 `GetBeardDrawOffset()+pos`。`UseHairColor[beard]` 时 tint 发色（`:2435`）。compositor `:770-774` cell-local(0,0) + `hair_color` 判定（`_BEARD_HAIR_COLOR={2,3,4}`）✓帧/tint；**漏 `GetBeardDrawOffset`（裸头=0，通常无影响，P2）+ 漏 `PreventBeardDraw[head]` 门**。
- **脸 FaceAcc(22)** `DrawPlayer_22_FaceAcc` (2801)：偏移 `GetFaceDrawOffset(face)+vector(mount)`（`:2809,2814`）。compositor `:776-778` 无 face 偏移 → **face 19 错 (0,-6)（P1-3）**；`DrawInFaceUnderHairLayer` 的 face 应在发下而非发上（**P1-4**）。`faceMask`/`faceFlower`/`faceHead` 是头盔/特殊物件字段，非 appearance API 的 `faceSlot`，本审计范围外。

### 前饰 FrontAcc 前后半 — `DrawPlayer_32_FrontAcc_FrontPart` (3891) / `_BackPart` (3934) / `draw_acc_front_half` (482)

- **拆分**：FrontPart 保 `bodyFrame.Width-=num`（X 不变）= 纹理**左半** [0,20)（`:3897-3899`）；BackPart 保 `bodyFrame.X+=num`（Width-=num）= 纹理**右半** [20,40)（`:3940-3943`）。compositor：`front=True` 保左半 [0,20)（`masked[:,half:]=0`）、`front=False` 保右半 [20,40)（`:499-502`）✓ **拆分方向正确**。
- **偏移**：两半都加 `GetFrontDrawOffset()`（`:3905,3949`）。compositor 漏（仅 front 13 非零，**P1-5**）。`DontDrawIfWearingAScarfOrCape` 门（`:3893`）本审计未在 compositor 找到对应实现（属遮挡/隐藏逻辑，非帧/偏移，提示存在但不展开）。
- **层序（关键, P0-2）**：master 默认（idle, `drawFrontAccInNeckAccLayer=false`）：`21_Head(213) → 22_Face(218) → 32_BackPart(229) → 25_Shield(230) → [front 臂 238-240] → 32_FrontPart(243)`。
  - compositor：`neck → 32_BackPart(757-758) → head 组(760-778) → front 臂(781-792) → 32_FrontPart(795-797) → shield(798-800)`。
  - **差异 1**：BackPart 在 head **之前**（应在之后）。**差异 2**：Shield 在 FrontPart **之后/最外**（应在 BackPart 后、front 臂前）。两者都属层序错，与"鞋 bug"同类（顺序与逆向相反）。

### 盾 Shield — `DrawPlayer_25_Shield` (3055) / `draw_acc_strip` (798)

- **帧**：`bodyFrame` frame0；纹理可能 >40 宽时 `bodyFrame.Width=texW` 并调 `bodyVect.X`（`:3066-3077`）。compositor `_acc_strip_frame` 保留满纹理宽（`:181-183`，shield ≤44）✓帧/宽。
- **偏移**：`zero`，仅 `shieldRaised` 时 Y-=4（idle 非 raised → 0）✓。
- **层序**：**错（P0-1）**，见上。盾应在 front 臂之前、前饰前半之前。

---

## Caveats / Not Found

- **染料 / tint 逻辑全部跳过未审**（另有人查），表中"tint"仅在与帧/层序耦合处顺带提及。
- **隐藏/遮挡集合**（`DontDrawIfWearingAScarfOrCape`、`PreventBeardDraw`、`HidesHead`、`armorAdjust` 的 body 帧裁切）只确认其在逆向存在并影响层，未逐一核对 compositor 是否实现 —— 严格说属"是否漏画/误画"边界，本次聚焦帧/偏移/层序主干。
- **翅膀 bespoke 偏移的具体修正常数**（表中 47/49/50/51 的等效 num13/num12）是按 idle、direction=1、gravDir=1、bodyFrame.Y/H=0 推导；若要覆盖 mount/翻转需再推。建议落实修复时用单元测试对单个 wing 的 cell-local top-left 做数值断言（现有 `tests/test_terraria_render.py` 只断言"变宽/不同"，未数值校验偏移）。
- **face/front 的 `GetFaceDrawOffset`/`GetFrontDrawOffset` 全表**未逐 case 展开（只确认 face 19 与 front 13 的 idle 非零项 + 多数为 0 或 head-依赖）。修复时应直接移植这两个 switch（`Player.cs:4365-4363` / `4708-4717`）。
- 规格 `research/accessories_spec.md` §1/§3 的翅膀/气球偏移与逆向一致（已交叉验证），但 §1 未指出 47/49/50/51 的 bespoke 偏移与默认公式不同 —— **以逆向源码为准**。
