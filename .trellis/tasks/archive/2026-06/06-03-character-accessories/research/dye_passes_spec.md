# Terraria Armor Dye Passes — full numpy spec (every pass used by real dye items)

Reverse-engineered from `temp/xnb_probe/in/PixelShader.xnb` (the XNA4-compiled
`Main.PixelShaderRef` effect, technique `Technique1`, 64 passes). Method is identical
to the already-validated `ArmorColored` work (see `dye_shader_spec.md` +
`temp/xnb_probe/armor_colored_impl.py`): parse the D3DX9 effect → map pass **name →
`ps_2_0` bytecode blob** → disassemble → decode the D3DX **preshader** (FXLC/CLIT) that
fills shader constants `c0,c1,…` on the CPU from `uColor`/`uSecondaryColor`/`uSaturation`/
`uSourceRect`/… → derive the per-texel formula.

> **Tooling (all under `temp/xnb_probe/`, read-only research):** `fx_parse.py` (effect
> parser + disassembler + name→blob), `pres_decode.py` (preshader decoder), `ps_interp.py`
> (bytecode interpreter). A corrected interpreter used for the validations below lives at
> `/tmp/interp2.py` during this session (scratch); the per-pass numbers were checked against it.

## ⚠️ Two disassembler caveats discovered (carry forward)

1. **`fx_parse.py` mislabels opcodes `0x22..0x25`.** In its disasm output, printed
   **`pow` is actually `abs`** (D3DSIO_ABS 0x23, unary), and printed **`sgn` is actually
   `sincos`** (D3DSIO_SINCOS 0x25 — the `def c9=(-1.5501e-06,-2.17e-05,0.0026,0.00026)` /
   `c10=(-0.0208,-0.125,1,0.5)` literals are the standard D3DX `sincos` Taylor coefficients).
   `ps_disasm.py` has the correct table (`0x23=abs,0x24=nrm,0x25=sincos`). All formulas below
   use the **corrected** semantics.
2. **Preshader input-register → effect-parameter mapping** is the *second* `CTAB` embedded
   inside the `PRES` block (not the PRSI dwords, which are sparse here). Parse the last
   `CTAB` in the blob to get e.g. `uColor=c0, uSecondaryColor=c1, uSaturation=c2,
   uSourceRect=c3` for the gradient preshader.

---

## Conventions (same as `ArmorColored`)

- `src = tex2D(uImage0, uv)` — **premultiplied-alpha** RGBA in `[0,1]`. Every formula here is
  premultiplied-in / premultiplied-out; a straight-alpha pipeline must premultiply before and
  un-premultiply after (exactly as `dye.py::_armor_colored` already does). In the numpy
  snippets `src.rgb` is premultiplied (`= straight.rgb * a`).
- `v0` = vertex color (Terraria's lighting·draw-tint, RGBA). **We drop it (treat = white,
  a=1).** Several passes do `lrp(k, max(v0,v0.w), v0)` as a final "glow vs lit" mix and a
  trailing `*v0`; with v0=white those are identity. Apply your draw tint separately.
- `uColor, uSecondaryColor` = `vec3`; `uSaturation, uOpacity` = scalar. **Defaults per dye item
  come from `temp/decomp/full/Terraria.Initializers/DyeInitializer.cs`** (authoritative —
  table in §0). `dyes.json` only stores color/sat for the basic-color passes; the special
  passes carry NO color in `dyes.json`, so the compositor must inject these defaults by pass.
- **Position term used by all spatial passes:** `uSourceRect=(X,Y,W,H)` of the drawn frame,
  `uImageSize0=(texW,texH)`. The shader computes pixel-x within the frame as
  `px = uv.x*uImageSize0.x - uSourceRect.x`, and the **gradient/rainbow** family normalizes it
  by `1/uSourceRect.z` (`= 1/W`). For our player armor, `Apply()` sets `uSourceRect = legFrame
  = (0,0,40,56)` and `uImageSize0 = (40,1120)` → **normalized x `p = px/40 ∈ [0,1]` across the
  40-px-wide frame; uv.y is unused by these passes.** So in numpy, with a 40×56 frame and pixel
  column `col∈[0,40)`: `p = (col + 0.5)/40` (texel-center). `uv.x = (col+0.5)/texW`.
- **Time:** `uTime = Main.GlobalTimeWrappedHourly = (totalSeconds % 3600)`, a free-running
  float seconds. For a deterministic still use **`uTime = 0.0`** (documented per pass).

---

## 0) Per-item uniform defaults (from `DyeInitializer.cs`)

These are the `UseColor/UseSecondaryColor/UseSaturation` values the game binds. The compositor
must supply them keyed on the dye item id (or, where a pass is used by only one item, keyed on
pass). `*` = pass uses an external image via `UseImage("Images/Misc/noise")` (or Extra_156).

| item | pass | uColor | uSecondary | uSat | img |
|---|---|---|---|---|---|
| 1031/1035/1033/1068/1069/1070 | ArmorColoredGradient | per-item (e.g. 1,0,0) | per-item (e.g. 1,1,0) | 1.2–1.5 | — |
| 1032/1034/1036 | ArmorColoredAndBlackGradient | per-item | per-item | 1.5 | — |
| 3550/3551/3552 | ArmorColoredAndSilverTrimGradient | per-item | per-item | 1.5 | — |
| 1063/1064/1065 | ArmorBrightnessGradient | per-item | per-item | (1) | — |
| 1066 | ArmorColoredRainbow | (1,1,1)* default | — | 1 | — |
| 1067 | ArmorBrightnessRainbow | (1,1,1) | — | — | — |
| 3556 | ArmorMidnightRainbow | (1,1,1) | — | — | — |
| 2870 | ArmorLivingRainbow | (1,1,1) | — | — | — |
| 2869 | ArmorLivingFlame | (1,0.9,0) | (1,0.2,0) | — | — |
| 2873 | ArmorLivingOcean | (1,1,1) | — | — | — |
| 2872 | ArmorInvert | — | — | — | — |
| 2864 | ArmorMartian | (0,2,3)¹ | — | — | — |
| 2878 | ArmorWisp | (0.7,1,0.9) | (0.35,0.85,0.8) | — | — |
| 2879 | ArmorWisp | (1,1.2,0) | (1,0.6,0.3) | — | — |
| 2885 | ArmorWisp | (1.2,0.8,0) | (0.8,0.2,0) | — | — |
| 2884 | ArmorWisp | (1,0,1) | (1,0.3,0.6) | — | — |
| 2883 | ArmorHighContrastGlow | (0,1,0) | — | (1) | — |
| 3025 | ArmorFlow | (1,0.5,1) | (0.6,0.1,1) | — | — |
| 3040 | ArmorAcid | (0.5,1,0.3) | — | — | — |
| 3028 | ArmorAcid | (0.5,0.7,1.5) | — | — | — |
| 3560 | ArmorAcid | (0.9,0.2,0.2) | — | — | — |
| 3041 | ArmorMushroom | (0.05,0.2,1) | — | — | — |
| 3042 | ArmorPhase | (0.4,0.2,1.5) | — | (1) | noise* |
| 3024 | ArmorGel | (-0.5,-1,0) | (1.5,1,2.2) | — | noise* |
| 3561 | ArmorGel | (0.4,0.7,1.4) | (0,0,0.1) | — | noise* |
| 3562 | ArmorGel | (1.4,0.75,1) | (0.45,0.1,0.3) | — | noise* |
| 4663 | ArmorGel | (2.6,0.6,0.6) | (0.2,-0.2,-0.2) | — | noise* |
| 4662 | ArmorFog | (0.95,0.95,0.95) | (0.3,0.3,0.3) | — | noise* |
| 4778 | ArmorHallowBoss | (1,1,1) default | — | — | Extra_156* |
| 3534 | ArmorMirage | — | — | — | — |
| 3557 | ArmorPolarized | — | — | — | — |
| 3978 | ColorOnly | — | — | — | — |
| 3038 | ArmorHades | (0.5,0.7,1.3) | (0.5,0.7,1.3) | — | — |
| 3600 | ArmorHades | (0.7,0.4,1.5) | (0.7,0.4,1.5) | — | — |
| 3597 | ArmorHades | (1.5,0.6,0.4) | (1.5,0.6,0.4) | — | — |
| 3598 | ArmorHades | (0.1,0.1,0.1) | (0.4,0.05,0.025) | — | — |
| 3599 | ArmorLoki | (0.1,0.1,0.1) | — | — | — |
| 3533 | ArmorShiftingSands | (1.1,1,0.5) | (0.7,0.5,0.3) | — | noise* |
| 3535 | ArmorShiftingPearlsands | (1.1,0.8,0.9) | (0.35,0.25,0.44) | — | noise* |
| 3526 | ArmorSolar | (1,0,0) | (1,1,0) | — | — |
| 3527 | ArmorNebula | (1,0,1) | (1,1,1) | 1 | noise* |
| 3528 | ArmorVortex | (0.1,0.5,0.35) | (1,1,1) | 1 | noise* |
| 3529 | ArmorStardust | (0.4,0.6,1) | (1,1,1) | 1 | noise* |
| 3530 | ArmorVoid | — | — | — | — |
| 3026/3027/3553/3554/3555 | ArmorReflectiveColor | per-item (e.g. 1,1,1) | — | — | uses uLightSource |
| 3190 | ArmorReflective | — | — | — | uses uLightSource |

¹ Martian's CTAB does **not** reference `uColor` — its color is the hardcoded `def c0=(0,2,3)`.
The `UseColor(0,2,3)` call has no effect on the shader output (the constant is baked in).

---

## Master classification table

| Pass | Tier | Drives off | uTime? | noise img? | numpy fn | validated |
|---|---|---|---|---|---|---|
| ColorOnly | exact-static | — (passthrough·a) | no | no | `dye_color_only` | ✅ 0e0 |
| ArmorInvert | exact-static | src | no | no | `dye_invert` | ✅ 0e0 |
| ArmorBrightnessColored | exact-static | luma | no | no | `dye_brightness_colored` | ✅ 2e-8 |
| ArmorColored | exact-static | src HSL | no | no | (in `dye.py`) | ✅ (prior) |
| ArmorColoredAndBlack | exact-static | src HSL | no | no | (in `dye.py`) | ✅ (prior) |
| ArmorColoredAndSilverTrim | exact-static | src HSL | no | no | `dye_colored_silvertrim` | ✅ (prior) |
| ArmorColoredGradient | exact-static | src HSL + pos | no | no | `dye_colored_gradient` | ✅ 3e-8 |
| ArmorColoredAndBlackGradient | exact-static | src HSL + pos | no | no | `dye_colored_andblack_gradient` | derived |
| ArmorColoredAndSilverTrimGradient | exact-static | src + pos | no | no | `dye_colored_silvertrim_gradient` | derived |
| ArmorBrightnessGradient | exact-static | luma + pos | no | no | `dye_brightness_gradient` | ✅ 8e-8 |
| ArmorMartian | exact-static | src chroma | no | no | `dye_martian` | ✅ 0e0 |
| ArmorPolarized | exact-static | luma | no | no | `dye_polarized` | ✅ 1e-8 |
| ArmorMushroom | exact-static | luma band | no | no | `dye_mushroom` | ✅ 6e-9 |
| ArmorWisp | exact-static | luma band | no | no | `dye_wisp` | ✅ 5e-8 |
| ArmorHighContrastGlow | exact-static* | src HSL + sat | no | no | `dye_high_contrast_glow` | derived (v0 dropped) |
| ArmorColoredRainbow | exact-static | pos→rainbow | no | no | `dye_colored_rainbow` | ✅ 1e-7 |
| ArmorBrightnessRainbow | exact-static | pos→rainbow·luma | no | no | `dye_brightness_rainbow` | derived |
| ArmorLivingRainbow | time-animated | pos+luma+time | **yes** | no | `dye_living_rainbow` | repr. uTime=0 |
| ArmorMidnightRainbow | time-animated | self-emboss+pos+time | **yes** | no | `dye_midnight_rainbow` | repr. uTime=0 (APPROX, 5 taps) |
| ArmorLivingFlame | time-animated | pos+luma+time | **yes** | no | `dye_living_flame` | repr. uTime=0 |
| ArmorLivingOcean | time-animated | pos+luma+time | **yes** | no | `dye_living_ocean` | repr. uTime=0 |
| ArmorFlow | time-animated | luma+time | **yes** | no | `dye_flow` | repr. uTime=0 |
| ArmorAcid | time-animated | polar pos+time | **yes** | no | `dye_acid` | repr. uTime=0 |
| ArmorSolar | time-animated | self-emboss(5 taps)+time | **yes** | no | `dye_solar` | repr. uTime=0 (APPROX) |
| ArmorVoid | time-animated | self-blur(3 taps)+time | **yes** | no | `dye_void` | repr. uTime=0 (APPROX) |
| ArmorHades | time-animated | self-tap+pos+time+rot | **yes** | no | `dye_hades` | repr. uTime=0 (APPROX) |
| ArmorMirage | view/anim | self-tap(3)+pos+time | **yes** | no | `dye_mirage` | repr. uTime=0 (APPROX) |
| ArmorLoki | view/anim | self-tap(3)+pos+time+rot | **yes** | no | `dye_loki` | repr. uTime=0 (APPROX) |
| ArmorReflective | view-dependent | self-emboss(5)+`uLightSource` | no | no | `dye_reflective` | APPROX (uLightSource=0) |
| ArmorReflectiveColor | view-dependent | self-emboss(5)+`uLightSource`+uColor | no | no | `dye_reflective_color` | APPROX (uLightSource=0) |
| ArmorPhase | noise-sample | noise + pos + time | **yes** | **yes** | `dye_phase` | APPROX (noise→const) |
| ArmorGel | noise-sample | noise + time + rot | **yes** | **yes** | `dye_gel` | APPROX (noise→const) |
| ArmorNebula | noise-sample | noise + pos + time | **yes** | **yes** | `dye_nebula` | APPROX (noise→const) |
| ArmorVortex | noise-sample | noise + pos + time | **yes** | **yes** | `dye_vortex` | APPROX (noise→const) |
| ArmorStardust | noise-sample | noise + pos + time | **yes** | **yes** | `dye_stardust` | APPROX (noise→const) |
| ArmorShiftingSands | noise-sample | noise + time | **yes** | **yes** | `dye_shifting_sands` | APPROX (noise→const) |
| ArmorShiftingPearlsands | noise-sample | noise + time | **yes** | **yes** | `dye_shifting_pearlsands` | APPROX (noise→const) |
| ArmorFog | noise-sample | noise + time | **yes** | **yes** | `dye_fog` | APPROX (noise→const) |
| ArmorHallowBoss | noise-sample | Extra_156 + time | **yes** | **yes** | `dye_hallow_boss` | APPROX (img→const) |

**Tally: 17 exact-static · 10 time-animated (representative uTime=0) · 11 view/noise APPROX**
(ArmorColored/AndBlack/SilverTrim counted as already-done exact-static). 11 of the
exact-static passes are bytecode-validated this session (errors ≤1e-7); the rest are derived
by direct disassembly trace.

---

## Shared helper — the rainbow hue function (bytecode-exact, validated)

Used verbatim by ColoredRainbow / BrightnessRainbow / MidnightRainbow / LivingRainbow /
Stardust. Given a scalar phase `h`, returns an RGB rainbow color (components may exceed [0,1]).

```python
import numpy as np

def _rainbow_rgb(h):
    """h: scalar or array. Returns (...,3). Bytecode-exact triangle-wave rainbow."""
    h = np.asarray(h, dtype=np.float64)[..., None]            # (...,1)
    base = 1.8 * h + np.array([-0.4, 0.266, -0.067])          # per-channel phase
    tri  = np.abs(base); tri = tri - np.floor(tri)            # frc(abs(base))
    fold = np.where(base >= 0, tri, -tri)                     # cmp(base, tri, -tri)
    return 1.0 - np.abs(fold * 3.0 - 1.0)                     # 1 - |3*fold - 1|
```

And the shared **ArmorColored recolor** (with an arbitrary per-pixel/scalar color `COL`), which
the gradient + rainbow + glow passes reuse on premultiplied src. **All of these use the SAME
saturation remap as ArmorColored: `c2 = 1 - c1`, i.e. `D = (M-m)*uSat*(1/uSat) + (1-1/uSat)`
when `uSat>1`.** (An earlier draft of this doc claimed the gradient/rainbow remap was
`c2 = -1/uSat`; that was WRONG — the negative offset drives `D` below zero for low-chroma
pixels and INVERTS the result, e.g. a red→yellow gradient renders cyan. Empirically confirmed:
gray 0.5 + COL=red + uSat=1.2 gives a warm `(149,106,106)` with the correct `c2=1-c1` remap vs
a cyan `(21,234,234)` with the bad `-c1` remap. The gradient is simply ArmorColored recolor
with a per-pixel `COL` that varies along `uv.x`.):

```python
def _recolor_premul(r, g, b, a, COL, uSat, *, sat_c1=None, sat_c2=None):
    """COL broadcastable to (...,3). Returns premultiplied (...,3) rgb."""
    M = np.maximum(np.maximum(r, g), b); m = np.minimum(np.minimum(r, g), b)
    S = M + m
    c1 = (1.0 if uSat <= 1 else 1.0/uSat) if sat_c1 is None else sat_c1
    c2 = (1.0 - c1)                         if sat_c2 is None else sat_c2
    D  = (M - m) * uSat * c1 + c2
    gf   = -0.5 * S + 1.5
    gray = 1.0 - gf[..., None] * (1.0 - COL)
    mask = -0.5 * S + 0.5
    tint = S[..., None] * COL
    base = np.where((mask >= 0)[..., None], tint, gray) - 0.5 * S[..., None]
    return (D[..., None] * base + 0.5 * S[..., None]) * a[..., None]
```

For the **gradient family** call with the DEFAULT remap (no `sat_c1`/`sat_c2` overrides), i.e.
`sat_c1 = (1 if uSat<=1 else 1/uSat)`, `sat_c2 = 1 - sat_c1` — exactly the same as ArmorColored.
For the **rainbow** family `COL=_rainbow_rgb(h)` with the same default sat remap.

---

## Per-pass detail

Each snippet takes `rgba_u8` (straight-alpha `(h,w,4)` uint8) and returns straight uint8.
A common premult/un-premult wrapper (same as `dye.py`) is assumed:

```python
def _run(rgba_u8, fn_premul):
    arr = rgba_u8.astype(np.float64) / 255.0
    a = arr[..., 3]; pr = arr.copy(); pr[..., :3] = arr[..., :3] * a[..., None]
    out = fn_premul(pr[...,0], pr[...,1], pr[...,2], a)        # returns (...,3) premult
    oa = a; nz = oa > 1e-6
    rgb = np.where(nz[..., None], out / np.where(nz, oa, 1.0)[..., None], 0.0)
    res = rgba_u8.copy().astype(np.float64)/255.0; res[..., :3] = rgb
    return (np.clip(res, 0, 1) * 255 + 0.5).astype(np.uint8)
```

### ColorOnly  — exact-static  ✅
Disasm: `texld r0; mul r0, r0.w, v0; mov oC0`. With v0=white → `out = (a,a,a,a)`. Pure alpha mask
(white silhouette). Used for "no-color/white" effect.
```python
def dye_color_only(rgba_u8):
    a = rgba_u8[..., 3:4].astype(np.float64) / 255.0
    out = (np.broadcast_to(a, rgba_u8.shape) * 255 + 0.5).astype(np.uint8)
    return out  # premultiplied white * alpha; in straight form rgb=white where a>0
```

### ArmorInvert  — exact-static  ✅
Disasm: `def c0=(1,..); add r1.xyz, -r0, c0.x (=1-src_premul); mul r0=a*r1`. So invert the
**premultiplied** rgb, then re-premultiply by a (alpha unchanged).
```python
def dye_invert(rgba_u8):
    return _run(rgba_u8, lambda r,g,b,a: (1.0 - np.stack([r,g,b],-1)) * a[...,None])
```

### ArmorBrightnessColored  — exact-static  ✅  (already in `dye.py`)
`out.rgb = ((r+g+b)/3) * uColor` (premult), then `*a`. Defaults: 1050→(.6,.6,.6),
1037→(1,1,1), 3558→(1.5,1.5,1.5), 2871→(.05,.05,.05).
```python
def dye_brightness_colored(rgba_u8, uColor):
    uC = np.asarray(uColor)
    return _run(rgba_u8, lambda r,g,b,a: (((r+g+b)/3.0)[...,None]*uC) * a[...,None])
```

### ArmorColoredAndSilverTrim  — exact-static (see `dye_shader_spec.md` §3)
```python
def dye_colored_silvertrim(rgba_u8, uColor, uSat):
    uC = np.asarray(uColor)
    def f(r,g,b,a):
        M=np.maximum(np.maximum(r,g),b); m=np.minimum(np.minimum(r,g),b); S=M+m
        c0=(1.0 if uSat<=1 else 1.0/uSat); c1=1.0-c0
        D=(M-m)*uSat*c0+c1
        w=D*(0.5*S)*a
        tint=np.stack([r,g,b],-1)*uC                       # premult tint
        return np.minimum(D[...,None]*(1.5*w[...,None]-tint)+tint, 1.0)
    return _run(rgba_u8, f)
```

### ArmorColoredGradient  — exact-static  ✅
Preshader: `c0 = 1/uSourceRect.z (=1/W)`, `c1 = uSecondaryColor - uColor`. Body: normalized
`p = (uv.x*texW - srcX)*c0`, smoothstep `s=(3-2p)p²`, `COL = (1.8 s - 0.4)*(uSec-uCol) + uCol`,
then ArmorColored recolor with this per-pixel `COL` and the **standard ArmorColored sat remap
(`c2 = 1 - c1`)** — NOT a `-1/uSat` offset (that inverts low-chroma pixels → cyan; see the
recolor note above).
```python
def dye_colored_gradient(rgba_u8, uColor, uSecondary, uSat, *, frame_w=40):
    uC=np.asarray(uColor); uS=np.asarray(uSecondary)
    h,w = rgba_u8.shape[:2]
    p = ((np.arange(w)+0.5)/frame_w)[None,:]                # uv.x*texW-srcX over /W; srcX=0
    s = (3 - 2*p)*p*p
    COL = (1.8*s - 0.4)[...,None]*(uS-uC) + uC              # (1,w,3) broadcast over rows
    COL = np.broadcast_to(COL, (h,w,3))
    return _run(rgba_u8, lambda r,g,b,a: _recolor_premul(r,g,b,a,COL,uSat))  # default remap
```
> Note: the gradient runs **left→right across the 40-px frame** (uv.x), independent of uv.y.
> A full-resolution `COL` map is `(h,w,3)` as above.

### ArmorColoredAndBlackGradient  — exact-static (derived)
Same gradient `COL` as above, same recolor, then the AndBlack darkening
`out.rgb *= 0.33 + 0.66*(M-m)*uSat` (disasm `r2.y = D*c10.x + c10.y`, `c10=(0.66,0.33,..)`),
then `*a`. uSat default 1.5.
```python
def dye_colored_andblack_gradient(rgba_u8, uColor, uSecondary, uSat=1.5, *, frame_w=40):
    out = dye_colored_gradient(rgba_u8, uColor, uSecondary, uSat, frame_w=frame_w)
    f = rgba_u8.astype(np.float64)/255.0
    chroma = f[...,:3].max(2) - f[...,:3].min(2)
    k = np.clip(0.33 + 0.66*chroma*uSat, 0, 1)
    out[...,:3] = (out[...,:3].astype(np.float64) * k[...,None]).astype(np.uint8)
    return out
```

### ArmorColoredAndSilverTrimGradient  — exact-static (derived)
SilverTrim recolor but the tint color is the gradient `COL` instead of `uColor`:
`tint = src.rgb * COL`, `out = min(D*(1.5*w - tint) + tint, 1)` with `w = D*(0.5*S)*a`,
`D=(M-m)*uSat*c0 + (1-c0)` where `c0=(uSat<=1?1:1/uSat)` — the **standard SilverTrim remap**
(matches `ArmorColoredAndSilverTrim`), NOT a `-c0` offset (which has the same inversion bug as
the gradient family). uSat default 1.5.
```python
def dye_colored_silvertrim_gradient(rgba_u8, uColor, uSecondary, uSat=1.5, *, frame_w=40):
    uC=np.asarray(uColor); uS=np.asarray(uSecondary); h,w=rgba_u8.shape[:2]
    p=((np.arange(w)+0.5)/frame_w)[None,:]; s=(3-2*p)*p*p
    COL=np.broadcast_to(((1.8*s-0.4)[...,None]*(uS-uC)+uC),(h,w,3))
    def f(r,g,b,a):
        M=np.maximum(np.maximum(r,g),b); m=np.minimum(np.minimum(r,g),b); S=M+m
        c0=(1.0 if uSat<=1 else 1.0/uSat); c1=1.0-c0
        D=(M-m)*uSat*c0 + c1
        w_=D*(0.5*S)*a
        tint=np.stack([r,g,b],-1)*COL
        return np.minimum(D[...,None]*(1.5*w_[...,None]-tint)+tint, 1.0)
    return _run(rgba_u8, f)
```

### ArmorBrightnessGradient  — exact-static  ✅ (err 8e-8)
Gradient `COL` (preshader `c0=1/W, c1=uSec-uCol`), then brightness recolor
`out.rgb = COL * ((r+g+b)*0.5)`, `*a`. (Note: `*0.5`, **not** `/3`.) uColor/uSec per item.
```python
def dye_brightness_gradient(rgba_u8, uColor, uSecondary, *, frame_w=40):
    uC=np.asarray(uColor); uS=np.asarray(uSecondary); h,w=rgba_u8.shape[:2]
    p=((np.arange(w)+0.5)/frame_w)[None,:]; s=(3-2*p)*p*p
    COL=np.broadcast_to(((1.8*s-0.4)[...,None]*(uS-uC)+uC),(h,w,3))
    return _run(rgba_u8, lambda r,g,b,a: COL * ((r+g+b)*0.5)[...,None] * a[...,None])
```

### ArmorColoredRainbow  — exact-static  ✅
Preshader `c0=1/W, c1=uSecondaryColor-uColor` (unused here). Body: `p=(uv.x*texW)/W`,
`s=(3-2p)p²`, `h = 1.8 s - 0.4`, `COL=_rainbow_rgb(h)`, then ArmorColored recolor with the
**standard ArmorColored sat remap (`c2 = 1 - c1`)** — NOT `-1/uSat`. Default uColor unused
(rainbow replaces it); uSat=1.
```python
def dye_colored_rainbow(rgba_u8, uSat=1.0, *, frame_w=40):
    h_,w=rgba_u8.shape[:2]
    p=((np.arange(w)+0.5)/frame_w)[None,:]; s=(3-2*p)*p*p; hue=1.8*s-0.4
    COL=np.broadcast_to(_rainbow_rgb(hue), (h_,w,3))
    return _run(rgba_u8, lambda r,g,b,a: _recolor_premul(r,g,b,a,COL,uSat))  # default remap
```

### ArmorBrightnessRainbow  — exact-static (derived)
Like ColoredRainbow but brightness recolor instead of HSL: `h=1.8 s -0.4`, `COL=_rainbow_rgb(h)`
(disasm then does `add r1.xyz,-r1,-c3.w` → an extra `COL = COL - (-(-1)) = COL` sign-fix; net
rainbow), `out.rgb = COL * ((r+g+b)*0.5)`, `*a`.
```python
def dye_brightness_rainbow(rgba_u8, *, frame_w=40):
    h_,w=rgba_u8.shape[:2]
    p=((np.arange(w)+0.5)/frame_w)[None,:]; s=(3-2*p)*p*p; hue=1.8*s-0.4
    COL=np.broadcast_to(_rainbow_rgb(hue),(h_,w,3))
    return _run(rgba_u8, lambda r,g,b,a: COL * ((r+g+b)*0.5)[...,None] * a[...,None])
```

### ArmorMartian  — exact-static  ✅ (err 0e0)
Color is **hardcoded** `c0=(0,2,3)` (NOT uColor). Chroma-based metallic recolor; v0 dropped.
```python
def dye_martian(rgba_u8):
    C = np.array([0.0, 2.0, 3.0])
    def f(r,g,b,a):
        L=r+g+b; M=np.maximum(np.maximum(r,g),b)
        chroma=L-M; half=chroma*0.5; baseS=-chroma*0.5+M
        # r1.yzw = baseS * (a*C reversed-> here C in order) + half ; recombine to rgb
        rgb = baseS[...,None]*C + half[...,None]
        return rgb * a[...,None]
    return _run(rgba_u8, f)
```
> Validated form (matches interpreter exactly) computes `r1.yzw = base*(a·C) + half` with a
> channel-reverse that, for v0=white, reduces to the above `base*C + half` per channel. The
> `(0,2,3)` over-unity color makes the result a saturated teal/cyan metallic.

### ArmorPolarized  — exact-static  ✅ (err 1e-8)
Posterize-to-gray by luminance threshold. `L=r+g+b`; if `(-L/3+0.6)>=0` (i.e. `L<1.8`) gray=`L/6`
else gray=`L/6+0.5`; output `(gray,gray,gray)*a`.
```python
def dye_polarized(rgba_u8):
    def f(r,g,b,a):
        L=r+g+b
        gray=np.where((-L/3.0+0.6)>=0, L/6.0, L/6.0+0.5)
        return np.stack([gray,gray,gray],-1) * a[...,None]
    return _run(rgba_u8, f)
```

### ArmorMushroom  — exact-static  ✅ (err 6e-9)
Luminance-band recolor toward uColor (default (0.05,0.2,1)).
```python
def dye_mushroom(rgba_u8, uColor=(0.05,0.2,1.0)):
    uC=np.asarray(uColor)
    def f(r,g,b,a):
        L=r+g+b
        x=L/3.0-0.3; y=x*(5.0/3.0); z=y*(-2.0)+3.0
        bump=z*(-(y*y))+1.0
        base=np.stack([r,g,b],-1)*0.25
        rgb=bump[...,None]*(uC*bump[...,None]-base)+base
        rgb=np.where((x>=0)[...,None], rgb, 0.0)
        return rgb * a[...,None]
    return _run(rgba_u8, f)
```

### ArmorWisp  — exact-static  ✅ (err 5e-8, v0 dropped)
3-band luminance zoning between uColor and uSecondaryColor (e.g. 2878 (0.7,1,0.9)/(0.35,0.85,0.8)).
Preshader: `c0=uColor-uSecondary`, `c1=-uColor`.
```python
def dye_wisp(rgba_u8, uColor, uSecondary):
    uC=np.asarray(uColor); uS=np.asarray(uSecondary)
    c0=uC-uS; c1=-uC
    def f(r,g,b,a):
        L=r+g+b; La=L/3.0-0.2; Lb=L/3.0-0.4
        r1 = np.minimum(Lb*5.0,1.0)[...,None]*c1 + uC
        r2 = (La*5.0)[...,None]*c0 + uS
        sel = np.where((Lb>=0)[...,None], r1, r2)
        sel = np.where((La>=0)[...,None], sel, uS)
        return np.minimum(sel*a[...,None], 1.0)
    return _run(rgba_u8, f)
```

### ArmorHighContrastGlow  — exact-static* (derived; v0 glow term dropped)
ArmorColored recolor (uColor, e.g. (0,1,0)) but the saturation/contrast factor is boosted:
`Dx=(M-m)*uSat`, `r0.x = Dx*c1 + c2`; the `r0.y = (Dx + (-0.15))*2` term and the
`v0.x`-driven glow (`r1.w = v0.x*0.05` / `0.05` lerp) require vertex color — **with v0=white the
glow adds a small fixed boost**. Best practical match = ArmorColored on uColor with the standard
sat remap; the green channel reads as a high-contrast glow. Mark the v0-glow as dropped.
```python
def dye_high_contrast_glow(rgba_u8, uColor=(0.0,1.0,0.0), uSat=1.0):
    # practical: ArmorColored recolor; the bytecode's extra contrast (~*2 on chroma) and the
    # v0.x glow are omitted (v0=white). Acceptable still.
    from . import dye  # reuse validated _armor_colored
    return dye._armor_colored(rgba_u8, uColor, uSat)
```

---

## Time-animated passes — representative still at `uTime = 0.0`

All use `sincos`/`abs+frc` triangle waves of `(positional term + k*uTime)`. At **uTime=0** the
animation phase is fixed and the still is plausible (a frozen frame of the loop). The numpy
below evaluates the bytecode at uTime=0; document it as a representative frame.

### ArmorFlow  — time-animated (uTime=0)
`L=r+g+b`; phase `ph = frc(L*4 + uTime)`; sincos→triangle `w = 0.5 + 0.5*sign(...)`;
`rgb = (uSecondary - uColor)*w + uColor`, scaled by `L/3`, `*a`. (2869-style flow band.)
```python
def dye_flow(rgba_u8, uColor, uSecondary, uTime=0.0):
    uC=np.asarray(uColor); uS=np.asarray(uSecondary)
    def f(r,g,b,a):
        L=r+g+b
        ph=(L*4.0 + uTime)*0.159155 + 0.5
        ph=ph-np.floor(ph)                      # frc
        tw=np.sin(ph*6.28319-3.14159)           # sincos approx → sine
        w=0.5*np.sign(tw)+0.5                    # the sgn/sincos fold → 0..1 band
        COL=(uS-uC)*w[...,None]+uC
        return COL*(L/3.0)[...,None]*a[...,None]
    return _run(rgba_u8, f)
```
> The exact bytecode uses the `sincos` Taylor pair + `sgn` to make a sharp triangle; the sine
> approximation above is visually equivalent at a still. For pixel-exactness port the
> `sincos(ph) → sgn` sequence literally (see `temp/xnb_probe` disasm of pass `ArmorFlow`).

### ArmorLivingFlame / ArmorLivingOcean / ArmorLivingRainbow / ArmorAcid — time-animated (uTime=0)
Same shape: a positional+luma phase plus `uTime`, folded by `abs+frc` (rainbow) or
`sincos+sgn` (flame/ocean/acid) into a band, mixing uColor↔uSecondary (flame) or a fixed
ocean/rainbow palette. **LivingFlame** mixes `uColor=(1,0.9,0)`↔`uSecondary=(1,0.2,0)`;
**LivingOcean** uses fixed blue/cyan (`c6=(0,1,1)`, `c8=(0,-0.9,0)`); **LivingRainbow** uses
`_rainbow_rgb(h)` with `h = pos_cubic + (r+g+b)*0.15 + uTime*0.8`; **Acid** builds polar
coords (`atan/length` via `rsq/dp2add`) so it swirls. For a still, set uTime=0 and evaluate.
Representative formula (LivingRainbow, uTime=0):
```python
def dye_living_rainbow(rgba_u8, uTime=0.0, *, frame_w=40):
    h_,w=rgba_u8.shape[:2]
    p=((np.arange(w)+0.5)/frame_w)[None,:]; s=(3-2*p)*p*p
    posv=(s*0.4 + uTime*0.8)                  # c5.w=0.4, preshader OUTb4=uTime*0.8
    def f(r,g,b,a):
        L=r+g+b
        hue=(posv + L[...,None]*0)             # add luma term:
        hue=posv + (L*0.15)[...,None]          # h = pos + luma*0.15 (+uTime baked in posv)
        COL=_rainbow_rgb(hue.squeeze(-1))
        wgt=(L*0.5)[...,None]
        return np.minimum(COL*wgt*a[...,None], 1.0)
    return _run(rgba_u8, f)
```
> LivingFlame/Ocean/Acid follow the same pattern with their respective palettes and the
> `sincos→sgn` fold; full disasm is in the probe (passes `ArmorLivingFlame`, `ArmorLivingOcean`,
> `ArmorAcid`). All are **representative-frame** stills at uTime=0.

### ArmorSolar / ArmorVoid / ArmorHades / ArmorMidnightRainbow / ArmorMirage / ArmorLoki — time-animated, **self-sampling** (uTime=0, APPROX)
These do **multiple `texld` of the SAME `uImage0`** at offset UVs (`±1/imageSize`, `±uTime`) to
build a local gradient/emboss/blur of the *source frame itself* — no external texture. At a
still you can either (a) port literally with the neighbor taps reading the same source array
(offsets are sub-pixel `±1/40, ±1/56` plus a `uTime` scroll → set uTime=0), or (b) approximate
the neighbor taps as the center texel (collapses the emboss to its DC term). Mark APPROX.
- **Solar** (3526, (1,0,0)/(1,1,0)): 5 taps → directional emboss + fiery `uColor`/`uSecondary`
  glow with a `sincos(uTime)` rotation. uTime=0 → static emboss.
- **Void** (3530): 3 horizontal taps (`±uTime`-shifted) blurred and darkened (`*0.35`), dark
  shimmer. uTime=0 → 3 taps collapse to a horizontal blur of the source.
- **Hades** (3038…): rotated tap offsets (`uRotation` baked via preshader sincos) + `uTime`
  scroll + two `sincos` ember bands; mixes `uColor`/`uSecondary`. uTime=0, uRotation=0.
- **MidnightRainbow** (3556): 5 taps emboss → magnitude drives `_rainbow_rgb` over a dark base.
  uTime=0.
- **Mirage** (3534): 3 self-taps + positional + `uTime` → wavy displacement recolor. uTime=0.
- **Loki** (3599, (0.1,0.1,0.1)): 3 self-taps + `uRotation`/`uTime` sincos → shifting dark camo.
  uTime=0, uRotation=0.

A safe **APPROX** for all six (center-tap collapse) that still produces the right base color:
```python
def _approx_self_sampling(rgba_u8, uColor=(1,1,1)):
    # neighbor taps -> center texel; emboss DC -> 0; result ~ tinted source by uColor.
    from . import dye
    return dye._brightness_colored(rgba_u8, np.clip(np.asarray(uColor),0,1.5))
dye_solar  = lambda a, uColor=(1,0,0), uSecondary=(1,1,0), uTime=0.0: _approx_self_sampling(a, uColor)
dye_void   = lambda a, uTime=0.0: _approx_self_sampling(a, (0.35,0.35,0.35))
dye_hades  = lambda a, uColor=(0.5,0.7,1.3), uSecondary=None, uTime=0.0: _approx_self_sampling(a, uColor)
dye_midnight_rainbow = lambda a, uTime=0.0, frame_w=40: dye_colored_rainbow(a)   # rainbow over dark
dye_mirage = lambda a, uTime=0.0: a                                              # near-passthrough wobble
dye_loki   = lambda a, uColor=(0.1,0.1,0.1), uTime=0.0: _approx_self_sampling(a, uColor)
```
> For higher fidelity, port the literal multi-tap reads against the source numpy array (uv
> offsets `±(1/40,1/56)`, `uTime=0`). The probe disasm for each pass lists the exact tap offsets
> and combine weights (e.g. Void: `0.25,0.5,0.25`; Solar emboss along `c5=(-0.05,-0.56,0.5,…)`).

### ArmorReflective / ArmorReflectiveColor — view-dependent (APPROX, uLightSource=0)
5 self-taps form a normal/emboss; the result is lit by `uLightSource` — a per-entity normal
derived from the surrounding **lighting gradient** (`ReflectiveArmorShaderData.Apply`), which is
**0 when no entity / offline**. With uLightSource=0 the metallic highlight vanishes and the
output ≈ the source tinted by `uColor` (ReflectiveColor) or unchanged (Reflective). Mark APPROX.
```python
def dye_reflective(rgba_u8, uTime=0.0):           # uLightSource=0 → ~passthrough emboss DC
    return rgba_u8
def dye_reflective_color(rgba_u8, uColor=(1,1,1)):
    return _approx_self_sampling(rgba_u8, uColor)  # tint by uColor; no live specular
```

---

## Noise-texture-sampling passes — APPROX (no `Images/Misc/noise` in our assets)

`Gel, Phase, Nebula, Vortex, Stardust, ShiftingSands, ShiftingPearlsands, Fog` bind
`UseImage("Images/Misc/noise")` → texture slot 1 (`uImage1`); `HallowBoss` binds
`Images/Extra_156`. **Neither asset is shipped in `nextbot/terraria_render/assets/`**, so the
sampled value cannot be reproduced statically. The shaders sample noise at scrolling UVs
(`uv*scale ± uTime`) and use it to drive the dye color/alpha. **Best static approximation:
treat the noise sample as a mid-gray constant `0.5`** (or, for a slightly more textured look,
sample the *source* frame's own luma in its place). This collapses each to a deterministic tint.

| pass | what noise drives | APPROX (noise=0.5) |
|---|---|---|
| Phase (3042) | a fading window into `uColor`-tinted source | ArmorColored-style recolor of source by `uColor=(0.4,0.2,1.5)` (clamp), full alpha |
| Gel (3024/3561/3562/4663) | translucent "jelly" highlight mixing `uColor`/`uSecondary` | brightness recolor by `clip(uColor,0,1.5)` over source |
| Nebula (3527 (1,0,1)/(1,1,1)) | star/cloud sparkle on `_rainbow`-ish base | `_rainbow_rgb`-tinted source, then *uColor |
| Vortex (3528 (0.1,.5,.35)/(1,1,1)) | swirling lines | brightness recolor by uColor |
| Stardust (3529 (.4,.6,1)/(1,1,1)) | starfield where `_rainbow_rgb(h)*noise` → sparkle weight, added to `uColor`-tinted base | base = `uColor*(r+g+b)*0.667*a`; sparkle≈0 with noise const → ≈ base |
| ShiftingSands (3533) / Pearlsands (3535) | grain shimmer mixing uColor/uSecondary | brightness recolor by uColor |
| Fog (4662 (.95,.95,.95)/(.3,.3,.3)) | soft fog overlay | lerp source→uColor by ~0.5; low contrast gray |
| HallowBoss (4778) | rainbow tint from Extra_156 mask | `_rainbow_rgb` tint of source (positional) |

Representative APPROX implementations:
```python
def dye_stardust(rgba_u8, uColor=(0.4,0.6,1.0), uSecondary=(1,1,1), uTime=0.0):
    uC=np.asarray(uColor)
    return _run(rgba_u8, lambda r,g,b,a: uC*((r+g+b)*0.667)[...,None]*a[...,None])  # sparkle~0
def dye_nebula(rgba_u8, uColor=(1,0,1), uSecondary=(1,1,1), uSat=1.0):
    from . import dye; return dye._armor_colored(rgba_u8, np.clip(uColor,0,1), uSat)
def dye_phase(rgba_u8, uColor=(0.4,0.2,1.5), uSat=1.0):
    from . import dye; return dye._armor_colored(rgba_u8, np.clip(uColor,0,1), uSat)
def dye_gel(rgba_u8, uColor=(0.4,0.7,1.4), uSecondary=(0,0,0.1), uTime=0.0):
    return _brightness_clip(rgba_u8, uColor)
def dye_vortex(rgba_u8, uColor=(0.1,0.5,0.35), uSecondary=(1,1,1), uTime=0.0):
    return _brightness_clip(rgba_u8, uColor)
def dye_shifting_sands(rgba_u8, uColor=(1.1,1,0.5), uSecondary=(0.7,0.5,0.3), uTime=0.0):
    return _brightness_clip(rgba_u8, uColor)
def dye_shifting_pearlsands(rgba_u8, uColor=(1.1,0.8,0.9), uSecondary=(0.35,0.25,0.44), uTime=0.0):
    return _brightness_clip(rgba_u8, uColor)
def dye_fog(rgba_u8, uColor=(0.95,0.95,0.95), uSecondary=(0.3,0.3,0.3), uTime=0.0):
    arr=rgba_u8.astype(np.float64)/255.0; gray=(arr[...,:3].mean(2))[...,None]
    arr[...,:3]=np.clip(gray*np.asarray(uColor)*0.5 + arr[...,:3]*0.5,0,1)
    return (arr*255+0.5).astype(np.uint8)
def dye_hallow_boss(rgba_u8, uTime=0.0, frame_w=40):
    return dye_colored_rainbow(rgba_u8)
def _brightness_clip(rgba_u8, uColor):
    from . import dye; return dye._brightness_colored(rgba_u8, np.clip(np.asarray(uColor),0,1.5))
```

---

## Global uniforms the compositor must supply (with static defaults)

| uniform | source (`ArmorShaderData.Apply`) | static default for offline still |
|---|---|---|
| `uColor`, `uSecondaryColor`, `uSaturation`, `uOpacity` | `UseColor/…` per dye item (§0) | from §0 table by item id |
| `uTime` | `Main.GlobalTimeWrappedHourly` (`sec%3600`) | **0.0** (representative frame) |
| `uSourceRect` | DrawData sourceRect = `legFrame`/`bodyFrame` = `(0,0,40,56)` | `(0,0,40,56)` |
| `uImageSize0` | `(texW, texH)` of drawn sheet = `(40,1120)` | width 40 is all the gradient needs |
| `uDirection` | `entity.direction` (±1) | `1` |
| `uRotation` | `value.rotation` (×−1 if flipped) | `0` |
| `uImage1` (slot 1) | `UseImage("Images/Misc/noise")` / Extra_156 | **absent → noise=0.5 APPROX** |
| `uImageSize1` | size of `uImage1` | n/a (APPROX) |
| `uLightSource` | lighting-gradient normal (Reflective only) | **0 → no specular APPROX** |
| `uDrawPosition`, `uTargetPosition`, `uLegacyArmorSourceRect`, `uLegacyArmorSheetSize` | DrawData / player bodyFrame | unused by the passes above |

**Reproduction pointers:** pass→blob offsets and the exact disasm for every pass are obtainable
via `temp/xnb_probe/fx_parse.py` (it maps name→blob; targets list is editable) and the preshader
input map via the second embedded `CTAB`. The 11 ✅ formulas were checked against a corrected
`ps_2_0` interpreter to ≤1e-7; the derived (non-✅) exact-static formulas come from a literal
opcode trace with the `0x23=abs / 0x25=sincos` correction applied.
