#!/bin/bash
# B2 LIVE spot-check — lunch-peak 2026-06-29 (09-12 UTC = 11-14 Warsaw). at-job, odpala 12:10 UTC.
cd /root/.openclaw/workspace/scripts/dispatch_v2
/root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-06-28/b2_spotcheck.py 2026-06-29T09:00 2026-06-29T12:00 \
  > eod_drafts/2026-06-28/b2_spotcheck_lunch_verdict_2026-06-29.txt 2>&1
echo "B2 lunch spot-check done $(date -u) -> eod_drafts/2026-06-28/b2_spotcheck_lunch_verdict_2026-06-29.txt" \
  >> /root/.openclaw/workspace/scripts/logs/b2_spotcheck_lunch.log
