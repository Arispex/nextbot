# Research: Solar + 6 Reflective 染料逆向再核 (solar_reflective_revisit)

- **Query**: 重新逆向 Solar (3526) + 6 个 Reflective 染料 (3190/3026/3027/3553/3554/3555)，找离线最接近游戏的办法
- **Scope**: internal — 反汇编 `temp/xnb_probe/in/PixelShader.xnb` 的 `ArmorSolar`/`ArmorReflective`/`ArmorReflectiveColor` 三个 pass + 游戏源码 `ReflectiveArmorShaderData.cs`/`ArmorShaderData.cs`/`DyeInitializer.cs`，并跑 `dye_noise` 解释器实测
- **Date**: 2026-06-05
- **只读**：未改任何生产代码。两张原型图由 `temp/xnb_probe/_proto_solar_revisit.py` / `_proto_reflective_revisit.py` 生成（已保留可复跑）。

---

## TL;DR（最重要 —— 推翻了旧结论）

**1. Solar：旧代码注释里"忠实字节码离线偏暗（mean ~74 暗红余烬）"的说法是过时的。** 那是 **preshader 字面量 bug（commit `8398dd5` 已修）之前** 测的——当时 `c2`(uTime 亮度脉冲) / 火焰带乘子都被 CLIT table-1 读成 0，火焰带冻死。**修复后实测**（`uTime=5.0`，生产代表帧，`Armor_Head_276`）：忠实字节码 raw straight rgb **max=1.94、38.6% 像素 over-unity、mean=0.577**——bloom 是**在**的。硬裁 [0,1] 后 luma≈128（不是 74）。**忠实字节码本身就是炽热熔岩观感**（结构化橙红火焰带 + 黄色热斑），远比手写 `_solar_approx`（均匀黄金色 heat-ramp，无结构）接近游戏。
   - **推荐方案：把 Solar 从手写 `_solar_approx` 切到忠实字节码 `_solar`，并加 `emissive=True` + `gain≈1.5` 的 `_emissive_tonemap`**（复用 Nebula 同款 over-unity→白热 bloom 工具）。这是"忠实字节码 + 对 over-unity 加性项做 bloom 近似"（候选 a），最贴近游戏炽焰。原型图列 4（tonemap×1.5）最像"亮熔岩 + 白热高光"。

**2. Reflective：离线给 `uLightSource` 一个固定代表光向是可行且有据的，能从"暗哑金属"变"亮闪金属"。** 逆向 `ReflectiveArmorShaderData.cs` 确认 `uLightSource` 是一个**法向量**（从 4 点光照梯度算出、归一化）。离线 entity=null → 它被设成 `Vector3.Zero` → `dp3 N·L=0` → 高光项塌掉 → 只剩 `0.5*source` 暗金属（=用户抱怨的暗哑）。**给它一个固定正面光 `uLightSource=(0,0,1)`**（即着色器自身表面法向的 +Z 轴），高光项重新点亮：实测 luma mean 从 **58.5→175.8**，60.8% 像素变亮——清晰的金属高光。
   - **推荐方案：给 Reflective/ReflectiveColor 绑定固定 `uLightSource=(0,0,1)`**（正面光）。这是"为贴近观感的**有据**近似"（不是忠实——游戏是随光照移动的高光，离线无 entity 取不到），取值依据 = 着色器表面法向的 z 轴 / 正面观察光。原型图证明 6 个金属色（银/金/铜/黑曜/金属）都从暗哑变亮闪。

---

## 一、ArmorSolar (3526) —— 真实着色器 + 离线最佳方案

### 1.1 反汇编（`fx_parse.py`，pass `ArmorSolar`，tech=0 passidx=30，blob len 2568）

CTAB effect-set 常量：`uColor → c4`，`uImage0 → s0`。
preshader 派生常量（CLIT 24 个字面量 + FXLC 15 指令，实测见下）：
- `c0.x = 2 / uImageSize0.x`（横向 texel offset；测试 sheet 40×56 → 0.0357）
- `c1.x` = 另一个 texel 量（实测 0.05，来自 `rcp uImageSize0.y` 链；下文 §1.4）
- `c2.x = sin(frc(uTime*0.477465 + 0.5)*6.28319 - 3.14159)*0.2 + 1.0` —— **uTime 亮度脉冲**（uTime=0→1.0；uTime=5.0→1.1301）
- `c3 = uSecondaryColor`（= (1,1,0)，火焰带的"黄"目标色；preshader 把 `-uColor + uSecondary` 写进 c3.xyz... 实测 c3=(0,1,0)，即 secondary−color 的差，见 §1.4）

固定 `def`：
```
def c5, -0.05, -0.56, 0.5, 1.53846
def c6, 0, 0.333333, 1, -0.3
def c7, -0.461538, -2, 3, 0.769231
def c8, -1.3, 0, 0, 0
```

### 1.2 PS 主体逐段（火焰怎么生成）

5-tap 自采样（uImage0 在 t0 与 ±c0/±c1 偏移处采 5 次 → 自浮雕/法向）：
```
texld r0..r3 at (t0 ± c0.x in x, t0 ± c1.y in y) ; 4 邻居
texld r4 at t0                                   ; 中心
add r0.xyz, r0, -r1 ; 横向梯度 gx
add r1.xyz, -r2, r3 ; 纵向梯度 gy
... r0.x=mean(gx), r0.y=mean(gy)
r0.w = -(gx^2+gy^2)+1 ; = 1 - |grad|^2
rsq/rcp → r0.z = sqrt(1-|grad|^2) (cmp 保护负值)  ; 表面法向 z
dp3 r0.x, r0, c5      ; 法向 · 固定方向 c5=(-0.05,-0.56,0.5) —— Solar 的"光"是 HARDCODED 常量 c5（不像 Reflective 用 uLightSource）！
```
**关键差异**：Solar 的"光照方向"是**写死的 `def c5=(-0.05,-0.56,0.5)`**，不是 uLightSource。所以 Solar 的火焰高光**离线就在**（不依赖 entity），这与 Reflective 根本不同——Solar 没有"离线丢光"问题。

火焰带 / 发光生成（`r0.x` = N·c5 后）：
```
add r0.y, r0.x, c6.w(-0.3) ; bias
mul r0.y, r0.y, c5.w(1.538) ; scale
cmp r0.x = (N·L>=0) ? r0.y : c7.x(-0.4615)
mad r0.y = r0.x*c7.y(-2)+c7.z(3)
r0.x = r0.y * r0.x^2 ; 高光 lobe
r0.x = r0.x + r0.x   ; *2
r0.y = r0.x * c2.x   ; * uTime 亮度脉冲 (c2)  <- 这就是 _PILLAR_TIME 控的亮度
r0.x = r0.x*c2.x + c8.x(-1.3) ; 另一段
r0.x = r0.x + r0.x
r0.z = r0.y * -c6.w(0.3)
max r1.xyz, v0, v0.w        ; v0=white 项
lrp r2.xyz, r0.z, r1, v0    ; 在 v0(白) 与 max(v0) 之间插值（高光层）
```
火色 hue（`mad r1.xyz, r0.z, c3, uColor` 等）：
```
mov r1.xyz, uColor           ; 火焰基色 = uColor(1,0,0) 红
mad r1.xyz, r0.z, c3, r1     ; + 火焰带强度 * c3(=secondary 差,黄) -> 红->黄火焰带
lrp r3.xyz, r0.x, c6.z(1), r1 ; 热核 -> 白(1)
mul r3.w, r4.w, r0.y         ; alpha * 亮度
mul r0.xyz, r3, r3.w         ; 预乘
min r1, r0, c6.z(1)          ; 这个分支裁到 1（body 层）
mov r2.w, v0.w
mul r0, r2, r1               ; r0 = lit body（in-gamut 火焰体）
```
**加性发光（bloom）项**（最后 4 行，关键）：
```
... r1.xyz = mean(r4.rgb) * uColor * c5.z(0.5)  ; 源亮度*火色*0.5
mad r4.xyz, r4, c5.z, r1     ; r4 = source*0.5 + 源亮*火色  (发光层)
mad r0, r4, v0, r0           ; oC0 = r4*v0 + r0   <-- 加性 bloom 叠在 body 上
mov oC0, r0
```
最后一句 `mad r0, r4, v0, r0` 就是**加性发光项**：`r4*v0`（v0=white→r4 本身）**加**在已裁到 1 的 body `r0` 上。游戏里 over-unity（>1）经 additive blend / HDR → 屏幕呈亮火；离线单层 LDR 硬裁就把 >1 削平。

### 1.3 离线实测（`Armor_Head_276`，opaque px，现行 `run_noise_pass` 返回未裁 oC0）

| uTime | raw max | over-1% | clip luma | tonemap×1 luma | tonemap×1.5(估) |
|---|---|---|---|---|---|
| 0.0 | 1.94 | 37% | 128 | 135 | — |
| 2.0 | 1.94 | 37% | 125 | 132 | — |
| 5.0（生产代表帧） | 1.94 | 38.6% | 132 | 138 | 更亮带白热 |

对比手写 `_solar_approx`：luma **189**、mean RGB 0.688（**均匀黄金色，无火焰结构**）。
over-unity overflow：38% 像素的 max-channel>1（峰值 1.94），平均 overflow 0.65——**有实打实的加性 bloom 能量可被 tonemap 提亮**。

> 即：忠实字节码 mean luma(128) < 手写(189)，但"mean 更低"是因为**手写把每个像素都涂亮黄**（失真过亮），而真实 Solar 有结构化暗火槽 + 亮火带 + 黄热斑（=游戏真观感）。"mean 偏暗"≠"观感差"。

### 1.4 实测 preshader 派生常量（`_run_preshader`）

```
ArmorSolar pres_inputs = {0:uColor, 1:uSecondaryColor, 2:uTime, 3:uImageSize0}
CLIT(24) 关键字面量: [..., 0.477465(=1.5/π·... uTime 系数), 0.5, 6.283185(2π), -3.141593(-π), 0.2, 1.0]
derived @uTime=0: c0=(0.0357,0,0,0) c1=(0.05,0,0,0) c2=(1.0,..) c3=(0,1,0,0)
derived @uTime=5: c0 c1 同上, c2=(1.1301,..)  <- 仅亮度脉冲随 uTime 变
```

### 1.5 Solar 三候选 + 推荐

| 候选 | 做法 | 观感 | 类型 | 取值依据 |
|---|---|---|---|---|
| (a) **推荐** | 忠实字节码 `_solar` + `emissive=True, gain≈1.5` 的 `_emissive_tonemap` | 结构化橙红熔岩 + 白热 bloom 高光，最像 Solar Flare 火焰 | **忠实 + 有据 bloom 近似**（tonemap 是对 over-unity 加性项的 LDR 复现，与 Nebula 同款） | gain 1.5 由原型图选（×1 bloom 不足、×2 过曝洗白）；over-unity 38%/peak1.94 支撑提亮 |
| (b) | 忠实字节码 `_solar` 硬裁（现 `_solar`，默认未启用） | 忠实橙红火焰，热斑被裁平、略闷 | 忠实 | uTime=5.0 脉冲峰（`_PILLAR_TIME`） |
| (c) | 手写 `_solar_approx`（现生产默认） | 均匀黄金色，无火焰结构 | 纯近似（非逆向） | heat-ramp 手调 |

**推荐 = (a)**：把 `apply_dye` 的 `ArmorSolar` 分支从 `_solar_approx` 切到 `_solar`，并在 `_solar` 里给 `_noise_pass(..., emissive=True, gain≈1.5)`（现 `_solar` 走 `emissive=False` 硬裁）。原型图 `_diag_solar_revisit.png` 列 2(忠实裁)/列 4(tonemap×1.5) 都已明显胜过列 1(手写 approx)。`gain` 落地时应在 `Armor_Head_276` + 银盔上扫 1.2~1.8 目视定一个值（与 `_PILLAR_GAIN` 同法）。

---

## 二、ArmorReflective (3190) / ArmorReflectiveColor (5 个) —— 真实着色器 + 固定光向方案

### 2.1 `uLightSource` 在游戏里怎么算（`ReflectiveArmorShaderData.cs:29-78`）

```
if entity == null: uLightSource = Vector3.Zero        ; <- 离线/无实体即此分支（line 34）
else:
  // 取 entity 包围盒 4 个边中点的 Lighting.GetSubLight 亮度 (上/下/左/右)
  spinningpoint = (right-left 亮度差, bottom-top 亮度差)  ; 即光照梯度
  if |sp|>1: 归一化
  if direction==-1: sp.x*=-1
  sp = sp.RotatedBy(-rotation)
  value = (sp.x, sp.y, 1 - (sp.x^2+sp.y^2))            ; 法向: xy=梯度, z=朝外
  value.X *= 2; value.Y = (value.Y-0.15)*2; value.Normalize(); value.Z *= 0.6
  uLightSource = value
```
**结论**：`uLightSource` 是一个**单位化的表面法向量**（由局部光照梯度构造）。离线 `entity==null` → 强制 `Vector3.Zero` → 高光全灭。这就是"离线丢移动高光"的根因（物理上限，忠实），但**给它一个固定代表法向就能产出静态高光**。

### 2.2 PS 高光链（`ArmorReflective` blob len 1404；ReflectiveColor 1496，多一段 uColor）

CTAB：`uImage0→s0`，`uLightSource→c2`（ReflectiveColor 里 `uColor→c2, uLightSource→c3`）。
preshader 只算 texel offset：`c0=2/uImageSize0.x`，`c1` 来自 `rcp uImageSize0.y`（实测 c0=0.0357, c1=0.05）。**`uLightSource` 是 effect-set（CTAB），不经 preshader**。

```
5-tap 自采样 → 表面法向 r0.xyz（同 Solar 的浮雕，z=sqrt(1-|grad|^2)）
dp3 r0.x, r0, uLightSource   ; N·L  —— uLightSource=0 时恒 0
add r0.y = r0.x + c3.w(-0.3)
mul r0.y = r0.y * c4.x(1.538)
cmp r0.x = (N·L>=0) ? r0.y : c4.y(-0.4615)
mad r0.y = r0.x*c4.z(-2)+c4.w(3)
r0.x = r0.y*r0.x^2 ; 高光 lobe
r0.x = r0.x+r0.x   ; *2
r0.x = r0.x^2      ; 锐化
r0.x = src.a * r0.x ; 覆盖门控
mul r0.yzw = src.wzyx * c5.x(0.5) ; base = 0.5*source（暗金属体）
mad r4.xyz = r0.x*src.xyz + base  ; 高光*源 加在 0.5*源 上
mul r0, r4, v0 ; *v0(white)
```
`uLightSource=0` → `dp3=0` → `cmp` 取 −0.4615 → 高光 lobe 塌 → 只剩 `0.5*source`（=暗哑金属，用户抱怨的）。**给 N·L 一个非零方向**，高光 lobe 重新点亮。

ReflectiveColor 末段把 `0.5*source` 换成 `mean(src)*uColor*0.5`（金属色染），高光项同 Reflective。

### 2.3 固定光向实测（`Armor_Head_276`，opaque px，`ArmorReflective`）

| uLightSource | luma mean(0..255) | luma max | frac(luma>0.6) | rgb mean |
|---|---|---|---|---|
| (0,0,0) **现行离线** | 58.5 | 117.5 | 0.0% | 0.228 |
| **(0,0,1) 正面光（推荐）** | **175.8** | 255 | **60.8%** | 0.691 |
| (0,0.7,0.714) 上方光 | 142.0 | 255 | 43.9% | 0.556 |
| (-0.5,0.6,0.62) 左上光 | 130.7 | 255 | 37.4% | 0.511 |
| (0,1,0.3)norm 正上光 | 100.2 | 255 | 22.8% | 0.392 |

`(0,0,1)` 正面光把暗哑金属（mean 58）提成亮闪金属（mean 176，60% 像素亮）——这正是游戏"亮闪反光金属"的静态等价。

### 2.4 ReflectiveColor 金属色核对（`DyeInitializer.cs:86-91`，与 `dyes.json` 逐条一致 ✓）

| netId | 名称 | uColor (游戏=dyes.json) | 固定光下观感（原型图） |
|---|---|---|---|
| 3026 | Silver | (1,1,1) | 亮白银 ✓ |
| 3027 | Gold | (1.5,1.2,0.5) | 亮金（over-unity 黄绿被裁向白金）✓ |
| 3553 | Copper | (1.35,0.7,0.4) | 亮铜橙 ✓ |
| 3554 | Obsidian | (0.25,0,0.7) | 暗紫 + 紫红高光 ✓ |
| 3555 | Metal | (0.4,0.4,0.4) | 中性铬 ✓ |
| 3190 | Reflective(plain) | 无 uColor（=源色高光）| 银铬反光 ✓ |

金属色全部忠实游戏（无需改色）。`L=0` 时这些色都被压暗（金→暗棕、银→近黑），`L=(0,0,1)` 后才显出金属感——见 `_diag_reflective_revisit.png` 每行 col1(L=0) vs col2(L=front)。

### 2.5 Reflective 三候选 + 推荐

| 候选 | 做法 | 观感 | 类型 | 取值依据 |
|---|---|---|---|---|
| (a) **推荐** | 绑定固定 `uLightSource=(0,0,1)` 跑忠实字节码 | 亮闪金属 + 静态高光，最像游戏 | **有据近似**（非忠实：游戏高光随光移动，离线无 entity 取不到；固定一个法向是合理代表） | (0,0,1)=着色器表面法向 z 轴 / 正面观察光；原型图证明 6 色全部从暗哑变亮闪，且 (0,0,1) 比上方/侧光更均匀 |
| (b) | 绑定 `uLightSource=(0,0.7,0.714)` 上方光 | 亮金属 + 偏上方向性高光 | 有据近似 | 模拟游戏默认顶光；高光更有方向性但整体略暗(mean142) |
| (c) | `uLightSource=0`（现行忠实离线） | 暗哑金属（0.5*源），无高光 | 忠实（离线物理上限） | entity=null → Vector3.Zero（`ReflectiveArmorShaderData.cs:34`）|

**推荐 = (a) `uLightSource=(0,0,1)`**。落地动作（极小）：在 `dye_noise.run_noise_pass` 的 `params` dict 里加一行 `"uLightSource": np.array([0.,0.,1.,0.])`（现在没绑它 → CTAB 的 `if nm in params` 跳过 → 解释器读 0）。因为 `uLightSource` 已在 `cmap`（c2/c3），加进 params 后 `consts[reg]=params[nm]` 自动绑定（`dye_noise.py:486-488`）。Reflective/ReflectiveColor 两 pass 共用同一 params，一处改动同时生效。**注意**：这会改其它任何用 uLightSource 的 pass——实测仅这 2 个 pass 的 CTAB 含 uLightSource，安全。若想保留"忠实 c 类"，也可只在 Reflective 的 `dye.py` 包装里走一条专门绑定 `(0,0,1)` 的路径（不污染通用 run_noise_pass）。

---

## 三、落地改动清单（供主 agent 派 implement，本研究不改码）

1. **Solar (`dye.py`)**：
   - `apply_dye` 第 ~1201 行 `if name=="ArmorSolar": return _solar_approx(...)` → 改为 `return _solar(arr_u8, ..., **geom)`。
   - `_solar`（`dye.py:598-624`）的 `_noise_pass(...)` 加 `emissive=True, gain=<扫定,≈1.5>`（现 `emissive=False`）。可在 `_PILLAR_GAIN` 加 `"ArmorSolar": 1.5`。
   - uTime 代表帧沿用 `_PILLAR_TIME["ArmorSolar"]=5.0`（已有）。
   - 回归：`test_solar_bytecode_runs_but_default_stays_handwritten`（`tests/test_terraria_render.py:1065`）会失败（它断言默认=手写、且 bc 比手写暗）——**需重写该测试**为"默认=忠实 tonemap 字节码"，断言改为 over-unity bloom 已提亮（mean 上升）+ 火焰红 hue（br>bg>bb）。
2. **Reflective (`dye_noise.py` 或 `dye.py`)**：
   - 方案 A（全局）：`run_noise_pass` 的 `params` 加 `"uLightSource": np.array([0.,0.,1.,0.])`（`dye_noise.py:451-460` 区）。
   - 方案 B（隔离，更稳）：仅 `_reflective`/`_reflective_color` 走绑定 `(0,0,1)` 的专路，不动通用 run_noise_pass。
   - 回归：现有 Reflective 测试（`tests/test_terraria_render.py:928-933` 附近 + class-C 断言）期望"无高光/passthrough"——**需更新**为"固定光下有静态高光"（luma 显著高于 L=0）。
3. **原型对比图**（已生成，供目视核对游戏截图）：
   - `temp/dynamic_frames/_diag_solar_revisit.png`（6 列：源/手写/字节码裁/tonemap×1/×1.5/×2，2 行 head）
   - `temp/dynamic_frames/_diag_reflective_revisit.png`（4 列：源/L=0/L=front/L=top × 6 行：plain+5 金属色）

---

## 四、每个结论的逆向 file:line 索引

- **Solar 着色器**：反汇编 `temp/xnb_probe/in/PixelShader.xnb` pass `ArmorSolar`（tech0 pidx30，blob@60290 len2568）。加性 bloom 项 = PS 末 `mad r0, r4, v0, r0`；火色 = `mov r1.xyz,uColor` / `mad r1.xyz,r0.z,c3,r1`；亮度脉冲 c2 = preshader（CLIT 字面量 0.477465/0.2/1.0 + `sin/frc` 链）。Solar 的"光向"是写死 `def c5=(-0.05,-0.56,0.5)`（非 uLightSource）。
- **Solar 离线数据**：`uTime=5` raw max=1.94 / over-1%=38.6% / clip luma=128 / tonemap×1 luma=138；手写 approx luma=189（实测，本研究跑 `_proto_solar_revisit.py` 同款链路）。
- **preshader 字面量 bug 修复**：commit `8398dd5`（"修复 preshader 字面量 bug"）= dye_noise.py `get()` 的 `table in (0,1)` + 字面量广播注释（`dye_noise.py:282-298`）。这是旧"mean74"过时的原因。
- **Reflective uLightSource 来源**：`temp/decomp/full/Terraria.GameContent.Dyes/ReflectiveArmorShaderData.cs:29-78`（entity==null→Zero 在 line 34；法向构造 line 57-75）。
- **Reflective PS 高光链**：反汇编 pass `ArmorReflective`（blob@62882 len1404）`dp3 r0.x,r0,uLightSource` + `cmp` lobe；base=`0.5*source`（`mul r0.yzw,r4.wzyx,c5.x`）。ReflectiveColor（blob@75426 len1496）末段 `mul r0.yzw, r0.y, uColor.wzyx` 染金属色。
- **Reflective 固定光实测**：L=(0,0,1)→luma mean 175.8（vs L=0 的 58.5），本研究跑 `_proto_reflective_revisit.py`。
- **uLightSource 绑定缺口**：`dye_noise.py:483-488`（`for nm,(reg,_) in cmap.items(): if nm in params: consts[reg]=params[nm]`）——uLightSource 不在 params 即读 0（`_Z` 默认零，`dye_noise.py:334-337`）。
- **颜色真值**：`DyeInitializer.cs:86-91`（Reflective 5 色）/ `:130`（Solar uColor(1,0,0) secondary(1,1,0)），与 `dyes.json` 逐条一致。
- **现行 dispatch / 包装**：`dye.py` Solar=`598-624`+`1201-1204`，Reflective=`813-844`+`1209-1212`；`_emissive_tonemap`=`736-751`；`_noise_pass(emissive,gain)`=`755-787`；`_PILLAR_TIME`/`_PILLAR_GAIN`=`708-733`。
- **现行测试**：`tests/test_terraria_render.py:1065`（solar bytecode 断言"默认=手写且更暗"）、`:928-933`（Reflective class-C 列表）。
- 既有研究对齐：`research/dye_bytecode_audit.md`（§Solar/§C类，本研究修正其"mean74 暗红"过时结论）、`research/vortex_dye_bug.md`（emissive tonemap vs 硬裁的判据先例）、`research/midnight_rainbow.md`（offset-tap / preshader 修复史）。

---

## Caveats / 未尽事项

- **gain 未定死值**：Solar tonemap 的 `gain≈1.5` 由原型图目视选（×1 偏闷、×2 过曝），落地应在多个 cell（head276/银盔/body）扫 1.2~1.8 与游戏截图比对定值，方法同 `_PILLAR_GAIN` 现有项。
- **固定光是"有据近似"非忠实**：Reflective 的 `(0,0,1)` 是为复现"亮金属"观感选的代表法向，**不是**游戏的真实随光移动高光（那离线无 entity 物理取不到）。文档/代码注释须标注"static representative light, not faithful moving specular"。
- **未做逐像素 game-diff**：本研究比对的是"忠实字节码 vs 手写近似 vs 游戏观感描述"，未对真实游戏截图做逐像素 diff（无截图素材）。落地后建议截游戏 Solar/Reflective 与原型图三方目视对齐。
- **`(0,0,1)` 对极端法向的边角**：正面光下，朝向相机的平面高光最强；源里近乎纯平/无浮雕的区域 N≈(0,0,1) → N·L≈1 → 高光最亮可能略洗白（银色尤甚）。若洗白过头，退 `(0,0.7,0.714)` 上方光（mean142，更克制）。
- **测试需重写**：切 Solar/Reflective 会破现有"默认=近似/无高光"的 pin 测试，属预期；implement 时同步更新断言（已在 §三 列出）。
