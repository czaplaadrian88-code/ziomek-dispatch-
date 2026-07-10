# Sprint 3 — prawda ETA i obserwowalność (Faza A)

Status: implementacja Fazy A jest zintegrowana i zweryfikowana w izolowanym
worktree. Nie było merge do `master`, deployu, restartu, migracji, flipu flag
ani zapisu do stanu runtime. Pełna regresja po ostatniej zmianie jest zielona;
commit implementacyjny został wypchnięty wyłącznie na izolowaną gałąź.

## Tożsamość pracy i bramki

- Zakres: Z-P1-02, Z-P1-03 i Z-P2-06.
- Base SHA bezpiecznego `master`: `c2bde5894976eea9e186336453d8bcaeec1d2489`.
- Branch: `sprint3/eta-observability-osrm`.
- Worktree: `/root/sprint3_wt/dispatch_v2`.
- Commit implementacyjny: `e48b21e` (`feat(observability): add ETA truth and
  OSRM telemetry`), push do `origin/sprint3/eta-observability-osrm`: wykonany.
- Handoff (`ZIOMEK_BACKLOG.md` i ten raport) jest wersjonowany w osobnym
  commicie `docs(sprint3): record phase-a evidence and rollout gates` na tej
  samej izolowanej gałęzi.
- Po utworzeniu worktree `master` przesunął się do `3c43573` wyłącznie o
  dokumentację Sprintu 2 (`CODEMAP`, `ARCHITECTURE` i raporty). Nie było
  nakładania z write-setem Sprintu 3; zachowano jawny base zamiast wciągać
  cudzy handoff w trakcie pracy.
- Obszary Sprintu 2 sesji 54 — event retry/DLQ, FSM/state machine,
  `panel_watcher.py`, `parcel_lane_merge.py` i courier API/auth — nie zostały
  dotknięte.
- Produkcja pozostaje bez zmian. Dispatch, panel i courier API nie były
  restartowane ani przeładowywane; flagi i pliki runtime nie były modyfikowane.
- Nowa obserwowalność i health nie są aktywne w produkcji; enforcement pozostaje
  nieobecny. Nie zmieniono żadnej efektywnej wartości flagi.
- Zastany chroniony dirty file
  `daily_accounting/kurier_full_names.json` w głównym repo pozostał nietknięty;
  nie stage'owano również innych zastanych/generated zmian użytkownika.

Kanoniczny baseline na dokładnym base SHA:

```text
4710 passed, 24 skipped, 10 xfailed, 147 warnings in 143.19s
```

## Problem i dowód przed zmianą

### Z-P1-02 — brak jednego kontraktu prawdy ETA/SLA

Legacy `tools/eta_truth_map.py` łączy predykcje z klikami pickup/delivery i może
wybrać predykcję powstałą po przypisaniu. Nie jest więc niezależnym oracle dla
oceny ETA sprzed decyzji. Audyt writerów wykazał dodatkowo, że repo nie zawiera
historycznie wersjonowanego zdarzenia potwierdzającego fizyczny pickup ani
przekazanie przesyłki klientowi.

Dostępne sygnały GPS są słabsze semantycznie:

- historyczne pole `restaurant_dwell.departed_restaurant` jest ostatnim punktem
  GPS nadal wewnątrz geofence restauracji, a nie potwierdzonym wyjściem,
  wyjazdem ani odebraniem jedzenia;
- delivery GPS potwierdza przyjazd pod adres, a nie handoff klientowi.

Dlatego Faza A nie nazywa tych zdarzeń „fizycznym pickupem” ani „fizyczną
dostawą”, nie definiuje KPI i nie promuje nowego ETA.

### Z-P1-03 — `latency_ms` nie obejmował pełnego lifecycle

Dotychczasowy `latency_ms` obejmował tylko część obsługi pojedynczego eventu.
Nie było wspólnego kontraktu dla wieku i oczekiwania kolejki, pobrania floty i
stanu ticka, fan-outu kandydatów, OSRM, solvera, selekcji, budowy rekordu,
appendu ledgera, efektów po zapisie i ACK. Suma pracy kandydatów/OSRM/solvera
nakłada się w wątkach i nie może być traktowana jak ścieżka krytyczna.

### Z-P2-06 — fallback i cache udawały zdrowie upstream

Dotychczasowy `health_check()` opierał wynik na publicznych `route()` i
`table()`. Te ścieżki mogą zwrócić poprawnie ukształtowany cache albo fallback,
więc awaria realnego upstream mogła nadal dawać zielone `osrm_ok`. Brakowało
jednoznacznej provenance wyniku oraz pomiaru contention i ewikcji cache.

Legacy cache przy limicie sortuje całość pod wspólnym `RLock` i usuwa 10%
najstarszych wpisów. Baseline syntetycznej pełnej ewikcji wynosił:

```text
route cache 5k:   median 0.486 ms, p95 0.725 ms, max 0.895 ms
table cache 50k: median 6.593 ms, p95 9.397 ms, max 9.814 ms
```

To jest znany koszt, nie naprawiony w tej fazie. Próba inkrementalnej ewikcji
O(1) została wycofana, ponieważ zmieniała zbiór zachowanych kluczy, a podczas
awarii mogłaby zmienić cache na fallback i tym samym wynik decyzji.

## Zachowanie po zmianie

- Powstał offline, wersjonowany bundle dataset/manifest/report dla tego samego
  okna, kohorty, anchoru predykcji i obserwowalnych zdarzeń GPS.
- Shadow otrzymał addytywną telemetrię czasów etapów i kolejki. Nie dodano
  limitów, sleepów, odrzucania pracy, alertów ani backpressure.
- Każdy wynik OSRM deklaruje źródło `upstream`, `cache` albo `fallback`, bez
  zmiany wartości duration/distance i bez zmiany kompatybilnego boola
  `osrm_fallback`.
- Health bada upstream bezpośrednio; cache i fallback nie mogą go zazielenić.
- Polityka cache, scoring, feasibility, selection i HARD/SOFT pozostają bez
  zmian. Replay nie wykazał różnicy krytycznej w decyzjach, ale jeden miękki
  licznik puli nie ma byte-parity i jest opisany jako ryzyko timing-sensitive.

## Kontrakty Fazy A

### `eta_truth.dataset.v1`, `eta_truth.manifest.v1`, `eta_truth.report.v1`

- Okno jest jawne, UTC i półotwarte: `[start, end)`; `as_of >= end`.
- Bazowy membership kotwiczy `sla.button_delivery_at`. Jest to stabilny anchor
  kohorty, nie delivery truth.
- Assignment anchor wymaga `actual_cid` i akcji z allowlisty:
  `PANEL_OVERRIDE`, `PANEL_AGREE`, `ASSIGN_DIRECT`, `F7AGREE`.
- Predykcja jest ostatnim shadow istniejącym nie później niż czas decyzji, w
  którym występuje faktycznie przypisany kurier. Nie ma fallbacku na rekord po
  przypisaniu ani skanowania do wygodniejszego kandydata.
- Restauracja używa wyłącznie `_source=gps_geofence`, zgodnego kuriera i
  wymaganego confidence. Schema nazywa drugie zdarzenie
  `restaurant_last_inside_at`; nie udaje ono pickup ani departure.
- Delivery używa najpierw przyjazdu z geofence aplikacji, potem wyłącznie
  wysokiej pewności rekonstrukcji serwerowej. Schema nazywa je
  `delivery_arrival_at`; nie udaje handoffu.
- Klik pickup/delivery pozostaje jawnym proxy i nigdy nie wypełnia braku GPS.
- Błąd ma znak `actual - predicted`; raport podaje osobno coverage, `n`, MAE,
  mean/median bias, p10/p90 oraz wspólny complete-case support.
- Każda metryka używa tego samego `denominator_base`; support ma hash związany
  z dokładną bazową kohortą.
- Paczki rozpoznaje wyłącznie kanoniczny `common.is_paczka_order(address_id)`.
  `address_id` może pochodzić tylko z SLA albo ostatniego shadow przed
  assignment. Nieznana klasyfikacja nie jest zgadywana i blokuje KPI.
- `canonical_kpi_event=unbound`, a progi promocji pozostają puste.
- Dataset nie emituje surowych order/courier ID, nazw, adresów ani GPS; outputy
  mają tryb `0600`.
- Outcomes i GPS są czytane rotation-aware. Stat/hash wejścia jest porównywany
  przed i po odczycie; zmiana źródła przerywa generowanie.
- Niewersjonowane whole-map `restaurant_dwell` i `courier_ground_truth` mają
  scope hasha `full_snapshot_nonversioned`. Dataset-effective hash dla samego
  historycznego `as_of` jest jawnie niedostępny; generator fail-loud odrzuca
  snapshot starszy niż mtime źródła.
- Manifest zaczyna jako `complete=false`, a kompletny manifest z hashami
  datasetu i raportu jest publikowany jako ostatni element bundle.

Legacy `tools/eta_truth_map.py` oraz jego konsumenci load-aware nie zostały
zmienione ani przepięte na nową semantykę.

### `decision_timing.v1` i `decision_stage_timing.v1`

- `DecisionTrace` używa `perf_counter_ns`, jest fail-soft i jest dołączany po
  podjęciu decyzji, aby selection nie mogła go konsumować.
- Rozłączne odcinki głównego wątku obejmują assess/impl/effects/post-hooks,
  prepare, top-level pre-recheck, setup i wall fan-outu, post-pool, selection,
  budowę rekordu, pre/post-ledger effects, append, ACK, service i event E2E.
- `fanout_wall_ms` jest ścieżką krytyczną. `candidate/osrm/solver work_sum_ms`
  są nakładającą się pracą i nie są do niej addytywne.
- Wszystkie strategie solvera przechodzą przez jeden top-level span; próby
  ORTools są detalem, bez podwójnego odejmowania czasu.
- Workerzy kandydatów jawnie wiążą `ContextVar`, ponieważ pula wątków nie
  dziedziczy go automatycznie.
- Fallbackowy pre-recheck per kandydat ma jawne agregaty: liczba wywołań,
  work-sum i work-max.
- Tick zapisuje minimalny marker `open` przed odczytem queue/fleet/state oraz
  marker `complete` z outcome counters. Crash pomiędzy nimi jest widoczny.
- Queue raportuje event age, batch wait/index, głębokość, najstarszy event,
  koszt zapytania i źródło. `atomic=false` jest jawne; clock skew jest liczony,
  lecz wyłączony z percentyli wieku.
- Wspólne fleet/state/poll są mierzone raz na tick. `queue_tick_timing` trafia
  do głównego ledgera raz na tick, a decyzje odwołują się do niego.
- Main ledger i sidecar łączą się po pseudonimowym `event_ref`; decyzje i
  lifecycle ticka łączą się po `tick_ref`. Nie ma joinu po surowym `event_id`.
- Sidecar zapisuje po ACK rzeczywiste czasy append/ACK/E2E i marker complete;
  marker open jest osobnym appendem. Sidecar nie zawiera surowych ID.
- Reader kotwiczy mianownik w głównym ledgerze dla `[since, until)`, stosuje
  margines na granicach i raportuje missing/orphan/duplicate sidecar rows,
  incomplete ticks oraz inwarianty outcome counters.
- Pełny panel ingress pozostaje jawnie niedostępny jako
  `no_pre_fetch_anchor`, bo zmiana `panel_watcher.py` kolidowałaby z sesją 54.
- Telemetria tylko obserwuje; nie wymusza budżetów ani backpressure.

### `osrm_health.v1` i `osrm_telemetry.v1`

- `osrm_source=upstream|cache|fallback` oraz `osrm_degraded` są addytywne;
  legacy `osrm_fallback` jest ich kompatybilną projekcją.
- Wszystkie numeryczne duration/distance i traffic multiplier pozostają bez
  zmiany.
- Direct probe bada strict route, table `2x1` i nearest bez cache, fallbacku i
  circuit breakera. Nie otwiera, nie zamyka ani nie resetuje operacyjnego CB.
- `upstream_ok` i kompatybilne `osrm_ok` pochodzą wyłącznie z direct probe.
- Payloady `code=Ok` są nadal walidowane strukturalnie i liczbowo; malformed
  response nie daje fałszywego sukcesu.
- Health ma `state_scope=process_local`, PID i rolę procesu. Direct upstream
  truth jest prawdą o backendzie, ale cache/CB pokazywane przez CLI należą do
  świeżego procesu reportera i nie opisują pamięci dispatch daemona.
- Rzeczywisty proces importujący klienta emituje godzinne
  `osrm_telemetry.v1`: hit/miss/expire/set/eviction, czas ewikcji, lock
  wait/hold, źródła wyników, próby/sukcesy/failure/timeout/rejected upstream,
  latency i dokładne przejścia circuit closed→open.
- Snapshot/reset liczników jest wykonywany pod lockiem, ale I/O po zwolnieniu
  locka; cache i stan CB nie są resetowane.
- Per-decision timing otrzymuje pracę OSRM, źródło, operację oraz osobne koszty
  lock wait i eviction.
- Ewikcja pozostaje dokładnie legacy: pełny sort po timestampie i batch 10%.
  Test retained-key-set oraz oracle cache-vs-fallback podczas awarii chronią
  parytet decyzji. Optymalizacja O(1) nie jest częścią tej zmiany.

## Mapa kompletności Z-P1-02

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód | Test / dowód |
|---|---|---|---|---|---|
| `sla_log.jsonl` | membership i click proxy | SLA writer / dataset v1 | TAK, read-only | wspólny mianownik i `[start,end)` | end-exclusive, as-of |
| `shadow_decisions.jsonl` | predykcja przed decyzją | shadow serializer / dataset v1 | TAK, read-only | blokada post-assignment leakage | mutation probe i future-shadow oracle |
| `decision_outcomes.jsonl` | assignment anchor | timer/legacy writers / dataset v1 | TAK, read-only | jawna akcja, czas i `actual_cid` | allowlista, schema i rotations |
| `restaurant_dwell.json` | arrival i last-inside | zewnętrzny detector / dataset v1 | TAK, read-only | najsilniejszy dostępny sygnał restauracji | source, confidence, courier match |
| `courier_ground_truth.json` | primary delivery arrival | courier app / dataset v1 | TAK, read-only | bezpośredni geofence | precedence i as-of |
| `gps_delivery_truth.jsonl` | secondary delivery arrival | validation rebuild / dataset v1 | TAK, read-only | wyłącznie high-confidence fallback | confidence i rotations |
| `common.is_paczka_order` | wykluczenie paczek | SLA/preassignment shadow / dataset v1 | TAK | kanon bez zgadywania kohorty | package/unknown oracle |
| `customer_dwell` | surowy upstream | detector / `gps_delivery_truth` | N-D | dataset konsumuje zatwierdzony indeks | parity/precedence indeksu |
| `tools/eta_ground_truth.py` | dataset, manifest, report | offline CLI | TAK | jeden kontrakt i lineage | unit, CLI E2E, fault injection |
| `tools/eta_truth_map.py` | legacy click/proxy | load-aware replay/calibrate | N-D | aktywni konsumenci wymagają starej semantyki | exact base diff/import smoke |
| ETA live, flags i kalibracja | decyzje produkcyjne | runtime | N-D | brak KPI i osobnego ACK | brak zmian w diffie |
| dashboard/alert | konsument KPI | brak zatwierdzonego writera | N-D | event/progi nadal `unbound` | raport blokuje werdykt |

## Mapa kompletności Z-P1-03

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód | Test / dowód |
|---|---|---|---|---|---|
| `observability/stage_timing.py` | kontrakt i agregacja | pipeline/OSRM/solver / serializer | TAK | jedno źródło matematyki | fake clock, overlap, fail-soft |
| `shadow_dispatcher.py` | queue/fleet/write/ACK/E2E | tick / ledger+sidecar | TAK | pełny outer lifecycle | tmp E2E, open/complete, join |
| `dispatch_pipeline.py` | impl/recheck/fan-out/selection/effects | dispatcher / trace | TAK | właściwe granice stage | partition i unattributed |
| `core/candidates.py` | worker timing | thread pool / trace | TAK | jawne wiązanie ContextVar | isolation i parallelism |
| `route_simulator_v2.py` | solver timing | planner strategies / trace | TAK | wszystkie strategie jednym seamem | solver calls i attempts |
| `osrm_client.py` | nested OSRM/cache work | route/table / trace | TAK | wspólny seam z Z-P2-06 | provenance i lock/eviction |
| `core/selection` | selection implementation | pipeline / decyzja | N-D | mierzona na granicy caller | selection wall parity |
| `courier_resolver` | fleet snapshot implementation | tick / pipeline | N-D | mierzony raz na granicy caller | shared-per-tick oracle |
| `core/jsonl_appender` | fizyczny append | dispatcher / ledger+sidecar | N-D | outer span mierzy cały append | rzeczywisty sidecar timing |
| `event_bus` | depth i event timestamps | panel/tick / queue metrics | N-D | plik Sprintu 2; użyte istniejące API | depth source/atomic/skew |
| `panel_watcher.py` | pre-fetch ingress | panel / event bus | N-D | jawna kolizja z sesją 54 | `no_pre_fetch_anchor` |
| `tools/stage_timing_report.py` | reader i denominator | ledgery / offline report | TAK | realny konsument metryki | boundary grace, loss/orphan/duplicate |
| alert/backpressure | enforcement | brak | N-D | poza Fazą A i bez ACK | brak symbolu decyzyjnego |

## Mapa kompletności Z-P2-06

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód | Test / dowód |
|---|---|---|---|---|---|
| route/table return paths | wartości i provenance | OSRM client / solver+world record | TAK | źródło każdego wyniku | upstream/cache/fallback |
| route/table cache get/set | cache i contention | OSRM client / telemetry | TAK | hit/miss/expiry/set/lock/eviction | warm, mixed, retained-key parity |
| fallback/invalid coords | degraded serving | OSRM client / legacy consumers | TAK | jawna provenance bez zmiany liczb | failure i numeric parity |
| circuit breaker | proces-local degraded state | OSRM client / health+telemetry | TAK | odróżnienie serving od upstream | down, open, recovery, transition |
| direct route/table/nearest probe | control-plane truth | health / operator | TAK | bypass cache/fallback/CB | mutation-free i malformed payload |
| `osrm_telemetry.v1` | licznik procesu | realny importer / log+stage reader | TAK | brak martwej metryki | snapshot/reset i hourly emit |
| `tools/osrm_health_report.py` | read-only CLI | direct probe / operator | TAK | jawny `process_local` | text/JSON contract |
| `tools/osrm_fallback_smoke.py` | smoke consumer | OSRM telemetry / operator | TAK | źródło i exact CB transition | failure/recovery smoke test |
| `route_simulator_v2.py` | legacy bool consumer | solver / decyzja | N-D semantycznie | kompatybilny bool pozostaje | replay i fallback parity |
| world-record/replay | decision parity | recorder / porównanie | TAK, addytywnie | provenance bez zmiany decyzji | dwa replaye po zmianie |
| timer, unit, drop-in, restart | aktywacja runtime | systemd / proces | N-D | wymaga osobnego ACK | brak operacji live |

## Zmienione pliki

Kod produkcyjny i obserwowalność:

- `core/candidates.py`
- `dispatch_pipeline.py`
- `observability/stage_timing.py`
- `osrm_client.py`
- `route_simulator_v2.py`
- `shadow_dispatcher.py`

Narzędzia read-only/offline:

- `tools/eta_ground_truth.py`
- `tools/osrm_health_report.py`
- `tools/osrm_fallback_smoke.py`
- `tools/stage_timing_report.py`

Testy i dokumentacja:

- `tests/test_eta_truth_map_contract.py`
- `tests/test_osrm_health_cache_zp206.py`
- `tests/test_stage_timing_zp103.py`
- `tests/test_stage_timing_report_zp103.py`
- `docs/eta/06_ground_truth_contract.md`
- `eod_drafts/2026-07-10/SPRINT3_PHASE_A_REPORT.md`
- `ZIOMEK_BACKLOG.md`

Nie zmieniono `tools/eta_truth_map.py`, plików sesji 54, plików flag ani
chronionych danych użytkownika.

## Testy, replay i pomiar read-only

### Testy i checkery

Końcowa pełna regresja po wszystkich zmianach, w tym agregatach
`candidate_pre_recheck`:

```text
4784 passed, 24 skipped, 10 xfailed, 147 warnings in 122.78s
```

Skupiony klaster integracyjny oraz dokładny zestaw nowych testów:

```text
81 passed in 5.32s
74 passed in 3.46s
```

`git diff --check` oraz `py_compile` wszystkich 14 zmienionych modułów Python
przeszły. Entropy dashboard zachował parytet z baseline: `377`, `17`, `~13`,
`25/49`, `1`, `7`, `13`, `11+4`, `10`. Sześć z tych wartości jest oznaczonych
przez narzędzie jako statyczny `AUDIT-BASELINE`; dynamiczne dead-flag i sentinel
również nie wzrosły. Jest to dowód braku wykrytej regresji, nie dowód spadku
entropii.

Testy uruchamiano kanonicznym interpreterem z venv, z
`DISPATCH_UNDER_PYTEST=1`, `ZIOMEK_SCRIPTS_ROOT=/root/sprint3_wt` i
`PYTHONPATH=/root/sprint3_wt`. Worktree-rootowy symlink `flags.json` służył
wyłącznie bootstrapowi harnessu; testowy `conftest` izolował flagi i stan, a
symlink nie jest plikiem repo ani zmianą produkcyjną.

### World replay na stałym korpusie

Okno: `2026-07-09T08:00Z..2026-07-10T08:00Z`, `n=202`.

| Przebieg | Zgodne | Miękkie różnice | Krytyczne różnice |
|---|---:|---:|---:|
| exact base, run 1 | 177 | 23 | 0 |
| exact base, run 2 | 177 | 23 | 0 |
| po zmianie, run 1 | 177 | 23 | 0 |
| po zmianie, run 2 | 178 | 22 | 0 |

W każdym przebiegu było także `OSRM misses=24`, `missing writes=0`, `errors=0`.
Oba przebiegi base były identyczne: jedna historyczna miękka różnica miała
`pool_feasible replay=8` wobec zapisanego `7`. Po zmianie ten sam typ pola dał
odpowiednio `6` w run 1 i `7` w run 2. Nie zmieniły się wybór, werdykt ani inne
pola klasyfikowane przez bramkę jako krytyczne, ale nie ma byte-for-byte parity
całego payloadu soft. Ponieważ base był w dwóch przebiegach stabilny, a wynik
instrumentowany nie, narzut/scheduling instrumentacji jest wiarygodnym
kandydatem przyczyny. Nie jest to dowód zmiany decyzji biznesowej, ale jest to
jawna bramka do ograniczenia narzutu i 48-godzinnego canary przed rolloutem.

### ETA ground truth — live inputs, wyłącznie odczyt

Offline CLI uruchomiono na produkcyjnych wejściach tylko do odczytu; artefakty
trafiły do prywatnych plików w `/tmp`, nie do runtime. Okno pomiaru:
`[2026-07-09T08:00Z, 2026-07-10T08:00Z)`.

```text
base denominator po jawnym wykluczeniu rozpoznanych paczek: 188
package classification coverage: 183/194 = 94.330%
rozpoznane i wykluczone paczki: 6
package status: unresolved — KPI zablokowany

restaurant_last_inside error:
  n=99/188 = 52.660%
  MAE=209.033 min
  mean bias=-196.296 min
  median bias=+4.267 min, p10=-3.369, p90=+15.827

delivery_arrival error:
  n=86/188 = 45.745%
  MAE=242.470 min
  mean bias=-225.383 min
  median bias=+5.666 min, p10=-7.316, p90=+20.686

complete-case obu nóg: 78/188 = 41.489%
restaurant arrival coverage: 164/188 = 87.234%
restaurant last-inside coverage: 163/188 = 86.702%
delivery arrival coverage: 135/188 = 71.809%
```

Bardzo duże MAE przy znacznie mniejszych medianach nie jest błędem definicji
metryki: w korpusie znajduje się co najmniej jedna skrajna predykcja z terminem
około 14 dni po obserwowalnym zdarzeniu. Rekord pozostaje pseudonimowy w
artefakcie. To luka jakości istniejącej predykcji do osobnego audytu, nie
podstawa do ręcznego usunięcia outliera ani promocji ETA.

### OSRM direct health i benchmark narzutu

Read-only CLI uruchomione w osobnym procesie zwróciło
`schema=osrm_health.v1`, `state_scope=process_local` oraz bezpośredni sukces
route/table/nearest (`upstream_ok=true`). Cache i circuit reportera były puste,
co jest oczekiwane dla świeżego PID i nie opisuje stanu dispatch daemona.

Syntetyczny benchmark bez sieci i bez runtime wykazał:

```text
cache hit current: median/p95/max 4.419/5.428/8.709 us
delta median do lokalnej ścieżki legacy: +3.972 us/op

eviction current:
  route 5k:   0.295/0.336/0.503 ms
  table 50k: 4.173/4.974/6.390 ms

paired current vs legacy-shaped reference:
  route median +3.4%, table median +9.1%
  retained-size parity: true/true

sidecar append do /tmp, tryb 0600:
  batch 1:  0.034/0.045/0.739 ms
  batch 10: 0.066/0.084/0.206 ms
  batch 50: 0.284/0.328/0.352 ms
```

Wyniki ewikcji nie dowodzą optymalizacji: polityka nadal jest `O(n log n)`, a
różnice względem historycznego baseline zależą od szumu hosta. Paired benchmark
pokazuje mały, ale realny koszt telemetrii. Wartości sidecara potwierdzają mały
koszt batcha w izolacji; nie zastępują pomiaru w rzeczywistym procesie.

## Ryzyka i luki danych

1. Nie istnieje potwierdzony fizyczny pickup ani customer handoff. Last-inside
   i arrival są obserwowalnymi proxy GPS, nie mocniejszą prawdą.
2. Pokrycie GPS jest niepełne i nielosowe. Complete-case 41.489% nie może być
   traktowany jako reprezentatywna populacja bez analizy biasu braków.
3. Klasyfikacja paczek ma 94.330% coverage. Nieznane rekordy pozostają w bazie,
   a status `unresolved` blokuje KPI; nie wolno ich po cichu nazwać gastronomią.
4. Niewersjonowane whole-map mają hash pełnego snapshotu, ale nie mają
   dataset-effective historycznego hasha. Historyczny replay przed mtime
   słusznie fail-loud zamiast udawać rekonstrukcję.
5. Skrajna predykcja około 14 dni w przyszłość dominuje mean/MAE i wymaga
   audytu źródła predykcji; kontrakt celowo jej nie obcina.
6. Pełny wiek panel ingress nie jest dostępny bez wejścia w `panel_watcher.py`.
   Zamiast kolizji z sesją 54 raport emituje jawne `no_pre_fetch_anchor`.
7. Sidecar nie ma jeszcze zatwierdzonego logrotate/retention ani kill-switcha.
   Każdy niepusty tick wykonuje osobny append `open` i końcowy batch po ACK.
   I/O jest fail-soft, ale koszt i wzrost pliku muszą zostać zmierzone przed
   live activation.
8. CLI health pokazuje bezpośrednią prawdę upstream, lecz jego cache/CB są
   `process_local` dla świeżego PID. Prawdziwy stan daemona będzie widoczny
   dopiero z jego godzinnej telemetrii po zatwierdzonym wdrożeniu.
9. Pełny sort ewikcji table cache pozostaje potencjalnym spike pod lockiem.
   Zmiana retained-key set może zmienić cache/fallback podczas awarii, więc
   optymalizacja wymaga osobnego projektu z równoważnym oracle lub flagą.
10. Dwa stabilne replaye base i dwa zmienne replaye po zmianie mają zero różnic
    krytycznych, ale wskazują timing-sensitive drift jednego pola miękkiego.
    Pełny payload parity nie jest udowodniony; rollout wymaga canary oraz limitu
    dopuszczalnego narzutu.
11. Nie ma jeszcze pomiaru z rzeczywistego procesu po wdrożeniu ani 2-dniowego
    okna obserwacji. Faza A nie może ogłosić wpływu produkcyjnego.

## Wymagane decyzje biznesowe

Przed jakąkolwiek kalibracją lub promocją ETA Adrian musi zatwierdzić:

1. czy KPI pickup ma oznaczać last-inside, przyszły potwierdzony exit, pickup
   operacyjny czy inny event;
2. czy KPI delivery ma oznaczać arrival pod adres czy potwierdzony handoff;
3. produktowy anchor predykcji: przed decyzją, pierwszy po przypisaniu czy live;
4. politykę nieznanej klasyfikacji paczek i minimalne package/GPS coverage;
5. KPI i progi MAE/bias/tail, minimalne `n`, sposób traktowania skrajnych
   predykcji oraz koszt uboczny na wspólnym support.

Przed aktywacją obserwowalności potrzebny jest osobny ACK na deploy i restart
oraz decyzja operacyjna o:

- retencji/logrotate sidecara i kill-switchu;
- dopuszczalnym narzucie I/O/CPU;
- sposobie odczytu stanu właściwego dispatch PID;
- ewentualnej zmianie polityki ewikcji cache. Taka zmiana nie należy do Fazy A.

## Proponowany bezpieczny etap wdrożenia

1. Po wykonanej zielonej pełnej regresji, static checks, entropy i audycie
   kolizji utrzymać commit/push wyłącznie na izolowanej gałęzi; nie merge'ować
   go do `master` bez osobnego etapu review.
2. Przed live activation dodać zatwierdzoną retencję/logrotate sidecara i
   kill-switch obserwacji, a następnie powtórzyć testy oraz benchmark appendu i
   contention. To jest osobna zmiana/ACK, nie wykonana w tej fazie.
3. Po osobnym ACK wdrożyć wyłącznie obserwację poza peakiem, bez zmiany flag
   decyzji, ETA, backpressure i cache policy. Wykonać jeden kontrolowany restart
   właściwego procesu i sprawdzić PID, `NRestarts`, health, ledger-sidecar join
   oraz proces-local telemetry.
4. Obserwować minimum 48 godzin od wdrożenia. Werdukt ma obejmować coverage
   sidecara, incomplete ticks, stage/queue percentyle, OSRM source/error/cache
   contention oraz rozmiar i tempo wzrostu logu. Deadline będzie `T+48h` od
   faktycznie zatwierdzonego startu; dziś nie ma daty, bo nie było deployu.
5. ETA pozostawić offline i measurement-only aż do zatwierdzenia semantyki KPI,
   kohorty, coverage i progów. Żadna liczba tego raportu nie jest promocją.

## Rollback

- Kod: jawny `git revert e48b21e` (oraz osobny revert commita handoffu, jeśli
  potrzebny), bez resetu i bez naruszania zmian sesji 54.
- Runtime: po osobnym ACK wyłączyć zatwierdzonym kill-switchem obserwację albo,
  jeśli nie zostanie dodany, wdrożyć revert i wykonać jeden kontrolowany restart
  właściwego procesu poza peakiem.
- Dane: brak migracji. Sidecar i offline bundle są addytywne; rollback nie
  wymaga przepisywania ledgerów ani danych biznesowych.
- Backup: nie powstały dane runtime wymagające kopii. Dokładny base SHA,
  izolowana gałąź i worktree są punktem powrotu dla kodu; przed ewentualnym
  deployem obowiązuje ponowny backup zgodny z ETAPEM 6.
- OSRM: revert usuwa provenance/telemetrię, a legacy wartości, fallback, cache
  policy i CB pozostają takie jak na base.
- Próba powrotu: kanoniczny full suite, replay na tym samym korpusie, health,
  PID/`NRestarts` i brak nowych wpisów sidecara po wyłączeniu.

Rollback jest przygotowany jako plan, ale nie był wykonywany na produkcji,
ponieważ Faza A nie dokonała żadnej operacji live.
