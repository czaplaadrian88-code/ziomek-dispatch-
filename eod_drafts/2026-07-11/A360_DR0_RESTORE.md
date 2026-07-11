# A360-DR0 RESTORE — raport wykonania

Data aktualizacji review: 2026-07-11
Wykonawca: tmux63
Status: **kod i syntetyczny artifact+PostgreSQL schema smoke PASS; realny artifact/DB drill i service RTO HOLD**

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

## Mapa kompletnosci

| Miejsce | Rola | Writer/consumer | Status | Dowod |
|---|---|---|---|---|
| CLI/target | scratch root, jawny tryb, brak prod targetu | operator -> restore | TAK | target nowy, direct child, 0700, owner marker; prod opcje RED |
| restic snapshot | immutable snapshot + RPO | restic -> verifier | TAK | globalnie najnowszy z wielu grup; remis RED; real bug naprawiony |
| restic cache | izolacja cache | restic -> scratch | TAK | cache pod targetem; verify cache efemeryczny |
| wymagane pliki | core/private/systemd/nginx | snapshot -> verifier | TAK syntetycznie | manifest 4+8+19+3; private metadata-only; atrapy RED |
| panel dump | plain gzip -> panel DB | backup panelu -> psql | TAK | pelna dekompresja/count, strict SQL, schema sentinels |
| Papu dump | plain albo encrypted | backup Papu -> psql | TAK | global newest/explicit mode, brak fallbacku, fixed decrypt vector |
| quota/free-space | scratch + Docker root | preflight -> restore | TAK syntetycznie | jawne budgety, shared-device i post-decompress guard; overflow RED |
| PostgreSQL | izolowany container/volume | restore -> schema smoke | TAK syntetycznie | stateful fake + strict SQL + role swap/network mutations |
| service/app smoke | import/health/start-order | aplikacja -> operator | **HOLD** | brak kodu aplikacji w restore scope i brak izolowanego dowodu |
| RTO/RPO | raport metadata-only | verifier -> operator | CZESCIOWO | component timing; service RTO HOLD; RPO `proven=false`, PITR `false` |
| produkcja | DB/kontener/unity/ruch | N-D | N-D | zakres jawnie zabranial zmian live; zero operacji produkcyjnych |

## Testy i oracle

- `bash -n docs/deploy/ha-lite/restore_from_restic.sh` — PASS.
- `py_compile tests/test_restore_from_restic_a360_dr0.py` — PASS.
- `HERMETIC_STRICT=1 ... pytest tests/test_restore_from_restic_a360_dr0.py -q`
  — finalny run na commitach review: **104 passed w 60.45 s**.
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
- blad cleanupu daje osobny `rc=90 scratch_rollback_incomplete`;
- sentinel wrazliwej tresci nie pojawia sie w stdout, stderr ani raporcie.

## RTO i RPO

| Zakres | Status | RTO | RPO |
|---|---|---:|---:|
| Syntetyczny artifact + PostgreSQL schema smoke | PASS | component timing emitowany per run; **nie service RTO** | 1800 s (fixture) |
| Real repository verify (dowod sprzed review) | PASS | 7.451 s czasu komendy; **nie service RTO** | snapshot age 43 475 s (~12 h 05 min) |
| Real artifact + PostgreSQL schema drill na obecnym kontrakcie | **HOLD / NIE URUCHAMIANO** | brak | **NOT PROVEN** |
| Pelny service recovery (import/health/start-order/ruch) | **HOLD / NOT PROVEN** | brak prawidlowego RTO | **NOT PROVEN** |

Realne RPO calego systemu pozostaje `NOT PROVEN`: wiek snapshotu jest znany,
ale bez encrypted Papu restore nie ma dowodu RPO obu baz. PITR/WAL nie byl
przedmiotem drill-a i pozostaje `NOT PROVEN`.

Historyczne realne proby ponizej dotyczyly rewizji sprzed nowego manifestu i
capacity contract. W ramach domkniecia review nie wykonywano restic restore,
Docker ani DB; nie przenosze ich PASS na obecny component drill.

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
5. Finalne real `--mode verify` po fixie: PASS, snapshot age 43 475 s,
   efemeryczny verify cache usuniety.

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
git revert 2295ac6
```

To cofa tylko domkniecie review (manifest, capacity guards i raport schema v2).
Pelny rollback calego zrodla A360-DR0 wymaga dodatkowo, od najnowszego:
`ed52812`, `6faf9d9`, `38da482`. Kazdy revert pozostaje operacja source-only;
nie wymaga restartu ani deployu.

Rollback udanego przyszlego drill-a: cleanup kontenera i volume wykonuje skrypt
po dokladnym labelu `run_id`; pozostawiony target wolno usunac tylko po zgodnym
owner markerze. Zakazane sa prune, wildcard cleanup i operacje na zasobach bez
zgodnego labelu.

## Git, live i otwarte ograniczenie

- Worktree: `/root/a360_dr0_wt/dispatch_v2`.
- Branch: `ops/a360-dr0-restore`.
- Base: tag `a360-wave1-closed-20260711`, commit `f679a88`.
- Commity przed review: `38da482`, `6faf9d9`, `ed52812`, `357b38a`, `9465d79`.
- Kod i testy domykajace review: `2295ac6`.
- README/runbook i glowna aktualizacja raportu: `0ca19b4`.
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

Do pelnego service RTO potrzebne sa lacznie: zatwierdzony mechanizm podania
wymaganych danych prywatnych bez ujawnienia tresci, kompletny manifest realnego
snapshotu oraz izolowany dowod importu aplikacji, health i kolejnosci startu.
Dopiero potem wolno zaplanowac realny `drill`. Instalacja skryptu lub jakakolwiek
operacja live nadal wymaga osobnego ACK.
