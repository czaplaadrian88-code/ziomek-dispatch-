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

import ast
import builtins
import gzip
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
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


def test_regular_append_separates_truncated_predecessor(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_bytes(b'{"interrupted":')

    ja.append_jsonl(p, {"new": 1})

    lines = p.read_bytes().splitlines()
    assert lines[0] == b'{"interrupted":'
    assert json.loads(lines[1]) == {"new": 1}


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


def test_custom_serializer_options_preserve_producer_format(tmp_path):
    from datetime import datetime

    p = tmp_path / "out.jsonl"
    ja.append_jsonl(
        p,
        {"when": datetime(2026, 7, 19, 12, 30), "value": 1},
        separators=(",", ":"),
        default=str,
    )

    assert p.read_text(encoding="utf-8") == (
        '{"when":"2026-07-19 12:30:00","value":1}\n'
    )


def test_append_once_exact_identity_is_durable_and_idempotent(tmp_path, monkeypatch):
    p = tmp_path / "out.jsonl"
    real_fsync = ja.os.fsync
    fsynced = []

    def spy_fsync(fd):
        fsynced.append("dir" if os.path.isdir(f"/proc/self/fd/{fd}") else "file")
        return real_fsync(fd)

    monkeypatch.setattr(ja.os, "fsync", spy_fsync)
    record = {"lifecycle_event_id": "evt-1", "action": "PANEL_AGREE"}

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-1",
    ) is True
    assert ja.append_jsonl_once(
        p,
        {**record, "action": "PANEL_OVERRIDE"},
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-1",
    ) is False

    assert [json.loads(line) for line in p.read_text().splitlines()] == [record]
    assert "file" in fsynced
    assert "dir" in fsynced


def test_known_first_attempt_durable_append_does_not_scan_history(
    tmp_path, monkeypatch
):
    p = tmp_path / "large-learning-log.jsonl"
    p.write_text('{"legacy":true}\n', encoding="utf-8")
    monkeypatch.setattr(
        ja,
        "_fd_has_identity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first durable delivery must not scan history")
        ),
    )
    record = {"lifecycle_event_id": "evt-first", "action": "PANEL_AGREE"}

    ja.append_jsonl_durable(p, record)

    assert json.loads(p.read_text(encoding="utf-8").splitlines()[-1]) == record


def test_append_once_concurrent_same_identity_writes_one_line(tmp_path):
    p = tmp_path / "once.jsonl"

    def writer(_idx):
        return ja.append_jsonl_once(
            p,
            {"lifecycle_event_id": "evt-concurrent", "action": "PANEL_AGREE"},
            dedupe_key="lifecycle_event_id",
            dedupe_value="evt-concurrent",
        )

    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(writer(i))) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sum(results) == 1
    assert not any(thread.is_alive() for thread in threads)
    assert len(p.read_text().splitlines()) == 1


def test_append_once_separates_truncated_legacy_tail(tmp_path):
    p = tmp_path / "truncated.jsonl"
    p.write_bytes(b'{"incomplete":')
    record = {"lifecycle_event_id": "evt-after-crash", "action": "PANEL_AGREE"}

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-after-crash",
    ) is True

    lines = p.read_bytes().splitlines()
    assert lines[0] == b'{"incomplete":'
    assert json.loads(lines[1]) == record


def test_append_once_retry_finds_identity_after_logrotate_copytruncate(tmp_path):
    p = tmp_path / "learning_log.jsonl"
    rotated = tmp_path / "learning_log.jsonl.1"
    record = {"lifecycle_event_id": "evt-rotated", "action": "PANEL_AGREE"}

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-rotated",
    ) is True
    rotated.write_bytes(p.read_bytes())
    p.write_bytes(b"")

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-rotated",
        scan_rotated=True,
    ) is False
    assert p.read_bytes() == b""
    assert [json.loads(line) for line in rotated.read_text().splitlines()] == [record]


def test_append_once_retry_finds_identity_in_compressed_rotation(tmp_path):
    p = tmp_path / "learning_log.jsonl"
    rotated = tmp_path / "learning_log.jsonl.2.gz"
    record = {"lifecycle_event_id": "evt-rotated-gz", "action": "PANEL_OVERRIDE"}
    with gzip.open(rotated, "wb") as stream:
        stream.write((json.dumps(record) + "\n").encode("utf-8"))

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-rotated-gz",
        scan_rotated=True,
    ) is False
    assert p.read_bytes() == b""


def test_append_once_restarts_scan_when_rotation_moves_after_glob(
    tmp_path, monkeypatch
):
    """Zmiana .1->.2 miedzy glob/open nie moze stac sie clean miss."""
    p = tmp_path / "learning_log.jsonl"
    rotated_1 = tmp_path / "learning_log.jsonl.1"
    rotated_2 = tmp_path / "learning_log.jsonl.2"
    record = {"lifecycle_event_id": "evt-moving", "action": "PANEL_AGREE"}
    rotated_1.write_text(json.dumps(record) + "\n", encoding="utf-8")
    real_open = open
    moved = False

    def rotating_open(path, *args, **kwargs):
        nonlocal moved
        if Path(path) == rotated_1 and not moved:
            moved = True
            rotated_1.rename(rotated_2)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(ja, "open", rotating_open, raising=False)

    assert ja.append_jsonl_once(
        p,
        record,
        dedupe_key="lifecycle_event_id",
        dedupe_value="evt-moving",
        scan_rotated=True,
    ) is False
    assert moved is True
    assert p.read_bytes() == b""
    assert json.loads(rotated_2.read_text(encoding="utf-8")) == record


def test_jsonl_logrotate_uses_rename_not_copytruncate():
    """Rename keeps overlapping legacy writers linked; copytruncate can lose."""
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    config_path = deploy / "dispatch-v2-jsonl-logrotate.conf"
    text = config_path.read_text(encoding="utf-8")
    blocks = []
    prefix = []
    body = []
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if not inside and line == "{":
            inside = True
            body = []
            continue
        if inside and line == "}":
            blocks.append((tuple(prefix), tuple(body)))
            prefix = []
            body = []
            inside = False
            continue
        if inside:
            if line and not line.startswith("#"):
                body.append(line)
        elif line and not line.startswith("#"):
            prefix.append(line)

    jsonl_blocks = [
        body
        for paths, body in blocks
        if "@@DISPATCH_V2_JSONL_PATHS@@" in paths
    ]
    assert len(jsonl_blocks) == 1
    assert all("copytruncate" not in body for body in jsonl_blocks)
    assert all("create 0644 root root" in body for body in jsonl_blocks)
    assert all("daily" in body for body in jsonl_blocks)
    assert all(any(line.startswith("maxsize ") for line in body) for body in jsonl_blocks)
    global_config = (deploy / "dispatch-v2-logrotate.conf").read_text(
        encoding="utf-8"
    )
    assert not any(
        line.strip().endswith(".jsonl") for line in global_config.splitlines()
    )


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


def test_durable_batch_separates_tail_and_fsyncs_file_and_directory(
    tmp_path, monkeypatch
):
    p = tmp_path / "out.jsonl"
    p.write_bytes(b'{"interrupted":')
    real_fsync = ja.os.fsync
    fsynced = []

    def spy_fsync(fd):
        fsynced.append("dir" if os.path.isdir(f"/proc/self/fd/{fd}") else "file")
        return real_fsync(fd)

    monkeypatch.setattr(ja.os, "fsync", spy_fsync)

    assert ja.append_jsonl_batch_durable(p, ({"i": i} for i in range(2))) == 2

    lines = p.read_bytes().splitlines()
    assert lines[0] == b'{"interrupted":'
    assert [json.loads(line) for line in lines[1:]] == [{"i": 0}, {"i": 1}]
    assert "file" in fsynced
    assert "dir" in fsynced


def test_eta_calibration_writer_uses_durable_batch(tmp_path, monkeypatch):
    from dispatch_v2 import eta_calibration_logger as eta

    output = tmp_path / "eta_calibration_log.jsonl"
    monkeypatch.setattr(eta, "OUT_LOG", str(output))
    rows = [{"oid": "A", "error": 1.5}, {"oid": "B", "error": -0.5}]

    eta.append_atomic(rows)

    assert [json.loads(line) for line in output.read_text().splitlines()] == rows


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


def test_append_once_serializes_writers_across_active_inode_rotation(
    tmp_path, monkeypatch
):
    """Two writers cannot dedupe on different active/rotated inodes."""
    p = tmp_path / "out.jsonl"
    p.write_text("", encoding="utf-8")
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    gate = threading.Lock()
    paused = False
    real_scan = ja._fd_has_identity

    def pause_first_scan(fd, key, value):
        nonlocal paused
        with gate:
            first = not paused
            if first:
                paused = True
        result = real_scan(fd, key, value)
        if first:
            entered.set()
            assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(ja, "_fd_has_identity", pause_first_scan)
    results = {}

    def writer_one():
        results["one"] = ja.append_jsonl_once(
            p,
            {"lifecycle_event_id": "E"},
            dedupe_key="lifecycle_event_id",
            dedupe_value="E",
            scan_rotated=True,
        )

    def writer_two():
        results["two"] = ja.append_jsonl_once(
            p,
            {"lifecycle_event_id": "E"},
            dedupe_key="lifecycle_event_id",
            dedupe_value="E",
            scan_rotated=True,
        )
        second_done.set()

    first = threading.Thread(target=writer_one)
    first.start()
    assert entered.wait(timeout=5)
    p.rename(p.with_name("out.jsonl.1"))
    p.write_text("", encoding="utf-8")
    second = threading.Thread(target=writer_two)
    second.start()
    assert second_done.wait(timeout=0.1) is False
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert results == {"one": True, "two": False}
    records = []
    for candidate in (p.with_name("out.jsonl.1"), p):
        records.extend(
            json.loads(line)
            for line in candidate.read_text(encoding="utf-8").splitlines()
            if line
        )
    assert [row["lifecycle_event_id"] for row in records] == ["E"]


def test_rotated_scan_retries_when_numeric_path_reuses_an_inode(
    tmp_path, monkeypatch
):
    p = tmp_path / "out.jsonl"
    p.write_text("", encoding="utf-8")
    rot1 = p.with_name("out.jsonl.1")
    rot2 = p.with_name("out.jsonl.2.gz")
    rot3 = p.with_name("out.jsonl.3.gz")
    rot1.write_text('{"lifecycle_event_id":"other-1"}\n', encoding="utf-8")
    with gzip.open(rot2, "wb") as stream:
        stream.write(b'{"lifecycle_event_id":"E"}\n')
    real_match = ja._line_has_identity
    moved = False

    def rotate_during_first_candidate(raw, key, value):
        nonlocal moved
        if not moved:
            moved = True
            rot2.rename(rot3)
            with gzip.open(rot2, "wb") as stream:
                stream.write(b'{"lifecycle_event_id":"other-2"}\n')
        return real_match(raw, key, value)

    monkeypatch.setattr(ja, "_line_has_identity", rotate_during_first_candidate)
    appended = ja.append_jsonl_once(
        p,
        {"lifecycle_event_id": "E"},
        dedupe_key="lifecycle_event_id",
        dedupe_value="E",
        scan_rotated=True,
    )

    assert appended is False
    assert p.read_text(encoding="utf-8") == ""
    with gzip.open(rot3, "rb") as stream:
        assert json.loads(stream.read())["lifecycle_event_id"] == "E"


def test_truncated_gzip_match_is_not_accepted_before_crc_footer(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text("", encoding="utf-8")
    rotated = p.with_name("out.jsonl.1.gz")
    with gzip.open(rotated, "wb") as stream:
        stream.write(b'{"lifecycle_event_id":"E"}\n')
    payload = rotated.read_bytes()
    rotated.write_bytes(payload[:-8])  # remove CRC32 + ISIZE trailer

    with pytest.raises(OSError, match="rotated dedupe namespace"):
        ja.append_jsonl_once(
            p,
            {"lifecycle_event_id": "E"},
            dedupe_key="lifecycle_event_id",
            dedupe_value="E",
            scan_rotated=True,
        )
    assert p.read_text(encoding="utf-8") == ""


def test_canonical_logrotate_wrapper_holds_writer_namespace_lock(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    p = tmp_path / "out.jsonl"
    completed = threading.Event()

    def writer():
        ja.append_jsonl(p, {"event": "after-rotation-lock"})
        completed.set()

    with jr.hold_jsonl_rotation_locks((p,)):
        thread = threading.Thread(target=writer)
        thread.start()
        assert completed.wait(timeout=0.2) is False
    thread.join(timeout=5)

    assert completed.is_set()
    assert json.loads(p.read_text(encoding="utf-8"))["event"] == "after-rotation-lock"


def test_logrotate_wrapper_defers_while_legacy_data_inode_is_open(
    tmp_path, monkeypatch
):
    from dispatch_v2.core import jsonl_rotation as jr

    p = tmp_path / "out.jsonl"
    p.write_text('{"event":"before"}\n', encoding="utf-8")
    called = []
    monkeypatch.setattr(
        jr.subprocess,
        "run",
        lambda *_args, **_kwargs: called.append(True),
    )

    with p.open("a", encoding="utf-8"):
        with pytest.raises(jr.OpenJsonlInodeError, match="still open"):
            monkeypatch.setattr(
                jr,
                "resolve_jsonl_paths",
                lambda _config=None, **_kwargs: (str(p),),
            )
            jr.run_logrotate(str(tmp_path / "logrotate.conf"))

    assert called == []


def test_legacy_writer_opening_after_gate_stays_linked_by_rename(
    tmp_path, monkeypatch
):
    """TOCTOU after /proc scan is safe because rotation never truncates."""
    from dispatch_v2.core import jsonl_rotation as jr

    p = tmp_path / "out.jsonl"
    p.write_text('{"event":"before"}\n', encoding="utf-8")
    rotated = p.with_name("out.jsonl.1")

    class Completed:
        returncode = 0

    def rename_while_legacy_fd_is_open(*_args, **_kwargs):
        with p.open("a", encoding="utf-8") as legacy:
            p.rename(rotated)
            p.touch()
            legacy.write('{"event":"late-legacy"}\n')
            legacy.flush()
            os.fsync(legacy.fileno())
        return Completed()

    monkeypatch.setattr(jr.subprocess, "run", rename_while_legacy_fd_is_open)
    monkeypatch.setattr(
        jr,
        "resolve_jsonl_paths",
        lambda _config=None, **_kwargs: (str(p),),
    )
    policy = tmp_path / "policy.conf"
    policy.write_text(
        "@@DISPATCH_V2_JSONL_PATHS@@\n"
        "{\n"
        "    daily\n"
        "    rotate 30\n"
        "    missingok\n"
        "    create 0644 root root\n"
        "}\n",
        encoding="utf-8",
    )

    assert jr.run_logrotate(str(policy)) == 0
    assert [
        json.loads(line)["event"]
        for line in rotated.read_text(encoding="utf-8").splitlines()
    ] == ["before", "late-legacy"]
    assert p.read_text(encoding="utf-8") == ""


def test_logrotate_wrapper_manifest_matches_every_jsonl_config_path():
    from dispatch_v2.core import jsonl_rotation as jr

    deploy = Path(__file__).resolve().parents[1] / "deploy"
    config_path = deploy / "dispatch-v2-jsonl-logrotate.conf"
    template = config_path.read_text(encoding="utf-8")
    assert template.count(jr.JSONL_PATHS_MARKER) == 1
    assert not any(
        line.strip().startswith("/") and line.strip().endswith(".jsonl")
        for line in template.splitlines()
    )
    resolved_paths = jr.resolve_jsonl_paths()
    rendered = jr.render_jsonl_logrotate_config(template, resolved_paths)
    configured = {
        json.loads(line.strip())
        for line in rendered.splitlines()
        if line.strip().startswith('"') and line.strip().endswith('"')
    }
    assert configured == set(resolved_paths)
    service = (deploy / "dispatch-v2-jsonl-logrotate.service").read_text(
        encoding="utf-8"
    )
    timer = (deploy / "dispatch-v2-jsonl-logrotate.timer").read_text(
        encoding="utf-8"
    )
    assert "dispatch_v2.core.jsonl_rotation" in service
    assert "/etc/logrotate-dispatch-v2-jsonl.conf" in service
    assert "OnCalendar=" in timer


def test_logrotate_operation_uses_exact_late_bound_manifest(
    tmp_path,
    monkeypatch,
):
    """Writer, lock, atestacja i realny config mają jeden manifest."""
    from dispatch_v2.core import jsonl_rotation as jr

    deploy = Path(__file__).resolve().parents[1] / "deploy"
    policy = tmp_path / "dispatch-v2-jsonl-logrotate.conf"
    policy.write_text(
        (deploy / "dispatch-v2-jsonl-logrotate.conf").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    writer_log = tmp_path / "alternate-writer" / "shadow.jsonl"
    config = {"paths": {"shadow_log": str(writer_log)}}
    expected = tuple(sorted(jr.resolve_jsonl_paths(config)))
    captured = {}

    @contextmanager
    def capture_locks(paths):
        captured["locked"] = tuple(paths)
        yield

    def capture_attestation(paths, *, proc_root):
        captured["attested"] = tuple(paths)
        captured["proc_root"] = proc_root

    class Completed:
        returncode = 0

    def capture_run(command, *, check, pass_fds):
        assert check is False
        assert command[:-1] == [
            "/usr/sbin/logrotate",
            "--state",
            str(tmp_path / "logrotate.status"),
        ]
        generated = Path(command[-1])
        assert generated != policy
        assert str(generated).startswith("/proc/self/fd/")
        assert pass_fds == (int(generated.name),)
        rendered = generated.read_text(encoding="utf-8")
        captured["rotated"] = tuple(
            sorted(
                json.loads(line.strip())
                for line in rendered.splitlines()
                if line.strip().startswith('"')
                and line.strip().endswith('"')
            )
        )
        captured["generated"] = generated
        return Completed()

    monkeypatch.setattr(jr, "hold_jsonl_rotation_locks", capture_locks)
    monkeypatch.setattr(jr, "assert_no_open_jsonl_inodes", capture_attestation)
    monkeypatch.setattr(jr.subprocess, "run", capture_run)

    assert jr.run_logrotate(
        str(policy),
        config=config,
        state_path=tmp_path / "logrotate.status",
        proc_root=tmp_path / "proc",
    ) == 0
    assert captured["locked"] == expected
    assert captured["attested"] == expected
    assert captured["rotated"] == expected
    assert captured["proc_root"] == tmp_path / "proc"
    assert writer_log.as_posix() in captured["rotated"]
    assert not captured["generated"].exists()


def test_logrotate_policy_without_exact_marker_fails_closed(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    policy = tmp_path / "invalid.conf"
    policy.write_text(
        '"/stale/owner.jsonl"\n{\n    daily\n}\n',
        encoding="utf-8",
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="exactly one"):
        jr.render_jsonl_logrotate_config(
            policy.read_text(encoding="utf-8"),
            (tmp_path / "current.jsonl",),
        )


def test_logrotate_policy_with_second_static_owner_fails_closed(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    policy = (
        jr.JSONL_PATHS_MARKER
        + '\n"/stale/unlocked.jsonl"\n'
        "{\n    daily\n}\n"
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="marker-owned"):
        jr.render_jsonl_logrotate_config(
            policy,
            (tmp_path / "current.jsonl",),
        )


def test_logrotate_policy_comment_braces_cannot_hide_second_block(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    policy = (
        jr.JSONL_PATHS_MARKER
        + "\n{\n    daily\n"
        "} # close marker block\n"
        '"/stale/unlocked.jsonl" { # hidden second block\n'
        "    daily\n}\n"
    )
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="inline comments|marker-owned",
    ):
        jr.render_jsonl_logrotate_config(
            policy,
            (tmp_path / "current.jsonl",),
        )


def test_logrotate_policy_rejects_inline_comment_bytes(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    policy = (
        jr.JSONL_PATHS_MARKER
        + "\n{\n"
        "    daily # bytes not covered by the policy whitelist\n"
        "}\n"
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="inline comments"):
        jr.render_jsonl_logrotate_config(
            policy,
            (tmp_path / "current.jsonl",),
        )


def test_logrotate_policy_rejects_include_directive(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    policy = (
        jr.JSONL_PATHS_MARKER
        + "\n{\n"
        "    daily\n"
        "    include /stale/unlocked.conf\n"
        "}\n"
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="unsafe"):
        jr.render_jsonl_logrotate_config(
            policy,
            (tmp_path / "current.jsonl",),
        )


def test_logrotate_manifest_rejects_filename_patterns(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    deploy = Path(__file__).resolve().parents[1] / "deploy"
    policy = (deploy / "dispatch-v2-jsonl-logrotate.conf").read_text(
        encoding="utf-8"
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="patterns"):
        jr.render_jsonl_logrotate_config(
            policy,
            (tmp_path / "events[12].jsonl",),
        )


def test_jsonl_contract_rejects_symlink_before_write_and_rotation(tmp_path):
    from dispatch_v2 import common as C
    from dispatch_v2.core import jsonl_rotation as jr

    target = tmp_path / "physical.jsonl"
    target.write_text("", encoding="utf-8")
    alias = tmp_path / "alias.jsonl"
    alias.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        C.validate_jsonl_path(alias)
    with pytest.raises(jr.JsonlRotationConfigError, match="symlink"):
        jr._normalize_jsonl_paths((alias,))


def test_jsonl_contract_checks_final_component_after_parent_resolution(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2 import common as C

    path = tmp_path / "events.jsonl"
    victim = tmp_path / "victim.jsonl"
    path.write_text("", encoding="utf-8")
    victim.write_text("", encoding="utf-8")
    real_resolve = Path.resolve
    injected = False

    def inject_final_symlink(self, strict=False):
        nonlocal injected
        if self == path and not injected:
            path.unlink()
            path.symlink_to(victim)
            injected = True
            return real_resolve(self, strict=strict)
        resolved = real_resolve(self, strict=strict)
        if self == path.parent and not injected:
            path.unlink()
            path.symlink_to(victim)
            injected = True
        return resolved

    monkeypatch.setattr(Path, "resolve", inject_final_symlink)
    with pytest.raises(ValueError, match="symlink"):
        C.validate_jsonl_path(path)
    assert injected


def test_jsonl_contract_preserves_symlink_parent_dotdot_semantics(tmp_path):
    from dispatch_v2 import common as C

    target = tmp_path / "target"
    nested = target / "nested"
    nested.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(nested, target_is_directory=True)
    raw_path = link / ".." / "events.jsonl"

    assert C.validate_jsonl_path(raw_path) == target / "events.jsonl"
    assert C.validate_jsonl_path(raw_path) == raw_path.resolve(strict=False)
    assert C.validate_jsonl_path(raw_path) != tmp_path / "events.jsonl"


def test_jsonl_appender_rejects_symlink_and_hardlink_at_open_time(tmp_path):
    from dispatch_v2.core.jsonl_appender import append_jsonl
    from dispatch_v2.core import jsonl_rotation as jr

    target = tmp_path / "physical.jsonl"
    target.write_text("", encoding="utf-8")
    symlink = tmp_path / "symlink.jsonl"
    symlink.symlink_to(target)
    with pytest.raises(jr.JsonlRotationConfigError, match="symlink"):
        append_jsonl(symlink, {"event": "must-not-follow"})

    hardlink = tmp_path / "hardlink.jsonl"
    os.link(target, hardlink)
    with pytest.raises(jr.JsonlRotationConfigError, match="hard link"):
        append_jsonl(target, {"event": "must-not-split"})
    assert target.read_text(encoding="utf-8") == ""


def test_managed_symlink_is_rejected_before_registry_publish(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "decision_eta_log.jsonl"
    target.write_text("", encoding="utf-8")
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    alias = alias_dir / "decision_eta_log.jsonl"
    alias.symlink_to(target)
    published = []
    monkeypatch.setattr(
        jr,
        "register_jsonl_writer_path",
        lambda path: published.append(Path(path)) or Path(path),
    )

    with pytest.raises(jr.JsonlRotationConfigError, match="symlink"):
        jr.register_managed_jsonl_writer_path(alias)
    assert published == []


def test_locked_namespace_yields_the_registered_canonical_path(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir()
    canonical = canonical_dir / "decision_eta_log.jsonl"
    lexical = tmp_path / "lexical" / "decision_eta_log.jsonl"
    monkeypatch.setattr(
        jr,
        "register_managed_jsonl_writer_path",
        lambda _path: canonical,
    )

    with ja._locked_namespace(lexical) as locked_path:
        assert locked_path == canonical


def test_jsonl_appender_rechecks_hardlink_count_after_path_lookup(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    target = tmp_path / "events.jsonl"
    alias = tmp_path / "late-hardlink.jsonl"
    target.write_text("", encoding="utf-8")
    real_stat = os.stat
    injected = False

    def inject_hardlink(path, *, dir_fd=None, follow_symlinks=True):
        nonlocal injected
        if not follow_symlinks and Path(path) == target and not injected:
            os.link(target, alias)
            injected = True
        return real_stat(
            path,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(ja.os, "stat", inject_hardlink)
    with pytest.raises(jr.JsonlRotationConfigError, match="hard link"):
        ja.append_jsonl(target, {"event": "must-not-write-after-race"})
    assert injected
    assert target.read_text(encoding="utf-8") == ""


def test_jsonl_registry_lock_rejects_symlink_without_touching_target(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("do-not-touch", encoding="utf-8")
    foreign.chmod(0o644)
    lock_path = registry.with_name(registry.name + ".lock")
    lock_path.symlink_to(foreign)

    with pytest.raises(OSError):
        jr.register_jsonl_writer_path(
            tmp_path / "events.jsonl",
            registry_path=registry,
        )
    assert foreign.stat().st_mode & 0o777 == 0o644
    assert foreign.read_text(encoding="utf-8") == "do-not-touch"


def test_attested_lock_rechecks_path_after_flock(tmp_path, monkeypatch):
    lock_path = tmp_path / "events.jsonl.append.lock"
    displaced = tmp_path / "displaced.lock"
    lock_path.write_text("", encoding="utf-8")
    real_flock = ja.fcntl.flock
    injected = False

    def replace_before_first_flock(fd, operation):
        nonlocal injected
        if operation == ja.fcntl.LOCK_EX and not injected:
            lock_path.rename(displaced)
            lock_path.write_text("", encoding="utf-8")
            injected = True
        return real_flock(fd, operation)

    monkeypatch.setattr(ja.fcntl, "flock", replace_before_first_flock)
    fd = ja.open_attested_regular_file(
        lock_path,
        os.O_RDWR,
        label="test namespace lock",
        exclusive_lock=True,
    )
    try:
        assert injected
        assert os.path.samefile(lock_path, f"/proc/self/fd/{fd}")
        assert not os.path.samefile(displaced, f"/proc/self/fd/{fd}")
    finally:
        real_flock(fd, ja.fcntl.LOCK_UN)
        os.close(fd)


def test_registry_reader_retries_cooperative_atomic_replace(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    old_path = tmp_path / "old" / "events.jsonl"
    new_path = tmp_path / "new" / "events.jsonl"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(old_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(new_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    initial_identity = (registry.stat().st_dev, registry.stat().st_ino)
    real_fstat = ja.os.fstat
    swapped = False

    def replace_after_first_fd_snapshot(fd):
        nonlocal swapped
        metadata = real_fstat(fd)
        if (
            (metadata.st_dev, metadata.st_ino) == initial_identity
            and not swapped
        ):
            os.replace(replacement, registry)
            swapped = True
        return metadata

    monkeypatch.setattr(ja.os, "fstat", replace_after_first_fd_snapshot)
    assert jr.registered_jsonl_paths(registry) == (str(new_path),)
    assert swapped


def test_registry_reader_retries_replace_before_first_fd_snapshot(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    old_path = tmp_path / "old" / "events.jsonl"
    new_path = tmp_path / "new" / "events.jsonl"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(old_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(new_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    real_fstat = ja.os.fstat
    swapped = False

    def replace_before_first_fd_snapshot(fd):
        nonlocal swapped
        if not swapped:
            os.replace(replacement, registry)
            swapped = True
        return real_fstat(fd)

    monkeypatch.setattr(ja.os, "fstat", replace_before_first_fd_snapshot)
    assert jr.registered_jsonl_paths(registry) == (str(new_path),)
    assert swapped


def test_registry_reader_retries_replace_before_second_fd_snapshot(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    old_path = tmp_path / "old" / "events.jsonl"
    new_path = tmp_path / "new" / "events.jsonl"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(old_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement.json"
    replacement.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "writers": [{"path": str(new_path), "role": "static"}],
            }
        ),
        encoding="utf-8",
    )
    real_fstat = ja.os.fstat
    snapshots = 0
    swapped = False

    def replace_before_second_fd_snapshot(fd):
        nonlocal snapshots, swapped
        snapshots += 1
        if snapshots == 2:
            os.replace(replacement, registry)
            swapped = True
        return real_fstat(fd)

    monkeypatch.setattr(ja.os, "fstat", replace_before_second_fd_snapshot)
    assert jr.registered_jsonl_paths(registry) == (str(new_path),)
    assert swapped


def test_run_logrotate_freezes_registry_before_manifest_snapshot(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    registry_lock = registry.with_name(registry.name + ".lock")
    data_path = tmp_path / "events.jsonl"
    data_path.write_text("", encoding="utf-8")
    template = tmp_path / "logrotate.conf"
    template.write_text(
        f"{jr.JSONL_PATHS_MARKER}\n{{\n  daily\n  rotate 1\n  missingok\n}}\n",
        encoding="utf-8",
    )
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    observed_locked_registry = False

    def resolve_while_registry_must_be_frozen(
        _config=None,
        *,
        registry_path=jr.JSONL_PATH_REGISTRY,
        **_kwargs,
    ):
        nonlocal observed_locked_registry
        assert Path(registry_path) == registry
        contender = os.open(registry_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                ja.fcntl.flock(
                    contender,
                    ja.fcntl.LOCK_EX | ja.fcntl.LOCK_NB,
                )
            observed_locked_registry = True
        finally:
            os.close(contender)
        return (str(data_path),)

    monkeypatch.setattr(
        jr,
        "resolve_jsonl_paths",
        resolve_while_registry_must_be_frozen,
    )
    assert (
        jr.run_logrotate(
            str(template),
            logrotate_bin="/bin/true",
            registry_path=registry,
            proc_root=proc_root,
        )
        == 0
    )
    assert observed_locked_registry


def test_jsonl_rotation_rejects_lexical_aliases_of_one_inode(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    double_slash = "/" + str(path)
    assert os.path.samefile(path, double_slash)
    assert jr._normalize_jsonl_paths((path, double_slash)) == (str(path),)


def test_jsonl_rotation_rejects_single_multiply_linked_file(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    os.link(path, tmp_path / "second-name.jsonl")
    with pytest.raises(jr.JsonlRotationConfigError, match="hard link"):
        jr._normalize_jsonl_paths((path,))


def test_jsonl_manifest_rejects_ancestor_descendant_data_paths(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    outer = tmp_path / "events.jsonl"
    inner = outer / "child.jsonl"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="ancestor|nested",
    ):
        jr._normalize_jsonl_paths((outer, inner))
    assert not outer.exists()


def test_jsonl_manifest_rejects_numeric_rotation_as_second_data_path(
    tmp_path,
):
    from dispatch_v2.core import jsonl_rotation as jr

    active = tmp_path / "events.jsonl"
    rotated = tmp_path / "events.jsonl.1.gz"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="rotation namespace",
    ):
        jr._normalize_jsonl_paths((active, rotated))


def test_jsonl_manifest_rejects_data_nested_below_another_writer_lock(
    tmp_path,
):
    from dispatch_v2.core import jsonl_rotation as jr

    active = tmp_path / "events.jsonl"
    nested = tmp_path / "events.jsonl.append.lock" / "child.jsonl"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="namespace lock",
    ):
        jr._normalize_jsonl_paths((active, nested))
    assert not active.with_name(active.name + ".append.lock").exists()


def test_jsonl_writer_must_not_share_registry_namespace(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "jsonl_rotation_paths.json"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="registry namespace",
    ):
        jr.register_jsonl_writer_path(
            registry,
            registry_path=registry,
        )
    assert not registry.exists()

    config = {"paths": {"shadow_log": str(registry)}}
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="registry namespace",
    ):
        jr.resolve_jsonl_paths(config, registry_path=registry)

    active = tmp_path / "events.jsonl"
    rotated_registry = tmp_path / "events.jsonl.1"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="registry namespace",
    ):
        jr.register_jsonl_writer_path(
            active,
            registry_path=rotated_registry,
        )


def test_registry_rejects_new_writer_colliding_with_registered_lock(
    tmp_path,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "jsonl_rotation_paths.json"
    active = tmp_path / "events.jsonl"
    colliding = tmp_path / "events.jsonl.append.lock"
    jr.register_jsonl_writer_path(active, registry_path=registry)
    original = registry.read_bytes()

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="namespace lock",
    ):
        jr.register_jsonl_writer_path(
            colliding,
            registry_path=registry,
        )
    assert registry.read_bytes() == original
    assert jr.registered_jsonl_paths(registry) == (str(active),)


def test_registry_rejects_dynamic_writer_colliding_with_static_manifest(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    static_path = tmp_path / "learning_log.jsonl"
    dynamic_path = tmp_path / "learning_log.jsonl.append.lock"
    monkeypatch.setattr(
        jr,
        "static_managed_jsonl_paths",
        lambda: (str(static_path),),
    )
    config = {"paths": {"shadow_log": str(dynamic_path)}}

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="namespace lock",
    ):
        jr.register_shadow_decisions_writer_path(
            config,
            registry_path=registry,
        )
    assert not registry.exists()


def test_shadow_registration_rejects_exact_static_writer_path(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    static_path = tmp_path / "learning_log.jsonl"
    monkeypatch.setattr(
        jr,
        "static_managed_jsonl_paths",
        lambda: (str(static_path),),
    )
    config = {"paths": {"shadow_log": str(static_path)}}

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="shadow_log overlaps static managed writer",
    ):
        jr.register_shadow_decisions_writer_path(
            config,
            registry_path=registry,
        )
    assert not registry.exists()


def test_registry_role_history_rejects_static_path_reused_as_shadow_after_root_change(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    historic_static = tmp_path / "old-state" / "learning_log.jsonl"
    current_static = tmp_path / "new-state" / "learning_log.jsonl"
    monkeypatch.setattr(
        jr,
        "static_managed_jsonl_paths",
        lambda: (str(historic_static),),
    )
    jr.register_jsonl_writer_path(historic_static, registry_path=registry)

    monkeypatch.setattr(
        jr,
        "static_managed_jsonl_paths",
        lambda: (str(current_static),),
    )
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="historic.*role|role.*static.*shadow",
    ):
        jr.register_shadow_decisions_writer_path(
            {"paths": {"shadow_log": str(historic_static)}},
            registry_path=registry,
        )


def test_shadow_writer_with_static_basename_keeps_shadow_role_on_append(
    tmp_path,
) -> None:
    registry = tmp_path / "control" / "registry.json"
    state_dir = tmp_path / "state"
    shadow_path = tmp_path / "separate-shadow" / "learning_log.jsonl"
    env = os.environ.copy()
    env["DISPATCH_STATE_DIR"] = str(state_dir)
    env["DISPATCH_JSONL_ROTATION_REGISTRY"] = str(registry)
    code = (
        "from dispatch_v2.core.jsonl_appender import append_jsonl\n"
        "from dispatch_v2.core.jsonl_rotation import "
        "register_shadow_jsonl_writer_path, registered_jsonl_writer_roles\n"
        f"path = {str(shadow_path)!r}\n"
        "register_shadow_jsonl_writer_path(path)\n"
        "append_jsonl(path, {'event': 'shadow-role-must-survive'})\n"
        "assert registered_jsonl_writer_roles() == ((path, 'shadow'),)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


def test_roleless_v1_registry_fails_closed_instead_of_guessing_role(
    tmp_path,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paths": [str(tmp_path / "historic.jsonl")],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="schema"):
        jr.registered_jsonl_writer_roles(registry)


def test_registry_rejects_physical_alias_of_registry_namespace(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "mount-a" / "registry.json"
    writer = tmp_path / "mount-b" / "registry.json"
    registry.parent.mkdir()
    writer.parent.mkdir()
    real_signature = getattr(jr, "_physical_namespace_signature", None)

    def aliased_signature(path):
        if Path(path) in (registry, writer):
            return ((123, 456), ("registry.json",))
        assert real_signature is not None
        return real_signature(path)

    monkeypatch.setattr(
        jr,
        "_physical_namespace_signature",
        aliased_signature,
        raising=False,
    )
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="physical namespace alias",
    ):
        jr.register_jsonl_writer_path(writer, registry_path=registry)
    assert not registry.exists()


def test_manifest_rejects_missing_paths_through_physical_parent_alias(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    first = tmp_path / "mount-a" / "events.jsonl"
    second = tmp_path / "mount-b" / "events.jsonl"
    first.parent.mkdir()
    second.parent.mkdir()
    real_signature = getattr(jr, "_physical_namespace_signature", None)

    def aliased_signature(path):
        if Path(path) in (first, second):
            return ((123, 456), ("events.jsonl",))
        assert real_signature is not None
        return real_signature(path)

    monkeypatch.setattr(
        jr,
        "_physical_namespace_signature",
        aliased_signature,
        raising=False,
    )
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="physical namespace alias",
    ):
        jr._normalize_jsonl_paths((first, second))


def test_manifest_rejects_physical_alias_between_writer_locks(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first_lock = tmp_path / "a.jsonl.append.lock"
    second_lock = tmp_path / "b.jsonl.append.lock"
    real_signature = jr._physical_namespace_signature

    def aliased_signature(path):
        if Path(path) in (first_lock, second_lock):
            return ((123, 456), ())
        return real_signature(path)

    monkeypatch.setattr(jr, "_physical_namespace_signature", aliased_signature)
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="namespace lock",
    ):
        jr._normalize_jsonl_paths((first, second))


def test_manifest_rejects_physical_alias_between_data_and_own_lock(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    data_path = tmp_path / "events.jsonl"
    data_lock = tmp_path / "events.jsonl.append.lock"
    real_signature = jr._physical_namespace_signature

    def aliased_signature(path):
        if Path(path) in (data_path, data_lock):
            return ((123, 456), ())
        return real_signature(path)

    monkeypatch.setattr(jr, "_physical_namespace_signature", aliased_signature)
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="own namespace lock",
    ):
        jr._normalize_jsonl_paths((data_path,))


def test_dynamic_shadow_log_must_not_reuse_static_writer_path(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    static_path = jr.static_managed_jsonl_paths()[0]
    config = {"paths": {"shadow_log": static_path}}
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="shadow_log overlaps static managed writer",
    ):
        jr.resolve_jsonl_paths(
            config,
            registry_path=tmp_path / "registry.json",
        )


@pytest.mark.parametrize("collision", ("registry", "data", "config"))
def test_logrotate_state_path_must_be_disjoint(
    tmp_path,
    collision,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "registry.json"
    data_path = tmp_path / "events.jsonl"
    template = tmp_path / "logrotate.conf"
    template.write_text(
        f"{jr.JSONL_PATHS_MARKER}\n{{\n  daily\n  rotate 1\n  missingok\n}}\n",
        encoding="utf-8",
    )
    state_path = {
        "registry": registry,
        "data": data_path,
        "config": template,
    }[collision]
    config = {"paths": {"shadow_log": str(data_path)}}
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="state",
    ):
        jr.run_logrotate(
            str(template),
            logrotate_bin="/bin/true",
            state_path=state_path,
            config=config,
            registry_path=registry,
            proc_root=proc_root,
        )


def test_logrotate_config_must_not_occupy_data_rotation_namespace(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    data_path = tmp_path / "events.jsonl"
    template = tmp_path / "events.jsonl.1"
    template.write_text(
        f"{jr.JSONL_PATHS_MARKER}\n{{\n  daily\n  rotate 1\n  missingok\n}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
    config = {"paths": {"shadow_log": str(data_path)}}
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="config.*rotation namespace",
    ):
        jr.run_logrotate(
            str(template),
            logrotate_bin="/bin/true",
            config=config,
            registry_path=tmp_path / "registry.json",
            proc_root=proc_root,
        )


def test_writer_registration_reserves_logrotate_control_paths_before_first_append(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    controls = {
        "config": tmp_path / "control" / "policy.conf",
        "state": tmp_path / "control" / "logrotate.status",
    }
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_CONFIG_PATH",
        controls["config"],
        raising=False,
    )
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_STATE_PATH",
        controls["state"],
        raising=False,
    )
    for role, control_path in controls.items():
        registry = tmp_path / f"{role}-registry.json"
        with pytest.raises(
            jr.JsonlRotationConfigError,
            match="control namespace",
        ):
            jr.register_jsonl_writer_path(
                control_path,
                registry_path=registry,
            )
        assert not registry.exists()


@pytest.mark.parametrize(
    "sidecar_name",
    (
        "logrotate.status.tmp",
        ".registry.json.synthetic.tmp",
    ),
)
def test_writer_registration_reserves_control_sidecar_namespaces(
    tmp_path,
    monkeypatch,
    sidecar_name,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "control" / "registry.json"
    config = tmp_path / "control" / "policy.conf"
    state = tmp_path / "control" / "logrotate.status"
    data_path = tmp_path / "control" / sidecar_name
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())

    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="control|state|registry|namespace",
    ):
        jr.register_jsonl_writer_path(
            data_path,
            registry_path=registry,
            config_path=config,
            state_path=state,
        )
    assert not registry.exists()
    assert not data_path.exists()


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_writer_registration_attests_existing_state_sidecar(
    tmp_path,
    monkeypatch,
    alias_kind,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "control" / "registry.json"
    config = tmp_path / "control" / "policy.conf"
    state = tmp_path / "control" / "logrotate.status"
    sidecar = state.with_name(state.name + ".tmp")
    target = tmp_path / "must-not-be-touched"
    sidecar.parent.mkdir(parents=True)
    target.write_text("sentinel", encoding="utf-8")
    if alias_kind == "symlink":
        sidecar.symlink_to(target)
        match = "symlink"
    else:
        os.link(target, sidecar)
        match = "hard link"
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())

    with pytest.raises(jr.JsonlRotationConfigError, match=match):
        jr.register_jsonl_writer_path(
            tmp_path / "data" / "events.jsonl",
            registry_path=registry,
            config_path=config,
            state_path=state,
        )
    assert target.read_text(encoding="utf-8") == "sentinel"
    assert not registry.exists()


def test_run_logrotate_always_passes_explicit_validated_state(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    template = tmp_path / "policy.conf"
    template.write_text(
        f"{jr.JSONL_PATHS_MARKER}\n{{\n  daily\n  rotate 1\n  missingok\n}}\n",
        encoding="utf-8",
    )
    state = tmp_path / "control" / "logrotate.status"
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_STATE_PATH",
        state,
        raising=False,
    )
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    captured = []

    class Completed:
        returncode = 0

    def capture_run(command, *, check, pass_fds):
        captured.append((command, check, pass_fds))
        return Completed()

    monkeypatch.setattr(jr.subprocess, "run", capture_run)
    assert (
        jr.run_logrotate(
            str(template),
            logrotate_bin="/usr/sbin/logrotate",
            state_path=None,
            config={"paths": {"shadow_log": str(tmp_path / "shadow.jsonl")}},
            registry_path=tmp_path / "registry.json",
            proc_root=proc_root,
        )
        == 0
    )
    assert captured
    assert captured[0][0][1:3] == ["--state", str(state)]


def test_run_logrotate_rejects_nested_registry_before_mkdir(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    data_path = tmp_path / "events.jsonl"
    nested_registry = data_path / "registry.json"
    config = {"paths": {"shadow_log": str(data_path)}}
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="registry namespace",
    ):
        jr.run_logrotate(
            str(tmp_path / "policy.conf"),
            config=config,
            registry_path=nested_registry,
            proc_root=tmp_path / "proc",
        )
    assert not data_path.exists()


def test_logrotate_manifest_rejects_relative_writer_before_rotation(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2 import common as C
    from dispatch_v2.core import jsonl_rotation as jr

    monkeypatch.chdir(tmp_path)
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="JSONL path must be absolute",
    ):
        ja.append_jsonl(
            "decision_eta_log.jsonl",
            {"event": "relative-managed-path-must-not-bypass-registry"},
        )

    config = {"paths": {"shadow_log": "relative.jsonl"}}
    with pytest.raises(ValueError, match="absolute"):
        C.resolve_shadow_decisions_writer_path(config)
    for unsafe in (
        "/var/log/shadow*.jsonl",
        "/var/log/shadow[1].jsonl",
        "/var/log/shadow~",
        "/var/log/shadow\\name.jsonl",
        '/var/log/shadow"name.jsonl',
        "/var/log/shadow#name.jsonl",
    ):
        with pytest.raises(ValueError, match="patterns"):
            C.resolve_shadow_decisions_writer_path(
                {"paths": {"shadow_log": unsafe}}
            )

    called = []
    monkeypatch.setattr(
        jr,
        "resolve_jsonl_paths",
        lambda _config, **_kwargs: ("relative.jsonl",),
    )
    monkeypatch.setattr(
        jr.subprocess,
        "run",
        lambda *_args, **_kwargs: called.append(True),
    )
    with pytest.raises(jr.JsonlRotationConfigError, match="absolute"):
        jr.run_logrotate(
            str(tmp_path / "policy.conf"),
            config=config,
            proc_root=tmp_path / "proc",
        )
    assert called == []


def test_unmanaged_appender_still_enforces_canonical_jsonl_path(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    monkeypatch.chdir(tmp_path)
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="absolute",
    ):
        ja.append_jsonl(
            "unmanaged.jsonl",
            {"event": "relative-unmanaged-must-not-bypass-contract"},
        )
    assert not (tmp_path / "unmanaged.jsonl").exists()

    unsafe = tmp_path / "unmanaged#unsafe.jsonl"
    with pytest.raises(
        jr.JsonlRotationConfigError,
        match="patterns",
    ):
        ja.append_jsonl(
            unsafe,
            {"event": "unsafe-unmanaged-must-not-bypass-contract"},
        )
    assert not unsafe.exists()


def test_unmanaged_appender_cannot_occupy_reserved_rotation_namespaces(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "control" / "registry.json"
    config = tmp_path / "control" / "policy.conf"
    state = tmp_path / "control" / "logrotate.status"
    static_path = tmp_path / "data" / "managed.jsonl"
    monkeypatch.setattr(jr, "JSONL_PATH_REGISTRY", registry)
    monkeypatch.setattr(jr, "JSONL_LOGROTATE_CONFIG_PATH", config)
    monkeypatch.setattr(jr, "JSONL_LOGROTATE_STATE_PATH", state)
    monkeypatch.setattr(
        jr,
        "static_managed_jsonl_paths",
        lambda: (str(static_path),),
    )

    reserved = (
        registry,
        registry.with_name(registry.name + ".lock"),
        config,
        state,
        static_path.with_name(static_path.name + ".append.lock"),
        static_path.with_name(static_path.name + ".1"),
        static_path.with_name(static_path.name + ".1.gz"),
        static_path / "nested.jsonl",
    )
    for path in reserved:
        with pytest.raises(
            jr.JsonlRotationConfigError,
            match="namespace|rotation|nested",
        ):
            ja.append_jsonl(
                path,
                {"event": "unmanaged-must-not-occupy-reserved-namespace"},
            )
        assert not path.exists()
        assert not path.with_name(path.name + ".append.lock").exists()


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_unmanaged_namespace_cache_revalidates_data_path(
    tmp_path,
    monkeypatch,
    alias_kind,
):
    from dispatch_v2.core import jsonl_rotation as jr

    candidate = tmp_path / "data" / "unmanaged.jsonl"
    target = tmp_path / "target.jsonl"
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
    monkeypatch.setattr(
        jr,
        "JSONL_PATH_REGISTRY",
        tmp_path / "control" / "registry.json",
    )
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_CONFIG_PATH",
        tmp_path / "control" / "policy.conf",
    )
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_STATE_PATH",
        tmp_path / "control" / "logrotate.status",
    )
    jr._attest_unmanaged_writer_namespace.cache_clear()
    try:
        assert jr.register_managed_jsonl_writer_path(candidate) == candidate
        candidate.parent.mkdir(parents=True)
        target.write_text("sentinel", encoding="utf-8")
        if alias_kind == "symlink":
            candidate.symlink_to(target)
            match = "symlink"
        else:
            os.link(target, candidate)
            match = "hard link"
        with pytest.raises(jr.JsonlRotationConfigError, match=match):
            jr.register_managed_jsonl_writer_path(candidate)
        assert target.read_text(encoding="utf-8") == "sentinel"
    finally:
        jr._attest_unmanaged_writer_namespace.cache_clear()


def test_unmanaged_namespace_cache_key_tracks_static_and_controls(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    managed = tmp_path / "data" / "managed.jsonl"
    candidate = managed.with_name(managed.name + ".append.lock")
    registry = tmp_path / "control" / "registry.json"
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
    monkeypatch.setattr(jr, "JSONL_PATH_REGISTRY", registry)
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_CONFIG_PATH",
        tmp_path / "control" / "policy.conf",
    )
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_STATE_PATH",
        tmp_path / "control" / "logrotate.status",
    )
    jr._attest_unmanaged_writer_namespace.cache_clear()
    try:
        assert jr.register_managed_jsonl_writer_path(candidate) == candidate
        monkeypatch.setattr(
            jr,
            "static_managed_jsonl_paths",
            lambda: (str(managed),),
        )
        with pytest.raises(jr.JsonlRotationConfigError, match="namespace lock"):
            jr.register_managed_jsonl_writer_path(candidate)

        monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
        monkeypatch.setattr(jr, "JSONL_PATH_REGISTRY", candidate)
        with pytest.raises(
            jr.JsonlRotationConfigError,
            match="registry namespace",
        ):
            jr.register_managed_jsonl_writer_path(candidate)
    finally:
        jr._attest_unmanaged_writer_namespace.cache_clear()


def test_logrotate_cli_rejects_second_config_before_subprocess(
    tmp_path,
    monkeypatch,
):
    from dispatch_v2.core import jsonl_rotation as jr

    called = []
    monkeypatch.setattr(
        jr.subprocess,
        "run",
        lambda *_args, **_kwargs: called.append(True),
    )

    with pytest.raises(SystemExit):
        jr.main(
            [
                str(tmp_path / "policy.conf"),
                str(tmp_path / "unlocked-extra.conf"),
            ]
        )

    assert called == []


def test_generated_logrotate_config_parses_with_real_binary(tmp_path):
    from dispatch_v2.core import jsonl_rotation as jr

    binary = shutil.which("logrotate")
    if binary is None:
        pytest.skip("logrotate binary unavailable")
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    policy = deploy / "dispatch-v2-jsonl-logrotate.conf"
    paths = (tmp_path / "state with space" / "events.jsonl",)

    with jr.materialize_jsonl_logrotate_config(
        policy,
        paths,
    ) as (generated, generated_fd):
        with pytest.raises(OSError):
            os.pwrite(generated_fd, b"tamper", 0)
        completed = subprocess.run(
            [
                binary,
                "-d",
                "-s",
                str(tmp_path / "logrotate.state"),
                str(generated),
            ],
            check=False,
            capture_output=True,
            text=True,
            pass_fds=(generated_fd,),
        )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not generated.exists()


def test_every_known_rotated_jsonl_writer_uses_shared_appender():
    """Completeness gate for all producer paths behind JSONL_PATHS.

    This list intentionally includes offline timers and the still-executable
    onboarding migration: any one of them can overlap system logrotate.
    """
    root = Path(__file__).resolve().parents[1]
    writers = {
        "learning_log": (
            "panel_watcher.py",
            "telegram_approver.py",
            "auto_assign_executor.py",
            "shift_notifications/state.py",
            "migrations/migrate_couriers_2026-05-05.py",
        ),
        "v319c_read_shadow": ("plan_manager.py",),
        "shadow_decisions": ("shadow_dispatcher.py",),
        "sla": ("sla_tracker.py",),
        "consumer_stuck": ("monitoring/consumer_stuck_alert.py",),
        "obj_replay": ("obj_replay_capture.py",),
        "eta_calibration": ("eta_calibration_logger.py",),
        "drive_min_enriched": ("tools/shadow_outcome_enricher.py",),
        "drive_min_calibration": ("auto_proximity_classifier.py",),
        "plan_recheck": ("plan_recheck.py",),
        "czasowka": ("czasowka_scheduler.py",),
        "czasowka_reclaim": ("czasowka_reclaim.py",),
        "uwagi_bridge": ("panel_watcher.py",),
        "geocoding": ("geocoding_audit.py",),
    }
    for group, relative_paths in writers.items():
        for relative_path in relative_paths:
            source = (root / relative_path).read_text(encoding="utf-8")
            assert "append_jsonl" in source, f"{group}: {relative_path} bypasses shim"
            tree = ast.parse(source, filename=relative_path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    mode_node = node.args[1] if len(node.args) > 1 else None
                    for keyword in node.keywords:
                        if keyword.arg == "mode":
                            mode_node = keyword.value
                    mode = (
                        mode_node.value
                        if isinstance(mode_node, ast.Constant)
                        and isinstance(mode_node.value, str)
                        else ""
                    )
                    target = ast.unparse(node.args[0]) if node.args else ""
                    if "a" in mode and "LOCK" not in target.upper():
                        pytest.fail(
                            f"{group}: {relative_path}:{node.lineno} retains "
                            f"bare append {target!r} mode={mode!r}"
                        )
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "open"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and any("O_APPEND" in ast.unparse(arg) for arg in node.args[1:])
                ):
                    pytest.fail(
                        f"{group}: {relative_path}:{node.lineno} retains "
                        "bare os.open(...O_APPEND)"
                    )


def test_rotated_identity_fsync_stays_bound_to_scanned_inode(
    tmp_path, monkeypatch
):
    """Rename/reuse after scan must not fsync an unrelated replacement path."""
    p = tmp_path / "out.jsonl"
    p.write_text("", encoding="utf-8")
    rot1 = p.with_name("out.jsonl.1")
    rot2 = p.with_name("out.jsonl.2")
    rot1.write_text('{"lifecycle_event_id":"E"}\n', encoding="utf-8")
    record_inode = rot1.stat().st_ino
    real_open = builtins.open
    moved = False

    class RotateAfterScan:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            self._stream.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            nonlocal moved
            result = self._stream.__exit__(exc_type, exc, tb)
            if not moved:
                moved = True
                rot1.rename(rot2)
                with real_open(rot1, "w", encoding="utf-8") as replacement:
                    replacement.write('{"lifecycle_event_id":"other"}\n')
            return result

        def __iter__(self):
            return iter(self._stream)

        def fileno(self):
            return self._stream.fileno()

    def racing_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        if Path(path) == rot1 and mode == "rb" and not moved:
            return RotateAfterScan(stream)
        return stream

    fsynced_inodes = []
    real_fsync = ja.os.fsync

    def spy_fsync(fd):
        fsynced_inodes.append(os.fstat(fd).st_ino)
        return real_fsync(fd)

    monkeypatch.setattr(builtins, "open", racing_open)
    monkeypatch.setattr(ja.os, "fsync", spy_fsync)

    assert ja.append_jsonl_once(
        p,
        {"lifecycle_event_id": "E"},
        dedupe_key="lifecycle_event_id",
        dedupe_value="E",
        scan_rotated=True,
    ) is False
    assert record_inode in fsynced_inodes
    assert p.read_text(encoding="utf-8") == ""


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


def test_registry_reserves_numeric_descendants_for_every_writer_role(
    tmp_path,
    monkeypatch,
):
    """Candidate37 F1: typed registry (kazda rola) rezerwuje numeric rotacje.

    Oracle defektu: shadow.jsonl zarejestrowany rola shadow, a
    register_managed_jsonl_writer_path(shadow.jsonl.1) przechodzil jako
    unmanaged writer, bo fence porownywal tylko static manifest.
    """
    from dispatch_v2.core import jsonl_rotation as jr

    registry = tmp_path / "control" / "registry.json"
    shadow = tmp_path / "data" / "shadow.jsonl"
    monkeypatch.setattr(jr, "static_managed_jsonl_paths", lambda: ())
    monkeypatch.setattr(jr, "JSONL_PATH_REGISTRY", registry)
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_CONFIG_PATH",
        tmp_path / "control" / "policy.conf",
    )
    monkeypatch.setattr(
        jr,
        "JSONL_LOGROTATE_STATE_PATH",
        tmp_path / "control" / "logrotate.status",
    )
    jr._attest_unmanaged_writer_namespace.cache_clear()
    try:
        jr.register_jsonl_writer_path(
            shadow,
            registry_path=registry,
            writer_role=jr.JSONL_WRITER_ROLE_SHADOW,
        )
        assert jr.register_managed_jsonl_writer_path(shadow) == shadow
        for descendant in ("shadow.jsonl.1", "shadow.jsonl.1.gz"):
            with pytest.raises(
                jr.JsonlRotationConfigError,
                match="rotation",
            ):
                jr.register_managed_jsonl_writer_path(
                    shadow.with_name(descendant)
                )
        assert jr.register_managed_jsonl_writer_path(shadow) == shadow
    finally:
        jr._attest_unmanaged_writer_namespace.cache_clear()
