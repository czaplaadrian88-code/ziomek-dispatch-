"""FIX-PACK 4/5: negatywne oracle dla findingów finalnych review R8 i R14.

Kontrakty:
* pending food ages from ready, never from a later planned pickup;
* HARD35 is a filter over the winner established upstream, not a selector;
* disabling the canonical carry producer makes HARD35 byte-inert.
* S1/S2/Alarm share one LE35/OVER35/EXEMPT/UNKNOWN interpretation;
* best-effort keeps calibration shadow and parser/frozen precedence.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import route_simulator_v2 as RS
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import alarm_certificate as AC
from dispatch_v2.core import carry_freshness as CF
from dispatch_v2.core import lex_window_guards as LG
from dispatch_v2.core import selection as SEL
from dispatch_v2.core import strategy2_probe as S2


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _plan(*, drop_min: float, pickup_min: float = 25.0):
    return SimpleNamespace(
        sequence=["food"],
        predicted_delivered_at={
            "food": NOW + timedelta(minutes=drop_min),
        },
        pickup_at={
            "food": NOW + timedelta(minutes=pickup_min),
        },
        per_order_delivery_times={"food": drop_min},
        sla_violations=0,
        total_duration_min=drop_min,
        strategy="fixpack4",
    )


def _pending(*, ready=NOW):
    return SimpleNamespace(
        order_id="food",
        status="assigned",
        picked_up_at=None,
        pickup_ready_at=ready,
        physical_possession_at=None,
        physical_possession_source=None,
        event_gate_status=None,
        contract_version=None,
    )


def _carry(value: float) -> dict:
    return {
        "schema": "carry_eval.v1",
        "status": "EVALUATED",
        "orders": [{
            "order_id": "food",
            "carry_min": value,
            "le_35": value <= 35.0,
            "le_40": value <= 40.0,
            "source": "proxy",
            "possession_source": "pickup_ready_at",
            "handoff_source": "predicted_delivery_with_dropoff_dwell",
        }],
        "evaluated_count": 1,
        "unknown_count": 0,
        "invalid_count": 0,
        "max_carry_min": value,
        "all_le_35": value <= 35.0,
        "all_le_40": value <= 40.0,
    }


def _candidate(cid: str, score: float, carry_min: float):
    plan = _plan(drop_min=carry_min)
    return DP.Candidate(
        cid,
        cid,
        score,
        "MAYBE",
        "ok",
        plan,
        {
            "bundle_level3_dev": None,
            "bag_size_before": 0,
            "r6_bag_size": 0,
            "pos_source": "gps",
            "new_pickup_late_min": 0.0,
            "late_pickup_committed_max": 0.0,
            "carry_eval": _carry(carry_min),
        },
    )


def _e2_reversed_result(
    monkeypatch,
    *,
    hard35: bool,
    carry_canon: bool,
    winner_carry: float,
    candidates_hook=None,
):
    a = _candidate("a", 100.0, 30.0)
    b = _candidate("b", 90.0, 30.0)
    c = _candidate("c", 80.0, winner_carry)
    if candidates_hook is not None:
        candidates_hook(a, b, c)
    enabled = {
        "ENABLE_HARD35_ENFORCE": hard35,
        "ENABLE_CARRY_CANON_V2": carry_canon,
    }
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: enabled.get(name, False),
    )
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: name == "ENABLE_E2_PLN_AB",
    )
    monkeypatch.setattr(DP, "_e2_ab_arm", lambda _order_id: "pln")
    monkeypatch.setattr(DP, "_pln_pure_resort", lambda ranked: ranked.reverse())
    monkeypatch.setattr(DP, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = SEL.SelectionContext(
        now=NOW,
        order_event={"order_id": "10"},
        order_id="10",
        restaurant="R",
        delivery_address="D",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
        new_order=SimpleNamespace(order_id="food"),
        fleet_snapshot={},
        v328_fail_causes={},
        shadow_only=True,
    )
    return SEL.select_and_emit(ctx, [a, b, c])


def test_r8_01_pending_carry_uses_ready_before_later_planned_pickup():
    """Mutation planned-before-ready changes 50 real minutes into 25."""
    got = CF.evaluate_plan(_plan(drop_min=50.0), [_pending()])
    row = got["orders"][0]
    assert row["carry_min"] == 50.0
    assert row["possession_source"] == "pickup_ready_at"
    assert got["all_le_35"] is False


def test_r8_02_planned_pickup_remains_fallback_when_ready_is_missing():
    got = CF.evaluate_plan(
        _plan(drop_min=50.0, pickup_min=25.0),
        [_pending(ready=None)],
    )
    row = got["orders"][0]
    assert row["carry_min"] == 25.0
    assert row["possession_source"] == "planned_pickup_at"


def test_r8_03_carry_and_r6_share_pending_ready_anchor():
    order = _pending()
    plan = _plan(drop_min=50.0)
    anchor, source, picked = RS.r6_thermal_anchor(
        order,
        is_new=False,
        plan_pickup_at=plan.pickup_at,
        now=NOW,
    )
    carry = CF.evaluate_plan(plan, [order])["orders"][0]
    assert (anchor, source, picked) == (NOW, "pickup_ready_at", False)
    assert carry["carry_min"] == 50.0
    assert carry["possession_source"] == source


def test_r8_04_hard35_preserves_safe_winner_selected_by_e2(monkeypatch):
    result = _e2_reversed_result(
        monkeypatch,
        hard35=True,
        carry_canon=True,
        winner_carry=30.0,
    )
    assert result.best.courier_id == "c"
    assert [c.courier_id for c in result.candidates[:3]] == ["c", "b", "a"]


def test_r8_05_hard35_filters_breach_without_reranking_survivors(monkeypatch):
    result = _e2_reversed_result(
        monkeypatch,
        hard35=True,
        carry_canon=True,
        winner_carry=40.0,
    )
    assert result.best.courier_id == "b"
    assert [c.courier_id for c in result.candidates[:2]] == ["b", "a"]
    assert result.reason == "feasible=2 best=b"
    assert result.pool_feasible_count == 2


def test_r8_06_hard35_on_carry_off_is_exact_selection_parity(monkeypatch):
    rollback = _e2_reversed_result(
        monkeypatch,
        hard35=True,
        carry_canon=False,
        winner_carry=40.0,
    )
    baseline = _e2_reversed_result(
        monkeypatch,
        hard35=False,
        carry_canon=False,
        winner_carry=40.0,
    )
    assert (
        rollback.verdict,
        rollback.reason,
        rollback.best.courier_id,
        [c.courier_id for c in rollback.candidates],
        getattr(rollback, "hard35_enforcement", None),
    ) == (
        baseline.verdict,
        baseline.reason,
        baseline.best.courier_id,
        [c.courier_id for c in baseline.candidates],
        getattr(baseline, "hard35_enforcement", None),
    )
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name, **_kwargs: name == "ENABLE_HARD35_ENFORCE",
    )
    thresholds = LG.load_thresholds()
    assert not hasattr(thresholds, "strict_absolute_cap")
    serialized_metrics = {}
    SD._propagate_prefixed_metrics(
        serialized_metrics,
        {
            "carry_eval": {"schema": "carry_eval.v1"},
            "alarm_other_hards_status": "PASSED",
            "hard35_enforcement": {"cap_min": 35.0},
        },
    )
    assert serialized_metrics == {}
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name, **_kwargs: name == "ENABLE_CARRY_CANON_V2",
    )
    SD._propagate_prefixed_metrics(
        serialized_metrics,
        {
            "carry_eval": {"schema": "carry_eval.v1"},
            "alarm_other_hards_status": "PASSED",
            "hard35_enforcement": {"cap_min": 35.0},
        },
    )
    assert serialized_metrics == {
        "carry_eval": {"schema": "carry_eval.v1"},
        "alarm_other_hards_status": "PASSED",
    }


def test_r13_09_best_effort_alert_has_single_classifier_owner(monkeypatch):
    """Classifier is sole owner; AUTO OFF cannot hide least-damage ALERT."""
    from dispatch_v2 import auto_proximity_classifier as APC

    best = _candidate("safe", 1.0, 30.0)
    best.best_effort = True
    result = DP.PipelineResult(
        order_id="food",
        verdict="PROPOSE",
        reason="best_effort (0 feasible)",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
        restaurant="R",
        delivery_address="D",
        pool_total_count=2,
        pool_feasible_count=0,
    )
    monkeypatch.setattr(C, "load_flags", lambda: {})

    DP._classify_and_set_auto_route(
        result,
        {},
        {"order_id": "food"},
        now=NOW,
    )
    assert result.auto_route == "ALERT"
    assert result.auto_route_reason == "best_effort_no_feasible (sla_viol=0)"

    # HARD35 consumes the same canonical owner; it must not need a pipeline
    # postcondition or a second routing policy.
    result.hard35_enforcement = {"cap_min": 35.0}
    DP._classify_and_set_auto_route(
        result,
        {},
        {"order_id": "food"},
        now=NOW,
    )
    assert result.auto_route == "ALERT"
    assert result.auto_route_reason == "best_effort_no_feasible (sla_viol=0)"

    # Mutation oracle for ownership: replacing the classifier removes ALERT.
    # A duplicate pipeline catch-all would make this assertion red.
    monkeypatch.setattr(
        APC,
        "classify_auto_route",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("mutant")),
    )
    DP._classify_and_set_auto_route(
        result,
        {},
        {"order_id": "food"},
        now=NOW,
    )
    assert result.auto_route == "ACK"
    assert result.auto_route_reason == "classifier_exception:RuntimeError"


def test_r8_07_source_ratchets_filter_owner_dependency_and_anchor_order():
    from dispatch_v2 import auto_proximity_classifier as APC

    selection_source = inspect.getsource(SEL.select_and_emit)
    possession_source = inspect.getsource(CF._possession)
    lex_source = inspect.getsource(LG.load_thresholds)
    serializer_source = inspect.getsource(SD._metric_owner_enabled)
    classifier_source = inspect.getsource(APC.classify_auto_route)
    edge_source = inspect.getsource(APC._detect_edge_routing)
    pipeline_owner_source = inspect.getsource(DP._classify_and_set_auto_route)
    serializer_owners = SD._METRICS_FLAG_OWNERS
    registry = json.loads(
        Path("tools/flag_lifecycle_registry.json").read_text(encoding="utf-8")
    )["flags"]
    hard35 = registry["ENABLE_HARD35_ENFORCE"]
    carry = registry["ENABLE_CARRY_CANON_V2"]
    logic_reference = Path("ZIOMEK_LOGIC_REFERENCE.md").read_text(
        encoding="utf-8"
    )
    effect_baseline = json.loads(
        Path("tools/flag_effect_baseline.json").read_text(encoding="utf-8")
    )
    assert "candidate_identity_key as _candidate_key" in selection_source
    assert "hard35_enforcement_enabled()" in selection_source
    assert "hard35_enforcement_enabled()" in lex_source
    assert "hard35_enforcement_enabled()" in serializer_source
    # The kill-switch may only demand inspection. Route/reason stays in the
    # normal edge owner, after context/calibration and parser/frozen precedence.
    assert "_mandatory_best_effort_route" not in classifier_source
    assert "_best_effort_requires_classification" in classifier_source
    assert classifier_source.index("_build_context") < (
        classifier_source.index("_detect_edge_routing")
    )
    assert "ctx.best_effort" in edge_source
    assert edge_source.index("ctx.parser_degraded") < (
        edge_source.index("ctx.best_effort")
    )
    assert edge_source.index("_has_frozen_window_violation") < (
        edge_source.index("ctx.best_effort")
    )
    assert "hard35_enforcement" not in pipeline_owner_source
    assert (
        serializer_owners["alarm_other_hards_status"]
        == "ENABLE_CARRY_CANON_V2"
    )
    assert (
        possession_source.index('"pickup_ready_at"')
        < possession_source.index('"planned_pickup_at"')
    )
    assert "ENABLE_CARRY_CANON_V2=ON" in hard35["notes"]
    assert "ENABLE_HARD35_ENFORCE jest inert" in carry["rollback"]
    assert "`HARD35=true` przy `CARRY_CANON_V2=false`" in logic_reference
    assert "ENABLE_E2_PLN_AB" not in effect_baseline["baseline"]
    assert effect_baseline["_count"] == len(effect_baseline["baseline"])


def test_r16_01_hard35_repick_rebuilds_owner_pickup_redirect(monkeypatch):
    """Negative oracle: redirect must describe the final, safe winner."""
    monkeypatch.setattr(C, "ENABLE_LATE_PICKUP_HARD_GATE", True)
    monkeypatch.setattr(C, "ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", False)

    def configure(a, b, c):
        a.metrics.update({
            "new_pickup_needs_extension": True,
            "new_pickup_eta_iso": "2026-07-29T13:10:00+00:00",
            "new_pickup_late_min": 60.0,
        })
        b.metrics.update({
            "new_pickup_needs_extension": True,
            "new_pickup_eta_iso": "2026-07-29T12:17:00+00:00",
            "new_pickup_late_min": 7.0,
        })
        c.metrics.update({
            "new_pickup_needs_extension": True,
            "new_pickup_eta_iso": "2026-07-29T12:05:00+00:00",
            "new_pickup_late_min": 5.0,
        })

    result = _e2_reversed_result(
        monkeypatch,
        hard35=True,
        carry_canon=True,
        winner_carry=40.0,
        candidates_hook=configure,
    )
    # E2/late-pickup ranking owns survivor order.  This oracle owns only the
    # post-HARD35 contract: the redirect must be rebuilt from whichever safe
    # survivor is final, never retained from rejected winner ``c``.
    assert result.best.courier_id in {"a", "b"}
    assert result.pickup_extension_redirect == {
        "tier": 1,
        "courier_id": result.best.courier_id,
        "suggested_pickup_iso": result.best.metrics["new_pickup_eta_iso"],
        "new_pickup_late_min": result.best.metrics["new_pickup_late_min"],
        "committed_breach_min": None,
        "committed_worst_restaurant": None,
    }
    assert result.pickup_extension_redirect["courier_id"] != "c"
    assert (
        result.pickup_extension_redirect["suggested_pickup_iso"]
        != "2026-07-29T12:05:00+00:00"
    )


def test_r16_02_solo_hard35_keeps_unevaluated_display_pool(monkeypatch):
    """HARD35 may filter only rows it evaluated, never erase solo alternatives."""
    original = [_candidate("a", 2.0, 30.0), _candidate("b", 1.0, 30.0)]
    for candidate in original:
        candidate.feasibility_verdict = "NO"
        candidate.plan = None

    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name in {
            "ENABLE_CARRY_CANON_V2",
            "ENABLE_HARD35_ENFORCE",
        },
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kwargs: (
            "MAYBE",
            "solo",
            {
                "pickup_dist_km": (
                    1.0 if _kwargs["courier_pos"][0] == 53.10 else 2.0
                ),
                "carry_eval": _carry(30.0),
            },
            _plan(drop_min=30.0),
        ),
    )
    monkeypatch.setattr(DP, "_classify_and_set_auto_route", lambda *a, **k: None)
    fleet = {
        "a": SimpleNamespace(
            pos=(53.10, 23.10),
            name="a",
            pos_source="gps",
            shift_start=None,
            shift_end=None,
            available_from=None,
            tier_bag=None,
            schedule_source_stale=False,
            pos_from_store=False,
        ),
        "b": SimpleNamespace(
            pos=(53.20, 23.20),
            name="b",
            pos_source="gps",
            shift_start=None,
            shift_end=None,
            available_from=None,
            tier_bag=None,
            schedule_source_stale=False,
            pos_from_store=False,
        ),
    }
    result = SEL.select_and_emit(
        SEL.SelectionContext(
            now=NOW,
            order_event={"order_id": "food"},
            order_id="food",
            restaurant="R",
            delivery_address="D",
            pickup_coords=(53.13, 23.16),
            delivery_coords=(53.14, 23.17),
            pickup_ready_at=NOW,
            new_order=SimpleNamespace(order_id="food"),
            fleet_snapshot=fleet,
            v328_fail_causes={},
            shadow_only=True,
        ),
        original,
    )
    assert result.reason == "solo_fallback (R1/R5/R8 ignored, fleet_n=2)"
    assert result.best.courier_id == "a"
    assert [candidate.courier_id for candidate in result.candidates] == ["a", "b"]
    assert (
        CF.hard35_status(result.best.metrics["carry_eval"])
        == CF.HARD35_LE35
    )


def test_r16_03_plan_continuity_and_pool_coverage_source_ratchets():
    """Mutation/duplicate-owner ratchet for all four R15 review findings."""
    plan_source = Path("plan_recheck.py").read_text(encoding="utf-8")
    guard_source = Path("core/lex_window_guards.py").read_text(encoding="utf-8")
    selection_source = inspect.getsource(SEL.select_and_emit)
    assert "strict_absolute_cap" not in plan_source
    assert "strict_absolute_cap" not in guard_source
    assert "carry_cap = max(_alarm_cap, bcarry)" in plan_source
    assert "_pickup_extension_redirect_for(result.best)" in selection_source
    assert "candidate_pool_is_complete=False" in selection_source


def _thermal_exempt_carry() -> dict:
    return {
        "schema": "carry_eval.v1",
        "status": "UNEVALUABLE",
        "orders": [],
        "evaluated_count": 0,
        "unknown_count": 0,
        "invalid_count": 0,
        "max_carry_min": None,
        "all_le_35": None,
        "all_le_40": None,
        "thermal_scope": {
            "schema": "carry_thermal_scope.v1",
            "status": "EXEMPT",
            "reason": "paczka_r6_thermal_exempt",
            "order_count": 1,
        },
    }


def test_r15_01_s2_legal_no_and_thermal_exempt_are_complete():
    """Negative oracle: real NO and parcel EXEMPT cannot poison the S2 slot."""
    rows = [
        {
            "courier_id": "early-no",
            "status": S2.classify_courier_status("NO", CF.HARD35_UNKNOWN),
            "feasibility_verdict": "NO",
            "carry_status": "NOT_APPLICABLE",
            "hard35_status": CF.HARD35_UNKNOWN,
            "all_le_35": None,
        },
        {
            "courier_id": "parcel",
            "status": S2.classify_courier_status(
                "MAYBE", CF.HARD35_EXEMPT
            ),
            "feasibility_verdict": "MAYBE",
            "carry_status": "UNEVALUABLE",
            "hard35_status": CF.HARD35_EXEMPT,
            "all_le_35": None,
        },
    ]
    got = S2.probe(
        order_id="parcel",
        created_at=NOW - timedelta(minutes=85),
        declared_ready_at=NOW,
        now=NOW,
        fleet_ids=["early-no", "parcel"],
        evaluate_slot=lambda _slot, _fleet: {
            "status": "EVALUATED",
            "couriers": rows,
        },
    )
    assert got["status"] == "EVALUATED"
    assert got["found"] is True
    assert got["courier_id"] == "parcel"


def test_r15_02_s1_s2_alarm_share_thermal_exempt_owner():
    carry = _thermal_exempt_carry()
    parcel = DP.Candidate(
        "parcel",
        "parcel",
        1.0,
        "MAYBE",
        "ok",
        _plan(drop_min=30.0),
        {"carry_eval": carry},
    )
    assert CF.hard35_status(carry) == CF.HARD35_EXEMPT
    assert DP._strategy2_required([parcel]) is False
    assert AC._candidate_hard35_eval(parcel) == ("EXEMPT", None)
    assert S2.classify_courier_status(
        parcel.feasibility_verdict,
        CF.hard35_status(carry),
    ) == "SAFE_LE35"


def test_r15_03_s2_status_owner_mutation_turns_complete_slot_unknown(
    monkeypatch,
):
    row = {
        "courier_id": "early-no",
        "status": "NO_SAFE_PLAN",
        "feasibility_verdict": "NO",
        "carry_status": "NOT_APPLICABLE",
        "hard35_status": CF.HARD35_UNKNOWN,
        "all_le_35": None,
    }
    assert S2._complete_courier_result(row) is True
    monkeypatch.setattr(
        S2,
        "classify_courier_status",
        lambda _verdict, _hard35: "UNEVALUABLE",
    )
    assert S2._complete_courier_result(row) is False


def test_r15_04_best_effort_keeps_shadow_and_priority(monkeypatch):
    from dispatch_v2 import auto_proximity_classifier as APC

    best = _candidate("least-damage", 1.0, 30.0)
    best.best_effort = True
    best.metrics["drive_min"] = 12.0
    best.metrics["pos_source"] = "gps"
    best.metrics["v3274_frozen_window_violation"] = True
    result = DP.PipelineResult(
        order_id="food",
        verdict="PROPOSE",
        reason="best_effort (0 feasible)",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
        restaurant="R",
        delivery_address="D",
        pool_total_count=2,
        pool_feasible_count=0,
    )
    emitted = []
    monkeypatch.setattr(
        APC,
        "_append_drive_min_calibration_shadow",
        emitted.append,
    )
    monkeypatch.setattr(
        APC._drive_calib,
        "apply_calibration",
        lambda raw, _ctx: (
            float(raw),
            {
                "raw_drive_min": float(raw),
                "calibrated_drive_min": float(raw),
                "offset_applied": 0.0,
                "floor_hit": False,
                "calibration_version": "r15-test",
            },
        ),
    )
    flags = {
        "AUTO_PROXIMITY_ENABLED": False,
        "AUTO_PROXIMITY_SHADOW_ONLY": False,
        "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW": True,
        "PARSER_DEGRADED": True,
    }
    route, reason = APC.classify_auto_route(
        result,
        flags=flags,
        order_event={"order_id": "food"},
        now=NOW,
    )
    assert (route, reason) == ("ALERT", "parser_degraded")
    assert len(emitted) == 1

    flags["PARSER_DEGRADED"] = False
    route, reason = APC.classify_auto_route(
        result,
        flags=flags,
        order_event={"order_id": "food"},
        now=NOW,
    )
    assert (route, reason) == ("ALERT", "frozen_window_violation")
    assert len(emitted) == 2

    del best.metrics["v3274_frozen_window_violation"]
    route, reason = APC.classify_auto_route(
        result,
        flags=flags,
        order_event={"order_id": "food"},
        now=NOW,
    )
    assert (route, reason) == (
        "ALERT",
        "best_effort_no_feasible (sla_viol=0)",
    )
    assert len(emitted) == 3


def test_r15_05_contract_ownership_ratchet():
    strategy_required = inspect.getsource(DP._strategy2_required)
    s2_owner = inspect.getsource(S2.classify_courier_status)
    s2_consumer = inspect.getsource(S2._complete_courier_result)
    certificate = inspect.getsource(AC._strategy2_fingerprint)
    alarm_consumer = inspect.getsource(AC._candidate_hard35_eval)
    pipeline = inspect.getsource(DP._assess_order_impl)

    assert "_carry_contract.hard35_status" in strategy_required
    assert "_carry_contract.hard35_evaluation" in alarm_consumer
    assert "classify_courier_status" in s2_consumer
    assert "_strategy2._complete_courier_result" in certificate
    assert "_s2.classify_courier_status" in pipeline
    assert "_safe =" not in pipeline
    assert "carry_status == \"EVALUATED\"" not in certificate
    assert "all_le_35 is False" not in certificate
    assert "verdict == \"NO\"" in s2_owner
