"""Boss 召唤通知设置项（settings_service 的 boss_notify_*）测试。

依赖轻量：不连网、不依赖 pytest-only fixture。可在 pytest 下运行
（``uv run pytest tests/test_boss_notify_settings.py``）或作为脚本直接运行
（``uv run python tests/test_boss_notify_settings.py``）。

覆盖：
  - boss_notify_mode normalize：all/single 保留、非法 → all、大小写归一。
  - boss_notify_group_id / boss_notify_template allow_empty 字符串。
  - 默认值：snapshot 在 .env 与 config 均缺省时给出三项默认。
  - save → snapshot round-trip：写入 .env 后读回一致。
  - 注册完整性：三项字段在 _FIELD_SPECS / _SINGLE_LINE_STRING_FIELDS / metadata。
  - 既有 player_notify_* 默认不回归。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# allow `python tests/test_boss_notify_settings.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

import server.settings_service as ss

_DEFAULT_BOSS_TEMPLATE = "[{server}]{player} 召唤了 {boss}"


# ── normalize ──────────────────────────────────────────────────


def test_boss_notify_mode_keeps_valid() -> None:
    assert ss._normalize_field("boss_notify_mode", "all") == "all"
    assert ss._normalize_field("boss_notify_mode", "single") == "single"


def test_boss_notify_mode_normalizes_case() -> None:
    assert ss._normalize_field("boss_notify_mode", "ALL") == "all"
    assert ss._normalize_field("boss_notify_mode", "Single") == "single"


def test_boss_notify_mode_invalid_falls_back_to_all() -> None:
    # 非法值不报错，回落 all（与 player_notify_mode 一致）。
    assert ss._normalize_field("boss_notify_mode", "broadcast") == "all"
    assert ss._normalize_field("boss_notify_mode", "") == "all"


def test_boss_notify_group_id_allows_empty() -> None:
    assert ss._normalize_field("boss_notify_group_id", "") == ""
    assert ss._normalize_field("boss_notify_group_id", "  12345  ") == "12345"


def test_boss_notify_template_allows_empty() -> None:
    assert ss._normalize_field("boss_notify_template", "") == ""
    assert (
        ss._normalize_field("boss_notify_template", _DEFAULT_BOSS_TEMPLATE)
        == _DEFAULT_BOSS_TEMPLATE
    )


# ── 默认值 ─────────────────────────────────────────────────────


def test_boss_notify_defaults_from_config() -> None:
    class _EmptyConfig:
        pass

    config = _EmptyConfig()
    assert ss._load_value_from_config("boss_notify_mode", config) == "all"
    assert ss._load_value_from_config("boss_notify_group_id", config) == ""
    assert (
        ss._load_value_from_config("boss_notify_template", config)
        == _DEFAULT_BOSS_TEMPLATE
    )


def test_snapshot_includes_boss_defaults_when_env_missing(_tmp_env: Any) -> None:
    snapshot = ss.get_settings_snapshot()
    assert snapshot["boss_notify_mode"] == "all"
    assert snapshot["boss_notify_group_id"] == ""
    assert snapshot["boss_notify_template"] == _DEFAULT_BOSS_TEMPLATE


# ── round-trip ─────────────────────────────────────────────────


def test_save_then_snapshot_round_trip(_tmp_env: Any) -> None:
    result = ss.save_settings(
        {
            "boss_notify_mode": "single",
            "boss_notify_group_id": "20001",
            "boss_notify_template": "{player} 召唤了 {boss}（{server}）",
        }
    )
    assert "boss_notify_mode" in result.saved_fields
    assert "boss_notify_group_id" in result.saved_fields
    assert "boss_notify_template" in result.saved_fields

    snapshot = ss.get_settings_snapshot()
    assert snapshot["boss_notify_mode"] == "single"
    assert snapshot["boss_notify_group_id"] == "20001"
    assert snapshot["boss_notify_template"] == "{player} 召唤了 {boss}（{server}）"


def test_save_invalid_mode_persists_as_all(_tmp_env: Any) -> None:
    ss.save_settings({"boss_notify_mode": "nope"})
    snapshot = ss.get_settings_snapshot()
    assert snapshot["boss_notify_mode"] == "all"


# ── 注册完整性 ─────────────────────────────────────────────────


def test_boss_fields_registered() -> None:
    field_names = {spec.field for spec in ss._FIELD_SPECS}
    assert {
        "boss_notify_mode",
        "boss_notify_group_id",
        "boss_notify_template",
    } <= field_names
    # env_key 映射存在
    assert ss._FIELD_BY_NAME["boss_notify_mode"].env_key == "BOSS_NOTIFY_MODE"
    assert ss._FIELD_BY_NAME["boss_notify_group_id"].env_key == "BOSS_NOTIFY_GROUP_ID"
    assert ss._FIELD_BY_NAME["boss_notify_template"].env_key == "BOSS_NOTIFY_TEMPLATE"


def test_boss_fields_are_single_line() -> None:
    assert "boss_notify_mode" in ss._SINGLE_LINE_STRING_FIELDS
    assert "boss_notify_group_id" in ss._SINGLE_LINE_STRING_FIELDS
    assert "boss_notify_template" in ss._SINGLE_LINE_STRING_FIELDS


def test_boss_template_rejects_newline(_tmp_env: Any) -> None:
    # 单行字段：含换行应被拒绝（_assert_single_line_string）。
    raised = False
    try:
        ss.save_settings({"boss_notify_template": "line1\nline2"})
    except ss.SettingsValidationError as exc:
        raised = True
        assert exc.field == "boss_notify_template"
    assert raised, "含换行的单行模板应抛 SettingsValidationError"


def test_metadata_lists_boss_fields() -> None:
    metadata = ss.get_settings_metadata()
    managed = set(metadata["managed_fields"])
    assert {
        "boss_notify_mode",
        "boss_notify_group_id",
        "boss_notify_template",
    } <= managed


# ── 既有字段不回归 ─────────────────────────────────────────────


def test_player_notify_defaults_not_regressed() -> None:
    class _EmptyConfig:
        pass

    config = _EmptyConfig()
    assert ss._load_value_from_config("player_notify_mode", config) == "all"
    assert (
        ss._load_value_from_config("player_notify_online_template", config)
        == "[{server}]{player} 上线了"
    )
    assert (
        ss._load_value_from_config("player_notify_offline_template", config)
        == "[{server}]{player} 下线了"
    )


# ── 测试夹具（脚本式手动管理临时 .env）──────────────────────────


class _TmpEnv:
    """把 ss._ENV_PATH 指向临时空文件，保证 round-trip 隔离不污染真实 .env。"""

    def __init__(self) -> None:
        self._saved: Path | None = None
        self._tmp: Path | None = None

    def open(self) -> "_TmpEnv":
        import os
        import tempfile

        self._saved = ss._ENV_PATH
        fd, name = tempfile.mkstemp(suffix=".env")
        os.close(fd)
        self._tmp = Path(name)
        # 起始为空文件（snapshot 应回落 config 默认）
        self._tmp.write_text("", encoding="utf-8")
        ss._ENV_PATH = self._tmp
        return self

    def close(self) -> None:
        if self._saved is not None:
            ss._ENV_PATH = self._saved
        if self._tmp is not None and self._tmp.exists():
            self._tmp.unlink()
        # 清理 _write_env_values 可能产生的 .env.tmp
        if self._tmp is not None:
            sidecar = self._tmp.with_suffix(".env.tmp")
            if sidecar.exists():
                sidecar.unlink()


def _run_all() -> int:
    # 无参测试 + 需要临时 .env 的测试分别跑（脚本式无 pytest fixture 注入）。
    plain_tests = [
        test_boss_notify_mode_keeps_valid,
        test_boss_notify_mode_normalizes_case,
        test_boss_notify_mode_invalid_falls_back_to_all,
        test_boss_notify_group_id_allows_empty,
        test_boss_notify_template_allows_empty,
        test_boss_notify_defaults_from_config,
        test_boss_fields_registered,
        test_boss_fields_are_single_line,
        test_metadata_lists_boss_fields,
        test_player_notify_defaults_not_regressed,
    ]
    env_tests = [
        test_snapshot_includes_boss_defaults_when_env_missing,
        test_save_then_snapshot_round_trip,
        test_save_invalid_mode_persists_as_all,
        test_boss_template_rejects_newline,
    ]
    failed = 0
    total = len(plain_tests) + len(env_tests)
    for t in plain_tests:
        try:
            t()
        except AssertionError as exc:  # noqa: PERF203 - tiny test loop
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    for t in env_tests:
        env = _TmpEnv().open()
        try:
            t(env)
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
        finally:
            env.close()
    print(f"{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
