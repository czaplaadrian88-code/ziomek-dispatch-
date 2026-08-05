# RAPORT ITER2 (sesja 297) — OD-7 archiver: zamknięcie 4 findingów blind-297

* **Gałąź:** `wt/od7-archiver-297-cto-20260805` · **baza:** `06e4d5c39` · **iter1:** `f9d4d824f`
  (+ `3afc9ec88` docs)
* **KANDYDAT ITER2 (pełny SHA):** `5575cb14e0cfd017475dd5e8dcb129bcd60b2523`
  (`fix(tools/OD-7 iter2): zamkniecie 4 findingow blind-297 …`; ten raport dopina do niego
  osobny commit dokumentacyjny — kod recenzuj po SHA powyżej)
* **Werdykt wejściowy:** `/root/artifacts/blind-297/od7/verdict.json` → `CONFIRMED_DEFECT`
  (F1 high, F2 medium, F3 medium-low, F4 low; rdzeń + 6 granic POTWIERDZONE)
* **Pliki zmienione:** `tools/retention_archiver.py`, `tests/test_retention_archiver.py` (nic więcej;
  ledger `process_debt_gate` NIETKNIĘTY — należy do CTO)
* **sha256 plików kandydata** (do weryfikacji bundla przez recenzenta):
  `tools/retention_archiver.py` = `592a249e1940f0265d3c6e33f1a8cf101ae3bbcac28e467b2e6b1b38d1e49c38` ·
  `tests/test_retention_archiver.py` = `598fe4370620aaaf39b28ff396b0f8954971c6b4b21ed8b7983d886c334d5bde` ·
  `tools/retention_od7_policy.json` = `26e6d0856dc65a219976c9fd535f0f4701f75bb94b8ae50790b401c0209a0112` (BEZ ZMIAN vs iter1)
* **Żywe ścieżki w tej iteracji:** ZERO biegów. Wszystkie sondy i testy na drzewach syntetycznych
  (`tmp_path` / katalog roboczy poza repo). Kanoniczna polityka jest tylko CZYTANA (bez skanu korzeni).

---

## F1 (HIGH) — sqlite tworzył `-wal`/`-shm` w skanowanym ŻYWYM korzeniu

**Przyczyna u źródła.** `sqlite3.connect("file:…?mode=ro")` NIE jest operacją bezzapisową: baza w trybie
WAL wymaga indeksu `-shm` i pliku `-wal`, a połączenie read-only nie może zrobić checkpointu, więc oba
pliki ZOSTAJĄ obok bazy po `close()`. Zapisu dokonuje biblioteka sqlite3, więc `WriteGate` (który widzi
tylko prymitywy zapisu tego modułu) był z zasady ślepy. Dotyczyło to DWÓCH ścieżek: `sqlite_age_stats`
(REPORT `--include-sqlite`) i `sqlite_snapshot` (APPLY, bez żadnej flagi opt-in).

**Zmierzone (sonda własna, sqlite 3.45.1):** czysto domknięta baza WAL → po `mode=ro` na dysku
`events.db`, `events.db-shm`, `events.db-wal`, także po zamknięciu połączenia. Po `immutable=1` → sam
`events.db`.

**Co zmienione.**

| Ścieżka | Było | Jest |
|---|---|---|
| statystyki (`sqlite_age_stats`) | `mode=ro` na ŻYWEJ bazie | `sqlite_connect_readonly()` = `immutable=1` — jeden kanoniczny opener cudzej bazy w module |
| snapshot (`sqlite_snapshot`) | `mode=ro` na ŻYWEJ bazie + `backup()` | bajtowa kopia pliku bazy (potem `-wal`/`-journal`) do TEMP **W ARCHIWUM** → baza otwierana dopiero NA KOPII → `PRAGMA integrity_check` → `backup()` → gzip |
| oba | brak dowodu | `_assert_no_sidecars_created()` przed każdym wyjściem: gdy obok źródła przybył `-wal`/`-shm`/`-journal`, bieg pada z `OD7-SQLITE-SIDECAR` |

**Dlaczego taki kierunek (uzasadnienie wyboru z briefu).**
* Statystyki to CZYSTY odczyt liczb — `immutable=1` daje zero plików towarzyszących i zero blokad.
  Cena: połączenie widzi wyłącznie plik główny, więc dane siedzące w niescheckpointowanym `-wal` są
  niewidoczne. Nie chowam tego: raport dostaje `read_mode: "immutable"` i `wal_pending: true`, a bieg
  dokłada wpis do `errors[]` → **exit 3**. Świadomie wybrałem „liczby mogą być niepełne i mówię to
  wprost" ponad „liczby pewne, ale zaśmiecony żywy korzeń" — granica 2 (żaden zapis do żywych korzeni)
  jest twardsza niż kompletność statystyki poglądowej.
* Snapshot MUSI widzieć dane z `-wal` (inaczej archiwum bezterminowe `events_db` gubiłoby świeże
  wiersze), więc tu `immutable=1` NIE wystarcza. Stąd kopia: źródło jest tylko czytane, a WAL odzyskuje
  się na kopii w archiwum. Ryzyko z briefu („kopia bazy WAL bez `-wal` może być niespójna") zamknięte
  dwoma sposobami: (a) kopiuję też `-wal`/`-journal` — najpierw bazę, potem dziennik, który tylko rośnie;
  (b) niespójność (np. czyjś checkpoint w trakcie kopiowania) wyłapuje `PRAGMA integrity_check` i bieg
  pada, zamiast opublikować rozspójnione archiwum. `-shm` świadomie NIE jest kopiowany (odtwarzalny
  indeks; kopia mogłaby zmylić recovery).
* **Koszt do świadomej akceptacji:** w archiwum powstaje przejściowo kopia robocza + czysty snapshot
  (~2× rozmiar bazy) przed gzipem; oba sprzątane w `finally`. Konkretnie dziś: `events.db` = 590 MB
  → ~1,2 GB szczytu w archive-root, `courier_api.db` = 59 MB → ~120 MB. Żywy dysk (91 % zajęty) NIE
  jest tym obciążony — archive-root leży z definicji na innym mouncie. Tańszy wariant (checkpoint WAL
  na kopii i gzip prosto z niej, ~1× zamiast ~2×) jest możliwy i równoważnie zabezpieczony
  `integrity_check`; zostawiam `Connection.backup()`, bo to droga konwencjonalna i przetestowana —
  zamiana to świadoma decyzja CTO, nie cicha optymalizacja w bramce naprawczej.

**Dowód.**
* Turnkey-oracle recenzenta `repro_test_od7_wal.py`: **GREEN** (był RED na `f9d4d824f`).
* Wcielone do suity: `test_report_with_include_sqlite_writes_only_to_out` (snapshot całego drzewa
  przed/po; jedyny nowy plik = `--out`; `rows_total == 20`, czyli liczby nadal prawdziwe),
  `test_apply_snapshot_of_cleanly_closed_wal_db_creates_no_sidecars` (ścieżka APPLY, przypadek z repro:
  `set(after) - set(before) == ∅` ORAZ identyczne rozmiary/mtime),
  `test_apply_snapshot_creates_nothing_next_to_live_db_and_captures_wal` (żywy pisarz trzyma WAL →
  archiwum zawiera 20 wierszy z `-wal`, sha żywej bazy bez zmian, zero plików roboczych),
  `test_sqlite_stats_report_uncheckpointed_wal_instead_of_silently_stale_numbers` (exit 3 + `wal_pending`),
  `test_sidecar_guard_reddens_if_readonly_open_regresses_to_mode_ro` (RATCHET bezpiecznika).
* Uczciwa granica oracle'a, zapisana w docstringu testu: gdy pliki towarzyszące JUŻ istnieją (silnik
  trzyma bazę otwartą), otwarcie `mode=ro` nie zmienia ani nazw, ani rozmiarów, ani mtime — zmierzyłem
  to i dlatego oraclem defektu jest stan CZYSTO DOMKNIĘTY, dokładnie jak w repro recenzenta.

---

## F2 (MED) — ucięty `.gz` zabijał CAŁY bieg REPORT (exit 1, bez raportu)

**Przyczyna u źródła.** `detect_pii()` łapało wyłącznie `OSError`. `gzip` przy uciętym ogonie rzuca
`EOFError`, a przy zepsutym środku strumienia `zlib.error` — żaden z nich nie jest podklasą `OSError`
(jest nią tylko `gzip.BadGzipFile`, czyli zły nagłówek). Wyjątek leciał do generycznego handlera
`main()` i kończył bieg ZANIM powstał `--out`, łamiąc własny kontrakt modułu.

**Co zmienione.**
* Stała `CORRUPT_STREAM_ERRORS = (OSError, EOFError, zlib.error)` + `except` po niej w `detect_pii`
  (nie gołe `except Exception` — łapiemy klasę „uszkodzony strumień", nie wszystko).
* Uszkodzenie nie kasuje dowodów: zwracam `sampled_bytes` + `hits` zebrane DO MOMENTU błędu i `error`.
* Domknięcie kontraktu w `main()`: `det["error"]` ląduje w `errors[]` (`pii-scan: …`) → **exit 3** →
  raport POWSTAJE. Wcześniej błąd z `detect_pii` był po cichu gubiony (raport go nie pokazywał).
* Docstring modułu mówi to teraz wprost.

**Dowód.** `test_truncated_gzip_does_not_kill_report` (rc 3, plik raportu istnieje, wpis `pii-scan`
w `errors[]`, uszkodzony plik nadal w planie jako ARCHIVE\*),
`test_detect_pii_survives_broken_gzip_and_keeps_partial_evidence[truncate|corrupt]` (oba typy
uszkodzenia; dla ucięcia dodatkowo: częściowe trafienia zachowane).

---

## F3 (MED-LOW) — brak wykluczenia wzajemnego: dwa APPLY dublowały pracę i manifest

**Przyczyna u źródła.** Idempotencja opiera się na manifeście czytanym RAZ na starcie (`read_manifest`
→ `archived_rel` w `build_plan`). Między odczytem a `append_manifest` nie było żadnego wykluczenia, więc
dwa równoległe biegi planowały ten sam zbiór akcji (repro recenzenta: 56 rekordów zamiast 28).

**Co zmienione.** `acquire_apply_lock(archive_root)` — `flock(LOCK_EX|LOCK_NB)` na
`<archive-root>/.od7_apply.lock` (**w archiwum, nigdy w żywym korzeniu**; ścieżka i tak przechodzi przez
`GATE.check`). Blokada zakładana PRZED `read_manifest` i trzymana przez cały bieg (plan + zapisy),
zwalniana w `finally`. Zajęte = `ApplyLockBusy` → jasny komunikat na stderr → **exit 4** (nowy kod,
udokumentowany w docstringu; nie miesza się z „STOP: brak ACK" = 2 ani z fail-closed = 1). Plik locka
nie jest kasowany — kasowanie locka jest wyścigiem samo w sobie.

**Dowód.** `test_second_apply_is_refused_while_lock_is_held` (rc 4, manifest pusty, lock leży
w archiwum; po zwolnieniu ten sam bieg przechodzi i archiwizuje 1 plik) oraz
`test_two_parallel_apply_processes_do_not_duplicate_manifest` — repro recenzenta 1:1: dwa PROCESY naraz
na 12 plikach → 12 rekordów, zero duplikatów, jeden `run_id`, kody `{0, 0|4}`.

---

## F4 (LOW) — `*` przekraczał granice katalogów (reguły płaskie działały jak rekurencyjne)

**Przyczyna u źródła.** Gałąź `if "/" in pattern: fnmatch.fnmatch(rel, pattern)` — w `fnmatch` `*`
tłumaczy się na `.*`, które łyka także `/`.

**Co zmienione.** `_match_segments()` — dopasowanie SEGMENT PO SEGMENCIE (`fnmatchcase` per segment,
deterministycznie niezależnie od platformy), rekursja WYŁĄCZNIE przez jawny segment `**` (obsługiwany
w dowolnej pozycji, pochłania 0..n segmentów). Wzorzec bez `/` dalej znaczy „plik wprost w korzeniu".
Kierunek domyślny jest WĄSKI: co nie jest jawnie objęte regułą, zostaje `unknown` → `REPORT_UNKNOWN` →
NIGDY nie ruszane. Zmiana nie rozszerza zakresu działania narzędzia, tylko go zawęża.

**Dowód.** `test_star_does_not_cross_directory_boundary_in_real_policy` (na KANONICZNEJ polityce:
`world_record/world_record-x/DEEP/leak.jsonl`, `observability/candidate_decisions_a/b/c.jsonl`,
`reports/sub/dir/anything.bin` → brak reguły; a płaskie `world_record-20260101.jsonl`,
`candidate_decisions_20260101.jsonl`, `reports/dzienny.txt`, `watcher.log.1` → trafiają jak dotąd),
`test_deep_file_under_rule_prefix_is_unknown_not_archivable` (plan: `unknown` + `REPORT_UNKNOWN`),
`test_double_star_is_the_only_recursive_marker` (wykluczenia `**/*.lock`, `**/__pycache__/**`,
`backups/**` nadal działają rekurencyjnie).

---

## Mutation-testy (fix wycofany ⇒ oracle znów czerwony)

Mutanty budowane w katalogu roboczym poza repo (worktree nietknięty); dla F1c to DOSŁOWNY powrót do
funkcji z `f9d4d824f` (`git show`), nie parafraza.

| Mutant | Wynik |
|---|---|
| `F1a` opener → `mode=ro` (bezpiecznik zostaje) | CZERWONY (1 failed) |
| `F1b` `mode=ro` + wyłączony `_assert_no_sidecars_created` | CZERWONY (1 failed) |
| `F1c` `sqlite_snapshot` = oryginał z `f9d4d824f` + bez bezpiecznika | CZERWONY (1 failed, 1 passed¹) |
| `F2` `except CORRUPT_STREAM_ERRORS` → `except OSError` | CZERWONY (3 failed) |
| `F3` brak zakładania locka w `main()` | CZERWONY (2 failed) |
| `F4` `_match_segments` → `fnmatch` na całej ścieżce | CZERWONY (3 failed) |

¹ test „żywy pisarz trzyma WAL" przechodzi także na mutancie — bo przy JUŻ istniejących plikach
towarzyszących naruszenia nie da się zaobserwować z zewnątrz (zmierzone). Defekt wykrywa test stanu
czysto domkniętego; obie granice są opisane w docstringach testów, żeby nikt nie wziął tego pierwszego
za dowód, którym nie jest.

---

## Regresja i delta

* Suita modułu: `PK=/root/worktrees/dispatch_v2/pkgroot/20260805-od7-archiver-297-cto; cd $PK/dispatch_v2
  && ZIOMEK_SCRIPTS_ROOT=$PK HERMETIC_STRICT=1 pytest tests/test_retention_archiver.py -q` →
  **60 passed** (47 z iter1 + 13 nowych, `HERMETIC_STRICT=1`).
* Oracle recenzenta: `repro_test_od7_wal.py` → **1 passed**.
* Pełna regresja (pkgroot + `ZIOMEK_SCRIPTS_ROOT`, `pytest tests/ -q`):
  **7589 passed, 24 skipped, 8 xfailed, 0 failed** (810 s, exit 0) —
  `scratchpad/full_regression_iter2_run2.txt`. Baseline iter1 (pomiar recenzenta):
  7576 passed / 0 failed → **+13 passed = dokładnie moje nowe nodeidy**, zero regresji.
* Bieg 1 tej samej regresji dał 1 failed: `tests/test_a6_security_pin_kdf.py::
  test_security_oracle_no_plaintext_in_store` — **znany flake losowy, nie moja zmiana**.
  Dowód mechanizmu wprost z logu: oracle żąda, by ciąg PIN-u nie występował NIGDZIE
  w blobie JSON, a wylosowana sól brzmiała `c3be**5678**27e81bc796a761a8a640ddf5`
  (PIN „5678" trafił w losowy hex). Ten test dotyczy `identity/pin_auth`, którego mój diff
  nie rusza; 8/8 powtórzeń pliku = zielone, a bieg 2 całej suity = 0 failed.
  (To ten sam flake, który zamyka osobny kandydat s297 `d09a783eb`, niezmergowany do tej bazy.)
* Delta `--collect-only` vs zapieczętowany manifest night-guard v46 (baza `06e4d5c39`, 7557 nodeidów):
  **7617 nodeidów, +60, ZERO usuniętych**, wszystkie dodane w `tests/test_retention_archiver.py`
  (47 iter1 + 13 iter2). Manifest strażnika NIE był re-seedowany — to należy do merge'a/CTO.

**Nowe nodeidy iter2 (13):**
```
test_report_with_include_sqlite_writes_only_to_out
test_apply_snapshot_of_cleanly_closed_wal_db_creates_no_sidecars
test_apply_snapshot_creates_nothing_next_to_live_db_and_captures_wal
test_sidecar_guard_reddens_if_readonly_open_regresses_to_mode_ro
test_sqlite_stats_report_uncheckpointed_wal_instead_of_silently_stale_numbers
test_detect_pii_survives_broken_gzip_and_keeps_partial_evidence[truncate]
test_detect_pii_survives_broken_gzip_and_keeps_partial_evidence[corrupt]
test_truncated_gzip_does_not_kill_report
test_second_apply_is_refused_while_lock_is_held
test_two_parallel_apply_processes_do_not_duplicate_manifest
test_star_does_not_cross_directory_boundary_in_real_policy
test_deep_file_under_rule_prefix_is_unknown_not_archivable
test_double_star_is_the_only_recursive_marker
```

---

## Poza zakresem (nietknięte, zgodnie z briefem)

* Polityka liczb OD-7 (`retention_od7_policy.json`) — **plik bez zmian**.
* Kasowniki GC / P-2 / P-3 — decyzja ownera.
* `notes_not_findings` recenzenta: zachowanie `MASK_LIVE` poza kanonicznymi rootami — bez zmian logiki.
  Jedyne, co dopisałem, to zdanie do docstringa `gzip_copy_verified`: `source_sha256` (bajty źródła) vs
  `content_sha256` (treść w archiwum) opisują różne rzeczy dla źródeł `.gz` i przy maskowaniu —
  gwarantowana jest integralność TREŚCI, nie bajtów kontenera. Zero zmian logiki.
* Ledger `process_debt_gate` — nietknięty (CTO).

## Zmiany kontraktu do świadomości ownera/CTO

1. **Nowy kod wyjścia 4** = „APPLY zajęty przez inny bieg" (był tylko 0/1/2/3).
2. **`--include-sqlite` może teraz dać exit 3** tam, gdzie wcześniej dawał 0 — gdy baza ma
   niescheckpointowany `-wal`, liczby są jawnie oznaczone jako niepełne (`wal_pending`).
3. **Zawężenie dopasowania wzorców ze slashem**: pliki w podkatalogach pod prefiksem reguły przestają
   wpadać do klasy archiwizowalnej i lądują jako `REPORT_UNKNOWN` (kierunek bezpieczny — nie ruszane;
   na dzisiejszych danych recenzent zmierzył zero takich plików).
4. W `archive-root` pojawia się plik `.od7_apply.lock` (0600, nie kasowany).
