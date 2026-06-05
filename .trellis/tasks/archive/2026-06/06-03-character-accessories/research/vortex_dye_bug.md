# Research: Vortex 染料 (netId 3528, pass ArmorVortex) 渲染与游戏不一致根因

- **Query**: 诊断 Vortex 染料渲染（深青绿底 + 大量亮白/青色条纹）与游戏不一致的根因，逆向 `ArmorVortex` 字节码为准，给逐区对比 + 修复规格 + 回归注意
- **Scope**: internal（逆向 ps_2_0 字节码 + 数值复现 + 我方实现核对）
- **Date**: 2026-06-04

## TL;DR / 一句话根因

我方 **ps_2_0 字节码解释器对 ArmorVortex 是忠实的**（uv 映射、噪声采样区、颜色常量、sparkle 公式都对，且 Vortex 字节码确无 `_sat`）。亮白条纹本身**在游戏里也存在**（Vortex 的 sparkle 项 `noise·luma·5·uSecondary(白)` 在亮源像素上真就过曝到白）。**真正的偏差是我方在字节码之上额外套了 `emissive=True` 的 `gain=1.5` + `_emissive_tonemap`（溢出回灌全通道→更白）**，把游戏本应"硬裁剪到白"的条纹进一步放大约 **1.5–3×** 覆盖面积，导致鹿角/腿等亮区比游戏更白更抢眼。`_PILLAR_TIME=0.5` 与亮度**无关**（uTime 在 Vortex 只进噪声 uv 的 swirl 相位，不进任何亮度乘子），不是 bug。

**修复要点**：把 `_vortex` 的 `gain` 从 1.5 降到 ~1.0、并改用更温和的 tonemap（或对 Vortex 直接走 `emissive=False` 硬裁剪 = 游戏行为），即与游戏一致；对 Nebula/Stardust/HallowBoss **零回归**（它们各有独立 `_PILLAR_GAIN`，不共享）。

---

## 1. 真实 ArmorVortex 公式（逆向为准）

### 1.1 着色器注册与常量绑定

- 注册：`ArmorVortex` 是 `Misc/noise` 噪声 pass，pixel program 在 `PixelShader.xnb`（`temp/xnb_probe/in/PixelShader.xnb`）；blob 已 base64 烘焙进 `nextbot/terraria_render/data/noise_shaders.json` 的 `ArmorVortex` 条目（blob=2020 bytes）。
- **CTAB（uniform → const 寄存器）**，从 blob 反解：
  | 寄存器 | uniform |
  |---|---|
  | `c4` | `uColor` |
  | `c5` | `uSecondaryColor` |
  | `c6` | `uTime` |
  | `s0` | `uImage0`（源精灵） |
  | `s1` | `uImage1`（`Misc/noise`，256×256） |
- **preshader 输入**（`noise_shaders.json[ArmorVortex].pres_inputs`，由 `_build/gen_noise_shaders.py:42` 烘焙）：`{0: uSourceRect, 1: uImageSize1}`。preshader 填充 `c0/c1/c2/c3`：
  - `c0 = (128,128)` = `uImageSize1/2`（噪声尺寸的一半，tile-and-divide family 共有）
  - `c1 = (1/128, 1/128)`
  - `c2 = (0,0)`
  - `c3 = (uSourceRect 派生的极坐标归一缩放)`：随精灵帧宽高变化（src 40×56 → c3=(6.4,4.571)；src 360×56 → c3=(0.711,4.571)，c3.y 只随高 56 固定，c3.x 随宽反比）。
- **def 字面量**（atan2 Taylor 近似 + 组合常量）：
  - `c7 = (-0.5, 0.02084, -0.08513, 0.1801)`
  - `c8 = (-0.3303, 0.9999, 0, 1)`
  - `c9 = (-2, 1.571, -0, -3.142)`
  - `c10 = (0.1, -0.1, 0.3333, 5)`

> 数值用 `dye_noise._run_preshader` + `_parse_blob('ArmorVortex')` 实跑得到（与 `noise_dyes_spec.md` §2 "Nebula/Vortex/Stardust: c0=(128,128) c1=(1/128,1/128) c2=(0,0)" 一致）。

### 1.2 完整反汇编（authoritative，等于游戏行为）

```asm
def c7,  (-0.5, 0.02084, -0.08513, 0.1801)
def c8,  (-0.3303, 0.9999, 0, 1)
def c9,  (-2, 1.571, -0, -3.142)
def c10, (0.1, -0.1, 0.3333, 5)
dcl v0 ; dcl t0.xy ; dcl s0 ; dcl s1
mul r0.xy, t0, c0            ; t0(sprite uv)*128
frc r0.zw, r0.wzyx           ; floor(t0*128) via frac trick →
add r0.xy, r0, -r0.wzyx      ;   r0.xy = floor(t0*128)
mov r1.xy, c1
mad r0.xy, r0, r1, -c2       ; r0 = floor*(1/128) - 0  = 量化后的格点 [0,1)
mov r1.x, c7.x
mad r0.xy, r0, c3, r1.x      ; r0 = grid*c3 + (-0.5) → 居中到极坐标域（c3 含 uSourceRect 缩放）
; ── 极坐标 angle/radius via abs/max/min/rcp + atan2 Taylor (c7.yzw,c8,c9) ──
abs r0.z,r0.x ; abs r0.w,r0.y ; max r1.x,r0.z,r0.w ; rcp r1.x,r1.x ; min r1.y,r0.w,r0.z
... (c7.y/z/w,c8.x/y atan2 多项式) ... → r0.z = 角度（弧度，含 c9 象限修正）
; ── radius ──
mul r0.y,r0.y,r0.y ; mad r0.x,r0.x,r0.x,r0.y ; rsq r0.x ; rcp r0.x  ; r0.x = sqrt(x²+y²)=radius
add r1.y, r0.x, uTime.x      ; ★ uTime 仅在此进入：noise uv.y = radius + uTime（swirl 相位）
add r0.x, r0.x, c10.x        ; radius + 0.1
min r1.z, r0.x, c8.w         ; clamp(radius+0.1, ≤1)   = sparkle 半径衰减门
mul r0.xy, r1, c10.x         ; noise uv = (angle, radius+uTime) * 0.1
texld r0, r0, s1             ; ★ r0 = noise 采样（uImage1=Misc/noise，bilinear WRAP）
texld r2, t0, s0             ; r2 = 源像素（uImage0）
mad r0.x, r0.x, r1.z, c10.y  ; noise.x*半径门 - 0.1   = sparkle 强度（门控）
add r0.y, r2.y, r2.x ; add r0.y, r2.z, r0.y ; mul r0.y, r0.y, c10.z   ; ★ r0.y = luma = (r+g+b)*0.3333
mul r0.z, r0.x, r0.y         ; sparkle * luma
mul r0.z, r0.z, c10.w        ; * 5
mul r1.xyz, r0.z, uSecondaryColor   ; ★ sparkle = noise·luma·5·uSecondary
cmp r1.xyz, r0.x, r1, c8.z   ; if (sparkle_strength>=0) keep else 0   （门控 sparkle）
mad r0.xyz, uColor, r0.y, r1 ; ★ out.rgb = uColor·luma + sparkle
mov r0.w, c8.w               ; alpha=1
mul r0, r2.w, r0             ; * 源 alpha（premult）
mul r0, r0, v0               ; * vertex color (白)
mov oC0, r0
```

### 1.3 精确 source→output（游戏公式）

```
luma     = (src.r + src.g + src.b) * 0.3333
angle    = atan2(centered.y, centered.x)        # c7/c8/c9 Taylor
radius   = length(centered)                     # centered = 量化格点经 c3/uSourceRect 居中
noise    = sampleWRAP(Misc/noise, uv=(angle, radius + uTime) * 0.1).x
sparkle  = noise * clamp(radius+0.1, ≤1) - 0.1
out.rgb  = uColor * luma  +  step(sparkle≥0) * (sparkle * luma * 5 * uSecondaryColor)
out.rgb *= src.a                                # premult；最终 GPU 硬裁剪 [0,1]
out.a    = src.a
```

**关键结论**：
- **噪声纹理**：`Misc/noise`（256×256），**不是**位置/源 rgb 当 uv，而是**极坐标(angle, radius)·0.1** 当 uv（swirl）。源 rgb **不进 uv**，只通过 `luma` 进亮度。我方采样区/uv 映射与此一致（见 §2）。
- **uColor=(0.1,0.5,0.35)**（深青绿）：只作为 `uColor·luma` 的**底色**——故底色是随源明暗变化的深青绿。
- **uSecondaryColor=(1,1,1)**（**白**）：是 sparkle 条纹的颜色——**这就是亮白/青条纹的来源，且是游戏本身的设计**。
- **sparkle 强度 = noise·luma·5**：源越亮（luma 越大）+ 噪声越高 → 白色 sparkle 越强，**在亮源像素上 ≫1 会被硬裁剪成白**。鹿角/腿亮 → 条纹白且密；躯干暗 → 偏绿。
- **uTime 只进噪声 uv.y（swirl 相位），不进任何亮度乘子** → 改 uTime 只是旋转哪条条纹出现，**不改变整体亮度/白度**。

---

## 2. 我方实现核对（逐项比对）

调用链：`apply_dye`（`dye.py:863`）→ `_vortex`（`dye.py:632`）→ `_noise_pass`（`dye.py:533`）→ `dye_noise.run_noise_pass`（`dye_noise.py:372`）跑真实字节码 → 回到 `_noise_pass` 做 emissive tonemap。

| 比对项 | 真实字节码 | 我方实现 | 结论 |
|---|---|---|---|
| **噪声纹理** | `uImage1 = Misc/noise` 256×256 | `assets/noise.png`（256×256），`dye_noise.py:435` 绑 `s1` | ✅ 对 |
| **uv 映射** | 极坐标(angle,radius+uTime)·0.1，源 rgb 不进 uv | 解释器忠实跑字节码（`_run_ps` `dye_noise.py:271`），t0=精灵 uv（`dye_noise.py:289`），texld 走 `_sample_tex` bilinear WRAP（`dye_noise.py:79`） | ✅ 对（**非** Stardust 那类输入映射错） |
| **采样寻址** | bilinear WRAP | `_sample_tex` `% 1.0` + 双线性（`dye_noise.py:83-94`） | ✅ 对 |
| **preshader 输入** | `{0:uSourceRect,1:uImageSize1}` → c0=128,c3 含 uSourceRect | `gen_noise_shaders.py:42` 烘焙同值；`run_noise_pass` 实跑 preshader（`dye_noise.py:428-429`），实测 c0=(128,128) ✅ | ✅ 对（与 Stardust 不同：Stardust 的 c0 须喂 uTime，Vortex 本就是 uSourceRect） |
| **uColor / uSecondary** | dyes.json[3528]: color=(0.1,0.5,0.35), secondary=(1,1,1) | `dyes.json[3528]` 完全一致；`_vortex` 默认同值（`dye.py:633/643`），`run_noise_pass` 绑 c4/c5（`dye_noise.py:407-408`） | ✅ 对 |
| **uSat** | Vortex 字节码不用 uSaturation | `_vortex` 传 uSat=1.0（`dye.py:644`），无影响 | ✅ 对 |
| **`_sat` 修饰符** | Vortex blob **无** `_sat`（反汇编全程未见 `_sat`） | 解释器 `_dst` 支持 `_sat`（`dye_noise.py:263`）但 Vortex 不触发 | ✅ 不是 `noise_dye_luma_bug.md` 的 `_sat` 受害者 |
| **sparkle 公式** | `noise·luma·5·uSecondary`，硬裁剪 | 字节码忠实产出（解释器 oC0） | ✅ 字节码层忠实 |
| **uTime 代表值** | uTime 只进 uv 相位，**不改亮度** | `_PILLAR_TIME["ArmorVortex"]=0.5`（`dye.py:497`），注释写"最亮 teal 能量条纹相位"——**该理由错误**：uTime 不改亮度，只旋相位 | ⚠️ 注释误导，但**视觉无害**（0 vs 0.5 几乎相同，见 §3） |
| **emissive 后处理** | **无**（GPU 直接硬裁剪 [0,1]，offline 无 additive bloom 也应硬裁剪 = 游戏） | `_vortex` 传 `emissive=True` + `gain=_PILLAR_GAIN["ArmorVortex"]=1.5`（`dye.py:508,646-647`），`_noise_pass` 调 `_emissive_tonemap(rgb,1.5)`（`dye.py:563`）：先 `*1.5`（`dye.py:525`）再把每像素 `max(channel)-1` 的溢出**回灌到全部通道**（`dye.py:528` `lifted = g + overflow`）→ 亮像素整体推向白 | ❌ **这是与游戏的主要偏差来源** |

### 我方偏差根因（file:line）

1. **`nextbot/terraria_render/dye.py:508`** — `_PILLAR_GAIN["ArmorVortex"] = 1.5`：在忠实字节码输出上整体乘 1.5。
2. **`nextbot/terraria_render/dye.py:525,528`** — `_emissive_tonemap`：`g = rgb*1.5` 后 `lifted = g + overflow`（溢出回灌全通道），把本应保留青绿色相的亮像素进一步**去饱和推白**。
3. **`nextbot/terraria_render/dye.py:646`** — `_vortex(..., emissive=True)`：选择了 tonemap 分支而非游戏的硬裁剪分支（`dye.py:563` 的 `np.clip` else 支）。
4. **`nextbot/terraria_render/dye.py:497`** — `_PILLAR_TIME["ArmorVortex"]=0.5` 注释理由错误（uTime 不改亮度）；非视觉 bug，但建议改注释 / 归 0 以与"idle 代表帧"语义一致。

> 解释器与 `gen_noise_shaders` 与 `dyes.json` **均无 bug**；偏差全在 `dye.py` 的 emissive 放大层。

---

## 3. 判别样本：忠实字节码 vs 我方当前输出（逐区对比）

**方法**：取真实装备帧 idle frame0（slot 经 `equip_slots.json`：DeerclopsMask head→`Armor_Head_276.png`，PumpkinShirt body→`ArmorBody_82.png`，MoonLordLegs legs→`Armor_Legs_217.png`），分别跑：
- **A 忠实** = `run_noise_pass` 原始 oC0 → un-premult → **硬裁剪 [0,1]**（= 游戏 GPU 行为，gain=1，无 tonemap）
- **B 我方** = `_vortex`（gain=1.5 + `_emissive_tonemap`，uTime=0.5）

"白条纹率" = 裁剪后三通道 min>0.7 的像素占比（近白）。可视 3-up（源｜A 忠实｜B 我方）见 `temp/dynamic_frames/_diag_vortex_3up.png`；既有症状图 `temp/dynamic_frames/_diag_vortex_cur0p5.png`（uTime0.5）、`_diag_vortex_t0.png`（uTime0）几乎相同 → 证实 uTime 无关亮度。

| 区域 (源 luma) | A 忠实 mean(clip) | A 白条纹率 | B 我方 mean(clip) | B 白条纹率 | 偏差 |
|---|---|---|---|---|---|
| **HEAD** Deerclops 276 (luma 0.456, max 0.92) | `[.374,.504,.458]` | **21.3%** | `[.484,.628,.577]` | **32.3%** | 我方亮度↑~30%、白率↑1.5× |
| **BODY** Pumpkin 82 (luma 0.265, max 0.64) | `[.046,.152,.112]` | **0.0%** | `[.069,.228,.167]` | **0.1%** | 躯干基本一致（暗→偏绿，两者都对） |
| **LEGS** MoonLord 217 (luma 0.373, max 0.83) | `[.334,.462,.417]` | **13.9%** | `[.463,.601,.551]` | **28.3%** | 我方亮度↑~35%、白率↑2× |

**哪一步偏了**：
- 字节码层（A）已**自带**亮白条纹（鹿角 21%、腿 14%）——**这是游戏行为，正确**。源越亮条纹越白（sparkle=noise·luma·5），躯干暗→几乎全绿（0%）。
- 我方在 A 之上 `gain=1.5` + 溢出回灌 tonemap（B），把每区平均亮度抬 ~30–35%、白条纹覆盖**翻约 1.5–2×**，且去饱和（青绿被推白）→ 这正是用户看到的"亮白/青条纹抢眼、比游戏更白"。
- **uTime**：A 在 uTime=0 vs 0.5，白率 19.7%↔21.3%（head）、9.4%↔13.9%（legs），只是条纹位置/微量变化，**非主因**。

补充（emissive 是否"合理"）：Vortex sparkle 在亮源上确为 over-unity（鹿角 max channel 扫相位达 **3.19**、20% 像素 >1；腿 2.56/14%；躯干 1.18/<1%），所以"它是 emissive over-unity pass"判断没错——**但游戏对 over-unity 也只是硬裁剪到白**，offline 再乘 1.5 + 回灌是**双重过曝**。

---

## 4. 修复规格（最小正确修法）

**目标**：让 Vortex 输出 = 忠实字节码硬裁剪（= 游戏）。两种等价改法，按侵入度排序：

### 方案 A（推荐，最小且零回归）— Vortex 走硬裁剪
- 改 `nextbot/terraria_render/dye.py:646` 的 `_vortex(...)`：把 `emissive=True` 改为 `emissive=False`（删掉 `gain` 传参或忽略），使 `_noise_pass`（`dye.py:563`）走 `np.clip(rgb,0,1)` 分支 = 游戏 GPU 硬裁剪。
- 同时 `_PILLAR_GAIN` 删掉/忽略 `"ArmorVortex"`（`dye.py:508`）。
- 代价：暗装备上 Vortex 底色会比现在略暗（少了 1.5× 提亮），但这正是游戏行为；亮装备的白条纹回落到游戏水平。

### 方案 B（保留一点 offline 提亮，但去掉"推白"）
- 若希望保留少量 emissive 提亮以补偿 offline 无 bloom：把 `_PILLAR_GAIN["ArmorVortex"]` 从 **1.5 降到 ~1.0–1.1**（`dye.py:508`），并**单独**给 Vortex 用一个不回灌的 tonemap（直接 `clip(rgb*gain,0,1)`，去掉 `dye.py:528` 的 `lifted = g + overflow`）。回灌（overflow→全通道）正是把青绿推成白的元凶，对底色饱和度伤害最大。
- 不要全局改 `_emissive_tonemap`（Nebula/Stardust 依赖回灌让 over-unity 星点/云读成白光，见回归）。

### uTime 注释/语义修正（非视觉 bug，建议顺手）
- `dye.py:497` 把 `"ArmorVortex": 0.5` 的注释从"最亮 teal 条纹相位"改为"swirl 相位（uTime 仅旋转 noise uv，不改亮度）"；可改为 `0.0` 与 idle 语义一致（视觉差异极小，见 §3）。**勿误以为调 uTime 能修白条纹**。

### 不要做的事
- **勿改 `dye_noise.py` 解释器**：字节码、preshader、uv、采样区均已忠实，改它会引回归。
- **勿改 `noise_shaders.json` / `gen_noise_shaders.py` 的 Vortex pres_inputs**：`{0:uSourceRect,1:uImageSize1}` 正确（与 Stardust 的"应喂 uTime"不同——Stardust 的 c0 须为 uTime，Vortex 的 c0 本就是 uSourceRect→128）。
- **勿改 `dyes.json[3528]`**：color/secondary/sat 与游戏 DyeInitializer 一致。

---

## 5. 回归注意（对其它噪声 pass）

- **零回归**：`_PILLAR_GAIN` / `_PILLAR_TIME` 是 **per-pass dict**（`dye.py:494-511`），Nebula/Stardust/HallowBoss 各有独立条目，改 Vortex 那一项**不影响**其它 pass。方案 A/B 只动 `_vortex` 的调用与 Vortex 的 gain 项。
- **需要保持的对照（验证修复时同时看一眼，确保没误伤）**：
  - **ArmorStardust**（gain 1.35）：faithful 在普通装备上 mean 0.402、over-unity **24%**、maxch 2.45 → 它**真正依赖** emissive lift + 回灌让星点读成白光；**保持不动**。
  - **ArmorNebula**（gain 1.4）：faithful mean 0.225、maxch 1.30 → 云需要轻度提亮；**保持不动**。
  - **ArmorHallowBoss**（gain 1.0）：已不提亮（in-gamut），不受影响。
- 若选**全局**改 tonemap（不推荐），务必重测 Stardust/Nebula 的星点/云白光是否变暗——这是为什么建议**仅对 Vortex 局部改**（方案 A）。
- `noise_dye_luma_bug.md:209,274` 已记录 "ArmorVortex 不受 `_sat` luma bug 影响、字节输出不变"——本次结论与之一致，本 bug 属**后处理放大层**，与那条 `_sat` 修复正交，互不影响。

---

## 相关文件 (file:line)

| 文件 | 关键行 | 说明 |
|---|---|---|
| `nextbot/terraria_render/dye.py` | 632-648 | `_vortex`：`emissive=True` + `gain=1.5`（偏差入口） |
| `nextbot/terraria_render/dye.py` | 494-511 | `_PILLAR_TIME`(497=0.5) / `_PILLAR_GAIN`(508=1.5) per-pass 表 |
| `nextbot/terraria_render/dye.py` | 514-529 | `_emissive_tonemap`：525 `*gain`、528 溢出回灌全通道（推白元凶） |
| `nextbot/terraria_render/dye.py` | 533-565 | `_noise_pass`：563 emissive→tonemap / else→硬裁剪（游戏） |
| `nextbot/terraria_render/dye.py` | 863-864 | `apply_dye` 派发 ArmorVortex→`_vortex` |
| `nextbot/terraria_render/dye_noise.py` | 271-368 | `_run_ps` ps_2_0 解释器（忠实，无需改） |
| `nextbot/terraria_render/dye_noise.py` | 79-94 | `_sample_tex` bilinear WRAP（采样区正确） |
| `nextbot/terraria_render/dye_noise.py` | 372-436 | `run_noise_pass`：preshader→consts→texld（uv/常量正确） |
| `nextbot/terraria_render/data/noise_shaders.json` | `ArmorVortex` | blob(2020B) + `pres_inputs={0:uSourceRect,1:uImageSize1}`（正确） |
| `nextbot/terraria_render/_build/gen_noise_shaders.py` | 42 | Vortex pres_inputs 烘焙（正确） |
| `nextbot/terraria_render/data/dyes.json` | `3528` | color=(0.1,0.5,0.35) secondary=(1,1,1) sat=1 pass=ArmorVortex（正确） |
| `temp/xnb_probe/in/PixelShader.xnb` | — | 真实 ps_2_0 源（反汇编经此 blob） |
| `temp/dynamic_frames/_diag_vortex_3up.png` | — | 本次生成：源｜忠实硬裁剪｜我方 三联对比 |
| `temp/dynamic_frames/_diag_vortex_cur0p5.png` / `_diag_vortex_t0.png` | — | 既有症状图（uTime 0.5 vs 0 几乎相同→证 uTime 无关亮度） |
| `temp/xnb_probe/out/pillar_3528.png` | — | 我方整角色 Vortex（含 gain+tonemap，偏白） |

## Caveats / Not Found

- 反汇编的极坐标 angle 段（c7.yzw/c8/c9 atan2 Taylor）我标注为"角度多项式"，未逐指令展开数值，但这不影响结论：解释器逐指令忠实执行同一字节码，angle/radius 都由它算出，且 uv 仅 `*0.1` 后采样——亮度通道与 uTime 路径已精确追踪（§1.3 公式经 `dye_noise` 实跑验证）。
- 逐区数值用 **frame0（idle）**；其它动作帧 src_rect 不同 → c3 随之变（极坐标缩放），条纹分布会变，但白条纹的**过曝机制与 gain 放大结论与帧无关**（取决于源 luma 与 gain，不取决于哪一帧）。
- "白条纹率 min>0.7" 是近白启发式阈值，用于量级对比（忠实 vs 我方约 1.5–2×），非像素级裁剪计数；绝对值随阈值变，相对差稳健。
- 真实游戏截图未提供，基准来自"忠实跑 ps_2_0 字节码 + 硬裁剪 = 游戏行为"这一权威前提（任务给定）。
