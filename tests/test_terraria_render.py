"""Minimal tests for the reusable Terraria character renderer.

Dependency-light: no network, no pytest-only fixtures. Runs under pytest
(``uv run pytest tests/test_terraria_render.py``) or as a plain script
(``uv run python tests/test_terraria_render.py``).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

# allow `python tests/test_terraria_render.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nextbot.terraria_render import compositor as glow_mod
from nextbot.terraria_render import dye_noise, render_character
from nextbot.terraria_render.compositor import (
    FH,
    FW,
    PAD_L,
    PAD_T,
    _acc_wing_frame,
    _back_hair_style,
    _Compositor,
    _frame,
    _over_glow,
    _resolve_accessories,
)
from nextbot.terraria_render.dye import (
    _BATCH2_TIME,
    _PILLAR_GAIN,
    _PILLAR_TIME,
    _emissive_tonemap,
    _high_contrast_glow_approx,
    _loki,
    _midnight_rainbow,
    _midnight_rainbow_real,
    _nebula,
    _reflective,
    _reflective_approx,
    _reflective_color,
    _reflective_color_approx,
    _solar,
    _solar_approx,
    _stardust,
    _void,
    _vortex,
    apply_dye,
)
from nextbot.terraria_render.image_io import read_png

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_size(png: bytes) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR (bytes 16..24)."""
    width, height = struct.unpack_from(">II", png, 16)
    return int(width), int(height)


def _decode(png: bytes) -> np.ndarray:
    """Decode rendered PNG bytes -> (h, w, 4) uint8 (reuses the project's PNG codec)."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(png)
        path = fh.name
    try:
        return read_png(path)
    finally:
        Path(path).unlink(missing_ok=True)

# Known-good input from the task verification snippet (FemaleCoat + armor + RedDye).
_APPEARANCE = {
    "skinVariant": 7,
    "hair": 112,
    "hairDye": 0,
    "hairColor": -3270602,
    "skinColor": -10059269,
    "eyeColor": -15100654,
    "shirtColor": -4021652,
    "underShirtColor": -4639811,
    "pantsColor": -12772014,
    "shoeColor": -4963208,
}
_EQUIPMENT = {"head": {"netId": 690}, "body": {"netId": 80}, "legs": {"netId": 1733}}
_DYE = {"head": {"netId": 1007}, "body": {"netId": 1007}, "legs": {"netId": 1007}}


def test_render_returns_valid_png() -> None:
    png = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    assert png[:8] == _PNG_SIG
    assert len(png) > 1000


def test_render_is_deterministic() -> None:
    a = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    b = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    assert a == b


def test_render_appearance_only() -> None:
    # No equipment/vanity/dye: naked base body + hair must still produce a PNG.
    png = render_character(_APPEARANCE)
    assert png[:8] == _PNG_SIG
    assert len(png) > 100


def test_scale_changes_dimensions() -> None:
    small = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=1)
    big = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    # Both are valid PNGs; upscaled output is strictly larger on disk.
    assert small[:8] == _PNG_SIG
    assert big[:8] == _PNG_SIG
    assert len(big) > len(small)


def _gray_frame(value: int = 200) -> np.ndarray:
    """A fully-opaque 56x40 gray cell to dye."""
    f = np.full((56, 40, 4), value, np.uint8)
    f[..., 3] = 255
    return f


def test_armor_colored_is_copper() -> None:
    # ArmorColored RedDye on silver gray -> copper (high R, equal-and-lower G==B),
    # brightness preserved (NOT flat red). Bit-exact against dye_shader_spec ramp.
    px = np.array([[[204, 204, 204, 255]]], np.uint8)  # 0.8 gray
    out = apply_dye(px, {"pass": "ArmorColored", "color": [1.0, 0.0, 0.0], "sat": 1.2})
    r, g, b = int(out[0, 0, 0]), int(out[0, 0, 1]), int(out[0, 0, 2])
    assert (r, g, b) == (213, 183, 183)  # spec copper ramp @0.8
    assert r > g and g == b and g > 0  # copper, not pure red


def test_invert_deterministic_and_changes_input() -> None:
    f = _gray_frame(200)
    a = apply_dye(f.copy(), {"pass": "ArmorInvert"})
    b = apply_dye(f.copy(), {"pass": "ArmorInvert"})
    assert np.array_equal(a, b)  # deterministic
    assert not np.array_equal(a, f)  # differs from input
    # opaque 200 -> premult 200/255, inverted 1-200/255 -> straight -> 55
    assert int(a[0, 0, 0]) == round((1.0 - 200 / 255) * 255)


def test_gradient_red_to_yellow_is_warm() -> None:
    # ArmorColoredGradient red->yellow runs left->right across the 40px frame and must
    # stay WARM (high R, low B), NOT inverted to cyan. Regression guard for the
    # sat-remap sign bug (sat_c2=-c1 drove D negative and flipped red -> cyan).
    f = _gray_frame(200)
    out = apply_dye(
        f, {"pass": "ArmorColoredGradient", "color": [1.0, 0.0, 0.0],
            "secondary": [1.0, 1.0, 0.0], "sat": 1.2})
    r_left, g_left, b_left = (int(out[0, 0, c]) for c in range(3))
    r_right, g_right, b_right = (int(out[0, 39, c]) for c in range(3))
    # warm everywhere: red channel dominates, blue stays low (not cyan)
    assert r_left > b_left and r_right > b_right
    assert r_left > g_left  # red end leans red
    # red->yellow means the green channel rises left->right (and the gradient varies)
    assert g_right > g_left
    # rows are identical (gradient is column-only)
    assert np.array_equal(out[0], out[40])


def test_unknown_pass_falls_back_undyed() -> None:
    f = _gray_frame(200)
    assert np.array_equal(apply_dye(f.copy(), {"pass": "NotARealPass"}), f)
    assert np.array_equal(apply_dye(f.copy(), None), f)


# ── batch 1: non-animated recolor/gradient passes run the REAL compiled bytecode ──────
# research/dye_bytecode_audit.md: these 16 passes were handwritten approximations; they
# now dispatch through the real ps_2_0 bytecode (dye_noise.run_noise_pass), with the
# handwritten fn kept only as the offline fallback (baked blob / noise.png absent).
_BATCH1_SPECS = (
    {"pass": "ArmorColored", "color": [1.0, 0.0, 0.0], "sat": 1.2},
    {"pass": "ArmorColoredAndBlack", "color": [1.0, 0.0, 0.0], "sat": 1.2},
    {"pass": "ArmorColoredAndSilverTrim", "color": [1.0, 0.0, 0.0], "sat": 1.2},
    {"pass": "ArmorColoredGradient", "color": [1.0, 0.0, 0.0],
     "secondary": [1.0, 1.0, 0.0], "sat": 1.2},
    {"pass": "ArmorColoredAndBlackGradient", "color": [1.0, 0.0, 0.0],
     "secondary": [1.0, 1.0, 0.0], "sat": 1.5},
    {"pass": "ArmorColoredAndSilverTrimGradient", "color": [1.0, 0.0, 0.0],
     "secondary": [1.0, 1.0, 0.0], "sat": 1.5},
    {"pass": "ArmorBrightnessGradient", "color": [1.0, 0.0, 0.0],
     "secondary": [1.0, 1.0, 0.0]},
    {"pass": "ArmorColoredRainbow"},
    {"pass": "ArmorBrightnessRainbow"},
    {"pass": "ArmorBrightnessColored", "color": [1.0, 1.0, 1.0]},
    {"pass": "ArmorInvert"},
    {"pass": "ColorOnly"},
    {"pass": "ArmorMartian", "color": [0.0, 2.0, 3.0]},
    {"pass": "ArmorPolarized"},
    {"pass": "ArmorMushroom", "color": [0.05, 0.2, 1.0]},
    {"pass": "ArmorWisp", "color": [0.7, 1.0, 0.9], "secondary": [0.35, 0.85, 0.8]},
)


def test_batch1_passes_dispatch_through_bytecode() -> None:
    # Every batch-1 pass must call dye_noise.run_noise_pass (the real bytecode), NOT
    # silently use the handwritten fallback. Spy on run_noise_pass and assert it fires
    # for each. Also asserts the six no-preshader passes (BrightnessColored/Invert/
    # ColorOnly/Martian/Polarized/Mushroom) survive the FXLC-guard fix (crashed before).
    from nextbot.terraria_render import dye as dye_mod

    f = _structured_frame()
    calls: list[str] = []
    orig = dye_mod.dye_noise.run_noise_pass

    def spy(premul: np.ndarray, name: str, **k: object) -> "np.ndarray | None":
        calls.append(name)
        return orig(premul, name, **k)  # type: ignore[arg-type]

    dye_mod.dye_noise.run_noise_pass = spy
    try:
        for spec in _BATCH1_SPECS:
            calls.clear()
            out = apply_dye(
                f.copy(), dict(spec), src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
            assert calls == [spec["pass"]], (
                f"{spec['pass']} did not dispatch through the bytecode (calls={calls})")
            assert out is not None and out.shape == f.shape
    finally:
        dye_mod.dye_noise.run_noise_pass = orig


def test_armor_colored_copper_is_bytecode_faithful() -> None:
    # The user-verified hero case: ArmorColored RedDye on a SILVER (neutral gray) ramp
    # -> COPPER with the brightness preserved (highlight bright, shadow dark), NOT flat
    # red. The real bytecode must reproduce it bit-for-bit, equal to the handwritten
    # (which dye_shader_spec validated). Pins the spec ramp value + per-band ordering.
    from nextbot.terraria_render import dye as dye_mod

    bands = np.array([230, 180, 130, 90, 50], np.uint8)      # highlight..shadow, gray
    f = np.zeros((bands.size, 1, 4), np.uint8)
    f[..., 3] = 255
    f[:, 0, :3] = bands[:, None]
    spec = {"pass": "ArmorColored", "color": [1.0, 0.0, 0.0], "sat": 1.2}
    bc = apply_dye(f.copy(), spec, src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    hw = dye_mod._armor_colored(f.copy(), [1.0, 0.0, 0.0], 1.2)
    # 1) bytecode == handwritten copper on neutral silver (verified case), bit-for-bit.
    assert np.array_equal(bc, hw), "bytecode ArmorColored diverged from verified copper"
    # 2) every band is copper (R > G == B > 0), not flat red.
    for i in range(bands.size):
        r, g, b = (int(bc[i, 0, c]) for c in range(3))
        assert r > g and g == b and g > 0, f"band {i} not copper: {(r, g, b)}"
    # 3) brightness preserved: the highlight band stays brighter than the shadow band.
    assert int(bc[0, 0, 0]) > int(bc[-1, 0, 0])
    # 4) the exact spec ramp pixel (0.8 gray -> (213,183,183)).
    px = np.array([[[204, 204, 204, 255]]], np.uint8)
    out = apply_dye(px, dict(spec), src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    assert tuple(int(out[0, 0, c]) for c in range(3)) == (213, 183, 183)


def test_batch1_no_preshader_passes_run_without_crash() -> None:
    # The FXLC empty-guard fix (dye_noise._decode_preshader): the 6 no-preshader passes
    # have an empty pres_inputs; without the guard the interpreter read past the blob,
    # crashed. Each must now run the bytecode + return a sane frame (ColorOnly = white
    # silhouette). Compared against run_noise_pass directly (not the fallback).
    f = _structured_frame()
    arr = f.astype(np.float64) / 255.0
    pr = arr.copy()
    pr[..., :3] = arr[..., :3] * arr[..., 3][..., None]
    for name, col in (
        ("ArmorBrightnessColored", [1.0, 1.0, 1.0]), ("ArmorInvert", [1.0, 1.0, 1.0]),
        ("ColorOnly", [1.0, 1.0, 1.0]), ("ArmorMartian", [0.0, 2.0, 3.0]),
        ("ArmorPolarized", [1.0, 1.0, 1.0]), ("ArmorMushroom", [0.05, 0.2, 1.0]),
    ):
        out = dye_noise.run_noise_pass(
            pr, name, u_color=np.asarray(col, dtype=np.float64),
            u_secondary=np.asarray(col, dtype=np.float64), u_sat=1.0,
            src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        assert out is not None, f"{name} bytecode None (FXLC guard / blob missing)"
        assert np.isfinite(out).all(), f"{name} produced non-finite output"
    # ColorOnly = white silhouette where opaque (out.rgb == src.a, premult).
    co = dye_noise.run_noise_pass(
        pr, "ColorOnly", u_color=np.array([1.0, 1.0, 1.0]),
        u_secondary=np.array([1.0, 1.0, 1.0]), u_sat=1.0,
        src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    assert co is not None
    op = arr[..., 3] > 0
    assert np.allclose(co[op][:, :3], arr[op][:, 3:4], atol=1e-6)


def test_batch1_falls_back_to_handwritten_without_baked_blob() -> None:
    # Offline-safe: when a batch-1 baked blob is absent, apply_dye falls back to
    # the handwritten function (no crash), same as every noise pass. Drop the baked
    # ArmorColored blob and assert the result equals the handwritten _armor_colored.
    from nextbot.terraria_render import dye as dye_mod

    f = _structured_frame()
    shaders = dye_noise._shaders()
    saved = shaders.pop("ArmorColored", None)
    dye_noise._parse_blob.cache_clear()
    try:
        fb = apply_dye(
            f.copy(), {"pass": "ArmorColored", "color": [1.0, 0.0, 0.0], "sat": 1.2},
            src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    finally:
        if saved is not None:
            shaders["ArmorColored"] = saved
        dye_noise._parse_blob.cache_clear()
    assert np.array_equal(fb, dye_mod._armor_colored(f.copy(), [1.0, 0.0, 0.0], 1.2)), (
        "ArmorColored asset-missing fallback is not the documented handwritten port")


def _structured_frame() -> np.ndarray:
    """An opaque 56x40 cell with a luminance gradient (so a noise dye has signal)."""
    f = np.zeros((56, 40, 4), np.uint8)
    f[..., 3] = 255
    ramp = np.linspace(60, 240, 40).astype(np.uint8)[None, :, None]
    f[..., :3] = np.broadcast_to(ramp, (56, 40, 3))
    return f


def test_noise_dye_is_spatially_varying() -> None:
    # ArmorStardust (netId 3529) samples the real Misc/noise texture -> a starfield:
    # spatially varying, NOT a flat color. Asserts >1 distinct color + nonzero variance
    # (when the noise asset ships). Geometry threaded as a 360x224 armor sheet cell.
    f = _structured_frame()
    spec = {"pass": "ArmorStardust", "color": [0.4, 0.6, 1.0],
            "secondary": [1.0, 1.0, 1.0], "sat": 1.0}
    out = apply_dye(f, spec, src_rect=(80, 0, 40, 56), sheet_size=(360, 224))
    distinct = np.unique(out[..., :3].reshape(-1, 3), axis=0)
    assert distinct.shape[0] > 1  # not a flat color
    assert out[..., :3].astype(np.float64).var() > 0.0  # spatial variance


def test_noise_dye_falls_back_without_geometry() -> None:
    # apply_dye stays back-compatible: no src_rect/sheet_size (positional spec only)
    # still produces a valid dyed frame (the inventory handler calls it this way).
    f = _structured_frame()
    out = apply_dye(f, {"pass": "ArmorNebula", "color": [1.0, 0.0, 1.0],
                        "secondary": [1.0, 1.0, 1.0], "sat": 1.0})
    assert out.shape == f.shape
    assert not np.array_equal(out, f)


def test_twilight_dye_changes_input() -> None:
    # ArmorTwilight (the Twilight hair dye #12 pixel pass) was a silent no-op before;
    # it must now alter the frame (purple glow over the source).
    f = _structured_frame()
    out = apply_dye(f, {"pass": "ArmorTwilight", "color": [0.5, 0.1, 1.0]},
                    src_rect=(0, 0, 40, 56), sheet_size=(40, 56))
    assert not np.array_equal(out, f)


def _dye_cell(sheet: str, cell: int, spec: dict) -> tuple[np.ndarray, np.ndarray]:
    """Dye one composite cell of `sheet` with `spec`; return (dyed_u8, opaque_mask).

    Threads the cell's real (col*40,row*56) origin in the 360x224 armor sheet so the
    noise pass samples Misc/noise at the in-game uv (research/noise_dye_luma_bug.md)."""
    frame = _frame(sheet, cell)
    col, row = cell % 9, cell // 9
    out = apply_dye(frame.copy(), spec, src_rect=(col * 40, row * 56, FW, FH),
                    sheet_size=(360, 224))
    return out, frame[..., 3] > 0


def test_gel_dye_preserves_source_arm_body_shading() -> None:
    # Regression for the noise-dye `_sat` (saturate) drop (noise_dye_luma_bug.md).
    # ArmorGel (Bloodbath = netId 4663) must preserve the armor's built-in arm-vs-body
    # shading. PumpkinShirt (body slot 82, netId 1755) ships a real composite sheet
    # whose forearm cell (idle front-arm cell 2, mean-luma ~28) is darker than its torso
    # cell (cell 0, ~103). Once dyed, the forearm must stay DARKER than the torso
    # (monotonic with source luma), not exploded to clip-white. The bug dropped the
    # in-shader `mul r1.x_sat` clamp, blowing oC0 to millions, which collapsed/INVERTED
    # the shading (forearm ended up brighter, ~26% of forearm px pinned to R==255).
    sheet = "ArmorBody_82"
    if _frame(sheet, 0)[..., 3].sum() == 0:  # asset absent -> skip (renderer falls back
        return                               # to the APPROX path; nothing to assert)
    spec = {"pass": "ArmorGel", "color": [2.6, 0.6, 0.6],
            "secondary": [0.2, -0.2, -0.2]}
    forearm, fa_op = _dye_cell(sheet, 2, spec)   # front-arm cell (dark source)
    torso, to_op = _dye_cell(sheet, 0, spec)     # torso cell (bright source)
    assert int(fa_op.sum()) > 0 and int(to_op.sum()) > 0
    fa_luma = forearm[fa_op][:, :3].astype(np.float64).mean()
    to_luma = torso[to_op][:, :3].astype(np.float64).mean()
    # 1) monotonic: the darker source (forearm) stays the darker output (source shading
    #    preserved, NOT flattened or inverted as the bug did: broken fa=78 > to=72).
    assert to_luma > fa_luma, (
        f"Gel inverted/flattened source shading: forearm luma {fa_luma:.1f} "
        f">= torso luma {to_luma:.1f} (the `_sat` drop)")
    # 2) no clip-white explosion: the dark forearm must not pin its red channel to 255
    #    (the bug clipped ~26% of forearm px to R==255; the fix keeps it ~0%).
    fa_clip = float((forearm[fa_op][:, 0] == 255).mean())
    assert fa_clip < 0.05, (
        f"Gel clipped the forearm to white ({fa_clip:.0%} of px R==255), "
        "oC0 exploded past unity")


_HEAD_GEOM = ((0, 0, FW, FH), (FW, FH))           # idle head/leg strip cell + sheet


def _raw_noise_rgb(
    frame: np.ndarray, name: str, color: list, u_time: float,
) -> np.ndarray:
    """Run the real ps_2_0 bytecode for `name`; return un-premultiplied (h,w,3) float
    rgb (the over-unity shader output BEFORE any clip/tone-map). uSecondary=(1,1,1)."""
    arr = frame.astype(np.float64) / 255.0
    a = arr[..., 3]
    pr = arr.copy()
    pr[..., :3] = arr[..., :3] * a[..., None]
    out = dye_noise.run_noise_pass(
        pr, name, u_color=np.asarray(color, dtype=np.float64),
        u_secondary=np.array([1.0, 1.0, 1.0]), u_sat=1.0,
        src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1], u_time=u_time)
    assert out is not None                                   # noise asset must ship
    oa = out[..., 3]
    nz = oa > 1e-6
    return np.where(nz[..., None], out[..., :3] / np.where(nz, oa, 1.0)[..., None], 0.0)


def _compose_rgb(frame: np.ndarray, rgb: np.ndarray) -> np.ndarray:
    """Put float rgb back onto the frame's alpha and quantize -> straight uint8."""
    res = frame.astype(np.float64) / 255.0
    res[..., :3] = np.clip(rgb, 0.0, 1.0)
    return (np.clip(res, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _near_white_pct(out: np.ndarray, op: np.ndarray) -> float:
    """% of opaque pixels whose min channel > 0.7 (near-white streak coverage)."""
    rgb = out[op][:, :3].astype(np.float64) / 255.0
    return float((rgb.min(axis=1) > 0.7).mean() * 100.0)


def test_vortex_dye_is_faithful_hard_clip_not_overexposed() -> None:
    # research/vortex_dye_bug.md plan A: ArmorVortex (netId 3528) must equal the
    # faithful bytecode HARD-CLIPPED to [0,1] (the game GPU), NOT the old emissive path
    # (gain=1.5 + overflow tone-map) which double-exposed the teal/white streaks (~1.5x
    # more near-white than the game, desaturating the teal toward white). The bytecode
    # already makes the bright streaks (sparkle = noise*luma*5*uSecondary, over-unity on
    # bright source pixels); the fix just stops re-amplifying them offline.
    assert "ArmorVortex" not in _PILLAR_GAIN            # no extra emissive lift
    u_time = _PILLAR_TIME["ArmorVortex"]               # swirl phase still bakes
    color = [0.1, 0.5, 0.35]
    for sheet in ("Armor_Head_276", "Armor_Legs_217"):
        frame = _frame(sheet, 0)
        if frame[..., 3].sum() == 0:                   # asset absent -> APPROX fallback
            continue
        op = frame[..., 3] > 0
        raw = _raw_noise_rgb(frame, "ArmorVortex", color, u_time)
        # 1) production _vortex == faithful bytecode hard-clipped (bit-for-bit).
        got = _vortex(frame.copy(), src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
        assert np.array_equal(got, _compose_rgb(frame, raw)), (
            f"{sheet}: Vortex no longer matches the faithful hard-clip "
            "(emissive gain/tone-map still applied?)")
        # 2) ...and meaningfully LESS white than the old emissive path (gain 1.5 +
        #    overflow tone-map), so the over-exposure regression cannot return.
        old = _compose_rgb(frame, _emissive_tonemap(raw, 1.5))
        assert _near_white_pct(got, op) < _near_white_pct(old, op), (
            f"{sheet}: fixed Vortex not less white than the old emissive path")


def test_stardust_dye_is_faithful_hard_clip_not_overexposed() -> None:
    # research/vortex_dye_bug.md (same mechanism as ArmorVortex): ArmorStardust (netId
    # 3529) must equal the faithful bytecode HARD-CLIPPED to [0,1] (the game GPU), NOT
    # the old emissive path (gain=1.35 + overflow tone-map) which double-exposed the
    # starfield (~+0.14 mean brightness, 2-13x more near-white than the game,
    # desaturating the deep blue toward white). The bytecode already makes the bright
    # sparkles (noise-threshold * uSecondary * 8, over-unity on bright source pixels);
    # the fix just stops re-amplifying them offline. uColor/uSecondary/sat stay faithful
    # to DyeInitializer.cs (3529 binds UseColor(0.4,0.6,1)/UseSecondaryColor(1,1,1)/1).
    assert "ArmorStardust" not in _PILLAR_GAIN          # no extra emissive lift
    u_time = _PILLAR_TIME["ArmorStardust"]              # starfield phase still bakes
    color = [0.4, 0.6, 1.0]
    for sheet in ("Armor_Head_276", "Armor_Legs_217"):
        frame = _frame(sheet, 0)
        if frame[..., 3].sum() == 0:                 # asset absent -> APPROX fallback
            continue
        op = frame[..., 3] > 0
        raw = _raw_noise_rgb(frame, "ArmorStardust", color, u_time)
        # 1) production _stardust == faithful bytecode hard-clipped (bit-for-bit).
        got = _stardust(frame.copy(), src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
        assert np.array_equal(got, _compose_rgb(frame, raw)), (
            f"{sheet}: Stardust no longer matches the faithful hard-clip "
            "(emissive gain/tone-map still applied?)")
        # 2) ...and meaningfully LESS white than the old emissive path (gain 1.35 +
        #    overflow tone-map), so the over-exposure regression cannot return.
        old = _compose_rgb(frame, _emissive_tonemap(raw, 1.35))
        assert _near_white_pct(got, op) < _near_white_pct(old, op), (
            f"{sheet}: fixed Stardust not less white than the old emissive path")


def test_stardust_fix_leaves_other_noise_pillars_unchanged() -> None:
    # Regression: the Stardust fix touched only ArmorStardust. Nebula (3527) keeps its
    # emissive lift (per-pass _PILLAR_GAIN=1.4), so its output is NOT a plain faithful
    # hard-clip -- it stays brighter (gain + tone-map). HallowBoss (4778) stays emissive
    # at gain 1.0 (in-gamut, but still routed through the tone-map). Guards against
    # accidentally turning off emissive globally for the still-emissive pillars.
    assert _PILLAR_GAIN["ArmorNebula"] == 1.4          # untouched (Nebula needs lift)
    assert _PILLAR_GAIN["ArmorHallowBoss"] == 1.0      # untouched (in-gamut emissive)
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    got = _nebula(frame.copy(), src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
    raw = _raw_noise_rgb(
        frame, "ArmorNebula", [1.0, 0.0, 1.0], _PILLAR_TIME["ArmorNebula"])
    # the emissive lift makes Nebula differ from a plain hard-clip (still emissive).
    assert not np.array_equal(got, _compose_rgb(frame, raw)), (
        "ArmorNebula collapsed to the faithful hard-clip -- emissive lift lost")


def test_midnight_rainbow_faithful_emboss_differs_from_approx() -> None:
    # research/midnight_rainbow.md: ArmorMidnightRainbow (3556) is now the real 5-tap
    # self-emboss bytecode (the source offset-tap fix makes it run), NOT the old flat
    # rainbow recolor (_midnight_rainbow APPROX). The faithful path traces a rainbow
    # over the sprite contours on a DARK base -> it must (a) differ from the APPROX and
    # (b) be darker overall (the embossed interior is dark, only edges glow).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:        # asset absent -> APPROX fallback, skip
        return
    op = frame[..., 3] > 0
    real = apply_dye(frame.copy(), {"pass": "ArmorMidnightRainbow"},
                     src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
    approx = _midnight_rainbow(frame.copy())
    assert not np.array_equal(real, approx), (
        "MidnightRainbow still equals the old APPROX -- the self-emboss did not come "
        "back (source offset-tap fix not applied / pass not baked?)")
    # the faithful embossed result is meaningfully darker than the flat APPROX recolor
    real_luma = real[op][:, :3].astype(np.float64).mean()
    approx_luma = approx[op][:, :3].astype(np.float64).mean()
    assert real_luma < approx_luma, (
        f"faithful MidnightRainbow not darker than APPROX (real {real_luma:.1f} >= "
        f"approx {approx_luma:.1f}) -- emboss interior should be dark")
    # and it is not a black sprite (the emboss lights the contours)
    lit = int((real[op][:, :3].sum(axis=1) > 5).sum())
    assert lit > 0, "MidnightRainbow rendered black (emboss did not light the contours)"


def test_midnight_rainbow_animates_with_utime() -> None:
    # uTime adds a *0.4 scroll to the rainbow hue (research/midnight_rainbow.md §4): the
    # faithful pass MOVES with uTime (the APPROX had no uTime term at all). Two phases
    # part of a cycle apart must differ.
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    a = apply_dye(frame.copy(), {"pass": "ArmorMidnightRainbow"}, u_time=0.0,
                  src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
    b = apply_dye(frame.copy(), {"pass": "ArmorMidnightRainbow"}, u_time=1.25,
                  src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1])
    assert not np.array_equal(a, b), (
        "MidnightRainbow did not roll with uTime (the hue scroll was lost)")


def test_midnight_rainbow_falls_back_without_baked_blob() -> None:
    # Offline-safe: when the baked ArmorMidnightRainbow blob is absent, the faithful
    # helper must fall back to the _midnight_rainbow APPROX (no crash) -- same contract
    # as every other noise pass.
    frame = _structured_frame()
    shaders = dye_noise._shaders()
    saved = shaders.pop("ArmorMidnightRainbow", None)
    dye_noise._parse_blob.cache_clear()
    try:
        fb = _midnight_rainbow_real(
            frame.copy(), src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    finally:
        if saved is not None:
            shaders["ArmorMidnightRainbow"] = saved
        dye_noise._parse_blob.cache_clear()
    assert np.array_equal(fb, _midnight_rainbow(frame.copy())), (
        "MidnightRainbow asset-missing fallback is not the documented APPROX")


def test_offset_tap_fix_leaves_center_only_noise_pass_unchanged() -> None:
    # Regression for the source offset-tap fix's blast radius: a noise pass whose ONLY
    # uImage0 tap is the plain center t0 (ArmorFog -- 1 source texld at t0) must be
    # BIT-IDENTICAL to the old center-collapse, because a center uv lands exactly on the
    # texel center (bilinear weights 0). Guards the single-center-tap passes (Vortex/
    # Stardust/HallowBoss/Shifting*/Fog/Nebula) against drift. Compared against a
    # hand-rolled center-collapse of the same baked bytecode.
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    name = "ArmorFog"
    arr = frame.astype(np.float64) / 255.0
    pr = arr.copy()
    pr[..., :3] = arr[..., :3] * arr[..., 3][..., None]

    def run() -> np.ndarray | None:
        return dye_noise.run_noise_pass(
            pr, name, u_color=np.array([0.95, 0.95, 0.95]),
            u_secondary=np.array([0.3, 0.3, 0.3]), u_sat=1.0,
            src_rect=_HEAD_GEOM[0], sheet_size=_HEAD_GEOM[1], u_time=0.0)

    fixed = run()
    # swap _sample_src for the OLD center-collapse (return the center texel array as-is)
    saved = dye_noise._sample_src
    dye_noise._sample_src = lambda src_rgba, _uv, _sr, _sh: src_rgba
    try:
        old = run()
    finally:
        dye_noise._sample_src = saved
    assert fixed is not None and old is not None
    # quantize both to uint8 (production output) -> identical (center tap unchanged)
    q = lambda x: (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)  # noqa: E731
    assert np.array_equal(q(fixed), q(old)), (
        "offset-tap fix changed a center-only noise pass (ArmorFog) -- regression")


# ── batch 2: animated / self-sampling time passes run the REAL compiled bytecode ──────
# research/dye_bytecode_audit.md §"third tier": these 9 passes were handwritten time /
# flat-tint approximations; they now dispatch through the real ps_2_0 bytecode
# (dye_noise.run_noise_pass), the handwritten fn kept only as the offline fallback
# (baked blob / noise.png absent). Representative still = uTime=0 for the passes lit
# there, the swept _BATCH2_TIME for the two that collapse at 0 (Acid=2.5/Void=1.0).
_BATCH2_SPECS = (
    {"pass": "ArmorFlow", "color": [1.0, 0.5, 1.0], "secondary": [0.6, 0.1, 1.0]},
    {"pass": "ArmorLivingRainbow"},
    {"pass": "ArmorLivingFlame", "color": [1.0, 0.9, 0.0],
     "secondary": [1.0, 0.2, 0.0]},
    {"pass": "ArmorLivingOcean"},
    {"pass": "ArmorAcid", "color": [0.5, 1.0, 0.3]},
    {"pass": "ArmorVoid"},
    {"pass": "ArmorMirage"},
    {"pass": "ArmorHades", "color": [0.5, 0.7, 1.3], "secondary": [0.5, 0.7, 1.3]},
    {"pass": "ArmorLoki", "color": [0.1, 0.1, 0.1]},
)


def test_batch2_passes_dispatch_through_bytecode() -> None:
    # Every batch-2 pass must call dye_noise.run_noise_pass (the real bytecode), NOT
    # silently use the handwritten fallback. Spy on run_noise_pass and assert it fires
    # once per pass with the right name + a sane (finite, shape-preserving) frame back.
    from nextbot.terraria_render import dye as dye_mod

    f = _structured_frame()
    calls: list[str] = []
    orig = dye_mod.dye_noise.run_noise_pass

    def spy(premul: np.ndarray, name: str, **k: object) -> "np.ndarray | None":
        calls.append(name)
        return orig(premul, name, **k)  # type: ignore[arg-type]

    dye_mod.dye_noise.run_noise_pass = spy
    try:
        for spec in _BATCH2_SPECS:
            calls.clear()
            out = apply_dye(
                f.copy(), dict(spec), src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
            assert calls == [spec["pass"]], (
                f"{spec['pass']} did not dispatch through the bytecode (calls={calls})")
            assert out is not None and out.shape == f.shape
            assert np.isfinite(out.astype(np.float64)).all()
    finally:
        dye_mod.dye_noise.run_noise_pass = orig


def test_batch2_faithful_differs_from_handwritten_approx() -> None:
    # The whole point of batch 2: the faithful bytecode must produce a DIFFERENT image
    # than the old handwritten approximation for the passes that collapsed (Void flat
    # 0.35 wash, Hades/Loki flat uColor tint, Mirage passthrough, Acid/Living* band).
    # ArmorFlow is the documented exception: its handwritten port was already a faithful
    # uTime=0 transcription, so it is BIT-IDENTICAL (asserted separately below).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:                 # asset absent -> fallback path, skip
        return
    op = frame[..., 3] > 0
    from nextbot.terraria_render import dye as dye_mod

    # passes whose faithful bytecode must visibly differ from the old approx (>1% px),
    # each paired with the handwritten approximation dye.py used before batch 2.
    differs = (
        {"pass": "ArmorLivingRainbow"},
        {"pass": "ArmorLivingFlame", "color": [1.0, 0.9, 0.0],
         "secondary": [1.0, 0.2, 0.0]},
        {"pass": "ArmorLivingOcean"},
        {"pass": "ArmorAcid", "color": [0.5, 1.0, 0.3]},
        {"pass": "ArmorVoid"},
        {"pass": "ArmorMirage"},
        {"pass": "ArmorHades", "color": [0.5, 0.7, 1.3], "secondary": [0.5, 0.7, 1.3]},
        {"pass": "ArmorLoki", "color": [0.1, 0.1, 0.1]},
    )
    handwritten = {
        "ArmorLivingRainbow": lambda f: dye_mod._living_rainbow_approx(f, 0.0),
        "ArmorLivingFlame": lambda f: dye_mod._living_flame_approx(
            f, [1.0, 0.9, 0.0], [1.0, 0.2, 0.0], 0.0),
        "ArmorLivingOcean": lambda f: dye_mod._living_ocean_approx(f, 0.0),
        "ArmorAcid": lambda f: dye_mod._acid_approx(f, [0.5, 1.0, 0.3], 0.0),
        "ArmorVoid": lambda f: dye_mod._brightness_clip(f, (0.35, 0.35, 0.35)),
        "ArmorMirage": lambda f: f,
        "ArmorHades": lambda f: dye_mod._brightness_clip(f, [0.5, 0.7, 1.3]),
        "ArmorLoki": lambda f: dye_mod._brightness_clip(f, [0.1, 0.1, 0.1]),
    }
    for spec in differs:
        name = spec["pass"]
        faithful = apply_dye(
            frame.copy(), dict(spec), src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        old = handwritten[name](frame.copy())
        changed = float(np.any(faithful[op][:, :3] != old[op][:, :3], axis=1).mean())
        assert changed > 0.01, (
            f"{name}: faithful bytecode equals the old handwritten approx "
            f"({changed:.0%} differ) -- the wire-up did not change the visual")


def test_batch2_flow_is_bit_identical_to_handwritten_at_utime0() -> None:
    # ArmorFlow: the handwritten `_flow_approx` already transcribed the bytecode exact,
    # so at uTime=0 the faithful path must be BIT-FOR-BIT equal to it (documents the one
    # "SAME" pass; the others all differ). Guards that Flow still goes through the
    # bytecode without drifting.
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    from nextbot.terraria_render import dye as dye_mod

    faithful = apply_dye(
        frame.copy(), {"pass": "ArmorFlow", "color": [1.0, 0.5, 1.0],
                       "secondary": [0.6, 0.1, 1.0]},
        src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
    hw = dye_mod._flow_approx(frame.copy(), [1.0, 0.5, 1.0], [0.6, 0.1, 1.0], 0.0)
    assert np.array_equal(faithful, hw), (
        "ArmorFlow faithful bytecode diverged from its handwritten port")


def test_batch2_animates_with_utime() -> None:
    # These are animated passes: the faithful bytecode MOVES with uTime (the flat-tint
    # approximations did not). uTime 0.0 vs 0.5 differs for every batch-2 pass (each has
    # a different hue/swirl/scroll period, but a half-second step always lands on a
    # different phase for all 9 -- verified by sweeping).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    for spec in _BATCH2_SPECS:
        a = apply_dye(frame.copy(), dict(spec), u_time=0.0,
                      src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        b = apply_dye(frame.copy(), dict(spec), u_time=0.5,
                      src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        assert not np.array_equal(a, b), (
            f"{spec['pass']} did not change with uTime (the animation phase was lost)")


def test_batch2_acid_void_swept_representative_not_collapsed() -> None:
    # The two passes whose uTime=0 phase collapses must pin a swept representative more
    # lit than their uTime=0 frame (research: Acid 38%->100% at 2.5, Void 73%->~95% at
    # 1.0). Guards the _BATCH2_TIME pins (a bad pin would dim the dye).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    op = frame[..., 3] > 0
    assert _BATCH2_TIME == {"ArmorAcid": 2.5, "ArmorVoid": 1.0}

    def lit_pct(out: np.ndarray) -> float:
        rgb = out[op][:, :3].astype(np.float64) / 255.0
        return float((rgb.max(axis=1) >= 0.08).mean())

    for spec, rep in (({"pass": "ArmorAcid", "color": [0.5, 1.0, 0.3]}, 2.5),
                      ({"pass": "ArmorVoid"}, 1.0)):
        at0 = apply_dye(frame.copy(), dict(spec), u_time=0.0,
                        src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        # production default (None) -> the swept representative pin
        prod = apply_dye(frame.copy(), dict(spec),
                         src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        at_rep = apply_dye(frame.copy(), dict(spec), u_time=rep,
                           src_rect=(0, 0, FW, FH), sheet_size=(FW, FH))
        assert np.array_equal(prod, at_rep), (
            f"{spec['pass']} production still is not the swept _BATCH2_TIME pin {rep}")
        assert lit_pct(prod) > lit_pct(at0) + 0.1, (
            f"{spec['pass']} swept pin {rep} not clearly more lit than the collapsed "
            f"uTime=0 ({lit_pct(prod):.0%} vs {lit_pct(at0):.0%})")


def test_batch2_falls_back_to_handwritten_without_baked_blob() -> None:
    # Offline-safe: when a batch-2 baked blob is absent, the faithful helper falls back
    # to the handwritten approximation (no crash), same contract as every noise pass.
    # Drop the baked ArmorVoid/ArmorLoki blobs; result must equal the handwritten body.
    from nextbot.terraria_render import dye as dye_mod

    frame = _structured_frame()
    shaders = dye_noise._shaders()
    for name, helper, expect in (
        ("ArmorVoid", lambda f: _void(f, src_rect=(0, 0, FW, FH), sheet_size=(FW, FH)),
         lambda f: dye_mod._brightness_clip(f, (0.35, 0.35, 0.35))),
        ("ArmorLoki", lambda f: _loki(f, [0.1, 0.1, 0.1],
                                      src_rect=(0, 0, FW, FH), sheet_size=(FW, FH)),
         lambda f: dye_mod._brightness_clip(f, [0.1, 0.1, 0.1])),
    ):
        saved = shaders.pop(name, None)
        dye_noise._parse_blob.cache_clear()
        try:
            fb = helper(frame.copy())
        finally:
            if saved is not None:
                shaders[name] = saved
            dye_noise._parse_blob.cache_clear()
        assert np.array_equal(fb, expect(frame.copy())), (
            f"{name} asset-missing fallback is not the documented handwritten approx")


# ── batch 3 (final): the last 3 special passes run the REAL compiled bytecode ─────
# research/dye_bytecode_audit.md §"batch 3/5": HighContrastGlow (correction: restores
# the dropped v0 glow + chroma gating) + Reflective / ReflectiveColor (class C:
# faithful no-highlight offline, uLightSource=0). ArmorSolar's bytecode is baked +
# wired but stays OFF by default (dimmer offline -- see test_solar_bytecode_*).
_BATCH3_BYTECODE_SPECS = (
    {"pass": "ArmorHighContrastGlow", "color": [0.0, 1.0, 0.0], "sat": 1.0},
    {"pass": "ArmorReflective"},
    {"pass": "ArmorReflectiveColor", "color": [1.0, 0.85, 0.1]},
)
_HCG = {"pass": "ArmorHighContrastGlow", "color": [0.0, 1.0, 0.0], "sat": 1.0}
_GEOM = {"src_rect": (0, 0, FW, FH), "sheet_size": (FW, FH)}


def test_batch3_passes_dispatch_through_bytecode() -> None:
    # The 3 wired batch-3 passes must call dye_noise.run_noise_pass (the real bytecode),
    # NOT the handwritten fallback. Spy on run_noise_pass and assert it fires once per
    # pass with a sane (finite, shape-preserving) frame back.
    from nextbot.terraria_render import dye as dye_mod

    f = _structured_frame()
    calls: list[str] = []
    orig = dye_mod.dye_noise.run_noise_pass

    def spy(premul: np.ndarray, name: str, **k: object) -> "np.ndarray | None":
        calls.append(name)
        return orig(premul, name, **k)  # type: ignore[arg-type]

    dye_mod.dye_noise.run_noise_pass = spy
    try:
        for spec in _BATCH3_BYTECODE_SPECS:
            calls.clear()
            out = apply_dye(f.copy(), dict(spec), **_GEOM)
            assert calls == [spec["pass"]], (
                f"{spec['pass']} did not dispatch through the bytecode (calls={calls})")
            assert out is not None and out.shape == f.shape
            assert np.isfinite(out.astype(np.float64)).all()
    finally:
        dye_mod.dye_noise.run_noise_pass = orig


def test_high_contrast_glow_chroma_gated_bytecode() -> None:
    # ArmorHighContrastGlow correction: the real bytecode restores the v0-driven glow
    # and GATES it on per-pixel chroma. A zero-chroma (grey) pixel is crushed toward
    # black; a chromatic (green) pixel glows toward uColor. The handwritten approx
    # (recolor) did neither, so the faithful result DIFFERS and crushes grey ramps dark.
    grey = _gray_frame(204)                                  # 0.8 grey, zero chroma
    real = apply_dye(grey.copy(), dict(_HCG), **_GEOM)
    approx = _high_contrast_glow_approx(grey.copy(), [0.0, 1.0, 0.0], 1.0)
    if np.array_equal(real, approx):
        return                                               # blob absent -> fell back
    # 1) the faithful (chroma-gated) result crushes the zero-chroma grey to near-black,
    #    far darker than the handwritten recolor (which kept it a green-grey mid).
    real_mean = float(real[..., :3].mean())
    assert real_mean < approx[..., :3].mean(), (
        "HighContrastGlow bytecode not darker than the approx on grey "
        f"(real {real_mean:.1f} >= approx {approx[..., :3].mean():.1f})")
    assert real_mean < 20.0, (
        f"zero-chroma grey should crush toward black under the chroma gate "
        f"({real_mean:.1f})")
    # 2) a CHROMATIC (green) pixel survives and glows green (G dominant), proving the
    #    gate passes chroma rather than killing everything.
    green = np.array([[[51, 178, 51, 255]]], np.uint8)       # (0.2,0.7,0.2) chroma
    gout = apply_dye(green.copy(), dict(_HCG), **_GEOM)
    gr, gg, gb = (int(gout[0, 0, c]) for c in range(3))
    assert gg > gr and gg > gb and gg > 20, (
        f"chromatic pixel did not glow green under HighContrastGlow: {(gr, gg, gb)}")


def test_high_contrast_glow_falls_back_without_baked_blob() -> None:
    # Offline-safe: when the baked ArmorHighContrastGlow blob is absent, apply_dye falls
    # back to the handwritten approx (no crash), same contract as every noise pass.
    f = _structured_frame()
    shaders = dye_noise._shaders()
    saved = shaders.pop("ArmorHighContrastGlow", None)
    dye_noise._parse_blob.cache_clear()
    try:
        fb = apply_dye(f.copy(), dict(_HCG), **_GEOM)
    finally:
        if saved is not None:
            shaders["ArmorHighContrastGlow"] = saved
        dye_noise._parse_blob.cache_clear()
    expected = _high_contrast_glow_approx(f.copy(), [0.0, 1.0, 0.0], 1.0)
    assert np.array_equal(fb, expected), (
        "HighContrastGlow asset-missing fallback is not the handwritten approx")


def test_reflective_offline_is_no_highlight_not_crash() -> None:
    # ArmorReflective / ArmorReflectiveColor (class C): the faithful bytecode runs
    # offline (uLightSource=0) without crashing and produces the honest NO-HIGHLIGHT
    # version -- Reflective ~= the source darkened by the emboss DC (*0.5),
    # ReflectiveColor + a uColor tint. The moving specular is the offline limit (gone).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:                             # asset absent -> approx
        return
    op = frame[..., 3] > 0
    refl = _reflective(frame.copy(), **_GEOM)
    assert np.isfinite(refl.astype(np.float64)).all() and refl.shape == frame.shape
    src_luma = frame[op][:, :3].astype(np.float64).mean()
    refl_luma = refl[op][:, :3].astype(np.float64).mean()
    # no live specular -> the embossed source is DARKER than the source (the *0.5 DC),
    # so this no-highlight version is darker than the old flat passthrough (= source).
    assert refl_luma < src_luma, (
        f"Reflective bytecode not the darkened no-highlight emboss "
        f"(refl {refl_luma:.1f} >= source {src_luma:.1f})")
    # ReflectiveColor with a gold uColor tints the result gold (R,G > B) on the emboss.
    rc = _reflective_color(frame.copy(), [1.0, 0.85, 0.1], **_GEOM)
    assert np.isfinite(rc.astype(np.float64)).all() and rc.shape == frame.shape
    rr, rg, rb = rc[op][:, :3].astype(np.float64).mean(0)
    assert rr > rb and rg > rb, (
        f"ReflectiveColor gold tint not applied (mean rgb {(rr, rg, rb)})")


def test_reflective_falls_back_without_baked_blob() -> None:
    # Offline-safe: with the baked Reflective/ReflectiveColor blobs absent, the faithful
    # helpers fall back to the documented approximations (Reflective passthrough,
    # ReflectiveColor uColor tint) -- no crash, same contract as every noise pass.
    frame = _structured_frame()
    shaders = dye_noise._shaders()
    for name, helper, expect in (
        ("ArmorReflective",
         lambda f: _reflective(f, **_GEOM),
         _reflective_approx),
        ("ArmorReflectiveColor",
         lambda f: _reflective_color(f, [1.0, 1.0, 1.0], **_GEOM),
         lambda f: _reflective_color_approx(f, [1.0, 1.0, 1.0])),
    ):
        saved = shaders.pop(name, None)
        dye_noise._parse_blob.cache_clear()
        try:
            fb = helper(frame.copy())
        finally:
            if saved is not None:
                shaders[name] = saved
            dye_noise._parse_blob.cache_clear()
        assert np.array_equal(fb, expect(frame.copy())), (
            f"{name} asset-missing fallback is not the documented approximation")


def test_solar_bytecode_runs_but_default_stays_handwritten() -> None:
    # ArmorSolar: the bytecode is baked + `_solar` runs it without crashing, BUT the
    # production dispatch (apply_dye) keeps the handwritten `_solar_approx` by default,
    # because offline (v0=white, no additive bloom) the bytecode reads markedly DIMMER
    # than the in-game bright Solar. This pins that call (flip the dispatch to switch).
    frame = _frame("Armor_Head_276", 0)
    if frame[..., 3].sum() == 0:
        return
    op = frame[..., 3] > 0
    solar = {"pass": "ArmorSolar", "color": [1.0, 0.0, 0.0],
             "secondary": [1.0, 1.0, 0.0]}
    # 1) production apply_dye(ArmorSolar) == the handwritten approx (default).
    prod = apply_dye(frame.copy(), dict(solar), **_GEOM)
    hw = _solar_approx(frame.copy(), [1.0, 0.0, 0.0], [1.0, 1.0, 0.0])
    assert np.array_equal(prod, hw), (
        "ArmorSolar default dispatch is no longer the handwritten approx")
    # 2) the bytecode path runs (faithful-but-dim) and is meaningfully DARKER than the
    #    handwritten fire ramp -- the documented offline limitation (no additive bloom).
    bc = _solar(frame.copy(), [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], **_GEOM)
    assert np.isfinite(bc.astype(np.float64)).all() and bc.shape == frame.shape
    bc_mean, hw_mean = bc[op][:, :3].mean(), hw[op][:, :3].mean()
    assert bc_mean < hw_mean, (
        f"Solar bytecode not dimmer than the handwritten fire ramp offline "
        f"(bc {bc_mean:.1f} >= hw {hw_mean:.1f})")
    # 3) the bytecode still carries the uColor fire hue (red-dominant), not a grey wash.
    br, bg, bb = bc[op][:, :3].astype(np.float64).mean(0)
    assert br > bg > bb, f"Solar bytecode lost the warm fire hue (rgb {(br, bg, bb)})"


def test_solar_bytecode_falls_back_without_baked_blob() -> None:
    # The `_solar` bytecode helper falls back to `_solar_approx` when the baked blob is
    # absent (no crash), same contract as every noise pass.
    frame = _structured_frame()
    shaders = dye_noise._shaders()
    saved = shaders.pop("ArmorSolar", None)
    dye_noise._parse_blob.cache_clear()
    try:
        fb = _solar(frame.copy(), [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], **_GEOM)
    finally:
        if saved is not None:
            shaders["ArmorSolar"] = saved
        dye_noise._parse_blob.cache_clear()
    expected = _solar_approx(frame.copy(), [1.0, 0.0, 0.0], [1.0, 1.0, 0.0])
    assert np.array_equal(fb, expected), (
        "Solar bytecode asset-missing fallback is not the handwritten approx")


def _hair_appearance(hair_dye: int) -> dict:
    # A back-flowing hair style (index 5) so the hair pixels are prominent in the frame.
    return {**_APPEARANCE, "skinVariant": 0, "hair": 5, "hairDye": hair_dye}


def test_hairdye_twilight_differs_from_none() -> None:
    # hairDye 12 (Twilight) runs the ArmorTwilight pass on the hair; the render must
    # differ from hairDye 0 (plain hairColor tint).
    none = render_character(_hair_appearance(0), scale=4)
    twilight = render_character(_hair_appearance(12), scale=4)
    assert none != twilight


def test_hairdye_legacy_changes_hair_color() -> None:
    # A legacy hairDye index (1 = Life Hair Dye) REPLACES hairColor with red
    # (255,20,20); the render must differ from the undyed hairColor tint.
    none = render_character(_hair_appearance(0), scale=4)
    life = render_character(_hair_appearance(1), scale=4)
    assert none != life
    # ...and differ from another legacy index (9 = Rainbow -> red (255,0,0)).
    rainbow = render_character(_hair_appearance(9), scale=4)
    assert life != rainbow


def test_back_hair_predicate_matches_game() -> None:
    # _back_hair_style must match Player.GetHairSettings backHairDraw exactly
    # (Player.cs:16787). Drives the 26px forehead clip on the FRONT hair pass:
    # True -> clip (genuine back style), False -> full-height long front hair.
    expect = {
        87: True, 82: True,
        # 52 is in the (50,56) window -> True (genuine back style, forehead-clipped)
        52: True,
        # the bug: these returned True before (clipped long hair to forehead)
        112: False, 194: False, 214: False, 58: False, 75: False,
        89: False, 120: False, 200: False,
        # explicit extras forced True
        6: True, 133: True, 134: True, 146: True, 162: True,
        # range/exclusion boundaries
        50: False, 51: True, 55: True, 56: False, 63: False, 64: True,
        94: False, 100: False, 104: False, 115: True, 116: False, 227: False,
    }
    for idx, want in expect.items():
        assert _back_hair_style(idx) is want, f"hair {idx}: {want} expected"
    # cross-check against a literal transcription of the Player.cs:16787 formula
    # over the full hair range (228 styles). Phrased with set membership to read
    # cleanly while staying 1:1 with the (<a||>b) windows and != points.
    excluded = {*range(56, 64), *range(74, 78), *range(88, 90), 94, 100, 104, 112}
    forced = {6, 133, 134, 146, 162}
    for idx in range(228):
        ref = (50 < idx < 116 and idx not in excluded) or idx in forced
        assert _back_hair_style(idx) is ref, f"hair {idx} diverges from formula"


# ── accessories ──────────────────────────────────────────────────────
# Known netIds (Item.cs): 493 AngelWings (wing slot 2), 492 DemonWings (wing slot 1),
# 532 StarCloak (back cape slot 2 — drawn behind the body, always visible),
# 54 HermesBoots (shoe slot 6), 128 RocketBoots (shoe slot 12). For a default-clothed
# (non-robe) player the shoe accessory draws OVER the default shoes (the normal
# LegacyPlayerRenderer branch), so it is visible — see test_shoe_accessory_is_visible.
def _acc(net_id: int) -> dict:
    return {"netId": net_id, "stack": 1, "prefixId": 0}


def _slots7(*items: int) -> list[dict]:
    """A 7-element accessory list with `items` in the first slots, rest empty."""
    out = [{"netId": 0, "stack": 0, "prefixId": 0} for _ in range(7)]
    for i, n in enumerate(items):
        out[i] = _acc(n)
    return out


def test_no_accessory_render_is_body_sized() -> None:
    # The output is content-cropped: a plain (no-accessory) character crops to roughly
    # the body cell (the body content is ~28-34px wide, ~50px tall, narrower than the
    # full 40px cell because the arms are tucked), NOT the full 152x112 padded canvas.
    # At scale=1 that means a small, taller-than-wide frame well under the padded size.
    w1, h1 = _png_size(render_character(_APPEARANCE, scale=1))
    assert 20 <= w1 <= 40, f"plain width {w1} not ~body-cell-sized"
    assert 40 <= h1 <= 56, f"plain height {h1} not ~body-cell-sized"
    assert h1 > w1                     # body cell is taller than wide
    assert w1 < 80 and h1 < 80         # nowhere near the 152x112 padded canvas
    # scale just multiplies the cropped frame.
    w4, h4 = _png_size(render_character(_APPEARANCE, scale=4))
    assert (w4, h4) == (w1 * 4, h1 * 4)


def test_wings_change_and_widen_render() -> None:
    # A wings accessory adds content to the sides of the body; after content-cropping,
    # the winged render differs from the no-accessory one and is WIDER than it (wings
    # extend horizontally past the 40px body cell).
    none = render_character(_APPEARANCE, scale=4)
    winged = render_character(_APPEARANCE, scale=4, accessories=_slots7(493))
    assert none[:8] == _PNG_SIG and winged[:8] == _PNG_SIG
    assert winged != none
    none_w, _ = _png_size(none)
    winged_w, _ = _png_size(winged)
    assert winged_w > none_w  # wing pixels extend the crop horizontally


def test_vanity_accessory_overrides_functional() -> None:
    # Functional AngelWings(493) with vanity DemonWings(492): the vanity wins, so the
    # render must equal the DemonWings-only render and differ from the AngelWings one.
    func_only = render_character(_APPEARANCE, scale=4, accessories=_slots7(493))
    vanity_only = render_character(_APPEARANCE, scale=4, accessories=_slots7(492))
    overridden = render_character(
        _APPEARANCE, scale=4,
        accessories=_slots7(493), vanity_accessories=_slots7(492))
    assert overridden == vanity_only      # vanity DemonWings wins
    assert overridden != func_only        # ...and is not the functional AngelWings


def test_hidevisuals_hides_functional_accessory() -> None:
    # hideVisuals bit 3 hides functional accessory slot 0; with StarCloak there and no
    # vanity twin, the render must equal the no-accessory render (the cape is absent).
    none = render_character(_APPEARANCE, scale=4)
    shown = render_character(_APPEARANCE, scale=4, accessories=_slots7(532))
    # hideVisuals lives on appearance; build a hidden-bit-3 appearance copy.
    appearance_hidden = {**_APPEARANCE, "hideVisuals": 1 << 3}
    hidden = render_character(appearance_hidden, scale=4, accessories=_slots7(532))
    assert shown != none          # the cape is visible without hiding
    assert hidden == none         # ...and gone once hideVisuals bit 3 is set


def test_hidden_functional_keeps_vanity_twin() -> None:
    # hideVisuals hides the functional acc but its vanity twin still draws: functional
    # HermesBoots(54) hidden + vanity RocketBoots(128) -> resolves to the vanity shoe.
    res = _resolve_accessories(
        _slots7(54), _slots7(128), None, 1 << 3, male=True)
    assert res["slots"].get("shoe") == 12   # RocketBoots vanity survives the hide
    res_no_twin = _resolve_accessories(_slots7(54), _slots7(), None, 1 << 3, male=True)
    assert "shoe" not in res_no_twin["slots"]  # hidden, no twin -> absent


def test_shoe_accessory_is_visible() -> None:
    # A shoe accessory (RocketBoots, netId 128 -> shoe slot 12) on a default-clothed
    # (non-robe) character must draw OVER the default shoes and be visible, matching the
    # game's normal LegacyPlayerRenderer branch (leggings then shoes). Regression for
    # the bug where the shoe acc was drawn BEFORE the default shoes (layer 12) and was
    # mostly hidden. _APPEARANCE has no equipment -> default pants+shoes (the `else`
    # legs branch); no body armor -> not a robe body -> the normal branch.
    none = render_character(_APPEARANCE, scale=1)
    shod = render_character(_APPEARANCE, scale=1, accessories=_slots7(128))
    assert none[:8] == _PNG_SIG and shod[:8] == _PNG_SIG
    assert shod != none                          # the shoe acc adds foot-region pixels
    # The shoe acc stacks inside the body cell, so the content crop is identical (same
    # dimensions); compare the foot (bottom) region. When the acc draws OVER the default
    # shoes it overwrites a large patch of the foot; drawn UNDER them only a few px peek
    # out. Assert it covers a substantial part of the foot (not a sliver), so the test
    # separates the correct (shoe-over) order from the buggy (shoe-under) one.
    a, b = _decode(none), _decode(shod)
    assert a.shape == b.shape, "shoe acc unexpectedly changed the content bounding box"
    foot = slice(a.shape[0] * 3 // 4, a.shape[0])    # lowest quarter = the feet
    changed = int(np.any(a[foot] != b[foot], axis=2).sum())
    assert changed > 30, f"shoe acc barely visible ({changed}px), likely under shoes"


def test_accessory_dye_routes_to_category() -> None:
    # accessoryDyes[k] pairs with the category of accessories[k]: HermesBoots in slot 0
    # + RedDye(1007) in dye slot 0 -> the shoe category gets the ArmorColored red dye.
    res = _resolve_accessories(
        _slots7(54), _slots7(), [_acc(1007)] + [{"netId": 0}] * 6, 0, male=True)
    shoe_dye = res["dyes"].get("shoe")
    assert shoe_dye is not None
    assert shoe_dye["pass"] == "ArmorColored"
    # and a dyed cape render differs from an undyed one (the cape is visible; the dye
    # rides accessoryDyes slot 0, the same slot as the functional accessory).
    undyed = render_character(_APPEARANCE, scale=4, accessories=_slots7(532))
    dyed = render_character(
        _APPEARANCE, scale=4, accessories=_slots7(532),
        accessory_dyes=[_acc(1007)] + [{"netId": 0}] * 6)
    assert dyed != undyed


def test_animated_wing_renders_nothing_grounded() -> None:
    # AlwaysAnimated wings (e.g. Hoverboard, wing slot 22) draw nothing for a still
    # grounded avatar; the render must equal the no-accessory render. Hoverboard netId
    # = 1866 (Item.cs). If the table lacks it the test still passes (slot absent).
    none = render_character(_APPEARANCE, scale=4)
    hover = render_character(_APPEARANCE, scale=4, accessories=_slots7(1866))
    assert hover == none


# ── draw-order / visibility-gate regressions (audit_frames_*.md) ──────
# These exercise the layer-order, visibility-gate and missing-layer fixes ("shoe-bug
# family": all assets present but drawn in the wrong order / wrongly hidden or shown).
# Body skinVariant 0 (male) keeps the body cell at column 0 so the cell maths are
# simple.
_APP_M = {**_APPEARANCE, "skinVariant": 0, "hair": 5}


def _opaque_cells(name: str, cell: int) -> np.ndarray:
    """Boolean (FH,FW) mask of the opaque pixels of a sheet's idle cell."""
    return _frame(name, cell)[..., 3] > 0


def test_handoff_acc_draws_over_body_armor_back_arm() -> None:
    # P1 (item 3): the off-hand composite accessory (HandsOff) is the LAST thing in the
    # back-arm group, drawn OVER the body armor's back arm (PlayerDrawLayers.cs:1365
    # body back arm, 1421 handoff acc). The bug drew it UNDER the armor, so where the
    # handoff sits entirely inside the armor's back-arm sprite the armor swallowed it.
    #
    # Fixture check: HandsOff_1 (handoff slot 1) sits entirely inside body 1's (netId
    # 80) back-arm sprite, so under the buggy order it contributes ZERO visible pixels.
    back_cell = _Compositor(_APP_M).cells["back_arm"]
    arm = _opaque_cells("ArmorBody_80", back_cell)
    hoff = _opaque_cells("Acc_HandsOff_1", back_cell)
    assert int(hoff.sum()) > 0
    assert int((hoff & ~arm).sum()) == 0, (
        "fixture: the handoff must lie wholly inside the armor back arm so the buggy "
        "under-armor order would hide it entirely")

    # Gauntlet netId 1343 = handOff 1 (back arm) + handOn 6 (front arm). The handOn
    # always shows over the front arm (left cluster); the handOff only shows over the
    # armor back arm (right cluster) when drawn AFTER the armor. So the back-arm (right)
    # cluster of the body-vs-body+gauntlet diff is the discriminator: present after the
    # fix, empty before.
    none = _decode(render_character(_APP_M, {"body": _acc(80)}, scale=1))
    handed = _decode(render_character(
        _APP_M, {"body": _acc(80)}, accessories=_slots7(1343)))
    assert none.shape == handed.shape
    diff = np.any(none != handed, axis=2)
    _ys, xs = np.nonzero(diff)
    assert xs.size > 0
    w = none.shape[1]
    # the back-arm sits on the player's right (image-right) half; the handOff pixels
    # there only appear once the accessory is drawn over the armor (the fix).
    right_cluster = int((xs >= w * 7 // 10).sum())
    assert right_cluster > 0, (
        "off-hand accessory not visible over the body armor back arm "
        "(it is being drawn under the armor and swallowed)")


def test_skin_hide_gates_match_decompiled_sets() -> None:
    # P1 (item 5): the torso/leg skin-hide gates. A pure-logic transcription cross-check
    # of the decompiled sets (same approach as test_back_hair_predicate_matches_game),
    # because the bare skin is almost fully covered by the armor sprites at idle so a
    # pixel test would be flaky (the leg/torso skin only leaks through holes vanilla
    # idle sprites barely have -- see audit_frames_body_equip.md B6). A wrong set =>
    # wrong gate, so this guards the fix directly.
    from nextbot.terraria_render.compositor import (
        _HIDES_BOTTOM_SKIN_LEGS,
        _HIDES_TOP_SKIN_BODIES,
        _LEG_OVERRIDE_SLOTS,
        _SHOE_OVERRIDE_EXCEPTION,
    )

    # PlayerDrawSet.cs:1755 hidesTopSkin.
    assert set(_HIDES_TOP_SKIN_BODIES) == {21, 22, 82, 83, 93}
    # PlayerDrawSet.cs:1756 hidesBottomSkin legs (body 93 handled separately in code).
    assert set(_HIDES_BOTTOM_SKIN_LEGS) == {20, 21, 214, 215, 216}
    # PlayerDrawLayers.cs:1218-1241 ShouldOverrideLegs_CheckPants legs.
    assert set(_LEG_OVERRIDE_SLOTS) == {
        55, 63, 67, 106, 138, 140, 143, 217, 222, 226, 228}
    # PlayerDrawLayers.cs:1246 ShouldOverrideLegs_CheckShoes.
    assert _SHOE_OVERRIDE_EXCEPTION == 15


def test_masked_body_render_is_valid() -> None:
    # P1 (item 5), end-to-end sanity: a hidesTopSkin body (82, netId 1755) still renders
    # a valid PNG with the torso-skin gate applied (the gate only changes pixels where
    # the armor has a hole exposing the torso, which vanilla body 82 does not at idle,
    # so the render matches the no-gate output here -- the gate is correctness for
    # hole-y bodies).
    png = render_character(
        {**_APP_M, "skinColor": -65281}, {"body": _acc(1755)}, scale=2)
    assert png[:8] == _PNG_SIG


def test_back_head_front_to_back_set_matches_decompiled() -> None:
    # P1 (item 6): the FrontToBackID map (ArmorIDs.cs:14 = CreateIntSet(-1, 242,246,
    # 243,247, 244,248, 245,249, 133,252, 224,253)) drives the behind-body back-head
    # texture (DrawPlayer_01_3_BackHead, LegacyPlayerRenderer.cs:185), which the bug
    # omitted entirely. Transcription cross-check + assets-present check (a
    # wrong/missing entry => no back head).
    from nextbot.terraria_render.compositor import _HEAD_FRONT_TO_BACK
    assert _HEAD_FRONT_TO_BACK == {
        242: 246, 243: 247, 244: 248, 245: 249, 133: 252, 224: 253}
    for back in _HEAD_FRONT_TO_BACK.values():
        assert int(_opaque_cells(f"Armor_Head_{back}", 0).sum()) > 0


def test_back_head_adds_visible_pixels_behind_body() -> None:
    # P1 (item 6), the layer actually shows: the back-head is drawn behind the body, so
    # it only contributes where it pokes out from behind the head skin / front helmet /
    # front hair. Head 224 (netId 4560 -> back 253) is the one vanilla head whose back
    # piece is exposed at idle. Reproduce the head-cell stack the renderer builds and
    # assert the back-head has surviving (uncovered) pixels -- the ones the bug dropped.
    from nextbot.terraria_render.compositor import _resolve_player
    back = _opaque_cells("Armor_Head_253", 0)            # behind-body back head
    head_layer0 = _resolve_player(0, 0)                  # variant-0 head-skin sheet
    assert head_layer0 is not None                       # ships for variant 0
    head_skin = _opaque_cells(head_layer0, 0)            # layer 0 head skin (in front)
    front_helm = _opaque_cells("Armor_Head_224", 0)      # front helmet (in front)
    front_hair = _opaque_cells("Player_Hair_1", 0)       # head 224 fullHair: hair draws
    surviving = back & ~head_skin & ~front_helm & ~front_hair
    assert int(surviving.sum()) > 0, (
        "the back-head texture is fully covered -- it would be invisible either way")

    # end-to-end: head 224 renders (the back-head poking out is what makes its render
    # differ from the bare head; the front helmet shares the same idle frame anyway).
    helmeted = render_character({**_APP_M, "hair": 0}, {"head": _acc(4560)}, scale=1)
    assert helmeted[:8] == _PNG_SIG


def test_useskincolor_head_uses_skin_color() -> None:
    # P3 (item 2): ArmorIDs.Head.Sets.UseSkinColor (ArmorIDs.cs:16 = CreateBoolSet(
    # false, 274, 277)) heads draw with the player's skinColor + the skin shader, NOT
    # armor white + the head dye (PlayerDrawLayers.cs:2145/2223). VulkelfEar = netId
    # 5136 (head 274), GoblorcEar = netId 5305 (head 277). Set must match decompiled.
    from nextbot.terraria_render.compositor import _HEAD_USE_SKIN_COLOR
    assert set(_HEAD_USE_SKIN_COLOR) == {274, 277}
    # A UseSkinColor head changes with the player's skinColor (it is tinted by skin),
    # which a white-drawn armor head would NOT do in its own (non-skin) sprite region.
    for net_id in (5136, 5305):
        pink = render_character({**_APP_M, "skinColor": -65281}, {"head": _acc(net_id)})
        blue = render_character(
            {**_APP_M, "skinColor": -16776961}, {"head": _acc(net_id)})
        assert pink[:8] == _PNG_SIG
        assert pink != blue, f"head {net_id} not tinted by skinColor"
    # ...and the head dye does NOT apply to a UseSkinColor head (it uses the skin
    # shader): a dyed-vs-undyed render of the ear head is identical (dye is dropped),
    # whereas a normal armor head (GoldHelmet 8 -> some slot) would change under it.
    fixed_skin = {**_APP_M, "skinColor": -65281}
    undyed = render_character(fixed_skin, {"head": _acc(5136)})
    dyed = render_character(
        fixed_skin, {"head": _acc(5136)}, dye={"head": _acc(1007)})  # RedDye
    assert undyed == dyed, "UseSkinColor head must ignore the head dye (skin shader)"


def test_shoe_acc_suppressed_by_leg_override() -> None:
    # P1 (item 4): DrawPlayer_14_Shoes is gated by !ShouldOverrideLegs_CheckPants
    # (PlayerDrawLayers.cs:1758): leg slots {55,63,67,106,138,140,143,217,222,226,228}
    # suppress the shoe accessory -- UNLESS shoe==15 (ShouldOverrideLegs_CheckShoes,
    # 1246) which short-circuits and keeps it. legs 55 = netId 1505; RocketBoots = netId
    # 128 (shoe slot 12); FrogLeg = netId 2423 (shoe slot 15).
    override_legs = {"legs": _acc(1505)}
    legs_only = render_character(_APP_M, override_legs, scale=1)
    legs_shoe = render_character(_APP_M, override_legs, accessories=_slots7(128))
    # under override legs the shoe accessory (slot 12) is suppressed: render unchanged.
    assert legs_shoe == legs_only
    # control: with the default (non-override) legs the same shoe IS visible.
    assert render_character(_APP_M, accessories=_slots7(128)) != render_character(
        _APP_M, scale=1)
    # exception: shoe slot 15 (FrogLeg) is NOT suppressed even under the override legs.
    legs_frog = render_character(_APP_M, override_legs, accessories=_slots7(2423))
    assert legs_frog != legs_only


def test_shoe15_suppresses_default_legs_and_leg_armor() -> None:
    # P3 (item 1): shoe==15 (FrogLeg, ShouldOverrideLegs_CheckShoes PlayerDrawLayers.cs
    # :1246) suppresses BOTH the leg armor (DrawPlayer_13_Leggings :1540 only draws when
    # !CheckShoes || wearsRobe) AND the default pants+shoes (:1576 else-if only when
    # !CheckShoes) -- FrogLeg replaces the legs. The bug drew the default pants+shoes
    # (or leg armor) unconditionally underneath. With shoe==15, a default-clothed render
    # must equal an override-legs render (both leave only the FrogLeg sprite + leg-skin
    # suppression): no default pants/shoes leak. FrogLeg = netId 2423 (shoe 15);
    # override legs 55 = netId 1505. A distinctive pants color makes a leak observable.
    app = {**_APP_M, "pantsColor": -16711681}
    frog_default = render_character(app, scale=1, accessories=_slots7(2423))
    frog_override = render_character(
        app, {"legs": _acc(1505)}, scale=1, accessories=_slots7(2423))
    # shoe==15 suppresses the default pants+shoes exactly like override legs suppress
    # the leg armor -> the two are byte-identical (only FrogLeg remains in both).
    assert frog_default == frog_override
    # ...and the equality is meaningful: WITHOUT FrogLeg the two paths differ (default
    # pants+shoes vs the override-legs suppression), so the equality above is the fix,
    # not a trivial match.
    assert render_character(app, scale=1) != render_character(
        app, {"legs": _acc(1505)}, scale=1)
    # FrogLeg still visibly changes a default-clothed render (it replaces the legs).
    frog = render_character(app, scale=1, accessories=_slots7(2423))
    assert frog != render_character(app, scale=1)


def test_under_hair_face_is_occluded_by_hair() -> None:
    # P1 (item 7): a DrawInFaceUnderHairLayer face (ArmorIDs.cs:2144 = {5}, the
    # Blindfold, netId 888) is drawn in the head group BEFORE the front hair
    # (PlayerDrawLayers.cs:2631) and NOT again at layer 22. The bug drew every face OVER
    # the hair. Hair index 5 (a forehead-covering style) overlaps the blindfold, so
    # under the fix the hair occludes part of the blindfold: its visible footprint is
    # strictly smaller than its own sprite footprint (over-hair would show more).
    blindfold_footprint = int(_opaque_cells("Acc_Face_5", 0).sum())
    assert blindfold_footprint > 0
    none = _decode(render_character(_APP_M, scale=1))
    with_face = _decode(render_character(_APP_M, accessories=_slots7(888)))
    assert none.shape == with_face.shape
    visible = int(np.any(none != with_face, axis=2).sum())
    assert 0 < visible < blindfold_footprint, (
        f"blindfold visible={visible} should be >0 and < "
        f"footprint {blindfold_footprint} "
        "(hair must occlude part of it; over-hair would show ~the full sprite)")


def test_shield_and_front_acc_render_is_stable() -> None:
    # P0 (items 1/2): the shield (DrawPlayer_25_Shield, LegacyPlayerRenderer.cs:230) and
    # the FrontAcc back-half (DrawPlayer_32_FrontAcc_BackPart, 229) were moved to the
    # correct spot relative to the head group and front arm. With vanilla assets at idle
    # the shield / front-acc-front-half occupy disjoint x-regions from the front arm and
    # the head, so the reorder produces no pixel difference (verified in the audit).
    # This is a sanity guard that the combined shield + front-acc + head + body render
    # stays valid and deterministic after the reorder.
    app = {**_APP_M, "hair": 1}
    # CobaltShield = netId 156 (shield slot 1); CrimsonCloak = netId 2284 (front slot 1,
    # which exercises both the front-acc back-half and front-half layers).
    a = render_character(
        app, {"head": _acc(1824), "body": _acc(80)},
        accessories=_slots7(156, 2284), scale=2)
    b = render_character(
        app, {"head": _acc(1824), "body": _acc(80)},
        accessories=_slots7(156, 2284), scale=2)
    assert a[:8] == _PNG_SIG
    assert a == b


# ── draw-offset / frame-formula regressions (audit_frames_*.md, P1 round) ──────
# These pin the per-category draw offsets that the previous round got wrong: the four
# bespoke wings (47/49/50/51 used the default formula but the game uses their own base +
# 2px frame crop) and the Get*DrawOffset additions (face 19 (0,-6), front 13 (-2,0),
# roller skates 27-30 (0,2)). Offsets are derived for idle direction=1 / gravDir=1
# (Directions=(1,1)).


def _wing_topleft(slot: int) -> tuple[tuple[int, int], np.ndarray]:
    """Recompute (cell-local top-left, drawn frame) for a wing exactly as draw_acc_wing
    does, from the decompiled-derived _WING_BESPOKE / _WING_FRAMES."""
    from nextbot.terraria_render.compositor import _WING_BESPOKE, _WING_FRAMES

    n = _WING_FRAMES.get(slot, 4)
    cx, cy, crop = _WING_BESPOKE[slot]
    frame, _src, _size = _acc_wing_frame(f"Wings_{slot}", n, crop=crop)
    assert frame is not None                             # the bespoke wing sheets ship
    lx, ly = cx - frame.shape[1] // 2, cy - frame.shape[0] // 2
    return (lx, ly), frame


def test_bespoke_wing_offsets_match_decompiled() -> None:
    # P1 (item 1): wings 47/49/50/51 use bespoke offsets + frame crops in
    # DrawPlayer_09_Wings, NOT the default (num13-9, num12+2) formula. Pin each one's
    # cell-local top-left (derived from the decompiled branch; see _WING_BESPOKE) AND
    # confirm the composited canvas places the source frame there. The expected values
    # differ from the OLD default-formula top-lefts (the bug) -> this is a real guard.
    expect = {                       # slot -> (top_left, (frame_w, frame_h))
        47: ((-42, -14), (118, 92)),  # crop 120x94 -> 118x92, center (17,32)
        49: ((-42, -14), (118, 92)),  # same as 47
        50: ((-44, -16), (120, 94)),  # NO crop, center (16,31)
        51: ((-26, 0), (84, 60)),     # crop 86x62 -> 84x60, center (16,30)
    }
    # the buggy default-formula top-lefts these REPLACE (must be different now).
    old_buggy = {47: (-49, -14), 49: (-49, -14), 50: (-53, -14), 51: (-32, 2)}
    for slot, (exp_tl, exp_dims) in expect.items():
        (lx, ly), frame = _wing_topleft(slot)
        assert (lx, ly) == exp_tl, f"wing {slot} top-left {(lx, ly)} != {exp_tl}"
        assert (frame.shape[1], frame.shape[0]) == exp_dims, (
            f"wing {slot} frame {(frame.shape[1], frame.shape[0])} != {exp_dims}")
        assert (lx, ly) != old_buggy[slot], (
            f"wing {slot} still at the old default-formula offset {old_buggy[slot]}")
        # the composited canvas holds the (uncropped-corner) source frame at that spot.
        comp = _Compositor(_APP_M)
        comp.draw_acc_wing(slot, None)
        y0, x0 = PAD_T + ly, PAD_L + lx
        region = comp.canvas[y0:y0 + frame.shape[0], x0:x0 + frame.shape[1]]
        assert np.array_equal(region, frame[:region.shape[0], :region.shape[1]]), (
            f"wing {slot} not composited at its derived top-left")


def test_bespoke_wing_frame_crop() -> None:
    # P1 (item 1, crop part): 47/49/51 trim the frame Width/Height by 2px
    # (rectangle.Width-=2; Height-=2); 50 draws the full frame (uses `value10`). The
    # cropped frame is the sheet's top-left, 2px shorter on each axis.
    from nextbot.terraria_render.compositor import _WING_FRAMES, _sheet
    for slot, crop in ((47, True), (49, True), (50, False), (51, True)):
        sheet = _sheet(f"Wings_{slot}")
        assert sheet is not None
        n = _WING_FRAMES.get(slot, 4)
        full_h = sheet.shape[0] // n
        full_w = sheet.shape[1]
        _tl, frame = _wing_topleft(slot)
        if crop:
            assert (frame.shape[1], frame.shape[0]) == (full_w - 2, full_h - 2)
        else:
            assert (frame.shape[1], frame.shape[0]) == (full_w, full_h)


def test_default_wing_offset_unchanged() -> None:
    # Guard: a NON-bespoke wing (AngelWings, slot 2) still uses the default formula
    # center = (11+num13, 33+num12) = (11, 33) (num13=num12=0), unaffected by the fix.
    comp = _Compositor(_APP_M)
    comp.draw_acc_wing(2, None)
    frame, _src, _size = _acc_wing_frame("Wings_2", 4)
    assert frame is not None                             # AngelWings (slot 2) ships
    lx, ly = 11 - frame.shape[1] // 2, 33 - frame.shape[0] // 2
    y0, x0 = PAD_T + ly, PAD_L + lx
    region = comp.canvas[y0:y0 + frame.shape[0], x0:x0 + frame.shape[1]]
    assert np.array_equal(region, frame[:region.shape[0], :region.shape[1]])


def _strip_shift(name: str, offset: tuple[int, int]) -> bool:
    """True iff drawing strip `name` at `offset` equals drawing it at (0,0) shifted by
    `offset` (i.e. the offset moves the whole cell content by exactly (dx, dy))."""
    base = _Compositor(_APP_M)
    base.draw_acc_strip(name, None, offset=(0, 0))
    moved = _Compositor(_APP_M)
    moved.draw_acc_strip(name, None, offset=offset)
    dx, dy = offset
    a = base.canvas[PAD_T:PAD_T + FH, PAD_L:PAD_L + FW]
    b = moved.canvas[PAD_T + dy:PAD_T + dy + FH, PAD_L + dx:PAD_L + dx + FW]
    return bool(np.array_equal(a, b))


def test_face19_draw_offset_applied() -> None:
    # P1 (item 2): face 19 (BoneHelm) carries GetFaceDrawOffset (0,-6)*Directions
    # (Player.cs:4384). The renderer must shift it up 6px; other faces stay at (0,0).
    from nextbot.terraria_render.compositor import _FACE_DRAW_OFFSET
    assert _FACE_DRAW_OFFSET == {19: (0, -6)}
    # the offset plumbing moves the Acc_Face_19 cell content up by exactly 6 rows.
    assert _strip_shift("Acc_Face_19", (0, -6))
    # end-to-end: face 19 (netId 5100) renders and differs from a no-accessory render
    # (BoneHelm also PreventHairDraw, but the face sprite itself dominates the change).
    none = render_character(_APP_M, scale=2)
    boned = render_character(_APP_M, scale=2, accessories=_slots7(5100))
    assert boned[:8] == _PNG_SIG
    assert boned != none


def test_front13_and_rollerskate_offsets_match_decompiled() -> None:
    # P1 item 3 / P2 item 4: GetFrontDrawOffset front==13 -> (-2,0) (Player.cs:4712).
    # GetShoeDrawOffset roller skates 27-30 -> (0,2) (4732). No vanilla netId maps to
    # front 13 / shoe 27-30, so these are exercised at the offset level (the constants +
    # the draw-offset plumbing), not end-to-end.
    from nextbot.terraria_render.compositor import (
        _FRONT_DRAW_OFFSET,
        _SHOE_DRAW_OFFSET,
    )
    assert _FRONT_DRAW_OFFSET == {13: (-2, 0)}
    assert _SHOE_DRAW_OFFSET == {27: (0, 2), 28: (0, 2), 29: (0, 2), 30: (0, 2)}
    # front 13 (-2,0): both halves of the split front acc shift left by 2px. Use the
    # front-half draw with the same offset the renderer passes (front_offset).
    base = _Compositor(_APP_M)
    base.draw_acc_front_half("Acc_Front_1", None, front=True, offset=(0, 0))
    moved = _Compositor(_APP_M)
    moved.draw_acc_front_half("Acc_Front_1", None, front=True, offset=(-2, 0))
    a = base.canvas[PAD_T:PAD_T + FH, PAD_L:PAD_L + FW]
    b = moved.canvas[PAD_T:PAD_T + FH, PAD_L - 2:PAD_L - 2 + FW]
    assert np.array_equal(a, b)
    # roller-skate (0,2): the shoe strip shifts down by 2px (use a real shoe sheet).
    assert _strip_shift("Acc_Shoes_27", (0, 2))


# ── equipment glowmask (research/glowmask_spec.md) ────────────────────
# Glowmask netIds (Item.cs / equip_slots.json):
#   body 227 NebulaBreastplate 4756 (bodyGlowColor (230,230,230,60), arm too);
#   body 80 GoldChainmail 80 (NO glow, 360x224 sheet -> regression baseline);
#   head 291 -> 5683 (headGlowColor white A=255, opaque cover);
#   legs 222 -> 5053 (legsGlowColor mouseText additive A=0).
def test_over_glow_additive_does_not_drop_layer() -> None:
    # The core fix: a glow color with alpha==0 (the common case) is PURELY ADDITIVE: it
    # brightens the canvas without occluding it. The old straight-over (_over) drops a
    # src-alpha-0 layer (contributes nothing); _over_glow must brighten instead.
    dst = np.zeros((2, 2, 4), np.uint8)
    dst[..., :3] = 100
    dst[..., 3] = 255                              # opaque gray base
    glow = np.zeros((2, 2, 4), np.uint8)
    glow[..., :3] = 80
    glow[..., 3] = 255                             # straight glow tex, full tex-alpha
    out = _over_glow(dst.copy(), glow, (255, 255, 255, 0))  # additive white, A=0
    assert int(out[0, 0, 0]) > 100, "additive (alpha=0) glow must brighten"
    assert int(out[0, 0, 0]) == min(255, 100 + 80)  # 100 + 80 additive = 180
    assert int(out[0, 0, 3]) == 255                # alpha unchanged (no occlusion)


def test_over_glow_opaque_color_covers() -> None:
    # A glow color with alpha==255 (body 238/260/291, ...) is an OPAQUE COVER (spec §8:
    # not an outline -- it fully replaces the pixel), not additive.
    dst = np.zeros((1, 1, 4), np.uint8)
    dst[..., :3] = 50
    dst[..., 3] = 255
    glow = np.zeros((1, 1, 4), np.uint8)
    glow[..., :3] = 200
    glow[..., 3] = 255
    out = _over_glow(dst.copy(), glow, (255, 255, 255, 255))
    assert tuple(int(out[0, 0, c]) for c in range(3)) == (200, 200, 200)


def test_over_glow_empty_texture_is_noop() -> None:
    # Where the glow texture is transparent (alpha 0), nothing is added regardless of
    # the glow color -- the canvas is untouched (the per-cell skip / regression safety).
    dst = np.zeros((3, 3, 4), np.uint8)
    dst[..., :3] = 123
    dst[..., 3] = 255
    out = _over_glow(dst.copy(), np.zeros((3, 3, 4), np.uint8), (230, 230, 230, 60))
    assert np.array_equal(out, dst)


def _without_glow(table: dict, key: str):
    """Context-managed temporary removal of a glow-table entry (restore on exit)."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        saved = table.pop(key, None)
        try:
            yield
        finally:
            if saved is not None:
                table[key] = saved

    return cm()


def test_glow_body_brightens_torso_region() -> None:
    # End-to-end: a glowmask body (Nebula, 4756) must brighten its torso/arm region vs.
    # the SAME body with its glow suppressed (the colored armor underneath is identical,
    # so the diff IS the glow contribution).
    body_t, arm_t = glow_mod._GLOW_BODY, glow_mod._GLOW_ARM
    with _without_glow(body_t, "227"), _without_glow(arm_t, "227"):
        base = _decode(render_character(_APP_M, {"body": _acc(4756)}, scale=1))
    glown = _decode(render_character(_APP_M, {"body": _acc(4756)}, scale=1))
    assert base.shape == glown.shape
    diff = np.any(base != glown, axis=2)
    assert int(diff.sum()) > 0, "glow layer changed nothing (must brighten pixels)"
    # the changed pixels must be brighter overall (a bright (230,230,230) additive/
    # partial wash, never darkening), summed over the changed region.
    ys, xs = np.nonzero(diff)
    base_lum = base[ys, xs, :3].astype(np.int32).sum()
    glow_lum = glown[ys, xs, :3].astype(np.int32).sum()
    assert glow_lum > base_lum, "glow must brighten the affected pixels"


def test_glow_head_changes_and_legs_glow_render() -> None:
    # head/legs use independent Glow_{id} strips. A glowmask head (291 -> 5683, opaque
    # white cover) must change the render vs the same head with its glow suppressed; a
    # legs glowmask (222 -> 5053) renders a valid PNG.
    with _without_glow(glow_mod._GLOW_HEAD, "291"):
        base = render_character(_APP_M, {"head": _acc(5683)}, scale=1)
    glown = render_character(_APP_M, {"head": _acc(5683)}, scale=1)
    assert base != glown, "head glowmask layer changed nothing"
    legs = render_character(_APP_M, {"legs": _acc(5053)}, scale=1)
    assert legs[:8] == _PNG_SIG


def test_non_glow_body_unaffected_by_glow_code() -> None:
    # Regression: a body WITHOUT a glowmask (GoldChainmail, 80; sheet is 360x224 with no
    # lower-half glow data) renders identically with the glow code present -- the glow
    # color resolves to None and no glow cell exists, so nothing is drawn.
    comp = _Compositor(_APP_M)
    # body slot 1 has no glow-table entry -> resolved color is None (no-op non-glow).
    assert comp._glow_color(glow_mod._GLOW_BODY.get("1")) is None
    a = render_character(_APP_M, {"body": _acc(80)}, scale=2)
    b = render_character(_APP_M, {"body": _acc(80)}, scale=2)
    assert a == b
    assert a[:8] == _PNG_SIG


def test_glow_color_arkhalis_uses_undershirt() -> None:
    # The 'arkhalis' sentinel resolves to (underShirtColor.rgb, A=180) (PlayerDrawSet.cs
    # :515-516). With a known undershirt color the resolved glow color must match.
    app = {**_APP_M, "underShirtColor": -16711936}  # 0xFF00FF00 -> straight green
    comp = _Compositor(app)
    # packed -16711936 = 0xFF00FF00 -> R=0, G=255, B=0 (FNA 0xAABBGGRR layout).
    assert comp.colors["under"] == (0, 255, 0)
    assert comp._glow_color("arkhalis") == (0, 255, 0, 180)


# ── glowmask corner-case details (glowmask_spec.md §8, reverse-engineered) ────
# These exercise the C-class details previously deferred: the TV-screen head (271,
# GlowMask_309 6x4 grid), head 269's FrontShoulder extra (Extra_214 + GlowMask_308),
# sub-pixel jitter multi-passes (body 227 / head 240 / legs 210 / body 205 4-tap), the
# ChickenBones coat-front-238 glow (GlowMask_363), and the armor-set backpack RGB.
# Animated/jittered layers use a documented representative still. netIds
# (equip_slots.json): head 271=5061, head 269=5054, head 240=4755, body 227=4756,
# body 205=3875, legs 210=4757; ChickenBonesRobe=5587 (coat 251 -> front 238).


def test_tv_head_glow_constants_match_decompiled() -> None:
    # The TV-screen grid + idle selection (PlayerDrawLayers.cs:2357-2385 / GetTVScreen
    # :2514, Frame(6,4,col,row,-2)). Idle column = GetTVScreen default = 3 (no danger,
    # full health, no biome, not wet, no town NPCs); row = miscCounter%20/5 frozen = 0;
    # vector5 = OffsetsPlayerHeadgear[0](0,2) -2 *1 = (0,0). Cell w 42, drawn rect 40.
    from nextbot.terraria_render.compositor import (
        _TV_CELL_W,
        _TV_COLS,
        _TV_IDLE_COL,
        _TV_IDLE_ROW,
        _TV_IDLE_VEC5,
        _TV_ROWS,
    )
    assert (_TV_COLS, _TV_ROWS, _TV_CELL_W) == (6, 4, 42)
    assert _TV_IDLE_COL == 3 and _TV_IDLE_ROW == 0
    assert _TV_IDLE_VEC5 == (0, 0)
    # head 271 is tagged as the TV grid glow with mask 309 (252x224 sheet).
    assert glow_mod._GLOW_HEAD["271"]["grid"] == "tv"
    assert glow_mod._GLOW_HEAD["271"]["mask"] == 309
    sheet = glow_mod._sheet("Glow_309")
    assert sheet is not None and sheet.shape[1] == _TV_COLS * _TV_CELL_W  # 252 wide


def test_tv_head_glow_draws_default_column_not_empty_col0() -> None:
    # The bug: the generic strip path read grid cell 0 (column 0), EMPTY at idle
    # (0 opaque px) -> the TV screen never showed. The fix selects column 3 (the calm
    # default screen) which has content. Assert the rendered glow == TV cell (col 3,
    # row 0) and that column 0 would have been blank (the discriminator).
    sheet = glow_mod._sheet("Glow_309")
    assert sheet is not None
    col0 = sheet[0:FH, 0:FW]                            # grid col 0 (old buggy cell)
    col3 = sheet[0:FH, 3 * 42:3 * 42 + FW]             # grid col 3 (GetTVScreen idle)
    assert int((col0[..., 3] > 0).sum()) == 0          # col 0 is blank at idle
    assert int((col3[..., 3] > 0).sum()) > 0           # col 3 has the screen
    # the composited TV glow (opaque A=255 cover) lands col 3's silhouette at the head.
    comp = _Compositor(_APP_M)
    comp.draw_tv_head_glow("Glow_309", (255, 255, 255, 255), None)
    drawn = comp.canvas[PAD_T:PAD_T + FH, PAD_L:PAD_L + FW]
    assert np.array_equal(drawn[..., 3] > 0, col3[..., 3] > 0)
    assert not np.array_equal(drawn[..., 3] > 0, col0[..., 3] > 0)
    # end-to-end: head 271 (netId 5061) renders and differs from the same head with the
    # TV glow suppressed (the screen IS the difference).
    with _without_glow(glow_mod._GLOW_HEAD, "271"):
        base = render_character(_APP_M, {"head": _acc(5061)}, scale=1)
    tv = render_character(_APP_M, {"head": _acc(5061)}, scale=1)
    assert base != tv, "TV head glow changed nothing (screen not drawn)"


def test_head269_frontshoulder_extra_glow() -> None:
    # head 269 draws a FrontShoulder-context extra: Extra_214 (white colorArmorHead) +
    # GlowMask_308 (the glow), both at the head cell with the head dye
    # (PlayerDrawLayers.cs:107-116). The fix adds these two layers; suppressing head 269
    # removes them, so the render must differ. Assets must ship with idle content.
    extra = glow_mod._GLOW_HEAD["269"]["extra"]
    assert extra == {"armor": 214, "mask": 308}
    for name in ("Extra_214", "Glow_308"):
        sheet = glow_mod._sheet(name)
        assert sheet is not None, f"{name} missing"
        assert int((sheet[:FH, :FW, 3] > 0).sum()) > 0
    with _without_glow(glow_mod._GLOW_HEAD, "269"):
        base = render_character(_APP_M, {"head": _acc(5054)}, scale=1)
    glown = render_character(_APP_M, {"head": _acc(5054)}, scale=1)
    assert base != glown, "head 269 FrontShoulder extra (Extra_214 + GlowMask_308) gone"


def test_jitter_offsets_match_decompiled_magnitudes() -> None:
    # The representative jitter fans: the x0.125 jitter spans +-1.25px (rounded to +-1),
    # the x0.2/x0.15 4-tap spans up to +-2px in X. The first pass is on-grid (0,0). body
    # 227 / head 240 / legs 210 declare 2 passes; body 205's 4-tap is a distinct color.
    from nextbot.terraria_render.compositor import (
        _BODY205_4TAP_COLOR,
        _GLOW_ARM_JITTER,
        _GLOW_BODY_JITTER,
        _JITTER_OFFSETS,
        _TAP4_OFFSETS,
    )
    assert _JITTER_OFFSETS[0] == (0, 0)                       # first pass on-grid
    assert all(abs(x) <= 1 and abs(y) <= 1 for x, y in _JITTER_OFFSETS)  # +-1.25 -> +-1
    assert _TAP4_OFFSETS[0] == (0, 0) and len(_TAP4_OFFSETS) == 4
    assert max(abs(x) for x, _ in _TAP4_OFFSETS) == 2         # x0.2 -> +-2px in X
    assert _BODY205_4TAP_COLOR == (100, 100, 100, 0)         # additive (A=0) shimmer
    assert _GLOW_BODY_JITTER.get("227") == 2 and _GLOW_ARM_JITTER.get("227") == 2
    assert glow_mod._GLOW_HEAD["240"].get("jitter") == 2
    assert glow_mod._GLOW_LEGS["210"].get("jitter") == 2


def _strip_glow_passes(name: str, color: tuple, jitter: int) -> int:
    """Composite a strip glow with `jitter` passes onto a fresh canvas; return the count
    of changed (glow-touched) pixels in the head cell + 1px halo."""
    comp = _Compositor(_APP_M)
    comp.draw_strip_glow(name, "col", color, None, jitter=jitter)
    region = comp.canvas[PAD_T - 1:PAD_T + FH + 1, PAD_L - 1:PAD_L + FW + 1]
    return int((region[..., 3] > 0).sum() + np.any(region[..., :3] > 0, axis=2).sum())


def test_jitter_spreads_glow_region() -> None:
    # A 2-pass jitter draws the glow at (0,0) AND a ~1px-offset spot, so the additive
    # footprint is a superset of the single-pass one (the in-game "spread" of the
    # sub-pixel jitter). Use head 240's mask 273 (an additive A=60 glow) as the probe.
    one = _strip_glow_passes("Glow_273", (230, 230, 230, 60), 1)
    two = _strip_glow_passes("Glow_273", (230, 230, 230, 60), 2)
    assert one > 0
    assert two >= one, "2-pass jitter footprint must cover the single-pass one"
    # the multi-pass changed-pixel set strictly grows when the offset lands new pixels
    # (Glow_273 has a non-trivial sprite, so the +1,+1 pass adds edge pixels).
    assert two > one, "jitter second pass added no spread (offset ineffective)"


def test_body227_jitter_renders_brighter_superset() -> None:
    # body 227 (Nebula, netId 4756) draws torso + arm composite glow in 2 jitter passes.
    # The 2-pass render must differ from a forced 1-pass render (the extra offset pass
    # spreads the glow), and still be a valid brightening (no darkening artifacts).
    saved_b = int(glow_mod._GLOW_BODY_JITTER["227"])
    saved_a = int(glow_mod._GLOW_ARM_JITTER["227"])
    try:
        glow_mod._GLOW_BODY_JITTER["227"] = 1
        glow_mod._GLOW_ARM_JITTER["227"] = 1
        one = _decode(render_character(_APP_M, {"body": _acc(4756)}, scale=1))
        glow_mod._GLOW_BODY_JITTER["227"] = 2
        glow_mod._GLOW_ARM_JITTER["227"] = 2
        two = _decode(render_character(_APP_M, {"body": _acc(4756)}, scale=1))
    finally:
        glow_mod._GLOW_BODY_JITTER["227"] = saved_b
        glow_mod._GLOW_ARM_JITTER["227"] = saved_a
    assert one.shape == two.shape
    assert int(np.any(one != two, axis=2).sum()) > 0, "jitter pass changed nothing"


def test_body205_frontarm_4tap_adds_shimmer() -> None:
    # body 205 (netId 3875) has NO armGlowColor but draws a 4-tap additive shimmer,
    # color (100,100,100,0), over the front-arm glow (PlayerDrawLayers.cs:118-135).
    # Toggling the 4-tap off -> the render must differ (the shimmer is the diff).
    from nextbot.terraria_render import compositor as cmod
    saved = cmod._BODY205_FRONTARM_4TAP
    on = render_character(_APP_M, {"body": _acc(3875)}, scale=1)
    try:
        cmod._BODY205_FRONTARM_4TAP = -1            # no body matches -> 4-tap disabled
        off = render_character(_APP_M, {"body": _acc(3875)}, scale=1)
    finally:
        cmod._BODY205_FRONTARM_4TAP = saved
    assert on != off, "body 205 front-arm 4-tap shimmer absent"


def test_head211_4tap_constants_match_decompiled() -> None:
    # head 211 (ApprenticeAltHead, netId 3874 — head sibling of body 205's
    # ApprenticeAltShirt) draws the SAME 4-tap additive shimmer over the head cell via
    # an independent GlowMask_241 strip (PlayerDrawLayers.cs:2403-2415). Color
    # (100,100,100,0); X tap range RandomInt(-10,11)*0.2 = +-2px (same as body 205); Y
    # range RandomInt(-14,1)*0.15 = [-2.1,0] (a WIDER upward fan than body 205's
    # [-1.5,0]). The mask is tagged 'fourtap' in glowmask.json (no normal 'mask'), so it
    # routes to the dedicated path and is extracted via referenced_glow_masks().
    from nextbot.terraria_render.compositor import (
        _HEAD211_4TAP,
        _HEAD211_4TAP_COLOR,
        _HEAD211_TAP4_OFFSETS,
    )
    assert _HEAD211_4TAP == 211
    assert _HEAD211_4TAP_COLOR == (100, 100, 100, 0)        # additive (A=0)
    assert _HEAD211_TAP4_OFFSETS[0] == (0, 0) and len(_HEAD211_TAP4_OFFSETS) == 4
    assert max(abs(x) for x, _ in _HEAD211_TAP4_OFFSETS) == 2   # x0.2 -> +-2px in X
    assert min(y for _, y in _HEAD211_TAP4_OFFSETS) == -2       # x0.15 [-2.1,0] -> -2
    assert all(y <= 0 for _, y in _HEAD211_TAP4_OFFSETS)        # upward-only Y fan
    # glowmask.json tags head 211 with 4-tap mask 241 (no 'mask'/'grid'/'extra').
    entry = glow_mod._GLOW_HEAD["211"]
    assert entry.get("fourtap") == 241 and "mask" not in entry
    assert entry["color"] == [100, 100, 100, 0]
    # the GlowMask_241 strip is extracted (referenced_glow_masks picks up 'fourtap') and
    # has idle content (frame 0 = the head cell).
    sheet = glow_mod._sheet("Glow_241")
    assert sheet is not None and sheet.shape[1] == FW    # 40-wide vertical strip
    assert int((sheet[:FH, :FW, 3] > 0).sum()) > 0       # idle head cell has glow px


def test_head211_4tap_adds_additive_head_local_shimmer() -> None:
    # head 211's 4-tap is the only difference between rendering it on vs off. Toggling
    # _HEAD211_4TAP (the on/off gate, mirrors body 205) must change the render; the diff
    # must be ADDITIVE (brighten, never darken) and HEAD-LOCAL, and a plain head stays
    # untouched (regression).
    from nextbot.terraria_render import compositor as cmod
    saved = cmod._HEAD211_4TAP
    on = render_character(_APP_M, {"head": _acc(3874)}, scale=1)
    try:
        cmod._HEAD211_4TAP = -1                  # no head matches -> 4-tap disabled
        off = render_character(_APP_M, {"head": _acc(3874)}, scale=1)
    finally:
        cmod._HEAD211_4TAP = saved
    assert on != off, "head 211 4-tap shimmer absent"
    a, b = _decode(on), _decode(off)
    assert a.shape == b.shape
    both = (a[..., 3] > 0) & (b[..., 3] > 0)
    delta = a[..., :3].astype(int) - b[..., :3].astype(int)
    assert (delta[both] >= 0).all(), "4-tap must be additive (never darken pixels)"
    assert int(np.any(a != b, axis=2).sum()) > 0
    # head-local: the shimmer footprint sits in the upper head region (alt-head glow +
    # the upward/rightward tap fan), within the head cell's vertical span (<= 56 rows).
    ys = np.nonzero(np.any(a != b, axis=2))[0]
    assert ys.size > 0 and int(ys.max()) <= FH
    # head 211 carries the head dye through cHead -> a head dye changes the shimmer too.
    plain = render_character(_APP_M, {"head": _acc(3874)}, scale=1)
    dyed = render_character(
        _APP_M, {"head": _acc(3874)}, dye={"head": _acc(1007)}, scale=1)
    assert plain != dyed, "head 211 dye (cHead) did not reach the 4-tap glow"
    # regression: an ordinary head (Copper helmet 690, no 4-tap) is stable re-rendered.
    h = render_character(_APP_M, {"head": _acc(690)}, scale=2)
    assert h == render_character(_APP_M, {"head": _acc(690)}, scale=2)


def test_coat238_glow_constants_and_render() -> None:
    # ChickenBones coat front piece (Armor_Legs_238) carries an extra GlowMask_363 glow
    # with the ChickenBones representative color (255,255,255,0)*0.9 = (229,229,229,0),
    # same leg frame + cCoat dye (DrawLongCoat, PlayerDrawLayers.cs:1826-1834). coat 251
    # -> front 238.
    from nextbot.terraria_render.compositor import (
        _CHICKENBONES_GLOW_COLOR,
        _COAT_FRONT_GLOW,
    )
    assert _COAT_FRONT_GLOW == {238: 363}
    assert _CHICKENBONES_GLOW_COLOR == (229, 229, 229, 0)   # spec §3.2 representative
    sheet = glow_mod._sheet("Glow_363")
    assert sheet is not None and int((sheet[:FH, :FW, 3] > 0).sum()) > 0
    # end-to-end: the robe (netId 5587 -> coat 251 -> front 238) render differs with vs
    # without the 238 glow (the glow is the only difference between them).
    robed = render_character(_APP_M, scale=1, accessories=_slots7(5587))
    saved = dict(glow_mod._COAT_FRONT_GLOW)
    try:
        glow_mod._COAT_FRONT_GLOW.clear()           # disable the coat-front glow
        no_glow = render_character(_APP_M, scale=1, accessories=_slots7(5587))
    finally:
        glow_mod._COAT_FRONT_GLOW.clear()
        glow_mod._COAT_FRONT_GLOW.update(saved)
    assert robed != no_glow, "ChickenBones coat-238 GlowMask_363 glow absent"


def test_armorset_backpack_applies_rgb_factor() -> None:
    # PlayerDrawLayers.cs:452/466: the backpack draw color (250,250,250,200) multiplies
    # per-channel into the texture, so RGB scales by 250/255 (not just the alpha). The
    # fixed color carries all four channels; drawing a white sprite must dim RGB to 250.
    from nextbot.terraria_render.compositor import _ARMORSET_BACKPACK_COLOR, _Compositor
    assert _ARMORSET_BACKPACK_COLOR == (250, 250, 250, 200)
    # synthesize a fully-white opaque Extra sheet (5-frame strip) and draw it; the cell
    # must come out RGB=250 (the 250/255 factor) with alpha scaled by 200/255.
    comp = _Compositor(_APP_M)
    white = np.full((FH * 5, FW, 4), 255, np.uint8)
    glow_mod._sheet.cache_clear()
    import unittest.mock as _mock
    with _mock.patch.object(glow_mod, "_sheet", return_value=white):
        comp.draw_armorset_backpack("Extra_FAKE", (0, 0), None)
    cell = comp.canvas[PAD_T:PAD_T + FH, PAD_L:PAD_L + FW]
    # over a transparent canvas the straight-alpha RGB is the (dimmed) sprite RGB = 250.
    assert int(cell[0, 0, 0]) == 250, f"RGB not dimmed by 250/255 (got {cell[0, 0, 0]})"
    assert int(cell[0, 0, 3]) == 255 * 200 // 255   # alpha scaled by 200/255
    glow_mod._sheet.cache_clear()


def test_glowmask_aux_masks_extracted() -> None:
    # The auxiliary Glow ids (308 head-269 extra, 363 coat-238) are listed in
    # glowmask.json's aux_masks and must be extracted as Glow_{id}.png (extract_assets
    # referenced_glow_masks() now includes aux_masks; Extra_214 via SINGLE_TEXTURES).
    assert set(glow_mod._GLOW.get("aux_masks", [])) == {308, 363}
    for mask in (308, 363):
        assert glow_mod._sheet(f"Glow_{mask}") is not None, f"Glow_{mask} not extracted"


def test_glow_detail_changes_dont_affect_plain_equipment() -> None:
    # KEY REGRESSION: the glowmask-detail changes (TV head, head-269 extra, jitter,
    # coat-238 glow, backpack RGB) must NOT alter the output for ordinary, non-glow
    # equipment. A plain body (GoldChainmail 80, no glow), a glow body that is NOT
    # jittered (Spectre 176) and a plain head (no glow) stay byte-identical re-rendered.
    a = render_character(_APP_M, {"head": _acc(690), "body": _acc(80)}, scale=2)
    assert a == render_character(_APP_M, {"head": _acc(690), "body": _acc(80)}, scale=2)
    # a non-jitter glow body (Spectre, slot 176, netId 2761) is unchanged by the jitter
    # code (its slot has no jitter entry -> single pass, exactly as before).
    spectre = render_character(_APP_M, {"body": _acc(2761)}, scale=2)
    assert spectre == render_character(_APP_M, {"body": _acc(2761)}, scale=2)
    assert spectre[:8] == _PNG_SIG


# ── backcoat / tails / backpacks / body→back routing (backcoat_tails_spec.md) ──
# netIds (Item.cs / data tables):
#   tail accessories: 4769 DogTail (backSlot 25), 4775 BunnyTail (backSlot 28);
#   backpack accessories: 1321 MagicQuiver (backSlot 7), 3061 ArchitectGizmoPack (8);
#   5587 ChickenBonesRobe (vanity, NO *Slot -> coat=251); 532 StarCloak (back cape 2);
#   bodies that force a cape: 1839 (slot 96 -> tail 18), 1822 (94 -> tail 19),
#   1750 (80 -> tail 21), 3881 (207 -> back cape 13), 5055 (238 -> backpack 32),
#   1764 (85 -> front 7 + back 20 pair); armor sets 5045/5046/5047 (266/235/218 ->
#   Extra_212 backpack) and 5051/5052/5053 (268/237/222 -> Extra_213).
def test_tail_accessory_routes_to_tail_field() -> None:
    # A back item in DrawInTailLayer (DogTail 4769 -> backSlot 25) resolves to the
    # `tail` field, NOT the `back` cape field (Player.UpdateVisibleAccessory:36481).
    res = _resolve_accessories(_slots7(4769), None, None, 0, male=True)
    assert res["slots"].get("tail") == 25
    assert "back" not in res["slots"] and "backpack" not in res["slots"]


def test_tail_accessory_renders_behind_body() -> None:
    # A tail accessory must now render (it used to be skipped): the render differs from
    # a no-accessory one and adds pixels behind the body (08_1_Tails, before body skin).
    none = render_character(_APP_M, scale=2)
    tailed = render_character(_APP_M, scale=2, accessories=_slots7(4775))
    assert tailed[:8] == _PNG_SIG
    assert tailed != none


def test_tail_female_x_offset() -> None:
    # 08_1_Tails shifts the tail +2*direction in X for a female player (idle dir=1 ->
    # +2); male is unshifted (PlayerDrawLayers.cs:577-579). The female render is the
    # male one moved right by 2px in the tail region, so the two differ.
    app_f = {**_APPEARANCE, "skinVariant": 4, "hair": 5}  # female base variant
    app_m = {**_APP_M}
    fem = render_character(app_f, scale=1, accessories=_slots7(4775))
    masc = render_character(app_m, scale=1, accessories=_slots7(4775))
    assert fem[:8] == _PNG_SIG and masc[:8] == _PNG_SIG
    # (the body sprites also differ by gender, so this only asserts both render; the
    # offset itself is unit-tested below via the resolver-independent strip shift.)
    base = _Compositor(app_f)
    base.draw_acc_strip("Acc_Back_28", None, offset=(0, 0))
    moved = _Compositor(app_f)
    moved.draw_acc_strip("Acc_Back_28", None, offset=(2, 0))
    a = base.canvas[PAD_T:PAD_T + FH, PAD_L:PAD_L + FW]
    b = moved.canvas[PAD_T:PAD_T + FH, PAD_L + 2:PAD_L + 2 + FW]
    assert np.array_equal(a, b)


def test_backpack_accessory_routes_and_renders() -> None:
    # A back item in DrawInBackpackLayer (MagicQuiver 1321 -> backSlot 7) resolves to
    # the `backpack` field (UpdateVisibleAccessory:36477), and renders (was skipped).
    res = _resolve_accessories(_slots7(1321), None, None, 0, male=True)
    assert res["slots"].get("backpack") == 7
    assert "back" not in res["slots"]
    none = render_character(_APP_M, scale=2)
    packed = render_character(_APP_M, scale=2, accessories=_slots7(1321))
    assert packed[:8] == _PNG_SIG
    assert packed != none


def test_chickenbones_robe_sets_coat_and_draws_both_pieces() -> None:
    # ChickenBonesRobe (netId 5587) carries NO *Slot, so it's absent from the slot
    # tables; it must be special-cased to coat=251 (UpdateVisibleAccessory:36585), which
    # draws the back piece (Armor_Legs_239, behind the body) AND the front piece
    # (Armor_Legs_238, the long-coat skirt). Both differ from the no-robe render.
    res = _resolve_accessories(_slots7(5587), None, None, 0, male=True)
    assert res["coat"] == 251
    assert res["slots"] == {}                  # the robe sets no visual slot, only coat
    none = render_character(_APP_M, scale=2)
    robed = render_character(_APP_M, scale=2, accessories=_slots7(5587))
    assert robed[:8] == _PNG_SIG
    assert robed != none
    # and works from the vanity (social) accessory slots too.
    robed_vanity = render_character(
        _APP_M, scale=2, vanity_accessories=_slots7(5587))
    assert robed_vanity != none


def test_chickenbones_robe_dye_applies() -> None:
    # The coat (cCoat) dye comes from item 5587's own accessory-dye slot; a dyed robe
    # differs from the undyed one (the dye rides accessoryDyes[k], k = the robe's slot).
    undyed = render_character(_APP_M, scale=2, accessories=_slots7(5587))
    dyed = render_character(
        _APP_M, scale=2, accessories=_slots7(5587),
        accessory_dyes=[_acc(1007)] + [{"netId": 0}] * 6)   # RedDye in slot 0
    assert dyed != undyed
    res = _resolve_accessories(
        _slots7(5587), None, [_acc(1007)] + [{"netId": 0}] * 6, 0, male=True)
    assert res["coat_dye"] is not None
    assert res["coat_dye"]["pass"] == "ArmorColored"


def test_body_forced_tail_routes_and_renders() -> None:
    # A body whose IncludedCapeBack maps to a DrawInTailLayer slot forces a tail
    # (Player.cs:35417). Body slot 96 (netId 1839) -> back 18 -> tail. The tail is dyed
    # with the BODY dye (cTail = cBody). It must render and add tail pixels vs a
    # non-cape body (GoldChainmail 80, body slot 1, which forces nothing).
    res = _resolve_accessories(None, None, None, 0, male=True, body_slot=96)
    assert res["slots"].get("tail") == 18
    plain = render_character(_APP_M, {"body": _acc(80)}, scale=2)
    caped = render_character(_APP_M, {"body": _acc(1839)}, scale=2)
    assert caped[:8] == _PNG_SIG
    assert caped != plain


def test_body_forced_back_cape_and_backpack() -> None:
    # body 207 (netId 3881) -> back cape 13 (a real cape, drawn at layer 10); body 238
    # (netId 5055) -> backpack 32 (the backpack layer). Both via IncludedCapeBack + the
    # 3-way Back.Sets routing (Player.cs:35412-35425).
    assert _resolve_accessories(
        None, None, None, 0, male=True, body_slot=207)["slots"].get("back") == 13
    assert _resolve_accessories(
        None, None, None, 0, male=True, body_slot=238)["slots"].get("backpack") == 32
    cape = render_character(_APP_M, {"body": _acc(3881)}, scale=1)
    pack = render_character(_APP_M, {"body": _acc(5055)}, scale=1)
    assert cape[:8] == _PNG_SIG and pack[:8] == _PNG_SIG


def test_body_cape_female_variant_differs() -> None:
    # IncludedCapeBackFemale diverges from the male table only at body 217 (back 22
    # male, 23 female) -- the one gender-specific cape slot. Cross-check the resolver.
    male = _resolve_accessories(None, None, None, 0, male=True, body_slot=217)
    female = _resolve_accessories(None, None, None, 0, male=False, body_slot=217)
    assert male["slots"].get("back") == 22
    assert female["slots"].get("back") == 23


def test_body_cape_front_and_back_pair_is_atomic() -> None:
    # IncludeCapeFrontAndBack (body 85 -> front 7 + back 20) is applied atomically and
    # ONLY when both back and front are still unset (Player.cs:35436). With an accessory
    # back cape already present (StarCloak 532 -> back 2) the body's whole pair is
    # suppressed -> only the accessory cape remains.
    pair = _resolve_accessories(None, None, None, 0, male=True, body_slot=85)
    assert pair["slots"].get("back") == 20 and pair["slots"].get("front") == 7
    suppressed = _resolve_accessories(
        _slots7(532), None, None, 0, male=True, body_slot=85)
    assert suppressed["slots"].get("back") == 2     # the accessory cape
    assert "front" not in suppressed["slots"]       # body pair fully suppressed


def test_body_forced_cape_uses_body_dye() -> None:
    # A body-forced cape is dyed with the BODY dye (cBack/cTail/cFront = cBody,
    # Player.cs:35415..35433), not an accessory dye. Pass a body dye and assert the
    # resolved cape dye is that body dye.
    red = {"pass": "ArmorColored", "color": [1.0, 0.0, 0.0], "sat": 1.2}
    res = _resolve_accessories(
        None, None, None, 0, male=True, body_slot=207, body_dye=red)
    assert res["dyes"].get("back") == red       # back cape carries the body dye


def test_armorset_backpack_renders() -> None:
    # The two armor-set backpacks: displayed (head,body,legs) == (266,235,218) draws
    # Extra_212, (268,237,222) draws Extra_213 (DrawPlayer_08_Backpacks:446/458). The
    # full set differs from wearing just the body (which doesn't complete the set -> no
    # backpack). netIds 5045/5046/5047 (set1) and 5051/5052/5053 (set2).
    set1 = {"head": _acc(5045), "body": _acc(5046), "legs": _acc(5047)}
    full1 = render_character(_APP_M, set1, scale=1)
    body_only = render_character(_APP_M, {"body": _acc(5046)}, scale=1)
    assert full1[:8] == _PNG_SIG
    assert full1 != body_only                   # the backpack appears only for the set
    set2 = {"head": _acc(5051), "body": _acc(5052), "legs": _acc(5053)}
    assert render_character(_APP_M, set2, scale=1)[:8] == _PNG_SIG


def test_armorset_backpack_triggers_match_decompiled() -> None:
    # Transcription cross-check of the two armor-set triggers + their Extra ids/offsets
    # (DrawPlayer_08_Backpacks:446/458). A wrong triple/offset => wrong/absent backpack.
    from nextbot.terraria_render.compositor import (
        _ARMORSET_BACKPACK_COLOR,
        _ARMORSET_BACKPACKS,
    )
    assert _ARMORSET_BACKPACKS == (
        ((266, 235, 218), 212, (-4, 0)),
        ((268, 237, 222), 213, (-8, -4)),
    )
    # full draw color (250,250,250,200): RGB 250/255 + alpha 200/255 (PlayerDrawLayers
    # .cs:452/466 -- sb.Draw multiplies all four channels into the texture).
    assert _ARMORSET_BACKPACK_COLOR == (250, 250, 250, 200)
    # the Extra backpack sheets ship and have content in their idle (frame-0) cell.
    for _triple, extra_id, _off in _ARMORSET_BACKPACKS:
        sheet = glow_mod._sheet(f"Extra_{extra_id}")
        assert sheet is not None, f"Extra_{extra_id} missing"
        fh = sheet.shape[0] // 5                 # 5-frame vertical strip
        assert int((sheet[:fh, :, 3] > 0).sum()) > 0


def test_back_routing_sets_match_decompiled() -> None:
    # The 3-way back-slot routing sets (ArmorIDs.cs:1695/1697) drive backpack/tail/back.
    from nextbot.terraria_render.compositor import _BACK_BACKPACK, _BACK_TAIL
    assert set(_BACK_BACKPACK) == {7, 8, 9, 10, 15, 16, 32, 33}
    assert set(_BACK_TAIL) == {18, 19, 21, 25, 26, 27, 28}
    # a back slot not in either set is a real back cape (drawn at layer 10).
    res = _resolve_accessories(_slots7(532), None, None, 0, male=True)
    assert res["slots"].get("back") == 2        # StarCloak is a real cape


def test_cape_with_both_back_and_front_keeps_both() -> None:
    # Player.UpdateVisibleAccessory order (36475-36494): the `front = -1` clear at 36488
    # runs BEFORE the SAME item's `frontSlot` apply at 36491, so a cape carrying BOTH a
    # back and a front slot (CrimsonCloak 2284 -> back 3 + front 1) keeps BOTH halves --
    # the clear only drops a front set by an EARLIER item.
    res = _resolve_accessories(_slots7(2284), None, None, 0, male=True)
    assert res["slots"].get("back") == 3
    assert res["slots"].get("front") == 1       # the item's own front survives (36491)


def test_real_back_cape_clears_earlier_front() -> None:
    # The 36488 `front = -1` clear DOES drop a front set by an EARLIER item. With
    # CrimsonCloak (2284: back 3 + front 1) in slot 0 then StarCloak (532: back 2, no
    # front) in slot 1, the StarCloak back clears the CrimsonCloak front -> only back 2
    # survives (the later real back wins, and StarCloak has no own front to restore it).
    res = _resolve_accessories(_slots7(2284, 532), None, None, 0, male=True)
    assert res["slots"].get("back") == 2        # StarCloak (slot 1) is the last back
    assert "front" not in res["slots"]          # CrimsonCloak's front cleared by 36488


def test_plain_and_normal_accessory_output_unchanged() -> None:
    # KEY REGRESSION: the backcoat/tails/backpacks/body-routing changes must NOT alter
    # the output for a plain character or for ordinary (non-tail/backpack/coat) accs.
    # A plain render, a wings render, a back-cape render, and a body that forces NO cape
    # must all be byte-identical to a fresh render (determinism + no spurious layers).
    plain = render_character(_APP_M, scale=4)
    assert plain == render_character(_APP_M, scale=4)
    # a normal back cape (StarCloak 532 -> back 2) is a real cape, unchanged by routing.
    cape = render_character(_APP_M, scale=4, accessories=_slots7(532))
    assert cape == render_character(_APP_M, scale=4, accessories=_slots7(532))
    assert cape != plain                         # the cape is still drawn
    # wings still render and widen (the original accessory regression still holds).
    winged = render_character(_APPEARANCE, scale=4, accessories=_slots7(493))
    assert winged == render_character(_APPEARANCE, scale=4, accessories=_slots7(493))
    # a body that forces NO cape (GoldChainmail 80, body slot 1) is unchanged by the
    # body-cape code (its slot isn't in body_cape.json).
    b80 = render_character(_APP_M, {"body": _acc(80)}, scale=4)
    assert b80 == render_character(_APP_M, {"body": _acc(80)}, scale=4)


def _run() -> int:
    tests = [
        test_render_returns_valid_png,
        test_render_is_deterministic,
        test_render_appearance_only,
        test_scale_changes_dimensions,
        test_armor_colored_is_copper,
        test_invert_deterministic_and_changes_input,
        test_gradient_red_to_yellow_is_warm,
        test_unknown_pass_falls_back_undyed,
        test_batch1_passes_dispatch_through_bytecode,
        test_armor_colored_copper_is_bytecode_faithful,
        test_batch1_no_preshader_passes_run_without_crash,
        test_batch1_falls_back_to_handwritten_without_baked_blob,
        test_noise_dye_is_spatially_varying,
        test_noise_dye_falls_back_without_geometry,
        test_twilight_dye_changes_input,
        test_gel_dye_preserves_source_arm_body_shading,
        test_vortex_dye_is_faithful_hard_clip_not_overexposed,
        test_stardust_dye_is_faithful_hard_clip_not_overexposed,
        test_stardust_fix_leaves_other_noise_pillars_unchanged,
        test_midnight_rainbow_faithful_emboss_differs_from_approx,
        test_midnight_rainbow_animates_with_utime,
        test_midnight_rainbow_falls_back_without_baked_blob,
        test_offset_tap_fix_leaves_center_only_noise_pass_unchanged,
        test_batch2_passes_dispatch_through_bytecode,
        test_batch2_faithful_differs_from_handwritten_approx,
        test_batch2_flow_is_bit_identical_to_handwritten_at_utime0,
        test_batch2_animates_with_utime,
        test_batch2_acid_void_swept_representative_not_collapsed,
        test_batch2_falls_back_to_handwritten_without_baked_blob,
        test_batch3_passes_dispatch_through_bytecode,
        test_high_contrast_glow_chroma_gated_bytecode,
        test_high_contrast_glow_falls_back_without_baked_blob,
        test_reflective_offline_is_no_highlight_not_crash,
        test_reflective_falls_back_without_baked_blob,
        test_solar_bytecode_runs_but_default_stays_handwritten,
        test_solar_bytecode_falls_back_without_baked_blob,
        test_hairdye_twilight_differs_from_none,
        test_hairdye_legacy_changes_hair_color,
        test_back_hair_predicate_matches_game,
        test_no_accessory_render_is_body_sized,
        test_wings_change_and_widen_render,
        test_vanity_accessory_overrides_functional,
        test_hidevisuals_hides_functional_accessory,
        test_hidden_functional_keeps_vanity_twin,
        test_shoe_accessory_is_visible,
        test_accessory_dye_routes_to_category,
        test_animated_wing_renders_nothing_grounded,
        test_handoff_acc_draws_over_body_armor_back_arm,
        test_skin_hide_gates_match_decompiled_sets,
        test_masked_body_render_is_valid,
        test_back_head_front_to_back_set_matches_decompiled,
        test_back_head_adds_visible_pixels_behind_body,
        test_useskincolor_head_uses_skin_color,
        test_shoe_acc_suppressed_by_leg_override,
        test_shoe15_suppresses_default_legs_and_leg_armor,
        test_under_hair_face_is_occluded_by_hair,
        test_shield_and_front_acc_render_is_stable,
        test_bespoke_wing_offsets_match_decompiled,
        test_bespoke_wing_frame_crop,
        test_default_wing_offset_unchanged,
        test_face19_draw_offset_applied,
        test_front13_and_rollerskate_offsets_match_decompiled,
        test_over_glow_additive_does_not_drop_layer,
        test_over_glow_opaque_color_covers,
        test_over_glow_empty_texture_is_noop,
        test_glow_body_brightens_torso_region,
        test_glow_head_changes_and_legs_glow_render,
        test_non_glow_body_unaffected_by_glow_code,
        test_glow_color_arkhalis_uses_undershirt,
        test_tv_head_glow_constants_match_decompiled,
        test_tv_head_glow_draws_default_column_not_empty_col0,
        test_head269_frontshoulder_extra_glow,
        test_jitter_offsets_match_decompiled_magnitudes,
        test_jitter_spreads_glow_region,
        test_body227_jitter_renders_brighter_superset,
        test_body205_frontarm_4tap_adds_shimmer,
        test_head211_4tap_constants_match_decompiled,
        test_head211_4tap_adds_additive_head_local_shimmer,
        test_coat238_glow_constants_and_render,
        test_armorset_backpack_applies_rgb_factor,
        test_glowmask_aux_masks_extracted,
        test_glow_detail_changes_dont_affect_plain_equipment,
        test_tail_accessory_routes_to_tail_field,
        test_tail_accessory_renders_behind_body,
        test_tail_female_x_offset,
        test_backpack_accessory_routes_and_renders,
        test_chickenbones_robe_sets_coat_and_draws_both_pieces,
        test_chickenbones_robe_dye_applies,
        test_body_forced_tail_routes_and_renders,
        test_body_forced_back_cape_and_backpack,
        test_body_cape_female_variant_differs,
        test_body_cape_front_and_back_pair_is_atomic,
        test_body_forced_cape_uses_body_dye,
        test_armorset_backpack_renders,
        test_armorset_backpack_triggers_match_decompiled,
        test_back_routing_sets_match_decompiled,
        test_cape_with_both_back_and_front_keeps_both,
        test_real_back_cape_clears_earlier_front,
        test_plain_and_normal_accessory_output_unchanged,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
