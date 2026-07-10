"""Z-P0-01 faza A: czysty finalny RuleVerdict R6/R27/SLA."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from dispatch_v2.core import invariant_firewall as FW


NOW = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)


def _policy(**overrides):
    data = dict(
        r6_limit_min=35.0,
        sla_limit_min=35.0,
        r27_strict_limit_min=5.0,
        r27_overload_limit_min=10.0,
        overload_threshold=4.5,
        package_address_ids=(161, 232, 233, 234, 235, 236),
        package_thermal_exempt=True,
        sla_anchor_kind="now",
        always_propose_enabled=False,
    )
    data.update(overrides)
    return FW.FirewallPolicy(**data)


def _plan(*, pod, predicted, pickup, sla_violations=0):
    return NS(
        per_order_delivery_times=dict(pod),
        predicted_delivered_at=dict(predicted),
        pickup_at=dict(pickup),
        sequence=list(predicted),
        sla_violations=sla_violations,
    )


def _result(plan, *, oid="N", metrics=None, best_effort=False,
            verdict="PROPOSE", reason="feasible=1 best=c1", best=True):
    cand = None if not best else NS(
        courier_id="c1", plan=plan, metrics=dict(metrics or {}),
        best_effort=best_effort, score=77.0, feasibility_verdict="MAYBE",
    )
    return NS(
        order_id=oid, verdict=verdict, reason=reason, best=cand,
        candidates=([cand] if cand else []), pickup_ready_at=NOW,
    )


def _event(oid="N", **extra):
    out = {"order_id": oid, "address_id": None, "order_type": "elastic"}
    out.update(extra)
    return out


def _violations(rv, rule_id):
    return [v for v in rv.violations if v.rule_id == rule_id]


@pytest.mark.parametrize(
    "elapsed, expected",
    [(35.0, 0), (35.001, 2)],
)
def test_strict_boundary_r6_and_sla_and_required_schema(elapsed, expected):
    plan = _plan(
        pod={"N": elapsed},
        predicted={"N": NOW + timedelta(minutes=elapsed)},
        pickup={"N": NOW},
    )
    rv = FW.evaluate_final(
        _result(plan), _event(), {"c1": NS(bag=[])}, NOW,
        _policy(package_thermal_exempt=False),
    )
    assert len(rv.violations) == expected
    assert rv.status == (FW.VIOLATION if expected else FW.PASS)
    for row in rv.to_dict()["violations"]:
        assert {
            "order_id", "rule_id", "value", "limit", "mode", "exception_reason"
        } <= set(row)
    if expected:
        assert {v.rule_id for v in rv.violations} == {
            FW.R6_THERMAL, FW.SLA_DELIVERY,
        }
        assert all(v.value == 35.001 and v.limit == 35.0 for v in rv.violations)


def test_raw_predicted_elapsed_prevents_pod_rounding_blind_spot():
    plan = _plan(
        pod={"N": 35.00},
        predicted={"N": NOW + timedelta(minutes=35.004)},
        pickup={"N": NOW},
    )
    rv = FW.evaluate_final(
        _result(plan), _event(), {"c1": NS(bag=[])}, NOW,
        _policy(package_thermal_exempt=False, sla_anchor_kind="ready"),
    )
    by_rule = {v.rule_id: v for v in rv.violations}
    assert by_rule[FW.R6_THERMAL].value == 35.004
    assert by_rule[FW.SLA_DELIVERY].value == 35.004
    assert "ready_anchor+elapsed_min" in by_rule[FW.R6_THERMAL].source
    assert "ready_anchor+elapsed_min" in by_rule[FW.SLA_DELIVERY].source


def test_sla_now_uses_canonical_anchor_and_elapsed_helpers(monkeypatch):
    plan = _plan(
        pod={"N": 20.0}, predicted={"N": NOW + timedelta(minutes=20)},
        pickup={"N": NOW},
    )
    real_now_anchor = FW._sla_anchor.now_anchor
    real_elapsed = FW._sla_anchor.elapsed_min
    calls = {"now_anchor": 0, "elapsed": 0}

    def spy_now_anchor(*args, **kwargs):
        calls["now_anchor"] += 1
        return real_now_anchor(*args, **kwargs)

    def spy_elapsed(*args, **kwargs):
        calls["elapsed"] += 1
        return real_elapsed(*args, **kwargs)

    monkeypatch.setattr(FW._sla_anchor, "now_anchor", spy_now_anchor)
    monkeypatch.setattr(FW._sla_anchor, "elapsed_min", spy_elapsed)
    FW.evaluate_final(
        _result(plan), _event(), {"c1": NS(bag=[])}, NOW,
        _policy(package_thermal_exempt=False, sla_anchor_kind="now"),
    )
    assert calls["now_anchor"] == 1
    assert calls["elapsed"] == 2  # R6-ready + SLA-now


def test_mixed_bag_package_czasowka_thermal_exempt_but_r27_applies():
    plan = _plan(
        pod={"P": 80.0, "N": 36.0},
        predicted={"P": NOW + timedelta(minutes=80),
                   "N": NOW + timedelta(minutes=36)},
        pickup={"P": NOW + timedelta(minutes=7), "N": NOW},
        sla_violations=2,  # package-blind aggregate must not drive final verdict
    )
    fleet = {"c1": NS(bag=[{
        "order_id": "P", "address_id": 232, "order_type": "czasowka",
        "status": "assigned", "czas_kuriera_warsaw": NOW.isoformat(),
    }])}
    rv = FW.evaluate_final(
        _result(plan, metrics={"loadgov_load_ewma": 5.0}),
        _event(), fleet, NOW, _policy(),
    )

    assert {v.order_id for v in _violations(rv, FW.R6_THERMAL)} == {"N"}
    assert {v.order_id for v in _violations(rv, FW.SLA_DELIVERY)} == {"N"}
    r27 = _violations(rv, FW.R27_COMMITTED_PICKUP)
    assert len(r27) == 1
    assert r27[0].order_id == "P" and r27[0].limit == 5.0
    assert "order_paczka" in r27[0].mode
    assert "order_czasowka" in r27[0].mode
    assert r27[0].exception_reason == "B02_OVERLOAD_VARIANT_PENDING"
    assert {(e.order_id, e.rule_id, e.reason) for e in rv.exceptions} == {
        ("P", FW.R6_THERMAL, "PACZKA_THERMAL_EXEMPT"),
        ("P", FW.SLA_DELIVERY, "PACZKA_THERMAL_EXEMPT"),
    }
    variants = {(r.policy_variant, r.status) for r in rv.rules
                if r.rule_id == FW.R27_COMMITTED_PICKUP}
    assert variants == {("strict_5_candidate", FW.VIOLATION),
                        ("overload_10_candidate", FW.PASS)}


@pytest.mark.parametrize("late, expected_limits", [(5.0, set()), (5.001, {5.0}), (10.0, {5.0}), (10.001, {5.0, 10.0})])
def test_r27_overload_dual_policy_boundaries(late, expected_limits):
    plan = _plan(
        pod={"B": 10.0, "N": 10.0},
        predicted={"B": NOW + timedelta(minutes=20), "N": NOW + timedelta(minutes=20)},
        pickup={"B": NOW + timedelta(minutes=late), "N": NOW},
    )
    fleet = {"c1": NS(bag=[{
        "order_id": "B", "status": "assigned", "address_id": 1,
        "czas_kuriera_warsaw": NOW.isoformat(),
    }])}
    rv = FW.evaluate_final(
        _result(plan, metrics={"loadgov_load_ewma": 4.5}),
        _event(), fleet, NOW, _policy(),
    )
    got = {v.limit for v in _violations(rv, FW.R27_COMMITTED_PICKUP)}
    assert got == expected_limits
    assert rv.policy_pending == ("B-01", "B-02")
    assert rv.enforcement == "NONE"


def test_r27_naive_commit_is_warsaw_and_uses_absolute_deviation():
    # 12:00 naive Warsaw == 10:00 UTC; plan 10:06 => +6 min.
    plan = _plan(
        pod={"B": 10.0, "N": 10.0},
        predicted={"B": NOW + timedelta(minutes=20), "N": NOW + timedelta(minutes=20)},
        pickup={"B": NOW + timedelta(minutes=6), "N": NOW},
    )
    fleet = {"c1": NS(bag=[{
        "order_id": "B", "status": "assigned", "address_id": 1,
        "czas_kuriera_warsaw": "2026-07-09T12:00:00",
    }])}
    rv = FW.evaluate_final(
        _result(plan, metrics={"loadgov_load_ewma": 2.0}),
        _event(), fleet, NOW, _policy(),
    )
    assert [(v.value, v.limit) for v in _violations(rv, FW.R27_COMMITTED_PICKUP)] == [(6.0, 5.0)]
    assert "pickup_late" in _violations(rv, FW.R27_COMMITTED_PICKUP)[0].mode

    plan.pickup_at["B"] = NOW - timedelta(minutes=6)
    rv_early = FW.evaluate_final(
        _result(plan, metrics={"loadgov_load_ewma": 2.0}),
        _event(), fleet, NOW, _policy(),
    )
    early = _violations(rv_early, FW.R27_COMMITTED_PICKUP)
    assert [(v.value, v.limit) for v in early] == [(6.0, 5.0)]
    assert "pickup_early" in early[0].mode


def test_pre_existing_picked_up_breach_is_visible_with_exception_reason():
    plan = _plan(
        pod={"B": 45.0, "N": 20.0},
        predicted={"B": NOW + timedelta(minutes=5), "N": NOW + timedelta(minutes=25)},
        pickup={"N": NOW + timedelta(minutes=10)},
    )
    fleet = {"c1": NS(bag=[{
        "order_id": "B", "status": "picked_up", "address_id": 1,
        "picked_up_at": (NOW - timedelta(minutes=40)).isoformat(),
    }])}
    rv = FW.evaluate_final(_result(plan), _event(), fleet, NOW, _policy())
    for rule in (FW.R6_THERMAL, FW.SLA_DELIVERY):
        rows = _violations(rv, rule)
        assert len(rows) == 1 and rows[0].order_id == "B"
        assert rows[0].exception_reason == "PRE_EXISTING_PICKED_UP_NO_NEW_DETOUR"


def test_best_effort_always_propose_mode_does_not_hide_violation():
    plan = _plan(
        pod={"N": 50.0}, predicted={"N": NOW + timedelta(minutes=50)},
        pickup={"N": NOW},
    )
    rv = FW.evaluate_final(
        _result(plan, best_effort=True), _event(), {"c1": NS(bag=[])}, NOW,
        _policy(always_propose_enabled=True),
    )
    assert rv.selection_mode == "best_effort"
    assert rv.always_propose_enabled is True
    assert rv.status == FW.VIOLATION
    assert all("selection_best_effort" in v.mode for v in rv.violations)
    assert all("always_propose_enabled" in v.mode for v in rv.violations)


def test_no_best_is_not_applicable_but_planless_best_is_unknown():
    no_best = _result(None, best=False, verdict="KOORD", reason="early_bird (70 min ahead)")
    rv_none = FW.evaluate_final(no_best, _event(), {}, NOW, _policy())
    assert rv_none.status == FW.NOT_APPLICABLE
    assert rv_none.coverage == FW.NONE
    assert rv_none.missing_reasons == ("NO_SELECTED_PLAN:early_bird",)
    assert rv_none.violations == ()

    no_plan = _result(None, best=True, verdict="PROPOSE", reason="heuristic fallback")
    rv_missing = FW.evaluate_final(no_plan, _event(), {"c1": NS(bag=[])}, NOW, _policy())
    assert rv_missing.status == FW.UNKNOWN
    assert rv_missing.coverage == FW.NONE
    assert rv_missing.missing_reasons == ("SELECTED_PLAN_MISSING",)


def test_unknown_order_metadata_is_partial_not_silent_food_assumption():
    plan = _plan(
        pod={"N": 20.0, "X": 20.0},
        predicted={"N": NOW + timedelta(minutes=20), "X": NOW + timedelta(minutes=20)},
        pickup={"N": NOW, "X": NOW},
    )
    rv = FW.evaluate_final(
        _result(plan), _event(), {"c1": NS(bag=[])}, NOW, _policy(),
    )
    assert rv.coverage == FW.PARTIAL
    assert any(x == "R6_ORDER_METADATA_MISSING:X" for x in rv.missing_reasons)
    assert any(x == "SLA_ORDER_METADATA_MISSING:X" for x in rv.missing_reasons)
    summaries = {r.rule_id: r for r in rv.rules if r.rule_id != FW.R27_COMMITTED_PICKUP}
    assert summaries[FW.R6_THERMAL].unknown_count == 1
    assert summaries[FW.SLA_DELIVERY].unknown_count == 1


def test_incomplete_multi_order_plan_is_explicit_partial_unknown():
    plan = _plan(
        pod={"N": 20.0},
        predicted={"N": NOW + timedelta(minutes=20)},
        pickup={"B": NOW + timedelta(minutes=2), "N": NOW},
    )
    plan.sequence = ["B", "N"]
    fleet = {"c1": NS(bag=[{
        "order_id": "B", "status": "assigned", "address_id": 1,
        "pickup_ready_at": NOW.isoformat(),
    }])}
    rv = FW.evaluate_final(
        _result(plan), _event(), fleet, NOW,
        _policy(package_thermal_exempt=False),
    )
    assert rv.status == FW.UNKNOWN
    assert rv.coverage == FW.PARTIAL
    assert "R6_PER_ORDER_DATA_MISSING:B" in rv.missing_reasons
    assert "SLA_PREDICTED_DELIVERY_MISSING:B" in rv.missing_reasons
    summaries = {r.rule_id: r for r in rv.rules}
    assert summaries[FW.R6_THERMAL].unknown_count == 1
    assert summaries[FW.SLA_DELIVERY].unknown_count == 1


def test_unknown_load_emits_both_b02_variants_and_partial_coverage():
    plan = _plan(
        pod={"B": 10.0, "N": 10.0},
        predicted={"B": NOW + timedelta(minutes=20), "N": NOW + timedelta(minutes=20)},
        pickup={"B": NOW + timedelta(minutes=7), "N": NOW},
    )
    fleet = {"c1": NS(bag=[{
        "order_id": "B", "status": "assigned", "address_id": 1,
        "czas_kuriera_warsaw": NOW.isoformat(),
    }])}
    rv = FW.evaluate_final(_result(plan), _event(), fleet, NOW, _policy())
    variants = [r.policy_variant for r in rv.rules if r.rule_id == FW.R27_COMMITTED_PICKUP]
    assert variants == ["strict_5_candidate", "overload_10_candidate"]
    assert rv.coverage == FW.PARTIAL
    assert "R27_OVERLOAD_STATE_UNKNOWN" in rv.missing_reasons


def test_evaluator_is_pure_and_error_verdict_is_explicit_unknown():
    plan = _plan(
        pod={"N": 40.0}, predicted={"N": NOW + timedelta(minutes=40)}, pickup={"N": NOW},
    )
    result = _result(plan)
    fleet = {"c1": NS(bag=[])}
    before_result = deepcopy(result)
    before_fleet = deepcopy(fleet)
    policy = _policy(package_thermal_exempt=False)
    FW.evaluate_final(result, _event(), fleet, NOW, policy)
    assert result == before_result
    assert fleet == before_fleet

    err = FW.error_verdict(result, policy, ValueError("secret detail"))
    assert err.status == FW.UNKNOWN and err.coverage == FW.NONE
    assert err.missing_reasons == ("EVALUATOR_ERROR:ValueError",)
    assert err.violations == ()


def test_decision_now_must_be_aware():
    plan = _plan(pod={"N": 10.0}, predicted={"N": NOW}, pickup={"N": NOW})
    with pytest.raises(ValueError, match="timezone-aware"):
        FW.evaluate_final(
            _result(plan), _event(), {"c1": NS(bag=[])},
            datetime(2026, 7, 9, 10, 0), _policy(),
        )
