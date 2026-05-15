from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "user_info.html"

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def build_payload(
    *,
    user_id: str,
    user_name: str,
    coins: int,
    sign_streak: int,
    sign_total: int,
    permissions: str,
    group: str,
    created_at: str,
    sign_dates: list[str],
    days: int = 90,
) -> dict[str, Any]:
    return {
        "generated_at": beijing_now_text(),
        "user_id": str(user_id),
        "user_name": str(user_name),
        "coins": int(coins),
        "sign_streak": int(sign_streak),
        "sign_total": int(sign_total),
        "permissions": str(permissions or ""),
        "group": str(group or ""),
        "created_at": str(created_at),
        "sign_dates": [str(d) for d in sign_dates],
        "days": max(7, min(int(days), 365)),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "user_id": str(payload.get("user_id", "")),
        "user_name": str(payload.get("user_name", "")),
        "coins": int(payload.get("coins", 0)),
        "sign_streak": int(payload.get("sign_streak", 0)),
        "sign_total": int(payload.get("sign_total", 0)),
        "permissions": str(payload.get("permissions", "")),
        "group": str(payload.get("group", "")),
        "created_at": str(payload.get("created_at", "")),
        "sign_dates": payload.get("sign_dates", []),
        "days": int(payload.get("days", 90)),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__USER_INFO_DATA_JSON__", data_json)
    return content.encode("utf-8")
