# Research: Vortex 噪声 uv 的 Y 塌缩——竖条是 uniform 输入 bug 还是游戏忠实？

- **Query**: 质疑上一轮"竖条=忠实"的归因。Vortex 渲染出竖直白条（每列 x 固定时整列 y 方向值变化小），但用户记忆中游戏是放射状/四周到中心。深挖：噪声 uv.y 的塌缩是游戏真实行为，还是我方喂给着色器的 `uSourceRect`/`uImageSize0`/`uImageSize1` 的 **Y 尺度/偏移错了**导致的输入 bug？
- **Scope**: internal（反汇编 pass 32 preshader + 逐指令推 noise uv 公式 + 从 `temp/decomp/full/` 追游戏绘制时的 uniform 真值 + 我方 vs 游戏 uniform 逐项对比 + 双几何渲染对照）
- **Date**: 2026-06-05

## 一句话结论

**忠实，不是输入 bug。** 竖条是游戏真机就有的形态：游戏对头甲用 **完整 40×1120 纹理 + 帧 sourceRect(0, frameY, 40, 56)** 绘制（`PlayerDrawLayers.cs:328`），SpriteBatch 让 t0.y 只覆盖 `56/1120 = 5%` 的 [0,1]，于是 Vortex 着色器的极坐标域被压成 **`256/40 × 256/1120 = 6.4 × 0.229`（28:1 扁条）**，radius 几乎只随 x 变 → 噪声 uv 几乎只沿 x 变 → **竖条**。我方 `dye_noise.py` 喂的 `uSourceRect`/`uImageSize0`/`uImageSize1`(file:line 见 §3) 与游戏**逐项相同**，竖条是字节码 + 游戏真实贴图尺寸的必然产物。**着色器本身是极坐标(angle/radius)放射设计**——用户记忆的"放射"是着色器意图，但**头甲的扁窗口只截取了放射场的一条水平细缝**，所以贴在立绘上呈竖条。把它改成 2D 放射需要把 sheet 当成 40×56 帧喂（`_diag_vortex_uvfix.png` 右列），那是**对游戏的刻意偏离**，不是修 bug。

---

## 1. 噪声 uv 的精确公式（pass 32 反汇编，authoritative）

工具：`temp/xnb_probe/disasm_stardust.py`（现成，直接反汇编 + 交叉核对 json，blob 2020B `IDENTICAL=True`）。

### 1.1 preshader（10 条指令）——常量从哪来

CTAB：`uColor→c4, uSecondaryColor→c5, uTime→c6, uImage0→s0, uImage1→s1`。
**preshader 输入只有两个**：`pres_inputs = {'0':'uSourceRect', '1':'uImageSize1'}`
——即 **frame 矩形** 与 **噪声尺寸(256,256)**。**`uImageSize0`(sheet 尺寸)不是 Vortex preshader 输入**。

```
; 记 N = uImageSize1 = (256,256) ; R = uSourceRect = (sx,sy,sw,sh)
t0.x = 1/N.x = 1/256 ;  t0.y = 1/N.y = 1/256
t1.x = t0.x+t0.x = 2/256
c0.x = 1/t1.x = 128 ;  c0.y = 1/t1.y = 128            → c0 = (128,128)
c1   = t1 = 2/256 = 1/128                              → c1 = (1/128, 1/128)
c2.x = t0.x·R.x = sx/256 ;  c2.y = t0.y·R.y = sy/256   → c2 = (sx/256, sy/256)
c3.x = 1/(t0.x·R.z) = 256/sw ; c3.y = 256/sh           → c3 = (256/sw, 256/sh)
```

数值实跑（`dye_noise._run_preshader`，与游戏一致）：

| 帧 | c0 | c1 | **c2** | **c3** |
|---|---|---|---|---|
| idle (0,0,40,56) | (128,128) | (1/128,1/128) | **(0, 0)** | **(6.4, 4.571)** |
| frameY=280 | (128,128) | (1/128,1/128) | **(0, 1.094)** | (6.4, 4.571) |
| frameY=560 | (128,128) | (1/128,1/128) | **(0, 2.188)** | (6.4, 4.571) |

> 注意 **c3 用的是 frame 宽高(sw,sh)，c2 用的是 frame 偏移(sx,sy)/256**。上一轮文档把 centered 写成 `quantize(t0)·c3 - 0.5`，**漏了 `-c2` 这一项**——本轮补上（见 §1.3）。但 c2 只是把整条 band 平移，不改变 y 跨度塌缩（§2）。

### 1.2 ps_2_0 噪声 uv 段（逐指令）

```asm
mul  r0.xy, t0, c0            ; t0(sheet 归一化 uv) * 128
frc  r0.zw, r0.wzyx
add  r0.xy, r0, -r0.wzyx      ; floor(t0*128)
mov  r1.xy, c1
mad  r0.xy, r0, r1, -c2       ; ★ floor(t0*128)/128 - c2   （量化 + 减帧偏移）
mov  r1.x, c7.x              ; c7.x = -0.5
mad  r0.xy, r0, c3, r1.x      ; ★ centered = (floor(t0*128)/128 - c2)·c3 - 0.5
; …… atan2(Taylor) 由 centered.x/centered.y 得 r1.x = angle ……
mul  r0.y, r0.y, r0.y
mad  r0.x, r0.x, r0.x, r0.y
rsq  r0.x, r0.x
rcp  r0.x, r0.x              ; ★ radius = length(centered)
add  r1.y, r0.x, uTime.x     ; radius + uTime
add  r0.x, r0.x, c10.x       ; radius + 0.1
min  r1.z, r0.x, c8.w        ; radiusGate = min(radius+0.1, 1)
mul  r0.xy, r1, c10.x        ; ★ noise_uv = (angle, radius+uTime) · 0.1
texld r0, r0, uImage1        ; 采样 Misc/noise (256², bilinear WRAP)
```

### 1.3 精确 uv 公式（游戏 = 我方）

```
centered.x = (floor(t0.x·128)/128 - sx/256)·(256/sw) - 0.5      # t0 来自顶点(sheet 归一)
centered.y = (floor(t0.y·128)/128 - sy/256)·(256/sh) - 0.5
angle      = atan2(centered.y, centered.x)      # 着色器用 Taylor 展开，意图即 atan2
radius     = length(centered)
noise_uv.x = angle · 0.1
noise_uv.y = (radius + uTime) · 0.1             # uTime 只让 radius 整体平移=旋相位
noise      = sampleWRAP(Misc/noise, noise_uv).x
```

**uv.x = angle·0.1（极角），uv.y = (radius+uTime)·0.1（极半径）**。这是**极坐标放射设计**——uv.x 绕中心扫角度，uv.y 沿半径向外。**uv.y 在设计上不是"沿 1120 高纹理归一化"，而是 = 半径·0.1**；它随帧内像素行变不变，取决于 **radius 随行(py)变多少**，而 radius 由 centered.x/centered.y 决定。下面证明：在头甲几何下 radius 几乎只随 **列(px)** 变 → uv 几乎只沿 x 变 → 竖条。

---

## 2. uv.y 是否应随帧内行(0..55)变化？——取决于 centered 域的长宽比

centered 域的尺寸（忽略量化，解析式）：

```
centered.x 跨度 = (sw/W)·(256/sw) = 256/W      # W = sheet 宽
centered.y 跨度 = (sh/H)·(256/sh) = 256/H      # H = sheet 高
```

**关键：centered 域的长宽比 = sheet 的 H/W**（与 frame 尺寸无关！因为 c3 除以帧尺寸恰好抵消了 t0 里帧尺寸的分量，只剩 sheet 尺寸）。逐像素实测（`src_rect=(0,0,40,56)`, sheet=(40,1120)）：

| 量 | 跨度 | 来源 |
|---|---|---|
| centered.x（沿 40 列） | **6.25**（[-0.45, 5.8]） | 256/40 |
| centered.y（沿 56 行） | **0.214**（[-0.5, -0.286]） | 256/1120 |
| radius | [0.29, 5.82] | 几乎 = |centered.x| |

→ centered 域 = **6.4 × 0.229 ≈ 28:1 的扁条**。radius 几乎只随 centered.x（列）变，沿列(行方向)几乎不变。逐指令测噪声 uv 的变化量：

| 切片 | d(noise_uv.x) | d(noise_uv.y) |
|---|---|---|
| 沿某列向下 56 行（col 20） | **0.008** | **0.003** |
| 沿某行横跨 40 列（row 28） | **0.236** | **0.542** |

**沿列向下 noise_uv 几乎不动（0.003~0.008），沿行横跨变化 30~180 倍** → 噪声采样沿列近恒 → **竖条**。

**判定 uv.y 是否随行变**：理论上 uv.y = radius·0.1 **会**随行变（radius 含 centered.y），但因 centered.y 跨度只有 0.214（而 centered.x 跨度 6.25），radius 的行向变化被 x 向淹没 → **uv.y 实测沿行只变 0.003** → **设计上是放射(2D)，但头甲扁窗口让它退化成"沿 1120 高纹理近似归一化"的竖条**。**这不是 bug——是 40×1120 这个真实贴图尺寸 + 极坐标着色器的几何必然。**

> 上一轮"t0.y 极小→噪声 uv 主要沿 x 变"方向对，但①漏了 `-c2`；②把原因简化为"高条贴图 t0.y 小"，**真正的机制是 centered 域长宽比 = sheet 的 H/W = 1120/40 = 28:1**（c3 抵消帧尺寸后只剩 sheet 尺寸主导）。两种说法结论同为竖条，本轮给出可证伪的精确机制。

### 2.1 竖条有多"竖"（量化，纠正上一轮"整列几乎不变"的夸张）

实测头甲帧 Vortex 输出（src=(0,0,40,56), sheet=(40,1120), uTime=0.5）：

- 相邻**上下**像素亮度差均值 = **0.127**；相邻**左右**像素差均值 = **0.283** → **上下邻居相似度约为左右的 2.2 倍** → 条纹确实竖向。
- 但 per-column 亮度 std = 0.217（**非 0**）→ **不是"整列完全恒定"**，只是"竖向比横向连续 ~2×"。上一轮"整列 y 几乎不变"是**夸张**，实际是"竖向更连续"。

---

## 3. 游戏绘制时这些 uniform 的真实值（逆向链，逐环 file:line）

### 3.1 游戏头甲绘制：完整纹理 + 帧 sourceRect

`temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs:328`：
```csharp
DrawData item = new DrawData(
    TextureAssets.ArmorHead[num].Value,          // ★ 完整 40×1120 纹理
    pos + …,
    drawinfo.drawPlayer.bodyFrame,               // ★ sourceRect = 当前帧 (0, frameY, 40, 56)
    drawinfo.colorArmorHead, …);
```
腿甲同构（`PlayerDrawLayers.cs:254` 用 `legFrame` 作 sourceRect，完整腿纹理）。

### 3.2 idle 站立 = 帧 0（frameY=0）

`temp/decomp/full/Terraria/Player.cs:36204-36209`：站立不动（落地、velocity.Y==0、未用物品/游泳）的 `else` 分支 `bodyFrame.Y = 0` → **idle sourceRect = (0, 0, 40, 56)**。

### 3.3 ArmorShaderData 怎么设 uSourceRect / uImageSize0 / uImageSize1

`temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs:91-95`：
```csharp
Vector4 value2 = value.sourceRect.HasValue
    ? new Vector4(sourceRect.X, sourceRect.Y, sourceRect.Width, sourceRect.Height)  // = bodyFrame
    : new Vector4(0,0, texture.Width, texture.Height);
uSourceRect.SetValue(value2);                                       // ★ = (0, frameY, 40, 56)
uImageSize0.SetValue(new Vector2(value.texture.Width, value.texture.Height));  // ★ = (40, 1120)
```
`ArmorShaderData.cs:110`：`uImageSize1 = (_uImage.Width, _uImage.Height)` = **Misc/noise = (256,256)**（`DyeInitializer` 对 Vortex `UseImage("Images/Misc/noise")`）。

> 即游戏对**一帧头甲**传：`uSourceRect=(0, frameY, 40, 56)`、**`uImageSize0=(40,1120)`（完整 sheet，非帧）**、`uImageSize1=(256,256)`。SpriteBatch 顶点 t0.y 由 `sourceRect.Y / texture.Height` → 跨度 `56/1120`。

### 3.4 我方 `dye_noise.py` / `compositor.py` 传的值

| uniform | 我方传值 | file:line | 游戏真值 | 一致？ |
|---|---|---|---|---|
| t0 (顶点 uv) | `(sx+px+0.5)/sheet_w, (sy+py+0.5)/sheet_h` | `dye_noise.py:402-403` | SpriteBatch `sourceRect/textureSize` 同式 | ✅ |
| **uSourceRect** | `(sx,sy,sw,sh)` = `(0,0,40,56)`(idle) | `dye_noise.py:410` ← `compositor._frame_geom:346` 对 40×1120 头返回 `(0,0,40,56)` | `(0,0,40,56)`(idle frameY=0) | ✅ |
| **uImageSize0** | `(sheet_w, sheet_h)` = **(40,1120)** | `dye_noise.py:411` ← `_frame_geom:346` 返回 `(w,h)=(40,1120)` | **(40,1120)** | ✅ |
| **uImageSize1** | noise `(256,256)` | `dye_noise.py:423`（`tex1.shape`） | `(256,256)` | ✅ |
| sheet→帧判定 | 40×1120 头：`w<=FW(40)` → cell=0, `src_rect=(0,0,40,56)`, `sheet=(40,1120)` | `compositor.py:343-346` `_frame_geom` | 同（完整纹理+帧 rect） | ✅ **传的是 sheet(40×1120)，不是帧(40×56)** |

**∴ 我方喂的 `src_rect`/`sheet_size`/`uImageSize1` 与游戏逐项相同。** 任务设想的"我方把整条尺寸/坐标当帧用导致 y 塌缩"——**反了**：竖条恰恰出现在"sheet=40×1120"（=游戏真值）这一侧；若误把 sheet 当 40×56 帧喂，反而会变成 2D 放射（§4 右列），那才是偏离游戏。

> 头甲资源 `Armor_Head_276.png`（Deerclops）= **40×1120**（实测 shape (1120,40,4)）；204 个 vanilla 头 sheet 为 40×1120、80 个 40×1118——即游戏 `texture.Width/Height=(40,1120)` 是真实贴图尺寸，非我方拼图副产物。

---

## 4. 判定：竖条是忠实，不是 bug

| 证据 | 结论 |
|---|---|
| noise_uv.x=angle·0.1, uv.y=(radius+t)·0.1，**极坐标放射设计**（§1.3） | 着色器意图是放射 |
| centered 域长宽比 = sheet H/W = **1120/40 = 28:1**（§2） | 头甲窗口是放射场的扁水平细缝 |
| 游戏 `uImageSize0=(40,1120)`、`uSourceRect=帧`、t0.y 跨 56/1120（§3.1-3.3） | 游戏喂的就是这套扁几何 |
| 我方三个尺寸 uniform 与游戏逐项 IDENTICAL（§3.4） | 输入无错 |
| angle 在头甲细缝里只扫 2.53 rad 且主要沿 x；若 sheet=40×56 则扫 6.10 rad（§4.1） | 扁缝→竖条，方缝→2D 放射 |

**可证伪依据**：uv.y 公式 = (radius+t)·0.1，radius=length(centered)，centered.y 跨度 = 256/sheet_H = 256/1120 = 0.229（远小于 centered.x 跨度 256/40=6.4）。只要游戏 sheet 高 = 1120（已由 PlayerDrawLayers + ArmorHead 贴图尺寸确证），radius 就由 x 主导 → 竖条。**任何人重放 pass32 字节码 + 喂 (uSourceRect=(0,0,40,56), uImageSize0=(40,1120), uImageSize1=(256,256)) 都会得到竖条**——这就是游戏满亮下的真实形态。

### 4.1 双几何对照大图 `temp/dynamic_frames/_diag_vortex_uvfix.png`（本轮产出，scale 9）

生成器 `temp/xnb_probe/diag_vortex_uvfix.py`（只读源、只写该 png）。2×2：

- **上排**：全身 Vortex（头 Deerclops / 身 Pumpkin / 腿 MoonLord，三部位都染 Vortex，uTime=0.5）。
  - **左 = 忠实（sheet=完整 40×1120，=游戏）**：肉眼可见**竖白条**（鹿角、腿部尤甚）。
  - **右 = frame-as-sheet（sheet=40×56，偏离游戏）**：变成**2D 散斑/放射**（无竖向偏置）。
- **下排**：单独鹿角头帧 Vortex sparkle，同两种几何，放大。
  - 左竖条、右 2D，对照清晰。

> 该图**证明**：把 sheet 尺寸从游戏真值(40×1120)改成帧(40×56)，竖条→2D 放射。即"放射"确实可达，但代价是**偏离游戏喂的 uImageSize0**。所以：**竖条=忠实；2D 放射=刻意偏离（非 bug 修复）**。

数值佐证（鹿角头帧）：

| 几何 | 近白率(min(rgb)>0.85) | 竖条性（上下邻差 vs 左右邻差） |
|---|---|---|
| **忠实 sheet=40×1120**（游戏） | 14.2% | 上下 0.127 < 左右 0.283 → 竖条 |
| frame-as-sheet 40×56（偏离） | 8.9% | 上下 0.238 ≈ 左右 0.245 → 2D |

---

## 5. 与用户"放射状"印象的调和（非 bug，是观感/几何）

1. **着色器确实是放射(angle/radius 极坐标)** ——用户没记错"放射"这一着色器意图；放射在 noise 纹理空间里成立。
2. **立绘上呈竖条** ——因为头甲只有 56/1120≈5% 高的扁窗口，截取放射场的一条水平细缝（angle 在此缝里主要沿 x 变）。**这是几何投影差，不是渲染错。**
3. 若用户记忆的"四周到中间放射"来自**游戏内大尺寸/受光/或穿其它装备**的整体观感，或来自对**单帧 noise 纹理本身**的印象，那与"立绘 40×1120 扁窗口"的呈现不冲突——同一字节码、同一 uniform，只是窗口形状决定了可见形态。

---

## 6. 若要 2D 放射（产品取向；明确是"刻意偏离游戏"，非修 bug）

**不推荐当 bug 修**——会让 `uImageSize0` 偏离游戏真值。若产品就是要 2D 放射观感，最小改动（只动 Vortex、零回归）：

- **落点**：`compositor.py` 的 `_frame_geom`（:331-346）/ `_acc_strip_frame`（:350-361）对 **noise 染料**返回 `sheet_size = (sw, sh)`（帧尺寸）而非 `(w, h)`（整条）。这等价于把 centered 域从 28:1 改成 ~1.4:1（方窗口）→ angle 扫满 → 2D 放射。
- **副作用/正交性**：
  - 该改动也会改 **t0.y 范围**（→ 帧[0,1]），与游戏 SpriteBatch 不一致——这正是"偏离"代价，须命名常量 + 注释标注 "deliberate deviation: feed frame size as sheet to widen the polar window"。
  - 对 **Nebula/Stardust**：它们用同一 centered/极坐标前段，**uv 修复（方窗口）同样适用且几何上更"放射"**；但 Nebula 阈值严（noise≥0.4，18% 命中）、Stardust 是稀疏彩色星点，**竖条本就不显**，所以视觉变化小（near-white 基本仍 0%）。即此改动对 Nebula/Stardust 无害、更正确，但收益主要在 Vortex。
  - **`uImageSize1`(=256 noise) 不要动**——它是 preshader 的 c0/c1/c2/c3 推导基准，改了会破坏整个常量链。

> 一句话：**修复 = 把 sheet_size 的 y(乃至 x) 从整条改成帧**（在 `_frame_geom`/`_acc_strip_frame`，仅 noise 染料路径），即得 2D 放射（`_diag_vortex_uvfix.png` 右列）；但这是**对游戏 uImageSize0 的刻意偏离**，要不要做是产品决定，不是字节码 bug。

---

## 相关文件 (file:line)

| 文件 | 行 | 说明 |
|---|---|---|
| `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs` | 328 | 头甲 `new DrawData(ArmorHead[num].Value(完整40×1120), …, bodyFrame(帧 rect), …)` ——游戏用完整纹理+帧 sourceRect |
| 同上 | 254 | 腿甲同构（legFrame 作 sourceRect，完整腿纹理） |
| `temp/decomp/full/Terraria/Player.cs` | 36204-36209 | 站立不动 `else → bodyFrame.Y = 0`（idle = 帧0） |
| `temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs` | 91-95 | `uSourceRect = 帧 rect`，**`uImageSize0 = texture.Width/Height = (40,1120)`** |
| 同上 | 110 | `uImageSize1 = _uImage.W/H = noise (256,256)` |
| `nextbot/terraria_render/dye_noise.py` | 402-403 | 我方 t0 = `(sx+px+0.5)/sheet_w, (sy+py+0.5)/sheet_h`（与 SpriteBatch 同式） |
| 同上 | 410-411 | `uSourceRect=(sx,sy,sw,sh)`，**`uImageSize0=(sheet_w,sheet_h)=(40,1120)`** |
| 同上 | 423 | `uImageSize1 = tex1.shape = (256,256)` |
| 同上 | 372-436 | `run_noise_pass`：preshader→consts→texld（极坐标 uv 在此跑） |
| `nextbot/terraria_render/compositor.py` | 343-346 | `_frame_geom`：40×1120 头 `w<=FW` → `src_rect=(0,0,40,56)`, `sheet_size=(40,1120)`（**传 sheet 非帧**；§6 修复落点） |
| 同上 | 350-361 | `_acc_strip_frame`（条状饰品同理，§6 修复落点之二） |
| 同上 | 652-653 | 头甲 dye 调 `apply_dye(src_rect, sheet_size)`（来自 `_frame_geom`） |
| `nextbot/terraria_render/data/noise_shaders.json` | `ArmorVortex` | blob 2020B + `pres_inputs={'0':'uSourceRect','1':'uImageSize1'}`（**uImageSize0 非 preshader 输入**） |
| `nextbot/terraria_render/assets/Armor_Head_276.png` | — | Deerclops 头 = 40×1120（实测 (1120,40,4)）——游戏真实贴图尺寸 |
| `temp/xnb_probe/disasm_stardust.py` | — | 现成：反汇编 Vortex preshader+ps_2_0，blob `IDENTICAL=True` |
| `temp/xnb_probe/diag_vortex_uvfix.py` | — | **本轮产出**的生成器（只读源/只写 png） |
| `temp/dynamic_frames/_diag_vortex_uvfix.png` | — | **本轮产出**：忠实(竖条) vs frame-as-sheet(2D放射) 全身+头帧对照，scale 9 |
| `.../research/vortex_sparkle_bug.md` | — | 上一轮（归因白条=低门限+白 secondary；竖条简化为"t0.y 小"，本轮补精确机制 + 纠"整列几乎不变"夸张） |

## Caveats / Not Found

- **无游戏真机截图**：所有"我方=游戏"建立在"PlayerDrawLayers 完整纹理+帧 rect（:328）+ idle frameY=0（:36209）+ ArmorShaderData 的 uImageSize0=(40,1120)（:95）+ SpriteBatch t0=sourceRect/textureSize + 字节相同 noise/blob + AlphaBlend = GPU 行为"这一可证链（每环 file:line 已列）。SpriteBatch 顶点 UV 公式取自 FNA/XNA 标准约定（`texCoordTL=sourceRect.TopLeft/texSize`），其源不在 Terraria 反编译内（属 FNA 框架），但其结果与 §3.4 我方 t0 公式一致，且被双几何渲染图佐证。
- **body 副差（非本问题）**：我方 `ArmorBody_82.png` 是 360×224 网格而游戏 vanilla body 是 40×1120；这会让身体 Vortex 的 t0/竖条相位与游戏不同（身体 sheet 长宽比不同 → 域长宽比不同）。但①竖条主角是头（鹿角，40×1120 与游戏同）；②本问题问的是"uv.y 塌缩是否输入 bug"，结论（忠实）对头/腿（真 40×1120）成立。若要严格复现身体 Vortex 相位，应让 body 也按 40×1120 投喂——与"竖条 vs 放射"判定正交。
- **"frame-as-sheet→2D 放射"是经验对照**（`_diag_vortex_uvfix.png` 右列 + 数值表），用于证明竖条可被几何改成放射；它本身**偏离游戏**，仅作"是否为输入 bug"的反证与"若产品要放射"的修复示意，不建议当 bug 修。
