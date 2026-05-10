"""Atomic flags.json write helper (Master plan TOP-15 #6, ARCH P0#4 R-7).

Single source of truth dla mutacji /root/.openclaw/workspace/scripts/flags.json.
Replaces ad-hoc `json.dump(open(p,'w'))` one-liners w dokumentacji + skryptach
rollbacku. Eliminuje race "Adrian + parallel CC = corrupt flagi" (Lekcja #85).

Pattern (Lekcja #14 atomic + Lekcja #71 cross-process lock):
1. fcntl.LOCK_EX na lock-file przed RMW (cross-process serialization)
2. tempfile.mkstemp w katalogu docelowym → json.dump → flush → fsync → close
3. os.replace (atomic per POSIX) — guarantee plik jest valid JSON in-flight
4. Cleanup tempfile w except (BaseException, w tym KeyboardInterrupt)

Reference: dispatch_v2/manual_overrides.py (atomic-only), plan_manager.py (fcntl).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

FLAGS_PATH = Path("/root/.openclaw/workspace/scripts/flags.json")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic JSON write: tempfile + fsync + os.replace.

    Preserves file permissions (flags.json is 0600 in production).
    Cleanup tempfile on any exception including KeyboardInterrupt.
    """
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    mode = 0o600
    if path.exists():
        mode = path.stat().st_mode & 0o777

    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".flags_io_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _locked_rmw(path: Path, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Cross-process locked read-modify-write of JSON file.

    Lock file separate od target (.flags_io.lock obok flags.json). LOCK_EX
    serializes RMW between procesami → no lost-update race per master plan AC.

    Mutator pure function: dict in → dict out. Same dict mutation OK.
    """
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    lock_path = parent / (path.name + ".lock")

    with open(lock_path, "a") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            data = load_flags(path)
            data = mutator(data)
            _atomic_write_json(path, data)
            return data
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def load_flags(path: Path = FLAGS_PATH) -> dict[str, Any]:
    """Pure read flags.json. Returns empty dict if missing or empty."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)
    except json.JSONDecodeError:
        # Corrupt file — caller decyduje czy raise vs default
        raise


def update_flag(name: str, value: Any, path: Path = FLAGS_PATH) -> dict[str, Any]:
    """Load → set/replace flag → atomic write under LOCK_EX."""
    def _mut(d: dict[str, Any]) -> dict[str, Any]:
        d[name] = value
        return d
    return _locked_rmw(path, _mut)


def delete_flag(name: str, path: Path = FLAGS_PATH) -> dict[str, Any]:
    """Load → del key (no-op if missing) → atomic write under LOCK_EX."""
    def _mut(d: dict[str, Any]) -> dict[str, Any]:
        d.pop(name, None)
        return d
    return _locked_rmw(path, _mut)


def set_flags(flags: dict[str, Any], path: Path = FLAGS_PATH) -> None:
    """Bulk replace whole flags dict atomically (migrations/imports).

    NIE używa LOCK_EX — caller odpowiada za serialization (typowo 1× przy starcie).
    Dla coordinated bulk + RMW użyj _locked_rmw bezpośrednio.
    """
    _atomic_write_json(path, flags)
