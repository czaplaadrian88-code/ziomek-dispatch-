"""Czasówka committed-pickup authority (Adrian 2026-06-24, root #483023).

Umówiony czas CZASÓWKI = pickup_at_warsaw (deklaracja restauracji). Gastro
przestempluje `czas_kuriera` przy zmianie statusu → pasywny re-odczyt panelu
(panel_re_check / pre_proposal_recheck) wpuszczał śmieć jako zmianę committed
(#483023: 16:22→15:04, 5 s po assignie). Fix:
  - panel_re_check / pre_proposal_recheck czas_kuriera dla czasówki → BLOK
  - first_acceptance + kanały deliberatne (ziomek_late_extension) → przechodzą
  - PICKUP_TIME_UPDATED (koordynator/restauracja, dowolny kierunek) → lustrzy
    pickup_at → czas_kuriera, więc apka nadąża za legalną zmianą
  - elastyki nietknięte (tam czas_kuriera to realna obietnica przyjazdu)
"""
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# Script-runner uruchamia ten legacy test w osobnym procesie. Pod pytestem nie
# inicjalizuj FileHandlerow ani import-time cache z zywych sciezek hosta.
if os.environ.get("DISPATCH_UNDER_PYTEST"):
    from dispatch_v2 import common as _test_common

    _test_common.setup_logger = (
        lambda name, log_file=None: logging.getLogger(name)
    )
    _real_getmtime = os.path.getmtime

    def _hermetic_getmtime(path):
        if os.path.abspath(str(path)).startswith("/root/.openclaw/"):
            raise FileNotFoundError(path)
        return _real_getmtime(path)

    with patch.object(os.path, "getmtime", side_effect=_hermetic_getmtime):
        from dispatch_v2 import panel_watcher as _test_panel_watcher  # noqa: F401

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def run(label, fn):
    try:
        fn()
    except Exception as e:
        global failed
        failed += 1
        import traceback
        print(f"  FAIL {label} — {type(e).__name__}: {e}")
        traceback.print_exc()


class _TmpState:
    def __enter__(self):
        from dispatch_v2 import state_machine
        self.sm = state_machine
        self.tmpdir = tempfile.mkdtemp(prefix="czas_auth_")
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
        for p in (self.path, self.path + ".lock"):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass


_PICKUP = "2026-06-24T16:21:22+02:00"
_CK = "2026-06-24T16:22:00+02:00"
_GARBAGE = "2026-06-24T15:04:00+02:00"   # gastro re-stamp (the bug)
_LATER = "2026-06-24T16:35:00+02:00"     # legit forward
_EARLIER = "2026-06-24T16:00:00+02:00"   # legit coordinator backward
_I489052_PICKUP = "2026-07-20T12:00:09+02:00"
_I489052_CK = "2026-07-20T12:01:00+02:00"
_I489052_MANUAL = "2026-07-20T11:41:00+02:00"


def _seed_czasowka(sm, oid="483023", status="assigned"):
    sm.upsert_order(oid, {
        "status": status, "courier_id": "484", "order_type": "czasowka",
        "prep_minutes": 126,
        "pickup_at_warsaw": _PICKUP,
        "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22",
        "zmiana_czasu_odbioru": False,
    }, event="COURIER_ASSIGNED")


def _seed_elastyk(sm, oid="490100"):
    sm.upsert_order(oid, {
        "status": "assigned", "courier_id": "400", "order_type": "elastic",
        "prep_minutes": 20,
        "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22",
    }, event="COURIER_ASSIGNED")


def _seed_489052(sm):
    sm.upsert_order("489052", {
        "status": "assigned", "courier_id": "484",
        "order_type": "czasowka", "prep_minutes": 120,
        "pickup_at_warsaw": _I489052_PICKUP,
        "czas_kuriera_warsaw": _I489052_CK,
        "czas_kuriera_hhmm": "12:01",
        "zmiana_czasu_odbioru": False,
    }, event="COURIER_ASSIGNED")


def _ck_evt(oid, new_iso, new_hhmm, source):
    return {
        "event_type": "CZAS_KURIERA_UPDATED", "order_id": oid, "courier_id": "484",
        "payload": {"oid": oid, "courier_id": "484",
                    "old_ck_iso": _CK, "old_ck_hhmm": "16:22",
                    "new_ck_iso": new_iso, "new_ck_hhmm": new_hhmm,
                    "delta_min": None, "source": source},
    }


def _manual_ck_evt(oid, new_iso, new_hhmm, source="panel_re_check",
                   marker=True, observed_pickup=_PICKUP, status_id=3,
                   observed_prep=None):
    evt = _ck_evt(oid, new_iso, new_hhmm, source)
    evt["payload"].update({
        "new_zmiana_czasu_odbioru": marker,
        "observed_pickup_at_warsaw": observed_pickup,
        "observed_status_id": status_id,
        "observed_prep_minutes": observed_prep,
        "observed_decision_deadline": None,
    })
    return evt


def _test_flag(name, default=None):
    if name in ("ENABLE_CZASOWKA_CK_PASSIVE_GUARD",
                "ENABLE_PICKUP_TIME_MIRRORS_CK"):
        return True
    return default


print("=== L1 panel_watcher._diff_czas_kuriera: czasówka passive blocked ===")


def t1_diff_czasowka_backward_suppressed():
    from dispatch_v2 import panel_watcher as pw
    old = {"order_type": "czasowka", "prep_minutes": 126, "courier_id": "484",
           "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"}
    fresh = {"czas_kuriera": "15:04", "czas_kuriera_warsaw": _GARBAGE, "czas_kuriera_hhmm": "15:04"}
    evt = pw._diff_czas_kuriera(old, fresh, oid="483023")
    check("1. czasówka panel_re_check backward 16:22→15:04 → None (no emit)", evt is None, f"evt={evt}")


def t2_diff_czasowka_forward_also_suppressed():
    from dispatch_v2 import panel_watcher as pw
    old = {"order_type": "czasowka", "prep_minutes": 126, "courier_id": "484",
           "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"}
    fresh = {"czas_kuriera": "16:35", "czas_kuriera_warsaw": _LATER, "czas_kuriera_hhmm": "16:35"}
    evt = pw._diff_czas_kuriera(old, fresh, oid="483023")
    check("2. czasówka panel_re_check forward 16:22→16:35 → None (gastro re-stamp not authoritative)",
          evt is None, f"evt={evt}")


def t3_diff_elastyk_still_emits():
    from dispatch_v2 import panel_watcher as pw
    old = {"order_type": "elastic", "prep_minutes": 20, "courier_id": "400",
           "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"}
    fresh = {"czas_kuriera": "16:35", "czas_kuriera_warsaw": _LATER, "czas_kuriera_hhmm": "16:35"}
    evt = pw._diff_czas_kuriera(old, fresh, oid="490100")
    check("3. ELASTYK panel_re_check still emits (guard scoped to czasówki)",
          evt is not None and evt.get("event_type") == "CZAS_KURIERA_UPDATED", f"evt={evt}")


print("=== L2 state_machine CZAS_KURIERA_UPDATED: source guard ===")


def t4_sm_czasowka_panel_re_check_skipped():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        res = sm.update_from_event(_ck_evt("483023", _GARBAGE, "15:04", "panel_re_check"))
        got = sm.get_order("483023") or {}
        check("4. czasówka + panel_re_check → skip persist (committed 16:22 kept)",
              res is None and got.get("czas_kuriera_hhmm") == "16:22", f"got={got.get('czas_kuriera_hhmm')}")


def t5_sm_czasowka_pre_proposal_recheck_skipped():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        sm.update_from_event(_ck_evt("483023", _GARBAGE, "15:04", "pre_proposal_recheck"))
        got = sm.get_order("483023") or {}
        check("5. czasówka + pre_proposal_recheck → skip persist (committed kept)",
              got.get("czas_kuriera_hhmm") == "16:22", f"got={got.get('czas_kuriera_hhmm')}")


def t6_sm_czasowka_deliberate_applies():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        sm.update_from_event(_ck_evt("483023", _LATER, "16:35", "ziomek_late_extension"))
        got = sm.get_order("483023") or {}
        check("6. czasówka + ziomek_late_extension (deliberate) → APPLIED (forward)",
              got.get("czas_kuriera_hhmm") == "16:35", f"got={got.get('czas_kuriera_hhmm')}")


def t7_sm_elastyk_panel_re_check_applies():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event(_ck_evt("490100", _LATER, "16:35", "panel_re_check"))
        got = sm.get_order("490100") or {}
        check("7. ELASTYK + panel_re_check → APPLIED (guard scoped to czasówki)",
              got.get("czas_kuriera_hhmm") == "16:35", f"got={got.get('czas_kuriera_hhmm')}")


print("=== PICKUP_TIME_UPDATED mirrors pickup → czas_kuriera (czasówka) ===")


def _pickup_evt(oid, new_pickup):
    return {"event_type": "PICKUP_TIME_UPDATED", "order_id": oid,
            "payload": {"new_pickup_at_warsaw": new_pickup,
                        "old_pickup_at_warsaw": _PICKUP, "source": "panel_re_check"}}


def t8_pickup_forward_mirrors_ck():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        sm.update_from_event(_pickup_evt("483023", _LATER))
        got = sm.get_order("483023") or {}
        check("8. coordinator/restaurant pickup→16:35 (forward) → czas_kuriera follows to 16:35",
              got.get("pickup_at_warsaw") == _LATER and got.get("czas_kuriera_hhmm") == "16:35",
              f"pickup={got.get('pickup_at_warsaw')} ck={got.get('czas_kuriera_hhmm')}")


def t9_pickup_backward_mirrors_ck():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        sm.update_from_event(_pickup_evt("483023", _EARLIER))
        got = sm.get_order("483023") or {}
        check("9. COORDINATOR pickup→16:00 (BACKWARD) → czas_kuriera follows to 16:00 (any direction)",
              got.get("pickup_at_warsaw") == _EARLIER and got.get("czas_kuriera_hhmm") == "16:00",
              f"pickup={got.get('pickup_at_warsaw')} ck={got.get('czas_kuriera_hhmm')}")


def t10_pickup_elastyk_no_mirror():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event(_pickup_evt("490100", _LATER))
        got = sm.get_order("490100") or {}
        check("10. ELASTYK pickup change → czas_kuriera NOT mirrored (stays 16:22)",
              got.get("czas_kuriera_hhmm") == "16:22", f"ck={got.get('czas_kuriera_hhmm')}")


print("=== COURIER_ASSIGNED: czasówka keeps committed czas ===")


def t11_assign_czasowka_keeps_committed():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm, status="planned")
        sm.update_from_event({
            "event_type": "COURIER_ASSIGNED", "order_id": "483023", "courier_id": "484",
            "payload": {"czas_kuriera_warsaw": _GARBAGE, "czas_kuriera_hhmm": "15:04"},
        })
        got = sm.get_order("483023") or {}
        check("11. COURIER_ASSIGNED czasówka w/ re-stamped ck → keep committed 16:22, assign OK",
              got.get("czas_kuriera_hhmm") == "16:22" and got.get("status") == "assigned"
              and got.get("courier_id") == "484",
              f"ck={got.get('czas_kuriera_hhmm')} st={got.get('status')}")


def t12_assign_elastyk_updates_ck():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event({
            "event_type": "COURIER_ASSIGNED", "order_id": "490100", "courier_id": "400",
            "payload": {"czas_kuriera_warsaw": _LATER, "czas_kuriera_hhmm": "16:35"},
        })
        got = sm.get_order("490100") or {}
        check("12. COURIER_ASSIGNED elastyk → czas_kuriera updated to 16:35 (normal)",
              got.get("czas_kuriera_hhmm") == "16:35", f"ck={got.get('czas_kuriera_hhmm')}")


print("=== flag-off rollback + real #483023 replay ===")


def t13_flag_off_passthrough():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        orig = sm.flag
        sm.flag = lambda n, d=None: (False if n == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD" else orig(n, d))
        try:
            sm.update_from_event(_ck_evt("483023", _GARBAGE, "15:04", "panel_re_check"))
        finally:
            sm.flag = orig
        got = sm.get_order("483023") or {}
        check("13. flag OFF → czasówka panel_re_check passes (old behavior, rollback intact)",
              got.get("czas_kuriera_hhmm") == "15:04", f"ck={got.get('czas_kuriera_hhmm')}")


def t14_real_483023_replay():
    """Full incident: gastro re-stamp blocked; then coordinator pulls pickup earlier → ck follows."""
    from dispatch_v2 import panel_watcher as pw, state_machine as sm
    with _TmpState():
        _seed_czasowka(sm)
        # (a) gastro re-stamp via panel_re_check → suppressed at L1
        old = sm.get_order("483023")
        fresh = {"czas_kuriera": "15:04", "czas_kuriera_warsaw": _GARBAGE, "czas_kuriera_hhmm": "15:04"}
        evt = pw._diff_czas_kuriera(old, fresh, oid="483023")
        if evt is not None:
            sm.update_from_event(evt)
        after_garbage = (sm.get_order("483023") or {}).get("czas_kuriera_hhmm")
        # (b) coordinator deliberately moves pickup earlier → ck follows (backward OK)
        sm.update_from_event(_pickup_evt("483023", _EARLIER))
        after_coord = sm.get_order("483023") or {}
        check("14. replay: 15:04 never lands (stays 16:22), THEN coordinator→16:00 follows",
              after_garbage == "16:22" and after_coord.get("czas_kuriera_hhmm") == "16:00",
              f"after_garbage={after_garbage} after_coord={after_coord.get('czas_kuriera_hhmm')}")


print("=== #489052 manual gastro edit passthrough (new flag default OFF) ===")


def _incident_fresh(marker=True, status_id=3):
    return {
        "czas_kuriera": "11:41",
        "czas_kuriera_warsaw": _I489052_MANUAL,
        "czas_kuriera_hhmm": "11:41",
        "pickup_at_warsaw": _I489052_PICKUP,
        "zmiana_czasu_odbioru": marker,
        "status_id": status_id,
        "prep_minutes": 120,
        "decision_deadline": None,
    }


def t15_manual_panel_edit_off_stays_suppressed():
    from dispatch_v2 import common as C, panel_watcher as pw, state_machine as sm
    old = {
        "order_id": "489052", "status": "assigned", "courier_id": "484",
        "order_type": "czasowka", "prep_minutes": 120,
        "pickup_at_warsaw": _I489052_PICKUP,
        "czas_kuriera_warsaw": _I489052_CK, "czas_kuriera_hhmm": "12:01",
        "zmiana_czasu_odbioru": False,
    }
    with patch.object(sm, "decision_flag", return_value=False), \
         patch.object(sm, "flag", side_effect=_test_flag), \
         patch.object(C, "flag", side_effect=_test_flag):
        evt = pw._diff_czas_kuriera(old, _incident_fresh(), oid="489052")
    check("15. nowa flaga OFF: ręczne 12:01→11:41 nadal CK_PASSIVE_SUPPRESSED",
          evt is None, f"evt={evt}")


def t16_manual_panel_edit_on_becomes_pickup_event():
    from dispatch_v2 import common as C, panel_watcher as pw, state_machine as sm
    old = {
        "order_id": "489052", "status": "assigned", "courier_id": "484",
        "order_type": "czasowka", "prep_minutes": 120,
        "pickup_at_warsaw": _I489052_PICKUP,
        "czas_kuriera_warsaw": _I489052_CK, "czas_kuriera_hhmm": "12:01",
        "zmiana_czasu_odbioru": False,
    }
    with patch.object(sm, "decision_flag", return_value=True), \
         patch.object(sm, "flag", side_effect=_test_flag), \
         patch.object(C, "flag", side_effect=_test_flag):
        evt = pw._diff_czas_kuriera(old, _incident_fresh(), oid="489052")
    p = (evt or {}).get("payload", {})
    check("16. flaga ON + marker False→True: panel emituje kanoniczny PICKUP_TIME_UPDATED",
          (evt or {}).get("event_type") == "PICKUP_TIME_UPDATED"
          and p.get("new_pickup_at_warsaw") == _I489052_MANUAL
          and p.get("manual_ck_edit_passthrough") is True,
          f"evt={evt}")


def t17_manual_state_on_updates_pickup_and_courier_app_time():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_489052(sm)
        evt = _manual_ck_evt(
            "489052", _I489052_MANUAL, "11:41",
            observed_pickup=_I489052_PICKUP,
        )
        with patch.object(sm, "decision_flag", return_value=True), \
             patch.object(sm, "flag", side_effect=_test_flag):
            result = sm.update_from_event(evt)
        got = sm.get_order("489052") or {}
        check("17. flaga ON: state deleguje do PICKUP_TIME_UPDATED i lustrzy czas apki",
              result is not None
              and got.get("pickup_at_warsaw") == _I489052_MANUAL
              and got.get("czas_kuriera_warsaw") == _I489052_MANUAL
              and got.get("czas_kuriera_hhmm") == "11:41"
              and (got.get("history") or [{}])[-1].get("event")
              == "PICKUP_TIME_UPDATED",
              f"got={got}")


def t18_gastro_restamp_blocked_off_and_on():
    from dispatch_v2 import state_machine as sm
    outcomes = []
    for enabled in (False, True):
        with _TmpState():
            _seed_489052(sm)
            # Brak krawędzi manualnego markera = statusowy re-stamp.
            evt = _manual_ck_evt(
                "489052", _I489052_MANUAL, "11:41", marker=False,
                observed_pickup=_I489052_PICKUP,
            )
            with patch.object(sm, "decision_flag", return_value=enabled), \
                 patch.object(sm, "flag", side_effect=_test_flag):
                result = sm.update_from_event(evt)
            got = sm.get_order("489052") or {}
            outcomes.append(result is None
                            and got.get("czas_kuriera_hhmm") == "12:01"
                            and got.get("pickup_at_warsaw") == _I489052_PICKUP)
    check("18. śmieciowy re-stamp bez markera zablokowany przy OFF i ON",
          outcomes == [True, True], f"outcomes={outcomes}")


def t19_preproposal_twin_updates_state_and_refreshes_app_view():
    from dispatch_v2 import dispatch_pipeline as dp, state_machine as sm
    with _TmpState():
        _seed_489052(sm)
        fresh_time = {
            "czas_kuriera_warsaw": _I489052_MANUAL,
            "czas_kuriera_hhmm": "11:41",
            "pickup_at_warsaw": _I489052_PICKUP,
            "status_id": 3,
            "prep_minutes": 120,
            "decision_deadline": None,
            "zmiana_czasu_odbioru": True,
        }
        with patch.object(dp.C, "decision_flag", return_value=True), \
             patch.object(dp.C, "flag", side_effect=_test_flag), \
             patch.object(sm, "decision_flag", return_value=True), \
             patch.object(sm, "flag", side_effect=_test_flag), \
             patch("dispatch_v2.event_bus.emit_audit") as audit, \
             patch("dispatch_v2.plan_manager.touch_plan", return_value=True) as touch:
            dp._v327_emit_pre_recheck_event(
                "489052", "484", _I489052_CK, _I489052_MANUAL,
                "11:41", datetime.now(timezone.utc), fresh_time=fresh_time,
            )
        got = sm.get_order("489052") or {}
        audit_type = audit.call_args.args[0] if audit.call_args else None
        check("19. pre_proposal twin: PICKUP event + state mirror + plan_version refresh",
              got.get("pickup_at_warsaw") == _I489052_MANUAL
              and got.get("czas_kuriera_hhmm") == "11:41"
              and audit_type == "PICKUP_TIME_UPDATED"
              and touch.call_count == 1,
              f"got={got} audit={audit_type} touch={touch.call_count}")


def t20_elastyk_unchanged_with_new_flag_on():
    from dispatch_v2 import common as C, panel_watcher as pw, state_machine as sm
    old = {"order_type": "elastic", "prep_minutes": 20, "courier_id": "400",
           "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"}
    fresh = {"czas_kuriera": "16:35", "czas_kuriera_warsaw": _LATER,
             "czas_kuriera_hhmm": "16:35", "zmiana_czasu_odbioru": True,
             "pickup_at_warsaw": _PICKUP, "status_id": 3}
    with patch.object(sm, "decision_flag", return_value=True), \
         patch.object(sm, "flag", side_effect=_test_flag), \
         patch.object(C, "flag", side_effect=_test_flag):
        evt = pw._diff_czas_kuriera(old, fresh, oid="490100")
    check("20. elastyk przy nowej fladze ON zachowuje CZAS_KURIERA_UPDATED",
          (evt or {}).get("event_type") == "CZAS_KURIERA_UPDATED",
          f"evt={evt}")


def t21_new_flag_registered_default_off():
    from dispatch_v2 import common as C
    name = "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
    check("21. nowa flaga jest decyzyjna i ma fallback default OFF",
          name in C.ETAP4_DECISION_FLAGS and getattr(C, name, None) is False)


def t22_manual_signal_mutation_tripwires():
    from dispatch_v2 import common as C, panel_watcher as pw, state_machine as sm
    old = {
        "order_id": "489052", "status": "assigned", "courier_id": "484",
        "order_type": "czasowka", "prep_minutes": 120,
        "pickup_at_warsaw": _I489052_PICKUP,
        "czas_kuriera_warsaw": _I489052_CK, "czas_kuriera_hhmm": "12:01",
        "zmiana_czasu_odbioru": False,
    }
    changed_pickup = dict(_incident_fresh(),
                          pickup_at_warsaw="2026-07-20T11:40:00+02:00")
    changed_status = _incident_fresh(status_id=5)
    with patch.object(sm, "decision_flag", return_value=True), \
         patch.object(sm, "flag", side_effect=_test_flag), \
         patch.object(C, "flag", side_effect=_test_flag):
        pickup_evt = pw._diff_czas_kuriera(old, changed_pickup, oid="489052")
        status_evt = pw._diff_czas_kuriera(old, changed_status, oid="489052")
    check("22. mutation: zmieniony pickup lub status nie może udawać standalone manual CK",
          pickup_evt is None and status_evt is None,
          f"pickup_evt={pickup_evt} status_evt={status_evt}")


def t23_app_refresh_respects_existing_kill_switch():
    from dispatch_v2 import dispatch_pipeline as dp
    with patch.object(dp.C, "ENABLE_SAVED_PLANS", True), \
         patch("dispatch_v2.plan_manager.touch_plan", return_value=True) as touch:
        with patch.object(dp.C, "flag", return_value=False):
            dp._v327_touch_committed_view("489052", "484")
        blocked_calls = touch.call_count
        with patch.object(dp.C, "flag", return_value=True):
            dp._v327_touch_committed_view("489052", "484")
        enabled_calls = touch.call_count
    check("23. refresh apki respektuje ENABLE_COMMITTED_INVALIDATES_VIEW OFF/ON",
          blocked_calls == 0 and enabled_calls == 1,
          f"blocked={blocked_calls} enabled={enabled_calls}")


def t24_preproposal_fetch_off_is_exact_legacy_tuple():
    from dispatch_v2 import dispatch_pipeline as dp
    snapshot = {
        "czas_kuriera_warsaw": _I489052_MANUAL,
        "czas_kuriera_hhmm": "11:41",
    }
    with patch.object(dp, "_v327_safe_fetch_order_time", return_value=snapshot), \
         patch.object(dp.C, "decision_flag", return_value=False):
        got = dp._v327_safe_fetch_czas_kuriera("489052")
    check("24. nowa flaga OFF: preproposal helper zwraca dokładnie zwykły legacy tuple",
          type(got) is tuple and got == (_I489052_MANUAL, "11:41")
          and not hasattr(got, "fresh_time"),
          f"type={type(got)} got={got}")


for lbl, fn in [
    ("t1", t1_diff_czasowka_backward_suppressed), ("t2", t2_diff_czasowka_forward_also_suppressed),
    ("t3", t3_diff_elastyk_still_emits), ("t4", t4_sm_czasowka_panel_re_check_skipped),
    ("t5", t5_sm_czasowka_pre_proposal_recheck_skipped), ("t6", t6_sm_czasowka_deliberate_applies),
    ("t7", t7_sm_elastyk_panel_re_check_applies), ("t8", t8_pickup_forward_mirrors_ck),
    ("t9", t9_pickup_backward_mirrors_ck), ("t10", t10_pickup_elastyk_no_mirror),
    ("t11", t11_assign_czasowka_keeps_committed), ("t12", t12_assign_elastyk_updates_ck),
    ("t13", t13_flag_off_passthrough), ("t14", t14_real_483023_replay),
    ("t15", t15_manual_panel_edit_off_stays_suppressed),
    ("t16", t16_manual_panel_edit_on_becomes_pickup_event),
    ("t17", t17_manual_state_on_updates_pickup_and_courier_app_time),
    ("t18", t18_gastro_restamp_blocked_off_and_on),
    ("t19", t19_preproposal_twin_updates_state_and_refreshes_app_view),
    ("t20", t20_elastyk_unchanged_with_new_flag_on),
    ("t21", t21_new_flag_registered_default_off),
    ("t22", t22_manual_signal_mutation_tripwires),
    ("t23", t23_app_refresh_respects_existing_kill_switch),
    ("t24", t24_preproposal_fetch_off_is_exact_legacy_tuple),
]:
    run(lbl, fn)

print(f"\n=== czasowka_pickup_authority: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
