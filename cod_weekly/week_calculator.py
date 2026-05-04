"""Kalkulator tygodnia kalendarzowego pon-niedz w Warsaw TZ."""
from datetime import date, timedelta, datetime
from typing import Optional, Tuple

from dispatch_v2.cod_weekly.config import WARSAW


def get_previous_closed_week(now: Optional[datetime] = None) -> Tuple[date, date]:
    """(pon, niedz) poprzedniego w pełni zamkniętego tygodnia w Warsaw TZ.

    Dla dowolnego dnia X tygodnia N zwraca pon/niedz tygodnia N-1.
    """
    if now is None:
        now = datetime.now(WARSAW)
    today = now.date()
    this_week_mon = today - timedelta(days=today.weekday())
    last_week_mon = this_week_mon - timedelta(days=7)
    last_week_sun = last_week_mon + timedelta(days=6)
    return last_week_mon, last_week_sun


def get_current_week_ending_sunday(now: Optional[datetime] = None) -> Tuple[date, date]:
    """(pon, niedz) tygodnia BIEŻĄCEGO w Warsaw TZ — używane przez --preflight.

    Inaczej niż get_previous_closed_week: zwraca tydzień, który zaraz będzie
    rozliczony (cron preflight w niedzielę 23:00 chce sprawdzić arkusz
    pod kątem nadchodzącego pn 08:00 odpalenia, a NIE poprzedniego tygodnia).
    """
    if now is None:
        now = datetime.now(WARSAW)
    today = now.date()
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)
    return mon, sun


def format_week_for_header(start: date, end: date) -> str:
    """'06-12.04.2026' gdy jeden miesiąc, inaczej '30.03-05.04.2026'."""
    if start.month == end.month:
        return f"{start.day:02d}-{end.day:02d}.{end.month:02d}.{end.year}"
    return (
        f"{start.day:02d}.{start.month:02d}-"
        f"{end.day:02d}.{end.month:02d}.{end.year}"
    )


def parse_override(arg: str) -> Tuple[date, date]:
    """'2026-04-13:2026-04-19' → (start, end). Waliduje 7 dni pon-niedz."""
    try:
        a, b = arg.split(":")
    except ValueError:
        raise ValueError(f"--week '{arg}': oczekiwany format 'YYYY-MM-DD:YYYY-MM-DD'")
    start = date.fromisoformat(a.strip())
    end = date.fromisoformat(b.strip())
    if (end - start).days != 6:
        raise ValueError(f"--week {start}..{end}: nie 7 dni (delta={(end-start).days})")
    if start.weekday() != 0:
        raise ValueError(
            f"--week start {start} nie poniedziałek (weekday={start.weekday()})"
        )
    return start, end
