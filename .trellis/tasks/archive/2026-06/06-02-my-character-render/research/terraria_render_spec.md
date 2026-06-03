# Terraria Player Avatar Rendering Spec (base body + hair, no armor)

Authoritative spec for compositing a Terraria player avatar from TShock appearance
data. Every fact below was extracted by **decompiling the actual local game binary**
with ILSpy, cross-checked against the on-disk asset filenames. The render
architecture (`PlayerDrawLayers` / `PlayerDrawSet` data-driven layers) is identical
between 1.4.4.x and 1.4.5.x, so this applies to your 1.4.4 assets unchanged.

## Sources

- **PRIMARY (authoritative):** decompiled local binary
  `/Users/arispex/.../Terraria/Terraria.app/Contents/Resources/Terraria.exe`,
  reported version string `Main.versionNumber = "v1.4.5.6"`. Decompiled with
  `ilspycmd` 9.1 (`DOTNET_ROOT=<net9 sdk> DOTNET_ROLL_FORWARD=Major ilspycmd -t <Type>`).
  Key types read verbatim:
  - `Terraria.Graphics.Renderers.LegacyPlayerRenderer.DrawPlayer_UseNormalLayers` — master draw order
  - `Terraria.DataStructures.PlayerDrawLayers` — every `DrawPlayer_NN_*` layer method
  - `Terraria.DataStructures.PlayerDrawSet.BoringSetup` — color-field derivations
  - `Terraria.DataStructures.PlayerDrawHelper` — shader packing, display-doll skin const
  - `Terraria.ID.PlayerVariantID` (+ `.Sets`) — skinVariant table
  - `Terraria.Player` (fields, ctor, `GetHairColor`, `GetHairSettings`, `PlayerFrame`)
  - `Terraria.GameContent.TextureAssets` — `Players[,]`, `PlayerHair[]`, `PlayerHairAlt[]`
  - `Terraria.ID.PlayerTextureID` — **official layer-index name constants** (authoritative §A)
  - `Terraria.Initializers.PlayerDataInitializer` — **`Players[,]` allocation + exact fallback (`CopyVariant`) logic** (authoritative §E)
  - `Terraria.Initializers.AssetInitializer` — hair asset name pattern
  - `Terraria.Lighting.GetColorClamped`, `Terraria.Item.headSlot` default
- **On-disk assets:** `Content/Images/Player_{var}_{layer}.xnb` filenames enumerated
  directly (LZX-compressed XNB; dimensions taken from the decompiled frame rects,
  not the compressed blobs).
- The GitHub decompiled mirror (Danjoe4/Terraria-1.4.4.9) is **DMCA-blocked**
  (`gh api` returns DMCA notice) — not needed; the local binary is the real thing.
- Community cross-check: Official Terraria Wiki sprite-sheet category, The Spriters
  Resource (used only to sanity-check the 20-frame / 40x56 layout; the binary is the
  definitive source).

---

## TL;DR for a naked, standing, front/side avatar (frame 0)

Composite these in this back-to-front order. Each entry is
`Players[skinVar, LAYER]` (or hair), drawn with source-rect = the idle frame
`(0, 0, 40, 56)`, multiplied ("tinted") by the listed color at full opacity.

| # | Texture | Layer | Tint color (no lighting) | Notes |
|---|---------|-------|--------------------------|-------|
| 1 | `Player_Hair_{hair+1}` | back portion | `hairColor` | only if hair is a "back-hair" style; back frame |
| 2 | `Players[var,3]` | body/torso skin | `skinColor` | |
| 3 | `Players[var,10]` | leg skin | `skinColor` | |
| 4 | `Players[var,11]` | default pants | `pantsColor` | |
| 5 | `Players[var,12]` | default shoes | `shoeColor` | |
| 6 | `Players[var,14]` | skin long-coat | `shirtColor` | only var 3,7,8 (coat/dress) |
| 7 | `Players[var,4]` | undershirt | `underShirtColor` | |
| 8 | `Players[var,6]` | shirt/sleeves | `shirtColor` | |
| 9 | `Players[var,5]` | arm/hand skin | `skinColor` | |
| 10 | `Players[var,0]` | head/face skin | `skinColor` | |
| 11 | `Players[var,1]` | eye whites | **WHITE (untinted)** | `Color.White` |
| 12 | `Players[var,2]` | eyes/pupils | `eyeColor` | |
| 13 | `Players[var,15]` | eyelid | `skinColor` | optional blink overlay (3 sub-frames); open-eye for idle |
| 14 | `Player_Hair_{hair+1}` | front portion | `hairColor` | front frame, over the face |

All "skin" layers also carry the `skinDye` shader (`hairDye`/`skinDye` out of scope —
if both are 0, ignore shaders). Display dolls (var 10,11) ignore `skinColor` and use a
fixed skin tone (see §B / §E).

---

## A) LAYER SEMANTICS — `Player_{var}_{layer}`, layer 0..15

**Authoritative: the official `Terraria.ID.PlayerTextureID` constants** (each `const int`
is the exact layer index → name). Cross-checked against which `Players[skinVar, N]` each
draw method reads in `PlayerDrawLayers.cs`.

| Layer | `PlayerTextureID` name | Meaning | Drawn by | Tint | Source frame |
|------:|------------------------|---------|----------|------|--------------|
| 0 | **Head** | head / face skin | `DrawPlayer_21_Head_TheFace` | skinColor | `bodyFrame` |
| 1 | **EyeWhites** | eye whites / sclera | `DrawPlayer_21_Head_TheFace` | WHITE | `bodyFrame` |
| 2 | **Eyes** | pupils / irises | `DrawPlayer_21_Head_TheFace` | eyeColor | `bodyFrame` |
| 3 | **TorsoSkin** | body / torso skin | `DrawPlayer_12_Skin` | skinColor | `bodyFrame` |
| 4 | **Undershirt** | default undershirt (torso) | `DrawPlayer_17_Torso` | underShirtColor | `bodyFrame` |
| 5 | **Hands** | arm / hand skin (non-composite) | `DrawPlayer_17_Torso`; `missingHand` | skinColor | `bodyFrame` |
| 6 | **Shirt** | default shirt / sleeves (torso) | `DrawPlayer_17_Torso` | shirtColor | `bodyFrame` |
| 7 | **ArmSkin** | front-arm skin (composite) | `DrawPlayer_28_ArmOverItemComposite` | skinColor | `bodyFrame` |
| 8 | **ArmUndershirt** | front-arm undershirt (composite) | `DrawPlayer_28_ArmOverItemComposite` | underShirtColor | `bodyFrame` |
| 9 | **ArmHand** | front-arm hand (composite) | `DrawPlayer_28_ArmOverItemComposite` | skinColor | `bodyFrame` |
| 10 | **LegSkin** | leg skin | `DrawPlayer_12_Skin` | skinColor | `legFrame` |
| 11 | **Pants** | default pants | `DrawPlayer_13_Leggings` | pantsColor | `legFrame` |
| 12 | **Shoes** | default shoes | `DrawPlayer_13_Leggings` | shoeColor | `legFrame` |
| 13 | **ArmShirt** | front-arm shirt (composite) | `DrawPlayer_28_ArmOverItemComposite` | shirtColor | `bodyFrame` |
| 14 | **Extra** | skin long-coat / jacket tails | `DrawPlayer_15_SkinLongCoat` | shirtColor | `legFrame` |
| 15 | **EyeBlink** | eyelid / blink overlay | `DrawPlayer_21_Head_TheFace_Eyelid` | skinColor | `Frame(1,3,0,eyeFrame)` |

`PlayerTextureID.Count = 16` (layers 0..15).

Notes / proof:
- The **"naked / default-clothing" layers used when NO armor is equipped**, on the standard
  (non-composite) draw path, are exactly: Head **0**, EyeWhites **1**, Eyes **2**,
  TorsoSkin **3**, Undershirt **4**, Hands **5**, Shirt **6**, LegSkin **10**, Pants **11**,
  Shoes **12**, EyeBlink **15**, and (only on coat/dress variants) Extra/long-coat **14**.
- **Layers 7,8,9,13 (ArmSkin / ArmUndershirt / ArmHand / ArmShirt) are the COMPOSITE
  front-arm pieces** — drawn by `DrawPlayer_28_ArmOverItemComposite` only when
  `usesCompositeTorso` is true (certain armor sets, and the front arm rendered over a held
  item). For a plain standing naked player **the front arm is just layer 5 (Hands)**; you
  do **not** need 7/8/9/13 for a static avatar. (They still exist in the variant sets and
  serve as fallback fillers — see §E.)
- Layer **15 (EyeBlink)** is a 3-frame vertical strip (open / mid / closed eye), indexed by
  `drawPlayer.eyeHelper.EyeFrameToShow` via `val.Frame(1, 3, 0, frameY)`. For a static idle
  avatar use the **open-eye sub-frame** (frame 0 of the 3). Only drawn when
  `Players[var,15].IsLoaded`; only variant 0 ships a real texture (others inherit it by
  copy, see §E). Tinted by skin color.

---

## B) COLOR TINTING — which Player color field tints which layer

From `PlayerDrawSet.BoringSetup` (lines ~437-446, with the no-lighting variant at
~1411-1420 which is what an avatar compositor wants). Each draw multiplies the texture
by a `Color` derived from a raw `Player.*Color` field. **In flat/no-lighting mode the
derived color == the raw packed field** (see §note on lighting), so:

| Player color field | Derived `PlayerDrawSet` color | Tints layer(s) | Hair |
|--------------------|-------------------------------|----------------|------|
| `skinColor` | `colorHead` | **0** (head skin) | |
| `skinColor` | `colorBodySkin` | **3, 5** (body skin, arm skin), **15** (eyelid) | |
| `skinColor` | `colorLegs` | **10** (leg skin) | |
| *(none)* | `colorEyeWhites` = `Color.White` | **1** (eye whites — UNTINTED) | |
| `eyeColor` | `colorEyes` | **2** (pupils) | |
| `shirtColor` | `colorShirt` | **6** (shirt), **14** (skin long-coat) | |
| `underShirtColor` | `colorUnderShirt` | **4** (undershirt) | |
| `pantsColor` | `colorPants` | **11** (pants) | |
| `shoeColor` | `colorShoes` | **12** (shoes) | |
| `hairColor` | `colorHair` = `GetHairColor()` | — | **`Player_Hair_*` / `Player_HairAlt_*`** (front + back) |

Key facts:
- **Eye whites (layer 1) are always pure white** — `colorEyeWhites = Color.White`
  (no-lighting) — do NOT tint with eyeColor. Only the pupils (layer 2) get `eyeColor`.
- `colorHair = GetHairColor(useLighting:false)` = `GameShaders.Hair.GetColor(hairDye, this, White)`.
  With `hairDye == 0` this returns the raw `hairColor`. (Dye applies a pixel shader — out of scope.)
- **Multiply semantics:** `GetColorClamped(x,y,oldColor)` returns
  `new Color(lightVec3 * oldColor.ToVector3())`, i.e. world-light × field-color. In
  `Main.gameMenu` (character preview) it returns `oldColor` unchanged. For a flat avatar
  with no world, treat the tint as the **raw field color**, alpha 255.
- `GetImmuneAlpha` / `GetImmuneAlphaPure` only reduce alpha for buffs/shadow/shimmer
  (immuneAlpha, shimmerTransparency, shadow). For a normal idle player all are 0 → opaque.
- **Skin uses the `skinDyePacked` shader** on layers 0,3,5,10,15; with `skinDye == 0`
  this is a no-op identity shader — ignore it for a plain composite.
- **Display dolls (skinVariant 10, 11)** ignore `skinColor`: skin layers are tinted with
  `PlayerDrawHelper.DISPLAY_DOLL_DEFAULT_SKIN_COLOR = Color(163, 121, 92)` via
  `colorDisplayDollSkin`. (Only relevant if you ever render a doll variant.)

### Packed color note (FNA `Color`)
FNA/XNA `Color` stores RGBA in a packed `uint` as `0xAABBGGRR` (R lowest byte). The DB
"packed int" fields are these values. Decode: `R=b&0xFF, G=(b>>8)&0xFF, B=(b>>16)&0xFF,
A=(b>>24)&0xFF`. A multiply tint is per-channel `out = src*tint/255`.

### Vanilla default field values (for sanity / fallback) — `Player` ctor
```
hairColor       = (215, 90, 55)
skinColor       = (255, 125, 90)
eyeColor        = (105, 90, 75)
shirtColor      = (175, 165, 140)
underShirtColor = (160, 180, 215)
pantsColor      = (255, 230, 175)
shoeColor       = (160, 105, 60)
```

---

## C) DRAW ORDER (back-to-front) — naked, no armor, no accessories, standing

The canonical order is the literal call sequence in
`LegacyPlayerRenderer.DrawPlayer_UseNormalLayers`. All accessory/mount/buff/wing/held-item
layers are skipped here (they no-op when nothing is equipped). The surviving sequence:

```
1.  DrawPlayer_01_BackHair      -> PlayerHair[hair], hairBackFrame, tint hairColor   (only if back-hair style)
2.  DrawPlayer_12_Skin          -> Players[var,3]  body skin  (tint skinColor)
                                -> Players[var,10] leg skin   (tint skinColor)
3.  DrawPlayer_13_Leggings      -> Players[var,11] pants      (tint pantsColor)   [legs==0 path]
                                -> Players[var,12] shoes      (tint shoeColor)
4.  DrawPlayer_14_Shoes         -> (no-op when shoe accessory == 0)
5.  DrawPlayer_15_SkinLongCoat  -> Players[var,14] coat tails (tint shirtColor)   [only var 3,7,8]
6.  DrawPlayer_17_Torso         -> Players[var,4]  undershirt (tint underShirtColor)
                                -> Players[var,6]  shirt      (tint shirtColor)
                                -> Players[var,5]  arm skin   (tint skinColor)
7.  DrawPlayer_21_Head          -> Players[var,0]  head skin  (tint skinColor)     via _TheFace
                                -> Players[var,1]  eye whites (WHITE)
                                -> Players[var,2]  pupils     (tint eyeColor)
                                -> Players[var,15] eyelid     (tint skinColor)      via _TheFace_Eyelid
                                -> PlayerHair[hair] front     (tint hairColor)      hairFrontFrame  [head==-1 else-branch]
```

Within `DrawPlayer_17_Torso` (body==0 branch) the explicit order is
**undershirt(4) → shirt(6) → arm-skin(5)**.
Within `DrawPlayer_21_Head_TheFace` (head==-1) the order is
**head-skin(0) → eye-whites(1) → pupils(2) → eyelid(15)**, and the **front hair is added
after the head skin/eyes** by the `else` branch of `DrawPlayer_21_Head` (so hair overlays
the forehead but the face/eyes are already on the head texture beneath it).

**Hair vs head:** back hair is far back (step 1, behind the body); front hair is near the
very front of the head group (step 7, on top of head skin + eyes). A naked player has
`head == -1` (empty `armor[0].headSlot` defaults to -1), so:
- `GetHairSettings` sets `fullHair=false, hatHair=false, drawsBackHairWithoutHeadgear=true`.
- Back hair condition `head==-1 || fullHair || drawsBackHairWithoutHeadgear` → **true**
  (uses `PlayerHair`, not `PlayerHairAlt`), but only fires if `backHairDraw` is true for
  that hair index (long/back-flowing styles).
- Front hair is drawn by the `head <= 0` `else` branch (`PlayerDrawLayers.cs` ~2420)
  using `PlayerHair[hair]` + `hairFrontFrame`.
- `PlayerHairAlt` ("hat hair") is **only** used with certain helmets — irrelevant for a
  naked avatar.

---

## D) FRAME GEOMETRY (sprite sheet layout & offsets)

From `Player` ctor and `PlayerFrame()`:

- **Per-frame size: 40 × 56 px.** `bodyFrame.Width=40; bodyFrame.Height=56;
  legFrame.Width=40; legFrame.Height=56;` (Player ctor). Player hitbox is `width=20,
  height=42` (smaller than the sprite — the sprite is centered horizontally and the feet
  sit at hitbox bottom +4, see offset formula).
- **Sprite sheet = a single vertical column of 20 frames** (indices 0..19), stacked top to
  bottom → full texture is **40 × 1120 px**. Proof: `PlayerFrame()` assigns
  `legFrame.Y` / `bodyFrame.Y` as `Height * N` for N up to 19
  (e.g. swim frames use `*19` and `*7`). All `Player_{var}_{layer}` sheets share this layout.
- **Idle / standing pose = frame 0 (Y = 0).** When `velocity == 0`, on ground, not using an
  item, `PlayerFrame()` falls through to `legFrame.Y = 0` and `bodyFrame.Y = 0`. So the
  idle source rect is `Rectangle(0, 0, 40, 56)`. Walking cycle is frames ~6..19; use-item
  poses are frames 1..4; jump/fall is frame 5.
- **All base layers share the same origin and frame.** Body/head/eyes/shirt use
  `bodyFrame`; legs/pants/shoes/coat use `legFrame`. Both frames are identical (40×56,
  same Y for idle), and the draw origin for every base layer is the frame center
  `new Vector2(bodyFrame.Width/2, bodyFrame.Height/2)` = `(20, 28)` (`bodyVect`/`legVect`).
  So for a flat composite you can **stack all base layers at the same (0,0) top-left with no
  per-layer pixel offset** — they are pre-aligned within the 40×56 frame.
- The big position expression in every draw
  (`-bodyFrame.Width/2 + width/2 ... + height - bodyFrame.Height + 4`) only places the
  sprite relative to the world hitbox + camera; for offline compositing it cancels out and
  you just overlay frames 1:1.
- Runtime-only offsets that **do NOT apply** to a plain standing avatar (all zero for an
  idle, unequipped player): `torsoOffset`, `headPosition`/`bodyPosition`/`legPosition`
  (animation lean, 0 on frame 0), `hairOffset` (`GetHairDrawOffset`), `helmetOffset`,
  `legsOffset`, `armorAdjust`, `bodyRotation`/`legRotation`/`headRotation`. The
  composite-torso path (`usesCompositeTorso`) only triggers for certain armor sets — a
  naked player uses the **non-composite** path shown above.
- Hair frame split: when `backHairDraw` is true, `hairFrontFrame.Height` is clamped to
  **26 px** (just the forehead bit shows in front) while `hairBackFrame` keeps the full
  56 px. Otherwise both equal the full `bodyFrame` (`PlayerDrawSet` ~1743-1754).

---

## E) SKINVARIANT MAPPING (0..11) and fallback to variant 0

From `Terraria.ID.PlayerVariantID` and `.Sets`:

| skinVariant | Constant | Gender | Body shape / outfit |
|------------:|----------|--------|---------------------|
| 0 | MaleStarter      | Male   | standard male, starter clothes |
| 1 | MaleSticker      | Male   | male, "sticker" outfit |
| 2 | MaleGangster     | Male   | male, "gangster" outfit |
| 3 | MaleCoat         | Male   | male, long coat (has layer 14) |
| 4 | FemaleStarter    | Female | standard female (slim), starter clothes |
| 5 | FemaleSticker    | Female | female, sticker |
| 6 | FemaleGangster   | Female | female, gangster |
| 7 | FemaleCoat       | Female | female, long coat (has layer 14) |
| 8 | MaleDress        | Male   | male, dress (has layer 14) |
| 9 | FemaleDress      | Female | female, dress |
| 10 | MaleDisplayDoll   | Male   | mannequin / display doll (fixed skin tone) |
| 11 | FemaleDisplayDoll | Female | female display doll (fixed skin tone) |

- `Count = 12`.
- `Sets.Male = {0,1,2,3,8,10}` (used as `drawPlayer.Male`; the rest are female →
  `DrawPlayer_17_Torso` picks `FemaleBody` armor and the female default-clothes layering;
  the naked-clothes path is the same indices either way).
- `Sets.AltGenderReference` maps each variant to its opposite-gender twin
  (`{0,0,4,4,0,1,5,5,1,2,6,6,2,3,7,7,3,8,9,9,8,10,11,11,10}` as ordered pairs).
- `Sets.VariantOrderMale = {0,1,2,3,8,10}`, `VariantOrderFemale = {4,5,6,7,9,11}`.

### Per-variant on-disk layer sets (which `Player_{var}_*` files exist)
Enumerated directly from `Content/Images/` + the embedded asset-name table in the binary:

| Variant | Layers shipped as `Player_{var}_*` |
|--------:|-------------------------------------|
| 0  | 0,1,2,3,4,5,6,7,8,9,10,11,12,13,15  (master / superset; no 14) |
| 1  | 4,6,8,11,12,13 |
| 2  | 4,6,8,11,12,13 |
| 3  | 4,6,8,11,12,13,**14** |
| 4  | 3,4,5,6,7,8,9,10,11,12,13  (female base; no 0,1,2,15) |
| 5  | 4,6,8,11,12,13 |
| 6  | 4,6,8,11,12,13 |
| 7  | 4,6,8,11,12,13,**14** |
| 8  | 4,6,8,11,12,13,**14** |
| 9  | 4,6,8,11,12,13 |
| 10 | 0,2,3,5,7,9,10  (display doll) |
| 11 | 3,5,7,9,10  (display doll) |

### Fallback logic — EXACT (from `PlayerDataInitializer.Load`)
`TextureAssets.Players = new Asset<Texture2D>[PlayerVariantID.Count, PlayerTextureID.Count]`
= `[12, 16]`. The engine always indexes `Players[skinVar, N]` directly; there is **no
draw-time fallback**. Instead, slots are populated at load time by **copying a base variant
then overriding** with the variant's own pieces. The exact procedure (verbatim semantics):

`LoadVariant(ID, pieceIDs)` does `Players[ID, p] = Request("Images/Player_{ID}_{p}")` for
each `p` in `pieceIDs`. `CopyVariant(to, from)` copies all 16 slots `to <- from`. Then:

| Variant | Base copied from | Own pieces loaded (`Player_{var}_*`) |
|--------:|------------------|--------------------------------------|
| 0 MaleStarter      | — (root)            | 0,1,2,3,4,5,6,7,8,9,10,11,12,13,15;  **`[0,14] = Asset.Empty`** |
| 4 FemaleStarter    | **copy 0**          | 3,4,5,6,7,8,9,10,11,12,13 |
| 1 MaleSticker      | **copy 0**          | 4,6,8,11,12,13 |
| 2 MaleGangster     | **copy 0**          | 4,6,8,11,12,13 |
| 3 MaleCoat         | **copy 0**          | 4,6,8,11,12,13,14 |
| 8 MaleDress        | **copy 0**          | 4,6,8,11,12,13,14 |
| 5 FemaleSticker    | **copy 4**          | 4,6,8,11,12,13 |
| 6 FemaleGangster   | **copy 4**          | 4,6,8,11,12,13 |
| 7 FemaleCoat       | **copy 4**          | 4,6,8,11,12,13,14 |
| 9 FemaleDress      | **copy 4**          | 4,6,8,11,12,13 |
| 10 MaleDisplayDoll   | **copy 0**  | 0,2,3,5,7,9,10; then alias slots 1,4,6,8,11,12,13,15 → `Players[10,2]` |
| 11 FemaleDisplayDoll | **copy 10** | 3,5,7,9,10;     then alias slots 1,4,6,8,11,12,13,15 → `Players[10,2]` |

So the resolution chain is:
- **Male non-base (1,2,3,8):** own piece if loaded, else **variant 0**.
- **Female base (4):** own piece if loaded, else **variant 0** (so head/eyes/eyelid 0,1,2,15
  and arm pieces 14 come from var 0; var 4 overrides the female body shape + clothes).
- **Female non-base (5,6,7,9):** own piece if loaded, else **variant 4**, else (transitively)
  **variant 0**.
- **Display dolls (10,11):** mostly the doll's own minimal set; **all clothing/extra layers
  (1,4,6,8,11,12,13,15) are aliased to the single doll sprite `Players[10,2]`** so dolls
  render no clothes. Tinted with the fixed doll skin color (§B), not `skinColor`.
- **Variant 0 has NO layer 14** (`Asset.Empty`); only coat/dress variants 3,7,8 have a real
  layer 14.

Practical rule of thumb for an avatar compositor (replicates the above):
```
def tex(var, layer):
    if file_exists(f"Player_{var}_{layer}"): return f"Player_{var}_{layer}"
    if var in (1,2,3,8):        return tex(0, layer)          # male  -> 0
    if var == 4:                return tex(0, layer)          # female base -> 0
    if var in (5,6,7,9):        return tex(4, layer)          # female -> 4 -> 0
    if var == 10:               return tex(0, layer)          # doll (clothes blank in-game)
    if var == 11:               return tex(10, layer)
    return f"Player_0_{layer}"
```
(Display dolls 10/11 only matter if you must render a mannequin; for real players you only
ever see variants 0-9.)

---

## F) HAIR

- Two texture arrays: `TextureAssets.PlayerHair[228]` and `TextureAssets.PlayerHairAlt[228]`
  (228 hair styles each). Loaded in `AssetInitializer`:
  ```
  PlayerHair[i]    = LoadAsset("Images/Player_Hair_"    + (i + 1));
  PlayerHairAlt[i] = LoadAsset("Images/Player_HairAlt_" + (i + 1));
  ```
- **`Player.hair` is the 0-based array index used directly:** the draw code reads
  `PlayerHair[drawPlayer.hair]`. Therefore the on-disk file for a given `hair` value `H` is
  **`Player_Hair_{H + 1}.xnb`** (and the alt is `Player_HairAlt_{H + 1}.xnb`). I.e. hair
  style "1" in the UI / file `Player_Hair_1` corresponds to `Player.hair == 0`.
  (Verify the off-by-one against your DB: TShock stores whatever the game stored in
  `Player.hair`, which is the 0-based index → filename is `hair + 1`.)
- **Plain (`Player_Hair_*`) vs Alt (`Player_HairAlt_*`):**
  - `Player_Hair_*` = the normal hair (used when bare-headed, or with helmets flagged
    `fullHair`). **A naked avatar always uses `Player_Hair_*`** (both front and back).
  - `Player_HairAlt_*` = "hat hair" — a shortened/tucked variant drawn only with helmets
    flagged `hatHair`. **Not used for a naked avatar.**
- **Hair is split front/back within the same 40×56 frame.** Back hair draws early
  (`DrawPlayer_01_BackHair`, behind the body) using `hairBackFrame`; front hair draws in the
  head group (`DrawPlayer_21_Head`) using `hairFrontFrame`. Whether back hair shows is
  gated by `backHairDraw`, computed from the hair index in `GetHairSettings`:
  ```
  backHairDraw = hair>50 && (hair<56||hair>63) && (hair<74||hair>77)
               && (hair<88||hair>89) && hair!=94 && hair!=100 && hair!=104
               && hair!=112 && hair<116;
  // plus explicitly true for hair == 6,133,134,146,162
  ```
  For hair styles where `backHairDraw` is false, only the front frame is drawn (the whole
  hair fits in the head frame). For the idle frame you can simply draw the full
  `Player_Hair_{hair+1}` frame 0 over the head and (if it's a back style) also behind the
  body — but since both use the same 40×56 frame 0, for a static avatar drawing the single
  front-hair frame on top of the head is usually sufficient; add the back pass only if you
  need long-hair styles to show behind the shoulders.
- **Tint:** hair is multiplied by `colorHair = GetHairColor()`. With `hairDye == 0`,
  `colorHair == hairColor` (the packed field). 
- **`hairDye`** selects a `GameShaders.Hair` pixel shader (rainbow, etc.). It is a shader,
  not a tint — **out of scope** for a plain composite; if `hairDye != 0` the avatar would
  need that shader to be pixel-exact, otherwise treat hair as `hairColor`-tinted.

---

## G) Misc fields from the prompt

- **`hideVisuals` (bitmask):** corresponds to `Player.hideVisibleAccessory[10]` (10 vanity/
  accessory slots) — controls whether equipped accessories/armor-vanity are drawn. It does
  **not** affect the base body, skin, or hair, so it is irrelevant to a naked base + hair
  avatar. (Pet/light-pet hiding is the separate `hideMisc` BitsByte.)
- **`hairDye`** — see §F (shader, out of scope).
- A genuinely "naked" character (no armor in slots 0/1/2) has `head=-1, body=0, legs=0`,
  which selects every default-clothes path documented above. `head==-1` (not 0) because an
  empty armor `Item.headSlot` defaults to `-1`.

---

## Idle frame grid mapping

> **CORRECTION to §D.** §D claimed every base layer is addressed by a single vertical
> strip with idle = `(0,0,40,56)`. That is **only true for the head group (layers 0,1,2,
> 15) and the legs (10,11,12,14)**. It is **WRONG for the torso + arms (layers 3,4,5,6,7,
> 8,9,13)**: those textures are **360×224** (a **9-col × 4-row grid of 40×56 cells**) and
> are addressed through the **composite framing path**, which a naked player **always**
> takes. The idle torso/arm sit at grid cells that are *not* `(0,0)`, which is why the
> front arm was missing. Everything below was re-derived by decompiling the local
> `Terraria.exe` (v1.4.5.6) with ilspycmd 9.1 and verified pixel-by-pixel against the XNB
> opaque-pixel counts.

### 0. Why the texture layout split (40×1118 head vs 360×224 torso)

Decoded real dimensions (XNB header, surface=Color):

| Layer(s) | File examples | Dimensions | Addressing |
|----------|---------------|-----------:|------------|
| 0,1,2 (head/eyes), 10,11,12,14 (legs/pants/shoes/coat), 15 (eyelid) | `Player_0_0` … | **40 × 1118** | **vertical strip** — sourceRect = raw `bodyFrame`/`legFrame` = `(0, frame*56, 40, 56)` |
| 3,4,5,6,7,8,9,13 (torso + both arms) | `Player_0_3`, `Player_0_5`, `Player_0_7` … | **360 × 224** | **9×4 composite grid** — sourceRect = a remapped `(col*40, row*56, 40, 56)` |

Both are still *driven* by the same scalar `bodyFrame.Y`, but the torso/arm layers run it
through a frame→grid remap (`CreateCompositeData`, below) before drawing.

### 1. `Player.PlayerFrame()` only ever sets `bodyFrame.Y` / `legFrame.Y` — never `.X`

`Terraria/Player.cs`:
- Ctor (lines 55102-55105): `bodyFrame.Width = 40; bodyFrame.Height = 56; legFrame.Width
  = 40; legFrame.Height = 56;`. **`bodyFrame.X` and `legFrame.X` are initialized to 0 and
  are NEVER assigned anywhere in the entire assembly** (verified by grepping every
  `bodyFrame.X`/`legFrame.X` write — the only writes are `bodyFrame.X += armorAdjust` at
  draw time in `PlayerDrawLayers`, and `armorAdjust == 0` for a naked player).
- `PlayerFrame()` (lines 35321-36234) assigns `bodyFrame.Y` / `legFrame.Y` as `Height * N`
  for `N` in 0..19. **It is a pure vertical-strip frame index.** Idle (velocity == 0, on
  ground, no item, not swimming) falls through to the final `else` (lines 36204-36210)
  → `bodyFrame.Y = 0`, and the leg block's final `else` (lines 35866-35872)
  → `legFrame.Y = 0`. So at idle the *scalar* frame number is **`num = bodyFrame.Y /
  bodyFrame.Height = 0`.**

So `bodyFrame.X` is irrelevant; the 9-wide grid is **NOT** addressed via `bodyFrame.X`.
The grid `X` comes entirely from the composite remap below. (Answer to Q2: `bodyFrame.X`
is *never* nonzero; `bodyFrame.Y` is a vertical index that for the head strip can reach
`19*56=1064` (texture is 1118 tall), and for the torso is fed into the remap that caps
row at ≤3.)

### 2. A naked player ALWAYS uses the composite torso/arm path

`Terraria.DataStructures/PlayerDrawSet.CreateCompositeData()` (line 1878-1884):
```csharp
usesCompositeTorso = drawPlayer.body > 0 && drawPlayer.body < ArmorIDs.Body.Count
                     && ArmorIDs.Body.Sets.UsesNewFramingCode[drawPlayer.body];
...
if (drawPlayer.body < 1)        // body == 0  ==>  naked / default clothes
    usesCompositeTorso = true;  // <-- the naked player is FORCED composite
```
Therefore the dispatchers take the composite branch:
- `DrawPlayer_12_Skin` → `DrawPlayer_12_Skin_Composite` (PlayerDrawLayers.cs:1177)
- `DrawPlayer_17_Torso` → `DrawPlayer_17_TorsoComposite` (line 1930)
- `DrawPlayer_28_ArmOverItem` → `DrawPlayer_28_ArmOverItemComposite` (line 3602)

The **non-composite** `DrawData(..., drawinfo.drawPlayer.bodyFrame, ...)` calls in
`DrawPlayer_12_Skin` (line 1187) / `DrawPlayer_17_Torso` (lines 1968-1980) — the ones the
old spec read — are **dead code for a naked player**. (They were the 1.4.4 path; in 1.4.5
the textures were re-laid-out to the 9×4 grid and `body<1` forces composite.)

### 3. The frame→grid remap (`CreateCompositeData`, lines 1889-2011)

```csharp
int num = drawPlayer.bodyFrame.Y / drawPlayer.bodyFrame.Height;   // 0..19; idle => 0
Point pt  = new Point(1, 1);   // back  shoulder
Point pt2 = new Point(0, 1);   // front shoulder
Point pt3 = default;           // (0,0) torso
Point frameIndex  = default;   // back  arm
Point frameIndex2 = default;   // front arm
switch (num) {                 // sets frameIndex2 (front arm) + sometimes pt3 (torso row)
  case 0:  frameIndex2.X = 2;                       break;   // <-- IDLE
  case 1:  frameIndex2.X = 3;                       break;
  case 2:  frameIndex2.X = 4;                       break;
  case 3:  frameIndex2.X = 5;                       break;
  case 4:  frameIndex2.X = 6;                       break;
  case 5:  frameIndex2.X = 2; frameIndex2.Y = 1; pt3.X = 1; break;  // jump/fall
  case 6:  frameIndex2.X = 3; frameIndex2.Y = 1;    break;   // walk frames 6..19 ...
  case 7: case 8: case 9: case 10: frameIndex2.X = 4; frameIndex2.Y = 1; break;
  case 11: case 12: case 13:       frameIndex2.X = 3; frameIndex2.Y = 1; break;
  case 14:                          frameIndex2.X = 5; frameIndex2.Y = 1; break;
  case 15: case 16:                 frameIndex2.X = 6; frameIndex2.Y = 1; break;
  case 17:                          frameIndex2.X = 5; frameIndex2.Y = 1; break;
  case 18: case 19:                 frameIndex2.X = 3; frameIndex2.Y = 1; break;
}
frameIndex.X = frameIndex2.X;      // back arm column == front arm column
frameIndex.Y = frameIndex2.Y + 2;  // back arm row = front arm row + 2

UpdateCompositeArm(drawPlayer.compositeFrontArm, ..., ref frameIndex2, 7); // see §5
UpdateCompositeArm(drawPlayer.compositeBackArm,  ..., ref frameIndex,  8);

if (!drawPlayer.Male) {            // FEMALE gender shift — ONLY shoulders + torso
    pt.Y  += 2;
    pt2.Y += 2;
    pt3.Y += 2;                    // <-- the torso row +2 for female
}                                  //     (frameIndex / frameIndex2 are NOT shifted)

compBackShoulderFrame = CreateCompositeFrameRect(pt);          // Rectangle(pt.X*40, pt.Y*56,40,56)
compFrontShoulderFrame= CreateCompositeFrameRect(pt2);
compBackArmFrame      = CreateCompositeFrameRect(frameIndex);
compFrontArmFrame     = CreateCompositeFrameRect(frameIndex2);
compTorsoFrame        = CreateCompositeFrameRect(pt3);
```
and (line 2205-2208):
```csharp
private Rectangle CreateCompositeFrameRect(Point pt)
    => new Rectangle(pt.X * 40, pt.Y * 56, 40, 56);
```

**Key consequences (cell index = `row*9 + col`, matching your probe numbering):**
- The 9 grid **columns = animation pose** (idle = col 2 for the arm, col 0 for the torso),
  **rows 0/1 = male, rows 2/3 = female** (the `+2` shift), with row also encoding
  arm sub-pose for walking.
- The **gender `+2` row shift is applied ONLY to `pt`, `pt2`, `pt3`** — i.e. back
  shoulder, front shoulder, and **torso**. It is **NOT applied to `frameIndex` /
  `frameIndex2`** (the arms), so **both genders use the same arm cells** — consistent with
  `Player_0_5` and `Player_4_5` being byte-identical and both having content only in
  columns 2-8.

### 4. EXACT idle source rectangles (num = 0)

For a standing-still player (`velocity == 0`, grounded, no item, `body == 0`):

| Piece | DrawSet field | Point (col,row) — MALE | Point (col,row) — FEMALE | sourceRect MALE | sourceRect FEMALE | your cell (male/female) |
|-------|---------------|:----------------------:|:------------------------:|:----------------|:------------------|:-----------------------:|
| **Torso** (skin 3, undershirt 4, shirt 6) | `compTorsoFrame` (`pt3`) | (0, 0) | (0, 2) | **(0, 0, 40, 56)** | **(0, 112, 40, 56)** | **0 / 18** |
| **Front arm** (arm-skin 7, arm-undershirt 8, arm-shirt 13, shirt 6, + hand 9 when `missingHand`) | `compFrontArmFrame` (`frameIndex2`) | (2, 0) | (2, 0) | **(80, 0, 40, 56)** | **(80, 0, 40, 56)** | **2 / 2** |
| **Back arm** (arm-skin 7, hand 5, arm-undershirt 8, arm-shirt 13) | `compBackArmFrame` (`frameIndex`) | (2, 2) | (2, 2) | **(80, 112, 40, 56)** | **(80, 112, 40, 56)** | **20 / 20** |
| Front shoulder | `compFrontShoulderFrame` (`pt2`) | (0, 1) | (0, 3) | (0, 56, 40, 56) | (0, 168, 40, 56) | 9 / 27 |
| Back shoulder + torso undershirt/shirt 2nd pass | `compBackShoulderFrame` (`pt`) | (1, 1) | (1, 3) | (40, 56, 40, 56) | (40, 168, 40, 56) | 10 / 28 |

**At idle (num=0) all the shoulder cells (cols 0 & 1) are fully transparent** in every
torso/arm texture for both genders (verified: 0 opaque px at those cells). So the standing
pose has **no separate shoulder sprite** — the whole arm is in the column-2 arm frame, and
the whole torso is in the column-0 torso frame. **This is exactly why your probe found
columns 0 and 1 empty in the arm layers (`Player_*_5/7/8/9/13`): the engine reads col 0/1
only for the *shoulder* sub-frames, which are blank at idle.** Drawing the arm at column 2
is the fix for the missing front arm.

Pixel-verification (opaque px at the derived idle cell, via the fallback chain
`tex(var,layer)`):

| var | torso skin L3 @torso | shirt L6 @torso | front-arm skin L7 @(2,0) | front-arm hand L9 @(2,0) | back-arm skin L7 @(2,2) | back-arm hand L5 @(2,2) |
|----:|---------------------:|----------------:|-------------------------:|-------------------------:|------------------------:|------------------------:|
| 0 (male)   | 228 | 232 | 152 | 72 | 32 | 44 |
| 4 (female) | 224 | 156 | 152 | 72 | 32 | 44 |

All non-zero → the avatar renders torso + both arms + hands. (Male `undershirt` L4 @torso
is 0 px — the male default undershirt frame is empty at idle; the skin+shirt carry the
torso. Female L4 @torso = 56 px.)

### 5. Idle formulas (drop-in for the compositor)

`num` (idle) `= 0`. With `Male = skinVariant ∈ {0,1,2,3,8,10}` (else female):
```
# grid cell helpers (col,row) -> cell index = row*9 + col, sourceRect = (col*40, row*56, 40, 56)
def idle_torso_cell(skinVariant):
    male = skinVariant in (0,1,2,3,8,10)
    col, row = 0, (0 if male else 2)          # pt3, +2 row if female
    return row*9 + col                         # male -> 0 ; female -> 18

def idle_front_arm_cell(skinVariant):          # gender-independent
    return 0*9 + 2                             # always (col 2,row 0) = cell 2

def idle_back_arm_cell(skinVariant):           # gender-independent
    return 2*9 + 2                             # always (col 2,row 2) = cell 20

def idle_front_shoulder_cell(skinVariant):     # transparent at idle, draw is a no-op
    male = skinVariant in (0,1,2,3,8,10)
    return (1 if male else 3)*9 + 0            # male 9 / female 27  (blank)

def idle_back_shoulder_cell(skinVariant):      # transparent at idle, draw is a no-op
    male = skinVariant in (0,1,2,3,8,10)
    return (1 if male else 3)*9 + 1            # male 10 / female 28 (blank)
```
Equivalent source rectangles:
```
sourceRect(cell) = ( (cell % 9) * 40, (cell // 9) * 56, 40, 56 )

idle_torso_srcRect(var)     = (0,   0, 40,56) if Male else (0, 112, 40,56)
idle_front_arm_srcRect(var) = (80,  0, 40,56)                 # both genders
idle_back_arm_srcRect(var)  = (80, 112, 40,56)                # both genders
```

### 6. Composite idle draw recipe (replaces the old non-composite TL;DR)

Back-to-front, naked standing avatar. Layers tinted per §B (skin layers also carry the
`skinDyePacked` shader — a no-op when `skinDye==0`). Front/back **shoulder** draws are
included for completeness but are transparent at idle (skip them safely).

```
# BACK ARM group  (DrawPlayer_12_SkinComposite_BackArmShirt, body==0 branch, PlayerDrawLayers.cs:1379-1404)
1.  Players[var,7]  @ compBackArmFrame  (col2,row2)  tint skinColor       # back arm skin
2.  Players[var,5]  @ compBackArmFrame  (col2,row2)  tint skinColor       # back hand   (layer 5!)
3.  Players[var,8]  @ compBackArmFrame  (col2,row2)  tint underShirtColor # back arm undershirt
4.  Players[var,13] @ compBackArmFrame  (col2,row2)  tint shirtColor      # back arm shirt

# TORSO SKIN  (DrawPlayer_12_Skin_Composite, PlayerDrawLayers.cs:1280)
5.  Players[var,3]  @ compTorsoFrame    (col0,row0 male / col0,row2 female)  tint skinColor

# LEG SKIN (unchanged — vertical strip)  + pants/shoes/coat as in §C (legFrame, idle Y=0)
6.  Players[var,10] @ legFrame (0,0,40,56)  tint skinColor
    ... Players[var,11] pants, [var,12] shoes, [var,14] coat — all legFrame ...

# TORSO CLOTHES  (DrawPlayer_17_TorsoComposite, body==0 branch, PlayerDrawLayers.cs:2020-2025)
7.  Players[var,4]  @ compBackShoulderFrame (blank at idle)  tint underShirtColor
8.  Players[var,6]  @ compBackShoulderFrame (blank at idle)  tint shirtColor
9.  Players[var,4]  @ compTorsoFrame    tint underShirtColor   # main torso undershirt
10. Players[var,6]  @ compTorsoFrame    tint shirtColor        # main torso shirt

# FRONT ARM group  (DrawPlayer_28_ArmOverItemComposite, body==0 else branch, PlayerDrawLayers.cs:3792-3804)
#   shoulder sub-pass (j==num2) — blank at idle:
11. Players[var,7]  @ compFrontShoulderFrame (blank)  tint skinColor
12. Players[var,8]  @ compFrontShoulderFrame (blank)  tint underShirtColor
13. Players[var,13] @ compFrontShoulderFrame (blank)  tint shirtColor
14. Players[var,6]  @ compFrontShoulderFrame (blank)  tint shirtColor
#   arm sub-pass (j==num3) — THE VISIBLE FRONT ARM:
15. Players[var,7]  @ compFrontArmFrame  (col2,row0)  tint skinColor       # front arm skin  <-- was missing
16. Players[var,8]  @ compFrontArmFrame  (col2,row0)  tint underShirtColor # front arm undershirt
17. Players[var,13] @ compFrontArmFrame  (col2,row0)  tint shirtColor      # front arm shirt
18. Players[var,6]  @ compFrontArmFrame  (col2,row0)  tint shirtColor      # front arm shirt(default)

# HEAD group (unchanged — vertical strip, raw bodyFrame = (0,0,40,56))
19. Players[var,0] head skin, [var,1] eye whites (WHITE), [var,2] pupils, [var,15] eyelid
    + Player_Hair_{hair+1} front
```
Notes:
- The **front hand at idle is part of `Players[var,7]` (front-arm skin) and/or
  `Players[var,9]` (front-arm hand)** at `compFrontArmFrame (2,0)`. For a plain naked
  player `missingHand == false` (body 0 isn't in the `missingHand` body list,
  PlayerDrawSet.cs:369), so the `else`-branch (line 3792) draws the arm via layer 7 (which
  already includes the hand at idle — 152 px). If you also draw layer 9 (72 px hand) it
  overlays the hand cleanly; either way the hand is at cell 2.
- The **back hand is layer 5** at `compBackArmFrame (2,2)` (PlayerDrawLayers.cs:1392) — this
  is the only place layer 5 is used in the composite path, which is why your `Player_*_5`
  probe showed content in cols 2-8 (poses) and specifically at cell (2,2)=20 for the back
  arm's idle.
- `UpdateCompositeArm` (PlayerDrawSet.cs:2210-2234) only overrides the arm frames when
  `Player.compositeFrontArm.enabled` / `compositeBackArm.enabled` is true (set by code for
  held-item poses, football, etc.). For a static idle avatar both are **disabled**, so the
  `switch(num)` values stand. There is **no extra per-arm X/Y offset** for a plain idle —
  `armorAdjust == 0`, `torsoOffset == 0`, `frontShoulderOffset`/`backShoulderOffset` and
  the `Main.OffsetsPlayerHeadgear[num]` nudge are sub-pixel/zero at frame 0 and only matter
  for exact in-world alignment, not for stacking 40×56 frames 1:1.

### 7. Per-variant idle cells (all 12 skinVariants)

`Male = {0,1,2,3,8,10}`; torso row = 0 (male) or 2 (female); arm cells gender-independent.
Display dolls (10,11) blank their clothes (see §E) but the skin/arm frames still resolve to
the same cells.

| skinVariant | gender | torso cell | front-arm cell | back-arm cell |
|------------:|--------|:----------:|:--------------:|:-------------:|
| 0 Male*      | M | **0**  | **2** | **20** |
| 1 Male       | M | 0  | 2 | 20 |
| 2 Male       | M | 0  | 2 | 20 |
| 3 MaleCoat   | M | 0  | 2 | 20 |
| 4 Female*    | F | **18** | 2 | 20 |
| 5 Female     | F | 18 | 2 | 20 |
| 6 Female     | F | 18 | 2 | 20 |
| 7 FemaleCoat | F | 18 | 2 | 20 |
| 8 MaleDress  | M | 0  | 2 | 20 |
| 9 FemaleDress| F | 18 | 2 | 20 |
| 10 MaleDoll  | M | 0  | 2 | 20 |
| 11 FemaleDoll| F | 18 | 2 | 20 |

i.e. `idle_torso_cell = (Male ? 0 : 18)`, `idle_front_arm_cell = 2`,
`idle_back_arm_cell = 20`, for **all** variants.
