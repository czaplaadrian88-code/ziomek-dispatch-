"""Daily Accounting — date/bucket logic.

Mon → weekend bucket (Fri..Sun, target_date = Sun).
Tue-Fri → yesterday only (target_date = yesterday).
Sat/Sun → None (caller exit(0)).
All timestamps via zoneinfo.ZoneInfo('Europe/Warsaw').
"""
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


def today_warsaw() -> date:
    return datetime.now(WARSAW).date()


def compute_bucket(today: date) -> Optional[Tuple[date, date, date]]:
    """Return (date_from, date_to, target_date_for_C). None = exit (Sat/Sun)."""
    weekday = today.weekday()  # 0=Mon, 6=Sun
    if weekday == 0:  # Monday → weekend bucket
        date_from = today - timedelta(days=3)  # Fri
        date_to = today - timedelta(days=1)    # Sun
        return date_from, date_to, date_to
    if weekday in (1, 2, 3, 4):  # Tue-Fri → yesterday
        date_from = today - timedelta(days=1)
        return date_from, date_from, date_from
    return None  # Sat/Sun
