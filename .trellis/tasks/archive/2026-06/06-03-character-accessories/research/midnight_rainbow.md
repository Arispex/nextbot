# Research: MidnightRainbow dye (item 3556, pass `ArmorMidnightRainbow`) — real shader reverse + faithful upgrade

- **Query**: Reverse the *real* `ArmorMidnightRainbow` shader; upgrade `dye.py:_midnight_rainbow` from a static APPROX to the faithful bytecode path; prototype-validate.
- **Scope**: internal (package + `temp/xnb_probe` reverse tools) + bytecode disasm
- **Date**: 2026-06-05

## TL;DR (the one-liner)

**Can it run on the current interpreter as-is? NO — but only one thing is missing, and it is NOT an opcode.** Every pixel opcode and every preshader opcode `ArmorMidnightRainbow` uses is already implemented in `dye_noise.py`. The single gap: the interpreter collapses every `uImage0` (source) `texld` to the **center texel** (`dye_noise.py:323`, `res = src_rgba`), discarding the offset uv. MidnightRainbow is a **5-tap self-emboss** — the emboss magnitude *is* the entire output signal (the final `mul r0.xyz, r0.x(=emboss), rainbow`), so with center-collapsed taps the emboss is 0 and the sprite renders **fully black**. The minimal fix is: make the `samp is None` branch **bilinear-sample the source frame at the (offset) uv** instead of returning the center texel. With that one change the real bytecode runs, the embossed rainbow returns, and it animates with `uTime`.

**Prototype proves it**: `temp/dynamic_frames/_diag_midnightrainbow_real.png` — APPROX (flat desaturated grey + faint static stripe) vs the real bytecode at uTime ∈ {0, 0.25, 0.5, 0.75} (rainbow tracing the sprite contours over a dark base, hue **rolling** between phases).

---

## 1) The real `ArmorMidnightRainbow` shader (disassembled, authoritative)

Disassembled directly from `temp/xnb_probe/in/PixelShader.xnb`, technique pass `(0, 40)` (the same `PixelShader.xnb` `gen_noise_shaders.py` reads). Blob = **1968 bytes**, has `CTAB`/`PRES`/`FXLC`/`CLIT`. Full disasm captured this session via `temp/xnb_probe/fx_parse.py disasm`.

### Uniforms / consts

| Source | What | Value (still) |
|---|---|---|
| CTAB `uImage0` → `s0` | **the dyed source sprite** (the ONLY sampler) | — |
| CTAB `uImageSize0` → `c5` | drawn sheet size (W,H) | (360, 224) for armor; per-cell sheet at runtime |
| CTAB `uSourceRect` → `c4` | frame origin/size (x,y,w,h) | (0,0,40,56) |
| `def c6` | `(0, 0.333333, 4, 0.4)` | emboss weight `1/3`, amp `4`, hue gain `0.4` |
| `def c7` | `(-2, 3, 0.0666667, -1)` | smoothstep coefs, luma→hue `0.0667`, fold `-1` |
| `def c8` | `(0, 0.666, 0.333, 0.9)` | the 3 rainbow channel phase offsets |
| preshader `c0.x` | `2 / uImageSize0.y` | vertical tap offset = **2 texels** |
| preshader `c1.x` | `2 / uImageSize0.x` | horizontal tap offset = **2 texels** |
| preshader `c2.x` | `1 / uSourceRect.z` (=1/40) | normalize frame-local x to [0,1) |
| preshader `c3.x` | `uTime * 0.4` | **the animation term** (rainbow scroll phase) |

### Preshader (FXLC) — input map (recovered from the PRES-block 2nd CTAB)

The PRES block carries its own CTAB (`@ offset 268` in the blob) mapping the preshader **input registers** (`in_cN`, table 2) → effect parameter:

```
in_c0 = uTime
in_c1 = uSourceRect
in_c2 = uImageSize0
```

→ **`PRES_INPUTS["ArmorMidnightRainbow"] = {0: "uTime", 1: "uSourceRect", 2: "uImageSize0"}`**

Preshader insns (7): `rcp/mul/rcp/rcp/add/mov/mov` — all already in `dye_noise._run_preshader` (`dye_noise.py:203-234`). Verified: the package `_run_preshader` on the real blob reproduces the consts above **exactly** (c0.x=2/224=0.008929, c1.x=2/360=0.005556, c2.x=0.025, c3.x=uTime·0.4).

### Pixel program — source → output (traced instruction-by-instruction)

**(a) 5 taps of the source** — center + 4 neighbors at **±2 texels** (a cross / Laplacian, NOT a diagonal emboss):
```
r4 = sample(uv)                  # CENTER
r0 = sample(uv + (-c1.x, 0))     # LEFT   (-2/W)
r3 = sample(uv + (+c1.x, 0))     # RIGHT  (+2/W)
r1 = sample(uv + (0, -c0.x))     # UP     (-2/H)
r2 = sample(uv + (0, +c0.x))     # DOWN   (+2/H)
```

**(b) emboss magnitude** `E` = mean of the 4 per-neighbor RGB-summed differences from center, scaled by mean luma and 4:
```
dX   = sum_rgb(neighborX - center)          for X in {L,U,D,R}
E    = (dL + dU + dD + dR) * c6.y(=1/3)      # accumulated via the mad chain -> r0.x
Lsum = center.r + center.g + center.b
AMP  = E * (Lsum * 1/3) * c6.z(=4)          # r0.x ; the rainbow intensity / "self-emboss"
```
This is the **"self-emboss"** the APPROX comment says it "dropped": bright rainbow only where the source has local contrast (edges/contours), scaled by how bright that region is.

**(c) hue phase** — positional + luma + **uTime**:
```
px        = t0.x * uImageSize0.x - uSourceRect.x      # frame-local pixel x
X         = px * c2.x(=1/40)                          # normalized x in [0,1)
smooth    = (3 - 2X) * X^2                             # smoothstep(X)  (mad c7.x,c7.y then *X^2)
HUE       = Lsum * c7.z(=0.0667)  +  smooth * c6.w(=0.4)  +  c3.x(=uTime*0.4)
```
So **`uTime` adds a `*0.4` scroll to the hue** → the rainbow rolls horizontally with time. There is also a static `smoothstep(frameX)*0.4` spatial ramp across the 40px frame and a small `Lsum*0.0667` brightness-dependent shift.

**(d) rainbow color from HUE** — the standard Terraria 3-channel phase-shifted triangle wave (`add c8.wzyx` → 3 phase offsets 0.9/0.333/0.666; `abs`(=op 0x23, see note); `frc`; `cmp`-fold; `mad c7.y,c7.w`; `abs`; `add c8.w`). Same family as `dye._rainbow_rgb` (`dye.py:82`), seeded by `HUE`.

**(e) output**:
```
rgb = AMP * rainbow      # mul r0.xyz, r0.x, rainbow   <-- emboss gates the whole color
a   = center.a           # mov r0.w, -c7.w(=1)  then  mul r0, r4.w, r0  (premult by src alpha)
oC0 = (rgb, a) * v0      # v0 = vertex color = white offline
```

> **Opcode note (important):** the disasm string shows `pow r1.xyz, r0.wzyx` / `pow r0.yzw, r0`, but the real opcode is **`0x23` = ABS** (`D3DSIO_ABS`); `fx_parse` mislabels `0x23` as "pow". `dye_noise.py:355` already handles `0x23` as `np.abs` with the matching comment. **No `pow` is used.** The full opcode histogram for the pass is `{add:20, mul:10, mov:9, mad:8, texld:5, abs:2, frc:1, cmp:1}` — **all implemented**.

---

## 2) Interpreter run-ability — the exact gap + the minimal fix

**Pixel opcodes**: all 8 used (`add 0x02 / mul 0x05 / mov 0x01 / mad 0x04 / texld 0x42 / abs 0x23 / frc 0x13 / cmp 0x58`) are in `dye_noise._run_ps` (`dye_noise.py:327-366`). **Nothing to add.**

**Preshader opcodes**: all 7 used (`rcp/mul/rcp/rcp/add/mov/mov`) are in `dye_noise._run_preshader` (`dye_noise.py:203-234`). **Nothing to add.**

**The one gap — source self-taps at offset uv:**
- `dye_noise.py:323`:
  ```python
  res = src_rgba if samp is None else _sample_tex(samp, _src(regs, toks[1])[..., :2])
  ```
  `samplers` is built only from `uImage1` (`dye_noise.py:435`: `{reg: tex1 for reg,nm in smap.items() if nm == "uImage1"}`). MidnightRainbow's CTAB sampler map is `{0:'uImage0'}` with **no** `uImage1`, so `samplers` is **empty** → every one of the 5 `texld` hits `samp is None` → returns `src_rgba` (the center texel), **ignoring the offset uv `toks[1]`**.
- Consequence (verified): all 4 neighbor diffs = 0 → `AMP = 0` → `oC0.rgb = 0` → **black sprite**. The emboss is the entire signal; collapsing the taps deletes it.

**Minimal fix (the ONLY interpreter change needed):** in the `samp is None` (uImage0/source) branch, **bilinear-sample the source frame at the offset uv** instead of returning the center. The uv is in cell-sheet units `(sx+col+0.5)/sheet_w`; invert to a frame-local pixel `px = uv.x*sheet_w - sx`, then bilinear with **clamp** addressing (D3D `uImage0` clamp; wrap would bleed the opposite frame edge). This is what the prototype's `sample_src()` does (`temp/xnb_probe/proto_midnight_rainbow.py`). Gel/Reflective also self-tap, so this is a **general, reusable** interpreter capability, not MidnightRainbow-specific.

> Note: today's Gel pass "works" *despite* this gap only because Gel's output is dominated by its `uImage1` noise tap + the uColor recolor; its 4 self-taps currently also collapse to center (its blur is silently a no-op). So the same fix makes Gel's blur faithful too (low-risk: it only sharpens an already-correct image).

---

## 3) Extraction + wiring spec (which files change)

### 3a. `_build/gen_noise_shaders.py` — add the pass to extraction
`gen_noise_shaders.py` extracts exactly the passes listed in its `PRES_INPUTS` dict (`gen_noise_shaders.py:39-56`; it iterates `for name, inputs in PRES_INPUTS.items()`). **Add one entry** (verified input map from §1):
```python
"ArmorMidnightRainbow": {0: "uTime", 1: "uSourceRect", 2: "uImageSize0"},
```
Re-running `python3 gen_noise_shaders.py` then bakes `ArmorMidnightRainbow` into `data/noise_shaders.json` (blob + pres_inputs), alongside the existing 11 passes. (Precedent: `ArmorSolar` is already extracted this exact way — also a self-sampling, preshader-driven pass.)

### 3b. `dye_noise.py:323` — honor uImage0 offset taps (the §2 fix)
Replace the center-collapse with a clamped bilinear sample of `src_rgba` at the offset uv (uv→pixel inversion needs `src_rect`/`sheet_size`, already passed into `run_noise_pass`). Affects only self-tap passes; the noise passes (uImage1) are unchanged.

### 3c. `dye.py` — route 3556 to the faithful path
- **`dye.py:855`** currently:
  ```python
  if name == "ArmorMidnightRainbow":
      return _midnight_rainbow(arr_u8)
  ```
  Change to call a `run_noise_pass`-backed helper (mirror `_gel`/`_stardust`), keeping `_midnight_rainbow` (`dye.py:427`) as the **offline asset-missing fallback** (consistent with every other noise pass). Sketch:
  ```python
  if name == "ArmorMidnightRainbow":
      return _midnight_rainbow_real(arr_u8, **ngeom)   # ngeom = {src_rect, sheet_size, u_time}
  ```
  ```python
  def _midnight_rainbow_real(arr_u8, *, src_rect, sheet_size, u_time=None):
      return _noise_pass(arr_u8, "ArmorMidnightRainbow",
                         uColor=np.array([1.,1.,1.]), uSecondary=np.array([1.,1.,1.]),
                         uSat=1.0, src_rect=src_rect, sheet_size=sheet_size,
                         u_time=UTIME if u_time is None else u_time, emissive=False,
                         fallback=lambda: _midnight_rainbow(arr_u8))
  ```
  Move the dispatch line from the "time-animated (APPROX)" block down to the "noise-sampling" block (`dye.py:874-894`) so it threads `src_rect`/`sheet_size`/`u_time` like the other faithful passes. `emissive=False` → plain hard clip (the GPU clips; no extra bloom — same call as Vortex/Stardust).

### 3d. Input mapping cross-check (`dyes.json[3556]`)
`dyes.json["3556"] == {"pass": "ArmorMidnightRainbow"}` — **no `color`/`secondary`/`sat`** (matches `DyeInitializer.cs:82` `BindShader(3556, ArmorShaderData(pixelShaderRef, "ArmorMidnightRainbow"))`, which sets no colors). The shader **does not read** `uColor`/`uSecondaryColor`/`uSaturation` at all (CTAB only has `uImage0`/`uImageSize0`/`uSourceRect`), so passing the `[1,1,1]` defaults is harmless/correct. `pres_inputs` = `{0:uTime, 1:uSourceRect, 2:uImageSize0}`; `uImageSize1`/noise are **unused** (no uImage1 sampler) — `run_noise_pass` still loads `noise.png` (fine; the pass just never samples it). `has_noise_assets()` already gates the fallback.

---

## 4) Representative uTime (the frozen still)

`uTime` enters **only** through `c3.x = uTime*0.4`, a pure additive **scroll** of the rainbow hue (it does **not** change emboss/brightness — the emboss is uTime-independent). So any uTime gives an equally bright, valid frame; the choice only picks *which colors* sit where. Following the package convention for the **scroll-only** noise passes (Phase/Shifting*/Fog/Twilight all use `UTIME = 0.0`, `dye.py:46`; only the *emissive pillar* passes need a per-pass bright frame via `_PILLAR_TIME`), **use `uTime = 0.0`**. MidnightRainbow is **not** emissive/pillar (no brightness pulse), so it does **not** need a `_PILLAR_TIME` entry. For the dynamic sweep (`render_dynamic_frames.py`), the hue period in uTime is `1/0.4 = 2.5` (one full rainbow cycle per Δhue=1, and `_rainbow_rgb` repeats per unit phase), so a sweep of **uTime ∈ [0, 2.5), N≈24** shows one complete color cycle. (The catalog's old `N≈24` guess in `dynamic_effects_catalog.md:65` is right in count.)

---

## 5) Prototype validation

`temp/xnb_probe/proto_midnight_rainbow.py` (standalone; monkeypatches the source-tap fix, does **not** modify package code). Renders `temp/dynamic_frames/_diag_midnightrainbow_real.png` (5 tiles: APPROX vs real bytecode at uTime 0/0.25/0.5/0.75 on a full body of head=DeerclopsMask/body=PumpkinShirt/legs=MoonLordLegs — the catalog base set).

Measured (per-tile mean RGB over opaque px), proving emboss is back AND it animates:

| tile | mean RGB | reading |
|---|---|---|
| APPROX (now) | (72, 82, 82) | flat, desaturated, static |
| REAL uT=0.0 | (33, 41, 48) | dark base, bluish rainbow on edges |
| REAL uT=0.25 | (36, 28, 64) | hue rolled to purple/blue |
| REAL uT=0.5 | (38, 25, 66) | hue rolled further |
| REAL uT=0.75 | (45, 30, 46) | hue rolled to red/orange |

- Synthetic self-check (a luma ramp + a bright horizontal edge): emboss **lights the edge rows** (320 px) — confirms offset taps are honored.
- Animation check: `mean |frame(uTime=0) − frame(uTime=0.5)| = 13.75` (>0) — the rainbow **rolls** with uTime (the APPROX has no uTime term at all).
- Preshader cross-check: package `_run_preshader` on the real blob = hand-traced consts (c0.x=2/H, c1.x=2/W, c2.x=1/40, c3.x=uTime·0.4) **exactly**.

Visual (the saved sheet): leftmost APPROX is a near-grey sprite with a faint static vertical stripe; the four REAL tiles show **rainbow highlights tracing the antlers/shoulders/outline over a dark interior**, with colors shifting between phases — the dynamic embossed-rainbow MidnightRainbow look.

> Caveat on the prototype's geometry: it runs the dye on the **assembled** sprite as one sheet (for a quick A/B), whereas production dyes **per cell** with the cell's real `src_rect`/`sheet_size` (threaded by the compositor). The per-cell path is what 3c wires; the emboss math and tap offsets are identical, only the uv origin differs (the ±2px taps are in pixel units regardless). The faithful in-package render will look the same modulo cell seams.

---

## 6) Blast radius — the other 8 self-sampling APPROX passes

All 9 "self-sampling APPROX" passes were opcode-audited against `dye_noise._run_ps` (this session, `temp/xnb_probe`). **Every one uses ONLY already-implemented opcodes** and has a preshader; all sample `s0`/uImage0 and **need the same §2 offset-tap fix** (and nothing else opcode-wise):

| pass | item | bytes | unsupported ps opcodes | samples uImage1 (noise)? | same fix unlocks it? |
|---|---|---|---|---|---|
| **ArmorMidnightRainbow** | 3556 | 1968 | **none** | no | **yes (this task)** |
| ArmorSolar | 3526 | 2568 | none | no (self only) | yes — would replace the hand-written fire ramp `_solar` (`dye.py:432`) with the real 5-tap+`sincos(uTime)`; **note** its glow rides `v0` (vertex color) which is white offline, so the real bytecode may still wash out — needs its own validation (see `dye.py:436-445`) |
| ArmorVoid | 3530 | 1344 | none | no | yes — 3 horizontal `±uTime` taps blur+darken (`*0.35`); real-able |
| ArmorHades | 3038… | 3124 | none | no | yes — rotated taps (`uRotation` preshader sincos) + 2 ember bands |
| ArmorMirage | 3534 | 1380 | none | no | yes — 3 self-taps wavy `uTime` displacement |
| ArmorLoki | 3599 | 3004 | none | no | yes — 3 self-taps + `uRotation`/`uTime` dark camo |
| ArmorReflective | 3190 | 1404 | none | no | partial — real 5-tap emboss runs, **but** it is lit by `uLightSource` (=0 offline) → still collapses to passthrough; faithful bytecode ≈ current APPROX (no asset can fix; `dye_passes_spec.md:528`) |
| ArmorReflectiveColor | 3553… | 1496 | none | no | partial — same `uLightSource=0` caveat |

**Conclusion for §6**: the §2 source-offset-tap fix is the single unlock for **all** of these. **MidnightRainbow / Void / Hades / Mirage / Loki** (5 passes) become genuinely faithful + animated with just the fix + per-pass `PRES_INPUTS` + `dye.py` rewiring (same recipe as 3a–3c). **Solar** is faithful-able but its `v0`-carried glow needs separate validation (may still need the existing fire approximation). **Reflective×2** stay APPROX (their lighting input is genuinely 0 offline). Recommend doing MidnightRainbow now (this task) and following up with Void/Hades/Mirage/Loki as a batch (their exact tap offsets/weights are already noted in `dye_passes_spec.md:499-526`).

---

## Caveats / Not Found

- The prototype A/B is on the assembled sprite, not per-cell (see §5 caveat). The in-package faithful render (3a–3c) uses per-cell geometry; emboss math is identical.
- `uImage0` addressing mode: the disasm `dcl_2d s0` doesn't encode the sampler state (XNA sets it at bind time). I assumed **clamp** for the edge taps (matches Terraria's `SamplerState` for armor dyes; wrap would bleed across frame seams). If a future check shows a seam artifact, switch the source tap to clamp-to-frame-rect (it already clamps to the cell in the prototype).
- The exact `±2 texel` tap distance (`2/uImageSize0`, not `1/`) is firmly from the preshader (`add r0,r0` doubles `1/size`) — double-check this survives the per-cell `sheet_size` (the offset scales with the *sheet*, not the cell; on a 40×56 cell-as-sheet it is ±2px, matching the armor sheet's ±2px since both use the cell's `uImageSize0`). Confirmed in the prototype.

## Files

- Doc: `/Users/arispex/CascadeProjects/nextbot/.trellis/tasks/06-03-character-accessories/research/midnight_rainbow.md`
- Diag: `/Users/arispex/CascadeProjects/nextbot/temp/dynamic_frames/_diag_midnightrainbow_real.png`
- Prototype: `/Users/arispex/CascadeProjects/nextbot/temp/xnb_probe/proto_midnight_rainbow.py`
