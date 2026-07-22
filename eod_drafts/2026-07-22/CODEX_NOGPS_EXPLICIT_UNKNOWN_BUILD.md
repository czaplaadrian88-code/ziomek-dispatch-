# BUILD: no-GPS explicit-unknown-position

Data: 2026-07-22  
Branch: `nogps-explicit-unknown`  
Base: `f9cd49fab895941c4510a90b8289d10fe9616a6c`  
Zakres live: **brak** — bez merge, flipu, deployu, restartu i zapisu runtime.

## Wynik i mapa kompletności

| Warstwa | Writer / consumer | Zmiana | Dowód |
|---|---|---|---|
| Resolver / domena | `position_model.py:34`, `position_model.py:54`, `position_model.py:108`; `courier_resolver.py:855` | `ResolvedPosition` z jawnym provenance i `coords=None` dla UNKNOWN; `OriginTravelEstimate` 6,5 / 15 / 22. `KNOWN_ANCHOR` zachowuje realne coords. | Goldeny provenance, UNKNOWN bez coords, known no-op. |
| Scoring SOFT | `core/candidates.py:554`, `scoring.py:189` | UNKNOWN dostaje `road_km=6.5`, więc `s_dystans=100*exp(-6.5/5)≈27.25`; bearing jest nieewaluowalny, bez mediany puli i centrum. | `test_unknown_soft_constants_score_and_no_bearing`. |
| Feasibility / HARD | `feasibility_v2.py:463`, `feasibility_v2.py:471`, `feasibility_v2.py:676` | Pickup-reach widzi estymowane 6,5 km; metryka HARD = 22 min; R1/R5 origin geometry `False`. | Golden feasibility: 6,5 SOFT/reach, 22 HARD, brak geometrii origin. |
| Symulator SLA/R6 | `route_simulator_v2.py:253`, `route_simulator_v2.py:408` | UNKNOWN jest jednym wirtualnym pierwszym wierszem macierzy. Pierwsza noga ma 22 min; do OSRM idą wyłącznie realne pickup/drop coords. Kolumna powrotna do wirtualnego depotu = 0, więc brak drugiej fikcyjnej nogi. | Golden przechwytuje argumenty `osrm.table`; `plan.total_duration_min>=22`. |
| Chain ETA | `chain_eta.py:53`, `chain_eta.py:144`, `chain_eta.py:161`, `chain_eta.py:211` | Pusty bag: `max(now/available_from+15, scheduled)`. Pierwszy unpicked pickup: ≥15 min, potem realny łańcuch. `starting_point=unknown_profile`; zero centrum/OSRM/haversine dla origin. | Golden z funkcjami geo, które rzucają przy wywołaniu. |
| R29 SOLO | `core/selection.py:1190`, `core/candidates.py:1949` | Ten sam resolver/estimator; `100-6.5*10=35`. | Golden `r29_solo_score=35`. |
| Bundle / corridor | `core/candidates.py:284`; `feasibility_v2.py:477` | Realne pickup↔pickup/drop↔drop pozostają; origin-corridor UNKNOWN nie jest liczony, R1/R5 origin geometry = false, brak bonusu z fikcyjnego wektora. | Test feasibility + brak wywołania geografii origin. |
| Display | `core/candidates.py:1936`; `dispatch_pipeline.py:5125` | `km_to_pickup=None`, `estimated_road_km=6.5`, `estimated_drive_min=15`, `position_kind=UNKNOWN`, tekst „pozycja nieznana · dojazd szac. 15 min”. | Golden kontraktu display. Panel poza klonem: N-D, konsumuje kontrakt po osobnym review/deployu. |
| Flaga / konflikt | `common.py:353`, `common.py:1788`, `dispatch_pipeline.py:4664`; registry `tools/flag_lifecycle_registry.json:8766` | Nowa flaga default OFF, odczyt raz na decyzję. Konflikt ze starą flagą: ALERT, nowy model nieaktywny, legacy zachowany. Stara flaga `deprecated` + `superseded_by`. | Test strukturalny single-read/conflict; lifecycle checker 0 błędów. |
| Shadow / kontrfaktyk | `core/candidates.py:78`, `dispatch_pipeline.py:5209`, `shadow_dispatcher.py:1174` | Per-kandydat oba originy; per-decyzja oba winner/verdict. Kontrfaktyk biegnie przez prawdziwy `core.selection.select_and_emit`, nie `max(score)`. LOCATION A+B korzystają ze wspólnej propagacji metrics. | Serializer golden A+B; mutation probe: `max(score)` wybiera HARD-NO, selektor wybiera MAYBE. |

## Inwarianty

1. `ENABLE_NO_GPS_EQUAL_TREATMENT` nie jest zmieniany; nowy profil to koszt, nie demote ani bucket-kara.
2. Flaga OFF zachowuje legacy jako wynik główny; jawna telemetria shadow jest addytywna. Znana pozycja ma `origin_estimate_for(...) is None`, więc score/plan/feasibility idą starym torem.
3. HARD 22 jest w macierzy symulatora i feasibility, SOFT 15/6,5 w ETA/score/display.
4. UNKNOWN nie przekazuje coords do geografii origin; corridor origin nie jest oceniany.
5. Nie zmieniono `auto_assign_gate` ani authority — equal treatment nadal nie oznacza auto-assign.
6. Stara neutralizacja nie komponuje się z nową flagą; konflikt jest fail-closed.

## Bliźniaki N-D (dla bramki kompletności)

- N-D: auto_assign_gate.py — authority pozostaje bez zmian; nowy model dostarcza wyłącznie koszt/niepewność i nie może podnieść uprawnień auto-assign.
- N-D: drive_min_calibration.py — owner jawnie odłożył kalibrację; v1 ma zatwierdzone stałe Białystok.
- N-D: tools/reassignment_forward_shadow.py — to osobny workstream reassign; kandydat dotyczy pierwotnego dispatchu i nie zmienia kontraktu reassign.
- N-D: objm_lexr6.py — prawdziwy selektor konsumuje już przeliczone Candidate; model nie dodaje osobnej kopii rankingu OBJM.
- N-D: plan_recheck.py — recheck istniejącego worka wymaga realnej kotwicy z GPS/zdarzenia (`_start_anchor`); nie tworzy nowego unknown-origin. Wspólny symulator zachowuje legacy, gdy `origin_travel=None`.
- N-D: sla_anchor.py — kotwice SLA/R6 nie zmieniają semantyki; zmieniony jest wyłącznie koszt pierwszego wiersza przed istniejącą kotwicą.
- N-D: claim_ledger.py — shadow pozycji nie tworzy ani nie mutuje claimu.
- N-D: tools/pending_global_resweep.py — brak zmiany lifecycle/claim; resweep pozostaje osobnym konsumentem bieżących decyzji.

## Shadow: sposób odczytu

- Per kandydat (zarówno `alternatives[]`, jak i inline `best`): `position_model_shadow.position_kind`, `position_source`, `position_provenance`, `position_age_min`, `legacy_origin{...}`, `explicit_unknown_origin{...}`.
- Per decyzja: top-level `position_model_shadow.{legacy_winner_cid,explicit_winner_cid,would_change_winner,legacy_verdict,explicit_verdict,selector_path,flag_requested,flag_effective,flag_conflict}`.
- Kryterium ownera po review/deployu shadow: min. 300 decyzji albo 7 dni; winner-share U0 wobec G0 45–55%. Brak PII i współrzędnych w nowym payloadzie.

## Testy i delta

- `py_compile` 11 dotkniętych modułów: PASS.
- `tools/flag_lifecycle_check.py`: PASS, 523/523 curated, 0 błędów; panel/apka SKIP z powodu nieobecnych repo w sandboxie.
- Targeted regression: **59 passed / 0 failed** (11 nowych goldenów + 48 istniejących testów no-GPS, chain ETA, selection, feasibility-first i K16).
- `diff --check`: PASS.
- Pełna kanoniczna suita: **BLOCKED przez sandbox**, nie przez fail testu. `/root/.openclaw/venvs/dispatch/bin/python` zwraca permission denied (exit 126).
- Próba pełnej suity systemowym Pythonem zatrzymała się w kolekcji: 10 błędów + 4 skipy; brak siblingów `schedule_utils`/narzędzi, brak OR-Tools w systemowym Pythonie oraz zakaz odczytu root runtime/secrets. Wynik nie jest oracle regresji.
- Baseline klonu pod kanonicznym venv był z tego samego powodu niedostępny. Uczciwa delta pełnej suity: **N/D**; delta na dostępnym, dotkniętym klastrze: 0 fail po zmianie.

## Rollback

Przed live: flaga pozostaje OFF. Po ewentualnym wdrożeniu rollback zachowania: `flaga=false`, dokładnie `ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL=false` (bez kompozycji ze starą flagą). Rollback kodu = revert trzech commitów kandydata. Nie wykonano migracji danych ani zmian runtime.

Rollback: flaga=false; następnie git revert commitów kandydata, bez migracji danych i bez restartu w tym buildzie.

## OPEN QUESTIONS do CTO

1. Potwierdzić semantykę nazwy `pickup_too_far`: implementacja zachowuje istniejący limit 15 km, profil UNKNOWN przechodzi go jako 6,5 km, a 22 min jest HARD w tej samej feasibility i pierwszym wierszu SLA/R6. Czy owner oczekuje dodatkowego, osobnego progu czasowego `pickup_too_far`, czy właśnie tego kosztu w symulacji?
2. Shadow wykonuje drugi pełny eval tylko dla UNKNOWN oraz drugi prawdziwy selector. Przed wdrożeniem zmierzyć p95/p99 CPU/latency na reprezentatywnym rosterze; w razie przekroczenia budżetu utrzymać semantykę, ale przenieść kontrfaktyk do izolowanego workera z tym samym selektorem.
3. Zweryfikować, czy obserwacyjne hooki uruchamiane podczas drugiego eval (poza wyłączonym emit/classifier i capture bez coords) wymagają dodatkowego `shadow_only` guardu przed produkcyjnym deployem.
4. Panel/apka są poza klonem. Po akceptacji kontraktu osobny kandidat powinien jedynie renderować nowe pola, bez rekonstrukcji km jako faktu.

## Rekomendacja

Kandydat jest gotowy do niezależnego review kodu i architektury, ale **nie do merge/flip**, dopóki kanoniczny venv nie przeprowadzi pełnej regresji i CTO nie zamknie pytań 1–3. Następny bezpieczny etap: review bundle → pełna suita w środowisku z OR-Tools → benchmark shadow → dopiero osobny ACK na deploy (flaga nadal OFF).
