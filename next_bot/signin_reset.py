from __future__ import annotations

import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta

from nonebot.log import logger

from next_bot.db import User, get_session

_worker_started = False
_worker_lock = threading.Lock()


def _reset_signed_today(*, reset_all: bool) -> int:
    session = get_session()
    try:
        query = session.query(User).filter(User.signed_today.is_(True))
        if not reset_all:
            today_text = date.today().isoformat()
            query = query.filter(User.last_sign_date != today_text)
        changed = query.update({User.signed_today: False}, synchronize_session=False)
        session.commit()
        return int(changed or 0)
    finally:
        session.close()


def _seconds_until_next_midnight() -> float:
    now = datetime.now()
    next_midnight = datetime.combine(
        (now + timedelta(days=1)).date(),
        datetime_time.min,
    )
    return max((next_midnight - now).total_seconds(), 1.0)


def _signin_reset_worker() -> None:
    while True:
        time.sleep(_seconds_until_next_midnight())
        try:
            changed = _reset_signed_today(reset_all=True)
            logger.info(f"每日签到重置完成：reset_count={changed}")
        except Exception:
            logger.exception("每日签到重置失败")


def start_signin_reset_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return

        try:
            changed = _reset_signed_today(reset_all=False)
            logger.info(f"签到状态启动检查完成：reset_count={changed}")
        except Exception:
            logger.exception("签到状态启动检查失败")

        thread = threading.Thread(
            target=_signin_reset_worker,
            name="nextbot-signin-reset",
            daemon=True,
        )
        thread.start()
        _worker_started = True
