"""V3.27.5 Path B — state_machine COURIER_ASSIGNED preserve terminal states.

Naprawia chain bug TASK H (2026-04-27): panel_diff COURIER_ASSIGNED ~12-18s
post COURIER_PICKED_UP nadpisywał status="picked_up" → "assigned", tworząc
inconsistency 13.4% picked-up orders.

Tests:
1. Unit — assigned status (initial NEW_ORDER → assigned) → status updated
2. Unit — picked_up status preserved (THE FIX)
3. Unit — delivered status preserved (terminal)
4. Integration replay #469087 z events.db scenario → final status=picked_up
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


# Use isolated tmp state dir
_TMP_DIR = tempfile.mkdtemp(prefix="v3275_path_b_test_")
os.environ["DISPATCH_STATE_DIR"] = _TMP_DIR

from dispatch_v2 import state_machine  # noqa: E402


def _reset_state():
    """Clear test state file."""
    p = state_machine._state_path()
    if os.path.exists(p):
        os.remove(p)


def _make_assigned_order(oid, status):
    """Inject order with given status into state."""
    state_machine.upsert_order(oid, {
        "status": status,
        "commitment_level": status,
        "courier_id": "100",
        "restaurant": "TestRest",
        "delivery_address": "TestAddr",
    }, event="TEST_INIT")


def test_courier_assigned_normal_assigned_to_assigned():
    """Standard re-assignment: status=assigned → assigned (legitymny)."""
    _reset_state()
    _make_assigned_order("oid_normal", "assigned")
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "oid_normal",
        "courier_id": "200",
        "payload": {},
    })
    o = state_machine.get_order("oid_normal")
    assert o["status"] == "assigned", f"Expected assigned, got {o['status']}"
    assert o["courier_id"] == "200", f"courier_id should update to 200, got {o['courier_id']}"


def test_courier_assigned_picked_up_PRESERVED():
    """V3.27.5 Path B FIX: status=picked_up → preserved (NIE revert)."""
    _reset_state()
    _make_assigned_order("oid_picked", "picked_up")
    # Set picked_up_at separately (matches real flow)
    state_machine.upsert_order("oid_picked", {
        "picked_up_at": "2026-04-27 20:48:43",
    }, event="TEST_PICKED_AT")
    # Now COURIER_ASSIGNED panel_diff (the bug scenario)
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "oid_picked",
        "courier_id": "200",
        "payload": {},
    })
    o = state_machine.get_order("oid_picked")
    assert o["status"] == "picked_up", f"FIX FAIL: status reverted to {o['status']}, expected picked_up"
    assert o["picked_up_at"] == "2026-04-27 20:48:43", "picked_up_at must be preserved"
    # courier_id should still update (legitimate re-assignment field)
    assert o["courier_id"] == "200", f"courier_id should update, got {o['courier_id']}"


def test_courier_assigned_delivered_PRESERVED():
    """V3.27.5 Path B: status=delivered (terminal) → preserved."""
    _reset_state()
    _make_assigned_order("oid_delivered", "delivered")
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": "oid_delivered",
        "courier_id": "300",
        "payload": {},
    })
    o = state_machine.get_order("oid_delivered")
    assert o["status"] == "delivered", f"FIX FAIL: status reverted to {o['status']}, expected delivered"


def test_integration_469087_replay_real_events():
    """Replay #469087 events.db scenario:
    - 18:14:04 COURIER_ASSIGNED initial cid=484
    - 18:14:34 CZAS_KURIERA_UPDATED None → 20:43 (first_acceptance)
    - 18:44:24 COURIER_PICKED_UP timestamp=20:44:10
    - 18:44:41 COURIER_ASSIGNED panel_diff cid=515 (THE BUG TRIGGER)

    Pre-fix: final status="assigned" + picked_up_at SET (inconsistent)
    Post-fix: final status="picked_up" + picked_up_at SET (consistent)
    """
    _reset_state()
    oid = "469087"

    # Step 1: NEW_ORDER + initial COURIER_ASSIGNED
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {
            "restaurant": "Miejska Miska",
            "delivery_address": "aleja JP II 61C/85",
            "pickup_at_warsaw": "2026-04-27T20:31:36+02:00",
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
    })
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "484",
        "payload": {},
    })

    # Step 2: CZAS_KURIERA_UPDATED → 20:43
    state_machine.update_from_event({
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": oid,
        "courier_id": "484",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-04-27T20:43:00+02:00",
            "new_ck_hhmm": "20:43",
            "delta_min": None,
            "source": "first_acceptance",
        },
    })

    # Step 3: COURIER_PICKED_UP @ 18:44:24
    state_machine.update_from_event({
        "event_type": "COURIER_PICKED_UP",
        "order_id": oid,
        "courier_id": "484",
        "payload": {
            "timestamp": "2026-04-27 20:44:10",
            "source": "reconcile",
        },
    })

    o = state_machine.get_order(oid)
    assert o["status"] == "picked_up", f"After PICKED_UP, status should be picked_up, got {o['status']}"
    assert o["picked_up_at"] == "2026-04-27 20:44:10"

    # Step 4: COURIER_ASSIGNED panel_diff cid=515 (THE BUG TRIGGER)
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "515",
        "payload": {},
    })

    # Path B FIX verification
    o_final = state_machine.get_order(oid)
    assert o_final["status"] == "picked_up", \
        f"V3.27.5 Path B FIX: final status should be picked_up, got {o_final['status']}"
    assert o_final["picked_up_at"] == "2026-04-27 20:44:10", \
        "picked_up_at preserved"
    # courier_id allowed to change (legitimate re-assignment)
    assert o_final["courier_id"] == "515", \
        f"courier_id should update to 515, got {o_final['courier_id']}"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
