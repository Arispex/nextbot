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

from nextbot.terraria_render import render_character
from nextbot.terraria_render.compositor import (
    _back_hair_style,
    _resolve_accessories,
)
from nextbot.terraria_render.dye import apply_dye
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
        test_no_accessory_render_is_body_sized,
        test_wings_change_and_widen_render,
        test_vanity_accessory_overrides_functional,
        test_hidevisuals_hides_functional_accessory,
        test_hidden_functional_keeps_vanity_twin,
        test_shoe_accessory_is_visible,
        test_accessory_dye_routes_to_category,
        test_animated_wing_renders_nothing_grounded,
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
