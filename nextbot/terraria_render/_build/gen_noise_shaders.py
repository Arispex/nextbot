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

# Verified preshader input-register -> effect-parameter map per pass
# (research/noise_dyes_spec.md §3 "Input-register -> parameter map", cross-checked
# against the validated temp/xnb_probe/ps_interp_full.py harness for the first three).
PRES_INPUTS = {
    "ArmorStardust": {0: "uSourceRect", 1: "uImageSize1"},
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
}

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))


def main() -> None:
    probe = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp", "xnb_probe"))
    if not os.path.isfile(os.path.join(probe, "fx_parse.py")):
        sys.exit(f"probe tools not found under: {probe}")
    sys.path.insert(0, probe)
    os.chdir(probe)  # fx_parse loads in/PixelShader.xnb relative to cwd
    import fx_parse as fx  # type: ignore[import-not-found]  # noqa: PLC0415 - dev tool via sys.path

    base, p, npar, nt, no = fx.parse_header()
    _names, p = fx.parse_params(base, p, npar)
    techs, p = fx.parse_techniques(base, p, nt)
    objmap, _small, _end = fx.parse_objects(base, p, no)
    name2loc = {pn: (ti, j) for ti, (_tn, ps) in enumerate(techs)
                for j, (pn, _st) in enumerate(ps)}

    def get_blob(name: str) -> bytes:
        ti, j = name2loc[name]
        for (T, I, _S), (b, length) in objmap.items():
            if T == ti and I == j and (fx.u32(b) >> 16) == 0xFFFF:  # ps_2_0 object
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
