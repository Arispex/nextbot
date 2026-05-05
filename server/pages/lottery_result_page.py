from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "lottery_result.html"

# Rarity tier thresholds — upper bound (inclusive) of draw probability percent.
# Bucket: 0=miss, 1=common, 2=uncommon, 3=rare, 4=epic, 5=legendary.
_TIER_LEGENDARY_MAX_PCT = 1.0
_TIER_EPIC_MAX_PCT = 5.0
_TIER_RARE_MAX_PCT = 15.0
_TIER_UNCOMMON_MAX_PCT = 40.0


def _rarity_tier(kind: str, prob_pct: float) -> int:
    """Map (kind, draw probability %) to a 0-5 rarity tier."""
    if kind == "miss":
        return 0
    if prob_pct <= _TIER_LEGENDARY_MAX_PCT:
        return 5
    if prob_pct <= _TIER_EPIC_MAX_PCT:
        return 4
    if prob_pct <= _TIER_RARE_MAX_PCT:
        return 3
    if prob_pct <= _TIER_UNCOMMON_MAX_PCT:
        return 2
    return 1


def _normalize_outcomes(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in outcomes:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip()
        if kind not in {"item", "command", "coin", "miss"}:
            continue
        try:
            count = max(1, int(raw.get("count", 1)))
        except (TypeError, ValueError):
            continue
        try:
            probability = float(raw.get("probability", 0.0))
        except (TypeError, ValueError):
            probability = 0.0
        probability = max(0.0, min(100.0, probability))
        entry: dict[str, Any] = {
            "kind": kind,
            "count": count,
            "name": str(raw.get("name", "")).strip(),
            "is_mystery": bool(raw.get("is_mystery", False)),
            "probability": probability,
            "rarity_tier": _rarity_tier(kind, probability),
        }
        if kind == "item":
            try:
                entry["item_id"] = max(0, int(raw.get("item_id", 0)))
                entry["prefix_id"] = max(0, int(raw.get("prefix_id", 0)))
                entry["quantity"] = max(1, int(raw.get("quantity", 1)))
            except (TypeError, ValueError):
                continue
            entry["total_quantity"] = entry["quantity"] * count
        elif kind == "coin":
            try:
                entry["coin_amount"] = int(raw.get("coin_amount", 0))
            except (TypeError, ValueError):
                continue
            entry["total_coin"] = entry["coin_amount"] * count
        out.append(entry)
    # Sort by rarity desc, then count desc (rarest big-count first)
    out.sort(key=lambda e: (-e["rarity_tier"], -e["count"]))
    return out


def build_payload(
    *,
    pool_id: int,
    pool_name: str,
    user_user_id: str,
    user_user_name: str,
    user_coins_after: int,
    draw_count: int,
    total_cost: int,
    coin_delta: int,
    outcomes: list[dict[str, Any]],
    item_value_gained: int = 0,
    item_slots_used: int = 0,
    command_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_outcomes(outcomes)
    cmd_results = command_results or []
    return {
        "generated_at": beijing_now_text(),
        "pool_id": int(pool_id),
        "pool_name": str(pool_name),
        "user_user_id": str(user_user_id),
        "user_user_name": str(user_user_name),
        "user_coins_after": int(user_coins_after),
        "draw_count": max(1, int(draw_count)),
        "total_cost": int(total_cost),
        "coin_delta": int(coin_delta),
        "item_value_gained": max(0, int(item_value_gained)),
        "outcomes": normalized,
        "item_slots_used": int(item_slots_used),
        "command_results": [
            {
                "server_label": str(r.get("server_label", "")),
                "ok": bool(r.get("ok", False)),
                "reason": str(r.get("reason", "")),
            }
            for r in cmd_results
            if isinstance(r, dict)
        ],
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "pool_id": int(payload.get("pool_id", 0)),
        "pool_name": str(payload.get("pool_name", "")),
        "user_user_id": str(payload.get("user_user_id", "")),
        "user_user_name": str(payload.get("user_user_name", "")),
        "user_coins_after": int(payload.get("user_coins_after", 0)),
        "draw_count": int(payload.get("draw_count", 1)),
        "total_cost": int(payload.get("total_cost", 0)),
        "coin_delta": int(payload.get("coin_delta", 0)),
        "item_value_gained": max(0, int(payload.get("item_value_gained", 0))),
        "outcomes": payload.get("outcomes", []),
        "item_slots_used": int(payload.get("item_slots_used", 0)),
        "command_results": payload.get("command_results", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__LOTTERY_RESULT_DATA_JSON__", data_json)
    return content.encode("utf-8")
