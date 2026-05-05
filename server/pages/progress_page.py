from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "progress.html"


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"1", "true", "yes", "on"}
    return False


def _normalize_progress(
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for key, value in progress.items():
        name = str(key).strip()
        if not name:
            continue
        normalized.append(
            {
                "name": name,
                "defeated": _to_bool(value),
            }
        )
    return normalized


def build_payload(
    *,
    server_id: int,
    server_name: str,
    progress: dict[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_progress(progress)
    defeated_count = sum(1 for item in normalized if item["defeated"])
    return {
        "generated_at": beijing_now_text(),
        "server_id": str(server_id),
        "server_name": str(server_name),
        "progress": normalized,
        "total_count": len(normalized),
        "defeated_count": defeated_count,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "server_id": payload.get("server_id", ""),
        "server_name": payload.get("server_name", ""),
        "generated_at": payload.get("generated_at", ""),
        "progress": payload.get("progress", []),
        "total_count": payload.get("total_count", 0),
        "defeated_count": payload.get("defeated_count", 0),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__PROGRESS_DATA_JSON__", data_json)
    return content.encode("utf-8")
