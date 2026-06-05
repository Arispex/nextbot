"""在线命令图片模式 + 页面渲染 + 边界降级测试。

依赖轻量：不连网、不依赖 pytest-only fixture。可在 pytest 下运行
（``uv run pytest tests/test_online_image_mode.py``）或作为脚本直接运行
（``uv run python tests/test_online_image_mode.py``）。

覆盖：
  - online_players_page.build_payload：按服务器分区 + 玩家字段规整。
  - online_players_page.render：`</` 注入转义为 `<\\/`（防 </script> 截断）。
  - 图片分支数据流：appearance=null 跳过、失败 server 跳过、部分失败仍出图。
  - 文字分支不回归：image_mode=False 走 /v2/server/status 文本路径，输出与现状一致。
  - 降级：全失败 / 无可渲染玩家 → 文字降级，原因原样透传。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

# allow `python tests/test_online_image_mode.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


# player_query 模块顶层调用 on_command(...)，需要 NoneBot 已初始化。
def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

import nextbot.plugins.player_query as pq  # noqa: I001
from nextbot.tshock_api import TShockRequestError, TShockResponse
from server.pages import online_players_page


# ── 测试替身 ───────────────────────────────────────────────────


class _FakeServer:
    def __init__(self, server_id: int, name: str) -> None:
        self.id = server_id
        self.name = name


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, _event: Any, message: Any) -> None:
        self.sent.append(message)


class _FakeEvent:
    def get_user_id(self) -> str:
        return "10001"


def _server(server_id: int, name: str) -> Any:
    return _FakeServer(server_id, name)


def _bot() -> Any:
    return _FakeBot()


def _event() -> Any:
    return _FakeEvent()


def _ok_response(players: list[dict[str, Any]]) -> TShockResponse:
    return TShockResponse(
        http_status=200, payload={"players": players}, api_status="200"
    )


def _err_response(reason: str) -> TShockResponse:
    return TShockResponse(
        http_status=200, payload={"error": reason}, api_status="400"
    )


def _player(
    name: str, *, appearance: dict[str, Any] | None, seconds: Any = 1234
) -> dict[str, Any]:
    """构造一个 /nextbot/online-players players[] 元素（字段名原样）。"""
    zero = {"netId": 0, "stack": 0, "prefixId": 0}
    return {
        "name": name,
        "appearance": appearance,
        "equipment": {"head": zero, "body": zero, "legs": zero} if appearance else None,
        "vanity": {"head": zero, "body": zero, "legs": zero} if appearance else None,
        "dye": {"head": zero, "body": zero, "legs": zero} if appearance else None,
        "accessories": [dict(zero) for _ in range(7)] if appearance else None,
        "vanityAccessories": [dict(zero) for _ in range(7)] if appearance else None,
        "accessoryDyes": [dict(zero) for _ in range(7)] if appearance else None,
        "sessionOnlineSeconds": seconds,
    }


_SAMPLE_APPEARANCE = {
    "skinVariant": 7, "hair": 112, "hairDye": 0,
    "hairColor": -3270602, "skinColor": -10059269, "eyeColor": -15100654,
    "shirtColor": -4021652, "underShirtColor": -4639811,
    "pantsColor": -12772014, "shoeColor": -4963208,
}


class _Patches:
    """简易 monkeypatch 上下文：保存 / 还原 pq 模块属性。"""

    def __init__(self) -> None:
        self._saved: dict[str, Any] = {}

    def set(self, name: str, value: Any) -> None:
        if name not in self._saved:
            self._saved[name] = getattr(pq, name)
        setattr(pq, name, value)

    def restore(self) -> None:
        for name, value in self._saved.items():
            setattr(pq, name, value)
        self._saved.clear()


def _run_coro(coro: Any) -> Any:
    return asyncio.run(coro)


def _fail_not_built(**_kwargs: Any) -> str:
    """create_online_players_page 替身：文字模式不应建图，被调用即断言失败。"""
    msg = "文字模式不应调用 create_online_players_page"
    raise AssertionError(msg)


# ── online_players_page 单测 ───────────────────────────────────


def test_build_payload_normalizes_servers_and_players() -> None:
    # 故意混入非 dict 服务器 / 非 dict 玩家 / players 非 list，验证规整健壮性。
    malformed_servers = cast(
        "list[dict[str, Any]]",
        [
            {
                "server_id": 3,
                "server_name": "Server A",
                "players": [
                    {
                        "name": "Alice",
                        "online_time_text": "20 分 34 秒",
                        "character_sprite_data_uri": "data:image/png;base64,AAA",
                    },
                    "not-a-dict",  # 非 dict 玩家应被跳过
                ],
            },
            "not-a-dict",  # 非 dict 服务器应被跳过
            # players 非 list → 规整为空列表；缺 server_id → 兜底 None
            {"server_name": "Server B", "players": "not-a-list"},
        ],
    )
    payload = online_players_page.build_payload(servers=malformed_servers)
    servers = payload["servers"]
    assert len(servers) == 2, servers
    assert servers[0]["server_id"] == 3
    assert servers[0]["server_name"] == "Server A"
    assert len(servers[0]["players"]) == 1
    assert servers[0]["players"][0]["name"] == "Alice"
    assert (
        servers[0]["players"][0]["character_sprite_data_uri"]
        == "data:image/png;base64,AAA"
    )
    assert servers[1]["server_id"] is None  # 缺失 server_id 兜底为 None
    assert servers[1]["server_name"] == "Server B"
    assert servers[1]["players"] == []
    assert payload["generated_at"]  # 非空时间戳


def test_build_payload_player_missing_fields_default_empty() -> None:
    payload = online_players_page.build_payload(
        servers=[{"server_name": "S", "players": [{"name": "Bob"}]}]
    )
    player = payload["servers"][0]["players"][0]
    assert player["name"] == "Bob"
    assert player["online_time_text"] == ""
    assert player["character_sprite_data_uri"] == ""


def test_build_payload_server_id_fallback() -> None:
    # int / 数字字符串 → int；非法字符串 / bool / 缺失 → None（不崩、模板省略前缀）。
    payload = online_players_page.build_payload(
        servers=cast(
            "list[dict[str, Any]]",
            [
                {"server_id": 5, "server_name": "A", "players": []},
                {"server_id": "7", "server_name": "B", "players": []},
                {"server_id": "abc", "server_name": "C", "players": []},
                {"server_id": True, "server_name": "D", "players": []},
                {"server_name": "E", "players": []},
            ],
        )
    )
    ids = [s["server_id"] for s in payload["servers"]]
    assert ids == [5, 7, None, None, None], ids


def test_render_outputs_server_id_in_html() -> None:
    payload = online_players_page.build_payload(
        servers=[
            {
                "server_id": 3,
                "server_name": "生存服",
                "players": [
                    {
                        "name": "Alice",
                        "online_time_text": "1 秒",
                        "character_sprite_data_uri": "data:image/png;base64,AAA",
                    }
                ],
            }
        ]
    )
    html = online_players_page.render(payload).decode("utf-8")
    json_blob = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(json_blob.replace("<\\/", "</"))
    assert data["servers"][0]["server_id"] == 3
    assert data["servers"][0]["server_name"] == "生存服"


def test_render_escapes_closing_script_tag() -> None:
    # 含 </script> 的字段必须在 JSON 注入时被转义为 <\/script>，否则截断脚本块。
    payload = online_players_page.build_payload(
        servers=[
            {
                "server_name": "</script><img src=x>",
                "players": [
                    {
                        "name": "evil</script>",
                        "online_time_text": "1 秒",
                        "character_sprite_data_uri": "data:image/png;base64,AAA",
                    }
                ],
            }
        ]
    )
    html = online_players_page.render(payload).decode("utf-8")
    # 原始 </ 不应出现在注入的 JSON 数据里（仅模板自身的 </script> 等结构标签除外）。
    json_blob = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in json_blob
    assert "<\\/script>" in json_blob
    # 占位符已被替换
    assert "__ONLINE_PLAYERS_DATA_JSON__" not in html


def test_render_outputs_player_data_in_html() -> None:
    payload = online_players_page.build_payload(
        servers=[
            {
                "server_name": "MyServer",
                "players": [
                    {
                        "name": "Charlie",
                        "online_time_text": "5 分钟",
                        "character_sprite_data_uri": "data:image/png;base64,ZZZ",
                    }
                ],
            }
        ]
    )
    html = online_players_page.render(payload).decode("utf-8")
    json_blob = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    data = json.loads(json_blob.replace("<\\/", "</"))
    assert data["servers"][0]["server_name"] == "MyServer"
    assert data["servers"][0]["players"][0]["name"] == "Charlie"
    assert data["servers"][0]["players"][0]["online_time_text"] == "5 分钟"


# ── _render_online_player_card：逐玩家渲染 + 跳过规则 ───────────


def test_render_card_skips_when_appearance_null() -> None:
    patches = _Patches()
    try:
        # render_character 不应被调用（appearance 为 None 时纯渲染核提前返回）
        def _boom(*_a: Any, **_k: Any) -> bytes:
            msg = "render_character 不应在 appearance=None 时被调用"
            raise AssertionError(msg)

        patches.set("render_character", _boom)
        card = _run_coro(
            pq._render_online_player_card(
                _player("Ghost", appearance=None), server_id=1
            )
        )
        assert card is None
    finally:
        patches.restore()


def test_render_card_builds_uri_and_online_text() -> None:
    patches = _Patches()
    try:
        patches.set("render_character", lambda *_a, **_k: b"PNGBYTES")
        card = _run_coro(
            pq._render_online_player_card(
                _player("Alice", appearance=_SAMPLE_APPEARANCE, seconds=1234),
                server_id=1,
            )
        )
        assert card is not None
        assert card["name"] == "Alice"
        assert card["online_time_text"] == "20 分 34 秒"
        assert card["character_sprite_data_uri"].startswith("data:image/png;base64,")
    finally:
        patches.restore()


def test_render_card_session_seconds_null_placeholder() -> None:
    patches = _Patches()
    try:
        patches.set("render_character", lambda *_a, **_k: b"PNGBYTES")
        card = _run_coro(
            pq._render_online_player_card(
                _player("Alice", appearance=_SAMPLE_APPEARANCE, seconds=None),
                server_id=1,
            )
        )
        assert card is not None
        # sessionOnlineSeconds 为 null → 文本为空，模板渲染为「在线 —」占位
        assert card["online_time_text"] == ""
    finally:
        patches.restore()


# ── _handle_online_image：图片分支数据流 ───────────────────────


def _install_image_mode_capture(patches: _Patches) -> dict[str, Any]:
    """安装图片分支所需替身：捕获 create_online_players_page 的 servers 入参
    与 render_and_send_screenshot 的调用。"""
    captured: dict[str, Any] = {"page_servers": None, "screenshot_called": False}

    def _fake_create_page(*, servers: list[dict[str, Any]]) -> str:
        captured["page_servers"] = servers
        return "http://127.0.0.1:0/render/online_players/tok"

    async def _fake_screenshot(*_a: Any, **kwargs: Any) -> bool:
        captured["screenshot_called"] = True
        captured["screenshot_kwargs"] = kwargs
        return True

    patches.set("create_online_players_page", _fake_create_page)
    patches.set("render_and_send_screenshot", _fake_screenshot)
    patches.set("render_character", lambda *_a, **_k: b"PNGBYTES")
    return captured


def test_image_mode_data_flow_filters_and_renders() -> None:
    patches = _Patches()
    try:
        captured = _install_image_mode_capture(patches)

        s1 = _server(1, "Server A")
        s2 = _server(2, "Server B")
        s3 = _server(3, "Server C")

        async def _fake_request(server: Any, path: str, *_a: Any, **_k: Any):
            assert path == "/nextbot/online-players", path
            if server.id == 1:
                # 一个可渲染 + 一个 appearance=null（应跳过）
                return _ok_response(
                    [
                        _player("Alice", appearance=_SAMPLE_APPEARANCE),
                        _player("Ghost", appearance=None),
                    ]
                )
            if server.id == 2:
                # 整台失败（连接级）→ 跳过该服务器
                raise TShockRequestError("boom", kind="unreachable")
            # server 3：无人在线（空列表）→ 无可渲染玩家，但有成功响应
            return _ok_response([])

        patches.set("request_server_api", _fake_request)

        bot = _bot()
        event = _event()
        _run_coro(pq._handle_online_image(bot, event, [s1, s2, s3]))

        # 出图：截图被调用，文字未发送
        assert captured["screenshot_called"] is True
        assert bot.sent == [], f"图片模式不应发文字：{bot.sent}"
        # 只有 Server A 进图（A 有 1 个可渲染；B 失败跳过；C 无玩家跳过）
        page_servers = captured["page_servers"]
        assert page_servers is not None
        assert len(page_servers) == 1, page_servers
        assert page_servers[0]["server_id"] == 1
        assert page_servers[0]["server_name"] == "Server A"
        assert len(page_servers[0]["players"]) == 1
        assert page_servers[0]["players"][0]["name"] == "Alice"
        # 图片榜单不 @ 任何人（不传 at_user_id）
        assert "at_user_id" not in captured["screenshot_kwargs"]
        assert captured["screenshot_kwargs"].get("semaphore") is None
    finally:
        patches.restore()


def test_image_mode_degrades_to_text_when_all_fail() -> None:
    patches = _Patches()
    try:
        captured = _install_image_mode_capture(patches)

        s1 = _server(1, "Server A")
        s2 = _server(2, "Server B")

        # /nextbot/online-players 全失败；文字降级会再调 /v2/server/status。
        async def _fake_request(_server: Any, path: str, *_a: Any, **_k: Any):
            if path == "/nextbot/online-players":
                return _err_response("权限不足")
            # 文字降级路径
            return _ok_response([])  # /v2/server/status 缺 playercount → 文字格式错误行

        patches.set("request_server_api", _fake_request)

        bot = _bot()
        _run_coro(pq._handle_online_image(bot, _event(), [s1, s2]))

        # 未出图，降级文字
        assert captured["screenshot_called"] is False
        assert len(bot.sent) == 1
        assert "服务器在线状态" in str(bot.sent[0])
    finally:
        patches.restore()


def test_image_mode_degrades_to_text_when_no_renderable_players() -> None:
    patches = _Patches()
    try:
        captured = _install_image_mode_capture(patches)

        s1 = _server(1, "Server A")

        async def _fake_request(_server: Any, path: str, *_a: Any, **_k: Any):
            if path == "/nextbot/online-players":
                # 有成功响应，但玩家都无 appearance（模拟刚连入无 SSC）
                return _ok_response([_player("Ghost", appearance=None)])
            # 文字降级路径：返回一个正常状态，确认走的是 /v2/server/status
            return TShockResponse(
                http_status=200,
                payload={"players": [], "playercount": 0, "maxplayers": 8},
                api_status="200",
            )

        patches.set("request_server_api", _fake_request)

        bot = _bot()
        _run_coro(pq._handle_online_image(bot, _event(), [s1]))

        assert captured["screenshot_called"] is False
        assert len(bot.sent) == 1
        body = str(bot.sent[0])
        assert "服务器在线状态" in body
        assert "无玩家在线" in body  # 文字模式的空在线语义
    finally:
        patches.restore()


# ── _handle_online_text：文字分支不回归 ────────────────────────


def test_text_mode_output_matches_status_path() -> None:
    patches = _Patches()
    try:
        s1 = _server(1, "Server A")
        s2 = _server(2, "Server B")

        async def _fake_request(server: Any, path: str, *_a: Any, **_k: Any):
            assert path == "/v2/server/status", path
            if server.id == 1:
                return TShockResponse(
                    http_status=200,
                    payload={
                        "players": [{"nickname": "Alice"}, {"nickname": "Bob"}],
                        "playercount": 2,
                        "maxplayers": 8,
                    },
                    api_status="200",
                )
            return TShockResponse(
                http_status=200,
                payload={"players": [], "playercount": 0, "maxplayers": 8},
                api_status="200",
            )

        patches.set("request_server_api", _fake_request)
        # create_online_players_page 不应在文字模式被调用
        patches.set("create_online_players_page", _fail_not_built)

        bot = _bot()
        _run_coro(pq._handle_online_text(bot, _event(), [s1, s2]))

        assert len(bot.sent) == 1
        body = str(bot.sent[0])
        # 与历史文字输出一致的关键片段
        assert body.startswith("🖥️ 服务器在线状态")
        assert "1.Server A" in body
        assert "在线玩家（2/8）" in body
        assert "Alice,Bob" in body
        assert "2.Server B" in body
        assert "无玩家在线" in body
    finally:
        patches.restore()


def test_text_mode_partial_failure_passes_through_reason() -> None:
    patches = _Patches()
    try:
        s1 = _server(1, "Server A")
        s2 = _server(2, "Server B")

        async def _fake_request(server: Any, _path: str, *_a: Any, **_k: Any):
            if server.id == 1:
                raise TShockRequestError("conn", kind="unreachable")
            return _err_response("自定义原始错误")

        patches.set("request_server_api", _fake_request)
        bot = _bot()
        _run_coro(pq._handle_online_text(bot, _event(), [s1, s2]))

        body = str(bot.sent[0])
        assert "无法连接服务器" in body
        # 原始 API error.message 原样透传（不改写）
        assert "自定义原始错误" in body
    finally:
        patches.restore()


def _run() -> int:
    tests = [
        test_build_payload_normalizes_servers_and_players,
        test_build_payload_player_missing_fields_default_empty,
        test_build_payload_server_id_fallback,
        test_render_outputs_server_id_in_html,
        test_render_escapes_closing_script_tag,
        test_render_outputs_player_data_in_html,
        test_render_card_skips_when_appearance_null,
        test_render_card_builds_uri_and_online_text,
        test_render_card_session_seconds_null_placeholder,
        test_image_mode_data_flow_filters_and_renders,
        test_image_mode_degrades_to_text_when_all_fail,
        test_image_mode_degrades_to_text_when_no_renderable_players,
        test_text_mode_output_matches_status_path,
        test_text_mode_partial_failure_passes_through_reason,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
