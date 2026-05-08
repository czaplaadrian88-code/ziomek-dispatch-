"""MP-#5 (2026-05-08): event_bus retry transient locks + peak-aware cleanup.

Tests cover:
- _is_peak_window() Warsaw TZ logic (lunch 11-14, dinner 17-20)
- _retry_on_locked() recovers from sqlite3.OperationalError 'database is locked'
- _retry_on_locked() does NOT retry non-lock errors
- _retry_on_locked() exhausts retries i raises ostatni exception
- cleanup() returns 0 podczas peak window (no DB write)
- cleanup_audit_log() returns 0 podczas peak window
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus

WT = ZoneInfo("Europe/Warsaw")


# ─────────────────────────────────────────────
# _is_peak_window (8 tests)
# ─────────────────────────────────────────────

def test_peak_lunch_start_inclusive():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 11, 0, tzinfo=WT)) is True


def test_peak_lunch_mid():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 12, 30, tzinfo=WT)) is True


def test_peak_lunch_end_exclusive():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 14, 0, tzinfo=WT)) is False


def test_peak_dinner_start_inclusive():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 17, 0, tzinfo=WT)) is True


def test_peak_dinner_end_exclusive():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 20, 0, tzinfo=WT)) is False


def test_offpeak_morning():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 9, 0, tzinfo=WT)) is False


def test_offpeak_afternoon():
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 15, 30, tzinfo=WT)) is False


def test_offpeak_naive_dt_treated_as_warsaw():
    # naive datetime → WT replace
    assert event_bus._is_peak_window(datetime(2026, 5, 8, 11, 30)) is True


# ─────────────────────────────────────────────
# _retry_on_locked (4 tests)
# ─────────────────────────────────────────────

def test_retry_no_error_returns_immediately():
    calls = [0]
    def fn():
        calls[0] += 1
        return "ok"
    assert event_bus._retry_on_locked(fn) == "ok"
    assert calls[0] == 1


def test_retry_recovers_from_transient_lock():
    calls = [0]
    def fn():
        calls[0] += 1
        if calls[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"
    assert event_bus._retry_on_locked(fn) == "ok"
    assert calls[0] == 3


def test_retry_does_not_retry_non_lock_errors():
    calls = [0]
    def fn():
        calls[0] += 1
        raise sqlite3.OperationalError("syntax error")  # NOT lock
    with pytest.raises(sqlite3.OperationalError, match="syntax"):
        event_bus._retry_on_locked(fn)
    assert calls[0] == 1


def test_retry_exhausts_and_raises_last_exception():
    calls = [0]
    def fn():
        calls[0] += 1
        raise sqlite3.OperationalError(f"database is locked (attempt {calls[0]})")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        event_bus._retry_on_locked(fn)
    # 1 initial + 3 retries = 4 attempts
    assert calls[0] == 4


# ─────────────────────────────────────────────
# cleanup peak guard (2 tests)
# ─────────────────────────────────────────────

def test_cleanup_skips_during_peak():
    # mock _is_peak_window → True
    with patch.object(event_bus, "_is_peak_window", return_value=True):
        result = event_bus.cleanup(retention_hours=48)
    assert result == 0


def test_cleanup_audit_log_skips_during_peak():
    with patch.object(event_bus, "_is_peak_window", return_value=True):
        result = event_bus.cleanup_audit_log(retention_days=90)
    assert result == 0
