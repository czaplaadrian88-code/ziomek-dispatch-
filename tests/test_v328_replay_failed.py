"""V3.28 Fix 4 (incident 03.05.2026) — replay_failed CLI tests.

Test coverage:
- query_failed_events (filter oid / status / since)
- replay_event (skip non-NEW_ORDER, empty payload, redacted failure metadata)
- apply_status_flip (atomic UPDATE, transactional rollback on error)
- end-to-end replay_batch z synthetic events.db
"""
import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import replay_failed as rf  # noqa: E402


def _build_synthetic_events_db():
    """Build temp events.db z 5 synthetic events (3 failed NEW_ORDER, 1 processed, 1 unsupported)."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.executescript("""
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
    """)
    payload_minimal = json.dumps({
        "order_id": "999",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
        "restaurant": "Test",
        "pickup_at_warsaw": "2026-05-03T15:00:00+02:00",
        "prep_minutes": 15,
        "order_type": "elastic",
        "status_id": 1,
    })
    rows = [
        ("e1", "NEW_ORDER", "100", None, payload_minimal, "2026-05-01T12:00:00+00:00", None, "failed"),
        ("e2", "NEW_ORDER", "101", None, payload_minimal, "2026-05-02T12:00:00+00:00", None, "failed"),
        ("e3", "NEW_ORDER", "102", None, payload_minimal, "2026-05-03T12:00:00+00:00", None, "failed"),
        ("e4", "NEW_ORDER", "103", None, payload_minimal, "2026-04-25T12:00:00+00:00", "2026-04-25T12:01:00+00:00", "processed"),
        ("e5", "COURIER_ASSIGNED", "104", "201", "{}", "2026-05-01T13:00:00+00:00", None, "failed"),
    ]
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_query_failed_events_filter_by_status():
    """Default status='failed' → returns 4 (e1, e2, e3, e5)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, status="failed")
        assert len(rows) == 4
        oids = sorted(r["order_id"] for r in rows)
        assert oids == ["100", "101", "102", "104"]
    finally:
        Path(db_path).unlink()


def test_query_failed_events_filter_by_oid():
    """--oid 101 → returns 1 row (e2)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, oid="101")
        assert len(rows) == 1
        assert rows[0]["event_id"] == "e2"
    finally:
        Path(db_path).unlink()


def test_query_failed_events_filter_by_since():
    """--since '2026-05-02' → returns 2 (e2 + e3, e1+e5 are older)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, status="failed", since="2026-05-02")
        assert len(rows) == 2
        oids = sorted(r["order_id"] for r in rows)
        assert oids == ["101", "102"]
    finally:
        Path(db_path).unlink()


def test_replay_event_skip_unsupported_event_type():
    """COURIER_ASSIGNED event type → SKIP verdict (only NEW_ORDER replay supported)."""
    row = {
        "event_id": "e5",
        "event_type": "COURIER_ASSIGNED",
        "order_id": "104",
        "payload": "{}",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert result["outcome"] == "skip"
    assert result["error_code"] == "unsupported_event_type"


def test_replay_event_skip_empty_payload():
    """NEW_ORDER z empty payload → SKIP verdict."""
    row = {
        "event_id": "e_empty",
        "event_type": "NEW_ORDER",
        "order_id": "999",
        "payload": "",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert result["outcome"] == "skip"
    assert result["error_class"] == "permanent"
    assert result["error_code"] == "invalid_payload"


def test_apply_status_flip_atomic_success():
    """Apply flip dla 2 PASS event_ids → 2 flipped, 0 errors."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["e1", "e2"])
        assert stats["flipped"] == 2
        assert stats["errors"] == []
        # Verify post-flip
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT status FROM events WHERE event_id IN ('e1', 'e2')")
        statuses = [r[0] for r in cur.fetchall()]
        assert all(s == "processed" for s in statuses)
        conn.close()
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_only_flips_failed_status():
    """Apply flip dla event w status=processed (e4) → 0 flipped (WHERE status='failed' clause)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["e4"])  # e4 already processed
        assert stats["flipped"] == 0
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_empty_list_safe():
    """Empty list → 0 flipped, 0 errors (defensive default)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, [])
        assert stats["flipped"] == 0
        assert stats["errors"] == []
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_nonexistent_eid_zero_flipped():
    """Non-existent event_id → 0 flipped (no row matches)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["nonexistent_eid"])
        assert stats["flipped"] == 0
    finally:
        Path(db_path).unlink()


def test_replay_batch_synthetic_no_apply():
    """End-to-end replay_batch bez --apply → status events unchanged."""
    db_path = _build_synthetic_events_db()
    try:
        summary = rf.replay_batch(db_path=db_path, status="failed", apply=False)
        assert summary["total"] == 4  # 3 NEW_ORDER + 1 COURIER_ASSIGNED skip
        # Bez apply, summary["applied"] is None
        assert summary["applied"] is None
        # Verify events.db status NIE zmieniona
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE status='failed'")
        assert cur.fetchone()[0] == 4
        conn.close()
    finally:
        Path(db_path).unlink()


def test_replay_event_returns_dict_with_required_fields():
    """Public result zawiera tylko zredagowany kontrakt."""
    row = {
        "event_id": "e_skip",
        "event_type": "OTHER",
        "order_id": "?",
        "payload": "{}",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert isinstance(result, dict)
    assert set(result) == {
        "event_ref",
        "outcome",
        "error_class",
        "error_code",
        "candidates_count",
    }
    assert result["event_ref"] == rf.event_retry.event_reference("e_skip")


def test_replay_event_redacts_malicious_ids_payload_and_exception(
    monkeypatch, capsys, caplog,
):
    id_marker = "RAW-EVENT-ID-MARKER"
    order_marker = "RAW-ORDER-ID-MARKER"
    payload_marker = "RAW-PAYLOAD-MARKER"
    exception_marker = "RAW-EXCEPTION-MARKER"
    stdout_marker = "RAW-STDOUT-MARKER"
    stderr_marker = "RAW-STDERR-MARKER"
    logger_marker = "RAW-LOGGER-MARKER"
    marker_logger = logging.getLogger("replay_failed_malicious_dependency")

    from dispatch_v2 import courier_resolver

    def fail_without_leaking():
        print(stdout_marker)
        print(stderr_marker, file=sys.stderr)
        marker_logger.error(logger_marker)
        raise RuntimeError(exception_marker)

    monkeypatch.setattr(courier_resolver, "dispatchable_fleet", fail_without_leaking)
    with caplog.at_level(logging.WARNING):
        result = rf.replay_event({
            "event_id": id_marker,
            "event_type": "NEW_ORDER",
            "order_id": order_marker,
            "payload": json.dumps({
                "restaurant": payload_marker,
                "delivery_address": payload_marker,
            }),
            "status": "failed",
        })
    suppressed_capture = capsys.readouterr()
    encoded = json.dumps(result, sort_keys=True)
    assert result["outcome"] == "fail"
    assert result["error_class"] == "permanent"
    assert result["error_code"] == "unexpected_failure"
    for marker in (
        id_marker,
        order_marker,
        payload_marker,
        exception_marker,
        stdout_marker,
        stderr_marker,
        logger_marker,
    ):
        assert marker not in encoded
        assert marker not in suppressed_capture.out
        assert marker not in suppressed_capture.err
        assert marker not in caplog.text
    for forbidden_key in (
        "event_id",
        "order_id",
        "best_courier_id",
        "reason",
        "traceback",
    ):
        assert forbidden_key not in result

    print("AFTER-STDOUT-VISIBLE")
    print("AFTER-STDERR-VISIBLE", file=sys.stderr)
    marker_logger.warning("AFTER-LOGGER-VISIBLE")
    restored_capture = capsys.readouterr()
    assert "AFTER-STDOUT-VISIBLE" in restored_capture.out
    assert "AFTER-STDERR-VISIBLE" in restored_capture.err
    assert "AFTER-LOGGER-VISIBLE" in caplog.text


def test_secure_output_is_atomic_private_and_rejects_existing_target(tmp_path):
    output = tmp_path / "replay.json"
    value = {"total": 1, "results": [{"event_ref": "digest-only"}]}
    rf.write_secure_output(str(output), value)
    assert json.loads(output.read_text(encoding="utf-8")) == value
    output_stat = output.stat()
    assert output_stat.st_mode & 0o777 == 0o600
    assert output_stat.st_nlink == 1
    with pytest.raises(FileExistsError):
        rf.write_secure_output(str(output), value)


def test_secure_output_rejects_symlink_without_touching_target(tmp_path):
    victim = tmp_path / "victim.json"
    victim.write_text("VICTIM-MARKER", encoding="utf-8")
    output = tmp_path / "replay.json"
    output.symlink_to(victim)

    with pytest.raises(FileExistsError):
        rf.write_secure_output(str(output), {"total": 0})

    assert output.is_symlink()
    assert victim.read_text(encoding="utf-8") == "VICTIM-MARKER"


def test_secure_output_rejects_symlink_in_ancestor(tmp_path):
    real_parent = tmp_path / "real" / "nested"
    real_parent.mkdir(parents=True)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(tmp_path / "real", target_is_directory=True)
    output = linked_parent / "nested" / "replay.json"

    with pytest.raises(OSError):
        rf.write_secure_output(str(output), {"total": 0})

    assert not (real_parent / "replay.json").exists()


def test_secure_output_rejects_existing_hardlink_leaf(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("SOURCE-MARKER", encoding="utf-8")
    output = tmp_path / "replay.json"
    os.link(source, output)
    before = source.stat()

    with pytest.raises(FileExistsError):
        rf.write_secure_output(str(output), {"total": 0})

    assert source.read_text(encoding="utf-8") == "SOURCE-MARKER"
    assert output.samefile(source)
    assert source.stat().st_nlink == before.st_nlink == 2


def test_query_missing_db_raises():
    """Missing db raises sqlite OperationalError przy connect (NOT silent default)."""
    with pytest.raises(sqlite3.OperationalError):
        rf.query_failed_events("/nonexistent/path.db", oid="999")
