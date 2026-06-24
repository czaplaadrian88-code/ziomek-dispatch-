"""Elastyk forward-only committed czas_kuriera (Adrian 2026-06-24, opcja B).

Elastyk czas_kuriera = żywy szacunek przyjazdu, ale apka pokazuje go jako
obietnicę. Gastro przelicza ETA → pasywny re-odczyt (panel_re_check) wobble'uje
go w obie strony. Decyzja Adriana (opcja B): blokuj TYLKO COFNIĘCIA pasywne
(„przyjazd wcześniej niż umówiono" = jednoznaczny śmieć, 5/75 w 5 dni); forward
zostaje (koordynatorski +15 / realne spóźnienie). Czasówki = osobny mocniejszy
guard (pickup_at authority, dowolny kierunek pasywny blokowany).
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
        self.tmpdir = tempfile.mkdtemp(prefix="elastyk_ck_")
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


_CK = "2026-06-24T16:22:00+02:00"
_EARLIER = "2026-06-24T16:10:00+02:00"   # backward (−12) = garbage
_LATER = "2026-06-24T16:37:00+02:00"     # forward (+15) = legit +15 / lateness


def _seed_elastyk(sm, oid="490100"):
    sm.upsert_order(oid, {
        "status": "assigned", "courier_id": "400", "order_type": "elastic",
        "prep_minutes": 20,
        "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22",
    }, event="COURIER_ASSIGNED")


def _ck_evt(oid, new_iso, new_hhmm, source):
    return {"event_type": "CZAS_KURIERA_UPDATED", "order_id": oid, "courier_id": "400",
            "payload": {"oid": oid, "courier_id": "400", "old_ck_iso": _CK, "old_ck_hhmm": "16:22",
                        "new_ck_iso": new_iso, "new_ck_hhmm": new_hhmm, "delta_min": None, "source": source}}


print("=== L2 state_machine: elastyk forward-only (passive) ===")


def t1_elastyk_backward_passive_blocked():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        res = sm.update_from_event(_ck_evt("490100", _EARLIER, "16:10", "panel_re_check"))
        got = sm.get_order("490100") or {}
        check("1. elastyk panel_re_check BACKWARD 16:22→16:10 → BLOCKED (committed kept)",
              res is None and got.get("czas_kuriera_hhmm") == "16:22", f"ck={got.get('czas_kuriera_hhmm')}")


def t2_elastyk_forward_passive_applied():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event(_ck_evt("490100", _LATER, "16:37", "panel_re_check"))
        got = sm.get_order("490100") or {}
        check("2. elastyk panel_re_check FORWARD 16:22→16:37 → APPLIED (+15/lateness kept)",
              got.get("czas_kuriera_hhmm") == "16:37", f"ck={got.get('czas_kuriera_hhmm')}")


def t3_elastyk_backward_pre_proposal_blocked():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event(_ck_evt("490100", _EARLIER, "16:10", "pre_proposal_recheck"))
        got = sm.get_order("490100") or {}
        check("3. elastyk pre_proposal_recheck BACKWARD → BLOCKED",
              got.get("czas_kuriera_hhmm") == "16:22", f"ck={got.get('czas_kuriera_hhmm')}")


def t4_elastyk_backward_deliberate_applied():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        sm.update_from_event(_ck_evt("490100", _EARLIER, "16:10", "coordinator_edit"))
        got = sm.get_order("490100") or {}
        check("4. elastyk DELIBERATE (coordinator_edit) backward → APPLIED (any direction)",
              got.get("czas_kuriera_hhmm") == "16:10", f"ck={got.get('czas_kuriera_hhmm')}")


def t5_elastyk_assign_backward_applied():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        # reassignment to a closer courier → earlier arrival is legit
        sm.update_from_event({"event_type": "COURIER_ASSIGNED", "order_id": "490100",
                              "courier_id": "999",
                              "payload": {"czas_kuriera_warsaw": _EARLIER, "czas_kuriera_hhmm": "16:10"}})
        got = sm.get_order("490100") or {}
        check("5. elastyk COURIER_ASSIGNED backward (reassign new courier) → APPLIED",
              got.get("czas_kuriera_hhmm") == "16:10" and got.get("courier_id") == "999",
              f"ck={got.get('czas_kuriera_hhmm')}")


def t6_czasowka_still_blocked_forward():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        sm.upsert_order("483023", {"status": "assigned", "courier_id": "484",
                                   "order_type": "czasowka", "prep_minutes": 126,
                                   "pickup_at_warsaw": "2026-06-24T16:21:22+02:00",
                                   "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"},
                        event="COURIER_ASSIGNED")
        sm.update_from_event(_ck_evt("483023", _LATER, "16:37", "panel_re_check"))
        got = sm.get_order("483023") or {}
        check("6. CZASÓWKA panel_re_check forward → STILL blocked (czasówka guard, any dir)",
              got.get("czas_kuriera_hhmm") == "16:22", f"ck={got.get('czas_kuriera_hhmm')}")


def t7_flag_off_passthrough():
    from dispatch_v2 import state_machine as sm
    with _TmpState():
        _seed_elastyk(sm)
        orig = sm.flag
        sm.flag = lambda n, d=None: (False if n == "ENABLE_ELASTYK_CK_NO_BACKWARD" else orig(n, d))
        try:
            sm.update_from_event(_ck_evt("490100", _EARLIER, "16:10", "panel_re_check"))
        finally:
            sm.flag = orig
        got = sm.get_order("490100") or {}
        check("7. flag OFF → elastyk backward passes (rollback intact)",
              got.get("czas_kuriera_hhmm") == "16:10", f"ck={got.get('czas_kuriera_hhmm')}")


print("=== L1 panel_watcher._diff_czas_kuriera ===")


def _state(order_type, prep):
    return {"order_type": order_type, "prep_minutes": prep, "courier_id": "400",
            "czas_kuriera_warsaw": _CK, "czas_kuriera_hhmm": "16:22"}


def _fresh(iso, hhmm):
    return {"czas_kuriera": hhmm, "czas_kuriera_warsaw": iso, "czas_kuriera_hhmm": hhmm}


def t8_diff_elastyk_backward_none():
    from dispatch_v2 import panel_watcher as pw
    evt = pw._diff_czas_kuriera(_state("elastic", 20), _fresh(_EARLIER, "16:10"), oid="490100")
    check("8. _diff elastyk BACKWARD → None (suppressed)", evt is None, f"evt={evt}")


def t9_diff_elastyk_forward_emits():
    from dispatch_v2 import panel_watcher as pw
    evt = pw._diff_czas_kuriera(_state("elastic", 20), _fresh(_LATER, "16:37"), oid="490100")
    check("9. _diff elastyk FORWARD → emits (legit +15/lateness)",
          evt is not None and evt.get("event_type") == "CZAS_KURIERA_UPDATED", f"evt={evt}")


def t10_diff_czasowka_any_none():
    from dispatch_v2 import panel_watcher as pw
    evt = pw._diff_czas_kuriera(_state("czasowka", 126), _fresh(_LATER, "16:37"), oid="483023")
    check("10. _diff czasówka forward → None (czasówka guard, any direction)", evt is None, f"evt={evt}")


for lbl, fn in [
    ("t1", t1_elastyk_backward_passive_blocked), ("t2", t2_elastyk_forward_passive_applied),
    ("t3", t3_elastyk_backward_pre_proposal_blocked), ("t4", t4_elastyk_backward_deliberate_applied),
    ("t5", t5_elastyk_assign_backward_applied), ("t6", t6_czasowka_still_blocked_forward),
    ("t7", t7_flag_off_passthrough), ("t8", t8_diff_elastyk_backward_none),
    ("t9", t9_diff_elastyk_forward_emits), ("t10", t10_diff_czasowka_any_none),
]:
    run(lbl, fn)

print(f"\n=== elastyk_ck_no_backward: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
