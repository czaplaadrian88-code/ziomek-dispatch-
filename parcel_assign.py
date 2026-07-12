"""Faza 2 Etap 3b — przydział PACZKI do kuriera WPROST w orders_state (NIE gastro).

Paczki nie ma w gastro → `gastro_assign.py` rzuca HTTP 500. Tu: COURIER_ASSIGNED
emitowany do event_bus + zastosowany w orders_state (`state_machine.update_from_event`)
→ status=assigned + courier_id. Apka kuriera (czyta orders_state po cid) i konsola
pokazują paczkę przypisaną. Nazwę kuriera → cid rozwiązujemy z `kurier_ids.json`
(to samo źródło co gastro_assign).

CLI: `parcel_assign.py --oid 900138096 --kurier "Szymon P" [--time 15]`
Wynik: linia `PARCEL_ASSIGN_OK: ...` (rc 0) lub `PARCEL_ASSIGN_ERROR: ...` (rc 1).
Idempotent po event_id (powtórka tego samego przydziału = brak duplikatu).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2 import event_bus, event_outbox, state_machine
from dispatch_v2.event_envelope import event_id_after_state_revision

KURIER_IDS_FILE = Path("/root/.openclaw/workspace/dispatch_state/kurier_ids.json")


def _resolve_cid(name: str):
    try:
        ids = json.loads(KURIER_IDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ids.get(name)


def assign_parcel(oid: str, kurier_name: str, time_arg: str | None = None) -> tuple[bool, str]:
    """Przypisz paczkę (orders_state). Zwraca (ok, komunikat)."""
    cid = _resolve_cid(kurier_name)
    if cid is None:
        return False, f"PARCEL_ASSIGN_ERROR: nie znaleziono kuriera '{kurier_name}' w kurier_ids.json"
    cur = state_machine.get_all().get(str(oid))
    if cur is None:
        return False, f"PARCEL_ASSIGN_ERROR: paczki {oid} nie ma w orders_state (tor natywny OFF?)"
    if cur.get("source") != "parcel":
        return False, f"PARCEL_ASSIGN_ERROR: zlecenie {oid} nie jest paczką (source={cur.get('source')})"

    payload = {"source": "parcel_assign"}
    if time_arg:
        payload["time_arg"] = str(time_arg)
    event_id = f"{oid}_COURIER_ASSIGNED_parcel_{cid}"
    if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        event_id = event_id_after_state_revision(event_id, cur)
    observed_at = datetime.now(timezone.utc)
    envelope = event_bus.maybe_create_order_envelope(
        event_id=event_id,
        event_type="COURIER_ASSIGNED",
        order_id=str(oid),
        courier_id=str(cid),
        payload=payload,
        created_at=observed_at,
        source="parcel_assign:manual_assignment",
        policy_version=event_bus.ORDER_EVENT_POLICY_VERSION,
        producer_key=event_id,
    )
    event_bus.emit(
        "COURIER_ASSIGNED",
        order_id=str(oid),
        courier_id=str(cid),
        payload=payload,
        event_id=event_id,
        **event_bus.durable_envelope_kwargs(envelope),
    )
    state_event = {
        "event_type": "COURIER_ASSIGNED",
        "order_id": str(oid),
        "courier_id": str(cid),
        "payload": payload,
    }
    if (
        state_machine.ORDER_FSM_ENFORCEMENT_ENABLED
        or event_outbox.DURABLE_EVENT_OUTBOX_ENABLED
    ):
        event_bus.apply_state_event(
            state_event,
            event_id=event_id,
            emitted=True,
            enforce=True,
            **event_bus.durable_envelope_kwargs(envelope),
        )
    else:
        # Byte-parity OFF: historyczny caller zawsze aplikowal state po emit.
        state_machine.update_from_event(state_event)
    return True, f"PARCEL_ASSIGN_OK: {kurier_name} (cid={cid}) → paczka {oid}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oid", required=True)
    ap.add_argument("--kurier", required=True)
    ap.add_argument("--time", default=None)
    args = ap.parse_args(argv)
    ok, msg = assign_parcel(args.oid, args.kurier, args.time)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
