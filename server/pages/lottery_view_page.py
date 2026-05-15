from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.progression import PROGRESSION_KEY_TO_ZH, PROGRESSION_RANK
from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "lottery_view.html"

MAX_ENTRIES = 200

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def _normalize_prizes(prizes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in prizes:
        if not isinstance(raw, dict):
            continue
        if len(out) >= MAX_ENTRIES:
            break
        kind = str(raw.get("kind", "")).strip()
        if kind not in {"item", "command", "coin"}:
            continue
        try:
            probability = float(raw.get("probability", 0.0))
        except (TypeError, ValueError):
            continue
        entry: dict[str, Any] = {
            "name": str(raw.get("name", "")).strip(),
            "description": str(raw.get("description", "")).strip(),
            "kind": kind,
            "probability": max(0.0, min(100.0, probability)),
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
        elif kind == "command":
            target_server_id = raw.get("target_server_id")
            target_server_label = str(raw.get("target_server_label", "")).strip() or "全部服务器"
            entry.update({
                "target_server_id": int(target_server_id) if target_server_id is not None else None,
                "target_server_label": target_server_label,
                "command_template": str(raw.get("command_template", "")),
            })
        else:  # coin
            try:
                coin_amount = int(raw.get("coin_amount", 0))
            except (TypeError, ValueError):
                coin_amount = 0
            entry["coin_amount"] = coin_amount
        out.append(entry)
    return out


def build_payload(
    *,
    pool_id: int,
    pool_name: str,
    pool_description: str,
    cost_per_draw: int,
    prizes: list[dict[str, Any]],
    miss_probability: float = 0.0,
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
) -> dict[str, Any]:
    normalized = _normalize_prizes(prizes)
    return {
        "generated_at": beijing_now_text(),
        "pool_id": int(pool_id),
        "pool_name": str(pool_name),
        "pool_description": str(pool_description),
        "cost_per_draw": int(cost_per_draw),
        "prizes": normalized,
        "miss_probability": max(0.0, min(100.0, float(miss_probability))),
        "page": max(1, int(page)),
        "total_pages": max(1, int(total_pages)),
        "total": max(0, int(total)),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "pool_id": int(payload.get("pool_id", 0)),
        "pool_name": str(payload.get("pool_name", "")),
        "pool_description": str(payload.get("pool_description", "")),
        "cost_per_draw": int(payload.get("cost_per_draw", 0)),
        "prizes": payload.get("prizes", []),
        "miss_probability": float(payload.get("miss_probability", 0.0)),
        "page": int(payload.get("page", 1)),
        "total_pages": int(payload.get("total_pages", 1)),
        "total": int(payload.get("total", 0)),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__LOTTERY_VIEW_DATA_JSON__", data_json)
    return content.encode("utf-8")
