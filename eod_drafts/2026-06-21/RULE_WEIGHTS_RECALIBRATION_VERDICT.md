# rule_weights recalibration — replay verdict (read-only, 2026-06-21)

**Request:** prepare a recalibration of `dispatch_state/rule_weights.json` (R1/R5/R8 soft-penalty
weights, static since 2026-04-16) via replay.

**TL;DR verdict — DO NOT recalibrate the weight VALUES. It is a measured no-op.**
Reweighting R1/R5/R8 within ±2× changes the **picked courier in ≤0.7%** of decisions and the
**PROPOSE↔KOORD verdict in ≤1.6%**. The penalties are dwarfed by the typical winning margin and the
rule violations don't predict the real failure mode. Numbers below. **Zero production changes made.**

---

## Method (why this is a valid read-only replay)

The R1/R5/R8 penalties are **linear in the weight** (`dispatch_pipeline.py:4091/4095/4204`):
`bonus_r1_soft_pen = r1_violation_km · R1_spread_per_km`, etc. So a candidate's final score under a
new weight is exact arithmetic on the **logged** penalty — no engine re-run, no flip:
`score_new = score + bonus·(w_new/w_old − 1)` (R1/R5); R8 derives `viol8` from logged span+bag (its
fixed `-(span-7)·3` base is weight-independent and cancels). We re-rank `{best + alternatives}` by
score and ask whether the top-1 changes.

**Corpus:** 2967 shadow decisions (`shadow_decisions.jsonl` + `.1` rotation, ≈ several days);
2103 multi-candidate PROPOSE; realized outcomes from `backfill_decisions_outcomes_v1.jsonl` (n≈3021).
**Current weights:** R1 −8.0/km (thr 8km), R5 −6.0/km (thr 2.5km), R8 −1.5/min (thr 15/30min).
**Caveat:** logged `best` equals the score-argmax in only 55% of cases (the rest are
`_demote_blind_empty`/late-pickup tiering reorders); the proxy measures leverage on the **score
ranking**, which is the primary selection driver. The leverage is so small (<1%) that the caveat
doesn't change the conclusion.

---

## Findings

**(B) Leverage — remove a rule entirely, how often does the winner change?**
| change | winner flips (of 2103) |
|---|---|
| zero R1 | 9 (0.4%) |
| zero R5 | 5 (0.2%) |
| zero R8 | 3 (0.1%) |
| **zero ALL R1+R5+R8** | **14 (0.7%)** |

**(C) Sensitivity — scale one weight at a time, winner-flip vs current:**
| scale | R1 | R5 | R8 |
|---|---|---|---|
| 0.5× | 0.0% | 0.1% | 0.1% |
| 0.75× | 0.0% | 0.0% | 0.0% |
| 1.5× | 0.0% | 0.5% | 0.1% |
| 2.0× | 0.2% | 0.6% | 0.1% |

**(D) Why so inert:** median top-1 margin (score gap to #2) = **88 points**; the rule penalties
(−8/−6/−1.5 per unit of violation) are tiny beside it. The base score (0.30/0.25/0.25/0.20) and large
bonuses (R4 free-stop ≤150, R06 district ±40, corridor) dominate.

**(E) Verdict-boundary — does reweighting move the PROPOSE↔KOORD floor (MIN_PROPOSE_SCORE −100)?**
| scale (all 3) | PROPOSE→KOORD (of 2351) | KOORD→PROPOSE (of 318 low-score KOORDs) |
|---|---|---|
| 0.5× | 0 | 5 (1.6%) |
| 1.5× | 17 (0.7%) | 0 |
| 2.0× | 29 (1.2%) | 0 |

**Outcome context (`rule_deviation_report.py`, realized n≈3021 vs proposed n=2351):**
- Rules fire **constantly**: proposed bundles violate **R5 57.5%**, R8 38.3%, R1 22.9%.
- Yet **realized R6 breach (>35min) is only 8.3%** (median 17, p90 33). → the R1/R5/R8 violations
  **do not predict the real failure** — most "violating" bundles deliver fine. This is exactly why
  Adrian keeps them SOFT (hard R1 would kill peak throughput; audit 2026-05-21: 37/37 wide bundles
  were time-feasible).
- The real failure signal is R6 breach + **ETA underestimation (83.6%, median +9.3 min)** + per-courier
  reliability (top R6 breachers cid=533/529/484) — none of which `rule_weights` controls.

---

## Verdict & recommendation

1. **Do not recalibrate the R1/R5/R8 weight VALUES** — proven no-op (≤0.7% selection, ≤1.6% verdict
   under any ±2×). Per the cardinal rule ("net-szkodliwe/no-op → NIE rób, werdykt z liczbami"), this
   is a clear NIE-rób.
2. **The only change that would give them leverage is a large MAGNITUDE increase** (≈5–10×). But the
   outcome data says that would suppress many bundles that deliver fine (R6 breach is only 8.3% despite
   57% R5-violation) → **net-harmful to throughput** (R-FLEET-LEVEL / R-NO-WASTE). Not recommended; if
   ever pursued it needs outcome-validated design + ACK, not a weight tweak.
3. **Where the real calibration leverage is** (for whoever reviews this next):
   - **ETA calibration** — 83.6% underestimation, +9.3 min median bias. This is the highest-value lever
     and work is already in flight in 🟡 SHADOW (`eta_residual` R3, `eta_quantile_map`). That, not
     `rule_weights`, is where to focus.
   - **R6 hard gate + per-courier reliability / GPS** — the actual breach drivers (cid=533/529/484),
     consistent with the 20.06 KOORD-funnel finding (lever = GPS on idle couriers, not the algorithm).
   - **Fleet concentration** — Ziomek top-3 share 38.7% vs human 27.1% (a fairness/distribution lever).

---

## Reproduce (read-only)
```
PYTHONPATH=/root/.openclaw/workspace/scripts \
  /root/.openclaw/venvs/dispatch/bin/python \
  dispatch_v2/eod_drafts/2026-06-21/rule_weights_sensitivity.py        # leverage + sensitivity (B-E)
PYTHONPATH=/root/.openclaw/workspace/scripts \
  /root/.openclaw/venvs/dispatch/bin/python tools/rule_deviation_report.py   # realized vs proposed
```
No writes, no flips. `rule_weights.json` is unchanged.
