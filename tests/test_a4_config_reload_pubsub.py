"""A4 CONFIG_RELOAD broadcast pub/sub regression coverage (2026-05-08).

Per audit META_AUDIT_ROOT_CAUSES RC2 2026-05-07: cache invalidation
cross-process via events.db typed event. Zamyka Decyzja #4 Redis defer.

Coverage:
  emit_config_reload happy path + payload merge
  emit_config_reload defensywne (DB fail → return None, NIE crash)
  make_broadcast_event_id collision-immune (ns + 8-hex)
  poll_broadcast empty + filtered by event_types + ValueError dla queue type
  poll_broadcast cursor advance (since_event_id filter)
  emit/poll round-trip end-to-end
  BroadcastSubscriber per-process cursor (advance, no duplicate redelivery)
  BroadcastSubscriber 2 instances independent cursors
  BroadcastSubscriber corrupt state → fresh start
  flags_admin set/del → emit CONFIG_RELOAD hook
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dispatch_v2 import event_bus
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_events_db(tmp_path, monkeypatch):
    """Isolated events.db per test."""
    db_path = tmp_path / "events.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    return db_path


# ---------------------------------------------------------------------------
# make_broadcast_event_id
# ---------------------------------------------------------------------------


def test_make_broadcast_event_id_format():
    eid = event_bus.make_broadcast_event_id("flags")
    assert eid.startswith("CONFIG_RELOAD_flags_")
    parts = eid.split("_")
    # CONFIG_RELOAD + flags + ns + hex
    assert len(parts) >= 4
    assert len(parts[-1]) == 8  # 4-byte hex


def test_make_broadcast_event_id_collision_immune():
    """1000 fast emits — wszystkie unikalne."""
    ids = {event_bus.make_broadcast_event_id("flags") for _ in range(1000)}
    assert len(ids) == 1000


# ---------------------------------------------------------------------------
# emit_config_reload
# ---------------------------------------------------------------------------


def test_emit_config_reload_happy_path(tmp_events_db):
    eid = event_bus.emit_config_reload(scope="flags", payload={"name": "FLAG_X", "value": True})
    assert eid is not None
    assert eid.startswith("CONFIG_RELOAD_flags_")

    conn = sqlite3.connect(str(tmp_events_db))
    cur = conn.execute("SELECT event_type, status, payload FROM events WHERE event_id = ?", (eid,))
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "CONFIG_RELOAD"
    assert row[1] == "broadcast"
    payload = json.loads(row[2])
    assert payload["scope"] == "flags"
    assert payload["name"] == "FLAG_X"
    assert payload["value"] is True


def test_emit_config_reload_no_payload(tmp_events_db):
    eid = event_bus.emit_config_reload(scope="courier_tiers")
    assert eid is not None
    conn = sqlite3.connect(str(tmp_events_db))
    cur = conn.execute("SELECT payload FROM events WHERE event_id = ?", (eid,))
    payload = json.loads(cur.fetchone()[0])
    assert payload == {"scope": "courier_tiers"}


def test_emit_config_reload_defensive_db_fail_returns_none(tmp_events_db, monkeypatch, caplog):
    """DB locked exhausted retry → return None, NIE re-raise."""
    def fail(*a, **kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(event_bus, "_emit_broadcast_inner", fail)
    monkeypatch.setattr(event_bus, "_RETRY_BACKOFF_MS", (1, 1))  # fast test

    with caplog.at_level("ERROR"):
        result = event_bus.emit_config_reload(scope="flags")
    assert result is None
    assert any("emit_config_reload" in r.message and "FAIL" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# poll_broadcast
# ---------------------------------------------------------------------------


def test_poll_broadcast_empty(tmp_events_db):
    result = event_bus.poll_broadcast(["CONFIG_RELOAD"])
    assert result == []


def test_poll_broadcast_invisible_to_get_pending(tmp_events_db):
    """Broadcast events status='broadcast' → get_pending(NEW_ORDER) ich nie zwraca."""
    event_bus.emit_config_reload(scope="flags")
    event_bus.emit("NEW_ORDER", order_id="100", payload={})
    pending = event_bus.get_pending(event_types=["NEW_ORDER"])
    assert len(pending) == 1
    assert pending[0]["event_type"] == "NEW_ORDER"


def test_poll_broadcast_rejects_non_broadcast_type(tmp_events_db):
    with pytest.raises(ValueError, match="not in BROADCAST_EVENT_TYPES"):
        event_bus.poll_broadcast(["NEW_ORDER"])


def test_poll_broadcast_cursor_filter(tmp_events_db):
    e1 = event_bus.emit_config_reload(scope="flags", payload={"i": 1})
    time.sleep(0.001)
    e2 = event_bus.emit_config_reload(scope="flags", payload={"i": 2})
    time.sleep(0.001)
    e3 = event_bus.emit_config_reload(scope="flags", payload={"i": 3})

    all_events = event_bus.poll_broadcast(["CONFIG_RELOAD"])
    assert len(all_events) == 3
    assert [e["payload"]["i"] for e in all_events] == [1, 2, 3]

    after_e1 = event_bus.poll_broadcast(["CONFIG_RELOAD"], since_event_id=e1)
    assert len(after_e1) == 2
    assert after_e1[0]["payload"]["i"] == 2

    after_e3 = event_bus.poll_broadcast(["CONFIG_RELOAD"], since_event_id=e3)
    assert after_e3 == []


def test_poll_broadcast_limit(tmp_events_db):
    for i in range(10):
        event_bus.emit_config_reload(scope="flags", payload={"i": i})
        time.sleep(0.0005)
    result = event_bus.poll_broadcast(["CONFIG_RELOAD"], limit=3)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# BroadcastSubscriber
# ---------------------------------------------------------------------------


def test_subscriber_first_poll_returns_all_advances_cursor(tmp_events_db, tmp_path):
    event_bus.emit_config_reload(scope="flags", payload={"i": 1})
    time.sleep(0.001)
    event_bus.emit_config_reload(scope="flags", payload={"i": 2})

    sub = BroadcastSubscriber("test_consumer", tmp_path / "sub.json")
    new = sub.poll(["CONFIG_RELOAD"])
    assert len(new) == 2

    # State persisted with cursor=last_seen
    state = json.loads((tmp_path / "sub.json").read_text())
    assert "CONFIG_RELOAD" in state["cursor_per_type"]


def test_subscriber_no_duplicate_redelivery(tmp_events_db, tmp_path):
    event_bus.emit_config_reload(scope="flags")
    sub = BroadcastSubscriber("test", tmp_path / "sub.json")
    first = sub.poll(["CONFIG_RELOAD"])
    second = sub.poll(["CONFIG_RELOAD"])
    assert len(first) == 1
    assert second == []


def test_subscriber_picks_up_new_after_first_poll(tmp_events_db, tmp_path):
    event_bus.emit_config_reload(scope="flags", payload={"i": 1})
    sub = BroadcastSubscriber("test", tmp_path / "sub.json")
    sub.poll(["CONFIG_RELOAD"])

    time.sleep(0.001)
    event_bus.emit_config_reload(scope="flags", payload={"i": 2})
    new = sub.poll(["CONFIG_RELOAD"])
    assert len(new) == 1
    assert new[0]["payload"]["i"] == 2


def test_subscriber_two_instances_independent_cursors(tmp_events_db, tmp_path):
    event_bus.emit_config_reload(scope="flags", payload={"i": 1})
    time.sleep(0.001)
    event_bus.emit_config_reload(scope="flags", payload={"i": 2})

    sub_a = BroadcastSubscriber("a", tmp_path / "a.json")
    sub_b = BroadcastSubscriber("b", tmp_path / "b.json")

    a_first = sub_a.poll(["CONFIG_RELOAD"])
    assert len(a_first) == 2

    b_first = sub_b.poll(["CONFIG_RELOAD"])
    assert len(b_first) == 2  # B niezależny od A — dostaje wszystkie
    assert sub_a.poll(["CONFIG_RELOAD"]) == []
    assert sub_b.poll(["CONFIG_RELOAD"]) == []


def test_subscriber_corrupt_state_fresh_start(tmp_events_db, tmp_path, caplog):
    state_path = tmp_path / "sub.json"
    state_path.write_text("{ corrupt JSON not valid")
    event_bus.emit_config_reload(scope="flags")

    sub = BroadcastSubscriber("test", state_path)
    with caplog.at_level("WARNING"):
        new = sub.poll(["CONFIG_RELOAD"])
    assert len(new) == 1
    assert any("corrupt state" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# flags_admin hook
# ---------------------------------------------------------------------------


def _patch_flags_path(monkeypatch, flags_path):
    """Lekcja #90 — default-arg binding: monkeypatch FLAGS_PATH na module
    NIE wpływa na default args już-zbindowane w sygnaturze. Trzeba podmienić
    helpers na flags_admin module żeby używały nowej ścieżki."""
    from dispatch_v2.core import flags_io
    from dispatch_v2 import flags_admin
    monkeypatch.setattr(flags_admin, "load_flags", lambda: flags_io.load_flags(flags_path))
    monkeypatch.setattr(flags_admin, "update_flag", lambda n, v: flags_io.update_flag(n, v, path=flags_path))
    monkeypatch.setattr(flags_admin, "delete_flag", lambda n: flags_io.delete_flag(n, path=flags_path))


def test_flags_admin_set_emits_config_reload(tmp_events_db, tmp_path, monkeypatch):
    """CLI cmd_set → emit CONFIG_RELOAD scope='flags'."""
    flags_path = tmp_path / "flags.json"
    flags_path.write_text("{}")
    _patch_flags_path(monkeypatch, flags_path)

    from dispatch_v2 import flags_admin
    args = MagicMock()
    args.name = "A4_TEST_FLAG"
    args.value = "true"
    flags_admin.cmd_set(args)

    events = event_bus.poll_broadcast(["CONFIG_RELOAD"])
    assert len(events) == 1
    assert events[0]["payload"]["scope"] == "flags"
    assert events[0]["payload"]["name"] == "A4_TEST_FLAG"
    assert events[0]["payload"]["action"] == "set"
    assert events[0]["payload"]["value"] is True


def test_flags_admin_del_emits_config_reload_only_when_existed(tmp_events_db, tmp_path, monkeypatch):
    flags_path = tmp_path / "flags.json"
    flags_path.write_text(json.dumps({"EXISTING": True}))
    _patch_flags_path(monkeypatch, flags_path)

    from dispatch_v2 import flags_admin
    a1 = MagicMock(); a1.name = "EXISTING"
    a2 = MagicMock(); a2.name = "NONEXISTING"
    flags_admin.cmd_del(a1)
    flags_admin.cmd_del(a2)

    events = event_bus.poll_broadcast(["CONFIG_RELOAD"])
    assert len(events) == 1  # only existing del → emit
    assert events[0]["payload"]["name"] == "EXISTING"
    assert events[0]["payload"]["action"] == "del"
