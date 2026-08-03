"""A-3 D-A3-1/2/3 (owner 2026-08-03) — negatywny oracle i ratchets.

Całość jest hermetyczna: pliki context/learning są pod ``tmp_path``. Testy
udowadniają, że A-3 pozostaje shadow/log-only: nawet przy fladze ON legacy
``PipelineResult.verdict`` oraz ``best`` nie zmieniają się.
"""
from __future__ import annotations

import json
import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import event_bus as EB
from dispatch_v2 import lifecycle_downstream as LD
from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import always_propose_learning as APL
from dispatch_v2.core import proposal_output as PO
from dispatch_v2.core import selection as SEL


NOW = datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc)


def _fleet() -> dict:
    return {
        "20": SimpleNamespace(
            pos=(53.20, 23.20), name="K20", shift_end=None,
            shift_start=None, available_from=None, tier_bag=None,
            schedule_source_stale=False, pos_from_store=False,
            pos_source="gps",
        ),
        "10": SimpleNamespace(
            pos=(53.10, 23.10), name="K10", shift_end=None,
            shift_start=None, available_from=None, tier_bag=None,
            schedule_source_stale=False, pos_from_store=False,
            pos_source="gps",
        ),
    }


def _ctx(fleet: dict) -> SEL.SelectionContext:
    return SEL.SelectionContext(
        now=NOW,
        order_event={"order_id": "A3-O1"},
        order_id="A3-O1",
        restaurant="R",
        delivery_address="D",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=NOW,
        new_order=SimpleNamespace(order_id="A3-O1"),
        fleet_snapshot=fleet,
        v328_fail_causes={},
        shadow_only=True,
    )


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _escalation_context() -> dict:
    return {
        "schema": "always_propose.decision_context.v1",
        "order_id": "A3-O1",
        "decision_event_id": "A3-O1_NEW_ORDER_1",
        "decision_at": "2026-08-03T12:29:00+00:00",
        "proposal_output_type": PO.COORDINATOR_ESCALATION,
        "verdict": "KOORD",
        "reason": "no_solo_candidates (fleet_n=2)",
        "best_courier_id": None,
        "proposal_best_of_worst": {
            "schema": "always_propose.best_of_worst.v1",
            "proposal_output_type": PO.COORDINATOR_ESCALATION,
            "recommend_only": True,
            "candidate": {
                "courier_id": "10",
                "hard_safe": False,
                "feasibility_verdict": "NO",
            },
        },
    }


def _receipt_context(*, pending_record=None, decision_context=None) -> dict:
    return {
        "schema_version": 4,
        "captured_at": "2026-08-03T12:30:00+00:00",
        "capture_status": "always_propose_context",
        "panel_agree_max_age_min": 15.0,
        "decision_join_enabled": True,
        "always_propose_enabled": True,
        "pending_record": pending_record,
        "assign_direct": None,
        "always_propose_decision_context": decision_context,
        "assigned_by": {
            "status": "ATTESTED",
            "actor_id": "actor_sha256:fixture",
            "provenance": "coordinator_assign_audit",
        },
    }


def test_red_no_solo_on_emits_deterministic_candidate_without_changing_decision(
    monkeypatch,
):
    """Negatywny oracle: przed D2 ``no_solo`` nie wskazywało żadnego CID."""
    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)

    def hard_no(**kwargs):
        distance = 1.0 if kwargs.get("courier_pos") == (53.10, 23.10) else 4.0
        return "NO", "NO_ACTIVE_SHIFT", {"pickup_dist_km": distance}, None

    monkeypatch.setattr(DP, "check_feasibility_v2", hard_no)
    result = SEL.select_and_emit(_ctx(_fleet()), [])

    # HARD/legacy są nietknięte: żaden kandydat nie staje się wykonywalnym best.
    assert result.verdict == "KOORD"
    assert result.best is None
    observed = result.always_propose_best_of_worst
    assert observed["candidate"]["courier_id"] == "10"
    assert observed["candidate"]["hard_safe"] is False
    assert observed["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert observed["recommend_only"] is True

    serialized = SD._serialize_result(result, event_id="A3-E1", latency_ms=1.0)
    assert serialized["proposal_best_of_worst"] == observed
    assert serialized["proposal_output_type"] == PO.COORDINATOR_ESCALATION


def test_off_is_byte_shape_parity_for_no_solo(monkeypatch):
    monkeypatch.setattr(C, "decision_flag", lambda *_a, **_k: False)
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: ("NO", "NO_ACTIVE_SHIFT", {"pickup_dist_km": 1.0}, None),
    )
    result = SEL.select_and_emit(_ctx(_fleet()), [])
    assert result.verdict == "KOORD" and result.best is None
    assert not hasattr(result, "always_propose_best_of_worst")
    serialized = SD._serialize_result(result, event_id="A3-OFF", latency_ms=1.0)
    assert "proposal_best_of_worst" not in serialized
    assert "proposal_output_type" not in serialized


def test_off_serializer_is_byte_identical_even_for_result_instrumented_on(
    monkeypatch,
):
    enabled = {"value": True}

    def decision_flag(name, *args, **kwargs):
        return enabled["value"] if name == "ENABLE_ALWAYS_PROPOSE" else False

    monkeypatch.setattr(C, "decision_flag", decision_flag)
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(SD, "now_iso", lambda: "2026-08-03T12:30:00+00:00")
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "NO",
            "NO_ACTIVE_SHIFT",
            {"pickup_dist_km": 1.0},
            None,
        ),
    )
    instrumented_result = SEL.select_and_emit(_ctx(_fleet()), [])
    assert hasattr(instrumented_result, "always_propose_best_of_worst")

    enabled["value"] = False
    with_instrument_bytes = json.dumps(
        SD._serialize_result(
            instrumented_result,
            event_id="A3-BYTE",
            latency_ms=1.0,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    del instrumented_result.always_propose_best_of_worst
    legacy_shape_bytes = json.dumps(
        SD._serialize_result(
            instrumented_result,
            event_id="A3-BYTE",
            latency_ms=1.0,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert with_instrument_bytes == legacy_shape_bytes


def test_best_of_worst_policy_is_hard_first_and_tie_is_canonical_cid():
    from dispatch_v2.core import always_propose as AP

    def candidate(cid, verdict, distance, score, plan):
        return SimpleNamespace(
            courier_id=cid,
            feasibility_verdict=verdict,
            feasibility_reason="fixture",
            plan=plan,
            score=score,
            metrics={"pickup_dist_km": distance},
        )

    plan = SimpleNamespace(total_duration_min=20.0)
    hard_no_but_close = candidate("1", "NO", 0.1, 999.0, None)
    safe_20 = candidate("20", "MAYBE", 2.0, 50.0, plan)
    safe_10 = candidate("10", "MAYBE", 2.0, 50.0, plan)

    first = AP.build_best_of_worst(
        [hard_no_but_close, safe_20, safe_10], fleet_count=3
    )
    second = AP.build_best_of_worst(
        [safe_10, hard_no_but_close, safe_20], fleet_count=3
    )
    assert first == second
    assert first["candidate"]["courier_id"] == "10"
    assert first["candidate"]["hard_safe"] is True
    assert first["candidate"]["a3_solo_score"] == 50.0
    assert "score" not in first["candidate"]
    assert first["proposal_output_type"] == PO.LEAST_DAMAGE_ALERT


def test_f3_nonempty_fleet_without_observable_cid_is_total_diagnostic():
    from dispatch_v2.core import always_propose as AP

    observed_errors = []
    fallback = AP.build_best_of_worst(
        [],
        fleet_count=1,
        on_error=lambda code, exception_type: observed_errors.append(
            (code, exception_type)
        ),
    )
    assert fallback["candidate"] is None
    assert fallback["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert fallback["recommend_only"] is True
    assert fallback["diagnostic"]["status"] == "FALLBACK"
    assert fallback["invariant_violation"] == (
        AP.NON_EMPTY_FLEET_WITHOUT_OBSERVABLE_CID
    )
    assert observed_errors == [
        (AP.NON_EMPTY_FLEET_WITHOUT_OBSERVABLE_CID, "_AlwaysProposeInvariant")
    ]

    empty = AP.build_best_of_worst([], fleet_count=0)
    assert empty["candidate"] is None
    assert "diagnostic" not in empty


def test_f3_blank_cid_on_always_returns_a_decision_and_logs(
    monkeypatch, caplog
):
    from dispatch_v2.core import always_propose as AP

    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "NO",
            "NO_ACTIVE_SHIFT",
            {"pickup_dist_km": 1.0},
            None,
        ),
    )
    blank_cid_fleet = {"  ": next(iter(_fleet().values()))}

    with caplog.at_level(logging.ERROR):
        result = SEL.select_and_emit(_ctx(blank_cid_fleet), [])

    assert result.verdict == "KOORD"
    assert result.best is None
    assert result.reason.startswith("no_solo_candidates")
    diagnostic = result.always_propose_best_of_worst
    assert diagnostic["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert diagnostic["invariant_violation"] == (
        AP.NON_EMPTY_FLEET_WITHOUT_OBSERVABLE_CID
    )
    assert any(
        "A3_TOTAL_FALLBACK" in record.getMessage()
        and AP.NON_EMPTY_FLEET_WITHOUT_OBSERVABLE_CID in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize(
    "faulted_symbol",
    [
        "_physical_fleet_count",
        "_finite_number",
        "_score",
        "_rank_key",
        "canon_cid",
        "_deduplicate",
        "_is_hard_safe",
        "_pickup_distance",
        "_soft_rank_key",
        "_public_candidate",
    ],
)
def test_f3_every_internal_builder_branch_falls_back_to_a_decision(
    monkeypatch, faulted_symbol
):
    from dispatch_v2.core import always_propose as AP

    plan = SimpleNamespace(total_duration_min=20.0)
    candidate = SimpleNamespace(
        courier_id="10",
        feasibility_verdict="MAYBE",
        feasibility_reason="fixture",
        plan=plan,
        score=50.0,
        metrics={"pickup_dist_km": 1.0},
    )

    def injected_failure(*_args, **_kwargs):
        raise RuntimeError(f"fault:{faulted_symbol}")

    monkeypatch.setattr(AP, faulted_symbol, injected_failure)
    observed_errors = []
    fallback = AP.build_best_of_worst(
        [candidate],
        fleet_count=1,
        on_error=lambda code, exception_type: observed_errors.append(
            (code, exception_type)
        ),
    )

    assert fallback["candidate"] is None
    assert fallback["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert fallback["diagnostic"] == {
        "status": "FALLBACK",
        "code": AP.INTERNAL_ERROR,
    }
    assert observed_errors == [(AP.INTERNAL_ERROR, "RuntimeError")]

    # Ten sam seam musi zostać domknięty także przez publiczny boundary:
    # nie wystarcza payload helpera — select_and_emit zawsze zwraca decyzję.
    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "NO",
            "NO_ACTIVE_SHIFT",
            {"pickup_dist_km": 1.0},
            None,
        ),
    )
    result = SEL.select_and_emit(_ctx(_fleet()), [])
    assert result.verdict == "KOORD"
    assert result.best is None
    assert result.always_propose_best_of_worst["diagnostic"]["code"] == (
        AP.INTERNAL_ERROR
    )


def test_f3_logger_failure_cannot_break_total_fallback(monkeypatch):
    from dispatch_v2.core import always_propose as AP

    monkeypatch.setattr(
        AP,
        "_deduplicate",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("builder fault")),
    )

    def broken_logger(*_args, **_kwargs):
        raise RuntimeError("logger fault")

    fallback = AP.build_best_of_worst(
        [SimpleNamespace(courier_id="10")],
        fleet_count=1,
        on_error=broken_logger,
    )
    assert fallback["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert fallback["diagnostic"]["code"] == AP.INTERNAL_ERROR


def test_f3_callsite_preserves_decision_if_public_instrument_raises(
    monkeypatch, caplog
):
    from dispatch_v2.core import always_propose as AP

    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "NO",
            "NO_ACTIVE_SHIFT",
            {"pickup_dist_km": 1.0},
            None,
        ),
    )
    monkeypatch.setattr(
        AP,
        "build_best_of_worst",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("public fault")),
    )

    with caplog.at_level(logging.ERROR):
        result = SEL.select_and_emit(_ctx(_fleet()), [])

    assert result.verdict == "KOORD"
    assert result.best is None
    assert result.always_propose_best_of_worst["diagnostic"]["code"] == (
        AP.SELECT_ATTACHMENT_ERROR
    )
    assert any(
        "A3_ATTACH_FALLBACK" in record.getMessage()
        for record in caplog.records
    )


def test_f3_callsite_logger_failure_cannot_erase_decision(monkeypatch):
    from dispatch_v2.core import always_propose as AP

    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "NO",
            "NO_ACTIVE_SHIFT",
            {"pickup_dist_km": 1.0},
            None,
        ),
    )
    monkeypatch.setattr(
        AP,
        "build_best_of_worst",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("public fault")),
    )
    monkeypatch.setattr(
        DP.log,
        "error",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("logger fault")),
    )

    result = SEL.select_and_emit(_ctx(_fleet()), [])

    assert result.verdict == "KOORD"
    assert result.best is None
    assert result.always_propose_best_of_worst["diagnostic"]["code"] == (
        AP.SELECT_ATTACHMENT_ERROR
    )


def test_missing_gps_soft_evidence_is_neutral_not_a_hidden_penalty():
    from dispatch_v2.core import always_propose as AP

    unknown = SimpleNamespace(
        courier_id="10", feasibility_verdict="NO",
        feasibility_reason="NO_POSITION", plan=None, score=-1_000_000_000.0,
        metrics={"pickup_dist_km": None},
    )
    known = SimpleNamespace(
        courier_id="20", feasibility_verdict="NO",
        feasibility_reason="HARD", plan=None, score=99.0,
        metrics={"pickup_dist_km": 0.1},
    )
    first = AP.build_best_of_worst([known, unknown], fleet_count=2)
    second = AP.build_best_of_worst([unknown, known], fleet_count=2)
    assert first == second
    assert first["soft_evidence_complete"] is False
    assert first["candidate"]["courier_id"] == "10"
    assert first["candidate"]["a3_solo_score"] is None
    assert "score" not in first["candidate"]


def test_missing_soft_metric_never_rewrites_a_hard_safe_verdict(monkeypatch):
    monkeypatch.setattr(
        C, "decision_flag", lambda name, *a, **k: name == "ENABLE_ALWAYS_PROPOSE"
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=False: default)
    plan = SimpleNamespace(total_duration_min=20.0, sla_violations=[])
    monkeypatch.setattr(
        DP,
        "check_feasibility_v2",
        lambda **_kw: (
            "MAYBE",
            "origin estimate without comparable distance",
            {"pickup_dist_km": None},
            plan,
        ),
    )

    result = SEL.select_and_emit(_ctx(_fleet()), [])
    assert result.verdict == "KOORD" and result.best is None  # log-only
    observed = result.always_propose_best_of_worst
    assert observed["hard_safe_count"] == 2
    assert observed["soft_evidence_complete"] is False
    assert observed["candidate"]["hard_safe"] is True
    assert observed["candidate"]["feasibility_verdict"] == "MAYBE"
    assert observed["proposal_output_type"] == PO.LEAST_DAMAGE_ALERT


def test_mutation_removing_hard_filter_changes_winner(monkeypatch):
    from dispatch_v2.core import always_propose as AP

    plan = SimpleNamespace(total_duration_min=20.0)
    rejected = SimpleNamespace(
        courier_id="1", feasibility_verdict="NO", feasibility_reason="HARD",
        plan=None, score=999.0, metrics={"pickup_dist_km": 0.1},
    )
    legal = SimpleNamespace(
        courier_id="2", feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=plan, score=1.0, metrics={"pickup_dist_km": 2.0},
    )
    assert AP.build_best_of_worst([rejected, legal], fleet_count=2)["candidate"][
        "courier_id"
    ] == "2"
    monkeypatch.setattr(AP, "_is_hard_safe", lambda _candidate: True)
    mutant = AP.build_best_of_worst([rejected, legal], fleet_count=2)
    assert mutant["candidate"]["courier_id"] == "1"  # oracle czerwienieje na mutancie


def test_red_escalation_resolution_is_written_to_existing_learning_stream(
    tmp_path, monkeypatch
):
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))

    PW._check_panel_override(
        "A3-O1",
        "20",
        "panel_diff",
        _context_by_receipt=_receipt_context(
            decision_context=_escalation_context()
        ),
    )

    rows = _read_jsonl(learning)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "COORDINATOR_ESCALATION_RESOLVED"
    assert row["proposal_output_type"] == PO.COORDINATOR_ESCALATION
    assert row["actual_courier_id"] == "20"
    assert row["assignment"]["assigned_at"] == "2026-08-03T12:30:00+00:00"
    assert row["assignment"]["assigned_by"]["actor_id"] == "actor_sha256:fixture"
    assert row["engine_context"]["decision_event_id"] == "A3-O1_NEW_ORDER_1"


def test_coordinator_hold_is_not_mistaken_for_final_human_assignment(
    tmp_path, monkeypatch
):
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))
    legacy_decision = {
        "order_id": "A3-O1",
        "event_id": "A3-O1_NEW_ORDER_1",
        "ts": "2026-08-03T12:29:00+00:00",
        "verdict": "KOORD",
        "reason": "no_solo_candidates (fleet_n=2)",
        "best": {"courier_id": "10", "score": -137.4},
        "alternatives": [{"courier_id": "20", "score": -140.0}],
    }
    receipt = _receipt_context(
        pending_record={
            "sent_at": legacy_decision["ts"],
            "decision_record": legacy_decision,
        },
        decision_context=_escalation_context(),
    )

    PW._check_panel_override(
        "A3-O1",
        str(PW.KOORDYNATOR_ID),
        "panel_diff",
        _context_by_receipt=receipt,
    )
    hold_rows = _read_jsonl(learning)
    assert len(hold_rows) == 1
    assert hold_rows[0]["action"] == "PANEL_OVERRIDE"
    assert hold_rows[0]["actual_courier_id"] == str(PW.KOORDYNATOR_ID)
    assert hold_rows[0]["decision"] == legacy_decision
    assert hold_rows[0].get("learning_event_type") is None

    PW._check_panel_override(
        "A3-O1",
        "20",
        "panel_reassign",
        _context_by_receipt=receipt,
    )
    rows = _read_jsonl(learning)
    assert [row["action"] for row in rows] == [
        "PANEL_OVERRIDE",
        "COORDINATOR_ESCALATION_RESOLVED"
    ]
    assert rows[1]["actual_courier_id"] == "20"


def test_red_manual_override_is_owner_exception_without_reason_prompt(
    tmp_path, monkeypatch
):
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))
    decision = {
        "order_id": "A3-O1",
        "event_id": "A3-O1_NEW_ORDER_1",
        "ts": "2026-08-03T12:29:00+00:00",
        "verdict": "PROPOSE",
        "proposal_output_type": PO.EXECUTABLE_PROPOSAL,
        "reason": "feasible=2 best=10",
        "best": {"courier_id": "10", "score": 50.0},
        "alternatives": [{"courier_id": "20", "score": 40.0}],
    }
    pending = {"sent_at": decision["ts"], "decision_record": decision}
    context = _receipt_context(
        pending_record=pending,
        decision_context={
            **_escalation_context(),
            "proposal_output_type": PO.EXECUTABLE_PROPOSAL,
            "verdict": "PROPOSE",
            "reason": "feasible=2 best=10",
            "best_courier_id": "10",
            "proposal_best_of_worst": None,
        },
    )

    PW._check_panel_override(
        "A3-O1", "20", "panel_diff", _context_by_receipt=context
    )
    row = _read_jsonl(learning)[0]
    assert row["action"] == "PANEL_OVERRIDE"  # kompatybilność obecnych konsumentów
    assert row["learning_event_type"] == PO.OWNER_EXCEPTION
    assert row["proposal_output_type"] == PO.OWNER_EXCEPTION
    assert row["reason"] == "nieokreślony"
    assert row["explanation"]["status"] == "PENDING"
    assert row["actual_courier_id"] == "20"
    assert row["assignment"]["assigned_by"]["status"] == "ATTESTED"


@pytest.mark.parametrize(
    "a3_type,a3_best,pending_best,expected_action",
    [
        (
            PO.COORDINATOR_ESCALATION,
            None,
            "20",
            "COORDINATOR_ESCALATION_RESOLVED",
        ),
        (PO.EXECUTABLE_PROPOSAL, "10", "20", "PANEL_OVERRIDE"),
        (PO.EXECUTABLE_PROPOSAL, "20", "10", None),
    ],
)
def test_exact_a3_context_wins_over_conflicting_stale_pending(
    tmp_path,
    monkeypatch,
    a3_type,
    a3_best,
    pending_best,
    expected_action,
):
    """First-wins receipt nie może utrwalić klasy z innej decyzji."""
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))
    pending_decision = {
        "order_id": "A3-O1",
        "event_id": "A3-OLD-PENDING",
        "ts": "2026-08-03T12:28:00+00:00",
        "verdict": "PROPOSE",
        "reason": "stale pending fixture",
        "best": {"courier_id": pending_best, "score": 40.0},
    }
    decision_context = {
        **_escalation_context(),
        "proposal_output_type": a3_type,
        "verdict": (
            "KOORD" if a3_type == PO.COORDINATOR_ESCALATION else "PROPOSE"
        ),
        "reason": (
            "no_solo_candidates"
            if a3_type == PO.COORDINATOR_ESCALATION
            else "current executable fixture"
        ),
        "best_courier_id": a3_best,
        "proposal_best_of_worst": (
            _escalation_context()["proposal_best_of_worst"]
            if a3_type == PO.COORDINATOR_ESCALATION else None
        ),
    }
    receipt = _receipt_context(
        pending_record={
            "sent_at": pending_decision["ts"],
            "decision_record": pending_decision,
        },
        decision_context=decision_context,
    )

    # Runtime order is AGREE first, then OVERRIDE/A-3.
    PW._check_panel_agree(
        "A3-O1",
        "20",
        "panel_diff",
        _enabled_by_receipt=True,
        _context_by_receipt=receipt,
    )
    PW._check_panel_override(
        "A3-O1",
        "20",
        "panel_diff",
        _context_by_receipt=receipt,
    )

    rows = _read_jsonl(learning)
    if expected_action is None:
        assert rows == []
    else:
        assert [row["action"] for row in rows] == [expected_action]
        assert rows[0]["engine_decision_event_id"] == "A3-O1_NEW_ORDER_1"


def test_a3_durable_projection_is_idempotent_for_escalation_resolution(
    tmp_path, monkeypatch
):
    learning = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))
    monkeypatch.setattr(PW, "_durable_downstream_attempt", lambda *_a, **_k: 1)
    projections: dict[str, dict] = {}

    def get_projection(effect_id):
        return projections.get(effect_id)

    def prepare(lifecycle_event_id, effect_name, record):
        effect_id = f"{lifecycle_event_id}:{effect_name}"
        if effect_id in projections:
            return projections[effect_id], False
        projections[effect_id] = {
            "effect_id": effect_id,
            "record": dict(record),
            "projected_at": None,
        }
        return projections[effect_id], True

    def mark(effect_id):
        projections[effect_id]["projected_at"] = "2026-08-03T12:30:01+00:00"
        return True

    monkeypatch.setattr(EB, "get_durable_learning_projection", get_projection)
    monkeypatch.setattr(EB, "prepare_durable_learning_projection", prepare)
    monkeypatch.setattr(EB, "mark_durable_learning_projected", mark)
    context = _receipt_context(decision_context=_escalation_context())

    for _ in range(2):
        PW._check_panel_override(
            "A3-O1",
            "20",
            "panel_diff",
            lifecycle_event_id="A3-ASSIGN-1",
            _raise_on_error=True,
            _context_by_receipt=context,
        )

    rows = _read_jsonl(learning)
    assert len(rows) == 1
    assert rows[0]["action"] == "COORDINATOR_ESCALATION_RESOLVED"
    assert rows[0]["assignment_lifecycle_event_id"] == "A3-ASSIGN-1"


def test_context_index_is_first_wins_idempotent_and_ack_is_exact_cas(tmp_path):
    path = tmp_path / "always_propose_decision_context.json"
    record = {
        "ts": "2026-08-03T12:29:00+00:00",
        "event_id": "A3-O1_NEW_ORDER_1",
        "order_id": "A3-O1",
        "verdict": "KOORD",
        "reason": "no_solo_candidates",
        "proposal_output_type": PO.COORDINATOR_ESCALATION,
        "best": None,
        "proposal_best_of_worst": {
            "schema": "always_propose.best_of_worst.v1",
            "proposal_output_type": PO.COORDINATOR_ESCALATION,
            "recommend_only": True,
            "candidate": {
                "courier_id": "10",
                "hard_safe": False,
                "feasibility_verdict": "NO",
                "name": "NIE MOŻE WEJŚĆ DO INDEKSU",
            },
        },
        "delivery_address": "NIE MOŻE WEJŚĆ DO INDEKSU",
    }
    first = APL.remember_decision(record, path=str(path), now=NOW)
    duplicate = APL.remember_decision(
        {**record, "ts": "2026-08-03T12:31:00+00:00", "reason": "mutated"},
        path=str(path),
        now=NOW,
    )
    assert duplicate == first
    raw = path.read_text(encoding="utf-8")
    assert raw.count('"A3-O1"') == 2  # klucz + jawne order_id wpisu
    assert "NIE MOŻE" not in raw

    newer = {**record, "event_id": "A3-O1_NEW_ORDER_2"}
    APL.remember_decision(newer, path=str(path), now=NOW)
    assert APL.acknowledge_decision(
        "A3-O1", "A3-O1_NEW_ORDER_1", path=str(path)
    ) is False
    assert APL.acknowledge_decision(
        "A3-O1", "A3-O1_NEW_ORDER_2", path=str(path)
    ) is True
    assert APL.peek_decision("A3-O1", path=str(path)) is None


def test_actor_attestation_is_pseudonymous_and_never_stores_raw_identity(tmp_path):
    audit = tmp_path / "coordinator_assign_audit.jsonl"
    raw_actor = "synthetic.operator@nadajesz.pl"
    audit.write_text(json.dumps({
        "ts": "2026-08-03T12:29:50+00:00",
        "mode": "live",
        "kind": "assign",
        "ok": True,
        "rc": 0,
        "order_id": "A3-O1",
        "actor": raw_actor,
    }) + "\n", encoding="utf-8")
    assigned_by = APL.capture_assigned_by(
        "A3-O1",
        "2026-08-03T12:30:00+00:00",
        audit_path=str(audit),
    )
    assert assigned_by["status"] == "ATTESTED"
    assert assigned_by["actor_id"].startswith("actor_sha256:")
    assert raw_actor not in json.dumps(assigned_by)
    record = APL.build_learning_record(
        context=_escalation_context(),
        actual_courier_id="20",
        assigned_at="2026-08-03T12:30:00+00:00",
        assigned_by={**assigned_by, "raw_actor": raw_actor},
        panel_source="panel_diff",
        assignment_lifecycle_event_id="A3-ASSIGN-1",
    )
    assert raw_actor not in json.dumps(record)

    # Bieżący writer panelu nadal ma wąski legacy schema bez kind=assign.
    audit.write_text(json.dumps({
        "ts": "2026-08-03T12:29:51+00:00",
        "mode": "live",
        "actor": raw_actor,
        "order_id": "A3-O1",
        "courier": "Synthetic Courier",
        "command": "/venv/python /scripts/gastro_assign.py --id A3-O1",
        "ok": True,
        "rc": 0,
    }) + "\n", encoding="utf-8")
    legacy = APL.capture_assigned_by(
        "A3-O1",
        "2026-08-03T12:30:00+00:00",
        audit_path=str(audit),
    )
    assert legacy["status"] == "ATTESTED"
    assert legacy["audit_schema"] == "legacy_gastro_assign_signature"
    assert raw_actor not in json.dumps(legacy)
    legacy_record = APL.build_learning_record(
        context=_escalation_context(),
        actual_courier_id="20",
        assigned_at="2026-08-03T12:30:00+00:00",
        assigned_by=legacy,
        panel_source="panel_diff",
        assignment_lifecycle_event_id="A3-ASSIGN-LEGACY",
    )
    assert legacy_record["assignment"]["assigned_by"]["audit_schema"] == (
        "legacy_gastro_assign_signature"
    )
    assert raw_actor not in json.dumps(legacy_record)

    audit.write_text(json.dumps({
        "ts": "2026-08-03T12:29:50+00:00",
        "mode": "live",
        "kind": "assign",
        "ok": True,
        "rc": 0,
        "order_id": "A3-O1",
        "actor": "test@nadajesz.pl",
    }) + "\n", encoding="utf-8")
    filtered = APL.capture_assigned_by(
        "A3-O1",
        "2026-08-03T12:30:00+00:00",
        audit_path=str(audit),
    )
    assert filtered == {
        "status": "UNKNOWN",
        "actor_id": None,
        "provenance": "coordinator_assign_audit",
        "reason": "actor_filtered",
    }


def test_flag_off_preserves_legacy_panel_receipt_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "decision_flag", lambda *_a, **_k: False)
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(tmp_path / "pending.json"))
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(tmp_path / "learning.jsonl"))
    context = PW._capture_panel_learning_context("A3-OFF")
    assert context["schema_version"] == 3
    assert "always_propose_enabled" not in context
    assert "always_propose_decision_context" not in context
    assert "assigned_by" not in context


@pytest.mark.parametrize(
    "courier_id,expected_acknowledgements",
    [
        ("20", [("A3-O1", "A3-O1_NEW_ORDER_1")]),
        (str(PW.KOORDYNATOR_ID), []),
    ],
)
def test_lifecycle_consumes_context_only_after_final_courier_assignment(
    monkeypatch, courier_id, expected_acknowledgements
):
    monkeypatch.setattr(
        LD.state_machine,
        "get_order_strict",
        lambda _oid: {"status": "assigned", "courier_id": courier_id},
    )
    monkeypatch.setattr(PW, "_check_panel_agree", lambda *_a, **_k: None)
    monkeypatch.setattr(PW, "_check_panel_override", lambda *_a, **_k: None)
    monkeypatch.setattr(PW, "_save_plan_on_assign_signal", lambda *_a, **_k: None)
    monkeypatch.setattr(PW, "_remove_pending_on_assign", lambda *_a, **_k: None)
    acknowledgements = []
    monkeypatch.setattr(
        APL,
        "acknowledge_decision",
        lambda oid, event_id: acknowledgements.append((oid, event_id)) or True,
    )
    LD.apply({
        "event_type": "COURIER_ASSIGNED",
        "event_id": "A3-ASSIGN-1",
        "order_id": "A3-O1",
        "courier_id": courier_id,
        "payload": {"source": "panel_diff"},
        "panel_learning_context": {
            "always_propose_enabled": True,
            "always_propose_decision_context": _escalation_context(),
        },
    })
    assert acknowledgements == expected_acknowledgements


@pytest.mark.parametrize(
    "reason,source_prefix,expected_class",
    [
        ("state_likely_stale (age=90s)", "state_likely_stale", "STALE"),
        ("geometry_blind_fallback (pool=2)", "geometry_blind_fallback", "GEOMETRY"),
        ("commit_divergence_gate (delta=16min)", "commit_divergence_gate", "COMMIT"),
        ("difficult_geometry_redirect (score=-40)", "difficult_geometry_redirect", "DIFFICULT"),
    ],
)
def test_f1_named_escalation_classes_map_from_their_real_sources(
    reason, source_prefix, expected_class
):
    result = SimpleNamespace(verdict="KOORD", reason=reason, best=None)
    escalation_class = PO.coordinator_escalation_class(result)
    assert escalation_class.value == expected_class
    # Ratchet writera: każdy prefiks klasy musi nadal pochodzić z realnej
    # gałęzi selection, a nie wyłącznie z fixture/mappingu.
    assert source_prefix in inspect.getsource(SEL.select_and_emit)

    context = APL.decision_context_from_record({
        "ts": "2026-08-03T12:29:00+00:00",
        "event_id": "A3-O1_NEW_ORDER_1",
        "order_id": "A3-O1",
        "verdict": "KOORD",
        "reason": reason,
        "proposal_output_type": PO.COORDINATOR_ESCALATION,
        "best": None,
    })
    record = APL.build_learning_record(
        context=context,
        actual_courier_id="20",
        assigned_at="2026-08-03T12:30:00+00:00",
        assigned_by={"status": "UNKNOWN", "actor_id": None},
        panel_source="panel_diff",
        assignment_lifecycle_event_id="A3-ASSIGN-1",
    )
    assert record["action"] == "COORDINATOR_ESCALATION_RESOLVED"
    assert record["coordinator_escalation_class"] == expected_class
    assert record["engine_context"]["coordinator_escalation_class"] == (
        expected_class
    )
    assert record["engine_context"]["reason"] == reason


def test_f1_escalation_enum_has_exact_owner_named_classes_plus_unknown():
    assert tuple(item.value for item in PO.CoordinatorEscalationClass) == (
        "STALE",
        "GEOMETRY",
        "COMMIT",
        "DIFFICULT",
        "UNKNOWN",
    )


@pytest.mark.parametrize(
    "reason",
    [
        "no_solo_candidates (fleet_n=2)",
        "all_candidates_low_score (best=-99)",
        "best_effort_r6_breach (count=1)",
        "best_effort_r6_breach_v2 (count=1)",
        "best_effort_low_score (best=-99)",
        "new_unclassified_coordinator_reason",
    ],
)
def test_f1_unmapped_coordinator_reason_is_explicit_unknown(reason):
    result = SimpleNamespace(verdict="KOORD", reason=reason, best=None)
    escalation_class = PO.coordinator_escalation_class(result)
    assert escalation_class.value == "UNKNOWN"


def test_f1_mutation_removing_source_mapping_turns_named_class_unknown(
    monkeypatch
):
    result = SimpleNamespace(
        verdict="KOORD",
        reason="commit_divergence_gate (delta=16min)",
        best=None,
    )
    assert PO.coordinator_escalation_class(result).value == "COMMIT"
    monkeypatch.setattr(
        PO,
        "COORDINATOR_ESCALATION_REASON_CLASSES",
        tuple(
            item
            for item in PO.COORDINATOR_ESCALATION_REASON_CLASSES
            if item[0] != "commit_divergence_gate"
        ),
    )
    assert PO.coordinator_escalation_class(result).value == "UNKNOWN"


def test_ratchet_single_learning_stream_and_exact_context_wiring():
    from dispatch_v2.core import always_propose as AP
    from dispatch_v2.tools import decision_episode_v1 as DE

    learning_source = inspect.getsource(APL)
    shadow_source = inspect.getsource(SD._tick)
    panel_source = inspect.getsource(PW._check_panel_override)
    proposal_source = inspect.getsource(PO)
    public_candidate_source = inspect.getsource(AP._public_candidate)
    episode_candidate_source = inspect.getsource(DE._shadow_candidates)
    assert "append_jsonl" not in learning_source
    assert "remember_decision(record)" in shadow_source
    assert shadow_source.index(
        "_append_decision(shadow_log_path, record)"
    ) < shadow_source.index(
        "_a3_learning.remember_decision(record)"
    ) < shadow_source.index(
        "_pp_upserts.append((str(oid), record))"
    )
    assert "except Exception as _a3_context_exc" in shadow_source
    assert "_append_learning_record(" in panel_source
    assert "always_propose_decision_context.json" in learning_source
    assert "if _a3_enabled and not _a3_coordinator_hold:" in panel_source
    assert proposal_source.count("COORDINATOR_ESCALATION_REASON_CLASSES =") == 1
    assert '"score":' not in public_candidate_source
    assert "candidates.append(dict(diagnostic))" not in episode_candidate_source
    assert "update(overlay)" in episode_candidate_source
