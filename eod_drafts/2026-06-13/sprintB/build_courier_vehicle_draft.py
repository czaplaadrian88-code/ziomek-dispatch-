#!/usr/bin/env python3
"""B6 — build courier_vehicle.draft.json from panel (read-only).

Source of truth = panel Postgres `courier` table (nadajesz_panel @127.0.0.1:5433):
  external_id = cid (klucz Ziomka), vehicle_owner in ('company','own').

Maps to the schema consumed by dispatch_v2/pln_objective.py (E7):
  {"<cid>": "firmowe" | "wlasne"}     # 'own' -> 'wlasne', 'company' -> 'firmowe'
_vehicle_for() defaults missing cid -> 'firmowe' (cost/km 0.90, konserwatywnie).

READ-ONLY: SELECT only, no writes anywhere. Writes the DRAFT to a *.draft.json
(never the live dispatch_state/courier_vehicle.json — that swap is a separate ACK).

Run from panel backend so it loads its own .env:
  cd /root/.openclaw/workspace/nadajesz_clone/panel/backend && \
    ./.venv/bin/python /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-13/sprintB/build_courier_vehicle_draft.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# Allow running from any cwd: ensure panel backend (which holds `app/`) is importable.
_PANEL_BACKEND = "/root/.openclaw/workspace/nadajesz_clone/panel/backend"
if _PANEL_BACKEND not in sys.path:
    sys.path.insert(0, _PANEL_BACKEND)

from app.core.config import settings  # panel app config (reads its own .env)
from sqlalchemy import create_engine, text

OUT_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-13/sprintB"
DRAFT_PATH = os.path.join(OUT_DIR, "courier_vehicle.draft.json")
LIVE_TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

# pln_objective.py constants (kept in sync for the human-readable cost block)
KM_COST = {"firmowe": 0.90, "wlasne": 0.0}
OWNER_TO_LABEL = {"own": "wlasne", "company": "firmowe"}


def _atomic_write_json(path: str, obj) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> None:
    eng = create_engine(settings.database_url)
    with eng.connect() as c:
        rows = list(c.execute(text(
            "SELECT external_id, name, full_name, vehicle_owner, active, archived, "
            "       updated_at "
            "FROM courier ORDER BY CAST(external_id AS INTEGER)"
        )))
        # audit trail of explicit operator ownership changes
        hist = {str(r[0]): str(r[1])[:19] for r in c.execute(text(
            "SELECT co.external_id, ch.effective_from "
            "FROM courier_history ch JOIN courier co ON co.id=ch.courier_id "
            "WHERE ch.kind='vehicle'"
        ))}

    # Live Ziomek roster (cids actually in dispatch) — for coverage attestation.
    try:
        tiers = json.load(open(LIVE_TIERS, encoding="utf-8"))
        roster = {k for k in tiers.keys() if k != "_meta"}
    except Exception:
        roster = set()

    active = [r for r in rows if r[4] and not r[5]]
    own_active = [r for r in active if r[3] == "own"]

    # ── mapping (consumer schema) — ALL active+non-archived couriers, explicit. ──
    # Coordinator cid=26 is a virtual holding bucket (not a driver) -> skip.
    mapping: dict[str, str] = {}
    detail: list[dict] = []
    for r in active:
        cid = str(r[0])
        if cid == "26":  # Koordynator (virtual)
            continue
        label = OWNER_TO_LABEL.get(r[3], "firmowe")
        mapping[cid] = label
        detail.append({
            "cid": cid,
            "name": r[2] or r[1],
            "vehicle_owner_panel": r[3],
            "label": label,
            "km_cost_pln": KM_COST[label],
            "in_ziomek_roster": cid in roster,
            "ownership_audit_from": hist.get(cid),  # None if no explicit change record
        })

    own_cids = sorted([str(r[0]) for r in own_active], key=int)

    # IMPORTANT: pln_objective._vehicle_for reads cid->label pairs at the TOP LEVEL
    # of the JSON (json.load(...).get(str(cid), "firmowe")). So the swap-ready draft
    # must have cid keys at top level. _meta / _detail are non-numeric keys and are
    # never looked up by a cid -> harmless to the consumer, useful for audit.
    out = {}
    out["_meta"] = {
            "schema": "cid(str) -> 'firmowe'|'wlasne' (consumed by dispatch_v2/pln_objective.py _vehicle_for)",
            "purpose": "B6 DRAFT — per-courier vehicle ownership / km-cost for E7 PLN objective",
            "status": "DRAFT — NOT live; swap to dispatch_state/courier_vehicle.json is a separate ACK'd action",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "panel Postgres nadajesz_panel.courier (external_id=cid, vehicle_owner), read-only",
            "owner_to_label": OWNER_TO_LABEL,
            "km_cost_pln_by_label": KM_COST,
            "default_for_missing_cid": "firmowe (0.90 PLN/km) — pln_objective._vehicle_for fallback",
            "couriers_total_panel": len(rows),
            "couriers_active_nonarchived": len(active),
            "mapped_in_draft": len(mapping),
            "own_count": len(own_cids),
            "company_count": len(mapping) - len(own_cids),
            "own_cids": own_cids,
            "skipped_virtual": ["26 (Koordynator — holding bucket, not a driver)"],
            "coverage_vs_ziomek_roster": {
                "ziomek_roster_cids": len(roster),
                "own_present_in_roster": sorted([c for c in own_cids if c in roster], key=int),
                "active_missing_from_roster": sorted([str(r[0]) for r in active if str(r[0]) not in roster], key=int),
            },
            "provenance_note": (
                "vehicle_owner='own' rows confirmed by operator via panel courier_history "
                "(kind=vehicle): " + ", ".join(sorted(hist.keys(), key=lambda s: int(s) if s.isdigit() else 0))
                + ". cids set to 'own' WITHOUT an explicit history entry (lower provenance, "
                  "likely bulk/seed set): "
                + ", ".join(sorted([c for c in own_cids if c not in hist], key=int))
            ),
    }
    # flat cid -> label pairs at TOP LEVEL (this is what the consumer reads)
    for cid in sorted(mapping, key=int):
        out[cid] = mapping[cid]
    # audit-only detail (non-cid key, ignored by consumer)
    out["_detail"] = detail

    _atomic_write_json(DRAFT_PATH, out)
    print("wrote draft:", DRAFT_PATH)
    print("mapped:", len(mapping), "| own:", len(own_cids), "| company:", len(mapping) - len(own_cids))
    print("own cids:", own_cids)


if __name__ == "__main__":
    main()
