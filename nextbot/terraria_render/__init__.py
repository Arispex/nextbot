"""Terraria character avatar rendering (reusable, no NoneBot dependency).

Public API:
    render_character(appearance, equipment=None, vanity=None, dye=None, scale=1)
        -> PNG bytes

`appearance` / `equipment` / `vanity` / `dye` are the data blocks from
`GET /nextbot/users/{user}/appearance` (fields kept verbatim). Returns a
transparent-background PNG of the character's idle pose with equipment,
vanity (per-part override), and armor dyes applied. Accessories TBD.
"""
from .compositor import render_character

__all__ = ["render_character"]
