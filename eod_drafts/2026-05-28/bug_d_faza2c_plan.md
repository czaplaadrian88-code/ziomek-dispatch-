# BUG-D Faza 2c — Empirical Per-Bin Validation Plan

**Status:** PLAN (planned execution ~04.06 po ≥7d shadow data)
**Author:** Claude (sesja 28.05)
**Pre-requisites:** Faza 1+2a+2b LIVE od 06:29-08:02 UTC 28.05 (commits 23c940f / 2f57c49 / b6acbd7) + #21 Opcja C enrichment cron LIVE od 07:05 UTC 28.05 (commit d8eb749)
**Effort estimate:** ~2-3h implementacja + 1h smoke + decision

## Cel

Empirycznie zwalidować boost values w `V326_OSRM_DISTANCE_BIN_BOOST_PEAK`:
```python
V326_OSRM_DISTANCE_BIN_BOOST_PEAK = (
    (2.0, 1.0),        # <2 km: +1.0
    (5.0, 0.4),        # 2-5 km: +0.4
    (float("inf"), -0.15),  # >=5 km: -0.15
)
```

Source values pochodzą z TomTom sample n=8 (2026-05-26 measurements). 7d shadow data dostarcza n~200-400 realnych OSRM call'i per bin → statystyczna confidence dla każdego boost value.

## Cross-cutting dependencies

Faza 2c **NIE jest standalone** — wymaga:

1. **#21 Opcja C enriched.jsonl ground truth** (commit d8eb749) — dostarcza `actual.assign_to_pickup_min` ground truth dla per-route validation. Join shadow_decisions.jsonl ↔ enriched.jsonl po `order_id`.
2. **BUG-D Faza 2b serialization** (commit b6acbd7) — `traffic_v2_shadow_route` field z **full per-leg breakdown** (per-leg `distance_km`, `raw_min`, `v1_mult`, `v2_mult`, `bin`).
3. **Override filter MANDATORY** — Day 7 finding 28.05: 78.6% baseline override inflates bias. Tylko `kurier_overridden=False` records reprezentują REAL prediction quality.

## Architektura tool

### `tools/analyze_traffic_v2_shadow.py` (NEW, ~250-300 LOC)

```python
"""BUG-D Faza 2c — empirical per-bin v2 multiplier validation.

Joinuje shadow_decisions.jsonl (traffic_v2_shadow_route per-leg breakdown
z Faza 2b) z drive_min_enriched.jsonl (actual outcomes z #21 Opcja C).
Per-leg actual estimate dystrybuuje całkowity actual_assign_to_pickup_min
proporcjonalnie do per-leg raw_min. Per-bin aggregate z confidence intervals.

CLI:
  python3 -m dispatch_v2.tools.analyze_traffic_v2_shadow [--days N] [--accepted-only]
"""
```

### Pipeline

```
shadow_decisions.jsonl                drive_min_enriched.jsonl
  ↓ (filter: ts>=cutoff, best != None,                     ↓ (filter: kurier_overridden=False)
     traffic_v2_shadow_route != None)
                ↓                                          ↓
       Inner join po order_id  ←──────────────────────────┘
                ↓
       Per record: 1 route → N legs (per-leg breakdown)
                ↓
       Per-leg validation:
       - per_leg_raw_min × actual_assign_to_pickup_min / sum_route_raw_min
         = empirical actual per leg (proportional distribution)
       - bin = leg["bin"] (short/medium/long)
       - boost_predicted = leg["v2_mult"] - leg["v1_mult"]
       - boost_actual = (actual_per_leg / raw_min) - v1_mult
       - bin_residual = boost_actual - boost_predicted
                ↓
       Per-bin aggregate:
       - n (sample size)
       - median residual + mean
       - 95% confidence interval (bootstrap or t-distribution)
       - recommended boost adjustment
```

### Output report

```markdown
# BUG-D Faza 2c — Empirical Validation Report

Generated: <timestamp>
Window: <N> days, source: <shadow_log + enriched_log>

## Sample sizes
- Records joined (after override filter): N
- Total legs: K
- Per-bin n: short=X, medium=Y, long=Z

## Per-bin validation table

| bin | n | current_boost | empirical_median_boost | 95% CI | recommendation |
|---|---|---|---|---|---|
| short (<2km) | 213 | +1.0 | +0.94 | [+0.78, +1.10] | KEEP (+1.0 OK, CI overlap) |
| medium (2-5km) | 287 | +0.4 | +0.18 | [+0.05, +0.31] | REDUCE to +0.2 (current overestimates) |
| long (≥5km) | 142 | -0.15 | -0.08 | [-0.18, +0.02] | KEEP or set to -0.1 (CI overlap 0) |

## Decision tree

(see plan markdown)
```

## Statistical methodology

### Why proportional distribution of actual per leg?

`actual_assign_to_pickup_min` to **total route time** (multi-leg). Per-leg ground truth NIE jest direct measurable (audit_log ma tylko ASSIGNED/PICKED_UP/DELIVERED — brak per-stop timestamps).

**Założenie**: actual time distributed proportionally to predicted raw_min per leg. To **best available proxy**:

```
predicted_total_raw = sum(leg.raw_min for leg in legs)
actual_total = actual_assign_to_pickup_min  # ground truth z enrichment

per leg:
  actual_leg_min = leg.raw_min * actual_total / predicted_total_raw
  empirical_per_leg_mult = actual_leg_min / leg.raw_min = actual_total / predicted_total_raw
```

**Caveat**: jednorodny multiplier na cały route — NIE rozróżnia legów. To **upper bound dla statistical power** ale **lower bound dla bias resolution per-leg**.

### Confidence intervals

Bootstrap (B=1000 resamples per bin) lub t-distribution dla median. Preferred bootstrap (no normality assumption).

```python
def bootstrap_ci(values, n_boot=1000, ci=0.95):
    n = len(values)
    boot_medians = [median(random.choices(values, k=n)) for _ in range(n_boot)]
    boot_medians.sort()
    lo_idx = int((1-ci)/2 * n_boot)
    hi_idx = int((1+ci)/2 * n_boot)
    return boot_medians[lo_idx], boot_medians[hi_idx]
```

### Minimum n per bin

**n ≥ 50** dla stat significance (rule of thumb). Bootstrap CI width inversely proportional to √n. n=50 daje CI width ~30% sample range; n=200 daje ~15%.

Z baseline 28.05 24h: ~248 records → ~50-80 records po override filter (~21.4%). 7d window: ~350-500 accepted records. Per-bin: short ~150, medium ~200, long ~100 (rough estimate, assumes 30-40% short, 40-50% medium, 20-30% long bins per typical route).

## Decision tree (per-bin)

Per each bin (short/medium/long):

```
if CI doesn't overlap current boost value:
    if |empirical - current| > 0.3:
        RECOMMEND: change boost to empirical_median ± rounded
    elif |empirical - current| > 0.1:
        RECOMMEND: small adjustment (round to nearest 0.1)
    else:
        KEEP current (within tolerance)
elif n < 50:
    DEFER decision (collect more data)
else:
    KEEP current (CI overlaps, no statistical evidence for change)
```

## Flag flip decision

Po per-bin recommendations:

**Scenario A: All 3 bins CI overlap current values OR small adjustments**
→ Update `V326_OSRM_DISTANCE_BIN_BOOST_PEAK` z rekomendacjami + flip `ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST=1`. Restart shadow + panel-watcher off-peak. 7d monitor outliers / R6 breach.

**Scenario B: ≥1 bin shows big shift (>0.3) without good CI overlap**
→ Update boost values + **NIE flip MAIN=true od razu**. Re-run Faza 2c po +3d w shadow → confirm stability. Then flip.

**Scenario C: n<50 dla ≥1 bin**
→ Defer flip. Pożyje sygnałów +1-2 tygodnie. Re-run.

**Scenario D: Empirical signal sugeruje że per-bin model NIE jest właściwy**
→ Big rethink. Może per-distance-AND-per-hour table. Możliwe że route geometry (district, congestion) matter więcej niż distance bin.

## Implementation steps

1. **`tools/analyze_traffic_v2_shadow.py`** (~250 LOC):
   - argparse: `--days N` (default 7), `--accepted-only` (default True), `--out PATH`
   - Read shadow_decisions.jsonl + drive_min_enriched.jsonl
   - Inner join po order_id (memory dict)
   - Filter `kurier_overridden=False` jeśli flag
   - Per-leg proportional distribution
   - Per-bin aggregate + bootstrap CI
   - Markdown report

2. **`tests/test_analyze_traffic_v2_shadow.py`** (~150 LOC):
   - Join correctness (records bez match → skip)
   - Proportional distribution math
   - Bootstrap CI deterministic z fixed seed
   - Per-bin classification (boundary cases)
   - Override filter
   - Empty data → report z warning

3. **Smoke run**:
   ```bash
   cd /root/.openclaw/workspace/scripts
   /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.analyze_traffic_v2_shadow \
     --days 7 --accepted-only --out /tmp/bug_d_faza2c_report_<date>.md
   ```

4. **Commit + tag** `bug-d-faza2c-empirical-validation-<date>`. NO push automatically — Adrian ACK po review report.

5. **Decision** per scenarios A/B/C/D (above). Update `common.py` z new boost values (jeśli applicable). Update tech_debt #BUG-D status.

## Edge cases

- **traffic_v2_shadow_route == None** — records pre-Faza 2b deploy (06:29 UTC 28.05). Skip (no per-leg data).
- **Route z 0 legs** (np. early return paths) — skip.
- **actual_assign_to_pickup_min negative** (clock skew?) — skip + warn.
- **Bin "none"** (distance_km missing) — skip (only short/medium/long count for validation).
- **Override filter dramatycznie reduces n**: jeśli accepted-only ma <30/bin, run też wide (`--no-accepted-filter`) i zgłoś jak mixed signal.

## Cross-effect z Sprint 1 calibration

**Sprint 1 calibration values DOTYKAJĄ predicted travel_min** (i.e., `_v327_eval_courier` może adjustować predicted). Jeśli Sprint 1 flip MAIN=true PRZED Faza 2c:
- predicted_travel_min ↑ (over-corrected)
- actual / predicted ratio ↓
- v2 boost recommendations będą **artificially niższe**

**Sequencing recommended**:
1. **NIE flipuj Sprint 1 MAIN=true PRZED Faza 2c** (Sprint 1 OVERCORRECT gps confirmed 28.05)
2. Faza 2c validation w shadow mode (Sprint 1 OFF + v2 OFF default)
3. Jeśli scenarios A → flip v2 ON, Sprint 1 nadal OFF
4. Sprint 1 calibration fix (per-pos thresholds) — separate sprint po Faza 2c

## Cross-ref

- BUG-D Faza 1: commit `23c940f`, tag `bug-d-distance-bin-shadow-2026-05-28`
- BUG-D Faza 2a: commit `2f57c49`, tag `bug-d-faza2a-stats-tool-2026-05-28`
- BUG-D Faza 2b: commit `b6acbd7`, tag `bug-d-faza2b-shadow-serialization-2026-05-28`
- #21 Opcja C: commit `d8eb749`, tag `td21-opcja-c-empirical-bias-2026-05-28` (enrichment cron LIVE 07:05 UTC)
- TomTom sample: `eod_drafts/2026-05-26/measurements.md` (n=8 baseline)
- Sprint plan: `eod_drafts/2026-05-26/SPRINT_PLAN_geometry_fairness_bugs.md` (BUG D rozdział)
- Lekcja #80: consumer audit przy każdej metryce (audit serialization 2b już DONE)
- Sprint 1 OVERCORRECT discovery: `sprint_timeline.md` ARCHIVE 28.05 KEY FINDING

## Day 7 (03.06) integration

Routine `trig_01VJtAdwE7PWEeeQs4pheJ78` Sekcja 2 (Sprint 1 flip decision) i Faza 2c są **niezależne** ale **collaborują**:
- Sprint 1 decision PRZED Faza 2c (NO flip jeśli gps overcorrect persists)
- Faza 2c validation może się rozpocząć **niezależnie** od Sprint 1 decision — boost values nie zależą od Sprint 1 (Sprint 1 dotyka predicted_travel_min, Faza 2c dotyka osrm.duration_min)

**Day 7 execution order**:
1. LATENT KPI verification (Sekcja 1 routine)
2. Sprint 1 calibration empirical bias check (Sekcja 2 routine)
3. **Faza 2c run** (jeśli ≥7d shadow data dostępne)
4. Sprint 2.1 KK + BUG-C-THRESHOLD + #33 (Sekcje 3-5)
5. Decision tree consolidated

## Long-term considerations (BUG-D Faza 3?)

Jeśli scenarios D (per-bin model insufficient):
- **Per-district-cluster correction** — Białystok ma znane congestion patterns per dzielnica/route. Może osobny table per geographic cluster + distance bin.
- **Per-restaurant baseline** — niektóre restauracje są w korkach (centrum), inne na obrzeżach. R-Klasterowa może mieć osobne baselines.
- **Time-of-day × distance interaction** — peak hour amplifies short urban segments more than long inter-district. Może 2D table.
- **Machine learning baseline** — LGBM dla traffic prediction zamiast static table. Już mamy LGBM_SHADOW infra.

Te są **deferred do Q3 2026** — najpierw Faza 2c empirically validate na current static model.
