# KANDYDAT: ENABLE_NO_GPS_NEUTRAL_SCORE_DIST (2026-07-19) — PREP, NIE DEPLOY

Bug: memory `ziomek-nogps-center-score-bug-2026-07-19`. Kurier bez GPS planowany
w BIALYSTOK_CENTER (`courier_resolver.py` `_synthetic_pos_fallback`); fikcja
zasilała SCORE (`core/candidates.py` km_to_pickup_haversine → `scoring.py`
`s_dystans=100·exp(-road_km/5)`, waga 0.30 — near-ceiling przy centralnych
restauracjach + pusty worek maksuje pozostałe komponenty), a F1.7
(`dispatch_pipeline.py`) neutralizował TYLKO display PO zamrożeniu score.
Dane 16-19.07 (835 decyzji): no-GPS 24.8% puli → 50.5% zwycięzców; mixed-pool
win 66%; cid 179+413 = 50% propozycji #1. Regresja `ENABLE_NO_GPS_EQUAL_TREATMENT`
(demote zdjęty, ukryty bonus centrum został).

## FIX (u źródła; flaga OFF default; branch `fix/nogps-neutral-score-dist`)

1. `core/candidates.py` — KLASYFIKACJA U ŹRÓDŁA: nowa metryka
   `road_km_from_synthetic_pos` = (pozycja RAW kuriera Unknown wg F-3
   `is_position_known` — jedno źródło) AND NOT (anchor V326 / bag-tail nadpisały
   effective_start_pos realną kotwicą). Import `is_position_known` z
   courier_resolver (bez cyklu). Bag-tail dostał tracking `_v326_bag_tail_used`.
2. `dispatch_pipeline.py` — `_nogps_neutral_score_pass(candidates, order_id)`
   (module-level, testowalna): neutral_km = **MEDIANA** km_to_pickup kandydatów
   z road_km NIE-syntetycznym (fallback 5.0 = mirror F1.7); dla kandydatów
   `road_km_from_synthetic_pos AND pos_source ∈ POSITION_UNKNOWN_SOURCES`:
   - SHADOW ZAWSZE: `bonus_nogps_neutral_raw_km` (km z centrum),
     `bonus_nogps_neutral_km` (mediana), `bonus_nogps_neutral_dist_delta`
     (= W_DYSTANS·(s_dystans(mediana) − components.dystans)),
     `bonus_nogps_neutral_applied` (bool). Prefix `bonus_` ⇒ auto-serializacja
     L1.1 LOCATION A+B (deny-lista `_METRICS_EXCLUDE` nie zawiera kluczy).
   - APPLY za `C.decision_flag("ENABLE_NO_GPS_NEUTRAL_SCORE_DIST")`:
     `c.score += delta` + spójnie `metrics.score.components.dystans` i
     `metrics.score.total`. Wywołanie w bloku F1.7 PRZED pętlą display
     (shadow widzi surowe km) i PRZED `select_and_emit` (sort po `c.score`
     dziedziczy — zero osobnego re-sortu).
3. Display (JEDNA wartość napędza score i display):
   - branch `no_gps`: km = mediana gdy `bonus_nogps_neutral_applied`, inaczej
     legacy fleet_avg (OFF bajt-parytet),
   - branch `pre_shift`: km=None zostaje (jawny brak — score neutralizowany,
     display bez kłamstwa),
   - NOWY elif: pozostałe syntetyki (pin/none/post_shift_start_synthetic/
     working_override_synthetic) z applied → km = mediana (bez niego score
     =mediana a display=km-z-centrum byłby NOWYM rozjazdem). ETA/travel_min
     syntetyków NIETYKANE (osobna oś).
4. `common.py` — stała-fallback OFF + wpis `ETAP4_DECISION_FLAGS`
   (fingerprint/conftest-izolacja/registry).
5. `tools/flag_lifecycle_registry.json` — wpis lifecycle=shadow (checker
   `--repo-hermetic` zielony, 507 flag). `ZIOMEK_LOGIC_REFERENCE.md` — wiersz
   flagi (flag_doc_coverage nie krzyknie po flipie).
6. `tests/test_nogps_neutral_score_dist.py` — 17 testów (ON≠OFF funkcjonalnie,
   mediana-nie-średnia, anchor/post_wave/pre_shift/fallback, winner-flip,
   source-regression wpięć, brak kopii w plan_recheck, serializer deny-list).

## MAPA KOMPLETNOŚCI (klasa: scoring/selekcja pozycyjna — 8 bliźniaków z #0 + ścieżki)

| Miejsce | Dotknięte? | Dlaczego |
|---|---|---|
| `core/candidates.py` road_km→score | TAK (źródło) | klasyfikacja syntetyczności road_km + metryka |
| `dispatch_pipeline.py` F1.7 display no_gps (4680) | TAK | display=mediana przy applied; OFF=fleet_avg bajt-parytet |
| F1.7 display pre_shift twin (4695) | ŚWIADOMIE NIE | km=None to nie kłamstwo; score neutralizowany w pass |
| F1.7 pozostałe syntetyki | TAK (nowy elif) | bez tego nowy rozjazd score↔display |
| `_selection_bucket` (main/`_late_pickup_score_first_key`/`_best_effort_sort_key`/objm `bucket_fn`/resweep) | NIE (dziedziczy) | single-source bucket czyta pos_source (nietknięty); klucze sortu czytają `c.score` → widzą neutralizację |
| `_demote_blind_empty`/`_is_demotable_blind_empty` | NIE | pod equal-treatment ON ~inert; fix nie zmienia bucketów (kompozycja, nie zamiana) |
| `_best_effort_fastest_pickup_key` (SHADOW/LOG-ONLY) | NIE | ETA-based, nie czyta score; bucket z `_selection_bucket` |
| `objm_lexr6` (`bucket`/`lex_qual`/pick+shadow) | NIE (dziedziczy) | grupuje po tier×bucket wokół zwycięzcy score z posortowanej `feasible` |
| `plan_recheck.py` | NIE (zweryfikowane) | brak własnej kopii normalizacji (test source-assert: brak s_dystans/fleet_avg/nogps_neutral) |
| `tools/reassignment_forward_shadow` (duch przerzutu) | NIE (dziedziczy) | woła PRAWDZIWY `decide`/assess_order (l.338) — pass biegnie w środku; `_SYNTH_POS` tam = oznaczanie hipotetycznej floty, nie scoring |
| `auto_assign_gate` G7 (LATENTNE, ENABLE_AUTO_ASSIGN OFF) | NIE (dziedziczy) | `_quality_score` reuse `_gate_score_excluding_ranking_deltas(cand.score)` → widzi score po neutralizacji |
| `feed.py` konsola quality_reassign | NIE (dziedziczy) | konsumuje wynik forward_shadow |
| `drive_min_calibration` OFFSET no_gps+6.5 | NIE (świadomie) | oś ETA nie score-dystans; MAIN OFF, artefakt — „NIE flipować" (#0) |
| serializer LOCATION A+B | NIE (auto) | prefix `bonus_` = auto-propagacja L1.1; test deny-listy |
| `_GATE_RANKING_DELTA_EXCLUSIONS` / bramka MIN_PROPOSE | ŚWIADOMIE NIE | neutralizacja = korekta POMIARU wejścia (jak prawdziwy km GPS-a), nie kara rankingowa; bramka ma widzieć uczciwy score. Strażnik `test_inv_gate_score_delta` nie wymusza (apply poza wzorcem `final_score = final_score + var`). RYZYKO do pomiaru w cieniu: KOORD-rate (patrz plan) |
| cross-proces (shadow/czasówka/plan-recheck/forward-shadow) | SPÓJNE | decision_flag → wspólny flags.json hot-reload; flaga w ETAP4 ⇒ fingerprint + strip-izolacja testów |
| post_wave (F2.1c) | NIE (z konstrukcji) | ∉ POSITION_UNKNOWN_SOURCES; własny mechanizm wave_bonus |
| apka/panel (światy 2-3) | NIE | konsumują wynik silnika (proposal/plan); brak kopii s_dystans |

Znana granica (istniejąca dziś, nie pogorszona): no_gps z REALNĄ kotwicą
(anchor/bag-tail) przy ON trzyma realny score, a display dalej legacy fleet_avg
(minimalizm diffu; do ewentualnego domknięcia osobno).

## DOWODY (harness: pkgroot-worktree + ZIOMEK_SCRIPTS_ROOT, lekcja-ziomek-scripts-root-import)

- BASELINE master 7e57085: **5205 passed / 0 failed** (27 skipped, 8 xfailed), 339.8s.
- KANDYDAT (ten branch): **5222 passed / 0 failed** (27 skipped, 8 xfailed), 354.2s.
  **DELTA = +17 passed (dokładnie nowe testy), 0 nowych faili, skip/xfail 1:1** —
  OFF-parytet potwierdzony też przez 5205 istniejących testów bez zmiany wyniku.
- ON≠OFF: `tests/test_nogps_neutral_score_dist.py` 17/17 — OFF: score/ranking
  bajt-parytet + shadow policzony; ON: delta=W·Δs_dystans aplikowana, winner
  flip no_gps→GPS-bliski na puli wzorowanej na landslide 112-vs-4.1.
- Checkery: flag_lifecycle --repo-hermetic ✅ (507), flag_effect_coverage ✅
  (130 ETAP4, moja pokryta), flag_hygiene ✅ (bez nowych sierot).

## PLAN POMIARU SHADOW (po at#220; ZERO live do tego czasu)

Dane: `logs/shadow_decisions.jsonl` — po deployu kodu (flaga OFF!) każdy
kandydat no-GPS-synthetic niesie `bonus_nogps_neutral_*` w LOCATION A
(kandydaci) i B (best). Metodyka (narzędzie liczy OFFLINE, bez dotykania live):
1. **Kontrfaktyczny winner ON vs OFF** per decyzja: re-sort kandydatów po
   `score + bonus_nogps_neutral_dist_delta` (delta≠0 tylko dla neutralizowanych;
   pozostałe komponenty niezmienne) → `would_flip_winner`, `winner_on`.
2. Metryki docelowe (48h okno, min ~300 decyzji):
   - `nogps_winner_share_off` (dziś ~50.5%) vs `nogps_winner_share_on` —
     KRYTERIUM: ON zbliża się do udziału no-GPS w puli (~24.8%±5pp),
   - mixed-pool head-to-head win-rate no-GPS: 66% → ~50%±7pp,
   - koncentracja: udział top-2 cid (179/413) w propozycjach #1: 50% → spadek,
   - **BEZ REGRESJI**: (a) KOORD/cisza-rate nie rośnie >2pp (delta może zbijać
     poniżej MIN_PROPOSE — świadomie poza `_GATE_RANKING_DELTA_EXCLUSIONS`;
     jeśli rośnie → decyzja: wpis do exclusions przed flipem), (b) mediana
     km_to_pickup zwycięzców nie rośnie >10% (jakość dojazdu), (c) brak wzrostu
     `best_effort_low_score`.
3. Dodatkowy dowód POZYTYWNEGO wpływu (ETAP 5): na dniach z GPS-truth
   (fleet_position_history) porównać błąd |display_km − realny dojazd| dla
   neutralizowanych — mediana vs stare fleet_avg/centrum.
4. Kryteria FLIP: (2a)+(2b) w progach + winner-share trend ku puli → propozycja
   flipu do ACK ownera (dodanie klucza `ENABLE_NO_GPS_NEUTRAL_SCORE_DIST` do
   flags.json=true; rollback=false hot-reload, bez restartu).

## SEKWENCJA / ROLLBACK

Kandydat czeka: merge do master + deploy (restart dispatch-shadow) DOPIERO po
at#220 + ACK ownera (C2: flip = pełny deploy). Kod z flagą OFF jest inertny
poza dopisaniem metryk shadow. Rollback: flaga false (hot) / `git revert`
commitu kandydata. NIE flipować `ENABLE_NO_GPS_EQUAL_TREATMENT` off — odwrotna
nadkorekta (wraca stara kara).
