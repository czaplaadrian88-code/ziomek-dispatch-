"""V3.27.5 Path B race condition stress tests (MP-#12, 2026-05-08).

Doplenia tests/test_v3275_path_b_state_preserve.py o 4 race-specific scenariusze
adresujące real-world panel_watcher cycle race (META top-5, STATE_OWNERSHIP F3+F8):

R1. Out-of-order delivery — COURIER_ASSIGNED *przed* COURIER_PICKED_UP w jednym cyklu
    (panel_diff fires PRZED reconcile picked_up). Kolejność deterministyczna: po
    PICKED_UP order musi być terminal, subsequent ASSIGNED ignored.
R2. Burst storm — 5× COURIER_ASSIGNED w <1s post-PICKED_UP (panel re-emit storm).
    Expected: status=picked_up zachowany przez wszystkie 5; courier_id propagowany
    z ostatniego eventu (legitimate re-assignment field).
R3. Hand-off — kurier A picked_up, panel re-assigns do kuriera B przed delivery.
    Expected: status=picked_up preserved + courier_id=B + WARN logged.
R4. Delivered terminal lockout — COURIER_DELIVERED → COURIER_ASSIGNED race.
    Delivered = absolute terminal, żaden subsequent event nie revertuje.
"""
import logging
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="v3275_race_test_")
os.environ["DISPATCH_STATE_DIR"] = _TMP_DIR

from dispatch_v2 import state_machine  # noqa: E402


def _reset_state():
    p = state_machine._state_path()
    if os.path.exists(p):
        os.remove(p)


def _capture_warnings():
    """Capture state_machine WARN logs (Path B emits one per ignored revert)."""
    handler = logging.Handler()
    records = []
    handler.emit = lambda r: records.append(r)
    handler.setLevel(logging.WARNING)
    state_machine._log.addHandler(handler)
    return records, handler


def _release_capture(handler):
    state_machine._log.removeHandler(handler)


# ---- R1: Out-of-order — ASSIGNED before PICKED_UP same cycle ----

def test_race_R1_assigned_before_picked_up_same_cycle():
    """panel_diff COURIER_ASSIGNED ~12-18s przed reconcile COURIER_PICKED_UP.
    PICKED_UP wygrywa terminal — następna ASSIGNED ignored."""
    _reset_state()
    oid = "race_r1"
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {"restaurant": "R1", "delivery_address": "A1"},
    })
    # ASSIGNED first (panel_diff)
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "100",
        "payload": {},
    })
    assert state_machine.get_order(oid)["status"] == "assigned"

    # PICKED_UP arrives (reconcile)
    state_machine.update_from_event({
        "event_type": "COURIER_PICKED_UP",
        "order_id": oid,
        "courier_id": "100",
        "payload": {"timestamp": "2026-05-08 12:00:00", "source": "reconcile"},
    })
    assert state_machine.get_order(oid)["status"] == "picked_up"

    # 2nd ASSIGNED (panel_diff stale view, same cycle race) — must NOT revert
    records, handler = _capture_warnings()
    try:
        state_machine.update_from_event({
            "event_type": "COURIER_ASSIGNED",
            "order_id": oid,
            "courier_id": "100",
            "payload": {},
        })
    finally:
        _release_capture(handler)

    o = state_machine.get_order(oid)
    assert o["status"] == "picked_up", f"R1 FAIL: status reverted to {o['status']}"
    assert o["picked_up_at"] == "2026-05-08 12:00:00", "picked_up_at preserved"
    assert any("ignored status revert" in r.getMessage() for r in records), \
        "R1 FAIL: WARN log missing for ignored revert"


# ---- R2: Burst storm — 5x ASSIGNED post-PICKED_UP ----

def test_race_R2_burst_5x_assigned_post_picked_up():
    """Panel emits 5× COURIER_ASSIGNED w <1s (re-fetch storm). Status preserved
    przez wszystkie; courier_id propagated z ostatniego."""
    _reset_state()
    oid = "race_r2"
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {"restaurant": "R2", "delivery_address": "A2"},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "200",
        "payload": {},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_PICKED_UP",
        "order_id": oid,
        "courier_id": "200",
        "payload": {"timestamp": "2026-05-08 13:00:00", "source": "reconcile"},
    })

    records, handler = _capture_warnings()
    try:
        for cid in ("201", "202", "203", "204", "205"):
            state_machine.update_from_event({
                "event_type": "COURIER_ASSIGNED",
                "order_id": oid,
                "courier_id": cid,
                "payload": {},
            })
    finally:
        _release_capture(handler)

    o = state_machine.get_order(oid)
    assert o["status"] == "picked_up", f"R2 FAIL: status={o['status']} po 5x ASSIGNED storm"
    assert o["courier_id"] == "205", f"R2 FAIL: courier_id should be 205 (last), got {o['courier_id']}"
    assert o["picked_up_at"] == "2026-05-08 13:00:00", "picked_up_at preserved"
    n_warnings = sum(1 for r in records if "ignored status revert" in r.getMessage())
    assert n_warnings == 5, f"R2 FAIL: expected 5 WARN logs, got {n_warnings}"


# ---- R3: Hand-off — courier A picked_up, re-assign do B ----

def test_race_R3_handoff_courier_swap_post_picked_up():
    """Kurier A=300 picked_up, panel re-assigns do B=400 (rzadkie, np. uszkodzenie
    pojazdu mid-trip). Status preserved (terminal), courier_id update OK."""
    _reset_state()
    oid = "race_r3"
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {"restaurant": "R3", "delivery_address": "A3"},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "300",
        "payload": {},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_PICKED_UP",
        "order_id": oid,
        "courier_id": "300",
        "payload": {"timestamp": "2026-05-08 14:00:00", "source": "reconcile"},
    })

    records, handler = _capture_warnings()
    try:
        state_machine.update_from_event({
            "event_type": "COURIER_ASSIGNED",
            "order_id": oid,
            "courier_id": "400",
            "payload": {},
        })
    finally:
        _release_capture(handler)

    o = state_machine.get_order(oid)
    assert o["status"] == "picked_up", f"R3 FAIL: status reverted to {o['status']}"
    assert o["courier_id"] == "400", f"R3 FAIL: hand-off courier_id should be 400, got {o['courier_id']}"
    msgs = [r.getMessage() for r in records if "ignored status revert" in r.getMessage()]
    assert msgs, "R3 FAIL: WARN log missing for hand-off"
    assert "courier_id_new=400" in msgs[0] and "courier_id_old=300" in msgs[0], \
        f"R3 FAIL: WARN must include hand-off audit trail, got: {msgs[0]}"


# ---- R4: Delivered absolute terminal lockout ----

def test_race_R4_delivered_terminal_lockout():
    """COURIER_DELIVERED = absolute terminal. Subsequent ASSIGNED race
    (panel_diff stale by minutes) MUST NOT revert do assigned."""
    _reset_state()
    oid = "race_r4"
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {"restaurant": "R4", "delivery_address": "A4"},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid,
        "courier_id": "500",
        "payload": {},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_PICKED_UP",
        "order_id": oid,
        "courier_id": "500",
        "payload": {"timestamp": "2026-05-08 15:00:00", "source": "reconcile"},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_DELIVERED",
        "order_id": oid,
        "courier_id": "500",
        "payload": {"timestamp": "2026-05-08 15:30:00"},
    })
    assert state_machine.get_order(oid)["status"] == "delivered"

    records, handler = _capture_warnings()
    try:
        state_machine.update_from_event({
            "event_type": "COURIER_ASSIGNED",
            "order_id": oid,
            "courier_id": "501",
            "payload": {},
        })
    finally:
        _release_capture(handler)

    o = state_machine.get_order(oid)
    assert o["status"] == "delivered", f"R4 FAIL: delivered terminal NOT preserved, got {o['status']}"
    assert any("ignored status revert" in r.getMessage() for r in records), \
        "R4 FAIL: WARN log missing for delivered lockout"


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
