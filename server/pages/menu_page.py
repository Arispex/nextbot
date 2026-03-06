from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "menu.html"


def build_payload(*, commands: list[dict[str, str]]) -> dict[str, Any]:
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
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "commands": normalized_commands,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "commands": payload.get("commands", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__MENU_DATA_JSON__", data_json)
    return content.encode("utf-8")
