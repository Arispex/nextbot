from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "red_packet_all.html"


def build_payload(
    *,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "index": int(item.get("index", i + 1)),
                "name": str(item.get("name", "")).strip(),
                "sender_name": str(item.get("sender_name", "")).strip(),
                "sender_user_id": str(item.get("sender_user_id", "")).strip(),
                "type_zh": str(item.get("type_zh", "")).strip(),
                "remaining_amount": int(item.get("remaining_amount", 0)),
                "total_amount": int(item.get("total_amount", 0)),
                "remaining_count": int(item.get("remaining_count", 0)),
                "total_count": int(item.get("total_count", 0)),
            }
        )
    return {
        "generated_at": beijing_now_text(),
        "page": int(page),
        "total_pages": int(total_pages),
        "entries": normalized,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "entries": payload.get("entries", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__RED_PACKET_ALL_DATA_JSON__", data_json)
    return content.encode("utf-8")
