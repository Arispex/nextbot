from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nextbot.time_utils import beijing_now_text

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = BASE_DIR / "server" / "templates" / "online_players.html"

# 单图硬上限：避免极端场景（大量服务器 / 大量在线玩家）合成超大页拖垮浏览器截图。
# 渲染侧（handler）已只装可渲染玩家的 data URI，这里仅作兜底裁剪。
MAX_PLAYERS_PER_SERVER = 100

_template_cache: tuple[float, str] | None = None


def _load_template() -> str:
    global _template_cache
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _template_cache is None or _template_cache[0] != mtime:
        _template_cache = (mtime, TEMPLATE_PATH.read_text(encoding="utf-8"))
    return _template_cache[1]


def _normalize_player(player: dict[str, Any]) -> dict[str, str]:
    """单个玩家卡片字段规整：账号名、本次在线时长文本、立绘 data URI。

    立绘 data URI 由 handler 侧 ``render_character`` 已合成完毕，本函数只做
    字符串化兜底（防 None / 非 str），不重新渲染。
    """
    sprite_uri = player.get("character_sprite_data_uri")
    return {
        "name": str(player.get("name", "")).strip(),
        "online_time_text": str(player.get("online_time_text", "")).strip(),
        "character_sprite_data_uri": (
            str(sprite_uri) if isinstance(sprite_uri, str) else ""
        ),
    }


def _normalize_servers(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        raw_players = server.get("players")
        players: list[dict[str, str]] = []
        if isinstance(raw_players, list):
            for player in raw_players:
                if not isinstance(player, dict):
                    continue
                if len(players) >= MAX_PLAYERS_PER_SERVER:
                    break
                players.append(_normalize_player(player))
        normalized.append(
            {
                "server_name": str(server.get("server_name", "")).strip(),
                "players": players,
            }
        )
    return normalized


def build_payload(
    *,
    servers: list[dict[str, Any]],
) -> dict[str, Any]:
    """构建在线玩家榜单页 payload。

    ``servers``：按服务器分区的列表，每项 ``{server_name, players:[{name,
    online_time_text, character_sprite_data_uri}]}``。立绘 data URI 已由
    handler 侧 ``render_character`` 合成；本页只负责布局展示。
    """
    return {
        "generated_at": beijing_now_text(),
        "servers": _normalize_servers(servers),
    }


def render(payload: dict[str, Any]) -> bytes:
    template = _load_template()
    data = {
        "generated_at": payload.get("generated_at", ""),
        "servers": payload.get("servers", []),
    }
    # 与 inventory_page.render 一致：注入前 `</` → `<\/`，防止 data URI / 服务器名
    # 含 `</script>` 截断 <script type="application/json"> 块（XSS / 渲染破坏）。
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    content = template.replace("__ONLINE_PLAYERS_DATA_JSON__", data_json)
    return content.encode("utf-8")
