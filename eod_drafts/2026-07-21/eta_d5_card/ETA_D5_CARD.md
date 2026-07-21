# KARTA DECYZYJNA D5 — flip APPLY obietnic ETA kalibratora (per-leg)

**Data pomiaru:** 2026-07-21 (READ-ONLY; zero zmian kodu/flag).
**Autor:** sesja CTO (pomiar), **flip wykonuje wyłącznie CTO za osobnym ACK Adriana.**
**Zakres wg D6a (OWNER_CONFIRMED 18.07):** wyłącznie warstwa OBIETNIC/PREZENTACJI
(proponowany czas odbioru w konsoli + ETA w apce). Feasibility/R6/scoring/selekcja
**NIETKNIĘTE.** Pola NOWE OBOK (wzorzec #8), żadnej podmiany pól karmiących decyzje.

---

## 1. DEFINICJA PROGÓW D5 (cytat, nie moja propozycja)

Źródło kanoniczne: `dispatch_v2/tools/eta_ground_truth.py` → `KPI_BINDING_V1["thresholds"]`
(OWNER_CONFIRMED „D5 ok", rekord `owner-decision-eta-calib-d1-d7-2026-07-18`):

```
pickup:   mae_max_min = 6.0 ,  min_improvement_vs_engine_pct = 25.0
delivery: mae_max_min = 8.0 ,  min_improvement_vs_engine_pct = 10.0
late_band_pct = [15.0, 22.0]          (odsetek spóźnień pod obietnicą P80)
median_bias_abs_max_min = 1.5
p90_abs_err_max_min = 20.0
outliers = winsorize_report_p99_keep_denominator
coverage_gate (D4): min_complete_case_pct = 60.0 , min_n = 200 , poniżej → HOLD komórki (fail-closed)
```

Prawda referencyjna: `dispatch_state/eta_calib.db` (feature store, 9464 wierszy, 08.05→20.07)
+ leak-free rolling holdout w `eta_calib_metrics.jsonl` (nocny gate) + per-order
`eta_calib_shadow.jsonl` (predykcje kalibratora vs real). Wszystkie liczby niżej z tych źródeł.

---

## 2. TABELA PROGÓW ↔ WARTOŚCI ZMIERZONE

Wartość główna = nocny gate 21.07 (leak-free rolling holdout, cut 07-07→07-17);
w nawiasie zakres z 7 ostatnich nocy (15–21.07). bias/p90 = z `eta_calib_shadow.jsonl` (okno od 07-07).

### PICKUP (odbiór)
| Kryterium D5 | Próg | Zmierzone (21.07) | Werdykt |
|---|---|---|---|
| MAE | ≤ 6.0 | **5.40** (5.10–5.40) | ✅ |
| poprawa vs silnik | ≥ 25% | **51.6%** (51.6–54.9%) | ✅ duży zapas |
| spóźnienia P80 | 15–22% | **18.3%** (16.0–18.7%) | ✅ |
| \|bias mediany\| | ≤ 1.5 | **+0.54** | ✅ |
| p90 \|błędu\| | ≤ 20 min | **11.0** | ✅ |
| coverage (D4) | n≥200 | **n=3129** | ✅ |
| **PICKUP RAZEM** | | | **✅ GO (wszystkie z zapasem, stabilne)** |

### DELIVERY (dostawa)
| Kryterium D5 | Próg | Zmierzone (21.07) | Werdykt |
|---|---|---|---|
| MAE | ≤ 8.0 | **7.28** (7.28–7.61) | ✅ |
| poprawa vs silnik | ≥ 10% | **14.9%** (14.9–20.1%, ↓ trend) | ⚠️ PASS, margines maleje ku 10% |
| spóźnienia P80 | 15–22% | **13.0%** (11.4–20.2%, zmienne) | ❌ ostatnie 4 noce <15% (nad-buforowane) |
| \|bias mediany\| | ≤ 1.5 | **+0.71** | ✅ |
| p90 \|błędu\| | ≤ 20 min | **15.7** | ✅ |
| coverage (D4) | n≥200 | **n=2575** | ✅ |
| **DELIVERY RAZEM** | | | **⚠️ WAIT (late-band poza pasmem, kierunek BEZPIECZNY; margines poprawy maleje)** |

**Trend improvement dostawy (silnik się poprawia szybciej niż kalibrator):**
20.1%→19.1%→19.1%→17.3%→17.2%→16.4%→**14.9%** (silnik MAE 9.52→8.55; kalibrator 7.61→7.28).
Nadal >10%, ale kierunek do obserwacji przed i po flipie.

**Niezależna kontrola prawdy (eta_calib.db, silnik):** delivery engine MAE 8.70 (14d) ≈ baseline
gate'u 8.55; pickup engine slip MAE 11.43 ≈ 11.15. Bazy silnika się zgadzają → progi liczone poprawnie.

---

## 3. RYZYKO KIERUNKOWE — „kalibrator obiecuje WCZEŚNIEJ"

Delta obietnicy na żywo (shadow, 490 decyzji 18.07→21.07): **pickup P80 kalibratora med −14.9 min
względem ETA silnika** (mean −17.6). Tzn. kalibrator pokazuje odbiór ~15 min wcześniej niż silnik —
bo silnik grubo zawyża (MAE 11.15 vs 5.40), kalibrator jest ciaśniejszy, nie „optymistyczny per se".

**Koszt błędu w drugą stronę (kurier przyjeżdża przed jedzeniem = czeka)** = odsetek, gdzie faktyczny
odbiór jest PÓŹNIEJSZY niż obietnica P80 (obietnica była za wczesna):
| Leg | za-wczesna obietnica (nocny) | (shadow-log okno 07-07) | za wczesna o >10 min |
|---|---|---|---|
| pickup | 18.3% | 20.5% | **2.9%** |
| delivery | 13.0% | 22.0% | **6.2%** |

- **Guard:** odsetek za-wczesnych > **22%** (górny brzeg late_band) = NO-GO.
- Pickup: 18.3–20.5% — **pod progiem**, „za wczesna o >10 min" tylko ~3% → ryzyko czekania małe i ograniczone.
- Delivery: nocny 13.0% (nad-buforowane, kierunek bezpieczny) **vs** shadow-log 22.0% na samej granicy →
  **rozbieżność pomiarowa to główny powód WAIT dla dostawy.** Bufor P80 dostawy = +11.5 min (med obietnicy 27.4 vs real 17.7).

Uwaga zakresowa: przy D6a (tylko prezentacja) obietnica NIE zmienia momentu wysłania kuriera ani okna
R27 ±5 / `czas_kuriera` (te rządzone silnikiem, nietknięte) — zmienia wyświetlany czas. Koszt „czekania"
to sygnał koordynacyjny w konsoli/apce, nie realne przyspieszenie dyspozycji.

---

## 4. WOLUMEN, POKRYCIE, STABILNOŚĆ

- **Pokrycie cienia (żywy serving D6a, `shadow_decisions.jsonl`, 18.07 19:05→21.07 09:25):**
  490/525 decyzji = **93.3%**; **100% wśród decyzji z wybranym kurierem** (35 bez obietnicy = BRAK KANDYDATÓW).
  `srv_skip = 0`, zero fail-soft. **19/19 kurierów z przypisaniem = 100% pokrycia.** ~200 obietnic/dobę.
- **Holdout gate:** pickup n=3129, delivery n=2575 — grubo ponad D4 min_n=200.
- **Stabilność dzień-po-dniu:** pickup bardzo stabilny (MAE 5.10–5.40, late 16–19%). Dostawa: MAE stabilne
  (7.28–7.61), ale late% **zmienne 11.4–20.2%** i improvement w trendzie spadkowym — to najczulsze metryki.

### ⚠️ Sprostowanie artefaktu at#220
`eod_drafts/2026-07-18/ETA_PARITY_VERDICT.txt` (zapis 21.07 05:30) pokazuje **pickup 0/508 (0.0%)** —
to **artefakt rotacji logu** w oknie nocnego zadania 05:20–05:22 (skrypt policzył moment, gdy świeże
wiersze z obietnicą były poza plikiem), a NIE chory cień. Pole jest dokładnie tam, gdzie parser go szuka
(`best.eta_calib_promise_pickup_p80_min`). Rzeczywiste pokrycie liczone teraz = **93.3%**. **Rekomendacja:
`eta_promise_parity.py` uruchamiać POZA oknem 05:15–05:25 UTC** (albo dodać retry/któryś-świeży-plik).

---

## 5. WERDYKT WSTĘPNY (per-leg, rozdzielony)

- **PICKUP (odbiór): GO-ready co do progów D5.** Wszystkie 5 kryteriów spełnione z zapasem i stabilnie,
  ryzyko za-wczesnej obietnicy pod guardem. Blokada = tylko brak zbudowanej warstwy APPLY (§6) + ACK.
- **DELIVERY (dostawa): WAIT.** MAE/bias/p90/coverage OK; late-band poza [15,22] na ostatnich nocach
  (13% <15% = nad-buforowane, kierunek bezpieczny) + rozbieżność za-wczesnych 13% vs 22% + malejący
  margines improvement (14.9%→ku 10%). Rekomendacja: dostroić kwantyl operacyjny dostawy tak, by late%
  wpadło w pasmo, albo świadomie zaakceptować bezpieczny nad-bufor — decyzja Adriana; nie flipować razem z pickup.

**Nie jest to NO-GO** (dostawa myli się w bezpieczną stronę), ale i nie czysty GO — stąd rozdzielenie legów.

---

## 6. PLAN FLIPA (która flaga, hot/restart)

**Stan obecny:** jedyna flaga = `ENABLE_ETA_CALIB_PROMISE_SHADOW = true` w `flags.json`
(`eta_calib_serving.py:105`, `C.decision_flag(...)`). Robi TYLKO: liczy i **dokleja pola OBOK**
(`eta_calib_promise_pickup/delivery_p80_min`) w `shadow_decisions.jsonl`. **Prezentacja NIE podmieniona
— nic tych pól jeszcze nie wyświetla.**

**APPLY (D6a) = OSOBNY krok, JESZCZE NIEZBUDOWANY** (potwierdzone grepem: brak flagi/konsumenta apply):
1. Nowa flaga np. `ENABLE_ETA_CALIB_PROMISE_APPLY` (ETAP4 + const OFF + `flags.json`), lifecycle registry.
2. Protokół #0 przez warstwy prezentacji: konsola (proponowany czas odbioru, `panel_watcher`/panel) +
   apka (`courier_api`) — pola NOWE OBOK, podmiana WYŁĄCZNIE wyświetlanej wartości, zero dotyku pól decyzyjnych.
3. Dowody: test ON≠OFF (OFF = engine ETA, ON = calib promise), parytet stary-vs-nowy na tych samych zleceniach,
   pełna regresja vs baseline, e2e przez dotknięte warstwy.
4. **Rekomendacja per-leg:** wpiąć APPLY dla **pickup** (GO); **delivery** trzymać w cieniu do domknięcia late-band.
5. **Hot czy restart:** przełączenie samej flagi = `flags.json` **hot** (bez restartu silnika). ALE wdrożenie
   KODU apply w konsoli/apce = **restart** tych usług (`dispatch-panel-watcher`, `courier-api`) — nie w peak, ACK.

**Flip wykonuje wyłącznie CTO po: (a) zbudowaniu warstwy APPLY protokołem #0, (b) domknięciu late-band dostawy
lub świadomej decyzji per-leg, (c) KOŃCOWYM ACK Adriana.**

---

## 7. ROLLBACK

- **Serving/shadow:** `ENABLE_ETA_CALIB_PROMISE_SHADOW = false` w `flags.json` — **hot**, serving znika
  od następnej decyzji (bez restartu).
- **APPLY (gdy powstanie):** `ENABLE_ETA_CALIB_PROMISE_APPLY = false` — hot; wyświetlanie wraca do ETA silnika.
- **Kod:** `git revert` commitu apply.
- **Championy:** przywrócenie map z `dispatch_state/eta_calib_{pickup,delivery}_map.json.bak-legacy-pre-d7-bootstrap-2026-07-18`
  + usunięcie `eta_calib_champion_provenance.json`.
- **Bezpiecznik:** feasibility/R6/scoring/`czas_kuriera`/R27 nietknięte na każdym etapie → rollback prezentacji
  nie dotyka decyzji dyspozytorskich.

---

### Źródła (wszystkie READ-ONLY)
- Progi: `dispatch_v2/tools/eta_ground_truth.py` (KPI_BINDING_V1)
- Nocny gate leak-free: `dispatch_state/eta_calib_metrics.jsonl`
- Per-order predykcje: `dispatch_state/eta_calib_shadow.jsonl`
- Prawda/feature store: `dispatch_state/eta_calib.db`
- Cień żywy: `scripts/logs/shadow_decisions.jsonl` (pole `best.eta_calib_promise_*`)
- Decyzje ownera: memory `owner-decision-eta-calib-d1-d7-2026-07-18`, `ziomek-parity-audit-2026-07-18`
