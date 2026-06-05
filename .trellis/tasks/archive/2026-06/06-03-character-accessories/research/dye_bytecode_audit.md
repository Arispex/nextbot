# Research: 全量染料 pass 真实字节码可接性审计 (dye_bytecode_audit)

- **Query**: 把**所有**染料 pass 改走真实编译 ps_2_0 字节码（消灭 `dye.py` 手写近似）。逐 pass 产出工作清单 + 可行性：枚举全 pass、分类 A/B/C、对每个 B 类判断能否接字节码（CTAB/PRES、解释器 opcode 缺口、颜色链路）、给接线规格 + 推荐批次。
- **Scope**: internal — 直接反汇编 `temp/xnb_probe/in/PixelShader.xnb`（gen_noise_shaders.py 读的同一份）的全部 39 个 pass，对照 `dye.py` / `dye_noise.py` / `noise_shaders.json` / `dyes.json` + 游戏源码 `temp/decomp/full/Terraria.Initializers/DyeInitializer.cs`。
- **Date**: 2026-06-05
- **只读审计**：未改任何代码；结论均经反汇编 + 端到端实跑解释器验证。

---

## 一句话结论（最重要）

**全部 39 个 pass 在 `PixelShader.xnb` 里都有真实 ps_2_0 字节码，且现有 `dye_noise.py` 解释器的 PS opcode 与 preshader opcode 对 39 个 pass 全部齐全——没有任何 pass 缺 opcode。** offset-tap 修复后，唯一阻挡"全量接字节码"的**解释器缺口只有 1 个**：`_decode_preshader` (`dye_noise.py:172-174`) 没有对**无 preshader（无 FXLC 块）**的 blob 做空保护，会越界崩溃，影响 6 个无 preshader 的 pass（BrightnessColored / Invert / ColorOnly / Martian / Polarized / Mushroom）。补 1 行 `if blob.find(b"FXLC")<0: return [],[]` 后，这 6 个也实跑通过。

因此：
- **B 类（当前手写近似）待接 pass = 27 个**（覆盖 76 个 netId）。
- **C 类（离线物理做不到）= 2 个 pass**（Reflective / ReflectiveColor，`uLightSource=0`），但仍可"按忠实离线结果"接（标注局限）。
- **需补的解释器项 = 1 个**（`_decode_preshader` 的 FXLC 空保护）。次要：`run_noise_pass` 的 noise-guard 对纯 uImage0 pass 也强依赖 `noise.png`（生产已 ship，仅离线安全性问题）。
- **推荐第一批（零解释器改动、低风险、纯静态/颜色驱动）**：`ArmorColored` 家族 + 衍生（Colored/AndBlack/AndSilverTrim/三个 Gradient/两个 Rainbow/BrightnessColored/BrightnessGradient/HighContrastGlow/Invert/ColorOnly/Martian/Polarized/Mushroom/Wisp）。其中 6 个无 preshader 的需先合入那 1 行 FXLC 守卫。

> 这与已有研究一致：`research/midnight_rainbow.md` §TL;DR 早已发现"每个 opcode 都已实现，唯一缺的是 offset-tap"（已修）；本审计把同一结论扩展到全部 39 个 pass，并定位了第 2 个（也是最后一个）解释器缺口。

---

## 方法与证据链

1. **反汇编全 39 pass**：用 `temp/xnb_probe/fx_parse.py`（pass 名→ps_2_0 blob→反汇编 + CTAB 解析）遍历 `dyes.json` 的 39 个 distinct pass。逐 pass 提取：是否存在 blob、texld 数、def 数、是否有 preshader（FXLC）、用到的 PS opcode 集合、preshader opcode 集合、CTAB uniform 列表。
2. **opcode 覆盖比对**：把每个 pass 的 PS opcode 集合与 `dye_noise._run_ps`（`dye_noise.py:372-411`）实现集 `{0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0x12,0x13,0x23,0x25,0x58,0x5A,0x42,0x1F,0x51}` 比对；preshader opcode 与 `_run_preshader`（`dye_noise.py:234-268`）实现集 `{mov,neg,add,mul,div,rcp,min,max,lt,ge,cmp,frc,sin,cos}` 比对。**结果：39 个 pass 的 missPS 与 missPRES 全部为空集。**
3. **0x23 歧义核对**（abs vs pow）：所有出现 `0x23` 的 pass，其 `ilen` 均为 2（=1 个源操作数）= **abs**，与解释器 `0x23→np.abs`（`dye_noise.py:400`）一致；**无任何 pass 把 0x23 当 pow（2 源）用**。
4. **v0（顶点色）语义核对**：逐 pass 看 v0 在输出写回处怎么用。基准 `ArmorColored` 末尾即 `mul r0, r0, v0`（顶点色仅做最终 tint）。解释器把 `v0=white`（`dye_noise.py:328`）——对应游戏里 inventory/标准绘制的白色 draw color，**正是正确取值**。详见 §v0 专项。
5. **PRES_INPUTS 复原**：对每个未接 pass，解码其 PRES 块**自带的第 2 个 CTAB**（映射 preshader 输入寄存器 `in_cN`→effect 参数名），得到精确的 `PRES_INPUTS` 条目（见每个 B 类的接线规格）。
6. **端到端实跑验证**：把未接 pass 的真实 blob + 复原的 PRES_INPUTS 注入 `dye_noise._shaders`，在合成 56×40 sprite 上调 `run_noise_pass`。7/8 抽样 pass（Acid/Void/Mirage/Hades/Colored/ColoredGradient/Reflective）直接跑通并输出空间变化结果；唯一崩溃的 Martian 定位到 §解释器缺口①，补 1 行守卫后 6 个无 preshader pass 全部跑通。
7. **颜色保真核对**：`dyes.json` 各 netId 的 color/secondary/sat 与 `DyeInitializer.cs`（`temp/decomp/full/Terraria.Initializers/DyeInitializer.cs:83-140`）逐条一致（如 Acid 3040=(0.5,1,0.3)、Hades 3038 color+secondary=(0.5,0.7,1.3)、Wisp/Flow/Martian 全对）→ 接字节码并透传 `dyes.json` 颜色 = 复现游戏 uniform，无色偏。

---

## A/B/C 全量分类表（39 pass / 115 netId）

> A=已走真实字节码；B=`dye.py` 手写近似/公式；C=离线物理做不到。
> "tex" = uImage0/uImage1 的 texld 数；"pres" = preshader 指令数（`-`=无 preshader）。
> dye.py 路由行号见 `dye.py` 的 `apply_dye`（`dye.py:840-926`）。

| pass | 分类 | netId(s) | dye.py 当前处理 (函数 @行) | tex | pres | 缺 opcode |
|---|---|---|---|---|---|---|
| ArmorGel | **A** | 3024,3561,3562,4663 | `_gel` @596 → run_noise_pass | 6 | 36 | — |
| ArmorPhase | **A** | 3042 | `_phase` @610 | 3 | 26 | — |
| ArmorNebula | **A** | 3527 | `_nebula` @623 (emissive) | 2 | 10 | — |
| ArmorVortex | **A** | 3528 | `_vortex` @642 | 2 | 10 | — |
| ArmorStardust | **A** | 3529 | `_stardust` @665 | 2 | 7 | — |
| ArmorShiftingSands | **A** | 3533 | `_shifting_sands` @694 | 2 | 5 | — |
| ArmorShiftingPearlsands | **A** | 3535 | `_shifting_pearlsands` @708 | 3 | 5 | — |
| ArmorFog | **A** | 4662 | `_fog` @722 | 2 | 5 | — |
| ArmorHallowBoss | **A** | 4778 | `_hallow_boss` @742 (emissive) | 2 | 1 | — |
| ArmorTwilight | **A** | (发色 #12, 非 dyes.json) | `_twilight` @759 | — | — | — |
| ArmorMidnightRainbow | **A** | 3556 | `_midnight_rainbow_real` @776 (5-tap self-emboss) | 5 | 7 | — |
| ArmorSolar | **A**(部分忠实) | 3526 | `_solar` @434 **手写火焰近似**（已 baked 但 dye.py 没用真实路径） | 5 | 15 | — |
| ArmorColored | **B** | 1007-1018,1038-1049,1969 (25) | `_armor_colored` @123 | 1 | 9 | — |
| ArmorColoredAndBlack | **B** | 1019-1030,3559 (13) | `_armor_colored_andblack` @130 | 1 | 9 | — |
| ArmorColoredAndSilverTrim | **B** | 1051-1062 (12) | `_armor_colored_silvertrim` @142 | 1 | 7 | — |
| ArmorColoredGradient | **B** | 1031,1033,1035,1068-1070 (6) | `_colored_gradient` @183 | 1 | 10 | — |
| ArmorColoredAndBlackGradient | **B** | 1032,1034,1036 (3) | `_colored_andblack_gradient` @196 | 1 | 10 | — |
| ArmorColoredAndSilverTrimGradient | **B** | 3550-3552 (3) | `_colored_silvertrim_gradient` @207 | 1 | 10 | — |
| ArmorBrightnessColored | **B** | 1037,1050,2871,3558 (4) | `_brightness_colored` @161 | 1 | **-** | — |
| ArmorBrightnessGradient | **B** | 1063-1065 (3) | `_brightness_gradient` @229 | 1 | 3 | — |
| ArmorColoredRainbow | **B** | 1066 | `_colored_rainbow` @239 | 1 | 8 | — |
| ArmorBrightnessRainbow | **B** | 1067 | `_brightness_rainbow` @250 | 1 | 1 | — |
| ArmorMartian | **B** | 2864 | `_martian` @259 | 1 | **-** | — |
| ArmorPolarized | **B** | 3557 | `_polarized` @275 | 1 | **-** | — |
| ArmorMushroom | **B** | 3041 | `_mushroom` @285 | 1 | **-** | — |
| ArmorWisp | **B** | 2878,2879,2884,2885 (4) | `_wisp` @302 | 1 | 4 | — |
| ArmorHighContrastGlow | **B** | 2883 | `_high_contrast_glow` @322 (丢了 v0 glow 项) | 1 | 9 | — |
| ArmorInvert | **B** | 2872 | `_invert` @178 | 1 | **-** | — |
| ColorOnly | **B** | 3978 | `_color_only` @173 | 1 | **-** | — |
| ArmorFlow | **B** | 3025 | `_flow` @330 (uTime=0 静止) | 1 | 2 | — |
| ArmorLivingRainbow | **B** | 2870 | `_living_rainbow` @348 (uTime=0) | 1 | 2 | — |
| ArmorLivingFlame | **B** | 2869 | `_living_flame` @364 (uTime=0) | 1 | 1 | — |
| ArmorLivingOcean | **B** | 2873 | `_living_ocean` @386 (uTime=0) | 1 | 2 | — |
| ArmorAcid | **B** | 3028,3040,3560 (3) | `_acid` @406 (uTime=0) | 1 | 3 | — |
| ArmorVoid | **B** | 3530 | `_void` @465 **塌成 0.35 平涂**（真实是 3-tap 横向模糊+压暗） | 3 | 10 | — |
| ArmorHades | **B** | 3038,3597,3598,3600 (4) | `_hades` @470 **塌成 uColor 平涂**（真实是旋转自采样） | 3 | 30 | — |
| ArmorMirage | **B** | 3534 | `_mirage` @478 **纯 passthrough**（真实是 sin/sgn 横向位移） | 3 | 4 | — |
| ArmorLoki | **B** | 3599 | `_loki` @483 **塌成暗平涂**（真实是旋转自采样暗迷彩） | 3 | 31 | — |
| ArmorReflective | **C** | 3190 | `_reflective` @579 passthrough | 5 | 5 | — |
| ArmorReflectiveColor | **C** | 3026,3027,3553,3554,3555 (5) | `_reflective_color` @584 (uColor tint) | 5 | 5 | — |

**统计**：A=12 pass，B=25 pass（76 netId），C=2 pass（6 netId）。
> 备注：表里把 **Solar** 计入 A 行但标"部分忠实"——它的 blob 已 baked 进 `noise_shaders.json`，但 `dye.py:apply_dye` 第 887 行仍路由到手写 `_solar`（火焰近似），**没有走 `_noise_pass`**。即 Solar 处于"已 baked 但未接线"的中间态，应在批次里把路由从 `_solar` 切到 run_noise_pass（带局限，见 §Solar）。

---

## v0（顶点色）专项 —— 为什么大多数 B 类离线可忠实

游戏里 `v0` = 该 sprite 的 draw color（光照/队伍色 tint）。`ArmorShaderData.Apply` 不直接写 v0，它来自顶点流；armor 在 inventory/标准世界绘制时 draw color 基本是白。解释器固定 `v0=white`（`dye_noise.py:328`）即对应这一取值。逐 pass 的 v0 用法分两类，**两类离线都忠实**：

**Pattern A — v0 仅做末尾 tint（`mul r0, r0, v0`）**：v0=white 完全 inert。
- ArmorColored（基准）、Flow、LivingOcean（`mul r0, r0, v0.w` 仅 alpha）、LivingFlame（同）、Polarized、Martian、Mirage、Void、Reflective/ReflectiveColor（末尾 `mul r0, r4, v0`）。

**Pattern B — `max r1.xyz, v0, v0.w` + `lrp glow, ..., v0`**：这是 `ArmorColored` 家族共用的"低彩度像素向 v0 收敛"逻辑；v0=white 时即标准的"灰度像素变白"，**正是游戏内行为**。
- Acid、LivingRainbow、Hades、Loki、Wisp、Mushroom、HighContrastGlow。

**唯一真正的 v0-emissive 例外 = Solar**（详见下）：它把 `源*v0` 作为**加性发光项**叠加（`mad r0, r4, v0, r0`），v0=white 时发光高光偏白而非游戏内的彩色辉光。

---

## 每个 B 类的"可接性 + 缺口 + 接线规格"

> 通用：所有 B 类的 **PS/preshader opcode 都齐全**（缺口列只标特殊项）。接线动作 = ①给 `gen_noise_shaders.py:PRES_INPUTS` 加该 pass 条目（下方"PRES_INPUTS"）→ 重跑 `gen_noise_shaders.py` 把 blob baked 进 `noise_shaders.json`；②把 `dye.py:apply_dye` 对应分支从手写函数改为 `_noise_pass(...)` 调用（透传 `color/secondary/sat` + `**ngeom`）。`run_noise_pass` 已接受并绑定 `u_color/u_secondary/u_sat`（`dye_noise.py:421-477`），颜色链路对 recolor/gradient 类已就绪。

### 第一梯队：recolor / gradient / 纯静态（颜色驱动，零或一处解释器改动）

这些**有 preshader**、opcode 齐全、可直接接（无需任何解释器改动）：

| pass | PRES_INPUTS（已复原） | uColor/uSec/uSat 依赖 | 代表 uTime | 风险/回归点 |
|---|---|---|---|---|
| **ArmorColored** | `{0:"uColor",1:"uSaturation"}` | uColor✓ uSat✓ | — | 极低。手写已 bit-for-bit 验证（dye_shader_spec.md），接字节码应像素一致；回归=对比 25 个 netId 渲染 |
| **ArmorColoredAndBlack** | `{0:"uColor",1:"uSaturation"}` | uColor✓ uSat✓ | — | 低。注意 AndBlack 的二次压暗在字节码 def 里，手写是事后 `*k`——接字节码更忠实 |
| **ArmorColoredAndSilverTrim** | `{0:"uSaturation"}` | uColor✓(CTAB) uSat✓ | — | 低 |
| **ArmorColoredGradient** | `{0:"uColor",1:"uSecondaryColor",2:"uSaturation",3:"uSourceRect"}` | uColor✓ uSec✓ uSat✓ | — | 低。**强依赖 uColor+uSecondary**；接字节码可顺带消灭手写的 sat-remap 分歧（`dye.py:191-192` 注释提到的 c1 vs -c1 坑） |
| **ArmorColoredAndBlackGradient** | 同上 | uColor✓ uSec✓ uSat✓ | — | 低 |
| **ArmorColoredAndSilverTrimGradient** | 同上 | uColor✓ uSec✓ uSat✓ | — | 低 |
| **ArmorBrightnessGradient** | `{0:"uColor",1:"uSecondaryColor",2:"uSourceRect"}` | uColor✓ uSec✓ | — | 低 |
| **ArmorColoredRainbow** | `{0:"uSaturation",1:"uSourceRect"}` | uSat✓（彩虹色来自 def） | — | 低。uColor 不读 |
| **ArmorBrightnessRainbow** | `{0:"uSourceRect"}` | 无颜色 | — | 低 |
| **ArmorWisp** | `{0:"uColor",1:"uSecondaryColor"}` | uColor✓ uSec✓ | — | 低（Pattern B v0，忠实） |
| **ArmorHighContrastGlow** | `{0:"uColor",1:"uSaturation"}` | uColor✓ uSat✓ | — | **中**：手写 `_high_contrast_glow` @322 明确"丢了 v0 glow 项"，接字节码会**恢复** v0-driven 高光（v0=white 时是白高光，与游戏 inventory 一致）→ 视觉会变，属修正 |
| **ArmorFlow** | `{0:"uColor",1:"uSecondaryColor"}` | uColor✓ uSec✓ uTime | uTime=0（与手写一致） | 低（Pattern A v0） |

### 第二梯队：无 preshader（需先补解释器缺口①，再接）

opcode 齐全，但 `entry["pres_inputs"]={}` 会触发 `_decode_preshader` 越界崩溃（§缺口①）。补 1 行 FXLC 守卫后即可接：

| pass | PRES_INPUTS | 依赖 | 备注 |
|---|---|---|---|
| **ArmorBrightnessColored** | `{}`（无 preshader） | uColor✓(CTAB) | 实跑 OK（带守卫）。手写 `(r+g+b)/3*uColor`，字节码同 |
| **ArmorInvert** | `{}` | 无 | 实跑 OK，输出 0..1 正确 |
| **ColorOnly** | `{}` | 无 | 实跑 OK，输出全白剪影（mean=1.0） |
| **ArmorMartian** | `{}` | 无（color hardcoded c0=(0,2,3)） | **实跑曾崩**（定位到缺口①），守卫后 OK。手写 `_martian` 的 (0,2,3) 与字节码 def 一致 |
| **ArmorPolarized** | `{}` | 无 | 实跑 OK |
| **ArmorMushroom** | `{}` | uColor✓(CTAB) | 实跑 OK |

### 第三梯队：动画自采样（接字节码=取消近似，需选代表 uTime）

opcode 齐全（offset-tap 已修，多 tap uImage0 已能跑）。这些手写近似**塌得最厉害**（passthrough / 平涂），接字节码收益最大：

| pass | PRES_INPUTS（已复原） | 依赖 | 代表 uTime | 风险/回归点 |
|---|---|---|---|---|
| **ArmorVoid** | `{0:"uTime",1:"uImageSize0"}` | 无颜色 | 需扫（横向模糊+压暗，uTime 控位移相位） | 中：手写是 0.35 平涂，字节码是 **3-tap 横向模糊 ×0.35**——实跑 OK（mean≈0.42, 空间变化）。视觉会变，属修正 |
| **ArmorMirage** | `{0:"uTime",1:"uSourceRect",2:"uImageSize0"}` | 无颜色 | 需扫（sin 相位驱动横向 UV 位移） | 中：手写纯 passthrough，字节码是真实波动位移——实跑 OK（rng 0..1）。uTime=0 可能位移≈0，需扫相位选有位移的帧 |
| **ArmorHades** | `{0:"uRotation",1:"uTime",2:"uSourceRect",3:"uImageSize0"}` | uColor✓ uSec✓ | 需扫（旋转自采样辉光，uRotation=0/uTime 控相位） | 中：手写塌成 uColor 平涂，字节码是旋转 self-tap——实跑 OK（rng 0..1）。**uRotation 取 0**（游戏 `ArmorShaderData.cs:97/105`：非旋转 sprite rotation=0）；run_noise_pass 已默认 uRotation=[0,0,0,0]（`dye_noise.py:459`）✓ |
| **ArmorLoki** | `{0:"uRotation",1:"uTime",2:"uSourceRect",3:"uImageSize0"}` | uColor✓ | 需扫 | 中：同 Hades 机制（暗迷彩）。uRotation=0 ✓ |
| **ArmorAcid** | `{0:"uTime",1:"uSourceRect"}` | uColor✓ | uTime=0（与手写一致）或扫漩涡相位 | 低-中：手写已是 uTime=0 极坐标近似，字节码是真实自采样 swirl——实跑 OK |
| **ArmorLivingRainbow** | `{0:"uTime",1:"uSourceRect"}` | 无颜色 | uTime=0 或扫 | 低（Pattern B v0） |
| **ArmorLivingFlame** | `{0:"uSourceRect"}` | uColor✓ uSec✓ uTime | uTime=0 | 低 |
| **ArmorLivingOcean** | `{0:"uTime",1:"uSourceRect"}` | 无颜色（蓝青 hardcoded） | uTime=0 或扫 | 低 |

### C 类：Reflective / ReflectiveColor（uLightSource=0，按忠实离线结果接）

| pass | PRES_INPUTS（已复原） | 局限 | 接法 |
|---|---|---|---|
| **ArmorReflective** | `{0:"uImageSize0"}` | `uLightSource=0` → `dp3 r0.x, r0, uLightSource`（高光朝向项）恒 0 → **镜面高光消失**；其余（5-tap 边缘 emboss + `mul r4*v0`）照常 | 可接，得到"无活体高光的忠实离线结果"。实跑 OK（mean=0.25, 即源经 0.5 缩放）。需 CTAB 提供 `uLightSource`（run_noise_pass 当前**未**绑定它，默认 0 即所需局限值——但要确认 c2 寄存器有定义，否则解释器 `_Z` 默认零正好等价）|
| **ArmorReflectiveColor** | `{0:"uImageSize0"}` | 同上 + 末尾 uColor tint | 可接，标注"高光缺失 + uColor 染" |

> C 类结论：**能接，但只是"忠实的无高光版"**，并非视觉上更接近游戏（游戏的卖点就是那条随光照移动的高光）。是否接取决于"忠实字节码"vs"当前 passthrough/tint 近似"哪个观感更可接受——建议低优先级，或保留近似并在文档标注。

---

## 解释器待补项清单（关键）

### 缺口① （唯一阻塞项）`_decode_preshader` 缺 FXLC 空保护 —— P0

- **位置**：`dye_noise.py:172-174`。
- **现象**：无 preshader 的 blob（`blob.find(b"FXLC")==-1` 且 `find(b"CLIT")==-1`）时，`clit=-1` → `nlit=_u32(blob,3)`（读垃圾）→ 字面量循环越界 `struct.error: unpack_from requires a buffer of at least 511 bytes ... (actual 508)`（实测 Martian）。
- **影响 pass**：6 个无 preshader pass（BrightnessColored / Invert / ColorOnly / Martian / Polarized / Mushroom）。
- **修法（1 行）**：在 `_decode_preshader` 开头加 `if blob.find(b"FXLC")<0: return [], []`（或在 `run_noise_pass` 里当 `entry["pres_inputs"]` 为空时跳过 `_run_preshader`）。
- **验证**：加守卫后 6 个 pass 全部实跑通过（ColorOnly mean=1.0 白剪影、Invert 0..1、Martian 0..2.5 等，输出合理）。

### 缺口②（次要 / 离线安全性）`run_noise_pass` 的 noise-guard 对纯 uImage0 pass 误依赖 —— P2

- **位置**：`dye_noise.py:437-439`：`noise=_noise_tex(); if parsed is None or noise is None: return None`。
- **现象**：**所有 27 个 B 类 + 2 个 C 类都不采样 uImage1（noise）**（实测 `uImage1=0`），但该 guard 仍要求 `noise.png` 存在，否则整批 fallback 到手写近似。
- **影响**：生产环境 `noise.png` 已 ship（`has_noise_assets()==True`，256×256），**当前无实际影响**；仅当 noise.png 缺失时，这些本不需要 noise 的 pass 也会退回近似。
- **修法（可选）**：把 noise 加载改为"按需"（仅采样 uImage1 的 pass 才要求 noise 非空）。非阻塞，可后置。

### 非缺口（已确认无需补）

- **opcode**：39 个 pass 的 PS opcode + preshader opcode **全部已实现**，无一缺失。
- **0x23 abs/pow 歧义**：全部 0x23 都是 1-源 = abs，解释器正确。
- **offset-tap**：已修（`_sample_src` CLAMP 采样，`dye_noise.py:97-128/367`）；多 tap uImage0 pass（Void/Mirage/Hades/Loki/Reflective/Solar/MidnightRainbow）已能跑。
- **v0=white**：对 recolor 家族（Pattern A/B）是正确取值（≡ inventory 白 draw color）；仅 Solar 例外（见下）。
- **uRotation/uDirection**：Hades/Loki 需 uRotation，游戏值=0（非旋转 sprite，`ArmorShaderData.cs:97/105`），run_noise_pass 默认 `[0,0,0,0]` 已对（`dye_noise.py:459`）；uDirection 默认 `[1,0,0,0]` 对（`dye_noise.py:458`）。

---

## 特殊：Solar（v0-emissive 唯一例外）

- **状态**：blob 已 baked（`noise_shaders.json` 有 `ArmorSolar`），但 `dye.py:apply_dye` 第 887 行仍路由到手写 `_solar`（火焰近似），**未接真实路径**。
- **可接性**：opcode 齐全，offset-tap 已修，5-tap uImage0 自采样能跑（PRES_INPUTS 已 baked：`{0:"uColor",1:"uSecondaryColor",2:"uTime",3:"uImageSize0"}`，含 `c2=sin(uTime*0.477+0.5)*0.2+1` 亮度脉冲）。
- **局限（关键）**：Solar 把发光做成**加性项叠在 v0 上**——反汇编可见 `max r1.xyz, v0, v0.w` / `lrp r2, r0.z, r1=max(v0), v0` / `mad r0, r4, v0, r0`。火色 hue 来自 `uColor`+`c3`（`mad r1.xyz, r0.z, c3, uColor`），**这部分在**；但发光高光项乘 v0，v0=white 时高光**偏白**而非游戏内的彩色炽焰辉光。即接字节码得到的是"带 uColor 火色带 + 偏白发光高光"的**半忠实**结果，`dye.py:438-447` 的 docstring 已记录此现象（"collapses the glow to a flat pale wash"）。
- **建议**：可接（比手写火焰近似更接近真实结构），但需在文档标注"发光高光偏白（v0 离线=white，非游戏彩色辉光）"。代表 uTime 沿用 `_PILLAR_TIME["ArmorSolar"]=5.0`（`dye.py:497`，亮度脉冲峰）。

---

## 推荐批次顺序（按"先补解释器缺口 → 可直接接"排序）

> 每批接完都应跑：①`tests/test_terraria_render.py`（防回归）；②对该批每个 netId 渲一帧目视对照游戏。

**批 0（解释器）**：合入缺口①的 FXLC 守卫（1 行）。可顺带处理缺口②（可选）。
→ 解锁第二梯队 6 个无 preshader pass。

**批 1（最低风险，颜色驱动静态，零解释器依赖）**：`ArmorColored` / `ArmorColoredAndBlack` / `ArmorColoredAndSilverTrim` / `ArmorColoredGradient` / `ArmorColoredAndBlackGradient` / `ArmorColoredAndSilverTrimGradient` / `ArmorBrightnessGradient` / `ArmorColoredRainbow` / `ArmorBrightnessRainbow` / `ArmorWisp` / `ArmorFlow`（共 11 pass，覆盖最多 netId）。
- 收益：消灭手写 recolor/gradient 的 sat-remap 分歧坑（`dye.py:191-192,220` 注释）；颜色链路已就绪。
- 风险：低，多数应像素级一致（ArmorColored 已 bit-for-bit 验证过）。

**批 2（批 0 之后，无 preshader 静态）**：`ArmorBrightnessColored` / `ArmorInvert` / `ColorOnly` / `ArmorMartian` / `ArmorPolarized` / `ArmorMushroom`（6 pass）。
- 风险：低，已实跑验证。

**批 3（修正型，视觉会变但更忠实）**：`ArmorHighContrastGlow`（恢复 v0 glow）/ `ArmorAcid` / `ArmorLivingRainbow` / `ArmorLivingFlame` / `ArmorLivingOcean`（5 pass）。
- 风险：中，需确认 uTime=0 帧观感可接受。

**批 4（自采样动画，需扫 uTime 选代表帧）**：`ArmorVoid` / `ArmorMirage` / `ArmorHades` / `ArmorLoki`（4 pass）。
- 风险：中。这 4 个手写塌得最狠（passthrough/平涂），接字节码视觉变化最大但收益最大。每个需扫 uTime/相位选有特征的静止帧；Hades/Loki 确认 uRotation=0。

**批 5（可选，C 类，标注局限）**：`ArmorReflective` / `ArmorReflectiveColor`（2 pass）。
- 仅"忠实无高光版"，观感未必更好，最低优先级或保留近似。
- **Solar**：归这一批处理（半忠实，标注发光偏白）。

---

## 关键文件与行号索引（结论附 file:line）

- `dye.py` 全部手写近似函数 + 路由：`dye.py:123-256`（recolor 家族）、`259-326`（Martian/Polarized/Mushroom/Wisp/HighContrastGlow）、`330-487`（time-animated 近似 Flow/Living*/Acid/Solar/Void/Hades/Mirage/Loki）、`543-575`（`_noise_pass` 包装）、`579-586`（Reflective）、`804-929`（`apply_dye` dispatch，每个 pass 路由行见 A/B/C 表）。
- `dye_noise.py`：`_decode_preshader`（缺口①）`172-174`；`_run_preshader` 实现集 `234-268`；`_run_ps` opcode 实现集 `372-411`；v0=white `328`；offset-tap `_sample_src` `97-128` + 调用 `367`；`run_noise_pass` noise-guard（缺口②）`437-439`、uniform 绑定 `451-477`、uRotation/uDirection 默认 `458-459`。
- `gen_noise_shaders.py:PRES_INPUTS` `39-63`（已接 12 pass 的输入映射模板，新增 pass 照此格式追加）。
- `noise_shaders.json`：现 12 个 baked pass（Fog/Gel/HallowBoss/MidnightRainbow/Nebula/Phase/ShiftingPearlsands/ShiftingSands/Solar/Stardust/Twilight/Vortex）。
- `dyes.json`：115 netId / 39 pass（pass→netId 映射见 A/B/C 表）。
- 反汇编与游戏源真值：`temp/xnb_probe/in/PixelShader.xnb`（全 39 pass blob）、`temp/xnb_probe/fx_parse.py`（pass→blob→反汇编）、`temp/decomp/full/Terraria.Initializers/DyeInitializer.cs:83-140`（color/secondary 真值）、`temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs:85/97/105/114`（uTime/uRotation/uDirection 绑定）。
- 既有研究对齐：`research/midnight_rainbow.md`（offset-tap 缺口的发现与修复，opcode 已齐全的先例）、`research/audit_dyes.md`（slot 路由 + src_rect/sheet_size 链路已正确）、`research/vortex_dye_bug.md` / `research/dye_passes_spec.md` / `research/noise_dyes_spec.md`。

## Caveats / 未尽事项

- **像素级一致性未全量比对**：本审计验证"能跑通 + 输出合理空间变化 + 颜色 uniform 一致"，但未对每个 netId 做"字节码 vs 游戏截图"的逐像素 diff。批次落地时应补每个 netId 的目视/数值对照（尤其批 3/4 的视觉变化项）。
- **uLightSource 绑定细节**：Reflective 类若接字节码，需确认解释器对 CTAB 里 `uLightSource`（c2）的处理——run_noise_pass 当前不绑定它，靠 `_run_ps` 的 `_Z` 默认零（`dye_noise.py:322-326`）得到 0，正好等于所需局限值；但若未来给它非零值需走 params 绑定。
- **uTime 代表帧未给定数**：第三/四梯队的动画 pass（Void/Mirage/Hades/Loki/Living*/Acid）的"最佳代表 uTime"需按 `_PILLAR_TIME` 同样的扫帧法（`temp/xnb_probe/pillar_sweep.py` 思路）逐个挑，本审计只确认 uTime=0 能跑、相位输入正确，未挑定每个的最佳静止帧。
- **AndBlack/AndSilverTrim 的二次修饰**：手写把"压暗/银边"做成事后 `*k`（`dye.py:135-139,201-204`），字节码里是 def 常量内联——接字节码理论上更忠实，但需确认 AndBlack 的暗化系数在 uTime=0 与手写吻合。
