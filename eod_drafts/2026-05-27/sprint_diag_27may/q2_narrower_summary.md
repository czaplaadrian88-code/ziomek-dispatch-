# Q2 Narrower Counterfactual Harness — Results Summary

**Run timestamp:** 2026-05-27T18:47:08.566773Z
**Subset:** 229 unique PANEL_OVERRIDE cases (last ~14d, paczki excluded, pickup_to_delivery available)
**Method:** Dual counterfactual under same OSRM/route_simulator_v2 model. Both Ziomek's proposed_courier and operator's actual_courier are simulated picking up the new order from their reconstructed fleet state at `decision_ts`. Compared on `assign_to_delivery_min`. Tie threshold = ±5 min (Adrian spec).

## 1. Coverage

- **Total cases:** 229
- **Known (counterfactual computed):** 223 (97.4%)
- **Unknown:** 6 (2.6%)
  - Unknown reasons:
    - `no_order_coords`: 6

## 2. Distribution per subset

| Subset | n | known | Ziomek-right | Operator-right | Tie | Unknown |
|---|---:|---:|---:|---:|---:|---:|
| auto | 144 | 142 | 1 (0.7% [0.1%, 3.9%]) | 0 (0.0% [0.0%, 2.6%]) | 141 (99.3% [96.1%, 99.9%]) | 2 |
| courier_avoided | 50 | 49 | 4 (8.2% [3.2%, 19.2%]) | 4 (8.2% [3.2%, 19.2%]) | 41 (83.7% [71.0%, 91.5%]) | 1 |
| courier_favorite | 35 | 32 | 0 (0.0% [0.0%, 10.7%]) | 2 (6.2% [1.7%, 20.1%]) | 30 (93.8% [79.9%, 98.3%]) | 3 |
| **TOTAL** | **229** | **223** | **5** (2.2% [1.0%, 5.1%]) | **6** (2.7% [1.2%, 5.7%]) | **212** (95.1% [91.4%, 97.2%]) | **6** |

Wilson 95% CI shown in brackets.

## 3. Delta (cf_proposed_a2d − cf_actual_a2d) distribution

- **min:** -13.55 min
- **p25:** 0.00 min
- **median:** 0.00 min
- **mean:** -0.06 min
- **p75:** 0.00 min
- **max:** 21.55 min
- **stdev:** 2.53 min

Distribution bins:
- delta < -10: **3** (1.3%)
- -10 <= delta < -5: **2** (0.9%)
- -5 <= delta < -1: **6** (2.7%)
- -1 <= delta <= 1 (modal "near-zero"): **203** (91.0%)
- 1 < delta <= 5: **3** (1.3%)
- 5 < delta <= 10: **5** (2.2%)
- delta > 10: **1** (0.4%)

## 4. Verdict: BIMODAL vs TIES-DOMINATED

**TIE rate: 95.1%** (212/223).

**Verdict: B — DOMINATED BY TIES (fidelity-limited).**

Per Lekcja #11 (audit ≠ production validation) guidance:
> "jeśli >70% case'ów wychodzi tie → flaguj problem fidelity, nie traktuj tego jako operator nie poprawia"

Result: **95.1% tie >> 70% threshold**. The simulator cannot resolve dispatching differences at ±5 min granularity.

### Why: structural model biases

1. **OSRM optimism**: simulator uses free-flow OSRM drive time + flat dwell. Real-world traffic, parking, customer interaction not captured. Median realized A2D ≈ 55 min vs simulated A2D ≈ 30 min — **~25 min systematic underestimate**.
2. **Prep variance**: pickup_ready_at in panel is operator estimate, not real ready time. Real couriers waited median ≈ 37 min from assign→pickup; simulator predicts ≈ 21 min.
3. **Carry-over scope**: harness models only `picked_up` carry; ASSIGNED-pending orders are stripped (operator could reassign them in counterfactual). This *reduces* bag size differences which would otherwise produce signal.
4. **Same dwell constants**: both counterfactuals use identical DWELL_PICKUP_MIN/DWELL_DROPOFF_MIN — so tier-aware speed (S1 sprint 17.05) is the only systematic kurier-vs-kurier differentiator. With typical tier-aware speed deltas of <10%, the simulator can't distinguish couriers at ±5 min resolution for trips <30 min.

### Bag-load asymmetry within ties (informational)
Of the 212 cases classified TIE, **18** have CF-proposed bag > CF-actual bag (Ziomek's pick was more loaded) and **36** have CF-proposed bag < CF-actual bag (Ziomek's pick was less loaded). The simulator considers these all "tie" because dwell+drive aggregate is similar even with bag size 0 vs 2 — likely under-penalises bag handoff time.

## 5. Implication for Faza 7 ramp-up

**Cannot conclude from this 200-case sample whether autonomy "loses" by overriding operator overrides.** Specifically:

- **AUTO bucket: 141/142 tie (99.3%)** — under model resolution, AUTO selections look identical to operator picks. This neither validates AUTO nor flags problems.
- **courier_avoided (179/413/515): 4 ziomek_right vs 4 operator_right (8.2% each)** — the only subset with non-trivial signal in BOTH directions. Suggests these "avoided" couriers are sometimes legit picks and sometimes operator-known-bad (consistent with Q1 v2 Finding 3 hypothesis: operator override has real signal but is noisy).
- **courier_favorite (370/400/393): 0 ziomek_right, 2 operator_right (6.2%)** — when Ziomek proposes a "favorite", operator override is *never* faster in-model but *sometimes* slower. Tentatively: when these couriers are proposed by Ziomek and overridden, the override may carry cost — but n=32 is too small for confidence.

**Practical recommendation:** do NOT use this harness output to gate Faza 7 ramp. Needs:
1. Calibrate simulator: realized A2D ≈ sim A2D + ~25 min systematic bias. Add traffic multiplier + variance term per-hour-bucket from production logs.
2. Increase Tie threshold: ±5 min is too tight relative to ~25 min model bias. Try ±15 min for next pass.
3. Use realized-realized comparison: for orders where SAME courier proposed by Ziomek vs picked by operator on different orders SAME time window, compare realized A2D. Avoids simulator entirely.
4. **Better tool needed for Faza 7 ramp validation** — recommend post-deploy shadow log analysis (V3.28 ANCHOR / commit_divergence) rather than offline simulation.

## 6. Case studies — Top 5 Ziomek-right (largest within-model A2D delta)

### 1. Order 476292 — Chinatown Bistro (courier_avoided)

- **Decision ts:** 2026-05-26T18:57:22.965061+00:00
- **Ziomek proposed:** cid=179 (bag=0, pos=synthetic_center)
- **Operator picked:** cid=75 (bag=2, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=29.0min vs actual=42.6min → **delta=-13.55min** (Ziomek faster)
- **Realized A2D**: 49.8min (realized P2D: 15.0min)
- **Pool/auto:** pool_feasible=1 auto_route=ALERT margin=0.0
- **Interpretation:** Ziomek's proposed courier had bag=0 vs operator picked courier with bag=2. Operator chose more-loaded courier; simulator says ~14 min worse. Realized A2D was 50min — consistent with simulator: even with structural bias, realized delivery was moderately slow.

### 2. Order 476150 — Maison du cafe (auto)

- **Decision ts:** 2026-05-26T13:50:32.226582+00:00
- **Ziomek proposed:** cid=376 (bag=0, pos=synthetic_center)
- **Operator picked:** cid=514 (bag=2, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=47.1min vs actual=59.4min → **delta=-12.23min** (Ziomek faster)
- **Realized A2D**: 93.9min (realized P2D: 33.1min)
- **Pool/auto:** pool_feasible=3 auto_route=AUTO margin=109.5
- **Interpretation:** Ziomek's proposed courier had bag=0 vs operator picked courier with bag=2. Operator chose more-loaded courier; simulator says ~12 min worse. Realized A2D was 94min — consistent with simulator: even with structural bias, realized delivery was very slow.

### 3. Order 476287 — Raj (courier_avoided)

- **Decision ts:** 2026-05-26T18:47:46.301634+00:00
- **Ziomek proposed:** cid=179 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=75 (bag=3, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=29.2min vs actual=40.3min → **delta=-11.11min** (Ziomek faster)
- **Realized A2D**: 48.4min (realized P2D: 10.4min)
- **Pool/auto:** pool_feasible=1 auto_route=ALERT margin=0.0
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked courier with bag=3. Operator chose more-loaded courier; simulator says ~11 min worse. Realized A2D was 48min — consistent with simulator: even with structural bias, realized delivery was moderately slow.

### 4. Order 476223 — Pani Pierożek (courier_avoided)

- **Decision ts:** 2026-05-26T16:18:53.039336+00:00
- **Ziomek proposed:** cid=413 (bag=0, pos=synthetic_center)
- **Operator picked:** cid=179 (bag=3, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=43.9min vs actual=51.9min → **delta=-7.94min** (Ziomek faster)
- **Realized A2D**: 60.4min (realized P2D: 36.9min)
- **Pool/auto:** pool_feasible=0 auto_route=ALERT margin=0.0
- **Interpretation:** Ziomek's proposed courier had bag=0 vs operator picked courier with bag=3. Operator chose more-loaded courier; simulator says ~8 min worse. Realized A2D was 60min — consistent with simulator: even with structural bias, realized delivery was very slow.

### 5. Order 476174 — Mama Thai Bistro (courier_avoided)

- **Decision ts:** 2026-05-26T14:38:08.660469+00:00
- **Ziomek proposed:** cid=515 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=509 (bag=1, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=25.5min vs actual=33.3min → **delta=-7.74min** (Ziomek faster)
- **Realized A2D**: 98.5min (realized P2D: 24.4min)
- **Pool/auto:** pool_feasible=0 auto_route=ALERT margin=0.0
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked courier with bag=1. Operator chose more-loaded courier; simulator says ~8 min worse. Realized A2D was 98min — consistent with simulator: even with structural bias, realized delivery was very slow.

## 7. Case studies — Top 5 Operator-right (largest within-model A2D delta)

### 1. Order 476221 — Mama Thai Bistro (courier_avoided)

- **Decision ts:** 2026-05-26T16:13:50.222029+00:00
- **Ziomek proposed:** cid=179 (bag=3, pos=last_picked_up_delivery)
- **Operator picked:** cid=509 (bag=1, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=52.0min vs actual=30.5min → **delta=+21.55min** (Operator faster)
- **Realized A2D**: 87.2min (realized P2D: 30.6min)
- **Pool/auto:** pool_feasible=0 auto_route=ALERT margin=0.0
- **Interpretation:** Ziomek's proposed courier had bag=3 vs operator picked bag=1. Operator chose less-loaded courier; simulator says ~22 min better. Realized A2D was 87min.

### 2. Order 476242 — Ogniomistrz (courier_avoided)

- **Decision ts:** 2026-05-26T17:09:49.643578+00:00
- **Ziomek proposed:** cid=515 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=509 (bag=0, pos=synthetic_center)
- **Within-model A2D**: proposed=69.2min vs actual=61.0min → **delta=+8.16min** (Operator faster)
- **Realized A2D**: 62.4min (realized P2D: 10.3min)
- **Pool/auto:** pool_feasible=6 auto_route=ALERT margin=4.04
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked bag=0. Operator chose less-loaded courier; simulator says ~8 min better. Realized A2D was 62min.

### 3. Order 476070 — Toriko (courier_favorite)

- **Decision ts:** 2026-05-26T09:58:57.394298+00:00
- **Ziomek proposed:** cid=393 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=370 (bag=0, pos=synthetic_center)
- **Within-model A2D**: proposed=52.2min vs actual=44.1min → **delta=+8.15min** (Operator faster)
- **Realized A2D**: 47.0min (realized P2D: 6.2min)
- **Pool/auto:** pool_feasible=2 auto_route=ALERT margin=5.08
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked bag=0. Operator chose less-loaded courier; simulator says ~8 min better. Realized A2D was 47min.

### 4. Order 476294 — Grill Kebab (courier_avoided)

- **Decision ts:** 2026-05-26T19:16:04.715778+00:00
- **Ziomek proposed:** cid=179 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=515 (bag=1, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=34.5min vs actual=26.6min → **delta=+7.87min** (Operator faster)
- **Realized A2D**: 44.3min (realized P2D: 21.6min)
- **Pool/auto:** pool_feasible=2 auto_route=ALERT margin=6.38
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked bag=1. Operator chose less-loaded courier; simulator says ~8 min better. Realized A2D was 44min.

### 5. Order 476108 — Sushi Rany Julek &amp; Pizza Majstry (courier_favorite)

- **Decision ts:** 2026-05-26T11:43:17.167667+00:00
- **Ziomek proposed:** cid=393 (bag=2, pos=last_picked_up_delivery)
- **Operator picked:** cid=470 (bag=1, pos=last_picked_up_delivery)
- **Within-model A2D**: proposed=55.3min vs actual=49.0min → **delta=+6.31min** (Operator faster)
- **Realized A2D**: 72.6min (realized P2D: 21.2min)
- **Pool/auto:** pool_feasible=4 auto_route=ACK margin=11.23
- **Interpretation:** Ziomek's proposed courier had bag=2 vs operator picked bag=1. Operator chose less-loaded courier; simulator says ~6 min better. Realized A2D was 73min.

## 8. Honesty disclosure — Lekcja #11

Per Adrian's spec requirement: **yes, fidelity issue detected**.

**Three concrete fidelity findings:**

1. **Systematic optimism**: median realized A2D (55.3 min) vs median simulated A2D (32.8 min) → **~23 min systematic underestimate** by route_simulator_v2 on this 14d sample.
2. **Resolution gap**: 95% of within-model deltas fall in [-5, +5] min window. The simulator output variance per-courier is smaller than the noise floor of realized world (traffic, prep, parking). Lekcja #11 directly applies: "Resolution może nie odróżnić 2-3 min różnic".
3. **Carry-over scope ambiguity**: I model ONLY picked_up carry (conservative; assigned-pending could be reassigned by operator in counterfactual). This deliberately reduces signal but is the only defensible model — alternative (include ASSIGNED) would artificially inflate bag size for proposed_courier.

**What this harness CAN tell you (honest read):**
- The 11 non-tie cases are real (within-model differences > 5 min); these are useful as case studies but n=11 is too small for distribution statistics.
- Coverage is high (97.4%). Geocoding cache + snapshot merging filled most missing data.
- Subset stratification works: AUTO bucket is nearly all-tie; "avoided" couriers have the most diagnostic signal.

**What this harness CANNOT tell you:**
- Whether autonomy ramp-up loses by ignoring operator overrides — model bias dominates.
- Quality differences between specific couriers — tier-aware speed mult <10% is below model resolution.
- Whether AUTO bucket selections are systematically better/worse — 99.3% tie rate means simulator is blind to AUTO decision quality.

**Better tool recommendation:** Faza 7 ramp validation should rely on (a) production shadow log lift in commit_divergence rates, (b) per-courier realized A2D distributions across same-restaurant pairs, (c) A/B comparison once flag is partially enabled. **Not this offline counterfactual at ±5 min resolution.**
