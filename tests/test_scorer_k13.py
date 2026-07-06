"""K13 (refaktor, 2026-07-06, ADR-R06): interfejs Scorer — kontrakty.

1. HeuristicScorer = tożsamość (bajt-parytet score z konstrukcji).
2. LgbmScorer = fail-soft: awaria/brak modelu/pusty wynik → heurystyka + fallback=True.
3. get_scorer: wybór z flags.json SCORER_IMPL, default heuristic, nieznane → heuristic.
4. Flaga ENABLE_SCORER_INTERFACE realnym mechanizmem (tmp flags.json):
   OFF → brak kluczy scorer_* w metrics i score bez zmian; ON(heuristic) →
   klucze obecne, score identyczny (ON==OFF numerycznie = dowód tożsamości,
   ON≠OFF obserwacyjnie = efekt flagi dla checkera C-FLAG-EFFECT).
"""
import json
import os
from datetime import datetime, timedelta, timezone

import dispatch_v2.dispatch_pipeline as dp
from dispatch_v2 import common as C
from dispatch_v2.core import scorer as sc
from dispatch_v2.courier_resolver import CourierState

_NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def test_heuristic_scorer_tozsamosc():
    v = sc.HeuristicScorer().score_candidate(-123.45)
    assert (v.score, v.source, v.fallback) == (-123.45, "heuristic", False)


def test_lgbm_scorer_fail_soft_fallback(monkeypatch):
    import dispatch_v2.ml_inference as ml

    def boom(*a, **k):
        raise RuntimeError("k13 boom")

    monkeypatch.setattr(ml, "predict_two_model_for_decision", boom)
    v = sc.LgbmScorer().score_candidate(-7.5, decision_ctx={"order_id": "X"})
    assert v.score == -7.5 and v.source == "lgbm" and v.fallback is True


def test_lgbm_scorer_pusty_wynik_to_fallback(monkeypatch):
    import dispatch_v2.ml_inference as ml
    monkeypatch.setattr(ml, "predict_two_model_for_decision", lambda *a, **k: None)
    v = sc.LgbmScorer().score_candidate(11.0)
    assert v.fallback is True and v.score == 11.0


def test_get_scorer_wybor_i_default(monkeypatch):
    monkeypatch.setattr(C, "load_flags", lambda: {"SCORER_IMPL": "lgbm"})
    assert isinstance(sc.get_scorer(), sc.LgbmScorer)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    assert isinstance(sc.get_scorer(), sc.HeuristicScorer)
    monkeypatch.setattr(C, "load_flags", lambda: {"SCORER_IMPL": "nieznany"})
    assert isinstance(sc.get_scorer(), sc.HeuristicScorer)


def _cs():
    cs = CourierState(courier_id="913")
    cs.pos = (53.131, 23.161)
    cs.pos_source = "gps"
    cs.pos_age_min = 1.0
    cs.bag = []
    cs.shift_start = _NOW - timedelta(hours=2)
    cs.shift_end = _NOW + timedelta(hours=4)
    cs.name = "K13 Tester"
    return cs


def _run_assess(monkeypatch, tmp_path, scorer_on: bool):
    flags_path = tmp_path / f"flags_{scorer_on}.json"
    base = {}
    try:
        base = json.loads(open(C.FLAGS_PATH).read())
    except Exception:
        pass
    base = dict(base)
    base["ENABLE_SCORER_INTERFACE"] = scorer_on
    base.pop("SCORER_IMPL", None)  # default heuristic
    flags_path.write_text(json.dumps(base))
    monkeypatch.setattr(C, "FLAGS_PATH", flags_path)  # Path (load_flags robi .stat())

    def fake_cf(**kw):
        return ("MAYBE", "ok", {"r6_bag_size": 0, "eta_pickup_min": 5.0}, None)

    monkeypatch.setattr(dp, "check_feasibility_v2", fake_cf)
    ev = {"order_id": "K13S", "restaurant": "Testownia", "delivery_address": "Testowa 1",
          "pickup_coords": [53.13, 23.16], "delivery_coords": [53.14, 23.17]}
    return dp.assess_order(ev, {"913": _cs()}, None, _NOW)


def test_flaga_on_off_realnym_mechanizmem(monkeypatch, tmp_path):
    r_off = _run_assess(monkeypatch, tmp_path, scorer_on=False)
    r_on = _run_assess(monkeypatch, tmp_path, scorer_on=True)

    def _cand(r):
        if r.best is not None:
            return r.best
        assert r.candidates, f"brak kandydata: {r.verdict}/{r.reason}"
        return r.candidates[0]

    c_off, c_on = _cand(r_off), _cand(r_on)
    assert "scorer_impl" not in (c_off.metrics or {}), "OFF nie może pisać kluczy scorer_*"
    assert (c_on.metrics or {}).get("scorer_impl") == "heuristic"
    assert (c_on.metrics or {}).get("scorer_fallback") is False
    assert c_on.score == c_off.score, "HeuristicScorer MUSI być tożsamością (bajt-parytet)"
