from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "inventory.html"


def _normalize_slots(slots: list[dict[str, Any]]) -> list[dict[str, int]]:
    slot_map: dict[int, dict[str, Any]] = {}
    for item in slots:
        if not isinstance(item, dict):
            continue
        try:
            slot_index = int(item.get("slot", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= slot_index < 350:
            slot_map[slot_index] = item

    normalized: list[dict[str, int]] = []
    for index in range(350):
        net_id = 0
        prefix_id = 0
        stack = 0
        if index in slot_map:
            raw = slot_map[index]
            raw_net_id = raw.get("netId", 0)
            raw_prefix_id = raw.get("prefixId", 0)
            raw_stack = raw.get("stack", 0)
            try:
                net_id = int(raw_net_id)
            except (TypeError, ValueError):
                net_id = 0
            try:
                prefix_id = int(raw_prefix_id)
            except (TypeError, ValueError):
                prefix_id = 0
            try:
                stack = int(raw_stack)
            except (TypeError, ValueError):
                stack = 0
        normalized.append(
            {
                "net_id": max(net_id, 0),
                "prefix_id": max(prefix_id, 0),
                "stack": max(stack, 0),
            }
        )
    return normalized


def build_payload(
    *,
    user_id: str,
    user_name: str,
    server_id: int,
    server_name: str,
    life_text: str,
    mana_text: str,
    fishing_tasks_text: str,
    pve_deaths_text: str,
    pvp_deaths_text: str,
    show_stats: bool,
    show_index: bool,
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generated_at": beijing_now_text(),
        "user_id": str(user_id),
        "user_name": str(user_name),
        "server_id": str(server_id),
        "server_name": str(server_name),
        "life_text": str(life_text),
        "mana_text": str(mana_text),
        "fishing_tasks_text": str(fishing_tasks_text),
        "pve_deaths_text": str(pve_deaths_text),
        "pvp_deaths_text": str(pvp_deaths_text),
        "show_stats": bool(show_stats),
        "show_index": bool(show_index),
        "slots": _normalize_slots(slots),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "user_id": payload.get("user_id", ""),
        "user_name": payload.get("user_name", ""),
        "server_id": payload.get("server_id", ""),
        "server_name": payload.get("server_name", ""),
        "generated_at": payload.get("generated_at", ""),
        "life_text": payload.get("life_text", ""),
        "mana_text": payload.get("mana_text", ""),
        "fishing_tasks_text": payload.get("fishing_tasks_text", ""),
        "pve_deaths_text": payload.get("pve_deaths_text", ""),
        "pvp_deaths_text": payload.get("pvp_deaths_text", ""),
        "show_stats": bool(payload.get("show_stats", True)),
        "show_index": bool(payload.get("show_index", True)),
        "slots": payload.get("slots", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__INVENTORY_DATA_JSON__", data_json)
    return content.encode("utf-8")
