"""H1-D1 OD-07: EXEMPT dotyczy tylko związanego possession->handoff."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS

import pytest

from dispatch_v2 import common as C
from dispatch_v2.core import invariant_firewall as FW


NOW = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
FLAG = "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH"


def _policy(enabled=False):
    return FW.FirewallPolicy(
        r6_limit_min=35.0,
        sla_limit_min=35.0,
        r27_strict_limit_min=5.0,
        r27_overload_limit_min=10.0,
        overload_threshold=4.5,
        package_address_ids=(161, 232, 233, 234, 235, 236),
        package_thermal_exempt=True,
        sla_anchor_kind="now",
        always_propose_enabled=False,
        od07_firewall_exempt_truth_enabled=enabled,
    )


def _result(*, carried=False, tier="silver"):
    oids = ["B", "N"] if carried else ["N"]
    plan = NS(
        per_order_delivery_times={oid: 20.0 for oid in oids},
        predicted_delivered_at={
            oid: NOW + timedelta(minutes=20) for oid in oids
        },
        pickup_at={"N": NOW},
        sequence=oids,
        total_duration_min=20.0,
        strategy="a360_d1_od07_synthetic",
        sla_violations=0,
        osrm_fallback_used=False,
    )
    best = NS(
        courier_id="c1",
        plan=plan,
        metrics={"loadgov_load_ewma": 2.0, "courier_tier": tier},
        best_effort=False,
        score=77.0,
        feasibility_verdict="MAYBE",
    )
    return NS(
        order_id="N",
        verdict="PROPOSE",
        reason="synthetic a360-d1 od07",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
    )


def _event(**extra):
    event = {"order_id": "N", "address_id": 1, "order_type": "elastic"}
    event.update(extra)
    return event


def _fleet(carried=False):
    bag = []
    if carried:
        bag.append({
            "order_id": "B",
            "address_id": 1,
            "order_type": "elastic",
            "status": "picked_up",
            # Sam status pozostaje proxy; nie wolno nim zbudować interwału R6.
        })
    return {"c1": NS(bag=bag)}


def _interval(
        age_min, *, baseline_age_min=None, mode=FW.R6_MODE_NORMAL,
        baseline_mode=FW.R6_MODE_NORMAL):
    return FW.R6IntervalEvidence(
        physical_possession_at=NOW,
        customer_handoff_at=NOW + timedelta(minutes=age_min),
        event_contract_version="synthetic.od07.events.v1",
        physical_possession_source="synthetic_physical_possession",
        customer_handoff_source="synthetic_customer_handoff",
        cohort="synthetic_test",
        event_gate_status=FW.R6_EVENT_GATE_BOUND,
        mode=mode,
        mode_contract_version=(
            "synthetic.od07.mode.v1" if mode != FW.R6_MODE_UNBOUND else ""),
        predecision_customer_handoff_at=(
            None if baseline_age_min is None
            else NOW + timedelta(minutes=baseline_age_min)),
        predecision_mode=baseline_mode,
        predecision_mode_contract_version=(
            "synthetic.od07.mode.v1" if baseline_age_min is not None else ""),
        counterfactual_contract_version=(
            "synthetic.od07.counterfactual.v1"
            if baseline_age_min is not None else ""),
    )


def _evaluate(*, event=None, intervals=None, carried=False, tier="silver"):
    return FW.evaluate_final(
        _result(carried=carried, tier=tier),
        event or _event(),
        _fleet(carried),
        NOW,
        _policy(enabled=True),
        r6_intervals=intervals,
    )


def _r6_rule(verdict):
    return next(r for r in verdict.rules if r.rule_id == FW.R6_THERMAL)


def test_flag_off_is_byte_identical_and_ignores_new_evidence_seam(monkeypatch):
    # Systemowy env testowy może nie mieć OR-Tools, więc pinujemy istniejący
    # legacy-anchor; test dotyczy wyłącznie neutralności nowego gate'a.
    monkeypatch.setattr(
        FW._sla_anchor,
        "ready_anchor",
        lambda *_args, **_kwargs: (NOW, "test_bound_ready", False),
    )
    result = _result()
    event = _event()
    fleet = _fleet()
    baseline = FW.evaluate_final(result, event, fleet, NOW, _policy(False))
    with_od07_input = FW.evaluate_final(
        result,
        event,
        fleet,
        NOW,
        _policy(False),
        r6_intervals={"N": _interval(99.0, baseline_age_min=20.0)},
    )

    baseline_bytes = json.dumps(
        baseline.to_dict(), sort_keys=True, separators=(",", ":"))
    comparison_bytes = json.dumps(
        with_od07_input.to_dict(), sort_keys=True, separators=(",", ":"))
    assert baseline_bytes == comparison_bytes
    assert baseline.schema == FW.SCHEMA == "rule_verdict.v1"
    assert "physical_status" not in baseline.to_dict()


def test_on_without_bound_events_is_hold_and_never_uses_named_proxies(monkeypatch):
    ready_anchor_calls = []

    def forbidden_ready_anchor(*args, **kwargs):
        ready_anchor_calls.append((args, kwargs))
        raise AssertionError("OD-07 ON must not execute legacy ready anchor")

    monkeypatch.setattr(FW._sla_anchor, "ready_anchor", forbidden_ready_anchor)
    event = _event(
        picked_up_at=(NOW - timedelta(minutes=50)).isoformat(),
        pickup_ready_at=(NOW - timedelta(minutes=80)).isoformat(),
        restaurant_exit_at=(NOW - timedelta(minutes=45)).isoformat(),
        last_inside_at=(NOW - timedelta(minutes=44)).isoformat(),
        delivered_at=(NOW + timedelta(minutes=5)).isoformat(),
        delivery_arrival_at=(NOW + timedelta(minutes=4)).isoformat(),
        pickup_click_at=(NOW - timedelta(minutes=43)).isoformat(),
        handoff_click_at=(NOW + timedelta(minutes=6)).isoformat(),
    )

    verdict = _evaluate(event=event)

    assert verdict.schema == FW.OD07_SCHEMA
    assert verdict.status == FW.HOLD
    assert verdict.physical_status == FW.UNBOUND
    assert verdict.r6_event_binding == FW.UNBOUND
    assert not [v for v in verdict.violations if v.rule_id == FW.R6_THERMAL]
    assert {
        "R6_PHYSICAL_POSSESSION_EVENT_UNBOUND:N",
        "R6_CUSTOMER_HANDOFF_EVENT_UNBOUND:N",
    } <= set(verdict.missing_reasons)
    rule = _r6_rule(verdict)
    assert rule.interval == FW.R6_INTERVAL
    assert rule.food_ready_age_status == "SEPARATE_UNBOUND"
    assert rule.food_ready_age_threshold_min is None
    assert ready_anchor_calls == []


@pytest.mark.parametrize(
    "age,mode,expected_physical,expected_impact",
    [
        (35.0, FW.R6_MODE_NORMAL, FW.PASS, FW.PASS),
        (35.001, FW.R6_MODE_NORMAL, FW.VIOLATION, FW.VIOLATION_INTRODUCED),
        (40.0, FW.R6_MODE_NORMAL, FW.VIOLATION, FW.VIOLATION_INTRODUCED),
        (40.0, FW.R6_MODE_ALARM, FW.ALARM, FW.ALARM),
        (40.001, FW.R6_MODE_ALARM, FW.PROHIBITED, FW.VIOLATION_INTRODUCED),
    ],
)
def test_od07_boundaries_are_35_normal_40_alarm_only_for_every_class(
        age, mode, expected_physical, expected_impact):
    verdict = _evaluate(intervals={"N": _interval(age, mode=mode)})

    assert verdict.physical_status == expected_physical
    assert verdict.status == expected_impact
    rule = _r6_rule(verdict)
    assert rule.normal_limit_min == 35.0
    assert rule.alarm_limit_min == 40.0
    assert rule.policy_variant == "in_vehicle_age_od07"
    assert len(rule.evidence_lineage) == 1
    assert rule.evidence_lineage[0].event_contract_version == (
        "synthetic.od07.events.v1")
    assert rule.evidence_lineage[0].cohort == "synthetic_test"


def test_alarm_window_without_bound_alarm_predicate_is_hold_not_normal_or_alarm():
    verdict = _evaluate(intervals={
        "N": _interval(36.0, mode=FW.R6_MODE_UNBOUND),
    })

    assert verdict.status == FW.HOLD
    assert verdict.physical_status == FW.HOLD
    assert "R6_ALARM_PREDICATE_UNBOUND:N" in verdict.missing_reasons


def test_mutation_probe_preexisting_direction_and_worsening_are_not_symmetric():
    new_order_pass = _interval(20.0)
    improved = _evaluate(
        carried=True,
        intervals={
            "N": new_order_pass,
            "B": _interval(36.0, baseline_age_min=37.0),
        },
    )
    worsened = _evaluate(
        carried=True,
        intervals={
            "N": new_order_pass,
            "B": _interval(37.0, baseline_age_min=36.0),
        },
    )

    assert improved.status == FW.EXEMPT_PREEXISTING
    assert improved.physical_status == FW.VIOLATION
    improved_row = next(v for v in improved.violations if v.order_id == "B")
    assert improved_row.status == FW.EXEMPT_PREEXISTING
    assert improved_row.exception_reason == "R6_BREACH_PREEXISTING_NOT_WORSENED"
    assert worsened.status == FW.VIOLATION_INTRODUCED
    worsened_row = next(v for v in worsened.violations if v.order_id == "B")
    assert worsened_row.status == FW.VIOLATION_INTRODUCED


def test_carried_breach_without_counterfactual_is_hold_not_exempt():
    verdict = _evaluate(
        carried=True,
        intervals={"N": _interval(20.0), "B": _interval(36.0)},
    )

    assert verdict.status == FW.HOLD
    assert verdict.physical_status == FW.VIOLATION
    assert "R6_PREDECISION_COUNTERFACTUAL_UNBOUND:B" in verdict.missing_reasons
    row = next(v for v in verdict.violations if v.order_id == "B")
    assert row.status == FW.HOLD


def test_food_ready_age_and_courier_class_do_not_change_bound_r6():
    evidence = {"N": _interval(34.0)}
    first = _evaluate(
        event=_event(food_ready_age=1, food_ready_at=NOW.isoformat()),
        intervals=evidence,
        tier="silver",
    ).to_dict()
    second = _evaluate(
        event=_event(
            food_ready_age=9999,
            food_ready_at=(NOW - timedelta(days=7)).isoformat(),
        ),
        intervals=evidence,
        tier="gold",
    ).to_dict()

    assert first["rules"][0] == second["rules"][0]
    assert first["physical_status"] == second["physical_status"] == FW.PASS


@pytest.mark.parametrize(
    "bad_evidence",
    [
        {
            "physical_possession_at": NOW.isoformat(),
            "customer_handoff_at": (NOW + timedelta(minutes=20)).isoformat(),
        },
        FW.R6IntervalEvidence(
            physical_possession_at=NOW.isoformat(),
            customer_handoff_at=NOW + timedelta(minutes=20),
            mode=FW.R6_MODE_NORMAL,
        ),
    ],
)
def test_untyped_or_string_event_contract_is_rejected_to_unbound(bad_evidence):
    verdict = _evaluate(intervals={"N": bad_evidence})

    assert verdict.status == FW.HOLD
    assert verdict.physical_status == FW.UNBOUND
    assert verdict.r6_event_binding == FW.UNBOUND


@pytest.mark.parametrize(
    "field,proxy",
    [
        ("physical_possession_source", "panel.picked_up_at"),
        ("physical_possession_source", "restaurant_exit_click"),
        ("customer_handoff_source", "routing.delivery_arrival"),
        ("customer_handoff_source", "courier_handoff_click"),
    ],
)
def test_named_proxy_cannot_be_attested_as_physical_event(field, proxy):
    forged = replace(_interval(20.0), **{field: proxy})
    verdict = _evaluate(intervals={"N": forged})

    assert verdict.status == FW.HOLD
    assert verdict.physical_status == FW.UNBOUND
    assert "R6_PHYSICAL_EVENT_PROVENANCE_UNBOUND:N" in verdict.missing_reasons
    assert _r6_rule(verdict).evidence_lineage == ()


def test_pipeline_and_shadow_wiring_emit_one_v3_without_mutating_decision(
        monkeypatch):
    from dispatch_v2 import dispatch_pipeline as DP
    from dispatch_v2 import shadow_dispatcher as SD
    from dispatch_v2.observability import candidate_logger

    base = _result()
    best = DP.Candidate(
        courier_id="c1",
        name="Synthetic",
        score=base.best.score,
        feasibility_verdict="MAYBE",
        feasibility_reason="synthetic",
        plan=base.best.plan,
        metrics=base.best.metrics,
    )
    result = DP.PipelineResult(
        order_id="N",
        verdict="PROPOSE",
        reason="synthetic a360-d1 od07",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
        restaurant="Synthetic Restaurant",
        delivery_address="Synthetic Address",
        pool_total_count=1,
        pool_feasible_count=1,
    )
    best_before = result.best
    plan_before = result.best.plan
    score_before = result.best.score
    sequence_before = list(result.best.plan.sequence)
    flags = {FLAG: True}
    monkeypatch.setattr(C, "load_flags", lambda: dict(flags))
    monkeypatch.setattr(
        C, "flag", lambda name, fallback=False: flags.get(name, fallback))
    monkeypatch.setattr(
        DP, "_assess_order_impl",
        lambda *_args, **_kwargs: result,
    )
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *_a, **_k: None)
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(
        candidate_logger,
        "get_logger",
        lambda: NS(
            _flag_check=lambda: False,
            log_evaluation=lambda **_kwargs: None,
        ),
    )

    out = DP.assess_order(_event(), _fleet(), now=NOW)
    record = SD._serialize_result(out, event_id="synthetic", latency_ms=1.0)
    serialized = record["rule_verdict"]

    assert serialized["schema"] == FW.OD07_SCHEMA
    assert serialized["status"] == FW.HOLD
    assert serialized["physical_status"] == FW.UNBOUND
    assert out is result
    assert result.best is best_before
    assert result.best.plan is plan_before
    assert result.best.score == score_before
    assert result.best.plan.sequence == sequence_before
    assert result.verdict == "PROPOSE"


def test_pipeline_and_shadow_last_resort_v3_fallbacks_stay_twin(monkeypatch):
    from dispatch_v2 import dispatch_pipeline as DP
    from dispatch_v2 import shadow_dispatcher as SD

    class BrokenVerdict:
        def to_dict(self):
            raise RuntimeError("synthetic serializer fault")

    monkeypatch.setattr(C, "flag", lambda name, fallback=False: name == FLAG)
    result = _result()
    pipeline_fallback = DP._final_rule_unknown_dict(result, "SYNTHETIC")
    broken_result = NS(
        order_id=result.order_id,
        verdict=result.verdict,
        best=result.best,
        rule_verdict=BrokenVerdict(),
    )
    shadow_fallback = SD._serialize_rule_verdict(broken_result)

    assert pipeline_fallback["schema"] == shadow_fallback["schema"] == FW.OD07_SCHEMA
    assert pipeline_fallback["rules"] == shadow_fallback["rules"]
    assert pipeline_fallback["policy_pending"] == shadow_fallback["policy_pending"]
    assert pipeline_fallback["physical_status"] == shadow_fallback["physical_status"]
    assert pipeline_fallback["r6_event_binding"] == shadow_fallback["r6_event_binding"]
