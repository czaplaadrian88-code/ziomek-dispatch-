"""BUG E resolution tracker (2026-05-26).

Koreluje:
- decyzje Ziomka `verdict=KOORD` z reason `best_effort_*` (R6 breach / low_score)
  — Ziomek wycofał się, NIE wysłał propozycji, NIE dotknął panelu
- faktyczne `COURIER_ASSIGNED` events z audit_log po decyzji
  — Adrian/Bartek ręcznie przypisali w panelu

Output: `logs/bug_e_resolutions.jsonl` — per order:
  {ziomek_proposed: {cid, score, max_bag, breach_count, breach_orders, sum_bag, fifo},
   human_resolved: {cid, ts, source, delta_min},
   override: bool (ziomek_proposed_cid != human_resolved_cid),
   ziomek_ts, oid}

Idempotent: dedup po (oid, human_resolved_ts). Re-runs skipują zalogowane.

Usage:
  python3 -m dispatch_v2.tools.bug_e_resolution_tracker [--hours 24] [--dry-run]

Cwd: /root/.openclaw/workspace/scripts
Venv: /root/.openclaw/venvs/dispatch/bin/python
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

SHADOW_LOG = Path("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")
EVENTS_DB = Path("/root/.openclaw/workspace/dispatch_state/events.db")
RESOLUTION_LOG = Path("/root/.openclaw/workspace/scripts/logs/bug_e_resolutions.jsonl")

# Reasony Ziomka które = "wycofuję się, brak propozycji" (no-op w panelu).
# Ten tracker traktuje wszystkie 3 wspólnie jako sygnał R6/score breach.
BEST_EFFORT_REASON_PREFIXES = (
    "best_effort_r6_breach_v2",   # BUG E hotfix (2026-05-26)
    "best_effort_r6_breach",       # OBJ F3 (2026-05-18)
    "best_effort_low_score",       # V3.28 P3-D3 (2026-05-11)
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
_log = logging.getLogger("bug_e_resolution_tracker")


def parse_iso_utc(iso: str) -> Optional[datetime]:
    """Parse ISO timestamp → tz-aware UTC datetime. None na błąd."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def is_best_effort_koord(rec: dict) -> bool:
    """Czy rec to verdict=KOORD z reason w best_effort_* family."""
    if rec.get("verdict") != "KOORD":
        return False
    reason = rec.get("reason") or ""
    return any(reason.startswith(p) for p in BEST_EFFORT_REASON_PREFIXES)


def extract_ziomek_proposed(rec: dict) -> dict:
    """Wyciąg z rec[best] tego co Ziomek BY zaproponował."""
    best = rec.get("best") or {}
    return {
        "courier_id": best.get("courier_id"),
        "name": best.get("name"),
        "score": best.get("score"),
        "max_bag_time_min": best.get("max_bag_time_min"),
        "sum_bag_time_min": best.get("sum_bag_time_min"),
        "fifo_violations": best.get("fifo_violations"),
        "r5_pickup_detour_total_km": best.get("r5_pickup_detour_total_km"),
        # R6 metric variants
        "r6_max_bag_time": best.get("r6_max_bag_time") or best.get("r6_max_bag_time_min"),
        "objm_r6_breach_max_min": best.get("objm_r6_breach_max_min"),
    }


def collect_ziomek_koord_records(hours_back: int) -> Dict[str, dict]:
    """Skanuje shadow_decisions.jsonl z ostatnich N godzin.
    Zwraca dict {order_id: rec} — najnowsza KOORD per oid (gdy >1, wygrywa later ts).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    by_oid: Dict[str, dict] = {}
    if not SHADOW_LOG.exists():
        _log.warning(f"shadow log not found: {SHADOW_LOG}")
        return by_oid
    n_total = 0
    n_match = 0
    with SHADOW_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_iso_utc(rec.get("ts"))
            if ts is None or ts < cutoff:
                continue
            n_total += 1
            if not is_best_effort_koord(rec):
                continue
            n_match += 1
            oid = str(rec.get("order_id") or "")
            if not oid:
                continue
            prev = by_oid.get(oid)
            if prev is None:
                by_oid[oid] = rec
            else:
                prev_ts = parse_iso_utc(prev.get("ts"))
                if prev_ts is None or ts > prev_ts:
                    by_oid[oid] = rec
    _log.info(f"scan shadow log: {n_total} records last {hours_back}h, {n_match} best_effort KOORD, {len(by_oid)} unique oids")
    return by_oid


def lookup_first_assignment_after(
    conn: sqlite3.Connection, oid: str, after_ts: datetime,
) -> Optional[dict]:
    """Pierwszy COURIER_ASSIGNED dla oid po after_ts (>=). Z audit_log (pełna historia).
    Zwraca {courier_id, created_at_dt, source}, None gdy brak.
    """
    after_iso = after_ts.isoformat()
    row = conn.execute(
        """SELECT courier_id, created_at, payload
           FROM audit_log
           WHERE event_type='COURIER_ASSIGNED'
             AND order_id=?
             AND created_at >= ?
           ORDER BY created_at ASC LIMIT 1""",
        (oid, after_iso),
    ).fetchone()
    if row is None:
        return None
    cid, created_at, payload_str = row
    src = None
    try:
        payload = json.loads(payload_str) if payload_str else {}
        src = payload.get("source")
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "courier_id": cid,
        "ts": created_at,
        "source": src,
    }


def load_existing_signatures() -> Set[Tuple[str, str]]:
    """Zbiera (oid, human_resolved_ts) z istniejącego output dla dedup."""
    sigs: Set[Tuple[str, str]] = set()
    if not RESOLUTION_LOG.exists():
        return sigs
    with RESOLUTION_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            oid = str(rec.get("oid") or "")
            hr = rec.get("human_resolved") or {}
            hr_ts = str(hr.get("ts") or "")
            if oid and hr_ts:
                sigs.add((oid, hr_ts))
    return sigs


def append_resolution(rec: dict, dry_run: bool) -> None:
    if dry_run:
        _log.info(f"DRY RUN — would write: {json.dumps(rec, ensure_ascii=False)}")
        return
    RESOLUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RESOLUTION_LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24,
                        help="Ile godzin wstecz skanować shadow log (default 24)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nie pisz do output, tylko loguj decyzje")
    args = parser.parse_args()

    if not EVENTS_DB.exists():
        _log.error(f"events.db not found: {EVENTS_DB}")
        return 1

    ziomek_koords = collect_ziomek_koord_records(args.hours)
    if not ziomek_koords:
        _log.info("no best_effort KOORD records — exit clean")
        return 0

    existing_sigs = load_existing_signatures()
    _log.info(f"dedup signatures already logged: {len(existing_sigs)}")

    conn = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True, timeout=10)
    n_written = 0
    n_pending = 0
    n_skipped_dedup = 0
    try:
        for oid, rec in ziomek_koords.items():
            ziomek_ts = parse_iso_utc(rec.get("ts"))
            if ziomek_ts is None:
                continue
            assignment = lookup_first_assignment_after(conn, oid, ziomek_ts)
            if assignment is None:
                n_pending += 1
                continue
            sig = (oid, str(assignment["ts"]))
            if sig in existing_sigs:
                n_skipped_dedup += 1
                continue
            proposed = extract_ziomek_proposed(rec)
            human_ts = parse_iso_utc(assignment["ts"])
            delta_min: Optional[float] = None
            if human_ts is not None:
                delta_min = round((human_ts - ziomek_ts).total_seconds() / 60.0, 2)
            human_resolved = {
                "courier_id": str(assignment["courier_id"]) if assignment["courier_id"] else None,
                "ts": assignment["ts"],
                "source": assignment["source"],
                "delta_min": delta_min,
            }
            override = (
                proposed["courier_id"] is not None
                and human_resolved["courier_id"] is not None
                and str(proposed["courier_id"]) != human_resolved["courier_id"]
            )
            out = {
                "tracker_ts": datetime.now(timezone.utc).isoformat(),
                "oid": oid,
                "ziomek_ts": rec.get("ts"),
                "ziomek_reason": rec.get("reason"),
                "ziomek_proposed": proposed,
                "human_resolved": human_resolved,
                "override": override,
            }
            append_resolution(out, args.dry_run)
            n_written += 1
    finally:
        conn.close()

    _log.info(f"done: written={n_written} pending={n_pending} dedup_skipped={n_skipped_dedup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
