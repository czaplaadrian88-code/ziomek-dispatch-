#!/bin/bash
# Cron wrapper: epaka_fetcher (przesyłki CSV + prowizje DO WYPŁATY od-do). Domyślnie ostatnie 30 dni.
# Schedule np.: 0 6 * * *  /root/.openclaw/workspace/scripts/dispatch_v2/tools/epaka_fetch_cron.sh
TOOLS=/root/.openclaw/workspace/scripts/dispatch_v2/tools
OUT=/root/.openclaw/workspace/scripts/dispatch_v2/dispatch_state/epaka_data
mkdir -p "$OUT"
echo "=== $(date -u +%FT%TZ) epaka fetch ($*) ===" >> "$OUT/fetch.log"
/usr/bin/python3 "$TOOLS/epaka_fetcher.py" "$@" >> "$OUT/fetch.log" 2>&1
rc=$?; echo "exit=$rc" >> "$OUT/fetch.log"
[ $rc -ne 0 ] && echo "[ALERT] epaka fetch exit=$rc (re-seed sesji / OCR?) $(date -u +%FT%TZ)" >> "$OUT/fetch.log"
exit $rc
