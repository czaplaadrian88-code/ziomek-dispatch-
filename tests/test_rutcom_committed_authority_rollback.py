"""Mechanical forward-rollout and hot-OFF gate for pickup authority."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from dispatch_v2.committed_pickup_authority import (
    is_committed_pickup_artifact,
    is_committed_pickup_outbox_artifact,
)
from dispatch_v2.tools import rutcom_committed_authority_rollback as rollback


@pytest.fixture(autouse=True)
def _isolate_read_only_queue_snapshot(monkeypatch):
    """Every collect_status test owns its exact, non-production queue view."""
    monkeypatch.setattr(
        rollback.queue,
        "queue_records_snapshot",
        lambda: {},
    )
    monkeypatch.setattr(
        rollback.queue,
        "forward_rollout_fence_status",
        lambda: _forward_fence_status(),
    )


def _queue_status(**overrides):
    status = {
        "records": 0,
        "legacy_records": 0,
        "pending_pre_policy_records": 0,
        "claimed_records": 0,
        "successor_records": 0,
        "invalid_records": 0,
        "safe_empty_queue": True,
        "blockers": [],
    }
    status.update(overrides)
    return status


def _rollforward_code_manifest(*, salt: str = "v28") -> dict:
    files = {
        path: hashlib.sha256(f"{salt}:{path}".encode("utf-8")).hexdigest()
        for path in rollback.queue.ROLLFORWARD_CODE_PATHS
    }
    body = {
        "schema": rollback.queue.ROLLFORWARD_CODE_MANIFEST_SCHEMA,
        "files": files,
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**body, "manifest_sha256": manifest_sha256}


def _forward_fence_status(*, manifest=None, **overrides):
    manifest = manifest or _rollforward_code_manifest()
    status = {
        "forward_fence_present": True,
        "forward_fence_valid": True,
        "forward_fence_error": None,
        "forward_fence_id": "00000000-0000-4000-8000-000000000001",
        "forward_fence_queue_sha256": "0" * 64,
        "forward_fence_code_manifest": manifest,
        "forward_fence_code_manifest_sha256": manifest["manifest_sha256"],
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )

    status = rollback.collect_status()

    assert status["unfinished_outbox_total"] == 106
    assert status["unfinished_authority_outbox"] == 1
    assert status["unfinished_authority_event_ids"] == [
        "authority-after-105"
    ]
    assert status["safe_for_forward_deploy"] is False
    assert status["behavioral_rollback"] == "hot_flag_off_only"
    assert "safe_for_code_revert" not in status


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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
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


def test_receipt_bound_new_order_blocks_forward_even_if_payload_is_elastic(
    monkeypatch,
):
    """Pre-v20 code cannot consume a top-level initial-time receipt safely."""
    event_id = "receipt-bound-new-order"
    new_order = {
        "event_type": "NEW_ORDER",
        "event_id": event_id,
        "order_id": "receipt-bound-order",
        "czasowka_new_order_time_authority_enabled": True,
        "pending_committed_time_intent": {"schema": "receipt-present"},
        "payload": {
            "order_type": "elastic",
            "prep_minutes": 15,
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
                event_id=event_id,
                event_key=f"{event_id}-key",
                order_id="receipt-bound-order",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
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


def test_forward_deploy_blocks_new_order_labeled_elastic_with_prep60(
    monkeypatch,
):
    """NEW_ORDER fencing uses the same canonical class as live producers."""
    event_id = "mislabelled-new-order"
    new_order = {
        "event_type": "NEW_ORDER",
        "event_id": event_id,
        "order_id": "mislabelled-new-order",
        "payload": {
            "order_type": "elastic",
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
                event_id=event_id,
                event_key=f"{event_id}-key",
                order_id="mislabelled-new-order",
            )
        ],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {elastic["order_id"]: elastic},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )
    # Klasyfikator nadal rezerwuje każdy raw CK dla diagnostyki.
    assert status["unfinished_authority_outbox"] == 1
    # Forward rollout changes only czasowka semantics, not this exact receipt.
    assert status["unfinished_forward_authority_outbox"] == 0
    assert status["safe_for_forward_deploy"] is True


def test_forward_deploy_blocks_pickup_that_promotes_elastic_to_prep60(
    monkeypatch,
):
    """Preflight classifies the durable post-event state, not stale state."""
    event_id = "elastic-pickup-promotes-prep60"
    pickup = {
        "event_type": "PICKUP_TIME_UPDATED",
        "event_id": event_id,
        "order_id": "elastic-promoted",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
            "new_pickup_at_warsaw": "2026-08-02T14:15:00+02:00",
            "old_prep_minutes": 20,
            "new_prep_minutes": 60,
            "source": "panel_re_check",
        },
    }
    row = _outbox_row(
        pickup,
        event_id=event_id,
        event_key=f"{event_id}-key",
        order_id="elastic-promoted",
    )
    elastic = {
        "order_id": "elastic-promoted",
        "status": "assigned",
        "order_type": "elastic",
        "prep_minutes": 20,
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {elastic["order_id"]: elastic},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

    assert status["unfinished_forward_authority_outbox"] == 1
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_blocks_explicit_elastic_with_canonical_prep60(
    monkeypatch,
):
    """The canonical prep classifier wins over a stale order_type label."""
    event_id = "mislabelled-czasowka-raw-ck"
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": event_id,
        "order_id": "mislabelled-czasowka",
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
        event_id=event_id,
        event_key=f"{event_id}-key",
        order_id="mislabelled-czasowka",
    )
    mislabeled = {
        "order_id": "mislabelled-czasowka",
        "status": "assigned",
        "order_type": "elastic",
        "prep_minutes": 60,
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {mislabeled["order_id"]: mislabeled},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

    assert status["unfinished_forward_authority_outbox"] == 1
    assert status["safe_for_forward_deploy"] is False


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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

    assert status["safe_for_forward_deploy"] is True


def test_forward_deploy_is_never_safe_without_verified_quiescence(
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
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status()

    assert status["writer_quiescence_verified"] is False
    assert status["safe_for_forward_deploy"] is False


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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
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
        "queue_compatibility_status",
        lambda: _queue_status(records=queue_records),
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    assert rollback.collect_status()["safe_for_forward_deploy"] is False


@pytest.mark.parametrize(
    "state_result",
    [RuntimeError("state unreadable"), {"491578": "corrupt-order"}],
)
def test_forward_rollout_fails_closed_when_state_scan_is_not_authoritative(
    monkeypatch, state_result
):
    manifest = _rollforward_code_manifest()
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(),
    )

    def state_read():
        if isinstance(state_result, Exception):
            raise state_result
        return state_result

    monkeypatch.setattr(rollback.state_machine, "get_all_strict", state_read)

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=manifest,
    )

    assert status["state_scan_ok"] is False
    assert status["forward_handoff_safe"] is False
    assert status["safe_for_forward_deploy"] is False


def test_forward_fence_rejects_deployed_manifest_drift(monkeypatch):
    expected = _rollforward_code_manifest(salt="expected")
    observed = _rollforward_code_manifest(salt="drifted")
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.queue,
        "forward_rollout_fence_status",
        lambda: _forward_fence_status(manifest=expected),
    )
    monkeypatch.setattr(rollback.state_machine, "get_all_strict", lambda: {})

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=observed,
    )

    assert status["forward_target_code_verified"] is False
    assert status["forward_handoff_safe"] is False
    assert status["safe_for_forward_deploy"] is False


def test_rollout_contract_has_one_behavioral_rollback_owner(monkeypatch):
    manifest = _rollforward_code_manifest()
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(rollback.state_machine, "get_all_strict", lambda: {})

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=manifest,
    )

    assert status["schema"] == "rutcom_committed_authority.rollout_preflight.v6"
    assert status["forward_handoff_safe"] is True
    assert status["safe_for_forward_deploy"] is True
    assert status["behavioral_rollback"] == "hot_flag_off_only"
    assert {
        "safe_to_prepare",
        "safe_for_code_revert",
        "rollback_target_code_verified",
    }.isdisjoint(status)
    assert not hasattr(rollback.queue, "prepare_legacy_rollback")
    assert not hasattr(rollback.queue, "release_legacy_rollback_fence")


def test_deployed_rollforward_manifest_hashes_every_exact_authority_file(
    tmp_path, monkeypatch
):
    assert rollback.queue.ROLLFORWARD_CODE_PATHS == (
        "committed_pickup_apply.py",
        "committed_pickup_authority.py",
        "common.py",
        "coordinator_time_recheck.py",
        "dispatch_pipeline.py",
        "durable_event_apply.py",
        "event_bus.py",
        "panel_client.py",
        "panel_watcher.py",
        "shadow_dispatcher.py",
        "state_machine.py",
        "tools/rutcom_committed_authority_rollback.py",
    )
    deploy_root = tmp_path / "deployed-dispatch-v2"
    for relative_path in rollback.queue.ROLLFORWARD_CODE_PATHS:
        target = deploy_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"first:{relative_path}\n".encode("utf-8"))
    monkeypatch.setattr(rollback, "DEPLOYED_DISPATCH_ROOT", deploy_root)
    monkeypatch.setattr(rollback, "_PACKAGE_ROOT", str(deploy_root))

    first = rollback._deployed_rollforward_code_manifest()
    second = rollback._deployed_rollforward_code_manifest()

    assert first == second
    assert set(first["files"]) == set(
        rollback.queue.ROLLFORWARD_CODE_PATHS
    )
    assert first["manifest_sha256"]

    changed_path = deploy_root / "coordinator_time_recheck.py"
    changed_path.write_bytes(b"second\n")
    changed = rollback._deployed_rollforward_code_manifest()
    assert changed["manifest_sha256"] != first["manifest_sha256"]
    assert changed["files"]["coordinator_time_recheck.py"] != (
        first["files"]["coordinator_time_recheck.py"]
    )


def test_deployed_manifest_rejects_noncanonical_tool_copy(
    tmp_path, monkeypatch
):
    deploy_root = tmp_path / "deployed-dispatch-v2"
    runtime_root = tmp_path / "staging-dispatch-v2"
    deploy_root.mkdir()
    runtime_root.mkdir()
    monkeypatch.setattr(rollback, "DEPLOYED_DISPATCH_ROOT", deploy_root)
    monkeypatch.setattr(rollback, "_PACKAGE_ROOT", str(runtime_root))

    with pytest.raises(RuntimeError, match="canonical deployed root"):
        rollback._deployed_rollforward_code_manifest()


@pytest.mark.parametrize("command", ["status", "prepare", "release-fence"])
def test_cli_surface_rejects_generic_code_revert_commands(command):
    with pytest.raises(SystemExit):
        rollback._parser().parse_args([command])


def test_release_forward_fence_refuses_red_post_on_handoff(
    monkeypatch, capsys
):
    manifest = _rollforward_code_manifest()
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: (True, {"writers": "inactive"}),
    )
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: True)
    monkeypatch.setattr(
        rollback,
        "_deployed_rollforward_code_manifest",
        lambda: manifest,
    )
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda **_kwargs: {
            "forward_handoff_safe": False,
            "forward_target_code_verified": True,
        },
    )
    monkeypatch.setattr(
        rollback.queue,
        "release_forward_rollout_fence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("red post-ON handoff must keep the fence")
        ),
    )

    result = rollback._cmd_release_forward_fence(
        SimpleNamespace(
            apply=True,
            quiesced=True,
            authority_active=True,
            abort_off=False,
            fence_id="00000000-0000-4000-8000-000000000028",
        )
    )

    assert result == 2
    assert '"released": false' in capsys.readouterr().out


def test_release_forward_fence_binds_id_and_live_manifest_supplier(
    monkeypatch, capsys
):
    fence_id = "00000000-0000-4000-8000-000000000028"
    manifest = _rollforward_code_manifest()
    probes = iter(
        [
            (True, {"phase": "before"}),
            (True, {"phase": "after"}),
        ]
    )
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: next(probes),
    )
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: True)
    monkeypatch.setattr(
        rollback,
        "_deployed_rollforward_code_manifest",
        lambda: manifest,
    )
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda **_kwargs: {
            "forward_handoff_safe": True,
            "forward_target_code_verified": True,
        },
    )
    released_with = []

    guards = []

    def release(received_id, manifest_supplier, quiescence_supplier):
        guards.append(quiescence_supplier)
        released_with.append((received_id, manifest_supplier()))
        return True

    monkeypatch.setattr(
        rollback.queue,
        "release_forward_rollout_fence",
        release,
    )
    monkeypatch.setattr(
        rollback.queue,
        "forward_rollout_fence_status",
        lambda: {
            "forward_fence_present": False,
            "forward_fence_valid": False,
        },
    )

    result = rollback._cmd_release_forward_fence(
        SimpleNamespace(
            apply=True,
            quiesced=True,
            authority_active=True,
            abort_off=False,
            fence_id=fence_id,
        )
    )

    assert result == 0
    assert released_with == [(fence_id, manifest)]
    assert len(guards) == 1 and callable(guards[0])
    assert '"released": true' in capsys.readouterr().out


def test_abort_off_release_requires_exact_fenced_manifest(monkeypatch):
    manifest = _rollforward_code_manifest()
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: (True, {"writers": "inactive"}),
    )
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback,
        "_deployed_rollforward_code_manifest",
        lambda: manifest,
    )
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda **_kwargs: {
            "forward_handoff_safe": False,
            "forward_target_code_verified": False,
        },
    )
    monkeypatch.setattr(
        rollback.queue,
        "release_forward_rollout_fence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("manifest mismatch must keep the fence")
        ),
    )

    result = rollback._cmd_release_forward_fence(
        SimpleNamespace(
            apply=True,
            quiesced=True,
            authority_active=False,
            abort_off=True,
            fence_id="00000000-0000-4000-8000-000000000028",
        )
    )

    assert result == 2


def test_forward_status_requires_explicit_quiescence_ack(monkeypatch, capsys):
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda **_kwargs: {"safe_for_forward_deploy": True},
    )

    assert rollback.main(["forward-status"]) == 4
    assert "forward-status requires --quiesced" in capsys.readouterr().out


def test_forward_status_mechanically_rejects_active_writer(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        rollback,
        "_deployed_rollforward_code_manifest",
        lambda: _rollforward_code_manifest(),
    )
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: (
            False,
            {
                "dispatch-panel-watcher.service": {
                    "load_state": "loaded",
                    "active_state": "active",
                },
                "dispatch-shadow.service": {
                    "load_state": "loaded",
                    "active_state": "inactive",
                },
            },
        ),
        raising=False,
    )

    def status(
        *,
        writer_quiescence_verified=False,
        writer_states=None,
        deployed_code_manifest=None,
    ):
        return {
            "writer_quiescence_verified": writer_quiescence_verified,
            "writer_states": writer_states,
            "safe_for_forward_deploy": writer_quiescence_verified,
        }

    monkeypatch.setattr(rollback, "collect_status", status)

    assert rollback.main(["forward-status", "--quiesced"]) == 1
    output = capsys.readouterr().out
    assert '"writer_quiescence_verified": false' in output
    assert '"active_state": "active"' in output


@pytest.mark.parametrize(
    ("shadow_active_state", "expected"),
    [("inactive", True), ("active", False)],
)
def test_forward_writer_probe_requires_both_loaded_and_inactive(
    monkeypatch, shadow_active_state, expected
):
    def run(command, **_kwargs):
        unit = command[2]
        module = rollback.FORWARD_WRITER_UNIT_MODULES[unit]
        active_state = (
            shadow_active_state
            if unit == "dispatch-shadow.service"
            else "inactive"
        )
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "LoadState=loaded\n"
                f"ActiveState={active_state}\n"
                f"WorkingDirectory={rollback.DEPLOYED_SCRIPTS_ROOT}\n"
                "ExecStart={ "
                f"path={rollback.DISPATCH_PYTHON} ; "
                f"argv[]={rollback.DISPATCH_PYTHON} -m {module} ; "
                "ignore_errors=no }\n"
            ),
        )

    monkeypatch.setattr(rollback.subprocess, "run", run)

    verified, states = rollback._probe_forward_writer_quiescence()

    assert verified is expected
    assert set(states) == set(rollback.FORWARD_WRITER_UNITS)
    assert states["dispatch-shadow.service"]["active_state"] == (
        shadow_active_state
    )
    assert states["dispatch-shadow.service"][
        "target_mode_verified"
    ] is True


def test_forward_writer_probe_rejects_wrong_deployed_module(monkeypatch):
    def run(command, **_kwargs):
        unit = command[2]
        module = rollback.FORWARD_WRITER_UNIT_MODULES[unit]
        if unit == "dispatch-shadow.service":
            module = "dispatch_v2.staging_shadow"
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "LoadState=loaded\n"
                "ActiveState=inactive\n"
                f"WorkingDirectory={rollback.DEPLOYED_SCRIPTS_ROOT}\n"
                "ExecStart={ "
                f"path={rollback.DISPATCH_PYTHON} ; "
                f"argv[]={rollback.DISPATCH_PYTHON} -m {module} ; "
                "ignore_errors=no }\n"
            ),
        )

    monkeypatch.setattr(rollback.subprocess, "run", run)

    verified, states = rollback._probe_forward_writer_quiescence()

    assert verified is False
    assert states["dispatch-panel-watcher.service"][
        "target_mode_verified"
    ] is True
    assert states["dispatch-shadow.service"][
        "target_mode_verified"
    ] is False


def test_forward_gate_reserves_assignment_policy_snapshot():
    """A pre-v16 pending assignment must terminalize before the handoff."""
    assignment = {
        "event_type": "COURIER_ASSIGNED",
        "event_id": "assignment-policy-snapshot",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
            "czas_kuriera_hhmm": "19:40",
        },
        "czasowka_assignment_ck_forward_authority_enabled": True,
        "czasowka_assignment_ck_passive_guard_enabled": True,
    }

    assert is_committed_pickup_outbox_artifact(
        _outbox_row(assignment, event_id="assignment-policy-snapshot")
    ) is True


@pytest.mark.parametrize("receipt_forward_enabled", [False, True])
def test_forward_deploy_ignores_valid_unclaimed_elastic_queue_receipt(
    monkeypatch, receipt_forward_enabled
):
    """A bound explicit-elastic click has flag-independent semantics."""
    oid = "elastic-queue-stable"
    receipt = {
        "schema": "coordinator_time_recheck.v6",
        "request_id": "elastic-request",
        "order_id": oid,
        "requested_at": "2026-08-02T18:00:00+00:00",
        "eligible_at": "2026-08-02T18:00:00+00:00",
        "source": "coordinator_panel",
        "continuation_depth": 0,
        "committed_time_policy_snapshot": {
            "schema": "committed_pickup.policy_snapshot.v1",
            "producer": "coordinator_queue",
            "manual_passthrough_enabled": False,
            "rutcom_forward_authority_enabled": receipt_forward_enabled,
            "passive_guard_enabled": True,
        },
    }
    elastic = {
        "order_id": oid,
        "status": "assigned",
        "courier_id": "492",
        "order_type": "elastic",
        "prep_minutes": 20,
        "pickup_at_warsaw": "2026-08-02T20:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T20:00:00+02:00",
        "czas_kuriera_hhmm": "20:00",
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(records=1, pending_pre_policy_records=1),
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_records_snapshot",
        lambda: {oid: receipt},
        raising=False,
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_record_is_unclaimed",
        lambda record, *, order_id: record == receipt and order_id == oid,
        raising=False,
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {oid: elastic},
    )

    status = rollback.collect_status(
        writer_quiescence_verified=True,
        deployed_code_manifest=_rollforward_code_manifest(),
    )

    assert status["forward_blocking_queue_records"] == 0
    assert status["forward_ignored_elastic_queue_records"] == 1
    assert status["safe_for_forward_deploy"] is True


def test_forward_deploy_blocks_unbound_pre_policy_elastic_receipt(
    monkeypatch,
):
    """State-only elasticity cannot prove an OFF-started click is stable."""
    oid = "elastic-queue-unbound-v5"
    receipt = {
        "schema": "coordinator_time_recheck.v5",
        "request_id": "elastic-unbound-request",
        "order_id": oid,
        "requested_at": "2026-08-02T18:00:00+00:00",
        "eligible_at": "2026-08-02T18:00:00+00:00",
        "source": "coordinator_panel",
        "continuation_depth": 0,
    }
    order = {
        "order_id": oid,
        "status": "assigned",
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(records=1, pending_pre_policy_records=1),
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_records_snapshot",
        lambda: {oid: receipt},
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_record_is_unclaimed",
        lambda record, *, order_id: record == receipt and order_id == oid,
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {oid: order},
    )

    status = rollback.collect_status(writer_quiescence_verified=True)

    assert status["forward_blocking_queue_records"] == 1
    assert status["forward_ignored_elastic_queue_records"] == 0
    assert status["safe_for_forward_deploy"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_receipt",
        "missing_state",
        "implicit_type",
        "prep_promoted",
        "terminal",
        "committed_artifact",
    ],
)
def test_forward_queue_elastic_exception_fails_closed_on_mutation(
    monkeypatch, mutation
):
    """Only the full exact elastic proof may bypass the nonempty queue gate."""
    oid = "elastic-queue-mutated"
    receipt = {"schema": "coordinator_time_recheck.v5", "order_id": oid}
    order = {
        "order_id": oid,
        "status": "assigned",
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    valid_receipt = mutation != "invalid_receipt"
    if mutation == "missing_state":
        orders = {}
    else:
        if mutation == "implicit_type":
            order.pop("order_type")
        elif mutation == "prep_promoted":
            order["prep_minutes"] = 60
        elif mutation == "terminal":
            order["status"] = "delivered"
        elif mutation == "committed_artifact":
            order["committed_pickup_event_key"] = "reserved"
        orders = {oid: order}
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(records=1, pending_pre_policy_records=1),
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_records_snapshot",
        lambda: {oid: receipt},
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_record_is_unclaimed",
        lambda _record, *, order_id: valid_receipt and order_id == oid,
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: orders,
    )

    status = rollback.collect_status(writer_quiescence_verified=True)

    assert status["forward_blocking_queue_records"] == 1
    assert status["forward_ignored_elastic_queue_records"] == 0
    assert status["safe_for_forward_deploy"] is False


def test_forward_queue_snapshot_count_mismatch_fails_closed(monkeypatch):
    """Two separately read queue views may agree only by exact record count."""
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(records=1, pending_pre_policy_records=1),
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_records_snapshot",
        lambda: {},
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status(writer_quiescence_verified=True)

    assert status["queue_record_count_matches_status"] is False
    assert status["safe_for_forward_deploy"] is False


def test_forward_deploy_requires_atomic_enqueue_fence(monkeypatch):
    monkeypatch.setattr(rollback.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        rollback.event_bus,
        "list_unfinished_state_applies",
        lambda: [],
    )
    monkeypatch.setattr(
        rollback.queue,
        "queue_compatibility_status",
        lambda: _queue_status(),
    )
    monkeypatch.setattr(
        rollback.queue,
        "forward_rollout_fence_status",
        lambda: {
            "forward_fence_present": False,
            "forward_fence_valid": False,
            "forward_fence_error": None,
            "forward_fence_id": None,
            "forward_fence_queue_sha256": None,
        },
    )
    monkeypatch.setattr(
        rollback.state_machine,
        "get_all_strict",
        lambda: {},
    )

    status = rollback.collect_status(writer_quiescence_verified=True)

    assert status["forward_fence"]["forward_fence_valid"] is False
    assert status["safe_for_forward_deploy"] is False


def test_fence_forward_reprobes_writers_and_returns_exact_receipt(
    monkeypatch, capsys
):
    manifest = _rollforward_code_manifest()
    probes = iter([(True, {"phase": "before"}), (True, {"phase": "after"})])
    fence = {
        "acquired": True,
        "forward_fence_valid": True,
        "forward_fence_id": "00000000-0000-4000-8000-000000000001",
    }
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: next(probes),
    )
    acquired_with = []
    monkeypatch.setattr(
        rollback.queue,
        "acquire_forward_rollout_fence",
        lambda value: acquired_with.append(value) or fence,
    )
    monkeypatch.setattr(
        rollback,
        "_deployed_rollforward_code_manifest",
        lambda: manifest,
    )
    monkeypatch.setattr(
        rollback,
        "collect_status",
        lambda **_kwargs: {
            "forward_fence": {"forward_fence_valid": True},
            "safe_for_forward_deploy": True,
        },
    )

    result = rollback._cmd_fence_forward(
        SimpleNamespace(apply=True, quiesced=True)
    )

    assert result == 0
    assert acquired_with == [manifest]
    output = capsys.readouterr().out
    assert '"ready": true' in output
    assert fence["forward_fence_id"] in output


def test_release_forward_fence_binds_exact_id_to_effective_flag(
    monkeypatch,
):
    fence_id = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(
        rollback,
        "_probe_forward_writer_quiescence",
        lambda: (True, {"phase": "release"}),
    )
    monkeypatch.setattr(
        rollback.C,
        "decision_flag",
        lambda name: False,
    )
    monkeypatch.setattr(
        rollback.queue,
        "release_forward_rollout_fence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("flag mismatch must be checked before release")
        ),
    )
    args = SimpleNamespace(
        apply=True,
        quiesced=True,
        authority_active=True,
        abort_off=False,
        fence_id=fence_id,
    )

    with pytest.raises(RuntimeError, match="mismatches OFF flag"):
        rollback._cmd_release_forward_fence(args)

    with pytest.raises(
        RuntimeError,
        match="exactly one",
    ):
        rollback._cmd_release_forward_fence(
            SimpleNamespace(
                apply=True,
                quiesced=True,
                authority_active=False,
                abort_off=False,
                fence_id=fence_id,
            )
        )
