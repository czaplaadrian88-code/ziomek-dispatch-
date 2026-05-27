# Drive_min calibration sprint — design (2026-05-27)

**Author:** Ziomek architect (CC SELF, design-only)
**Trigger:** Q1 v2 multi-agent audit potwierdziło `predicted_drive_min` jest systematycznie zaniżony o +16.2 min mean / +12.9 min median na 3013-row backfill set. Trust operatora cierpi → wyższy override rate → mniejsza wartość Faza 7 ramp-up. Zaadresować PRZED zwiększeniem autonomii.

**Status:** DESIGN ONLY (no production code modified). Empirical analysis from `/tmp/backfill_decisions_outcomes_v1.jsonl` (3013 rows with `outcome.assign_to_pickup_min` + `predicted_drive_min`).

**Source data quality note:** `predicted_drive_min` w backfill snapshot _w wielu wierszach_ równy `predicted_travel_min` ≠ pure `courier_pos → pickup` leg (proxy zawiera już chain + buforowanie). Analiza bias mierzy _what Ziomek printed_ vs _what really happened_ (assign→pickup wall clock). To jest dokładnie ten sam kwantitet, który steruje wertdyktem operatora i scoringem — więc kalibracja musi działać _na tym samym poziomie kompozycji_.

---

## Step 1 — Feature breakdown bias analysis

n = 3013 (rows z `outcome.assign_to_pickup_min` ≠ null i `predicted_drive_min` ≠ null).
Surowa próbka: mean Δ = +16.2 min, median Δ = +12.9 min, p25 = +2.9, p75 = +27.4, p90 = +40.1, **69.2% case'ów with Δ > 5 min**.

### 1.1 Per tier

| tier  | n     | mean   | median | p25   | p75   | p90   | under>5min |
|-------|-------|--------|--------|-------|-------|-------|------------|
| gold  | 1359  | +12.7  | +9.4   | +1.2  | +22.3 | +34.3 | 62.5%      |
| new   | 37    | +14.3  | +13.4  | +6.5  | +17.2 | +34.9 | 86.5%      |
| slow  | 90    | +18.8  | +8.1   | -0.4  | +38.8 | +52.2 | 61.1%      |
| std   | 1079  | +17.3  | +14.0  | +3.7  | +28.2 | +41.4 | 71.2%      |
| std+  | 448   | +23.7  | +23.9  | +10.5 | +34.8 | +45.4 | 85.0%      |

**Verdict:** Tier matters ale spread jest 9-24 min median (factor ~2.5x), NIE explained by `tier` alone. `std+` najgorszy — większy procentowo niż `gold` (factor 2.5x). `slow` ma wysoki mean ale niski median = ciężki ogon outlierów.

### 1.2 Per pos_source — **DOMINANT AXIS**

| pos_source              | n     | mean   | median | p25   | p75   | p90   | under>5min |
|-------------------------|-------|--------|--------|-------|-------|-------|------------|
| gps                     | 41    | +33.2  | +35.1  | +18.6 | +42.9 | +50.1 | **90.2%**  |
| last_assigned_pickup    | 317   | +32.3  | +30.9  | +23.9 | +40.4 | +48.9 | **99.1%**  |
| last_picked_up_delivery | 16    | +29.6  | +30.5  | +25.1 | +35.6 | +45.3 | 100.0%     |
| last_picked_up_pickup   | 194   | +35.2  | +34.7  | +24.6 | +46.3 | +55.6 | **99.0%**  |
| **no_gps**              | 1797  | +8.9   | **+6.5** | +0.8 | +15.5 | +26.0 | 56.3%      |
| post_wave               | 193   | +32.3  | +30.9  | +23.0 | +43.5 | +49.3 | 97.4%      |
| pre_shift               | 455   | +16.6  | +15.3  | +2.7  | +27.3 | +40.1 | 71.6%      |

**Surprise verdict:** Najlepszy source = `no_gps` (median **+6.5 min**!), najgorszy = `last_*_pickup` + `gps` + `post_wave` (median **+30-35 min**). Mechaniczna interpretacja:

- `no_gps` używa _synthetic BIALYSTOK_CENTER_ + max(15, prep) buforu — to działa! Conservative floor maskuje brak prawdziwej pozycji.
- `last_picked_up_pickup` / `last_assigned_pickup`: pozycja kuriera = **przeszły pickup** (ostatni komitowany), ale kurier od tego czasu jechał w trasę → realna pozycja prawie zawsze _dalej_ od proponowanego pickupu. F4 K2 interpolacja miała to fix'ować, ale shadow nie ON jeszcze.
- `gps` (n=41) ma p90 +50 — wskazówka że flag staleness > fresh_gps_max_age slip-through (pos_age_min ≤ 5 ale GPS not really fresh, np. parked courier).
- `post_wave` = pozycja po falie commit, ale solver liczy dystans od kotwicy bag, nie od courier_pos rzeczywistego.

### 1.3 Per peak window

| peak         | n    | mean  | median | under>5min |
|--------------|------|-------|--------|------------|
| breakfast (7-9) | 31   | +12.8 | +2.0   | 48.4%      |
| off_peak     | 1218 | +15.2 | +12.2  | 68.6%      |
| dinner_peak (18-20) | 958  | +14.4 | +9.9   | 64.6%      |
| **lunch_peak (12-14)** | 806  | +19.8 | **+19.7** | **76.3%** |

Lunch amplification confirmed (+10 min vs off_peak median). Dinner _below_ off_peak — Adrian's hypothesis o dinner peak = nieprawdziwy.

### 1.4 Per predicted_drive_min bucket (magnitude effect)

| bucket   | n   | mean  | median | under>5min |
|----------|-----|-------|--------|------------|
| **≤5**     | 499 | +35.1 | **+33.6** | **99.8%** |
| **5-10**   | 294 | +30.9 | **+30.1** | 98.3%      |
| 10-15    | 739 | +14.0 | +11.8  | 66.7%      |
| 15-20    | 424 | +10.5 | +8.3   | 63.2%      |
| 20-30    | 566 | +10.0 | +8.1   | 62.5%      |
| **>30**    | 491 | +3.3  | **+2.3** | 37.3%   |

**Killer finding:** to NIE jest "wszystkie predykcje są o 16 min za niskie". To jest "_małe predykcje są dramatycznie za niskie_, _duże predykcje są w miarę OK_". Krzywa: predicted=5 → actual ≈ 38, predicted=30 → actual ≈ 32. **Linear fit:** `actual ≈ 31.2 + 0.22 × predicted_drive_min` z R² = **0.037** (!!).

Implikacja: existing `predicted_drive_min` ma **floor problem** — zwraca <5 min gdy w rzeczywistości <5 min jest niemal niemożliwe (driver pickup obejmuje parking + entry + DWELL + handover). Floor wymaga adresacji niezależnie od reszty kalibracji.

### 1.5 Per day-of-week

| dow  | n    | mean  | median | under>5min |
|------|------|-------|--------|------------|
| Mon  | 404  | +16.8 | +15.0  | 68.3%      |
| **Tue**  | 747  | **+20.8** | **+19.8** | 77.8% |
| Wed  | 322  | +17.6 | +15.4  | 71.7%      |
| Thu  | 354  | +17.1 | +15.0  | 76.6%      |
| Fri  | 363  | +16.3 | +14.2  | 73.8%      |
| Sat  | 343  | +9.6  | +6.0   | 55.4%      |
| Sun  | 480  | +11.3 | +6.7   | 55.8%      |

Tue najgorszy (Tue lunch ≈ shopping + business). Weekend best (mniejszy ruch). Effect ~14 min weekday vs weekend (median).

### 1.6 Per district (heuristic mapping)

| district | n    | median |
|----------|------|--------|
| leśna    | 35   | +17.0  |
| centrum  | 538  | +16.1  |
| other    | 2440 | +12.4  |

Effect mały (+4 min centrum vs other median) — district nie jest dominant axis. Większość rows trafia do "other" bo heuristyka mapping restaurant→district jest słaba.

### 1.7 Per hour (Warsaw)

Maxima: 13:00 (+23.9), 15:00 (+19.9), 12:00 (+17.5), 14:00 (+18.8). Min: 9:00 (+2.0), 21:00 (+5.9), 22:00 (+3.7). Spread 22 min od godziny do godziny.

### 1.8 Tier × peak matrix (median Δ)

| tier   | off_peak     | breakfast    | lunch_peak   | dinner_peak  |
|--------|--------------|--------------|--------------|--------------|
| gold   | +7.8 (n=537) | +7.5 (n=4)   | +13.5 (n=425)| +9.0 (n=393) |
| new    | +14.1 (n=5)  | -            | +11.6 (n=16) | +14.3 (n=16) |
| slow   | +9.1 (n=22)  | -            | +0.6 (n=20)  | **+30.2 (n=48)** |
| std    | +15.3 (n=474)| -0.3 (n=4)   | **+25.3 (n=188)** | +8.9 (n=413) |
| std+   | **+23.0 (n=180)** | +2.0 (n=23) | **+30.6 (n=157)** | +13.8 (n=88) |

Najgorsze cele: `std+` lunch (+30.6 min), `std` lunch (+25.3), `slow` dinner (+30.2). Najlepsze: `gold` everything <15 min.

### 1.9 Tier × pos_source matrix (median Δ)

(skrót — najważniejsze cele kalibracji to **niekorzystne combinations**, n≥30):

| tier   | last_assigned_pickup | last_picked_up_pickup | no_gps          | post_wave        | pre_shift        |
|--------|----------------------|------------------------|-----------------|------------------|------------------|
| gold   | +28.2 (n=78)         | +36.2 (n=54)           | +6.2 (n=994)    | +34.9 (n=42)     | +13.2 (n=189)    |
| std    | +33.7 (n=102)        | +37.2 (n=71)           | +6.7 (n=604)    | +29.9 (n=92)     | +18.2 (n=182)    |
| std+   | +31.1 (n=118)        | +32.5 (n=60)           | +8.1 (n=129)    | +30.6 (n=50)     | +16.5 (n=64)     |

Pattern: stała `~+30 min systematic bias` dla wszystkich tierów × każdy stale-pos source. To jest **architektoniczny problem F4** — solver myśli że kurier startuje z kotwicy bag, ale w rzeczywistości kurier jechał od ostatniego pickupu przez "n" minut do _kolejnego_ pickupu którego my proponujemy. F4 K2 interpolacja jest właściwym fixem, ale dopóki nie jest LIVE → potrzebny offset.

### 1.10 Predicted_bucket × tier matrix (median Δ)

(skrót — najwyższe biases w `≤5` i `5-10` buckets niezależnie od tieru):

| tier  | ≤5            | 5-10          | 10-15         | 15-20         | 20-30         | >30           |
|-------|---------------|---------------|---------------|---------------|---------------|---------------|
| gold  | +34.1 (n=126) | +29.7 (n=73)  | +13.1 (n=403) | +6.3 (n=207)  | +7.2 (n=290)  | +1.2 (n=260)  |
| std   | +32.8 (n=206) | +29.0 (n=123) | +9.7 (n=227)  | +9.3 (n=156)  | +9.3 (n=198)  | +3.1 (n=169)  |
| std+  | +32.9 (n=138) | +30.9 (n=89)  | +15.9 (n=89)  | +10.4 (n=46)  | +12.1 (n=55)  | +4.0 (n=31)   |
| slow  | +49.4 (n=26)  | +31.2 (n=5)   | +2.4 (n=18)   | -0.6 (n=7)    | +5.4 (n=14)   | +0.6 (n=20)   |

**Verdict:** Bias `≤5` bucket ≈ +33 min (constant per tier!). To jest **floor signal** — Ziomek printuje `4 min` gdy w rzeczywistości assign→pickup zawsze trwa ≥15-20 min (parking + entry + DWELL handover + walk-to-car + drive 1 km). Najmniejsza efektywna `predicted_drive_min` od physical floor to ≈ 8-10 min. Linear fit per tier potwierdza:

| tier  | a (intercept) | b (slope)   | R²    | n    |
|-------|---------------|-------------|-------|------|
| gold  | +28.01        | +0.294      | 0.073 | 1359 |
| std   | +31.19        | +0.245      | 0.041 | 1079 |
| std+  | +34.73        | +0.161      | 0.016 | 448  |
| slow  | +39.09        | **-0.095**  | 0.007 | 90   |

**Intercept ~+30 min, slope ~+0.2 (powinno być +1.0!)** — Ziomek's `predicted_drive_min` is essentially decoupled from actual. The R² spread (0.01-0.07) shows _per-tier linear regression is not the answer_.

### 1.11 Offset-table candidates (table of evaluated grouping schemes)

Median |residual| po zastosowaniu per-group median offset (groups n≥30 only, otherwise no-op):

| Grouping              | |resid| med | resid med | applied | groups |
|-----------------------|--------------|-----------|---------|--------|
| baseline (no calibration) | **13.64** | +12.92 | 3013 | -    |
| tier                  | 10.62 | 0.00  | 3013 | 5      |
| peak                  | 10.76 | 0.00  | 3013 | 4      |
| pos_source            | **7.88**  | +0.05 | 2997 | 6      |
| tier + peak           | 9.68  | +0.29 | 2903 | 10     |
| **tier + pos_source** | **7.89**  | +0.54 | 2870 | 16     |
| tier + peak + pos_source | 8.72 | +2.63 | 2451 | 21    |
| tier + pred_bucket    | 8.22  | +0.13 | 2886 | 18     |
| **tier + peak + pred_bucket** | 8.27 | +1.57 | 2567 | 35  |
| district              | 11.42 | 0.00  | 3013 | 3      |
| tier + district       | 10.44 | +0.09 | 2965 | 8      |

**Key result:**

1. **`pos_source` alone** już daje 13.64 → 7.88 (-42%) i obsłuży 99.5% rows (n=2997 z 16 groups).
2. **`tier + pos_source`** dokłada tylko marginalne ulepszenie (7.88 → 7.89 = nieistotne) ale obsłuży tylko 95% rows i wymaga 16 grup zamiast 6. **NIE warto** dodawać `tier`.
3. **`tier + peak + pred_bucket`** (35 grup, granularny) daje gorszy wynik (8.27) niż czysty `pos_source` (7.88) — fragmentacja danych zjada zysk. Anti-pattern.
4. **`tier + peak + pos_source`** (21 grup) jest najgorszy (8.72) — bo `tier`+`peak` dla gold/std/std+ × peak/off_peak częściowo overlapują z `pos_source` (kurierzy `gold` częściej mają `no_gps`, std+ częściej w peak → multicollinearity).
5. Najprostsza wygrana = **pos_source-only offset table** z 6 cells.

**Top-3 najbardziej impactful features (rank by single-feature |residual| reduction):**

1. **pos_source** — reduction 13.64 → 7.88 (42%)
2. **tier** — reduction 13.64 → 10.62 (22%)
3. **predicted_bucket** — reduction implicit via floor effect, samodzielnie nie testowane (zob. 4.4 niżej)

---

## Step 2 — Calibration model alternatives

### Alternatywa A — pos_source offset table z floor guard (LIGHT, ~6h CC)

**Mechanizm:**
```
OFFSETS = {
    'no_gps':                  +6.5,
    'pre_shift':               +15.3,
    'gps':                     +35.1,
    'last_assigned_pickup':    +30.9,
    'last_picked_up_pickup':   +34.7,
    'last_picked_up_delivery': +30.5,
    'post_wave':               +30.9,
}
FLOOR_MIN = 8.0  # absolute floor; pickup z parking+DWELL+entry never <8 min

drive_min_calibrated = max(FLOOR_MIN, predicted_drive_min + OFFSETS[pos_source])
```

**Hyperparametry (znane z Step 1):** 7 offsetów + 1 floor. Można dodatkowo per-tier secondary correction (`tier + pos_source` 16 cells) ale dane Step 1.11 pokazują że to NIE pomaga (residual identyczny 7.88 vs 7.89).

**Pro:**
- Tabela `Dict[str, float]` — 5 linii kodu w `chain_eta.py` lub `dispatch_pipeline.py`.
- Najprostszy do verify w smoke (printuj kalibracja w log, diff przed/po).
- Idempotent (single transformation, no order dependency).
- Floor guard adresuje BUG-A1 (`predicted_drive_min` < 5 = nigdy realne).
- Hot-reload przez `common.py` constants — Adrian może shadow-flip flag bez restart.
- ~6h CC: design (DONE) + impl (1h) + test (2h) + shadow setup (2h) + ramp (1h).

**Con:**
- Static — nie adaptuje się gdy kurier behavior się zmienia. Wymaga okresowej re-calibration (np. monthly mass-fit).
- Nie używa `tier` × `peak` interaction (np. `std+` × `lunch_peak` zostawia +30 min residual). Ale Step 1.11 pokazał że dodanie tier+peak NIE pomaga w residual.
- Floor jako single global stała — nie per-tier (slow tier ma p25 -0.4 w bucket >30 → floor 8 wciąż OK dla long-haul).

**Per-tier extension (rejected):** Dane Step 1.11 jednoznacznie. Nie warto.

**Per-peak extension (conditional):** Można dorzucić `LUNCH_PEAK_BUMP = +5 min` na top jeżeli post-shadow data potwierdzi że lunch×no_gps residual jest gorszy niż off_peak×no_gps (Step 1.8 sugeruje +6 min difference, ale 5/+10 lunch correlate z `pos_source != no_gps` distribution shift — multicollinearity). **Defer to Faza 2.**

### Alternatywa B — feature regression (MEDIUM, ~16-20h CC)

**Mechanizm:**
```
actual_assign_to_pickup_min ~ β₀
    + β₁ · predicted_drive_min
    + Σ βᵢ · pos_source_dummies  (6 dummies, no_gps as reference)
    + Σ βⱼ · tier_dummies  (4 dummies, gold as reference)
    + Σ βₖ · peak_dummies  (3 dummies, off_peak as reference)
    + β_log · log(predicted_drive_min + 1)  [magnitude effect]
```

**Train/test:** 70/30 split na `decision_ts` (older = train). Retrain monthly via cron.

**Pro:**
- Pojedyncze regression coefficient per feature — diagnostykę można printować.
- Adaptuje się do nowych data automatycznie (monthly retrain).
- Może być zaimplementowane w pure Python z `numpy.linalg.lstsq` (już dependency).

**Con:**
- Step 1.11 pokazał empirycznie że dodawanie wielu features _NIE_ pomaga, fragmentacja danych zjada zysk. Linear regression overfittuje na wielu dummy cells.
- R² = 0.037 dla `actual ~ a + b·predicted` — sygnał że linear model NIE captures the non-linearity (floor effect). Dodawanie dummies podniesie R² do ~0.2-0.3 ale residual nie spadnie więcej niż offset table.
- Wymaga train/test infrastructure + monthly retrain cron + version tracking modeli — duży narzut operacyjny.
- Trudniej debug — coefficient interactions niejasne dla operatora.
- Black-box dla Adriana ("dlaczego Ziomek printuje 22 min?") vs offset table ("bo pos_source=`last_picked_up_pickup` → +35 offset").

**Quality-per-effort:** Niska. R² fundamentalnie niski (data ma noise + missing causal features takie jak actual GPS drift, traffic real-time, parking time). Regression NIE poprawi residual nad offset table więcej niż ~1 min.

### Alternatywa C — LGBM gradient boosting (HEAVY, ~28-32h CC)

**Mechanizm:**
```
features = [
    predicted_drive_min, pos_source, tier, peak_window, dow, hour_warsaw,
    score_margin, pool_feasible, pool_total, restaurant_id_hash,
    pickup_district, courier_district_last_known, czasowka, best_effort,
    pos_age_min, shift_end_edge,
]
model = lightgbm.train(...)
drive_min_calibrated = model.predict(features)
```

Integracja z istniejącym LGBM v1.1 pipeline (`tools/lgbm_train_v1.py`).

**Pro:**
- Najwyższa accuracy teoretycznie. Niemonotoniczne interakcje (feasibility×pos_source) automatycznie capture.
- Już istnieje LGBM v1.1 infrastructure (NDCG@5=0.852, pa=88.45%).
- Cross-validation framework w miejscu.

**Con:**
- ~30h CC — nieproporcjonalnie wysoki do problemu.
- Black box vs Adrian's mental model. Operator NIE może łatwo verify "dlaczego +X". Naruszenie zasady Z2 (jakość = predictable behavior, NIE absolute accuracy).
- Latency overhead — model.predict per candidate = ~5-10ms × 10 candidates × 200 propozycji/dzień. Ok, ale dodaje tail.
- Wymaga inference time GPU/CPU lighter than R² gain.
- Cross-coupling z LGBM v1.1 score pipeline — bug w drive_min model → cascade na top1 selection.

**Quality-per-effort:** Marginalna. Step 1 fundamental finding: `pos_source` alone explains majority of variance. LGBM trees będą głównie split'ować na `pos_source` → end up emulating offset table z dodatkowym noise.

---

## Rekomendacja: **Alternatywa A — pos_source offset table + floor guard**

**Uzasadnienie:**

1. **Empirical data Step 1.11 jednoznacznie:** offset table z `pos_source` alone redukuje median |residual| z 13.64 → 7.88 (42%). Dodawanie features (tier, peak, district, pred_bucket) NIE poprawia. Inżynieryjna zasada YAGNI.

2. **Quality-per-effort:** Alt A daje 42% reduction (target z briefu = 60%, ale baseline 13.64 nie 16.2 — relatywnie target = 60% × 13.64 = ~5.5 min). Alt A osiąga 7.88. Aby przekroczyć 5.5 wymaga floor guard (poniżej).

3. **Z2 / Z3 zgodność:** Najprostszy mechanizm. Operator zrozumie. Predictable. Hot-reloadable. Rollback `flag = False`. Buduje na lata (per Adrian's "najwyższa jakość = najprostsze rozwiązanie które działa").

4. **Floor guard jako bonus:** Step 1.10 pokazał że `predicted ≤ 5 min` ma stały +33 min bias. Floor `drive_min_calibrated = max(8, ...)` adresuje to bez dodatkowej kompleksy regresji. Każdy pickup obejmuje fizycznie nieusuwalny parking + DWELL + handover ≥ 5-8 min — to nie jest tunable, to physical floor.

5. **Future-proof:** Gdy F4 K2 interpolacja (`pos_source=last_picked_up_interp`) zostanie zwalidowana i włączona, _ten sam_ offset table może mieć cell dla nowego pos_source — re-calibration miesięczna podchwytuje to automatycznie.

**Rezygnacja z Alt B/C:** R² = 0.037 jest fundamentalnym sygnałem że dane mają _zbyt mało causal signal_ żeby regression/LGBM dał istotną przewagę nad offset. To NIE jest noise-bound problem — to **architektoniczny problem F4** (kurier startuje z anchora ≠ rzeczywista pozycja). Prawdziwy fix = F4 K2 interpolacja LIVE, NIE coraz większe modele.

---

## Step 3 — Integration plan

### 3.1 Code touch points (READ-ONLY analysis — produkcja NIE modyfikowana)

**Plik:** `dispatch_v2/chain_eta.py`

- **`safe_drive` (line 87)** — punkt wejścia OSRM. _Calibration NIE ma być tu_ — chain_eta odpowiada za realistic chain modeling, calibration adresuje different layer.
- **Po Case A (line 152)** + **po Case B final hop (line 231)** — gdzie `drive_to_proposal` i `drive_proposal` są computed. **NIE modify tutaj** — chain_eta returns ChainETAResult przed feasibility check.

**Plik:** `dispatch_v2/dispatch_pipeline.py` (~line 1586-1614)

Tutaj wywoływany `compute_chain_eta` z resultem `r07_chain_eta_utc`. **Calibration hook = tutaj**, post-chain_eta, przed `check_feasibility_v2`:

```
# DESIGN ONLY — example positioning, NOT production code
if C.ENABLE_DRIVE_MIN_CALIBRATION_V2:
    pos_src = getattr(cs, "pos_source", None) or "no_gps"
    offset = C.DRIVE_MIN_CALIBRATION_OFFSETS.get(pos_src, 0.0)
    drive_min_raw = predicted_drive_min  # from earlier compute
    drive_min_calibrated = max(C.DRIVE_MIN_CALIBRATION_FLOOR, drive_min_raw + offset)
    # Wire calibrated value into proposal_arrival / scoring inputs
```

**Plik:** `dispatch_v2/auto_proximity_classifier.py` (line ~30-100)

`classify_auto_route(ctx, thresholds)` używa `predicted_travel_min` i `predicted_drive_min` w T1/T2/T3 thresholds. **Calibration MUST apply tutaj również** żeby AUTO/ACK/ALERT bramka używała rzeczywistego ETA.

**Plik:** `dispatch_v2/common.py` (na końcu, near other flags ~line 1750+)

Dodać:
```
# DRIVE_MIN CALIBRATION V2 (per pos_source offset + floor)
ENABLE_DRIVE_MIN_CALIBRATION_V2 = False  # default OFF, shadow-only first
ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW = True  # always log diff, both rails
DRIVE_MIN_CALIBRATION_OFFSETS = {
    "no_gps": 6.5,
    "pre_shift": 15.3,
    "gps": 35.1,
    "last_assigned_pickup": 30.9,
    "last_picked_up_pickup": 34.7,
    "last_picked_up_delivery": 30.5,
    "post_wave": 30.9,
    "last_picked_up_interp": 10.0,  # placeholder dla post-F4-K2
}
DRIVE_MIN_CALIBRATION_FLOOR = 8.0  # min minutes; absolute floor
DRIVE_MIN_CALIBRATION_VERSION = "v1_2026-05-27"  # tag dla shadow log audit
```

**Plik:** `dispatch_v2/shadow_dispatcher.py` (line 374, 611)

Dodać do `_serialize_result` i candidate serialization 2 nowe pola:
```
"drive_min_raw": <pre-calibration value>,
"drive_min_calibrated": <post-calibration value>,
"calibration_offset_applied": <float>,
"calibration_version": "v1_2026-05-27",
```

### 3.2 Feature flag layout

| Flag | Default | Effect |
|---|---|---|
| `ENABLE_DRIVE_MIN_CALIBRATION_V2` | False | Wire kalibrowanego value do scoring/feasibility/auto-proximity |
| `ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW` | True | Log raw vs calibrated do `drive_min_calibration_log_v2.jsonl` (zero side-effect) |

Hot-reload via `flags.json` (existing infrastructure). Restart NIE wymagany do flip.

### 3.3 Shadow phase (7d minimum)

**Log destination:** `/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl`

**Per-row schema:**
```json
{
  "ts": "2026-05-28T12:34:56+00:00",
  "order_id": "...",
  "courier_id": "...",
  "tier": "std+",
  "pos_source": "last_picked_up_pickup",
  "predicted_drive_min_raw": 18.4,
  "predicted_drive_min_calibrated": 53.1,
  "offset_applied": 34.7,
  "floor_hit": false,
  "calibration_version": "v1_2026-05-27",
  "decision_path": "shadow"
}
```

Plus _post-fact_ post-outcome (gdy `assign_to_pickup_min` znany 30-90 min później): worker dosypuje `actual_assign_to_pickup_min` do tej samej linii (lookup by `order_id + courier_id`). Daily aggregation cron computes:

- median |raw - actual|
- median |calibrated - actual|
- % reduction
- Per-pos_source / per-tier breakdown

**Daily aggregation cron:** `dispatch-drive-min-calib-aggregate.timer` (1x dzień 06:00 Warsaw, oneshot, output → `/tmp/drive_min_calibration_kpi_<date>.md`, append summary do Telegram daily digest).

### 3.4 Roll-out triggers

| Day | Action |
|---|---|
| D0 | Deploy code, flag SHADOW=True, MAIN=False. Verify shadow log writes. |
| D1-D7 | Daily KPI digest. Verify median |calibrated-actual| < median |raw-actual| consistently. |
| D7 | Decision gate Adrian: jeśli reduction ≥ 30%, flip MAIN=True ON. Else iterate offsets. |
| D7-D14 | Live, monitor override rate (Telegram propose→ALERT/KOORD rate). Expect modest decrease. |
| D14+ | Sprint zamknięty, calibration jest "tło". |

**Rollback (instant, < 5s):** `python3 -c "..."; flags.json['ENABLE_DRIVE_MIN_CALIBRATION_V2'] = False`. Worker hot-reload na next decision call.

### 3.5 Re-calibration cron (post-LIVE)

`dispatch-drive-min-recalibrate.timer` — monthly (1st każdego miesiąca, 04:00 Warsaw). Re-runs `/tmp/drive_min_bias_analysis.py` styled aggregation na latest 30-day window, proposes new `DRIVE_MIN_CALIBRATION_OFFSETS` dict, writes to `/tmp/drive_min_calibration_proposed_<date>.json`, sends Telegram alert. Adrian manual approval do code commit.

---

## Step 4 — Validation framework

### 4.1 Primary KPI

- **Median |predicted_calibrated − actual|** — target **<5 min** (baseline 13.64 → 60% reduction = 5.5 min, target 5 = 63% reduction stretched).

Tabular daily report sample (mock):
```
=== Drive-min calibration KPI 2026-05-28 ===
n_decisions:                     245
n_with_outcome:                  198 (80.8%)
median |raw - actual|:           13.4 min
median |calibrated - actual|:    7.9 min
% reduction:                     41.0%
Floor hits (calibrated == 8):    2
Calibration absent (pos_source missing): 0

Per pos_source:
  no_gps           n=118  |raw|=6.4  |calibrated|=4.7  red=26.6%
  last_picked_up_pickup n=23  |raw|=33.8  |calibrated|=6.2  red=81.7%
  ...
```

### 4.2 Secondary KPIs

- **Per-tier KPI breakdown** — verify żaden tier nie ma worse residual po calibration vs raw.
- **Per-pos_source KPI** — verify offset values są empirically zwalidowane (nie tylko backfill, ale forward-looking).
- **Floor hit rate** — `% calibrated == FLOOR` — jeżeli >20% rows, podnieść floor (signal że predicted_drive_min zbyt często < 5).
- **Override rate trend** — `% propozycji gdzie operator zmienił courier`. Hipoteza: spadek o 5-10pp po calibration LIVE (operator wierzy ETA → akceptuje top1 częściej).

### 4.3 Alarms (do Telegrama)

- **Weekly KPI degrades >2 min vs poprzedni tydzień** → ALERT (gradient detect: model offsets stale).
- **Floor hit rate >25%** → ALERT (predicted_drive_min systematycznie za niskie nawet po calibration — re-investigate chain_eta).
- **Per-pos_source absolute |residual| >12 min for n≥50 daily** → ALERT (offset dla tego pos_source not converging).

### 4.4 Pre-flight smoke (przed shadow ON)

Replay `tools/sequential_replay.py` na 24h window z flagą SHADOW=True. Verify:

- 100% rows w shadow log mają `predicted_drive_min_raw + offset == predicted_drive_min_calibrated` ALBO floor hit.
- Zero exception traces w log.
- KPI median |calibrated-actual| ~7.9 (matching backfill).

---

## Step 5 — Risk assessment

### 5.1 Side effects — route_simulator_v2 / scoring

**Risk:** Calibrated value pójdzie do `r6_max_bag_time`, `r5_pickup_spread`, `assign_to_pickup_min` w scoring → top1 candidate może się zmienić. Kandydaci z `last_picked_up_pickup` (długi offset +34.7) zostaną silniej karani — _to jest exactly intent_.

**Mitigation:** Shadow phase 7d compare ranking top1 pre-calibration vs post-calibration. Akceptowalna szybkość divergence: <15% propozycji ma inny top1.

**Verify:** Adrian Telegram digest podaje per-day top1 divergence rate. Jeżeli >25% → halt, re-evaluate scoring weights.

### 5.2 R6 max_bag_time cascade

**Risk:** R6 = max(carry_time, future_to_pickup, ...) — jeżeli `future_to_pickup` rośnie po calibration, R6 max może triggerować KOORD (>35 min hard cap) gdzie pre-calibration był feasible.

**Mitigation:** Shadow log tracking specifically R6 outcome pre/post calibration. Hypothesis: KOORD trigger rate wzrośnie 5-10pp dla `last_picked_up_pickup` cases — _to jest correct behavior_ (system był overconfident).

**Adrian guard:** Hard threshold `R6_HARD_BAG_MIN=35` może wymagać re-tune do 38-40 _temporarily_ podczas sprint Faza 1, żeby uniknąć throughput cliff. Decision: post-shadow week 1.

### 5.3 Replay tools backwards compat

**Risk:** `replay_failed.py`, `sequential_replay.py`, `gate_b_tomtom_poc` historical decisions używały raw `predicted_drive_min`. Post-calibration replay produces _different_ predicted values → kompar dla `1 day before` window się rozjeżdża.

**Mitigation:** Replay tools mają parametr `--calibration-version=raw|v1`. Default `raw` zachowuje backward-compat. New analyses używają `v1`.

### 5.4 Drift z F4 K2 interpolation

**Risk:** Gdy F4 K2 (`pos_source=last_picked_up_interp`) zostanie LIVE, `pos_source` distribution shift. Aktualne offset values są empiryczne dla pre-F4-K2 świat.

**Mitigation:** `DRIVE_MIN_CALIBRATION_OFFSETS["last_picked_up_interp"] = 10.0` jako _placeholder_ (oczekuję że interp redukuje stale-position bias do ~10 min, vs +34.7 raw). Po F4 K2 LIVE 1 month → re-calibrate offsets via cron.

### 5.5 Shadow log volume

**Risk:** `drive_min_calibration_log_v2.jsonl` rośnie ~250 entries/dzień × 100 bytes = 25 KB/dzień. 30 dni = 750 KB. Akceptowalne. Z dyspatchem propozycji × candidates rate (typically ~10 candidates/decision) = 2500 entries/dzień × 100 = 250 KB/dzień, 7.5 MB/mies. Wciąż OK.

**Mitigation:** Logrotate weekly, retention 30 dni. Daily aggregate output → `/tmp/` (cron managed retention 90 dni).

### 5.6 Backwards compat dla aud datasets (LGBM v1.1)

**Risk:** LGBM v1.1 score model trained na _raw_ `predicted_drive_min` jako feature. Calibration zmienia distribution feature → degradation NDCG@5.

**Mitigation:** _Disjoint feature_ — dodać `drive_min_calibrated` jako _nowy_ feature, zachować `drive_min_raw`. LGBM v2 może (w przyszłości) consume oba. V1.1 niezmieniony.

---

## Sprint estimate

| Etap | CC time | Adrian time | Calendar days |
|---|---|---|---|
| Design (DONE) | 2-3h | 30 min ACK | 1 |
| Step 1 deep dive (DONE) | 1h | 0 (review report) | - |
| Impl: common.py constants + offsets | 0.5h | - | - |
| Impl: dispatch_pipeline.py hook + serialize | 1.5h | - | - |
| Impl: auto_proximity_classifier.py hook | 0.5h | - | - |
| Impl: shadow_dispatcher.py log fields | 0.5h | - | - |
| Tests (per-pos_source unit + integration smoke) | 2h | - | - |
| Aggregation cron (oneshot + KPI report) | 1h | - | - |
| Shadow phase observability | 0h (passive) | 5 min/day review | 7 |
| Decision gate Adrian + flip MAIN ON | - | 30 min | 1 |
| Live monitoring + R6 cascade observation | 1h | 15 min/day | 7 |
| Re-calibration cron + Telegram alert | 1.5h | - | - |
| **TOTAL** | **~8.5h CC** | **~5h Adrian (review + ACK gates)** | **~16 days** |

CC time is **~8.5h** total = jedna ciężka sesja + 2 lekkie sesje (post-shadow tune + post-LIVE tune).

Calendar: D0 deploy → D7 flip decision → D14 LIVE stable → D16 sprint close (re-calibration cron LIVE).

---

## Pre-sprint validation (co empirycznie sprawdzić przed startem)

1. **Verify backfill set freshness:** Last decision_ts powinien być ≤ 7 dni wstecz. Old data = stale offsets. Status: `/tmp/backfill_decisions_outcomes_v1.jsonl` ostatnia `decision_ts` = (sprawdzić). Jeżeli > 14 dni, refresh dataset przez nightly backfill rerun.

2. **Verify pos_source distribution stability:** Run Step 1 analysis na _ostatnie 7 dni_ (subset) vs _wszystkie 3013_ — czy `pos_source` distribution oraz median Δ per group są stable? Jeżeli `no_gps` udział spadł z 60% do 40% przez ostatni miesiąc (np. dzięki upowszechnieniu apki kuriera), offsets zwalidowane na full set będą stale.

3. **Verify pos_source values po stronie kodu:** `cs.pos_source` zwracane przez `courier_resolver.py` MUSI być z range `{"no_gps", "pre_shift", "gps", "last_assigned_pickup", "last_picked_up_pickup", "last_picked_up_delivery", "post_wave"}` — żaden nieoczekiwany value. Grep `pos_source =` w `courier_resolver.py` zwalidować.

4. **Verify Faza 7 timeline:** Faza 7 ramp-up nie startuje przed 2026-06-07 (replay calibration window 7-14d post-26.05 BUG E hotfix). Sprint Drive_min ma 14 dni → start NIE PÓŹNIEJ niż 2026-05-28 żeby zamknąć przed ramp.

5. **Sandbox: verify F4 K2 status:** `ENABLE_F4_COURIER_POS_INTERP` w common.py = ? Jeżeli już ON shadow, distribution `last_picked_up_interp` istnieje w newer rows backfill — wymagana subgrupa analizy.

6. **Disk check:** `/root/.openclaw/workspace/dispatch_state/` ma >100 MB wolnego dla shadow log. (Free check: `df -h /root`).

7. **Lekcja #149 verify:** Nowa serializacja `drive_min_raw/calibrated` MUSI być w `shadow_dispatcher._serialize_result` (top-level), NIE attached jako in-memory attribute do PipelineResult. Empirically Adrian złapał ten landmine 27.05 z `commit_divergence_redirect`.

---

## Rollback procedury (per-flag, hot-reload)

```
# Soft (5s, no restart):
python3 -c "
import json, tempfile, os
p='/root/.openclaw/workspace/scripts/flags.json'
d=json.load(open(p))
d['ENABLE_DRIVE_MIN_CALIBRATION_V2']=False
fd,t=tempfile.mkstemp(dir=os.path.dirname(p))
open(fd,'w').write(json.dumps(d, indent=2, ensure_ascii=False))
os.replace(t,p)
"
```

Shadow logging zostaje ON (`ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW=True`) — fail-safe diagnostyka dla post-mortem.

**Hard (git revert):**
```
cd /root/.openclaw/workspace/scripts/dispatch_v2
git revert <calibration-sprint-tag> --no-edit
sudo systemctl restart dispatch-shadow dispatch-panel-watcher
# dispatch-telegram NIE restart bez ACK Adrian
```

---

## Lekcje candidate (post-sprint, do MEMORY)

- **#150** (lessons.md candidate): Multi-feature offset tables NIE są lepsze niż single-feature offset table gdy R² < 0.1 (low signal-to-noise). Empirical evaluation MUSI poprzedzać feature engineering — fragment podzielony zjada zysk.
- **#151** (feedback_rules.md candidate): Calibration kotwiczy się na _actual outcome_, NIE na engineering intuition. Jeżeli mental model mówi "regression powinno działać lepiej" a R² = 0.04, model ma rację (model = data).

---

**END OF DESIGN**
