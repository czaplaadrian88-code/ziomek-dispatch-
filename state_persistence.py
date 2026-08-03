"""Canonical persistence policy for JSON object state files.

This leaf module owns exactly one ``.prev`` contract:

* ``.prev`` is the validated predecessor of the current main file;
* a first write has no predecessor and therefore creates no ``.prev``;
* a missing or malformed main is never copied over a healthy ``.prev``;
* writes use temp -> file fsync -> predecessor rename+directory fsync -> main
  rename+directory fsync.

Callers retain domain policy (whether recovery is allowed and which exception is
public), but they do not implement their own backup/read/write machinery.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Sequence


ReadSource = Literal[
    "main",
    "previous",
    "bootstrap",
    "legacy_empty_missing",
    "legacy_empty_corrupt",
]


@dataclass(frozen=True)
class JsonObjectRead:
    """A parsed state object plus the provenance needed by recovery policy."""

    data: dict[str, Any]
    source: ReadSource
    main_error: Optional[BaseException] = None


class JsonObjectShapeError(ValueError):
    """JSON parsed successfully but the state root is not an object."""


def previous_path(path: Path | str) -> Path:
    """Return the one canonical predecessor sidecar path."""
    target = Path(path)
    return Path(str(target) + ".prev")


def sidecar_path(path: Path | str, suffix: str) -> Path:
    """Derive a sibling sidecar without introducing a second path policy."""
    if not suffix.startswith(".") or suffix == ".prev":
        raise ValueError("sidecar suffix must start with '.' and not be '.prev'")
    return Path(str(Path(path)) + suffix)


def fsync_parent(path: Path | str) -> None:
    """Durably commit directory-entry changes for ``path``."""
    target = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(str(target.parent), flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _parse_object(raw: bytes, path: Path) -> dict[str, Any]:
    # Keep UnicodeDecodeError distinct. Legacy feature-OFF readers historically
    # propagated binary/encoding failures while swallowing JSON syntax errors;
    # collapsing both into ValueError would silently change OFF behaviour.
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise JsonObjectShapeError(f"{path} JSON root is not an object")
    return value


def _read_object(path: Path) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return _parse_object(handle.read(), path)


def _read_main_with_retries(
    path: Path,
    retry_delays: Sequence[float],
) -> tuple[Optional[dict[str, Any]], Optional[BaseException]]:
    attempts = len(retry_delays) + 1
    for attempt in range(attempts):
        try:
            return _read_object(path), None
        except FileNotFoundError as exc:
            if attempt == attempts - 1:
                return None, exc
            time.sleep(float(retry_delays[attempt]))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
            return None, exc
    raise AssertionError("unreachable")


def read_json_object(
    path: Path | str,
    *,
    recover_previous: bool = False,
    allow_bootstrap: bool = False,
    legacy_empty: bool = False,
    retry_delays: Sequence[float] = (),
) -> JsonObjectRead:
    """Read a JSON object without an implicit silent fallback.

    Default is strict: malformed/missing input raises its original exception.
    ``recover_previous`` is an explicit domain decision and never applies when
    the caller omits it. ``allow_bootstrap`` accepts a missing main only when a
    predecessor is also absent. ``legacy_empty`` exists solely for feature-OFF
    compatibility and records why the empty object was returned.
    """
    target = Path(path)
    data, main_error = _read_main_with_retries(target, retry_delays)
    if data is not None:
        return JsonObjectRead(data=data, source="main")
    if main_error is None:
        raise AssertionError("read failure without an exception")

    prev = previous_path(target)
    if recover_previous:
        try:
            recovered = _read_object(prev)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError,
                ValueError, OSError):
            recovered = None
        if recovered is not None:
            return JsonObjectRead(
                data=recovered,
                source="previous",
                main_error=main_error,
            )

    if (
        allow_bootstrap
        and isinstance(main_error, FileNotFoundError)
        and not prev.exists()
    ):
        return JsonObjectRead(
            data={}, source="bootstrap", main_error=main_error
        )

    if legacy_empty and isinstance(main_error, (
        FileNotFoundError,
        json.JSONDecodeError,
        JsonObjectShapeError,
    )):
        source: ReadSource = (
            "legacy_empty_missing"
            if isinstance(main_error, FileNotFoundError)
            else "legacy_empty_corrupt"
        )
        return JsonObjectRead(data={}, source=source, main_error=main_error)

    raise main_error


def _write_bytes_temp(path: Path, payload: bytes, *, prefix: str) -> str:
    descriptor, temporary = tempfile.mkstemp(
        prefix=prefix,
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return temporary


def atomic_write_json(
    path: Path | str,
    data: dict[str, Any],
    *,
    backup_previous: bool = True,
    unreadable_main_policy: Literal["raise", "recover", "replace"] = "raise",
    indent: Optional[int] = None,
    separators: Optional[tuple[str, str]] = None,
    ensure_directory_durable: Optional[Callable[[Path], None]] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Atomically write a JSON object under the canonical predecessor policy.

    If a valid main exists, its exact bytes are fsynced to a temp and renamed to
    ``.prev`` before main is replaced. Backup failure aborts the main write.
    Missing/invalid main follows an explicit policy: strict ``raise`` (default),
    ``recover`` only with a valid predecessor, or feature-OFF legacy ``replace``.
    A genuine bootstrap (neither main nor predecessor exists) is always legal.
    """
    if not isinstance(data, dict):
        raise TypeError("state JSON root must be a dict")
    if unreadable_main_policy not in {"raise", "recover", "replace"}:
        raise ValueError(
            f"invalid unreadable_main_policy={unreadable_main_policy!r}"
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    ).encode("utf-8")
    main_temp: Optional[str] = None
    prev_temp: Optional[str] = None
    durable = ensure_directory_durable or fsync_parent
    try:
        # D-1 ordering: finish and fsync new content before any rename.
        main_temp = _write_bytes_temp(
            target, encoded, prefix=target.name + ".new."
        )

        current_raw: Optional[bytes] = None
        main_error: Optional[BaseException] = None
        # Exact legacy write path: without predecessor backup and with an
        # explicit replace policy, the caller neither needs nor historically
        # performed a read/validation of the file being replaced. This keeps
        # A-2 OFF free of extra I/O and permission-sensitive behaviour.
        inspect_current = backup_previous or unreadable_main_policy != "replace"
        if inspect_current:
            try:
                current_raw = target.read_bytes()
            except FileNotFoundError as exc:
                current_raw = None
                main_error = exc
            if current_raw is not None:
                try:
                    _parse_object(current_raw, target)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                    current_raw = None
                    main_error = exc

        if main_error is not None:
            prev = previous_path(target)
            prev_exists = prev.exists()
            bootstrap = isinstance(main_error, FileNotFoundError) and not prev_exists
            if not bootstrap and unreadable_main_policy == "raise":
                raise main_error
            if not bootstrap and unreadable_main_policy == "recover":
                # This is a defensive second strict read. Domain readers already
                # recovered under their lock, but the writer refuses to heal from
                # a missing/corrupt predecessor if a caller ever bypasses them.
                _read_object(prev)
            if logger is not None and not bootstrap:
                logger.error(
                    "state predecessor preserved: current main unreadable "
                    "path=%s policy=%s cause=%s",
                    target,
                    unreadable_main_policy,
                    type(main_error).__name__,
                )

        if backup_previous and current_raw is not None:
            prev = previous_path(target)
            prev_temp = _write_bytes_temp(
                prev, current_raw, prefix=prev.name + ".new."
            )
            os.replace(prev_temp, prev)
            prev_temp = None
            # The recovery point must be directory-durable before main can
            # advance. Otherwise a power loss between the two renames could
            # discard both directory entries despite both temp files having
            # been fsynced.
            # This fsync belongs to the canonical predecessor transaction, not
            # to the caller's post-main durable-receipt hook. Keeping the hooks
            # separate preserves the existing classification/retry contract:
            # a domain fsync failure is injected only after main is visible.
            fsync_parent(prev)

        os.replace(main_temp, target)
        main_temp = None
        durable(target)
    except Exception as exc:
        if logger is not None:
            logger.error(
                "atomic write fail path=%s (%s: %s)",
                target,
                type(exc).__name__,
                exc,
            )
        for temporary in (main_temp, prev_temp):
            if temporary is None:
                continue
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        raise
