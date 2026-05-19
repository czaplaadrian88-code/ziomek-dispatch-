"""PICKUP_TIME_UPDATED detection — unit + integration tests.

Root cause oid 474577 (2026-05-19): pickup_at_warsaw zapisywany RAZ w
NEW_ORDER, nigdy nie odświeżany dla czasówek status=planned. Koordynator
zmienił czas odbioru na życzenie restauracji (ck 10:10 → 12:17, Δ+127min),
Ziomek czytał stary pickup_at_warsaw → czasowka_scheduler._minutes_to_pickup
liczył T-minus od 10:10 zamiast 12:17 → FORCE_ASSIGN spam ~3h za wcześnie.

Fix: panel_watcher._diff_pickup_time + state_machine PICKUP_TIME_UPDATED
handler + rozszerzenie pętli re-check na czasówki planned.

Coverage:
  1-9   Unit:        panel_watcher._diff_pickup_time
  10-13 Unit:        state_machine.update_from_event PICKUP_TIME_UPDATED
  14    Integration: panel diff → event → state (scenariusz 474577)
"""
import json
import os
import sys
import tempfile

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


def run(label, fn):
    global errors
    try:
        fn()
    except Exception as e:
        errors += 1
        print(f"  ERROR {label}: {type(e).__name__}: {e}")


def _diff():
    from dispatch_v2 import panel_watcher
    return panel_watcher._diff_pickup_time


# Realne pola ze zlecenia 474577 (2026-05-19).
OLD_PICKUP = "2026-05-19T10:10:00+02:00"
NEW_PICKUP = "2026-05-19T12:17:00+02:00"


def _state(pickup_iso, prep=60, cid="393"):
    return {
        "courier_id": cid,
        "pickup_at_warsaw": pickup_iso,
        "prep_minutes": prep,
        "order_type": "czasowka",
    }


def _fresh(pickup_iso, prep=60, decision_deadline=None, zmiana=False):
    return {
        "pickup_at_warsaw": pickup_iso,
        "prep_minutes": prep,
        "decision_deadline": decision_deadline,
        "zmiana_czasu_odbioru": zmiana,
    }


# ============================================================
print("=== PICKUP_TIME_UPDATED: panel_watcher._diff_pickup_time ===")
# ============================================================


def t1_no_change():
    out = _diff()(_state(OLD_PICKUP), _fresh(OLD_PICKUP), oid="474577")
    check("1. no change → no event (None)", out is None, detail=f"got={out}")


def t2_below_threshold():
    out = _diff()(
        _state("2026-05-19T10:10:00+02:00"),
        _fresh("2026-05-19T10:12:00+02:00"),
        oid="474577",
    )
    check("2. below threshold (Δ=2min) → no event", out is None,
          detail=f"got={out}")


def t3_above_threshold_474577():
    """Realny scenariusz 474577: pickup 10:10 → 12:17 (Δ+127min)."""
    out = _diff()(_state(OLD_PICKUP), _fresh(NEW_PICKUP), oid="474577")
    p = (out or {}).get("payload", {}) if isinstance(out, dict) else {}
    ok = (
        isinstance(out, dict)
        and out.get("event_type") == "PICKUP_TIME_UPDATED"
        and out.get("order_id") == "474577"
        and p.get("old_pickup_at_warsaw") == OLD_PICKUP
        and p.get("new_pickup_at_warsaw") == NEW_PICKUP
        and abs(p.get("delta_min", 0) - 127.0) < 0.01
        and p.get("source") == "panel_re_check"
        and out.get("event_id_suffix") is None
    )
    check("3. scenariusz 474577 (Δ=+127min) → emit event z pełnym payload",
          ok, detail=f"got={out}")


def t4_negative_delta():
    """Pickup przesunięty wcześniej — też musi być wykryty."""
    out = _diff()(_state(NEW_PICKUP), _fresh(OLD_PICKUP), oid="474577")
    p = (out or {}).get("payload", {}) if isinstance(out, dict) else {}
    ok = (
        isinstance(out, dict)
        and out.get("event_type") == "PICKUP_TIME_UPDATED"
        and abs(p.get("delta_min", 0) - (-127.0)) < 0.01
    )
    check("4. negatywna Δ (pickup wcześniej) → emit event", ok,
          detail=f"got={out}")


def t5_null_null():
    out = _diff()(_state(None), _fresh(None), oid="474577")
    check("5. null→null → no event", out is None, detail=f"got={out}")


def t6_value_to_null():
    """Panel revert do null — NIE nadpisuj realnej wartości."""
    out = _diff()(_state(OLD_PICKUP), _fresh(None), oid="474577")
    check("6. value→null (panel revert) → no event", out is None,
          detail=f"got={out}")


def t7_null_to_value():
    """Late-arriving pole panelu → emit z suffixem _LATE, delta=None."""
    out = _diff()(_state(None), _fresh(NEW_PICKUP), oid="474577")
    p = (out or {}).get("payload", {}) if isinstance(out, dict) else {}
    ok = (
        isinstance(out, dict)
        and out.get("event_type") == "PICKUP_TIME_UPDATED"
        and out.get("event_id_suffix") == "_LATE"
        and p.get("delta_min") is None
        and p.get("old_pickup_at_warsaw") is None
        and p.get("new_pickup_at_warsaw") == NEW_PICKUP
    )
    check("7. null→value (late panel field) → emit, _LATE suffix, delta=None",
          ok, detail=f"got={out}")


def t8_parse_fail():
    """Nieparsowalny ISO → None (fail-soft, nie crash)."""
    out = _diff()(_state("garbage-not-iso"), _fresh(NEW_PICKUP), oid="474577")
    check("8. nieparsowalny old ISO → no event (fail-soft)", out is None,
          detail=f"got={out}")


def t9_payload_carries_bundle():
    """Payload niesie prep_minutes / decision_deadline / zmiana_czasu_odbioru."""
    out = _diff()(
        _state(OLD_PICKUP, prep=60),
        _fresh(NEW_PICKUP, prep=180,
               decision_deadline="2026-05-19T12:00:00+02:00", zmiana=True),
        oid="474577",
    )
    p = (out or {}).get("payload", {}) if isinstance(out, dict) else {}
    ok = (
        isinstance(out, dict)
        and p.get("old_prep_minutes") == 60
        and p.get("new_prep_minutes") == 180
        and p.get("new_decision_deadline") == "2026-05-19T12:00:00+02:00"
        and p.get("new_zmiana_czasu_odbioru") is True
    )
    check("9. payload niesie pakiet czasu (prep/deadline/zmiana)", ok,
          detail=f"got={p}")


for _t in (t1_no_change, t2_below_threshold, t3_above_threshold_474577,
           t4_negative_delta, t5_null_null, t6_value_to_null,
           t7_null_to_value, t8_parse_fail, t9_payload_carries_bundle):
    run(_t.__name__, _t)


# ============================================================
print("=== PICKUP_TIME_UPDATED: state_machine handler ===")
# ============================================================


class _TmpState:
    def __enter__(self):
        from dispatch_v2 import state_machine
        self.sm = state_machine
        self.tmpdir = tempfile.mkdtemp(prefix="pickup_time_state_")
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
        for suffix in ("", ".lock"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass


def _evt(oid="474577", old=OLD_PICKUP, new=NEW_PICKUP, prep=180,
         deadline="2026-05-19T12:00:00+02:00", zmiana=True, delta=127.0):
    return {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "courier_id": "393",
        "payload": {
            "oid": oid,
            "courier_id": "393",
            "old_pickup_at_warsaw": old,
            "new_pickup_at_warsaw": new,
            "old_prep_minutes": 60,
            "new_prep_minutes": prep,
            "new_decision_deadline": deadline,
            "new_zmiana_czasu_odbioru": zmiana,
            "delta_min": delta,
            "source": "panel_re_check",
        },
    }


def t10_handler_updates_and_preserves():
    """Handler odświeża pola czasu, preserve status/courier/czas_kuriera."""
    from dispatch_v2 import state_machine
    with _TmpState():
        state_machine.upsert_order("474577", {
            "status": "planned",
            "courier_id": "26",
            "pickup_at_warsaw": OLD_PICKUP,
            "prep_minutes": 60,
            "czas_kuriera_warsaw": "2026-05-19T10:10:00+02:00",
            "czas_kuriera_hhmm": "10:10",
            "order_type": "czasowka",
        }, event="NEW_ORDER")
        state_machine.update_from_event(_evt())
        got = state_machine.get_order("474577") or {}
        ok = (
            got.get("pickup_at_warsaw") == NEW_PICKUP
            and got.get("prep_minutes") == 180
            and got.get("decision_deadline") == "2026-05-19T12:00:00+02:00"
            and got.get("zmiana_czasu_odbioru") is True
            and got.get("pickup_time_change_count") == 1
            # preserved — orthogonal fields nietknięte
            and got.get("status") == "planned"
            and got.get("courier_id") == "26"
            and got.get("czas_kuriera_warsaw") == "2026-05-19T10:10:00+02:00"
            and got.get("czas_kuriera_hhmm") == "10:10"
        )
        check("10. handler: pickup/prep/deadline odświeżone, status/courier/"
              "czas_kuriera preserved, change_count=1", ok, detail=f"got={got}")


def t11_handler_unknown_oid():
    """Unknown oid → None, brak crashu."""
    from dispatch_v2 import state_machine
    with _TmpState():
        out = state_machine.update_from_event(_evt(oid="999999"))
        check("11. handler: unknown oid → None (no crash)", out is None,
              detail=f"got={out}")


def t12_handler_missing_new_pickup():
    """Brak new_pickup_at_warsaw → None skip (nie kasuje pola)."""
    from dispatch_v2 import state_machine
    with _TmpState():
        state_machine.upsert_order("474577", {
            "status": "planned", "pickup_at_warsaw": OLD_PICKUP,
        }, event="NEW_ORDER")
        out = state_machine.update_from_event(_evt(new=None))
        got = state_machine.get_order("474577") or {}
        ok = out is None and got.get("pickup_at_warsaw") == OLD_PICKUP
        check("12. handler: brak new_pickup → None skip, stare pole nietknięte",
              ok, detail=f"out={out} got={got}")


def t13_handler_partial_bundle():
    """prep/deadline=None w payload → NIE nadpisuje istniejących wartości."""
    from dispatch_v2 import state_machine
    with _TmpState():
        state_machine.upsert_order("474577", {
            "status": "assigned", "pickup_at_warsaw": OLD_PICKUP,
            "prep_minutes": 60, "decision_deadline": "keep-me",
        }, event="NEW_ORDER")
        state_machine.update_from_event(
            _evt(prep=None, deadline=None, zmiana=None))
        got = state_machine.get_order("474577") or {}
        ok = (
            got.get("pickup_at_warsaw") == NEW_PICKUP
            and got.get("prep_minutes") == 60          # nietknięte
            and got.get("decision_deadline") == "keep-me"  # nietknięte
        )
        check("13. handler: None w payload → istniejące pola nietknięte", ok,
              detail=f"got={got}")


for _t in (t10_handler_updates_and_preserves, t11_handler_unknown_oid,
           t12_handler_missing_new_pickup, t13_handler_partial_bundle):
    run(_t.__name__, _t)


# ============================================================
print("=== PICKUP_TIME_UPDATED: integracja panel → state (474577) ===")
# ============================================================


def t14_integration_474577():
    """Pełny przepływ: czasówka planned, koordynator zmienił czas odbioru →
    _diff_pickup_time wykrywa → event → state_machine odświeża pickup."""
    from dispatch_v2 import panel_watcher, state_machine
    with _TmpState():
        state_machine.upsert_order("474577", {
            "status": "planned",
            "courier_id": "26",
            "pickup_at_warsaw": OLD_PICKUP,
            "prep_minutes": 60,
            "order_type": "czasowka",
        }, event="NEW_ORDER")
        old_state = state_machine.get_order("474577")
        fresh = _fresh(NEW_PICKUP, prep=180)
        evt = panel_watcher._diff_pickup_time(old_state, fresh, oid="474577")
        assert evt is not None, "diff musi wykryć zmianę pickup"
        state_machine.update_from_event(evt)
        got = state_machine.get_order("474577") or {}
        ok = (
            got.get("pickup_at_warsaw") == NEW_PICKUP
            and got.get("prep_minutes") == 180
            and got.get("status") == "planned"  # nadal w Koordynatorze
        )
        check("14. integracja 474577: planned czasówka → pickup odświeżony "
              "10:10→12:17, status preserved", ok, detail=f"got={got}")


run("t14_integration_474577", t14_integration_474577)


# ============================================================
print()
print("=== SUMMARY ===")
print(f"  PASSED: {passed}")
print(f"  FAILED: {failed}")
print(f"  ERRORS: {errors}")
print(f"  TOTAL:  {passed + failed + errors}")
sys.exit(0 if failed + errors == 0 else 1)
