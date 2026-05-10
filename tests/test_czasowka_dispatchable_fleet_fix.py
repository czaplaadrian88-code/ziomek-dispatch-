"""Czasowka_scheduler dispatchable_fleet fix — test 2026-05-06.

PRE-FIX (bug):
  fleet_snapshot = courier_resolver.build_fleet_snapshot()
POST-FIX:
  fleet_snapshot = {cs.courier_id: cs for cs in courier_resolver.dispatchable_fleet()}

Reason: dispatchable_fleet() enriches CourierState with shift_end from schedule
V3.24-A. Raw build_fleet_snapshot() leaves shift_end=None → feasibility_v2:300
hard-rejects all candidates with v325_NO_ACTIVE_SHIFT → "BRAK KANDYDATÓW" alert
(incident #471036 2026-05-06 14:24 UTC).

Tests target eval_czasowka() directly (the function holding the fix), mocking
the boundary calls (dispatchable_fleet, assess_order) — not eval_czasowka itself.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import czasowka_scheduler as cs
from dispatch_v2.courier_resolver import CourierState


def _build_czasowka_order_state(now_utc: datetime, mins_to_pickup: float = 50.0) -> dict:
    return {
        "order_id": "TEST_OID",
        "status": "planned",
        "courier_id": "26",
        "prep_minutes": 90,
        "pickup_at_warsaw": (now_utc + timedelta(minutes=mins_to_pickup)).isoformat(),
        "first_seen": (now_utc - timedelta(minutes=10)).isoformat(),
        "updated_at": now_utc.isoformat(),
        "restaurant": "Test Restauracja",
        "delivery_address": "Test 1, Białystok",
        "pickup_address": "Pickup 1, Białystok",
        "pickup_coords": [53.14, 23.17],
        "delivery_coords": [53.13, 23.16],
        "address_id": 1,
        "pickup_city": "Białystok",
        "delivery_city": "Białystok",
        "order_type": "elastyk",
        "status_id": 2,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }


def _fake_assess_order_result(candidates):
    """Build a minimal PipelineResult-like object for assert_called_with."""
    res = mock.MagicMock()
    res.best = candidates[0] if candidates else None
    res.candidates = candidates
    return res


def test_eval_czasowka_uses_dispatchable_fleet_not_raw_snapshot():
    """eval_czasowka() must call dispatchable_fleet() (the fix), NOT build_fleet_snapshot()."""
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    order_state = _build_czasowka_order_state(now_utc, mins_to_pickup=50.0)

    courier = CourierState(
        courier_id="414",
        pos=(53.13, 23.16),
        pos_source="gps",
        shift_end=now_utc + timedelta(hours=4),
    )

    fake_result = _fake_assess_order_result([])
    fake_result.best = None

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet",
                           return_value=[courier]) as mock_dispatchable, \
         mock.patch.object(cs.courier_resolver, "build_fleet_snapshot",
                           return_value={}) as mock_raw, \
         mock.patch.object(cs, "assess_order", return_value=fake_result) as mock_assess:
        cs.eval_czasowka("TEST_OID", order_state, now_utc)

    assert mock_dispatchable.call_count >= 1, \
        f"dispatchable_fleet() should be called by eval_czasowka, got {mock_dispatchable.call_count}"
    assert mock_raw.call_count == 0, \
        f"build_fleet_snapshot() should NOT be called directly (only inside dispatchable_fleet), got {mock_raw.call_count}"


def test_assess_order_receives_fleet_with_shift_end_set():
    """The fleet_snapshot passed to assess_order must contain CourierState with shift_end != None."""
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    order_state = _build_czasowka_order_state(now_utc, mins_to_pickup=50.0)

    courier_with_shift = CourierState(
        courier_id="508",
        pos=(53.13, 23.16),
        pos_source="gps",
        shift_end=now_utc + timedelta(hours=3),
    )

    captured = {}

    def _capture_assess(order_event, fleet_snapshot, now=None):
        captured["fleet_snapshot"] = fleet_snapshot
        return _fake_assess_order_result([])

    fake_result = _fake_assess_order_result([])

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet",
                           return_value=[courier_with_shift]), \
         mock.patch.object(cs, "assess_order", side_effect=_capture_assess):
        cs.eval_czasowka("TEST_OID", order_state, now_utc)

    fs = captured.get("fleet_snapshot")
    assert fs is not None, "assess_order was not called"
    assert "508" in fs, f"courier 508 missing from fleet_snapshot: {list(fs.keys())}"
    assert fs["508"].shift_end is not None, \
        "shift_end is None on CourierState → would trigger v325_NO_ACTIVE_SHIFT (the bug)"
    assert fs["508"].shift_end == courier_with_shift.shift_end, \
        "shift_end value mismatch — fleet not properly threaded through"


def test_dispatchable_fleet_empty_does_not_crash():
    """When dispatchable_fleet() returns [] (e.g. schedule load fail, all off-shift),
    eval_czasowka must not crash — graceful degradation to KOORD-style decision."""
    now_utc = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    order_state = _build_czasowka_order_state(now_utc, mins_to_pickup=30.0)  # ≤40 → KOORD path

    fake_result = _fake_assess_order_result([])
    fake_result.best = None

    with mock.patch.object(cs.courier_resolver, "dispatchable_fleet", return_value=[]), \
         mock.patch.object(cs, "assess_order", return_value=fake_result):
        result = cs.eval_czasowka("TEST_OID", order_state, now_utc)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert result.get("decision") in {"KOORD", "WAIT", "DONT_EMIT", "FORCE_ASSIGN"}, \
        f"Unexpected decision under empty fleet: {result.get('decision')}"


if __name__ == "__main__":
    tests = [
        test_eval_czasowka_uses_dispatchable_fleet_not_raw_snapshot,
        test_assess_order_receives_fleet_with_shift_end_set,
        test_dispatchable_fleet_empty_does_not_crash,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  OK {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"PASSED: {passed}/{len(tests)}")
    sys.exit(0 if failed == 0 else 1)
