"""FIX-PACK 2: regresje blind-review r2 F-A..F-N.

F-O i F-P wzmacniają istniejące ratchety w
``test_escalation_ladder_night_2026_07_28.py``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import route_simulator_v2 as RS
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import alarm_certificate as AC
from dispatch_v2.core import carry_freshness as CF
from dispatch_v2.core import selection
from dispatch_v2.core import strategy2_probe as S2


NOW = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)


def _carry(value, *, status="EVALUATED"):
    rows = []
    if status == "EVALUATED" and value is not None:
        rows.append({
            "order_id": "order",
            "carry_min": value,
            "le_35": value <= 35.0,
            "le_40": value <= 40.0,
        })
    return {
        "schema": "carry_eval.v1",
        "status": status,
        "orders": rows,
        "evaluated_count": 1 if status == "EVALUATED" else 0,
        "unknown_count": 0 if status == "EVALUATED" else 1,
        "invalid_count": 0,
        "max_carry_min": value,
        "all_le_35": (
            value <= 35.0 if status == "EVALUATED" and value is not None else None
        ),
        "all_le_40": (
            value <= 40.0 if status == "EVALUATED" and value is not None else None
        ),
    }


def _candidate(cid, value, *, verdict="MAYBE", reason="ok", plan=True, metrics=None):
    candidate_metrics = {"carry_eval": _carry(value)}
    candidate_metrics.update(metrics or {})
    route = None
    if plan:
        route = SimpleNamespace(
            sequence=["order"],
            predicted_delivered_at={},
            pickup_at={},
            sla_violations=0,
            total_duration_min=10.0,
            strategy="fixpack2",
        )
    return DP.Candidate(
        cid, cid, 1.0, verdict, reason, route, candidate_metrics
    )


def _s2_proof(*, found=False, courier_status="NO_SAFE_PLAN"):
    status = "SAFE_LE35" if found else courier_status
    return S2.probe(
        order_id="order",
        created_at=NOW - timedelta(minutes=85),
        declared_ready_at=NOW,
        now=NOW,
        fleet_ids=["c1"],
        evaluate_slot=lambda _slot, _fleet: {
            "status": "EVALUATED",
            "couriers": [{
                "courier_id": "c1",
                "status": status,
                "feasibility_verdict": "MAYBE" if found else "NO",
                "carry_status": "EVALUATED",
                "hard35_status": "LE35" if found else "OVER35",
                "all_le_35": found,
            }],
        },
    )


def test_FA_strategy2_runs_when_maybe_exists_but_zero_plans_le35():
    maybe_37 = _candidate("c37", 37.0)
    assert DP._strategy2_required([maybe_37]) is True


def test_FB_failed_current_evaluation_stays_unknown_and_blocks_alarm():
    pool = DP._alarm_counterfactual_pool(
        [_candidate(
            "c37", 37.0, verdict="NO",
            reason="R6_per_order_>35min",
            metrics={"alarm_other_hards_status": "PASSED"},
        )],
        failed_courier_ids=["c_safe"],
    )
    cert = AC.build(
        pool, decision_order_id="order", now=NOW,
        strategy2_probe=_s2_proof(),
    )
    assert cert["counterfactual"]["unknown_count"] == 1
    assert cert["classification"] == "UNEVALUABLE"
    assert cert["alarm"] is False


def test_FC_strategy2_exception_is_unevaluable_never_evaluated_false():
    result = S2.probe(
        order_id="order",
        created_at=NOW,
        declared_ready_at=NOW,
        now=NOW,
        fleet_ids=["c1"],
        evaluate_slot=lambda _slot, _fleet: {
            "status": "UNEVALUABLE",
            "couriers": [{
                "courier_id": "c1",
                "status": "UNEVALUABLE",
                "error_type": "RuntimeError",
            }],
        },
    )
    assert result["status"] == "UNEVALUABLE"
    assert result["found"] is False


def test_FD_thermal_no_with_other_hard_failure_cannot_justify_alarm():
    shift_failed = _candidate(
        "c37", 37.0, verdict="NO",
        reason="R6_per_order_>35min",
        metrics={"alarm_other_hards_status": "FAILED"},
    )
    cert = AC.build(
        [shift_failed], decision_order_id="order", now=NOW,
        strategy2_probe=_s2_proof(),
    )
    assert cert["counterfactual"]["between_35_40_count"] == 0
    assert cert["counterfactual"]["excluded_other_hard_count"] == 1
    assert cert["alarm"] is False


def test_FE_skeletal_strategy2_dict_is_not_proof_of_no_rescue_plan():
    skeletal = {
        "schema": S2.SCHEMA,
        "status": "EVALUATED",
        "order_id": "order",
        "found": False,
    }
    cert = AC.build(
        [_candidate(
            "c37", 37.0, verdict="NO",
            reason="R6_per_order_>35min",
            metrics={"alarm_other_hards_status": "PASSED"},
        )],
        decision_order_id="order",
        now=NOW,
        strategy2_probe=skeletal,
    )
    assert cert["classification"] == "UNEVALUABLE_STRATEGY2"
    assert cert["alarm"] is False


def test_FF_certificate_ttl_and_current_scope_are_recomputed():
    pool = [_candidate(
        "c37", 37.0, verdict="NO",
        reason="R6_per_order_>35min",
        metrics={"alarm_other_hards_status": "PASSED"},
    )]
    proof = _s2_proof()
    cert = AC.bind_scope(
        AC.build(
            pool, decision_order_id="order", now=NOW,
            strategy2_probe=proof,
        ),
        ["order", "bag-a"],
    )
    forged_ttl = dict(cert)
    forged_ttl["valid_until"] = (NOW + timedelta(days=365)).isoformat()
    assert not AC.validate(
        forged_ttl, NOW + timedelta(days=30),
        decision_order_id="order",
        scope_order_ids=["order", "bag-a"],
        candidates=pool,
        strategy2_probe=proof,
    )
    assert not AC.validate(
        cert, NOW,
        decision_order_id="order",
        scope_order_ids=["order", "bag-b"],
        candidates=pool,
        strategy2_probe=proof,
    )


def test_FG_forged_source_without_gate_and_contract_stays_proxy():
    plan = SimpleNamespace(
        predicted_delivered_at={"food": NOW + timedelta(minutes=30)},
        pickup_at={},
    )
    forged = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW,
        physical_possession_source="gps_bag_sensor",
        event_gate_status=None,
        contract_version=None,
        picked_up_at=None,
    )
    legit = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW,
        physical_possession_source="gps_bag_sensor",
        event_gate_status="BOUND",
        contract_version="physical_possession.v1",
        picked_up_at=None,
    )
    assert CF.evaluate_plan(plan, [forged])["orders"][0]["source"] == "proxy"
    assert CF.evaluate_plan(plan, [legit])["orders"][0]["source"] == "bound"


def test_FH_negative_carry_is_invalid_and_never_evaluated_safe():
    plan = SimpleNamespace(
        predicted_delivered_at={"food": NOW},
        pickup_at={},
    )
    order = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW + timedelta(minutes=5),
        physical_possession_source="gps_bag_sensor",
        event_gate_status="BOUND",
        contract_version="physical_possession.v1",
        picked_up_at=None,
    )
    result = CF.evaluate_plan(plan, [order])
    assert result["status"] != "EVALUATED"
    assert result["all_le_35"] is not True
    assert result["orders"][0]["carry_min"] is None
    assert result["orders"][0]["reason"] == "negative_carry"


def _picked_state():
    return {
        "food": {
            "status": "picked_up",
            "physical_possession_at": NOW.isoformat(),
            "physical_possession_source": "gps_bag_sensor",
            "event_gate_status": "BOUND",
            "contract_version": "physical_possession.v1",
        }
    }


def test_FI_plan_recheck_uses_physical_possession_and_unknown_is_not_ok(monkeypatch):
    monkeypatch.setattr(PR._alarm_cert, "read", lambda *a, **k: None)
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: True if name == "ENABLE_CARRY_CANON_V2" else original(name),
    )
    stops = [{
        "type": "dropoff",
        "order_id": "food",
        "predicted_at": (NOW + timedelta(minutes=60)).isoformat(),
        "dwell_min": 0.0,
    }]
    result = PR._g4_final_validator(stops, [], _picked_state(), NOW)
    assert result["ok"] is False
    assert result["reason"] == "freshness_envelope"


def test_FJ_plan_recheck_validator_exception_is_fail_closed(monkeypatch):
    monkeypatch.setattr(PR._alarm_cert, "read", lambda *a, **k: None)
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: True if name == "ENABLE_CARRY_CANON_V2" else original(name),
    )
    monkeypatch.setattr(
        PR, "_g4_carry_map",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("oracle")),
    )
    stops = [{
        "type": "dropoff",
        "order_id": "food",
        "predicted_at": (NOW + timedelta(minutes=60)).isoformat(),
    }]
    result = PR._g4_final_validator(stops, stops, _picked_state(), NOW)
    assert result == {"ok": False, "reason": "validator_error"}


def test_FK_capz_uses_canonical_possession_not_legacy_proxy(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_PACZKA_R6_THERMAL_EXEMPT", False)
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: True if name == "ENABLE_CARRY_CANON_V2" else original(name),
    )
    order = SimpleNamespace(
        order_id="food",
        status="picked_up",
        picked_up_at=NOW + timedelta(minutes=5),
        physical_possession_at=NOW,
        physical_possession_source="gps_bag_sensor",
        event_gate_status="BOUND",
        contract_version="physical_possession.v1",
        address_id=None,
        order_type=None,
    )
    new_order = SimpleNamespace(
        order_id="new", status="assigned", picked_up_at=None,
        physical_possession_at=None, physical_possession_source=None,
        event_gate_status=None, contract_version=None,
        address_id=None, order_type=None,
    )
    plan = SimpleNamespace(
        predicted_delivered_at={"food": NOW + timedelta(minutes=38)},
        pickup_at={},
        per_order_delivery_times={"food": 33.0},
    )
    _overage, max_carried = RS._capz_bag_metrics(
        plan, [order], new_order, 35.0
    )
    assert max_carried == 38.0


def test_FL_hard35_plan_none_is_never_propose(monkeypatch):
    candidate = _candidate("unknown", None, plan=False)
    monkeypatch.setattr(
        C, "decision_flag",
        lambda name: name in {
            "ENABLE_CARRY_CANON_V2",
            "ENABLE_HARD35_ENFORCE",
        },
    )
    monkeypatch.setattr(DP, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = selection.SelectionContext(
        now=NOW,
        order_event={"order_id": "order"},
        order_id="order",
        restaurant="R",
        delivery_address="D",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
        new_order=SimpleNamespace(order_id="order"),
        fleet_snapshot={},
        v328_fail_causes={},
        shadow_only=True,
    )
    result = selection.select_and_emit(ctx, [candidate])
    assert result.verdict != "PROPOSE"
    assert result.reason.startswith("hard35_least_damage_alert")


def test_FM_least_damage_koord_is_enqueued_for_owner():
    from dispatch_v2 import telegram_approver as TA

    state = {"incoming": asyncio.Queue()}
    record = {
        "order_id": "order",
        "verdict": "KOORD",
        "reason": "hard35_least_damage_alert (cap=35)",
    }
    assert asyncio.run(TA._enqueue_owner_record(state, record)) is True
    assert asyncio.run(state["incoming"].get()) == record
    payload, actionable = TA._owner_message_payload(
        {"admin_id": 1}, record
    )
    assert actionable is False
    assert "reply_markup" not in payload


def test_FN_world_record_input_has_no_possession_keys_when_flag_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_CARRY_CANON_V2", False)
    event = {
        "event_id": "e",
        "event_type": "NEW_ORDER",
        "order_id": "order",
        "payload": {},
    }
    order_event = SD._build_order_event(event)
    assert "physical_possession_at" not in order_event
    assert "physical_possession_source" not in order_event
    assert "event_gate_status" not in order_event
    assert "contract_version" not in order_event
