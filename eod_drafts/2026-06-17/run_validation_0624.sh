#!/bin/bash
# One-shot walidacja objm-lexr6 D2-shadow (zaplanowana na 2026-06-24, bramka §6 specu).
# Uruchamia validate_objm_lexr6.py na ŻYWEJ telemetrii tego serwera, pisze wynik + powiadamia panel.
DIR=/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17
cd /root/.openclaw/workspace/scripts || exit 1
/root/.openclaw/venvs/dispatch/bin/python "$DIR/validate_objm_lexr6.py" > "$DIR/VALIDATION_RESULT_2026-06-24.txt" 2>&1
echo "objm-lexr6 validation ran $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$DIR/VALIDATION_RESULT_2026-06-24.txt"
