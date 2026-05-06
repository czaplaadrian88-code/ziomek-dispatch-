"""TASK A CZASÓWKI PROACTIVE — handlers.py callback tests (2026-05-05).

Custom-runner pattern (matches test_shift_telegram_router.py — no pytest dep).

Coverage (12 tests):
  TAK (5):
    1. tak_master_flag_off_silent
    2. tak_malformed_raw_oid
    3. tak_idempotent_double_tap
    4. tak_race_lost_detected (id_kurier != 26)
    5. tak_happy_path_panel_assign + state mutate
  NIE (2):
    6. nie_excludes_cid_per_czasowka
    7. nie_decision_persisted
  CZEKAJ (2):
    8. czekaj_no_exclusion
    9. czekaj_decision_persisted
  Edit/router (3):
   10. edit_message_called_after_decision
   11. chat_id_unauthorized (router level — already tested w test_shift_telegram_router)
        ↳ replaced z: callback_router_czas_tak_dispatches_to_handler
   12. handler_writes_state_atomically (concurrent click)
"""
import sys
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import asyncio
import shutil
import tempfile
from contextlib import contextmanager

from dispatch_v2 import telegram_approver
from dispatch_v2.czasowka_proactive import handlers as czas_handlers
from dispatch_v2.czasowka_proactive import state as czas_state


passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ---------------- helpers ----------------

@contextmanager
def isolated_czas_state():
    """Redirect czas_state.STATE_PATH + LOCK_PATH to fresh tmpdir.

    Mirror tests/_shift_test_helpers.py pattern. Original paths captured
    in __enter__ (NIE __init__) per Lekcja #71.
    """
    tmpdir = tempfile.mkdtemp(prefix="czas_test_")
    orig_state = czas_state.STATE_PATH
    orig_lock = czas_state.LOCK_PATH
    state_file = Path(tmpdir) / "czasowka_proposals_state.json"
    lock_file = Path(tmpdir) / "czasowka_proposals_state.json.lock"
    czas_state.STATE_PATH = state_file
    czas_state.LOCK_PATH = lock_file
    try:
        yield Path(tmpdir)
    finally:
        czas_state.STATE_PATH = orig_state
        czas_state.LOCK_PATH = orig_lock
        shutil.rmtree(tmpdir, ignore_errors=True)


class _FlagOverride:
    """Patch czas_handlers.flag(name, default) for test scope."""
    def __init__(self, **kwargs):
        self.values = kwargs

    def __enter__(self):
        self.orig = czas_handlers.flag
        def fake_flag(name, default=False):
            return self.values.get(name, default)
        czas_handlers.flag = fake_flag
        return self

    def __exit__(self, exc_type, exc, tb):
        czas_handlers.flag = self.orig


class _TGCapture:
    """Stub telegram_approver.tg_request to capture all outgoing telegram calls."""
    def __init__(self):
        self.calls = []

    def __enter__(self):
        self.orig = telegram_approver.tg_request
        def fake(token, method, payload=None, timeout=35):
            self.calls.append({"method": method, "payload": payload})
            return {"ok": True, "result": {"message_id": 1}}
        telegram_approver.tg_request = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        telegram_approver.tg_request = self.orig


class _PanelClientStub:
    """Stub dispatch_v2.panel_client.fetch_order_details for race-precheck.

    Constructor args:
      id_kurier: value to return as raw_order["id_kurier"] (default "26")
      raise_exc: if not None, raise this exception
      return_none: if True, fetch returns None
    """
    def __init__(self, id_kurier="26", raise_exc=None, return_none=False):
        self.id_kurier = id_kurier
        self.raise_exc = raise_exc
        self.return_none = return_none
        self.fetch_calls = []

    def __enter__(self):
        from dispatch_v2 import panel_client
        self.module = panel_client
        self.orig = panel_client.fetch_order_details
        stub = self
        def fake(zid, csrf=None, timeout=10):
            stub.fetch_calls.append(zid)
            if stub.raise_exc is not None:
                raise stub.raise_exc
            if stub.return_none:
                return None
            return {"id_kurier": stub.id_kurier, "id_status_zamowienia": 2}
        panel_client.fetch_order_details = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        self.module.fetch_order_details = self.orig


class _SubprocessStub:
    """Stub telegram_approver.run_gastro_assign (subprocess wrapper).

    Constructor args:
      ok: bool (gastro_assign success)
      msg: response string
    """
    def __init__(self, ok=True, msg="ok"):
        self.ok = ok
        self.msg = msg
        self.calls = []

    def __enter__(self):
        self.orig = telegram_approver.run_gastro_assign
        stub = self
        def fake(order_id, kurier_name, time_minutes=0, koordynator=False):
            stub.calls.append({
                "order_id": order_id, "kurier_name": kurier_name,
                "time_minutes": time_minutes, "koordynator": koordynator,
            })
            return (stub.ok, stub.msg)
        telegram_approver.run_gastro_assign = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        telegram_approver.run_gastro_assign = self.orig


class _NameLookupStub:
    """Stub telegram_approver.name_lookup → predictable cid→name."""
    def __init__(self, mapping=None):
        self.mapping = mapping or {"413": "Mateusz O.", "555": "Adrian R.", "999": "Mykyta K."}

    def __enter__(self):
        self.orig = telegram_approver.name_lookup
        m = self.mapping
        def fake(cid, existing_name=None):
            if existing_name:
                return existing_name
            return m.get(str(cid), f"K{cid}")
        telegram_approver.name_lookup = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        telegram_approver.name_lookup = self.orig


def _seed_order(oid="469900", id_kurier_holding="26"):
    """Seed czasowka_proposals_state with one order record."""
    with czas_state.locked_write_proposals_state() as proposals:
        orders = proposals.setdefault("orders", {})
        orders[oid] = {
            "first_seen_ts": "2026-05-05T10:00:00+00:00",
            "czas_odbioru_ts": "2026-05-05T13:00:00+00:00",
            "id_kurier_holding": id_kurier_holding,
            "restaurant": "Mama Thai",
            "delivery_address": "Mickiewicza 17",
            "delivery_city": "Białystok",
            "triggers_fired": {},
            "excluded_candidates": [],
            "final_assignment_cid": None,
            "final_assignment_ts": None,
        }


def _make_state(admin_id="123456"):
    return {
        "token": "fake_token",
        "admin_id": admin_id,
        "pending": {},
        "pending_path": "/tmp/fake_pending.json",
        "learning_log_path": "/tmp/fake_learning.jsonl",
    }


def _make_cb(action, raw_oid, admin_id="123456", message_id=999):
    return {
        "id": "test_cb_id",
        "data": f"{action}:{raw_oid}",
        "from": {"id": 8765130486, "first_name": "Adrian"},
        "message": {"chat": {"id": int(admin_id)}, "message_id": message_id},
    }


# ============================================================
# 1: TAK — master flag off → silent "wyłączone"
# ============================================================

def test_tak_master_flag_off_silent():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=False), \
         _TGCapture() as tg, _NameLookupStub():
        st = _make_state()
        cb = _make_cb("CZAS_TAK", "469900:413:50")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb)
        # No subprocess assign call expected (early exit)
        # Only answerCallbackQuery with "wyłączone"
        feedback = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert len(feedback) == 1, f"expected 1 cb answer, got {tg.calls}"
        assert "wyłączone" in feedback[0]["payload"]["text"]
t("tak_master_flag_off_silent", test_tak_master_flag_off_silent)


# ============================================================
# 2: TAK — malformed raw_oid → error feedback
# ============================================================

def test_tak_malformed_raw_oid():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub():
        st = _make_state()
        # only 2 segments instead of 3
        cb = _make_cb("CZAS_TAK", "469900:413")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413", cb)
        feedback = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert any("malformed" in c["payload"]["text"].lower() for c in feedback), \
            f"expected malformed feedback: {tg.calls}"
        # trigger_min not int
        tg.calls.clear()
        cb = _make_cb("CZAS_TAK", "469900:413:notnum")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:notnum", cb)
        feedback = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert any("malformed" in c["payload"]["text"].lower() for c in feedback)
t("tak_malformed_raw_oid", test_tak_malformed_raw_oid)


# ============================================================
# 3: TAK — idempotent double-tap (decision != None)
# ============================================================

def test_tak_idempotent_double_tap():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub(), \
         _PanelClientStub(id_kurier="26") as panel, \
         _SubprocessStub(ok=True, msg="ok") as subp:
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_TAK", "469900:413:50")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb)
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb)
        # subprocess fired only once
        assert len(subp.calls) == 1, \
            f"expected 1 subprocess call (idempotent guard), got {subp.calls}"
        # second answer == "już zapisane"
        feedback_texts = [c["payload"]["text"] for c in tg.calls
                          if c["method"] == "answerCallbackQuery"]
        # second answerCallbackQuery — szukamy "już zapisane" lub "ℹ"
        assert any("już zapisane" in t or "ℹ" in t for t in feedback_texts), \
            f"second click should be idempotent: {feedback_texts}"
        # State final_assignment_cid set once
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        assert rec["final_assignment_cid"] == "413", f"final cid mismatch: {rec}"
        assert rec["triggers_fired"]["50"]["decision"] == "TAK"
t("tak_idempotent_double_tap", test_tak_idempotent_double_tap)


# ============================================================
# 4: TAK — race lost (panel id_kurier != 26)
# ============================================================

def test_tak_race_lost_detected():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub(), \
         _PanelClientStub(id_kurier="555") as panel, \
         _SubprocessStub(ok=True, msg="ok") as subp:
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_TAK", "469900:413:50")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb)
        # subprocess MUST NOT fire (race lost)
        assert len(subp.calls) == 0, f"race lost — no panel assign expected, got {subp.calls}"
        # Adrian Z3 (2026-05-05): RACE_LOST split into 3 distinct decisions:
        #   RACE_LOST_ALREADY_ASSIGNED — id_kurier=other real cid
        #   REJECT_RACE_ID_KURIER_NONE — id_kurier=None anomalia, REJECT
        #   REJECT_RACE_FETCH_NONE — fetch_order_details returned None
        # Tu test stub returns id_kurier="555" → RACE_LOST_ALREADY_ASSIGNED.
        feedback = [c["payload"]["text"] for c in tg.calls
                    if c["method"] == "answerCallbackQuery"]
        assert any("już przypisane" in f or "555" in f for f in feedback), \
            f"missing 'już przypisane' / '555': {feedback}"
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        decision = rec["triggers_fired"]["50"]["decision"]
        assert decision == "RACE_LOST_ALREADY_ASSIGNED", \
            f"expected RACE_LOST_ALREADY_ASSIGNED, got {decision}"
        assert rec["final_assignment_cid"] is None
t("tak_race_lost_detected", test_tak_race_lost_detected)


# ============================================================
# 5: TAK — happy path panel assign + state mutate + edit message
# ============================================================

def test_tak_happy_path_panel_assign():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub(), \
         _PanelClientStub(id_kurier="26") as panel, \
         _SubprocessStub(ok=True, msg="assigned ok") as subp:
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_TAK", "469900:413:50")
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb)
        # subprocess called z poprawnymi args
        assert len(subp.calls) == 1
        call = subp.calls[0]
        assert call["order_id"] == "469900"
        assert call["kurier_name"] == "Mateusz O."
        assert call["koordynator"] is False
        # State mutated
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        sub = rec["triggers_fired"]["50"]
        assert sub["decision"] == "TAK", f"sub={sub}"
        assert sub["proposed_cid"] == "413"
        assert sub["proposed_name"] == "Mateusz O."
        assert rec["final_assignment_cid"] == "413"
        assert rec["final_assignment_ts"] is not None
        # editMessageText fired
        assert any(c["method"] == "editMessageText" for c in tg.calls), \
            f"no editMessageText: {[c['method'] for c in tg.calls]}"
t("tak_happy_path_panel_assign", test_tak_happy_path_panel_assign)


# ============================================================
# 6: NIE — excludes cid per-czasówka
# ============================================================

def test_nie_excludes_cid_per_czasowka():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub():
        _seed_order(oid="469900")
        # Drugi order — exclusion NIE może leakować
        _seed_order(oid="470001")
        st = _make_state()
        cb = _make_cb("CZAS_NIE", "469900:413:50")
        czas_handlers.handle_czas_nie(st, "CZAS_NIE", "469900:413:50", cb)
        result = czas_state.read_proposals_state()
        assert "413" in result["orders"]["469900"]["excluded_candidates"]
        # OTHER order excludeded list pusty (per-czasówka isolation)
        assert "413" not in result["orders"]["470001"]["excluded_candidates"]
t("nie_excludes_cid_per_czasowka", test_nie_excludes_cid_per_czasowka)


# ============================================================
# 7: NIE — decision persisted (incl. T-40 reuse)
# ============================================================

def test_nie_decision_persisted():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub():
        _seed_order(oid="469900")
        st = _make_state()
        # T-50 NIE
        cb50 = _make_cb("CZAS_NIE", "469900:413:50")
        czas_handlers.handle_czas_nie(st, "CZAS_NIE", "469900:413:50", cb50)
        # T-40 NIE (different cid for next candidate)
        cb40 = _make_cb("CZAS_NIE", "469900:555:40")
        czas_handlers.handle_czas_nie(st, "CZAS_NIE", "469900:555:40", cb40)
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        assert rec["triggers_fired"]["50"]["decision"] == "NIE"
        assert rec["triggers_fired"]["40"]["decision"] == "NIE"
        assert "413" in rec["excluded_candidates"]
        assert "555" in rec["excluded_candidates"]
        # final_assignment NIE ustawione (NIE != assignment)
        assert rec["final_assignment_cid"] is None
t("nie_decision_persisted", test_nie_decision_persisted)


# ============================================================
# 8: CZEKAJ — NO exclusion (kandydat może wrócić w T-40)
# ============================================================

def test_czekaj_no_exclusion():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub():
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_CZEKAJ", "469900:413:50")
        czas_handlers.handle_czas_czekaj(st, "CZAS_CZEKAJ", "469900:413:50", cb)
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        assert rec["triggers_fired"]["50"]["decision"] == "CZEKAJ"
        # excluded_candidates MUSI być empty (CZEKAJ != exclusion)
        assert rec["excluded_candidates"] == [], \
            f"CZEKAJ should NOT exclude cid: {rec['excluded_candidates']}"
        assert rec["final_assignment_cid"] is None
t("czekaj_no_exclusion", test_czekaj_no_exclusion)


# ============================================================
# 9: CZEKAJ — decision persisted, feedback contains "T-40"
# ============================================================

def test_czekaj_decision_persisted():
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub():
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_CZEKAJ", "469900:413:50")
        czas_handlers.handle_czas_czekaj(st, "CZAS_CZEKAJ", "469900:413:50", cb)
        # answerCallbackQuery feedback wspomina T-40
        feedback = [c["payload"]["text"] for c in tg.calls
                    if c["method"] == "answerCallbackQuery"]
        assert any("T-40" in f or "Czekaj" in f or "re-eval" in f for f in feedback), \
            f"missing CZEKAJ feedback: {feedback}"
t("czekaj_decision_persisted", test_czekaj_decision_persisted)


# ============================================================
# 10: editMessageText fired after every decision (TAK/NIE/CZEKAJ)
# ============================================================

def test_edit_message_called_after_decision():
    for action, fn in [
        ("CZAS_TAK", czas_handlers.handle_czas_tak),
        ("CZAS_NIE", czas_handlers.handle_czas_nie),
        ("CZAS_CZEKAJ", czas_handlers.handle_czas_czekaj),
    ]:
        with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
             _TGCapture() as tg, _NameLookupStub(), \
             _PanelClientStub(id_kurier="26"), \
             _SubprocessStub(ok=True, msg="ok"):
            _seed_order(oid="469900")
            st = _make_state()
            cb = _make_cb(action, "469900:413:50")
            fn(st, action, "469900:413:50", cb)
            edit_calls = [c for c in tg.calls if c["method"] == "editMessageText"]
            assert len(edit_calls) >= 1, \
                f"action={action} expected editMessageText, got {[c['method'] for c in tg.calls]}"
            payload = edit_calls[0]["payload"]
            assert payload["reply_markup"]["inline_keyboard"] == [], \
                f"keyboard should be stripped: {payload}"
t("edit_message_called_after_decision", test_edit_message_called_after_decision)


# ============================================================
# 11: Router-level fork — handle_callback dispatches CZAS_* to handler
# ============================================================

def test_callback_router_czas_tak_dispatches_to_handler():
    """handle_callback w telegram_approver musi krótko zwarciem fork-ować
    CZAS_TAK do handlers.handle_czas_tak (no fall-through na pending lookup).
    """
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub(), \
         _PanelClientStub(id_kurier="26"), \
         _SubprocessStub(ok=True, msg="ok") as subp:
        _seed_order(oid="469900")
        st = _make_state()
        cb = _make_cb("CZAS_TAK", "469900:413:50")
        # Dispatcher entry point
        asyncio.run(telegram_approver.handle_callback(st, "CZAS_TAK", "469900:413:50", cb))
        # subprocess called → handler reached
        assert len(subp.calls) == 1, f"router did not dispatch to handler: {subp.calls}"
        # State mutated
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        assert rec["triggers_fired"]["50"]["decision"] == "TAK"
t("callback_router_czas_tak_dispatches_to_handler",
  test_callback_router_czas_tak_dispatches_to_handler)


# ============================================================
# 12: Concurrent click — second writer sees idempotent (locked write order)
# ============================================================

def test_handler_writes_state_atomically():
    """Two sequential clicks (same cb, same oid) — first wins TAK, second
    hits idempotent guard. Verifies fcntl.LOCK_EX context releases properly."""
    with isolated_czas_state(), _FlagOverride(CZASOWKA_PROACTIVE_ENABLED=True), \
         _TGCapture() as tg, _NameLookupStub(), \
         _PanelClientStub(id_kurier="26"), \
         _SubprocessStub(ok=True, msg="ok") as subp:
        _seed_order(oid="469900")
        st = _make_state()
        # 1st: TAK
        cb1 = _make_cb("CZAS_TAK", "469900:413:50", message_id=1)
        czas_handlers.handle_czas_tak(st, "CZAS_TAK", "469900:413:50", cb1)
        # 2nd: CZEKAJ — should hit idempotent (decision already set)
        cb2 = _make_cb("CZAS_CZEKAJ", "469900:413:50", message_id=2)
        czas_handlers.handle_czas_czekaj(st, "CZAS_CZEKAJ", "469900:413:50", cb2)
        result = czas_state.read_proposals_state()
        rec = result["orders"]["469900"]
        # First write wins (TAK)
        assert rec["triggers_fired"]["50"]["decision"] == "TAK", \
            f"first write should win (TAK), got {rec}"
        # subprocess fired only once (z TAK call)
        assert len(subp.calls) == 1
t("handler_writes_state_atomically", test_handler_writes_state_atomically)


# ============================================================
print(f"\n=== test_czasowka_proactive_handlers: {passed} PASSED / {failed} FAILED ===")
sys.exit(0 if failed == 0 else 1)
