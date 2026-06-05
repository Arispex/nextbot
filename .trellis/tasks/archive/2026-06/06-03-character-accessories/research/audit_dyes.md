# Research: 染料覆盖完整性审计 (装备 + 12 配饰)

- **Query**: 审计装备染料(head/body/legs) + 配饰染料(12 类) 是否对所有应染的层套了正确 shader；找漏染 / 路由错 slot / 漏 dye pass / src_rect 传错。
- **Scope**: internal — 逆向 `temp/decomp/full/.../Player.cs`(c* 字段绑定) + `PlayerDrawLayers.cs`(每层 DrawData.shader) 对照 `compositor.py` / `dye.py`。
- **Date**: 2026-06-04
- **只读审计**：未改任何代码。

---

## 一句话结论

染料覆盖**基本完整且 slot 路由正确**：装备 head/body/legs 与全部 12 配饰类别都在 compositor 调用了 `apply_dye` 且染料 slot 路由正确；body armor 的 torso/前臂/后臂/前肩/后肩、long-coat 扩展、head、leg 都套了 `body_dye`/`leg_dye`/`head_dye`。**唯一真缺口是 P2 级 `wearsRobe → cLegs=cBody` 未实现**(穿袍+腿甲+染料时腿甲用错染料槽)；其余为已知限制(降级正确、未误染乱色) + 两个 P3 观察项(glowmask 整体未绘制、coat 槽位 cCoat 概念未建模但当前 robe_extensions 走的是 cBody 路径、是对的)。

---

## 1) 客户端会染色的全部层 → c* 字段 → compositor 对照表

逆向依据：c* 字段赋值见 `Player.cs UpdateDyes/UpdateItemDye` (9300–9469)；每层 `DrawData.shader` 见 `PlayerDrawLayers.cs`。compositor 依据 `compositor.py render_character` (615–805) + `draw_armor`/`draw_acc_*`。

### 装备 (armor) 层

| 绘制层 (PlayerDrawLayers.cs) | DrawData.shader | 逆向 file:line | compositor 调用点 | 染料槽 | 结论 |
|---|---|---|---|---|---|
| 17_Torso (非合成) 躯干 | `cBody` | PlayerDrawLayers.cs:1946 | `draw_armor(armor_body,"torso",body_dye)` compositor.py:742 | body_dye | ✅ 正确 |
| 17_TorsoComposite 躯干 | `cBody` | :2008 | 同上(合成路径在 game，本 renderer 用 torso cell) | body_dye | ✅ |
| 28_ArmOverItem (非合成) 前臂 | `cBody` | :3636 | `draw_armor(armor_body,"front_arm",body_dye)` :783 | body_dye | ✅ |
| 28_ArmOverItemComposite 前臂 | `cBody` | :3747 | 同上 :783 | body_dye | ✅ |
| 12_SkinComposite_BackArmShirt 后臂 | `cBody` | :1367 | `draw_armor(armor_body,"back_arm",body_dye)` :693 | body_dye | ✅ |
| 12_SkinComposite 后肩 | `cBody` | :1353 | `draw_armor(armor_body,"back_shoulder",body_dye)` :743 | body_dye | ✅ |
| 28 Composite 前肩 | `cBody` | :3724 | `draw_armor(armor_body,"front_shoulder",body_dye)` :784 | body_dye | ✅ |
| 16_ArmorLongCoat (body 扩展裙) | `cBody` | :1804 | `draw_armor(Armor_Legs_{ext},"col",body_dye)` :739 | body_dye | ✅ 用 cBody 正确(见 §3 coat 辨析) |
| 13_ArmorBackCoat (coat 扩展裙) | `cCoat` | :1452 | — (未建模 coat 槽) | — | ⚠️ P3 见 §3 |
| 16_ArmorLongCoat (coat 扩展裙) | `cCoat` | :1818 | — (未建模 coat 槽) | — | ⚠️ P3 见 §3 |
| 13_Leggings 腿甲 | `cLegs` (robe 时 = cBody) | :1547；`cLegs=cBody` 见 Player.cs:9309-9311 | `draw_armor(armor_legs,"col",leg_dye)` :711 | leg_dye | ⚠️ **P2 真缺口**：robe 时未把 leg_dye 改成 body_dye |
| 21_Head 头甲 | `cHead` | :2144/2151 | `draw_armor(armor_head,"col",head_dye)` :768 | head_dye | ✅ |
| 14_Shoes 鞋(默认装备 shoe 部件) | `cShoe`(22/23→cFlameWaker) | :1761-1772 | — (装备 shoe 部件未单列；配饰 shoe 见下) | — | 注：装备 leg 甲不含独立鞋槽，鞋走配饰 §鞋 |

> body armor 子部件(torso/前臂/后臂/前肩/后肩)**全部染了**，且全部 `cBody`/`body_dye` —— 无漏染。head、leg 各 1 部件也染了。

### glowmask 层 (与基底同 c*)

| glowmask | DrawData.shader | 逆向 file:line | compositor | 结论 |
|---|---|---|---|---|
| bodyGlowMask (躯干) | `cBody` | :1951 | 整体不绘制 glowmask | ⚠️ P3 见 §2 |
| armGlowMask (前臂) | `cBody` | :3641 | 不绘制 | ⚠️ P3 |
| legsGlowMask (腿) | `cLegs` | :1565/1572 | 不绘制 | ⚠️ P3 |
| headGlowMask (头) | `cHead` | :2174/2199/3787 | 不绘制 | ⚠️ P3 |

### 配饰 12 类 (+ beard)

依据 `UpdateItemDye` (Player.cs:9329-9469) 设 c* + 各 DrawPlayer 层。compositor 在 `_resolve_accessories` 把每类的 dye 解析进 `acc_dyes[cat]`,再逐层 `apply_dye`。

| 配饰类别 | c* 字段 | 设值 file:line | 绘制层 shader file:line | compositor 调用点 | 传入 dye | 结论 |
|---|---|---|---|---|---|---|
| wing | `cWings` | Player.cs:9436 | PlayerDrawLayers.cs:701 等 | `draw_acc_wing(slot, acc_dyes["wing"])` compositor.py:671 | wing | ✅ |
| back (真斗篷) | `cBack` | :9365 | :628 | `draw_acc_strip(Acc_Back_,acc_dyes["back"])` :678 | back | ✅ (backpack/tail 路由项不绘制,合理) |
| balloon (普通) | `cBalloon` | :9431 | :1150/1169 | `draw_acc_balloon(...,acc_dyes["balloon"])` :683 | balloon | ✅ |
| balloon 18 (torso/front) | `cBalloonFront` | :9427 | :1117/1136 | `draw_acc_strip(Acc_Balloon_18,acc_dyes["balloon"])` :696 | balloon | ✅ 值相同(见 §4) |
| handOff (合成) | `cHandOff` | :9351 | :1426 | `draw_acc_hand(Acc_HandsOff_,back_arm,acc_dyes["handOff"])` :691 | handOff | ✅ |
| shoe | `cShoe` (22/23→cFlameWaker) | :9377/9385 | :1772 | `draw_acc_strip(Acc_Shoes_,acc_dyes["shoe"])` :706 | shoe | ✅ (cFlameWaker 同值,见 §3) |
| waist | `cWaist` | :9390 | :2076 | `draw_acc_strip(Acc_Waist_,acc_dyes["waist"])` :751 | waist | ✅ |
| neck | `cNeck` | :9398 | :2086 | `draw_acc_strip(Acc_Neck_,acc_dyes["neck"])` :754 | neck | ✅ |
| beard | `cBeard` | :9421 | :2440 | `draw_acc_strip(Acc_Beard_,acc_dyes["beard"],hair_color=…)` :772 | beard | ✅ |
| face | `cFace` (mask→cFaceMask, flower→cFaceFlower, head→cFaceHead) | :9404-9416 | :2815/2835/2855/2594 | `draw_acc_strip(Acc_Face_,acc_dyes["face"])` :778 | face | ✅ 值相同(见 §4 face 多层辨析) |
| handOn (合成) | `cHandOn` | :9347 | :3833/3843 | `draw_acc_hand(Acc_HandsOn_,front_arm,acc_dyes["handOn"])` :791 | handOn | ✅ |
| front (前/后两半) | `cFront` | :9370 | :3907(前半)/:3951(后半) | `draw_acc_front_half(...,acc_dyes["front"],front=…)` ×2 :757/:796 | front | ✅ 两半同 dye,src_rect 分半正确(见 §4) |
| shield | `cShield` | :9394 | :3089/3094 | `draw_acc_strip(Acc_Shield_,acc_dyes["shield"])` :800 | shield | ✅ |

> **12 类全部把自己的 dye 传进 `apply_dye`,逐槽路由与 `UpdateItemDye` 完全一致 —— 无漏传、无错槽。**

---

## 2) body 多子部件 / leg / head / glowmask 专项

- **body armor 多子部件全染** ✅：torso(1946/2008)、前臂(3636/3747)、后臂(1367)、前肩(3724)、后肩(1353) 全部 `cBody`;compositor 对 torso/back_arm/back_shoulder/front_arm/front_shoulder 五个 cell 都调 `draw_armor(..., body_dye)` (compositor.py:693/742/743/783/784)。**无漏染某子部件**。
- **leg** ✅(除 robe 例外,见 §1 表 & 真缺口①)：13_Leggings 用 `cLegs` (1547),compositor 用 leg_dye (711)。
- **head** ✅：21_Head 用 `cHead` (2144/2151),compositor 用 head_dye (768)。
- **glowmask 是否该染**：游戏中 4 个 glowmask 都用与基底相同的 c*（bodyGlow→cBody 1951、armGlow→cBody 3641、legsGlow→cLegs 1565/1572、headGlow→cHead 2174/2199）。**compositor 完全不绘制任何 glowmask**(`grep -i glow compositor.py` 为空;`draw_armor` 只画基底 sheet,`extract_assets` 也未抽 GlowMask)。→ 这是**整类未实现**(发光层缺失),不是“漏染单层”也不是“误染”：基底层本身染色正确,只是少了叠加的发光层。归为 **P3 观察项**(超出本任务“染色路由”范畴,属功能缺失;且大多 glowmask 是 boss/星柱套甲,与已暂缓的发光类高度重叠)。

---

## 3) hand 复合图 / wings / 鞋 cShoe-vs-cFlameWaker / coat 用 cCoat 专项

- **hand 复合图 dye** ✅：handOn→`cHandOn`(合成层 3833)、handOff→`cHandOff`(合成层 1426);compositor `draw_acc_hand` 在 front_arm(cell 2)/back_arm(cell 20) 用 `_frame_geom` 取 360×224 网格 cell 的 src_rect 后 `apply_dye`(compositor.py:471-480)。槽位 + 几何都对。
- **wings dye** ✅：`cWings`(701);`draw_acc_wing` 传 `acc_dyes["wing"]`,src_rect 用竖排 N 帧的 frame 0 (compositor.py:454-469)。
- **鞋 cShoe vs cFlameWaker(22/23)**：逆向 `UpdateItemDye` (Player.cs:9372-9387) 对鞋槽:type 4874(slot 23)同时设 `cFlameWaker` 与 `cShoe`;type 4822(slot 22)只设 `cFlameWaker`;其余设 `cShoe`。绘制层 `DrawPlayer_14_Shoes` (1761-1772):**鞋精灵的 shader** 在 shoe∈{22,23} 时取 `cFlameWaker`,否则 `cShoe`。**关键:`cFlameWaker` 与 `cShoe` 被赋的是同一个 `dyeItem.dye` 值**(同一配饰槽的同一染料),且 `cFlameWaker` 没有任何独立绘制层(`grep cFlameWaker PlayerDrawLayers.cs` 仅 1764 这一处用于鞋精灵)。→ compositor 对 slot 22/23 仍传 `acc_dyes["shoe"]` 得到**完全相同的颜色**。**不区分是正确的,非缺口**(cFlameWaker 的存在是为了给 FlameWaker 的火焰粒子用 `GetSecondaryShader(cFlameWaker)`,粒子本就不在静态 avatar 里)。
- **coat 用 cCoat 而非 cBody —— long-coat 扩展染料字段辨析**(重要,易误判):
  - 游戏里有**两个不同概念**:
    1. `drawPlayer.body` 的**裙摆扩展** = `GetMatchingBodyExtension(body)` (PlayerDrawLayers.cs:1848-1924),绘制层 `16_ArmorLongCoat` 用 **`cBody`** (1804)。
    2. `drawPlayer.coat` 的**裙摆扩展** = `GetMatchingBodyExtension(coat)` / `GetMatchingBodyExtensionBack(coat)`,绘制层 `13_ArmorBackCoat`(1452)、`16_ArmorLongCoat`(1818) 用 **`cCoat`** (仅 type 5587 设 cCoat,Player.cs:9466-9468)。
  - compositor 的 `_longcoat_ext_slot` + `robe_extensions.json` 映射的是 **body slot → leg-armor 扩展**(键全是 body slot:52/53/73/81/89/168/182/187/198/200-237/251),对应**概念①**(`GetMatchingBodyExtension(body)`)。`compositor.py:737-739` 用 `body_dye` 绘制它 —— **与游戏 `cBody`(1804) 一致,是对的**。
  - 即:用户提示的“coat 用 cCoat”指的是**概念②(独立 coat 装扮槽,item 5587)**,而 compositor **根本没建模独立 coat 槽**(只建模了 body 裙摆=概念①)。所以**当前实现没有“把 coat 错用成 cBody”的 bug** —— 它处理的 body 裙摆本就该用 cBody。**真正的 gap 是“概念②(独立 coat 槽 + cCoat)整块未实现”**(robe_extensions.json 无 coat-keyed 项;render_character 无 coat 入参)。归为 **P3 观察项**(独立 coat 槽极少见,且需要新增数据入口)。

---

## 4) src_rect / sheet_size 传参正确性 (noise 采样依赖正确矩形)

逆向:`ArmorShaderData.Apply` 用每个 DrawData 的 `sourceRect`→`uSourceRect`、`texture.(W,H)`→`uImageSize0`(accessories_spec.md:654-656);noise uv 由 `dye_noise.run_noise_pass` 从 `src_rect/sheet_size` 算 (dye_noise.py:395-401)。

| 路径 | compositor 取 src_rect/sheet_size 处 | 传入值 | 与游戏 idle 帧对照 | 结论 |
|---|---|---|---|---|
| armor head/body/legs/coat扩展 | `_frame_geom` compositor.py:153-168 | cell `(cx,cy,40,56)` / `(360,224)` 9×4 网格 | bodyFrame/legFrame idle = 帧0 在 360×224 sheet | ✅ 正确(noise 测试用 (80,0,40,56)/(360,224)) |
| 条状配饰 back/waist/neck/shield/face/beard/balloon18 | `_acc_strip_frame` :172-184 | `(0,0,texW,56)` / `(texW,sheetH)` | 游戏用 bodyFrame/legFrame 帧0 = `(0,0,40,56)`,sheet 40×1120 | ✅ idle 帧0 `sy=0` 对;shield texW=44 也对 |
| 普通气球 | `_acc_balloon_frame` :186-197 | `(0,0,52,56)` / `(52,224)` | 帧0,4 帧竖排 52×224 | ✅ |
| wings | `_acc_wing_frame` :200-211 | `(0,0,W,H/N)` / `(W,H)` | 帧0(折叠),竖排 N 帧 | ✅ |
| hand 复合图 | `_frame_geom` (经 draw_acc_hand) :478 | front cell2 `(80,0,40,56)` / back cell20 `(80,112,40,56)`,sheet `(360,224)` | 合成前/后臂 idle cell | ✅ 与 accessories_spec.md:662-663 一致 |
| front 前/后半 | `draw_acc_front_half` :482-503 | 前半 `(0,0,20,56)`、后半 `(20,0,20,56)`,sheet `(texW,sheetH)` | 游戏 FrontPart 左半 `Width-=W/2`(3897-3899)、BackPart 右半 `X+=W/2`(3941-3943) | ✅ 半幅矩形 sx 偏移正确 |
| hair Twilight | `_frame_geom(hair_file,0)` :421 | hair sheet 帧0 | 发丝 sheet 自有 uImageSize0 | ✅ |

> **src_rect/sheet_size 全部正确**:armor 走真实 360×224 网格 cell,配饰走各自纹理帧0+全纹理尺寸,front 半幅偏移与游戏一致。noise 类落到任意槽都能拿到正确矩形。
>
> **face 多层 / balloonFront 的 dye 值等价性**:游戏把 face 按 Sets 拆成 cFace/cFaceMask/cFaceFlower/cFaceHead 四个绘制层(2815/2835/2855/2594),balloon 拆成 cBalloon/cBalloonFront —— 但这些 c* 都从**同一配饰槽的同一 `dyeItem.dye`** 取值(只影响 Z 序/绘制层,不影响颜色)。compositor 用单一 `acc_dyes["face"]`/`["balloon"]` 调一次 `apply_dye`,**颜色与游戏完全一致**,差异仅在分层 Z 序(非染色范畴)。**非缺口**。

---

## 5) 已知限制类:归类降级确认 (未误染乱色)

用户已同意暂缓、不算缺口的类别。逐一确认 `dye.py` 已正确识别并合理降级:

| 已知限制类 | dye.py 处理 | file:line | 是否误染乱色 | 确认 |
|---|---|---|---|---|
| Solar 星柱(发光) | emissive tone-map,`_PILLAR_TIME["ArmorSolar"]=5.0`;但实际走 `_solar` 火焰近似(非 noise) | dye.py:432-460/805 | 否,受控火焰渐变 | ✅ 已识别,降级合理 |
| Nebula 星柱(noise+发光) | noise pass + emissive gain 1.4,缺资产回退 `_armor_colored` | :602-617/827 | 否 | ✅ |
| Vortex 星柱(noise+发光) | noise pass + gain 1.5,回退 `_brightness_clip` | :620-635/829 | 否 | ✅ |
| Stardust 星柱(noise+发光) | noise pass + gain 1.35,回退 uColor base | :638-657/831 | 否(starfield) | ✅ |
| HallowBoss (Extra_156 调色板) | noise pass(Extra_156)+ gain 1.0,回退 `_colored_rainbow` | :702-715/839 | 否 | ✅ |
| Reflective (uLightSource=0) | 直通(passthrough) | :569-571/817 | 否 | ✅ APPROX 正确 |
| ReflectiveColor (uLightSource=0) | `_brightness_clip(uColor)` | :574-576/819 | 否 | ✅ |
| 噪声类依赖 uTime/视角 (Gel/Phase/ShiftingSands/ShiftingPearlsands/Fog) | 真 noise 采样 @ uTime=0,缺资产各有 APPROX 回退 | :579-699/823-838 | 否 | ✅ 静态采样降级合理 |

> 其余应精确的:ArmorColored 系列、gradient/rainbow、noise(静态采样 @ uTime=0) 在 dye.py 均走精确/真采样路径,未发现误染。
> 所有降级都收敛到“受控色调/passthrough/回退近似”,**没有任何已知限制类会产出乱色**(emissive tone-map 把 over-unity 折回白色而非硬裁成单色原色,见 dye.py:514-529)。

---

## Findings 表 (severity | 症状 | 逆向证据 | repo 证据 | 建议修法)

| # | severity | 症状 | 逆向证据 file:line | repo 证据 file:line | 建议修法 |
|---|---|---|---|---|---|
| ① | **P2 真缺口** | 穿“袍类”身甲(wearsRobe)且**同时穿腿甲**时,腿甲应被 body 染料染(游戏 `cLegs=cBody`),compositor 仍用 leg 染料 → 腿甲颜色错(若 body 有染料而 legs 无/不同) | `Player.cs:9309-9311` `if(wearsRobe) cLegs=cBody`;腿甲层用 cLegs `PlayerDrawLayers.cs:1547` | `compositor.py:711` `draw_armor(armor_legs,"col",armor["leg_dye"])` 恒用 leg_dye;robe 判定已有 `wears_robe` (723-725) 却只用于绘制顺序 | 在 `_resolve_armor`/`render_character` 计算 `wears_robe` 后,绘制 `armor_legs` 时用 `body_dye if wears_robe else leg_dye`。注意 `_WEARS_ROBE_LEGLESS_ONLY={81}`:slot 81 仅在无腿甲时算 robe,故“robe+腿甲”组合里 81 不触发——与游戏 9309 的 wearsRobe 语义一致(81 在有腿甲时 wearsRobe=false) |
| ② | P3 观察 | 4 类 glowmask(body/arm/legs/head)整体不绘制 → 发光套甲缺发光层(基底仍染色正确,非误染) | bodyGlow→cBody `:1951`、armGlow→cBody `:3641`、legsGlow→cLegs `:1565/1572`、headGlow→cHead `:2174/2199` | `compositor.py` 无任何 glow 绘制(grep 空);`extract_assets` 未抽 GlowMask | 若要补:抽对应 GlowMask 资产,在 draw_armor 之后按 bodyGlowMask/legsGlowMask/headGlowMask 叠加,shader 沿用同层 body_dye/leg_dye/head_dye。与已暂缓的星柱/Boss 发光高度重叠,可一并暂缓 |
| ③ | P3 观察 | 独立 coat 装扮槽(item type 5587,用 cCoat)未建模 → 该单品的裙摆不渲染;但 body 裙摆(概念①)用 cBody 是**正确的** | coat 裙摆用 cCoat `:1452/1818`;cCoat 仅 type 5587 设 `Player.cs:9466-9468`;body 裙摆用 cBody `:1804` | `robe_extensions.json` 仅 body-keyed;`render_character` 无 coat 入参;`compositor.py:737-739` 用 body_dye 画 body 裙摆(与 cBody 一致) | 如需支持独立 coat 单品:新增 coat 槽入参 + coat→leg-ext 映射(`GetMatchingBodyExtension(coat)`),用新 `coat_dye`(cCoat)绘制。当前“body 裙摆用 body_dye”无需改 |

---

## 真缺口清单 (排除已知限制)

1. **(P2) `wearsRobe → cLegs=cBody` 未实现** —— 唯一影响染色正确性的真缺口。触发条件窄:袍类身甲 + 腿甲 + body/legs 染料不同。修法见 Findings ①。

> P3 两项(glowmask 整类未绘制、独立 coat 槽未建模)属功能缺失而非染色路由错误,且与已暂缓范围重叠,本任务可不计为染色缺口。

---

## Caveats / Not Found

- 本审计聚焦“染色 slot 路由 + dye pass + src_rect”,**未逐像素验证 noise/emissive 的视觉准确度**(那已在 `noise_dyes_spec.md` / `dye_passes_spec.md` 验证)。
- `cBackpack`/`cTail`(back 配饰路由到 backpack/tail 层)对应的 DrawPlayer_08/08_1 层在本 renderer **不绘制**(PRD 优先 capes,见 compositor.py:52-54);这些 back 单品被 `_BACK_BACKPACK`/`_BACK_TAIL` 跳过 —— 是**有意降级**,其 dye(cBackpack/cTail)随之不适用,非缺口。
- `cShieldFallback`(冲刺/EoC 盾)、`cFaceHead`(face 进 head 层、用 skin shader 的子情况)、以及 mount/pet/grapple/minecart 等 misc dye 不在本任务 12 类范围内,未审。
- robe 判定的 slot 集合 `_WEARS_ROBE_BODIES`(compositor.py:71-74)是否与 `Player.cs SetMatch`(36776-36886)逐项一致,本次未逐 slot 复核(沿用 accessories_spec.md:396-401 已记录的集合);缺口①的修法不依赖该集合的完备性,只依赖“wears_robe 为真时换 dye 槽”。
- 逆向源为反编译 v1.4.5.6(`temp/decomp/full/`,见 accessories_spec.md:8);行号对应该副本。
