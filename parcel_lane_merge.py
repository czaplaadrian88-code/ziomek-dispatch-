"""Faza 2 Etap 3 — MERGER paczek do ŻYWEGO orders_state (dispatch-side).

Czyta snapshot paczek `orders_state.parcels_shadow.json` (pisany przez panel sidecar
`parcel_lane`) i wpisuje AKTYWNE paczki do orders_state przez `state_machine.upsert_order`
(LOCK_EX — ten sam zamek co panel_watcher, zero korupcji). Wtedy realny silnik
(shadow_dispatcher) proponuje je jak gastro, a konsola/apka widzą je natywnie.

Strategia BEZ nadpisywania pracy silnika:
- paczka NIEOBECNA w orders_state → utwórz (pełny wpis),
- paczka JUŻ w orders_state → POMIŃ (nie zatrzyj courier_id/history/decyzji silnika),
- source=parcel w stanie, ZNIKŁA ze snapshotu (anulowana/dostarczona/usunięta) i jeszcze
  nie-terminalna → ustaw terminalny (sprzątanie; prune ją usunie).

Watcher pomija source=parcel BEZWARUNKOWO (guard w panel_watcher). Flaga
`ENABLE_PARCEL_LANE_LIVE` (hot z flags.json) gate'uje TYLKO ten merger: OFF = no-op.
"""
from __future__ import annotations

import json
import fcntl
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2 import common as C
from dispatch_v2 import durable_event_apply, event_bus, lifecycle_downstream
from dispatch_v2 import state_machine as sm

# Pola payloadu NEW_ORDER czytane przez shadow_dispatcher._event_to_pipeline.
_NEW_ORDER_FIELDS = (
    "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "pickup_at_warsaw", "czas_kuriera_warsaw", "czas_kuriera_hhmm",
    "address_id", "order_type", "created_at_utc", "geocode_street_only_approx",
)

log = logging.getLogger("parcel_lane_merge")

SNAPSHOT_NAME = "orders_state.parcels_shadow.json"
SNAPSHOT_MAX_AGE_SEC = 600  # >10 min = panel sidecar padł → NIE ufaj (nie wpychaj starych)
_TERMINAL = ("delivered", "cancelled", "returned_to_pool")

# Etap 3c: status apki kuriera (courier_api inbox) → orders_state. 5=odebrane, 7=doręczone.
STATUS_INBOX_NAME = "parcel_status_inbox.jsonl"
_STATUS_CODE_EVENT = {5: "COURIER_PICKED_UP", 7: "COURIER_DELIVERED"}


def _snapshot_path() -> Path:
    return Path(sm._state_path()).parent / SNAPSHOT_NAME


def _load_snapshot():
    """{oid: entry} świeżych AKTYWNYCH paczek; None gdy brak/stale/zły plik."""
    try:
        raw = json.loads(_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(str(raw.get("written_at")))).total_seconds()
    except (TypeError, ValueError):
        return None
    if age > SNAPSHOT_MAX_AGE_SEC:
        log.warning("snapshot paczek stale (%.0fs > %ds) — pomijam", age, SNAPSHOT_MAX_AGE_SEC)
        return None
    orders = raw.get("orders")
    return orders if isinstance(orders, dict) else {}


def _status_inbox_archives(path: Path) -> list[Path]:
    legacy = path.with_name(STATUS_INBOX_NAME + ".1")
    archives = sorted(path.parent.glob(STATUS_INBOX_NAME + ".pending.*"))
    if legacy.exists():
        archives.insert(0, legacy)
    return archives


def _fsync_parent(path: Path) -> None:
    """Persist inbox rename metadata before reporting the rotation complete."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    dir_fd = os.open(str(path.parent), flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _status_inbox_lock_path(path: Path) -> Path:
    """Stable namespace lock shared with courier-api's parcel producer."""
    return path.with_name(path.name + ".lock")


def _archive_has_open_fd(path: Path) -> bool:
    """Fail closed while any process still references this archive inode.

    A rolling-deploy marker can only attest one new process.  It says nothing
    about an overlapping legacy writer that opened the active pathname before
    our rename and still owns that inode.  Linux ``/proc/*/fd`` is the direct
    oracle for the object we are about to unlink.  Transient process exits are
    harmless; an unreadable fd namespace is treated as busy, never as proof
    that deletion is safe.
    """
    try:
        target = path.stat()
    except OSError:
        return True
    target_identity = (target.st_dev, target.st_ino)
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return True
    for process in processes:
        if not process.name.isdigit():
            continue
        fd_dir = process / "fd"
        try:
            descriptors = list(fd_dir.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            return True
        for descriptor in descriptors:
            try:
                opened = descriptor.stat()
            except FileNotFoundError:
                continue
            except OSError:
                return True
            if (opened.st_dev, opened.st_ino) == target_identity:
                return True
    return False


@contextmanager
def _status_inbox_lock(path: Path):
    lock_path = _status_inbox_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _apply_status_file(path: Path) -> tuple[int, bool]:
    """Apply one immutable snapshot; bool says every durable row is terminal."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, False
    applied = 0
    all_resolved = True
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
            if not isinstance(e, dict):
                raise TypeError("parcel status row must be a JSON object")
            status_code = int(e.get("status_code", 0) or 0)
        except (TypeError, ValueError):
            # Could be a writer line observed before its final bytes. Once the
            # active file is renamed, the full archive is retried and retained
            # loudly if it is genuinely malformed.
            all_resolved = False
            log.warning("parcel status inbox malformed row retained in %s", path)
            continue
        etype = _STATUS_CODE_EVENT.get(status_code)
        if not etype:
            continue
        oid = str(e.get("oid"))
        cid = str(e.get("cid") or "")
        eid = f"{oid}_{etype}_{e.get('ts')}"
        payload = {
            # Provenance only. Timestamp pozostaje celowo nieuzupelniony:
            # jego kontrakt wymaga osobnej decyzji, a observer ma ujawnic
            # dotychczasowy fallback legacy zamiast go maskowac.
            "source": "parcel_status_inbox",
        }
        outcome = durable_event_apply.emit_and_apply(
            etype,
            order_id=oid,
            courier_id=cid,
            payload=payload,
            state_payload=None,
            event_key=eid,
            emit_fn=event_bus.emit,
            state_update_fn=sm.update_from_event,
            effect_status_fn=sm.event_effect_status,
            get_order_fn=sm.get_order_strict,
            downstream_fn=lifecycle_downstream.apply,
        )
        if outcome.state_ready and (
            outcome.event_created or outcome.state_transitioned
        ):
            applied += 1
            log.info("paczka %s ← %s (apka)", oid, etype)
        if not (outcome.state_ready or outcome.superseded):
            all_resolved = False
            log.warning(
                "paczka %s: durable %s pending stage=%s",
                oid,
                etype,
                outcome.failure_stage,
            )
    return applied, all_resolved


def _apply_status_inbox() -> int:
    """Etap 3c: zastosuj statusy paczek z inboxu (apka kuriera → courier_api) do orders_state.
    Idempotent po event_id (event_bus). 5→picked_up, 7→delivered; 3/4 nie zmieniają statusu.
    Fail-soft per wiersz. (v1: czyta cały inbox/tick — niska wolumetria paczek; rotacja = TODO.)"""
    path = Path(sm._state_path()).parent / STATUS_INBOX_NAME
    applied = 0
    # Immutable archives are replayed until every durable event is terminal.
    # They never collide by name, so a failed row or a concurrent append cannot
    # disappear behind a later ``.1`` replacement.
    for archive in _status_inbox_archives(path):
        count, resolved = _apply_status_file(archive)
        applied += count
        if resolved and not _archive_has_open_fd(archive):
            try:
                archive.unlink()
                _fsync_parent(archive)
            except OSError:
                pass

    # Snapshot the active inode under the same stable sidecar lock used by the
    # courier-api writer. The lock is held only for rename+dir fsync, never for
    # durable state/downstream work. Therefore no producer can retain an open
    # fd to an archive that a later tick might unlink.
    archive = None
    with _status_inbox_lock(path):
        try:
            if path.exists() and path.stat().st_size > 0:
                archive = path.with_name(
                    f"{STATUS_INBOX_NAME}.pending.{time.time_ns()}.{os.getpid()}"
                )
                path.rename(archive)
                _fsync_parent(archive)
                log.info("parcel_status_inbox zrotowany do %s", archive.name)
        except OSError:
            archive = None

    if archive is not None:
        count, _resolved = _apply_status_file(archive)
        applied += count
        # Keep the fresh immutable snapshot until the next tick. This makes a
        # crash after state apply but before its receipt a plain replay and
        # gives a rolling deployment one full cycle to replace legacy writers.
    return applied


def run() -> dict:
    """Jeden przebieg mergera. Zwraca statystyki. Flaga OFF → no-op."""
    if not C.flag("ENABLE_PARCEL_LANE_LIVE", getattr(C, "ENABLE_PARCEL_LANE_LIVE", False)):
        return {"enabled": False}
    # Etap 3c: statusy z apki (inbox) → orders_state — NIEZALEŻNIE od snapshotu.
    status_applied = _apply_status_inbox()
    snap = _load_snapshot()
    if snap is None:
        return {"enabled": True, "snapshot": "missing_or_stale", "status_applied": status_applied}

    state = sm.get_all()
    snap_oids = set(snap.keys())
    stats = {"enabled": True, "created": 0, "kept": 0, "retired": 0}

    # 1. NOWE paczki → utwórz; ISTNIEJĄCE → zostaw silnikowi (bez clobberu).
    #    ZAWSZE emituj NEW_ORDER (idempotent po event_id) → shadow_dispatcher PROPONUJE
    #    paczkę jak gastro (silnik jest event-driven, nie skanuje orders_state).
    stats["emitted"] = 0
    for oid, entry in snap.items():
        if oid in state:
            stats["kept"] += 1
        else:
            sm.upsert_order(oid, entry, event="PARCEL_LANE_NEW")
            stats["created"] += 1
        payload = {k: entry.get(k) for k in _NEW_ORDER_FIELDS}
        if event_bus.emit("NEW_ORDER", order_id=str(oid), payload=payload,
                          event_id=f"{oid}_NEW_ORDER_parcel"):
            stats["emitted"] += 1

    # 2. Sprzątanie: paczki w stanie, których już nie ma w snapshocie (anulowana/dostarczona/usunięta).
    for oid, so in list(state.items()):
        if so.get("source") != "parcel" or oid in snap_oids:
            continue
        if so.get("status") in _TERMINAL:
            continue
        sm.set_status(oid, "cancelled", event="PARCEL_LANE_GONE")
        stats["retired"] += 1

    stats["status_applied"] = status_applied
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stats = run()
    log.info("parcel lane merge: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
