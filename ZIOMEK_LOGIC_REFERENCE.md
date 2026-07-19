# ZIOMEK — Logic & Calibration Reference

> **Purpose.** A single, self-contained, *current* description of how the Ziomek autonomous
> courier dispatcher decides — its pipeline, scoring weights, feasibility gates, dependencies,
> shadow tests, and ML layer — written so **another AI model can review it and advise what is
> worth recalibrating and where the logic has gaps**.
>
> **Audience.** An external LLM (and Adrian). Written in English for maximum model comprehension;
> Polish domain terms are preserved with glosses (*czasówka* = scheduled-time order, *worek* = the
> courier's current bag of orders, *elastyk* = flexible/ASAP order, *koordynator* = human dispatcher).
>
> **Provenance.** Generated 2026-06-21 by a read-only multi-agent audit of
> `/root/.openclaw/workspace/scripts/dispatch_v2/`. Every constant below was read from source;
> `file:line` citations are exact unless prefixed `~` (approximate region). This file is **not
> committed** and changes nothing in production — it is documentation only.
>
> **Why this file exists (tech-debt note).** The in-repo `dispatch_v2/CLAUDE.md` and
> `ZIOMEK_MASTER_KB.md` are *frozen snapshots from 2026-05-10*; the live source of truth has been
> the `memory/` directory plus the code. This document consolidates the **current** decision logic
> from the code itself.

### Legend — deployment status of every mechanic
| Tag | Meaning |
|---|---|
| 🟢 **LIVE** | Affects real dispatch decisions / Telegram proposals right now. |
| 🟡 **SHADOW** | Computed and logged (`shadow_decisions.jsonl`) but **does not** change the decision. |
| ⚪ **OFF** | Coded behind a flag that is currently `false`; no effect, no logging. |

---

> ## ⚠️ LIVE-STATE CORRECTION — 2026-06-23 (read before trusting any 🟢/🟡/⚪ tag below)
>
> A live-flags audit (2026-06-22/23; full `flags.json` + 3162 `shadow_decisions.jsonl` records) found
> several mechanics below **mis-tagged vs production**. `flags.json` wins over the module constant
> (`decision_flag`, `common.py:232`) and has drifted since this doc was generated. **Trust this block.**
> Full strategic checkpoint: `dispatch_v2/ZIOMEK_STRATEGIC_AUDIT_2026-06-23.md`.
>
> | Mechanic | tag below | **LIVE today** | effect |
> |---|---|---|---|
> | `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` | 🟢 | **⚪ OFF** | cold-food divergence no longer →KOORD |
> | `ENABLE_HARD_TIER_BAG_CAP` | ⚪ | **🟢 LIVE** | NEW hard reject: gold/std+ 6 / std 5 / slow,new 4 |
> | `ENABLE_FLEET_LOAD_GOVERNOR` | 🟡 | **🟢 LIVE** | −40 ranking delta (12.4% of proposals) |
> | `ENABLE_BUNDLE_SYNC_SPREAD` | 🟡 | **🟢 LIVE** | 0..−150 ranking delta (59.5% of proposals) |
> | `ENABLE_R5_PICKUP_DETOUR_PENALTY` | ⚪ | **🟢 LIVE** | −4.0/km over 0.5km |
> | `ENABLE_A2_RELIABILITY_SOFT_SCORE` | 🟡 | **🟢 LIVE** | reliability penalty coeff 60 |
> | `ENABLE_OBJ_COMMITTED_PICKUP_PENALTY` | — | **🟢 LIVE** | OR-Tools soft coeff 100, never INFEASIBLE |
> | `ENABLE_NO_GPS_EQUAL_TREATMENT` | — | **🟢 LIVE** | no_gps competes on raw score; `_demote_blind_empty` ~inert |
> | `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` | ⚪ | **SHADOW/OFF** (29.06, Sprint1 „bez kary przed zmianą") | ON → zeruje karę score pre_shift (`_apply_pre_shift_equal_gate`, oba źródła: stała V325 + gradient); „dotrze później" = clamp + R-LATE-PICKUP propozycja do restauracji, NIE ukryta kara; HARD-reject >30min-przed-zmianą zostaje. Default OFF=kara; flip po replayu+ACK |
> | `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` | — | **🟢 LIVE** | every quality→KOORD gate carries `and not _always_propose_on()` |
> | `ENABLE_CZASOWKA_CK_PASSIVE_GUARD` | — | **🟢 LIVE** (24.06, #483023) | czasówka: passive gastro `czas_kuriera` re-stamp (`panel_re_check`/`pre_proposal_recheck`) NIE zmienia committed; umówiony czas = `pickup_at` |
> | `ENABLE_PICKUP_TIME_MIRRORS_CK` | — | **🟢 LIVE** (24.06) | czasówka: `PICKUP_TIME_UPDATED` mirrors `pickup_at`→`czas_kuriera` (koordynator/restauracja, any direction) |
> | `ENABLE_ELASTYK_CK_NO_BACKWARD` | — | **🟢 LIVE** (24.06, opcja B) | elastyk: passive `czas_kuriera` change blocked tylko BACKWARD (forward = +15/lateness zostaje) |
> | `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` | — | **🟢 LIVE** (26.06 canary) | `picked_up_at`/`delivered_at` (Warsaw-naive) parsed as Warsaw not UTC w 4 miejscach `courier_resolver` (interp/recent-activity/ZOMBIE-guard/per-status) → ożywia predykcję pozycji no-GPS (interp 0/16984→żyje); OFF=legacy UTC |
> | `ENABLE_O2_READY_ANCHOR_SWEEP` | — | ⚪ OFF (build 27.06, flip po review 02.07) | O2 re-seq Faza 1: objektyw worka = **overage** (Σ max(0, age_ready−cap), READY-anchor) zamiast `sla_violations`(count, pickup-anchor) w `_select_best_with_tie_breaker`+`plan_recheck._sweep` + **cap-Z** twardy sufit świeżości niesionego (`max_carried_age`≤Z). overage z `per_order_delivery_times` (compute-always). Faza 2 `czas_late` odroczona (brak deadline OrderSim). Gate `feasibility:1135` anchor = osobna sub-flaga (objektyw-first). cap/cap-Z tunable z `under_z` 02.07. Commit `22ba058`+`fe233d1`; design `eod_drafts/2026-06-27/O2_SPRINT_PREP.md` |
>
> **Net (Adrian directive 2026-06-23 — full autonomy):** quality-driven KOORD escalation is deliberately
> disabled. R6 35-min is hard at the feasibility/candidate layer, **soft at the verdict layer** (20.6% of
> proposals ship breaching it). Safety invariant: LOADGOV/SYNCWORKA deltas are stripped from the KOORD
> gate-score (`_gate_score_excluding_ranking_deltas`, `dp:1975`) — they re-rank but never silence. ML = pure shadow.

## 1. What Ziomek is

Ziomek is an **autonomous, rule-based** dispatcher for NadajeSz (food + parcel delivery, Białystok):
~30 couriers, ~40 restaurants, 180–300 orders/day. It watches the restaurant panel
(`gastro.nadajesz.pl`), and for each new order computes **which courier should take it** and whether
to **PROPOSE** that courier to the Telegram group, auto-assign, or escalate to a human
**KOORD**(ynator).

- **Rule-based, not ML.** An ML-first design (LGBM PRIMARY) was **cancelled 2026-05-03**. All ML now
  runs in 🟡 SHADOW only (§11). The live decision is a transparent weighted score + hard feasibility
  gates + verdict gates.
- **Shadow-first culture.** Almost every new mechanic ships behind a flag in 🟡 SHADOW, is validated
  by replay/forward-validation over 7–14 days, and only then flipped 🟢 LIVE. Many flags below are
  intentionally still SHADOW/OFF — *do not recommend recalibrating an OFF mechanic without saying so.*
- **Cardinal principles (Z1/Z2/Z3):** Z1 autonomy ASAP, Z2 quality always (root-cause over patch),
  Z3 build for scale (config-driven, reversible, multi-tenant-ready).

### Core source files (size = complexity)
| File | Lines | Role |
|---|---|---|
| `dispatch_pipeline.py` | 6251 | **Hub.** `assess_order()` — the whole decision flow: candidate build, scoring assembly, selection, verdict gates. |
| `common.py` | 3362 | Shared constants, weights, traffic tables, tier matrices, flag loader (`load_flags`, `decision_flag`). |
| `scoring.py` | 288 | The **base** weighted score (`score_candidate`) + wait penalties. |
| `feasibility_v2.py` | 1336 | **Hard gates** (`check_feasibility_v2`) + soft metrics (R1/R5/R8, return-to-restaurant). |
| `route_simulator_v2.py` | ~1600 | TSP/route planning (OR-Tools + greedy), per-order ETAs, SLA counts. |
| `shadow_dispatcher.py` | 1662 | Runs the pipeline read-only; serializes decisions to `shadow_decisions.jsonl`. |
| `telegram_approver.py` | ~3500 | Renders proposals + buttons; **never restart without explicit ACK**. |
| `ml_inference.py` | ~1000 | LGBM shadow inference (single + two-model). |
| `auto_proximity_classifier.py` | ~700 | Faza-7 AUTO/ACK/ALERT autonomy classifier (shadow). |

---

## 2. Runtime topology

**Long-running services (🟢 active):** `dispatch-gps` (PWA), `dispatch-monitor-419` (panel-storm
detector), `dispatch-panel-watcher` (parses panel, emits events, owns `:8888/health`),
`assistant-telegram`. The actual decision worker is the **shadow dispatcher** (`dispatch-shadow`),
plus `dispatch-telegram` for proposal delivery.

**Timers (fire every 1–10 min):** `dispatch-czasowka` (scheduled-order scheduler T-60/T-50/T-40),
`dispatch-pending-pool`, `dispatch-plan-recheck` (5-min route re-check), `dispatch-shadow-enrichment`,
`dispatch-drtusz-bridge` + `dispatch-papu-bridge` (parcel/order bridges), `dispatch-eta-calibration`,
`dispatch-state-reconcile`, `dispatch-postpone-sweeper`, and several shadow/monitor timers.

**State & data (`/root/.openclaw/workspace/dispatch_state/`):**
`orders_state.json` (assignments), `courier_plans.json` (saved routes), `flags.json`
(`…/scripts/flags.json`, hot-reload feature flags), `rule_weights.json` (R1/R5/R8 penalty knobs,
**static, hand-tuned, no writer**), `restaurant_coords.json` (pickup coords cache),
`courier_tiers.json` (per-courier tier + caps), `eta_quantile_map.json` / `restaurant_prep_bias.json`
(shadow calibration maps), `courier_last_pos.json` (no-GPS rescue store).

**Logs (`…/scripts/logs/`):** `shadow_decisions.jsonl` (the decision record, §10), `shadow.log`,
`dispatch.log`, `route_simulator.log`, `eta_calibration_log.jsonl`.

**Venv:** `/root/.openclaw/venvs/dispatch/bin/python` (Python 3.12, ortools 9.15). **OSRM:** docker
`osrm-server` :5001.

---

## 3. Module dependency graph (internal imports)

```
dispatch_pipeline  ──> scoring, feasibility_v2, route_simulator_v2, common, ml_inference,
                       auto_proximity_classifier, chain_eta, osrm_client, bag_state, fleet_context,
                       calib_maps, pln_objective, traffic_v2_aggregator, state_machine, event_bus,
                       districts_data, pipeline_geometry, insertion_anchor, auto_assign_gate,
                       pending_queue_provider, telegram_utils
scoring            ──> common, geometry
feasibility_v2     ──> common, route_simulator_v2, route_metrics, calib_maps
route_simulator_v2 ──> common, same_restaurant_grouper
shadow_dispatcher  ──> dispatch_pipeline, common, courier_resolver, geocoding, telegram_utils
ml_inference       ──> common, district_reverse_lookup
common             ──> districts_data            (leaf)
```

`dispatch_pipeline` is the hub; `common` is the leaf everyone imports. Clean, acyclic among the
core decision modules. **Tech-debt:** `dispatch_pipeline.py` (6251 lines, one 3433-line function
`_assess_order_impl`) and `telegram_approver.py` are *god objects* — see §13.

---

## 4. The decision pipeline — `assess_order()`

Entry: `dispatch_pipeline.assess_order(order_event, fleet_snapshot, …)` → `_assess_order_impl()`
(`dispatch_pipeline.py:2754` / impl `:2818`). Returns a `PipelineResult(verdict, reason, best,
candidates, auto_route, …)`. `verdict ∈ {PROPOSE, AUTO, KOORD, SKIP}`.

**Ordered stages:**

1. **Setup** — parse order, validate pickup coords, build `new_order` (an `OrderSim`).
2. **SKIP gate** — if pickup coords missing or `(0,0)` → `verdict=SKIP, reason=no_pickup_geocode`
   (`~2917`). (Defends the *firmowe konto* free-text-address case.)
3. **Early-bird gate** — if `pickup_at − now ≥ EARLY_BIRD_THRESHOLD_MIN` (60, hot via flags) →
   `KOORD, reason=early_bird` (`~2972`). Too far in the future to assign now.
4. **Load governor** — measure fleet saturation (telemetry + a single de-duplicated "defensive mode"
   alert; the EWMA does not by itself change decisions).
5. **Per-courier evaluation** (`_v327_eval_courier`, `~3058`) — runs **in parallel**
   (ThreadPoolExecutor). For each courier: sanitize position, build bag `OrderSim`s, run
   `check_feasibility_v2` (§6), compute the route (`route_simulator_v2`), and assemble the score
   (§5). Produces a `Candidate` (`@dataclass`, `~2236`): `courier_id, name, score,
   feasibility_verdict ∈ {MAYBE, NO}, plan, metrics{…}, best_effort, …`.
6. **Selection** — keep `feasible = [c for c if verdict=="MAYBE"]`, sort + reorder (§5.4), pick `best`.
7. **Verdict gates** — a series of escalations to KOORD; else PROPOSE (§5.5).
8. **Fallbacks** — if `feasible == []`: *best-effort* path; else *solo fallback*; else final KOORD.

---

## 5. The scoring model

The final number that ranks couriers is built in **three layers**: a 0–100 **base score**, a large
set of additive **bonuses/penalties**, and (for some bundle cases) a **multiplier**. Then a
**layered sort + demotion** picks the winner, and **verdict gates** decide PROPOSE vs KOORD.

### 5.1 Base score — `scoring.score_candidate` (`scoring.py`)
Weighted sum of four 0–100 sub-scores (weights sum to 1.0):

```
base = s_dystans·0.30 + s_obciazenie·0.25 + s_kierunek·0.25 + s_czas·0.20      # scoring.py:22-25,222
```

| Sub-score | Formula | Range | Source |
|---|---|---|---|
| `s_dystans` (distance to pickup) | `100·exp(−road_km / 5.0)` → 0km=100, 5km=37, 10km=14, 15km=5 | 0–100 | `scoring.py:34`, `DIST_DECAY_KM=5.0` `:27` |
| `s_obciazenie` (bag load) | `bag≥5 → 0`; else `100·(1 − bag/5)` → 0=100,1=80,2=60,3=40,4=20 | 0–100 | `scoring.py:37-43`, `MAX_BAG_TSP_BRUTEFORCE=5` `common.py:300` |
| `s_kierunek` (direction fit) | empty bag → 100; else `100·(1 − angle°/180)` (angle between courier→bag-centroid and courier→pickup) | 0–100 | `scoring.py:45-48` |
| `s_czas` (oldest-in-bag age) | `100 − time_penalty`; `time_penalty = ((min−30)/5)^2.5·100` for 30–35 min, else 0 | 0–100 | `scoring.py:50-58`; `START=30, FULL=35` `:31-32` |

`road_km` prefers OSRM; the `scoring.py:206` `haversine·1.3` is a **fallback only when `road_km is
None`** — but the live pipeline **always passes `road_km`** (computed with ×1.37 at
`dispatch_pipeline.py:3408`, fed into `score_candidate` at `:3484`), so the 1.3 path is **dead in
production**. The live engine is therefore **consistent at 1.37** (`HAVERSINE_ROAD_FACTOR_BIALYSTOK`,
`common.py:367`; used in `osrm_client:373`, `feasibility_v2:109` pickup-reach gate,
`dispatch_pipeline:3408/3510/5495`). The **1.42** literal is **ML-shadow-only** (`ml_inference.py`,
intentional train/serve constant) — not a dispatch path. (Audit-corrected; see §13.2.)

**Wait penalties** (added per pickup in the plan, `scoring.py:61-186`):
- `compute_wait_penalty(wait)` — interpolated table `V327_WAIT_PENALTY_TABLE` (`common.py:2152`):
  `(20→0)(25→−10)(30→−30)(35→−90)(40→−150)(50→−400)(60→−700)`, `>60 → −1000` hard fallback
  (or continuous B3 gradient if `ENABLE_B3_WAIT_GRADIENT`, ⚪ OFF). Gated by `ENABLE_V327_WAIT_PENALTY` 🟢.
- `compute_wait_courier_penalty(wait, bag)` — only if `bag≥1` (food cooling): `≤5→0`, `6→−10`, then
  `−5/min` to 20, `>20 → HARD REJECT`. Constants `V3273_WAIT_COURIER_*` (`common.py`). 🟢
- `compute_idle_wait_soft_penalty(wait)` — empty-handed courier idling (`picked_up==0`): `>5min →
  (wait−5)·−4`, never rejects (N2, 2026-06-17). 🟢

### 5.2 Bonuses & penalties (assembled in `_v327_eval_courier`)
All terms are summed into the candidate score. **Penalties are ≤0, bonuses vary.** Exhaustive table
(value = constant; sign as applied):

> **Exact assembly (verified):** `bonus_penalty_sum` is summed at **`dispatch_pipeline.py:4537`** from
> 19 terms (`bonus_r6_soft_pen + bonus_r1_soft_pen + bonus_r5_soft_pen + bonus_r8_soft_pen +
> bonus_r9_stopover + bonus_r9_wait_pen + bonus_bug4_cap_soft + bonus_v325_pre_shift_soft +
> bonus_d2_stale_soft + bonus_v3273_wait_courier + bonus_r1_corridor + bonus_r5_detour +
> bonus_wave_clean + bonus_inter_wave_deadhead + bonus_state_panel_mismatch + bonus_coordinator_idle
> + bonus_r_paczki_flex + bonus_r_return_rest + bonus_carry_chain_penalty`). Then at **`:4580`**:
> `final_score = score_result["total"] + bundle_bonus + timing_gap_bonus + wave_bonus +
> bonus_penalty_sum + bonus_bug2_continuation + v324a_extension_penalty`. The 🟡 SHADOW deltas
> (`bonus_*_shadow_delta`) are added at `:4584+` **only when their flag is ON** — that is the LIVE vs
> SHADOW boundary in code.

| Term | Value / formula | When | Status | Source |
|---|---|---|---|---|
| `bonus_l1` | +25 (×drop-proximity factor) | same restaurant already in bag | 🟢 | `~dp:3555,3592` |
| `bonus_l2` | `max(0, 20 − dist_km·10)` | cross-restaurant pickup proximity | 🟢 | `~dp:3594` |
| `bonus_r4` (free stop) | 0–150, tier curve ×1.5 | delivery near an existing bag drop | 🟢 | `~dp:3598-3615` |
| `timing_gap_bonus` | +25 (≤5min) / +15 (≤10) / +5 (≤15) / −3·min (>15) / −2·min (early) | courier free-at vs pickup-ready alignment | 🟢 | `~dp:3664` |
| `wave_bonus` (post-shift return) | `POST_WAVE_BONUS_FAST=15` (free≤20) / `SLOW=8` (free≤30) | all-picked-up bag, non-GPS return | 🟢 | `common.py:708-709` |
| `bonus_bug2_continuation` | 0–30 (`BUG2_WAVE_CONTINUATION_BONUS=30`) | new pickup falls inside projected free wave | 🟢 | `common.py:1529` |
| `bonus_r9_stopover` | `−8·bag_size` (`STOPOVER_SCORE_PER_STOP=8`) | every stop (parking + handoff) | 🟢 | `common.py:699` |
| `bonus_r9_wait_pen` | quadratic wait table (above) | predicted restaurant wait | 🟢 | `scoring.py:61` |
| `bonus_r6_soft_pen` | `−(max_bag_time−30)·per_min` + extra in 32–35 "danger zone" | bag time 30–35 min (soft R6) | 🟢 | `~dp:627` |
| `bonus_r8_soft_pen` | `−(span−7)·3` + `−viol_min·1.5` | pickup span >7 min | 🟢 | `rule_weights.json` R8 |
| `bonus_r1_soft_pen` | `−viol_km·8.0` over 8 km | delivery spread (R1) | 🟢 | `rule_weights.json` |
| `bonus_r1_corridor` | +15 (cos>0.85) … −35 (cos<0, orthogonal/opposite) | delivery directionality (avg pairwise cosine) | 🟢 | `~dp:4110` |
| `bonus_r5_detour` | 0 / −5 / −15 / −40 by detour km bucket (`R5_pickup_per_km=−6`) | per-order pickup detour | 🟢 | `~dp:4138`, `rule_weights.json` |
| `bonus_r_return_rest` | **−100** (`RETURN_TO_RESTAURANT_PENALTY=100`) | return-to-restaurant carrying its delivery (R-NO-RETURN) | 🟢 (flag ON) | `common.py:3116` |
| `bonus_r_paczki_flex` | `−1/min` over soft cap (2h pickup / 3h delivery) | parcel orders past flex window | 🟢 | `~dp:2653` |
| `bonus_wave_clean` | +10 | single-wave bag (atomic burst) | 🟢 | `~dp:4157` |
| `bonus_inter_wave_deadhead` | `−3.0·(max_deadhead−4)` over 4 km | multi-wave bag spread | 🟢 | `~dp:4161` |
| `bonus_state_panel_mismatch` | `−50·min(phantom_oids,4)` | orders_state vs panel bag divergence | 🟢 | `~dp:4186` |
| `bonus_coordinator_idle` | −100 | coordinator account inactive | 🟢 | `~dp:4170` |
| V326 R-06 district bonus | same +40 / adjacent +15 / sideways −10 / opposite −40 | multi-stop trajectory district relation | 🟢 | consts `common.py:2033-2036`, applied `dp:747-750` |
| `v326_speed` adjustment | `(1.0 − tier_mult)·50` → gold +7.5 … slow −12.5 | tier speed (see §7.D) | 🟢 | `common.py:1913,1927` |
| `v324a_extension_penalty` | tiered 0/−10/−50/−100/−200 | pickup past shift end (graduated) | 🟢 | `common.py:1675` |
| `bonus_bag_time_sum/max/fifo` | `−1.0·Σbag, −0.7·max, −5·fifo_viol` | fairness across bag (BUG-A) | ⚪ OFF (`ENABLE_BAG_TIME_FAIRNESS_SCORING`) | `~dp:3757` |
| `bonus_r5_pickup_detour_penalty` | `−8.0·(detour−0.5)` | pickup-not-on-route (BUG-B) | ⚪ OFF | `~dp:3784` |
| `bonus_sync_spread_shadow_delta` | `0/−30/−80/−150` by ready-spread | bundle ready-time spread (SYNCWORKA) | 🟡 SHADOW | `common.py:1825` |
| `bonus_repo_cost_shadow_delta` | `−30·min(repo_km/4,1)` | dead-head reposition cost | 🟡 SHADOW | `common.py:1845` |
| `bonus_loadgov_shadow_delta` | `LOADGOV_BAG_PENALTY=−40` | fleet overload governor | 🟡 SHADOW | `~dp:4433` |
| `bonus_bundle_fit_shadow_delta` | cos/thermal/span weights (`BUNDLE_FIT_*`) | bundle value scoring | 🟡 SHADOW | `common.py:1002` |
| `bonus_gps_age_discount` | `−min(cap, (age−free)·per_min)` | stale GPS position | 🟡 SHADOW | `~dp:1089` |
| `bonus_a2_reliability_delta` | courier reliability coeff | reliability soft score | 🟡 SHADOW | `~dp:1067` |

### 5.3 rule_weights.json (live R-penalty knobs — **static, hand-tuned, no writer**)
`/root/.openclaw/workspace/dispatch_state/rule_weights.json`, `_updated: 2026-04-16`:
```
R1_spread_per_km: -8.0   R1_threshold_km: 8.0
R5_pickup_per_km: -6.0   R5_threshold_km: 2.5
R8_span_per_min: -1.5    R8_threshold_bundle2_min: 15.0   R8_threshold_bundle3_min: 30.0
```
Loaded via `_load_rule_weights()` (mtime-cached, fail-loud to defaults). `learning_analyzer` writes
`learning_analysis.json` but **does not** write this file — these are pure manual levers.

### 5.4 Selection — layered sort then authoritative demotion
After scoring, `feasible` (MAYBE candidates) is ordered by **successive passes**; the *last* pass wins
(Lekcja #150 — "any later sort must preserve the demotion invariant"):
1. Primary sort `key = (−score, bundle_level3_dev or 999.0)` (`dp:5275`).
2. **R-LATE-PICKUP tiering** reorder (`_late_pickup_score_first_key`, `~dp:5305-5397`): buckets by
   courier tier × late-pickup risk, score within bucket.
3. `OBJM_LEXR6_SELECT` reorder (lexicographic R6, ⚪ OFF / 🟡 shadow, `~dp:5352`).
4. **`_demote_blind_empty`** (def `dp:2045`, called `dp:5303`, **authoritative final pass**): if the top candidate has
   `pos_source ∈ {no_gps, pre_shift, none}` **and** empty bag, and an *informed* candidate exists,
   reorder `informed → other → blind_empty`. Prevents a fictitious `BIALYSTOK_CENTER` position from
   beating a real-GPS courier (root cause of historical 78% override rate).
5. `top = feasible[:16]`; `best = top[0]`.

`pos_source` buckets: **informed** = `{gps, last_assigned_pickup, last_picked_up_*, last_delivered,
post_wave}`; **blind** = `{no_gps, pre_shift, none}`.

**Position-cascade TZ (26.06, `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` 🟢 canary):** `orders_state.picked_up_at`/`delivered_at`
= NAIVE Warsaw (panel Rutcom); `updated_at`/`assigned_at` = aware-UTC. `courier_resolver` parsuje checkpointy
przez `_parse_checkpoint_ts`→`parse_panel_timestamp` (naive→Warsaw) — inaczej świeży odbiór miał elapsed/age
UJEMNE → interp (F4 Krok 2) martwy + recent-activity pomijał świeże + ZOMBIE/staleness zaniżone o offset (~2h).
Rdzeń decyzyjny (feasibility/R6/ETA/plan) był czysty — wchodzi sparsowanym `OrderSim.picked_up_at`.

### 5.5 Verdict gates (in evaluation order → first match wins)
| Verdict | reason | Condition | Knob | Source |
|---|---|---|---|---|
| KOORD | `state_likely_stale` | panel cache >60 s & ≥2 stale signals | — | `~dp:5672` |
| KOORD | `geometry_blind_fallback` | all candidates greedy-fallback **and** all pairwise cos<0 | — | `~dp:5702` |
| KOORD | `all_candidates_low_score` | `best.score(excl. ranking deltas) < MIN_PROPOSE_SCORE` | `MIN_PROPOSE_SCORE=−100` | `~dp:5745`, `common.py:679` |
| KOORD | `commit_divergence_gate` | `max(plan_pickup_eta − committed czas_kuriera) > 10 min` (cold-food risk) | `…KOORD_MIN_MIN=10` | `~dp:5790` 🟢 |
| KOORD | `difficult_geometry_redirect` | `best.score < −30` | `DIFFICULT_CASE_SCORE_FLOOR=−30` | `~dp:5867` ⚪ (flag OFF) |
| **PROPOSE** | `feasible=N best=…` | otherwise (≥1 feasible passes gates) | — | `~dp:5955` |

**No-feasible fallbacks** (`feasible==[]`, `~dp:5978-6251`): `best_effort_r6_breach_v2` (any bag
order >35 → KOORD) → `obj_f3_best_effort_r6` (breach >20 → KOORD) → `best_effort_low_score`
(<−100 → KOORD) → else **PROPOSE best_effort** (banner warns) → else **solo_fallback** (empty-bag
courier, R1/R5/R8 ignored) → else **KOORD no_solo_candidates**.

**Best_effort candidate selection** (which infeasible courier is proposed; `_best_effort_objm_pick`
PRIMARY = carry-inclusive `objm_r6_breach_max_min`, cap≤40 safe-filter). **Post-shift overrun penalty**
(Adrian 2026-06-24, `ENABLE_POST_SHIFT_OVERRUN_PENALTY`, **default OFF / forward-shadow**): rosnąca
(wypukła, grace≤5, tiery 8/16/28/45 pkt/min) kara za minuty `predicted_delivered_at[new] − shift_end`
jako WIODĄCY term selekcji (`_best_effort_objm_pick._lex_qual` + `_best_effort_sort_key`) → kurier
kończący PO zmianie spada poniżej kończących w oknie (case 483144: Piotr +27/Kuba +38 pod Patryka 0).
Cap≤40 chroni przed flipem na zimne. Metryka `post_shift_overrun_min/_penalty` logowana ZAWSZE;
parytet w `objm_lexr6.lex_qual` (LEXR6 feasible D2). `common.post_shift_overrun_penalty`. Flip po
replay 25.06 + ACK. _(verdict tool: `tools/post_shift_overrun_forward_replay.py`)_

The `gate_score` used for low-score gates *excludes* ranking-only deltas (SYNCWORKA, LOADGOV) so a
shadow ranking penalty can re-order without silencing a proposal (`_gate_score_excluding_ranking_deltas`,
`~dp:1975`).

---

## 6. Feasibility — hard gates & soft metrics (`feasibility_v2.check_feasibility_v2`)

### 6.1 HARD gates (return `("NO", reason, …)` → candidate infeasible)
| Gate | Threshold | Status | Source |
|---|---|---|---|
| Bag sanity cap | `len(bag) ≥ 8` | 🟢 | `MAX_BAG_SANITY_CAP=8` `common.py:306` |
| Hard tier bag cap | gold/std+ 6, std 5, slow/new 4 (default 6) | 🟢 **LIVE** (`ENABLE_HARD_TIER_BAG_CAP`, flip ~06-22) | `common.py:1174` + `feasibility_v2.py:463` |
| R7 long-haul peak | ride >99 km & hour∈[14,17] | 🟢 but **dormant** (99 km ⇒ never fires) | `LONG_HAUL_DISTANCE_KM=99` `common.py:684` |
| Pickup too far | haversine·1.37 > 15 km | 🟢 | `MAX_PICKUP_REACH_KM=15` `common.py:313` |
| `v325_NO_ACTIVE_SHIFT` | `shift_end is None` (no schedule), unless fail-open | 🟢 (`ENABLE_V325_SCHEDULE_HARDENING`) | `~feas:674-721` |
| `PICKUP_POST_SHIFT` | pickup after shift end (unless end-of-day salvage) | 🟢 | `~feas:726` |
| `PRE_SHIFT_TOO_EARLY` | pickup >30 min before shift start | 🟢 | `V325_PRE_SHIFT_HARD_REJECT_MIN=30` `common.py:1761` |
| `v324a_dropoff_after_shift` | planned drop > shift_end + 5 min | 🟢 (`ENABLE_V324A…`) | `V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN=5` `common.py:1668` |
| SLA violation | `plan.sla_violations > 0` unless all pre-existing | 🟢 (`ENABLE_SLA_PREEXISTING_BYPASS`) | `DEFAULT_SLA_MINUTES=35` |
| **R6 per-order** | any bag order delivery >35 min from ready-anchor (unless all-paczki) | 🟢 **the single canonical hard rule** | `BAG_TIME_HARD_MAX_MIN=35` `common.py:647` |
| R6 picked-up delta | picked-up order >35 min **and** new order causes the delay | 🟢 | `~feas:1255` |
| C2 per-order 35 | per-order >35 (future gate) | ⚪ OFF / 🟡 shadow (`USE_PER_ORDER_GATE`) | `~feas:1306` |

**R6 thermal anchor selection** (the crux of food-freshness): new order → `pickup_ready_at`; bag
order not picked → `pickup_ready_at` (food waits from ready); picked-up → `picked_up_at`
(soft-tracked); fallback `now`. Optional per-restaurant `prep_bias` shift is ⚪ OFF.

### 6.2 SOFT metrics (telemetry / scoring only — **not** rejects)
`R1` delivery spread + `r1_avg_pairwise_cosine` (directionality); `R3` dynamic bag cap (softened to
telemetry, F1.9b); `R5` mixed-pickup spread + detour km; `R8` pickup span; wave detection
(`n_waves`, inter-wave deadhead, window 12 min / 1.5 km); `detect_return_to_restaurant`
(`same_rest=0.08 km`, `group_tol=5 min`); OBJ plan metrics (`objm_route_span_min`,
`objm_idle_total_min`, `objm_max_thermal_age_min`, `objm_r6_breach_*`).

> **Important business-rule clarification:** R1 (8 km spread) and R8 (pickup span) are **SOFT**, not
> hard — verified by audit 2026-05-21. The only hard bundle limits are **R6 (35-min per-order
> thermal) + SLA**. Adrian's decision: hard R1 would kill peak throughput (Sat 16.05: 37/37 wide
> bundles were time-feasible). Do not "fix" R1/R8 into hard gates.

---

## 7. Master knobs inventory (the recalibration levers)

> These are the dials another model would actually tune. Each has been read from source. Group by
> kind. **Bold = most load-bearing.**

**A. Base score (`scoring.py`)** — **`W_DYSTANS 0.30 / W_OBCIAZENIE 0.25 / W_KIERUNEK 0.25 /
W_CZAS 0.20`** (`:22-25`); **`DIST_DECAY_KM 5.0`** (`:27`); time penalty 30→35 min, exponent 2.5
(`:31-55`); `MAX_BAG_TSP_BRUTEFORCE 5` (`common.py:300`).

**B. R6 / time (`common.py`)** — **`BAG_TIME_HARD_MAX_MIN 35`** (`:647`); soft zone 30–35;
`STOPOVER_SCORE_PER_STOP 8` (`:699`); `DWELL_PICKUP_FLAT_MIN 1.0` (`:1937`), `DWELL_DEFAULT 3.5`
(`:1938`); wait table (§5.1).

**C. Rule penalties (`rule_weights.json`)** — R1 −8/km @8 km; R5 −6/km @2.5 km; R8 −1.5/min @15/30 min.
Plus `RETURN_TO_RESTAURANT_PENALTY 100` (`common.py:3116`).

**D. Tier speed multiplier `V326_SPEED_MULTIPLIER_MAP`** (`common.py:1913-1924`, recalibrated
2026-06-10 from 3056 deliveries; score adj = `(1−mult)·50`):
| tier | gold | std+ | std | slow | new |
|---|---|---|---|---|---|
| mult | 0.850 | 0.940 | 1.000 | 1.250 | 1.200 |
| score | +7.5 | +3.0 | 0 | −12.5 | −10.0 |

**E. Tier-aware DWELL dropoff `DWELL_BY_TIER`** (`common.py:1939-1950`, min): gold 1.5 / std+ 2.5 /
std 4.5 / slow 6.5 / new 6.5 (pickup is flat 1.0 for all).

**F. Tier bag-cap matrix `BUG4_TIER_CAP_MATRIX`** (`common.py:1158`, soft cap, 🟢):
| tier | off_peak | normal | peak |
|---|---|---|---|
| gold | 4 | 4 | 6 |
| std+ | 3 | 4 | 5 |
| std | 2 | 3 | 4 |
| slow | 2 | 2 | 3 |

**G. OSRM traffic multipliers `V326_OSRM_TRAFFIC_TABLE`** (`common.py:478-530`, median-recalibrated
2026-06-05/06-12):
- **Weekday** (h→×): 0–9 1.0 · 9–10 1.15 · 10–12 1.25 · 12–13 1.40 · 13–14 **1.50** · 14–15 1.35 ·
  15–17 **1.55** · 17–20 1.25 · 20–21 1.10 · 21–24 1.05.
- **Saturday:** 0–12 1.0 · 12–13 1.30 · 13–16 1.20 · 16–17 **1.55** · 17–18 1.45 · 18–21 1.25 · 21–22 1.10.
- **Sunday:** 0–11 1.0 · 11–12 **1.50** · 12–13 1.40 · 13–15 1.35 · 15–16 1.45 · 16–19 1.30 · 19–20 1.15.
- Distance-bin boost (peak only): <2 km +1.0 / 2–5 km +0.4 / ≥5 km −0.15 — ⚪ OFF
  (`ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST`).

**H. Verdict thresholds** — `MIN_PROPOSE_SCORE −100` (`:679`); `EARLY_BIRD_THRESHOLD_MIN 60`
(`:314`); commit-divergence 10 min; difficult-case floor −30 (OFF);
`V326_FLEET_LOAD_BONUS/PENALTY 15.0` (`:2023-2024`); `AUTO_APPROVE_THRESHOLD 130`
(disabled).

**I. Schedule/shift** — pre-shift hard reject 30 min, soft penalty −20; dropoff-after-shift +5 min;
extension hard reject >60 min, graduated penalties 0/−10/−50/−100/−200 (`V324_EXTENSION_PENALTY_TIERS`
`common.py:1675`); end-of-day salvage (last company hour, Fri/Sat to 24:00) — ⚪/🟢 per
`ENABLE_END_OF_DAY_SALVAGE`.

**J. New-courier ramp** — `RAMP_DELIVERIES 30`, `RAMP_MAX_KM 2.5`, `RAMP_MALUS −20`,
`SOLO_MALUS −60`; tiered new-courier advantage penalties −10/−30/−50 (`common.py:1788-1812`).

---

## 8. Business rules → code mapping

| Rule | Type | Encoded as | Where |
|---|---|---|---|
| **R-DECLARED-TIME** (`czas_kuriera ≥ ready`) | HARD | pre-shift/post-shift gates; frozen pickup window R27 ±5 | `feasibility_v2`, `route_simulator_v2` |
| **R-35MIN-MAX** (R6) | HARD | per-order thermal gate, anchor=ready_at | `feas:1235` |
| **R-NO-WASTE** (BUG-2 gap) | SOFT gradient | `timing_gap_bonus`, `bonus_bug2_continuation` | `dp:3664,4206` |
| **R-FLEET-LEVEL** | principle | fleet-load governor (shadow), tier caps, demotion | `dp`, `common` |
| **R-SCHEDULE-AWARE** | HARD-ish | V3.24-A grafik check (Google Sheet, 10-min TTL) | `feas`, `courier_resolver` |
| **R-NO-RETURN-RESTAURANT** | strong penalty (−100) | `detect_return_to_restaurant` | `feas:131` |
| **R-PACZKI-FLEX** (parcels) | SOFT cap | bypass R6 for all-parcel bag; 2h/3h soft window | `feas:1016`, `dp:2653` |
| **R-PRIORYTETÓW** | hierarchy | sort/demote order: waste → proximity → R4 → tier → bag | `dp:5275-5397` |

`czas_odbioru` field: `<60` = *elastyk* (flexible), `≥60` = *czasówka* (hard restaurant-declared time,
held under virtual courier `id_kurier=26`).

---

## 9. Feature flags taxonomy (`flags.json`, hot-reload)

The flag system (`common.load_flags` / `decision_flag`) reads `flags.json` first, then falls back to
the module constant. ~80+ flags exist. Notable **current** states:

- 🟢 **LIVE-ON:** `RECONCILIATION_ENABLED`, `ENABLE_V325_SCHEDULE_HARDENING`,
  `ENABLE_V324A_SCHEDULE_INTEGRATION`, `ENABLE_V327_WAIT_PENALTY`, `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER`,
  `ENABLE_V326_SPEED_MULTIPLIER`, `ENABLE_R_PACZKI_FLEX`, `ENABLE_R_RETURN_TO_RESTAURANT_VETO`,
  `ENABLE_HARD_TIER_BAG_CAP` (flip ~06-22), `ENABLE_R5_PICKUP_DETOUR_PENALTY`, `ENABLE_STATE_WRITE_GUARD`,
  several `*_GUARD` defenses. _(zsynchronizowano 2026-06-24 z żywym flags.json + env; usunięto zombie `feasibility_check` — 0 odczytów)_
- 🟡 **SHADOW (computed, logged, no effect):** `AUTO_PROXIMITY_SHADOW_ONLY`,
  `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW`, `ENABLE_ETA_QUANTILE_SHADOW`, `ENABLE_PREP_BIAS_SHADOW`,
  `ENABLE_REPO_COST_SHADOW`, `ENABLE_PLN_OBJECTIVE_SHADOW`, `ENABLE_LGBM_TWOMODEL_SHADOW`,
  `ENABLE_OBJM_LEXR6_SELECT_SHADOW`, `ENABLE_ETA_R3_SHADOW`, `ENABLE_ETA_R3_DROP_SHADOW`,
  `ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW`, `ENABLE_SAME_RESTAURANT_RACE_PROBE`,
  `ENABLE_MIN_DELIVERED_AT_SHADOW` (Adrian 2026-06-25: log-only komparator selekcji „min total
  spóźnienie+dowóz" = `min predicted_delivered_at[new]` vs live winner + regresja floty R6/spread/late
  w tej samej decyzji (Pareto); metryka `min_delivered_at_shadow` w `shadow_decisions.jsonl`;
  helper `_new_delivered_at_dt`, `dispatch_pipeline` po `_winner=feasible[0]`; ZERO zmiany decyzji).
  `ENABLE_BUG4_RESEQ_SHADOW` (Adrian 2026-06-26: log-only pomiar bug #4 „zygzak worka" — przy RETIME
  worka ≥2 zleceń (`plan_recheck` gałąź lock-ON) liczy ŚWIEŻY solve (`_sweep` jak `_gen_one_bag_plan`)
  i loguje deltę DRIVE zamrożona-sekwencja↔świeża + `seq_differs` do `dispatch_state/bug4_reseq_shadow.jsonl`;
  helper `_bug4_reseq_shadow`/`_osrm_drive_min_sum`, cap 20/tick, fail-soft; ZERO zmiany decyzji/zapisu.
  Cel: materialność kary zamrożonej sekwencji vs świeży solve przed naprawą u źródła feasibility↔route_simulator↔plan_recheck).
  `ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW` (Adrian 2026-06-26: log-only detektor rozjazdu ulica↔miasto w ingestii —
  `address_mismatch.check_street_town()` (bliźniak panelowego `/estimate`, próg 5/1) + `maybe_log_mismatch` →
  `dispatch_state/address_mismatch_shadow.jsonl`; hook w `shadow_dispatcher._tick` po geokodzie dostawy, try/except
  fail-soft; cel: złe miasto → zły geokod (case Olmonty/483504); ZERO zmiany decyzji/dispatchu).
  `ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW` (Adrian 2026-06-29: log-only detektor rozjazdu TEKST↔PIN —
  inna klasa niż town: miasto OK, ale napisany `delivery_address` geokoduje się > 400 m od zapisanego
  `delivery_coords`, na których kurier jedzie. `address_mismatch.check_text_coords()` + throttlowany
  `maybe_sweep_text_coords` (sweep aktywnych orders_state co ~300 s, dedup, geocode wstrzykiwany) →
  ten sam `address_mismatch_shadow.jsonl` z polem `check:"text_coords"`; hook w `shadow_dispatcher._tick`
  po `state_all`, try/except fail-soft; case 484269 „Można"≠„Mroźna" 4,26 km — tekst stał po edycji,
  bo `gastro_edit.regeocode_and_update` aktualizuje tylko coords; ZERO zmiany decyzji/dispatchu).
  `ENABLE_REGEOCODE_SYNC_TEXT` (Adrian 2026-06-29: fix asymetrii — po edycji adresu w gastro
  `gastro_edit.regeocode_and_update` zapisuje też `delivery_address`+`delivery_city` spójnie z
  `delivery_coords` (nie tylko coords). Koniec rozjazdu tekst↔pin tworzonego przez naszą ścieżkę
  edycji; tekst karmi `drop_zone_from_address` → poprawny district w scoringu. Default OFF → flip ON
  po weryfikacji; subprocess `gastro_edit.py` czyta flagę per-edycję, bez restartu. Komplement do
  `ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW` (detektor mierzy spadek rozjazdów). Naprawia case a/edycja,
  NIE tekst błędny u źródła w gastro/case b).
  `ENABLE_COORD_SENTINEL_INGEST_GUARD` (L2.1 2026-07-01, Faza 3 audytu, most K5: JEDEN kanoniczny
  walidator `coords_in_bialystok_bbox` u KAŻDEGO ingest — gps_server POST, `state_machine.upsert_order`
  [chokepoint: NEW_ORDER×2 + picked_up + delivered + parcel_lane_merge], shadow-tick geocode-or-skip,
  read-side `_load_gps_positions` — + guardy konsumentów geometrii `_coords_pass` (soon_free probe+serializer,
  wave-veto, repo-cost, bundle L2/L3, coloc), `_save_plan_on_assign` pisze REALNE coords z orders_state
  zamiast placeholderów (0,0) [K5b], `feasibility._valid`→kanon (6 definicji sentinela→1). OFF = legacy
  bajt-w-bajt (truthy-guardy, (0,0)-as-data → haversine ValueError → `_v328_eval_safe` eject kuriera:
  2046+14456 zdarzeń, 8-28 ofiar/dzień). Telemetria unconditional: `coord_poison_bag_oids`/
  `coord_poison_new_delivery` w shadow_decisions. Testy: `test_coord_sentinel_ingest_l21` (22, e2e V328).
  Flip = pełny deploy C2 (restart shadow+panel-watcher+gps) za ACK; potem L2.2 catch-all rozróżnia).
  `ENABLE_FIRMOWE_BAG_COORD_FALLBACK` (Sprint F 2026-07-08, źródło (0,0)/COORD_GUARD: paczki FIRMOWE
  `aid∈FIRMOWE_KONTO_ADDRESS_IDS` persystują `pickup_coords=None` [świadomy reject→KOORD parsera uwag];
  przypisane→w worku jako `assigned` → `_bag_dict_to_ordersim` runtime re-geokod `_repair_bag_coords`
  pada pod peakiem [timeout 2 s] → cichy `(0.0,0.0)` → `route_simulator.table` → COORD_GUARD sentinel
  9999 → holder cicho wykluczany [geometria-ślepy pile-on, resztkowa choroba L2.1 — N-D #1 tamtej fali].
  ON → odbiór firmowy nierozwiązywalny dostaje `FIRMOWE_KONTO_FALLBACK_COORDS` [centrala Nadajesz, w bbox]
  przez `dispatch_pipeline._firmowe_bag_pickup_fallback` zamiast (0,0). WĄSKO: tylko ODBIÓR firmowy —
  delivery/nie-firmowe/nowe zlecenia bez zmian [guard OSRM = backstop]. OFF = legacy (0,0) bajt-w-bajt.
  Testy `test_firmowe_bag_coord_fallback_sprintf` [7]. Flip = pełny deploy C2 [restart shadow] za ACK.
- ⚪ **OFF:** `AUTO_PROXIMITY_ENABLED`, `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` (cold-food divergence
  no longer →KOORD), `ENABLE_BAG_TIME_FAIRNESS_SCORING`,
  `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT`, `ENABLE_CARRY_CHAIN_PENALTY`, `kill_switch_to_v1`,
  `ENABLE_DRIVE_MIN_CALIBRATION_V2` (main), `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (ETAP4; forward-shadow
  od 2026-06-24 20:52 — metryka logowana, flip czeka replay 25.06 + ACK; demote kuriera kończącego po
  zmianie w selekcji best_effort + LEXR6).
  `ENABLE_FEAS_CARRY_READMIT` (#483000, ETAP4 decyzyjna) — **🟢 LIVE od 2026-06-27 22:18 UTC**
  (flaga flipnięta ON w flags.json; const-fallback OFF; replay GO przed flipem: redirect 52,6%,
  median −9,7min worst-breach floty, Pareto, cap-koszt ≤5min; commit `e72139e`+monitor `3eecef6`;
  post-flip at-192 28.06 21:00 UTC; rollback hot = flaga false bez restartu). Bramka
  `check_feasibility_v2` wybacza najgorszy breach NIESIONEMU
  (`SLA_PREEXISTING_BYPASS`) a HARD-rejectuje blocking SLA/R6 → pula feasible bywa GORSZY ocalały,
  lepszy carry-inclusive wycięty (shadow `ENABLE_FEAS_CARRY_BLIND_SHADOW` 27.06: 55,5% would_redirect,
  n=596). Ta flaga re-dopuszcza odrzuconego (NO, blocking sla/r6) na warstwie SELEKCJI
  (`dispatch_pipeline` po `_feas_carry_blind_shadow`) gdy carry-inclusive `lex_qual` lepszy od top[0]
  ORAZ nowy order ≤ `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` (40 = Tier-3 cap-stretch, ten sam guard co
  `_best_effort_objm_pick`). Werdykt HARD bramki nietknięty u źródła — downstream MIN_PROPOSE +
  `commit_divergence_gate` dalej gate'ują nowy top[0]. Mirror best_effort na feasible-path; promote
  verdict→MAYBE, metryki prefix `feas_carry_` w `shadow_decisions.jsonl`. Helper `_feas_carry_readmit_pick`.
- 🟢 **`ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN` (2026-06-25, LIVE on `dispatch-shadow`, display-only).**
  Linia „Kandydaci" w propozycji Telegram (`_candidate_line_v2`) pokazywała `eta_pickup_hhmm` =
  dojazd pod restaurację, a dla `pre_shift` = **start zmiany** (np. Patryk K-75 18:00) — czyli odbiór
  PRZED gotowością jedzenia / przed faktycznym planem (case #483301 Piwo Kaczka Sushi, plan 18:07).
  Header `_format_proposal_v2` JUŻ floruje do `plan.pickup_at[oid]` (Etap2 2026-05-13 #472788) — ta
  linia była **bliźniaczą luką**. Fix: floor ETA kandydata do `plan.pickup_at[oid]` (per-kandydat,
  fallback `pickup_ready_at`); komponuje z `ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED` (czas_kuriera)
  przez `max` — oba tylko podnoszą, nigdy nie obniżają. Silnik/plan NIETKNIĘTE (display). Rollback hot:
  `flags.json` → `false`. Materialność: ~98% propozycji (głównie +1 min food-ready, pre_shift do +36 min).
- 🟢 **LIVE route-sequencing (systemd-env flags, NOT `flags.json`; set on `dispatch-plan-recheck` +
  `dispatch-panel-watcher` where the canon is written to `courier_plans.json`):**
  `ENABLE_PLAN_CANON_ORDER_INVARIANTS` (carried `picked_up` dropoffs front + pickups sorted by committed
  time), `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` (never re-visit a departed restaurant — two pickups of one
  restaurant coalesced into one visit), and `ENABLE_CARRIED_FIRST_RELAX` (2026-06-22, **flipped LIVE**):
  among precedence-valid bag permutations pick **min-drive** subject to **5 guards** — carried delivered
  ≤SOFT_MAX(20) of `picked_up_at`, no other delivery later >TOL(3), no pickup later >TOL, no new R6, and
  **NO-RETURN**: never route a pickup at a restaurant the courier already carries food from / never split
  one restaurant's pickups (bundling preserved); accept only if >DRIVE_EPS(0.3) shorter, else carried-first.
  By construction improve-or-no-op; replay 29k situations zero-harm. Code: `_relax_carried_first` +
  `_detect_departed_pickup_revisit` (+`carried_rest_keys` seed) in `plan_recheck.py`. The courier app and
  coordinator console render this canon verbatim via `ENABLE_BUILD_VIEW_TRUST_CANON_ORDER` (courier-api)
  and `PANEL_FLAG_TRUST_CANON_ORDER` (nadajesz-panel) — both 🟢 LIVE. ⚠ **2026-06-28 (faseta #3):** the
  courier-api flag was previously DEAD (masked by `_console_done=True` set at `build_view:1105` before its
  only consumer `courier_orders.py:1146`) → the app rendered via `route_podjazdy.order_podjazdy` which
  force-carried-first, diverging from the console (which alone got the 22.06 relax — twin fix-in-1-of-2 #11).
  **Fixed:** `route_podjazdy.order_podjazdy(trust_canon=…)` now renders the canon verbatim via
  `_canon_order_from_plan` (mirror of console `_order_from_plan_seq`; coverage `cov_drop/cov_pick` like
  console), flag wired at `build_view:1107`; monitor `ziomek_time_route_monitor.route_app` mirrors the flag
  too. Live: q3_route_mismatches 15.1%→0 (app==console==canon). These re-sequence an **already
  assigned** bag only; they do **not** touch assignment/feasibility (a courier carrying a restaurant's
  food can still be assigned new orders, incl. from that restaurant).
- 🟢 **`ENABLE_RECANON_ON_WRITE` (2026-06-23, LIVE on `dispatch-panel-watcher`).** Root cause fixed
  "from the foundations": the canon order-invariants above were applied **only** by the 5-min
  `plan_recheck` tick. Event-time writers of `courier_plans.json` (`_save_plan_on_assign` proposal-save,
  `mark_picked_up` on pickup, `advance_plan` on delivery) wrote the plan **without** them → carried not
  floored / pickups not sorted until the next tick (cases Piotr/Grzesiek/Dawid 23.06). `recanon_courier`
  (in `plan_recheck.py`, called from the 3 `panel_watcher` event handlers) re-enforces the canon on the
  **existing** plan immediately on every bag event via `_retime_one_bag_plan` (**no re-TSP** — Ziomek's
  sequence preserved, just floor+committed-sort+relax + re-time). ~4–8 ms, idempotent, self-gating
  (no-op if plan missing/invalidated/not-covering). Console complement: `PANEL_FLAG_SKIP_INVALIDATED_PLAN`
  (🟢 LIVE) — for an invalidated plan the panel does **not** trust the raw canon, falls back to its
  carried-first rebuild (measured ≡ full canon in 95.9% of carried bags).
- 🔵 **`ENABLE_B_ROUTE_SHADOW` (2026-06-23, read-only).** Open question: would **immediate full re-TSP**
  on every override ("option B", `_gen_one_bag_plan`) yield better real outcomes than the served canon?
  Rejected as-live (0.8–2.1 s OR-Tools in the hot path would choke `panel_watcher` in peak; cheap
  `insert_stop_optimal`+canon "B-lite" is ~10 ms / 120× but static-quality −1.4 min vs re-TSP). Shadow
  (`tools/b_route_shadow.py`, timer every 5 min, writes to a **temp** plans file — zero live mutation)
  logs served/B/B-lite + freshness/punctuality/drive metrics to `b_route_shadow.jsonl`; one-shot review
  (`tools/b_route_shadow_review.py`, timer **2026-06-30**) joins `order_ids`→`sla_log.jsonl` for the
  GO/NO-GO verdict (preliminary signal: B trades carried freshness for total-duration → leaning **NO-GO**).
- 🟢 **`ENABLE_DRIVE_SPEED_TIER_CORRECTION` (2026-06-26, Adrian).** Per-tier drive-speed multiplier on
  every route leg (`common.speed_mult_for_tier` → `DRIVE_SPEED_MULT_BY_TIER` gold 0.78 / std+ 0.82 /
  std 0.82 / slow,new 1.0). Root cause: the live ETA systematically **over**-estimated drive time
  (fleet median −4.7 min delivered-vs-live-ETA, n=657; a stable bag's predicted `dur` drifts down each
  tick as GPS re-anchoring catches the courier ahead of plan — Bartek 123: 54→44 min in 15 min). That
  pessimism mis-ranks proposals (couriers shown "will be late / breach R6" though they arrive on time)
  and churns re-sequencing. The mult (<1.0 = faster) is applied at the **single source**
  `speed_mult_for_tier`, consumed by **both** twin route-sim paths: `feasibility_v2:811` (proposal/R6)
  **and** `plan_recheck` `_gen_one_bag_plan` (`_sweep`, the live ETA/display/re-sequence path — wired
  2026-06-26 for parity; previously ran at 1.0). Flag **OFF (default) → 1.0 = byte-identical/legacy**;
  ON → calibrated. ⛔ **ROLLED BACK same-day (flag OFF):** composition-clean GPS-motion
  (`courier_speed_build`: motion ~1.375×OSRM = NOT faster, `NO_PER_COURIER_SIGNAL`) + June-05 traffic
  recalib (drive-leg bias already −1.37 min) showed the over-prediction is **not in drive speed** — it
  is in DWELL/route-composition. Drive mult was mis-targeted (would overshoot). Code stays inert for
  possible fleet-level reuse. See next entry for the real fix.
- 🟢 **`ENABLE_PLAN_RECHECK_TIER_DWELL` (2026-06-26, Adrian — the real fix).** Brings `plan_recheck`
  `_gen_one_bag_plan` (`_sweep`, the display/re-sequence path used by `panel_watcher`) to **parity with
  `feasibility_v2:804`** which already uses `common.dwell_for_tier` (dropoff per tier gold 1.5 / std+
  2.5 / std 4.5 …, **recalibrated 2026-06-10 from 7496 eta_calibration_log records — absorbs per-tier
  ETA residual, unbiased**). Root cause of the displayed-ETA drift: plan_recheck used the route_simulator
  **default** dropoff 3.5 (vs real geofence ~2.2 min, n=793, and vs feasibility's calibrated gold 1.5) →
  **over**-estimated displayed ETA by ~dwell_gap × stops → "times drift down after each stop". This is
  the **correct layer** (drive is fine). Self-bounded: it does not introduce a free shortening knob — it
  reuses the already-live, calibrated, unbiased feasibility dwell, so gold shortens by exactly the
  calibrated amount and **std actually lengthens** (3.5→4.5). Does **NOT** touch the R6/feasibility hard
  gate (separate path, already on dwell_for_tier) — only display/sequencing. Flag OFF (default) → route_
  simulator default dwell = byte-identical; ON → dwell_for_tier. Rollback = flag OFF (hot). Deploy needs
  `dispatch-panel-watcher` restart (long-running consumer of plan_recheck) — flag-OFF restart is byte-id.
- 🟢 **`ENABLE_GPS_FREE_ANCHOR_LAST_POS` (2026-06-26, Adrian — case 509 "plan stuck 52 min").** Adds a
  **last-resort anchor** to `plan_recheck._start_anchor`: when a courier has no fresh GPS **and** no event
  anchor **and** no committed-pickup anchor, it now falls back to the **last-known-pos store**
  (`courier_last_pos.json`) instead of returning `None`. Without it, `_start_anchor=None` → `_gen_one_bag_plan`
  **skips the whole courier** → the invalidated plan **never regenerates** (lingers with already-delivered
  stops + missing active orders). Reuses `courier_resolver._load_last_known_pos`+`_rescue_from_last_pos`
  (TTL 25 min + Białystok bbox + allowed sources — **one skeleton, zero dup**); brings plan_recheck regen to
  **parity with the decision path** which has rescued no-GPS couriers since 2026-06-08. Strictly additive:
  only fills the `None`→skip case, so **cannot regress** (OFF = byte-identical). Material effect is the
  oneshot `dispatch-plan-recheck` gap-fill only (panel_watcher `recanon_courier` bails on invalidated plans,
  so the store-fallback never bites there). Fires rarely (~1 stuck no-GPS courier at a time) — logs
  `START_ANCHOR_LAST_POS` per firing for the evidence trail. Flag OFF (default). LIVE via env drop-in on
  `dispatch-plan-recheck` (oneshot picks up next tick, **no daemon restart**). Rollback = rm drop-in +
  daemon-reload (next tick OFF). Commit `bd2f7a2`.
- 🔵 **`ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` (2026-06-28, sesja 20 — case 484034 Sikorskiego, observability-first).**
  Ziomek classifies czasówka **only** by `prep_minutes ≥ 60` (`panel_client.normalize_order`) and is **blind**
  to a hard **delivery** deadline written in free-text `uwagi` ("Czasówka na 17:10") on an otherwise `elastic`
  order → R6 computes generic pickup+35 (`expected_delivery_by` 17:34, not 17:10). This flag turns ON
  **extraction + persistence** of that deadline: `czasowka_uwagi.parse_delivery_deadline` (single source,
  regex parity with the `bundle_calib_shadow` shadow parser) populates a **new additive field**
  `delivery_deadline_uwagi` in `normalize_order`, persisted by `state_machine.upsert_order` alongside `uwagi`.
  **Strictly additive / observability-only:** it does **NOT** overwrite `order_type` or `czas_kuriera`
  (committed R27 untouched, wzorzec #8), and **no decision consumer reads it yet** — wiring into the 3 SLA-anchor
  twins (`_count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key`) + serializer is a separate,
  ACK-gated step after the offline oracle (`tools/czasowka_uwagi_oracle.py`) proves materiality and the
  HARD-vs-SOFT decision is made (note: `delivered_at` is button-press ±~3 min per the 2026-06-28 runtime-oracle
  audit, so the oracle tolerances that). OFF (default) → field is not produced (byte-identical ingest). Rollback =
  flag OFF (hot) / `.bak-pre-czasowka-uwagi-2026-06-28`. Spec: `eod_drafts/2026-06-28/CZASOWKA_UWAGI_PARSER_SPEC.md`.

`kill_switch_to_v1=true` reverts the whole v2 to the legacy `gastro_trigger.sh`.

---

## 10. Shadow system (`shadow_dispatcher.py`)

The shadow dispatcher is the **decision engine running read-only**: it consumes the same NEW_ORDER
events, runs `assess_order()`, but **sends no Telegram and mutates no state**. It serializes every
decision to **`shadow_decisions.jsonl`** (append-only).

- `_tick()` (`~:1040`) batches ≤50 events/cycle; `process_event()` (`~:1012`) is the pure wrapper.
- **Record schema** (`_serialize_result`/`_serialize_candidate`, `~:254,476`): `ts, event_id,
  order_id, restaurant, verdict, reason, auto_route{AUTO|ACK|ALERT}, would_auto_assign,
  best{courier_id, score, feasibility, km_to_pickup, travel_min, drive_min, eta_pickup_hhmm,
  pos_source, pos_age_min, r6_max_bag_time_min, r6_per_order_violations, r1_avg_pairwise_cosine,
  bundle_level*, bonus_* (all terms), plan{sequence, total_duration_min, strategy, sla_violations,
  per_order_delivery_times, predicted_delivered_at, pickup_at}, czas_kuriera_warsaw, v326_rationale,
  …}, alternatives[…]`. ~200 fields per record (LOCATION A = alternatives, LOCATION B = best).
- **Metrics completeness (L1.1, 2026-07-01, Faza 3 audytu):** serializer propaguje **KAŻDY** klucz
  `metrics` do rekordu (LOCATION A+B przez wspólny `_propagate_prefixed_metrics`), chyba że klucz
  jest jawnie wykluczony Z POWODEM w `_METRICS_EXCLUDE` (deny-lista; dziś tylko 5 REDUND-kopii pól
  planu). Stary mechanizm allowlisty `_AUTO_PROP_PREFIXES` (35 prefiksów) gubił 38 kluczy, w tym
  14 HARD (`sla_violations` detail, `eta_source`, `pickup_dist_km`, `r6_*`, `c2_*`, `d2_*`) —
  0/858 w ledgerze (audyt 30.06 B07). Nowa metryka = widoczna od urodzenia, bez rejestracji.
  Wartości sanityzowane `_json_safe` (datetime→iso, set→list, obiekt→str) — zapis nie może paść.
  Inwariant: `tests/test_serializer_completeness_l11.py`.
- **Shadow-only pattern:** a new mechanic writes a `*_shadow_delta` field + a serialized metric, runs
  7–14 days, is replayed/forward-validated by tools in `tools/` and `eod_drafts/`, and only then is
  its flag flipped 🟢. The **encoding checklist** for any new rule (or it's an invisible bug): code +
  test + shadow serializer (A+B auto przez completeness) + learning_analyzer reader + dashboard.

---

## 11. ML & autonomy layer — **all SHADOW or OFF**

> Ziomek's live decision uses **none** of this for ranking. Documented so the reviewer understands
> the latent capability and the blockers.

**`ml_inference.py` — LGBM ranker v1.1** (`ml_data_prep/models/v1.1/lgbm_ranker.txt`): behavioral
clone, **NDCG@5 = 0.852, pairwise acc = 88.45%**, ~49 features (distance/rank, bag state, courier
idle/level, district match, decision time/peak, pool tier composition). Latency caps 200 ms soft /
500 ms hard, 6 fail-soft triggers. 🟡 SHADOW — attached to the decision record only.

**Two-model (solo vs bundle), Faza-7-A2 (2026-06-20):** route per *bag state*, not GPS.
- `LGBM_solo` (empty bag): **forward pairwise 0.896** (14/14 days >0.80) — strong. Verdict **MODEL=GO**.
- `LGBM_bundle` (with bag): **forward pairwise 0.642** — only ~8 pp above random; weak.
- **Arbitrage unsolved:** when both regimes feasible (44.6% of decisions), naive merge top-1 acc is
  only ~20%. No safe regime-arbitration logic yet.
- **4 documented train/serve skews** (all *fixed in retrain*, parity 0/58 385, but **flip=NO-GO**
  until productionized): (1) `level` axis = reconstruction-availability vs live bag-state; (2)
  `delta_dist_km` pairwise-train vs pool-mean-serve; (3) haversine ×1.42 applied at serve but raw at
  train; (4) lat/lon present in shadow log, absent in live inference (privacy) — unfixable parity gap.
- Status: ⚪ OFF (`ENABLE_LGBM_PRIMARY`). Net verdict (git `c43314a`): **MODEL=GO / FLIP=NO-GO until
  skews fixed + router-by-bag-state**.

**`auto_proximity_classifier.py` — Faza-7 autonomy (AUTO/ACK/ALERT)** 🟡 SHADOW
(`AUTO_PROXIMITY_SHADOW_ONLY=true`). Thresholds T1/T2/T3 (placeholder, never calibrated): e.g. T1
`min_pool_feasible 2, min_score_margin 15, tiers (gold,std+), min_score 50`. ALERT on
parser-degraded / frozen-window / best-effort / weak-pick / Kebab-Król-dinner; ACK on czasówka /
solo-fallback / shift-end-edge; AUTO only if all conditions pass. High-risk 14–17 "death zone"
bucket boosts margin +5 (R6 breach 13–20% there vs 7–9% lunch/dinner).

**`auto_assign_gate.py` + `auto_assign_executor.py` — AUTON-01/02 (2026-06-13 / 2026-06-30)** ⚪ executor OFF.
- **Gate** `evaluate_auto_assign` = pure telemetry computed **always** (lesson #186): `would_auto_assign` +
  `auto_block_reasons` on every PROPOSE. Hard gates (always): G1 verdict=PROPOSE, G4 czasówka, G5
  paczka/firmowe, G6 new-courier-ramp, G7 informed pos (not blind/store), G8 late-pickup, G9
  R6/commit-divergence/best-effort/plan-sla, G11 score≤90 distrust ceiling, **G13 shift-end-edge**,
  **G14 parser-degraded** (G13/G14 added AUTON-02, explicit — they were implicit in G2).
- **AUTON-02 profile** (flags `AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO` / `AUTO_ASSIGN_REQUIRE_MARGIN`,
  ETAP4, **default True = strict AUTON-01**): when False → drops G2 (classifier=AUTO) and G12 (margin),
  pool gate `AUTO_ASSIGN_MIN_POOL_FEASIBLE` 3→2. This is **"plaster D"** — gated by *physics* not
  coordinator-agreement. Physical validation (calibration 2827/14d): AGREE≈OVERRIDE in delivery (R6
  breach 8.6%≈9.0%) → couriers in the feasible pool are interchangeable; coordinator-agreement is a
  *biased* gate. Slice D (pool≥2 + informed + non-czasówka/paczka) ≈ 62% vol / ~125/day at breach 5.5%
  vs 9.0% human baseline. `dispatch_pipeline` logs `would_auto_assign_d` (pool≥2) + `_dprime` (pool≥3)
  alongside strict in `shadow_decisions.jsonl` (compute-always, no execution impact).
- **Executor** `maybe_execute` (only from `shadow_dispatcher`, behind `ENABLE_AUTO_ASSIGN` killswitch,
  default **OFF**): rate-cap `AUTO_ASSIGN_MAX_PER_HOUR=6`, cooldown `AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN=60`
  after PANEL_OVERRIDE on that courier, executes via subprocess `gastro_assign.py` (ASSIGN_DIRECT path).
  ⚠ **never run E2E** — first execution must be supervised. Console killswitch: coordinator toggle
  "Autonomia Ziomka WŁ/WYŁ" (`/api/coordinator/auto-assign` → `flags_admin set ENABLE_AUTO_ASSIGN`).
  Design: `eod_drafts/2026-06-13/AUTON01_DESIGN.md` + `eod_drafts/2026-06-30/AUTON02_PLASTER_D_DESIGN.md`.

**`calib_maps.py`** 🟡 SHADOW — `eta_quantile_map` (pred→real ETA calibration; pred>25 min biased
−10..−25) and `restaurant_prep_bias` (declared vs real pickup-ready, med +9..+22 min). Consumption
gated OFF — R-DECLARED-TIME stays authoritative.

**`pln_objective.py`** 🟡 SHADOW (telemetry ON) — economic value per assignment in PLN, calibrated on
52.9k deliveries: `V = 6.33 margin − 0.90·Δkm − 14·P(breach) − 0.20·max(0,drive−ready) −
opp_cost·(blocking+waiting)`; breach logit `−5.746 + 0.297·km + 0.649·bag + 0.090·load`. A future
score/PLN selector lever, not wired to decisions.

**ETA residual R3** (`eta_residual_infer.py`) 🟡 SHADOW — LightGBM correcting OSRM ETA (held-out MAE
−13.4%), logged off-hot-path in `eta_calibration_logger`. Variant B_drop (removes the leaky
`pool_feasible` feature) forward-validated **+12% MAE = NO-GO**.

---

## 12. Tests & shadow validation

- **356 test files** in `dispatch_v2/tests/`, ~3100 cases. Runner: `pytest` with a custom
  `conftest.py` that (a) auto-blocks real Telegram (3 layers, Lekcja #75), and (b) detects
  script-style legacy tests via AST and runs them as subprocesses, so one `pytest tests/` works.
- **Live baseline (2026-06-21, this audit):**
  `3076 passed · 4 failed · 23 skipped · 2 xfailed · 2 xpassed` in ~101 s.
  (Memory recorded ~8 failed earlier — baseline has *improved*.)
- Run: `cd /root/.openclaw/workspace/scripts && PYTHONPATH=. \
  /root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tests/ -q`

**The 4 current failures are test-hygiene debt, NOT production bugs** (verified by isolating them):

| Test | Why it "fails" | Class |
|---|---|---|
| `test_eta_residual_drop::test_drop_flag_default_off_when_absent` | asserts `ENABLE_ETA_R3_DROP_SHADOW` is **absent** from `flags.json`; it was since flipped **ON** (commit `30c5b23`). Reads live state. | Stale premise / non-hermetic |
| `test_sla_preexisting_bypass::test_preexisting_breach_bypasses_sla_reject` | depends on the *full* live flag set producing a specific TSP ordering; fixture itself documents the fragility (Lekcja #191). | Non-hermetic — **needs owner triage, must not be silently "greened"** |
| `test_sla_preexisting_bypass::test_flag_off_legacy_reject` | same root cause (got `MAYBE`, expected `NO`). | Non-hermetic — needs triage |
| `test_state_schema_validator::test_synthetic_drift_flat_object_fails` | **passes in isolation, fails in full suite** → leaked global state from an earlier test. | Test-ordering contamination |

Root cause of all four: **in-process pytest tests read the *live* `flags.json`**, whereas only the
subprocess (script-style) tests get a stripped/hermetic flag file (`conftest._isolate_flags_json`).
As Adrian flips shadow flags, these tests drift. The correct fix is hermeticity, not assertion edits.

---

## 13. Tech-debt & hygiene findings (prioritized, with *safe* remediation)

> None of these were changed by this audit. Each is rated for risk; remediation respects the
> system's ACK-gate + shadow-first workflow.
>
> **Measurement update (2026-06-21).** Two items were **downgraded by direct grep**: §13.2 (haversine
> consistent live = 1.37) and §13.5 (zero bare `except:` in production core; the 18.06 "88" were
> `eod_drafts/` scripts). Then **three candidate calibration levers were settled by read-only replay**
> (verdict docs in `eod_drafts/2026-06-21/`):
> 1. **`rule_weights` R1/R5/R8 = inert** — ±2× reweighting moves ≤0.7% of selections / ≤1.6% of verdicts.
> 2. **route ETA = already calibrated** — the "+8 min underestimation" decomposes to pickup-timing slip;
>    the drive+dwell leg is median −1.2 min. ML R3 + additive correctors aren't flip-safe.
> 3. **pickup slip / `prep_bias` = irreducible per-order noise** — restaurant identity explains R²≈0 of
>    the slip; a static (per-restaurant or global) correction overcorrects, breaking 83–100% of
>    already-on-time pickups.
>
> **None is a flip-safe calibration win.** The real levers are **operational** — GPS on idle couriers,
> per-courier reliability, real-time restaurant-ready signals — consistent with the 20.06 KOORD-funnel
> finding (a GPS/obsada problem, not an algorithm one). Lesson: every headline number flipped or moved
> once decomposed — measure before acting.

**P1 — test hermeticity — ✅ DONE 2026-06-21** (commit `7093cf7`, tag `test-hermeticity-flaky-fix-2026-06-21`, pushed to origin/master).
- 13.1 The 4 flaky tests were made hermetic → baseline **3076/4 → 3080/0** (3 consecutive clean full
  runs). Root cause was confirmed **test-isolation contamination, not production bugs** (the SLA case
  was replayed against the live engine → correct `MAYBE`). Fixes: force flag-absent for the eta test;
  overlay OBJ-on via `monkeypatch` on `load_flags()` in the SLA fixture (replacing the old global
  mutation that leaked cross-file); build a synthetic schema-conformant `panel_packs_cache.json` for
  the state-schema test instead of copying the live, concurrently-rewritten file.

**P2 — calibration consistency (needs measurement + ACK; prod-affecting).**
- 13.2 **Haversine→road factor — actually CONSISTENT live (severity downgraded after audit).** The
  dispatch engine uses **1.37** (`HAVERSINE_ROAD_FACTOR_BIALYSTOK`) on the whole hot path
  (`osrm_client:373`, `feasibility_v2:109`, `dispatch_pipeline:3408/3510/5495`). The `scoring.py:206`
  `×1.3` is a `road_km is None` fallback the live pipeline never triggers (it always passes road_km
  computed with 1.37 at `:3408`/`:3484`). The `×1.42` literal is **ML-shadow-only** (`ml_inference.py`,
  train/serve alignment), orthogonal to live dispatch. **Net: no live inconsistency.** Only cleanup =
  align the dead 1.3 literal to the named constant (zero behavior change). Low priority, low risk.
- 13.3 **`rule_weights.json` recalibration — MEASURED, verdict NO-OP (2026-06-21).** Replayed 2967
  shadow decisions: reweighting R1/R5/R8 within ±2× changes the picked courier in **≤0.7%** of
  decisions and the PROPOSE↔KOORD verdict in **≤1.6%** — the penalties (−8/−6/−1.5 per unit) are
  dwarfed by the median 88-pt winning margin. The rules fire constantly (R5 violated on 57.5% of
  bundle proposals) but realized R6 breach is only 8.3%, so they don't predict failure → correctly
  SOFT by design. **Do not recalibrate the values.** The real lever for outcomes is ETA calibration
  (83.6% underestimation, +9.3 min bias — already in shadow) + R6/reliability, not these weights.
  Full verdict + reproduction: `eod_drafts/2026-06-21/RULE_WEIGHTS_RECALIBRATION_VERDICT.md`.
  (The ETA lever replayed to **pickup-timing slip**, not route ETA — and the slip itself replayed to
  **irreducible per-order noise** (restaurant R²≈0), so `prep_bias` is not a flip-safe lever either.
  All three calibration roads end at "operational, not calibration." See the three verdict docs in
  `eod_drafts/2026-06-21/` — ETA + PICKUP_SLIP_PREP_BIAS.)

**P3 — structure / hygiene (larger refactors, ACK-gated).**
- 13.4 **God objects.** `dispatch_pipeline.py` 6251 lines incl. one 3433-line `_assess_order_impl`;
  `telegram_approver.py` ~3500 lines. Hard to test stage-by-stage. Extract pure stages
  (`_stage_feasibility`, `_stage_select`, `_stage_verdict`). Behavior-preserving but high blast radius.
- 13.5 **Silent error swallowing — production core is CLEAN (corrected by measurement 2026-06-21).**
  A grep of the production decision modules (excluding `eod_drafts/`, `tests/`, `tools/`, `.bak`) finds
  **0 bare `except:`**. The 18.06 audit's "88" were all in one-off `eod_drafts/` replay scripts + 2
  tests — not the live engine. Hot-path handlers in `feasibility_v2`/`scoring`/`dispatch_pipeline` are
  `except Exception` with logging/sentinels per the fail-loud policy (Lekcja #32). **No production
  sweep needed.** (The `~872 except Exception` are mostly the correct fail-loud pattern, not debt.)
- 13.6 **Duplication.** `_bucket`-style time bucketing exists in ≥3 forms across files; unify when the
  selector is refactored. `R6 anchor-selection` logic is duplicated between `feasibility_v2` and
  `route_simulator_v2` (DRY risk if one drifts).
- 13.7 **JSON-as-database.** ~80 `dispatch_state/*.json` files (some multi-MB: `geocode_cache.json`,
  `customer_dwell.json`) with no schema/index, protected only by `STATE_WRITE_GUARD` after the
  2026-05-18 clobber incident. Largest ones are SQLite candidates.
- 13.8 **`.bak` clutter (gitignored).** `common.py` alone has ~25 `.bak-*` files; `.aider.chat.history.md`
  is 5.6 MB. These are local rollback nets (the patch workflow depends on them) — **do not delete
  blindly**; an age-based archive script (keep last N per file) is the safe path, with ACK.

**Security posture (good):** `.gitignore` excludes `.secrets/`, `*.env`, `*.key`, `credentials*`;
secrets are not in the tree. No hardcoded credentials were found in the core decision files. Telegram
token / chat IDs live in `.secrets/`. Tests have 3-layer defense against accidental real Telegram
sends. Nothing alarming surfaced.

---

## 14. Calibration review guide — questions for the reviewing AI

The most useful places to focus an external recalibration analysis (with the data needed to do it):

1. **Base-weight balance (0.30/0.25/0.25/0.20).** Is 30% distance / 25% load / 25% direction / 20%
   time still right for a 30-courier peak? Test via `shadow_decisions.jsonl` joined to actual delivery
   outcomes: does a higher final score correlate with on-time delivery? Target ≥85% concordance.
2. **`DIST_DECAY_KM = 5.0`** sets how sharply distance dominates. With Białystok ride distances, does
   exp-decay over-favor near couriers vs better-positioned bundles? (Per-city scaling is already a
   noted future need.)
3. **R6 anchor & the 35-min hard rule.** It is the *single* hard quality gate. Is `pickup_ready_at`
   the right thermal anchor for all restaurants, or is the (currently OFF) `prep_bias` table needed?
   The shadow `eta_quantile_map` shows ETA bias of −10..−25 min for predictions >25 min — does that
   imply systematic R6 mis-rejection?
4. **`rule_weights.json` (R1 −8 / R5 −6 / R8 −1.5) — RESOLVED 2026-06-21: low-leverage, leave as-is.**
   Replay sensitivity measured: ±2× changes selection ≤0.7% / verdict ≤1.6%. These weights are not a
   useful calibration lever; focus on ETA calibration + R6/reliability instead. See §13.3 + the verdict
   doc. (Listed here only so the reviewer doesn't re-derive it.)
5. **Tier speed/DWELL coupling.** Speed mult and DWELL dropoff were both recalibrated 2026-06-10 from
   the same data — is there double-counting of tier slowness (penalized once in speed score, again in
   DWELL ETA)?
6. **Two-model arbitrage (the real ML gap).** solo=0.896, bundle=0.642; the unsolved question is *how
   to choose the regime when both are feasible*. This is where ML could most help — but only after the
   4 train/serve skews are productionized.
7. **Verdict-gate thresholds.** `MIN_PROPOSE_SCORE −100`, commit-divergence 10 min, no-GPS uncertainty
   +12 min — are these producing too many KOORD escalations? (Memory note 2026-06-20: most KOORDs trace
   to *missing GPS on ~7 couriers*, not the algorithm — a data/ops lever, not a weight.)

---

## 15. Appendix

**Key paths:** code `…/scripts/dispatch_v2/`; venv `/root/.openclaw/venvs/dispatch/`; state
`…/workspace/dispatch_state/`; logs `…/scripts/logs/`; flags `…/scripts/flags.json`; rule weights
`…/dispatch_state/rule_weights.json`.

**Order status (`id_status_zamowienia`):** 2 new · 3 en-route · 4 waiting-at-restaurant · 5 picked-up
· 6 delay · 7 delivered · 8 not-picked · 9 cancelled (panel-watcher ignores 7/8/9). `id_kurier=26` =
virtual "Koordynator" holding bucket for *czasówki*.

**Glossary:** *worek* = courier's current bag; *czasówka* = order with restaurant-declared hard pickup
time (`czas_odbioru ≥ 60`); *elastyk* = flexible/ASAP order (`czas_odbioru < 60`); *czas_kuriera* =
declared courier arrival time at restaurant (frozen once committed); *koordynator* = human dispatcher;
*paczka* = parcel (no thermal deadline); *pos_source* = how the courier's position was obtained
(gps / bag / last-known / no_gps fiction).

**Verification basis:** all base-score, traffic, tier-cap, speed, DWELL, wait-penalty, and gate
constants in this document were read directly from source on 2026-06-21 and match. Line numbers
without `~` are exact; `~` marks an approximate region inside `dispatch_pipeline.py` (6251 lines) where
the agent citation was not line-verified.

*End of reference.*

### D.3 fala A/B — flagi route/kanon (KANON=flags.json od 2026-07-02, migracja z env-frozen)
- `ENABLE_PLAN_REAL_PICKED_UP_AT` — przekazuje realny picked_up_at do symulatora (kara R6 chroni niesione).
- `ENABLE_PLAN_SEQUENCE_LOCK` — sekwencja worka zamrożona, tick tylko re-czasuje (bez re-TSP).
- `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` — natychmiastowa re-decyzja sekwencji na override/reassign (pw).
- `ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP` — re-decyzja także po ODEBRANE (zmiana bag_signature).
- `ENABLE_CARRIED_AGE_TZ_FIX` — poprawne parsowanie picked_up_at (naiwny Warsaw), realny wiek carried w relaxie.
- `ENABLE_LEX_COMMITTED_WINDOW` / `ENABLE_LEX_COMMITTED_WINDOW_SHADOW` — constrained-lex okno odbioru (APPLY / SHADOW).
- `ENABLE_RELAX_COLOC_PICKUP` — współlokalny odbiór (start==restauracja) brany od razu, nie po powrocie.
- `ENABLE_NONCARRIED_DROPOFF_REORDER` — min-jazda reorder dropoffów w worku bez niesionych.
- `ENABLE_V326_OR_TOOLS_TSP` / `ENABLE_V326_SAME_RESTAURANT_GROUPING` — para atomowa (OR-Tools TSP + same-restaurant grouping); rozjazd = double-insert super-pickupa (#13, check_v326_pair_coherence).

### FALA-2 (2026-07-02) — observability
- `ENABLE_DATA_ALERTS` — MASTER monitor DANOWY (`observability/data_alerts.py`, timer 5 min): 5 sygnałów edge-triggered (sentinel-rate / empty-pool / stale-grafik / stale-GPS / ledger-stall). OFF = oneshot no-op exit 0. ON = log `scripts/logs/data_alerts.log` + stan `dispatch_state/data_alerts_state.json`; NIE dotyka decyzji silnika (czysty odczyt).
- `DATA_ALERTS_TELEGRAM` — druga bramka: alerty danowe idą też na Telegram (wymaga MASTER ON). Default OFF (log-only).

### L2.2 (2026-07-02) — alert data-poison
- `ENABLE_V328_POISON_ALERT` — alert (Telegram admin) gdy klasyfikator `v328_fail_causes` (L2.2, LIVE od 02.07 11:45) wykryje `data_poison` w świeżych decyzjach (catch-all V328 rozróżnia zatrucie danych od real_bug). OFF = tylko serializacja przyczyn do ledgera (bez alertu). Flip 03.07 ~00:55 off-peak za GO Adriana. Rollback hot = false.

### L7 tanie podkroki (2026-07-03) — tripwire R-DECLARED + split-layer guard + strażnik flag
- `ENABLE_R_DECLARED_TRIPWIRE` — L7.1: OBSERWACYJNY tripwire reguły HARD R-DECLARED-TIME (`czas_kuriera >= czas_odbioru_timestamp`) w chokepoincie `state_machine.upsert_order` (jedyny funnel commitowanego czas_kuriera; wzorzec L2.1). Naruszenie → WARNING + append `dispatch_state/r_declared_tripwire.jsonl` (throttle per oid+sygnatura). NIGDY reject/zmiana decyzji (always-propose); OFF = zero kodu ścieżki (bajt-parytet). Pomiar przy budowie 03.07: 11/168 zleceń (~6,5%) łamało regułę. FLIP ON 03.07 ~13:20 za ACK Adriana (restart shadow+pw 13:18/13:19). Rollback hot = false.
- `ENABLE_SPLIT_LAYER_GUARD` — L7.3: OBSERWACYJNY strażnik warstw w `dispatch_pipeline`: (INV-LAYER-1) re-assert `_assert_feasibility_first` na KAŻDYM EMIT (lejek `_classify_and_set_auto_route`, 11 call-site'ów; best_effort/solo poza zakresem bramką pool_feasible_count>0) + (INV-LAYER-2) kanalizacja zapisów `feasibility_verdict` przez setter `_set_feasibility_verdict` (jedyna mutacja poza L5 = FEAS_CARRY_READMIT z warstwy selekcji → logowana). Naruszenie → WARNING + `dispatch_state/split_layer_guard.jsonl`. OFF = INERT/bajt-parytet. FLIP ON 03.07 ~13:20 za ACK. Rollback hot = false.
- `ENABLE_FLAG_FINGERPRINT_GUARD_ALERT` — druga bramka strażnika flag (`tools/flag_fingerprint_guard.py`, timer 30 min): wysyłka Telegram edge-triggered dla DRIFT/COLD (COLD tylko po korelacji z journalem — anty-§20). Default OFF = log-only do `dispatch_state/flag_fingerprint_guard.jsonl`. Flip = osobny ACK po ≥1 dniu obserwacji log-only.

### L6.C — geometria w selekcji + de-pile (2026-07-04, sprint C1+C2+C3; design `eod_drafts/2026-07-04/L6C_SPRINT_DESIGN.md`)
- `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` — L6.C2 (R2 ROOT-7): człon `deliv_spread_km` (już policzony w feasibility, empty-bag→0.0) jako OSTATNI term kanonu `objm_lexr6.lex_qual` — SOFT tie-break podrzędny wobec osi czasowej R6→committed→new-late (INV-LAYER-5: nie osłabia HARD). Konsumenci AUTO przez kanon: d2_pick LIVE, best_effort_objm, feas_carry_readmit, cień, `global_allocate` (resweep/przerzut przez assess_order). Leczy klasę „279 propozycji spread>8km" (C10-oracle 30.06) i jest ZAKODOWANĄ bramką flipu `PENDING_RESWEEP_LIVE` (`pending_global_resweep.live_gate_open`). Default OFF = krotka bajt-identyczna. Flip za ACK po replayu ON↔OFF.
- `LEXQUAL_TIME_QUANT_MIN` — pokrętło kwantyzacji termów czasowych lex_qual do kubełków N-min (float, 0.0=OFF; aktywne TYLKO z geometrią ON). Czysty append rozstrzyga wyłącznie idealne remisy floatów — pod scarcity geometria mogłaby nie odpalać; kubełkowanie zlewa bliskie remisy. Wartość = decyzja z pomiaru (replay quant=0 vs 1.0), nie zgadywana.
- `ENABLE_ENGINE_CLAIM_LEDGER` — L6.C3 (R2 ROOT-8 / INV-LAYER-4): `shadow_dispatcher._tick` po PROPOSE dokłada zwycięzcy zlecenie do JEGO worka w snapshocie floty (wspólny `claim_ledger.tentative_assign` — ten sam mechanizm co global_allocate; zero 2. kopii) → kolejne eventy tego samego ticku widzą obciążenie (korzeń pile-onu: 447 proponowany 127×/32 zlecenia, g_maxpile=7). Marker mierzalności: `claim_ledger_applied` top-level w shadow_decisions. Default OFF = flota niemutowana (bajt-parytet). Flip za ACK po replayu.
- `ENABLE_CLAIM_LEDGER_INVARIANT_CHECK` — Sprint B (INV-FEAS-NO-DOUBLE-BOOK, Kontrakt ②): strażnik LOG-LOUD spójności claim-ledger. Weryfikuje ślad claimów sweepu/ticku (`claim_ledger.verify_no_stale_claim`): kolejne claimy TEGO SAMEGO kuriera muszą widzieć worek rosnący o +1 (poprzedni doklejony); regres/pile-on → worek nierosnący → naruszenie `stale`, `log.error` + metryka `g_claim_ledger_breaches` (resweep jsonl+summary). Wpięty w OBA bliźniaki: `pending_global_resweep.global_allocate` + `shadow_dispatcher._tick`. STRAŻNIK, NIE reguła — allocation bajt-identyczna ON vs OFF (dowód: 5000 sweepów, 3092 z bundlingiem, 0 fałszywek). LIVE-obserwacja od 2026-07-08 (ACK Adriana). Siostrzana `ENABLE_CLAIM_LEDGER_INVARIANT_HARD` (twarda blokada — raise) NADAL OFF, za osobnym ACK po 2-dniowym dowodzie zero-FP na żywo.

### L5.1 — ETA load-aware (2026-07-05, Sprint 1 Z3; merge `69727c9`)
- `ENABLE_ETA_LOAD_AWARE` — bufor optymizmu nogi ODBIORU z tabeli kalibracji `dispatch_state/eta_load_aware_calib.json` (bufor = clamp(−med(err) per tier×solo/bundle, 0, 12); generator kalibracji joinuje importem z `eta_truth_map` — zero drugiej kopii; fail-soft: brak/zepsuta tabela → bufor 0). OFF (default, stała-fallback `common.py`) = shadow-only: metryki `eta_la_buffer_min`/`eta_pickup_load_aware_utc` liczone ZAWSZE w pętli kandydatów (zbiórka żywa od restartu shadow 05.07 18:44 UTC), decyzja nietknięta. ON = bufor przesuwa `eta_pickup_utc`/`travel_min` — **oś OBIETNICY odbioru, NIE feasibility** (`feasibility_v2` nietknięte; GATE-STRICTER/„nie zdąży→nie dostaje" = osobny pas za ACK, inwersja HARD). Replay out-of-sample PASS (`dispatch_state/eta_load_aware_replay_verdict.txt`): bias med −3,73→+0,42 (n=415), celność |err|≤5 +1,4 pp, **trade-off ogona p90 +6,1→+10,7 min — flip (S2 K4b) WYŁĄCZNIE po jawnej akceptacji trade-offu przez Adriana**. Klucz w flags.json od S2 K4a (05.07, wartość false). Rollback hot = false.

### PERF SLO (FALA-1 2026-07-02, flip 2026-07-04)
- `ENABLE_PERF_SLO_ALERT` — sekcja SLO budżetu wydajności w canary (`tools/perf_budget_report.py` + monitor): przy breachu progów p50/p95/ogona latencji decyzji emituje alert Telegram. OFF = log-only (raporty perf liczone zawsze, bajt-parytet decyzji). **FLIP ON 04.07 08:00 UTC przez at-209** (`scheduled_flip_gate --profile perf-slo`, ACK Adriana 03.07; raport pre-flip `eod_drafts/2026-07-04/perf_budget_report_pre_slo_flip.*`). ⚠ 1. alert po flipie = spodziewany breach peak-p95 (ogon peaku = osobne źródło niż naprawione IO — `perf_verdict_at207.md`) — świadomy żywy tracker regresu ogona, nie awaria. Rollback hot = false w flags.json.

### FALA SERIAL S2 (2026-07-02) — grafik
- `ENABLE_GRAFIK_ENTRY_SALVAGE` — czytana przez `scripts/fetch_schedule.py:_flag` (poza repo; prosty json.load flags.json, hot per bieg fetch/T3). OFF = niepełna para godzin w grafiku (np. sam start `'12'` albo sam koniec `'15'`) kasuje CAŁY wpis kuriera → `v325_NO_ACTIVE_SHIFT` na cały dzień. ON (LIVE od 02.07 ~14:15) = wpis z ≥1 poprawną godziną ZOSTAJE (`parse_degraded: true`, druga godzina None → `is_on_shift` fail-open) + WARNING z nazwą i surową komórką. Replay 42 dni: 12 utraconych kurier-dni (0,29/dz), BOTH_BAD=0. Rollback hot = false.

### FALA SERIAL S1 (2026-07-02) — konsolidacja 35-min HARD
- `ENABLE_SLA_ANCHOR_UNIFIED` — 35-min HARD (R6 carried-age ↔ SLA dostawy) liczony z JEDNEGO źródła `sla_anchor.py` z JAWNĄ kotwicą (ready vs now), w 3 bliźniakach RAZEM (`route_simulator_v2._count_sla_violations` + feasibility SLA-loop + R6 per-order; `plan_recheck._o2_key` dziedziczy przez `plan.sla_violations`). OFF (default) = inline bez zmian, bajt-w-bajt (fuzz 400/0 mismatch). ON = TE SAME decyzje (werdykt+reason+sla_violations identyczne) + metryka obs `sla_anchor_source` (de-maskowanie: naruszenie każdej kotwicy niezależnie widoczne; mutation-probe 5/5). Prerekwizyt flipu O2 (finding feas-r6-sla-anchor-gap). Flip: wpis do flags.json (hot) + restart shadow + 2 dni obserwacji; rollback hot = false.

### FALA SERIAL O2 cap-Z (2026-07-02) — wąska reguła Opcji 3 + gate-fix kotwicy
- `ENABLE_O2_CAPZ_RESEQ` — WĄSKA reguła Opcji 3 Adriana OBOK surowego `ENABLE_O2_READY_ANCHOR_SWEEP` (nietknięta). Na WYBRANYM planie każdej strategii (ogon `route_simulator_v2.simulate_bag_route_v2` → greedy+ortools+bruteforce; feasibility + plan_recheck DZIEDZICZĄ) silnik może preferować przeplot zmniejszający overage świeżości (Σ max(0, age_ready−35), jedzeniówki, paczki wyłączone `ENABLE_PACZKA_R6_THERMAL_EXEMPT`) TYLKO gdy JEDNOCZEŚNIE: (a) drive-detour ≤ `O2_CAPZ_DETOUR_MAX_MIN`=8 (review Z=20 p90 7.93), (b) max wiek NIESIONEJ jedzeniówki ≤ `O2_CAPZ_Z_MIN`=20 (rekom. review = max ochrona niesionego), (c) overage niższy o ≥ `O2_CAPZ_MIN_GAIN_MIN`=2 (argmin), (d) sla_violations NIE większe (SOFT nie osłabia HARD; carried-first zachowane przez lock_first). Brak kandydata pod capami → kolejność BEZ ZMIAN. Metryka obs `o2_capz`={considered,applied,blocked_by_cap,detour_min,overage_saved_min} (auto-serializacja L1.1). OFF (default) = bajt-w-bajt (fuzz 500 vs kanon 0/0 mismatch). ON = replay korpus bundle_calib: engine-improved 7.3% (vs review 7.9%, med gain 10.5min, detour p90 5.06 ≤ cap; genuine-regres 0 — λ-artefakty odfiltrowane guardem gain≥2). Progi Z/X/Y z `bundle_calib_review_verdict_2026-07-02.txt`. Flip = OSOBNY ACK po replay; rollback hot=false. `O2_CAPZ_MAX_STOPS`=8 (sufit enumeracji; >8 stopów → keep, konserwatywnie).
- `ENABLE_SLA_GATE_READY_ANCHOR` — gate-fix (finding feas-r6-sla-anchor-gap): bramka 35-min SLA (`_count_sla_violations` + feasibility SLA-loop, S1-unified twins) przestawiona z kotwicy NOW (pickup_at) na READY (od gotowości) — WYŁĄCZNIE przez źródło `sla_anchor.anchor(kind='ready')`; działa tylko gdy `ENABLE_SLA_ANCHOR_UNIFIED` ON. Co-design z `ENABLE_ETA_QUANTILE_R6_BAGCAP` (gold≤4 p80 kalibracja PRZED porównaniem — inaczej naiwne ready-anchorowanie re-rejectowałoby zlecenia, które R6-gate QUANTILE odzyskuje) + `ENABLE_PACZKA_R6_THERMAL_EXEMPT` (paczki pominięte). REALNA zmiana decyzji: replay ON↔OFF (S1 unified ON, proxy syntetyczne): PURE ready-anchor (QUANTILE OFF) = 0 flipów werdyktu, ΔNO=0 (R6-gate już ready-anchoruje → SLA-gate switch = tylko re-atrybucja reason, ~48% reason-class churn → wpływa na downstream `_kind()` best-effort/promocja); z QUANTILE ON (live) = +4% gold NO→MAYBE (co-design zgodności SLA↔R6-gate). OFF (default) = NOW-anchor bez zmian. Flip = OSOBNY ACK (nie łączyć z cap-Z); wymaga przeglądu downstream `_kind()` + dłuższego real-shadow replay; rollback hot=false.

### Fale L3/L4 (2026-07-02, flipy trwałym `at` z bramką `scheduled_flip_gate` — at-202/203 So 04.07)
- `ENABLE_PLAN_RECHECK_GATES` — L3: bramka ZAPISU regenu `plan_recheck` (compare-and-keep): regen, który pogarsza R6 carried>35 sekwencyjnie, NIE nadpisuje dobrego istniejącego planu. Nie zmienia scoringu live — tylko decyzję „zapisać regen czy zostawić". OFF = zapis regenu bajt-w-bajt jak dotąd. **FLIP ON 04.07 12:35 UTC (at-202, bramkowany)**; rollback hot = false w flags.json.
- `ENABLE_COURIER_PLANS_GC` — L3: GC `courier_plans.json` (prune terminal-stopów + zombie-plany by age/no-active) przez API `plan_manager` pod lockiem. Sprzężona z `PLAN_GC_DRY_RUN` (default True = tylko raport; realny GC = flip dry_run→false, at-205 Pn 06.07 12:40 z bramką świeżego dry-run). OFF = brak GC jak dotąd. **FLIP ON (dry) 04.07 12:35 UTC (at-202)**; rollback hot = false.
- `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` — L4: jedno źródło `available_from = max(now, shift_start)` w `courier_resolver`; konsumenci (#1/#3/#5 + chokepoint `dispatch_pipeline`) DZIEDZICZĄ zamiast liczyć po swojemu (koniec dryfu kopii). OFF = stare ścieżki bajt-w-bajt. **FLIP ON 04.07 12:50 UTC (at-203, bramkowany; verify at-204 14:30)**; rollback hot = false.

### Sprint B2 (2026-07-18) — migracja committed tie-break do flags.json (fix „mruganie kolejności")
- `ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION` — committed tie-break w `_gen_one_bag_plan` (plan_recheck :~839): liczy plan bazowy ORAZ wariant świadomy `czas_kuriera_warsaw` (okno frozen + miękka kara N5 w symulatorze) i **adoptuje świadomy TYLKO gdy nie pogarsza SLA dostaw** (O2 ON → porównanie `_o2_key`; OFF → `sla_violations`) — markery logowe `COMMITTED_TIEBREAK_ADOPT/REJECT`. **HISTORIA/MIGRACJA:** do 18.07 env-frozen module-const (drop-iny =1 tylko w plan-recheck + b-route-shadow + carried-first-guard) → panel-watcher (`redecide_courier` → `_gen_one_bag_plan`, ścieżka od F3 07.06) liczył **OFF**, tick **ON** = kolejność odbiorów MRUGAŁA między recanonem zdarzeniowym a tickiem (KANON §9 B2; fałszywa kuracja „single-service" w flag_registry wykryta audytem parytetu 18.07). **Od 18.07: KANON=flags.json (True)** przez `decision_flag` + lista `_D3_FALA_A_FLAGS` (hot-reload w pw przez `_refresh_d3_fala_a_flags` na wejściu recanon/redecide) + const-fallback `common.py`=True (steady-state) + wpis ETAP4. Dowody: testy ON≠OFF + guard + wiring (`test_committed_propagation_b2.py` 3/3), replay A/B na żywych workach 18.07: 5/5 identyczne OFF↔ON, szum OFF↔OFF2 = 0 (przed-peakowy snapshot — adopcje wystąpią w peaku; okno obserwacji 2 dni: `COMMITTED_TIEBREAK_*` z journala pw vs plan-recheck). Rollback hot = `false` w flags.json (pw łapie bez restartu — hot-reload; timery następny tick) / `.bak-pre-b2-migration-2026-07-18` / git revert.

### Sprint D3-gold (2026-07-18) — koniec wyjątku klasowego R6 dla gold (flip OFF)
- `ENABLE_ETA_QUANTILE_R6_BAGCAP` — **FLIP OFF 18.07 ~13:45 UTC (GO Adriana „Dawaj D3-gold")**: wdrożenie werdyktu **D3 (29.06: „BEZ WYJĄTKÓW: 35 dla KAŻDEGO — usuń recovery gold≤4")** + **OD-07 OWNER_CONFIRMED** (R6 nigdy klasa kuriera). Mechanizm (LIVE 14.06→18.07): gold z workiem ≤4 miał bramkę R6 na skalibrowanym p80 zamiast surowego czasu (`feasibility_v2` `_gate_bt`, metryka `r6_gold4_gate_recovered`) + uśpiona gałąź co-design z `ENABLE_SLA_GATE_READY_ANCHOR` (OFF) — jedna flaga gasi obie. **Pomiar live przed flipem: 39 odzysków/3 dni (~13/d), ale odzysk na ZWYCIĘZCY 0/39 → flip nie zmienia żadnego wyniku na korpusie**; ówczesny dowód wdrożeniowy (CI[−0.63,+1.25]) był słaby i został nadpisany kanonem właściciela. Odczyt `C.flag` = hot-reload (flip bez restartów). Testy gałęzi (nowe, ON≠OFF): `test_d3_gold_quantile_flip.py` 4/4 — w tym strażnik „wyłącznie gold" pod przyszłe USUNIĘCIE kodu gałęzi (cleanup po oknie, za ACK). Kalibracja prędkości gold („wyrabiają 35 bo szybcy") = osobne TODO z werdyktu. Okno 2d: at#217 sekcja D3 (0 nowych odzysków + sanity werdyktów). Rollback hot = `true`.
- `ENABLE_DRIVE_SPEED_TIER_CORRECTION` + `DRIVE_SPEED_MULT_BY_TIER` — **TODO werdyktu D3 („skalibruj prędkość gold") DOMKNIĘTE POMIAREM 18.07 wieczór (GO Adriana), flip ŚWIADOMIE ZANIECHANY**: na 2953 czystych bezpośrednich nogach z `eta_calib.db` (2 spójne okna; pierwszy naiwny bieg = lekcja C10 — kontaminacja stopami worka dawała ~1.6× dla wszystkich) mediany ratio jazdy: **gold 0.96 / std+ 1.06 / std 0.86 / new 0.95** → stara tabela 26.06 (gold 0.78, „Krok 1 agresywny" na czasie skażonym workiem) **OBALONA** (flip 0.78 zaniżyłby ETA gold o 22% → fałszywe „zdąży" → breache R6; NIE WSKRZESZAĆ). Zysk zmierzonej tabeli: MAE ETA dostawy 3.01→2.92 min (~3%, gold −2%) = o rząd słabszy niż **kalibrator per-leg/per-KURIER** — ⚠ KOREKTA 18.07: historyczne „−52/−20" z 07.07 są WYCOFANE przez A360-A0 (leakage); świeże leak-free rolling (18.07): odbiór −54%/dostawa −17% vs silnik, ALE instrument w fail-closed HOLD (champion legacy, zakaz bootstrap) + brama właścicielska OD-01..03 — karta decyzyjna: `eod_drafts/2026-07-18/ETA_CALIB_OWNER_DECISION_CARD.md`. Tabela w common = wartości zmierzone (kod nie kłamie) + `test_drive_speed_mult_d3gold.py` 3/3 (OFF-inert każdy tier · tabela==pomiar anti-drift · ON czyta tabelę). Flaga zostaje **OFF**. Pomiar: `eod_drafts/2026-07-18/gold_speed_mult_measure.py` + `GOLD_SPEEDMULT_EVIDENCE.md`.

### Sprint ETA-CALIB D1-D7 (2026-07-18 wieczór) — binding KPI + bootstrap championa + serving obietnic (SHADOW)
- **OWNER_CONFIRMED D1-D7** (Adrian, odpowiedź na kartę `eod_drafts/2026-07-18/ETA_CALIB_OWNER_DECISION_CARD.md`; rekord memory `owner-decision-eta-calib-d1-d7-2026-07-18`): D1a possession-KPI=`restaurant_last_inside_at` (JAWNE PROXY, klik=fallback niższej rangi) · D2a arrival-KPI=`delivery_arrival_at` (handoff OSOBNY, unbound) · D3a kotwica=predykcja w momencie decyzji · D4 unknown-package poza mianownikiem + **coverage-gate ≥60% complete-case i n≥200/komórkę (inaczej HOLD)** · D5 progi flipu obietnic (odbiór MAE≤6 i ≥25% vs silnik; dostawa ≤8 i ≥10%; late-band 15-22%; |bias|≤1,5; p90≤20) · D6a zakres = WYŁĄCZNIE obietnice/prezentacja · D7 jawny bootstrap championa v2.
- **Binding w kodzie:** `tools/eta_ground_truth.KPI_BINDING_V1` (wersjonowany, owner_ack; raport emituje `canonical_kpi_event` związane + `business_kpi.thresholds/coverage_gate` zamiast „unbound/not_bound"). Test-pin: `test_eta_calib_promise_shadow::test_kpi_binding_matches_owner_decisions`.
- **Bootstrap D7 WYKONANY 18.07 ~18:45:** kandydaci leak-free z nocy 18.07 (sha `a565499b…`/`b197b26b…`) skopiowani bajt-w-bajt na mapy championów + sidecar `eta_calib_champion_provenance.json` + backupy legacy `.bak-legacy-pre-d7-bootstrap-2026-07-18`. **Dowód mechaniki:** ręczny bieg calibrate → gate przeszedł z `artifact_legacy_or_unknown_schema` (n_common=0) na **`support_exact=True, n_common=3115/2542`** z merytorycznym HOLD `improvement_below_config_threshold` (challenger==champion — oczekiwane; od kolejnej nocy gate mierzy realne różnice, fail-closed).
- `ENABLE_ETA_CALIB_PROMISE_SHADOW` — **serving obietnic D6a (SHADOW, flags.json=true, const OFF):** `eta_calib_serving.attach_shadow_promise_metrics` w lejku `_classify_and_set_auto_route` dokłada dla ZWYCIĘZCY **NOWE** metryki `eta_calib_promise_pickup_p80_min`/`_delivery_p80_min`/`eta_calib_champion` (+`eta_calib_srv_skip` przy braku) do `best.metrics` → auto-serializacja do shadow_decisions. **Parytet cech z treningiem:** osrm ff = SUROWY `/route` (lustro `features.OSRM.freeflow`; silnikowy `route()` dokłada traffic-mult — NIE używać). Zero wpływu na decyzje/wyświetlane czasy (wzorzec #8 — pola OBOK); warstwa APPLY = osobna flaga przy flipie za końcowym ACK po cieniu 2 dni. Testy 7/7 `test_eta_calib_promise_shadow.py`. Rollback hot = klucz false. **Bonus sprintu: FIX seedera lifecycle** (`--merge` wycierał `known_drift_note` po domknięciu dryfu — ugryzł 2× jednego dnia; teraz pusta świeża nota nie nadpisuje niepustej starej — dowód: nota USE_V2_PARSER przeżyła merge).

### Sprint PERF A2 (2026-07-08) — deterministyczny budżet solvera OR-Tools
- `ENABLE_ORTOOLS_DET_TIME_LIMIT` — A2: solver TSP (`tsp_solver.solve_tsp_with_constraints` via `_ortools_det_budget()`) zatrzymuje się po STAŁEJ liczbie rozwiązań (`solution_limit=120`) zamiast po wall-clock `time_limit` (200ms GLS). Usuwa ~1,7% niedeterminizmu replayu (tmux31: budżet-na-zegarek zależny od obciążenia maszyny → różne trasy dla tej samej sytuacji). Sufit `ORTOOLS_DET_WALL_CEILING_MS` czytany getattrem stałej modułu (NIE z flags.json): 0 (default)=budżet callera, produkcja zero-regresji; >0=override TYLKO offline-replay determinism-first (~24s blow-up bag7-8, NIE na produkcję). `route_simulator_v2` NIETKNIĘTY. **Dowód parytetu: solver-level `perf_ortools_det_parity.py` 100% + e2e pełna fasada `decide()` `a2_e2e_parity.py` 756/756 case'ów 0 różnic materialnych** (wybór kuriera/kolejność trasy/werdykt/ranking; szum bajtowy = ambient load-governor, kontrola OFF-OFF większa). Latencja solve p50 −53%, p95 −45% (Sprint D `perf_tsp_parallel_ceiling` 40/40+103/103 exact). OFF (default) = wall-clock bajt-identyczny. **FLIP ON 08.07 ~18:57 UTC (ACK Adriana „teraz", peak; merge `320a888`, restart dispatch-shadow)**; rollback hot = false (silnik ma kod → wraca na wall-clock ≤60s) / backup `flags.json.bak-pre-a2-flip-2026-07-08` / `git revert 320a888`.
- `USE_V2_PARSER` — wybór parsera panelu v1(regex)/v2(universal-ID) w `panel_client.parse_panel_html`. Migracja 1b (ACK Adrian 10.07): read-site czyta flags.json-FIRST (`flag("USE_V2_PARSER", <env-const>)`, wzorzec dual-carrier geocode); brak klucza = fallback na env-frozen stałą modułu (dziś: drop-in `dispatch-panel-watcher`=1, pozostałe serwisy default=0 — genuine-drift z rejestru lifecycle). Testy kontraktu OBU nośników: `test_use_v2_parser_dual_carrier.py` (klucz wygrywa nad stałą w obie strony + fallback bez klucza). PAKIET FLIP **WYKONANY 10.07 ~17:45 (ACK Adrian 'flip teraz'; werdykt obserwacji: parser_health healthy/v2/anomaly=False, 0 błędów w journalach, shadow fingerprint USE_V2_PARSER=1)** — kroki: rejestracja w ETAP4 + `flags.json USE_V2_PARSER=true` (hot) + restart dispatch-shadow/panel-watcher (nowy kod) + 15 min obserwacji parser_health/journal + re-seed rejestru `--merge`. Rollback hot = klucz false/usunięty (wraca per-service consty), bez restartu.

### FALA SERIAL perf compute-zawsze (2026-07-02) — finding E audytu 2.0 (regres wydajności 2×)
- `ENABLE_PERF_LAZY_MEMBERS` — **flaga INFRA, NIE decyzyjna** (zmienia KIEDY liczymy, nigdy TREŚĆ decyzji → poza `ETAP4_DECISION_FLAGS`; kanon = flags.json, stała-fallback `common.ENABLE_PERF_LAZY_MEMBERS=False`, env override tylko test/harness). Klasa (iv) „to samo liczone N× w jednej decyzji" — dwa cache pod JEDNĄ flagą:
  - **flag-load fast path** (`common.load_flags`): `flag()`/`decision_flag()` wołane ~700×/decyzję robiły `FLAGS_PATH.stat()` KAŻDE (zmierzone cProfile ~740 stat/decyzję przez 10 kandydatów w ThreadPoolExecutor). ON = re-stat najwyżej co `PERF_FLAGS_STAT_TTL_S`=0.25s; w oknie TTL zwraca cache bez stat (mutation-proof: 200 odczytów → 1 stat vs 200). Gate po EFEKTYWNEJ fladze odświeżanej przy reloadzie JSON (bez rekurencji).
  - **plans read-cache** (`plan_manager.load_plan`/`load_plans`): `load_plan` czytany PER KANDYDAT → N× pełny `_read_raw` (open+json.load) POD fcntl-lockiem → wątki serializują się (kontencja rośnie w peak). ON = cache po `(mtime_ns, size)` NAD lockiem (ciepły hit pomija fcntl+open+json.load); WYŁĄCZNIE ścieżki READ, zwrot przez `copy.deepcopy` (caller nie mutuje współdzielonego cache). WRITERS (save/invalidate/advance/insert) dalej surowy `_read_raw` pod exclusive lock; `os.replace` bumpuje mtime → cache sam się unieważnia.
- **Bajt-parytet**: OFF vs ON = 0 mismatch na fuzzie ≥400 realnych zdarzeń (events.db, flota 0/3/5/8/10/12, werdykty PROPOSE+KOORD+best-effort), po wykluczeniu WYŁĄCZNIE pól czysto-czasowych (`latency_ms`, `r07_compute_latency_ms`, `osrm_cache_age_s`, `lgbm_*.evaluation_ts/latency_ms`, `ts`) — potwierdzone kontrolą OFF vs OFF (identyczne). OFF (default) = ścieżka bajt-w-bajt sprzed fali.
- **Pomiar (replay, fleet=10, nice-19)**: p50 402→312 ms (**−22,4%**), mean 389→325 (−16,5%), p95 566→470 (−17%); flags-frozen −63 ms, plans-cached −47 ms (stackują). Offline single-proc = DOLNA granica (peak: kontencja fcntl-locka planów + stat spam między równoległymi decyzjami → większy zysk). Strażniki: `tests/test_perf_lazy_members.py` (7 behawioralnych + mutation ×3: const-key/no-deepcopy/TTL=0 KILL). Flip = OSOBNY ACK (wpis flags.json + restart shadow + pomiar p95 live przed peakiem); rollback hot=false.

### Sprint 3 Z-P1-03 (2026-07-11) — pełny stage timing, LIVE SHADOW CANARY
- `ENABLE_STAGE_TIMING_OBSERVATION` — **flaga INFRA/obserwacyjna, NIE decyzyjna**.
  Brak klucza i fallback `common.ENABLE_STAGE_TIMING_OBSERVATION=False` oznacza
  ścieżkę OFF: bez `DecisionTrace`, dodatkowego query głębokości kolejki,
  `timing`/`queue_timing`/`event_ref` w głównym ledgerze i bez sidecara. ON
  mierzy queue/fleet/pre-recheck/fan-out/OSRM/solver/selection/write/ACK/E2E w
  kontraktach `decision_timing.v1` i `decision_stage_timing.v1`; nie narzuca
  budżetu ani backpressure i nie jest wejściem selection/feasibility/scoring.
  Stan jest snapshotowany raz na tick, więc hot reload działa między batchami,
  nie w połowie batcha. Sidecar ma pseudonimowe referencje, `0600` i osobny
  wersjonowany logrotate `deploy/stage-timing-logrotate.conf` (`daily`,
  `rotate 30`, `maxsize 100M`; próg rozmiaru rotuje wcześniej i nie wyłącza
  rotacji dziennej). `tools/paired_flag_replay.py` wstrzykuje flagę do
  zamrożonego world record wyłącznie w pamięci; zwykła podmiana live flags nie
  jest testem ON, bo replay odtwarza snapshot flag rekordu. Na korpusie 202
  rekordów oba porządki OFF/ON miały zero różnic `verdict/best_cid/best_score`,
  ale wykazały miękki scheduling drift `pool_feasible+reason`, więc nie ma
  deklaracji pełnego byte-parity.
  **LIVE od 2026-07-11T10:27:12Z za ACK Adriana:** klucz `true` w flags.json,
  logrotate zainstalowany, jeden restart `dispatch-shadow`, fingerprint=1,
  sidecar 0600. Pierwszy join 1/1, zero missing/orphan/duplicate/incomplete,
  service 2121,608 ms i append ledgera 0,644 ms. Canary minimum 48 h; at-214
  wykona raport 13.07 12:15 UTC. Rollback hot = `false`; błąd odczytu flagi
  fail-closed do OFF. ETA, backpressure i cache eviction pozostaja bez zmian.

### A360-A0 (2026-07-11) — prawdziwa bramka kalibracji ETA, LIVE tool/HOLD

- Kalibrator nie moze budowac decision-time feature z pola outcome. Champion i
  challenger sa porownywane na tym samym zamrozonym supporcie i paired errors;
  promocja wymaga odtwarzalnego artifactu championa o znanym schema.
- Brak/legacy champion, rozny support albo niespojny artifact daje fail-closed
  `HOLD`, nie automatyczny fallback do wygladajacego lepiej agregatu.
- Kontrolowany bieg live 11.07 zakonczyl sie `Result=success`, ale oba ramiona
  mialy `promote=false`, powod `artifact_legacy_or_unknown_schema`.
  Candidate zostal zapisany, champion maps pozostaly bajtowo bez zmian.
- Source: dispatch `2595bed`, finalne wydanie `4c351d5`, tag
  `a360-a0-n0-live-verified-20260711`. Nie istnieje flaga ani termin
  automatycznej promocji. Rollback: zatrzymany timer + tag pre-live i spójny
  restore SQLite/map z backupu opisany w raporcie wydania.

### A360-N0 (2026-07-11) — night guard fail-closed, LIVE

- Manifest v4 przypina dokladny zbior 5171 nodeidow i oczekiwane outcome.
  Unexpected/missing/duplicate/not-run, hard-error i XPASS sa jawnym ALERT;
  hard-error nie jest kandydatem na baseline.
- Pytest child dostaje jawny package-parent w `PYTHONPATH`, bo plugin jest
  importowany przed rootdir discovery przy cwd uslugi systemd. Testy pluginu
  snapshotuja i odtwarzaja globalny collector, aby nie skazic outer-run.
- Finalny dokladny systemd E2E: 5140 passed, 27 skipped, 8 xfailed,
  0 failed/XPASS, `verdict=OK`, `contract_ok=true`, baseline eligible.
  Timer jest active; najblizszy bieg 12.07 01:15 UTC.
- Source/fix-forward: `8056319`, `8fc2920`, `4c351d5`; rollback tag
  `a360-a0-n0-pre-live-20260711`.

### A360-I1 (2026-07-11) — Papu exact-marker recovery, LIVE integration

- Po submit 2xx/unknown bez `panel_zid` most zapisuje pending recovery i nie
  wykonuje ponownego submitu. Nastepne ticki robia read-back tylko po dokladnym,
  stabilnym markerze. Zero markerow lub wieloznacznosc daje jawny hold.
- Zastany przypadek po 3 naturalnych probach ma reason `marker_missing`,
  `inject_attempt_count=0` i `dispatched_count=0`. To dowod at-most-once dla
  recovery, nie pelny kontrakt exactly-once z zewnetrznym Papu.
- Workspace commit/tag: `b2c65b2` /
  `a360-papu-recovery-live-20260711`; rollback wymaga zatrzymania timera i
  przywrocenia backupu stanu 0600, nigdy ponownego wyslania niepewnego case'u.

### P-FLAGREG partia D (2026-07-05) — doc-uzupełnienie flag decyzyjnych/danościeżkowych (C-FLAG-DRIFT ↓12)

Flagi LIVE (wszystkie `flags.json=true`, hot-reload), dotąd nieudokumentowane w ref; shadow/alert/scalar
świadomie ZOSTAJĄ w baseline (ref = doc logiki, nie rejestr — filozofia checkera). Inwentarz pełny:
`eod_drafts/2026-07-05/FLAGREG_inwentarz_i_plan.md`.

| flaga | warstwa (czytelnik) | co robi |
|---|---|---|
| `ENABLE_GEOCODE_NOMINATIM_FALLBACK` | geocoding:518 | fallback geokodera do Nominatim, gdy ścieżka podstawowa nie zwróci współrzędnych |
| `ENABLE_GEOCODE_VERIFICATION_ENFORCE` | geocoding:577 | FAZA 2 weryfikacji geokodu (location_type+dzielnica+cross-source): werdykt „reject" ODRZUCA współrzędne → caller dostaje `no_pickup_geocode` (OFF = tylko log) |
| `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK` | geocoding:`_pin_memory_fallback` | po porażce oficjalnej ścieżki geocode sprawdza pinezkę nauczoną z realnego GPS; OFF = tylko shadow log/audit i nadal `None`, ON = zwrot pinezki, jeśli `n_inliers >= GEOCODE_PIN_MEMORY_MIN_INLIERS` |
| `ENABLE_PICKUP_FROM_GROUND_TRUTH` | panel_watcher:2085 | domyka lukę fantomowego odbioru: gdy ground_truth (geofence) zna odbiór, a orders_state wciąż `assigned` → emit `COURIER_PICKED_UP` z czasem GT (naprawia plan/sla/gate/apkę u źródła) |
| `ENABLE_COORDINATOR_FORCE_TIME_RECHECK` | panel_watcher:2169 | kill-switch przycisku „Odśwież czas" konsoli: drenuje kolejkę `coordinator_time_recheck` i wymusza recheck czasu BEZWARUNKOWO (obie strony, także planned-elastyki) |
| `ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE` | panel_watcher:569 | inwalidacja zapisanego planu kuriera przy zmianie zawartości worka (anty-stale-plan) |
| `ENABLE_WAITING_AT_PERSIST` | panel_watcher:2025 | persystencja wejścia w id_status=4 → `waiting_at` (atrybucja kurier-vs-restauracja w sla_tracker, arrival_source=status4) |
| `ENABLE_UWAGI_ADDRESS_PARSER` | panel_watcher:1247 | parser adresu pickup z pola `uwagi` (firmowe konto Nadajesz, defense-in-depth 6 warstw 07.05) |
| `ENABLE_PENDING_PROPOSALS_WRITE` | shadow_dispatcher:1134 | Opcja B: atomowy zapis zebranych PROPOSE → `pending_proposals.json` (źródło propozycji konsoli koordynatora) |
| `ENABLE_PARCEL_LANE_LIVE` | parcel_lane_merge:114 | gate mergera pasa paczek do obrazu floty/konsoli (OFF = no-op mergera, reszta nietknięta) |
| `ENABLE_ORDERS_STATE_PRUNE` | prune_orders_state:37 | oneshot prune `orders_state` do retencji ~12 h (`ORDERS_STATE_PRUNE_RETENTION_HOURS`; OFF = no-op) |
| `ENABLE_OSRM_TABLE_CELL_CACHE` | osrm_client:466 | kill-switch hot cache'u komórkowego OSRM /table (OFF = legacy pełne wywołania; infra, Front C) |
| `ENABLE_PANEL_DETAIL_PREFETCH` | panel_watcher:1156 | kill-switch prefetchu detali zleceń w panel_watcher (infra, Front C) |
| `ENABLE_WORLD_RECORD` | world_record + shadow_dispatcher.process_event + osrm_client (rekorder) + dispatch_pipeline (2 hooki note_decision_input: k07/loadgov) | K04+v1 refaktoru (ADR-R04): nagrywanie WEJŚĆ decyzji (flagi+flota+order+OSRM+kalibracje) → `dispatch_state/world_record/*.jsonl`, golden corpus do replayu bit-w-bit; telemetria, NIE zmienia decyzji (OFF/brak klucza = delegacja 1:1); retencja 14 d. **v1 (SCHEMA wr1, 2026-07-06): +`live_inputs`** = żywe wejścia nieodtwarzalne offline (K07 prefetch czas_kuriera, loadgov obliczony, treść reliability/plans/eta/bias przycięta do floty) — domyka klasę różnic replayu „dryf żywych plików"; hooki k07/loadgov no-op poza oknem capture (fail-soft, first-note-wins). ADDITIVE: rekord bez `live_inputs` = v0, replay best-effort |
| `ENABLE_FLAG_SNAPSHOT` | common.flags_snapshot_begin/end + shadow_dispatcher.run (pętla ticku) | K05 refaktoru (ADR-R01): flagi ZAMROŻONE na czas ticku silnika (load_flags zwraca snapshot) — spójna decyzja między kandydatami + deterministyczny replay; hot-reload MIĘDZY tickami; inne procesy nietknięte; NIE-decyzyjna (świeżość odczytu, wzorzec ENABLE_PERF_LAZY_MEMBERS); OFF/brak klucza = 1:1 |
| `ENABLE_PRE_RECHECK_BEFORE_POOL` | dispatch_pipeline._k07_prefetch_fresh_ck (przed pulą) + _k07_apply_fresh_ck (pętla) | K07 refaktoru: pre-proposal recheck czas_kuriera RAZ na decyzję PRZED pulą kandydatów (unia worków floty, ta sama get_fresh_czas_kuriera_for_bag — te same skip-reguły i synth-eventy); w ocenie kandydata zero HTTP, czysta aplikacja; OFF/brak klucza = ścieżka legacy 1:1 |
| `ENABLE_EFFECTS_AFTER_DECISION` | effects_buffer + assess_order (begin/finally-flush) + 6 helperów (difficult_case/split_layer/earlybird_t30/feas_carry_blind/r6_breach/c2_shadow) + loadgov save+alert | K08 refaktoru (ADR-R02): efekty uboczne decyzji buforowane i wykonywane PO impl (te same helpery/argumenty — treść linii 1:1, ts eventów feasibility budowany w miejscu zdarzenia); poison-alert V328 świadomie POZA (zwrot steruje cooldownem); OFF/brak klucza = 1:1 |
| `ENABLE_POS_SOURCE_HIERARCHY` | courier_resolver._resolve_position (adnotacja `pos_resolution`) + is_position_known | K16 refaktoru (sesja B, commit `bab1797`): bramkuje WYŁĄCZNIE addytywną adnotację PositionResolution (atrybut dynamiczny, nie pole dataclassy) na CourierState — pos_source/pos/serializacja world_record BEZ zmian przy ON i OFF; hierarchia źródeł (gps→bag→recent→store→no_gps) działa ZAWSZE (przenosiny 1:1, nie za flagą); OFF/brak klucza = zero nowych pól |
| `ENABLE_SCORER_INTERFACE` | core/scorer.py (HeuristicScorer/LgbmScorer/get_scorer) + core/candidates.py (tuż przed budową Candidate) | K13 refaktoru (ADR-R06): interfejs Scorer jako strategia. OFF/brak klucza = ścieżka 1:1 (zero odczytu modułu). ON + `SCORER_IMPL`='heuristic' (default) = TOŻSAMOŚĆ score (bajt-parytet) + metryki obserwacyjne `scorer_impl`/`scorer_fallback` (auto-serializacja A+B). `SCORER_IMPL`='lgbm' = wrapper istniejącej inferencji shadow (predict_two_model_for_decision) z fail-soft fallbackiem do heurystyki (`scorer_fallback=true`); flip LGBM primary = POZA zakresem programu, wyłącznie jawna decyzja Adriana |
| `ENABLE_PLANNER_UNIFIED` | core/planner.py (tier_params+plan_bag) + plan_recheck._gen_one_bag_plan (parametry i `_sweep`→plan_bag z simulate_fn=R) | K15 refaktoru (ADR-R03, kontrakt ①): bliźniak parametryzacji tier→(dwell,tempo) silnik↔re-planer sprowadzony do JEDNEGO źródła. Silnik deleguje tier_params ZAWSZE (przenosiny 1:1, nie za flagą; simulate zostaje lokalnym symbolem feasibility — kontrakt monkeypatch suity). Re-planer: OFF/brak klucza = stary inline bajt-w-bajt; ON = parametry+wywołanie przez core.planner (semantyka TIER_DWELL zachowana, flaga czytana HOT — env-rozjazd drop-inów bez znaczenia dla tej ścieżki) |
| `ENABLE_PLANNER_UNIFIED_SHADOW` | plan_recheck._gen_one_bag_plan (gałąź OFF głównej) | K15 refaktoru: przy głównej OFF liczy parametry OBIEMA drogami (inline i core.planner) i loguje rozjazd `PLANNER_PARAM_MISMATCH` (WARNING, log-only, bez drugiej symulacji — zero wpływu na plan). Dowód parytetu na żywo przed flipem głównej |
| `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE` | operator_route_override.pin_stops + plan_recheck._gen_one_bag_plan / _retime_one_bag_plan (hook PO F6, przed retime) | 2026-07-19 (zadanie ownera): koordynator ustawia KOLEJNOŚĆ podjazdów kuriera (`dispatch_state/operator_route_overrides.json`: pełna permutacja aktywnego worka + set_by/set_at/ttl_min) → kanon `courier_plans.json` = sekwencja operatora (nadrzędna wobec soft-heurystyk kolejności F6: carried-first/relax/no-return/lex-window), ETA legów przeliczane ISTNIEJĄCYM `_retime_stops` (łańcuch OSRM + clamp committed). Walidacja: zbiór id == zbiór aktywnych zleceń kuriera, TTL od set_at, fail-open na brak/uszkodzony plik. `czas_kuriera` NIETYKALNY (R27) — spóźnienie odbioru > 5 min logowane w `committed_breaches`. Telemetria ZAWSZE (też OFF): `dispatch_state/operator_route_override_events.jsonl`, zdarzenia `operator_route_override_{applied\|rejected\|expired}`. Konsola (`fleet_state._build_route` → `route_order.order_podjazdy`) i apka czytają kanon = przezroczyste. OFF/brak klucza = zachowanie dotychczasowe 1:1 |
