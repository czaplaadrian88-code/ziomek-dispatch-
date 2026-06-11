#!/bin/bash
# Jednorazowy restart panel-watchera PO PEAKU (aktywacja redecide-on-pickup,
# tag redecide-on-pickup-2026-06-11). Telegram z wynikiem.
systemctl restart dispatch-panel-watcher
sleep 30
ACTIVE=$(systemctl is-active dispatch-panel-watcher)
ERRS=$(journalctl -u dispatch-panel-watcher --since "-3 min" --no-pager | grep -ciE "error|traceback")
FLAG=$(systemctl show dispatch-panel-watcher -p Environment | grep -c REDECIDE_ON_PICKUP=1)
cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python -c "
from dispatch_v2.telegram_utils import send_admin_alert
send_admin_alert('panel-watcher restart po peaku (redecide-on-pickup): active=$ACTIVE, journal_err=$ERRS, flaga_w_env=$FLAG. Rollback: usun ostatnia linie unified-route-f3.conf + daemon-reload + restart.')
"
