"""#21 Opcja C + BUG-D Faza 2c support — shadow_decisions.jsonl ground-truth enricher.

Cross-cutting backfill cron: dla każdej shadow decision (verdict PROPOSE) zbiera
faktyczne outcomes z `events.db audit_log`:
  - COURIER_ASSIGNED — kiedy human zatwierdził kuriera (acceptance latency)
  - COURIER_PICKED_UP — kiedy kurier rzeczywiście dotarł do restauracji (real travel)
  - COURIER_DELIVERED — kiedy dostarczył (real bag execution)

Output: `drive_min_enriched.jsonl` append-only, single source of truth dla:
  - #21 Sprint 1 drive_min calibration empirical bias (predicted vs actual)
  - BUG-D Faza 2c v2 multiplier validation per distance bin

Idempotent: stateful offset file `shadow_enricher_state.json` zapamiętuje last
processed shadow_decisions.jsonl byte offset + last enriched oid set (dedup).

NIE modyfikuje source files. Pure read+enrich+append pipeline.

CLI:
  python3 -m dispatch_v2.tools.shadow_outcome_enricher [--hours N] [--dry-run]

Triggered cron: dispatch-shadow-enrichment.service (oneshot) + .timer (5 min).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# ── Paths ────────────────────────────────────────────────────────────────
SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
ENRICHED_LOG = "/root/.openclaw/workspace/dispatch_state/drive_min_enriched.jsonl"
STATE_FILE = "/root/.openclaw/workspace/dispatch_state/shadow_enricher_state.json"
WARSAW_OFFSET_HOURS = 2  # CEST summer (BUG-D scope: 28.05.2026 = CEST)


# ── State management (idempotency) ───────────────────────────────────────
def load_state() -> dict:
    """Stateful offset + processed_oids set dla dedup."""
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
            s["processed_oids"] = set(s.get("processed_oids", []))
            return s
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_offset": 0, "processed_oids": set()}


def save_state(state: dict) -> None:
    """Atomic write: tempfile + fsync + rename (Lekcja #14 pattern)."""
    serializable = {
        "last_offset": state["last_offset"],
        "processed_oids": sorted(state["processed_oids"]),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    dir_path = os.path.dirname(STATE_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_path, prefix=".shadow_enricher_state.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(serializable, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── audit_log queries ────────────────────────────────────────────────────
def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def query_outcomes(conn: sqlite3.Connection, oid: str) -> dict:
    """Zwraca dict z ALL relevant audit_log events dla order_id."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT event_type, courier_id, payload, created_at
        FROM audit_log
        WHERE order_id = ?
          AND event_type IN ('COURIER_ASSIGNED', 'COURIER_PICKED_UP', 'COURIER_DELIVERED')
        ORDER BY created_at
        """,
        (str(oid),),
    )
    out = {
        "assigned": None,
        "picked_up": None,
        "delivered": None,
    }
    for event_type, courier_id, payload_raw, created_at in cur.fetchall():
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except (ValueError, TypeError):
            payload = {}
        entry = {
            "courier_id": str(courier_id) if courier_id is not None else None,
            "created_at_utc": created_at,
            "payload": payload,
        }
        if event_type == "COURIER_ASSIGNED" and out["assigned"] is None:
            out["assigned"] = entry  # first assignment (human accept)
        elif event_type == "COURIER_PICKED_UP" and out["picked_up"] is None:
            out["picked_up"] = entry
        elif event_type == "COURIER_DELIVERED" and out["delivered"] is None:
            out["delivered"] = entry
    return out


# ── Enrichment logic ─────────────────────────────────────────────────────
def enrich_record(decision: dict, outcomes: dict) -> Optional[dict]:
    """Combine shadow decision + audit_log outcomes → enriched record.

    Returns None gdy core ground truth (picked_up) brak — order pending lub
    nigdy nie zaakceptowany. Caller skip + try later.
    """
    picked_up = outcomes.get("picked_up")
    if picked_up is None:
        return None  # not ready yet

    best = decision.get("best") or {}
    if not best:
        return None  # shadow record bez proposed best (KOORD verdict)

    decision_ts = _parse_iso(decision.get("ts"))
    if decision_ts is None:
        return None

    pickup_ts = _parse_iso(picked_up.get("created_at_utc"))
    if pickup_ts is None:
        return None

    assigned = outcomes.get("assigned") or {}
    assigned_ts = _parse_iso(assigned.get("created_at_utc"))
    delivered = outcomes.get("delivered") or {}
    delivered_ts = _parse_iso(delivered.get("created_at_utc"))

    applied_cid = (assigned.get("courier_id")
                   or picked_up.get("courier_id")
                   or "")
    proposed_cid = str(best.get("courier_id") or "")
    overridden = bool(proposed_cid and applied_cid and proposed_cid != applied_cid)

    actual_kurier_to_pickup_min = (pickup_ts - decision_ts).total_seconds() / 60.0
    actual_assign_to_pickup_min = (
        (pickup_ts - assigned_ts).total_seconds() / 60.0
        if assigned_ts else None
    )
    actual_pickup_to_delivery_min = (
        (delivered_ts - pickup_ts).total_seconds() / 60.0
        if delivered_ts else None
    )

    pred_travel = best.get("travel_min")
    pred_drive = best.get("drive_min")

    return {
        "order_id": decision.get("order_id"),
        "decision_ts": decision.get("ts"),
        "verdict": decision.get("verdict"),
        "reason": decision.get("reason"),
        "predicted": {
            "courier_id": proposed_cid,
            "name": best.get("name"),
            "target_pickup_at": best.get("target_pickup_at"),
            "travel_min": pred_travel,
            "drive_min": pred_drive,
            "km_to_pickup": best.get("km_to_pickup"),
            "pos_source": best.get("pos_source"),
            "traffic_v2_shadow_route": best.get("traffic_v2_shadow_route"),
        },
        "actual": {
            "applied_courier_id": str(applied_cid) if applied_cid else None,
            "kurier_overridden": overridden,
            "assigned_ts": assigned.get("created_at_utc"),
            "pickup_ts": picked_up.get("created_at_utc"),
            "delivered_ts": delivered.get("created_at_utc"),
            "actual_kurier_to_pickup_min": round(actual_kurier_to_pickup_min, 2),
            "actual_assign_to_pickup_min": (
                round(actual_assign_to_pickup_min, 2)
                if actual_assign_to_pickup_min is not None else None
            ),
            "actual_pickup_to_delivery_min": (
                round(actual_pickup_to_delivery_min, 2)
                if actual_pickup_to_delivery_min is not None else None
            ),
        },
        "delta": {
            # Most useful: assign_to_pickup vs predicted (po acceptance)
            "assign_to_pickup_vs_travel_min": (
                round(actual_assign_to_pickup_min - pred_travel, 2)
                if (actual_assign_to_pickup_min is not None
                    and pred_travel is not None)
                else None
            ),
            "assign_to_pickup_vs_drive_min": (
                round(actual_assign_to_pickup_min - pred_drive, 2)
                if (actual_assign_to_pickup_min is not None
                    and pred_drive is not None)
                else None
            ),
            # Decision→pickup includes acceptance latency (less clean)
            "decision_to_pickup_vs_travel_min": (
                round(actual_kurier_to_pickup_min - pred_travel, 2)
                if pred_travel is not None else None
            ),
        },
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Pipeline ─────────────────────────────────────────────────────────────
def iter_shadow_records(path: str, start_offset: int, max_records: int):
    """Yield (offset, record) pairs from start_offset. Tracks bytes consumed."""
    try:
        f = open(path, "r", encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return
    try:
        f.seek(start_offset)
        count = 0
        while count < max_records:
            line_start = f.tell()
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            yield (line_start + len(line.encode("utf-8")) + 1, rec)
    finally:
        f.close()


def append_enriched(records: list[dict]) -> None:
    """Append enriched records (one JSON per line)."""
    if not records:
        return
    with open(ENRICHED_LOG, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(hours: int, dry_run: bool = False) -> dict:
    """Main pipeline. Returns stats dict."""
    state = load_state()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    enriched_batch: list[dict] = []
    stats = {
        "shadow_scanned": 0,
        "outside_cutoff": 0,
        "skipped_dedup": 0,
        "skipped_no_best": 0,
        "skipped_not_ready": 0,
        "enriched": 0,
    }

    conn = sqlite3.connect(EVENTS_DB)
    try:
        # Strategy: re-scan recent window (last `hours`) every run. Dedup po oid
        # bo enrichment is idempotent — overwrite OK byłby acceptable, ale dedup
        # skipuje re-write tych samych entries. last_offset = max bytes consumed
        # dla incremental fast path; fallback do full scan when offset stale.
        last_offset = state.get("last_offset", 0)
        # Safety: jeśli plik się skurczył (rotate), reset offset
        try:
            file_size = os.path.getsize(SHADOW_LOG)
            if last_offset > file_size:
                last_offset = 0
        except FileNotFoundError:
            return stats

        max_records = 50000  # safety cap per run
        new_offset = last_offset
        for offset, rec in iter_shadow_records(SHADOW_LOG, last_offset, max_records):
            stats["shadow_scanned"] += 1
            new_offset = offset

            ts = _parse_iso(rec.get("ts"))
            if ts is None or ts < cutoff:
                stats["outside_cutoff"] += 1
                continue
            oid = rec.get("order_id")
            if not oid:
                continue
            if str(oid) in state["processed_oids"]:
                stats["skipped_dedup"] += 1
                continue
            if not rec.get("best"):
                stats["skipped_no_best"] += 1
                continue

            outcomes = query_outcomes(conn, oid)
            enriched = enrich_record(rec, outcomes)
            if enriched is None:
                stats["skipped_not_ready"] += 1
                continue

            enriched_batch.append(enriched)
            state["processed_oids"].add(str(oid))
            stats["enriched"] += 1
    finally:
        conn.close()

    # Trim processed_oids to recent window (avoid unbounded growth)
    # Keep oids from records seen in last `hours * 2` window only
    if len(state["processed_oids"]) > 50000:
        state["processed_oids"] = set(list(state["processed_oids"])[-25000:])

    state["last_offset"] = new_offset

    if not dry_run:
        append_enriched(enriched_batch)
        save_state(state)

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default 24)")
    p.add_argument("--dry-run", action="store_true", help="No file writes")
    args = p.parse_args(argv)

    stats = run(args.hours, dry_run=args.dry_run)
    summary = (
        f"shadow_enricher: scanned={stats['shadow_scanned']} "
        f"enriched={stats['enriched']} "
        f"skipped_dedup={stats['skipped_dedup']} "
        f"skipped_no_best={stats['skipped_no_best']} "
        f"skipped_not_ready={stats['skipped_not_ready']} "
        f"outside_cutoff={stats['outside_cutoff']} "
        f"dry_run={args.dry_run}"
    )
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
