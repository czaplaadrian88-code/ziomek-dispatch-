# FOUNDATION dataset — `decisions_outcomes_loadbucketed.jsonl`

Built `2026-06-29` for Ziomek dispatch calibration. **Read-only on repo.** One row per `order_id`.

## What it is
Per-order join of decision features + predicted times + ACTUAL outcome + fleet-load bucket +
courier class + bag size. **2827 rows** (= unique orders in the 13-day rich decision log).

## Spine + join
- **Spine** = `dispatch_state/backfill_decisions_outcomes_v1.jsonl` (13d, richest decision features),
  deduped to last `decision_ts` per `order_id` (4856 rows → 2827 orders).
- LEFT-joined by `order_id` string (eta_calibration keyed by `oid`), last-ts-wins dedup each:
  - `scripts/logs/shadow_decisions.jsonl` — loadgov load metrics, bag, `alternatives[]`, tier_best (overlap 12.2%)
  - `dispatch_state/decision_outcomes.jsonl` — clean UTC r6_actual/breach, picked/delivered (34.6%)
  - `scripts/logs/sla_log.jsonl` — master outcome, NAIVE-Warsaw stamps (99.3%)
  - `dispatch_state/gps_delivery_truth.jsonl` — physical delivered UTC + geofence (21%)
  - `dispatch_state/eta_calibration_log.jsonl` — predicted/real delivery min + bag_size + is_bundle (99.3%)
  - `dispatch_state/ready_at_log.jsonl` — declared_ready / arrived / picked (prep, dwell) (96.7%)
  - `dispatch_state/drive_min_calibration_log_v2.jsonl` — raw/calibrated drive min, peak_window (100%)

## Timezone handling (rule #1) — VERIFIED
`sla_log` & `eta_calibration_log` `picked_up_at`/`delivered_at` are **NAIVE WARSAW** (mostly
`YYYY-MM-DD HH:MM:SS`; a minority are already ISO+offset and handled as such). All other
timestamps (decision-side, decision_outcomes, gps_truth, ready_at, predicted_delivered_at)
are UTC/aware. Naive-Warsaw → UTC via `ZoneInfo("Europe/Warsaw")` (DST-aware; June = CEST = UTC+2).
**Spot-check passed:** for orders present in both sla (naive) and decision_outcomes (UTC),
naive-treated-as-UTC was +119.7..119.9 min ahead of true UTC; after Warsaw→UTC conversion the
two systems agreed sub-minute (≈0.1-0.3 min). Every cross-log time math runs on UTC.

## KEY metric — `eta_error_min`
`eta_error_min = actual_delivered_at_utc − predicted_delivered_at_utc` (minutes).
**Positive = engine OPTIMISTIC** (order delivered LATER than the promised time).
- This is the **timestamp-based, span-safe** definition = the headline metric in
  `eta_calibration_logger.py` (`delivered_at − predicted_delivered_at`).
- NOTE on the prompt: it wrote the formula as "predicted − actual" but labelled
  "positive = optimistic". Those are inconsistent; we followed the load-bearing **semantic
  label** (positive = optimistic = under-promised), which is `actual − predicted`.
- Actual delivered prefers GPS physical > decision_outcomes > backfill_outcome > sla(naive→UTC)
  (`delivered_source` records which). `eta_error_min_gps` = same vs GPS physical only (cross-check).
- `eta_error_dur_min` (secondary) = `actual_delivery_min − predicted_delivery_min` (durations).
  **Do not use as primary**: `predicted_delivery_min` (= `per_order_delivery_times`) spans a
  ~11 min LONGER window than pickup→delivery, so this column carries a systematic span offset.

## load_bucket
- `luzno` ewma≤2.0 (or pool_feasible≥3) · `srednio` ewma 2.0–2.7 (or pool_feasible==2) ·
  `ciasno` ewma 2.7–3.5 · `niedobor` ewma>3.5 (or pool_feasible≤1).
- `load_source` = `ewma` (345 rows, shadow only) or `pool_feasible` (2482 rows, fallback).

## bag_size semantics
`bag_size` (source `eta_cal`, 2808/2827 rows) = the logger's `_bag_final` = `r6_bag_size + 1`,
i.e. **1-indexed, includes the order itself**: `bag_size=1` is a solo order, `bag_size≥2` is a
bundle (`is_bundle = bag_size≥2`). There is no `0` bucket. (The shadow fallback `bag_size_before`
is 0-indexed but was needed for only 1 row.)
- **Caveat:** pool_feasible can't separate srednio/ciasno → pool-derived mid rows are labelled
  `srednio`; `ciasno` therefore exists only for ewma rows (92 total). Prefer continuous
  `pool_feasible` / `load_ewma` over the 4-bucket label for modelling (see findings).

## Columns (selected)
`order_id, decision_ts_utc, decision_hour_warsaw/utc, restaurant, verdict, is_propose, is_koord,
proposed_courier_id, proposed_score, courier_id_final, auto_route, tier, pos_source,
load_ewma, load_now, load_active_couriers, load_active_orders, fleet_bag_avg, pool_feasible,
pool_total, load_bucket, load_source, bag_size, bag_source, is_bundle, czasowka, best_effort,
shift_end_edge, score_margin, predicted_travel_min, predicted_drive_min, predicted_r6_max_bag_min,
predicted_delivery_min, predicted_delivered_at_utc, raw_drive_min, calibrated_drive_min,
picked_up_at_utc, pickup_source, delivered_at_utc, delivered_source, actual_delivery_min(+_source,
+_gps), assign_to_delivery_min, assign_to_pickup_min, outcome_status, sla_ok, r6_actual_min,
r6_breach, gps_confidence, gps_dwell_min, eta_error_min, eta_error_min_gps, eta_error_dur_min,
pickup_slip_min, pickup_lateness_do_min, dwell_actual_min, prep_bias_min, wait_min, has_shadow,
has_gps_truth, n_alternatives, order_type, eta_cal_bucket, drive_peak_window, alternatives[]`

- `pickup_slip_min` = picked_up − declared_ready (ready_at, UTC). `dwell_actual_min` =
  picked_up − arrived (only where arrival logged, ~25%). `prep_bias_min`, `wait_min` from ready_at.
- `alternatives[]` (only ewma/shadow rows) = per-candidate {courier_id, score, feasibility,
  best_effort, travel_min, travel_min_cal, drive_min, bundle_bonus, bonus_penalty_sum,
  r6_max_bag_time_min, r6_bag_size, pos_source} — for the weight-track.

## Sort order
Ascending `decision_ts_utc`.

## Companion files
`cell_counts.txt` (load×tier×bag cell counts), `build_dataset.py` (builder),
`analyze.py` / `analyze2.py` / `finalize.py` (validation).
