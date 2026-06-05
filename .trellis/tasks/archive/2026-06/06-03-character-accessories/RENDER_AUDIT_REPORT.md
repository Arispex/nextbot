# 角色立绘渲染 完整性审计报告

> 范围：功能装备 / 社交装备 / 装备染料 / 功能配饰 / 社交配饰 / 配饰染料。
> 方法：�comparison 全部对照**反编译客户端**（`temp/decomp/full/`，Terraria 1.4.5.6）+ 实际客户端贴图（已挂载 Steam 安装）。
> 状态：审计完成；12 个确认 bug 已修复并验证（**未提交**，待 review）；其余缺口列为建议。

---

## TL;DR

| 检查点 | 结论 |
|---|---|
| **1. 贴图齐全性** | ✅ **完全齐全**。装备 + 12 类配饰贴图与客户端逐 slot 一一对应，0 缺失，0 多余；每个被引用的 netId 都有对应 PNG。 |
| **2. 帧/层齐全性** | ⚠️ 核心框架全对（idle 帧、性别 cell、手部复合图 cell、染料路由、vanity 覆盖），但发现 **12 个"鞋 bug 同类"渲染错误**（贴图在但绘制错）→ 已全部修复。 |
| **3. 有无缺口** | 仍有少数缺口未实现（glowmask 发光层、robe 背片、tail 等），均已逆向定性，列为建议；动画/发光染料维持既有"已知限制"。 |

---

## 1. 贴图齐全性 — ✅ 完全齐全

`assets/*.png` 的 slot 集合与客户端 `Content/Images` 的 xnb slot 集合**完全相等**：

| 类别 | 客户端 | repo | 缺失 |
|---|---|---|---|
| Armor_Head | 292 | 292 | 0 |
| ArmorBody | 203 | 203 | 0 |
| Armor_Legs | 253 | 253 | 0 |
| Wings | 51 | 51 | 0 |
| Acc_Back | 39 | 39 | 0 |
| Acc_Balloon | 19 | 19 | 0 |
| Acc_Shoes | 30 | 30 | 0 |
| Acc_Waist | 16 | 16 | 0 |
| Acc_Neck | 12 | 12 | 0 |
| Acc_Face | 23 | 23 | 0 |
| Acc_Shield | 9 | 9 | 0 |
| Acc_HandsOn | 24 | 24 | 0 |
| Acc_HandsOff | 15 | 15 | 0 |
| Acc_Front | 16 | 16 | 0 |
| Acc_Beard | 4 | 4 | 0 |

**真缺口测试**：遍历 `equip_slots.json`(634) + `accessory_slots.json`(201) 的每个 netId→slot，**0 个引用指向缺失贴图**。结论：不存在"装备/配饰因贴图缺失而渲染不出"的情况。

---

## 2. 帧/层齐全性 — 已修 12 个 bug

### 方法
1. 从 `LegacyPlayerRenderer.cs:164-250` 提取 idle 渲染的**完整层序**（权威），逐层对照 compositor 是否都画、顺序是否一致。
2. 对每个绘制的层，对照 `PlayerDrawLayers.cs` 的 `DrawPlayer_XX_*` 方法核对 idle 取的**帧/cell/偏移**。

### 核心框架：全部正确 ✅
- idle 取顶帧（bodyFrame.Y=0 / legFrame.Y=0 / wingFrame=0）；
- 性别 cell（torso m0/f18、前臂2、后臂20、前肩9/27、后肩10/28）+ female +2 行位移；
- 手部复合图 360×224 grid 的 cell 选择（前臂 cell2、后臂 cell20）逐像素对；
- body armor 五子部件（躯干/前臂/后臂/前肩/后肩）全画；
- 染料路由（装备 head/body/leg + 12 配饰逐槽）与 `UpdateItemDye` 一致，src_rect/sheet_size 全对；
- vanity 逐部件 last-non-air 覆盖、hideVisuals 与 `UpdateVisibleAccessory` 一致。

### 已修复的 12 个 bug（均逆向取证）

**A. 层序错（画了但被覆盖 / 遮挡关系反 —— "鞋 bug 同类"）**
| # | 严重 | 问题 | 逆向证据 |
|---|---|---|---|
| 1 | P0 | Shield 画在最外层（盖住前臂）→ 应在前臂**之前**（被臂遮挡） | LegacyPlayerRenderer.cs:230 vs 238 |
| 2 | P0 | FrontAcc 背半画在 head **之前**（被头盖住）→ 应在 head/face **之后** | :229 |
| 3 | P1 | handOff 复合图画在身体甲背臂**之前**（被甲袖盖住）→ 应在**之后** | PlayerDrawLayers.cs:1365/1421 |

**B. 可见性门缺失（该隐藏的没隐藏 / 该抑制的没抑制）**
| # | 严重 | 问题 | 逆向证据 |
|---|---|---|---|
| 4 | P1 | 鞋配饰缺腿甲抑制：legs∈{55,63,67,106,138,140,143,217,222,226,228} 应不画鞋配饰（shoe==15 例外） | PlayerDrawLayers.cs:1218-1246 |
| 5 | P1 | 皮肤隐藏门缺失：躯干皮 body∈{21,22,82,83,93} / 腿皮 legs∈{20,21,214,215,216}∨body93∨IsBottomOverridden 应隐藏（遮罩甲/美人鱼腿会露裸皮） | PlayerDrawSet.cs:1755-1756 |

**C. 漏层（整层没画）**
| # | 严重 | 问题 | 逆向证据 |
|---|---|---|---|
| 6 | P1 | BackHead 漏层：6 个头盔(133,224,242-245)有背片(246-249,252,253)画在身后，整层未画 | ArmorIDs.cs:14 FrontToBackID + DrawPlayer_01_3_BackHead |
| 7 | P1 | 发下层脸漏：DrawInFaceUnderHairLayer={5}(眼罩)应画在**前发之前**，原全画在发之上 | PlayerDrawLayers.cs:2631 |

**D. 偏移/帧公式错（位置偏几像素）**
| # | 严重 | 问题 | 逆向证据 |
|---|---|---|---|
| 8 | P1 | 端游翅膀 47/49/50/51 走默认偏移公式，偏 5~9px；且 47/49/51 漏 Width/Height-=2 裁边 | DrawPlayer_09_Wings + OffsetsPlayerHeadgear |
| 9 | P1 | 脸 19 漏 GetFaceDrawOffset (0,-6)·Directions | Player.cs:4384-4386 |
| 10 | P1 | 前饰 13 漏 GetFrontDrawOffset (-2,0) | Player.cs:4708-4717 |
| 11 | P2 | 轮滑鞋 27-30 漏 (0,2)·Directions | Player.cs:4729-4737 |

**E. 染料路由**
| # | 严重 | 问题 | 逆向证据 |
|---|---|---|---|
| 12 | P2 | 穿袍身甲时腿甲应用 body 染料（cLegs=cBody），原恒用 leg 染料 | Player.cs:9309-9311 |

> 全部 12 项已修复，并对照反编译源码二次复核（trellis-check 通过，无需更正）。

---

## 3. 剩余缺口（未实现 —— 建议项，按优先级）

### P1 —— body armor glowmask 发光层（影响最大）
约 11 个端游套装（**227 Nebula、208 Arkhalis、238/260、239、194、179、190、176、205、237**）的 ArmorBody 贴图是 **360×448**，下半 224 行是发光像素。客户端 `DrawCompositeArmorPiece` 对躯干/臂/肩每个子部件，从 `sourceRect.Y += 224` 再画一遍发光层。
- **机制已逆向**：发光色 `bodyGlowColor`/`armGlowColor` 在 `PlayerDrawSet.cs:458-700` 按套装逐一设定；多数 alpha=0（**叠加混合**），且 **Nebula 等用脉动亮度变量（动画）**。
- **未实现原因**：这与已暂缓的**柱/Boss 动画发光染料同属一类**（动画 + emissive + 特殊混合），需在 compositor 增加叠加混合 + 代表帧时间。建议作为独立后续任务，与发光染料一起做。

### P2/P3 —— 其它
| 项 | 严重 | 说明 | 逆向 |
|---|---|---|---|
| ArmorBackCoat 袍背片 | P2 | robe/coat 的**背半**（`coat`→GetMatchingBodyExtensionBack）画在身体最后面，compositor 只画了前半（body→GetMatchingBodyExtension）。触发取决于 `coat` 字段 | DrawPlayer_13_ArmorBackCoat |
| Tails 尾饰 | P2 | `tail` 字段（tail-flagged back 物品的 backSlot）单独画成尾巴，未实现 | DrawPlayer_08_1_Tails |
| shoe==15 抑制腿甲 | P3 | shoe==15(FrogLeg) 非袍时还应抑制**腿甲 + 默认裤鞋**，目前只抑制了鞋配饰/裸皮 | PlayerDrawLayers.cs:1540/1576 |
| Backpacks | P3 | 单一套装(266/235/218)显示背包 | DrawPlayer_08_Backpacks |
| leg/head glowmask | P3 | 腿甲/头盔独立 GlowMask 贴图同样未画 | 同 glowmask |
| UseSkinColor 盔 274/277 | P3 | 应按肤色画 | ArmorIDs.Head.Sets |
| coat 装扮槽(item 5587) | P3 | 独立 coat costume 槽整块未建模 | — |

### 已知限制（既往已与你确认，维持现状）
动画/发光/视角依赖染料：Solar/Vortex/Nebula/Stardust 柱染料、HallowBoss Boss 染料、Reflective 反射、依赖实时 uTime/视角的噪声类。已被正确识别并降级（受控色调/passthrough），**不会染成乱色**。

---

## 验证

- 测试：`tests/test_terraria_render.py` **35/35 通过**（修复前 21 → 新增 14 个回归测试，覆盖盾遮挡/handOff/裸皮隐藏/BackHead/鞋抑制/发下脸/翅膀偏移等，均确认"修前失败、修后通过"）。
- Lint/Type：ruff 0 错、pyright 0 错。
- 视觉抽查：BackHead 背片正常显示；普通装备角色无回归。
- 复核：trellis-check 对照反编译源码逐项核对，**通过、无需更正**、回归安全。
- 审计明细文档：`research/audit_frames_hands_shield_shoes.md`、`audit_frames_back_overbody.md`、`audit_frames_body_equip.md`、`audit_dyes.md`。

## Git 状态
- 已提交：`03c7c11`（配饰功能 + 鞋层序）。
- **未提交**：本次 12 个渲染修复（`compositor.py` +214、`tests` +331、`wing_meta.json`）—— 待你 review 后 `提交`。
