"""Czasowka reclaim: wylacznie shadow durable PICKUP_TIME_UPDATED.

Fixture sa syntetyczne i hermetyczne. Nie wymagaja OR-Tools, sieci ani stanu
produkcyjnego; test LIVE obejmuje tylko niepodlaczony kontrakt state-machine.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import durable_event_apply as DEA
from dispatch_v2 import event_bus as EB
from dispatch_v2 import lifecycle_downstream as LD
from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import state_machine as SM
from dispatch_v2 import czasowka_reclaim as CR
from dispatch_v2.order_fsm import FsmOutcome, validate_order_event


OBSERVED = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)


def _at(minutes: float, *, base: datetime = OBSERVED) -> str:
    return (base + timedelta(minutes=minutes)).isoformat()


def _order(new_min: float = 80, **overrides) -> dict:
    order = {
        "order_id": "oid-1",
        "status": "assigned",
        "commitment_level": "assigned",
        "courier_id": "101",
        "picked_up_at": None,
        "assignment_event_id": "assign-1",
        "pickup_at_at_assignment": _at(40),
        "pickup_at_warsaw": _at(new_min),
        "prep_minutes": 30,
        "source": "gastro",
        "address_id": 1,
        "reclaim_exempt": False,
        "reclaim_exempt_reason": None,
    }
    order.update(overrides)
    return order


def _event(
    old_min: float,
    new_min: float,
    *,
    event_id: str = "pickup-time-1",
    assignment_event_id: str = "assign-1",
    courier_id: str = "101",
    base: datetime = OBSERVED,
) -> dict:
    return {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": event_id,
        "order_id": "oid-1",
        "courier_id": courier_id,
        "durable_observed_at": base.isoformat(),
        "czasowka_reclaim_shadow_authorized": True,
        "czasowka_reclaim_live_authorized": False,
        "payload": {
            "oid": "oid-1",
            "courier_id": courier_id,
            "old_pickup_at_warsaw": _at(old_min, base=base),
            "new_pickup_at_warsaw": _at(new_min, base=base),
            "delta_min": new_min - old_min,
            "source": "panel_re_check",
            "assignment_event_id_at_observation": assignment_event_id,
            "courier_id_at_observation": courier_id,
        },
    }


@pytest.mark.parametrize(
    "old_min,new_min,expected,reason",
    [
        (70, 85, False, "old_at_or_before_60m_boundary"),  # +15, ale daleko
        (-20, 45, False, "new_at_or_after_hysteresis_boundary"),
        (50, 80, True, None),
        (60, 65, True, None),
        (60 + 1 / 60, 80, False, "old_at_or_before_60m_boundary"),
        (50, 65 - 1 / 60, False, "new_at_or_after_hysteresis_boundary"),
    ],
)
def test_trigger_is_boundary_crossing_not_delta(
    old_min, new_min, expected, reason
):
    record = CR.evaluate_pickup_time_updated(
        _event(old_min, new_min), _order(new_min)
    )

    assert record["would_reclaim"] is expected
    assert record["rejection_reason"] == reason
    assert record["near_boundary_at"] == _at(60)
    assert record["reclaim_boundary_at"] == _at(65)


def test_hysteresis_mutation_probe_guards_plus_direction(monkeypatch):
    event = _event(50, 64)
    current = _order(64)

    monkeypatch.setattr(C, "CZASOWKA_RECLAIM_HYSTERESIS_MIN", 5)
    assert CR.evaluate_pickup_time_updated(event, current)["would_reclaim"] is False

    # Mutacja +5 -> -5 zmienilaby wynik tej samej fixture. Pierwsza asercja
    # zabija zatem zarowno odwrocony znak, jak i prog liczony od new/old.
    monkeypatch.setattr(C, "CZASOWKA_RECLAIM_HYSTERESIS_MIN", -5)
    assert CR.evaluate_pickup_time_updated(event, current)["would_reclaim"] is True


@pytest.mark.parametrize(
    "current,event,failed_guard",
    [
        (
            _order(picked_up_at=_at(55)),
            _event(50, 80),
            "picked_up_at_is_none",
        ),
        (
            _order(status="picked_up", picked_up_at=_at(55)),
            _event(50, 80),
            "status_is_assigned_string",
        ),
        (
            _order(
                assignment_event_id="assign-2",
                pickup_at_at_assignment=_at(80),
            ),
            _event(50, 80, assignment_event_id="assign-1"),
            "same_assignment_generation",
        ),
        (
            _order(
                reclaim_exempt=True,
                reclaim_exempt_reason="manual_time_hold",
            ),
            _event(50, 80),
            "not_reclaim_exempt",
        ),
        (
            _order(courier_id="102"),
            _event(50, 80, courier_id="101"),
            "courier_unchanged",
        ),
    ],
)
def test_state_and_causality_guards_are_fail_closed(current, event, failed_guard):
    record = CR.evaluate_pickup_time_updated(event, current)

    assert record["would_reclaim"] is False
    assert record["guards"][failed_guard] is False


def test_shadow_record_contains_every_future_action_and_guard_field():
    record = CR.evaluate_pickup_time_updated(_event(50, 80), _order(80))

    assert record["would_reclaim"] is True
    assert {
        "oid",
        "cid",
        "old_pickup_at_warsaw",
        "new_pickup_at_warsaw",
        "delta_min",
        "boundary_min",
        "hysteresis_min",
        "near_boundary_at",
        "reclaim_boundary_at",
        "guards",
        "rejection_reason",
        "future_action",
    } <= record.keys()
    assert all(value is True for value in record["guards"].values())
    assert record["future_action"] == {
        "event_type": "ORDER_RECLAIMED_TO_CZASOWKA",
        "status": "planned",
        "courier_id": "26",
        "previous_courier_id": "101",
        "reclaim_generation": "assign-1",
        "reclaimed_at": record["recorded_at"],
        "reason": "pickup_boundary_crossed",
    }


def test_firmowe_and_parcel_are_counted_separately_but_not_live_candidates():
    firmowe = _order(80, address_id=next(iter(C.FIRMOWE_KONTO_ADDRESS_IDS)))
    parcel = _order(80, source="parcel")
    firmowe_record = CR.evaluate_pickup_time_updated(
        _event(50, 80, event_id="firmowe"), firmowe
    )
    parcel_record = CR.evaluate_pickup_time_updated(
        _event(50, 80, event_id="parcel"), parcel
    )

    assert firmowe_record["would_reclaim_candidate"] is True
    assert parcel_record["would_reclaim_candidate"] is True
    assert firmowe_record["would_reclaim"] is False
    assert parcel_record["would_reclaim"] is False
    metrics = CR.aggregate_shadow_metrics([firmowe_record, parcel_record])
    assert metrics["firmowe_candidates_distinct"] == 1
    assert metrics["parcel_candidates_distinct"] == 1
    assert metrics["would_reclaim"] == 0


def test_off_has_zero_io_on_differs_and_metric_is_idempotent_per_generation(tmp_path):
    log_path = tmp_path / "reclaim.jsonl"
    event_1 = _event(50, 80, event_id="time-1")
    event_2 = _event(50, 80, event_id="time-2")
    current = _order(80)

    assert CR.record_pickup_time_shadow(
        event_1, current, log_path=log_path, enabled_by_receipt=False
    ) is None
    assert not log_path.exists()

    CR.record_pickup_time_shadow(
        event_1, current, log_path=log_path, enabled_by_receipt=True
    )
    CR.record_pickup_time_shadow(
        event_1, current, log_path=log_path, enabled_by_receipt=True
    )
    CR.record_pickup_time_shadow(
        event_2, current, log_path=log_path, enabled_by_receipt=True
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(rows) == 2
    metrics = CR.read_shadow_metrics(log_path)
    assert metrics["evaluated"] == 2
    assert metrics["would_reclaim_candidates_distinct"] == 1
    assert metrics["would_reclaim"] == 1


def _write_state(path, order):
    path.write_text(json.dumps({order["order_id"]: order}), encoding="utf-8")


def test_assignment_persists_generation_pickup_snapshot_and_manual_hold(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "orders_state.json"
    initial = _order(50, status="planned", courier_id="26")
    initial.pop("assignment_event_id")
    initial.pop("pickup_at_at_assignment")
    _write_state(state_path, initial)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(SM, "decision_flag", lambda _name: False)

    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "assign-durable-7",
            "order_id": "oid-1",
            "courier_id": "101",
            "payload": {
                "reclaim_exempt": True,
                "reclaim_exempt_reason": "manual_time_hold",
            },
        }
    )

    assigned = SM.get_order_strict("oid-1")
    assert assigned["assignment_event_id"] == "assign-durable-7"
    assert assigned["pickup_at_at_assignment"] == _at(50)
    assert assigned["reclaim_exempt"] is True
    assert assigned["reclaim_exempt_reason"] == "manual_time_hold"


def test_panel_pickup_emitter_captures_assignment_and_courier_snapshot():
    event = PW._diff_pickup_time(
        _order(50),
        {"pickup_at_warsaw": _at(80)},
        "oid-1",
    )

    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["assignment_event_id_at_observation"] == "assign-1"
    assert event["payload"]["courier_id_at_observation"] == "101"


def test_durable_downstream_receipt_is_after_state_and_only_pending_is_recovered(
    tmp_path, monkeypatch
):
    events_db = tmp_path / "events.db"
    state_path = tmp_path / "orders_state.json"
    with sqlite3.connect(events_db) as conn:
        conn.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                order_id TEXT, courier_id TEXT, payload TEXT,
                created_at TEXT NOT NULL, processed_at TEXT,
                status TEXT DEFAULT 'pending'
            );
            CREATE TABLE processed_events (
                event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL
            );
            """
        )
    base = datetime.now(timezone.utc)
    current = _order(50)
    current["pickup_at_warsaw"] = _at(50, base=base)
    current["pickup_at_at_assignment"] = _at(40, base=base)
    _write_state(state_path, current)
    monkeypatch.setattr(EB, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(EB, "_audit_log_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(SM, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name == "ENABLE_CZASOWKA_RECLAIM_SHADOW",
    )

    payload = {
        "oid": "oid-1",
        "courier_id": "101",
        "old_pickup_at_warsaw": _at(50, base=base),
        "new_pickup_at_warsaw": _at(80, base=base),
        "delta_min": 30,
        "source": "panel_re_check",
        "assignment_event_id_at_observation": "assign-1",
        "courier_id_at_observation": "101",
    }
    callbacks = []

    def fail_after_observing(event):
        callbacks.append(dict(event))
        assert SM.get_order_strict("oid-1")["pickup_at_warsaw"] == payload[
            "new_pickup_at_warsaw"
        ]
        assert event["durable_observed_at"]
        assert CR.evaluate_pickup_time_updated(
            event, SM.get_order_strict("oid-1")
        )["would_reclaim"] is True
        raise RuntimeError("synthetic crash after shadow")

    outcome = DEA.emit_and_apply(
        "PICKUP_TIME_UPDATED",
        order_id="oid-1",
        courier_id="101",
        payload=payload,
        state_payload=None,
        event_key="oid-1_PICKUP_TIME_UPDATED_reclaim-test",
        emit_fn=EB.emit_audit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=fail_after_observing,
        sweeper_enabled=False,
    )

    receipt = EB.get_state_apply_outbox(outcome.event_id)
    assert outcome.failure_stage == "downstream"
    assert receipt["state_status"] == "applied"
    assert receipt["downstream_status"] == "pending"
    assert receipt["state_event"]["czasowka_reclaim_shadow_authorized"] is True
    assert receipt["state_event"]["czasowka_reclaim_live_authorized"] is False

    recovered = []

    def recover(event):
        recovered.append(dict(event))
        assert event["durable_observed_at"] == callbacks[0]["durable_observed_at"]

    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=recover,
        min_age_seconds=0,
    )
    assert counts["completed"] == 1
    assert len(recovered) == 1
    assert EB.get_state_apply_outbox(outcome.event_id)["downstream_status"] == "applied"

    recovered_again = []
    second = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=recovered_again.append,
        min_age_seconds=0,
    )
    assert second["seen"] == 0
    assert recovered_again == []


def test_lifecycle_downstream_wires_shadow_only(monkeypatch):
    current = _order(80)
    event = _event(50, 80)
    calls = []
    monkeypatch.setattr(SM, "get_order_strict", lambda _oid: current)
    monkeypatch.setattr(CR, "record_pickup_time_shadow", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(PW, "_invalidate_plan_on_committed_change", lambda *a, **kw: None)

    LD.apply(event)

    assert calls[0][0] == (event, current)
    assert calls[0][1]["enabled_by_receipt"] is True
    source = inspect.getsource(LD.apply)
    assert "build_live_reclaim_event" not in source
    assert "ORDER_RECLAIMED_TO_CZASOWKA" not in source


def test_live_stub_is_off_and_state_event_semantics_are_atomic(tmp_path, monkeypatch):
    pickup_event = _event(50, 80)
    current = _order(80)
    assert CR.build_live_reclaim_event(
        pickup_event, current, live_enabled=False
    ) is None

    live_event = CR.build_live_reclaim_event(
        pickup_event,
        current,
        live_enabled=True,
        evaluated_at=OBSERVED,
    )
    assert live_event is not None
    same_generation = CR.build_live_reclaim_event(
        _event(50, 90, event_id="another-time-event"),
        _order(90),
        live_enabled=True,
        evaluated_at=OBSERVED,
    )
    assert same_generation["event_id"] == live_event["event_id"]
    state_path = tmp_path / "orders_state.json"
    _write_state(state_path, current)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(SM, "decision_flag", lambda _name: False)

    blocked = dict(live_event)
    blocked["czasowka_reclaim_live_authorized"] = False
    assert SM.event_effect_status(blocked, current) == "superseded"
    assert SM.update_from_event(blocked) is None
    assert SM.get_order_strict("oid-1")["courier_id"] == "101"

    verdict = validate_order_event(live_event, current)
    assert verdict.outcome is FsmOutcome.LEGAL
    assert verdict.transition_allowed is True
    assert SM.event_effect_status(live_event, current) == "pending"
    updated = SM.update_from_event(live_event)
    assert {
        "status": updated["status"],
        "courier_id": updated["courier_id"],
        "previous_courier_id": updated["previous_courier_id"],
        "reclaim_generation": updated["reclaim_generation"],
        "reclaimed_at": updated["reclaimed_at"],
        "reason": updated["reason"],
    } == {
        "status": "planned",
        "courier_id": "26",
        "previous_courier_id": "101",
        "reclaim_generation": "assign-1",
        "reclaimed_at": OBSERVED.isoformat(),
        "reason": "pickup_boundary_crossed",
    }
    assert SM.event_effect_status(live_event, updated) == "applied"


def test_flags_are_independent_etap4_defaults_off():
    assert C.ENABLE_CZASOWKA_RECLAIM_SHADOW is False
    assert C.ENABLE_CZASOWKA_RECLAIM_LIVE is False
    assert "ENABLE_CZASOWKA_RECLAIM_SHADOW" in C.ETAP4_DECISION_FLAGS
    assert "ENABLE_CZASOWKA_RECLAIM_LIVE" in C.ETAP4_DECISION_FLAGS
