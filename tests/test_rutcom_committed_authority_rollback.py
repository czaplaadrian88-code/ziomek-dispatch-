"""Mechanical code-rollback gate for committed pickup authority."""

from types import SimpleNamespace

import pytest

from dispatch_v2.committed_pickup_authority import (
    is_committed_pickup_artifact,
    is_committed_pickup_outbox_artifact,
)
from dispatch_v2.tools import rutcom_committed_authority_rollback as rollback


def _queue_status(**overrides):
    status = {
        "records": 0,
        "legacy_records": 0,
        "pending_v4_records": 0,
        "claimed_records": 0,
        "successor_records": 0,
        "invalid_records": 0,
        "safe_queue_projection": True,
        "blockers": [],
        "rollback_fence_present": False,
        "rollback_prepared": False,
    }
    status.update(overrides)
    return status


def _authority_event():
    return {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "payload": {
            "source": "rutcom_pickup_field",
            "observed_source": "coordinator_force",
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        },
    }


def test_rollback_classifier_keeps_stripped_coordinator_event_reserved():
    assert is_committed_pickup_artifact({}) is True
    assert is_committed_pickup_artifact(None) is True
    assert is_committed_pickup_artifact("corrupt-state-event") is True
    assert is_committed_pickup_artifact(_authority_event()) is True
    assert is_committed_pickup_artifact(
        {
            "event_type": "PICKUP_TIME_UPDATED",
            "payload": {
                "source": "panel_re_check",
                "new_pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
            },
        }
    ) is False
    assert is_committed_pickup_artifact(
        {
            "event_type": "CZAS_KURIERA_UPDATED",
            "payload": {
                "source": "panel_re_check",
                "committed_authority": "rutcom_forward_commitment",
            },
        }
    ) is True
    assert is_committed_pickup_artifact(
        {
            "event_type": "PICKUP_TIME_UPDATED",
            "payload": {
                "source": "rutcom_forward_commitment",
                "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            },
        }
    ) is True
    assert is_committed_pickup_artifact(
        {
            "event_type": "CZAS_KURIERA_UPDATED",
            "payload": {
                "source": "panel_re_check",
                "new_zmiana_czasu_odbioru": True,
            },
        }
    ) is True
    assert is_committed_pickup_artifact(
        {
            "event_type": "PICKUP_TIME_UPDATED",
            "committed_authority_attestation": {
                "schema": "committed_pickup_outbox_attestation.v1",
            },
            "payload": None,
        }
    ) is True
    assert is_committed_pickup_artifact(
        {
            "event_type": "UNKNOWN_AFTER_PARTIAL_REVERT",
            "committed_authority_attestation": {
                "schema": "committed_pickup_outbox_attestation.v1",
            },
            "payload": None,
        }
    ) is True


def _outbox_row(state_event, **overrides):
    row = {
        "event_id": "legacy-pickup-event",
        "event_key": "legacy-pickup-key",
        "order_id": "491578",
        "state_event": state_event,
    }
    row.update(overrides)
    return row


def test_rollback_row_classifier_fails_closed_on_partial_corruption():
    assert is_committed_pickup_outbox_artifact({}) is True
    assert is_committed_pickup_outbox_artifact(
        _outbox_row({})
    ) is True
    assert is_committed_pickup_outbox_artifact(
        _outbox_row(
            {
                "event_type": "PICKUP_TIME_UPDATED",
                "event_id": "different-event",
                "order_id": "491578",
                "payload": {"source": "panel_re_check"},
            }
        )
    ) is True


def test_rollback_row_identity_reserves_stripped_committed_event():
    marker = "491578_PICKUP_TIME_UPDATED_COMMITTED_deadbeef"
    stripped = {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": "legacy-pickup-event",
        "order_id": "491578",
        "payload": {
            "source": "panel_re_check",
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        },
    }

    assert is_committed_pickup_outbox_artifact(
        _outbox_row(stripped, event_key=marker)
    ) is True


def test_rollback_row_classifier_keeps_valid_legacy_pickup_unblocked():
    legacy = {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": "legacy-pickup-event",
        "order_id": "491578",
        "payload": {
            "source": "panel_re_check",
            "new_pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
        },
    }

    assert is_committed_pickup_outbox_artifact(_outbox_row(legacy)) is False


def test_code_revert_intentionally_blocks_even_valid_legacy_raw_ck():
    """Pre-v4 nie odróżni raw CK czasówki od elastyka; hot-OFF jest rollbackiem."""
    legacy_raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": "legacy-raw-ck",
        "order_id": "491578",
        "payload": {
            "source": "panel_re_check",
            "new_ck_iso": "2026-08-01T19:21:00+02:00",
            "new_ck_hhmm": "19:21",
        },
    }

    assert is_committed_pickup_artifact(legacy_raw_ck) is True
    assert is_committed_pickup_outbox_artifact(
        _outbox_row(
            legacy_raw_ck,
            event_id="legacy-raw-ck",
            event_key="legacy-raw-ck-key",
        )
    ) is True


def test_status_blocks_any_unfinished_authority_outbox(monkeypatch):
    rows = [
        {
            "event_id": f"legacy-{index}",
            "event_key": f"legacy-key-{index}",
            "order_id": f"legacy-order-{index}",
            "state_event": {
                "event_id": f"legacy-{index}",
                "event_type": "COURIER_ASSIGNED",
                "order_id": f"legacy-order-{index}",
                "payload": {},
            },
        }
        for index in range(105)
    ]
    rows.append(
        {
            "event_id": "authority-after-105",
            "event_key": "authority-after-105",
            "order_id": "491578",
            "state_event": {
                **_authority_event(),
                "event_id": "authority-after-105",
            },
        }
    )
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: rows,
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )

    status = rollback.collect_status()

    assert status["unfinished_outbox_total"] == 106
    assert status["unfinished_authority_outbox"] == 1
    assert status["unfinished_authority_event_ids"] == [
        "authority-after-105"
    ]
    assert status["safe_to_prepare"] is False
    assert status["safe_for_code_revert"] is False


def test_forward_deploy_blocks_unfinished_first_acceptance_ck_outbox(
    monkeypatch,
):
    """Flip nie może zmienić terminalności już utrwalonego raw CK eventu."""
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": "predeploy-first-acceptance-491578",
        "order_id": "491578",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": None,
            "source": "first_acceptance",
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                raw_ck,
                event_id="predeploy-first-acceptance-491578",
                event_key="predeploy-first-acceptance-491578-key",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {
            "491578": {
                "order_id": "491578",
                "status": "assigned",
                "courier_id": "492",
                "order_type": "czasowka",
                "pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
                "czas_kuriera_warsaw": "2026-08-01T19:18:00+02:00",
                "czas_kuriera_hhmm": "19:18",
            }
        },
    )

    status = rollback.collect_status()

    assert status["unfinished_authority_outbox"] == 1
    assert status["active_incomplete_time_contract_count"] == 0
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_does_not_block_unrelated_unfinished_outbox(
    monkeypatch,
):
    """Release gate jest wąski: zwykły NEW_ORDER nie zmienia semantyki flagi."""
    unrelated = {
        "event_type": "NEW_ORDER",
        "event_id": "unrelated-new-order",
        "order_id": "900001",
        "payload": {"source": "panel"},
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                unrelated,
                event_id="unrelated-new-order",
                event_key="unrelated-new-order-key",
                order_id="900001",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status()

    assert status["unfinished_outbox_total"] == 1
    assert status["unfinished_authority_outbox"] == 0
    assert status["safe_for_forward_deploy"] is True


def test_forward_deploy_blocks_unbound_czasowka_new_order(monkeypatch):
    """A pre-v19 NEW_ORDER may persist a split tuple after the hot flip."""
    new_order = {
        "event_type": "NEW_ORDER",
        "event_id": "time-new-order",
        "order_id": "491578",
        "payload": {
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
            "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
            "czas_kuriera_hhmm": "14:05",
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                new_order,
                event_id="time-new-order",
                event_key="time-new-order-key",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status()
    assert status["unfinished_unbound_new_order_time_outbox"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_pending_sanitized_czasowka_new_order(
    monkeypatch,
):
    """A receipt-bound NEW_ORDER can still create a pending aggregate shell."""
    new_order = {
        "event_type": "NEW_ORDER",
        "event_id": "pending-sanitized-new-order",
        "order_id": "time-order-pending-shell",
        "czasowka_new_order_time_authority_enabled": True,
        "payload": {
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                new_order,
                event_id="pending-sanitized-new-order",
                event_key="pending-sanitized-new-order-key",
                order_id="time-order-pending-shell",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status()

    assert status["unfinished_unbound_new_order_time_outbox"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_pending_legacy_pickup_for_czasowka(
    monkeypatch,
):
    """Every pending time writer for a czasowka must drain before the flip."""
    order_id = "time-order-pending-pickup"
    pickup = {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": "legacy-pickup-time-order",
        "order_id": order_id,
        "payload": {
            "source": "panel_re_check",
            "old_pickup_at_warsaw": "2026-08-02T19:16:00+02:00",
            "new_pickup_at_warsaw": "2026-08-02T19:18:00+02:00",
            "pickup_time_revision_at_observation": 0,
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                pickup,
                event_id="legacy-pickup-time-order",
                event_key="legacy-pickup-time-order-key",
                order_id=order_id,
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {
            order_id: {
                "order_id": order_id,
                "status": "planned",
                "order_type": "czasowka",
                "prep_minutes": 60,
                "pickup_at_warsaw": "2026-08-02T19:16:00+02:00",
                "czas_kuriera_warsaw": "2026-08-02T19:16:00+02:00",
                "czas_kuriera_hhmm": "19:16",
            }
        },
    )

    status = rollback.collect_status()

    assert status["unfinished_forward_authority_outbox"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_unassigned_legacy_czasowka_missing_contract(
    monkeypatch,
):
    """Kanon prep>=60 obowiązuje preflight także bez order_type/kuriera."""
    legacy_planned = {
        "order_id": "legacy-planned-time-order",
        "status": "planned",
        "prep_minutes": 60,
        "courier_id": None,
        "pickup_at_warsaw": None,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {legacy_planned["order_id"]: legacy_planned},
    )

    status = rollback.collect_status()

    assert rollback.C.is_czasowka_order(legacy_planned) is True
    assert status["active_incomplete_time_contract_count"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_split_active_time_contract(monkeypatch):
    """Three populated fields are unsafe when they encode two truths."""
    split = {
        "order_id": "491578",
        "status": "planned",
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
        "czas_kuriera_hhmm": "14:05",
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {split["order_id"]: split},
    )

    assert rollback._active_time_contract_incomplete(split) is True
    status = rollback.collect_status()
    assert status["active_incomplete_time_contract_count"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_malformed_active_time_contract():
    malformed = {
        "order_id": "491578",
        "status": "assigned",
        "courier_id": "492",
        "order_type": "czasowka",
        "pickup_at_warsaw": "not-an-iso",
        "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
        "czas_kuriera_hhmm": "19:99",
    }

    assert rollback._active_time_contract_incomplete(malformed) is True


def test_forward_deploy_ignores_well_formed_elastic_raw_ck(monkeypatch):
    """Forward flag cannot change an explicitly elastic raw CK receipt."""
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": "elastic-raw-ck",
        "order_id": "elastic-1",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-02T14:00:00+02:00",
            "old_ck_hhmm": "14:00",
            "new_ck_iso": "2026-08-02T14:05:00+02:00",
            "new_ck_hhmm": "14:05",
            "delta_min": 5.0,
            "source": "panel_re_check",
        },
    }
    row = _outbox_row(
        raw_ck,
        event_id="elastic-raw-ck",
        event_key="elastic-raw-ck-key",
        order_id="elastic-1",
    )
    elastic = {
        "order_id": "elastic-1",
        "status": "assigned",
        "order_type": "elastic",
        "courier_id": "492",
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_hhmm": "14:00",
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [row],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {elastic["order_id"]: elastic},
    )

    status = rollback.collect_status()
    # Code revert remains deliberately conservative for every raw CK row.
    assert status["unfinished_authority_outbox"] == 1
    assert status["safe_for_code_revert"] is False
    # Forward rollout changes only czasowka semantics, not this exact receipt.
    assert status["unfinished_forward_authority_outbox"] == 0
    assert status["safe_for_forward_deploy"] is True


def test_forward_deploy_blocks_unfinished_pre_v4_coordinator_time_event(
    monkeypatch,
):
    pre_v4 = {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": "pre-v4-coordinator-pickup",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:16:00+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "source": "coordinator_force",
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                pre_v4,
                event_id="pre-v4-coordinator-pickup",
                event_key="pre-v4-coordinator-pickup-key",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {
            "491578": {
                "order_id": "491578",
                "status": "assigned",
                "order_type": "czasowka",
            }
        },
    )

    status = rollback.collect_status()

    assert status["unfinished_pre_v4_coordinator_time_outbox"] == 1
    assert status["unfinished_pre_v4_coordinator_time_event_ids"] == [
        "pre-v4-coordinator-pickup"
    ]
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_requires_dark_flag_empty_queue_and_no_old_events(
    monkeypatch,
):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status()

    assert status["safe_for_forward_deploy"] is True


@pytest.mark.parametrize(
    "active_order",
    [
        {
            "order_id": "491578",
            "status": "assigned",
            "courier_id": "492",
            "order_type": "czasowka",
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        {
            "order_id": "491578",
            "status": "assigned",
            "courier_id": "492",
        },
    ],
)
def test_forward_deploy_blocks_incomplete_active_time_contract(
    monkeypatch, active_order
):
    """ON nie może wejść nad rekordem z dawnego thin cold-start."""
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {"491578": dict(active_order)},
    )

    status = rollback.collect_status()

    assert status["active_incomplete_time_contract_count"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_unfinished_pre_v16_assignment_ck_policy(
    monkeypatch,
):
    """Hot flip nie może zmienić znaczenia już utrwalonego assignmentu."""
    legacy_assignment = {
        "event_type": "COURIER_ASSIGNED",
        "event_id": "pre-v16-assignment-491578",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "source": "panel_initial",
            "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
            "czas_kuriera_hhmm": "19:16",
        },
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [
            _outbox_row(
                legacy_assignment,
                event_id="pre-v16-assignment-491578",
                event_key="pre-v16-assignment-491578-key",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {
            "491578": {
                "order_id": "491578",
                "status": "planned",
                "order_type": "czasowka",
                "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
                "czas_kuriera_warsaw": None,
                "czas_kuriera_hhmm": None,
            }
        },
    )

    status = rollback.collect_status()

    assert status["unfinished_pre_v16_assignment_ck_outbox"] == 1
    assert status["unfinished_pre_v16_assignment_ck_event_ids"] == [
        "pre-v16-assignment-491578"
    ]
    assert status["safe_for_forward_deploy"] is False


@pytest.mark.parametrize(
    ("event", "order"),
    [
        (
            {
                "event_type": "COURIER_ASSIGNED",
                "order_id": "491578",
                "payload": {
                    "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
                    "czas_kuriera_hhmm": "19:16",
                },
                "czasowka_assignment_ck_forward_authority_enabled": False,
                "czasowka_assignment_ck_passive_guard_enabled": True,
            },
            {"order_type": "czasowka"},
        ),
        (
            {
                "event_type": "COURIER_ASSIGNED",
                "order_id": "491578",
                "payload": {
                    "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
                    "czas_kuriera_hhmm": "19:16",
                },
                "czasowka_assignment_ck_forward_authority_enabled": False,
            },
            {"order_type": "czasowka"},
        ),
        (
            {
                "event_type": "COURIER_ASSIGNED",
                "order_id": "491578",
                "payload": {"source": "panel_initial"},
            },
            {"order_type": "czasowka"},
        ),
        (
            {
                "event_type": "COURIER_ASSIGNED",
                "order_id": "491578",
                "payload": {
                    "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
                    "czas_kuriera_hhmm": "19:16",
                },
            },
            {"order_type": "elastic"},
        ),
    ],
)
def test_forward_assignment_gate_does_not_block_semantically_stable_rows(
    event, order
):
    row = _outbox_row(event, order_id="491578")

    assert rollback._pre_v16_assignment_ck_row_blocks_forward(
        row, {"491578": order}
    ) is False


@pytest.mark.parametrize(
    "order",
    [
        {"status": "assigned", "order_type": "elastic", "courier_id": "1"},
        {"status": "assigned", "order_type": "parcel", "courier_id": "1"},
        {
            "status": "delivered",
            "order_type": "czasowka",
            "courier_id": "1",
        },
        {
            "status": "assigned",
            "order_type": "czasowka",
            "courier_id": "1",
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "czas_kuriera_warsaw": "2026-08-01T19:15:58+02:00",
            "czas_kuriera_hhmm": "19:15",
        },
    ],
)
def test_forward_state_gate_does_not_block_complete_or_out_of_scope_rows(order):
    assert rollback._active_time_contract_incomplete(order) is False


@pytest.mark.parametrize(
    ("flag_enabled", "queue_records"),
    [(True, 0), (False, 1)],
)
def test_forward_deploy_blocks_live_flag_or_nonempty_queue(
    monkeypatch, flag_enabled, queue_records
):
    monkeypatch.setattr(
        rollback.C,
        "decision_flag",
        lambda name: bool(name == rollback.FLAG and flag_enabled),
    )
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(records=queue_records),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    assert rollback.collect_status()["safe_for_forward_deploy"] is False


def test_code_revert_requires_off_terminal_outbox_fence_and_legacy_queue(
    monkeypatch,
):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(
            rollback_fence_present=True,
            rollback_prepared=True,
        ),
    )

    status = rollback.collect_status()

    assert status["safe_to_prepare"] is False
    assert status["safe_for_code_revert"] is True


def test_code_revert_blocks_active_persisted_authority_even_when_flags_off(
    monkeypatch,
):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(
            rollback_fence_present=True,
            rollback_prepared=True,
        ),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {
            "491578": {
                "order_id": "491578",
                "status": "assigned",
                "committed_pickup_authority": "rutcom_forward_commitment",
            }
        },
    )

    status = rollback.collect_status()

    assert status["active_committed_state_count"] == 1
    assert status["safe_for_code_revert"] is False


@pytest.mark.parametrize(
    "state_result",
    [RuntimeError("state unreadable"), {"491578": "corrupt-order"}],
)
def test_code_revert_fails_closed_when_state_scan_is_not_authoritative(
    monkeypatch, state_result
):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(
            rollback_fence_present=True,
            rollback_prepared=True,
        ),
    )

    def state_read():
        if isinstance(state_result, Exception):
            raise state_result
        return state_result

    monkeypatch.setattr(rollback.state_machine, "get_all_strict", state_read)

    status = rollback.collect_status()

    assert status["state_scan_ok"] is False
    assert status["safe_for_code_revert"] is False


def test_code_revert_blocks_when_manual_authority_writer_remains_on(
    monkeypatch,
):
    monkeypatch.setattr(
        rollback.C,
        "decision_flag",
        lambda name: name == "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
    )
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(
            rollback_fence_present=True,
            rollback_prepared=True,
        ),
    )

    status = rollback.collect_status()

    assert rollback.AUTHORITY_FLAGS == (
        "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
        "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
    )
    assert status["enabled_authority_flags"] == [
        "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
    ]
    assert status["safe_for_code_revert"] is False


def test_release_fence_refuses_when_manual_authority_writer_remains_on(
    monkeypatch,
):
    before = {
        "flag_enabled": False,
        "enabled_authority_flags": [
            "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
        ],
        "unfinished_authority_outbox": 0,
        "queue": {"safe_queue_projection": True},
    }
    monkeypatch.setattr(rollback, "collect_status", lambda: before)
    monkeypatch.setattr(
        rollback.queue,
        "release_legacy_rollback_fence",
        lambda: (_ for _ in ()).throw(
            AssertionError("manual authority writer must keep fence closed")
        ),
    )

    assert rollback._cmd_release_fence(
        SimpleNamespace(apply=True, v4_code_active=True)
    ) == 2


def test_fence_without_validated_backup_never_authorizes_code_revert(
    monkeypatch,
):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "legacy_rollback_status",
        lambda: _queue_status(
            rollback_fence_present=True,
            rollback_prepared=False,
        ),
    )

    status = rollback.collect_status()

    assert status["safe_to_prepare"] is False
    assert status["safe_for_code_revert"] is False


def test_prepare_cli_requires_explicit_apply_and_quiesced(monkeypatch, capsys):
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda: {
            "safe_to_prepare": True,
            "safe_for_code_revert": False,
        },
    )
    monkeypatch.setattr(
        rollback.queue,
        "prepare_legacy_rollback",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("must not mutate without both acknowledgements")
        ),
    )

    exit_code = rollback.main(
        [
            "prepare",
            "--queue-backup",
            "/root/worktrees/not-written.json",
        ]
    )

    assert exit_code == 4
    assert "requires both --apply and --quiesced" in capsys.readouterr().out
