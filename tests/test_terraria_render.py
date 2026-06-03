"""Minimal tests for the reusable Terraria character renderer.

Dependency-light: no network, no pytest-only fixtures. Runs under pytest
(``uv run pytest tests/test_terraria_render.py``) or as a plain script
(``uv run python tests/test_terraria_render.py``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# allow `python tests/test_terraria_render.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nextbot.terraria_render import render_character
from nextbot.terraria_render.compositor import _back_hair_style
from nextbot.terraria_render.dye import apply_dye

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

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
        test_noise_dye_is_spatially_varying,
        test_noise_dye_falls_back_without_geometry,
        test_twilight_dye_changes_input,
        test_hairdye_twilight_differs_from_none,
        test_hairdye_legacy_changes_hair_color,
        test_back_hair_predicate_matches_game,
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
