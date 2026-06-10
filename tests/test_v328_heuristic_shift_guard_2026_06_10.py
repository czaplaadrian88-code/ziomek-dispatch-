"""Z-11 (audyt 2026-06-10) — bramka grafikowa w heurystyce mass-fail V328 Fix 6.

Bug: heurystyka mass-fail (>=50% kurierów crash w OR-Tools pool) omija CAŁĄ
feasibility — jedyny guard to bag-cap. Kurier PO KOŃCU ZMIANY mógł wygrać
w degraded mode (łamie R-SCHEDULE-AWARE / V325 PICKUP_POST_SHIFT).

Fix: _v328_heuristic_post_shift_skip — skip gdy shift_end < now + naive_eta
(haversine / fallback speed). Fail-open: brak shift_end / pozycji / wyjątek →
NIE skipuj (grafik mógł paść razem z OR-Tools). Flaga
ENABLE_V328_HEURISTIC_SHIFT_END_GUARD env default ON + hot-reload kill-switch.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.dispatch_pipeline import _v328_heuristic_post_shift_skip  # noqa: E402


_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
_PICKUP = (53.13, 23.16)
# ~7.4 km haversine od _PICKUP (0.1° lat) → przy 25 km/h ~18 min naive_eta
_POS_FAR = (53.23, 23.16)
_SPEED = 25.0


def _cs(shift_end=None, pos=_POS_FAR):
    return SimpleNamespace(shift_end=shift_end, pos=pos)


def _ev(pickup=_PICKUP):
    return {"pickup_coords": pickup}


def test_shift_already_ended_skipped():
    """KIERUNKOWY: zmiana skończyła się godzinę temu → skip (pre-fix: wygrywał)."""
    cs = _cs(shift_end=_NOW - timedelta(hours=1))
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is True


def test_shift_ends_before_naive_eta_skipped():
    """Zmiana kończy się za 5 min, dojazd ~18 min → skip."""
    cs = _cs(shift_end=_NOW + timedelta(minutes=5))
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is True


def test_shift_ends_after_eta_not_skipped():
    """Zmiana do +2h, dojazd ~18 min → kurier zostaje w puli."""
    cs = _cs(shift_end=_NOW + timedelta(hours=2))
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is False


def test_no_shift_end_fail_open():
    """Brak shift_end (grafik padł razem z OR-Tools) → NIE skipuj (fail-open)."""
    cs = _cs(shift_end=None)
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is False


def test_no_position_fail_open():
    """Brak pozycji → ocenę zostawiamy score'owi (-1000 no-GPS penalty)."""
    cs = _cs(shift_end=_NOW - timedelta(hours=1), pos=None)
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is False
    cs0 = _cs(shift_end=_NOW - timedelta(hours=1), pos=(0.0, 0.0))
    assert _v328_heuristic_post_shift_skip(cs0, _ev(), _NOW, _SPEED) is False


def test_zero_pickup_coords_fail_open():
    cs = _cs(shift_end=_NOW - timedelta(hours=1))
    assert _v328_heuristic_post_shift_skip(cs, _ev(pickup=(0.0, 0.0)), _NOW, _SPEED) is False
    assert _v328_heuristic_post_shift_skip(cs, {}, _NOW, _SPEED) is False
    assert _v328_heuristic_post_shift_skip(cs, None, _NOW, _SPEED) is False


def test_naive_shift_end_assumed_utc():
    """Naive datetime → traktowany jako UTC (spójnie z resztą pipeline)."""
    cs = _cs(shift_end=datetime(2026, 6, 10, 11, 0))  # naive, 1h przed _NOW
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, _SPEED) is True


def test_zero_speed_no_crash():
    """fleet_speed_kmh 0/None → clamp do 1.0, brak ZeroDivisionError."""
    cs = _cs(shift_end=_NOW + timedelta(hours=12))
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, 0.0) is False
    assert _v328_heuristic_post_shift_skip(cs, _ev(), _NOW, None) is False


def test_env_flag_default_on():
    """Env default ON — guard aktywny od deploya (kill-switch dostępny)."""
    assert C.ENABLE_V328_HEURISTIC_SHIFT_END_GUARD is True
