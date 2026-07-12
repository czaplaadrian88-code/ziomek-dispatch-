from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dispatch_v2.privacy.private_ledger import SecureJsonlWriter, rotate_secure_jsonl


def _records(paths: list[Path]) -> list[dict]:
    out: list[dict] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            out.append(json.loads(line))
    return out


def test_concurrent_append_and_rotate_has_no_loss_duplicate_or_torn_line(tmp_path):
    root = tmp_path / "private"
    target = root / "shadow.jsonl"
    writer = SecureJsonlWriter(target)
    total_threads = 4
    per_thread = 80
    errors: list[Exception] = []
    start = threading.Barrier(total_threads + 1)

    def produce(thread_id: int) -> None:
        try:
            start.wait(timeout=5)
            for sequence in range(per_thread):
                writer.append({"thread": thread_id, "sequence": sequence})
                if sequence % 13 == 0:
                    time.sleep(0.0005)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=produce, args=(idx,)) for idx in range(total_threads)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    archives: list[Path] = []
    for index in range(5):
        time.sleep(0.003)
        if target.exists():
            archives.append(rotate_secure_jsonl(target, f"shadow-2026010{index + 1}.jsonl"))
    for thread in threads:
        thread.join(timeout=20)
    assert not errors
    paths = archives + ([target] if target.exists() else [])
    got = _records(paths)
    expected = {(thread_id, sequence)
                for thread_id in range(total_threads)
                for sequence in range(per_thread)}
    actual = {(record["thread"], record["sequence"]) for record in got}
    assert len(got) == len(expected)
    assert actual == expected


def test_crash_after_rename_recovers_on_next_append(tmp_path):
    target = tmp_path / "private" / "shadow.jsonl"
    writer = SecureJsonlWriter(target)
    writer.append({"sequence": 1})

    def crash(stage: str) -> None:
        if stage == "after_rename":
            raise RuntimeError("synthetic crash")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        rotate_secure_jsonl(target, "shadow-20260101.jsonl", crash_hook=crash)
    assert not target.exists()
    writer.append({"sequence": 2})
    assert _records([target.parent / "shadow-20260101.jsonl", target]) == [
        {"sequence": 1}, {"sequence": 2},
    ]


def test_crash_after_reopen_keeps_both_archive_and_empty_current(tmp_path):
    target = tmp_path / "private" / "shadow.jsonl"
    SecureJsonlWriter(target).append({"sequence": 1})

    def crash(stage: str) -> None:
        if stage == "after_reopen":
            raise RuntimeError("synthetic crash")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        rotate_secure_jsonl(target, "shadow-20260101.jsonl", crash_hook=crash)
    assert target.exists() and target.stat().st_size == 0
    SecureJsonlWriter(target).append({"sequence": 2})
    assert len(_records([target.parent / "shadow-20260101.jsonl", target])) == 2
