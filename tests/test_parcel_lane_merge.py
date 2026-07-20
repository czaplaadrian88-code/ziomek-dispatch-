"""Faza 2 Etap 3 — merger paczek do orders_state + strażnik watchera (pomija source=parcel).

Flaga ON≠OFF, create/keep/retire, oraz dowód że watcher NIE prefetchuje paczek (twin gastro
nietknięty). Izolowane (monkeypatch state_machine/flag/snapshot), bez sieci/plików.
"""
import fcntl
import json
import os
import threading

from dispatch_v2 import panel_watcher as pw
from dispatch_v2 import parcel_lane_merge as plm

_PARCEL = {"order_id": 900000005, "source": "parcel", "status": "planned",
           "pickup_coords": [53.13, 23.16], "delivery_coords": [53.12, 23.14]}

# ── merger ────────────────────────────────────────────────────────────────
def test_merge_flag_off_noop(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: False)
    assert plm.run() == {"enabled": False}


def test_merge_stale_or_missing_snapshot(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: None)
    assert plm.run() == {"enabled": True, "snapshot": "missing_or_stale", "status_applied": 0}


def test_merge_creates_new_parcel(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {"900000005": _PARCEL})
    monkeypatch.setattr(plm.sm, "get_all", lambda: {})
    created = []
    monkeypatch.setattr(plm.sm, "upsert_order", lambda oid, e, event=None: created.append((oid, event)))
    monkeypatch.setattr(plm.sm, "set_status", lambda *a, **k: None)
    emitted = []
    monkeypatch.setattr(plm.event_bus, "emit",
                        lambda et, order_id=None, payload=None, event_id=None: emitted.append((et, order_id, event_id)) or event_id)
    stats = plm.run()
    assert stats["created"] == 1 and created == [("900000005", "PARCEL_LANE_NEW")]
    # NEW_ORDER wyemitowany → shadow_dispatcher zaproponuje paczkę
    assert stats["emitted"] == 1
    assert emitted == [("NEW_ORDER", "900000005", "900000005_NEW_ORDER_parcel")]


def test_merge_keeps_existing_no_clobber(monkeypatch):
    """Paczka już w stanie (silnik mógł dodać courier_id) → NIE re-upsert."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {"900000005": _PARCEL})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"900000005": {"source": "parcel", "status": "assigned", "courier_id": 7}})
    upserts = []
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: upserts.append(a))
    monkeypatch.setattr(plm.sm, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(plm.event_bus, "emit", lambda *a, **k: None)  # już wyemitowany wcześniej
    stats = plm.run()
    assert stats["kept"] == 1 and stats["created"] == 0 and upserts == []


def test_merge_retires_gone_parcel(monkeypatch):
    """Paczka zniknęła ze snapshotu (anulowana/usunięta) → terminalna (sprzątanie)."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"900000005": {"source": "parcel", "status": "planned"}})
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: None)
    retired = []
    monkeypatch.setattr(plm.sm, "set_status", lambda oid, st, event=None: retired.append((oid, st)))
    stats = plm.run()
    assert stats["retired"] == 1 and retired == [("900000005", "cancelled")]


def test_merge_leaves_gastro_alone(monkeypatch):
    """Sprzątanie dotyka TYLKO source=parcel — gastro w stanie nietknięte."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"484000": {"status": "planned"}})  # gastro, brak source
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: None)
    touched = []
    monkeypatch.setattr(plm.sm, "set_status", lambda oid, st, event=None: touched.append(oid))
    stats = plm.run()
    assert stats["retired"] == 0 and touched == []


# ── strażnik watchera ───────────────────────────────────────────────────────
def test_watcher_prefetch_skips_parcels():
    """_build_prefetch_candidates POMIJA source=parcel, ale gastro spoza HTML NADAL bierze."""
    parsed = {"order_ids": ["111"], "assigned_ids": set()}
    state = {
        "900000005": {"status": "planned", "source": "parcel"},  # paczka → pominąć
        "222": {"status": "planned"},                            # gastro spoza HTML → prefetch
    }
    out = pw._build_prefetch_candidates(parsed, state, set(), False, False, False)
    assert "900000005" not in out      # strażnik działa
    assert "222" in out                # twin gastro nietknięty


# ── Etap 3c: inbox statusów z apki → orders_state ──────────────────────────
def test_apply_status_inbox(monkeypatch, tmp_path):
    """5=odebrane→PICKED_UP, 7=doręczone→DELIVERED, 3=ignorowane. Idempotent po event_id."""
    (tmp_path / "parcel_status_inbox.jsonl").write_text(
        '{"oid":"900138096","status_code":5,"cid":61,"ts":111}\n'
        '{"oid":"900138096","status_code":7,"cid":61,"ts":222}\n'
        '{"oid":"900138096","status_code":3,"cid":61,"ts":333}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    calls = []

    def durable_ok(event_type, **kwargs):
        calls.append((event_type, kwargs))
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{kwargs['event_key']}_v1",
            event_key=kwargs["event_key"],
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_ok)
    assert plm._apply_status_inbox() == 2          # 5+7; 3 pominięte
    assert [event_type for event_type, _kwargs in calls] == [
        "COURIER_PICKED_UP", "COURIER_DELIVERED"
    ]
    assert all(
        kwargs["payload"] == {"source": "parcel_status_inbox"}
        for _event_type, kwargs in calls
    )
    # Faza A tylko ujawnia brak kontraktu czasu; nie zamienia e.ts na timestamp
    # bez decyzji o jednostce/semantyce ani nie przywraca fallbacku now().
    assert all("timestamp" not in kwargs["payload"] for _, kwargs in calls)
    assert calls[0][1]["event_key"] == "900138096_COURIER_PICKED_UP_111"
    assert calls[0][1]["emit_fn"] is plm.event_bus.emit


def test_apply_status_inbox_idempotent(monkeypatch, tmp_path):
    """Domknięty durable duplicate nie podnosi licznika zastosowań."""
    (tmp_path / "parcel_status_inbox.jsonl").write_text(
        '{"oid":"900138096","status_code":5,"cid":61,"ts":111}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    monkeypatch.setattr(
        plm.durable_event_apply,
        "emit_and_apply",
        lambda _event_type, **kwargs: plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{kwargs['event_key']}_v1",
            event_key=kwargs["event_key"],
            event_created=False,
            state_ready=True,
            state_transitioned=False,
            downstream_executed=False,
        ),
    )
    assert plm._apply_status_inbox() == 0


def test_apply_status_inbox_snapshots_every_nonempty_active_inode(monkeypatch, tmp_path):
    """Niepusty inbox trafia do unikalnego archiwum ponawianego do skutku."""
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text('{"oid":"900000005","status_code":5,"cid":61,"ts":1}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    monkeypatch.setattr(
        plm.durable_event_apply,
        "emit_and_apply",
        lambda _event_type, **kwargs: plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{kwargs['event_key']}_v1",
            event_key=kwargs["event_key"],
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
        ),
    )
    plm._apply_status_inbox()
    assert not inbox.exists()                         # zrotowany
    assert len(list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*"))) == 1


def test_failed_emit_survives_rotation_and_retries_from_archive(monkeypatch, tmp_path):
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text(
        '{"oid":"900000005","status_code":5,"cid":61,"ts":1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    attempts = 0

    def fail_then_apply(_event_type, **kwargs):
        nonlocal attempts
        attempts += 1
        ready = attempts > 1
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{kwargs['event_key']}_v1",
            event_key=kwargs["event_key"],
            event_created=ready,
            state_ready=ready,
            state_transitioned=ready,
            downstream_executed=ready,
            failure_stage=None if ready else "emit",
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", fail_then_apply)

    assert plm._apply_status_inbox() == 0
    assert len(list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*"))) == 1
    assert plm._apply_status_inbox() == 1
    assert attempts == 2
    assert list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*")) == []


def test_append_after_snapshot_moves_into_replayed_archive(monkeypatch, tmp_path):
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text(
        '{"oid":"900000005","status_code":5,"cid":61,"ts":1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    seen = {}

    def durable_once(_event_type, **kwargs):
        key = kwargs["event_key"]
        prior = seen.get(key, 0)
        seen[key] = prior + 1
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{key}_v1",
            event_key=key,
            event_created=prior == 0,
            state_ready=True,
            state_transitioned=prior == 0,
            downstream_executed=prior == 0,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_once)
    real_apply_file = plm._apply_status_file
    appended = False

    def append_after_read(target):
        nonlocal appended
        result = real_apply_file(target)
        if target.name.startswith(plm.STATUS_INBOX_NAME + ".pending.") and not appended:
            appended = True
            with inbox.open("a", encoding="utf-8") as stream:
                stream.write(
                    '{"oid":"900000006","status_code":7,"cid":61,"ts":2}\n'
                )
        return result

    monkeypatch.setattr(plm, "_apply_status_file", append_after_read)

    assert plm._apply_status_inbox() == 1
    assert inbox.exists()  # append po snapshotcie trafia do nowego aktywnego inode
    assert plm._apply_status_inbox() == 1
    assert seen == {
        "900000005_COURIER_PICKED_UP_1": 2,
        "900000006_COURIER_DELIVERED_2": 1,
    }
    # Najswiezszy immutable snapshot jest celowo trzymany do kolejnego ticku.
    assert len(list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*"))) == 1


def test_open_producer_fd_cannot_be_unlinked_before_append(monkeypatch, tmp_path):
    """The shared sidecar lock closes the open-fd -> rename -> unlink loss window."""
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text("", encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    seen = []
    completed = set()

    def durable_ok(_event_type, **kwargs):
        key = kwargs["event_key"]
        first = key not in completed
        if first:
            completed.add(key)
            seen.append(key)
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{key}_v1",
            event_key=key,
            event_created=first,
            state_ready=True,
            state_transitioned=first,
            downstream_executed=first,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_ok)
    producer_open = threading.Event()
    allow_write = threading.Event()

    def producer():
        lock_path = inbox.with_name(inbox.name + ".lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            stream = inbox.open("a", encoding="utf-8")
            try:
                producer_open.set()
                assert allow_write.wait(timeout=5)
                stream.write(
                    '{"oid":"900000007","status_code":5,"cid":61,"ts":7}\n'
                )
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                stream.close()
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    consumed = []

    def consumer():
        consumed.append(plm._apply_status_inbox())
        consumed.append(plm._apply_status_inbox())

    producer_thread = threading.Thread(target=producer)
    producer_thread.start()
    assert producer_open.wait(timeout=5)
    consumer_thread = threading.Thread(target=consumer)
    consumer_thread.start()
    # A correct consumer waits on the producer's stable namespace lock. The
    # old implementation races through two ticks and unlinks the old inode.
    consumer_thread.join(timeout=0.2)
    allow_write.set()
    producer_thread.join(timeout=5)
    consumer_thread.join(timeout=5)

    assert not producer_thread.is_alive()
    assert not consumer_thread.is_alive()
    plm._apply_status_inbox()
    assert seen == ["900000007_COURIER_PICKED_UP_7"]


def test_legacy_unlocked_writer_archive_is_retained_without_live_v2_marker(
    monkeypatch, tmp_path
):
    """Rolling deploy: old open fd stays reachable until v2 writer is live."""
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text(
        '{"oid":"900000010","status_code":5,"cid":61,"ts":10}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    seen = set()

    def durable_once(_event_type, **kwargs):
        key = kwargs["event_key"]
        first = key not in seen
        seen.add(key)
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{key}_v1",
            event_key=key,
            event_created=first,
            state_ready=True,
            state_transitioned=first,
            downstream_executed=first,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_once)
    legacy_stream = inbox.open("a", encoding="utf-8")  # old binary: no lock
    try:
        assert plm._apply_status_inbox() == 1
        assert plm._apply_status_inbox() == 0
        archives = list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*"))
        assert len(archives) == 1
        legacy_stream.write(
            '{"oid":"900000011","status_code":7,"cid":61,"ts":11}\n'
        )
        legacy_stream.flush()
        os.fsync(legacy_stream.fileno())
    finally:
        legacy_stream.close()

    assert plm._apply_status_inbox() == 1
    assert "900000011_COURIER_DELIVERED_11" in seen


def test_cooperative_writer_does_not_unlink_archive_held_by_legacy_writer(
    monkeypatch, tmp_path
):
    """A new writer PID is not proof that an overlapping v1 fd is gone."""
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text(
        '{"oid":"900000012","status_code":5,"cid":61,"ts":12}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json")
    )
    seen = set()

    def durable_once(_event_type, **kwargs):
        key = kwargs["event_key"]
        first = key not in seen
        seen.add(key)
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=f"{key}_v1",
            event_key=key,
            event_created=first,
            state_ready=True,
            state_transitioned=first,
            downstream_executed=first,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_once)
    legacy_stream = inbox.open("a", encoding="utf-8")
    try:
        assert plm._apply_status_inbox() == 1
        lock_fd = os.open(
            str(plm._status_inbox_lock_path(inbox)),
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            inbox.touch()  # a new lock-aware writer now owns the active name
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        assert plm._apply_status_inbox() == 0
        archives = list(tmp_path.glob("parcel_status_inbox.jsonl.pending.*"))
        assert len(archives) == 1
        legacy_stream.write(
            '{"oid":"900000013","status_code":7,"cid":61,"ts":13}\n'
        )
        legacy_stream.flush()
        os.fsync(legacy_stream.fileno())
    finally:
        legacy_stream.close()

    assert plm._apply_status_inbox() == 1
    assert "900000013_COURIER_DELIVERED_13" in seen


def test_non_object_json_row_is_retained_without_blocking_later_status(
    monkeypatch, tmp_path
):
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    archive = tmp_path / "parcel_status_inbox.jsonl.pending.1.1"
    archive.write_text(
        '[]\n'
        '{"oid":"900000014","status_code":5,"cid":61,"ts":14}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json")
    )
    seen = []

    def durable_once(_event_type, **kwargs):
        seen.append(kwargs["event_key"])
        return plm.durable_event_apply.DurableApplyOutcome(
            event_id=kwargs["event_key"],
            event_key=kwargs["event_key"],
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
        )

    monkeypatch.setattr(plm.durable_event_apply, "emit_and_apply", durable_once)

    assert plm._apply_status_inbox() == 1
    assert seen == ["900000014_COURIER_PICKED_UP_14"]
    assert archive.exists()  # malformed row remains fail-loud/retry-visible


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
