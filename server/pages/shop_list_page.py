from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "shop_list.html"

MAX_ENTRIES = 200

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def _normalize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        if len(out) >= MAX_ENTRIES:
            break
        try:
            shop_id = int(raw.get("shop_id", 0))
            item_count = max(0, int(raw.get("item_count", 0)))
        except (TypeError, ValueError):
            continue
        out.append({
            "shop_id": shop_id,
            "name": str(raw.get("name", "")).strip() or "未命名商店",
            "description": str(raw.get("description", "")).strip(),
            "item_count": item_count,
        })
    return out


def build_payload(
    *,
    entries: list[dict[str, Any]],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> dict[str, Any]:
    normalized = _normalize_entries(entries)
    return {
        "generated_at": beijing_now_text(),
        "entries": normalized,
        "page": max(1, int(page)),
        "total_pages": max(1, int(total_pages)),
        "total": max(0, int(total)),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "entries": payload.get("entries", []),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "total": int(payload.get("total", 0)),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__SHOP_LIST_DATA_JSON__", data_json)
    return content.encode("utf-8")
