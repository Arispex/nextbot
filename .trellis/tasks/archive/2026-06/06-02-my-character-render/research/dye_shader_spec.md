# Terraria Armor Dye Shader Spec — `ArmorColored` family (exact pixel math)

Reverse-engineered from `temp/xnb_probe/in/PixelShader.xnb` (the XNA4-compiled
`Main.PixelShaderRef` effect). Recovered by fully parsing the D3DX9 effect
container, mapping pass **name → pixel-shader bytecode blob**, disassembling the
`ps_2_0` bytecode, and decoding the **D3DX preshader** (FXLC/CLIT) that fills the
shader constants `c0/c1/c2` from `uColor`/`uSaturation`.

All formulas below are **bytecode-verified**: a direct ps_2_0 interpreter
(`temp/xnb_probe/ps_interp.py`) executing the real instructions reproduces the
hand-derived numpy formula to <1e-12.

> Tooling produced (all under `temp/xnb_probe/`, read-only research):
> - `fx_parse.py` — D3DX9/XNA4 effect parser + correct `ps_2_0` disassembler; maps any pass name to its shader blob.
> - `pres_decode.py` — D3DX preshader (FXLC/CLIT/PRSI) decoder.
> - `ps_interp.py` — ground-truth ps_2_0 bytecode interpreter (validation).
> - `armor_colored_impl.py` — final numpy implementation.

---

## How the effect was decoded (so results are reproducible)

1. **XNB payload → FX blob.** The XNB manifest (1 type reader =
   `EffectReader`) is followed by a `u32` byte-count then the FX blob at byte
   **158**. Blob starts with the XNA4 wrapper magic `0xBCF00BCF`.
2. **XNA4 wrapper.** Read magic, then `u32 skip`; advance `skip - 8` bytes to
   reach the real D3DX9 header `0xFEFF0901` (at byte 678).
3. **Header.** After `0xFEFF0901`: `u32 offset`; set `base =` (ptr right after
   offset, byte **686**, the reference for all string/value offsets); then
   `ptr += offset` to the counts: `numparams=21, numtechniques=1, FIXME, numobjects=71`.
4. **Params / techniques / passes.** Standard MojoShader walk. There is **one
   technique ("Technique1") with 64 passes**; each pass *is* a named dye effect
   (`Default`, `ColorOnly`, `ArmorColored`, `ArmorColoredAndBlack`, …). Each pass
   has one state of type **147 = PIXELSHADER**.
5. **Objects → shader blobs.** `numsmallobjects=0, numlargeobjects=70`. Large
   objects are stored in **reverse pass order**; each record is
   `(technique, passIndex, FIXME, stateSlot, type, length)` followed by inline
   bytecode (4-byte aligned). The pixel shader for pass *N* is the large object
   whose `passIndex == N`.
6. **Preshader.** Each shader blob carries a `PRES` comment block (FXLC opcodes +
   CLIT literal doubles). Wine d3dx9 semantics: opcode = `(ins>>20)&0x7FF`,
   operands = `(relFlag, table, offset)`; tables `0=IMM/CLIT(double)`,
   `2=INPUT`, `4=output shader-consts (c0,c1,…)`, `6/7=temp`. `offset>>2` =
   register, `offset&3` = component. `CMP(a,b,c) = a>=0 ? b : c`.

### Blob locations (this exact `PixelShader.xnb`)
| pass | passIndex | blob offset | length |
|------|-----------|-------------|--------|
| `ArmorColored` | 3 | 100530 | 1432 |
| `ArmorColoredAndBlack` | 4 | 99026 | 1480 |
| `ArmorColoredAndSilverTrim` | 5 | 97794 | 1208 |
| `ArmorBrightnessColored` | 7 | 96194 | 388 |

---

## Conventions

- `src` = `tex2D(uImage0, uv)` — **premultiplied-alpha** RGBA (XNA convention),
  components in `[0,1]`. The shader operates on premultiplied values and
  re-multiplies by `src.a` at the end (so a straight-alpha pipeline must
  premultiply before, un-premultiply after).
- `v0` = vertex color (Terraria passes white·tint·alpha). Below we drop it
  (treat as white); apply your own draw tint separately if needed.
- `uColor` = `(r,g,b)` (RedDye = `(1,0,0)`). `uSaturation` = scalar (basic dyes
  use `1.2`). `uSecondaryColor`, `uOpacity` are **not used** by this family.

---

## 1) `ArmorColored`  (RedDye item 1007: `uColor=(1,0,0)`, `uSaturation=1.2`)

### Disassembly (`ps_2_0`)
```
def c5, 0.5, 1.5, 1.0, 0.0
dcl v0
dcl t0.xy
dcl_2d s0                  ; uImage0
texld r0, t0, s0           ; r0 = src (premultiplied rgba)
max   r1.w, r0.y, r0.z
max   r2.w, r0.x, r1.w     ; r2.w = M = max(r,g,b)
min   r1.x, r0.z, r0.y
min   r2.x, r1.x, r0.x     ; r2.x = m = min(r,g,b)
add   r0.x, r2.w, r2.x     ; S = M + m
add   r0.y, r2.w, -r2.x    ; D = M - m  (chroma)
mul   r0.y, r0.y, c4.x     ; D *= uSaturation
mov   r1.x, c1.x
mad   r0.y, r0.y, r1.x, c2.x   ; D = D*c1 + c2        (saturation remap)
mad   r0.z, r0.x, -c5.x, c5.y  ; g = -0.5*S + 1.5
mov   r1.z, c5.z
mad   r1.xyz, r0.z, -c0, r1.z  ; gray = 1 - g*c0      (c0 = 1-uColor)
mad   r1.w, r0.x, -c5.x, c5.x  ; mask = -0.5*S + 0.5  (>=0 iff S<=1)
mul   r2.xyz, r0.x, c3         ; tint = S * uColor
cmp   r1.xyz, r1.w, r2, r1     ; base = (mask>=0) ? tint : gray
mad   r1.xyz, r0.x, -c5.x, r1  ; base -= 0.5*S
mul   r1.w, r0.x, c5.x         ; half = 0.5*S
mad   r1.xyz, r0.y, r1, r1.w   ; rgb = D*base + half
mov   r1.w, c5.z               ; (alpha path -> 1, then *src.a below)
mul   r0, r0.w, r1             ; rgb *= src.a   (re-premultiply)
mul   r0, r0, v0               ; * vertex color
mov   oC0, r0
```

### Constants (from CTAB + preshader)
- `c3 = uColor`, `c4 = uSaturation` — set directly by the effect.
- `c5 = (0.5, 1.5, 1.0, 0.0)` — `def` literal.
- **Preshader** (inputs `in0 = uColor`, `in1 = uSaturation`):
  - `c0 = 1 - uColor`                              (vec3)
  - `c1 = (uSaturation <= 1) ? 1.0 : 1/uSaturation`  (scalar)
  - `c2 = 1 - c1`                                   (scalar)
  - For `uSaturation = 1.2`: `c1 = 0.83333`, `c2 = 0.16667`.

### Exact formula (premultiplied in, premultiplied out)
```
M  = max(r,g,b);  m = min(r,g,b)
S  = M + m                                  # 2*lightness  (sum of extremes)
D  = (M - m) * uSaturation
D  = D * c1 + c2                            # c1=(uSat<=1?1:1/uSat), c2=1-c1
g  = 1.5 - 0.5*S
gray = 1 - g*(1 - uColor)                   # per-channel
mask = 0.5 - 0.5*S                          # >=0  iff  S <= 1
base = (mask >= 0) ? (S * uColor) : gray    # per-channel select
base = base - 0.5*S
rgb  = D*base + 0.5*S                        # saturation lerp about mid-gray 0.5*S
rgb  = src.a * rgb                           # re-premultiply
out  = (rgb, src.a)
```

**Intuition.** `S = max+min` is twice the HSL lightness; the shader recolors
toward `S*uColor` (or a desaturating "gray" branch when `S>1`, i.e. bright
premultiplied texels), then re-injects luminance via the `D*base + 0.5*S`
saturation blend. Because `uColor=(1,0,0)`, the green/blue channels are pulled
*toward* `0.5*S` rather than to 0 → the silver pixel keeps brightness in G and B
and reads as **copper/bronze**, not pure red.

### Copper-ramp sanity (RedDye, opaque gray inputs)
```
in gray | out R out G out B   (uint8, straight alpha)
   0.20 |    60    43    43
   0.40 |   119    85    85
   0.60 |   170   132   132
   0.80 |   213   183   183     <- silver -> copper/bronze (NOT pure red)
   1.00 |   255   234   234
```
High R, substantial-and-equal G=B (G=B forced because `uColor.g==uColor.b==0`),
brightness preserved — exactly a copper ramp. Naive `uColor*lum*sat` would give
`(lum,0,0)` (pure red); this does not.

---

## 2) `ArmorColoredAndBlack`  (black-dye variant, e.g. item 1019)

Identical preshader (`c0=1-uColor`, `c1=(uSat<=1?1:1/uSat)`, `c2=1-c1`).
Same body as `ArmorColored` **plus** an extra `def c6 = (0.66, 0.33, 0, 0)` and a
final darkening multiply:

```
... identical recolor producing rgb' (premultiplied, before *src.a) ...
satA  = D * c6.x + c6.y          ; = (M-m)*uSat*0.66 + 0.33   (extra factor in [0.33..~1])
rgb   = rgb' * src.a
out.rgb = satA * rgb             ; darkens low-chroma (black) regions
```
i.e. **`out = ArmorColored_rgb * (0.33 + 0.66*(M-m)*uSaturation)`** then `*src.a`.
Low-saturation pixels are pushed toward black; saturated pixels keep the dye.

Ramp (uColor=(1,0,0), uSat=1.2, premult a=1): `0.8 -> (0.275, 0.237, 0.237)` —
the copper tone, globally darkened.

---

## 3) `ArmorColoredAndSilverTrim`  (silver-dye variant, e.g. item 1051)

Preshader uses **only `uSaturation`** (`in0 = uSaturation`); `uColor` is used
directly. Outputs: `c0 = (uSat<=1?1:1/uSat)`, `c1 = 1-c0`. Body:

```
def c4, 0.5, 1.5, 1.0, 0.0
texld r0, t0, s0
... M, m as before ...
add  r1.x, M, m            ; S
add  r1.y, M, -m           ; D
mul  r1.y, r1.y, c3.x      ; D *= uSaturation
mad  r1.y, r1.y, c0.x, c1.x ; D = D*c0 + c1   (c0=(uSat<=1?1:1/uSat), c1=1-c0)
mul  r1.x, r1.x, c4.x      ; lum = 0.5*S
mul  r1.x, r1.y, r1.x      ; w   = D*lum
mul  r1.x, r0.w, r1.x      ; w  *= src.a
mul  r2.xyz, r0, c2        ; premulTint = src.rgb * uColor
mad  r3.xyz, w, c4.y, -r2  ; t = w*1.5 - premulTint
mad  r0.xyz, r1.y, r3, r2  ; out = D*t + premulTint
min  r1, r0, c4.z          ; clamp to 1.0
mul  r0, r1, v0
mov  oC0, r0
```
Formula (premult):
```
S = M+m;  D = (M-m)*uSaturation;  D = D*c0 + c1   # c0=(uSat<=1?1:1/uSat), c1=1-c0
w   = D * (0.5*S) * src.a
tint= src.rgb * uColor                            # premultiplied tint
out.rgb = min( D*(1.5*w - tint) + tint , 1.0 )
```
This keeps the original texel's tint (the "silver trim" stays metallic) while
mixing in the colored component; it is more saturated than `ArmorColored`.
Ramp (uColor=(1,0,0), uSat=1.2, premult a=1): `0.8 -> (0.70, 0.033, 0.033)`.

---

## 4) `ArmorBrightnessColored`  (e.g. items 1037, 1050, 2871, 3558)

**No preshader, no saturation.** `uColor` at `c0`, `def c1 = (1/3, 0, 0, 0)`.
```
def c1, 0.333333, 0, 0, 0
texld r0, t0, s0
add r1.w, r0.y, r0.x
add r1.x, r0.z, r1.w       ; sum = r+g+b
mul r1.x, r1.x, c1.x       ; lum = (r+g+b)/3
mul r1.xyz, r1.x, c0       ; rgb = lum * uColor
mul r0.xyz, r0.w, r1       ; *= src.a
mul r0, r0, v0
mov oC0, r0
```
Formula (premult): **`out.rgb = ((r+g+b)/3) * uColor * src.a`**. Pure
luminance×color. With `uColor=(0.6,0.6,0.6)` it is a brightness/darken dye;
with `(1.5,1.5,1.5)` a brightening dye. (This is the correct "naive" recolor —
the basic-color dyes deliberately do NOT use this; they use `ArmorColored`.)

---

## Ready-to-use Python (`apply_armor_colored`)

Operates on **straight-alpha** RGBA `uint8` (premultiplies internally to match the
shader, then un-premultiplies the result). Full source in
`temp/xnb_probe/armor_colored_impl.py`.

```python
import numpy as np

def _preshader(uColor, uSaturation):
    uColor = np.asarray(uColor, dtype=np.float64)
    c0 = 1.0 - uColor                                       # vec3
    c1 = 1.0 if uSaturation <= 1.0 else 1.0 / uSaturation   # scalar
    c2 = 1.0 - c1
    return c0, c1, c2

def _armor_colored_premul(src, uColor, uSaturation):
    """src: (...,4) premultiplied RGBA in [0,1]. Returns premultiplied RGBA."""
    uColor = np.asarray(uColor, dtype=np.float64)
    c0, c1, c2 = _preshader(uColor, uSaturation)
    r, g, b, a = src[..., 0], src[..., 1], src[..., 2], src[..., 3]
    M = np.maximum(np.maximum(r, g), b)
    m = np.minimum(np.minimum(r, g), b)
    S = M + m
    D = (M - m) * uSaturation
    D = D * c1 + c2
    gfac = -0.5 * S + 1.5
    gray = (-gfac[..., None]) * c0 + 1.0          # 1 - gfac*(1-uColor)
    mask = -0.5 * S + 0.5                          # >=0 iff S<=1
    tint = S[..., None] * uColor
    base = np.where((mask >= 0.0)[..., None], tint, gray)
    base = base - 0.5 * S[..., None]
    rgb = D[..., None] * base + (0.5 * S)[..., None]
    rgb = a[..., None] * rgb                       # re-premultiply
    out = np.empty_like(src)
    out[..., 0:3] = rgb
    out[..., 3] = a
    return out

def apply_armor_colored(rgba_bytes, uColor, uSaturation):
    """ArmorColored dye on STRAIGHT-alpha uint8 RGBA (...,4).
    uColor=(r,g,b) e.g. (1,0,0); uSaturation e.g. 1.2. Returns uint8 straight RGBA."""
    arr = np.asarray(rgba_bytes, dtype=np.float64) / 255.0
    a = arr[..., 3]
    premul = arr.copy()
    premul[..., 0:3] = arr[..., 0:3] * a[..., None]   # straight -> premultiplied
    premul[..., 3] = a
    outp = _armor_colored_premul(premul, uColor, uSaturation)
    oa = outp[..., 3]
    nz = oa > 1e-6
    straight = outp.copy()
    straight[..., 0:3] = np.where(nz[..., None],
                                  outp[..., 0:3] / np.where(nz, oa, 1.0)[..., None], 0.0)
    return (np.clip(straight, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
```

### Notes for integration
- `texld` returns the **premultiplied** texel; this code premultiplies straight
  input and un-premultiplies output to slot into a straight-alpha pipeline.
- Vertex color `v0` (Terraria's lighting/alpha tint) is intentionally omitted.
  To match in-game exactly, multiply the *premultiplied* result by your draw
  color (RGBA, premultiplied) before un-premultiplying.
- The branch select (`cmp`) only diverges for premultiplied texels with
  `max+min > 1` (very bright opaque pixels); for typical sprite shading both
  branches are near-continuous.
```
