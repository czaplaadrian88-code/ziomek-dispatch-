# GEO-04: rekalibracja HAVERSINE_ROAD_FACTOR_BIALYSTOK — werdykt

**Data:** 2026-06-13 | **Sesja:** auton/geo-districts | **Stała:** `common.py:269 HAVERSINE_ROAD_FACTOR_BIALYSTOK = 1.37`

## Zarzut audytu
1.37 zaniżony dla realnego routingu fallback (haversine → road_km gdy OSRM padnie).

## Co robi stała
`feasibility_v2:61` i `osrm_client:373` i `dispatch_pipeline` (3 miejsca): `road_km = haversine_km × 1.37` — **ścieżka FALLBACK** używana gdy OSRM niedostępny do estymaty dystansu kurier→pickup / pickup→drop. Wpływa na ETA wszystkich kandydatów w trybie no-OSRM.

## Metodologia
Replikacja `calibration_20260412_baseline.json` (12.04, n=206, median=1.371): dla delivered orderów z `pickup_coords`+`delivery_coords` (z payloadów `NEW_ORDER` w `events.db`) policzono `factor = osrm_road_km / haversine_km`. OSRM :5001 żywy. Skrypt: `/tmp/geo04_recalib.py`; wynik: `/tmp/geo04_fresh_calib.json`.

Filtry jak baseline: bbox metropolii, `too_close` <0.5 km odrzucone, sanity 1.0 ≤ factor ≤ 5.0.

## Wynik (świeża próba, n=482 — 2.3× większa niż baseline)
| | median | mean | std | p10 | p25 | p75 | p90 | min | max |
|---|---|---|---|---|---|---|---|---|---|
| **Świeże (06-13, n=482)** | **1.390** | 1.489 | 0.329 | 1.207 | 1.284 | 1.586 | 1.916 | 1.046 | 3.992 |
| Baseline (12.04, n=206) | 1.371 | 1.468 | 0.354 | 1.197 | — | — | 1.825 | 1.08 | 3.702 |
| Stała w kodzie | 1.37 | | | | | | | | |

## WERDYKT: BEZ ZMIANY (1.37 zostaje)
- Świeża median 1.390 vs stała 1.37 = **różnica 0.020 = 1.4%** — w granicy szumu (std=0.329, czyli ±0.02 to ~0.06σ).
- Baseline median 1.371 → świeże 1.390 = system stabilny w ~12 mies., **brak trendu „zaniżania"**. p10/p90 niemal identyczne (1.197→1.207, 1.825→1.916).
- Zmiana 1.37→1.39 podniosłaby fallback ETA o 1.4% globalnie — pomijalny efekt operacyjny, a wprowadza ryzyko regresji w kilkudziesięciu testach które hardkodują oczekiwane km na 1.37 (`test_v327_drive_min_osrm`, `smoke_timing_gap_and_wave_pos`).
- **Zarzut „1.37 zaniżony" NIE potwierdza się danymi.** Mediana to dobry estymator dla fallbacku (odporna na ogon p90=1.92 wynikający z dojazdów przez rzekę/obwodnicę).

## Opcja dla ACK Adriana (gdyby chciał idealnego dopasowania)
Podbicie do **1.39** (świeża median, zaokrąglona) byłoby bardziej precyzyjne, ale to decyzja kalibracyjna (dotyka ETA fallbacku → R6 35-min gate → feasibility). Rekomendacja sesji: **nie ruszać** — korzyść 1.4% < ryzyko regresji testów + drift kolejnych kalibracji. Jeśli Adrian zdecyduje 1.39, to 1-linijkowa zmiana stałej + aktualizacja ~4 testów hardkodujących 1.37.

## ⚠ Uczciwość danych
- Próba świeża pochodzi z bieżącego okna events.db (rolluje — ~2 tyg.), więc to „teraz", nie cały rok. Sezonowość (zima/lato, korki) nieuwzględniona — ale baseline z kwietnia i świeże z czerwca dają niemal tę samą medianę, co sugeruje stabilność.
- factor liczony na delivered orderach (pickup→delivery), nie na legu kurier→pickup; zakładam tę samą charakterystykę road/haversine dla obu (uzasadnione: ta sama sieć dróg Białegostoku).
