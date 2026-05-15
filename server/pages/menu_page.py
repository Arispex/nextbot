from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "menu.html"

MAX_ENTRIES = 200

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def build_payload(*, title: str, commands: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_commands: list[dict[str, Any]] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        if len(normalized_commands) >= MAX_ENTRIES:
            break

        raw_aliases = item.get("aliases") or []
        seen: set[str] = set()
        aliases: list[str] = []
        if isinstance(raw_aliases, list):
            for raw in raw_aliases:
                alias = str(raw).strip()
                if not alias or alias in seen:
                    continue
                seen.add(alias)
                aliases.append(alias)

        normalized_commands.append(
            {
                "display_name": str(item.get("display_name", "")).strip(),
                "description": str(item.get("description", "")).strip(),
                "usage": str(item.get("usage", "")).strip(),
                "permission": str(item.get("permission", "")).strip(),
                "aliases": aliases,
            }
        )

    return {
        "generated_at": beijing_now_text(),
        "title": str(title).strip(),
        "commands": normalized_commands,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "title": str(payload.get("title", "菜单")),
        "commands": payload.get("commands", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__MENU_DATA_JSON__", data_json)
    return content.encode("utf-8")
