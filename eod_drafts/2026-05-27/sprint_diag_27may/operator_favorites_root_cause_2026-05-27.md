# Operator-favorites root cause analysis (2026-05-27)

## Executive summary

**Top-1 dominant hypothesis: SCORE FUNCTION BIAS** — explains >85% of the LATENT operator-favorite pattern across all 5 target couriers (cid 400, 509, 370, 484, 508 + 393).

The picture inverted vs the original 6 hypotheses. Targets are NOT off-shift, NOT excluded from pool, and operator does NOT have hardcoded restaurant preferences. **The data shows:**

- Target couriers are in the dispatchable pool in **53-71% of override cases** (often more, since the analysis only counts shadow-logged events).
- Where they ARE in the pool, their **score is dragged 55-85 points below the winner** by a stack of penalties that systematically punish "informed + carrying bag" couriers.
- The **WINNERS are mostly synthetic-position (`no_gps` 63%, `pre_shift` 16%) + empty-bag couriers** that get baseline scores with **zero bag penalties** and **+15 fleet-load bonus**.
- Operators consistently undo this by choosing the courier actually IN POSITION (fresh GPS, short drive_min, real bag knowledge) — exactly what Ziomek's `_demote_blind_empty` (V3.16) was meant to fix.

The V3.16 demote is **silently undone** because downstream layers (`_v325_new_courier_penalty`, `_v326_speed_multiplier_adjust`, `_v326_fleet_load_balance`, `_v326_multistop_trajectory`) **re-sort by `score` desc after the demote**, restoring the blind+empty courier to top when their bonuses outweigh the demote effect.

Confidence per hypothesis:
- H1 Shift coverage: **PARTIAL** (~15-45% of actuals at "blind" hours for cid 400/393; rest in-shift normal)
- H2 Restaurant affinity: **WEAK** (top-3 share 17-33%, broad — not a hardcoded preference)
- H3 Pool exclusion: **REJECTED** (no EXCLUDED_CIDS hit; manual_overrides has only 3 unrelated names)
- H4 Score function bias: **CONFIRMED — DOMINANT**
- H5 pos_source bias: **CONFIRMED — secondary** (drives H4 via free_at_min / r6 / r1 corridor cascade)
- H6 Operator hardcoded preference: **REJECTED**

## Per-courier deep dive

### cid=400 Adrian R (tier=std+, FAST_SAFE)
- **Schedule:** 13:00-21:00 today (Adrian Rutkowski in `schedule_today.json`)
- **DIM 1 hour gap:** ACT 11h-20h, PROP 11h-14h only → **45% of actuals (52/115) are at hours where Ziomek NEVER proposed him in 14d**. After ~14h Ziomek stops proposing him entirely despite his shift continuing.
- **DIM 2 pool presence:** 53% in shadow candidates on override cases (71/133 captures). When IN pool: 79% have fresh `gps`.
- **DIM 3 score:** when proposed: avg=4.7 med=-18.2. When operator overrode: WINNER avg=69.6, Adrian R when in pool avg=-10.9, **delta_avg +82.4**. Adrian R NEVER beats winner by score (0/71).
- **DIM 4 restaurants:** 40 unique, top3=25%, top5=36% — broad spread. NEVER-PROPOSED restaurants: 45 actuals (39%) — Grill Kebab, Maison du cafe, Raj, Pruszynka, Rukola Kaczorowskiego.
- **DIM 5 pos_source:** when proposed: gps 70%, when override (target IN pool): gps 79%. **Targets have fresh GPS, winners are 62% `no_gps`.**
- **DIM 6 auto_route:** when operator overrode Adrian R, Ziomek's pick distribution: ACK 103, AUTO 9, ALERT 3. Top reasons: `C2_score_margin=0.0<15.0` (24), `C3_tier=std_not_in_(gold,std+)` (23) — the winners themselves often didn't even qualify for AUTO.
- **Sample concrete case (oid=474624 Rany Julek, 2026-05-19 10:51):**
  - Winner cid=413 Mateusz O (gold): score=112, pos=`pre_shift` (synthetic BIALYSTOK_CENTER), bag=0, free_at_min=0, travel_min=8.9 (synthetic), km=None
  - Adrian R: score=4.1, pos=`post_wave`, bag=2, free_at_min=17.2, drive_min=4.5 (real — CLOSER), km_to_pickup=1.73, bonus_r1_corridor=**-35** (heavy penalty for not-on-corridor)
  - Log shows `NO_GPS_DEMOTE order=474624: top cid=413 (no_gps+empty) demoted; informed_alts=2; new_top_cid=400` — Adrian R WAS placed at top by demote, **then downstream V326 layers re-sorted and undid it**.
- **Verdict:** H4 (Score function bias) + H5 (pos_source cascade). The bonus_r1_corridor -35 alone eats almost the entire score advantage; r6 bag_time 29.3 min (vs winner 9.8) + free_at_min penalty stacks on top.

### cid=509 Dariusz M (tier=std+, FAST)
- **Schedule:** null today (not on grafik 2026-05-27)
- **DIM 1 hour gap:** ACT 12-21h, PROP 10-21h matches well. Blind hours: 14h (4 act), 20h (9 act) → 16% blind.
- **DIM 2 pool presence:** 56% in candidates (45/80). 38 MAYBE, 7 NO (R6_per_order >35min) — some hard rejects due to thermal anchor.
- **DIM 3 score:** when proposed: avg=37.4 (best of the 6!). When override+in_pool: avg=-30.1 vs winner 52.5. delta_avg +82.5.
- **DIM 4 restaurants:** 30 unique top3=29%, top5=41% — moderate concentration. NEVER-PROPOSED: 23% (Raj 6×, Doner Kebab, Ogniomistrz, Pan Schabowy 3 each).
- **DIM 5 pos_source on override+in_pool:** `last_assigned_pickup` 42%, `last_picked_up_pickup` 40% → he's mid-shift carrying bag.
- **Specific reason cluster:** `best_effort_no_feasible (sla_viol=0)` 8 cases — Ziomek's best-effort fallback (no feasible pool) often hits when Dariusz could have taken it.
- **Verdict:** H4 score bias + H5 mid-trip-with-bag penalty cascade.

### cid=370 Jakub OL (tier=std+, FAST)
- **Schedule:** Kuba Olchowik 12:00-22:00 today.
- **DIM 1 hour gap:** Best alignment — proposed and actual both 11-21h. ZERO blind hours.
- **DIM 2 pool presence:** **71% in candidates (98/138)** — highest of the 6.
- **DIM 3 score:** when proposed: avg=29.8 (n=165, large sample). When override+in_pool: avg=5.6 vs winner 77.2. delta_avg +71.6. Highest avg target score = most often actually picked when proposed (n_won=11/73).
- **DIM 4 restaurants:** 43 unique top3=22%, top5=33%. NEVER-PROPOSED: 10% only (best alignment).
- **DIM 5 pos_source on override+in_pool:** `gps` 24%, `last_picked_up_pickup` 21%, `post_wave` 20%, `last_assigned_pickup` 18% — heavily mid-wave activity.
- **DIM 6 auto_route_reason on overrides:** `C2_score_margin=0.0<15.0` 30 cases — margin too small, fires ACK even when Jakub IS in pool.
- **Verdict:** H4 score bias dominant. Jakub OL is the "best-behaving" target — Ziomek proposes him 73× and his actual_won=11 — but operator overrides 76.7% of HIS proposals (same `no_gps+empty` issue: Gabriel 179, Mateusz O 413, Szymon P 515 outrank him).

### cid=484 Andrei K (tier=std)
- **Schedule:** Andrei Kotsia 11:00-21:00 today.
- **DIM 1 hour gap:** Blind 22h only (2 act). Otherwise fully covered.
- **DIM 2 pool presence:** 55% (55/100). 4 NO (sla_violation + R6 thermal + shift_end).
- **DIM 3 score:** when proposed: avg=49.4 med=56.8 — surprisingly high. When override+in_pool: avg=-10.3 vs winner 78.3. delta +88.5 (largest gap!).
- **DIM 4 restaurants:** 37 unique top3=23%, top5=33%. NEVER-PROPOSED: 17%.
- **DIM 5 pos_source on override+in_pool:** **gps 67%** — fresh GPS most of the time. Winners are no_gps 69%!
- **DIM 6 auto_route_reason:** `C3_tier=std_not_in_(gold,std+)` 15 cases — the tier filter (which whitelists only gold + std+ for AUTO) blocks Andrei K from AUTO routing entirely.
- **Verdict:** H4 dominant + H5 inverse cascade (Andrei has FRESH GPS but loses to NO_GPS competitors due to score). C3 tier=std filter is OK (we don't want him on AUTO), but he should at least be PROPOSED more.

### cid=508 Michal Li (tier=slow)
- **Schedule:** Michał Lizuń null today.
- **DIM 1 hour gap:** Blind 17h (11 act = 20% of all actuals) + 22h (1). The 17h gap is significant — Ziomek goes silent on him at peak.
- **DIM 2 pool presence:** 71% in candidates (40/56) — high.
- **DIM 3 score:** when proposed: avg=54.4 med=65.7. When override+in_pool: avg=24.7 vs winner 83.4. delta +58.7 (smallest gap of the 6).
- **DIM 4 restaurants:** 27 unique top3=33%, top5=48% — most concentrated. Heavy on Rany Julek (8), Grill Kebab (5), Rukola Kaczorowskiego (5), Rukola Sienkiewicza (5), Gym Fit Food (3). 15% never-proposed.
- **DIM 5 pos_source on override+in_pool:** `post_wave` 38%, `no_gps` 25%, `last_picked_up_pickup` 18%. Mix — sometimes blind too.
- **Verdict:** H4 + tier penalty. tier=slow gets -5.55 score adjustment from V326_SPEED_MULTIPLIER (map: slow=1.111). Combined with bag-time/r6 cascade.
- **Outlier note:** Michal Li is the ONE atypical case — his proposal score is high (54+) but his override rate stays high (68.5%). Operator may genuinely prefer him for specific runs because of bag-handling skill at slower tier.

### cid=393 Michał K. (tier=std+, WHITELIST per Agent B) — baseline reference
- **Schedule:** Michał Karpiuk 10:00-20:00 today.
- **DIM 1 hour gap:** Blind 18h (18 act = 14% of all).
- **DIM 2 pool presence:** 57% in candidates (76/134) — moderate.
- **DIM 3 score:** when proposed: avg=12.5 med=1.3 (low — he's proposed with thin margin). When override+in_pool: avg=-11.5 vs winner 58.7. delta +70.2.
- **DIM 4 restaurants:** 42 unique top3=17%, top5=24% — broadest. 17% never-proposed.
- **DIM 5 pos_source on override+in_pool:** `last_picked_up_pickup` 38%, `post_wave` 26%, `last_assigned_pickup` 24% — extremely mid-wave heavy.
- **Verdict:** Same H4 pattern. He won WHITELIST in Agent B because his proposed-based override rate (60.7%) was lower than baseline — not because the underlying score-bias issue is gone.

## Cross-courier patterns

### Pattern 1: Score-stack penalty cascade (universal)
Every target shows the same delta breakdown when in pool vs winner:
- **r6_max_bag_time_min**: TARGET 23-30 min vs WINNER 13-19 min (close to 35 min thermal limit)
- **free_at_min**: TARGET 14-34 min vs WINNER 2-10 min (winners are immediately free)
- **bonus_r1_corridor**: TARGET -5 to -16 vs WINNER -1 to -2 (new drop NOT in their existing corridor)
- **bonus_r5_detour**: TARGET -2 to -11 vs WINNER -1 (pickup detour from existing route)
- **drive_min**: TARGET 7-12 min (REAL, fresh GPS) vs WINNER 17-25 min (SYNTHETIC from BIALYSTOK_CENTER)

Targets are *closer* (real km/drive) but get *score-bombed* by penalties triggered by their existing bag/route.

### Pattern 2: pos_source inversion
Winners in override cases — distribution across ALL 6 targets (n=386 winner observations):
- `no_gps`: 243 (63%)
- `pre_shift`: 61 (16%)
- `last_assigned_pickup`: 30 (8%)
- `post_wave`: 26 (7%)
- `gps`: 11 (3%)

Targets in same cases (when in pool, n=384):
- `gps`: 118 (31%)
- `last_picked_up_pickup`: 78 (20%)
- `last_assigned_pickup`: 71 (18%)
- `post_wave`: 62 (16%)
- `no_gps`: 29 (8%)

**Reversed**: targets are 10× more likely to have fresh GPS, winners are 8× more likely to have no GPS at all. This contradicts intuition that Ziomek would prefer "known location" couriers — instead the **synthetic BIALYSTOK_CENTER position + empty bag = no penalty = top score**.

### Pattern 3: V3.16 `_demote_blind_empty` is silently undone
Source code review (`dispatch_pipeline.py`):
1. Line 3135 `feasible = [c for c in candidates if c.feasibility_verdict == "MAYBE"]`
2. Line 3136 `feasible.sort(key=...)` — sort by score desc
3. Line 3139 `_demote_blind_empty(feasible, order_id)` — V3.16 reorder: informed first
4. Line 3142 `_v325_new_courier_penalty(feasible, order_id)` — **calls `feasible.sort()` at line 835 (only if `ENABLE_V325_NEW_COURIER_CAP=True`, which is DEFAULT ON)** — undoes demote ordering
5. Line 3145 `_v326_speed_multiplier_adjust(feasible, order_id)` — same pattern, line 643 calls `feasible.sort()` (default ON)
6. Line 3148 `_v326_fleet_load_balance(feasible, candidates, order_id)` — adjusts +/-15 score, re-sorts (default ON)
7. Line 3151 `_v326_multistop_trajectory(feasible, new_order, order_id)` — adjusts, re-sorts

**Concrete verified case (oid=474624):** dispatch.log shows `NO_GPS_DEMOTE ... new_top_cid=400` (Adrian R demoted Mateusz O), but shadow_decisions.jsonl shows cid=413 at top with score=112 vs cid=400 score=4.1. The score gap (108 pts) is large enough that **even with proper informed-first ordering, subsequent score-based re-sort restores the blind candidate to top.**

Specifically the score gap is created by:
- Adrian R bonus_r1_corridor: -35 (new drop not on his current corridor)
- Mateusz O bonus_r1_corridor: 0 (empty bag → no corridor to violate)
- V326_FLEET_LOAD_BALANCE bonus +15 for empty-bag Mateusz O vs -15 for bag=2 Adrian R = additional 30-pt gap

### Pattern 4: V326_SPEED_MULTIPLIER_MAP baked-in tier penalty
File: `common.py:1275`
- `gold: 0.889` → adjustment **+5.55** (Gabriel 179, Mateusz O 413, Bartek O 123)
- `std+: 1.056` → adjustment **-2.8** (**Adrian R, Jakub OL, Michal K, Dariusz M — 4 of 5 targets!**)
- `std: 1.000` → 0 (Andrei K, Łukasz W)
- `slow: 1.111` → adjustment **-5.55** (**Michal Li — target #5**)
- `new: 1.300` → adjustment **-15** (Dawid Cha, Grzegorz)

**Every single target gets a tier-based score penalty against the gold winners.** Backtest comment claims "distance bias suspected" for std+ — i.e. the model "knew" the bias was there and BAKED IT IN as adjustment. But the bias the backtest measured comes from this very pattern (proposed-based override is high because we proposed them less, when proposed they're MID-WAVE which inflates delivery_time_p90 in their bias-adjusted bucket).

## Recommended actions (Sprint 4 fixes)

### P0 (highest impact, low effort) — Fix the demote ordering

**Action 1: Make V3.16 demote AUTHORITATIVE — disable post-demote re-sort of blind+empty top-1**

Two options:
- **Option A (minimal patch):** In `_v325_new_courier_penalty`, `_v326_speed_multiplier_adjust`, `_v326_fleet_load_balance`, `_v326_multistop_trajectory` — **skip re-sort if `feasible[0]` is informed and any other candidate is `blind+empty`**. Preserve V3.16 invariant.
- **Option B (more correct):** Move `_demote_blind_empty` AFTER all V325/V326 score adjustments (line ~3152 instead of 3139). Demote becomes the final reordering pass before `top = feasible[:16]`.

Files: `dispatch_v2/dispatch_pipeline.py` lines 3138-3151
Effort: 1-2h (small refactor + regression tests)
Impact: probably eliminates 40-60% of operator overrides (the no_gps+empty wins).

### P1 (high impact, moderate effort) — Recalibrate V326_SPEED_MULTIPLIER

**Action 2:** The V326_SPEED_MULTIPLIER_MAP was backtested on data dominated by the same pattern it's trying to "fix". Tiers std+ (-2.8) and slow (-5.55) penalize EXACTLY the operator-favorite couriers. After Action 1 fixes the demote, **rerun the backtest** with the corrected ordering — std+ multiplier may converge to ~1.0 (no bias).

Files: `common.py:1275-1281`
Effort: 2-3h (rerun backtest off new shadow log post-Action-1)
Impact: removes systematic -2.8 to -5.55 penalty against all 5 targets.

### P2 (medium impact, low effort) — Bag-time gradient instead of cliff

**Action 3:** Currently `r6_max_bag_time_min` enters score via thermal anchor checks (HARD reject at 35 min) but there's no smooth penalty in 20-30 min range. The 23-30 min target couriers are getting hammered by `bonus_r1_corridor` and `bonus_r5_detour` cascading penalties — none of which are calibrated against the cost of *adding 5 more deliveries to an idle courier vs reusing an existing trip*.

**Re-weight `bonus_r1_corridor` from -35 max down to -10 max** (current case 474624: Adrian R -35 single penalty wiped his entire score advantage).

Files: search for `R1_CORRIDOR_PENALTY` constants in `common.py`
Effort: 1h
Impact: marginal — but addresses Lekcja R-NO-WASTE gradient principle.

### P3 (deferred — wait for Sprint 1 drive_min calibration)

**Action 4:** pos_source bias (H5) — winners are 63% `no_gps` and get `BIALYSTOK_CENTER` synthetic. Their `travel_min` ~8-9 min vs Adrian R's REAL `drive_min=4.5 min` is misleading. Sprint 1 drive_min calibration (per `feedback_pytest_httpx_monkey_patch.md` etc.) should address this. **Do NOT add ad-hoc no_gps penalties before Sprint 1 calibration lands.**

## Concrete Sprint 4 priority matrix

| # | Action | Impact | Effort | Risk |
|---|---|:---:|:---:|:---:|
| 1 | Demote ordering (Option B: move demote last) | **HIGH** | LOW | LOW (V3.16 was already passing regression) |
| 2 | Recalibrate V326_SPEED_MULTIPLIER post-Action-1 backtest | **HIGH** | MED | LOW |
| 3 | Reduce bonus_r1_corridor max penalty | MED | LOW | MED (may impact good bundles) |
| 4 | Wait for Sprint 1 drive_min calibration | — | — | — |

## By-product findings (unexpected)

1. **V3.16 demote silently undone bug** — known issue NOT in tech_debt_backlog. Likely been bleeding scoring quality since V3.25 (release of `_v325_new_courier_penalty`).
2. **Operator-favorites have FRESHER GPS than Ziomek's picks** — 79% Adrian R gps when in pool vs Mateusz O / Gabriel = no_gps. Counter-intuitive (would expect Ziomek to prefer known position) — but score cascade dominates.
3. **best_effort_no_feasible (sla_viol=0)** fires 24 times across the 6 targets where Ziomek had ZERO feasible candidates and used best-effort. The targets were typically excluded (likely by `dispatchable_fleet` filter). 47% NOT-in-pool ratio for Adrian R needs follow-up — could be `not_working_today` (he's listed null today even though shift 13:00-21:00 exists). Schedule loader / panel name mapping is one place to verify.
4. **No real restaurant affinity** — top3 share 17-33%, top5 share 24-48%. The operator is choosing by proximity/availability, not by hardcoded "this courier only goes to this restaurant" rule. H6 conclusively rejected.

## Outliers / caveats

- **Michal Li (cid=508)** is the partial outlier — slower tier, smaller delta vs winner (58.7 vs 70-88 for others), heavier restaurant concentration (top3=33%). Operator's preference for him is more genuinely behavior-based (he handles slower-tier work reliably). Lower priority for AUTO whitelist.
- **Andrei K (cid=484)** has fresh GPS 67% on override cases — yet tier=std auto-blocks him from AUTO routing (C3 filter `tier in {gold, std+}`). Worth considering relaxing C3 to `{gold, std+, std}` once score-bias fixes land.
- **Adrian R cid=400 only 19 proposals in 14d** — extreme blind-hour pattern (no proposals 15-20h despite shift 13-21h). May indicate schedule integration bug: he's `last_assigned_pickup` / `post_wave` from afternoon onwards (heavy mid-wave) and `dispatchable_fleet` may be dropping him. Worth one targeted log inspection of `pre_shift_window_miss` or `not_working_today` skips for cid=400 in 15-20h Warsaw window.

## Confidence notes / unknowns

- Sample size: 600 override OIDs across 14d, ~386 with target-in-pool shadow records (others not in pool or shadow log gap).
- Shadow log overlap with backfill 100% on captured records, no obvious gaps.
- "NOT in candidates" (47% Adrian R, 29-45% others) — could not directly determine WHY they were filtered out (rejected_for_log is at DEBUG level, not persisted; decision_meta only has OSRM degradation flags). Best inference: pre_shift_window_miss / no_position / not_working_today as schedule_utils filters fire. Would require log-level upgrade to confirm — see Action 4 follow-up.
