# Courier Whitelist Proposal — Faza 7 AUTO routing

Generated: 2026-05-27T18:40:07.123315+00:00

Source: `/tmp/backfill_decisions_outcomes_v1.jsonl` (n=3071 rows w/ outcome → 1674 unique orders)


## Executive summary

- **Baseline PANEL_OVERRIDE rate: 78.6%** (1316/1674 unique orders)
- **Median override rate (couriers with n_proposed>=30): 76.7%**
- Adrian's strict criterion `override<30% AND n_proposed>=50 AND r6<10%`: **0 couriers qualify**
- Buckets below use **RELATIVE-TO-BASELINE** thresholds, tier-aware (see _meta.criteria_rationale)

| Bucket | Count |
|---|---:|
| **WHITELIST** | 1 |
| **CONDITIONAL** | 11 |
| **BLACKLIST** | 2 |
| **INSUFFICIENT_DATA** | 15 |

## WHITELIST (proposed)

| cid | name | tier | override | n_prop | n_won | actual_14d | r6_breach | strict? | reason |
|---|---|---|---:|---:|---:|---:|---:|:---:|---|
| 393 | Michał K. | std+ |  60.7% | 61 | 19 | 152 | 10.3% (n=126) | — | override 60.7% vs baseline 78.6% (better by 18.0pp); r6_breach 10.3% (n=126); operator-favorite: 85% of actuals not proposed |

## CONDITIONAL (limited use, e.g. off-peak only / shadow-A/B before AUTO)

| cid | name | tier | override | n_prop | n_won | actual_14d | r6_breach | strict? | reason |
|---|---|---|---:|---:|---:|---:|---:|:---:|---|
| 484 | Andrei K | std |  61.0% | 41 | 12 | 109 | 13.7% (n=102) | — | override 61.0% vs baseline 78.6% (better by 17.6pp); r6_breach 13.7% (n=102); operator-favorite: 88% of actuals not proposed |
| 457 | Adrian Cit | std+ |  53.3% | 45 | 16 | 77 | 8.3% (n=72) | — | override 53.3% vs baseline 78.6% (better by 25.3pp); r6_breach 8.3% (n=72); operator-favorite: 78% of actuals not proposed |
| 370 | Jakub OL | std+ |  76.7% | 73 | 11 | 131 | 8.0% (n=125) | — | override 76.7% vs baseline 78.6% (better by 1.9pp); r6_breach 8.0% (n=125); operator-favorite: 91% of actuals not proposed |
| 413 | Mateusz O | gold |  79.9% | 249 | 26 | 125 | 1.7% (n=120) | — | override 79.9% vs baseline 78.6% (worse by 1.3pp); r6_breach 1.7% (n=120); operator-favorite: 78% of actuals not proposed |
| 508 | Michał Li | slow |  68.5% | 54 | 6 | 71 | 10.3% (n=68) | — | override 68.5% vs baseline 78.6% (better by 10.1pp); r6_breach 10.3% (n=68); operator-favorite: 91% of actuals not proposed |
| 123 | Bartek O. | gold |  82.1% | 112 | 10 | 118 | 1.9% (n=104) | — | override 82.1% vs baseline 78.6% (worse by 3.5pp); r6_breach 1.9% (n=104); operator-favorite: 91% of actuals not proposed |
| 470 | Piotr Zaw | std |  75.2% | 105 | 20 | 80 | 8.2% (n=73) | — | override 75.2% vs baseline 78.6% (better by 3.4pp); r6_breach 8.2% (n=73); operator-favorite: 73% of actuals not proposed |
| 179 | Gabriel | gold |  85.8% | 373 | 26 | 109 | 3.1% (n=98) | — | override 85.8% vs baseline 78.6% (worse by 7.2pp); r6_breach 3.1% (n=98); operator-favorite: 74% of actuals not proposed |
| 515 | Szymon P | std |  81.7% | 191 | 17 | 71 | 4.7% (n=64) | — | override 81.7% vs baseline 78.6% (worse by 3.1pp); r6_breach 4.7% (n=64); operator-favorite: 74% of actuals not proposed |
| 517 | Gabriel Je | std |  58.3% | 36 | 7 | 18 | 16.7% (n=18) | — | override 58.3% vs baseline 78.6% (better by 20.3pp); r6 sample low (n=18) |
| 376 | Paweł SC | std+ |  85.7% | 49 | 3 | 17 | 0.0% (n=11) | — | override 85.7% vs baseline 78.6% (worse by 7.1pp); r6 sample low (n=11) |

## BLACKLIST (never AUTO)

| cid | name | tier | override | n_prop | n_won | actual_14d | r6_breach | strict? | reason |
|---|---|---|---:|---:|---:|---:|---:|:---:|---|
| 471 | Łukasz W | std |  96.5% | 58 | 0 | 38 | 5.4% (n=37) | — | override 96.6% vs baseline 78.6% (worse by 17.9pp); r6_breach 5.4% (n=37); operator-favorite: 100% of actuals not proposed |
| 514 | Tomasz Ch | std |  69.8% | 43 | 10 | 44 | 21.6% (n=37) | — | override 69.8% vs baseline 78.6% (better by 8.8pp); r6_breach 21.6% (n=37); operator-favorite: 73% of actuals not proposed |

## INSUFFICIENT_DATA (n_proposed < 30 — needs longer observation)

| cid | name | tier | override | n_prop | n_won | actual_14d | r6_breach | strict? | reason |
|---|---|---|---:|---:|---:|---:|---:|:---:|---|
| 400 | Adrian R | std+ |  47.4% | 19 | 6 | 117 | 7.1% (n=112) | — | n_proposed<30 (19); r6_breach 7.1% (n=112); operator-favorite: 95% of actuals not proposed |
| 509 | Dariusz M | std+ |  60.0% | 25 | 9 | 95 | 8.1% (n=87) | — | n_proposed<30 (25); r6_breach 8.0% (n=87); operator-favorite: 90% of actuals not proposed |
| 503 | Gabriel J | std |  66.7% | 21 | 6 | 56 | 18.0% (n=50) | — | n_proposed<30 (21); r6_breach 18.0% (n=50); operator-favorite: 88% of actuals not proposed |
| 520 | Michał Rom | std |  35.3% | 17 | 10 | 53 | 15.1% (n=53) | — | n_proposed<30 (17); r6_breach 15.1% (n=53); operator-favorite: 81% of actuals not proposed |
| 524 | Dawid Cha | new |  50.0% | 6 | 3 | 50 | 8.7% (n=46) | — | n_proposed<30 (6); r6_breach 8.7% (n=46); operator-favorite: 93% of actuals not proposed |
| 409 | Mateusz Bro | std |  65.0% | 20 | 7 | 49 | 2.1% (n=48) | — | n_proposed<30 (20); r6_breach 2.1% (n=48); operator-favorite: 85% of actuals not proposed |
| 500 | Grzegorz | new |  93.3% | 15 | 1 | 39 | 5.7% (n=35) | — | n_proposed<30 (15); r6_breach 5.7% (n=35); operator-favorite: 97% of actuals not proposed |
| 518 | Michał Ro | std |  83.3% | 12 | 2 | 33 | 10.0% (n=30) | — | n_proposed<30 (12); r6_breach 10.0% (n=30); operator-favorite: 94% of actuals not proposed |
| 75 | Patryk | std |  46.2% | 26 | 10 | 33 | 18.2% (n=33) | — | n_proposed<30 (26); r6_breach 18.2% (n=33); operator-favorite: 70% of actuals not proposed |
| 207 | Marek | std |  86.2% | 29 | 4 | 21 | 15.0% (n=20) | — | n_proposed<30 (29); r6_breach 15.0% (n=20) |
| 522 | Szymon Sa | new |   0.0% | 0 | 0 | 21 | 14.3% (n=21) | — | n_proposed<30 (0); r6_breach 14.3% (n=21) |
| 527 | cid=527 | unknown |   0.0% | 0 | 0 | 19 | 35.3% (n=17) | — | n_proposed<30 (0); r6 sample low (n=17) |
| 387 | Aleksander G | std |  72.7% | 11 | 2 | 17 | 0.0% (n=15) | — | n_proposed<30 (11); r6 sample low (n=15) |
| 526 | cid=526 | unknown |   0.0% | 0 | 0 | 12 | 9.1% (n=11) | — | n_proposed<30 (0); r6 sample low (n=11) |
| 502 | Kacper Sa | std+ |   0.0% | 3 | 2 | 9 | 12.5% (n=8) | — | n_proposed<30 (3); r6 sample low (n=8) |

## Cross-reference: Q1 v2 Finding 3

Q1 v2 Agent 1 identified:
- **Top OVERRIDDEN by operator** (Ziomek proposes, operator changes): cid 179 (320×), 413 (198×), 515 (156×)
- **Top FAVORITES picked ad-hoc by operator**: cid 370 (101×), 400 (98×), 393 (94×)

| cid | name | expected | bucket | override | n_prop | actual_14d | actual_not_proposed | comment |
|---|---|---|---|---:|---:|---:|---:|---|
| 179 | Gabriel | BLACKLIST (top overridden) | **CONDITIONAL** ✗ |  85.8% | 373 | 99 | 73 |  |
| 413 | Mateusz O | BLACKLIST (top overridden) | **CONDITIONAL** ✗ |  79.9% | 249 | 120 | 94 |  |
| 515 | Szymon P | BLACKLIST (top overridden) | **CONDITIONAL** ✗ |  81.7% | 191 | 66 | 49 |  |
| 370 | Jakub OL | WHITELIST (operator favorite) | **CONDITIONAL** ✗ |  76.7% | 73 | 126 | 115 | low n_proposed (73) — Ziomek rarely proposes this courier but operator forces them in 115/126 actuals (91%). LATENT — proposed-based override_rate masks problem. |
| 400 | Adrian R | WHITELIST (operator favorite) | **INSUFFICIENT_DATA** ✗ |  47.4% | 19 | 112 | 106 | low n_proposed (19) — Ziomek rarely proposes this courier but operator forces them in 106/112 actuals (95%). LATENT — proposed-based override_rate masks problem. |
| 393 | Michał K. | WHITELIST (operator favorite) | **WHITELIST** ✓ |  60.7% | 61 | 126 | 107 |  |

## Tier breakdown per bucket

| Bucket | gold | std+ | std | slow | new | unknown |
|---|---:|---:|---:|---:|---:|---:|
| WHITELIST | 0 | 1 | 0 | 0 | 0 | 0 |
| CONDITIONAL | 3 | 3 | 4 | 1 | 0 | 0 |
| BLACKLIST | 0 | 0 | 2 | 0 | 0 | 0 |
| INSUFFICIENT_DATA | 0 | 3 | 7 | 0 | 3 | 2 |

## Risk assessment — marginal cases

Couriers within ±5pp of their tier threshold (may flip-flop):
| cid | name | tier | bucket | override | distance from threshold |
|---|---|---|---|---:|---|
| 393 | Michał K. | std+ | WHITELIST |  60.7% | beat baseline by 18.0pp vs req 15pp |
| 484 | Andrei K | std | CONDITIONAL |  61.0% | beat baseline by 17.6pp vs req 20pp |
| 517 | Gabriel Je | std | CONDITIONAL |  58.3% | beat baseline by 20.3pp vs req 20pp |

## Latent issue — operator-favorite couriers (high actual usage, low proposal volume)

Adrian's CAVEAT realized: these couriers have **low n_proposed** so proposed-based override_rate cannot diagnose them. Operator forces them on lots of orders Ziomek didn't propose.

| cid | name | tier | actual_14d | actual_not_proposed | force_share | n_proposed | bucket |
|---|---|---|---:|---:|---:|---:|---|
| 370 | Jakub OL | std+ | 126 | 115 | 91% | 73 | CONDITIONAL |
| 393 | Michał K. | std+ | 126 | 107 | 85% | 61 | WHITELIST |
| 400 | Adrian R | std+ | 112 | 106 | 95% | 19 | INSUFFICIENT_DATA |
| 123 | Bartek O. | gold | 107 | 97 | 91% | 112 | CONDITIONAL |
| 413 | Mateusz O | gold | 120 | 94 | 78% | 249 | CONDITIONAL |
| 484 | Andrei K | std | 103 | 91 | 88% | 41 | CONDITIONAL |
| 509 | Dariusz M | std+ | 87 | 78 | 90% | 25 | INSUFFICIENT_DATA |
| 179 | Gabriel | gold | 99 | 73 | 74% | 373 | CONDITIONAL |
| 508 | Michał Li | slow | 68 | 62 | 91% | 54 | CONDITIONAL |
| 457 | Adrian Cit | std+ | 72 | 56 | 78% | 45 | CONDITIONAL |
| 470 | Piotr Zaw | std | 73 | 53 | 73% | 105 | CONDITIONAL |
| 515 | Szymon P | std | 66 | 49 | 74% | 191 | CONDITIONAL |
| 503 | Gabriel J | std | 50 | 44 | 88% | 21 | INSUFFICIENT_DATA |
| 520 | Michał Rom | std | 53 | 43 | 81% | 17 | INSUFFICIENT_DATA |
| 524 | Dawid Cha | new | 46 | 43 | 93% | 6 | INSUFFICIENT_DATA |

## Implementation — AUTO_PROXIMITY_COURIER_WHITELIST flag

Proposed env value (currently 1 couriers):

```
AUTO_PROXIMITY_COURIER_WHITELIST=393
```
Gate: only allow AUTO routing when `proposed_courier_id in WHITELIST AND tier in {gold,std+}`. Empty whitelist => AUTO routing remains OFF until calibration (recommended given baseline).


## RECOMMENDATION (TL;DR for Adrian)

1. **DO NOT enable Faza 7 AUTO yet.** Baseline override 78.6% means Ziomek's proposals diverge systematically from operator choice — root cause likely lives outside per-courier quality (probably scheduling/availability/peak-hour pattern that Ziomek's model misses).
2. **Investigate operator favorites first** (latent table above): cid 370/393/400/484/509 etc. have high actual usage but Ziomek rarely proposes them. Why? Are they on shifts when Ziomek's pool excludes them? Are they preferred for certain restaurants?
3. **WHITELIST recommended starting set** (after root cause understood): 393.
4. **R6 sanity OK** — no courier shows alarming R6 breach rate, so the override is not driven by delivery quality of specific couriers — reinforces (1)/(2).
5. **Re-run after 30 more days** with richer outcome capture (assigned_first_ts, per-shift availability) before any AUTO ramp.
