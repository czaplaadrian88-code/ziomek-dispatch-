# BUG-D Faza 2c — Walidacja empiryczna additive traffic boost per distance bin

_Wygenerowano: 2026-06-13T15:00:45.007050+00:00  ·  okno: 14 dni  ·  wykluczenia skażonych okien: TAK_

**Flaga:** `ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST` — obecnie **OFF** (env override `=1`).  **Tool:** read-only, zero mutacji live.


## 0. Stan danych — co da się, a czego NIE da się zwalidować

Instrumentacja shadow (Faza 2b) loguje **predykcję** per leg (`best.traffic_v2_shadow_route`), ale **nie loguje realnego czasu przejazdu per leg**. Realne wyniki są tylko na poziomie zamówienia (pickup→delivery), a tablica legów to symulacja CAŁEGO planu TSP (16-24 legów nawet dla solo). W konsekwencji:

- decyzji w shadow z polem traffic_v2: **1,453** (z 1,534); z aktywnym boostem peak (max_v2_mult>1): **1,350**;
- realny join (predykcja↔realny czas) możliwy TYLKO order-level (`eta_calibration_log`: 1,114 matched; `drive_min_enriched`: 26 peak z realized);
- korelacja realized pickup→delivery vs predykcja per-order ≈ 0 (zmierzone: r≈0.02) — **legów NIE da się przypisać do realnego czasu**.

> **Wniosek metodyczny:** twarda walidacja per-bin („empiryczny optimum = X”) **nie jest dziś możliwa** z logowanych danych. Poniżej najlepsza analiza częściowa: rozkład firing + zewn. baseline TomTom + kierunkowy bias peak. Sekcja 5 mówi co dokładnie trzeba dologować, żeby Fazę 2c domknąć.


## 1. Rozkład boostu w shadow per bin (peak-boost-active)

Decyzje z aktywnym boostem peak: **1,350** (dominujący bin trasy: {'medium': 571, 'short': 570, 'long': 209}).

| Bin | obecny boost | legów (peak) | śr dystans | śr v1 mult | śr v2 mult | śr boost zastosowany | TomTom ref |
|---|---:|---:|---:|---:|---:|---:|---:|
| short | +1.00 | 11,085 | 1.33 km | 1.251 | 2.251 | +1.000 | 2.3 |
| medium | +0.40 | 18,964 | 3.31 km | 1.246 | 1.646 | +0.400 | 1.5 |
| long | -0.15 | 10,150 | 6.83 km | 1.242 | 1.106 | -0.136 | 1.15 |

## 2. Kierunkowy bias predykcji vs realny czas (eta_calibration_log)

`eta_error_min = predicted − real` (>0 = **PRZESZACOWANIE**). LIVE silnik = **v1** (flaga v2 OFF) — to bias v1; boost v2 dodawałby czas NA WIERZCHU. Sygnał agregatowy (NIE per-bin).

_Odsiano 1 rekordów |eta_error|>120 min (artefakt TZ/stale match — psuł mean offpeak)._

| Bucket | n | mean | median | p25 | p75 | n(solo) | median(solo) |
|---|---:|---:|---:|---:|---:|---:|---:|
| peak | 575 | +10.14 | +8.74 | 1.25 | 17.56 | 99 | 9.37 |
| shoulder | 429 | +10.33 | +7.87 | 1.1 | 16.9 | 55 | 7.75 |
| offpeak | 83 | +6.71 | +5.58 | -1.88 | 13.48 | 21 | 8.1 |

**Czytanie:** w peak predykcja (v1) ma medianę błędu **+8.74 min**. Przeszacowanie → dodawanie boostu v2 pogłębia błąd.

## 3. Route-level v2−v1 delta per dominujący bin (sanity)

Peak tras z realized p2d: **26**. Delta = ile minut v2 dodaje do CAŁEJ trasy (nie izolacja bina; homogeniczność <1 = trasa mieszana).

| Dominujący bin | n tras | śr route v2−v1 [min] | mediana realized p2d | homogeniczność |
|---|---:|---:|---:|---:|
| short | 16 | 44.27 | 3.3 | 0.64 |
| medium | 10 | 79.56 | 2.6 | 0.56 |
| long | 0 | — | — | — |

## 4. TABELA REKOMENDACJI per bin (deliverable)

| Bin | obecny boost | wynik. mult (przykł.) | TomTom ref | legów (peak) | pewność danych | kierunek empiryczny | rekomendacja |
|---|---:|---:|---:|---:|---|---|---|
| short | +1.00 | 2.3 | 2.3 | 11,085 | NISKA | peak PRZESZACOWANY (mediana +8.7 min) → boost agreguje ZA DUŻO czasu | NIE WŁĄCZAĆ bez per-leg walidacji (peak przeszac.) |
| medium | +0.40 | 1.7 | 1.5 | 18,964 | NISKA | peak PRZESZACOWANY (mediana +8.7 min) → boost agreguje ZA DUŻO czasu | NIE WŁĄCZAĆ (wynik +0.20 nad TomTom; peak już przeszac.) |
| long | -0.15 | 1.15 | 1.15 | 10,150 | NISKA | peak PRZESZACOWANY (mediana +8.7 min) → boost agreguje ZA DUŻO czasu | KEEP OFF; przy ew. flipie ten bin NAJBEZPIECZNIEJSZY (wynik≈TomTom, boost ujemny) |

_„wynik. mult (przykł.)” = przykładowa baza peak 1.3 + boost, floor 1.0 — rzeczywista baza jest godzinowa (tabela `V326_OSRM_TRAFFIC_TABLE`)._


## 5. Co dologować, żeby domknąć Fazę 2c (twarda walidacja per-bin)

Potrzebny **realny czas przejazdu per leg**, sparowany z predykcją tego lega:

1. **Per-leg realized** — dla każdego zrealizowanego segmentu pickup→drop (z GPS / kolejnych statusów) zapisać: `distance_km`, `bin`, `raw_min` (OSRM ff), `predicted_v1_min`, `predicted_v2_min`, **`realized_min`**, godzina UTC. Wtedy empiryczny optimum per bin = `median(realized_min / raw_min)` w peak.
2. Źródło realized: `eta_calibration_log` ma `picked_up_at`+`delivered_at` (solo = jeden segment) — rozszerzyć o `drop_distance_km`/`bin` z decyzji (dziś ich tam NIE ma — sprawdzone: 0 pól `*km`/`*bin`).
3. Alternatywa szybka: w `drive_min_enriched` dla **solo** dopisać `drop_leg_distance_km`+`drop_leg_bin`+`drop_leg_raw_min` z decyzji → porównać z `actual_pickup_to_delivery_min − dwell`. To da per-bin sygnał (short i tak będzie rzadki: solo<2km≈5 przypadków/2 tyg. — bundlują się).
4. Po ≥7 dniach takiego logu: ponowny run tego toola z trybem `--per-leg` (do dopisania) zwróci tabelę z empirycznym optimum + CI per bin.

**Effort dologowania:** ~2-3h (hook w sla_tracker/enricher, shadow-only, flaga). Potem ≥7d zbioru → Faza 2c zamknięta liczbowo.

---
_boost source: common (live) · flaga v2 ON: False_
