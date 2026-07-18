"""D6a SHADOW (OWNER_CONFIRMED D1-D7, 2026-07-18) — serving obietnic kalibratora.

Pinuje: OFF = zero śladu (default const False, strip-safe) · ON = NOWE metryki
eta_calib_promise_* na best (wzorzec #8, żadnej podmiany istniejących pól) ·
fail-soft (brak championa → srv_skip, nigdy wyjątek) · binding KPI v1 ==
decyzje właściciela D4/D5 (anti-drift: zmiana wartości wymaga nowej decyzji).
"""
import types

import dispatch_v2.common as C
from dispatch_v2 import eta_calib_serving as S
from dispatch_v2.tools import eta_ground_truth as G


def _fake_best(cid="509"):
    return types.SimpleNamespace(courier_id=cid, metrics={})


def _fake_result(best):
    return types.SimpleNamespace(best=best, order_id="o1")


_EV = {"pickup_coords": (53.13, 23.16), "delivery_coords": (53.14, 23.17),
       "czas_odbioru": 30}


class _FakeModel:
    def __init__(self, p80):
        self.p80 = p80

    def predict_quantiles(self, row):
        assert row.get("courier_id"), "serving musi podać courier_id"
        return {0.5: self.p80 - 2, 0.8: self.p80, 0.9: self.p80 + 3}


def test_off_is_total_noop(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ETA_CALIB_PROMISE_SHADOW", False)
    best = _fake_best()
    S.attach_shadow_promise_metrics(_fake_result(best), dict(_EV))
    assert best.metrics == {}


def test_on_attaches_new_metrics_only(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ETA_CALIB_PROMISE_SHADOW", True)
    monkeypatch.setattr(S, "_load_model", lambda leg: (
        _FakeModel(6.5 if leg == "pickup" else 9.5), f"sha_{leg[:4]}"))
    monkeypatch.setattr(S, "_ff_raw", lambda a, b: (2.1, 4.4))
    best = _fake_best()
    best.metrics["istniejace_pole"] = "nietykalne"
    S.attach_shadow_promise_metrics(_fake_result(best), dict(_EV))
    m = best.metrics
    assert m["eta_calib_promise_pickup_p80_min"] == 6.5
    assert m["eta_calib_promise_delivery_p80_min"] == 9.5
    assert m["eta_calib_champion"] == "sha_pick/sha_deli"
    assert m["istniejace_pole"] == "nietykalne"
    assert "eta_calib_srv_skip" not in m


def test_fail_soft_missing_champion(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ETA_CALIB_PROMISE_SHADOW", True)
    monkeypatch.setattr(S, "_load_model", lambda leg: (None, "champion_missing"))
    best = _fake_best()
    S.attach_shadow_promise_metrics(_fake_result(best), dict(_EV))
    assert "champion_missing" in best.metrics.get("eta_calib_srv_skip", "")
    assert "eta_calib_promise_pickup_p80_min" not in best.metrics


def test_fail_soft_no_best():
    S.attach_shadow_promise_metrics(types.SimpleNamespace(best=None), dict(_EV))


def test_was_czasowka_semantics():
    assert S._was_czasowka({"czas_odbioru": 60}) == 1
    assert S._was_czasowka({"czas_odbioru": 30}) == 0
    assert S._was_czasowka({}) == 0
    assert S._was_czasowka(None) == 0


def test_kpi_binding_matches_owner_decisions():
    b = G.KPI_BINDING_V1
    assert b["binding_version"] == "kpi_binding.v1"
    assert b["possession_event"]["field"] == "restaurant_last_inside_at"      # D1a
    assert b["arrival_event"]["field"] == "delivery_arrival_at"               # D2a
    assert b["prediction_anchor"] == "latest_shadow_at_or_before_operator_decision"  # D3a
    assert b["coverage_gate"] == {"min_complete_case_pct": 60.0, "min_n": 200,
                                  "below_gate": "HOLD_cell_fail_closed"}      # D4
    t = b["thresholds"]                                                       # D5
    assert t["pickup"] == {"mae_max_min": 6.0, "min_improvement_vs_engine_pct": 25.0}
    assert t["delivery"] == {"mae_max_min": 8.0, "min_improvement_vs_engine_pct": 10.0}
    assert t["late_band_pct"] == [15.0, 22.0]
    assert t["median_bias_abs_max_min"] == 1.5 and t["p90_abs_err_max_min"] == 20.0


def test_flag_registered_etap4_and_const_off():
    assert "ENABLE_ETA_CALIB_PROMISE_SHADOW" in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_ETA_CALIB_PROMISE_SHADOW is False
