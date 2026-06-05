# Research: Vortex 染料 sparkle "亮白条纹爆多" 根因（从原始字节码重新逆向）

- **Query**: 重新逆向 Vortex 染料（item netId 3528, pass `ArmorVortex`）的 sparkle，从原始 `PixelShader.xnb` 字节码重新推导，定位"亮白条纹爆多"的根因，**不信** `noise_shaders.json[ArmorVortex]` 与上一轮"忠实"结论
- **Scope**: internal（从游戏当前 `PixelShader.xnb` 重新反汇编 ps_2_0 + D3DX preshader + 数值复现 + 跨 pass 对照 + 原型验证）
- **Date**: 2026-06-05

## TL;DR / 一句话根因

**不是取错 pass，不是漏阈值解码错，不是 uSecondary 错，也不是 emissive/gain（上一轮那个"修复"已落地、白条纹照旧）。** 重新从游戏当前 `PixelShader.xnb`（与 Steam 安装目录**字节相同**）反汇编 `ArmorVortex`，并逐项验证：blob、technique→pass→object 关联、噪声纹理、preshader 常量、uSourceRect、解释器逐指令——**全部与游戏一致，我方输出 = 游戏 GPU 输出（逐像素 mean|diff|=0.0 对照参考解释器）**。

亮白条纹是 **Vortex 着色器本身的真实数学行为**，而它比 Nebula/Stardust 白得多的**专属原因**是三者 sparkle 公式不同：

| pass | sparkle 门限（noise 阈值） | sparkle 颜色 | 叠加方式 | 纯白(三通道全>1)倾向 |
|---|---|---|---|---|
| **ArmorVortex** | `noise·radiusGate ≥ 0.1`（radius≈2.9→radiusGate≈1→**实际阈值 noise≥0.1，85% 噪声命中**） | `uSecondary=(1,1,1)` **白** | **等量加到 RGB 三通道** | **极高（鹿角头 16.4%、亮身 32.9%）** |
| ArmorNebula | `noise ≥ 0.4`（仅 18% 噪声命中） | 白，但 uColor 绿通道=0 | 加 | 0.0% |
| ArmorStardust | rainbow(noise) 星点（pow/frc）**带色**、稀疏 | 白 | **逐通道不等量**（彩色星点） | 极低（0.8–2.5%） |

→ **Vortex = 白 secondary × 低门限(85% 命中) × 等量灌三通道**：任何中等亮度像素都被推到三通道全>1 → 硬裁剪成纯白，且因 `t0.y` 在 40×1120 高图里极小 → 噪声 uv 主要沿 x 变 → 呈**密集竖条**。Nebula 门限严 4×、Stardust 是彩色稀疏星点，所以只有 Vortex"爆白"。

**这是忠实于真实字节码的结果。** 若要让离线渲染贴近用户记忆中"游戏里白只有一点点、偏暗、偏边缘、面部几乎没有"，需要**有意偏离**真实字节码：把 Vortex 的 sparkle bias（`def c10.y`，游戏=`-0.1`）调严到约 `-0.3`，近白率从 16.4%→1.0%（稀疏/偏暗/边缘），且**只影响 ArmorVortex**（见 §6 修复规格）。验证大图：`temp/dynamic_frames/_diag_vortex_reRE.png`。

> ⚠️ **诚实声明**：本轮无法取得游戏内 Vortex 真机截图。所有"我方=游戏"的结论建立在"忠实跑真实 ps_2_0 字节码 + 字节相同的 noise + 游戏一致的 uSourceRect/alpha-blend = 游戏 GPU 行为"这一可证链上（每一环都已字节级/逐像素核对，见 §2–§4）。若用户记忆中的"稀疏"来自**穿彩色装备**或**游戏内 ~2× 缩放叠在受光场景**上的观感，则我方离线（鹿角灰白皮 + scale 9 + 平背景硬裁剪）会把同一输出放大得"爆白"——这是观感/曝光差异而非管线 bug。§6 同时给"承认忠实"和"有意调稀"两条路。

---

## 1. 真实 ArmorVortex（从游戏当前字节码重新反汇编，authoritative）

### 1.1 源文件与提取链已字节级核对（排除"取错 pass / 旧文件"）

- `temp/xnb_probe/in/PixelShader.xnb`（13818B）与 Steam 安装目录
  `…/Terraria.app/Contents/Resources/Content/PixelShader.xnb`（13818B）**`cmp` 完全相同** → 源是当前游戏文件，非旧/错文件。
- FX 结构：**只有 1 个 technique（"Technique1"）、64 个 pass、71 个 object**。pass 序号 **31=ArmorNebula、32=ArmorVortex、33=ArmorStardust**，命名顺序正确。
- `ArmorVortex`（pass 32）下**恰好 1 个** ps_2_0 object（`#ps_objects_matching=1`）→ **不存在取错 blob 的可能**（无歧义）。其 CTAB = `[uColor, uImage0, uImage1, uSecondaryColor, uTime]`——正是反汇编所用参数（注意 **无 uSaturation**，与 Nebula 的 `[…,uSaturation,…]` 不同，可作为"没串 pass"的指纹）。
- `noise_shaders.json[ArmorVortex].blob`（2020B）与从 XNB 现提的 blob **IDENTICAL=True**（Nebula 1564B、Stardust 1688B 同样 IDENTICAL）。
- 验证工具：`temp/xnb_probe/disasm_stardust.py`（现成，直接反汇编 Stardust/Nebula/Vortex + 交叉核对 json）；本轮另跑了 object 关联枚举与 token-ilen 校验。

> 结论：**"别信 noise_shaders.json 可能取错 pass"已被排除——它取对了，且与游戏字节相同。**

### 1.2 完整反汇编（ps_2_0，game-authoritative）

CTAB：`uColor→c4, uSecondaryColor→c5, uTime→c6, uImage0→s0, uImage1→s1`。
preshader 实跑得 `c0=(128,128), c1=(1/128,1/128), c2=(sx/256, sy/256), c3=(256/sw, 256/sh)`；
帧 40×56 → **c3=(6.4, 4.571)**，idle(sx=sy=0) → c2=(0,0)。def 字面量：
`c7=(-0.5, 0.02084, -0.08513, 0.18014)`、`c8=(-0.33030, 0.99987, 0, 1)`、
`c9=(-2, 1.5708, -0, -3.14159)`、**`c10=(0.1, -0.1, 0.33333, 5)`**。

```asm
; ── 量化格点 → 极坐标域 ──
mul r0.xy, t0, c0            ; t0(全图 uv)*128
frc r0.zw, r0.wzyx
add r0.xy, r0, -r0.wzyx      ; floor(t0*128)
mov r1.xy, c1
mad r0.xy, r0, r1, -c2       ; floor/128 - c2  (量化 + sx/sy 偏移)
mov r1.x, c7.x
mad r0.xy, r0, c3, r1.x      ; centered = grid*c3 - 0.5   (c3=(6.4,4.571))
; ── angle (atan2 Taylor: abs/max/min/rcp + c7.yzw/c8/c9) ──
abs r0.z, r0.x              ; ★ opcode 0x23 = ABS（ilen=2，单源）——见 §3 关键
abs r0.w, r0.y             ; ★ 同上
max r1.x, r0.z, r0.w
rcp r1.x, r1.x
min r1.y, r0.w, r0.z
add r0.z, -r0.z, r0.w
cmp r0.z, r0.z, c8.z, c8.w
mul r0.w, r1.x, r1.y
mul r1.x, r0.w, r0.w
mad r1.y, r1.x, c7.y, c7.z
mad r1.y, r1.x, r1.y, c7.w
mad r1.y, r1.x, r1.y, c8.x
mad r1.x, r1.x, r1.y, c8.y
mul r0.w, r0.w, r1.x
mad r1.x, r0.w, c9.x, c9.y
mad r0.z, r1.x, r0.z, r0.w
cmp r0.w, r0.y, c9.z, c9.w
add r0.z, r0.z, r0.w
add r0.w, r0.z, r0.z
max r1.x, r0.x, r0.y
cmp r1.x, r1.x, c8.w, c8.z
min r1.y, r0.y, r0.x
cmp r1.x, r1.y, c8.z, r1.x
mad r1.x, r1.x, -r0.w, r0.z  ; r1.x = angle（弧度）
; ── radius = length(centered) ──
mul r0.y, r0.y, r0.y
mad r0.x, r0.x, r0.x, r0.y
rsq r0.x, r0.x
rcp r0.x, r0.x              ; ★ r0.x = sqrt(x²+y²) = radius  （实测均值≈2.9, max≈5.8）
; ── 噪声 uv 与 sparkle ──
add r1.y, r0.x, uTime.x     ; noise uv.y = radius + uTime  （uTime 仅旋相位）
add r0.x, r0.x, c10.x       ; radius + 0.1
min r1.z, r0.x, c8.w        ; ★ radiusGate = min(radius+0.1, 1) ≈ 1（因 radius≫1）
mul r0.xy, r1, c10.x        ; noise uv = (angle, radius+uTime) * 0.1
texld r0, r0, uImage1       ; r0 = noise 采样（Misc/noise 256², bilinear WRAP）
texld r2, t0, uImage0       ; r2 = 源像素
mad r0.x, r0.x, r1.z, c10.y ; ★ gate = noise·radiusGate + (-0.1)   = SPARKLE 门控值
add r0.y, r2.y, r2.x
add r0.y, r2.z, r0.y
mul r0.y, r0.y, c10.z       ; luma = (r+g+b)*0.3333
mul r0.z, r0.x, r0.y        ; gate * luma
mul r0.z, r0.z, c10.w       ; * 5
mul r1.xyz, r0.z, uSecondaryColor ; ★ sparkle = gate·luma·5 · uSecondary(白) —— 等量入 RGB
cmp r1.xyz, r0.x, r1, c8.z  ; if gate(r0.x) ≥ 0: 保留 sparkle 否则 0
mad r0.xyz, uColor, r0.y, r1 ; out.rgb = uColor·luma + sparkle
mov r0.w, c8.w              ; alpha=1
mul r0, r2.w, r0           ; * 源 alpha（premult）
mul r0, r0, v0            ; * 顶点色（白）
mov oC0, r0
```

### 1.3 精确公式（游戏 = 我方）

```
luma        = (src.r+src.g+src.b)/3
centered    = quantize(t0)·c3 - 0.5         # c3=(256/sw, 256/sh)=(6.4,4.571)
radius      = length(centered)              # 实测 ≈2.9 均值（≫1）
radiusGate  = min(radius+0.1, 1) ≈ 1        # 因 radius≫1，几乎恒为 1
noise       = sampleWRAP(Misc/noise, ((angle, radius+uTime)·0.1)).x
gate        = noise·radiusGate - 0.1 ≈ noise - 0.1
out.rgb     = uColor·luma + step(gate≥0)·(gate·luma·5·uSecondary)   # uSecondary=(1,1,1)
            然后 *src.a，最终 GPU 硬裁剪 [0,1]
```

**核心**：`radiusGate≈1` ⇒ sparkle 门限退化为 **noise ≥ 0.1**。Misc/noise 有 **85.1%** 像素 ≥0.1 ⇒ sparkle 在绝大多数像素点亮，且 `·uSecondary(白)` 等量灌三通道 ⇒ 中等亮度即全白。

---

## 2. 我方实现核对（逐项；结论：全部忠实，输出 = 游戏）

调用链：`apply_dye`（dye.py:775）→ `_vortex`（dye.py:640）→ `_noise_pass`（dye.py:541）→ `dye_noise.run_noise_pass`（dye_noise.py:372）跑真实字节码。

| 比对项 | 真实字节码/游戏 | 我方 | 结论 |
|---|---|---|---|
| **blob** | XNB pass32 (2020B) | `noise_shaders.json[ArmorVortex]` 与之 IDENTICAL | ✅ |
| **technique→pass→object** | 唯一映射，无歧义 | `gen_noise_shaders.py:74-82` 取对 | ✅ 非取错 pass |
| **opcode 0x23** | **ABS**（ilen=2 单源；token 校验确认） | `dye_noise.py:355` `0x23→np.abs` | ✅（fx_parse 把它标成"pow"是**显示标签错**，与运行无关；ilen=2 实证为 abs） |
| **rsq/rcp/min/max/cmp** | radius=√、radiusGate=min、门控=cmp | `dye_noise.py:340/337/347/349/359` 全实现 | ✅ |
| **噪声纹理** | `Misc/noise` 256² | `assets/noise.png` **与游戏 noise.xnb 字节相同**（mean\|diff\|=0.0，alpha=1） | ✅（§4） |
| **uv/采样** | 极坐标·0.1，bilinear WRAP | 解释器逐指令；`_sample_tex` `%1.0`+双线性 | ✅ |
| **preshader 常量** | c0=128, c3=(6.4,4.571) | `run_noise_pass` 实跑 preshader 得同值 | ✅ |
| **uColor/uSecondary/uSat** | DyeInitializer.cs:134-136 → (0.1,0.5,0.35)/(1,1,1)/1 | `dyes.json[3528]` 与 `_vortex` 默认完全一致 | ✅ uSecondary **是白**，不是青 |
| **uSourceRect/uImageSize** | `ArmorShaderData.Apply` L91-95：rect=(0,sy,40,56)，body sheet=(40,1120) | 头/腿资源即 40×1120，`_frame_geom` 传 (0,0,40,56)+(40,1120) | ✅（idle 与游戏同；见 §5 注） |
| **blend** | `Main.cs:23204/23235` Immediate SpriteBatch = **AlphaBlend**（非 additive） | 我方硬裁剪后 over 合成 = 等价 | ✅ |
| **emissive/gain** | 无（GPU 硬裁剪） | `_PILLAR_GAIN` **无 ArmorVortex**；`_vortex(emissive=False)` 走 `np.clip` | ✅ 已是硬裁剪 |

**逐像素铁证**：在同一源/同一参数下，我方 `dye_noise._run_ps`（经 `run_noise_pass`）与参考解释器 `temp/xnb_probe/ps_interp_full.py` 的 oC0 **mean\|diff\|=0.0, max\|diff\|=0.0**。→ 解释器对 Vortex **100% 忠实**。

> **推论**：blob 忠实 + 噪声字节相同 + uSourceRect 与游戏一致 + alpha-blend 等价 ⇒ **我方 Vortex 输出 = 游戏 GPU 逐像素输出**。密集白条是游戏 Vortex 着色器的真实产物。

---

## 3. 上一轮结论已被证伪（"emissive/gain 放大"不是根因）

上一轮（`research/vortex_dye_bug.md`）判定根因是 `dye.py` 的 `gain=1.5 + _emissive_tonemap` 溢出回灌"双重过曝"，修复=去 gain 走硬裁剪（"plan A"）。

**现状**：plan A **已落地**——`_PILLAR_GAIN`（dye.py:516-519）现仅含 `ArmorNebula(1.4)/ArmorHallowBoss(1.0)`，**ArmorVortex 与 ArmorStardust 已被移除**；`_vortex(…, emissive=False)`（dye.py:659）走 `np.clip` 硬裁剪（dye.py:571 的 else 支）。

**但白条纹照旧**：实测 `dye._vortex`（生产路径）与裸 `run_noise_pass` 在鹿角头上**都是 16.4% 近白，逐像素相同** → **`dye.py` 没有附加任何白**，emissive 早已 OFF。**∴ 上一轮根因（后处理放大层）是错的**：去掉它白条纹不减。真正原因在 §1.3 的着色器数学本身（门限+白 secondary+等量灌通道），与 emissive 无关。

---

## 4. 噪声纹理核对（排除 noise.png 错）

解码游戏 `…/Content/Images/Misc/noise.xnb`（256×256）与 `assets/noise.png` 对比：

```
GAME noise.xnb : R mean 0.2506  frac>=0.1 0.851  frac>=0.4 0.184  alpha=1
OURS noise.png : R mean 0.2506  frac>=0.1 0.851  frac>=0.4 0.184
ours==game premult RGB? True   ours==game straight RGB? True   mean|diff|=0.0
```

**字节相同**（alpha 全 1，premult==straight）。所以"85% 像素 ≥0.1 → Vortex 85% 命中"是**游戏噪声的客观属性**，不是我方资源错。Nebula 阈值 0.4 仅 18% 命中→稀疏，同一张噪声。

---

## 5. sparkle 密度对照（量化：为什么只有 Vortex 爆白）

同一源、同一张噪声、各自代表 uTime，近白率（裁剪后三通道 `min>0.85`，即"纯白"）：

| 源（base） | **Vortex** | Nebula | Stardust |
|---|---|---|---|
| HEAD Deerclops（灰白皮, luma 0.46, max 0.92） | **16.4%** | 0.0% | 2.5% |
| BODY 193（亮, luma 0.64） | **32.9%** | 0.0% | 0.8% |
| BODY 177（彩色, luma 0.40, chroma 0.63） | **11.2%** | 0.0% | 0.0% |

**机制对照**（pre-clip，"三通道全>1"的比例 = 会裁成纯白的量，鹿角头）：

| pass | sparkle 门限 | 命中率 | sparkle 颜色 | 三通道全>1 |
|---|---|---|---|---|
| **Vortex** | noise≥0.1 | **85%** | 白·等量灌 RGB | **13.2%** |
| Nebula | noise≥0.4 | 18% | 白但 uColor 绿=0 | 0.0% |
| Stardust | rainbow 星点(稀疏) | 稀疏 | **逐通道不等量(彩色)** | 1.0% |

- **Vortex 爆白三因叠加**：①门限低（radiusGate≈1→阈值 0.1→85% 命中）；②`uSecondary=(1,1,1)` 把同一标量**等量加到 RGB**；③源中等亮即 `gate·luma·5 > 1` → 三通道齐过 1 → 纯白。
- **竖条形态**：头/腿是 40×1120 高图，`t0.y=(sy+py+0.5)/1120∈[0,0.05]` 极小 → `floor(t0.y*128)` 只有 ~6 个台阶 → 噪声 uv 几乎只沿 x 变 → **密集竖条**（与 `_diag_vortex_reRE.png` 第 2 列一致）。这是 c3/量化的忠实结果。
- Nebula/Stardust 无此问题：Nebula 阈值高 4×；Stardust sparkle 是 pow/frc 出的**彩色**稀疏星点，几乎不会三通道齐过 1。

可视化大图：**`temp/dynamic_frames/_diag_vortex_reRE.png`**（3 行 base × 4 列 [源｜Vortex｜Nebula｜Stardust]，scale 9；每格顶部红条长度 = 近白率）。肉眼即见 Vortex 满屏白竖条、Nebula 纯净、Stardust 仅零星白点。

---

## 6. 修复规格（两条路，按"是否偏离真实字节码"分）

### 路线 A —— 承认忠实，不改着色器（若判定是观感/曝光差）

数学上我方 = 游戏。若用户"游戏白很少"的记忆来自**穿彩色装备**或**游戏内小尺寸叠受光场景**，则无需改管线；可改的是**诊断基材/呈现**：
- 鹿角灰白皮（DeerclopsMask）是"最易暴露 dye"的高亮灰度面，**专门放大** Vortex 白；contact sheet 用它会让 Vortex 看着远比实战夸张。可加一张"彩色装备"对照行（已在 `_diag_vortex_reRE.png` 提供 body177）。
- 不动任何 `noise_shaders.json` / `dye_noise.py` / `dye.py` 着色器项。

### 路线 B —— 有意调稀（若产品要贴近"白只有一点点、偏暗、偏边缘、面部几乎无白"）

**这是对真实字节码的刻意偏离**，但只动 Vortex 一项、零回归。Vortex sparkle 门控值 `gate = noise·radiusGate + c10.y`，游戏 `c10.y=-0.1`。把它调严即可让 sparkle 稀疏（高 noise 才点亮）、且整体偏暗（gate 变小）：

| `c10.y`（sparkle bias） | 近白率(鹿角头) | 观感 |
|---|---|---|
| **-0.10（游戏真值）** | 16.4% | 满屏白竖条（现状） |
| **-0.30** | **1.0%** | 稀疏、偏暗、集中边缘高噪声区（贴近用户描述）✓ |
| -0.50 | 0.0% | 几乎无白，仅深青绿底 |

**落点（精确，零回归）**——三选一，等价但侵入度不同：

1. **运行时 per-pass 覆盖（推荐，最干净、可文档化为"deliberate deviation"）**：在 `dye_noise.run_noise_pass`（dye_noise.py:372）里，仅当 `name=="ArmorVortex"` 时，在执行字节码前把该 blob 的 `def c10` 第二分量（识别特征：`c10≈(0.1,-0.1,0.333,5)`）改写为 `(0.1, VORTEX_SPARKLE_BIAS, 0.333, 5)`，`VORTEX_SPARKLE_BIAS≈-0.30`。其它 pass 不含此特征 def，**不受影响**。原型已验证此法把 16.4%→1.0%。
2. **`dye.py` 后处理**：在 `_vortex`（dye.py:640）拿到 oC0 后，对 sparkle 过亮像素做一次"仅 Vortex"的去白（如对 `min(rgb)>阈值` 的像素回拉饱和/亮度）。比 (1) 更 hacky，不推荐。
3. **改 `noise_shaders.json[ArmorVortex].blob`**：直接 patch blob 里 `def c10` 的字面量。**不推荐**——会让 json 不再等于游戏字节，破坏"blob=游戏真值"的可验证性与 §1.1 的 IDENTICAL 断言。

> 建议：**路线 B 方案 1**，并在代码注释 + `noise_dyes_spec` 明确标注"deliberate deviation from faithful ArmorVortex (game c10.y=-0.1) to match in-game perceived sparsity"。务必命名常量（如 `_VORTEX_SPARKLE_BIAS`）便于回退到真值。

### 不要做的事
- **勿改解释器 opcode**（0x23=abs、rsq、cmp 均已逐像素核对正确）。
- **勿改 `gen_noise_shaders.py` 的 Vortex pres_inputs/取 blob 逻辑**（取对了，IDENTICAL）。
- **勿改 `assets/noise.png`**（与游戏字节相同）。
- **勿动 Nebula/Stardust/其它 pass**：它们各自 sparkle 公式不同、本就正常；本问题专属 Vortex。

---

## 7. 原型验证图

**`temp/dynamic_frames/_diag_vortex_reRE.png`**（已生成，1480×1544, scale 9）：
- 行 = 三种 base（鹿角灰白头 40×1120｜亮身 193｜彩色身 177），列 = [源｜Vortex 忠实｜Nebula 忠实｜Stardust 忠实]；每格顶红条 = 近白率(min>0.85)。
- 证明：①Vortex 三种 base 都满屏白竖条（红条长）；②Nebula 纯净无白；③Stardust 仅零星白点。即"只有 Vortex 爆白"，且为忠实字节码产物。
- （路线 B 的"调稀后" Vortex 大图未单独落盘，但 §6 表已量化 bias=-0.3 → 1.0% 近白；如需，按方案 1 改 `_VORTEX_SPARKLE_BIAS=-0.30` 后重渲即得稀疏/偏暗版。）

---

## 相关文件 (file:line)

| 文件 | 关键行 | 说明 |
|---|---|---|
| `nextbot/terraria_render/dye_noise.py` | 355 | `0x23 → np.abs`（Vortex 极坐标 abs；fx_parse 显示标"pow"但 ilen=2 实为 abs，运行正确） |
| `nextbot/terraria_render/dye_noise.py` | 271-368 | `_run_ps` ps_2_0 解释器（逐像素 = 参考解释器，忠实） |
| `nextbot/terraria_render/dye_noise.py` | 372-436 | `run_noise_pass`：preshader→consts→texld（**路线 B 方案 1 在此插 Vortex bias 覆盖**） |
| `nextbot/terraria_render/dye.py` | 640-660 | `_vortex`：`emissive=False` 硬裁剪（emissive 早已 OFF，**非白条纹来源**） |
| `nextbot/terraria_render/dye.py` | 516-519 | `_PILLAR_GAIN`：**已无 ArmorVortex**（上一轮 plan A 已落地，证伪上一轮根因） |
| `nextbot/terraria_render/data/noise_shaders.json` | `ArmorVortex` | blob 2020B，与游戏 XNB pass32 **IDENTICAL**（取对了，勿信"取错"假设） |
| `nextbot/terraria_render/data/dyes.json` | `3528` | color=(0.1,0.5,0.35) secondary=(1,1,1) sat=1（与 DyeInitializer 一致；secondary=白） |
| `temp/decomp/full/Terraria.Initializers/DyeInitializer.cs` | 134-136 | Vortex 绑定 `ArmorVortex` + UseImage Misc/noise + 上述常量（authoritative 注册） |
| `temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs` | 91-95 | `uSourceRect/uImageSize0` 来自 DrawData；body sheet=(40,1120) |
| `temp/decomp/full/Terraria/Main.cs` | 23204/23235 | dye 用 Immediate SpriteBatch + **AlphaBlend**（非 additive） |
| `temp/xnb_probe/in/PixelShader.xnb` | — | 与 Steam 安装目录字节相同（当前游戏文件） |
| `…/Steam/…/Content/Images/Misc/noise.xnb` | — | 与 `assets/noise.png` 字节相同（mean\|diff\|=0.0） |
| `temp/xnb_probe/disasm_stardust.py` | — | 现成：反汇编 Stardust/Nebula/Vortex + 交叉核对 json（IDENTICAL） |
| `temp/xnb_probe/ps_interp_full.py` | — | 参考解释器（我方 oC0 与之逐像素相同） |
| `temp/dynamic_frames/_diag_vortex_reRE.png` | — | **本轮产出**：3 base × 4 dye 对照，证"只 Vortex 爆白 + 忠实字节码" |
| `.trellis/tasks/06-03-character-accessories/research/vortex_dye_bug.md` | — | 上一轮文档（根因 emissive/gain，**本轮证伪**） |

## Caveats / Not Found

- **无游戏真机截图**：所有"我方=游戏"建立在"忠实字节码 + 字节相同 noise + 游戏一致 uSourceRect + AlphaBlend = GPU 行为"的可证链（每环已字节级/逐像素核对）。若用户"稀疏"印象来自彩色装备/游戏内小尺寸叠受光场景，则属观感差异，见 §6 路线 A。
- **radiusGate≈1 的判定**取自我方逐指令 trace（radius 均值 2.9、max 5.8）；该 radius 由 c3=(6.4,4.571)=(256/40,256/56) 决定，而 (40,56) 帧尺寸是游戏对 player armor 的真值（`ArmorShaderData.Apply` + bodyFrame），故游戏 radiusGate 同样≈1。
- **body sheet 布局副差**（非本 bug 主因）：我方 `ArmorBody_82.png` 是 360×224（9×4 网格），而游戏 vanilla body sheet 是 40×1120；这会使 grid-packed 身体的 `t0` 与游戏不同。但①爆白主角是**头**（鹿角，资源即 40×1120，与游戏同）；②`uImageSize0`(=sheet_size) 非 Vortex preshader 输入，仅 `t0` 经它变。身体 Vortex 近白本就低（Pumpkin 暗）。如要严格复现身体噪声相位，应让 body 资源/几何按 40×1120 投喂——但与"Vortex 爆白"根因正交。
- **sparkle bias=-0.30** 是经验落点（鹿角头 16.4%→1.0%）；若产品对"白的量"有精确目标，应在目标基材上扫 `c10.y∈[-0.5,-0.1]` 取定值，并固化为命名常量便于回退真值。
