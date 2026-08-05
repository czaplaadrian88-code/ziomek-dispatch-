# BRIEF ITER2 (sesja 297) — OD-7 archiver: 4 findingi z blind CONFIRMED_DEFECT

## Kontekst
Twój kandydat `f9d4d824f` przeszedł blind z 4 findingami (D1/D3–D7 + 6 granic POTWIERDZONE — rdzeń jest
dobry). Werdykt z reprodukcjami: `/root/artifacts/blind-297/od7/verdict.json` — PRZECZYTAJ W CAŁOŚCI.
Gotowy turnkey-oracle recenzenta: `/root/artifacts/blind-297/od7/repro_test_od7_wal.py` (RED na kandydacie
— po fixie MUSI być GREEN, a jego logika wcielona do twojej suity).

## Defekty do zamknięcia
- F1 (HIGH, l.657 + l.1333): sqlite w REPORT `--include-sqlite` ORAZ w APPLY snapshot tworzy `-wal`/`-shm`
  w SKANOWANYM ŻYWYM korzeniu (pisze biblioteka sqlite, nie twój kod; `mode=ro` NIE pomaga dla WAL;
  żywy events.db JEST w WAL). Kierunek (twój wybór, uzasadnij): `immutable=1` dla statystyk czystego
  odczytu; dla snapshotu — kopia pliku bazy do TEMP W ARCHIWUM przed otwarciem (uwaga: kopia bazy WAL
  bez -wal może być niespójna — przemyśl backup API sqlite3 (Connection.backup) z połączeniem immutable
  albo udokumentuj świadomie wymóg czystego checkpointu; NIE zostawiaj żadnej ścieżki tworzącej pliki
  obok żywej bazy). Testy: fixture z bazą `PRAGMA journal_mode=WAL` czysto zamkniętą; REPORT+flag i APPLY
  → zero nowych plików w korzeniu źródła.
- F2 (MED, l.489/516): ucięty `.gz` → EOFError (nie-OSError) zabija CAŁY bieg REPORT bez raportu.
  Kontrakt modułu: błąd per-plik → errors[] → exit 3 → raport POWSTAJE. Złap właściwe wyjątki
  (EOFError + zlib.error + co realnie rzuca gzip przy truncacji) per plik, nie globalnie łykaj Exception.
  Test: fixture z połową gzipa.
- F3 (MED-LOW, l.1249/1296): brak wykluczenia wzajemnego → dwa równoległe APPLY dublują pracę i manifest.
  Fix: flock na pliku locka W ARCHIVE-ROOT (nie w żywym korzeniu!), fail z jasnym komunikatem gdy zajęte.
  Test: dwa procesy → jeden archiwizuje, drugi odmawia.
- F4 (LOW, l.242): fnmatch dla wzorców ze slashem — `*` przekracza granice katalogów. Fix: dopasowanie
  per-segment (albo jawne udokumentowanie rekursji w polityce — ale domyślnie NIE-rekurencyjnie).
  Test: `world_record/world_record-x/DEEP/leak.jsonl` → unknown.

## Poza zakresem iter2 (NIE ruszaj)
Polityka liczb OD-7; kasowniki GC (P-2/P-3 = decyzja ownera); notes_not_findings recenzenta (zachowanie
MASK_LIVE poza kanonicznymi rootami; dokumentacja source_sha256 vs content_sha256 — jeżeli tanio, dopisz
zdanie do docstringa manifestu, ale zero zmian logiki).

## DoD iter2
1. repro_test_od7_wal.py recenzenta = GREEN po fixie; jego scenariusz wcielony do tests/.
2. Wszystkie 4 findingi zamknięte testami; 47 starych testów nadal zielonych.
3. ZERO biegów na żywych ścieżkach w tej iteracji (wszystko syntetyczne).
4. Pełna regresja (pkgroot + ZIOMEK_SCRIPTS_ROOT): 0 failed, delta = tylko twoje nodeidy (podaj).
5. Commit na tej samej gałęzi (jawny pathspec). ⛔ LEDGER = CTO, nie dotykaj process_debt_gate.
6. `RAPORT_ITER2.md`: per finding co zmienione + dowód, delta, pełny SHA.

Komenda: `PK=/root/worktrees/dispatch_v2/pkgroot/20260805-od7-archiver-297-cto; cd $PK/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PK /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_retention_archiver.py -q`
