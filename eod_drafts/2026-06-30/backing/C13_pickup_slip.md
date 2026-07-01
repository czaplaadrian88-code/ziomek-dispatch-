# C13 — pickup_slip / pickup_lateness ORACLE (lane C, RUNTIME-ORACLE)

**Agent:** C13-pickup-slip · **Lane:** C (runtime-oracle, C9/C11) · **READ-ONLY** · sesja tmux 2, 2026-06-30 ~17:40 UTC.
**Cel:** NIE czytać samego kodu — ODPALIĆ/ODCZYTAĆ przyrząd na realnej próbie i policzyć prawdę DRUGĄ, niezależną metodą.
**Zakres:** `pickup_slip_monitor` (#2, daily 22:30) — głównie; `pickup_lateness_shadow` (5min) — charakterystyka. Karmi FLIP load-aware buforu ETA (review 04.07).
**Numery linii re-grepowane świeżo (nie z seed).** `delivered_at`/`picked_up_at` = **prawda-PRZYCISKOWA** → znakowane proxy-certified vs ground-truth.

---

## 0. METODA DRUGIEJ PRAWDY (co odpaliłem, nie deklaracja)
1. **Niezależna re-implementacja** `collect()`/`summarize()` (NIE import żywego toola, własny `c13_oracle.py` w scratchpad) na ŚWIEŻYM `eta_calibration_log.jsonl` + join `pool_feasible` z `shadow_decisions.jsonl`.
2. **Krzyż z żywym toolem `--dry`** (read-only, ZERO zapisu — mtime OUT bez zmian) ×2 → determinizm + dowód że re-impl == logika toola.
3. **Re-recompute metryki** `eta_error_min` z surowych stempli: `delivered_at`[Warsaw→UTC −2h CEST] − `predicted_delivered_at`[UTC] na 3765 rekordach.
4. **Join GROUND-TRUTH** `gps_delivery_truth.jsonl` (fizyczny przyjazd GPS) per komórka load×bag → czy gradient obciążenia przeżywa na prawdzie fizycznej.
5. **Proxy-join `pickup_lateness_shadow`** distinct flagged orders → realny `picked_up_at` (precyzja/magnituda predykcji).
6. **Kwantyfikacja backfill** (eta_cal dorastа) + **retencja rolling shadow** vs okno review.
Skrypt: `/tmp/claude-0/.../scratchpad/c13_oracle.py` (read-only, output do scratchpad). Każda liczba odtwarzalna ≥2×.

---

## 1. WERDYKT GŁÓWNY — `pickup_slip_monitor` = **VALIDATED (proxy-certified)**

### 1a. Re-impl == tool (logika wierna)
Żywy tool `--dry --days 3` (PASS A == PASS B, identyczne) vs moja niezależna re-impl (CHECK3) — **różnica 1 rekord** (nowy wiersz eta_cal dopisany między uruchomieniami 17:37↔17:38):

| segment | tool `--dry` | moja re-impl | n |
|---|---|---|---|
| ciasno solo | median **29.0** buf +29.0 | 29.0 | 45 |
| ciasno bundle | 10.9 | 10.9 | 157 |
| srednio solo | 23.3 | 23.3 | 83 |
| srednio bundle | 11.6 | 11.5 | 207/208 |
| luzno solo | 10.4 | 10.4 | 80 |
| luzno bundle | 4.6 | 4.6 | 164 |

→ Re-impl jest WIERNYM drugim narzędziem. Tool liczy dokładnie to, co deklaruje. **Determinizm: tak.** **OUT mtime niezmieniony** (Jun 29 23:26 — `--dry` nie pisał).

### 1b. Metryka TZ-poprawna (mina +2h CEST NIE bije agregatu)
`eta_error_min` vs moja niezależna re-kalkulacja `delivered[Warsaw→UTC] − predicted[UTC]`:
**median |Δ| = 0.003 min, p95 = 0.005 min (n=3765)**. Pole loggera jest sound — docstring `pickup_slip_monitor.py:106-113` słusznie UNIKA samodzielnego liczenia obu stempli (użyłby +2h CEST miny). **1 rekord outlier = dokładnie 120.02 min (=2h)** → potwierdza że mina TZ jest REALNA i sporadycznie bije (F7 niżej), ale mediana/trim ją połyka.

### 1c. Reprodukuje kalibrację 29.06 — PATTERN tak, liczby drift (uczciwy powód)
Recorded snapshot (`pickup_slip_monitor.jsonl`, 1 wiersz, 29.06 23:26): ciasno/solo **27.4** · ciasno/bundle 9.5 · srednio/solo 17.7 · luzno/solo 6.2 · luzno/bundle 3.3.
Memory `ziomek-calibration-2026-06-29.md:30,64`: „ciasno solo +27/worek +9, luzno solo +6/worek +3" — **ZGODNE**.
Świeże okno (27-30.06) potwierdza GRADIENT: **ciasno/solo 29.0 >> srednio 23.3 >> luzno 10.4**; **solo >> bundle** w każdym kubełku; **ciasno > srednio > luzno** dla obu klas. **Rdzeń kalibracji (load>clock, poślizg rośnie z obciążeniem, solo gorsze) — POTWIERDZONY niezależnie.**
**Bramkowanie n≥30 działa:** `recommend_buffer_min` tylko gdy n≥30 (`pickup_slip_monitor.py:145`); unknown/solo n=2 → null. ✅

### 1d. GROUND-TRUTH: gradient przeżywa na fizycznym GPS (nie artefakt przycisku)
Join `gps_delivery_truth.jsonl` (okno 7d dla pokrycia), BUTTON-median vs PHYSICAL-median (n = gps-matched):

| komórka | all_n | gps-match | BUTTON | PHYSICAL | gap |
|---|---|---|---|---|---|
| ciasno solo | 48 | 8 | 25.9 | **23.6** | 2.3 |
| srednio solo | 93 | 10 | 18.2 | 16.4 | 1.7 |
| luzno solo | 103 | 1 | 2.1 | 1.5 | 0.6 |
| ciasno bundle | 158 | 16 | 19.5 | **17.5** | 2.0 |
| srednio bundle | 220 | 22 | 17.8 | 13.9 | 3.9 |
| luzno bundle | 219 | 6 | 4.9 | 1.8 | 3.1 |

**Gradient ciasno>>luzno przeżywa na prawdzie fizycznej** (solo 23.6→1.5; bundle 17.5→1.8). Gap BUTTON−PHYS ≈ **+2 min** = zgodne z `gps_delivery_validation_verdict.txt` (median +2.12, n=947). **Kalibracja NIE jest artefaktem opóźnienia przycisku.** (CAVEAT: gps-match n=8-22/komórkę < 30 → korroboracja KIERUNKOWA, nie n≥30-twarda.)

---

## 2. WERDYKT POBOCZNY — `pickup_lateness_shadow` = **UNTESTED jako kalibracja / observational (proxy-kierunek 74%)**
Inny przyrząd, inna semantyka: **FORWARD, żywo re-przewidywany** „odbiór będzie później" (5min, świeży, 2201 wierszy, last ts 17:37:56). Pola: `order_id, lateness_min(=predicted_pickup−committed), is_alarm(lead≥15), committed/predicted/suggested_pickup_warsaw_hhmm, restaurant`. **PRZESTRZEŃ PREDYKCJI — BRAK joinu z outcome w samym przyrządzie** (siostra problemu feas_carry blind-shadow).
**Proxy-join (dziś, distinct flagged → realny `picked_up_at`):** flagged=134, alarmów=76; matched=118 → **predykcja median 18.4 vs REALNE pickup-vs-committed median 7.0; 74% naprawdę spóźnione (>2min)**. → **Kierunek OK (74%), magnituda PRZESZACOWANA ~2.6×.** To badge ostrzegawczy (frontend NIE wdrożony), **NIE źródło kalibracji** — inna „winieta" predykcji (żywa re-prognoza) niż zamrożona decyzja pickup_slip. **NIE karmi flipu load-aware buforu.**

---

## 3. INSTANCJE (plik:linia świeży + klasa + kind + open? + severity + dowód + dedup)

| # | plik:linia | klasa | kind | open | sev | defekt / dowód | dedup_hint |
|---|---|---|---|---|---|---|---|
| F1 | `tools/pickup_slip_monitor.py:124-128` | **G** | source | TAK | **P1** | BUNDLE pooled BEZ de-konfundacji resekwencji. Komórki bundle niosą **20-31% wczesnych dostaw** (reseq: planowo-ostatni stop dowieziony pierwszy) vs 4-8% solo. Dowód: drop-early podnosi ciasno/bundle **10.9→17.6** (~calibration clean-bundle **19.1**, `b_route_shadow`). Flip użyłby buforu bundle ~11 → **clean-bundle pod ciasną flotą UNDER-buffered ~7-8min.** SOLO czyste (bez reseq) → solo godne zaufania. | calib-29.06 bundle-contamination / b_route clean-bundle |
| F2 | `tools/pickup_slip_monitor.py:49-66` + review svc `--days 6` | **H/J** | source | TAK | **P2** | Review 04.07 RE-COMPUTUJE `--days 6` na rolling `shadow_decisions` (54MB/**930 fat lines**, retencja teraz **~3.3 dnia** 27→30.06). Starsze ~2-3 dni okna 6d → `pf=None`→**„unknown" load-bucket** → load-segmentacja efektywnie pokrywa tylko świeże ~3-4 dni, podkopuje „uzbierane peaki". eta_error durable (eta_cal od V), ale ETYKIETA load znika. (Świeże-3d: 0 unjoinable — bije TYLKO przy oknie sięgającym przed start shadow.) | rolling-shadow-retention vs review-window |
| F3 | `dispatch-pickup-slip-monitor.timer` (LAST=`-`) | **H** | symptom | TAK | **P2** | Timer **NIGDY nie odpalił wg harmonogramu** (LAST=`-`, NEXT dziś 22:30); jedyny wiersz jsonl = **manualny seed 29.06 23:26**. GO/NO-GO 04.07 wisi na 4 nocnych odpaleniach (30.06-03.07) które MUSZĄ wpaść. Dowód obecny = 1 dzień high-load (29.06) + świeże-3d. Conf kalibracji „MED — dozbierać high-load" (memory:30). | timer-unproven-cadence |
| F4 | `pickup_lateness_review.py` + `dispatch_v2.pickup_lateness_shadow` | **E/G** | symptom | TAK | P2 | `pickup_lateness_shadow` przeszacowuje magnitudę ~**2.6×** (predykcja 18.4 vs real 7.0 median; 74% kierunek), BRAK outcome-joinu w przyrządzie → bezpieczne TYLKO jako observational badge. Heurystyka review (badge vs alarm) OK; magnituda NIE kalibrowana. | prediction-space-no-outcome-join (sibling feas_carry) |
| F5 | `tools/pickup_slip_monitor.py:1-13,106-113` | **F** | source | TAK | P3 | Nazwa „pickup_slip"/docstring „poślizg odbioru" vs metryka = **delivery-level** `eta_error` (delivered−predicted_delivered). PROXY poślizgu odbioru — uzasadniony bo noga-jazdy ≈0 (29.06, MED conf 1 dzień), ale przypisuje cały composite delivery-error do nogi odbioru. Docstring uczciwy; nazwa over-claimuje. | metric-name-vs-content |
| F6 | `tools/pickup_slip_monitor.py:115-119` | **H/N** | source | TAK | P3 | `collect()` BEZ górnej granicy okna (window=[cutoff,∞)) + eta_cal backfilluje późne rekordy → snapshoty NIE-odtwarzalne dla stałego okna historycznego. Re-run 29.06 ref TERAZ: **n_total 843 vs recorded 684** (wciąga 159 zleceń 06-30 postdatujących snapshot). Nieszkodliwe live (future=∅), ale myli re-derywację. | half-open-window-nonreproducible-snapshot |
| F7 | `tools/pickup_slip_monitor.py:120` | **L/M** | symptom | TAK | P3 | Filtr `abs(err)>180` NIE łapie skoku **+120min (2h CEST)**; 1/3765 rekordów ma taki skew → może zanieczyścić komórkę (mediana-odporna). Residuum miny TZ. | TZ-CEST-+2h-trap-residual |
| F8 | `gps_delivery_validation_verdict.txt` / metryka button | **E (proxy)** | source | TAK | P3 | FUNDAMENT-CAVEAT: bufor = button `delivered_at` → **+2.12min median nad fizycznym przyjazdem GPS** (n=947, 25% \|Δ\|>3min). POPRAWNY cel dla kalibracji OBIETNICY silnika (button=zapisana dostawa), ale NIE prawda fizyczna; flip ma świadomie zaakceptować ~2min button-lag. | button-truth-not-physical (FUNDAMENT-CAVEAT) |

---

## 4. CO FLIPUJE (decyzyjny wpływ na review 04.07)
- **SOLO bufory = VALIDATED, użyteczne wprost:** ciasno ~**27-29** / srednio ~**18-23** / luzno ~**6-10** (czyste, bez reseq, ground-truth-korroborowane).
- **BUNDLE bufory = NIE używać surowych** (F1): pooled ~11 understate'uje clean-bundle ~19 (ciasno). Flip MUSI wziąć de-konfundację `b_route_shadow` (clean order==plan) albo drop-early-korektę, INACZEJ pod-buforuje worki pod ciasną flotą o ~7-8min.
- **Proxy +2min button-lag (F8):** akceptowalny bo bufor celuje w button-promise silnika (tak jest).
- **Materiał 04.07 ograniczony retencją shadow (F2) + nieproven cadence (F3):** load-segmentacja review zobaczy realnie ~3-4 ostatnie dni, nie pełne 6; potwierdź n≥30 per komórka NA DZIEŃ review.
- **`pickup_lateness_shadow` (F4) NIE wchodzi do tego flipu** — to badge, inna winieta predykcji.
**Wszystko = silnik feasibility/ETA → FLIP = protokół ETAP 0→7 + ACK (nie w tej fazie audytu).**

---

## 5. INWARIANTY-TRIPWIRE (lane C) — status
- `delta≥0 (fresh≥frozen)` / `ten sam zbiór+liczba stopów` / `ZERO fikcyjnych pickupów` / `kolejność z p.sequence` = **N/A dla pickup_slip** (to inwarianty oracli reseq/route, nie metryki agregatu poślizgu). Jawnie odnotowane, nie ciche pominięcie.
- Zastosowane inwarianty pickup_slip: **(a) znak** (DODATNI=optymistyczny) — zweryfikowany re-kalkulacją; **(b) TZ-soundness** median|Δ|0.003; **(c) determinizm** PASS A==B; **(d) n≥30 gating**; **(e) monotoniczność load** ciasno>srednio>luzno (solo) + solo>bundle; **(f) join-coverage** 99.8% (świeże-3d 100%). Jeden mikro-non-monoton: srednio/bundle (11.6) ≳ ciasno/bundle (10.9) — w szumie + bundle skażony reseq (F1).

---

## 6. POKRYCIE (declared) / LUKI (gaps)

**Coverage declared:**
- `tools/pickup_slip_monitor.py` (cały, 1-189) — re-impl + `--dry` ×2.
- `tools/pickup_lateness_review.py` (cały) + `pickup_lateness_shadow.jsonl` (output) — proxy-join outcome.
- `pickup_slip_monitor.jsonl` (recorded snapshot 29.06) — porównany.
- Źródła: `eta_calibration_log.jsonl` (12887 rec, V-VI), `shadow_decisions.jsonl` (pool_feasible join, 930 lines), `gps_delivery_truth.jsonl`+verdict (ground-truth, n=947).
- Timery/serwisy: `dispatch-pickup-slip-monitor/.review/.lateness-shadow` (ExecStart + list-timers + enabled).
- Memory `ziomek-calibration-2026-06-29.md` + A4 registry.

**Coverage gaps (jawne):**
- **`dispatch_v2/pickup_lateness_shadow.py` ENGINE-SIDE source NIE czytany** (tylko output+review) — logika progu (lead≥15 alarm, committed-anchor) nie zaudytowana u źródła. Powód: czas; output+proxy-join wystarczył do werdyktu observational.
- **GPS-match n=8-22/komórkę < 30** → korroboracja fizyczna KIERUNKOWA, nie n≥30-twarda. Powód: gps_truth (947) słabo nachodzi 3-7d okno eta (wiele truth z 06-10..20).
- **Exact 29.06 shadow_decisions NIE odtwarzalny** (rolled) → „reproduce" = logic-equivalence + pattern, nie byte-exact snapshot. Powód: rolling file (F2/F6).
- **Pełny `b_route_shadow` clean-bundle de-confound NIE re-run tu** (użyto drop-negative proxy + calibration 19.1) — to lane oracle `bundle_calib`. Powód: granica zadania; F1 wskazuje zależność.
- **`eta_calibration_logger` source (jak/kiedy pisze `eta_error_min`, timing backfill) NIE czytany** — backfill wywnioskowany z przyrostu n_total (684→843). Powód: read-only, poza metryką lane.

---

## 7. ORACLE-VERDICTS (skrót do struktury)
1. **pickup_slip_monitor → VALIDATED (proxy-certified).** 2nd method: re-impl + tool `--dry`×2 + raw-stamp recompute (median|Δ|0.003/3765) + GPS ground-truth join. Flipuje: load-aware bufor ETA 04.07 — SOLO godne, BUNDLE skażone (F1). Inwarianty: TZ-sound/determinism/n≥30/load-monoton/sign — PASS.
2. **pickup_lateness_shadow → UNTESTED (jako kalibracja) / observational.** 2nd method: proxy-join distinct→picked_up_at (18.4 vs 7.0; 74%). Magnituda ×2.6 przeszacowana, brak outcome-joinu. Flipuje: badge deploy (nie buforu); NIE wchodzi do flipu silnika.
