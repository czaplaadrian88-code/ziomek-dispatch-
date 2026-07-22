"""Durable, operator-managed exemptions for czasowka reclaim.

The store is deliberately small and PII-free.  Every mutation holds a
companion lock and commits one JSON document with temp+fsync+rename+dir-fsync,
so active entries and their audit trail cannot diverge.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


STATE_PATH = Path(
    os.environ.get(
        "CZASOWKA_RECLAIM_EXEMPTIONS_PATH",
        "/root/.openclaw/workspace/dispatch_state/czasowka_reclaim_exemptions.json",
    )
)
SCHEMA = "czasowka_reclaim_exemptions.v1"
REASON_CODES = frozenset(
    {
        "business_exception",
        "investigation",
        "manual_assignment",
        "manual_time_hold",
        "operator_released",
    }
)


def validate_order_id(value: object) -> str:
    oid = str(value or "").strip()
    if not oid.isascii() or not oid.isdigit() or not 1 <= len(oid) <= 20:
        raise ValueError("order_id must contain 1-20 ASCII digits")
    return oid


def validate_reason_code(value: object) -> str:
    reason = str(value or "").strip()
    if reason not in REASON_CODES:
        allowed = ",".join(sorted(REASON_CODES))
        raise ValueError(f"reason_code must be one of: {allowed}")
    return reason


def _empty() -> dict:
    return {"schema": SCHEMA, "entries": {}, "audit": []}


def _validate_document(raw: object) -> dict:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ValueError("invalid reclaim exemptions schema")
    if not isinstance(raw.get("entries"), dict) or not isinstance(
        raw.get("audit"), list
    ):
        raise ValueError("invalid reclaim exemptions document")
    return raw


@contextmanager
def _locked(path: Path, *, exclusive: bool) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(str(path) + ".lock")
    with open(lock_path, "a+b") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(
            lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        )
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_unlocked(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _validate_document(json.load(handle))
    except FileNotFoundError:
        return _empty()
    except json.JSONDecodeError as exc:
        raise ValueError("malformed reclaim exemptions JSON") from exc


def _fsync_parent(path: Path) -> None:
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _atomic_write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _fsync_parent(path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def list_exemptions(path: Optional[Path] = None) -> dict:
    target = Path(path or STATE_PATH)
    # Reader nie materializuje store ani lockfile. Pierwszy zapis należy
    # wyłącznie do jawnej komendy operatora.
    if not target.exists():
        return {}
    with _locked(target, exclusive=False):
        document = _load_unlocked(target)
    return dict(document["entries"])


def get_exemption(order_id: object, path: Optional[Path] = None) -> Optional[dict]:
    oid = str(order_id or "").strip()
    if not oid:
        return None
    return list_exemptions(path).get(oid)


def set_exemption(
    order_id: object,
    reason_code: object,
    *,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    oid = validate_order_id(order_id)
    reason = validate_reason_code(reason_code)
    target = Path(path or STATE_PATH)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    with _locked(target, exclusive=True):
        document = _load_unlocked(target)
        document["entries"][oid] = {"reason_code": reason, "created_at": stamp}
        document["audit"].append(
            {
                "action": "add",
                "order_id": oid,
                "reason_code": reason,
                "source": "operator_cli",
                "ts": stamp,
            }
        )
        _atomic_write(target, document)
    return dict(document["entries"][oid])


def remove_exemption(
    order_id: object,
    reason_code: object,
    *,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict:
    oid = validate_order_id(order_id)
    reason = validate_reason_code(reason_code)
    target = Path(path or STATE_PATH)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    with _locked(target, exclusive=True):
        document = _load_unlocked(target)
        removed = document["entries"].pop(oid, None)
        if removed is None:
            raise KeyError(f"order_id {oid} is not exempt")
        document["audit"].append(
            {
                "action": "remove",
                "order_id": oid,
                "reason_code": reason,
                "source": "operator_cli",
                "ts": stamp,
            }
        )
        _atomic_write(target, document)
    return dict(removed)


__all__ = [
    "REASON_CODES",
    "STATE_PATH",
    "get_exemption",
    "list_exemptions",
    "remove_exemption",
    "set_exemption",
    "validate_order_id",
    "validate_reason_code",
]
