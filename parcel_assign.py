"""Faza 2 Etap 3b — przydział PACZKI do kuriera WPROST w orders_state (NIE gastro).

Paczki nie ma w gastro → `gastro_assign.py` rzuca HTTP 500. Tu: COURIER_ASSIGNED
emitowany do event_bus + zastosowany w orders_state (`state_machine.update_from_event`)
→ status=assigned + courier_id. Apka kuriera (czyta orders_state po cid) i konsola
pokazują paczkę przypisaną. Nazwę kuriera → cid rozwiązujemy z `kurier_ids.json`
(to samo źródło co gastro_assign).

CLI: `parcel_assign.py --oid 900138096 --kurier "Szymon P" [--time 15]`
Wynik: linia `PARCEL_ASSIGN_OK: ...` (rc 0) lub `PARCEL_ASSIGN_ERROR: ...` (rc 1).
Idempotentny durable key przydziału rozdziela retry od późniejszego cyklu
ponownego przypisania tego samego kuriera.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dispatch_v2 import (
    durable_event_apply,
    event_bus,
    lifecycle_downstream,
    state_machine,
)

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
    outcome = durable_event_apply.emit_and_apply(
        "COURIER_ASSIGNED",
        order_id=str(oid),
        courier_id=str(cid),
        payload=payload,
        state_payload=None,
        event_key=f"{oid}_COURIER_ASSIGNED_{cid}_canonical",
        # COURIER_ASSIGNED jest audit-only; queue emit zostawial wieczny pending.
        emit_fn=event_bus.emit_audit,
        state_update_fn=state_machine.update_from_event,
        effect_status_fn=state_machine.event_effect_status,
        get_order_fn=state_machine.get_order_strict,
        downstream_fn=lifecycle_downstream.apply,
    )
    if not outcome.state_ready:
        return (
            False,
            "PARCEL_ASSIGN_ERROR: przydzial utrwalony do retry, ale state nie "
            f"jest jeszcze gotowy (stage={outcome.failure_stage or 'pending'})",
        )
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
