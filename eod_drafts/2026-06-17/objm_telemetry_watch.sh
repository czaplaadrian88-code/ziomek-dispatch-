#!/bin/bash
# Wait until the first post-flip decision writes objm_lexr6_* (telemetry live) OR an error appears.
F=/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl
for i in $(seq 1 40); do
  err=$(journalctl -u dispatch-shadow.service --since "6 min ago" 2>/dev/null | grep -c "OBJM_LEXR6_SHADOW failed")
  if [ "${err:-0}" -gt 0 ]; then echo "OBJM_LEXR6_ERROR detected=$err — CHECK LOG"; exit 2; fi
  found=$(tail -n 10 "$F" 2>/dev/null | grep -c "objm_lexr6_best_cid")
  if [ "${found:-0}" -gt 0 ]; then echo "OBJM_LEXR6_TELEMETRY_LIVE: $found of last 10 records carry objm_lexr6_*"; exit 0; fi
  sleep 12
done
echo "OBJM_LEXR6_AWAITING: flag ON, no PROPOSE decision yet in window (telemetry will collect as orders arrive)"
