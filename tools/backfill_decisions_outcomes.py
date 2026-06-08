#!/usr/bin/env python3
"""backfill_decisions_outcomes — Q1 simpler: join decyzji Ziomka z faktycznym czasem dostawy.

Dla każdego entry w `learning_log.jsonl` (ostatnie N dni; default 14) dolicza
`actual_pickup_min` i `actual_delivery_min` z `dispatch_state/snapshots/orders_state_*.json`
(union wszystkich snapshotów). Output: derived artifact (overwrite) w dispatch_state/ (G2) do dalszej analizy.

Cel: czy override przez operatora dawało measurable outcome benefit (delivery_min < no-override)?
Czy auto_route="AUTO" decyzje były faktycznie szybciej dostarczone niż "ACK"/"ALERT"?

ZERO production touch (nie dotyka live pipeline). Read snapshots + learning_log,
write derived artifact (overwrite) do dispatch_state/ — konsumowany przez faza7 daily KPI.

Użycie:
  python3 -m dispatch_v2.tools.backfill_decisions_outcomes
  python3 -m dispatch_v2.tools.backfill_decisions_outcomes --days 7
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEARNING_LOG = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
SNAPSHOT_GLOB = "/root/.openclaw/workspace/dispatch_state/snapshots/orders_state_*.json"
# G2 (2026-05-29): /tmp → dispatch_state. /tmp ephemeral → po czyszczeniu daily
# faza7 timer + OnFailure dawałby fałszywy alarm. Konsumenci (faza7_daily_kpi,
# rebuild_courier_whitelist) wskazują tę samą ścieżkę. ACK Adrian 2026-05-29.
DEFAULT_OUT = Path("/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _open_maybe_gz(p):
    """Otwórz plik tekstowo, transparentnie obsługując .gz (zrotowane logi)."""
    if str(p).endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return open(p, encoding="utf-8", errors="replace")


def _learning_log_files_in_window(cutoff_dt):
    """Żywy learning_log + zrotowane siblingi (.1, .2.gz, ...) w oknie --days.

    FIX 2026-06-08: logrotate (/etc/logrotate.d/dispatch-v2, size 100M + copytruncate)
    truncuje żywy plik co ~tydzień → backfill --days 14 widział tylko bieżący ogon
    (o 04:00 = pusto, noc bez zleceń) → 'Brak danych' → łańcuch retro/A2 padał, feed
    courier_reliability.json zamarzał (A2 reliability soft-score live od 06-07 jechał
    na stale danych). Dołączamy zrotowane pliki, których mtime (czas rotacji = koniec
    zawartości) >= cutoff; gdy mtime < cutoff cała treść jest starsza od okna → pomijamy.
    Per-line filtr ts<cutoff dotnie resztę. Newest→oldest po numerze rotacji.
    """
    files = [str(LEARNING_LOG)]  # żywy zawsze (mtime = teraz)
    rotated = []
    for p in glob.glob(str(LEARNING_LOG) + ".*"):
        m = re.search(r"\.(\d+)(\.gz)?$", p)
        if not m:
            continue
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)
        except OSError:
            continue
        if mt >= cutoff_dt:
            rotated.append((int(m.group(1)), p))
    rotated.sort()  # .1, .2, .3 rosnąco = newest→oldest
    files.extend(p for _, p in rotated)
    return files


def _iter_learning_lines(log_files):
    """Yield linie z listy plików learning_log (żywy + zrotowane, w tym .gz)."""
    for p in log_files:
        if not os.path.exists(p):
            continue
        try:
            with _open_maybe_gz(p) as f:
                for line in f:
                    yield line
        except OSError as e:
            sys.stderr.write(f"[backfill] pomijam {p}: {e!r}\n")
            continue


def load_snapshots() -> dict[str, dict]:
    files = sorted(glob.glob(SNAPSHOT_GLOB))
    orders: dict[str, dict] = {}
    for sf in files:
        try:
            with open(sf) as f:
                d = json.load(f)
        except Exception:
            continue
        for oid, ord_data in d.items():
            if not isinstance(ord_data, dict):
                continue
            existing = orders.get(oid)
            # Prefer delivered records, then richest history
            if existing is None:
                orders[oid] = ord_data
            elif existing.get("status") != "delivered" and ord_data.get("status") == "delivered":
                orders[oid] = ord_data
            elif len(ord_data.get("history", []) or []) > len(existing.get("history", []) or []):
                orders[oid] = ord_data
    return orders


def extract_outcome(order: dict) -> dict:
    hist = order.get("history") or []
    assigned_first = None
    assigned_last = None
    picked_last = None
    delivered_last = None
    for h in hist:
        at = _parse_iso(h.get("at"))
        if not at:
            continue
        ev = h.get("event")
        if ev == "COURIER_ASSIGNED":
            if assigned_first is None:
                assigned_first = at
            assigned_last = at
        elif ev == "COURIER_PICKED_UP":
            picked_last = at
        elif ev == "COURIER_DELIVERED":
            delivered_last = at

    out = {
        "status": order.get("status"),
        "courier_id_final": order.get("courier_id"),
        "assigned_first_ts": assigned_first.isoformat() if assigned_first else None,
        "picked_up_ts": picked_last.isoformat() if picked_last else None,
        "delivered_ts": delivered_last.isoformat() if delivered_last else None,
    }
    if assigned_first and picked_last:
        out["assign_to_pickup_min"] = round((picked_last - assigned_first).total_seconds() / 60.0, 2)
    if picked_last and delivered_last:
        out["pickup_to_delivery_min"] = round((delivered_last - picked_last).total_seconds() / 60.0, 2)
    if assigned_first and delivered_last:
        out["assign_to_delivery_min"] = round((delivered_last - assigned_first).total_seconds() / 60.0, 2)
    return out


def backfill(days: int, out_path: Path) -> dict:
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff = cutoff_dt.isoformat()
    snapshots = load_snapshots()
    sys.stderr.write(f"[backfill] snapshots union: {len(snapshots)} orders\n")
    log_files = _learning_log_files_in_window(cutoff_dt)
    sys.stderr.write(
        f"[backfill] learning_log w oknie {days}d: {[Path(p).name for p in log_files]}\n"
    )

    stats = {
        "n_decisions_total": 0,
        "n_with_order_id": 0,
        "n_matched_snapshot": 0,
        "n_delivered": 0,
        "n_skipped_no_decision": 0,
    }
    n_written = 0
    with out_path.open("w") as outf:
        for line in _iter_learning_lines(log_files):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts = entry.get("ts", "")
            if ts < cutoff:
                continue
            stats["n_decisions_total"] += 1
            if "decision" not in entry:
                stats["n_skipped_no_decision"] += 1
                continue
            decision = entry["decision"] or {}
            oid = entry.get("order_id") or decision.get("order_id")
            if not oid:
                continue
            stats["n_with_order_id"] += 1
            order = snapshots.get(str(oid))
            outcome = extract_outcome(order) if order else None
            if order:
                stats["n_matched_snapshot"] += 1
                if order.get("status") == "delivered":
                    stats["n_delivered"] += 1

            best = decision.get("best") or {}
            arc = decision.get("auto_route_context") or {}
            row = {
                "order_id": str(oid),
                "decision_ts": decision.get("ts") or ts,
                "action_event_ts": ts,
                "action": entry.get("action"),
                "feedback": entry.get("feedback"),
                "verdict": decision.get("verdict"),
                "auto_route": decision.get("auto_route"),
                "auto_route_reason": decision.get("auto_route_reason"),
                "restaurant": decision.get("restaurant"),
                "proposed_courier_id": entry.get("proposed_courier_id") or best.get("courier_id"),
                "proposed_score": entry.get("proposed_score") if entry.get("proposed_score") is not None else best.get("score"),
                "predicted_travel_min": best.get("travel_min"),
                "predicted_drive_min": best.get("drive_min"),
                "predicted_r6_max_bag_min": best.get("r6_max_bag_time_min"),
                "tier": arc.get("auto_route_tier_best"),
                "pos_source": arc.get("auto_route_pos_source_best"),
                "pool_feasible": arc.get("auto_route_pool_feasible"),
                "pool_total": arc.get("auto_route_pool_total"),
                "score_margin": arc.get("auto_route_score_margin"),
                "czasowka": arc.get("auto_route_czasowka"),
                "best_effort": arc.get("auto_route_best_effort"),
                "shift_end_edge": arc.get("auto_route_shift_end_edge"),
                "actual_courier_id": entry.get("actual_courier_id"),
                "outcome": outcome,
            }
            outf.write(json.dumps(row, ensure_ascii=False) + "\n")
            n_written += 1
    stats["n_written"] = n_written
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    stats = backfill(args.days, args.out)
    print(json.dumps(stats, indent=2))
    print(f"wrote: {args.out}")


if __name__ == "__main__":
    main()
