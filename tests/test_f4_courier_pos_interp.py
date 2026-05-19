"""Regresja Sprint OBJ F4 Krok 2 (Opcja C) — interpolacja pozycji kuriera no-gps
po nodze pickup→delivery.

Krok 1 (Opcja A, LIVE od 18.05) stawia kuriera w pickup_coords gdy picked_up —
punkt realnie odwiedzony, ale statyczny: gdy elapsed duży, kurier dawno
odjechał. Krok 2: interpolacja `pickup + f·(delivery − pickup)` gdzie
f = clamp(elapsed/eta_leg, 0, 1); elapsed = now − picked_up_at, eta_leg
= OSRM duration_min pickup→delivery. Fail-soft: brak coords/ts, eta=0, OSRM
exception → None → caller pada na Krok 1 → legacy delivery.

Flaga `ENABLE_F4_COURIER_POS_INTERP` ma pierwszeństwo nad Krok 1; obie
niezależne, można mieć K2 ON z K1 OFF (czysty test) lub obie ON (prod).

Design: eod_drafts/2026-05-18/obj_f4_courier_position_design.md
Werdykt Krok 1: at-job #54 PASS 2026-05-19 21:00 UTC.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver  # noqa: E402

# Współrzędne wyraźnie rozdzielne — assert dokładnie który punkt wpadł.
PICKUP = [53.1400, 23.1600]
DELIVERY = [53.1100, 23.2200]
GPS = (53.1300, 23.1700)


def _state(status, now, *, picked_minutes_ago=None,
           with_pickup=True, with_delivery=True, with_picked_at=True):
    base_ts = (now - timedelta(minutes=12)).isoformat()
    rec = {
        "courier_id": "520",
        "status": status,
        "order_id": "900",
        "assigned_at": base_ts,
        "updated_at": base_ts,
    }
    if with_pickup:
        rec["pickup_coords"] = list(PICKUP)
    if with_delivery:
        rec["delivery_coords"] = list(DELIVERY)
    if status == "picked_up" and with_picked_at:
        delta = picked_minutes_ago if picked_minutes_ago is not None else 12
        rec["picked_up_at"] = (now - timedelta(minutes=delta)).isoformat()
    return {"900": rec}


def _run(state, *, k2_on=False, k1_on=False, gps=None, osrm_duration_min=10.0,
         osrm_raises=False):
    """build_fleet_snapshot z izolacją od I/O + kontrola OSRM."""
    if osrm_raises:
        osrm_mock = mock.Mock(side_effect=RuntimeError("OSRM down"))
    else:
        osrm_mock = mock.Mock(return_value={
            "duration_min": osrm_duration_min,
            "distance_km": 5.0,
            "duration_s": osrm_duration_min * 60,
            "distance_m": 5000,
        })
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value={}), \
         mock.patch.object(courier_resolver, "_load_courier_names",
                           return_value={"520": "Test Kurier"}), \
         mock.patch.object(courier_resolver, "_load_gps_positions",
                           return_value=gps or {}), \
         mock.patch.object(courier_resolver, "ENABLE_F4_COURIER_POS_INTERP", k2_on), \
         mock.patch.object(courier_resolver, "ENABLE_F4_COURIER_POS_PICKUP_PROXY",
                           k1_on), \
         mock.patch.object(courier_resolver.osrm_client, "route", osrm_mock), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return courier_resolver.build_fleet_snapshot(), osrm_mock


def _interp_expected(f):
    """Oczekiwana pozycja przy frakcji f między PICKUP a DELIVERY."""
    return (
        PICKUP[0] + f * (DELIVERY[0] - PICKUP[0]),
        PICKUP[1] + f * (DELIVERY[1] - PICKUP[1]),
    )


def _close(a, b, eps=1e-6):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def test_f4_k2_off_legacy_delivery():
    """Obie flagi OFF — picked_up zostaje na delivery_coords (legacy F4-pre)."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now), k2_on=False, k1_on=False)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_delivery"
    assert tuple(cs.pos) == tuple(DELIVERY)


def test_f4_k2_interp_middle_of_leg():
    """K2 ON, elapsed=5min, eta=10min → f=0.5 → pos w połowie nogi."""
    now = datetime.now(timezone.utc)
    fleet, osrm = _run(_state("picked_up", now, picked_minutes_ago=5),
                       k2_on=True, osrm_duration_min=10.0)
    cs = fleet.get("520")
    assert cs is not None, "cid=520 missing"
    assert cs.pos_source == "last_picked_up_interp", \
        f"expected interp, got {cs.pos_source}"
    expected = _interp_expected(0.5)
    assert _close(tuple(cs.pos), expected), \
        f"interp mismatch: got {tuple(cs.pos)}, expected {expected}"
    assert osrm.called, "OSRM route() powinien być wywołany"


def test_f4_k2_interp_elapsed_zero_at_pickup():
    """K2 ON, elapsed≈0 → f=0 → pos = PICKUP (kurier właśnie odebrał)."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now, picked_minutes_ago=0),
                        k2_on=True, osrm_duration_min=10.0)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_interp"
    assert _close(tuple(cs.pos), tuple(PICKUP))


def test_f4_k2_interp_elapsed_over_eta_clamps_to_delivery():
    """K2 ON, elapsed=20min, eta=10min → f=1 (clamp) → pos = DELIVERY."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now, picked_minutes_ago=20),
                        k2_on=True, osrm_duration_min=10.0)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_interp"
    assert _close(tuple(cs.pos), tuple(DELIVERY))


def test_f4_k2_failsoft_missing_picked_up_at_falls_to_k1():
    """K2 ON, K1 ON, brak picked_up_at → interp=None → Krok 1 wchodzi."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now, with_picked_at=False),
                        k2_on=True, k1_on=True)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_pickup", \
        f"expected K1 fallback, got {cs.pos_source}"
    assert tuple(cs.pos) == tuple(PICKUP)


def test_f4_k2_failsoft_osrm_raises_falls_to_legacy():
    """K2 ON, K1 OFF, OSRM rzuca wyjątek → interp=None → legacy delivery."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now), k2_on=True, k1_on=False,
                        osrm_raises=True)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_delivery"
    assert tuple(cs.pos) == tuple(DELIVERY)


def test_f4_k2_failsoft_osrm_zero_duration_falls_to_legacy():
    """K2 ON, K1 OFF, OSRM duration_min=0 (degenerat) → fail-soft → legacy."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now), k2_on=True, k1_on=False,
                        osrm_duration_min=0.0)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_delivery"


def test_f4_k2_failsoft_missing_pickup_falls_to_legacy():
    """K2 ON, brak pickup_coords → interp niemożliwa → legacy delivery."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now, with_pickup=False), k2_on=True)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_delivery"


def test_f4_k2_priority_over_k1_when_both_on():
    """K2 + K1 obie ON, valid data → K2 wygrywa (interp), K1 nie aktywuje się."""
    now = datetime.now(timezone.utc)
    fleet, _osrm = _run(_state("picked_up", now, picked_minutes_ago=3),
                        k2_on=True, k1_on=True, osrm_duration_min=10.0)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_picked_up_interp"
    assert not _close(tuple(cs.pos), tuple(PICKUP)), \
        "K1 by zwrócił PICKUP — tu musi być punkt interpolowany"
    assert not _close(tuple(cs.pos), tuple(DELIVERY))


def test_f4_k2_assigned_not_affected():
    """K2 ON dla assigned (nie picked_up) — bez wpływu, leci last_assigned_pickup."""
    now = datetime.now(timezone.utc)
    fleet, osrm = _run(_state("assigned", now), k2_on=True)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "last_assigned_pickup"
    assert tuple(cs.pos) == tuple(PICKUP)
    assert not osrm.called, "Dla assigned OSRM nie powinien być wołany"


def test_f4_k2_fresh_gps_wins_over_interp():
    """Świeży GPS ma priorytet — K2 interp nie aktywuje się."""
    now = datetime.now(timezone.utc)
    fresh_gps = {"520": {"lat": GPS[0], "lon": GPS[1],
                         "timestamp": now.isoformat()}}
    fleet, osrm = _run(_state("picked_up", now), k2_on=True, gps=fresh_gps)
    cs = fleet.get("520")
    assert cs is not None
    assert cs.pos_source == "gps"
    assert tuple(cs.pos) == tuple(GPS)
    assert not osrm.called, "GPS path nie wywołuje OSRM dla F4"


def test_f4_k2_pos_source_priority_registered():
    """POS_SOURCE_PRIORITY mapuje nowe źródło (równo z K1 — punkt realny)."""
    assert "last_picked_up_interp" in courier_resolver.POS_SOURCE_PRIORITY
    assert courier_resolver.POS_SOURCE_PRIORITY["last_picked_up_interp"] == 1


if __name__ == "__main__":
    tests = [
        test_f4_k2_off_legacy_delivery,
        test_f4_k2_interp_middle_of_leg,
        test_f4_k2_interp_elapsed_zero_at_pickup,
        test_f4_k2_interp_elapsed_over_eta_clamps_to_delivery,
        test_f4_k2_failsoft_missing_picked_up_at_falls_to_k1,
        test_f4_k2_failsoft_osrm_raises_falls_to_legacy,
        test_f4_k2_failsoft_osrm_zero_duration_falls_to_legacy,
        test_f4_k2_failsoft_missing_pickup_falls_to_legacy,
        test_f4_k2_priority_over_k1_when_both_on,
        test_f4_k2_assigned_not_affected,
        test_f4_k2_fresh_gps_wins_over_interp,
        test_f4_k2_pos_source_priority_registered,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\nPASS={passed} FAIL={failed} / {len(tests)}")
    sys.exit(0 if failed == 0 else 1)
