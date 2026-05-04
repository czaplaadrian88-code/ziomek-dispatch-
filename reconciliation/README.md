# Reconciliation Service — TASK 2 Część B

**Created**: 2026-05-04
**Status**: Code complete. NOT DEPLOYED. Awaiting ACK GATE 2 + migration plan.

## Purpose

Strukturalna infrastruktura która wykrywa rozjazd między `events.db` a
`orders_state.json` (panel reality) i bezpiecznie resync'uje terminal events
do events.db. Defense in depth dla phantom problem (70 phantom orderów wykrytych
w TASK 1 root cause investigation).

## Modules

| Module | Purpose |
|---|---|
| `phantom_detector.py` | Pure detection — events.db vs state cross-ref |
| `auto_resync.py` | Safety-gated emit terminal events |
| `reconcile_log.py` | JSONL structured logging + summary query |
| `reconcile_worker.py` | Main orchestrator + CLI for systemd |
| `health_endpoint.py` | `/health/reconcile` snapshot helper |

## Discrepancy types

| Type | Definition | Auto-resync? |
|---|---|---|
| **PHANTOM** STATE_TERMINAL | events.db active, state terminal | yes (if age >4h) |
| **PHANTOM** MISSING_FROM_STATE | events.db active, missing from state | yes (if age >4h) |
| **GHOST** | events.db terminal, state active | NEVER auto, alert only |

## Flags (flags.json)

```json
{
  "RECONCILIATION_ENABLED": false,                  // master switch
  "RECONCILIATION_AUTO_RESYNC_ENABLED": false,      // alert-only when false
  "RECONCILIATION_INTERVAL_MIN": 30,                 // systemd timer cadence
  "RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS": 4,     // young phantoms alert only
  "RECONCILIATION_HARD_CAP_PER_RUN": 5,             // safety stop
  "RECONCILIATION_TELEGRAM_ALERT_ENABLED": false,
  "RECONCILIATION_LOOKBACK_DAYS": 30
}
```

**ALL DEFAULTS FALSE** per Adrian's hard rule. Włączasz ręcznie po smoke test.

## Architectural decisions (Z3)

### Why NOT fresh panel_client API call?

Memory `feedback_panel_session_singleton`: standalone Python panel_client probes
inwalidują CSRF running watchera (HTTP 419). Reconciliation worker uruchamia
się w osobnym procesie (oneshot timer), więc fresh login = collision risk.

**Decision**: use `orders_state.json` (panel_watcher already-synced) jako
proxy panel reality. Panel_watcher reconcile cycle pulls panel co 60s, więc
state.json jest fresh w ramach minuty.

### Trade-off

Orders ARCHIVED z panelu (>24-72h post-delivery) nie są pullowane przez
panel_watcher. Reconciliation widzi je jako MISSING_FROM_STATE i infers
COURIER_DELIVERED. To jest correct (97% empirical pattern z TASK 1) —
panel archive orderów następuje TYLKO post-delivery.

Edge case: order NOT yet archived ale ALREADY missing from state z innego
powodu (manual cleanup, deploy artifact). Reconciliation by infered DELIVERED
zamiast właściwego CANCELLED. Mitigacja: hard_cap 5/run + telegram alert
catch'es batch anomaly.

## Deployment plan (Część C, post ACK GATE 2)

```bash
# 1. Install systemd files
sudo cp dispatch_v2/reconciliation/systemd/*.service /etc/systemd/system/
sudo cp dispatch_v2/reconciliation/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 2. Smoke test — manual run, dry-run mode
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.reconciliation.reconcile_worker --dry-run

# 3. Set RECONCILIATION_ENABLED=true (alert-only mode, AUTO_RESYNC=false)
# Wait for first scheduled run (~30 min after enable)
# Review reconciliation_log.jsonl

# 4. Adrian + Cloud Claude review listy 70 phantom

# 5. Migration run (one-time):
#    Set RECONCILIATION_HARD_CAP_PER_RUN=80 + AUTO_RESYNC_ENABLED=true
#    Wait 1 cycle → all phantoms resynced
#    Revert HARD_CAP=5

# 6. Enable timer
sudo systemctl enable --now dispatch-state-reconcile.timer
```

## Tests

```bash
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tests/test_reconciliation.py
# Expected: 12/12 PASS
```

## Rollback

```bash
# Disable cleanly
echo '{"RECONCILIATION_ENABLED": false}' >> flags.json  # pseudocode — edit JSON properly
sudo systemctl disable --now dispatch-state-reconcile.timer

# Code rollback (if needed)
git revert <commit-sha>
```
