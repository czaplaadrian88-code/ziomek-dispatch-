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
from dispatch_v2 import event_bus, event_outbox
from dispatch_v2 import state_machine as sm
from dispatch_v2.event_envelope import event_id_after_state_revision

# Pola payloadu NEW_ORDER czytane przez shadow_dispatcher._event_to_pipeline.
_NEW_ORDER_FIELDS = (
    "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "pickup_at_warsaw", "czas_kuriera_warsaw", "czas_kuriera_hhmm",
    "address_id", "order_type", "created_at_utc",
)

log = logging.getLogger("parcel_lane_merge")

SNAPSHOT_NAME = "orders_state.parcels_shadow.json"
SNAPSHOT_MAX_AGE_SEC = 600  # >10 min = panel sidecar padł → NIE ufaj (nie wpychaj starych)
_TERMINAL = ("delivered", "cancelled", "returned_to_pool")

# Etap 3c: status apki kuriera (courier_api inbox) → orders_state. 5=odebrane, 7=doręczone.
STATUS_INBOX_NAME = "parcel_status_inbox.jsonl"
_STATUS_CODE_EVENT = {5: "COURIER_PICKED_UP", 7: "COURIER_DELIVERED"}
INBOX_MAX_BYTES = 2_000_000  # po przetworzeniu: >tyle → rotacja do .1 (kosmetyka)


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


def _apply_status_inbox() -> int:
    """Etap 3c: zastosuj statusy paczek z inboxu (apka kuriera → courier_api) do orders_state.
    Idempotent po event_id (event_bus). 5→picked_up, 7→delivered; 3/4 nie zmieniają statusu.
    Fail-soft per wiersz. (v1: czyta cały inbox/tick — niska wolumetria paczek; rotacja = TODO.)"""
    path = Path(sm._state_path()).parent / STATUS_INBOX_NAME
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    applied = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        etype = _STATUS_CODE_EVENT.get(int(e.get("status_code", 0) or 0))
        if not etype:
            continue
        oid = str(e.get("oid"))
        cid = str(e.get("cid") or "")
        eid = f"{oid}_{etype}_{e.get('ts')}"
        # Boundary courier_api.main._maybe_inbox_parcel_status zapisuje tu
        # jawny ``recorded_at`` jako Unix seconds. OFF zachowuje historyczny
        # payload; durable przekazuje ten sam source time do koperty i reducera.
        payload = {"source": "parcel_status_inbox"}
        envelope = None
        try:
            recorded_at = datetime.fromtimestamp(int(e["ts"]), timezone.utc)
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
                raise ValueError("parcel status lacks canonical recorded_at")
        else:
            if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
                payload["timestamp"] = recorded_at.isoformat()
            envelope = event_bus.maybe_create_order_envelope(
                event_id=eid,
                event_type=etype,
                order_id=oid,
                courier_id=cid,
                payload=payload,
                created_at=datetime.now(timezone.utc),
                source="parcel_lane_merge:status_inbox",
                policy_version=event_bus.ORDER_EVENT_POLICY_VERSION,
                producer_key=eid,
            )
        emitted = event_bus.emit(
            etype,
            order_id=oid,
            courier_id=cid,
            payload=payload,
            event_id=eid,
            **event_bus.durable_envelope_kwargs(envelope),
        )
        effect = event_bus.apply_state_event(
            {
                "event_type": etype,
                "order_id": oid,
                "courier_id": cid,
                "payload": payload,
            },
            event_id=eid,
            emitted=bool(emitted),
            **event_bus.durable_envelope_kwargs(envelope),
        )
        if effect.should_run_followups:
            applied += 1
            log.info("paczka %s ← %s (apka)", oid, etype)
    # Rotacja: po przetworzeniu (idempotent), gdy plik duży → archiwum .1; courier_api
    # utworzy świeży przy kolejnym append. Bezpieczne: event_bus dedupuje okno rotacji.
    try:
        if path.stat().st_size > INBOX_MAX_BYTES:
            path.rename(path.with_name(STATUS_INBOX_NAME + ".1"))
            log.info("parcel_status_inbox zrotowany (>2MB → .1)")
    except OSError:
        pass
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
            if not (
                sm.ORDER_FSM_ENFORCEMENT_ENABLED
                or event_outbox.DURABLE_EVENT_OUTBOX_ENABLED
            ):
                sm.upsert_order(oid, entry, event="PARCEL_LANE_NEW")
            stats["created"] += 1
        payload = {k: entry.get(k) for k in _NEW_ORDER_FIELDS}
        event_id = f"{oid}_NEW_ORDER_parcel"
        observed_at = datetime.now(timezone.utc)
        envelope = event_bus.maybe_create_order_envelope(
            event_id=event_id,
            event_type="NEW_ORDER",
            order_id=str(oid),
            courier_id=None,
            payload=payload,
            created_at=observed_at,
            source="parcel_lane_merge:snapshot",
            policy_version=event_bus.ORDER_EVENT_POLICY_VERSION,
            producer_key=event_id,
        )
        emitted = event_bus.emit(
            "NEW_ORDER",
            order_id=str(oid),
            payload=payload,
            event_id=event_id,
            **event_bus.durable_envelope_kwargs(envelope),
        )
        if emitted:
            stats["emitted"] += 1
        if (
            sm.ORDER_FSM_ENFORCEMENT_ENABLED
            or event_outbox.DURABLE_EVENT_OUTBOX_ENABLED
        ):
            effect = event_bus.apply_state_event(
                {
                    "event_type": "NEW_ORDER",
                    "order_id": str(oid),
                    "payload": payload,
                },
                event_id=event_id,
                emitted=bool(emitted),
                enforce=True,
                **event_bus.durable_envelope_kwargs(envelope),
            )
            if effect.changed:
                supplemental = {
                    key: value
                    for key, value in entry.items()
                    if key not in {"status", "commitment_level", "history"}
                }
                sm.upsert_order(
                    str(oid), supplemental, event="PARCEL_LANE_ENRICH"
                )

    # 2. Sprzątanie: paczki w stanie, których już nie ma w snapshocie (anulowana/dostarczona/usunięta).
    for oid, so in list(state.items()):
        if so.get("source") != "parcel" or oid in snap_oids:
            continue
        if so.get("status") in _TERMINAL:
            continue
        if (
            sm.ORDER_FSM_ENFORCEMENT_ENABLED
            or event_outbox.DURABLE_EVENT_OUTBOX_ENABLED
        ):
            event_id = f"{oid}_ORDER_CANCELLED_parcel_lane_gone"
            if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
                event_id = event_id_after_state_revision(event_id, so)
            payload = {"reason": "snapshot_missing", "source": "parcel_lane_gone"}
            observed_at = datetime.now(timezone.utc)
            envelope = event_bus.maybe_create_order_envelope(
                event_id=event_id,
                event_type="ORDER_CANCELLED",
                order_id=str(oid),
                courier_id=None,
                payload=payload,
                created_at=observed_at,
                source="parcel_lane_merge:snapshot_retirement",
                policy_version=event_bus.ORDER_EVENT_POLICY_VERSION,
                producer_key=event_id,
            )
            emitted = event_bus.emit_audit(
                "ORDER_CANCELLED",
                order_id=str(oid),
                payload=payload,
                event_id=event_id,
                **event_bus.durable_envelope_kwargs(envelope),
            )
            event_bus.apply_state_event(
                {
                    "event_type": "ORDER_CANCELLED",
                    "order_id": str(oid),
                    "payload": payload,
                },
                event_id=event_id,
                emitted=bool(emitted),
                enforce=True,
                **event_bus.durable_envelope_kwargs(envelope),
            )
        else:
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
