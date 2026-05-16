from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from nonebot.log import logger

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "rob.html"

_VALID_RESULT_KINDS = frozenset({"crit", "success", "counter", "police", "fail"})
_VALID_CAP_SUBJECTS = frozenset({"robber", "victim", "none"})

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
            f"rob_page: int 字段无法解析，value={value!r}，已 fallback 为 {default}"
        )
        return default
    if n < min_v:
        logger.warning(
            f"rob_page: int 字段下溢，value={value!r}，已 clamp 到 {min_v}"
        )
        return min_v
    if n > max_v:
        logger.warning(
            f"rob_page: int 字段上溢，value={value!r}，已 clamp 到 {max_v}"
        )
        return max_v
    return n


def build_payload(
    *,
    robber_name: str,
    robber_qq: str,
    victim_name: str,
    victim_qq: str,
    result_kind: str,
    result_label: str,
    amount: int,
    applied_amount: int,
    capped: bool,
    cap_subject: str,
    robber_final_coins: int,
) -> dict[str, Any]:
    kind = str(result_kind).strip()
    if kind not in _VALID_RESULT_KINDS:
        logger.warning(
            f"rob_page: result_kind 非法，value={result_kind!r}，已 fallback 为 fail"
        )
        kind = "fail"

    subject = str(cap_subject).strip()
    if subject not in _VALID_CAP_SUBJECTS:
        logger.warning(
            f"rob_page: cap_subject 非法，value={cap_subject!r}，已 fallback 为 none"
        )
        subject = "none"

    return {
        # 32 与 User.name 注册路径 max 长度对齐；defense-in-depth 防极长昵称破版。
        "robber_name": str(robber_name).strip()[:32],
        "robber_qq": str(robber_qq).strip(),
        "victim_name": str(victim_name).strip()[:32],
        "victim_qq": str(victim_qq).strip(),
        "result_kind": kind,
        "result_label": str(result_label).strip()[:16],
        "amount": int(amount),
        "applied_amount": int(applied_amount),
        "capped": bool(capped),
        "cap_subject": subject,
        "robber_final_coins": int(robber_final_coins),
        "generated_at": beijing_now_text(),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    kind = str(payload.get("result_kind", "fail"))
    if kind not in _VALID_RESULT_KINDS:
        kind = "fail"
    subject = str(payload.get("cap_subject", "none"))
    if subject not in _VALID_CAP_SUBJECTS:
        subject = "none"
    data = {
        "robber_name": str(payload.get("robber_name", "")),
        "robber_qq": str(payload.get("robber_qq", "")),
        "victim_name": str(payload.get("victim_name", "")),
        "victim_qq": str(payload.get("victim_qq", "")),
        "result_kind": kind,
        "result_label": str(payload.get("result_label", "")),
        "amount": int(payload.get("amount", 0)),
        "applied_amount": int(payload.get("applied_amount", 0)),
        "capped": bool(payload.get("capped", False)),
        "cap_subject": subject,
        "robber_final_coins": int(payload.get("robber_final_coins", 0)),
        "generated_at": str(payload.get("generated_at", "")),
    }
    # JSON-safe escape: prevent the JSON literal from prematurely closing
    # the surrounding <script> via "</script>" sequences.
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__ROB_DATA_JSON__", data_json)
    return content.encode("utf-8")
