"""Armor dye shaders (numpy), recovered from Terraria's compiled ps_2_0 bytecode.

The basic color dyes use the **ArmorColored** pass (exact, validated bit-for-bit
against the bytecode interpreter): recolor while preserving brightness, so a
silver pixel + RedDye -> copper, not flat red. Other passes are best-effort;
unknown/animated passes fall back to undyed.

Operates on STRAIGHT-alpha (h, w, 4) uint8 arrays. XNA textures are premultiplied,
so we re-premultiply, run the shader, then convert back to straight alpha.

Single-letter math variables (M/m/S/D) and uColor/uSat mirror the disassembled
shader + dye_shader_spec.md; see this module's ruff per-file-ignores.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence


def _armor_colored_premul(
    src: np.ndarray, color: Sequence[float], uSat: float,
) -> np.ndarray:
    """src: (...,4) float premultiplied RGBA in [0,1]. Returns premultiplied RGBA.
    Faithful port of the disassembled ArmorColored ps_2_0 + its D3DX preshader."""
    uColor = np.asarray(color, dtype=np.float64)
    c0 = 1.0 - uColor                                   # preshader
    c1 = 1.0 if uSat <= 1.0 else 1.0 / uSat
    c2 = 1.0 - c1

    r, g, b, a = src[..., 0], src[..., 1], src[..., 2], src[..., 3]
    M = np.maximum(np.maximum(r, g), b)
    m = np.minimum(np.minimum(r, g), b)
    S = M + m                                           # max+min (~2*lightness)
    D = (M - m) * uSat
    D = D * c1 + c2                                     # saturation remap
    gfac = -0.5 * S + 1.5
    grayrgb = (-gfac[..., None]) * c0 + 1.0
    mask = -0.5 * S + 0.5
    tint = S[..., None] * uColor
    base = np.where((mask >= 0.0)[..., None], tint, grayrgb)
    base = base - 0.5 * S[..., None]
    half = 0.5 * S
    rgb = D[..., None] * base + half[..., None]
    rgb = a[..., None] * rgb                            # re-premultiply
    out = np.empty_like(src)
    out[..., 0:3] = rgb
    out[..., 3] = a
    return out


def _armor_colored(
    arr_u8: np.ndarray, uColor: Sequence[float], uSat: float,
) -> np.ndarray:
    arr = arr_u8.astype(np.float64) / 255.0
    a = arr[..., 3]
    premul = arr.copy()
    premul[..., 0:3] = arr[..., 0:3] * a[..., None]
    outp = _armor_colored_premul(premul, uColor, uSat)
    oa = outp[..., 3]
    straight = outp.copy()
    nz = oa > 1e-6
    straight[..., 0:3] = np.where(
        nz[..., None], outp[..., 0:3] / np.where(nz, oa, 1.0)[..., None], 0.0,
    )
    straight = np.clip(straight, 0.0, 1.0)
    return (straight * 255.0 + 0.5).astype(np.uint8)


def _brightness_colored(arr_u8: np.ndarray, uColor: Sequence[float]) -> np.ndarray:
    """ArmorBrightnessColored: out = (r+g+b)/3 * uColor (the literal recolor)."""
    arr = arr_u8.astype(np.float64) / 255.0
    gray = (arr[..., 0] + arr[..., 1] + arr[..., 2]) / 3.0
    out = arr.copy()
    out[..., 0:3] = np.clip(gray[..., None] * np.asarray(uColor), 0.0, 1.0)
    return (out * 255.0 + 0.5).astype(np.uint8)


def apply_dye(arr_u8: np.ndarray, spec: dict[str, Any] | None) -> np.ndarray:
    """Apply a dye to a straight-alpha (h,w,4) uint8 array. `spec` from dyes.json."""
    if not spec:
        return arr_u8
    pass_name = spec.get("pass")
    color = spec.get("color", [1.0, 1.0, 1.0])
    sat = float(spec.get("sat", 1.0))
    if pass_name == "ArmorColored":
        return _armor_colored(arr_u8, color, sat)
    if pass_name == "ArmorColoredAndBlack":
        # exact-ish: ArmorColored globally darkened by (0.33 + 0.66*(M-m)*sat)
        out = _armor_colored(arr_u8, color, sat)
        f = arr_u8.astype(np.float64) / 255.0
        chroma = np.max(f[..., :3], axis=2) - np.min(f[..., :3], axis=2)
        k = np.clip(0.33 + 0.66 * chroma * sat, 0.0, 1.0)
        out[..., 0:3] = (out[..., 0:3].astype(np.float64) * k[..., None]).astype(
            np.uint8,
        )
        return out
    if pass_name == "ArmorColoredAndSilverTrim":
        return _armor_colored(arr_u8, color, sat)  # approx: silver-trim mix TODO
    if pass_name == "ArmorBrightnessColored":
        return _brightness_colored(arr_u8, color)
    # gradient / rainbow / reflective / living / martian / invert / ...:
    # best-effort undyed
    return arr_u8
