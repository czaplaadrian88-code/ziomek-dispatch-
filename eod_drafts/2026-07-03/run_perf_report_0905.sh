#!/bin/bash
# tmux14 zad.1 (2026-07-03): pomiar perf po flipie ENABLE_PERF_LAZY_MEMBERS (00:25).
# Odpalany z at — READ-ONLY, wynik do pliku. Sędzia: baseline p50 852 / p95 1939 /
# ogon>1500 13,1% (FALA1_perfslo_raport.md). Okno od 00:30 = tylko po-flipowe decyzje.
OUT=/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-03/perf_budget_report_0905utc.txt
cd /root/.openclaw/workspace/scripts || exit 1
{
  echo "=== perf_budget_report — okno 2026-07-03T00:30 -> teraz ($(date -u -Is)) ==="
  /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/perf_budget_report.py \
    --since 2026-07-03T00:30:00 \
    --out /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-03/perf_budget_report_0905utc.json \
    --stdout-json
} > "$OUT" 2>&1
