"""Pre-shift okno (cap 60 min) + kara gradientowa (Adrian 2026-06-24).

#3 z wymagań: do puli pre-shift wpuszczamy kuriera ≤ PRE_SHIFT_WINDOW_MAX_MIN przed
startem zmiany (grafik/V3.24-A); kara rośnie z minutami do startu — ≤30 lekka, 30-60
~veto poza dużym przeładowaniem floty (loadgov_ewma). Rygor „odbiór nie przed zmianą"
= departure-clamp (osobny mechanizm). Manualny working-override NIE jest capowany.
"""
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("ENABLE_WORKING_OVERRIDE", "1")
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import schedule_utils  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import manual_overrides as mo  # noqa: E402
from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2.courier_resolver import CourierState, dispatchable_fleet  # noqa: E402

WAW = ZoneInfo("Europe/Warsaw")


# ---------------- kara gradientowa (_pre_shift_gradient_penalty) ----------------

def test_penalty_zero_minutes_none():
    assert DP._pre_shift_gradient_penalty(0, None) is None
    assert DP._pre_shift_gradient_penalty(-5, 5.0) is None


def test_penalty_near_zone_linear():
    assert DP._pre_shift_gradient_penalty(10, None) == C.PRE_SHIFT_NEAR_PEN_PER_MIN * 10
    assert DP._pre_shift_gradient_penalty(C.PRE_SHIFT_NEAR_MIN, None) == \
        C.PRE_SHIFT_NEAR_PEN_PER_MIN * C.PRE_SHIFT_NEAR_MIN


def test_penalty_far_zone_veto_without_overload():
    m = C.PRE_SHIFT_NEAR_MIN + 15
    assert DP._pre_shift_gradient_penalty(m, None) == C.PRE_SHIFT_FAR_PEN
    assert DP._pre_shift_gradient_penalty(m, C.PRE_SHIFT_FAR_UNLOCK_LOAD - 0.1) == C.PRE_SHIFT_FAR_PEN


def test_penalty_far_zone_relaxed_under_overload():
    m = C.PRE_SHIFT_NEAR_MIN + 15
    # loadgov ≥ próg → relaks do gradientu (umiarkowana, nie veto)
    out = DP._pre_shift_gradient_penalty(m, C.PRE_SHIFT_FAR_UNLOCK_LOAD)
    assert out == C.PRE_SHIFT_NEAR_PEN_PER_MIN * m
    assert out > C.PRE_SHIFT_FAR_PEN  # mniej dotkliwa niż veto


# ---------------- okno cap w dispatchable_fleet (grafik / V3.24-A) ----------------

def _patch_grafik(monkeypatch, schedule):
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)
    monkeypatch.setattr(schedule_utils, "is_on_shift", lambda name, sch: (False, "przed zmianą"))
    monkeypatch.setattr(schedule_utils, "match_courier", lambda name, sch: name if name in sch else None)
    monkeypatch.setattr(mo, "get_excluded", lambda: [])
    monkeypatch.setattr(mo, "get_excluded_cids", lambda: set())
    monkeypatch.setattr(mo, "get_working", lambda: {})  # brak working-override → ścieżka V3.24-A


def _sched_starting_in(mins):
    now_w = datetime.now(WAW)
    start = (now_w + timedelta(minutes=mins)).strftime("%H:%M")
    end = (now_w + timedelta(hours=8)).strftime("%H:%M")
    return {"Tester K": {"start": start, "end": end}}


def _fleet():
    return {"999": CourierState(courier_id="999", pos=(53.13, 23.16),
                                pos_source="gps", name="Tester K")}


def _included(res):
    return any(c.courier_id == "999" for c in res)


def test_within_window_included(monkeypatch):
    now_w = datetime.now(WAW)
    if not (1 <= now_w.hour <= 14):
        pytest.skip("okno czasowe testu (unik wrapu północy)")
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True)
    _patch_grafik(monkeypatch, _sched_starting_in(40))   # 40 ≤ 60
    res = dispatchable_fleet(fleet=_fleet())
    assert _included(res), "kurier 40 min przed startem (≤ cap) ma być w puli pre-shift"
    cs = next(c for c in res if c.courier_id == "999")
    assert cs.pos_source == "pre_shift"


def test_beyond_window_excluded(monkeypatch):
    now_w = datetime.now(WAW)
    if not (1 <= now_w.hour <= 14):
        pytest.skip("okno czasowe testu (unik wrapu północy)")
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True)
    _patch_grafik(monkeypatch, _sched_starting_in(90))   # 90 > 60 cap
    res = dispatchable_fleet(fleet=_fleet())
    assert not _included(res), "kurier 90 min przed startem (> cap 60) NIE w puli"


def test_window_cap_env_overridable(monkeypatch):
    now_w = datetime.now(WAW)
    if not (1 <= now_w.hour <= 14):
        pytest.skip("okno czasowe testu (unik wrapu północy)")
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True)
    monkeypatch.setattr(C, "PRE_SHIFT_WINDOW_MAX_MIN", 9999.0)  # kill-switch capa
    _patch_grafik(monkeypatch, _sched_starting_in(90))
    res = dispatchable_fleet(fleet=_fleet())
    assert _included(res), "z capem 9999 (off) 90-min pre-shift znów w puli (rollback)"
