"""Minimal tests for the reusable Terraria character renderer.

Dependency-light: no network, no pytest-only fixtures. Runs under pytest
(``uv run pytest tests/test_terraria_render.py``) or as a plain script
(``uv run python tests/test_terraria_render.py``).
"""
from __future__ import annotations

import sys
from pathlib import Path

# allow `python tests/test_terraria_render.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nextbot.terraria_render import render_character

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

# Known-good input from the task verification snippet (FemaleCoat + armor + RedDye).
_APPEARANCE = {
    "skinVariant": 7,
    "hair": 112,
    "hairDye": 0,
    "hairColor": -3270602,
    "skinColor": -10059269,
    "eyeColor": -15100654,
    "shirtColor": -4021652,
    "underShirtColor": -4639811,
    "pantsColor": -12772014,
    "shoeColor": -4963208,
}
_EQUIPMENT = {"head": {"netId": 690}, "body": {"netId": 80}, "legs": {"netId": 1733}}
_DYE = {"head": {"netId": 1007}, "body": {"netId": 1007}, "legs": {"netId": 1007}}


def test_render_returns_valid_png() -> None:
    png = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    assert png[:8] == _PNG_SIG
    assert len(png) > 1000


def test_render_is_deterministic() -> None:
    a = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    b = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    assert a == b


def test_render_appearance_only() -> None:
    # No equipment/vanity/dye: naked base body + hair must still produce a PNG.
    png = render_character(_APPEARANCE)
    assert png[:8] == _PNG_SIG
    assert len(png) > 100


def test_scale_changes_dimensions() -> None:
    small = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=1)
    big = render_character(_APPEARANCE, _EQUIPMENT, None, _DYE, scale=8)
    # Both are valid PNGs; upscaled output is strictly larger on disk.
    assert small[:8] == _PNG_SIG
    assert big[:8] == _PNG_SIG
    assert len(big) > len(small)


def _run() -> int:
    tests = [
        test_render_returns_valid_png,
        test_render_is_deterministic,
        test_render_appearance_only,
        test_scale_changes_dimensions,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
