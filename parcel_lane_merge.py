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
import logging
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2 import common as C
from dispatch_v2 import state_machine as sm

log = logging.getLogger("parcel_lane_merge")

SNAPSHOT_NAME = "orders_state.parcels_shadow.json"
SNAPSHOT_MAX_AGE_SEC = 600  # >10 min = panel sidecar padł → NIE ufaj (nie wpychaj starych)
_TERMINAL = ("delivered", "cancelled", "returned_to_pool")


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


def run() -> dict:
    """Jeden przebieg mergera. Zwraca statystyki. Flaga OFF → no-op."""
    if not C.flag("ENABLE_PARCEL_LANE_LIVE", getattr(C, "ENABLE_PARCEL_LANE_LIVE", False)):
        return {"enabled": False}
    snap = _load_snapshot()
    if snap is None:
        return {"enabled": True, "snapshot": "missing_or_stale"}

    state = sm.get_all()
    snap_oids = set(snap.keys())
    stats = {"enabled": True, "created": 0, "kept": 0, "retired": 0}

    # 1. NOWE paczki → utwórz; ISTNIEJĄCE → zostaw silnikowi (bez clobberu).
    for oid, entry in snap.items():
        if oid in state:
            stats["kept"] += 1
            continue
        sm.upsert_order(oid, entry, event="PARCEL_LANE_NEW")
        stats["created"] += 1

    # 2. Sprzątanie: paczki w stanie, których już nie ma w snapshocie (anulowana/dostarczona/usunięta).
    for oid, so in list(state.items()):
        if so.get("source") != "parcel" or oid in snap_oids:
            continue
        if so.get("status") in _TERMINAL:
            continue
        sm.set_status(oid, "cancelled", event="PARCEL_LANE_GONE")
        stats["retired"] += 1
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    stats = run()
    log.info("parcel lane merge: %s", stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
