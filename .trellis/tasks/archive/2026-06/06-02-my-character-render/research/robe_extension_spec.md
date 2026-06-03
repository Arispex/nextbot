# Robe / Long-Coat Body Extension — full spec for compositor.py

Reverse-engineered from `temp/decomp/full/Terraria.DataStructures/PlayerDrawLayers.cs`
(`DrawPlayer_16_ArmorLongCoat`, `DrawPlayer_13_ArmorBackCoat`, `GetMatchingBodyExtension`,
`GetMatchingBodyExtensionBack`, `DrawLongCoat`) + draw order in
`temp/decomp/full/Terraria.Graphics.Renderers/LegacyPlayerRenderer.cs`
(`DrawPlayer_UseNormalLayers`), cross-checked against `research/terraria_render_spec.md` and the
shipped assets in `nextbot/terraria_render/assets/`.

## TL;DR

Certain **body** armors (robes, long coats, dresses) draw an **extra leg-armor sprite** as their
coat skirt. The game keys this on `drawPlayer.body` (and a second pass on `drawPlayer.coat`),
maps it to a **leg-armor slot** via `GetMatchingBodyExtension`, and draws
`TextureAssets.ArmorLeg[extSlot]` (= our `Armor_Legs_{extSlot}.png`) at the **idle leg frame**,
tinted `colorArmorBody`, dyed with `cBody`, positioned **identically to the body** (1:1 overlay),
**just behind the torso sprite** (layer 16, before layer 17). **22 of our body slots need this.**
The separate **back-coat** layer (`coat==251`) is **N/A for our data** (no body 251).

---

## A) Front long-coat — the full `bodySlot → extension leg-armor slot` map

From `GetMatchingBodyExtension(drawinfo, bodyValue)` (`PlayerDrawLayers.cs:1848-1924`). The
switch is on the **body equip slot** (`drawPlayer.body`), returns a **leg-armor slot** (or −1).
Some entries are **gender-conditional** (`drawPlayer.Male`).

| body slot | → leg-armor slot | note |
|---:|---:|---|
| 200 | 149 | |
| 201 | 150 | |
| 202 | 151 | |
| 209 | 160 | |
| 207 | 161 | |
| 198 | 162 | |
| 182 | 163 | |
| 168 | 164 | |
| 73  | 170 | |
| 81  | 169 | |
| 89  | 186 | |
| 187 | 173 | |
| 205 | 174 | |
| 218 | 195 | |
| 225 | 206 | |
| 236 | 221 | |
| 237 | 223 | |
| 52  | **171 if Male else 172** | gender-conditional |
| 53  | **175 if Male else 176** | gender-conditional |
| 210 | **178 if Male else 177** | gender-conditional |
| 211 | **182 if Male else 181** | gender-conditional |
| 222 | **201 if Male else 200** | gender-conditional |
| 251 | 238 | (also has a back-coat, see §C) |

> Source order in the C# switch differs; above is sorted by body slot. The gender rule in C# is
> written `(!Male) ? femaleSlot : maleSlot`, e.g. body 52 → `(!Male) ? 172 : 171`. So **Male →
> first number, Female → second**: 52→{M:171,F:172}, 53→{M:175,F:176}, 210→{M:178,F:177},
> 211→{M:182,F:181}, 222→{M:201,F:200}.

**Data coverage (checked against `nextbot/terraria_render/data/equip_slots.json`):** the
following body slots are actually present in our data **and** have an extension — so this is a
required feature, not an edge case (22 slots):

```
52, 53, 73, 81, 89, 168, 182, 187, 198, 200, 201, 202, 205, 207, 209, 210, 211,
218, 222, 225, 236, 237
```
All 22 extension `Armor_Legs_{slot}.png` files **exist** in `assets/` (verified: 149,150,151,
160,161,162,163,164,169,170,171,172,173,174,175,176,177,178,181,182,186,195,200,201,206,221,223
— none missing). Body slot **251 is NOT in our data** (so its extension 238 / back-coat 239 are
unused).

Ready-to-wire Python map:
```python
# body equip slot -> long-coat leg-armor slot (None if not gender; tuple = (male, female))
_LONGCOAT_EXT = {
    200:149, 201:150, 202:151, 209:160, 207:161, 198:162, 182:163, 168:164,
    73:170, 81:169, 89:186, 187:173, 205:174, 218:195, 225:206, 236:221, 237:223,
    251:238,
    52:(171,172), 53:(175,176), 210:(178,177), 211:(182,181), 222:(201,200),
}
def longcoat_ext_slot(body_slot, male):
    v = _LONGCOAT_EXT.get(body_slot)
    if v is None:
        return None
    return (v[0] if male else v[1]) if isinstance(v, tuple) else v
```

---

## B) Draw rule (idle, offline composite)

`DrawPlayer_16_ArmorLongCoat` (`PlayerDrawLayers.cs:1791-1821`) for the **body** path:

```csharp
int ext = GetMatchingBodyExtension(drawinfo, drawinfo.drawPlayer.body);
if (ext != -1) {
    Main.instance.LoadArmorLegs(ext);
    DrawData cdd = new DrawData(
        TextureAssets.ArmorLeg[ext].Value,         // Armor_Legs_{ext}.png
        <legPosition expr> + legPosition + legVect,
        drawinfo.drawPlayer.legFrame,              // idle = (0,0,40,56)
        drawinfo.colorArmorBody,                   // tint  = body lighting color
        drawinfo.drawPlayer.legRotation,           // 0 at idle
        drawinfo.legVect,                          // origin = (20,28)
        1f, drawinfo.playerEffect);
    cdd.shader = drawinfo.cBody;                   // DYE = body dye, not leg dye
    DrawLongCoat(ref drawinfo, ref cdd, ext);      // adds cdd to cache (+ optional glowmask)
}
```

Mapped to the compositor's conventions (see `terraria_render_spec.md` §D — at idle the big
position expression cancels, `legFrame=(0,0,40,56)`, origin `(20,28)`, all base layers overlay
1:1 with no per-layer offset):

- **Texture:** `Armor_Legs_{extSlot}.png` (the same vertical-strip leg sheet, **40×1120 = 20
  frames of 40×56** — verified for 149/170/164/52/18). Use sheet-style framing (column sheet →
  top frame), exactly like pants/shoes/skin-coat.
- **Frame:** idle leg frame = `Rectangle(0, 0, 40, 56)` (top frame). Same as `Armor_Legs_{leg}`
  for normal leggings.
- **Position / origin:** identical to the body sprite — overlay at frame top-left `(0,0)` with
  origin `(20,28)`. **No extra offset** vs the torso. (`legPosition`/`legRotation` are 0 at idle.)
- **Tint:** `colorArmorBody` — the **body** armor's lighting color (same tint the torso uses),
  **not** the leg color. In the offline still this is `Color.White` (full bright) unless you
  model lighting.
- **Dye:** `cBody` — the **body** dye shader id (NOT `cLegs`). Resolve via `dyes.json[bodyDyeNetId]`
  and apply with the body dye, same `apply_dye` call the torso uses.
- **GlowMask:** `DrawLongCoat` adds a second pass only for `ext == 238` (ChickenBones glow mask
  363) — not in our data, ignore.

### Where it sits in the back-to-front order (CONFIRMED)
From `LegacyPlayerRenderer.DrawPlayer_UseNormalLayers` (lines 192-207), back→front:

```
13_ArmorBackCoat   (192)  ← behind everything (back of robe/coat)   [N/A for our data, §C]
12_Skin            (193)
13_Leggings / 14_Shoes (197-202)   (robe order swaps these two; see note)
15_SkinLongCoat    (205)  ← skin coat tails (Players[var,14]), only var 3/7/8
16_ArmorLongCoat   (206)  ← THE FRONT LONG-COAT (this spec)  — drawn BEFORE torso
17_Torso           (207)  ← body armor sprite (drawn ON TOP of the coat)
```

So the **front long-coat draws immediately BEFORE the torso → it ends up BEHIND the body sprite**
in the final image (the body armor overlaps the top of the coat; the skirt extends below). This
matches the existing compositor's step 5 (skin long-coat, layer 14) which is drawn right before
the torso (step 6). The armor long-coat is the armor analogue of that step and belongs in the
**same slot, just after the skin-coat and before the torso**.

> Note on the robe leg-order swap (lines 194-198): when `wearsRobe && body != 166`, shoes draw
> before leggings; and `PlayerDrawSet` sets `cLegs = cBody` for robes (`PlayerDrawSet.cs:285-287`).
> For a flat idle composite this ordering swap is cosmetically irrelevant (both are behind the
> coat/torso), but if you mirror the robe behavior, also set the leggings dye = body dye when the
> body is a robe.

### Compositor wiring (drop-in, matches `render_character` style)
Add a step **between** the skin long-coat (step 5) and the torso (step 6):

```python
# 5b. armor long-coat (robe/coat skirt): leg-armor sheet keyed on BODY slot, BODY dye/tint.
ext_slot = longcoat_ext_slot(body_slot, male=comp.var in _MALE) if body_slot else None
if ext_slot is not None:
    comp.draw_armor(f"Armor_Legs_{ext_slot}", "col", body_dye)   # tint colorArmorBody, dye cBody
```

- `body_slot` is the already-resolved displayed body slot (`armor["body"]` → its slot).
- Use the **same `body_dye`** that the torso uses (step 6), and the body tint (the compositor's
  white/lighting color for body layers — same as the torso path).
- Cell key `"col"` (the column/leg cell) matches how `Armor_Legs_{leg}` leggings are already drawn
  in step 4 — the leg sheet is a vertical strip so it resolves to the idle top frame.
- Note `male` is needed only for the 5 gender-conditional bodies (52,53,210,211,222). Derive from
  the skin variant (`comp.var in _MALE`) exactly as the rest of the compositor does.

---

## C) Back-coat (`DrawPlayer_13_ArmorBackCoat`) — N/A for our data, documented

`GetMatchingBodyExtensionBack(drawinfo, bodyValue)` (`PlayerDrawLayers.cs:1838-1846`) is a
**single-entry** map:

```csharp
int result = -1;
if (bodyValue == 251)   // keyed on drawPlayer.coat (passed as bodyValue)
    result = 239;
return result;          // every other value -> -1 (no back-coat)
```

- It is called as `GetMatchingBodyExtensionBack(ref drawinfo, drawinfo.drawPlayer.coat)`
  (`:1442`) — i.e. keyed on the **`coat`** slot, the only back-coat being **coat 251 → leg-armor
  slot 239**, drawn with `colorArmorBody` + `cCoat`, positioned like the body, at the very back
  (layer 13, line 192).
- **Our data exposes no separate `coat` slot** — `equip_slots.json` items map only to
  `head`/`body`/`legs` (no `coat`), and there is no `coat` field in the appearance/equipment
  input. **Body slot 251 is also absent from our data.** Therefore:
  - **`DrawPlayer_13_ArmorBackCoat` produces nothing for any input we can render → omit it.**
  - The second `coat` pass inside `DrawPlayer_16_ArmorLongCoat` (`:1808-1820`, keyed on
    `drawPlayer.coat`) is likewise a no-op for us — only the first **body**-keyed pass matters.

If a `coat` slot is ever added to the data model, the back-coat would be: `if coat == 251: draw
Armor_Legs_239` at the **very back** (before `12_Skin`), tint `colorArmorBody`, dye `cCoat`. Until
then it is correctly skipped.

---

## D) Quick reference summary

| | Front long-coat (layer 16) | Back-coat (layer 13) |
|---|---|---|
| keyed on | `drawPlayer.body` (+ `coat`, no-op for us) | `drawPlayer.coat` |
| map fn | `GetMatchingBodyExtension` (23 entries, 5 gendered) | `GetMatchingBodyExtensionBack` (251→239 only) |
| texture | `Armor_Legs_{extSlot}.png` (40×1120 strip, idle frame) | `Armor_Legs_239.png` |
| frame / origin | `(0,0,40,56)` / `(20,28)`, 1:1 overlay on body | same |
| tint | `colorArmorBody` (white offline) | `colorArmorBody` |
| dye | `cBody` (body dye) | `cCoat` |
| order | after skin-coat (15), **before torso (17)** → behind body sprite | very back, before skin (12) |
| our data | **needed — 22 body slots, all textures present** | **N/A — no coat slot, no body 251** |
