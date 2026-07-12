# Audyt 360 — SEC1 source w masterze, E1 branch close, V214 nadal WAIT

Data: 2026-07-12 UTC

Integrator / FLIPMASTER: tmux58

Stan: **N0 LIVE; SEC1 SOURCE INTEGRATED / HOST HOLD; E1 SOURCE BRANCH / OFF / HOLD; V214 WAIT/PENDING**

## Prosto: co zrobiono i jaki jest skutek

1. Nocny strażnik N0 został wcześniej naprawiony u źródła i uruchomiony
   kontrolowanym restartem one-shot. Manifest testów jest aktualny, a historia
   po atomowym zapisie zachowuje prywatne prawa `0600`. To jest jedyna nowa
   zmiana zachowania działająca live w tej fali.
2. SEC1 dostarczył do mastera bezpieczny, ręczny audytor oraz kontrakty dowodów
   dla przyszłego maintenance. Audytor nadal mówi `HOLD`: host nie jest jeszcze
   zabezpieczony, bo publiczne bindy, reguły host/provider i rotacja wymagają
   osobnego, precyzyjnego ACK i okna serwisowego.
3. E1 przygotował wspólną kopertę eventu, trwały outbox, osobne receipts i
   attempts per consumer, journal awarii, recovery, reducer oraz dry-run
   migracji. Kod pozostaje na osobnej gałęzi i twardo `OFF`; nie ma workera,
   polityki produkcyjnej, migracji live ani deployu. Nie zmienia dziś pracy
   Ziomka.
4. V214 ma gotowy preflight, ale prawdziwy werdykt może powstać dopiero po
   jobie 214 dnia 2026-07-13 12:15 UTC. Sesja tmux74 pozostaje otwarta; R0, D1
   i H1 nadal są zamrożone.

## N0 — wykonane live

- Kod live: `0891b06e9e894d88d6bd8a8b9dd9f837cf12a1e0`.
- Rollback tag: `a360-n0-followup-pre-live-20260712`.
- Verified tag: `a360-n0-followup-live-verified-20260712`.
- Systemd E2E `[08:25:10Z,08:30:02Z]`: 5155 passed, 24 skipped,
  8 xfailed, 0 failed, 0 XPASS; contract i manifest v5 poprawne.
- Restart dotyczył wyłącznie `dispatch-night-guard.service`; timer czeka na
  2026-07-13 01:15 UTC, historia ma `0600`, `NRestarts=0`.
- Backup historii 0600:
  `dispatch_state/backups/night_guard_history.pre-n0-followup-20260712T0824Z.jsonl`.
- Rollback: zatrzymać writer/timer, jawnie revertować `0891b06`, potem
  `8bf5f72`; backup przywracać tylko przy zatrzymanym writerze i utrzymać 0600.

## SEC1 — source wdrożony, host nadal HOLD

Gałąź `security/a360-sec1-host-remediation` jest clean/pushed na
`44f80bc39a344f4dacb80dec1ed978618333cd3f`. Jej kompletna seria została
zintegrowana do dispatch mastera:

- `5476e2c`, `58e18dd`, `cf8aaef`, `16f4ef4`, `c9a946c`;
- tag przed integracją: `a360-sec1-source-pre-live-20260712` @ `78aaac2`;
- tag źródła zintegrowanego:
  `a360-sec1-source-integrated-20260712` @ `c9a946c`.

Audytor `tools/host_boundary_audit.py` jest manualnym, read-only source bez
timera i bez runtime consumera. Najnowszy odczyt na masterze o 11:19:49Z zwrócił
exit 2 / `HOLD`, `mutations_performed=false` i osiem findingów: brak czterech
docelowych rodzin deny, provider `UNKNOWN`, publiczny IPv4 bind 8767 oraz
publiczne IPv4+IPv6 bindy 9222.

Finalne testy gałęzi SEC1 po dwóch pełnych review:

- targeted 11/11 DEFAULT i 11/11 STRICT;
- guard cluster 36/36;
- DEFAULT `[10:11:03Z,10:15:42Z]`: 5155/24/8, zero fail/XPASS;
- STRICT `[10:15:42Z,10:20:04Z]`: 5105/74/8, zero fail/XPASS;
- po integracji na masterze focused 39/39 DEFAULT i 39/39 STRICT, compile,
  import, lifecycle, kanon i diff — PASS.

Nie wykonano firewalla, provider API, bindu, `/etc`, credentialu, rotacji,
recreate kontenera, deployu API ani restartu. Source rollback to jawny revert
serii do tagu `a360-sec1-source-pre-live-20260712`; nie wymaga restartu, bo
audytor nie ma aktywnego consumera. Host wolno nazwać zabezpieczonym dopiero po
pełnym maintenance i realnych denied+allowed probes IPv4/IPv6.

## E1 — durable event outbox, source branch tylko

Tożsamość:

- worktree: `/root/a360_e1_wt/dispatch_v2`;
- branch: `reliability/a360-e1-durable-outbox`;
- frozen base: `0891b06`, z kontraktem E0 w `b53430a`;
- kod: `c9d02b4` (`feat(events): add durable outbox source contract`) oraz
  finalny fix C40 `50449113ef619994b03987c7c8664374546a79bd`;
- finalny branch HEAD/origin po raporcie i korekcie rollbacku:
  `66a2591e061fb386017ec807bf538234067cf077`;
- source default: `DURABLE_EVENT_OUTBOX_ENABLED=False`;
- worker, timer, runtime policy, flaga hot, live schema i migracja: **nie istnieją / nie wykonano**.
- raport gałęzi: `eod_drafts/2026-07-12/A360_E1_DURABLE_EVENT_OUTBOX_REPORT.md`;
  mapa: `eod_drafts/2026-07-12/A360_E1_COMPLETENESS_MAP.md`.

Root cause E0 był głębszy niż sam retry. Jeden status eventu nie dowodził
osobno zapisu stanu, efektu zewnętrznego i każdego downstream consumera. E1
dodaje:

- jedną kanoniczną kopertę używaną przez DB, stan i replay;
- transactional outbox i osobny receipt/attempt dla każdego consumera;
- fail-closed topology outbox↔receipt oraz CAS dla ACK/failure/recovery;
- lease związany z konkretnym claimem, nie tylko nazwą workera;
- journal awarii i bezpieczną kwarantannę zależnych efektów;
- pure reducer i DR rebuild oparty tylko na jawnych intentach `order_state`;
- retencję, która nie usuwa failure journal ani żadnej historii
  `order_state` bez przyszłego trwałego checkpointu/snapshotu;
- lazy OFF-parity: przy wyłączeniu nie powstaje koperta, nie zachodzi nowa
  walidacja i legacy call graph nie dostaje nowego argumentu.

Dowody po końcowym review C40:

- E1 oracle: 101 passed;
- E0+E1+retry metadata: 163 passed;
- szeroki klaster `[11:03:36Z,11:06:28Z]`: 685 passed, 1 skipped,
  1 xfailed, 0 failed/XPASS;
- compile/import/diff/checkery i entropy dashboard — PASS, kod zamrożony w
  `c9d02b4` plus osobnym fix-forward C40 `5044911`.

Finalna pełna regresja po freeze:

- superseded host-load `[10:49:37Z,10:58:51Z]`: kanał odbioru został
  przerwany korektą C32, a późniejsze review C40 zmieniło kod; ten przebieg nie
  jest dowodem testowym;
- DEFAULT `[11:07:51Z,11:12:38Z]`: 5302 passed, 24 skipped, 8 xfailed,
  0 failed/XPASS, RC=0;
- STRICT `[11:12:38Z,11:17:07Z]`: 5252 passed, 74 skipped, 8 xfailed,
  0 failed/XPASS, RC=0.

Werdykt E1 pozostaje **SOURCE COMPLETE / MERGE I LIVE HOLD**. Przygotowany kod
zmieni zachowanie dopiero w przyszłym sprincie po: projekcie checkpointu,
wyborze retry/retention policy, review migracji, workerze z liveness/health,
replay ON↔OFF, osobnym ACK na migrację/deploy/restart oraz kontrolowanym
rollbacku. Najbezpieczniejszy rollback dziś to pozostawić całą serię OFF.
Pełny rollback serii to revert `5044911`, potem `c9d02b4`, ale usuwa też
wspólny guard C40 z migracji E0; rollback tylko E1 wymaga dedykowanego patcha
zachowującego/reaplikującego część E0. Backup danych jest N-D, bo nie było
żadnego zapisu live.

## Near-miss i trwałe bezpieczniki

- Dwa review SEC1 wykryły, że poprawny schemat dowodu nie wystarcza bez
  świeżości, wspólnego observation ID i związania source z faktycznym
  postimage/PID/image. Zapisano regułę C49.
- Review E1 wykrył niepełne topology/CAS, OFF wykonywane zbyt późno oraz
  niebezpieczną retencję historii po pozornie terminalnym statusie. Zapisano
  reguły C50-C52.
- Końcowy review C40 wykrył, że jawne `--apply` obu bliźniaczych migracji E0/E1
  nie odmawiało znanej ścieżki live. Finalny source wymaga wspólnego
  fail-closed guarda, canonical path/symlink parity i jawnego synthetic sandbox
  przed pierwszym `sqlite3.connect` lub zapisem.
- Przy sprawdzaniu wolnego full-test locka E1 jednorazowo użył `pgrep -af`.
  Output zawierał tylko własną niewrażliwą komendę; nie ujawniono sekretu i
  nie było mutacji. Wywołanie zostało przerwane procesową korektą C32; dalsze
  kontrole używają wyłącznie PID/comm/cwd/lock metadata.

## Produkcja i bramki

- Dispatch-shadow, panel-watcher, SLA i courier-api nie były restartowane w
  SEC1/E1. N0 jest jedynym restartowanym one-shotem.
- Finalny smoke 11:19 UTC: shadow PID 573430, watcher PID 3659486, SLA PID
  2998575 i API PID 925329 są active/running, Result success, NRestarts0;
  parser HTTP 200. Night guard service jest poprawnie inactive/dead po one-shot,
  timer active/waiting do 13.07 01:15, historia i backup mają 0600.
- Flagi silnika i ich fingerprint nie zostały zmienione; zero flipa i zero
  zmiany relacji HARD/SOFT. `flags.json` nadal ma 0600, mtime
  2026-07-11 10:27:12Z i SHA256 `568436f3...848bf`.
- Zero migracji lub modyfikacji live DB/state, zero kasowania danych, zero
  deployu E1 i zero re-enable `dispatch-telegram`.
- SEC1 realny maintenance nadal wymaga ACK wymieniającego provider, firewall,
  bind, credential, kontener/API i restarty oraz drugiej sesji administracyjnej.
- E1 live nadal wymaga osobnego ACK na migrację, policy, worker, deploy i
  restart. Ten bieżący deploy objął wyłącznie bezpieczny kod audytora SEC1.
- `atq` nadal zawiera wyłącznie job 214 na 13.07 12:15 UTC.

## Tmux i dalsza kolejność

- tmux50 — cudzy/chroniony WIP, nietknięty;
- tmux58 — integrator i jedyny FLIPMASTER;
- tmux74 — V214 WAIT/PENDING do joba 214, pozostaje otwarty;
- tmux75 — SEC1 zakończony i zamknięty;
- tmux76 — zamknięty po finalnym raporcie, push parity `66a2591` i
  snapshotcie 0600.

Nieaktywne i jednoznacznie zakończone sesje zostały zamknięte. Nie zamykano
tmux50 ani tmux74. Chroniony dirty
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` pozostał nietknięty.

## Następny odczyt

Po dopisaniu wszystkich interwałów SEC1/E1 do
`memory/shadow-jobs-registry.md` tmux74 odświeżył sensitivity i wypchnął
`5375d8d3f5a52bc35012da7677e3e20a79cd1724`: 48 surowych wpisów,
36 przedziałów po scaleniu, unia 17 721 s = 295,35 min = 10,26% canary.
Po 2026-07-13 12:15 UTC najpierw sprawdzić `atq` i prywatne
artefakty 0600 joba 214. Bez kompletnego outputu status pozostaje WAIT/HOLD;
nie wolno uruchamiać joba wcześniej ani automatycznie mergować R0/D1/H1.
