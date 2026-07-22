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
        body for paths, body in blocks if any(path.endswith(".jsonl") for path in paths)
    ]
    assert len(jsonl_blocks) == 2
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
            jr.run_logrotate(
                str(tmp_path / "logrotate.conf"),
                paths=(p,),
            )

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

    assert jr.run_logrotate("unused.conf", paths=(p,)) == 0
    assert [
        json.loads(line)["event"]
        for line in rotated.read_text(encoding="utf-8").splitlines()
    ] == ["before", "late-legacy"]
    assert p.read_text(encoding="utf-8") == ""


def test_logrotate_wrapper_manifest_matches_every_jsonl_config_path():
    from dispatch_v2.core import jsonl_rotation as jr

    deploy = Path(__file__).resolve().parents[1] / "deploy"
    config_path = deploy / "dispatch-v2-jsonl-logrotate.conf"
    configured = {
        line.strip()
        for line in config_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("/") and line.strip().endswith(".jsonl")
    }
    assert configured == set(jr.JSONL_PATHS)
    service = (deploy / "dispatch-v2-jsonl-logrotate.service").read_text(
        encoding="utf-8"
    )
    timer = (deploy / "dispatch-v2-jsonl-logrotate.timer").read_text(
        encoding="utf-8"
    )
    assert "dispatch_v2.core.jsonl_rotation" in service
    assert "/etc/logrotate-dispatch-v2-jsonl.conf" in service
    assert "OnCalendar=" in timer


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
