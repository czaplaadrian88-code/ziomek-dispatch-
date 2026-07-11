# AUDIT360 — A0/I1/N0 live close — 2026-07-11

Status: **DONE/LIVE w zatwierdzonym zakresie**. A0 nie promowal modelu, I1 nie
wyslal ponownie zlecenia, a N0 zakonczyl dokladny systemd E2E werdyktem `OK`.
Polecenie Adriana z tej sesji, aby wprowadzic co mozna live i domknac sprinty,
bylo ACK na source deploy, kontrolowane uruchomienia i operacje na danych
runtime opisane ponizej. Nie obejmowalo flipa decyzji ETA, zmiany HARD/SOFT,
restartu `dispatch-telegram` ani nieustalonej polityki retencji.

## 1. Wynik i zmiana zachowania

| Sprint | Wydanie | Co zmienilo sie live | Granica, ktora pozostala |
|---|---|---|---|
| `A360-A0 ETA-CALIBRATION-TRUTH` | dispatch `2595bed` + raporty | Kalibrator odrzuca cechy niedostepne w chwili decyzji i wymaga odtwarzalnego championa, wspolnego supportu oraz paired evidence. Kontrolny bieg zapisal kandydata, lecz pozostawil championy bez zmian. | Werdykt `HOLD`, powod `artifact_legacy_or_unknown_schema`; pickup i delivery `promote=false`. Zero podpiecia modelu do decyzji. |
| `A360-I1 PAPU-BRIDGE-RECOVERY` | bridge `b2c65b2` | Submit bez jednoznacznego `panel_zid` trafia do trwalego exact-marker recovery. Kolejne ticki szukaja read-backu, ale nigdy nie robia ponownego submitu. | Zastany przypadek ma `hold/marker_missing`; po 3 probach `inject_attempt_count=0`, `dispatched_count=0`. Exactly-once po zewnetrznym POST nadal wymaga kontraktu Papu. |
| `A360-N0 NIGHT-GUARD-TRUTH` | dispatch `8056319`, fix-forward `8fc2920`, `4c351d5` | Nocny guard ma wersjonowany manifest nodeid/outcome, fail-closed denominator i nie przyjmuje hard-error jako baseline. Dwa losowe XPASS zostaly zastapione deterministycznym kontraktem. | Guard nie zmienia decyzji biznesowych; tylko blokuje falszywie zielony raport regresji. |

Tmux68/69/70 zachowano otwarte i idle jako audytowalne sesje. Tmux58 byl
jedynym integratorem/FLIPMASTEREM.

## 2. Tozsamosc wydania i rollback pointy

### Dispatch A0 + N0

- repo: `/root/.openclaw/workspace/scripts/dispatch_v2`;
- release worktree/branch:
  `/root/a360_a0_n0_live_wt/dispatch_v2`,
  `release/a360-a0-n0-live-20260711`;
- baza i rollback tag: `a360-a0-n0-pre-live-20260711` @ `2e853a2`;
- kodowy punkt wydania na master/origin przed commitem handoffu: `4c351d5`;
- finalny tag: `a360-a0-n0-live-verified-20260711` @ `4c351d5`;
- historyczny tag `a360-a0-n0-source-live-20260711` @ `7b2e6b6` jest
  superseded: oznacza tylko pierwszy source merge przed fix-forward E2E.

Commity integracyjne: `2595bed`, `431a9ad`, `01fb7cd`, `8056319`, `7b2e6b6`,
`8fc2920`, `4c351d5`.

### Papu bridge

- repo/workspace: `/root/.openclaw/workspace/scripts`;
- release worktree/branch: `/root/a360_papu_live_wt`,
  `release/a360-papu-recovery-live-20260711`;
- rollback tag: `a360-papu-recovery-pre-live-20260711` @ `51dfe90`;
- finalny kodowy master/origin i tag `a360-papu-recovery-live-20260711`:
  `b2c65b2`.

## 3. Dowod testowy

Pierwsza integracja A0+N0:

- targeted DEFAULT i STRICT: po 75 passed;
- pelny DEFAULT `21:46:48Z..21:51:34Z`: 5139 passed, 27 skipped,
  8 xfailed, 0 XPASS, 0 failed;
- pelny STRICT `21:51:43Z..21:56:05Z`: 5089 passed, 77 skipped,
  8 xfailed, 0 XPASS, 0 failed;
- lifecycle 505/505, import/compile, entropy i `diff --check`: PASS.

Dokladny systemd E2E N0 ujawnil dwie luki, ktorych branchowy test nie widzial:

1. przy cwd pakietu zewnetrzny plugin pytest nie byl importowalny, zanim pytest
   ustalil rootdir; `8fc2920` dodal jawny package-parent do `PYTHONPATH` potomka;
2. test agregatora czyscil globalny stan aktywnego pluginu outer-run, przez co
   updater manifestu widzial `not_run`; `4c351d5` izoluje i odtwarza ten stan.

Po pierwszym fixie:

- targeted DEFAULT/STRICT: 46/46;
- DEFAULT `22:05:17Z..22:10:00Z`: 5140 passed, 27 skipped, 8 xfailed;
- STRICT `22:10:00Z..22:14:25Z`: 5090 passed, 77 skipped, 8 xfailed.

Drugi systemd E2E prawidlowo zakonczyl sie `ALERT`, bo nowy test nie byl jeszcze
w manifest v3 (`SUITE-CONTRACT-UNEXPECTED(1)`). To byl pozytywny negative
control fail-closed. Po izolacji pluginu updater zbudowal manifest v4:

- updater `22:27:03Z..22:31:48Z`: 5140 passed, 27 skipped, 8 xfailed,
  0 XPASS, 0 failed; 5171 nodeidow;
- finalny STRICT `22:31:58Z..22:36:23Z`: 5090 passed, 77 skipped,
  8 xfailed, 0 XPASS, 0 failed;
- finalny systemd E2E `22:36:47Z..22:41:41Z`: `Result=success`,
  `ExecMainStatus=0`, `NRestarts=0`, verdict `OK`, alerts `[]`, manifest v4
  `contract_ok=true`, baseline eligible.

Papu: systemowy Python compile/import PASS, targeted 11/11 i kontrolowany
oneshot `Result=success`, `NRestarts=0`.

Konserwatywna nowa unia host-load do sensitivity `at-214`:
`[2026-07-11T21:46:48Z,22:41:41Z]`. Dokladne podprzedzialy sa powyzej; nie
wolno uzyc tego okna jako czystego latency baseline.

## 4. Operacje live i stan po wydaniu

### Night guard

- przed praca timer zostal zatrzymany, a historia dostala backup 0600:
  `/root/.openclaw/workspace/dispatch_state/backups/night_guard_history.pre-n0-live-20260711T2200Z.jsonl`;
- wykonano dokladne starty uslugi, w tym dwa kontrolowane faile i finalny PASS;
- timer jest ponownie active/waiting; nastepny planowany bieg:
  `2026-07-12 01:15 UTC`;
- live historia ma schema v2; nie kasowano dowodow nieudanych biegow.

### ETA calibration

- timer zatrzymano na czas backupu i kontrolowanego oneshotu;
- backup 0700, pliki 0600:
  `/root/.openclaw/workspace/dispatch_state/backups/eta_calibration.pre-a0-live-20260711T2242Z`;
- SQLite zbackupowano przez API `.backup`; `integrity_check=ok`;
- kontrolowany bieg `22:42:43Z..22:43:02Z` zakonczyl sie success;
- hashe obu champion maps przed i po sa identyczne; zapisano tylko candidate,
  metrics/shadow oraz addytywny stan DB;
- pickup holdout n=3249, delivery n=2655; oba `promote=false`,
  `champion_written=false`;
- timer ponownie active/waiting; nastepny bieg `2026-07-12 05:21:33 UTC`.

### Papu bridge

- timer zatrzymano, stan zbackupowano do pliku 0600:
  `/root/.openclaw/workspace/dispatch_state/backups/papu_dispatch_bridge_state.pre-i1-live-20260711T2158Z.json`;
- wykonano jeden kontrolowany oneshot i przywrocono timer active/waiting;
- zapis byl addytywny: istniejacy brak `panel_zid` ma jawny
  `pending_recovery/hold/marker_missing`;
- po naturalnych tickach licznik prob wynosi 3, lecz licznik prob wyslania i
  licznik wyslanych zlecen pozostaja 0.

Nie zmieniono `flags.json` ani decyzji silnika, nie wykonano migracji danych
biznesowych, nie restartowano shadow/watcher/API ani `dispatch-telegram`.

## 5. Rollback

Rollback jest przygotowany, ale nie byl potrzebny.

- A0/N0 source: zatrzymac odpowiednie timery, zrobic jawne reverts commitow
  wydania w odwrotnej kolejnosci do tagu
  `a360-a0-n0-pre-live-20260711`, powtorzyc targeted + pelny STRICT i dopiero
  przywrocic timery. Guard nie wymaga restartu stalej uslugi.
- N0 state: historia schema v2 jest addytywnym dowodem i domyslnie pozostaje;
  przy niekompatybilnosci przywrocic backup tylko przy zatrzymanym timerze.
- A0 state: przy zatrzymanym timerze przywrocic mapy i logi z backupu, a DB
  przez spójny restore SQLite; sprawdzic integralnosc i hashe map przed startem.
- I1: zatrzymac timer, revert `b2c65b2` do tagu pre-live, przywrocic backup
  stanu z mode 0600, wykonac import/11 testow i dopiero uruchomic timer.
- Rollback nigdy nie moze ponownie wyslac przypadku bez jednoznacznego
  `panel_zid`; w razie watpliwosci bridge pozostaje zatrzymany i fail-loud.

## 6. Near-miss i ochrona cudzej pracy

Pierwsza komenda cherry-pick zostala omylkowo wykonana w glownym checkoutcie,
zamiast w release worktree. Blad wykryto przed pushem i przed uruchomieniem
timerow; zadny proces nie zaladowal wtedy nowego kodu. Release worktree zostal
potem jawnie zsynchronizowany, a calosc przeszla pelne testy. Protokol dostaje
nowa bramke sprawdzenia top-level/branch/worktree bezposrednio przed kazda
komenda wydaniowa.

Chroniony dirty
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` oraz cudze
`papu_dispatch_bridge/restaurant_map.json` i niewersjonowany
`DEPLOY_PROCEDURE.md` pozostaly nietkniete i nie byly stage'owane.

## 7. Obserwacja

- N0: odczyt najblizszego biegu po `2026-07-12 01:15 UTC`; wymagane
  `verdict=OK`, `contract_ok=true`, zero XPASS/fail/hard-error.
- A0: dalsze biegi moga budowac kandydatow, ale promocja pozostaje fail-closed
  do odtwarzalnego championa v2 i zweryfikowanego KPI; zero automatycznego
  terminu flipa.
- I1: czytac tylko zagregowany status recovery; `resolved` wymaga dokladnie
  jednego markera. `missing/ambiguous` pozostaje hold, nie wyzwala resubmitu.
- `at-214` 13.07 musi wykluczyc albo osobno policzyc wszystkie zapisane okna
  host-load. R0/D1/H1 pozostaja HOLD.
