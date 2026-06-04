"""Terraria player avatar compositor (numpy). Renders base body + equipment +
vanity + armor dyes + the 12 accessory categories into a transparent PNG.
Reusable, no NoneBot dependency.

Faithful port of the validated prototype (see research/terraria_render_spec.md):
composite idle frames, variant fallback, hair occlusion, per-part equipment/vanity,
exact ArmorColored dyes. Accessories (research/accessories_spec.md): wings, back/front
capes, balloons, shoes, waist/neck/face, shield, on/off-hand composites and beard, with
the vanity-override + hideVisuals resolution and per-slot accessory dyes.

Canvas: the body occupies a fixed 40x56 cell at origin (PAD_L, PAD_T) inside a padded
CANVAS_W x CANVAS_H frame, because wings (≤120px wide) / balloons extend beyond the body
cell. Equipment/hair still align to the body cell. render_character then crops the result
to the bounding box of its non-transparent pixels (plus a small margin) before scaling, so
the returned sprite is content-tight: a no-accessory character is ~40x56, a winged/balloon
one is only as large as its actual content (no fixed padding around it).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import numpy as np

from .dye import apply_dye
from .image_io import read_png, write_png

_HERE = Path(__file__).resolve().parent
ASSETS = _HERE / "assets"
DATA = _HERE / "data"
FW, FH = 40, 56
# Composite body/arm glow rides the SAME ArmorBody sheet's lower half: sourceRect.Y += 224
# (glowmask_spec.md §5.1). On the 360x224 grid (9 cols x 56px rows) that is +4 rows = +36
# cells. So a sub-part's glow cell = its colored cell + 36 (only present on 360x448 sheets).
_GLOW_CELL_DELTA = 36
_RGBA_LEN = 4  # a glow-color table entry is an (r,g,b,a) 4-tuple
# Padded canvas: body cell sits at (PAD_L, PAD_T); wings (worst case ~53px left / 31px
# right / 14px up, widest vanilla wing 120px) and balloons (~24px below) overflow the
# 40x56 cell. Symmetric horizontal pad 56 + vertical pad 28 covers every drawable
# accessory with margin (accessories_spec.md §1/§3). Canvas = 152 x 112.
PAD_L, PAD_R, PAD_T, PAD_B = 56, 56, 28, 28
CANVAS_W = FW + PAD_L + PAD_R   # 152
CANVAS_H = FH + PAD_T + PAD_B   # 112
# Uniform margin kept around the content bounding box when cropping the padded canvas
# down to its non-transparent pixels (so the sprite isn't flush against the PNG edge).
_CROP_MARGIN = 2
# Non-torso balloon idle frame (52x56) cell-local top-left, derived from
# DrawPlayer_11_Balloons (OffsetsPlayerOffhand[0]=(14,20), origin (30,34)); the balloon
# floats up-left of the body. accessories_spec.md §3.
_BALLOON_OFFSET = (-6, -4)

# ── accessory category Sets (ArmorIDs.cs, cited; small enough to inline) ──
# back slot routing (Player.UpdateVisibleAccessory:36475-36490): a back slot in
# DrawInBackpackLayer goes to the backpack field (layer 08_Backpacks), one in
# DrawInTailLayer to the tail field (layer 08_1_Tails); everything else is a real back
# cape (layer 10). ArmorIDs.cs:1695 (DrawInBackpackLayer) / 1697 (DrawInTailLayer).
_BACK_BACKPACK = {7, 8, 9, 10, 15, 16, 32, 33}
_BACK_TAIL = {18, 19, 21, 25, 26, 27, 28}
# armor sets whose displayed (head, body, legs) trigger an Extra_* backpack
# (DrawPlayer_08_Backpacks:446/458). Each maps the (head,body,legs) triple to the Extra
# texture + the cell-local top-left offset of its 5-frame strip (idle frame 0) and its
# half-translucent draw color (250,250,250,200). PlayerDrawLayers.cs:448-468.
_ARMORSET_BACKPACKS = (
    # (head, body, legs), Extra id, (offset_x, offset_y)
    ((266, 235, 218), 212, (-4, 0)),     # vec.X = -2 + -2*Directions.X (dir=1) = -4
    ((268, 237, 222), 213, (-8, -4)),    # vec = (-9 + 1*dir, -4*gravDir) = (-8, -4)
)
# the half-translucent backpack draw color new Color(250,250,250,200): the DrawData color is
# multiplied per-channel into the texture by sb.Draw, so ALL FOUR channels scale the sprite —
# RGB by 250/255 (a ~2% dim) AND alpha by 200/255 (PlayerDrawLayers.cs:452/466). (Idle:
# GetImmuneAlphaPure and *stealth are both no-ops -> 1.0.) Earlier this only applied the
# alpha and treated the 250 RGB as negligible; the faithful behavior tints RGB too.
_ARMORSET_BACKPACK_COLOR = (250, 250, 250, 200)
# ChickenBonesRobe (item 5587) is a vanity/social accessory that sets NO *Slot; its only
# visual effect is coat=251 (Player.UpdateVisibleAccessory:36585). Item.cs gives it no
# slot, so it is absent from accessory_slots.json and must be special-cased on netId.
_CHICKENBONES_ROBE_NETID = 5587
_CHICKENBONES_COAT = 251
# coat -> the two long-coat leg-armor extension pieces (DrawPlayer_13_ArmorBackCoat /
# DrawPlayer_16_ArmorLongCoat coat branch). The BACK piece (GetMatchingBodyExtensionBack,
# PlayerDrawLayers.cs:1838: only 251->239) draws BEHIND the body (before the skin); the
# FRONT piece (GetMatchingBodyExtension, :1848: 251->238) draws as the long-coat skirt.
# Both carry the cCoat dye (the dye of the slot holding item 5587).
_COAT_BACK_EXT = {251: 239}
_COAT_FRONT_EXT = {251: 238}
# balloon 18 (RoyalScepter): torso-framed (40x1120) AND drawn in front of the back arm
# (balloonFront), not the normal behind-body balloon layer. ArmorIDs.cs:2212/2214.
_BALLOON_TORSO = {18}
# face items that suppress the front hair draw. ArmorIDs.cs:2140.
_FACE_PREVENT_HAIR = {2, 3, 4, 19}
# per-category draw offsets that are NONZERO for a bare-headed idle player (Directions =
# (direction, gravDir) = (1, 1), so the offset == the base vector). The other Get*DrawOffset
# cases (face 1/6/8/9/22 etc., beard) only fire for specific equipped head slots / mounts and
# are 0 at idle. (Player.cs GetFaceDrawOffset:4365 / GetFrontDrawOffset:4708 / GetShoeDrawOffset
# :4729.) Each maps slot -> (lx, ly) added to the cell-local top-left.
_FACE_DRAW_OFFSET = {19: (0, -6)}          # GetFaceDrawOffset case 19: (0,-6)*Directions
_FRONT_DRAW_OFFSET = {13: (-2, 0)}         # GetFrontDrawOffset front==13: (-2,0)*Directions
_SHOE_DRAW_OFFSET = {27: (0, 2), 28: (0, 2), 29: (0, 2), 30: (0, 2)}  # roller skates
# face items drawn UNDER the front hair (ArmorIDs.Face.Sets.DrawInFaceUnderHairLayer,
# ArmorIDs.cs:2144 = CreateBoolSet(false, 5)): only face 5 (Blindfold). It is drawn
# inside the head group right after the eyes (PlayerDrawLayers.cs:2631) and is the ONE
# face that is NOT drawn at layer 22 (DrawPlayer_22_FaceAcc guards on the same set,
# 2807). Every other face draws over the hair at layer 22.
_FACE_UNDER_HAIR = {5}
# head equip slots that have a separate behind-body back-head texture
# (ArmorIDs.Head.Sets.FrontToBackID, ArmorIDs.cs:14 = CreateIntSet(-1, 242,246, 243,247,
# 244,248, 245,249, 133,252, 224,253)): head slot -> its back-head ArmorHead texture id,
# drawn behind the body (DrawPlayer_01_3_BackHead, LegacyPlayerRenderer.cs:185) with the
# body frame + head dye.
_HEAD_FRONT_TO_BACK = {242: 246, 243: 247, 244: 248, 245: 249, 133: 252, 224: 253}
# head equip slots drawn with the player's SKIN color (skinColor) instead of armor color
# (white), using the skin shader rather than the head dye (ArmorIDs.Head.Sets.UseSkinColor,
# ArmorIDs.cs:16 = CreateBoolSet(false, 274, 277)). Read in DrawPlayer_21_Head's head-armor
# draws (PlayerDrawLayers.cs:2145/2223/2330): color = colorHead (= skinColor), shader =
# skinDyePacked. 274 = HallowedHood? / 277 = (skin-tinted hood-style heads).
_HEAD_USE_SKIN_COLOR = frozenset({274, 277})
# beards tinted by the player's hair color (Wilson beards). ArmorIDs.cs:2281.
_BEARD_HAIR_COLOR = {2, 3, 4}
# GlassSlipper male->female shoe-slot remap when the player is female. ArmorIDs.cs:1841
# (CreateIntSet(-1, 25, 26): slot 25 -> 26).
_SHOE_MALE_TO_FEMALE = {25: 26}
# body equip slots that set Player.wearsRobe (Player.cs SetMatch, ArmorSlotRequested=1,
# Player.cs:36776-36886): every case sets flag=true EXCEPT body 166 (flag=false), and the
# game's leg/shoe branch (LegacyPlayerRenderer.cs:194) also excludes 166, so 166 always
# takes the normal branch — it is intentionally NOT in this set. Slot 81 only counts as a
# robe when there is no leg armor (its case assigns num2 only if Legs in {-1,0}); that
# condition is applied separately at draw time.
_WEARS_ROBE_BODIES = frozenset({
    15, 36, 41, 42, 58, 59, 60, 61, 62, 63, 77, 165, 167, 180, 181, 183, 191,
    93, 90, 88, 81, 213, 215, 219, 221, 223, 231, 232, 233, 241, 256,
})
# slot 81 is a robe only when no leg armor is worn (Player.cs:36850 guards its num2).
_WEARS_ROBE_LEGLESS_ONLY = frozenset({81})
# leg equip slots that suppress both the shoe accessory (DrawPlayer_14_Shoes guarded by
# !ShouldOverrideLegs_CheckPants, PlayerDrawLayers.cs:1758) AND the leg skin (layer 10,
# guarded by !IsBottomOverridden, 1193/1205). ShouldOverrideLegs_CheckPants (1218-1241)
# returns true for these legs — UNLESS shoe == 15 (ShouldOverrideLegs_CheckShoes, 1243),
# which short-circuits CheckPants to false (the shoe acc and leg skin are then drawn).
_LEG_OVERRIDE_SLOTS = frozenset({55, 63, 67, 106, 138, 140, 143, 217, 222, 226, 228})
# the shoe slot that short-circuits the leg-override (ShouldOverrideLegs_CheckShoes,
# PlayerDrawLayers.cs:1246: shoe == 15).
_SHOE_OVERRIDE_EXCEPTION = 15
# body equip slots that hide the torso skin (PlayerDrawSet.hidesTopSkin,
# PlayerDrawSet.cs:1755): the body armor fully replaces the torso, so the bare skin must
# not show through.
_HIDES_TOP_SKIN_BODIES = frozenset({21, 22, 82, 83, 93})
# leg equip slots that hide the leg skin (PlayerDrawSet.hidesBottomSkin,
# PlayerDrawSet.cs:1756). The leg skin is additionally hidden when the body is
# _HIDES_BOTTOM_SKIN_BODY, or by IsBottomOverridden (the _LEG_OVERRIDE_SLOTS / shoe==15
# logic above).
_HIDES_BOTTOM_SKIN_LEGS = frozenset({20, 21, 214, 215, 216})
# the one body slot that also hides the leg skin (PlayerDrawSet.cs:1756: body == 93).
_HIDES_BOTTOM_SKIN_BODY = 93
# the 7 accessory inventory slots map to hideVisibleAccessory bits 3..9 (a still standing
# player hides functional wings too, since velocity.Y==0). accessories_spec.md §X2.
_HIDE_BIT_BASE = 3
# front-hair forehead clip height when the style also draws a back pass
_FRONT_HAIR_CLIP = 26
# Player.GetHairSettings backHairDraw (Player.cs:16787): the predicate is "hair index
# inside the open range (50, 116) but not in any of these excluded windows/values".
_BACK_HAIR_RANGE = range(51, 116)        # num > 50 && num < 116
_BACK_HAIR_EXCLUDED = (                   # the (<a||>b) windows + the != points
    *range(56, 64), *range(74, 78), *range(88, 90), 94, 100, 104, 112,
)
_BACK_HAIR_FORCED = (6, 133, 134, 146, 162)  # explicitly forced True regardless


def _load_json(name: str) -> Any:
    with (DATA / name).open(encoding="utf-8") as f:
        return json.load(f)


_EQUIP_SLOTS = _load_json("equip_slots.json")   # netId -> {"head"|"body"|"legs": slot}
# netId -> {category: slot} for the 12 visual accessory categories (accessory_slots.json)
_ACC_SLOTS = _load_json("accessory_slots.json")
# wing draw metadata: {always_animated:[...], frames:{slot:N}, offset:{slot:[n13,n12]}}
_WING_META = _load_json("wing_meta.json")
_WING_ALWAYS_ANIMATED = set(_WING_META["always_animated"])
_WING_FRAMES = {int(k): v for k, v in _WING_META["frames"].items()}
_WING_OFFSET = {int(k): tuple(v) for k, v in _WING_META["offset"].items()}
# Wings 47/49/50/51 use bespoke offsets in DrawPlayer_09_Wings, NOT the default
# (num13-9, num12+2) formula. Each maps slot -> (center_x, center_y, crop): `center` is the
# drawn frame's cell-local center (body cell top-left = (0,0)) derived from the decompiled
# branch, and `crop` trims the source frame Width/Height by 2px (rectangle.Width-=2;
# Height-=2) before drawing. The top-left is then center - frameDims//2 (same integer-origin
# convention as the default formula). Derivations (PlayerDrawLayers.cs, idle direction=1,
# gravDir=1, bodyFrame.Y=0 so OffsetsPlayerHeadgear[0]=(0,2)):
#   47 (:800-808) / 49 (:816-827): vector8=(0,2); .Y-=2 -> (0,0); vector9=(1,1)+(0,0)=(1,1);
#       vec = vector + (1,1)*Directions - UnitX*direction*4 = vector + (-3,1) -> center (17,32)
#   50 (:916-923): zero2=(0,0); vec = vector - UnitX*direction*4 = vector + (-4,0); the frame
#       uses `value10` directly (no -2 crop) -> center (16,31)
#   51 (:773-784): builds its OWN base with (0,6) instead of the common (0,7), then
#       - UnitX*direction*4; vec = [Position-screen+(width/2, height-bodyFrame.H/2)] + (0,6)
#       - (4,0) -> center (16,30)
# (vector's cell-local center = (20,31); see draw_acc_wing for the default-formula identity.)
_WING_BESPOKE: dict[int, tuple[int, int, bool]] = {
    47: (17, 32, True),
    49: (17, 32, True),
    50: (16, 31, False),
    51: (16, 30, True),
}
_DYES = _load_json("dyes.json")                 # dye netId -> {pass,color,sat,...}
# equipment glowmask tables (research/glowmask_spec.md §2): per-part equip-slot -> glow
# color. body/arm = RGBA list (or "arkhalis"); head/legs = {"mask": id, "color": ...}.
# Keys are stringified equip slots (matching equip_slots.json output).
_GLOW = _load_json("glowmask.json")
_GLOW_BODY: dict[str, Any] = _GLOW["body"]
_GLOW_ARM: dict[str, Any] = _GLOW["arm"]
_GLOW_HEAD: dict[str, Any] = _GLOW["head"]
_GLOW_LEGS: dict[str, Any] = _GLOW["legs"]
# composite torso/arm sub-parts whose glow is drawn in N jittered passes (body 227 Nebula:
# torso + arm composite glow each draw twice with a sub-pixel Main.rand offset,
# PlayerDrawLayers.cs:63-76/90-101). Keys are stringified body equip slots.
_GLOW_BODY_JITTER: dict[str, int] = _GLOW.get("body_jitter", {})
_GLOW_ARM_JITTER: dict[str, int] = _GLOW.get("arm_jitter", {})
# the 'arkhalis' sentinel = ArkhalisColor = (underShirtColor.rgb, A=180) (PlayerDrawSet
# .cs:515-516), resolved per-render from the appearance undershirt color.
_GLOW_ARKHALIS_ALPHA = 180
# Sub-pixel jitter: several glow layers are drawn multiple times at a small Main.rand
# offset (max ±1.25px for the x0.125 jitters, ±2px for the x0.2 4-tap). Our pipeline is
# integer-pixel, so we round to a fixed REPRESENTATIVE set of integer offsets (the random
# jitter has no deterministic phase; this mirrors dye.py's UTIME=0 representative-still
# convention). The first pass is on-grid (0,0) like the colored base; extra passes fan out
# by ~1px so the glow visibly "spreads" (the in-game effect of the jitter) rather than
# stacking exactly. PlayerDrawLayers.cs:68/95 (x0.125) and :125-126/2410-2411 (x0.2/x0.15).
_JITTER_OFFSETS = ((0, 0), (1, 1), (-1, 1), (1, -1))
# body 205 (ApprenticeAltShirt, netId 3875 — DrawCompositeArmorPiece FrontArm extra,
# PlayerDrawLayers.cs:118-135): a SEPARATE 4-tap additive shimmer on the front-arm composite
# glow (lower half), color (100,100,100,0), independent of armGlowColor (body 205 sets none).
# The 4 taps use RandomInt(-10,11)*0.2 (X, ±2px) and RandomInt(-10,1)*0.15 (Y, [-1.5,0]) —
# representative integer fan.
_BODY205_FRONTARM_4TAP = 205
_BODY205_4TAP_COLOR = (100, 100, 100, 0)
# representative integer fan for the x0.2 / x0.15 4-taps (body 205): a small spread the
# colored base does not have. (The exact per-frame offsets are Main.rand-driven; idle has no
# fixed phase, so we use a deterministic representative fan — same spirit as dye.py UTIME=0.)
_TAP4_OFFSETS = ((0, 0), (2, 0), (-2, -1), (1, -1))
# head 211 (ApprenticeAltHead, netId 3874 — the head sibling of body 205's ApprenticeAltShirt,
# PlayerDrawLayers.cs:2403-2415): the SAME 4-tap additive (100,100,100,0) shimmer mechanism,
# but over the head cell via an INDEPENDENT GlowMask_241 strip (sourceRect = bodyFrame, idle
# (0,0,40,56) — no composite +224 Y), carrying the head dye (cHead). It is NOT a normal
# headGlowMask (head 211 sets none); this is a hardcoded special case, so it does not go
# through the generic strip-glow path. The 4 taps: X = RandomInt(-10,11)*0.2 (±2px, same as
# body 205), Y = RandomInt(-14,1)*0.15 ([-2.1,0] — a WIDER upward fan than body 205's [-1.5,0]).
_HEAD211_4TAP = 211
_HEAD211_4TAP_COLOR = (100, 100, 100, 0)
# representative integer fan for head 211 (idle Main.rand has no fixed phase — same UTIME=0-style
# convention as body 205 / dye.py). First pass on-grid; extras fan out: X up to ±2 (the x0.2
# range), Y up to -2 (the x0.15 range reaches -2.1 → -2, the upward-only spread of head 211).
_HEAD211_TAP4_OFFSETS = ((0, 0), (2, -1), (-2, -2), (1, -1))
# TV-screen head glowmask (head 271, GlowMask_309): a 6-colx4-row grid of 42x56 cells in a
# 252x224 sheet. The drawn rect is Frame(6,4,col,row,-2) => width 42-2 = 40 (the -2 trims
# the right gutter), so it stacks 1:1 over the 40-wide head cell. PlayerDrawLayers.cs:2381.
_TV_COLS, _TV_ROWS, _TV_CELL_W = 6, 4, 42
# Idle column = DrawPlayer_Head_GetTVScreen (PlayerDrawLayers.cs:2514): an offline still
# avatar is not in danger / low-health / a biome / wet / near town-NPCs, so it falls through
# to the default `return 3`. (num19==0 would hide the glow; 3 is the calm/default screen.)
_TV_IDLE_COL = 3
# Idle row = miscCounter % 20 / 5 (0..3), frozen to the representative frame 0 (miscCounter
# has no deterministic idle phase — same UTIME=0-style representative as dye.py). For the
# default column (3) the row is purely this counter; only column 5 keys off eye state.
_TV_IDLE_ROW = 0
# vector5 offset at idle (PlayerDrawLayers.cs:2368-2370): OffsetsPlayerHeadgear[bodyFrame.Y
# /56] with .Y-=2, then *= -(FlipVertically?1:-1). Idle bodyFrame.Y=0 => (0,2); -2 => (0,0);
# no FlipVertically => *1 => (0,0). So the TV glow draws at the plain head-cell top-left.
_TV_IDLE_VEC5 = (0, 0)
# ChickenBones coat front extension (Armor_Legs_238) carries an extra GlowMask_363 glow with
# the ChickenBones representative color (255,255,255,0)·0.9 = (229,229,229,0), drawn at the
# same leg frame + cCoat dye (DrawLongCoat, PlayerDrawLayers.cs:1826-1834). coat 251 -> 238.
_COAT_FRONT_GLOW = {238: 363}
# ChickenBones representative glow color (spec §3.2: (255,255,255,0)·Remap mid 0.9). Shared
# by the coat-238 GlowMask_363; the head-284 mask 365 entry already bakes the same value.
_CHICKENBONES_GLOW_COLOR = (229, 229, 229, 0)
_HAIR = _load_json("hair_sets.json")
# hairDye index 1..11 -> replacement [r,g,b] (or null = keep hairColor); index 0 = no
# dye, index 12 = Twilight (keeps hairColor, runs ArmorTwilight pass). hairdye_spec.md.
_HAIR_DYE_COLORS = _load_json("hair_dye_colors.json")
_TWILIGHT_HAIR_DYE = 12
_VAR = _load_json("variants.json")
# body equip slot -> long-coat leg-armor slot (robe/coat skirt); int, or
# {"male","female"} for the 5 gender-conditional bodies. See robe_extension_spec.md.
_ROBE_EXT = _load_json("robe_extensions.json")
# body equip slot -> the cape/tail/backpack/front it forces (ArmorIDs.Body.Sets
# IncludedCapeBack[/Female] / IncludedCapeFront / IncludeCapeFrontAndBack, routed through
# Back.Sets the same 3 ways as an accessory backSlot — Player.cs:35407-35458). Keyed
# {"male"|"female": {body_slot: {"back"|"tail"|"backpack"|"front": acc_slot}}}. The runtime
# applies back/front only if the accessory left that field unset, and backpack/tail always
# (matching the game). See research/backcoat_tails_spec.md "共性缺口".
_BODY_CAPE = _load_json("body_cape.json")
_FULLHAIR = set(_HAIR["fullHair"])
_HATHAIR = set(_HAIR["hatHair"])
_BACKONLY = set(_HAIR["backonly"])
_MALE = set(_VAR["male_variants"])
_FALLBACK = _VAR["fallback"]
_CELLS = _VAR["idle_cells"]

# player layer -> appearance color key (None = untinted, e.g. eye whites)
_LAYER_TINT: dict[int, str | None] = {
    0: "skin", 1: None, 2: "eye", 3: "skin", 4: "under", 5: "skin", 6: "shirt",
    7: "skin", 8: "under", 10: "skin", 11: "pants", 12: "shoe", 13: "shirt",
    14: "shirt", 15: "skin",
}


# ── primitives ──────────────────────────────────────────────────────
@functools.cache
def _sheet(name: str) -> np.ndarray | None:
    p = ASSETS / (name + ".png")
    return read_png(str(p)) if p.exists() else None


def _frame(name: str, cell: int) -> np.ndarray:
    """Extract the 40x56 idle cell from a sheet -> (FH,FW,4) uint8 (zeros if absent).
    Column sheets (w<=40) always use the top frame; grids index by cell."""
    sheet = _sheet(name)
    out = np.zeros((FH, FW, 4), np.uint8)
    if sheet is None:
        return out
    w = sheet.shape[1]
    cols = max(1, w // FW)
    if w <= FW:
        cell = 0
    cx, cy = (cell % cols) * FW, (cell // cols) * FH
    sub = sheet[cy:cy + FH, cx:cx + FW]
    out[:sub.shape[0], :sub.shape[1]] = sub
    return out


def _frame_geom(
    name: str, cell: int,
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """Cell geometry for noise dyes: (src_rect=(x,y,40,56), sheet_size=(W,H)).

    Mirrors _frame's cell placement so the noise uv lands on the right cell of the
    360x224 armor grid. Missing sheet / column sheet -> the cell is its own sheet."""
    sheet = _sheet(name)
    if sheet is None:
        return (0, 0, FW, FH), (FW, FH)
    h, w = sheet.shape[0], sheet.shape[1]
    cols = max(1, w // FW)
    if w <= FW:
        cell = 0
    cx, cy = (cell % cols) * FW, (cell // cols) * FH
    return (cx, cy, FW, FH), (w, h)


# ── accessory idle frame extraction ─────────────────────────────────
def _acc_strip_frame(
    name: str,
) -> tuple[np.ndarray | None, tuple[int, int, int, int], tuple[int, int]]:
    """A strip / shield accessory idle frame (40x1120 — shields may be 44 wide).
    Idle = frame 0 = top (0,0,texW,56). Returns (frame|None, src_rect, sheet_size).
    The frame width is the FULL texture width (shields hang ≤4px past the 40-cell)."""
    sheet = _sheet(name)
    if sheet is None:
        return None, (0, 0, FW, FH), (FW, FH)
    h, w = sheet.shape[0], sheet.shape[1]
    frame = sheet[:FH, :w].copy()
    return frame, (0, 0, w, FH), (w, h)


def _acc_balloon_frame(
    name: str,
) -> tuple[np.ndarray | None, tuple[int, int, int, int], tuple[int, int]]:
    """Normal balloon idle frame: a 52x224 4-frame strip -> frame 0 = (0,0,52,56).
    (Animated in-game on a wall-clock timer; we pin frame 0 — a representative still.)
    Balloon 18 (torso-framed, 40x1120) is handled as a strip accessory elsewhere."""
    sheet = _sheet(name)
    if sheet is None:
        return None, (0, 0, 52, FH), (52, FH)
    h, w = sheet.shape[0], sheet.shape[1]
    frame = sheet[:FH, :w].copy()
    return frame, (0, 0, w, FH), (w, h)


def _acc_wing_frame(
    name: str, n_frames: int, *, crop: bool = False,
) -> tuple[np.ndarray | None, tuple[int, int, int, int], tuple[int, int]]:
    """Wing idle frame 0 of a vertical N-frame strip: (0,0,W,H/N). For a grounded
    standing player wingFrame==0 (folded). `crop` trims the right/bottom 2px
    (rectangle.Width-=2; Height-=2 — wings 47/49/51). Returns (frame|None, src_rect,
    sheet_size); src_rect/sheet_size feed the dye and reflect the (possibly cropped) frame."""
    sheet = _sheet(name)
    if sheet is None:
        return None, (0, 0, FW, FH), (FW, FH)
    h, w = sheet.shape[0], sheet.shape[1]
    fh = h // max(1, n_frames)
    fw = w
    if crop:
        fw, fh = fw - 2, fh - 2
    frame = sheet[:fh, :fw].copy()
    return frame, (0, 0, fw, fh), (w, h)


def _tint(layer: np.ndarray, rgb: tuple[int, int, int] | None) -> np.ndarray:
    if rgb is None:
        return layer
    out = layer.copy()
    f = out[..., :3].astype(np.uint16)
    f[..., 0] = f[..., 0] * rgb[0] // 255
    f[..., 1] = f[..., 1] * rgb[1] // 255
    f[..., 2] = f[..., 2] * rgb[2] // 255
    out[..., :3] = f.astype(np.uint8)
    return out


def _over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """src over dst, straight alpha, vectorized. Same shape. Mutates and returns dst."""
    sa = src[..., 3:4].astype(np.float64) / 255.0
    da = dst[..., 3:4].astype(np.float64) / 255.0
    oa = sa + da * (1.0 - sa)
    safe = np.where(oa > 0, oa, 1.0)
    s_rgb = src[..., :3].astype(np.float64)
    d_rgb = dst[..., :3].astype(np.float64)
    out_rgb = (s_rgb * sa + d_rgb * da * (1.0 - sa)) / safe
    dst[..., :3] = np.clip(out_rgb + 0.5, 0, 255).astype(np.uint8)
    dst[..., 3:4] = np.clip(oa * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return dst


def _over_at(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> np.ndarray:
    """Composite `src` onto `dst` with its top-left at (x, y) in dst coords, clipping
    to the dst bounds. (x, y) may be negative / past the edge. Mutates and returns dst."""
    dh, dw = dst.shape[0], dst.shape[1]
    sh, sw = src.shape[0], src.shape[1]
    dx0, dy0 = max(0, x), max(0, y)
    dx1, dy1 = min(dw, x + sw), min(dh, y + sh)
    if dx0 >= dx1 or dy0 >= dy1:
        return dst                      # fully off-canvas
    sx0, sy0 = dx0 - x, dy0 - y
    sub = src[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    _over(dst[dy0:dy1, dx0:dx1], sub)
    return dst


def _over_glow(
    dst: np.ndarray, glow: np.ndarray, color: tuple[int, int, int, int],
) -> np.ndarray:
    """Composite an equipment glow frame onto `dst` with XNA premultiplied AlphaBlend
    (glowmask_spec.md §4.3). `glow` is the STRAIGHT-alpha glow texture (already dyed);
    `color` = (r,g,b,a) glow tint (0..255).

    Unlike `_over` (straight-over), this is additive-aware: where the glow color's alpha
    `ca == 0` (the common case) the layer is PURELY ADDITIVE — it brightens the canvas
    without occluding it (straight-over would drop the whole layer). Where `ca > 0`
    (A=60/100/127/150/180/255) it both adds AND partially occludes (premult `over`);
    A=255 is a fully opaque cover (body 238/260/291, etc. — not an outline). Same shape
    as `dst`. Mutates and returns `dst`."""
    cr, cg, cb, ca = color
    ta = glow[..., 3:4].astype(np.float64) / 255.0           # texture alpha (straight)
    cvec = np.array([cr, cg, cb], dtype=np.float64) / 255.0
    # straight glow rgb -> premultiplied, then * color rgb (per-channel, incl. A):
    src_rgb = glow[..., :3].astype(np.float64) * ta * cvec[None, None, :]  # premult src
    src_a = ta * (ca / 255.0)                                 # final src alpha
    # premultiplied AlphaBlend onto dst (carry dst through premult and back):
    da = dst[..., 3:4].astype(np.float64) / 255.0
    out_a = src_a + da * (1.0 - src_a)
    d_rgb = dst[..., :3].astype(np.float64)
    out_rgb_premult = src_rgb + (d_rgb * da) * (1.0 - src_a)
    safe = np.where(out_a > 0, out_a, 1.0)
    dst[..., :3] = np.clip(out_rgb_premult / safe + 0.5, 0, 255).astype(np.uint8)
    dst[..., 3:4] = np.clip(out_a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return dst


def _over_glow_at(
    dst: np.ndarray, glow: np.ndarray, color: tuple[int, int, int, int], x: int, y: int,
) -> np.ndarray:
    """`_over_glow` with the glow frame's top-left at (x, y), clipped to dst bounds."""
    dh, dw = dst.shape[0], dst.shape[1]
    sh, sw = glow.shape[0], glow.shape[1]
    dx0, dy0 = max(0, x), max(0, y)
    dx1, dy1 = min(dw, x + sw), min(dh, y + sh)
    if dx0 >= dx1 or dy0 >= dy1:
        return dst
    sx0, sy0 = dx0 - x, dy0 - y
    sub = glow[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    _over_glow(dst[dy0:dy1, dx0:dx1], sub, color)
    return dst


def _crop_to_content(canvas: np.ndarray, margin: int = _CROP_MARGIN) -> np.ndarray:
    """Crop the padded canvas to the bounding box of its non-transparent pixels
    (alpha > 0) plus a uniform `margin` on each side, clamped to the canvas bounds.

    A no-accessory character crops back to ~40x56 (its body cell); wings/balloons/
    capes extend the box only as far as their actual content. If the canvas is fully
    transparent, returns a minimal 1x1 transparent frame."""
    ys, xs = np.nonzero(canvas[..., 3] > 0)
    if ys.size == 0:
        return np.zeros((1, 1, 4), np.uint8)   # all-transparent edge case
    h, w = canvas.shape[0], canvas.shape[1]
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(h, int(ys.max()) + 1 + margin)
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(w, int(xs.max()) + 1 + margin)
    return canvas[y0:y1, x0:x1]


def _packed_rgb(v: int | None) -> tuple[int, int, int] | None:
    if v is None:
        return None
    u = int(v) & 0xFFFFFFFF
    return (u & 0xFF, (u >> 8) & 0xFF, (u >> 16) & 0xFF)


# ── resolution helpers ──────────────────────────────────────────────
def _resolve_player(var: int, layer: int) -> str | None:
    for v in _FALLBACK.get(str(var), [var, 0]):
        name = f"Player_{v}_{layer}"
        if _sheet(name) is not None:
            return name
    return None


def _hair_mode(head_slot: int | None) -> str:
    if head_slot is None:
        return "full"
    if head_slot in _FULLHAIR:
        return "full"
    if head_slot in _HATHAIR:
        return "hat"
    if head_slot in _BACKONLY:
        return "backonly"
    return "none"


def _back_hair_style(hair_idx: int) -> bool:
    """Exact `backHairDraw` predicate from Player.GetHairSettings (Player.cs:16787).
    Gates both back-hair visibility AND the 26px forehead clip on the FRONT pass:
    when False, the front hair is drawn full-height (long hair drapes to the chest)."""
    if hair_idx in _BACK_HAIR_FORCED:
        return True
    return hair_idx in _BACK_HAIR_RANGE and hair_idx not in _BACK_HAIR_EXCLUDED


def _hair_tint_color(
    hair_dye: int, hair_rgb: tuple[int, int, int] | None,
) -> tuple[int, int, int] | None:
    """Hair tint (GetColor): hairDye 1..11 REPLACE hairColor with a per-index color
    (null entry = keep hairColor); 0 and 12 keep hairColor. See hairdye_spec.md §2."""
    rgb = _HAIR_DYE_COLORS.get(str(hair_dye))
    if rgb is None:  # idx 0/12 or a null (hairColor-derived) legacy dye
        return hair_rgb
    return (rgb[0], rgb[1], rgb[2])


def _slot_of(item: dict[str, Any] | None, kind: str) -> int | None:
    """item = {netId,...}; kind in head/body/legs -> equip slot or None."""
    if not item:
        return None
    nid = item.get("netId") or 0
    if not nid:
        return None
    return _EQUIP_SLOTS.get(str(nid), {}).get(kind)


def _longcoat_ext_slot(body_slot: int | None, *, male: bool) -> int | None:
    """body equip slot -> long-coat leg-armor extension slot (robe/coat skirt)."""
    if body_slot is None:
        return None
    v = _ROBE_EXT.get(str(body_slot))
    if v is None:
        return None
    return (v["male"] if male else v["female"]) if isinstance(v, dict) else v


def _cell(key: str, *, male: bool) -> int:
    c = _CELLS[key]
    if isinstance(c, dict):
        return c["male"] if male else c["female"]
    return c


def _displayed_piece(
    part: str,
    equipment: dict[str, Any],
    vanity: dict[str, Any],
) -> dict[str, Any] | None:
    """Per-part displayed piece: vanity overrides equipment when present."""
    v = vanity.get(part)
    return v if (v and v.get("netId")) else equipment.get(part)


class _Compositor:
    """Holds the resolved render state and stacks layers onto one canvas."""

    def __init__(self, appearance: dict[str, Any]) -> None:
        self.var = int(appearance["skinVariant"])
        self.male = self.var in _MALE
        self.hair = int(appearance["hair"])
        self.colors: dict[str | None, tuple[int, int, int] | None] = {
            "skin": _packed_rgb(appearance.get("skinColor")),
            "eye": _packed_rgb(appearance.get("eyeColor")),
            "shirt": _packed_rgb(appearance.get("shirtColor")),
            "under": _packed_rgb(appearance.get("underShirtColor")),
            "pants": _packed_rgb(appearance.get("pantsColor")),
            "shoe": _packed_rgb(appearance.get("shoeColor")),
            None: None,
        }
        self.hair_rgb = _packed_rgb(appearance.get("hairColor"))
        self.hair_dye = int(appearance.get("hairDye") or 0)
        self.cells = {
            "torso": _cell("torso", male=self.male),
            "front_arm": _CELLS["front_arm"],
            "back_arm": _CELLS["back_arm"],
            "front_shoulder": _cell("front_shoulder", male=self.male),
            "back_shoulder": _cell("back_shoulder", male=self.male),
            "col": 0,
        }
        # padded canvas; the 40x56 body cell is anchored at (PAD_L, PAD_T).
        self.canvas = np.zeros((CANVAS_H, CANVAS_W, 4), np.uint8)

    def _over_cell(self, frame: np.ndarray, lx: int = 0, ly: int = 0) -> None:
        """Composite a frame at cell-local (lx, ly) (0,0 = body-cell top-left)."""
        _over_at(self.canvas, frame, PAD_L + lx, PAD_T + ly)

    def draw_player(self, layer: int, cell_key: str) -> None:
        name = _resolve_player(self.var, layer)
        if name:
            tinted = _tint(
                _frame(name, self.cells[cell_key]),
                self.colors[_LAYER_TINT.get(layer)],
            )
            self._over_cell(tinted)

    def draw_armor(
        self,
        name: str | None,
        cell_key: str,
        dye_spec: dict[str, Any] | None = None,
        *,
        tint: tuple[int, int, int] | None = None,
    ) -> None:
        """Draw an armor sheet's idle cell. Normally untinted white (colorArmorHead/Body/
        Legs == white in the no-world path) + its dye. `tint` overrides the white with a
        player color (UseSkinColor head armor draws with skinColor and the skin shader, so
        the caller passes skinColor and no dye_spec — PlayerDrawLayers.cs:2145/2223)."""
        if name and _sheet(name) is not None:
            cell = self.cells[cell_key]
            buf = _frame(name, cell)
            if tint is not None:
                buf = _tint(buf, tint)
            if dye_spec:
                src_rect, sheet_size = _frame_geom(name, cell)
                buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
            self._over_cell(buf)

    # ── equipment glowmask layers (research/glowmask_spec.md) ──
    def _glow_color(self, raw: Any) -> tuple[int, int, int, int] | None:
        """Resolve a glow-color table entry (RGBA list or the 'arkhalis' sentinel) to an
        (r,g,b,a) tuple. 'arkhalis' = (underShirtColor.rgb, A=180); a missing undershirt
        color falls back to white. Returns None to skip (PackedValue==0)."""
        if raw == "arkhalis":
            rgb = self.colors.get("under") or (255, 255, 255)
            return (rgb[0], rgb[1], rgb[2], _GLOW_ARKHALIS_ALPHA)
        if isinstance(raw, (list, tuple)) and len(raw) == _RGBA_LEN:
            r, g, b, a = (int(v) for v in raw)
            if (r | g | b | a) == 0:           # Color.Transparent -> skip
                return None
            return (r, g, b, a)
        return None

    def _over_glow_passes(
        self, buf: np.ndarray, color: tuple[int, int, int, int],
        lx: int, ly: int, offsets: tuple[tuple[int, int], ...],
    ) -> None:
        """Composite a (dyed) glow frame `len(offsets)` times, each shifted by the integer
        offset (the rounded representative of the in-game sub-pixel Main.rand jitter)."""
        for ox, oy in offsets:
            _over_glow_at(self.canvas, buf, color, lx + ox, ly + oy)

    def draw_body_glow(
        self, name: str, cell_key: str, color: tuple[int, int, int, int],
        dye_spec: dict[str, Any] | None, *, jitter: int = 1,
    ) -> None:
        """Composite a body/arm composite-glow sub-part: the SAME ArmorBody sheet at the
        sub-part's colored cell + 36 (lower half, sourceRect.Y += 224), tinted by `color`
        and still carrying the base body dye (glowmask_spec.md §5.1). Additive-aware.

        `jitter` > 1 draws the glow that many times at representative integer offsets
        (body 227 Nebula draws each composite glow twice with a ±1.25px Main.rand jitter,
        PlayerDrawLayers.cs:63-76/90-101); idle has no deterministic phase, so we use the
        fixed `_JITTER_OFFSETS` fan (first pass on-grid, extras fan out ~1px)."""
        if _sheet(name) is None:
            return
        glow_cell = self.cells[cell_key] + _GLOW_CELL_DELTA
        buf = _frame(name, glow_cell)
        if buf[..., 3].max() == 0:             # no glow data in this cell -> nothing to add
            return
        if dye_spec:
            src_rect, sheet_size = _frame_geom(name, glow_cell)
            buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_glow_passes(buf, color, PAD_L, PAD_T, _JITTER_OFFSETS[:max(1, jitter)])

    def draw_body205_frontarm_4tap(
        self, name: str, dye_spec: dict[str, Any] | None,
    ) -> None:
        """body 205's extra front-arm shimmer (DrawCompositeArmorPiece FrontArm branch,
        PlayerDrawLayers.cs:118-135): a 4-tap additive (100,100,100,0) pass over the
        front-arm composite glow (lower half), independent of armGlowColor. Idle uses the
        representative `_TAP4_OFFSETS` fan (the per-frame offsets are Main.rand-driven)."""
        if _sheet(name) is None:
            return
        glow_cell = self.cells["front_arm"] + _GLOW_CELL_DELTA
        buf = _frame(name, glow_cell)
        if buf[..., 3].max() == 0:
            return
        if dye_spec:
            src_rect, sheet_size = _frame_geom(name, glow_cell)
            buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_glow_passes(buf, _BODY205_4TAP_COLOR, PAD_L, PAD_T, _TAP4_OFFSETS)

    def draw_head211_4tap(
        self, name: str, dye_spec: dict[str, Any] | None,
    ) -> None:
        """head 211's hardcoded 4-tap shimmer (PlayerDrawLayers.cs:2403-2415): a 4-tap
        additive (100,100,100,0) pass of the INDEPENDENT GlowMask_241 strip over the head
        cell (idle frame 0 = (0,0,40,56), NOT the composite +224 lower half — head glow is
        its own strip), carrying the head dye. Same mechanism as body 205's front-arm 4-tap
        but in the head group; idle uses the representative `_HEAD211_TAP4_OFFSETS` fan (the
        per-frame offsets are Main.rand-driven, [-2,+2] in X / [-2,0] in Y)."""
        cell = self.cells["col"]
        buf = _frame(name, cell)
        if buf[..., 3].max() == 0:
            return
        if dye_spec:
            src_rect, sheet_size = _frame_geom(name, cell)
            buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_glow_passes(buf, _HEAD211_4TAP_COLOR, PAD_L, PAD_T, _HEAD211_TAP4_OFFSETS)

    def draw_strip_glow(
        self, name: str, cell_key: str, color: tuple[int, int, int, int],
        dye_spec: dict[str, Any] | None, *, jitter: int = 1,
    ) -> None:
        """Composite an independent head/legs glowmask strip (Glow_{id}.png, 40x1120) at
        the same idle cell/frame as its base armor, tinted by `color` and still carrying
        the base armor dye (glowmask_spec.md §5.2). Additive-aware.

        `jitter` > 1 draws it that many times at representative integer offsets (head 240
        mask 273 and legs 210 mask 274 each draw twice with a ±1.25px Main.rand jitter,
        PlayerDrawLayers.cs:2389-2394 / 1559); idle representative = `_JITTER_OFFSETS`."""
        if _sheet(name) is None:
            return
        cell = self.cells[cell_key]
        buf = _frame(name, cell)
        if buf[..., 3].max() == 0:
            return
        if dye_spec:
            src_rect, sheet_size = _frame_geom(name, cell)
            buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_glow_passes(buf, color, PAD_L, PAD_T, _JITTER_OFFSETS[:max(1, jitter)])

    def draw_tv_head_glow(
        self, name: str, color: tuple[int, int, int, int],
        dye_spec: dict[str, Any] | None,
    ) -> None:
        """The TV-screen head glow (head 271, GlowMask_309): pick the 6x4-grid cell
        (col = GetTVScreen idle = 3, row = miscCounter frozen = 0), drawn rect width 40
        (the Frame(...,-2) gutter trim), at the head-cell top-left + vector5 (=(0,0) idle).
        Carries the head dye, additive-aware. PlayerDrawLayers.cs:2357-2385."""
        sheet = _sheet(name)
        if sheet is None:
            return
        # grid cell rect: x = col*42 (-2 trims to 40 wide), y = row*56, size 40x56.
        cx, cy = _TV_IDLE_COL * _TV_CELL_W, _TV_IDLE_ROW * FH
        buf = np.zeros((FH, FW, 4), np.uint8)
        sub = sheet[cy:cy + FH, cx:cx + FW]
        buf[:sub.shape[0], :sub.shape[1]] = sub
        if buf[..., 3].max() == 0:
            return
        if dye_spec:
            buf = apply_dye(
                buf, dye_spec, src_rect=(cx, cy, FW, FH),
                sheet_size=(sheet.shape[1], sheet.shape[0]))
        vx, vy = _TV_IDLE_VEC5
        _over_glow_at(self.canvas, buf, color, PAD_L + vx, PAD_T + vy)

    def draw_hair(self, hair_file: str, clip_rows: int | None = None) -> None:
        # Step A: tint. hairDye 1..11 replace hairColor; 0/12 keep it (hairdye_spec §2)
        tint = _hair_tint_color(self.hair_dye, self.hair_rgb)
        hf = _tint(_frame(hair_file, 0), tint)
        # Step B: Twilight (idx 12) runs the ArmorTwilight noise pixel pass on the hair
        # frame (uColor=(0.5,0.1,1.0)); the hair sheet is its own uImageSize0.
        if self.hair_dye == _TWILIGHT_HAIR_DYE:
            src_rect, sheet_size = _frame_geom(hair_file, 0)
            hf = apply_dye(hf, {"pass": "ArmorTwilight", "color": [0.5, 0.1, 1.0]},
                           src_rect=src_rect, sheet_size=sheet_size)
        if clip_rows is not None:
            hf[clip_rows:] = 0
        self._over_cell(hf)

    # ── accessory draws (untinted white + the accessory's own dye) ──
    def draw_acc_strip(
        self, name: str, dye_spec: dict[str, Any] | None, *,
        hair_color: bool = False, offset: tuple[int, int] = (0, 0),
    ) -> None:
        """A strip/shield/torso-framed accessory: idle frame 0 = (0,0,texW,56), top-left
        aligned in the cell at `offset` (default (0,0)). Untinted white (display-doll path)
        + its dye; beards with UseHairColor tint by the player's hair color instead
        (accessories_spec §12). `offset` carries the per-category draw offset (e.g. face 19's
        GetFaceDrawOffset (0,-6); most categories are 0 at idle)."""
        frame, src_rect, sheet_size = _acc_strip_frame(name)
        if frame is None:
            return
        if hair_color:
            frame = _tint(frame, _hair_tint_color(self.hair_dye, self.hair_rgb))
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_cell(frame, *offset)

    def draw_acc_balloon(self, name: str, dye_spec: dict[str, Any] | None) -> None:
        """A normal (non-torso) balloon: 52x56 frame 0, cell-local top-left
        (-6, -4) (floats above/left of the body — accessories_spec §3)."""
        frame, src_rect, sheet_size = _acc_balloon_frame(name)
        if frame is None:
            return
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_cell(frame, *_BALLOON_OFFSET)

    def draw_armorset_backpack(
        self, name: str, offset: tuple[int, int], dye_spec: dict[str, Any] | None,
    ) -> None:
        """An armor-set backpack (Extra_212/213, DrawPlayer_08_Backpacks:446/458): a
        5-frame vertical strip drawn at idle frame 0 (Frame(1,5,0,0)) with the body dye and
        the half-translucent color (250,250,250,200). sb.Draw multiplies the DrawData color
        per-channel into the texture, so we scale RGB by 250/255 AND alpha by 200/255 (idle
        GetImmuneAlphaPure/*stealth are 1.0). `offset` is its cell-local top-left."""
        sheet = _sheet(name)
        if sheet is None:
            return
        h, w = sheet.shape[0], sheet.shape[1]
        fh = h // 5                                  # 5-frame vertical strip
        frame = sheet[:fh, :w].copy()
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=(0, 0, w, fh), sheet_size=(w, h))
        # the (250,250,250,200) draw color: RGB scaled by 250/255, alpha by 200/255.
        cr, cg, cb, ca = _ARMORSET_BACKPACK_COLOR
        frame = _tint(frame, (cr, cg, cb))
        frame[..., 3] = (frame[..., 3].astype(np.uint16) * ca // 255).astype(np.uint8)
        self._over_cell(frame, *offset)

    def draw_acc_wing(self, slot: int, dye_spec: dict[str, Any] | None) -> None:
        """A wing: idle frame 0 (folded) of the vertical N-frame strip, at the
        cell-local offset derived from DrawPlayer_09_Wings (accessories_spec §1).
        AlwaysAnimated wings draw nothing grounded and are filtered by the caller.

        Most wings use the default formula center = (11+num13, 33+num12) (= vector's
        cell-local center (20,31) + (num13-9, num12+2)); wings 47/49/50/51 use their own
        bespoke center + frame crop (_WING_BESPOKE)."""
        name = f"Wings_{slot}"
        n = _WING_FRAMES.get(slot, 4)
        bespoke = _WING_BESPOKE.get(slot)
        crop = bespoke[2] if bespoke else False
        frame, src_rect, sheet_size = _acc_wing_frame(name, n, crop=crop)
        if frame is None:
            return
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        w, fh = frame.shape[1], frame.shape[0]
        if bespoke:
            cx, cy, _ = bespoke
        else:
            num13, num12 = _WING_OFFSET.get(slot, (0, 0))
            cx, cy = 11 + num13, 33 + num12
        self._over_cell(frame, cx - w // 2, cy - fh // 2)

    def draw_acc_hand(self, name: str, cell: int, dye_spec: dict[str, Any] | None) -> None:
        """A composite hand accessory (on/off): 360x224 9x4 grid, drawn at the same
        idle arm cell as the body arm (front=2, back=20), top-left aligned (§5/§10)."""
        if _sheet(name) is None:
            return
        frame = _frame(name, cell)
        if dye_spec:
            src_rect, sheet_size = _frame_geom(name, cell)
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_cell(frame)

    def draw_acc_front_half(
        self, name: str, dye_spec: dict[str, Any] | None, *, front: bool,
        offset: tuple[int, int] = (0, 0),
    ) -> None:
        """One half of a split front accessory (accessories_spec §11): the BACK half
        (right 20px, cols [20,40)) draws behind the shield/arm; the FRONT half (left
        20px, cols [0,20)) draws over the front arm. Dye uses the full 40x56 frame rect
        (ArmorShaderData.Apply reads the drawn sub-rect — here the half we keep). `offset`
        is GetFrontDrawOffset (front 13 -> (-2,0); 0 otherwise); both halves share it."""
        frame, _src, sheet_size = _acc_strip_frame(name)
        if frame is None:
            return
        half = FW // 2
        if dye_spec:
            sx = 0 if front else half
            rect = (sx, 0, half, FH)
            dyed = apply_dye(frame, dye_spec, src_rect=rect, sheet_size=sheet_size)
            frame = dyed
        masked = frame.copy()
        if front:
            masked[:, half:] = 0          # keep the left half only
        else:
            masked[:, :half] = 0          # keep the right half only
        self._over_cell(masked, *offset)


def _resolve_hair(hair: int, head_slot: int | None) -> dict[str, Any]:
    """Resolve hair file + visibility (back/front pass) for the head armor mode."""
    mode = _hair_mode(head_slot)
    hair_file = (
        "Player_HairAlt_" if mode == "hat" else "Player_Hair_"
    ) + str(hair + 1)
    has_hair = mode != "none" and _sheet(hair_file) is not None
    is_back = has_hair and _back_hair_style(hair)
    return {
        "file": hair_file,
        "is_back": is_back,
        "draw_back": has_hair and is_back and mode in ("full", "hat", "backonly"),
        "draw_front": has_hair and mode in ("full", "hat"),
    }


def _resolve_armor(
    equipment: dict[str, Any],
    vanity: dict[str, Any],
    dye: dict[str, Any],
) -> dict[str, Any]:
    """Resolve displayed armor sheet names + dye specs per part."""
    head_slot = _slot_of(_displayed_piece("head", equipment, vanity), "head")
    body_slot = _slot_of(_displayed_piece("body", equipment, vanity), "body")
    leg_slot = _slot_of(_displayed_piece("legs", equipment, vanity), "legs")
    return {
        "head_slot": head_slot,
        "body_slot": body_slot,
        "leg_slot": leg_slot,
        "head": f"Armor_Head_{head_slot}" if head_slot else None,
        "body": f"ArmorBody_{body_slot}" if body_slot else None,
        "legs": f"Armor_Legs_{leg_slot}" if leg_slot else None,
        "head_dye": _DYES.get(str((dye.get("head") or {}).get("netId") or 0)),
        "body_dye": _DYES.get(str((dye.get("body") or {}).get("netId") or 0)),
        "leg_dye": _DYES.get(str((dye.get("legs") or {}).get("netId") or 0)),
    }


# ── accessory resolution (vanity override + hideVisuals, accessories_spec §X1/X2) ──
def _net_id(item: Any) -> int:
    """A {netId,...} accessory entry -> netId int (0 = empty slot / malformed)."""
    if not isinstance(item, dict):
        return 0
    try:
        return int(item.get("netId") or 0)
    except (TypeError, ValueError):
        return 0


def _apply_item_slots(
    resolved: dict[str, int], net_id: int,
    dye_index: dict[str, int] | None = None, k: int | None = None,
) -> None:
    """Merge one item's `<cat>Slot` values into `resolved`, replicating
    Player.UpdateVisibleAccessory IN ITEM ORDER (36467-36556). Two routings are modeled
    exactly (the rest is a direct `<cat> = slot`):

    * `backSlot` is 3-way-routed (36475-36490): DrawInBackpackLayer -> `backpack`,
      DrawInTailLayer -> `tail`, else a real back cape -> `back` AND `front` is cleared
      (36488). The clear happens BEFORE this item's own `frontSlot` is applied, so a cape
      item that carries both (e.g. CrimsonCloak back 3 + front 1) keeps BOTH; only a
      front set by an EARLIER item is dropped.
    * `frontSlot` (36491) is applied AFTER the back routing, overriding the 36488 clear.

    `dye_index`/`k` (the accessory slot index) record which dye slot last set each routed
    field, so the per-field dye reads correctly. No-op for air / no-visual-slot items."""
    cats = _ACC_SLOTS.get(str(net_id), {})

    def mark(field: str) -> None:
        if dye_index is not None and k is not None:
            dye_index[field] = k

    # back routing (36475-36490) runs BEFORE the frontSlot apply (36491), so process
    # `back` first (clearing front), then `front`, then the remaining categories.
    if "back" in cats:
        slot = int(cats["back"])
        if slot in _BACK_BACKPACK:
            dest = "backpack"
        elif slot in _BACK_TAIL:
            dest = "tail"
        else:
            dest = "back"
            resolved.pop("front", None)        # real back cape clears front (36488)
            if dye_index is not None:
                dye_index.pop("front", None)
        resolved[dest] = slot
        mark(dest)
    for cat, raw in cats.items():
        if cat == "back":
            continue                           # already routed above
        resolved[cat] = int(raw)
        mark(cat)


def _apply_body_cape(
    resolved: dict[str, int], body_slot: int | None, *, male: bool,
) -> set[str]:
    """Apply the body-armor-forced cape/tail/backpack/front (Player.cs:35407-35458),
    AFTER accessory resolution+routing: backpack/tail always override; back/front only
    when the accessory left them unset. Source: body_cape.json (ArmorIDs.Body.Sets).
    Returns the set of fields this set FROM the body armor (they are dyed with cBody, not
    an accessory dye — Player.cs:35415/35420/35425/35433)."""
    from_body: set[str] = set()
    if body_slot is None:
        return from_body
    table = _BODY_CAPE["male" if male else "female"]
    entry = table.get(str(body_slot))
    if not entry:
        return from_body
    is_pair = entry.get("pair")
    # IncludeCapeFrontAndBack (a front+back pair) only applies when BOTH back and front are
    # still unset (Player.cs:35436); the independent IncludedCapeBack/Front maps don't gate.
    if is_pair and ("back" in resolved or "front" in resolved):
        return from_body
    for field, slot in entry.items():
        if field == "pair":
            continue
        if field in ("backpack", "tail"):
            resolved[field] = int(slot)            # always overrides
            from_body.add(field)
        elif field not in resolved:                # back/front: only if accessory left unset
            resolved[field] = int(slot)
            from_body.add(field)
    return from_body


def _resolve_accessories(
    accessories: list[Any] | None,
    vanity_accessories: list[Any] | None,
    accessory_dyes: list[Any] | None,
    hide_visuals: int,
    *, male: bool, body_slot: int | None = None,
    body_dye: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replicate Player.UpdateVisibleAccessories (accessories_spec §X1): iterate the 7
    functional acc slots (low->high index, higher index overwrites per category), then
    the 7 vanity slots (which unconditionally override). A hideVisuals-hidden functional
    acc is skipped, but its vanity twin still draws. Also resolves the per-category dye
    (functional acc k AND its vanity twin both read accessoryDyes[k]).

    Each item's back slot is 3-way-routed to back/tail/backpack in item order
    (Player.cs:36475; a real back cape also clears a prior front), then the body armor's
    own cape/tail/backpack/front is applied (Player.cs:35407, body_cape.json), and
    ChickenBonesRobe (netId 5587, which carries no *Slot) sets coat=251.

    Returns {"slots": {category: slot}, "dyes": {category: dye_spec|None}, "coat", "coat_dye"}.
    """
    acc = accessories or []
    van = vanity_accessories or []
    dyes = accessory_dyes or []
    resolved: dict[str, int] = {}
    # category -> the dye index (0..6) whose item last set it (so we read the right dye).
    dye_index: dict[str, int] = {}
    coat: int | None = None
    # coat (item 5587) reads its own slot's dye via the cCoat shader (Player.cs:9466);
    # remember which dye index the robe came from.
    coat_dye_index: int | None = None

    # 1) functional accessories 0..6 (armor[3+k]); skip if hidden by hideVisuals bit 3+k.
    for k in range(7):
        if hide_visuals & (1 << (_HIDE_BIT_BASE + k)):
            continue  # hidden functional acc (incl. wings: still grounded => velocity.Y==0)
        nid = _net_id(acc[k]) if k < len(acc) else 0
        if nid:
            _apply_item_slots(resolved, nid, dye_index, k)
            if nid == _CHICKENBONES_ROBE_NETID:
                coat, coat_dye_index = _CHICKENBONES_COAT, k
    # 2) social / vanity accessories 0..6 (armor[13+k]) — always draw, override functional.
    for k in range(7):
        nid = _net_id(van[k]) if k < len(van) else 0
        if nid:
            _apply_item_slots(resolved, nid, dye_index, k)
            if nid == _CHICKENBONES_ROBE_NETID:
                coat, coat_dye_index = _CHICKENBONES_COAT, k

    # add the body armor's own forced cape/tail/backpack/front (after accessory routing).
    from_body = _apply_body_cape(resolved, body_slot, male=male)

    # GlassSlipper male->female shoe remap (UpdateVisibleAccessory 36502-36505).
    if not male and resolved.get("shoe") in _SHOE_MALE_TO_FEMALE:
        resolved["shoe"] = _SHOE_MALE_TO_FEMALE[resolved["shoe"]]

    # per-category dye spec from the dye-slot that the category's item came from.
    cat_dyes: dict[str, Any] = {}
    for cat, k in dye_index.items():
        nid = _net_id(dyes[k]) if k < len(dyes) else 0
        cat_dyes[cat] = _DYES.get(str(nid)) if nid else None
    # body-armor-forced capes (back/tail/backpack/front) are dyed with the BODY dye
    # (cBack/cTail/cBackpack/cFront = cBody, Player.cs:35415..35433), not an accessory dye.
    for field in from_body:
        cat_dyes[field] = body_dye
    # the coat (cCoat) dye comes from item 5587's own accessory-dye slot.
    coat_dye = None
    if coat_dye_index is not None:
        nid = _net_id(dyes[coat_dye_index]) if coat_dye_index < len(dyes) else 0
        coat_dye = _DYES.get(str(nid)) if nid else None
    return {"slots": resolved, "dyes": cat_dyes, "coat": coat, "coat_dye": coat_dye}


# ── main render ─────────────────────────────────────────────────────
def render_character(
    appearance: dict[str, Any],
    equipment: dict[str, Any] | None = None,
    vanity: dict[str, Any] | None = None,
    dye: dict[str, Any] | None = None,
    scale: int = 1,
    *,
    accessories: list[Any] | None = None,
    vanity_accessories: list[Any] | None = None,
    accessory_dyes: list[Any] | None = None,
) -> bytes:
    """Render a Terraria character to PNG bytes (transparent background).

    appearance: dict with skinVariant/hair/hairDye + packed-int colors (and the optional
        ``hideVisuals`` accessory-hide bitmask).
    equipment/vanity/dye: dicts {head,body,legs: {netId,...}} or None.
    accessories/vanity_accessories/accessory_dyes: the appearance API's 7-element
        accessory lists (each element {netId,...}; empty slot = zero) or None. Vanity
        accessories override functional ones per slot; hideVisuals-hidden functional
        accessories are dropped (their vanity twin still draws); each slot's dye reuses
        the armor-dye shader. See research/accessories_spec.md.
    Returns PNG bytes of the rendered character, cropped to its non-transparent content
    (plus a small margin) and then upscaled by ``scale``. The output size is therefore
    content-dependent — ~40x56 for a plain character, larger for wings/balloons/capes —
    not a fixed padded frame. (Compositing still uses a padded canvas internally so
    accessories that overflow the body cell stay aligned.)
    """
    equipment = equipment or {}
    vanity = vanity or {}
    dye = dye or {}

    comp = _Compositor(appearance)
    armor = _resolve_armor(equipment, vanity, dye)
    armor_head, armor_body, armor_legs = armor["head"], armor["body"], armor["legs"]
    body_dye = armor["body_dye"]

    # equipment glowmask colors (glowmask_spec.md §2): body/arm ride the ArmorBody lower
    # half; head/legs use independent Glow_{id} strips. None = no glow for that slot.
    body_slot_str = str(armor["body_slot"]) if armor["body_slot"] else None
    body_glow = comp._glow_color(_GLOW_BODY.get(body_slot_str)) if body_slot_str else None
    arm_glow = comp._glow_color(_GLOW_ARM.get(body_slot_str)) if body_slot_str else None
    head_glow_entry = _GLOW_HEAD.get(str(armor["head_slot"])) if armor["head_slot"] else None
    legs_glow_entry = _GLOW_LEGS.get(str(armor["leg_slot"])) if armor["leg_slot"] else None

    # per-sub-part jitter pass counts (body 227 Nebula = 2 for torso + arm sub-parts).
    body_jitter = _GLOW_BODY_JITTER.get(body_slot_str, 1) if body_slot_str else 1
    arm_jitter = _GLOW_ARM_JITTER.get(body_slot_str, 1) if body_slot_str else 1

    def draw_body_arm_glow(cell_key: str, *, arm: bool) -> None:
        """Draw the composite-glow for one body sub-part right after its colored draw.
        body 227 draws each composite glow in 2 jittered passes (glowmask_spec.md §5.1)."""
        color = arm_glow if arm else body_glow
        if armor_body and color is not None:
            comp.draw_body_glow(
                armor_body, cell_key, color, body_dye,
                jitter=arm_jitter if arm else body_jitter)
        # body 205's extra 4-tap front-arm shimmer (independent of armGlowColor).
        if arm and cell_key == "front_arm" and armor["body_slot"] == _BODY205_FRONTARM_4TAP:
            comp.draw_body205_frontarm_4tap(armor_body, body_dye)

    hide_visuals = int(appearance.get("hideVisuals") or 0)
    acc = _resolve_accessories(
        accessories, vanity_accessories, accessory_dyes, hide_visuals,
        male=comp.male, body_slot=armor["body_slot"], body_dye=body_dye)
    acc_slots: dict[str, int] = acc["slots"]
    acc_dyes: dict[str, Any] = acc["dyes"]
    # coat (ChickenBonesRobe -> 251): its two long-coat extension pieces share the cCoat
    # dye (the dye of the slot holding item 5587). See research/backcoat_tails_spec.md §1/2.
    coat: int | None = acc["coat"]
    coat_dye: dict[str, Any] | None = acc["coat_dye"]

    def sheet_for(cat: str, prefix: str) -> str | None:
        slot = acc_slots.get(cat)
        return f"{prefix}{slot}" if slot else None

    hair = _resolve_hair(comp.hair, armor["head_slot"])
    hair_file, is_back = hair["file"], hair["is_back"]
    # a face acc with PreventHairDraw hides the front hair (accessories_spec §8).
    draw_front_hair = hair["draw_front"] and (
        acc_slots.get("face") not in _FACE_PREVENT_HAIR)

    # ===== BEHIND BODY (backpacks / tails / wings / back cape / balloon) =====
    # The whole back-to-front group sits in the BEHIND-BODY band (LegacyPlayerRenderer.cs
    # :177-187). Backpacks (08) and Tails (08_1) are the rear-most, BEFORE wings (09).
    # backpack (08_Backpacks): armor-set Extra_212/213 backpacks, then the AccBack-textured
    # `backpack` field (Acc_Back_*, with the body dye / cBackpack). PlayerDrawLayers.cs:444.
    for triple, extra_id, off in _ARMORSET_BACKPACKS:
        if (armor["head_slot"], armor["body_slot"], armor["leg_slot"]) == triple:
            comp.draw_armorset_backpack(f"Extra_{extra_id}", off, body_dye)
    backpack_slot = acc_slots.get("backpack")
    if backpack_slot:
        comp.draw_acc_strip(f"Acc_Back_{backpack_slot}", acc_dyes.get("backpack"))
    # tail (08_1_Tails): an AccBack-textured tail (Acc_Back_*); female shifts +2*direction
    # in X (PlayerDrawLayers.cs:577-579, dir=1 idle -> +2). cTail dye.
    tail_slot = acc_slots.get("tail")
    if tail_slot:
        comp.draw_acc_strip(
            f"Acc_Back_{tail_slot}", acc_dyes.get("tail"),
            offset=(0, 0) if comp.male else (2, 0))
    # 0. wings (idle frame 0; AlwaysAnimated wings render nothing grounded -> skip).
    wing_slot = acc_slots.get("wing")
    if wing_slot and wing_slot not in _WING_ALWAYS_ANIMATED:
        comp.draw_acc_wing(wing_slot, acc_dyes.get("wing"))
    # 1. back hair
    if hair["draw_back"]:
        comp.draw_hair(hair_file)
    # 2. back accessory (10_BackAcc): the routed `back` field is a real cape already
    #    (backpack/tail were split off in _resolve_accessories). cBack dye.
    back_slot = acc_slots.get("back")
    if back_slot:
        comp.draw_acc_strip(f"Acc_Back_{back_slot}", acc_dyes.get("back"))
    # 2b. back-head texture (DrawPlayer_01_3_BackHead, LegacyPlayerRenderer.cs:185): a few
    #     helmets have a behind-body back piece (FrontToBackID), drawn at the head cell with
    #     the body frame + head dye, behind the body.
    back_head = _HEAD_FRONT_TO_BACK.get(armor["head_slot"])
    if back_head is not None:
        comp.draw_armor(f"Armor_Head_{back_head}", "col", armor["head_dye"])
    # 3. balloon — normal balloons float behind the body; balloon 18 (torso-framed) is
    #    drawn later in front of the back arm (balloonFront).
    balloon_slot = acc_slots.get("balloon")
    if balloon_slot and balloon_slot not in _BALLOON_TORSO:
        comp.draw_acc_balloon(f"Acc_Balloon_{balloon_slot}", acc_dyes.get("balloon"))
    # 4. ArmorBackCoat (13_ArmorBackCoat, LegacyPlayerRenderer.cs:192): the long-coat BACK
    #    piece (GetMatchingBodyExtensionBack(coat); only coat 251 -> Armor_Legs_239), drawn
    #    at the idle leg frame with colorArmorBody (white) + the cCoat dye, BEHIND the body
    #    skin (before 12_Skin). See research/backcoat_tails_spec.md §1.
    coat_back_ext = _COAT_BACK_EXT.get(coat) if coat is not None else None
    if coat_back_ext is not None:
        comp.draw_armor(f"Armor_Legs_{coat_back_ext}", "col", coat_dye)

    # ===== BACK ARM group (+ off-hand composite) =====
    comp.draw_player(7, "back_arm")
    comp.draw_player(5, "back_arm")
    comp.draw_armor(armor_body, "back_arm", body_dye)
    draw_body_arm_glow("back_arm", arm=True)
    # balloon 18 (balloonFront): torso-framed, in front of the back arm.
    if balloon_slot and balloon_slot in _BALLOON_TORSO:
        comp.draw_acc_strip(f"Acc_Balloon_{balloon_slot}", acc_dyes.get("balloon"))
    # off-hand accessory (composite) is the LAST thing in the back-arm group, drawn OVER
    # the body armor's back arm (DrawPlayer_12_SkinComposite_BackArmShirt: body back arm at
    # PlayerDrawLayers.cs:1365, handoff acc at 1421).
    handoff_slot = acc_slots.get("handOff")
    if handoff_slot:
        comp.draw_acc_hand(
            f"Acc_HandsOff_{handoff_slot}", comp.cells["back_arm"],
            acc_dyes.get("handOff"))

    # ===== BODY + LEG SKIN (+ shoe accessory) =====
    body_slot = armor["body_slot"]
    leg_slot = armor["leg_slot"]
    shoe_slot = acc_slots.get("shoe")
    # IsBottomOverridden (PlayerDrawLayers.cs:1205): a robe/mermaid leg or shoe==15 fully
    # replaces the legs, so the shoe accessory AND the leg skin are suppressed. CheckPants
    # short-circuits to false when shoe==15 (CheckShoes), but CheckShoes itself still makes
    # IsBottomOverridden true — so the combined gate is (shoe==15) OR (legs in override set).
    bottom_overridden = (
        shoe_slot == _SHOE_OVERRIDE_EXCEPTION or leg_slot in _LEG_OVERRIDE_SLOTS)
    # torso skin (layer 3) is hidden by hidesTopSkin bodies (PlayerDrawSet.cs:1755).
    if body_slot not in _HIDES_TOP_SKIN_BODIES:
        comp.draw_player(3, "torso")
    # leg skin (layer 10) is hidden by hidesBottomSkin (legs/body93, PlayerDrawSet.cs:1756)
    # OR IsBottomOverridden (PlayerDrawLayers.cs:1193).
    hides_bottom_skin = (
        leg_slot in _HIDES_BOTTOM_SKIN_LEGS or body_slot == _HIDES_BOTTOM_SKIN_BODY)
    if not hides_bottom_skin and not bottom_overridden:
        comp.draw_player(10, "col")

    def draw_shoe_acc() -> None:
        # DrawPlayer_14_Shoes is guarded by !ShouldOverrideLegs_CheckPants
        # (PlayerDrawLayers.cs:1758): a leg-override slot suppresses the shoe accessory,
        # but shoe==15 (CheckShoes) short-circuits CheckPants to false so the shoe shows.
        if not shoe_slot:
            return
        if shoe_slot != _SHOE_OVERRIDE_EXCEPTION and leg_slot in _LEG_OVERRIDE_SLOTS:
            return
        # roller skates (27-30) get GetShoeDrawOffset (0,2); other shoes are 0 at idle.
        comp.draw_acc_strip(
            f"Acc_Shoes_{shoe_slot}", acc_dyes.get("shoe"),
            offset=_SHOE_DRAW_OFFSET.get(shoe_slot, (0, 0)))

    def draw_leggings() -> None:
        # leggings = leg armor (replaces pants+shoes) or the default pants+shoes.
        # shoe==15 (FrogLeg, ShouldOverrideLegs_CheckShoes PlayerDrawLayers.cs:1246) makes
        # CheckShoes true, which suppresses BOTH the leg armor (the :1540 branch only draws
        # when !CheckShoes || wearsRobe) AND the default pants+shoes (the :1576 else-if only
        # draws when !CheckShoes) — FrogLeg replaces the legs. The wearsRobe exception keeps
        # the leg armor (so the robe skirt still covers the FrogLeg legs).
        check_shoes = shoe_slot == _SHOE_OVERRIDE_EXCEPTION
        if armor_legs:
            if check_shoes and not wears_robe:
                return                        # leg armor suppressed by shoe==15 (:1540)
            # when wearing a robe the leg-armor layer is dyed with the BODY dye, not the
            # leg dye (UpdateDyes: cLegs = cBody when wearsRobe, Player.cs:9309-9311).
            leg_dye = body_dye if wears_robe else armor["leg_dye"]
            comp.draw_armor(armor_legs, "col", leg_dye)
            # leg glowmask (Glow_{legsGlowMask}) rides the same leg frame + leg dye, right
            # after the colored leggings (glowmask_spec.md §5.2). cLegs==cBody under a robe.
            if legs_glow_entry is not None:
                leg_color = comp._glow_color(legs_glow_entry["color"])
                if leg_color is not None:
                    comp.draw_strip_glow(
                        f"Glow_{legs_glow_entry['mask']}", "col", leg_color, leg_dye,
                        jitter=int(legs_glow_entry.get("jitter", 1)))
        elif not check_shoes:                 # default pants+shoes suppressed by shoe==15 (:1576)
            comp.draw_player(11, "col")
            comp.draw_player(12, "col")

    # Shoe accessory (DrawPlayer_14_Shoes) vs leggings (DrawPlayer_13_Leggings) order is
    # branch-dependent (LegacyPlayerRenderer.cs:194-203): the robe branch draws shoes UNDER
    # the leggings, the normal branch draws shoes OVER them. The leg skin (10) is always
    # first either way; the shoe accessory rides the leg frame. (The game also guards the
    # robe branch with `body != 166`, but body 166 is intentionally not in the robe set, so
    # it already falls through to the normal branch here.)
    wears_robe = body_slot in _WEARS_ROBE_BODIES and (
        # slot 81 only counts as a robe when no leg armor is worn.
        body_slot not in _WEARS_ROBE_LEGLESS_ONLY or not armor_legs)
    if wears_robe:
        draw_shoe_acc()
        draw_leggings()
    else:
        draw_leggings()
        draw_shoe_acc()
    # 5. skin long-coat (only without body armor)
    if not armor_body:
        comp.draw_player(14, "col")
    # 5b. armor long-coat (16_ArmorLongCoat, DrawPlayer_16:1791): TWO leg-armor skirts, in
    # order — first the BODY extension (GetMatchingBodyExtension(body), cBody dye), then the
    # COAT extension (GetMatchingBodyExtension(coat); only coat 251 -> Armor_Legs_238, cCoat
    # dye). Both at the idle leg frame, just BEHIND the torso.
    ext_slot = _longcoat_ext_slot(armor["body_slot"], male=comp.male)
    if ext_slot is not None:
        comp.draw_armor(f"Armor_Legs_{ext_slot}", "col", body_dye)
    coat_front_ext = _COAT_FRONT_EXT.get(coat) if coat is not None else None
    if coat_front_ext is not None:
        comp.draw_armor(f"Armor_Legs_{coat_front_ext}", "col", coat_dye)
        # the ChickenBones coat front piece (238) carries an extra GlowMask_363 glow with the
        # ChickenBones representative color, same leg frame + cCoat dye (DrawLongCoat,
        # PlayerDrawLayers.cs:1826-1834). Additive-aware (color A=0 -> pure additive).
        coat_glow_mask = _COAT_FRONT_GLOW.get(coat_front_ext)
        if coat_glow_mask is not None:
            comp.draw_strip_glow(
                f"Glow_{coat_glow_mask}", "col", _CHICKENBONES_GLOW_COLOR, coat_dye)
    # 6. torso + back shoulder
    if armor_body:
        comp.draw_armor(armor_body, "torso", body_dye)
        draw_body_arm_glow("torso", arm=False)        # torso uses bodyGlowColor
        comp.draw_armor(armor_body, "back_shoulder", body_dye)
        draw_body_arm_glow("back_shoulder", arm=True)  # shoulders use armGlowColor
    else:
        comp.draw_player(4, "torso")
        comp.draw_player(6, "torso")

    # ===== IN FRONT, torso accessories (waist / neck) =====
    waist_slot = acc_slots.get("waist")
    if waist_slot:
        comp.draw_acc_strip(f"Acc_Waist_{waist_slot}", acc_dyes.get("waist"))
    neck_slot = acc_slots.get("neck")
    if neck_slot:
        comp.draw_acc_strip(f"Acc_Neck_{neck_slot}", acc_dyes.get("neck"))
    front_slot = acc_slots.get("front")

    # ===== HEAD group (+ beard) =====
    comp.draw_player(0, "col")
    comp.draw_player(1, "col")
    comp.draw_player(2, "col")
    comp.draw_player(15, "col")
    face_slot = acc_slots.get("face")
    # face-under-hair (DrawInFaceUnderHairLayer = {5}, PlayerDrawLayers.cs:2631): drawn in
    # the head group right after the eyes and BEFORE the front hair. These faces are NOT
    # drawn again at layer 22 (DrawPlayer_22_FaceAcc guards on the same set, 2807).
    if face_slot in _FACE_UNDER_HAIR:
        comp.draw_acc_strip(
            f"Acc_Face_{face_slot}", acc_dyes.get("face"),
            offset=_FACE_DRAW_OFFSET.get(face_slot, (0, 0)))
    # 8. front hair (unless a PreventHairDraw face acc), then head armor over it. A
    # UseSkinColor head (274/277) is drawn with the player's skinColor + the skin shader
    # (a no-op for skinDye==0) instead of white + the head dye (PlayerDrawLayers.cs:2145).
    if draw_front_hair:
        comp.draw_hair(hair_file, clip_rows=_FRONT_HAIR_CLIP if is_back else FH)
    if armor["head_slot"] in _HEAD_USE_SKIN_COLOR:
        comp.draw_armor(armor_head, "col", None, tint=comp.colors["skin"])
    else:
        comp.draw_armor(armor_head, "col", armor["head_dye"])
    # head glowmask (Glow_{headGlowMask}) rides the same head cell (body frame) + head dye,
    # right after the colored head armor (glowmask_spec.md §5.2). Four shapes:
    #   - 'grid':'tv'    -> head 271 TV screen (6x4-grid cell, draw_tv_head_glow);
    #   - 'fourtap':<m>  -> head 211 hardcoded 4-tap shimmer of Glow_<m> (draw_head211_4tap,
    #                       PlayerDrawLayers.cs:2403-2415) — a special case, no normal 'mask';
    #   - normal 'mask'  -> draw_strip_glow (with optional 'jitter' for head 240);
    #   - 'extra'        -> head 269 FrontShoulder extra (Extra_214 white armor + GlowMask_308
    #                       glow), drawn at the same head cell (PlayerDrawLayers.cs:107-116).
    if head_glow_entry is not None:
        head_color = comp._glow_color(head_glow_entry["color"])
        head_dye = armor["head_dye"]
        if head_color is not None:
            if head_glow_entry.get("grid") == "tv":
                comp.draw_tv_head_glow(
                    f"Glow_{head_glow_entry['mask']}", head_color, head_dye)
            elif "fourtap" in head_glow_entry:
                # head 211 only: a hardcoded special case (PlayerDrawLayers.cs:2403), gated
                # on head_slot == _HEAD211_4TAP so the constant is the on/off switch (mirrors
                # body 205's _BODY205_FRONTARM_4TAP gate).
                if armor["head_slot"] == _HEAD211_4TAP:
                    comp.draw_head211_4tap(
                        f"Glow_{head_glow_entry['fourtap']}", head_dye)
            elif "mask" in head_glow_entry:
                comp.draw_strip_glow(
                    f"Glow_{head_glow_entry['mask']}", "col", head_color, head_dye,
                    jitter=int(head_glow_entry.get("jitter", 1)))
        extra = head_glow_entry.get("extra")
        if extra is not None:
            # Extra_{armor}: a white (colorArmorHead) armor layer + its GlowMask_{mask} glow,
            # both at the head cell with the head dye. The glow reuses the slot's headGlowColor.
            comp.draw_armor(f"Extra_{extra['armor']}", "col", head_dye)
            if head_color is not None:
                comp.draw_strip_glow(
                    f"Glow_{extra['mask']}", "col", head_color, head_dye)
    # beard (drawn in the head group; Wilson beards tint by hair color).
    beard_slot = acc_slots.get("beard")
    if beard_slot:
        comp.draw_acc_strip(
            f"Acc_Beard_{beard_slot}", acc_dyes.get("beard"),
            hair_color=beard_slot in _BEARD_HAIR_COLOR)
    # face accessory (layer 22, over the head/hair) — every face EXCEPT the under-hair set.
    # face 19 carries GetFaceDrawOffset (0,-6); others are 0 for a bare-headed idle player.
    if face_slot and face_slot not in _FACE_UNDER_HAIR:
        comp.draw_acc_strip(
            f"Acc_Face_{face_slot}", acc_dyes.get("face"),
            offset=_FACE_DRAW_OFFSET.get(face_slot, (0, 0)))

    # ===== IN FRONT, after the head/face group (front-acc back-half / shield) =====
    # FrontAcc back-half (DrawPlayer_32_FrontAcc_BackPart, LegacyPlayerRenderer.cs:229) is
    # drawn AFTER the head/face group (it covers the head/neck region), then the shield
    # (DrawPlayer_25_Shield, 230) — both BEFORE the front arm so the arm occludes them.
    # front 13 carries GetFrontDrawOffset (-2,0); other fronts are 0. Both halves share it.
    front_offset = _FRONT_DRAW_OFFSET.get(front_slot or 0, (0, 0))
    if front_slot:
        comp.draw_acc_front_half(
            f"Acc_Front_{front_slot}", acc_dyes.get("front"), front=False,
            offset=front_offset)
    shield_slot = acc_slots.get("shield")
    if shield_slot:
        comp.draw_acc_strip(f"Acc_Shield_{shield_slot}", acc_dyes.get("shield"))

    # ===== FRONT ARM group (+ on-hand composite) =====
    comp.draw_player(7, "front_arm")
    if armor_body:
        comp.draw_armor(armor_body, "front_arm", body_dye)
        draw_body_arm_glow("front_arm", arm=True)
        comp.draw_armor(armor_body, "front_shoulder", body_dye)
        draw_body_arm_glow("front_shoulder", arm=True)
    else:
        comp.draw_player(8, "front_arm")
        comp.draw_player(13, "front_arm")
    handon_slot = acc_slots.get("handOn")
    if handon_slot:
        comp.draw_acc_hand(
            f"Acc_HandsOn_{handon_slot}", comp.cells["front_arm"],
            acc_dyes.get("handOn"))

    # ===== IN FRONT, outermost (front-acc front-half over the front arm) =====
    # FrontAcc front-half (DrawPlayer_32_FrontAcc_FrontPart, LegacyPlayerRenderer.cs:243)
    # is the last accessory, drawn OVER the front arm.
    if front_slot:
        comp.draw_acc_front_half(
            f"Acc_Front_{front_slot}", acc_dyes.get("front"), front=True,
            offset=front_offset)

    canvas = _crop_to_content(comp.canvas)
    if scale > 1:
        canvas = np.repeat(np.repeat(canvas, scale, axis=0), scale, axis=1)
    return write_png(canvas)
