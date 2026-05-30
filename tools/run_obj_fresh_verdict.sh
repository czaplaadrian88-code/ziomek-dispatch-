#!/usr/bin/env bash
# Wrapper dla at-joba werdyktu OBJ FRESH +7d (2026-06-06). Cd + venv + log.
set -u
cd /root/.openclaw/workspace/scripts/dispatch_v2 || exit 1
LOG=/root/.openclaw/workspace/scripts/logs/obj_fresh_verdict_atrun.log
{
  echo "===== obj_fresh_verdict run $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  /root/.openclaw/venvs/dispatch/bin/python tools/obj_fresh_verdict_atrun.py
} >> "$LOG" 2>&1
