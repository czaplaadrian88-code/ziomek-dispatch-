"""Regression contract for Rutcom committed pickup authority.

Incident 491578 (2026-08-01): Rutcom exposed the restaurant-agreed 19:21,
while orders_state and the courier app remained at the initial 19:16.  The
resolver below is the only policy owner for distinguishing a legal commitment
from Rutcom's known status restamp (#483023).
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dispatch_v2.committed_pickup_authority import (
    CommittedPickupPolicySnapshot,
    ResolutionOutcome,
    pickup_event_has_authority_artifact,
    resolve_czasowka_committed_observation,
    validate_committed_pickup_event,
)


def _existing_491578(*, status: str = "assigned") -> dict:
    return {
        "order_id": "491578",
        "status": status,
        "courier_id": "492",
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_hhmm": "19:16",
        "zmiana_czasu_odbioru": False,
        "pickup_time_revision": 0,
    }


def _observation_491578(**overrides) -> dict:
    payload = {
        "oid": "491578",
        "courier_id": "492",
        "courier_id_at_observation": "492",
        "assignment_event_id_at_observation": None,
        "pickup_time_revision_at_observation": 0,
        "old_ck_iso": "2026-08-01T19:16:00+02:00",
        "old_ck_hhmm": "19:16",
        "new_ck_iso": "2026-08-01T19:21:00+02:00",
        "new_ck_hhmm": "19:21",
        "delta_min": 5.0,
        "source": "panel_re_check",
        "new_zmiana_czasu_odbioru": False,
        "observed_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "observed_status_id": 2,
        "observed_prep_minutes": 60,
        "observed_decision_deadline": "2026-08-01T18:16:58+02:00",
        "observed_at": "2026-08-01T18:50:57+02:00",
    }
    payload.update(overrides)
    if (
        "courier_id" in overrides
        and "courier_id_at_observation" not in overrides
    ):
        payload["courier_id_at_observation"] = overrides["courier_id"]
    return payload


def _resolve(existing: dict, payload: dict, **overrides):
    options = {
        "is_czasowka": True,
        "passive_guard_enabled": True,
        "manual_passthrough_enabled": True,
        "rutcom_forward_authority_enabled": True,
    }
    options.update(overrides)
    return resolve_czasowka_committed_observation(existing, payload, **options)


def test_491578_forward_rutcom_commitment_becomes_canonical_pickup_event():
    resolution = _resolve(_existing_491578(), _observation_491578())

    assert resolution.outcome is ResolutionOutcome.APPLY
    assert resolution.reason == "rutcom_forward_commitment"
    assert resolution.event is not None
    assert resolution.event["event_type"] == "PICKUP_TIME_UPDATED"
    assert resolution.event["order_id"] == "491578"
    assert resolution.event["payload"]["old_pickup_at_warsaw"] == (
        "2026-08-01T19:15:58+02:00"
    )
    assert resolution.event["payload"]["new_pickup_at_warsaw"] == (
        "2026-08-01T19:21:00+02:00"
    )
    assert resolution.event["payload"]["committed_authority"] == (
        "rutcom_forward_commitment"
    )


def test_new_authority_flag_off_is_exact_legacy_suppression():
    resolution = _resolve(
        _existing_491578(),
        _observation_491578(),
        rutcom_forward_authority_enabled=False,
    )

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.event is None


def test_proof_revalidation_rejects_concurrent_ck_only_change():
    event = _resolve(_existing_491578(), _observation_491578()).event
    current = {
        **_existing_491578(),
        "czas_kuriera_warsaw": "2026-08-01T19:18:00+02:00",
        "czas_kuriera_hhmm": "19:18",
    }

    validation = validate_committed_pickup_event(
        current,
        event,
        is_czasowka=True,
        passive_guard_enabled=True,
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
    )

    assert validation.outcome is ResolutionOutcome.SUPPRESS
    assert validation.reason == "proof_policy_rejected:observed_ck_changed"


@pytest.mark.parametrize(
    ("field", "concurrent_value"),
    [
        ("order_type", "elastic"),
        ("prep_minutes", 61),
        ("decision_deadline", "2026-08-01T18:20:00+02:00"),
        ("zmiana_czasu_odbioru", True),
    ],
)
def test_proof_revalidation_rejects_concurrent_coupled_state_change(
    field, concurrent_value
):
    """Authority CAS obejmuje każdy field atomowo mutowany z pickup+CK."""
    event = _resolve(_existing_491578(), _observation_491578()).event
    current = {**_existing_491578(), field: concurrent_value}

    validation = validate_committed_pickup_event(
        current,
        event,
        is_czasowka=True,
        passive_guard_enabled=True,
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
    )

    assert validation.outcome is ResolutionOutcome.SUPPRESS
    assert validation.reason == f"proof_policy_rejected:{field}_changed"


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("order_type", "elastic"),
        ("prep_minutes", 61),
        ("decision_deadline", "2026-08-01T18:20:00+02:00"),
        ("zmiana_czasu_odbioru", True),
    ],
)
def test_committed_postcondition_requires_every_coupled_field(
    tmp_path, monkeypatch, field, corrupt_value
):
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        committed_pickup_effect_applied,
    )

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = _resolve(_existing_491578(), _observation_491578()).event
    assert sm.update_from_event(event) is not None
    stored = sm.get_order("491578")
    assert committed_pickup_effect_applied(stored, event["payload"])

    corrupted = {**stored, field: corrupt_value}
    assert not committed_pickup_effect_applied(
        corrupted, event["payload"]
    )


def test_first_acceptance_without_delta_has_stable_time_event_key():
    from dispatch_v2.committed_pickup_apply import time_update_event_key

    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": None,
            "source": "first_acceptance",
        },
    }

    first = time_update_event_key("491578", event)
    second = time_update_event_key("491578", event)

    assert first == second
    assert "_NO_BASELINE_to_" in first


def test_partial_ck_cas_identity_cannot_downgrade_to_legacy():
    from dispatch_v2.committed_pickup_authority import (
        time_event_cas_artifact_present,
        time_event_cas_status,
    )

    payload = {
        "old_ck_iso": "2026-08-01T19:16:00+02:00",
        "old_ck_hhmm": "19:16",
        "new_ck_iso": "2026-08-01T19:21:00+02:00",
        "new_ck_hhmm": "19:21",
        "source": "coordinator_force",
        # Te dwa pola były już częścią v14 causal identity. Ich zachowanie po
        # częściowej korupcji rezerwuje kopertę; nie wolno spaść do legacy.
        "courier_id_at_observation": "492",
        "assignment_event_id_at_observation": None,
    }
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": payload,
    }

    assert time_event_cas_artifact_present(
        "CZAS_KURIERA_UPDATED", payload
    )
    assert time_event_cas_status(_existing_491578(), event) == "superseded"


def test_authority_on_blocks_delayed_first_acceptance_as_parallel_ck_writer(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    delayed_first_acceptance = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": None,
            "source": "first_acceptance",
        },
    }

    assert sm.update_from_event(delayed_first_acceptance) is None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_warsaw"].endswith("T19:16:00+02:00")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")


@pytest.mark.parametrize("source", ["coordinator_edit", "ziomek_late_extension"])
def test_authority_on_explicitly_retires_historical_ck_only_source(
    tmp_path, monkeypatch, source
):
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        RETIRED_CZASOWKA_CK_ONLY_SOURCES,
    )

    assert source in RETIRED_CZASOWKA_CK_ONLY_SOURCES
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:30:00+02:00",
            "new_ck_hhmm": "19:30",
            "delta_min": 14.0,
            "source": source,
        },
    }

    assert sm.update_from_event(raw_ck) is None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_hhmm"] == "19:16"
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")


def test_authority_on_blocks_first_acceptance_from_empty_ck_baseline(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {"czas_kuriera_warsaw": None, "czas_kuriera_hhmm": None},
        event="TEST_CLEAR_CK_BASELINE",
    )
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    first_acceptance = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-08-01T19:40:00+02:00",
            "new_ck_hhmm": "19:40",
            "delta_min": None,
            "source": "first_acceptance",
        },
    }

    assert sm.update_from_event(first_acceptance) is None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_warsaw"] is None
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")


def test_authority_on_assignment_cannot_create_divergent_ck_from_empty_baseline(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "planned",
            "courier_id": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_ASSIGNMENT_BASELINE",
    )
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    result = sm.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "order_id": "491578",
            "courier_id": "492",
            "payload": {
                "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
                "czas_kuriera_hhmm": "19:40",
            },
        }
    )

    assert result is not None
    stored = sm.get_order("491578")
    assert stored["status"] == "assigned"
    assert stored["courier_id"] == "492"
    assert stored["czas_kuriera_warsaw"] is None
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")


def test_authority_off_assignment_empty_ck_keeps_exact_legacy_writer(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "planned",
            "courier_id": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_ASSIGNMENT_BASELINE",
    )
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    result = sm.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "order_id": "491578",
            "courier_id": "492",
            "payload": {
                "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
                "czas_kuriera_hhmm": "19:40",
            },
        }
    )

    assert result is not None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_warsaw"].endswith("T19:40:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:40"


def test_authority_on_durable_assignment_oracle_matches_suppressed_ck_writer(
    tmp_path, monkeypatch
):
    """Pierwsza próba ma domknąć state+downstream, nie dopiero marker-retry."""
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "planned",
            "courier_id": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_ASSIGNMENT_BASELINE",
    )
    _isolate_durable_bus(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(pw.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(
        pw, "_capture_panel_learning_context", lambda _oid: None
    )

    outcome = pw._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="491578",
        courier_id="492",
        payload={
            "source": "panel_initial",
            "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
            "czas_kuriera_hhmm": "19:40",
        },
        event_id="491578_COURIER_ASSIGNED_492_canonical",
        audit=True,
    )

    stored = sm.get_order("491578")
    receipt = event_bus.get_state_apply_outbox(outcome.event_id)
    assert stored["status"] == "assigned"
    assert stored["courier_id"] == "492"
    assert stored["czas_kuriera_warsaw"] is None
    assert stored["czas_kuriera_hhmm"] is None
    assert outcome.state_ready is True
    assert outcome.failure_stage is None
    assert receipt["state_status"] == "applied"
    assert receipt["downstream_status"] == "applied"


@pytest.mark.parametrize(
    ("forward_at_emit", "expected_ck_hhmm"),
    [(True, None), (False, "19:40")],
)
def test_durable_assignment_policy_snapshot_survives_opposite_hot_flip(
    tmp_path, monkeypatch, forward_at_emit, expected_ck_hhmm
):
    """Handler and terminal oracle consume the persisted emission decision."""
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD,
        ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD,
    )

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "planned",
            "courier_id": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_ASSIGNMENT_BASELINE",
    )
    _isolate_durable_bus(tmp_path, monkeypatch)
    def at_emit(name):
        return bool(
            forward_at_emit
            and name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
        )
    monkeypatch.setattr(sm, "decision_flag", at_emit)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw.C, "decision_flag", at_emit)
    monkeypatch.setattr(pw.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(
        pw, "_capture_panel_learning_context", lambda _oid: None
    )
    real_update = sm.update_from_event

    def apply_then_flip_live_policy(event):
        result = real_update(event)
        monkeypatch.setattr(
            sm,
            "decision_flag",
            lambda name: bool(
                not forward_at_emit
                and name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
            ),
        )
        monkeypatch.setattr(
            sm,
            "flag",
            lambda name, default=None: (
                False
                if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
                else default
            ),
        )
        return result

    monkeypatch.setattr(sm, "update_from_event", apply_then_flip_live_policy)
    outcome = pw._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="491578",
        courier_id="492",
        payload={
            "source": "panel_initial",
            "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
            "czas_kuriera_hhmm": "19:40",
        },
        event_id=f"491578_ASSIGNMENT_SNAPSHOT_{int(forward_at_emit)}",
        audit=True,
    )

    row = event_bus.get_state_apply_outbox(outcome.event_id)
    stored = sm.get_order("491578")
    assert row["state_event"][ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD] is (
        forward_at_emit
    )
    assert row["state_event"][ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD] is True
    assert stored["czas_kuriera_hhmm"] == expected_ck_hhmm
    assert outcome.state_ready is True
    assert row["state_status"] == "applied"


@pytest.mark.parametrize(
    "snapshot",
    [
        {"czasowka_assignment_ck_forward_authority_enabled": False},
        {
            "czasowka_assignment_ck_forward_authority_enabled": "false",
            "czasowka_assignment_ck_passive_guard_enabled": True,
        },
    ],
)
def test_partial_or_malformed_assignment_snapshot_fails_closed_for_ck_only(
    tmp_path, monkeypatch, snapshot
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "planned",
            "courier_id": None,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_ASSIGNMENT_BASELINE",
    )
    event = {
        "event_type": "COURIER_ASSIGNED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "czas_kuriera_warsaw": "2026-08-01T19:40:00+02:00",
            "czas_kuriera_hhmm": "19:40",
        },
        **snapshot,
    }

    assert sm.update_from_event(event) is not None
    assert sm.event_effect_status(event) == "applied"
    stored = sm.get_order("491578")
    assert stored["status"] == "assigned"
    assert stored["czas_kuriera_warsaw"] is None
    assert stored["czas_kuriera_hhmm"] is None


def test_committed_writer_materializes_legacy_czasowka_identity_before_prep_drop(
    tmp_path, monkeypatch
):
    """Implicit prep>=60 identity cannot disappear under its own coupled write."""
    from dispatch_v2 import common as C
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        build_time_event_cas_snapshot,
    )

    legacy = _existing_491578()
    legacy.pop("order_type")
    assert C.is_czasowka_order(legacy) is True
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    sm.upsert_order("491578", legacy, event="LEGACY_CZASOWKA")
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    committed = _resolve(
        legacy,
        _observation_491578(observed_prep_minutes=20),
    ).event
    assert sm.update_from_event(committed) is not None
    stored = sm.get_order("491578")

    assert stored["prep_minutes"] == 20
    assert stored["order_type"] == "czasowka"
    assert C.is_czasowka_order(stored) is True
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": stored["czas_kuriera_warsaw"],
            "old_ck_hhmm": stored["czas_kuriera_hhmm"],
            "new_ck_iso": "2026-08-01T19:30:00+02:00",
            "new_ck_hhmm": "19:30",
            "delta_min": 9.0,
            "source": "coordinator_edit",
            **build_time_event_cas_snapshot(
                stored, "CZAS_KURIERA_UPDATED"
            ),
        },
    }
    assert sm.event_effect_status(raw_ck) == "superseded"
    assert sm.update_from_event(raw_ck) is None


def test_authority_off_preserves_legacy_first_acceptance_behavior(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    delayed_first_acceptance = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": None,
            "source": "first_acceptance",
        },
    }

    assert sm.update_from_event(delayed_first_acceptance) is not None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_warsaw"].endswith("T19:18:00+02:00")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")


def test_authority_proof_cannot_be_replayed_on_elastic_order():
    elastic = {
        **_existing_491578(),
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    # Dowodzi, że sama poprawna struktura proofu nie zastępuje kanonicznej
    # klasyfikacji istniejącego rekordu przy rewalidacji state/transport.
    forged = _resolve(elastic, _observation_491578()).event

    validation = validate_committed_pickup_event(
        elastic,
        forged,
        is_czasowka=False,
        passive_guard_enabled=True,
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
    )

    assert validation.outcome is ResolutionOutcome.SUPPRESS
    assert validation.reason == "not_czasowka"


def test_authority_proof_rejects_unbound_top_level_lifecycle_alias():
    current = _existing_491578()
    canonical = _resolve(current, _observation_491578()).event
    forged = dict(canonical)
    forged["state_marker_alias_event_type"] = "ORDER_DELIVERED"

    validation = validate_committed_pickup_event(
        current,
        forged,
        is_czasowka=True,
        passive_guard_enabled=True,
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
    )

    assert validation.outcome is ResolutionOutcome.SUPPRESS
    assert validation.reason == "proof_event_mismatch"


def test_483023_status_restamp_before_ready_remains_blocked():
    existing = {
        "order_id": "483023",
        "status": "assigned",
        "courier_id": "484",
        "order_type": "czasowka",
        "prep_minutes": 126,
        "pickup_at_warsaw": "2026-06-24T16:21:22+02:00",
        "czas_kuriera_warsaw": "2026-06-24T16:22:00+02:00",
        "czas_kuriera_hhmm": "16:22",
        "zmiana_czasu_odbioru": False,
    }
    payload = _observation_491578(
        oid="483023",
        courier_id="484",
        old_ck_iso="2026-06-24T16:22:00+02:00",
        old_ck_hhmm="16:22",
        new_ck_iso="2026-06-24T15:04:00+02:00",
        new_ck_hhmm="15:04",
        delta_min=-78.0,
        observed_pickup_at_warsaw="2026-06-24T16:21:22+02:00",
        observed_status_id=3,
        observed_prep_minutes=126,
        observed_at="2026-06-24T15:04:05+02:00",
    )

    resolution = _resolve(existing, payload)

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "passive_not_forward"
    assert resolution.event is None


def test_post_pickup_rutcom_restamp_never_rewrites_commitment():
    resolution = _resolve(
        _existing_491578(status="picked_up"),
        _observation_491578(observed_status_id=5),
    )

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "order_not_active"
    assert resolution.event is None


def test_manual_marker_accepts_backward_edit_for_planned_rutcom_status_2():
    resolution = _resolve(
        _existing_491578(status="planned"),
        _observation_491578(
            new_ck_iso="2026-08-01T19:10:00+02:00",
            new_ck_hhmm="19:10",
            delta_min=-6.0,
            new_zmiana_czasu_odbioru=True,
        ),
    )

    assert resolution.outcome is ResolutionOutcome.APPLY
    assert resolution.reason == "rutcom_manual_marker"
    assert resolution.event is not None
    assert resolution.event["payload"]["new_pickup_at_warsaw"].endswith(
        "T19:10:00+02:00"
    )


def test_coordinator_force_requires_durable_receipt_for_backward_edit():
    payload = _observation_491578(
        source="coordinator_force",
        new_ck_iso="2026-08-01T19:10:00+02:00",
        new_ck_hhmm="19:10",
        delta_min=-6.0,
    )
    without_receipt = _resolve(_existing_491578(), payload)
    with_receipt = _resolve(
        _existing_491578(),
        {
            **payload,
            "authority_receipt": {
                "schema": "coordinator_time_recheck.v6",
                "request_id": "req-491578",
                "order_id": "491578",
                "requested_at": "2026-08-01T18:50:50+02:00",
                "eligible_at": "2026-08-01T18:50:50+02:00",
                "source": "coordinator_panel",
                "continuation_depth": 0,
                "committed_time_policy_snapshot": {
                    "schema": "committed_pickup.policy_snapshot.v1",
                    "producer": "coordinator_queue",
                    "manual_passthrough_enabled": False,
                    "rutcom_forward_authority_enabled": True,
                    "passive_guard_enabled": True,
                },
            },
        },
        coordinator_receipt_verified=True,
    )

    assert without_receipt.outcome is ResolutionOutcome.SUPPRESS
    assert without_receipt.reason == "missing_authority_receipt"
    assert with_receipt.outcome is ResolutionOutcome.APPLY
    assert with_receipt.reason == "coordinator_receipt"


def test_legacy_v2_receipt_cannot_authorize_coordinator_change():
    payload = _observation_491578(
        source="coordinator_force",
        new_ck_iso="2026-08-01T19:10:00+02:00",
        new_ck_hhmm="19:10",
        authority_receipt={
            "schema": "coordinator_time_recheck.v2",
            "request_id": "legacy-v2",
            "order_id": "491578",
            "requested_at": "2026-08-01T18:50:50+02:00",
            "source": "coordinator_panel",
        },
    )

    resolution = _resolve(
        _existing_491578(),
        payload,
        coordinator_receipt_verified=True,
    )

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "missing_authority_receipt"


def _authority_decision_flag(name: str) -> bool:
    return name in {
        "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
        "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
    }


def _authority_runtime_flag(name: str, default=None):
    if name in {
        "ENABLE_CZASOWKA_CK_PASSIVE_GUARD",
        "ENABLE_PICKUP_TIME_MIRRORS_CK",
    }:
        return True
    return default


def _panel_policy(
    *,
    manual: bool = True,
    forward: bool = True,
    passive: bool = True,
) -> CommittedPickupPolicySnapshot:
    return CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=manual,
        rutcom_forward_authority_enabled=forward,
        passive_guard_enabled=passive,
    )


def _seed_state_491578(sm, tmp_path, monkeypatch):
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    sm.upsert_order(
        "491578",
        _existing_491578(),
        event="COURIER_ASSIGNED",
    )
    return state_path


def _isolate_durable_bus(tmp_path, monkeypatch):
    from dispatch_v2 import event_bus
    from dispatch_v2 import lifecycle_downstream

    events_db = tmp_path / "events.db"
    with sqlite3.connect(events_db) as conn:
        conn.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                order_id TEXT,
                courier_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                status TEXT DEFAULT 'pending'
            );
            CREATE INDEX idx_events_status ON events(status);
            CREATE TABLE processed_events (
                event_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            """
        )
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(event_bus, "_audit_log_initialized", False)
    monkeypatch.setattr(event_bus, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(event_bus, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    return events_db


def _seed_pending_initial_time_contract(tmp_path, monkeypatch, *, oid):
    """Create the exact crash state: NEW_ORDER shell plus durable raw intent."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        RUTCOM_FORWARD_AUTHORITY_FLAG,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))

    def decision(name):
        return name == RUTCOM_FORWARD_AUTHORITY_FLAG

    monkeypatch.setattr(pw.C, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    payload = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
        "czas_kuriera_warsaw": "2099-08-02T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 2,
        "restaurant": "fixture",
        "pickup_address": "fixture",
        "delivery_address": "fixture",
    }
    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload=payload,
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(manual=False),
    )
    assert initialized.state_ready is True
    return pw, sm, payload


def _isolate_coordinator_queue(tmp_path, monkeypatch):
    from dispatch_v2 import coordinator_time_recheck as ctr

    queue_path = tmp_path / "coordinator_time_recheck.json"
    monkeypatch.setattr(ctr, "QUEUE_PATH", str(queue_path))
    monkeypatch.setattr(ctr, "LOCK_PATH", str(queue_path) + ".lock")
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=True,
            rutcom_forward_authority_enabled=True,
            passive_guard_enabled=True,
        ),
    )
    return ctr


def _claim_coordinator_event(sm, ctr):
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    pending = ctr.pending_with_receipts()["491578"]
    payload = _observation_491578(
        source="coordinator_force",
        authority_receipt=pending,
        observed_at=pending["requested_at"],
    )
    resolution = sm.resolve_czasowka_ck_observation(
        _existing_491578(), payload
    )
    claimed = ctr.current_receipt("491578")
    assert resolution.outcome is ResolutionOutcome.APPLY
    assert claimed is not None and claimed.get("claim")
    assert ctr.verify_claimed_event(resolution.event)
    return resolution.event, claimed


def test_panel_watcher_routes_491578_through_canonical_pickup_event():
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:50:57+02:00",
    }
    with patch.object(sm, "decision_flag", side_effect=_authority_decision_flag), \
         patch.object(sm, "flag", side_effect=_authority_runtime_flag):
        event = pw._diff_czas_kuriera(
            _existing_491578(), fresh, oid="491578"
        )

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["committed_authority"] == (
        "rutcom_forward_commitment"
    )
    assert event["event_id_hint"].startswith(
        "491578_PICKUP_TIME_UPDATED_COMMITTED_"
    )


def test_panel_watcher_first_ck_snapshot_uses_forward_authority_owner():
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    old = {
        **_existing_491578(),
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:50:57+02:00",
    }
    with patch.object(
        sm, "decision_flag", side_effect=_authority_decision_flag
    ), patch.object(sm, "flag", side_effect=_authority_runtime_flag):
        event = pw._diff_czas_kuriera(old, fresh, oid="491578")

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["old_ck_iso"] is None
    assert event["payload"]["old_ck_hhmm"] is None
    assert event["payload"]["committed_authority"] == (
        "rutcom_forward_commitment"
    )


def test_panel_watcher_first_ck_snapshot_preserves_exact_off_legacy_event():
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    old = {
        **_existing_491578(),
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:50:57+02:00",
    }
    with patch.object(sm, "decision_flag", return_value=False), patch.object(
        sm, "flag", side_effect=_authority_runtime_flag
    ):
        event = pw._diff_czas_kuriera(old, fresh, oid="491578")

    assert event is not None
    assert event["event_type"] == "CZAS_KURIERA_UPDATED"
    assert event["event_id_suffix"] == "_FIRST_ACK"
    assert event["payload"]["source"] == "first_acceptance"
    assert event["payload"]["delta_min"] is None
    assert "committed_authority" not in event["payload"]


@pytest.mark.parametrize(
    ("existing_ck", "observed_ck"),
    [
        (
            (None, "19:16"),
            (None, None),
        ),
        (
            (None, None),
            (None, "19:16"),
        ),
    ],
)
def test_partial_null_ck_baseline_never_claims_committed_authority(
    existing_ck, observed_ck
):
    existing = {
        **_existing_491578(),
        "czas_kuriera_warsaw": existing_ck[0],
        "czas_kuriera_hhmm": existing_ck[1],
    }
    observation = _observation_491578(
        old_ck_iso=observed_ck[0],
        old_ck_hhmm=observed_ck[1],
    )

    resolution = _resolve(existing, observation)

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.event is None


def test_state_machine_atomically_persists_time_and_provenance(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        CK_CHANGE_REVISION_OBSERVATION_FIELD,
    )

    _seed_state_491578(sm, tmp_path, monkeypatch)
    resolution = _resolve(_existing_491578(), _observation_491578())
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)

    applied = sm.update_from_event(resolution.event)
    stored = sm.get_order("491578")

    assert applied is not None
    assert stored["pickup_at_warsaw"] == "2026-08-01T19:21:00+02:00"
    assert stored["czas_kuriera_warsaw"] == "2026-08-01T19:21:00+02:00"
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert resolution.event["payload"][CK_CHANGE_REVISION_OBSERVATION_FIELD] == 0
    assert (
        resolution.event["payload"]["committed_authority_proof"]
        ["observation"][CK_CHANGE_REVISION_OBSERVATION_FIELD]
        == 0
    )
    assert stored["v319g_ck_change_count"] == 1
    assert stored["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )
    assert stored["committed_pickup_observed_source"] == "panel_re_check"
    assert stored["committed_pickup_observed_at"] == (
        "2026-08-01T18:50:57+02:00"
    )
    assert stored["history"][-1]["event"] == "PICKUP_TIME_UPDATED"
    assert sm.event_effect_status(resolution.event) == "applied"


def test_state_machine_raw_ck_defense_uses_same_resolver(tmp_path, monkeypatch):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    applied = sm.update_from_event({
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": _observation_491578(),
    })
    stored = sm.get_order("491578")

    assert applied is not None
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )


def test_durable_raw_ck_is_terminally_rejected_before_second_transport(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    raw = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": "legacy-raw-durable-491578",
        "order_id": "491578",
        "courier_id": "492",
        "payload": _observation_491578(),
    }

    assert sm.event_effect_status(raw) == "superseded"
    assert sm.update_from_event(raw) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:16"
    assert stored.get("committed_pickup_authority") is None


def test_preproposal_twin_persists_same_canonical_event(
    tmp_path, monkeypatch
):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    _isolate_durable_bus(tmp_path, monkeypatch)
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(dp.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    fresh_time = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
    }
    observed_at = datetime.fromisoformat("2026-08-01T18:50:57+02:00")

    with patch("dispatch_v2.plan_manager.touch_plan", return_value=True):
        dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            observed_at,
            fresh_time=fresh_time,
        )

    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored["committed_pickup_event_key"].startswith(
        "491578_PICKUP_TIME_UPDATED_COMMITTED_"
    )


def test_preproposal_off_started_event_cannot_gain_authority_after_hot_on(
    tmp_path, monkeypatch
):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    # Runtime has already flipped ON, but this request owns the earlier OFF
    # policy. The state writer must not reinterpret it as a new authority write.
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    policy = CommittedPickupPolicySnapshot(
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=False,
        passive_guard_enabled=True,
    )

    with patch.object(event_bus, "emit_audit", return_value="legacy"):
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time={
                "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
                "status_id": 2,
                "prep_minutes": 60,
                "zmiana_czasu_odbioru": False,
            },
            authority_policy=policy,
        )

    stored = sm.get_order_strict("491578")
    assert accepted is True  # exact legacy emitter contract
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:16"
    assert stored.get("committed_pickup_authority") is None


def test_preproposal_on_started_event_finishes_after_hot_off(
    tmp_path, monkeypatch
):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    _isolate_durable_bus(tmp_path, monkeypatch)
    monkeypatch.setattr(dp.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    policy = CommittedPickupPolicySnapshot(
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=True,
        passive_guard_enabled=True,
    )
    fresh_time = {
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
    }

    with patch("dispatch_v2.plan_manager.touch_plan", return_value=True):
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time=fresh_time,
            authority_policy=policy,
        )

    stored = sm.get_order_strict("491578")
    assert accepted is True
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )


def test_flag_is_decision_scoped_default_off_and_registered():
    from dispatch_v2 import common as C

    name = "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
    registry_path = (
        __import__("pathlib").Path(__file__).parents[1]
        / "tools"
        / "flag_lifecycle_registry.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))["flags"]

    assert name in C.ETAP4_DECISION_FLAGS
    assert getattr(C, name) is False
    assert registry[name]["default"] is False
    # Przed deployem jedynym nosnikiem jest bezpieczny default w common.py;
    # operacja ON doda jawny klucz do runtime flags.json i re-seed zmieni SoT.
    assert registry[name]["source_of_truth"] in {
        "common.py-const",
        "flags.json",
    }


def test_raw_ck_cannot_ack_from_another_committed_effect(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    raw_event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": _observation_491578(),
    }

    assert sm.event_effect_status(raw_event) == "pending"
    assert sm.update_from_event(raw_event) is not None
    # Direct defense-in-depth potrafi przetłumaczyć raw event, ale raw outbox
    # nie ma prawa zaliczyć exact postcondition cudzego kanonicznego eventu.
    assert sm.event_effect_status(raw_event) == "superseded"

    unrelated = {
        **raw_event,
        "payload": {
            **raw_event["payload"],
            "old_ck_iso": "2026-08-01T18:00:00+02:00",
            "old_ck_hhmm": "18:00",
            "observed_at": "2026-08-01T18:51:30+02:00",
        },
    }
    assert sm.event_effect_status(unrelated) == "superseded"


def test_accepted_observation_cannot_land_after_pickup(tmp_path, monkeypatch):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    before_pickup = _existing_491578()
    event = _resolve(before_pickup, _observation_491578()).event
    stored = sm.get_order("491578")
    stored["status"] = "picked_up"
    sm.upsert_order("491578", stored, event="COURIER_PICKED_UP")
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    assert sm.update_from_event(event) is None
    after = sm.get_order("491578")
    assert after["pickup_at_warsaw"] == "2026-08-01T19:15:58+02:00"
    assert after["czas_kuriera_hhmm"] == "19:16"


def test_parallel_pickup_snapshot_cannot_revert_ck_authority(monkeypatch):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    current = {
        **_existing_491578(),
        "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "committed_pickup_authority": "rutcom_forward_commitment",
        "committed_pickup_panel_baseline_at_observation": (
            "2026-08-01T19:15:58+02:00"
        ),
    }
    fresh = {
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 2,
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:51:10+02:00",
    }
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    assert pw._diff_pickup_time(current, fresh, oid="491578") is None


def test_late_initial_pickup_uses_czasowka_authority_not_legacy_writer(
    monkeypatch,
):
    """Legalne null->value ma jeden proof-bound writer, bez legacy fallbacku."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    current = {
        **_existing_491578(),
        "pickup_at_warsaw": None,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
    }
    fresh = {
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 3,
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:16:58+02:00",
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:51:10+02:00",
    }
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    event = pw._diff_pickup_time(current, fresh, oid="491578")

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["committed_authority"] == "rutcom_pickup_field"
    assert event["payload"]["old_pickup_at_warsaw"] is None
    assert event["payload"]["new_pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )
    assert event["payload"]["delta_min"] is None


def test_new_czasowka_after_flip_commits_first_rutcom_tuple_in_one_tick(
    tmp_path, monkeypatch,
):
    """Pierwszy pełny tuple po NEW_ORDER nie czeka na drugi polling tick."""
    from dispatch_v2 import panel_client
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    oid = "late-after-flip"
    current = panel_client.normalize_order(
        {
            "id": oid,
            "id_status_zamowienia": 2,
            "czas_odbioru": 60,
        }
    )
    assert current is not None
    current.update(
        {
            "status": "planned",
            "courier_id": None,
            "assignment_event_id": None,
            "pickup_time_revision": 0,
            "v319g_ck_change_count": 0,
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        }
    )
    fresh = {
        **current,
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
        "czas_kuriera_hhmm": "14:05",
        "observed_at": "2026-08-02T12:00:00+02:00",
    }
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    sm.upsert_order(oid, current, event="NEW_ORDER")

    event = pw._diff_czas_kuriera(current, fresh, oid=oid)

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert event["payload"]["committed_authority"] == (
        "rutcom_forward_commitment"
    )
    assert event["payload"]["old_pickup_at_warsaw"] is None
    assert event["payload"]["new_pickup_at_warsaw"].endswith(
        "T14:05:00+02:00"
    )
    assert sm.update_from_event(event) is not None
    stored = sm.get_order(oid)
    assert stored["pickup_at_warsaw"].endswith("T14:05:00+02:00")
    assert stored["czas_kuriera_warsaw"].endswith("T14:05:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "14:05"
    assert pw._diff_pickup_time(stored, fresh, oid=oid) is None


def test_cold_start_absent_czasowka_routes_initial_tuple_through_authority(
    tmp_path, monkeypatch,
):
    """Status-3 CK restamp cannot become a second truth on initial ingest."""
    import builtins
    import io

    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2 import courier_resolver
    from dispatch_v2.committed_pickup_authority import (
        ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD,
        ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD,
        NEW_ORDER_TIME_INTENT_FIELD,
        build_new_order_time_intent,
    )
    NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD = (
        "czasowka_new_order_time_authority_enabled"
    )
    from dispatch_v2.durable_event_apply import DurableApplyOutcome

    oid = "491578"
    raw = {
        "id": int(oid),
        "id_kurier": 492,
        "id_status_zamowienia": 3,
    }
    norm = {
        "order_type": "czasowka",
        "restaurant": "Zapiecek",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 3,
        "prep_minutes": 60,
    }
    new_order_payload = {
        **norm,
        "pickup_address": "test",
        "delivery_address": "test",
        "prep_minutes": 60,
    }
    emitted = []

    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(courier_resolver, "_load_courier_tiers", lambda: {})
    monkeypatch.setattr(pw, "state_get_all", sm.get_all_strict)
    monkeypatch.setattr(pw, "fetch_order_details", lambda *_args: raw)
    monkeypatch.setattr(
        pw,
        "_build_order_details_payload",
        lambda *_args: (dict(norm), dict(new_order_payload)),
    )
    real_open = builtins.open

    def open_with_courier_map(path, *args, **kwargs):
        if str(path).endswith("/kurier_ids.json"):
            return io.StringIO('{"Jakub": 492}')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_with_courier_map)

    def capture(
        event_type,
        *,
        order_id,
        courier_id=None,
        payload=None,
        state_payload=None,
        event_id,
        audit=False,
        old_plan_release_authorized=None,
        committed_time_policy=None,
    ):
        if event_type == "NEW_ORDER":
            assert committed_time_policy is not None
            assert committed_time_policy.producer == "panel_watcher"
        state_body = dict(payload or {})
        initial_intent = None
        if event_type == "NEW_ORDER":
            initial_intent = build_new_order_time_intent(
                order_id,
                state_body,
                observed_at="2026-08-01T18:00:00+02:00",
            )
            state_body["pickup_at_warsaw"] = None
            state_body["czas_kuriera_warsaw"] = None
            state_body["czas_kuriera_hhmm"] = None
        emitted.append(
            {
                "event_type": event_type,
                "order_id": order_id,
                "courier_id": courier_id,
                "payload": state_body,
                "state_payload": state_payload,
                "event_id": event_id,
            }
        )
        state_event = {
            "event_type": event_type,
            "order_id": order_id,
            "courier_id": courier_id,
            "payload": state_body if state_payload is None else state_payload,
            "event_id": event_id,
        }
        if event_type == "NEW_ORDER":
            state_event[NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD] = True
            state_event[NEW_ORDER_TIME_INTENT_FIELD] = initial_intent
        if event_type == "COURIER_ASSIGNED":
            state_event[ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD] = True
            state_event[ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD] = True
        sm.update_from_event(state_event)
        return DurableApplyOutcome(
            event_id=event_id,
            event_key=event_id,
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
            state_event=state_event,
        )

    monkeypatch.setattr(pw, "_emit_and_apply_state", capture)

    def apply_time(order_id, event, **_kwargs):
        emitted.append(dict(event))
        assert sm.update_from_event(event) is not None
        return DurableApplyOutcome(
            event_id=str(event.get("event_id_hint") or "initial-time"),
            event_key=str(event.get("event_id_hint") or "initial-time"),
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
            state_event=dict(event),
        )

    monkeypatch.setattr(pw, "_apply_time_update_event", apply_time)
    stats = pw._post_restart_cold_start_scan(
        {"courier_packs": {"Jakub": [oid]}, "rest_names": {oid: "Zapiecek"}},
        csrf="test",
    )

    assert [event["event_type"] for event in emitted] == [
        "NEW_ORDER",
        "PICKUP_TIME_UPDATED",
        "COURIER_ASSIGNED",
    ]
    initializer = emitted[0]
    assert initializer["event_id"] == f"{oid}_NEW_ORDER_first"
    assert initializer["payload"]["order_type"] == "czasowka"
    assert initializer["payload"]["pickup_at_warsaw"] is None
    assert initializer["payload"]["czas_kuriera_warsaw"] is None
    assert initializer["payload"]["czas_kuriera_hhmm"] is None
    authority_event = emitted[1]
    assert authority_event["payload"]["committed_authority"] == (
        "rutcom_pickup_field"
    )
    assert authority_event["payload"]["new_pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )
    assert stats["cold_start_emitted"] == 1
    stored = sm.get_order(oid)
    assert stored["status"] == "assigned"
    assert stored["order_type"] == "czasowka"
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:15"


def test_real_durable_new_order_sanitizes_and_commits_initial_tuple(
    tmp_path, monkeypatch
):
    """Semantic event, outbox and state share one receipt-bound initializer."""
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
        RUTCOM_FORWARD_AUTHORITY_FLAG,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))

    def decision(name):
        return name == RUTCOM_FORWARD_AUTHORITY_FLAG

    monkeypatch.setattr(pw.C, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw.lifecycle_downstream, "apply", lambda _event: None)
    oid = "initial-durable"
    normalized = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
        "czas_kuriera_hhmm": "14:05",
        "status_id": 3,
    }
    payload = {
        **normalized,
        "restaurant": "fixture",
        "pickup_address": "fixture",
        "delivery_address": "fixture",
    }

    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload=payload,
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(manual=False),
    )

    assert initialized.state_ready is True
    assert initialized.state_event[
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD
    ] is True
    assert initialized.state_event["payload"]["pickup_at_warsaw"] is None
    assert initialized.state_event["payload"]["czas_kuriera_warsaw"] is None
    assert initialized.state_event["payload"]["czas_kuriera_hhmm"] is None
    semantic = event_bus.get_pending(limit=10, event_types=["NEW_ORDER"])
    assert len(semantic) == 1
    # Broadcast/audit keeps the source tuple. Only the state projection is
    # sanitized; otherwise a crash between shell creation and canonical time
    # recovery permanently changes what NEW_ORDER consumers observe.
    assert semantic[0]["payload"]["pickup_at_warsaw"] == (
        payload["pickup_at_warsaw"]
    )
    assert semantic[0]["payload"]["czas_kuriera_warsaw"] == (
        payload["czas_kuriera_warsaw"]
    )
    assert semantic[0]["payload"]["czas_kuriera_hhmm"] == (
        payload["czas_kuriera_hhmm"]
    )

    assert pw._initialize_new_order_time_contract(
        oid, normalized, initialized
    ) is True
    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"].endswith("T14:00:00+02:00")
    assert stored["czas_kuriera_warsaw"].endswith("T14:00:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "14:00"
    assert stored["committed_pickup_authority"] == "rutcom_pickup_field"


def test_new_order_forward_persists_original_time_intent_before_initializer(
    tmp_path, monkeypatch
):
    """Crash after NEW_ORDER must not erase the restaurant-agreed tuple."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        RUTCOM_FORWARD_AUTHORITY_FLAG,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))

    def decision(name):
        return name == RUTCOM_FORWARD_AUTHORITY_FLAG

    monkeypatch.setattr(pw.C, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw.lifecycle_downstream, "apply", lambda _event: None)
    oid = "initial-intent-durable"
    payload = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2026-08-02T19:16:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 2,
        "restaurant": "fixture",
        "pickup_address": "fixture",
        "delivery_address": "fixture",
    }

    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload=payload,
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(manual=False),
    )

    assert initialized.state_ready is True
    stored = sm.get_order_strict(oid)
    intent = stored["pending_committed_time_intent"]
    assert intent["pickup_at_warsaw"] == payload["pickup_at_warsaw"]
    assert intent["czas_kuriera_warsaw"] == payload["czas_kuriera_warsaw"]
    assert intent["czas_kuriera_hhmm"] == payload["czas_kuriera_hhmm"]


def test_new_order_time_intent_hash_rejects_tuple_tampering():
    """Changing 19:21 after receipt capture cannot forge initial authority."""
    from dispatch_v2.committed_pickup_authority import (
        ResolutionOutcome,
        build_new_order_time_intent,
        resolve_czasowka_initial_time_intent,
    )

    oid = "initial-intent-tamper"
    intent = build_new_order_time_intent(
        oid,
        {
            "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
            "czas_kuriera_warsaw": "2099-08-02T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
            "status_id": 2,
            "prep_minutes": 60,
        },
        observed_at="2099-08-02T18:50:00+02:00",
    )
    tampered = {
        **intent,
        "czas_kuriera_warsaw": "2099-08-02T19:26:00+02:00",
        "czas_kuriera_hhmm": "19:26",
    }
    shell = {
        "order_id": oid,
        "status": "planned",
        "order_type": "czasowka",
        "prep_minutes": 60,
        "courier_id": None,
        "pickup_at_warsaw": None,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
        "pickup_time_revision": 0,
        "v319g_ck_change_count": 0,
    }

    resolution = resolve_czasowka_initial_time_intent(shell, tampered)

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "invalid_new_order_time_intent"
    assert resolution.event is None


def test_coherently_rehashed_state_intent_cannot_forge_new_order_receipt(
    tmp_path, monkeypatch
):
    """A valid self-hash is not authority without the original outbox row."""
    from dispatch_v2.committed_pickup_apply import apply_event
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_INTENT_FIELD,
        ResolutionOutcome,
        build_new_order_time_intent,
        resolve_czasowka_initial_time_intent,
    )

    oid = "coherent-intent-tamper"
    _pw, sm, original = _seed_pending_initial_time_contract(
        tmp_path, monkeypatch, oid=oid
    )
    current = sm.get_order_strict(oid)
    original_intent = current[NEW_ORDER_TIME_INTENT_FIELD]
    forged = build_new_order_time_intent(
        oid,
        {
            **original,
            "czas_kuriera_warsaw": "2099-08-02T19:26:00+02:00",
            "czas_kuriera_hhmm": "19:26",
        },
        observed_at=original_intent["observed_at"],
    )
    sm.upsert_order(
        oid,
        {NEW_ORDER_TIME_INTENT_FIELD: forged},
        event="TEST_COHERENT_INTENT_TAMPER",
    )
    tampered = sm.get_order_strict(oid)
    resolution = resolve_czasowka_initial_time_intent(tampered, forged)
    assert resolution.outcome is ResolutionOutcome.APPLY

    with pytest.raises(ValueError, match="NEW_ORDER receipt"):
        apply_event(resolution.event)

    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"] is None
    assert stored[NEW_ORDER_TIME_INTENT_FIELD] == forged


def test_pending_initial_intent_rejects_sibling_legacy_pickup_writer(
    tmp_path, monkeypatch
):
    """Only the exact intent-id authority event may consume the shell."""
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_INTENT_FIELD,
    )

    oid = "pending-intent-sibling-writer"
    _pw, sm, _payload = _seed_pending_initial_time_contract(
        tmp_path, monkeypatch, oid=oid
    )
    before = sm.get_order_strict(oid)
    sibling = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "payload": {
            "old_pickup_at_warsaw": None,
            "new_pickup_at_warsaw": "2099-08-02T19:26:00+02:00",
            "source": "panel_re_check",
        },
    }

    assert sm.update_from_event(sibling) is None
    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"] is None
    assert stored[NEW_ORDER_TIME_INTENT_FIELD] == before[
        NEW_ORDER_TIME_INTENT_FIELD
    ]


def test_new_order_forward_receipt_survives_on_to_off_flip(
    tmp_path, monkeypatch
):
    """Crash-local data is unnecessary; the durable ON receipt survives OFF."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        RUTCOM_FORWARD_AUTHORITY_FLAG,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    enabled = {"value": True}

    def decision(name):
        return bool(
            enabled["value"] and name == RUTCOM_FORWARD_AUTHORITY_FLAG
        )

    monkeypatch.setattr(pw.C, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw.lifecycle_downstream, "apply", lambda _event: None)
    oid = "initial-receipt-flag-flip"
    normalized = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
        "czas_kuriera_warsaw": "2099-08-02T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 2,
    }
    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload={
            **normalized,
            "restaurant": "fixture",
            "pickup_address": "fixture",
            "delivery_address": "fixture",
        },
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(manual=False),
    )
    enabled["value"] = False
    crash_recovered_state = sm.get_order_strict(oid)
    del initialized

    assert pw._resume_new_order_time_contract(
        oid,
        crash_recovered_state,
    ) is True
    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"] == normalized["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_warsaw"] == normalized["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored["pending_committed_time_intent"] is None


def test_real_tick_recovers_pending_initial_intent_before_later_restamp(
    tmp_path, monkeypatch
):
    """The ordinary restart tick consumes the durable 19:21, not fresh 19:16."""
    from dispatch_v2 import common as C
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        RUTCOM_FORWARD_AUTHORITY_FLAG,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    enabled = {"value": True}

    def decision(name):
        return bool(
            enabled["value"] and name == RUTCOM_FORWARD_AUTHORITY_FLAG
        )

    monkeypatch.setattr(C, "decision_flag", decision)
    monkeypatch.setattr(pw, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    oid = "initial-real-tick-recovery"
    original = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
        "czas_kuriera_warsaw": "2099-08-02T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "status_id": 2,
        "restaurant": "fixture",
        "pickup_address": "fixture",
        "delivery_address": "fixture",
    }
    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload=original,
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(manual=False),
    )
    assert initialized.state_ready is True
    assert sm.get_order_strict(oid)["pickup_at_warsaw"] is None

    # Simulated process restart/hot rollback. The next panel response carries
    # the mutable status restamp, but both legacy detectors are disabled.
    enabled["value"] = False
    monkeypatch.setattr(C, "ENABLE_V319G_CK_DETECTION", False)
    monkeypatch.setattr(C, "ENABLE_PICKUP_TIME_DETECTION", False)
    monkeypatch.setattr(C, "flag", lambda _name, default=None: False)
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pw,
        "fetch_order_details",
        lambda *_args, **_kwargs: {
            "id_kurier": 26,
            "id_status_zamowienia": 2,
        },
    )
    monkeypatch.setattr(
        pw,
        "normalize_order",
        lambda _raw: {
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
            "czas_kuriera_warsaw": "2099-08-02T19:16:00+02:00",
            "czas_kuriera_hhmm": "19:16",
            "status_id": 2,
        },
    )

    stats = pw._diff_and_emit(
        {
            "order_ids": [oid],
            "assigned_ids": set(),
            "unassigned_ids": [oid],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    stored = sm.get_order_strict(oid)
    assert stats["errors"] == 0
    assert stored["pickup_at_warsaw"] == original["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_warsaw"] == original["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored["pending_committed_time_intent"] is None


def test_restart_tick_recovers_initial_intent_before_assignment_writer(
    tmp_path, monkeypatch
):
    """Lifecycle writers cannot advance the shell before receipt recovery."""
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_INTENT_FIELD,
    )

    oid = "initial-recovery-before-assignment"
    pw, sm, original = _seed_pending_initial_time_contract(
        tmp_path, monkeypatch, oid=oid
    )
    monkeypatch.setattr(
        pw.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_COORDINATOR_FORCE_TIME_RECHECK"
            else _authority_runtime_flag(name, default)
        ),
    )
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pw,
        "fetch_order_details",
        lambda *_args, **_kwargs: {
            "id_kurier": 492,
            "id_status_zamowienia": 2,
        },
    )
    monkeypatch.setattr(
        pw,
        "normalize_order",
        lambda _raw: {
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": "2099-08-02T19:16:00+02:00",
            "czas_kuriera_warsaw": "2099-08-02T19:16:00+02:00",
            "czas_kuriera_hhmm": "19:16",
            "status_id": 2,
        },
    )

    stats = pw._diff_and_emit(
        {
            "order_ids": [oid],
            "assigned_ids": {oid},
            "unassigned_ids": [],
            "rest_names": {},
            "courier_packs": {"492": [oid]},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    stored = sm.get_order_strict(oid)
    assert stats["errors"] == 0
    assert stored["status"] == "assigned"
    assert stored["courier_id"] == "492"
    assert stored["pickup_at_warsaw"] == original[
        "czas_kuriera_warsaw"
    ]
    assert stored["czas_kuriera_warsaw"] == original[
        "czas_kuriera_warsaw"
    ]
    assert stored[NEW_ORDER_TIME_INTENT_FIELD] is None


def test_restart_tick_recovers_initial_intent_even_when_order_left_board(
    tmp_path, monkeypatch
):
    """Durable recovery cannot depend on a mutable board/details response."""
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_INTENT_FIELD,
    )

    oid = "initial-recovery-absent-board"
    pw, sm, original = _seed_pending_initial_time_contract(
        tmp_path, monkeypatch, oid=oid
    )
    monkeypatch.setattr(
        pw.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_COORDINATOR_FORCE_TIME_RECHECK"
            else _authority_runtime_flag(name, default)
        ),
    )
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )

    stats = pw._diff_and_emit(
        {
            "order_ids": [],
            "assigned_ids": set(),
            "unassigned_ids": [],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    stored = sm.get_order_strict(oid)
    assert stats["errors"] == 0
    assert stored["pickup_at_warsaw"] == original["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_warsaw"] == original[
        "czas_kuriera_warsaw"
    ]
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored[NEW_ORDER_TIME_INTENT_FIELD] is None


def test_restart_tick_recovers_initial_intent_before_detail_fetch_failure(
    tmp_path, monkeypatch
):
    """A transient Rutcom detail failure happens after durable recovery."""
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_INTENT_FIELD,
    )

    oid = "initial-recovery-detail-failure"
    pw, sm, original = _seed_pending_initial_time_contract(
        tmp_path, monkeypatch, oid=oid
    )
    monkeypatch.setattr(
        pw.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_COORDINATOR_FORCE_TIME_RECHECK"
            else _authority_runtime_flag(name, default)
        ),
    )
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pw,
        "fetch_order_details",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fixture detail outage")
        ),
    )

    pw._diff_and_emit(
        {
            "order_ids": [oid],
            "assigned_ids": set(),
            "unassigned_ids": [oid],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"] == original["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_warsaw"] == original[
        "czas_kuriera_warsaw"
    ]
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored[NEW_ORDER_TIME_INTENT_FIELD] is None


def test_real_durable_new_order_off_preserves_legacy_initial_tuple(
    tmp_path, monkeypatch
):
    """The new initializer is dark at OFF, including the semantic event."""
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(pw.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(pw.lifecycle_downstream, "apply", lambda _event: None)
    oid = "initial-off"
    payload = {
        "order_type": "czasowka",
        "prep_minutes": 60,
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:05:00+02:00",
        "czas_kuriera_hhmm": "14:05",
        "status_id": 3,
        "restaurant": "fixture",
        "pickup_address": "fixture",
        "delivery_address": "fixture",
    }

    initialized = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload=payload,
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=_panel_policy(
            manual=False, forward=False
        ),
    )

    assert initialized.state_ready is True
    assert initialized.state_event[
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD
    ] is False
    semantic = event_bus.get_pending(limit=10, event_types=["NEW_ORDER"])
    assert semantic[0]["payload"]["pickup_at_warsaw"] == (
        payload["pickup_at_warsaw"]
    )
    assert semantic[0]["payload"]["czas_kuriera_warsaw"] == (
        payload["czas_kuriera_warsaw"]
    )
    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"] == payload["pickup_at_warsaw"]
    assert stored["czas_kuriera_warsaw"] == payload["czas_kuriera_warsaw"]
    assert stored["czas_kuriera_hhmm"] == payload["czas_kuriera_hhmm"]
    assert pw._initialize_new_order_time_contract(
        oid, payload, initialized
    ) is True


def test_unchanged_contradictory_rutcom_tuple_cannot_oscillate_authority(
    tmp_path, monkeypatch
):
    """Ten sam response pickup=19:10/CK=19:16 nie może tworzyć dwóch commitów."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    fresh = {
        "pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_hhmm": "19:16",
        "status_id": 2,
        "prep_minutes": 60,
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:51:00+02:00",
    }

    pickup_event = pw._diff_pickup_time(
        sm.get_order("491578"), fresh, oid="491578"
    )
    assert pickup_event is not None
    assert pickup_event["payload"]["committed_authority"] == (
        "rutcom_pickup_field"
    )
    assert sm.update_from_event(pickup_event) is not None
    after_pickup = sm.get_order("491578")
    assert after_pickup["pickup_at_warsaw"].endswith("T19:10:00+02:00")
    assert after_pickup["czas_kuriera_hhmm"] == "19:10"

    assert pw._diff_czas_kuriera(
        after_pickup, fresh, oid="491578"
    ) is None
    unchanged = sm.get_order("491578")
    assert unchanged["pickup_at_warsaw"].endswith("T19:10:00+02:00")
    assert unchanged["czas_kuriera_hhmm"] == "19:10"


def test_claimed_pickup_cas_binds_ck_snapshot_across_hot_off(
    tmp_path, monkeypatch
):
    """Późniejszy legalny CK wygrywa z niezaaplikowanym claimem pickup."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    claimed_resolution = sm.resolve_czasowka_pickup_observation(
        sm.get_order("491578"),
        {
            "oid": "491578",
            "courier_id": "492",
            "courier_id_at_observation": "492",
            "assignment_event_id_at_observation": None,
            "pickup_time_revision_at_observation": 0,
            "source": "coordinator_force",
            "observed_at": receipt["requested_at"],
            "observed_status_id": 2,
            "observed_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "new_ck_iso": "2026-08-01T19:16:00+02:00",
            "new_ck_hhmm": "19:16",
            "authority_receipt": receipt,
        },
    )
    assert claimed_resolution.outcome is ResolutionOutcome.APPLY
    assert ctr.verify_claimed_event(claimed_resolution.event)

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: default)
    later_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": 2.0,
            "source": "first_acceptance",
        },
    }
    assert sm.update_from_event(later_ck) is not None
    assert sm.event_effect_status(claimed_resolution.event) == "superseded"
    assert sm.update_from_event(claimed_resolution.event) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:18"


def test_claimed_pickup_cas_rejects_ck_aba_across_hot_off(
    tmp_path, monkeypatch
):
    """Exact claim wiąże generację CK, nie tylko wartość A po cyklu A→C→A."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    claimed_resolution = sm.resolve_czasowka_pickup_observation(
        sm.get_order("491578"),
        {
            "oid": "491578",
            "courier_id": "492",
            "courier_id_at_observation": "492",
            "assignment_event_id_at_observation": None,
            "pickup_time_revision_at_observation": 0,
            "source": "coordinator_force",
            "observed_at": receipt["requested_at"],
            "observed_status_id": 2,
            "observed_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "new_ck_iso": "2026-08-01T19:16:00+02:00",
            "new_ck_hhmm": "19:16",
            "authority_receipt": receipt,
        },
    )
    assert claimed_resolution.outcome is ResolutionOutcome.APPLY
    assert ctr.verify_claimed_event(claimed_resolution.event)

    # Hot-OFF blokuje nowe authority, lecz historyczne CK-only writerzy nadal
    # mogą legalnie wykonać dwie generacje. Sama równość wartości końcowej A
    # nie jest dowodem, że stary claim nadal opisuje bieżący stan.
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: default)

    def apply_legacy_ck(old_iso, old_hhmm, new_iso, new_hhmm):
        event = {
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": "491578",
            "courier_id": "492",
            "payload": {
                "old_ck_iso": old_iso,
                "old_ck_hhmm": old_hhmm,
                "new_ck_iso": new_iso,
                "new_ck_hhmm": new_hhmm,
                "delta_min": 4.0,
                "source": "coordinator_edit",
            },
        }
        assert sm.update_from_event(event) is not None

    apply_legacy_ck(
        "2026-08-01T19:16:00+02:00",
        "19:16",
        "2026-08-01T19:20:00+02:00",
        "19:20",
    )
    apply_legacy_ck(
        "2026-08-01T19:20:00+02:00",
        "19:20",
        "2026-08-01T19:16:00+02:00",
        "19:16",
    )
    assert sm.get_order("491578")["v319g_ck_change_count"] == 2

    assert sm.event_effect_status(claimed_resolution.event) == "superseded"
    assert sm.update_from_event(claimed_resolution.event) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:16"
    assert stored["v319g_ck_change_count"] == 2


def test_hot_off_keeps_active_committed_order_protected_from_raw_ck(
    tmp_path, monkeypatch
):
    """OFF zatrzymuje nowe authority, ale nie rozpoławia już committed stanu."""
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    authority_event = _resolve(
        sm.get_order("491578"), _observation_491578()
    ).event
    assert sm.update_from_event(authority_event) is not None
    assert sm.get_order("491578")["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    raw_ck = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:21:00+02:00",
            "old_ck_hhmm": "19:21",
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": -3.0,
            "source": "first_acceptance",
        },
    }

    assert sm.update_from_event(raw_ck) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"


def test_preproposal_suppressed_ck_never_leaks_into_scoring(
    tmp_path, monkeypatch
):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.route_simulator_v2 import OrderSim

    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    sm.upsert_order(
        "483023",
        {
            "order_id": "483023",
            "status": "assigned",
            "courier_id": "484",
            "order_type": "czasowka",
            "pickup_at_warsaw": "2026-06-24T16:21:22+02:00",
            "czas_kuriera_warsaw": "2026-06-24T16:22:00+02:00",
            "czas_kuriera_hhmm": "16:22",
            "zmiana_czasu_odbioru": False,
        },
        event="COURIER_ASSIGNED",
    )
    bag = OrderSim(
        order_id="483023",
        pickup_coords=(53.1, 23.1),
        delivery_coords=(53.2, 23.2),
        picked_up_at=None,
        status="assigned",
        pickup_ready_at=None,
    )
    bag.assigned_at = None
    bag.courier_id = "484"
    bag.czas_kuriera_warsaw = "2026-06-24T16:22:00+02:00"
    fresh = dp._V327FreshCzasKuriera(
        "2026-06-24T15:04:00+02:00",
        "15:04",
        {
            "czas_kuriera_warsaw": "2026-06-24T15:04:00+02:00",
            "czas_kuriera_hhmm": "15:04",
            "pickup_at_warsaw": "2026-06-24T16:21:22+02:00",
            "status_id": 3,
            "prep_minutes": 126,
            "zmiana_czasu_odbioru": False,
        },
    )
    dp._v327_pre_recheck_last_seen.clear()
    monkeypatch.setattr(dp.C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", True)
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(dp.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(dp, "_v327_safe_fetch_czas_kuriera", lambda *_a, **_k: fresh)

    result = dp.get_fresh_czas_kuriera_for_bag(
        [bag], datetime.fromisoformat("2026-06-24T15:04:05+02:00")
    )

    assert result["483023"] == "2026-06-24T16:22:00+02:00"


def test_preproposal_authority_uses_durable_apply_funnel(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(dp.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    outcome = SimpleNamespace(state_ready=True, downstream_executed=True)
    fresh_time = {
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "zmiana_czasu_odbioru": False,
    }

    def _apply_and_record(event, **_kwargs):
        sm.update_from_event(event)
        return outcome

    with patch.object(
        committed_pickup_apply, "apply_event", side_effect=_apply_and_record
    ) as durable_apply:
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "stale-courier-from-bag",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time=fresh_time,
        )

    assert accepted is True
    durable_apply.assert_called_once()
    assert durable_apply.call_args.kwargs[
        "authority_policy"
    ].rutcom_forward_authority_enabled is True
    emitted = durable_apply.call_args.args[0]
    assert emitted["courier_id"] == "492"
    assert emitted["payload"]["courier_id"] == "492"
    assert emitted["payload"]["courier_id_at_observation"] == "492"


def test_spoofed_authority_label_without_proof_or_receipt_is_rejected(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    forged = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "committed_authority": "coordinator_receipt",
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
        },
    }

    assert sm.update_from_event(forged) is None
    assert sm.get_order("491578")["pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )


def test_valid_proof_cannot_carry_tampered_state_or_downstream_fields(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    canonical = _resolve(_existing_491578(), _observation_491578()).event

    tampered_prep = json.loads(json.dumps(canonical))
    tampered_prep["payload"]["new_prep_minutes"] = 999
    tampered_courier = json.loads(json.dumps(canonical))
    tampered_courier["courier_id"] = "999"

    assert sm.update_from_event(tampered_prep) is None
    assert sm.update_from_event(tampered_courier) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:16"


def test_authority_event_is_frozen_by_pickup_evidence_even_if_status_regressed(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    event = _resolve(_existing_491578(), _observation_491578()).event
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    stored = sm.get_order("491578")
    stored["picked_up_at"] = "2026-08-01T19:17:00+02:00"
    sm.upsert_order("491578", stored, event="COURIER_PICKED_UP")

    assert sm.update_from_event(event) is None
    assert sm.get_order("491578")["czas_kuriera_hhmm"] == "19:16"


def test_authority_event_mirrors_ck_even_when_legacy_mirror_flag_is_off(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    resolution = _resolve(_existing_491578(), _observation_491578())
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_PICKUP_TIME_MIRRORS_CK"
            else _authority_runtime_flag(name, default)
        ),
    )
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)

    assert sm.update_from_event(resolution.event) is not None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"


def test_durable_effect_requires_pickup_ck_and_exact_provenance(monkeypatch):
    from dispatch_v2 import state_machine as sm

    event = _resolve(_existing_491578(), _observation_491578()).event
    partial = {
        **_existing_491578(),
        "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_hhmm": "19:16",
    }
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    assert sm.event_effect_status(event, current=partial) != "applied"


def test_watcher_and_preproposal_share_canonical_three_minute_noise_floor(
    monkeypatch,
):
    from dispatch_v2 import committed_pickup_apply
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:17:00+02:00",
        "czas_kuriera_hhmm": "19:17",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "zmiana_czasu_odbioru": False,
        "observed_at": "2026-08-01T18:50:57+02:00",
    }
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "get_order", lambda _oid: _existing_491578())
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)

    event = pw._diff_czas_kuriera(
        _existing_491578(), fresh, oid="491578"
    )
    pure = _resolve(_existing_491578(), {
        **_observation_491578(),
        "new_ck_iso": "2026-08-01T19:17:00+02:00",
        "new_ck_hhmm": "19:17",
    })
    with patch.object(committed_pickup_apply, "apply_event") as durable:
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:17:00+02:00",
            "19:17",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time=fresh,
        )

    assert event is None
    assert pure.outcome is ResolutionOutcome.SUPPRESS
    assert pure.reason == "committed_delta_below_threshold"
    assert accepted is False
    durable.assert_not_called()


def test_preproposal_proof_uses_current_state_ck_not_stale_bag_baseline(
    monkeypatch,
):
    from dispatch_v2 import committed_pickup_apply
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import state_machine as sm

    current = {
        **_existing_491578(),
        "czas_kuriera_warsaw": "2026-08-01T19:18:00+02:00",
        "czas_kuriera_hhmm": "19:18",
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "zmiana_czasu_odbioru": False,
    }
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "get_order", lambda _oid: dict(current))
    monkeypatch.setattr(dp.C, "decision_flag", _authority_decision_flag)

    with patch.object(
        committed_pickup_apply,
        "apply_event",
        return_value=SimpleNamespace(state_ready=False),
    ) as durable:
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            # Stary OrderSim/bag nadal niesie 19:16.
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time=fresh,
        )

    emitted = durable.call_args.args[0]
    proof_observation = emitted["payload"][
        "committed_authority_proof"
    ]["observation"]
    assert emitted["event_type"] == "PICKUP_TIME_UPDATED"
    assert proof_observation["old_ck_iso"].endswith("T19:18:00+02:00")
    assert proof_observation["old_ck_hhmm"] == "19:18"
    assert accepted is False


def test_manual_marker_keeps_legacy_state_status_compatibility():
    assigned_status_two = _resolve(
        _existing_491578(status="assigned"),
        _observation_491578(
            new_ck_iso="2026-08-01T19:10:00+02:00",
            new_ck_hhmm="19:10",
            new_zmiana_czasu_odbioru=True,
            observed_status_id=2,
        ),
    )
    planned_status_two = _resolve(
        _existing_491578(status="planned"),
        _observation_491578(
            new_ck_iso="2026-08-01T19:10:00+02:00",
            new_ck_hhmm="19:10",
            new_zmiana_czasu_odbioru=True,
            observed_status_id=2,
        ),
    )

    assert assigned_status_two.outcome is ResolutionOutcome.SUPPRESS
    assert planned_status_two.outcome is ResolutionOutcome.APPLY


def test_picked_up_timestamp_freezes_resolver_even_with_assigned_status():
    existing = {**_existing_491578(), "picked_up_at": "2026-08-01T19:17:00+02:00"}

    resolution = _resolve(existing, _observation_491578())

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "order_already_collected"


def test_sequential_forward_accepts_panel_baseline_from_previous_tick():
    first = _resolve(_existing_491578(), _observation_491578())
    first_payload = first.event["payload"]
    current = {
        **_existing_491578(),
        "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_time_revision": 1,
        "committed_pickup_authority": "rutcom_forward_commitment",
        "committed_pickup_panel_baseline_at_observation": first_payload[
            "committed_pickup_panel_baseline_at_observation"
        ],
    }
    second = _resolve(
        current,
        _observation_491578(
            old_ck_iso="2026-08-01T19:21:00+02:00",
            old_ck_hhmm="19:21",
            new_ck_iso="2026-08-01T19:26:00+02:00",
            new_ck_hhmm="19:26",
            observed_pickup_at_warsaw="2026-08-01T17:15:58+00:00",
            pickup_time_revision_at_observation=1,
            observed_at="2026-08-01T18:55:00+02:00",
        ),
    )

    assert second.outcome is ResolutionOutcome.APPLY
    assert second.event["payload"]["old_pickup_at_warsaw"].endswith(
        "T19:21:00+02:00"
    )
    assert second.event["payload"]["new_pickup_at_warsaw"].endswith(
        "T19:26:00+02:00"
    )
    assert second.event["payload"]["pickup_time_revision_at_observation"] == 1


def test_observation_and_generation_courier_must_both_match_current_lane():
    mismatched_observation = _resolve(
        _existing_491578(),
        _observation_491578(
            courier_id="999",
            courier_id_at_observation="492",
        ),
    )
    mismatched_generation = _resolve(
        _existing_491578(),
        _observation_491578(courier_id_at_observation="999"),
    )

    assert mismatched_observation.outcome is ResolutionOutcome.SUPPRESS
    assert mismatched_observation.reason == "courier_generation_changed"
    assert mismatched_generation.outcome is ResolutionOutcome.SUPPRESS
    assert mismatched_generation.reason == "courier_generation_changed"


def test_event_key_binds_full_proof_not_only_target_time():
    first = _resolve(_existing_491578(), _observation_491578()).event
    second = _resolve(
        _existing_491578(),
        _observation_491578(
            observed_at="2026-08-01T18:50:58+02:00",
        ),
    ).event
    third = _resolve(
        _existing_491578(),
        _observation_491578(observed_prep_minutes=61),
    ).event

    keys = {
        first["payload"]["committed_pickup_event_key"],
        second["payload"]["committed_pickup_event_key"],
        third["payload"]["committed_pickup_event_key"],
    }
    assert len(keys) == 3


def test_preproposal_both_authority_flags_off_is_exact_legacy_path(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(dp.C, "decision_flag", lambda _name: False)
    fresh_time = {
        "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
        "status_id": 2,
        "prep_minutes": 60,
        "zmiana_czasu_odbioru": False,
    }

    with patch.object(
        sm, "get_order", side_effect=AssertionError("OFF state read")
    ) as get_order, \
         patch.object(committed_pickup_apply, "apply_event") as durable_apply, \
         patch.object(event_bus, "emit_audit", return_value="legacy") as emit, \
         patch.object(sm, "update_from_event", return_value=None) as apply:
        accepted = dp._v327_emit_pre_recheck_event(
            "491578",
            "492",
            "2026-08-01T19:16:00+02:00",
            "2026-08-01T19:21:00+02:00",
            "19:21",
            datetime.fromisoformat("2026-08-01T18:50:57+02:00"),
            fresh_time=fresh_time,
        )

    assert accepted is True
    get_order.assert_not_called()
    durable_apply.assert_not_called()
    emit.assert_called_once()
    apply.assert_called_once()
    legacy_event = apply.call_args.args[0]
    assert set(legacy_event["payload"]) == {
        "oid",
        "courier_id",
        "old_ck_iso",
        "old_ck_hhmm",
        "new_ck_iso",
        "new_ck_hhmm",
        "delta_min",
        "source",
    }


def test_watcher_pickup_both_authority_flags_off_is_exact_legacy_path(
    monkeypatch,
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = pw._diff_pickup_time(
        _existing_491578(),
        {
            "pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
            "prep_minutes": 60,
            "decision_deadline": "2026-08-01T18:26:00+02:00",
            "zmiana_czasu_odbioru": False,
            "status_id": 2,
        },
        oid="491578",
    )

    assert event is not None
    assert event["event_type"] == "PICKUP_TIME_UPDATED"
    assert "committed_authority" not in event["payload"]
    assert event["payload"]["pickup_time_revision_at_observation"] == 0
    assert set(event["payload"]) == {
        "oid",
        "courier_id",
        "old_pickup_at_warsaw",
        "new_pickup_at_warsaw",
        "old_prep_minutes",
        "new_prep_minutes",
        "new_decision_deadline",
        "new_zmiana_czasu_odbioru",
        "delta_min",
        "source",
        "assignment_event_id_at_observation",
        "courier_id_at_observation",
        "pickup_time_revision_at_observation",
    }
    sentinel = object()
    with patch.object(
        pw, "_emit_and_apply_state", return_value=sentinel
    ) as legacy_apply:
        assert pw._apply_time_update_event("491578", event) is sentinel
    legacy_apply.assert_called_once()


def test_ordinary_pickup_off_keeps_predeploy_durable_event_key(monkeypatch):
    """Dark deploy cannot fork an already durable legacy pickup transition."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = pw._diff_pickup_time(
        _existing_491578(),
        {
            "pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
            "prep_minutes": 60,
            "decision_deadline": "2026-08-01T18:26:00+02:00",
            "zmiana_czasu_odbioru": False,
            "status_id": 2,
        },
        oid="491578",
    )

    assert event is not None
    assert pw._time_update_event_key("491578", event) == (
        "491578_PICKUP_TIME_UPDATED_100_to_"
        "e59620545053fcbf88922c88f2ee6651bd499b3e2959326fc06357f7f2371a9d"
    )


def test_stripped_coordinator_pickup_cannot_degrade_to_legacy_writer():
    """Both canonical and observed source remain reserved after proof stripping."""
    from dispatch_v2 import state_machine as sm

    current = _existing_491578()
    forged = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": current["pickup_at_warsaw"],
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "source": "rutcom_pickup_field",
            "observed_source": "coordinator_force",
        },
    }

    assert sm.event_effect_status(forged, current) == "superseded"


@pytest.mark.parametrize(
    "authority_key",
    [
        "committed_authority",
        "committed_authority_proof",
        "committed_pickup_event_key",
        "committed_authority_receipt_id",
        "committed_pickup_panel_baseline_at_observation",
        "committed_ck_panel_baseline_at_observation",
        "observed_source",
        "observed_at",
        "manual_ck_edit_passthrough",
    ],
)
def test_null_or_remaining_authority_key_cannot_degrade_to_legacy_writer(
    authority_key,
):
    """Presence, not truthiness, reserves a partially corrupted envelope."""
    from dispatch_v2 import state_machine as sm

    current = _existing_491578()
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": current["pickup_at_warsaw"],
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "source": "panel_re_check",
            authority_key: None,
        },
    }

    assert pickup_event_has_authority_artifact(event) is True
    assert sm.event_effect_status(event, current) == "superseded"


def test_null_durable_authority_marker_cannot_degrade_to_legacy_writer():
    from dispatch_v2 import state_machine as sm

    current = _existing_491578()
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": current["pickup_at_warsaw"],
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "source": "panel_re_check",
        },
        "committed_authority_attestation": None,
    }

    assert pickup_event_has_authority_artifact(event) is True
    assert sm.event_effect_status(event, current) == "superseded"


@pytest.mark.parametrize(
    "event_key",
    [
        "committed_invalidates_view_authorized",
        "saved_plans_authorized",
        "czasowka_reclaim_shadow_authorized",
        "czasowka_reclaim_live_authorized",
    ],
)
def test_generic_durable_marker_does_not_relabel_legacy_pickup(event_key):
    """Downstream snapshots are generic; authority attestation seals them."""
    from dispatch_v2 import state_machine as sm

    current = _existing_491578()
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": current["pickup_at_warsaw"],
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "source": "panel_re_check",
        },
        event_key: True,
    }

    assert pickup_event_has_authority_artifact(event) is False
    assert sm.event_effect_status(event, current) == "pending"


def test_committed_durable_identity_cannot_degrade_after_payload_stripping():
    from dispatch_v2 import state_machine as sm

    current = _existing_491578()
    event = json.loads(
        json.dumps(_resolve(current, _observation_491578()).event)
    )
    committed_event_id = event.pop("event_id_hint")
    event["event_id"] = committed_event_id
    event["payload"] = {
        "old_pickup_at_warsaw": current["pickup_at_warsaw"],
        "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "source": "panel_re_check",
    }

    assert pickup_event_has_authority_artifact(event) is True
    assert sm.event_effect_status(event, current) == "superseded"


@pytest.mark.parametrize("receipt_forward_enabled", [False, True])
def test_valid_claimed_elastic_coordinator_pickup_keeps_apply_contract(
    tmp_path, monkeypatch, receipt_forward_enabled
):
    """Reserved source stays valid for active elastic work after exact claim."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        TIME_EVENT_CAS_SCHEMA_FIELD,
    )

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=True,
            rutcom_forward_authority_enabled=receipt_forward_enabled,
            passive_guard_enabled=True,
        ),
    )
    current = {
        "order_id": "elastic-491578",
        "status": "assigned",
        "courier_id": "492",
        "order_type": "elastic",
        "prep_minutes": 20,
        "pickup_at_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_hhmm": "19:16",
        "assignment_event_id": "elastic-assignment",
    }
    assert ctr.enqueue(["elastic-491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["elastic-491578"]
    event = pw._diff_pickup_time(
        current,
        {
            "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "prep_minutes": 20,
        },
        oid="elastic-491578",
        deliberate=True,
        authority_receipt=receipt,
        policy_snapshot=_panel_policy(forward=not receipt_forward_enabled),
    )

    assert event is not None
    assert event["payload"]["source"] == "coordinator_force"
    assert "committed_authority" not in event["payload"]
    assert (
        TIME_EVENT_CAS_SCHEMA_FIELD in event["payload"]
    ) is receipt_forward_enabled
    claimed = ctr.claim_receipt(
        receipt, order_id="elastic-491578", event=event
    )
    assert claimed is not None
    assert ctr.verify_claimed_event(event)
    assert sm.event_effect_status(event, current) == "pending"

    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    sm.upsert_order("elastic-491578", current, event="COURIER_ASSIGNED")

    assert sm.update_from_event(event) is not None
    stored = sm.get_order("elastic-491578")
    assert stored["pickup_at_warsaw"] == "2026-08-01T19:21:00+02:00"


def test_stripped_forward_authority_artifact_cannot_degrade_to_legacy_writer(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    forged = json.loads(
        json.dumps(_resolve(_existing_491578(), _observation_491578()).event)
    )
    del forged["payload"]["committed_authority"]
    forged["committed_authority_attestation"] = {
        "schema": "committed_pickup_outbox_attestation.v1",
        "authority": "rutcom_forward_commitment",
        "event_key": forged["payload"]["committed_pickup_event_key"],
        "core_sha256": "stripped",
    }

    assert sm.event_effect_status(forged, _existing_491578()) == "superseded"
    assert sm.update_from_event(forged) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:16"


def test_live_manual_flag_alone_keeps_ordinary_pickup_on_legacy_contract(
    tmp_path, monkeypatch
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(
        sm,
        "decision_flag",
        lambda name: name
        == "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
    )
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_PICKUP_TIME_MIRRORS_CK"
            else True
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    event = pw._diff_pickup_time(
        _existing_491578(),
        {
            "pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
            "status_id": 2,
        },
        oid="491578",
    )

    assert event is not None
    assert "committed_authority" not in event["payload"]
    assert sm.update_from_event(event) is not None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:26:00+02:00")
    assert stored["czas_kuriera_warsaw"].endswith("T19:16:00+02:00")


def test_small_ck_delta_keeps_exact_legacy_noise_floor_when_authority_off(
    monkeypatch,
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    event = pw._diff_czas_kuriera(
        _existing_491578(),
        {
            "czas_kuriera_warsaw": "2026-08-01T19:17:00+02:00",
            "czas_kuriera_hhmm": "19:17",
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "status_id": 2,
        },
        oid="491578",
    )

    assert event is None


def test_legacy_pickup_state_write_advances_revision_without_authority_schema(
    tmp_path, monkeypatch
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    pristine = _existing_491578()
    pristine.pop("pickup_time_revision")
    sm.upsert_order("491578", pristine, event="COURIER_ASSIGNED")
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = pw._diff_pickup_time(
        pristine,
        {"pickup_at_warsaw": "2026-08-01T19:26:00+02:00"},
        oid="491578",
    )

    assert sm.update_from_event(event) is not None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:26:00+02:00")
    assert stored["pickup_time_revision"] == 1
    assert "committed_pickup_authority" not in stored


def test_pickup_writer_classifies_post_event_prep_before_ck_mirror(
    tmp_path, monkeypatch
):
    """A pickup event promoting prep 20→60 cannot create split truth."""
    from dispatch_v2 import state_machine as sm

    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    oid = "elastic-promoted-to-time-order"
    sm.upsert_order(
        oid,
        {
            "order_id": oid,
            "status": "assigned",
            "courier_id": "492",
            "order_type": "elastic",
            "prep_minutes": 20,
            "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
            "czas_kuriera_warsaw": "2026-08-02T14:00:00+02:00",
            "czas_kuriera_hhmm": "14:00",
        },
        event="COURIER_ASSIGNED",
    )
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
            "new_pickup_at_warsaw": "2026-08-02T14:15:00+02:00",
            "old_prep_minutes": 20,
            "new_prep_minutes": 60,
            "source": "panel_re_check",
        },
    }

    assert sm.update_from_event(event) is not None
    stored = sm.get_order_strict(oid)
    assert stored["prep_minutes"] == 60
    assert stored["pickup_at_warsaw"] == "2026-08-02T14:15:00+02:00"
    assert stored["czas_kuriera_warsaw"] == "2026-08-02T14:15:00+02:00"
    assert stored["czas_kuriera_hhmm"] == "14:15"


def test_legacy_coordinator_source_without_authority_is_rejected_at_state(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    forged = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "oid": "491578",
            "courier_id": "492",
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:26:00+02:00",
            "delta_min": 10.0,
            "source": "coordinator_force",
        },
    }

    assert sm.event_effect_status(forged) == "superseded"
    assert sm.update_from_event(forged) is None
    assert sm.get_order("491578")["pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )


def test_old_boolean_cannot_authorize_event_after_flag_off(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    event = _resolve(_existing_491578(), _observation_491578()).event
    event["committed_authority_authorized"] = True
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    assert sm.update_from_event(event) is None
    assert sm.get_order("491578")["pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )


def test_forward_authority_fails_closed_without_passive_guard(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import state_machine as sm

    pure = _resolve(
        _existing_491578(),
        _observation_491578(),
        passive_guard_enabled=False,
    )
    assert pure.outcome is ResolutionOutcome.SUPPRESS
    assert pure.reason == "authority_requires_passive_guard"

    _seed_state_491578(sm, tmp_path, monkeypatch)
    canonical = _resolve(
        _existing_491578(), _observation_491578()
    ).event
    monkeypatch.setattr(
        apply_boundary.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )

    with pytest.raises(ValueError, match="requires passive guard"):
        apply_boundary.apply_event(
            canonical,
            authority_policy=_panel_policy(passive=False),
        )

    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    assert sm.update_from_event(canonical) is None
    assert sm.get_order("491578")["pickup_at_warsaw"].endswith(
        "T19:15:58+02:00"
    )


def test_pickup_revision_blocks_aba_retry(tmp_path, monkeypatch):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)

    first_leg = _resolve(
        _existing_491578(),
        _observation_491578(
            new_ck_iso="2026-08-01T19:30:00+02:00",
            new_ck_hhmm="19:30",
        ),
    ).event
    assert first_leg is not None
    assert sm.update_from_event(first_leg) is not None

    after_first = sm.get_order("491578")
    second_leg = _resolve(
        after_first,
        _observation_491578(
            old_ck_iso="2026-08-01T19:30:00+02:00",
            old_ck_hhmm="19:30",
            new_ck_iso="2026-08-01T19:15:58+02:00",
            new_ck_hhmm="19:15",
            new_zmiana_czasu_odbioru=True,
            observed_status_id=3,
            pickup_time_revision_at_observation=1,
        ),
    ).event
    assert second_leg is not None
    assert sm.update_from_event(second_leg) is not None

    assert sm.event_effect_status(first_leg) == "superseded"
    assert sm.update_from_event(first_leg) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:15:58+02:00")
    assert stored["pickup_time_revision"] == 2


def test_legacy_aba_before_first_authority_apply_supersedes_pending_event(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import durable_event_apply
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )
    monkeypatch.setattr(apply_boundary.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    pending_event = _resolve(
        _existing_491578(), _observation_491578()
    ).event
    real_update = sm.update_from_event

    def crash_before_first_authority_state(_event):
        raise RuntimeError("synthetic crash before authority state apply")

    monkeypatch.setattr(sm, "update_from_event", crash_before_first_authority_state)
    first = apply_boundary.apply_event(
        pending_event, authority_policy=_panel_policy()
    )
    assert first.state_ready is False
    assert event_bus.get_state_apply_outbox(first.event_id)[
        "state_status"
    ] == "pending"

    monkeypatch.setattr(sm, "update_from_event", real_update)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)

    def legacy(old_value: str, new_value: str) -> dict:
        return {
            "event_type": "PICKUP_TIME_UPDATED",
            "order_id": "491578",
            "courier_id": "492",
            "payload": {
                "oid": "491578",
                "courier_id": "492",
                "old_pickup_at_warsaw": old_value,
                "new_pickup_at_warsaw": new_value,
                "delta_min": 10.0,
                "source": "panel_re_check",
            },
        }

    a = "2026-08-01T19:15:58+02:00"
    c = "2026-08-01T19:26:00+02:00"
    assert sm.update_from_event(legacy(a, c)) is not None
    assert sm.update_from_event(legacy(c, a)) is not None
    assert sm.get_order("491578")["pickup_time_revision"] == 2

    counts = durable_event_apply.drain_pending(
        state_update_fn=sm.update_from_event,
        effect_status_fn=sm.event_effect_status,
        get_order_fn=sm.get_order_strict,
        downstream_fn=lambda _event: None,
    )

    assert counts["superseded"] == 1
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"] == a
    assert stored["pickup_time_revision"] == 2
    assert stored.get("committed_pickup_authority") is None


def test_lifecycle_lock_makes_pickup_revision_a_real_concurrent_cas(
    tmp_path, monkeypatch
):
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    events = [
        _resolve(
            _existing_491578(),
            _observation_491578(
                new_ck_iso=f"2026-08-01T19:{minute}:00+02:00",
                new_ck_hhmm=f"19:{minute}",
            ),
        ).event
        for minute in ("21", "26")
    ]
    assert all(events)

    start = threading.Barrier(3)
    release_first = threading.Event()
    overlap = threading.Event()
    active_lock = threading.Lock()
    active = 0
    real_status = sm._pickup_time_event_status

    def observed_status(event, current):
        nonlocal active
        with active_lock:
            active += 1
            first = active == 1
            if active > 1:
                overlap.set()
                release_first.set()
        if first:
            # Z poprawnym outer lifecycle lockiem drugi wątek nie może wejść
            # do oracle przed zakończeniem pierwszego. Mutacja usuwająca
            # dekorator wpuszcza go tutaj i ustawia overlap bez wyścigu testu.
            release_first.wait(timeout=0.5)
        try:
            return real_status(event, current)
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(sm, "_pickup_time_event_status", observed_status)
    results = []

    def apply(candidate):
        start.wait()
        results.append(sm.update_from_event(candidate))

    threads = [threading.Thread(target=apply, args=(event,)) for event in events]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    assert not overlap.is_set()
    assert sum(result is not None for result in results) == 1
    stored = sm.get_order("491578")
    assert stored["pickup_time_revision"] == 1
    assert stored["pickup_at_warsaw"] in {
        "2026-08-01T19:21:00+02:00",
        "2026-08-01T19:26:00+02:00",
    }


def test_durable_boundary_canonicalizes_raw_ck_before_outbox(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )
    monkeypatch.setattr(
        apply_boundary.C, "flag", _authority_runtime_flag
    )
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    raw = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": _observation_491578(),
    }

    outcome = apply_boundary.apply_event(
        raw, authority_policy=_panel_policy()
    )
    row = event_bus.get_state_apply_outbox(outcome.event_id)
    stored = sm.get_order("491578")

    assert outcome.state_ready is True
    assert outcome.downstream_executed is True
    assert row["state_event"]["event_type"] == "PICKUP_TIME_UPDATED"
    assert row["state_event"].get("committed_authority_attestation")
    assert apply_boundary.verify_durable_authority_attestation(
        row["state_event"]
    )
    assert stored["last_lifecycle_event_id_pickup_time_updated"] == (
        outcome.event_id
    )
    assert "last_lifecycle_event_id_czas_kuriera_updated" not in stored


def test_exact_outbox_attestation_finishes_after_authority_flag_turns_off(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import durable_event_apply
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    canonical = _resolve(
        _existing_491578(), _observation_491578()
    ).event
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )
    monkeypatch.setattr(
        apply_boundary.C, "flag", _authority_runtime_flag
    )
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    real_update = sm.update_from_event

    def crash_before_state(_event):
        raise RuntimeError("synthetic crash after durable receipt")

    monkeypatch.setattr(sm, "update_from_event", crash_before_state)
    policy = CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
        passive_guard_enabled=True,
    )
    first = apply_boundary.apply_event(
        canonical, authority_policy=policy
    )
    row = event_bus.get_state_apply_outbox(first.event_id)
    assert first.state_ready is False
    assert row["state_status"] == "pending"
    assert row["state_event"].get("committed_authority_attestation")
    assert row["state_event"].get(
        "committed_time_policy_snapshot"
    ) == {
        "schema": "committed_pickup.policy_snapshot.v1",
        "producer": "panel_watcher",
        "manual_passthrough_enabled": True,
        "rutcom_forward_authority_enabled": True,
        "passive_guard_enabled": True,
    }

    monkeypatch.setattr(sm, "update_from_event", real_update)
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", lambda _name: False
    )
    def unavailable_decision_flag(name, *_args, **_kwargs):
        if name in {
            "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
            "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
        }:
            raise RuntimeError("later flag store unavailable")
        return False

    def unavailable_runtime_flag(name, default=None, *_args, **_kwargs):
        if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD":
            raise RuntimeError("later flag store unavailable")
        return default

    monkeypatch.setattr(sm, "decision_flag", unavailable_decision_flag)
    monkeypatch.setattr(sm, "flag", unavailable_runtime_flag)
    counts = durable_event_apply.drain_pending(
        state_update_fn=sm.update_from_event,
        effect_status_fn=sm.event_effect_status,
        get_order_fn=sm.get_order_strict,
        downstream_fn=lambda _event: None,
    )

    assert counts["state_ready"] == 1
    assert counts["failed"] == 0
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["pickup_time_revision"] == 1


def test_outbox_attestation_binds_every_authorization_marker(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import event_bus
    from dispatch_v2 import state_machine as sm

    events_db = _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    canonical = _resolve(
        _existing_491578(), _observation_491578()
    ).event
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )
    monkeypatch.setattr(apply_boundary.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)

    def crash_before_state(_event):
        raise RuntimeError("synthetic crash after durable receipt")

    monkeypatch.setattr(sm, "update_from_event", crash_before_state)
    first = apply_boundary.apply_event(
        canonical, authority_policy=_panel_policy()
    )
    with sqlite3.connect(events_db) as conn:
        raw = conn.execute(
            "SELECT state_event FROM state_apply_outbox WHERE event_id = ?",
            (first.event_id,),
        ).fetchone()[0]
        pristine = json.loads(raw)
        assert apply_boundary.verify_durable_authority_attestation(
            pristine
        ) is True
        corrupted = dict(pristine)
        marker = "saved_plans_authorized"
        corrupted[marker] = not corrupted[marker]
        conn.execute(
            "UPDATE state_apply_outbox SET state_event = ? WHERE event_id = ?",
            (
                json.dumps(corrupted, ensure_ascii=False, sort_keys=True),
                first.event_id,
            ),
        )

    stored_event = event_bus.get_state_apply_outbox(first.event_id)[
        "state_event"
    ]
    assert apply_boundary.verify_durable_authority_attestation(
        stored_event
    ) is False
    assert sm.event_effect_status(
        stored_event, _existing_491578()
    ) == "superseded"


def test_exact_claim_applies_after_all_authority_guards_turn_off_pre_outbox(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    claimed_event, _claimed = _claim_coordinator_event(sm, ctr)

    monkeypatch.setattr(apply_boundary.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        apply_boundary.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )

    outcome = apply_boundary.apply_event(claimed_event)

    assert outcome.state_ready is True
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["pickup_time_revision"] == 1


def test_claim_replay_after_state_apply_resumes_exact_outbox_and_acks(
    tmp_path, monkeypatch
):
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(apply_boundary.C, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(apply_boundary.C, "flag", _authority_runtime_flag)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    claimed_event, claimed = _claim_coordinator_event(sm, ctr)
    first = apply_boundary.apply_event(claimed_event)
    assert first.state_ready is True
    assert ctr.current_receipt("491578") == claimed

    monkeypatch.setattr(apply_boundary.C, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(
        apply_boundary.C,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )
    monkeypatch.setattr(
        sm,
        "flag",
        lambda name, default=None: (
            False
            if name == "ENABLE_CZASOWKA_CK_PASSIVE_GUARD"
            else default
        ),
    )

    replay, did_ack = pw._replay_claimed_time_event(
        "491578", claimed, ctr
    )

    assert replay is not None and replay.state_ready is True
    assert replay.failure_stage is None
    assert did_ack is True
    assert ctr.current_receipt("491578") is None
    assert sm.get_order("491578")["pickup_time_revision"] == 1


def test_claim_losing_revision_cas_is_durably_superseded_and_promotes_successor(
    tmp_path, monkeypatch
):
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    claimed_event, claimed = _claim_coordinator_event(sm, ctr)

    # Legalny równoległy legacy writer wygrywa CAS zanim claim trafi do outboxa.
    legacy = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
            "delta_min": 2.03,
            "source": "panel_re_check",
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
        },
    }
    assert sm.update_from_event(legacy) is not None
    assert sm.get_order("491578")["pickup_time_revision"] == 1

    # Re-click nie może zginąć za przegranym headem.
    assert ctr.enqueue(["491578"], source="coordinator_console") == 1
    with_successor = ctr.current_receipt("491578")
    assert with_successor["request_id"] == claimed["request_id"]
    successor_id = with_successor["successor"]["request_id"]

    outcome, did_ack = pw._replay_claimed_time_event(
        "491578", with_successor, ctr
    )

    assert outcome.superseded is True
    row = event_bus.get_state_apply_outbox(outcome.event_id)
    assert row["state_status"] == "superseded"
    assert did_ack is True
    promoted = ctr.current_receipt("491578")
    assert promoted["request_id"] == successor_id
    assert promoted.get("claim") is None
    assert sm.get_order("491578")["pickup_at_warsaw"].endswith(
        "T19:18:00+02:00"
    )


def test_claimed_legacy_pickup_cannot_overwrite_newer_pickup_revision(
    tmp_path, monkeypatch
):
    """Exact transport claim nie zwalnia legacy eventu z lifecycle CAS."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {"order_type": "elastic", "prep_minutes": 20},
        event="TEST_ELASTIC",
    )
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    claimed_event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:10:00+02:00",
            "pickup_time_revision_at_observation": 0,
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
            "delta_min": -5.97,
            "source": "coordinator_force",
        },
    }
    claimed = ctr.claim_receipt(
        receipt,
        order_id="491578",
        event=claimed_event,
        continue_after_ack=True,
    )
    assert claimed is not None
    assert ctr.verify_claimed_event(claimed_event)

    newer = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
            "delta_min": 2.03,
            "source": "panel_re_check",
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
        },
    }
    assert sm.update_from_event(newer) is not None
    assert sm.get_order("491578")["pickup_time_revision"] == 1

    assert sm.event_effect_status(claimed_event) == "superseded"
    assert sm.update_from_event(claimed_event) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:18:00+02:00")
    assert stored["pickup_time_revision"] == 1


@pytest.mark.parametrize("event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"])
def test_pruned_order_terminalizes_every_exact_claimed_time_event(
    tmp_path, monkeypatch, event_type
):
    """Claim po legalnym prune kończy się superseded i zwalnia kolejkę."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    if event_type == "CZAS_KURIERA_UPDATED":
        payload = {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": 2.0,
            "source": "coordinator_force",
        }
    else:
        payload = {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
            "delta_min": 2.03,
            "source": "coordinator_force",
        }
    event = {
        "event_type": event_type,
        "order_id": "491578",
        "courier_id": "492",
        "payload": payload,
    }
    claimed = ctr.claim_receipt(
        receipt, order_id="491578", event=event
    )
    assert claimed is not None

    outcome, did_ack = pw._replay_claimed_time_event(
        "491578", claimed, ctr
    )

    assert outcome.superseded is True
    assert did_ack is True
    assert ctr.current_receipt("491578") is None


@pytest.mark.parametrize(
    "event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"]
)
def test_pruned_order_terminalizes_unclaimed_versioned_time_event(
    tmp_path, monkeypatch, event_type
):
    """A causal time event cannot remain retryable after its aggregate is gone."""
    from dispatch_v2 import durable_event_apply as dea
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    if event_type == "CZAS_KURIERA_UPDATED":
        payload = {
            "time_event_cas_schema": "time_update_cas.v1",
            "status_at_observation": "assigned",
            "courier_id_at_observation": "492",
            "assignment_event_id_at_observation": "assign-1",
            "ck_change_revision_at_observation": 0,
            "old_ck_iso": "2026-08-02T14:00:00+02:00",
            "old_ck_hhmm": "14:00",
            "new_ck_iso": "2026-08-02T14:05:00+02:00",
            "new_ck_hhmm": "14:05",
            "delta_min": 5.0,
            "source": "pre_proposal_recheck",
        }
    else:
        payload = {
            "time_event_cas_schema": "time_update_cas.v1",
            "status_at_observation": "assigned",
            "courier_id_at_observation": "492",
            "assignment_event_id_at_observation": "assign-1",
            "pickup_time_revision_at_observation": 0,
            "old_pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
            "new_pickup_at_warsaw": "2026-08-02T14:05:00+02:00",
            "delta_min": 5.0,
            "source": "panel_re_check",
        }
    event = {
        "event_type": event_type,
        "order_id": "gone",
        "courier_id": "492",
        "payload": payload,
    }

    assert sm.event_effect_status(event, current=None) == "superseded"
    outcome = pw._apply_time_update_event("gone", event)
    assert outcome.superseded is True
    assert dea.is_terminal_outcome(outcome) is True


def test_claim_waits_for_exact_downstream_terminal_before_ack(
    tmp_path, monkeypatch
):
    from dispatch_v2 import lifecycle_downstream
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    _claimed_event, claimed = _claim_coordinator_event(sm, ctr)

    def fail_downstream(_event):
        raise RuntimeError("injected downstream failure")

    monkeypatch.setattr(lifecycle_downstream, "apply", fail_downstream)
    first, first_acked = pw._replay_claimed_time_event(
        "491578", claimed, ctr
    )

    assert first.state_ready is True
    assert first.failure_stage == "downstream"
    assert first_acked is False
    assert ctr.current_receipt("491578") == claimed

    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    retry, retry_acked = pw._replay_claimed_time_event(
        "491578", claimed, ctr
    )

    assert retry.state_ready is True
    assert retry.failure_stage is None
    assert retry_acked is True
    assert ctr.current_receipt("491578") is None


def test_claim_for_pruned_order_is_terminally_superseded_not_poisoned(
    tmp_path, monkeypatch
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    state_path = _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    _claimed_event, claimed = _claim_coordinator_event(sm, ctr)

    # Symuluje długi crash i legalny prune już terminalnego rekordu.
    state_path.write_text("{}", encoding="utf-8")
    outcome, did_ack = pw._replay_claimed_time_event(
        "491578", claimed, ctr
    )

    assert outcome.superseded is True
    assert did_ack is True
    assert ctr.current_receipt("491578") is None


def test_watcher_tick_replays_exact_claim_after_order_was_pruned(
    tmp_path, monkeypatch
):
    """Recovery belongs to the durable queue, not to the current-state loop.

    A crash can happen after the coordinator receipt is claimed but before its
    outbox row exists.  If normal retention then removes the terminal order,
    the next watcher tick must still terminalize the exact claim and ACK it.
    """
    from dispatch_v2 import committed_pickup_apply
    from dispatch_v2 import event_bus
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    state_path = _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(pw, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(pw.C, "flag", _authority_runtime_flag)
    claimed_event, _claimed = _claim_coordinator_event(sm, ctr)
    claimed_event_key = committed_pickup_apply.time_update_event_key(
        "491578", claimed_event
    )

    # Crash before outbox creation, followed by legal terminal-state pruning.
    assert event_bus.get_latest_state_apply(
        claimed_event_key, "491578"
    ) is None
    state_path.write_text("{}", encoding="utf-8")

    stats = pw._diff_and_emit(
        {
            "order_ids": [],
            "assigned_ids": set(),
            "unassigned_ids": [],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
    )

    row = event_bus.get_latest_state_apply(claimed_event_key, "491578")
    assert row is not None and row["state_status"] == "superseded"
    assert ctr.current_receipt("491578") is None
    assert stats["errors"] == 0


@pytest.mark.parametrize(
    ("pickup_detection_enabled", "expected_events"),
    [(False, 0), (True, 1)],
)
def test_ENABLE_PICKUP_TIME_DETECTION_on_off_gates_real_watcher_pickup_path(
    tmp_path,
    monkeypatch,
    pickup_detection_enabled,
    expected_events,
):
    """The pre-existing pickup kill-switch still owns the real watcher seam.

    V12 changes force-claim ordering around this branch.  Pinning the whole
    ``_diff_and_emit`` path prevents an accidental force-recovery refactor from
    making ordinary pickup detection run while its production kill-switch is
    OFF (or from leaving the ON branch dead).
    """
    from dispatch_v2 import common as C
    from dispatch_v2 import durable_event_apply
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)

    monkeypatch.setattr(C, "ENABLE_V319G_CK_DETECTION", False)
    monkeypatch.setattr(
        C,
        "ENABLE_PICKUP_TIME_DETECTION",
        pickup_detection_enabled,
    )
    monkeypatch.setattr(C, "flag", lambda _name, default=None: False)
    monkeypatch.setattr(pw, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: False)
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )

    raw = {
        "id_kurier": 492,
        "id_status_zamowienia": 2,
    }
    normalized = {
        "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "prep_minutes": 60,
        "decision_deadline": "2026-08-01T18:21:00+02:00",
        "zmiana_czasu_odbioru": False,
        "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
        "czas_kuriera_hhmm": "19:16",
        "status_id": 2,
    }
    monkeypatch.setattr(
        pw,
        "fetch_order_details",
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        pw,
        "normalize_order",
        lambda _raw: normalized,
    )

    applied = []

    def record_time_update(_order_id, event, **_kwargs):
        applied.append(event)
        return SimpleNamespace(
            state_ready=True,
            downstream_executed=True,
            failure_stage=None,
            superseded=False,
        )

    monkeypatch.setattr(pw, "_apply_time_update_event", record_time_update)
    monkeypatch.setattr(
        durable_event_apply,
        "is_terminal_outcome",
        lambda _outcome: True,
    )

    stats = pw._diff_and_emit(
        {
            "order_ids": ["491578"],
            "assigned_ids": set(),
            "unassigned_ids": [],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    assert len(applied) == expected_events
    assert stats["errors"] == 0
    if pickup_detection_enabled:
        assert applied[0]["event_type"] == "PICKUP_TIME_UPDATED"
        assert applied[0]["payload"]["new_pickup_at_warsaw"].endswith(
            "T19:21:00+02:00"
        )


@pytest.mark.parametrize(
    ("forward_authority_enabled", "expected_detectors"),
    [(False, []), (True, ["ck", "pickup"])],
)
def test_forward_authority_forces_both_recovery_detectors_on_real_tick(
    tmp_path,
    monkeypatch,
    forward_authority_enabled,
    expected_detectors,
):
    """The new owner can recover a shell even with both legacy flags OFF.

    A crash may land after durable NEW_ORDER created the aggregate shell but
    before its synchronous canonical initializer ran.  The next ordinary tick
    must therefore fetch every active czasowka and run both halves of the same
    owner while the forward flag is ON.  OFF preserves the exact legacy cost
    and routing boundary.
    """
    from dispatch_v2 import common as C
    from dispatch_v2 import panel_detail_prefetch
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import parse_continuity_guard
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)

    monkeypatch.setattr(C, "ENABLE_V319G_CK_DETECTION", False)
    monkeypatch.setattr(C, "ENABLE_PICKUP_TIME_DETECTION", False)

    def decision(name):
        return bool(
            forward_authority_enabled
            and name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
        )

    monkeypatch.setattr(C, "decision_flag", decision)
    monkeypatch.setattr(C, "flag", lambda _name, default=None: False)
    monkeypatch.setattr(pw, "decision_flag", decision)
    monkeypatch.setattr(sm, "decision_flag", decision)
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: False)
    monkeypatch.setattr(
        panel_detail_prefetch,
        "prefetch_details",
        lambda *_args, **_kwargs: ({}, {"prefetch_enabled": False}),
    )
    monkeypatch.setattr(
        parse_continuity_guard,
        "evaluate",
        lambda *_args, **_kwargs: {
            "freeze_new": False,
            "suspicious": False,
        },
    )
    monkeypatch.setattr(
        pw,
        "_heal_missing_order_details",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pw,
        "fetch_order_details",
        lambda *_args, **_kwargs: {
            "id_kurier": 492,
            "id_status_zamowienia": 2,
        },
    )
    monkeypatch.setattr(
        pw,
        "normalize_order",
        lambda _raw: {
            "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "prep_minutes": 60,
            "decision_deadline": "2026-08-01T18:21:00+02:00",
            "zmiana_czasu_odbioru": False,
            "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
            "status_id": 2,
        },
    )

    detectors = []

    def ck_detector(*_args, **_kwargs):
        detectors.append("ck")
        return None

    def pickup_detector(*_args, **_kwargs):
        detectors.append("pickup")
        return None

    monkeypatch.setattr(pw, "_diff_czas_kuriera", ck_detector)
    monkeypatch.setattr(pw, "_diff_pickup_time", pickup_detector)

    stats = pw._diff_and_emit(
        {
            "order_ids": ["491578"],
            "assigned_ids": set(),
            "unassigned_ids": [],
            "rest_names": {},
            "courier_packs": {},
            "courier_load": {},
            "html_times": {},
            "closed_ids": set(),
            "pickup_addresses": {},
            "delivery_addresses": {},
        },
        csrf="test",
        _state_outbox_sweeper_on=True,
    )

    assert detectors == expected_detectors
    assert stats["errors"] == 0


def test_stale_unclaimed_pickup_event_cannot_overwrite_newer_authority_commit(
    tmp_path, monkeypatch
):
    """Każdy nowy pickup writer respektuje tę samą rewizję, nie tylko claim."""
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    stale_legacy = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
            "pickup_time_revision_at_observation": 0,
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
            "delta_min": 2.03,
            "source": "panel_re_check",
        },
    }
    authority = _resolve(
        _existing_491578(), _observation_491578()
    ).event

    assert sm.update_from_event(authority) is not None
    committed = sm.get_order("491578")
    assert committed["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert committed["pickup_time_revision"] == 1
    assert committed["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )

    assert sm.event_effect_status(stale_legacy) == "superseded"
    assert sm.update_from_event(stale_legacy) is None
    stored = sm.get_order("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["pickup_time_revision"] == 1
    assert stored["committed_pickup_authority"] == (
        "rutcom_forward_commitment"
    )


def test_claimed_elastic_ck_cannot_overwrite_newer_ck_generation(
    tmp_path, monkeypatch
):
    """Crash-retry starego kliknięcia przegrywa z nowszym legalnym CK."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "order_type": "elastic",
            "prep_minutes": 20,
            "v319g_ck_change_count": 0,
        },
        event="TEST_ELASTIC",
    )
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    claimed_event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:30:00+02:00",
            "new_ck_hhmm": "19:30",
            "delta_min": 14.0,
            "source": "coordinator_force",
        },
    }
    claimed = ctr.claim_receipt(
        receipt,
        order_id="491578",
        event=claimed_event,
        continue_after_ack=True,
    )
    assert claimed is not None
    newer = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:20:00+02:00",
            "new_ck_hhmm": "19:20",
            "delta_min": 4.0,
            "source": "panel_re_check",
        },
    }
    assert sm.update_from_event(newer) is not None
    assert sm.get_order("491578")["v319g_ck_change_count"] == 1

    assert sm.event_effect_status(claimed_event) == "superseded"
    assert sm.update_from_event(claimed_event) is None
    stored = sm.get_order("491578")
    assert stored["czas_kuriera_hhmm"] == "19:20"
    assert stored["v319g_ck_change_count"] == 1


def test_versioned_elastic_ck_cas_rejects_aba_cycle(
    tmp_path, monkeypatch
):
    """Monotoniczna rewizja, nie samo old_ck, jest oracle dla A→C→A."""
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        build_time_event_cas_snapshot,
    )

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "order_type": "elastic",
            "prep_minutes": 20,
            "v319g_ck_change_count": 0,
        },
        event="TEST_ELASTIC",
    )
    original = sm.get_order("491578")
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    claimed_event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": original["czas_kuriera_warsaw"],
            "old_ck_hhmm": original["czas_kuriera_hhmm"],
            "new_ck_iso": "2026-08-01T19:30:00+02:00",
            "new_ck_hhmm": "19:30",
            "delta_min": 14.0,
            "source": "coordinator_force",
            **build_time_event_cas_snapshot(
                original, "CZAS_KURIERA_UPDATED"
            ),
        },
    }
    claimed = ctr.claim_receipt(
        receipt, order_id="491578", event=claimed_event
    )
    assert claimed is not None

    def apply_ck(current, target, hhmm):
        event = {
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": "491578",
            "courier_id": "492",
            "payload": {
                "old_ck_iso": current["czas_kuriera_warsaw"],
                "old_ck_hhmm": current["czas_kuriera_hhmm"],
                "new_ck_iso": target,
                "new_ck_hhmm": hhmm,
                "delta_min": 0.0,
                "source": "coordinator_edit",
                **build_time_event_cas_snapshot(
                    current, "CZAS_KURIERA_UPDATED"
                ),
            },
        }
        assert sm.update_from_event(event) is not None

    apply_ck(
        original,
        "2026-08-01T19:20:00+02:00",
        "19:20",
    )
    after_c = sm.get_order("491578")
    apply_ck(
        after_c,
        "2026-08-01T19:16:00+02:00",
        "19:16",
    )
    after_aba = sm.get_order("491578")
    assert after_aba["v319g_ck_change_count"] == 2
    assert after_aba["czas_kuriera_hhmm"] == "19:16"

    assert sm.event_effect_status(claimed_event) == "superseded"
    assert sm.update_from_event(claimed_event) is None
    assert sm.get_order("491578")["czas_kuriera_hhmm"] == "19:16"


def test_watcher_versioned_ck_payload_and_key_bind_revision_when_flag_on(
    monkeypatch,
):
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2.committed_pickup_authority import (
        CK_CHANGE_REVISION_OBSERVATION_FIELD,
        RUTCOM_FORWARD_AUTHORITY_FLAG,
        TIME_EVENT_CAS_SCHEMA,
        TIME_EVENT_CAS_SCHEMA_FIELD,
    )

    monkeypatch.setattr(
        pw.C,
        "decision_flag",
        lambda name: name == RUTCOM_FORWARD_AUTHORITY_FLAG,
    )
    old = {
        **_existing_491578(),
        "order_type": "elastic",
        "prep_minutes": 20,
        "assignment_event_id": "assign-7",
        "v319g_ck_change_count": 7,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:30:00+02:00",
        "czas_kuriera_hhmm": "19:30",
    }

    event = pw._diff_czas_kuriera(old, fresh, "491578")
    payload = event["payload"]
    assert payload[TIME_EVENT_CAS_SCHEMA_FIELD] == TIME_EVENT_CAS_SCHEMA
    assert payload[CK_CHANGE_REVISION_OBSERVATION_FIELD] == 7
    assert payload["assignment_event_id_at_observation"] == "assign-7"
    assert payload["courier_id_at_observation"] == "492"
    assert payload["status_at_observation"] == "assigned"

    next_generation = pw._diff_czas_kuriera(
        {**old, "v319g_ck_change_count": 8}, fresh, "491578"
    )
    assert pw._time_update_event_key("491578", event) != (
        pw._time_update_event_key("491578", next_generation)
    )


def test_raw_ck_canonicalization_uses_strict_state_read(
    tmp_path, monkeypatch
):
    """Fail-soft read nie może utrwalić legalnej czasówki jako raw CK."""
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import state_machine as sm

    _isolate_durable_bus(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(
        apply_boundary.C, "decision_flag", _authority_decision_flag
    )
    monkeypatch.setattr(apply_boundary.C, "flag", _authority_runtime_flag)
    real_get_order = sm.get_order
    real_get_order_strict = sm.get_order_strict
    reads = []

    def strict_read(oid):
        reads.append("strict")
        return real_get_order_strict(oid)

    def transient_soft_read(oid):
        reads.append("soft")
        if "strict" not in reads[:-1]:
            return None
        return real_get_order(oid)

    monkeypatch.setattr(sm, "get_order_strict", strict_read)
    monkeypatch.setattr(sm, "get_order", transient_soft_read)
    raw = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": _observation_491578(),
    }

    outcome = apply_boundary.apply_event(
        raw, authority_policy=_panel_policy()
    )

    assert reads[0] == "strict"
    assert outcome.state_ready is True
    assert outcome.superseded is False
    stored = sm.get_order_strict("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"


def test_retired_raw_ck_writer_and_effect_oracle_share_terminal_policy(
    tmp_path, monkeypatch
):
    """Handler-blocked raw writer nie może zostać wiecznym pending outboxem."""
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    raw = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "event_id": "retired-first-acceptance-durable",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": "2026-08-01T19:16:00+02:00",
            "old_ck_hhmm": "19:16",
            "new_ck_iso": "2026-08-01T19:18:00+02:00",
            "new_ck_hhmm": "19:18",
            "delta_min": 2.0,
            "source": "first_acceptance",
        },
    }

    assert sm.update_from_event(raw) is None
    assert sm.event_effect_status(raw) == "superseded"


def test_present_null_attestation_is_invalid_before_postcondition_shortcut(
    tmp_path, monkeypatch
):
    """Obecny klucz atestacji zawsze podlega walidacji, także gdy ma null."""
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    event = _resolve(_existing_491578(), _observation_491578()).event
    assert sm.update_from_event(event) is not None
    forged_retry = {
        **event,
        "event_id": "forged-null-attestation",
        "committed_authority_attestation": None,
        "czasowka_reclaim_live_authorized": not bool(
            event.get("czasowka_reclaim_live_authorized")
        ),
    }

    assert sm.event_effect_status(forged_retry) == "superseded"


def test_legacy_pickup_clears_every_partial_committed_provenance_field(
    tmp_path, monkeypatch
):
    """Czyszczenie provenance używa kanonicznego klasyfikatora całego artefaktu."""
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        COMMITTED_PICKUP_STATE_FIELDS,
        state_has_committed_pickup_artifact,
    )

    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "committed_pickup_authority": None,
            "committed_pickup_event_key": "orphaned-old-key",
        },
        event="TEST_PARTIAL_PROVENANCE",
    )
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    assert state_has_committed_pickup_artifact(sm.get_order("491578"))
    legacy = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "new_pickup_at_warsaw": "2026-08-01T19:18:00+02:00",
            "pickup_time_revision_at_observation": 0,
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
            "delta_min": 2.03,
            "source": "panel_re_check",
        },
    }

    assert sm.update_from_event(legacy) is not None
    stored = sm.get_order("491578")
    assert not state_has_committed_pickup_artifact(stored)
    assert all(stored.get(field) is None for field in COMMITTED_PICKUP_STATE_FIELDS)


@pytest.mark.parametrize(
    "mutation",
    ["drop_schema", "drop_revision", "null_schema"],
)
def test_partial_ck_cas_envelope_cannot_downgrade_to_legacy(mutation):
    """Każdy ślad v14 rezerwuje kopertę; uszkodzenie failuje przed outboxem."""
    from dispatch_v2.committed_pickup_apply import time_update_event_key
    from dispatch_v2.committed_pickup_authority import (
        CK_CHANGE_REVISION_OBSERVATION_FIELD,
        TIME_EVENT_CAS_SCHEMA_FIELD,
        build_time_event_cas_snapshot,
        time_event_cas_status,
    )

    current = {
        **_existing_491578(),
        "order_type": "elastic",
        "assignment_event_id": "assign-7",
        "v319g_ck_change_count": 7,
    }
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_ck_iso": current["czas_kuriera_warsaw"],
            "old_ck_hhmm": current["czas_kuriera_hhmm"],
            "new_ck_iso": "2026-08-01T19:30:00+02:00",
            "new_ck_hhmm": "19:30",
            "delta_min": 14.0,
            "source": "panel_re_check",
            **build_time_event_cas_snapshot(
                current, "CZAS_KURIERA_UPDATED"
            ),
        },
    }
    payload = event["payload"]
    if mutation == "drop_schema":
        payload.pop(TIME_EVENT_CAS_SCHEMA_FIELD)
    elif mutation == "drop_revision":
        payload.pop(CK_CHANGE_REVISION_OBSERVATION_FIELD)
    else:
        payload[TIME_EVENT_CAS_SCHEMA_FIELD] = None

    assert time_event_cas_status(current, event) == "superseded"
    with pytest.raises(ValueError, match="malformed time CAS envelope"):
        time_update_event_key("491578", event)


def test_v13_pickup_cas_cannot_apply_after_pickup_lifecycle_progressed():
    """Kompatybilna koperta v13 nadal respektuje terminalny lifecycle fence."""
    from dispatch_v2.committed_pickup_authority import time_event_cas_status

    current = {
        **_existing_491578(),
        "status": "picked_up",
        "picked_up_at": "2026-08-01T19:17:00+02:00",
        "pickup_time_revision": 0,
    }
    event = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "491578",
        "courier_id": "492",
        "payload": {
            "old_pickup_at_warsaw": current["pickup_at_warsaw"],
            "new_pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
            "pickup_time_revision_at_observation": 0,
            "assignment_event_id_at_observation": None,
            "courier_id_at_observation": "492",
            "source": "panel_re_check",
        },
    }

    assert time_event_cas_status(current, event) == "superseded"


def test_panel_new_order_uses_pre_io_policy_after_live_hot_on(
    tmp_path, monkeypatch
):
    """An OFF-started detail transaction cannot gain NEW_ORDER authority."""
    from dispatch_v2 import lifecycle_downstream
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
        NEW_ORDER_TIME_INTENT_FIELD,
    )

    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    # Runtime is already ON when persistence starts; the fetch began with OFF.
    monkeypatch.setattr(pw.C, "decision_flag", lambda _name: True)
    policy = CommittedPickupPolicySnapshot(
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=False,
        passive_guard_enabled=True,
        producer="panel_watcher",
    )
    oid = "off-started-new-order"

    outcome = pw._emit_and_apply_state(
        "NEW_ORDER",
        order_id=oid,
        payload={
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": "2026-08-02T19:21:00+02:00",
            "czas_kuriera_warsaw": "2026-08-02T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
        },
        event_id=f"{oid}_NEW_ORDER_first",
        committed_time_policy=policy,
    )

    assert outcome.state_event[
        NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD
    ] is False
    assert NEW_ORDER_TIME_INTENT_FIELD not in outcome.state_event
    stored = sm.get_order_strict(oid)
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"


@pytest.mark.parametrize(
    ("started_forward", "live_forward", "expected_authority"),
    [(False, True, False), (True, False, True)],
)
def test_panel_ck_recheck_policy_is_frozen_across_hot_flip(
    monkeypatch, started_forward, live_forward, expected_authority
):
    """The same panel policy lease governs fetch, resolve and apply."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    def live_decision(name):
        return bool(
            live_forward
            and name == "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
        )

    monkeypatch.setattr(sm, "decision_flag", live_decision)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    policy = CommittedPickupPolicySnapshot(
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=started_forward,
        passive_guard_enabled=True,
        producer="panel_watcher",
    )

    event = pw._diff_czas_kuriera(
        _existing_491578(),
        {
            "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "status_id": 2,
            "prep_minutes": 60,
            "decision_deadline": "2026-08-01T18:16:58+02:00",
            "observed_at": "2026-08-01T18:50:57+02:00",
        },
        oid="491578",
        policy_snapshot=policy,
    )

    assert bool(
        event
        and event["payload"].get("committed_authority")
        == "rutcom_forward_commitment"
    ) is expected_authority


def test_panel_pickup_producer_classifies_post_event_prep(monkeypatch):
    """Producer and state/preflight must agree on an elastic 20→time 60 event."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    current = {
        "order_id": "elastic-promoted-at-producer",
        "status": "assigned",
        "courier_id": "492",
        "order_type": "elastic",
        "prep_minutes": 20,
        "pickup_at_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_warsaw": "2026-08-02T14:00:00+02:00",
        "czas_kuriera_hhmm": "14:00",
        "pickup_time_revision": 0,
    }

    event = pw._diff_pickup_time(
        current,
        {
            "pickup_at_warsaw": "2026-08-02T14:15:00+02:00",
            "czas_kuriera_warsaw": "2026-08-02T14:15:00+02:00",
            "czas_kuriera_hhmm": "14:15",
            "prep_minutes": 60,
            "status_id": 2,
            "observed_at": "2026-08-02T13:30:00+02:00",
        },
        oid=current["order_id"],
    )

    assert event is not None
    assert event["payload"]["committed_authority"] == "rutcom_pickup_field"
    assert event["payload"]["new_prep_minutes"] == 60


def test_coordinator_ck_resolver_classifies_post_observation_prep(
    tmp_path, monkeypatch
):
    """A coordinator refresh may not bypass the coupled owner on 20→60."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=True,
            rutcom_forward_authority_enabled=True,
            passive_guard_enabled=True,
        ),
    )
    assert ctr.enqueue(["elastic-promoted-at-coordinator"]) == 1
    receipt = ctr.pending_with_receipts()[
        "elastic-promoted-at-coordinator"
    ]
    existing = {
        **_existing_491578(),
        "order_id": "elastic-promoted-at-coordinator",
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    payload = _observation_491578(
        oid=existing["order_id"],
        source="coordinator_force",
        authority_receipt=receipt,
        observed_prep_minutes=60,
        observed_at=receipt["requested_at"],
    )

    resolution = sm.resolve_czasowka_ck_observation(existing, payload)

    assert resolution.outcome is ResolutionOutcome.APPLY
    assert resolution.reason == "coordinator_receipt"
    assert resolution.event is not None
    assert resolution.event["event_type"] == "PICKUP_TIME_UPDATED"
    assert resolution.event["payload"]["new_prep_minutes"] == 60
    assert ctr.verify_claimed_event(resolution.event)


def test_off_started_coordinator_refresh_cannot_gain_authority_after_live_on(
    tmp_path, monkeypatch
):
    """The click-time OFF lease wins even when detail promotes 20→60."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=False,
            rutcom_forward_authority_enabled=False,
            passive_guard_enabled=True,
        ),
    )
    oid = "off-click-promoted-after-flip"
    assert ctr.enqueue([oid], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()[oid]

    # Runtime is now ON, but the already queued work owns the durable OFF lease.
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    existing = {
        **_existing_491578(),
        "order_id": oid,
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    payload = _observation_491578(
        oid=oid,
        source="coordinator_force",
        authority_receipt=receipt,
        observed_prep_minutes=60,
        observed_at=receipt["requested_at"],
    )

    resolution = sm.resolve_czasowka_ck_observation(existing, payload)

    assert resolution.outcome is ResolutionOutcome.SUPPRESS
    assert resolution.reason == "forward_authority_off"
    assert resolution.event is None
    assert ctr.current_receipt(oid) == receipt


def test_off_started_coordinator_refresh_remains_legacy_for_true_elastic(
    tmp_path, monkeypatch
):
    """The rollout exception preserves the old path only while class stays elastic."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=False,
            rutcom_forward_authority_enabled=False,
            passive_guard_enabled=True,
        ),
    )
    oid = "off-click-still-elastic"
    assert ctr.enqueue([oid], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()[oid]
    existing = {
        **_existing_491578(),
        "order_id": oid,
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    payload = _observation_491578(
        oid=oid,
        source="coordinator_force",
        authority_receipt=receipt,
        observed_prep_minutes=20,
        observed_at=receipt["requested_at"],
    )

    resolution = sm.resolve_czasowka_ck_observation(existing, payload)

    assert resolution.outcome is ResolutionOutcome.NOT_APPLICABLE
    assert resolution.reason == "not_czasowka"
    assert ctr.current_receipt(oid) == receipt


@pytest.mark.parametrize("forward_enabled", [False, True])
def test_coordinator_pickup_receipt_keeps_true_elastic_on_legacy_path(
    tmp_path, monkeypatch, forward_enabled
):
    """A queue receipt cannot turn an elastic pickup refresh into authority."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=False,
            rutcom_forward_authority_enabled=forward_enabled,
            passive_guard_enabled=True,
        ),
    )
    oid = f"elastic-pickup-receipt-{int(forward_enabled)}"
    assert ctr.enqueue([oid], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()[oid]
    existing = {
        **_existing_491578(),
        "order_id": oid,
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    payload = _observation_491578(
        oid=oid,
        source="coordinator_force",
        authority_receipt=receipt,
        observed_prep_minutes=20,
        observed_at=receipt["requested_at"],
        new_pickup_at_warsaw="2026-08-01T19:21:00+02:00",
    )
    assert sm.project_time_observation_order(existing, payload)[
        "order_type"
    ] == "elastic"

    resolution = sm.resolve_czasowka_pickup_observation(existing, payload)

    assert resolution.outcome is ResolutionOutcome.NOT_APPLICABLE
    assert resolution.reason == "not_czasowka"
    assert resolution.event is None
    assert ctr.current_receipt(oid) == receipt


@pytest.mark.parametrize("detector", ["ck", "pickup"])
@pytest.mark.parametrize(
    ("receipt_forward_enabled", "tick_forward_enabled", "expects_cas"),
    [(True, False, True), (False, True, False)],
)
def test_coordinator_elastic_fallback_cas_uses_click_policy(
    tmp_path,
    monkeypatch,
    detector,
    receipt_forward_enabled,
    tick_forward_enabled,
    expects_cas,
):
    """A hot flag flip cannot replace the immutable v6 click policy."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2.committed_pickup_authority import (
        TIME_EVENT_CAS_SCHEMA_FIELD,
    )

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=False,
            rutcom_forward_authority_enabled=receipt_forward_enabled,
            passive_guard_enabled=True,
        ),
    )
    oid = (
        f"elastic-{detector}-click-{int(receipt_forward_enabled)}-"
        f"tick-{int(tick_forward_enabled)}"
    )
    assert ctr.enqueue([oid], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()[oid]
    existing = {
        **_existing_491578(),
        "order_id": oid,
        "order_type": "elastic",
        "prep_minutes": 20,
    }
    fresh = {
        "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
        "czas_kuriera_hhmm": "19:21",
        "pickup_at_warsaw": "2026-08-01T19:21:00+02:00",
        "prep_minutes": 20,
        "status_id": 2,
        "observed_at": receipt["requested_at"],
    }
    tick_policy = CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=tick_forward_enabled,
        passive_guard_enabled=True,
    )

    if detector == "ck":
        event = pw._diff_czas_kuriera(
            existing,
            fresh,
            oid,
            deliberate=True,
            authority_receipt=receipt,
            policy_snapshot=tick_policy,
        )
    else:
        event = pw._diff_pickup_time(
            existing,
            fresh,
            oid,
            deliberate=True,
            authority_receipt=receipt,
            policy_snapshot=tick_policy,
        )

    assert event is not None
    assert event["payload"]["source"] == "coordinator_force"
    assert "committed_authority" not in event["payload"]
    assert (TIME_EVENT_CAS_SCHEMA_FIELD in event["payload"]) is expects_cas


def test_claim_cannot_override_coordinator_click_time_off_policy(
    tmp_path, monkeypatch
):
    """A durable claim is a journal, never a replacement authority bit."""
    from dispatch_v2 import committed_pickup_apply as apply_boundary
    from dispatch_v2 import lifecycle_downstream
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    _isolate_durable_bus(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            # This intentionally catches the too-broad generic
            # ``manual OR forward`` authority predicate.
            manual_passthrough_enabled=True,
            rutcom_forward_authority_enabled=False,
            passive_guard_enabled=True,
        ),
    )
    oid = "claimed-off-policy"
    existing = {
        **_existing_491578(),
        "order_id": oid,
    }
    sm.upsert_order(oid, existing, event="TEST_CLAIMED_OFF_POLICY")
    assert ctr.enqueue([oid], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()[oid]
    observation = _observation_491578(
        oid=oid,
        source="coordinator_force",
        authority_receipt=receipt,
        observed_at=receipt["requested_at"],
    )
    # Model a faulty sibling caller that tries to claim a canonical event
    # without first consuming the queue policy. Every downstream boundary
    # must still reject it from the exact receipt snapshot.
    crafted = resolve_czasowka_committed_observation(
        existing,
        observation,
        is_czasowka=True,
        passive_guard_enabled=True,
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
        coordinator_receipt_verified=True,
    )
    assert crafted.outcome is ResolutionOutcome.APPLY
    claimed = ctr.claim_receipt(
        receipt,
        order_id=oid,
        event=crafted.event,
    )
    assert claimed is not None

    def unavailable_flag_store(*_args, **_kwargs):
        raise RuntimeError("later flag store unavailable")

    monkeypatch.setattr(sm, "decision_flag", unavailable_flag_store)
    monkeypatch.setattr(sm, "flag", unavailable_flag_store)

    replay = sm.resolve_czasowka_ck_observation(
        existing,
        {**observation, "authority_receipt": claimed},
    )

    assert replay.outcome is ResolutionOutcome.SUPPRESS
    assert replay.reason == "claimed_receipt_policy_off"
    with pytest.raises(
        ValueError, match="coordinator policy cannot apply authority"
    ):
        apply_boundary.apply_event(crafted.event)
    assert sm.get_order_strict(oid)["czas_kuriera_hhmm"] == "19:16"


def test_panel_off_started_raw_ck_keeps_durable_policy_after_live_on(
    tmp_path, monkeypatch
):
    """Crash recovery must read the pre-fetch OFF lease, not the live flag."""
    from dispatch_v2 import lifecycle_downstream
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    )

    _seed_state_491578(sm, tmp_path, monkeypatch)
    _isolate_durable_bus(tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "czas_kuriera_warsaw": None,
            "czas_kuriera_hhmm": None,
        },
        event="TEST_CLEAR_INITIAL_CK",
    )
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    # The process is now ON, while this already-started request owns OFF.
    monkeypatch.setattr(sm, "decision_flag", _authority_decision_flag)
    monkeypatch.setattr(sm, "flag", _authority_runtime_flag)
    policy = CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=False,
        passive_guard_enabled=True,
    )
    current = sm.get_order_strict("491578")
    event = pw._diff_czas_kuriera(
        current,
        {
            "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
            "pickup_at_warsaw": current["pickup_at_warsaw"],
            "status_id": 2,
            "prep_minutes": 60,
            "observed_at": "2026-08-01T18:50:57+02:00",
        },
        oid="491578",
        policy_snapshot=policy,
    )

    assert event is not None
    assert event["event_type"] == "CZAS_KURIERA_UPDATED"
    outcome = pw._apply_time_update_event(
        "491578",
        event,
        policy_snapshot=policy,
    )

    assert outcome.state_ready is True
    assert outcome.state_event[
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD
    ]["rutcom_forward_authority_enabled"] is False
    stored = sm.get_order_strict("491578")
    assert stored["czas_kuriera_hhmm"] == "19:21"
    assert stored.get("committed_pickup_authority") is None
    assert sm.event_effect_status(outcome.state_event, stored) == "applied"


def test_panel_on_started_authority_finishes_after_live_off(
    tmp_path, monkeypatch
):
    """An ON lease remains sufficient through canonical seal and state apply."""
    from dispatch_v2 import lifecycle_downstream
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm

    _seed_state_491578(sm, tmp_path, monkeypatch)
    _isolate_durable_bus(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)
    monkeypatch.setattr(sm, "decision_flag", lambda _name: False)
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: False)
    policy = CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=True,
        passive_guard_enabled=True,
    )
    event = pw._diff_czas_kuriera(
        sm.get_order_strict("491578"),
        {
            "czas_kuriera_warsaw": "2026-08-01T19:21:00+02:00",
            "czas_kuriera_hhmm": "19:21",
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "status_id": 2,
            "prep_minutes": 60,
            "observed_at": "2026-08-01T18:50:57+02:00",
        },
        oid="491578",
        policy_snapshot=policy,
    )

    assert event["payload"]["committed_authority"] == (
        "rutcom_forward_commitment"
    )
    outcome = pw._apply_time_update_event(
        "491578",
        event,
        policy_snapshot=policy,
    )

    assert outcome.state_ready is True
    stored = sm.get_order_strict("491578")
    assert stored["pickup_at_warsaw"].endswith("T19:21:00+02:00")
    assert stored["czas_kuriera_hhmm"] == "19:21"


@pytest.mark.parametrize(
    "mutation",
    ["drop_producer", "wrong_schema", "non_boolean"],
)
def test_durable_time_policy_mutations_fail_closed(mutation):
    """Any partial lease remains reserved and cannot fall back to live flags."""
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
        deserialize_committed_time_policy,
        serialize_committed_time_policy,
    )

    snapshot = serialize_committed_time_policy(
        CommittedPickupPolicySnapshot(
            producer="panel_watcher",
            manual_passthrough_enabled=False,
            rutcom_forward_authority_enabled=False,
            passive_guard_enabled=True,
        )
    )
    if mutation == "drop_producer":
        snapshot.pop("producer")
    elif mutation == "wrong_schema":
        snapshot["schema"] = "committed_pickup.policy_snapshot.v0"
    else:
        snapshot["passive_guard_enabled"] = 1
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": "491578",
        "payload": {
            "source": "first_acceptance",
            "new_ck_iso": "2026-08-01T19:21:00+02:00",
            "new_ck_hhmm": "19:21",
        },
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD: snapshot,
    }

    with pytest.raises((TypeError, ValueError)):
        deserialize_committed_time_policy(snapshot)
    assert sm.event_effect_status(event, _existing_491578()) == "superseded"


def test_raw_coordinator_claim_does_not_gain_unclaimed_policy_metadata(
    monkeypatch,
):
    """The exact queue claim hash remains identical across durable transport."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2.committed_pickup_authority import (
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    )

    captured = {}

    def emit_and_apply(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(state_ready=True)

    monkeypatch.setattr(pw.durable_event_apply, "emit_and_apply", emit_and_apply)
    policy = CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=False,
        passive_guard_enabled=True,
    )
    pw._emit_and_apply_state(
        "PICKUP_TIME_UPDATED",
        order_id="elastic-claim",
        payload={
            "source": "coordinator_force",
            "new_pickup_at_warsaw": "2026-08-02T20:00:00+02:00",
        },
        event_id="elastic-claim-event",
        audit=True,
        committed_time_policy=policy,
    )

    metadata = captured.get("state_event_metadata") or {}
    assert COMMITTED_TIME_POLICY_SNAPSHOT_FIELD not in metadata


@pytest.mark.parametrize(
    "event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"]
)
@pytest.mark.parametrize(
    ("queue_state", "expected_effect"),
    [("missing", "superseded"), ("unreadable", "pending")],
)
def test_versioned_raw_coordinator_time_event_requires_exact_queue_claim(
    tmp_path, monkeypatch, event_type, queue_state, expected_effect
):
    """A causal CAS envelope is not a substitute for the exact click claim."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        TIME_EVENT_CAS_SCHEMA,
        TIME_EVENT_CAS_SCHEMA_FIELD,
    )

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "order_type": "elastic",
            "prep_minutes": 20,
            "assignment_event_id": "assign-elastic-1",
            "v319g_ck_change_count": 0,
        },
        event="TEST_ELASTIC",
    )
    before = sm.get_order("491578")
    policy = _panel_policy(forward=True)
    if event_type == "CZAS_KURIERA_UPDATED":
        event = pw._diff_czas_kuriera(
            before,
            {
                "czas_kuriera_warsaw": "2026-08-01T19:30:00+02:00",
                "czas_kuriera_hhmm": "19:30",
            },
            oid="491578",
            deliberate=True,
            authority_receipt=None,
            policy_snapshot=policy,
        )
        protected_fields = (
            "czas_kuriera_warsaw",
            "czas_kuriera_hhmm",
            "v319g_ck_change_count",
        )
    else:
        event = pw._diff_pickup_time(
            before,
            {
                "pickup_at_warsaw": "2026-08-01T19:30:00+02:00",
                "prep_minutes": 20,
            },
            oid="491578",
            deliberate=True,
            authority_receipt=None,
            policy_snapshot=policy,
        )
        protected_fields = ("pickup_at_warsaw", "pickup_time_revision")

    assert event is not None
    assert event["payload"][TIME_EVENT_CAS_SCHEMA_FIELD] == TIME_EVENT_CAS_SCHEMA
    if queue_state == "unreadable":
        Path(ctr.QUEUE_PATH).write_text("{", encoding="utf-8")
        with pytest.raises(RuntimeError, match="queue unreadable"):
            ctr.verify_claimed_event(event)
    else:
        assert ctr.verify_claimed_event(event) is False
    effect = sm.event_effect_status(event)
    result = sm.update_from_event(event)
    after = sm.get_order("491578")

    assert effect == expected_effect
    assert result is None
    assert {field: after.get(field) for field in protected_fields} == {
        field: before.get(field) for field in protected_fields
    }


@pytest.mark.parametrize("receipt_forward_enabled", [False, True])
def test_versioned_coordinator_pickup_respects_active_lifecycle_fence(
    tmp_path, monkeypatch, receipt_forward_enabled
):
    """Forward rollout cannot make an elastic pickup mutable after collection."""
    from dispatch_v2 import panel_watcher as pw
    from dispatch_v2 import state_machine as sm
    from dispatch_v2.committed_pickup_authority import (
        TIME_EVENT_CAS_SCHEMA_FIELD,
    )

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ctr,
        "_coordinator_policy_snapshot",
        lambda: CommittedPickupPolicySnapshot(
            producer="coordinator_queue",
            manual_passthrough_enabled=True,
            rutcom_forward_authority_enabled=receipt_forward_enabled,
            passive_guard_enabled=True,
        ),
    )
    _seed_state_491578(sm, tmp_path, monkeypatch)
    sm.upsert_order(
        "491578",
        {
            "status": "picked_up",
            "picked_up_at": "2026-08-01T19:17:00+02:00",
            "order_type": "elastic",
            "prep_minutes": 20,
            "assignment_event_id": "assign-elastic-1",
        },
        event="TEST_PICKED_UP_ELASTIC",
    )
    before = sm.get_order("491578")
    assert ctr.enqueue(["491578"], source="coordinator_panel") == 1
    receipt = ctr.pending_with_receipts()["491578"]
    event = pw._diff_pickup_time(
        before,
        {
            "pickup_at_warsaw": "2026-08-01T19:30:00+02:00",
            "prep_minutes": 20,
            "status_id": 5,
            "observed_at": receipt["eligible_at"],
        },
        oid="491578",
        deliberate=True,
        authority_receipt=receipt,
        # Deliberately opposite: a v6 click owns policy, not this later tick.
        policy_snapshot=_panel_policy(forward=not receipt_forward_enabled),
    )

    assert event is not None
    assert (
        TIME_EVENT_CAS_SCHEMA_FIELD in event["payload"]
    ) is receipt_forward_enabled
    claimed = ctr.claim_receipt(
        receipt, order_id="491578", event=event
    )
    assert claimed is not None
    assert ctr.verify_claimed_event(event)
    effect = sm.event_effect_status(event)
    result = sm.update_from_event(event)
    after = sm.get_order("491578")

    assert effect == "superseded"
    assert result is None
    assert after["pickup_at_warsaw"] == before["pickup_at_warsaw"]
    assert after["pickup_time_revision"] == before["pickup_time_revision"]


@pytest.mark.parametrize(
    "event_type", ["CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"]
)
@pytest.mark.parametrize(
    ("queue_state", "expected_effect"),
    [("missing", "superseded"), ("unreadable", "pending")],
)
def test_missing_aggregate_raw_coordinator_event_uses_exact_claim_gate(
    tmp_path, monkeypatch, event_type, queue_state, expected_effect
):
    """The missing-state early return cannot bypass transport authority."""
    from dispatch_v2 import state_machine as sm

    ctr = _isolate_coordinator_queue(tmp_path, monkeypatch)
    event = {
        "event_type": event_type,
        "order_id": "missing-aggregate",
        "payload": {"source": "coordinator_force"},
    }
    if queue_state == "unreadable":
        Path(ctr.QUEUE_PATH).write_text("{", encoding="utf-8")

    assert sm.event_effect_status(event, current=None) == expected_effect
    assert sm.update_from_event(event) is None
