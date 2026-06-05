# Research: Noise-dye source-luma / shading-loss bug (ArmorGel & noise-pass family)

- **Query**: Why does a noise dye (Bloodbath / ArmorGel) on PumpkinShirt flatten or
  invert the armor's built-in arm-vs-body shading? Find the exact Gel source→output
  formula by reverse-engineering, locate our inversion root cause (file:line), enumerate
  affected passes, and give a minimal fix spec with zero impact on validated dyes.
- **Scope**: mixed (reverse-engineer compiled `ps_2_0` bytecode + audit our numpy interpreter)
- **Date**: 2026-06-04

## TL;DR

- **One-line root cause**: `dye_noise._dst()` (nextbot/terraria_render/dye_noise.py:259–266)
  applies the write-mask but **ignores the `_sat` (saturate / clamp-to-[0,1]) result
  modifier** in the dst token (bit 20). ArmorGel's `mul r1.x_sat, r1.x, c18.x`
  (instruction [50]) is meant to clamp to 1.0; un-clamped it yields ~11.7, which the very
  next instructions **square** ([57] `mul r1.x,r1.x,r1.x`) and multiply, exploding `oC0` to
  ~5.2 million. After premult-divide every pixel collapses to ≈`[255, ~25, ~25]`, **erasing
  the source-luma term** (and even inverting it: the darker forearm ends up *brighter* than
  the lighter torso). The Gel formula itself **does** use source luma — our interpreter just
  destroys it numerically.
- **Affected passes (run the bytecode + contain `_sat`)**: **ArmorGel, ArmorFog,
  ArmorPhase, ArmorShiftingSands, ArmorShiftingPearlsands, ArmorStardust, ArmorTwilight**.
  Critically broken (clip-to-white / non-monotonic): **Gel, ShiftingSands,
  ShiftingPearlsands, Twilight**. Affected but still monotonic (subtler color/level error):
  **Fog, Phase, Stardust**. **Not affected**: ArmorNebula, ArmorVortex, ArmorHallowBoss
  (no `_sat` in their bytecode). **ArmorSolar** has `_sat` in its blob but its blob is **not
  executed** at runtime (dye.py routes Solar to the APPROX `_solar`), so it is unaffected.
- **Fix**: one clause in `_dst` (clamp `val` when bit 20 set). Verified to restore correct
  monotonic shading. **Zero impact** on the validated ArmorColored family / gradient /
  rainbow / invert — those never touch `dye_noise` (separate numpy path in dye.py).

---

## Findings

### Files Found

| File Path | Description |
|---|---|
| `nextbot/terraria_render/dye_noise.py` | `ps_2_0` bytecode interpreter for the noise passes. **`_dst` at :259–266 is the bug site.** |
| `nextbot/terraria_render/dye.py` | Dye orchestration; `_noise_pass` (:533), `_gel` (:586), `apply_dye` (:757) route noise dyes into `dye_noise`. Static dyes use a disjoint numpy path. |
| `nextbot/terraria_render/_build/gen_noise_shaders.py` | DEV-only baker: extracts each pass's blob + `pres_inputs` into `data/noise_shaders.json`. ArmorGel `pres_inputs` at :48–49. |
| `nextbot/terraria_render/data/noise_shaders.json` | Baked `ps_2_0` blobs (base64) + preshader input maps. Source of all disasm below. |
| `temp/decomp/full/Terraria.Initializers/DyeInitializer.cs` | Shader registration. **Bloodbath = netId 4663 → `ArmorGel`, `UseColor(2.6,0.6,0.6)`, `UseSecondaryColor(0.2,-0.2,-0.2)`** (:112–113). |
| `temp/decomp/full/Terraria.Graphics.Shaders/ArmorShaderData.cs` | The C# uniform binding (`uColor`,`uSecondaryColor`,`uSaturation`,`uSourceRect`,`uImageSize0/1`, `texld` of `uImage0`=armor / `uImage1`=`Misc/noise`). |
| `temp/xnb_probe/ps_disasm.py` | The validated DX9 `ps_2_0` disassembler (reused to produce the disasm in this doc). |

### Bloodbath → ArmorGel registration (reverse-engineered)

`DyeInitializer.cs:112-113`:
```csharp
GameShaders.Armor.BindShader(4663, new ArmorShaderData(pixelShaderRef, "ArmorGel"))
    .UseImage("Images/Misc/noise").UseColor(2.6f, 0.6f, 0.6f).UseSecondaryColor(0.2f, -0.2f, -0.2f);
```
So Gel samples `uImage0` = the armor sprite (s0) and `uImage1` = `Misc/noise` (s1); `uColor`
= over-unity red, `uSecondaryColor` = slightly-negative near-black.

---

### 1. ArmorGel — exact `source → output` formula (from the `ps_2_0` bytecode)

Full disasm of the baked `ArmorGel` blob (3896 B; const/sampler labels from its CTAB). The
preshader (`PRES`/`FXLC`) fills `c0..c10` (uv-offset/scale derived consts) and the effect set
binds `c11=uSecondaryColor`*, `c12=uColor`, `c13=uSecondaryColor`, `c14=uSourceRect`,
`c15=uImageSize0`. The literal `def`s are `c16..c20`. (`r0` = the **armor source pixel** from
`texld r0, t0, s0`.)

```
def c16 (-0.0, 0.0025, 0.33333, 4.0)   def c17 (0.5, 0.25, 1.5, 2.0)
def c18 (10.0, -2.0, 3.0, 0.75)        def c19 (-0.45, 1.0, 0.7, -0.2)   def c20 (0.1875,0,0,0)
texld r0, t0, s0                       ; r0 = SOURCE armor pixel (premult rgba)
... (c0..c10 = jitter/uv offsets; 5 noise tap UVs r1/r3/r4/r5 + r2 = noise tap UV) ...
add  r2.z, r0.y, r0.x                  ; --- SOURCE LUMA ---
add  r2.z, r0.z, r2.z                  ;   r2.z = r0.r + r0.g + r0.b
mul  r2.w, r2.z, c16.z                 ;   r2.w = (r+g+b) * 0.33333   = AVG SOURCE LUMA  L
mul  r3.z, r2.w, c4.x                  ; noise-uv jitter scaled by L (sub-pixel only)
...
texld r1,r1,s0  texld r3,r3,s0  texld r4,r4,s0  texld r5,r5,s0   ; 4 neighbor source taps
texld r6, r2, s1                       ; r6 = NOISE tap (uImage1)
; --- alpha-coverage / edge term from the 5 source taps (max of .w) ---
max ... -> r2.y (coverage)             ; r1.x..r2.y = max chain of tap alphas
mul/mad r1.* with c17                  ; weighted edge-highlight scalar
mul  r0.xyz, r2.w, uColor              ; *** BASE COLOR = L * uColor ***   (shading carrier)
mul  r1.x, r6.x, r1.x                  ; noise * edge term
mad  r1.y, r1.x, 0.5, r0.w  ...        ; build highlight scalar r1.x (jelly sheen)
add  r1.x, r1.x, c19.w(-0.2)
mul  r1.x_sat, r1.x, c18.x(10.0)       ; [50] *** r1.x = saturate((r1.x-0.2)*10) ∈ [0,1] ***
add  r1.y, r1.y, c19.x(-0.45)
cmp  r1.y, r1.y, c19.y(1.0), c19.z(0.7)
mul  r1.y, r6.x, r1.y                  ; noise-masked highlight color weight
mov  r3.xyz, c11(uSecondaryColor)
mad  r3.xyz, r1.y, c11, uSecondaryColor; highlight tint
mad  r1.y, r1.x, c18.y(-2), c18.z(3)   ; smootherstep on r1.x:  (3 - 2*r1.x)
mul  r1.x, r1.x, r1.x                  ; [57] r1.x = r1.x^2     (DEPENDS ON [50] SAT)
mul  r1.x, r1.y, r1.x                  ; [58] r1.x = r1.x^2*(3-2*r1.x)  smootherstep s∈[0,1]
mov  r3.w, c19.y(1.0)
mul  r1, r3, r1.x                      ; highlight rgba * s
mul  r1, r2.y, r1                      ; * coverage
mul  r2.x, r1.w, c18.w(0.75)
mad  r2.x, r2.z(=r+g+b), -c20.x(0.1875), r2.x   ; mix factor f, REDUCED by source luma
mad  r1, r1, c18.w(0.75), -r0          ; (highlight*0.75 - base)
mad  r3, r2.x, r1, r0                  ; *** OUT = lerp(base, highlight, f) = r0 + f*(hi-base) ***
mul  r0.x, r2.x, r1.w   cmp r3.w, r0.x, r3.w, r0.w
mul  r0, r3, v0                        ; * vertex color (white offline)
mov  oC0, r0
```

**Plain-English Gel formula:**
```
L          = (src.r + src.g + src.b) / 3                       # AVERAGE source luma (premult)
base.rgb   = L * uColor                                        # ← shading carrier: dark src → dark base
highlight  = uSecondaryColor-tinted jelly sheen, gated by:
               s = smootherstep( saturate((edge*noise - 0.2)*10) )   # [50] _sat is ESSENTIAL here
               coverage = max(neighbor source alphas)
f          = 0.75*highlight.a - 0.1875*(src.r+src.g+src.b)     # highlight mix, attenuated by source luma
out.rgb    = base + f * (0.75*highlight - base)                # lerp(base, highlight, f)
out.a      = src.a (with cmp tweak)
```
The **source enters twice as a shading signal**: directly as `L*uColor` (base) and as a
luma-driven *reduction* of the highlight mix `f`. Both make brighter source pixels read
brighter and darker source pixels read darker — i.e. Gel **does** preserve the armor's
built-in arm/body shading, exactly as observed in-game. The weight on source luma is `L`
(0.333·sum) for the base and `−0.1875·sum` for the mix.

*Note: `c11` and `c13` both label `uSecondaryColor`; the preshader writes the working copy
used by `mad r3.xyz`, consistent with the `pres_inputs` map (gen_noise_shaders.py:48-49).*

---

### 2. Why our implementation flattens/inverts it — `_sat` is dropped (root cause, file:line)

`nextbot/terraria_render/dye_noise.py:259-266`:
```python
def _dst(regs: dict, tok: int, val: np.ndarray) -> None:
    rt, rn = _regtype(tok), tok & 0x7FF
    cur = regs[(rt, rn)].copy()
    mask = (tok >> 16) & 0xF          # ← only the write-mask is honored
    for i in range(4):
        if mask & (1 << i):
            cur[..., i] = val[..., i]  # ← raw val written; NO saturate
    regs[(rt, rn)] = cur
```

The `ps_2_0` dst token carries a **result modifier** in bits 20–23
(`D3DSPDM_SATURATE = 0x1`). Verified against ArmorGel instruction [50]:

| instr | dst token | writemask | result-mod | meaning |
|---|---|---|---|---|
| `[50] mul r1.x_sat` | `0x80110001` | `0b0001` | `0b0001` | **saturate** to [0,1] |
| `[31]/[32] texld`   | `0x800f0001` | `0b1111` | `0b0000` | no modifier (proves the bit is per-instr) |
| `[44] mul r0.xyz`   | `0x80070000` | `0b0111` | `0b0000` | base color — never saturated |

**Cascade (instrumented single-pixel trace of the *real* interpreter on the dark-green
forearm `[22,74,5]`):**
```
[44] mul r0.xyz = L*uColor      -> r0 = [0.343, 0.079, 0.079, 1.0]   ✓ correct base (dim)
[49] add r1.x                   -> r1.x = 1.1708
[50] mul r1.x_SAT, r1.x, 10.0   -> r1.x = 11.7083   ✗ should be saturate(...)=1.0
[57] mul r1.x, r1.x, r1.x       -> r1.x = 137.0851  ✗ (11.7^2) — explosion seeded here
[58] mul r1.x, r1.y, r1.x       -> r1.x = -2798.82
[60..65] mad r3 = base + f*(hi-base) -> r3 = [5_215_872, 563_499, 563_499, 4_408_540]  ✗✗✗
[69] oC0 = [5.2e6, 5.6e5, 5.6e5, 4.4e6]
```
After `dye.py:_noise_pass` premult-divides by the (equally huge) alpha and clips, the tiny
`L*uColor` base is swamped: **every pixel** lands at `[255, ~25, ~25]`. Because the explosion
magnitude is dominated by the *noise/edge* term (not source luma), the residual ordering is
essentially random and here **inverts**:

| source pixel | source avg-luma | BROKEN output (no `_sat`) | output avg-luma |
|---|---|---|---|
| forearm `[22,74,5]` (dark) | 34 | `[255, 33, 33]` | **107** |
| torso `[181,126,37]` (bright) | 115 | `[255, 21, 21]` | **99** |

→ darker source becomes the **brighter** red — the exact "hand/body merge + reversed shading"
symptom. This is the **same bug family** as the earlier gradient/Stardust "bytecode right,
post-processing wrong" issues, but the defect is in the **opcode result-modifier handling**,
not the input mapping. The Gel input mapping (uImage0=source via `texld …, s0`; uImage1=noise)
is correct; `uv` for `s0` is irrelevant because the interpreter returns the whole cropped
`src_rgba` for the source sampler (dye_noise.py:321).

**With the `_sat` fix applied (same interpreter, same params):**

| source pixel | FIXED output | avg-luma | raw `oC0` (in-range) |
|---|---|---|---|
| forearm (dark) | `[181, 23, 23]` | **76** | `[0.71, 0.09, 0.09, 1.0]` |
| torso (bright) | `[249, 42, 42]` | **111** | `[0.98, 0.17, 0.17, 1.0]` |

→ torso brighter than forearm (**monotonic with source luma**), `oC0` back in [0,1], shading
preserved. Matches in-game.

---

### 3. Blast radius — per-pass `_sat` audit + PumpkinShirt monotonicity test

Scanned every blob in `noise_shaders.json` for `_sat` (bit 20) and ran each affected pass on
the forearm(dark)/torso(bright) samples with `_sat` OFF (current) vs ON (fixed). "Monotonic"
= bright source → brighter output (the property Gel/static dyes must satisfy).

| Pass | runs bytecode? | #`_sat` | BROKEN (no-sat) fa→to | FIXED (sat) fa→to | Verdict |
|---|---|---|---|---|---|
| **ArmorGel** | ✅ `_gel` | 1 (`[50]`) | `[255,33,33]`→`[255,21,21]` (107→**99**, inverted) | `[181,23,23]`→`[249,42,42]` (76→111) | **BROKEN → fixed** |
| **ArmorShiftingSands** | ✅ `_shifting_sands` | 2 | `[255,255,255]`→`[255,255,255]` (clip white) | `[148,105,63]`→`[159,113,68]` | **BROKEN → fixed** |
| **ArmorShiftingPearlsands** | ✅ `_shifting_pearlsands` | 3 | `[0,255,255]`→`[0,255,255]` (clip) | `[74,53,93]`→`[106,76,117]` | **BROKEN → fixed** |
| **ArmorTwilight** | ✅ `_twilight` (hair dye 3039/3259) | 3 | `[255,255,255]`→`[255,255,255]` (clip white) | `[22,7,40]`→`[29,15,46]` | **BROKEN → fixed** |
| **ArmorFog** | ✅ `_fog` | 2 | `[55,65,52]`→`[100,93,81]` (57→91) | `[59,64,57]`→`[100,93,81]` (60→91) | affected, monotonic; small level shift |
| **ArmorPhase** | ✅ `_phase` | 1 | `[108,117,133]`→`[94,163,255]` | identical here | affected; benign on this sample (sat term not hit) |
| **ArmorStardust** | ✅ `_stardust` (emissive, `_PILLAR_TIME=1.0`) | 1 (`[23]`) | `[0,0,0]`→`[0,35,127]` | `[27,40,67]`→`[92,138,229]` | affected; brightens correctly |
| ArmorNebula | ✅ `_nebula` | **0** | — | — | not affected |
| ArmorVortex | ✅ `_vortex` | **0** | — | — | not affected |
| ArmorHallowBoss | ✅ `_hallow_boss` | **0** | — | — | not affected |
| ArmorSolar | ❌ APPROX `_solar` (blob NOT run) | 3 | n/a | n/a | **not affected** (see note) |

Notes:
- **ArmorNebula / ArmorVortex** build the noise contribution as an **additive emissive** term
  (`mad r2.xyz, uColor, srcLuma, -r0` then add a `noise*uSecondary*5` cloud) with **no `_sat`**
  in the program, so the interpreter already runs them correctly. Their source-luma handling
  (`uColor * (r+g+b)/3`, Nebula c8.x=0.333; Vortex c10.z=0.333; Stardust c8.w=0.666) is intact.
- **ArmorSolar**: although `ArmorSolar`'s blob contains 3 `_sat` instructions, `dye.py`
  deliberately routes Solar to the hand-written APPROX `_solar` (dye.py:432, `apply_dye` :839)
  because the real Solar glow is carried on the (offline-white) vertex color `v0` and collapses
  without live `v0`. The Solar blob is therefore **never executed at runtime**; the `_sat` fix
  has **no effect** on Solar's output. (If Solar is ever switched to the bytecode path, the fix
  becomes a prerequisite.)
- The dst-token result-modifier nibble also defines `PARTIALPRECISION (0x2)` and `CENTROID
  (0x4)`; neither appears in these blobs and neither needs emulation. Only `SATURATE (0x1)`
  matters.

---

### 4. Fix specification (minimal, correct, zero-regression)

**Where**: `nextbot/terraria_render/dye_noise.py`, function `_dst` (lines 259–266) — the single
chokepoint every `ps_2_0` write goes through (called at :322 for `texld`, :365 for ALU ops).

**What**: honor the saturate result-modifier bit (dst token bit 20) before writing.

```python
def _dst(regs: dict, tok: int, val: np.ndarray) -> None:
    rt, rn = _regtype(tok), tok & 0x7FF
    cur = regs[(rt, rn)].copy()
    mask = (tok >> 16) & 0xF
    if (tok >> 20) & 0x1:            # D3DSPDM_SATURATE: clamp result to [0,1] before masking
        val = np.clip(val, 0.0, 1.0)
    for i in range(4):
        if mask & (1 << i):
            cur[..., i] = val[..., i]
    regs[(rt, rn)] = cur
```

Rationale / why this is the right altitude:
- `_sat` is a *result* modifier: it clamps the computed value **before** the write-mask selects
  components. Clamping `val` (full vec4) then masking is exactly the hardware order, and is safe
  even when only one channel is written (the unwritten lanes are discarded anyway).
- Clamp range is **[0,1]** for `ps_2_0` (`_sat` is fixed-range, unlike `_pp`/`_centroid`).
- Applying it inside `_dst` covers `texld` and all ALU ops uniformly; no opcode-by-opcode edits.

Alternative (not recommended): a one-line patch at each `_dst` callsite would duplicate logic
and risk missing the `texld` path; keep it in `_dst`.

**No `dye.py` change is required.** `_noise_pass` already premult-divides + clips the (now
in-range) `oC0`, so once `_sat` is honored the source-luma base survives untouched.

---

### 5. Regression notes (what must stay byte-identical)

- **Validated static dyes are provably unaffected.** ArmorColored / ColoredAndBlack /
  ColoredAndSilverTrim / BrightnessColored / *Gradient / *Rainbow / Invert / ColorOnly /
  Martian / Polarized / Mushroom / Wisp / HighContrastGlow / Flow / Living* / Acid / Hades /
  Mirage / Loki / Solar / Void all run **pure-numpy paths in dye.py** (`_armor_colored`,
  `_recolor_premul`, `_run`, `_brightness_*`, etc.) and **never import or call `dye_noise`**
  (confirmed: the only `dye_noise.run_noise_pass` callsite is `_noise_pass` at dye.py:553, used
  solely by the noise branches). The `_dst` change cannot reach them.
- **Nebula / Vortex / HallowBoss byte-output is unchanged** — their blobs contain no `_sat`
  instruction, so `(tok >> 20) & 0x1` is always 0 for them and the new branch is a no-op.
- **Gel / ShiftingSands / ShiftingPearlsands / Twilight WILL change** (they are currently
  wrong); that is the intended fix. **Fog / Phase / Stardust** outputs will shift slightly
  (now correct). Re-bake/refresh any golden PNGs that snapshot these 7 passes.
- Recommended regression assertion (cheap, catches re-introduction): for ArmorGel on a
  source with a dark cell and a bright cell, assert `out_avg_luma(bright) > out_avg_luma(dark)`
  and `max(oC0) <= ~1.5` (raw output stays near unity, never millions). The PumpkinShirt
  forearm `[22,74,5]` / torso `[181,126,37]` pair is a ready-made discriminator.
- Keep the existing emissive tone-map (`_emissive_tonemap`, dye.py:563) for Stardust — `_sat`
  fixes the in-shader clamp; the emissive lift is a separate, still-correct post step.

## Caveats / Not Found

- Bytecode was read from the **baked** `data/noise_shaders.json` blobs (identical to the
  source-of-truth `temp/xnb_probe/in/PixelShader.xnb`, per gen_noise_shaders.py). I did not
  re-extract from the raw XNB; if the JSON were ever stale the disasm would be too, but the
  blob byte-lengths match the documented passes.
- Numeric reproductions used a representative `src_rect=(0,0,40,56)` / `sheet_size=(360,224)`
  and `u_time` = each pass's production value (0, or `_PILLAR_TIME` for Stardust). The
  inversion/clip findings are robust to these (the `_sat` explosion is source-luma-independent),
  but exact post-fix RGB values will vary per cell/uv — treat the tables as
  directional (monotonicity, clip-vs-no-clip), not pixel-exact goldens.
- I did **not** modify any code (research-only). The fix block above is a spec for the
  implementer; it was validated by monkey-patching `_dst` in an isolated process.
