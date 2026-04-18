"""F2.2 C4: Speed tier tracker — nightly job candidate.

Per spec UPDATE A (F2.2 Sprint Plan):
  - Filter bundle_size=1 (singleton orders only)
  - Rolling window: last 30 days (configurable)
  - p90 delivery_min dla singletonów = "solo speed baseline"
  - Classify:
      FAST:    p90 <= 25 min
      NORMAL:  25 < p90 <= 32 min
      SAFE:    p90 > 32 min
      INSUFFICIENT_DATA: < 30 singletons

Input:  /tmp/wave_audit_dataset_merged_2026-04-18.db
        (fallback: /root/.openclaw/workspace/docs/wave_audit_outputs/2026-04-18/...db)
Output: /root/.openclaw/workspace/dispatch_state/courier_speed_tiers.json

Standalone executable. C5 wave_scoring will read the JSON (future).
"""
import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

DB_PATHS = [
    "/tmp/wave_audit_dataset_merged_2026-04-18.db",
    "/root/.openclaw/workspace/docs/wave_audit_outputs/2026-04-18/wave_audit_dataset_merged_2026-04-18.db",
]
OUT_PATH = Path("/root/.openclaw/workspace/dispatch_state/courier_speed_tiers.json")

WINDOW_DAYS = 30
BUNDLE_GAP_MIN = 8
MIN_SINGLETONS_FOR_TIER = 30
FAST_P90_MAX = 25.0
NORMAL_P90_MAX = 32.0


def percentile(sorted_list, q):
    if not sorted_list:
        return None
    n = len(sorted_list)
    if n == 1:
        return sorted_list[0]
    k = (n - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_list[int(k)]
    return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)


def classify_tier(p90: "float|None", n_singletons: int) -> str:
    if n_singletons < MIN_SINGLETONS_FOR_TIER or p90 is None:
        return "INSUFFICIENT_DATA"
    if p90 <= FAST_P90_MAX:
        return "FAST"
    if p90 <= NORMAL_P90_MAX:
        return "NORMAL"
    return "SAFE"


def find_db_path() -> "str|None":
    for p in DB_PATHS:
        if Path(p).exists():
            return p
    return None


def compute_tiers(db_path: str, window_days: int = WINDOW_DAYS):
    """For each courier, compute singleton p90 + tier per spec UPDATE A."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Rolling window cutoff (use data_warsaw string comparison)
    latest = cur.execute(
        "SELECT MAX(data_warsaw) FROM orders WHERE status='doręczone'"
    ).fetchone()[0]
    if latest is None:
        con.close()
        return {}, None, None
    latest_dt = datetime.fromisoformat(latest)
    cutoff = (latest_dt - timedelta(days=window_days)).date().isoformat()

    rows = list(cur.execute("""
        SELECT kurier, kurier_anon, t_odbior_warsaw, delivery_min
        FROM orders
        WHERE status='doręczone'
          AND delivery_min IS NOT NULL
          AND delivery_min_suspicious=0
          AND is_holiday=0
          AND t_odbior_warsaw IS NOT NULL
          AND substr(data_warsaw, 1, 10) >= ?
        ORDER BY kurier ASC, t_odbior_warsaw ASC
    """, (cutoff,)))
    con.close()

    by_courier = defaultdict(list)
    for r in rows:
        by_courier[r["kurier"]].append(
            (datetime.fromisoformat(r["t_odbior_warsaw"]), r["delivery_min"], r["kurier_anon"])
        )

    tiers = {}
    for courier, events in by_courier.items():
        events.sort(key=lambda x: x[0])
        # Bundle detection (gap > 8 min)
        bundles = []
        cur_bundle = [events[0]] if events else []
        for i in range(1, len(events)):
            gap = (events[i][0] - events[i - 1][0]).total_seconds() / 60.0
            if gap > BUNDLE_GAP_MIN:
                bundles.append(cur_bundle)
                cur_bundle = [events[i]]
            else:
                cur_bundle.append(events[i])
        if cur_bundle:
            bundles.append(cur_bundle)
        singletons = sorted([b[0][1] for b in bundles if len(b) == 1])
        n_singletons = len(singletons)
        p90 = percentile(singletons, 0.9) if n_singletons >= MIN_SINGLETONS_FOR_TIER else None
        tier = classify_tier(p90, n_singletons)
        tiers[courier] = {
            "kurier_anon": events[0][2] if events else courier,
            "tier": tier,
            "p90_singleton_delivery_min": round(p90, 2) if p90 is not None else None,
            "n_singletons": n_singletons,
            "n_total_orders": len(events),
            "n_bundles": len(bundles),
        }
    return tiers, cutoff, latest


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def main(dry_run: bool = False):
    db_path = find_db_path()
    if db_path is None:
        print(f"ERROR: no DB found in {DB_PATHS}")
        return 2
    print(f"=== Speed tier tracker (F2.2 C4) ===")
    print(f"DB: {db_path}")
    print(f"Window: {WINDOW_DAYS} days")

    tiers, cutoff, latest = compute_tiers(db_path, WINDOW_DAYS)
    if not tiers:
        print("No tier data computed (empty DB or all filtered out)")
        return 1
    print(f"Cutoff: {cutoff}  Latest: {latest}")
    print(f"Couriers computed: {len(tiers)}")

    # Summary
    summary = {"FAST": 0, "NORMAL": 0, "SAFE": 0, "INSUFFICIENT_DATA": 0}
    for rec in tiers.values():
        summary[rec["tier"]] += 1
    print(f"Summary: {summary}")

    # Top-10 by n_total_orders
    top = sorted(tiers.items(), key=lambda kv: -kv[1]["n_total_orders"])[:10]
    print(f"\nTop-10 couriers:")
    print(f"  {'courier':<18} {'anon':<16} {'tier':<20} {'p90':>8} {'n_sing':>7} {'n_tot':>7}")
    for courier, rec in top:
        p90 = rec["p90_singleton_delivery_min"]
        p90_s = f"{p90:.1f}" if p90 is not None else "—"
        print(f"  {courier:<18} {rec['kurier_anon']:<16} {rec['tier']:<20} "
              f"{p90_s:>8} {rec['n_singletons']:>7} {rec['n_total_orders']:>7}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": db_path,
        "window_days": WINDOW_DAYS,
        "cutoff_date": cutoff,
        "latest_in_db": latest,
        "thresholds": {
            "fast_p90_max": FAST_P90_MAX,
            "normal_p90_max": NORMAL_P90_MAX,
            "min_singletons_for_tier": MIN_SINGLETONS_FOR_TIER,
            "bundle_gap_min": BUNDLE_GAP_MIN,
        },
        "tiers": tiers,
        "summary": summary,
    }

    if dry_run:
        print(f"\n[DRY-RUN] would write {OUT_PATH}")
        return 0

    atomic_write_json(OUT_PATH, payload)
    print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(main(dry_run=args.dry_run))
