"""Reader-first cutover for durable committed-time envelopes.

The reader must accept exactly two authenticated transport shapes before the
writer starts emitting the new source-payload fingerprint.  Keeping the
reader unchanged in the writer commit makes a code rollback safe: rows emitted
by either writer remain readable.
"""

import hashlib
import json
import sqlite3

import pytest

from dispatch_v2 import committed_pickup_authority as CPA
from dispatch_v2 import committed_pickup_apply as APPLY
from dispatch_v2 import durable_event_apply as DURABLE
from dispatch_v2 import event_bus as EVENT_BUS
from dispatch_v2 import state_machine as STATE
from dispatch_v2.committed_pickup_authority import CommittedPickupPolicySnapshot


def _envelope(*, current: bool) -> tuple[dict, dict]:
    expected = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": "synthetic-order",
        "courier_id": "synthetic-courier",
        "payload": {"synthetic": True},
    }
    event = {key: None for key in CPA._DURABLE_EVENT_KEYS}
    event.update(expected)
    event.update(
        {
            "event_id": "synthetic-event-id",
            "committed_authority_attestation": {"schema": "synthetic"},
            "saved_plans_authorized": False,
            "committed_invalidates_view_authorized": False,
            "czasowka_reclaim_shadow_authorized": False,
            "czasowka_reclaim_live_authorized": False,
            CPA.COMMITTED_TIME_POLICY_SNAPSHOT_FIELD: {"synthetic": True},
            CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD: "0123456789abcdef",
        }
    )
    if not current:
        event.pop(CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD)
    return event, expected


def test_reader_first_accepts_legacy_and_current_exact_shapes():
    for current in (False, True):
        event, expected = _envelope(current=current)
        assert CPA._event_envelope_matches(
            event,
            expected,
            durable_attestation_verified=True,
        )


def test_reader_first_shape_registry_is_closed_and_exact():
    shapes = tuple(frozenset(shape) for shape in CPA._ACCEPTED_DURABLE_EVENT_SHAPES)
    assert len(shapes) == 2
    assert frozenset(CPA._DURABLE_EVENT_KEYS) in shapes
    assert frozenset(CPA._LEGACY_DURABLE_EVENT_KEYS) in shapes
    assert set(shapes[0] ^ shapes[1]) == {CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD}

    current, expected = _envelope(current=True)
    smuggled = dict(current, synthetic_extra=True)
    truncated = dict(current)
    truncated.pop(CPA.COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
    assert not CPA._event_envelope_matches(
        smuggled,
        expected,
        durable_attestation_verified=True,
    )
    assert not CPA._event_envelope_matches(
        truncated,
        expected,
        durable_attestation_verified=True,
    )


def test_reader_first_current_shape_requires_nonempty_text_fingerprint():
    current, expected = _envelope(current=True)
    for invalid in ("", None, 17):
        current[CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD] = invalid
        assert not CPA._event_envelope_matches(
            current,
            expected,
            durable_attestation_verified=True,
        )


def test_mutation_removing_legacy_reader_reproduces_cutover_loss(monkeypatch):
    legacy, expected = _envelope(current=False)
    monkeypatch.setattr(
        CPA,
        "_ACCEPTED_DURABLE_EVENT_SHAPES",
        (CPA._DURABLE_EVENT_KEYS,),
    )
    assert not CPA._event_envelope_matches(
        legacy,
        expected,
        durable_attestation_verified=True,
    )


_SYNTHETIC_ORDER = "synthetic-cutover-order"
_SYNTHETIC_COURIER = "synthetic-cutover-courier"


def _decision_flag(name: str) -> bool:
    return name in {
        "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH",
        "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY",
    }


def _runtime_flag(name: str, default=None):
    if name in {
        "ENABLE_CZASOWKA_CK_PASSIVE_GUARD",
        "ENABLE_PICKUP_TIME_MIRRORS_CK",
    }:
        return True
    return default


def _policy() -> CommittedPickupPolicySnapshot:
    return CommittedPickupPolicySnapshot(
        producer="panel_watcher",
        manual_passthrough_enabled=True,
        rutcom_forward_authority_enabled=True,
        passive_guard_enabled=True,
    )


def _observation() -> dict:
    return {
        "oid": _SYNTHETIC_ORDER,
        "courier_id": _SYNTHETIC_COURIER,
        "courier_id_at_observation": _SYNTHETIC_COURIER,
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


def _isolate_durable_bus(tmp_path, monkeypatch) -> None:
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
    monkeypatch.setattr(EVENT_BUS, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(EVENT_BUS, "_audit_log_initialized", False)
    monkeypatch.setattr(EVENT_BUS, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(EVENT_BUS, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(lifecycle_downstream, "apply", lambda _event: None)


def _setup_committed_cutover(tmp_path, monkeypatch) -> None:
    _isolate_durable_bus(tmp_path, monkeypatch)
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(STATE, "_state_path", lambda: str(state_path))
    STATE.upsert_order(
        _SYNTHETIC_ORDER,
        {
            "order_id": _SYNTHETIC_ORDER,
            "status": "assigned",
            "courier_id": _SYNTHETIC_COURIER,
            "order_type": "czasowka",
            "prep_minutes": 60,
            "pickup_at_warsaw": "2026-08-01T19:15:58+02:00",
            "czas_kuriera_warsaw": "2026-08-01T19:16:00+02:00",
            "czas_kuriera_hhmm": "19:16",
            "zmiana_czasu_odbioru": False,
            "pickup_time_revision": 0,
        },
        event="COURIER_ASSIGNED",
    )
    monkeypatch.setattr(APPLY.C, "decision_flag", _decision_flag)
    monkeypatch.setattr(APPLY.C, "flag", _runtime_flag)
    monkeypatch.setattr(STATE, "decision_flag", _decision_flag)
    monkeypatch.setattr(STATE, "flag", _runtime_flag)


def _blocked_state(_event):
    raise RuntimeError("synthetic state outage")


def _persist_current_shape(event_id: str, legacy: dict) -> dict:
    current = dict(legacy)
    current.pop("committed_authority_attestation")
    material = json.dumps(
        current["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    current[CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD] = hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()[:16]
    current["committed_authority_attestation"] = APPLY._authority_attestation(
        current
    )
    db_path = EVENT_BUS._state_apply_outbox_db_path or EVENT_BUS._db_path()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE state_apply_outbox SET state_event=? WHERE event_id=?",
            (json.dumps(current, ensure_ascii=False), event_id),
        )
    return current


@pytest.mark.parametrize("shape", ["legacy", "current"])
def test_reader_first_full_drain_applies_rows_from_both_writers(
    tmp_path,
    monkeypatch,
    shape,
):
    """Exact reader-first bytes drain both the old and future writer shape."""
    _setup_committed_cutover(tmp_path, monkeypatch)
    real_update = STATE.update_from_event
    monkeypatch.setattr(STATE, "update_from_event", _blocked_state)
    outcome = APPLY.apply_event(
        {
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": _SYNTHETIC_ORDER,
            "courier_id": _SYNTHETIC_COURIER,
            "payload": _observation(),
        },
        authority_policy=_policy(),
    )
    stored = EVENT_BUS.get_state_apply_outbox(outcome.event_id)["state_event"]

    # R1's native writer is intentionally still legacy.  The current shape is
    # constructed and independently re-attested to simulate the future W1.
    assert CPA.SOURCE_PAYLOAD_FINGERPRINT_FIELD not in stored
    if shape == "current":
        stored = _persist_current_shape(outcome.event_id, stored)
    expected_keys = (
        CPA._DURABLE_EVENT_KEYS
        if shape == "current"
        else CPA._LEGACY_DURABLE_EVENT_KEYS
    )
    assert set(stored) == set(expected_keys)
    assert STATE.event_effect_status(
        stored,
        STATE.get_order(_SYNTHETIC_ORDER),
    ) == "pending"

    monkeypatch.setattr(STATE, "update_from_event", real_update)
    drained = DURABLE.drain_pending(
        state_update_fn=real_update,
        effect_status_fn=STATE.event_effect_status,
        get_order_fn=STATE.get_order_strict,
        downstream_fn=lambda _event: None,
        limit=20,
    )
    row = EVENT_BUS.get_state_apply_outbox(outcome.event_id)
    state = STATE.get_order(_SYNTHETIC_ORDER)
    assert drained.get("superseded", 0) == 0
    assert row["state_status"] == "applied", row.get("last_error")
    assert state["pickup_at_warsaw"] == "2026-08-01T19:21:00+02:00"
    assert state["committed_pickup_authority"] == "rutcom_forward_commitment"
