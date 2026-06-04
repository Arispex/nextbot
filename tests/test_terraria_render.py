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
    FH,
    FW,
    PAD_L,
    PAD_T,
    _acc_wing_frame,
    _back_hair_style,
    _Compositor,
    _frame,
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
    head_skin = _opaque_cells(_resolve_player(0, 0), 0)  # layer 0 head skin (in front)
    front_helm = _opaque_cells("Armor_Head_224", 0)      # front helmet (in front)
    front_hair = _opaque_cells("Player_Hair_1", 0)       # head 224 fullHair: hair draws
    surviving = back & ~head_skin & ~front_helm & ~front_hair
    assert int(surviving.sum()) > 0, (
        "the back-head texture is fully covered -- it would be invisible either way")

    # end-to-end: head 224 renders (the back-head poking out is what makes its render
    # differ from the bare head; the front helmet shares the same idle frame anyway).
    helmeted = render_character({**_APP_M, "hair": 0}, {"head": _acc(4560)}, scale=1)
    assert helmeted[:8] == _PNG_SIG


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
        test_handoff_acc_draws_over_body_armor_back_arm,
        test_skin_hide_gates_match_decompiled_sets,
        test_masked_body_render_is_valid,
        test_back_head_front_to_back_set_matches_decompiled,
        test_back_head_adds_visible_pixels_behind_body,
        test_shoe_acc_suppressed_by_leg_override,
        test_under_hair_face_is_occluded_by_hair,
        test_shield_and_front_acc_render_is_stable,
        test_bespoke_wing_offsets_match_decompiled,
        test_bespoke_wing_frame_crop,
        test_default_wing_offset_unchanged,
        test_face19_draw_offset_applied,
        test_front13_and_rollerskate_offsets_match_decompiled,
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
