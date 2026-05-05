from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "tutorial.html"

_BOT_AVATAR = "__BOT__"


def _resolve_avatar(placeholder: str, self_user_id: str) -> str:
    raw = str(placeholder or "").strip()
    if raw == "__SELF__":
        return f"http://q1.qlogo.cn/g?b=qq&nk={self_user_id}&s=100"
    if raw == "__BOT__":
        return _BOT_AVATAR
    return raw


def _resolve_name(raw: str) -> str:
    value = str(raw or "").strip()
    return value or "未命名"


def _normalize_chat(
    chat: Any,
    *,
    self_user_id: str,
) -> list[dict[str, str]]:
    if not isinstance(chat, list):
        return []
    out: list[dict[str, str]] = []
    for m in chat:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        if role not in {"user", "bot"}:
            continue
        out.append(
            {
                "role": role,
                "name": _resolve_name(m.get("name", "")),
                "avatar": _resolve_avatar(str(m.get("avatar", "")), self_user_id),
                "text": str(m.get("text", "")),
            }
        )
    return out


def _normalize_tip(tip: Any) -> dict[str, str] | None:
    if not isinstance(tip, dict):
        return None
    kind = str(tip.get("type", "")).strip().lower()
    if kind not in {"warn", "hint", "info"}:
        kind = "info"
    text = str(tip.get("text", "")).strip()
    if not text:
        return None
    return {"type": kind, "text": text}


def _normalize_steps(
    steps: Any,
    *,
    self_user_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "title": str(step.get("title", "")).strip(),
                "desc": str(step.get("desc", "")),
                "chat": _normalize_chat(
                    step.get("chat"), self_user_id=self_user_id,
                ),
                "tip": _normalize_tip(step.get("tip")),
            }
        )
    return out


def build_payload(
    *,
    tutorial: dict[str, Any],
    self_user_id: str,
) -> dict[str, Any]:
    return {
        "generated_at": beijing_now_text(),
        "title": str(tutorial.get("title", "")).strip(),
        "subtitle": str(tutorial.get("subtitle", "")).strip(),
        "emoji": str(tutorial.get("emoji", "")).strip(),
        "steps": _normalize_steps(
            tutorial.get("steps", []),
            self_user_id=str(self_user_id),
        ),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = {
        "generated_at": str(payload.get("generated_at", "")),
        "title": str(payload.get("title", "")),
        "subtitle": str(payload.get("subtitle", "")),
        "emoji": str(payload.get("emoji", "")),
        "steps": payload.get("steps", []),
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__TUTORIAL_DATA_JSON__", data_json)
    return content.encode("utf-8")
