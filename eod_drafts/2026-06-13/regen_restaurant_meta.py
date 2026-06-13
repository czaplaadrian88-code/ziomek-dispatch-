#!/usr/bin/env python3
"""FAIL-04 regen — odświeżenie restaurant_meta.json (prep-variance) z aktualnego CSV.

Kontekst (AUDIT_FIX_PLAN 2026-06-10, FAIL-04 prep-variance meta):
  restaurant_meta.json (źródło prep_variance_min.median + flags.prep_variance_high
  czytane przez PREP_VARIANCE_ANOMALY w dispatch_pipeline.detect_prep_variance_anomaly,
  shadow) było policzone z CSV z 2026-04-12 (metadata.reference_date) → stale.
  To regen DANYCH (czas prep per restauracja), nie zmiana logiki/flagi.

Źródło (świeże, autonomicznie dostępne):
  /root/panel_history_new/*.csv — eksporty panelu gastro z polem `czas restauracji`
  (deklarowana gotowość) + `czas odbioru` (realny pickup). Pokrycie 2025-11-01 →
  2026-06-09, 56k unikalnych zleceń. TEN SAM format co oryginalny
  /tmp/zestawienie_all.csv (zniknął) — zgodny z tools/gap_fill_restaurant_meta.py.
  (To samo źródło używa już cron restaurant_prep_bias.py.)

Mechanizm:
  1. Scal 4 CSV deduplikując po `nr zlecenia` (najświeższy plik mtime wygrywa —
     wzorzec restaurant_prep_bias.read_csv_observations).
  2. Wywołaj tools/gap_fill_restaurant_meta.py na scalonym CSV → wylicza per
     restauracja prep_variance_min / waiting_time_sec / extension_min + flagi
     (low_confidence / chronically_late / prep_variance_high / unreliable /
     critical) + fleet_medians + fallbacks. Format wyjścia = 1:1 z obecnym
     restaurant_meta.json (ten sam generator).

Wyjście: eod_drafts/2026-06-13/restaurant_meta.regen.json (sandbox — NIE live).
Live restaurant_meta.json NIE jest dotykany przez tę sesję (podmiana = człowiek).

NIE zmienia logiki detekcji ani flagi ENABLE_PREP_VARIANCE_ANOMALY.

Użycie:
  python3 eod_drafts/2026-06-13/regen_restaurant_meta.py [--dry-run] \
      [--out eod_drafts/2026-06-13/restaurant_meta.regen.json]
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import subprocess
import sys
import tempfile

csv.field_size_limit(10_000_000)

CSV_GLOB = "/root/panel_history_new/*.csv"
GAP_FILL_TOOL = "/root/.openclaw/workspace/scripts/tools/gap_fill_restaurant_meta.py"
OUT_DEFAULT = ("/root/_auton_wt/tier01/dispatch_v2/eod_drafts/2026-06-13/"
               "restaurant_meta.regen.json")
EXPECTED_HDR_FIRST = "nr zlecenia"


def merge_dedup_csv(out_csv_path):
    """Scal wszystkie CSV deduplikując po nr zlecenia (najświeższy plik wygrywa).

    Zwraca (n_files_used, n_total_rows, n_unique). Pisze scalony CSV na out_csv_path.
    """
    paths = sorted(glob.glob(CSV_GLOB), key=lambda p: os.path.getmtime(p),
                   reverse=True)
    seen = set()
    header = None
    rows_out = []
    n_total = 0
    files_used = 0
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            hdr = reader.fieldnames or []
            if not hdr or (hdr[0] or "").strip().lower() != EXPECTED_HDR_FIRST:
                continue
            files_used += 1
            if header is None:
                header = hdr
            for row in reader:
                n_total += 1
                zid = (row.get("nr zlecenia") or "").strip()
                if not zid or zid in seen:
                    continue
                seen.add(zid)
                rows_out.append(row)
    if header is None:
        raise RuntimeError("brak CSV z poprawnym nagłówkiem w " + CSV_GLOB)

    with open(out_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for row in rows_out:
            w.writerow({k: row.get(k, "") for k in header})
    return files_used, n_total, len(rows_out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true",
                    help="scal CSV i odpal gap_fill --dry-run (raport, bez zapisu meta)")
    ap.add_argument("--keep-merged", action="store_true",
                    help="zostaw scalony CSV obok --out (debug)")
    args = ap.parse_args()

    merged_dir = os.path.dirname(args.out) or "."
    os.makedirs(merged_dir, exist_ok=True)
    fd, merged_csv = tempfile.mkstemp(dir=merged_dir, prefix="merged_panel_history_",
                                      suffix=".csv")
    os.close(fd)
    try:
        files_used, n_total, n_unique = merge_dedup_csv(merged_csv)
        print(f"=== FAIL-04 regen restaurant_meta ===")
        print(f"CSV scalony: {files_used} plików, {n_total} wierszy -> {n_unique} unikalnych")
        print(f"merged: {merged_csv}")
        print(f"--- gap_fill_restaurant_meta.py ---")

        cmd = [sys.executable, GAP_FILL_TOOL, "--csv", merged_csv, "--output", args.out]
        if args.dry_run:
            cmd.append("--dry-run")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"ERROR: gap_fill exit {rc}", file=sys.stderr)
            return rc
        if not args.dry_run:
            print(f"\nzapisano (sandbox): {args.out}")
        return 0
    finally:
        if not args.keep_merged and os.path.exists(merged_csv):
            os.unlink(merged_csv)


if __name__ == "__main__":
    sys.exit(main())
