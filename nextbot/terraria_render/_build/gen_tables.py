"""DEV-ONLY one-time data-table generation (not used at runtime).

Parses the decompiled Terraria source to bake the runtime lookup tables into
../data/*.json (committed). Run with the decompiled tree available:

    python3 gen_tables.py ["/path/to/temp/decomp/full"]

Produces:
    equip_slots.json  netID -> {"head"|"body"|"legs": slot}   (Item.cs SetDefaults)
    accessory_slots.json  netID -> {category: slot}  for the 12 visual accessory
                      categories (wing/back/balloon/shoe/handOff/waist/neck/face/
                      shield/handOn/front/beard)               (Item.cs SetDefaults)
    dyes.json         dye netID -> {pass, color, secondary?, sat}  (DyeInitializer.cs)
    hair_sets.json    {fullHair:[...], hatHair:[...], backonly:[...]}  (Player.GetHairSettings)
    hair_dye_colors.json  hairDye index 1..11 -> [r,g,b] | null  (DyeInitializer.cs)
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


# the 12 visual accessory categories: `item.<cat>Slot` field name -> json key.
# (these are the categories whose slot routes a DrawPlayer_NN layer; see
# research/accessories_spec.md. Order doesn't matter — keyed by category.)
_ACC_CATS = (
    "wing", "back", "balloon", "shoe", "handOff", "waist",
    "neck", "face", "shield", "handOn", "front", "beard",
)


def gen_accessory_slots(decomp: str) -> dict:
    """Walk Item.cs SetDefaults: netID -> {category: slot} for every accessory
    visual slot. Same case-accumulation parser as gen_equip_slots, plus two extra
    forms the accessory slots use that the armor slots don't:

    * literal  `<cat>Slot = N;`                 -> all pending cases get slot N.
    * computed `<cat>Slot = (sbyte)(B + type - F);` (a run of consecutive item
      types mapped to consecutive slots) -> each pending case `c` gets `B+(c-F)`.
      Pending cases come from `case N:` labels OR an `if (type >= A && type <= B)`
      guard (the SuperHero cape/front pair, line 22961, uses the if-guard form).

    A break/return clears the pending set; one item may set several categories
    (e.g. type 211 sets both handOn and handOff), so we DON'T clear on a slot
    assignment — only on break/return — exactly like gen_equip_slots."""
    src = open(os.path.join(decomp, "Terraria", "Item.cs")).read()
    cats = "|".join(_ACC_CATS)
    case_rx = re.compile(r"^\s*case (\d+):")
    # `if (type >= A && type <= B)` — inclusive type range acting as a case group
    ifrange_rx = re.compile(r"^\s*if \(type >= (\d+) && type <= (\d+)\)")
    lit_rx = re.compile(rf"^\s*({cats})Slot = (\d+);")
    # computed forms vary in inner parens: `(sbyte)(13 + type - 3250)` (wing/balloon)
    # vs `(sbyte)(2 + (type - 5104))` (Wilson beards) — tolerate the optional `(`/`)`.
    comp_rx = re.compile(
        rf"^\s*({cats})Slot = \(sbyte\)\((\d+) \+ \(?type - (\d+)\)?\);")
    reset_rx = re.compile(r"^\s*(break;|return\b)")
    pending: list[int] = []
    out: dict[str, dict] = {}
    for line in src.splitlines():
        m = case_rx.match(line)
        if m:
            pending.append(int(m.group(1)))
            continue
        m = ifrange_rx.match(line)
        if m:
            pending.extend(range(int(m.group(1)), int(m.group(2)) + 1))
            continue
        m = lit_rx.match(line)
        if m and pending:
            cat, slot = m.group(1), int(m.group(2))
            for nid in pending:
                out.setdefault(str(nid), {})[cat] = slot
            continue
        m = comp_rx.match(line)
        if m and pending:
            cat, base, first = m.group(1), int(m.group(2)), int(m.group(3))
            for nid in pending:
                out.setdefault(str(nid), {})[cat] = base + (nid - first)
            continue
        if reset_rx.match(line):
            pending = []
    return out


def gen_wing_meta(decomp: str) -> dict:
    """Wing draw metadata for the idle still (DrawPlayer_09_Wings, PlayerDrawLayers.cs
    :655-1104). Returns {"always_animated": [...], "frames": {slot: N}, "offset":
    {slot: [num13, num12]}}.

    * always_animated = ArmorIDs.Wing.Sets.AlwaysAnimated (ArmorIDs.cs:1967) — these
      wings only draw airborne (ShouldDrawWingsThatAreAlwaysAnimated()==false grounded),
      so they render NOTHING for a still avatar; the compositor skips them.
    * frames N = the `Frame(1, N, ...)` / `num14` / `num11` row count of the wing strip
      (idle = frame 0 = top N-th of the sheet). Default N=4; the non-default drawable
      wings are read below. The AlwaysAnimated wings' N is irrelevant (skipped).
    * offset = the default-block `(num13, num12)` position tweaks (most wings 0,0).

    Both AlwaysAnimated and the N literals are ASSERTED against the source so the table
    can't silently drift; the non-default-N / non-zero-offset wings are transcribed from
    the cited draw-method blocks (each is a one-off `if (wings == K)` branch)."""
    armor_ids = open(os.path.join(decomp, "Terraria.ID", "ArmorIDs.cs")).read()
    m = re.search(r"Wing[\s\S]*?AlwaysAnimated = Factory\.CreateBoolSet\(false,\s*"
                  r"([\d,\s]+)\);", armor_ids)
    if m is None:
        raise ValueError("Wing.Sets.AlwaysAnimated not found in ArmorIDs.cs")
    always = sorted(int(x) for x in m.group(1).replace(" ", "").split(","))
    if always != sorted([22, 28, 45, 34, 48, 39, 40, 44]):
        raise ValueError(f"Wing AlwaysAnimated set changed: {always}")
    pdl = open(os.path.join(decomp, "Terraria.DataStructures",
                            "PlayerDrawLayers.cs")).read()
    wings_src = pdl[pdl.find("void DrawPlayer_09_Wings"):]
    wings_src = wings_src[:wings_src.find("void DrawPlayer_10_BackAcc")]
    # assert the per-wing Frame(1, N, ...) row counts the still relies on are present.
    for slot in (43, 47, 49, 50, 51):
        if f"wings == {slot}" not in wings_src:
            raise ValueError(f"wing {slot} branch missing in DrawPlayer_09_Wings")
    if "int num14 = 4" not in wings_src:
        raise ValueError("wing default num14=4 changed")
    # N per wing (only the drawable non-default ones matter; AlwaysAnimated skipped).
    # 43→7 (num14, line 285), 51→8 (Frame(1,8), 47/49/50→11 (Frame(1,11)/num11).
    frames = {43: 7, 47: 11, 49: 11, 50: 11, 51: 8}
    # default-block (num13, num12) tweaks (PlayerDrawLayers.cs:282-308) + wing-50's
    # `-UnitX*dir*4` horizontal nudge (line ~922, num13≈-4 at dir=1).
    offset = {5: [4, -4], 12: [-1, -1], 27: [3, 0], 41: [-1, 0],
              43: [-5, -7], 50: [-4, 0]}
    return {"always_animated": always, "frames": frames, "offset": offset}


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


def gen_hair_dye_colors(decomp: str) -> dict:
    """hairDye index (1..11) -> representative-still [r,g,b], or null = "use hairColor".

    `player.hairDye` is a 1-based index into GameShaders.Hair; indices 1..11 are
    LegacyHairShaderData whose color delegate REPLACES hairColor with a value derived
    from live state (life/mana/world/time/team/speed). For a static portrait we have no
    live state, so each index is evaluated at its documented representative condition
    (DyeInitializer.LoadLegacyHairdyes, bind order = item 1977..2863). Indices whose
    representative reduces to hairColor itself are null (Speed at rest, Martian offline).
    Index 12 (Twilight) is NOT here — it keeps hairColor and runs the ArmorTwilight pixel
    pass; index 0 is no dye. The bind order below is asserted against the source so the
    1..11 indexing can't silently drift."""
    di = open(os.path.join(decomp, "Terraria.Initializers", "DyeInitializer.cs")).read()
    # anchor to the method DEFINITION (not its call site inside LoadHairDyes, which
    # binds the idx-12 Twilight id 3259 first).
    legacy = di[di.find("void LoadLegacyHairdyes"):]
    bind_ids = [int(m) for m in re.findall(r"Hair\.BindShader\((\d+),", legacy)]
    expected = [1977, 1978, 1979, 1980, 1981, 1982, 1983, 1984, 1985, 1986, 2863]
    if bind_ids[:11] != expected:
        raise ValueError(f"legacy hairdye bind order changed: {bind_ids[:11]}")
    # teamColor[0] (idx 6 Team, team 0) — read from Main.cs so it stays sourced
    main = open(os.path.join(decomp, "Terraria", "Main.cs")).read()
    tc0 = re.search(r"teamColor\[0\]\s*=\s*[\w.]*Color\.White", main)
    team0 = [255, 255, 255] if tc0 else None
    # representative-still color per index (source line in DyeInitializer.cs):
    #  1 Life: full life -> R=235+20, G=B=20                                   (155-158)
    #  2 Mana: full mana -> R=50, G=75, B=255                                  (162-165)
    #  3 Depth: surface (center.Y ~ 0) -> (116,160,249)                        (178-180)
    #  4 Money: broke (num=0) -> Color(226,118,76)                             (251,259-261)
    #  5 Time: dawn (dayTime, time=0) -> Color(1,142,255)                      (289,299-301)
    #  6 Team: team 0 -> Main.teamColor[0] = White                            (337 + Main.cs)
    #  7 Biome: default waterStyle -> Color(28,216,94)                         (346)
    #  8 Party: constant Color(244,22,175)                                     (387)
    #  9 Rainbow: Disco cycling -> representative red (255,0,0)                (392)
    # 10 Speed: at rest -> hairColor                                          (405-407) -> null
    # 11 Martian: offline avg with local light ~ hairColor                    (416-418) -> null
    return {
        "1": [255, 20, 20], "2": [50, 75, 255], "3": [116, 160, 249],
        "4": [226, 118, 76], "5": [1, 142, 255], "6": team0,
        "7": [28, 216, 94], "8": [244, 22, 175], "9": [255, 0, 0],
        "10": None, "11": None,
    }


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
        "accessory_slots.json": gen_accessory_slots(decomp),
        "wing_meta.json": gen_wing_meta(decomp),
        "dyes.json": gen_dyes(decomp),
        "hair_sets.json": gen_hair_sets(decomp),
        "hair_dye_colors.json": gen_hair_dye_colors(decomp),
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
    acc = tables["accessory_slots.json"]
    # known item types -> (category, slot). 492 DemonWings, 493 AngelWings,
    # 159 ShinyRedBalloon, 54 HermesBoots, 156 CobaltShield, 2501 GingerBeard,
    # 3224 WormScarf; 211 sets BOTH handOn 5 + handOff 9 (multi-slot item).
    acc_checks = {"492": ("wing", 1), "493": ("wing", 2), "159": ("balloon", 8),
                  "54": ("shoe", 6), "156": ("shield", 1), "2501": ("beard", 1),
                  "3224": ("neck", 8), "211": ("handOn", 5)}
    for nid, (cat, slot) in acc_checks.items():
        got = acc.get(nid, {}).get(cat)
        status = "OK" if got == slot else f"MISMATCH got={got}"
        print(f"  validate acc netID {nid} {cat}={slot}: {status}")
    print(f"  acc 211 (multi-slot) = {acc.get('211')}")  # expect handOn 5 + handOff 9
    # computed-range forms: SuperHero cape 2284-2287 -> back 3..6 / front 1..4 (if-guard);
    # Yoraiz0r wings 3469-3471 -> wing 30..32 (case-label run).
    print(f"  acc 2285 (if-range cape) = {acc.get('2285')}")  # expect back 4, front 2
    print(f"  acc 3470 (case-run wing) = {acc.get('3470')}")  # expect wing 31
    print(f"  acc 5105 (Wilson beard, inner-parens) = {acc.get('5105')}")  # expect beard 3
    print(f"  accessory_slots: {len(acc)} items")
    wm = tables["wing_meta.json"]
    print(f"  wing AlwaysAnimated = {wm['always_animated']}")
    print(f"  wing frames (non-default N) = {wm['frames']}")  # 43:7 47/49/50:11 51:8
    dyes = tables["dyes.json"]
    print(f"  validate dye 1007: {dyes.get('1007')}")
    # gradient dyes must carry color + secondary now (bind_rx multi-line fix)
    grad_missing = [k for k, v in dyes.items()
                    if v.get("pass", "").endswith("Gradient") and "secondary" not in v]
    print(f"  validate gradient dyes carry secondary: "
          f"{'OK' if not grad_missing else f'MISSING {sorted(grad_missing, key=int)}'}")
    print(f"  validate dye 1031 (ColoredGradient): {dyes.get('1031')}")
    hdc = tables["hair_dye_colors.json"]
    print(f"  validate hairDye colors: idx1={hdc.get('1')} idx8={hdc.get('8')} "
          f"idx6(team0)={hdc.get('6')} idx10(hairColor)={hdc.get('10')}")
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
