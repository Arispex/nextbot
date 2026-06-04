"""DEV-ONLY one-time asset extraction (not used at runtime).

Decodes the player/armor/hair textures from a local Terraria install
(LZX-XNB) into PNGs under ../assets/. Run once on a machine that owns
Terraria; the resulting PNGs are committed (asset strategy A).

    python3 extract_assets.py ["/path/to/Terraria.../Content/Images"]

Output naming (consumed by compositor.py / dye.py):
    Player_{var}_{layer}.png        player body layers (var 0..11)
    Player_Hair_{n}.png             hair styles      (n = Player.hair + 1)
    Player_HairAlt_{n}.png          hat-hair styles
    Armor_Head_{slot}.png           head armor
    ArmorBody_{slot}.png            body armor  (from Content/Images/Armor/Armor_{slot}.xnb)
    Armor_Legs_{slot}.png           leg armor
    Wings_{slot}.png                wing accessory   (vertical N-frame strip, var WxH)
    Acc_Back_{slot}.png             back accessory   (capes/quivers, 40x1120)
    Acc_Front_{slot}.png            front accessory  (front-worn capes/scarves, 40x1120)
    Acc_Shoes_{slot}.png            shoe accessory   (40x1120)
    Acc_Waist_{slot}.png            waist accessory  (40x1120)
    Acc_Neck_{slot}.png             neck accessory   (40x1120)
    Acc_Face_{slot}.png             face accessory   (40x1120)
    Acc_Shield_{slot}.png           shield accessory (40x1120, some 44 wide)
    Acc_Beard_{slot}.png            beard accessory  (40x1120)
    Acc_Balloon_{slot}.png          balloon accessory (52x224 4-frame; #18=40x1120)
    Acc_HandsOn_{slot}.png          on-hand composite (from Accessories/, 360x224 9x4 grid)
    Acc_HandsOff_{slot}.png         off-hand composite (from Accessories/, 360x224 9x4 grid)
    noise.png                       Misc/noise.xnb 256x256 - noise-sampling dyes (dye.py)
    Extra_156.png                   Extra_156.xnb 512x512 - HallowBoss dye palette (dye.py)

Accessory texture geometry/draw rules are reverse-engineered in
research/accessories_spec.md (the runtime consumer is compositor.py). Per that spec
the root Acc_HandsOn/Off (40x1120) are DEAD code - the live draw uses the Accessories/
composite 360x224 ones, so those are the only hand textures extracted (from the subdir).
"""
from __future__ import annotations

import json
import os
import re
import sys

from xnb_to_png import decode_texture, unpremultiply, write_png

DEFAULT_CONTENT = (
    "/Users/arispex/Library/Application Support/Steam/steamapps/common/"
    "Terraria/Terraria.app/Contents/Resources/Content/Images"
)
OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets"))
DATA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))


def referenced_glow_masks() -> set[int]:
    """The head/legs glowmask ids actually consumed by compositor.py, read from
    data/glowmask.json (its `head`/`legs` sections map equip slot -> {mask, color}).
    Only these Glow_{id}.png are extracted; the ~348 other Glow_*.xnb are weapon/NPC/
    projectile glowmasks the player renderer never reads. (body/arm glow rides the
    ArmorBody composite lower half, not a Glow_ strip — see glowmask_spec.md §1.3/§6.)

    Also pulls each head entry's optional `fourtap` mask (head 211's hardcoded 4-tap
    shimmer of GlowMask_241, PlayerDrawLayers.cs:2403-2415 — it has no normal `mask`) and
    the top-level `aux_masks` list — auxiliary Glow_{id} the compositor draws BESIDE a
    slot's primary mask: 308 (head 269 FrontShoulder extra, PlayerDrawLayers.cs:114) and
    363 (ChickenBones coat front-238 glow, :1829)."""
    with open(os.path.join(DATA, "glowmask.json"), encoding="utf-8") as fh:
        table = json.load(fh)
    ids: set[int] = set()
    for section in ("head", "legs"):
        for entry in table.get(section, {}).values():
            if isinstance(entry, dict) and "mask" in entry:
                ids.add(int(entry["mask"]))
            if isinstance(entry, dict) and "fourtap" in entry:
                ids.add(int(entry["fourtap"]))
    ids.update(int(m) for m in table.get("aux_masks", []))
    return ids

# (source-dir-relative-glob regex, output-name-template) — output uses the captured groups
PATTERNS = [
    (re.compile(r"^Player_(\d+)_(\d+)\.xnb$"), "Player_{0}_{1}.png"),
    (re.compile(r"^Player_Hair_(\d+)\.xnb$"), "Player_Hair_{0}.png"),
    (re.compile(r"^Player_HairAlt_(\d+)\.xnb$"), "Player_HairAlt_{0}.png"),
    (re.compile(r"^Armor_Head_(\d+)\.xnb$"), "Armor_Head_{0}.png"),
    (re.compile(r"^Armor_Legs_(\d+)\.xnb$"), "Armor_Legs_{0}.png"),
    # accessory textures (root Content/Images): one strip/grid per visual slot id
    (re.compile(r"^Wings_(\d+)\.xnb$"), "Wings_{0}.png"),
    (re.compile(r"^Acc_Back_(\d+)\.xnb$"), "Acc_Back_{0}.png"),
    (re.compile(r"^Acc_Front_(\d+)\.xnb$"), "Acc_Front_{0}.png"),
    (re.compile(r"^Acc_Shoes_(\d+)\.xnb$"), "Acc_Shoes_{0}.png"),
    (re.compile(r"^Acc_Waist_(\d+)\.xnb$"), "Acc_Waist_{0}.png"),
    (re.compile(r"^Acc_Neck_(\d+)\.xnb$"), "Acc_Neck_{0}.png"),
    (re.compile(r"^Acc_Face_(\d+)\.xnb$"), "Acc_Face_{0}.png"),
    (re.compile(r"^Acc_Shield_(\d+)\.xnb$"), "Acc_Shield_{0}.png"),
    (re.compile(r"^Acc_Beard_(\d+)\.xnb$"), "Acc_Beard_{0}.png"),
    (re.compile(r"^Acc_Balloon_(\d+)\.xnb$"), "Acc_Balloon_{0}.png"),
]
# equipment glowmasks (root Content/Images/Glow_{id}.xnb, 40x1120 vertical strip): the
# independent head/legs glow layers (body/arm glow lives in the 360x448 ArmorBody lower
# half, not here). compositor.py only reads Glow_{headGlowMask}/Glow_{legsGlowMask} for
# the ~31 head/legs ids referenced in glowmask.json; the other ~348 Glow_*.xnb are weapon/
# NPC/projectile glowmasks and are NOT extracted. See research/glowmask_spec.md §1.3/§6.
GLOW_PATTERN = (re.compile(r"^Glow_(\d+)\.xnb$"), "Glow_{0}.png")
# body armor lives in the Armor/ subdir as Armor_{slot}.xnb -> ArmorBody_{slot}.png
BODY_PATTERN = (re.compile(r"^Armor_(\d+)\.xnb$"), "ArmorBody_{0}.png")
# composite hand accessories live in the Accessories/ subdir (the live 360x224 grid;
# the root Acc_HandsOn/Off 40x1120 strips are dead code per accessories_spec.md §X4).
ACC_HAND_DIR = "Accessories"
ACC_HAND_PATTERNS = [
    (re.compile(r"^Acc_HandsOn_(\d+)\.xnb$"), "Acc_HandsOn_{0}.png"),
    (re.compile(r"^Acc_HandsOff_(\d+)\.xnb$"), "Acc_HandsOff_{0}.png"),
]
# individually-named textures: (content-relative-src, output-name). noise -> all 8 noise
# dyes + ArmorTwilight hair dye; Extra_156 -> HallowBoss palette (noise_dyes_spec.md).
# Extra_212/213 are the two armor-set backpacks (5-frame vertical strips) drawn by
# DrawPlayer_08_Backpacks for the displayed sets (266,235,218)/(268,237,222) —
# research/backcoat_tails_spec.md §4. Extra_214 (40x1120 strip) is the white armor layer
# drawn alongside GlowMask_308 for head 269's FrontShoulder extra (PlayerDrawLayers.cs:111
# — the Extra_214 piece carries colorArmorHead, the GlowMask_308 the glow). All are fmt-0
# RGBA (alpha==255) so unpremultiply is a no-op.
SINGLE_TEXTURES = [
    (os.path.join("Misc", "noise.xnb"), "noise.png"),
    ("Extra_156.xnb", "Extra_156.png"),
    ("Extra_212.xnb", "Extra_212.png"),
    ("Extra_213.xnb", "Extra_213.png"),
    ("Extra_214.xnb", "Extra_214.png"),
]


def convert(src_path: str, out_name: str) -> bool:
    try:
        info = decode_texture(src_path)
    except Exception as exc:
        print(f"  SKIP {os.path.basename(src_path)}: {exc}")
        return False
    if info["surface_format"] != 0:  # only Color (RGBA32) sheets are player/armor art
        return False
    rgba = unpremultiply(info["pixels"])
    write_png(os.path.join(OUT, out_name), info["width"], info["height"], rgba)
    return True


def main() -> None:
    content = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONTENT
    if not os.path.isdir(content):
        sys.exit(f"Terraria Content/Images not found: {content}")
    os.makedirs(OUT, exist_ok=True)
    count = 0
    glow_ids = referenced_glow_masks()
    glow_rx, glow_tmpl = GLOW_PATTERN

    for fn in os.listdir(content):
        # Glow_*.xnb: only extract the head/legs ids referenced in glowmask.json (skip the
        # ~348 unrelated weapon/NPC/projectile glowmasks).
        gm = glow_rx.match(fn)
        if gm:
            if int(gm.group(1)) in glow_ids and convert(
                os.path.join(content, fn), glow_tmpl.format(*gm.groups())):
                count += 1
            continue
        for rx, tmpl in PATTERNS:
            m = rx.match(fn)
            if m:
                if convert(os.path.join(content, fn), tmpl.format(*m.groups())):
                    count += 1
                break

    armor_dir = os.path.join(content, "Armor")
    if os.path.isdir(armor_dir):
        rx, tmpl = BODY_PATTERN
        for fn in os.listdir(armor_dir):
            m = rx.match(fn)
            if m and convert(os.path.join(armor_dir, fn), tmpl.format(*m.groups())):
                count += 1

    hand_dir = os.path.join(content, ACC_HAND_DIR)
    if os.path.isdir(hand_dir):
        for fn in os.listdir(hand_dir):
            for rx, tmpl in ACC_HAND_PATTERNS:
                m = rx.match(fn)
                if m:
                    if convert(os.path.join(hand_dir, fn), tmpl.format(*m.groups())):
                        count += 1
                    break

    for rel_src, out_name in SINGLE_TEXTURES:
        src = os.path.join(content, rel_src)
        if os.path.isfile(src) and convert(src, out_name):
            count += 1
        elif not os.path.isfile(src):
            print(f"  SKIP {rel_src}: not found")

    print(f"extracted {count} PNGs -> {OUT}")


if __name__ == "__main__":
    main()
