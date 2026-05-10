#!/bin/bash
# MP-#4 fix-it (2026-05-08 morning): ExecStopPost helper dla oneshot services.
# Aktualizuje cron_health.json last_success TYLKO gdy SERVICE_RESULT=success.
# Wywoływane jako: ExecStopPost=/path/record_oneshot_success.sh %n
#
# Pre-fix problem: bootstrap script set last_success=now ONCE, watchdog `is_stale`
# fires false-positive po expected_max_silence_h (czasowka 6min, plan-recheck 12min,
# shift-notify 6min) bo NIC nie aktualizowało last_success per tick. Adrian dostał
# 3 spurious Telegram alerty 08:31 UTC.
#
# Post-fix: per success tick ExecStopPost wywołuje record_run_success (idempotent),
# next watchdog tick (4h) widzi fresh last_success → 0 stale → 0 alerts.

if [ "$SERVICE_RESULT" != "success" ]; then
    exit 0
fi

UNIT="$1"
if [ -z "$UNIT" ]; then
    exit 0
fi

/root/.openclaw/venvs/dispatch/bin/python -c "
from dispatch_v2.observability import cron_health
cron_health.record_run_success('$UNIT', unit_type='cron_timer')
" 2>&1
