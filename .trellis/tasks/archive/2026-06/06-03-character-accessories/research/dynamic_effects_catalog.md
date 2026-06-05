# Research: 动态染料 + 动态发光装备/饰品 —— 逐帧对比渲染编目

- **Query**: 为"动态染料 + 动态发光装备/饰品"的逐帧对比渲染做精确编目，让渲染脚本能照表把每个效果的完整动画周期逐帧渲染出来、并标注当前实现取的帧。
- **Scope**: internal（只读 repo 实现 + decomp 逆向；只写 research/）
- **Date**: 2026-06-04

---

## 0. 总览结论（先看这个）

> 数量已用 `dyes.json`(115 netId / 39 pass) 脚本核对，精确无误。

### 数量
- **动态染料（表 A）= 22 个 pass / 共 34 个 dye netId**（= 全部非纯静态 recolor）。其中：
  - **离线可渲染成"真动画"= 20 个 pass / 28 netId**：11 个时间动画 pass（`uTime` 驱动）+ 9 个 noise pass（`uTime` 驱动，跑真实 bytecode + noise.png）。**但注意**：这 20 个里有 **9 netId（MidnightRainbow/Solar/Void/Hades×4/Mirage/Loki）当前实现是 APPROX 静态塌缩、不含 uTime**，要真动画须先升级（见 §A.1 小结 + Caveat 1）；其余 **19 个 netId 现在就能扫 uTime 出周期帧**（time 7 + noise 12）。
  - **离线只能出"单帧"（不可动画）= 2 个 pass / 6 netId**：`ArmorReflective`(1: 3190) / `ArmorReflectiveColor`(5)。它们靠 `uLightSource`（活体光照梯度），离线 = 0，无任何内部量可 sweep。
- **排除在表 A 外的纯静态 recolor = 17 个 pass / 81 netId**（无时变量，单帧，无需扫描）。
- **动态发光装备/饰品（表 B）= 8 个动画发光件**（按 equip slot 计）：4 个 mouseTextColor 脉动（body 237 / head 268 / head 282 / legs 222）+ ChickenBones(head 284) + Luna(head 292) + TV 头(head 271) + head 282 的 ArmorHead 自身 9 帧动画。另有 **3 类亚像素抖动 / 4-tap**（227 Nebula 抖动 body+arm+head+legs、body 205 / head 211 ApprenticeAlt 4-tap）属"伪随机相位"，离线取代表静帧、**不可周期动画**。

### "可渲染动画" vs "离线单帧" 一句话判据
| 类别 | 内部时变量 | 离线可渲染动画? |
|---|---|---|
| 染料 time-animated（11 pass） | `uTime = GlobalTimeWrappedHourly` | **看实现**：7 netId 有真公式+uTime → 是；9 netId 当前 APPROX 无 uTime → 须先升级 |
| 染料 noise-sample（9 pass / 12 netId） | `uTime`（scroll/相位）+ 真 noise 纹理 | **是**（全部），`run_noise_pass(..., u_time=)` 已支持；噪声纹理静态、相位随 uTime 动 |
| 染料 Reflective（2 pass / 6 netId） | `uLightSource`（活体光照法线，离线=0） | **否**，单帧；emboss 静态、无相位可扫 |
| 染料纯静态 recolor（17 pass / 81 netId，**已排除出表 A**） | 无 | 否，单帧（无需扫描） |
| 发光 mouseTextColor（4 件） | `Main.mouseTextColor ∈[190,255]` 三角 | **是**，扫亮度 `num=(mc/255)²` |
| 发光 ChickenBones / Luna（2 件） | `miscCounter%100` → WrappedLerp 三角 → Remap | **是**，扫相位 0..1 |
| 发光 TV 头（head 271） | col=GetTVScreen(离线固定3) + row=`miscCounter%20/5` | **部分**，仅 row（4 帧）可扫；col 离线恒 3 |
| 发光 head 282 ArmorHead | `miscCounter%36/4`（9 帧；发光层本身固定 frame0） | **是**（彩甲帧动画，发光层不动） |
| 发光抖动 / 4-tap（227/205/211） | `Main.rand` 亚像素偏移 | **否（伪随机无相位）**，单帧代表 fan |

### 关键控制点（哪个常量/函数控制"当前代表值"，加 phase 参数从这改）
- **染料 uTime**：`dye.py:46 UTIME = 0.0`（全 time-animated pass 默认值）；`dye_noise.py:46 _UTIME = 0.0`。noise pillar pass 另有 `dye.py:494 _PILLAR_TIME`（Solar/Nebula/Vortex/Stardust/HallowBoss 各自烤的"亮帧"uTime）。
- **noise pass 端到端 uTime 形参**：`dye.py:_noise_pass(..., u_time=)` → `dye_noise.run_noise_pass(..., u_time=)` → 写进 `params["uTime"]`（`dye_noise.py:410`）。**已可逐帧**，只需把 `apply_dye` 暴露一个 `u_time` 透传即可 sweep 任意染料帧。
- **发光 mouseText 代表值**：`glowmask.json` 已把动画色烤成中点 222→`num=0.758`→`193`（body 237 / head 268 / head 282 / legs 222 全是 `[193,193,193,0]`）。要逐帧 = 把这些条目改成函数 `base_rgb * (mc/255)²`，mc 扫 [190,255]。
- **发光 ChickenBones 代表值**：`compositor.py:275 _CHICKENBONES_GLOW_COLOR=(229,229,229,0)`（=mid 0.9）；`glowmask.json head.284 = [229,229,229,0]`。
- **发光 Luna 代表值**：`glowmask.json head.292 = [236,236,236,92]`（=mid 0.925；A=100·0.925）。
- **TV 头代表帧**：`compositor.py:260 _TV_IDLE_COL=3`、`:264 _TV_IDLE_ROW=0`。
- **抖动 / 4-tap 代表 fan**：`compositor.py:228 _JITTER_OFFSETS`、`:239 _TAP4_OFFSETS`、`:252 _HEAD211_TAP4_OFFSETS`。

---

## 表 A — 动态染料

> 排除纯静态 recolor（**不在本表**，无需扫描，单帧；共 17 pass / 81 netId）：`ArmorColored`(25 netId) / `ArmorColoredAndBlack`(13) / `ArmorColoredAndSilverTrim`(12) / `ArmorColoredGradient`(6) / `ArmorColoredAndBlackGradient`(3) / `ArmorColoredAndSilverTrimGradient`(3) / `ArmorBrightnessColored`(4) / `ArmorBrightnessGradient`(3) / `ArmorBrightnessRainbow`(1: 1067 IntenseRainbowDye) / `ArmorColoredRainbow`(1: 1066 RainbowDye) / `ArmorInvert`(1: 2872 NegativeDye) / `ArmorMartian`(1: 2864) / `ArmorPolarized`(1: 3557 BlackAndWhiteDye) / `ArmorMushroom`(1: 3041) / `ArmorWisp`(4) / `ArmorHighContrastGlow`(1: 2883 ChlorophyteDye) / `ColorOnly`(1: 3978)。
>
> 注：`ArmorColoredRainbow`/`ArmorBrightnessRainbow` 的彩虹是**沿 uv.x 的空间**渐变（`_rainbow_rgb`），**不含 uTime**，故归静态、不在本表。真正随时间转动彩虹的是 `ArmorLivingRainbow`（在表内）。
>
> 列含义见任务说明。"我方渲染处理"四类：**uTime动画取代表**=跑真公式但 uTime 固定 / **噪声真采样**=跑真 bytecode + noise.png（uTime 固定）/ **view-approx静态**=uLightSource=0 塌缩 / **passthrough**=近似为原图。
> **当前代表值**列 = 我方现在固定取的那一帧。**可动画?** = 离线能否扫成完整周期。
> 所有 file:line 指 `nextbot/terraria_render/dye.py` 除非另注。

### A.1 时间动画 pass（11 pass / 16 netId；`uTime` 驱动）

| netId | 物品名(ItemID.cs) | pass | 我方渲染处理 | 游戏内动画(在变什么) | 可动画? | 扫描参数&范围&建议帧数 | 当前代表值 |
|---|---|---|---|---|---|---|---|
| 2869 | LivingFlameDye | ArmorLivingFlame | uTime动画取代表(`_living_flame:364`) | luma+pos 相位 `+uTime` 经 sincos→sgn 折成带，在 uColor(1,.9,0)↔uSec(1,.2,0) 火色间往复流动 | **是** | `uTime`：相位项 `s*.4+L*.15+uTime`，周期 1.0（frc）→ 扫 **uTime∈[0,1) N=24~30 帧** | uTime=0 |
| 2873 | LivingOceanDye | ArmorLivingOcean | uTime动画取代表(`_living_ocean:386`) | 同上结构，固定蓝/青调 base(0,.4,1)↔alt(0,1,1) 波动 | **是** | 同 LivingFlame，**uTime∈[0,1) N≈24** | uTime=0 |
| 2870 | LivingRainbowDye | ArmorLivingRainbow | uTime动画取代表(`_living_rainbow:348`) | `hue=pos*.4 + luma*.15 + uTime*0.8`，整条彩虹随 uTime 转动 | **是** | `uTime`：hue 含 `uTime*0.8`，彩虹 1 周期需 hue 走 ~1.11 → **uTime∈[0,1.39) N≈32**（让彩虹转满一圈） | uTime=0 |
| 3025 | PurpleOozeDye | ArmorFlow | uTime动画取代表(`_flow:330`) | `ph=frc(L*4+uTime)` sincos 折带，uColor(1,.5,1)↔uSec(.6,.1,1) 沿亮度流动 | **是** | `uTime`：`frc(L*4+uTime)` 周期 1.0 → 扫 **uTime∈[0,1) N≈24** | uTime=0 |
| 3040 | AcidDye | ArmorAcid | uTime动画取代表(`_acid:406`) | 极坐标 `ang/2π + luma*.15 + uTime` 旋转带，绕中心 swirl，uColor(.5,1,.3) | **是** | `uTime`：相位周期 1.0 → **uTime∈[0,1) N≈24~36**（swirl 转一圈） | uTime=0 |
| 3028 | BlueAcidDye | ArmorAcid | 同上，uColor=(.5,.7,1.5) | 同 AcidDye | **是** | 同上 | uTime=0 |
| 3560 | RedAcidDye | ArmorAcid | 同上，uColor=(.9,.2,.2) | 同 AcidDye | **是** | 同上 | uTime=0 |
| 3556 | MidnightRainbowDye | ArmorMidnightRainbow | **APPROX**(`_midnight_rainbow:427` → 退化成 `_colored_rainbow` 空间彩虹) | 原版：5-tap self-emboss 的幅度驱动 `_rainbow_rgb`，叠在暗底上，随 uTime 闪动 | **否**（当前实现已塌成静态空间彩虹，**无 uTime**）；要真动画须补 5-tap+uTime | 当前不可扫。若补真版：`uTime` N≈24 | 静态空间彩虹(uTime 无效) |
| 3526 | SolarDye | ArmorSolar | **APPROX**(`_solar:432` 手写火焰 ramp，**非真 bytecode**，无 uTime) | 原版：5-tap emboss + `sincos(uTime)` 旋转的加性火焰辉光（载在 v0，离线塌成灰；故我方改用亮度→火色 ramp 近似） | **否**（当前 APPROX 不含 uTime；注 `_PILLAR_TIME["ArmorSolar"]=5.0` 是给真 bytecode 路径的，但 Solar 未走 noise 路径，故此值当前**未被使用**） | 当前不可扫（APPROX 静态） | 亮度 ramp 静帧 |
| 3530 | VoidDye | ArmorVoid | **APPROX**(`_void:463` → `_brightness_clip(.35,.35,.35)` 暗灰塌缩，无 uTime) | 原版：3 水平 tap（`±uTime` 位移）模糊+压暗的暗色 shimmer | **否**（当前 APPROX 无 uTime） | 当前不可扫 | 暗灰单帧 |
| 3038 | HadesDye | ArmorHades | **APPROX**(`_hades:468` → `_brightness_clip(uColor)`，无 uTime/uRotation) | 原版：旋转 tap 偏移(uRotation) + uTime scroll + 2 条 sincos 火band，混 uColor/uSec | **否**（当前 APPROX 无 uTime） | 当前不可扫 | uColor 染色单帧 |
| 3597 | BurningHadesDye | ArmorHades | 同上，uColor=(1.5,.6,.4) | 同 HadesDye | **否** | — | 单帧 |
| 3598 | GrimDye | ArmorHades | 同上，uColor=(.1,.1,.1)/uSec=(.4,.05,.025) | 同 HadesDye | **否** | — | 单帧 |
| 3600 | ShadowflameHadesDye | ArmorHades | 同上，uColor=(.7,.4,1.5) | 同 HadesDye | **否** | — | 单帧 |
| 3534 | MirageDye | ArmorMirage | **APPROX**(`_mirage:476` → passthrough 原图，无 uTime) | 原版：3 self-tap + pos + uTime 波纹位移 recolor | **否**（当前 passthrough） | 当前不可扫 | 原图 |
| 3599 | LokisDye | ArmorLoki | **APPROX**(`_loki:481` → `_brightness_clip(.1,.1,.1)` 暗塌缩) | 原版：3 self-tap + uRotation/uTime sincos 的移动暗迷彩 | **否** | 当前不可扫 | 暗单帧 |

> **时间动画小结**：真正"逐帧公式正确、离线现就能扫成动画"的是 **LivingFlame / LivingOcean / LivingRainbow / Flow / Acid×3 = 7 netId**（`dye.py` 里有真 sincos/极坐标公式 + `uTime` 形参，扫 uTime 即得周期帧）。其余 **MidnightRainbow / Solar / Void / Hades×4 / Mirage / Loki = 9 netId** 当前是 **APPROX 静态塌缩（无 uTime）**：要做"对比逐帧动画"必须先把这些 pass 升级成真 self-sampling bytecode（offsets/uTime 见 `dye_passes_spec.md:493-526`），否则它们的"动画"无从渲染。

### A.2 noise 采样 pass（9 pass / 12 netId；`uTime` 驱动 + 真 noise.png；离线**全部可动画**）

> 全部走 `dye_noise.run_noise_pass`，端到端有 `u_time` 形参（`dye_noise.py:379/410`）。噪声纹理 `assets/noise.png`(256²) / `Extra_156.png`(512²) 静态，**动画来自 uTime 把 noise uv / 相位 scroll**。pillar 类（Nebula/Vortex/Stardust/HallowBoss）当前固定取 `_PILLAR_TIME` 的"亮帧"，可改扫。

| netId | 物品名 | pass | 我方渲染处理 | 游戏内动画(在变什么) | 可动画? | 扫描参数&范围&建议帧数 | 当前代表值 |
|---|---|---|---|---|---|---|---|
| 3527 | NebulaDye | ArmorNebula | 噪声真采样+emissive(`_nebula:602`) | noise 云随 uv-scroll(uTime) 飘动 + `_rainbow(noise)*uSec*5` 粉紫云 | **是** | `uTime`：云 uv 含 uTime scroll → 扫 **uTime∈[0,~6) N≈24~48**（覆盖一个 scroll 循环） | `_PILLAR_TIME["ArmorNebula"]=3.0`(`dye.py:497`) |
| 3528 | VortexDye | ArmorVortex | 噪声真采样+emissive(`_vortex:620`) | 极坐标 swirl 的 noise 流线随 uTime 旋转，青绿能量 | **是** | `uTime`：swirl 相位 → **uTime∈[0,~6) N≈36** | `_PILLAR_TIME["ArmorVortex"]=0.5`(`dye.py:498`) |
| 3529 | StardustDye | ArmorStardust | 噪声真采样+emissive(`_stardust:638`) | 星场：`noise-threshold * uSec * 8` 亮白星点随 uTime 闪烁/移动 | **是** | `uTime`：星点相位（preshader `CONST[8]` 经 uTime；见 `dye_noise.py:184` 注） → **uTime∈[0,~4) N≈30** | `_PILLAR_TIME["ArmorStardust"]=1.0`(`dye.py:499`) |
| 4778 | HallowBossDye | ArmorHallowBoss | 噪声真采样(Extra_156)+emissive(`_hallow_boss:702`) | Extra_156 调色板查找，`out=src*.2+palette*.8` 虹彩；palette uv 随 uTime 微移 | **是**（uTime 影响极小） | `uTime`：palette uv 几乎不随时间动 → **N=1~8 足够**（变化微弱） | `_PILLAR_TIME["ArmorHallowBoss"]=0.0`(`dye.py:500`) |
| 3042 | PhaseDye | ArmorPhase | 噪声真采样(`_phase:591`) | noise 窗口随相位淡入淡出 uColor(.4,.2,1.5) 染色源 | **是** | `uTime`：相位 scroll → **uTime∈[0,1) N≈24** | uTime=0(默认) |
| 3024 | DevDye | ArmorGel | 噪声真采样(`_gel:579`) | 4 self-tap 模糊 + 1 noise tap 的果冻高光，uColor/uSec 混；offsets 含 `sincos(uTime)` 旋转 | **是** | `uTime`：tap 旋转相位 → **uTime∈[0,~6.28) N≈24** | uTime=0(默认，注 uRotation=0) |
| 3561 | GelDye | ArmorGel | 同上，uColor=(.4,.7,1.4) | 同 DevDye | **是** | 同上 | uTime=0 |
| 3562 | PinkGelDye | ArmorGel | 同上，uColor=(1.4,.75,1) | 同 DevDye | **是** | 同上 | uTime=0 |
| 4663 | BloodbathDye | ArmorGel | 同上，uColor=(2.6,.6,.6) | 同 DevDye | **是** | 同上 | uTime=0 |
| 3533 | ShiftingSandsDye | ArmorShiftingSands | 噪声真采样(`_shifting_sands:660`) | 竖向 scroll：`tri=sgn(py/56*10+uTime)` 让 noise grain 随 uTime **垂直滚动**，混 uColor/uSec | **是** | `uTime`：竖滚相位周期 1.0 → 扫 **uTime∈[0,1) N≈24** | uTime=0(默认) |
| 3535 | ShiftingPearlSandsDye | ArmorShiftingPearlsands | 噪声真采样(`_shifting_pearlsands:672`) | 同上 + 第 2 tap 珠光 sparkle | **是** | 同 Sands，**uTime∈[0,1) N≈24** | uTime=0(默认) |
| 4662 | FogboundDye | ArmorFog | 噪声真采样(`_fog:684`) | noise 软雾覆盖随竖滚 uTime 飘移，低对比灰 | **是** | `uTime`：竖滚相位 → **uTime∈[0,1) N≈24** | uTime=0(默认) |

> **noise 小结**：12 netId（9 个 pass）**全部离线可渲染成动画** —— `run_noise_pass` 已带 `u_time`，噪声纹理静态、相位/scroll 随 uTime 动。pillar 4 个（Nebula/Vortex/Stardust/HallowBoss）当前固定在 `_PILLAR_TIME` 的"最亮帧"，做逐帧对比时把这个常量换成扫描变量即可。**当前代表帧 = pillar 各自 `_PILLAR_TIME` 值 / 非 pillar = uTime 0。**

### A.3 view-dependent pass（2 pass / 6 netId；`uLightSource` 驱动；离线**只能单帧**）

| netId | 物品名 | pass | 我方渲染处理 | 游戏内动画(在变什么) | 可动画? | 扫描 | 当前代表值 |
|---|---|---|---|---|---|---|---|
| 3190 | ReflectiveDye | ArmorReflective | view-approx 静态(`_reflective:569` → passthrough) | 原版：5-tap emboss 法线被 `uLightSource`(活体光照梯度)照亮 → 金属高光随**镜头/光照**变；非时间动画 | **否**（`uLightSource=0` 离线无光向，无相位/时间量可扫，单帧） | **单帧, 无需扫描** | 原图 |
| 3026 | ReflectiveSilverDye | ArmorReflectiveColor | view-approx 静态(`_reflective_color:574` → `_brightness_clip(uColor)`) | 同上 + uColor=(1,1,1) 染色 | **否** | **单帧, 无需扫描** | uColor 染色单帧 |
| 3027 | ReflectiveGoldDye | ArmorReflectiveColor | 同上，uColor=(1.5,1.2,.5) | 同上 | **否** | 单帧 | 单帧 |
| 3553 | ReflectiveCopperDye | ArmorReflectiveColor | 同上，uColor=(1.35,.7,.4) | 同上 | **否** | 单帧 | 单帧 |
| 3554 | ReflectiveObsidianDye | ArmorReflectiveColor | 同上，uColor=(.25,0,.7) | 同上 | **否** | 单帧 | 单帧 |
| 3555 | ReflectiveMetalDye | ArmorReflectiveColor | 同上，uColor=(.4,.4,.4) | 同上 | **否** | 单帧 | 单帧 |

> 备注（来自 `noise_dyes_spec.md:273-291`）：若想要更生动的静帧，可硬编一个固定光向 `uLightSource=normalize((-0.7,-0.7))` 跑 5-tap emboss，得到斜向 sheen —— 但那是**风格化**而非游戏离线行为，且仍是**单帧**（不是周期动画）。

---

## 表 B — 动态发光装备/饰品

> 数据源 `glowmask.json` + `compositor.py`（绘制）+ `PlayerDrawSet.cs:517-802`(发光色) / `PlayerDrawLayers.cs`(绘制/动画) / `Main.cs:18066-18073`(mouseTextColor) 逆向。
> item netId 由 `equip_slots.json` 反查（slot→netId→`ItemID.cs` 名）。
> **当前代表帧/相位值**列 = 我方现在固定取的那个发光色/帧。
> "发光类型"：脉动色 = 整层乘一个随时间变的标量；网格 = 多帧贴图选帧；4-tap/抖动 = 多次偏移叠加。

### B.1 mouseTextColor 脉动（4 件；离线**可动画**，扫亮度标量）

> 公式 `num = (mouseTextColor/255)²; glowColor = baseRGB * num`（如 `PlayerDrawSet.cs:553-555`）。
> `mouseTextColor ∈ [190,255]`，每帧 ±1（`Main.cs:18066-18073`，三角往复，**周期 130 帧** = 65↑+65↓）。
> 当前 `glowmask.json` 全烤成中点 mc=222 → num=(222/255)²≈0.758 → `[193,193,193,0]`。

| item netId(part/slot) | 物品名 | 槽位 slot | 发光类型 | 动画来源&范围 | 可动画? | 扫描参数&范围&建议帧数 | 当前代表帧/相位值 |
|---|---|---|---|---|---|---|---|
| 5052(body) | TimelessTravelerRobe | body 237 | mouseText 脉动(叠加,A=0) | `mc∈[190,255]` 三角；`num=(mc/255)²∈[0.555,1.0]` | **是** | 扫 `mc` 整数 **190→255→191**(三角) 或直接扫 num∈[0.555,1.0] **N≈16~24** | mc=222→`[193,193,193,0]`(glowmask.json body.237) |
| 5051(head) | TimelessTravelerHood | head 268(mask 302) | mouseText 脉动(叠加,A=0) | 同上 | **是** | 同上 | `[193,193,193,0]`(head.268) |
| 5457(head) | DeadCellsBeheadedHead | head 282(mask 357) | mouseText 脉动(叠加,A=0) **+** ArmorHead 自身 9 帧动画(见 B.4) | 同上（发光层）；彩甲帧另算 | **是** | 发光：同上 `mc` N≈16；彩甲帧另见 B.4 | 发光 `[193,193,193,0]`(head.282)；彩甲帧 0 |
| 5053(legs) | TimelessTravelerBottom | legs 222(mask 303) | mouseText 脉动(叠加,A=0) | 同上 | **是** | 同上 | `[193,193,193,0]`(legs.222) |

> **控制点**：`glowmask.json` body.237 / head.268 / head.282 / legs.222 的 `[193,193,193,0]`。逐帧 = 把这 4 条换成 `[255*num,255*num,255*num,0]`，num 由 mc 扫出。备选最亮 mc=255→num=1.0→`[255,255,255,0]`，最暗 mc=190→num≈0.555→`[141,141,141,0]`。

### B.2 ChickenBones / Luna 脉动（2 件；离线**可动画**，扫 miscCounter 相位）

> `GetChickenBonesGlowColor`(`PlayerDrawLayers.cs:164-181`) / `GetLunaGlowColor`(`:183-197`)：
> `phase = WrappedLerp(0,1,(miscCounter%100)/100)`（**三角波，周期 100 ticks** = 50↑+50↓）；`num = Remap(phase, 0,1, lo,hi); color *= num`。

| item netId(part/slot) | 物品名 | 槽位 slot | 发光类型 | 动画来源&范围 | 可动画? | 扫描参数&范围&建议帧数 | 当前代表帧/相位值 |
|---|---|---|---|---|---|---|---|
| 5583(head) | ChickenBonesHead | head 284(mask 365) | ChickenBones 脉动(叠加,A=0) | `phase=tri(miscCounter%100/100)`；`num=Remap→[0.8,1.0]`；color=(255,255,255,0)*num | **是** | 扫 `miscCounter%100` **0→99**（或 phase 0→1→0），num∈[0.8,1.0] **N≈20~25** | mid phase→num=0.9→`[229,229,229,0]`(head.284；`compositor.py:275 _CHICKENBONES_GLOW_COLOR`) |
| 6137(head) | LunasHead | head 292(mask 378) | Luna 脉动(部分遮挡,A>0) | 同结构；`num=Remap→[0.85,1.0]`；color=(255,255,255,100)*num（**4 通道含 A 都乘**） | **是** | 扫 `miscCounter%100` 0→99，num∈[0.85,1.0] **N≈20**；RGB=255*num，A=100*num | mid→num=0.925→`[236,236,236,92]`(head.292) |

> **控制点**：`compositor.py:275 _CHICKENBONES_GLOW_COLOR=(229,229,229,0)`、`glowmask.json head.284 / head.292`。另：ChickenBones 同族还驱动 **coat-238 的 GlowMask_363**（`compositor.py:272 _COAT_FRONT_GLOW`，DrawLongCoat `PlayerDrawLayers.cs:1826-1834`）与 wings/back —— 同 `num`，逐帧时一起扫。

### B.3 抖动 / 4-tap（伪随机相位；离线**单帧代表 fan**，不可周期动画）

> 这些是 `Main.rand` 亚像素偏移，**无确定相位**，离线取代表整数 fan（与 dye.py UTIME=0 同精神）。"可动画?"=否（随机不可复现成周期）。

| item netId(part/slot) | 物品名 | 槽位 slot | 发光类型 | 动画来源&范围 | 可动画? | 扫描 | 当前代表帧/相位值 |
|---|---|---|---|---|---|---|---|
| 4756(body) | GroxTheGreatArmor | body 227 + arm 227 | 复合发光抖动 2 遍(A=60 部分遮挡) | torso/arm 复合发光各画 2 遍，±1.25px `Main.rand`(`PlayerDrawLayers.cs:63-76/90-101`) | **否**(随机) | **单帧, 2-tap fan** | `[230,230,230,60]` ×`_JITTER_OFFSETS[:2]`(`compositor.py:228`；jitter 来自 `glowmask.json body_jitter.227=2`) |
| 4755(head) | GroxTheGreatHelm | head 240(mask 273) | 独立 strip 抖动 2 遍(A=60) | 同 227，head 自身 strip 抖 2 遍 | **否** | 单帧 2-tap | `[230,230,230,60]` jitter=2(head.240) |
| 4757(legs) | GroxTheGreatGreaves | legs 210(mask 274) | 独立 strip 抖动 2 遍(A=60) | legs 抖 2 遍(`:1559`) | **否** | 单帧 2-tap | `[230,230,230,60]` jitter=2(legs.210) |
| 3875(body) | ApprenticeAltShirt | body 205(FrontArm extra) | 复合 FrontArm 4-tap 加性(A=0) | 4 tap：X=`RandomInt(-10,11)*0.2`(±2px)、Y=`RandomInt(-10,1)*0.15`([-1.5,0])(`PlayerDrawLayers.cs:118-135`) | **否**(随机) | **单帧, 4-tap fan** | `(100,100,100,0)` ×`_TAP4_OFFSETS`(`compositor.py:239`/`_BODY205_4TAP_COLOR:235`) |
| 3874(head) | ApprenticeAltHead | head 211(GlowMask_241) | 独立 4-tap 加性(A=0) | 4 tap：X=`RandomInt(-10,11)*0.2`(±2px)、Y=`RandomInt(-14,1)*0.15`([-2.1,0])(`PlayerDrawLayers.cs:2403-2415`) | **否** | **单帧, 4-tap fan** | `(100,100,100,0)` ×`_HEAD211_TAP4_OFFSETS`(`compositor.py:252`/`_HEAD211_4TAP_COLOR:248`) |

### B.4 网格 / 帧动画（贴图选帧；**部分可动画**）

| item netId(part/slot) | 物品名 | 槽位 slot | 发光类型 | 动画来源&范围 | 可动画? | 扫描参数&范围&建议帧数 | 当前代表帧/相位值 |
|---|---|---|---|---|---|---|---|
| 5061(head) | TVHeadMask | head 271(mask 309) | TV 屏 6×4 网格选帧(A=255 不透明) | col=`GetTVScreen`(离线状态恒 **3**,`PlayerDrawLayers.cs:2514-2536`)；row=`miscCounter%20/5`(**4 帧,周期 20 ticks**,`:2375`) | **部分**(仅 row 4 帧；col 离线恒 3) | 扫 `row` **0→3**（col 固定 3）**N=4**；要全网格需模拟 GetTVScreen 各状态(危险/低血/生态/湿/town,见 :2514) | col=3,row=0(`compositor.py:260 _TV_IDLE_COL=3`,`:264 _TV_IDLE_ROW=0`) |
| 5457(head) | DeadCellsBeheadedHead | head 282(ArmorHead) | 彩甲 9 帧循环(`miscCounter%36/4`,**周期 36 ticks**,`PlayerDrawLayers.cs:2181`) | 彩甲帧 `bodyFrame2.Y=Height*(miscCounter%36/4)`；**发光层固定 frame0**(`bodyFrame3.Y=0`,:2181) | **是**(彩甲帧；发光层不动) | 扫彩甲帧 **0→8** **N=9**；发光层不变 | 彩甲 frame0(发光层恒 frame0；该件发光色见 B.1) |

> **控制点**：TV → `compositor.py:260/264 _TV_IDLE_COL/_TV_IDLE_ROW`（绘制在 `draw_tv_head_glow:756`）。head 282 彩甲帧 → 当前 compositor 用 `"col"` 帧（idle frame 0）画 head armor（`draw_armor`），9 帧动画需把彩甲 cell 的 frame index 参数化（发光 strip 保持 frame0 不动）。

### B.5 静态发光件（**不在动画范畴，单帧，仅供完整性**）

其余发光件均为**固定色**（不随时间），单帧即可、无需扫描：
- body：175 VortexBreastplate / 208 ArkhalisShirt(arkhalis 色) / 238 FloretProtectorChestplate(A=255) / 260 PalworldPalMetalArmorBody(A=255) / 239 CapricornChestplate / 190 StardustBreastplate / 176 NebulaBreastplate / 194 LokisShirt / 179 MartianUniformTorso。
- head：169 VortexHelmet / 210 SquireAltHead / 214 ArkhalisHat(arkhalis) / 267 RoninHat / 269 FloretProtectorHelmet(+extra mask 308/Extra 214) / 270 CapricornMask / 170 NebulaHelmet / 189 StardustHelmet / 175 MartianUniformHelmet / 193 LokisHelm / 109 JimsHelmet / 178 HiTekSunglasses / 285(无 shipped item,mask 367) / 291 KazzymodusHood(A=255) / 216(无 shipped item,mask 256)。
- legs：111 NebulaLeggings / 157(无 shipped item,mask 249,arkhalis) / 158 ArkhalisPants(arkhalis) / 225 CapricornLegs / 226 CapricornTail / 110 VortexLeggings / 134 LokisPants / 130 StardustLeggings。
- arkhalis 色（208/214/157/158 + arm 208）= `(underShirtColor.rgb, A=180)`，按外观衬衫色 runtime 解析（`compositor.py:220 _GLOW_ARKHALIS_ALPHA=180`）—— 随外观变但**非时间动画**，单帧。

---

## 扫描配方（给渲染脚本用）

> 对每个效果："要 sweep 哪个内部量 / 范围 / 建议步数"，以及"哪个常量/函数控制当前代表值"。
> view-approx/passthrough/纯静态标 **单帧, 无需扫描**。

### C.1 染料扫描配方

| pass(es) | sweep 量 | 范围 | 建议步数 N | 控制当前代表值的点 | 备注 |
|---|---|---|---|---|---|
| LivingFlame / LivingOcean | `uTime` | [0,1) | 24~30 | `dye.py:46 UTIME=0.0`（`_living_flame`/`_living_ocean` 的 `uTime` 形参默认） | frc 相位周期 1.0 |
| LivingRainbow | `uTime` | [0,1.39) | 32 | `UTIME=0.0`(`_living_rainbow` 形参) | hue 含 `uTime*0.8`，转满一圈需 uTime≈1.39 |
| Flow | `uTime` | [0,1) | 24 | `UTIME`(`_flow` 形参) | `frc(L*4+uTime)` |
| Acid×3(3040/3028/3560) | `uTime` | [0,1) | 24~36 | `UTIME`(`_acid` 形参) | 极坐标 swirl |
| **Nebula** | `uTime` | [0,~6) | 24~48 | `dye.py:497 _PILLAR_TIME["ArmorNebula"]=3.0` | 真 bytecode；改这个常量为扫描值 |
| **Vortex** | `uTime` | [0,~6) | 36 | `dye.py:498 _PILLAR_TIME["ArmorVortex"]=0.5` | 真 bytecode |
| **Stardust** | `uTime` | [0,~4) | 30 | `dye.py:499 _PILLAR_TIME["ArmorStardust"]=1.0` | 真 bytecode；uTime 经 preshader CONST[8](`dye_noise.py:184`) |
| **HallowBoss** | `uTime` | [0,~8) | 1~8 | `dye.py:500 _PILLAR_TIME["ArmorHallowBoss"]=0.0` | palette uv 几乎不随 uTime；变化微弱 |
| Phase / Gel×4 | `uTime` | [0,1)~[0,2π) | 24 | `UTIME=0.0`(默认；非 pillar) | Gel 含 `sincos(uTime)` tap 旋转 |
| ShiftingSands / Pearlsands / Fog | `uTime` | [0,1) | 24 | `UTIME=0.0`(默认) | 竖向 scroll `py/56*10+uTime` |
| **统一入口**(全 noise pass) | `u_time` | — | — | `dye.py:_noise_pass(u_time=)` → `dye_noise.run_noise_pass(u_time=)` → `params["uTime"]`(`dye_noise.py:410`) | 链路已通，加 `apply_dye` 透传即可逐帧任意 noise 染料 |
| MidnightRainbow / Solar / Void / Hades×4 / Mirage / Loki | （当前 APPROX 无 uTime） | — | — | `_midnight_rainbow:427`/`_solar:432`/`_void:463`/`_hades:468`/`_mirage:476`/`_loki:481` | **需先升级成真 self-sampling bytecode** 才能扫；offsets/uTime 见 `dye_passes_spec.md:493-526` |
| Reflective / ReflectiveColor×5 | — | — | — | `_reflective:569`/`_reflective_color:574` | **单帧, 无需扫描**（uLightSource=0 无相位） |
| 全 17 静态 recolor | — | — | — | — | **单帧, 无需扫描** |

### C.2 发光扫描配方

| 效果 | sweep 量 | 范围 | 建议步数 N | 控制当前代表值的点 |
|---|---|---|---|---|
| mouseText 脉动(body237/head268/head282/legs222) | `mouseTextColor`(或 num=(mc/255)²) | mc∈[190,255] 三角（num∈[0.555,1.0]），周期 130 帧 | 16~24 | `glowmask.json` body.237/head.268/head.282/legs.222 = `[193,193,193,0]`（=mc 222） |
| ChickenBones(head284 + coat363 + wings) | `miscCounter%100`（或 phase 0→1→0） | num∈[0.8,1.0]，周期 100 ticks | 20~25 | `compositor.py:275 _CHICKENBONES_GLOW_COLOR=(229,229,229,0)`；`glowmask.json head.284` |
| Luna(head292) | `miscCounter%100` | num∈[0.85,1.0]，RGB=255num/A=100num，周期 100 ticks | 20 | `glowmask.json head.292 = [236,236,236,92]`(=mid 0.925) |
| TV 头(head271) | `row`(col 离线固定 3) | row 0→3（周期 20 ticks） | 4 | `compositor.py:260 _TV_IDLE_COL=3`、`:264 _TV_IDLE_ROW=0`（绘制 `draw_tv_head_glow:756`） |
| head282 彩甲帧 | 彩甲 frame index | 0→8（周期 36 ticks；发光层恒 frame0） | 9 | compositor 用 `"col"`(frame0) 画 head armor；需参数化彩甲 cell frame |
| 抖动(227/240/210) | — | — | — | **单帧, 2-tap fan**：`_JITTER_OFFSETS[:2]`(`compositor.py:228`)，jitter 来自 `glowmask.json *_jitter` |
| 4-tap(body205/head211) | — | — | — | **单帧, 4-tap fan**：`_TAP4_OFFSETS`(`compositor.py:239`)、`_HEAD211_TAP4_OFFSETS`(`:252`) |
| 全静态发光件(B.5) | — | — | — | **单帧, 无需扫描** |

---

## D. 控制点清单（一处改一处生效的"代表帧"开关）

### D.1 dye.py / dye_noise.py
- `dye.py:46` `UTIME = 0.0` —— 全 time-animated pass 默认 uTime（`_flow`/`_living_*`/`_acid` 等的形参默认值）。
- `dye.py:494-500` `_PILLAR_TIME` —— Solar/Nebula/Vortex/Stardust/HallowBoss 各自烤的代表 uTime（**Solar 当前未走 noise 路径，此值未被使用**；其余 4 个 noise pillar 生效）。
- `dye.py:506-511` `_PILLAR_GAIN` —— emissive 增益（影响亮度观感，非帧）。
- `dye.py:533 _noise_pass(..., u_time=...)` / `dye.py:553 dye_noise.run_noise_pass(..., u_time=...)` —— noise pass 的 uTime 注入口。
- `dye_noise.py:46` `_UTIME = 0.0`、`dye_noise.py:379 run_noise_pass(..., u_time=_UTIME)`、`dye_noise.py:410 params["uTime"]=[u_time,...]` —— uTime 真正写进 shader 常量处。
- `dye.py:734 apply_dye(...)` —— **当前不透传 uTime**；逐帧渲染需给它加一个可选 `u_time` 参数转发给各 pass（time-animated + noise 都能接）。

### D.2 compositor.py（发光代表帧/相位/抖动 fan）
- `:228 _JITTER_OFFSETS = ((0,0),(1,1),(-1,1),(1,-1))` —— 抖动/strip 代表整数 fan。
- `:239 _TAP4_OFFSETS` / `:252 _HEAD211_TAP4_OFFSETS` —— body205 / head211 4-tap fan。
- `:235 _BODY205_4TAP_COLOR=(100,100,100,0)` / `:248 _HEAD211_4TAP_COLOR=(100,100,100,0)`。
- `:260 _TV_IDLE_COL=3` / `:264 _TV_IDLE_ROW=0` / `:268 _TV_IDLE_VEC5=(0,0)` —— TV 头代表帧。
- `:272 _COAT_FRONT_GLOW={238:363}` / `:275 _CHICKENBONES_GLOW_COLOR=(229,229,229,0)` —— ChickenBones coat 发光 + 代表色。
- `:220 _GLOW_ARKHALIS_ALPHA=180` —— arkhalis 发光 alpha（非时间动画）。
- 绘制函数：`_over_glow:438`(premult/additive 合成)、`draw_body_glow:675`、`draw_body205_frontarm_4tap:698`、`draw_head211_4tap:716`、`draw_strip_glow:734`、`draw_tv_head_glow:756`。

### D.3 glowmask.json（发光色，含动画代表值，已烤死）
- `body.237` / `head.268` / `head.282` / `legs.222` = `[193,193,193,0]` —— mouseText mc=222 代表。
- `head.284` = `[229,229,229,0]` —— ChickenBones mid 0.9。
- `head.292` = `[236,236,236,92]` —— Luna mid 0.925。
- `body_jitter.227=2` / `arm_jitter.227=2` / `head.240.jitter=2` / `legs.210.jitter=2` —— 抖动遍数。
- `head.271.grid="tv"` / `head.211.fourtap=241` / `head.269.extra={armor:214,mask:308}` —— 特殊绘制路由。
- `aux_masks=[308,363]` —— 额外提取的辅助 glowmask。

### D.4 逆向源（动画来源行号，全 `temp/decomp/full/`）
- `Terraria/Main.cs:18066-18073` —— mouseTextColor 在 [190,255] 每帧 ±1 三角往复。
- `Terraria/Main.cs:387/16777` —— `GlobalTimeWrappedHourly = totalSeconds % 3600`（染料 uTime）。
- `Terraria/Player.cs:28847` —— `miscCounter++`（每 tick +1，驱动 ChickenBones/Luna/TV/head282）。
- `Terraria.DataStructures/PlayerDrawLayers.cs:164-181` —— ChickenBones `Remap→[0.8,1.0]`，phase=`WrappedLerp(miscCounter%100/100)`(周期 100)。
- `…/PlayerDrawLayers.cs:183-197` —— Luna `Remap→[0.85,1.0]`，base A=100，4 通道乘 num。
- `…/PlayerDrawLayers.cs:2173-2200` —— head 282：彩甲 `miscCounter%36/4`(9 帧)、发光层固定 frame0。
- `…/PlayerDrawLayers.cs:2357-2385` —— TV 头 6×4：col=GetTVScreen、row=`miscCounter%20/5`(4 帧,周期 20)、`Frame(6,4,col,row,-2)`(宽 42→40)。
- `…/PlayerDrawLayers.cs:2514-2536` —— `DrawPlayer_Head_GetTVScreen`（离线状态 → `return 3`）。
- `…/PlayerDrawLayers.cs:63-76/90-101/118-135/2403-2415` —— 227 抖动 2 遍 / 205 与 211 的 `RandomInt*0.2`(X) /`*0.15`(Y) 4-tap。
- 染料 shader 周期/相位/offsets：`research/dye_passes_spec.md:441-526`（time-animated + self-sampling）、`research/noise_dyes_spec.md:108-189`（noise uv/uTime/per-pass combine）。

---

## Caveats / Not Found

1. **MidnightRainbow / Solar / Void / Hades / Mirage / Loki（9 netId）当前是 APPROX 静态塌缩，无 uTime** —— `dye.py` 里它们不跑真 self-sampling bytecode（`_solar` 甚至是手写火焰 ramp）。要做"完整周期逐帧动画"必须先把这些 pass 升级成真多-tap+uTime 实现（offsets/uTime/combine 已在 `dye_passes_spec.md:493-526` 给出），否则它们的"动画"无法渲染，只能出当前那一张近似静帧。Solar 的 `_PILLAR_TIME["ArmorSolar"]=5.0` 是为真 bytecode 路径预留的，**当前代码路径未使用**（Solar 不走 noise 分支）。因此"20 pass / 28 netId 可动画"里，**现成能扫的是 19 netId（time 7 + noise 12）**，其余 9 netId 需升级。
2. **`apply_dye` 当前不透传 uTime** —— 链路下游（`_noise_pass`/各 time-animated 函数）都有 `u_time`/`uTime` 形参且默认 0，但顶层 `apply_dye`(`dye.py:734`) 没暴露。逐帧脚本需要么直接调底层函数、要么给 `apply_dye` 加一个可选 `u_time` 转发参数。
3. **noise 动画的精确周期未逐一实测** —— noise pass 的 uTime scroll/swirl 周期（如 Nebula 云一圈、Gel tap 旋转 2π）是从 disasm 结构推的（`noise_dyes_spec.md`），建议渲染时先扫一个较大 uTime 区间出 contact sheet 目测循环点，再定 N。竖滚类(Sands/Pearlsands/Fog)的 `frc(...+uTime)` 周期确定为 1.0。
4. **mouseTextColor / miscCounter 无"相位 0"语义** —— 是连续往复计数器，离线无确定起点；代表值取区间中点是设计抉择（与 dye.py UTIME=0 同精神）。逐帧时按"一个完整三角周期"扫即可（mouseText 130 帧、ChickenBones/Luna 100 ticks、TV row 20 ticks、head282 彩甲 36 ticks）。
5. **TV 头 col 离线恒 3** —— `GetTVScreen` 依赖玩家危险/低血/生态/湿/town-NPC 等运行态(`PlayerDrawLayers.cs:2514`)，离线全 false → 恒 3。要渲染其他 5 列需人为构造这些状态，非"自然动画"。
6. **head 216 / head 285 / legs 157 无 shipped equip item** —— `equip_slots.json` 反查不到对应 netId（dev/未发布或仅内部 equip 槽）。其发光色/mask 仍在 `glowmask.json`（mask 256/367/249），但无玩家可穿物品名，故表 B.5 标"无 shipped item"。
7. **arkhalis 发光色随外观（衬衫色）变但非时间动画** —— `(underShirtColor, A=180)`，每个角色外观不同，但同一角色内不随时间动，归静态单帧。
