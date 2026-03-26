from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "leaderboard.html"


def build_payload(
    *,
    title: str,
    value_label: str,
    page: int,
    total_pages: int,
    entries: list[dict[str, Any]],
    self_entry: dict[str, Any] | None = None,
    theme: str = "dark",
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        raw_value = item.get("value", 0)
        normalized.append(
            {
                "rank": int(item.get("rank", 0)),
                "name": str(item.get("name", "")).strip(),
                "user_id": str(item.get("user_id", "")).strip(),
                "value": str(raw_value) if isinstance(raw_value, str) else int(raw_value),
            }
        )
    normalized_self: dict[str, Any] | None = None
    if isinstance(self_entry, dict):
        raw_self_value = self_entry.get("value", 0)
        normalized_self = {
            "rank": int(self_entry.get("rank", 0)),
            "name": str(self_entry.get("name", "")).strip(),
            "value": str(raw_self_value) if isinstance(raw_self_value, str) else int(raw_self_value),
        }

    return {
        "generated_at": beijing_now_text(),
        "title": str(title).strip(),
        "value_label": str(value_label).strip(),
        "page": int(page),
        "total_pages": int(total_pages),
        "entries": normalized,
        "self_entry": normalized_self,
        "theme": str(theme).strip() if str(theme).strip() in {"dark", "light"} else "dark",
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "title": str(payload.get("title", "排行榜")),
        "value_label": str(payload.get("value_label", "")),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "entries": payload.get("entries", []),
        "self_entry": payload.get("self_entry"),
        "theme": str(payload.get("theme", "dark")),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__LEADERBOARD_DATA_JSON__", data_json)
    return content.encode("utf-8")
