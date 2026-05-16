from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from nonebot.log import logger

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "signin.html"

# 当前段连续打卡 SVG 点链显示上限；超出部分通过 "+M 天" 标签合并显示。
# 与模板里的 max_streak_chain 字段一致；放在 server 端便于后续随设计 token 调整。
DEFAULT_MAX_STREAK_CHAIN = 30

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
            f"signin_page: int 字段无法解析，value={value!r}，已 fallback 为 {default}"
        )
        return default
    if n < min_v:
        logger.warning(
            f"signin_page: int 字段下溢，value={value!r}，已 clamp 到 {min_v}"
        )
        return min_v
    if n > max_v:
        logger.warning(
            f"signin_page: int 字段上溢，value={value!r}，已 clamp 到 {max_v}"
        )
        return max_v
    return n


def _normalize_recent_signs(value: Any, length: int) -> list[bool]:
    """Hybrid streak chain：把入参 right-align 成 ``length`` 长度的 bool 数组。

    - 非 list/tuple → 全 False
    - 过长 → 取末尾 ``length`` 个（保留最近若干天）
    - 过短 → 左侧补 False（最早若干天补为未签）

    这里只做形状归一化，单元素是否真的为布尔交由 JS 端 truthy 处理；
    Python 层用 ``bool(...)`` 显式转换以防 None / 非布尔类型滑入 JSON。
    """
    if not isinstance(value, (list, tuple)):
        return [False] * length
    items = [bool(v) for v in value]
    if len(items) > length:
        items = items[-length:]
    elif len(items) < length:
        items = [False] * (length - len(items)) + items
    return items


def build_payload(
    *,
    player_name: str,
    player_qq: str,
    today_order: int,
    base_reward: int,
    streak_reward: int,
    total_reward: int,
    current_streak: int,
    streak_enabled: bool,
    streak_broken: bool,
    recent_signs: list[bool],
    coins_after: int,
    sign_total: int,
    capped: bool,
    requested_reward: int,
    applied_reward: int,
) -> dict[str, Any]:
    return {
        # 32 与 User.name 注册路径 max 长度对齐；defense-in-depth 防极长昵称破版。
        "player_name": str(player_name).strip()[:32],
        "player_qq": str(player_qq).strip(),
        "today_order": int(today_order),
        "base_reward": int(base_reward),
        "streak_reward": int(streak_reward),
        "total_reward": int(total_reward),
        "current_streak": int(current_streak),
        "streak_enabled": bool(streak_enabled),
        "streak_broken": bool(streak_broken),
        # recent_signs[i]：i=0 为 29 天前，i=29 为今天。
        # 长度归一化在 builder 而非 render，避免渲染端再 normalize 一次。
        "recent_signs": _normalize_recent_signs(recent_signs, DEFAULT_MAX_STREAK_CHAIN),
        "max_streak_chain": DEFAULT_MAX_STREAK_CHAIN,
        "coins_after": int(coins_after),
        "sign_total": int(sign_total),
        "capped": bool(capped),
        "requested_reward": int(requested_reward),
        "applied_reward": int(applied_reward),
        "generated_at": beijing_now_text(),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    chain_len = int(payload.get("max_streak_chain", DEFAULT_MAX_STREAK_CHAIN))
    data = {
        "player_name": str(payload.get("player_name", "")),
        "player_qq": str(payload.get("player_qq", "")),
        "today_order": int(payload.get("today_order", 0)),
        "base_reward": int(payload.get("base_reward", 0)),
        "streak_reward": int(payload.get("streak_reward", 0)),
        "total_reward": int(payload.get("total_reward", 0)),
        "current_streak": int(payload.get("current_streak", 0)),
        "streak_enabled": bool(payload.get("streak_enabled", False)),
        "streak_broken": bool(payload.get("streak_broken", False)),
        # Defensive normalize again at render time：build_payload 已 normalize，
        # 但渲染端可能拿到旧 cache / 手工构造 payload，保险起见再 right-align 一次。
        "recent_signs": _normalize_recent_signs(payload.get("recent_signs"), chain_len),
        "max_streak_chain": chain_len,
        "coins_after": int(payload.get("coins_after", 0)),
        "sign_total": int(payload.get("sign_total", 0)),
        "capped": bool(payload.get("capped", False)),
        "requested_reward": int(payload.get("requested_reward", 0)),
        "applied_reward": int(payload.get("applied_reward", 0)),
        "generated_at": str(payload.get("generated_at", "")),
    }
    # JSON-safe escape: prevent the JSON literal from prematurely closing
    # the surrounding <script> via "</script>" sequences.
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__SIGNIN_DATA_JSON__", data_json)
    return content.encode("utf-8")
