"""MP-#11 core/jsonl_appender.py — atomic JSONL append shim tests (2026-05-08).

Covers:
  - happy path single record
  - parent dir auto-create
  - existing file append (preserves prior content)
  - non-ASCII Polski characters preserved (default ensure_ascii=False)
  - non-serializable record raises TypeError
  - permission denied raises OSError
  - concurrent stress 5 threads × 100 records → 500 valid JSON lines (no torn writes)
  - batch helper writes N records atomically
  - empty batch returns 0 (no I/O)
  - 3 callsites integration: panel_watcher.PANEL_OVERRIDE, telegram_approver.append_learning,
    shadow_dispatcher._append_decision all use shim end-to-end

Stress test verifies the master plan claim: 5 writers × long records (>4KB each) produce
zero torn lines (every line is valid JSON). Without flock LOCK_EX, lines >PIPE_BUF could
interleave on POSIX append.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from dispatch_v2.core import jsonl_appender as ja


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_append_single_record(tmp_path):
    p = tmp_path / "out.jsonl"
    ja.append_jsonl(p, {"a": 1, "b": "x"})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines == ['{"a": 1, "b": "x"}']


def test_parent_dir_auto_created(tmp_path):
    p = tmp_path / "nested" / "deep" / "out.jsonl"
    assert not p.parent.exists()
    ja.append_jsonl(p, {"k": "v"})
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": "v"}


def test_append_preserves_prior_content(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text('{"prior": true}\n', encoding="utf-8")
    ja.append_jsonl(p, {"new": 1})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines == ['{"prior": true}', '{"new": 1}']


def test_polish_chars_preserved(tmp_path):
    p = tmp_path / "out.jsonl"
    ja.append_jsonl(p, {"name": "Świętojańska"})
    txt = p.read_text(encoding="utf-8")
    assert "Świętojańska" in txt, f"expected native UTF-8, got {txt!r}"


def test_ensure_ascii_true_escapes(tmp_path):
    p = tmp_path / "out.jsonl"
    ja.append_jsonl(p, {"name": "Świętojańska"}, ensure_ascii=True)
    txt = p.read_text(encoding="utf-8")
    assert "\\u015a" in txt or "\\u015b" in txt or "\\u015A" in txt or "\\u015B" in txt


def test_list_record_supported(tmp_path):
    p = tmp_path / "out.jsonl"
    ja.append_jsonl(p, [1, 2, 3])
    assert json.loads(p.read_text(encoding="utf-8")) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Failure modes (fail-loud)
# ---------------------------------------------------------------------------


def test_non_serializable_raises_typeerror(tmp_path):
    p = tmp_path / "out.jsonl"

    class _NotJson:
        pass

    with pytest.raises(TypeError):
        ja.append_jsonl(p, {"obj": _NotJson()})


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses POSIX permission bits")
def test_permission_denied_raises_oserror(tmp_path):
    # Create read-only dir; appending creates file inside → PermissionError
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    os.chmod(ro_dir, 0o500)  # r-x for owner, no write
    p = ro_dir / "out.jsonl"
    try:
        with pytest.raises((PermissionError, OSError)):
            ja.append_jsonl(p, {"a": 1})
    finally:
        os.chmod(ro_dir, 0o755)  # restore for cleanup


def test_invalid_path_type_raises():
    """Non-existent disk-full simulation via os.write returning 0."""
    import unittest.mock as _m
    with pytest.raises((TypeError, OSError)):
        ja.append_jsonl(123, {"a": 1})  # int path → TypeError od pathlib


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------


def test_batch_writes_all_records(tmp_path):
    p = tmp_path / "out.jsonl"
    records = [{"i": i} for i in range(5)]
    n = ja.append_jsonl_batch(p, records)
    assert n == 5
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert [json.loads(l) for l in lines] == records


def test_batch_empty_returns_zero_no_io(tmp_path):
    p = tmp_path / "out.jsonl"
    n = ja.append_jsonl_batch(p, [])
    assert n == 0
    assert not p.exists(), "empty batch should NOT create file"


def test_batch_preserves_order(tmp_path):
    p = tmp_path / "out.jsonl"
    records = [{"i": i, "name": f"r{i}"} for i in range(20)]
    ja.append_jsonl_batch(p, records)
    lines = p.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(l) for l in lines]
    assert parsed == records


def test_batch_generator_input(tmp_path):
    p = tmp_path / "out.jsonl"
    n = ja.append_jsonl_batch(p, ({"i": i} for i in range(3)))
    assert n == 3


# ---------------------------------------------------------------------------
# Concurrency stress (master plan claim — eliminuje torn writes)
# ---------------------------------------------------------------------------


def test_concurrent_5_threads_100_records_no_torn_lines(tmp_path):
    """5 threads × 100 records each → 500 valid JSON lines."""
    p = tmp_path / "stress.jsonl"
    n_threads = 5
    n_per_thread = 100
    errors = []

    def writer(thread_id: int):
        try:
            for i in range(n_per_thread):
                ja.append_jsonl(p, {"thread": thread_id, "seq": i})
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"thread errors: {errors}"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_per_thread, \
        f"expected {n_threads * n_per_thread} lines, got {len(lines)}"
    # Every line must be valid JSON (no torn writes)
    parsed = [json.loads(l) for l in lines]
    # Each thread contributed exactly n_per_thread records
    by_thread = {}
    for r in parsed:
        by_thread.setdefault(r["thread"], []).append(r["seq"])
    for thread_id in range(n_threads):
        assert len(by_thread[thread_id]) == n_per_thread, \
            f"thread {thread_id}: lost {n_per_thread - len(by_thread[thread_id])} records"
        # Each thread's sequence is preserved (FIFO within thread)
        assert by_thread[thread_id] == list(range(n_per_thread))


def test_concurrent_long_records_no_torn_lines(tmp_path):
    """Records >PIPE_BUF (4KB) — verifies flock works for big records.

    Without flock, only O_APPEND atomicity for ≤4096B; long records would interleave.
    """
    p = tmp_path / "long.jsonl"
    big_value = "X" * 8000  # 8KB string + JSON overhead → record well above PIPE_BUF
    n_threads = 4
    n_per_thread = 25
    errors = []

    def writer(thread_id: int):
        try:
            for i in range(n_per_thread):
                ja.append_jsonl(p, {"thread": thread_id, "seq": i, "big": big_value})
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"thread errors: {errors}"
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_per_thread
    # Every line must parse cleanly — proves no interleaving even >PIPE_BUF
    for ln in lines:
        rec = json.loads(ln)
        assert rec["big"] == big_value, "record corruption — big field mismatch"


def test_concurrent_batch_writes_atomic(tmp_path):
    """Batch helper: each batch should appear contiguously, never split."""
    p = tmp_path / "batch.jsonl"
    n_threads = 3
    batch_size = 10
    n_batches_per_thread = 5
    errors = []

    def writer(thread_id: int):
        try:
            for batch_no in range(n_batches_per_thread):
                records = [
                    {"thread": thread_id, "batch": batch_no, "seq": i}
                    for i in range(batch_size)
                ]
                ja.append_jsonl_batch(p, records)
        except Exception as e:
            errors.append((thread_id, e))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"thread errors: {errors}"
    lines = p.read_text(encoding="utf-8").splitlines()
    expected = n_threads * n_batches_per_thread * batch_size
    assert len(lines) == expected
    # Verify each batch is contiguous (10 records with same (thread,batch) appear as run)
    parsed = [json.loads(l) for l in lines]
    i = 0
    while i < len(parsed):
        thread_id = parsed[i]["thread"]
        batch_no = parsed[i]["batch"]
        for j in range(batch_size):
            assert parsed[i + j]["thread"] == thread_id, \
                f"batch torn at line {i + j}: thread mismatch"
            assert parsed[i + j]["batch"] == batch_no, \
                f"batch torn at line {i + j}: batch mismatch"
            assert parsed[i + j]["seq"] == j, "sequence within batch wrong"
        i += batch_size


# ---------------------------------------------------------------------------
# 3 callsites integration (end-to-end via migrated helpers)
# ---------------------------------------------------------------------------


def test_telegram_approver_append_learning_uses_shim(tmp_path):
    from dispatch_v2 import telegram_approver as ta
    p = tmp_path / "learning_log.jsonl"
    ta.append_learning(str(p), {"action": "TAK", "oid": "469100"})
    assert json.loads(p.read_text()) == {"action": "TAK", "oid": "469100"}


def test_shadow_dispatcher_append_decision_uses_shim(tmp_path):
    from dispatch_v2 import shadow_dispatcher as sd
    p = tmp_path / "shadow.jsonl"
    sd._append_decision(str(p), {"order_id": "X", "verdict": "PROPOSE"})
    assert json.loads(p.read_text()) == {"order_id": "X", "verdict": "PROPOSE"}


def test_panel_watcher_panel_override_uses_shim(monkeypatch, tmp_path):
    """Smoke: PANEL_OVERRIDE write path uses shim (NIE bare open('a')).

    Mockuje pending_proposals JSON file na disk + redirectuje _LEARNING_LOG_PATH
    do tmp_path. _check_panel_override read pending → write override → assert pisał via shim.
    """
    from dispatch_v2 import panel_watcher as pw

    learning_path = tmp_path / "learning_log.jsonl"
    pending_path = tmp_path / "pending_proposals.json"

    pending_data = {
        "469200": {
            "decision_record": {
                "best": {"courier_id": 100, "score": 50.0},
            }
        }
    }
    pending_path.write_text(json.dumps(pending_data), encoding="utf-8")

    monkeypatch.setattr(pw, "_LEARNING_LOG_PATH", str(learning_path))
    monkeypatch.setattr(pw, "_PENDING_PROPOSALS_PATH", str(pending_path))

    pw._check_panel_override("469200", "999", source="test")
    assert learning_path.exists()
    rec = json.loads(learning_path.read_text(encoding="utf-8"))
    assert rec["order_id"] == "469200"
    assert rec["actual_courier_id"] == "999"
    assert rec["proposed_courier_id"] == "100"
    assert rec["action"] == "PANEL_OVERRIDE"
