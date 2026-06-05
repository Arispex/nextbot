"""数据库自动备份设置项（settings_service 的 db_backup_*）测试。

依赖轻量：不连网、不依赖 pytest-only fixture。可在 pytest 下运行
（``uv run pytest tests/test_db_backup_settings.py``）或作为脚本直接运行
（``uv run python tests/test_db_backup_settings.py``）。

覆盖：
  - db_backup_enabled normalize：bool 真假值、字符串归一、非法报错。
  - db_backup_interval_hours / db_backup_retention：int 范围、越界报错、非法报错。
  - 默认值：snapshot / config 在 .env 与 config 均缺省时给出 True / 24 / 30。
  - save → snapshot round-trip：写入 .env 后读回一致（含 int 类型保持）。
  - 注册完整性：三项字段在 _FIELD_SPECS / metadata；两个 int + bool 不在单行字段集。
  - 既有 group_auto_ban_* / web_server_port 默认不回归。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# allow `python tests/test_db_backup_settings.py` from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot


def _ensure_nonebot() -> None:
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()


_ensure_nonebot()

import server.settings_service as ss

# ── normalize: bool ────────────────────────────────────────────


def test_enabled_accepts_bool() -> None:
    true_value: bool = True
    false_value: bool = False
    assert ss._normalize_field("db_backup_enabled", true_value) is True
    assert ss._normalize_field("db_backup_enabled", false_value) is False


def test_enabled_normalizes_strings() -> None:
    assert ss._normalize_field("db_backup_enabled", "true") is True
    assert ss._normalize_field("db_backup_enabled", "1") is True
    assert ss._normalize_field("db_backup_enabled", "false") is False
    assert ss._normalize_field("db_backup_enabled", "0") is False


def test_enabled_invalid_raises() -> None:
    raised = False
    try:
        ss._normalize_field("db_backup_enabled", "maybe")
    except ss.SettingsValidationError as exc:
        raised = True
        assert exc.field == "db_backup_enabled"
    assert raised, "非法 bool 应抛 SettingsValidationError"


# ── normalize: interval_hours (int 1-8760) ─────────────────────


def test_interval_keeps_in_range() -> None:
    assert ss._normalize_field("db_backup_interval_hours", 1) == 1
    assert ss._normalize_field("db_backup_interval_hours", 24) == 24
    assert ss._normalize_field("db_backup_interval_hours", 8760) == 8760
    # 字符串数字也接受（.env round-trip 场景）。
    assert ss._normalize_field("db_backup_interval_hours", "12") == 12


def test_interval_out_of_range_raises() -> None:
    for bad in (0, -1, 8761, 100000):
        raised = False
        try:
            ss._normalize_field("db_backup_interval_hours", bad)
        except ss.SettingsValidationError as exc:
            raised = True
            assert exc.field == "db_backup_interval_hours"
        assert raised, f"越界值 {bad} 应抛 SettingsValidationError"


def test_interval_non_int_raises() -> None:
    # 非数字字符串报错；bool 被显式拦截（不当整数）。
    # 注意：float 如 1.5 会被 int() 截断为 1（与既有 _coerce_port 行为一致），
    # 故不在此断言 float 报错。
    for bad in ("abc", True):
        raised = False
        try:
            ss._normalize_field("db_backup_interval_hours", bad)
        except ss.SettingsValidationError as exc:
            raised = True
            assert exc.field == "db_backup_interval_hours"
        assert raised, f"非整数 {bad!r} 应抛 SettingsValidationError"


# ── normalize: retention (int 1-1000) ──────────────────────────


def test_retention_keeps_in_range() -> None:
    assert ss._normalize_field("db_backup_retention", 1) == 1
    assert ss._normalize_field("db_backup_retention", 30) == 30
    assert ss._normalize_field("db_backup_retention", 1000) == 1000


def test_retention_out_of_range_raises() -> None:
    for bad in (0, -5, 1001):
        raised = False
        try:
            ss._normalize_field("db_backup_retention", bad)
        except ss.SettingsValidationError as exc:
            raised = True
            assert exc.field == "db_backup_retention"
        assert raised, f"越界值 {bad} 应抛 SettingsValidationError"


# ── 默认值 ─────────────────────────────────────────────────────


def test_defaults_from_config() -> None:
    class _EmptyConfig:
        pass

    config = _EmptyConfig()
    assert ss._load_value_from_config("db_backup_enabled", config) is True
    assert ss._load_value_from_config("db_backup_interval_hours", config) == 24
    assert ss._load_value_from_config("db_backup_retention", config) == 30


def test_snapshot_includes_defaults_when_env_missing(_tmp_env: Any) -> None:
    snapshot = ss.get_settings_snapshot()
    assert snapshot["db_backup_enabled"] is True
    assert snapshot["db_backup_interval_hours"] == 24
    assert snapshot["db_backup_retention"] == 30


# ── round-trip ─────────────────────────────────────────────────


def test_save_then_snapshot_round_trip(_tmp_env: Any) -> None:
    result = ss.save_settings(
        {
            "db_backup_enabled": False,
            "db_backup_interval_hours": 6,
            "db_backup_retention": 10,
        }
    )
    assert "db_backup_enabled" in result.saved_fields
    assert "db_backup_interval_hours" in result.saved_fields
    assert "db_backup_retention" in result.saved_fields

    snapshot = ss.get_settings_snapshot()
    assert snapshot["db_backup_enabled"] is False
    # int 类型在 round-trip 后保持为 int（不是字符串）。
    assert snapshot["db_backup_interval_hours"] == 6
    assert isinstance(snapshot["db_backup_interval_hours"], int)
    assert snapshot["db_backup_retention"] == 10
    assert isinstance(snapshot["db_backup_retention"], int)


def test_save_out_of_range_rejected(_tmp_env: Any) -> None:
    raised = False
    try:
        ss.save_settings({"db_backup_retention": 99999})
    except ss.SettingsValidationError as exc:
        raised = True
        assert exc.field == "db_backup_retention"
    assert raised, "越界 retention 应在 save 阶段被拒"


# ── 注册完整性 ─────────────────────────────────────────────────


def test_fields_registered() -> None:
    field_names = {spec.field for spec in ss._FIELD_SPECS}
    assert {
        "db_backup_enabled",
        "db_backup_interval_hours",
        "db_backup_retention",
    } <= field_names
    assert ss._FIELD_BY_NAME["db_backup_enabled"].env_key == "DB_BACKUP_ENABLED"
    assert (
        ss._FIELD_BY_NAME["db_backup_interval_hours"].env_key
        == "DB_BACKUP_INTERVAL_HOURS"
    )
    assert ss._FIELD_BY_NAME["db_backup_retention"].env_key == "DB_BACKUP_RETENTION"


def test_fields_not_single_line() -> None:
    # int / bool 字段不进单行字符串白名单（仿 web_server_port）。
    assert "db_backup_enabled" not in ss._SINGLE_LINE_STRING_FIELDS
    assert "db_backup_interval_hours" not in ss._SINGLE_LINE_STRING_FIELDS
    assert "db_backup_retention" not in ss._SINGLE_LINE_STRING_FIELDS


def test_metadata_lists_fields() -> None:
    metadata = ss.get_settings_metadata()
    managed = set(metadata["managed_fields"])
    assert {
        "db_backup_enabled",
        "db_backup_interval_hours",
        "db_backup_retention",
    } <= managed


# ── 既有字段不回归 ─────────────────────────────────────────────


def test_existing_defaults_not_regressed() -> None:
    class _EmptyConfig:
        pass

    config = _EmptyConfig()
    assert ss._load_value_from_config("web_server_port", config) == 18081
    assert (
        ss._load_value_from_config("group_auto_ban_on_leave_enabled", config) is False
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
        self._tmp.write_text("", encoding="utf-8")
        ss._ENV_PATH = self._tmp
        return self

    def close(self) -> None:
        if self._saved is not None:
            ss._ENV_PATH = self._saved
        if self._tmp is not None and self._tmp.exists():
            self._tmp.unlink()
        if self._tmp is not None:
            sidecar = self._tmp.with_suffix(".env.tmp")
            if sidecar.exists():
                sidecar.unlink()


def _run_all() -> int:
    plain_tests = [
        test_enabled_accepts_bool,
        test_enabled_normalizes_strings,
        test_enabled_invalid_raises,
        test_interval_keeps_in_range,
        test_interval_out_of_range_raises,
        test_interval_non_int_raises,
        test_retention_keeps_in_range,
        test_retention_out_of_range_raises,
        test_defaults_from_config,
        test_fields_registered,
        test_fields_not_single_line,
        test_metadata_lists_fields,
        test_existing_defaults_not_regressed,
    ]
    env_tests = [
        test_snapshot_includes_defaults_when_env_missing,
        test_save_then_snapshot_round_trip,
        test_save_out_of_range_rejected,
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
