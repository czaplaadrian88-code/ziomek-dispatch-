# BARTEK GOLD STANDARD — Dispatch Rules from Real Data

**Źródło:** 231 zleceń Bartek O. z 5 czystych dni (40/41/42/43/45) + 1280 zleceń z plików floty (50-53, 59, 60) dla porównania.
**Wygenerowane:** 2026-04-14 z `bartek_gold/analyze_bartek.py` → `analysis.json`
**Haversine road factor:** 1.37 (Białystok, z `common.py`)
**Metoda bundla:** pickup time ±3 min (czas_odbioru z panelu).

> **WAŻNE:** Wszystkie liczby poniżej pochodzą z danych, nie ze zgadywania. Progi progowe są p90/p95 empiryczne z zachowania Bartka — NIE teoretyczne.

---

## R1 — Outlier Detection (delivery spread w bundlu)

**Metryka:** maksymalny dystans (road km = haversine × 1.37) pomiędzy dwoma dowolnymi adresami dostawy w aktywnym bundlu kuriera.

**Rozkład empiryczny Bartka (47 bundli ≥2):**
| median | p75 | p90 | p95 | max |
|--------|-----|-----|-----|-----|
| 4.54 km | 6.14 km | **8.00 km** | ~9.5 km | 22.39 km (outlier) |

**Próg:** **R1 = 8.0 km** (p90 Bartka).

**Znaczenie:** Nowy delivery NIE może być dalej niż 8.0 km od najbliższego delivery w aktywnym bagu kuriera. Jeśli jest dalej → `verdict=NO, reason="spread_outlier"`.

**Uzasadnienie:** Bartek w 90% przypadków trzyma bag w obrębie 8 km. Jedyny case 22 km to bundle 3-zleceniowy z Kumar's do 4 stref miasta (wyjątek, dłuższy delivery 40 min).

**UWAGA dla mixed-restaurant bundli:** dotyczy TYLKO delivery addresses. Pickup spread jest mierzony osobno (patrz R5).

---

## R2 — Max corridor deviation (nowe zlecenie na trasie)

**Metryka:** perpendicular distance (km) od nowego delivery point do linii prostej łączącej pierwszy pickup bundla i najdalszy delivery.

**Rozkład empiryczny Bartka (124 multi-orders):**
| median | p75 | p90 | p95 | max |
|--------|-----|-----|-----|-----|
| 0.47 km | 1.47 km | **2.26 km** | ~3.0 km | 5.83 km |

**Próg:** **R2 = 2.5 km** (tuż powyżej p90 dla marginesu błędu geocodingu ±100m).

**Znaczenie:** Przy ocenie "czy nowe zlecenie jest na trasie kuriera" — jeśli delivery leży > 2.5 km od korytarza aktualnej trasy → outside corridor, standardowy scoring (nie jako "free stop"). Jeśli ≤ 2.5 km → kandydat bundlowy z preferencją (patrz R4).

**Uzasadnienie:** 90% Bartka mieści się w 2.26 km korytarzu. p75 = 1.47 km to "comfort zone".

---

## R3 — Dynamic MAX_BAG_SIZE (zależny od rozrzutu)

**Dane empiryczne — rozrzut delivery vs rozmiar bundla (Bartek clean):**

| size | n  | median km | max km | uwagi |
|------|----|-----------|--------|-------|
| 2    | 25 | 4.16      | 10.36  | typowy |
| 3    | 17 | 4.54      | 22.39  | outlier raz |
| 4    | 2  | 9.45      | 10.45  | rzadki, czas 32-40 min |
| 5    | 3  | 7.24      | 7.86   | best-case, 19-30 min |
| 6+   | 0  | —         | —      | **Bartek NIGDY nie robi 6+ w clean sample** |

**Progi (kapiszon na bazie Bartka):**

```
spread ≤ 5.0 km    →  MAX_BAG = 5   (ciasny cluster, Bartek grę ogarnia)
spread ≤ 8.0 km    →  MAX_BAG = 4   (p75-p90, dalej ok ale ostrożnie)
spread > 8.0 km    →  MAX_BAG = 3   (R1 już odrzuca nowych kandydatów)
ABSOLUTE_CAP       =  5             (D3 zachowany: MAX_BAG_TSP_BRUTEFORCE=5)
```

**Znaczenie:** Efektywny `MAX_BAG_SIZE(courier) = f(spread_km)`. Zastępuje statyczny MAX_BAG z common.py.

---

## R4 — Free Stop Bonus (korytarzowy)

**Metryka:** perpendicular distance nowego delivery do linii kurier→farthest_delivery_in_bag.

**Próg:** **R4 = 0.5 km** (median Bartka 0.47 km).

**Znaczenie w scoringu:**
- `dev ≤ 0.5 km` → **free_stop_bonus = +100** (pełny, "prawie po drodze")
- `0.5 < dev ≤ 1.5 km` → bonus = +50 linear decay
- `1.5 < dev ≤ 2.5 km` → bonus = +20
- `dev > 2.5 km` → bonus = 0 (R2 outside corridor)

**Uzasadnienie:** Istniejąca reguła R4 w CLAUDE.md mówi "<500m = zero-cost". Dane Bartka potwierdzają — median odchylenia = 0.47 km. To nie jest przypadek, Bartek świadomie bierze zlecenia które są "po drodze".

---

## R5 — Zasady których Bartek NIGDY nie łamie

Z analizy 231 zleceń wyłania się kilka twardych constraint które Bartek zachowuje 100%:

1. **NIGDY size > 5.** W 5 czystych dniach nie ma ani jednego bundla 6+. Pokrywa się z D3 MAX_BAG_SANITY_CAP=8 jako absolutny maks, ale Bartek operacyjnie trzyma ≤5.

2. **NIGDY pickup spread > 1.8 km przy mixed-rest bundlu.** Gdy Bartek bierze zlecenia z ≥2 restauracji, restauracje są max 1.79 km od siebie. Median pickup spread = 0.0 km (same restaurant dominant). **Implikacja:** dopóki nie ma GPS routing na pickup points, `feasibility_v2` powinno odrzucać mixed-rest kandydatów gdy `road_km(rest_A, rest_B) > 1.8 km`.

3. **95% multi-delivery w ≤45 min, 86% w ≤35 min.** Jako SLA-level guarantee. Jeśli symulacja trasy pokazuje ETA > 45 min dla któregokolwiek zlecenia w bagu → bundle NO.

4. **Mixed-rest bundling to wyjątek, nie norma.** 35/47 bundli = same-restaurant (74%), 12/47 mixed (26%). Bartek preferuje "zabrać 5 zleceń z jednej restauracji w jednym przejściu" nad "zbierać po drodze z różnych". Wniosek dla scoringu: same-restaurant bundle dostaje preferencyjny weighting.

5. **Ramp-up godzina 1 — bundling ograniczony do 2.** (R14 z CLAUDE.md, zgodne z obserwacjami.)

---

## R6 — Bartek vs reszta floty

**Dane: 1280 zleceń Bartka + 9 innych kurierów (top 10 po wolumenie) z plików 50-53, 59-60.**

| Kurier    | Orders | Bundled % | Avg bag | Avg deliv min |
|-----------|--------|-----------|---------|---------------|
| **Bartek O.** | **1280** | **58.1%** | **2.40** | **17.08** |
| Mateusz O | 1079   | 51.4%     | 2.52    | 16.79         |
| Gabriel   | 1006   | 50.2%     | 2.24    | 19.13         |
| Adrian R  | 1244   | 40.0%     | 2.25    | 21.52         |
| Grzegorz  | 711    | 33.8%     | 2.20    | 21.35         |
| Kacper Sa | 813    | 31.0%     | 2.14    | 17.75         |
| Andrei K  | 1219   | 28.4%     | 2.12    | 21.83         |
| Jakub OL  | 1114   | 27.3%     | 2.04    | 18.46         |
| Michał K. | 1485   | 25.9%     | 2.08    | 19.09         |
| Artsem Km | 734    | 19.5%     | 2.04    | 22.51         |

**Fleet non-Bartek średnie:**
- Bundled %: **31.0%** (Bartek +27 pp, prawie 2× więcej)
- Avg bag size: **2.14** (Bartek +0.26)
- Avg delivery min: **20.1 min** (Bartek **-3.0 min szybciej mimo większych bagów**)

**Top insights:**
- Bartek **#1 w bundle rate** (58.1%) spośród 10 największych wolumenowo.
- **Szybszy delivery mimo większych bagów** — dowód że dobry bundling nie kosztuje czasu, tylko go oszczędza.
- **Mateusz O** jest bardzo blisko (51.4%, 2.52 avg, 16.79 min) — potencjalny "Bartek #2" benchmark.
- **Artsem Km, Michał K., Jakub OL, Andrei K** — <30% bundling — tu jest największa rezerwa operacyjna (proactive coaching candidate dla Fazy 2).

**Ile by Ziomek zaoszczędził gdyby fleet = Bartek?**
Przy 1500-2000 orderów/tydz i Bartek bundled% 58% vs fleet 31%, różnica to ~27 pp × 1750 orders = ~470 dodatkowych bundled orderów/tydz. Zakładając +0.25 saved delivery per bundled order (21.6−18 solo + overhead) → ~2h savings/day fleet → **~1 pełny etat kuriera mniej przy tym samym wolumenie**. Realna skala oszczędności do walidacji po Fazie 2 auto-approve.

---

## Przykłady bundli z danych

### Perfect bundle — Rukola Sienkiewicza 5× (2026-04-10)
- 5 zleceń, spread 7.86 km, avg 19.2 min
- Adresy: Komisji Edukacji Narodowej 36b, Waryńskiego 1a, Leszczynowa 55, Kraszewskiego 17c, Lewandowskiego 3
- **Insight:** 5 zleceń z jednej restauracji w 19 min avg — to nie jest zgaduwanka, Bartek wie że te adresy leżą blisko siebie mimo pozornie rozrzuconych lokalizacji.

### Perfect mixed bundle — Raj + 350 Stopni (2025-12-28)
- 5 zleceń, size 5, spread 6.24 km, avg 21.2 min
- Rests: Raj + _350 Stopni Kilińskiego (2 restauracje)
- Adresy: Żabia 10, Rzemieślnicza 28, Ukośna 42, Trzcinowa 23, Rzemieślnicza 13
- **Insight:** Dwie restauracje blisko siebie (pickup spread <1 km), 5 deliveries w ciasnym klastrze = optimal mixed bundle.

### Hard case — Kumar's 4× (2025-12-28)
- 4 zleceń, spread 10.45 km, avg **40.0 min**
- Adresy: Waszyngtona 25B, Curie-Skłodowskiej 24A, Nowowarszawska 61, Herberta 18
- **Insight:** Już blisko granicy. 10.45 km > R1=8, nowy order by został odrzucony. Avg 40 min = poza "95% ≤45 min". Ta sytuacja jest dokładnie tym czego chcemy unikać — R1=8 km by ten bundle zatrzymał.

### Outlier — Kumar's 5× 2026-01-11
- 5 zleceń, spread 7.24 km, avg **29.6 min**
- Adresy: Kołłątaja 36, Waszyngtona 24, Wierzbowa 3a, Pogodna 4c, Skrzetuskiego 26a
- **Insight:** Rozrzut 7.24 km OK (R3 → max bag 5), ale czas 29.6 min wskazuje że to był trudny case. Walkaround: same-restaurant high-volume + rozsądny spread.

---

## Proponowane zmiany w kodzie (KROK 3 — czeka na aprobatę)

### A. `feasibility_v2.py`
- **R1 (nowa):** `check_spread_outlier(bag, new_delivery_coord)` — if `max(road_km(new, d) for d in bag.deliveries) > 8.0` → `FeasibilityResult(ok=False, reason="R1_spread_outlier")`
- **R5 (nowa):** `check_mixed_rest_pickup(bag, new_restaurant_coord)` — if `road_km(bag.primary_pickup, new_rest) > 1.8` AND bag.restaurant != new.restaurant → NO

### B. `route_simulator_v2.py`
- **R3 (modyfikacja):** `dynamic_max_bag_size(bag)` → zastąp static MAX_BAG. Return 5/4/3 w zależności od `deliv_spread_km(bag)`.
- **R2 (tie-breaker):** w greedy loop, przy równym SLA score, preferuj kandydata o niższej `perpendicular_deviation_km` od korytarza.

### C. `scoring.py` / `dispatch_pipeline.py`
- **R4 (nowa komponenta):** `free_stop_bonus(dev_km)`:
  ```python
  if dev <= 0.5: return +100
  if dev <= 1.5: return +50 * (1 - (dev-0.5)/1.0)
  if dev <= 2.5: return +20 * (1 - (dev-1.5)/1.0)
  return 0
  ```
  Waga w total score: dodaj jako 5-ty komponent z weight=1.0 (pozostałe 4 mają weight=1.0 każdy — do walidacji z Adrianem).

---

## Punkty do dyskusji z Adrianem (PRZED implementacją)

1. **R1 = 8 km czy 7 km?** p90=8.0, ale outlier 22 km sugeruje że p85=~7 byłby bezpieczniejszy. Do wyboru.
2. **R3 ABSOLUTE_CAP=5 czy 6?** Bartek w clean sample nigdy nie robi 6+, ale preliminary analiza wspomina "1 bundle rozmiaru 6". Sprawdzić źródło tej liczby.
3. **R4 waga** — 1.0 (równa pozostałym) czy 1.5 (priorytetyzacja bundlingu)? Dane mówią że bundling = -3 min delivery, więc waga 1.5 uzasadniona.
4. **R5 mixed-rest pickup spread 1.8 km** — to p100 Bartka. p90 = 0.45 km. Czy nie trzymać ściślej = 1.0 km? Wtedy szersze bundle z drugiej restauracji by były blokowane.
5. **Czy implementować R5 "never >45 min ETA" jako hard block** — to de facto R20 SLA guard z CLAUDE.md, może już istnieje w `feasibility_v2`?

---

## Sign-off

- [x] Adrian: akceptuję progi R1=8.0, R2=2.5, R3 tabela, R4=0.5/1.5/2.5, R5 constraint _(DONE F1.9, verified F2.1b test suite A1/A2/A4/A5)_
- [x] Adrian: odpowiedzi na 5 pytań powyżej _(DONE F1.9 sprint — R1=8.0 empirycznie p90 Bartka, R3 soft-only per F1.9b, R5=1.8 km p100 Bartka)_
- [x] Claude: implementacja w osobnym commicie, bez restartu serwisu bez potwierdzenia _(DONE F1.9 commit dde032f + F1.9b f24f0c9)_
- [x] Claude: shadow mode walidacja agreement rate przed promote do auto _(DONE F2.1b step 7 test B11 race condition guard, verified F2.1b prod 2026-04-15)_

**Status:** R1-R5 LIVE w `feasibility_v2` od F1.9 (13.04.2026). F2.1 Extensions R6-R9 DONE w F2.1b (15.04.2026). Patrz sekcja poniżej.

---

## F2.1 Extensions — Decision Engine 3.0 (R6-R9)

**Dodane:** 2026-04-15 w sprint F2.1b. Rozszerza Bartek Gold Standard (R1-R5) o cztery nowe reguły: R6 BAG_TIME termiczny, R7 long-haul peak isolation, R8 pickup span czasowy (DEFERRED F2.1c), R9 stopover + wait penalty.

### R6 — BAG_TIME termiczny (hard 35 + soft 30-35)

**Kalibracja empiryczna:** 743 delivered orderów 11-15.04.2026 (pre-F21b analyzer):
- p50: 15.1 min | p75: 23.0 | p90: 30.9 | p95: 35.6 | p99: 44.3 | max: 80.5
- 5.7% orderów > 35 min | 11.6% > 30 min

**Stałe (`common.py` F2.1 extensions):**
```python
BAG_TIME_HARD_MAX_MIN = 35          # = p95 empiryczne, obcina 5.7% thermal tail
BAG_TIME_SOFT_MIN = 30              # = p90, soft zone 30-35 łapie +5.9% penalty
BAG_TIME_PRE_WARNING_MIN = 30       # sla_tracker Telegram alert trigger
BAG_TIME_SOFT_PENALTY_PER_MIN = 8   # -(bag_time - 30) * 8 do scoringu
```

**Dwa enforcement points:**
1. **`feasibility_v2.check_feasibility_v2`** — hard reject przy projekcji > 35 min (po SLA check, reuse `plan.predicted_delivered_at`). Rejectuje SOLO i BUNDLE bez różnicy — jedzenie stygnie identycznie niezależnie od bag size.
2. **`dispatch_pipeline.assess_order`** — soft penalty w strefie 30-35 (reuse `metrics.r6_max_bag_time_min` ze step 3 feasibility, zero duplicate compute).

**Pre-warning alert:** `sla_tracker._check_bag_time_alerts` scan co 10s, wysyła Telegram do admina gdy `(now_utc - picked_up_at) > 30 min` AND `bag_time_alerted == False`. One-shot per order (flag setowany przed send — set-then-send Opcja X). Format:
```
⚠️ BAG_TIME N min (limit 30)
#<oid> <restaurant> → <delivery>
Kurier: <name> (<cid>) • picked up <HH:MM Warsaw>
```

### R7 — Long-haul peak isolation

**Reguła:** `ride_km > 4.5 km` AND `hour ∈ [14, 17] Warsaw` AND `bag niepusty` → hard reject `R7_longhaul_peak`. Solo (bag pusty) i offpeak bez ograniczenia.

**Stałe:**
```python
LONG_HAUL_DISTANCE_KM = 4.5
LONG_HAUL_PEAK_HOURS_START = 14   # inclusive, Warsaw local
LONG_HAUL_PEAK_HOURS_END = 17     # inclusive
```

**Status:** placeholder thresholds — brak danych empirycznych na `ride_distance` w shadow_decisions.

**Post-deploy monitoring z konkretnymi triggerami kalibracji:**
- reject rate > 20% w peak → threshold 4.5 km za restrykcyjny, bump w górę
- reject rate < 2% w peak → threshold za liberalny, bump w dół
- cel: 5-10% reject rate w peak hours (analogiczny do R1 current)
- miara: `grep "R7_longhaul_peak" shadow_decisions.jsonl | wc -l` vs total peak orders

### R8 — Pickup span czasowy (DEFERRED F2.1c)

**Planowane:** max 15 min różnicy T_KUR między orderami w `bundle=2`, max 30 min dla `bundle=3`.

**Dlaczego odroczone:** `_bag_dict_to_ordersim` w `dispatch_pipeline.py:105` NIE propaguje `pickup_ready_at` do `OrderSim` dla orderów w bagu. T_KUR deklarowany przez kuchnię jest znany tylko dla nowego ordera. Wymaga rozszerzenia + weryfikacji że panel_watcher zachowuje `pickup_at_warsaw` w bag dict kuriera.

**Placeholdery `common.py` w miejscu** (niewykorzystywane do F2.1c):
```python
PICKUP_SPAN_HARD_BUNDLE2_MIN = 15
PICKUP_SPAN_HARD_BUNDLE3_MIN = 30
PICKUP_SPAN_SOFT_START_MIN = 7
PICKUP_SPAN_SOFT_PENALTY_PER_MIN = 3
```

`shadow_decisions.jsonl` pole `bonus_r8_soft_pen` zostaje `null` do F2.1c (schema stabilne od step 0).

### R9 — Stopover tax + restaurant wait penalty

**R9 stopover (differential):**
```python
bonus_r9_stopover = -len(bag) * STOPOVER_SCORE_PER_STOP   # -8 per stop
# bag=0 solo → 0 (pierwszy stop free)
# bag=1 → -8, bag=2 → -16, bag=3 → -24, bag=4 → -32
```

**R9 wait:**
```python
wait_pred = max(0, (T_KUR - now) - drive_min_to_restaurant)
if wait_pred > 5:
    bonus_r9_wait_pen = -(wait_pred - 5) * 6   # -6 per min over 5
```

Oba soft-only. Dodawane do `final_score` przez `bonus_penalty_sum` w `dispatch_pipeline.assess_order`.

### Kolejność egzekucji w `feasibility_v2`

```
1. bag size cap (MAX=8)                   ← istniejące
2. R7 long-haul peak                      ← F2.1b NEW (fast, no OSRM)
3. R1 delivery spread (≤8km)              ← F1.9
4. R5 mixed-rest pickup (≤1.8km)          ← F1.9
5. pickup_dist reach (≤15km)              ← istniejące
6. shift_end guard (≥20min buffer)        ← F1.8
7. simulate_bag_route_v2 (OSRM)           ← istniejące
8. SLA violations (sla_minutes budget)    ← istniejące
9. R6 BAG_TIME hard (≤35min)              ← F2.1b NEW (po simulate, reuse plan)
10. return MAYBE / NO                     ← istniejące
```

### Race condition guard (krytyczny regression)

`COURIER_PICKED_UP` handler w `state_machine.update_from_event` **CELOWO NIE resetuje** `bag_time_alerted` do False. Panel_watcher reconcile może reemit ten event po tym jak sla_tracker już ustawił flag=True — reset powodowałby duplicate alerts w każdym ticku 10s do delivered. Reset odbywa się w `NEW_ORDER`, `COURIER_ASSIGNED`, `COURIER_DELIVERED`, `COURIER_REJECTED_PROPOSAL`, `ORDER_RETURNED_TO_POOL` — 5 z 6 handlerów. Regression guard: `tests/test_decision_engine_f21.py::test_B11`.

### Empirical milestones F2.1b

- **`bonus_l1 = 25.0` first production** — order #466122 Rany Julek, kurier 400 Adrian R, 2026-04-15 11:16:59 UTC. Pierwszy same-resto bundle z pełnym enriched_metrics breakdown zalogowany w shadow_decisions.jsonl (step 0 deployed).
- **R6 pre-warning first alert** — order #466154 @ 43.1 min w torbie, `alerted=True`. sla_tracker FAZA A live od 2026-04-15 12:56:04 UTC.

### Related docs

- `docs/TECH_DEBT.md` — F2.1b resolved + F2.1c backlog (R8, `_parse()` unified fix, learning analyzer bonus layer)
- `tests/test_decision_engine_f21.py` — 38 testów (A=6 regression, B=11 unit R6/R7/parse/race, C=3 bundle, D=5 edge, E=3 anti-pattern, F=10 sanity/smoke)
