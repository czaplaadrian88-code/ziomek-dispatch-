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
> | `ENABLE_ALWAYS_PROPOSE_ON_SATURATION` | — | **🟢 LIVE** | every quality→KOORD gate carries `and not _always_propose_on()` |
> | `ENABLE_CZASOWKA_CK_PASSIVE_GUARD` | — | **🟢 LIVE** (24.06, #483023) | czasówka: passive gastro `czas_kuriera` re-stamp (`panel_re_check`/`pre_proposal_recheck`) NIE zmienia committed; umówiony czas = `pickup_at` |
> | `ENABLE_PICKUP_TIME_MIRRORS_CK` | — | **🟢 LIVE** (24.06) | czasówka: `PICKUP_TIME_UPDATED` mirrors `pickup_at`→`czas_kuriera` (koordynator/restauracja, any direction) |
> | `ENABLE_ELASTYK_CK_NO_BACKWARD` | — | **🟢 LIVE** (24.06, opcja B) | elastyk: passive `czas_kuriera` change blocked tylko BACKWARD (forward = +15/lateness zostaje) |
> | `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` | — | **🟢 LIVE** (26.06 canary) | `picked_up_at`/`delivered_at` (Warsaw-naive) parsed as Warsaw not UTC w 4 miejscach `courier_resolver` (interp/recent-activity/ZOMBIE-guard/per-status) → ożywia predykcję pozycji no-GPS (interp 0/16984→żyje); OFF=legacy UTC |
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
- ⚪ **OFF:** `AUTO_PROXIMITY_ENABLED`, `ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE` (cold-food divergence
  no longer →KOORD), `ENABLE_BAG_TIME_FAIRNESS_SCORING`,
  `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT`, `ENABLE_CARRY_CHAIN_PENALTY`, `kill_switch_to_v1`,
  `ENABLE_DRIVE_MIN_CALIBRATION_V2` (main), `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (ETAP4; forward-shadow
  od 2026-06-24 20:52 — metryka logowana, flip czeka replay 25.06 + ACK; demote kuriera kończącego po
  zmianie w selekcji best_effort + LEXR6).
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
  and `PANEL_FLAG_TRUST_CANON_ORDER` (nadajesz-panel) — both 🟢 LIVE. These re-sequence an **already
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
  ON → calibrated. Rollback = flag OFF (hot-reload, no restart). Magnitude = aggressive step 1; deepen
  later from composition-clean GPS-motion data. `slow`/`new` stay 1.0 (no clean data).

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
- **Shadow-only pattern:** a new mechanic writes a `*_shadow_delta` field + a serialized metric, runs
  7–14 days, is replayed/forward-validated by tools in `tools/` and `eod_drafts/`, and only then is
  its flag flipped 🟢. The **encoding checklist** for any new rule (or it's an invisible bug): code +
  test + shadow serializer (A+B) + learning_analyzer reader + dashboard.

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
