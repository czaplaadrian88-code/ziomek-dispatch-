# Pickup-timing slip / prep_bias — replay verdict (read-only, 2026-06-21)

**Request:** the ETA verdict located the real bias as the +9 min **pickup-timing slip** (prep /
declared-time), not the route ETA. This replay asks: is that slip per-restaurant **systematic** (so a
`prep_bias` table fixes it) or per-order **noise** (so it can't be calibrated away)?

**TL;DR — the +9 min pickup slip is IRREDUCIBLE PER-ORDER NOISE, not a per-restaurant offset. Restaurant
identity explains R²≈0.00 of its variance; a per-restaurant `prep_bias` correction performs identically
to a flat global one, and BOTH break 83–100% of already-on-time pickups (overcorrection). `prep_bias` is
not a flip-safe lever. Keep it shadow; R-DECLARED-TIME (czas_kuriera ≥ declared) stays authoritative.**
Zero production changes; nothing flipped.

---

## Method
Per-order slip recovered tz-safely from logged durations (no timezone math):
`slip = eta_error_min − (real_delivery_min − predicted_delivery_min)` = how much later pickup happened
than the prediction assumed. Corpus: 5434 matched non-czasówka deliveries with a restaurant. Robust
(median) metrics; |slip|≤45 filter drops reconcile artifacts.

## Findings

**(A) The slip is real and large:** median **+9.31**, mean +11.33, **std 11.18** (spread > the median),
only 29.9% of pickups within ±5 min.

**(B) Variance decomposition — the ceiling of any per-restaurant correction:**
- 56 restaurants with ≥10 orders (98% of volume). Total slip variance 125.0, within-restaurant 124.5
  → **restaurant explains R² = 0.00.**
- Per-restaurant median slip spans only +4.2 → +15.7 (modest between-restaurant spread), but **40/56
  restaurants have within-std > |median|** — per-order noise dominates the signal.

**(C) Held-out (train 3260 / test 2174), residual slip after correction:**
| corrector | median|slip| | median | on-time ≤5 |
|---|---|---|---|
| BASE (none) | 8.72 | +8.48 | 31.8% |
| GLOBAL (−10 to all) | 6.70 | −1.57 | 36.3% |
| **PER-RESTAURANT (prep_bias)** | **6.69** | −1.67 | 37.0% |

→ Per-restaurant `prep_bias` is **identical to a flat global subtraction** — restaurant info adds
nothing (corroborates R²≈0). Both only remove the median bias; the spread (the thing that matters)
is untouched.

**(D) Regression guard — both corrections are net-harmful to on-time pickups:**
Among the 691 test pickups that were already on-time (|slip|≤5): **GLOBAL breaks 100%, PER-RESTAURANT
breaks 83.4%** (pushed to >5 min off). Subtracting a +9 median from a ±11 distribution converts
systematic-late into systematic-early without reducing error.

**(E) Sanity vs shipped `restaurant_prep_bias.json`:** measured slip +9.3/std 11.2 matches the table's
global bias_med 11.0 / std 15.3. The table's own std ≫ median independently confirms noise dominates.

---

## Verdict & recommendation
1. **Do not flip `prep_bias` (`ENABLE_PREP_BIAS_TABLE`).** It cannot fix per-order noise (R²≈0), gives
   nothing over a flat offset, and overcorrects the on-time majority. Flipping it would also shift the
   R6 thermal anchor by a noisy per-restaurant constant → harmful. Keep it shadow (telemetry only).
2. **R-DECLARED-TIME staying authoritative is correct** — you cannot systematically pre-shift pickup
   readiness because the variance is genuinely per-order (a restaurant is on time sometimes, 30 min
   late other times, unpredictably).
3. **The +9 min slip is not a calibration problem — it's an information problem.** The only things that
   could reduce it are **operational/real-time signals**, not a static table:
   - a real "food ready" confirmation from the restaurant (POS/integration), or
   - courier-arrival telemetry — i.e., the same GPS/ops lever the 20.06 KOORD-funnel analysis found.
   The system already absorbs the variance defensively via DWELL + the R6 30–35 soft zone.

## Closing the loop — all three calibration levers, settled by replay (2026-06-21)
| candidate lever | replay verdict |
|---|---|
| `rule_weights` R1/R5/R8 | **inert** — ±2× moves ≤0.7% of selections (no-op) |
| route ETA (drive/dwell/traffic) | **already calibrated** — drive leg median −1.2 min |
| `prep_bias` / pickup slip | **irreducible per-order noise** — R²≈0, static correction overcorrects |
**None is a flip-safe calibration win.** The real levers are operational (GPS on idle couriers,
per-courier reliability, real-time restaurant-ready signals), consistent with the 20.06 finding that
the KOORD funnel is a GPS/obsada problem, not an algorithm one.

---

## Reproduce (read-only)
```
cd /root/.openclaw/workspace/scripts && PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/eod_drafts/2026-06-21/pickup_slip_prep_bias_replay.py
```
No writes, no flips. `restaurant_prep_bias.json` / `ENABLE_PREP_BIAS_TABLE` unchanged.
