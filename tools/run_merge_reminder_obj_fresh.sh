#!/usr/bin/env bash
# Wrapper dla at-joba przypomnienia o merge OBJ FRESH (~06.06, po werdykcie). Cd + venv + log.
set -u
cd /root/.openclaw/workspace/scripts/dispatch_v2 || exit 1
LOG=/root/.openclaw/workspace/scripts/logs/obj_fresh_merge_reminder_atrun.log
{
  echo "===== merge_reminder run $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  /root/.openclaw/venvs/dispatch/bin/python tools/merge_reminder_obj_fresh.py
} >> "$LOG" 2>&1
