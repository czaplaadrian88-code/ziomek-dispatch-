# At-gate authority, durable receipt, ledger v4 i kolejka Fable — evidence

Status: `HOLD — ROOT FIX, FRESH BLIND I FULL REGRESSION GREEN; COMMIT/LIVE/E2E PENDING`

Data: 2026-08-01 UTC

Model tier / effort wykonawcy: `sol / ultra` — współbieżność, trwały ledger,
migracja SQLite i bramka wydania mają wysoki koszt błędu.

Worktree: `/root/worktrees/dispatch_v2/active/20260801-at-gate-execution-claim`

Branch/base: `fix/at-gate-execution-claim-20260801` /
`cfbfe8e92a99f0d6ad0de53fdcff476381772b86`

Historyczny commit `40daa2d603ddc6efd6cbd743f833521681103166` nie
zawiera późniejszych root-fixów i nie może zostać wdrożony samodzielnie.

## Zakres i źródła defektów

Dotknięte źródła repo:

- `tools/process_debt_gate.py` — jedyny owner ledgera, claimów i publicznego DTO;
- `tools/at_gate.py` — jedyny owner planowania, uruchomienia, capture i cancel;
- `tools/process_debt_collect.py` — read-only inwentaryzacja kolejki bez drugiej
  polityki missing/cancel;
- trzy odpowiadające pliki testów;
- ten raport evidence.

Oddzielny, zamrożony katalog kolejki Fable:
`/root/artifacts/session284-sessions274-280-review-queue-20260730T124826Z`.

Potwierdzone źródła problemów:

1. Wykonanie childa mogło rozpocząć się bez atomowego, jednorazowego RUN claimu.
2. Child i jego potomkowie dziedziczyli pliki outputu. Receipt mógł zostać
   sfinalizowany, a potomek dopisywał później bajty i unieważniał dowód.
3. CANCEL opierał poprawność na `atrm <numeryczne-id>`. GNU `at` może ponownie
   użyć ID, więc retry po crashu mógł usunąć cudzy, nowszy job.
4. Jeśli `atd` uruchomił wrapper przed commitem `SCHEDULED`, wrapper blokował
   childa, lecz po późniejszym submission-cancel sealed payload mógł zostać
   osierocony.
5. Niezerowy durable result po zmianie wersji bramki mógł zakończyć job jako
   `FAILED` bez trwałego ALARM-u.
6. Szeroka migracja legacy, niepełne exact-schema, publiczne ścieżki/bindingi i
   surowe `sqlite3.Error` tworzyły nierozliczalne albo fałszywie zielone stany.
7. Kolejka audytu Fable miała drugi launcher/politykę schedulera, lokalnie
   rekonstruowała claim i nie wiązała wszystkich wykonywalnych bajtów.

## Kanoniczny kontrakt po root-fixie

### RUN, output i receipt

- `schedule` zapisuje auth2 sealed payload `0600` w katalogu `0700`; spool `at`
  zawiera tylko ścieżkę do payloadu.
- Outputy są tworzone prywatnie przed claimem. Awaria pre-open nie ustanawia
  authority i nie uruchamia childa.
- `GateStore.claim_at_job` pod `BEGIN IMMEDIATE` sprawdza token/HMAC, exact argv,
  payload identity, wyprowadzony `artifact_root`/receipt, czas oraz aktywną
  bramkę bez alarmu. RUN i CANCEL wygrywają ten sam pojedynczy rekord claimu.
- `transition` i `note` są zamrożone w fazach `CLAIMED`/`RECEIPT_READY` RUN.
  Po commicie claimu wrapper wykonuje jeszcze kanoniczne
  `verify_active_run_claim` przed granicą childa; drift oznacza zero childa,
  `OUTCOME_UNKNOWN` i ALARM.
- Child dostaje wyłącznie `subprocess.PIPE`; jedynym writerem plików
  `stdout.bin`/`stderr.bin` jest wrapper z równoległym selectorem.
- Receipt może powstać dopiero po zakończeniu bezpośredniego childa i EOF obu
  pipe'ów. Potomek trzymający FD ponad bounded grace, błąd odczytu albo overflow
  daje `OUTCOME_UNKNOWN` + ALARM + rc 125, bez receiptu i bez `FINALIZED`.
- Exact receipt poświadcza `child_started`, `direct_child_exit_observed` oraz
  obowiązkowe `stdio_eof_observed=true`. Nie poświadcza zakończenia dowolnego
  drzewa daemonów.
- FSM: `CLAIMED → RECEIPT_READY → FINALIZED`. Istniejący poprawny receipt może
  sfinalizować DB bez re-exec; brak receiptu po claimie nigdy nie pozwala na
  automatyczny drugi start.

### CANCEL i lifecycle payloadu

- Numeryczne ID `at` nie jest authority do destrukcji. Produkcyjny cancel i
  submission rollback nie wywołują `atrm`; zapisują atomowy logiczny tombstone.
- Retry aktywnego i terminalnego CANCEL jest exact DB no-op. Późniejszy wrapper
  widzi claim CANCEL, wykonuje zero `Popen` i usuwa tylko payload o związanym
  device/inode/ctime/size.
- Wyścig early-runner jest rozliczony dwoma porządkami: jeśli CANCEL wygra
  pierwszy, payload sprząta późniejszy wrapper; jeśli runner wcześniej zapisze
  `EARLY_RUNNER_ABORTED`, bit jest związany w claimie tej samej transakcji i
  payload sprząta finalizer submission-cancel.
- Collector widzi tombstone jako zarejestrowany tylko dopóki istnieje jego exact
  sealed payload o tym samym device/inode/ctime/size. Podmiana, symlink,
  katalog albo brak pliku powodują `AT_JOB_UNREGISTERED`. Po GC ponownie użyte
  numeryczne ID jest obce, nigdy podstawą kasowania.

### Ledger, migracja i eksport

- Schema v4 dopuszcza tylko claim statuses `CLAIMED`, `RECEIPT_READY`,
  `FINALIZED`, `OUTCOME_UNKNOWN`; `LEGACY_HOLD` nie istnieje.
- Numer v4 jest celowo nowy: historyczny, niewdrożony commit `40daa2d603dd`
  używał niezgodnego v3. Kandydat nie otwiera żadnego v3; live v2 migruje
  bezpośrednio do v4, a obce/stare v3 zatrzymuje się fail-closed.
- Migracja v2→v4 jest atomowa tylko przy pustej tabeli legacy claims. Każda
  niepusta tabela `at_job_claims` w v2 — także z driftowaną listą kolumn —
  zatrzymuje się pod write lockiem przed pierwszym `CREATE/ALTER/DROP` i
  pozostawia v2 do osobnej adjudykacji. Nie ma cichej konwersji ani re-exec.
- Exact-schema obejmuje wszystkie user tables/indexes/triggers/views, PRAGMA
  columns/FK/index metadata i tokenizowany SQL bez sklejania granic/literałów.
  Manifest schematu wchodzi do prywatnego `ledger_hash`.
- Niezależny, przypięty SHA expected-manifestu blokuje samowalidację mutable
  `SCHEMA`. Publiczny verifier otwiera jeden read snapshot, sprawdza realny
  `PRAGMA user_version=4` i exact schema przed odczytem job/claim/gate; zwraca
  rzeczywisty `job.auth_version`, nie stałą.
- Publiczny eksport ma osobny `export_format_version=2` i deny-by-default
  allowlisty. Nie publikuje DB/pathów, payload/artifact/receipt identity,
  pełnych bindingów, metadata ani snapshotów; publikuje tylko bezpieczne pola i
  ich digesty.
- `StorageError(GateError)` jest domenową granicą SQLite. Runner/reconcile nie
  importują ani nie interpretują surowych wyjątków backendu.
- Niezerowy durable result zawsze latchuje ALARM na bramce nieterminalnej, także
  po gate-version drift; nowsze `state/blocker/next_step/code/evidence/metadata`
  są zachowane. ALARM blokuje promocję/CLOSED poza jawnym reject/supersede.
- `OUTCOME_UNKNOWN` i stale reconcile dla `CLAIMED`/`RECEIPT_READY` także
  zachowują nowsze pola operatorskie po driftowanym writerze.

### Kolejka Fable

- Publiczny model-bearing entrypoint to wyłącznie `execute`; nie ma drugiego
  pinned launchera ani `mark-scheduled`.
- Pierwsza faza `execute` jest read-only i wymaga actual argv identycznego z
  jednym `build_execution_argv`, hash środowiska `AT_GATE_COMMAND_SHA256` oraz
  sanitized 14-key attestation z `GateStore.verify_active_run_claim`.
- Kolejka nie czyta `receipt_path`, `show_at_job`, `show_at_claim` ani prywatnego
  bindingu. Ledger jest jedyną scheduler truth.
- Claude, Python, Git, blind driver/selftest i queue selftest są związane przez
  SHA + device/inode/ctime/size/mode/resolved path i retained FD. Brak fallbacku
  do niepoświadczonej ścieżki.
- Exact Q1/Q5 pathsets są porównywane z rzeczywistym `git diff`, nie tylko z
  deklarowaną liczbą plików.
- `QUEUE_STATUS.state=REMEDIATION_REQUIRED`, a stary `PREFLIGHT.json` v2 jest
  celowo stale. Queue pozostaje fail-closed aż do provider deploy, no-model E2E
  i świeżego preflight v3.

## Mapa kompletności

| miejsce | rola | writer/consumer | wynik | oracle |
|---|---|---|---:|---|
| `at_gate.schedule` | sealed auth2 + submission | writer | TAK | accepted-ID/nonzero, post-commit retry, early runner |
| `GateStore.register/confirm/fail_at_submission` | intencja | writer | TAK | atomowy SUBMITTING/SCHEDULED/tombstone |
| `GateStore.claim_at_job` | RUN authority | writer | TAK | auth/time/argv/path/gate tamper = zero child |
| `_execute_with_owned_streams` | direct child + capture | writer | TAK | delayed writer, lingering FD, overflow, PIPE ratchet |
| receipt writer/loader | durable wynik | writer/consumer | TAK | exact shape/identity + jawne EOF attestation |
| recovery/reconcile | bez re-exec | writer/consumer | TAK | receipt recovery i OUTCOME_UNKNOWN |
| normal/submission CANCEL | authority | writer | TAK | RUN↔CANCEL, reused ID, zero `atrm` |
| payload GC | private data lifecycle | writer | TAK | cancel-first i early-runner-first |
| failure latch + transition | gate FSM | writer | TAK | drift zachowuje pola, ALARM blokuje promocję |
| v2→v4 migrator | schema writer | writer | TAK | empty PASS; exact i drifted nonempty fail-before-DDL |
| exact schema/hash | attestation | consumer | TAK | index/FK/CHECK/trigger/token mutation |
| public DTO | operator/export | consumer | TAK | marker redaction + exact keysets/version |
| SQLite boundary | storage errors | writer/consumer | TAK | connect/commit/rollback/close/integrity→StorageError |
| collector | inwentaryzacja atq | consumer | TAK | exact tombstone identity/reused ID/symlink/dir/brak |
| queue `execute` | model authority | consumer | TAK w kodzie | 43 selftests; real no-model E2E po deployu |

## Aktualne dowody testowe

- Focused ledger/scheduler/collector: `56 passed in 7.00s`; exit 0.
- Queue remediation: `43 tests in 0.184s`; exit 0.
- `ziomek-blind-review/selftest.sh`: 8/8 PASS, `SELFTEST OK`.
- `ziomek-cto/selftest.sh`: wszystkie bramki scope/DoD/brief/handoff PASS.
- `py_compile` dotkniętych narzędzi/testów: PASS.
- `git diff --check`: PASS.
- Finalna kanoniczna pełna regresja z worktree i aktualnym pkgroot:
  `6399 passed, 24 skipped, 8 xfailed, 0 failed, 149 warnings in 418.84s`;
  exit 0.
- Świeży scratch migration z kopii live wykonanej SQLite Backup API:
  `/root/artifacts/session284-at-gate-migration-rehearsal-20260801T1505Z-v4/`;
  `user_version 2→4`; przed i po `gates=109`, `gate_events=434`, `at_jobs=6`,
  `at_job_claims=0`; stabilne tabele mają identyczne SHA przed/po;
  `integrity_check=ok`; FK violations 0; oba pliki mode `0600`.
- Szeroki przebieg z błędnego pkgroot CWD nie jest bramką: `6384 passed`, 5
  `FileNotFoundError` dla istniejących względnych plików. Wymagany jest świeży
  kanoniczny `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`
  uruchomiony z worktree.
- Trzy świeże, niezależne review zakończyły się finalnym `GO` na dokładnych
  końcowych SHA: ledger/provider/evidence, queue↔provider oraz incremental
  collector/test. Ich kolejne reprodukcje wcześniej wykryły i wymusiły: truth
  `auth_version`, drifted-nonempty pre-DDL, schema/user_version snapshot w
  verifierze i behawioralny ratchet SHA tombstone. Historyczne zielone lub
  czerwone werdykty nie autoryzują bieżącego kandydata.
- Pierwszy poprawny-CWD pełny przebieg został świadomie przerwany po nowym
  defekcie przy `5777 passed, 23 skipped, 7 xfailed`; drugi po kolejnym
  defekcie przy `4107 passed, 21 skipped, 6 xfailed` i jednym niezależnym
  solverowym failu. Ten node po przerwaniu przeszedł `5/5` osobnych powtórzeń.
  Żaden przerwany przebieg nie jest release gate; finalny przebieg od zera
  opisany wyżej jest właściwą zieloną bramką.

## Runtime baseline i operacje LIVE

Na moment tego raportu nic z kandydata nie zostało wdrożone:

- live code pozostaje na kanonicznym masterze sprzed tego root-fixu;
- live ledger: schema v2, claims 0; integrity/FK green w read-only preflight;
- istniejące joby i `atq` nie zostały zmienione;
- żadna usługa nie została zrestartowana, żadna flaga nie została przełączona;
- Fable ani inny model nie został uruchomiony.

## Backup i rollback

Aktualne rehearsal artifacts są prywatne w:
`/root/artifacts/session284-at-gate-migration-rehearsal-20260801T1505Z-v4/`.

Bezpośrednio przed produkcyjną migracją musi powstać nowy backup przez SQLite
Backup API, z mode `0600`, hashem, `integrity_check=ok` i FK=0. Przed pierwszym
claimem rollback jest wspólny: przywrócenie trzech zgodnych narzędzi oraz bazy
v2 z backupu w quiet window. Stary commit v3 nie jest rollbackiem i nie może
otworzyć finalnego v4. Po jakimkolwiek claimie downgrade jest zabroniony;
rollback jest forward-only przez logiczny CANCEL v4 i zatrzymanie nowych
schedule. Nie wolno kopiować aktywnego SQLite z WAL.

## Pozostałe bramki do LIVE

1. Zamrozić SHA wszystkich sześciu plików, kolejki i evidence; uruchomić
   mechaniczne `ziomek-cto dod` oraz `ziomek-blind-review blind/check`.
2. Commitować wyłącznie jawne ścieżki i wdrożyć spójny provider bez restartu
   usług, zachowując cudze dirty files.
3. W quiet window: świeży SQLite backup API, atomowa migracja v2→v4, exact
   schema/integrity/FK/count verification.
4. Wykonać realny no-model E2E `at_gate → queue execute`: claim i 14-key
   attestation muszą przejść, a time/preflight gate zatrzymać przed Claude.
5. Wygenerować świeży preflight v3, utworzyć nowy gate/job auth2 przez
   `at_gate.py`, zaplanować audyt Fable dopiero na zatwierdzone okno po resecie.

Nie ogłaszać DONE ani LIVE przed wykonaniem wszystkich punktów.

## Mechaniczne evidence dla bramki DoD

regresja: 6399 passed, 24 skipped, 8 xfailed, failed=0; canonical worktree run
e2e: isolated GateStore v4 → at_gate claim/verifier → queue execute passed; stale preflight zatrzymał przed model subprocess
pozytywny-wplyw: mutation probes uruchamiały child przy usuniętym verifierze i maskowały reused ID przy pominiętym SHA; finalne oracles czerwieniały mutanty
rollback: świeży SQLite Backup API v2 mode 0600 + git revert trzech narzędzi przed claimem; po claimie forward-only logical CANCEL v4
