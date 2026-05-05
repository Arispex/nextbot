from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.progression import PROGRESSION_KEY_TO_ZH, PROGRESSION_RANK
from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "shop_view.html"


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip()
        if kind not in {"item", "command"}:
            continue
        try:
            shop_item_id = int(raw.get("shop_item_id", 0))
            price = max(0, int(raw.get("price", 0)))
        except (TypeError, ValueError):
            continue
        entry: dict[str, Any] = {
            "shop_item_id": shop_item_id,
            "name": str(raw.get("name", "")).strip(),
            "description": str(raw.get("description", "")).strip(),
            "kind": kind,
            "price": price,
        }
        if kind == "item":
            try:
                item_id = max(0, int(raw.get("item_id", 0)))
                prefix_id = max(0, int(raw.get("prefix_id", 0)))
                quantity = max(1, int(raw.get("quantity", 1)))
            except (TypeError, ValueError):
                continue
            min_tier = str(raw.get("min_tier", "none")).strip() or "none"
            entry.update({
                "item_id": item_id,
                "prefix_id": prefix_id,
                "quantity": quantity,
                "min_tier": min_tier,
                "min_tier_zh": PROGRESSION_KEY_TO_ZH.get(min_tier, min_tier),
                "min_tier_rank": PROGRESSION_RANK.get(min_tier, -1),
                "is_mystery": bool(raw.get("is_mystery", False)),
            })
        else:
            target_server_id = raw.get("target_server_id")
            target_server_label = str(raw.get("target_server_label", "")).strip() or "全部服务器"
            entry.update({
                "target_server_id": int(target_server_id) if target_server_id is not None else None,
                "target_server_label": target_server_label,
                "command_template": str(raw.get("command_template", "")),
            })
        out.append(entry)
    return out


def build_payload(
    *,
    shop_id: int,
    shop_name: str,
    shop_description: str,
    user_user_id: str,
    user_user_name: str,
    user_coins: int,
    items: list[dict[str, Any]],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> dict[str, Any]:
    normalized = _normalize_items(items)
    return {
        "generated_at": beijing_now_text(),
        "shop_id": int(shop_id),
        "shop_name": str(shop_name),
        "shop_description": str(shop_description),
        "user_user_id": str(user_user_id),
        "user_user_name": str(user_user_name),
        "user_coins": int(user_coins),
        "items": normalized,
        "page": max(1, int(page)),
        "total_pages": max(1, int(total_pages)),
        "total": max(0, int(total)),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "shop_id": int(payload.get("shop_id", 0)),
        "shop_name": str(payload.get("shop_name", "")),
        "shop_description": str(payload.get("shop_description", "")),
        "user_user_id": str(payload.get("user_user_id", "")),
        "user_user_name": str(payload.get("user_user_name", "")),
        "user_coins": int(payload.get("user_coins", 0)),
        "items": payload.get("items", []),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "total": int(payload.get("total", 0)),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__SHOP_VIEW_DATA_JSON__", data_json)
    return content.encode("utf-8")
