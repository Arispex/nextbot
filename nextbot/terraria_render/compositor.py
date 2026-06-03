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
# back items routed to the backpack/tail layers (08/08_1) instead of the back layer 10
# — those layers aren't implemented (PRD prioritizes capes), so such back items are
# skipped. ArmorIDs.cs:1695 (DrawInBackpackLayer) / 1697 (DrawInTailLayer).
_BACK_BACKPACK = {7, 8, 9, 10, 15, 16, 32, 33}
_BACK_TAIL = {18, 19, 21, 25, 26, 27, 28}
# balloon 18 (RoyalScepter): torso-framed (40x1120) AND drawn in front of the back arm
# (balloonFront), not the normal behind-body balloon layer. ArmorIDs.cs:2212/2214.
_BALLOON_TORSO = {18}
# face items that suppress the front hair draw. ArmorIDs.cs:2140.
_FACE_PREVENT_HAIR = {2, 3, 4, 19}
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
_DYES = _load_json("dyes.json")                 # dye netId -> {pass,color,sat,...}
_HAIR = _load_json("hair_sets.json")
# hairDye index 1..11 -> replacement [r,g,b] (or null = keep hairColor); index 0 = no
# dye, index 12 = Twilight (keeps hairColor, runs ArmorTwilight pass). hairdye_spec.md.
_HAIR_DYE_COLORS = _load_json("hair_dye_colors.json")
_TWILIGHT_HAIR_DYE = 12
_VAR = _load_json("variants.json")
# body equip slot -> long-coat leg-armor slot (robe/coat skirt); int, or
# {"male","female"} for the 5 gender-conditional bodies. See robe_extension_spec.md.
_ROBE_EXT = _load_json("robe_extensions.json")
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
    name: str, n_frames: int,
) -> tuple[np.ndarray | None, tuple[int, int, int, int], tuple[int, int]]:
    """Wing idle frame 0 of a vertical N-frame strip: (0,0,W,H/N). For a grounded
    standing player wingFrame==0 (folded). Returns (frame|None, src_rect, sheet_size)."""
    sheet = _sheet(name)
    if sheet is None:
        return None, (0, 0, FW, FH), (FW, FH)
    h, w = sheet.shape[0], sheet.shape[1]
    fh = h // max(1, n_frames)
    frame = sheet[:fh, :w].copy()
    return frame, (0, 0, w, fh), (w, h)


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
    ) -> None:
        if name and _sheet(name) is not None:
            cell = self.cells[cell_key]
            buf = _frame(name, cell)
            if dye_spec:
                src_rect, sheet_size = _frame_geom(name, cell)
                buf = apply_dye(buf, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
            self._over_cell(buf)

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
        self, name: str, dye_spec: dict[str, Any] | None, *, hair_color: bool = False,
    ) -> None:
        """A strip/shield/torso-framed accessory: idle frame 0 = (0,0,texW,56), top-left
        aligned in the cell (0,0). Untinted white (display-doll path) + its dye; beards
        with UseHairColor tint by the player's hair color instead (accessories_spec §12)."""
        frame, src_rect, sheet_size = _acc_strip_frame(name)
        if frame is None:
            return
        if hair_color:
            frame = _tint(frame, _hair_tint_color(self.hair_dye, self.hair_rgb))
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_cell(frame)

    def draw_acc_balloon(self, name: str, dye_spec: dict[str, Any] | None) -> None:
        """A normal (non-torso) balloon: 52x56 frame 0, cell-local top-left
        (-6, -4) (floats above/left of the body — accessories_spec §3)."""
        frame, src_rect, sheet_size = _acc_balloon_frame(name)
        if frame is None:
            return
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        self._over_cell(frame, *_BALLOON_OFFSET)

    def draw_acc_wing(self, slot: int, dye_spec: dict[str, Any] | None) -> None:
        """A wing: idle frame 0 (folded) of the vertical N-frame strip, at the
        cell-local offset derived from DrawPlayer_09_Wings (accessories_spec §1).
        AlwaysAnimated wings draw nothing grounded and are filtered by the caller."""
        name = f"Wings_{slot}"
        n = _WING_FRAMES.get(slot, 4)
        frame, src_rect, sheet_size = _acc_wing_frame(name, n)
        if frame is None:
            return
        if dye_spec:
            frame = apply_dye(frame, dye_spec, src_rect=src_rect, sheet_size=sheet_size)
        w, fh = frame.shape[1], frame.shape[0]
        num13, num12 = _WING_OFFSET.get(slot, (0, 0))
        lx = 11 + num13 - w // 2
        ly = 33 + num12 - fh // 2
        self._over_cell(frame, lx, ly)

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
    ) -> None:
        """One half of a split front accessory (accessories_spec §11): the BACK half
        (right 20px, cols [20,40)) draws behind the shield/arm; the FRONT half (left
        20px, cols [0,20)) draws over the front arm. Dye uses the full 40x56 frame rect
        (ArmorShaderData.Apply reads the drawn sub-rect — here the half we keep)."""
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
        self._over_cell(masked)


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


def _apply_item_slots(resolved: dict[str, int], net_id: int) -> None:
    """Merge one item's `<cat>Slot` values into `resolved` (later writes win, mirroring
    Player.UpdateVisibleAccessory). No-op for air / items with no visual slot."""
    for cat, slot in _ACC_SLOTS.get(str(net_id), {}).items():
        resolved[cat] = int(slot)


def _resolve_accessories(
    accessories: list[Any] | None,
    vanity_accessories: list[Any] | None,
    accessory_dyes: list[Any] | None,
    hide_visuals: int,
    *, male: bool,
) -> dict[str, Any]:
    """Replicate Player.UpdateVisibleAccessories (accessories_spec §X1): iterate the 7
    functional acc slots (low->high index, higher index overwrites per category), then
    the 7 vanity slots (which unconditionally override). A hideVisuals-hidden functional
    acc is skipped, but its vanity twin still draws. Also resolves the per-category dye
    (functional acc k AND its vanity twin both read accessoryDyes[k]).

    Returns {"slots": {category: slot}, "dyes": {category: dye_spec|None}}.
    """
    acc = accessories or []
    van = vanity_accessories or []
    dyes = accessory_dyes or []
    resolved: dict[str, int] = {}
    # category -> the dye index (0..6) whose item last set it (so we read the right dye).
    dye_index: dict[str, int] = {}

    def record(k: int, net_id: int) -> None:
        for cat in _ACC_SLOTS.get(str(net_id), {}):
            dye_index[cat] = k

    # 1) functional accessories 0..6 (armor[3+k]); skip if hidden by hideVisuals bit 3+k.
    for k in range(7):
        if hide_visuals & (1 << (_HIDE_BIT_BASE + k)):
            continue  # hidden functional acc (incl. wings: still grounded => velocity.Y==0)
        nid = _net_id(acc[k]) if k < len(acc) else 0
        if nid:
            _apply_item_slots(resolved, nid)
            record(k, nid)
    # 2) social / vanity accessories 0..6 (armor[13+k]) — always draw, override functional.
    for k in range(7):
        nid = _net_id(van[k]) if k < len(van) else 0
        if nid:
            _apply_item_slots(resolved, nid)
            record(k, nid)

    # GlassSlipper male->female shoe remap (UpdateVisibleAccessory 36502-36505).
    if not male and resolved.get("shoe") in _SHOE_MALE_TO_FEMALE:
        resolved["shoe"] = _SHOE_MALE_TO_FEMALE[resolved["shoe"]]

    # per-category dye spec from the dye-slot that the category's item came from.
    cat_dyes: dict[str, Any] = {}
    for cat, k in dye_index.items():
        nid = _net_id(dyes[k]) if k < len(dyes) else 0
        cat_dyes[cat] = _DYES.get(str(nid)) if nid else None
    return {"slots": resolved, "dyes": cat_dyes}


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

    hide_visuals = int(appearance.get("hideVisuals") or 0)
    acc = _resolve_accessories(
        accessories, vanity_accessories, accessory_dyes, hide_visuals, male=comp.male)
    acc_slots: dict[str, int] = acc["slots"]
    acc_dyes: dict[str, Any] = acc["dyes"]

    def sheet_for(cat: str, prefix: str) -> str | None:
        slot = acc_slots.get(cat)
        return f"{prefix}{slot}" if slot else None

    hair = _resolve_hair(comp.hair, armor["head_slot"])
    hair_file, is_back = hair["file"], hair["is_back"]
    # a face acc with PreventHairDraw hides the front hair (accessories_spec §8).
    draw_front_hair = hair["draw_front"] and (
        acc_slots.get("face") not in _FACE_PREVENT_HAIR)

    # ===== BEHIND BODY (wings / back cape / balloon — padded canvas) =====
    # 0. wings (idle frame 0; AlwaysAnimated wings render nothing grounded -> skip).
    wing_slot = acc_slots.get("wing")
    if wing_slot and wing_slot not in _WING_ALWAYS_ANIMATED:
        comp.draw_acc_wing(wing_slot, acc_dyes.get("wing"))
    # 1. back hair
    if hair["draw_back"]:
        comp.draw_hair(hair_file)
    # 2. back accessory (real capes only; backpack/tail-routed items aren't drawn).
    back_slot = acc_slots.get("back")
    if back_slot and back_slot not in _BACK_BACKPACK and back_slot not in _BACK_TAIL:
        comp.draw_acc_strip(f"Acc_Back_{back_slot}", acc_dyes.get("back"))
    # 3. balloon — normal balloons float behind the body; balloon 18 (torso-framed) is
    #    drawn later in front of the back arm (balloonFront).
    balloon_slot = acc_slots.get("balloon")
    if balloon_slot and balloon_slot not in _BALLOON_TORSO:
        comp.draw_acc_balloon(f"Acc_Balloon_{balloon_slot}", acc_dyes.get("balloon"))

    # ===== BACK ARM group (+ off-hand composite) =====
    comp.draw_player(7, "back_arm")
    comp.draw_player(5, "back_arm")
    handoff_slot = acc_slots.get("handOff")
    if handoff_slot:
        comp.draw_acc_hand(
            f"Acc_HandsOff_{handoff_slot}", comp.cells["back_arm"],
            acc_dyes.get("handOff"))
    comp.draw_armor(armor_body, "back_arm", body_dye)
    # balloon 18 (balloonFront): torso-framed, in front of the back arm.
    if balloon_slot and balloon_slot in _BALLOON_TORSO:
        comp.draw_acc_strip(f"Acc_Balloon_{balloon_slot}", acc_dyes.get("balloon"))

    # ===== BODY + LEG SKIN (+ shoe accessory) =====
    comp.draw_player(3, "torso")
    comp.draw_player(10, "col")

    shoe_slot = acc_slots.get("shoe")

    def draw_shoe_acc() -> None:
        if shoe_slot:
            comp.draw_acc_strip(f"Acc_Shoes_{shoe_slot}", acc_dyes.get("shoe"))

    def draw_leggings() -> None:
        # leggings = leg armor (replaces pants+shoes) or the default pants+shoes.
        if armor_legs:
            comp.draw_armor(armor_legs, "col", armor["leg_dye"])
        else:
            comp.draw_player(11, "col")
            comp.draw_player(12, "col")

    # Shoe accessory (DrawPlayer_14_Shoes) vs leggings (DrawPlayer_13_Leggings) order is
    # branch-dependent (LegacyPlayerRenderer.cs:194-203): the robe branch draws shoes UNDER
    # the leggings, the normal branch draws shoes OVER them. The leg skin (10) is always
    # first either way; the shoe accessory rides the leg frame. (The game also guards the
    # robe branch with `body != 166`, but body 166 is intentionally not in the robe set, so
    # it already falls through to the normal branch here.)
    body_slot = armor["body_slot"]
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
    # 5b. armor long-coat (robe/coat skirt): leg-armor sheet keyed on BODY slot,
    # drawn with the BODY dye/tint, at the idle leg frame, just BEHIND the torso.
    ext_slot = _longcoat_ext_slot(armor["body_slot"], male=comp.male)
    if ext_slot is not None:
        comp.draw_armor(f"Armor_Legs_{ext_slot}", "col", body_dye)
    # 6. torso + back shoulder
    if armor_body:
        comp.draw_armor(armor_body, "torso", body_dye)
        comp.draw_armor(armor_body, "back_shoulder", body_dye)
    else:
        comp.draw_player(4, "torso")
        comp.draw_player(6, "torso")

    # ===== IN FRONT, torso accessories (waist / neck / front-acc back-half) =====
    waist_slot = acc_slots.get("waist")
    if waist_slot:
        comp.draw_acc_strip(f"Acc_Waist_{waist_slot}", acc_dyes.get("waist"))
    neck_slot = acc_slots.get("neck")
    if neck_slot:
        comp.draw_acc_strip(f"Acc_Neck_{neck_slot}", acc_dyes.get("neck"))
    front_slot = acc_slots.get("front")
    if front_slot:
        comp.draw_acc_front_half(
            f"Acc_Front_{front_slot}", acc_dyes.get("front"), front=False)

    # ===== HEAD group (+ beard) =====
    comp.draw_player(0, "col")
    comp.draw_player(1, "col")
    comp.draw_player(2, "col")
    comp.draw_player(15, "col")
    # 8. front hair (unless a PreventHairDraw face acc), then head armor over it
    if draw_front_hair:
        comp.draw_hair(hair_file, clip_rows=_FRONT_HAIR_CLIP if is_back else FH)
    comp.draw_armor(armor_head, "col", armor["head_dye"])
    # beard (drawn in the head group; Wilson beards tint by hair color).
    beard_slot = acc_slots.get("beard")
    if beard_slot:
        comp.draw_acc_strip(
            f"Acc_Beard_{beard_slot}", acc_dyes.get("beard"),
            hair_color=beard_slot in _BEARD_HAIR_COLOR)
    # face accessory (over the head/hair).
    face_slot = acc_slots.get("face")
    if face_slot:
        comp.draw_acc_strip(f"Acc_Face_{face_slot}", acc_dyes.get("face"))

    # ===== FRONT ARM group (+ on-hand composite) =====
    comp.draw_player(7, "front_arm")
    if armor_body:
        comp.draw_armor(armor_body, "front_arm", body_dye)
        comp.draw_armor(armor_body, "front_shoulder", body_dye)
    else:
        comp.draw_player(8, "front_arm")
        comp.draw_player(13, "front_arm")
    handon_slot = acc_slots.get("handOn")
    if handon_slot:
        comp.draw_acc_hand(
            f"Acc_HandsOn_{handon_slot}", comp.cells["front_arm"],
            acc_dyes.get("handOn"))

    # ===== IN FRONT, outermost (front-acc front-half / shield) =====
    if front_slot:
        comp.draw_acc_front_half(
            f"Acc_Front_{front_slot}", acc_dyes.get("front"), front=True)
    shield_slot = acc_slots.get("shield")
    if shield_slot:
        comp.draw_acc_strip(f"Acc_Shield_{shield_slot}", acc_dyes.get("shield"))

    canvas = _crop_to_content(comp.canvas)
    if scale > 1:
        canvas = np.repeat(np.repeat(canvas, scale, axis=0), scale, axis=1)
    return write_png(canvas)
