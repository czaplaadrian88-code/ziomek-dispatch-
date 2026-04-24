"""Tests — bucket_logic."""
from datetime import date

from dispatch_v2.daily_accounting.bucket_logic import compute_bucket


def test_monday_weekend_bucket():
    # 2026-04-27 = Monday
    today = date(2026, 4, 27)
    assert today.weekday() == 0
    out = compute_bucket(today)
    assert out is not None
    date_from, date_to, target = out
    assert date_from == date(2026, 4, 24)  # Fri
    assert date_to == date(2026, 4, 26)    # Sun
    assert target == date(2026, 4, 26)     # C = Sun


def test_tuesday_regular():
    # 2026-04-28 = Tuesday
    today = date(2026, 4, 28)
    assert today.weekday() == 1
    out = compute_bucket(today)
    assert out is not None
    date_from, date_to, target = out
    assert date_from == date(2026, 4, 27)
    assert date_to == date(2026, 4, 27)
    assert target == date(2026, 4, 27)


def test_friday_regular():
    # 2026-04-24 = Friday
    today = date(2026, 4, 24)
    assert today.weekday() == 4
    out = compute_bucket(today)
    assert out is not None
    date_from, date_to, target = out
    assert date_from == date(2026, 4, 23)
    assert date_to == date(2026, 4, 23)
    assert target == date(2026, 4, 23)


def test_saturday_exits():
    # 2026-04-25 = Saturday
    today = date(2026, 4, 25)
    assert today.weekday() == 5
    assert compute_bucket(today) is None


def test_sunday_exits():
    # 2026-04-26 = Sunday
    today = date(2026, 4, 26)
    assert today.weekday() == 6
    assert compute_bucket(today) is None
