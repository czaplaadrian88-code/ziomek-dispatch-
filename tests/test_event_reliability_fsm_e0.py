"""A360-E0: golden FSM, retry/DLQ, crash-window i jawny OFF/ON.

Kazdy test uzywa wylacznie ``tmp_path``. Payloady i identyfikatory sa
syntetyczne; asercje nie emituja surowych danych ani tekstu wyjatku.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2 import (
    event_bus,
    event_retry,
    order_fsm,
    parcel_lane_merge,
    replay_dead_letter,
)
from dispatch_v2 import state_machine as sm
from dispatch_v2.migrations import event_retry_metadata


UTC = timezone.utc
T0 = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)


def _legacy_db(path) -> None:
    conn = sqlite3.connect(path)
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
        CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def isolated_e0(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "orders_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(state_dir))
    monkeypatch.setattr(sm, "decision_flag", lambda *a, **k: False)
    monkeypatch.setattr(sm, "flag", lambda _name, default=False: default)

    # COURIER_ASSIGNED nie czyta realnego registry koordynatorow w tescie.
    from dispatch_v2 import courier_resolver

    monkeypatch.setattr(courier_resolver, "_load_courier_tiers", lambda: {})

    db_path = tmp_path / "events.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_retry_metadata.apply_to_connection(conn, synthetic_sandbox=True)
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    monkeypatch.setattr(event_bus, "_audit_log_initialized", False)
    return state_dir, db_path


def _event(event_type: str, suffix: str, **overrides):
    payloads = {
        "NEW_ORDER": {"restaurant": "R", "delivery_address": "D"},
        "COURIER_ASSIGNED": {"source": "panel_diff"},
        "COURIER_PICKED_UP": {"timestamp": "2026-07-12T00:10:00+00:00"},
        "COURIER_DELIVERED": {"timestamp": "2026-07-12T00:20:00+00:00"},
        "ORDER_RETURNED_TO_POOL": {"reason": "synthetic"},
    }
    event_minute = {
        "NEW_ORDER": 0,
        "COURIER_ASSIGNED": 1,
        "COURIER_PICKED_UP": 2,
        "COURIER_DELIVERED": 3,
        "ORDER_RETURNED_TO_POOL": 4,
    }[event_type]
    event = {
        "event_id": f"synthetic-{suffix}",
        "event_type": event_type,
        "order_id": "synthetic-order",
        "payload": dict(payloads[event_type]),
        "created_at": (T0 + timedelta(minutes=event_minute)).isoformat(),
    }
    if event_type in {"COURIER_ASSIGNED", "COURIER_PICKED_UP"}:
        event["courier_id"] = "synthetic-courier"
    for key, value in overrides.items():
        if key == "payload":
            event["payload"] = value
        else:
            event[key] = value
    return event


def _publish(event: dict) -> str | None:
    return event_bus.emit(
        event["event_type"],
        order_id=event.get("order_id"),
        courier_id=event.get("courier_id"),
        payload=event.get("payload"),
        event_id=event["event_id"],
        idempotency_key=event["event_id"],
    )


def test_log_only_off_is_byte_identical_to_legacy(
    isolated_e0, monkeypatch, tmp_path
):
    fixed_now = "2026-07-12T00:00:00+00:00"
    monkeypatch.setattr(sm, "now_iso", lambda: fixed_now)
    events = [
        _event("NEW_ORDER", "off-new"),
        _event("COURIER_ASSIGNED", "off-assigned"),
        _event("COURIER_PICKED_UP", "off-picked"),
        _event("COURIER_DELIVERED", "off-delivered"),
    ]
    events[0]["payload"]["first_seen"] = fixed_now

    legacy_dir = tmp_path / "legacy"
    wrapper_dir = tmp_path / "wrapper"
    legacy_dir.mkdir()
    wrapper_dir.mkdir()
    for directory in (legacy_dir, wrapper_dir):
        (directory / "orders_state.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("DISPATCH_STATE_DIR", str(legacy_dir))
    legacy = None
    for event in events:
        legacy = sm._update_from_event_legacy(event)
    legacy_bytes = (legacy_dir / "orders_state.json").read_bytes()

    monkeypatch.setenv("DISPATCH_STATE_DIR", str(wrapper_dir))
    wrapped = None
    for event in events:
        wrapped = sm.apply_order_event(event, enforce=False)
    wrapper_bytes = (wrapper_dir / "orders_state.json").read_bytes()

    assert wrapped is not None
    assert wrapped.enforcement_enabled is False
    assert wrapped.record == legacy
    assert wrapper_bytes == legacy_bytes
    assert event_retry.AUTOMATIC_RETRY_ENABLED is False
    assert event_retry.SELECTED_RETRY_POLICY_ID is None
    assert sm.ORDER_FSM_ENFORCEMENT_ENABLED is False


def test_observer_logs_only_closed_values_and_rejection_message_is_redacted(
    caplog,
):
    event_marker = "RAW-EVENT-TYPE-MARKER"
    source_marker = "RAW-SOURCE-MARKER"
    status_marker = "RAW-STATUS-MARKER"
    event = {
        "event_id": "synthetic-observer-redaction",
        "event_type": event_marker,
        "order_id": "synthetic-order",
        "payload": {"source": source_marker},
    }
    with caplog.at_level(logging.WARNING, logger="state_machine"):
        verdict = sm._observe_order_event(
            event,
            current={"status": status_marker},
        )
    assert verdict is not None and verdict.would_reject
    assert "event=unknown" in caplog.text
    assert "from=unknown" in caplog.text
    assert "source=unknown" in caplog.text
    for marker in (event_marker, source_marker, status_marker):
        assert marker not in caplog.text

    rejected = sm.OrderEventRejected("illegal_transition", event_marker)
    assert event_marker not in str(rejected)
    assert str(rejected) == "order event rejected: code=illegal_transition"


def test_off_observer_read_failure_is_fail_open_but_on_is_fail_loud(
    isolated_e0, monkeypatch, caplog
):
    state_dir, _db_path = isolated_e0
    marker = "SYNTHETIC-READ-FAILURE-MARKER"
    event = _event("NEW_ORDER", "observer-read-failure")

    def fail_read(_order_id):
        raise RuntimeError(marker)

    monkeypatch.setattr(sm, "get_order", fail_read)
    with caplog.at_level(logging.WARNING, logger="state_machine"):
        result = sm.apply_order_event(event, enforce=False)
    assert result.enforcement_enabled is False
    assert result.changed is True
    assert result.record["status"] == "planned"
    persisted = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert persisted["history"][-1]["event"] == "NEW_ORDER"
    assert "ORDER_FSM_OBSERVER_FAIL" in caplog.text
    assert marker not in caplog.text

    with pytest.raises(RuntimeError, match=marker):
        sm.apply_order_event(event, enforce=True)


def test_golden_legal_history_and_exact_idempotency(isolated_e0):
    events = [
        _event("NEW_ORDER", "new"),
        _event("COURIER_ASSIGNED", "assigned"),
        _event("COURIER_PICKED_UP", "picked"),
        _event("COURIER_DELIVERED", "delivered"),
    ]
    for event in events:
        result = sm.apply_order_event(event, enforce=True)
        assert result.changed is True

    final = sm.get_order("synthetic-order")
    assert final["status"] == "delivered"
    assert [entry["event"] for entry in final["history"]] == [
        "NEW_ORDER",
        "COURIER_ASSIGNED",
        "COURIER_PICKED_UP",
        "COURIER_DELIVERED",
    ]
    assert len(final["fsm_idempotency_keys"]) == 4

    # Mutation tripwire: bez receipt retry starego PICKED po DELIVERED bylby
    # illegal albo wykonalby drugi efekt. Dokladny key wygrywa nad kolejnoscia.
    retried = sm.apply_order_event(events[2], enforce=True)
    assert retried.duplicate is True
    assert retried.changed is False
    assert len(sm.get_order("synthetic-order")["history"]) == 4


def test_mutation_tripwire_illegal_transition_goes_terminal_dlq(isolated_e0):
    _state_dir, db_path = isolated_e0
    sm.apply_order_event(_event("NEW_ORDER", "illegal-base"), enforce=True)
    illegal = _event("COURIER_DELIVERED", "illegal-delivery")
    assert _publish(illegal) == illegal["event_id"]

    outcome = event_bus.apply_state_event(
        illegal,
        event_id=illegal["event_id"],
        emitted=True,
        enforce=True,
    )
    assert outcome.quarantined is True
    assert outcome.error_code == "illegal_transition"
    assert sm.get_order("synthetic-order")["status"] == "planned"
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,failure_class,error_code FROM events WHERE event_id=?",
        (illegal["event_id"],),
    ).fetchone()
    conn.close()
    assert row == ("dead_letter", "illegal", "illegal_transition")


@pytest.mark.parametrize(
    "bad_payload,expected_code",
    [
        ({"timestamp": "not-a-timestamp"}, "invalid_timestamp"),
        ({"source": "parcel_status_inbox"}, "invalid_payload"),
    ],
)
def test_mutation_tripwire_invalid_timestamp_never_becomes_now(
    isolated_e0, bad_payload, expected_code
):
    _state_dir, db_path = isolated_e0
    sm.apply_order_event(_event("NEW_ORDER", "timestamp-new"), enforce=True)
    sm.apply_order_event(_event("COURIER_ASSIGNED", "timestamp-assigned"), enforce=True)
    broken = _event(
        "COURIER_PICKED_UP",
        "timestamp-broken",
        payload=bad_payload,
    )
    _publish(broken)
    outcome = event_bus.apply_state_event(
        broken,
        event_id=broken["event_id"],
        emitted=True,
        enforce=True,
    )
    current = sm.get_order("synthetic-order")
    assert outcome.quarantined is True
    assert outcome.error_code == expected_code
    assert current["status"] == "assigned"
    assert current.get("picked_up_at") is None
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,failure_class,error_code FROM events WHERE event_id=?",
        (broken["event_id"],),
    ).fetchone()
    conn.close()
    assert row == ("dead_letter", "permanent", expected_code)


def test_crash_window_emit_then_effect_and_restart_is_single_effect(
    isolated_e0, monkeypatch
):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", "crash-window")
    assert _publish(event) == event["event_id"]
    assert sm.get_order("synthetic-order") is None

    real_marker = event_retry.mark_effect_applied
    calls = {"count": 0}

    def crash_after_state(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic crash after state write")
        return real_marker(*args, **kwargs)

    monkeypatch.setattr(event_retry, "mark_effect_applied", crash_after_state)
    first = event_bus.apply_state_event(
        event,
        event_id=event["event_id"],
        emitted=True,
        enforce=True,
    )
    assert first.changed is True
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT effect_applied_at FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()[0] is None
    conn.close()

    # Restart/replay: INSERT jest DUP, state receipt robi no-op, marker domyka sie.
    assert _publish(event) is None
    second = event_bus.apply_state_event(
        event,
        event_id=event["event_id"],
        emitted=False,
        enforce=True,
    )
    assert second.duplicate is True
    assert second.changed is False
    assert len(sm.get_order("synthetic-order")["history"]) == 1
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT effect_applied_at FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()[0] is not None
    conn.close()


def test_mutation_tripwire_retry_limit_and_next_retry_alias(isolated_e0):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", "retry-limit")
    _publish(event)
    policy = event_retry.RetryPolicy(max_attempts=2, backoff_seconds=(5.0,))
    conn = sqlite3.connect(db_path, isolation_level=None)
    first = event_retry.record_failure(
        conn,
        event["event_id"],
        TimeoutError("synthetic timeout"),
        failed_at=T0,
        policy=policy,
        policy_id="test-bounded-v1",
        enabled=True,
    )
    assert first.status == "retry_scheduled"
    row = conn.execute(
        "SELECT next_retry_at,next_attempt_at FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()
    assert row[0] == row[1] == (T0 + timedelta(seconds=5)).isoformat()

    exhausted = event_retry.record_failure(
        conn,
        event["event_id"],
        TimeoutError("synthetic timeout"),
        failed_at=T0 + timedelta(seconds=6),
        expected_status="retry_scheduled",
        expected_attempt_count=1,
        policy=policy,
        policy_id="test-bounded-v1",
        enabled=True,
    )
    assert exhausted.status == "dead_letter"
    assert exhausted.attempt_count == 2
    assert conn.execute(
        "SELECT next_retry_at,next_attempt_at FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone() == (None, None)
    conn.close()


def test_mark_processed_clears_both_retry_aliases(isolated_e0):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", "processed-alias-clear")
    _publish(event)
    conn = sqlite3.connect(db_path, isolation_level=None)
    transition = event_retry.record_failure(
        conn,
        event["event_id"],
        TimeoutError("synthetic"),
        failed_at=T0,
        policy=event_retry.RetryPolicy(2, (5.0,)),
        policy_id="test-alias-clear-v1",
        enabled=True,
    )
    assert transition.status == "retry_scheduled"
    conn.close()

    assert event_bus.mark_processed(
        event["event_id"], retry_consumer_enabled=True
    ) is True
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status,next_attempt_at,next_retry_at FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone() == ("processed", None, None)
    conn.close()


def test_poison_dlq_stores_no_exception_text(isolated_e0):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", "safe-error")
    _publish(event)
    sensitive_marker = "synthetic-sensitive-marker"
    assert event_bus.mark_failed(
        event["event_id"],
        ValueError(sensitive_marker),
        enabled=True,
        policy=event_retry.FSM_QUARANTINE_POLICY,
        policy_id=event_retry.FSM_QUARANTINE_POLICY_ID,
    ) is True
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,last_error,failure_class,error_code FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()
    conn.close()
    assert row == (
        "dead_letter",
        "invalid_payload",
        "permanent",
        "invalid_payload",
    )
    assert sensitive_marker not in json.dumps(row)


@pytest.mark.parametrize(
    "suffix,error,expected_class,expected_code",
    [
        ("off-transient", TimeoutError("synthetic"), "transient", "timeout"),
        ("off-permanent", ValueError("synthetic"), "permanent", "invalid_payload"),
        (
            "off-illegal",
            event_retry.FailureDescriptor(
                event_retry.FailureClass.ILLEGAL, "illegal_transition"
            ),
            "illegal",
            "illegal_transition",
        ),
    ],
)
def test_retry_policy_off_all_classes_remain_failed(
    isolated_e0, suffix, error, expected_class, expected_code
):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", suffix)
    _publish(event)
    conn = sqlite3.connect(db_path, isolation_level=None)
    transition = event_retry.record_failure(
        conn,
        event["event_id"],
        error,
        failed_at=T0,
        policy=event_retry.FSM_QUARANTINE_POLICY,
        policy_id="must-not-apply-while-off",
        enabled=False,
    )
    row = conn.execute(
        "SELECT status,next_retry_at,next_attempt_at,failure_class,error_code,"
        "retry_policy_id FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()
    conn.close()
    assert transition.status == "failed"
    assert row == (
        "failed",
        None,
        None,
        expected_class,
        expected_code,
        None,
    )


@pytest.mark.parametrize(
    "suffix,error,expected_status",
    [
        ("on-transient", TimeoutError("synthetic"), "retry_scheduled"),
        ("on-permanent", ValueError("synthetic"), "dead_letter"),
        (
            "on-illegal",
            event_retry.FailureDescriptor(
                event_retry.FailureClass.ILLEGAL, "illegal_transition"
            ),
            "dead_letter",
        ),
    ],
)
def test_retry_policy_on_is_explicit_for_each_failure_class(
    isolated_e0, suffix, error, expected_status
):
    _state_dir, db_path = isolated_e0
    event = _event("NEW_ORDER", suffix)
    _publish(event)
    conn = sqlite3.connect(db_path, isolation_level=None)
    transition = event_retry.record_failure(
        conn,
        event["event_id"],
        error,
        failed_at=T0,
        policy=event_retry.RetryPolicy(2, (5.0,)),
        policy_id="test-explicit-matrix-v1",
        enabled=True,
    )
    row = conn.execute(
        "SELECT status,next_retry_at,next_attempt_at,retry_policy_id "
        "FROM events WHERE event_id=?",
        (event["event_id"],),
    ).fetchone()
    conn.close()
    assert transition.status == expected_status
    assert row[0] == expected_status
    assert row[1] == row[2]
    assert row[3] == "test-explicit-matrix-v1"
    if expected_status == "retry_scheduled":
        assert row[1] == (T0 + timedelta(seconds=5)).isoformat()
    else:
        assert row[1] is None


def test_migration_dry_run_is_read_only_and_apply_is_backward_compatible(tmp_path):
    db_path = tmp_path / "migration.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO events(event_id,event_type,payload,created_at,status)
           VALUES ('synthetic-old','NEW_ORDER','{}',?,'failed')""",
        (T0.isoformat(),),
    )
    conn.commit()
    conn.close()
    before = db_path.read_bytes()
    plan = event_retry_metadata.inspect(str(db_path))
    after = db_path.read_bytes()
    assert before == after
    assert plan["ready"] is False
    assert plan["idempotency_backfill_count"] == 1

    conn = sqlite3.connect(db_path, isolation_level=None)
    event_retry_metadata.apply_to_connection(conn, synthetic_sandbox=True)
    assert event_retry_metadata.inspect_connection(conn)["ready"] is True
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    assert {"next_attempt_at", "next_retry_at", "idempotency_key"} <= columns
    assert conn.execute(
        "SELECT length(idempotency_key) FROM events"
    ).fetchone()[0] == 64
    second = event_retry_metadata.apply_to_connection(
        conn, synthetic_sandbox=True
    )
    assert second["before"]["ready"] is True
    conn.close()


def test_order_cancelled_has_real_parcel_writer_e2e(isolated_e0, monkeypatch):
    state_dir, db_path = isolated_e0
    sm.upsert_order(
        "synthetic-parcel",
        {
            "status": "planned",
            "source": "parcel",
            "restaurant": "Parcel",
            "delivery_address": "Synthetic",
        },
        event="TEST_SETUP",
    )
    monkeypatch.setattr(sm, "ORDER_FSM_ENFORCEMENT_ENABLED", True)
    monkeypatch.setattr(parcel_lane_merge.C, "flag", lambda *_a, **_k: True)
    monkeypatch.setattr(parcel_lane_merge, "_apply_status_inbox", lambda: 0)
    monkeypatch.setattr(parcel_lane_merge, "_load_snapshot", lambda: {})
    stats = parcel_lane_merge.run()

    current = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-parcel"]
    assert stats["retired"] == 1
    assert current["status"] == "cancelled"
    assert current["history"][-1]["event"] == "ORDER_CANCELLED"
    assert current["fsm_idempotency_keys"]
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE event_type='ORDER_CANCELLED'"
    ).fetchone()[0] == 1
    conn.close()


def test_policy_options_are_versioned_and_none_is_selected():
    options = event_retry.policy_options_summary()
    assert [option["policy_id"] for option in options] == [
        "manual_hold_v1",
        "sqlite_busy_parity_v1",
        "business_transient_pending_v1",
        "fsm_quarantine_v1",
    ]
    assert options[1]["backoff_seconds"] == [0.1, 0.5, 2.0]
    assert options[1]["provenance"].startswith("event_bus_existing")
    assert options[2]["max_attempts"] is None
    assert not any(option["selected"] for option in options)


def test_formal_fsm_event_types_are_derived_and_cover_event_bus_exactly():
    derived = frozenset(
        rule.event_type for rule in order_fsm.TRANSITION_RULES
    ) | order_fsm.DATA_ONLY_EVENT_TYPES
    assert order_fsm.FORMAL_FSM_EVENT_TYPES == derived
    assert event_bus.EVENT_TYPES == (
        order_fsm.FORMAL_FSM_EVENT_TYPES - order_fsm.CORRECTION_EVENT_TYPES
    ) | order_fsm.NON_STATE_EVENT_TYPES


def test_correction_event_requeue_requires_snapshot_and_formal_validation(
    isolated_e0,
):
    _state_dir, db_path = isolated_e0
    event_id = "synthetic-resurrection-dlq"
    payload = {
        "new_status": "assigned",
        "reason": "manual_correction",
        "source": "manual_correction",
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO events(
               event_id,event_type,order_id,payload,created_at,status,
               failure_class,error_code,dead_lettered_at,idempotency_key
           ) VALUES (?,?,?,?,?,'dead_letter','illegal','illegal_transition',?,?)""",
        (
            event_id,
            "ORDER_RESURRECTED",
            "synthetic-order",
            json.dumps(payload),
            T0.isoformat(),
            T0.isoformat(),
            event_retry.idempotency_key(event_id),
        ),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="state snapshot"):
        replay_dead_letter.requeue(
            str(db_path),
            event_id,
            reason="source_repaired",
            reset_attempt_count=False,
            confirmed=True,
            replayed_at=T0 + timedelta(minutes=1),
        )

    assert replay_dead_letter.requeue(
        str(db_path),
        event_id,
        reason="source_repaired",
        reset_attempt_count=False,
        confirmed=True,
        replayed_at=T0 + timedelta(minutes=1),
        current_state={
            "synthetic-order": {
                "status": "delivered",
                "updated_at": T0.isoformat(),
            }
        },
    ) is True


def test_dead_letter_listing_rehashes_key_and_sanitizes_legacy_metadata(
    isolated_e0,
):
    _state_dir, db_path = isolated_e0
    raw_event_marker = "RAW-EVENT-MARKER"
    payload_marker = "RAW-PAYLOAD-MARKER"
    reason_marker = "RAW-REASON-MARKER"
    stored_digest = event_retry.idempotency_key(raw_event_marker)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO events(
               event_id,event_type,order_id,payload,created_at,status,
               failure_class,error_code,dead_lettered_at,last_replay_reason,
               idempotency_key
           ) VALUES (?,?,?,?,?,'dead_letter',?,?,?,?,?)""",
        (
            raw_event_marker,
            "NEW_ORDER",
            "RAW-ORDER-MARKER",
            json.dumps({"marker": payload_marker}),
            T0.isoformat(),
            "RAW-CLASS-MARKER",
            "RAW-ERROR-MARKER",
            T0.isoformat(),
            reason_marker,
            stored_digest,
        ),
    )
    conn.commit()
    conn.close()

    rows = replay_dead_letter.list_dead_letters(str(db_path))
    assert rows == [{
        "event_ref": event_retry.event_reference(
            raw_event_marker,
            stored_idempotency_key=stored_digest,
        ),
        "event_type": "NEW_ORDER",
        "attempt_count": 0,
        "failure_class": "permanent",
        "error_code": "unexpected_failure",
        "replay_count": 0,
        "last_replay_reason": None,
    }]
    encoded = json.dumps(rows, sort_keys=True)
    assert rows[0]["event_ref"] != stored_digest[:12]
    for marker in (
        raw_event_marker,
        payload_marker,
        reason_marker,
        "RAW-ORDER-MARKER",
        "RAW-CLASS-MARKER",
        "RAW-ERROR-MARKER",
    ):
        assert marker not in encoded

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE events SET idempotency_key='legacy-low-entropy' "
        "WHERE event_id=?",
        (raw_event_marker,),
    )
    conn.commit()
    conn.close()
    legacy_rows = replay_dead_letter.list_dead_letters(str(db_path))
    assert legacy_rows[0]["event_ref"] == event_retry.event_reference(
        raw_event_marker,
        stored_idempotency_key="legacy-low-entropy",
    )
    assert "legacy-low-entropy" not in json.dumps(legacy_rows)


def test_dead_letter_requeue_rejects_unknown_event_type_without_leak(
    isolated_e0, capsys
):
    state_dir, db_path = isolated_e0
    event_type_marker = "RAW-CORRUPT-EVENT-TYPE-MARKER"
    event_id = "synthetic-corrupt-dlq"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO events(
               event_id,event_type,order_id,payload,created_at,status,
               failure_class,error_code,dead_lettered_at,idempotency_key
           ) VALUES (?,?,?,?,?,'dead_letter','permanent','invalid_payload',?,?)""",
        (
            event_id,
            event_type_marker,
            "synthetic-order",
            "{}",
            T0.isoformat(),
            T0.isoformat(),
            event_retry.idempotency_key(event_id),
        ),
    )
    conn.commit()
    conn.close()

    rc = replay_dead_letter.main([
        "--db",
        str(db_path),
        "--requeue",
        event_id,
        "--reason",
        "test_only",
        "--preserve-attempts",
        "--confirm-requeue",
        "--state",
        str(state_dir / "orders_state.json"),
    ])
    captured = capsys.readouterr()
    assert rc == 2
    assert json.loads(captured.out) == {
        "ok": False,
        "error_class": "permanent",
        "error_code": "invalid_payload",
    }
    assert event_type_marker not in captured.out
    assert event_type_marker not in captured.err
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()[0] == "dead_letter"
    conn.close()
