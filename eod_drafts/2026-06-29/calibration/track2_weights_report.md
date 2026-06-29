# Track 2 — Ziomek scoring-WEIGHT calibration (directional)

Built 2026-06-29. **READ-ONLY analysis. No flips recommended.** Confidence: **DIRECTIONAL ONLY**
(alternatives logged on ~3–4 days = time-confounded; non-chosen alternatives have only
*predicted* features, never realized outcomes; `eta_error_min` mean is corrupted by large
negative outliers so all readings below use **median eta / p90 / r6_breach% / sla_ok%**).

Data: 1,831 shadow decisions with ≥2 candidates → **1,626 joined to realized outcomes**
(`scripts/logs/shadow_decisions.jsonl` + `.1`, joined by `order_id` to the foundation).
In **1,618/1,626 (99.5%)** the engine's `best` == the actually-assigned courier, so the
realized outcome (eta_error / r6_breach / sla_ok) belongs to the **chosen** courier — this is
what makes the outcome-grounding valid. Segments (pool_feasible): **luzno 757, srednio 604,
pf0 265**. `ciasno` (pf==1) is absent — pf==1 almost never co-occurs with ≥2 logged candidates.

---

## 1. Current weight map

Base "D5" score is `S = S_dyst·0.30 + S_obc·0.25 + S_kier·0.25 + S_czas·0.20` (max 100), then a
long list of additive bonus/penalty terms, then a multiplicative Bug-Z cross-quadrant factor.

| Weight / mechanism | Current value | Sign | File:line |
|---|---|---|---|
| **W_DYSTANS** proximity `100·exp(−km/DECAY)` | **0.30** (`DIST_DECAY_KM=5.0`) | + | `scoring.py:22,27` |
| **W_OBCIAZENIE** load `100·(1−bag/5)` | **0.25** | + | `scoring.py:23` |
| **W_KIERUNEK** direction | **0.25** | + | `scoring.py:24` |
| **W_CZAS** oldest-in-bag time | **0.20** | + | `scoring.py:25` |
| **Class / tier** — *no additive base weight* | tier acts only via bag caps | n/a | `BUG4_TIER_CAP_MATRIX` `common.py:1296`, `HARD_TIER_BAG_CAP` `common.py:1313` |
| **R9 stopover** `−stops·PER_STOP` | `STOPOVER_SCORE_PER_STOP=8` | − | `common.py:808`, `dispatch_pipeline.py:4304` |
| **R9 restaurant-wait** | `RESTAURANT_WAIT_PENALTY_PER_MIN=6` | − | `common.py:810` |
| **R8 pickup-span** | `PICKUP_SPAN_SOFT_PENALTY_PER_MIN=3` | − | `common.py:803` |
| **R6 bag-time soft** | `SOFT_PENALTY_PER_MIN=8` + danger `16/min` over 32 | − | `common.py:759,769-770` |
| **BUG4 cap soft** | matrix `0 / −20 / −60 / −120 / −9999` | − | `common.py:1335` |
| **v3273 idle-wait courier** | `−10` at 6min, `−5/min`, hard-reject >20 | − | `common.py`, `scoring.py:141-184` |
| **Load governor** | `LOADGOV_BAG_PENALTY=−40` at `TIGHTEN_AT=2.7` | − | `common.py:2082,2086` (**flag ON**) |
| **Bundle bonus** (l1 same-rest / l2 po-drodze / r4 free-stop) | additive | + | pipeline; `BUNDLE_VALUE_SCORING` **OFF** |
| **v327 cross-quadrant mult** | `0.1` cross / `0.7` adj / `1.0` same | × (pos. only) | `common.py:3274`, `dispatch_pipeline.py:5222` |
| **Post-wave bonus** | `FAST=15 / SLOW=8` | + | `common.py:817-818` |
| **v324a extension** | `0 / −10 / −50 / −100 / −200` | − | `common.extension_penalty` |
| **Post-shift overrun** | progressive | − | `POST_SHIFT_OVERRUN_PENALTY` (flag, shadow-first) |
| **GPS-age / freshness discount** | — | — | `ENABLE_GPS_AGE_DISCOUNT=**False**` → **no live freshness weight** |

Final assembly: `dispatch_pipeline.py:5090-5223`
(`bonus_penalty_terms` dict → `bonus_penalty_sum` → `final_score` → v327 mult → v326 speed adj).
Note: the logged `score` could only be reconstructed from `v327_score_pre_mult`×mult in **27%**
of candidates — there are post-mult adjustments (v326 speed etc.) captured after the pre-mult
snapshot, which is exactly why the proper instrument (§4) must log the full decomposition.

---

## 2. Baseline outcomes per segment (chosen = assigned)

| Segment | n | eta_err median | p90 | r6_breach | sla_ok |
|---|---|---|---|---|---|
| luzno (loose, pf≥5) | 757 | 5.1 | 25.0 | **4%** | 95% |
| srednio (pf 2–4) | 604 | 9.8 | 32.9 | **11%** | 89% |
| pf0 (degenerate) | 265 | 5.9 | 31.7 | 18% | 79% |
| ALL | 1626 | 6.4 | 29.4 | 8% | 90% |

Tighter fleet ⇒ ~3× the R6-breach rate and worse SLA. The question is whether that is a
**weight** problem (engine prefers couriers that then fail) or a **supply** problem (no better
feasible option). The counterfactual (§3c) answers: mostly supply.

---

## 3. Findings

### (a) Which chosen-courier features track REALIZED badness
(WITH vs WITHOUT, robust metrics; full table in `partA` run output)

| Chosen feature | luzno r6 | srednio r6 | ALL sla | Read |
|---|---|---|---|---|
| predicted **r6_max ≥30min** / R6-soft fired | **11% vs 3%** | 13% vs 11% | 84% vs 93% | **near-R6 strongly tracks breach** |
| **BUG4 over-cap** soft fired | 0% (n21) | 8% vs 12% | **80%** vs 91% | over-cap bags = worst SLA bucket |
| **loadgov** penalty fired (tight) | n9 | 11% | 85% vs 90% | tracks badness (but = tight fleet) |
| **v3273 idle-wait** fired | 4% vs 3% | **13% vs 10%** | 91% | mild: idle couriers slightly worse in srednio |
| chosen **stale pos** (≠gps) | **4% vs 0%** | 11% vs 13% | 90% | mild freshness signal in luzno |
| **v327 cross-quadrant ×0.1** | 3% vs 4% | **9% vs 13%** | 88% vs 92% | **NOT worse — often better** |
| chosen **stopover** (bag>0) | 3% vs 5% | 10% vs 13% | 89% | bundling **not worse** |
| chosen **km≥4km** (far) | 3% vs 4% | 11% vs 9% | 91% | far pickups **not worse** |

### (b) Were better alternatives within reach on bad-outcome decisions?
421 bad-outcome decisions had a runner-up. The dimension where a better alternative most often
existed within a flippable score margin (<25pts) is **R6 bag-time headroom**:

| within-margin better alt | ALL | luzno | srednio |
|---|---|---|---|
| runner-up r6_max ≥3min lower | **118** | 62 | 56 |
| runner-up ≥1.5km closer | 40 | 17 | 23 |
| runner-up gps vs chosen stale | 10 | 6 | 4 |

But of those 118 R6-headroom cases only **12** had the chosen actually R6-breach — so the
engine *does* sometimes pick the higher-bag-time courier over a close, lower-bag-time one, yet
that choice rarely caused the breach itself (the badness was usually lateness/SLA, not R6).

### (c) Counterfactual flips (additive re-rank; approximate where v327 mult applied)
Median score margin chosen−runner-up = **9.7 pts; 58% of decisions <20 pts** ⇒ choices are
close and weights genuinely move picks.

| Perturbation | flips / 1626 | of flips: current pick was realized-BAD (R6-breach) | segment skew |
|---|---|---|---|
| STOPOVER ×0.5 / ×2 | 9 / 12 | 3 / 3 (0–1) | even | low leverage |
| **R6-soft ×2** | **45** | 9 (1) | srednio 24 / luzno 21 |
| BUG4-cap ×2 | 6 | 1 (0) | even | **forced picks** |
| **v3273 idle-wait ×2** | **118** | **38** (2) | **luzno 96** / srednio 22 |
| LOADGOV turn-up (+1×δ) | 10 | 2 (0) | even | **forced picks** |
| v327-mult relax 0.1→0.5 | 5 | 1 (0) | luzno | inert (sign-guard) |

---

## 4. Flagged weights (directional strength)

**① v327 cross-quadrant multiplier (×0.1) — looks MIS-SET TOO HARSH.** It crushes a candidate's
score by 90%, yet chosen cross-quadrant bundles deliver **no worse — slightly better in srednio
(r6 9% vs 13%, sla 89% vs 88%)**. The ×0.1 is largely inert in practice because the sign-guard
skips it on the (very common) negative scores, so relaxing it flips only 5 decisions — but where
it *does* bite (positive-score bundles) it is over-penalizing geometry that reality says is fine.
*Strength: moderate signal, low live blast-radius. Segment: srednio most.*

**② v3273 idle-wait penalty — highest-leverage knob; directionally slightly TOO WEAK in
srednio.** Idle-penalized picks breach more in srednio (13% vs 10%) and doubling the penalty
re-routes **118** picks (96 in luzno), 38 of whose current picks were realized-bad. This is the
single weight with real leverage on the selection. *Strength: leverage HIGH, outcome-edge SMALL →
net confidence LOW-MED. Segment: luzno (leverage) + srednio (worse outcomes).*

**③ R6 bag-time headroom — under-weighted relative to other terms, but breaches are mostly
FORCED.** near-R6 picks breach 2–4× baseline and in 118 bad decisions a 3-min-better alternative
sat within a flippable margin; yet doubling R6-soft flips only 45 and fixes ~1 breach → the
remaining breaches have **no better feasible option** (a supply constraint, not a weight bug).
*Strength: clear correlation, weak fixability. Segment: srednio/pf0.*

**Not mis-set:** STOPOVER (8/stop), proximity W_DYSTANS (0.30) — far/bundled picks are not worse,
so neither over- nor under-weighting is supported. **LOADGOV (−40)** and **BUG4-cap** correlate
with badness only because they correlate with tight fleet; raising them flips ~6–10 forced picks
and would not help. **Freshness**: a small luzno edge (gps 0% vs stale 4% breach) is the only
support for adding a freshness preference under loose fleet — currently there is **no live
freshness weight** (`GPS_AGE_DISCOUNT` OFF).

**Per-segment summary (vs the prompt's hypothesis):** under tight fleet the badness driver is R6
headroom + over-cap + load — but those picks are **forced** (low flip leverage) → it's a supply
problem, so "proximity/feasibility should dominate more under tight fleet" is *not* actionable
from this data. Under loose fleet there is real flip leverage (idle-wait, freshness), consistent
with "secondary factors can weigh more when the fleet is loose."

**Confidence: LOW / directional.** Do **not** flip any weight on this evidence. The deployable
numbers come from the prediction track; this track's deliverable is the direction above + the
instrument below.

---

## 4b. REFINEMENT — SOLO vs BUNDLE split + engine drift (supersedes parts of §3–4)

Engine-owner refinements applied. Two facts reframe the read:
- **Window is 06-18..06-28 (11 days), not 3-4** (I used `shadow_decisions.jsonl` + `.1`). The
  **scoring-weight constants** (stopover 8, R6-soft 8, v3273, loadgov −40, v327 mult) have **no
  git commits in the window** → weight/breach correlations are weight-stable throughout. Drift is
  in *positioning/ETA* (checkpoint-tz fix **06-26**, geocode negative-cache) + flag-gated shadow
  code → freshness/pos/eta readings lean **post-06-26**.
- **SOLO = 343 / 1626 (21%)**; BUNDLE = 1278 (79%). Per the owner, bundle realized per-order
  time reflects the courier's *actual driven* sequence (may differ from Ziomek's plan) → bundle
  outcome = partly route-divergence, **directional-only**. SOLO = clean verdict on the pick.

Baseline split (etaMed / r6_breach / sla):
| cell | SOLO | BUNDLE |
|---|---|---|
| luzno | 7.3 / **4%** / 94% | 4.1 / 4% / 95% |
| srednio | 16.8 / **21%** / 83% | 7.1 / 8% / 90% |
| ALL | 12.4 / 11% / 89% | 5.4 / 8% / 90% |

**Corrections to §3–4:**

- **① v327 cross-quad ×0.1 — RETRACT "too harsh".** Split flips it: among **bundles**, ×0.1 picks
  deliver modestly **worse** (r6 9% vs 6%, sla 87% vs 94%) → the penalty's **direction is
  justified**. (The earlier "not worse / better" was the un-split srednio artifact.) SOLO ×0.1 looks
  better but n=11. Magnitude not assessable; still largely inert via the sign-guard. **No longer a
  flagged mis-set weight.**

- **② idle-wait (v3273) — strengthened on CLEAN solo; this is the top directional signal.** On
  SOLO: idle-penalized picks deliver worse — etaMed **13.3 vs 7.4**, r6 **12% vs 8%**, sla **88% vs
  92%** (n=265 vs 78). (Bundle data had muddied/reversed it.) Combined with high flip-leverage (118
  flips, 96 in luzno) → directionally **too weak**, esp. luzno/srednio. *Confidence LOW-MED (no
  realized outcome for the alternate courier).*

- **③ near-R6 (r6_max≥30) still predicts breach** in both (SOLO 22% vs 11% n9; BUNDLE 14% vs 4%),
  but on clean SOLO only **19** bad decisions had a 3-min-better alt within margin and **0** of them
  breached → breaches are **forced** (supply), not a reweight fix. Unchanged.

- **Freshness — now INCONCLUSIVE (was a mild suggestion).** The 06-26 checkpoint-tz fix moved SOLO
  stale-pos breach **15%→5%**; gps n is tiny (5 pre / 14 post). Too confounded to claim a freshness
  weight is missing. **Drop the suggestion.**

- **New flag: SOLO×srednio is the worst cell** (r6 **21%**, sla 83%) — un-bundlable leftover orders
  under medium fleet. Not a single-weight fix; a candidate for a dedicated look (these are the hard
  far/awkward solos that couldn't bundle).

- stopover / proximity / loadgov / bug4: unchanged (low-leverage or forced). The one mild clean
  geometry signal left: 34 bad SOLO decisions had a ≥1.5km-closer runner-up (3 breached).

Net after refinement: the **only weight with a clean, leverage-backed directional case is v3273
idle-wait (too weak, loose/medium fleet)**; everything else is either forced (supply), inert, or
inconclusive. Confidence remains **LOW / directional — no live flip**. Script: `track2_solobundle.py`.

## 5. Live weight-shadow instrument — spec (implement next)

**Why needed:** today's `shadow_decisions.jsonl` logs only the final `score`; the pre/post-mult
decomposition reconstructs in just 27% of candidates, so we cannot exactly re-score under an
alternate weight vector. The instrument fixes that by logging the **full additive+multiplicative
decomposition with a self-check invariant**, so any weight vector W′ can be re-scored *exactly*
offline.

**Attach point (observability-only):** a pure serializer `weight_shadow.serialize(decision)`
called from `shadow_dispatcher._serialize_result` right after `final_score` is computed for every
candidate — reads already-computed locals, never branches the decision, never added to
`final_score`. Gate `ENABLE_WEIGHT_SHADOW_LOG` (default ON, cheap). Atomic append (temp→fsync→
rename), mirroring existing `*_shadow` tools.

**Where:** `dispatch_state/weight_shadow.jsonl` (decision-time) + a periodic joiner writing
`dispatch_state/weight_shadow_outcomes.jsonl` (outcome-attached).

**Per-decision record:**
- key: `order_id, decision_ts_utc, event_id, restaurant`
- context: `pool_feasible_count, loadgov_load_ewma, load_now, active_couriers/orders, segment,
  bag_size, czasowka, peak_window, chosen_courier_id`
- `candidates[]` (every feasible incl. chosen):
  `courier_id, feasibility, best_effort, pos_source, pos_age_min`
  - `base{s_dyst, s_obc, s_kier, s_czas, w_dyst, w_obc, w_kier, w_czas, base_total}`
  - `weights{r9_stopover, r9_wait, r8_span, r6_soft, r6_danger, bug4_cap, v3273_wait, r5_detour,
    r1_corridor, bundle_l1, bundle_l2, bundle_r4, timing_gap, wave, v324a_ext, post_shift_overrun,
    loadgov_value+loadgov_applied(bool), gps_age_discount}`
  - `v327{pre_mult, mult, sign_guarded}, v326_speed_adj, post_mult_other`
  - `final_score`
  - `predicted{km_to_pickup, travel_min, travel_min_cal, drive_min, r6_max_bag_time_min,
    r6_bag_size, eta_pickup_utc, predicted_delivered_at_utc}`
- **INVARIANT (the trust anchor):** also write
  `recon_final = base_total + Σweights + v327/post adjustments`, `recon_ok = |recon_final −
  final_score| < 0.01`, `recon_resid`. A non-OK row signals an undocumented term (the bug that
  gives today's 27% mismatch) — alert, don't silently log a lying instrument.

**Outcome join (later):** periodic joiner attaches per `order_id` from
`eta_calibration_log / decision_outcomes / gps_delivery_truth / sla_log`:
`actual_delivered_at_utc, eta_error_min, r6_actual_min, r6_breach, sla_ok, picked_up_at,
delivered_source` → `weight_shadow_outcomes.jsonl`.

Each record must also carry `is_bundle` / `bag_size` and (for bundles) `planned_sequence` so the
joiner can flag plan-vs-actual route divergence; the **clean confirmation metric is computed on
SOLO decisions**, with bundle decisions reported separately as directional-only.

**Counterfactual metric (offline confirmation):** for a candidate vector W′, recompute every
candidate's `final` *exactly* (now possible) and take `argmax = chosen'(W′)`. Report, per segment:
(i) `flip_rate` = fraction where `chosen'(W′) ≠ chosen`; (ii) on flips where `chosen` had a
realized BAD outcome (r6_breach / eta_error>τ / !sla) **and** `chosen'(W′)` was predicted-better
(lower r6_max / closer / fresher) = **"avoided-bad"**; (iii) inverse guard = flips where `chosen`
was GOOD but `chosen'(W′)` predicted-worse = **"introduced-risk"**; headline = `avoided_bad −
introduced_risk` per segment. **Caveat to bake into the report:** (ii)/(iii) still use *predicted*
features for the alternate courier — full proof needs either long accumulation (so alternate picks
recur as real assignments with realized outcomes) or a guarded online A/B that actually applies W′
to a small % of live decisions behind a flag and logs both arms. The instrument enables the former
and is the on-ramp to the latter.
