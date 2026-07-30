"""FIX-PACK 3: negatywne oracle dla 14 findingów blind review r3.

Każdy test odtwarza konkretny kontrprzykład z finalnego werdyktu
``codex_blind-escalation-r3_out.txt``. Zmiana pozostaje source-only i za
czterema flagami domyślnie OFF.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import feasibility_v2 as FV
from dispatch_v2 import osrm_client
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import route_simulator_v2 as RS
from dispatch_v2 import telegram_approver as TA
from dispatch_v2.core import alarm_certificate as AC
from dispatch_v2.core import carry_freshness as CF
from dispatch_v2.core import lex_window_guards as LG
from dispatch_v2.core import selection as SEL
from dispatch_v2.core import strategy2_probe as S2


NOW = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


def _plan(*, drop_min: float = 30.0):
    return SimpleNamespace(
        sequence=["food"],
        predicted_delivered_at={
            "food": NOW + timedelta(minutes=drop_min),
        },
        pickup_at={},
        per_order_delivery_times={"food": drop_min},
        sla_violations=0,
        total_duration_min=drop_min,
        strategy="fixpack3",
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
            "source": "bound",
            "possession_source": "gps_geofence",
            "handoff_source": "predicted_delivery_with_dropoff_dwell",
        }],
        "evaluated_count": 1,
        "unknown_count": 0,
        "invalid_count": 0,
        "max_carry_min": value,
        "all_le_35": value <= 35.0,
        "all_le_40": value <= 40.0,
    }


def _candidate(
    cid: str,
    value: float,
    *,
    verdict: str = "NO",
    reason: str = "R6_per_order_>35min",
    plan=True,
):
    candidate = DP.Candidate(
        cid,
        cid,
        1.0,
        verdict,
        reason,
        _plan(drop_min=value) if plan else None,
        {
            "carry_eval": _carry(value),
            "alarm_other_hards_status": "PASSED",
        },
    )
    return candidate


def _complete_s2_no_rescue(*, now: datetime = NOW) -> dict:
    return S2.probe(
        order_id="order",
        created_at=now - timedelta(minutes=85),
        declared_ready_at=now,
        now=now,
        fleet_ids=["c37"],
        evaluate_slot=lambda _slot, _fleet: {
            "status": "EVALUATED",
            "couriers": [{
                "courier_id": "c37",
                "status": "NO_SAFE_PLAN",
                "feasibility_verdict": "NO",
                "carry_status": "EVALUATED",
                "hard35_status": "OVER35",
                "all_le_35": False,
            }],
        },
    )


def test_r3_01_s2_ignores_no_candidate_with_safe_looking_plan():
    no_but_measured_safe = _candidate(
        "no",
        30.0,
        verdict="NO",
        reason="R_SCHEDULE",
    )
    assert DP._strategy2_required([no_but_measured_safe]) is True


def test_r3_02_legal_none_result_stays_unknown_in_full_pool():
    pool = DP._alarm_counterfactual_pool(
        [_candidate("c37", 37.0)],
        failed_courier_ids=[],
        fleet_courier_ids=["c37", "legal-none"],
    )
    cert = AC.build(
        pool,
        decision_order_id="order",
        now=NOW,
        strategy2_probe=_complete_s2_no_rescue(),
    )
    assert cert["counterfactual"]["unknown_count"] == 1
    assert cert["alarm"] is False


def test_r3_03_not_applicable_carry_is_not_complete_no_safe_proof():
    assert S2._complete_courier_result({
        "courier_id": "c1",
        "status": "NO_SAFE_PLAN",
        "feasibility_verdict": "MAYBE",
        "carry_status": "NOT_APPLICABLE",
        "hard35_status": "UNKNOWN",
        "all_le_35": None,
    }) is False


def test_r3_04_one_slot_does_not_certify_ninety_minute_horizon():
    forged = {
        "schema": S2.SCHEMA,
        "status": "EVALUATED",
        "order_id": "order",
        "found": False,
        "slot_at": None,
        "shift_min": None,
        "courier_id": None,
        "feasible_courier_ids": [],
        "fleet_courier_ids": ["c37"],
        "fleet_count": 1,
        "slots_checked": 1,
        "deadline_at": (NOW + timedelta(minutes=90)).isoformat(),
        "evaluations": [{
            "slot_at": (NOW + timedelta(minutes=5)).isoformat(),
            "couriers": [{
                "courier_id": "c37",
                "status": "NO_SAFE_PLAN",
                "feasibility_verdict": "NO",
                "carry_status": "EVALUATED",
                "hard35_status": "OVER35",
                "all_le_35": False,
            }],
        }],
    }
    cert = AC.build(
        [_candidate("c37", 37.0)],
        decision_order_id="order",
        now=NOW,
        strategy2_probe=forged,
    )
    assert cert["strategy2_found"] is None
    assert cert["alarm"] is False


def test_r3_05_unknown_status_cannot_reuse_stale_safe_carry():
    unknown = _candidate("unknown", 37.0)
    unknown.alarm_evaluation_status = "UNKNOWN"
    cert = AC.build(
        [unknown],
        decision_order_id="order",
        now=NOW,
        strategy2_probe=_complete_s2_no_rescue(),
    )
    assert cert["counterfactual"]["unknown_count"] == 1
    assert cert["alarm"] is False


def test_r3_06_d3_gps_bound_and_click_is_named_fallback():
    plan = _plan(drop_min=40.0)
    gps = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW,
        physical_possession_source="gps_geofence",
        event_gate_status="BOUND",
        contract_version="physical_possession.v1",
        picked_up_at=NOW + timedelta(minutes=10),
    )
    gps_row = CF.evaluate_plan(plan, [gps])["orders"][0]
    assert gps_row["source"] == "bound"
    assert gps_row["possession_source"] == "gps_geofence"

    untrusted = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW + timedelta(minutes=10),
        physical_possession_source="panel_guess",
        event_gate_status="UNBOUND",
        contract_version="physical_possession.v1",
        picked_up_at=NOW,
    )
    fallback_row = CF.evaluate_plan(plan, [untrusted])["orders"][0]
    assert fallback_row["source"] == "proxy_fallback"
    assert fallback_row["possession_source"] == "picked_up_at_click"
    assert fallback_row["carry_min"] == 40.0


def test_r3_07_declared_max_must_match_rows_and_plan():
    forged = _candidate("forged", 30.0)
    forged.metrics["carry_eval"]["orders"][0]["carry_min"] = 50.0
    forged.metrics["carry_eval"]["orders"][0]["le_35"] = False
    forged.metrics["carry_eval"]["orders"][0]["le_40"] = False
    allowed, _least, meta = AC.hard35_best_effort_choice(
        [forged],
        alarm_certificate=None,
    )
    assert allowed == []
    assert meta["unknown_count"] == 1

    planless = _candidate("planless", 30.0, verdict="MAYBE", plan=False)
    allowed, _least, meta = AC.hard35_best_effort_choice(
        [planless],
        alarm_certificate=None,
    )
    assert allowed == []
    assert meta["unknown_count"] == 1


def test_r3_08_g4_keeps_existing_overcap_bag_writable_when_not_worse(
    monkeypatch,
):
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            True
            if name in {
                "ENABLE_CARRY_CANON_V2",
                "ENABLE_HARD35_ENFORCE",
            }
            else original(name)
        ),
    )
    monkeypatch.setattr(PR._alarm_cert, "read", lambda *a, **k: None)
    envelope = [{"type": "dropoff", "order_id": "food",
                 "predicted_at": (NOW + timedelta(minutes=60)).isoformat()}]
    final = [{"type": "dropoff", "order_id": "food",
              "predicted_at": (NOW + timedelta(minutes=50)).isoformat()}]
    values = iter((
        {"food": 60.0},
        {"food": 50.0},
        {"food": 60.0},
        {"food": 65.0},
    ))
    monkeypatch.setattr(PR, "_g4_carry_map", lambda *a, **k: next(values))
    improving = PR._g4_final_validator(
        final,
        envelope,
        {"food": {"status": "picked_up"}},
        NOW,
    )
    worsening = PR._g4_final_validator(
        final,
        envelope,
        {"food": {"status": "picked_up"}},
        NOW,
    )
    assert improving["ok"] is True
    assert worsening["ok"] is False
    assert worsening["reason"] == "freshness_envelope"


def test_r3_09_lex_guard_existing_overcap_requires_not_worse():
    baseline = LG.Facts(
        window_viol=1,
        drive_min=20.0,
        carry_by_order={"food": 60.0},
    )
    candidate = LG.Facts(
        window_viol=0,
        drive_min=19.0,
        carry_by_order={"food": 50.0},
    )
    result = LG.evaluate(
        baseline,
        candidate,
        assigned_ids=[],
        carried_ids=["food"],
        thresholds=LG.Thresholds(
            delay_tol_min=3.0,
            carry_cap_min=35.0,
            min_gain_min=1.0,
        ),
    )
    assert result.admissible is True

    # Mutation probe: pogorszenie fizycznego stanu 60 -> 65 nadal jest HARD.
    mutated = LG.evaluate(
        baseline,
        LG.Facts(
            window_viol=0,
            drive_min=19.0,
            carry_by_order={"food": 65.0},
        ),
        assigned_ids=[],
        carried_ids=["food"],
        thresholds=LG.Thresholds(
            delay_tol_min=3.0,
            carry_cap_min=35.0,
            min_gain_min=1.0,
        ),
    )
    assert mutated.admissible is False
    assert mutated.reason == "g2_cap"


def test_r3_10_lex_real_path_calls_canonical_carry(monkeypatch):
    fixture = (
        Path(__file__).with_name("fixtures")
        / "wb2_incident_492_20260727T160912Z.json"
    )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    matrix = data["leg_matrix_min"]
    monkeypatch.setattr(
        osrm_client,
        "table",
        lambda a, b: [
            [{"duration_s": matrix[i][j] * 60.0} for j in range(len(b))]
            for i in range(len(a))
        ],
    )
    monkeypatch.setattr(PR, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(PR, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", True)
    monkeypatch.setattr(PR, "ENABLE_LEX_WINDOW_GUARDS_V2", True)
    original_flag = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: True if name == "ENABLE_CARRY_CANON_V2" else original_flag(name),
    )
    original_eval = CF.evaluate_plan
    calls = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original_eval(*args, **kwargs)

    monkeypatch.setattr(CF, "evaluate_plan", counted)
    PR._lex_committed_window_reorder(
        [dict(stop) for stop in data["stops"]],
        data["orders_state"],
        tuple(data["start_pos"]),
        datetime.fromisoformat(data["now"]),
    )
    assert calls, "realna ścieżka lex ominęła kanoniczny carry evaluator"


def test_r3_11_g4_flag_off_never_calls_canonical_carry(monkeypatch):
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: False if name == "ENABLE_CARRY_CANON_V2" else original(name),
    )
    monkeypatch.setattr(
        CF,
        "evaluate_plan",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("OFF leak")),
    )
    stops = [{
        "type": "dropoff",
        "order_id": "food",
        "predicted_at": (NOW + timedelta(minutes=30)).isoformat(),
        "dwell_min": 0.0,
    }]
    got = PR._g4_carry_map(
        stops,
        {"food": {
            "status": "picked_up",
            "picked_up_at": NOW.isoformat(),
        }},
        NOW,
    )
    # OFF-parity: legacy resolver traktował falsy dwell=0 jak brak wartości
    # i podstawiał 3.5. Usunięcie `or 3.5` mutacyjnie przywraca 30.0 i czerwieni.
    assert got == {"food": 33.5}


def test_r3_12_capz_flag_off_never_calls_canonical_carry(monkeypatch):
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: False if name == "ENABLE_CARRY_CANON_V2" else original(name),
    )
    monkeypatch.setattr(
        CF,
        "evaluate_plan",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("OFF leak")),
    )
    order = SimpleNamespace(
        order_id="food",
        status="picked_up",
        picked_up_at=NOW,
        physical_possession_at=NOW,
        address_id=None,
        order_type=None,
    )
    new = SimpleNamespace(
        order_id="new",
        status="assigned",
        picked_up_at=None,
        physical_possession_at=None,
        address_id=None,
        order_type=None,
    )
    plan = _plan(drop_min=50.0)
    assert RS._capz_bag_metrics(plan, [order], new, 35.0) == (15.0, 50.0)


def test_r3_13_shadow_certificate_alone_cannot_raise_cap(monkeypatch):
    cert_now = datetime.now(timezone.utc)
    candidate = _candidate("c37", 37.0)
    proof = _complete_s2_no_rescue(now=cert_now)
    cert = AC.build(
        [candidate],
        decision_order_id="order",
        now=cert_now,
        strategy2_probe=proof,
    )
    original = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            True
            if name == "ENABLE_ALARM_CERTIFICATE_SHADOW"
            else False
            if name == "ENABLE_HARD35_ENFORCE"
            else original(name)
        ),
    )
    thresholds = LG.load_thresholds(
        alarm_certificate=cert,
        alarm_candidates=[candidate],
        strategy2_probe=proof,
    )
    assert thresholds.carry_cap_min == 35.0
    assert thresholds.alarm is False


def test_r3_14_firmowe_filter_never_suppresses_hard35_koord(monkeypatch):
    record = {
        "order_id": "food",
        "address_id": 161,
        "verdict": "KOORD",
        "reason": "hard35_least_damage_alert (cap=35)",
    }

    class OneRecord:
        async def get(self):
            TA._shutdown = True
            return record

    sent = []

    def fake_tg(_token, method, payload):
        sent.append((method, payload))
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(TA, "_shutdown", False)
    monkeypatch.setattr(TA, "tg_request", fake_tg)
    monkeypatch.setattr(
        TA,
        "flag",
        lambda name, default=False: False,
    )
    state = {
        "incoming": OneRecord(),
        "pending": {},
        "admin_id": 1,
        "token": "redacted-test-token",
    }
    asyncio.run(TA.proposal_sender(state))
    assert len(sent) == 1
    assert "reply_markup" not in sent[0][1]


def test_r4_01_hard35_repick_preserves_best_effort_decision_class(monkeypatch):
    """Repick innego kuriera nie może zmienić decyzji 0-feasible w zwykły PROPOSE."""
    breach = _candidate("breach", 37.0)
    safe = _candidate("safe", 30.0, reason="sla_violation")
    breach.score = 100.0
    safe.score = 1.0
    breach.metrics["r6_per_order_violations"] = ["old-a", "old-b"]
    breach.plan.sla_violations = 2
    safe.metrics["r6_per_order_violations"] = []
    safe.plan.sla_violations = 0
    original_decision_flag = C.decision_flag
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            True
            if name in {
                "ENABLE_CARRY_CANON_V2",
                "ENABLE_HARD35_ENFORCE",
            }
            else False
            if name in {
                "ENABLE_ALARM_CERTIFICATE_SHADOW",
                "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
            }
            else original_decision_flag(name)
        ),
    )
    monkeypatch.setattr(DP, "_always_propose_on", lambda: True)
    monkeypatch.setattr(DP, "_best_effort_sort_key", lambda c: -c.score)
    ctx = SEL.SelectionContext(
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

    result = SEL.select_and_emit(ctx, [breach, safe])

    assert result.verdict == "PROPOSE"
    assert result.pool_feasible_count == 0
    assert result.best is safe
    assert result.reason == (
        "best_effort (0 feasible, r6_violations=0, legacy_sla_v=0)"
    )
    # Mutation oracle: usunięcie transferu w `_hard35_proposal_boundary`
    # zostawia tu False i czerwieni zarówno serializer, jak i classifier.
    assert result.best.best_effort is True


def test_r4_02_best_effort_repick_still_forces_alert(monkeypatch):
    """Classifier konsumuje marker przeniesiony przez granicę HARD35."""
    best = _candidate("safe", 30.0, reason="sla_violation")
    best.best_effort = True
    result = DP.PipelineResult(
        order_id="order",
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
    # Kanoniczny owner routingu to auto_proximity_classifier. Włączamy jego
    # tryb shadow zamiast polegać na duplikującym, bezflagowym postcondition.
    monkeypatch.setattr(
        C,
        "load_flags",
        lambda: {"AUTO_PROXIMITY_SHADOW_ONLY": True},
    )

    DP._classify_and_set_auto_route(result, {}, {"order_id": "order"}, now=NOW)

    assert result.auto_route == "ALERT"
    assert result.auto_route_reason.startswith("best_effort_no_feasible")


def test_r4_03_hard35_koord_owner_payload_is_explicit_alert(monkeypatch):
    """KOORD least-damage ma jawny alarm i nigdy nie dostaje klawiatury propozycji."""
    record = {
        "order_id": "food",
        "verdict": "KOORD",
        "reason": "hard35_least_damage_alert (cap=35; 0 within cap)",
        "auto_route": "ALERT",
        "hard35_enforcement": {"cap_min": 35.0},
        "restaurant": "R",
        "delivery_address": "D",
        "best": {
            "courier_id": "c37",
            "name": "C37",
            "best_effort": True,
            "metrics": {},
            "plan": {},
        },
        "alternatives": [],
    }
    monkeypatch.setattr(
        TA,
        "flag",
        lambda name, default=False: name == "PROPOSAL_FORMAT_V2",
    )

    payload, actionable = TA._owner_message_payload({"admin_id": 1}, record)

    assert actionable is False
    assert "reply_markup" not in payload
    assert payload["text"].startswith(
        "🚨 ALERT — brak planu mieszczącego się w limicie 35 min."
    )
    assert "wariant najmniej szkodliwy" in payload["text"]
    assert "🔴 ALERT — wymaga Twojej decyzji." in payload["text"]


def test_r4_04_hard35_koord_classifier_cannot_downgrade_to_ack(monkeypatch):
    """Precondition PROPOSE klasyfikatora nie może zamienić KOORD alarmu w ACK."""
    from dispatch_v2 import auto_proximity_classifier as APC

    best = _candidate("c37", 37.0)
    best.best_effort = True
    result = DP.PipelineResult(
        order_id="food",
        verdict="KOORD",
        reason="hard35_least_damage_alert (cap=35; 0 within cap)",
        best=best,
        candidates=[best],
        pickup_ready_at=NOW,
        restaurant="R",
        delivery_address="D",
        pool_total_count=2,
        pool_feasible_count=0,
    )
    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(
        APC,
        "classify_auto_route",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("mutant")),
    )

    DP._classify_and_set_auto_route(result, {}, {"order_id": "food"}, now=NOW)

    assert result.auto_route == "ALERT"
    assert result.auto_route_reason == "hard35_no_candidate_within_cap"


def test_r5_01_carry_writer_round_trips_raw_value_into_alarm_consumer():
    """Writer i konsument carry_eval.v1 muszą uzgadniać dokładnie te same minuty."""
    raw_minutes = 1210.0 / 60.0
    plan = _plan(drop_min=raw_minutes)
    order = SimpleNamespace(
        order_id="food",
        physical_possession_at=NOW,
        physical_possession_source="gps_bag_sensor",
        event_gate_status="BOUND",
        contract_version="physical_possession.v1",
        picked_up_at=None,
    )

    carry = CF.evaluate_plan(plan, [order])
    candidate = DP.Candidate(
        "raw",
        "raw",
        1.0,
        "MAYBE",
        "ok",
        plan,
        {"carry_eval": carry},
    )

    # Mutation oracle: round(raw, 2) u writera ponownie rozjeżdża wiersz z max.
    assert carry["orders"][0]["carry_min"] == raw_minutes
    assert carry["max_carry_min"] == raw_minutes
    assert AC._candidate_carry(candidate) == raw_minutes


def _r5_sla_candidate(monkeypatch, *, fail_later_hard: bool):
    """Realny writer feasibility: SLA i canonical carry ready→drop = 40."""
    order = RS.OrderSim(
        order_id="food",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
    )
    plan = RS.RoutePlanV2(
        sequence=["food"],
        predicted_delivered_at={"food": NOW + timedelta(minutes=40)},
        pickup_at={"food": NOW + timedelta(minutes=10)},
        total_duration_min=40.0,
        strategy="r5-opus-oracle",
        sla_violations=1,
        osrm_fallback_used=False,
        per_order_delivery_times={"food": 40.0},
    )
    monkeypatch.setattr(FV, "simulate_bag_route_v2", lambda *_a, **_k: plan)
    monkeypatch.setattr(FV, "USE_PER_ORDER_GATE", False)
    monkeypatch.setattr(FV, "ENABLE_C2_SHADOW_LOG", False)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False)
    monkeypatch.setattr(
        C,
        "ENABLE_V324A_SCHEDULE_INTEGRATION",
        fail_later_hard,
    )
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name == "ENABLE_CARRY_CANON_V2",
    )
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: (
            name in {
                "ENABLE_SLA_ANCHOR_UNIFIED",
                "ENABLE_SLA_GATE_READY_ANCHOR",
            }
        ),
    )

    shift_end = NOW + timedelta(minutes=20) if fail_later_hard else None
    return FV.check_feasibility_v2(
        (53.13, 23.16),
        [],
        order,
        shift_end=shift_end,
        now=NOW,
        pickup_ready_at=NOW,
        shadow_probe=True,
    )


def test_r5_02_sla_thermal_reject_marks_other_hards_only_after_full_pass(
    monkeypatch,
):
    """SLA thermal reject trafia do Alarmu dopiero po przejściu reszty HARD."""
    verdict, reason, metrics, plan = _r5_sla_candidate(
        monkeypatch,
        fail_later_hard=False,
    )
    candidate = DP.Candidate(
        "sla",
        "sla",
        1.0,
        verdict,
        reason,
        plan,
        metrics,
    )

    assert verdict == "NO"
    assert reason.startswith("sla_violation")
    assert metrics["carry_eval"]["max_carry_min"] == 40.0
    assert (
        metrics["carry_eval"]["orders"][0]["possession_source"]
        == "pickup_ready_at"
    )
    # Mutation oracle: bez odroczenia early return nie zapisuje markera.
    assert metrics["alarm_other_hards_status"] == "PASSED"
    assert AC._passes_other_hards(candidate) is True
    assert AC._candidate_carry(candidate) == 40.0
    counterfactual, _fingerprint = AC._pool_counterfactual([candidate])
    assert counterfactual["le_35_count"] == 0
    assert counterfactual["between_35_40_count"] == 1
    assert counterfactual["excluded_other_hard_count"] == 0


def test_r5_03_later_hard_failure_never_gets_false_alarm_pass_marker(
    monkeypatch,
):
    """Wczesne ustawienie PASSED byłoby fałszywym dowodem i odblokowałoby Alarm."""
    verdict, reason, metrics, plan = _r5_sla_candidate(
        monkeypatch,
        fail_later_hard=True,
    )
    candidate = DP.Candidate(
        "shift-fail",
        "shift-fail",
        1.0,
        verdict,
        reason,
        plan,
        metrics,
    )

    assert verdict == "NO"
    assert reason.startswith("v324a_dropoff_after_shift")
    assert "alarm_other_hards_status" not in metrics
    assert AC._passes_other_hards(candidate) is False
    counterfactual, _fingerprint = AC._pool_counterfactual([candidate])
    assert counterfactual["le_35_count"] == 0
    assert counterfactual["excluded_other_hard_count"] == 1


def _r8_parcel_candidate(
    monkeypatch,
    *,
    thermal_exempt: bool,
    flex: bool,
    with_food: bool = False,
):
    """Realny writer feasibility dla worka złożonego wyłącznie z paczki."""
    order = RS.OrderSim(
        order_id="parcel",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
    )
    order.address_id = 232
    order.order_type = "elastic"
    order.physical_possession_at = NOW
    order.physical_possession_source = "gps_bag_sensor"
    order.event_gate_status = "BOUND"
    order.contract_version = "physical_possession.v1"
    bag = []
    predicted = {"parcel": NOW + timedelta(minutes=90)}
    if with_food:
        food = RS.OrderSim(
            order_id="food",
            pickup_coords=(53.12, 23.15),
            delivery_coords=(53.15, 23.18),
            pickup_ready_at=NOW,
        )
        food.status = "picked_up"
        food.address_id = 190
        food.order_type = "food"
        food.physical_possession_at = NOW
        food.physical_possession_source = "gps_bag_sensor"
        food.event_gate_status = "BOUND"
        food.contract_version = "physical_possession.v1"
        bag = [food]
        predicted["food"] = NOW + timedelta(minutes=37)
    plan = RS.RoutePlanV2(
        sequence=list(predicted),
        predicted_delivered_at=predicted,
        pickup_at={"parcel": NOW},
        total_duration_min=90.0,
        strategy="r8-parcel-oracle",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={
            oid: (when - NOW).total_seconds() / 60.0
            for oid, when in predicted.items()
        },
    )
    monkeypatch.setattr(FV, "simulate_bag_route_v2", lambda *_a, **_k: plan)
    monkeypatch.setattr(FV, "USE_PER_ORDER_GATE", False)
    monkeypatch.setattr(FV, "ENABLE_C2_SHADOW_LOG", False)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False)
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", flex)
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name == "ENABLE_CARRY_CANON_V2",
    )
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: (
            thermal_exempt
            if name == "ENABLE_PACZKA_R6_THERMAL_EXEMPT"
            else flex
            if name == "ENABLE_R_PACZKI_FLEX"
            else False
        ),
    )
    verdict, reason, metrics, evaluated_plan = FV.check_feasibility_v2(
        (53.13, 23.16),
        bag,
        order,
        now=NOW,
        pickup_ready_at=NOW,
        shadow_probe=True,
    )
    return DP.Candidate(
        "parcel-courier",
        "parcel-courier",
        1.0,
        verdict,
        reason,
        evaluated_plan,
        metrics,
    )


def test_r7_01_parcel_thermal_exemption_survives_hard35_boundary(monkeypatch):
    candidate = _r8_parcel_candidate(
        monkeypatch,
        thermal_exempt=True,
        flex=False,
    )
    scope = candidate.metrics["carry_eval"]["thermal_scope"]

    assert scope == {
        "schema": "carry_thermal_scope.v1",
        "status": "EXEMPT",
        "reason": "paczka_r6_thermal_exempt",
        "order_count": 1,
    }
    assert candidate.metrics["carry_eval"]["orders"] == []
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [candidate],
        alarm_certificate=None,
    )
    assert allowed == [candidate]
    assert alert is None
    assert meta["thermal_exempt_count"] == 1
    counterfactual, _fingerprint = AC._pool_counterfactual([candidate])
    assert counterfactual["le_35_count"] == 1
    assert counterfactual["unknown_count"] == 0


def test_r7_02_parcel_flex_over_35_survives_hard35_boundary(monkeypatch):
    candidate = _r8_parcel_candidate(
        monkeypatch,
        thermal_exempt=False,
        flex=True,
    )
    scope = candidate.metrics["carry_eval"]["thermal_scope"]

    assert scope["status"] == "EXEMPT"
    assert scope["reason"] == "paczki_only_flex"
    assert candidate.metrics["carry_eval"]["max_carry_min"] > 35.0
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [candidate],
        alarm_certificate=None,
    )
    assert allowed == [candidate]
    assert alert is None
    assert meta["thermal_exempt_count"] == 1


def test_r7_03_mixed_food_scope_remains_hard35_applicable(monkeypatch):
    candidate = _r8_parcel_candidate(
        monkeypatch,
        thermal_exempt=True,
        flex=True,
        with_food=True,
    )
    scope = candidate.metrics["carry_eval"]["thermal_scope"]

    assert scope["status"] == "APPLICABLE"
    assert scope["reason"] is None
    assert [
        row["order_id"] for row in candidate.metrics["carry_eval"]["orders"]
    ] == ["food"]
    assert candidate.metrics["carry_eval"]["max_carry_min"] > 35.0
    allowed, alert, meta = AC.hard35_best_effort_choice(
        [candidate],
        alarm_certificate=None,
    )
    assert allowed == []
    assert alert is candidate
    assert meta["thermal_exempt_count"] == 0


def test_r7_04_g4_legacy_none_preserves_off_parity(monkeypatch):
    monkeypatch.setattr(PR._alarm_cert, "read", lambda *a, **k: None)
    monkeypatch.setattr(C, "decision_flag", lambda _name: False)
    stops = [{
        "type": "dropoff",
        "order_id": "food",
        "predicted_at": (NOW + timedelta(minutes=10)).isoformat(),
        "dwell_min": 3.5,
    }]
    result = PR._g4_final_validator(
        stops,
        stops,
        {"food": {"status": "picked_up"}},
        NOW,
    )

    assert result == {"ok": True, "reason": None}


def test_r7_05_g4_validator_exception_preserves_off_parity(monkeypatch):
    monkeypatch.setattr(PR._alarm_cert, "read", lambda *a, **k: None)
    monkeypatch.setattr(C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        PR,
        "_g4_carry_map",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("oracle")),
    )
    stops = [{
        "type": "dropoff",
        "order_id": "food",
        "predicted_at": (NOW + timedelta(minutes=10)).isoformat(),
    }]
    result = PR._g4_final_validator(
        stops,
        stops,
        {"food": {"status": "picked_up"}},
        NOW,
    )

    assert result == {"ok": True, "reason": "validator_error"}


def test_r7_06_malformed_thermal_scope_cannot_bypass_hard35():
    candidate = _candidate(
        "forged-exempt",
        90.0,
        verdict="MAYBE",
        reason="ok",
    )
    candidate.metrics["carry_eval"]["thermal_scope"] = {
        "schema": "carry_thermal_scope.v1",
        "status": "EXEMPT",
        "reason": "food_is_not_exempt",
        "order_count": 1,
    }

    allowed, alert, meta = AC.hard35_best_effort_choice(
        [candidate],
        alarm_certificate=None,
    )
    assert allowed == []
    assert alert is candidate
    assert meta["unknown_count"] == 1
