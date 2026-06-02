"""DEV-ONLY one-time asset extraction (not used at runtime).

Decodes the player/armor/hair textures from a local Terraria install
(LZX-XNB) into PNGs under ../assets/. Run once on a machine that owns
Terraria; the resulting PNGs are committed (asset strategy A).

    python3 extract_assets.py ["/path/to/Terraria.../Content/Images"]

Output naming (consumed by compositor.py):
    Player_{var}_{layer}.png        player body layers (var 0..11)
    Player_Hair_{n}.png             hair styles      (n = Player.hair + 1)
    Player_HairAlt_{n}.png          hat-hair styles
    Armor_Head_{slot}.png           head armor
    ArmorBody_{slot}.png            body armor  (from Content/Images/Armor/Armor_{slot}.xnb)
    Armor_Legs_{slot}.png           leg armor
"""
from __future__ import annotations

import os
import re
import sys

from xnb_to_png import decode_texture, unpremultiply, write_png

DEFAULT_CONTENT = (
    "/Users/arispex/Library/Application Support/Steam/steamapps/common/"
    "Terraria/Terraria.app/Contents/Resources/Content/Images"
)
OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets"))

# (source-dir-relative-glob regex, output-name-template) — output uses the captured groups
PATTERNS = [
    (re.compile(r"^Player_(\d+)_(\d+)\.xnb$"), "Player_{0}_{1}.png"),
    (re.compile(r"^Player_Hair_(\d+)\.xnb$"), "Player_Hair_{0}.png"),
    (re.compile(r"^Player_HairAlt_(\d+)\.xnb$"), "Player_HairAlt_{0}.png"),
    (re.compile(r"^Armor_Head_(\d+)\.xnb$"), "Armor_Head_{0}.png"),
    (re.compile(r"^Armor_Legs_(\d+)\.xnb$"), "Armor_Legs_{0}.png"),
]
# body armor lives in the Armor/ subdir as Armor_{slot}.xnb -> ArmorBody_{slot}.png
BODY_PATTERN = (re.compile(r"^Armor_(\d+)\.xnb$"), "ArmorBody_{0}.png")


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

    for fn in os.listdir(content):
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

    print(f"extracted {count} PNGs -> {OUT}")


if __name__ == "__main__":
    main()
