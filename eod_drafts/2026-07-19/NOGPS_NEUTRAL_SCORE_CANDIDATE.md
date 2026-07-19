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

## v2 PO RECENZJI ADWERSARYJNEJ (2026-07-19, recenzent Sol/Codex → CTO zawęził do punktów POTWIERDZONYCH)

Recenzja adwersaryjna dała REJECT z 9 punktami; CTO zweryfikował je niezależnie
na żywym shadow (7 dni, 866 decyzji). v2 = ZMIANY tylko w punktach potwierdzonych
(#2 donorzy mediany, #3 edge-case'y, #9 testy funkcjonalne) + rozstrzygnięcia
projektowe pozostałych. Zero zmian post_wave/F2.1c, display F1.7, defaultów flag.

### ZMIANA v2: donorzy mediany = wykonalni konkurenci (pkt #2, POTWIERDZONY)

`_nogps_neutral_score_pass`, pętla `known_kms` (dispatch_pipeline.py):

- STARY warunek donora: `NOT road_km_from_synthetic_pos` AND `km_to_pickup` liczbowy.
- NOWY warunek donora: jak wyżej **AND `feasibility_verdict == "MAYBE"`**
  (kanoniczne pole werdyktu — to samo, którym filtruje selekcja
  `core/selection.py:98`). HARD-NO (post-shift, R-35MIN, pickup_too_far…) nie
  konkuruje o zlecenie, więc jego km nie zniekształca neutralnego dystansu.
- Fallback BEZ ZMIAN: 0 donorów (brak realnych kotwic ALBO wszystkie HARD-NO)
  → 5.0 km (mirror F1.7).
- Timing: ⚠ SPROSTOWANE w v3 (recenzja delty) — twierdzenie „pre_shift jako
  syntetyk i tak nie jest donorem" było FAŁSZYWE dla pre_shift Z KOTWICĄ
  (anchor/bag-tail ⇒ road realny ⇒ donor), a legacy F1.8e mutowało MAYBE→NO
  dopiero PO passie. Fix = hoist werdyktu przed pass (sekcja v3 niżej).
- CELE neutralizacji bez zmian (road syntetyczny + POSITION_UNKNOWN_SOURCES,
  niezależnie od werdyktu — shadow/score spójny w serializacji; NO i tak
  odfiltrowany w selekcji).

### TESTY v2 (pkt #3 + #9): edge-case'y + funkcjonalne E2E

Nowe w `tests/test_nogps_neutral_score_dist.py` (istniejące source-asercje
ZOSTAJĄ — dokumentują wpięcia; nowe testy są funkcjonalne):

- dokładnie 1 donor → mediana = jego km (3.7),
- donor z werdyktem NO wykluczony → mediana z samych MAYBE ({2,4} → 3.0, nie 4.0),
- wszyscy donorzy NO → 0 donorów → fallback 5.0 (neutralizacja dalej działa),
- cel neutralizacji z werdyktem NO → shadow/apply liczone (spójność serializacji),
- **E2E pass → selekcja** (`select_and_emit`, prawdziwy `dp.Candidate`, flagi jak
  live: EQUAL_TREATMENT + EQUAL_TREATMENT_BUCKET ON): OFF → verdict=PROPOSE,
  wygrywa no-GPS z centrum (bug zachowany bajt-w-bajt); ON → winner FLIP na
  GPS-bliskiego; HARD-NO ze score 120 NIGDY nie wygrywa (feasible=4/5);
  mediana w E2E = 4.0 (dowód, że HARD-NO 0.5 km nie zasilił mediany).

### ROZSTRZYGNIĘCIA pozostałych punktów recenzji (bez zmian kodu — uzasadnienie)

1. **post_wave (pkt #4 „krytyczna luka") = ŚWIADOMY WYJĄTEK projektowy F2.1c,
   NIE dziura.** post_wave to celowy bonus za powrót do centrum po fali —
   bramka F2.1c wymaga worka `len(bag_sim)>0` i WSZYSTKO picked_up (+ okno
   ≤30 min), więc **pusty worek nie może zostać post_wave** → główny wzorzec
   buga (blind+empty z centrum) NIE zmigruje do tej ścieżki. Empiria CTO
   (żywy shadow 7 dni, 866 decyzji, winner pos_source): no_gps 422 (51.5%),
   last_delivered 135, pre_shift 81, last_assigned_pickup 70, gps 47,
   last_picked_up_interp 43, **post_wave 21 (2.6%)**; z 421 wygranych cid
   179+413 post_wave to tylko **8**. Ewentualna neutralizacja post_wave =
   OSOBNA decyzja ownera (zmiana świadomej reguły F2.1c, nie bugfix).
   **Monitoring w 48h shadow: udział post_wave w zwycięzcach — wzrost po ON
   = sygnał migracji do wyjątku → alarm i eskalacja do ownera.**
2. **OFF-parytet (pkt #1): przy OFF zmienia się WYŁĄCZNIE telemetria shadow
   (`bonus_nogps_neutral_*`) — ZAMIERZONE** (wzorzec compute-always-for-shadow,
   lekcja #186: flip-walidacja ETAP-5 potrzebuje metryk PRZED flipem). Decyzja,
   score, ranking, display = bajt-parytet (testy OFF + pełna regresja 5205
   istniejących bez zmiany wyniku).
3. **Tie-break przy remisach (pkt #7): pre-existing** — stabilny sort
   `(-score, bundle_dev)` + kolejność `fleet_snapshot` istnieją sprzed
   kandydata; neutralizacja ich nie pogarsza (ta sama mediana ≠ identyczne
   score total — pozostałe komponenty różnicują). POZA ZAKRESEM tego
   kandydata; follow-up = osobny temat (neutralny tie-break w selekcji).
4. Display no_gps-z-kotwicą / pre_shift km=None (pkt #5): znana granica
   opisana wyżej (MAPA + „Znana granica") — pre-existing zachowania F1.7,
   świadomie nietykane w tym kandydacie.

### DOWODY v2 (harness jak wyżej: pkgroot-worktree + ZIOMEK_SCRIPTS_ROOT, -p no:cacheprovider)

- `tests/test_nogps_neutral_score_dist.py`: **23/23** (17 v1 + 6 nowych), w tym
  ON≠OFF (OFF bajt-parytet + shadow, ON apply/flip) i E2E OFF/ON przez selekcję.
- Pełna regresja: **5228 passed / 0 failed** (27 skipped, 8 xfailed, 316.8s)
  vs baseline kandydata v1 **5222/0** (27 skipped, 8 xfailed) —
  **DELTA = +6 (dokładnie nowe testy), 0 nowych faili, skip/xfail 1:1.**

### MONITORING FLIPU: twardy gate `donor_filter_match_rate` (narzędzie pomiarowe)

Narzędzie pomiaru cienia (scratchpad/nogps_measure/) po deployu przeliczy
NIEZALEŻNIE medianę donorów z zalogowanych decyzji i porówna z
`bonus_nogps_neutral_km`; **~100% zgodności = warunek ACK**.

⚠ ROZBIEŻNOŚĆ WZORÓW do świadomego rozstrzygnięcia w narzędziu (silnik jest
zgodny ze specyfikacją CTO — NIE zmieniać silnika pod narzędzie):

- SILNIK (kanon, ten kandydat): donor ⇔ `NOT metrics.road_km_from_synthetic_pos`
  AND `feasibility_verdict == "MAYBE"` AND km liczbowy.
- Wzór opisany dla narzędzia: `is_position_known(pos_source)` AND MAYBE.
  To NIE jest tożsame w 2 klasach brzegowych:
  1. **no_gps/pre_shift Z KOTWICĄ (anchor/bag-tail)**: road realny
     (`road_km_from_synthetic_pos=False`) → silnik: DONOR; `is_position_known
     ("no_gps")=False` → narzędzie: NIE-donor. Dodatkowo pętla display NADPISUJE
     ich `km_to_pickup` (no_gps→fleet_avg/mediana, pre_shift→None) PO passie —
     serializacja nie niesie surowego km donora → niezależna rekonstrukcja
     mediany z samego logu jest dla takich pul NIEMOŻLIWA wprost.
  2. **post_wave po PRZEMIANOWANIU** (F2.1c renames pos_source; raw źródło było
     Unknown, road z centrum): `road_km_from_synthetic_pos=True` → silnik:
     NIE-donor; `is_position_known("post_wave")=True` → narzędzie: donor.
- REKOMENDACJA dla narzędzia: liczyć donorów z serializowanego
  `road_km_from_synthetic_pos` (klucz w metrics kandydata) + `feasibility_verdict`,
  a pule z klasą brzegową 1 (donor bez surowego km w logu) wyłączać z gate'u
  albo raportować osobno — inaczej match_rate <100% będzie ARTEFAKTEM wzoru,
  nie błędem silnika. Decyzja o kształcie gate'u = team-lead/owner.

## v3 PO RECENZJI DELTY (2026-07-19, Sol REJECT z 1 blokerem — potwierdzony przez CTO)

Delta v2 (`88acde3..15ecc79`) zrecenzowana adwersaryjnie: pkt #3/#9 uznane za
naprawione (donor poprawny, E2E prawdziwy, scope czysty); JEDEN bloker.

### BLOKER: werdykt donora czytany PRZED finalizacją (pre_shift_too_late po passie)

`known_kms` filtrował po `feasibility_verdict` W CHWILI passu, a legacy F1.8e
(pętla display F1.7, gałąź pre_shift) mutowało pre_shift MAYBE→NO
(`_set_feasibility_verdict(c,"NO",layer="L5")`) DOPIERO PO passie. Obrona v2
(„pre_shift = syntetyk ⇒ nie-donor") była fałszywa: `core/candidates.py`
liczy `road_km_from_synthetic_pos = NOT is_position_known AND NOT
(anchor/bag-tail)` — ZAKOTWICZONY pre_shift ma road realny (synth=False),
więc przy MAYBE-w-chwili-passu BYŁ donorem → mediana mogła zawierać km
przyszłego HARD-NO.

### FIX v3: HOIST (decyzja CTO; jedno źródło predykatu)

- NOWY module-level `_pre_shift_too_late_verdict_pass(candidates,
  prep_remaining_min, order_id)` — predykat, komunikat i layer=L5 przeniesione
  1:1 z pętli display; wołany w `_assess_order_impl` PRZED
  `_nogps_neutral_score_pass` (razem z przeniesionym wyżej wyliczeniem
  `prep_remaining_min` — czysta arytmetyka z `pickup_ready_at`/`now`, nic
  między starą a nową pozycją jej nie czytało).
- Pętla display: gałąź pre_shift zachowuje WYŁĄCZNIE display (km=None,
  travel=shift_min, ETA=shift_start, marker v324a) — predykat USUNIĘTY
  (zero duplikacji; test pilnuje `count("shift_min > prep_remaining_min
  + 0.01") == 1`). Pass dalej biegnie przed pętlą display (asercja
  i_pass<i_loop bez zmian; display podąża za `bonus_nogps_neutral_applied`).
- Setter warstw: identyczne wywołanie (layer="L5" ⇒ garda L7.3 cicha,
  zachowanie settera niezmienione); MAPA zapisów w docstringu settera
  zaktualizowana o nową lokalizację.
- INWARIANT po v3: między `_nogps_neutral_score_pass` a `select_and_emit`
  NIE zachodzi żadna mutacja `feasibility_verdict` w `_assess_order_impl`
  — donor filter widzi werdykty FINALNE.
- DLACZEGO NIE „filtruj-w-passie" (przeliczanie predykatu pre_shift w donor
  filtrze): dublowałoby predykat w drugim miejscu (dryf = nowa klasa bugów),
  a mutacja po passie ZOSTAŁABY w kodzie — inwariant „pass widzi finalne
  werdykty" dalej fałszywy i kruchy na kolejne po-passowe mutacje.

### V3.24-A: osiągalność ścieżki mutacji w prodzie (LATENTNA)

- Default kodu: `ENABLE_V324A_SCHEDULE_INTEGRATION = env("...", "1") == "1"`
  → **ON** (common.py). Klucza NIE MA w flags.json (zweryfikowane) — to stała
  modułowa czytana z env przy imporcie, NIE hot-reload. Env-override w systemd
  RÓWNIEŻ zweryfikowany: `grep -r V324A /etc/systemd/system/` = brak (drop-iny
  dispatch-shadow ustawiają 5 innych flag, nie tę) ⇒ prod = default ON.
- Przy ON gałąź legacy F1.8e (jedyna po-passowa mutacja werdyktu) jest
  NIEOSIĄGALNA — pre-shift hard-reject >60 min egzekwuje warstwa B5
  feasibility PRZY BUDOWIE kandydata (werdykt finalny przed passem z natury).
- ⇒ Bloker w prodzie DZIŚ = **LATENTNY** (aktywowałby go env-override
  `ENABLE_V324A_SCHEDULE_INTEGRATION=0` per-service). Fix i tak obowiązkowy:
  latentna bomba + kontrakt „pass widzi finalne werdykty" musi być prawdziwy
  niezależnie od konfiguracji. Test kontraktowy dokumentuje default ON.

### TESTY v3 (nowe, w test_nogps_neutral_score_dist.py)

- kontrprzykład Sola: zakotwiczony pre_shift (synth=False) za-późny → NO przed
  passem → NIE-donor (mediana {4,6}=5.0, nie {1,4,6}=4.0),
- zdąży → MAYBE zostaje i JEST donorem (hoist nie nadgorliwy),
- V3.24-A ON → hoist no-op (B5 właścicielem odrzutu),
- spójność po odrzuceniu: zakotwiczony NIE-cel (score/metrics nietknięte),
  syntetyczny dalej cel (shadow+apply — serializacja spójna),
- wiring: hoist przed passem + predykat w module DOKŁADNIE RAZ,
- kontrakt defaultu V3.24-A ON (przesłanka latentności).
