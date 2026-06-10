"""ETAP 5 KROK 1 (2026-06-10, Z-05) — testy score-based selektora czasówek (shadow).

Czyste funkcje, zero I/O (lekcja #180: żadnych zapisów state/log; flagi tylko
przez monkeypatch funkcji flag w module — nie dotykamy flags.json, conftest L2
i tak izoluje).
"""
from types import SimpleNamespace

import pytest

from dispatch_v2.czasowka_proactive import score_selector as ss


def _cand(cid, score, verdict="MAYBE", wait=0.0, r6=None, name=None):
    return SimpleNamespace(
        courier_id=cid,
        name=name or f"K{cid}",
        score=score,
        feasibility_verdict=verdict,
        metrics={
            "v3273_wait_courier_max_min": wait,
            "r6_per_order_violations": r6 or [],
        },
    )


def _eval_result(best, alternatives=(), all_cands=None):
    cands = list(all_cands) if all_cands is not None else (
        ([best] if best else []) + list(alternatives)
    )
    return {
        "best": best,
        "alternatives": list(alternatives),
        "all_candidates_for_proactive": cands,
    }


# ---------- evaluate_score_based (gates) ----------

class TestGates:
    def test_happy_path_would_assign(self):
        best = _cand("100", 55.0, wait=4.0)
        rival = _cand("200", 30.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is True
        assert out["sb_reject_reason"] is None
        assert out["sb_cid"] == "100"
        assert out["sb_score"] == 55.0
        assert out["sb_margin"] == 25.0
        assert out["sb_wait_min"] == 4.0
        assert out["sb_r6_violations"] == 0
        assert out["sb_pool_feasible"] == 2
        assert out["sb_best_is_score_top"] is True
        assert out["sb_solo"] is False

    def test_no_maybe_best(self):
        best = _cand("100", 80.0, verdict="NO")
        out = ss.evaluate_score_based(_eval_result(best, []))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "no_maybe_best"
        assert out["sb_cid"] is None

    def test_best_none(self):
        out = ss.evaluate_score_based(_eval_result(None, []))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "no_maybe_best"
        assert out["sb_pool_feasible"] == 0

    def test_score_below_min(self):
        best = _cand("100", 20.0)
        rival = _cand("200", 1.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "score_below_min"
        # metryki dalej zalogowane mimo reject
        assert out["sb_margin"] == 19.0
        assert out["sb_wait_min"] == 0.0

    def test_solo_pool_rejected_strict(self):
        # Pula solo: margin niezdefiniowany → strict reject (mirror klasyfikatora
        # Fazy 7, solo=0.0 nie przechodzi). Kalibracja KROK 2 skwantyfikuje koszt.
        best = _cand("100", 90.0)
        out = ss.evaluate_score_based(_eval_result(best, []))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "solo_pool"
        assert out["sb_solo"] is True
        assert out["sb_margin"] is None
        assert out["sb_best_is_score_top"] is True

    def test_margin_below_min(self):
        best = _cand("100", 50.0)
        rival = _cand("200", 45.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "margin_below_min"
        assert out["sb_margin"] == 5.0

    def test_negative_margin_best_not_score_top(self):
        # Z-10: best wybrany po demote/tieringu może NIE być score-topem —
        # margin ujemny, sb_best_is_score_top=False (kluczowa obserwacja kalibracji).
        best = _cand("100", 40.0)
        rival = _cand("200", 70.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "margin_below_min"
        assert out["sb_margin"] == -30.0
        assert out["sb_best_is_score_top"] is False

    def test_wait_above_max(self):
        best = _cand("100", 60.0, wait=12.5)
        rival = _cand("200", 30.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "wait_above_max"
        assert out["sb_wait_min"] == 12.5

    def test_r6_violations_reject(self):
        best = _cand("100", 60.0, r6=[{"oid": "1", "over_min": 3.2}])
        rival = _cand("200", 30.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "r6_violations"
        assert out["sb_r6_violations"] == 1

    def test_margin_ignores_no_candidates(self):
        # Kandydaci NO nie wchodzą do marginu (scores NO = bez znaczenia).
        best = _cand("100", 50.0, wait=2.0)
        maybe_rival = _cand("200", 20.0)
        no_rival = _cand("300", 49.0, verdict="NO")
        out = ss.evaluate_score_based(_eval_result(best, [maybe_rival, no_rival]))
        assert out["sb_margin"] == 30.0
        assert out["sb_pool_feasible"] == 2
        assert out["sb_would_assign"] is True

    def test_custom_thresholds(self):
        best = _cand("100", 25.0, wait=11.0)
        rival = _cand("200", 15.0)
        out = ss.evaluate_score_based(
            _eval_result(best, [rival]),
            min_score=20.0, min_margin=5.0, max_wait_min=15.0,
        )
        assert out["sb_would_assign"] is True
        assert out["sb_thresholds"] == {
            "min_score": 20.0, "min_margin": 5.0, "max_wait_min": 15.0,
        }

    def test_metrics_missing_keys_safe(self):
        best = SimpleNamespace(
            courier_id="100", name="X", score=60.0,
            feasibility_verdict="MAYBE", metrics={},
        )
        rival = _cand("200", 10.0)
        out = ss.evaluate_score_based(_eval_result(best, [rival]))
        assert out["sb_would_assign"] is True
        assert out["sb_wait_min"] == 0.0
        assert out["sb_r6_violations"] == 0

    def test_fallback_best_plus_alternatives_when_no_all_cands(self):
        best = _cand("100", 55.0)
        rival = _cand("200", 30.0)
        er = {"best": best, "alternatives": [rival]}
        out = ss.evaluate_score_based(er)
        assert out["sb_would_assign"] is True
        assert out["sb_margin"] == 25.0


# ---------- shadow_fields_for_eval (flag + window gating) ----------

class TestShadowGating:
    @pytest.fixture(autouse=True)
    def _flags(self, monkeypatch):
        self._flag_values = {
            "CZASOWKA_PROACTIVE_SCORE_SHADOW": True,
            "CZASOWKA_PROACTIVE_MIN_SCORE": 30,
            "CZASOWKA_PROACTIVE_MIN_MARGIN": 15,
            "CZASOWKA_PROACTIVE_MAX_WAIT_MIN": 10,
        }
        monkeypatch.setattr(
            ss, "flag",
            lambda name, default=False: self._flag_values.get(name, default),
        )

    def _er(self):
        return _eval_result(_cand("100", 55.0, wait=2.0), [_cand("200", 30.0)])

    def test_in_window_returns_fields(self):
        out = ss.shadow_fields_for_eval(self._er(), 52.0)
        assert out["sb_would_assign"] is True
        assert out["sb_cid"] == "100"

    def test_window_edges(self):
        assert ss.shadow_fields_for_eval(self._er(), 60.0) != {}
        assert ss.shadow_fields_for_eval(self._er(), 40.0) == {}  # FORCE okno
        assert ss.shadow_fields_for_eval(self._er(), 60.1) == {}
        assert ss.shadow_fields_for_eval(self._er(), 39.0) == {}

    def test_none_mins_skipped(self):
        assert ss.shadow_fields_for_eval(self._er(), None) == {}

    def test_flag_off_skipped(self):
        self._flag_values["CZASOWKA_PROACTIVE_SCORE_SHADOW"] = False
        assert ss.shadow_fields_for_eval(self._er(), 52.0) == {}

    def test_thresholds_from_flags(self):
        self._flag_values["CZASOWKA_PROACTIVE_MIN_SCORE"] = 70
        out = ss.shadow_fields_for_eval(self._er(), 52.0)
        assert out["sb_would_assign"] is False
        assert out["sb_reject_reason"] == "score_below_min"

    def test_bad_threshold_value_falls_back(self):
        self._flag_values["CZASOWKA_PROACTIVE_MIN_SCORE"] = "nie-liczba"
        out = ss.shadow_fields_for_eval(self._er(), 52.0)
        # fallback do default 30 → 55 przechodzi
        assert out["sb_would_assign"] is True
