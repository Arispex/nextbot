# Terraria Player Accessory Rendering Spec (12 categories + beard)

Reverse-engineered spec for compositing **all** Terraria player accessory categories
onto the existing numpy idle compositor. Builds on
`archive/2026-06/06-02-my-character-render/research/terraria_render_spec.md` (base body +
idle 40×56 frame + composite arm cells), `dye_passes_spec.md`, `noise_dyes_spec.md`.

## Sources (all PRIMARY, decompiled local binary v1.4.5.6, ilspycmd 9.1)

- `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs` — every `DrawPlayer_NN`
  accessory draw method (verbatim).
- `temp/decomp/full/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs:162-` —
  `DrawPlayer_UseNormalLayers` master back-to-front call order.
- `temp/decomp/full/Terraria/Player.cs` — `UpdateVisibleAccessories` (36340),
  `UpdateVisibleAccessory` (36457), `UpdateDyes`/`UpdateItemDye` (9300-9445),
  `GetFaceDrawOffset`/`GetBeardDrawOffset`/`GetFrontDrawOffset`/`GetShoeDrawOffset`
  (4365/4634/4708/4729), `ShouldDrawWingsThatAreAlwaysAnimated` (30287),
  `Directions` (3703), wing-frame update (26876-27097), `armor[]`/`dye[]` arrays (1388/1390),
  `hideVisibleAccessory[10]` (1606), `IsItemSlotUnlockedAndUsable`.
- `temp/decomp/full/Terraria.DataStructures/PlayerDrawSet.cs` — `colorArmorBody/Legs/Head`
  derivation (447-449, gameMenu/fullbright override 920-922), `c*` shader copy (289-313),
  `bodyVect/legVect/headVect` (1717-1719), `usesCompositeFront/BackHandAcc` (1879-1880),
  composite frame setup.
- `temp/decomp/full/Terraria.ID/ArmorIDs.cs` — per-category `.Count` + `.Sets` flags.
- `temp/decomp/full/Terraria.GameContent/TextureAssets.cs:134-172` — accessory texture arrays.
- `temp/decomp/full/Terraria.Initializers/AssetInitializer.cs:449-535` — on-disk file name patterns.
- `temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs:79-99` — dye `Apply()` reads
  `uSourceRect`/`uImageSize0` from each DrawData (per-accessory frame + texture size).
- `temp/decomp/full/Terraria/Main.cs:456-502` — `OffsetsPlayerOffhand/Onhand` tables.
- **On-disk dimensions** probed directly from
  `…/Terraria.app/Contents/Resources/Content/Images/*.xnb` via
  `temp/xnb_probe/xnb_to_png.decode_texture` (counts + W×H below are measured, not guessed).

---

## TL;DR

**Draw order (back→front), interleaved with body** — the `DrawPlayer_NN` number IS the order:

```
09 Wings        (BEHIND body)          wings
10 BackAcc      (BEHIND body)          back        ← capes, quivers, packs (non-backpack/tail)
11 Balloons     (BEHIND body)          balloon
── body back-arm, torso skin, legs, torso clothes ──
14 Shoes        (with leggings)        shoe
18 OffhandAcc   (IN FRONT, w/ torso)   handoff     ← non-composite path only (rarely hit)
19 WaistAcc     (IN FRONT)             waist
20 NeckAcc      (IN FRONT)             neck
21 Head group   head/eyes/hair/BEARD   beard       ← beard drawn inside head group
22 FaceAcc      (IN FRONT, over head)  face
── 28 front-arm composite (handon/handoff composite acc ride here) ──
25 Shield       (IN FRONT, after 32-back)  shield
29 OnhandAcc    (IN FRONT, w/ front arm)   handon  ← non-composite path only (rarely hit)
32 FrontAcc     front+back split        front      ← scarves/capes worn on front
```

- **Vanity override rule (one line):** drawn-visual is resolved by iterating functional
  accessories `armor[3..9]` then social/vanity `armor[13..19]`; **last non-air write wins per
  category**, so a vanity item in slot 13+i overrides the functional item in slot 3+i (a
  hidden functional acc with `hideVisibleAccessory[i]` is skipped, except wings which still
  show when airborne).
- **hideVisuals mapping (one line):** `appearance.hideVisuals` IS `Player.hideVisibleAccessory[10]`;
  bit *k* (k=0..9) = "hide the visual of accessory slot *k*" = inventory slot 59+k (functional
  acc 0..6 are k=3..9, the 3 first bits k=0/1/2 are head/body/legs vanity and never apply to
  accessory draw). If bit k set, `armor[3+k]`'s visual is dropped (but its vanity twin
  `armor[13+k]` still draws).
- **Dye:** every accessory draw carries `item.shader = drawinfo.c<Cat>` which IS a
  `GameShaders.Armor` index (same family as body armor). `dye.apply_dye` works unchanged — just
  pass the accessory's **own** `src_rect` (its idle frame rect) and `sheet_size` (its full
  texture W×H), because `ArmorShaderData.Apply` derives `uSourceRect`/`uImageSize0` from the
  DrawData itself.
- **Texture extraction list (add to `_build/extract_assets.py`):**

  | prefix | dir | output | count | layout |
  |---|---|---|---:|---|
  | `Wings_{w}` | `Images/` | `Wings_{w}.png` | 51 (w=1..51) | vertical N-frame strip, var W×H |
  | `Acc_Back_{s}` | `Images/` | `Acc_Back_{s}.png` | 39 (s=1..39) | 40×1120 (20-frame strip) |
  | `Acc_Front_{s}` | `Images/` | `Acc_Front_{s}.png` | 16 (s=1..16) | 40×1120 |
  | `Acc_Shoes_{s}` | `Images/` | `Acc_Shoes_{s}.png` | 30 (s=1..30) | 40×1120 |
  | `Acc_Waist_{s}` | `Images/` | `Acc_Waist_{s}.png` | 16 (s=1..16) | 40×1120 |
  | `Acc_Neck_{s}` | `Images/` | `Acc_Neck_{s}.png` | 12 (s=1..12) | 40×1120 |
  | `Acc_Face_{s}` | `Images/` | `Acc_Face_{s}.png` | 23 (s=1..23) | 40×1120 |
  | `Acc_Shield_{s}` | `Images/` | `Acc_Shield_{s}.png` | 9 (s=1..9) | 40×1120 (some 44 wide) |
  | `Acc_Beard_{s}` | `Images/` | `Acc_Beard_{s}.png` | 4 (s=1..4) | 40×1120 |
  | `Acc_Balloon_{s}` | `Images/` | `Acc_Balloon_{s}.png` | 19 (s=1..19) | 52×224 (4-frame); #18=40×1120 |
  | `Acc_HandsOn_{s}` (composite) | `Images/Accessories/` | `AccHandsOnComposite_{s}.png` | 21 (s=1..24, gaps) | 360×224 (9×4 grid) |
  | `Acc_HandsOff_{s}` (composite) | `Images/Accessories/` | `AccHandsOffComposite_{s}.png` | 13 (s=1..15) | 360×224 (9×4 grid) |
  | *(optional)* `Acc_HandsOn_{s}` / `Acc_HandsOff_{s}` | `Images/` (root) | `AccHandsOn_{s}.png` / `AccHandsOff_{s}.png` | 21 / 13 | 40×1120 — **rarely used, see §HandAcc** |

  (counts measured on disk; numbering matches `*Slot` IDs, slot 0 unused.)

---

## Cross-cutting logic (the part you cannot guess)

### X1. Vanity / social override — which item's visual wins per slot

`Player.armor` is `Item[20]` (`Player.cs:1388`). Layout:

| index | meaning |
|---|---|
| 0,1,2 | functional head / body / legs armor |
| 3..9 | **functional accessories 0..6** (slot 8 needs expert, slot 9 needs master) |
| 10,11,12 | **vanity** head / body / legs |
| 13..19 | **social/vanity accessories 0..6** (mirror of 3..9) |

`dye` is `Item[10]` (`Player.cs:1390`): `dye[0..2]` = head/body/legs armor dye,
`dye[3..9]` = the 7 accessory dyes (shared by functional acc *k* and its vanity twin).

**Resolution = `Player.UpdateVisibleAccessories` (`Player.cs:36340-36421`).** It writes the
per-category drawn fields (`back, front, wings, neck, shield, handon, handoff, waist, balloon,
balloonFront, face, faceMask, faceFlower, faceHead, beard, shoe, backpack, tail`) by calling
`UpdateVisibleAccessory(i, armor[i])` (36457) in two passes:

```csharp
for (int i = 3; i < 10; i++) {              // FUNCTIONAL accessories
    if (!IsItemSlotUnlockedAndUsable(i)) continue;
    Item item = armor[i];
    // (shield-from-dash/raise special-cases first)
    if (ItemIsVisuallyIncompatible(item)) continue;
    if (item.wingSlot > 0) {
        if (hideVisibleAccessory[i] && (velocity.Y==0f || mount.Active)) continue;  // wings show airborne even if hidden
        wings = item.wingSlot;
    }
    if (!hideVisibleAccessory[i]) UpdateVisibleAccessory(i, item);   // <- the visual
}
for (int j = 13; j < 20; j++) {             // SOCIAL / VANITY accessories
    if (IsItemSlotUnlockedAndUsable(j)) {
        Item item2 = armor[j];
        if (!ItemIsVisuallyIncompatible(item2))
            UpdateVisibleAccessory(j, item2);                        // <- ALWAYS draws, overrides
    }
}
```

`UpdateVisibleAccessory` does `if (item.<cat>Slot > 0) <catField> = item.<cat>Slot;` for each
category (36467-36556). Because the **vanity loop (13..19) runs AFTER the functional loop
(3..9)** and unconditionally overwrites, the rule is:

> **Per category: the drawn slot = the vanity item in 13+i if it has that slot type, else the
> functional item in 3+i (when not hidden), else the lower-index slot that last wrote.** Within
> a loop, **higher index overwrites lower index** (slot 9 beats slot 3) for the same category —
> but in practice one item carries one category, so it's "the visible item in the
> highest-numbered slot that has that `*Slot`, vanity preferred."

**Bake-ready rule for the compositor** (given a TShock player with functional accessory item
IDs `acc[0..6]` in inv slots 62..68 and vanity IDs `vanity[0..6]` in inv slots 72..78, plus
`hideVisibleAccessory` bits):

```python
# resolved[cat] starts unset; iterate functional 0..6 then vanity 0..6 (low->high index)
for k in range(7):                          # functional acc k  (armor index 3+k)
    if hide_bit(3+k): 
        # wings exception: airborne shows anyway; for a STILL idle player velocity.Y==0 -> hidden
        continue
    apply_item_slots(resolved, acc[k])      # sets resolved[cat]=slot for each *Slot>0
for k in range(7):                          # vanity acc k     (armor index 13+k)  -> overrides
    apply_item_slots(resolved, vanity[k])
```

Notes:
- `IsItemSlotUnlockedAndUsable(8|18)` requires expert mode, `(9|19)` requires master mode
  (always true in `Main.gameMenu`). For an offline avatar treat **all 7 accessory slots as
  usable** (matches the character-preview path).
- `ItemIsVisuallyIncompatible` (36423) hides a few combos (shield while held-food, balloon 18
  with body 93/83, etc.) — edge cases, safe to ignore for a generic avatar.
- `back`/`front` interact: setting a real `back` sets `front = -1` (36488); `sitting` forces
  `back = -1`. For a standing avatar with no sit, both can coexist if from different items.

### X2. hideVisibleAccessory ↔ `appearance.hideVisuals`

`Player.hideVisibleAccessory` is `bool[10]` (`Player.cs:1606`). TShock/netplay packs it as a
bitmask (`hideVisuals` byte/short). **Bit k ↔ `hideVisibleAccessory[k]` ↔ armor slot k**:

| bit k | armor[] index | what it hides |
|------:|:-------------:|---|
| 0 | 10? — see note | (vanity head region; not an accessory visual) |
| 1 | — | (vanity body region) |
| 2 | — | (vanity legs region) |
| 3 | armor[3] | functional accessory **0** visual (inv 62) |
| 4 | armor[4] | functional accessory **1** visual (inv 63) |
| 5 | armor[5] | functional accessory **2** visual (inv 64) |
| 6 | armor[6] | functional accessory **3** visual (inv 65) |
| 7 | armor[7] | functional accessory **4** visual (inv 66) |
| 8 | armor[8] | functional accessory **5** visual (inv 67, expert) |
| 9 | armor[9] | functional accessory **6** visual (inv 68, master) |

Mechanics: `hideVisibleAccessory[i]` is read in the functional loop (`if (!hideVisibleAccessory[i])
UpdateVisibleAccessory(...)`, 36377) — it **only suppresses the functional item's visual**; the
**vanity twin (`armor[13+i]`) still draws** (the j-loop ignores the hide bit). Wings are the one
exception: hidden functional wings still draw while airborne (36371), but for a **still standing
avatar (velocity.Y==0)** the wings are hidden by the bit. The dye loop also respects it
(`UpdateItemDye(i<10, hideVisibleAccessory[i%10], …)`, 9323) — a hidden functional acc's dye is
dropped except for wings/special types.

> **NOTE on bits 0-2:** the 10-element array indexes parallel `armor[0..9]` (the 3 armor + 7
> functional acc). Bits 0/1/2 correspond to `armor[0/1/2]` = the **functional armor head/body/legs**
> — toggling them swaps to the **vanity** head/body/legs (handled in the head/body/leg layers, not
> the accessory layers). For accessory categories only **bits 3..9 matter**. Confirm the exact
> wire packing against your TShock version (it stores the raw `hideVisibleAccessory` booleans).

### X3. Accessory dyes — `accessoryDyes` (slots 82-88) → shader per category

`Player.UpdateDyes` (`Player.cs:9300`) + `UpdateItemDye` (9329). The dye loop:

```csharp
for (int i = 0; i < 20; i++)
    if (IsItemSlotUnlockedAndUsable(i)) {
        int num = i % 10;                                 // dye index 0..9
        UpdateItemDye(i < 10, hideVisibleAccessory[num], armor[i], dye[num]);
    }
```

So **`armor[i]` pairs with `dye[i % 10]`** — i.e. functional acc *k* (armor 3+k) AND its vanity
twin (armor 13+k) BOTH read `dye[3+k]`. `dye[3..9]` are the 7 accessory-dye items (TShock
`accessoryDye` inv slots 82..88 → these map to `dye[3..9]`; `dye[0..2]` = armor dyes from inv
79..81). `UpdateItemDye` then sets the matching `c<Cat>` from the **drawn** item's category
(mirrors `UpdateVisibleAccessory`'s category routing exactly):

| accessory category | shader field set by UpdateItemDye | used by draw layer |
|---|---|---|
| handon | `cHandOn = dye.dye` (9347) | DrawPlayer_29 / composite-front-arm |
| handoff | `cHandOff` (9351) | DrawPlayer_18 / composite-back-arm |
| back (cape/quiver) | `cBack` (9365); backpack→`cBackpack`, tail→`cTail` | DrawPlayer_10 |
| front | `cFront` (9370) | DrawPlayer_32 |
| shoe | `cShoe` (9377/9385); flamewaker special | DrawPlayer_14 |
| waist | `cWaist` (9390) | DrawPlayer_19 |
| shield | `cShield` (9394) | DrawPlayer_25 |
| neck | `cNeck` (9398) | DrawPlayer_20 |
| face | `cFace` (9416); head/mask/flower variants → `cFaceHead/cFaceMask/cFaceFlower` | DrawPlayer_22 / 21 |
| beard | `cBeard` (9421) | DrawPlayer_21 (head group) |
| balloon | `cBalloon` (9431); front→`cBalloonFront` | DrawPlayer_11 / 12_1 |
| wings | `cWings` (9436) | DrawPlayer_09 |

`dye.dye` is the `GameShaders.Armor` shader index (the same int your body-armor dye path uses).
**Confirmation it reuses `GameShaders.Armor`:** `ArmorShaderData.Apply` (the body-armor dye
shader) is what consumes these — `DrawData.shader = c<Cat>` is later applied by
`GameShaders.Armor.Apply(entity, drawData)` in the render pass; the dust trails use
`GameShaders.Armor.GetSecondaryShader(cWings, this)` (Player.cs:26875). So **`dye.apply_dye`
(your existing armor-dye numpy impl) applies unchanged** — see §Dye-geometry for the per-frame
`src_rect`/`sheet_size` you must feed it.

### X4. Hand accessories (handOn / handOff) — arm alignment

`UsesNewFramingCode` is set **for every** hand accessory (HandOn 1..24, HandOff 1..15;
ArmorIDs.cs:1583/1649). Therefore `usesCompositeFrontHandAcc`/`usesCompositeBackHandAcc` is
**true for all of them** (PlayerDrawSet.cs:1879-1880), and:

- The simple `DrawPlayer_18_OffhandAcc` (2044) / `DrawPlayer_29_OnhandAcc` (3838) paths — which
  draw `AccHandsOff`/`AccHandsOn` (40×1120 strip) with `bodyFrame` — are **guarded by
  `!usesCompositeBackHandAcc`/`!usesCompositeFrontHandAcc`** and so are **effectively never
  taken** for vanilla hand accessories. (They exist for legacy/unframed items; you can skip
  these textures unless you find an item whose slot is absent from `UsesNewFramingCode`.)
- The real draw is **inside the composite arm layers**:
  - **handoff** → `DrawPlayer_12_SkinComposite`'s back-arm block (PlayerDrawLayers.cs:1421-1428):
    `AccHandsOffComposite[handoff]` at `compBackArmFrame` (the **back-arm idle cell (2,2) = cell
    20**), shader `cHandOff`.
  - **handon** → `DrawPlayer_28_ArmOverItemComposite`'s front-arm block (3828-3835):
    `AccHandsOnComposite[handon]` at `compFrontArmFrame` (the **front-arm idle cell (2,0) = cell
    2**), shader `cHandOn`.
- These are the **exact same composite cells the body already uses** (terraria_render_spec §C/§4:
  front-arm cell 2, back-arm cell 20). So the compositor draws them 1:1 over the existing arm
  layers using the same `360×224` 9×4 grid geometry — **front-hand acc on top of front arm,
  back-hand acc on top of back arm**, no extra offset (`armorAdjust=0` idle). Composite hand-acc
  textures live in `Images/Accessories/Acc_Hands{On,Off}_{slot}.xnb` (360×224).

---

## Per-category sections

All offsets are **cell-local** = relative to the 40×56 player cell top-left. The common base
position term (identical to every body layer, terraria_render_spec §D) is:

```
basePos = Position - screenPos
        + (-bodyFrame.W/2 + width/2,  height - bodyFrame.H + 4)     // = cell top-left in screen
        + bodyPosition (or legPosition)                            // animation lean, 0 at idle
        + bodyVect (=(20,28)) / legVect (=(20,42))                 // = rotation origin; +draw origin
```

For an idle player (`velocity=0`, on ground, no mount, `bodyRotation=legRotation=headRotation=0`,
`bodyPosition=legPosition=headPosition=(0,0)`, `armorAdjust=0`, `playerEffect=None` for
right-facing) every **strip-framed** accessory composites **1:1 top-left aligned in the 40×56
cell**, exactly like the body. Tint = `colorArmorBody`/`colorArmorLegs`/`colorArmorHead` which —
in the no-world / fullbright / display-doll preview path — are **`Color.White`** (PlayerDrawSet.cs:
920-922; the normal path multiplies by world lighting only). **So accessories draw UNTINTED
(white), opacity 1.0**, with only their dye shader applied. None use a player color field.

`Directions = (direction, gravDir)` (Player.cs:3703); idle right-facing = `(1, 1)`.

---

### 1. Wings — `DrawPlayer_09_Wings` (PlayerDrawLayers.cs:655) — **BEHIND body**

- **Texture:** `TextureAssets.Wings[wings]` ← `Images/Wings_{wings}.xnb` (root, NOT
  `Accessories/`). `wings` = `item.wingSlot` (1..51). **Vertical strip of N frames**, frame
  height = `Height / N`. N varies by wing (the draw method hardcodes `Frame(1,N,…)` or
  `Height/N`):
  - default path (most wings): **N = 4** (`num14=4`); e.g. `Wings_1` = 86×248 → frame 86×62.
  - special N per wing: 6 (`34,39`), 7 (`22,44`), 8 (`48,51`?), 11 (`47,49,50`); measured:
    `Wings_5`=86×264, `Wings_22`=70×210, `Wings_44`=86×434, `Wings_50`=120×1034.
- **Idle frame:** `wingFrame`. **For a grounded standing player `wingFrame = 0`**
  (Player.cs:27097 `else { wingFrame = 0; }` — the `velocity.Y==0` / not-flying branch). So the
  idle source rect = **`(0, 0, Width, Height/N)`** (top frame = folded wings). *(Approximate
  still: animated/"AlwaysAnimated" wings 22,28,34,39,45,48,40,44 only draw when airborne —
  `ShouldDrawWingsThatAreAlwaysAnimated()` returns false at `velocity.Y==0` (Player.cs:30287) —
  so those wings render NOTHING for a still avatar. Chosen idle frame for all drawable wings =
  frame 0.)*
- **Draw position / offset:** centered origin `origin = (Width/2, (Height/N)/2)`. Base
  `vector = basePos_center + (0,7)` then standard path `vector18 = vector + (num13-9, num12+2)`
  (most wings `num13=num12=0`; a few tweak: w5 `(+4,-4)`, w12/41/27/43/44 small, w43 `(-5,-7)`).
  **Computed idle cell-local top-left for a default 86×62 wing = `(-32, +2)`**, spanning
  x∈[-32,54], y∈[2,64]. → **extends ~32 px to one side and ~8 px below the 40×56 cell.** Drawn at
  `Directions=(1,1)`; a left-facing player flips horizontally (extends right instead).
- **Canvas:** wings REQUIRE padding. Recommend a working canvas ≥ **left+right pad ≈ 48 px,
  bottom pad ≈ 16 px** (largest vanilla wing ~120 wide). Compute per-wing from
  `topleft = vector18 - origin - cellTopLeft` (formula above; verified numerically in research).
- **Draw order:** layer 09 — **first thing after mounts/backpacks, BEHIND back-hair and the
  whole body.**
- **Tint:** `colorArmorBody` (white). Many wings add extra glow/flame DrawData passes (lines
  670-1104) that only fire when airborne / `shadow==0` / specific IDs — **all skippable for a
  still avatar**; the single base `Wings[wings]` frame is the only always-present draw.
- **Dye:** shader `cWings`.
- **Sets (`ArmorIDs.Wing.Sets`, ArmorIDs.cs:1959):** `AlwaysAnimated = {22,28,45,34,48,39,40,44}`
  (these don't draw when grounded). `Stats[]` = flight stats (irrelevant to draw). No gender
  variant for wings.

### 2. Back accessory — `DrawPlayer_10_BackAcc` (590) — **BEHIND body**

- **Texture:** `TextureAssets.AccBack[back]` ← `Images/Acc_Back_{back}.xnb`. **40×1120**
  (20-frame vertical strip, same as body). `back` = `item.backSlot` IF not a backpack/tail (see
  routing). Drawn with `drawPlayer.bodyFrame` (idle = **`(0,0,40,56)`**).
- **Idle frame:** `bodyFrame` = `(0, 0, 40, 56)` (frame 0).
- **Draw position / offset:** `vec = basePos + (0,-4) + (0,8)` with origin `bodyVect`. Net
  cell-local = top-left aligned with a **+4 px downward** nudge vs the body
  (`(0,-4)+(0,8)=(0,+4)` after the `+4` base term math → effectively draws on the body cell;
  treat as **(0,0)** top-left for compositing, the cape art is pre-positioned in the 40×56 frame).
  `armorAdjust` is only set non-zero when wearing a front cape (front 1-4) — irrelevant for a
  plain back acc. Cell stays 40×56 (capes fit the frame).
- **Draw order:** layer 10 — behind body (drawn between wings and the body skin; after back-hair).
- **Tint:** `colorArmorBody` (white). (Special: back 36 SuperHeroCostume adds a shimmer glow
  strip 630-651 — skippable.)
- **Dye:** shader `cBack`.
- **Sets (`ArmorIDs.Back.Sets`, 1689):** `DrawInBackpackLayer = {7,8,9,10,15,16,32,33}` (these go
  to the `backpack` field + DrawPlayer_08_Backpacks, NOT `back`); `DrawInTailLayer =
  {18,19,21,25,26,27,28}` (→ `tail` field + DrawPlayer_08_1_Tails); `IsACape =
  {1,2,3,4,5,6,14,24,34,36,39}` (used by FrontAcc scarf/cape conflict check). **A "real" back acc
  (`back` field) is any backSlot NOT in backpack/tail sets.** No gender split.

### 3. Balloons — `DrawPlayer_11_Balloons` (1140) — **BEHIND body**

- **Texture:** `TextureAssets.AccBalloon[balloon]` ← `Images/Acc_Balloon_{balloon}.xnb`. Two
  layouts:
  - **Normal balloons (most):** **52×224 = 4-frame vertical strip** of 52×56 (measured
    `Acc_Balloon_1/8` = 52×224). `UsesTorsoFraming = false`.
  - **balloon 18 (RoyalScepter):** `UsesTorsoFraming = true` → **40×1120** strip, drawn with
    `bodyFrame` like a back acc.
- **Idle frame:** non-torso balloons animate on a wall-clock timer
  (`num = DateTime.Now.Millisecond % 800 / 200`, 0..3). **For a deterministic still pick frame
  0** → source rect `(0, 0, 52, 56)`. (Inherently approximate: the in-game balloon cycles 4
  frames; idle frame 0 chosen.) Torso-framed balloon 18 uses `bodyFrame` = `(0,0,40,56)`.
- **Draw position / offset (non-torso):** origin `= (26 + dir*4, 28 + grav*6) = (30, 34)` idle;
  base `vector3 = basePos + OffsetsPlayerOffhand[0]*(1,grav) + (0, height-bodyFrame.H) + (0,8)+(0,6)`
  with `OffsetsPlayerOffhand[0] = (14,20)`. **Computed idle cell-local top-left = `(-6, -24)`**,
  spanning x∈[-6,46], y∈[-24,32] → **extends ~24 px ABOVE the cell** (balloon floats overhead).
  → **needs top padding ≈ 24 px.** Torso-framed (18) composites 1:1 at (0,0).
- **Draw order:** layer 11 — behind body (after back acc, before the held-item/body). (There's
  also `DrawPlayer_12_1_BalloonFronts` (1107) for `balloonFront` items — balloon 18 only, drawn
  in front of the back arm; same texture/framing logic, shader `cBalloonFront`.)
- **Tint:** `colorArmorBody` (white).
- **Dye:** shader `cBalloon` (or `cBalloonFront`).
- **Sets (`ArmorIDs.Balloon.Sets`, 2206):** `UsesTorsoFraming = {18}`; `DrawInFrontOfBackArmLayer
  = {18}` (routes balloon 18 to the `balloonFront` field instead of `balloon`). No gender split.

### 4. Shoes (shoe accessory) — `DrawPlayer_14_Shoes` (1756) — drawn with leggings (over legs)

- **Texture:** `TextureAssets.AccShoes[shoe]` ← `Images/Acc_Shoes_{shoe}.xnb`. **40×1120** strip.
  `shoe` = `item.shoeSlot` (with female remap, see Sets). Drawn with `drawPlayer.legFrame`.
- **Idle frame:** `legFrame` = `(0, 0, 40, 56)` (frame 0).
- **Draw position / offset:** `GetShoeDrawOffset()` + standard leg base + `legVect`. Idle offset
  = **(0,0)** for normal shoes; **+(0,2)·Directions** only for roller skates (shoe 27-30, which
  are mount-driven). Top-left aligned in the 40×56 cell.
- **Draw order:** layer 14 — interleaved with leggings (`DrawPlayer_13_Leggings`, layer 13)
  by a `wearsRobe` branch in `LegacyPlayerRenderer.DrawPlayer_UseNormalLayers`
  (LegacyPlayerRenderer.cs:194-203):
  - **Normal branch (the common/default case** — `!(wearsRobe && body != 166)`, lines 201-202):
    `DrawPlayer_13_Leggings` **then** `DrawPlayer_14_Shoes`, i.e. the shoe acc draws **OVER** the
    leggings (default pants+default shoes, or leg armor). For a default-clothed idle player the
    shoe acc sits **on top of the default shoes** and is visible.
  - **Robe branch** (`wearsRobe && body != 166`, lines 196-197): `DrawPlayer_14_Shoes` **then**
    `DrawPlayer_13_Leggings`, i.e. the shoe acc draws **UNDER** the leggings (so the long robe
    skirt covers the feet). Only the listed robe bodies take this branch.
  - `wearsRobe` is set by `Player.SetMatch(ArmorSlotRequested=1, ref wearsRobe)`
    (Player.cs:35350-35360 → switch at 36776-36886): every listed body slot sets `flag=true`
    **except body 166 (`flag=false`)**, and the branch condition itself re-excludes 166, so
    **body 166 always takes the normal branch**. The `flag=true` body slots are:
    `{15, 36, 41, 42, 58, 59, 60, 61, 62, 63, 77, 165, 167, 180, 181, 183, 191, 93, 90, 88,
    81, 213, 215, 219, 221, 223, 231, 232, 233, 241, 256}`. **Slot 81** only counts as a robe
    when there is **no leg armor** (its `case 81` assigns `num2` — hence `somethingSpecial = flag`
    — only when `request.Legs ∈ {-1, 0}`; with leg armor `num2` stays `-1` so `wearsRobe` is not
    set). The leg skin (layer 10) is drawn first in both branches; the shoe acc rides `legFrame`.
- **Tint:** `colorArmorLegs` (white). (shoe 22/23 FlameWaker use `cFlameWaker` shader instead of
  `cShoe`.)
- **Dye:** shader `cShoe` (or `cFlameWaker` for 22/23).
- **Sets (`ArmorIDs.Shoe.Sets`, 1835):** `MaleToFemaleID = {25→?, 26→?}` — **gender variant:**
  GlassSlipper male=25 remaps to female id via `MaleToFemaleID[shoe]` when `!Male`
  (UpdateVisibleAccessory 36502-36505). `IsARollerSkate = {27,28,29,30}` (mount shoes). No
  separate female TEXTURE file — the remap picks a different slot id (different sprite).

### 5. Offhand accessory (handOff) — `DrawPlayer_18_OffhandAcc` (2044) — IN FRONT (with torso)

- **See §X4.** Non-composite path (this method) is **guarded off for all vanilla items**
  (`!usesCompositeBackHandAcc`). The real draw is `AccHandsOffComposite[handoff]` at
  `compBackArmFrame` (cell 20) inside the back-arm composite layer (PlayerDrawLayers.cs:1421).
- **Texture:** composite `Images/Accessories/Acc_HandsOff_{handoff}.xnb` = **360×224** 9×4 grid;
  (legacy root `Images/Acc_HandsOff_{s}.xnb` = 40×1120, unused).
- **Idle frame:** back-arm idle cell `(col 2, row 2)` → source rect **`(80, 112, 40, 56)`** (=
  cell 20), gender-independent (matches body back arm).
- **Offset:** none beyond the back-arm composite position (`armorAdjust=0` idle). Composites over
  the body's back-arm.
- **Draw order:** rides the **back-arm composite group** (early, with `DrawPlayer_12_Skin`), so
  visually it's on the back arm behind the torso. (The standalone DrawPlayer_18 slot in the
  master list, 208, is the dead path.)
- **Tint:** `colorArmorBody` (white). **Dye:** `cHandOff`.
- **Sets:** `HandOff.Sets.UsesNewFramingCode = {1..15}` (all) → always composite.

### 6. Waist accessory — `DrawPlayer_19_WaistAcc` (2066) — IN FRONT

- **Texture:** `TextureAssets.AccWaist[waist]` ← `Images/Acc_Waist_{waist}.xnb`. **40×1120**
  strip. `waist` = `item.waistSlot`.
- **Idle frame:** `legFrame` normally → **`(0,0,40,56)`**; but if `UsesTorsoFraming[waist]` it
  uses `bodyFrame` (same rect at idle, different art row mapping). Both = `(0,0,40,56)` at idle.
- **Draw position / offset:** standard leg base + `legVect`. Top-left aligned (0,0).
- **Draw order:** layer 19 — in front, after torso (DrawPlayer_17), before neck. On top of the
  body's waist.
- **Tint:** `colorArmorLegs` (white). **Dye:** `cWaist`.
- **Sets (`ArmorIDs.Waist.Sets`, 1911):** `UsesTorsoFraming = {5,10,12}` (Toolbelt, Master Ninja/
  BlackBelt, MonkBelt — draw with bodyFrame); `IsABelt = {5,10,12}`. No gender split.

### 7. Neck accessory — `DrawPlayer_20_NeckAcc` (2081) — IN FRONT

- **Texture:** `TextureAssets.AccNeck[neck]` ← `Images/Acc_Neck_{neck}.xnb`. **40×1120** strip.
  `neck` = `item.neckSlot`.
- **Idle frame:** `bodyFrame` = **`(0,0,40,56)`**.
- **Draw position / offset:** standard body base + `bodyVect`. Top-left aligned (0,0).
- **Draw order:** layer 20 — in front, after waist, just before the head group. On top of torso.
- **Tint:** `colorArmorBody` (white). **Dye:** `cNeck`.
- **Sets (`ArmorIDs.Neck.Sets`, 2098):** `IsAScarf = {8,9}` (WormScarf, ApprenticeScarf — used by
  FrontAcc cape/scarf conflict). No gender split.

### 8. Face accessory — `DrawPlayer_22_FaceAcc` (2801) — IN FRONT (over head)

- **Texture:** `TextureAssets.AccFace[face]` ← `Images/Acc_Face_{face}.xnb`. **40×1120** strip.
  `face` = `item.faceSlot` IF not routed to faceMask/faceFlower/faceHead (see Sets routing).
  Drawn with `bodyFrame`.
- **Idle frame:** `bodyFrame` = **`(0,0,40,56)`** plus `GetFaceDrawOffset(face)` which is **(0,0)
  for a bare-headed player** for most faces (only nonzero with specific helmets, or face 19
  `(0,-6)·Directions`). For a naked avatar (head=-1) treat as `(0,0)`.
- **Draw position / offset:** `faceDrawOffset + headBase + headVect`. Idle = top-left aligned
  (0,0). (`face 5` Blindfold has `DrawInFaceUnderHairLayer` → drawn earlier under hair, not here.)
- **Draw order:** layer 22 — in front, drawn right after the head group (DrawPlayer_21), over the
  face/hair. (`faceMask` and `faceFlower` are additional face sub-layers drawn in the same method,
  shaders `cFaceMask`/`cFaceFlower`; `faceHead` 12/10/13/11 draws in the head layer with skin
  shader.) Also draws AngelHalo (face 7) / UnicornHorn extras — gated, skippable.
- **Tint:** `colorArmorHead` (white). **Dye:** `cFace` (mask=`cFaceMask`, flower=`cFaceFlower`).
- **Sets (`ArmorIDs.Face.Sets`, 2134):** `PreventHairDraw = {2,3,4,19}` (these hide the front
  hair); `DrawInFaceUnderHairLayer = {5}`; `DrawInFaceMaskLayer = {22}`; `DrawInFaceFlowerLayer =
  {1,6,8,9}`; `DrawInFaceHeadLayer = {10,11,12,13}`; `AltFaceHead` pairs alt skull variants.
  No gender split. **A face acc with `PreventHairDraw` suppresses the front-hair draw in
  DrawPlayer_21 (2420).**

### 9. Shield — `DrawPlayer_25_Shield` (3055) — IN FRONT

- **Texture:** `TextureAssets.AccShield[shield]` ← `Images/Acc_Shield_{shield}.xnb`. **40×1120**
  strip — but **some are WIDER than 40** (measured `Acc_Shield_5` ShieldofCthulhu = 44×1120). The
  draw adjusts `bodyFrame.Width = texture.Width` and shifts `bodyVect.X` accordingly (3069-3077).
  `shield` = `item.shieldSlot`.
- **Idle frame:** `bodyFrame` with `Width = texture.Width` → **`(0, 0, texW, 56)`** (texW=40 or 44).
- **Draw position / offset:** base body + adjusted `bodyVect` + `zero` (0,0 unless
  `shieldRaised`, which adds `(0,-4)` — not for idle). Top-left aligned; if texW>40 the shield
  hangs slightly left/right of the 40-wide cell (≤4px), minor — keep a few px horizontal pad or
  just clamp.
- **Draw order:** layer 25 — in front, after `DrawPlayer_32_FrontAcc_BackPart` (229) and before
  the front arm's onhand (master list 230). On the body's side.
- **Tint:** `colorArmorBody` (white). (shieldRaised adds glow passes — not idle.)
- **Dye:** shader `cShield`. (Note `cShieldFallback` handles dash/eoc shields — edge case.)
- **Sets:** `ArmorIDs.Shield` has **no `.Sets`** (Count=10). No gender split.

### 10. Onhand accessory (handOn) — `DrawPlayer_29_OnhandAcc` (3838) — IN FRONT (with front arm)

- **See §X4.** Non-composite path guarded off for all vanilla items. Real draw =
  `AccHandsOnComposite[handon]` at `compFrontArmFrame` (cell 2) inside the front-arm composite
  (PlayerDrawLayers.cs:3828).
- **Texture:** composite `Images/Accessories/Acc_HandsOn_{handon}.xnb` = **360×224** 9×4 grid;
  (legacy root `Images/Acc_HandsOn_{s}.xnb` = 40×1120, unused).
- **Idle frame:** front-arm idle cell `(col 2, row 0)` → source rect **`(80, 0, 40, 56)`** (= cell
  2), gender-independent (matches body front arm).
- **Offset:** none beyond the front-arm composite position. Composites over the body's front arm.
- **Draw order:** rides the **front-arm composite group** (`DrawPlayer_28_ArmOverItemComposite`,
  late, over the held item / torso). On the front arm, in front of the torso.
- **Tint:** `colorArmorBody` (white). **Dye:** `cHandOn`.
- **Sets:** `HandOn.Sets.UsesNewFramingCode = {1..24}` (all) → always composite.

### 11. Front accessory — `DrawPlayer_32_FrontAcc` (3880) — split front/back parts

- **Texture:** `TextureAssets.AccFront[front]` ← `Images/Acc_Front_{front}.xnb`. **40×1120**
  strip. `front` = `item.frontSlot`.
- **Idle frame:** `bodyFrame` = **`(0,0,40,56)`**, but drawn as **TWO half-width halves**:
  - `_FrontPart` (3891): left half `bodyFrame.Width -= W/2` → src `(0,0,20,56)`, drawn on the
    front side of the torso. Drawn in the head-group region (master list 222 / 243).
  - `_BackPart` (3934): right half `bodyFrame.X += W/2; Width -= W/2` → src `(20,0,20,56)`, drawn
    behind (master list 229, before shield).
  Plus `GetFrontDrawOffset()` = **(0,0)** idle (only front 13 = `(-2,0)·Directions`).
  (The plain `DrawPlayer_32_FrontAcc` (3880) full-frame path is used only when `mount.Active` is
  false in the older code path; the front/back split is the active path — render BOTH halves to
  reconstruct the full 40-wide front-acc sprite at (0,0).)
- **Draw position / offset:** top-left aligned (0,0); the two halves tile to the full 40×56.
- **Draw order:** front acc is **split**: `_BackPart` at layer ~29.5 (behind shield, in front of
  body), `_FrontPart` at layer ~22 / 32 (over the head/front arm). For a simple still avatar you
  can draw the full `Acc_Front_{front}` frame 0 at (0,0) **in front of the torso/neck**
  (approximation that merges both halves; pixel-exact requires the two-half split to interleave
  with the arm).
- **Tint:** `colorArmorBody` (white). (front 12 SuperHero adds shimmer glow strip — skippable.)
- **Dye:** shader `cFront`.
- **Sets (`ArmorIDs.Front.Sets`, 1783):** `DrawsInNeckLayer = {6}` (TaxCollector draws in neck
  layer); `DrawsInNeckLayerRegardlessOfPlayerFrame = {13}`; `DontDrawIfWearingAScarfOrCape = {13}`
  (DeadCells body hidden if wearing a scarf/cape — checks `Neck.IsAScarf` / `Back.IsACape`);
  `IsACape = {1,2,3,4,5,8,11,12,16}`; `HidesCompositeShoulders = {8,11,15,16}`. No gender split.

### 12. Beard — drawn inside `DrawPlayer_21_Head` (head group, PlayerDrawLayers.cs:2431-2442)

- **Texture:** `TextureAssets.AccBeard[beard]` ← `Images/Acc_Beard_{beard}.xnb`. **40×1120**
  strip. `beard` = `item.beardSlot` (1..4).
- **Idle frame:** `bodyFrame` = **`(0,0,40,56)`** plus `GetBeardDrawOffset()` = **(0,0)** for a
  bare-headed player (nonzero only with specific helmets/mounts).
- **Draw position / offset:** `beardOffset + headBase + headVect`. Idle = top-left aligned (0,0).
- **Draw order:** drawn in the **head group**, after the front hair / face (so the beard overlays
  the lower face). Gated by `flag7 = head < 0 || !Head.Sets.PreventBeardDraw[head]` (2426) — for
  a naked player (head=-1) `flag7=true`.
- **Tint:** `colorArmorHead` (white) **unless** `Beard.Sets.UseHairColor[beard]` → then
  `colorHair` (the player's hair color, possibly hair-dyed). **Beards 2,3,4 (Wilson beards) use
  the hair color**; beard 1 (GingerBeard) uses white.
- **Dye:** shader `cBeard`.
- **Sets (`ArmorIDs.Beard.Sets`, 2275):** `UseHairColor = {2,3,4}`. Count=5. No gender split.

---

## netID → slot tables (per category, bake-ready)

`item.{wing,back,balloon,shoe,handOff,waist,neck,face,shield,handOn,front,beard}Slot` is assigned
in `Item.SetDefaults`. The values below are the **ArmorIDs.* constant tables** (verbatim from
`ArmorIDs.cs`, authoritative — each constant IS the `*Slot` an item gets). To build the full
**netID→slot** map, you still need to read each item's `SetDefaults` to learn which item type sets
which slot constant; the constant→meaning tables here are the bake target (slot count per category
== `Count-1`, slot 0 = "none").

> **TODO for the implementer:** parse `Terraria/Item.cs` `SetDefaults` (49.5k lines) for every
> `<cat>Slot = N` to get item.type → slot, exactly as the prior `equip_slots` work did for
> armor. The category→slot-id tables (below) and on-disk file counts (matching `Count-1`) are the
> complete target ranges.

### Wing (Count=52, slots 1..51) — `Wings_{slot}`
1 DemonWings · 2 AngelWings · 3 RedsWings · 4 Jetpack · 5 ButterflyWings · 6 FairyWings · 7
HarpyWings · 8 BoneWings · 9 FlameWings · 10 FrozenWings · 11 SpectreWings · 12 SteampunkWings ·
13 LeafWings · 14 BatWings · 15 BeeWings · 16 DTownsWings · 17 WillsWings · 18 CrownosWings · 19
CenxsWings · 20 TatteredFairyWings · 21 SpookyWings · **22 Hoverboard\*** · 23 FestiveWings · 24
BeetleWings · 25 FinWings · 26 FishronWings · 27 MothronWings · **28 LazuresBarrierPlatform\*** ·
29 SolarWings · 30 VortexBooster · 31 NebulaMantle · 32 StardustWings · 33 Yoraiz0rsSpell · **34
JimsWings\*** · 35 SkiphssPaws · 36 LokisWings · 37 BetsyWings · 38 ArkhalisWings · **39
LeinforsWings\*** · **40 GhostarsWings\*** · 41 SafemanWings · 42 FoodBarbarianWings · 43
GroxTheGreatWings · **44 RainbowWings\*** · **45 LongTrailRainbowWings\*** · 46 CreativeWings · 47
ChickenBonesWings · **48 ChippysWings\*** · 49 HeroicisWings · 50 KazzymodusWings · 51 LunasWings.
(\* = `AlwaysAnimated`, draws nothing when grounded.)

### Back (Count=40, slots 1..39) — `Acc_Back_{slot}`
1 BeeCloak · 2 StarCloak · 3 CrimsonCloak · 4 MysteriousCape · 5 RedCape · 6 WinterCape · **7
MagicQuiver(BP)** · **8 ArchitectGizmoPack(BP)** · **9 HivePack(BP)** · **10 AnglerTackleBag(BP)** ·
11 ApprenticeDark · 12 RedRidingHuntress · 13 ShinobiInfiltrator · 14 ManaCloak · **15
MoltenQuiver(BP)** · **16 StalkersQuiver(BP)** · 17 ClothiersJacket · **18 SpaceCreatureShirt(T)** ·
**19 FoxShirt(T)** · 20 VampireShirt · **21 CatShirt(T)** · 22 SuperHeroCostumeMale · 23
SuperHeroCostumeFemale · 24 HunterCloak · **25 DogTail(T)** · **26 FoxTail(T)** · **27
LizardTail(T)** · **28 BunnyTail(T)** · 29 HallowedCape · 30 PlaguebringerCloak · 31 RoninCloak ·
**32 FloretProtecterChestplate(BP)** · **33 LavaproofTackleBag(BP)** · 34 PrinceCape · 35
HandOfCreation · 36 ShimmerCloak · 37 ChickenBonesRobe · 38 ChippysWings · 39 LunasCloak.
(**(BP)** routes to `backpack` field/layer 08; **(T)** routes to `tail` field/layer 08_1 — NOT
the `back` layer 10. The remaining = real `back` capes.)

### Front (Count=17, slots 1..16) — `Acc_Front_{slot}`
1 CrimsonCloak · 2 MysteriousCape · 3 RedCape · 4 WinterCape · 5 ManaCloak · 6 TaxCollectorsSuit ·
7 VampireShirt · 8 HunterCloak · 9 PlaguebringerCloak · 10 RoninCloak · 11 PrinceCape · 12
ShimmerCloak · 13 DeadCellsBeheadedBody · 14 ChickenBonesRobe · 15 ChippysWings · 16 LunasCloak.

### Shoe (Count=31, slots 1..30) — `Acc_Shoes_{slot}`
1 Flipper · 2 WaterWalkingBoots · 3 Tabi · 4 TigerClimbingGear/ShoeSpikes · 5 FlurryBoots · 6
HermesBoots · 7 IceSkates · 8 LavaWaders · 9 FrostsparkBoots · 10 LightningBoots · 11
ObsidianWaterWalkingBoots · 12 RocketBoots · 13 SpectreBoots · 14 MasterNinjaGear · 15 FrogLeg ·
16 FlowerBoots · 17 SailfishBoots · 18 AmphibianBoots · 19 FairyBoots · 20 FrogFlipper · 21
SandBoots · 22 FlameWakerBoots\* · 23 HellfireTreads\* · 24 TerrasparkBoots · 25 GlassSlipperMale ·
26 GlassSlipperFemale · 27-30 RollerSkates(+colors, mount). (\* shoe 22/23 → `cFlameWaker` shader.
GlassSlipper male 25 → female 26 via `MaleToFemaleID` when `!Male`.)

### Waist (Count=17, slots 1..16) — `Acc_Waist_{slot}`
1 CloudinaBottle · 2 CopperWatch · 3 GoldWatch · 4 PlatinumWatch · **5 Toolbelt(torso)** · 6
ManaFlower · 7 SilverWatch · 8 TinWatch · 9 TungstenWatch · **10 MasterNinjaGear/BlackBelt(torso)** ·
11 TsunamiinaBottle · **12 MonkBelt(torso)** · 13 BlizzardinaBottle · 14 FartinaJar · 15
SandstorminaBottle · 16 TreasureMagnet. (`UsesTorsoFraming = {5,10,12}`.)

### Neck (Count=13, slots 1..12) — `Acc_Neck_{slot}`
1 JellyfishNecklace · 2 CrossNecklace · 3 PanicNecklace · 4 PygmyNecklace · 5 StarVeil · 6
SweetheartNecklace · 7 SharkToothNecklace · **8 WormScarf(scarf)** · **9 ApprenticeScarf(scarf)** ·
10 Stinger · 11 Magiluminescence · 12 MoltenCharm. (`IsAScarf = {8,9}`.)

### Face (Count=24, slots 1..23) — `Acc_Face_{slot}`
1 NaturesGift(flower) · 2 ArcticDivingGear · 3 JellyfishDivingGear · 4 DivingGear · 5
Blindfold(underHair) · 6 ObsidianRose(flower) · 7 AngelHalo · 8 JungleRose(flower) · 9
ArcaneFlower(flower) · 10 LavaSkull(head) · 11 MoltenSkullRose(head) · 12 ObsidianSkull(head) · 13
ObsidianSkullRose(head) · 14 SpectreGoggles · 15-18 *Alt skull variants(head)* · 19 BoneHelm · 20
ReflectiveShades · 21 JimsDroneVisor · 22 WeldingMask(mask) · 23 ChippysHeadband. (routing: head
={10,11,12,13}, mask={22}, flower={1,6,8,9}, underHair={5}; rest = `face` field/layer 22.)

### Shield (Count=10, slots 1..9) — `Acc_Shield_{slot}`
1 CobaltShield · 2 PaladinsShield · 3 ObsidianShield · 4 AnkhShield · 5 ShieldofCthulhu(44px wide)
· 6 SquireShield · 7 Frozen · 8 Hero · 9 BouncingShield.

### HandOn (Count=25, slots 1..24) — composite `Accessories/Acc_HandsOn_{slot}`
1 ManaRegenerationBand · 2 BandofRegeneration · 3 BandofStarpower · 4 CharmofMyths · 5 FeralClaws ·
6 FireGauntlet · 7 HandWarmer · 8 MagicCuffs · 9 MechanicalGlove · 10 PowerGlove · 11
MasterNinjaGear/TigerClimbingGear/ClimbingClaws · 12 Shackle · 13 SunStone · 14 MoonStone · 15
TitanGlove · 16 DiamondRing · 17 CelestialCuffs · 18 YoyoGlove · 19 HuntressBuckler · 20
BersekerGlove · 21 FrogWebbing · 22 BoneGlove · 23 HandOfCreation · 24 LavaCharm.

### HandOff (Count=16, slots 1..15) — composite `Accessories/Acc_HandsOff_{slot}`
1 FireGauntlet · 2 HandWarmer · 3 MagicCuffs · 4 MechanicalGlove · 5 PowerGlove · 6
MasterNinjaGear/TigerClimbingGear/ClimbingClaws · 7 Shackle · 8 TitanGlove · 9 FeralClaws · 10
CelestialCuffs · 11 YoyoGlove · 12 BersekerGlove · 13 FrogWebbing · 14 BoneGlove · 15
HandOfCreation.

### Balloon (Count=20, slots 1..19) — `Acc_Balloon_{slot}`
1 BlizzardinaBalloon · 2 BlueHorseshoeBalloon · 3 BundleofBalloons · 4 CloudinaBalloon · 5
FartinaBalloon · 6 SandstorminaBalloon · 7 HoneyBalloon · 8 ShinyRedBalloon · 9
WhiteHorseshoeBalloon · 10 YellowHorseshoeBalloon · 11 BalloonPufferfish · 12 SharkronBalloon · 13
GreenHorseshoeBalloon · 14 AmberHorseshoeBalloon · 15 PinkHorseshoeBalloon · 16
BundledPartyBalloons · 17 BalloonAnimal · **18 RoyalScepter(torso-framed,balloonFront)** · 19
HorseshoeBundle.

### Beard (Count=5, slots 1..4) — `Acc_Beard_{slot}`
1 GingerBeard(white) · 2 WilsonBeardShort(hairColor) · 3 WilsonBeardLong(hairColor) · 4
WilsonBeardMagnificent(hairColor). (`UseHairColor = {2,3,4}`.)

---

## Dye geometry — feeding `dye.apply_dye` per accessory

`ArmorShaderData.Apply` (ArmorShaderData.cs:91-96) sets `uSourceRect = DrawData.sourceRect`,
`uImageSize0 = (texture.Width, texture.Height)`. So when applying an accessory dye, pass the
**accessory's own** frame rect + full texture size (NOT the body's `(0,0,40,56)`/`(40,1120)`):

| category | `src_rect` (idle) | `sheet_size` |
|---|---|---|
| strip accessories (back/front/neck/face/shoe/waist/beard, balloon#18) | `(0,0,40,56)` | `(40,1120)` |
| shield | `(0,0,texW,56)` | `(texW,1120)` (texW=40 or 44) |
| composite hand-on | `(80,0,40,56)` | `(360,224)` |
| composite hand-off | `(80,112,40,56)` | `(360,224)` |
| wings (default N=4) | `(0,0,W,H/N)` | `(W,H)` |
| balloons (normal) | `(0,0,52,56)` | `(52,224)` |

This matters for the **spatial dye passes** (gradient/rainbow/noise) which normalize pixel-x by
`1/uSourceRect.z` and sample noise by `uImageSize0` — see `dye_passes_spec.md` §Conventions. The
non-spatial passes (basic color) don't care. `dye.apply_dye(frame, spec, src_rect=…,
sheet_size=…)` already takes these params; just supply the per-accessory values above.

---

## Recommended compositor integration order (full back-to-front)

Merge accessories into the existing body recipe (terraria_render_spec §6). Strip-framed
accessories use idle frame `(0,0,40,56)` and composite **1:1 top-left**; wings/balloons need a
padded canvas (draw at their computed cell-local offsets). Each layer: tint = white, opacity 1,
shader = its `c<Cat>` dye.

```
# ===== BEHIND BODY (needs padded canvas for wings/balloons) =====
 0. Wings[wings]                @ wing idle offset (cell-local ~(-32,+2) for 86×62), frame 0   [cWings]
 1. (back hair — existing)
 2. AccBack[back]               @ (0,0,40,56)                                                   [cBack]
 3. AccBalloon[balloon]         @ balloon idle offset (cell-local ~(-6,-24)), frame 0           [cBalloon]
 4. AccBalloon[balloonFront]    @ same (balloon 18 only, in front of back arm)                  [cBalloonFront]

# ===== BACK ARM group (existing) + back-hand acc =====
 5. Players[var,7] back-arm skin @ cell20  /  Players[var,5] back hand @ cell20  (existing)
 6. AccHandsOffComposite[handoff] @ (80,112,40,56) cell20                                       [cHandOff]
 7. Players[var,8],[var,13] back-arm undershirt/shirt (existing)

# ===== TORSO SKIN / LEGS (existing) =====
 8. Players[var,3] torso skin (existing)
 9. Players[var,10] leg skin, [var,11] pants, [var,12] shoes, [var,14] coat (existing)
10. AccShoes[shoe]              @ (0,0,40,56)   (normal branch: AFTER leggings, over the         [cShoe]
                                                default shoes; robe branch swaps to before — §4)

# ===== TORSO CLOTHES (existing) =====
11. Players[var,4] undershirt, [var,6] shirt @ torso cell (existing)

# ===== IN FRONT, torso accessories =====
12. AccWaist[waist]            @ (0,0,40,56)                                                    [cWaist]
13. AccNeck[neck]              @ (0,0,40,56)                                                    [cNeck]
14. AccFront[front] BACK half  @ (20,0,20,56)  (front/back split — back part)                   [cFront]

# ===== HEAD group (existing) + beard =====
15. Players[var,0] head, [1] eyewhites, [2] pupils, [15] eyelid, + front hair (existing)
16. AccBeard[beard]            @ (0,0,40,56)   (hairColor if beard∈{2,3,4} else white)          [cBeard]
17. AccFace[face]              @ (0,0,40,56)   (skip if PreventHairDraw already handled hair)   [cFace]
    AccFace[faceMask]/[faceFlower] as sub-layers                                       [cFaceMask/cFaceFlower]

# ===== FRONT ARM group (existing) + front-hand acc =====
18. Players[var,7] front-arm skin @ cell2, [8],[13],[9] (existing front arm)
19. AccHandsOnComposite[handon] @ (80,0,40,56) cell2                                            [cHandOn]

# ===== IN FRONT, outermost =====
20. AccFront[front] FRONT half @ (0,0,20,56)  (front/back split — front part, over arm)         [cFront]
21. AccShield[shield]          @ (0,0,texW,56)                                                  [cShield]
```

> **Approximation note:** if you don't want to split the front-acc into two 20-px halves, draw
> the full `Acc_Front_{front}` frame 0 once at step 20 (over the torso/neck) — visually close for
> most capes/scarves, but the pixel-exact behavior layers the back half behind the front arm and
> the front half over it. Wings, normal balloons, and any animated wing (AlwaysAnimated set) are
> the only **inherently approximate** stills: animated wings draw nothing grounded, normal
> balloons cycle 4 frames (we pin frame 0), and the chosen idle wing frame is frame 0 (folded).

## Caveats / Not found

- **Item.cs netID→slot extraction NOT done here** (49.5k lines; the constant→meaning tables +
  on-disk file counts are provided as the target). Replicate the `equip_slots` extraction over
  `Item.cs SetDefaults` for the full type→slot map per category.
- Wing per-slot frame-count N and exact `(num13,num12)` position tweaks are enumerated in the
  draw method (PlayerDrawLayers.cs:932-1104) but only a handful differ from the default
  (N=4, offset (-9,+2)); a wing-by-wing table would require reading each `if (wings==K)` branch —
  the default covers the majority, special wings (22,28,34,39,40,44,45,48 = AlwaysAnimated; 5,12,
  27,41,43 = small offset tweaks; 50/51 = many frames) are flagged.
- Glow/flame/shimmer secondary DrawData passes (per-wing, shield-raised, back#36, front#12) are
  intentionally omitted — they only fire airborne / `shadow==0` / on specific IDs and are not
  part of a plain still avatar.
- `hideVisibleAccessory` bits 0-2 wire semantics (vanity armor head/body/legs) should be
  re-confirmed against the exact TShock `hideVisuals` packing; bits 3-9 (the accessory visuals)
  are the ones this spec relies on and are unambiguous from `UpdateVisibleAccessories`.
