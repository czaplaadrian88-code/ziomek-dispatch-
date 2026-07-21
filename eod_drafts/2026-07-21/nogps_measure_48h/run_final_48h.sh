#!/bin/bash
# at#221 — finalny pomiar 48h cienia no-GPS v3 (READ-ONLY, zero zmian w kodzie/flagach)
cd /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-20/nogps_measure || exit 1
OUT=/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-21/nogps_measure_48h
python3 measure_nogps_shadow.py \
  --since 2026-07-19T23:39:21+00:00 --until 2026-07-21T23:39:21+00:00 \
  --target-cids 179,413 \
  --json-out "$OUT/report_final_48h.json" > "$OUT/run_output_final_48h.txt" 2>&1
echo "exit=$? done $(date -u +%FT%TZ)" >> "$OUT/run_output_final_48h.txt"
