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

- [ ] Adrian: akceptuję progi R1=8.0, R2=2.5, R3 tabela, R4=0.5/1.5/2.5, R5 constraint
- [ ] Adrian: odpowiedzi na 5 pytań powyżej
- [ ] Claude: implementacja w osobnym commicie, bez restartu serwisu bez potwierdzenia
- [ ] Claude: shadow mode walidacja agreement rate przed promote do auto

**Status:** Draft v1, czeka na review Adriana. **NIE zaczynamy KROKU 3 bez zielonego światła.**
