"""R-04 graduation telemetry: source truth, serializer twins and purity."""
from __future__ import annotations

import pickle
from dataclasses import asdict

from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult
from dispatch_v2.r04_evaluator import (
    CourierMetrics,
    evaluate_courier_tier,
)


def _suggestion(cid: str, **updates) -> dict:
    data = {
        "cid": cid,
        "current_tier": "std",
        "suggested_tier": "standard_plus",
        "tier_match": False,
        "insufficient_data": False,
        "insufficient_data_reason": None,
        "gold_candidate": False,
        "promotion_eligible": True,
        "demotion_required": False,
        "gates_evaluated": {
            "std_to_std_plus_promotion": [{
                "metric": "peak_deliveries_30d",
                "op": ">=",
                "threshold": 50,
                "value": 57,
                "passed": True,
                "sustained_days": None,
            }],
        },
        "metrics": {
            "cid": cid,
            "peak_deliveries_30d": 57,
            "peak_active_days_30d": 8,
        },
        "reasoning": "std → standard_plus promotion: all gates passed",
        "evaluated_at": "2026-07-20T03:00:00+00:00",
        "schema_version": "2.0",
    }
    data.update(updates)
    return data


def _candidate(cid: str, score: float, *, metrics: dict | None = None) -> Candidate:
    return Candidate(
        courier_id=cid,
        name=None,
        score=score,
        feasibility_verdict="MAYBE",
        feasibility_reason="test",
        plan=None,
        metrics=dict(metrics or {}),
    )


def test_r04_payload_exposes_outcome_inputs_thresholds_and_candidate(monkeypatch):
    monkeypatch.setattr(
        SD, "_load_r04_suggestions", lambda: {"900": _suggestion("900")})

    payload = SD._r04_field_for_cid("900")

    assert payload["courier_id"] == "900"
    assert payload["evaluation_ran"] is True
    assert payload["evaluated_in_decision"] is False
    assert payload["decision_effect"] == "telemetry_only"
    assert payload["source"] == "tier_suggestions"
    assert payload["outcome"] == "promotion_suggested"
    assert payload["promotion_eligible"] is True
    assert payload["demotion_required"] is False
    assert payload["reasoning"].startswith("std → standard_plus")
    rule = payload["gates_evaluated"]["std_to_std_plus_promotion"][0]
    assert (rule["threshold"], rule["value"], rule["passed"]) == (50, 57, True)
    assert payload["metrics"]["peak_active_days_30d"] == 8
    assert "cid" not in payload["metrics"]
    assert "name" not in payload["metrics"]


def test_serializer_locations_a_and_b_use_candidate_identity_not_metrics(monkeypatch):
    suggestions = {
        "900": _suggestion("900"),
        "901": _suggestion(
            "901",
            current_tier="standard",
            suggested_tier="standard",
            tier_match=True,
            promotion_eligible=False,
            reasoning="std maintained",
        ),
    }
    monkeypatch.setattr(SD, "_load_r04_suggestions", lambda: suggestions)
    best = _candidate("900", 100.0, metrics={"courier_id": "WRONG"})
    alt = _candidate("901", 90.0)

    location_a = SD._serialize_candidate(alt)
    result = PipelineResult(
        order_id="order-r04",
        verdict="PROPOSE",
        reason="test",
        best=best,
        candidates=[best, alt],
        pickup_ready_at=None,
        restaurant="Test",
    )
    location_b = SD._serialize_result(result, "event-r04", 1.0)

    assert location_a["r04"]["courier_id"] == "901"
    assert location_a["r04"]["outcome"] == "tier_maintained"
    assert location_b["best"]["courier_id"] == "900"
    assert location_b["best"]["r04"]["courier_id"] == "900"
    assert location_b["alternatives"][0]["r04"]["courier_id"] == "901"


def test_missing_suggestion_keeps_explicit_not_evaluated_signal(monkeypatch):
    monkeypatch.setattr(SD, "_load_r04_suggestions", lambda: {})
    out = SD._serialize_candidate(_candidate("999", 1.0))
    assert "r04" in out
    assert out["r04"] is None


def test_r04_outcome_distinguishes_insufficient_data_and_demotion(monkeypatch):
    suggestions = {
        "904": _suggestion(
            "904",
            suggested_tier="std",
            tier_match=True,
            promotion_eligible=False,
            insufficient_data=True,
            insufficient_data_reason="peak_deliveries=12<40",
        ),
        "905": _suggestion(
            "905",
            current_tier="standard_plus",
            suggested_tier="standard",
            promotion_eligible=False,
            demotion_required=True,
            reasoning="std+ → std demotion: gates triggered",
        ),
    }
    monkeypatch.setattr(SD, "_load_r04_suggestions", lambda: suggestions)

    insufficient = SD._r04_field_for_cid("904")
    demotion = SD._r04_field_for_cid("905")

    assert insufficient["outcome"] == "insufficient_data"
    assert insufficient["insufficient_data_reason"] == "peak_deliveries=12<40"
    assert demotion["outcome"] == "demotion_suggested"
    assert demotion["demotion_required"] is True


def test_new_graduation_reports_exact_thresholds_and_values(monkeypatch):
    schema = {
        "_meta": {"version": "2.0"},
        "insufficient_data": {
            "min_peak_deliveries_30d": 40,
            "min_peak_active_days_30d": 5,
            "min_speed_data_completeness_pct": 70.0,
        },
    }
    metrics = CourierMetrics(
        cid="902",
        name=None,
        peak_deliveries_30d=55,
        peak_active_days_30d=6,
        speed_data_completeness_pct=100.0,
        days_since_first_delivery=20,
    )

    suggestion = evaluate_courier_tier(
        "902", None, metrics, "new", schema,
        now_iso="2026-07-20T03:00:00+00:00",
    )
    monkeypatch.setattr(
        SD, "_load_r04_suggestions", lambda: {"902": asdict(suggestion)})
    payload = SD._r04_field_for_cid("902")

    assert suggestion.promotion_eligible is True
    assert suggestion.suggested_tier == "standard"
    rules = payload["gates_evaluated"]["new_graduation"]["rules"]
    assert [(r["threshold"], r["value"], r["passed"]) for r in rules] == [
        (14, 20, True),
        (50, 55, True),
        (5, 6, True),
    ]
    assert payload["outcome"] == "promotion_suggested"


def test_new_graduation_failure_is_visible_without_changing_tier(monkeypatch):
    schema = {
        "_meta": {"version": "2.0"},
        "insufficient_data": {
            "min_peak_deliveries_30d": 40,
            "min_peak_active_days_30d": 5,
            "min_speed_data_completeness_pct": 70.0,
        },
    }
    metrics = CourierMetrics(
        cid="903",
        name=None,
        peak_deliveries_30d=50,
        peak_active_days_30d=5,
        speed_data_completeness_pct=100.0,
        days_since_first_delivery=13,
    )
    suggestion = evaluate_courier_tier(
        "903", None, metrics, "new", schema,
        now_iso="2026-07-20T03:00:00+00:00",
    )
    monkeypatch.setattr(
        SD, "_load_r04_suggestions", lambda: {"903": asdict(suggestion)})

    payload = SD._r04_field_for_cid("903")

    assert suggestion.promotion_eligible is False
    assert suggestion.suggested_tier == "new"
    assert payload["outcome"] == "tier_maintained"
    assert payload["gates_evaluated"]["new_graduation"]["rules"][0] == {
        "metric": "days_since_first_delivery",
        "op": ">=",
        "threshold": 14,
        "value": 13,
        "passed": False,
    }


def test_serialization_leaves_decision_object_byte_identical(monkeypatch):
    monkeypatch.setattr(
        SD, "_load_r04_suggestions", lambda: {"900": _suggestion("900")})
    best = _candidate("900", 123.5, metrics={"score_component": 7.0})
    result = PipelineResult(
        order_id="order-purity",
        verdict="PROPOSE",
        reason="unchanged",
        best=best,
        candidates=[best],
        pickup_ready_at=None,
        restaurant="Test",
    )
    before = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)

    serialized = SD._serialize_result(result, "event-purity", 2.0)

    after = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
    assert after == before
    assert (result.verdict, result.reason, result.best.score) == (
        "PROPOSE", "unchanged", 123.5)
    assert serialized["best"]["r04"]["outcome"] == "promotion_suggested"
