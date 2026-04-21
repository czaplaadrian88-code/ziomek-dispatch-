"""V3.19g1 TDD failing tests — czas_kuriera detection + state update + metrics.

10 tests (7 unit + 3 integration) + 1 optional bonus (11).

Before Step 4 implementation: ALL tests must FAIL.

Coverage:
  1-6 Unit: panel_watcher._diff_czas_kuriera detection logic
  7    Unit: state_machine.update_from_event CZAS_KURIERA_UPDATED branch
  8    Integration: panel → state flow (fresh ck updates orders_state)
  9    Integration: bag_raw after update uses fresh ck
  10   Integration: learning_log entry has all 8 v319g_* fields (LOCATION A+B)
  11   Bonus: kid diagnostic (kid_state/kid_panel/kid_mismatch) no action
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

passed = 0
failed = 0
errors = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def check_raises(label, fn, exc_types, detail=""):
    global passed, failed
    try:
        fn()
    except exc_types:
        passed += 1
        print(f"  OK {passed}. {label}")
        return
    except Exception as e:
        failed += 1
        print(f"  FAIL {label} — unexpected {type(e).__name__}: {e} {detail}")
        return
    failed += 1
    print(f"  FAIL {label} — did not raise {exc_types} {detail}")


def run(label, fn):
    global errors
    try:
        fn()
    except Exception as e:
        errors += 1
        print(f"  ERROR {label}: {type(e).__name__}: {e}")


# ============================================================
print("=== V3.19g1: panel_watcher._diff_czas_kuriera detection ===")
# ============================================================

# Tests 1-6 rely on a helper panel_watcher._diff_czas_kuriera(old_state, fresh_response, cid)
# that returns either None or a CZAS_KURIERA_UPDATED event payload dict.
# Before impl: function does not exist → ImportError (counts as FAIL via exception).


def _import_diff_helper():
    from dispatch_v2 import panel_watcher
    return getattr(panel_watcher, "_diff_czas_kuriera")


def _fresh(ck_hhmm, ck_iso=None):
    """Synthetic fresh panel response snippet with czas_kuriera."""
    return {
        "czas_kuriera": ck_hhmm,
        "czas_kuriera_warsaw": ck_iso,
        "czas_kuriera_hhmm": ck_hhmm,
    }


def _state(ck_iso, ck_hhmm="10:26", cid="400"):
    return {
        "courier_id": cid,
        "czas_kuriera_warsaw": ck_iso,
        "czas_kuriera_hhmm": ck_hhmm,
    }


# Test 1
def t1_no_change_no_event():
    diff = _import_diff_helper()
    out = diff(
        _state("2026-04-21T10:33:00+02:00", "10:33"),
        _fresh("10:33", "2026-04-21T10:33:00+02:00"),
        oid="467533",
    )
    check("1. no_change → no event (None)", out is None)


# Test 2
def t2_below_threshold_no_event():
    diff = _import_diff_helper()
    out = diff(
        _state("2026-04-21T10:33:00+02:00", "10:33"),
        _fresh("10:35", "2026-04-21T10:35:00+02:00"),
        oid="467533",
    )
    check("2. below threshold (Δ=2min) → no event", out is None)


# Test 3
def t3_above_threshold_emits_event():
    diff = _import_diff_helper()
    out = diff(
        _state("2026-04-21T10:26:00+02:00", "10:26"),
        _fresh("10:33", "2026-04-21T10:33:00+02:00"),
        oid="467533",
    )
    ok = (
        isinstance(out, dict)
        and out.get("event_type") == "CZAS_KURIERA_UPDATED"
        and out.get("order_id") == "467533"
        and out.get("payload", {}).get("old_ck_hhmm") == "10:26"
        and out.get("payload", {}).get("new_ck_hhmm") == "10:33"
        and abs(out.get("payload", {}).get("delta_min", 0) - 7.0) < 0.01
        and out.get("payload", {}).get("source") == "panel_re_check"
    )
    check("3. above threshold (Δ=+7min) → emit event with full payload", ok,
          detail=f"got={out}")


# Test 4
def t4_negative_delta_emits_event():
    diff = _import_diff_helper()
    out = diff(
        _state("2026-04-21T10:33:00+02:00", "10:33"),
        _fresh("10:26", "2026-04-21T10:26:00+02:00"),
        oid="467533",
    )
    ok = (
        isinstance(out, dict)
        and out.get("event_type") == "CZAS_KURIERA_UPDATED"
        and abs(out.get("payload", {}).get("delta_min", 0) - (-7.0)) < 0.01
    )
    check("4. negative Δ=-7min (shortening) → emit event", ok, detail=f"got={out}")


# Test 5
def t5_first_acceptance_no_event():
    diff = _import_diff_helper()
    out = diff(
        _state(None, None),
        _fresh("10:33", "2026-04-21T10:33:00+02:00"),
        oid="467533",
    )
    check("5. first acceptance (old=null, new=10:33) → no event", out is None)


# Test 6
def t6_null_after_value_warn_skip():
    diff = _import_diff_helper()
    out = diff(
        _state("2026-04-21T10:26:00+02:00", "10:26"),
        _fresh(None, None),
        oid="467533",
    )
    check("6. value→null (panel revert) → no event", out is None)


run("t1", t1_no_change_no_event)
run("t2", t2_below_threshold_no_event)
run("t3", t3_above_threshold_emits_event)
run("t4", t4_negative_delta_emits_event)
run("t5", t5_first_acceptance_no_event)
run("t6", t6_null_after_value_warn_skip)


# ============================================================
print("=== V3.19g1: state_machine CZAS_KURIERA_UPDATED branch ===")
# ============================================================


class _TmpState:
    def __enter__(self):
        from dispatch_v2 import state_machine
        self.sm = state_machine
        self.tmpdir = tempfile.mkdtemp(prefix="v319g1_state_")
        self.path = os.path.join(self.tmpdir, "orders.json")
        with open(self.path, "w") as f:
            json.dump({}, f)
        self._orig = state_machine._state_path
        state_machine._state_path = lambda: self.path
        return self

    def __exit__(self, *a):
        try:
            self.sm._state_path = self._orig
        except Exception:
            pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        try:
            os.unlink(self.path + ".lock")
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass


# Test 7
def t7_state_update_via_event():
    from dispatch_v2 import state_machine
    with _TmpState() as _tmp:
        # Seed existing order with old ck
        state_machine.upsert_order("467533", {
            "status": "assigned",
            "courier_id": "400",
            "czas_kuriera_warsaw": "2026-04-21T10:26:00+02:00",
            "czas_kuriera_hhmm": "10:26",
        }, event="COURIER_ASSIGNED")
        # Dispatch CZAS_KURIERA_UPDATED event
        evt = {
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": "467533",
            "courier_id": "400",
            "payload": {
                "oid": "467533",
                "courier_id": "400",
                "old_ck_iso": "2026-04-21T10:26:00+02:00",
                "old_ck_hhmm": "10:26",
                "new_ck_iso": "2026-04-21T10:33:00+02:00",
                "new_ck_hhmm": "10:33",
                "delta_min": 7.0,
                "source": "panel_re_check",
            },
        }
        result = state_machine.update_from_event(evt)
        got = state_machine.get_order("467533") or {}
        ok = (
            got.get("czas_kuriera_warsaw") == "2026-04-21T10:33:00+02:00"
            and got.get("czas_kuriera_hhmm") == "10:33"
            and got.get("courier_id") == "400"  # preserved
            and got.get("status") == "assigned"  # preserved
        )
        check("7. state_machine applies CZAS_KURIERA_UPDATED → ck fields updated, other preserved",
              ok, detail=f"got={got}")


run("t7", t7_state_update_via_event)


# ============================================================
print("=== V3.19g1: integration panel → state ===")
# ============================================================


# Test 8
def t8_panel_to_state_flow():
    """Simulate panel_watcher detection → event emit → state_machine update."""
    from dispatch_v2 import panel_watcher, state_machine
    with _TmpState() as _tmp:
        state_machine.upsert_order("467533", {
            "status": "assigned",
            "courier_id": "400",
            "czas_kuriera_warsaw": "2026-04-21T10:26:00+02:00",
            "czas_kuriera_hhmm": "10:26",
        }, event="COURIER_ASSIGNED")
        diff = getattr(panel_watcher, "_diff_czas_kuriera", None)
        assert diff is not None, "panel_watcher._diff_czas_kuriera must exist (Step 4 impl)"
        old_state = state_machine.get_order("467533")
        fresh = {
            "czas_kuriera": "10:33",
            "czas_kuriera_warsaw": "2026-04-21T10:33:00+02:00",
            "czas_kuriera_hhmm": "10:33",
        }
        evt = diff(old_state, fresh, oid="467533")
        assert evt is not None, "should emit"
        state_machine.update_from_event(evt)
        got = state_machine.get_order("467533") or {}
        ok = (
            got.get("czas_kuriera_hhmm") == "10:33"
            and got.get("czas_kuriera_warsaw") == "2026-04-21T10:33:00+02:00"
        )
        check("8. panel detection → state update end-to-end", ok, detail=f"got={got}")


run("t8", t8_panel_to_state_flow)


# ============================================================
print("=== V3.19g1: bag_raw after update uses fresh ck ===")
# ============================================================


# Test 9
def t9_bag_raw_uses_fresh_ck():
    """After V3.19g1 state update, build_fleet_snapshot produces bag item
    with new czas_kuriera_warsaw. Proves NO cache — fresh read every call."""
    from dispatch_v2 import state_machine, courier_resolver
    with _TmpState() as _tmp:
        state_machine.upsert_order("467533", {
            "status": "assigned",
            "courier_id": "400",
            "czas_kuriera_warsaw": "2026-04-21T10:26:00+02:00",
            "czas_kuriera_hhmm": "10:26",
        }, event="COURIER_ASSIGNED")
        # Apply V3.19g1 update
        state_machine.update_from_event({
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": "467533",
            "payload": {
                "oid": "467533",
                "new_ck_iso": "2026-04-21T10:33:00+02:00",
                "new_ck_hhmm": "10:33",
                "old_ck_iso": "2026-04-21T10:26:00+02:00",
                "old_ck_hhmm": "10:26",
                "delta_min": 7.0,
                "source": "panel_re_check",
            },
        })
        # Build fleet snapshot (no cache per discovery)
        fleet = courier_resolver.build_fleet_snapshot()
        cs = fleet.get("400")
        bag = getattr(cs, "bag", []) if cs else []
        found = [b for b in bag if str(b.get("order_id")) == "467533"]
        ok = (
            len(found) == 1
            and found[0].get("czas_kuriera_warsaw") == "2026-04-21T10:33:00+02:00"
        )
        check("9. bag_raw after V3.19g1 update contains fresh czas_kuriera", ok,
              detail=f"bag_entry={found}")


run("t9", t9_bag_raw_uses_fresh_ck)


# ============================================================
print("=== V3.19g1: shadow_dispatcher LOCATION A + B metrics ===")
# ============================================================


# Test 10
def t10_serializer_location_a_b():
    """All 8 v319g_* fields (5 ck + 3 kid) present in serialized learning_log entry,
    both LOCATION A (per-candidate) and LOCATION B (inline best)."""
    from dispatch_v2 import shadow_dispatcher as sd
    # LOCATION A — _serialize_candidate output must have all 8 keys
    serialize_fn = getattr(sd, "_serialize_candidate", None)
    assert serialize_fn is not None, "shadow_dispatcher._serialize_candidate missing"

    # Minimal synthetic candidate matching _serialize_candidate contract
    # (metrics dict-like via .get, plan=None, feasibility_* attrs).
    class _Cand:
        def __init__(self):
            self.courier_id = "400"
            self.name = "Adrian Cit"
            self.score = 100.0
            self.feasibility_verdict = "OK"
            self.feasibility_reason = ""
            self.best_effort = False
            self.plan = None
            self.metrics = {
                "v319g_ck_changed": True,
                "v319g_ck_old": "10:26",
                "v319g_ck_new": "10:33",
                "v319g_ck_delta_min": 7.0,
                "v319g_ck_change_count": 1,
                "v319g_kid_state": 400,
                "v319g_kid_panel": 400,
                "v319g_kid_mismatch": False,
            }

    try:
        out = serialize_fn(_Cand())
    except Exception as e:
        check(f"10. _serialize_candidate crashed on v319g_ fields", False, detail=str(e))
        return
    if not isinstance(out, dict):
        check("10. _serialize_candidate returns dict", False, detail=f"type={type(out)}")
        return
    required = [
        "v319g_ck_changed", "v319g_ck_old", "v319g_ck_new",
        "v319g_ck_delta_min", "v319g_ck_change_count",
        "v319g_kid_state", "v319g_kid_panel", "v319g_kid_mismatch",
    ]
    missing = [k for k in required if k not in out]
    ok_a = not missing
    check(f"10a. LOCATION A _serialize_candidate has 8 v319g_ keys",
          ok_a, detail=f"missing={missing}")

    # LOCATION B — inline best serialization. V3.19f lesson: best dict is built
    # separately, must also contain v319g_* fields. Introspect module for a
    # function/marker or integration-sample.
    # Minimal check: that the module source text contains the keys at top level
    # (imperfect but catches missing-from-inline-best case).
    import inspect
    src = inspect.getsource(sd)
    loc_b_ok = all(k in src for k in required)
    check(f"10b. LOCATION B inline best (module source references all 8 keys)",
          loc_b_ok,
          detail=f"missing_in_source={[k for k in required if k not in src]}")


run("t10", t10_serializer_location_a_b)


# ============================================================
print("=== V3.19g1: BONUS — kid diagnostic (no action) ===")
# ============================================================


# Test 11 bonus
def t11_kid_diagnostic_no_action():
    """State=400, panel=26 (HTML lag scenario). Diagnostic metrics set,
    NO event emitted (diagnostic-only, Case 2 detection deferred)."""
    from dispatch_v2 import panel_watcher
    diag_fn = getattr(panel_watcher, "_compute_kid_diagnostic", None)
    assert diag_fn is not None, "panel_watcher._compute_kid_diagnostic missing"

    state_order = {"courier_id": "400"}
    fresh_order = {"id_kurier": 26}
    out = diag_fn(state_order, fresh_order)
    ok = (
        isinstance(out, dict)
        and out.get("v319g_kid_state") == 400
        and out.get("v319g_kid_panel") == 26
        and out.get("v319g_kid_mismatch") is True
        and out.get("_event") is None  # no event emitted
    )
    check("11. kid diagnostic: state=400, panel=26 → mismatch=True, no event",
          ok, detail=f"got={out}")


run("t11", t11_kid_diagnostic_no_action)


# ============================================================
print()
print(f"=== SUMMARY ===")
print(f"  PASSED: {passed}")
print(f"  FAILED: {failed}")
print(f"  ERRORS: {errors}")
total = passed + failed + errors
print(f"  TOTAL:  {total}")
if passed > 0:
    print(f"\n  WARNING: TDD expects 0 passing before Step 4 impl.")
    print(f"           {passed} test(s) PASS already — investigate.")
sys.exit(0 if failed + errors == 0 else 1)
