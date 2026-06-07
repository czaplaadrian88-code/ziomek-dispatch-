"""F1 unifikacja silnika trasy — realny picked_up_at w plan_recheck.

Niesione (picked_up) zlecenie dostaje realny picked_up_at (jak ścieżka propozycji),
żeby kara R6 soft-deadline chroniła stygnące jedzenie. Flaga OFF = None (jak dotąd).
Parsowanie MUSI być identyczne jak `_bag_dict_to_ordersim` (parse_panel_timestamp).
"""
from datetime import timezone

from dispatch_v2 import plan_recheck as PR


def _set(on):
    PR.ENABLE_PLAN_REAL_PICKED_UP_AT = on


def test_flag_off_returns_none():
    _set(False)
    assert PR._sim_picked_up_at({"picked_up_at": "2026-06-07 14:51:27"}, "picked_up") is None


def test_carried_parsed_as_warsaw_to_utc():
    _set(True)
    try:
        dt = PR._sim_picked_up_at({"picked_up_at": "2026-06-07 14:51:27"}, "picked_up")
        # 14:51 Warsaw (lato, +02) → 12:51 UTC, aware
        assert dt is not None
        assert dt.tzinfo is not None
        u = dt.astimezone(timezone.utc)
        assert (u.hour, u.minute, u.second) == (12, 51, 27)
    finally:
        _set(False)


def test_assigned_returns_none_even_when_on():
    _set(True)
    try:
        # tylko carried (picked_up) dostaje kotwicę; assigned nie ma jeszcze odbioru
        assert PR._sim_picked_up_at({"picked_up_at": "2026-06-07 14:51:27"}, "assigned") is None
    finally:
        _set(False)


def test_missing_or_bad_timestamp_none():
    _set(True)
    try:
        assert PR._sim_picked_up_at({}, "picked_up") is None
        assert PR._sim_picked_up_at({"picked_up_at": None}, "picked_up") is None
        assert PR._sim_picked_up_at({"picked_up_at": "śmieci"}, "picked_up") is None
    finally:
        _set(False)


def test_matches_proposal_path_parser():
    """Identyczny wynik jak ścieżka propozycji (jeden parser = jeden input)."""
    _set(True)
    try:
        from dispatch_v2.common import parse_panel_timestamp
        raw = "2026-06-07 15:59:02"
        assert PR._sim_picked_up_at({"picked_up_at": raw}, "picked_up") == parse_panel_timestamp(raw)
    finally:
        _set(False)
