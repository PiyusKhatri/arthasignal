from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from src.pipeline.backfill_calendar import is_market_open_today

logger = logging.getLogger(__name__)

NPT_OFFSET = timezone(timedelta(hours=5, minutes=45))
SESSION_START = time(11, 0)
SESSION_END = time(15, 0)
DRIFT_BUFFER = timedelta(minutes=2)


def _current_npt_time() -> datetime:
    return datetime.now(timezone.utc).astimezone(NPT_OFFSET)


def _warn_if_near_boundary(now_npt: datetime) -> None:
    today_start = now_npt.replace(hour=SESSION_START.hour, minute=SESSION_START.minute, second=0, microsecond=0)
    today_end = now_npt.replace(hour=SESSION_END.hour, minute=SESSION_END.minute, second=0, microsecond=0)

    if abs(now_npt - today_start) <= DRIFT_BUFFER:
        logger.warning(
            "Called within %d minutes of NEPSE session open (11:00 NPT) - current NPT time is %s, "
            "possible cron scheduling drift",
            DRIFT_BUFFER.seconds // 60,
            now_npt.strftime("%H:%M:%S"),
        )
    elif abs(now_npt - today_end) <= DRIFT_BUFFER:
        logger.warning(
            "Called within %d minutes of NEPSE session close (15:00 NPT) - current NPT time is %s, "
            "possible cron scheduling drift",
            DRIFT_BUFFER.seconds // 60,
            now_npt.strftime("%H:%M:%S"),
        )


def is_within_intraday_window() -> bool:
    now_npt = _current_npt_time()
    _warn_if_near_boundary(now_npt)

    if not is_market_open_today():
        return False

    return SESSION_START <= now_npt.time() <= SESSION_END


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    now_npt = _current_npt_time()
    trading_day = is_market_open_today()
    result = is_within_intraday_window()
    print(f"current NPT time: {now_npt.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"is_market_open_today(): {trading_day}")
    print(f"is_within_intraday_window(): {result}")
