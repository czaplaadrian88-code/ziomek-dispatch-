"""Tests for L2 defense gates + L3 fail-loud haversine + L4 KOORD alert format.

Sprint 2026-05-07: firmowe-konto-uwagi-parser. Każda warstwa testowana
osobno. Integration test: assess_order behavior + czasowka_scheduler
defense gate + custom KOORD alert wording.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.dispatch_pipeline import assess_order
from dispatch_v2.osrm_client import haversine
from dispatch_v2.czasowka_scheduler import _format_koord_alert
from dispatch_v2.geometry import haversine_km
from dispatch_v2.plan_recheck import _haversine_m


# ─────────────────────────────────────────────
# L3 fail-loud haversine (4 tests)
# ─────────────────────────────────────────────

def test_haversine_none_ll1_raises():
    with pytest.raises(ValueError, match="None coords"):
        haversine(None, (53.13, 23.16))


def test_haversine_none_ll2_raises():
    with pytest.raises(ValueError, match="None coords"):
        haversine((53.13, 23.16), None)


def test_haversine_sentinel_ll1_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        haversine((0.0, 0.0), (53.13, 23.16))


def test_haversine_sentinel_ll2_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        haversine((53.13, 23.16), (0.0, 0.0))


def test_haversine_valid_returns_positive():
    d = haversine((53.13, 23.16), (53.14, 23.18))
    assert d > 0
    assert d < 5  # < 5km dla bliskich punktów


# ─────────────────────────────────────────────
# MP-#7 Sprint A: geometry.haversine_km guards (5 tests, Lekcja #81 cross-codebase)
# ─────────────────────────────────────────────

def test_geometry_haversine_km_none_a_raises():
    with pytest.raises(ValueError, match="None coords"):
        haversine_km(None, (53.13, 23.16))


def test_geometry_haversine_km_none_b_raises():
    with pytest.raises(ValueError, match="None coords"):
        haversine_km((53.13, 23.16), None)


def test_geometry_haversine_km_sentinel_a_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        haversine_km((0.0, 0.0), (53.13, 23.16))


def test_geometry_haversine_km_sentinel_b_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        haversine_km((53.13, 23.16), (0.0, 0.0))


def test_geometry_haversine_km_valid_returns_positive():
    d = haversine_km((53.13, 23.16), (53.14, 23.18))
    assert d > 0
    assert d < 5


# ─────────────────────────────────────────────
# MP-#7 Sprint B: plan_recheck._haversine_m guards (5 tests)
# ─────────────────────────────────────────────

def test_plan_recheck_haversine_m_none_p1_raises():
    with pytest.raises(ValueError, match="None coords"):
        _haversine_m(None, (53.13, 23.16))


def test_plan_recheck_haversine_m_none_p2_raises():
    with pytest.raises(ValueError, match="None coords"):
        _haversine_m((53.13, 23.16), None)


def test_plan_recheck_haversine_m_sentinel_p1_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        _haversine_m((0.0, 0.0), (53.13, 23.16))


def test_plan_recheck_haversine_m_sentinel_p2_raises():
    with pytest.raises(ValueError, match="sentinel.*0,0"):
        _haversine_m((53.13, 23.16), (0.0, 0.0))


def test_plan_recheck_haversine_m_valid_returns_meters():
    d = _haversine_m((53.13, 23.16), (53.14, 23.18))
    assert d > 0
    assert d < 5000  # < 5km


# ─────────────────────────────────────────────
# L2 defense gate w dispatch_pipeline (3 tests)
# ─────────────────────────────────────────────

def _build_event(pickup_coords):
    return {
        "order_id": "TEST_999",
        "restaurant": "Nadajesz.pl",
        "delivery_address": "Mickiewicza 50",
        "pickup_coords": pickup_coords,
        "delivery_coords": [53.13, 23.16],
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
        "address_id": "161",
        "uwagi": "Odbiór 13:30-14:00: MALI WOJOWNICY",
    }


def test_assess_order_skip_when_pickup_coords_none():
    result = assess_order(_build_event(None), fleet_snapshot={})
    assert result.verdict == "SKIP"
    assert result.reason == "no_pickup_geocode"
    assert result.best is None
    assert result.candidates == []


def test_assess_order_skip_when_pickup_coords_sentinel_list():
    result = assess_order(_build_event([0.0, 0.0]), fleet_snapshot={})
    assert result.verdict == "SKIP"
    assert result.reason == "no_pickup_geocode"


def test_assess_order_skip_when_pickup_coords_sentinel_tuple():
    result = assess_order(_build_event((0.0, 0.0)), fleet_snapshot={})
    assert result.verdict == "SKIP"
    assert result.reason == "no_pickup_geocode"


def test_assess_order_normal_flow_when_pickup_coords_valid():
    """Defense gate bypassed gdy coords poprawne; flow kończy KOORD bo fleet pusty."""
    result = assess_order(_build_event([53.12, 23.17]), fleet_snapshot={})
    assert result.reason != "no_pickup_geocode"
    # Empty fleet → KOORD or SKIP normal reasons, not defense gate
    assert result.verdict in ("KOORD", "SKIP", "PROPOSE")


# ─────────────────────────────────────────────
# L4 czasowka_scheduler KOORD alert wording (3 tests)
# ─────────────────────────────────────────────

def test_koord_alert_no_pickup_geocode_dedicated_wording():
    order_state = {
        "address_id": "161",
        "uwagi": "Odbiór 13:30-14:00: MALI WOJOWNICY\r\nDostawa: Mickiewicza 50",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
        "restaurant": "Nadajesz.pl",
    }
    result = {
        "decision": "KOORD",
        "reason": "no_pickup_geocode",
        "minutes_to_pickup": 36,
        "match_quality": "none",
        "best": None,
        "alternatives": [],
    }
    text = _format_koord_alert("471173", order_state, result)
    assert "BEZ GEOKODACJI" in text
    assert "address_id=161" in text
    assert "MALI WOJOWNICY" in text
    assert "BRAK KANDYDATÓW" not in text
    assert "ręczne przypisanie KOORD" in text


def test_koord_alert_normal_brak_kandydatow_wording():
    order_state = {
        "restaurant": "Pizzeria",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    result = {
        "decision": "KOORD",
        "reason": "≤40min + zero MAYBE candidates",
        "minutes_to_pickup": 36,
        "match_quality": "none",
        "best": None,
        "alternatives": [],
    }
    text = _format_koord_alert("999", order_state, result)
    assert "BRAK KANDYDATÓW" in text
    assert "BEZ GEOKODACJI" not in text



# ─────────────────────────────────────────────
# Firmowe konto fallback coords (3 tests)
# ─────────────────────────────────────────────

def test_firmowe_fallback_coords_constant_dms_match():
    """Adrian decision 2026-05-07: 53°07'56.0"N 23°10'06.4"E (DMS round-trip)."""
    from dispatch_v2.common import FIRMOWE_KONTO_FALLBACK_COORDS
    lat, lon = FIRMOWE_KONTO_FALLBACK_COORDS
    # 53 + 7/60 + 56/3600 = 53.13222...
    assert abs(lat - (53 + 7 / 60 + 56.0 / 3600)) < 1e-4
    # 23 + 10/60 + 6.4/3600 = 23.16844...
    assert abs(lon - (23 + 10 / 60 + 6.4 / 3600)) < 1e-4


def test_firmowe_fallback_when_parser_returns_none():
    """P3 edge (uwagi=company-only) → parser None → fallback coords."""
    from dispatch_v2.uwagi_address_parser import parse_pickup_from_uwagi
    from dispatch_v2.common import FIRMOWE_KONTO_FALLBACK_COORDS
    uwagi = "Odbiór 13:30-14:00: MALI WOJOWNICY\r\nDostawa: Mickiewicza 50"
    parsed = parse_pickup_from_uwagi(uwagi)
    assert parsed is None
    # Fallback path mirror
    coords = tuple(FIRMOWE_KONTO_FALLBACK_COORDS) if parsed is None else None
    assert coords == (53.13222, 23.16844)


def test_assess_order_normal_flow_with_fallback_coords():
    """Fallback coords valid → defense gate bypass → normal feasibility flow."""
    from dispatch_v2.common import FIRMOWE_KONTO_FALLBACK_COORDS
    event = _build_event(list(FIRMOWE_KONTO_FALLBACK_COORDS))
    result = assess_order(event, fleet_snapshot={})
    assert result.reason != "no_pickup_geocode"
    assert result.verdict in ("KOORD", "SKIP", "PROPOSE")


# ─────────────────────────────────────────────
# Firmowe konto KOORD alert suppression (3 tests)
# ─────────────────────────────────────────────

def test_send_koord_alert_suppressed_for_firmowe_konto():
    """Firmowe (aid=161) + reason zwykły (BRAK KANDYDATÓW) + flag False → suppress.
    (Decyzja Adriana 07.05 — firmowe zwykłe KOORD = noise; nadal aktualne dla
    reason ≠ no_pickup_geocode.)"""
    from unittest.mock import patch
    from dispatch_v2 import czasowka_scheduler
    from dispatch_v2 import common as C

    order_state = {
        "address_id": "161",
        "uwagi": "Odbiór: Drtusz, Wyszyńskiego 2/75",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    # reason ≠ no_pickup_geocode → firmowe suppress nadal działa
    result = {"reason": "brak_feasible_maybe", "minutes_to_pickup": 36, "best": None, "alternatives": []}

    with patch.object(C, "flag", side_effect=lambda name, default=False: False if name == "ENABLE_FIRMOWE_KONTO_KOORD_ALERTS" else default), \
         patch.object(czasowka_scheduler, "tg_request") as mock_tg, \
         patch.object(czasowka_scheduler, "_resolve_bot_token", return_value="dummy"), \
         patch.object(czasowka_scheduler, "_resolve_alert_chat_id", return_value=-123):
        czasowka_scheduler._send_koord_alert("471173", order_state, result)
    assert mock_tg.call_count == 0, "tg_request must NOT be called when firmowe + zwykły reason + flag False"


def test_send_koord_alert_FIRES_for_firmowe_no_pickup_geocode_reject():
    """FAZA 2 #1: firmowe + no_pickup_geocode + reject-flag ON → alert MUSI dotrzeć
    do koordynatora (reject bez flagi = cichy drop). Wyjątek od firmowe-suppress."""
    from unittest.mock import patch
    from dispatch_v2 import czasowka_scheduler
    from dispatch_v2 import common as C

    order_state = {
        "address_id": "161",
        "uwagi": "Odbiór: MALI WOJOWNICY",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    result = {"reason": "no_pickup_geocode", "minutes_to_pickup": 36, "best": None, "alternatives": []}

    def _flag(name, default=False):
        if name == "ENABLE_FIRMOWE_KONTO_KOORD_ALERTS":
            return False
        if name == "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL":
            return True
        return default

    with patch.object(C, "flag", side_effect=_flag), \
         patch.object(czasowka_scheduler, "tg_request") as mock_tg, \
         patch.object(czasowka_scheduler, "_resolve_bot_token", return_value="dummy"), \
         patch.object(czasowka_scheduler, "_resolve_alert_chat_id", return_value=-123):
        czasowka_scheduler._send_koord_alert("471173", order_state, result)
    assert mock_tg.call_count == 1, "no_pickup_geocode pod reject-flagą MUSI alertować koordynatora"


def test_send_koord_alert_fires_for_non_firmowe():
    """Regular restaurant (address_id != 161) → tg_request wywołane normalnie."""
    from unittest.mock import patch, MagicMock
    from dispatch_v2 import czasowka_scheduler

    order_state = {
        "address_id": "200",  # regular restaurant
        "restaurant": "Pizzeria",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    result = {"reason": "≤40min + zero MAYBE", "minutes_to_pickup": 36, "best": None, "alternatives": []}

    mock_response = MagicMock()
    mock_response.get = lambda k, d=None: True if k == "ok" else d
    with patch.object(czasowka_scheduler, "tg_request", return_value=mock_response) as mock_tg, \
         patch.object(czasowka_scheduler, "_resolve_bot_token", return_value="dummy"), \
         patch.object(czasowka_scheduler, "_resolve_alert_chat_id", return_value=-123):
        czasowka_scheduler._send_koord_alert("999", order_state, result)
    assert mock_tg.call_count == 1, "tg_request must be called for non-firmowe"


def test_send_koord_alert_fires_for_firmowe_when_flag_true():
    """Override flag True → firmowe alerts re-enabled (escape hatch)."""
    from unittest.mock import patch, MagicMock
    from dispatch_v2 import czasowka_scheduler
    from dispatch_v2 import common as C

    order_state = {
        "address_id": "161",
        "uwagi": "Odbiór: Drtusz, Wyszyńskiego 2/75",
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    result = {"reason": "no_pickup_geocode", "minutes_to_pickup": 36, "best": None, "alternatives": []}

    mock_response = MagicMock()
    mock_response.get = lambda k, d=None: True if k == "ok" else d
    with patch.object(C, "flag", side_effect=lambda name, default=False: True if name == "ENABLE_FIRMOWE_KONTO_KOORD_ALERTS" else default), \
         patch.object(czasowka_scheduler, "tg_request", return_value=mock_response) as mock_tg, \
         patch.object(czasowka_scheduler, "_resolve_bot_token", return_value="dummy"), \
         patch.object(czasowka_scheduler, "_resolve_alert_chat_id", return_value=-123):
        czasowka_scheduler._send_koord_alert("471173", order_state, result)
    assert mock_tg.call_count == 1, "tg_request must be called when firmowe + flag True"


def test_koord_alert_no_pickup_geocode_truncates_long_uwagi():
    long_uwagi = "Odbiór: " + ("X" * 500)  # 500+ chars
    order_state = {
        "address_id": "161",
        "uwagi": long_uwagi,
        "pickup_at_warsaw": "2026-05-07T11:00:00+02:00",
    }
    result = {
        "decision": "KOORD",
        "reason": "no_pickup_geocode",
        "minutes_to_pickup": 36,
        "best": None,
        "alternatives": [],
    }
    text = _format_koord_alert("999", order_state, result)
    # Truncation marker present (test for ellipsis char)
    assert "…" in text
    # Full 500-char uwagi NOT included
    assert ("X" * 350) not in text
