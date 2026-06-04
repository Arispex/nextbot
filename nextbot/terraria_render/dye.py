"""Armor dye shaders (numpy), recovered from Terraria's compiled ps_2_0 bytecode.

Faithful ports of every dye pass used by real dye items (see research/
dye_passes_spec.md + dye_shader_spec.md), in three tiers:

* exact-static  — bit-for-bit recolors (ArmorColored family, Invert, ColorOnly,
  gradient family, Brightness*, Martian, Polarized, Mushroom, Wisp, rainbow…).
* time-animated — sincos/triangle-wave passes evaluated at a representative
  ``uTime = 0`` still (Living*, Flow, Acid, Solar, Void, Hades, Mirage, Loki,
  MidnightRainbow…).
* noise-sampling — Gel/Phase/Nebula/Vortex/Stardust/Shifting*/Fog/HallowBoss and
  the ArmorTwilight hair dye run the *real* compiled bytecode (``dye_noise``)
  against the shipped ``noise.png`` / ``Extra_156.png`` at ``uTime = 0`` — accurate,
  spatially-varying. They fall back to a documented flat approximation only if the
  noise asset is missing (offline-safe).
* view APPROX — the two Reflective passes sample live lighting (`uLightSource`),
  which is 0 offline; collapsed to the documented static tint (no asset can fix).

Operates on STRAIGHT-alpha (h, w, 4) uint8 arrays. XNA textures are premultiplied,
so we re-premultiply, run the shader (premultiplied-in / premultiplied-out), then
convert back to straight alpha — exactly as the bytecode's `texld` + trailing
`mul r0, r0.w, r1` does.

Single-letter math variables (M/m/S/D/L…) and uColor/uSat/uSecondary mirror the
disassembled shader + spec; see this module's ruff per-file-ignores.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Union

import numpy as np

from . import dye_noise

if TYPE_CHECKING:
    from collections.abc import Sequence

# A color uniform: an (r,g,b) sequence, an already-normalized ndarray, or None
# (use the per-pass default). _col() normalizes any of these.
ColorLike = Union["Sequence[float]", np.ndarray, None]

# Frame width of one player cell; the gradient/rainbow family normalizes pixel-x
# by 1/uSourceRect.z = 1/40 (Apply() sets uSourceRect = legFrame = (0,0,40,56)).
FRAME_W = 40
# Representative still: GlobalTimeWrappedHourly frozen at 0 for animated passes.
UTIME = 0.0
# Frame geometry threaded from the compositor for noise-sampling passes:
# src_rect = the cell's (x,y,w,h) in its sheet; sheet_size = (W,H). The noise uv
# depends on the cell's position in the 360x224 armor grid (noise_dyes_spec.md §3).
# Defaults treat the cropped cell as its own 40x56 sheet (correct tiling for the
# dominant frac(uv*128) scale); the compositor passes the true values.
SrcRect = tuple[int, int, int, int]
SheetSize = tuple[int, int]
_DEFAULT_RECT: SrcRect = (0, 0, 40, 56)
_DEFAULT_SHEET: SheetSize = (40, 56)


# ── premult/un-premult wrapper (matches the bytecode's texld + final *src.a) ──
def _run(
    arr_u8: np.ndarray,
    fn_premul: Callable[[np.ndarray, np.ndarray, np.ndarray, np.ndarray], np.ndarray],
) -> np.ndarray:
    """fn_premul(r,g,b,a) takes premultiplied channels, returns premult (...,3) rgb."""
    arr = arr_u8.astype(np.float64) / 255.0
    a = arr[..., 3]
    pr = arr.copy()
    pr[..., :3] = arr[..., :3] * a[..., None]
    out = fn_premul(pr[..., 0], pr[..., 1], pr[..., 2], a)
    nz = a > 1e-6
    rgb = np.where(nz[..., None], out / np.where(nz, a, 1.0)[..., None], 0.0)
    res = arr.copy()
    res[..., :3] = rgb
    res = np.clip(res, 0.0, 1.0)
    return (res * 255.0 + 0.5).astype(np.uint8)


def _col(c: ColorLike, default: Sequence[float]) -> np.ndarray:
    return np.asarray(default if c is None else c, dtype=np.float64)


# ── shared math (bytecode-exact, validated) ──────────────────────────
def _rainbow_rgb(h: np.ndarray) -> np.ndarray:
    """Triangle-wave rainbow for scalar phase h -> (...,3). Bytecode-exact."""
    h = np.asarray(h, dtype=np.float64)[..., None]
    base = 1.8 * h + np.array([-0.4, 0.266, -0.067])
    tri = np.abs(base)
    tri = tri - np.floor(tri)
    fold = np.where(base >= 0, tri, -tri)
    return 1.0 - np.abs(fold * 3.0 - 1.0)


def _recolor_premul(
    r: np.ndarray, g: np.ndarray, b: np.ndarray, a: np.ndarray,
    COL: np.ndarray, uSat: float,
    *, sat_c1: float | None = None, sat_c2: float | None = None,
) -> np.ndarray:
    """ArmorColored HSL recolor toward per-pixel/scalar COL. Premultiplied rgb out."""
    M = np.maximum(np.maximum(r, g), b)
    m = np.minimum(np.minimum(r, g), b)
    S = M + m
    c1 = (1.0 if uSat <= 1 else 1.0 / uSat) if sat_c1 is None else sat_c1
    c2 = (1.0 - c1) if sat_c2 is None else sat_c2
    D = (M - m) * uSat * c1 + c2
    gf = -0.5 * S + 1.5
    gray = 1.0 - gf[..., None] * (1.0 - COL)
    mask = -0.5 * S + 0.5
    tint = S[..., None] * COL
    base = np.where((mask >= 0)[..., None], tint, gray) - 0.5 * S[..., None]
    return (D[..., None] * base + 0.5 * S[..., None]) * a[..., None]


def _gradient_col(
    h: int, w: int, uColor: np.ndarray, uSecondary: np.ndarray, *, frame_w: int = FRAME_W,
) -> np.ndarray:
    """Left->right gradient COL across the frame (uv.x only). Returns (h,w,3)."""
    p = ((np.arange(w) + 0.5) / frame_w)[None, :]
    s = (3 - 2 * p) * p * p
    col = (1.8 * s - 0.4)[..., None] * (uSecondary - uColor) + uColor
    return np.broadcast_to(col, (h, w, 3))


# ── ArmorColored family (validated bit-for-bit in dye_shader_spec.md) ─
def _armor_colored(
    arr_u8: np.ndarray, uColor: ColorLike, uSat: float,
) -> np.ndarray:
    uC = np.asarray(uColor, dtype=np.float64)
    return _run(arr_u8, lambda r, g, b, a: _recolor_premul(r, g, b, a, uC, uSat))


def _armor_colored_andblack(
    arr_u8: np.ndarray, uColor: ColorLike, uSat: float,
) -> np.ndarray:
    """ArmorColored, then darken by (0.33 + 0.66*(M-m)*uSat)."""
    out = _armor_colored(arr_u8, uColor, uSat)
    f = arr_u8.astype(np.float64) / 255.0
    chroma = np.max(f[..., :3], axis=2) - np.min(f[..., :3], axis=2)
    k = np.clip(0.33 + 0.66 * chroma * uSat, 0.0, 1.0)
    out[..., :3] = (out[..., :3].astype(np.float64) * k[..., None] + 0.5).astype(np.uint8)
    return out


def _armor_colored_silvertrim(
    arr_u8: np.ndarray, uColor: ColorLike, uSat: float,
) -> np.ndarray:
    uC = np.asarray(uColor, dtype=np.float64)

    def f(r, g, b, a):
        M = np.maximum(np.maximum(r, g), b)
        m = np.minimum(np.minimum(r, g), b)
        S = M + m
        c0 = 1.0 if uSat <= 1 else 1.0 / uSat
        c1 = 1.0 - c0
        D = (M - m) * uSat * c0 + c1
        w = D * (0.5 * S) * a
        tint = np.stack([r, g, b], -1) * uC
        return np.minimum(D[..., None] * (1.5 * w[..., None] - tint) + tint, 1.0)

    return _run(arr_u8, f)


def _brightness_colored(arr_u8: np.ndarray, uColor: ColorLike) -> np.ndarray:
    """ArmorBrightnessColored: out.rgb = (r+g+b)/3 * uColor (premult)."""
    uC = np.asarray(uColor, dtype=np.float64)
    return _run(arr_u8, lambda r, g, b, a: (((r + g + b) / 3.0)[..., None] * uC) * a[..., None])


def _brightness_clip(arr_u8: np.ndarray, uColor: ColorLike) -> np.ndarray:
    """Brightness recolor clamped to a displayable color (for over-unity APPROX)."""
    return _brightness_colored(arr_u8, np.clip(np.asarray(uColor, dtype=np.float64), 0.0, 1.5))


# ── exact-static special passes ──────────────────────────────────────
def _color_only(arr_u8: np.ndarray) -> np.ndarray:
    """ColorOnly: white silhouette (out = src.a everywhere; v0=white)."""
    return _run(arr_u8, lambda r, g, b, a: np.broadcast_to(a[..., None], (*a.shape, 3)) * a[..., None])


def _invert(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorInvert: invert the premultiplied rgb, re-premultiply by a."""
    return _run(arr_u8, lambda r, g, b, a: (1.0 - np.stack([r, g, b], -1)) * a[..., None])


def _colored_gradient(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uSat: float,
) -> np.ndarray:
    uC = _col(uColor, [1.0, 0.0, 0.0])
    uS = _col(uSecondary, [1.0, 1.0, 0.0])
    h, w = arr_u8.shape[:2]
    COL = _gradient_col(h, w, uC, uS)
    # Same recolor as ArmorColored, but with a per-pixel COL that varies along uv.x;
    # uses the DEFAULT sat remap (c2 = 1 - c1), NOT -c1 (the latter inverts low-chroma
    # pixels -> cyan). See research/dye_passes_spec.md gradient-family note.
    return _run(arr_u8, lambda r, g, b, a: _recolor_premul(r, g, b, a, COL, uSat))


def _colored_andblack_gradient(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uSat: float = 1.5,
) -> np.ndarray:
    out = _colored_gradient(arr_u8, uColor, uSecondary, uSat)
    f = arr_u8.astype(np.float64) / 255.0
    chroma = f[..., :3].max(2) - f[..., :3].min(2)
    k = np.clip(0.33 + 0.66 * chroma * uSat, 0.0, 1.0)
    out[..., :3] = (out[..., :3].astype(np.float64) * k[..., None] + 0.5).astype(np.uint8)
    return out


def _colored_silvertrim_gradient(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uSat: float = 1.5,
) -> np.ndarray:
    uC = _col(uColor, [1.0, 0.0, 0.0])
    uS = _col(uSecondary, [1.0, 1.0, 0.0])
    h, w = arr_u8.shape[:2]
    COL = _gradient_col(h, w, uC, uS)

    def f(r, g, b, a):
        M = np.maximum(np.maximum(r, g), b)
        m = np.minimum(np.minimum(r, g), b)
        S = M + m
        c0 = 1.0 if uSat <= 1 else 1.0 / uSat
        c1 = 1.0 - c0  # DEFAULT remap (matches _armor_colored_silvertrim), NOT -c0
        D = (M - m) * uSat * c0 + c1
        w_ = D * (0.5 * S) * a
        tint = np.stack([r, g, b], -1) * COL
        return np.minimum(D[..., None] * (1.5 * w_[..., None] - tint) + tint, 1.0)

    return _run(arr_u8, f)


def _brightness_gradient(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike,
) -> np.ndarray:
    uC = _col(uColor, [1.0, 0.0, 0.0])
    uS = _col(uSecondary, [1.0, 1.0, 0.0])
    h, w = arr_u8.shape[:2]
    COL = _gradient_col(h, w, uC, uS)
    return _run(arr_u8, lambda r, g, b, a: COL * ((r + g + b) * 0.5)[..., None] * a[..., None])


def _colored_rainbow(arr_u8: np.ndarray, uSat: float = 1.0) -> np.ndarray:
    h_, w = arr_u8.shape[:2]
    p = ((np.arange(w) + 0.5) / FRAME_W)[None, :]
    s = (3 - 2 * p) * p * p
    hue = 1.8 * s - 0.4
    COL = np.broadcast_to(_rainbow_rgb(hue), (h_, w, 3))
    # ArmorColored recolor toward a per-pixel rainbow COL; DEFAULT sat remap (c2 = 1 - c1),
    # NOT -c1 (which inverts low-chroma pixels). See dye_passes_spec.md gradient-family note.
    return _run(arr_u8, lambda r, g, b, a: _recolor_premul(r, g, b, a, COL, uSat))


def _brightness_rainbow(arr_u8: np.ndarray) -> np.ndarray:
    h_, w = arr_u8.shape[:2]
    p = ((np.arange(w) + 0.5) / FRAME_W)[None, :]
    s = (3 - 2 * p) * p * p
    hue = 1.8 * s - 0.4
    COL = np.broadcast_to(_rainbow_rgb(hue), (h_, w, 3))
    return _run(arr_u8, lambda r, g, b, a: COL * ((r + g + b) * 0.5)[..., None] * a[..., None])


def _martian(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorMartian: chroma-based metallic recolor toward hardcoded (0,2,3)."""
    C = np.array([0.0, 2.0, 3.0])

    def f(r, g, b, a):
        L = r + g + b
        M = np.maximum(np.maximum(r, g), b)
        chroma = L - M
        half = chroma * 0.5
        baseS = -chroma * 0.5 + M
        rgb = baseS[..., None] * C + half[..., None]
        return rgb * a[..., None]

    return _run(arr_u8, f)


def _polarized(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorPolarized: posterize to gray by luminance threshold (L<1.8)."""
    def f(r, g, b, a):
        L = r + g + b
        gray = np.where((-L / 3.0 + 0.6) >= 0, L / 6.0, L / 6.0 + 0.5)
        return np.stack([gray, gray, gray], -1) * a[..., None]

    return _run(arr_u8, f)


def _mushroom(arr_u8: np.ndarray, uColor: ColorLike = (0.05, 0.2, 1.0)) -> np.ndarray:
    uC = _col(uColor, [0.05, 0.2, 1.0])

    def f(r, g, b, a):
        L = r + g + b
        x = L / 3.0 - 0.3
        y = x * (5.0 / 3.0)
        z = y * (-2.0) + 3.0
        bump = z * (-(y * y)) + 1.0
        base = np.stack([r, g, b], -1) * 0.25
        rgb = bump[..., None] * (uC * bump[..., None] - base) + base
        rgb = np.where((x >= 0)[..., None], rgb, 0.0)
        return rgb * a[..., None]

    return _run(arr_u8, f)


def _wisp(arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike) -> np.ndarray:
    """ArmorWisp: 3-band luminance zoning between uColor and uSecondaryColor."""
    uC = _col(uColor, [0.7, 1.0, 0.9])
    uS = _col(uSecondary, [0.35, 0.85, 0.8])
    c0 = uC - uS
    c1 = -uC

    def f(r, g, b, a):
        L = r + g + b
        La = L / 3.0 - 0.2
        Lb = L / 3.0 - 0.4
        r1 = np.minimum(Lb * 5.0, 1.0)[..., None] * c1 + uC
        r2 = (La * 5.0)[..., None] * c0 + uS
        sel = np.where((Lb >= 0)[..., None], r1, r2)
        sel = np.where((La >= 0)[..., None], sel, uS)
        return np.minimum(sel * a[..., None], 1.0)

    return _run(arr_u8, f)


def _high_contrast_glow(
    arr_u8: np.ndarray, uColor: ColorLike = (0.0, 1.0, 0.0), uSat: float = 1.0,
) -> np.ndarray:
    """ArmorHighContrastGlow: ArmorColored recolor (v0-driven glow term dropped)."""
    return _armor_colored(arr_u8, _col(uColor, [0.0, 1.0, 0.0]), uSat)


# ── time-animated passes (representative still at uTime=0) ────────────
def _flow(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uTime: float = UTIME,
) -> np.ndarray:
    uC = _col(uColor, [1.0, 0.5, 1.0])
    uS = _col(uSecondary, [0.6, 0.1, 1.0])

    def f(r, g, b, a):
        L = r + g + b
        ph = (L * 4.0 + uTime) * 0.159155 + 0.5
        ph = ph - np.floor(ph)
        tw = np.sin(ph * 6.28319 - 3.14159)
        w = 0.5 * np.sign(tw) + 0.5
        COL = (uS - uC) * w[..., None] + uC
        return COL * (L / 3.0)[..., None] * a[..., None]

    return _run(arr_u8, f)


def _living_rainbow(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    h_, w = arr_u8.shape[:2]
    p = ((np.arange(w) + 0.5) / FRAME_W)[None, :]
    s = (3 - 2 * p) * p * p
    posv = s * 0.4 + uTime * 0.8

    def f(r, g, b, a):
        L = r + g + b
        hue = posv + (L * 0.15)
        COL = _rainbow_rgb(hue)
        wgt = (L * 0.5)[..., None]
        return np.minimum(COL * wgt * a[..., None], 1.0)

    return _run(arr_u8, f)


def _living_flame(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uTime: float = UTIME,
) -> np.ndarray:
    """LivingFlame: positional+luma phase band mixing uColor<->uSecondary, uTime=0."""
    uC = _col(uColor, [1.0, 0.9, 0.0])
    uS = _col(uSecondary, [1.0, 0.2, 0.0])
    h_, w = arr_u8.shape[:2]
    p = ((np.arange(w) + 0.5) / FRAME_W)[None, :]
    s = (3 - 2 * p) * p * p

    def f(r, g, b, a):
        L = r + g + b
        ph = (s * 0.4 + L * 0.15 + uTime) + 0.5
        ph = ph - np.floor(ph)
        tw = np.sin(ph * 6.28319 - 3.14159)
        wgt = 0.5 * np.sign(tw) + 0.5
        COL = (uS - uC) * wgt[..., None] + uC
        return np.minimum(COL * (L * 0.5)[..., None] * a[..., None], 1.0)

    return _run(arr_u8, f)


def _living_ocean(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """LivingOcean: fixed blue/cyan palette band (uColor unused), uTime=0."""
    base = np.array([0.0, 0.4, 1.0])
    alt = np.array([0.0, 1.0, 1.0])
    h_, w = arr_u8.shape[:2]
    p = ((np.arange(w) + 0.5) / FRAME_W)[None, :]
    s = (3 - 2 * p) * p * p

    def f(r, g, b, a):
        L = r + g + b
        ph = (s * 0.4 + L * 0.15 + uTime) + 0.5
        ph = ph - np.floor(ph)
        tw = np.sin(ph * 6.28319 - 3.14159)
        wgt = 0.5 * np.sign(tw) + 0.5
        COL = (alt - base) * wgt[..., None] + base
        return np.minimum(COL * (L * 0.5)[..., None] * a[..., None], 1.0)

    return _run(arr_u8, f)


def _acid(arr_u8: np.ndarray, uColor: ColorLike, uTime: float = UTIME) -> np.ndarray:
    """ArmorAcid: swirling polar-coordinate band toward uColor, uTime=0 still."""
    uC = _col(uColor, [0.5, 1.0, 0.3])
    h_, w = arr_u8.shape[:2]
    yy, xx = np.mgrid[0:h_, 0:w].astype(np.float64)
    cx = (xx + 0.5) / FRAME_W - 0.5
    cy = (yy + 0.5) / 56.0 - 0.5
    ang = np.arctan2(cy, cx)

    def f(r, g, b, a):
        L = r + g + b
        ph = (ang / 6.28319 + L * 0.15 + uTime) + 0.5
        ph = ph - np.floor(ph)
        tw = np.sin(ph * 6.28319 - 3.14159)
        wgt = 0.5 * np.sign(tw) + 0.5
        COL = uC * (0.4 + 0.6 * wgt)[..., None]
        return np.minimum(COL * (L * 0.5)[..., None] * a[..., None], 1.0)

    return _run(arr_u8, f)


def _midnight_rainbow(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """MidnightRainbow APPROX: rainbow recolor over the source (self-emboss dropped)."""
    return _colored_rainbow(arr_u8)


def _solar(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 0.0, 0.0),
    uSecondary: ColorLike = (1.0, 1.0, 0.0), uTime: float = UTIME,
) -> np.ndarray:
    """ArmorSolar APPROX: emissive lava/fire heat ramp from source luminance.

    The real ArmorSolar bytecode (5 self-taps + a `sincos(uTime)` rotation) builds its
    fiery glow as an ADDITIVE emissive term carried on the vertex color `v0`; running it
    with v0=white (offline) collapses the glow to a flat pale wash and loses the fire
    hue entirely (verified). So this is a deliberate fire approximation, NOT the literal
    shader: map source brightness to a Solar-pillar heat ramp uColor(ember red) ->
    uSecondary(yellow) -> white-hot, scaled emissively (dark embers stay dim, hot cores
    bloom to near-white). Reads as bright orange/yellow lava with hot highlights, not the
    dark red the old `_brightness_clip(uColor)` produced."""
    uC = _col(uColor, [1.0, 0.0, 0.0])
    uS = _col(uSecondary, [1.0, 1.0, 0.0])
    white = np.array([1.0, 1.0, 1.0])

    def f(r, g, b, a):
        L = (r + g + b) / 3.0                     # premult luma in [0,1]
        t = np.clip((L - 0.04) * 2.0, 0.0, 1.0)   # heat: lift mids toward yellow
        lo = (t * 2.0)[..., None]                 # ramp segment 1: ember -> yellow
        hi = (t * 2.0 - 1.0)[..., None]           # ramp segment 2: yellow -> white-hot
        low = uC + (uS - uC) * np.clip(lo, 0.0, 1.0)
        high = uS + (white - uS) * np.clip(hi, 0.0, 1.0)
        col = np.where((t < 0.5)[..., None], low, high)
        return col * (0.4 + 0.95 * t)[..., None] * a[..., None]  # emissive: dim cold, bloom hot

    return _run(arr_u8, f)


def _void(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """ArmorVoid APPROX: horizontal blur+darken collapsed -> dark tint."""
    return _brightness_clip(arr_u8, (0.35, 0.35, 0.35))


def _hades(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike = None,
    uTime: float = UTIME,
) -> np.ndarray:
    """ArmorHades APPROX: rotated self-taps collapsed -> uColor ember tint."""
    return _brightness_clip(arr_u8, _col(uColor, [0.5, 0.7, 1.3]))


def _mirage(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """ArmorMirage APPROX: self-tap wavy displacement collapses to passthrough."""
    return arr_u8


def _loki(
    arr_u8: np.ndarray, uColor: ColorLike = (0.1, 0.1, 0.1), uTime: float = UTIME,
) -> np.ndarray:
    """ArmorLoki APPROX: self-tap dark camo collapsed -> dark uColor tint."""
    return _brightness_clip(arr_u8, _col(uColor, [0.1, 0.1, 0.1]))


# ── emissive HDR tone-map (for the pillar/boss glow passes) ───────────
# Representative GlobalTimeWrappedHourly per emissive pillar/boss pass — chosen by
# sweeping uTime over a cycle and picking the BRIGHTEST, most characteristic still
# (research/dye_passes_spec.md "Time-animated"; sweep contact sheets in
# temp/xnb_probe/out/sweep_<name>.png). These are animated emissive effects in-game;
# uTime=0 lands on a dim phase for several, so each bakes its own bright frame.
_PILLAR_TIME: dict[str, float] = {
    "ArmorSolar": 5.0,       # brightness pulse c2=sin(uTime*0.477+0.5)*0.2+1 peaks ~1.13
    "ArmorNebula": 3.0,      # cloud uv-scroll phase with the most pink coverage
    "ArmorVortex": 0.5,      # swirl phase with the brightest teal energy streaks
    "ArmorStardust": 1.0,    # starfield phase showing the most bright white sparkles
    "ArmorHallowBoss": 0.0,  # palette uv barely time-shifts; 0 already bright pastel
}
# Per-pass emissive gain applied before the tone-map: a mild lift so the glow reads as
# bright (the in-game additive bloom) instead of a dark tint on the dark armor base.
# Tuned by eye (temp/xnb_probe/out/tonemap_<pass>.png): high enough to glow, low enough
# to keep the hue (>~2.0 washes everything to white). HallowBoss is already bright and
# in-gamut -> 1.0 (no gain, stays accurate).
_PILLAR_GAIN: dict[str, float] = {
    "ArmorNebula": 1.4,
    "ArmorVortex": 1.5,
    "ArmorStardust": 1.35,
    "ArmorHallowBoss": 1.0,
}


def _emissive_tonemap(rgb: np.ndarray, gain: float = 1.0) -> np.ndarray:
    """Map an over-unity (HDR) emissive rgb into [0,1] so >1 regions read as bright
    glow instead of hard-clipping to a flat primary.

    In-game these dyes are drawn additively (the shader output exceeds 1.0 and blooms);
    offline we have one LDR layer, so a plain clip to [0,1] turns the glow into a dark
    tint and skews the hue (a (1.3,1.45,1.73) sparkle clips to (1,1,1)'s neighbours,
    losing brightness). Instead: apply a mild per-pass `gain` (the bloom lift), then fold
    each pixel's overflow (max(channel)-1, when >0) back into ALL channels so a hot texel
    desaturates toward white (a glowing highlight) while sub-unity texels keep their hue.
    Hue is preserved below 1; only genuinely over-bright texels lift toward white."""
    g = rgb * gain
    m = np.max(g, axis=-1, keepdims=True)
    overflow = np.clip(m - 1.0, 0.0, None)  # how far the brightest channel exceeds 1
    lifted = g + overflow  # push the dimmer channels up by the same amount -> white-ish
    return np.clip(lifted, 0.0, 1.0)


# ── noise-sampling passes (real Misc/noise sampling via dye_noise) ────
def _noise_pass(
    arr_u8: np.ndarray, name: str, *, uColor: np.ndarray, uSecondary: np.ndarray,
    uSat: float, src_rect: SrcRect, sheet_size: SheetSize,
    fallback: Callable[[], np.ndarray],
    u_time: float = UTIME, emissive: bool = False, gain: float = 1.0,
) -> np.ndarray:
    """Run baked shader `name` per-pixel with real noise sampling (dye_noise).

    Premultiplies straight input, runs the actual ps_2_0 bytecode (premult-in/out),
    un-premultiplies — same wrapper as `_run`. Falls back to the documented APPROX
    when the baked blob / noise.png is absent (renderer never crashes offline).

    `u_time` freezes the still (emissive pillar passes bake a per-pass bright frame via
    `_PILLAR_TIME`). `emissive=True` tone-maps the over-unity output with `gain`
    (preserving the glow) instead of hard-clipping; non-pillar passes keep the hard clip.
    """
    arr = arr_u8.astype(np.float64) / 255.0
    a = arr[..., 3]
    pr = arr.copy()
    pr[..., :3] = arr[..., :3] * a[..., None]
    out = dye_noise.run_noise_pass(
        pr, name, u_color=np.asarray(uColor, dtype=np.float64),
        u_secondary=np.asarray(uSecondary, dtype=np.float64), u_sat=uSat,
        src_rect=src_rect, sheet_size=sheet_size, u_time=u_time)
    if out is None:
        return fallback()
    oa = out[..., 3]
    nz = oa > 1e-6
    rgb = np.where(nz[..., None], out[..., :3] / np.where(nz, oa, 1.0)[..., None], 0.0)
    res = arr.copy()
    res[..., :3] = _emissive_tonemap(rgb, gain) if emissive else np.clip(rgb, 0.0, 1.0)
    res = np.clip(res, 0.0, 1.0)
    return (res * 255.0 + 0.5).astype(np.uint8)


# ── view APPROX passes (uLightSource=0; no live light offline) ────────
def _reflective(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorReflective APPROX: uLightSource=0 -> no live specular -> passthrough."""
    return arr_u8


def _reflective_color(arr_u8: np.ndarray, uColor: ColorLike = (1.0, 1.0, 1.0)) -> np.ndarray:
    """ArmorReflectiveColor APPROX: uLightSource=0 -> tint source by uColor only."""
    return _brightness_clip(arr_u8, _col(uColor, [1.0, 1.0, 1.0]))


def _pillar_time(name: str, u_time: float | None) -> float:
    """Resolve the frozen uTime for an emissive pillar pass: the per-pass baked
    `_PILLAR_TIME` representative frame by default, or an explicit override (the
    dynamic-frame dev script sweeps this; production passes None -> unchanged)."""
    return _PILLAR_TIME[name] if u_time is None else u_time


def _gel(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.7, 1.4), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorGel: real Misc/noise jelly highlight; APPROX = brightness recolor by uColor."""
    uC = _col(uColor, [0.4, 0.7, 1.4])
    uS = _col(uSecondary, [0.0, 0.0, 0.1])
    return _noise_pass(arr_u8, "ArmorGel", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time,
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _phase(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.2, 1.5), uSat: float = 1.0,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorPhase: real noise window glow; APPROX = ArmorColored recolor by uColor."""
    uC = _col(uColor, [0.4, 0.2, 1.5])
    return _noise_pass(arr_u8, "ArmorPhase", uColor=uC, uSecondary=uC, uSat=uSat,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time,
                       fallback=lambda: _armor_colored(arr_u8, np.clip(uC, 0.0, 1.0), uSat))


def _nebula(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 0.0, 1.0), uSecondary: ColorLike = None,
    uSat: float = 1.0, *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorNebula: real noise cloud over recolor (emissive); APPROX = ArmorColored.

    Bright pink/purple nebula clouds: the bytecode adds a `_rainbow(noise)*uSecondary*5`
    cloud (over-unity) onto the uColor recolor. Frozen at a representative bright frame
    (`_PILLAR_TIME`) and tone-mapped so the cloud highlights read as glow, not a clip."""
    uC = _col(uColor, [1.0, 0.0, 1.0])
    uS = _col(uSecondary, [1.0, 1.0, 1.0])
    return _noise_pass(arr_u8, "ArmorNebula", uColor=uC, uSecondary=uS, uSat=uSat,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorNebula", u_time), emissive=True,
                       gain=_PILLAR_GAIN["ArmorNebula"],
                       fallback=lambda: _armor_colored(arr_u8, np.clip(uC, 0.0, 1.0), uSat))


def _vortex(
    arr_u8: np.ndarray, uColor: ColorLike = (0.1, 0.5, 0.35), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorVortex: real swirling noise (emissive); APPROX = brightness recolor.

    Teal/green energy glow: the bytecode swirls polar-coord noise into bright
    `uSecondary` streaks (over-unity) over a `uColor*luma` base. Frozen at a bright
    swirl phase and tone-mapped so the energy streaks read as glow."""
    uC = _col(uColor, [0.1, 0.5, 0.35])
    uS = _col(uSecondary, [1.0, 1.0, 1.0])
    return _noise_pass(arr_u8, "ArmorVortex", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorVortex", u_time), emissive=True,
                       gain=_PILLAR_GAIN["ArmorVortex"],
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _stardust(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.6, 1.0), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorStardust: real noise starfield (emissive); APPROX = uColor base.

    Deep blue (uColor*luma*0.666) with bright white star sparkles
    (`noise-threshold * uSecondary * 8`, strongly over-unity). Frozen at a phase
    (`_PILLAR_TIME`, via the now-correct uTime preshader input) showing the most
    sparkles; tone-mapped so the specks read as hot white stars, not clipped blue."""
    uC = _col(uColor, [0.4, 0.6, 1.0])
    uS = _col(uSecondary, [1.0, 1.0, 1.0])

    def approx() -> np.ndarray:
        return _run(arr_u8, lambda r, g, b, a: uC * ((r + g + b) * 0.667)[..., None] * a[..., None])

    return _noise_pass(arr_u8, "ArmorStardust", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorStardust", u_time), emissive=True,
                       gain=_PILLAR_GAIN["ArmorStardust"], fallback=approx)


def _shifting_sands(
    arr_u8: np.ndarray, uColor: ColorLike = (1.1, 1.0, 0.5), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorShiftingSands: real vertical-scroll noise; APPROX = brightness recolor."""
    uC = _col(uColor, [1.1, 1.0, 0.5])
    uS = _col(uSecondary, [0.7, 0.5, 0.3])
    return _noise_pass(arr_u8, "ArmorShiftingSands", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time,
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _shifting_pearlsands(
    arr_u8: np.ndarray, uColor: ColorLike = (1.1, 0.8, 0.9), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorShiftingPearlsands: real noise + pearlescent 2nd tap; APPROX = recolor."""
    uC = _col(uColor, [1.1, 0.8, 0.9])
    uS = _col(uSecondary, [0.35, 0.25, 0.44])
    return _noise_pass(arr_u8, "ArmorShiftingPearlsands", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time,
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _fog(
    arr_u8: np.ndarray, uColor: ColorLike = (0.95, 0.95, 0.95), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorFog: real noise soft overlay; APPROX = low-contrast gray lerp src->uColor."""
    uC = _col(uColor, [0.95, 0.95, 0.95])
    uS = _col(uSecondary, [0.3, 0.3, 0.3])

    def approx() -> np.ndarray:
        arr = arr_u8.astype(np.float64) / 255.0
        gray = (arr[..., :3].mean(2))[..., None]
        arr[..., :3] = np.clip(gray * uC * 0.5 + arr[..., :3] * 0.5, 0.0, 1.0)
        return (arr * 255.0 + 0.5).astype(np.uint8)

    return _noise_pass(arr_u8, "ArmorFog", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time, fallback=approx)


def _hallow_boss(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorHallowBoss: real Extra_156 palette lookup (emissive); APPROX = rainbow tint.

    Bright iridescent rainbow pastel: `out = src*0.2 + palette*0.8` from the Extra_156
    rainbow texture. Already bright/in-gamut; emissive tone-map is a safe no-op below 1
    and keeps any palette highlight from clipping."""
    return _noise_pass(arr_u8, "ArmorHallowBoss", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorHallowBoss", u_time), emissive=True,
                       gain=_PILLAR_GAIN["ArmorHallowBoss"],
                       fallback=lambda: _colored_rainbow(arr_u8))


def _twilight(
    arr_u8: np.ndarray, uColor: ColorLike = (0.5, 0.1, 1.0),
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorTwilight (Twilight hair dye #12): real two-tap noise purple glow over src.

    APPROX = brightness recolor by clamped uColor. The hair sheet has its own size
    (uImageSize0); the compositor threads it via src_rect/sheet_size.
    """
    uC = _col(uColor, [0.5, 0.1, 1.0])
    return _noise_pass(arr_u8, "ArmorTwilight", uColor=uC, uSecondary=uC, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time,
                       fallback=lambda: _brightness_clip(arr_u8, uC))


# ── dispatch ─────────────────────────────────────────────────────────
def apply_dye(
    arr_u8: np.ndarray, spec: dict[str, Any] | None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """Apply a dye to a straight-alpha (h,w,4) uint8 array. `spec` from dyes.json.

    `spec` carries `pass` and (where applicable) `color`/`secondary`/`sat`, baked
    from DyeInitializer.cs. Animated passes are evaluated at a representative still
    (uTime=0). Noise-sampling passes (Gel/Phase/Nebula/Vortex/Stardust/Shifting*/Fog/
    HallowBoss/Twilight) sample the real Misc/noise (or Extra_156) texture; `src_rect`
    (the cell's x,y,w,h in its sheet) + `sheet_size` (W,H) place the noise uv (the
    compositor threads them; non-noise passes ignore them). The two Reflective passes
    stay APPROX (uLightSource=0 offline). Unknown passes fall back to undyed (no crash).

    `u_time` overrides the frozen GlobalTimeWrappedHourly of the time-animated and
    noise-sampling passes (a phase for sweeping a dye's animation cycle; see the
    dynamic-frame dev script). `None` (production default) keeps each pass's baked
    representative still — UTIME=0 for the time/scroll passes, `_PILLAR_TIME[name]` for
    the emissive pillar passes — so the production byte-output is unchanged. The APPROX
    time passes (MidnightRainbow/Solar/Void/Hades/Mirage/Loki) ignore it (they have no
    real uTime formula offline — see research/dynamic_effects_catalog.md §A.1).
    """
    if not spec:
        return arr_u8
    name = spec.get("pass")
    color = spec.get("color")
    secondary = spec.get("secondary")
    sat = float(spec.get("sat", 1.0))
    geom = {"src_rect": src_rect, "sheet_size": sheet_size}
    ngeom = {**geom, "u_time": u_time}  # noise passes additionally take a phase override

    # exact-static
    if name == "ArmorColored":
        return _armor_colored(arr_u8, _col(color, [1.0, 1.0, 1.0]), sat)
    if name == "ArmorColoredAndBlack":
        return _armor_colored_andblack(arr_u8, _col(color, [1.0, 1.0, 1.0]), sat)
    if name == "ArmorColoredAndSilverTrim":
        return _armor_colored_silvertrim(arr_u8, _col(color, [1.0, 1.0, 1.0]), sat)
    if name == "ArmorBrightnessColored":
        return _brightness_colored(arr_u8, _col(color, [1.0, 1.0, 1.0]))
    if name == "ColorOnly":
        return _color_only(arr_u8)
    if name == "ArmorInvert":
        return _invert(arr_u8)
    if name == "ArmorColoredGradient":
        return _colored_gradient(arr_u8, color, secondary, sat)
    if name == "ArmorColoredAndBlackGradient":
        return _colored_andblack_gradient(arr_u8, color, secondary, sat or 1.5)
    if name == "ArmorColoredAndSilverTrimGradient":
        return _colored_silvertrim_gradient(arr_u8, color, secondary, sat or 1.5)
    if name == "ArmorBrightnessGradient":
        return _brightness_gradient(arr_u8, color, secondary)
    if name == "ArmorColoredRainbow":
        return _colored_rainbow(arr_u8, sat)
    if name == "ArmorBrightnessRainbow":
        return _brightness_rainbow(arr_u8)
    if name == "ArmorMartian":
        return _martian(arr_u8)
    if name == "ArmorPolarized":
        return _polarized(arr_u8)
    if name == "ArmorMushroom":
        return _mushroom(arr_u8, _col(color, [0.05, 0.2, 1.0]))
    if name == "ArmorWisp":
        return _wisp(arr_u8, color, secondary)
    if name == "ArmorHighContrastGlow":
        return _high_contrast_glow(arr_u8, _col(color, [0.0, 1.0, 0.0]), sat)

    # time-animated (representative uTime=0; u_time sweeps the real-formula ones)
    _ut = UTIME if u_time is None else u_time
    if name == "ArmorFlow":
        return _flow(arr_u8, color, secondary, _ut)
    if name == "ArmorLivingRainbow":
        return _living_rainbow(arr_u8, _ut)
    if name == "ArmorLivingFlame":
        return _living_flame(arr_u8, color, secondary, _ut)
    if name == "ArmorLivingOcean":
        return _living_ocean(arr_u8, _ut)
    if name == "ArmorAcid":
        return _acid(arr_u8, _col(color, [0.5, 1.0, 0.3]), _ut)
    if name == "ArmorMidnightRainbow":
        return _midnight_rainbow(arr_u8)
    if name == "ArmorSolar":
        return _solar(arr_u8, _col(color, [1.0, 0.0, 0.0]), _col(secondary, [1.0, 1.0, 0.0]))
    if name == "ArmorVoid":
        return _void(arr_u8)
    if name == "ArmorHades":
        return _hades(arr_u8, color, secondary)
    if name == "ArmorMirage":
        return _mirage(arr_u8)
    if name == "ArmorLoki":
        return _loki(arr_u8, _col(color, [0.1, 0.1, 0.1]))

    # view APPROX (uLightSource=0 offline -> no live specular)
    if name == "ArmorReflective":
        return _reflective(arr_u8)
    if name == "ArmorReflectiveColor":
        return _reflective_color(arr_u8, _col(color, [1.0, 1.0, 1.0]))

    # noise-sampling (real Misc/noise via dye_noise; APPROX fallback if asset missing)
    if name == "ArmorGel":
        return _gel(arr_u8, _col(color, [0.4, 0.7, 1.4]), secondary, **ngeom)
    if name == "ArmorPhase":
        return _phase(arr_u8, _col(color, [0.4, 0.2, 1.5]), sat, **ngeom)
    if name == "ArmorNebula":
        return _nebula(arr_u8, _col(color, [1.0, 0.0, 1.0]), secondary, sat, **ngeom)
    if name == "ArmorVortex":
        return _vortex(arr_u8, _col(color, [0.1, 0.5, 0.35]), secondary, **ngeom)
    if name == "ArmorStardust":
        return _stardust(arr_u8, _col(color, [0.4, 0.6, 1.0]), secondary, **ngeom)
    if name == "ArmorShiftingSands":
        return _shifting_sands(arr_u8, _col(color, [1.1, 1.0, 0.5]), secondary, **ngeom)
    if name == "ArmorShiftingPearlsands":
        return _shifting_pearlsands(arr_u8, _col(color, [1.1, 0.8, 0.9]), secondary, **ngeom)
    if name == "ArmorFog":
        return _fog(arr_u8, _col(color, [0.95, 0.95, 0.95]), secondary, **ngeom)
    if name == "ArmorHallowBoss":
        return _hallow_boss(arr_u8, **ngeom)
    if name == "ArmorTwilight":
        return _twilight(arr_u8, _col(color, [0.5, 0.1, 1.0]), **ngeom)

    # unknown / unlisted pass -> undyed
    return arr_u8
