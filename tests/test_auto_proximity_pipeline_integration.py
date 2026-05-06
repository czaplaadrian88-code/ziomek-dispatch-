"""Faza 7-AUTO-PROXIMITY integration tests — PipelineResult + shadow log roundtrip.

Tests:
1. PipelineResult dataclass has auto_route + auto_route_reason + auto_route_context fields w/ defaults.
2. _classify_and_set_auto_route mutates result correctly when classifier conditions met.
3. _classify_and_set_auto_route falls back to ACK on classifier exception.
4. shadow_dispatcher._serialize_result includes auto_route* fields w out dict.
5. End-to-end: dispatch_pipeline.assess_order returns PipelineResult with auto_route set.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.dispatch_pipeline import PipelineResult, _classify_and_set_auto_route, Candidate


def _make_minimal_plan():
    """Plan mock with all fields used by shadow_dispatcher._serialize_result."""
    return SimpleNamespace(
        sequence=[],
        total_duration_min=15.0,
        strategy="greedy",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={},
        predicted_delivered_at={},
        pickup_at={},
    )


def _make_minimal_candidate(courier_id="c1", score=80.0, verdict="MAYBE",
                             metrics=None, best_effort=False):
    return Candidate(
        courier_id=courier_id,
        name=f"Kurier {courier_id}",
        score=score,
        feasibility_verdict=verdict,
        feasibility_reason="ok",
        plan=_make_minimal_plan(),
        metrics=(metrics or {}),
        best_effort=best_effort,
    )


def _make_courier_state(tier_bag="gold", pos_source="gps"):
    return SimpleNamespace(
        tier_bag=tier_bag,
        shift_end=datetime.now(timezone.utc) + timedelta(hours=2),
        shift_start=datetime.now(timezone.utc) - timedelta(hours=4),
        pos_source=pos_source,
        courier_id="c1",
    )


def test_pipeline_result_dataclass_has_auto_route_fields():
    """PipelineResult instance must have auto_route='ACK', auto_route_reason='', auto_route_context dict by default."""
    r = PipelineResult(
        order_id="X", verdict="KOORD", reason="x",
        best=None, candidates=[], pickup_ready_at=None, restaurant=None,
    )
    assert hasattr(r, "auto_route"), "auto_route field missing"
    assert hasattr(r, "auto_route_reason"), "auto_route_reason field missing"
    assert hasattr(r, "auto_route_context"), "auto_route_context field missing"
    assert r.auto_route == "ACK", f"default auto_route should be 'ACK', got {r.auto_route!r}"
    assert r.auto_route_reason == "", f"default reason should be empty, got {r.auto_route_reason!r}"
    assert isinstance(r.auto_route_context, dict), f"context should be dict, got {type(r.auto_route_context)}"


def test_classify_helper_mutates_result_when_classifier_returns_auto():
    """_classify_and_set_auto_route reads flags + populates auto_route + context."""
    best = _make_minimal_candidate(courier_id="c1", score=80.0)
    second = _make_minimal_candidate(courier_id="c2", score=60.0)
    r = PipelineResult(
        order_id="471036", verdict="PROPOSE", reason="feasible=2 best=c1",
        best=best, candidates=[best, second],
        pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        restaurant="Test", pool_total_count=2, pool_feasible_count=2,
    )
    fleet = {"c1": _make_courier_state(tier_bag="gold")}

    flags_dict = {
        "AUTO_PROXIMITY_ENABLED": True,
        "AUTO_PROXIMITY_THRESHOLD": "T1",
    }
    with mock.patch("dispatch_v2.common.load_flags", return_value=flags_dict):
        _classify_and_set_auto_route(r, fleet, order_event={})

    assert r.auto_route == "AUTO", f"expected AUTO, got {r.auto_route} (reason={r.auto_route_reason})"
    assert "high_conf_T1" in r.auto_route_reason, f"reason mismatch: {r.auto_route_reason}"
    # Context should be populated
    assert r.auto_route_context.get("auto_route_score_margin") == 20.0, \
        f"expected margin=20.0, got {r.auto_route_context}"
    assert r.auto_route_context.get("auto_route_tier_best") == "gold"


def test_classify_helper_defends_against_classifier_exception():
    """If classify_auto_route raises, helper falls back to ACK + classifier_exception reason."""
    r = PipelineResult(
        order_id="X", verdict="PROPOSE", reason="x",
        best=_make_minimal_candidate(), candidates=[],
        pickup_ready_at=None, restaurant=None,
    )
    # Force classifier to raise
    with mock.patch(
        "dispatch_v2.auto_proximity_classifier.classify_auto_route",
        side_effect=RuntimeError("simulated classifier crash"),
    ):
        _classify_and_set_auto_route(r, {}, {})

    assert r.auto_route == "ACK", f"expected ACK fallback, got {r.auto_route}"
    assert "classifier_exception" in r.auto_route_reason, \
        f"reason should signal exception, got {r.auto_route_reason}"


def test_shadow_serialize_includes_auto_route_fields():
    """shadow_dispatcher._serialize_result must include auto_route + reason + context in output dict."""
    from dispatch_v2 import shadow_dispatcher

    r = PipelineResult(
        order_id="471036", verdict="PROPOSE", reason="feasible=2 best=c1",
        best=_make_minimal_candidate(courier_id="c1"),
        candidates=[_make_minimal_candidate("c1"), _make_minimal_candidate("c2", score=60.0)],
        pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        restaurant="Karczma", delivery_address="Test",
        pool_total_count=2, pool_feasible_count=2,
    )
    r.auto_route = "AUTO"
    r.auto_route_reason = "high_conf_T1|margin=20.0|tier=gold"
    r.auto_route_context = {
        "auto_route_pool_feasible": 2,
        "auto_route_pool_total": 2,
        "auto_route_score_margin": 20.0,
        "auto_route_tier_best": "gold",
        "auto_route_pos_source_best": "gps",
    }

    record = shadow_dispatcher._serialize_result(r, event_id="evt-1", latency_ms=12.3)

    assert "auto_route" in record, "auto_route missing from serialized record"
    assert record["auto_route"] == "AUTO"
    assert "auto_route_reason" in record
    assert "high_conf_T1" in record["auto_route_reason"]
    assert "auto_route_context" in record
    ctx = record["auto_route_context"]
    assert ctx["auto_route_score_margin"] == 20.0
    assert ctx["auto_route_tier_best"] == "gold"

    # JSON serializable check (shadow log writes JSON lines)
    json_str = json.dumps(record, default=str)
    assert "auto_route" in json_str, "auto_route should survive JSON roundtrip"


def test_shadow_serialize_backward_compat_legacy_result():
    """Legacy result without auto_route fields (e.g., during gradual deploy) → defaults work."""
    from dispatch_v2 import shadow_dispatcher

    # Simulate older-version PipelineResult without auto_route fields
    legacy = SimpleNamespace(
        order_id="X", verdict="KOORD", reason="x", best=None, candidates=[],
        pickup_ready_at=None, restaurant=None, delivery_address=None,
        pool_total_count=0, pool_feasible_count=0,
    )
    # auto_route attribute missing on this duck-typed obj → getattr default kicks in
    record = shadow_dispatcher._serialize_result(legacy, event_id="evt-2", latency_ms=5.0)
    assert record["auto_route"] == "ACK", "legacy result should default to ACK"
    assert record["auto_route_reason"] == ""
    assert record["auto_route_context"] == {}


def test_global_kill_propagates_through_helper():
    """When AUTO_PROXIMITY flags both False, helper sets auto_route=ACK with disabled_global reason."""
    r = PipelineResult(
        order_id="X", verdict="PROPOSE", reason="x",
        best=_make_minimal_candidate(), candidates=[_make_minimal_candidate()],
        pickup_ready_at=None, restaurant=None,
    )
    with mock.patch("dispatch_v2.common.load_flags", return_value={
        "AUTO_PROXIMITY_ENABLED": False,
        "AUTO_PROXIMITY_SHADOW_ONLY": False,
    }):
        _classify_and_set_auto_route(r, {}, {})

    assert r.auto_route == "ACK"
    assert "auto_proximity_disabled_global" in r.auto_route_reason


if __name__ == "__main__":
    tests = [
        test_pipeline_result_dataclass_has_auto_route_fields,
        test_classify_helper_mutates_result_when_classifier_returns_auto,
        test_classify_helper_defends_against_classifier_exception,
        test_shadow_serialize_includes_auto_route_fields,
        test_shadow_serialize_backward_compat_legacy_result,
        test_global_kill_propagates_through_helper,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  OK {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"PASSED: {passed}/{len(tests)}")
    sys.exit(0 if failed == 0 else 1)
