"""Terraria player avatar compositor (numpy). Renders base body + equipment +
vanity + armor dyes into a transparent PNG. Reusable, no NoneBot dependency.

Faithful port of the validated prototype (see research/terraria_render_spec.md):
composite idle frames, variant fallback, hair occlusion, per-part equipment/vanity,
exact ArmorColored dyes. Accessories are out of scope for now.
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
# Hair indices > this are "back-flowing" styles drawn behind the body
# (Player.GetHairSettings backHairDraw threshold). A handful below it are
# back styles too — see _back_hair_style.
_BACK_HAIR_MIN_INDEX = 50
_BACK_HAIR_EXTRA = (6, 133, 134, 146, 162)
# front-hair forehead clip height when the style also draws a back pass
_FRONT_HAIR_CLIP = 26


def _load_json(name: str) -> Any:
    with (DATA / name).open(encoding="utf-8") as f:
        return json.load(f)


_EQUIP_SLOTS = _load_json("equip_slots.json")   # netId -> {"head"|"body"|"legs": slot}
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
    """src over dst, straight alpha, vectorized. Mutates and returns dst."""
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
    return hair_idx > _BACK_HAIR_MIN_INDEX or hair_idx in _BACK_HAIR_EXTRA


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
        self.canvas = np.zeros((FH, FW, 4), np.uint8)

    def draw_player(self, layer: int, cell_key: str) -> None:
        name = _resolve_player(self.var, layer)
        if name:
            tinted = _tint(
                _frame(name, self.cells[cell_key]),
                self.colors[_LAYER_TINT.get(layer)],
            )
            _over(self.canvas, tinted)

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
            _over(self.canvas, buf)

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
        _over(self.canvas, hf)


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


# ── main render ─────────────────────────────────────────────────────
def render_character(
    appearance: dict[str, Any],
    equipment: dict[str, Any] | None = None,
    vanity: dict[str, Any] | None = None,
    dye: dict[str, Any] | None = None,
    scale: int = 1,
) -> bytes:
    """Render a Terraria character to PNG bytes (transparent background).

    appearance: dict with skinVariant/hair/hairDye + packed-int colors.
    equipment/vanity/dye: dicts {head,body,legs: {netId,...}} or None.
    Returns PNG bytes (40*scale x 56*scale).
    """
    equipment = equipment or {}
    vanity = vanity or {}
    dye = dye or {}

    comp = _Compositor(appearance)
    armor = _resolve_armor(equipment, vanity, dye)
    armor_head, armor_body, armor_legs = armor["head"], armor["body"], armor["legs"]
    body_dye = armor["body_dye"]

    hair = _resolve_hair(comp.hair, armor["head_slot"])
    hair_file, is_back = hair["file"], hair["is_back"]

    # 1. back hair
    if hair["draw_back"]:
        comp.draw_hair(hair_file)
    # 2. back arm
    comp.draw_player(7, "back_arm")
    comp.draw_player(5, "back_arm")
    comp.draw_armor(armor_body, "back_arm", body_dye)
    # 3. body + leg skin
    comp.draw_player(3, "torso")
    comp.draw_player(10, "col")
    # 4. leggings (armor replaces pants+shoes)
    if armor_legs:
        comp.draw_armor(armor_legs, "col", armor["leg_dye"])
    else:
        comp.draw_player(11, "col")
        comp.draw_player(12, "col")
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
    # 7. head group
    comp.draw_player(0, "col")
    comp.draw_player(1, "col")
    comp.draw_player(2, "col")
    comp.draw_player(15, "col")
    # 8. front hair, then head armor over it
    if hair["draw_front"]:
        comp.draw_hair(hair_file, clip_rows=_FRONT_HAIR_CLIP if is_back else FH)
    comp.draw_armor(armor_head, "col", armor["head_dye"])
    # 9. front arm + front shoulder
    comp.draw_player(7, "front_arm")
    if armor_body:
        comp.draw_armor(armor_body, "front_arm", body_dye)
        comp.draw_armor(armor_body, "front_shoulder", body_dye)
    else:
        comp.draw_player(8, "front_arm")
        comp.draw_player(13, "front_arm")

    canvas = comp.canvas
    if scale > 1:
        canvas = np.repeat(np.repeat(canvas, scale, axis=0), scale, axis=1)
    return write_png(canvas)
