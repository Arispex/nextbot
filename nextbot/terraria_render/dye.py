"""Armor dye shaders (numpy), recovered from Terraria's compiled ps_2_0 bytecode.

Faithful ports of every dye pass used by real dye items (see research/
dye_passes_spec.md + dye_shader_spec.md), in three tiers:

* exact-static  — bit-for-bit recolors (ArmorColored family, Invert, ColorOnly,
  gradient family, Brightness*, Martian, Polarized, Mushroom, Wisp, rainbow…).
* time-animated — sincos/triangle-wave + self-sampling passes that run the *real*
  compiled bytecode (``dye_noise``) at a representative still: ``uTime = 0`` for the
  passes that stay fully lit there (Flow, Living*, Mirage, Hades, Loki), and a swept
  representative for the ones whose ``uTime = 0`` phase collapses (Acid → 2.5, Void →
  1.0; see ``_BATCH2_TIME``). The handwritten ports stay as the offline fallback. Solar
  runs the real bytecode (its hardcoded fire light is offline-faithful) at the uTime=5.0
  flame phase, un-premultiplied by the source alpha then faithfully per-channel HARD-CLIPPED
  like the game GPU (the Vortex/Stardust treatment) so the over-unity additive bloom keeps its
  molten orange/red fire hue instead of desaturating to pink-white (``_solar``).
* self-sampling / noise-sampling — Gel/Phase/Nebula/Vortex/Stardust/Shifting*/Fog/
  HallowBoss, the ArmorTwilight hair dye, and ArmorMidnightRainbow (a 5-tap
  self-emboss, no noise texture) run the *real* compiled bytecode (``dye_noise``)
  against the shipped ``noise.png`` / ``Extra_156.png`` (and the source frame itself)
  at ``uTime = 0`` — accurate, spatially-varying. They fall back to a documented flat
  approximation only if the baked blob / noise asset is missing (offline-safe).
* view approx — the two Reflective passes sample live lighting (`uLightSource`, a surface
  normal), which is 0 offline (no entity); we bind a static representative front light
  (0,0,1) so the metallic highlight lights up — a grounded stand-in, not the game's
  moving specular (which is physically unavailable offline).

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


def _high_contrast_glow_approx(
    arr_u8: np.ndarray, uColor: ColorLike = (0.0, 1.0, 0.0), uSat: float = 1.0,
) -> np.ndarray:
    """ArmorHighContrastGlow OFFLINE FALLBACK: ArmorColored recolor with the v0-driven glow
    term DROPPED. Used only when the baked blob / noise.png is absent; the faithful path is
    `_high_contrast_glow` (the real bytecode, which restores the v0 glow + chroma gating)."""
    return _armor_colored(arr_u8, _col(uColor, [0.0, 1.0, 0.0]), uSat)


def _high_contrast_glow(
    arr_u8: np.ndarray, uColor: ColorLike = (0.0, 1.0, 0.0), uSat: float = 1.0,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
) -> np.ndarray:
    """ArmorHighContrastGlow (item 2883): the real high-contrast glow bytecode.

    A CORRECTION pass -- the handwritten `_high_contrast_glow_approx` dropped the v0-driven
    glow term (`mul r1.w, v0.x, c5.w` / `mad r1.w, r0.y, ...`); the real shader keeps it, and
    GATES the green glow on per-pixel CHROMA (`r0.y = (M-m)*uSat`): a zero-chroma / grey pixel
    drives the glow weight negative -> crushes to black, while a chromatic pixel glows toward
    uColor. v0 (vertex colour) = white = the inventory white draw colour, so the v0 glow term
    is active (research/dye_bytecode_audit.md §HighContrastGlow). This visibly differs from the
    old approx (which recoloured grey toward green); the faithful result is darker on
    low-chroma armor. Falls back to `_high_contrast_glow_approx` if the blob / noise.png is
    absent."""
    uC = _col(uColor, [0.0, 1.0, 0.0])
    return _noise_pass(arr_u8, "ArmorHighContrastGlow", uColor=uC, uSecondary=uC, uSat=uSat,
                       src_rect=src_rect, sheet_size=sheet_size,
                       fallback=lambda: _high_contrast_glow_approx(arr_u8, uColor, uSat))


# ── time-animated / self-sampling passes (faithful bytecode via dye_noise) ────
# Representative GlobalTimeWrappedHourly for the batch-2 animated passes (research/
# dye_bytecode_audit.md §"third tier"). Most stay fully lit at uTime=0 (Flow/Living*/
# Mirage/Hades/Loki — all 100% of opaque px lit on the high-shading head-276 surface) so
# they pin 0, matching the existing time-pass convention. The TWO exceptions collapse at
# uTime=0 and are swept (the same method as the emissive pillars `_PILLAR_TIME`):
#   ArmorAcid: its swirl band leaves 62% of the sprite in the dark trough at uTime=0
#     (only 38% lit); the band sweeps to cover the whole sprite, plateauing at 100% lit
#     for uTime>=2.5 -> pin 2.5 (the first fully-lit phase; stable plateau to ~6.0).
#   ArmorVoid: a horizontal blur+darken whose uTime=0 scroll phase lights only 73% of px;
#     the lit plateau (~95%) begins at uTime=1.0 -> pin 1.0 (a representative lit frame of
#     this intentionally-dark shimmer dye; the mean barely moves, ~0.37-0.41, so this is a
#     phase pick, not a brightness boost).
# Anything not listed pins UTIME (0.0). uRotation is 0 (non-rotated sprite) for Hades/Loki.
_BATCH2_TIME: dict[str, float] = {"ArmorAcid": 2.5, "ArmorVoid": 1.0}


def _batch2_time(name: str, u_time: float | None) -> float:
    """Resolve the frozen uTime for a batch-2 animated pass: the swept representative for
    the passes whose uTime=0 collapses (Acid/Void), else UTIME (0); an explicit override
    (the dynamic-frame sweep) always wins. Production passes None -> the baked still."""
    if u_time is not None:
        return u_time
    return _BATCH2_TIME.get(name, UTIME)


def _flow_approx(
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


def _flow(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorFlow (item 3025): the real luma-phase sincos band mixing uColor<->uSecondary.

    uTime=0 is fully representative (100% of opaque px lit) and the faithful bytecode is
    BIT-IDENTICAL to the handwritten `_flow_approx` there (the handwritten port already
    transcribed this pass exactly; verified max-abs-diff 0) -- routing through the bytecode
    keeps it consistent with the family and lets uTime actually animate it when swept.
    Falls back to `_flow_approx` when the baked blob / noise.png is absent."""
    uC = _col(uColor, [1.0, 0.5, 1.0])
    uS = _col(uSecondary, [0.6, 0.1, 1.0])
    return _noise_pass(arr_u8, "ArmorFlow", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorFlow", u_time),
                       fallback=lambda: _flow_approx(arr_u8, uColor, uSecondary, UTIME))


def _living_rainbow_approx(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
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


def _living_rainbow(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET, u_time: float | None = None,
) -> np.ndarray:
    """ArmorLivingRainbow (item 2870): the real positional+luma+uTime rainbow band.

    No colour uniforms (rainbow comes from `def` consts); uTime=0 is fully lit and
    representative. Falls back to `_living_rainbow_approx` if the blob/noise.png is absent."""
    return _noise_pass(arr_u8, "ArmorLivingRainbow", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorLivingRainbow", u_time),
                       fallback=lambda: _living_rainbow_approx(arr_u8, UTIME))


def _living_flame_approx(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike, uTime: float = UTIME,
) -> np.ndarray:
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


def _living_flame(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorLivingFlame (item 2869): the real positional+luma flame band mixing
    uColor(1,0.9,0)<->uSecondary(1,0.2,0). uTime=0 fully lit. Falls back to the approx."""
    uC = _col(uColor, [1.0, 0.9, 0.0])
    uS = _col(uSecondary, [1.0, 0.2, 0.0])
    return _noise_pass(arr_u8, "ArmorLivingFlame", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorLivingFlame", u_time),
                       fallback=lambda: _living_flame_approx(arr_u8, uColor, uSecondary, UTIME))


def _living_ocean_approx(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
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


def _living_ocean(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET, u_time: float | None = None,
) -> np.ndarray:
    """ArmorLivingOcean (item 2873): the real blue/cyan palette band (colour hardcoded in
    `def` consts, uColor unused). uTime=0 fully lit. Falls back to the approx."""
    return _noise_pass(arr_u8, "ArmorLivingOcean", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorLivingOcean", u_time),
                       fallback=lambda: _living_ocean_approx(arr_u8, UTIME))


def _acid_approx(arr_u8: np.ndarray, uColor: ColorLike, uTime: float = UTIME) -> np.ndarray:
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


def _acid(
    arr_u8: np.ndarray, uColor: ColorLike,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorAcid (items 3028/3040/3560): the real swirling polar-coordinate band toward
    uColor (the swirl reads frame-local position from uSourceRect + luma + uTime).

    uTime=0 COLLAPSES (the swirl trough covers 62% of the sprite -> only 38% lit), so the
    representative still is the swept `_BATCH2_TIME['ArmorAcid']=2.5` -- the first uTime
    where the band fully lights the sprite (100% of opaque px, stable plateau to ~6.0),
    chosen by sweeping like the emissive pillars. Falls back to `_acid_approx` (its own
    uTime=0 polar approximation) when the blob / noise.png is absent."""
    uC = _col(uColor, [0.5, 1.0, 0.3])
    return _noise_pass(arr_u8, "ArmorAcid", uColor=uC, uSecondary=uC, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorAcid", u_time),
                       fallback=lambda: _acid_approx(arr_u8, uColor, UTIME))


def _midnight_rainbow(arr_u8: np.ndarray, uTime: float = UTIME) -> np.ndarray:
    """MidnightRainbow OFFLINE FALLBACK: rainbow recolor over the source (self-emboss
    dropped). Used only when the baked blob / noise.png is absent; the faithful path is
    `_midnight_rainbow_real` (the real 5-tap self-emboss bytecode via dye_noise)."""
    return _colored_rainbow(arr_u8)


def _solar_approx(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 0.0, 0.0),
    uSecondary: ColorLike = (1.0, 1.0, 0.0), uTime: float = UTIME,
) -> np.ndarray:
    """ArmorSolar OFFLINE FALLBACK: handwritten emissive lava/fire heat ramp from source luma.

    Maps source brightness to a Solar-pillar heat ramp uColor(ember red) -> uSecondary(yellow)
    -> white-hot, scaled emissively (dark embers stay dim, hot cores bloom to near-white). A
    uniform golden heat-ramp with no fire STRUCTURE. Used ONLY when the baked blob / noise.png is
    absent; the production path is the faithful bytecode `_solar` (emissive tone-mapped) -- which
    reads as a structured orange/red lava with white-hot highlights, closer to the in-game Solar
    Flare than this flat ramp. (The earlier note that the bytecode was 'dim offline, mean ~74'
    pre-dated the preshader-literal fix -- with it fixed the bytecode is hot lava: see `_solar`.)"""
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


def _solar(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 0.0, 0.0),
    uSecondary: ColorLike = (1.0, 1.0, 0.0),
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorSolar (item 3526): the real 5-tap self-emboss + fire-band bytecode, faithfully
    HARD-CLIPPED -- the PRODUCTION default (see apply_dye).

    The real shader carries its fiery glow as an ADDITIVE emissive term on the vertex colour v0
    (`max r1.xyz, v0, v0.w` / `mad oC0, r4*v0, body`) which BLOOMS in-game. At the uTime=5.0
    flame phase the bytecode's straight rgb hits max 1.94 with 38.6% of pixels over-unity (a
    structured orange/red lava with yellow hot-spots). `_noise_pass(src_alpha=True)` un-
    premultiplies by the SOURCE alpha (the `mad` inflates oC0's alpha to 2.0 for Solar; dividing
    by it would crush the bloom to a dim ember) so the over-unity glow survives, then does the
    SAME per-channel GPU HARD-CLIP the game does (the Vortex/Stardust plan-A treatment): the over-
    unity additive bloom clips PER CHANNEL, which keeps the fire HUE -- R saturates to 255 while
    G/B stay lower -> molten orange/red, and only the all-channels-high cores clip to yellow/white-
    hot. This faithfully reads as hot orange/red lava (luma ~125, R>G>B), unlike the earlier
    emissive tone-map (gain 1.5) which folded every channel's overflow into all channels and so
    DESATURATED the fire toward pink-white (washing the orange/red out, ~50% near-white). Solar's
    light direction is HARDCODED in the shader (`def c5=(-0.05,-0.56,0.5)`), NOT uLightSource, so
    there is NO offline-lost-light problem. uTime drives only a brightness pulse
    (`c2 = sin(uTime*0.477+0.5)*0.2+1`); the still uses the swept `_PILLAR_TIME['ArmorSolar']`=5.0
    (the flame phase). gain=1.0 = the faithful GPU clip (no extra lift -- the over-unity bloom is
    already bright; matches diag `_diag_solar_hardclip` col 3). ADJUSTABLE: bump gain (e.g. 1.2,
    still src_alpha=True so it stays a hard clip, NOT a tone-map) if the lava reads too dark vs a
    game screenshot. Falls back to the handwritten `_solar_approx` when the blob / noise.png is
    absent (offline-safe)."""
    uC = _col(uColor, [1.0, 0.0, 0.0])
    uS = _col(uSecondary, [1.0, 1.0, 0.0])
    return _noise_pass(arr_u8, "ArmorSolar", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorSolar", u_time), src_alpha=True,
                       fallback=lambda: _solar_approx(arr_u8, uColor, uSecondary, UTIME))


def _void(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET, u_time: float | None = None,
) -> np.ndarray:
    """ArmorVoid (item 3530): the real 3-tap horizontal self-blur + darken (a dark shimmer).

    Self-samples uImage0 at +-1/uImageSize0 horizontal taps (the offset-tap fix honours
    them), blurs and darkens (*0.35), scrolling by uTime. uTime=0 lights only 73% of px;
    the lit plateau (~95%) begins at `_BATCH2_TIME['ArmorVoid']=1.0`, so the still pins 1.0
    (a representative lit phase of this intentionally-dark dye -- the mean barely shifts).
    Falls back to the old flat dark-tint approximation if the blob / noise.png is absent.
    NOTE: the faithful result is a textured dark blur, NOT the old flat 0.35 wash."""
    return _noise_pass(arr_u8, "ArmorVoid", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorVoid", u_time),
                       fallback=lambda: _brightness_clip(arr_u8, (0.35, 0.35, 0.35)))


def _hades(
    arr_u8: np.ndarray, uColor: ColorLike, uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorHades (items 3038/3597/3598/3600): the real rotated self-sampling ember glow
    mixing uColor<->uSecondary (3-tap, uRotation-rotated offsets + uTime scroll).

    uRotation=0 (non-rotated sprite, ArmorShaderData.cs:97/105) -> run_noise_pass binds 0.
    uTime=0 is fully lit (100% of opaque px) and representative. Falls back to the old flat
    uColor ember tint if the blob / noise.png is absent. NOTE: the faithful result is the
    rotated self-tap glow, NOT the old flat uColor wash (markedly brighter / textured)."""
    uC = _col(uColor, [0.5, 0.7, 1.3])
    uS = _col(uSecondary, [0.5, 0.7, 1.3])
    return _noise_pass(arr_u8, "ArmorHades", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorHades", u_time),
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _mirage(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET, u_time: float | None = None,
) -> np.ndarray:
    """ArmorMirage (item 3534): the real sin/sgn horizontal self-displacement (a wavy
    shimmer, 3 self-taps + positional + uTime).

    uTime=0 is fully lit (100% of opaque px) and representative. Falls back to a plain
    passthrough if the blob / noise.png is absent. NOTE: the faithful result is the real
    wavy displacement, NOT the old no-op passthrough."""
    return _noise_pass(arr_u8, "ArmorMirage", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorMirage", u_time),
                       fallback=lambda: arr_u8)


def _loki(
    arr_u8: np.ndarray, uColor: ColorLike = (0.1, 0.1, 0.1),
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorLoki (item 3599): the real rotated self-sampling dark camo (same mechanism as
    Hades, uColor=(0.1,0.1,0.1), 3-tap rotated + uTime scroll).

    uRotation=0 -> bound 0. uTime=0 is fully lit (100% of opaque px) and representative.
    Falls back to the old flat dark uColor tint if the blob / noise.png is absent. NOTE: the
    faithful result is the rotated dark-camo self-tap, NOT the old near-black flat wash
    (the old approx collapsed it to luma*0.1 ~ mean 12; the faithful is ~mean 118)."""
    uC = _col(uColor, [0.1, 0.1, 0.1])
    return _noise_pass(arr_u8, "ArmorLoki", uColor=uC, uSecondary=uC, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_batch2_time("ArmorLoki", u_time),
                       fallback=lambda: _brightness_clip(arr_u8, uC))


# ── emissive HDR tone-map (for the pillar/boss glow passes) ───────────
# Representative GlobalTimeWrappedHourly per emissive pillar/boss pass — chosen by
# sweeping uTime over a cycle and picking the BRIGHTEST, most characteristic still
# (research/dye_passes_spec.md "Time-animated"; sweep contact sheets in
# temp/xnb_probe/out/sweep_<name>.png). These are animated emissive effects in-game;
# uTime=0 lands on a dim phase for several, so each bakes its own bright frame.
_PILLAR_TIME: dict[str, float] = {
    "ArmorSolar": 5.0,       # brightness pulse c2=sin(uTime*0.477+0.5)*0.2+1 peaks ~1.13
    "ArmorNebula": 3.0,      # cloud uv-scroll phase with the most pink coverage
    "ArmorVortex": 0.5,      # swirl phase (uTime only rotates the noise uv -> which
                             # streaks appear; it does NOT change brightness, see
                             # research/vortex_dye_bug.md §2)
    "ArmorStardust": 1.0,    # starfield phase: uTime only scrolls the noise uv (which
                             # sparkles appear), NOT brightness -- the sparkles are
                             # hard-clipped like the game GPU (no emissive gain)
    "ArmorHallowBoss": 0.0,  # palette uv barely time-shifts; 0 already bright pastel
}
# Per-pass emissive gain applied before the tone-map: a mild lift so the glow reads as
# bright (the in-game additive bloom) instead of a dark tint on the dark armor base.
# Tuned by eye (temp/xnb_probe/out/tonemap_<pass>.png): high enough to glow, low enough
# to keep the hue (>~2.0 washes everything to white). HallowBoss is already bright and
# in-gamut -> 1.0 (no gain, stays accurate).
# ArmorVortex, ArmorStardust AND ArmorSolar are intentionally ABSENT: their bright streaks/
# sparkles/fire already exist in the faithful bytecode (over-unity on bright source pixels) and
# the game just hard-clips them. An extra gain + overflow tone-map double-exposed them (~1.5x
# more near-white than the game for Vortex; ~+0.14 mean / 2-13x near-white for Stardust; and for
# SOLAR the tone-map folded each channel's overflow into ALL channels, DESATURATING the orange/
# red fire toward pink-white ~50% near-white), so all three go through the plain np.clip per-
# channel hard-clip = GPU behaviour, which keeps the fire HUE (Solar: R>G>B molten orange/red).
# Solar un-premults by SOURCE alpha first (its `mad` inflates oC0-alpha to 2.0) via
# `_noise_pass(src_alpha=True, gain=1.0)` -- no _PILLAR_GAIN entry needed (research/
# vortex_dye_bug.md plan A; solar_reflective_revisit.md).
_PILLAR_GAIN: dict[str, float] = {
    "ArmorNebula": 1.4,
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
    src_alpha: bool = False,
) -> np.ndarray:
    """Run baked shader `name` per-pixel with real noise sampling (dye_noise).

    Premultiplies straight input, runs the actual ps_2_0 bytecode (premult-in/out),
    un-premultiplies — same wrapper as `_run`. Falls back to the documented APPROX
    when the baked blob / noise.png is absent (renderer never crashes offline).

    `u_time` freezes the still (emissive pillar passes bake a per-pass bright frame via
    `_PILLAR_TIME`). Three exit paths control how the (possibly over-unity) output lands:

    * `emissive=True` un-premultiplies by SOURCE alpha then TONE-MAPS the glow with `gain`
      (overflow desaturates toward white -> a bright bloom). Nebula/HallowBoss use it.
    * `src_alpha=True` un-premultiplies by SOURCE alpha then HARD-CLIPS (`gain` applied
      first). For Solar: the additive `mad oC0, glow*v0, body` inflates oC0's ALPHA to 2.0,
      so dividing by oC0-alpha would crush the bloom into gamut (a dim ember); dividing by
      the source alpha keeps the over-unity orange/red, and a per-channel `np.clip` then
      keeps the fire HUE (R saturates to 255, G/B preserved -> molten orange/red; only the
      all-channels-high cores clip to white-hot) instead of the tone-map's hue-flattening
      desaturation-to-pink-white (research/solar_reflective_revisit.md). This is the SAME
      GPU hard-clip the game does -- identical to the Vortex/Stardust plan-A fix, just with
      the source-alpha un-premult that Solar's inflated oC0-alpha requires.
    * default (both False) un-premultiplies by oC0 alpha then HARD-CLIPS -- the faithful GPU
      clip for passes whose `mad` does NOT touch alpha (Vortex/Stardust/most noise passes).
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
    res = arr.copy()
    if emissive or src_alpha:
        # Un-premultiply by SOURCE alpha so an additive bloom (`mad oC0, glow*v0, body`,
        # which for Solar inflates oC0's ALPHA to 2.0) survives as straight over-unity rgb
        # instead of being crushed back into gamut by the inflated oC0-alpha. (For the
        # non-Solar emissive passes Nebula/HallowBoss oC0.a == src.a, so this is byte-neutral.)
        sa = arr[..., 3]
        nz = sa > 1e-6
        rgb = np.where(nz[..., None], out[..., :3] / np.where(nz, sa, 1.0)[..., None], 0.0)
        # emissive -> tone-map (overflow->white bloom); src_alpha -> faithful per-channel
        # GPU hard-clip (gain first), which keeps the fire hue (R>G>B) for Solar lava.
        res[..., :3] = _emissive_tonemap(rgb, gain) if emissive else np.clip(rgb * gain, 0.0, 1.0)
    else:
        oa = out[..., 3]
        nz = oa > 1e-6
        rgb = np.where(nz[..., None], out[..., :3] / np.where(nz, oa, 1.0)[..., None], 0.0)
        res[..., :3] = np.clip(rgb, 0.0, 1.0)
    res = np.clip(res, 0.0, 1.0)
    return (res * 255.0 + 0.5).astype(np.uint8)


# ── view-dependent passes (uLightSource bound to a static front light offline) ──
# CLASS C (grounded approximation): the two Reflective passes are lit by `uLightSource`, a unit
# surface NORMAL the game derives from the entity's live lighting gradient
# (ReflectiveArmorShaderData.Apply). With no entity offline the game forces it to Vector3.Zero ->
# `dp3 r0.x, r0, uLightSource` is identically 0 -> the metallic highlight collapses to a dull
# *0.5 metal. run_noise_pass instead binds a STATIC representative front light (0,0,1) = the
# shader's +Z surface normal / a head-on viewer, so the specular highlight statically lights up
# (luma 58->176 on Armor_Head_276) -- the bright-reflective look. This is a grounded stand-in for
# the offline-unavailable live gradient, NOT faithful: the game's highlight MOVES with the
# lighting; a fixed normal is a representative still (research/solar_reflective_revisit.md §2).
def _reflective_approx(arr_u8: np.ndarray) -> np.ndarray:
    """ArmorReflective OFFLINE FALLBACK: no baked blob -> passthrough (the source unchanged).
    Used only when the baked blob / noise.png is absent (faithful path = `_reflective`)."""
    return arr_u8


def _reflective_color_approx(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """ArmorReflectiveColor OFFLINE FALLBACK: no baked blob -> tint source by uColor only.
    Used only when the baked blob / noise.png is absent (faithful path = `_reflective_color`)."""
    return _brightness_clip(arr_u8, _col(uColor, [1.0, 1.0, 1.0]))


def _reflective(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET,
) -> np.ndarray:
    """ArmorReflective (item 3190): the real 5-tap emboss + specular bytecode, lit by a STATIC
    front light offline.

    CLASS C grounded approximation: run_noise_pass binds uLightSource=(0,0,1) (the shader's +Z
    surface normal / a head-on viewer) so the specular highlight lobe (`dp3 N.L` + `cmp`) lights
    up statically -- a bright reflective chrome (luma ~176 vs the dull ~58 at uLightSource=0),
    instead of collapsing. This is a representative stand-in: the game's highlight moves with the
    live lighting gradient, which is physically unavailable offline (no entity). Falls back to
    `_reflective_approx` (passthrough) when the blob / noise.png is absent."""
    return _noise_pass(arr_u8, "ArmorReflective", uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       fallback=lambda: _reflective_approx(arr_u8))


def _reflective_color(
    arr_u8: np.ndarray, uColor: ColorLike = (1.0, 1.0, 1.0),
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
) -> np.ndarray:
    """ArmorReflectiveColor (items 3026/3027/3553/3554/3555): the real emboss + specular +
    uColor metallic-tint bytecode, lit by a STATIC front light offline.

    CLASS C grounded approximation: same fixed uLightSource=(0,0,1) as `_reflective` so the
    metallic highlight lights up (silver/gold/copper/obsidian/metal read as bright reflective
    instead of dull), tinted by the per-item uColor (DyeInitializer.cs:86-91). A representative
    stand-in for the offline-unavailable moving specular. Falls back to `_reflective_color_approx`
    (uColor tint of the source) when the blob / noise.png is absent."""
    uC = _col(uColor, [1.0, 1.0, 1.0])
    return _noise_pass(arr_u8, "ArmorReflectiveColor", uColor=uC, uSecondary=uC, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       fallback=lambda: _reflective_color_approx(arr_u8, uColor))


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
    """ArmorVortex: real swirling noise, hard-clipped exactly like the game GPU.

    Teal/green energy glow: the bytecode swirls polar-coord noise into bright
    `uSecondary` streaks over a `uColor*luma` base. The streaks are already over-unity
    in the faithful bytecode (sparkle = noise*luma*5*uSecondary on bright source pixels);
    the game simply hard-clips them to white. So this runs the faithful bytecode and
    clips to [0,1] (`emissive=False`) -- NO extra gain / overflow tone-map, which would
    double-expose it (~1.5x more near-white than the game; research/vortex_dye_bug.md).
    `_PILLAR_TIME` still picks the representative swirl phase, but uTime only rotates the
    noise uv (which streaks appear), not the brightness."""
    uC = _col(uColor, [0.1, 0.5, 0.35])
    uS = _col(uSecondary, [1.0, 1.0, 1.0])
    return _noise_pass(arr_u8, "ArmorVortex", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorVortex", u_time), emissive=False,
                       fallback=lambda: _brightness_clip(arr_u8, uC))


def _stardust(
    arr_u8: np.ndarray, uColor: ColorLike = (0.4, 0.6, 1.0), uSecondary: ColorLike = None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """ArmorStardust: real swirling-noise starfield, hard-clipped exactly like the game GPU.

    Deep blue base (uColor*luma*0.666) with bright white star sparkles
    (`noise-threshold * uSecondary * 8`). The sparkles are already over-unity in the
    faithful bytecode on bright source pixels; the game GPU simply hard-clips them to
    white. So this runs the faithful bytecode and clips to [0,1] (`emissive=False`) --
    NO extra gain / overflow tone-map, which double-exposed it (~+0.14 mean brightness,
    2-13x more near-white than the game, desaturating the blue toward white;
    research/vortex_dye_bug.md, same mechanism as ArmorVortex). `_PILLAR_TIME` still
    picks the representative starfield phase (the now-correct uTime preshader input),
    but it only scrolls the noise uv (which sparkles appear), not the brightness.
    uColor/uSecondary/sat are faithful to DyeInitializer.cs (3529: 0.4,0.6,1 / 1,1,1 / 1)."""
    uC = _col(uColor, [0.4, 0.6, 1.0])
    uS = _col(uSecondary, [1.0, 1.0, 1.0])

    def approx() -> np.ndarray:
        return _run(arr_u8, lambda r, g, b, a: uC * ((r + g + b) * 0.667)[..., None] * a[..., None])

    return _noise_pass(arr_u8, "ArmorStardust", uColor=uC, uSecondary=uS, uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=_pillar_time("ArmorStardust", u_time), emissive=False,
                       fallback=approx)


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


def _midnight_rainbow_real(
    arr_u8: np.ndarray, *, src_rect: SrcRect = _DEFAULT_RECT,
    sheet_size: SheetSize = _DEFAULT_SHEET, u_time: float | None = None,
) -> np.ndarray:
    """ArmorMidnightRainbow (item 3556): the faithful 5-tap self-emboss bytecode.

    Runs the real compiled shader (dye_noise) — a cross/Laplacian emboss of the source
    (center ± 2 texels) whose magnitude gates a phase-shifted rainbow (hue = luma*0.0667 +
    smoothstep(frameX)*0.4 + uTime*0.4), so the rainbow traces the sprite's contours over a
    dark interior (research/midnight_rainbow.md). Needs the source offset-tap fix in
    dye_noise._run_ps to render at all (without it the emboss is 0 -> a black sprite).

    `uTime` only ADDS a `*0.4` horizontal scroll to the hue (it does not change the emboss
    brightness), so the representative still uses UTIME=0 like the other scroll-only passes —
    NOT a `_PILLAR_TIME` bright frame (it is not an emissive pillar). The shader reads no
    color uniforms (the CTAB has only uImage0/uImageSize0/uSourceRect), so the [1,1,1]
    defaults are inert. `emissive=False` -> plain hard clip (the GPU clips; no extra bloom).
    Falls back to the `_midnight_rainbow` APPROX when the baked blob / noise.png is absent.
    """
    return _noise_pass(arr_u8, "ArmorMidnightRainbow",
                       uColor=np.array([1.0, 1.0, 1.0]),
                       uSecondary=np.array([1.0, 1.0, 1.0]), uSat=1.0,
                       src_rect=src_rect, sheet_size=sheet_size,
                       u_time=UTIME if u_time is None else u_time, emissive=False,
                       fallback=lambda: _midnight_rainbow(arr_u8))


# ── dispatch ─────────────────────────────────────────────────────────
def apply_dye(
    arr_u8: np.ndarray, spec: dict[str, Any] | None,
    *, src_rect: SrcRect = _DEFAULT_RECT, sheet_size: SheetSize = _DEFAULT_SHEET,
    u_time: float | None = None,
) -> np.ndarray:
    """Apply a dye to a straight-alpha (h,w,4) uint8 array. `spec` from dyes.json.

    `spec` carries `pass` and (where applicable) `color`/`secondary`/`sat`, baked
    from DyeInitializer.cs. Animated passes are evaluated at a representative still
    (uTime=0). Noise/self-sampling passes (Gel/Phase/Nebula/Vortex/Stardust/Shifting*/Fog/
    HallowBoss/Twilight/MidnightRainbow) run the real bytecode; the noise ones sample the
    real Misc/noise (or Extra_156) texture and MidnightRainbow self-samples the source
    (5-tap emboss); `src_rect` (the cell's x,y,w,h in its sheet) + `sheet_size` (W,H) place
    the sampling uv (the compositor threads them; non-noise passes ignore them). The two
    Reflective passes run the real bytecode with a static front light (uLightSource=(0,0,1),
    a grounded stand-in for the offline-unavailable live specular). Unknown passes fall back
    to undyed (no crash).

    `u_time` overrides the frozen GlobalTimeWrappedHourly of the time-animated and
    noise/self-sampling passes (a phase for sweeping a dye's animation cycle; see the
    dynamic-frame dev script). `None` (production default) keeps each pass's baked
    representative still — UTIME=0 for the time/scroll passes (incl. MidnightRainbow, whose
    uTime only scrolls the rainbow hue, and the batch-2 Flow/Living*/Mirage/Hades/Loki,
    all fully lit at 0), the swept `_BATCH2_TIME[name]` for the two batch-2 passes whose
    uTime=0 collapses (Acid=2.5 / Void=1.0), and `_PILLAR_TIME[name]` for the emissive
    pillar passes (incl. `ArmorSolar`=5.0, the flame phase) — so the production byte-output
    is unchanged.
    """
    if not spec:
        return arr_u8
    name = spec.get("pass")
    color = spec.get("color")
    secondary = spec.get("secondary")
    sat = float(spec.get("sat", 1.0))
    geom = {"src_rect": src_rect, "sheet_size": sheet_size}
    ngeom = {**geom, "u_time": u_time}  # noise passes additionally take a phase override

    # exact-static (non-animated recolor / gradient / colour-driven): run the REAL compiled
    # ps_2_0 bytecode (dye_noise) — uTime is irrelevant, so the frozen still is exact, not an
    # approximation. The handwritten ports stay as the offline fallback (baked blob / noise.png
    # absent), same contract as the noise dyes. v0 (vertex colour) = white = the inventory /
    # standard white draw colour (research/dye_bytecode_audit.md §v0). The compositor threads
    # src_rect/sheet_size so the gradient/rainbow family normalises pixel-x by 1/uSourceRect.z.
    cC = _col(color, [1.0, 1.0, 1.0])
    cS = _col(secondary, [1.0, 1.0, 0.0])
    if name == "ArmorColored":
        return _noise_pass(arr_u8, "ArmorColored", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _armor_colored(arr_u8, cC, sat), **geom)
    if name == "ArmorColoredAndBlack":
        return _noise_pass(arr_u8, "ArmorColoredAndBlack", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _armor_colored_andblack(arr_u8, cC, sat), **geom)
    if name == "ArmorColoredAndSilverTrim":
        return _noise_pass(
            arr_u8, "ArmorColoredAndSilverTrim", uColor=cC, uSecondary=cC, uSat=sat,
            fallback=lambda: _armor_colored_silvertrim(arr_u8, cC, sat), **geom)
    if name == "ArmorBrightnessColored":
        return _noise_pass(arr_u8, "ArmorBrightnessColored", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _brightness_colored(arr_u8, cC), **geom)
    if name == "ColorOnly":
        return _noise_pass(arr_u8, "ColorOnly", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _color_only(arr_u8), **geom)
    if name == "ArmorInvert":
        return _noise_pass(arr_u8, "ArmorInvert", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _invert(arr_u8), **geom)
    if name == "ArmorColoredGradient":
        return _noise_pass(arr_u8, "ArmorColoredGradient", uColor=cC, uSecondary=cS, uSat=sat,
                           fallback=lambda: _colored_gradient(arr_u8, color, secondary, sat),
                           **geom)
    if name == "ArmorColoredAndBlackGradient":
        return _noise_pass(
            arr_u8, "ArmorColoredAndBlackGradient", uColor=cC, uSecondary=cS, uSat=sat or 1.5,
            fallback=lambda: _colored_andblack_gradient(arr_u8, color, secondary, sat or 1.5),
            **geom)
    if name == "ArmorColoredAndSilverTrimGradient":
        return _noise_pass(
            arr_u8, "ArmorColoredAndSilverTrimGradient", uColor=cC, uSecondary=cS,
            uSat=sat or 1.5,
            fallback=lambda: _colored_silvertrim_gradient(arr_u8, color, secondary, sat or 1.5),
            **geom)
    if name == "ArmorBrightnessGradient":
        return _noise_pass(arr_u8, "ArmorBrightnessGradient", uColor=cC, uSecondary=cS, uSat=sat,
                           fallback=lambda: _brightness_gradient(arr_u8, color, secondary),
                           **geom)
    if name == "ArmorColoredRainbow":
        return _noise_pass(arr_u8, "ArmorColoredRainbow", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _colored_rainbow(arr_u8, sat), **geom)
    if name == "ArmorBrightnessRainbow":
        return _noise_pass(arr_u8, "ArmorBrightnessRainbow", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _brightness_rainbow(arr_u8), **geom)
    if name == "ArmorMartian":
        return _noise_pass(arr_u8, "ArmorMartian", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _martian(arr_u8), **geom)
    if name == "ArmorPolarized":
        return _noise_pass(arr_u8, "ArmorPolarized", uColor=cC, uSecondary=cC, uSat=sat,
                           fallback=lambda: _polarized(arr_u8), **geom)
    if name == "ArmorMushroom":
        cM = _col(color, [0.05, 0.2, 1.0])
        return _noise_pass(arr_u8, "ArmorMushroom", uColor=cM, uSecondary=cM, uSat=sat,
                           fallback=lambda: _mushroom(arr_u8, cM), **geom)
    if name == "ArmorWisp":
        cWC = _col(color, [0.7, 1.0, 0.9])
        cWS = _col(secondary, [0.35, 0.85, 0.8])
        return _noise_pass(arr_u8, "ArmorWisp", uColor=cWC, uSecondary=cWS, uSat=sat,
                           fallback=lambda: _wisp(arr_u8, color, secondary), **geom)
    if name == "ArmorHighContrastGlow":
        # batch 3 correction: the real bytecode restores the v0-driven glow + chroma gating
        # (handwritten dropped both). `**geom` threads src_rect/sheet_size; uTime is irrelevant.
        return _high_contrast_glow(arr_u8, _col(color, [0.0, 1.0, 0.0]), sat, **geom)

    # time-animated / self-sampling (batch 2): run the REAL compiled bytecode (dye_noise),
    # frozen at a representative still -- UTIME=0 for the passes that stay lit there, the
    # swept `_BATCH2_TIME` value for the ones whose uTime=0 collapses (Acid 2.5 / Void 1.0).
    # The handwritten ports stay as the offline fallback (baked blob / noise.png absent).
    # `**ngeom` threads src_rect/sheet_size (the swirl/emboss taps read uSourceRect/
    # uImageSize0) + the u_time override (the dynamic-frame sweep).
    if name == "ArmorFlow":
        return _flow(arr_u8, color, secondary, **ngeom)
    if name == "ArmorLivingRainbow":
        return _living_rainbow(arr_u8, **ngeom)
    if name == "ArmorLivingFlame":
        return _living_flame(arr_u8, color, secondary, **ngeom)
    if name == "ArmorLivingOcean":
        return _living_ocean(arr_u8, **ngeom)
    if name == "ArmorAcid":
        return _acid(arr_u8, _col(color, [0.5, 1.0, 0.3]), **ngeom)
    if name == "ArmorVoid":
        return _void(arr_u8, **ngeom)
    if name == "ArmorHades":
        return _hades(arr_u8, color, secondary, **ngeom)
    if name == "ArmorMirage":
        return _mirage(arr_u8, **ngeom)
    if name == "ArmorLoki":
        return _loki(arr_u8, _col(color, [0.1, 0.1, 0.1]), **ngeom)

    # ArmorSolar: the real bytecode (`_solar`) + faithful per-channel GPU HARD-CLIP IS the
    # production default. The faithful shader is hot lava offline (Solar's light is hardcoded `def
    # c5`, not uLightSource, so nothing is lost offline): straight rgb max 1.94, 38.6% over-unity at
    # the uTime=5.0 flame phase, un-premult by SOURCE alpha (its `mad` inflates oC0-alpha to 2.0)
    # then hard-clipped per channel (the same GPU treatment as Vortex/Stardust), which keeps the
    # fire HUE -- molten orange/red (R>G>B) with yellow/white-hot cores, closer to the in-game Solar
    # Flare than the old handwritten uniform-yellow ramp (now only the offline fallback
    # `_solar_approx`) AND than the earlier emissive tone-map (gain 1.5) which DESATURATED the fire
    # to pink-white. `**ngeom` threads the emboss-tap geometry + a uTime sweep override.
    if name == "ArmorSolar":
        return _solar(arr_u8, _col(color, [1.0, 0.0, 0.0]), _col(secondary, [1.0, 1.0, 0.0]),
                      **ngeom)

    # ArmorReflective / ArmorReflectiveColor (CLASS C, grounded approximation): the real bytecode
    # runs with a STATIC representative front light bound in run_noise_pass (uLightSource=(0,0,1),
    # the shader's +Z surface normal) so the metallic specular highlight statically lights up
    # (luma 58->176, bright reflective metal) instead of collapsing to dull *0.5 metal at the
    # offline uLightSource=0. This is a grounded stand-in, NOT faithful: the game's specular MOVES
    # with the live lighting gradient, which no entity offline can supply (see _reflective + the
    # uLightSource comment in dye_noise.run_noise_pass). `**geom` threads the emboss-tap geometry.
    if name == "ArmorReflective":
        return _reflective(arr_u8, **geom)
    if name == "ArmorReflectiveColor":
        return _reflective_color(arr_u8, _col(color, [1.0, 1.0, 1.0]), **geom)

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
    if name == "ArmorMidnightRainbow":
        return _midnight_rainbow_real(arr_u8, **ngeom)

    # unknown / unlisted pass -> undyed
    return arr_u8
