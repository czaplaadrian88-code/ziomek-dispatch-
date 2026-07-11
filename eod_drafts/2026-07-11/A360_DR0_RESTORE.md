# A360-DR0 RESTORE — raport wykonania

Data aktualizacji review: 2026-07-11
Wykonawca: tmux63
Status: **source/fake PASS; real verify na provenance v1, artifact/DB drill i service RTO HOLD; NOT DONE**

## Wynik

`restore_from_restic.sh` jest fail-closed i nie ma drogi do istniejacego
kontenera ani wskazanej nazwy bazy. Tryb `drill` sam tworzy dwie
scratch DB w nowym kontenerze PostgreSQL, na nowym volume, z `--network none`,
bez portow, z przypietym digestem obrazu i dokladnymi labelami `run_id`.

Panel i Papu maja osobne strumienie restore, `psql -X -v ON_ERROR_STOP=1
--single-transaction`, osobne liczniki oraz sentinele schematu. JSON, SQLite,
systemd i nginx sa walidowane przed Dockerem. Wersjonowany kontrakt
`a360-dr0-required-artifacts-v1-20260711` wymaga 4 artefaktow rdzenia, 8 plikow
prywatnych/tozsamosci sprawdzanych tylko metadanymi, 19 konkretnych unitow i 3
konkretnych vhostow. Niepusty katalog z atrapa nie wystarcza. Brak zasobu,
blad decrypt/gzip/SQL, niezgodny schema sentinel, zly target, symlink, stary
backup, wysoki load, rownolegla pelna regresja, brak jawnego budzetu albo zbyt
malo miejsca daje RED.

Przed rozpakowaniem jest twardy per-run budget i free-space guard dla scratcha
oraz Docker root, lacznie dla wspolnego filesystemu. Po dekompresji dumpow
osobny guard uwzglednia ekspansje SQL przed utworzeniem volume. Nie jest to
filesystem quota; raport nie twierdzi inaczej.

Pelnego realnego smoke PostgreSQL ani service RTO nie oglaszam jako PASS.
Zarzadzany guard zatrzymal probe przekazania pliku klucza encrypted Papu jeszcze
przed startem procesu. Zgodnie z zakazem odczytu sekretow nie obchodzilem
guarda. Bezsekretowa proba `--papu-format plain` potwierdzila, ze biezacy
snapshot nie zawiera plain dumpu Papu i zakonczyla sie RED przed Dockerem, bez
fallbacku do encrypted.

## Dodatkowy review DR0/DR1

| Punkt review | Source/fake | Dowod realny | Bramka |
|---|---|---|---|
| progi RPO/age/reserve/memory/tables bez env weakening | CLOSED | semantyka source-pinned/readonly; real run niepotrzebny do zmiany progu | testy zatruwaja stare nazwy env i nadal dostaja RED |
| snapshot hostname/tag/path provenance przed newest | CLOSED | **NOT RUN / NOT PROVEN** dla biezacego repo | **DR1 HOLD** przed real artifact/drill |
| verify capacity + concurrent-backup guard | CLOSED | nowy verify **NOT RUN** | **DR1 HOLD** do bezpiecznego real verify |
| cleanup exact-name + scratch label + run_id po partial create/run | CLOSED | stateful fake + negatywy; real failure injection **NOT RUN** | **DR1 HOLD** jako brak realnego fault injection |

Realny profil ma stale `readonly`: max snapshot/dump 93 600 s, rezerwa 5 GiB,
pamiec 3 GiB i floor 50 tabel. Nazwy produkcyjnych override'ow nie sa
konsumowane; injection istnieje tylko jako `A360_TEST_*` w hermetycznym test mode.
Aktywacja test mode wymaga dodatkowo `PYTEST_CURRENT_TEST` i procesu nadrzednego
pytest; same dwie zmienne srodowiskowe nie wystarcza.

Provenance v1 wymaga producenta `Ziomek`, tagow `daily` i `scheduled` oraz
pieciu jawnych path sentinels. Skrypt pobiera kandydatow bez pre-filtra
`--latest`, filtruje provenance, potem wybiera jednoznacznie najnowszy. Raport
ujawnia tylko wersje i liczniki kontraktu.

`verify` wykonuje guard hosta przed lista snapshotow i ponownie przed checkiem,
rezerwuje 2 GiB cache + 5 GiB wolnego miejsca, a po checku sprawdza realny cache,
budzet i pozostala rezerwe. Cleanup jest uzbrojony przed `volume create`; po
non-zero sprawdza dokladna nazwe i oba labele, usuwa container przed volume i
potwierdza ich nieobecnosc. Niepewnosc lub obcy label daje `rc=90` bez kasowania.

## Mapa kompletnosci

| Miejsce | Rola | Writer/consumer | Status | Dowod |
|---|---|---|---|---|
| CLI/target | scratch root, jawny tryb, brak prod targetu | operator -> restore | TAK | target nowy, direct child, 0700, owner marker; prod opcje RED |
| restic snapshot | provenance + immutable ID + RPO | restic -> verifier | TAK syntetycznie | hostname+2 tagi+5 paths, potem newest; remis/ambiguous prefix RED |
| restic cache | izolacja + verify capacity | restic -> scratch | TAK syntetycznie | cache pod targetem; allowance/budget/free/reserve; efemeryczny cleanup |
| wymagane pliki | core/private/systemd/nginx | snapshot -> verifier | TAK syntetycznie | manifest 4+8+19+3; private metadata-only; atrapy RED |
| panel dump | plain gzip -> panel DB | backup panelu -> psql | TAK | pelna dekompresja/count, strict SQL, schema sentinels |
| Papu dump | plain albo encrypted | backup Papu -> psql | TAK | global newest/explicit mode, brak fallbacku, fixed decrypt vector |
| quota/free-space | scratch + Docker root | preflight -> restore | TAK syntetycznie | jawne budgety, shared-device i post-decompress guard; overflow RED |
| real safety profile | source constants -> guards | env/operator -> verifier | TAK | readonly; produkcyjne override'y ignorowane; tylko test namespace injectable |
| PostgreSQL | izolowany container/volume | restore -> schema smoke | TAK syntetycznie | stateful fake + strict SQL + role swap/network mutations |
| service/app smoke | import/health/start-order | aplikacja -> operator | **HOLD** | brak kodu aplikacji w restore scope i brak izolowanego dowodu |
| RTO/RPO | raport metadata-only | verifier -> operator | CZESCIOWO | component timing; service RTO HOLD; RPO `proven=false`, PITR `false` |
| produkcja | DB/kontener/unity/ruch | N-D | N-D | zakres jawnie zabranial zmian live; zero operacji produkcyjnych |

## Testy i oracle

- `bash -n docs/deploy/ha-lite/restore_from_restic.sh` — PASS.
- `py_compile tests/test_restore_from_restic_a360_dr0.py` — PASS.
- `HERMETIC_STRICT=1 ... pytest tests/test_restore_from_restic_a360_dr0.py -q`
  — finalny run po dodatkowym review i dokumentacji: **138 passed w 95.11 s**.
- Pelna regresja sprzed domkniecia review: **4991 passed, 27 skipped,
  10 xfailed, 147 warnings w 147.83 s**. Nie byla ponawiana, bo aktualne
  zlecenie wymagalo focused tests i nie zmienia silnika Ziomka.
- `git diff --check` — PASS.
- Wczesniejszy `tools/entropy_dashboard.py` — exit 0; zmiana nie dodaje flag ani
  logiki decyzyjnej silnika.

Kluczowe tripwire'y testowe:

- mutacja `ON_ERROR_STOP=1 -> 0` daje modelowany falszywy sukces i jest
  wykrywana przez known-answer strict restore;
- syntetyczny blad SQL z tripwire daje non-zero;
- mutacja usuwajaca `--network none` daje RED z realnego state fake inspect;
- zamiana panel/Papu daje RED na role-specific schema sentinel;
- usuniecie albo duplikacja pozycji manifestu daje RED na stalej oczekiwanej
  liczbie; brak kazdego jawnego artefaktu jest osobnym negative control;
- katalog zawierajacy tylko `synthetic.service`/`synthetic.conf` daje RED;
- brak/invalid/za maly budget, overflow, shared-device exhaustion i ekspansja
  SQL ponad budzet daja RED przed utworzeniem volume;
- mutacja usuwajaca oba scratch budget tripwire daje modelowany falszywy PASS,
  wykrywany przez negative control prawdziwego skryptu;
- encrypted korzysta ze stalego ciphertext known-answer, nie generowanego w
  runtime przez ten sam encryptor;
- brak/tie snapshotu, dumpu, JSON, kolumn SQLite lub artefaktu manifestu daje RED;
- leaf i ancestor symlink escape daja RED;
- foreign volume label nie jest montowany ani usuwany;
- partial `volume create`/`docker run` z non-zero jest rekoncyliowany po nazwie i
  labelach; foreign run_id i sticky remove daja `rc=90` bez obcego cleanupu;
- obcy hostname, brak kazdego tagu/path, malformed provenance, ambiguous ID i
  remis daja RED; nowszy obcy snapshot nie zaslania poprawnego;
- stare produkcyjne env nie oslabiaja zadnego z pieciu progow;
- verify ma negatywy conflict/load/memory, pre/post free, cache allowance i
  reserve erosion;
- blad cleanupu daje osobny `rc=90 scratch_rollback_incomplete`;
- sentinel wrazliwej tresci nie pojawia sie w stdout, stderr ani raporcie.

## RTO i RPO

| Zakres | Status | RTO | RPO |
|---|---|---:|---:|
| Syntetyczny artifact + PostgreSQL schema smoke | PASS | component timing emitowany per run; **nie service RTO** | 1800 s (fixture) |
| Real repository verify sprzed provenance v1 | historyczny PASS | 7.451 s czasu komendy; **nie dowodzi obecnego verify ani service RTO** | historyczny snapshot age 43 475 s |
| Real verify z provenance v1 i nowymi guardami | **HOLD / NOT RUN** | brak | **NOT PROVEN** |
| Real artifact + PostgreSQL schema drill na obecnym kontrakcie | **HOLD / NIE URUCHAMIANO** | brak | **NOT PROVEN** |
| Pelny service recovery (import/health/start-order/ruch) | **HOLD / NOT PROVEN** | brak prawidlowego RTO | **NOT PROVEN** |

Realne RPO calego systemu pozostaje `NOT PROVEN`: wiek snapshotu jest znany,
ale bez encrypted Papu restore nie ma dowodu RPO obu baz. PITR/WAL nie byl
przedmiotem drill-a i pozostaje `NOT PROVEN`.

Historyczne realne proby ponizej dotyczyly rewizji sprzed manifestu, provenance
v1 i obecnego capacity contract. W ramach dodatkowego review nie wykonywano
restic, Docker ani DB; nie przenosze ich PASS na obecny verify/component drill.

## Realne proby i bezpieczniki

1. Proba encrypted zostala zablokowana przez managed guard przed wykonaniem
   komendy. Zero skutkow ubocznych.
2. Pierwsza bezsekretowa proba ujawnila, ze `restic snapshots --latest 1`
   zwraca wiele grup. Stary parser dal `invalid_snapshot_metadata`; zero restore
   i zero Docker. Fix: commit `6faf9d9`, test multi-group + tie RED.
3. Druga bezsekretowa proba przeszla realne snapshot/check/stats/restore i
   zatrzymala sie na `papu_plain_dump_missing_or_ambiguous`. Zero Docker,
   target usuniety automatycznie.
4. Ta proba ujawnila ok. **149 008 384 B** wzrostu wspoldzielonego cache restic.
   Nie usuwalem go, bo katalog istnial wczesniej i nie bylo bezpiecznej granicy
   ownership. Fix `ed52812` kieruje nowe cache pod target/verify scratch.
5. Historyczne real `--mode verify` po fixie cache: PASS, snapshot age 43 475 s,
   efemeryczny cache usuniety. Ten wynik poprzedza provenance v1 i nowe guardy,
   wiec obecny real verify pozostaje HOLD.

Przed realnymi probami byly po dwie czyste probki hosta oddalone o 30 s:
brak pytest/backup, load1 ponizej 2.0, pamiec powyzej 3 GiB, `si=so=wa=0`.

## Rollback i stan koncowy

Koncowy licznik rollbacku:

- restore targety: **0**;
- verify cache: **0**;
- scratch kontenery: **0**;
- scratch volume: **0**;
- aktywny restic: **0**.

Rollback kodu (bez restartu/deployu):

```bash
git revert 98ea13b 2295ac6
```

To cofa source domkniec obu review (provenance/verify/cleanup oraz manifest,
capacity guards i report schema v2).
Pelny rollback calego zrodla A360-DR0 wymaga dodatkowo, od najnowszego:
`ed52812`, `6faf9d9`, `38da482`. Kazdy revert pozostaje operacja source-only;
nie wymaga restartu ani deployu.

Rollback udanego przyszlego drill-a: cleanup kontenera i volume wykonuje skrypt
po dokladnej nazwie, labelu scratch i `run_id`, a potem potwierdza nieobecnosc;
pozostawiony target wolno usunac tylko po zgodnym owner markerze. Zakazane sa
prune, wildcard cleanup i operacje na zasobach bez zgodnych labeli.

## Git, live i otwarte ograniczenie

- Worktree: `/root/a360_dr0_wt/dispatch_v2`.
- Branch: `ops/a360-dr0-restore`.
- Base: tag `a360-wave1-closed-20260711`, commit `f679a88`.
- Commity przed review: `38da482`, `6faf9d9`, `ed52812`, `357b38a`, `9465d79`.
- Kod i testy domykajace review: `2295ac6`.
- README/runbook i glowna aktualizacja raportu: `0ca19b4`.
- Dodatkowy source/fake gate DR0/DR1: `98ea13b`.
- Finalny checkpoint dowodu: commit zawierajacy ten raport; SHA podany po pushu.
- Push: `origin/ops/a360-dr0-restore`, bez force.
- Deploy/restart/systemd/nginx/DNS/ruch/flagi/runtime DB: **nie wykonywano**.
- Produkcyjny kontener i bazy: **nietkniete**.
- Wspolny backlog i pamiec: **nietkniete** zgodnie z karta integratora.
- Chronione dirty pliki: **nietkniete**; worktree przed raportem byl czysty.
- Snapshot `backup_restic.sh` w repo nie pokazuje globu dla dwoch unitow
  `backup-sentinel`; kontrakt wymaga ich jawnie. Do czasu poprawy producenta
  backupu albo dowodu, ze sa w realnym snapshotcie, real artifact drill ma
  pozostac RED/HOLD — nie obnizam manifestu, by ukryc luke.
- Realne repo nie zostalo odczytane po dodaniu provenance v1; match
  hostname/tag/path oraz nowy verify capacity/concurrency sa **NOT PROVEN**.
- Filesystem quota nadal nie jest dostepny; per-run budget i free-space guard
  nie sa przedstawiane jako quota enforcement.
- Realny partial-Docker fault injection nie byl wykonywany; cleanup ma dowod
  stateful fake, ale real drill pozostaje HOLD.

Do pelnego service RTO potrzebne sa lacznie: zatwierdzony mechanizm podania
wymaganych danych prywatnych bez ujawnienia tresci, kompletny manifest realnego
snapshotu oraz izolowany dowod importu aplikacji, health i kolejnosci startu.
Dopiero potem wolno zaplanowac realny `drill`. Instalacja skryptu lub jakakolwiek
operacja live nadal wymaga osobnego ACK.

**Nie oglaszam DONE. Stan koncowy tego sprintu to source/fake PASS oraz DR1 HOLD
przed jakimkolwiek real artifact/drill.**
