from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "about.html"

THANKS_LIST: list[dict[str, str]] = [
    {"qq": "197761067", "name": "Smelody"},
    {"qq": "3191959156", "name": "告白气球"},
]


def build_payload() -> dict[str, Any]:
    return {
        "generated_at": beijing_now_text(),
        "project_name": "NextBot",
        "project_desc": "基于 NoneBot2 的 Terraria TShock QQ 机器人",
        "project_url": "https://github.com/Arispex/nextbot",
        "author": "Arispex",
        "author_url": "https://github.com/Arispex",
        "thanks": [
            {"qq": str(t["qq"]), "name": str(t["name"])}
            for t in THANKS_LIST
        ],
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "project_name": str(payload.get("project_name", "")),
        "project_desc": str(payload.get("project_desc", "")),
        "project_url": str(payload.get("project_url", "")),
        "author": str(payload.get("author", "")),
        "author_url": str(payload.get("author_url", "")),
        "thanks": payload.get("thanks", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__ABOUT_DATA_JSON__", data_json)
    return content.encode("utf-8")
