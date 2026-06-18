"""End-of-day salvage (#2, 2026-06-18): w ostatniej godzinie pracy FIRMY (23:00, pt/sb 24:00)
zluzuj twarde reguły końca zmiany dla (zwykle jedynego) kuriera — warunek twardy: ODBIÓR ≤
koniec pracy firmy. Testy: company_close (dni tygodnia), okno salvage, gate flagą OFF=brak zmian.
Flaga ENABLE_END_OF_DAY_SALVAGE default OFF (decision_flag); ON sterujemy patchem."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import dispatch_v2.common as C
import dispatch_v2.feasibility_v2 as F

_W = ZoneInfo("Europe/Warsaw")


def _warsaw(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=_W).astimezone(timezone.utc)


# --- company close ---

def test_company_close_weekday_2300():
    # czwartek 2026-06-18, 22:30 Warsaw → close 23:00 Warsaw
    now = _warsaw(2026, 6, 18, 22, 30)
    close = F._company_close_utc(now)
    assert close.astimezone(_W).hour == 23 and close.astimezone(_W).minute == 0
    assert close.astimezone(_W).date() == datetime(2026, 6, 18).date()


def test_company_close_friday_midnight():
    # piątek 2026-06-19, 23:30 Warsaw → close 24:00 = sobota 00:00 Warsaw
    now = _warsaw(2026, 6, 19, 23, 30)
    close = F._company_close_utc(now)
    loc = close.astimezone(_W)
    assert loc.hour == 0 and loc.minute == 0
    assert loc.date() == datetime(2026, 6, 20).date()   # sobota


def test_company_close_saturday_midnight():
    now = _warsaw(2026, 6, 20, 23, 0)   # sobota
    loc = F._company_close_utc(now).astimezone(_W)
    assert loc.hour == 0 and loc.date() == datetime(2026, 6, 21).date()


# --- okno salvage + flaga ---

def test_salvage_inactive_when_flag_off(monkeypatch):
    monkeypatch.setattr(C, "decision_flag", lambda n: False)
    now = _warsaw(2026, 6, 18, 22, 30)   # w oknie, ale flaga OFF
    assert F._end_of_day_salvage(now) == (False, None)


def test_salvage_active_in_last_hour(monkeypatch):
    monkeypatch.setattr(C, "decision_flag",
                        lambda n: n == "ENABLE_END_OF_DAY_SALVAGE")
    now = _warsaw(2026, 6, 18, 22, 30)   # 30 min przed 23:00
    active, close = F._end_of_day_salvage(now)
    assert active is True and close is not None
    assert close.astimezone(_W).hour == 23


def test_salvage_inactive_before_last_hour(monkeypatch):
    monkeypatch.setattr(C, "decision_flag",
                        lambda n: n == "ENABLE_END_OF_DAY_SALVAGE")
    now = _warsaw(2026, 6, 18, 21, 30)   # 90 min przed close → poza oknem
    active, _ = F._end_of_day_salvage(now)
    assert active is False


def test_salvage_inactive_after_close(monkeypatch):
    monkeypatch.setattr(C, "decision_flag",
                        lambda n: n == "ENABLE_END_OF_DAY_SALVAGE")
    now = _warsaw(2026, 6, 18, 23, 10)   # po close
    active, _ = F._end_of_day_salvage(now)
    assert active is False
