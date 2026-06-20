"""A2 dwumodel SHADOW wiring (2026-06-20) — OR-gate + serializacja LOCATION A/B.

Testuje JEDYNIE nową logikę wpięcia shadow (nie sam model — to robi test_ml_twomodel):
  1. predict_two_model_for_decision: OR-gate ENABLE_LGBM_PRIMARY | ENABLE_LGBM_TWOMODEL_SHADOW
     — obie OFF → None (zero compute); shadow ON → przepuszcza do inferera.
  2. shadow_dispatcher._serialize_candidate / _serialize_result emitują lgbm_twomodel_shadow
     (LOCATION A = alternatives, LOCATION B = best) — None gdy metrics nie ma pola.

Uruchom: python3 -m pytest tests/test_twomodel_shadow_wiring.py -q
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import ml_inference
from dispatch_v2 import shadow_dispatcher

T0 = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)

_TM_PAYLOAD = {
    "enabled": True, "fallback_reason": None, "winner_cid": "c1",
    "winner_score": 1.23, "regime_counts": {"solo": 2, "bundle": 1},
    "n_candidates_scored": 3, "agreement_with_primary": True,
}


# ───────────────────────────── 1. OR-gate ─────────────────────────────
def _patch_flags(monkeypatch, *, primary, shadow):
    def fake_flag(name, default=False):
        if name == "ENABLE_LGBM_PRIMARY":
            return primary
        if name == "ENABLE_LGBM_TWOMODEL_SHADOW":
            return shadow
        return default
    # predict_two_model_for_decision robi `from dispatch_v2.common import flag`
    monkeypatch.setattr("dispatch_v2.common.flag", fake_flag, raising=False)


def test_gate_both_off_returns_none(monkeypatch):
    _patch_flags(monkeypatch, primary=False, shadow=False)
    called = {"n": 0}

    def boom():
        called["n"] += 1
        raise AssertionError("inferer NIE powinien być wołany gdy obie flagi OFF")

    monkeypatch.setattr(ml_inference, "get_twomodel_inferer", boom, raising=True)
    assert ml_inference.predict_two_model_for_decision({"order_id": "o"}, []) is None
    assert called["n"] == 0  # zero compute


def test_gate_shadow_on_passes_through(monkeypatch):
    _patch_flags(monkeypatch, primary=False, shadow=True)
    sentinel = SimpleNamespace(winner_cid="c1")
    fake = SimpleNamespace(predict_for_decision=lambda ctx, cands: sentinel)
    monkeypatch.setattr(ml_inference, "get_twomodel_inferer", lambda: fake, raising=True)
    out = ml_inference.predict_two_model_for_decision({"order_id": "o"}, ["cand"])
    assert out is sentinel


def test_gate_primary_on_still_passes(monkeypatch):
    _patch_flags(monkeypatch, primary=True, shadow=False)
    sentinel = SimpleNamespace(winner_cid="c1")
    fake = SimpleNamespace(predict_for_decision=lambda ctx, cands: sentinel)
    monkeypatch.setattr(ml_inference, "get_twomodel_inferer", lambda: fake, raising=True)
    assert ml_inference.predict_two_model_for_decision({"order_id": "o"}, ["x"]) is sentinel


# ───────────────────────── 2. serializacja LOCATION A/B ─────────────────────────
def _cand(metrics):
    return SimpleNamespace(
        courier_id="c1", name="T", score=50.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics=metrics,
    )


def test_serializer_location_a_emits_field():
    out = shadow_dispatcher._serialize_candidate(_cand({"lgbm_twomodel_shadow": _TM_PAYLOAD}))
    assert out["lgbm_twomodel_shadow"] == _TM_PAYLOAD


def test_serializer_location_a_none_when_absent():
    out = shadow_dispatcher._serialize_candidate(_cand({}))
    assert out["lgbm_twomodel_shadow"] is None


def test_serializer_location_b_best_emits_field():
    best = _cand({"lgbm_twomodel_shadow": _TM_PAYLOAD})
    result = SimpleNamespace(
        order_id="474900", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=T0,
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["lgbm_twomodel_shadow"] == _TM_PAYLOAD
