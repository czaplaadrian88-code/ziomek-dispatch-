# At-gate execution binding i wyścig RUN↔CANCEL — evidence

Status: `ROOT FIX BUILT / LIVE DEPLOY PENDING / ZERO MODEL CONTACT`

Data: 2026-08-01 UTC
Model tier / effort wykonawcy: `sol / ultra` — współbieżność, migracja ledgera i bramka wydania.
Worktree: `/root/worktrees/dispatch_v2/active/20260801-at-gate-execution-claim`
Branch/base: `fix/at-gate-execution-claim-20260801` / `cfbfe8e92a99f0d6ad0de53fdcff476381772b86`

## Defekt i root cause

Na bazie sprintu runner mógł dojść do `subprocess` przed ustanowieniem
niepodzielnego prawa wykonania. Token był sprawdzany dopiero przy terminalnym
receipcie, komenda w spoolu była przenoszona jako mutowalny argument, a cancel
usuwał wpis z `atq` przed ledgerowym CAS. W efekcie dwa runnery albo RUN i
CANCEL mogły rozstrzygnąć konflikt dopiero po skutku ubocznym.

Drugi writer tej samej prawdy znajdował się w `process_debt_collect.py`:
sam brak aktywnego joba w `atq` był klasyfikowany jako `AT_JOB_MISSING`.
To jest niepoprawne podczas prawidłowego dequeue, krótkiego launch grace albo
świeżego RUN/CANCEL claimu. Kanonicznym ownerem tej klasyfikacji jest teraz
wyłącznie `GateStore.reconcile_at_jobs`; collector nie tworzy już żadnego
proposal dla zarejestrowanego joba, a stan/alarm pokazuje `OPEN_GATES` z ledgera.

Stary Fable job `#228` został bezpiecznie anulowany i jego gate superseded.
Nie uruchomiono Fable ani żadnego innego modelu. Nie restartowano usług, nie
zmieniano flag ani reviewed bundle'a.

## Nowy kontrakt

- Nowe schedule atomowo tworzy gate oraz job z sealed `auth_version=2`.
- Prywatny payload ma tryb `0600`, katalog `0700`; spool zawiera tylko ścieżkę.
- `GateStore.claim_at_job` pod `BEGIN IMMEDIATE` sprawdza token, exact argv,
  payload identity/HMAC, czas startu, job oraz bramkę bez alarmu.
- RUN i CANCEL konkurują o dokładnie jeden rekord `at_job_claims` w stanie
  `CLAIMED`; terminalny receipt zmienia go na `FINALIZED`.
- Child process może powstać dopiero po zwycięskim RUN claimie.
- CANCEL najpierw rezerwuje authority w ledgerze, potem wykonuje `atrm`, bada
  postcondition i finalizuje receipt. Drift bramki zachowuje jej nowsze pola.
- Sukces joba nie promuje samodzielnie gate'a i nie nadpisuje jego evidence,
  blockerów ani next step; zapisuje field-scoped receipt i event.
- `reconcile_at_jobs` rozróżnia launch grace, świeży RUN/CANCEL claim, stale
  claim i rzeczywiście zaginiony job. Collector nie ma własnej polityki ani
  drugiego `AT_JOB_MISSING` proposal writera.
- Launcher kolejki Fable wymaga exact outer execution context i uruchamia
  zweryfikowane bajty runnera oraz gate toola przez otwarte deskryptory.

## Mapa kompletności

| miejsce | rola | writer/consumer | dotknięte | dowód / oracle |
|---|---|---|---:|---|
| `tools/at_gate.py:schedule` | sealed payload + scheduler body | writer | TAK | auth2, tryby 0600/0700, bez tokenu/argv w spoolu |
| `GateStore.register_at_job` | atomowy gate + intent | writer | TAK | fault injection nie zostawia połowy kontraktu |
| `GateStore.claim_at_job` | jedyny pre-exec authority | writer | TAK | token/argv/payload/time/gate tamper = zero child |
| `at_gate._run_registered_inner` | wykonanie | consumer | TAK | claim przed `subprocess.run`; dwa runnery = jeden child |
| `GateStore.finish_at_job` | terminalny RUN receipt | writer | TAK | exact claim binding; field-scoped gate update |
| `at_gate.cancel` + store | cancel orchestration | konkurencyjny writer | TAK | RUN↔CANCEL: dokładnie jeden zwycięzca; DB przed `atrm` |
| `GateStore.reconcile_at_jobs` | recovery i missing owner | writer | TAK | launching/running bez fałszywego alarmu; stale claim alarmuje |
| `tools/process_debt_collect.py` | inwentaryzacja `atq` | consumer | TAK | brak registered-missing proposal; tylko nieznane joby + licznik ledgerowy |
| `tools/process_debt_gate.py` schema v3 | trwałość claimów | writer/reader | TAK | atomowe v1/v2→v3, exact columns, legacy rows fail closed |
| pinned Fable launcher | child authority | consumer | TAK | context/gate/hash/FD drift = exit 97 przed modelem |
| `OPEN_GATES.md` exporter | widok gate/alarm | consumer | N-D | nie klasyfikuje obecności w `atq`; wyświetla kanoniczny ledger |

## Bramka testowa

- Focused gate/schema/collector: `25 passed`.
- Pinned queue selftests: `38 passed`.
- Pierwsza pełna regresja przed finalnym collector ratchetem:
  `6317 passed, 74 skipped, 8 xfailed`.
- Finalna pełna regresja po ostatniej zmianie:
  `6318 passed, 74 skipped, 8 xfailed, 0 failed` (`322.49s`).
- `git diff --check`: zielony podczas niezależnego review.
- Niezależny review: code-level blocker wyczyszczony; deploy czeka wyłącznie na
  finalną regresję, aktualny evidence i weryfikację migracji live.

Oracles mutacyjne obejmują: przesunięcie claimu za child, odwrócenie RUN/CANCEL,
zmianę argv/token/payload/gate, dwa realne runnery, wyjątek/rc/postcondition
`atrm`, drift CAS po `atrm`, launch grace, stale claim, MISSING_ALARM ze świeżym
CANCEL claimem, failure receipt z równoległą zmianą gate'a, atomowość
rejestracji i migracji oraz ponowne włączenie registered-missing proposal
w collectorze.

## Migracja i runtime baseline

Żywa baza przed deployem ma `PRAGMA user_version=2`, szeroki legacy
`at_job_claims` i `0` rekordów claimów. Dry-run wykonano na backupie SQLite API:

- artefakt: `/root/handover/process-gates-v2-migration-dryrun-20260801T1035Z.sqlite3`;
- po migracji: `integrity_check=ok`, `user_version=3`, exact narrow claims table;
- zachowane: job `#224` (`SCHEDULED/auth1`) oraz `#225` (`MISSING_ALARM/auth1`);
- SHA-256 kopii po migracji: `1f89cb1afd70a9d691c8a3246fce1bf1d785e9a36627814170802cc89ac0d69b`.

Przed live zostanie wykonany świeży backup SQLite API z integralnością, trybem
`0600` i SHA oraz backup dokładnych plików obu narzędzi. Pierwsze użycie nowego
GateStore wykona atomową migrację v2→v3; restart systemd nie jest potrzebny.

## Rollback

Przed pierwszym nowym claimem dopuszczalny jest pełny rollback: w quiet window
usunąć wyłącznie nowy scheduler job, potwierdzić brak świeżych claimów,
odtworzyć bazę v2 przez SQLite API z exact backupu i przywrócić razem
`process_debt_gate.py`, `at_gate.py` oraz `process_debt_collect.py`.

Po utworzeniu jakiegokolwiek claimu v3 downgrade bazy i zwykły revert starego
czytnika są zabronione. Forward rollback to anulowanie świeżego joba narzędziem
v3, brak nowych schedule i pozostawienie kompatybilnego readera v3. Żadnego
kopiowania pliku SQLite z aktywnym WAL.

## Evidence dla mechanicznej bramki DoD

regresja: 6318 passed, 74 skipped, 8 xfailed, failed=0; exact final worktree vs baseline
e2e: 25 passed — real runner, dwa child procesy konkurujące o claim, RUN↔CANCEL, CLI cancel/atrm i collector/reconcile
pozytywny-wplyw: stary pre-exec race reprodukuje wielokrotne wykonanie; nowy kontrakt daje dokładnie jeden child i zero child przy każdym tamperze
rollback: przed claimem restore SQLite API v2 + trzy pliki .bak; po claimie forward rollback przez cancel v3 i brak nowych schedule
N-D: OPEN_GATES.md — exporter jest wyłącznie konsumentem kanonicznego gate/alarm i nie klasyfikuje atq ani claimów
