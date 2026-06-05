# Research: making the noise-sampling dyes accurate (replace the `noise=const` APPROX)

- **Query**: For the ~9 noise-sampling dye passes (Gel/Phase/Nebula/Vortex/Stardust/ShiftingSands/
  ShiftingPearlsands/Fog/HallowBoss) + the two Reflective passes: identify the exact noise texture,
  the uv mapping + time offset, the per-pass combine formula, and the extraction + runtime wiring
  plan so they stop being flat-tint approximations.
- **Scope**: internal — bytecode disasm + preshader decode of `temp/xnb_probe/in/PixelShader.xnb`,
  decode of the local Terraria `Content/Images` noise textures, validated against a real frame.
- **Date**: 2026-06-02

## TL;DR (the answer)

**One texture covers all 8 noise dyes: `Content/Images/Misc/noise.xnb` → 256×256 RGBA**, bound by
`UseImage("Images/Misc/noise")` in `DyeInitializer` (slot `uImage1`). **HallowBoss uses a second
texture, `Content/Images/Extra_156.xnb` → 512×512 RGBA.** Both exist in the local install and
**decode cleanly with our `xnb_to_png.decode_texture` (`surface_format == 0`, alpha == 255
everywhere → store as straight RGBA, no premult handling needed).**

**Validated end-to-end**: I extended the probe interpreter (`temp/xnb_probe/ps_interp_full.py`,
new) to do per-pixel uv + real bilinear noise sampling and ran the **actual `ps_2_0` bytecode** for
ArmorStardust / ArmorNebula / ArmorVortex on a real 40×56 armor frame. The output is **spatially
varying** (driven by the noise texture) and clearly different from the flat-0.5 APPROX:

| pass | accurate (real noise) mean / spatial-std | APPROX(noise=0.5) mean / spatial-std |
|---|---|---|
| ArmorStardust | `[.02,.03,.09]` / `[.08,.11,.21]` | `[.19,.26,.42]` / `[.26,.27,.32]` |
| ArmorNebula   | `[.09,.13,.22]` / `[.06,.09,.14]` | `[.20,.24,.33]` / `[.13,.16,.21]` |
| ArmorVortex   | `[.16,.20,.29]` / `[.13,.16,.21]` | `[.30,.34,.43]` / `[.19,.22,.27]` |

(Stardust correctly goes dark-with-bright-specks — a starfield — instead of the blown-out flat blue
the const approx produced.)

**Recommendation: don't hand-port 9 bespoke formulas. Ship the noise PNG(s) as assets and run the
already-validated per-pixel bytecode interpreter** (or a faithful numpy transcription of it) with a
real `texld(uImage1, …)`. This makes **all 8 `Misc/noise` passes + HallowBoss accurate** (9 of 11).
The two **Reflective passes need `uLightSource` (live lighting gradient) which is 0 offline → they
stay APPROX (flat tint)** — that is the correct static answer; nothing in the assets can fix them.

**Score: 9 of 11 APPROX passes become accurate; 2 (Reflective, ReflectiveColor) remain approx by
design.** ArmorTwilight (the hair dye from `hairdye_spec.md`) is a 10th pass that uses the same noise
and becomes accurate for free.

---

## 1) The textures — file, path, dimensions, decodability

| dye pass(es) | texture param | file (under `Content/Images/`) | dims | format | alpha |
|---|---|---|---|---|---|
| Gel, Phase, Nebula, Vortex, Stardust, ShiftingSands, ShiftingPearlsands, Fog, **(ArmorTwilight hair)** | `uImage1` = `UseImage("Images/Misc/noise")` | `Misc/noise.xnb` | **256×256** | Color/RGBA32 (fmt 0) | 255 (opaque) |
| HallowBoss (4778) | `uImage1` = `Images/Extra_156` (`DyeInitializer` binds `Extra_"+(short)156`) | `Extra_156.xnb` | **512×512** | Color/RGBA32 (fmt 0) | 255 (opaque) |

- `Misc/Perlin.xnb` (512×512) also exists but **no dye binds it** — every noise dye uses `Misc/noise`.
  (Perlin is used elsewhere, e.g. screen shaders.) Do **not** extract it for the dyes.
- Decodability confirmed via `temp/xnb_probe/xnb_to_png.decode_texture`:
  `noise: 256×256 fmt=0`, `Extra_156: 512×512 fmt=0`. Both are single-mip, uncompressed Color.
- **Channel content**: `noise` R/G/B are three independent noise channels (means ≈ 0.25, range
  R∈[0,0.79]); **alpha is constant 255** so premultiplied == straight. Most passes read only
  `noise.x` (the R channel); Pearlsands & Twilight take **two** taps (different uv scales).
  `Extra_156` is a colored gradient/rainbow texture (R/G/B differ; means `[.52,.60,.79]`).

> Because alpha is uniformly opaque, store these as straight RGBA PNG (run them through
> `unpremultiply` is a no-op but harmless). The shader samples them with **wrap** addressing
> (linear filter) — the dye sampler must tile (`uv % 1`), not clamp.

---

## 2) The sampling math (uv mapping, time offset, per-pass combine)

### Shared structure (verified from disasm + preshader decode)
Every noise pass computes the noise uv from the frame-local pixel position and a per-pass scale that
the **preshader** bakes into shader constants `c0,c1,c2,…` from `uImageSize1` (=noise size),
`uSourceRect` (=frame rect) and `uImageSize0` (=sheet size). Two scale families occur:

- **Tile-and-divide family — Nebula, Vortex, Stardust**: preshader gives `c0 = 2·uImageSize1.x =
  (128,128)` (for 256-noise: `2/(1/256)`… numerically `c0=(128,128)`, `c1 = 1/c0 = (1/128,1/128)`,
  `c2 = 0`). Body: `n = frc(t0 · c0) · c1` → samples noise at `frac(uv·128)/128` (a fine
  high-frequency tiling of the whole sheet), then adds a **luminance + positional** term before the
  `texld`. (`t0` = the sprite uv ∈[0,1].)
- **Vertical-scroll family — ShiftingSands, ShiftingPearlsands, Fog**: preshader gives
  `c0 = 1/uSourceRect.w = 1/56` (frame height), `c2 = 1/uImageSize1 = (1/256,1/256)`, `c1 = c3 = 0`.
  Body: takes the frame-local y `py = t0.y·uImageSize0.y - uSourceRect.y`, builds a `sin/sgn`
  triangle of `py·(1/56)·10 + uTime`, and offsets the noise uv by it → the grain scrolls vertically
  with time.
- **Phase**: `c2 = 1/uImageSize1 = (1/256,1/256)`; plus HSL helper consts `c5=-uColor`, `c6=+uColor`,
  `c7=(1,0,0)`, `c8=(-1,0,0)`. Two uv builds: a `frc(uv·…)` noise tap and a self-recolor; the noise
  sample fades a window into the `uColor=(0.4,0.2,1.5)`-tinted source.
- **Gel**: `c4..c10` baked from `uColor/uSecondaryColor` + a **`sincos(uTime)` / `sincos(uRotation)`
  rotation** of the tap offsets; at `uTime=0, uRotation=0` the sincos collapse to `cos=1, sin=0`.
  Body does **4 self-taps of `uImage0`** (a blur/emboss) PLUS **1 noise tap of `uImage1`** that
  modulates a translucent jelly highlight mixing `uColor`/`uSecondary`.
- **HallowBoss**: preshader `c0=0` (at uTime=0). Body: `h = (max+min)·0.5 + c0`, `frc(pow(h,…))`
  → uv `(h,0.5)` into `Extra_156`, then `out = src·0.2 + noise·0.8` (rainbow tint of the source).
- **ArmorTwilight** (hair, see `hairdye_spec.md`): `c0 = 1/uImageSize0 = (1/360,1/224)`; **two noise
  taps** at scales `c0` and `0.125·…+c1`; shaped into a purple glow `·uColor=(0.5,0.1,1)` over src.

### Concrete preshader constants (uImageSize1=256², uSourceRect=(0,0,40,56), uImageSize0 sheet, uTime=0)
```
Nebula/Vortex/Stardust:  c0=(128,128,0,0)  c1=(1/128,1/128,0,0)  c2=(0,0,0,0)
ShiftingSands/Fog:       c0=(1/56,0,0,0)   c2=(1/256,1/256,0,0)  c1=c3=(0,…)
ShiftingPearlsands:      c0=(1/56,0,0,0)   c2=(1/256,1/256,0,0)  c1=c3=(0,…)
Phase:                   c2=(1/256,1/256)  c5=-uColor c6=+uColor c7=(1,0,0) c8=(-1,0,0)
HallowBoss:              c0=(0,0,0,0)
ArmorTwilight:           c0=(1/360,1/224,0,0)   (1/uImageSize0)
```
(Reproduce via `temp/xnb_probe`: `pres_decode.decode_preshader` on each pass's `PRES` block, or
`ps_interp.run_preshader_for` with the input map in §3 below. The Gel preshader additionally needs
`frc`/`sincos` opcodes — trivial at uTime=0=uRotation: `cos=1,sin=0`.)

### Time → static offset
All animated terms add `uTime = Main.GlobalTimeWrappedHourly`. **For a deterministic still use
`uTime = 0.0`** (already the `UTIME` convention in `dye.py`). At `uTime=0` the scroll phase is fixed
and the noise is sampled at its un-scrolled position — a valid frozen frame.

### uv recipe the sampler must implement (per pixel, frame col∈[0,40), row in sheet)
```
t0   = ( (col + 0.5)/uImageSize0.x ,  (frameY + row + 0.5)/uImageSize0.y )   # sprite uv
# then per family, e.g. tile-and-divide:
nuv  = frac(t0 * c0) * c1  +  <per-pass luma/positional/time offset>
noise = bilinear_wrap(noiseTex, nuv)        # read .x (and .y for Pearlsands/Twilight)
```
where `uImageSize0` = the **drawn sheet** size and `frameY` = the frame's row origin in that sheet.
**Important geometry correction discovered:** the player armor sheets are a **9×4 grid of 40×56
cells, 360×224** (`temp/xnb_probe/in/Armor_*.xnb`), **not** the `(40,1120)` vertical strip assumed in
`dye_passes_spec.md §conventions`. For the dye output this only matters through `uSourceRect` (frame
origin) and `uImageSize0` (sheet size), which the compositor knows when it crops the cell. The
spatial **gradient/rainbow** passes (already accurate) normalize by `uSourceRect.z=40`, so they are
unaffected; the **noise** passes need the correct `uImageSize0`/`uSourceRect` to place the noise uv.

---

## 3) The recommended implementation — port the bytecode with real sampling (validated)

The cleanest, lowest-risk path (and the one I validated) is **not** to re-derive 9 numpy formulas by
hand — the combine math is long (40–70 ops with `pow`/`sincos`/`rsq`/`cmp`) and error-prone. Instead:

1. **Add the noise PNG(s) as assets** (§4).
2. **Run the actual `ps_2_0` bytecode** for these passes through a small per-pixel interpreter whose
   only addition over the existing `temp/xnb_probe/ps_interp.py` is a real `texld(uImage1)` that
   bilinearly samples the loaded noise texture at the shader-computed uv. This is exactly
   `temp/xnb_probe/ps_interp_full.py` (created this session, validated on Stardust/Nebula/Vortex).

`ps_interp_full.py` implements all opcodes these passes use: `mov/add/sub/mul/mad/min/max/cmp/rcp/
rsq/frc/abs/exp/log/pow/dp3/dp4/dp2add/sincos/slt/sge/nrm/texld`, vectorized over an `(H,W,4)` frame.
`texld` returns the source texel for `s0` and `bilinear_wrap(noiseTex, uv)` for `s1`. Constants come
from the per-pass `def` tokens + the decoded preshader (`run_preshader_for`).

### Input-register → parameter map for the preshaders (resolved + verified this session)
The effect param order is `[0]uImage0 [1]uImage1 [2]uImage2 [3]uColor [4]uSecondaryColor [5]uOpacity
[6]uSaturation [7]uRotation [8]uTime [9]uSourceRect [10]uDrawPosition [11]uTargetPosition
[12]uDirection [13]uLightSource [14]uImageSize0 [15]uImageSize1 …`. Each preshader's `in[cK]` are the
subset it references, and the verified maps are:
```
Nebula/Vortex/Stardust:   in c0 = uSourceRect,  in c1 = uImageSize1
ShiftingSands/Fog/Pearl:  in c0 = uColor,       in c1 = uSourceRect,  in c2 = uImageSize1
Phase:   in c0=uColor c1=uSaturation c2,c3=uSecondaryColor c4=uSourceRect c5=uImageSize1
Gel:     in c0=uColor c1=uSecondaryColor c2=uTime c3=uSaturation c4=uSecondaryColor c5=uImageSize1
HallowBoss:  in c0 = uTime
ArmorTwilight: in c0=uColor c1=uSourceRect c2=uDirection c3=uImageSize0
```
(`OUTb[N]` writes shader-const `c[N/4]`; the produced `c*` values for the still are listed in §2.)

### If a pure-numpy transcription is preferred over the interpreter
Each pass reduces to: `noise = sample(nuv).x` (see uv recipe), then a small algebraic combine. The
representative shapes (with the noise sample now REAL instead of 0.5):

- **Stardust** — `base = uColor·(r+g+b)·0.667·a`; `sparkle = ((rainbow-ish(noise.z·8 + posphase))²
  summed)·uSecondary·a·8`; `out = base + sparkle·src.a`. The `c7=(1/128,0.333,0.666,0)`,
  `c8=(3,-1,1,0.667)`, `c9=(0.5,-0.1,8,0)` literals drive the sparkle. Starfield: mostly `base`,
  bright specks where noise crosses threshold.
- **Nebula** — `n = sample(nuv).x; w = (r+g+b)·0.333; recolor src→uColor by uSaturation; cloud =
  step(n)·rainbow(n)·uSecondary·5; out = recolor + cloud`. (`c8=(0.333,0.1,-0.4,5)`.)
- **Vortex** — builds polar coords (`pow/rsq/dp2add` → angle+radius), swirls the noise uv, then
  `out = uColor·luma + swirl·uSecondary`. (`c7..c10` are atan2 Taylor + `(0.1,-0.1,0.333,5)`.)
- **ShiftingSands / Pearlsands** — `tri = sgn-triangle(py/56·10 + uTime)`; `nuv = (luma·0.0133 +
  tri·0.04, …)`; `n = sample(nuv).x`; mix `uColor`↔`uSecondary` weighted by `n` and `v0`; Pearlsands
  adds a 2nd tap `n2 = sample(nuv·0.0667).x` for a pearlescent sparkle. (`c8/c9` triangle consts,
  `c10/c11` mix weights; Sands uColor=(1.1,1,0.5)/uSec=(0.7,0.5,0.3).)
- **Fog** — `n = sample(nuv).x`; soft overlay `lerp(src, uColor=(0.95,0.95,0.95), f(n))·0.9` with a
  low-contrast grey base; `uSecondary=(0.3,0.3,0.3)`. (`c10=(0.004,1.8,0.183,0.3)…`.)
- **Phase** — `recolor = ArmorColored(src, uColor=(0.4,0.2,1.5), uSat)`; `n = sample(nuv).x`;
  `window = (n-0.2)·1.25` shaped `·²·5`; `out = recolor + window·uColor` where the luma gate
  `(r+g+b)·(-0.333)+uSourceRect-ish` decides glow vs base. (`c14=(0.909,-0.2,1.25,5)`.)
- **Gel** — 4 self-taps of src (`±c5..c10` offsets, blur) → `g`; `n = sample(nuv).x`; jelly highlight
  `= g·(uColor) + n·mix(uColor,uSecondary)`; alpha-weighted translucent. (`c16..c20`.)
- **HallowBoss** — `h = (max(src)+min(src))·0.5`; `nuv=(frc(pow(h,..)), 0.5)`; sample **Extra_156**;
  `out = src·0.2 + extra·0.8` (rainbow recolor). (`c1=(0.5,0.8,0.2,0)`.)

> These prose formulas are a guide; for pixel-fidelity the **interpreter port is authoritative** —
> it already produced the validated numbers in the TL;DR table. Recommend wiring the interpreter (or
> a generated numpy function transcribed 1:1 from each pass's `lines`, which `fx_parse.disasm`
> emits) rather than retyping the algebra.

### Minimal numpy sampler snippet (drop-in for the `texld(uImage1)` step)
```python
def sample_noise(tex, uv):
    """tex: (H,W,4) float in [0,1]; uv: (...,2). Bilinear, WRAP. Returns (...,4)."""
    H, W = tex.shape[:2]
    u = (uv[..., 0] % 1.0) * W - 0.5
    v = (uv[..., 1] % 1.0) * H - 0.5
    x0 = np.floor(u).astype(int); y0 = np.floor(v).astype(int)
    fx_ = (u - x0)[..., None]; fy_ = (v - y0)[..., None]
    x0m, x1m = x0 % W, (x0 + 1) % W
    y0m, y1m = y0 % H, (y0 + 1) % H
    c00, c10 = tex[y0m, x0m], tex[y0m, x1m]
    c01, c11 = tex[y1m, x0m], tex[y1m, x1m]
    top = c00 * (1 - fx_) + c10 * fx_
    bot = c01 * (1 - fx_) + c11 * fx_
    return top * (1 - fy_) + bot * fy_
```

---

## 4) Extraction plan (add the textures to `assets/`)

`_build/extract_assets.py` only matches `Player_*/Armor_*` patterns today. Two small additions:

1. **Add patterns** for the noise textures (they live at `Content/Images/Misc/noise.xnb` and
   `Content/Images/Extra_156.xnb`):
   - `Misc/noise.xnb → noise.png` (the build script currently globs the top of `Content/Images`; add
     a `Misc/` sub-glob, or special-case these two paths). Suggested output names that the dye loader
     expects: **`noise.png`** and **`Extra_156.png`** directly under `nextbot/terraria_render/assets/`.
   - The existing `convert()` already does `decode_texture → unpremultiply → write_png`; for these
     opaque textures unpremultiply is a no-op. Note `convert()` currently early-returns when
     `surface_format != 0`; both noise textures are fmt 0, so they pass.
2. **Re-run** `extract_assets.py` once on the machine with Terraria installed (the same
   `DEFAULT_CONTENT` Steam path already used). Commit `noise.png` (256×256) + `Extra_156.png`
   (512×512) like the other PNG assets (asset strategy A). Combined ≈ a few hundred KB.

Source paths confirmed present locally:
`…/Terraria.app/Contents/Resources/Content/Images/Misc/noise.xnb` (52 KB),
`…/Content/Images/Extra_156.xnb` (2.8 KB). (`Misc/Perlin.xnb` exists too but is NOT needed.)

---

## 5) Runtime wiring (how dye.py loads + samples the noise)

`dye.apply_dye(arr_u8, spec)` today only receives the armor frame — it has no noise texture and no
frame-geometry. Cleanest wiring (matches the existing module style):

1. **Lazy-load the noise PNG in `dye.py`** from the package `assets/` dir (the package already ships
   PNGs there; `image_io.py` has PNG read helpers). e.g.:
   ```python
   _NOISE = None
   def _noise():
       global _NOISE
       if _NOISE is None:
           _NOISE = _read_png_rgba_f01(_ASSETS / "noise.png")   # (256,256,4) float[0,1]
       return _NOISE
   ```
   (and a parallel `_extra156()` for HallowBoss). Lazy so importing `dye` stays cheap and so the
   exact-static/animated passes don't pay for it.
2. **Pass frame geometry into the noise passes.** The noise uv needs `uSourceRect`
   (frame origin+size) and `uImageSize0` (sheet size). The compositor knows both: it crops cell
   `(row,col)` from a sheet of known size. Extend the dye `spec` for noise passes with optional
   `src_rect=(x,y,w,h)` and `sheet_size=(W,H)` (the compositor fills them when calling `apply_dye`
   for a noise dye). Defaults if absent: `src_rect=(0,0,40,56)`, `sheet_size=(40,56)` (treat the
   cropped cell as its own sheet) — a reasonable fallback that still yields correct tiling because
   the tile-and-divide family only depends on the uv *fraction*.
3. **Replace the APPROX bodies** of `_gel/_phase/_nebula/_vortex/_stardust/_shifting_sands/
   _shifting_pearlsands/_fog/_hallow_boss` (and add `_twilight`) with a call into the bytecode
   interpreter (port `ps_interp_full.py` into the package as e.g. `dye_noise.py`) or the 1:1 numpy
   transcription. Keep the current APPROX bodies as the fallback when the PNG is missing (so the
   renderer never crashes on a machine without the extracted asset).
4. The `apply_dye` dispatch already routes these pass names; only the function bodies change. Add an
   `ArmorTwilight` branch (currently missing) per `hairdye_spec.md`.

> Keeping the interpreter approach means the per-pass math is the **real shader**, not a paraphrase —
> the validated Stardust/Nebula/Vortex numbers came straight from it. Performance is fine: a 40×56
> frame is 2240 pixels; the heaviest pass (~70 vectorized ops) is sub-millisecond in numpy.

---

## 6) Reflective / ReflectiveColor — stays APPROX (correct static answer)

`ArmorReflective` (3190) and `ArmorReflectiveColor` (3026/3027/3553/3554/3555) do **not** sample a
texture — they build a 5-tap emboss of the source and light it with **`uLightSource`**, a per-entity
**normal derived from the live lighting gradient** (`ReflectiveArmorShaderData.Apply`,
`Terraria.GameContent.Dyes/ReflectiveArmorShaderData.cs`). **Offline / no entity, `uLightSource = 0`
→ the specular highlight term vanishes**, leaving ≈ the source (Reflective) or the source tinted by
`uColor` (ReflectiveColor). No asset can reconstruct a live light direction.

- **Best static option = the current flat tint** (Reflective → passthrough; ReflectiveColor →
  `_brightness_clip(src, uColor)`). This is the honest representation of "metal with no light
  source."
- **Optional cosmetic improvement** (if a livelier still is wanted): hardcode a fixed light direction
  `uLightSource = normalize((−0.7, −0.7))` (top-left, the conventional Terraria key light) and run the
  5-tap emboss + the pass's lighting combine. This adds a fake but plausible diagonal sheen. It is a
  *stylistic* choice, not the game's offline behavior — document it as such if adopted. The emboss
  tap offsets are `±(1/uImageSize0)` (from the disasm `def c5=(-0.05,-0.56,0.5,…)` family).

These two remain in the **APPROX** column either way.

---

## Caveats / Not Found

- The per-pass prose formulas in §2/§3 are derived from the disasm but only **Stardust/Nebula/Vortex
  were numerically validated** with real sampling this session (others were validated structurally —
  same shared sampler, same `texld(uImage1)` slot). The **interpreter port is the authoritative
  implementation**; if a hand numpy version is written, validate each against `ps_interp_full.py` on
  a real frame (the harness is one function call).
- The Gel preshader uses `frc`/`sincos` (for its `uRotation`/`uTime` tap rotation); the existing
  `ps_interp.run_preshader_for` lacks those two ops. At the static `uTime=0,uRotation=0` they reduce
  to `cos=1,sin=0`; either add the two ops to the preshader runner or hardcode the collapsed
  constants. (The pixel-body interpreter `ps_interp_full.py` already has `frc`/`sincos`.)
- HallowBoss's `Extra_156` is a *colored* texture (not greyscale noise) — the loader must keep all 3
  channels (it's effectively a palette/rainbow lookup), unlike `Misc/noise` where `.x` suffices.
- Sheet geometry: confirmed armor sheets are **360×224 grids** (not 40×1120). The compositor must
  pass the true `uSourceRect`/`uImageSize0` for pixel-correct noise placement; the fallback (treat
  cell as its own sheet) is close because the dominant scale is `frac(uv·128)`.
- Reproduction tooling (all under `temp/xnb_probe/`, read-only): `fx_parse.py` (name→blob + disasm),
  `pres_decode.py` / `ps_interp.run_preshader_for` (preshader consts), and **`ps_interp_full.py`**
  (new: per-pixel interp with real bilinear noise sampling — the validation harness). The decoded
  noise PNGs were written to `/tmp/noiseprobe/` during this session (scratch).
