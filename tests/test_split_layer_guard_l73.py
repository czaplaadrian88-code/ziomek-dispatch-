"""L7.3 split-layer guard (2026-07-03, R2 ROOT-9, INV-LAYER-1/2).

OBSERWACYJNY strażnik kontraktu warstw:
  • INV-LAYER-1 (HARD-before-SOFT, pełny): re-assert `_assert_feasibility_first` na EMIT
    (`_split_layer_emit_assert`, wołany ze wspólnego lejka `_classify_and_set_auto_route`).
  • INV-LAYER-2 (NO-VERDICT-OUTSIDE-L5): jeden setter `_set_feasibility_verdict` z gardą
    warstwy — zapis werdyktu poza L5 loguje naruszenie.

Kontrakt testów: flaga OFF = bajt-parytet (zero logu/jsonl, zapis werdyktu NIEZMIENIONY);
flaga ON = TYLKO log/jsonl (decyzja/werdykt nietknięte); best_effort/solo (0 feasible)
WYŁĄCZONE (verdict NO z kontraktu R28). Mutation-probe: zdjęcie gardy (OFF) → detekcja RED.
"""
import json
import logging

import dispatch_v2.common as C
import dispatch_v2.dispatch_pipeline as DP


class _Cand:
    def __init__(self, cid, verdict="MAYBE"):
        self.courier_id = cid
        self.feasibility_verdict = verdict
        self.feasibility_reason = None
        self.metrics = {}


class _Result:
    def __init__(self, *, verdict="PROPOSE", best=None, candidates=None,
                 pool_feasible_count=0, order_id="O1"):
        self.verdict = verdict
        self.best = best
        self.candidates = candidates or []
        self.pool_feasible_count = pool_feasible_count
        self.order_id = order_id


# ──────────────────────────────────────────────────────────────────────────────
# Setter (INV-LAYER-2) — jeden zapis feasibility_verdict z gardą warstwy
# ──────────────────────────────────────────────────────────────────────────────

def test_setter_always_writes_off(monkeypatch):
    """Bajt-parytet: OFF → setter ustawia werdykt, ZERO logu/jsonl (nawet poza L5)."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: False)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    c = _Cand("A", "NO")
    DP._set_feasibility_verdict(c, "MAYBE", layer="L7_selekcja", order_id="O1")
    assert c.feasibility_verdict == "MAYBE"  # zapis wykonany
    assert _logged == []                     # brak jsonl (parytet)


def test_setter_l5_silent_on(monkeypatch):
    """ON + layer=L5 → zapis + garda CICHA (L5 to dozwolona warstwa werdyktu)."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    c = _Cand("A", "MAYBE")
    with caplog_level(logging.WARNING) as cap:
        DP._set_feasibility_verdict(c, "NO", layer="L5", order_id="O1")
    assert c.feasibility_verdict == "NO"
    assert _logged == []
    assert "SPLIT_LAYER_VERDICT_WRITE" not in cap.text


def test_setter_outside_l5_fires_on(monkeypatch):
    """ON + layer!=L5 → zapis wykonany (parytet decyzji) + WARNING + wpis jsonl."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    c = _Cand("BAD", "NO")
    with caplog_level(logging.WARNING) as cap:
        DP._set_feasibility_verdict(c, "MAYBE", layer="L7_selekcja", order_id="O7")
    assert c.feasibility_verdict == "MAYBE"                 # decyzja NIEZMIENIONA
    assert "SPLIT_LAYER_VERDICT_WRITE" in cap.text
    assert len(_logged) == 1
    e = _logged[0]
    assert e["kind"] == "verdict_write_outside_l5"
    assert e["layer"] == "L7_selekcja"
    assert e["order_id"] == "O7"
    assert e["courier_id"] == "BAD"


def test_setter_on_off_behavioral_diff(monkeypatch):
    """ON≠OFF: ten sam zapis poza L5 → OFF cicho, ON loguje (różnica = obserwowalność)."""
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: False)
    DP._set_feasibility_verdict(_Cand("A"), "MAYBE", layer="L7_selekcja")
    assert _logged == []
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    DP._set_feasibility_verdict(_Cand("A"), "MAYBE", layer="L7_selekcja")
    assert len(_logged) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Emit-assert (INV-LAYER-1) — re-assert HARD-before-SOFT na EMIT
# ──────────────────────────────────────────────────────────────────────────────

def test_emit_clean_feasible_pool_silent(monkeypatch):
    """ON + feasible-path + pula czysta (all MAYBE) → cisza."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    top = [_Cand("A", "MAYBE"), _Cand("B", "MAYBE")]
    r = _Result(best=top[0], candidates=top, pool_feasible_count=2)
    with caplog_level(logging.WARNING) as cap:
        DP._split_layer_emit_assert(r, r.order_id)
    assert _logged == []
    assert "SPLIT_LAYER_EMIT_VIOLATION" not in cap.text


def test_emit_no_in_pool_fires(monkeypatch):
    """ON + feasible-path (pool>0) + NO w emitowanej puli → WARNING + jsonl."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    bad = _Cand("BAD", "NO")
    top = [bad, _Cand("B", "MAYBE")]
    r = _Result(best=bad, candidates=top, pool_feasible_count=2, order_id="O2")
    with caplog_level(logging.WARNING) as cap:
        DP._split_layer_emit_assert(r, r.order_id)
    assert "SPLIT_LAYER_EMIT_VIOLATION" in cap.text
    assert len(_logged) == 1
    e = _logged[0]
    assert e["kind"] == "no_verdict_in_emit_pool"
    assert e["best_verdict_no"] is True
    assert "BAD" in e["no_in_pool_cids"]
    # _assert_feasibility_first uruchomiony → metryka na naruszającym
    assert bad.metrics.get("inv_feasibility_first_violation") is True


def test_emit_best_effort_exempt(monkeypatch):
    """ON + best_effort/solo (pool_feasible_count=0, verdict NO z kontraktu R28) → CISZA."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    be = _Cand("BE", "NO")
    r = _Result(verdict="PROPOSE", best=be, candidates=[be],
                pool_feasible_count=0, order_id="Obe")
    with caplog_level(logging.WARNING) as cap:
        DP._split_layer_emit_assert(r, r.order_id)
    assert _logged == []
    assert "SPLIT_LAYER_EMIT_VIOLATION" not in cap.text


def test_emit_off_is_noop_even_dirty(monkeypatch):
    """Bajt-parytet: OFF → no-op nawet dla brudnej puli (NO w feasible-path)."""
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: False)
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    bad = _Cand("BAD", "NO")
    r = _Result(best=bad, candidates=[bad], pool_feasible_count=1)
    DP._split_layer_emit_assert(r, r.order_id)
    assert _logged == []
    assert bad.metrics.get("inv_feasibility_first_violation") is None  # brak mutacji


def test_emit_mutation_probe(monkeypatch):
    """Mutation-probe: brudne wejście wykrywane TYLKO gdy garda ON. Zdjęcie gardy (OFF)
    → detekcja RED (brak wpisu) = dowód, że test naprawdę zależy od gardy."""
    _logged = []
    monkeypatch.setattr(DP, "_append_split_layer_guard_log", lambda e: _logged.append(e))
    bad = _Cand("BAD", "NO")
    r = _Result(best=bad, candidates=[bad], pool_feasible_count=1)
    # Garda ON → wykrywa
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: True)
    DP._split_layer_emit_assert(r, r.order_id)
    assert len(_logged) == 1
    # Garda OFF (mutacja: „wyłącz strażnika") → NIE wykrywa (RED)
    _logged.clear()
    monkeypatch.setattr(DP, "_split_layer_guard_on", lambda: False)
    DP._split_layer_emit_assert(_Result(best=_Cand("X", "NO"),
                                        candidates=[_Cand("X", "NO")],
                                        pool_feasible_count=1), "O")
    assert _logged == []


# ──────────────────────────────────────────────────────────────────────────────
# Integracja / okablowanie / flaga
# ──────────────────────────────────────────────────────────────────────────────

def test_flag_gate_reflects_constant(monkeypatch):
    """`_split_layer_guard_on()` odzwierciedla stałą-fallback gdy klucz spoza flags.json."""
    monkeypatch.setattr(C, "ENABLE_SPLIT_LAYER_GUARD", False, raising=False)
    assert DP._split_layer_guard_on() is False
    monkeypatch.setattr(C, "ENABLE_SPLIT_LAYER_GUARD", True, raising=False)
    assert DP._split_layer_guard_on() is True


def test_emit_assert_wired_into_common_funnel():
    """`_split_layer_emit_assert` MUSI biec ze wspólnego lejka pre-emit (11 call-site'ów)."""
    import inspect
    src = inspect.getsource(DP._classify_and_set_auto_route)
    assert "_split_layer_emit_assert(result" in src, \
        "INV-LAYER-1 re-assert musi być wpięty w _classify_and_set_auto_route (EMIT funnel)"


def test_verdict_writes_channeled_through_setter():
    """Wszystkie MUTACJE feasibility_verdict (FCR readmit L7 + pre_shift L5) idą setterem;
    ZERO surowych przypisań `.feasibility_verdict =` w silniku (poza samym setterem)."""
    import inspect
    src = inspect.getsource(DP)
    assert "_set_feasibility_verdict(\n" in src or "_set_feasibility_verdict(" in src
    # surowe przypisanie atrybutu istnieje TYLKO w ciele settera (1×)
    raw = src.count(".feasibility_verdict = ")
    assert raw == 1, f"oczekiwano 1 surowego zapisu (w setterze), jest {raw}"


def test_flag_not_in_decision_registry():
    """Garda jest OBSERWACYJNA → poza ETAP4_DECISION_FLAGS (nie wpływa na treść decyzji)."""
    assert "ENABLE_SPLIT_LAYER_GUARD" not in C.ETAP4_DECISION_FLAGS


# ──────────────────────────────────────────────────────────────────────────────
# Helper: caplog bez fixture (część suity biega jako script-runner)
# ──────────────────────────────────────────────────────────────────────────────

class _CapCtx:
    def __init__(self, level):
        self._level = level
        self._records = []
        self._handler = None

    def __enter__(self):
        self._handler = logging.Handler()
        self._handler.setLevel(self._level)
        self._records_ref = self._records
        _self = self

        class _H(logging.Handler):
            def emit(self, record):
                _self._records.append(record)
        self._handler = _H()
        self._handler.setLevel(self._level)
        logging.getLogger().addHandler(self._handler)
        self._prev_level = logging.getLogger().level
        logging.getLogger().setLevel(self._level)
        return self

    @property
    def text(self):
        return "\n".join(r.getMessage() for r in self._records)

    def __exit__(self, *a):
        logging.getLogger().removeHandler(self._handler)
        logging.getLogger().setLevel(self._prev_level)
        return False


def caplog_level(level):
    return _CapCtx(level)


if __name__ == "__main__":
    import sys
    _fns = [v for k, v in sorted(globals().items())
            if k.startswith("test_") and callable(v)]

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, val, raising=True):
            old = getattr(obj, name, None)
            had = hasattr(obj, name)
            self._undo.append((obj, name, old, had))
            setattr(obj, name, val)

        def undo(self):
            for obj, name, old, had in reversed(self._undo):
                if had:
                    setattr(obj, name, old)
            self._undo = []

    _fail = 0
    for _fn in _fns:
        _mp = _MP()
        try:
            import inspect as _ins
            if "monkeypatch" in _ins.signature(_fn).parameters:
                _fn(_mp)
            else:
                _fn()
            print(f"PASS {_fn.__name__}")
        except Exception as _e:  # noqa: BLE001
            _fail += 1
            print(f"FAIL {_fn.__name__}: {_e!r}")
        finally:
            _mp.undo()
    sys.exit(1 if _fail else 0)
