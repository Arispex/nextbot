"""DEV-ONLY one-time data-table generation (not used at runtime).

Parses the decompiled Terraria source to bake the runtime lookup tables into
../data/*.json (committed). Run with the decompiled tree available:

    python3 gen_tables.py ["/path/to/temp/decomp/full"]

Produces:
    equip_slots.json  netID -> {"head"|"body"|"legs": slot}   (Item.cs SetDefaults)
    dyes.json         dye netID -> {pass, color, secondary?, sat}  (DyeInitializer.cs)
    hair_sets.json    {fullHair:[...], hatHair:[...], backonly:[...]}  (Player.GetHairSettings)
    variants.json     male variants, fallback chains, idle composite cells
"""
from __future__ import annotations

import json
import os
import re
import sys

DEFAULT_DECOMP = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "temp", "decomp", "full")
)
OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))


def gen_equip_slots(decomp: str) -> dict:
    """Walk SetDefaults' switch: accumulate `case N:` labels, assign on
    head/body/legSlot=, clear on break/return. Captures fall-through groups."""
    src = open(os.path.join(decomp, "Terraria", "Item.cs")).read()
    case_rx = re.compile(r"^\s*case (\d+):")
    slot_rx = re.compile(r"^\s*(head|body|leg)Slot = (\d+);")
    reset_rx = re.compile(r"^\s*(break;|return\b)")
    pending: list[int] = []
    out: dict[str, dict] = {}
    for line in src.splitlines():
        m = case_rx.match(line)
        if m:
            pending.append(int(m.group(1)))
            continue
        m = slot_rx.match(line)
        if m and pending:
            key = {"head": "head", "body": "body", "leg": "legs"}[m.group(1)]
            slot = int(m.group(2))
            for nid in pending:
                out.setdefault(str(nid), {})[key] = slot
            continue
        if reset_rx.match(line):
            pending = []
    return out


def gen_dyes(decomp: str) -> dict:
    src = open(os.path.join(decomp, "Terraria.Initializers", "DyeInitializer.cs")).read()
    out: dict[str, dict] = {}
    # basic color dyes: LoadBasicColorDye(base, r, g, b [, sat]) -> 4 variants
    basic_rx = re.compile(
        r"LoadBasicColorDye\((\d+),\s*(-?[\d.]+)f,\s*(-?[\d.]+)f,\s*(-?[\d.]+)f"
        r"(?:,\s*([\d.]+)f)?")
    for m in basic_rx.finditer(src):
        base = int(m.group(1))
        r, g, b = float(m.group(2)), float(m.group(3)), float(m.group(4))
        sat = float(m.group(5)) if m.group(5) else 1.0
        out[str(base)] = {"pass": "ArmorColored", "color": [r, g, b], "sat": sat}
        out[str(base + 12)] = {"pass": "ArmorColoredAndBlack", "color": [r, g, b], "sat": sat}
        out[str(base + 31)] = {"pass": "ArmorColored",
                               "color": [r * .5 + .5, g * .5 + .5, b * .5 + .5], "sat": sat}
        out[str(base + 44)] = {"pass": "ArmorColoredAndSilverTrim", "color": [r, g, b], "sat": sat}
    # explicit BindShader(itemId, new <X>ArmorShaderData(ref, "Pass"))<chain>;
    # statements span several lines (fluent .UseColor/.UseSecondaryColor/.UseSaturation,
    # interleaved with .UseImage); parse each full statement to its ';' then pull the
    # uniform calls out regardless of order/whitespace.
    bind_rx = re.compile(
        r"GameShaders\.Armor\.BindShader\((\d+),\s*new\s+\w*ArmorShaderData"
        r"\([^,]+,\s*\"(\w+)\"\)(.*?);",
        re.DOTALL)
    vec3_rx = re.compile(r"\(\s*(-?[\d.]+)f,\s*(-?[\d.]+)f,\s*(-?[\d.]+)f\s*\)")
    use_color_rx = re.compile(r"\.UseColor" + vec3_rx.pattern)
    use_secondary_rx = re.compile(r"\.UseSecondaryColor" + vec3_rx.pattern)
    use_sat_rx = re.compile(r"\.UseSaturation\(\s*(-?[\d.]+)f\s*\)")

    def vec3(m):
        return [float(m.group(1)), float(m.group(2)), float(m.group(3))]

    for m in bind_rx.finditer(src):
        nid = int(m.group(1))
        chain = m.group(3)
        entry: dict = {"pass": m.group(2)}
        cm = use_color_rx.search(chain)
        if cm:
            entry["color"] = vec3(cm)
        sm = use_secondary_rx.search(chain)
        if sm:
            entry["secondary"] = vec3(sm)
        satm = use_sat_rx.search(chain)
        if satm:
            entry["sat"] = float(satm.group(1))
        # hair dyes (GameShaders.Hair) and Misc are not armor dyes; armor item ids
        # are >=1007. don't override basic-dye entries already loaded above.
        out.setdefault(str(nid), entry)
    return out


def gen_robe_extensions(decomp: str) -> dict:
    """Parse GetMatchingBodyExtension's switch (PlayerDrawLayers.cs): bodySlot ->
    leg-armor extension slot. Gender-conditional cases (`(!Male) ? F : M`) become
    {"male": M, "female": F}; plain cases become an int. Robe/long-coat skirts."""
    path = os.path.join(decomp, "Terraria.DataStructures", "PlayerDrawLayers.cs")
    src = open(path).read()
    m = re.search(
        r"public static int GetMatchingBodyExtension\([^)]*\)\s*\{.*?\n\t\}",
        src, re.DOTALL)
    if m is None:
        raise ValueError("GetMatchingBodyExtension not found in PlayerDrawLayers.cs")
    body = m.group(0)
    out: dict[str, object] = {}
    case_rx = re.compile(r"^\s*case (\d+):")
    plain_rx = re.compile(r"^\s*result = (\d+);")
    # result = ((!drawinfo.drawPlayer.Male) ? 172 : 171);  -> female 172, male 171
    cond_rx = re.compile(r"^\s*result = \(\(!.*?\.Male\) \? (\d+) : (\d+)\);")
    pending: list[int] = []
    for line in body.splitlines():
        cm = case_rx.match(line)
        if cm:
            pending.append(int(cm.group(1)))
            continue
        condm = cond_rx.match(line)
        if condm and pending:
            female, male = int(condm.group(1)), int(condm.group(2))
            for nid in pending:
                out[str(nid)] = {"male": male, "female": female}
            pending = []
            continue
        pm = plain_rx.match(line)
        if pm and pending:
            slot = int(pm.group(1))
            for nid in pending:
                out[str(nid)] = slot
            pending = []
    return out


def gen_hair_sets(decomp: str) -> dict:
    src = open(os.path.join(decomp, "Terraria", "Player.cs")).read()
    m = re.search(r"public void GetHairSettings\(.*?\n(\t\t)\}", src, re.DOTALL)
    if m is None:
        raise ValueError("GetHairSettings not found in Player.cs")
    body = m.group(0)
    groups = {"backonly": [], "fullHair": [], "hatHair": []}
    flag_to_key = {"drawsBackHairWithoutHeadgear = true": "backonly",
                   "fullHair = true": "fullHair", "hatHair = true": "hatHair"}
    cur: list[int] = []
    for line in body.splitlines():
        cm = re.match(r"\s*case (\d+):", line)
        if cm:
            cur.append(int(cm.group(1)))
            continue
        for flag, key in flag_to_key.items():
            if flag in line:
                groups[key].extend(cur)
                cur = []
    return {k: sorted(v) for k, v in groups.items()}


def gen_variants() -> dict:
    # constants from terraria_render_spec.md (PlayerVariantID / PlayerDataInitializer)
    return {
        "male_variants": [0, 1, 2, 3, 8, 10],
        "fallback": {  # variant -> resolution chain for a missing layer
            "0": [0], "1": [1, 0], "2": [2, 0], "3": [3, 0], "8": [8, 0],
            "4": [4, 0], "5": [5, 4, 0], "6": [6, 4, 0], "9": [9, 4, 0], "7": [7, 4, 0],
            "10": [10, 0], "11": [11, 10, 0],
        },
        "idle_cells": {  # composite grid cells (male / female via +2-row shift)
            "torso": {"male": 0, "female": 18},
            "front_arm": 2, "back_arm": 20,
            "front_shoulder": {"male": 9, "female": 27},
            "back_shoulder": {"male": 10, "female": 28},
        },
        "frame": {"w": 40, "h": 56},
    }


def main() -> None:
    decomp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DECOMP
    if not os.path.isdir(decomp):
        sys.exit(f"decompiled source tree not found: {decomp}")
    os.makedirs(OUT, exist_ok=True)
    tables = {
        "equip_slots.json": gen_equip_slots(decomp),
        "dyes.json": gen_dyes(decomp),
        "hair_sets.json": gen_hair_sets(decomp),
        "robe_extensions.json": gen_robe_extensions(decomp),
        "variants.json": gen_variants(),
    }
    for name, data in tables.items():
        with open(os.path.join(OUT, name), "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=0, sort_keys=True)
        n = len(data) if isinstance(data, dict) else "-"
        print(f"wrote {name}: {n} entries")

    # validate known items
    eq = tables["equip_slots.json"]
    checks = {"690": ("head", 48), "80": ("body", 1), "1733": ("legs", 64),
              "1212": ("head", 88), "229": ("body", 8), "153": ("legs", 7)}
    for nid, (slot_kind, slot) in checks.items():
        got = eq.get(nid, {}).get(slot_kind)
        status = "OK" if got == slot else f"MISMATCH got={got}"
        print(f"  validate netID {nid} {slot_kind}={slot}: {status}")
    dyes = tables["dyes.json"]
    print(f"  validate dye 1007: {dyes.get('1007')}")
    # gradient dyes must carry color + secondary now (bind_rx multi-line fix)
    grad_missing = [k for k, v in dyes.items()
                    if v.get("pass", "").endswith("Gradient") and "secondary" not in v]
    print(f"  validate gradient dyes carry secondary: "
          f"{'OK' if not grad_missing else f'MISSING {sorted(grad_missing, key=int)}'}")
    print(f"  validate dye 1031 (ColoredGradient): {dyes.get('1031')}")
    ext = tables["robe_extensions.json"]
    ext_checks = {"200": 149, "52": {"male": 171, "female": 172},
                  "222": {"male": 201, "female": 200}, "251": 238}
    for body_slot, want in ext_checks.items():
        got = ext.get(body_slot)
        status = "OK" if got == want else f"MISMATCH got={got}"
        print(f"  validate robe ext body {body_slot} -> {want}: {status}")
    print(f"  robe_extensions: {len(ext)} body slots")


if __name__ == "__main__":
    main()
