from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "menu.html"


def build_payload(*, title: str, commands: list[dict[str, str]], theme: str = "light") -> dict[str, Any]:
    normalized_commands: list[dict[str, str]] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        normalized_commands.append(
            {
                "display_name": str(item.get("display_name", "")).strip(),
                "description": str(item.get("description", "")).strip(),
                "usage": str(item.get("usage", "")).strip(),
                "permission": str(item.get("permission", "")).strip(),
            }
        )

    return {
        "generated_at": beijing_now_text(),
        "title": str(title).strip(),
        "commands": normalized_commands,
        "theme": str(theme).strip() if str(theme).strip() in {"dark", "light"} else "light",
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "title": str(payload.get("title", "菜单")),
        "commands": payload.get("commands", []),
        "theme": str(payload.get("theme", "light")),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__MENU_DATA_JSON__", data_json)
    return content.encode("utf-8")
