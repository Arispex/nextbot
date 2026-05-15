from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "ban_list.html"

MAX_ENTRIES = 200

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


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
        if len(normalized) >= MAX_ENTRIES:
            break
        normalized.append(
            {
                "index": int(item.get("index", i + 1)),
                "name": str(item.get("name", "")).strip(),
                "user_id": str(item.get("user_id", "")).strip(),
                "ban_reason": str(item.get("ban_reason", "")).strip(),
                "banned_at": str(item.get("banned_at", "")).strip(),
            }
        )
    return {
        "generated_at": beijing_now_text(),
        "page": max(1, int(page)),
        "total_pages": max(1, int(total_pages)),
        "entries": normalized,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "entries": payload.get("entries", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__BAN_LIST_DATA_JSON__", data_json)
    return content.encode("utf-8")
