# WERDYKT GATE B — TomTom API vs darmowa krzywa B (recalib) — 2026-06-11

## WERDYKT (TL;DR — 5 linijek)

1. **NIE PŁACIĆ za TomTom. Krzywa B (recalib, $0) wystarcza — PoC ZAMKNĄĆ po jutrzejszym at#123.**
2. Oficjalny test Gate B na OOS peak (n=62, miarodajny): bcRMSE krzywa B **4.76** vs TomTom **4.81** — TomTom po korekcji biasu **GORSZY o 0.05 min**; próg „płacić" (≥0.75 min i ≥10% i win>55%) nieosiągnięty nawet w 1/10.
3. Kumulatywnie (cały PoC, peak n=274): TomTom lepszy o ledwie **+0.09 min / +2%** bcRMSE, win-rate 51% → remis → OSRM (Z3: prostsze, bez zależności od API).
4. Rekalibracja OOS TRZYMA: bias **-2.54 → -1.64** (Δ-0.90), MAE **3.81 → 3.71**, RMSE **6.18 → 5.86**, win 59% (n=249, 5 dni roboczych) — dokładnie to, co jutro o 08:00 wyśle at#123 (werdykt skryptu: „✅ RECALIB TRZYMA przewagę OOS — nie rollbackować").
5. Jedyna przewaga TomTom = mniejszy bias (-0.85 vs -1.64 min) — a bias jest **kalibrowalny za darmo** (to istota krzywej B); rozrzutu niekalibrowalnego TomTom NIE redukuje. Koszt wdrożenia w pipeline: ~**$50–260/mies** + p95 latencji ~289 ms/call (vs OSRM lokalny ~11 ms) — zerowy zwrot.

---

## 1. Metodologia i poprzednie werdykty (z plików PoC)

**Metodyka Gate B** (`analyze_realworld.py`): `err = predykcja − ground_truth_drive`; bias = średnia per bucket; **bcRMSE = RMSE rezyduów po zdjęciu biasu** (błąd niekalibrowalny — główna metryka). TomTom „wygrywa" ⟺ na bucket **PEAK**: bcRMSE niższy o **≥0.75 min I ≥10% ORAZ win-rate >55%**, przy n≥25. Inaczej OSRM (Z3). Ground truth: hybryda tier-1 (GPS pure-drive, gold) + tier-2 (delivery_time − median nondrive per bucket).

**Metodyka monitora OOS** (`monitor_recalib_oos.py`, to odpala at#123): tylko tropy weekday z odbiorem ≥2026-06-04 (krzywa trenowana na danych do 03.06 → czysty out-of-sample); porównuje starą tabelę V326 vs żywą krzywą z `common.py`; progi: trzyma ⟺ |bias_live| < |bias_old|−0.10 ORAZ MAE_live ≤ MAE_old+0.05; cienki sygnał gdy n<25.

| Werdykt | Data | Wynik |
|---|---|---|
| `analyze_verdict_2026-05-19.txt` | 19.05 | NIEMIARODAJNY (peak n=20 < 25) |
| `analyze_verdict_2026-05-22.txt` | 22.05 | peak n=54: bcRMSE OSRM 5.28 vs TT 5.30 → **remis → OSRM** |
| `recalib_verdict_B_2026-06-05.txt` | 05.06 | krzywa B wygrywa in-sample (bias 2.23→1.37, MAE 3.80→3.72) → wdrożona do `common.py` (weekday) |
| `recalib_weekend_verdict_2026-06-05.txt` | 05.06 | kandydaci sat/sun policzeni, **NIE wdrożeni** |
| `monitor_recalib_oos_latest.txt` (stary, 1 dzień) | 05.06 | n=32: „≈ neutralnie OOS — zbieraj dalej" |

## 2. Wynik OOS od 2026-06-04 (liczony dziś, dzień przed at#123)

Uruchomione przez import `monitor_recalib_oos.run("2026-06-04")` — zero `--notify`, zero zapisów w katalogu PoC. Skrypt: `/tmp/gate_b_oos_verdict_2026-06-11.py`.

### 2a. Oficjalny monitor (to policzy at#123) — weekday, n=249

| metryka | STARA V326 | KRZYWA B (LIVE) | Δ |
|---|---|---|---|
| bias | -2.54 | **-1.64** | -0.90 |
| rawMAE | 3.81 | **3.71** | -0.09 |
| rawRMSE | 6.18 | **5.86** | -0.32 |
| win% B | | **59%** | |

**WERDYKT skryptu: ✅ RECALIB TRZYMA przewagę OOS — nie rollbackować.** Trend per-dzień spójny — B lepsza każdego z 5 dni (04/05/08/09/10.06, n=30/38/67/52/62). Nocny rebuild GT (03:30) doliczy 11.06 → at#123 policzy na n≈300, kierunek bez szans na zmianę.

### 2b. 3-way: krzywa B vs TomTom vs OSRM raw (weekday OOS, n=249)

| predyktor | bias | rawMAE | rawRMSE |
|---|---|---|---|
| OSRM raw (freeflow, bez mnożnika) | -3.77 | 4.35 | 6.84 |
| stara tabela V326 | -2.54 | 3.81 | 6.18 |
| **krzywa B (recalib LIVE)** | **-1.64** | **3.71** | **5.86** |
| TomTom live traffic | -0.85 | 3.51 | 5.51 |

Surowa przewaga TomTom nad B: **MAE -0.20 min (-5%)**, RMSE -0.35, bias +0.79 — całość przewagi siedzi w biasie (kalibrowalnym za darmo).

### 2c. Oficjalny test progowy Gate B na OOS (peak, n=62 — pierwszy raz miarodajny na OOS)

- bcRMSE: krzywa B **4.76** vs TomTom **4.81** → TomTom **-0.05 min (gorszy, -1%)**, win-rate TT 58%
- **FAIL progów Gate B** (wymagane ≥0.75 min I ≥10% I win>55%) — po zdjęciu biasu TomTom nie wnosi nic.
- Cross-check tier-1 OOS (GPS gold, n=12, cienki): B MAE 3.63 vs TT 4.17 — **B wygrywa 67%** tropów.

### 2d. Kumulatywnie cały PoC (16.05→11.06, join n=1264, `analyze_realworld.py`)

- Pełen sample peak n=274: bcRMSE OSRM 5.13 vs TT 5.04 (**+0.09 min, +2%**, win 51%) → **„remis / marginalne → OSRM"** (werdykt skryptu).
- Tier-1 peak n=31: TT +0.52 min/+14%, win 68% — poniżej progu absolutnego 0.75 min i cienki sample → wciąż remis.
- Korelacja z realną jazdą: OSRM r=0.523 vs TT r=0.548 (pełen), 0.768 vs 0.782 (tier-1) — praktycznie identyczna.

### 2e. Weekend OOS (n=110, info — krzywa weekend NIEwdrożona)

| predyktor | bias | MAE | RMSE |
|---|---|---|---|
| tabela prod (sat/sun LIVE = stare) | -2.47 | 3.59 | 6.40 |
| TomTom | -0.30 | 3.53 | 5.79 |

Niedziela (n=58): prod bias **-3.96**/MAE 4.88 vs TT -1.61/4.42 — luka jest, ale to argument za wdrożeniem **darmowego** kandydata krzywej weekend z 05.06 (in-sample: sun bias -2.69→-0.60, MAE 3.49→3.14), nie za TomTom.

## 3. Świeżość/kompletność danych wejściowych

- `rw_results.jsonl`: 4813 pomiarów, ostatni **2026-06-11 11:20 UTC** (cron `*/10` ŻYJE; log `measure_rw.log` czysty, ok/err bez 429). Per-dzień ~130-270.
- `trips_realworld.jsonl` (GT): 1646 tropów, do **2026-06-10** włącznie (nocny rebuild 03:30 dziś OK w `build_gt.log`; 11.06 doliczy się jutro w nocy).
- Okno OOS: 5 pełnych dni roboczych + 1 weekend (06-07.06) — n=249 weekday >> próg 25. **Dane WYSTARCZAJĄCE, werdykt miarodajny** (wariant (c) odpada).

## 4. Koszt TomTom, gdyby jednak płacić (dla porządku)

- Skala PoC (~190 calli/dzień, 1 call/zamknięty trop): mieści się w darmowym tierze 2 500 req/dzień → **$0** — ale to tylko pomiar, nie produkcja.
- Produkcyjna integracja w pipeline (każda propozycja: ~10 kandydatów × 4-8 nóg tras, ~150-250 propozycji/dzień + recheck): **~6-20k calli/dzień** → ponad free tier; przy ~$0.50/1k req ≈ **$50-260/mies** (rosnąco z flotą/Warszawą).
- Plus koszt architektoniczny: p95 TomTom ~289 ms/call (pomiar PoC z 14.05) vs OSRM lokalny p95 ~26 ms — dziesiątki calli na propozycję rozsadziłyby budżet <500 ms p95; do tego zależność od zewnętrznego API i klucza (anty-Z3).
- Zwrot: -0.05 min bcRMSE na peaku OOS. **Stosunek wartość/koszt = zero.**

## 5. Rekomendacja porządkowa (NIC nie wykonano — same rekomendacje)

1. **at#123 (12.06 08:00, `monitor_recalib_oos.py --since 2026-06-04 --notify`) → ZOSTAWIĆ.** Dane go potwierdzają (werdykt będzie „✅ RECALIB TRZYMA"); Adrian dostanie oficjalną notyfikację na Telegram z n≈300. Nic go nie unieważnia.
2. **Po at#123 + ACK Adriana zdjąć z crontaba 4 linie** (obecnie linie 24-27):
   ```
   # GATE B TomTom PoC - forward-live pomiar OSRM vs TomTom (added 2026-05-16)
   */10 7-22 * * * cd /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-14/tomtom_poc && python3 measure_realworld.py >> measure_rw.log 2>&1
   # GATE B TomTom PoC - nocny rebuild ground truth
   30 3 * * * cd /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-14/tomtom_poc && python3 build_ground_truth.py >> build_gt.log 2>&1
   ```
   Żaden kod produkcyjny nie konsumuje plików PoC (jedyna referencja w `common.py:309` to komentarz o pochodzeniu krzywej). Pozostałe at-joby (84/118/119/131/132) = Mailek/E7/gate-guard — nie dotyczą PoC.
3. **Wyjątek — decyzja Adriana o krzywej WEEKEND:** jeśli wdrażamy darmowego kandydata sat/sun z 05.06 (niedziela mocno niedoszacowana: bias -3.96), zostawić oba crony jeszcze ~2 tyg na walidację OOS weekendu (monitor czyta żywą tabelę, zadziała automatycznie), potem zdjąć. Jeśli nie wdrażamy — zdjąć od razu po pkt 2.
4. Drobne: `TOMTOM_API_KEY` w `.env` może zostać (free tier, po zdjęciu cronów nieużywany) lub do wykreślenia przy okazji; katalog PoC zostawić jako archiwum — `monitor_recalib_oos.py` + `build_ground_truth.py` są reużywalne przy kolejnych iteracjach rekalibracji ($0).
5. **Follow-up darmowy zamiast TomTom:** bias krzywej B na OOS wciąż -1.64 min i pogarsza się pod koniec okna (09.06: -2.00, 10.06: -2.51 — możliwy dryf sezonowy czerwca) → za ~4 tyg. kolejna iteracja rekalibracji weekday na świeżych danych (ten sam darmowy pipeline; wymaga włączonych cronów przez ~2 tyg. przed — wzorzec: włącz → zbierz → recalib → OOS monitor → wyłącz).

---
*Analityk PoC routingu; liczone 2026-06-11 ~13:30 UTC na danych do 11.06 11:20 UTC. Skrypt analizy: `/tmp/gate_b_oos_verdict_2026-06-11.py` (read-only, bez notify). Zero zmian w crontab/at/flags.json/git; zero Telegrama.*
