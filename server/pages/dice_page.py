from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "dice.html"

_VALID_RESULT_KINDS = {"win", "lose", "triple_win", "triple_kill", "tie"}

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def _clamp_die(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    if n < 1:
        return 1
    if n > 6:
        return 6
    return n


def build_payload(
    *,
    player_name: str,
    player_qq: str,
    choice: str,
    cost: int,
    dice: tuple[int, int, int] | list[int],
    total: int,
    is_triple: bool,
    result_kind: str,
    payout: int,
    applied_payout: int,
    net: int,
    applied_net: int,
    final_coins: int,
    capped: bool,
) -> dict[str, Any]:
    dice_list = [_clamp_die(d) for d in list(dice)[:3]]
    while len(dice_list) < 3:
        dice_list.append(1)

    kind = str(result_kind).strip()
    if kind not in _VALID_RESULT_KINDS:
        # Fallback to lose if invalid; safer than crashing the template.
        kind = "lose"

    return {
        "player_name": str(player_name).strip(),
        "player_qq": str(player_qq).strip(),
        "choice": str(choice).strip(),
        "cost": int(cost),
        "dice": dice_list,
        "total": int(total),
        "is_triple": bool(is_triple),
        "result_kind": kind,
        "payout": int(payout),
        "applied_payout": int(applied_payout),
        "net": int(net),
        "applied_net": int(applied_net),
        "final_coins": int(final_coins),
        "capped": bool(capped),
        "generated_at": beijing_now_text(),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "player_name": str(payload.get("player_name", "")),
        "player_qq": str(payload.get("player_qq", "")),
        "choice": str(payload.get("choice", "")),
        "cost": int(payload.get("cost", 0)),
        "dice": [_clamp_die(d) for d in (payload.get("dice") or [1, 1, 1])[:3]],
        "total": int(payload.get("total", 0)),
        "is_triple": bool(payload.get("is_triple", False)),
        "result_kind": str(payload.get("result_kind", "lose")),
        "payout": int(payload.get("payout", 0)),
        "applied_payout": int(payload.get("applied_payout", 0)),
        "net": int(payload.get("net", 0)),
        "applied_net": int(payload.get("applied_net", 0)),
        "final_coins": int(payload.get("final_coins", 0)),
        "capped": bool(payload.get("capped", False)),
        "generated_at": str(payload.get("generated_at", "")),
    }
    # JSON-safe escape: prevent the JSON literal from prematurely closing
    # the surrounding <script> via "</script>" sequences.
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__DICE_DATA_JSON__", data_json)
    return content.encode("utf-8")
