# Research: 装备 glowmask 发光层 实现级规格 (head / body / legs / arm 全覆盖)

- **Query**: 逆向出装备发光层（glowmask）的实现级规格，让实现者照着写出与原版一致的发光，不需再猜。
- **Scope**: internal（逆向 decomp + 核对 repo 资产/管线，只读）
- **Date**: 2026-06-04
- **逆向源（PRIMARY，本地 v1.4.5.6, ilspycmd 9.1）**:
  - `temp/decomp/PlayerDrawSet.cs`（= `…/full/Terraria.DataStructures/PlayerDrawSet.cs`）—— `*GlowColor` / `*GlowMask` 逐 slot 赋值 (458–807)、`usesCompositeTorso` 判定 (1878)。
  - `temp/decomp/PlayerDrawLayers.cs`（= `…/full/Terraria.DataStructures/PlayerDrawLayers.cs`）—— `DrawCompositeArmorPiece` (40)、torso 复合 (1987)、非复合 body/arm glow (1948/3638)、legs glow (1556)、head glow (2173/2198/2355)、helper (164/183)。
  - `temp/decomp/full/Terraria.ID/ArmorIDs.cs:673` —— `Body.Sets.UsesNewFramingCode`（决定复合 vs 非复合）。
  - `temp/decomp/full/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs:268` —— player SpriteBatch `BlendState.AlphaBlend`（premultiplied）。
  - `temp/decomp/full/Terraria.DataStructures/DrawData.cs` —— `sb.Draw(texture, …, color, …)`（color 逐通道乘纹理）。
  - `temp/decomp/full/Terraria/Main.cs:18066-18073` —— `mouseTextColor` 脉动范围 [190,255]。
  - `temp/decomp/full/Terraria/Player.cs:53286` —— `GetImmuneAlphaPure`。
- **repo**: `nextbot/terraria_render/compositor.py`、`dye.py`、`image_io.py`、`_build/extract_assets.py`、`assets/*.png`。

---

## 一句话结论

**发光层用两套机制，但对“我们要画的全部 slot”实际只用其一**：**body/arm 发光 100% 走复合 Y+224 路径**（同一张 360×448 `ArmorBody_{slot}.png` 的下半，发光数据**已在仓库**，无需提取），而 **head / legs 发光走独立 `Glow_{id}.xnb`（40×1120 条，当前未提取 → 需给 `extract_assets.py` 加一条 `^Glow_(\d+)\.xnb$` 模式，按需提取约 30 个 id）**。混合方式是 **XNA premultiplied AlphaBlend**：发光色 `alpha=0`（占绝大多数）→ **纯叠加 additive**；`alpha>0`（少数：60/100/127/150/255）→ **部分遮挡 + 叠加**。所有发光层与基底同帧同位置、**仍套基底 dye shader**（`cBody`/`cHead`/`cLegs`）。动画发光（mouseTextColor / miscCounter 脉动）按 `dye.py` 既有“代表静帧”风格取**代表值**（mouseTextColor→222；miscCounter Remap→区间中点）。

**关键决策**：
1. **需提取的资产** = 仅 head + legs 的 `Glow_{id}.xnb`（body/arm 不需要，已在 448 高图里）。给出确切 id 清单（见 §1.3）。提取模式 `^Glow_(\d+)\.xnb$` → `Glow_{0}.png`，与现有 head/leg armor 同 40×1120 几何。
2. **动画代表相位取值**：`mouseTextColor` 取区间中点 **222**（`num=(222/255)²≈0.758`）；ChickenBones `Remap→[0.8,1.0]` 取 **0.9**；Luna `Remap→[0.85,1.0]` 取 **0.925**。备选“最亮”值见 §3。

---

## 1. 发光贴图来源（两套机制）

### 1.1 机制 A — 复合身体 Y+224（body torso + arm + shoulder）

`DrawCompositeArmorPiece` (`PlayerDrawLayers.cs:40`) 对每个子部件，在画完彩色部件后，把**同一张 `data.texture`（= ArmorBody 复合图）**复制一份、`sourceRect.Y += 224`、`color = bodyGlowColor / armGlowColor`，再 `Add` 一遍：

- Torso 子部件：`PlayerDrawLayers.cs:79-104`（`bodyGlowColor`，`value.Y += 224` at :88）。
- BackArm / FrontArm / BackShoulder / FrontShoulder 子部件：`:49-77`（`armGlowColor`，`value2.Y += 224` at :61）。
- **跳过条件**：`if (…GlowColor.PackedValue == 0) break;`（:54 / :81）——发光色为全 0（Color.Transparent）时不画。

> **核对结论（已用 `image_io.read_png` 抽查）**：仓库 `ArmorBody_{slot}.png` 对所有发光 body slot **已是 360×448**（下半即发光数据）：
> `227,208,238,239,194,179,190,176,205,237,260,175` → 全部 **360×448**。  
> 即：**body/arm 发光数据已在仓库，无需任何新提取**。（`175,208,…` 等本就在 `extract_assets.py` 的 `Armor_{slot}.xnb → ArmorBody_{slot}.png` 路径里被完整提取为 448 高。）

**哪些 body slot 走复合**：`usesCompositeTorso = Body.Sets.UsesNewFramingCode[body]`（`PlayerDrawSet.cs:1878`）。该集合 = `{1..106, 165..261}`（`ArmorIDs.cs:673`）。**所有会赋发光色的 body slot（175,176,177,179,190,194,208,227,237,238,239,260）全部 ∈ 165..261 → 全部走复合**。
→ **推论（重要）**：非复合路径里的 `GlowMask[bodyGlowMask]` / `GlowMask[armGlowMask]`（`PlayerDrawLayers.cs:1948/3638`）对这些装备**永不触发**；`bodyGlowMask`/`armGlowMask`（13,18,246,247,248,185,186,188,14,19,12,210,211,42,43,44）这些 id 是**死字段**，实现可忽略；body/arm 发光**只看复合下半 + 发光色**。

### 1.2 机制 B — 独立 GlowMask（head + legs，**当前未提取**）

- **legs**：`DrawPlayer_18_Leggings`（`PlayerDrawLayers.cs:1556-1574`）。`if legsGlowMask==-1 return;` 否则用 `TextureAssets.GlowMask[legsGlowMask]`，`sourceRect = legFrame`，pos 与腿甲同位（`legPosition + legVect`），`color=legsGlowColor`，`shader=cLegs`。slot 210（legsGlowMask 274）特殊：抖动画 2 遍（idle 取一遍即可，抖动幅度亚像素）。
- **head**：三处都用 `TextureAssets.GlowMask[headGlowMask]`，`sourceRect = bodyFrame`（**注意是 bodyFrame，不是 headFrame**，与 head armor / BackHead 同款定位），pos 与 head armor 同位（`headPosition + headVect + helmetOffset`），`color=headGlowColor`，`shader=cHead`：
  - 通用分支：`:2398`（`bodyFrame5` = bodyFrame.Height-4，见 :2314-2324）。
  - head 270：`:2173`（bodyFrame.Width+2）。
  - head 282：`:2198`（动画帧 `bodyFrame3.Y` 由 miscCounter 选，见 §3）。
  - head 271（glowMask 309，TV 屏）：`:2357-2385` 特殊 6×4 网格动画（罕见，可暂缓）。
  - head 240（glowMask 273）：`:2387-2394` 抖动 2 遍（idle 取一遍）。

> **核对结论**：仓库 `assets/` **无任何 `Glow_*.png`**；`extract_assets.py` 也**无 GlowMask 模式** → head/legs 发光资产**全未提取**，必须新增提取（见 §1.3 + §6）。客户端实际文件名经核对为 **`Content/Images/Glow_{id}.xnb`**（根目录，共 380 个；**不是** `Glow_{id}` 在 Armor/ 子目录），几何 **40×1120**（与 Armor_Head/Armor_Legs 同，20 帧条，fmt=0 RGBA）。

### 1.3 需提取的 GlowMask 资产清单（确切 id）

> 这些 id 已逐一在客户端 `Content/Images/` 用解码器确认 **存在且 fmt=0**。

**head（独立 GlowMask）**——按 head slot 出现：

| head slot | GlowMask id | 备注 |
|---|---|---|
| 169 | 15 | 静态 |
| 216 | 256 | 静态 |
| 210 | 242 | 静态 |
| 214 | 245 | Arkhalis 色 |
| 240 | 273 | 抖动 |
| 267 | 301 | 静态 |
| 268 | 302 | **动画**(mouseTextColor) |
| 269 | 304 | 静态；另注：FrontShoulder 时还画 `GlowMask[308]`+`Extra[214]`（罕见） |
| 270 | 305 | 静态，bodyFrame.Width+2 |
| 271 | 309 | TV 屏 6×4 动画（罕见，可暂缓） |
| 170 | 16 | 静态 |
| 189 | 184 | 静态 |
| 175 | 41 | 静态 |
| 193 | 209 | 静态 |
| 109 | 208 | 静态（另画 `Extra[276]`） |
| 178 | 96 | 静态 |
| 282 | 357 | **动画**(mouseTextColor) + ArmorHead 自身 9 帧动画 |
| 284 | 365 | **动画**(ChickenBones) |
| 285 | 367 | 静态 |
| 291 | 375 | 静态(全白不透明) |
| 292 | 378 | **动画**(Luna) |
| 211 | 241 | 抖动叠加（head 自身，非 headGlowMask） |

**legs（独立 GlowMask）**——按 legs slot：

| legs slot | GlowMask id | 备注 |
|---|---|---|
| 111 | 17 | 静态 |
| 157 | 249 | Arkhalis 色 |
| 158 | 250 | Arkhalis 色 |
| 210 | 274 | 抖动 2 遍 |
| 222 | 303 | **动画**(mouseTextColor) |
| 225 | 306 | 静态 |
| 226 | 307 | 静态 |
| 110 | 199 | 静态 |
| 134 | 212 | 静态 |
| 130 | 187 | 静态 |

> **body/arm 不在此表**——它们用机制 A（复合下半），无需提取。`Glow_{id}` for body/arm 是死资产。

---

## 2. 逐 slot 发光色表（静态值 / 动画代表值）

> 来源 `PlayerDrawSet.cs:517-802`。**关键常量**：`num2 = num3 = num4 = num5 = 3`（硬编码 `:511-514`，覆盖了前面的累加），所以所有 `(byte)(62.5f * (1 + num))` = `62.5 * 4` = **250**；`(byte)(127 * (1 + num5))` = `127 * 4` = **508 → byte 截断 = 252**（slot legs 130）。`ArkhalisColor = underShirtColor; A=180`（`:515-516`），即玩家衬衫色、alpha=180。
>
> 所有 4 个发光色在最后过一遍 `GetImmuneAlphaPure(color, shadow)`（`:804-807`）；我们渲染 `immuneAlpha=0, shadow=0, shimmer=0` → 乘子 = **1.0（无操作）**（`Player.cs:53286`）。

### 2.1 BODY（`bodyGlowColor`，复合下半，跳过条件 PackedValue==0）

| body slot | 发光色（RGBA） | alpha=0? | 动画 | 套装 |
|---|---|---|---|---|
| 175 | (250,250,250,0) | ✔ 叠加 | 否 | Meteor |
| 208 | ArkhalisColor=(undershirt,180) | ✘ A=180 | 否 | Arkhalis body |
| 227 | (230,230,230,60) | ✘ A=60 | 否 | Nebula（复合时抖动 2 遍，见 §5） |
| 237 | (255,255,255)·num8 | ✔ A=0 | **是**(mouseTextColor) | 见 §3 |
| 238 / 260 | (255,255,255,255) | ✘ A=255 | 否 | 全白不透明（**注意会完全遮挡**） |
| 239 | (200,200,200,150) | ✘ A=150 | 否 | |
| 190 | (250,250,250,0) | ✔ 叠加 | 否 | + `colorArmorBody` 被改写为 (250,250,250,255)（`:699`，影响基底亮度，非发光） |
| 176 | (250,250,250,0) | ✔ 叠加 | 否 | Spectre? |
| 194 | (255,255,255,127) | ✘ A=127 | 否 | |
| 177 | —（无 bodyGlowColor，仅改 colorArmorBody）| — | 否 | 无发光 |
| 179 | (255,255,255,0) | ✔ 叠加 | 否 | |

> 备注：body 237/238/260/239/227/194 这几支**只赋色、不赋 bodyGlowMask**（复合路径不需要 mask）。

### 2.2 ARM（`armGlowColor`，复合臂/肩下半，跳过条件 PackedValue==0）

| body slot | armGlowColor | alpha=0? | 动画 |
|---|---|---|---|
| 208 | ArkhalisColor=(undershirt,180) | ✘ A=180 | 否 |
| 227 | (230,230,230,60) | ✘ A=60 | 否 |
| 238 / 260 | (255,255,255,255) | ✘ A=255 | 否 |
| 239 | (200,200,200,150) | ✘ A=150 | 否 |
| 190 | (250,250,250,0) | ✔ 叠加 | 否 |
| 176 | (250,250,250,0) | ✔ 叠加 | 否 |
| 194 | (255,255,255,127) | ✘ A=127 | 否 |
| 179 | (255,255,255,0) | ✔ 叠加 | 否 |

> body 175/237 **不设 armGlowColor**（默认 Transparent，PackedValue==0 → 臂不发光）。

### 2.3 HEAD（`headGlowColor`，独立 `GlowMask[headGlowMask]`）

| head slot | GlowMask | headGlowColor（RGBA） | alpha=0? | 动画 |
|---|---|---|---|---|
| 169 | 15 | (250,250,250,0) | ✔ | 否 |
| 216 | 256 | (127,127,127,0) | ✔ | 否 |
| 210 | 242 | (127,127,127,0) | ✔ | 否 |
| 214 | 245 | ArkhalisColor=(undershirt,180) | ✘ | 否 |
| 240 | 273 | (230,230,230,60) | ✘ | 否（抖动 2 遍） |
| 267 | 301 | (230,230,230,60) | ✘ | 否 |
| 268 | 302 | (255,255,255)·num6 | ✔ | **是**(mouseTextColor) §3 |
| 269 | 304 | (200,200,200,255) | ✘ | 否 |
| 270 | 305 | (200,200,200,150) | ✘ | 否 |
| 271 | 309 | White(255,255,255,255) | ✘ | TV 6×4 动画(罕见) |
| 170 | 16 | (250,250,250,0) | ✔ | 否 |
| 189 | 184 | (250,250,250,0) | ✔ | 否 |
| 175 | 41 | (255,255,255,0) | ✔ | 否 |
| 193 | 209 | (255,255,255,127) | ✘ | 否 |
| 109 | 208 | (255,255,255,0) | ✔ | 否 |
| 178 | 96 | (255,255,255,0) | ✔ | 否 |
| 282 | 357 | (255,255,255,0)·num7 | ✔ | **是**(mouseTextColor) §3 |
| 284 | 365 | ChickenBones=(255,255,255,0)·k | ✔ | **是** §3 |
| 285 | 367 | (255,255,255,0) | ✔ | 否 |
| 291 | 375 | (255,255,255,255) | ✘ | 否（全白不透明） |
| 292 | 378 | Luna=(255,255,255,100)·k | ✘ A≈92 | **是** §3 |

### 2.4 LEGS（`legsGlowColor`，独立 `GlowMask[legsGlowMask]`）

| legs slot | GlowMask | legsGlowColor（RGBA） | alpha=0? | 动画 |
|---|---|---|---|---|
| 111 | 17 | (250,250,250,0) | ✔ | 否 |
| 157 | 249 | ArkhalisColor | ✘ A=180 | 否 |
| 158 | 250 | ArkhalisColor | ✘ A=180 | 否 |
| 210 | 274 | (230,230,230,60) | ✘ | 否（抖动 2 遍） |
| 222 | 303 | (255,255,255)·num9 | ✔ | **是**(mouseTextColor) §3 |
| 225 | 306 | (200,200,200,150) | ✘ | 否 |
| 226 | 307 | (200,200,200,150) | ✘ | 否 |
| 110 | 199 | (250,250,250,0) | ✔ | 否 |
| 134 | 212 | (255,255,255,127) | ✘ | 否 |
| 130 | 187 | (252,252,252,0) | ✔ | 否（值=127·4 截断为 252；另改 colorArmorLegs） |

---

## 3. 动画套装 —— 代表相位/代表值（按 `dye.py` 既有“代表静帧”风格）

> `dye.py` 对时间动画 pass 取 `uTime=0` 代表静帧（`dye.py:45` 注释 “Representative still: GlobalTimeWrappedHourly frozen at 0”）。发光动画同理取**代表值**。

### 3.1 `Main.mouseTextColor`-脉动（亮度 num = (mouseTextColor/255)²）

驱动 slot：head 268(302) / head 282(357) / body 237 / legs 222(303)。  
公式（如 `:553-555`）：`num = (mouseTextColor/255f)²; color = baseRGB * num`。  
`mouseTextColor` 在 **[190,255]** 三角往复（`Main.cs:18066-18073`）。**代表值取区间中点 222** → `num = (222/255)² ≈ 0.758`：
- head 268 / body 237 / legs 222（base (255,255,255,0)）→ **(193,193,193,0)**（255·0.758），叠加。
- head 282（base (255,255,255,0)）→ **(193,193,193,0)**，叠加。
- **备选“最亮”**：mouseTextColor=255 → num=1.0 → (255,255,255,0)。**最暗** 190 → num≈0.555 → (141,141,141,0)。
> 建议：与 `dye.py` 取确定相位一致，用**中点 222 → 0.758**；若想视觉更亮可用峰值 1.0。二选一，文档已给两端。

### 3.2 ChickenBones（head 284 → glowMask 365；wings/back/front 同族）

`GetChickenBonesGlowColor`（`PlayerDrawLayers.cs:164-181`，head 用 `scaleByShadow:false`）：  
`color=(255,255,255,0); num=Remap(WrappedLerp(0,1,(miscCounter%100)/100), 0,1, 0.8,1.0); color*=num`。  
`miscCounter%100/100` ∈ [0,1)，WrappedLerp 三角波 → Remap 到 **[0.8,1.0]**。**代表值取区间中点 0.9** → **(229,229,229,0)**，叠加。（备选最亮 1.0 → (255,255,255,0)。）

### 3.3 Luna（head 292 → glowMask 378）

`GetLunaGlowColor`（`:183-197`）：`color=(255,255,255,100); num=Remap(…, 0.85,1.0); color*=num`。  
Color·num 在 XNA 对 **所有 4 通道**乘（含 A）。代表 num 取 [0.85,1.0] 中点 **0.925** → RGB=236, **A=100·0.925≈92** → **(236,236,236,92)**（A>0 → 部分遮挡 + 叠加）。

### 3.4 head 282 的 ArmorHead 自身帧动画（与发光同步）

head 282（`:2177-2200`）：`num4 = miscCounter % (9*4) / 4` 选 ArmorHead 第 num4 帧（9 帧循环），**发光层 `bodyFrame3.Y=0`**（发光取第 0 帧，`:2181`）。idle 代表：ArmorHead 取 frame0、发光取 frame0 即可（发光本就固定 Y=0）。

---

## 4. 混合方式（numpy premultiplied AlphaBlend）

### 4.1 客户端真实语义

- player SpriteBatch：`BlendState.AlphaBlend`（**premultiplied alpha**，`LegacyPlayerRenderer.cs:268`）。XNA `AlphaBlend`：`out.rgb = src.rgb + dst.rgb*(1-src.a)`、`out.a = src.a + dst.a*(1-src.a)`，其中 src 已 premult。
- 纹理由 XNA content pipeline 加载为 **premultiplied**（rgb 已乘 a）。已核验：原始 `Glow_*.xnb` 像素 **rgb ≤ a 恒成立**、有软边（如 Glow_184 有 2400 软边像素）→ 确属 premult。
- `DrawData.Draw → sb.Draw(tex, …, color, …)`（`DrawData.cs`）：`color` **逐通道（含 A）straight 乘** premult 纹理。即 `finalSrc.rgb = tex.rgb_premult · (color.rgb/255)`，`finalSrc.a = tex.a · (color.a/255)`。

### 4.2 repo 资产是 STRAIGHT alpha（`extract_assets.py` 调 `unpremultiply`）

`xnb_to_png.unpremultiply`（`_build/xnb_to_png.py:94`）把 premult 还原成 straight（a>0 时 rgb=rgb·255/a；**a==0 时 rgb 置 0**，但 premult 下 a==0 本就 rgb=0，无损）。所以**所有 repo PNG（含将来提取的 Glow_*.png 与现有 ArmorBody 下半）都是 straight alpha**。

### 4.3 numpy 实现公式（实现者照抄）

设发光帧 straight：`gx_rgb`(0..255), `gx_a`(0..255)；发光色 `(cr,cg,cb,ca)`(0..255)。**先把发光帧还原成 premult-style 再乘 color**，得到 premult 的 src，然后 over：

```
# 1) 纹理 straight -> premult，并乘发光色(逐通道含 A)
ta = gx_a / 255.0                         # 纹理 alpha
src_rgb = gx_rgb * ta[...,None] * ([cr,cg,cb]/255.0)   # premult 已乘 ta
src_a   = ta * (ca / 255.0)               # 最终 src alpha
# 2) premultiplied AlphaBlend 到画布 dst(straight RGBA):
#    dst 也需按 premult 处理: 把 dst 转 premult -> 加 -> 解回 straight
da = dst_a / 255.0
out_a   = src_a + da * (1 - src_a)
out_rgb_premult = src_rgb + (dst_rgb * da[...,None]) * (1 - src_a)[...,None]
dst_rgb = out_rgb_premult / max(out_a, eps)   # premult -> straight
dst_a   = out_a * 255
```

- **`ca == 0`（绝大多数发光色）** → `src_a = 0` → `out_a = da`（alpha 不变）、`out_rgb_premult = src_rgb + dst_rgb·da` → 解回 straight = `dst_rgb + src_rgb/da`。**净效果 = 纯叠加（additive）**：发光不遮挡底下像素，只把 `gx_rgb·ta·color/255` 加到 RGB 上。
- **`ca > 0`（A=60/100/127/150/180/255）** → `src_a>0` → 既叠加又**部分遮挡**底层（标准 premult over）。A=255（body 238/260/291/271）→ 完全遮挡。

> **与现有 `compositor._over`（`compositor.py:292`，straight-over）的关系**：`_over` 是 straight-alpha over，**不能**直接用于 alpha=0 的叠加发光（straight-over 在 src_a=0 时贡献为 0，会丢掉整个发光）。**必须新增一个 premult/additive-aware 合成函数**（或对发光层用上面的 premult over）。等价的简化：对发光，直接 `dst_rgb += clip(gx_rgb·ta·color_rgb/255)`（叠加），再对 `ca>0` 部分按 `src_a` 做 over 遮挡。最稳妥是整段按 §4.3 premult over 实现（一式通吃 ca=0 与 ca>0）。

### 4.4 复合下半（body/arm）同理

复合发光帧 = `ArmorBody_{slot}.png` 的 `cell+36` 子格（下半，见 §5），其 alpha 多为 0/255 硬边（如 227 下半 272 像素、无软边），**走与 §4.3 完全相同的公式**，color=`bodyGlowColor`/`armGlowColor`。

---

## 5. 绘制位置 / 帧 / 层序 / shader

### 5.1 复合 body/arm（机制 A）

- **同帧同位置**：发光是把已构造好的子部件 `DrawData` 复制、`sourceRect.Y += 224`、换 color，**位置/rotation/origin/effect 全不变**（`PlayerDrawLayers.cs:58-103`）。
- **层序**：每个子部件**画完彩色立刻画其发光**（彩色 Add 后紧跟发光 Add）。即：BackShoulder彩+发光 → BackArm彩+发光 →（torso 之间）→ Torso彩+发光 → BackShoulder… → FrontArm彩+发光 → FrontShoulder彩+发光。对 numpy 管线 = **在 `comp.draw_armor(armor_body, <cell>, body_dye)` 这一行之后，紧接画该 cell 的发光**。
- **cell 映射（实现公式）**：compositor 复合格是 360×224 网格（9 列×4 行，FW=40/FH=56）。`_frame` 用 `cx=(cell%9)*40, cy=(cell//9)*56`（`compositor.py:208`）。下半 `cy+=224` ⟺ `cy += 4*56` ⟺ **glow_cell = base_cell + 36**（4 行×9 列）。即对 360×448 图，发光子格索引 = 现有彩色 cell + 36。各 cell 现值（`_VAR["idle_cells"]`，见 `data/variants.json`）：torso(male0/female18)、front_arm 2、back_arm 20、front_shoulder(male9/female27)、back_shoulder(male10/female28)，发光取各 +36（如 torso male 发光 cell=36、back_arm 发光 cell=56）。
- **shader**：复合彩色子部件 `item.shader = cBody`（如 `:2008` 等）；**发光子部件由 `data` 复制而来，继承同 `data.shader`（即 cBody）** → **发光仍套 body dye**。numpy：发光层与彩色层用**同一 `body_dye`** 走 `apply_dye`（src_rect 用下半子格几何）。
- **跳过**：`bodyGlowColor.PackedValue==0` / `armGlowColor.PackedValue==0` 时该子部件不画发光（§1.1）。
- **特例**：body 227（Nebula）复合发光抖动 2 遍（`:66-76,:91-101`，亚像素 ±1.25px）；body 205 复合 FrontArm 额外 4 抖动叠加 `Color(100,100,100,0)`（`:118-135`）。idle 取一遍即可，抖动可忽略（亚像素）。

### 5.2 独立 head/legs（机制 B）

- **legs**：与腿甲**同帧（legFrame）同位置（legPosition+legVect）**，紧跟腿甲彩色之后（`:1556`），`shader=cLegs` → **仍套 leg dye**。numpy：在 `comp.draw_armor(armor_legs, "col", leg_dye)` 之后画 `Glow_{legsGlowMask}.png` 同 col 帧 + 同 `leg_dye`。（注意：穿袍 `wearsRobe` 时 cLegs=cBody，腿发光也应随之用 body_dye —— 与现有 `:825` 同逻辑。）
- **head**：与 head armor **同位置（headPosition+headVect+helmetOffset）**、`sourceRect = bodyFrame`（**body 帧，非 head 帧**；与 BackHead 同款，参 `audit_frames_body_equip.md`），紧跟 head armor 彩色之后（`:2355`），`shader=cHead` → **仍套 head dye**。numpy：在 `comp.draw_armor(armor_head, "col", head_dye)`（`compositor.py:886`）之后画 `Glow_{headGlowMask}.png`，cell="col"（head 用 col 帧）+ 同 `head_dye`。
  - 帧高微调：通用分支 `bodyFrame5.Height-=4`（`:2318/2323`）—— idle 第 0 帧 (0,0,40,56)，减 4 行 → (0,0,40,52)；与 head armor 彩色帧一致（彩色也用 bodyFrame5）。实现可与 head armor 取**同一 col 帧裁剪**保持对齐。
  - head 270：bodyFrame.Width+2（`:2169`）；head 282：发光取 frame0（`bodyFrame3.Y=0`）。

### 5.3 dye shader 是否仍套：**是**

所有发光 `item.shader` 均 = 对应基底 shader（`cBody`/`cHead`/`cLegs`），逐行可见：复合继承 data.shader；legs `:1565/1572`；head `:2174/2199/2383/2399/2400`。→ numpy 发光层**复用基底的 dye_spec**（body→body_dye、head→head_dye、legs→leg_dye），src_rect 用发光帧自身几何。

---

## 6. extract_assets.py 需新增的提取模式

> 仅需 head + legs 的 `Glow_{id}`（body/arm 不需要）。文件在 `Content/Images/` **根目录**（与 Player_/Acc_ 同级），fmt=0 RGBA，40×1120。

加入现有 `PATTERNS`（`_build/extract_assets.py:51`）：

```python
(re.compile(r"^Glow_(\d+)\.xnb$"), "Glow_{0}.png"),
```

- 现有 `convert()`（`:87`）已处理 fmt==0 + `unpremultiply` + `write_png` → straight-alpha PNG，**无需改 convert**。
- 这会提取全部 380 个 `Glow_*.png`（含手持武器 glowmask 等）。**若想最小化**，可在 `convert` 调用处加一个 head/legs id 白名单（§1.3 两表，约 30 个 id）。但全提取也无害（多余 PNG 不被 compositor 引用）。
- 命名 `Glow_{id}.png` 与 compositor 读取约定一致（`_sheet(name)` 拼 `name+".png"`）。compositor 侧用 `f"Glow_{headGlowMask}"` / `f"Glow_{legsGlowMask}"` 走 `draw_armor`（col 帧）。

---

## 7. 实现计划（最小步骤）

1. **资产**：`extract_assets.py` 加 `^Glow_(\d+)\.xnb$ → Glow_{0}.png`，重跑提取 head/legs glowmask（§6）。body/arm 无需提取（已在 448 高 ArmorBody）。
2. **合成函数**：新增 premult/additive-aware 合成（§4.3），区分 ca=0（叠加）与 ca>0（部分遮挡+叠加）。建立 slot→(glowMask来源, glowColor) 查表（§2，含 `num2..5=3 → 250`、Arkhalis=undershirt/A180、mouseText→222、Chicken→0.9、Luna→0.925 代表值）。
3. **body/arm**：在每处 `comp.draw_armor(armor_body, <cell>, body_dye)` 后，若该装备 body/arm 发光色 PackedValue≠0，画 `armor_body` 的 **cell+36** 子格，tint=对应发光色，dye=body_dye，用新合成函数。torso 用 bodyGlowColor；back_arm/front_arm/back_shoulder/front_shoulder 用 armGlowColor。
4. **legs**：在 `comp.draw_armor(armor_legs,"col",leg_dye)` 后，若 legsGlowMask≠-1，画 `Glow_{legsGlowMask}` col 帧，tint=legsGlowColor，dye=（wearsRobe? body_dye: leg_dye）。
5. **head**：在 `comp.draw_armor(armor_head,"col",head_dye)` 后，若 headGlowMask≠-1，画 `Glow_{headGlowMask}` col 帧（同 head armor 裁剪/对齐），tint=headGlowColor，dye=head_dye。
6. **动画/罕见可暂缓**：head 271(309 TV 6×4)、head 269 的 GlowMask308+Extra214、抖动 2 遍（227/240/210）—— 取代表静帧/单遍即可，亚像素抖动可忽略。

---

## 8. Caveats / 不确定项

- **mouseTextColor 代表值**是设计抉择（[190,255] 往复，无确定“相位 0”，clamp 永不到 0）。本规格取**中点 222**与 dye.py “确定相位”精神一致；若实测偏暗可改峰值 255。两端值已给。
- **TV 头 head 271（glowMask 309）**：6×4 网格、依 `DrawPlayer_Head_GetTVScreen` 选列、miscCounter 选行（`:2357-2385`），逻辑复杂且罕见 → 建议暂缓或固定一个静帧（如 col 0,row 0）。
- **head 269 的 FrontShoulder 额外发光**（`GlowMask[308]` + `Extra[214]`，`PlayerDrawLayers.cs:107-116`）：依赖 FrontShoulder 复合上下文 + 头盔偏移，罕见，可暂缓。
- **`armorAdjust` X 偏移**（`PlayerDrawLayers.cs:1935/3607`）只作用于**非复合** body torso/arm；我们的发光 body 全走复合 → **不受影响**，compositor 未建模 armorAdjust 也无碍。
- **body 238/260/291/271 发光 A=255**：会**完全遮挡**对应像素（不是发光描边而是不透明覆盖层）—— 这是原版行为，实现勿误当叠加。
- **gravDir / playerEffect 翻转**：idle direction=1、gravDir=1，发光与基底同享 playerEffect，本规格按正面单帧；翻转分支（`:2316-2324` 等）在 idle 不触发。
- 未核对 `Glow_*` 中 head 357/365/378/302/303 的具体像素形状（只确认存在+fmt），但发光形状由贴图决定、与发光色/混合解耦，实现照表取色即可。
