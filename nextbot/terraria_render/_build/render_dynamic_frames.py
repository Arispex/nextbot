"""Dev tool: per-frame contact sheets for every dynamic dye / dynamic glow effect.

Renders each effect from `research/dynamic_effects_catalog.md` on ONE shared base
character (a high-coverage armor set), sweeping its internal time/phase variable across a
full animation cycle into a labelled contact sheet, with the *current* still (the frame the
production renderer pins) highlighted by a green border. Effects that are offline-static
(APPROX time passes, the two Reflective passes, the pseudo-random jitter/4-tap glows) render
a single annotated frame instead.

DYE sheets are split into four per-part variants — since a dye is an ArmorShaderData applied
per equip slot, head / body / legs are dyed independently. Each dye sheet is a stack of four
labelled row-bands (HEAD-only / BODY-only / LEGS-only / ALL-three), each band a row of the
swept animation frames (columns = phase). The base armor is the SILVER set — a neutral
mid-grey across head/body/legs that shows each dye's TRUE colour (a near-greyscale base lets
the dye's own colour come through faithfully instead of being skewed by a coloured base):
head = SilverHelmet (netId 91, slot 3), body = SilverChainmail (netId 82, slot 3),
legs = SilverGreaves (netId 78, slot 3). GLOW sheets are unchanged (they still wear their
matching glow item, no per-part split).

It does NOT touch the production render path: the public `render_character` signature /
defaults are unchanged. The dye sweeps drive the documented control points
(`dye.UTIME` / `dye._PILLAR_TIME[name]`, catalog section D.1) and the glow sweeps drive the
`glowmask.json` colour tables + the draw-method phase overrides added in step 1 (catalog
sections D.2/D.3). Every override is restored after each frame, so re-running deterministic.

Run:  ``python3 nextbot/terraria_render/_build/render_dynamic_frames.py``
Out:  ``temp/dynamic_frames/<effect>.png`` + ``temp/dynamic_frames/index.md``
"""
from __future__ import annotations

import contextlib
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

# Run as a plain script from anywhere: put the repo root on sys.path.
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from nextbot.terraria_render import compositor, dye, render_character
from nextbot.terraria_render.image_io import read_png, write_png

if TYPE_CHECKING:
    from collections.abc import Iterator

OUT_DIR = _ROOT / "temp" / "dynamic_frames"

# index rows accumulated as sheets are written: (effect, netId, type, current, image, animated?)
_INDEX: list[tuple[str, str, str, str, str, str]] = []

# ── shared base character + a high-coverage armor set (large dyeable surface) ──
# A male starter (variant 0, body cell column 0) keeps the cell maths simple; the armor set
# covers head + torso + both arms + legs so a dye/glow has signal everywhere.
BASE_APP = {
    "skinVariant": 0, "hair": 5, "hairDye": 0,
    "hairColor": -3270602, "skinColor": -10059269, "eyeColor": -15100654,
    "shirtColor": -4021652, "underShirtColor": -4639811,
    "pantsColor": -12772014, "shoeColor": -4963208,
}
# Base armor set for the DYE sheets — the SILVER set (neutral mid-grey across head/body/legs).
# A near-greyscale base is the best canvas for reading a dye's TRUE colour: with no strong
# native hue of its own, the silver armour lets the dye's own colour come through faithfully
# (a coloured base would multiply/skew the dye and muddy what colour it actually produces),
# while still carrying clear highlight / mid / shadow bands so gradient/recolor shading bugs
# remain visible. The whole set is one consistent grey so head / body / legs read alike.
#   head: SilverHelmet     netId 91 (slot 3)
#   body: SilverChainmail  netId 82 (slot 3)
#   legs: SilverGreaves    netId 78 (slot 3)
ARMOR = {"head": {"netId": 91}, "body": {"netId": 82}, "legs": {"netId": 78}}
SCALE = 3  # each cell is upscaled this much in the sheets
# DYE sheets split each effect into 4 per-part variants (catalog: every dye is an
# ArmorShaderData applied per equip slot, so head / body / legs can be dyed independently):
# HEAD-only, BODY-only, LEGS-only, and ALL-three. Each becomes one labelled row-band of the
# contact sheet so a head dye that the old BODY-only sheet hid is now visible on its own band.
_DYE_VARIANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("HEAD", ("head",)),
    ("BODY", ("body",)),
    ("LEGS", ("legs",)),
    ("ALL", ("head", "body", "legs")),
)

# ── a tiny 5x7 bitmap font (uppercase, digits, a few symbols) for cell labels ──
# Each glyph = 7 rows of a 5-char string ('#'=ink). Enough for the labels we emit.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "=": ("00000", "11111", "00000", "00000", "11111", "00000", "00000"),
    "/": ("00001", "00010", "00100", "00100", "01000", "10000", "10000"),
    "#": ("01010", "11111", "01010", "01010", "11111", "01010", "00000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}
_GLYPH_W, _GLYPH_H = 5, 7
_LABEL_H = _GLYPH_H + 3                          # 1px top + glyph + 2px bottom
_INK = (235, 235, 235, 255)
_BG = (26, 26, 30, 255)
_HILITE = (60, 230, 90, 255)                     # current-frame border
_PLAIN_BORDER = (70, 70, 78, 255)


def _draw_text(canvas: np.ndarray, x: int, y: int, text: str) -> None:
    """Blit `text` (uppercased; unknown chars -> space) at (x,y) into an RGBA canvas."""
    cx = x
    for ch in text.upper():
        glyph = _GLYPHS.get(ch, _GLYPHS[" "])
        for gy, line in enumerate(glyph):
            for gx, px in enumerate(line):
                if px == "1":
                    yy, xx = y + gy, cx + gx
                    if 0 <= yy < canvas.shape[0] and 0 <= xx < canvas.shape[1]:
                        canvas[yy, xx] = _INK
        cx += _GLYPH_W + 1


def _decode(png: bytes) -> np.ndarray:
    """Render PNG bytes -> (h,w,4) uint8 via the project codec."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(png)
        path = fh.name
    try:
        return read_png(path)
    finally:
        Path(path).unlink(missing_ok=True)


# ── contact-sheet tiling ──────────────────────────────────────────────
class _Cell:
    """One rendered frame + its caption + whether it is the production 'current' still."""

    __slots__ = ("current", "img", "label")

    def __init__(self, img: np.ndarray, label: str, *, current: bool = False) -> None:
        self.img = img
        self.label = label
        self.current = current


def _paste_cell(sheet: np.ndarray, c: _Cell, x: int, y: int, cw: int, ch: int) -> None:
    """Draw one `_Cell` (border + image-over-bg + caption) into `sheet` at top-left (x, y).
    `cw`/`ch` are the shared cell image box size (so labels clip to the cell width)."""
    cell_w = cw + 2
    border = _HILITE if c.current else _PLAIN_BORDER
    sheet[y:y + ch + 2, x:x + cell_w] = border
    iy, ix = y + 1, x + 1
    ih, iw = c.img.shape[0], c.img.shape[1]
    region = sheet[iy:iy + ih, ix:ix + iw]
    a = c.img[..., 3:4].astype(np.float64) / 255.0
    region[:] = (c.img.astype(np.float64) * a + region.astype(np.float64) * (1 - a)).astype(
        np.uint8)
    region[..., 3] = 255
    _draw_text(sheet, x + 1, y + ch + 3, c.label[: (cell_w - 2) // (_GLYPH_W + 1)])


def _contact_sheet(cells: list[_Cell], title: str, *, cols: int = 6) -> np.ndarray:
    """Tile `cells` into a labelled grid with a title bar; highlight current cells."""
    pad, gap = 6, 6
    cw = max(c.img.shape[1] for c in cells)
    ch = max(c.img.shape[0] for c in cells)
    cell_w = cw + 2                                   # +1px border each side
    cell_h = ch + 2 + _LABEL_H
    rows = math.ceil(len(cells) / cols)
    title_h = _LABEL_H + 2
    W = pad * 2 + cols * cell_w + (cols - 1) * gap
    H = pad * 2 + title_h + rows * cell_h + (rows - 1) * gap
    sheet = np.empty((H, W, 4), np.uint8)
    sheet[:] = _BG
    _draw_text(sheet, pad, pad + 1, title)
    y0 = pad + title_h
    for i, c in enumerate(cells):
        r, col = divmod(i, cols)
        x = pad + col * (cell_w + gap)
        y = y0 + r * (cell_h + gap)
        _paste_cell(sheet, c, x, y, cw, ch)
    return sheet


def _dye_band_sheet(bands: list[tuple[str, list[_Cell]]], title: str) -> np.ndarray:
    """Tile per-part dye variants as labelled row-bands: one band per variant (HEAD / BODY /
    LEGS / ALL), each a single row of its N animation frames (columns = phase), with the band
    label down the left gutter and the production-'current' frame highlighted in every band.

    All bands share the column count `n` (the frame sweep), so phases line up vertically; a
    static/single-frame dye is n=1 (the 4 variants sit side by side, one per band)."""
    pad, gap, band_gap = 6, 6, 4
    # room for the widest 4-glyph band label ('HEAD'/'LEGS'): 4*(GLYPH_W+1)-1 + a small pad.
    gutter = 4 * (_GLYPH_W + 1) + 6
    cw = max(c.img.shape[1] for _, cs in bands for c in cs)
    ch = max(c.img.shape[0] for _, cs in bands for c in cs)
    cell_w, cell_h = cw + 2, ch + 2 + _LABEL_H
    n = max(len(cs) for _, cs in bands)
    title_h = _LABEL_H + 2
    W = pad * 2 + gutter + n * cell_w + (n - 1) * gap
    H = pad * 2 + title_h + len(bands) * cell_h + (len(bands) - 1) * band_gap
    sheet = np.empty((H, W, 4), np.uint8)
    sheet[:] = _BG
    _draw_text(sheet, pad, pad + 1, title)
    y0 = pad + title_h
    for b, (label, cs) in enumerate(bands):
        y = y0 + b * (cell_h + band_gap)
        _draw_text(sheet, pad, y + (ch - _GLYPH_H) // 2, label)   # band label in the gutter
        for col, c in enumerate(cs):
            x = pad + gutter + col * (cell_w + gap)
            _paste_cell(sheet, c, x, y, cw, ch)
    return sheet


def _save(name: str, sheet: np.ndarray) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    path.write_bytes(write_png(sheet))
    return path.name


# ── dye rendering ─────────────────────────────────────────────────────
def _render_dye(net_id: int, *, slots: tuple[str, ...] = ("body",)) -> np.ndarray:
    """Render the base armor set with dye `net_id` on every part in `slots` -> RGBA frame.

    `slots` is one of the `_DYE_VARIANTS` part tuples (HEAD/BODY/LEGS = one part, ALL =
    all three), so a head dye is rendered on the head slot etc. Uses the production render
    path; the dye's frozen uTime is whatever the module globals currently hold (the caller
    sweeps `dye.UTIME` / `dye._PILLAR_TIME`)."""
    dye = {slot: {"netId": net_id} for slot in slots}
    return _decode(render_character(BASE_APP, ARMOR, None, dye, scale=SCALE))


@contextlib.contextmanager
def _utime(value: float) -> Iterator[None]:
    """Temporarily set the time-animated default uTime (catalog section D.1)."""
    saved = dye.UTIME
    dye.UTIME = value
    try:
        yield
    finally:
        dye.UTIME = saved


@contextlib.contextmanager
def _pillar(name: str, value: float) -> Iterator[None]:
    """Temporarily set an emissive pillar pass's representative uTime (catalog section D.1)."""
    saved = dye._PILLAR_TIME[name]
    dye._PILLAR_TIME[name] = value
    try:
        yield
    finally:
        dye._PILLAR_TIME[name] = saved


@contextlib.contextmanager
def _batch2(name: str, value: float) -> Iterator[None]:
    """Temporarily set a batch-2 pass's frozen uTime so the production render path (which
    calls apply_dye with u_time=None) sweeps it.

    The batch-2 animated passes resolve their still through dye._batch2_time(name, None):
    Acid/Void read dye._BATCH2_TIME[name] (their swept representative), the rest fall to
    dye.UTIME. So Acid/Void sweep by overriding _BATCH2_TIME[name]; the others sweep by
    overriding dye.UTIME (the _utime CM)."""
    if name in dye._BATCH2_TIME:
        saved = dye._BATCH2_TIME[name]
        dye._BATCH2_TIME[name] = value
        try:
            yield
        finally:
            dye._BATCH2_TIME[name] = saved
    else:
        with _utime(value):
            yield


def _animates(cells: list[_Cell]) -> bool:
    """True iff the swept frames are not all identical (the dye really moves offline).

    Several catalog-'scannable' noise passes (Phase/ShiftingSands/ShiftingPearlsands/Fog,
    and HallowBoss to within a sliver) carry no uTime term in their compiled bytecode
    offline, so sweeping uTime yields one frozen frame — surfaced here as 'no'."""
    return len({c.img.tobytes() for c in cells}) > 1


def _sweep_time_dye(
    net_id: int, lo: float, hi: float, n: int, current: float,
    slots: tuple[str, ...] = ("body",),
) -> list[_Cell]:
    """Render n frames over uTime in [lo,hi) for one part variant (`slots`), highlighting
    the frame closest to `current`."""
    cur_i = min(range(n), key=lambda i: abs((lo + (hi - lo) * i / n) - current))
    cells = []
    for i in range(n):
        t = lo + (hi - lo) * i / n
        with _utime(t):
            img = _render_dye(net_id, slots=slots)
        cells.append(_Cell(img, f"T={t:.2f}", current=(i == cur_i)))
    return cells


def _sweep_noise_dye(
    net_id: int, pass_name: str, lo: float, hi: float, n: int, current: float,
    slots: tuple[str, ...] = ("body",),
) -> list[_Cell]:
    """Render n frames over a noise pass's uTime for one part variant (`slots`); highlight
    the production frame.

    Picks the right phase-override CM per pass family:
    - emissive pillar passes (Nebula/Vortex/Stardust/HallowBoss) pin their frozen frame in
      `dye._PILLAR_TIME[name]` -> sweep that;
    - batch-2 animated / self-sampling passes (Flow/Living*/Acid/Void/Mirage/Hades/Loki) run
      the real bytecode and resolve via `dye._batch2_time` (Acid/Void from `dye._BATCH2_TIME`,
      the rest from `dye.UTIME`) -> `_batch2` overrides the right one;
    - the remaining scroll passes (Phase/Gel/ShiftingSands/Pearlsands/Fog/MidnightRainbow)
      default to `dye.UTIME` -> sweep that."""
    if pass_name in dye._PILLAR_TIME:
        ctx = _pillar
    elif pass_name in _BATCH2_PASSES:
        ctx = _batch2
    else:
        ctx = _utime_named
    cur_i = min(range(n), key=lambda i: abs((lo + (hi - lo) * i / n) - current))
    cells = []
    for i in range(n):
        t = lo + (hi - lo) * i / n
        with ctx(pass_name, t):
            img = _render_dye(net_id, slots=slots)
        cells.append(_Cell(img, f"T={t:.2f}", current=(i == cur_i)))
    return cells


@contextlib.contextmanager
def _utime_named(_name: str, value: float) -> Iterator[None]:
    """`_utime` with a (name, value) signature so the sweep can pick a CM uniformly."""
    with _utime(value):
        yield


# batch-2 passes that run the real bytecode (research/dye_bytecode_audit.md §"third tier"):
# all sweep via `_batch2` (Acid/Void through dye._BATCH2_TIME, the rest through dye.UTIME).
_BATCH2_PASSES = frozenset({
    "ArmorFlow", "ArmorLivingRainbow", "ArmorLivingFlame", "ArmorLivingOcean", "ArmorAcid",
    "ArmorVoid", "ArmorMirage", "ArmorHades", "ArmorLoki",
})


# ── catalog data (from research/dynamic_effects_catalog.md, authoritative) ──
_DYES = json.loads((compositor.ASSETS.parent / "data" / "dyes.json").read_text())


def _name(net_id: int) -> str:
    """Item display name from the catalog tables, keyed by dye netId."""
    return _DYE_NAMES.get(net_id, f"DYE{net_id}")


_DYE_NAMES = {
    2869: "LivingFlame", 2873: "LivingOcean", 2870: "LivingRainbow", 3025: "PurpleOoze",
    3040: "Acid", 3028: "BlueAcid", 3560: "RedAcid",
    3556: "MidnightRainbow", 3526: "Solar", 3530: "Void", 3038: "Hades",
    3597: "BurningHades", 3598: "Grim", 3600: "ShadowflameHades", 3534: "Mirage",
    3599: "Loki",
    3527: "Nebula", 3528: "Vortex", 3529: "Stardust", 4778: "HallowBoss",
    3042: "Phase", 3024: "Dev", 3561: "Gel", 3562: "PinkGel", 4663: "Bloodbath",
    3533: "ShiftingSands", 3535: "ShiftingPearlSands", 4662: "Fogbound",
    3190: "Reflective", 3026: "ReflectiveSilver", 3027: "ReflectiveGold",
    3553: "ReflectiveCopper", 3554: "ReflectiveObsidian", 3555: "ReflectiveMetal",
}

# table A.1 time-animated WITH a real uTime formula. Empty after batch 2: the Living*/Flow/
# Acid passes that used to live here now run the real compiled bytecode (dye_noise) and so
# moved into _SCAN_NOISE (the dye_noise sweep group), alongside the other animatable bytecode.
_SCAN_TIME: list[tuple[int, float, float, int]] = []
# table A.2 animatable dye_noise passes (real ps_2_0 bytecode, all scannable). (netId, pass,
# lo, hi, N). Three families share this sweep:
#   - the original noise-texture / emboss scroll passes (Nebula/Vortex/Stardust/HallowBoss/
#     MidnightRainbow/Phase/Gel/Shifting*/Fog);
#   - the batch-2 animated band passes (Flow 3025 / LivingFlame 2869 / LivingOcean 2873 /
#     LivingRainbow 2870 / Acid 3028,3040,3560) — moved here from _SCAN_TIME;
#   - the batch-2 self-sampling passes (Void 3530 / Hades 3038,3597,3598,3600 / Mirage 3534 /
#     Loki 3599) — moved here from _APPROX_TIME (they are no longer flat-tint approximations).
# MidnightRainbow (3556) is a self-emboss pass whose uTime scrolls the hue with period
# 1/0.4 = 2.5 (research/midnight_rainbow.md §4). The batch-2 Living*/Acid period is ~1.0-2.5;
# Hades/Loki/Void/Mirage scroll over a few seconds -> swept [0,6).
_SCAN_NOISE = [
    (3527, "ArmorNebula", 0.0, 6.0, 24), (3528, "ArmorVortex", 0.0, 6.0, 24),
    (3529, "ArmorStardust", 0.0, 4.0, 24), (4778, "ArmorHallowBoss", 0.0, 8.0, 8),
    (3556, "ArmorMidnightRainbow", 0.0, 2.5, 24),
    (3042, "ArmorPhase", 0.0, 1.0, 24), (3024, "ArmorGel", 0.0, 6.28, 24),
    (3561, "ArmorGel", 0.0, 6.28, 24), (3562, "ArmorGel", 0.0, 6.28, 24),
    (4663, "ArmorGel", 0.0, 6.28, 24), (3533, "ArmorShiftingSands", 0.0, 1.0, 24),
    (3535, "ArmorShiftingPearlsands", 0.0, 1.0, 24), (4662, "ArmorFog", 0.0, 1.0, 24),
    # ── batch 2: animated band passes (uTime=0 representative unless noted) ──
    (3025, "ArmorFlow", 0.0, 1.0, 24), (2869, "ArmorLivingFlame", 0.0, 1.0, 24),
    (2873, "ArmorLivingOcean", 0.0, 1.0, 24), (2870, "ArmorLivingRainbow", 0.0, 1.39, 32),
    (3040, "ArmorAcid", 0.0, 6.0, 24), (3028, "ArmorAcid", 0.0, 6.0, 24),
    (3560, "ArmorAcid", 0.0, 6.0, 24),
    # ── batch 2: self-sampling passes (uTime scroll over a few seconds) ──
    (3530, "ArmorVoid", 0.0, 6.0, 24), (3534, "ArmorMirage", 0.0, 6.0, 24),
    (3038, "ArmorHades", 0.0, 6.0, 24), (3597, "ArmorHades", 0.0, 6.0, 24),
    (3598, "ArmorHades", 0.0, 6.0, 24), (3600, "ArmorHades", 0.0, 6.0, 24),
    (3599, "ArmorLoki", 0.0, 6.0, 24),
]
# A.1 APPROX time passes left after batch 3: only Solar (3526). Its real bytecode IS baked +
# wired (dye._solar), but offline (v0=white, no additive bloom) it collapses to a dark reddish
# ember -- markedly dimmer than the in-game bright Solar -- so the PRODUCTION dispatch keeps the
# handwritten emissive fire approximation (dye._solar_approx) by default. The production path is
# therefore offline-static (no uTime animation), so Solar stays a single-frame dye_static sheet.
# A.3 Reflective stays here too (class C: faithful bytecode wired, but uLightSource=0 offline ->
# the moving specular highlight is absent; a single no-highlight frame).
_APPROX_TIME = [3526]
_REFLECTIVE = [3190, 3026, 3027, 3553, 3554, 3555]

# default frames (where the production still is pinned), for the highlight. Emissive pillars
# pin _PILLAR_TIME; the batch-2 swept passes pin _BATCH2_TIME (Acid 2.5 / Void 1.0); every
# other animatable noise pass pins uTime=0 (the default _PILLAR_CURRENT.get fallback).
_PILLAR_CURRENT = {
    "ArmorNebula": 3.0, "ArmorVortex": 0.5, "ArmorStardust": 1.0, "ArmorHallowBoss": 0.0,
    "ArmorAcid": 2.5, "ArmorVoid": 1.0,
}


def render_scannable_dyes() -> None:
    """Sweep the 20 scannable dyes (7 time + 13 noise/self-sampling) -> one 4-variant
    banded sheet each.

    Each sheet stacks the HEAD / BODY / LEGS / ALL row-bands; every band sweeps the same
    uTime range so the part-specific dye (e.g. head-only) is visible on its own band."""
    for net_id, lo, hi, n in _SCAN_TIME:
        bands = [
            (label, _sweep_time_dye(net_id, lo, hi, n, dye.UTIME, slots))
            for label, slots in _DYE_VARIANTS
        ]
        spec = _DYES.get(str(net_id), {})
        title = f"{_name(net_id)} #{net_id} {spec.get('pass', '')} uTime[{lo},{hi}) N={n}"
        fn = _save(f"dye_time_{_name(net_id)}_{net_id}", _dye_band_sheet(bands, title))
        anim = "yes" if _animates(dict(bands)["BODY"]) else "no (no uTime offline)"
        _INDEX.append((_name(net_id), str(net_id), spec.get("pass", ""), "uTime=0", fn, anim))
    for net_id, pass_name, lo, hi, n in _SCAN_NOISE:
        cur = _PILLAR_CURRENT.get(pass_name, 0.0)   # pillar frame pin, else uTime=0
        bands = [
            (label, _sweep_noise_dye(net_id, pass_name, lo, hi, n, cur, slots))
            for label, slots in _DYE_VARIANTS
        ]
        title = f"{_name(net_id)} #{net_id} {pass_name} uTime[{lo},{hi}) N={n}"
        fn = _save(f"dye_noise_{_name(net_id)}_{net_id}", _dye_band_sheet(bands, title))
        # measured offline animation (some noise passes carry no uTime term in the bytecode).
        anim = "yes" if _animates(dict(bands)["BODY"]) else "no (no uTime offline)"
        _INDEX.append((_name(net_id), str(net_id), pass_name, f"uTime={cur}", fn, anim))


def _static_dye_bands(net_id: int) -> list[tuple[str, list[_Cell]]]:
    """The 4 single-frame per-part variants (HEAD/BODY/LEGS/ALL) of a static dye, one cell
    per band (n=1). Every cell is 'current' (there is no sweep to choose from)."""
    return [
        (label, [_Cell(_render_dye(net_id, slots=slots), f"{_name(net_id)} {net_id}",
                       current=True)])
        for label, slots in _DYE_VARIANTS
    ]


def render_static_dyes() -> None:
    """One 4-variant banded sheet per APPROX-time + Reflective dye (no offline animation).

    These passes are offline-static, so each band is a single frame; the 4 bands let a
    head-only dye be inspected next to body/legs/all even when nothing animates."""
    for net_id in _APPROX_TIME:
        spec = _DYES.get(str(net_id), {})
        # Solar (3526): bytecode baked + wired (dye._solar) but the production default is the
        # handwritten emissive approx (the bytecode is dim offline -- no additive bloom), so this
        # sheet shows the handwritten fire ramp (offline-static).
        title = f"{_name(net_id)} #{net_id} {spec.get('pass', '')} (handwritten default; bytecode dim offline)"
        fn = _save(f"dye_static_{_name(net_id)}_{net_id}",
                   _dye_band_sheet(_static_dye_bands(net_id), title))
        _INDEX.append((
            _name(net_id), str(net_id), spec.get("pass", ""),
            "single (handwritten; bytecode dim offline)", fn, "no"))
    for net_id in _REFLECTIVE:
        spec = _DYES.get(str(net_id), {})
        # Reflective (class C): the real bytecode IS wired (dye._reflective[_color]); offline
        # uLightSource=0 so the moving specular highlight is absent -> a faithful no-highlight
        # frame (embossed source *0.5, ReflectiveColor + uColor tint). The physical offline limit.
        title = f"{_name(net_id)} #{net_id} {spec.get('pass', '')} (offline no-highlight, uLightSource=0)"
        fn = _save(f"dye_static_{_name(net_id)}_{net_id}",
                   _dye_band_sheet(_static_dye_bands(net_id), title))
        _INDEX.append((
            _name(net_id), str(net_id), spec.get("pass", ""),
            "single (offline no-highlight)", fn, "no"))


# ── dynamic glow rendering ────────────────────────────────────────────
@contextlib.contextmanager
def _glow_color(table: dict, key: str, color: list[int]) -> Iterator[None]:
    """Temporarily override a glow-table entry's representative colour (catalog section D.3)."""
    saved = json.loads(json.dumps(table.get(key)))  # deep copy (entry may be a dict/list)
    entry = table[key]
    if isinstance(entry, dict):
        entry["color"] = color
    else:
        table[key] = color
    try:
        yield
    finally:
        table[key] = saved


def _render_glow(part: str, net_id: int) -> np.ndarray:
    """Render the base character wearing glow item `net_id` in `part`."""
    equip = {part: {"netId": net_id}}
    return _decode(render_character(BASE_APP, equip, scale=SCALE))


def render_mousetext_glow() -> None:
    """mouseText pulse: num=(mc/255)^2, mc in [190,255] triangle (catalog B.1).

    Sweep the brightness scalar -> the additive glow colour [255*num]*3, A=0."""
    items = [
        ("body", 5052, compositor._GLOW_BODY, "237"), ("head", 5051, compositor._GLOW_HEAD, "268"),
        ("legs", 5053, compositor._GLOW_LEGS, "222"),
    ]
    mcs = list(range(190, 256, 5)) + list(range(250, 189, -10))  # one triangle period
    cur_mc = 222                                                  # baked representative
    for part, net_id, table, key in items:
        cells = []
        for i, mc in enumerate(mcs):
            num = (mc / 255.0) ** 2
            v = round(255 * num)
            with _glow_color(table, key, [v, v, v, 0]):
                img = _render_glow(part, net_id)
            cells.append(_Cell(img, f"MC{mc}", current=(mc == cur_mc and i < len(mcs) // 2 + 1)))
        title = f"mouseText {part} #{net_id} num=(mc/255)^2 mc[190,255] N={len(mcs)}"
        fn = _save(f"glow_mousetext_{part}_{net_id}", _contact_sheet(cells, title))
        anim = "yes" if _animates(cells) else "no (glow saturated)"
        _INDEX.append((
            f"mouseText {part}", str(net_id), "mouseText pulse",
            "mc=222 -> [193,193,193,0]", fn, anim))


def render_remap_glow() -> None:
    """ChickenBones (head 284) + Luna (head 292): Remap-pulse over miscCounter%100.

    phase = WrappedLerp triangle 0..1..0; num = Remap(phase, lo, hi); colour *= num."""
    specs = [
        ("ChickenBones", "head", 5583, compositor._GLOW_HEAD, "284", (0.8, 1.0), 0, "[229,229,229,0]"),
        ("Luna", "head", 6137, compositor._GLOW_HEAD, "292", (0.85, 1.0), 100, "[236,236,236,92]"),
    ]
    n = 20
    for nm, part, net_id, table, key, (lo, hi), base_a, cur_lbl in specs:
        cells = []
        cur_i = n // 4                                            # mid phase ~ baked still
        for i in range(n):
            phase = i / n
            tri = 1.0 - abs(2.0 * phase - 1.0)                   # WrappedLerp triangle 0..1..0
            num = lo + (hi - lo) * tri
            v = round(255 * num)
            a = round(base_a * num)
            with _glow_color(table, key, [v, v, v, a]):
                img = _render_glow(part, net_id)
            cells.append(_Cell(img, f"P{phase:.2f}", current=(i == cur_i)))
        title = f"{nm} {part} #{net_id} Remap[{lo},{hi}] miscCounter%100 N={n}"
        fn = _save(f"glow_remap_{nm}_{net_id}", _contact_sheet(cells, title))
        anim = "yes" if _animates(cells) else "no (glow saturated)"
        _INDEX.append((nm, str(net_id), "Remap pulse", f"mid -> {cur_lbl}", fn, anim))


def render_tv_head_glow() -> None:
    """TV head (head 271): 6x4 grid, col=GetTVScreen(offline 3 fixed), row=miscCounter%20/5.

    Sweep the 4 rows (col fixed at the offline-default 3); the production still is row 0."""
    net_id = 5061
    entry = compositor._GLOW_HEAD["271"]
    mask = f"Glow_{entry['mask']}"
    color = tuple(entry["color"])
    cells = []
    for row in range(compositor._TV_ROWS):
        comp = compositor._Compositor(BASE_APP)
        # build the head stack so the TV glow lands on a head (draw the armor head first).
        comp.draw_armor("Armor_Head_250", "col", None)          # a base helmet under the screen
        comp.draw_tv_head_glow(mask, color, None, cell=(compositor._TV_IDLE_COL, row))
        canvas = compositor._crop_to_content(comp.canvas)
        canvas = np.repeat(np.repeat(canvas, SCALE, 0), SCALE, 1)
        cells.append(_Cell(canvas, f"ROW{row}", current=(row == compositor._TV_IDLE_ROW)))
    title = f"TVHead #{net_id} col=3(fixed) row=miscCounter%20/5 N={compositor._TV_ROWS}"
    fn = _save(f"glow_tvhead_{net_id}", _contact_sheet(cells, title, cols=4))
    _INDEX.append(("TVHead", str(net_id), "TV grid (row only)", "col=3,row=0", fn, "partial"))


def render_head282_armor_frames() -> None:
    """head 282 DeadCells: ColorArmor 9-frame cycle (miscCounter%36/4); glow layer fixed.

    Sweep the 9 colour-armor frames (the additive mouseText glow layer stays at frame 0)."""
    net_id = 5457
    cells = []
    for fr in range(9):
        comp = compositor._Compositor(BASE_APP)
        comp.draw_armor("Armor_Head_282", "col", None, frame_row=fr)
        canvas = compositor._crop_to_content(comp.canvas)
        canvas = np.repeat(np.repeat(canvas, SCALE, 0), SCALE, 1)
        cells.append(_Cell(canvas, f"FR{fr}", current=(fr == 0)))
    title = f"DeadCellsHead #{net_id} ColorArmor miscCounter%36/4 N=9 (glow fixed frame0)"
    fn = _save(f"glow_head282_armorframes_{net_id}", _contact_sheet(cells, title, cols=5))
    _INDEX.append((
        "DeadCellsHead colorarmor", str(net_id), "armor 9-frame",
        "frame 0 (glow fixed)", fn, "yes"))


def render_jitter_glows() -> None:
    """Pseudo-random jitter / 4-tap glows (catalog B.3): single representative fan only.

    GroxTheGreat 2-tap (body 4756 / head 4755 / legs 4757) + ApprenticeAlt 4-tap
    (body 3875 / head 3874). Main.rand has no deterministic phase -> one frame, annotated."""
    items = [
        ("body", 4756, "GroxArmor 2-tap"), ("head", 4755, "GroxHelm 2-tap"),
        ("legs", 4757, "GroxGreaves 2-tap"),
        ("body", 3875, "ApprenticeAltShirt 4-tap"), ("head", 3874, "ApprenticeAltHead 4-tap"),
    ]
    cells = []
    for part, net_id, lbl in items:
        cells.append(_Cell(_render_glow(part, net_id), f"{lbl} {net_id}", current=True))
        _INDEX.append((
            lbl, str(net_id), "jitter/4-tap (pseudo-random)",
            "single representative fan", "glow_jitter_4tap.png", "no"))
    _save("glow_jitter_4tap",
          _contact_sheet(cells, "Jitter / 4-tap glows - single fan (Main.rand, no cycle)", cols=5))


# ── index.md ──────────────────────────────────────────────────────────
def write_index() -> str:
    lines = [
        "# Dynamic dye / glow per-frame contact sheets",
        "",
        "Generated by `nextbot/terraria_render/_build/render_dynamic_frames.py` from "
        "`research/dynamic_effects_catalog.md`. Each scannable effect is swept across one "
        "animation cycle; the production 'current' still is highlighted with a green border. "
        "Static effects (APPROX time passes, Reflective, jitter/4-tap) show a single frame.",
        "",
        "## Dye sheets: 4 per-part variants",
        "",
        "A dye is an `ArmorShaderData` applied per equip slot, so head / body / legs dye "
        "independently. Every `dye_time_*` / `dye_noise_*` / `dye_static_*` sheet is therefore "
        "a stack of **four labelled row-bands** — `HEAD` (dye on the head slot only), `BODY`, "
        "`LEGS`, and `ALL` (all three) — each band a row of the swept animation frames "
        "(columns = phase / uTime; the current frame is the green-bordered cell). Static dyes "
        "use one frame per band (the 4 variants side by side).",
        "",
        "Base armor for the dye sheets is the **Silver set** (neutral mid-grey across "
        "head/body/legs). A near-greyscale base shows each dye's TRUE colour: with no strong "
        "native hue of its own the silver armour lets the dye's own colour come through "
        "faithfully (a coloured base would skew/muddy it), while its highlight / mid / shadow "
        "bands keep gradient/recolor shading bugs visible.",
        "",
        "- head: **SilverHelmet** netId 91 (slot 3)",
        "- body: **SilverChainmail** netId 82 (slot 3)",
        "- legs: **SilverGreaves** netId 78 (slot 3)",
        "",
        "GLOW sheets are unchanged: each still wears its matching glow item (no per-part "
        "split).",
        "",
        "| effect | item netId | type | current frame | image | real animation? |",
        "|---|---|---|---|---|---|",
    ]
    for eff, nid, typ, cur, img, anim in _INDEX:
        lines.append(f"| {eff} | {nid} | {typ} | {cur} | `{img}` | {anim} |")
    lines.append("")
    path = OUT_DIR / "index.md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return str(path)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[render_dynamic_frames] out -> {OUT_DIR}")
    render_scannable_dyes()
    render_static_dyes()
    render_mousetext_glow()
    render_remap_glow()
    render_tv_head_glow()
    render_head282_armor_frames()
    render_jitter_glows()
    idx = write_index()
    n_sheets = len({row[4] for row in _INDEX})
    print(f"[render_dynamic_frames] wrote {n_sheets} contact sheets + index -> {idx}")
    print(f"[render_dynamic_frames] {len(_INDEX)} effects catalogued")


if __name__ == "__main__":
    main()
