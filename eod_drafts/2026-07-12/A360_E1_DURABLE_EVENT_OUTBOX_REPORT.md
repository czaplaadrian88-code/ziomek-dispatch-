# A360-E1 DURABLE EVENT OUTBOX — raport SOURCE / FROZEN

Data raportu: 2026-07-12 UTC.

Status: SOURCE FROZEN / RUNTIME HOLD. Ten sprint nie jest wdrożeniem i nie
ogłasza DONE ani LIVE. Kod źródłowy usuwa blokery trwałości zidentyfikowane w
E0, ale żaden worker, retry policy, retencja state, migracja live ani flaga
runtime nie zostały wybrane lub uruchomione.

## Wynik w jednym zdaniu

Powstał jeden kanoniczny envelope tworzony raz przez produkcyjny call-site,
transakcyjny outbox z osobnym receipt/attempt/status per consumer, pure reducer
stanu, trwały zredagowany failure journal, bezpieczny rebuild i fail-closed
kontrakt migracji; OFF zachowuje legacy, a ON został uruchomiony wyłącznie na
syntetycznym golden case pod HERMETIC-GUARD.

## Tożsamość i przegląd semantyczny

- Worktree: /root/a360_e1_wt/dispatch_v2.
- Branch: reliability/a360-e1-durable-outbox.
- Frozen base: 0891b06e9e894d88d6bd8a8b9dd9f837cf12a1e0.
- Punkt odniesienia E0: branch reliability/a360-e0-event-fsm, final
  5dd4c80cc0bcae9685545b942b48f92bd40bdd4b, kod
  b2a602755a101a991074d06d3c4e819b09531c3e.
- E0 został włączony jawnie jako cherry-pick
  b53430add19bcdf783555add5830bdbb14ceb0df. Stabilny patch-id źródła E0 i
  cherry-picku jest identyczny:
  439d730a4383c1b415589d70604246a45c182eb8.
- Implementacja E1: c9d02b4 (durable outbox source contract).
- Fix-forward C40: 5044911 (wspólny guard mutujących migracji).
- Master nie był dotykany. Finalny read-only review mastera przy
  c9a946c43e865c9b22a2fed99cefb19e2c2b8f33 wykazał od frozen base wyłącznie
  rozłączny write-set SEC1 security/runbook/test/tool oraz chroniony backlog i
  dokumentację N0/SEC1. Nie ma nakładających się plików kodu event/FSM/outbox,
  dlatego frozen branch nie był rebase'owany na późniejszy master.
- Root/tmux58 pozostaje jedynym integratorem i FLIPMASTEREM.

E0 nie został uznany za dowód E1. Call graph został zbudowany ponownie. Osobna
mapa wszystkich producerów, writerów, mutation pathów, external effects,
replay/rebuild/reconcile i downstream consumers znajduje się w
A360_E1_COMPLETENESS_MAP.md.

## Root cause i granica gwarancji

E0 chronił pojedynczy state receipt, lecz nie potwierdzał efektu ani ACK per
consumer. Producer mógł wykonać follow-up przed trwałym commitem, PICKED i
DELIVERED miały współdzielony receipt, audit-only nie miał trwałego failure
journal, a retencja mogła utracić dedup. Produkcyjne call-site'y nie dzieliły
jednego event_id/created_at/source/policy/version.

E1 rozdziela granice:

1. Producer tworzy dokładnie jeden jawny EventEnvelope i publikuje go wraz z
   pełną topologią intentów w jednej transakcji SQLite.
2. order_state jest jedynym ownerem pure redukcji. Downstream intent zależy od
   ACK order_state, jeżeli event ma state effect.
3. Każdy downstream ma osobny outbox row, receipt, attempt i status.
4. Efekt state jest idempotentny przez trwały fence w orders_state. Crash po
   state i przed ACK kończy retry jako no-op stanu, a następnie ACK.
5. Efekty zewnętrzne mają kontrakt confirm_before_retry. Crash po efekcie i
   przed ACK daje effect_unknown; sprint nie udaje exactly-once poza granicą
   realnie potwierdzonego state fence.
6. Failure journal jest trwały i zredagowany także dla audit-only. Nie jest
   elementem kompaktowania.

## Kanoniczna koperta i publish

- event_envelope.py wymaga jawnego event_id, aware created_at, source,
  policy_version i producer_key. Nie ma fallbacku now(), hydracji brakującego
  czasu ani content-hash jako jedynej tożsamości.
- payload jest kanonizowany raz; envelope przechowuje payload JSON i SHA-256.
- maybe_create_order_envelope wraca natychmiast z None przy twardym OFF, zanim
  dotknie czasu lub payloadu. Jedyny eager create_order_envelope znajduje się
  wewnątrz tego lazy helpera.
- Przy ON produkcyjny emit wymaga koperty z call-site. Producer nie aplikuje
  stanu ani follow-upów inline.
- Duplicate event_id jest legalny tylko przy identycznej kopercie, delivery
  kind, policy, pełnym zestawie canonical intent rows, dedup ledgerze,
  receiptach i terminal markerze. Mismatch effect_type, effect payload,
  depends_on_consumer, retry_contract, idempotency key, payload hash, policy,
  dedup horizon, brak ledgeru albo brak receipt jest EnvelopeConflict bez
  częściowego zapisu.
- Event z pustą topologią dostaje terminal_at=created_at już przy publish;
  duplicate weryfikuje ten kontrakt.

## Outbox, claims, attempts i recovery

- Publish koperty, dedup ledgeru, wszystkich outbox rows, wszystkich receiptów
  oraz legacy compatibility row jest jedną transakcją.
- Claim wymaga jednocześnie pending po stronie outbox i receipt. Oba CAS muszą
  zwrócić rowcount 1, inaczej całość jest rollbackowana.
- Begin attempt wymaga claim.updated_at <= started_at < lease_expires_at.
  Equality na granicy expiry jest odrzucana; recovery używa expiry <= now.
- ACK i failure wymagają statusu executing, exact attempt_count i lease tokenu
  właściciela. finished_at/acknowledged_at nie może poprzedzać started_at.
- ACK/failure po nominalnym expiry jest legalny, jeśli recovery jeszcze nie
  wygrało CAS. Po recovery albo nowszym attempt stary worker jest odrzucany.
- Recovery failuje zamknięcie przy brakującej parze outbox/receipt, rozjechanym
  statusie, pustym owner/expiry, brakującym lub starym executing attempt oraz
  przy dowolnym CAS rowcount innym niż 1.
- Quarantine dependentów wymaga pełnej topologii oraz pending po obu stronach;
  journal, receipt i outbox są objęte sprawdzanym CAS.
- Kolejność jest serializowana osobno per order_id i consumer_id. State ACK
  odblokowuje downstream, ale nie znosi kolejności poprzedniego eventu tego
  samego consumera.

## Pure reducer, state i rebuild

- order_event_reducer.py nie wykonuje I/O, nie odczytuje zegara i nie wywołuje
  efektów zewnętrznych. Stale event o innym ID jest kwarantannowany bez zmiany
  stanu.
- state_machine.commit_durable_state_claim jest jedynym E1 primitive dla
  order_state: begin attempt, redukcja i atomic JSON write, a potem ACK.
- Crash przed state pozostawia persisted outbox. Crash po state i przed ACK
  jest rozpoznawany przez receipt fence w orders_state.
- Durable rebuild czyta wyłącznie dokładne envelopes mające state intent i
  używa tego samego pure reducera.
- Wspólny secure atomic writer dla legacy i durable rebuild otwiera katalog
  przez dir_fd, tworzy temp przez O_CREAT|O_EXCL|O_NOFOLLOW w trybie 0600,
  fsyncuje plik, publikuje bez overwrite przez hardlink relative do dir_fd,
  usuwa temp i fsyncuje katalog. Kolizja, symlink i target utworzony tuż przed
  publish pozostawiają cudzy plik nietknięty i nie tworzą częściowego targetu.

## Schema, migracja i C40

Addytywna migracja E1 deklaruje siedem tabel:

- event_retention_policies;
- event_envelopes;
- event_dedup_ledger;
- event_outbox;
- event_consumer_receipts;
- event_consumer_attempts;
- event_failure_journal.

Inspect porównuje znormalizowany sqlite_master.sql dla każdej tabeli i każdego
indeksu. Nie uznaje za ready schematu o tych samych kolumnach bez CHECK, FK lub
PK ani indeksu o tych samych kolumnach, lecz innym UNIQUE, partial WHERE, DESC
lub COLLATE. Jawny checker i apply wykonują zredagowany
PRAGMA foreign_key_check; orphan wstawiony przy foreign_keys OFF blokuje ready
i apply. Runtime require_schema sprawdza tani kontrakt DDL z verify_data=False,
więc publish/claim/policy load nie skanują całej bazy.

C40 został zastosowany wspólnie do:

- migrations/event_retry_metadata.py;
- migrations/durable_event_outbox.py.

Każdy mutujący CLI wymaga jednocześnie --apply i --synthetic-sandbox, a target
musi być kanoniczną ścieżką pod /tmp. Guard odrzuca known-live path, ścieżkę z
symlinkiem i hardlink/samefile alias przed sqlite3.connect. Pre-opened
connection przechodzi ten sam guard przed BEGIN i DDL.

Test C40 używa wyłącznie tmp_path: KNOWN_LIVE_EVENT_DATABASES jest
monkeypatchowane na syntetyczny victim, tworzony jest hardlink alias, a oba CLI
mają zero wywołań sqlite3.connect. Osobno pre-opened exact victim i hardlink
alias są odrzucane dla obu migracji; nie pojawia się BEGIN/ALTER/CREATE/INSERT/
UPDATE/DELETE, schema i bajty obu nazw pozostają identyczne. Nie czytano ani
nie otwierano live DB.

## Retencja

- Dedup horizon musi obejmować max(event retention, receipt retention) plus
  max replay age. Zbyt krótki kontrakt jest odrzucany.
- Kandydat wymaga kompletnej pary outbox/receipt, status parity, terminal_at,
  terminalne receipt statusy i zachowany failure journal. Korupcja przerywa
  kwalifikację oraz compact.
- Zero-intent event jest terminalny od publish.
- Cała historia eventu zawierającego intent order_state jest twardo wykluczona
  z retention_candidates, niezależnie od delivered/cancelled/resurrected.
  Bez wersjonowanego durable snapshotu albo nieodwracalnego tombstone nie wolno
  jej usuwać. State retention pozostaje osobnym checkpointem HOLD.
- Compact dotyczy wyłącznie eventów bez state intentu i wymaga CAS każdego
  child/dedup delete. Nie istnieje destrukcyjna down migration.

## OFF parity i syntetyczny ON

- event_outbox.DURABLE_EVENT_OUTBOX_ENABLED=False jest twardą stałą source, nie
  wpisem flags.json.
- OFF nie konstruuje ani nie waliduje koperty. Mutation, w której
  create_order_envelope rzuca, nadal zachowuje legacy emit/state/status.
- Wszystkie historyczne follow-upy wykonują się tylko po legacy effect result;
  trwały publish zwraca should_run_followups=False.
- shadow_dispatcher i sla_tracker failują głośno, jeżeli ktoś zmieni source
  constant na ON przed dostarczeniem przyszłego workera.
- ON został wykonany wyłącznie w syntetycznym golden lifecycle/correction:
  durable outbox, order_state claim, pure reducer i ACK. Nie uruchomiono pętli,
  timera, backoffu ani selekcji polityki.

## Walidacja

Wszystkie pytest używały wyłącznie venv
/root/.openclaw/venvs/dispatch/bin/python, tmp_path/syntetyków i
HERMETIC-GUARD, z:

- ZIOMEK_SCRIPTS_ROOT=/root/a360_e1_wt;
- PYTHONPATH=/root/a360_e1_wt;
- DISPATCH_UNDER_PYTEST=1;
- bez ALLOW_PROD_STATE_IN_TEST.

Dowód po C40 i po wszystkich review blockerach:

| zakres | UTC | wynik |
|---|---|---|
| E1 oracle | po fix-forward C40 | 101 passed, 0 failed, 4.25 s |
| E0 + E1 + Phase A | po fix-forward C40 | 163 passed, 0 failed, 6.20 s |
| finalny szeroki klaster | 2026-07-12T11:03:36Z–11:06:28Z | 685 passed, 1 skipped, 1 xfailed, 0 failed, 170.81 s |
| final DEFAULT pod lockiem | 2026-07-12T11:07:51Z–11:12:38Z | 5302 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings, 285.14 s |
| final STRICT pod tym samym lockiem | 2026-07-12T11:12:38Z–11:17:07Z | 5252 passed, 74 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings, 267.85 s |

Finalny log DEFAULT+STRICT ma tryb 0600:
/tmp/a360_e1_final_full_YaIB1X.log.

Baseline E1 po jawnej integracji E0, przed kodem E1:

- DEFAULT 2026-07-12T08:43:10Z–08:47:58Z:
  5155 passed, 24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings,
  284.63 s.
- STRICT 2026-07-12T08:47:58Z–08:52:20Z:
  5105 passed, 74 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings,
  260.14 s.
- Final względem baseline: +147 passed w DEFAULT i STRICT, bez zmiany liczby
  skipów, xfailów i warnings oraz bez failed/XPASS.

Pozostałe checkery po finalnym source:

- git diff --check: PASS;
- py_compile/import: C40_IMPORT_OK;
- flag lifecycle: 505/505 curated, errors=[];
- flag check uruchomiony z --skip-external, więc flags.json nie był czytany ani
  dotykany;
- canon static check: PASS;
- entropy dashboard: bez przypisywania poprawy E1; historyczne wartości
  copy=17, twins około 13, void=25/49, deadflag=1, layer=7, conflicts=13,
  sentinel=11+4, thresholds=10.

## Host-load i przebiegi superseded

To jest materiał load dla V214; integrator dopisuje go do wspólnego registry.
Przebieg bez odzyskanego RC nie jest dowodem.

| przebieg | UTC / bound | load start → end | disposition |
|---|---|---|---|
| baseline DEFAULT | 08:43:10Z–08:47:58Z | 0.53/0.81/1.01 → 1.74/1.36/1.19 | ważny RC0 |
| baseline STRICT | 08:47:58Z–08:52:20Z | 1.74/1.36/1.19 → 2.00/1.58/1.31 | ważny RC0 |
| superseded klaster 643/1/1 | dokładny start/end nieutrwalony; zakończył się przed 10:45:12Z | brak uczciwego exact load | load only, zastąpiony 672 i 685 |
| pre-C40 szeroki 672/1/1 | 10:45:12Z–10:48:02Z | 0.93/0.85/0.97 → 1.93/1.47/1.20 | ważny wtedy, zastąpiony finalnym C40 |
| superseded full | start obserwowany 10:50:30Z; lock zwolniony około 10:59:02Z | 1.65/1.43/1.22 → 2.00/1.84/1.52 odczytane 10:59:07Z | brak odzyskanego RC/output; NIE dowód |
| finalny klaster 685/1/1 | 11:03:36Z–11:06:28Z | 2.39/1.77/1.53 → 2.52/1.89/1.61 | ważny RC0 |
| final DEFAULT | 11:07:51Z–11:12:38Z | 1.09/1.57/1.51 → 2.06/1.92/1.70 | ważny RC0 |
| final STRICT | 11:12:38Z–11:17:07Z | 2.06/1.92/1.70 → 2.13/2.09/1.84 | ważny RC0 |

W pierwszym podejściu do finalnego full warstwa sterująca została przerwana po
około 7 sekundach, lecz owner locka kontynuował. Ponieważ nie odzyskano
rzeczywistego RC ani kompletnego outputu, przebieg jest oznaczony wyłącznie jako
superseded host-load. Finalny DEFAULT+STRICT został uruchomiony dopiero po
zwolnieniu locka, sekwencyjnie pod jednym własnym flock.

C32 near-miss: jednorazowo użyto zabronionej klasy inspekcji pełnego argv.
Output obejmował wyłącznie własną, niewrażliwą komendę; nie ujawnił sekretu i
nie wykonał mutacji. Po korekcie nie używano już pgrep -af, ps args/cmd ani
pełnych argv; owner locka był sprawdzany wyłącznie przez lslocks oraz
PID/comm/cwd. Near-miss nie jest ukrywany ani przedstawiany jako dowód testowy.

## Operacje live

Wykonano zero:

- zmian flags.json i efektywnych flag;
- migracji lub odczytu danych live/PII;
- workerów, timerów, retry policy i retencji;
- deployów, restartów, systemd i zmian HARD/SOFT;
- zapisów do live state, events DB i logów;
- merge do mastera.

PID/NRestarts/health po deployu są N-D, ponieważ deployu nie było. Backup live
DB nie powstał, ponieważ live DB nie została otwarta ani zmieniona.

## Otwarte bramki / HOLD

1. Zatwierdzenie biznesowe retention/replay/idempotency policy oraz osobnego
   state-retention checkpointu z wersjonowanym snapshotem lub tombstone.
2. Backup SQLite przez API .backup, read-only dry-run/checker na kopii,
   zatwierdzenie policy row i osobny ACK na migrację live.
3. Ustalenie backfillu revision/source identity dla zastanych eventów.
4. Implementacja i osobny review workera; dopiero potem jawna runtime flaga,
   policy selection, lease/backoff/health/stuck alert i kill-switch.
5. Potwierdzenie realnej idempotency lub confirm-before-retry dla każdego
   external consumer: shadow, plan, SLA, auto_koord, coordinator activation,
   assignment audit i delivery geocode.
6. Osobny ACK integratora na merge, deploy i kontrolowany restart właściwych
   procesów. dispatch-telegram pozostaje poza zakresem.
7. Syntetyczny ON nie jest zgodą na live ON. Przyszłe wydanie wymaga pełnego
   smoke, fingerprintu, health i co najmniej dwudniowego okna obserwacji.

## Rollback

Najbezpieczniejszy rollback teraz to pozostawienie
DURABLE_EVENT_OUTBOX_ENABLED=False; żadna zmiana runtime nie zaszła.

Rollback całego source sprintu, wyłącznie na branchu, to jawne reverty od
najnowszego:

1. git revert 5044911;
2. git revert c9d02b4;
3. git revert b53430a.

Pełny rollback całej serii to kolejno 5044911, c9d02b4 i b53430a; usuwa on
również wspólny guard C40 chroniący migracje E0 i E1. Rollback wyłącznie E1 nie
może więc być mechanicznym revertem pary 5044911+c9d02b4. Musi być
dedykowanym patchem, który usuwa część E1, lecz zachowuje albo ponownie
aplikuje część E0 z 5044911; bez takiego patcha bezpiecznym wariantem jest
pozostawienie całej serii OFF. Nie ma destrukcyjnej down migration i nie wolno
usuwać failure journalu, dedup ledgeru ani state history. Restart i restore
danych są N-D, ponieważ nie było deployu ani live write.

## Chronione artefakty

Nie edytowano ZIOMEK_BACKLOG.md, memory, handover, flags.json, raportów
V214/SEC1, night-guarda, CLAIM_LEDGER_HARD_GATE_CARD.md ani
daily_accounting/kurier_full_names.json. Raport i mapa E1 są jedynymi
artefaktami handoffu dodanymi po finalnych testach; nie zmieniają ważności
DEFAULT/STRICT.
