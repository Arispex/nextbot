"""Terraria character avatar rendering (reusable, no NoneBot dependency).

Public API:
    render_character(appearance, equipment=None, vanity=None, dye=None, scale=1, *,
                     accessories=None, vanity_accessories=None, accessory_dyes=None)
        -> PNG bytes

`appearance` / `equipment` / `vanity` / `dye` and the three accessory lists are the
data blocks from `GET /nextbot/users/{user}/appearance` (fields kept verbatim). Returns
a transparent-background PNG of the character's idle pose with equipment, vanity
(per-part override), armor dyes, and the 12 accessory categories (wings, capes,
balloons, shoes, waist/neck/face, shield, hand composites, beard) applied — with the
vanity-override + hideVisuals resolution and per-slot accessory dyes.
"""
from .compositor import render_character

__all__ = ["render_character"]
