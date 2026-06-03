# Research: hairDye (hair-dye shader) — how to render `tsCharacter.hairDye`

- **Query**: How does `tsCharacter.hairDye` render? What does the stored int index, does it
  reuse the armor dye pixel passes, and what is the `hairColor`↔`hairDye` apply order so the
  compositor can wire it in?
- **Scope**: internal (decompiled Terraria source `temp/decomp/full/`) + bytecode disasm of
  the `ArmorTwilight` pass via `temp/xnb_probe/`
- **Date**: 2026-06-02

## TL;DR (the answer)

`tsCharacter.hairDye` is **NOT** a dye-item-id and it does **NOT** index the armor-dye passes.
It is a **1-based shader index into `GameShaders.Hair`** (a separate `HairShaderDataSet`), set by
`player.hairDye = (byte)item.hairDye` where `item.hairDye = GameShaders.Hair.GetShaderIdFromItemId(type)`
(`Terraria/Item.cs:48322`).

There are **exactly 12 hair dyes** bound (full list in §1). **11 of the 12 are
`LegacyHairShaderData` with `_shaderDisabled = true`** — they run **no pixel shader at all**; they
only compute a replacement hair *color* on the CPU (`GetColor`) and the hair is drawn normally
with that color. **Only `hairDye == 12` (item 3259, "Twilight Hair Dye") runs a real pixel-shader
pass** — `ArmorTwilight`, which lives in the **same 64-pass `Main.PixelShaderRef` effect we already
disassembled** and samples `Images/Misc/noise`.

So the compositor fix is two-part and small:

1. **Hair tint color** — replace `hairColor` with `GameShaders.Hair.GetColor(hairDye, …)` for
   `hairDye ∈ 1..11` (each is a tiny deterministic color rule; §1 gives the static value or
   formula for an offline still). For `hairDye == 0` keep the current `hairColor` tint.
2. **Twilight only** (`hairDye == 12`): after tinting, run a pixel pass on the hair frame. It is
   **a noise-sampling pass** (`ArmorTwilight`), so it depends on the same `Misc/noise` texture as
   Topic B; until that lands, a flat tint by its `uColor=(0.5,0.1,1.0)` is the static approximation.

**Most characters have `hairDye = 0` → no-op** (current code already correct for them). The dye is
non-trivial only for the ~12 dye items, and only #12 needs the shader path.

---

## 1) The `hairDye` value → shader mapping (`Terraria.Initializers.DyeInitializer.cs`)

`GameShaders.Hair` is a `HairShaderDataSet` (`Terraria.Graphics.Shaders/GameShaders.cs:9`). Binds
happen in `DyeInitializer.LoadLegacyHairdyes()` (idx 1..11) then `LoadHairDyes()` (idx 12), in this
exact order — `BindShader` assigns `++_shaderDataCount`, so the index is the bind ordinal:

| hairDye | item id | class | shaderDisabled | pixel pass | color rule (for a static still) |
|---|---|---|---|---|---|
| 0 | — | (none) | — | — | vanilla: tint hair by `hairColor` |
| 1 | 1977 Life Hair Dye | Legacy | **yes** | — | `R = statLife/statLifeMax2*235+20, G=B=20` → full-life ≈ `(255,20,20)` red |
| 2 | 1978 Mana Hair Dye | Legacy | **yes** | — | from mana ratio; full-mana = `(50,75,255)` blue |
| 3 | 1979 Depth Hair Dye | Legacy | **yes** | — | color by world depth (player.Center.Y); surface ≈ `(116,160,249)` |
| 4 | 1980 Money Hair Dye | Legacy | **yes** | — | color by coins held; broke ≈ `(226,118,76)` |
| 5 | 1981 Time Hair Dye | Legacy | **yes** | — | color by `Main.time`/day-night; dawn ≈ `(1,142,255)` |
| 6 | 1982 Team Hair Dye | Legacy | **yes** | — | `Main.teamColor[player.team]`; no team (team 0) = `Color.White` passthrough → keep `hairColor` |
| 7 | 1983 Biome Hair Dye | Legacy | **yes** | — | color by `Main.waterStyle`; default style ≈ `(28,216,94)` green |
| 8 | 1984 Party Hair Dye | Legacy | **yes** | — | **constant** `(244,22,175)` magenta (= `TeamDyeShaderIndex`; only spawns dust, see note) |
| 9 | 1985 Rainbow Hair Dye | Legacy | **yes** | — | `(Main.DiscoR, Main.DiscoG, Main.DiscoB)` cycling; pick any e.g. `(255,0,0)` for a still |
| 10 | 1986 Speed Hair Dye | Legacy | **yes** | — | lerp `hairColor`→`(75,255,200)` by speed; at rest = `hairColor` |
| 11 | 2863 Twilight Hair Dye? (Martian) | Legacy | **yes** | — | `lighting=false`; averages local `Lighting.GetColor` with `hairColor`; offline ≈ `hairColor` |
| **12** | **3259 Twilight Hair Dye** | **TwilightHairDyeShaderData** | **no** | **`ArmorTwilight`** | `UseColor(0.5,0.1,1.0)`, `UseImage("Images/Misc/noise")` |

Sources: `Terraria.Initializers/DyeInitializer.cs:143-421` (all 12 `Hair.BindShader` calls — grep
confirms there are no others anywhere in the tree); `Terraria.Graphics.Shaders/HairShaderDataSet.cs`
(`BindShader` → `++_shaderDataCount`, `GetShaderIdFromItemId`, `Apply`, `GetColor`).

> **`hairDye` is therefore a small dense index 0..12**, NOT a sparse item id. If the JSON the
> compositor receives stores the *item id* (1977…3259) instead, convert with the table above; if it
> stores the already-resolved `player.hairDye` byte (which is what the netcode/`.plr` file stores —
> `Player.cs:53824` writes `newPlayer.hairDye`), use it directly as the 1..12 index.

> **Note on idx 8 (`TeamDyeShaderIndex`)**: `Player.UpdateHairDyeDust` (`Player.cs:24012`) checks
> `hairDye == TeamDyeShaderIndex` only to spawn cosmetic dust — irrelevant to a static render.

### Do hair dyes reuse the armor dye pixel passes?

**No for 11/12; yes (one shared pass) for #12.** The legacy dyes (1..11) bind
`LegacyHairShaderData`, whose ctor sets `_shaderDisabled = true`
(`Terraria.GameContent.Dyes/LegacyHairShaderData.cs:12-16`). `HairShaderDataSet.Apply` then takes
the disabled branch and calls `Main.pixelShader.CurrentTechnique.Passes[0].Apply()` (the default/no-op
pass) — i.e. **no recolor shader, the hair is just drawn with the CPU-computed color**.

Only #12 (`TwilightHairDyeShaderData`, `Terraria.GameContent.Dyes/TwilightHairDyeShaderData.cs`)
constructs with `new TwilightHairDyeShaderData(Main.PixelShaderRef, "ArmorTwilight")` — i.e. it uses
the **same effect file and the same technique** as every armor dye. `ArmorTwilight` IS one of the 64
passes in `temp/xnb_probe/in/PixelShader.xnb` (confirmed present in the pass list). It is NOT one of
the armor *items'* passes (no armor dye binds `ArmorTwilight`), but it is the same shader program
family — a noise-sampling pass, structurally identical to ArmorVortex/Phase (see §3 and
`noise_dyes_spec.md`). `HairShaderData` exposes the same uniforms as `ArmorShaderData`
(`uColor/uSecondaryColor/uSaturation/uTime/uSourceRect/uImageSize0/uImageSize1/uDirection`,
`Terraria.Graphics.Shaders/HairShaderData.cs:55-72`), so the existing dye machinery applies.

---

## 2) `hairColor` ↔ `hairDye` apply order (`GetHairColor` / draw path)

The hair is drawn in two coupled steps; the dye participates in BOTH:

### Step A — the tint color (`GetColor`, replaces or folds `hairColor`)
`Player.GetHairColor(useLighting)` (`Player.cs:54949`):
```csharp
Color light = Lighting.GetColor(tileX, tileY);
return GameShaders.Hair.GetColor(hairDye, this, useLighting ? light : Color.White);
```
And in the draw set, the per-frame hair color is
`colorHair = GameShaders.Hair.GetColor(drawPlayer.hairDye, drawPlayer, Color.White)`
(`Terraria.DataStructures/PlayerDrawSet.cs:1484, 1507`).

`HairShaderDataSet.GetColor(shaderId, player, lightColor)`:
- **`shaderId == 0`** (no dye) → `lightColor * player.hairColor` → **tint = hairColor** (× lighting).
- **`shaderId` is a legacy dye (1..11)** → `LegacyHairShaderData.GetColor` runs
  `_colorProcessor(player, player.hairColor, ref lighting)` which **returns a NEW color computed
  from `player.hairColor`** (some rules ignore `hairColor` entirely, e.g. Life/Party; others lerp
  from it, e.g. Speed). If the processor leaves `lighting == true`, the result is then × `lightColor`.
  → **the dye REPLACES `hairColor`'s role**; you do NOT also multiply by `hairColor` again.
- **`shaderId == 12` (Twilight)** → base `HairShaderData.GetColor` =
  `new Color(lightColor.ToVector4() * player.hairColor.ToVector4())` → **tint = hairColor** (× light),
  same as vanilla; the *recolor* happens in the pixel pass (Step B), not the vertex color.

### Step B — the pixel pass (only for `shaderId == 12`)
The hair `DrawData` is packed with `hairShaderPacked = PackShader(drawPlayer.hairDye, HairShader)`
(`Terraria.DataStructures/PlayerDrawHeadSet.cs:107`). At draw, `PlayerDrawHelper.SetShaderForData`
unpacks it and (when `player.head != 0`) calls
`GameShaders.Hair.Apply((short)localShaderIndex, player, cdd)`
(`Terraria.DataStructures/PlayerDrawHelper.cs:38-48`). For idx 1..11 this hits the
`_shaderDisabled` branch (default pass, no recolor). For idx 12 it runs `ArmorTwilight` on the hair
sprite, with the drawn **vertex color = `colorHair`** (the Step-A tint) as `v0`.

> When `player.head == 0` (no head armor) the code path swaps: it applies the *armor* head shader
> instead and forces `Hair.Apply(0,…)`. For our compositor (no live head-armor shader pass) this
> distinction doesn't matter — we always have the hair sprite + its tint + optionally the Twilight
> pass.

### Exact order for the compositor
```
1. frame   = hair sprite cell (straight-alpha RGBA)               # _frame(hair_file, 0)
2. tint    = hair_tint_color(hairDye, hairColor)                  # Step A, see lookup below
   frame   = multiply_rgb(frame, tint)                            # == current _tint(frame, hair_rgb)
3. if hairDye == 12:                                              # Step B, Twilight only
       frame = apply_dye(frame, {"pass":"ArmorTwilight",
                                 "color":[0.5,0.1,1.0]})          # reuses dye.py
4. composite frame over canvas
```
Where `hair_tint_color`:
- `hairDye == 0` → `hairColor` (current behavior — unchanged).
- `hairDye ∈ 1..11` → the **replacement color** from the §1 table (a constant or simple formula;
  for a deterministic still pick the representative value listed). Do **not** also multiply by
  `hairColor` (the legacy processor already consumed it).
- `hairDye == 12` → `hairColor` (Twilight keeps the vanilla tint; recolor is the pixel pass).

---

## 3) Wiring into the compositor (concrete)

The compositor already has the exact hooks:
- Tinting happens in `nextbot/terraria_render/compositor.py::draw_hair` →
  `hf = _tint(_frame(hair_file, 0), self.hair_rgb)` (line 228). `_tint` is an RGB multiply — this IS
  Step A for `hairDye == 0`.
- `self.hair_rgb` is already parsed from `appearance["hairColor"]` (line 195). `appearance` is the
  same dict that carries `hairDye` (the PRD/`_resolve_hair` already plumbs `appearance["hair"]`).

**Change set (read-only research — describing, not editing):**

1. Parse `hairDye` in `__init__` (e.g. `self.hair_dye = int(appearance.get("hairDye", 0))`).
2. Add a tiny lookup `hair_tint_color(hair_dye, hair_rgb) -> (r,g,b)` implementing the §1 table
   (mostly constants; for offline/static, the time/world-dependent ones use their representative
   value). This belongs next to `draw_hair` in `compositor.py` (it is appearance logic, not a dye
   pixel pass), OR in `data/` as a JSON table `hair_dye_colors.json` keyed by index 1..11 → rgb,
   loaded like the existing `_DYES`/`_HAIR`. The JSON-table approach is cleanest and matches the
   existing `_load_json` pattern.
3. In `draw_hair`: tint with the resolved color instead of always `self.hair_rgb`:
   ```python
   tint = hair_tint_color(self.hair_dye, self.hair_rgb)   # == hair_rgb when hair_dye in (0,12)
   hf = _tint(_frame(hair_file, 0), tint)
   if self.hair_dye == 12:                                # Twilight
       hf = apply_dye(hf, {"pass": "ArmorTwilight", "color": [0.5, 0.1, 1.0]})
   ```
   `apply_dye` is already imported (`compositor.py:17`).

4. **`apply_dye` needs an `ArmorTwilight` branch** (it currently has none — unknown pass → undyed,
   so today Twilight silently no-ops). Add it to the dispatch in `dye.py`. Until the noise texture
   is wired (Topic B / `noise_dyes_spec.md`), the static approximation is a flat tint by
   `uColor=(0.5,0.1,1.0)` — i.e. reuse the existing brightness/colored recolor, e.g.
   `_brightness_clip(arr_u8, (0.5,0.1,1.0))` or `_armor_colored(arr_u8, clip((0.5,0.1,1.0)), 1.0)`.
   Once `noise_dyes_spec.md` lands the per-pixel noise sampler, `ArmorTwilight` becomes accurate
   (its disasm is in `noise_dyes_spec.md §ArmorTwilight`).

> **Geometry note for the Twilight pass:** like the armor noise passes it reads
> `uImageSize0`/`uSourceRect`. For hair, `drawData.texture` is the **hair sheet**
> (`TextureAssets.PlayerHair[hair]`) and `sourceRect` is the hair frame. The compositor extracts a
> 40×56 idle cell from the *top* of the hair sheet (`_frame(hair_file, 0)`), so the effective
> `uSourceRect ≈ (0,0,40,56)` and `uImageSize0 = (hairSheetW, hairSheetH)`. The Twilight pass's
> spatial term only matters for the noise uv; the static approximation ignores it, and the accurate
> version uses the same uv recipe as `noise_dyes_spec.md` with the hair frame's rect.

### `ArmorTwilight` pass facts (from disasm, for the accurate version)
- CTAB: `uColor=c3, uImage0=s0, uImage1=s1(noise), uSourceRect=c4, uImageSize0=c5`. No `uTime`/`uSat`.
- Preshader: `c0(=OUTb0,1)= 1/uImageSize0` ; `c1,c2` derived from `uImageSize0·uSourceRect`-scaled
  noise-uv offsets (`OUTb4 = 0.004·…`, `OUTb8 = 0.02·…` — two noise taps at different scales).
- Body: computes `pos = t0*uImageSize0 - uSourceRect`, builds two noise UVs (`r0` at scale c0, `r1`
  at scale `0.125·… + c1`), does `texld r0,s1` and `texld r1,s1` (two noise samples), shapes them
  with `def c6=(0.125,-0.6,5,20) c7=(0.25,-0.7,0.35,0.05) c8=(-2,3,-0.4,2.5) c9=(0.0333,1,..)` into a
  glow weight, multiplies by `uColor=(0.5,0.1,1)`, adds over the source, `*v0`, `*src.a`. Same shape
  as ArmorVortex/Phase — a purple twilight shimmer. Full opcode trace is reproducible via
  `temp/xnb_probe/fx_parse.py` (target `"ArmorTwilight"`).

---

## Caveats / Not Found

- The legacy color rules (idx 1..11) are **dynamic** (life/mana/time/world/team/speed/lighting).
  For a static portrait there is no single "correct" frame; §1 lists a sensible representative per
  dye. If the renderer has access to the live player stats it can evaluate the exact rule (formulas
  are transcribed verbatim from `DyeInitializer.cs:153-420` in §1's source lines).
- Idx 8 (1984) is the one whose `GetShaderIdFromItemId` is cached as `TeamDyeShaderIndex`; its render
  color is the constant magenta `(244,22,175)` — the team logic at `Player.cs:24012` is dust-only.
- The `ArmorTwilight` pixel pass shares Topic B's dependency on `Images/Misc/noise` (256×256, decodes
  fine — see `noise_dyes_spec.md`). Accurate Twilight = "wire noise + add `ArmorTwilight` to the
  per-pixel sampler". Static Twilight = flat `(0.5,0.1,1.0)` tint. Either way the compositor change
  (steps 1-4) is required to stop ignoring `hairDye`.
