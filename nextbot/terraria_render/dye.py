"""Armor dye shaders (numpy), recovered from Terraria's compiled ps_2_0 bytecode.

Faithful ports of every dye pass used by real dye items (see research/
dye_passes_spec.md + dye_shader_spec.md), in three tiers:

* exact-static  — bit-for-bit recolors (ArmorColored family, Invert, ColorOnly,
  gradient family, Brightness*, Martian, Polarized, Mushroom, Wisp, rainbow…).
* time-animated — sincos/triangle-wave passes evaluated at a representative
  ``uTime = 0`` still (Living*, Flow, Acid, Solar, Void, Hades, Mirage, Loki,
  MidnightRainbow…).
* view/noise APPROX — passes that sample live lighting (`uLightSource`) or an
  external noise/Extra texture we don't ship; collapsed to a documented static
  approximation (Reflective(Color), Gel, Phase, Nebula, Vortex, Stardust,
  Shifting*, Fog, HallowBoss).

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
    """ArmorSolar APPROX: self-emboss collapsed to DC -> fiery uColor tint."""
    return _brightness_clip(arr_u8, uColor)


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


# ── view/noise APPROX passes (uLightSource=0, noise=const) ────────────
def _reflective(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorReflective APPROX: uLightSource=0 -> no live specular -> passthrough."""
    return arr_u8


def _reflective_color(arr_u8: np.ndarray, uColor: ColorLike = (1.0, 1.0, 1.0)) -> np.ndarray:
    """ArmorReflectiveColor APPROX: uLightSource=0 -> tint source by uColor only."""
    return _brightness_clip(arr_u8, _col(uColor, [1.0, 1.0, 1.0]))


def _gel(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.7, 1.4),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorGel APPROX (noise=const): brightness recolor by clamped uColor."""
    return _brightness_clip(arr_u8, _col(uColor, [0.4, 0.7, 1.4]))


def _phase(arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.2, 1.5), uSat: float = 1.0) -> np.ndarray:
    """ArmorPhase APPROX (noise=const): ArmorColored recolor of source by uColor."""
    return _armor_colored(arr_u8, np.clip(_col(uColor, [0.4, 0.2, 1.5]), 0.0, 1.0), uSat)


def _nebula(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 0.0, 1.0),
    uSecondary: ColorLike = None, uSat: float = 1.0,
) -> np.ndarray:
    """ArmorNebula APPROX (noise=const): ArmorColored recolor by uColor."""
    return _armor_colored(arr_u8, np.clip(_col(uColor, [1.0, 0.0, 1.0]), 0.0, 1.0), uSat)


def _vortex(
    arr_u8: np.ndarray, uColor: ColorLike = (0.1, 0.5, 0.35),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorVortex APPROX (noise=const): brightness recolor by uColor."""
    return _brightness_clip(arr_u8, _col(uColor, [0.1, 0.5, 0.35]))


def _stardust(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.6, 1.0),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorStardust APPROX (noise=const -> sparkle~0): base = uColor*(r+g+b)*0.667."""
    uC = _col(uColor, [0.4, 0.6, 1.0])
    return _run(arr_u8, lambda r, g, b, a: uC * ((r + g + b) * 0.667)[..., None] * a[..., None])


def _shifting_sands(
    arr_u8: np.ndarray, uColor: ColorLike = (1.1, 1.0, 0.5),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorShiftingSands APPROX (noise=const): brightness recolor by uColor."""
    return _brightness_clip(arr_u8, _col(uColor, [1.1, 1.0, 0.5]))


def _shifting_pearlsands(
    arr_u8: np.ndarray, uColor: ColorLike = (1.1, 0.8, 0.9),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorShiftingPearlsands APPROX (noise=const): brightness recolor by uColor."""
    return _brightness_clip(arr_u8, _col(uColor, [1.1, 0.8, 0.9]))


def _fog(
    arr_u8: np.ndarray, uColor: ColorLike = (0.95, 0.95, 0.95),
    uSecondary: ColorLike = None, uTime: float = UTIME,
) -> np.ndarray:
    """ArmorFog APPROX (noise=const): low-contrast gray lerp source->uColor by 0.5."""
    uC = _col(uColor, [0.95, 0.95, 0.95])
    arr = arr_u8.astype(np.float64) / 255.0
    gray = (arr[..., :3].mean(2))[..., None]
    arr[..., :3] = np.clip(gray * uC * 0.5 + arr[..., :3] * 0.5, 0.0, 1.0)
    return (arr * 255.0 + 0.5).astype(np.uint8)


def _hallow_boss(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """ArmorHallowBoss APPROX (Extra_156=const): positional rainbow tint of source."""
    return _colored_rainbow(arr_u8)


# ── dispatch ─────────────────────────────────────────────────────────
def apply_dye(arr_u8: np.ndarray, spec: dict[str, Any] | None) -> np.ndarray:
    """Apply a dye to a straight-alpha (h,w,4) uint8 array. `spec` from dyes.json.

    `spec` carries `pass` and (where applicable) `color`/`secondary`/`sat`, baked
    from DyeInitializer.cs. Animated passes are evaluated at a representative still
    (uTime=0); view/noise passes use the documented static approximation. Unknown
    passes fall back to undyed (no crash).
    """
    if not spec:
        return arr_u8
    name = spec.get("pass")
    color = spec.get("color")
    secondary = spec.get("secondary")
    sat = float(spec.get("sat", 1.0))

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

    # time-animated (representative uTime=0)
    if name == "ArmorFlow":
        return _flow(arr_u8, color, secondary)
    if name == "ArmorLivingRainbow":
        return _living_rainbow(arr_u8)
    if name == "ArmorLivingFlame":
        return _living_flame(arr_u8, color, secondary)
    if name == "ArmorLivingOcean":
        return _living_ocean(arr_u8)
    if name == "ArmorAcid":
        return _acid(arr_u8, _col(color, [0.5, 1.0, 0.3]))
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

    # view/noise APPROX
    if name == "ArmorReflective":
        return _reflective(arr_u8)
    if name == "ArmorReflectiveColor":
        return _reflective_color(arr_u8, _col(color, [1.0, 1.0, 1.0]))
    if name == "ArmorGel":
        return _gel(arr_u8, _col(color, [0.4, 0.7, 1.4]))
    if name == "ArmorPhase":
        return _phase(arr_u8, _col(color, [0.4, 0.2, 1.5]), sat)
    if name == "ArmorNebula":
        return _nebula(arr_u8, _col(color, [1.0, 0.0, 1.0]), secondary, sat)
    if name == "ArmorVortex":
        return _vortex(arr_u8, _col(color, [0.1, 0.5, 0.35]))
    if name == "ArmorStardust":
        return _stardust(arr_u8, _col(color, [0.4, 0.6, 1.0]))
    if name == "ArmorShiftingSands":
        return _shifting_sands(arr_u8, _col(color, [1.1, 1.0, 0.5]))
    if name == "ArmorShiftingPearlsands":
        return _shifting_pearlsands(arr_u8, _col(color, [1.1, 0.8, 0.9]))
    if name == "ArmorFog":
        return _fog(arr_u8, _col(color, [0.95, 0.95, 0.95]))
    if name == "ArmorHallowBoss":
        return _hallow_boss(arr_u8)

    # unknown / unlisted pass -> undyed
    return arr_u8
