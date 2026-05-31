#!/usr/bin/env bash
# Wrapper dla at-joba meta-werdyktu merge-remindera OBJ FRESH (~5 min po #100). Cd + venv + log.
set -u
cd /root/.openclaw/workspace/scripts/dispatch_v2 || exit 1
LOG=/root/.openclaw/workspace/scripts/logs/obj_fresh_merge_reminder_meta_atrun.log
{
  echo "===== merge_reminder_meta run $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  /root/.openclaw/venvs/dispatch/bin/python tools/merge_reminder_meta_verdict.py
} >> "$LOG" 2>&1
