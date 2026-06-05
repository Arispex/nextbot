"""DEV-ONLY one-time noise-shader baking (not used at runtime).

Extracts the ps_2_0 bytecode blob for each noise-sampling dye pass from
Terraria's compiled effect (PixelShader.xnb) and bakes it — base64-encoded,
together with the pass's preshader input-register -> effect-parameter map — into
../data/noise_shaders.json. The runtime (dye_noise.py) then runs those blobs
through a pure-numpy ps_2_0 interpreter with real noise sampling, so the package
ships NO LZX/XNB decoder (PRD asset strategy A: runtime = PNG + numpy only).

    python3 gen_noise_shaders.py ["/path/to/temp/xnb_probe"]

The probe dir must contain in/PixelShader.xnb + fx_parse.py + pres_decode.py
(the validated reverse-engineering tools). Produces:
    noise_shaders.json   {pass: {"blob": b64, "pres_inputs": {creg: paramName}}}

Each blob embeds its own CTAB (uniform -> shader-const register map), PRES
(preshader) and def literals; only the preshader INPUT-register -> parameter map
is not recoverable from the blob (it comes from the effect's parameter layout),
so it is baked here from the verified table in research/noise_dyes_spec.md §3.
"""
from __future__ import annotations

import base64
import json
import os
import sys

# Verified preshader input-register -> effect-parameter map per pass.
# Source: each pass's PRES block carries its OWN second CTAB whose entries map the
# preshader's INPUT registers (in_cN) -> effect parameter name (decoded this session,
# see research/dye_passes_spec.md §"Preshader input-register" caveat + the PRES-CTAB
# dump in temp/xnb_probe). For the tile-and-divide family (Stardust/Nebula/Vortex) the
# scale const c0 must come out to (128,128) = noise_size/2, so the size input is fed
# the NOISE size (uImageSize1=256), even though the PRES-CTAB labels it "uImageSize0":
# the runtime binds uImageSize1 to the noise dims, which is what makes c0=128.
#   - Stardust in_c0 = uTime (preshader: c2.x = uTime*0.2 -> starfield phase). The earlier
#     map fed uSourceRect here, which froze the phase at uSourceRect.x*0.2 (=16) and made
#     the starfield time-invariant / wrong. Fixed to uTime.
PRES_INPUTS = {
    "ArmorStardust": {0: "uTime", 1: "uImageSize1"},
    "ArmorNebula": {0: "uSourceRect", 1: "uImageSize1"},
    "ArmorVortex": {0: "uSourceRect", 1: "uImageSize1"},
    "ArmorShiftingSands": {0: "uColor", 1: "uSourceRect", 2: "uImageSize1"},
    "ArmorShiftingPearlsands": {0: "uColor", 1: "uSourceRect", 2: "uImageSize1"},
    "ArmorFog": {0: "uColor", 1: "uSourceRect", 2: "uImageSize1"},
    "ArmorPhase": {0: "uColor", 1: "uSaturation", 2: "uSecondaryColor",
                   3: "uSecondaryColor", 4: "uSourceRect", 5: "uImageSize1"},
    "ArmorGel": {0: "uColor", 1: "uSecondaryColor", 2: "uTime", 3: "uSaturation",
                 4: "uSecondaryColor", 5: "uImageSize1"},
    "ArmorHallowBoss": {0: "uTime"},
    "ArmorTwilight": {0: "uColor", 1: "uSourceRect", 2: "uDirection", 3: "uImageSize0"},
    # ArmorMidnightRainbow (item 3556) is a 5-tap self-emboss of uImage0 (NO noise texture)
    # with a preshader: c0=2/uImageSize0.y, c1=2/uImageSize0.x (the ±2px tap offsets),
    # c2=1/uSourceRect.z (normalize frame-local x), c3=uTime*0.4 (the rainbow hue scroll).
    # in_c0=uTime, in_c1=uSourceRect, in_c2=uImageSize0 (verified PRES-CTAB, research/
    # midnight_rainbow.md §1). The emboss magnitude gates the whole rainbow output, so the
    # source offset taps MUST be honoured by dye_noise._run_ps (the offset-tap fix).
    "ArmorMidnightRainbow": {0: "uTime", 1: "uSourceRect", 2: "uImageSize0"},
    # ArmorSolar (item 3526) is self-sampling (5 taps of uImage0, NO noise texture) but
    # has a preshader: c2 = sin(uTime*0.477+0.5)*0.2 + 1.0 (a brightness pulse), c3 =
    # uSecondaryColor.x - uColor.x, c0/c1 = 1/uImageSize0 (tap offsets). in_c2 = uTime.
    "ArmorSolar": {0: "uColor", 1: "uSecondaryColor", 2: "uTime", 3: "uImageSize0"},
    # ── Batch 1: non-animated recolor / gradient / colour-driven passes ──────────
    # These are pure uImage0 passes (no noise texture); the recolor family multiplies the
    # source by a per-pixel HSL-remapped colour, the gradient family varies that colour along
    # uv.x. PRES_INPUTS recovered from each pass's PRES-CTAB (research/dye_bytecode_audit.md
    # §"recommended batch 1"). uColor/uSecondaryColor/uSaturation/uSourceRect are bound by
    # run_noise_pass; v0 (vertex colour) = white = the inventory/standard white draw colour.
    "ArmorColored": {0: "uColor", 1: "uSaturation"},
    "ArmorColoredAndBlack": {0: "uColor", 1: "uSaturation"},
    # AndSilverTrim/AndSilverTrimGradient read uColor via the CTAB (not the preshader), so the
    # preshader input is only uSaturation / the gradient endpoints + uSourceRect.
    "ArmorColoredAndSilverTrim": {0: "uSaturation"},
    "ArmorColoredGradient": {0: "uColor", 1: "uSecondaryColor", 2: "uSaturation",
                             3: "uSourceRect"},
    "ArmorColoredAndBlackGradient": {0: "uColor", 1: "uSecondaryColor", 2: "uSaturation",
                                     3: "uSourceRect"},
    "ArmorColoredAndSilverTrimGradient": {0: "uColor", 1: "uSecondaryColor", 2: "uSaturation",
                                          3: "uSourceRect"},
    "ArmorBrightnessGradient": {0: "uColor", 1: "uSecondaryColor", 2: "uSourceRect"},
    "ArmorColoredRainbow": {0: "uSaturation", 1: "uSourceRect"},
    "ArmorBrightnessRainbow": {0: "uSourceRect"},
    "ArmorWisp": {0: "uColor", 1: "uSecondaryColor"},
    # Six passes have NO preshader (empty PRES_INPUTS): their colour (when any) comes from a
    # `def` const or the CTAB. _decode_preshader's FXLC guard makes the empty map safe.
    "ArmorBrightnessColored": {},
    "ArmorInvert": {},
    "ColorOnly": {},
    "ArmorMartian": {},
    "ArmorPolarized": {},
    "ArmorMushroom": {},
    # ── Batch 2: animated / self-sampling time passes (research/dye_bytecode_audit.md
    # §"third tier"). uImage0-only (no noise texture); the Living*/Flow/Acid passes do a
    # positional+luma sincos band, Void/Mirage/Hades/Loki self-sample uImage0 at offset
    # taps (the offset-tap fix in dye_noise._run_ps honours them). PRES_INPUTS recovered
    # from each pass's PRES-CTAB (the 2nd CTAB embedded in the FXLC block; verified this
    # session, identical to the audit table). uColor/uSecondaryColor/uSourceRect/uImageSize0
    # /uTime/uRotation are bound by run_noise_pass; v0 (vertex colour) = white.
    "ArmorFlow": {0: "uColor", 1: "uSecondaryColor"},
    "ArmorLivingRainbow": {0: "uTime", 1: "uSourceRect"},
    "ArmorLivingFlame": {0: "uSourceRect"},
    "ArmorLivingOcean": {0: "uTime", 1: "uSourceRect"},
    "ArmorAcid": {0: "uTime", 1: "uSourceRect"},
    # Void/Mirage/Hades/Loki self-sample uImage0 with ±k/uImageSize0 tap offsets (their
    # emboss/blur stencil) and scroll by uTime; Hades/Loki additionally rotate the taps by
    # uRotation (=0 for a non-rotated sprite, ArmorShaderData.cs:97/105).
    "ArmorVoid": {0: "uTime", 1: "uImageSize0"},
    "ArmorMirage": {0: "uTime", 1: "uSourceRect", 2: "uImageSize0"},
    "ArmorHades": {0: "uRotation", 1: "uTime", 2: "uSourceRect", 3: "uImageSize0"},
    "ArmorLoki": {0: "uRotation", 1: "uTime", 2: "uSourceRect", 3: "uImageSize0"},
    # ── Batch 3 (final): the last 3 special passes (research/dye_bytecode_audit.md
    # §"recommended batch 3/5"). PRES_INPUTS recovered from each pass's 2nd (PRES-embedded)
    # CTAB this session (verified against the table). uColor/uSaturation/uImageSize0 are
    # bound by run_noise_pass; v0 (vertex colour) = white = the inventory white draw colour.
    #   - ArmorHighContrastGlow (2883): a CORRECTION — the handwritten port dropped the
    #     v0-driven glow term; the real bytecode adds it (gates the high-contrast green glow on
    #     pixel CHROMA, so a zero-chroma/grey pixel crushes to black, a chromatic one glows).
    #     in_c0=uColor, in_c1=uSaturation; the shader also reads preshader-derived c1/c2 and
    #     CTAB uColor=c3 / uSaturation=c4.
    "ArmorHighContrastGlow": {0: "uColor", 1: "uSaturation"},
    #   - ArmorReflective (3190) / ArmorReflectiveColor (3026/3027/3553/3554/3555): class C.
    #     The 5-tap emboss is lit by `uLightSource` (a live lighting-gradient normal, CTAB c2/
    #     c3) which is 0 offline -> the moving specular highlight VANISHES; what remains is the
    #     faithful no-highlight version (≈ source through the emboss DC * 0.5, ReflectiveColor
    #     additionally uColor-tinted). uLightSource is NOT in run_noise_pass's params, so the
    #     CTAB loop leaves c2/c3 at the interpreter's zero default = exactly the offline limit.
    #     The only preshader input is uImageSize0 (the ±1px tap offsets).
    "ArmorReflective": {0: "uImageSize0"},
    "ArmorReflectiveColor": {0: "uImageSize0"},
}

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))


def main() -> None:
    probe = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp", "xnb_probe"))
    if not os.path.isfile(os.path.join(probe, "fx_parse.py")):
        sys.exit(f"probe tools not found under: {probe}")
    sys.path.insert(0, probe)
    os.chdir(probe)  # fx_parse loads in/PixelShader.xnb relative to cwd
    import fx_parse as fx  # type: ignore[import-not-found]

    base, p, npar, nt, no = fx.parse_header()
    _names, p = fx.parse_params(base, p, npar)
    techs, p = fx.parse_techniques(base, p, nt)
    objmap, _small, _end = fx.parse_objects(base, p, no)
    name2loc = {pn: (ti, j) for ti, (_tn, ps) in enumerate(techs)
                for j, (pn, _st) in enumerate(ps)}

    def get_blob(name: str) -> bytes:
        ti, j = name2loc[name]
        for (tech, pass_i, _S), (b, length) in objmap.items():
            if tech == ti and pass_i == j and (fx.u32(b) >> 16) == 0xFFFF:  # ps_2_0 object
                return fx.DATA[b:b + length]
        raise KeyError(name)

    out: dict[str, dict] = {}
    for name, inputs in PRES_INPUTS.items():
        blob = get_blob(name)
        out[name] = {
            "blob": base64.b64encode(blob).decode("ascii"),
            "pres_inputs": {str(k): v for k, v in inputs.items()},
        }
        print(f"baked {name:24} blob={len(blob)} bytes  inputs={inputs}")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "noise_shaders.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"wrote noise_shaders.json: {len(out)} passes -> {OUT}")


if __name__ == "__main__":
    main()
