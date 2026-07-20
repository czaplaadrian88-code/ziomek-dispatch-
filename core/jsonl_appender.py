"""Atomic JSONL append helper (Master plan TOP-15 #11, audit 2026-05-07).

Eliminates torn-write and logrotate races when multiple writers append do tego
samego .jsonl pliku. Every JSONL covered by dispatch-v2 logrotate uses this
stable ``<name>.append.lock`` namespace together with the canonical rotation
wrapper in :mod:`dispatch_v2.core.jsonl_rotation`.

Race observed: panel_watcher + telegram_approver write do TEGO SAMEGO
learning_log.jsonl. POSIX O_APPEND atomic only for writes ≤ PIPE_BUF (4096B
on Linux). Long records (>4KB) mogą się przeplatać. Belt-and-suspenders:
O_APPEND + fcntl.flock LOCK_EX (kernel atomic dla małych + flock dla każdej
długości cross-process).

Pattern (Lekcja #14 atomic + Lekcja #71 cross-process lock):
1. ensure parent dir
2. open with O_RDWR | O_APPEND | O_CREAT (mode 0o644)
3. fcntl.flock(fd, LOCK_EX) — serializuje cross-process
4. separate a truncated predecessor, then write all bytes
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
  - batch records helper dla shadow_decisions burst writes
"""
from __future__ import annotations

import fcntl
import gzip
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable


_DEFAULT_FILE_MODE = 0o644
_NAMESPACE_LOCK_SUFFIX = ".append.lock"
_NAMESPACE_RETRIES = 3


@contextmanager
def _locked_namespace(path: Path):
    """Serialize writers on a stable inode that log rotation never renames."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + _NAMESPACE_LOCK_SUFFIX)
    lock_fd = os.open(
        str(lock_path),
        os.O_RDWR | os.O_CREAT,
        _DEFAULT_FILE_MODE,
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _open_current(path: Path, flags: int) -> int:
    """Open the active pathname, rejecting an inode already renamed by rotate."""
    last_error: OSError | None = None
    for _attempt in range(_NAMESPACE_RETRIES):
        fd = os.open(str(path), flags | os.O_CREAT, _DEFAULT_FILE_MODE)
        try:
            opened = os.fstat(fd)
            current = os.stat(path)
            if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                return fd
        except OSError as exc:
            last_error = exc
        os.close(fd)
    raise OSError(f"active JSONL pathname kept moving: {path}") from last_error


def append_jsonl(
    path: str | os.PathLike,
    record: dict | list,
    *,
    ensure_ascii: bool = False,
    separators: tuple[str, str] | None = None,
    default: Callable[[Any], Any] | None = None,
) -> None:
    """Append single JSON record + trailing newline do .jsonl file.

    Cross-process safe via fcntl.flock LOCK_EX. Single os.write syscall
    (kernel atomic dla ≤PIPE_BUF; flock guarantees serialization dla każdej
    długości — race-free nawet dla 100KB records).

    Args:
        path: Plik .jsonl docelowy. Parent dir tworzony jeśli brakuje.
        record: dict lub list JSON-serializable. TypeError jeśli nie.
        ensure_ascii: Per `json.dumps` (default False — natywny Polski w pliku).
        separators: Optional compact/custom `json.dumps` separators.
        default: Optional `json.dumps` fallback for otherwise unsupported values.

    Raises:
        TypeError: record nie JSON-serializable.
        OSError: parent dir nie creatable, permission denied, disk full.
    """
    json_kwargs: dict[str, Any] = {"ensure_ascii": ensure_ascii}
    if separators is not None:
        json_kwargs["separators"] = separators
    if default is not None:
        json_kwargs["default"] = default
    line = json.dumps(record, **json_kwargs) + "\n"
    _append_bytes(path, line.encode("utf-8"))


def append_jsonl_durable(
    path: str | os.PathLike,
    record: dict,
    *,
    ensure_ascii: bool = False,
) -> None:
    """Append one known-new record and fsync file plus parent directory.

    This is the fast first-attempt half of a durable external receipt: the
    caller already owns the unique delivery lane, so scanning a potentially
    100+ MB history cannot improve correctness. If that caller crashes before
    committing its receipt, the retry must use :func:`append_jsonl_once`.
    """
    if not isinstance(record, dict):
        raise TypeError("append_jsonl_durable record must be a dict")
    line = (json.dumps(record, ensure_ascii=ensure_ascii) + "\n").encode("utf-8")
    p = Path(path)
    with _locked_namespace(p):
        fd = _open_current(p, os.O_RDWR | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                _ensure_line_boundary(fd)
                _write_all(fd, line)
                os.fsync(fd)
                _fsync_parent_dir(p)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def append_jsonl_once(
    path: str | os.PathLike,
    record: dict,
    *,
    dedupe_key: str,
    dedupe_value: Any,
    ensure_ascii: bool = False,
    scan_rotated: bool = False,
) -> bool:
    """Durably append a record at most once for one exact dedupe identity.

    Check and append happen under the same cross-process ``LOCK_EX``. Both the
    file and its parent directory are fsynced before success is returned, so a
    caller may safely persist its own completion receipt afterwards. A retry
    that finds the prior line also fsyncs it before reporting the duplicate.

    Returns ``True`` when this call appended, ``False`` when the identity was
    already present. On a durable callback retry, ``scan_rotated=True`` also
    checks numeric logrotate siblings (including ``.gz``) before appending.
    This is safe with the repository's dedicated, namespace-locked,
    rename-based JSONL service. ``copytruncate`` has an append-after-copy loss
    window and is forbidden. Malformed legacy JSONL lines are ignored and
    cannot be mistaken for a matching record.
    """
    if not isinstance(record, dict):
        raise TypeError("append_jsonl_once record must be a dict")
    if not dedupe_key:
        raise ValueError("append_jsonl_once dedupe_key must be non-empty")
    if record.get(dedupe_key) != dedupe_value:
        raise ValueError(
            "append_jsonl_once record dedupe value does not match argument"
        )

    line = (json.dumps(record, ensure_ascii=ensure_ascii) + "\n").encode("utf-8")
    p = Path(path)
    with _locked_namespace(p):
        fd = _open_current(p, os.O_RDWR | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                already_present = _fd_has_identity(fd, dedupe_key, dedupe_value)
                if (
                    not already_present
                    and scan_rotated
                    and _rotated_files_have_identity(p, dedupe_key, dedupe_value)
                ):
                    already_present = True
                # A kill during an earlier short-write loop may leave a truncated
                # final line. Separate it before any durable retry record so the
                # new JSON remains independently parseable.
                _ensure_line_boundary(fd)
                if already_present:
                    os.fsync(fd)
                    _fsync_parent_dir(p)
                    return False
                _write_all(fd, line)
                os.fsync(fd)
                _fsync_parent_dir(p)
                return True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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


def append_jsonl_batch_durable(
    path: str | os.PathLike,
    records: Iterable[dict | list],
    *,
    ensure_ascii: bool = False,
) -> int:
    """Append a batch and fsync the file plus its parent directory.

    This preserves the durability contract of batch producers while sharing
    the same stable namespace lock as logrotate. A truncated legacy tail is
    separated before the first new record so valid JSON cannot be glued to an
    interrupted predecessor.
    """
    materialized = list(records)
    if not materialized:
        return 0
    payload = "".join(
        json.dumps(record, ensure_ascii=ensure_ascii) + "\n"
        for record in materialized
    ).encode("utf-8")
    p = Path(path)
    with _locked_namespace(p):
        fd = _open_current(p, os.O_RDWR | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                _ensure_line_boundary(fd)
                _write_all(fd, payload)
                os.fsync(fd)
                _fsync_parent_dir(p)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return len(materialized)


def _append_bytes(path: str | os.PathLike, data: bytes) -> None:
    """Internal: cross-process safe append bytes do file.

    Ensures parent dir, opens with O_RDWR|O_APPEND|O_CREAT, takes LOCK_EX,
    separates an interrupted tail, writes (loop on partial write), and
    releases lock implicitly on close.
    """
    p = Path(path)
    with _locked_namespace(p):
        fd = _open_current(p, os.O_RDWR | os.O_APPEND)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                _ensure_line_boundary(fd)
                _write_all(fd, data)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    """Write the complete payload, failing loudly on a zero-length write."""
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written == 0:
            raise OSError("os.write returned 0 (disk full?)")
        offset += written


def _fd_has_identity(fd: int, dedupe_key: str, dedupe_value: Any) -> bool:
    """Exact full-file JSONL scan while caller owns the exclusive flock."""
    os.lseek(fd, 0, os.SEEK_SET)
    pending = b""
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        pending += chunk
        lines = pending.split(b"\n")
        pending = lines.pop()
        for raw in lines:
            if _line_has_identity(raw, dedupe_key, dedupe_value):
                return True
    return _line_has_identity(pending, dedupe_key, dedupe_value)


def _line_has_identity(raw: bytes, dedupe_key: str, dedupe_value: Any) -> bool:
    if not raw.strip():
        return False
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(parsed, dict) and parsed.get(dedupe_key) == dedupe_value


def _rotated_files_have_identity(
    path: Path,
    dedupe_key: str,
    dedupe_value: Any,
) -> bool:
    """Scan logrotate ``name.N[.gz]`` siblings from a stable-enough snapshot.

    Logrotate renames ``.1 -> .2`` between glob and open. Treating a vanished
    candidate as a clean miss can append a duplicate even though the identity
    still exists under its new number. Re-snapshot a bounded number of times;
    continuous churn fails loudly so the durable caller leaves its receipt for
    a later retry instead of guessing.
    """
    last_moved: OSError | None = None
    for _attempt in range(_NAMESPACE_RETRIES):
        candidates = _rotation_snapshot(path)
        try:
            for _rotation, candidate, expected in candidates:
                opener = gzip.open if candidate.name.endswith(".gz") else open
                found = False
                with opener(candidate, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if _stat_identity(opened) != expected:
                        raise OSError(
                            f"rotated JSONL pathname changed inode: {candidate}"
                        )
                    # Always consume through EOF. For gzip this validates the
                    # CRC/ISIZE trailer; short-circuiting on the first match can
                    # accept a truncated archive as durable evidence.
                    for raw in stream:
                        if _line_has_identity(raw, dedupe_key, dedupe_value):
                            found = True
                    if found:
                        # Fsync the exact inode that supplied the identity.
                        # Reopening ``candidate`` after close can hit a reused
                        # .N pathname while logrotate moves the original to
                        # .N+1, falsely acknowledging an unrelated file.
                        os.fsync(stream.fileno())
                if found:
                    if _rotation_snapshot(path) != candidates:
                        raise OSError(
                            f"rotated JSONL namespace changed after match: {path}"
                        )
                    return True
            if _rotation_snapshot(path) != candidates:
                raise OSError(f"rotated JSONL namespace changed: {path}")
        except (FileNotFoundError, OSError, EOFError, gzip.BadGzipFile) as exc:
            last_moved = exc
            continue
        return False
    raise OSError(
        f"rotated dedupe namespace kept moving during scan: {path}"
    ) from last_moved


def _stat_identity(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _rotation_snapshot(
    path: Path,
) -> list[tuple[int, Path, tuple[int, int, int, int]]]:
    """Bind every numeric rotation pathname to the inode actually snapshotted."""
    prefix = f"{path.name}."
    candidates: list[tuple[int, Path, tuple[int, int, int, int]]] = []
    for candidate in path.parent.glob(f"{path.name}.*"):
        suffix = candidate.name[len(prefix):]
        numeric = suffix[:-3] if suffix.endswith(".gz") else suffix
        if not numeric.isdigit():
            continue
        try:
            identity = _stat_identity(candidate.stat())
        except FileNotFoundError:
            continue
        candidates.append((int(numeric), candidate, identity))
    return sorted(candidates, key=lambda item: (item[0], item[1].name))


def _ensure_line_boundary(fd: int) -> None:
    end = os.lseek(fd, 0, os.SEEK_END)
    if end == 0:
        return
    os.lseek(fd, end - 1, os.SEEK_SET)
    if os.read(fd, 1) != b"\n":
        _write_all(fd, b"\n")


def _fsync_parent_dir(path: Path) -> None:
    """Persist a possibly new directory entry before external receipt commit."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    dir_fd = os.open(str(path.parent), flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
