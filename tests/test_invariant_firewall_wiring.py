"""Wiring Z-P0-01: jeden finalny hook, wspolny zegar i serializacja."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import invariant_firewall as FW
from dispatch_v2.observability import candidate_logger


NOW_NAIVE = datetime(2026, 7, 9, 10, 0)
NOW_UTC = NOW_NAIVE.replace(tzinfo=timezone.utc)

FULL_RULE_VERDICT_KEYS = {
    "schema", "phase", "evaluation_stage", "status", "physical_status",
    "coverage", "enforcement",
    "decision_order_id", "decision_verdict", "selected_courier_id",
    "selection_mode", "always_propose_enabled", "policy_pending", "rules",
    "violations", "exceptions", "missing_reasons",
    "introduced_rule_variant_row_count", "preexisting_rule_variant_row_count",
    "causality_unknown_rule_variant_row_count", "count_unit",
}


def _assert_complete_unknown_dict(rv):
    assert set(rv) == FULL_RULE_VERDICT_KEYS
    assert rv["schema"] == "rule_verdict.v2"
    assert rv["evaluation_stage"] == FW.FINAL_STAGE
    assert rv["status"] == FW.UNKNOWN and rv["coverage"] == FW.NONE
    assert rv["physical_status"] == FW.UNKNOWN
    assert rv["count_unit"] == FW.COUNT_UNIT
    assert len(rv["rules"]) == 3
    assert {row["rule_id"] for row in rv["rules"]} == {
        FW.R6_THERMAL, FW.R27_COMMITTED_PICKUP, FW.SLA_DELIVERY,
    }
    assert rv["violations"] == [] and rv["exceptions"] == []
    assert all({
        "physical_status", "introduced_rule_variant_row_count",
        "preexisting_rule_variant_row_count",
        "causality_unknown_rule_variant_row_count", "count_unit",
    } <= set(row) for row in rv["rules"])
    json.dumps(rv)


def _plan(elapsed=40.0):
    return NS(
        per_order_delivery_times={"N": elapsed},
        predicted_delivered_at={"N": NOW_UTC + timedelta(minutes=elapsed)},
        pickup_at={"N": NOW_UTC},
        sequence=["N"],
        total_duration_min=elapsed,
        strategy="test",
        sla_violations=1 if elapsed > 35 else 0,
        osrm_fallback_used=False,
    )


def _result(plan=None):
    plan = plan or _plan()
    best = DP.Candidate(
        courier_id="c1", name="Test", score=77.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=plan, metrics={"loadgov_load_ewma": 2.0},
    )
    return DP.PipelineResult(
        order_id="N", verdict="PROPOSE", reason="feasible=1 best=c1",
        best=best, candidates=[best], pickup_ready_at=NOW_UTC,
        restaurant="R", delivery_address="A",
        pool_total_count=1, pool_feasible_count=1,
    )


def _patch_wrapper(monkeypatch, result, flags=None):
    flags = dict(flags or {})
    seen = {}

    def fake_impl(order_event, fleet_snapshot, restaurant_meta, now, **kwargs):
        seen["impl_now"] = now
        return result

    monkeypatch.setattr(DP, "_assess_order_impl", fake_impl)
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *a, **k: None)
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(C, "load_flags", lambda: dict(flags))
    monkeypatch.setattr(
        candidate_logger, "get_logger",
        lambda: NS(_flag_check=lambda: False, log_evaluation=lambda **kwargs: None),
    )
    return seen


def test_single_hook_uses_same_normalized_now_and_preserves_decision_identity(monkeypatch):
    result = _result()
    best_before = result.best
    plan_before = result.best.plan
    score_before = result.best.score
    sequence_before = list(result.best.plan.sequence)
    verdict_before = result.verdict
    flags = {
        "ENABLE_ALWAYS_PROPOSE_ON_SATURATION": True,
        "ENABLE_PACZKA_R6_THERMAL_EXEMPT": True,
        "ENABLE_SLA_ANCHOR_UNIFIED": True,
        "ENABLE_SLA_GATE_READY_ANCHOR": True,
    }
    seen = _patch_wrapper(monkeypatch, result, flags)
    real_evaluate = FW.evaluate_final
    calls = []

    def spy(res, event, fleet, now, policy):
        calls.append((res, now, policy))
        return real_evaluate(res, event, fleet, now, policy)

    monkeypatch.setattr(FW, "evaluate_final", spy)
    out = DP.assess_order(
        {"order_id": "N", "address_id": None, "order_type": "elastic"},
        {"c1": NS(bag=[])}, now=NOW_NAIVE,
    )

    assert out is result
    assert len(calls) == 1
    assert seen["impl_now"] is calls[0][1]
    assert calls[0][1] == NOW_UTC and calls[0][1].tzinfo == timezone.utc
    assert calls[0][2].sla_anchor_kind == "ready"
    assert calls[0][2].always_propose_enabled is True

    # Kontrakt neutralnosci: nawet tozsamosc obiektow best/plan zostaje.
    assert result.verdict == verdict_before
    assert result.best is best_before
    assert result.best.plan is plan_before
    assert result.best.score == score_before
    assert result.best.plan.sequence == sequence_before
    assert result.rule_verdict.status == FW.VIOLATION_INTRODUCED
    assert result.rule_verdict.physical_status == FW.VIOLATION


@pytest.mark.parametrize(
    "unified,ready,expected",
    [(False, False, "now"), (False, True, "now"),
     (True, False, "now"), (True, True, "ready")],
)
def test_ready_sla_anchor_requires_both_effective_flags(monkeypatch, unified, ready, expected):
    result = _result(_plan(elapsed=20.0))
    flags = {
        "ENABLE_SLA_ANCHOR_UNIFIED": unified,
        "ENABLE_SLA_GATE_READY_ANCHOR": ready,
    }
    _patch_wrapper(monkeypatch, result, flags)
    real_evaluate = FW.evaluate_final
    policies = []

    def spy(res, event, fleet, now, policy):
        policies.append(policy)
        return real_evaluate(res, event, fleet, now, policy)

    monkeypatch.setattr(FW, "evaluate_final", spy)
    DP.assess_order({"order_id": "N"}, {"c1": NS(bag=[])}, now=NOW_UTC)
    assert len(policies) == 1
    assert policies[0].sla_anchor_kind == expected


def test_hook_failure_sets_explicit_unknown_without_touching_decision(monkeypatch):
    result = _result()
    before = (result.verdict, result.best, result.best.score,
              result.best.plan, list(result.best.plan.sequence))
    _patch_wrapper(monkeypatch, result)

    def boom(*args, **kwargs):
        raise RuntimeError("instrument failed")

    monkeypatch.setattr(FW, "evaluate_final", boom)
    out = DP.assess_order({"order_id": "N"}, {"c1": NS(bag=[])}, now=NOW_UTC)
    assert out.rule_verdict.status == FW.UNKNOWN
    assert out.rule_verdict.coverage == FW.NONE
    assert out.rule_verdict.missing_reasons == ("EVALUATOR_ERROR:RuntimeError",)
    after = (result.verdict, result.best, result.best.score,
             result.best.plan, list(result.best.plan.sequence))
    assert after == before


def test_evaluator_fallback_and_logger_double_failure_uses_final_json_unknown(monkeypatch):
    result = _result()
    before = (result.verdict, result.best, result.best.score,
              result.best.plan, list(result.best.plan.sequence))
    _patch_wrapper(monkeypatch, result)
    monkeypatch.setattr(
        FW, "evaluate_final",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("evaluate")),
    )
    monkeypatch.setattr(
        FW, "error_verdict",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fallback")),
    )
    monkeypatch.setattr(
        DP.log, "warning",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logger")),
    )

    out = DP.assess_order({"order_id": "N"}, {"c1": NS(bag=[])}, now=NOW_UTC)
    _assert_complete_unknown_dict(out.rule_verdict)
    assert out.rule_verdict["missing_reasons"] == ["FINAL_FALLBACK:RuntimeError"]
    after = (result.verdict, result.best, result.best.score,
             result.best.plan, list(result.best.plan.sequence))
    assert after == before


def test_firewall_import_failure_is_last_resort_unknown_and_neutral(monkeypatch):
    result = _result()
    before = (result.verdict, result.best, result.best.score,
              result.best.plan, list(result.best.plan.sequence))
    _patch_wrapper(monkeypatch, result)
    real_import = DP.importlib.import_module

    def fail_firewall_import(name, package=None):
        if name == "dispatch_v2.core.invariant_firewall":
            raise ImportError("unavailable")
        return real_import(name, package)

    monkeypatch.setattr(DP.importlib, "import_module", fail_firewall_import)
    out = DP.assess_order({"order_id": "N"}, {"c1": NS(bag=[])}, now=NOW_UTC)
    _assert_complete_unknown_dict(out.rule_verdict)
    assert out.rule_verdict["missing_reasons"] == ["FINAL_FALLBACK:ImportError"]
    after = (result.verdict, result.best, result.best.score,
             result.best.plan, list(result.best.plan.sequence))
    assert after == before


def test_shadow_serializer_emits_rule_verdict_as_dict(monkeypatch):
    result = DP.PipelineResult(
        order_id="N", verdict="KOORD", reason="early_bird",
        best=None, candidates=[], pickup_ready_at=None, restaurant="R",
    )
    policy = FW.FirewallPolicy(
        35.0, 35.0, 5.0, 10.0, 4.5, (161, 232), True, "now", False)
    result.rule_verdict = FW.evaluate_final(
        result, {"order_id": "N", "order_type": "czasowka"}, {}, NOW_UTC, policy)
    monkeypatch.setattr(C, "flag", lambda *a, **k: False)
    record = SD._serialize_result(result, event_id="e1", latency_ms=1.0)
    rv = record["rule_verdict"]
    assert isinstance(rv, dict)
    assert rv["schema"] == "rule_verdict.v2"
    assert rv["status"] == FW.NOT_APPLICABLE
    assert rv["violations"] == []
    assert rv["missing_reasons"] == ["NO_SELECTED_PLAN:early_bird"]


def test_serializer_failure_is_unknown_not_dropped():
    result = DP.PipelineResult(
        order_id="N", verdict="PROPOSE", reason="r", best=None,
        candidates=[], pickup_ready_at=None, restaurant="R",
    )
    result.rule_verdict = NS(to_dict=lambda: (_ for _ in ()).throw(TypeError("bad")))
    rv = SD._serialize_rule_verdict(result)
    _assert_complete_unknown_dict(rv)
    assert rv["missing_reasons"] == ["SERIALIZER_ERROR:TypeError"]
