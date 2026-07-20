"""C3-01 — atomowy outbox event->state->downstream."""

import json
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dispatch_v2 import durable_event_apply as DEA
from dispatch_v2 import event_bus as EB
from dispatch_v2 import lifecycle_downstream as LD
from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import parcel_lane_merge as PLM
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import state_machine as SM


_REAL_LIFECYCLE_DOWNSTREAM_APPLY = PW.lifecycle_downstream.apply


@pytest.fixture
def isolated_stores(tmp_path, monkeypatch):
    events_db = tmp_path / "events.db"
    state_path = tmp_path / "orders_state.json"
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
    state_path.write_text(
        json.dumps(
            {
                "c3-order": {
                    "order_id": "c3-order",
                    "status": "assigned",
                    "commitment_level": "assigned",
                    "courier_id": "100",
                    "updated_at": "2026-07-19T08:00:00+00:00",
                    "delivery_coords": [53.1, 23.1],
                    "history": [],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(EB, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(EB, "_audit_log_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(PW, "emit", EB.emit)
    monkeypatch.setattr(PW, "emit_audit", EB.emit_audit)
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", lambda _event: None)
    return events_db, state_path


def _returned_kwargs():
    return {
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"reason": "cancelled", "source": "test"},
        "event_id": "c3-order_ORDER_RETURNED_cancelled_test",
        "audit": True,
    }


def _state(state_path):
    return json.loads(state_path.read_text(encoding="utf-8"))["c3-order"]


def _apply_resurrection(new_status="picked_up", courier_id="100", reason="test-correction"):
    return SM.update_from_event(
        {
            "event_type": "ORDER_RESURRECTED",
            "event_id": f"c3-order_ORDER_RESURRECTED_{new_status}_{reason}",
            "order_id": "c3-order",
            "courier_id": courier_id,
            "payload": {
                "new_status": new_status,
                "reason": reason,
                "source": reason,
            },
        }
    )


def test_outbox_schema_migration_is_additive_and_thread_serialized(
    isolated_stores,
):
    events_db, _state_path = isolated_stores
    with sqlite3.connect(events_db) as conn:
        conn.execute(
            """CREATE TABLE state_apply_outbox (
                event_id TEXT PRIMARY KEY,
                event_key TEXT NOT NULL,
                order_id TEXT NOT NULL,
                expected_state_version TEXT,
                state_event TEXT NOT NULL,
                state_status TEXT NOT NULL DEFAULT 'pending',
                state_applied_at TEXT,
                downstream_status TEXT NOT NULL DEFAULT 'pending',
                downstream_applied_at TEXT,
                state_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO state_apply_outbox
               (event_id, event_key, order_id, expected_state_version,
                state_event, state_status, downstream_status, state_attempts,
                created_at, updated_at)
               VALUES ('legacy-indeterminate', 'legacy-key', 'c3-order', NULL,
                       ?, 'applied', 'pending', 1, ?, ?)""",
            (
                json.dumps(
                    {
                        "event_type": "COURIER_ASSIGNED",
                        "event_id": "legacy-indeterminate",
                        "order_id": "c3-order",
                        "courier_id": "100",
                        "payload": {"source": "legacy"},
                    }
                ),
                "2026-07-19T08:00:00+00:00",
                "2026-07-19T08:00:00+00:00",
            ),
        )
    barrier = threading.Barrier(8)
    errors = []

    def initialize():
        try:
            barrier.wait(timeout=5)
            EB._ensure_state_apply_outbox_initialized()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=initialize) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    with sqlite3.connect(events_db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(state_apply_outbox)")
        }
        migrated_attempts = conn.execute(
            "SELECT downstream_attempts FROM state_apply_outbox "
            "WHERE event_id='legacy-indeterminate'"
        ).fetchone()[0]
    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert {
        "expected_state_marker",
        "expected_state_token",
        "predecessor_event_id",
        "downstream_attempts",
    } <= columns
    assert migrated_attempts == 1
    new_id = "new-row-explicit-zero"
    new_event = {
        "event_type": "NEW_ORDER",
        "event_id": new_id,
        "order_id": "new-row-order",
        "courier_id": None,
        "payload": {},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="new-row-order",
        event_id=new_id,
        state_event=new_event,
        event_key="new-row-key",
    ) == new_id
    assert EB.get_state_apply_outbox(new_id)["downstream_attempts"] == 0


def test_emit_success_apply_failure_duplicate_retry_then_plain_duplicate(
    isolated_stores, monkeypatch
):
    """Golden: pending receipt odzyskuje state, a domkniety duplicate jest no-op."""
    events_db, state_path = isolated_stores
    real_apply = SM.update_from_event
    attempts = 0
    downstream = []

    def fail_first(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SM.StateReadError("synthetic guarded write rejection")
        return real_apply(event)

    monkeypatch.setattr(PW, "update_from_event", fail_first)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", downstream.append)

    first = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())
    second = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())
    third = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())

    assert first.event_created is True
    assert first.state_ready is False
    assert first.failure_stage == "state_apply"
    assert second.event_created is False
    assert second.state_transitioned is True
    assert second.downstream_executed is True
    assert third.state_ready is True
    assert third.state_transitioned is False
    assert third.downstream_executed is False
    assert first.event_id == second.event_id == third.event_id
    assert attempts == 2
    assert len(downstream) == 1
    assert _state(state_path)["status"] == "returned_to_pool"

    with sqlite3.connect(events_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
        row = conn.execute(
            "SELECT state_status, downstream_status FROM state_apply_outbox"
        ).fetchone()
    assert row == ("applied", "applied")


def test_state_read_failure_still_persists_event_and_recovers(
    isolated_stores, monkeypatch
):
    """Oryginalny trigger C3: strict-read fail nie moze zgubic intencji eventu."""
    events_db, state_path = isolated_stores
    reads = 0

    def fail_pre_read_and_first_apply_read(oid):
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise SM.StateReadError("synthetic strict-read failure")
        return SM.get_order(oid)

    monkeypatch.setattr(
        PW, "state_get_order_strict", fail_pre_read_and_first_apply_read
    )
    first = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())

    assert first.event_created is True
    assert first.state_ready is False
    assert first.failure_stage == "state_read"
    assert _state(state_path)["status"] == "assigned"
    row = EB.get_state_apply_outbox(first.event_id)
    assert row["state_status"] == "pending"
    assert row["expected_state_version"] == "__STATE_READ_UNAVAILABLE__"
    assert str(row["expected_state_token"]).startswith("sha256:")
    with sqlite3.connect(events_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1

    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=lambda _event: None,
    )

    assert counts["state_ready"] == 1
    assert counts["failed"] == 0
    assert _state(state_path)["status"] == "returned_to_pool"
    assert _state(state_path)["last_lifecycle_event_id"] == first.event_id


def test_unknown_version_does_not_overwrite_newer_state(
    isolated_stores, monkeypatch
):
    reads = 0

    def fail_twice(oid):
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise SM.StateReadError("synthetic strict-read failure")
        return SM.get_order(oid)

    monkeypatch.setattr(PW, "state_get_order_strict", fail_twice)
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )
    assert first.failure_stage == "state_read"

    SM.update_from_event(
        {
            "event_type": "COURIER_DELIVERED",
            "event_id": "c3-order_COURIER_DELIVERED_newer_T2",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"timestamp": "T2"},
        }
    )
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=lambda _event: None,
    )

    assert counts["superseded"] == 1
    assert _state(isolated_stores[1])["delivered_at"] == "T2"
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "superseded"


def test_exact_marker_survives_orthogonal_lifecycle_event(
    isolated_stores, monkeypatch
):
    """ASSIGNED receipt da sie odzyskac po ortogonalnej zmianie czasu."""
    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("before state")),
    )
    first = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload={"source": "panel_reassign"},
        event_id="c3-order_COURIER_ASSIGNED_200_reassign",
        audit=True,
    )
    assert first.failure_stage == "state_apply"
    stored_event = EB.get_state_apply_outbox(first.event_id)["state_event"]

    # Symulacja crashu po atomowym JSON write, ale przed mark_state_applied.
    SM.update_from_event(stored_event)
    SM.update_from_event(
        {
            "event_type": "CZAS_KURIERA_UPDATED",
            "event_id": "c3-order_CZAS_KURIERA_UPDATED_orthogonal",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {
                "new_ck_iso": "2026-07-19T12:00:00+02:00",
                "new_ck_hhmm": "12:00",
                "source": "coordinator_edit",
            },
        }
    )
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)
    retry = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload={"source": "panel_reassign"},
        event_id="c3-order_COURIER_ASSIGNED_200_reassign",
        audit=True,
    )

    current = _state(isolated_stores[1])
    assert retry.state_ready is True
    assert retry.superseded is False
    assert current["last_lifecycle_event_id_courier_assigned"] == first.event_id
    assert current["last_lifecycle_event_id"].endswith("orthogonal")


def test_retry_uses_exact_durable_t1_not_current_callsite_t2(
    isolated_stores, monkeypatch
):
    """Blind repro: canonical duplicate z innego writera nie podmienia payloadu."""
    events_db, state_path = isolated_stores
    real_apply = SM.update_from_event
    attempts = 0
    downstream = []

    def fail_first(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fail before state")
        return real_apply(event)

    monkeypatch.setattr(PW, "update_from_event", fail_first)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", downstream.append)
    key = "c3-order_COURIER_DELIVERED_canonical"
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1", "deliv_source": "panel", "final_location": "A"},
        state_payload={"timestamp": "T1", "deliv_source": "panel"},
        event_id=key,
    )
    second = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="200",
        payload={"timestamp": "T2", "deliv_source": "reconcile", "final_location": "B"},
        event_id=key,
    )

    assert first.state_ready is False
    assert second.state_ready is True
    assert second.event_created is False
    assert _state(state_path)["delivered_at"] == "T1"
    assert downstream[0]["courier_id"] == "100"
    assert downstream[0]["payload"] == {"timestamp": "T1", "deliv_source": "panel"}
    with sqlite3.connect(events_db) as conn:
        payload = json.loads(
            conn.execute("SELECT payload FROM events").fetchone()[0]
        )
        stored_state = json.loads(
            conn.execute("SELECT state_event FROM state_apply_outbox").fetchone()[0]
        )
    assert payload["timestamp"] == "T1"
    assert stored_state["payload"]["timestamp"] == "T1"


def test_queue_event_is_hidden_until_durable_state_is_applied(
    isolated_stores, monkeypatch
):
    """Blind repro: consumer nie widzi commitu event/outbox przed state write."""
    entered_state_apply = threading.Event()
    release_state_apply = threading.Event()
    real_apply = SM.update_from_event

    def pause_after_durable_emit(event):
        entered_state_apply.set()
        assert release_state_apply.wait(timeout=5)
        return real_apply(event)

    monkeypatch.setattr(PW, "update_from_event", pause_after_durable_emit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            PW._emit_and_apply_state,
            "COURIER_DELIVERED",
            order_id="c3-order",
            courier_id="100",
            payload={"timestamp": "2026-07-19T08:05:00+00:00"},
            event_id="c3-order_COURIER_DELIVERED_visibility_gate",
        )
        assert entered_state_apply.wait(timeout=5)

        with sqlite3.connect(isolated_stores[0]) as conn:
            row = conn.execute(
                """SELECT e.status, s.state_status
                   FROM events AS e JOIN state_apply_outbox AS s
                     ON s.event_id = e.event_id"""
            ).fetchone()
        assert row == ("pending", "pending")
        assert EB.get_pending(event_types=["COURIER_DELIVERED"]) == []
        assert EB.get_pending_count(event_types=["COURIER_DELIVERED"]) == 1

        release_state_apply.set()
        outcome = future.result(timeout=5)

    assert outcome.state_ready is True
    ready = EB.get_pending(event_types=["COURIER_DELIVERED"])
    assert [item["event_id"] for item in ready] == [outcome.event_id]


def test_get_pending_keeps_legacy_events_but_closes_superseded_queue_receipt(
    isolated_stores,
):
    legacy_id = EB.emit(
        "NEW_ORDER",
        order_id="legacy-order",
        payload={"source": "legacy"},
        event_id="legacy-order_NEW_ORDER_no_outbox",
    )
    durable_id = EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_superseded_source",
        state_event={
            "event_type": "COURIER_DELIVERED",
            "event_id": "c3-order_COURIER_DELIVERED_superseded_source",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"timestamp": "T1"},
        },
        event_key="c3-order_COURIER_DELIVERED_superseded_source",
    )

    assert legacy_id is not None
    assert durable_id is not None
    assert [item["event_id"] for item in EB.get_pending()] == [legacy_id]
    assert EB.mark_state_apply_superseded(durable_id, "newer state wins") is True

    with sqlite3.connect(isolated_stores[0]) as conn:
        durable_status = conn.execute(
            "SELECT status FROM events WHERE event_id = ?", (durable_id,)
        ).fetchone()[0]
        dedup_count = conn.execute(
            "SELECT COUNT(*) FROM processed_events WHERE event_id = ?", (durable_id,)
        ).fetchone()[0]
    assert durable_status == "processed"
    assert dedup_count == 1
    assert [item["event_id"] for item in EB.get_pending()] == [legacy_id]


def test_queue_cleanup_is_atomic_with_closed_outbox_and_keeps_unresolved(
    isolated_stores, monkeypatch
):
    events_db, _state_path = isolated_stores
    monkeypatch.setattr(EB, "_is_peak_window", lambda: False)

    closed_id = EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "closed"},
        event_id="c3-order_COURIER_DELIVERED_cleanup_closed",
        state_event={
            "event_type": "COURIER_DELIVERED",
            "event_id": "c3-order_COURIER_DELIVERED_cleanup_closed",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"timestamp": "closed"},
        },
        event_key="c3-order_COURIER_DELIVERED_cleanup_closed",
    )
    unresolved_id = EB.emit(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "unresolved"},
        event_id="c3-order_COURIER_PICKED_UP_cleanup_unresolved",
        state_event={
            "event_type": "COURIER_PICKED_UP",
            "event_id": "c3-order_COURIER_PICKED_UP_cleanup_unresolved",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"timestamp": "unresolved"},
        },
        event_key="c3-order_COURIER_PICKED_UP_cleanup_unresolved",
    )
    assert closed_id is not None
    assert unresolved_id is not None
    assert EB.mark_state_apply_superseded(closed_id, "newer state") is True
    assert EB.mark_state_apply_applied(unresolved_id) is True
    assert EB.mark_processed(unresolved_id) is True

    with sqlite3.connect(events_db) as conn:
        conn.execute(
            """UPDATE events SET processed_at = datetime('now', '-3 days')
               WHERE event_id IN (?, ?)""",
            (closed_id, unresolved_id),
        )
        conn.execute(
            """UPDATE processed_events SET processed_at = datetime('now', '-3 days')
               WHERE event_id IN (?, ?)""",
            (closed_id, unresolved_id),
        )

    assert EB.cleanup(retention_hours=48) == 2

    with sqlite3.connect(events_db) as conn:
        closed_counts = tuple(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE event_id = ?", (closed_id,)
            ).fetchone()[0]
            for table in ("events", "processed_events", "state_apply_outbox")
        )
        unresolved_counts = tuple(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE event_id = ?",
                (unresolved_id,),
            ).fetchone()[0]
            for table in ("events", "processed_events", "state_apply_outbox")
        )
    assert closed_counts == (0, 0, 0)
    assert unresolved_counts == (1, 1, 1)


def test_equal_time_deltas_have_distinct_durable_transition_keys(
    isolated_stores, monkeypatch
):
    """Blind repro: +5, potem +5 to dwie intencje, nie retry starego T1."""
    monkeypatch.setattr(
        PW.lifecycle_downstream,
        "apply",
        lambda _event: (_ for _ in ()).throw(
            RuntimeError("keep downstream pending")
        ),
    )

    def ck_event(old_iso, old_hhmm, new_iso, new_hhmm):
        return {
            "event_type": "CZAS_KURIERA_UPDATED",
            "payload": {
                "old_ck_iso": old_iso,
                "old_ck_hhmm": old_hhmm,
                "new_ck_iso": new_iso,
                "new_ck_hhmm": new_hhmm,
                "delta_min": 5.0,
                "source": "panel_re_check",
            },
        }

    first_event = ck_event(
        "2026-07-19T12:00:00+02:00",
        "12:00",
        "2026-07-19T12:05:00+02:00",
        "12:05",
    )
    second_event = ck_event(
        "2026-07-19T12:05:00+02:00",
        "12:05",
        "2026-07-19T12:10:00+02:00",
        "12:10",
    )
    first_key = PW._time_update_event_key("c3-order", first_event)
    second_key = PW._time_update_event_key("c3-order", second_event)
    return_to_first_target = ck_event(
        "2026-07-19T12:10:00+02:00",
        "12:10",
        "2026-07-19T12:05:00+02:00",
        "12:05",
    )

    assert first_key != second_key
    assert PW._time_update_event_key("c3-order", first_event) == first_key
    assert PW._time_update_event_key(
        "c3-order", return_to_first_target
    ) != first_key

    first = PW._emit_and_apply_state(
        "CZAS_KURIERA_UPDATED",
        order_id="c3-order",
        courier_id="100",
        payload=first_event["payload"],
        event_id=first_key,
        audit=True,
    )
    second = PW._emit_and_apply_state(
        "CZAS_KURIERA_UPDATED",
        order_id="c3-order",
        courier_id="100",
        payload=second_event["payload"],
        event_id=second_key,
        audit=True,
    )

    assert first.state_ready is True
    assert first.failure_stage == "downstream"
    assert second.event_created is True
    assert second.state_ready is True
    assert _state(isolated_stores[1])["czas_kuriera_warsaw"] == (
        "2026-07-19T12:10:00+02:00"
    )
    with sqlite3.connect(isolated_stores[0]) as conn:
        rows = conn.execute(
            "SELECT event_key, state_status FROM state_apply_outbox ORDER BY rowid"
        ).fetchall()
    assert rows == [(first_key, "applied"), (second_key, "applied")]


def test_pickup_equal_deltas_have_distinct_durable_transition_keys():
    def pickup_event(old_iso, new_iso):
        return {
            "event_type": "PICKUP_TIME_UPDATED",
            "payload": {
                "old_pickup_at_warsaw": old_iso,
                "new_pickup_at_warsaw": new_iso,
                "old_prep_minutes": 20,
                "new_prep_minutes": 20,
                "new_decision_deadline": None,
                "new_zmiana_czasu_odbioru": True,
                "delta_min": 5.0,
                "source": "panel_re_check",
            },
        }

    first = pickup_event(
        "2026-07-19T12:00:00+02:00",
        "2026-07-19T12:05:00+02:00",
    )
    second = pickup_event(
        "2026-07-19T12:05:00+02:00",
        "2026-07-19T12:10:00+02:00",
    )

    first_key = PW._time_update_event_key("c3-order", first)
    assert first_key == PW._time_update_event_key("c3-order", first)
    assert first_key != PW._time_update_event_key("c3-order", second)


def test_crash_after_state_before_downstream_is_drained_once(
    isolated_stores, monkeypatch
):
    """Receipt downstream pozostaje pending i drainer domyka go po crashu."""
    _events_db, state_path = isolated_stores
    calls = []

    def crash_downstream(event):
        calls.append(("crash", event["event_id"]))
        raise RuntimeError("synthetic crash window")

    monkeypatch.setattr(PW.lifecycle_downstream, "apply", crash_downstream)
    first = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())
    assert first.state_ready is True
    assert first.failure_stage == "downstream"
    assert _state(state_path)["status"] == "returned_to_pool"
    pending_row = EB.get_state_apply_outbox(first.event_id)
    assert pending_row["downstream_status"] == "pending"
    assert pending_row["last_error"].startswith("downstream:RuntimeError")

    recovered = []
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=recovered.append,
    )
    again = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())

    assert counts == {
        "seen": 0,
        "state_ready": 0,
        "downstream": 1,
        "superseded": 0,
        "failed": 0,
    }
    assert len(recovered) == 1
    assert again.downstream_executed is False
    assert EB.get_state_apply_outbox(first.event_id)["last_error"] is None
    history = [h for h in _state(state_path)["history"] if h["event"] == "ORDER_RETURNED_TO_POOL"]
    assert len(history) == 1


def test_internal_plan_error_keeps_downstream_receipt_pending(
    isolated_stores, monkeypatch
):
    """Helper best-effort musi przejsc w tryb strict pod trwalym outboxem."""
    from dispatch_v2 import common
    from dispatch_v2 import plan_manager
    from dispatch_v2 import plan_recheck

    _events_db, state_path = isolated_stores
    # Bramka A (audyt Sola 20.07): usuwanie stopu jest za flagą także w torze durable.
    # Ten test bada strict-error POD włączoną funkcją — włącz ją jawnie.
    monkeypatch.setattr(common, "ENABLE_REASSIGN_OLD_PLAN_RELEASE", True)
    monkeypatch.setattr(common, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(
        plan_manager,
        "remove_stops",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic inner plan failure")
        ),
    )
    monkeypatch.setattr(plan_recheck, "recanon_courier", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY
    )

    first = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL", **_returned_kwargs()
    )

    assert first.state_ready is True
    assert first.failure_stage == "downstream"
    assert _state(state_path)["status"] == "returned_to_pool"
    row = EB.get_state_apply_outbox(first.event_id)
    assert row["downstream_status"] == "pending"
    assert "synthetic inner plan failure" in row["last_error"]

    recovered = []
    monkeypatch.setattr(
        plan_manager,
        "remove_stops",
        lambda courier_id, order_id: recovered.append((courier_id, order_id)),
    )
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=_REAL_LIFECYCLE_DOWNSTREAM_APPLY,
    )

    assert counts["downstream"] == 1
    assert recovered == [("100", "c3-order")]
    assert EB.get_state_apply_outbox(first.event_id)["downstream_status"] == "applied"


def test_downstream_state_read_is_fail_closed_and_retryable(
    isolated_stores, monkeypatch
):
    monkeypatch.setattr(
        PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY
    )
    monkeypatch.setattr(
        SM,
        "get_order_strict",
        lambda _oid: (_ for _ in ()).throw(
            SM.StateReadError("synthetic downstream strict-read failure")
        ),
    )

    outcome = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL", **_returned_kwargs()
    )

    assert outcome.state_ready is True
    assert outcome.failure_stage == "downstream"
    row = EB.get_state_apply_outbox(outcome.event_id)
    assert row["downstream_status"] == "pending"
    assert "synthetic downstream strict-read failure" in row["last_error"]


def test_learning_record_is_not_duplicated_when_later_downstream_step_retries(
    isolated_stores, monkeypatch, tmp_path
):
    """Crash po PANEL_AGREE, przed receiptem, nie moze dopisac drugiej linii."""
    pending_path = tmp_path / "pending_proposals.json"
    learning_path = tmp_path / "learning_log.jsonl"
    pending_path.write_text(
        json.dumps(
            {
                "c3-order": {
                    "decision_record": {
                        "best": {"courier_id": "200", "score": 42.0},
                        "verdict": "PROPOSE",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(pending_path))
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning_path))
    monkeypatch.setattr(PW, "_panel_agree_enabled", lambda: True)
    monkeypatch.setattr(PW, "_remove_stops_on_return", lambda *a, **k: None)
    monkeypatch.setattr(
        PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY
    )
    save_attempts = 0

    def fail_after_learning_once(*_args, **_kwargs):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise RuntimeError("synthetic failure after learning append")

    monkeypatch.setattr(PW, "_save_plan_on_assign_signal", fail_after_learning_once)
    kwargs = {
        "order_id": "c3-order",
        "courier_id": "200",
        "payload": {"source": "panel_reassign"},
        "event_id": "c3-order_COURIER_ASSIGNED_200_learning_retry",
        "audit": True,
    }

    first = PW._emit_and_apply_state("COURIER_ASSIGNED", **kwargs)
    rotated_learning = learning_path.with_name(f"{learning_path.name}.1")
    rotated_learning.write_bytes(learning_path.read_bytes())
    learning_path.write_bytes(b"")
    second = PW._emit_and_apply_state("COURIER_ASSIGNED", **kwargs)

    assert first.failure_stage == "downstream"
    assert second.state_ready is True
    assert second.downstream_executed is True
    records = [
        json.loads(line)
        for path in (rotated_learning, learning_path)
        for line in path.read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]["action"] == "PANEL_AGREE"
    assert records[0]["lifecycle_event_id"] == first.event_id
    assert save_attempts == 2
    assert EB.get_state_apply_outbox(first.event_id)["downstream_attempts"] == 2


def test_plan_recanon_strict_mode_propagates_internal_read_error(
    tmp_path, monkeypatch
):
    """Plan helper rozroznia legacy best-effort od retryable outbox error."""
    from dispatch_v2 import plan_recheck

    missing = tmp_path / "missing-orders-state.json"
    monkeypatch.setattr(plan_recheck, "_refresh_d3_fala_a_flags", lambda: None)
    monkeypatch.setattr(plan_recheck, "ENABLE_RECANON_ON_WRITE", True)
    monkeypatch.setattr(plan_recheck, "ORDERS_STATE_PATH", str(missing))

    assert plan_recheck.recanon_courier("100") is False
    with pytest.raises(FileNotFoundError):
        plan_recheck.recanon_courier("100", _raise_on_error=True)


def test_two_threads_cannot_double_apply_or_downstream(isolated_stores, monkeypatch):
    """Blind repro check-then-act: cross-thread flock serializuje caly protokol."""
    _events_db, state_path = isolated_stores
    downstream = []
    downstream_lock = threading.Lock()

    def record(event):
        with downstream_lock:
            downstream.append(event["event_id"])

    monkeypatch.setattr(PW.lifecycle_downstream, "apply", record)
    barrier = threading.Barrier(2)

    def invoke():
        barrier.wait(timeout=5)
        return PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _i: invoke(), range(2)))

    assert sum(o.event_created for o in outcomes) == 1
    assert sum(o.state_transitioned for o in outcomes) == 1
    assert sum(o.downstream_executed for o in outcomes) == 1
    history = [h for h in _state(state_path)["history"] if h["event"] == "ORDER_RETURNED_TO_POOL"]
    assert len(history) == 1
    assert len(downstream) == 1


def test_direct_state_writer_waits_for_durable_version_boundary(
    isolated_stores, monkeypatch
):
    """Kazdy orders_state writer musi respektowac lock check-version->apply."""
    entered_apply = threading.Event()
    direct_started = threading.Event()
    direct_finished = threading.Event()
    real_apply = SM.update_from_event

    def paused_apply(event):
        entered_apply.set()
        assert direct_started.wait(timeout=5)
        assert direct_finished.wait(timeout=0.1) is False
        return real_apply(event)

    def direct_writer():
        assert entered_apply.wait(timeout=5)
        direct_started.set()
        real_apply({
            "event_type": "COURIER_ASSIGNED",
            "event_id": "direct-writer-T2",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "direct_test"},
        })
        direct_finished.set()

    monkeypatch.setattr(PW, "update_from_event", paused_apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        durable = pool.submit(
            PW._emit_and_apply_state,
            "ORDER_RETURNED_TO_POOL",
            **_returned_kwargs(),
        )
        direct = pool.submit(direct_writer)
        outcome = durable.result(timeout=5)
        direct.result(timeout=5)

    assert outcome.state_ready is True
    assert direct_finished.is_set()


def test_slow_downstream_does_not_block_direct_state_writer(
    isolated_stores, monkeypatch
):
    """Plan/OSRM lane nie moze trzymac globalnego locka orders_state."""
    _events_db, state_path = isolated_stores
    downstream_entered = threading.Event()
    release_downstream = threading.Event()

    def slow_downstream(_event):
        downstream_entered.set()
        assert release_downstream.wait(timeout=5)

    monkeypatch.setattr(PW.lifecycle_downstream, "apply", slow_downstream)
    with ThreadPoolExecutor(max_workers=2) as pool:
        durable = pool.submit(
            PW._emit_and_apply_state,
            "ORDER_RETURNED_TO_POOL",
            **_returned_kwargs(),
        )
        assert downstream_entered.wait(timeout=5)
        direct = pool.submit(
            SM.upsert_order,
            "c3-order",
            {"writer_probe": "completed_while_downstream_waited"},
            event="DIRECT_WRITER_PROBE",
        )
        direct.result(timeout=1)
        assert durable.done() is False
        release_downstream.set()
        outcome = durable.result(timeout=5)

    assert outcome.state_ready is True
    assert outcome.downstream_executed is True
    assert _state(state_path)["writer_probe"] == "completed_while_downstream_waited"


def test_pending_downstream_is_drained_in_state_apply_fifo(isolated_stores):
    """Dwa zastosowane state nie moga odwrocic kolejnosci plan/recanon."""
    for idx in (1, 2):
        event_id = f"fifo-{idx}_NEW_ORDER_v1"
        state_event = {
            "event_type": "NEW_ORDER",
            "event_id": event_id,
            "order_id": f"fifo-{idx}",
            "courier_id": None,
            "payload": {"first_seen": f"2026-07-19T08:0{idx}:00+00:00"},
        }
        assert EB.emit(
            "NEW_ORDER",
            order_id=f"fifo-{idx}",
            event_id=event_id,
            state_event=state_event,
            event_key=f"fifo-{idx}_NEW_ORDER_first",
            expected_state_version=None,
        ) == event_id
        assert EB.mark_state_apply_applied(event_id) is True

    callback_order = []
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=lambda event: callback_order.append(event["event_id"]),
    )

    assert callback_order == ["fifo-1_NEW_ORDER_v1", "fifo-2_NEW_ORDER_v1"]
    assert counts["downstream"] == 2
    assert counts["failed"] == 0


def test_none_downstream_is_an_explicit_successful_noop(isolated_stores):
    """Brak callbacku domyka receipt zamiast tworzyc wieczny pending row."""
    outcome = DEA.emit_and_apply(
        "ORDER_RETURNED_TO_POOL",
        order_id="c3-order",
        courier_id="100",
        payload={"reason": "cancelled", "source": "no_downstream_test"},
        state_payload=None,
        event_key="c3-order_ORDER_RETURNED_no_downstream",
        emit_fn=EB.emit_audit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=None,
    )

    assert outcome.state_ready is True
    assert outcome.failure_stage is None
    assert outcome.downstream_executed is False
    assert EB.get_state_apply_outbox(outcome.event_id)["downstream_status"] == "applied"


def test_none_downstream_cannot_swallow_an_older_receipt(isolated_stores):
    """No-op target nie nadaje authority do pomijania cudzej starszej pracy."""
    older_id = "older_NEW_ORDER_v1"
    older_event = {
        "event_type": "NEW_ORDER",
        "event_id": older_id,
        "order_id": "older",
        "courier_id": None,
        "payload": {"first_seen": "2026-07-19T08:00:00+00:00"},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="older",
        event_id=older_id,
        state_event=older_event,
        event_key="older_NEW_ORDER_first",
        expected_state_version=None,
    ) == older_id
    assert EB.mark_state_apply_applied(older_id) is True

    target = DEA.emit_and_apply(
        "NEW_ORDER",
        order_id="target",
        courier_id=None,
        payload={"first_seen": "2026-07-19T08:01:00+00:00"},
        state_payload=None,
        event_key="target_NEW_ORDER_first",
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=None,
    )

    assert target.state_ready is True
    assert target.failure_stage == "downstream"
    assert EB.get_state_apply_outbox(older_id)["downstream_status"] == "pending"
    assert EB.get_state_apply_outbox(target.event_id)["downstream_status"] == "pending"


def test_apply_exception_matching_other_marker_is_superseded(
    isolated_stores, monkeypatch
):
    """Status T2 nie moze zamknac receiptu ani downstream eventu T1."""
    downstream = []

    def other_effect_then_raise(_event):
        SM.upsert_order(
            "c3-order",
            {
                "status": "returned_to_pool",
                "courier_id": None,
                "last_lifecycle_event_id_order_returned_to_pool": "other-T2",
            },
            event="OTHER_T2",
        )
        raise RuntimeError("T1 synthetic exception")

    monkeypatch.setattr(PW, "update_from_event", other_effect_then_raise)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", downstream.append)

    outcome = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL", **_returned_kwargs()
    )

    assert outcome.superseded is True
    assert outcome.state_ready is False
    assert downstream == []
    row = EB.get_state_apply_outbox(outcome.event_id)
    assert row["state_status"] == "superseded"
    assert row["downstream_status"] == "skipped"


def test_retry_order_is_fair_after_permanent_failures_with_frozen_clock(
    isolated_stores, monkeypatch
):
    """LIMIT nie moze glodzic 101. row przez 100 trwale blednych starszych."""
    _events_db, _state_path = isolated_stores
    monkeypatch.setattr(EB, "now_iso", lambda: "2026-07-19T08:00:00+00:00")
    target_oid = "fair-100"
    for idx in range(101):
        oid = f"fair-{idx}"
        event_id = f"{oid}_NEW_ORDER_v1"
        state_event = {
            "event_type": "NEW_ORDER",
            "event_id": event_id,
            "order_id": oid,
            "courier_id": None,
            "payload": {"first_seen": "2026-07-19T08:00:00+00:00"},
        }
        assert EB.emit(
            "NEW_ORDER",
            order_id=oid,
            event_id=event_id,
            state_event=state_event,
            event_key=f"{oid}_NEW_ORDER_first",
            expected_state_version=None,
        ) == event_id

    def fail_old_rows(event):
        if event["order_id"] != target_oid:
            raise RuntimeError("permanent old-row failure")
        return SM.update_from_event(event)

    first = DEA.drain_pending(
        state_update_fn=fail_old_rows,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=lambda _event: None,
        limit=100,
    )
    second = DEA.drain_pending(
        state_update_fn=fail_old_rows,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order,
        downstream_fn=lambda _event: None,
        limit=100,
    )

    assert first["failed"] == 100
    assert second["state_ready"] == 1
    # Pending state ma osobny lane: 100 uszkodzonych, niezależnych receiptów
    # nie może już zatrzymać gotowego callbacku 101. zlecenia.
    assert second["downstream"] == 1
    assert EB.get_state_apply_outbox("fair-100_NEW_ORDER_v1")["state_status"] == "applied"
    assert SM.get_order(target_oid)["status"] == "planned"


def test_same_timestamp_fifo_uses_insert_order_not_event_id(
    isolated_stores, monkeypatch
):
    monkeypatch.setattr(EB, "now_iso", lambda: "2026-07-19T08:00:00+00:00")
    ids = ["z-first", "a-second"]
    for event_id in ids:
        event = {
            "event_type": "NEW_ORDER",
            "event_id": event_id,
            "order_id": event_id,
            "courier_id": None,
            "payload": {},
        }
        assert EB.emit(
            "NEW_ORDER",
            order_id=event_id,
            event_id=event_id,
            state_event=event,
            event_key=f"{event_id}_NEW_ORDER",
        ) == event_id
        assert EB.mark_state_apply_applied(event_id) is True

    oldest = EB.get_oldest_pending_downstream()
    assert oldest["event_id"] == "z-first"


def test_unknown_version_without_storage_token_fails_closed(
    isolated_stores, monkeypatch
):
    """Nieudowodniony snapshot nie daje prawa do RMW nawet po udanym readzie."""
    reads = 0
    updates = []

    def unavailable_then_unversioned(_oid):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise SM.StateReadError("synthetic pre-read failure")
        return {
            "order_id": "unversioned",
            "status": "assigned",
            "courier_id": "100",
        }

    monkeypatch.setattr(
        SM,
        "state_storage_token",
        lambda: (_ for _ in ()).throw(OSError("synthetic token failure")),
    )
    outcome = DEA.emit_and_apply(
        "ORDER_RETURNED_TO_POOL",
        order_id="unversioned",
        courier_id="100",
        payload={"reason": "cancelled"},
        state_payload=None,
        event_key="unversioned_ORDER_RETURNED_cancelled",
        emit_fn=EB.emit_audit,
        state_update_fn=updates.append,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=unavailable_then_unversioned,
        downstream_fn=lambda _event: None,
    )

    assert outcome.event_created is True
    assert outcome.failure_stage == "state_token"
    assert outcome.state_ready is False
    assert updates == []
    assert EB.get_state_apply_outbox(outcome.event_id)["state_status"] == "pending"


def test_missing_baseline_token_terminalizes_after_storage_recovers_and_unblocks_fifo(
    isolated_stores, monkeypatch
):
    """An unprovable double-read failure is loud/terminal, never global poison."""
    reads = 0
    token_reads = 0
    updates = []

    def unavailable_then_current(_oid):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise SM.StateReadError("synthetic pre-read failure")
        return {
            "order_id": "unversioned-recovery",
            "status": "assigned",
            "courier_id": "100",
        }

    def token_unavailable_twice_then_recovers():
        nonlocal token_reads
        token_reads += 1
        if token_reads <= 2:
            raise OSError("synthetic token failure")
        return "sha256:recovered-current-snapshot"

    monkeypatch.setattr(
        SM, "state_storage_token", token_unavailable_twice_then_recovers
    )
    first = DEA.emit_and_apply(
        "ORDER_RETURNED_TO_POOL",
        order_id="unversioned-recovery",
        courier_id="100",
        payload={"reason": "cancelled"},
        state_payload=None,
        event_key="unversioned-recovery_ORDER_RETURNED_cancelled",
        emit_fn=EB.emit_audit,
        state_update_fn=updates.append,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=unavailable_then_current,
        downstream_fn=lambda _event: None,
    )
    assert first.failure_stage == "state_token"
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "pending"

    later_id = "later_NEW_ORDER_v1"
    later_event = {
        "event_type": "NEW_ORDER",
        "event_id": later_id,
        "order_id": "later-order",
        "courier_id": None,
        "payload": {},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="later-order",
        event_id=later_id,
        state_event=later_event,
        event_key="later_NEW_ORDER",
    ) == later_id
    assert EB.mark_state_apply_applied(later_id) is True
    downstream = []

    DEA.drain_pending(
        state_update_fn=updates.append,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=unavailable_then_current,
        downstream_fn=lambda event: downstream.append(event["event_id"]),
    )

    first_row = EB.get_state_apply_outbox(first.event_id)
    assert first_row["state_status"] == "superseded"
    assert "missing expected state storage token" in first_row["last_error"]
    assert EB.get_state_apply_outbox(later_id)["downstream_status"] == "applied"
    assert downstream == [later_id]
    assert updates == []


def test_lifecycle_lock_excludes_threads_in_same_process(isolated_stores):
    """fcntl sam nie jest mutexem watkowym; drugi watek musi realnie czekac."""
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def holder():
        with SM.lifecycle_apply_lock():
            first_entered.set()
            assert release_first.wait(timeout=5)

    def waiter():
        assert first_entered.wait(timeout=5)
        with SM.lifecycle_apply_lock():
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(holder)
        second = pool.submit(waiter)
        assert first_entered.wait(timeout=5)
        assert second_entered.wait(timeout=0.1) is False
        release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert second_entered.is_set()


def test_older_delivered_is_superseded_by_newer_returned(
    isolated_stores, monkeypatch
):
    """Pending terminal T1 nie moze nadpisac nowszego terminala T2."""
    _events_db, state_path = isolated_stores
    monkeypatch.setattr(PW, "update_from_event", lambda _event: (_ for _ in ()).throw(RuntimeError("T1 fail")))
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )
    assert first.state_ready is False

    SM.update_from_event(
        {
            "event_type": "ORDER_RETURNED_TO_POOL",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"reason": "cancelled", "source": "newer"},
        }
    )
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)
    retry = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )

    assert retry.superseded is True
    assert retry.state_ready is False
    assert _state(state_path)["status"] == "returned_to_pool"
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "superseded"


def test_older_delivered_is_superseded_by_newer_delivered_with_other_payload(
    isolated_stores, monkeypatch
):
    """Sam status=delivered nie dowodzi apply starego T1 po nowszym T2."""
    _events_db, state_path = isolated_stores
    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("T1 fail")),
    )
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )
    assert first.state_ready is False

    SM.update_from_event(
        {
            "event_type": "COURIER_DELIVERED",
            "event_id": "c3-order_COURIER_DELIVERED_newer_T2",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"timestamp": "T2"},
        }
    )
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)
    retry = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T1"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )

    assert retry.superseded is True
    assert _state(state_path)["delivered_at"] == "T2"
    assert _state(state_path)["last_lifecycle_event_id"].endswith("newer_T2")


def test_nonthrowing_noop_is_not_reported_as_success(isolated_stores, monkeypatch):
    monkeypatch.setattr(PW, "update_from_event", lambda _event: None)
    outcome = PW._emit_and_apply_state("ORDER_RETURNED_TO_POOL", **_returned_kwargs())

    assert outcome.event_created is True
    assert outcome.state_ready is False
    assert outcome.failure_stage == "state_postcondition"
    assert EB.get_state_apply_outbox(outcome.event_id)["state_status"] == "pending"


def test_reused_semantic_key_gets_new_generation_after_real_state_change(
    isolated_stores, monkeypatch
):
    """100->200->100 nie jest mylone ze starym dedupem targetu 100."""
    _events_db, state_path = isolated_stores
    SM.update_from_event(
        {
            "event_type": "ORDER_RETURNED_TO_POOL",
            "order_id": "c3-order",
            "courier_id": "100",
            "payload": {"reason": "reset"},
        }
    )
    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "test"},
        }
    )
    key = "c3-order_COURIER_ASSIGNED_100_reassign"
    first = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "panel_reassign"},
        event_id=key,
        audit=True,
    )
    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "test"},
        }
    )
    second = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "panel_reassign"},
        event_id=key,
        audit=True,
    )

    assert first.event_created is True
    assert second.event_created is True
    assert first.event_id != second.event_id
    assert _state(state_path)["courier_id"] == "100"


def test_reused_key_creates_new_generation_while_old_downstream_is_pending(
    isolated_stores,
):
    """A->B->A nie moze zostac pomylone z retry starego callbacku A."""
    events_db, state_path = isolated_stores

    def fail_downstream(_event):
        raise RuntimeError("synthetic downstream outage")

    def assign(cid, key, downstream):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id=cid,
            payload={"source": "generation-test"},
            state_payload=None,
            event_key=key,
            emit_fn=EB.emit_audit,
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=SM.get_order_strict,
            downstream_fn=downstream,
        )

    key_a = "c3-order_COURIER_ASSIGNED_100_canonical"
    old_a = assign("100", key_a, fail_downstream)
    to_b = assign(
        "200", "c3-order_COURIER_ASSIGNED_200_canonical", fail_downstream
    )

    assert old_a.state_ready is True
    assert old_a.failure_stage == "downstream"
    assert to_b.state_transitioned is True
    assert _state(state_path)["courier_id"] == "200"
    assert EB.get_state_apply_outbox(old_a.event_id)["downstream_status"] == "pending"

    callbacks = []
    new_a = assign("100", key_a, callbacks.append)

    assert new_a.event_created is True
    assert new_a.event_id != old_a.event_id
    assert new_a.state_transitioned is True
    assert _state(state_path)["courier_id"] == "100"
    assert [event["event_id"] for event in callbacks] == [
        old_a.event_id,
        to_b.event_id,
        new_a.event_id,
    ]
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox"
        ).fetchone()[0] == 3


def test_reused_key_survives_two_read_failures_after_full_state_cycle(
    isolated_stores,
):
    """A->B->A nie ginie, gdy oba odczyty przed persist chwilowo zawodza."""
    events_db, state_path = isolated_stores
    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "c3-order_COURIER_ASSIGNED_200_test_setup",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "generation-test-setup"},
        }
    )

    def fail_downstream(_event):
        raise RuntimeError("synthetic downstream outage")

    def assign(cid, key, downstream, get_order_fn=SM.get_order_strict):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id=cid,
            payload={"source": "generation-read-outage-test"},
            state_payload=None,
            event_key=key,
            emit_fn=EB.emit_audit,
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=get_order_fn,
            downstream_fn=downstream,
        )

    key_a = "c3-order_COURIER_ASSIGNED_100_canonical"
    old_a = assign("100", key_a, fail_downstream)
    to_b = assign(
        "200", "c3-order_COURIER_ASSIGNED_200_canonical", fail_downstream
    )
    reads = 0

    def fail_generation_reads_then_recover(oid):
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise SM.StateReadError("synthetic two-read outage")
        return SM.get_order_strict(oid)

    callbacks = []
    new_a = assign(
        "100", key_a, callbacks.append, fail_generation_reads_then_recover
    )

    assert reads >= 3
    assert old_a.state_ready is True
    assert to_b.state_ready is True
    assert new_a.event_created is True
    assert new_a.event_id != old_a.event_id
    assert new_a.state_transitioned is True
    assert _state(state_path)["courier_id"] == "100"
    assert [event["event_id"] for event in callbacks] == [
        old_a.event_id,
        to_b.event_id,
        new_a.event_id,
    ]
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox"
        ).fetchone()[0] == 3


def test_read_outage_on_plain_retry_finishes_old_callback_without_duplicate(
    isolated_stores,
):
    """Niepewny retry A nie moze ani zgubic, ani zdublowac starego callbacku."""
    events_db, state_path = isolated_stores
    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "c3-order_COURIER_ASSIGNED_200_test_setup",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "generation-test-setup"},
        }
    )
    key_a = "c3-order_COURIER_ASSIGNED_100_canonical"

    def fail_downstream(_event):
        raise RuntimeError("synthetic downstream outage")

    def assign(downstream, get_order_fn=SM.get_order_strict):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id="100",
            payload={"source": "generation-read-outage-test"},
            state_payload=None,
            event_key=key_a,
            emit_fn=EB.emit_audit,
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=get_order_fn,
            downstream_fn=downstream,
        )

    old_a = assign(fail_downstream)
    assert old_a.failure_stage == "downstream"
    assert _state(state_path)["last_lifecycle_event_id"] == old_a.event_id
    reads = 0

    def fail_generation_reads_then_recover(oid):
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise SM.StateReadError("synthetic two-read outage")
        return SM.get_order_strict(oid)

    callbacks = []
    retry = assign(callbacks.append, fail_generation_reads_then_recover)

    assert reads >= 3
    assert retry.event_id == old_a.event_id
    assert retry.state_ready is True
    assert retry.downstream_executed is True
    assert [event["event_id"] for event in callbacks] == [old_a.event_id]
    assert _state(state_path)["last_lifecycle_event_id"] == old_a.event_id
    with sqlite3.connect(events_db) as conn:
        rows = conn.execute(
            """SELECT event_id, state_status, downstream_status
               FROM state_apply_outbox ORDER BY rowid"""
        ).fetchall()
    assert len(rows) == 2
    assert rows[0] == (old_a.event_id, "applied", "applied")
    assert rows[1][0] != old_a.event_id
    assert rows[1][1:] == ("superseded", "skipped")


def test_retry_prefers_existing_pending_same_key_successor(
    isolated_stores,
):
    """Pending nowe A nie moze zostac zasloniete przez stary callback A."""
    events_db, state_path = isolated_stores
    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "c3-order_COURIER_ASSIGNED_200_test_setup",
            "order_id": "c3-order",
            "courier_id": "200",
            "payload": {"source": "generation-test-setup"},
        }
    )

    def fail_downstream(_event):
        raise RuntimeError("synthetic downstream outage")

    def assign(cid, key, get_order_fn, downstream):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id=cid,
            payload={"source": "pending-successor-test"},
            state_payload=None,
            event_key=key,
            emit_fn=EB.emit_audit,
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=get_order_fn,
            downstream_fn=downstream,
        )

    key_a = "c3-order_COURIER_ASSIGNED_100_canonical"
    old_a = assign("100", key_a, SM.get_order_strict, fail_downstream)
    to_b = assign(
        "200",
        "c3-order_COURIER_ASSIGNED_200_canonical",
        SM.get_order_strict,
        fail_downstream,
    )
    reads = 0

    def fail_only_successor_apply_read(oid):
        nonlocal reads
        reads += 1
        if reads == 3:
            raise SM.StateReadError("synthetic successor apply-read outage")
        return SM.get_order_strict(oid)

    pending_a = assign("100", key_a, fail_only_successor_apply_read, fail_downstream)
    assert pending_a.event_created is True
    assert pending_a.failure_stage == "state_read"
    assert _state(state_path)["courier_id"] == "200"
    assert EB.get_state_apply_outbox(pending_a.event_id)["state_status"] == "pending"

    callbacks = []
    retry = assign("100", key_a, SM.get_order_strict, callbacks.append)

    assert retry.event_created is False
    assert retry.event_id == pending_a.event_id
    assert retry.state_transitioned is True
    assert _state(state_path)["courier_id"] == "100"
    assert [event["event_id"] for event in callbacks] == [
        old_a.event_id,
        to_b.event_id,
        pending_a.event_id,
    ]
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox"
        ).fetchone()[0] == 3


def test_retry_exact_pending_successor_does_not_fork_third_generation(
    isolated_stores,
):
    """Pending T2/B must win over older pending T1/A for an exact B retry."""
    event_key = "c3-order_COURIER_ASSIGNED_shared_retry_key"
    current = {
        "order_id": "c3-order",
        "status": "assigned",
        "courier_id": "100",
        "updated_at": "2026-07-19T08:00:00+00:00",
    }

    def persistently_fail(_event):
        raise RuntimeError("synthetic persistent state outage")

    def assign(source):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id="200",
            payload={"source": source},
            state_payload=None,
            event_key=event_key,
            emit_fn=EB.emit_audit,
            state_update_fn=persistently_fail,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=lambda _oid: current,
            downstream_fn=lambda _event: None,
        )

    t1 = assign("intent-A")
    t2 = assign("intent-B")
    retry_b = assign("intent-B")

    assert t1.failure_stage == "state_apply"
    assert t2.failure_stage == "state_predecessor"
    assert retry_b.failure_stage == "state_predecessor"
    assert retry_b.event_id == t2.event_id
    assert retry_b.event_created is False
    with sqlite3.connect(isolated_stores[0]) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox WHERE event_key=?",
            (event_key,),
        ).fetchone()[0] == 2


def test_resurrection_invalidates_crash_marker_before_delivered_recovery(
    isolated_stores,
):
    """Stary DELIVERED state-pending nie odzyskuje prawa po korekcie."""
    _events_db, state_path = isolated_stores
    initial = _state(state_path)
    event_id = "c3-order_COURIER_DELIVERED_crash_before_receipt"
    event = {
        "event_type": "COURIER_DELIVERED",
        "event_id": event_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T08:05:00+00:00"},
    }
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=event["payload"],
        event_id=event_id,
        state_event=event,
        event_key="c3-order_COURIER_DELIVERED_canonical",
        expected_state_version=initial["updated_at"],
        expected_state_marker=initial.get("last_lifecycle_event_id"),
    ) == event_id

    # Crash window: JSON zawiera exact marker, SQLite receipt nadal pending.
    SM.update_from_event(event)
    assert EB.get_state_apply_outbox(event_id)["state_status"] == "pending"
    _apply_resurrection("picked_up")

    callbacks = []
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=callbacks.append,
    )

    current = _state(state_path)
    assert current["status"] == "picked_up"
    assert current["last_lifecycle_event_id_courier_delivered"] is None
    assert current["last_lifecycle_event_id_order_resurrected"] == current[
        "last_lifecycle_event_id"
    ]
    assert counts["superseded"] == 1
    assert callbacks == []
    assert EB.get_state_apply_outbox(event_id)["state_status"] == "superseded"


def test_resurrection_skips_already_applied_stale_delivered_downstream(
    isolated_stores, monkeypatch
):
    """Korekta chroni tez receipt state=applied/downstream=pending."""
    _events_db, state_path = isolated_stores

    def fail_downstream(_event):
        raise RuntimeError("synthetic callback outage")

    delivered = DEA.emit_and_apply(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:05:00+00:00"},
        state_payload=None,
        event_key="c3-order_COURIER_DELIVERED_canonical",
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=fail_downstream,
    )
    assert delivered.state_ready is True
    assert delivered.failure_stage == "downstream"

    _apply_resurrection("assigned")
    plan_calls = []
    monkeypatch.setattr(PW, "_advance_plan_on_deliver", lambda *a, **k: plan_calls.append((a, k)))
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=_REAL_LIFECYCLE_DOWNSTREAM_APPLY,
    )

    assert _state(state_path)["status"] == "assigned"
    assert plan_calls == []
    assert counts["downstream"] == 1
    assert EB.get_state_apply_outbox(delivered.event_id)["downstream_status"] == "applied"


def test_permanently_invalid_time_event_is_terminal_not_fifo_poison(
    isolated_stores,
):
    """Wadliwy ISO/HH:MM jest audytowany, superseded i nie blokuje T2."""
    _events_db, state_path = isolated_stores
    bad = PW._emit_and_apply_state(
        "CZAS_KURIERA_UPDATED",
        order_id="c3-order",
        courier_id="100",
        payload={
            "new_ck_iso": "2026-07-19T12:05:00+02:00",
            "new_ck_hhmm": "12:06",
            "source": "coordinator_edit",
        },
        event_id="c3-order_CZAS_KURIERA_UPDATED_bad",
        audit=True,
    )

    assert bad.superseded is True
    assert bad.state_ready is False
    assert EB.get_state_apply_outbox(bad.event_id)["state_status"] == "superseded"
    assert EB.get_oldest_unfinished_apply() is None

    good = PW._emit_and_apply_state(
        "CZAS_KURIERA_UPDATED",
        order_id="c3-order",
        courier_id="100",
        payload={
            "new_ck_iso": "2026-07-19T12:10:00+02:00",
            "new_ck_hhmm": "12:10",
            "source": "coordinator_edit",
        },
        event_id="c3-order_CZAS_KURIERA_UPDATED_good",
        audit=True,
    )
    assert good.state_ready is True
    assert _state(state_path)["czas_kuriera_hhmm"] == "12:10"


def test_intentionally_suppressed_passive_time_event_is_terminal(
    isolated_stores,
):
    _events_db, state_path = isolated_stores
    SM.upsert_order(
        "c3-order",
        {
            "order_type": "czasowka",
            "czas_kuriera_warsaw": "2026-07-19T12:00:00+02:00",
            "czas_kuriera_hhmm": "12:00",
        },
        event="TEST_SETUP",
    )
    outcome = PW._emit_and_apply_state(
        "CZAS_KURIERA_UPDATED",
        order_id="c3-order",
        courier_id="100",
        payload={
            "new_ck_iso": "2026-07-19T12:15:00+02:00",
            "new_ck_hhmm": "12:15",
            "source": "panel_re_check",
        },
        event_id="c3-order_CZAS_KURIERA_UPDATED_passive",
        audit=True,
    )

    assert outcome.superseded is True
    assert _state(state_path)["czas_kuriera_hhmm"] == "12:00"
    assert EB.get_state_apply_outbox(outcome.event_id)["state_status"] == "superseded"
    assert EB.get_oldest_unfinished_apply() is None


def test_pickup_twin_sources_collapse_to_one_durable_generation(
    isolated_stores,
):
    events_db, state_path = isolated_stores
    key = "c3-order_COURIER_PICKED_UP_canonical"
    panel = PW._emit_and_apply_state(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19 10:00:00", "source": "reconcile"},
        event_id=key,
    )
    ground_truth = PW._emit_and_apply_state(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload={
            "timestamp": "2026-07-19 10:00:01",
            "source": "ground_truth_fallback",
        },
        event_id=key,
    )

    assert panel.event_created is True
    assert ground_truth.event_created is False
    assert ground_truth.event_id == panel.event_id
    assert _state(state_path)["picked_up_at"] == "2026-07-19 10:00:00"
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox WHERE event_key=?", (key,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_type='COURIER_PICKED_UP'"
        ).fetchone()[0] == 1


def test_cleanup_retains_closed_predecessor_of_unresolved_child(
    isolated_stores, monkeypatch
):
    """Retencja nie moze osierocic causal predecessor T1->T2."""
    events_db, _state_path = isolated_stores
    t1 = "cleanup-parent"
    t2 = "cleanup-child"
    event1 = {
        "event_type": "NEW_ORDER",
        "event_id": t1,
        "order_id": "cleanup-order",
        "courier_id": None,
        "payload": {},
    }
    event2 = {
        "event_type": "NEW_ORDER",
        "event_id": t2,
        "order_id": "cleanup-order",
        "courier_id": None,
        "payload": {},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="cleanup-order",
        event_id=t1,
        state_event=event1,
        event_key="cleanup-parent-key",
    ) == t1
    assert EB.mark_state_apply_applied(t1) is True
    assert EB.mark_state_apply_downstream(t1) is True
    assert EB.mark_processed(t1) is True
    assert EB.emit(
        "NEW_ORDER",
        order_id="cleanup-order",
        event_id=t2,
        state_event=event2,
        event_key="cleanup-child-key",
        predecessor_event_id=t1,
    ) == t2
    with sqlite3.connect(events_db) as conn:
        conn.execute(
            "UPDATE events SET processed_at='2020-01-01T00:00:00+00:00' WHERE event_id=?",
            (t1,),
        )
        conn.execute(
            "UPDATE processed_events SET processed_at='2020-01-01T00:00:00+00:00' WHERE event_id=?",
            (t1,),
        )
    monkeypatch.setattr(EB, "_is_peak_window", lambda: False)

    EB.cleanup(retention_hours=1)

    assert EB.get_state_apply_outbox(t1) is not None
    assert EB.get_state_apply_outbox(t2)["state_status"] == "pending"
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_id=?", (t1,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM processed_events WHERE event_id=?", (t1,)
        ).fetchone()[0] == 1


def test_audit_cleanup_retains_closed_predecessor_and_its_audit_row(
    isolated_stores, monkeypatch
):
    """90d cleanup nie moze osierocic causal chain audit-only T1->T2."""
    events_db, _state_path = isolated_stores
    t1 = "audit-cleanup-parent"
    t2 = "audit-cleanup-child"

    def audit_event(event_id):
        return {
            "event_type": "COURIER_ASSIGNED",
            "event_id": event_id,
            "order_id": "audit-cleanup-order",
            "courier_id": "100",
            "payload": {"source": "audit-cleanup-test"},
        }

    assert EB.emit_audit(
        "COURIER_ASSIGNED",
        order_id="audit-cleanup-order",
        courier_id="100",
        payload={"source": "audit-cleanup-test"},
        event_id=t1,
        state_event=audit_event(t1),
        event_key="audit-cleanup-parent-key",
    ) == t1
    assert EB.mark_state_apply_applied(t1) is True
    assert EB.mark_state_apply_downstream(t1) is True
    assert EB.emit_audit(
        "COURIER_ASSIGNED",
        order_id="audit-cleanup-order",
        courier_id="100",
        payload={"source": "audit-cleanup-test"},
        event_id=t2,
        state_event=audit_event(t2),
        event_key="audit-cleanup-child-key",
        predecessor_event_id=t1,
    ) == t2
    with sqlite3.connect(events_db) as conn:
        conn.execute(
            "UPDATE audit_log SET created_at='2020-01-01T00:00:00+00:00' "
            "WHERE event_id=?",
            (t1,),
        )
        conn.execute(
            "UPDATE state_apply_outbox SET created_at='2020-01-01T00:00:00+00:00' "
            "WHERE event_id=?",
            (t1,),
        )
    monkeypatch.setattr(EB, "_is_peak_window", lambda: False)

    EB.cleanup_audit_log(retention_days=1)

    assert EB.get_state_apply_outbox(t1) is not None
    assert EB.get_state_apply_outbox(t2)["state_status"] == "pending"
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_id=?", (t1,)
        ).fetchone()[0] == 1


def test_parcel_status_inbox_recovers_apply_after_durable_emit(
    isolated_stores, monkeypatch
):
    """Dormant parcel twin nie moze zachowac starego emit→if→apply defektu."""
    events_db, state_path = isolated_stores
    inbox = state_path.parent / PLM.STATUS_INBOX_NAME
    inbox.write_text(
        '{"oid":"c3-order","status_code":5,"cid":100,"ts":111}\n',
        encoding="utf-8",
    )
    real_update = SM.update_from_event
    attempts = 0

    def fail_first(event):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SM.StateReadError("synthetic parcel state failure")
        return real_update(event)

    monkeypatch.setattr(PLM.sm, "update_from_event", fail_first)
    first = PLM._apply_status_inbox()
    monkeypatch.setattr(PLM.sm, "update_from_event", real_update)
    second = PLM._apply_status_inbox()

    assert first == 0
    assert second == 1
    assert _state(state_path)["status"] == "picked_up"
    with sqlite3.connect(events_db) as conn:
        rows = conn.execute(
            """SELECT state_status, downstream_status
               FROM state_apply_outbox"""
        ).fetchall()
    assert rows == [("applied", "applied")]


def test_emit_failure_never_creates_outbox_or_state(isolated_stores, monkeypatch):
    _events_db, state_path = isolated_stores

    def broken_emit(*_args, **_kwargs):
        raise RuntimeError("synthetic event store failure")

    monkeypatch.setattr(PW, "emit", broken_emit)
    before = _state(state_path)
    outcome = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )

    assert outcome.failure_stage == "emit"
    assert _state(state_path) == before


def test_exact_marker_recovery_precedes_oracle_and_preserves_crash_fifo(
    isolated_stores, monkeypatch
):
    """T1 JSON commit bez receipt nie moze zostac ominiety przez downstream T2."""
    _events_db, state_path = isolated_stores
    t1_id = "c3-order_COURIER_PICKED_UP_crash_T1"
    t1_event = {
        "event_type": "COURIER_PICKED_UP",
        "event_id": t1_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T08:01:00+00:00"},
    }
    assert EB.emit(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload=t1_event["payload"],
        event_id=t1_id,
        state_event=t1_event,
        event_key="c3-order_COURIER_PICKED_UP_T1",
        expected_state_version=_state(state_path)["updated_at"],
    ) == t1_id

    # Symulacja crashu dokladnie po trwalym rename orders_state, przed
    # pending→applied w SQLite.
    SM.update_from_event(t1_event)
    assert EB.get_state_apply_outbox(t1_id)["state_status"] == "pending"

    downstream = []
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", downstream.append)
    t2 = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:02:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_T2",
    )

    # Jedno wejscie T2 domyka crash-receipt T1, ale nie moze porzucic nowej
    # one-shot intencji. Oba callbacki zachowuja causal FIFO.
    assert t2.event_id != t1_id
    assert _state(state_path)["status"] == "delivered"
    assert t2.state_ready is True
    assert [event["event_id"] for event in downstream] == [t1_id, t2.event_id]
    assert EB.get_state_apply_outbox(t1_id)["state_status"] == "applied"


def test_one_shot_t2_is_persisted_while_older_t1_remains_pending(
    isolated_stores, monkeypatch
):
    """T2 nie moze zniknac tylko dlatego, ze jedyna proba T1 nadal failuje."""
    _events_db, state_path = isolated_stores
    t1_id = "c3-order_COURIER_PICKED_UP_pending_T1"
    t1_event = {
        "event_type": "COURIER_PICKED_UP",
        "event_id": t1_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T08:01:00+00:00"},
    }
    initial = _state(state_path)
    assert EB.emit(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload=t1_event["payload"],
        event_id=t1_id,
        state_event=t1_event,
        event_key="c3-order_COURIER_PICKED_UP_pending_T1",
        expected_state_version=initial["updated_at"],
        expected_state_marker=initial.get("last_lifecycle_event_id"),
    ) == t1_id

    def reject_t1(event):
        if event["event_id"] == t1_id:
            raise RuntimeError("synthetic persistent T1 failure")
        return SM.update_from_event(event)

    monkeypatch.setattr(PW, "update_from_event", reject_t1)
    t2 = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:02:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_one_shot_T2",
    )

    assert t2.event_created is True
    assert t2.event_id != t1_id
    assert t2.failure_stage == "state_predecessor"
    t2_row = EB.get_state_apply_outbox(t2.event_id)
    assert t2_row["state_status"] == "pending"
    assert t2_row["predecessor_event_id"] == t1_id
    assert EB.get_state_apply_outbox(t1_id)["state_status"] == "pending"
    assert _state(state_path)["status"] == "assigned"

    downstream = []
    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=downstream.append,
    )

    assert counts["state_ready"] == 2
    assert counts["downstream"] == 2
    assert _state(state_path)["status"] == "delivered"
    assert [event["event_id"] for event in downstream] == [t1_id, t2.event_id]
    assert EB.get_state_apply_outbox(t1_id)["state_status"] == "applied"
    assert EB.get_state_apply_outbox(t2.event_id)["state_status"] == "applied"


def test_three_one_shot_intents_form_causal_predecessor_chain(
    isolated_stores, monkeypatch
):
    """T3 zalezy od T2, nie rownolegle od wspolnego, starego T1."""
    _events_db, state_path = isolated_stores
    t1_id = "c3-order_COURIER_ASSIGNED_200_chain_T1"
    t1_event = {
        "event_type": "COURIER_ASSIGNED",
        "event_id": t1_id,
        "order_id": "c3-order",
        "courier_id": "200",
        "payload": {"source": "chain-test"},
    }
    initial = _state(state_path)
    assert EB.emit_audit(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload=t1_event["payload"],
        event_id=t1_id,
        state_event=t1_event,
        event_key="c3-order_COURIER_ASSIGNED_200_chain_T1",
        expected_state_version=initial["updated_at"],
        expected_state_marker=initial.get("last_lifecycle_event_id"),
    ) == t1_id

    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("hold whole chain")),
    )
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", lambda _event: None)
    t2 = PW._emit_and_apply_state(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="200",
        payload={"timestamp": "2026-07-19T08:01:00+00:00"},
        event_id="c3-order_COURIER_PICKED_UP_chain_T2",
    )
    t3 = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="200",
        payload={"timestamp": "2026-07-19T08:02:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_chain_T3",
    )

    assert EB.get_state_apply_outbox(t2.event_id)["predecessor_event_id"] == t1_id
    assert EB.get_state_apply_outbox(t3.event_id)["predecessor_event_id"] == t2.event_id

    downstream = []
    totals = {"state_ready": 0, "downstream": 0}
    for _ in range(3):
        counts = DEA.drain_pending(
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=SM.get_order_strict,
            downstream_fn=downstream.append,
        )
        totals["state_ready"] += counts["state_ready"]
        totals["downstream"] += counts["downstream"]

    assert totals == {"state_ready": 3, "downstream": 3}
    assert _state(state_path)["status"] == "delivered"
    assert _state(state_path)["courier_id"] == "200"
    assert [event["event_id"] for event in downstream] == [
        t1_id,
        t2.event_id,
        t3.event_id,
    ]


def test_same_timestamp_matching_effect_with_other_marker_is_superseded(
    isolated_stores, monkeypatch
):
    """Rowny updated_at nie moze ukryc konfliktu markera nowszego T2."""
    _events_db, state_path = isolated_stores
    real_apply = SM.update_from_event
    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("before state commit")),
    )
    first = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL", **_returned_kwargs()
    )
    row = EB.get_state_apply_outbox(first.event_id)
    assert row["state_status"] == "pending"

    # Kontrolowany fixture: T2 osiagnal ten sam efekt i ten sam timestamp, ale
    # jego durable marker jest inny. Sam zegar nie moze zamknac receiptu T1.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current = state["c3-order"]
    current.update(
        {
            "status": "returned_to_pool",
            "commitment_level": "returned_to_pool",
            "courier_id": None,
            "updated_at": row["expected_state_version"],
            "last_lifecycle_event_id": "other-return-T2",
            "last_lifecycle_event_id_order_returned_to_pool": "other-return-T2",
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")

    downstream = []
    monkeypatch.setattr(PW, "update_from_event", real_apply)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", downstream.append)
    retry = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL", **_returned_kwargs()
    )

    assert retry.superseded is True
    assert retry.state_ready is False
    assert downstream == []
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "superseded"
    assert _state(state_path)["last_lifecycle_event_id"] == "other-return-T2"


def test_retry_applies_after_orthogonal_direct_writer_version_change(
    isolated_stores, monkeypatch
):
    """updated_at z touch nie jest semantycznym superseded terminala."""
    real_apply = SM.update_from_event
    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("before state commit")),
    )
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:03:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_orthogonal_retry",
    )
    assert first.failure_stage == "state_apply"
    expected = EB.get_state_apply_outbox(first.event_id)["expected_state_version"]

    SM.upsert_order(
        "c3-order", {"waiting_at": "2026-07-19T08:02:30+00:00"}, event="WAITING_AT"
    )
    assert _state(isolated_stores[1])["updated_at"] != expected
    monkeypatch.setattr(PW, "update_from_event", real_apply)
    retry = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:03:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_orthogonal_retry",
    )

    assert retry.state_ready is True
    assert retry.superseded is False
    assert _state(isolated_stores[1])["status"] == "delivered"


def test_retry_does_not_overwrite_newer_lifecycle_marker(
    isolated_stores, monkeypatch
):
    """Znany V0: marker T2 odroznia konflikt od ortogonalnego waiting_at."""
    real_apply = SM.update_from_event
    monkeypatch.setattr(
        PW,
        "update_from_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("before state commit")),
    )
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:03:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_stale_T1",
    )
    assert first.failure_stage == "state_apply"

    SM.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "event_id": "c3-order_COURIER_ASSIGNED_newer_T2",
        "order_id": "c3-order",
        "courier_id": "200",
        "payload": {"source": "newer-direct-writer"},
    })
    monkeypatch.setattr(PW, "update_from_event", real_apply)
    retry = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:03:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_stale_T1",
    )

    assert retry.superseded is True
    assert retry.state_ready is False
    assert _state(isolated_stores[1])["status"] == "assigned"
    assert _state(isolated_stores[1])["courier_id"] == "200"


def test_durable_runtime_wiring_uses_fail_closed_state_reader(monkeypatch):
    """Fallback read-only {} nie moze stac sie wersja oczekiwana outboxa."""
    assert PW.state_get_order_strict is SM.get_order_strict
    monkeypatch.setattr(SM, "_read_state", lambda: {})

    def broken_strict():
        raise SM.StateReadError("synthetic unreadable state")

    monkeypatch.setattr(SM, "_read_state_strict", broken_strict)
    assert SM.get_order("missing") is None
    with pytest.raises(SM.StateReadError, match="synthetic unreadable"):
        SM.get_order_strict("missing")


def test_applied_downstream_backlog_cannot_starve_pending_state(
    isolated_stores,
):
    """101 applied rows nie wypelniaja LIMIT lane'u zapisu state."""
    for idx in range(101):
        oid = f"applied-backlog-{idx:03d}"
        event_id = f"{oid}_NEW_ORDER_v1"
        event = {
            "event_type": "NEW_ORDER",
            "event_id": event_id,
            "order_id": oid,
            "courier_id": None,
            "payload": {},
        }
        assert EB.emit(
            "NEW_ORDER",
            order_id=oid,
            event_id=event_id,
            state_event=event,
            event_key=f"{oid}_NEW_ORDER_first",
            expected_state_version=None,
        ) == event_id
        assert EB.mark_state_apply_applied(event_id) is True

    target_id = "state-target_NEW_ORDER_v1"
    target_event = {
        "event_type": "NEW_ORDER",
        "event_id": target_id,
        "order_id": "state-target",
        "courier_id": None,
        "payload": {},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="state-target",
        event_id=target_id,
        state_event=target_event,
        event_key="state-target_NEW_ORDER_first",
        expected_state_version=None,
    ) == target_id

    def poison_downstream(_event):
        raise RuntimeError("poison oldest downstream")

    counts = DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=poison_downstream,
        limit=100,
    )

    assert counts["seen"] == 1
    assert counts["state_ready"] == 1
    assert EB.get_state_apply_outbox(target_id)["state_status"] == "applied"
    assert SM.get_order("state-target")["status"] == "planned"


def test_durable_queue_outbox_and_audit_mirror_rollback_as_one_unit(
    isolated_stores, monkeypatch
):
    events_db, state_path = isolated_stores
    before = _state(state_path)

    def reject_atomic_mirror(*_args, **_kwargs):
        raise RuntimeError("synthetic atomic mirror failure")

    monkeypatch.setattr(EB, "_insert_audit_mirror_tx", reject_atomic_mirror)
    outcome = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:04:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_atomic_mirror",
    )

    with sqlite3.connect(events_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM state_apply_outbox").fetchone()[0] == 0
    assert outcome.failure_stage == "emit"
    assert _state(state_path) == before


def test_durable_mirrored_event_has_no_postcommit_best_effort_gap(
    isolated_stores, monkeypatch
):
    events_db, _state_path = isolated_stores

    def forbidden_legacy_mirror(*_args, **_kwargs):
        raise AssertionError("durable path must not use post-commit mirror")

    monkeypatch.setattr(EB, "_emit_audit_mirror", forbidden_legacy_mirror)
    outcome = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:05:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_atomic_present",
    )

    assert outcome.state_ready is True
    with sqlite3.connect(events_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM state_apply_outbox").fetchone()[0] == 1


def test_orders_state_rename_is_followed_by_parent_directory_fsync(
    isolated_stores, monkeypatch
):
    real_fsync = SM.os.fsync
    fsync_kinds = []

    def spy_fsync(fd):
        fsync_kinds.append("dir" if stat.S_ISDIR(SM.os.fstat(fd).st_mode) else "file")
        return real_fsync(fd)

    monkeypatch.setattr(SM.os, "fsync", spy_fsync)
    SM.upsert_order("c3-order", {"waiting_at": "2026-07-19T08:06:00+00:00"})

    assert "file" in fsync_kinds
    assert fsync_kinds[-1] == "dir"


def test_failed_directory_fsync_cannot_close_receipt_until_retry(
    isolated_stores, monkeypatch
):
    real_ensure = SM.ensure_state_directory_durable

    def fail_directory_fsync(*_args):
        raise OSError("synthetic directory fsync failure")

    monkeypatch.setattr(SM, "ensure_state_directory_durable", fail_directory_fsync)
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:07:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_dir_fsync",
    )

    assert first.failure_stage == "state_durability"
    assert first.state_ready is False
    assert _state(isolated_stores[1])["status"] == "delivered"
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "pending"

    monkeypatch.setattr(SM, "ensure_state_directory_durable", real_ensure)
    retry = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:07:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_dir_fsync",
    )
    assert retry.state_ready is True
    assert EB.get_state_apply_outbox(first.event_id)["state_status"] == "applied"


def test_unknown_version_recovery_uses_content_not_nonmonotonic_clock(
    isolated_stores, monkeypatch
):
    reads = 0

    def fail_pre_read_then_succeed(oid):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise SM.StateReadError("synthetic pre-read failure")
        return SM.get_order(oid)

    monkeypatch.setattr(
        EB, "now_iso", lambda: "2099-01-01T00:00:00+00:00"
    )
    outcome = DEA.emit_and_apply(
        "ORDER_RETURNED_TO_POOL",
        order_id="c3-order",
        courier_id="100",
        payload={"reason": "cancelled"},
        state_payload=None,
        event_key="c3-order_ORDER_RETURNED_nonmonotonic_clock",
        emit_fn=EB.emit_audit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=fail_pre_read_then_succeed,
        downstream_fn=lambda _event: None,
    )

    assert outcome.state_ready is True
    assert outcome.superseded is False
    assert _state(isolated_stores[1])["status"] == "returned_to_pool"


@pytest.mark.parametrize(
    ("event_type", "audit", "event_key"),
    [
        ("COURIER_DELIVERED", False, "c3-order_COURIER_DELIVERED_canonical"),
        ("ORDER_RETURNED_TO_POOL", True, "c3-order_ORDER_RETURNED_atomic"),
    ],
)
def test_outbox_insert_failure_rolls_back_source_event(
    isolated_stores, monkeypatch, event_type, audit, event_key
):
    """Queue i audit source nie moga przezyc bez odpowiadajacego outboxu."""
    events_db, state_path = isolated_stores
    before = _state(state_path)

    def reject_outbox(*_args, **_kwargs):
        raise RuntimeError("synthetic outbox insert failure")

    monkeypatch.setattr(EB, "_insert_state_apply_outbox", reject_outbox)
    outcome = PW._emit_and_apply_state(
        event_type,
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "T", "reason": "cancelled"},
        event_id=event_key,
        audit=audit,
    )

    source_table = "audit_log" if audit else "events"
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {source_table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM state_apply_outbox").fetchone()[0] == 0
    assert outcome.failure_stage == "emit"
    assert _state(state_path) == before


@pytest.mark.parametrize(
    ("event", "current", "expected"),
    [
        ({"event_type": "NEW_ORDER", "order_id": "x"}, {"status": "planned"}, "applied"),
        (
            {"event_type": "COURIER_ASSIGNED", "order_id": "x", "courier_id": "100"},
            {"status": "assigned", "courier_id": "100"},
            "applied",
        ),
        (
            {"event_type": "COURIER_ASSIGNED", "order_id": "x", "courier_id": "100"},
            {"status": "picked_up", "courier_id": "200"},
            "superseded",
        ),
        (
            {"event_type": "COURIER_DELIVERED", "order_id": "x"},
            {"status": "returned_to_pool"},
            "superseded",
        ),
        (
            {"event_type": "ORDER_RETURNED_TO_POOL", "order_id": "x"},
            {"status": "delivered"},
            "superseded",
        ),
        (
            {"event_type": "COURIER_PICKED_UP", "order_id": "x"},
            {"status": "assigned"},
            "pending",
        ),
        (
            {
                "event_type": "CZAS_KURIERA_UPDATED",
                "order_id": "x",
                "payload": {
                    "new_ck_iso": "2026-07-19T10:30:00+02:00",
                    "new_ck_hhmm": "10:30",
                },
            },
            {
                "status": "assigned",
                "czas_kuriera_warsaw": "2026-07-19T10:30:00+02:00",
                "czas_kuriera_hhmm": "10:30",
            },
            "applied",
        ),
        (
            {
                "event_type": "CZAS_KURIERA_UPDATED",
                "order_id": "x",
                "payload": {
                    "new_ck_iso": "2026-07-19T10:30:00+02:00",
                    "new_ck_hhmm": "10:31",
                },
            },
            {"status": "assigned"},
            "superseded",
        ),
        (
            {
                "event_type": "PICKUP_TIME_UPDATED",
                "order_id": "x",
                "payload": {"new_pickup_at_warsaw": "not-an-iso"},
            },
            {"status": "assigned"},
            "superseded",
        ),
    ],
)
def test_state_postcondition_is_three_way(event, current, expected):
    assert SM.event_effect_status(event, current=current) == expected
    assert SM.event_effect_is_applied(event, current=current) is (expected == "applied")


def test_all_panel_lifecycle_writers_use_one_durable_chokepoint():
    panel_source = Path(PW.__file__).read_text(encoding="utf-8")
    durable_source = Path(DEA.__file__).read_text(encoding="utf-8")

    assert panel_source.count("_emit_and_apply_state(") >= 16
    assert "emit_fn(" in durable_source
    assert durable_source.count("state_update_fn(state_event)") == 1
    assert "get_unresolved_state_apply" in durable_source
    assert "drain_pending(" in panel_source


def test_distinct_successor_survives_pending_same_key_supersede(
    isolated_stores,
):
    """T1 superseded during retry must not consume the one-shot T2 call."""
    events_db, state_path = isolated_stores
    initial = _state(state_path)
    t1_id = "c3-order_COURIER_DELIVERED_pending_T1"
    t1 = {
        "event_type": "COURIER_DELIVERED",
        "event_id": t1_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T08:05:00+00:00"},
    }
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=t1["payload"],
        event_id=t1_id,
        state_event=t1,
        event_key="c3-order_COURIER_DELIVERED_canonical",
        expected_state_version=initial["updated_at"],
        expected_state_marker=initial.get("last_lifecycle_event_id"),
    ) == t1_id
    SM.update_from_event(t1)  # crash before pending -> applied receipt
    _apply_resurrection("assigned")

    second = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:10:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )

    assert second.event_created is True
    assert second.event_id != t1_id
    assert second.state_ready is True
    assert _state(state_path)["delivered_at"] == "2026-07-19T08:10:00+00:00"
    with sqlite3.connect(events_db) as conn:
        rows = conn.execute(
            """SELECT event_id, state_status, downstream_status
               FROM state_apply_outbox ORDER BY rowid"""
        ).fetchall()
    assert rows == [
        (t1_id, "superseded", "skipped"),
        (second.event_id, "applied", "applied"),
    ]


def test_obsolete_delivery_callback_is_skipped_after_resurrection_and_t2(
    isolated_stores, monkeypatch
):
    """T1 callback cannot run merely because T2 made status delivered again."""
    _events_db, _state_path = isolated_stores

    def fail_downstream(_event):
        raise RuntimeError("hold T1 callback")

    monkeypatch.setattr(PW.lifecycle_downstream, "apply", fail_downstream)
    first = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:05:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )
    assert first.failure_stage == "downstream"
    _apply_resurrection("assigned")

    calls = []
    monkeypatch.setattr(
        PW, "_advance_plan_on_deliver", lambda *args, **kwargs: calls.append(args)
    )
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY)
    second = PW._emit_and_apply_state(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:10:00+00:00"},
        event_id="c3-order_COURIER_DELIVERED_canonical",
    )

    assert second.state_ready is True
    assert [args[2] for args in calls] == ["2026-07-19T08:10:00+00:00"]


def test_advance_plan_retry_does_not_overwrite_newer_anchor(tmp_path, monkeypatch):
    plans_path = tmp_path / "courier_plans.json"
    monkeypatch.setattr(PM, "PLANS_FILE", plans_path)
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    base = {
        "start_pos": {"lat": 53.0, "lng": 23.0, "source": "base"},
        "start_ts": "2026-07-19T08:00:00+00:00",
        "stops": [
            {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.1, "lng": 23.1}},
            {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.2, "lng": 23.2}},
        ],
        "optimization_method": "incremental",
    }
    PM.save_plan("100", base)
    PM.advance_plan(
        "100", "A", "2026-07-19T08:05:00+00:00", (53.1, 23.1)
    )
    newer = {
        **base,
        "start_pos": {"lat": 60.0, "lng": 30.0, "source": "newer"},
        "start_ts": "2026-07-19T09:00:00+00:00",
        "stops": [base["stops"][1]],
    }
    saved = PM.save_plan("100", newer)

    PM.advance_plan(
        "100", "A", "2026-07-19T08:05:00+00:00", (53.1, 23.1)
    )

    after = PM.load_plan("100")
    assert after["start_pos"] == saved["start_pos"]
    assert after["start_ts"] == saved["start_ts"]
    assert after["plan_version"] == saved["plan_version"]


@pytest.mark.parametrize(
    ("event_type", "audit"),
    [("COURIER_PICKED_UP", False), ("COURIER_ASSIGNED", True)],
)
def test_emit_rejects_malformed_state_event_before_source_commit(
    isolated_stores, event_type, audit
):
    events_db, _state_path = isolated_stores
    EB._ensure_state_apply_outbox_initialized()
    emitter = EB.emit_audit if audit else EB.emit
    with pytest.raises(ValueError, match="state_event"):
        emitter(
            event_type,
            order_id="c3-order",
            courier_id="100",
            payload={},
            event_id=f"bad-{event_type}",
            state_event={},
            event_key=f"bad-{event_type}",
        )
    with sqlite3.connect(events_db) as conn:
        source_table = "audit_log" if audit else "events"
        assert conn.execute(f"SELECT COUNT(*) FROM {source_table}").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM state_apply_outbox"
        ).fetchone()[0] == 0


def test_legacy_malformed_outbox_row_is_terminalized_without_poisoning_fifo(
    isolated_stores,
):
    events_db, _state_path = isolated_stores
    EB._ensure_state_apply_outbox_initialized()
    with sqlite3.connect(events_db) as conn:
        conn.execute(
            """INSERT INTO events
               (event_id, event_type, order_id, courier_id, payload, created_at, status)
               VALUES ('legacy-poison', 'COURIER_PICKED_UP', 'c3-order', '100',
                       '{}', '2026-07-19T07:59:00+00:00', 'pending')"""
        )
        conn.execute(
            """INSERT INTO state_apply_outbox
               (event_id, event_key, order_id, state_event,
                state_status, downstream_status, downstream_attempts,
                state_attempts, created_at, updated_at)
               VALUES ('legacy-poison', 'legacy-poison', 'c3-order', '{}',
                       'pending', 'pending', 0, 0,
                       '2026-07-19T07:59:00+00:00',
                       '2026-07-19T07:59:00+00:00')"""
        )

    valid = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL",
        **_returned_kwargs(),
    )

    assert valid.state_ready is True
    assert valid.downstream_executed is True
    poison = EB.get_state_apply_outbox("legacy-poison")
    assert poison["state_status"] == "superseded"
    assert poison["downstream_status"] == "skipped"
    assert "missing event_type" in poison["last_error"]
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT status FROM events WHERE event_id='legacy-poison'"
        ).fetchone()[0] == "processed"


def test_projected_learning_effect_does_not_depend_on_rotation_history(
    isolated_stores, monkeypatch, tmp_path
):
    pending_path = tmp_path / "pending.json"
    learning_path = tmp_path / "learning.jsonl"
    pending_path.write_text(
        json.dumps(
            {"c3-order": {"decision_record": {"best": {"courier_id": "200"}}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(pending_path))
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning_path))
    monkeypatch.setattr(PW, "_panel_agree_enabled", lambda: True)
    monkeypatch.setattr(PW, "_remove_stops_on_return", lambda *a, **k: None)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY)
    attempts = 0

    def fail_plan_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("later composite effect failed")

    monkeypatch.setattr(PW, "_save_plan_on_assign_signal", fail_plan_once)
    kwargs = dict(
        order_id="c3-order",
        courier_id="200",
        payload={"source": "panel_reassign"},
        event_id="c3-order_COURIER_ASSIGNED_200_projection",
        audit=True,
    )
    first = PW._emit_and_apply_state("COURIER_ASSIGNED", **kwargs)
    assert first.failure_stage == "downstream"
    assert learning_path.exists()
    learning_path.unlink()  # stronger than finite rotation history: no line remains

    second = PW._emit_and_apply_state("COURIER_ASSIGNED", **kwargs)

    assert second.state_ready is True
    assert second.downstream_executed is True
    assert not learning_path.exists()
    with sqlite3.connect(isolated_stores[0]) as conn:
        row = conn.execute(
            """SELECT projected_at FROM durable_learning_projection
               WHERE lifecycle_event_id = ?""",
            (first.event_id,),
        ).fetchone()
    assert row is not None and row[0] is not None


def test_learning_projection_first_classification_wins_across_retry(
    isolated_stores, monkeypatch, tmp_path
):
    """Changed proposal files cannot flip AGREE to OVERRIDE after a crash."""
    learning_path = tmp_path / "learning.jsonl"
    lifecycle_event_id = "assignment-learning-first-wins"
    first = {
        "action": "PANEL_AGREE",
        "order_id": "c3-order",
        "lifecycle_event_id": lifecycle_event_id,
    }
    second = {
        "action": "PANEL_OVERRIDE",
        "order_id": "c3-order",
        "lifecycle_event_id": lifecycle_event_id,
    }
    EB.prepare_durable_learning_projection(
        lifecycle_event_id, "panel_assignment_learning", first
    )
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning_path))
    monkeypatch.setattr(PW, "_durable_downstream_attempt", lambda *a, **k: 2)

    PW._append_learning_record(
        second, lifecycle_event_id, _raise_on_error=True
    )

    rows = [
        json.loads(line)
        for line in learning_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [first]
    with sqlite3.connect(isolated_stores[0]) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM durable_learning_projection "
            "WHERE lifecycle_event_id = ?",
            (lifecycle_event_id,),
        ).fetchone()[0] == 1


def test_delayed_assignment_callback_skips_old_courier_and_prunes_previous(
    monkeypatch,
):
    current = {
        "order_id": "A",
        "status": "assigned",
        "courier_id": "200",
        "last_lifecycle_event_id_courier_assigned": "assign-T2",
    }
    monkeypatch.setattr(LD.state_machine, "get_order_strict", lambda _oid: current)
    removed = []
    saved = []
    monkeypatch.setattr(
        PW,
        "_release_plan_on_reassign",
        lambda cid, oid, **k: removed.append((cid, oid)) or True,
    )
    monkeypatch.setattr(PW, "_save_plan_on_assign_signal", lambda oid, cid, **k: saved.append((oid, cid)))
    monkeypatch.setattr(PW, "_check_panel_agree", lambda *a, **k: None)
    monkeypatch.setattr(PW, "_check_panel_override", lambda *a, **k: None)

    LD.apply(
        {"event_type": "COURIER_ASSIGNED", "event_id": "assign-T1", "order_id": "A", "courier_id": "100", "payload": {}}
    )
    LD.apply(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "assign-T2",
            "order_id": "A",
            "courier_id": "200",
            "previous_courier_id": "100",
            "payload": {},
        }
    )

    assert removed == [("100", "A")]
    assert saved == [("A", "200")]


def test_return_callback_recovers_prior_courier_after_state_clears_it(
    isolated_stores, monkeypatch
):
    _events_db, state_path = isolated_stores
    removed = []
    monkeypatch.setattr(PW, "_remove_stops_on_return", lambda cid, oid, **k: removed.append((cid, oid)))
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY)

    outcome = PW._emit_and_apply_state(
        "ORDER_RETURNED_TO_POOL",
        order_id="c3-order",
        courier_id="",
        payload={"reason": "cancelled"},
        event_id="return-without-panel-cid",
        audit=True,
    )

    current = _state(state_path)
    assert current["courier_id"] is None
    assert "previous_courier_id" not in current
    receipt = EB.get_state_apply_outbox(outcome.event_id)
    assert receipt["state_event"]["previous_courier_id"] == "100"
    assert removed == [("100", "c3-order")]


def test_assignment_chain_prunes_every_event_local_previous_courier(
    isolated_stores, monkeypatch
):
    """A plan cannot survive merely because state reached C before B callback."""
    _events_db, _state_path = isolated_stores

    def hold_b(_event):
        raise RuntimeError("hold B callback")

    monkeypatch.setattr(PW.lifecycle_downstream, "apply", hold_b)
    to_b = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload={"source": "test"},
        event_id="assign-B",
        audit=True,
    )
    assert to_b.failure_stage == "downstream"
    assert EB.get_state_apply_outbox(to_b.event_id)["state_event"][
        "previous_courier_id"
    ] == "100"

    removed = []
    saved = []
    monkeypatch.setattr(
        PW,
        "_release_plan_on_reassign",
        lambda cid, oid, **_kwargs: removed.append((cid, oid)) or True,
    )
    monkeypatch.setattr(
        PW,
        "_save_plan_on_assign_signal",
        lambda oid, cid, **_kwargs: saved.append((oid, cid)),
    )
    monkeypatch.setattr(PW, "_check_panel_agree", lambda *a, **k: None)
    monkeypatch.setattr(PW, "_check_panel_override", lambda *a, **k: None)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY)

    to_c = PW._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="300",
        payload={"source": "test"},
        event_id="assign-C",
        audit=True,
    )

    assert to_c.downstream_executed is True
    assert removed == [("100", "c3-order"), ("200", "c3-order")]
    assert saved == [("c3-order", "300")]


def test_delayed_return_prunes_old_not_new_courier(monkeypatch):
    """Return T1/A followed by assign T2/B must never remove B's new plan."""
    removed = []
    current = {
        "order_id": "c3-order",
        "status": "assigned",
        "courier_id": "200",
        "last_lifecycle_event_id_courier_assigned": "assign-T2",
    }
    monkeypatch.setattr(LD.state_machine, "get_order_strict", lambda _oid: current)
    monkeypatch.setattr(
        PW,
        "_remove_stops_on_return",
        lambda cid, oid, **_kwargs: removed.append((cid, oid)),
    )
    LD.apply(
        {
            "event_type": "ORDER_RETURNED_TO_POOL",
            "event_id": "return-T1",
            "order_id": "c3-order",
            "courier_id": "",
            "previous_courier_id": "100",
            "payload": {"reason": "cancelled"},
        }
    )

    assert removed == [("100", "c3-order")]


def test_stale_pickup_for_different_courier_is_superseded_not_queued(
    isolated_stores,
):
    """Status alone cannot acknowledge a parcel callback for another courier."""
    SM.upsert_order(
        "c3-order",
        {
            "status": "picked_up",
            "commitment_level": "picked_up",
            "courier_id": "200",
            "last_lifecycle_event_id": "pickup-by-200",
            "last_lifecycle_event_id_courier_picked_up": "pickup-by-200",
        },
        event="TEST_PICKUP_BY_200",
    )

    outcome = DEA.emit_and_apply(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "parcel_status_inbox"},
        state_payload=None,
        event_key="c3-order_COURIER_PICKED_UP_stale-courier-100",
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=_REAL_LIFECYCLE_DOWNSTREAM_APPLY,
    )

    assert outcome.superseded is True
    assert outcome.state_ready is False
    assert SM.get_order_strict("c3-order")["courier_id"] == "200"
    assert EB.get_pending(event_types=["COURIER_PICKED_UP"]) == []


def test_stale_parcel_pickup_generation_for_same_courier_is_superseded(
    isolated_stores,
):
    SM.upsert_order(
        "c3-order",
        {
            "status": "picked_up",
            "commitment_level": "picked_up",
            "courier_id": "100",
            "last_lifecycle_event_id": "older-parcel-pickup",
            "last_lifecycle_event_id_courier_picked_up": "older-parcel-pickup",
        },
        event="TEST_OLDER_PARCEL_PICKUP",
    )

    outcome = DEA.emit_and_apply(
        "COURIER_PICKED_UP",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "parcel_status_inbox"},
        state_payload=None,
        event_key="c3-order_COURIER_PICKED_UP_new-unseen-generation",
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=_REAL_LIFECYCLE_DOWNSTREAM_APPLY,
    )

    assert outcome.superseded is True
    assert EB.get_pending(event_types=["COURIER_PICKED_UP"]) == []
    assert SM.get_order_strict("c3-order")[
        "last_lifecycle_event_id_courier_picked_up"
    ] == "older-parcel-pickup"


def test_parcel_delivery_for_different_assigned_courier_is_superseded():
    assert SM.event_effect_status(
        {
            "event_type": "COURIER_DELIVERED",
            "event_id": "parcel-delivery-courier-100",
            "order_id": "parcel-order",
            "courier_id": "100",
            "payload": {"source": "parcel_status_inbox"},
        },
        current={
            "order_id": "parcel-order",
            "status": "assigned",
            "courier_id": "200",
        },
    ) == "superseded"


def test_changed_token_after_unknown_successor_never_overwrites_newer_marker(
    isolated_stores,
):
    """C written after an unknown-baseline A receipt cannot be mistaken for B."""
    key_a = "c3-order_COURIER_ASSIGNED_100_unknown-cycle"

    def assign(cid, key, get_order_fn, downstream):
        return DEA.emit_and_apply(
            "COURIER_ASSIGNED",
            order_id="c3-order",
            courier_id=cid,
            payload={"source": "unknown-cycle-test"},
            state_payload=None,
            event_key=key,
            emit_fn=EB.emit_audit,
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=get_order_fn,
            downstream_fn=downstream,
        )

    hold = lambda _event: (_ for _ in ()).throw(RuntimeError("hold callback"))
    old_a = assign("100", key_a, SM.get_order_strict, hold)
    assert old_a.state_ready is True
    to_b = assign(
        "200",
        "c3-order_COURIER_ASSIGNED_200_unknown-cycle",
        SM.get_order_strict,
        hold,
    )
    assert to_b.state_ready is True
    reads = 0

    def fail_three_reads_then_recover(oid):
        nonlocal reads
        reads += 1
        if reads <= 3:
            raise SM.StateReadError("synthetic unknown-baseline window")
        return SM.get_order_strict(oid)

    pending_a = assign("100", key_a, fail_three_reads_then_recover, hold)
    assert pending_a.state_ready is False
    assert EB.get_state_apply_outbox(pending_a.event_id)["state_status"] == "pending"

    SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "event_id": "assign-C-after-pending-A",
            "order_id": "c3-order",
            "courier_id": "300",
            "payload": {"source": "direct-newer-writer"},
        }
    )
    retried = assign("100", key_a, SM.get_order_strict, hold)

    assert retried.state_ready is False
    assert retried.failure_stage == "state_version"
    assert SM.get_order_strict("c3-order")["courier_id"] == "300"
    assert EB.get_state_apply_outbox(pending_a.event_id)["state_status"] == "pending"


def test_unknown_token_conflict_does_not_block_unrelated_downstream(
    isolated_stores,
):
    """A retryable state conflict and an unrelated ready callback use separate lanes."""
    expected_token = SM.state_storage_token()
    blocked_id = "c3-order_ORDER_RETURNED_unknown-token"
    blocked_event = {
        "event_type": "ORDER_RETURNED_TO_POOL",
        "event_id": blocked_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"reason": "cancelled"},
    }
    assert EB.emit_audit(
        "ORDER_RETURNED_TO_POOL",
        order_id="c3-order",
        courier_id="100",
        payload=blocked_event["payload"],
        event_id=blocked_id,
        state_event=blocked_event,
        event_key=blocked_id,
        expected_state_version="__STATE_READ_UNAVAILABLE__",
        expected_state_marker="__STATE_MARKER_UNAVAILABLE__",
        expected_state_token=expected_token,
    ) == blocked_id
    SM.upsert_order(
        "unrelated-storage-change",
        {"order_id": "unrelated-storage-change", "status": "planned"},
        event="UNRELATED_RMW",
    )

    ready_id = "ready-unrelated_NEW_ORDER"
    ready_event = {
        "event_type": "NEW_ORDER",
        "event_id": ready_id,
        "order_id": "ready-unrelated",
        "courier_id": None,
        "payload": {},
    }
    assert EB.emit(
        "NEW_ORDER",
        order_id="ready-unrelated",
        event_id=ready_id,
        state_event=ready_event,
        event_key=ready_id,
    ) == ready_id
    assert EB.mark_state_apply_applied(ready_id) is True
    callbacks = []

    DEA.drain_pending(
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=lambda event: callbacks.append(event["event_id"]),
    )

    assert EB.get_state_apply_outbox(blocked_id)["state_status"] == "pending"
    assert EB.get_state_apply_outbox(ready_id)["downstream_status"] == "applied"
    assert callbacks == [ready_id]


def test_permanent_downstream_failure_does_not_starve_unrelated_receipt(
    isolated_stores,
):
    ids = ("callback-A", "callback-B")
    for event_id in ids:
        event = {
            "event_type": "NEW_ORDER",
            "event_id": event_id,
            "order_id": event_id,
            "courier_id": None,
            "payload": {},
        }
        assert EB.emit(
            "NEW_ORDER",
            order_id=event_id,
            event_id=event_id,
            state_event=event,
            event_key=event_id,
        ) == event_id
        assert EB.mark_state_apply_applied(event_id) is True

    callbacks = []

    def callback(event):
        callbacks.append(event["event_id"])
        if event["event_id"] == "callback-A":
            raise RuntimeError("permanent callback A failure")

    for _ in range(2):
        DEA.drain_pending(
            state_update_fn=SM.update_from_event,
            effect_status_fn=SM.event_effect_status,
            get_order_fn=SM.get_order_strict,
            downstream_fn=callback,
        )

    assert callbacks[:2] == ["callback-A", "callback-B"]
    assert EB.get_state_apply_outbox("callback-A")["downstream_status"] == "pending"
    assert EB.get_state_apply_outbox("callback-B")["downstream_status"] == "applied"


def test_durable_resurrection_repairs_plan_when_delivery_callback_wins_race(
    isolated_stores, monkeypatch
):
    """Correction waits for a running delivery and then durably repairs its plan."""
    _events_db, state_path = isolated_stores

    def hold_delivery(_event):
        raise RuntimeError("hold delivered callback before race")

    delivered = DEA.emit_and_apply(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"timestamp": "2026-07-19T08:05:00+00:00"},
        state_payload=None,
        event_key="c3-order_COURIER_DELIVERED_resurrection-race",
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=hold_delivery,
    )
    assert delivered.state_ready is True
    assert delivered.failure_stage == "downstream"

    snapshot_taken = threading.Event()
    release_delivery = threading.Event()
    correction_done = threading.Event()
    original_get = SM.get_order_strict

    def pause_after_delivered_snapshot(oid):
        current = original_get(oid)
        if (
            threading.current_thread().name == "delivery-drain"
            and current
            and current.get("status") == "delivered"
        ):
            snapshot_taken.set()
            assert release_delivery.wait(timeout=5)
        return current

    effects = []
    monkeypatch.setattr(LD.state_machine, "get_order_strict", pause_after_delivered_snapshot)
    monkeypatch.setattr(
        PW,
        "_advance_plan_on_deliver",
        lambda *_args, **_kwargs: effects.append("delivery-pruned"),
    )
    monkeypatch.setattr(
        PW,
        "_invalidate_plan_on_bag_change",
        lambda *_args, **_kwargs: effects.append("resurrection-repaired"),
    )
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", _REAL_LIFECYCLE_DOWNSTREAM_APPLY)
    drain_result = {}
    correction_result = {}

    def drain_delivery():
        drain_result.update(
            DEA.drain_pending(
                state_update_fn=SM.update_from_event,
                effect_status_fn=SM.event_effect_status,
                get_order_fn=original_get,
                downstream_fn=_REAL_LIFECYCLE_DOWNSTREAM_APPLY,
            )
        )

    def apply_correction():
        correction_result["outcome"] = PW._emit_and_apply_state(
            "ORDER_RESURRECTED",
            order_id="c3-order",
            courier_id="100",
            payload={
                "new_status": "assigned",
                "reason": "panel_status_restored",
                "source": "panel_status_restored",
            },
            event_id="c3-order_ORDER_RESURRECTED_assigned_canonical",
            audit=True,
        )
        correction_done.set()

    delivery_thread = threading.Thread(
        target=drain_delivery, name="delivery-drain"
    )
    delivery_thread.start()
    assert snapshot_taken.wait(timeout=5)
    correction_thread = threading.Thread(target=apply_correction)
    correction_thread.start()
    assert correction_done.wait(timeout=0.2) is False
    release_delivery.set()
    delivery_thread.join(timeout=5)
    correction_thread.join(timeout=5)

    assert not delivery_thread.is_alive()
    assert not correction_thread.is_alive()
    correction = correction_result["outcome"]
    assert correction.state_ready is True
    assert correction.downstream_executed is True
    assert _state(state_path)["status"] == "assigned"
    assert effects == ["delivery-pruned", "resurrection-repaired"]
    assert EB.get_state_apply_outbox(delivered.event_id)["downstream_status"] == "applied"
    assert EB.get_state_apply_outbox(correction.event_id)["downstream_status"] == "applied"


def test_panel_resurrection_writer_uses_durable_chokepoint():
    source = Path(PW.__file__).read_text(encoding="utf-8")
    resurrection_block = source[source.index("DELIVERED RESURRECTION") :]
    assert '"ORDER_RESURRECTED"' in resurrection_block
    assert "resurrect_order(" not in resurrection_block


@pytest.mark.parametrize("processed", [False, True])
def test_legacy_queue_duplicate_is_upgraded_with_missing_durable_outbox(
    isolated_stores, processed
):
    """A legacy source row cannot permanently block the later durable receipt."""
    event_id = "c3-order_COURIER_DELIVERED_legacy-upgrade"
    source_payload = {"source": "legacy-upgrade"}
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=source_payload,
        event_id=event_id,
    ) == event_id
    if processed:
        assert EB.mark_processed(event_id) is True

    state_event = {
        "event_type": "COURIER_DELIVERED",
        "event_id": event_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T09:00:00+00:00"},
    }
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=source_payload,
        event_id=event_id,
        state_event=state_event,
        event_key="c3-order_COURIER_DELIVERED_legacy-upgrade",
        expected_state_version="2026-07-19T08:00:00+00:00",
    ) is None

    receipt = EB.get_state_apply_outbox(event_id)
    assert receipt is not None
    assert receipt["state_status"] == "pending"
    assert receipt["state_event"] == state_event


def test_legacy_audit_duplicate_is_upgraded_with_missing_durable_outbox(
    isolated_stores,
):
    event_id = "c3-order_COURIER_ASSIGNED_legacy-upgrade"
    source_payload = {"source": "legacy-upgrade"}
    assert EB.emit_audit(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload=source_payload,
        event_id=event_id,
    ) == event_id
    state_event = {
        "event_type": "COURIER_ASSIGNED",
        "event_id": event_id,
        "order_id": "c3-order",
        "courier_id": "200",
        "payload": source_payload,
    }

    assert EB.emit_audit(
        "COURIER_ASSIGNED",
        order_id="c3-order",
        courier_id="200",
        payload=source_payload,
        event_id=event_id,
        state_event=state_event,
        event_key="c3-order_COURIER_ASSIGNED_legacy-upgrade",
        expected_state_version="2026-07-19T08:00:00+00:00",
    ) is None

    receipt = EB.get_state_apply_outbox(event_id)
    assert receipt is not None
    assert receipt["state_event"] == state_event


def test_real_bridge_upgrades_matching_legacy_event_key_instead_of_forking_source(
    isolated_stores,
):
    """Production bridge must reuse legacy K, not create a second K_v event."""
    event_key = "c3-order_COURIER_DELIVERED_canonical"
    payload = {"source": "legacy-bridge"}
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=payload,
        event_id=event_key,
    ) == event_key

    outcome = DEA.emit_and_apply(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload=payload,
        state_payload={"timestamp": "2026-07-19T09:00:00+00:00"},
        event_key=event_key,
        emit_fn=EB.emit,
        state_update_fn=SM.update_from_event,
        effect_status_fn=SM.event_effect_status,
        get_order_fn=SM.get_order_strict,
        downstream_fn=lambda _event: None,
    )

    assert outcome.event_id == event_key
    assert outcome.event_created is False
    assert outcome.state_ready is True
    assert EB.get_state_apply_outbox(event_key) is not None
    with sqlite3.connect(isolated_stores[0]) as conn:
        source_ids = [
            row[0]
            for row in conn.execute(
                "SELECT event_id FROM events WHERE event_id LIKE ? ORDER BY event_id",
                (event_key + "%",),
            )
        ]
    assert source_ids == [event_key]


def test_durable_upgrade_rejects_legacy_source_event_id_collision(
    isolated_stores,
):
    event_id = "c3-order_COURIER_DELIVERED_collision"
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "different"},
        event_id=event_id,
    ) == event_id
    state_event = {
        "event_type": "COURIER_DELIVERED",
        "event_id": event_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-07-19T09:00:00+00:00"},
    }

    with pytest.raises(ValueError, match="event_id collision"):
        EB.emit(
            "COURIER_DELIVERED",
            order_id="c3-order",
            courier_id="100",
            payload={"source": "requested"},
            event_id=event_id,
            state_event=state_event,
            event_key="c3-order_COURIER_DELIVERED_collision",
        )
    assert EB.get_state_apply_outbox(event_id) is None


def test_audit_cleanup_keeps_queue_receipt_until_queue_is_processed(
    isolated_stores, monkeypatch
):
    events_db, _state_path = isolated_stores
    event_id = "c3-order_COURIER_DELIVERED_old-pending-queue"
    state_event = {
        "event_type": "COURIER_DELIVERED",
        "event_id": event_id,
        "order_id": "c3-order",
        "courier_id": "100",
        "payload": {"timestamp": "2026-04-01T09:00:00+00:00"},
    }
    assert EB.emit(
        "COURIER_DELIVERED",
        order_id="c3-order",
        courier_id="100",
        payload={"source": "retention-golden"},
        event_id=event_id,
        state_event=state_event,
        event_key="c3-order_COURIER_DELIVERED_old-pending-queue",
    ) == event_id
    assert EB.mark_state_apply_applied(event_id) is True
    assert EB.mark_state_apply_downstream(event_id) is True
    with sqlite3.connect(events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox SET created_at='2026-01-01T00:00:00+00:00' "
            "WHERE event_id=?",
            (event_id,),
        )
        conn.execute(
            "UPDATE audit_log SET created_at='2026-01-01T00:00:00+00:00' "
            "WHERE event_id=?",
            (event_id,),
        )
    monkeypatch.setattr(EB, "_is_peak_window", lambda: False)

    EB.cleanup_audit_log(retention_days=1)
    assert EB.get_state_apply_outbox(event_id) is not None
    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 1

    assert EB.mark_processed(event_id) is True
    EB.cleanup_audit_log(retention_days=1)
    assert EB.get_state_apply_outbox(event_id) is None
