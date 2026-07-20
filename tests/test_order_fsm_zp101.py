"""Z-P1-01 Phase A: formal order FSM validator + log-only observer.

Hermetic by construction: pure-validator tests touch no files; the two legacy
integration tests redirect orders_state to ``tmp_path``, replace the logger,
and disable all optional decision guards.  No runtime flags, network, or live
state are read.
"""
from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("DISPATCH_UNDER_PYTEST", "1")

from dispatch_v2.order_fsm import (  # noqa: E402
    ABSENT_STATE,
    FsmOutcome,
    ORDER_STATES,
    validate_order_event,
)


STATUSES = (None, "planned", "assigned", "picked_up", "delivered",
            "returned_to_pool", "cancelled")
LIFECYCLE_EVENTS = (
    "NEW_ORDER",
    "COURIER_ASSIGNED",
    "COURIER_REJECTED_PROPOSAL",
    "COURIER_PICKED_UP",
    "COURIER_DELIVERED",
    "ORDER_RETURNED_TO_POOL",
)


def _current(status):
    if status is None:
        return None
    return {
        "order_id": "o1",
        "status": status,
        "restaurant": "R",
        "delivery_address": "D",
        "courier_id": "old-cid",
        "last_rejected_by": "old-cid",
        "picked_up_at": "2026-07-09T10:00:00+00:00",
        "delivered_at": "2026-07-09T11:00:00+00:00",
        "return_reason": "old_reason",
        "czas_kuriera_warsaw": "2026-07-09T14:00:00+02:00",
        "czas_kuriera_hhmm": "14:00",
        "pickup_at_warsaw": "2026-07-09T13:00:00+02:00",
    }


def _event(event_type, *, source=None, event_id=None, created_at=None):
    payload = {}
    courier_id = None
    if event_type == "NEW_ORDER":
        payload = {"restaurant": "R", "delivery_address": "D"}
    elif event_type == "COURIER_ASSIGNED":
        courier_id = "new-cid"
    elif event_type == "COURIER_REJECTED_PROPOSAL":
        courier_id = "new-cid"
    elif event_type == "COURIER_PICKED_UP":
        courier_id = "new-cid"
        payload = {"timestamp": "2026-07-09T10:30:00+00:00"}
    elif event_type == "COURIER_DELIVERED":
        payload = {"timestamp": "2026-07-09T11:30:00+00:00"}
    elif event_type == "ORDER_RETURNED_TO_POOL":
        payload = {"reason": "new_reason"}
    elif event_type == "CZAS_KURIERA_UPDATED":
        payload = {
            "new_ck_iso": "2026-07-09T14:30:00+02:00",
            "new_ck_hhmm": "14:30",
        }
    elif event_type == "PICKUP_TIME_UPDATED":
        payload = {"new_pickup_at_warsaw": "2026-07-09T13:30:00+02:00"}
    if source:
        payload["source"] = source
    event = {
        "event_type": event_type,
        "order_id": "o1",
        "payload": payload,
    }
    if courier_id is not None:
        event["courier_id"] = courier_id
    if event_id is not None:
        event["event_id"] = event_id
    if created_at is not None:
        event["created_at"] = created_at
    return event


# Base graph with no reconciliation/correction source.  Self-repetitions with a
# changed fact are intentionally illegal and tested as exact duplicates below.
BASE_ALLOWED = {
    (None, "NEW_ORDER"),
    ("planned", "NEW_ORDER"),
    ("planned", "COURIER_ASSIGNED"),
    ("assigned", "COURIER_ASSIGNED"),
    ("assigned", "COURIER_REJECTED_PROPOSAL"),
    ("assigned", "COURIER_PICKED_UP"),
    ("assigned", "ORDER_RETURNED_TO_POOL"),
    ("picked_up", "COURIER_DELIVERED"),
    ("picked_up", "ORDER_RETURNED_TO_POOL"),
    ("returned_to_pool", "COURIER_ASSIGNED"),
}


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("event_type", LIFECYCLE_EVENTS)
def test_full_base_transition_matrix(status, event_type):
    verdict = validate_order_event(_event(event_type), _current(status))
    expected = (status, event_type) in BASE_ALLOWED
    assert verdict.transition_allowed is expected, (
        status, event_type, verdict.outcome, verdict.issue_codes
    )
    if expected:
        assert verdict.outcome in {FsmOutcome.LEGAL, FsmOutcome.DUPLICATE}
    else:
        assert verdict.outcome is FsmOutcome.ILLEGAL
        assert "illegal_transition" in verdict.issue_codes


@pytest.mark.parametrize(
    "status,event_type",
    [
        (None, "COURIER_ASSIGNED"),
        (None, "COURIER_PICKED_UP"),
        (None, "COURIER_DELIVERED"),
        (None, "ORDER_RETURNED_TO_POOL"),
        ("planned", "COURIER_PICKED_UP"),
        ("planned", "COURIER_DELIVERED"),
        ("planned", "ORDER_RETURNED_TO_POOL"),
        ("assigned", "COURIER_DELIVERED"),
    ],
)
def test_reconcile_edges_require_explicit_source(status, event_type):
    without = validate_order_event(_event(event_type), _current(status))
    assert without.outcome is FsmOutcome.ILLEGAL
    assert "missing_reconcile_source" in without.issue_codes

    tagged = validate_order_event(
        _event(event_type, source="reconcile"), _current(status)
    )
    assert tagged.outcome is FsmOutcome.RECONCILE_EXCEPTION
    assert tagged.transition_allowed is True
    assert tagged.would_reject is False


@pytest.mark.parametrize(
    "source", [
        "panel",
        "panel_diff",
        "packs_ghost_detect",
        "reconcile",
        "ground_truth_fallback",
        "parcel_status_inbox",
    ],
)
def test_real_reconcile_provenance_tags_are_explicit(source):
    verdict = validate_order_event(
        _event("COURIER_DELIVERED", source=source), _current("assigned")
    )
    assert verdict.outcome is FsmOutcome.RECONCILE_EXCEPTION
    assert verdict.source == source


def test_deliv_source_alias_preserves_panel_provenance():
    event = _event("COURIER_DELIVERED")
    event["payload"]["deliv_source"] = "panel"
    verdict = validate_order_event(event, _current("assigned"))
    assert verdict.outcome is FsmOutcome.RECONCILE_EXCEPTION
    assert verdict.source == "panel"


@pytest.mark.parametrize(
    "source",
    ["handoff", "panel_diff", "panel_reassign", "packs_fallback", "cold_start_scan"],
)
def test_picked_up_handoff_is_explicit_correction_only(source):
    event = _event("COURIER_ASSIGNED", source=source)
    verdict = validate_order_event(event, _current("picked_up"))
    assert verdict.outcome is FsmOutcome.CORRECTION_EXCEPTION
    assert verdict.to_status == "picked_up"

    untagged = validate_order_event(_event("COURIER_ASSIGNED"), _current("picked_up"))
    assert untagged.outcome is FsmOutcome.ILLEGAL


@pytest.mark.parametrize("new_status", ["assigned", "picked_up"])
def test_delivered_resurrection_is_explicit_correction(new_status):
    event = {
        "event_type": "ORDER_RESURRECTED",
        "order_id": "o1",
        "courier_id": "c1",
        "payload": {
            "new_status": new_status,
            "reason": "panel_status_restored",
            "source": "panel_status_restored",
        },
    }
    verdict = validate_order_event(event, _current("delivered"))
    assert verdict.outcome is FsmOutcome.CORRECTION_EXCEPTION
    assert verdict.to_status == new_status
    assert verdict.would_reject is False


def test_resurrection_from_non_delivered_is_illegal():
    event = {
        "event_type": "ORDER_RESURRECTED",
        "order_id": "o1",
        "payload": {
            "new_status": "picked_up",
            "reason": "panel_status_restored",
            "source": "panel_status_restored",
        },
    }
    verdict = validate_order_event(event, _current("picked_up"))
    assert verdict.outcome is FsmOutcome.ILLEGAL


@pytest.mark.parametrize("status", sorted(ORDER_STATES))
@pytest.mark.parametrize(
    "event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"]
)
def test_data_only_events_preserve_every_existing_status(status, event_type):
    verdict = validate_order_event(_event(event_type), _current(status))
    assert verdict.outcome is FsmOutcome.DATA_ONLY
    assert verdict.to_status == status
    assert verdict.would_reject is False


@pytest.mark.parametrize(
    "event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"]
)
def test_data_only_event_cannot_create_order(event_type):
    verdict = validate_order_event(_event(event_type), None)
    assert verdict.outcome is FsmOutcome.ILLEGAL
    assert "data_event_without_order" in verdict.issue_codes


def test_exact_event_id_duplicate_wins_after_state_advanced():
    # A retried PICKED event can arrive after DELIVERED.  If its original ID is
    # already applied it is an exact no-op, not an illegal regression.
    event = _event("COURIER_PICKED_UP", event_id="pick-1")
    verdict = validate_order_event(
        event, _current("delivered"), seen_event_ids={"pick-1"}
    )
    assert verdict.outcome is FsmOutcome.DUPLICATE
    assert verdict.to_status == "delivered"
    assert verdict.would_reject is False


def test_duplicate_can_come_from_current_fsm_last_event_id():
    current = _current("delivered")
    current["fsm_last_event_id"] = "delivery-1"
    event = _event("COURIER_DELIVERED", event_id="delivery-1")
    verdict = validate_order_event(event, current)
    assert verdict.outcome is FsmOutcome.DUPLICATE


def test_exact_duplicate_beats_stale_ordering_and_old_payload_contract():
    current = _current("delivered")
    current["fsm_last_event_at"] = "2026-07-09T12:00:00+00:00"
    event = {
        "event_type": "COURIER_PICKED_UP",
        "order_id": "o1",
        "event_id": "old-pick",
        "created_at": "2026-07-09T10:00:00+00:00",
        "payload": {},  # historical producer omitted timestamp/courier
    }
    verdict = validate_order_event(event, current, seen_event_ids={"old-pick"})
    assert verdict.outcome is FsmOutcome.DUPLICATE
    assert verdict.to_status == "delivered"
    assert verdict.issue_codes == ()
    assert verdict.would_reject is False


def test_semantic_duplicate_delivery_is_noop():
    current = _current("delivered")
    event = _event("COURIER_DELIVERED")
    event["payload"]["timestamp"] = current["delivered_at"]
    verdict = validate_order_event(event, current)
    assert verdict.outcome is FsmOutcome.DUPLICATE


def test_semantic_duplicate_data_update_is_noop():
    current = _current("assigned")
    event = _event("CZAS_KURIERA_UPDATED")
    event["payload"].update({
        "new_ck_iso": current["czas_kuriera_warsaw"],
        "new_ck_hhmm": current["czas_kuriera_hhmm"],
    })
    verdict = validate_order_event(event, current)
    assert verdict.outcome is FsmOutcome.DUPLICATE


def test_missing_required_fields_are_reported_together():
    verdict = validate_order_event(
        {"event_type": "NEW_ORDER", "order_id": "o1", "payload": {}}, None
    )
    missing = [i.field for i in verdict.issues if i.code == "missing_required_field"]
    assert missing == ["payload.restaurant", "payload.delivery_address"]
    assert verdict.transition_allowed is True
    assert verdict.would_reject is True


@pytest.mark.parametrize(
    "event_type,missing_path",
    [
        ("COURIER_ASSIGNED", "courier_id"),
        ("COURIER_REJECTED_PROPOSAL", "courier_id"),
        ("COURIER_PICKED_UP", "courier_id"),
        ("COURIER_PICKED_UP", "payload.timestamp"),
        ("COURIER_DELIVERED", "payload.timestamp"),
        ("ORDER_RETURNED_TO_POOL", "payload.reason"),
        ("CZAS_KURIERA_UPDATED", "payload.new_ck_iso"),
        ("CZAS_KURIERA_UPDATED", "payload.new_ck_hhmm"),
        ("PICKUP_TIME_UPDATED", "payload.new_pickup_at_warsaw"),
    ],
)
def test_required_field_contract_for_every_external_state_event(
    event_type, missing_path
):
    event = _event(event_type)
    if missing_path.startswith("payload."):
        event["payload"].pop(missing_path.split(".", 1)[1])
    else:
        event.pop(missing_path)
    status = {
        "COURIER_ASSIGNED": "planned",
        "COURIER_REJECTED_PROPOSAL": "assigned",
        "COURIER_PICKED_UP": "assigned",
        "COURIER_DELIVERED": "picked_up",
        "ORDER_RETURNED_TO_POOL": "assigned",
        "CZAS_KURIERA_UPDATED": "assigned",
        "PICKUP_TIME_UPDATED": "assigned",
    }[event_type]
    verdict = validate_order_event(event, _current(status))
    assert any(
        issue.code == "missing_required_field" and issue.field == missing_path
        for issue in verdict.issues
    )
    assert verdict.would_reject is True


def test_non_mapping_payload_is_detected_without_mutating_input():
    event = {"event_type": "COURIER_ASSIGNED", "order_id": "o1",
             "courier_id": "c1", "payload": ["bad"]}
    before = copy.deepcopy(event)
    verdict = validate_order_event(event, _current("planned"))
    assert "payload_not_mapping" in verdict.issue_codes
    assert event == before


def test_bad_pickup_timestamp_is_detected_not_replaced():
    event = _event("COURIER_PICKED_UP")
    event["payload"]["timestamp"] = "not-a-timestamp"
    verdict = validate_order_event(event, _current("assigned"))
    assert verdict.transition_allowed is True
    assert "timestamp_unparseable" in verdict.issue_codes
    assert verdict.would_reject is True
    assert event["payload"]["timestamp"] == "not-a-timestamp"


def test_naive_panel_timestamp_is_parseable_but_warned():
    event = _event("COURIER_PICKED_UP")
    event["payload"]["timestamp"] = "2026-07-09 12:30:00"
    verdict = validate_order_event(event, _current("assigned"))
    assert verdict.outcome is FsmOutcome.LEGAL
    assert "timestamp_naive" in verdict.issue_codes
    assert verdict.would_reject is False


def test_delivery_before_pickup_is_detected():
    current = _current("picked_up")
    current["picked_up_at"] = "2026-07-09T12:00:00+00:00"
    event = _event("COURIER_DELIVERED")
    event["payload"]["timestamp"] = "2026-07-09T11:59:59+00:00"
    verdict = validate_order_event(event, current)
    assert "timestamp_before_pickup" in verdict.issue_codes
    assert verdict.would_reject is True


def test_czas_kuriera_iso_hhmm_mismatch_is_detected():
    event = _event("CZAS_KURIERA_UPDATED")
    event["payload"]["new_ck_hhmm"] = "15:30"
    verdict = validate_order_event(event, _current("assigned"))
    assert "timestamp_hhmm_mismatch" in verdict.issue_codes
    assert verdict.would_reject is True


@pytest.mark.parametrize(
    "event_type,path",
    [
        ("CZAS_KURIERA_UPDATED", "new_ck_iso"),
        ("PICKUP_TIME_UPDATED", "new_pickup_at_warsaw"),
    ],
)
def test_bad_data_event_timestamp_is_detected(event_type, path):
    event = _event(event_type)
    event["payload"][path] = "broken"
    verdict = validate_order_event(event, _current("assigned"))
    assert "timestamp_unparseable" in verdict.issue_codes
    assert verdict.would_reject is True


def test_stale_retry_event_is_detected_from_original_created_at():
    current = _current("assigned")
    current["fsm_last_event_at"] = "2026-07-09T12:00:00+00:00"
    event = _event(
        "COURIER_PICKED_UP", created_at="2026-07-09T11:59:59+00:00"
    )
    verdict = validate_order_event(event, current)
    assert "stale_event" in verdict.issue_codes
    assert verdict.would_reject is True


def test_non_state_global_event_needs_no_order_id():
    verdict = validate_order_event(
        {"event_type": "PANEL_UNREACHABLE", "payload": {"fail_count": 3}}, None
    )
    assert verdict.outcome is FsmOutcome.NON_STATE
    assert verdict.would_reject is False


def test_unknown_event_is_reported():
    verdict = validate_order_event(
        {"event_type": "MADE_UP", "order_id": "o1", "payload": {}},
        _current("planned"),
    )
    assert verdict.outcome is FsmOutcome.ILLEGAL
    assert "unsupported_order_event" in verdict.issue_codes


class _FakeLogger:
    def __init__(self):
        self.records = []

    def _add(self, level, message):
        self.records.append((level, message))

    def debug(self, message):
        self._add("debug", message)

    def info(self, message):
        self._add("info", message)

    def warning(self, message):
        self._add("warning", message)

    def error(self, message):
        self._add("error", message)


@pytest.fixture
def isolated_state_machine(monkeypatch, tmp_path):
    from dispatch_v2 import state_machine as sm

    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    (tmp_path / "orders_state.json").write_text("{}", encoding="utf-8")
    logger = _FakeLogger()
    monkeypatch.setattr(sm, "_log", logger)
    monkeypatch.setattr(sm, "decision_flag", lambda *a, **k: False)
    monkeypatch.setattr(sm, "flag", lambda name, default=False: default)
    return sm, logger


def test_phase_a_observer_logs_but_never_blocks_legacy(isolated_state_machine):
    sm, logger = isolated_state_machine
    assert sm.ORDER_FSM_ENFORCEMENT_ENABLED is False

    bad = {
        "event_type": "COURIER_DELIVERED",
        "order_id": "ghost",
        "payload": {"timestamp": "broken"},
    }
    # Formal verdict: illegal absent->delivered + malformed timestamp.  Legacy
    # behavior remains untouched in Phase A and still writes delivered.
    result = sm.update_from_event(bad)
    assert result["status"] == "delivered"
    assert result["delivered_at"] == "broken"
    warnings = [m for level, m in logger.records if level == "warning"]
    assert any("ORDER_FSM_OBSERVER" in m and "would_reject=1" in m for m in warnings)


def test_observer_failure_is_fail_open(isolated_state_machine, monkeypatch):
    sm, logger = isolated_state_machine

    def boom(*_a, **_k):
        raise RuntimeError("validator-test-boom")

    monkeypatch.setattr(sm, "validate_order_event", boom)
    event = {
        "event_type": "NEW_ORDER",
        "order_id": "o-fail-open",
        "payload": {"restaurant": "R", "delivery_address": "D"},
    }
    result = sm.update_from_event(event)
    assert result["status"] == "planned"
    assert any("ORDER_FSM_OBSERVER_FAIL" in m for _level, m in logger.records)


def test_resurrection_is_observed_as_correction(isolated_state_machine):
    sm, logger = isolated_state_machine
    sm.upsert_order(
        "res-1",
        {"status": "delivered", "courier_id": "c1",
         "delivered_at": "2026-07-09T11:00:00+00:00"},
        event="TEST_INIT",
    )
    out = sm.update_from_event({
        "event_type": "ORDER_RESURRECTED",
        "event_id": "res-1_ORDER_RESURRECTED_picked_up_test",
        "order_id": "res-1",
        "courier_id": "c1",
        "payload": {
            "new_status": "picked_up",
            "reason": "panel_status_restored",
            "source": "panel_status_restored",
        },
    })
    assert out["status"] == "picked_up"
    assert any(
        level == "info" and "outcome=correction_exception" in message
        for level, message in logger.records
    )
