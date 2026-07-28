# Route-order parity monitor — systemd spec and legacy retirement

**Status:** source-only candidate, 2026-07-28. Installation, daemon-reload,
enablement and any live write require a separate owner ACK. This document does
not authorize them.

## Contract

`tools/route_order_live_parity_check.py` checks the actual chain:

`one orders/plans snapshot -> route_order canon ->
courier_orders.build_view_from_snapshots (explicit RouteConfig) -> faithful
projection of the stop contract consumed by Kotlin RouteLogic.buildSteps`.

It reuses `tests/golden/route_order_corpus.json`; it does not introduce a
second WB3 golden. Since ADR-010, `stop_id` plus `order_ids` are authoritative
and per-order committed must match the source order byte-for-byte. The monitor
must not reconstruct membership from coordinates, restaurant names or a local
time threshold.

| verdict | exit | meaning |
|---|---:|---|
| `OK` | 0 | at least one qualifying active bag; 100% DTO coverage; full parity |
| `EXPECTED_NO_DATA` | 3 | no qualifying active bag; legal, but explicitly not success |
| `BROKEN` | 1 | route mismatch |
| `BROKEN` | 2 | import/read/coverage/result-write failure |
| `CONFIG_DRIFT` | 4 | running courier-api route flags differ from golden |

Every result contains a heartbeat (`observed_at_utc`, `run_id`, coverage
denominator/numerator), mismatch/error counts, and `open_gates_line`. Courier
identifiers in artifacts are SHA-256 correlations, not raw IDs.

## Proposed unit (do not install without ACK)

Target: `/etc/systemd/system/dispatch-route-order-parity.service`

```ini
[Unit]
Description=Ziomek route-order parity: canon -> /orders DTO -> Kotlin projection
After=courier-api.service
Documentation=file:/root/.openclaw/workspace/scripts/dispatch_v2/docs/ROUTE_ORDER_PARITY_MONITOR_SYSTEMD_SPEC.md

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/root/.openclaw/workspace/scripts
Environment=PYTHONPATH=/root/.openclaw/workspace/scripts
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/root/.openclaw/venvs/dispatch/bin/python /root/.openclaw/workspace/scripts/dispatch_v2/tools/route_order_live_parity_check.py --result-path /root/.openclaw/workspace/dispatch_state/route_order_live_parity_verdict.json
SuccessExitStatus=3
TimeoutStartSec=120
Nice=10
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/root/.openclaw/workspace/dispatch_state
StandardOutput=append:/root/.openclaw/workspace/scripts/logs/route_order_live_parity.log
StandardError=append:/root/.openclaw/workspace/scripts/logs/route_order_live_parity.log
```

The dispatch venv is sufficient: the successor does not import panel
`fleet_state` or panel flags. It resolves `courier-api.service` `MainPID` with
`systemctl show`, then reads only the four named route flags from
`/proc/<MainPID>/environ`. Failure to resolve/read that process is
`BROKEN`/`INFRA_BROKEN`, exit 2. The monitor never falls back to its own env.

No flag must be copied into this unit. `courier_orders.RouteConfig` is the
single parser/owner; the monitor passes that explicit object to
`build_view_from_snapshots`. The public production `build_view` remains the I/O
wrapper and retains `earnings_history.record_day`; the snapshot builder has no
state reload and no writer.

Target: `/etc/systemd/system/dispatch-route-order-parity.timer`

```ini
[Unit]
Description=Run route-order end-to-end parity every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
AccuracySec=30s
Persistent=true
Unit=dispatch-route-order-parity.service

[Install]
WantedBy=timers.target
```

`SuccessExitStatus=3` keeps a quiet-night `EXPECTED_NO_DATA` from being treated
as a failed systemd execution while preserving the non-zero semantic exit.
Exit 1/2/4 remains failed and reaches the standard `OnFailure` mechanism if the
operator adds the canonical drop-in during the ACKed installation.

No service-file change is required for the config fix: the proposed unit
already runs as root, does not hide `/proc`, and starts after courier-api.
Installation preflight must nevertheless prove that its hardening permits
read-only access to `/proc/<MainPID>/environ`; a denial remains exit 2 rather
than a reason to duplicate flags into `Environment=`.

## ACKed installation checklist (not executed in this sprint)

1. Confirm off-peak window and owner ACK.
2. Verify source commit and clean deployment target; back up any same-named
   unit files.
3. Run `py_compile`, import smoke, focused tests and full canonical regression.
4. Copy the two reviewed unit definitions, then `systemctl daemon-reload`.
5. Run the service once manually. Inspect exit, JSON mode, result file mode
   `0600`, coverage heartbeat, `config_source`, all four effective flags and
   the `open_gates_line`. `CONFIG_DRIFT` blocks activation.
6. Enable/start only the timer. After two ticks verify `LastTriggerUSec`,
   result mtime, journal/log, exit and denominator.
7. Register the recurring observer in the process-gate/shadow-job inventory.

Rollback: `systemctl disable --now dispatch-route-order-parity.timer`, restore
backed-up unit files (or remove only these two newly installed units),
`systemctl daemon-reload`, then verify no later result heartbeat appears.
Do not remove the last verdict until the incident/handoff has captured it.

## Formal retirement of the legacy monitor

Legacy implementation:

- panel repo:
  `/root/.openclaw/workspace/nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py`;
- legacy units historically named `ziomek-time-route-monitor.service/.timer`
  and review timer `ziomek-time-route-review`;
- legacy JSONL:
  `/root/.openclaw/workspace/dispatch_state/ziomek_time_route_monitor.jsonl`.

The old code self-expired through `MONITOR_STOP_AFTER=2026-07-10`; repository
evidence records that the timer could remain installed after expiry. A
self-expired executable plus an installed timer is resurrection risk, not a
retirement mechanism.

After separate owner ACK, retirement must be mechanical:

1. Read-only inventory: `systemctl cat/status/is-enabled` for all three names,
   unit-file locations, timer list, process list and current legacy JSONL mtime.
2. Back up exact installed unit files and record hashes.
3. Disable/stop only the legacy timers; do not touch `courier-api`,
   `nadajesz-panel`, `dispatch-shadow` or `dispatch-telegram`.
4. Remove installed legacy unit files only after confirming the successor has
   two valid heartbeats; daemon-reload; verify unit names are `not-found`.
5. Keep legacy source as an explicitly archived artifact or delete it in the
   panel repository with its own reviewed commit. Never remove
   `MONITOR_STOP_AFTER` or rename/copy the script back into an active unit.
6. Add a ratchet test/inventory assertion that no installed/source unit
   references `ziomek_time_route_monitor` or `ziomek-time-route-*`.
7. Preserve the old JSONL read-only for the agreed retention period; never feed
   it into the new monitor as current evidence.

Resurrection rollback is intentionally not “re-enable legacy”. If the
successor fails, keep both monitors off, retain the BROKEN verdict, fix the
successor at source and re-run the ACK gate.
