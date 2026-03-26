from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"


def db_now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_today() -> date:
    return beijing_now().date()


def beijing_today_text() -> str:
    return beijing_today().isoformat()


def utc_naive_to_beijing(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)


def format_beijing_datetime(value: datetime | None) -> str:
    converted = utc_naive_to_beijing(value)
    if converted is None:
        return ""
    return converted.strftime(_DATETIME_FORMAT)


def beijing_now_text() -> str:
    return beijing_now().strftime(_DATETIME_FORMAT)


def beijing_filename_timestamp() -> str:
    return beijing_now().strftime(_FILENAME_TIMESTAMP_FORMAT)


def format_online_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m} 分 {s} 秒" if s else f"{m} 分钟"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = [f"{h} 小时"]
    if m:
        parts.append(f"{m} 分")
    if s:
        parts.append(f"{s} 秒")
    return " ".join(parts)


def seconds_until_next_beijing_midnight() -> float:
    now = beijing_now()
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1),
        datetime_time.min,
        tzinfo=BEIJING_TZ,
    )
    return max((next_midnight - now).total_seconds(), 1.0)
