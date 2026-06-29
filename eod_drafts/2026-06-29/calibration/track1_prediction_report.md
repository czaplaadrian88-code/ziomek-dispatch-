# Track 1 — Prediction calibration (v2: per-day regime, SOLO-primary, BUNDLE-separate)

**Dataset:** `decisions_outcomes_loadbucketed.jsonl`, 2827 orders, 14 operational days (Warsaw, 2026-06-15 → 06-28). Read-only; outputs in scratchpad. Metric `eta_error_min = actual_delivered − predicted_delivered` (timestamp-based). **Positive = OPTIMISTIC** (delivered later than promised). Medians + 10%-trim (heavy tails). Non-koord throughout.
**Load v2** from `pool_feasible` (100% covered): `luzno` pf≥5 · `srednio` pf∈{2,3,4} · `ciasno` pf==1 · `pf0` pf==0 (separate).
**SOLO** = `bag_size==1` (single stop = CLEAN per-order ETA). **BUNDLE** = `bag_size≥2` (per-order error confounded by route divergence — see §c).

---

# ★ 06-29 (TODAY) = PRIMARY CALIBRATION WINDOW — current engine (added on owner's addendum)

The owner designates **today (2026-06-29) as the most representative day**: it reflects the CURRENT engine after all the changes, and is bundle-heavy/high-activity. Built fresh from source logs (`eta_calibration_log` + `shadow_decisions` load + `decision_outcomes` r6 + **`b_route_shadow` ground-truth `served` sequences** for the clean-bundle subset). **214 orders delivered + predicted** (71 solo / 143 bundle). **Open/undelivered: 22 of 236 dispatched orders had no completed delivery/prediction — excluded cleanly.** eta_error cross-checked: recomputed-from-timestamps − logger field = **median 0.00 min** (TZ correct).

**Today is a HIGH-LOAD day** (luzno nearly absent: 9 orders all day; srednio 109, ciasno 66, pf0 30) — so today anchors the *high-load* end of the curve, not luzno.

### Today's accuracy (signed median eta_error, load-segmented; positive = optimistic)
| | luzno | srednio | ciasno | pf0 |
|---|---|---|---|---|
| **SOLO** (clean primary) | +15.8 (n1) | **+29.0 (n36)** | **+30.9 (n33)** | +2.9 (n1) |
| **CLEAN-bundle** (served==delivered seq, n=108 total) | — | **+12.7 (n57)** | **+19.1 (n26)** | +22.4 (n7) |
| pooled bundle | +17.5 (n8) | +14.0 (n73) | +18.4 (n33) | +11.1 (n29) |
| ALL orders | +15.8 | +17.7 | +24.9 | +10.1 |

Today the current engine is **strongly optimistic and it worsens with load** (all-orders +15.8→+17.7→+24.9). **SOLO is far hotter than bundle (+29/+31 vs clean-bundle +13/+19)** — even after removing route divergence, solo orders suffer a much bigger miss today (they are the orders the engine couldn't bundle, typically assigned to a busy/distant courier → long pickup wait).

### Bundle route-divergence on ground-truth `served` (de-confounding the owner asked for) — confirmed but SMALL on the current engine
Using b_route_shadow's real planned dropoff order vs delivered order (137 of 143 bundle orders classifiable, ≥2 delivered bag-mates):
| bundle stop type | n | median eta | % delivered early (eta<0) |
|---|---|---|---|
| IN-ORDER (planned rank == delivered rank) | 108 | +14.2 | 13% |
| RESEQUENCED (rank moved) | 29 | +11.1 | **24%** |
| all classified bundle | 137 | +13.7 | 15% |
| solo (reference) | 71 | — | 8% |

**The resequencing signature is real:** resequenced stops are ~2× more likely to deliver early (24% vs 13%), and among bundle orders delivered early, resequenced are over-represented (33% of the early mass vs 21% base rate). **BUT the effect is SMALL on the current engine:** only 21% of today's bundle stops are resequenced, and **clean-bundle median (+14.2) ≈ pooled-bundle (+13.7)** — route-divergence barely moves the per-order bundle number today. This is an honest correction to my 14-day *heuristic* reconstruction (which estimated clean +8.4 vs pooled +13.9 at ciasno, ~5.5 min): with **ground-truth sequences**, de-confounding moves the bundle median by <1.5 min. → The pessimistic early-delivery mass is *partly* resequencing but *mostly* the broad spread of the pickup-slip; route divergence is a minor confound on the current engine, not the headline.

### ★ THE MECHANISM (corrects my earlier "drive" claim): it is PICKUP SLIP, not drive
Decomposing today's eta_error into **pickup-side** vs **delivery-leg**:
| load | eta_error | pickup_slip (picked − engine-assumed pickup) | delivery-leg miss (actual − `predicted_delivery_min`) |
|---|---|---|---|
| luzno  | +15.8 | +12.1 | +0.3 |
| srednio| +17.7 | +16.0 | +0.3 |
| ciasno | +24.9 | +18.1 | +4.6 |
| ALL    | +18.6 | **+17.9** | **+0.3** |

**The delivery/drive leg is essentially correct (~0 min miss today). ~100% of the optimism is that the courier reaches the restaurant ~18 min later than the engine assumed when it froze `predicted_delivered_at` at decision time** — and that pickup slip grows with load (queueing: courier still finishing prior stops). This is confirmed identically on the **14-day pool** (correct basis = `predicted_delivery_min`): pickup-side +5.4 / +12.0 / +17.4 across load, delivery-leg miss −0.9 / −2.2 / −2.6 (the drive leg is if anything slightly *conservative*).

**Correction to my original Track-1 decomposition:** I earlier attributed the optimism to drive (~2× OSRM). That used the wrong reference column (`predicted_drive_min` = raw OSRM, an internal ~0.5×-reality field that does NOT feed the promise). Against the **actual promise basis** (`predicted_delivery_min`), the drive leg is well-calibrated and **the optimism is pickup-side**. → The right fix is a **load-aware PICKUP buffer** (or re-anchoring the ETA at actual pickup), NOT a drive buffer.

**Independent cross-validation of the pickup-slip mechanism (3 sources):**
- **GPS physical delivered** (geofence truth, not button-press): today's eta_error vs GPS-physical = **+17.3 min** (n=36) ≈ logger +18.6 → the optimism is **physically real, not a late-tap artifact** (button-press vs physical delivered differ only +2.3 min).
- **`ready_at_log`** (220 today): food is frequently ready *early* (czasówki picked up before declared-ready, wait_min negative) → the slip is **NOT prep/food delay**; it is the courier arriving at the restaurant later than the engine assumed (queueing/busy under load).
- `decision_outcomes.pickup_lateness_min` is empty for today, so the derived `picked_up − engine-implied-pickup` (+17.9) is the load-bearing measure.

### Today's R6-reality breach (predicted-feasible ≤35 that actually breach 35) — high
solo srednio **14.7%** (n34) · solo ciasno **28.1%** (n32) · bundle srednio **16.9%** (n59) · bundle ciasno **24.1%** (n29) · **ALL today 20.4%** (n162). Far above the calm-window rates — consistent with the high-load pickup slip.

### Buffer FROM TODAY vs 14-day pool vs recent calm window (side by side)
median eta_error = the additive buffer the promise needs:
| signal × load | **TODAY 06-29 (current, high-load)** | recent calm 06-26…28 | 14-day pool |
|---|---|---|---|
| SOLO srednio | **+29.0** | +10.9 | +17.0 |
| SOLO ciasno  | **+30.9** | +16.2 | +21.2 |
| CLEAN-bundle srednio | **+12.7** | +6.2 | +8.1 |
| CLEAN-bundle ciasno  | **+19.1** | +6.1 | +8.4 |

→ **The buffer is NOT a fixed number — it tracks load, and today (high-load) sits well above both the calm window and the 14-day pool.** This is exactly why a single pooled buffer is wrong: deploy a **load-aware** correction, with today providing the high-load anchors and calm days the low-load anchors.

### Recommended (load-aware, kind-aware), from today's current engine
| load | SOLO buffer | clean-BUNDLE buffer | confidence |
|---|---|---|---|
| luzno (pf≥5)  | +5 | +5 | LOW today (n≈1–9 today → borrowed from calm window) |
| srednio (pf2–4)| **+25–29** | **+13** | MED (solo n36 / clean-bundle n57 today; one day) |
| ciasno (pf==1) | **+30** | **+19** | MED (solo n33 / clean-bundle n26 today; one day) |

**Bottom line for deployment:** the current engine under load promises delivery ~18–30 min too early, almost entirely because it under-estimates *when the courier will pick up* (slip grows with load); the drive leg is fine. Add a **load-aware pickup/delivery buffer** (solo needs ~2× the bundle buffer), or better, **re-anchor the committed ETA at actual pickup**. Confidence on the *mechanism and direction* is HIGH (consistent today + 14-day pool); confidence on the *exact high-load minutes* is MEDIUM (today is a single high-load day — more high-load days would tighten it).

### ★ CONFIRM-or-REVISE vs my 14-day heuristic, and FINAL deploy verdict
The 06-29 build uses the **true `served` planned sequence** — the real version of my 14-day heuristic. It **REVISES two heuristic conclusions:**

1. **"Pooled tight-load bundle (+13.9) was inflated ~+5.5 min by route-divergence" → REVISED (overturned).** With true sequences, **clean-bundle ≈ pooled-bundle today** (overall clean +14.2 vs pooled +13.7; srednio clean +12.7 vs pooled +14.0 = −1.3; ciasno clean +19.1 vs pooled +18.4 = +0.7). The clean-vs-pooled delta is **<1.5 min and mixed-sign**, not +5.5. The +13.9-vs-+8.4 gap in my heuristic was largely a **reconstruction artifact** (noisy courier+pickup-proximity clustering), not real route contamination. Route-divergence on the current engine is a **minor per-order confound**; it shows up as a small early-delivery over-representation (resequenced stops 24% early vs in-order 13%) but does **not** materially move the bundle median.

2. **"Clean-bundle optimism is FLAT at ~+5..+8 across load" → REVISED.** That was true only in the *calm* window (06-26…28: clean-bundle srednio +6.2 / ciasno +6.1). On the **high-load current-engine day**, clean-bundle is **srednio +12.7 / ciasno +19.1** — 2× higher at the *same* pf-segment. So clean-bundle optimism is **not a fixed +5..+8; it scales with how busy the day actually is**, and the coarse 3-bucket pf-segment does **not** fully capture pickup-queue intensity (same `srednio` bucket → +6 calm vs +13 busy).

3. **Therefore the +5/+7/+6-8 recommendation → REVISED for the current engine.** Those are *low-load* numbers. **06-29 is the post-06-23 calibrated-drive regime = the CURRENT engine** (most decision-relevant), and under load it needs far more: **clean-bundle +13 (srednio) / +19 (ciasno), solo +25–30.** The calm-window +6 is the *floor* (low-load); it is not what to deploy as a flat buffer.

**FINAL deploy verdict:**
- **Do NOT deploy a single flat buffer.** Deploy a **load-aware pickup buffer** that ranges from ~**+5 min at low load (luzno)** up to ~**+13 (bundle) / +25–30 (solo) at srednio–ciasno on busy days**, with **solo ≈ 2× the bundle buffer**.
- Key it on a **finer/continuous load or recent-pickup-slip signal** (e.g., EWMA of realized pickup slip, or continuous pool_feasible), **not** the 3 coarse buckets — because the same bucket swings +6→+13 between a calm and a busy day.
- **Best fix is structural, not a buffer:** **re-anchor the committed ETA at actual pickup** (recompute delivery promise when the courier actually picks up). That removes the +18 min frozen-promise error at its root and makes the buffer largely unnecessary.
- **Confidence:** mechanism + direction **HIGH** (06-29 true-served + 14d pool + GPS agree); route-divergence-is-minor **HIGH** (true sequences); exact high-load minutes **MEDIUM** (one high-load day — confirm on the next 1–2 busy days before locking magnitudes).

---

# RECONCILIATION — owner's "pessimistic days" vs the data (added on review)

**Verdict up front: the owner is RIGHT that pessimism happened and that the engine changed a lot — but the pessimism does NOT live in whole-day medians (no day's solo/bundle median goes negative); it lives in (1) BUNDLE resequencing and (2) a drive-basis that changed mid-window. My earlier "all 14 days optimistic" was true only at the MEDIAN and masked a large, real pessimistic mass. I am correcting that.**

### P1a — Per-day SIGNED median (solo vs bundle): no median flips, but that is not the whole story
Every day's SOLO median (+5.2…+28.9) and BUNDLE median (+0.9…+12.1) is positive — see §(a). No day is net-pessimistic at the median. BUT pessimistic ORDERS are common: **8.1% of all orders are strongly pessimistic (eta_error < −10 min)** — and they are **89% bundle, 74% "calibrated-basis"** (see P1c). On calm days 20–46% of *bundle* orders deliver early. So "pessimistic days" is real at the order/condition level, just not at the day median.

### P1b — BUNDLE resequencing is the dominant pessimism mechanism (the owner's exact concern)
Reconstructed 448 multi-order bags (courier_id_final + pickup ≤25 min cluster), then within each bag compared **planned order (by `predicted_delivered_at`) vs actual order (by `delivered_at`)**:
- **242 CLEAN bags (delivered seq == planned seq)** vs **206 REORDERED bags.**
- In REORDERED bags, splitting by planned stop position:
  - **first-planned stop: median +21.1** (delivered late → very optimistic, 9% pessimistic)
  - **last-planned stop: median +1.0, 47% PESSIMISTIC** (delivered EARLY because the courier brought it forward).
- In CLEAN bags, first and last planned stops are symmetric (+6.0 / +6.1, 26% neg each) — no position artifact.

→ **Exactly "przy workach trasa mogła inaczej wyglądać niż ziomka": resequencing makes the late-planned stop arrive early (pessimistic) and the first-planned stop arrive late (optimistic); pooling cancels them and hides both.** This is why per-order bundle error must never be pooled with solo, and why ~half of reordered-bag late stops look pessimistic.

### P1c — The promise's drive basis CHANGED during the window (concrete "było dużo zmian")
`predicted_drive_min` vs `raw_drive_min` per day:
- **06-15 → 06-22: `predicted_drive == raw` OSRM in ~99–100% of orders** (raw basis = optimistic, ~0.5× reality).
- **06-23 → 06-28: match drops to ~3–10%** — `predicted_drive` was inflated to ~1.4–1.5× raw (a calibration uplift switched on ~06-23).
- Two calibration-anomaly days: **06-17 (calib/raw ≈ 1.70) and 06-25 (≈ 1.43)** vs ~5× on every other day — the `calibrated_drive` pipeline was in a different state those days.
- Orders whose `predicted_delivery_min` leans on the **calibrated** (pessimistic) basis are **less optimistic (+6.3, 32% neg) than raw-basis orders (+10.2, 21% neg)** — so when the promise used the pessimistic estimate, optimism shrank and the pessimistic tail grew, but not enough to flip the daily median.

→ The owner's recollection of unstable/pessimistic behaviour is corroborated by a **real config change (~06-23) and the calibrated-basis subset**, even though no full day flips sign.

### P2 — CLEAN-bundle refit (pooling was the confound): pooled bundle OVERSTATES the tight-load buffer
| segment | SOLO 14d | CLEAN-bundle 14d | **POOLED bundle 14d** | CLEAN-bundle window |
|---|---|---|---|---|
| luzno   | +7.8 (244) | +4.8 (153) | +4.0 (650) | +4.7 (79) |
| srednio | +17.0 (324)| +8.1 (208) | +8.1 (823) | +6.2 (66) |
| ciasno  | +21.2 (93) | **+8.4 (79)** | **+13.9 (280)** | +6.1 (29) |

**At ciasno, pooling route-divergent bundles inflated the buffer from +8.4 (clean) to +13.9 (pooled) — ~5.5 min of pure route contamination.** Clean-bundle optimism is far FLATTER across load (4.8→8.1→8.4) than pooled bundle or solo. My earlier pooled **+4/+11/+16** buffer was contaminated and is withdrawn.

### P3 — Recent window vs 14-day: the 14-day number is NOT deployable
SOLO 14d srednio/ciasno (+17.0/+21.2) are far hotter than the recent window (+10.9/+16.2) because the 14-day pool includes the **high-load peak days 06-21/06-22** (different regime). So a 14-day buffer would over-buffer today. Deploy from the **recent stable window (06-26…28)**, de-contaminated:

**De-contaminated recommended buffer (SOLO clean + CLEAN-bundle, recent window):**
| segment | recommend | basis (recent window) | confidence |
|---|---|---|---|
| luzno   | **+5 min** | solo +6.9 (n48), clean-bundle +4.7 (n79) | HIGH |
| srednio | **+7 min** | solo +10.9 (n25, thin), clean-bundle +6.2 (n66) | MEDIUM |
| ciasno  | **+6–8 min** | clean-bundle +6.1 (n29); solo n=8 unusable | LOW (solo too thin; was NOT +13–16) |

**The single biggest correction:** the tight-load (ciasno) buffer is **~+6–8 min, not +11–16.** The larger number was an artifact of (a) pooling route-resequenced bundle stops and (b) peak days in the 14-day pool. SOLO is too thin in any 2–3-day window at srednio/ciasno to confirm a solo-specific uplift, so the deployable number leans on the de-confounded clean-bundle signal. (Caveat: bag reconstruction is a heuristic — courier+pickup-proximity clustering, planned order = `predicted_delivered_at` order — because the only ground-truth route log `b_route_shadow.jsonl` covers 06-29 only, zero overlap with this window. 06-29 with true `served` sequences is the right day to confirm.)

---

## (a) PER-DAY regime — SOLO vs BUNDLE (the headline that replaces a single pooled number)

| day (Warsaw) | SOLO med | p25 | %neg | n | BUND med | p25 | %neg | n |
|---|---|---|---|---|---|---|---|---|
| 06-15 | +7.6 | −3.0 | 33% | 33 | +12.1 | +1.1 | 20% | 119 |
| 06-16 | +16.4 | +5.9 | 12% | 85 | +6.6 | −0.8 | 27% | 119 |
| 06-17 | +12.4 | +3.3 | 16% | 58 | +5.3 | −2.9 | 34% | 117 |
| 06-18 | +10.8 | +1.9 | 24% | 51 | +0.9 | −3.8 | 46% | 122 |
| 06-19 | +10.0 | +3.8 | 14% | 57 | +3.8 | −5.2 | 41% | 153 |
| 06-20 | +11.1 | +0.2 | 22% | 58 | +2.2 | −5.6 | 41% | 122 |
| 06-21 | **+24.0** | +13.6 | 7% | 72 | +8.8 | −2.1 | 28% | 281 |
| 06-22 | **+28.9** | +15.5 | 12% | 42 | +10.1 | −0.4 | 26% | 125 |
| 06-23 | +13.3 | +5.9 | 9% | 35 | +11.3 | +1.2 | 21% | 180 |
| 06-24 | +9.7 | +3.2 | 20% | 44 | +3.6 | −3.7 | 37% | 115 |
| 06-25 | +18.0 | +10.6 | 6% | 49 | +8.5 | +0.6 | 20% | 113 |
| 06-26 | +13.2 | +1.6 | 23% | 22 | +9.0 | +0.9 | 23% | 211 |
| 06-27 | +5.2 | +1.4 | 18% | 17 | +5.6 | −1.7 | 28% | 103 |
| 06-28 | +10.9 | +2.6 | 23% | 43 | +5.0 | −2.2 | 34% | 181 |

**What the table shows (honest reading, correcting the premise):**
1. **No median SIGN-FLIP.** SOLO and BUNDLE medians are POSITIVE (optimistic) on **all 14 days**. The engine is not "pessimistic some days" at the median.
2. **The "pessimistic" reality is the within-day MASS, mostly in BUNDLES.** Bundle `p25` is negative on most days and **20–46% of bundle orders deliver early (eta_error<0)** — this bimodal spread is the **route-divergence signature** (in a bag, some orders arrive earlier than their planned slot, some later). SOLO has a much smaller pessimistic mass (6–33%, p25 mostly ≥0). This is why per-order bundle error must NOT be pooled with solo.
3. **The real drift is in MAGNITUDE, not sign.** SOLO median swings **+5.2 → +28.9** and BUNDLE **+0.9 → +12.1** across days. The spikes are the **high-load peak days 06-21/06-22** (SOLO +24/+29). A single pooled "+8" hides a factor-of-5 day-to-day swing → confirms: don't quote one number; segment and use a recent stable window.
4. **SOLO > BUNDLE almost every day** — solo orders get the tightest promise and slip most.

---

## (b) Chosen calibration window: **06-26 + 06-27 + 06-28 (last 3 days)**

**Why:** most recent 3 consecutive days; behaviourally stable (all-order daily medians +9.2/+5.4/+5.4; bundle medians +9.0/+5.6/+5.0 — tight band); deliberately **excludes the high-load 06-21/22 spike and the +18 06-25 day** (different regime). Good volume: 577 non-koord eta rows (bundle 495). Data ends 06-28, so no same-day (06-29) noise to exclude.
**Caveat (stated):** SOLO is thin in any 3-day window (n=82 total). **Class-level breakdown is NOT viable** — 0 of 9 solo×load×class cells reach n≥30 → **class dropped; reporting SOLO/BUNDLE × LOAD only**, per instruction.

---

## (c) Calibration on the window — SOLO first (clean), BUNDLE separate

### SOLO × load (clean primary signal)
| segment | n | median | trim10 | p25 | p75 | status |
|---|---|---|---|---|---|---|
| luzno  | 48 | **+6.9** | +9.2 | −0.1 | +18.1 | **CALIB (n≥30)** |
| srednio| 25 | +10.9 | +16.8 | +2.8 | +29.9 | thin (indicative) |
| ciasno | 8  | +16.2 | +26.3 | +9.5 | +33.2 | **too thin — do NOT calibrate** |

### BUNDLE × load (route-divergence-confounded — secondary, see caveat)
| segment | n | median | trim10 | p25 | p75 |
|---|---|---|---|---|---|
| luzno  | 200 | +5.2 | +5.6 | −1.1 | +11.4 |
| srednio| 159 | +7.0 | +8.6 | −0.3 | +17.1 |
| ciasno | 67  | +10.5| +10.1| +2.5 | +20.2 |
| pf0 (excl.) | 69 | +5.9 | +4.5 | −8.3 | +16.5 |

**BUNDLE route-divergence caveat (cannot be removed on this window):** per-order bundle `predicted_delivered_at` assumes Ziomek's PLANNED stop sequence; the courier may have resequenced. The only route-sequence log, `dispatch_state/b_route_shadow.jsonl` (field `served`), is **entirely dated 06-29 with ZERO overlap** with the 14-day dataset, and there is no planned-sequence log covering 06-15…28 — so the **clean `delivered-seq==planned-seq` subset cannot be extracted for these days**. Therefore bundle per-order error is reported as a confounded secondary; the 20–46% early-delivery mass (§a) is the visible divergence. **Bag-level interpretation:** the bundle MEDIAN is still informative (resequencing reshuffles WHICH order is early/late but the bag's overall completion drift survives in the median), and it tracks the solo gradient.

**Both signals agree on the load gradient** (rises with load): SOLO 6.9→10.9→16.2, BUNDLE 5.2→7.0→10.5. SOLO runs ~2–6 min hotter (tighter promises; bundle per-order partially cancels via early stops). Where SOLO is too thin (srednio/ciasno), the BUNDLE gradient + the SOLO shape anchor the recommendation.

### Recommended per-segment buffer (window-anchored), with confidence
| segment | recommend add | basis | confidence |
|---|---|---|---|
| luzno  | **+6 min**  | solo CALIB +6.9, bundle +5.2 | **HIGH** (solo n=48, both agree) |
| srednio| **+8–9 min**| solo thin +10.9, bundle +7.0 | MEDIUM (solo thin; bundle solid) |
| ciasno | **+11–13 min** | solo uncalibratable (n=8, +16), bundle +10.5 + full-window shape | LOW (no clean solo n; lean on bundle) |

Additive (not multiplicative) — load-sensitivity is captured by the pf-segment. Solo deserves a slightly larger buffer than bundle at each load if the engine can distinguish them.

---

## R6-REALITY breach (35-min rule) — predicted-feasible bags that ACTUALLY breach 35

| scope | cell | n | breach | rate |
|---|---|---|---|---|
| **window 06-26..28** | solo luzno | 46 | 1 | **2.2%** |
| | solo srednio | 22 | 4 | **18.2%** |
| | bundle luzno | 196 | 3 | **1.5%** |
| | bundle srednio | 147 | 13 | **8.8%** |
| | bundle ciasno | 49 | 5 | **10.2%** |
| **full 14d** | solo luzno | 74 | 3 | 4.1% |
| | solo srednio | 72 | 10 | **13.9%** |
| | solo ciasno | 21 | 6 | **28.6%** |
| | bundle luzno | 263 | 10 | 3.8% |
| | bundle srednio | 257 | 22 | 8.6% |
| | bundle ciasno | 71 | 6 | 8.5% |

Breach rate **climbs with load and is worse for SOLO** (full-14d solo: 4%→14%→29%). The recent window confirms solo-srednio ~18%, bundle-ciasno ~10%. (~35% r6_actual coverage; ciasno solo n is small — directional.) From the earlier full analysis: breaches are NOT concentrated near the 35 cap (even pred_r6<20 breaches under load) → tightening the admission cap is ineffective; the coherent fix is feeding the load-aware buffer into the R6 drive term (separate engine-behaviour decision).

---

## OOS / in-sample status (within-regime, volume-limited)

Within-regime mini-OOS: train 06-26+06-27 → test held-out 06-28.
- **BUNDLE generalizes:** corr {luzno +6.0, srednio +6.9, ciasno +10.8}; test n=166; median|err| **7.98 → 7.57**; signed median **+5.34 → −0.96** (de-biased on the held-out day). ✅
- **SOLO too thin for OOS:** only luzno calibratable on train (+5.8); test n=43 mixes uncalibrated srednio/ciasno; median|err| 11.37→10.85, signed +10.85→+6.50 (partial). The solo calibration is therefore **IN-SAMPLE on a stable window**, not OOS-validated — stated explicitly.

**Verdict:** the load-aware buffer **de-biases** the promise (signed median → ~0) and holds out-of-day for BUNDLE; for SOLO the window lacks volume for OOS, so the solo luzno buffer is in-sample-on-stable-window and the srednio/ciasno solo buffers are indicative only. The residual ~7–11 min median|err| is irreducible per-order spread (drive/prep/route variance), not removable by a static buffer.

---

## Confidence summary
- **Per-day regime table & "no sign-flip, magnitude drifts 5→29, bundle 20–46% early" finding: HIGH.**
- **luzno buffer +6: HIGH** (solo n=48 + bundle agree). **srednio +8–9: MEDIUM** (solo thin, bundle solid, OOS-held for bundle). **ciasno +11–13: LOW** (no clean solo n; bundle + shape only).
- **R6 breach rising with load, worse for solo: MEDIUM-HIGH** (window n small for ciasno-solo).
- **Bundle route-divergence un-removable on this window: HIGH** (b_route_shadow zero overlap — a hard data limit, not a choice).
