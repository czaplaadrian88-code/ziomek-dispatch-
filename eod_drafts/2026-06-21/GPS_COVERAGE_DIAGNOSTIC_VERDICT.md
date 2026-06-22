# GPS-coverage / no_gps per-courier — diagnostic verdict (read-only, 2026-06-21)

**Request:** the calibration arc (rule_weights / route-ETA / prep_bias all not flip-safe) pointed to
**operational** levers. The 20.06 KOORD-funnel work found GPS-on-idle-couriers as the one cheap lever.
This refreshes that per-courier on current data and adds coverage + trend. Read-only, nothing flipped.

**TL;DR — the GPS gap is concrete and tiny: THREE couriers' apps never send GPS (cid=518 Rogucki, 524
Charytoniuk, 525 Dawid Kr = 100% blind, 0 real-GPS fixes). BUT the no_gps→KOORD funnel is currently
DORMANT (~0 since 2026-06-15, vs 33–65/day in 06-11..14), so the *KOORD* ROI right now is low. The fix
is still worth it for DATA QUALITY (those couriers' ETA / R6-anchor / the B3 +12 min penalty all run on
fiction = BIALYSTOK_CENTER) and as latent-risk insurance (the funnel fired hard under early-June load).
This is an app/ops fix, not algorithm — confirming the whole investigation's conclusion.**

---

## Findings

**(A) Fleet coverage:** 29% of all candidate appearances are **blind** (no_gps / pre_shift / none);
only 15% are real live-GPS fixes. A large share of decision-time positions are fiction or stale.

**(B) Per-courier — the actionable list** (sorted by blind volume; CHRONIC = ≥90% blind):
| cid | name | blind | real-gps | total | blind% | class |
|---|---|---|---|---|---|---|
| 524 | Dawid Charytoniuk | 672 | **0** | 672 | **100%** | CHRONIC — app never sends GPS |
| 525 | Dawid Kr | 671 | **0** | 671 | **100%** | CHRONIC |
| 518 | **Michał Rogucki** | 330 | **0** | 330 | **100%** | CHRONIC — the 20.06 target |
| 500 | Grzegorz Rogowski | 263 | 0 | 567 | 46% | FREQUENT |
| 376 | Paweł Ściepko | 132 | 0 | 249 | 53% | FREQUENT |
| 75 | Patryk Milankiewicz | 112 | 0 | 193 | 58% | FREQUENT |

Contrast (apps work fine): cid=484 Andrei Kotsia 742 real-GPS / 8% blind; 370 Kuba Olchowik 375 / 11%.
→ Coverage is **bimodal**: most couriers always have GPS; a handful never do. It's per-courier app
state (install / login / location-permission / device), not a fleet-wide signal problem.

**(C) no_gps→KOORD funnel TREND (per date):**
| date | decisions | KOORD_all | KOORD_nogps (proxy) |
|---|---|---|---|
| 06-11 | 268 | 138 | 33 |
| 06-12 | 279 | 134 | 57 |
| 06-14 | 429 | 106 | 65 |
| 06-15 | 239 | 79 | 4 |
| **06-16 … 06-21** | ~210/day | **8–20** | **0** |

The funnel was a real problem 06-11..14 (high load, KOORD_all ~130/day) but has been **~0 for the last
week** — both because total KOORD collapsed (138→~11/day) and the situations stopped arising.
Authoritative KOORD attribution (`tools/no_gps_who.py`) over the full window: **75 no_gps-blocked
KOORD, 7 couriers, Rogucki = 57%** — but those are concentrated in the early window.

**B3 trial (`ENABLE_NO_GPS_UNCERTAINTY_PENALTY`, live since 20.06):** firing is not separately visible
(no distinct reason string; rescue returns a normal PROPOSE) — and with the funnel dormant there's
little for it to convert recently. It remains the algorithmic safety net for when no_gps situations
recur under load.

---

## Verdict & recommendation

1. **Concrete ops action (cheap, ~3 couriers):** check the courier app / GPS on **cid=518 Rogucki,
   524 Charytoniuk, 525 Dawid Kr** (100% blind, zero real fixes — almost certainly app not running /
   permission off / not logged in). Then the FREQUENT three (500, 376, 75 at ~50%). This is the entire
   lever — it's tiny and targeted, not systemic.
2. **Current KOORD urgency is LOW** (funnel ~0 for a week) — do **not** oversell it as "−75 KOORD now."
   The honest value today is: (a) **data quality** — those couriers' ETAs, R6 thermal anchoring, and
   the B3 +12 min uncertainty penalty are all computed from a fiction (BIALYSTOK_CENTER), so even when
   it doesn't cause a KOORD it produces worse proposals for them; (b) **latent-risk insurance** — the
   funnel fired hard under early-June peak load (33–65/day) and will recur without GPS.
3. **This confirms the investigation's thesis:** the real levers are operational (courier GPS / device
   state), not algorithm calibration. The engine side is already handled (B3 safety net + `_demote`
   policy); the remaining gap is field/ops — get the app reporting on those couriers.

## The full arc (2026-06-21, all read-only, nothing flipped)
calibration levers → all inert/already-good/noise (rule_weights, route-ETA, prep_bias) → **operational
lever (GPS coverage) → diagnosed to 3 chronic couriers; KOORD-impact currently low but data-quality +
latent-risk justify the app fix.** Every quantitative claim survived decomposition; none required a
production change.

---

## Reproduce (read-only)
```
PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/eod_drafts/2026-06-21/gps_coverage_diagnostic.py     # coverage table + funnel trend
PYTHONPATH=/root/.openclaw/workspace/scripts /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/tools/no_gps_who.py                                  # authoritative KOORD attribution
```
No writes, no flips.
