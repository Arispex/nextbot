"""``server.routes.read_json_object`` 请求体大小上限测试。

依赖轻量：只 import ``server.routes``（仅依赖 fastapi），不连网、不碰数据库、
不需要 nonebot。可在 pytest 下运行（``uv run pytest
tests/test_read_json_object_size_limit.py``）或作为脚本直接运行
（``uv run python tests/test_read_json_object_size_limit.py``）。

覆盖：
  - 默认（``max_bytes=MAX_JSON_BODY_BYTES``）：Content-Length 预检超限 → 413；
    Content-Length 缺失时流式累加超限 → 413；未超限正常解析。
  - ``max_bytes=None``（商店 / 抽奖导入端点）：上述两种超限场景都放行。
  - 显式传入自定义 ``max_bytes`` 时两道校验都按该值判定，边界为 ``>`` 而非 ``>=``。
  - 解除字节上限后，Content-Type / JSON 解析 / dict 结构校验仍然生效。
  - 静态扫描 ``server/routes/*.py``：解除字节上限的端点恰好是 ``import_shops`` /
    ``import_lottery``，其余调用点一律不传 ``max_bytes``（守住 256 KiB 默认值）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# allow `python tests/test_read_json_object_size_limit.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.routes import MAX_JSON_BODY_BYTES, read_json_object

if TYPE_CHECKING:
    from fastapi.responses import JSONResponse

# ── Request 替身 ────────────────────────────────────────────────

_CHUNK_SIZE = 64 * 1024


class _FakeRequest:
    """最小 Request 替身：只暴露 read_json_object 用到的 headers + stream()。"""

    def __init__(
        self,
        body: bytes,
        *,
        content_type: str | None = "application/json",
        send_content_length: bool = True,
    ) -> None:
        self._body = body
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["content-type"] = content_type
        if send_content_length:
            headers["content-length"] = str(len(body))
        self.headers = headers

    async def stream(self) -> Any:
        # 分块 yield，模拟真实 ASGI receive；用于验证流式累加上限。
        for offset in range(0, len(self._body), _CHUNK_SIZE):
            yield self._body[offset:offset + _CHUNK_SIZE]


def _oversized_body() -> bytes:
    """构造一个明显超过 256 KiB 的合法 JSON body。"""
    blob = "a" * (MAX_JSON_BODY_BYTES + 1024)
    body = json.dumps({"blob": blob}).encode("utf-8")
    assert len(body) > MAX_JSON_BODY_BYTES
    return body


def _read(
    request: Any,
    **kwargs: Any,
) -> tuple[dict[str, Any] | None, "JSONResponse | None"]:
    return asyncio.run(read_json_object(request, **kwargs))


def _error_payload(response: "JSONResponse") -> dict[str, Any]:
    return json.loads(bytes(response.body))["error"]


# ── 默认行为：仍保持 256 KiB 上限 ───────────────────────────────


def test_default_rejects_oversized_content_length() -> None:
    payload, error = _read(_FakeRequest(_oversized_body()))
    assert payload is None
    assert error is not None
    assert error.status_code == 413
    assert _error_payload(error)["code"] == "payload_too_large"


def test_default_rejects_oversized_stream_without_content_length() -> None:
    request = _FakeRequest(_oversized_body(), send_content_length=False)
    payload, error = _read(request)
    assert payload is None
    assert error is not None
    assert error.status_code == 413
    assert _error_payload(error)["code"] == "payload_too_large"


def test_default_accepts_body_under_limit() -> None:
    body = json.dumps({"name": "small"}).encode("utf-8")
    payload, error = _read(_FakeRequest(body))
    assert error is None
    assert payload == {"name": "small"}


def test_custom_max_bytes_is_respected() -> None:
    """自定义上限在 Content-Length 预检（第一道）生效。"""
    body = json.dumps({"name": "small"}).encode("utf-8")
    payload, error = _read(_FakeRequest(body), max_bytes=8)
    assert payload is None
    assert error is not None
    assert error.status_code == 413


def test_custom_max_bytes_is_respected_on_stream() -> None:
    """自定义上限在流式累加（第二道）同样生效——Content-Length 缺失时不能漏判。"""
    body = json.dumps({"name": "small"}).encode("utf-8")
    request = _FakeRequest(body, send_content_length=False)
    payload, error = _read(request, max_bytes=8)
    assert payload is None
    assert error is not None
    assert error.status_code == 413
    assert _error_payload(error)["code"] == "payload_too_large"


def test_custom_max_bytes_accepts_body_at_limit() -> None:
    """恰好等于上限不算超限（边界是 ``>`` 而非 ``>=``）。"""
    body = json.dumps({"a": 1}).encode("utf-8")
    payload, error = _read(_FakeRequest(body), max_bytes=len(body))
    assert error is None
    assert payload == {"a": 1}


# ── max_bytes=None：商店 / 抽奖导入端点解除字节上限 ─────────────


def test_unlimited_accepts_oversized_content_length() -> None:
    body = _oversized_body()
    payload, error = _read(_FakeRequest(body), max_bytes=None)
    assert error is None
    assert payload is not None
    assert len(payload["blob"]) == MAX_JSON_BODY_BYTES + 1024


def test_unlimited_accepts_oversized_stream_without_content_length() -> None:
    request = _FakeRequest(_oversized_body(), send_content_length=False)
    payload, error = _read(request, max_bytes=None)
    assert error is None
    assert payload is not None
    assert len(payload["blob"]) == MAX_JSON_BODY_BYTES + 1024


# ── 解除字节上限后，其余校验不变 ───────────────────────────────


def test_unlimited_still_rejects_wrong_content_type() -> None:
    body = json.dumps({"name": "small"}).encode("utf-8")
    request = _FakeRequest(body, content_type="text/plain")
    payload, error = _read(request, max_bytes=None)
    assert payload is None
    assert error is not None
    assert error.status_code == 415
    assert _error_payload(error)["code"] == "unsupported_media_type"


def test_unlimited_still_rejects_invalid_json() -> None:
    payload, error = _read(_FakeRequest(b"{not json"), max_bytes=None)
    assert payload is None
    assert error is not None
    assert error.status_code == 400
    assert _error_payload(error)["code"] == "invalid_json"


def test_unlimited_still_rejects_non_object_payload() -> None:
    payload, error = _read(_FakeRequest(b"[1, 2, 3]"), max_bytes=None)
    assert payload is None
    assert error is not None
    assert error.status_code == 400
    assert _error_payload(error)["code"] == "invalid_request_body"


# ── 调用点白名单：只有两个导入端点可以解除字节上限 ─────────────

_ROUTES_DIR = Path(__file__).resolve().parent.parent / "server" / "routes"
# 唯一允许传 max_bytes=None 的两个端点；新增豁免必须同步改这里（防止误扩大攻击面）。
_UNLIMITED_CALLERS = {"import_shops", "import_lottery"}


def _collect_read_json_object_callers() -> dict[str, list[str]]:
    """静态扫描 ``server/routes/*.py``，返回 ``{函数名: [max_bytes 实参源码, ...]}``。

    纯 ``ast`` 解析，不 import 路由模块，因此不需要 nonebot / 数据库 / 网络。
    """
    callers: dict[str, list[str]] = {}
    for module_path in sorted(_ROUTES_DIR.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        scopes = [
            (n.lineno, n.end_lineno or n.lineno, n.name)
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "read_json_object":
                continue
            enclosing = min(
                (s for s in scopes if s[0] <= node.lineno <= s[1]),
                key=lambda s: s[1] - s[0],
                default=(0, 0, f"<module:{module_path.name}>"),
            )[2]
            given = [
                ast.unparse(kw.value) for kw in node.keywords if kw.arg == "max_bytes"
            ]
            callers.setdefault(enclosing, []).extend(given)
    return callers


def test_only_import_endpoints_lift_the_byte_cap() -> None:
    callers = _collect_read_json_object_callers()
    assert callers, "未扫描到任何 read_json_object 调用点，检查扫描路径"

    lifted = {fn for fn, args in callers.items() if "None" in args}
    assert lifted == _UNLIMITED_CALLERS, (
        f"解除字节上限的端点应恰好是 {sorted(_UNLIMITED_CALLERS)}，"
        f"实际为 {sorted(lifted)}"
    )


def test_all_other_callers_keep_the_default_cap() -> None:
    callers = _collect_read_json_object_callers()
    offenders = {
        fn: args
        for fn, args in callers.items()
        if fn not in _UNLIMITED_CALLERS and args
    }
    assert not offenders, f"其余调用点不应传 max_bytes，实际：{offenders}"


# ── 脚本入口 ────────────────────────────────────────────────────


def _run_all() -> int:
    tests = [
        test_default_rejects_oversized_content_length,
        test_default_rejects_oversized_stream_without_content_length,
        test_default_accepts_body_under_limit,
        test_custom_max_bytes_is_respected,
        test_custom_max_bytes_is_respected_on_stream,
        test_custom_max_bytes_accepts_body_at_limit,
        test_unlimited_accepts_oversized_content_length,
        test_unlimited_accepts_oversized_stream_without_content_length,
        test_unlimited_still_rejects_wrong_content_type,
        test_unlimited_still_rejects_invalid_json,
        test_unlimited_still_rejects_non_object_payload,
        test_only_import_endpoints_lift_the_byte_cap,
        test_all_other_callers_keep_the_default_cap,
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
