"""TASK 2 Część A (2026-05-04) — tests dla panel_diff path status_id 8/9.

Pre-fix bug: panel_watcher.py:580 wywoływał upsert_order(status='cancelled')
bez emit do events.db → 70 phantom orders nakumulowanych w events.db.

Post-fix: panel_diff path mirroruje reconcile path L960 → emit ORDER_RETURNED_TO_POOL
+ update_from_event. Single source of truth dla cancellation/uncollected events.

Tests:
  1-2: status_id 8/9 emituje ORDER_RETURNED_TO_POOL z poprawnym reason mapping
  3:   payload.source="panel_diff" (dyskryminator vs reconcile path)
  4:   idempotent — emit dedup → update_from_event NIE wołane
  5:   state_machine.delete_order raises na non-terminal status (Z3 safety guard)
  6:   state_machine.delete_order akceptuje terminal status
  7:   panel_diff path 8/9 → reason mapping spójny z reconcile L960
"""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import panel_watcher

FAKE_STATE = {
    "PD001": {"status": "assigned", "restaurant": "Test Resto 1", "delivery_address": "Addr 1"},
    "PD002": {"status": "assigned", "restaurant": "Test Resto 2", "delivery_address": "Addr 2"},
    "PD003": {"status": "delivered","restaurant": "Test Resto 3", "delivery_address": "Addr 3"},
}
FAKE_DETAILS = {
    "PD001": {"id_status_zamowienia": 8, "id_kurier": 511, "czas_doreczenia": None},
    "PD002": {"id_status_zamowienia": 9, "id_kurier": 512, "czas_doreczenia": None},
}

emitted, updated, fetched = [], [], []
def fake_emit(event_type, order_id=None, courier_id=None, payload=None, event_id=None):
    emitted.append({"event_type": event_type, "order_id": order_id, "courier_id": courier_id,
                    "payload": payload, "event_id": event_id})
    return event_id or f"FAKE_{event_type}_{order_id}"
def fake_update_from_event(event):
    updated.append(event)
    return {"order_id": event.get("order_id"), "status": "fake_updated"}
def fake_fetch_order_details(zid, csrf=None):
    fetched.append(zid)
    return FAKE_DETAILS.get(zid)
def fake_state_get_all():
    return FAKE_STATE

panel_watcher.emit = fake_emit
panel_watcher.update_from_event = fake_update_from_event
panel_watcher.fetch_order_details = fake_fetch_order_details
panel_watcher.state_get_all = fake_state_get_all


def build_parsed_panel_diff(disappeared_zids):
    """parsed gdzie order_ids NIE zawiera disappeared zids — triggeruje panel_diff path L543."""
    state_zids = set(FAKE_STATE.keys())
    visible = state_zids - set(disappeared_zids)
    return {
        "order_ids": sorted(visible),
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def reset(): emitted.clear(); updated.clear(); fetched.clear()


passed, failed = 0, 0
def t(name, fn):
    global passed, failed
    reset()
    try:
        fn()
        passed += 1; print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1; print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1; print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


print("=" * 70)
print("TASK 2 Część A — panel_diff path status_id 8/9 + delete_order guard")
print("=" * 70)


def test_panel_diff_status_8_emits_returned_to_pool():
    parsed = build_parsed_panel_diff(["PD001"])
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
    assert len(rtp) == 1, f"expected 1 ORDER_RETURNED_TO_POOL, got {len(rtp)}"
    assert rtp[0]["order_id"] == "PD001"
    assert rtp[0]["payload"]["reason"] == "undelivered"
    assert rtp[0]["payload"]["source"] == "panel_diff"
    assert rtp[0]["courier_id"] == "511"
    assert "PD001_ORDER_RETURNED_undelivered_panel_diff" == rtp[0]["event_id"]
    assert any(u["event_type"] == "ORDER_RETURNED_TO_POOL" and u["order_id"] == "PD001" for u in updated)
t("panel_diff status_id=8 emits ORDER_RETURNED_TO_POOL", test_panel_diff_status_8_emits_returned_to_pool)


def test_panel_diff_status_9_emits_returned_to_pool():
    parsed = build_parsed_panel_diff(["PD002"])
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
    assert len(rtp) == 1
    assert rtp[0]["order_id"] == "PD002"
    assert rtp[0]["payload"]["reason"] == "cancelled"
    assert rtp[0]["payload"]["source"] == "panel_diff"
    assert "PD002_ORDER_RETURNED_cancelled_panel_diff" == rtp[0]["event_id"]
t("panel_diff status_id=9 emits ORDER_RETURNED_TO_POOL", test_panel_diff_status_9_emits_returned_to_pool)


def test_panel_diff_source_discriminator():
    parsed = build_parsed_panel_diff(["PD001"])
    panel_watcher._diff_and_emit(parsed, csrf="dummy")
    rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
    assert rtp[0]["payload"]["source"] == "panel_diff"
    assert "panel_diff" in rtp[0]["event_id"]
t("panel_diff source discriminator (vs reconcile)", test_panel_diff_source_discriminator)


def test_panel_diff_no_update_when_emit_dedup():
    parsed = build_parsed_panel_diff(["PD001"])
    saved_emit = panel_watcher.emit
    def dedup_emit(*a, **kw):
        emitted.append({"event_type": kw.get("event_type") or (a[0] if a else None), **kw})
        return None
    panel_watcher.emit = dedup_emit
    try:
        panel_watcher._diff_and_emit(parsed, csrf="dummy")
        rtp_updates = [u for u in updated if u["event_type"] == "ORDER_RETURNED_TO_POOL"]
        assert len(rtp_updates) == 0
    finally:
        panel_watcher.emit = saved_emit
t("idempotent: no update when emit deduped", test_panel_diff_no_update_when_emit_dedup)


def test_delete_order_safety_guard():
    from dispatch_v2 import state_machine
    import tempfile, os
    tmpf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    tmpf.write('{"X1": {"order_id":"X1","status":"assigned"}}')
    tmpf.close()
    saved = state_machine._state_path
    state_machine._state_path = lambda: tmpf.name
    try:
        try:
            state_machine.delete_order("X1")
            raise AssertionError("delete_order should have raised on non-terminal status")
        except RuntimeError as e:
            assert "not terminal" in str(e), f"expected 'not terminal' in error, got: {e}"
    finally:
        state_machine._state_path = saved
        os.unlink(tmpf.name)
t("delete_order raises on non-terminal status", test_delete_order_safety_guard)


def test_delete_order_accepts_terminal():
    from dispatch_v2 import state_machine
    import tempfile, os, json
    tmpf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    tmpf.write('{"X2": {"order_id":"X2","status":"delivered"}}')
    tmpf.close()
    saved = state_machine._state_path
    state_machine._state_path = lambda: tmpf.name
    try:
        result = state_machine.delete_order("X2")
        assert result is True
        with open(tmpf.name) as f:
            data = json.load(f)
        assert "X2" not in data
    finally:
        state_machine._state_path = saved
        if os.path.exists(tmpf.name): os.unlink(tmpf.name)
t("delete_order accepts terminal status", test_delete_order_accepts_terminal)


def test_panel_diff_and_reconcile_consistency():
    expected = {8: "undelivered", 9: "cancelled"}
    for sid, exp_reason in expected.items():
        reset()
        zid = "PD001" if sid == 8 else "PD002"
        parsed = build_parsed_panel_diff([zid])
        panel_watcher._diff_and_emit(parsed, csrf="dummy")
        rtp = [e for e in emitted if e["event_type"] == "ORDER_RETURNED_TO_POOL"]
        assert len(rtp) == 1, f"sid={sid}: expected 1 emit, got {len(rtp)}"
        assert rtp[0]["payload"]["reason"] == exp_reason
t("panel_diff status_id 8/9 → reason mapping spójny", test_panel_diff_and_reconcile_consistency)


print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
