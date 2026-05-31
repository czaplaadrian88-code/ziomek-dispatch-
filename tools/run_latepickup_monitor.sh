#!/bin/bash
# Wrapper dla at-job: monitor lunch-peaku 01.06 (4 zmiany selekcji 31.05).
# Okno 09:00-12:00 UTC = 11:00-14:00 Warsaw. READ-ONLY. Telegram digest + raport md.
LOG=/root/.openclaw/workspace/scripts/logs/latepickup_monitor_peak_2026_06_01.log
echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
cd /root/.openclaw/workspace/scripts || exit 1
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.monitor_latepickup_peak_2026_06_01 \
  --since-iso 2026-06-01T09:00:00+00:00 \
  --until-iso 2026-06-01T12:00:00+00:00 \
  >> "$LOG" 2>&1
echo "=== exit $? ===" >> "$LOG"
