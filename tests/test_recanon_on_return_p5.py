"""P-5 (audyt 2026-06-24): symetria recanon-on-write — cancel/return path też re-egzekwuje kanon.

Bug: `_advance_plan_on_deliver` / `_update_plan_on_picked_up` / `_save_plan_on_assign_signal`
wołały `plan_recheck.recanon_courier`, ale `_remove_stops_on_return` (anulowanie/zwrot zlecenia)
NIE → plan zostawał niezkanonizowany (niesione nie na froncie / okno committed nieuwzględnione)
do następnego 5-min ticku. Fix: dołożenie recanon (reason='return'), symetrycznie. Self-gating
na ENABLE_RECANON_ON_WRITE, best-effort → no-op gdy worek pusty / flaga OFF.
"""
import types
import dispatch_v2.panel_watcher as PW
import dispatch_v2.common as C
import dispatch_v2.plan_manager as PM
import dispatch_v2.plan_recheck as PR


def test_return_calls_recanon(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)
    removed = {}
    monkeypatch.setattr(PM, "remove_stops", lambda cid, oid: removed.update(cid=cid, oid=oid))
    calls = []
    monkeypatch.setattr(PR, "recanon_courier",
                        lambda cid, **kw: calls.append((cid, kw.get("reason"))) or True)
    PW._remove_stops_on_return("207", "999001")
    assert removed == {"cid": "207", "oid": "999001"}      # stop usunięty
    assert calls == [("207", "return")], "cancel-path MUSI wołać recanon (reason='return')"


def test_return_recanon_failure_is_swallowed(monkeypatch):
    # recanon best-effort — wyjątek nie może wywrócić handlera (≤ stan sprzed fixu)
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(PM, "remove_stops", lambda cid, oid: None)
    def _boom(cid, **kw):
        raise RuntimeError("gps missing")
    monkeypatch.setattr(PR, "recanon_courier", _boom)
    PW._remove_stops_on_return("207", "999002")            # nie rzuca


def test_return_noop_when_saved_plans_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", False)
    calls = []
    monkeypatch.setattr(PR, "recanon_courier", lambda cid, **kw: calls.append(cid))
    PW._remove_stops_on_return("207", "999003")
    assert calls == [], "ENABLE_SAVED_PLANS OFF → handler wczesny return, brak recanon"


def test_all_four_write_handlers_call_recanon():
    """Strażnik symetrii: każdy handler dochodzi do jednego recanonu P-5."""
    import inspect
    for fn in (PW._save_plan_on_assign_signal, PW._advance_plan_on_deliver,
               PW._update_plan_on_picked_up):
        assert "recanon_courier" in inspect.getsource(fn), \
            f"{fn.__name__} musi wołać recanon_courier (symetria P-5)"
    # RETURN/REASSIGN oddzielają szybki CAS cleanup pod state lockiem od
    # wolnego recanonu. Delegacja nadal jest obowiązkowa i wspólna dla obu.
    assert "_recanon_after_plan_cleanup" in inspect.getsource(
        PW._remove_stops_on_return
    )
    assert "recanon_courier" in inspect.getsource(
        PW._recanon_after_plan_cleanup
    )
