# Sprint 2 Analytical Scripts

Offline root-cause analysis for the 2026-04-30 Sprint 1 + V3.19i deploys.
**Pure read-only. No live-service touch. No DB writes.**

## Files

| Script | Purpose |
|---|---|
| `_common.py` | Shared loader + Warsaw-time helpers |
| `sanity_checks.py` | Pre-flight (data presence, thresholds, lock state) |
| `data_inventory.py` | Counts since 11:05 deploy + readiness GREEN/YELLOW/RED |
| `tak_mystery.py` | Why TAK=0 last 48h: A/B/C/D scoring |
| `override_patterns.py` | 7 patterns from PANEL_OVERRIDE entries |
| `propose_uptime_analysis.py` | Uptime % per hour + stoppage windows + baseline compare |
| `report_builder.py` | Glues outputs into `data/sprint2_root_cause_<DATE>.md` |
| `run_all.sh` | One-shot evening wrapper |

## Usage

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2/sprint2_analysis
./run_all.sh
```

This runs:
1. `sanity_checks.py` (pre-flight)
2. `data_inventory.py`
3. `tak_mystery.py`
4. `override_patterns.py`
5. `propose_uptime_analysis.py`
6. `report_builder.py` (consumes logs/ → writes data/)

Output report: `data/sprint2_root_cause_<DATE>.md`

## Inputs

- `/root/.openclaw/workspace/dispatch_state/learning_log.jsonl` (read-only)
- `/root/.openclaw/workspace/dispatch_state/events.db` (read-only, only for sanity check)

## Time anchors

- Sprint 1 deploy:  **11:05 Warsaw** (09:05 UTC) 2026-04-30
- V3.19i deploy:    **11:49 Warsaw** (09:49 UTC) 2026-04-30
- Peak windows:     **11–14, 17–20** weekdays (saturday 16–21)

## Caveats / known design choices

- `decision.alternatives` proxy for TOP_N=16 — counts entries with len ≥ 12
  (Sprint 1 raised 4→16 but most pools are smaller; this is a presence flag,
  not a coverage metric).
- `panel_source` distinguishes `panel_diff` (override detected via diff)
  from `panel_reassign` (explicit reassign in panel UI).
- `propose-uptime` baseline = last 7 days, same hours; thin-data hours
  (early morning) get unreliable comparisons.
- TG_REASON expected sparse — operator workload in peak makes Telegram
  approval flow inherent bottleneck (per Adrian observation).
- score gap (P2) skips overrides where `actual_courier_id` is NOT in pool —
  this is itself a signal (off-pool reassign = scoring missed the right
  candidate entirely).

## Hard rules respected

- C.1 — Pure offline, zero touch live services ✓
- C.2 — Peak-hour development OK per Lekcja #34 ✓
- C.3 — Date prefix in run_all.sh stdout ✓
- C.4 — Sample-first dry-run validated on current 5h dataset ✓
