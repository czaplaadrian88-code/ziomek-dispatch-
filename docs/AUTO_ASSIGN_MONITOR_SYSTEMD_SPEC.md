# AUTO-assign monitor — systemd source specification

**Status:** source-only candidate, 2026-07-28. Installation, daemon-reload,
enablement and live writes require a separate owner ACK. Nothing in this file
authorizes AUTO or changes `ENABLE_AUTO_ASSIGN`.

## Contract

`tools/auto_assign_monitor.py` is an independent process. Every cycle (at most
30 seconds apart) it atomically writes
`/var/lib/ziomek-authority/state/monitor-heartbeat.json` with `ts`, `pid` and
the complete `checks` result. It reads the card/latch state, the executor
counter state and rotation-aware `shadow_decisions`; a counter divergence or
an `auto_executed` receipt not covered by executor state writes the monotonic
AUTO-OFF latch. It never assigns, retimes or edits an order.

The executor refuses and latches `monitor_heartbeat_stale` when the heartbeat
is missing, malformed, from the future, or older than 60 seconds.

## Proposed unit (do not install without ACK)

Target: `/etc/systemd/system/dispatch-auto-assign-monitor.service`

```ini
[Unit]
Description=Ziomek AUTO-canary independent authority monitor
After=dispatch-shadow.service
Documentation=file:/root/.openclaw/workspace/scripts/dispatch_v2/docs/AUTO_ASSIGN_MONITOR_SYSTEMD_SPEC.md

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/root/.openclaw/workspace/scripts
Environment=PYTHONPATH=/root/.openclaw/workspace/scripts
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/root/.openclaw/venvs/dispatch/bin/python /root/.openclaw/workspace/scripts/dispatch_v2/tools/auto_assign_monitor.py --interval 30
Restart=on-failure
RestartSec=2
TimeoutStopSec=15
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadOnlyPaths=/root/.openclaw/workspace/scripts/logs /root/.openclaw/workspace/dispatch_state
ReadWritePaths=/var/lib/ziomek-authority/state
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`auto_assign_state.json` is listed read-only; the only normal monitor write is
the heartbeat, while an alarm may additionally update the card latch through
the same authority-state lock and atomic writer as the executor.

## ACKed installation checklist (not executed here)

1. Confirm owner ACK and an off-peak window.
2. Back up any same-named installed unit; verify exact source commit.
3. Run `py_compile`, import smoke, focused tests and the full canonical suite.
4. Install the reviewed unit, `daemon-reload`, then run one `--once` dry cycle
   with AUTO still OFF.
5. Verify heartbeat mode `0600`, age below 60 seconds, PID ownership, `checks`
   verdict, service PID/NRestarts and journal.
6. Enable the service only after the executor and monitor source are deployed
   together. Keep `ENABLE_AUTO_ASSIGN=false` until T1–T7 are independently
   accepted.

Rollback: set the AUTO killswitch false first; stop/disable only this unit,
restore its backup (or remove only the newly installed unit), daemon-reload,
and verify that the executor now refuses with `monitor_heartbeat_stale`.
