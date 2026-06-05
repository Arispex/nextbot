"""Pure-numpy ps_2_0 interpreter for the noise-sampling dye passes.

Runs the *actual* compiled Terraria shader bytecode (baked into
``data/noise_shaders.json`` by ``_build/gen_noise_shaders.py``) per pixel, with a
real bilinear-wrap sample of the noise texture (``assets/noise.png``) and the
HallowBoss palette (``assets/Extra_156.png``). This makes the 8 ``Misc/noise``
passes + HallowBoss + the Twilight hair dye accurate instead of flat-tint
approximations (research/noise_dyes_spec.md).

No LZX/XNB at runtime: the blobs are pre-extracted bytes; this module only
decodes their embedded CTAB (uniform->const-register), PRES (D3DX preshader) and
``def`` literals, runs the preshader to fill the derived consts, then executes the
pixel program. Validated 1:1 against ``temp/xnb_probe/ps_interp_full.py``.

Each pass operates on PREMULTIPLIED (h, w, 4) float in [0,1] (``dye.py`` does the
straight<->premult conversion) and returns premultiplied ``oC0`` rgba.

Single-letter / register names (r0,c5,t0,oC0…) mirror the disassembled shader;
see this module's ruff per-file-ignores.
"""
from __future__ import annotations

import base64
import functools
import json
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .image_io import read_png

if TYPE_CHECKING:
    from collections.abc import Mapping

_HERE = Path(__file__).resolve().parent
_ASSETS = _HERE / "assets"
_DATA = _HERE / "data"

# Effect parameter layout (PixelShader.xnb param order) — the preshader's input
# registers index into this; gen_noise_shaders bakes the per-pass subset by name.
_NOISE_SIZE = (256, 256)
_EXTRA_SIZE = (512, 512)
# Representative still: GlobalTimeWrappedHourly frozen at 0 for animated terms.
_UTIME = 0.0

# ── Reflective specular light (ADJUSTABLE — controls the highlight brightness) ──
# The two Reflective passes (ArmorReflective 3190 + ArmorReflectiveColor 3026/3027/3553/3554/
# 3555) light a 5-tap emboss with `uLightSource`, a unit surface NORMAL the game derives from
# the entity's live lighting gradient (ReflectiveArmorShaderData.cs:29-78). Offline there is no
# entity, so the game forces it to Vector3.Zero -> `dp3 N.L == 0` -> the metallic highlight
# collapses to a dull dark metal. We instead bind a STATIC representative FRONT light (the
# shader's +Z surface normal / a head-on viewer) so the highlight statically lights up.
#
# This is a GROUNDED APPROXIMATION (the game's specular MOVES with the lighting gradient, which
# is physically unavailable offline). Its z component IS the highlight strength: the shader's
# `dp3 N.L` scales linearly with |L|, so a SMALLER z makes a WEAKER/DARKER highlight. The old
# value (0,0,1) ran the specular at full intensity and read OVER-BRIGHT (luma ~176 on
# Armor_Head_276 vs a ~117 source -- a washed-out near-white chrome, not the game's metallic
# reflection). 0.7 is the "bright reflection, not over-exposed" tier the user picked off the
# `_diag_reflective_dim.png` scan -- a believable metal sheen still clearly brighter than the
# no-highlight source, without the washed-out near-white of full 1.0. ADJUSTABLE: sweep
# `temp/dynamic_frames/_diag_reflective_dim.png`
# (light-z ∈ {1.0 over-bright, 0.7, 0.5, 0.35, 0.0 no-highlight}) to pick the final value; this
# is the landed default. ONLY the two Reflective passes read uLightSource (its CTAB register) --
# every other dye pass ignores it, so changing this affects NOTHING else.
_REFLECTIVE_LIGHT = np.array([0.0, 0.0, 0.7, 0.0])


# ── baked shader blobs + lazy textures ───────────────────────────────
@functools.cache
def _shaders() -> dict[str, dict]:
    p = _DATA / "noise_shaders.json"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return json.load(f)


@functools.cache
def _noise_tex() -> np.ndarray | None:
    """noise.png -> (256,256,4) float[0,1] (alpha==1; straight==premult)."""
    p = _ASSETS / "noise.png"
    return read_png(str(p)).astype(np.float64) / 255.0 if p.exists() else None


@functools.cache
def _extra_tex() -> np.ndarray | None:
    """Extra_156.png -> (512,512,4) float[0,1] (HallowBoss colored palette)."""
    p = _ASSETS / "Extra_156.png"
    return read_png(str(p)).astype(np.float64) / 255.0 if p.exists() else None


def has_noise_assets() -> bool:
    """True iff the baked blobs + noise.png are present (else dye.py falls back)."""
    return bool(_shaders()) and _noise_tex() is not None


# ── bilinear-wrap texture sample (research/noise_dyes_spec.md §3 snippet) ──
def _sample_tex(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """tex (H,W,4) float; uv (...,2) -> (...,4). Bilinear, WRAP addressing."""
    h, w = tex.shape[:2]
    u = (uv[..., 0] % 1.0) * w - 0.5
    v = (uv[..., 1] % 1.0) * h - 0.5
    x0 = np.floor(u).astype(np.int64)
    y0 = np.floor(v).astype(np.int64)
    fx = (u - x0)[..., None]
    fy = (v - y0)[..., None]
    x0m, x1m = x0 % w, (x0 + 1) % w
    y0m, y1m = y0 % h, (y0 + 1) % h
    c00, c10 = tex[y0m, x0m], tex[y0m, x1m]
    c01, c11 = tex[y1m, x0m], tex[y1m, x1m]
    top = c00 * (1 - fx) + c10 * fx
    bot = c01 * (1 - fx) + c11 * fx
    return top * (1 - fy) + bot * fy


def _sample_src(
    src_rgba: np.ndarray, uv: np.ndarray, src_rect: tuple[int, int, int, int],
    sheet_size: tuple[int, int],
) -> np.ndarray:
    """Sample the cropped source frame `src_rgba` at sheet-space `uv` (..,2) -> (..,4).

    The self-sampling passes (MidnightRainbow/Gel/Phase/Solar emboss + blur) `texld`
    uImage0 at uv OFFSET by ±k/sheet (their tap stencil). `src_rgba` is the already
    cropped (H,W) cell, while the shader's uv is in the *full sheet*'s units
    (``(sx+col+0.5)/sheet_w``); invert it back to a frame-local pixel
    ``px = uv.x*sheet_w - sx`` and bilinear-sample with **CLAMP** addressing (D3D
    SamplerState on uImage0 — wrap would bleed the neighbouring cell across the frame
    seam). A center (unoffset) ``t0`` uv lands exactly on the texel center, so this is
    bit-identical to the old center-collapse for the single-tap noise passes (their
    only source tap is the plain ``t0``); it only differs for the offset stencil taps.
    """
    h, w = src_rgba.shape[:2]
    sx, sy, _sw, _sh = src_rect
    sheet_w, sheet_h = sheet_size
    fxp = uv[..., 0] * sheet_w - sx - 0.5  # frame-local pixel x (texel-center origin)
    fyp = uv[..., 1] * sheet_h - sy - 0.5
    x0 = np.clip(np.floor(fxp).astype(np.int64), 0, w - 1)
    y0 = np.clip(np.floor(fyp).astype(np.int64), 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    tx = np.clip(fxp - x0, 0.0, 1.0)[..., None]
    ty = np.clip(fyp - y0, 0.0, 1.0)[..., None]
    c00, c10 = src_rgba[y0, x0], src_rgba[y0, x1]
    c01, c11 = src_rgba[y1, x0], src_rgba[y1, x1]
    top = c00 * (1 - tx) + c10 * tx
    bot = c01 * (1 - tx) + c11 * tx
    return top * (1 - ty) + bot * ty


# ── blob parsing: CTAB (uniform->creg), def literals, PRES (preshader) ──
def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


@functools.cache
def _parse_blob(name: str) -> tuple[bytes, dict[str, tuple[int, int]], dict[int, str]] | None:
    """-> (blob, ctab_float {uniform:(reg,cnt)}, sampler {reg:uniform}) or None."""
    entry = _shaders().get(name)
    if not entry:
        return None
    blob = base64.b64decode(entry["blob"])
    cmap, smap = {}, {}
    cp = blob.find(b"CTAB")
    if cp >= 0:
        st = cp + 4
        nconst = _u32(blob, st + 12)
        coff = _u32(blob, st + 16)
        if nconst <= 128:
            for k in range(nconst):
                ci = st + coff + k * 20
                nameoff = _u32(blob, ci)
                regset = struct.unpack_from("<H", blob, ci + 4)[0]
                regidx, regcnt = struct.unpack_from("<HH", blob, ci + 6)
                end = blob.find(b"\x00", st + nameoff)
                nm = blob[st + nameoff:end].decode("ascii", "replace")
                if regset == 2:  # FLOAT4 -> c registers
                    cmap[nm] = (regidx, regcnt)
                elif regset == 3:  # SAMPLER -> s registers
                    smap[regidx] = nm
    return blob, cmap, smap


# ── D3DX preshader (FXLC/CLIT) — scalar, runs once per frame for consts ──
_PRES_OPS = {0x100: "mov", 0x101: "neg", 0x103: "rcp", 0x104: "frc", 0x105: "exp",
             0x106: "log", 0x107: "rsq", 0x108: "sin", 0x109: "cos", 0x200: "min",
             0x201: "max", 0x202: "lt", 0x203: "ge", 0x204: "add", 0x205: "mul",
             0x206: "atan2", 0x208: "div", 0x300: "cmp", 0x500: "dot"}


def _decode_preshader(blob: bytes) -> tuple[list[float], list]:
    # A pass with NO preshader (no FXLC block: BrightnessColored/Invert/ColorOnly/Martian/
    # Polarized/Mushroom) has no derived consts to fill -> return empties. Without this the
    # `clit`/`fxlc` lookups below find -1 and read garbage past the buffer end (struct error;
    # research/dye_bytecode_audit.md gap #1). Its CTAB-set consts are still bound in run_noise_pass.
    if blob.find(b"FXLC") < 0:
        return [], []
    clit = blob.find(b"CLIT")
    nlit = _u32(blob, clit + 4)
    lits = [struct.unpack_from("<d", blob, clit + 8 + i * 8)[0] for i in range(nlit)]
    fxlc = blob.find(b"FXLC")
    ninst = _u32(blob, fxlc + 4)
    o = fxlc + 8

    def read_operand() -> tuple[int, int]:
        nonlocal o
        rel = _u32(blob, o)
        o += 4
        if rel:
            o += 8  # relative-addressing operand (unused by these passes)
        table = _u32(blob, o)
        offset = _u32(blob, o + 4)
        o += 8
        return table, offset

    insns = []
    for _ in range(ninst):
        ins_raw = _u32(blob, o)
        o += 4
        opcode = (ins_raw & 0x7FF00000) >> 20
        comps = ins_raw & 0xFFFF
        in_count = _u32(blob, o)
        o += 4
        srcs = [read_operand() for _ in range(in_count)]
        dst = read_operand()
        insns.append((_PRES_OPS.get(opcode, f"?{opcode:#x}"), comps, srcs, dst))
    return lits, insns


def _run_preshader(blob: bytes, inputs: Mapping[int, np.ndarray]) -> dict[int, np.ndarray]:
    """Returns {const_reg: vec4} for the shader consts the preshader fills."""
    lits, insns = _decode_preshader(blob)
    imm = lits + [0.0] * 64
    temp: dict[int, list[float]] = {}
    out: dict[int, list[float]] = {}
    inp = {k: np.asarray(v, dtype=np.float64) for k, v in inputs.items()}

    def get(table: int, off: int, ci: int) -> float:
        # tables 0 (IMM) and 1 (CONST) both index the CLIT literal doubles by `off`
        # (Wine d3dx9 reg_table). Solar/Stardust/HallowBoss reference their literals
        # via table 1 (e.g. Stardust `mul OUTb[8] <- uTime.x, CONST[8]=0.2`); reading
        # only table 0 made those literals 0 -> the uTime brightness/phase terms froze.
        # A LITERAL is a scalar: it BROADCASTS across the instruction's components (read
        # `off` for every `ci`, never `off+ci`). Advancing the literal offset per-component
        # read the next CLIT slot (a 0) instead -> e.g. ArmorColored's `c0 = 1 - uColor`
        # collapsed to `-uColor` (gray, not copper). The register tables (input/temp/output)
        # DO advance per-component, with a boundary clamp (D3DX broadcasts the last lane when
        # a 1-wide source feeds a vector op) — matches the validated ps_interp reference.
        if table in (0, 1):
            return imm[off] if off < len(imm) else 0.0
        o = off + (ci if (off & 3) + ci < 4 else 0)
        reg, comp = o >> 2, o & 3
        if table == 2:
            return float(inp.get(reg, np.zeros(4))[comp])
        if table in (6, 7):
            return temp.get(reg, [0.0, 0.0, 0.0, 0.0])[comp]
        if table == 4:
            return out.get(reg, [0.0, 0.0, 0.0, 0.0])[comp]
        return 0.0

    def setv(table: int, off: int, val: float) -> None:
        reg, comp = off >> 2, off & 3
        tgt = temp if table in (6, 7) else (out if table == 4 else None)
        if tgt is not None:
            tgt.setdefault(reg, [0.0, 0.0, 0.0, 0.0])[comp] = val

    for op, comps, srcs, dst in insns:
        for ci in range(comps):
            a = [get(t, off, ci) for (t, off) in srcs]
            if op == "mov":
                r = a[0]
            elif op == "neg":
                r = -a[0]
            elif op == "add":
                r = a[0] + a[1]
            elif op == "mul":
                r = a[0] * a[1]
            elif op == "div":
                r = a[0] / a[1] if a[1] != 0 else 0.0
            elif op == "rcp":
                r = 1.0 / a[0] if a[0] != 0 else 0.0
            elif op == "min":
                r = min(a[0], a[1])
            elif op == "max":
                r = max(a[0], a[1])
            elif op == "lt":
                r = 1.0 if a[0] < a[1] else 0.0
            elif op == "ge":
                r = 1.0 if a[0] >= a[1] else 0.0
            elif op == "cmp":
                r = a[1] if a[0] >= 0.0 else a[2]
            elif op == "frc":
                r = a[0] - np.floor(a[0])
            elif op == "sin":
                r = np.sin(a[0])
            elif op == "cos":
                r = np.cos(a[0])
            else:
                msg = f"preshader op {op} not impl"
                raise RuntimeError(msg)
            setv(dst[0], dst[1] + ci, float(r))
    return {reg: np.asarray(v, dtype=np.float64) for reg, v in out.items()}


# ── ps_2_0 pixel interpreter (vectorized over the (H,W) frame) ──────────
def _regtype(tok: int) -> int:
    return ((tok >> 28) & 0x7) | ((tok >> 8) & 0x18)


def _src(regs: dict, tok: int) -> np.ndarray:
    rt, rn = _regtype(tok), tok & 0x7FF
    v = regs[(rt, rn)]
    sw = (tok >> 16) & 0xFF
    comps = [(sw >> (2 * i)) & 3 for i in range(4)]
    v = np.stack([v[..., c] for c in comps], -1)
    mod = (tok >> 24) & 0xF
    if mod == 1:
        v = -v
    elif mod == 0xB:
        v = np.abs(v)
    elif mod == 0xC:
        v = -np.abs(v)
    return v


def _dst(regs: dict, tok: int, val: np.ndarray) -> None:
    rt, rn = _regtype(tok), tok & 0x7FF
    cur = regs[(rt, rn)].copy()
    mask = (tok >> 16) & 0xF
    if (tok >> 20) & 0x1:  # D3DSPDM_SATURATE: clamp result to [0,1] before masking
        val = np.clip(val, 0.0, 1.0)
    for i in range(4):
        if mask & (1 << i):
            cur[..., i] = val[..., i]
    regs[(rt, rn)] = cur


def _run_ps(  # faithful 1:1 bytecode dispatch (opcode table mirrors d3d9types.h)
    blob: bytes,
    src_rgba: np.ndarray,
    consts: dict[int, np.ndarray],
    uv: np.ndarray,
    samplers: dict[int, np.ndarray],
    src_rect: tuple[int, int, int, int],
    sheet_size: tuple[int, int],
) -> np.ndarray:
    """src_rgba (H,W,4) premult. consts {creg:vec4}. uv (H,W,2). Returns oC0 (H,W,4).

    `src_rect`/`sheet_size` locate the cropped `src_rgba` in its sheet so a uImage0
    `texld` at an OFFSET uv (the self-emboss/blur taps) resolves to the right frame
    pixel (clamped bilinear); the noise samplers (uImage1) are unchanged.
    """
    h, w = src_rgba.shape[:2]

    class _Z(dict):
        def __missing__(self, key: object) -> np.ndarray:
            self[key] = np.zeros((h, w, 4))
            return self[key]

    regs: dict = _Z()
    regs[(1, 0)] = np.ones((h, w, 4))  # v0 = vertex color (white)
    t0 = np.zeros((h, w, 4))
    t0[..., 0], t0[..., 1] = uv[..., 0], uv[..., 1]
    regs[(3, 0)] = t0
    for cr, vec in consts.items():
        c = np.zeros((h, w, 4))
        c[:] = vec
        regs[(2, cr)] = c

    o = 4
    while o + 4 <= len(blob):
        tok = _u32(blob, o)
        if tok == 0x0000FFFF:
            break
        op = tok & 0xFFFF
        if op == 0xFFFE:  # comment (CTAB/PRES)
            o += 4 + ((tok >> 16) & 0x7FFF) * 4
            continue
        ilen = (tok >> 24) & 0xF
        o += 4
        if op == 0x1F:  # dcl
            o += 8
            continue
        if op == 0x51:  # def c#, f,f,f,f
            dtok = _u32(blob, o)
            vals = np.array(struct.unpack_from("<4f", blob, o + 4))
            c = np.zeros((h, w, 4))
            c[:] = vals
            regs[(_regtype(dtok), dtok & 0x7FF)] = c
            o += 4 + 16
            continue
        toks = [_u32(blob, o + 4 * k) for k in range(ilen)]
        o += ilen * 4
        dtok = toks[0]
        if op == 0x42:  # texld dst, uv, sampler
            samp = samplers.get(toks[2] & 0x7FF)
            uvt = _src(regs, toks[1])[..., :2]
            # uImage1 (noise) -> wrap-sample; uImage0 (source) -> clamp-sample the cell at
            # the (possibly offset) uv so the self-emboss/blur taps are honoured.
            res = _sample_src(src_rgba, uvt, src_rect, sheet_size) if samp is None \
                else _sample_tex(samp, uvt)
            _dst(regs, dtok, res)
            continue
        s = [_src(regs, t) for t in toks[1:]]
        if op == 0x01:  # mov
            res = s[0]
        elif op == 0x02:  # add
            res = s[0] + s[1]
        elif op == 0x03:  # sub
            res = s[0] - s[1]
        elif op == 0x04:  # mad
            res = s[0] * s[1] + s[2]
        elif op == 0x05:  # mul
            res = s[0] * s[1]
        elif op == 0x06:  # rcp
            d = s[0]
            res = np.where(d != 0, 1.0 / np.where(d == 0, 1.0, d), 0.0)
        elif op == 0x07:  # rsq
            d = np.abs(s[0])
            res = np.where(d != 0, 1.0 / np.sqrt(np.where(d == 0, 1.0, d)), 0.0)
        elif op == 0x08:  # dp3 (3-component dot, broadcast to all 4 lanes)
            res = np.repeat((s[0][..., :3] * s[1][..., :3]).sum(-1, keepdims=True), 4, -1)
        elif op == 0x09:  # dp4 (4-component dot, broadcast to all 4 lanes)
            res = np.repeat((s[0] * s[1]).sum(-1, keepdims=True), 4, -1)
        elif op == 0x0A:  # min
            res = np.minimum(s[0], s[1])
        elif op == 0x0B:  # max
            res = np.maximum(s[0], s[1])
        elif op == 0x12:  # lrp: src2 + src0*(src1 - src2)
            res = s[2] + s[0] * (s[1] - s[2])
        elif op == 0x13:  # frc
            res = s[0] - np.floor(s[0])
        elif op == 0x23:  # abs (D3DSIO_ABS=0x23; fx_parse mislabels it "pow")
            res = np.abs(s[0])
        elif op == 0x25:  # sgn: sign(src0) per component (src1/src2 are scratch)
            res = np.sign(s[0])
        elif op == 0x58:  # cmp: src0>=0 ? src1 : src2
            res = np.where(s[0] >= 0.0, s[1], s[2])
        elif op == 0x5A:  # dp2add
            res = np.repeat(
                (s[0][..., :2] * s[1][..., :2]).sum(-1, keepdims=True) + s[2][..., :1], 4, -1)
        else:
            msg = f"ps op {op:#x} not impl"
            raise RuntimeError(msg)
        _dst(regs, dtok, res)
    return regs[(8, 0)]  # oC0


# ── public entry: run one baked noise pass on a premult frame ──────────
def run_noise_pass(
    premul: np.ndarray,
    name: str,
    *,
    u_color: np.ndarray,
    u_secondary: np.ndarray,
    u_sat: float,
    src_rect: tuple[int, int, int, int],
    sheet_size: tuple[int, int],
    u_time: float = _UTIME,
) -> np.ndarray | None:
    """Run baked pass `name` per-pixel with real noise sampling.

    premul: (H,W,4) PREMULTIPLIED rgba in [0,1]. Returns premult oC0, or None if
    the blob/textures are unavailable (caller falls back to the APPROX body).
    `u_time` is the frozen GlobalTimeWrappedHourly for this still (default 0); the
    animated/emissive pillar passes (Solar/Nebula/Vortex/Stardust/HallowBoss) bake a
    per-pass representative value in dye.py so the frozen frame reads bright.
    """
    parsed = _parse_blob(name)
    noise = _noise_tex()
    if parsed is None or noise is None:
        return None
    blob, cmap, smap = parsed
    h, w = premul.shape[:2]
    sx, sy, sw, sh = src_rect
    sheet_w, sheet_h = sheet_size

    # sprite uv (t0): pixel center within the *sheet* (research §3 uv recipe).
    uv = np.zeros((h, w, 2))
    uv[..., 0] = (sx + np.arange(w)[None, :] + 0.5) / sheet_w
    uv[..., 1] = (sy + np.arange(h)[:, None] + 0.5) / sheet_h

    # effect parameter values for this still (uImageSize1 = noise size).
    params: dict[str, np.ndarray] = {
        "uColor": np.append(u_color, 1.0),
        "uSecondaryColor": np.append(u_secondary, 1.0),
        "uSaturation": np.array([u_sat, u_sat, u_sat, u_sat]),
        "uSourceRect": np.array([sx, sy, sw, sh], dtype=np.float64),
        "uImageSize0": np.array([sheet_w, sheet_h, 0.0, 0.0]),
        "uTime": np.array([u_time, 0.0, 0.0, 0.0]),
        "uDirection": np.array([1.0, 0.0, 0.0, 0.0]),
        "uRotation": np.array([0.0, 0.0, 0.0, 0.0]),
        # Reflective/ReflectiveColor specular light: a STATIC front-normal stand-in for the
        # game's live lighting gradient (forced to zero offline). Its z is the highlight
        # strength -- see `_REFLECTIVE_LIGHT` above (default 0.7, dimmed from the over-bright
        # full-intensity 1.0; sweep `_diag_reflective_dim.png` to retune). ONLY the two
        # Reflective passes read uLightSource; every other pass ignores it.
        "uLightSource": _REFLECTIVE_LIGHT,
    }
    # uImage1 is the noise texture, except HallowBoss which samples the colored palette.
    tex1 = noise
    if name == "ArmorHallowBoss":
        extra = _extra_tex()
        if extra is None:
            return None
        tex1 = extra
    params["uImageSize1"] = np.array([tex1.shape[1], tex1.shape[0], 0.0, 0.0])

    consts: dict[int, np.ndarray] = {}
    # preshader-derived consts (low registers, disjoint from the CTAB params)
    entry = _shaders()[name]
    pres_inputs = {int(k): params[v] for k, v in entry["pres_inputs"].items()}
    consts.update(_run_preshader(blob, pres_inputs))
    # effect-set consts (CTAB uniform -> register), overrides nothing the preshader set
    for nm, (reg, _cnt) in cmap.items():
        if nm in params:
            consts[reg] = params[nm]

    samplers = {reg: tex1 for reg, nm in smap.items() if nm == "uImage1"}
    return _run_ps(blob, premul, consts, uv, samplers, src_rect, sheet_size)
