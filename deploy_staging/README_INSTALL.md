# FALA1 watchdog-close — staged systemd drop-ins (install by coordinator, за ACK)

STAGING ONLY. Nothing here is applied to the live host. 🔴 enable/daemon-reload = coordinator, за ACK Adriana.

## Files (10 drop-ins, mirror /etc paths)
- 3 timer OnCalendar anchors (durability, finding A):
  `dispatch-watchdog.timer.d/oncalendar.conf` (4h), `dispatch-delivered-integrity.timer.d/oncalendar.conf` (20 min), `dispatch-state-panel-monitor.timer.d/oncalendar.conf` (10 min)
- cod-weekly bliźniak parity (finding B): `dispatch-cod-weekly.service.d/{onfailure,cron_health_success,resource_limits}.conf`
- false-"failed" oneshot recorders: `dispatch-{cod-panel-ingest,downstream-crosscheck,retro-learning}.service.d/cron_health_success.conf` + `dispatch-retro-learning.service.d/onfailure.conf`

## Install sequence (за ACK Adriana; off-peak)
1. Copy the staged tree onto the host (preserves paths):
   `cp -rv deploy_staging/etc/systemd/system/* /etc/systemd/system/`
2. `systemctl daemon-reload`
3. Verify merges (no double-fire; expected results are in each .conf header):
   `systemctl cat dispatch-watchdog.timer dispatch-delivered-integrity.timer dispatch-state-panel-monitor.timer`
   `systemctl cat dispatch-cod-weekly.service`
   `systemctl list-timers --all | grep -E 'watchdog|delivered-integrity|state-panel-monitor'`  (each ONE next-elapse)
4. Register thresholds + cod-weekly in the ledger (idempotent, threshold-only):
   `PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.cron_health --sync-thresholds`
5. Seed the 3 verified-healthy false-failed units (clears frozen "failed"; they self-refresh via ExecStopPost thereafter):
   `... --record-success dispatch-cod-panel-ingest.service` (repeat for downstream-crosscheck.service, retro-learning.service)
6. Supervised confirm (no spam): `... --dry-run`  → expect would_alert_stale=0.

## Rollback
`rm -rf` the added `*.d/` dirs (or the specific `.conf` files) → `systemctl daemon-reload`.
The base timers/services return to their prior definitions. Ledger thresholds are inert
without the watchdog acting on them; to also revert ledger edits restore a pre-deploy
copy of `dispatch_state/cron_health.json`.
