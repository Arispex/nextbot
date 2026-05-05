from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.progression import PROGRESSION_KEY_TO_ZH, PROGRESSION_RANK
from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "warehouse.html"

WAREHOUSE_CAPACITY = 100


def _normalize_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for raw in slots:
        if not isinstance(raw, dict):
            continue
        try:
            slot_index = int(raw.get("slot_index", 0))
        except (TypeError, ValueError):
            continue
        if not (1 <= slot_index <= WAREHOUSE_CAPACITY):
            continue
        try:
            item_id = max(0, int(raw.get("item_id", 0)))
            prefix_id = max(0, int(raw.get("prefix_id", 0)))
            quantity = max(0, int(raw.get("quantity", 0)))
            value = max(0, int(raw.get("value", 0)))
        except (TypeError, ValueError):
            continue
        min_tier = str(raw.get("min_tier", "")).strip()
        by_index[slot_index] = {
            "slot_index": slot_index,
            "item_id": item_id,
            "prefix_id": prefix_id,
            "quantity": quantity,
            "value": value,
            "min_tier": min_tier,
            "min_tier_zh": PROGRESSION_KEY_TO_ZH.get(min_tier, min_tier),
            "min_tier_rank": PROGRESSION_RANK.get(min_tier, -1),
        }

    out: list[dict[str, Any]] = []
    for i in range(1, WAREHOUSE_CAPACITY + 1):
        if i in by_index:
            out.append(by_index[i])
        else:
            out.append(
                {
                    "slot_index": i,
                    "item_id": 0,
                    "prefix_id": 0,
                    "quantity": 0,
                    "value": 0,
                    "min_tier": "",
                    "min_tier_zh": "",
                    "min_tier_rank": -1,
                }
            )
    return out


def build_payload(
    *,
    owner_user_id: str,
    owner_user_name: str,
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = _normalize_slots(slots)
    used = sum(1 for s in normalized if s["item_id"] > 0)
    return {
        "generated_at": beijing_now_text(),
        "owner_user_id": str(owner_user_id),
        "owner_user_name": str(owner_user_name),
        "capacity": WAREHOUSE_CAPACITY,
        "used": used,
        "slots": normalized,
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "owner_user_id": str(payload.get("owner_user_id", "")),
        "owner_user_name": str(payload.get("owner_user_name", "")),
        "capacity": int(payload.get("capacity", WAREHOUSE_CAPACITY)),
        "used": int(payload.get("used", 0)),
        "slots": payload.get("slots", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__WAREHOUSE_DATA_JSON__", data_json)
    return content.encode("utf-8")
