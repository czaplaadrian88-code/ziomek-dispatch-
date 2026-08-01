# At-gate authority, durable receipt, ledger v4 i kolejka Fable — evidence

Status: `LIVE — PROVIDER D0C0640B5 + LEDGER V4 + NO-MODEL E2E GREEN; FABLE AT#229 PENDING`

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
- Po deployu preflight wykrył u źródła jeden stale pin własnego selftestu
  kolejki. Rebind wyłącznie `QUEUE_SELFTEST_SHA256` do realnego SHA przeszedł
  43/43 testy i niezależny incremental `GO`.
- `QUEUE_STATUS.state=PREFLIGHT_VERIFIED`; świeży `PREFLIGHT.json` v3 ma SHA
  `10a14a472027a5c996da3b5f96ee846a8c3bf51f8ebb7f4e03546c0bf4d74161`.
  Queue fingerprint to
  `a119aa14ffd473b0285ae5b1d29cbe6fcc340b9c32c4d2e1f28c92bacbc2f08b`.

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
| queue `execute` | model authority | consumer | TAK LIVE | 43 selftests + real no-model E2E po deployu |

## Aktualne dowody testowe

- Focused ledger/scheduler/collector: `56 passed in 7.00s`; exit 0.
- Queue remediation po finalnym rebindzie: `43 passed, 29 subtests passed in
  0.12s`; exit 0. Runner SHA:
  `08271e127d7c2f73c5bf314d34c4c0f2b1e07787309ccc256cc06c8d45a8fc5b`.
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

## Runtime i operacje LIVE

- Kanoniczny `master` został fast-forwardowany bez konfliktu z cudzymi dirty
  plikami do `d0c0640b577f4ffd92f2ab70157fb511ee3d78c1` (obejmuje wymagany
  poprzednik `40daa2d603ddc6efd6cbd743f833521681103166`).
- Przed deployem powstał tag
  `rollback/at-gate-v4-predeploy-20260801T154412Z` oraz jawne kopie trzech
  narzędzi. Finalne SHA live są identyczne z zaakceptowanym kandydatem.
- Świeży backup SQLite Backup API v2 ma mode `0600`, integrity `ok`, FK=0,
  claims=0 i SHA
  `d75b62734b68229b91427a2dfe6559b35f9a155827f73a4c862c89adfe4bf936`.
- Produkcyjny ledger przeszedł atomowo `user_version 2→4`; zachowane liczności
  przed migracją: gates=109, events=434, jobs=6, claims=0. Po rejestracji
  następcy audytu: gates=110, events=437, jobs=7, claims=0; integrity `ok`,
  FK=0, mode `0600`.
- Powdrożeniowy scratch E2E użył realnych wdrożonych narzędzi i realnego queue
  runnera. `at_gate` utworzył auth2 RUN claim, oba publiczne verifiery v4
  przeszły, a kolejka zatrzymała się dopiero na guardzie resetu z rc=97.
  Claim został sfinalizowany z durable receiptem; `run-20260802` nie powstał,
  `QUEUE_STATUS` nie zmienił SHA, a fingerprint produkcyjnej bazy przed/po był
  identyczny. Dowód:
  `/root/artifacts/session284-at-gate-postdeploy-e2e-20260801T154909Z/`.
- Nowy realny sealed auth2 job `at#229` jest `SCHEDULED` na
  `2026-08-02T10:10:00Z`; gate
  `release.sessions274-280-review-queue-v4-postdeploy-20260802` jest
  `READY_FOR_REVIEW` v3 bez alarmu. Audyt ma wyłącznie authority read-only i
  nie może automatycznie promować ani mutować LIVE.
- Żadna usługa nie została zrestartowana, żadna flaga nie została przełączona;
  `dispatch-telegram` pozostaje inactive/disabled. Fable ani inny model nie
  został uruchomiony w tej sesji.

## Backup i rollback

Rehearsal jest w
`/root/artifacts/session284-at-gate-migration-rehearsal-20260801T1505Z-v4/`,
a rzeczywisty backup i kopie kodu w
`/root/artifacts/session284-at-gate-live-deploy-20260801T154412Z/`.

Po utworzeniu auth2 joba #229 nie wolno wykonywać downgrade ani otwierać bazy
starym v3. Rollback jest forward-only: logiczny exact cancel joba #229,
zatrzymanie nowych schedule i jawny forward-fix v4. Backup v2 pozostaje dowodem
i ostatnią opcją disaster recovery wyłącznie po adjudykacji, nie zwykłym
automatycznym rollbackiem aktywnego ledgera. Nie wolno kopiować SQLite z WAL.

## Pozostała bramka powdrożeniowa

Kod, migracja, preflight i scheduler są LIVE oraz zweryfikowane. Pozostał tylko
read-only audyt Fable uruchamiany przez `at#229` po resecie. Jego werdykt nie
zmienia automatycznie kodu, flag ani produkcji; po wyniku MAIN waliduje receipt,
trzy werdykty i dopiero aktualizuje odpowiednie gate'y.

## Mechaniczne evidence dla bramki DoD

regresja: 6399 passed, 24 skipped, 8 xfailed, failed=0; canonical worktree run
e2e: deployed GateStore v4 → at_gate auth2 claim/verifier → queue verifier passed; quota-reset guard rc97 zatrzymał przed model subprocess; prod DB unchanged
pozytywny-wplyw: mutation probes uruchamiały child przy usuniętym verifierze i maskowały reused ID przy pominiętym SHA; finalne oracles czerwieniały mutanty
rollback: świeży SQLite Backup API v2 mode 0600 + git revert trzech narzędzi przed claimem; po claimie forward-only logical CANCEL v4
