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
        r"LoadBasicColorDye\((\d+),\s*([\d.]+)f,\s*([\d.]+)f,\s*([\d.]+)f"
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
    # explicit BindShader(itemId, new (Reflective|Team|)ArmorShaderData(ref, "Pass"))[.UseColor(..)][.UseSecondaryColor(..)][.UseSaturation(..)]
    bind_rx = re.compile(
        r"BindShader\((\d+),\s*new\s+\w*ArmorShaderData\([^,]+,\s*\"(\w+)\"\)"
        r"(?:\.UseColor\(([\d.fs, ]+)\))?"
        r"(?:\.UseSecondaryColor\(([\d.fs, ]+)\))?"
        r"(?:\.UseSaturation\(([\d.]+)f\))?")
    def nums(s):
        return [float(x) for x in re.findall(r"[\d.]+", s)] if s else None
    for m in bind_rx.finditer(src):
        nid = int(m.group(1))
        entry = {"pass": m.group(2)}
        col = nums(m.group(3))
        sec = nums(m.group(4))
        if col:
            entry["color"] = col
        if sec:
            entry["secondary"] = sec
        if m.group(5):
            entry["sat"] = float(m.group(5))
        out.setdefault(str(nid), entry)  # don't override basic-dye entries
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


if __name__ == "__main__":
    main()
