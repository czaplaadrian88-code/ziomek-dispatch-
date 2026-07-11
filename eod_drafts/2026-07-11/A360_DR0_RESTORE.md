# A360-DR0 RESTORE — raport wykonania

Data zamkniecia: 2026-07-11 13:36 UTC  
Wykonawca: tmux63  
Status: **kod i faza syntetyczna PASS; pelny realny RTO NOT ACHIEVED (STOP przed DB)**

## Wynik

`restore_from_restic.sh` jest fail-closed i nie ma juz drogi do istniejacego
kontenera, produkcyjnej nazwy bazy ani `--force`. Tryb `drill` sam tworzy dwie
scratch DB w nowym kontenerze PostgreSQL, na nowym volume, z `--network none`,
bez portow, z przypietym digestem obrazu i dokladnymi labelami `run_id`.

Panel i Papu maja osobne strumienie restore, `psql -X -v ON_ERROR_STOP=1
--single-transaction`, osobne liczniki oraz sentinele schematu. JSON, SQLite,
systemd i nginx sa walidowane przed Dockerem. Brak zasobu, blad decrypt/gzip/SQL,
niezgodny schema sentinel, zly target, symlink, stary backup, wysoki load,
rowolegla pelna regresja albo zbyt malo miejsca daje RED.

Pelnego realnego smoke PostgreSQL nie oglaszam jako PASS. Zarzadzany guard
zatrzymal probe przekazania pliku klucza encrypted Papu jeszcze przed startem
procesu. Zgodnie z zakazem odczytu sekretow nie obchodzilem guarda. Bezsekretowa
proba `--papu-format plain` potwierdzila, ze biezacy snapshot nie zawiera plain
dumpu Papu i zakonczyla sie RED przed Dockerem, bez fallbacku do encrypted.

## Mapa kompletnosci

| Miejsce | Rola | Writer/consumer | Status | Dowod |
|---|---|---|---|---|
| CLI/target | scratch root, tryb, brak legacy force | operator -> restore | TAK | target nowy, direct child, 0700, owner marker; prod opcje RED |
| restic snapshot | immutable snapshot + RPO | restic -> verifier | TAK | globalnie najnowszy z wielu grup; remis RED; real bug naprawiony |
| restic cache | izolacja cache | restic -> scratch | TAK | cache pod targetem; verify cache efemeryczny |
| wymagane pliki | JSON/SQLite/systemd/nginx | snapshot -> verifier | TAK | exact HARD paths, parser JSON, SQLite integrity/schema |
| panel dump | plain gzip -> panel DB | backup panelu -> psql | TAK | pelna dekompresja/count, strict SQL, schema sentinels |
| Papu dump | plain albo encrypted | backup Papu -> psql | TAK | global newest/explicit mode, brak fallbacku, fixed decrypt vector |
| PostgreSQL | izolowany container/volume | restore -> smoke | TAK syntetycznie | stateful fake + strict SQL + role swap/network mutations |
| RTO/RPO | raport metadata-only | verifier -> operator | TAK kontraktowo | raport 0600; RPO `proven=false`, PITR `false` |
| produkcja | DB/kontener/unity/ruch | N-D | N-D | zakres jawnie zabranial zmian live; zero operacji produkcyjnych |

## Testy i oracle

- `bash -n docs/deploy/ha-lite/restore_from_restic.sh` — PASS.
- `py_compile tests/test_restore_from_restic_a360_dr0.py` — PASS.
- `HERMETIC_STRICT=1 ... pytest tests/test_restore_from_restic_a360_dr0.py -q`
  — **53 passed w 27.22 s**.
- Kanoniczna pelna regresja `... python -m pytest tests/ -q` —
  **4991 passed, 27 skipped, 10 xfailed, 147 warnings w 147.83 s**.
- `git diff --check` — PASS.
- `tools/entropy_dashboard.py` — exit 0; zmiana nie dodala flag ani logiki
  decyzyjnej silnika.

Kluczowe tripwire'y testowe:

- mutacja `ON_ERROR_STOP=1 -> 0` daje modelowany falszywy sukces i jest
  wykrywana przez known-answer strict restore;
- syntetyczny blad SQL z tripwire daje non-zero;
- mutacja usuwajaca `--network none` daje RED z realnego state fake inspect;
- zamiana panel/Papu daje RED na role-specific schema sentinel;
- encrypted korzysta ze stalego ciphertext known-answer, nie generowanego w
  runtime przez ten sam encryptor;
- brak/tie snapshotu, dumpu, JSON, kolumn SQLite, systemd/nginx daje RED;
- leaf i ancestor symlink escape daja RED;
- foreign volume label nie jest montowany ani usuwany;
- blad cleanupu daje osobny `rc=90 scratch_rollback_incomplete`;
- sentinel wrazliwej tresci nie pojawia sie w stdout, stderr ani raporcie.

## RTO i RPO

| Zakres | Status | RTO | RPO |
|---|---|---:|---:|
| Syntetyczny full isolated drill | PASS | **1.496 s do smoke**, 1.670 s total | 1800 s (fixture) |
| Real repository verify | PASS | 7.451 s czasu komendy (nie full RTO) | **snapshot age 43 475 s** (~12 h 05 min) |
| Real full isolated PostgreSQL drill | **NOT ACHIEVED** | brak prawidlowego RTO | **NOT PROVEN** |

Realne RPO calego systemu pozostaje `NOT PROVEN`: wiek snapshotu jest znany,
ale bez encrypted Papu restore nie ma dowodu RPO obu baz. PITR/WAL nie byl
przedmiotem drill-a i pozostaje `NOT PROVEN`.

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
git revert ed52812 6faf9d9 38da482
```

Rollback udanego przyszlego drill-a: cleanup kontenera i volume wykonuje skrypt
po dokladnym labelu `run_id`; pozostawiony target wolno usunac tylko po zgodnym
owner markerze. Zakazane sa prune, wildcard cleanup i operacje na zasobach bez
zgodnego labelu.

## Git, live i otwarte ograniczenie

- Worktree: `/root/a360_dr0_wt/dispatch_v2`.
- Branch: `ops/a360-dr0-restore`.
- Base: tag `a360-wave1-closed-20260711`, commit `f679a88`.
- Commity: `38da482`, `6faf9d9`, `ed52812`.
- Push: `origin/ops/a360-dr0-restore`, bez force.
- Deploy/restart/systemd/nginx/DNS/ruch/flagi/runtime DB: **nie wykonywano**.
- Produkcyjny kontener i bazy: **nietkniete**.
- Wspolny backlog i pamiec: **nietkniete** zgodnie z karta integratora.
- Chronione dirty pliki: **nietkniete**; worktree przed raportem byl czysty.

Do pelnego realnego RTO potrzebny jest zatwierdzony mechanizm uruchomienia,
ktory poda encrypted Papu key bez ujawnienia tresci i bez obchodzenia managed
guarda. Dopiero wtedy nalezy uruchomic jeden izolowany `drill`; instalacja
skryptu lub jakakolwiek operacja live nadal wymaga osobnego ACK.
