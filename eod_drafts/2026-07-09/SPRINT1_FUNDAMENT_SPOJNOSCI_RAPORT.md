# Sprint 1 — Fundament spójności decyzji i stanu

Data wykonania: 2026-07-09
Źródło: `ZIOMEK_BACKLOG.md`, sekcja 7
Baza kodu: `9ab4592`
Miejsce pracy: izolowany worktree `/root/sprint1_wt/dispatch_v2`

## Status

Implementacja Z-P0-03, Z-P0-02, Z-P0-04 i Z-P0-01 faza A jest gotowa,
przetestowana i ponownie wdrożona do live. Ostatni restart zakończył się
`2026-07-10 06:10:36 UTC`; `dispatch-shadow` i `dispatch-panel-watcher` działają
bez restart-loopa. Pełna chronologia deploy → rollback → ponowny deploy znajduje
się w `SPRINT1_DEPLOY_AND_ROLLBACK_REPORT.md`.

Techniczne Definition of Done jest spełnione. Operacyjne DoD pozostaje otwarte
wyłącznie o 48 godzin obserwacji shadow: od `2026-07-10 06:10:36 UTC` do
najwcześniej `2026-07-12 06:10:36 UTC`, przy ciągłości usług i poprawnych danych.
Egzekwowanie pozostaje wyłączone (`enforcement=NONE`); B-01/B-02 są otwarte.

## Etap 1 — kontrakt i granice

- Sprint usuwa utratę aktualizacji cache, stale write planów i pięć znanych faili
  baseline oraz dodaje neutralny decyzyjnie pomiar R6/R27/SLA.
- Nie egzekwuje R6/R27, nie włącza auto-assign i nie zmienia relacji HARD/SOFT.
- B-01 i B-02 pozostają nierozstrzygnięte; firewall raportuje oba warianty R27
  przy przeciążeniu i ma `enforcement=NONE`.
- Dane produkcyjne nie podlegają migracji. Rollback każdego zadania jest kodowy.

## Z-P0-03 — zielony baseline

Wykonano:

- dopisano `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK` do rejestru flag decyzyjnych i
  dokumentacji;
- testy working override odcięto od żywego `get_excluded_cids()`;
- checker dokumentacji flag obsługuje oficjalny `ZIOMEK_SCRIPTS_ROOT`, dzięki
  czemu testuje kod z worktree zamiast live repo.

Wpływ runtime: brak wpływu na decyzję i wartości flag. Zmienia się wyłącznie
kompletność fingerprintu/dokumentacji i determinizm testów.

Rollback: cofnąć zmiany w `common.py`, `ZIOMEK_LOGIC_REFERENCE.md`, checkerze i
fixture working override. Brak danych do odtwarzania.

## Z-P0-02 — wieloprocesowy geocode RMW

Wykonano:

- jeden stały `<cache>.lock` (0600) dla każdego z trzech cache'y;
- transakcję `LOCK_EX -> strict fresh load -> mutate/merge -> fsync -> replace ->
  directory fsync` dla adresów, restauracji, negative cache i GC;
- zachowanie trybu pliku i odmowę zastąpienia uszkodzonego JSON pustym obiektem;
- ponowną kontrolę pinów pod lockiem i ochronę pinów przed GC;
- wspólny protokół dla dwóch narzędzi maintenance;
- atomowy bootstrap restauracji z merge 3-way: baseline / wynik bootstrapu /
  świeży current; równoległa zmiana, dodanie lub usunięcie wygrywa;
- fail-soft publicznych geocoderów: błąd zapisu cache nie niszczy cache i nie
  odbiera callerowi świeżych, zweryfikowanych współrzędnych.

Dowód: deterministyczny test starego unikalnego tempfile odtwarza lost-update;
nowy protokół zachowuje oba wpisy dla wszystkich trzech cache'y. Testy obejmują
również pin-race, GC, corrupt JSON, maintenance i bootstrap.

Rollback: przywrócić poprzednie `geocoding.py`, `bootstrap_restaurants.py` i dwa
narzędzia maintenance. Nie ma migracji cache. Powstałe lockfile są bezstanowe i
mogą pozostać, ale powrót do starego kodu ponownie otwiera race lost-update.

## Z-P0-04 — CAS planów

Polityka konfliktu: **keep-current/skip**, bez ponawiania starego body. Następny
event/tick może policzyć plan od nowa ze świeżego stanu.

Wykonano:

- snapshot `plan_version` przed pulą kandydatów;
- propagację tokenu przez candidate, solo fallback, serializer i pending proposal;
- `expected_version` we wszystkich trzech produkcyjnych wywołaniach `save_plan`;
- typed `ConcurrencyError`, strukturalny log i procesowy licznik publikowany w
  summary watchera i rechecku;
- bump wersji przy każdej końcowej invalidacji, co blokuje stale resurrection;
- CAS także dla read-check-invalidate: mismatch w `load_plan`, BAG_CHANGED,
  GC, auto-invalidacja i GPS stale;
- odświeżenie planu po refloor przed `_check_plan`;
- brak fallthrough z konfliktu retime do full regen;
- token `0` w dwóch izolowanych timerach shadow po wyzerowaniu ich store'a.

Dowód: test writer A/B potwierdza, że nowszy writer B przeżywa spóźniony zapis A;
drugi test dowodzi tego samego dla spóźnionej invalidacji. Pokryto również watcher,
recheck po refloor, invalidacje terminalne i oba timery shadow.

Ryzyko obserwacyjne: decyzja dostaje jeden dodatkowy `load_plans()` przed pulą.
Po deployu należy obserwować latency i `plan_cas_conflicts`. Licznik jest procesowy,
więc agregacja odbywa się przez summary poszczególnych procesów.

Rollback: cofnąć zmiany w plan managerze, pipeline, watcherze, rechecku, core
candidates/selection i dwóch narzędziach shadow. Brak migracji pliku planów;
`plan_version` pozostaje monotoniczne i kompatybilne wstecz.

## Z-P0-01 faza A — invariant firewall shadow

Wykonano:

- czysty `core/invariant_firewall.py` bez aplikacyjnego I/O i mutacji wejścia;
- pojedynczy hook na samym końcu publicznego `assess_order`;
- `rule_verdict.v1` z R6, R27 i SLA, coverage `COMPLETE/PARTIAL/NONE`, jawnymi
  `UNKNOWN/NOT_APPLICABLE`, wyjątkami paczek, czasówek i pre-existing pickup;
- każde naruszenie ma `order_id`, `rule_id`, `value`, `limit`, `mode` i
  `exception_reason`;
- R27 jest mierzone symetrycznie jako ±5/±10 i oznacza kierunek early/late;
- R6 oraz READY-SLA liczą surowy elapsed przez kanoniczną kotwicę, bez ślepoty
  zaokrąglenia POD; NOW-SLA korzysta z `sla_anchor.now_anchor`;
- domena kontroli obejmuje mapy planu, sequence, wybrany worek, bag_context i
  nowe zlecenie; brak części planu daje PARTIAL/UNKNOWN;
- pełny fail-safe: błąd importu, evaluatora, typed fallbacku, loggera lub
  serializera nie zmienia ani nie wywraca decyzji i daje pełny UNKNOWN schema;
- serializację top-level w shadow record.

Faza A nie zmienia `verdict`, obiektu `best`, score ani sequence. B-01/B-02 są
zapisane w `policy_pending`; `enforcement=NONE`.

Rollback: usunąć końcowy hook i pole `PipelineResult.rule_verdict`, wpis
serializera oraz nowy moduł. Brak danych/migracji i brak flagi do cofania.

## Testy i kontrola statyczna

Baseline przed pracą:

- `4518 passed, 5 failed, 27 skipped, 8 xfailed, 2 xpassed`;
- pięć faili odpowiadało dokładnie karcie Z-P0-03.

Wynik końcowy pełnej suity:

- **4579 passed, 0 failed, 27 skipped, 8 xfailed, 2 xpassed**;
- czas: 121,90 s;
- 147 zastanych ostrzeżeń `PytestReturnNotNoneWarning`;
- AST parse: 1104 pliki Python — OK;
- `git diff --check` — OK.

Najważniejsze klastry celowane przed pełną suitą:

- geocode po cross-review: 61/61 PASS;
- CAS po cross-review: 68/68 PASS;
- firewall finalny: 28/28 PASS.

## Replay world records

Okno: ostatnie 24 h od `2026-07-08T21:34:32.094125+00:00`, pierwsze 50 rekordów
WR1 w oknie (`max_n=50`). Replay był wywołany bez funkcji zapisującej verdict do
live state.

Wynik worktree:

- n=50, zgodne=34;
- **0 różnic krytycznych** (`verdict`, `best_cid`, `best_score`);
- 14 różnic miękkich, wyłącznie `pool_total`;
- 16 rekordów z OSRM miss, 0 błędów, 0 brakujących zapisów;
- ogólny status narzędzia: `DIFFS` z powodu missów/miękkich różnic.

Ten sam korpus uruchomiony na niezmienionym HEAD dał dokładnie ten sam wynik i
identyczną listę 14 rekordów. Wniosek: Sprint 1 dodał **0 nowych różnic**, a
status `DIFFS` pochodzi z zastanej luki nagrania macierzy OSRM (w replayu komórka
float powoduje pominięcie jednego kandydata i niższy `pool_total`). Kryterium
neutralności decyzji firewalla jest spełnione; pełna bitowa bramka replay pozostaje
niezielona z przyczyny niezależnej od Sprintu.

## Incydent izolacji testu

Pierwsze uruchomienie istniejącego audytu geocode, przed poprawą fixture, utworzyło
pusty live `geocode_cache.json.lock`. Żaden cache JSON nie został zmieniony.
Lockfile usunięto po osobnym ACK użytkownika, a test przekierowano na `tmp_path`;
ponowne uruchomienia nie dotykały live cache.

## Ochrona danych i repozytoryjny handoff

`daily_accounting/kurier_full_names.json` pozostaje zastaną lokalną zmianą
użytkownika i jest jawnie wyłączony z selektywnego commita Sprintu. Pliku
`daily_accounting/kurier_full_names.json` nie otwierano bezpośrednio, nie
edytowano, nie kopiowano i nie przywracano; obejmowały go jedynie repozytoryjne
kontrole read-only (`git status` / `git diff --check`). Nie wykonano flipa flag,
migracji ani zmiany danych runtime.

Kod, testy, `ZIOMEK_BACKLOG.md` i oba raporty są zakresem handoffu na `master`.
Kopia rollbackowa 30 nadpisanych plików pozostaje w
`/root/sprint1_rollback_20260709_2140`.

## Stan po ponownym wdrożeniu i bramka obserwacyjna

Ponowny deploy po jednoznacznym ACK skopiował tę samą listę 36 plików; SHA-256
źródło/live było zgodne **36/36**, AST modułów produkcyjnych **11/11 OK**, a
`git diff --check` był czysty. Po restarcie:

- `dispatch-shadow.service`: active/running, PID `2959287`, start
  `2026-07-10 06:10:24 UTC`, `NRestarts=0`;
- `dispatch-panel-watcher.service`: active/running, PID `2959451`, start
  `2026-07-10 06:10:36 UTC`, `NRestarts=0`;
- plan-recheck, czasówka, bundle-calib-shadow i b-route-shadow: active/waiting.

Globalne failed units obejmowały `dispatch-night-guard.service` (ostatni run
`01:15:02`–`01:17:34 UTC`, przed ponownym deployem) oraz `ssh.socket`. Sprint nie
wykonywał operacji na tych jednostkach.

Minimalne kryteria 48-godzinnej obserwacji:

1. Każda decyzja ma parse'owalny `rule_verdict.v1`; brak final fallbacków bez
   rozpoznanej przyczyny.
2. Każda violation zawiera sześć wymaganych pól.
3. Coverage UNKNOWN/PARTIAL ma jawne `missing_reasons`.
4. Brak zmian w rozkładzie verdict/best wynikających z firewalla.
5. `plan_cas_conflicts`, latency assess i błędy geocode/cache są monitorowane.
6. Egzekwowanie pozostaje wyłączone do osobnych decyzji B-01/B-02 i ACK.
