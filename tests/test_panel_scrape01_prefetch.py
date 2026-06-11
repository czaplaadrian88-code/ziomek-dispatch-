"""PANEL-SCRAPE-01 (2026-06-12): testy równoległego pre-fetchu detali.

Pokrycie:
  - _build_prefetch_candidates: predykaty lustrzane do pętli _diff_and_emit
    (nowe / zniknięte / planned→assigned / scope re-checku czasów / freeze_new).
  - prefetch_details: kill-switch OFF → pusta mapa; chunking bez współdzielenia
    sesji; semantyka miss (exception → zid poza mapą); legit None W mapie;
    deadline; nigdy nie rzuca.
Bez sieci: _WorkerSession.fetch / _perform_login mockowane.
"""
import threading
import time

import pytest

from dispatch_v2 import panel_detail_prefetch as pdp
from dispatch_v2.panel_watcher import _build_prefetch_candidates


# ───────────────────────── _build_prefetch_candidates ─────────────────────────

def _parsed(order_ids=(), assigned=()):
    return {"order_ids": list(order_ids), "assigned_ids": set(assigned)}


def test_candidates_new_orders():
    parsed = _parsed(order_ids=["1", "2", "3"])
    state = {"2": {"status": "planned"}}
    out = _build_prefetch_candidates(parsed, state, {"3"}, False, False, False)
    # 1 = nowe; 2 znane (planned, nie-assigned, brak detekcji czasów); 3 ignorowane
    assert out == ["1"]


def test_candidates_freeze_new_blocks_new():
    parsed = _parsed(order_ids=["1"])
    out = _build_prefetch_candidates(parsed, {}, set(), True, False, False)
    assert out == []


def test_candidates_disappeared_active():
    parsed = _parsed(order_ids=["9"])
    state = {
        "5": {"status": "assigned"},      # zniknęło → fetch
        "6": {"status": "delivered"},     # terminal → skip
        "9": {"status": "planned"},       # w HTML, bez przejścia → skip
    }
    out = _build_prefetch_candidates(parsed, state, set(), False, False, False)
    assert out == ["5"]


def test_candidates_planned_to_assigned_transition():
    parsed = _parsed(order_ids=["7"], assigned=["7"])
    state = {"7": {"status": "planned"}}
    out = _build_prefetch_candidates(parsed, state, set(), False, False, False)
    assert out == ["7"]


def test_candidates_order_time_scope_gated():
    parsed = _parsed(order_ids=["1", "2", "3", "4"])
    state = {
        "1": {"status": "assigned"},
        "2": {"status": "picked_up"},
        "3": {"status": "planned", "order_type": "czasowka"},
        "4": {"status": "planned"},  # elastyk planned → poza scope
    }
    # detekcje OFF → scope pusty (1/2 nie wchodzą mimo statusu)
    out_off = _build_prefetch_candidates(parsed, state, set(), False, False, False)
    assert out_off == []
    # detekcja ON → assigned/picked_up + planned czasówka
    out_on = _build_prefetch_candidates(parsed, state, set(), False, True, False)
    assert out_on == ["1", "2", "3"]
    # assigned w panelu (planned→assigned) nie dubluje wpisu (dedupe);
    # 1/2/4 poza order_ids parsed2 → zniknięte aktywne → też fetch (po razie)
    parsed2 = _parsed(order_ids=["3"], assigned=["3"])
    out2 = _build_prefetch_candidates(parsed2, state, set(), False, True, False)
    assert out2 == ["1", "2", "3", "4"]


def test_candidates_prep_minutes_czasowka():
    parsed = _parsed(order_ids=["8"])
    state = {"8": {"status": "planned", "prep_minutes": 90}}
    out = _build_prefetch_candidates(parsed, state, set(), False, False, True)
    assert out == ["8"]


# ───────────────────────────── prefetch_details ─────────────────────────────

@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(pdp.C, "flag",
                        lambda name, default=False: name == "ENABLE_PANEL_DETAIL_PREFETCH")
    monkeypatch.setattr(pdp.C, "load_flags",
                        lambda: {"PANEL_DETAIL_PREFETCH_WORKERS": 3})
    # świeża pula sesji per test
    monkeypatch.setattr(pdp, "_sessions", [])


def test_kill_switch_off(monkeypatch):
    monkeypatch.setattr(pdp.C, "flag", lambda name, default=False: False)
    m, st = pdp.prefetch_details(["1", "2", "3", "4"])
    assert m == {}
    assert st["prefetch_enabled"] is False


def test_small_batch_skips_threads(_flag_on, monkeypatch):
    called = []
    monkeypatch.setattr(pdp._WorkerSession, "fetch",
                        lambda self, zid, timeout=8: called.append(zid))
    m, st = pdp.prefetch_details(["1", "2"])  # < MIN_BATCH_FOR_THREADS
    assert m == {} and called == []
    assert st["prefetch_requested"] == 2


def test_prefetch_happy_path_no_session_sharing(_flag_on, monkeypatch):
    used_by = {}
    lock = threading.Lock()

    def fake_fetch(self, zid, timeout=8):
        with lock:
            used_by.setdefault(self.idx, set()).add(zid)
        return {"id_zlecenie": zid}

    monkeypatch.setattr(pdp._WorkerSession, "fetch", fake_fetch)
    zids = [str(i) for i in range(10)]
    m, st = pdp.prefetch_details(zids)
    assert set(m) == set(zids)
    assert all(m[z]["id_zlecenie"] == z for z in zids)
    assert st["prefetch_fetched"] == 10 and st["prefetch_errors"] == 0
    # chunking deterministyczny: żaden zid nie obsłużony przez 2 sesje
    seen = [z for s in used_by.values() for z in s]
    assert len(seen) == len(set(seen))
    # workers=3 → max 3 sesje
    assert len(used_by) <= 3


def test_prefetch_error_is_miss_legit_none_is_hit(_flag_on, monkeypatch):
    def fake_fetch(self, zid, timeout=8):
        if zid == "boom":
            raise RuntimeError("HTTP 500")
        if zid == "empty":
            return None  # legit odpowiedź panelu bez 'zlecenie'
        return {"id_zlecenie": zid}

    monkeypatch.setattr(pdp._WorkerSession, "fetch", fake_fetch)
    m, st = pdp.prefetch_details(["a", "boom", "empty", "b"])
    assert "boom" not in m            # miss → sekwencyjny fallback
    assert "empty" in m and m["empty"] is None  # hit z None → NIE ponawiamy
    assert m["a"] == {"id_zlecenie": "a"}
    assert st["prefetch_errors"] == 1


def test_prefetch_deadline_leftovers_are_misses(_flag_on, monkeypatch):
    monkeypatch.setattr(pdp, "PREFETCH_DEADLINE_SEC", 0.15)

    def slow_fetch(self, zid, timeout=8):
        time.sleep(0.06)
        return {"id_zlecenie": zid}

    monkeypatch.setattr(pdp._WorkerSession, "fetch", slow_fetch)
    zids = [str(i) for i in range(60)]
    m, st = pdp.prefetch_details(zids)
    # deadline tnie: nie wszystko pobrane, ale to co jest — poprawne
    assert 0 < len(m) < 60
    assert all(m[z]["id_zlecenie"] == z for z in m)


def test_prefetch_never_raises(_flag_on, monkeypatch):
    monkeypatch.setattr(pdp, "_get_sessions",
                        lambda n: (_ for _ in ()).throw(RuntimeError("pool fail")))
    m, st = pdp.prefetch_details(["1", "2", "3", "4"])
    assert m == {}
    assert st["prefetch_errors"] >= 1


def test_worker_session_relogin_on_419(monkeypatch):
    """fetch: HTTP 419 → re-login + retry raz; drugi 419 → raise (miss)."""
    import urllib.error

    calls = {"login": 0, "open": 0}

    class FakeOpener:
        def open(self, req, timeout=None):
            calls["open"] += 1
            if calls["open"] == 1:
                raise urllib.error.HTTPError("u", 419, "expired", {}, None)

            class R:
                def read(self):
                    return b'{"zlecenie": {"id_zlecenie": "x"}}'
            return R()

    def fake_perform_login():
        calls["login"] += 1
        return FakeOpener(), None, "csrf-tok", "<html>"

    monkeypatch.setattr(pdp.PC, "_perform_login", fake_perform_login)
    s = pdp._WorkerSession(0)
    out = s.fetch("x")
    assert out == {"id_zlecenie": "x"}
    assert calls["login"] == 2  # initial ensure + re-login po 419
