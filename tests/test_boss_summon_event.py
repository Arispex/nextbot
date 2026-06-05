"""Boss 召唤通知对外 API（复用 POST /webui/api/player-events，event=boss_summon）测试。

依赖轻量：不连网、不依赖 pytest-only fixture。可在 pytest 下运行
（``uv run pytest tests/test_boss_summon_event.py``）或作为脚本直接运行
（``uv run python tests/test_boss_summon_event.py``）。

覆盖：
  - boss_summon 成功下发（mock bot + 群解析 + 模板渲染含 {boss}）。
  - 缺 boss → 422 field=boss。
  - boss 含非法控制字符 → 422 field=boss。
  - boss 超长 → 422 field=boss。
  - 注入防护：boss 含 {player} 不被二次替换（渲染为全角占位）。
  - 未配置目标群 → 503 service_misconfigured。
  - bot 未连接 → 503 bot_unavailable。
  - 既有 online 事件不回归（仍走 player_notify_* 设置 + 模板）。
  - _render_template 直接单测 {boss} 渲染 + 注入。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fastapi import Request

# allow `python tests/test_boss_summon_event.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

import server.routes.webui_player_events as pe  # noqa: I001


# ── 测试替身 ───────────────────────────────────────────────────


class _FakeBot:
    """OneBot V11 bot 替身：记录 send_group_msg 调用，按预置成败回应。"""

    def __init__(self, *, fail_groups: set[int] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_groups = fail_groups or set()
        self.self_id = "100000"

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        self.calls.append({"api": api, **kwargs})
        gid = int(kwargs.get("group_id", 0))
        if gid in self._fail_groups:
            raise RuntimeError("send failed")  # noqa: TRY003
        return {"message_id": 9000 + gid}


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    """最小 Request 替身：满足 read_json_object + client_ip + user_agent。"""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.headers = {"content-type": "application/json"}
        self.client = _FakeClient()
        self.app = type("App", (), {"state": type("State", (), {})()})()

    async def stream(self) -> Any:
        yield self._body


class _Patches:
    """简易 monkeypatch 上下文：保存 / 还原 pe 模块属性。"""

    def __init__(self) -> None:
        self._saved: dict[str, Any] = {}

    def set(self, name: str, value: Any) -> None:
        if name not in self._saved:
            self._saved[name] = getattr(pe, name)
        setattr(pe, name, value)

    def restore(self) -> None:
        for name, value in self._saved.items():
            setattr(pe, name, value)
        self._saved.clear()


class _FakeConfig:
    """nonebot driver.config 替身，仅暴露用到的字段。"""

    def __init__(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def _install_common(
    patches: _Patches,
    *,
    bot: Any,
    target_groups: list[int],
    config: _FakeConfig,
    bound_qq: str | None = None,
) -> None:
    patches.set("_pick_onebot_bot", lambda: bot)
    patches.set("_resolve_boss_target_groups", lambda: list(target_groups))
    patches.set("_resolve_target_groups", lambda: list(target_groups))
    patches.set("_resolve_chat_target_groups", lambda: list(target_groups))
    patches.set("_resolve_user_id_by_name", lambda _name: bound_qq)
    fake_driver = type("Driver", (), {"config": config})()
    patches.set("nonebot", _FakeNonebot(fake_driver))


class _FakeNonebot:
    """替换 pe.nonebot，仅需 get_driver().config。"""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def get_driver(self) -> Any:
        return self._driver


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _parse(response: Any) -> tuple[int, dict[str, Any]]:
    status = response.status_code
    payload = json.loads(bytes(response.body))
    return status, payload


def _call(request: _FakeRequest) -> tuple[int, dict[str, Any]]:
    """跑 handler（_FakeRequest 鸭子替身 cast 成 Request）并解析响应。"""
    response = _run(pe.webui_player_events_create(cast("Request", request)))
    return _parse(response)


# ── boss_summon 成功路径 ───────────────────────────────────────


def test_boss_summon_sends_rendered_default_template() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(
            patches, bot=bot, target_groups=[111, 222], config=config
        )
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                "boss": "克苏鲁之眼",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        data = payload["data"]
        assert data["sent_groups"] == [111, 222]
        assert data["failed_groups"] == []
        assert data["summary"] == {"total": 2, "success": 2, "failed": 0}
        # 默认模板渲染含 {boss}
        for call in bot.calls:
            assert call["api"] == "send_group_msg"
            assert call["message"] == "[生存服]Alice 召唤了 克苏鲁之眼"
    finally:
        patches.restore()


def test_boss_summon_binds_qq_in_player_placeholder() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(
            patches,
            bot=bot,
            target_groups=[111],
            config=config,
            bound_qq="10001",
        )
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                "boss": "世界吞噬者",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        # 命中 QQ 绑定 → {player} = name（QQ），与上下线一致
        assert bot.calls[0]["message"] == "[生存服]Alice（10001） 召唤了 世界吞噬者"
    finally:
        patches.restore()


def test_boss_summon_uses_custom_template() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(
            boss_notify_mode="all",
            boss_notify_group_id="",
            boss_notify_template="⚔️ {player} 在 {server} 召唤了 {boss}！",
        )
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Bob",
                "server_name": "PVP",
                "event": "boss_summon",
                "boss": "血肉之墙",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        assert bot.calls[0]["message"] == "⚔️ Bob 在 PVP 召唤了 血肉之墙！"
    finally:
        patches.restore()


# ── boss 校验失败 ──────────────────────────────────────────────


def test_boss_summon_missing_boss_returns_422() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                # 缺 boss
            }
        )
        status, payload = _call(request)
        assert status == 422, payload
        assert payload["error"]["code"] == "validation_error"
        assert payload["error"]["details"][0]["field"] == "boss"
        # 校验在下发前，bot 不应被调用
        assert bot.calls == []
    finally:
        patches.restore()


def test_boss_summon_blank_boss_returns_422() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                "boss": "   ",  # strip 后为空
            }
        )
        status, payload = _call(request)
        assert status == 422, payload
        assert payload["error"]["details"][0]["field"] == "boss"
        assert bot.calls == []
    finally:
        patches.restore()


def test_boss_summon_forbidden_chars_returns_422() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        # 含控制字符（\x00）→ 非法
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                "boss": "克苏鲁\x00之眼",
            }
        )
        status, payload = _call(request)
        assert status == 422, payload
        assert payload["error"]["details"][0]["field"] == "boss"
        assert payload["error"]["message"] == "Boss 名称包含非法字符"
        assert bot.calls == []
    finally:
        patches.restore()


def test_boss_summon_too_long_returns_422() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "boss_summon",
                "boss": "x" * (pe._BOSS_NAME_MAX_LENGTH + 1),
            }
        )
        status, payload = _call(request)
        assert status == 422, payload
        assert payload["error"]["details"][0]["field"] == "boss"
        assert bot.calls == []
    finally:
        patches.restore()


# ── 注入防护 ───────────────────────────────────────────────────


def test_boss_summon_injection_braces_not_resubstituted() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        # boss 含 {player}：若被二次替换会泄漏玩家名；正确行为是转全角不替换。
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "S",
                "event": "boss_summon",
                "boss": "{player}{server}",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        message = bot.calls[0]["message"]
        # boss 里的 {player}/{server} 必须被转成全角，未被二次替换。
        # 期望值从 chr() 构造，避免源码出现歧义全角字面量。
        fw_open, fw_close = chr(0xFF5B), chr(0xFF5D)
        escaped = f"{fw_open}player{fw_close}{fw_open}server{fw_close}"
        assert escaped in message
        assert message == f"[S]Alice 召唤了 {escaped}"
    finally:
        patches.restore()


# ── 配置 / 连接错误 ────────────────────────────────────────────


def test_boss_summon_no_groups_returns_503() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(boss_notify_mode="single", boss_notify_group_id="")
        _install_common(patches, bot=bot, target_groups=[], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "S",
                "event": "boss_summon",
                "boss": "克苏鲁之眼",
            }
        )
        status, payload = _call(request)
        assert status == 503, payload
        assert payload["error"]["code"] == "service_misconfigured"
        assert bot.calls == []
    finally:
        patches.restore()


def test_boss_summon_bot_unavailable_returns_503() -> None:
    patches = _Patches()
    try:
        config = _FakeConfig(boss_notify_mode="all", boss_notify_group_id="")
        _install_common(patches, bot=None, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "S",
                "event": "boss_summon",
                "boss": "克苏鲁之眼",
            }
        )
        status, payload = _call(request)
        assert status == 503, payload
        assert payload["error"]["code"] == "bot_unavailable"
    finally:
        patches.restore()


# ── 既有事件不回归 ─────────────────────────────────────────────


def test_online_event_not_regressed() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(
            player_notify_online_template="",  # 用默认
            boss_notify_template="不应被 online 使用",
        )
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "online",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        # online 仍走默认上线模板，未沾染 boss 逻辑
        assert bot.calls[0]["message"] == "[生存服]Alice 上线了"
    finally:
        patches.restore()


def test_message_event_not_regressed() -> None:
    patches = _Patches()
    try:
        bot = _FakeBot()
        config = _FakeConfig(chat_sync_template="")  # 用默认
        _install_common(patches, bot=bot, target_groups=[111], config=config)
        request = _FakeRequest(
            {
                "player_name": "Alice",
                "server_name": "生存服",
                "event": "message",
                "message": "hello",
            }
        )
        status, payload = _call(request)
        assert status == 200, payload
        assert bot.calls[0]["message"] == "[生存服]Alice：hello"
    finally:
        patches.restore()


# ── _render_template 单测：{boss} 渲染 + 注入 ──────────────────


def test_render_template_boss_placeholder() -> None:
    text = pe._render_template(
        "[{server}]{player} 召唤了 {boss}",
        display_name="Alice",
        server_name="S",
        message_text="",
        boss="克苏鲁之眼",
    )
    assert text == "[S]Alice 召唤了 克苏鲁之眼"


def test_render_template_boss_braces_stripped() -> None:
    # boss 含 { } → 转全角，杜绝二次替换。
    text = pe._render_template(
        "{boss}",
        display_name="Alice",
        server_name="S",
        message_text="",
        boss="{player}",
    )
    fw_open, fw_close = chr(0xFF5B), chr(0xFF5D)
    assert text == f"{fw_open}player{fw_close}"


def test_render_template_boss_defaults_empty_for_other_events() -> None:
    # 不传 boss（其它事件路径）→ {boss} 仍是字面占位符不会误替换玩家名。
    text = pe._render_template(
        "[{server}]{player} 上线了",
        display_name="Alice",
        server_name="S",
        message_text="",
    )
    assert text == "[S]Alice 上线了"


def _run_all() -> int:
    tests = [
        test_boss_summon_sends_rendered_default_template,
        test_boss_summon_binds_qq_in_player_placeholder,
        test_boss_summon_uses_custom_template,
        test_boss_summon_missing_boss_returns_422,
        test_boss_summon_blank_boss_returns_422,
        test_boss_summon_forbidden_chars_returns_422,
        test_boss_summon_too_long_returns_422,
        test_boss_summon_injection_braces_not_resubstituted,
        test_boss_summon_no_groups_returns_503,
        test_boss_summon_bot_unavailable_returns_503,
        test_online_event_not_regressed,
        test_message_event_not_regressed,
        test_render_template_boss_placeholder,
        test_render_template_boss_braces_stripped,
        test_render_template_boss_defaults_empty_for_other_events,
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
    raise SystemExit(_run_all())
