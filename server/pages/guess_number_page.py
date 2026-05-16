from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from nonebot.log import logger

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "guess_number.html"

_VALID_RESULT_KINDS = frozenset({"exact", "near", "close", "far", "miss"})

_template_cache: tuple[float, str] | None = None
# defense-in-depth：截图链路理论上单事件循环串行，但若未来出现并发 import / 多线程渲染，
# 这把锁防止 stat + read + 写 cache 三段非原子操作产生撕裂状态。
_template_lock = threading.Lock()


def _load_template() -> str:
    global _template_cache
    with _template_lock:
        mtime = TEMPLATE_PATH.stat().st_mtime
        if _template_cache is None or _template_cache[0] != mtime:
            _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
        return _template_cache[1]


def _clamp_int(value: Any, min_v: int, max_v: int, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning(
            f"guess_number_page: int 字段无法解析，value={value!r}，已 fallback 为 {default}"
        )
        return default
    if n < min_v:
        logger.warning(
            f"guess_number_page: int 字段下溢，value={value!r}，已 clamp 到 {min_v}"
        )
        return min_v
    if n > max_v:
        logger.warning(
            f"guess_number_page: int 字段上溢，value={value!r}，已 clamp 到 {max_v}"
        )
        return max_v
    return n


def build_payload(
    *,
    player_name: str,
    player_qq: str,
    range_max: int,
    guess: int,
    answer: int,
    diff: int,
    cost: int,
    result_kind: str,
    result_label: str,
    payout: int,
    applied_payout: int,
    net: int,
    applied_net: int,
    final_coins: int,
    capped: bool,
) -> dict[str, Any]:
    kind = str(result_kind).strip()
    if kind not in _VALID_RESULT_KINDS:
        logger.warning(
            f"guess_number_page: result_kind 非法，value={result_kind!r}，已 fallback 为 miss"
        )
        kind = "miss"

    return {
        # 32 与 User.name 注册路径 max 长度对齐；defense-in-depth 防极长昵称破版。
        "player_name": str(player_name).strip()[:32],
        "player_qq": str(player_qq).strip(),
        "range_max": int(range_max),
        "guess": int(guess),
        "answer": int(answer),
        "diff": int(diff),
        "cost": int(cost),
        "result_kind": kind,
        "result_label": str(result_label).strip()[:16],
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
    kind = str(payload.get("result_kind", "miss"))
    if kind not in _VALID_RESULT_KINDS:
        kind = "miss"
    data = {
        "player_name": str(payload.get("player_name", "")),
        "player_qq": str(payload.get("player_qq", "")),
        "range_max": int(payload.get("range_max", 0)),
        "guess": int(payload.get("guess", 0)),
        "answer": int(payload.get("answer", 0)),
        "diff": int(payload.get("diff", 0)),
        "cost": int(payload.get("cost", 0)),
        "result_kind": kind,
        "result_label": str(payload.get("result_label", "")),
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
    content = template.replace("__GUESS_NUMBER_DATA_JSON__", data_json)
    return content.encode("utf-8")
