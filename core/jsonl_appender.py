"""Atomic JSONL append helper (Master plan TOP-15 #11, audit 2026-05-07).

Eliminates torn-write race when multiple writers append do tego samego .jsonl
pliku. 3 production callsites currently używają bare `open(path, 'a')`:
  - panel_watcher.py learning_log (PANEL_OVERRIDE)
  - telegram_approver.py learning_log (TG_REASON, F7AGREE, /koniec, /poprawa, ...)
  - shadow_dispatcher.py shadow_decisions.jsonl

Race observed: panel_watcher + telegram_approver write do TEGO SAMEGO
learning_log.jsonl. POSIX O_APPEND atomic only for writes ≤ PIPE_BUF (4096B
on Linux). Long records (>4KB) mogą się przeplatać. Belt-and-suspenders:
O_APPEND + fcntl.flock LOCK_EX (kernel atomic dla małych + flock dla każdej
długości cross-process).

Pattern (Lekcja #14 atomic + Lekcja #71 cross-process lock):
1. ensure parent dir
2. open with O_WRONLY | O_APPEND | O_CREAT (mode 0o644)
3. fcntl.flock(fd, LOCK_EX) — serializuje cross-process
4. write bytes (single os.write syscall; loop on short write)
5. fcntl.flock(fd, LOCK_UN)  (implicit on close)
6. os.close

**Fail-loud:** missing dir creatable / permission denied / disk full → raise.
Caller decyduje czy log+continue (audit-trail loss visible) czy fatal.
TypeError dla non-serializable record propagated do callera.

**Why nie shim atop `open()`:** Python's text-mode buffering robi multiple
write() syscalls (line buffering może być line-by-line, fragment per print).
Bare-fd os.write daje single-syscall guarantee w połączeniu z O_APPEND.

Reference patterns:
  dispatch_v2/manual_overrides.py — atomic JSON whole-file replace
  dispatch_v2/plan_manager.py — fcntl lockfile dla state mutation
  dispatch_v2/geocoding_audit.py — JSONL append z fcntl.LOCK_EX (tech-debt #18)

Future:
  - rotate-aware mode (open per call, ditto behavior on rotated file)
  - batch records helper dla shadow_decisions burst writes
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Iterable


_DEFAULT_FILE_MODE = 0o644


def append_jsonl(
    path: str | os.PathLike,
    record: dict | list,
    *,
    ensure_ascii: bool = False,
) -> None:
    """Append single JSON record + trailing newline do .jsonl file.

    Cross-process safe via fcntl.flock LOCK_EX. Single os.write syscall
    (kernel atomic dla ≤PIPE_BUF; flock guarantees serialization dla każdej
    długości — race-free nawet dla 100KB records).

    Args:
        path: Plik .jsonl docelowy. Parent dir tworzony jeśli brakuje.
        record: dict lub list JSON-serializable. TypeError jeśli nie.
        ensure_ascii: Per `json.dumps` (default False — natywny Polski w pliku).

    Raises:
        TypeError: record nie JSON-serializable.
        OSError: parent dir nie creatable, permission denied, disk full.
    """
    line = json.dumps(record, ensure_ascii=ensure_ascii) + "\n"
    _append_bytes(path, line.encode("utf-8"))


def append_jsonl_batch(
    path: str | os.PathLike,
    records: Iterable[dict | list],
    *,
    ensure_ascii: bool = False,
) -> int:
    """Append multiple records w jednym flock-protected write.

    Korzystne przy burst (np. shadow_dispatcher gdy pipeline emit'uje kilka
    decyzji per cycle) — eliminuje N×flock overhead → 1×flock. Każdy record
    serialized osobno, ale wszystkie zapisywane w jednym os.write call w
    miarę możliwości (kernel may split, ale flock chroni).

    Args:
        path: Plik .jsonl docelowy.
        records: Iterable JSON-serializable recordów.
        ensure_ascii: Per json.dumps.

    Returns:
        Liczba records zapisanych (= len(materialized list)).

    Raises:
        TypeError: dowolny record nie JSON-serializable.
        OSError: I/O fail.
    """
    materialized = list(records)
    if not materialized:
        return 0
    payload = "".join(
        json.dumps(r, ensure_ascii=ensure_ascii) + "\n" for r in materialized
    ).encode("utf-8")
    _append_bytes(path, payload)
    return len(materialized)


def _append_bytes(path: str | os.PathLike, data: bytes) -> None:
    """Internal: cross-process safe append bytes do file.

    Ensures parent dir, opens with O_WRONLY|O_APPEND|O_CREAT, takes LOCK_EX,
    writes (loop on partial write), releases lock implicitly on close.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        str(p),
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        _DEFAULT_FILE_MODE,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            n = 0
            total = len(data)
            while n < total:
                written = os.write(fd, data[n:])
                if written == 0:
                    # Disk full / quota — raise OSError
                    raise OSError("os.write returned 0 (disk full?)")
                n += written
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
